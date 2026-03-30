# Dashboard Redesign Requirements

## Objective
Redesign the "Real-time Dashboard" (實時 Dashboard) tab to display current lending status with proper layout. USD and USDT should each have their own dedicated view/section.

## Current Data Structure

### data.ts - existing data model
```typescript
interface Offer { id: number; currency: string; amount: number; rate: number; period: number; }
interface HistoryItem { date: string; days: number; rate: number; earned: number; }
interface AccountData {
  wallet: Record<string, number>;   // USD and USDT balances
  offers: Offer[];                 // Active lending orders
  history: HistoryItem[];           // Historical daily data
}
export const mockData: Record<string, AccountData> = { ... };
```

### Current mockData example (a11)
- wallet: { USD: 38856.41, USDT: 12317.14 }
- offers: Array of ~27 active orders with fields: id, currency, amount, rate, period
- history: Daily interest records

## Target Layout

### Per-Currency View (USD page / USDT page)

Each currency (USD and USDT) should have its own tab/page with 4 cards:

---

**Card 1: 融資錢包 (Funding Wallet)**

Summary stats for selected currency:
- 總額 (Total): wallet[currency] + sum of active offers for that currency
- 閒置金額 (Idle): wallet[currency] minus sum of active offers
- 累計收益 (Cumulative Earnings): sum of all earned interest for this currency from history
- 投資天數 (Days): count of days with data in history
- 歷史年化 (Historical Annualized): (totalEarned / avgPrincipal / days) * 365 * 100
- 加權平均利率 (Weighted Avg Rate): sum(offer.amount * offer.rate) / sum(offer.amount)

---

**Card 2: 放款中訂單 (Active Loans)**

Table showing currently active lending orders for this currency:
- Columns: 金額 (Amount), 日利率 (Daily Rate as %), 年化利率 (Annualized Rate as %), 期限 (Period in days), 到期時間 (Expiry countdown)
- Sort by annualized rate descending
- Show total count and sum at bottom
- 加權平均年化 = sum(amount * rate) / sum(amount) of all displayed orders

---

**Card 3: 掛單中訂單 (Pending Orders)**

Orders that have been placed but NOT yet matched/filled (from `/v1/offers` endpoint where `is_live=true` and `is_cancelled=false`). Same table structure as Active Loans, but visually distinguish from filled orders.

---

**Card 4: 近期利息 (Recent Interest)**

Daily interest earned for last 7 days for this currency:
- Table: 日期 (Date), 利息 (Earned), 年化 (Annualized Rate for that day)
- Show 7 most recent days from allDailyData
- Calculate daily annualized rate = earned / principal * 365

---

## Implementation Notes

### File structure
- src/components/RealTimeDashboard.tsx - new main component (replaces current realtime section)
- Modify src/App.tsx to use tab system: [錢包總覽 | USD | USDT | 月度匯總] or similar

### Currency tabs
- Tab 1: 全部 (All) - combined view for all currencies
- Tab 2: USD - USD only view
- Tab 3: USDT - USDT only view

### Data flow
- Fetch current offers/wallet from Bitfinex API on page load (not just static mockData)
- Use the `update_data.py` script data as historical baseline
- Active orders come from `/v1/offers` endpoint

### Styling
- Keep existing dark theme (bg-slate-800 cards)
- Use cyan/teal accent colors
- Compact card layout, 2 columns on desktop
- Responsive: stack to single column on mobile

### Deadline / Swap logic
- For expiry countdown: calculate time remaining from now to (offer MTS + period * 86400000 ms)
- If expired (time < 0), show "已到期" (expired)

### Empty states
- If no active orders: show "目前無進行中的訂單"
- If no pending orders: show "目前無掛單"

## Verification
After implementation:
1. Select account "a11" → USD tab → should show ~20 active USD orders and ~7 pending USDT orders
2. Wallet total should match sum of wallet + active offers
3. Historical annualized rate should match existing Monthly Summary calculation
