from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse, quote
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from concurrent.futures import ThreadPoolExecutor, as_completed
from http import cookies
from decimal import Decimal
import base64
import hashlib
import hmac
import json
import os
import secrets
import time

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

YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={range}&interval={interval}"
YAHOO_SEARCH = "https://query1.finance.yahoo.com/v1/finance/search?q={query}&quotesCount=8&newsCount=0"
HISTORY_RANGES = {
    "1d": ("1d", "1m"),
    "1wk": ("5d", "5m"),
    "1mo": ("1mo", "30m"),
    "3mo": ("3mo", "1d"),
    "6mo": ("6mo", "1d"),
    "1y": ("1y", "1d"),
}


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
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    with open(schema_path, "r", encoding="utf-8") as schema_file:
        schema = schema_file.read()
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


def record_equity_snapshot(conn, user_id, reason, related_trade_id=None):
    with conn.cursor() as cur:
        cur.execute("SELECT cash_balance FROM accounts WHERE user_id = %s", (user_id,))
        account = cur.fetchone()
    equity = account["cash_balance"] + estimate_positions_value(conn, user_id)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO equity_history (user_id, equity, cash_balance, positions_value, reason, related_trade_id)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (user_id, equity, account["cash_balance"], equity - account["cash_balance"], reason, related_trade_id),
        )
    return equity


class Handler(SimpleHTTPRequestHandler):
    GET_ROUTES = {
        "/api/search": "handle_search",
        "/api/quote": "handle_quote",
        "/api/history": "handle_history",
        "/api/health": "handle_health",
        "/api/me": "handle_me",
        "/api/state": "handle_state",
    }
    POST_ROUTES = {
        "/api/auth/register": "handle_register",
        "/api/auth/login": "handle_login",
        "/api/auth/logout": "handle_logout",
        "/api/account/deposit": "handle_deposit",
        "/api/account/reset": "handle_reset_account",
        "/api/account/active-symbol": "handle_active_symbol",
        "/api/watchlist": "handle_add_watchlist",
        "/api/trade": "handle_trade",
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
                cur.execute("SELECT symbol, qty, avg_price, currency FROM positions WHERE user_id = %s AND qty > 0 ORDER BY symbol", (user_id,))
                positions = cur.fetchall()
                cur.execute(
                    """
                    SELECT id, order_id, symbol, side, qty, price, value, currency,
                           account_balance_after, position_qty_after, realized_pnl, executed_at
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
                    SELECT equity, cash_balance, positions_value, reason, created_at
                    FROM equity_history
                    WHERE user_id = %s
                    ORDER BY created_at ASC
                    LIMIT 1000
                    """,
                    (user_id,),
                )
                equity_history = cur.fetchall()
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
        return {
            "startingCash": account["starting_cash"],
            "cash": account["cash_balance"],
            "baseCurrency": account["base_currency"],
            "activeSymbol": account["active_symbol"],
            "symbols": symbols or DEFAULT_SYMBOLS,
            "positions": {
                row["symbol"]: {"qty": row["qty"], "avgPrice": row["avg_price"], "currency": row["currency"]}
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
                    "reason": row["reason"],
                }
                for row in equity_history
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
                cur.execute(
                    """
                    UPDATE trades
                    SET account_balance_after = %s,
                        position_qty_after = %s,
                        realized_pnl = %s
                    WHERE id = %s
                    """,
                    (account_balance_after, position_qty_after, realized_pnl, trade_id),
                )
                record_equity_snapshot(conn, user["id"], "trade", trade_id)
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
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Real-market paper trading server: http://{HOST}:{PORT}/")
    server.serve_forever()
