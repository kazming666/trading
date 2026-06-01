from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse, quote
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import time


HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8765"))
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
            "User-Agent": "Mozilla/5.0 PaperTradingDesk/1.0",
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
        if digits.startswith(("5", "6", "9")):
            candidates = [f"{digits}.SS", f"{digits}.SZ"]
        else:
            candidates = [f"{digits}.SZ", f"{digits}.SS"]
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
        "price": price,
        "previousClose": previous_close,
        "regularMarketTime": meta.get("regularMarketTime"),
        "marketState": meta.get("marketState") or "",
    }


def normalize_history(symbol, range_key):
    range_value, interval = HISTORY_RANGES.get(range_key, HISTORY_RANGES["1d"])
    chart = yahoo_chart(symbol, range_value, interval)
    timestamps = chart.get("timestamp") or []
    quote = ((chart.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    points = [
        {"t": ts * 1000, "p": close}
        for ts, close in zip(timestamps, closes)
        if close is not None
    ]
    if len(points) < 2 and range_key == "1d":
        chart = yahoo_chart(symbol, "5d", "5m")
        timestamps = chart.get("timestamp") or []
        quote = ((chart.get("indicators") or {}).get("quote") or [{}])[0]
        closes = quote.get("close") or []
        points = [
            {"t": ts * 1000, "p": close}
            for ts, close in zip(timestamps, closes)
            if close is not None
        ]
    if not points:
        raise ValueError("No historical prices returned")
    return {"symbol": symbol.upper(), "range": range_key, "points": points[-360:]}


class Handler(SimpleHTTPRequestHandler):
    def end_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/search":
            self.handle_search(parsed)
            return
        if parsed.path == "/api/quote":
            self.handle_quote(parsed)
            return
        if parsed.path == "/api/history":
            self.handle_history(parsed)
            return
        super().do_GET()

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
                quotes.append(
                    {
                        "symbol": symbol,
                        "name": item.get("longname") or item.get("shortname") or symbol,
                        "exchange": item.get("exchDisp") or item.get("exchange") or "",
                        "type": quote_type,
                    }
                )
            for symbol in candidate_symbols(query):
                if any(item["symbol"].upper() == symbol.upper() for item in quotes):
                    continue
                try:
                    item = normalize_quote(symbol)
                    quotes.insert(
                        0,
                        {
                            "symbol": item["symbol"],
                            "name": item["name"],
                            "exchange": item["exchange"],
                            "type": "EQUITY",
                        },
                    )
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
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Real-market paper trading server: http://{HOST}:{PORT}/")
    server.serve_forever()
