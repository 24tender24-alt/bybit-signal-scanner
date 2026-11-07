import os
import time
import logging
import requests
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

COOLDOWN_MINUTES = 60
BASE_URL = "https://api.bybit.com"

last_alert = {}

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("scanner")


def api_get(path, params=None):
    response = requests.get(
        BASE_URL + path,
        params=params,
        timeout=20
    )
    response.raise_for_status()
    data = response.json()

    if data.get("retCode") != 0:
        raise RuntimeError(data.get("retMsg", "Bybit API error"))

    return data["result"]


def get_universe():
    result = api_get("/v5/market/tickers", {
        "category": "linear"
    })

    coins = [
        item for item in result["list"]
        if item["symbol"].endswith("USDT")
        and float(item.get("turnover24h", 0)) >= 10_000_000
    ]

    coins.sort(
        key=lambda x: float(x["turnover24h"]),
        reverse=True
    )

    return [x["symbol"] for x in coins[:30]]


def candles(symbol, interval, limit):
    result = api_get("/v5/market/kline", {
        "category": "linear",
        "symbol": symbol,
        "interval": interval,
        "limit": limit
    })

    rows = list(reversed(result["list"]))

    return [{
        "open": float(x[1]),
        "high": float(x[2]),
        "low": float(x[3]),
        "close": float(x[4]),
        "volume": float(x[5])
    } for x in rows]


def ema(values, period):
    if len(values) < period:
        return None

    result = sum(values[:period]) / period
    alpha = 2 / (period + 1)

    for value in values[period:]:
        result = value * alpha + result * (1 - alpha)

    return result


def rsi(values, period=14):
    if len(values) <= period:
        return None

    changes = [
        values[i] - values[i - 1]
        for i in range(1, len(values))
    ]

    gains = [max(x, 0) for x in changes]
    losses = [max(-x, 0) for x in changes]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(changes)):
        avg_gain = (
            avg_gain * (period - 1) + gains[i]
        ) / period
        avg_loss = (
            avg_loss * (period - 1) + losses[i]
        ) / period

    if avg_loss == 0:
        return 100

    return 100 - 100 / (1 + avg_gain / avg_loss)


def atr(data, period=14):
    if len(data) <= period:
        return None

    trs = []

    for i in range(1, len(data)):
        high = data[i]["high"]
        low = data[i]["low"]
        prev = data[i - 1]["close"]

        trs.append(max(
            high - low,
            abs(high - prev),
            abs(low - prev)
        ))

    return sum(trs[-period:]) / period


def analyze(symbol):
    htf = candles(symbol, "15", 210)
    mtf = candles(symbol, "5", 80)
    ltf = candles(symbol, "1", 40)

    hclose = [x["close"] for x in htf]
    mclose = [x["close"] for x in mtf]

    e20 = ema(hclose, 20)
    e50 = ema(hclose, 50)
    e200 = ema(hclose, 200)

    m20 = ema(mclose, 20)
    m50 = ema(mclose, 50)

    current = ltf[-1]
    previous = ltf[-21:-1]

    if not previous:
        return None

    volume_avg = sum(x["volume"] for x in previous) / len(previous)
    current_atr = atr(mtf)

    if not current_atr or current_atr <= 0:
        return None

    current_rsi = rsi(mclose)

    fast = ema(mclose, 12)
    slow = ema(mclose, 26)

    macd_line = fast - slow
    signal_line = ema(
        [ema(mclose[:i + 1], 12) -
         ema(mclose[:i + 1], 26)
         for i in range(25, len(mclose))],
        9
    )

    if signal_line is None:
        return None

    macd_positive = macd_line > signal_line
    macd_negative = macd_line < signal_line

    breakout_high = max(x["high"] for x in previous)
    breakout_low = min(x["low"] for x in previous)

    volume_ok = current["volume"] > volume_avg * 1.5

    long_ok = (
        e20 > e50 > e200
        and current["close"] > e20
        and m20 > m50
        and 50 < current_rsi < 70
        and macd_positive
        and current["close"] > breakout_high
        and volume_ok
    )

    short_ok = (
        e20 < e50 < e200
        and current["close"] < e20
        and m20 < m50
        and 30 < current_rsi < 50
        and macd_negative
        and current["close"] < breakout_low
        and volume_ok
    )

    if not long_ok and not short_ok:
        return None

    side = "LONG" if long_ok else "SHORT"
    entry = current["close"]
    distance = current_atr * 1.5

    stop = entry - distance if long_ok else entry + distance

    targets = (
        [entry + distance * x for x in (2, 3, 4)]
        if long_ok else
        [entry - distance * x for x in (2, 3, 4)]
    )

    return {
        "symbol": symbol,
        "side": side,
        "entry": entry,
        "stop": stop,
        "targets": targets,
        "rsi": current_rsi
    }


def format_signal(data):
    return (
        f"🚨 BYBIT SIGNAL — {data['side']}\n"
        f"Монета: {data['symbol']}\n"
        f"Вход: {data['entry']:.8g}\n"
        f"Stop Loss: {data['stop']:.8g}\n"
        f"TP1 (2R): {data['targets'][0]:.8g}\n"
        f"TP2 (3R): {data['targets'][1]:.8g}\n"
        f"TP3 (4R): {data['targets'][2]:.8g}\n"
        f"RSI: {data['rsi']:.1f}\n"
        f"Плечо для справки: 10x\n"
        f"⚠️ Сигнал не является финансовой рекомендацией."
    )


def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    response = requests.post(
        url,
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message
        },
        timeout=20
    )

    response.raise_for_status()

    if not response.json().get("ok"):
        raise RuntimeError("Telegram delivery failed")


if __name__ == "__main__":
    for symbol in get_universe():
        try:
            signal = analyze(symbol)

            if signal:
                key = (symbol, signal["side"])
                now = time.time()

                if now - last_alert.get(key, 0) >= COOLDOWN_MINUTES * 60:
                    message = format_signal(signal)
                    print(message)

                    if not DRY_RUN:
                        send_telegram(message)

                    last_alert[key] = now

            time.sleep(0.12)

        except Exception:
            log.exception("Error analyzing %s", symbol)
