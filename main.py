"""
main.py
========
Об'єднує модулі: data_collector -> analyzer -> telegram_notifier.

За замовчуванням робить ОДНУ перевірку і виходить — це розраховано на
запуск за розкладом (GitHub Actions cron, звичний crontab і т.д.), а не
на вічний процес. Рекомендовано: раз на 30-60 хв.

Чому не кожні 5 хв:
- На безкоштовному GitHub Actions (private repo) є ліміт ~2000 хв/місяць.
  Перевірка кожні 5 хв = 288 запусків/день — ліміт скінчиться за кілька днів.
- Ринкові дані на таймфреймі 1h чи 1d не змінюються настільки швидко, щоб
  це мало сенс; часті сповіщення радше заохочують імпульсивні рішення,
  ніж допомагають.
Таймфрейм свічок (5m/1h) і частота ПЕРЕВІРКИ — різні речі: можна
аналізувати 5-мінутні свічки для деталізації, перевіряючи раз на 30 хв.

Налаштування через змінні середовища:
    TICKER                 — напр. "SB=F", "BTC-USD", "EURUSD=X" (default "SB=F")
    INTERVAL               — таймфрейм свічок, напр. "1h" (default "1h")
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID — див. telegram_notifier.py

Запуск:
    python main.py                       # одна перевірка (для cron/Actions)
    python main.py --loop                # безперервний цикл (для власного сервера)
    python main.py --loop --interval-minutes 30
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import data_collector
import analyzer
import telegram_notifier
import dashboard_builder
import fundamentals

TICKER = os.environ.get("TICKER", "SB=F")
INTERVAL = os.environ.get("INTERVAL", "1h")

STATE_FILE = Path(__file__).parent / f"monitor_state_{TICKER.replace('=', '_').replace('/', '_')}.json"


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def run_check() -> None:
    df = data_collector.fetch_candles(TICKER, interval=INTERVAL)
    result = analyzer.analyze(df, label=TICKER)

    state = load_state()
    sent = []

    for ev in result["events"]:
        # ключ для дедуплікації: тип події + округлена ціна, щоб не слати
        # те саме повідомлення щоразу, поки умова лишається істинною
        key = f"{ev['type']}_{round(ev['price'], 2)}"
        if not state.get(key):
            sent.append(ev)
            state[key] = True

    if sent:
        lines = [f"📊 <b>{TICKER}</b> ({INTERVAL})"]
        for ev in sent:
            lines.append(f"• {ev['type']}: {ev['detail']}")
        lines.append(f"\nПоточна ціна: {result['price']:.4f} | RSI: {result['rsi']:.1f}")
        lines.append("\n<i>Описові відмітки індикаторів, не торгові рекомендації.</i>")
        telegram_notifier.send_alert("\n".join(lines))
        dashboard_builder.append_alerts_log(sent, TICKER)
        print(f"Надіслано {len(sent)} нове(их) сповіщення(ь).")
    else:
        print(f"Ціна={result['price']:.4f}, RSI={result['rsi']:.1f}. Нічого нового — сповіщень не надіслано.")

    save_state(state)

    # Фундаментальний погляд (окремо від технічного) — None для
    # крипти/форексу/фʼючерсів, де немає корпоративної звітності.
    fair_value = fundamentals.get_fair_value(TICKER)
    upcoming_events = fundamentals.get_upcoming_events(TICKER)

    dashboard_builder.build(df, result, TICKER, INTERVAL, fair_value=fair_value, upcoming_events=upcoming_events)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--loop", action="store_true", help="Безперервний цикл замість одноразової перевірки.")
    parser.add_argument("--interval-minutes", type=int, default=30, help="Пауза між перевірками в режимі --loop.")
    args = parser.parse_args()

    if args.loop:
        print(f"Цикл запущено: перевірка кожні {args.interval_minutes} хв. Ctrl+C для зупинки.")
        while True:
            try:
                run_check()
            except Exception as e:
                print(f"[main] Помилка перевірки: {e}", file=sys.stderr)
            time.sleep(args.interval_minutes * 60)
    else:
        run_check()


if __name__ == "__main__":
    main()
