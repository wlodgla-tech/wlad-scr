"""
dashboard_builder.py
======================
Будує статичну HTML-сторінку (docs/index.html) з поточним станом ринку
для одного або кількох тікерів одночасно (вкладки зверху, перемикання
без перезавантаження сторінки). Для кожного тікера: ціна, RSI, графік з
SMA, рівні підтримки/опору, фундаментальний погляд, найближчі корпоративні
події. Знизу — спільна історія останніх сповіщень по всіх тікерах.

Сторінка самодостатня (Chart.js через CDN) і призначена для публікації
через GitHub Pages — тоді вона має власну постійну адресу, яку можна
відкрити з телефону і навіть "встановити" як іконку на головний екран
(Add to Home Screen / Install app).
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
    """Дописує нові події в історію (для показу на дашборді) і повертає
    оновлений список. Викликати лише для подій, які справді щойно сталися
    (після дедуплікації в main.py), щоб історія не дублювалась."""
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
    """Повертає (nav_button_html, panel_html, chart_js) для одного тікера."""
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


def _trading_panel_html(trading_summary: dict | None) -> str:
    """Панель над вкладками: статус автоторгівлі (Trading 212), якщо вона
    налаштована. Якщо trading_summary=None — торгівля не налаштована
    (немає TRADING212_API_KEY), панель взагалі не показуємо."""
    if not trading_summary:
        return ""

    if trading_summary["dry_run"]:
        mode_label, mode_class = "ТЕСТ (dry-run) — реальних ордерів немає", "neutral"
    elif trading_summary["mode"] == "live":
        mode_label, mode_class = "LIVE — реальні гроші", "bear"
    else:
        mode_label, mode_class = "DEMO — віртуальний рахунок", "bull"

    positions = trading_summary["positions"]
    if positions:
        rows = []
        sl = trading_summary["stop_loss_pct"] / 100
        for t, p in positions.items():
            if p.get("side") == "SHORT":
                trough = p.get("trough_price", p["buy_price"])
                rows.append(
                    f'<div class="event-row">🔻 ШОРТ {t}: {p["qty"]} шт. від {p["buy_price"]:.4f} '
                    f'(трейлінг стоп-лос ≈{trough * (1 + sl):.4f}, мінімум ціни {trough:.4f})</div>'
                )
            else:
                peak = p.get("peak_price", p["buy_price"])
                rows.append(
                    f'<div class="event-row">{t}: {p["qty"]} шт. по {p["buy_price"]:.4f} '
                    f'(трейлінг стоп-лос ≈{peak * (1 - sl):.4f}, пік ціни {peak:.4f})</div>'
                )
        positions_html = "".join(rows)
    else:
        positions_html = '<p class="empty">Немає відкритих позицій.</p>'

    pause_html = ""
    if trading_summary.get("paused_for_drawdown"):
        pause_html = (
            '<div class="stat bear" style="margin-top:10px; padding:10px 12px; border-radius:6px;">'
            '⏸ Нові купівлі на паузі — спрацював запобіжник від серії збитків. '
            'Продажі й стоп-лоси продовжують працювати.</div>'
        )

    log = trading_summary["trade_log"]
    if log:
        log_html = ""
        for e in log:
            ts = e["time"][:16].replace("T", " ")
            ok_word = "" if e.get("ok") else " (НЕ ВДАЛОСЯ)"
            dry_word = " [dry-run]" if e.get("dry_run") else ""
            log_html += (
                f'<div class="alert-row"><span class="alert-time mono">{ts}</span>'
                f'<span class="alert-type">{e["action"]} {e["ticker"]}</span>'
                f'<span class="alert-detail">{e.get("reason", "")}{ok_word}{dry_word}</span></div>\n'
            )
    else:
        log_html = '<p class="empty">Угод ще не було.</p>'

    return f"""
<div class="panel">
  <h2>Автоторгівля (Trading 212)</h2>
  <div class="stat-strip">
    <div class="stat {mode_class}"><div class="val mono" style="font-size:14px;">{mode_label}</div><div class="lbl">режим</div></div>
    <div class="stat"><div class="val mono">£{trading_summary['daily_spent_gbp']:.2f} / £{trading_summary['max_daily_gbp']:.0f}</div><div class="lbl">витрачено сьогодні</div></div>
    <div class="stat"><div class="val mono">{trading_summary.get('open_positions_count', 0)} / {trading_summary.get('max_open_positions', '—')}</div><div class="lbl">відкритих позицій</div></div>
    <div class="stat {'bull' if trading_summary.get('cumulative_pnl_gbp', 0) >= 0 else 'bear'}"><div class="val mono">£{trading_summary.get('cumulative_pnl_gbp', 0):+.2f}</div><div class="lbl">сукупний P&amp;L</div></div>
  </div>
  <p class="empty">Макс. £{trading_summary['max_per_trade_gbp']:.0f} на угоду · трейлінг стоп-лос {trading_summary['stop_loss_pct']:.0f}%</p>
  {pause_html}
  <h2 style="margin-top:14px;">Відкриті позиції</h2>
  {positions_html}
  <h2 style="margin-top:14px;">Останні угоди</h2>
  {log_html}
</div>"""


def build_multi(tabs: list, interval: str, trading_summary: dict | None = None) -> None:
    """tabs: список dict з ключами ticker, df, result, fair_value, upcoming_events.
    Генерує docs/index.html з вкладками (по одній на тікер). trading_summary —
    опціональний dict від main.py з поточним станом автоторгівлі."""
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
        trading_panel=_trading_panel_html(trading_summary),
        nav_buttons="\n    ".join(nav_buttons),
        panels="\n".join(panels),
        chart_scripts="\n".join(chart_scripts),
        chart_inits="\n".join(f"charts['{_safe_id(t['ticker'], i)}']();" for i, t in enumerate(tabs)),
        first_tab_id=first_tab_id,
    )
    (DOCS_DIR / "index.html").write_text(html, encoding="utf-8")


def build(df, result: dict, ticker: str, interval: str, fair_value: dict = None, upcoming_events: list = None) -> None:
    """Сумісність зі старим викликом для одного тікера — обгортка над build_multi."""
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
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600;700&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
<style>
  :root{{
    --bg:#11151B; --panel:#161B22; --panel-2:#1C222B; --line:#262D38;
    --text:#E7E9EC; --muted:#8A92A0;
    --bull:#2FA86E; --bull-soft:#1A2E25;
    --bear:#C1452F; --bear-soft:#2E1C18;
    --neutral:#D9A62E; --neutral-soft:#2E2718;
  }}
  *{{box-sizing:border-box;}}
  body{{margin:0; background:var(--bg); color:var(--text); font-family:'IBM Plex Sans',sans-serif;}}
  .mono{{font-family:'IBM Plex Mono',monospace;}}
  .disclaimer{{background:var(--neutral-soft); border-bottom:1px solid #3A3220; padding:12px 20px; font-size:12px; color:#E6CE8F; line-height:1.5;}}
  .app{{max-width:760px; margin:0 auto; padding:16px 16px 60px;}}
  .header-row{{display:flex; justify-content:space-between; align-items:baseline; margin-bottom:10px;}}
  h1{{font-size:18px; margin:0;}}
  .updated{{font-size:11.5px; color:var(--muted);}}
  .tabs-nav{{display:flex; gap:6px; overflow-x:auto; padding-bottom:10px; margin-bottom:6px; -webkit-overflow-scrolling:touch;}}
  .tab-btn{{flex:0 0 auto; background:var(--panel); color:var(--muted); border:1px solid var(--line); border-radius:999px;
            padding:7px 14px; font-size:12.5px; font-family:'IBM Plex Mono',monospace; font-weight:600; white-space:nowrap;}}
  .tab-btn.active{{background:var(--neutral-soft); color:var(--neutral); border-color:#4A3F22;}}
  .tab-content{{display:none;}}
  .tab-content.active{{display:block;}}
  .stat-strip{{display:flex; gap:10px; margin:16px 0;}}
  .stat{{flex:1; background:var(--panel); border:1px solid var(--line); border-radius:6px; padding:14px; text-align:center;}}
  .stat .val{{font-size:22px; font-weight:700;}} .stat .lbl{{font-size:11px; color:var(--muted); margin-top:2px;}}
  .stat.bull .val{{color:var(--bull);}} .stat.bear .val{{color:var(--bear);}} .stat.neutral .val{{color:var(--neutral);}}
  .panel{{background:var(--panel); border:1px solid var(--line); border-radius:6px; padding:16px; margin-bottom:14px;}}
  .panel h2{{font-size:12px; color:var(--muted); margin:0 0 10px; font-weight:600;}}
  .event-row{{font-size:13px; padding:8px 0; border-bottom:1px solid var(--line);}}
  .event-row:last-child{{border-bottom:none;}}
  .dot{{display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:8px;}}
  .dot.bull{{background:var(--bull);}} .dot.bear{{background:var(--bear);}} .dot.neutral{{background:var(--neutral);}}
  .alert-row{{display:flex; gap:8px; font-size:12px; padding:7px 0; border-bottom:1px solid var(--line); flex-wrap:wrap;}}
  .alert-time{{color:var(--muted); min-width:110px;}}
  .alert-type{{color:var(--neutral); font-weight:600;}}
  .empty{{color:var(--muted); font-size:13px; margin:4px 0;}}
</style>
</head>
<body>
<div class="disclaimer"><b>Не фінансова консультація.</b> Це автоматичні відмітки технічних індикаторів з історичних даних, не прогноз і не торгові сигнали. Рішення — ваше, торгівля несе ризик втрати коштів.</div>
<div class="app">
  <div class="header-row"><h1>Market Monitor</h1><span class="updated mono">Оновлено: {updated}</span></div>
  {trading_panel}
  <div class="tabs-nav">
    {nav_buttons}
  </div>
{panels}
</div>
<script>
const charts = {{}};
{chart_scripts}

function showTab(id) {{
  document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
  document.getElementById('tab-' + id).classList.add('active');
  document.querySelector('.tab-btn[data-tab="' + id + '"]').classList.add('active');
}}

{chart_inits}
showTab('{first_tab_id}');

if ('serviceWorker' in navigator) {{ navigator.serviceWorker.register('service-worker.js').catch(() => {{}}); }}
</script>
</body>
</html>
"""
