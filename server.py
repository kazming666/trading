from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse, quote
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from http import cookies
from decimal import Decimal
from pathlib import Path
import base64
import hashlib
import hmac
import json
import math
import os
import secrets
import threading
import time

import strategy_engine

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # Render installs this from requirements.txt.
    psycopg = None
    dict_row = None


HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8765"))
DATABASE_URL = os.environ.get("DATABASE_URL", "")
SESSION_COOKIE = "ptd_session"
SESSION_DAYS = 30
DEFAULT_CASH = Decimal("100000")
DEFAULT_SYMBOLS = ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "600519.SS", "000001.SZ", "0700.HK"]
PROJECT_DIR = Path(__file__).resolve().parent

YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={range}&interval={interval}"
YAHOO_CHART_PERIOD = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?period1={period1}&period2={period2}&interval={interval}"
YAHOO_SEARCH = "https://query1.finance.yahoo.com/v1/finance/search?q={query}&quotesCount=8&newsCount=0"
HISTORY_RANGES = {
    "1d": ("1d", "1m"),
    "1wk": ("5d", "5m"),
    "1mo": ("1mo", "30m"),
    "3mo": ("3mo", "1d"),
    "6mo": ("6mo", "1d"),
    "1y": ("1y", "1d"),
}
BACKTEST_RANGES = {
    "1y": ("1y", "1d"),
    "3y": ("3y", "1d"),
    "5y": ("5y", "1d"),
    "10y": ("10y", "1d"),
    "max": ("max", "1d"),
}
BACKTEST_SYMBOLS = {"AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "META", "GOOGL", "AMD"}


def fetch_json(url):
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 PaperTradingDesk/2.0",
            "Accept": "application/json",
        },
    )
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def normalize_market_symbol(symbol):
    normalized = symbol.strip().upper()
    if normalized.endswith(".HK"):
        code = normalized[:-3]
        if code.isdigit() and len(code) == 5:
            return f"{code[-4:]}.HK"
    return normalized


def yahoo_chart(symbol, range_value="1d", interval="1m"):
    data = fetch_json(YAHOO_CHART.format(symbol=quote(symbol), range=range_value, interval=interval))
    result = data.get("chart", {}).get("result") or []
    if not result:
        error = data.get("chart", {}).get("error") or {}
        raise ValueError(error.get("description") or "No quote data returned")
    return result[0]


def yahoo_chart_period(symbol, start_date, end_date, interval="1d"):
    period1 = int(start_date.replace(tzinfo=timezone.utc).timestamp())
    period2 = int((end_date + timedelta(days=1)).replace(tzinfo=timezone.utc).timestamp())
    data = fetch_json(YAHOO_CHART_PERIOD.format(symbol=quote(symbol), period1=period1, period2=period2, interval=interval))
    result = data.get("chart", {}).get("result") or []
    if not result:
        error = data.get("chart", {}).get("error") or {}
        raise ValueError(error.get("description") or "No historical data returned")
    return result[0]


def parse_date_picker(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).replace(hour=0, minute=0, second=0, microsecond=0)
    except ValueError:
        return None


def candidate_symbols(query):
    normalized = query.strip().upper()
    digits = "".join(ch for ch in normalized if ch.isdigit())
    if len(digits) == 6:
        candidates = [f"{digits}.SS", f"{digits}.SZ"] if digits.startswith(("5", "6", "9")) else [f"{digits}.SZ", f"{digits}.SS"]
    elif len(digits) == 5 and normalized == digits:
        candidates = [f"{digits[-4:]}.HK"]
    elif len(digits) == 4 and normalized == digits:
        candidates = [f"{digits}.HK"]
    else:
        candidates = [normalized]
    seen = set()
    return [item for item in candidates if not (item in seen or seen.add(item))]


def normalize_quote(symbol):
    chart = yahoo_chart(symbol)
    meta = chart.get("meta", {})
    price = meta.get("regularMarketPrice")
    previous_close = meta.get("chartPreviousClose") or meta.get("previousClose")
    if price is None or previous_close is None:
        raise ValueError("Quote is missing price or previous close")

    return {
        "symbol": meta.get("symbol") or symbol.upper(),
        "name": meta.get("longName") or meta.get("shortName") or symbol.upper(),
        "exchange": meta.get("exchangeName") or meta.get("fullExchangeName") or "",
        "currency": meta.get("currency") or "USD",
        "price": float(price),
        "previousClose": float(previous_close),
        "regularMarketTime": meta.get("regularMarketTime"),
        "marketState": meta.get("marketState") or "",
    }


def normalize_history(symbol, range_key):
    range_value, interval = HISTORY_RANGES.get(range_key, HISTORY_RANGES["1d"])
    chart = yahoo_chart(symbol, range_value, interval)
    timestamps = chart.get("timestamp") or []
    quote_data = ((chart.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quote_data.get("close") or []
    points = [{"t": ts * 1000, "p": close} for ts, close in zip(timestamps, closes) if close is not None]
    if len(points) < 2 and range_key == "1d":
        chart = yahoo_chart(symbol, "5d", "5m")
        timestamps = chart.get("timestamp") or []
        quote_data = ((chart.get("indicators") or {}).get("quote") or [{}])[0]
        closes = quote_data.get("close") or []
        points = [{"t": ts * 1000, "p": close} for ts, close in zip(timestamps, closes) if close is not None]
    if not points:
        raise ValueError("No historical prices returned")
    return {"symbol": symbol.upper(), "range": range_key, "points": points[-360:]}


def normalize_backtest_history(symbol, range_key, start_date=None, end_date=None):
    interval = "1d"
    if start_date and end_date:
        if end_date <= start_date:
            raise ValueError("End date must be after start date")
        chart = yahoo_chart_period(symbol, start_date, end_date, interval)
    else:
        range_value, interval = BACKTEST_RANGES.get(range_key, BACKTEST_RANGES["1y"])
        chart = yahoo_chart(symbol, range_value, interval)
    timestamps = chart.get("timestamp") or []
    quote_data = ((chart.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quote_data.get("close") or []
    points = [{"t": ts * 1000, "p": float(close)} for ts, close in zip(timestamps, closes) if close is not None]
    if len(points) < 2:
        raise ValueError("No historical prices returned")
    return points


def clean_strategy_params(strategy_name, params):
    params = params or {}

    def as_int(name, default, minimum=1, maximum=400):
        try:
            value = int(params.get(name, default))
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(maximum, value))

    def as_float(name, default, minimum=0, maximum=100):
        try:
            value = float(params.get(name, default))
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(maximum, value))

    if strategy_name == "moving_average":
        cleaned = {"fastMa": as_int("fastMa", 5), "slowMa": as_int("slowMa", 20)}
    if strategy_name == "rsi":
        cleaned = {
            "period": as_int("period", 14),
            "oversold": as_float("oversold", 30),
            "overbought": as_float("overbought", 70),
        }
    if strategy_name == "macd":
        cleaned = {"fast": as_int("fast", 12), "slow": as_int("slow", 26), "signal": as_int("signal", 9)}
    if strategy_name not in {"moving_average", "rsi", "macd"}:
        cleaned = {}
    cleaned["frequencyBars"] = as_int("frequencyBars", 1, 1, 60)
    return cleaned


def calculate_max_drawdown(equity_values):
    peak = None
    max_drawdown = 0
    for value in equity_values:
        if peak is None or value > peak:
            peak = value
        if peak and peak > 0:
            drawdown = (peak - value) / peak * 100
            max_drawdown = max(max_drawdown, drawdown)
    return max_drawdown


def calculate_sharpe_ratio(equity_curve):
    returns = []
    previous = None
    for point in equity_curve:
        equity = float(point["equity"])
        if previous and previous > 0:
            returns.append((equity - previous) / previous)
        previous = equity
    if len(returns) < 2:
        return 0
    mean_return = sum(returns) / len(returns)
    variance = sum((value - mean_return) ** 2 for value in returns) / (len(returns) - 1)
    std_dev = math.sqrt(variance)
    if std_dev == 0:
        return 0
    return (mean_return / std_dev) * math.sqrt(252)


def optimizer_grid(strategy_name, base_params):
    frequency_bars = int(base_params.get("frequencyBars") or 1)
    if strategy_name == "moving_average":
        return [
            {"fastMa": fast, "slowMa": slow, "frequencyBars": frequency_bars}
            for fast in [5, 10, 15]
            for slow in [20, 30, 50]
            if fast < slow
        ]
    if strategy_name == "rsi":
        return [
            {"period": period, "oversold": oversold, "overbought": overbought, "frequencyBars": frequency_bars}
            for period in [7, 14, 21]
            for oversold in [25, 30, 35]
            for overbought in [65, 70, 75]
            if oversold < overbought
        ]
    if strategy_name == "macd":
        return [
            {"fast": fast, "slow": slow, "signal": signal, "frequencyBars": frequency_bars}
            for fast in [8, 12, 15]
            for slow in [21, 26, 35]
            for signal in [5, 9]
            if fast < slow
        ]
    return [base_params]


def summarize_backtest_result(result):
    return {
        "strategy": result["strategy"],
        "symbol": result["symbol"],
        "params": result["params"],
        "returnPct": result["returnPct"],
        "buyHoldReturnPct": result["buyHoldReturnPct"],
        "alphaPct": result["alphaPct"],
        "annualReturnPct": result["annualReturnPct"],
        "maxDrawdown": result["maxDrawdown"],
        "winRate": result["winRate"],
        "tradeCount": result["tradeCount"],
        "avgProfit": result["avgProfit"],
        "avgLoss": result["avgLoss"],
        "sharpeRatio": result["sharpeRatio"],
    }


def run_strategy_backtest(strategy_name, symbol, points, params):
    starting_cash = 100000.0
    cash = starting_cash
    qty = 0.0
    entry = None
    trades = []
    equity_curve = []
    first_price = float(points[0]["p"])
    buy_hold_qty = starting_cash / first_price if first_price > 0 else 0
    frequency_bars = max(1, int(params.get("frequencyBars") or 1))

    for index, point in enumerate(points):
        price = float(point["p"])
        if price <= 0:
            continue
        history = points[: index + 1]
        signal = {"signal": "HOLD"}
        if index % frequency_bars == 0:
            signal = strategy_engine.generate_signal(
                strategy_name,
                symbol,
                {"symbol": symbol, "price": price, "history": history, "params": params},
            )
        if signal["signal"] == "BUY" and qty <= 0 and cash > 0:
            qty = cash / price
            entry = {
                "buyTime": point["t"],
                "buyPrice": price,
                "qty": qty,
                "buyValue": cash,
            }
            cash = 0.0
        elif signal["signal"] == "SELL" and qty > 0:
            exit_value = qty * price
            buy_value = entry["buyValue"] if entry else qty * price
            pnl = exit_value - buy_value
            trade_return = (pnl / buy_value * 100) if buy_value else 0
            trades.append(
                {
                    "buyTime": entry["buyTime"] if entry else point["t"],
                    "sellTime": point["t"],
                    "buyPrice": entry["buyPrice"] if entry else price,
                    "sellPrice": price,
                    "qty": qty,
                    "returnPct": trade_return,
                    "pnl": pnl,
                }
            )
            cash = exit_value
            qty = 0.0
            entry = None
        equity = cash + qty * price
        buy_hold_equity = buy_hold_qty * price
        equity_curve.append(
            {
                "time": point["t"],
                "equity": equity,
                "buyHoldEquity": buy_hold_equity,
                "cash": cash,
                "positionValue": qty * price,
            }
        )

    if not equity_curve:
        raise ValueError("Backtest has no usable price points")

    end_price = float(points[-1]["p"])
    final_equity = cash + qty * end_price
    return_pct = (final_equity - starting_cash) / starting_cash * 100
    buy_hold_final_equity = buy_hold_qty * end_price
    buy_hold_return_pct = (buy_hold_final_equity - starting_cash) / starting_cash * 100
    alpha_pct = return_pct - buy_hold_return_pct
    start_time = datetime.fromtimestamp(points[0]["t"] / 1000, tz=timezone.utc)
    end_time = datetime.fromtimestamp(points[-1]["t"] / 1000, tz=timezone.utc)
    days = max((end_time - start_time).total_seconds() / 86400, 1)
    annual_return = ((final_equity / starting_cash) ** (365 / days) - 1) * 100 if final_equity > 0 else -100
    max_drawdown = calculate_max_drawdown([point["equity"] for point in equity_curve])
    sharpe_ratio = calculate_sharpe_ratio(equity_curve)
    trade_pnls = [trade["pnl"] for trade in trades]
    wins = [pnl for pnl in trade_pnls if pnl > 0]
    losses = [pnl for pnl in trade_pnls if pnl < 0]
    win_rate = len(wins) / len(trades) * 100 if trades else 0
    return {
        "strategy": strategy_name,
        "symbol": symbol,
        "initialCash": starting_cash,
        "finalEquity": final_equity,
        "buyHoldFinalEquity": buy_hold_final_equity,
        "startDate": int(start_time.timestamp() * 1000),
        "endDate": int(end_time.timestamp() * 1000),
        "returnPct": return_pct,
        "buyHoldReturnPct": buy_hold_return_pct,
        "alphaPct": alpha_pct,
        "annualReturnPct": annual_return,
        "maxDrawdown": max_drawdown,
        "sharpeRatio": sharpe_ratio,
        "winRate": win_rate,
        "tradeCount": len(trades),
        "avgProfit": (sum(wins) / len(wins)) if wins else 0,
        "avgLoss": (sum(losses) / len(losses)) if losses else 0,
        "equityCurve": equity_curve,
        "trades": trades,
        "params": params,
    }


def decimal_to_json(value):
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def db_ready():
    return bool(psycopg and DATABASE_URL)


def db_connect():
    if not db_ready():
        raise RuntimeError("PostgreSQL is not configured. Set DATABASE_URL and install requirements.txt.")
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def init_db():
    if not db_ready():
        print("Warning: DATABASE_URL is not configured; auth/account APIs will return 503.")
        return
    schema_path = PROJECT_DIR / "schema.sql"
    schema = schema_path.read_text(encoding="utf-8")
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(schema)
        conn.commit()


def hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 260000)
    return base64.b64encode(salt).decode("ascii"), base64.b64encode(digest).decode("ascii")


def verify_password(password, salt_b64, hash_b64):
    salt = base64.b64decode(salt_b64.encode("ascii"))
    _, candidate = hash_password(password, salt)
    return hmac.compare_digest(candidate, hash_b64)


def token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_user_session(conn, user_id):
    token = secrets.token_urlsafe(32)
    expires_at = int(time.time()) + SESSION_DAYS * 24 * 60 * 60
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO sessions (user_id, token_hash, expires_at) VALUES (%s, %s, to_timestamp(%s))",
            (user_id, token_hash(token), expires_at),
        )
    return token, expires_at


def ensure_account(conn, user_id):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO accounts (user_id, cash_balance, starting_cash, base_currency, active_symbol)
            VALUES (%s, %s, %s, 'USD', 'AAPL')
            ON CONFLICT (user_id) DO NOTHING
            """,
            (user_id, DEFAULT_CASH, DEFAULT_CASH),
        )
        for sort_order, symbol in enumerate(DEFAULT_SYMBOLS):
            cur.execute(
                """
                INSERT INTO watchlist (user_id, symbol, sort_order)
                VALUES (%s, %s, %s)
                ON CONFLICT (user_id, symbol) DO NOTHING
                """,
                (user_id, symbol, sort_order),
            )


def estimate_positions_value(conn, user_id):
    with conn.cursor() as cur:
        cur.execute("SELECT symbol, qty, avg_price FROM positions WHERE user_id = %s AND qty > 0", (user_id,))
        positions = cur.fetchall()
    total = Decimal("0")
    for position in positions:
        try:
            price = Decimal(str(normalize_quote(position["symbol"])["price"]))
        except Exception:
            price = position["avg_price"]
        total += position["qty"] * price
    return total


def portfolio_totals(conn, user_id):
    with conn.cursor() as cur:
        cur.execute("SELECT cash_balance, starting_cash FROM accounts WHERE user_id = %s", (user_id,))
        account = cur.fetchone()
    positions_value = estimate_positions_value(conn, user_id)
    equity = account["cash_balance"] + positions_value
    return {
        "cash": account["cash_balance"],
        "starting_cash": account["starting_cash"],
        "positions_value": positions_value,
        "equity": equity,
    }


def record_daily_snapshot(conn, user_id, totals=None):
    totals = totals or portfolio_totals(conn, user_id)
    starting_cash = totals["starting_cash"] or Decimal("0")
    total_pnl = totals["equity"] - starting_cash
    return_rate = (total_pnl / starting_cash * Decimal("100")) if starting_cash > 0 else Decimal("0")
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO daily_snapshots
                (user_id, snapshot_date, equity, cash_balance, positions_value, total_pnl, return_rate)
            VALUES (%s, CURRENT_DATE, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id, snapshot_date)
            DO UPDATE SET
                equity = EXCLUDED.equity,
                cash_balance = EXCLUDED.cash_balance,
                positions_value = EXCLUDED.positions_value,
                total_pnl = EXCLUDED.total_pnl,
                return_rate = EXCLUDED.return_rate,
                updated_at = now()
            """,
            (
                user_id,
                totals["equity"],
                totals["cash"],
                totals["positions_value"],
                total_pnl,
                return_rate,
            ),
        )
    return totals


def record_equity_snapshot(conn, user_id, reason, related_trade_id=None):
    totals = portfolio_totals(conn, user_id)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO equity_history (user_id, equity, cash_balance, positions_value, reason, related_trade_id)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                user_id,
                totals["equity"],
                totals["cash"],
                totals["positions_value"],
                reason,
                related_trade_id,
            ),
        )
    record_daily_snapshot(conn, user_id, totals)
    return totals["equity"]


def record_all_equity_snapshots():
    if not db_ready():
        return
    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT user_id FROM accounts ORDER BY user_id")
                user_ids = [row["user_id"] for row in cur.fetchall()]
            for user_id in user_ids:
                try:
                    record_equity_snapshot(conn, user_id, "adjustment")
                    conn.commit()
                except Exception as error:
                    conn.rollback()
                    print(f"Warning: equity snapshot failed for user {user_id}: {error}")
    except Exception as error:
        print(f"Warning: equity snapshot worker failed: {error}")


def start_equity_snapshot_worker():
    if not db_ready():
        return

    def run():
        while True:
            record_all_equity_snapshots()
            time.sleep(60)

    thread = threading.Thread(target=run, name="equity-snapshot-worker", daemon=True)
    thread.start()


class Handler(SimpleHTTPRequestHandler):
    GET_ROUTES = {
        "/api/search": "handle_search",
        "/api/quote": "handle_quote",
        "/api/history": "handle_history",
        "/api/health": "handle_health",
        "/api/me": "handle_me",
        "/api/state": "handle_state",
        "/api/strategy/signals": "handle_strategy_signals",
        "/api/strategy/backtests": "handle_strategy_backtests",
    }
    POST_ROUTES = {
        "/api/auth/register": "handle_register",
        "/api/auth/login": "handle_login",
        "/api/auth/logout": "handle_logout",
        "/api/auth/password": "handle_change_password",
        "/api/account/deposit": "handle_deposit",
        "/api/account/reset": "handle_reset_account",
        "/api/account/active-symbol": "handle_active_symbol",
        "/api/watchlist": "handle_add_watchlist",
        "/api/trade": "handle_trade",
        "/api/strategy/run": "handle_run_strategy",
        "/api/strategy/backtest": "handle_strategy_backtest",
        "/api/strategy/optimize": "handle_strategy_optimize",
        "/api/history/clear": "handle_clear_history",
    }
    DELETE_ROUTES = {
        "/api/watchlist": "handle_delete_watchlist",
    }

    def guess_type(self, path):
        content_type = super().guess_type(path)
        if content_type in {"text/html", "text/css", "text/javascript", "application/javascript"}:
            return f"{content_type}; charset=utf-8"
        return content_type

    def end_json(self, status, payload, headers=None):
        body = json.dumps(payload, ensure_ascii=False, default=decimal_to_json).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def send_error(self, code, message=None, explain=None):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self.end_json(code, {"error": message or self.responses.get(code, ["Error"])[0], "status": code})
            return
        super().send_error(code, message, explain)

    def route_path(self):
        parsed = urlparse(self.path)
        return parsed, (parsed.path.rstrip("/") or "/")

    def do_GET(self):
        parsed, path = self.route_path()
        route = self.GET_ROUTES.get(path)
        if route:
            self.safe_call(getattr(self, route), parsed)
            return
        if parsed.path.startswith("/api/"):
            self.end_json(404, {"error": "API endpoint not found", "path": parsed.path})
            return
        super().do_GET()

    def do_POST(self):
        parsed, path = self.route_path()
        route = self.POST_ROUTES.get(path)
        if route:
            self.safe_call(getattr(self, route), parsed)
            return
        if parsed.path.startswith("/api/"):
            self.end_json(404, {"error": "API endpoint not found", "path": parsed.path})
            return
        self.send_error(404, "Not Found")

    def do_DELETE(self):
        parsed, path = self.route_path()
        route = self.DELETE_ROUTES.get(path)
        if route:
            self.safe_call(getattr(self, route), parsed)
            return
        if parsed.path.startswith("/api/"):
            self.end_json(404, {"error": "API endpoint not found", "path": parsed.path})
            return
        self.send_error(404, "Not Found")

    def do_HEAD(self):
        parsed, path = self.route_path()
        if path in self.GET_ROUTES or path in self.POST_ROUTES or parsed.path.startswith("/api/"):
            self.send_response(200 if path in self.GET_ROUTES or path in self.POST_ROUTES else 404)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        super().do_HEAD()

    def do_OPTIONS(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self.send_response(204)
            self.send_header("Allow", "GET, POST, DELETE, HEAD, OPTIONS")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_error(404, "Not Found")

    def safe_call(self, handler, parsed):
        try:
            handler(parsed)
        except RuntimeError as error:
            self.end_json(503, {"error": str(error)})
        except Exception as error:
            self.end_json(500, {"error": f"Server error: {error}"})

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw or "{}")

    def cookie_header(self, token, expires_at):
        secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
        max_age = max(0, expires_at - int(time.time()))
        return f"{SESSION_COOKIE}={token}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Lax{secure}"

    def clear_cookie_header(self):
        secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
        return f"{SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax{secure}"

    def current_user(self):
        raw_cookie = self.headers.get("Cookie") or ""
        jar = cookies.SimpleCookie(raw_cookie)
        morsel = jar.get(SESSION_COOKIE)
        if not morsel:
            return None
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT u.id, u.email, u.display_name
                    FROM sessions s
                    JOIN users u ON u.id = s.user_id
                    WHERE s.token_hash = %s AND s.expires_at > now()
                    """,
                    (token_hash(morsel.value),),
                )
                return cur.fetchone()

    def require_user(self):
        user = self.current_user()
        if not user:
            self.end_json(401, {"error": "Login required"})
            return None
        return user

    def handle_health(self, parsed):
        db_ok = False
        if db_ready():
            try:
                with db_connect() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT 1")
                        db_ok = True
            except Exception:
                db_ok = False
        self.end_json(200, {"ok": True, "database": db_ok, "serverTime": int(time.time() * 1000)})

    def handle_register(self, parsed):
        data = self.read_json()
        email = (data.get("email") or "").strip().lower()
        password = data.get("password") or ""
        display_name = (data.get("displayName") or email.split("@")[0] or "Trader").strip()
        if "@" not in email or len(password) < 6:
            self.end_json(400, {"error": "Use a valid email and a password with at least 6 characters."})
            return
        salt, password_hash = hash_password(password)
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM users WHERE email = %s", (email,))
                if cur.fetchone():
                    self.end_json(409, {"error": "Email already registered."})
                    return
                cur.execute(
                    """
                    INSERT INTO users (email, display_name, password_salt, password_hash)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id, email, display_name
                    """,
                    (email, display_name, salt, password_hash),
                )
                user = cur.fetchone()
                ensure_account(conn, user["id"])
                record_equity_snapshot(conn, user["id"], "reset")
                token, expires_at = create_user_session(conn, user["id"])
            conn.commit()
        self.end_json(201, {"user": dict(user)}, {"Set-Cookie": self.cookie_header(token, expires_at)})

    def handle_login(self, parsed):
        data = self.read_json()
        email = (data.get("email") or "").strip().lower()
        password = data.get("password") or ""
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, email, display_name, password_salt, password_hash FROM users WHERE email = %s", (email,))
                user = cur.fetchone()
                if not user or not verify_password(password, user["password_salt"], user["password_hash"]):
                    self.end_json(401, {"error": "Invalid email or password."})
                    return
                ensure_account(conn, user["id"])
                token, expires_at = create_user_session(conn, user["id"])
            conn.commit()
        self.end_json(
            200,
            {"user": {"id": user["id"], "email": user["email"], "display_name": user["display_name"]}},
            {"Set-Cookie": self.cookie_header(token, expires_at)},
        )

    def handle_logout(self, parsed):
        raw_cookie = self.headers.get("Cookie") or ""
        jar = cookies.SimpleCookie(raw_cookie)
        morsel = jar.get(SESSION_COOKIE)
        if morsel and db_ready():
            with db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM sessions WHERE token_hash = %s", (token_hash(morsel.value),))
                conn.commit()
        self.end_json(200, {"ok": True}, {"Set-Cookie": self.clear_cookie_header()})

    def handle_change_password(self, parsed):
        user = self.require_user()
        if not user:
            return
        data = self.read_json()
        current_password = data.get("currentPassword") or ""
        new_password = data.get("newPassword") or ""
        if len(new_password) < 6:
            self.end_json(400, {"error": "New password must be at least 6 characters."})
            return
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT password_salt, password_hash FROM users WHERE id = %s", (user["id"],))
                password_row = cur.fetchone()
                if not password_row or not verify_password(current_password, password_row["password_salt"], password_row["password_hash"]):
                    self.end_json(401, {"error": "Current password is incorrect."})
                    return
                salt, password_hash = hash_password(new_password)
                cur.execute(
                    "UPDATE users SET password_salt = %s, password_hash = %s, updated_at = now() WHERE id = %s",
                    (salt, password_hash, user["id"]),
                )
            conn.commit()
        self.end_json(200, {"ok": True})

    def handle_me(self, parsed):
        if not db_ready():
            self.end_json(503, {"error": "Database is not configured."})
            return
        user = self.current_user()
        self.end_json(200, {"user": dict(user) if user else None})

    def load_state(self, user_id):
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT cash_balance, starting_cash, base_currency, active_symbol FROM accounts WHERE user_id = %s", (user_id,))
                account = cur.fetchone()
                cur.execute("SELECT symbol FROM watchlist WHERE user_id = %s ORDER BY sort_order, symbol", (user_id,))
                symbols = [row["symbol"] for row in cur.fetchall()]
                cur.execute("SELECT symbol, qty, avg_price, currency, opened_at FROM positions WHERE user_id = %s AND qty > 0 ORDER BY symbol", (user_id,))
                positions = cur.fetchall()
                cur.execute(
                    """
                    SELECT id, order_id, symbol, side, qty, price, value, currency,
                           account_balance_after, position_qty_after, realized_pnl,
                           equity_before, equity_after, equity_change, executed_at
                    FROM trades WHERE user_id = %s ORDER BY executed_at DESC LIMIT 500
                    """,
                    (user_id,),
                )
                trades = cur.fetchall()
                cur.execute(
                    """
                    SELECT id, amount, currency, created_at
                    FROM account_transactions
                    WHERE user_id = %s AND type = 'deposit'
                    ORDER BY created_at DESC LIMIT 160
                    """,
                    (user_id,),
                )
                deposits = cur.fetchall()
                cur.execute(
                    """
                    SELECT COALESCE(SUM(amount), 0) AS deposit_total
                    FROM account_transactions
                    WHERE user_id = %s AND type = 'deposit'
                    """,
                    (user_id,),
                )
                deposit_totals = cur.fetchone()
                cur.execute(
                    """
                    SELECT equity, cash_balance, positions_value, reason, created_at
                    FROM (
                        SELECT equity, cash_balance, positions_value, reason, created_at
                        FROM equity_history
                        WHERE user_id = %s
                        ORDER BY created_at DESC
                        LIMIT 10000
                    ) recent_equity_history
                    ORDER BY created_at ASC
                    """,
                    (user_id,),
                )
                equity_history = cur.fetchall()
                if not equity_history:
                    record_equity_snapshot(conn, user_id, "adjustment")
                    cur.execute(
                        """
                        SELECT equity, cash_balance, positions_value, reason, created_at
                        FROM equity_history
                        WHERE user_id = %s
                        ORDER BY created_at ASC
                        """,
                        (user_id,),
                    )
                    equity_history = cur.fetchall()
                cur.execute(
                    """
                    SELECT snapshot_date, equity, cash_balance, positions_value, total_pnl, return_rate
                    FROM daily_snapshots
                    WHERE user_id = %s
                    ORDER BY snapshot_date ASC
                    LIMIT 1000
                    """,
                    (user_id,),
                )
                daily_snapshots = cur.fetchall()
                cur.execute(
                    """
                    SELECT id, symbol, strategy_name, signal, reason, price, created_at
                    FROM signal_history
                    WHERE user_id = %s
                    ORDER BY created_at DESC
                    LIMIT 100
                    """,
                    (user_id,),
                )
                signal_history = cur.fetchall()
                cur.execute(
                    """
                    SELECT *
                    FROM (
                        SELECT
                            id,
                            strategy,
                            symbol,
                            params,
                            start_time AS start_date,
                            end_time AS end_date,
                            return_pct,
                            annual_return_pct,
                            max_drawdown,
                            sharpe_ratio,
                            win_rate,
                            trade_count,
                            avg_profit,
                            avg_loss,
                            runtime_ms,
                            created_at
                        FROM backtest_results
                        WHERE user_id = %s
                        UNION ALL
                        SELECT
                            id,
                            strategy,
                            symbol,
                            '{}'::jsonb AS params,
                            start_date,
                            end_date,
                            return_pct,
                            0 AS annual_return_pct,
                            max_drawdown,
                            0 AS sharpe_ratio,
                            win_rate,
                            trade_count,
                            0 AS avg_profit,
                            0 AS avg_loss,
                            0 AS runtime_ms,
                            created_at
                        FROM backtest_history
                        WHERE user_id = %s
                    ) combined_backtests
                    ORDER BY created_at DESC
                    LIMIT 100
                    """,
                    (user_id, user_id),
                )
                backtest_history = cur.fetchall()
                cur.execute(
                    """
                    SELECT
                        COUNT(*) AS total_trades,
                        COALESCE(SUM(CASE WHEN side = 'buy' THEN value ELSE 0 END), 0) AS total_buy_value,
                        COALESCE(SUM(CASE WHEN side = 'sell' THEN value ELSE 0 END), 0) AS total_sell_value,
                        COALESCE(SUM(CASE WHEN side = 'sell' AND realized_pnl > 0 THEN 1 ELSE 0 END), 0) AS winning_sells,
                        COALESCE(SUM(CASE WHEN side = 'sell' THEN 1 ELSE 0 END), 0) AS sell_count,
                        MAX(realized_pnl) AS max_profit_trade,
                        MIN(realized_pnl) AS max_loss_trade
                    FROM trades
                    WHERE user_id = %s
                    """,
                    (user_id,),
                )
                stats = cur.fetchone()
        sell_count = int(stats["sell_count"] or 0)
        winning_sells = int(stats["winning_sells"] or 0)
        starting_cash = account["starting_cash"] or Decimal("0")
        deposits_total = deposit_totals["deposit_total"] or Decimal("0")
        return_base = starting_cash + deposits_total
        return {
            "startingCash": starting_cash,
            "cash": account["cash_balance"],
            "baseCurrency": account["base_currency"],
            "activeSymbol": account["active_symbol"],
            "symbols": symbols or DEFAULT_SYMBOLS,
            "positions": {
                row["symbol"]: {
                    "qty": row["qty"],
                    "avgPrice": row["avg_price"],
                    "currency": row["currency"],
                    "openedAt": int(row["opened_at"].timestamp() * 1000) if row["opened_at"] else None,
                }
                for row in positions
            },
            "trades": [
                {
                    "id": row["id"],
                    "orderId": row["order_id"],
                    "time": int(row["executed_at"].timestamp() * 1000),
                    "symbol": row["symbol"],
                    "side": row["side"],
                    "qty": row["qty"],
                    "price": row["price"],
                    "value": row["value"],
                    "currency": row["currency"],
                    "accountBalanceAfter": row["account_balance_after"],
                    "positionQtyAfter": row["position_qty_after"],
                    "realizedPnl": row["realized_pnl"],
                    "equityBefore": row["equity_before"],
                    "equityAfter": row["equity_after"],
                    "equityChange": row["equity_change"],
                }
                for row in trades
            ],
            "deposits": [
                {
                    "id": row["id"],
                    "time": int(row["created_at"].timestamp() * 1000),
                    "amount": row["amount"],
                    "currency": row["currency"],
                }
                for row in deposits
            ],
            "equityHistory": [
                {
                    "time": int(row["created_at"].timestamp() * 1000),
                    "equity": row["equity"],
                    "cash": row["cash_balance"],
                    "positionsValue": row["positions_value"],
                    "returnRate": ((row["equity"] - return_base) / return_base * Decimal("100")) if return_base > 0 else Decimal("0"),
                    "reason": row["reason"],
                }
                for row in equity_history
            ],
            "dailySnapshots": [
                {
                    "date": row["snapshot_date"].isoformat(),
                    "equity": row["equity"],
                    "cash": row["cash_balance"],
                    "positionsValue": row["positions_value"],
                    "totalPnl": row["total_pnl"],
                    "returnRate": row["return_rate"],
                }
                for row in daily_snapshots
            ],
            "signalHistory": [
                {
                    "id": row["id"],
                    "symbol": row["symbol"],
                    "strategyName": row["strategy_name"],
                    "signal": row["signal"],
                    "reason": row["reason"],
                    "price": row["price"],
                    "time": int(row["created_at"].timestamp() * 1000),
                }
                for row in signal_history
            ],
            "backtestHistory": [
                {
                    "id": row["id"],
                    "strategy": row["strategy"],
                    "symbol": row["symbol"],
                    "startDate": int(row["start_date"].timestamp() * 1000),
                    "endDate": int(row["end_date"].timestamp() * 1000),
                    "returnPct": row["return_pct"],
                    "annualReturnPct": row["annual_return_pct"],
                    "maxDrawdown": row["max_drawdown"],
                    "sharpeRatio": row["sharpe_ratio"],
                    "winRate": row["win_rate"],
                    "tradeCount": row["trade_count"],
                    "avgProfit": row["avg_profit"],
                    "avgLoss": row["avg_loss"],
                    "runtimeMs": row["runtime_ms"],
                    "params": row["params"],
                    "time": int(row["created_at"].timestamp() * 1000),
                }
                for row in backtest_history
            ],
            "stats": {
                "totalTrades": int(stats["total_trades"] or 0),
                "winRate": (winning_sells / sell_count * 100) if sell_count else 0,
                "maxProfitTrade": stats["max_profit_trade"] or 0,
                "maxLossTrade": stats["max_loss_trade"] or 0,
                "totalBuyValue": stats["total_buy_value"] or 0,
                "totalSellValue": stats["total_sell_value"] or 0,
            },
        }

    def handle_state(self, parsed):
        user = self.require_user()
        if not user:
            return
        self.end_json(200, {"user": dict(user), "state": self.load_state(user["id"])})

    def handle_strategy_signals(self, parsed):
        user = self.require_user()
        if not user:
            return
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, symbol, strategy_name, signal, reason, price, created_at
                    FROM signal_history
                    WHERE user_id = %s
                    ORDER BY created_at DESC
                    LIMIT 100
                    """,
                    (user["id"],),
                )
                rows = cur.fetchall()
        self.end_json(
            200,
            {
                "signals": [
                    {
                        "id": row["id"],
                        "symbol": row["symbol"],
                        "strategyName": row["strategy_name"],
                        "signal": row["signal"],
                        "reason": row["reason"],
                        "price": row["price"],
                        "time": int(row["created_at"].timestamp() * 1000),
                    }
                    for row in rows
                ],
                "strategies": strategy_engine.available_strategies(),
            },
        )

    def handle_strategy_backtests(self, parsed):
        user = self.require_user()
        if not user:
            return
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM (
                        SELECT
                            id,
                            strategy,
                            symbol,
                            params,
                            start_time AS start_date,
                            end_time AS end_date,
                            return_pct,
                            annual_return_pct,
                            max_drawdown,
                            sharpe_ratio,
                            win_rate,
                            trade_count,
                            avg_profit,
                            avg_loss,
                            runtime_ms,
                            created_at
                        FROM backtest_results
                        WHERE user_id = %s
                        UNION ALL
                        SELECT
                            id,
                            strategy,
                            symbol,
                            '{}'::jsonb AS params,
                            start_date,
                            end_date,
                            return_pct,
                            0 AS annual_return_pct,
                            max_drawdown,
                            0 AS sharpe_ratio,
                            win_rate,
                            trade_count,
                            0 AS avg_profit,
                            0 AS avg_loss,
                            0 AS runtime_ms,
                            created_at
                        FROM backtest_history
                        WHERE user_id = %s
                    ) combined_backtests
                    ORDER BY created_at DESC
                    LIMIT 100
                    """,
                    (user["id"], user["id"]),
                )
                rows = cur.fetchall()
        self.end_json(
            200,
            {
                "backtests": [
                    {
                        "id": row["id"],
                        "strategy": row["strategy"],
                        "symbol": row["symbol"],
                        "startDate": int(row["start_date"].timestamp() * 1000),
                        "endDate": int(row["end_date"].timestamp() * 1000),
                        "returnPct": row["return_pct"],
                        "annualReturnPct": row["annual_return_pct"],
                        "maxDrawdown": row["max_drawdown"],
                        "sharpeRatio": row["sharpe_ratio"],
                        "winRate": row["win_rate"],
                        "tradeCount": row["trade_count"],
                        "avgProfit": row["avg_profit"],
                        "avgLoss": row["avg_loss"],
                        "runtimeMs": row["runtime_ms"],
                        "params": row["params"],
                        "time": int(row["created_at"].timestamp() * 1000),
                    }
                    for row in rows
                ],
            },
        )

    def handle_deposit(self, parsed):
        user = self.require_user()
        if not user:
            return
        amount = Decimal(str(self.read_json().get("amount") or "0"))
        if amount <= 0:
            self.end_json(400, {"error": "Deposit amount must be positive."})
            return
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE accounts SET cash_balance = cash_balance + %s, updated_at = now() WHERE user_id = %s", (amount, user["id"]))
                cur.execute(
                    "INSERT INTO account_transactions (user_id, type, amount, currency) VALUES (%s, 'deposit', %s, 'USD')",
                    (user["id"], amount),
                )
                record_equity_snapshot(conn, user["id"], "deposit")
            conn.commit()
        self.end_json(200, {"state": self.load_state(user["id"])})

    def handle_reset_account(self, parsed):
        user = self.require_user()
        if not user:
            return
        data = self.read_json()
        starting_cash = Decimal(str(data.get("startingCash") or DEFAULT_CASH))
        if starting_cash < 100:
            self.end_json(400, {"error": "Starting cash must be at least 100."})
            return
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE accounts SET cash_balance = %s, starting_cash = %s, updated_at = now() WHERE user_id = %s", (starting_cash, starting_cash, user["id"]))
                cur.execute("DELETE FROM positions WHERE user_id = %s", (user["id"],))
                cur.execute("DELETE FROM trades WHERE user_id = %s", (user["id"],))
                cur.execute("DELETE FROM orders WHERE user_id = %s", (user["id"],))
                cur.execute("DELETE FROM account_transactions WHERE user_id = %s", (user["id"],))
                cur.execute("DELETE FROM equity_history WHERE user_id = %s", (user["id"],))
                cur.execute("DELETE FROM daily_snapshots WHERE user_id = %s", (user["id"],))
                record_equity_snapshot(conn, user["id"], "reset")
            conn.commit()
        self.end_json(200, {"state": self.load_state(user["id"])})

    def handle_active_symbol(self, parsed):
        user = self.require_user()
        if not user:
            return
        symbol = normalize_market_symbol((self.read_json().get("symbol") or "").strip())
        if not symbol:
            self.end_json(400, {"error": "Missing symbol."})
            return
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE accounts SET active_symbol = %s, updated_at = now() WHERE user_id = %s", (symbol, user["id"]))
            conn.commit()
        self.end_json(200, {"ok": True})

    def handle_add_watchlist(self, parsed):
        user = self.require_user()
        if not user:
            return
        symbol = normalize_market_symbol((self.read_json().get("symbol") or "").strip())
        if not symbol:
            self.end_json(400, {"error": "Missing symbol."})
            return
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 AS next_order FROM watchlist WHERE user_id = %s", (user["id"],))
                sort_order = cur.fetchone()["next_order"]
                cur.execute(
                    "INSERT INTO watchlist (user_id, symbol, sort_order) VALUES (%s, %s, %s) ON CONFLICT (user_id, symbol) DO NOTHING",
                    (user["id"], symbol, sort_order),
                )
                cur.execute("UPDATE accounts SET active_symbol = %s, updated_at = now() WHERE user_id = %s", (symbol, user["id"]))
            conn.commit()
        self.end_json(200, {"state": self.load_state(user["id"])})

    def handle_delete_watchlist(self, parsed):
        user = self.require_user()
        if not user:
            return
        query = parse_qs(parsed.query)
        symbol = normalize_market_symbol((query.get("symbol") or [""])[0])
        if not symbol:
            self.end_json(400, {"error": "Missing symbol."})
            return
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM watchlist WHERE user_id = %s AND symbol = %s", (user["id"], symbol))
                cur.execute("SELECT symbol FROM watchlist WHERE user_id = %s ORDER BY sort_order, symbol LIMIT 1", (user["id"],))
                next_symbol = cur.fetchone()
                if not next_symbol:
                    cur.execute(
                        "INSERT INTO watchlist (user_id, symbol, sort_order) VALUES (%s, 'AAPL', 0) ON CONFLICT (user_id, symbol) DO NOTHING",
                        (user["id"],),
                    )
                    next_symbol = {"symbol": "AAPL"}
                cur.execute("UPDATE accounts SET active_symbol = %s, updated_at = now() WHERE user_id = %s", (next_symbol["symbol"], user["id"]))
            conn.commit()
        self.end_json(200, {"state": self.load_state(user["id"])})

    def handle_run_strategy(self, parsed):
        user = self.require_user()
        if not user:
            return
        data = self.read_json()
        symbol = normalize_market_symbol(data.get("symbol") or "")
        strategy_name = (data.get("strategyName") or "moving_average").strip()
        params = clean_strategy_params(strategy_name, data.get("params") or {})
        if not symbol:
            self.end_json(400, {"error": "Missing symbol."})
            return
        try:
            quote_data = normalize_quote(symbol)
            try:
                history_data = normalize_history(symbol, "1mo")
            except Exception:
                history_data = {"points": []}
            market_data = {
                "symbol": symbol,
                "price": quote_data["price"],
                "quote": quote_data,
                "history": history_data.get("points") or [],
                "params": params,
            }
            signal = strategy_engine.generate_signal(strategy_name, symbol, market_data)
        except (HTTPError, URLError, TimeoutError, ValueError) as error:
            self.end_json(502, {"error": f"Strategy signal failed: {error}"})
            return

        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO signal_history (user_id, symbol, strategy_name, signal, reason, price)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id, created_at
                    """,
                    (
                        user["id"],
                        symbol,
                        strategy_name,
                        signal["signal"],
                        signal["reason"],
                        Decimal(str(signal["price"])),
                    ),
                )
                row = cur.fetchone()
            conn.commit()

        signal_payload = {
            "id": row["id"],
            "symbol": symbol,
            "strategyName": strategy_name,
            "signal": signal["signal"],
            "reason": signal["reason"],
            "price": signal["price"],
            "time": int(row["created_at"].timestamp() * 1000),
            "params": params,
        }
        self.end_json(200, {"signal": signal_payload, "state": self.load_state(user["id"])})

    def handle_strategy_backtest(self, parsed):
        user = self.require_user()
        if not user:
            return
        data = self.read_json()
        symbol = normalize_market_symbol(data.get("symbol") or "")
        strategy_name = (data.get("strategyName") or "moving_average").strip()
        range_key = data.get("range") or "1y"
        start_date = parse_date_picker(data.get("startDate"))
        end_date = parse_date_picker(data.get("endDate"))
        params = clean_strategy_params(strategy_name, data.get("params") or {})
        if not symbol:
            self.end_json(400, {"error": "Missing symbol."})
            return
        if range_key not in BACKTEST_RANGES:
            self.end_json(400, {"error": "Invalid backtest range."})
            return
        started = time.perf_counter()
        try:
            points = normalize_backtest_history(symbol, range_key, start_date, end_date)
            result = run_strategy_backtest(strategy_name, symbol, points, params)
        except (HTTPError, URLError, TimeoutError, ValueError) as error:
            self.end_json(502, {"error": f"Backtest failed: {error}"})
            return
        runtime_ms = int((time.perf_counter() - started) * 1000)

        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO backtest_results
                        (
                            user_id,
                            symbol,
                            strategy,
                            params,
                            start_time,
                            end_time,
                            return_pct,
                            annual_return_pct,
                            max_drawdown,
                            sharpe_ratio,
                            win_rate,
                            trade_count,
                            avg_profit,
                            avg_loss,
                            runtime_ms
                        )
                    VALUES (%s, %s, %s, %s::jsonb, to_timestamp(%s), to_timestamp(%s), %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id, created_at
                    """,
                    (
                        user["id"],
                        symbol,
                        strategy_name,
                        json.dumps(params),
                        result["startDate"] / 1000,
                        result["endDate"] / 1000,
                        Decimal(str(result["returnPct"])),
                        Decimal(str(result["annualReturnPct"])),
                        Decimal(str(result["maxDrawdown"])),
                        Decimal(str(result["sharpeRatio"])),
                        Decimal(str(result["winRate"])),
                        int(result["tradeCount"]),
                        Decimal(str(result["avgProfit"])),
                        Decimal(str(result["avgLoss"])),
                        runtime_ms,
                    ),
                )
                row = cur.fetchone()
            conn.commit()
        result["id"] = row["id"]
        result["createdAt"] = int(row["created_at"].timestamp() * 1000)
        result["runtimeMs"] = runtime_ms
        self.end_json(200, {"backtest": result, "state": self.load_state(user["id"])})

    def handle_strategy_optimize(self, parsed):
        user = self.require_user()
        if not user:
            return
        data = self.read_json()
        symbol = normalize_market_symbol(data.get("symbol") or "")
        strategy_name = (data.get("strategyName") or "moving_average").strip()
        range_key = data.get("range") or "1y"
        start_date = parse_date_picker(data.get("startDate"))
        end_date = parse_date_picker(data.get("endDate"))
        base_params = clean_strategy_params(strategy_name, data.get("params") or {})
        if not symbol:
            self.end_json(400, {"error": "Missing symbol."})
            return
        if range_key not in BACKTEST_RANGES:
            self.end_json(400, {"error": "Invalid backtest range."})
            return
        started = time.perf_counter()
        try:
            points = normalize_backtest_history(symbol, range_key, start_date, end_date)
            results = [
                summarize_backtest_result(run_strategy_backtest(strategy_name, symbol, points, params))
                for params in optimizer_grid(strategy_name, base_params)
            ]
        except (HTTPError, URLError, TimeoutError, ValueError) as error:
            self.end_json(502, {"error": f"Optimization failed: {error}"})
            return
        runtime_ms = int((time.perf_counter() - started) * 1000)
        results.sort(key=lambda item: item["returnPct"], reverse=True)
        best_return = results[0] if results else None
        best_drawdown = min(results, key=lambda item: item["maxDrawdown"]) if results else None
        best_sharpe = max(results, key=lambda item: item["sharpeRatio"]) if results else None
        self.end_json(
            200,
            {
                "optimization": {
                    "strategy": strategy_name,
                    "symbol": symbol,
                    "range": range_key,
                    "combinations": len(results),
                    "runtimeMs": runtime_ms,
                    "bestReturn": best_return,
                    "bestDrawdown": best_drawdown,
                    "bestSharpe": best_sharpe,
                    "topResults": results[:20],
                }
            },
        )

    def handle_trade(self, parsed):
        user = self.require_user()
        if not user:
            return
        data = self.read_json()
        symbol = normalize_market_symbol(data.get("symbol") or "")
        side = data.get("side")
        qty = Decimal(str(data.get("qty") or "0"))
        if side not in {"buy", "sell"} or not symbol or qty <= 0:
            self.end_json(400, {"error": "Invalid trade."})
            return
        quote_data = normalize_quote(symbol)
        price = Decimal(str(quote_data["price"]))
        value = qty * price
        currency = quote_data["currency"]

        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT cash_balance FROM accounts WHERE user_id = %s FOR UPDATE", (user["id"],))
                account = cur.fetchone()
                cur.execute("SELECT qty, avg_price FROM positions WHERE user_id = %s AND symbol = %s FOR UPDATE", (user["id"], symbol))
                position = cur.fetchone()
                current_qty = position["qty"] if position else Decimal("0")
                avg_price = position["avg_price"] if position else Decimal("0")
                equity_before = account["cash_balance"] + estimate_positions_value(conn, user["id"])
                realized_pnl = None

                if side == "buy":
                    if account["cash_balance"] < value:
                        self.end_json(400, {"error": "Insufficient cash."})
                        conn.rollback()
                        return
                    new_qty = current_qty + qty
                    new_avg = ((current_qty * avg_price) + value) / new_qty
                    cur.execute("UPDATE accounts SET cash_balance = cash_balance - %s, updated_at = now() WHERE user_id = %s", (value, user["id"]))
                    account_balance_after = account["cash_balance"] - value
                    position_qty_after = new_qty
                    cur.execute(
                        """
                        INSERT INTO positions (user_id, symbol, qty, avg_price, currency)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (user_id, symbol)
                        DO UPDATE SET qty = EXCLUDED.qty, avg_price = EXCLUDED.avg_price, currency = EXCLUDED.currency, updated_at = now()
                        """,
                        (user["id"], symbol, new_qty, new_avg, currency),
                    )
                else:
                    if current_qty < qty:
                        self.end_json(400, {"error": "Insufficient shares."})
                        conn.rollback()
                        return
                    new_qty = current_qty - qty
                    realized_pnl = (price - avg_price) * qty
                    cur.execute("UPDATE accounts SET cash_balance = cash_balance + %s, updated_at = now() WHERE user_id = %s", (value, user["id"]))
                    account_balance_after = account["cash_balance"] + value
                    position_qty_after = new_qty
                    if new_qty <= 0:
                        cur.execute("DELETE FROM positions WHERE user_id = %s AND symbol = %s", (user["id"], symbol))
                    else:
                        cur.execute("UPDATE positions SET qty = %s, updated_at = now() WHERE user_id = %s AND symbol = %s", (new_qty, user["id"], symbol))

                cur.execute(
                    """
                    INSERT INTO orders (user_id, symbol, side, order_type, status, qty, filled_qty, price, value, currency)
                    VALUES (%s, %s, %s, 'market', 'filled', %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (user["id"], symbol, side, qty, qty, price, value, currency),
                )
                order_id = cur.fetchone()["id"]
                cur.execute(
                    """
                    INSERT INTO trades (user_id, order_id, symbol, side, qty, price, value, currency)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (user["id"], order_id, symbol, side, qty, price, value, currency),
                )
                trade_id = cur.fetchone()["id"]
                equity_after = record_equity_snapshot(conn, user["id"], "trade", trade_id)
                cur.execute(
                    """
                    UPDATE trades
                    SET account_balance_after = %s,
                        position_qty_after = %s,
                        realized_pnl = %s,
                        equity_before = %s,
                        equity_after = %s,
                        equity_change = %s
                    WHERE id = %s
                    """,
                    (
                        account_balance_after,
                        position_qty_after,
                        realized_pnl,
                        equity_before,
                        equity_after,
                        equity_after - equity_before,
                        trade_id,
                    ),
                )
            conn.commit()
        self.end_json(200, {"state": self.load_state(user["id"]), "fill": {"symbol": symbol, "side": side, "qty": qty, "price": price, "value": value, "currency": currency}})

    def handle_clear_history(self, parsed):
        user = self.require_user()
        if not user:
            return
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM trades WHERE user_id = %s", (user["id"],))
                cur.execute("DELETE FROM orders WHERE user_id = %s", (user["id"],))
                cur.execute("DELETE FROM account_transactions WHERE user_id = %s", (user["id"],))
            conn.commit()
        self.end_json(200, {"state": self.load_state(user["id"])})

    def handle_search(self, parsed):
        query = (parse_qs(parsed.query).get("q") or [""])[0].strip()
        if not query:
            self.end_json(400, {"error": "Missing search query"})
            return
        try:
            data = fetch_json(YAHOO_SEARCH.format(query=quote(query)))
            quotes = []
            for item in data.get("quotes", []):
                symbol = item.get("symbol")
                quote_type = item.get("quoteType")
                if not symbol or quote_type not in {"EQUITY", "ETF", "INDEX"}:
                    continue
                quotes.append({"symbol": symbol, "name": item.get("longname") or item.get("shortname") or symbol, "exchange": item.get("exchDisp") or item.get("exchange") or "", "type": quote_type})
            for symbol in candidate_symbols(query):
                if any(item["symbol"].upper() == symbol.upper() for item in quotes):
                    continue
                try:
                    item = normalize_quote(symbol)
                    quotes.insert(0, {"symbol": item["symbol"], "name": item["name"], "exchange": item["exchange"], "type": "EQUITY"})
                except (HTTPError, URLError, TimeoutError, ValueError):
                    pass
            self.end_json(200, {"results": quotes})
        except (HTTPError, URLError, TimeoutError, ValueError) as error:
            self.end_json(502, {"error": f"Search failed: {error}"})

    def handle_quote(self, parsed):
        raw = (parse_qs(parsed.query).get("symbols") or [""])[0]
        symbols = [normalize_market_symbol(item) for item in raw.split(",") if item.strip()]
        if not symbols:
            self.end_json(400, {"error": "Missing symbols"})
            return
        quotes = []
        errors = {}
        with ThreadPoolExecutor(max_workers=min(8, len(symbols[:30]))) as executor:
            tasks = {executor.submit(normalize_quote, symbol): symbol for symbol in symbols[:30]}
            for task in as_completed(tasks):
                symbol = tasks[task]
                try:
                    quotes.append(task.result())
                except (HTTPError, URLError, TimeoutError, ValueError) as error:
                    errors[symbol] = str(error)
        self.end_json(200, {"quotes": quotes, "errors": errors, "serverTime": int(time.time() * 1000)})

    def handle_history(self, parsed):
        query = parse_qs(parsed.query)
        symbol = normalize_market_symbol((query.get("symbol") or [""])[0])
        range_key = (query.get("range") or ["1d"])[0].strip()
        if not symbol:
            self.end_json(400, {"error": "Missing symbol"})
            return
        try:
            self.end_json(200, normalize_history(symbol, range_key))
        except (HTTPError, URLError, TimeoutError, ValueError) as error:
            self.end_json(502, {"error": f"History failed: {error}"})


if __name__ == "__main__":
    init_db()
    start_equity_snapshot_worker()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Real-market paper trading server: http://{HOST}:{PORT}/")
    server.serve_forever()
