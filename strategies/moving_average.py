from datetime import datetime, timezone


def generate_signal(symbol, market_data):
    return {
        "signal": "HOLD",
        "reason": "Moving average strategy framework is ready; logic is not enabled yet.",
        "price": market_data.get("price") or 0,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
