"""
analyzer.py
============
Рахує RSI(14) та рівні підтримки/опору, перевіряє типові технічні
"ситуації" (перепроданість/перекупленість, наближення до рівня, хибний
пробій) і формує текст для Telegram.

Чому тут немає 'BUY_SIGNAL' / 'SELL_SIGNAL'
---------------------------------------------
RSI<30 чи дотик рівня підтримки НЕ означає "купуй" — це лише означає, що
ціна зараз у такій-то технічній зоні. Індикатори дивляться в минуле і
регулярно помиляються; видавати їх за команду до дії створює фальшиву
впевненість. Тому сигнали тут описові (RSI_OVERSOLD, NEAR_SUPPORT,
FALSE_BREAKOUT_UP...) — що саме сталось, а не що робити. Рішення купити
чи продати — завжди ваше, з власним аналізом і керуванням ризиком.
"""

import numpy as np
import pandas as pd

RSI_PERIOD = 14
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70

SWING_WINDOW = 5            # скільки свічок зліва/справа для локального екстремуму
NEAR_LEVEL_PCT = 0.5        # наскільки близько (%) вважати "біля рівня"
BREAKOUT_LOOKBACK = 3       # скільки останніх свічок перевіряти на хибний пробій
BREAKOUT_MARGIN_PCT = 0.1   # на скільки % треба пробити рівень


def compute_rsi(close: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(100)


def find_swing_levels(df: pd.DataFrame, window: int = SWING_WINDOW, bars_back: int = None):
    """Повертає (support_levels, resistance_levels) з локальних екстремумів.
    bars_back обмежує пошук останніми N свічками (напр. ~24h для даного таймфрейму)."""
    data = df.tail(bars_back) if bars_back else df
    highs = data["high"].values
    lows = data["low"].values
    n = len(data)
    resistance, support = [], []
    for i in range(window, n - window):
        wh = highs[i - window:i + window + 1]
        wl = lows[i - window:i + window + 1]
        if highs[i] == wh.max():
            resistance.append(float(highs[i]))
        if lows[i] == wl.min():
            support.append(float(lows[i]))

    def dedupe(levels, tol_pct=0.3):
        levels = sorted(levels)
        out = []
        for lvl in levels:
            if not out or abs(lvl - out[-1]) / out[-1] * 100 > tol_pct:
                out.append(lvl)
        return out

    return dedupe(support), dedupe(resistance)


def _nearest(levels, price):
    if not levels:
        return None, None
    nearest = min(levels, key=lambda lvl: abs(lvl - price))
    pct_diff = (price - nearest) / nearest * 100
    return nearest, pct_diff


def detect_false_breakouts(df: pd.DataFrame, levels, lookback=BREAKOUT_LOOKBACK, margin_pct=BREAKOUT_MARGIN_PCT):
    events = []
    recent = df.tail(lookback + 1)
    if len(recent) < 2:
        return events
    last_close = float(recent["close"].iloc[-1])
    for lvl in levels:
        margin = lvl * margin_pct / 100
        pierced_above = (recent["high"].iloc[:-1] > lvl + margin).any()
        pierced_below = (recent["low"].iloc[:-1] < lvl - margin).any()
        if pierced_above and last_close < lvl:
            events.append(("resistance", lvl, "FALSE_BREAKOUT_UP"))
        if pierced_below and last_close > lvl:
            events.append(("support", lvl, "FALSE_BREAKOUT_DOWN"))
    return events


def analyze(df: pd.DataFrame, label: str = "", bars_per_24h: int = None) -> dict:
    """Головна функція. Повертає dict:
        {
          "events": [ {"type": str, "price": float, "detail": str}, ... ],
          "price": float,
          "rsi": float,
          "message": str | None,   # готовий текст для Telegram, або None якщо нічого цікавого
        }
    """
    df = df.dropna(subset=["close"]).reset_index(drop=True)
    df["rsi"] = compute_rsi(df["close"], RSI_PERIOD)

    last_price = float(df["close"].iloc[-1])
    last_rsi = float(df["rsi"].iloc[-1])

    support, resistance = find_swing_levels(df, SWING_WINDOW, bars_back=bars_per_24h)
    nearest_sup, sup_pct = _nearest(support, last_price)
    nearest_res, res_pct = _nearest(resistance, last_price)
    breakout_events = detect_false_breakouts(df, support + resistance)

    events = []

    if last_rsi < RSI_OVERSOLD:
        events.append({"type": "RSI_OVERSOLD", "price": last_price,
                        "detail": f"RSI={last_rsi:.1f} (&lt;30) — зона перепроданості"})
    elif last_rsi > RSI_OVERBOUGHT:
        events.append({"type": "RSI_OVERBOUGHT", "price": last_price,
                        "detail": f"RSI={last_rsi:.1f} (&gt;70) — зона перекупленості"})

    if nearest_sup is not None and abs(sup_pct) <= NEAR_LEVEL_PCT:
        events.append({"type": "NEAR_SUPPORT", "price": last_price,
                        "detail": f"ціна {sup_pct:+.2f}% від рівня підтримки ≈{nearest_sup:.4f}"})
    if nearest_res is not None and abs(res_pct) <= NEAR_LEVEL_PCT:
        events.append({"type": "NEAR_RESISTANCE", "price": last_price,
                        "detail": f"ціна {res_pct:+.2f}% від рівня опору ≈{nearest_res:.4f}"})

    for kind, lvl, event_type in breakout_events:
        events.append({"type": event_type, "price": last_price,
                        "detail": f"рівень ≈{lvl:.4f} ({kind}) пробитий і ціна повернулась назад"})

    message = None
    if events:
        lines = [f"📊 <b>{label}</b>" if label else "📊 <b>Ринок</b>"]
        for ev in events:
            lines.append(f"• {ev['type']}: {ev['detail']}")
        lines.append(f"\nПоточна ціна: {last_price:.4f}")
        lines.append("\n<i>Це описові відмітки індикаторів, не торгові рекомендації. "
                      "Рішення купувати чи продавати — ваше, з власним аналізом ризику.</i>")
        message = "\n".join(lines)

    return {"events": events, "price": last_price, "rsi": last_rsi, "message": message}


if __name__ == "__main__":
    # швидкий тест на синтетичних даних: python analyzer.py
    import numpy as np
    np.random.seed(0)
    n = 200
    price = 100 + np.cumsum(np.random.randn(n) * 0.8)
    df = pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=n, freq="h"),
        "open": price, "high": price + 0.5, "low": price - 0.5,
        "close": price, "volume": 1000,
    })
    result = analyze(df, label="TEST")
    print(result["message"] or "Нічого цікавого зараз.")
