#!/usr/bin/env python3
"""
email_report.py — HTML 報告 + Gmail SMTP
支援：指數 / 殖利率 / 商品 / 匯率 / 市場情緒層
"""

import smtplib, logging
from email.mime.multipart import MIMEMultipart
from email.mime.text      import MIMEText
from email.mime.base      import MIMEBase
from email                import encoders
from pathlib              import Path

log = logging.getLogger(__name__)


# ── 格式化工具 ────────────────────────────────────────────────────────────────
def _fmt(v, d=2, sfx="", sign=False):
    try:
        if v is None: return "N/A"
        return f"{float(v):{'+' if sign else ''}.{d}f}{sfx}"
    except Exception: return "N/A"

def _pct_bg(v):
    try:
        f = float(v)
        return "#d4edda" if f >= -3 else ("#fff3cd" if f >= -10 else "#f8d7da")
    except Exception: return "#f5f5f5"

def _chg_color(v):
    try:
        f = float(v)
        return "#155724" if f > 0.05 else ("#721c24" if f < -0.05 else "#555")
    except Exception: return "#555"


# ── CSS ───────────────────────────────────────────────────────────────────────
CSS = """
body{margin:0;padding:0;font-family:'Segoe UI',Arial,sans-serif;background:#f0f2f5;}
.wrap{max-width:920px;margin:0 auto;padding:24px 18px;}
.data-table{width:100%;border-collapse:collapse;font-size:13px;background:#fff;
  border-radius:8px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.08);margin-bottom:16px;}
.data-table thead tr{background:#f8f9fa;}
.data-table th{padding:9px 12px;text-align:left;border-bottom:2px solid #dee2e6;
  color:#495057;font-size:12px;white-space:nowrap;}
.data-table td{padding:7px 12px;border-bottom:1px solid #f0f0f0;}
.tr{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap;}
.ts{color:#888;font-size:11px;}
.sec{font-size:15px;font-weight:700;color:#343a40;margin:20px 0 8px 0;
  border-left:4px solid #0066cc;padding-left:10px;}
.kpi-row{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:22px;}
.kpi{flex:1;min-width:145px;border-radius:10px;padding:14px 18px;
  text-align:center;box-shadow:0 1px 4px rgba(0,0,0,.1);}
.kpi-lbl{font-size:12px;font-weight:600;margin-bottom:3px;}
.kpi-val{font-size:30px;font-weight:800;margin:4px 0;}
.kpi-sub{font-size:11px;}
.gauge{width:100%;height:14px;background:#e9ecef;border-radius:7px;overflow:hidden;margin:4px 0;}
.gauge-fill{height:100%;border-radius:7px;transition:width .3s;}
.legend{font-size:11px;color:#666;margin-top:6px;}
.legend span{padding:2px 8px;border-radius:4px;margin:0 3px;}
.sent-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:18px;}
.sent-card{background:#fff;border-radius:10px;padding:14px 18px;
  box-shadow:0 1px 4px rgba(0,0,0,.08);}
.sent-card h4{margin:0 0 10px 0;font-size:13px;color:#495057;border-bottom:1px solid #f0f0f0;padding-bottom:6px;}
.sent-row{display:flex;justify-content:space-between;font-size:13px;margin:4px 0;}
.sent-val{font-weight:600;}
"""


# ── KPI 卡片 ──────────────────────────────────────────────────────────────────
def _kpi(bg, tc, icon, label, value, sub):
    return (
        f'<div class="kpi" style="background:{bg};color:{tc};">'
        f'<div class="kpi-lbl">{icon} {label}</div>'
        f'<div class="kpi-val">{value}</div>'
        f'<div class="kpi-sub">{sub}</div>'
        f'</div>'
    )


def _macro_kpis(vix, fg, yields):
    # VIX
    try:
        vf = float(vix)
        vbg,vtc,vlbl = ("#f8d7da","#721c24","極度恐慌⚡") if vf>=35 else \
                       (("#fff3cd","#856404","高波動⚠️") if vf>=25 else ("#d4edda","#155724","正常✅"))
    except Exception:
        vbg,vtc,vlbl = "#e2e3e5","#383d41","N/A"

    fg_s = int(fg.get("score") or 50)
    fg_l = fg.get("label","Neutral")
    if   fg_s<=25: fgbg,fgtc = "#f8d7da","#721c24"
    elif fg_s<=45: fgbg,fgtc = "#ffe5b4","#856404"
    elif fg_s<=55: fgbg,fgtc = "#e2e3e5","#383d41"
    elif fg_s<=75: fgbg,fgtc = "#d4edda","#155724"
    else:          fgbg,fgtc = "#c3e6cb","#0d4d1f"

    sp   = yields.get("spread_3m10y",{})
    sv   = sp.get("value")
    stc  = "#721c24" if sv is not None and float(sv)<0 else "#155724"
    sbg  = "#f8d7da" if sv is not None and float(sv)<0 else "#d4edda"

    return (
        f'<div class="kpi-row">'
        + _kpi(vbg, vtc, "😱", "CBOE VIX", _fmt(vix,2), vlbl)
        + _kpi(fgbg, fgtc, "🎯", "CNN Fear &amp; Greed", str(fg_s), fg_l)
        + _kpi(sbg, stc, "📉", "3M-10Y 殖利率利差", _fmt(sv,3,"%",sign=True), sp.get("label",""))
        + '</div>'
    )


# ── 情緒層 HTML ───────────────────────────────────────────────────────────────
def _sentiment_html(sentiment: dict) -> str:
    aa  = sentiment.get("aaii",   {})
    pc  = sentiment.get("pcr",    {})
    bn  = sentiment.get("breadth",{}).get("nyse",   {})
    bq  = sentiment.get("breadth",{}).get("nasdaq", {})
    ins = sentiment.get("insider",{})

    # AAII gauge (bullish vs bearish)
    bull = aa.get("bullish") or 0
    bear = aa.get("bearish") or 0
    neut = aa.get("neutral") or max(0, 100 - bull - bear)
    spread = aa.get("bull_bear_spread")

    def _srow(label, val, color="#333", bold=False):
        fw = "font-weight:700;" if bold else ""
        return (f'<div class="sent-row"><span>{label}</span>'
                f'<span class="sent-val" style="color:{color};{fw}">{val}</span></div>')

    # AAII 卡
    bull_color = "#155724" if bull >= 40 else ("#856404" if bull >= 30 else "#721c24")
    bear_color = "#721c24" if bear >= 40 else ("#856404" if bear >= 30 else "#155724")
    sp_color   = "#155724" if (spread or 0) > 5 else ("#721c24" if (spread or 0) < -5 else "#555")

    aaii_card = (
        '<div class="sent-card">'
        f'<h4>📊 AAII 散戶情緒 <span style="color:#888;font-weight:400;font-size:11px;">({aa.get("survey_date","N/A")})</span></h4>'
        + _srow("看多 Bullish",    _fmt(bull,1,"%"), bull_color, True)
        + _srow("中性 Neutral",    _fmt(neut,1,"%"), "#555")
        + _srow("看空 Bearish",    _fmt(bear,1,"%"), bear_color, True)
        + _srow("牛熊差 Spread",   _fmt(spread,1,"%",sign=True), sp_color, True)
        + f'<div style="margin-top:8px;font-size:12px;color:#555;background:#f8f9fa;'
          f'border-radius:6px;padding:6px 10px;">{aa.get("label","N/A")}</div>'
        + '</div>'
    )

    # PCR 卡
    pcr_total  = pc.get("total")
    pcr_color  = "#721c24" if (pcr_total or 0) >= 1.2 else \
                 ("#856404" if (pcr_total or 0) >= 0.9 else \
                 ("#155724" if (pcr_total or 0) >= 0.7 else "#a84300"))
    pcr_card = (
        '<div class="sent-card">'
        '<h4>📐 Put/Call Ratio <span style="color:#888;font-weight:400;font-size:11px;">(CBOE)</span></h4>'
        + _srow("Total P/C",   _fmt(pcr_total,  2), pcr_color, True)
        + _srow("Equity P/C",  _fmt(pc.get("equity"), 2), "#555")
        + _srow("Index P/C",   _fmt(pc.get("index"),  2), "#555")
        + f'<div style="margin-top:8px;font-size:12px;color:#555;background:#f8f9fa;'
          f'border-radius:6px;padding:6px 10px;">{pc.get("label","N/A")}</div>'
        + '</div>'
    )

    # Breadth 卡
    def _breadth_bar(pct, label_val):
        if pct is None: return ""
        color = "#28a745" if pct>=65 else ("#ffc107" if pct>=45 else "#dc3545")
        return (
            f'<div class="gauge"><div class="gauge-fill" '
            f'style="width:{min(pct,100):.0f}%;background:{color};"></div></div>'
            f'<div style="font-size:11px;color:#555;">{pct:.1f}%  {label_val}</div>'
        )

    nyse_bpct = bn.get("breadth_pct"); nq_bpct = bq.get("breadth_pct")
    breadth_card = (
        '<div class="sent-card">'
        '<h4>📈 市場寬度 Market Breadth</h4>'
        f'<div style="font-size:12px;color:#666;margin-bottom:4px;font-weight:600;">NYSE</div>'
        + _srow("上漲", str(bn.get("advances","N/A")), "#155724")
        + _srow("下跌", str(bn.get("declines","N/A")), "#721c24")
        + _breadth_bar(nyse_bpct, bn.get("label",""))
        + f'<div style="font-size:12px;color:#666;margin:8px 0 4px 0;font-weight:600;">Nasdaq</div>'
        + _srow("上漲", str(bq.get("advances","N/A")), "#155724")
        + _srow("下跌", str(bq.get("declines","N/A")), "#721c24")
        + _breadth_bar(nq_bpct, bq.get("label",""))
        + '</div>'
    )

    # Insider 卡
    bc = ins.get("buy_count",  0) or 0
    sc = ins.get("sell_count", 0) or 0
    total_ins = bc + sc or 1
    ins_color = "#155724" if bc > sc*1.5 else ("#721c24" if sc > bc*1.5 else "#555")
    insider_card = (
        '<div class="sent-card">'
        f'<h4>🔍 Insider 買賣 <span style="color:#888;font-weight:400;font-size:11px;">(SEC EDGAR 3天)</span></h4>'
        + _srow("買入申報", str(bc), "#155724", True)
        + _srow("賣出申報", str(sc), "#721c24", True)
        + _srow("總申報數", str(ins.get("total_filings","N/A")), "#555")
        + f'<div style="margin-top:8px;font-size:12px;color:#555;background:#f8f9fa;'
          f'border-radius:6px;padding:6px 10px;color:{ins_color};font-weight:600;">'
          f'{ins.get("net_bias","N/A")}</div>'
        + '</div>'
    )

    return (
        '<div class="sec">🧭 市場情緒層 Market Sentiment</div>'
        f'<div class="sent-grid">{aaii_card}{pcr_card}{breadth_card}{insider_card}</div>'
    )


# ── 各資料區段表格 ────────────────────────────────────────────────────────────
def _table(headers, rows_html):
    ths = "".join(f"<th>{h}</th>" for h in headers)
    return (
        f'<table class="data-table"><thead><tr>{ths}</tr></thead>'
        f'<tbody>{rows_html}</tbody></table>'
    )

def _idx_section(indices, defs):
    region_rows, prev_r = {}, None
    region_icons = {"亞太":"🌏","歐洲":"🌍","美股":"🗽","台股個股":"📈"}
    for idx in defs:
        r = idx["region"]
        region_rows.setdefault(r, "")
        d   = indices.get(idx["key"], {})
        v   = d.get("value"); cp = d.get("change_pct")
        h   = d.get("high52w"); ph = d.get("pct_from_high")
        try: dec = 0 if float(v)>=1000 else 2
        except Exception: dec = 2
        region_rows[r] += (
            f'<tr><td>{idx["name"]}</td><td class="ts">{idx["symbol"]}</td>'
            f'<td class="tr">{_fmt(v,dec)}</td>'
            f'<td class="tr" style="color:{_chg_color(cp)};font-weight:600;">{_fmt(cp,2,"%",True)}</td>'
            f'<td class="tr">{_fmt(h,dec)}</td>'
            f'<td class="tr" style="background:{_pct_bg(ph)};color:{_chg_color(ph)};font-weight:600;">{_fmt(ph,2,"%",True)}</td>'
            f'</tr>'
        )
    html = '<div class="sec">📊 全球股票指數</div>'
    for r, rrows in region_rows.items():
        html += (f'<p style="margin:10px 0 4px;font-size:13px;font-weight:600;color:#495057;">'
                 f'{region_icons.get(r,"📊")} {r}</p>'
                 + _table(["指數名稱","Symbol","現價","單日%","52W High","距高點%"], rrows))
    return html

def _yield_section(yields, defs):
    sp = yields.get("spread_3m10y", {})
    sv = sp.get("value")
    stc = "#721c24" if sv is not None and float(sv)<0 else "#155724"
    rows = ""
    for y in defs:
        d = yields.get(y["key"],{})
        cp_tc = _chg_color(d.get("change_pct"))
        rows += (f'<tr><td>{y["name"]}</td><td class="ts">{y["tenor"]}</td>'
                 f'<td class="tr">{_fmt(d.get("value"),3,"%")}</td>'
                 f'<td class="tr" style="color:{cp_tc};font-weight:600;">{_fmt(d.get("change_pct"),3,"%",True)}</td>'
                 f'</tr>')
    rows += (f'<tr style="background:#f0f0f0;font-weight:700;">'
             f'<td>3M-10Y 利差</td><td class="ts">Spread</td>'
             f'<td class="tr" style="color:{stc};">{_fmt(sv,3,"%",True)}</td>'
             f'<td class="tr" style="color:{stc};">{sp.get("label","")}</td>'
             f'</tr>')
    return '<div class="sec">📉 殖利率曲線（美債）</div>' + _table(["名稱","期限","殖利率%","日變動"], rows)

def _comm_section(commodities, defs):
    rows = ""
    for c in defs:
        d   = commodities.get(c["key"],{})
        v   = d.get("value"); cp = d.get("change_pct")
        h   = d.get("high52w"); ph = d.get("pct_from_high")
        rows += (f'<tr><td>{c["name"]}</td><td class="ts">{c["unit"]}</td>'
                 f'<td class="tr">{_fmt(v,4)}</td>'
                 f'<td class="tr" style="color:{_chg_color(cp)};font-weight:600;">{_fmt(cp,2,"%",True)}</td>'
                 f'<td class="tr">{_fmt(h,4)}</td>'
                 f'<td class="tr" style="background:{_pct_bg(ph)};color:{_chg_color(ph)};font-weight:600;">{_fmt(ph,2,"%",True)}</td>'
                 f'</tr>')
    return '<div class="sec">🥇 商品行情</div>' + _table(["商品","單位","現價","單日%","52W High","距高點%"], rows)

def _fx_section(fx_rates, defs):
    rows = ""
    for fx in defs:
        d = fx_rates.get(fx["key"],{})
        rows += (f'<tr><td>{fx["name"]}</td><td class="ts">{fx["symbol"]}</td>'
                 f'<td class="tr">{_fmt(d.get("value"),4)}</td>'
                 f'<td class="tr" style="color:{_chg_color(d.get("change_pct"))};font-weight:600;">{_fmt(d.get("change_pct"),3,"%",True)}</td>'
                 f'</tr>')
    return '<div class="sec">💱 全球匯率</div>' + _table(["匯率對","Symbol","現值","日漲跌%"], rows)

def build_ta_html(ta_results: list) -> str:
    """
    渲染指數技術分析表格（RSI / MACD / MA200 / ATH）。
    直接嵌入 build_html_report() 回傳的 HTML 字串中。
    """
    if not ta_results:
        return ""

    def rsi_color(rsi):
        if rsi is None: return "#888"
        if rsi >= 70:   return "#e74c3c"   # 紅：超買
        if rsi <= 30:   return "#27ae60"   # 綠：超賣
        if rsi >= 60:   return "#f39c12"   # 橙：偏高
        if rsi <= 40:   return "#3498db"   # 藍：偏低
        return "#555"

    def macd_color(sig):
        if "Bullish" in sig: return "#27ae60"
        if "Bearish" in sig: return "#e74c3c"
        return "#888"

    def ma200_color(pct):
        if pct is None: return "#888"
        if pct >= 10:   return "#27ae60"
        if pct >= 3:    return "#2ecc71"
        if pct >= -3:   return "#888"
        if pct >= -10:  return "#f39c12"
        return "#e74c3c"

    def ath_color(pct):
        if pct is None: return "#888"
        if pct >= -2:   return "#8e44ad"
        if pct >= -10:  return "#27ae60"
        if pct >= -20:  return "#f39c12"
        if pct >= -30:  return "#e67e22"
        return "#e74c3c"

    rows = ""
    for r in ta_results:
        close_disp = f"{r['close']:.2f}" if r["close"] else "N/A"
        rows += f"""
        <tr>
          <td><b>{r['name']}</b></td>
          <td style="text-align:right">{close_disp}</td>
          <td style="color:{rsi_color(r['rsi'])};text-align:center">
            <b>{r['rsi_label']}</b>
          </td>
          <td style="color:{macd_color(r['macd_signal'])};text-align:center">
            {r['macd_signal']}
          </td>
          <td style="color:{ma200_color(r['ma200_pct'])};text-align:center">
            {r['ma200_label']}
          </td>
          <td style="color:{ath_color(r['ath_pct'])};text-align:center">
            {r['ath_label']}
          </td>
        </tr>"""

    return f"""
    <h2 style="font-family:sans-serif;border-bottom:2px solid #2c3e50;
               padding-bottom:6px;margin-top:32px">
      Index Technical Analysis
    </h2>
    <table style="border-collapse:collapse;width:100%;font-family:monospace;font-size:13px">
      <thead>
        <tr style="background:#2c3e50;color:white">
          <th style="padding:8px 12px;text-align:left">Index</th>
          <th style="padding:8px 12px;text-align:right">Close</th>
          <th style="padding:8px 12px;text-align:center">RSI (14)</th>
          <th style="padding:8px 12px;text-align:center">MACD</th>
          <th style="padding:8px 12px;text-align:center">vs MA200</th>
          <th style="padding:8px 12px;text-align:center">vs ATH</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
    <p style="font-size:11px;color:#888;margin-top:4px">
      RSI: &gt;70 Overbought | &lt;30 Oversold |
      MA200: &gt;+10% Strong Bull | &lt;-10% Bear Zone |
      ATH: &lt;2% off = ATH Zone | &lt;-30% = Deep Bear
    </p>
    """

def build_corr_html(corr: dict) -> str:
    """
    渲染 30 日滾動相關係數矩陣（熱圖色彩）。
    直接嵌入 build_html_report() 回傳的 HTML 字串中。
    """
    if not corr or corr.get("matrix") is None:
        return ""

    mat    = corr["matrix"]
    cols   = list(mat.columns)
    as_of  = corr.get("as_of", "N/A")
    win    = corr.get("window", 30)
    interp = corr.get("interpretation", "")

    def corr_bg(val, is_diagonal=False):
        """相關係數 → 背景/前景色"""
        if is_diagonal:
            return "#2c3e50", "white"
        v = float(val)
        if v >= 0.85: return "#1a5276", "white"   # 深藍：高度同步
        if v >= 0.70: return "#2980b9", "white"   # 藍
        if v >= 0.50: return "#85c1e9", "#333"    # 淡藍
        if v >= 0.30: return "#f0f3f4", "#333"    # 近白
        if v >= 0.0:  return "#fdebd0", "#333"    # 淡橙
        return "#e59866", "white"                  # 橙：負相關

    header_cells = "".join(
        f'<th style="padding:8px 14px;background:#2c3e50;color:white">{c}</th>'
        for c in cols
    )
    header_row = (
        f'<tr><th style="background:#2c3e50;color:white;padding:8px"></th>' +
        header_cells + "</tr>"
    )

    data_rows = ""
    for idx in mat.index:
        cells = ""
        for c in cols:
            val    = mat.loc[idx, c]
            is_d   = (idx == c)
            bg, fg = corr_bg(val, is_d)
            disp   = "—" if is_d else f"{val:.3f}"
            fw     = "bold" if is_d else "normal"
            cells += (
                f'<td style="padding:8px 14px;text-align:center;' +
                f'background:{bg};color:{fg};font-weight:{fw}">{disp}</td>'
            )
        data_rows += (
            f'<tr><td style="padding:8px 14px;font-weight:bold;' +
            f'background:#ecf0f1">{idx}</td>{cells}</tr>'
        )

    return f"""
    <h2 style="font-family:sans-serif;border-bottom:2px solid #2c3e50;
               padding-bottom:6px;margin-top:32px">
      Index Correlation Matrix
      <span style="font-size:13px;font-weight:normal;color:#888">
        ({win}D Rolling Returns, as of {as_of})
      </span>
    </h2>
    <table style="border-collapse:collapse;font-family:monospace;font-size:13px">
      <thead>{header_row}</thead>
      <tbody>{data_rows}</tbody>
    </table>
    <p style="font-size:12px;color:#555;margin-top:6px">
      <b>解讀：</b> {interp}
    </p>
    <p style="font-size:11px;color:#888">
      <span style="background:#1a5276;color:white;padding:2px 6px">>=0.85 高度同步</span>
      <span style="background:#2980b9;color:white;padding:2px 6px">0.70–0.85</span>
      <span style="background:#85c1e9;color:#333;padding:2px 6px">0.50–0.70</span>
      <span style="background:#f0f3f4;color:#333;padding:2px 6px;border:1px solid #ddd">0.30–0.50</span>
      <span style="background:#e59866;color:white;padding:2px 6px">&lt;0.30 低相關</span>
    </p>
    """


# ══════════════════════════════════════════════════════════════════════════════
# 主報告
# ══════════════════════════════════════════════════════════════════════════════

def build_html_report(run_time, vix, fg,
                      indices, global_indices_def,
                      yields,  yield_curve_def,
                      commodities, commodities_def,
                      fx_rates, fx_def,
                      sentiment,
                      ta_results=None, corr=None) -> str:

    header = (
        '<div style="background:linear-gradient(135deg,#003087 0%,#0066cc 100%);'
        'color:#fff;padding:22px 32px;text-align:center;">'
        '<div style="font-size:24px;font-weight:700;">📊 Global Market Report</div>'
        f'<div style="margin-top:5px;font-size:13px;opacity:.85;">{run_time}</div>'
        '</div>'
    )
    legend = (
        '<div class="legend">'
        '距高點色碼：'
        '<span style="background:#d4edda;">🟢 &gt;-3%</span>'
        '<span style="background:#fff3cd;">🟡 -3%~-10%</span>'
        '<span style="background:#f8d7da;">🔴 &lt;-10%</span>'
        '</div>'
    )
    footer = (
        '<div style="margin-top:28px;padding-top:14px;border-top:1px solid #dee2e6;'
        f'font-size:11px;color:#aaa;text-align:center;">'
        f'本報告由自動化程式產生，僅供參考，不構成投資建議。Generated at {run_time}'
        '</div>'
    )
    ta_section   = build_ta_html(ta_results) if ta_results else ""
    corr_section = build_corr_html(corr)     if corr       else ""
    body = (
        _macro_kpis(vix, fg, yields)
        + _sentiment_html(sentiment)
        + _idx_section(indices, global_indices_def)
        + _yield_section(yields, yield_curve_def)
        + _comm_section(commodities, commodities_def)
        + _fx_section(fx_rates, fx_def)
    )

    return (
        f'<!DOCTYPE html><html lang="zh-TW">'
        f'<head><meta charset="UTF-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>Global Market Report — {run_time}</title>'
        f'<style>{CSS}</style></head>'
        f'<body>{header}<div class="wrap">{body}'
        f'{ta_section}'
        f'{corr_section}'
        f'{legend}{footer}</div></body></html>'
    )


# ── Email 寄送 ────────────────────────────────────────────────────────────────
def send_email_report(sender, app_password, recipient, subject, html_body, attachment_path=None):
    msg = MIMEMultipart("alternative")
    msg["From"] = sender; msg["To"] = recipient; msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    if attachment_path:
        att = Path(attachment_path)
        if att.exists():
            with open(att, "rb") as f:
                part = MIMEBase("application", "octet-stream"); part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f"attachment; filename={att.name}")
            msg.attach(part); log.info(f"[Email] Attached: {att.name}")
    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as s:
            s.ehlo(); s.starttls()
            s.login(sender, app_password.replace(" ",""))
            s.sendmail(sender, recipient, msg.as_string())
        log.info(f"[Email] Sent to {recipient}")
    except smtplib.SMTPAuthenticationError:
        log.error("[Email] Auth failed. Check Gmail App Password.")
    except Exception as e:
        log.error(f"[Email] Error: {e}")
