"""
main.py
========
Об'єднує модулі: data_collector -> analyzer -> telegram_notifier ->
trading212_executor. Останній — опціональний: якщо TRADING212_API_KEY не
задано, увесь торговий блок просто пропускається і бот працює як і раніше
(тільки сигнали/дашборд, без жодної реальної торгівлі).

За замовчуванням робить ОДНУ перевірку і виходить — це розраховано на
запуск за розкладом (GitHub Actions cron, звичний crontab і т.д.), а не
на вічний процес. Рекомендовано: раз на 30-60 хв.

Налаштування через змінні середовища:
    TICKER                 — один тікер або кілька через кому, напр.
                              "SB=F,GC=F,BTC-USD,^GSPC,AAPL" (default "SB=F")
    INTERVAL               — таймфрейм свічок, напр. "1h" (default "1h"),
                              застосовується до всіх тікерів однаково
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID — див. telegram_notifier.py

    --- Автоматична торгівля (Trading 212), все опціональне ---
    TRADING212_API_KEY     — якщо не задано, торгівля вимкнена повністю
    TRADING212_MODE        — "demo" (віртуальний рахунок, РЕКОМЕНДОВАНО
                              для старту) або "live" (реальні гроші)
    TRADING212_DRY_RUN     — "true" (default) — лише рахує й логує, що б
                              зробив бот, жодних реальних ордерів.
                              "false" — ордери йдуть насправді (у demo чи
                              live — залежно від TRADING212_MODE).
    MAX_PER_TRADE_GBP      — максимум на одну угоду, default 20
    MAX_DAILY_GBP          — максимум нових покупок на добу, default 200
    STOP_LOSS_PCT          — автопродаж при падінні від ціни купівлі
                              на N%, default 7

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
from datetime import datetime, timezone
from pathlib import Path

import data_collector
import analyzer
import strategy
import telegram_notifier
import dashboard_builder
import fundamentals
import trading212_executor as t212
from watchlist import TRADING_UNIVERSE

def _env(name: str, default: str) -> str:
    # GitHub Actions підставляє ПОРОЖНІЙ РЯДОК для незаданої vars.X (не
    # відсутню змінну) — os.environ.get(name, default) сам по собі цього
    # не ловить, тому явно підставляємо дефолт і на порожній рядок теж.
    val = os.environ.get(name, "")
    return val.strip() if val.strip() else default


TICKERS = [t.strip() for t in _env("TICKER", "SB=F").split(",") if t.strip()]
INTERVAL = _env("INTERVAL", "1h")

TRADING_ENABLED = t212.is_configured()
MAX_PER_TRADE_GBP = float(_env("MAX_PER_TRADE_GBP", "20"))
MAX_DAILY_GBP = float(_env("MAX_DAILY_GBP", "200"))
STOP_LOSS_PCT = float(_env("STOP_LOSS_PCT", "7"))
MIN_TRADE_GBP = 1.0  # не морочитись з угодами менше £1

TRADING_STATE_FILE = Path(__file__).parent / "trading_state.json"
TRADE_LOG_MAX = 50


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


def load_trading_state() -> dict:
    state = load_state(TRADING_STATE_FILE)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if state.get("date") != today:
        state["date"] = today
        state["daily_spent_gbp"] = 0.0
    state.setdefault("positions", {})
    state.setdefault("trade_log", [])
    return state


def log_trade(trading_state: dict, **entry) -> None:
    entry["time"] = datetime.now(timezone.utc).isoformat()
    trading_state["trade_log"].append(entry)
    trading_state["trade_log"] = trading_state["trade_log"][-TRADE_LOG_MAX:]


def _execute_sell(ticker: str, t212_ticker: str, qty: float, price: float, reason: str, trading_state: dict) -> None:
    order = t212.place_market_order(t212_ticker, -qty)
    log_trade(
        trading_state, ticker=ticker, action="SELL", reason=reason,
        qty=qty, price=price, ok=order["ok"],
        dry_run=order.get("dry_run", False), detail=order["detail"],
    )
    if order["ok"]:
        del trading_state["positions"][ticker]
        print(f"[trading212] {ticker}: ПРОДАНО ({reason}) — {order['detail']}")
    else:
        print(f"[trading212] {ticker}: продаж НЕ вдався — {order['detail']}")


def _execute_buy(ticker: str, t212_ticker: str, price: float, reason: str, trading_state: dict) -> None:
    remaining_budget = MAX_DAILY_GBP - trading_state["daily_spent_gbp"]
    trade_value = min(MAX_PER_TRADE_GBP, remaining_budget)
    if trade_value < MIN_TRADE_GBP:
        return  # денний ліміт вичерпано — мовчки пропускаємо (не варто засмічувати лог на 100 тікерів)

    qty = round(trade_value / price, 4)
    if qty <= 0:
        return

    order = t212.place_market_order(t212_ticker, qty)
    log_trade(
        trading_state, ticker=ticker, action="BUY", reason=reason,
        qty=qty, price=price, value_gbp=round(trade_value, 2), ok=order["ok"],
        dry_run=order.get("dry_run", False), detail=order["detail"],
    )
    if order["ok"]:
        trading_state["positions"][ticker] = {
            "qty": qty, "buy_price": price, "t212_ticker": t212_ticker,
            "opened": datetime.now(timezone.utc).isoformat(),
        }
        trading_state["daily_spent_gbp"] = round(trading_state["daily_spent_gbp"] + trade_value, 2)
        print(f"[trading212] {ticker}: КУПЛЕНО ({reason}) — {order['detail']}")
    else:
        print(f"[trading212] {ticker}: купівля НЕ вдалася — {order['detail']}")


def run_trading_scan(trading_state: dict) -> None:
    """Сканує ввесь TRADING_UNIVERSE (watchlist.py) ОДНИМ пакетним
    запитом до Yahoo Finance, рахує посилену стратегію (strategy.py:
    RSI + SMA200-тренд + MACD + Bollinger, збіг мінімум 2 з 3 сигналів) і
    виконує купівлю/продаж там, де є сигнал. Окремо від дашбордних
    тікерів (TICKER) — ті лишаються для огляду/сповіщень, як і раніше."""
    print(f"[trading212] Сканую {len(TRADING_UNIVERSE)} акцій на наявність сигналів...")
    batch = data_collector.fetch_candles_batch(TRADING_UNIVERSE, interval=INTERVAL)
    # Мультитаймфрейм-фільтр: окремий пакетний запит ЩОДЕННИХ свічок, щоб
    # не купувати проти загального (денного) тренду, навіть якщо годинний
    # сигнал виглядає привабливо. Один зайвий пакетний запит на весь
    # список — дешево порівняно з кількістю хибних сигналів, яких уникаємо.
    daily_batch = data_collector.fetch_candles_batch(TRADING_UNIVERSE, interval="1d", period="2y")

    scanned, signals_found = 0, 0
    for ticker, df in batch.items():
        if df is None or len(df) < 210:  # замало історії для SMA200
            continue

        position = trading_state["positions"].get(ticker)
        t212_ticker = position["t212_ticker"] if position else t212.find_instrument_ticker(ticker)
        if not t212_ticker:
            continue
        scanned += 1

        try:
            dfi = strategy.add_all_indicators(df)
            i = len(dfi) - 1
            price = float(dfi["close"].iloc[i])

            if position:
                stop_price = position["buy_price"] * (1 - STOP_LOSS_PCT / 100)
                if price <= stop_price:
                    _execute_sell(ticker, t212_ticker, position["qty"], price, "STOP_LOSS", trading_state)
                    signals_found += 1
                    continue
                action, reason = strategy.new_strategy_signal(dfi, i, True)
                if action == "SELL":
                    _execute_sell(ticker, t212_ticker, position["qty"], price, reason, trading_state)
                    signals_found += 1
            else:
                action, reason = strategy.new_strategy_signal(dfi, i, False)
                if action == "BUY":
                    daily_df = daily_batch.get(ticker)
                    if not strategy.daily_trend_bullish(daily_df):
                        continue  # годинний сигнал є, але денний тренд не підтверджує — пропускаємо
                    _execute_buy(ticker, t212_ticker, price, f"{reason}+DAILY_TREND_UP", trading_state)
                    signals_found += 1
        except Exception as e:
            print(f"[trading212] Помилка аналізу {ticker}: {e}", file=sys.stderr)

    print(f"[trading212] Проскановано {scanned} акцій, оброблено сигналів: {signals_found}.")


def run_check() -> None:
    tabs = []
    trading_state = load_trading_state() if TRADING_ENABLED else None

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

    if TRADING_ENABLED:
        try:
            run_trading_scan(trading_state)
        except Exception as e:
            print(f"[trading212] Помилка сканування: {e}", file=sys.stderr)
        save_state(TRADING_STATE_FILE, trading_state)

    if tabs:
        trading_summary = _build_trading_summary(trading_state) if TRADING_ENABLED else None
        dashboard_builder.build_multi(tabs, INTERVAL, trading_summary=trading_summary)
    else:
        print("[main] Жодного з тікерів не вдалося опрацювати — дашборд не оновлено.", file=sys.stderr)


def _build_trading_summary(trading_state: dict) -> dict:
    return {
        "mode": t212.MODE,
        "dry_run": t212.DRY_RUN,
        "daily_spent_gbp": trading_state["daily_spent_gbp"],
        "max_daily_gbp": MAX_DAILY_GBP,
        "max_per_trade_gbp": MAX_PER_TRADE_GBP,
        "stop_loss_pct": STOP_LOSS_PCT,
        "positions": trading_state["positions"],
        "trade_log": list(reversed(trading_state["trade_log"][-15:])),
    }


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
