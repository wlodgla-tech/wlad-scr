"""
portfolio_backtest.py
======================
ПОРТФЕЛЬНИЙ бектест: усі акції разом, як у живому боті — ліміт позицій
(MAX_OPEN_POSITIONS), ліміт на сектор, фіксована ставка на угоду. На
відміну від звичайного бектесту (кожна акція окремо), тут видно реальну
просадку всього рахунку і чи допомагають: ранжування сигналів за силою,
фільтр ринку (S&P 500 вище SMA200), підтвердження обсягом, стоп за
волатильністю (ATR), різна ширина стопа.

Використовує вже завантажені дані з backtest.py (нічого не качає
повторно, крім індексу ^GSPC).
Припущення (як і в звичайному бектесті): сигнал і виконання — за ціною
закриття того ж дня; комісія/спред — з BACKTEST_COMMISSION_PCT на кожен бік.
"""

import numpy as np
import pandas as pd

import strategy

try:
    from watchlist import sector_of
except Exception:  # на крайній випадок
    def sector_of(t):
        return "Інше:" + t

WARMUP = 200
MOM_DAYS = 126          # ~6 місяців для ранжування сили тренду
VOL_MULT = 1.5          # обсяг при пробої має бути > 1.5 × середнього за 20 днів
ATR_MULT = 3.0          # стоп = 3 × ATR% (але в межах нижче)
ATR_MIN, ATR_MAX = 8.0, 30.0

# назва -> параметри
VARIANTS = [
    ("Базова (перші сигнали, стоп 20%)",            dict(rank=False, mkt=False, vol=False, atr=False, tstop=20)),
    ("+ ранжування за силою тренду",                dict(rank=True,  mkt=False, vol=False, atr=False, tstop=20)),
    ("+ ранжування + фільтр ринку",                 dict(rank=True,  mkt=True,  vol=False, atr=False, tstop=20)),
    ("+ ранжування + обсяг",                        dict(rank=True,  mkt=False, vol=True,  atr=False, tstop=20)),
    ("+ ранжування + ринок + обсяг",                dict(rank=True,  mkt=True,  vol=True,  atr=False, tstop=20)),
    ("+ ранжування + ринок + обсяг + ATR-стоп",     dict(rank=True,  mkt=True,  vol=True,  atr=True,  tstop=20)),
    ("Ранжування, стоп 10%",                        dict(rank=True,  mkt=False, vol=False, atr=False, tstop=10)),
    ("Ранжування, стоп 15%",                        dict(rank=True,  mkt=False, vol=False, atr=False, tstop=15)),
    ("Ранжування, стоп 25%",                        dict(rank=True,  mkt=False, vol=False, atr=False, tstop=25)),
    ("Ранжування, стоп 30%",                        dict(rank=True,  mkt=False, vol=False, atr=False, tstop=30)),
]


def _prepare(ticker: str, df: pd.DataFrame) -> dict:
    """Одноразово рахує для акції все, що потрібно портфельному циклу."""
    n = len(df)
    close = df["close"].astype(float).values
    hi, lo = df["high"].astype(float), df["low"].astype(float)
    pc = df["close"].astype(float).shift(1)
    tr = pd.concat([hi - lo, (hi - pc).abs(), (lo - pc).abs()], axis=1).max(axis=1)
    atr_pct = (tr.rolling(14).mean() / df["close"].astype(float) * 100).values
    mom = (df["close"].astype(float) / df["close"].astype(float).shift(MOM_DAYS) - 1).values * 100
    volx = (df["volume"].astype(float) / df["vol_sma20"].astype(float)).values

    entries = {}  # i -> is_trend (True/False) для барів, де є сигнал BUY з нуля
    for i in range(WARMUP, n):
        act, reason = strategy.regime_signal(df, i, False)
        if act == "BUY":
            entries[i] = str(reason).startswith("TREND")

    dates = pd.to_datetime(df["timestamp"]).dt.tz_localize(None).dt.normalize()
    return {
        "df": df, "close": close, "sma200": df["sma200"].astype(float).values,
        "atr_pct": atr_pct, "mom": mom, "volx": volx, "entries": entries,
        "date_to_i": {d: i for i, d in enumerate(dates)}, "sector": sector_of(ticker),
    }


def prepare_all(data: dict) -> dict:
    out = {}
    for t, df in data.items():
        try:
            out[t] = _prepare(t, df)
        except Exception as e:
            print(f"[portfolio] пропускаю {t}: {e}")
    return out


def _market_ok_series(index_df: pd.DataFrame, dates) -> pd.Series:
    s = index_df.set_index(pd.to_datetime(index_df["timestamp"]).dt.tz_localize(None).dt.normalize())["close"].astype(float)
    s = s[~s.index.duplicated(keep="last")]
    ok = (s > s.rolling(200).mean())
    return ok.reindex(dates, method="ffill").fillna(False)


def run_variant(prep: dict, dates, market_ok: pd.Series, params: dict, max_pos: int, sector_cap: int,
                trade_value: float, stop_loss_pct: float, commission_pct: float) -> dict:
    comm = commission_pct / 100
    capital = max_pos * trade_value
    positions = {}   # ticker -> dict
    closed = []
    realized = 0.0
    equity_curve = []
    slots_used = []
    last_price = {}

    for d in dates:
        # 1) виходи і оновлення відкритих позицій
        for t in list(positions):
            p = prep[t]
            i = p["date_to_i"].get(d)
            if i is None:
                continue
            pos = positions[t]
            price = float(p["close"][i])
            last_price[t] = price
            pos["peak"] = max(pos["peak"], price)
            reason = None
            if price <= pos["peak"] * (1 - pos["stop"] / 100):
                reason = "STOP"
            elif pos["trend"]:
                s200 = p["sma200"][i]
                if not np.isnan(s200) and price < s200:
                    reason = "TREND_BREAK"
            else:
                act, _ = strategy.old_strategy_signal(p["df"], i, True)
                if act == "SELL":
                    reason = "REBOUND_EXIT"
            if reason:
                gross = (price / pos["buy"] - 1) * trade_value
                cost = trade_value * comm * (1 + price / pos["buy"])
                pnl = gross - cost
                realized += pnl
                closed.append(pnl / trade_value * 100)
                del positions[t]

        # 2) нові входи
        if len(positions) < max_pos:
            m_ok = bool(market_ok.get(d, False)) if params["mkt"] else True
            cands = []
            for t, p in prep.items():
                if t in positions:
                    continue
                i = p["date_to_i"].get(d)
                if i is None or i not in p["entries"]:
                    continue
                is_trend = p["entries"][i]
                if is_trend:
                    if params["mkt"] and not m_ok:
                        continue
                    if params["vol"]:
                        vx = p["volx"][i]
                        if np.isnan(vx) or vx < VOL_MULT:
                            continue
                score = p["mom"][i] if not np.isnan(p["mom"][i]) else -1e9
                cands.append((score, t, i, is_trend))
            if params["rank"]:
                cands.sort(key=lambda x: -x[0])
            sec_count = {}
            for pos in positions.values():
                sec_count[pos["sector"]] = sec_count.get(pos["sector"], 0) + 1
            for score, t, i, is_trend in cands:
                if len(positions) >= max_pos:
                    break
                sec = prep[t]["sector"]
                if sec_count.get(sec, 0) >= sector_cap:
                    continue
                price = float(prep[t]["close"][i])
                if is_trend:
                    if params["atr"] and not np.isnan(prep[t]["atr_pct"][i]):
                        stop = min(ATR_MAX, max(ATR_MIN, ATR_MULT * prep[t]["atr_pct"][i]))
                    else:
                        stop = params["tstop"]
                else:
                    stop = stop_loss_pct
                positions[t] = {"buy": price, "peak": price, "stop": stop, "trend": is_trend, "sector": sec}
                last_price[t] = price
                sec_count[sec] = sec_count.get(sec, 0) + 1

        # 3) оцінка рахунку на кінець дня
        unreal = 0.0
        for t, pos in positions.items():
            lp = last_price.get(t, pos["buy"])
            unreal += (lp / pos["buy"] - 1) * trade_value
        equity_curve.append(realized + unreal)
        slots_used.append(len(positions))

    # позиції, що лишились, закриваємо по останній ціні
    for t, pos in positions.items():
        lp = last_price.get(t, pos["buy"])
        pnl = (lp / pos["buy"] - 1) * trade_value - trade_value * comm * (1 + lp / pos["buy"])
        closed.append(pnl / trade_value * 100)

    eq = np.array(equity_curve) if equity_curve else np.array([0.0])
    peak = np.maximum.accumulate(np.maximum(eq, 0))
    dd = (eq - peak)
    mid = len(eq) // 2
    n = len(closed)
    return {
        "trades": n,
        "win_rate": round(sum(1 for x in closed if x > 0) / n * 100, 1) if n else 0.0,
        "avg_trade_pct": round(float(np.mean(closed)), 2) if n else 0.0,
        "total_pct": round(float(eq[-1]) / capital * 100, 1),
        "h1_pct": round(float(eq[mid - 1]) / capital * 100, 1) if mid > 0 else 0.0,
        "h2_pct": round(float(eq[-1] - eq[mid - 1]) / capital * 100, 1) if mid > 0 else 0.0,
        "max_dd_pct": round(float(dd.min()) / capital * 100, 1),
        "avg_slots": round(float(np.mean(slots_used)), 1) if slots_used else 0.0,
    }


def index_benchmark(index_df: pd.DataFrame, dates) -> dict:
    s = index_df.set_index(pd.to_datetime(index_df["timestamp"]).dt.tz_localize(None).dt.normalize())["close"].astype(float)
    s = s[~s.index.duplicated(keep="last")].reindex(dates, method="ffill").dropna()
    if len(s) < 2:
        return {}
    ret = (s.iloc[-1] / s.iloc[0] - 1) * 100
    dd = ((s / s.cummax()) - 1).min() * 100
    mid = len(s) // 2
    return {"total_pct": round(float(ret), 1), "max_dd_pct": round(float(dd), 1),
            "h1_pct": round(float((s.iloc[mid - 1] / s.iloc[0] - 1) * 100), 1),
            "h2_pct": round(float((s.iloc[-1] / s.iloc[mid - 1] - 1) * 100), 1)}


def run_portfolio(data: dict, index_df: pd.DataFrame, max_pos: int, sector_cap: int, trade_value: float,
                  stop_loss_pct: float, commission_pct: float) -> dict:
    print(f"[portfolio] Готую сигнали для {len(data)} акцій...")
    prep = prepare_all(data)
    all_dates = sorted({d for p in prep.values() for d in p["date_to_i"] if True})
    # лише дати, коли вже можна торгувати (після прогріву хоча б однієї акції)
    first_ok = min(list(p["date_to_i"].keys())[WARMUP] for p in prep.values() if len(p["date_to_i"]) > WARMUP)
    dates = [d for d in all_dates if d >= first_ok]
    mok = _market_ok_series(index_df, dates)
    rows = []
    for name, params in VARIANTS:
        r = run_variant(prep, dates, mok, params, max_pos, sector_cap, trade_value, stop_loss_pct, commission_pct)
        r["name"] = name
        rows.append(r)
        print(f"[portfolio] {name}: угод {r['trades']}, прибуток {r['total_pct']:+.1f}%, "
              f"просадка {r['max_dd_pct']:.1f}%, win {r['win_rate']}%")
    bench = index_benchmark(index_df, dates)
    return {"rows": rows, "bench": bench, "max_pos": max_pos, "sector_cap": sector_cap,
            "period": f"{dates[0].date()} → {dates[-1].date()}", "n_tickers": len(prep)}


def build_html(res: dict) -> str:
    def cell(v, pct=True):
        cls = "bull" if v > 0 else ("bear" if v < 0 else "neutral")
        return f'<td class="mono {cls}">{v:+.1f}%</td>'

    rows = ""
    for r in res["rows"]:
        rows += (f"<tr><td>{r['name']}</td><td class='mono'>{r['trades']}</td>"
                 f"<td class='mono'>{r['win_rate']}%</td>" + cell(r["total_pct"]) + cell(r["h1_pct"]) + cell(r["h2_pct"])
                 + f"<td class='mono bear'>{r['max_dd_pct']:.1f}%</td><td class='mono'>{r['avg_slots']}</td></tr>")
    b = res["bench"]
    if b:
        rows += ("<tr class='benchmark-row'><td>Індекс S&amp;P 500 (купив і тримав)</td><td class='mono'>—</td><td class='mono'>—</td>"
                 + cell(b["total_pct"]) + cell(b["h1_pct"]) + cell(b["h2_pct"])
                 + f"<td class='mono bear'>{b['max_dd_pct']:.1f}%</td><td class='mono'>—</td></tr>")
    return f"""
  <h1 style="margin-top:18px">ПОРТФЕЛЬНИЙ тест (як живий бот)</h1>
  <div class="updated">{res['n_tickers']} акцій разом, не більше {res['max_pos']} позицій, не більше {res['sector_cap']} на сектор.
  Період {res['period']}. «Прибуток» і «просадка» — у % від капіталу ({res['max_pos']} ставок); невикористаний
  капітал вважається простоюючим (0%). Індекс — у % від вкладеної суми.</div>
  <table><tr><th>Варіант</th><th>Угод</th><th>Win</th><th>Прибуток</th><th>1-ша пол.</th><th>2-га пол.</th><th>Макс. просадка</th><th>Сер. позицій</th></tr>{rows}</table>"""
