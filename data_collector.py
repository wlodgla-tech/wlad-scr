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


def fetch_candles(ticker: str, interval: str = "1h", period: str = None) -> pd.DataFrame:
    """Завантажує свічки і повертає DataFrame з колонками:
    timestamp, open, high, low, close, volume.
    """
    if period is None:
        period = DEFAULT_PERIODS.get(interval, "60d")

    df = yf.download(ticker, period=period, interval=interval, progress=False, auto_adjust=True)
    if df.empty:
        raise RuntimeError(
            f"Не вдалося отримати дані для '{ticker}' (interval={interval}, period={period}). "
            "Перевірте формат тікера у Yahoo Finance."
        )

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df.reset_index()
    df = df.rename(columns={
        "Date": "timestamp", "Datetime": "timestamp",
        "Open": "open", "High": "high", "Low": "low",
        "Close": "close", "Volume": "volume",
    })
    df = df[["timestamp", "open", "high", "low", "close", "volume"]].dropna(subset=["close"])
    return df.reset_index(drop=True)


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
