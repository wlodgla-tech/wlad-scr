"""
watchlist.py
=============
Пул акцій, серед яких бот САМ шукає сигнали для реальної торгівлі на
Trading 212 (не плутати зі змінною TICKER — тим списком, що показується
на дашборді, і може містити фʼючерси/крипту/індекси, недоступні для
торгівлі на Trading 212 Invest/ISA).

Чому не "буквально всі" інструменти Trading 212 (їх тисячі):
    - Yahoo Finance (звідки беремо ціни) має практичні обмеження на
      кількість тікерів в одному пакетному запиті.
    - Сканування тисяч тікерів кожні 30 хв не влізло б у розумний час
      виконання GitHub Actions.
    ~100 великих ліквідних акцій США — розумний баланс між "є з чого
    вибирати" і "реально встигає відпрацювати за один запуск".

Щоб змінити список — просто відредагуйте цей файл (додайте/видаліть
тікери в форматі Yahoo Finance, той самий, що й у TICKER).
"""

TRADING_UNIVERSE = [
    # Технології
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "AMD", "INTC",
    "CSCO", "ORCL", "ADBE", "CRM", "NFLX", "QCOM", "TXN", "IBM", "AVGO",
    "NOW", "UBER", "SHOP", "PYPL", "SNOW", "PLTR",
    # Фінанси
    "JPM", "V", "MA", "BAC", "WFC", "GS", "MS", "C", "AXP", "BLK", "SCHW",
    "COF", "SPGI", "ICE",
    # Охорона здоров'я
    "UNH", "JNJ", "ABBV", "MRK", "ABT", "TMO", "DHR", "BMY", "LLY", "PFE",
    "GILD", "AMGN", "CVS", "CI", "ISRG", "SYK", "MDT",
    # Споживчі товари / роздріб
    "PG", "KO", "PEP", "COST", "WMT", "HD", "LOW", "NKE", "MCD", "SBUX",
    "TGT", "DIS", "EL", "CL", "KMB",
    # Промисловість / енергетика
    "XOM", "CVX", "COP", "SLB", "CAT", "BA", "GE", "HON",
    "UPS", "LMT", "RTX", "DE", "MMM", "UNP",
    # Телеком / комунальні послуги
    "T", "VZ", "TMUS", "CMCSA", "SO", "DUK", "NEE",
    # Нерухомість / матеріали
    "PLD", "AMT", "EQIX", "LIN", "APD", "SHW",
]

# Сектор кожного тікера — щоб бот міг обмежити, скільки позицій
# одночасно тримати в одному секторі (диверсифікація: кілька акцій
# однієї галузі зазвичай рухаються разом, тобто це по суті одна й та
# сама ставка, а не кілька незалежних).
TICKER_SECTORS = {
    **{t: "Технології" for t in (
        "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "AMD", "INTC",
        "CSCO", "ORCL", "ADBE", "CRM", "NFLX", "QCOM", "TXN", "IBM", "AVGO",
        "NOW", "UBER", "SHOP", "PYPL", "SNOW", "PLTR",
    )},
    **{t: "Фінанси" for t in (
        "JPM", "V", "MA", "BAC", "WFC", "GS", "MS", "C", "AXP", "BLK", "SCHW",
        "COF", "SPGI", "ICE",
    )},
    **{t: "Охорона здоров'я" for t in (
        "UNH", "JNJ", "ABBV", "MRK", "ABT", "TMO", "DHR", "BMY", "LLY", "PFE",
        "GILD", "AMGN", "CVS", "CI", "ISRG", "SYK", "MDT",
    )},
    **{t: "Споживчі товари" for t in (
        "PG", "KO", "PEP", "COST", "WMT", "HD", "LOW", "NKE", "MCD", "SBUX",
        "TGT", "DIS", "EL", "CL", "KMB",
    )},
    **{t: "Промисловість/енергетика" for t in (
        "XOM", "CVX", "COP", "SLB", "CAT", "BA", "GE", "HON",
        "UPS", "LMT", "RTX", "DE", "MMM", "UNP",
    )},
    **{t: "Телеком/комунальні" for t in (
        "T", "VZ", "TMUS", "CMCSA", "SO", "DUK", "NEE",
    )},
    **{t: "Нерухомість/матеріали" for t in (
        "PLD", "AMT", "EQIX", "LIN", "APD", "SHW",
    )},
}


def sector_of(ticker: str) -> str:
    return TICKER_SECTORS.get(ticker, "Інше")
