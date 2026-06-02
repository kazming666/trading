from datetime import datetime, timezone

from strategies import macd, moving_average, rsi


STRATEGIES = {
    "moving_average": moving_average,
    "rsi": rsi,
    "macd": macd,
}


def available_strategies():
    return sorted(STRATEGIES.keys())


def normalize_signal(signal, symbol, market_data):
    price = market_data.get("price") or 0
    normalized = {
        "signal": str(signal.get("signal") or "HOLD").upper(),
        "reason": str(signal.get("reason") or "No strategy reason provided."),
        "price": float(signal.get("price") or price or 0),
        "timestamp": signal.get("timestamp") or datetime.now(timezone.utc).isoformat(),
    }
    if normalized["signal"] not in {"BUY", "SELL", "HOLD"}:
        normalized["signal"] = "HOLD"
        normalized["reason"] = f"{symbol}: invalid signal normalized to HOLD."
    return normalized


def generate_signal(strategy_name, symbol, market_data):
    strategy = STRATEGIES.get(strategy_name)
    if not strategy:
        raise ValueError(f"Unknown strategy: {strategy_name}")
    return normalize_signal(strategy.generate_signal(symbol, market_data), symbol, market_data)
