from datetime import datetime, timezone


def generate_signal(symbol, market_data):
    params = market_data.get("params") or {}
    period = int(params.get("period") or 14)
    oversold = float(params.get("oversold") or 30)
    overbought = float(params.get("overbought") or 70)
    history = market_data.get("history") or []
    closes = [float(point["p"]) for point in history if point.get("p") is not None]
    price = closes[-1] if closes else market_data.get("price") or 0
    if period <= 1:
        reason = "RSI period must be greater than 1."
        rsi_value = None
    elif len(closes) <= period:
        reason = f"Need at least {period + 1} price points for RSI analysis."
        rsi_value = None
    else:
        gains = []
        losses = []
        for previous, current in zip(closes[-period - 1:-1], closes[-period:]):
            change = current - previous
            gains.append(max(change, 0))
            losses.append(abs(min(change, 0)))
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        rsi_value = 100 if avg_loss == 0 else 100 - (100 / (1 + (avg_gain / avg_loss)))
        reason = f"RSI {rsi_value:.2f}, oversold {oversold:g}, overbought {overbought:g}."
    signal = "HOLD"
    if rsi_value is not None and rsi_value <= oversold:
        signal = "BUY"
    elif rsi_value is not None and rsi_value >= overbought:
        signal = "SELL"
    return {
        "signal": signal,
        "reason": reason,
        "price": price,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
