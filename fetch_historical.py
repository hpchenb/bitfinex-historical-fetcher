#!/usr/bin/env python3
"""
Bitfinex Historical Wallet Data Fetcher
========================================
Fetches daily TOTAL wallet balance (lending credits + available) for each account.

CRITICAL API NOTES:
- Use bfxapi Python library (https://github.com/bitfinexcom/bfxapi)
- /v1/balances returns total wallet = lent funds + available funds
- /v1/credits ONLY returns actively lent funds - DO NOT use for total
- V2 ledgers/hist can go back 6 years - use this for historical data
- Daily principal should COMPOUND with earned interest (principal_t = principal_{t-1} + earned_t)

Install: pip install bfxapi

Accounts:
  - a11, hpchen, tina.ding, jessie.chen, yihlan.chen
  - API keys stored in config_local.py format

Output:
  - CSV: date, user, currency, principal, earned, annualizedRate
  - JSON: /tmp/historical_wallet_data.json
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
# ACCOUNTS
# ============================================================
ACCOUNTS = [
    {"name": "a11",        "api_key": "c94f96ff9f74a72f662b5639e188a0ecc0d07d4d11c", "api_secret": "818b95999a76bdb45f89c8b8a8e987b912636cbdd42"},
    {"name": "hpchen",     "api_key": "2ad754108e17a90b2bbdea859638c2fddf3154e1848", "api_secret": "59f5d5e5945cc953c542b7e482bc366dd3ce3ba8874"},
    {"name": "tina.ding",  "api_key": "TeerMOmZszI3gDdwwGOogAMeBxZV67UkywTNutyh4TW", "api_secret": "FvhMuGxn94BecqY3OKcsTG9mKQwXg9g1UT4F99yzN2b"},
    {"name": "jessie.chen","api_key": "eFyrUZdFeG32AmgVLrvzGMH1lv9yM6giimoAcDKHEnb", "api_secret": "unqcYMUujLSEub6avUFL8fT8ObjxvK3E8Bnh1T4RWxb"},
    {"name": "yihlan.chen","api_key": "ZZ1tPQE6IXBzMsCcHmdVryHwMkQftv8O4WpsbpderQf", "api_secret": "p16SdPIJ6xdoBur0gnUetLwjR4odzIjpcK0354ZscyE"},
]

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
# V2 LEDGERS (for historical balance - up to 6 years)
# ============================================================
# V2 API: POST https://api.bitfinex.com/v2/auth/r/ledgers/hist
# Body: ["ledgers", "hist", {"currency": "USD", "start": ts_ms, "end": ts_ms, "limit": 500}]
# Auth: bfx-apikey, bfx-nonce, bfx-signature headers
# Signature: HMAC-SHA384 of "AUTH" + nonce string

def get_ledger_entries(api_key, api_secret, currency, start_ts_ms, end_ts_ms, limit=500):
    """Get ledger entries via V2 API for historical balance snapshots"""
    nonce = str(int(time.time() * 1e9))
    auth_payload = f"AUTH{nonce}"
    sig = hmac.new(api_secret.encode('utf8'), auth_payload.encode('utf8'), hashlib.sha384).hexdigest()
    
    body = [
        "ledgers", "hist",
        {
            "currency": currency,
            "start": start_ts_ms,
            "end": end_ts_ms,
            "limit": limit
        }
    ]
    body_bytes = json.dumps(body).encode('utf8')
    
    r = requests.post('https://api.bitfinex.com/v2/auth/r/ledgers/hist',
        headers={
            'Content-Type': 'application/json',
            'bfx-apikey': api_key,
            'bfx-nonce': nonce,
            'bfx-signature': sig,
        },
        data=body_bytes, timeout=30)
    
    if r.status_code != 200:
        return None
    return r.json()

# ============================================================
# MAIN
# ============================================================

def fetch_all_data(start_date='2026-01-01', end_date='2026-03-29'):
    """Fetch historical data for all accounts"""
    
    # Generate date range
    dates = []
    dt = datetime.strptime(start_date, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(end_date, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    while dt <= end_dt:
        dates.append(dt.strftime('%Y-%m-%d'))
        dt += timedelta(days=1)
    
    results = []
    
    for acct in ACCOUNTS:
        name = acct['name']
        print(f"\nFetching {name}...")
        
        # Get current wallet snapshot (for latest data)
        current_wallet = get_total_wallet(acct['api_key'], acct['api_secret'])
        print(f"  Current wallet: {current_wallet}")
        
        # Try V2 ledgers for historical data
        for currency in ['USD', 'USDT']:
            bfx_cur = 'UST' if currency == 'USDT' else currency
            
            for i, date_str in enumerate(dates):
                # Get daily earned
                earned = get_daily_earned(acct['api_key'], acct['api_secret'], bfx_cur, date_str)
                
                # Get wallet balance for that day
                # TODO: Use V2 ledgers to get historical snapshots
                # For now, use current wallet as estimate (this is wrong - needs fixing)
                
                time.sleep(0.5)
                
                if (i + 1) % 10 == 0:
                    print(f"  {currency} progress: {i+1}/{len(dates)}")
                    time.sleep(1)
        
        print(f"  Done {name}")
    
    return results

if __name__ == '__main__':
    print("Bitfinex Historical Data Fetcher")
    print("================================")
    results = fetch_all_data()
    
    # Save to JSON
    with open('/tmp/historical_wallet_data.json', 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved {len(results)} records to /tmp/historical_wallet_data.json")
