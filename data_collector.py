"""
data_collector.py
===================
Завантажує історичні свічки через yfinance (безкоштовно, без API-ключа).

Працює для товарних фʼючерсів (Sugar No. 11 -> "SB=F"), крипти
("BTC-USD"), форексу ("EURUSD=X") та акцій.
"""

import pandas as pd
import yfinance as yf

# yfinance обмежує, скільки історії можна взяти для кожного таймфрейму.
# Для 5m — максимум ~60 днів; для 1h — максимум ~730 днів.
DEFAULT_PERIODS = {
    "5m": "5d",
    "15m": "5d",
    "1h": "60d",
    "1d": "1y",
}


def _normalize(df: pd.DataFrame):
    """Приводить сирий DataFrame від yfinance до єдиного формату:
    timestamp, open, high, low, close, volume. Повертає None, якщо
    даних немає або не вистачає потрібних колонок (тікер не знайдено,
    делістинг тощо) — замість падіння з помилкою."""
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.reset_index()
    df = df.rename(columns={
        "Date": "timestamp", "Datetime": "timestamp",
        "Open": "open", "High": "high", "Low": "low",
        "Close": "close", "Volume": "volume",
    })
    needed = ["timestamp", "open", "high", "low", "close", "volume"]
    if not all(c in df.columns for c in needed):
        return None
    df = df[needed].dropna(subset=["close"])
    return df.reset_index(drop=True) if not df.empty else None


def fetch_candles(ticker: str, interval: str = "1h", period: str = None) -> pd.DataFrame:
    """Завантажує свічки ОДНОГО тікера і повертає DataFrame з колонками:
    timestamp, open, high, low, close, volume.
    """
    if period is None:
        period = DEFAULT_PERIODS.get(interval, "60d")

    raw = yf.download(ticker, period=period, interval=interval, progress=False, auto_adjust=True)
    df = _normalize(raw)
    if df is None:
        raise RuntimeError(
            f"Не вдалося отримати дані для '{ticker}' (interval={interval}, period={period}). "
            "Перевірте формат тікера у Yahoo Finance."
        )
    return df


def fetch_candles_batch(tickers: list, interval: str = "1h", period: str = None) -> dict:
    """Та сама логіка, що fetch_candles, але для БАГАТЬОХ тікерів ОДНИМ
    запитом до Yahoo Finance замість окремого запиту на кожен — швидше і
    набагато менше навантажує Yahoo, коли скануємо десятки інструментів.
    Повертає {ticker: DataFrame або None (якщо дані недоступні)}."""
    tickers = list(dict.fromkeys(t.strip() for t in tickers if t.strip()))  # унікальні, без порожніх
    if not tickers:
        return {}
    if period is None:
        period = DEFAULT_PERIODS.get(interval, "60d")

    raw = yf.download(
        tickers, period=period, interval=interval, group_by="ticker",
        progress=False, auto_adjust=True, threads=True,
    )

    result = {}
    if len(tickers) == 1:
        result[tickers[0]] = _normalize(raw)
        return result

    for ticker in tickers:
        try:
            sub = raw[ticker] if isinstance(raw.columns, pd.MultiIndex) else raw
        except (KeyError, IndexError):
            result[ticker] = None
            continue
        result[ticker] = _normalize(sub)
    return result


def fetch_multi_timeframe(ticker: str, timeframes=("5m", "1h")) -> dict:
    """Повертає {"5m": df, "1h": df, ...} — зручно, коли аналізатор хоче
    дивитись на кілька таймфреймів одночасно."""
    result = {}
    for tf in timeframes:
        try:
            result[tf] = fetch_candles(ticker, interval=tf)
        except RuntimeError as e:
            print(f"[data_collector] Пропускаю таймфрейм {tf}: {e}")
    return result


if __name__ == "__main__":
    # швидкий тест: python data_collector.py
    df = fetch_candles("BTC-USD", interval="1h")
    print(df.tail())
    print(f"\nОтримано {len(df)} свічок.")
