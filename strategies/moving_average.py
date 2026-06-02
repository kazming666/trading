from datetime import datetime, timezone


def generate_signal(symbol, market_data):
    params = market_data.get("params") or {}
    fast = int(params.get("fastMa") or 5)
    slow = int(params.get("slowMa") or 20)
    history = market_data.get("history") or []
    closes = [float(point["p"]) for point in history if point.get("p") is not None]
    price = closes[-1] if closes else market_data.get("price") or 0
    if fast <= 0 or slow <= 0 or fast >= slow:
        return {
            "signal": "HOLD",
            "reason": "Moving Average parameters require Fast MA > 0 and Fast MA < Slow MA.",
            "price": price,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    if len(closes) < slow:
        return {
            "signal": "HOLD",
            "reason": f"Need at least {slow} price points for MA analysis.",
            "price": price,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    fast_ma = sum(closes[-fast:]) / fast
    slow_ma = sum(closes[-slow:]) / slow
    previous_fast = sum(closes[-fast - 1:-1]) / fast if len(closes) > fast else fast_ma
    previous_slow = sum(closes[-slow - 1:-1]) / slow if len(closes) > slow else slow_ma
    signal = "HOLD"
    if previous_fast <= previous_slow and fast_ma > slow_ma:
        signal = "BUY"
    elif previous_fast >= previous_slow and fast_ma < slow_ma:
        signal = "SELL"
    return {
        "signal": signal,
        "reason": f"Fast MA {fast_ma:.2f}, Slow MA {slow_ma:.2f}.",
        "price": price,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
