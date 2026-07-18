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
CRYPTO_SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]
PROJECT_DIR = Path(__file__).resolve().parent
AUTO_TRADING_INTERVAL_SECONDS = 300
AUTO_TRADING_SCAN_LIMIT = 60
AUTO_SCHEDULER_LOCK = threading.Lock()
DB_INITIALIZED = False
DB_INIT_ERROR = ""

YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={range}&interval={interval}"
YAHOO_CHART_PERIOD = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?period1={period1}&period2={period2}&interval={interval}"
YAHOO_SEARCH = "https://query1.finance.yahoo.com/v1/finance/search?q={query}&quotesCount=8&newsCount=0"
BINANCE_BASE_URLS = [
    "https://data-api.binance.vision",
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
]
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
SCANNER_UNIVERSES = {
    "us": ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "META", "GOOGL", "AMD", "NFLX", "AVGO", "JPM", "COST"],
    "a": ["600519.SS", "300750.SZ", "000001.SZ", "601318.SS", "000858.SZ", "600036.SS"],
    "hk": ["0700.HK", "9988.HK", "3690.HK", "1299.HK", "1810.HK", "9618.HK"],
    "crypto": CRYPTO_SYMBOLS,
}
QUALITY_FILTER_PRESETS = {
    "strict": {"min_score": 0, "min_sharpe": 0, "min_return": 0, "max_drawdown": 35, "min_trades": 5, "checks": {"score", "sharpe", "return", "drawdown", "trades"}},
    "normal": {"min_score": 0, "min_sharpe": -0.5, "min_return": -10, "max_drawdown": 50, "min_trades": 3, "checks": {"score", "sharpe", "return", "drawdown", "trades"}},
    "loose": {"min_score": 60, "min_sharpe": -10, "min_return": -100, "max_drawdown": 100, "min_trades": 0, "checks": {"score"}},
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
    if normalized.endswith(".SH"):
        return f"{normalized[:-3]}.SS"
    if normalized.endswith(".HK"):
        code = normalized[:-3]
        if code.isdigit() and len(code) == 5:
            return f"{code[-4:]}.HK"
    return normalized


def is_crypto_symbol(symbol):
    normalized = symbol.strip().upper()
    return normalized in CRYPTO_SYMBOLS or (normalized.endswith("USDT") and "." not in normalized and len(normalized) >= 7)


def yahoo_chart(symbol, range_value="1d", interval="1m"):
    data = fetch_json(YAHOO_CHART.format(symbol=quote(symbol), range=range_value, interval=interval))
    result = data.get("chart", {}).get("result") or []
    if not result:
        error = data.get("chart", {}).get("error") or {}
        raise ValueError(error.get("description") or "No quote data returned")
    return result[0]


def binance_klines(symbol, interval, limit=500, start_time=None, end_time=None):
    path = f"/api/v3/klines?symbol={quote(symbol)}&interval={interval}&limit={limit}"
    if start_time is not None and end_time is not None:
        path = f"{path}&startTime={start_time}&endTime={end_time}"
    return fetch_binance_json(path)


def fetch_binance_json(path):
    last_error = None
    for base_url in BINANCE_BASE_URLS:
        try:
            return fetch_json(f"{base_url}{path}")
        except (HTTPError, URLError, TimeoutError, ValueError) as error:
            last_error = error
    raise ValueError(f"Binance request failed: {last_error}")


def normalize_binance_quote(symbol):
    data = fetch_binance_json(f"/api/v3/ticker/24hr?symbol={quote(symbol)}")
    price = float(data["lastPrice"])
    previous_close = float(data.get("prevClosePrice") or data.get("openPrice") or price)
    base = symbol[:-4] if symbol.endswith("USDT") else symbol
    return {
        "symbol": symbol,
        "name": f"{base}/USDT",
        "exchange": "Binance",
        "currency": "USD",
        "price": price,
        "previousClose": previous_close,
        "regularMarketTime": int(time.time()),
        "marketState": "24/7",
    }


def normalize_binance_points(rows):
    points = []
    for row in rows:
        open_time, open_price, high, low, close, volume = row[:6]
        points.append(
            {
                "t": int(open_time),
                "p": float(close),
                "o": float(open_price),
                "h": float(high),
                "l": float(low),
                "c": float(close),
                "v": float(volume),
            }
        )
    return points


def crypto_history_window(range_key):
    now = int(time.time() * 1000)
    day = 24 * 60 * 60 * 1000
    days = {"1d": 1, "1wk": 7, "1mo": 30, "3mo": 90, "6mo": 180, "1y": 365, "3y": 365 * 3, "5y": 365 * 5, "10y": 365 * 10}
    if range_key == "max":
        return int(datetime(2017, 1, 1, tzinfo=timezone.utc).timestamp() * 1000), now
    return now - days.get(range_key, 365) * day, now


def normalize_binance_history(symbol, range_key):
    mapping = {
        "1d": ("5m", 288),
        "1wk": ("1h", 168),
        "1mo": ("4h", 180),
        "3mo": ("1d", 90),
        "6mo": ("1d", 180),
        "1y": ("1d", 365),
    }
    interval, limit = mapping.get(range_key, ("1d", 365))
    return normalize_binance_points(binance_klines(symbol, interval, limit))


def normalize_binance_backtest_history(symbol, range_key, start_date=None, end_date=None):
    if start_date and end_date:
        start_ms = int(start_date.replace(tzinfo=timezone.utc).timestamp() * 1000)
        end_ms = int((end_date + timedelta(days=1)).replace(tzinfo=timezone.utc).timestamp() * 1000)
    else:
        start_ms, end_ms = crypto_history_window(range_key)
    points = []
    cursor = start_ms
    while cursor < end_ms:
        rows = binance_klines(symbol, "1d", 1000, cursor, end_ms)
        if not rows:
            break
        chunk = normalize_binance_points(rows)
        points.extend(chunk)
        next_cursor = int(rows[-1][0]) + 24 * 60 * 60 * 1000
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        if len(rows) < 1000:
            break
    if len(points) < 2:
        raise ValueError("No crypto historical prices returned")
    return points


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
    symbol = normalize_market_symbol(symbol)
    if is_crypto_symbol(symbol):
        return normalize_binance_quote(symbol)
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
    symbol = normalize_market_symbol(symbol)
    if is_crypto_symbol(symbol):
        points = normalize_binance_history(symbol, range_key)
        if not points:
            raise ValueError("No crypto historical prices returned")
        return {"symbol": symbol.upper(), "range": range_key, "points": [{"t": point["t"], "p": point["p"]} for point in points[-360:]]}
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
    symbol = normalize_market_symbol(symbol)
    if is_crypto_symbol(symbol):
        return normalize_binance_backtest_history(symbol, range_key, start_date, end_date)
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
    opens = quote_data.get("open") or []
    highs = quote_data.get("high") or []
    lows = quote_data.get("low") or []
    closes = quote_data.get("close") or []
    volumes = quote_data.get("volume") or []
    points = []
    for index, (ts, close) in enumerate(zip(timestamps, closes)):
        if close is None:
            continue
        close_value = float(close)
        open_value = float(opens[index]) if index < len(opens) and opens[index] is not None else close_value
        high_value = float(highs[index]) if index < len(highs) and highs[index] is not None else max(open_value, close_value)
        low_value = float(lows[index]) if index < len(lows) and lows[index] is not None else min(open_value, close_value)
        volume_value = float(volumes[index]) if index < len(volumes) and volumes[index] is not None else 0
        points.append({"t": ts * 1000, "p": close_value, "o": open_value, "h": high_value, "l": low_value, "c": close_value, "v": volume_value})
    if len(points) < 2:
        raise ValueError("No historical prices returned")
    return points


def normalize_signal_history(symbol, timeframe="1h"):
    symbol = normalize_market_symbol(symbol)
    timeframe = timeframe if timeframe in {"1d", "1h", "15m"} else "1h"
    if timeframe == "1d":
        return normalize_backtest_history(symbol, "1y")[-260:]
    if is_crypto_symbol(symbol):
        interval, limit = ("15m", 500) if timeframe == "15m" else ("1h", 500)
        return normalize_binance_points(binance_klines(symbol, interval, limit))
    range_value, interval = ("1mo", "15m") if timeframe == "15m" else ("3mo", "1h")
    chart = yahoo_chart(symbol, range_value, interval)
    timestamps = chart.get("timestamp") or []
    quote_data = ((chart.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quote_data.get("close") or []
    volumes = quote_data.get("volume") or []
    points = []
    for index, (ts, close) in enumerate(zip(timestamps, closes)):
        if close is None:
            continue
        points.append({"t": ts * 1000, "p": float(close), "c": float(close), "v": float(volumes[index] or 0) if index < len(volumes) else 0})
    if len(points) < 30:
        raise ValueError(f"Not enough {timeframe} history for scanning")
    return points[-500:]


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


def clean_user_strategy_settings(data):
    data = data or {}

    def as_int(name, default, minimum=1, maximum=400):
        try:
            value = int(data.get(name, default))
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(maximum, value))

    def as_float(name, default, minimum=0, maximum=100):
        try:
            value = float(data.get(name, default))
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(maximum, value))

    settings = {
        "maFast": as_int("maFast", 5),
        "maSlow": as_int("maSlow", 20),
        "macdFast": as_int("macdFast", 12),
        "macdSlow": as_int("macdSlow", 26),
        "macdSignal": as_int("macdSignal", 9),
        "rsiPeriod": as_int("rsiPeriod", 14, 2, 400),
        "rsiBuyThreshold": as_float("rsiBuyThreshold", 30),
        "rsiSellThreshold": as_float("rsiSellThreshold", 70),
    }
    if settings["maFast"] >= settings["maSlow"]:
        settings["maSlow"] = settings["maFast"] + 1
    if settings["macdFast"] >= settings["macdSlow"]:
        settings["macdSlow"] = settings["macdFast"] + 1
    if settings["rsiBuyThreshold"] >= settings["rsiSellThreshold"]:
        settings["rsiSellThreshold"] = min(100, settings["rsiBuyThreshold"] + 1)
    return settings


def ensure_strategy_settings(conn, user_id):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO strategy_settings (user_id)
            VALUES (%s)
            ON CONFLICT (user_id) DO NOTHING
            """,
            (user_id,),
        )


def strategy_settings_payload(row):
    return {
        "maFast": row["ma_fast"],
        "maSlow": row["ma_slow"],
        "macdFast": row["macd_fast"],
        "macdSlow": row["macd_slow"],
        "macdSignal": row["macd_signal"],
        "rsiPeriod": row["rsi_period"],
        "rsiBuyThreshold": row["rsi_buy_threshold"],
        "rsiSellThreshold": row["rsi_sell_threshold"],
    }


def load_strategy_settings(conn, user_id):
    ensure_strategy_settings(conn, user_id)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ma_fast, ma_slow, macd_fast, macd_slow, macd_signal,
                   rsi_period, rsi_buy_threshold, rsi_sell_threshold
            FROM strategy_settings
            WHERE user_id = %s
            """,
            (user_id,),
        )
        return strategy_settings_payload(cur.fetchone())


def params_for_strategy(strategy_name, user_settings=None):
    settings = clean_user_strategy_settings(user_settings or {})
    if strategy_name == "moving_average":
        return clean_strategy_params("moving_average", {"fastMa": settings["maFast"], "slowMa": settings["maSlow"]})
    if strategy_name == "rsi":
        return clean_strategy_params(
            "rsi",
            {
                "period": settings["rsiPeriod"],
                "oversold": settings["rsiBuyThreshold"],
                "overbought": settings["rsiSellThreshold"],
            },
        )
    if strategy_name == "macd":
        return clean_strategy_params(
            "macd",
            {"fast": settings["macdFast"], "slow": settings["macdSlow"], "signal": settings["macdSignal"]},
        )
    return clean_strategy_params(strategy_name, {})


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


def calculate_sortino_ratio(equity_curve):
    returns = []
    previous = None
    for point in equity_curve:
        equity = float(point["equity"])
        if previous and previous > 0:
            returns.append((equity - previous) / previous)
        previous = equity
    downside = [value for value in returns if value < 0]
    if len(returns) < 2 or not downside:
        return 0
    mean_return = sum(returns) / len(returns)
    downside_deviation = math.sqrt(sum(value**2 for value in downside) / len(downside))
    if downside_deviation == 0:
        return 0
    return (mean_return / downside_deviation) * math.sqrt(252)


def ratio_or_none(numerator, denominator):
    if denominator == 0:
        return None
    return numerator / denominator


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
        "profitFactor": result["profitFactor"],
        "calmarRatio": result["calmarRatio"],
        "sortinoRatio": result["sortinoRatio"],
        "expectancy": result["expectancy"],
    }


def clean_training_ratio(value):
    try:
        ratio = float(value)
    except (TypeError, ValueError):
        ratio = 0.7
    return max(0.5, min(0.9, ratio))


def normalize_equity_curve(curve):
    return [
        {
            "time": point["time"],
            "equity": point["equity"],
            "cash": point.get("cash", 0),
            "positionValue": point.get("positionValue", 0),
        }
        for point in curve
    ]


def run_walk_forward_validation(strategy_name, symbol, points, base_params, training_ratio=0.7):
    ratio = clean_training_ratio(training_ratio)
    if len(points) < 60:
        return {
            "trainingRatio": ratio,
            "testingRatio": 1 - ratio,
            "error": "Need at least 60 historical price points for walk forward validation.",
        }
    split_index = max(30, min(len(points) - 30, int(len(points) * ratio)))
    training_points = points[:split_index]
    testing_points = points[split_index:]
    candidates = optimizer_grid(strategy_name, base_params)
    training_results = [run_strategy_backtest(strategy_name, symbol, training_points, params) for params in candidates]
    best_training = max(training_results, key=lambda item: item["returnPct"])
    testing_result = run_strategy_backtest(strategy_name, symbol, testing_points, best_training["params"])
    training_return = float(best_training["returnPct"])
    testing_return = float(testing_result["returnPct"])
    efficiency = (testing_return / training_return * 100) if training_return > 0 else None
    overfit_warning = training_return > 0 and (testing_return < 0 or testing_return < training_return * 0.5)
    return {
        "trainingRatio": ratio,
        "testingRatio": 1 - ratio,
        "bestParams": best_training["params"],
        "trainingReturn": training_return,
        "testingReturn": testing_return,
        "walkForwardEfficiency": efficiency,
        "overfitWarning": overfit_warning,
        "trainingStartDate": best_training["startDate"],
        "trainingEndDate": best_training["endDate"],
        "testingStartDate": testing_result["startDate"],
        "testingEndDate": testing_result["endDate"],
        "trainingEquityCurve": normalize_equity_curve(best_training["equityCurve"]),
        "testingEquityCurve": normalize_equity_curve(testing_result["equityCurve"]),
    }


def parse_portfolio_symbols(raw_symbols):
    if isinstance(raw_symbols, list):
        candidates = raw_symbols
    else:
        candidates = str(raw_symbols or "").replace(",", "\n").splitlines()
    symbols = []
    seen = set()
    for item in candidates:
        symbol = normalize_market_symbol(str(item).strip())
        if symbol and symbol not in seen:
            symbols.append(symbol)
            seen.add(symbol)
    return symbols[:12]


def parse_portfolio_weights(symbols, raw_weights, mode):
    if mode != "custom":
        equal_weight = 1 / len(symbols)
        return {symbol: equal_weight for symbol in symbols}
    source = {}
    if isinstance(raw_weights, dict):
        source = {normalize_market_symbol(key): value for key, value in raw_weights.items()}
    else:
        for line in str(raw_weights or "").replace(",", "\n").splitlines():
            if ":" in line:
                symbol, value = line.split(":", 1)
            elif "=" in line:
                symbol, value = line.split("=", 1)
            else:
                parts = line.split()
                if len(parts) != 2:
                    continue
                symbol, value = parts
            source[normalize_market_symbol(symbol)] = value
    weights = {}
    for symbol in symbols:
        try:
            weights[symbol] = max(0, float(source.get(symbol, 0)))
        except (TypeError, ValueError):
            weights[symbol] = 0
    total = sum(weights.values())
    if total <= 0:
        equal_weight = 1 / len(symbols)
        return {symbol: equal_weight for symbol in symbols}
    return {symbol: value / total for symbol, value in weights.items()}


def run_portfolio_backtest(symbols, weights, histories):
    price_maps = {}
    for symbol, points in histories.items():
        price_maps[symbol] = {
            datetime.fromtimestamp(point["t"] / 1000, tz=timezone.utc).date().isoformat(): float(point["p"])
            for point in points
            if point.get("p") is not None and float(point["p"]) > 0
        }
    common_dates = sorted(set.intersection(*(set(price_maps[symbol]) for symbol in symbols)))
    if len(common_dates) < 2:
        raise ValueError("Not enough overlapping history for selected assets.")
    first_prices = {symbol: price_maps[symbol][common_dates[0]] for symbol in symbols}
    starting_cash = 100000.0
    equity_curve = []
    for date_key in common_dates:
        equity = starting_cash * sum(weights[symbol] * (price_maps[symbol][date_key] / first_prices[symbol]) for symbol in symbols)
        timestamp = int(datetime.fromisoformat(date_key).replace(tzinfo=timezone.utc).timestamp() * 1000)
        equity_curve.append({"time": timestamp, "equity": equity})
    final_equity = equity_curve[-1]["equity"]
    return {
        "symbols": symbols,
        "weights": weights,
        "startDate": equity_curve[0]["time"],
        "endDate": equity_curve[-1]["time"],
        "initialCash": starting_cash,
        "finalEquity": final_equity,
        "returnPct": (final_equity - starting_cash) / starting_cash * 100,
        "sharpeRatio": calculate_sharpe_ratio(equity_curve),
        "maxDrawdown": calculate_max_drawdown([point["equity"] for point in equity_curve]),
        "equityCurve": equity_curve,
        "allocation": [{"symbol": symbol, "weight": weights[symbol] * 100} for symbol in symbols],
    }


def scanner_strategy_label(strategy_name):
    return {"moving_average": "MA", "rsi": "RSI", "macd": "MACD"}.get(strategy_name, strategy_name)


def market_kind(symbol):
    normalized = normalize_market_symbol(symbol)
    if is_crypto_symbol(normalized):
        return "crypto", "虚拟币"
    if normalized.endswith((".SS", ".SZ")):
        return "a", "A股"
    if normalized.endswith(".HK"):
        return "hk", "港股"
    return "us", "美股"


def market_kind(symbol):
    normalized = normalize_market_symbol(symbol)
    if is_crypto_symbol(normalized):
        return "crypto", "Crypto"
    if normalized.endswith((".SS", ".SZ")):
        return "a", "A-Share"
    if normalized.endswith(".HK"):
        return "hk", "HK"
    return "us", "US"


def default_strategy_params(strategy_name):
    return params_for_strategy(strategy_name)


def simple_ema(values, period):
    if not values:
        return []
    multiplier = 2 / (period + 1)
    result = [values[0]]
    for value in values[1:]:
        result.append((value - result[-1]) * multiplier + result[-1])
    return result


def recent_rsi(closes, period=14):
    if len(closes) <= period:
        return None
    gains = []
    losses = []
    for previous, current in zip(closes[-period - 1:-1], closes[-period:]):
        change = current - previous
        gains.append(max(change, 0))
        losses.append(abs(min(change, 0)))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100
    return 100 - (100 / (1 + (avg_gain / avg_loss)))


def volume_boost(points):
    volumes = [float(point.get("v") or 0) for point in points if float(point.get("v") or 0) > 0]
    if len(volumes) < 20:
        return 0
    recent = sum(volumes[-5:]) / 5
    baseline = sum(volumes[-20:-5]) / 15
    if baseline <= 0:
        return 0
    return max(-8, min(12, (recent / baseline - 1) * 12))


def signal_strength(strategy_name, signal, points, params):
    closes = [float(point["p"]) for point in points if point.get("p") is not None]
    if len(closes) < 5:
        return 0
    price = closes[-1]
    base = 48 if signal == "HOLD" else 62
    direction_boost = 8 if signal in {"BUY", "SELL"} else 0
    technical = 0
    if strategy_name == "moving_average":
        fast = int(params.get("fastMa") or 5)
        slow = int(params.get("slowMa") or 20)
        if len(closes) >= slow and price > 0:
            fast_ma = sum(closes[-fast:]) / fast
            slow_ma = sum(closes[-slow:]) / slow
            technical = min(22, abs(fast_ma - slow_ma) / price * 1000)
    elif strategy_name == "rsi":
        rsi_value = recent_rsi(closes, int(params.get("period") or 14))
        if rsi_value is not None:
            if signal == "BUY":
                technical = min(24, max(0, 50 - rsi_value) * 0.7)
            elif signal == "SELL":
                technical = min(24, max(0, rsi_value - 50) * 0.7)
            else:
                technical = min(14, abs(rsi_value - 50) * 0.35)
    elif strategy_name == "macd":
        fast = int(params.get("fast") or 12)
        slow = int(params.get("slow") or 26)
        signal_period = int(params.get("signal") or 9)
        if len(closes) >= slow + signal_period and price > 0:
            fast_ema = simple_ema(closes, fast)
            slow_ema = simple_ema(closes, slow)
            macd_line = [fast_value - slow_value for fast_value, slow_value in zip(fast_ema, slow_ema)]
            signal_line = simple_ema(macd_line, signal_period)
            technical = min(24, abs(macd_line[-1] - signal_line[-1]) / price * 1500)
    score = base + direction_boost + technical + volume_boost(points)
    if signal == "HOLD":
        score = min(score, 72)
    return int(max(0, min(100, round(score))))


def clamp_score(value):
    return max(0, min(100, float(value)))


def scanner_rating(score):
    if score >= 90:
        return "Strong Buy"
    if score >= 80:
        return "Buy"
    if score >= 70:
        return "Watch"
    if score >= 60:
        return "Hold"
    return "Avoid"


def suggested_position_size(score):
    if score > 90:
        return 20
    if score >= 80:
        return 15
    if score >= 70:
        return 10
    if score >= 60:
        return 5
    return 0


def scanner_final_score(strength, sharpe, return_pct, max_drawdown):
    strength_score = clamp_score(strength)
    sharpe_score = clamp_score((float(sharpe) / 2) * 100)
    return_score = clamp_score((float(return_pct) / 50) * 100)
    drawdown_score = clamp_score(100 - (float(max_drawdown) / 35) * 100)
    return round(
        strength_score * 0.30
        + sharpe_score * 0.30
        + return_score * 0.20
        + drawdown_score * 0.20,
        2,
    )


def strategy_decision_score(item):
    sharpe = float(item.get("sharpeRatio") or 0)
    return_pct = float(item.get("returnPct") or 0)
    drawdown = float(item.get("maxDrawdown") or 0)
    return round(0.6 * sharpe + 0.3 * return_pct - 0.1 * drawdown, 4)


def signal_mode_value(value):
    mode = (value or "best").strip().lower()
    return mode if mode in {"best", "weighted", "individual"} else "best"


def scanner_decision_results(results, signal_mode="best"):
    mode = signal_mode_value(signal_mode)
    if mode == "individual":
        return [
            {
                **item,
                "bestStrategy": item.get("strategy"),
                "bestScore": strategy_decision_score(item),
                "strategyScore": strategy_decision_score(item),
                "finalSignal": item.get("signal"),
                "signalMode": mode,
                "sourceStrategies": [item.get("strategy")],
            }
            for item in results
        ]

    grouped = {}
    for item in results:
        grouped.setdefault(item["symbol"], []).append(item)

    decisions = []
    for symbol, rows in grouped.items():
        scored = [{**item, "strategyScore": strategy_decision_score(item)} for item in rows]
        if mode == "weighted":
            signal_weights = {"BUY": 0.0, "SELL": 0.0, "HOLD": 0.0}
            for item in scored:
                weight = max(0.01, item["strategyScore"] + 100)
                signal_weights[item.get("signal") or "HOLD"] = signal_weights.get(item.get("signal") or "HOLD", 0.0) + weight
            final_signal = max(signal_weights, key=signal_weights.get)
            candidates = [item for item in scored if item.get("signal") == final_signal] or scored
            best = max(candidates, key=lambda item: item["strategyScore"])
            best_score = round(signal_weights.get(final_signal, 0.0) / max(1, len(scored)), 4)
        else:
            best = max(scored, key=lambda item: item["strategyScore"])
            final_signal = best.get("signal")
            best_score = best["strategyScore"]

        decisions.append(
            {
                **best,
                "bestStrategy": best.get("strategy"),
                "bestScore": best_score,
                "strategyScore": best["strategyScore"],
                "finalSignal": final_signal,
                "signal": final_signal,
                "signalMode": mode,
                "sourceStrategies": [item.get("strategy") for item in scored],
                "strategyCandidates": [
                    {
                        "strategy": item.get("strategy"),
                        "signal": item.get("signal"),
                        "strategyScore": item["strategyScore"],
                        "sharpeRatio": item.get("sharpeRatio"),
                        "returnPct": item.get("returnPct"),
                        "maxDrawdown": item.get("maxDrawdown"),
                    }
                    for item in sorted(scored, key=lambda item: item["strategyScore"], reverse=True)
                ],
            }
        )
    decisions.sort(key=lambda item: (item.get("bestScore") or 0, item.get("finalScore") or 0), reverse=True)
    return decisions


def quality_filter_config(settings):
    def configured_value(name, default):
        value = settings.get(name)
        return default if value is None else value

    mode = (settings.get("quality_mode") or "normal").lower()
    if mode == "custom":
        return {
            "mode": "custom",
            "min_score": float(configured_value("quality_min_score", 0)),
            "min_sharpe": float(configured_value("quality_min_sharpe", -0.5)),
            "min_return": float(configured_value("quality_min_return_pct", -10)),
            "max_drawdown": float(configured_value("quality_max_drawdown_pct", 50)),
            "min_trades": int(configured_value("quality_min_trade_count", 3)),
            "checks": {"score", "sharpe", "return", "drawdown", "trades"},
        }
    preset = QUALITY_FILTER_PRESETS.get(mode, QUALITY_FILTER_PRESETS["normal"])
    return {"mode": mode if mode in QUALITY_FILTER_PRESETS else "normal", **preset}


def passes_auto_quality_filter(item, config):
    signal = item.get("signal")
    if signal not in {"BUY", "SELL"}:
        return False, "Signal is HOLD or unsupported."
    score = float(item.get("finalScore") or item.get("strength") or 0)
    sharpe = float(item.get("sharpeRatio") or 0)
    return_pct = float(item.get("returnPct") or 0)
    drawdown = float(item.get("maxDrawdown") or 0)
    trades = int(item.get("tradeCount") or 0)
    failures = []
    checks = config.get("checks") or {"score", "sharpe", "return", "drawdown", "trades"}
    if "score" in checks and score <= float(config["min_score"]):
        failures.append(f"Score <= {config['min_score']:g}")
    if "sharpe" in checks and sharpe <= float(config["min_sharpe"]):
        failures.append(f"Sharpe <= {config['min_sharpe']:g}")
    if "return" in checks and return_pct <= float(config["min_return"]):
        failures.append(f"Return <= {config['min_return']:g}%")
    if "drawdown" in checks and drawdown >= float(config["max_drawdown"]):
        failures.append(f"Drawdown >= {config['max_drawdown']:g}%")
    if "trades" in checks and trades < int(config["min_trades"]):
        failures.append(f"Trades < {config['min_trades']}")
    if failures:
        return False, f"Failed {config['mode']} quality filter: " + ", ".join(failures)
    return True, f"Passed {config['mode']} quality filter."


def scan_watchlist_symbol(symbol, user_strategy_settings=None, timeframe="1d"):
    normalized = normalize_market_symbol(symbol)
    quote_data = normalize_quote(normalized)
    backtest_points = normalize_backtest_history(normalized, "1y")[-260:]
    points = normalize_signal_history(normalized, timeframe)
    if len(points) < 30 or len(backtest_points) < 30:
        raise ValueError("Not enough historical data for scanning")
    market_key, market_label = market_kind(normalized)
    rows = []
    for strategy_name in ("moving_average", "rsi", "macd"):
        params = params_for_strategy(strategy_name, user_strategy_settings)
        market_data = {
            "symbol": normalized,
            "price": quote_data["price"],
            "quote": quote_data,
            "history": points,
            "params": params,
        }
        signal = strategy_engine.generate_signal(strategy_name, normalized, market_data)
        try:
            backtest = summarize_backtest_result(run_strategy_backtest(strategy_name, normalized, backtest_points, params))
        except Exception:
            backtest = {"returnPct": 0, "sharpeRatio": 0, "maxDrawdown": 0, "tradeCount": 0}
        strength = signal_strength(strategy_name, signal["signal"], points, params)
        return_pct = float(backtest.get("returnPct") or 0)
        sharpe = float(backtest.get("sharpeRatio") or 0)
        max_drawdown = float(backtest.get("maxDrawdown") or 0)
        trade_count = int(backtest.get("tradeCount") or 0)
        final_score = scanner_final_score(strength, sharpe, return_pct, max_drawdown)
        passes_filter = sharpe > 0 and return_pct > 0 and max_drawdown < 35 and trade_count >= 5
        rows.append(
            {
                "symbol": normalized,
                "name": quote_data.get("name") or normalized,
                "market": market_label,
                "marketKey": market_key,
                "currentPrice": quote_data["price"],
                "currency": quote_data.get("currency") or "USD",
                "strategy": strategy_name,
                "strategyLabel": scanner_strategy_label(strategy_name),
                "signal": signal["signal"],
                "reason": signal.get("reason") or "",
                "strength": strength,
                "finalScore": final_score,
                "rating": scanner_rating(final_score),
                "suggestedPositionSize": suggested_position_size(final_score),
                "passesFilter": passes_filter,
                "returnPct": return_pct,
                "sharpeRatio": sharpe,
                "maxDrawdown": max_drawdown,
                "tradeCount": trade_count,
                "time": int(time.time() * 1000),
            }
        )
    return rows


def scanner_symbols_for_scope(conn, user_id, scope="watchlist", include_crypto=False):
    requested = (scope or "watchlist").strip().lower()
    if requested not in {"watchlist", "us", "a", "hk", "crypto", "mixed"}:
        requested = "watchlist"
    with conn.cursor() as cur:
        cur.execute("SELECT symbol FROM watchlist WHERE user_id = %s ORDER BY sort_order, symbol LIMIT %s", (user_id, AUTO_TRADING_SCAN_LIMIT))
        watchlist = [row["symbol"] for row in cur.fetchall()]

    symbols = []
    if requested == "watchlist":
        symbols = watchlist
    elif requested in SCANNER_UNIVERSES:
        symbols = SCANNER_UNIVERSES[requested]
    elif requested == "mixed":
        symbols = watchlist + SCANNER_UNIVERSES["us"] + SCANNER_UNIVERSES["a"] + SCANNER_UNIVERSES["hk"] + SCANNER_UNIVERSES["crypto"]

    if include_crypto:
        symbols = SCANNER_UNIVERSES["crypto"] + symbols

    normalized = []
    seen = set()
    for symbol in symbols:
        item = normalize_market_symbol(symbol)
        if item and item not in seen:
            normalized.append(item)
            seen.add(item)
        if len(normalized) >= AUTO_TRADING_SCAN_LIMIT:
            break
    return requested, normalized


def scan_symbols(symbols, user_strategy_settings=None, timeframe="1d"):
    if not symbols:
        return [], []
    results = []
    errors = []
    with ThreadPoolExecutor(max_workers=min(6, len(symbols))) as executor:
        futures = {executor.submit(scan_watchlist_symbol, symbol, user_strategy_settings, timeframe): symbol for symbol in symbols}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                results.extend(future.result())
            except Exception as error:
                errors.append({"symbol": symbol, "error": str(error)})
    results.sort(key=lambda item: (item["finalScore"], item["returnPct"], item["sharpeRatio"]), reverse=True)
    return results, errors


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
                "buyReason": signal.get("reason") or "",
                "strategy": strategy_name,
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
                    "strategy": strategy_name,
                    "buyReason": entry.get("buyReason") if entry else "",
                    "sellReason": signal.get("reason") or "",
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
    sortino_ratio = calculate_sortino_ratio(equity_curve)
    trade_pnls = [trade["pnl"] for trade in trades]
    wins = [pnl for pnl in trade_pnls if pnl > 0]
    losses = [pnl for pnl in trade_pnls if pnl < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = ratio_or_none(gross_profit, gross_loss)
    calmar_ratio = ratio_or_none(annual_return, max_drawdown)
    expectancy = (sum(trade_pnls) / len(trade_pnls)) if trade_pnls else 0
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
        "profitFactor": profit_factor,
        "calmarRatio": calmar_ratio,
        "sortinoRatio": sortino_ratio,
        "expectancy": expectancy,
        "winRate": win_rate,
        "tradeCount": len(trades),
        "avgProfit": (sum(wins) / len(wins)) if wins else 0,
        "avgLoss": (sum(losses) / len(losses)) if losses else 0,
        "equityCurve": equity_curve,
        "candles": [
            {"time": point["t"], "open": point.get("o", point["p"]), "high": point.get("h", point["p"]), "low": point.get("l", point["p"]), "close": point.get("c", point["p"])}
            for point in points
        ],
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
    try:
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, connect_timeout=10)
    except Exception as error:
        if "SSL connection has been closed unexpectedly" not in str(error) or "sslmode=" in DATABASE_URL:
            raise
        separator = "&" if "?" in DATABASE_URL else "?"
        fallback_url = f"{DATABASE_URL}{separator}sslmode=disable"
        print("Warning: PostgreSQL SSL negotiation failed; retrying with sslmode=disable.", flush=True)
        return psycopg.connect(fallback_url, row_factory=dict_row, connect_timeout=10)


def init_db():
    global DB_INITIALIZED, DB_INIT_ERROR
    if not db_ready():
        DB_INIT_ERROR = "DATABASE_URL is not configured or psycopg is unavailable."
        print(f"Warning: {DB_INIT_ERROR}; auth/account APIs will return 503.")
        DB_INITIALIZED = False
        return False
    schema_path = PROJECT_DIR / "schema.sql"
    schema = schema_path.read_text(encoding="utf-8")
    with db_connect() as conn:
        with conn.cursor() as cur:
            for statement in [part.strip() for part in schema.split(";") if part.strip()]:
                try:
                    cur.execute(statement)
                    conn.commit()
                except Exception as error:
                    conn.rollback()
                    preview = " ".join(statement.split())[:240]
                    DB_INIT_ERROR = f"{error}; statement: {preview}"
                    print(f"Schema migration failed: {preview}", flush=True)
                    raise
    DB_INITIALIZED = True
    DB_INIT_ERROR = ""
    return True


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
        cur.execute(
            """
            INSERT INTO auto_trading_settings (user_id)
            VALUES (%s)
            ON CONFLICT (user_id) DO NOTHING
            """,
            (user_id,),
        )


def estimate_positions_value(conn, user_id, price_overrides=None):
    price_overrides = price_overrides or {}
    with conn.cursor() as cur:
        cur.execute("SELECT symbol, qty, avg_price FROM positions WHERE user_id = %s AND qty > 0", (user_id,))
        positions = cur.fetchall()
    total = Decimal("0")
    for position in positions:
        if position["symbol"] in price_overrides:
            price = Decimal(str(price_overrides[position["symbol"]]))
        else:
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


def record_equity_snapshot(conn, user_id, reason, related_trade_id=None, totals=None):
    totals = totals or portfolio_totals(conn, user_id)
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


def execute_paper_trade(conn, user_id, symbol, side, qty, quote_data=None, execution_source="manual", strategy_name=None, signal_source=None, entry_reason=None, position_risk=None):
    symbol = normalize_market_symbol(symbol)
    qty = Decimal(str(qty))
    if side not in {"buy", "sell"} or not symbol or qty <= 0:
        raise ValueError("Invalid trade.")
    quote_data = quote_data or normalize_quote(symbol)
    price = Decimal(str(quote_data["price"]))
    value = qty * price
    currency = quote_data["currency"]

    with conn.cursor() as cur:
        cur.execute("SELECT cash_balance, starting_cash FROM accounts WHERE user_id = %s FOR UPDATE", (user_id,))
        account = cur.fetchone()
        cur.execute("SELECT qty, avg_price FROM positions WHERE user_id = %s AND symbol = %s FOR UPDATE", (user_id, symbol))
        position = cur.fetchone()
        current_qty = position["qty"] if position else Decimal("0")
        avg_price = position["avg_price"] if position else Decimal("0")
        equity_before = account["cash_balance"] + estimate_positions_value(conn, user_id, {symbol: price})
        realized_pnl = None

        if side == "buy":
            if account["cash_balance"] < value:
                raise ValueError("Insufficient cash.")
            new_qty = current_qty + qty
            new_avg = ((current_qty * avg_price) + value) / new_qty
            cur.execute("UPDATE accounts SET cash_balance = cash_balance - %s, updated_at = now() WHERE user_id = %s", (value, user_id))
            account_balance_after = account["cash_balance"] - value
            position_qty_after = new_qty
            market_key, market_label = market_kind(symbol)
            position_entry_price = price if current_qty <= 0 else avg_price
            position_opened_at_sql = "now()" if current_qty <= 0 else "positions.opened_at"
            cur.execute(
                """
                INSERT INTO positions
                    (user_id, symbol, qty, avg_price, currency, market, strategy_name, signal_source, entry_reason, entry_price,
                     highest_price, stop_loss_pct, take_profit_pct, trailing_stop_pct, max_holding_days, timeframe)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id, symbol)
                DO UPDATE SET
                    qty = EXCLUDED.qty,
                    avg_price = EXCLUDED.avg_price,
                    currency = EXCLUDED.currency,
                    market = CASE WHEN positions.market = '' THEN EXCLUDED.market ELSE positions.market END,
                    strategy_name = COALESCE(positions.strategy_name, EXCLUDED.strategy_name),
                    signal_source = COALESCE(positions.signal_source, EXCLUDED.signal_source),
                    entry_reason = COALESCE(positions.entry_reason, EXCLUDED.entry_reason),
                    entry_price = COALESCE(positions.entry_price, EXCLUDED.entry_price),
                    highest_price = GREATEST(COALESCE(positions.highest_price, 0), EXCLUDED.highest_price),
                    opened_at = """ + position_opened_at_sql + """,
                    updated_at = now()
                """,
                (
                    user_id, symbol, new_qty, new_avg, currency, market_label, strategy_name, signal_source,
                    entry_reason, position_entry_price, price,
                    position_risk.get("stop_loss_pct") if position_risk else None,
                    position_risk.get("take_profit_pct") if position_risk else None,
                    position_risk.get("trailing_stop_pct") if position_risk else None,
                    position_risk.get("max_holding_days") if position_risk else None,
                    position_risk.get("timeframe") if position_risk else None,
                ),
            )
        else:
            if current_qty < qty:
                raise ValueError("Insufficient shares.")
            new_qty = current_qty - qty
            realized_pnl = (price - avg_price) * qty
            cur.execute("UPDATE accounts SET cash_balance = cash_balance + %s, updated_at = now() WHERE user_id = %s", (value, user_id))
            account_balance_after = account["cash_balance"] + value
            position_qty_after = new_qty
            if new_qty <= 0:
                cur.execute("DELETE FROM positions WHERE user_id = %s AND symbol = %s", (user_id, symbol))
            else:
                cur.execute("UPDATE positions SET qty = %s, updated_at = now() WHERE user_id = %s AND symbol = %s", (new_qty, user_id, symbol))

        cur.execute(
            """
            INSERT INTO orders (user_id, symbol, side, order_type, status, qty, filled_qty, price, value, currency)
            VALUES (%s, %s, %s, 'market', 'filled', %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (user_id, symbol, side, qty, qty, price, value, currency),
        )
        order_id = cur.fetchone()["id"]
        cur.execute(
            """
            INSERT INTO trades (user_id, order_id, symbol, side, qty, price, value, currency, execution_source, strategy_name)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (user_id, order_id, symbol, side, qty, price, value, currency, execution_source, strategy_name),
        )
        trade_id = cur.fetchone()["id"]
        # A fill only converts cash into a position or a position into cash. With
        # no fees or slippage, the execution itself must not change total equity.
        trade_totals = {
            "cash": account_balance_after,
            "starting_cash": account["starting_cash"],
            "positions_value": equity_before - account_balance_after,
            "equity": equity_before,
        }
        equity_after = record_equity_snapshot(conn, user_id, "trade", trade_id, trade_totals)
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
    return {
        "tradeId": trade_id,
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "price": price,
        "value": value,
        "currency": currency,
        "realizedPnl": realized_pnl,
    }


def clean_auto_settings_payload(data):
    def numeric_value(name, default, minimum=None, maximum=None):
        try:
            value = float(data.get(name, default))
        except (TypeError, ValueError):
            value = default
        if minimum is not None:
            value = max(minimum, value)
        if maximum is not None:
            value = min(maximum, value)
        return value

    def int_value(name, default, minimum=None, maximum=None):
        try:
            value = int(data.get(name, default))
        except (TypeError, ValueError):
            value = default
        if minimum is not None:
            value = max(minimum, value)
        if maximum is not None:
            value = min(maximum, value)
        return value

    def allowed_number(name, default, allowed):
        try:
            value = float(data.get(name, default))
        except (TypeError, ValueError):
            value = default
        return value if value in allowed else default

    def allowed_int(name, default, allowed):
        try:
            value = int(data.get(name, default))
        except (TypeError, ValueError):
            value = default
        return value if value in allowed else default

    scope = (data.get("scanScope") or "mixed").strip().lower()
    if scope not in {"watchlist", "us", "a", "hk", "crypto", "mixed"}:
        scope = "watchlist"
    signal_mode = signal_mode_value(data.get("signalMode") or "best")
    timeframe = (data.get("timeframe") or "1h").strip().lower()
    if timeframe not in {"1d", "1h", "15m"}:
        timeframe = "1h"
    quality_mode = (data.get("qualityMode") or "normal").strip().lower()
    if quality_mode not in {"strict", "normal", "loose", "custom"}:
        quality_mode = "normal"
    return {
        "enabled": bool(data.get("enabled")),
        "positionPct": allowed_number("positionPct", 10, {5, 10, 20, 50}),
        "maxPositions": allowed_int("maxPositions", 3, {1, 3, 5, 10}),
        "maxDailyLossPct": allowed_number("maxDailyLossPct", 5, {2, 5, 10}),
        "maxTotalDrawdownPct": allowed_number("maxTotalDrawdownPct", 20, {10, 20, 30, 50}),
        "cooldownHours": allowed_number("cooldownHours", 6, {1, 3, 6, 12, 24}),
        "allowAddPosition": bool(data.get("allowAddPosition")),
        "scanScope": scope,
        "signalMode": signal_mode,
        "timeframe": timeframe,
        "scanIntervalMinutes": allowed_int("scanIntervalMinutes", 15, {5, 15, 60}),
        "stopLossPct": numeric_value("stopLossPct", 5, 0, 100),
        "takeProfitPct": numeric_value("takeProfitPct", 15, 0, 1000),
        "trailingStopPct": numeric_value("trailingStopPct", 8, 0, 100),
        "maxHoldingDays": int_value("maxHoldingDays", 30, 1, 3650),
        "maxPortfolioExposurePct": numeric_value("maxPortfolioExposurePct", 70, 0, 100),
        "maxCryptoExposurePct": numeric_value("maxCryptoExposurePct", 20, 0, 100),
        "maxDailyOrders": int_value("maxDailyOrders", 10, 1, 1000),
        "killSwitch": bool(data.get("killSwitch")),
        "qualityMode": quality_mode,
        "qualityMinScore": numeric_value("qualityMinScore", QUALITY_FILTER_PRESETS["normal"]["min_score"], 0, 100),
        "qualityMinSharpe": numeric_value("qualityMinSharpe", QUALITY_FILTER_PRESETS["normal"]["min_sharpe"], -10, 10),
        "qualityMinReturnPct": numeric_value("qualityMinReturnPct", QUALITY_FILTER_PRESETS["normal"]["min_return"], -100, 500),
        "qualityMaxDrawdownPct": numeric_value("qualityMaxDrawdownPct", QUALITY_FILTER_PRESETS["normal"]["max_drawdown"], 0, 100),
        "qualityMinTradeCount": int_value("qualityMinTradeCount", QUALITY_FILTER_PRESETS["normal"]["min_trades"], 0, 500),
    }


def auto_trading_stats(conn, user_id):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO auto_trading_settings (user_id)
            VALUES (%s)
            ON CONFLICT (user_id) DO NOTHING
            """,
            (user_id,),
        )
        cur.execute(
            """
            SELECT enabled, stopped, stop_reason, position_pct, max_positions, max_daily_loss_pct,
                   max_total_drawdown_pct, cooldown_hours, allow_add_position, scan_scope, signal_mode,
                   timeframe, scan_interval_minutes, stop_loss_pct, take_profit_pct, trailing_stop_pct,
                   max_holding_days, max_portfolio_exposure_pct, max_crypto_exposure_pct, max_daily_orders, kill_switch,
                   quality_mode, quality_min_score, quality_min_sharpe, quality_min_return_pct,
                   quality_max_drawdown_pct, quality_min_trade_count,
                   signals_generated, signals_passed_filter, signals_executed, signals_rejected,
                   scheduler_status, last_executed_signal, enabled_at, last_run_at, updated_at
            FROM auto_trading_settings
            WHERE user_id = %s
            """,
            (user_id,),
        )
        settings = cur.fetchone()
        cur.execute(
            """
            SELECT
                COUNT(*) FILTER (WHERE executed_at >= date_trunc('day', now())) AS today_trades,
                COUNT(*) AS total_trades,
                COALESCE(SUM(CASE WHEN side = 'sell' THEN realized_pnl ELSE 0 END), 0) AS realized_pnl
            FROM trades
            WHERE user_id = %s AND execution_source = 'auto'
            """,
            (user_id,),
        )
        trade_stats = cur.fetchone()
        cur.execute("SELECT starting_cash FROM accounts WHERE user_id = %s", (user_id,))
        account = cur.fetchone()
        cur.execute(
            """
            SELECT scan_id, symbol, market, strategy, signal, score, action, reason, price, qty, created_at
            FROM auto_trading_logs
            WHERE user_id = %s
            ORDER BY created_at DESC
            LIMIT 100
            """,
            (user_id,),
        )
        all_logs = cur.fetchall()
        latest_scan_id = all_logs[0]["scan_id"] if all_logs else None
        latest_scan_logs = [row for row in all_logs if row["scan_id"] == latest_scan_id] if latest_scan_id else []
        last_scan_at = all_logs[0]["created_at"] if all_logs else settings["last_run_at"]
        cur.execute(
            """
            SELECT symbol, strategy, signal, created_at
            FROM auto_trading_logs
            WHERE user_id = %s AND action = 'EXECUTED'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (user_id,),
        )
        last_executed = cur.fetchone()
    starting_cash = account["starting_cash"] or Decimal("0")
    realized_pnl = trade_stats["realized_pnl"] or Decimal("0")
    cumulative_return = (realized_pnl / starting_cash * Decimal("100")) if starting_cash > 0 else Decimal("0")
    next_scan_at = None
    if settings["enabled"] and not settings["stopped"] and not settings["kill_switch"]:
        base_time = settings["last_run_at"] or datetime.now(timezone.utc)
        next_scan_at = base_time + timedelta(minutes=int(settings["scan_interval_minutes"] or 15))
    running_time_ms = None
    if settings["enabled"] and settings["enabled_at"]:
        running_time_ms = max(0, int((datetime.now(timezone.utc) - settings["enabled_at"]).total_seconds() * 1000))
    return {
        "enabled": settings["enabled"],
        "stopped": settings["stopped"],
        "stopReason": settings["stop_reason"],
        "positionPct": settings["position_pct"],
        "maxPositions": settings["max_positions"],
        "maxDailyLossPct": settings["max_daily_loss_pct"],
        "maxTotalDrawdownPct": settings["max_total_drawdown_pct"],
        "cooldownHours": settings["cooldown_hours"],
        "allowAddPosition": settings["allow_add_position"],
        "scanScope": settings["scan_scope"],
        "signalMode": settings["signal_mode"],
        "timeframe": settings["timeframe"],
        "scanIntervalMinutes": settings["scan_interval_minutes"],
        "stopLossPct": settings["stop_loss_pct"],
        "takeProfitPct": settings["take_profit_pct"],
        "trailingStopPct": settings["trailing_stop_pct"],
        "maxHoldingDays": settings["max_holding_days"],
        "maxPortfolioExposurePct": settings["max_portfolio_exposure_pct"],
        "maxCryptoExposurePct": settings["max_crypto_exposure_pct"],
        "maxDailyOrders": settings["max_daily_orders"],
        "killSwitch": settings["kill_switch"],
        "qualityMode": settings["quality_mode"],
        "qualityMinScore": settings["quality_min_score"],
        "qualityMinSharpe": settings["quality_min_sharpe"],
        "qualityMinReturnPct": settings["quality_min_return_pct"],
        "qualityMaxDrawdownPct": settings["quality_max_drawdown_pct"],
        "qualityMinTradeCount": settings["quality_min_trade_count"],
        "signalsGenerated": settings["signals_generated"],
        "signalsPassedFilter": settings["signals_passed_filter"],
        "signalsExecuted": settings["signals_executed"],
        "signalsRejected": settings["signals_rejected"],
        "schedulerStatus": settings["scheduler_status"],
        "lastExecutedSignal": settings["last_executed_signal"],
        "enabledAt": int(settings["enabled_at"].timestamp() * 1000) if settings["enabled_at"] else None,
        "lastRunAt": int(settings["last_run_at"].timestamp() * 1000) if settings["last_run_at"] else None,
        "lastScanAt": int(last_scan_at.timestamp() * 1000) if last_scan_at else None,
        "nextScanAt": int(next_scan_at.timestamp() * 1000) if next_scan_at else None,
        "updatedAt": int(settings["updated_at"].timestamp() * 1000) if settings["updated_at"] else None,
        "todayTrades": int(trade_stats["today_trades"] or 0),
        "totalTrades": int(trade_stats["total_trades"] or 0),
        "cumulativeReturn": cumulative_return,
        "runningTimeMs": running_time_ms,
        "lastExecuted": {
            "symbol": last_executed["symbol"],
            "strategy": last_executed["strategy"],
            "signal": last_executed["signal"],
            "time": int(last_executed["created_at"].timestamp() * 1000),
        } if last_executed else None,
        "logs": [
            {
                "scanId": row["scan_id"],
                "symbol": row["symbol"],
                "market": row["market"],
                "strategy": row["strategy"],
                "signal": row["signal"],
                "score": row["score"],
                "action": row["action"],
                "reason": row["reason"],
                "price": row["price"],
                "qty": row["qty"],
                "time": int(row["created_at"].timestamp() * 1000),
            }
            for row in latest_scan_logs
        ],
    }


def account_daily_loss_pct(conn, user_id):
    totals = portfolio_totals(conn, user_id)
    current_equity = totals["equity"]
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT equity
            FROM equity_history
            WHERE user_id = %s AND created_at >= date_trunc('day', now())
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (user_id,),
        )
        first = cur.fetchone()
    start_equity = first["equity"] if first else current_equity
    if start_equity <= 0:
        return Decimal("0")
    loss = max(Decimal("0"), start_equity - current_equity)
    return loss / start_equity * Decimal("100")


def account_total_drawdown_pct(conn, user_id):
    totals = portfolio_totals(conn, user_id)
    current_equity = totals["equity"]
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT GREATEST(COALESCE(MAX(equity), 0), %s) AS peak_equity
            FROM equity_history
            WHERE user_id = %s
            """,
            (current_equity, user_id),
        )
        row = cur.fetchone()
    peak_equity = row["peak_equity"] or current_equity
    if peak_equity <= 0:
        return Decimal("0")
    return max(Decimal("0"), peak_equity - current_equity) / peak_equity * Decimal("100")


def record_auto_log(conn, user_id, scan_id, item, action, reason, price=None, qty=None):
    symbol = normalize_market_symbol(str(item.get("symbol") or "ACCOUNT"))
    market = item.get("market") or market_kind(symbol)[1]
    strategy = item.get("strategy") or item.get("strategyLabel") or ""
    signal = item.get("signal") or ""
    score = Decimal(str(item.get("finalScore") or item.get("score") or 0))
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO auto_trading_logs
                (user_id, scan_id, symbol, market, strategy, signal, score, action, reason, price, qty)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                user_id,
                scan_id,
                symbol,
                market,
                strategy,
                signal,
                score,
                action,
                reason,
                Decimal(str(price)) if price is not None else None,
                Decimal(str(qty)) if qty is not None else None,
            ),
        )
    return {
        "scanId": scan_id,
        "symbol": symbol,
        "market": market,
        "strategy": strategy,
        "signal": signal,
        "score": score,
        "action": action,
        "reason": reason,
        "price": price,
        "qty": qty,
        "time": int(time.time() * 1000),
    }


def auto_trade_in_cooldown(conn, user_id, symbol, cooldown_hours):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1
            FROM trades
            WHERE user_id = %s
              AND symbol = %s
              AND execution_source = 'auto'
              AND executed_at >= now() - (%s::text || ' hours')::interval
            LIMIT 1
            """,
            (user_id, symbol, float(cooldown_hours)),
        )
        return cur.fetchone() is not None


def run_auto_trading_cycle(user_id, scanner_results=None, scan_scope=None, triggered_by="scheduler"):
    actions = []
    skipped = []
    logs = []
    scan_id = secrets.token_hex(8)
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO auto_trading_settings (user_id)
                VALUES (%s)
                ON CONFLICT (user_id) DO NOTHING
                """,
                (user_id,),
            )
            cur.execute("SELECT * FROM auto_trading_settings WHERE user_id = %s FOR UPDATE", (user_id,))
            settings = cur.fetchone()
        if not settings["enabled"] or settings["stopped"] or settings["kill_switch"]:
            conn.commit()
            return {"actions": actions, "skipped": skipped, "logs": logs, "stateChanged": False, "settings": auto_trading_stats(conn, user_id)}

        daily_loss = account_daily_loss_pct(conn, user_id)
        if daily_loss >= settings["max_daily_loss_pct"]:
            reason = f"Max daily loss reached: {daily_loss:.2f}%"
            logs.append(record_auto_log(conn, user_id, scan_id, {"symbol": "ACCOUNT", "signal": "RISK", "strategy": "risk", "finalScore": 0}, "STOPPED", reason))
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE auto_trading_settings
                    SET enabled = false, stopped = true, stop_reason = %s, updated_at = now()
                    WHERE user_id = %s
                    """,
                    (reason, user_id),
                )
            conn.commit()
            return {"actions": actions, "skipped": skipped, "logs": logs, "stateChanged": True, "settings": auto_trading_stats(conn, user_id)}

        total_drawdown = account_total_drawdown_pct(conn, user_id)
        if total_drawdown >= settings["max_total_drawdown_pct"]:
            reason = f"Max total drawdown reached: {total_drawdown:.2f}%"
            logs.append(record_auto_log(conn, user_id, scan_id, {"symbol": "ACCOUNT", "signal": "RISK", "strategy": "risk", "finalScore": 0}, "STOPPED", reason))
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE auto_trading_settings
                    SET enabled = false, stopped = true, stop_reason = %s, updated_at = now()
                    WHERE user_id = %s
                    """,
                    (reason, user_id),
                )
            conn.commit()
            return {"actions": actions, "skipped": skipped, "logs": logs, "stateChanged": True, "settings": auto_trading_stats(conn, user_id)}

        if scanner_results is None:
            user_strategy_settings = load_strategy_settings(conn, user_id)
            scope, symbols = scanner_symbols_for_scope(conn, user_id, scan_scope or settings["scan_scope"], include_crypto=True)
            with conn.cursor() as cur:
                cur.execute("SELECT symbol FROM positions WHERE user_id = %s AND qty > 0 ORDER BY symbol", (user_id,))
                held_symbols = [row["symbol"] for row in cur.fetchall()]
            symbols = list(dict.fromkeys(held_symbols + symbols))[:AUTO_TRADING_SCAN_LIMIT]
            scanner_results, scan_errors = scan_symbols(symbols, user_strategy_settings, settings["timeframe"])
            for error in scan_errors:
                skipped.append({"symbol": error["symbol"], "reason": error["error"]})
                logs.append(record_auto_log(conn, user_id, scan_id, {"symbol": error["symbol"], "signal": "ERROR", "strategy": "scanner"}, "SKIPPED", error["error"]))
        else:
            scope = scan_scope or settings["scan_scope"]

        raw_scanner_results = scanner_results
        scanner_results = scanner_decision_results(raw_scanner_results, settings["signal_mode"])
        quality_config = quality_filter_config(settings)
        signals_generated = len(scanner_results)
        signals_passed = 0
        signals_executed = 0
        signals_rejected = 0
        ranked_results = sorted(scanner_results, key=lambda item: (item.get("finalScore") or 0, item.get("returnPct") or 0), reverse=True)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT symbol, qty, avg_price, market, strategy_name, entry_price, highest_price,
                       stop_loss_pct, take_profit_pct, trailing_stop_pct, max_holding_days, opened_at
                FROM positions WHERE user_id = %s AND qty > 0
                """,
                (user_id,),
            )
            position_rows = {row["symbol"]: row for row in cur.fetchall()}
            positions = {symbol: row["qty"] for symbol, row in position_rows.items()}
            cur.execute("SELECT cash_balance FROM accounts WHERE user_id = %s", (user_id,))
            cash_balance = cur.fetchone()["cash_balance"]
            cur.execute(
                "SELECT COUNT(*) AS count FROM trades WHERE user_id = %s AND execution_source = 'auto' AND executed_at >= date_trunc('day', now())",
                (user_id,),
            )
            daily_orders = int(cur.fetchone()["count"] or 0)

        raw_by_owner = {
            (normalize_market_symbol(item["symbol"]), item.get("strategy")): item
            for item in raw_scanner_results
        }
        crypto_value = Decimal("0")
        for symbol, position in list(position_rows.items()):
            try:
                quote_data = normalize_quote(symbol)
                price = Decimal(str(quote_data["price"]))
                if market_kind(symbol)[0] == "crypto":
                    crypto_value += price * position["qty"]
                entry_price = position["entry_price"] or position["avg_price"]
                highest_price = max(price, position["highest_price"] or Decimal("0"), entry_price)
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE positions SET highest_price = %s, updated_at = now() WHERE user_id = %s AND symbol = %s",
                        (highest_price, user_id, symbol),
                    )
                stop_loss = position["stop_loss_pct"] if position["stop_loss_pct"] is not None else settings["stop_loss_pct"]
                take_profit = position["take_profit_pct"] if position["take_profit_pct"] is not None else settings["take_profit_pct"]
                trailing_stop = position["trailing_stop_pct"] if position["trailing_stop_pct"] is not None else settings["trailing_stop_pct"]
                max_holding_days = position["max_holding_days"] or settings["max_holding_days"]
                held_days = (datetime.now(timezone.utc) - position["opened_at"]).total_seconds() / 86400 if position["opened_at"] else 0
                exit_reason = None
                if stop_loss and price <= entry_price * (Decimal("1") - Decimal(str(stop_loss)) / Decimal("100")):
                    exit_reason = f"Stop Loss triggered ({float(stop_loss):g}%)."
                elif take_profit and price >= entry_price * (Decimal("1") + Decimal(str(take_profit)) / Decimal("100")):
                    exit_reason = f"Take Profit triggered ({float(take_profit):g}%)."
                elif trailing_stop and highest_price > entry_price and price <= highest_price * (Decimal("1") - Decimal(str(trailing_stop)) / Decimal("100")):
                    exit_reason = f"Trailing Stop triggered ({float(trailing_stop):g}%)."
                elif max_holding_days and held_days >= int(max_holding_days):
                    exit_reason = f"Max Holding Days reached ({int(max_holding_days)} days)."
                owner_strategy = position["strategy_name"]
                owner_signal = raw_by_owner.get((symbol, owner_strategy))
                if not exit_reason and owner_signal and owner_signal.get("signal") == "SELL":
                    exit_reason = f"Opening strategy {scanner_strategy_label(owner_strategy)} issued SELL."
                if exit_reason:
                    exit_item = owner_signal or {
                        "symbol": symbol, "market": position["market"], "strategy": owner_strategy or "risk",
                        "signal": "SELL", "finalScore": 0,
                    }
                    fill = execute_paper_trade(conn, user_id, symbol, "sell", position["qty"], quote_data, "auto", owner_strategy or "risk")
                    positions.pop(symbol, None)
                    position_rows.pop(symbol, None)
                    cash_balance += fill["value"]
                    daily_orders += 1
                    actions.append({"symbol": symbol, "side": "sell", "strategy": owner_strategy or "risk", "qty": fill["qty"], "price": fill["price"], "value": fill["value"], "realizedPnl": fill["realizedPnl"]})
                    signals_executed += 1
                    logs.append(record_auto_log(conn, user_id, scan_id, exit_item, "EXECUTED", exit_reason, price=fill["price"], qty=fill["qty"]))
            except Exception as error:
                logs.append(record_auto_log(conn, user_id, scan_id, {"symbol": symbol, "strategy": position["strategy_name"] or "risk", "signal": "RISK"}, "SKIPPED", f"Position monitoring failed: {error}"))

        for item in ranked_results:
            symbol = normalize_market_symbol(item["symbol"])
            signal = item["signal"]
            strategy_name = item.get("strategy") or item.get("strategyLabel") or "scanner"
            if signal == "SELL":
                reason = "SELL ignored: only the opening strategy or position risk rules may close a position."
                skipped.append({"symbol": symbol, "signal": signal, "reason": reason})
                logs.append(record_auto_log(conn, user_id, scan_id, item, "SKIPPED", reason))
                continue
            passed_quality, quality_reason = passes_auto_quality_filter(item, quality_config)
            if not passed_quality:
                reason = quality_reason
                signals_rejected += 1
                skipped.append({"symbol": symbol, "signal": signal, "reason": reason})
                logs.append(record_auto_log(conn, user_id, scan_id, item, "SKIPPED", reason))
                continue
            signals_passed += 1
            try:
                quote_data = normalize_quote(symbol)
                price = Decimal(str(quote_data["price"]))
                if signal == "BUY":
                    if daily_orders >= int(settings["max_daily_orders"]):
                        reason = "Max Daily Orders Reached."
                        skipped.append({"symbol": symbol, "signal": signal, "reason": reason})
                        logs.append(record_auto_log(conn, user_id, scan_id, item, "SKIPPED", reason, price=price))
                        continue
                    already_holding = symbol in positions
                    if already_holding and not settings["allow_add_position"]:
                        reason = "Already Holding Position."
                        skipped.append({"symbol": symbol, "signal": signal, "reason": reason})
                        logs.append(record_auto_log(conn, user_id, scan_id, item, "SKIPPED", reason, price=price))
                        continue
                    if auto_trade_in_cooldown(conn, user_id, symbol, settings["cooldown_hours"]):
                        reason = f"Cooldown Active: {float(settings['cooldown_hours']):g} hours."
                        skipped.append({"symbol": symbol, "signal": signal, "reason": reason})
                        logs.append(record_auto_log(conn, user_id, scan_id, item, "SKIPPED", reason, price=price))
                        continue
                    if not already_holding and len(positions) >= int(settings["max_positions"]):
                        reason = "Max Positions Reached."
                        skipped.append({"symbol": symbol, "signal": signal, "reason": reason})
                        logs.append(record_auto_log(conn, user_id, scan_id, item, "SKIPPED", reason, price=price))
                        continue
                    totals = portfolio_totals(conn, user_id)
                    target_value = totals["equity"] * Decimal(str(settings["position_pct"])) / Decimal("100")
                    target_value = min(target_value, cash_balance)
                    max_exposure = totals["equity"] * Decimal(str(settings["max_portfolio_exposure_pct"])) / Decimal("100")
                    if totals["positions_value"] + target_value > max_exposure:
                        reason = "Max Portfolio Exposure Reached."
                        skipped.append({"symbol": symbol, "signal": signal, "reason": reason})
                        logs.append(record_auto_log(conn, user_id, scan_id, item, "SKIPPED", reason, price=price))
                        continue
                    if market_kind(symbol)[0] == "crypto":
                        max_crypto = totals["equity"] * Decimal(str(settings["max_crypto_exposure_pct"])) / Decimal("100")
                        if crypto_value + target_value > max_crypto:
                            reason = "Max Crypto Exposure Reached."
                            skipped.append({"symbol": symbol, "signal": signal, "reason": reason})
                            logs.append(record_auto_log(conn, user_id, scan_id, item, "SKIPPED", reason, price=price))
                            continue
                    if target_value <= 0 or price <= 0:
                        reason = "Insufficient Cash."
                        skipped.append({"symbol": symbol, "signal": signal, "reason": reason})
                        logs.append(record_auto_log(conn, user_id, scan_id, item, "SKIPPED", reason, price=price))
                        continue
                    qty = target_value / price
                    position_risk = {
                        "stop_loss_pct": settings["stop_loss_pct"],
                        "take_profit_pct": settings["take_profit_pct"],
                        "trailing_stop_pct": settings["trailing_stop_pct"],
                        "max_holding_days": settings["max_holding_days"],
                        "timeframe": settings["timeframe"],
                    }
                    fill = execute_paper_trade(conn, user_id, symbol, "buy", qty, quote_data, "auto", strategy_name, signal, item.get("reason") or quality_reason, position_risk)
                    positions[symbol] = positions.get(symbol, Decimal("0")) + fill["qty"]
                    cash_balance -= fill["value"]
                    daily_orders += 1
                    if market_kind(symbol)[0] == "crypto":
                        crypto_value += fill["value"]
                    actions.append({"symbol": symbol, "side": "buy", "strategy": strategy_name, "qty": fill["qty"], "price": fill["price"], "value": fill["value"]})
                    signals_executed += 1
                    reason = "Position Added" if already_holding else "Position Opened"
                    logs.append(record_auto_log(conn, user_id, scan_id, item, "EXECUTED", reason, price=fill["price"], qty=fill["qty"]))
            except Exception as error:
                reason = str(error)
                signals_rejected += 1
                skipped.append({"symbol": symbol, "signal": signal, "reason": reason})
                logs.append(record_auto_log(conn, user_id, scan_id, item, "SKIPPED", reason))

        with conn.cursor() as cur:
            signals_rejected = max(0, signals_generated - signals_passed)
            last_signal = ""
            if actions:
                latest = actions[-1]
                last_signal = f"{latest['symbol']} {str(latest['side']).upper()} {latest['strategy']}"
            cur.execute(
                """
                UPDATE auto_trading_settings
                SET last_run_at = now(),
                    scheduler_status = %s,
                    last_executed_signal = CASE WHEN %s <> '' THEN %s ELSE last_executed_signal END,
                    signals_generated = %s,
                    signals_passed_filter = %s,
                    signals_executed = %s,
                    signals_rejected = %s,
                    updated_at = now()
                WHERE user_id = %s
                """,
                ("running", last_signal, last_signal, signals_generated, signals_passed, signals_executed, signals_rejected, user_id),
            )
        conn.commit()
        return {"actions": actions, "skipped": skipped, "logs": logs, "scope": scope, "stateChanged": bool(actions), "settings": auto_trading_stats(conn, user_id)}


def start_auto_trading_scheduler():
    if not db_ready():
        return

    def due_users():
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT user_id
                    FROM auto_trading_settings
                    WHERE enabled = true
                      AND stopped = false
                      AND kill_switch = false
                      AND (
                        last_run_at IS NULL
                        OR last_run_at <= now() - (scan_interval_minutes::text || ' minutes')::interval
                      )
                    ORDER BY COALESCE(last_run_at, '1970-01-01'::timestamptz) ASC
                    LIMIT 20
                    """,
                )
                return [row["user_id"] for row in cur.fetchall()]

    def run():
        while True:
            try:
                if AUTO_SCHEDULER_LOCK.acquire(blocking=False):
                    try:
                        for user_id in due_users():
                            try:
                                run_auto_trading_cycle(user_id, triggered_by="scheduler")
                            except Exception as error:
                                with db_connect() as conn:
                                    scan_id = secrets.token_hex(8)
                                    record_auto_log(conn, user_id, scan_id, {"symbol": "ACCOUNT", "signal": "ERROR", "strategy": "scheduler"}, "ERROR", str(error))
                                    with conn.cursor() as cur:
                                        cur.execute(
                                            """
                                            UPDATE auto_trading_settings
                                            SET scheduler_status = 'error', stop_reason = %s, updated_at = now()
                                            WHERE user_id = %s
                                            """,
                                            (str(error), user_id),
                                        )
                                    conn.commit()
                    finally:
                        AUTO_SCHEDULER_LOCK.release()
            finally:
                time.sleep(15)

    thread = threading.Thread(target=run, name="auto-trading-scheduler", daemon=True)
    thread.start()


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
        "/api/scanner": "handle_scanner",
    }
    POST_ROUTES = {
        "/api/auth/register": "handle_register",
        "/api/auth/login": "handle_login",
        "/api/auth/logout": "handle_logout",
        "/api/auth/password": "handle_change_password",
        "/api/account/deposit": "handle_deposit",
        "/api/account/reset": "handle_reset_account",
        "/api/account/active-symbol": "handle_active_symbol",
        "/api/auto-trading/settings": "handle_auto_trading_settings",
        "/api/strategy/settings": "handle_strategy_settings",
        "/api/auto-trading/run": "handle_auto_trading_run",
        "/api/watchlist": "handle_add_watchlist",
        "/api/trade": "handle_trade",
        "/api/strategy/run": "handle_run_strategy",
        "/api/strategy/backtest": "handle_strategy_backtest",
        "/api/strategy/portfolio-backtest": "handle_portfolio_backtest",
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

    def end_headers(self):
        if not self.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.send_header("Clear-Site-Data", '"cache"')
        super().end_headers()

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
        db_reachable = False
        db_error = DB_INIT_ERROR
        if db_ready():
            try:
                with db_connect() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT 1")
                        db_reachable = True
            except Exception as error:
                db_error = str(error)
        self.end_json(200, {
            "ok": True,
            "database": db_reachable and DB_INITIALIZED,
            "databaseReachable": db_reachable,
            "databaseInitialized": DB_INITIALIZED,
            "databaseError": db_error,
            "serverTime": int(time.time() * 1000),
        })

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
                strategy_settings = load_strategy_settings(conn, user_id)
                cur.execute("SELECT symbol FROM watchlist WHERE user_id = %s ORDER BY sort_order, symbol", (user_id,))
                symbols = [row["symbol"] for row in cur.fetchall()]
                cur.execute(
                    """
                    SELECT symbol, qty, avg_price, currency, market, strategy_name, signal_source,
                           entry_reason, entry_price, highest_price, stop_loss_pct, take_profit_pct,
                           trailing_stop_pct, max_holding_days, timeframe, opened_at
                    FROM positions
                    WHERE user_id = %s AND qty > 0
                    ORDER BY symbol
                    """,
                    (user_id,),
                )
                positions = cur.fetchall()
                cur.execute(
                    """
                    SELECT id, order_id, symbol, side, qty, price, value, currency,
                           account_balance_after, position_qty_after, realized_pnl,
                           equity_before, equity_after, equity_change, execution_source,
                           strategy_name, executed_at
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
            auto_trading = auto_trading_stats(conn, user_id)
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
            "strategySettings": strategy_settings,
            "symbols": symbols or DEFAULT_SYMBOLS,
            "positions": {
                row["symbol"]: {
                    "qty": row["qty"],
                    "avgPrice": row["avg_price"],
                    "currency": row["currency"],
                    "market": row["market"],
                    "strategyName": row["strategy_name"],
                    "signalSource": row["signal_source"],
                    "entryReason": row["entry_reason"],
                    "entryPrice": row["entry_price"],
                    "highestPrice": row["highest_price"],
                    "stopLossPct": row["stop_loss_pct"],
                    "takeProfitPct": row["take_profit_pct"],
                    "trailingStopPct": row["trailing_stop_pct"],
                    "maxHoldingDays": row["max_holding_days"],
                    "timeframe": row["timeframe"],
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
                    "executionSource": row["execution_source"],
                    "strategyName": row["strategy_name"],
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
            "autoTrading": auto_trading,
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

    def handle_scanner(self, parsed):
        user = self.require_user()
        if not user:
            return
        query_data = parse_qs(parsed.query)
        requested_scope = (query_data.get("scope") or ["watchlist"])[0]
        requested_mode = signal_mode_value((query_data.get("signalMode") or ["best"])[0])
        with db_connect() as conn:
            scope, symbols = scanner_symbols_for_scope(conn, user["id"], requested_scope)
            user_strategy_settings = load_strategy_settings(conn, user["id"])
        if not symbols:
            self.end_json(200, {"scope": scope, "signalMode": requested_mode, "results": [], "topOpportunities": [], "errors": [], "serverTime": int(time.time() * 1000)})
            return

        raw_results, errors = scan_symbols(symbols, user_strategy_settings)
        results = scanner_decision_results(raw_results, requested_mode)
        qualified = [item for item in results if item.get("passesFilter")]
        directional = [item for item in qualified if item["finalSignal"] in {"BUY", "SELL"}]
        top_source = directional or qualified
        top_opportunities = sorted(top_source, key=lambda item: (item.get("bestScore") or 0, item.get("finalScore") or 0), reverse=True)[:10]
        with db_connect() as conn:
            auto_payload = auto_trading_stats(conn, user["id"])
        self.end_json(
            200,
            {
                "scope": scope,
                "signalMode": requested_mode,
                "results": results,
                "rawResults": raw_results,
                "topOpportunities": top_opportunities,
                "errors": errors,
                "autoTrading": {"settings": auto_payload, "actions": [], "skipped": [], "logs": auto_payload.get("logs", [])},
                "serverTime": int(time.time() * 1000),
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

    def handle_auto_trading_settings(self, parsed):
        user = self.require_user()
        if not user:
            return
        settings = clean_auto_settings_payload(self.read_json())
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO auto_trading_settings
                        (user_id, enabled, stopped, stop_reason, position_pct, max_positions, max_daily_loss_pct,
                         max_total_drawdown_pct, cooldown_hours, allow_add_position, scan_scope, signal_mode, scheduler_status,
                         timeframe, scan_interval_minutes, stop_loss_pct, take_profit_pct, trailing_stop_pct,
                         max_holding_days, max_portfolio_exposure_pct, max_crypto_exposure_pct, max_daily_orders, kill_switch,
                         quality_mode, quality_min_score, quality_min_sharpe, quality_min_return_pct,
                         quality_max_drawdown_pct, quality_min_trade_count,
                         enabled_at, updated_at)
                    VALUES (%s, %s, false, '', %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s,
                            CASE WHEN %s THEN now() ELSE NULL END, now())
                    ON CONFLICT (user_id)
                    DO UPDATE SET
                        enabled = EXCLUDED.enabled,
                        stopped = CASE WHEN EXCLUDED.enabled THEN false ELSE auto_trading_settings.stopped END,
                        stop_reason = CASE WHEN EXCLUDED.enabled THEN '' ELSE auto_trading_settings.stop_reason END,
                        position_pct = EXCLUDED.position_pct,
                        max_positions = EXCLUDED.max_positions,
                        max_daily_loss_pct = EXCLUDED.max_daily_loss_pct,
                        max_total_drawdown_pct = EXCLUDED.max_total_drawdown_pct,
                        cooldown_hours = EXCLUDED.cooldown_hours,
                        allow_add_position = EXCLUDED.allow_add_position,
                        scan_scope = EXCLUDED.scan_scope,
                        signal_mode = EXCLUDED.signal_mode,
                        scheduler_status = EXCLUDED.scheduler_status,
                        timeframe = EXCLUDED.timeframe,
                        scan_interval_minutes = EXCLUDED.scan_interval_minutes,
                        stop_loss_pct = EXCLUDED.stop_loss_pct,
                        take_profit_pct = EXCLUDED.take_profit_pct,
                        trailing_stop_pct = EXCLUDED.trailing_stop_pct,
                        max_holding_days = EXCLUDED.max_holding_days,
                        max_portfolio_exposure_pct = EXCLUDED.max_portfolio_exposure_pct,
                        max_crypto_exposure_pct = EXCLUDED.max_crypto_exposure_pct,
                        max_daily_orders = EXCLUDED.max_daily_orders,
                        kill_switch = EXCLUDED.kill_switch,
                        quality_mode = EXCLUDED.quality_mode,
                        quality_min_score = EXCLUDED.quality_min_score,
                        quality_min_sharpe = EXCLUDED.quality_min_sharpe,
                        quality_min_return_pct = EXCLUDED.quality_min_return_pct,
                        quality_max_drawdown_pct = EXCLUDED.quality_max_drawdown_pct,
                        quality_min_trade_count = EXCLUDED.quality_min_trade_count,
                        enabled_at = CASE
                            WHEN EXCLUDED.enabled AND NOT auto_trading_settings.enabled THEN now()
                            WHEN EXCLUDED.enabled THEN auto_trading_settings.enabled_at
                            ELSE NULL
                        END,
                        updated_at = now()
                    """,
                    (
                        user["id"],
                        settings["enabled"],
                        Decimal(str(settings["positionPct"])),
                        settings["maxPositions"],
                        Decimal(str(settings["maxDailyLossPct"])),
                        Decimal(str(settings["maxTotalDrawdownPct"])),
                        Decimal(str(settings["cooldownHours"])),
                        settings["allowAddPosition"],
                        settings["scanScope"],
                        settings["signalMode"],
                        "running" if settings["enabled"] else "disabled",
                        settings["timeframe"],
                        settings["scanIntervalMinutes"],
                        Decimal(str(settings["stopLossPct"])),
                        Decimal(str(settings["takeProfitPct"])),
                        Decimal(str(settings["trailingStopPct"])),
                        settings["maxHoldingDays"],
                        Decimal(str(settings["maxPortfolioExposurePct"])),
                        Decimal(str(settings["maxCryptoExposurePct"])),
                        settings["maxDailyOrders"],
                        settings["killSwitch"],
                        settings["qualityMode"],
                        Decimal(str(settings["qualityMinScore"])),
                        Decimal(str(settings["qualityMinSharpe"])),
                        Decimal(str(settings["qualityMinReturnPct"])),
                        Decimal(str(settings["qualityMaxDrawdownPct"])),
                        settings["qualityMinTradeCount"],
                        settings["enabled"],
                    ),
                )
            conn.commit()
            payload = auto_trading_stats(conn, user["id"])
        self.end_json(200, {"autoTrading": payload, "state": self.load_state(user["id"])})

    def handle_auto_trading_run(self, parsed):
        user = self.require_user()
        if not user:
            return
        result = run_auto_trading_cycle(user["id"], triggered_by="manual")
        self.end_json(200, {"autoTrading": result, "state": self.load_state(user["id"])})

    def handle_strategy_settings(self, parsed):
        user = self.require_user()
        if not user:
            return
        settings = clean_user_strategy_settings(self.read_json())
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO strategy_settings
                        (user_id, ma_fast, ma_slow, macd_fast, macd_slow, macd_signal,
                         rsi_period, rsi_buy_threshold, rsi_sell_threshold, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                    ON CONFLICT (user_id)
                    DO UPDATE SET
                        ma_fast = EXCLUDED.ma_fast,
                        ma_slow = EXCLUDED.ma_slow,
                        macd_fast = EXCLUDED.macd_fast,
                        macd_slow = EXCLUDED.macd_slow,
                        macd_signal = EXCLUDED.macd_signal,
                        rsi_period = EXCLUDED.rsi_period,
                        rsi_buy_threshold = EXCLUDED.rsi_buy_threshold,
                        rsi_sell_threshold = EXCLUDED.rsi_sell_threshold,
                        updated_at = now()
                    """,
                    (
                        user["id"],
                        settings["maFast"],
                        settings["maSlow"],
                        settings["macdFast"],
                        settings["macdSlow"],
                        settings["macdSignal"],
                        settings["rsiPeriod"],
                        Decimal(str(settings["rsiBuyThreshold"])),
                        Decimal(str(settings["rsiSellThreshold"])),
                    ),
                )
            conn.commit()
        self.end_json(200, {"state": self.load_state(user["id"])})

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
        training_ratio = clean_training_ratio(data.get("trainingRatio"))
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
            result["walkForward"] = run_walk_forward_validation(strategy_name, symbol, points, params, training_ratio)
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

    def handle_portfolio_backtest(self, parsed):
        user = self.require_user()
        if not user:
            return
        data = self.read_json()
        symbols = parse_portfolio_symbols(data.get("symbols") or data.get("symbolsText") or "")
        range_key = data.get("range") or "1y"
        start_date = parse_date_picker(data.get("startDate"))
        end_date = parse_date_picker(data.get("endDate"))
        weighting_mode = "custom" if data.get("weightingMode") == "custom" else "equal"
        if len(symbols) < 2:
            self.end_json(400, {"error": "Select at least two assets for portfolio backtest."})
            return
        if range_key not in BACKTEST_RANGES:
            self.end_json(400, {"error": "Invalid backtest range."})
            return
        weights = parse_portfolio_weights(symbols, data.get("weights"), weighting_mode)
        started = time.perf_counter()
        try:
            histories = {
                symbol: normalize_backtest_history(symbol, range_key, start_date, end_date)
                for symbol in symbols
            }
            result = run_portfolio_backtest(symbols, weights, histories)
        except (HTTPError, URLError, TimeoutError, ValueError) as error:
            self.end_json(502, {"error": f"Portfolio backtest failed: {error}"})
            return
        result["runtimeMs"] = int((time.perf_counter() - started) * 1000)
        result["weightingMode"] = weighting_mode
        self.end_json(200, {"portfolioBacktest": result})

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
        try:
            with db_connect() as conn:
                fill = execute_paper_trade(conn, user["id"], symbol, side, qty, execution_source="manual")
                conn.commit()
        except ValueError as error:
            self.end_json(400, {"error": str(error)})
            return
        except (HTTPError, URLError, TimeoutError) as error:
            self.end_json(502, {"error": f"Trade failed: {error}"})
            return
        self.end_json(
            200,
            {
                "state": self.load_state(user["id"]),
                "fill": {
                    "symbol": fill["symbol"],
                    "side": fill["side"],
                    "qty": fill["qty"],
                    "price": fill["price"],
                    "value": fill["value"],
                    "currency": fill["currency"],
                },
            },
        )

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
        query_data = parse_qs(parsed.query)
        query = (query_data.get("q") or [""])[0].strip()
        market = (query_data.get("market") or ["stock"])[0].strip()
        if not query:
            self.end_json(400, {"error": "Missing search query"})
            return
        if market == "crypto":
            normalized = query.upper().replace("/", "")
            results = [
                {"symbol": symbol, "name": f"{symbol[:-4]}/USDT", "exchange": "Binance", "type": "CRYPTO"}
                for symbol in CRYPTO_SYMBOLS
                if normalized in symbol or symbol.startswith(normalized)
            ]
            if is_crypto_symbol(normalized) and all(item["symbol"] != normalized for item in results):
                results.insert(0, {"symbol": normalized, "name": f"{normalized[:-4]}/USDT", "exchange": "Binance", "type": "CRYPTO"})
            self.end_json(200, {"results": results})
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
    try:
        database_initialized = init_db()
    except Exception as error:
        database_initialized = False
        print(f"Warning: database initialization failed; serving app with database APIs degraded: {error}", flush=True)
    if database_initialized:
        start_equity_snapshot_worker()
        start_auto_trading_scheduler()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Real-market paper trading server: http://{HOST}:{PORT}/")
    server.serve_forever()
