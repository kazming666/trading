from datetime import datetime, timezone


def ema(values, period):
    if not values:
        return []
    multiplier = 2 / (period + 1)
    result = [values[0]]
    for value in values[1:]:
        result.append((value - result[-1]) * multiplier + result[-1])
    return result


def generate_signal(symbol, market_data):
    params = market_data.get("params") or {}
    fast = int(params.get("fast") or 12)
    slow = int(params.get("slow") or 26)
    signal_period = int(params.get("signal") or 9)
    history = market_data.get("history") or []
    closes = [float(point["p"]) for point in history if point.get("p") is not None]
    price = closes[-1] if closes else market_data.get("price") or 0
    if fast <= 0 or slow <= 0 or signal_period <= 0 or fast >= slow:
        return {
            "signal": "HOLD",
            "reason": "MACD parameters require Fast > 0, Signal > 0, and Fast < Slow.",
            "price": price,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    if len(closes) < slow + signal_period:
        return {
            "signal": "HOLD",
            "reason": f"Need at least {slow + signal_period} price points for MACD analysis.",
            "price": price,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    fast_ema = ema(closes, fast)
    slow_ema = ema(closes, slow)
    macd_line = [fast_value - slow_value for fast_value, slow_value in zip(fast_ema, slow_ema)]
    signal_line = ema(macd_line, signal_period)
    macd_now = macd_line[-1]
    signal_now = signal_line[-1]
    macd_previous = macd_line[-2]
    signal_previous = signal_line[-2]
    signal = "HOLD"
    if macd_previous <= signal_previous and macd_now > signal_now:
        signal = "BUY"
    elif macd_previous >= signal_previous and macd_now < signal_now:
        signal = "SELL"
    return {
        "signal": signal,
        "reason": f"MACD {macd_now:.4f}, Signal {signal_now:.4f}.",
        "price": price,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
