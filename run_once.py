import json
import logging
import time
from pathlib import Path

import main as scanner

STATE_FILE = Path(".scanner_state.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger("bybit-actions-run")


def load_state():
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        log.warning("Could not read saved cooldown state; starting fresh.")
        return {}


def run_once():
    if not scanner.TELEGRAM_TOKEN or not scanner.TELEGRAM_CHAT_ID:
        raise RuntimeError("Missing Telegram GitHub Actions secrets.")

    saved = load_state()
    for key, value in saved.items():
        if "|" in key:
            symbol, side = key.split("|", 1)
            scanner.last_alert[(symbol, side)] = float(value)

    symbols = scanner.get_universe()
    log.info("Scanning %s symbols", len(symbols))

    for symbol in symbols:
        try:
            signal_data = scanner.analyze(symbol)
            if not signal_data:
                continue

            key = (symbol, signal_data["side"])
            now = time.time()

            if now - scanner.last_alert.get(key, 0) < scanner.COOLDOWN_MINUTES * 60:
                continue

            message = scanner.format_signal(signal_data)
            log.info("%s", message.replace("\n", " | "))

            if not scanner.DRY_RUN:
                scanner.send_telegram(message)

            scanner.last_alert[key] = now

        except Exception:
            log.exception("Failed to analyze %s", symbol)

        time.sleep(0.12)

    state = {
        f"{symbol}|{side}": stamp
        for (symbol, side), stamp in scanner.last_alert.items()
    }
    STATE_FILE.write_text(
        json.dumps(state, indent=2),
        encoding="utf-8"
    )
    log.info("One scan completed.")


if __name__ == "__main__":
    run_once()
