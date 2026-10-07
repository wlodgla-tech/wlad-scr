"""
dashboard_builder.py
======================
Будує статичну HTML-сторінку (docs/index.html) з поточним станом ринку
для одного або кількох тікерів одночасно (вкладки зверху, перемикання
без перезавантаження сторінки).
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import analyzer

DOCS_DIR = Path(__file__).parent / "docs"
ALERTS_LOG_FILE = DOCS_DIR / "alerts_log.json"
MAX_LOG_ENTRIES = 60


def _load_alerts_log() -> list:
    if ALERTS_LOG_FILE.exists():
        try:
            return json.loads(ALERTS_LOG_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return []
    return []


def append_alerts_log(new_events: list, ticker: str) -> list:
    log = _load_alerts_log()
    now = datetime.now(timezone.utc).isoformat()
    for ev in new_events:
        log.append({"time": now, "ticker": ticker, "type": ev["type"], "detail": ev["detail"]})
    log = log[-MAX_LOG_ENTRIES:]
    DOCS_DIR.mkdir(exist_ok=True)
    ALERTS_LOG_FILE.write_text(json.dumps(log, ensure_ascii=False, indent=2))
    return log


def _dot_class(event_type: str) -> str:
    if event_type in ("RSI_OVERSOLD", "NEAR_SUPPORT"):
        return "bull"
    if event_type in ("RSI_OVERBOUGHT", "NEAR_RESISTANCE"):
        return "bear"
    return "neutral"


def _safe_id(ticker: str, idx: int) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ticker).strip("-").lower()
    return f"t{idx}-{slug}" if slug else f"t{idx}"


def _ticker_tab(ticker: str, interval: str, df, result: dict, fair_value: dict,
                 upcoming_events: list, idx: int, alerts_for_ticker: list) -> tuple:
    tab_id = _safe_id(ticker, idx)

    labels = [str(t)[:16] for t in df["timestamp"].tail(120)]
    closes = [round(float(v), 4) for v in df["close"].tail(120)]
    sma20 = analyzer.pd.Series(closes).rolling(20).mean().round(4).where(lambda s: s.notna(), None).tolist()
    rsi_series = analyzer.compute_rsi(df["close"]).tail(120).round(2)
    rsi_series = rsi_series.where(rsi_series.notna(), None).tolist()

    events = result.get("events", [])
    if events:
        current_html = "".join(
            f'<div class="event-row"><span class="dot {_dot_class(ev["type"])}"></span>{ev["type"]}: {ev["detail"]}</div>'
            for ev in events
        )
    else:
        current_html = '<p class="empty">Зараз нічого особливого — індикатори в нейтральній зоні.</p>'

    rsi_val = result["rsi"]
    rsi_zone = "bull" if rsi_val < 30 else "bear" if rsi_val > 70 else "neutral"

    if fair_value:
        diff = fair_value["diff_pct"]
        diff_zone = "bear" if diff > 2 else "bull" if diff < -2 else "neutral"
        diff_word = "переоцінено" if diff > 0 else "недооцінено" if diff < 0 else "біля fair value"
        fundamentals_html = f"""
        <div class="panel">
          <h2>Фундаментал (EV/EBITDA)</h2>
          <div class="stat-strip">
            <div class="stat"><div class="val mono">${fair_value['market_price']}</div><div class="lbl">ринкова ціна</div></div>
            <div class="stat"><div class="val mono">${fair_value['fair_price']}</div><div class="lbl">fair value (модель)</div></div>
            <div class="stat {diff_zone}"><div class="val mono">{diff:+.1f}%</div><div class="lbl">{diff_word}</div></div>
          </div>
          <p class="empty" style="margin-top:10px;">Мультиплікатор: {fair_value['multiple_used']}× EBITDA (${fair_value['ebitda_m']}M). {fair_value['note']}</p>
        </div>"""
    else:
        fundamentals_html = """
        <div class="panel">
          <h2>Фундаментал (EV/EBITDA)</h2>
          <p class="empty">Недоступно для цього інструменту (крипта/форекс/фʼючерс не мають корпоративної звітності).</p>
        </div>"""

    if upcoming_events:
        events_html = "".join(
            f'<div class="event-row">{ev["type"]}: <span class="mono">{ev["date"][:16]}</span></div>'
            for ev in upcoming_events
        )
    else:
        events_html = '<p class="empty">Немає відомих найближчих подій (або недоступно для цього інструменту).</p>'
    events_panel_html = f"""
        <div class="panel">
          <h2>Найближчі корпоративні події</h2>
          {events_html}
        </div>"""

    if alerts_for_ticker:
        alerts_html = ""
        for entry in reversed(alerts_for_ticker[-15:]):
            ts = entry["time"][:16].replace("T", " ")
            alerts_html += (
                f'<div class="alert-row"><span class="alert-time mono">{ts}</span>'
                f'<span class="alert-type">{entry["type"]}</span>'
                f'<span class="alert-detail">{entry["detail"]}</span></div>\n'
            )
    else:
        alerts_html = '<p class="empty">Ще не було сповіщень — усе спокійно.</p>'

    nav_button_html = f'<button class="tab-btn" data-tab="{tab_id}" onclick="showTab(\'{tab_id}\')">{ticker}</button>'

    panel_html = f"""
  <div class="tab-content" id="tab-{tab_id}">
    <div class="stat-strip">
      <div class="stat"><div class="val mono">{result['price']:.4f}</div><div class="lbl">ціна ({interval})</div></div>
      <div class="stat {rsi_zone}"><div class="val mono">{rsi_val:.1f}</div><div class="lbl">RSI(14)</div></div>
    </div>
    <div class="panel">
      <h2>Графік (останні свічки)</h2>
      <canvas id="priceCanvas-{tab_id}" height="200"></canvas>
    </div>
    <div class="panel">
      <h2>RSI(14)</h2>
      <canvas id="rsiCanvas-{tab_id}" height="100"></canvas>
    </div>
    <div class="panel">
      <h2>Зараз (технічний погляд)</h2>
      {current_html}
    </div>
    {fundamentals_html}
    {events_panel_html}
    <div class="panel">
      <h2>Останні сповіщення ({ticker})</h2>
      {alerts_html}
    </div>
  </div>"""

    chart_js = f"""
charts['{tab_id}'] = function() {{
  const labels = {json.dumps(labels)};
  const closes = {json.dumps(closes)};
  const sma20 = {json.dumps(sma20)};
  const rsiData = {json.dumps(rsi_series)};
  const textColor = '#8A92A0', gridColor = '#262D38';
  new Chart(document.getElementById('priceCanvas-{tab_id}'), {{
    type: 'line',
    data: {{ labels, datasets: [
      {{ label: 'Ціна', data: closes, borderColor: '#9FB4D0', borderWidth: 1.5, pointRadius: 0, tension: 0.15 }},
      {{ label: 'SMA 20', data: sma20, borderColor: '#D9A62E', borderWidth: 1.5, pointRadius: 0, tension: 0.15 }}
    ]}},
    options: {{ responsive: true, plugins: {{ legend: {{ labels: {{ color: textColor, font: {{ size: 11 }} }} }} }},
      scales: {{ x: {{ ticks: {{ color: textColor, maxTicksLimit: 6, font: {{ size: 9 }} }}, grid: {{ color: gridColor }} }},
                 y: {{ ticks: {{ color: textColor, font: {{ size: 10 }} }}, grid: {{ color: gridColor }} }} }} }}
  }});
  new Chart(document.getElementById('rsiCanvas-{tab_id}'), {{
    type: 'line',
    data: {{ labels, datasets: [{{ label: 'RSI', data: rsiData, borderColor: '#9F7FD9', borderWidth: 1.5, pointRadius: 0, tension: 0.15 }}] }},
    options: {{ responsive: true, plugins: {{ legend: {{ display: false }} }},
      scales: {{ x: {{ ticks: {{ color: textColor, maxTicksLimit: 6, font: {{ size: 9 }} }}, grid: {{ color: gridColor }} }},
                 y: {{ min: 0, max: 100, ticks: {{ color: textColor, font: {{ size: 10 }} }}, grid: {{ color: gridColor }} }} }} }}
  }});
}};"""

    return nav_button_html, panel_html, chart_js


def build_multi(tabs: list, interval: str) -> None:
    DOCS_DIR.mkdir(exist_ok=True)

    full_log = _load_alerts_log()

    nav_buttons, panels, chart_scripts = [], [], []
    for idx, t in enumerate(tabs):
        alerts_for_ticker = [e for e in full_log if e.get("ticker") == t["ticker"]]
        nav_html, panel_html, chart_js = _ticker_tab(
            t["ticker"], interval, t["df"], t["result"], t["fair_value"],
            t["upcoming_events"], idx, alerts_for_ticker,
        )
        nav_buttons.append(nav_html)
        panels.append(panel_html)
        chart_scripts.append(chart_js)

    first_tab_id = _safe_id(tabs[0]["ticker"], 0)
    updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    title = tabs[0]["ticker"] if len(tabs) == 1 else f"{tabs[0]['ticker']} +{len(tabs) - 1}"

    html = HTML_TEMPLATE.format(
        title=title,
        updated=updated,
        nav_buttons="\n    ".join(nav_buttons),
        panels="\n".join(panels),
        chart_scripts="\n".join(chart_scripts),
        chart_inits="\n".join(f"charts['{_safe_id(t['ticker'], i)}']();" for i, t in enumerate(tabs)),
        first_tab_id=first_tab_id,
    )
    (DOCS_DIR / "index.html").write_text(html, encoding="utf-8")


def build(df, result: dict, ticker: str, interval: str, fair_value: dict = None, upcoming_events: list = None) -> None:
    build_multi(
        [{"ticker": ticker, "df": df, "result": result, "fair_value": fair_value, "upcoming_events": upcoming_events}],
        interval,
    )


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="uk">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} — моніторинг ринку</title>
<link rel="manifest" href="manifest.json">
<meta name="theme-color" content="#11151B">
<link rel="apple-touch-icon" href="icon-192.png">
<link rel="icon" href="icon-192.png">
