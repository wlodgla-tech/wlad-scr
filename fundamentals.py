"""
fundamentals.py
=================
Другий, незалежний погляд на інструмент — поруч із технічними
індикаторами (analyzer.py), тут рахується проста фундаментальна
"fair value" за методом EV/EBITDA-мультиплікатора, і підтягується
календар найближчих корпоративних подій (звітність, дивіденди).

Дані беруться автоматично через yfinance (ticker.info / ticker.calendar),
на відміну від ручного вводу — тому це працює лише для акцій і ETF, що
мають публічну фінансову звітність. Для криптовалют, форексу чи товарних
фʼючерсів (як Sugar No. 11) fundamentals просто недоступні — функції
нижче в такому разі повертають None, і це нормально, не помилка.

Чому тут знову немає "ПЕРЕОЦІНЕНО -> ПРОДАВАЙ"
---------------------------------------------------
Той самий принцип, що і в analyzer.py: EV/EBITDA-модель — це один із
багатьох можливих поглядів на вартість компанії, заснований на одному
простому мультиплікаторі медіани галузі. Вона свідомо НЕ враховує DCF,
темпи росту, якість менеджменту, борговий цикл, макроумови і т.д. Тому
результат — це "за цією простою моделлю, вартість приблизно X", а не
вердикт "купуй" чи "продавай".
"""

import yfinance as yf


def get_fair_value(ticker: str, target_multiple: float = None) -> dict | None:
    """Повертає dict з ринковою ціною, оціночною "fair value" (EV/EBITDA)
    і % переоцінки/недооцінки, або None якщо дані недоступні (напр. для
    крипти/форексу/фʼючерсів, де немає корпоративної звітності)."""
    try:
        info = yf.Ticker(ticker).info
    except Exception as e:
        print(f"[fundamentals] Не вдалося отримати info для {ticker}: {e}")
        return None

    market_price = info.get("currentPrice") or info.get("regularMarketPrice")
    ebitda = info.get("ebitda")
    shares = info.get("sharesOutstanding")
    total_debt = info.get("totalDebt") or 0
    cash = info.get("totalCash") or 0
    sector = info.get("sector")
    industry = info.get("industry")

    if not market_price or not ebitda or not shares or ebitda <= 0:
        # Немає даних (криптовалюта, форекс, фʼючерс) або компанія
        # збиткова за EBITDA — модель на такому не рахується коректно.
        return None

    # Якщо множник не заданий — використовуємо поточний EV/EBITDA самої
    # компанії як "нейтральну" точку відліку (тобто за замовчуванням
    # показує 0% розбіжності; це навмисно: без незалежного галузевого
    # бенчмарку вгадувати "правильний" мультиплікатор некоректно).
    market_cap = info.get("marketCap") or (market_price * shares)
    enterprise_value = market_cap + total_debt - cash
    current_multiple = enterprise_value / ebitda if ebitda else None

    multiple = target_multiple if target_multiple else current_multiple
    if multiple is None:
        return None

    fair_ev = multiple * ebitda
    fair_equity = fair_ev - total_debt + cash
    fair_price = fair_equity / shares if shares else None

    if not fair_price:
        return None

    diff_pct = (market_price - fair_price) / fair_price * 100

    return {
        "ticker": ticker,
        "sector": sector,
        "industry": industry,
        "market_price": round(market_price, 2),
        "fair_price": round(fair_price, 2),
        "diff_pct": round(diff_pct, 2),
        "ebitda_m": round(ebitda / 1e6, 1),
        "multiple_used": round(multiple, 2),
        "note": (
            "Проста оцінка за EV/EBITDA з поточним мультиплікатором компанії — "
            "не враховує темпи росту, DCF, борговий цикл чи галузеві умови."
        ),
    }


def get_upcoming_events(ticker: str) -> list:
    """Повертає список найближчих відомих корпоративних подій
    (дата звітності, дата екс-дивіденду), або [] якщо немає даних."""
    events = []
    try:
        t = yf.Ticker(ticker)
        cal = t.calendar
    except Exception as e:
        print(f"[fundamentals] Не вдалося отримати calendar для {ticker}: {e}")
        return events

    if not cal:
        return events

    # yfinance повертає або dict, або DataFrame залежно від версії
    if isinstance(cal, dict):
        earnings_dates = cal.get("Earnings Date") or []
        if isinstance(earnings_dates, (list, tuple)) and earnings_dates:
            events.append({"type": "EARNINGS", "date": str(earnings_dates[0])})
        ex_div = cal.get("Ex-Dividend Date")
        if ex_div:
            events.append({"type": "EX_DIVIDEND", "date": str(ex_div)})
    else:
        try:
            for col in cal.columns:
                val = cal[col].iloc[0]
                if val is not None:
                    events.append({"type": str(col).upper().replace(" ", "_"), "date": str(val)})
        except Exception:
            pass

    return events


if __name__ == "__main__":
    # швидкий тест: python fundamentals.py
    fv = get_fair_value("EBAY")
    print("Fair value:", fv)
    ev = get_upcoming_events("EBAY")
    print("Events:", ev)
