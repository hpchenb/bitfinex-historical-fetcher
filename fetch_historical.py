#!/usr/bin/env python3
"""
Bitfinex Historical Wallet Data Fetcher
========================================
Fetches daily TOTAL wallet balance (lending credits + available) for each account.

CRITICAL API NOTES:
- /v1/balances returns total wallet = lent funds + available funds
- /v1/credits ONLY returns actively lent funds - DO NOT use for total
- V2 /v2/auth/r/ledgers/{currency}/hist can go back 6 years - use this for historical data
- Daily principal should COMPOUND with earned interest (principal_t = principal_{t-1} + earned_t)
- V2 ledger entry format: [ID, CURRENCY, CURRENCY2, MTS, null, AMOUNT, BALANCE, null, DESCRIPTION]
  - entry[3]: timestamp in ms
  - entry[5]: transaction amount (delta)
  - entry[6]: running wallet balance after transaction
  - entry[8]: description (e.g. "Margin Funding Payment")
- V2 REST auth signature: HMAC-SHA384(secret, "/api" + path + nonce + body)

Accounts:
  - a11, hpchen, tina.ding, jessie.chen, yihlan.chen
  - API keys stored inline

Output:
  - JSON: /tmp/historical_wallet_data.json
    {
      "a11": {
        "USD": {"2026-03-26": {"principal": 38834.09, "earned": 2.8759}, ...},
        "USDT": {"2026-03-26": {"principal": 12309.32, "earned": 2.8351}, ...}
      }
    }
"""

import json
import time
import base64
import hmac
import hashlib
import requests
from datetime import datetime, timezone, timedelta
from collections import defaultdict

# ============================================================
# ACCOUNTS (loaded from central config file)
# ============================================================
with open(os.path.join(os.path.dirname(__file__), "accounts.json")) as _f:
    ACCOUNTS = json.load(_f)

# ============================================================
# BITFINEX API (raw - for reference)
# ============================================================

def bf_sign(payload, secret):
    """Sign V1 API request"""
    j = json.dumps(payload)
    data = base64.standard_b64encode(j.encode('utf8'))
    return hmac.new(secret.encode('utf8'), data, hashlib.sha384).hexdigest()

def bf_api_v1(path, payload, api_key, api_secret):
    """Make authenticated V1 API request"""
    nonce = str(int(time.time() * 1e9))
    payload['nonce'] = nonce
    j = json.dumps(payload).encode('utf8')
    data = base64.standard_b64encode(j).decode()
    sig = bf_sign(payload, api_secret)
    r = requests.post(f"https://api.bitfinex.com{path}",
        headers={'X-BFX-APIKEY': api_key, 'X-BFX-SIGNATURE': sig, 'X-BFX-PAYLOAD': data, 'Content-Type': 'application/json'},
        data=j, timeout=30)
    return r.json()

def get_daily_earned(api_key, api_secret, currency, date_str):
    """Get total earned interest for a specific date (YYYY-MM-DD)"""
    dt = datetime.strptime(date_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    next_dt = dt + timedelta(days=1)
    payload = {
        'request': '/v1/history',
        'currency': currency,
        'since': str(int(dt.timestamp())),
        'until': str(int(next_dt.timestamp())),
        'limit': 500,
        'wallet': 'deposit'
    }
    result = bf_api_v1('/v1/history', payload, api_key, api_secret)
    if isinstance(result, list):
        earned = sum(float(e['amount']) for e in result if 'Margin Funding Payment' in e.get('description',''))
        return earned
    return 0.0

def get_total_wallet(api_key, api_secret):
    """Get TOTAL wallet balance (lending + available) using /v1/balances"""
    payload = {'request': '/v1/balances'}
    result = bf_api_v1('/v1/balances', payload, api_key, api_secret)
    wallet = {}
    if isinstance(result, list):
        for r in result:
            if r.get('type') == 'deposit':
                cur = r.get('currency', '').upper()
                amt = float(r.get('amount', 0))
                if cur in ('USD', 'UST') and amt > 0:
                    cur_disp = 'USDT' if cur == 'UST' else cur
                    wallet[cur_disp] = round(wallet.get(cur_disp, 0) + amt, 2)
    return wallet

# ============================================================
# V2 LEDGERS (historical balance – up to 6 years back)
# ============================================================
# Endpoint: POST /v2/auth/r/ledgers/{currency}/hist
# Auth:     bfx-apikey, bfx-nonce, bfx-signature headers
# Signature: HMAC-SHA384(secret, "/api" + path + nonce + body_str)

def get_ledger_entries(api_key, api_secret, currency, start_ts_ms, end_ts_ms, limit=2500):
    """
    Get a single page of ledger entries via V2 REST API.

    V2 REST auth: HMAC-SHA384(secret, "/api" + path + nonce + body)
    Returns a list of ledger entry arrays, or [] on error.
    """
    nonce = str(round(time.time() * 1_000_000))
    path = f'/v2/auth/r/ledgers/{currency}/hist'
    body = json.dumps({
        "start": start_ts_ms,
        "end": end_ts_ms,
        "limit": limit
    })
    sig_payload = f"/api{path}{nonce}{body}"
    sig = hmac.new(api_secret.encode('utf8'), sig_payload.encode('utf8'), hashlib.sha384).hexdigest()

    try:
        r = requests.post(
            f'https://api.bitfinex.com{path}',
            headers={
                'Content-Type': 'application/json',
                'bfx-apikey': api_key,
                'bfx-nonce': nonce,
                'bfx-signature': sig,
            },
            data=body.encode('utf8'),
            timeout=30,
        )
    except requests.RequestException as exc:
        print(f"    Request error: {exc}")
        return []

    if r.status_code == 429:
        wait = int(r.headers.get('Retry-After', 60))
        print(f"    Rate limited, waiting {wait}s...")
        time.sleep(wait)
        return get_ledger_entries(api_key, api_secret, currency, start_ts_ms, end_ts_ms, limit)

    if r.status_code != 200:
        print(f"    Error {r.status_code}: {r.text[:200]}")
        return []

    data = r.json()
    # Bitfinex error responses look like ["error", code, "message"]
    if isinstance(data, list) and len(data) > 0 and isinstance(data[0], str) and data[0] == 'error':
        print(f"    API error: {data}")
        return []

    return data if isinstance(data, list) else []


def get_all_ledger_entries(api_key, api_secret, currency, start_ts_ms, end_ts_ms):
    """
    Fetch ALL ledger entries for the date range, paginating backwards as needed.

    V2 returns entries sorted newest-first. When a page is full (2500 entries),
    we step back to the oldest timestamp in the batch and fetch the next page.
    """
    all_entries = []
    current_end = end_ts_ms

    while True:
        time.sleep(1.5)  # Respect rate limits between pages
        batch = get_ledger_entries(api_key, api_secret, currency, start_ts_ms, current_end)

        if not batch:
            break

        all_entries.extend(batch)

        if len(batch) < 2500:
            break  # Last page

        # Step back: use the oldest timestamp in this batch as the new end
        oldest_ts = min(e[3] for e in batch if isinstance(e, list) and len(e) > 3)
        if oldest_ts <= start_ts_ms:
            break
        current_end = oldest_ts - 1

    return all_entries


def compute_daily_data(entries, dates):
    """
    Derive per-day principal and earned from a list of V2 ledger entries.

    Entry format: [ID, CURRENCY, CURRENCY2, MTS, null, AMOUNT, BALANCE, null, DESCRIPTION]
      - entry[3]: timestamp (ms)
      - entry[5]: transaction delta
      - entry[6]: running wallet balance after this transaction
      - entry[8]: human-readable description

    Strategy:
      - earned  = sum of AMOUNT for entries whose DESCRIPTION contains
                  "Margin Funding Payment" for that day.
      - principal = BALANCE from the most recent (newest) entry of that day,
                    which represents the end-of-day wallet total.
      - If a day has no entries, carry the last known balance forward with
        earned = 0 (no new transactions).
    """
    # Group entries by UTC date string
    daily_entries = defaultdict(list)
    for e in entries:
        if not isinstance(e, list) or len(e) < 7:
            continue
        ts_ms = e[3]
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        daily_entries[dt.strftime('%Y-%m-%d')].append(e)

    # Sort each day's entries newest-first so index 0 = last transaction of day
    for day_list in daily_entries.values():
        day_list.sort(key=lambda e: e[3], reverse=True)

    daily_data = {}
    last_balance = None

    for date_str in sorted(dates):
        day_entries = daily_entries.get(date_str, [])

        # Sum "Margin Funding Payment" amounts as daily earned
        earned = 0.0
        for e in day_entries:
            desc = e[8] if len(e) > 8 else ''
            if 'Margin Funding Payment' in str(desc):
                earned += float(e[5])

        # End-of-day balance = BALANCE of the most recent entry for this day
        if day_entries:
            last_balance = float(day_entries[0][6])

        # Only record days where we have a positive balance
        if last_balance is not None and last_balance > 0:
            daily_data[date_str] = {
                'principal': round(last_balance, 8),
                'earned': round(earned, 8),
            }

    return daily_data


# ============================================================
# MAIN
# ============================================================

def fetch_all_data(start_date='2026-01-01', end_date='2026-03-29'):
    """
    Fetch historical daily wallet data for all accounts using V2 ledgers.

    Returns a nested dict:
      {account_name: {currency: {date_str: {"principal": float, "earned": float}}}}
    """
    # Build ordered list of date strings in range
    dates = []
    dt = datetime.strptime(start_date, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(end_date, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    while dt <= end_dt:
        dates.append(dt.strftime('%Y-%m-%d'))
        dt += timedelta(days=1)

    # Epoch-ms boundaries for the full range
    start_ts_ms = int(datetime.strptime(start_date, '%Y-%m-%d').replace(tzinfo=timezone.utc).timestamp() * 1000)
    # end of end_date = last millisecond of that day (start of next day - 1 ms)
    end_ts_ms = int(
        (datetime.strptime(end_date, '%Y-%m-%d').replace(tzinfo=timezone.utc)
         + timedelta(days=1, milliseconds=-1)).timestamp() * 1000
    )

    output = {}

    for acct in ACCOUNTS:
        name = acct['name']
        print(f"\nFetching {name}...")
        output[name] = {}

        for currency in ['USD', 'USDT']:
            bfx_cur = 'UST' if currency == 'USDT' else currency
            print(f"  {currency}: fetching ledger entries {start_date} → {end_date}...")

            entries = get_all_ledger_entries(
                acct['api_key'], acct['api_secret'],
                bfx_cur, start_ts_ms, end_ts_ms,
            )
            print(f"  {currency}: got {len(entries)} entries")

            output[name][currency] = compute_daily_data(entries, dates)

            time.sleep(2)  # Pause between currency requests

        print(f"  Done {name}")

    return output


if __name__ == '__main__':
    print("Bitfinex Historical Data Fetcher")
    print("================================")
    output = fetch_all_data()

    out_path = '/tmp/historical_wallet_data.json'
    with open(out_path, 'w') as f:
        json.dump(output, f, indent=2)

    total_records = sum(
        len(day_data)
        for acct_data in output.values()
        for day_data in acct_data.values()
    )
    print(f"\nSaved data for {len(output)} accounts ({total_records} daily records) to {out_path}")

