import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _env(key, default=""):
    return os.environ.get(key, default)


def _fmt(value, digits=2):
    if value is None:
        return "—"
    try:
        return f"{float(value):.{digits}f}"
    except Exception:
        return str(value)


# ──────────────────────────────────────────────────────────
#  Plain-text body
# ──────────────────────────────────────────────────────────

def _build_text(results: list[dict]) -> str:
    lines = []
    now   = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines.append(f"Bull Call Debit 掃描報告  {now}")
    lines.append("=" * 60)

    triggered = [r for r in results if r.get("triggered")]
    errors    = [r for r in results if r.get("error")]

    lines.append(f"總計掃描：{len(results)} 支")
    lines.append(f"符合訊號：{len(triggered)} 支  {[r['symbol'] for r in triggered]}")
    lines.append(f"擷取錯誤：{len(errors)} 支  {[r['symbol'] for r in errors]}")
    lines.append("")

    for r in results:
        sym = r["symbol"]
        lines.append("─" * 60)
        if r.get("error"):
            lines.append(f"[ERROR] {sym}: {r['error']}")
            continue

        status = "✅ 符合訊號" if r.get("triggered") else "❌ 未達條件"
        lines.append(f"{sym}  {status}")
        lines.append(f"  現價:      {_fmt(r.get('price'), 2)}")
        lines.append(f"  RSI14:     {_fmt(r.get('rsi14'), 2)}  (✅ 50~70)")
        lines.append(f"  IVR(52W):  {_fmt(r.get('ivr_52w'), 1)}%  (✅ < 30%)")
        lines.append(f"  IV/HV(30): {_fmt(r.get('iv_hv30'), 3)}  (✅ < 1.2)")
        lines.append(f"  BB %B:     {_fmt(r.get('bb_pct_b'), 1)}%  (✅ 30~80%)")
        if r.get("earnings_date"):
            lines.append(f"  財報日:    {r['earnings_date']} ({r.get('earnings_days')} 天後)")
        lines.append("")
        for item in r.get("passed_list", []):
            lines.append(f"  {item}")
        for item in r.get("failed_list", []):
            lines.append(f"  {item}")
        lines.append("")

    lines.append("=" * 60)
    lines.append("⚠️  本報告僅供參考，不構成投資建議，請自行評估風險。")
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────
#  HTML helpers
# ──────────────────────────────────────────────────────────

def _cell_ok(condition: bool) -> str:
    return "background:#d4edda;" if condition else "background:#f8d7da;"


def _row_color(triggered, error):
    if error:     return "#fff3cd"
    if triggered: return "#d1f7d6"
    return "#ffffff"


def _cond_html(passed_list, failed_list) -> str:
    """將 passed_list（綠）和 failed_list（紅）合併成 HTML 清單。"""
    items = ""
    for item in passed_list:
        items += (
            f'<li style="color:#155724;list-style:none;padding:1px 0">'
            f'{item}</li>'
        )
    for item in failed_list:
        items += (
            f'<li style="color:#721c24;list-style:none;padding:1px 0">'
            f'{item}</li>'
        )
    if not items:
        return ""
    return f'<ul style="margin:4px 0;padding:0;font-size:12px">{items}</ul>'


# ──────────────────────────────────────────────────────────
#  HTML body
# ──────────────────────────────────────────────────────────

def _build_html(results: list[dict]) -> str:
    now       = datetime.now().strftime("%Y-%m-%d %H:%M")
    triggered = [r for r in results if r.get("triggered")]
    errors    = [r for r in results if r.get("error")]

    rows = []
    for r in results:
        sym = r["symbol"]
        bg  = _row_color(r.get("triggered"), r.get("error"))

        if r.get("error"):
            rows.append(
                f'<tr style="background:{bg}">'
                f'<td><b>{sym}</b></td>'
                f'<td colspan="7" style="color:#856404">ERROR: {r["error"]}</td>'
                f'</tr>'
            )
            continue

        status   = "🟢 符合" if r.get("triggered") else "🔴 未達"
        earn_str = (
            f'<div style="margin-bottom:4px;font-size:12px;color:#555">'
            f'📅 {r["earnings_date"]} ({r.get("earnings_days")} 天後)</div>'
            if r.get("earnings_date") else ""
        )
        detail = earn_str + _cond_html(
            r.get("passed_list", []),
            r.get("failed_list", []),
        )

        row = (
            f'<tr style="background:{bg}">'
            f'<td><b>{sym}</b></td>'
            f'<td style="text-align:center">{status}</td>'
            f'<td style="text-align:right">{_fmt(r.get("price"), 2)}</td>'
            '<td style="text-align:right;'
            + _cell_ok(r.get("rsi14") is not None and 50 <= r.get("rsi14") <= 70)
            + f'">{_fmt(r.get("rsi14"), 2)}</td>'
            '<td style="text-align:right;'
            + _cell_ok(r.get("ivr_52w") is not None and r.get("ivr_52w") < 30)
            + f'">{_fmt(r.get("ivr_52w"), 1)}%</td>'
            '<td style="text-align:right;'
            + _cell_ok(r.get("iv_hv30") is not None and r.get("iv_hv30") < 1.2)
            + f'">{_fmt(r.get("iv_hv30"), 3)}</td>'
            '<td style="text-align:right;'
            + _cell_ok(r.get("bb_pct_b") is not None and 30 <= r.get("bb_pct_b") <= 80)
            + f'">{_fmt(r.get("bb_pct_b"), 1)}%</td>'
            f'<td>{detail}</td>'
            f'</tr>'
        )
        rows.append(row)

    rows_html = "\n".join(rows)

    return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<style>
  body   {{ font-family: -apple-system, "Segoe UI", Arial, sans-serif; font-size: 14px; color: #333; }}
  h2     {{ color: #1a1a2e; border-bottom: 2px solid #4a90d9; padding-bottom: 6px; }}
  .meta  {{ color: #666; margin-bottom: 16px; }}
  .badge {{ display:inline-block; padding:2px 10px; border-radius:12px; font-weight:bold; font-size:13px; }}
  .ok    {{ background:#d1f7d6; color:#155724; }}
  .fail  {{ background:#f8d7da; color:#721c24; }}
  table  {{ border-collapse: collapse; width: 100%; margin-top: 12px; }}
  th     {{ background: #2c3e50; color: #fff; padding: 8px 10px; text-align: left; }}
  td     {{ padding: 7px 10px; border-bottom: 1px solid #e0e0e0; vertical-align: top; }}
  tr:hover td {{ filter: brightness(0.97); }}
  .note  {{ font-size: 11px; color: #888; margin-top: 16px; }}
</style>
</head>
<body>
<h2>📊 Bull Call Debit 掃描報告</h2>
<div class="meta">
  掃描時間：{now} &nbsp;|&nbsp;
  總計：{len(results)} 支 &nbsp;|&nbsp;
  <span class="badge ok">✅ 符合：{len(triggered)} 支</span> &nbsp;
  <span class="badge fail">❌ 錯誤：{len(errors)} 支</span>
</div>
<table>
  <thead>
    <tr>
      <th>Symbol</th>
      <th>狀態</th>
      <th>現價</th>
      <th>RSI14<br><span style="font-weight:normal;font-size:11px;opacity:0.8">✅ 50~70</span></th>
      <th>IVR(52W)<br><span style="font-weight:normal;font-size:11px;opacity:0.8">✅ &lt; 30%</span></th>
      <th>IV/HV(30)<br><span style="font-weight:normal;font-size:11px;opacity:0.8">✅ &lt; 1.2</span></th>
      <th>BB %B<br><span style="font-weight:normal;font-size:11px;opacity:0.8">✅ 30~80%</span></th>
      <th>通過 / 未通過條件明細</th>
    </tr>
    <tr style="background:#1a2e40;font-size:11px;color:#aac">
      <td colspan="2" style="padding:3px 10px">掃描條件：趨勢 price&gt;MA9&gt;MA20&gt;MA50 ｜ MACD(1d) Hist&gt;0 ｜ RSI(5)&gt;50 ｜ RSI(55)&gt;50 ｜ 財報日&gt;14天</td>
      <td colspan="6" style="padding:3px 10px;text-align:right">符合全部 9 項條件方觸發訊號</td>
    </tr>
  </thead>
  <tbody>
{rows_html}
  </tbody>
</table>
<p class="note">⚠️ 本報告僅供參考，不構成投資建議，請自行評估風險。</p>
</body>
</html>"""


# ──────────────────────────────────────────────────────────
#  send_scan_report  ← dashboard.py 呼叫此函式
# ──────────────────────────────────────────────────────────

def send_scan_report(results: list[dict]) -> None:
    """
    將掃描結果以 Email 寄出（純文字 + HTML 雙版本）。
    設定由 .env 讀取：SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS,
                      EMAIL_FROM, EMAIL_TO
    """
    smtp_host  = _env("SMTP_HOST", "smtp.gmail.com")
    smtp_port  = int(_env("SMTP_PORT", "587"))
    smtp_user  = _env("SMTP_USER")
    smtp_pass  = _env("SMTP_PASS")
    email_from = _env("EMAIL_FROM") or smtp_user
    email_to   = _env("EMAIL_TO")

    if not smtp_user or not smtp_pass:
        raise ValueError("SMTP_USER 或 SMTP_PASS 未設定，請檢查 .env 檔案。")
    if not email_to:
        raise ValueError("EMAIL_TO 未設定，請檢查 .env 檔案。")

    triggered = [r for r in results if r.get("triggered")]
    now       = datetime.now().strftime("%Y-%m-%d %H:%M")
    subject   = (
        f"[Bull Call Debit] {now}  "
        f"✅ 訊號：{len(triggered)} 支 / 共 {len(results)} 支"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = email_from
    msg["To"]      = email_to

    msg.attach(MIMEText(_build_text(results), "plain", "utf-8"))
    msg.attach(MIMEText(_build_html(results), "html",  "utf-8"))

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.ehlo()
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.sendmail(email_from, email_to.split(","), msg.as_string())

    print(f"✅ Email 已寄出 → {email_to}  主旨：{subject}")