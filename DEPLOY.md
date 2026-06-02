# Render Deployment Notes

This project is a PostgreSQL-backed paper trading web app. User accounts, sessions, balances, watchlists, positions, orders, trades, deposits, equity history, and daily snapshots are stored server-side.

## Database

`server.py` runs `schema.sql` automatically on startup. Existing databases are kept compatible through `CREATE TABLE IF NOT EXISTS` and `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`.

Main tables:

- `users`: registered users and password hashes.
- `sessions`: login sessions.
- `accounts`: cash balance, starting cash, base currency, and active symbol.
- `watchlist`: user watchlist symbols.
- `positions`: current holdings, average price, currency, and opened time.
- `orders`: simulated market orders.
- `trades`: filled trades, including after-trade cash, after-trade position quantity, and equity before/after the fill.
- `account_transactions`: deposits and account cash events.
- `equity_history`: account equity snapshots after trades/deposits/resets and every minute while the service is running.
- `daily_snapshots`: daily equity, cash, positions value, total P/L, and return rate.

## Equity History

After startup, a background worker records every account once per minute into `equity_history`:

- timestamp
- total equity
- cash
- positions market value

Dashboard reads `equity_history` and supports Today, This Week, This Month, and All. The chart displays both total equity and cumulative return rate.

## Pages

The frontend is an SPA-style app. Page changes do not reload the whole site.

- `Dashboard`: account summary and equity chart.
- `Trading`: multi-stock table, stock search, quote chart, and trade ticket.
- `Portfolio`: current holdings and P/L statistics.
- `History`: trades, deposits, pagination, and CSV export.
- `Watchlist`: watchlist symbols.
- `Settings`: password change, deposits, starting cash, and reset.

## Render Settings

Create or attach a PostgreSQL database and ensure the service has:

```text
DATABASE_URL=postgresql://...
```

Recommended service settings:

```text
Build Command: pip install -r requirements.txt
Start Command: HOST=0.0.0.0 python server.py
Health Check Path: /api/health
```

`requirements.txt` intentionally uses:

```text
psycopg[binary]
```

without pinning a specific version.

## Local Run

```powershell
pip install -r requirements.txt
$env:DATABASE_URL="postgresql://user:password@host:5432/dbname"
python server.py
```

Open:

```text
http://127.0.0.1:8765/
```

Useful checks:

```text
/api/health
/api/me
/api/state
/api/history?symbol=AAPL&range=1d
```
