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
    STOP_LOSS_PCT          — ТРЕЙЛІНГ стоп-лос: автопродаж при падінні на
                              N% від НАЙВИЩОЇ ціни з моменту купівлі (не
                              від ціни купівлі) — фіксує частину прибутку,
                              якщо ціна встигла вирости. default 7
    MAX_OPEN_POSITIONS     — макс. кількість одночасно відкритих позицій
                              (щоб не "розпорошуватись"), default 10
    MAX_POSITIONS_PER_SECTOR — макс. позицій в одному секторі одночасно
                              (диверсифікація — див. watchlist.py), default 3
    AVOID_EARNINGS_DAYS    — не купувати, якщо звітність компанії
                              очікується протягом N днів (різкий
                              непередбачуваний стрибок ціни), default 3
    DRAWDOWN_PAUSE_GBP     — "запобіжник": якщо сукупний збиток з початку
                              (сума по закритих угодах) падає на цю суму
                              від максимуму — нові купівлі ставляться на
                              паузу (продажі й стоп-лоси далі працюють),
                              поки збиток не скоротиться. default 0
                              (вимкнено; 0 = без обмеження)

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

import pandas as pd

import data_collector
import analyzer
import strategy
import telegram_notifier
import dashboard_builder
import fundamentals
import trading212_executor as t212
from watchlist import TRADING_UNIVERSE, sector_of

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
# Старі описові сповіщення про індикатори (RSI, Bollinger тощо) по тікерах
# дашборда. За замовчуванням ВИМКНЕНІ: в Telegram приходять лише повідомлення
# про віртуальні/реальні угоди. Щоб повернути — змінна INDICATOR_ALERTS=true.
INDICATOR_ALERTS = _env("INDICATOR_ALERTS", "false").lower() == "true"
# Віртуальні ШОРТИ (ставка на падіння). Працюють ЛИШЕ коли TRADING212_DRY_RUN
# не "false": в Trading 212 Invest/ISA шортів немає, тож реальні ордери на
# шорт бот не відправляє ніколи. За замовчуванням ВИМКНЕНО (бектест не показав переваги). Увімкнути: ENABLE_SHORTS=true.
ENABLE_SHORTS = _env("ENABLE_SHORTS", "false").lower() == "true"
MAX_OPEN_POSITIONS = int(float(_env("MAX_OPEN_POSITIONS", "10")))
MAX_POSITIONS_PER_SECTOR = int(float(_env("MAX_POSITIONS_PER_SECTOR", "3")))
AVOID_EARNINGS_DAYS = int(float(_env("AVOID_EARNINGS_DAYS", "3")))
DRAWDOWN_PAUSE_GBP = float(_env("DRAWDOWN_PAUSE_GBP", "0"))
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
    state.setdefault("cumulative_pnl_gbp", 0.0)
    state.setdefault("peak_pnl_gbp", 0.0)
    return state


def _is_paused_for_drawdown(trading_state: dict) -> bool:
    if DRAWDOWN_PAUSE_GBP <= 0:
        return False
    drawdown = trading_state["peak_pnl_gbp"] - trading_state["cumulative_pnl_gbp"]
    return drawdown >= DRAWDOWN_PAUSE_GBP


def log_trade(trading_state: dict, **entry) -> None:
    entry["time"] = datetime.now(timezone.utc).isoformat()
    trading_state["trade_log"].append(entry)
    trading_state["trade_log"] = trading_state["trade_log"][-TRADE_LOG_MAX:]


def _is_uk(ticker: str) -> bool:
    """Акція Лондонської біржі (формат Yahoo Finance: суфікс .L)."""
    return ticker.upper().endswith(".L")


def _cur(ticker: str) -> str:
    return "£" if _is_uk(ticker) else "$"


def _uk_pence_to_pounds(df):
    """Yahoo віддає ціни акцій LSE в пенсах (GBX). Переводимо у фунти, щоб
    розмір угоди, стопи й результат рахувались в одних одиницях з рештою."""
    out = df.copy()
    for col in ("open", "high", "low", "close"):
        if col in out.columns:
            out[col] = out[col] * 0.01
    return out


REASON_TEXT = {
    "TRAILING_STOP_LOSS": "спрацював трейлінг стоп-лос",
    "RSI_OVERBOUGHT": "RSI вище 70 (перекупленість)",
    "NEAR_UPPER_BB": "ціна біля верхньої межі Bollinger",
    "TREND_BREAK_SMA50": "злам тренду відносно SMA50",
    "RSI_OVERSOLD": "RSI нижче 30 (перепроданість)",
    "NEAR_LOWER_BB": "ціна біля нижньої межі Bollinger",
}


def _mode_label() -> str:
    return "🧪 ВІРТУАЛЬНО (dry-run)" if t212.DRY_RUN else f"💷 РЕАЛЬНО ({t212.MODE})"


def _notify_open(ticker: str, price: float, qty: float, value_gbp: float,
                 reason: str, stop_price: float, levels: dict, side: str = "LONG") -> None:
    """Повідомлення в Telegram про відкриття позиції: вхід, стоп, цілі виходу."""
    try:
        c = _cur(ticker)
        if side == "SHORT":
            lines = [
                f"🔻 <b>ШОРТ {ticker}</b> ({sector_of(ticker)}) — ставка на падіння",
                _mode_label() + " · шорти лише віртуально",
                "",
                f"Вхід: <b>{c}{price:.2f}</b> × {qty:.4f} шт (≈ £{value_gbp:.0f})",
                f"🛑 Початковий стоп-лос: <b>{c}{stop_price:.2f}</b> (+{STOP_LOSS_PCT:g}% вище входу)",
                f"   Стоп трейлінговий: опускається за ціною на {STOP_LOSS_PCT:g}% вище мінімуму.",
            ]
            bb = levels.get("bb_lower")
            sma50 = levels.get("sma50")
            if bb:
                gain = (1 - bb / price) * 100
                lines.append(f"🎯 Орієнтир виходу (нижня межа Bollinger): <b>{c}{bb:.2f}</b> (+{gain:.1f}% прибутку)")
            if sma50:
                lines.append(f"⚠️ Вихід при зламі тренду: ціна вище SMA50 <b>{c}{sma50:.2f}</b>")
            lines.append("📉 Також вихід при RSI &lt; 30.")
            risk = stop_price - price
            if bb and risk > 0 and bb < price:
                lines.append(f"Співвідношення прибуток/ризик ≈ {(price - bb) / risk:.1f} : 1")
        else:
            lines = [
                f"🟢 <b>КУПІВЛЯ {ticker}</b> ({sector_of(ticker)})",
                _mode_label(),
                "",
                f"Вхід: <b>{c}{price:.2f}</b> × {qty:.4f} шт (≈ £{value_gbp:.0f})",
                f"🛑 Початковий стоп-лос: <b>{c}{stop_price:.2f}</b> (−{STOP_LOSS_PCT:g}%)",
                f"   Стоп трейлінговий: підтягується за ціною на {STOP_LOSS_PCT:g}% нижче піку.",
            ]
            bb = levels.get("bb_upper")
            sma50 = levels.get("sma50")
            if bb:
                gain = (bb / price - 1) * 100
                lines.append(f"🎯 Орієнтир виходу (верхня межа Bollinger): <b>{c}{bb:.2f}</b> (+{gain:.1f}%)")
            if sma50:
                lines.append(f"⚠️ Вихід при зламі тренду: ціна нижче SMA50 <b>{c}{sma50:.2f}</b>")
            lines.append("📈 Також вихід при RSI &gt; 70.")
            risk = price - stop_price
            if bb and risk > 0 and bb > price:
                lines.append(f"Співвідношення прибуток/ризик ≈ {(bb - price) / risk:.1f} : 1")
        lines.append("")
        lines.append(f"Причина входу: {reason}")
        telegram_notifier.send_alert("\n".join(lines))
    except Exception as e:
        print(f"[telegram] Не вдалося надіслати повідомлення про відкриття: {e}", file=sys.stderr)


def _notify_close(ticker: str, price: float, position: dict, reason: str, cumulative: float) -> None:
    """Повідомлення в Telegram про закриття позиції: результат угоди."""
    try:
        c = _cur(ticker)
        side = position.get("side", "LONG")
        buy_price = position.get("buy_price") or price
        qty = position.get("qty", 0)
        if side == "SHORT":
            pnl = (buy_price - price) * qty
            pnl_pct = (1 - price / buy_price) * 100
            extreme_txt = f"Мінімум за час позиції: {c}{position.get('trough_price', buy_price):.2f}"
            title = f"ЗАКРИТО ШОРТ {ticker}"
        else:
            pnl = (price - buy_price) * qty
            pnl_pct = (price / buy_price - 1) * 100
            extreme_txt = f"Максимум за час позиції: {c}{position.get('peak_price', buy_price):.2f}"
            title = f"ПРОДАЖ {ticker}"
        icon = "✅" if pnl >= 0 else "🔴"
        opened = position.get("opened", "")[:16].replace("T", " ")
        reason_txt = REASON_TEXT.get(reason, reason)
        lines = [
            f"{icon} <b>{title}</b>",
            _mode_label(),
            "",
            f"Вхід: {c}{buy_price:.2f} → Вихід: <b>{c}{price:.2f}</b> ({pnl_pct:+.1f}%)",
            f"Результат угоди: <b>{pnl:+.2f}</b> (кількість {qty:.4f})",
            extreme_txt,
            f"Причина: {reason_txt}",
        ]
        if opened:
            lines.append(f"Відкрито: {opened} UTC")
        lines.append(f"Сукупний результат: {cumulative:+.2f}")
        telegram_notifier.send_alert("\n".join(lines))
    except Exception as e:
        print(f"[telegram] Не вдалося надіслати повідомлення про продаж: {e}", file=sys.stderr)


def _execute_sell(ticker: str, t212_ticker: str, qty: float, price: float, reason: str, trading_state: dict) -> None:
    position = trading_state["positions"].get(ticker, {})
    order = t212.place_market_order(t212_ticker, -qty)
    log_trade(
        trading_state, ticker=ticker, action="SELL", reason=reason,
        qty=qty, price=price, ok=order["ok"],
        dry_run=order.get("dry_run", False), detail=order["detail"],
    )
    if order["ok"]:
        buy_price = position.get("buy_price")
        if buy_price:
            pnl_gbp = (price - buy_price) * qty
            trading_state["cumulative_pnl_gbp"] = round(trading_state["cumulative_pnl_gbp"] + pnl_gbp, 2)
            trading_state["peak_pnl_gbp"] = max(trading_state["peak_pnl_gbp"], trading_state["cumulative_pnl_gbp"])
        _notify_close(ticker, price, position, reason, trading_state["cumulative_pnl_gbp"])
        del trading_state["positions"][ticker]
        print(f"[trading212] {ticker}: ПРОДАНО ({reason}) — {order['detail']}")
    else:
        print(f"[trading212] {ticker}: продаж НЕ вдався — {order['detail']}")


def _execute_cover(ticker: str, price: float, reason: str, trading_state: dict) -> None:
    """Закриття віртуального шорту. Реальних ордерів не існує (лише dry-run)."""
    position = trading_state["positions"].get(ticker, {})
    qty = position.get("qty", 0)
    log_trade(
        trading_state, ticker=ticker, action="COVER", reason=reason,
        qty=qty, price=price, ok=True, dry_run=True, detail="(virtual short) COVER",
    )
    entry = position.get("buy_price")
    if entry:
        pnl = (entry - price) * qty
        trading_state["cumulative_pnl_gbp"] = round(trading_state["cumulative_pnl_gbp"] + pnl, 2)
        trading_state["peak_pnl_gbp"] = max(trading_state["peak_pnl_gbp"], trading_state["cumulative_pnl_gbp"])
    _notify_close(ticker, price, position, reason, trading_state["cumulative_pnl_gbp"])
    trading_state["positions"].pop(ticker, None)
    print(f"[trading212] {ticker}: ШОРТ ЗАКРИТО ({reason}) — віртуально")


def _execute_short(ticker: str, price: float, reason: str, trading_state: dict, levels: dict) -> None:
    """Відкриття віртуального шорту. Реальних ордерів НЕ відправляє ніколи."""
    remaining_budget = MAX_DAILY_GBP - trading_state["daily_spent_gbp"]
    trade_value = min(MAX_PER_TRADE_GBP, remaining_budget)
    if trade_value < MIN_TRADE_GBP:
        return
    qty = round(trade_value / price, 4)
    if qty <= 0:
        return
    log_trade(
        trading_state, ticker=ticker, action="SHORT", reason=reason,
        qty=qty, price=price, value_gbp=round(trade_value, 2), ok=True,
        dry_run=True, detail="(virtual short) SHORT",
    )
    trading_state["positions"][ticker] = {
        "side": "SHORT", "qty": qty, "buy_price": price, "trough_price": price,
        "peak_price": price, "t212_ticker": "", "opened": datetime.now(timezone.utc).isoformat(),
    }
    trading_state["daily_spent_gbp"] = round(trading_state["daily_spent_gbp"] + trade_value, 2)
    _notify_open(ticker, price, qty, trade_value, reason,
                 price * (1 + STOP_LOSS_PCT / 100), levels, side="SHORT")
    print(f"[trading212] {ticker}: ШОРТ ВІДКРИТО ({reason}) — віртуально")


def _execute_buy(ticker: str, t212_ticker: str, price: float, reason: str, trading_state: dict,
                 levels: dict | None = None) -> None:
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
            "qty": qty, "buy_price": price, "peak_price": price, "t212_ticker": t212_ticker,
            "opened": datetime.now(timezone.utc).isoformat(),
        }
        trading_state["daily_spent_gbp"] = round(trading_state["daily_spent_gbp"] + trade_value, 2)
        _notify_open(ticker, price, qty, trade_value, reason,
                     price * (1 - STOP_LOSS_PCT / 100), levels or {})
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

    paused = _is_paused_for_drawdown(trading_state)
    if paused:
        print(f"[trading212] ПАУЗА нових купівель: сукупний збиток досяг £{DRAWDOWN_PAUSE_GBP:.0f} "
              f"від максимуму (поточний: £{trading_state['cumulative_pnl_gbp']:.2f}, "
              f"пік: £{trading_state['peak_pnl_gbp']:.2f}). Продажі й стоп-лоси продовжують працювати.")

    open_positions_count = len(trading_state["positions"])
    sector_counts = {}
    for t, pos in trading_state["positions"].items():
        sec = sector_of(t)
        sector_counts[sec] = sector_counts.get(sec, 0) + 1

    scanned, signals_found = 0, 0
    for ticker, df in batch.items():
        if df is None or len(df) < 210:  # замало історії для SMA200
            continue

        position = trading_state["positions"].get(ticker)
        if _is_uk(ticker):
            if not t212.DRY_RUN:
                continue  # реальних ордерів по акціях LSE бот не робить — лише віртуально
            t212_ticker = ticker + "_UK_VIRTUAL"
            df = _uk_pence_to_pounds(df)
        else:
            t212_ticker = (position.get("t212_ticker") if position else None) or t212.find_instrument_ticker(ticker)
        if not t212_ticker:
            continue
        scanned += 1
        shorts_active = ENABLE_SHORTS and t212.DRY_RUN  # шорти лише віртуально

        try:
            dfi = strategy.add_all_indicators(df)
            i = len(dfi) - 1
            price = float(dfi["close"].iloc[i])
            last = dfi.iloc[i]

            if position and position.get("side") == "SHORT":
                # Трейлінг-стоп шорту: рахується від найнижчої ціни з моменту
                # входу, стоп знаходиться на STOP_LOSS_PCT% ВИЩЕ мінімуму.
                position["trough_price"] = min(position.get("trough_price", position["buy_price"]), price)
                stop_price = position["trough_price"] * (1 + STOP_LOSS_PCT / 100)
                if price >= stop_price:
                    _execute_cover(ticker, price, "TRAILING_STOP_LOSS", trading_state)
                    signals_found += 1
                    continue
                action, reason = strategy.short_strategy_signal(dfi, i, True)
                if action == "COVER":
                    _execute_cover(ticker, price, reason, trading_state)
                    signals_found += 1
            elif position:
                # Трейлінг стоп-лос: рахуємо від найвищої ціни з моменту
                # купівлі, а не від самої ціни купівлі — фіксує частину
                # прибутку, якщо ціна встигла вирости, перш ніж впасти.
                position["peak_price"] = max(position.get("peak_price", position["buy_price"]), price)
                stop_price = position["peak_price"] * (1 - STOP_LOSS_PCT / 100)
                if price <= stop_price:
                    _execute_sell(ticker, t212_ticker, position["qty"], price, "TRAILING_STOP_LOSS", trading_state)
                    signals_found += 1
                    continue
                action, reason = strategy.new_strategy_signal(dfi, i, True)
                if action == "SELL":
                    _execute_sell(ticker, t212_ticker, position["qty"], price, reason, trading_state)
                    signals_found += 1
            elif not paused:
                if open_positions_count >= MAX_OPEN_POSITIONS:
                    continue  # досягнуто ліміту одночасних позицій
                sector = sector_of(ticker)
                if sector_counts.get(sector, 0) >= MAX_POSITIONS_PER_SECTOR:
                    continue  # досягнуто ліміту позицій у цьому секторі (диверсифікація)

                action, reason = strategy.new_strategy_signal(dfi, i, False)
                if action == "BUY":
                    daily_df = daily_batch.get(ticker)
                    if not strategy.daily_trend_bullish(daily_df):
                        continue  # годинний сигнал є, але денний тренд не підтверджує — пропускаємо
                    if AVOID_EARNINGS_DAYS > 0 and _earnings_too_soon(ticker, AVOID_EARNINGS_DAYS):
                        continue  # скоро звітність — надто непередбачувано
                    levels = {
                        "bb_upper": None if pd.isna(last["bb_upper"]) else float(last["bb_upper"]),
                        "sma50": None if pd.isna(last["sma50"]) else float(last["sma50"]),
                    }
                    _execute_buy(ticker, t212_ticker, price, f"{reason}+DAILY_TREND_UP", trading_state, levels)
                    open_positions_count += 1
                    sector_counts[sector] = sector_counts.get(sector, 0) + 1
                    signals_found += 1
                elif shorts_active:
                    s_action, s_reason = strategy.short_strategy_signal(dfi, i, False)
                    if s_action == "SHORT":
                        daily_df = daily_batch.get(ticker)
                        if not strategy.daily_trend_bearish(daily_df):
                            continue  # денний тренд не низхідний — не шортимо
                        if AVOID_EARNINGS_DAYS > 0 and _earnings_too_soon(ticker, AVOID_EARNINGS_DAYS):
                            continue
                        levels = {
                            "bb_lower": None if pd.isna(last["bb_lower"]) else float(last["bb_lower"]),
                            "sma50": None if pd.isna(last["sma50"]) else float(last["sma50"]),
                        }
                        _execute_short(ticker, price, f"{s_reason}+DAILY_TREND_DOWN", trading_state, levels)
                        open_positions_count += 1
                        sector_counts[sector] = sector_counts.get(sector, 0) + 1
                        signals_found += 1
        except Exception as e:
            print(f"[trading212] Помилка аналізу {ticker}: {e}", file=sys.stderr)

    print(f"[trading212] Проскановано {scanned} акцій, оброблено сигналів: {signals_found}.")


def _earnings_too_soon(ticker: str, days: int) -> bool:
    """True, якщо звітність компанії очікується протягом найближчих
    `days` днів — тоді краще не відкривати нову позицію (непередбачувані
    різкі стрибки ціни на новинах, які жоден технічний індикатор не
    бачить наперед). Викликається лише для тікерів, що ВЖЕ пройшли всі
    інші фільтри (рідко), тож не навантажує Yahoo Finance на кожному скані."""
    try:
        events = fundamentals.get_upcoming_events(ticker)
    except Exception:
        return False  # немає даних — не блокуємо через це
    for ev in events:
        if ev.get("type") != "EARNINGS":
            continue
        try:
            ev_date = datetime.fromisoformat(ev["date"][:19]).replace(tzinfo=timezone.utc) \
                if "T" in ev["date"] else datetime.strptime(ev["date"][:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except (ValueError, KeyError):
            continue
        days_until = (ev_date - datetime.now(timezone.utc)).days
        if 0 <= days_until <= days:
            return True
    return False


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
            if INDICATOR_ALERTS:
                telegram_notifier.send_alert("\n".join(lines))
            dashboard_builder.append_alerts_log(sent, ticker)
            print(f"[{ticker}] Нових відміток індикаторів: {len(sent)} "
                  f"({'надіслано в Telegram' if INDICATOR_ALERTS else 'лише в журнал дашборда'}).")
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
        "open_positions_count": len(trading_state["positions"]),
        "max_open_positions": MAX_OPEN_POSITIONS,
        "cumulative_pnl_gbp": trading_state["cumulative_pnl_gbp"],
        "paused_for_drawdown": _is_paused_for_drawdown(trading_state),
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
