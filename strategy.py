"""
strategy.py
============
Технічні індикатори та дві торгові стратегії, винесені окремо від
analyzer.py, щоб ОДНАКОВИЙ код можна було і бектестити на історії
(backtest.py), і (пізніше, якщо результати бектесту це виправдають)
підключити до живого бота (main.py). Жодних викликів мережі тут немає —
лише математика над уже завантаженим DataFrame.

Дві стратегії:

  OLD (поточна, вже працює в боті) — RSI(14) перепроданість/перекупленість
  + близькість до рівня підтримки/опору. Один-два сигнали, без фільтра
  тренду.

  NEW (посилена версія) — вимагає ЗБІГУ кількох незалежних сигналів, а не
  одного:
    - Фільтр тренду: купувати тільки якщо ціна вище SMA200 (довгостроковий
      висхідний тренд) — щоб не "ловити падаючий ніж" у ведмежому ринку.
    - Серед {RSI<35, ціна біля нижньої смуги Боллінджера, MACD бичачий
      (лінія MACD > сигнальна)} — потрібно щонайменше 2 з 3, щоб вважати
      це сигналом на купівлю.
    - Вихід: стоп-лос (рахується окремо, в самому бектесті/боті за ціною
      купівлі) АБО RSI>70 / ціна біля верхньої смуги Боллінджера АБО ціна
      пробила SMA50 вниз (злам короткострокового тренду).

  Чому саме так: один індикатор дає багато хибних сигналів. Вимога збігу
  кількох незалежних speed non-corrrelated ознак — стандартний спосіб
  зменшити кількість хибних входів, хоч і не усуває їх повністю.
"""

import numpy as np
import pandas as pd

import analyzer  # перевикористовуємо compute_rsi і find_swing_levels


def compute_sma(close: pd.Series, window: int) -> pd.Series:
    return close.rolling(window).mean()


def compute_macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def compute_bollinger(close: pd.Series, window: int = 20, num_std: float = 2.0):
    mid = close.rolling(window).mean()
    std = close.rolling(window).std()
    upper = mid + num_std * std
    lower = mid - num_std * std
    return upper, mid, lower


def compute_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ADX (Average Directional Index) — показує СИЛУ тренду (0-100),
    незалежно від напрямку. Низький ADX (<15-20) = ринок "в боці", без
    чіткого тренду — саме там технічні сигнали найчастіше хибні. Високий
    ADX = чіткий тренд, сигналам можна довіряти більше."""
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)

    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    atr = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / atr
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / atr

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx = dx.ewm(alpha=1 / period, adjust=False).mean()
    return adx


def add_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Додає всі колонки-індикатори одразу (векторизовано, швидко для
    бектесту). Повертає НОВИЙ DataFrame, оригінал не чіпає."""
    df = df.copy()
    df["rsi"] = analyzer.compute_rsi(df["close"])
    df["sma50"] = compute_sma(df["close"], 50)
    df["sma200"] = compute_sma(df["close"], 200)
    macd_line, signal_line, hist = compute_macd(df["close"])
    df["macd"] = macd_line
    df["macd_signal"] = signal_line
    df["macd_hist"] = hist
    bb_upper, bb_mid, bb_lower = compute_bollinger(df["close"])
    df["bb_upper"] = bb_upper
    df["bb_lower"] = bb_lower
    df["adx"] = compute_adx(df)
    df["vol_sma20"] = df["volume"].rolling(20).mean()
    return df


def old_strategy_signal(df: pd.DataFrame, i: int, has_position: bool) -> tuple:
    """Відтворює логіку, яка вже працює в analyzer.py/main.py: RSI +
    близькість до рівня підтримки/опору, рахованих на останніх ~60 барах.
    Повертає ("BUY"|"SELL"|None, reason: str)."""
    row = df.iloc[i]
    rsi = row["rsi"]
    if pd.isna(rsi):
        return None, ""

    window = df.iloc[max(0, i - 60):i + 1]
    support, resistance = analyzer.find_swing_levels(window, window=5)
    price = row["close"]
    near_support = any(abs(price - lvl) / lvl * 100 <= 0.5 for lvl in support)
    near_resistance = any(abs(price - lvl) / lvl * 100 <= 0.5 for lvl in resistance)

    if not has_position:
        if rsi < 30:
            return "BUY", "RSI_OVERSOLD"
        if near_support:
            return "BUY", "NEAR_SUPPORT"
        return None, ""
    else:
        if rsi > 70:
            return "SELL", "RSI_OVERBOUGHT"
        if near_resistance:
            return "SELL", "NEAR_RESISTANCE"
        return None, ""


def new_strategy_signal(df: pd.DataFrame, i: int, has_position: bool) -> tuple:
    """Посилена стратегія зі збігом кількох сигналів + фільтр тренду +
    фільтр сили тренду (ADX) + підтвердження обсягом."""
    row = df.iloc[i]
    rsi, price = row["rsi"], row["close"]
    sma50, sma200 = row["sma50"], row["sma200"]
    macd, macd_signal = row["macd"], row["macd_signal"]
    bb_upper, bb_lower = row["bb_upper"], row["bb_lower"]
    adx = row.get("adx")
    volume, vol_sma20 = row.get("volume"), row.get("vol_sma20")

    if pd.isna(rsi) or pd.isna(sma200) or pd.isna(macd) or pd.isna(bb_lower):
        return None, ""  # недостатньо історії ще для всіх індикаторів

    if not has_position:
        trend_ok = price > sma200
        if not trend_ok:
            return None, ""  # не купуємо проти довгострокового тренду

        # ADX < 15 означає "млявий", безнапрямковий ринок — там технічні
        # сигнали частіше хибні. Якщо ADX ще не порахувався (мало історії)
        # — не блокуємо, просто не враховуємо цей фільтр.
        if adx is not None and not pd.isna(adx) and adx < 15:
            return None, ""

        votes = []
        if rsi < 35:
            votes.append("RSI<35")
        if price <= bb_lower:
            votes.append("NEAR_LOWER_BB")
        if macd > macd_signal:
            votes.append("MACD_BULLISH")
        if volume is not None and vol_sma20 is not None and not pd.isna(vol_sma20) and vol_sma20 > 0 \
                and volume > 1.2 * vol_sma20:
            votes.append("VOLUME_CONFIRMED")

        if len(votes) >= 2:
            return "BUY", "+".join(votes)
        return None, ""
    else:
        if rsi > 70:
            return "SELL", "RSI_OVERBOUGHT"
        if price >= bb_upper:
            return "SELL", "NEAR_UPPER_BB"
        if not pd.isna(sma50) and price < sma50:
            return "SELL", "TREND_BREAK_SMA50"
        return None, ""


def daily_trend_bullish(daily_df: pd.DataFrame) -> bool:
    """Мультитаймфрейм-фільтр: дивимось на ЩОДЕННИЙ графік (окремо від
    того таймфрейму, яким торгуємо — напр. 1h) і визначаємо загальний
    тренд. Висхідний тренд = ціна вище SMA50 на денному графіку І SMA50
    вище SMA200 (довший тренд теж вгору). Повертає False, якщо даних
    недостатньо (краще пропустити угоду, ніж вгадувати)."""
    if daily_df is None or len(daily_df) < 200:
        return False
    d = add_all_indicators(daily_df)
    last = d.iloc[-1]
    if pd.isna(last["sma50"]) or pd.isna(last["sma200"]):
        return False
    return bool(last["close"] > last["sma50"] > last["sma200"])


STRATEGIES = {
    "old": old_strategy_signal,
    "new": new_strategy_signal,
}
