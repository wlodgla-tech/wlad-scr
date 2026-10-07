"""
main.py
========
Об'єднує модулі: data_collector -> analyzer -> telegram_notifier.

За замовчуванням робить ОДНУ перевірку і виходить — це розраховано на
запуск за розкладом (GitHub Actions cron, звичний crontab і т.д.), а не
на вічний процес. Рекомендовано: раз на 30-60 хв.

Налаштування через змінні середовища:
    TICKER                 — один тікер або кілька через кому, напр.
                              "SB=F,GC=F,BTC-USD,^GSPC,AAPL" (default "SB=F")
    INTERVAL               — таймфрейм свічок, напр. "1h" (default "1h"),
                              застосовується до всіх тікерів однаково
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

TICKERS = [t.strip() for t in os.environ.get("TICKER", "SB=F").split(",") if t.strip()]
INTERVAL = os.environ.get("INTERVAL", "1h")


def state_file_for(ticker: str) -> Path:
    safe = ticker.replace("=", "_").replace("/", "_").replace("^", "_")
    return Path(__file__).parent / f"monitor_state_{safe}.json"


def load_state(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_state(path: Path, state: dict) -> None:
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def run_check() -> None:
    tabs = []

    for ticker in TICKERS:
        try:
            df = data_collector.fetch_candles(ticker, interval=INTERVAL)
        except Exception as e:
            print(f"[main] Пропускаю '{ticker}': {e}", file=sys.stderr)
            continue

        result = analyzer.analyze(df, label=ticker)

        state_path = state_file_for(ticker)
        state = load_state(state_path)
        sent = []

        for ev in result["events"]:
            key = f"{ev['type']}_{round(ev['price'], 2)}"
            if not state.get(key):
                sent.append(ev)
                state[key] = True

        if sent:
            lines = [f"📊 <b>{ticker}</b> ({INTERVAL})"]
            for ev in sent:
                lines.append(f"• {ev['type']}: {ev['detail']}")
            lines.append(f"\nПоточна ціна: {result['price']:.4f} | RSI: {result['rsi']:.1f}")
            lines.append("\n<i>Описові відмітки індикаторів, не торгові рекомендації.</i>")
            telegram_notifier.send_alert("\n".join(lines))
            dashboard_builder.append_alerts_log(sent, ticker)
            print(f"[{ticker}] Надіслано {len(sent)} нове(их) сповіщення(ь).")
        else:
            print(f"[{ticker}] Ціна={result['price']:.4f}, RSI={result['rsi']:.1f}. Нічого нового.")

        save_state(state_path, state)

        fair_value = fundamentals.get_fair_value(ticker)
        upcoming_events = fundamentals.get_upcoming_events(ticker)

        tabs.append({
            "ticker": ticker,
            "df": df,
            "result": result,
            "fair_value": fair_value,
            "upcoming_events": upcoming_events,
        })

    if tabs:
        dashboard_builder.build_multi(tabs, INTERVAL)
    else:
        print("[main] Жодного з тікерів не вдалося опрацювати — дашборд не оновлено.", file=sys.stderr)


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
