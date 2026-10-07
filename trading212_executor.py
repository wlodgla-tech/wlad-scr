"""
trading212_executor.py
========================
Тонкий клієнт для публічного API Trading 212 (Invest / Stocks ISA рахунки
тільки — CFD не підтримується цим API). Відповідає ТІЛЬКИ за зв'язок з
Trading 212: авторизація, пошук інструмента, виставлення ринкового ордера,
баланс рахунку. Жодної торгової логіки тут немає — вона в main.py.

Налаштування через змінні середовища:
    TRADING212_API_KEY   — API-ключ з Trading 212 (Settings -> API, Beta)
    TRADING212_MODE       — "demo" (навчальний/віртуальний рахунок, РЕКОМЕНДОВАНО
                             для старту) або "live" (реальні гроші). default "demo"
    TRADING212_DRY_RUN    — "true"/"false". Якщо "true" (default) — бот РАХУЄ,
                             що б він зробив, пише це в лог і дашборд, але
                             жодного реального ордера НЕ виставляє. Це додатковий
                             запобіжник НАД режимом demo/live.

Чому два незалежні перемикачі (DRY_RUN і MODE):
    - DRY_RUN — "чи торгувати взагалі" (безпечний запобіжник за замовчуванням).
    - MODE    — "якщо торгувати, то де" (віртуальні гроші demo-рахунку, чи
                справжні гроші live-рахунку).
    Так можна тестувати логіку (DRY_RUN=true) на тому ж коді, яким потім
    піде в demo, а тоді і в live, не змінюючи нічого, крім змінних середовища.
"""

import base64
import json
import os
import time
from pathlib import Path

import requests

API_KEY = os.environ.get("TRADING212_API_KEY", "")
MODE = os.environ.get("TRADING212_MODE", "demo").strip().lower()
DRY_RUN = os.environ.get("TRADING212_DRY_RUN", "true").strip().lower() != "false"

BASE_URLS = {
    "demo": "https://demo.trading212.com/api/v0",
    "live": "https://live.trading212.com/api/v0",
}
BASE_URL = BASE_URLS.get(MODE, BASE_URLS["demo"])

INSTRUMENTS_CACHE_FILE = Path(__file__).parent / "t212_instruments_cache.json"
INSTRUMENTS_CACHE_MAX_AGE = 6 * 3600  # 6 годин — список інструментів міняється рідко


def _headers() -> dict:
    # Trading 212 API очікує Basic Auth: API Key як "логін", без пароля.
    token = base64.b64encode(f"{API_KEY}:".encode()).decode()
    return {"Authorization": f"Basic {token}", "Content-Type": "application/json"}


def is_configured() -> bool:
    return bool(API_KEY)


def _get(path: str, timeout: int = 20):
    resp = requests.get(f"{BASE_URL}{path}", headers=_headers(), timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _post(path: str, payload: dict, timeout: int = 20):
    resp = requests.post(f"{BASE_URL}{path}", headers=_headers(), json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def get_account_summary() -> dict | None:
    """Повертає баланс рахунку (готівка тощо), або None при помилці."""
    try:
        return _get("/equity/account/summary")
    except Exception as e:
        print(f"[trading212] Не вдалося отримати account summary: {e}")
        return None


def get_open_positions() -> list:
    """Повертає список відкритих позицій з самого Trading 212 (для звірки
    зі своїм локальним станом). Порожній список при помилці."""
    try:
        data = _get("/equity/positions")
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"[trading212] Не вдалося отримати positions: {e}")
        return []


def _load_instruments_cache() -> list | None:
    if INSTRUMENTS_CACHE_FILE.exists():
        try:
            raw = json.loads(INSTRUMENTS_CACHE_FILE.read_text())
            if time.time() - raw.get("fetched_at", 0) < INSTRUMENTS_CACHE_MAX_AGE:
                return raw.get("instruments", [])
        except (json.JSONDecodeError, OSError):
            pass
    return None


def _save_instruments_cache(instruments: list) -> None:
    INSTRUMENTS_CACHE_FILE.write_text(
        json.dumps({"fetched_at": time.time(), "instruments": instruments}, ensure_ascii=False)
    )


def list_instruments() -> list:
    """Повний список інструментів, доступних для торгівлі на Trading 212.
    Кешується локально, бо цей endpoint дозволяє лише 1 запит / 50с і дані
    однаково оновлюються раз на ~10 хв на боці Trading 212."""
    cached = _load_instruments_cache()
    if cached is not None:
        return cached
    try:
        data = _get("/equity/metadata/instruments")
        instruments = data if isinstance(data, list) else []
        _save_instruments_cache(instruments)
        return instruments
    except Exception as e:
        print(f"[trading212] Не вдалося отримати список інструментів: {e}")
        return []


def find_instrument_ticker(yahoo_ticker: str) -> str | None:
    """Мапить наш тікер у форматі Yahoo Finance (напр. "AAPL") на тікер-код
    Trading 212 (напр. "AAPL_US_EQ"). Повертає None, якщо не знайдено
    (інструмент недоступний на Trading 212 Invest/ISA — напр. фʼючерси,
    крипта, форекс чи індекси, яких немає серед акцій/ETF)."""
    base = yahoo_ticker.upper().strip()
    if any(ch in base for ch in ("=", "^", "-")):
        # Явно не акція/ETF (фʼючерс "SB=F", індекс "^GSPC", крипта "BTC-USD")
        return None

    instruments = list_instruments()
    candidates = [
        inst for inst in instruments
        if str(inst.get("ticker", "")).upper().startswith(base + "_")
        or str(inst.get("shortName", "")).upper() == base
    ]
    if not candidates:
        return None
    # Серед кількох збігів (різні біржі/лістинги) — перевага US_EQ, бо саме
    # в такому форматі зазвичай вказані наші акції.
    for inst in candidates:
        if str(inst.get("ticker", "")).upper().endswith("_US_EQ"):
            return inst["ticker"]
    return candidates[0]["ticker"]


def place_market_order(t212_ticker: str, quantity: float) -> dict:
    """Виставляє ринковий ордер. quantity > 0 — купити, quantity < 0 — продати.
    Повертає dict з результатом: {"ok": bool, "dry_run": bool, "detail": ..., "order": ...}.
    У режимі DRY_RUN жодного реального запиту до Trading 212 НЕ робиться."""
    action = "BUY" if quantity > 0 else "SELL"

    if DRY_RUN:
        print(f"[trading212] DRY RUN — не надсилаю реальний ордер: {action} {abs(quantity)} x {t212_ticker}")
        return {"ok": True, "dry_run": True, "detail": f"(dry-run) {action} {abs(quantity):.4f} x {t212_ticker}"}

    if not is_configured():
        return {"ok": False, "dry_run": False, "detail": "TRADING212_API_KEY не задано"}

    try:
        order = _post("/equity/orders/market", {"ticker": t212_ticker, "quantity": round(quantity, 4)})
        print(f"[trading212] Ордер відправлено ({MODE}): {action} {abs(quantity)} x {t212_ticker} -> {order}")
        return {"ok": True, "dry_run": False, "detail": f"{action} {abs(quantity):.4f} x {t212_ticker}", "order": order}
    except requests.exceptions.HTTPError as e:
        body = e.response.text if e.response is not None else str(e)
        print(f"[trading212] Помилка ордера: {e} | {body}")
        return {"ok": False, "dry_run": False, "detail": f"HTTP помилка: {body}"}
    except Exception as e:
        print(f"[trading212] Неочікувана помилка ордера: {e}")
        return {"ok": False, "dry_run": False, "detail": f"Помилка: {e}"}


if __name__ == "__main__":
    # швидкий тест: python trading212_executor.py
    print("MODE:", MODE, "| DRY_RUN:", DRY_RUN, "| configured:", is_configured())
    if is_configured():
        print("Account summary:", get_account_summary())
        print("AAPL ->", find_instrument_ticker("AAPL"))
