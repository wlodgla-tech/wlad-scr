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

# Лондонська біржа (LSE): великі ліквідні компанії FTSE 100. Суфікс ".L" —
# формат Yahoo Finance. ЛИШЕ віртуальна торгівля (dry-run): бот не
# відправляє реальних ордерів по цих акціях. Ціни Yahoo віддає в пенсах —
# бот переводить їх у фунти автоматично (див. main.py).
UK_UNIVERSE = [
    "SHEL.L", "AZN.L", "HSBA.L", "ULVR.L", "BP.L", "GSK.L", "RIO.L", "BATS.L",
    "DGE.L", "LSEG.L", "REL.L", "NG.L", "VOD.L", "LLOY.L", "BARC.L", "NWG.L",
    "PRU.L", "AAL.L", "GLEN.L", "BA.L", "RR.L", "CPG.L", "TSCO.L", "STAN.L",
    "EXPN.L", "FLTR.L", "ABF.L", "III.L", "IMB.L", "SSE.L", "ANTO.L", "LGEN.L",
    "AV.L", "NXT.L", "WPP.L", "RKT.L", "HLMA.L", "SGE.L", "INF.L", "IAG.L",
]
TRADING_UNIVERSE = TRADING_UNIVERSE + UK_UNIVERSE

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
    **{t: "Фінанси" for t in (
        "HSBA.L", "LLOY.L", "BARC.L", "NWG.L", "PRU.L", "STAN.L", "LSEG.L",
        "III.L", "LGEN.L", "AV.L",
    )},
    **{t: "Охорона здоров'я" for t in ("AZN.L", "GSK.L")},
    **{t: "Споживчі товари" for t in (
        "ULVR.L", "BATS.L", "DGE.L", "TSCO.L", "ABF.L", "NXT.L", "RKT.L", "IMB.L",
    )},
    **{t: "Промисловість/енергетика" for t in (
        "SHEL.L", "BP.L", "RIO.L", "AAL.L", "GLEN.L", "BA.L", "RR.L", "ANTO.L",
        "HLMA.L", "IAG.L", "CPG.L",
    )},
    **{t: "Телеком/комунальні" for t in ("NG.L", "VOD.L", "SSE.L")},
    **{t: "Технології" for t in ("REL.L", "EXPN.L", "SGE.L", "INF.L", "WPP.L", "FLTR.L")},
}


# ---------------------------------------------------------------------------
# Розширення до S&P 500 + FTSE 100 (списки беруться з Вікіпедії).
# Керується змінною UNIVERSE_EXTRA (за замовчуванням "SP500,FTSE100";
# порожнє значення = лишити тільки ручний список вище).
# Якщо Вікіпедія недоступна — беремо кеш universe_cache.json, а якщо й його
# нема — працюємо зі старим ручним списком (бот не ламається).
# ---------------------------------------------------------------------------
import json as _json
import os as _os

_GICS_MAP = {
    "Information Technology": "Технології", "Communication Services": "Технології",
    "Financials": "Фінанси", "Health Care": "Охорона здоров'я",
    "Consumer Staples": "Споживчі товари", "Consumer Discretionary": "Споживчі товари",
    "Industrials": "Промисловість/енергетика", "Energy": "Промисловість/енергетика",
    "Materials": "Нерухомість/матеріали", "Real Estate": "Нерухомість/матеріали",
    "Utilities": "Телеком/комунальні",
}
_CACHE = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "universe_cache.json")


def _wiki_rows(url, sym_words, sector_words):
    import requests
    from html.parser import HTMLParser
    html = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (market-monitor)"}, timeout=30).text

    class P(HTMLParser):
        def __init__(self):
            super().__init__()
            self.tables, self.row, self.cell, self.in_cell, self.cur = [], None, "", False, None

        def handle_starttag(self, tag, attrs):
            if tag == "table":
                self.cur = []
            elif tag == "tr" and self.cur is not None:
                self.row = []
            elif tag in ("td", "th") and self.row is not None:
                self.in_cell, self.cell = True, ""

        def handle_endtag(self, tag):
            if tag in ("td", "th") and self.in_cell:
                self.row.append(self.cell.strip()); self.in_cell = False
            elif tag == "tr" and self.row is not None and self.cur is not None:
                self.cur.append(self.row); self.row = None
            elif tag == "table" and self.cur is not None:
                self.tables.append(self.cur); self.cur = None

        def handle_data(self, data):
            if self.in_cell:
                self.cell += data

    p = P(); p.feed(html)
    for t in p.tables:
        if len(t) < 20:
            continue
        hdr = [h.lower() for h in t[0]]
        si = next((i for i, h in enumerate(hdr) if any(w in h for w in sym_words)), None)
        if si is None:
            continue
        gi = next((i for i, h in enumerate(hdr) if any(w in h for w in sector_words)), None)
        return [(r[si], r[gi] if gi is not None and len(r) > gi else "") for r in t[1:] if len(r) > si and r[si]]
    raise RuntimeError("table not found: " + url)


def _load_extra():
    """Повертає {ticker: sector}."""
    spec = _os.environ.get("UNIVERSE_EXTRA", "SP500,FTSE100")
    names = [x.strip().upper() for x in spec.split(",") if x.strip()]
    out = {}
    for n in names:
        try:
            if n == "SP500":
                for sym, sec in _wiki_rows("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", ("symbol", "ticker"), ("gics sector",)):
                    out[sym.replace(".", "-")] = _GICS_MAP.get(sec, "")
            elif n == "FTSE100":
                for sym, sec in _wiki_rows("https://en.wikipedia.org/wiki/FTSE_100_Index", ("ticker", "epic"), ("industry", "sector")):
                    out[sym.strip().rstrip(".").replace(".", "-") + ".L"] = ""
        except Exception as e:
            print(f"[watchlist] Не вдалось завантажити {n}: {e}")
    if out:
        try:
            with open(_CACHE, "w") as f:
                _json.dump(out, f)
        except Exception:
            pass
    elif names:
        try:
            with open(_CACHE) as f:
                out = _json.load(f)
            print(f"[watchlist] Беру кеш: {len(out)} тікерів")
        except Exception:
            pass
    return out


_EXTRA = _load_extra()
if _EXTRA:
    _have = set(TRADING_UNIVERSE)
    TRADING_UNIVERSE = TRADING_UNIVERSE + [t for t in _EXTRA if t not in _have]
    for _t, _s in _EXTRA.items():
        if _s and _t not in TICKER_SECTORS:
            TICKER_SECTORS[_t] = _s
print(f"[watchlist] Усього акцій у списку: {len(TRADING_UNIVERSE)}")


def sector_of(ticker: str) -> str:
    # невідомий сектор = окремий "сектор" на кожен тікер, щоб ліміт 3 на
    # сектор не блокував усі невідомі акції разом
    return TICKER_SECTORS.get(ticker) or ("Інше:" + ticker)
