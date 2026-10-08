"""
backtest.py
============
Перевіряє ОБИДВІ стратегії (стару — RSI+рівні, і нову — RSI+SMA200
тренд-фільтр+MACD+Bollinger Bands з вимогою збігу сигналів) на реальній
історії цін, та порівнює з "купив і тримав" (buy & hold) того самого
періоду. Нічого не торгує насправді — це лише аналіз "а що було б, якби".

Чому окремо від main.py:
    - Бектест працює на щоденних свічках за 2-5 років (для статистичної
      ваги), тоді як живий бот — на годинних за ~2 місяці. Це наближення,
      не точна копія поведінки бота, але достатнє, щоб чесно порівняти
      ЛОГІКУ двох стратегій між собою і з пасивним тримання.
    - Запускається вручну (workflow_dispatch), не за розкладом — це
      дослідницький інструмент, не частина регулярного моніторингу.

Результат пишеться в docs/backtest.html (відкривається в браузері так само,
як основний дашборд) + короткий підсумок друкується в лог запуску.

Налаштування через змінні середовища:
    BACKTEST_TICKERS   — через кому, default "AAPL,MSFT,NVDA,TSLA"
    BACKTEST_YEARS     — скільки років історії, default 3
    MAX_PER_TRADE_GBP  — розмір однієї угоди (та сама логіка, що й у
                          живому боті), default 20
    STOP_LOSS_PCT      — default 7
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

import strategy


def _env(name: str, default: str) -> str:
    # GitHub Actions підставляє ПОРОЖНІЙ РЯДОК для незаданої vars.X (не
    # відсутню змінну) — тому os.environ.get(name, default) сам по собі
    # не рятує. Явно підставляємо дефолт і на порожній рядок теж.
    val = os.environ.get(name, "")
    return val.strip() if val.strip() else default


TICKERS = [t.strip() for t in _env("BACKTEST_TICKERS", "AAPL,MSFT,NVDA,TSLA").split(",") if t.strip()]
YEARS = int(_env("BACKTEST_YEARS", "3"))
TRADE_VALUE = float(_env("MAX_PER_TRADE_GBP", "20"))
STOP_LOSS_PCT = float(_env("STOP_LOSS_PCT", "7"))
# Trading 212 Invest/ISA не бере комісію за акції США, але спред
# (різниця між ціною купівлі й продажу) все одно "з'їдає" трохи — типово
# ~0.15% в одну сторону. За замовчуванням рахуємо консервативно.
COMMISSION_PCT = float(_env("BACKTEST_COMMISSION_PCT", "0.15"))

DOCS_DIR = Path(__file__).parent / "docs"
MIN_WARMUP_BARS = 200  # SMA200 потребує мінімум стільки барів історії


def fetch_history(ticker: str, years: int) -> pd.DataFrame:
    df = yf.download(ticker, period=f"{years}y", interval="1d", progress=False, auto_adjust=True)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.reset_index().rename(columns={
        "Date": "timestamp", "Open": "open", "High": "high", "Low": "low",
        "Close": "close", "Volume": "volume",
    })
    return df[["timestamp", "open", "high", "low", "close", "volume"]].dropna(subset=["close"]).reset_index(drop=True)


def simulate(df: pd.DataFrame, signal_fn, trade_value: float, stop_loss_pct: float,
             commission_pct: float = 0.0) -> dict:
    """Проходить по барах один раз (без зазирання вперед — на кожному
    кроці рахуємо сигнал лише з даних ДО цього бара включно) і симулює
    угоди з фіксованим розміром `trade_value` на вхід. Стоп-лос —
    ТРЕЙЛІНГ (від найвищої ціни з моменту купівлі, не від ціни купівлі),
    так само як у живому боті. `commission_pct` — умовна комісія/спред
    в % від суми угоди, знімається і при купівлі, і при продажу."""
    trades = []
    position = None  # {"buy_price", "peak_price", "qty", "entry_i"}
    commission_frac = commission_pct / 100

    def _close(exit_i: int, price: float, reason: str) -> None:
        gross_pnl = (price - position["buy_price"]) * position["qty"]
        commission = (position["buy_price"] + price) * position["qty"] * commission_frac
        pnl = gross_pnl - commission
        trades.append({
            "entry_i": position["entry_i"], "exit_i": exit_i, "pnl": pnl,
            "pnl_pct": (pnl / (position["buy_price"] * position["qty"])) * 100, "reason": reason,
        })

    n = len(df)
    for i in range(MIN_WARMUP_BARS, n):
        price = float(df["close"].iloc[i])

        if position is not None:
            position["peak_price"] = max(position["peak_price"], price)
            stop_price = position["peak_price"] * (1 - stop_loss_pct / 100)
            if price <= stop_price:
                _close(i, price, "TRAILING_STOP_LOSS")
                position = None
                continue

            action, reason = signal_fn(df, i, True)
            if action == "SELL":
                _close(i, price, reason)
                position = None
        else:
            action, reason = signal_fn(df, i, False)
            if action == "BUY":
                qty = trade_value / price
                position = {"buy_price": price, "peak_price": price, "qty": qty, "entry_i": i}

    # якщо позиція лишилась відкритою в кінці періоду — закриваємо по
    # останній ціні, щоб статистика не ігнорувала "завислу" угоду
    if position is not None:
        price = float(df["close"].iloc[-1])
        _close(n - 1, price, "END_OF_PERIOD")

    return _summarize(trades, trade_value)


def _summarize(trades: list, trade_value: float) -> dict:
    if not trades:
        return {
            "num_trades": 0, "win_rate": None, "total_pnl": 0.0, "total_invested": 0.0,
            "total_pnl_pct": 0.0, "max_drawdown_pct": 0.0, "avg_win": None, "avg_loss": None,
            "best_trade_pct": None, "worst_trade_pct": None, "trades": [],
        }

    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    total_invested = trade_value * len(trades)
    total_pnl = sum(pnls)

    equity_curve = np.cumsum(pnls)
    running_max = np.maximum.accumulate(equity_curve)
    drawdowns = np.where(running_max != 0, (equity_curve - running_max), 0)
    max_dd_abs = float(drawdowns.min()) if len(drawdowns) else 0.0
    max_drawdown_pct = (max_dd_abs / trade_value) * 100 if trade_value else 0.0

    pnl_pcts = [t["pnl_pct"] for t in trades]

    return {
        "num_trades": len(trades),
        "win_rate": round(len(wins) / len(trades) * 100, 1),
        "total_pnl": round(total_pnl, 2),
        "total_invested": round(total_invested, 2),
        "total_pnl_pct": round(total_pnl / total_invested * 100, 2) if total_invested else 0.0,
        "max_drawdown_pct": round(max_drawdown_pct, 2),
        "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
        "best_trade_pct": round(max(pnl_pcts), 2),
        "worst_trade_pct": round(min(pnl_pcts), 2),
        "trades": trades,
    }


def run_all() -> dict:
    results = {}
    for ticker in TICKERS:
        print(f"[backtest] Завантажую історію для {ticker} ({YEARS} роки)...")
        try:
            raw = fetch_history(ticker, YEARS)
        except Exception as e:
            print(f"[backtest] Пропускаю {ticker}: {e}")
            continue
        if len(raw) < MIN_WARMUP_BARS + 20:
            print(f"[backtest] {ticker}: замало історії ({len(raw)} барів), пропускаю.")
            continue

        df = strategy.add_all_indicators(raw)

        old_result = simulate(df, strategy.old_strategy_signal, TRADE_VALUE, STOP_LOSS_PCT, COMMISSION_PCT)
        new_result = simulate(df, strategy.new_strategy_signal, TRADE_VALUE, STOP_LOSS_PCT, COMMISSION_PCT)

        buy_hold_pct = (float(df["close"].iloc[-1]) / float(df["close"].iloc[MIN_WARMUP_BARS]) - 1) * 100

        results[ticker] = {
            "old": old_result, "new": new_result, "buy_hold_pct": round(buy_hold_pct, 2),
            "period_start": str(df["timestamp"].iloc[MIN_WARMUP_BARS])[:10],
            "period_end": str(df["timestamp"].iloc[-1])[:10],
        }
        print(f"[backtest] {ticker}: OLD {old_result['num_trades']} угод, "
              f"{old_result['total_pnl_pct']}% | NEW {new_result['num_trades']} угод, "
              f"{new_result['total_pnl_pct']}% | Buy&Hold {buy_hold_pct:.1f}%")

    return results


def build_report_html(results: dict) -> str:
    rows = ""
    for ticker, r in results.items():
        for label, key in (("Стара (RSI+рівні)", "old"), ("Нова (RSI+тренд+MACD+BB)", "new")):
            res = r[key]
            pnl_class = "bull" if res["total_pnl_pct"] > 0 else ("bear" if res["total_pnl_pct"] < 0 else "neutral")
            rows += f"""
            <tr>
              <td class="mono">{ticker}</td>
              <td>{label}</td>
              <td class="mono">{res['num_trades']}</td>
              <td class="mono">{res['win_rate'] if res['win_rate'] is not None else '—'}%</td>
              <td class="mono {pnl_class}">{res['total_pnl_pct']:+.2f}%</td>
              <td class="mono">{res['max_drawdown_pct']:.2f}%</td>
            </tr>"""
        rows += f"""
            <tr class="benchmark-row">
              <td class="mono">{ticker}</td>
              <td>Купив і тримав (benchmark)</td>
              <td class="mono">1</td>
              <td class="mono">—</td>
              <td class="mono {'bull' if r['buy_hold_pct'] > 0 else 'bear'}">{r['buy_hold_pct']:+.2f}%</td>
              <td class="mono">—</td>
            </tr>"""

    periods = ", ".join(f"{t}: {r['period_start']} → {r['period_end']}" for t, r in results.items())
    updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    return f"""<!DOCTYPE html>
<html lang="uk">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Бектест стратегій — Market Monitor</title>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600;700&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root{{ --bg:#11151B; --panel:#161B22; --line:#262D38; --text:#E7E9EC; --muted:#8A92A0;
          --bull:#2FA86E; --bear:#C1452F; --neutral:#D9A62E; }}
  *{{box-sizing:border-box;}}
  body{{margin:0; background:var(--bg); color:var(--text); font-family:'IBM Plex Sans',sans-serif;}}
  .mono{{font-family:'IBM Plex Mono',monospace;}}
  .app{{max-width:860px; margin:0 auto; padding:16px 16px 60px;}}
  h1{{font-size:18px; margin:0 0 4px;}}
  .updated{{font-size:11.5px; color:var(--muted); margin-bottom:4px;}}
  .warn{{background:#2E2718; border:1px solid #4A3F22; color:#E6CE8F; border-radius:6px; padding:12px 14px; font-size:12.5px; line-height:1.5; margin:14px 0;}}
  table{{width:100%; border-collapse:collapse; margin-top:14px; font-size:12.5px;}}
  th, td{{padding:8px 6px; text-align:left; border-bottom:1px solid var(--line);}}
  th{{color:var(--muted); font-weight:600; font-size:11px;}}
  .bull{{color:var(--bull);}} .bear{{color:var(--bear);}} .neutral{{color:var(--neutral);}}
  .benchmark-row{{opacity:0.7; font-style:italic;}}
  .period{{font-size:11.5px; color:var(--muted); margin-top:10px;}}
</style>
</head>
<body>
<div class="app">
  <h1>Бектест: стара vs нова стратегія vs купив-і-тримай</h1>
  <div class="updated mono">Оновлено: {updated}</div>
  <div class="warn">
    Це перевірка ЛОГІКИ на минулих цінах, не гарантія майбутнього результату.
    Бектест рахує на ЩОДЕННИХ свічках (для достатньої історії), тоді як живий
    бот працює на ГОДИННИХ — це наближення, не точна копія. Умовна
    комісія/спред {COMMISSION_PCT:.2f}% врахована в розрахунку (і при купівлі,
    і при продажу) — реальний результат все одно може трохи відрізнятись.
  </div>
  <table>
    <tr><th>Тікер</th><th>Стратегія</th><th>Угод</th><th>Win rate</th><th>Прибуток</th><th>Макс. просадка</th></tr>
    {rows}
  </table>
  <div class="period">Період: {periods}</div>
</div>
</body>
</html>
"""


def main():
    DOCS_DIR.mkdir(exist_ok=True)
    results = run_all()
    if not results:
        print("[backtest] Жодного тікера не вдалось обробити.")
        return
    html = build_report_html(results)
    (DOCS_DIR / "backtest.html").write_text(html, encoding="utf-8")
    (DOCS_DIR / "backtest_results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2, default=str)
    )
    print("\n[backtest] Готово. Звіт: docs/backtest.html")


if __name__ == "__main__":
    main()
