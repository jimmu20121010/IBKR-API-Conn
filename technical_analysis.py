#!/usr/bin/env python3
"""
technical_analysis.py
新增四層技術分析模組，整合進 main.py 的 Global Market Report：
  1. RSI / MACD 掃描       - 各指數 14 日 RSI + MACD 訊號
  2. 200 日均線距離         - 現價距 MA200 百分比
  3. ATH 距離              - 現價距歷史最高點百分比
  4. 相關性矩陣             - S&P500 / 那斯達克 / KOSPI / 台股 30 日滾動相關係數

使用方式：
  from technical_analysis import get_index_technical_analysis, get_correlation_matrix
  在 main() 呼叫後，將結果傳入 buildhtmlreport() / savetoexcel()
"""

import logging
import numpy as np
import pandas as pd
import yfinance as yf

log = logging.getLogger(__name__)

# ── 指數代碼對照表 ───────────────────────────────────────────────
INDEX_SYMBOLS = {
    "SPX":    "^GSPC",    # S&P 500
    "NDX":    "^NDX",     # Nasdaq-100
    "SOX":    "^SOX",     # Philadelphia Semiconductor
    "DJI":    "^DJI",     # Dow Jones
    "KOSPI":  "^KS11",    # 韓國 KOSPI
    "TWII":   "^TWII",    # 台灣加權指數
    "HSI":    "^HSI",     # 香港恒生
    "N225":   "^N225",    # 日經 225
}

# ── ATH 快取（避免重複下載）─────────────────────────────────────
_ath_cache: dict = {}


# ════════════════════════════════════════════════════════════════
#  工具函式
# ════════════════════════════════════════════════════════════════

def _fetch_history(symbol: str, period: str = "5y") -> pd.Series:
    """下載收盤價 Series；失敗回傳空 Series。"""
    try:
        df = yf.Ticker(symbol).history(period=period, auto_adjust=False)
        if df.empty:
            return pd.Series(dtype=float)
        s = df["Close"].dropna()
        s.index = pd.to_datetime(s.index).tz_localize(None)
        return s
    except Exception as e:
        log.warning(f"[TA] fetch_history {symbol} failed: {e}")
        return pd.Series(dtype=float)


def _calc_rsi(close: pd.Series, period: int = 14):
    """計算最新 RSI（Wilder's smoothed）。"""
    if len(close) < period + 1:
        return None
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, np.nan)
    rsi   = 100 - 100 / (1 + rs)
    val   = rsi.iloc[-1]
    return round(float(val), 2) if not np.isnan(val) else None


def _calc_macd_signal(close: pd.Series) -> str:
    """回傳 'Bullish' / 'Bearish' / 'Neutral'。"""
    if len(close) < 35:
        return "Neutral"
    ema12  = close.ewm(span=12, adjust=False).mean()
    ema26  = close.ewm(span=26, adjust=False).mean()
    macd   = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    hist   = macd - signal
    if len(hist) < 2:
        return "Neutral"
    if macd.iloc[-1] > signal.iloc[-1] and hist.iloc[-1] > hist.iloc[-2]:
        return "Bullish ↑"
    elif macd.iloc[-1] > signal.iloc[-1]:
        return "Bullish"
    elif macd.iloc[-1] < signal.iloc[-1] and hist.iloc[-1] < hist.iloc[-2]:
        return "Bearish ↓"
    else:
        return "Bearish"


def _calc_ma200_pct(close: pd.Series):
    """現價與 200 日均線的百分比差距；正值 = 高於均線。"""
    if len(close) < 200:
        return None
    ma200   = close.rolling(200).mean().iloc[-1]
    current = close.iloc[-1]
    if np.isnan(ma200) or ma200 == 0:
        return None
    return round((current - ma200) / ma200 * 100, 2)


def _get_ath(symbol: str, close: pd.Series):
    """取得 ATH：優先用完整歷史，否則用傳入的 close 最大值。"""
    if symbol in _ath_cache:
        return _ath_cache[symbol]
    try:
        full = yf.Ticker(symbol).history(period="max", auto_adjust=False)
        if not full.empty:
            ath = float(full["Close"].dropna().max())
            _ath_cache[symbol] = ath
            return ath
    except Exception as e:
        log.warning(f"[TA] ATH full history {symbol} failed: {e}")
    if not close.empty:
        return float(close.max())
    return None


def _calc_ath_pct(symbol: str, close: pd.Series):
    """現價距 ATH 的百分比（負值 = 仍在 ATH 以下）。"""
    if close.empty:
        return None
    ath     = _get_ath(symbol, close)
    current = close.iloc[-1]
    if ath is None or ath == 0:
        return None
    return round((current - ath) / ath * 100, 2)


def _rsi_label(rsi) -> str:
    if rsi is None:
        return "N/A"
    if rsi >= 70:
        return f"{rsi:.1f} Overbought"
    elif rsi <= 30:
        return f"{rsi:.1f} Oversold"
    elif rsi >= 60:
        return f"{rsi:.1f} Elevated"
    elif rsi <= 40:
        return f"{rsi:.1f} Depressed"
    else:
        return f"{rsi:.1f} Neutral"


def _ma200_label(pct) -> str:
    if pct is None:
        return "N/A"
    if pct >= 10:
        return f"+{pct:.1f}% Strong Bull"
    elif pct >= 3:
        return f"+{pct:.1f}% Above MA200"
    elif pct >= -3:
        return f"{pct:.1f}% Near MA200"
    elif pct >= -10:
        return f"{pct:.1f}% Below MA200"
    else:
        return f"{pct:.1f}% Bear Zone"


def _ath_label(pct) -> str:
    if pct is None:
        return "N/A"
    if pct >= -2:
        return f"{pct:.1f}% ATH Zone"
    elif pct >= -10:
        return f"{pct:.1f}% Near ATH"
    elif pct >= -20:
        return f"{pct:.1f}% Correction"
    elif pct >= -30:
        return f"{pct:.1f}% Bear Market"
    else:
        return f"{pct:.1f}% Deep Bear"


# ════════════════════════════════════════════════════════════════
#  主要公開 API
# ════════════════════════════════════════════════════════════════

def get_index_technical_analysis(symbols=None, period="2y") -> list:
    """
    對每支指數計算 RSI / MACD / MA200距離 / ATH距離。

    Parameters
    ----------
    symbols : dict  {"顯示名": "Yahoo代碼"}，預設使用 INDEX_SYMBOLS
    period  : str   yfinance history period（建議 >= 2y 以確保 MA200 足夠）

    Returns
    -------
    list[dict]  每支指數一筆 dict，欄位：
        name, symbol, close, rsi, rsi_label,
        macd_signal, ma200_pct, ma200_label, ath_pct, ath_label
    """
    if symbols is None:
        symbols = INDEX_SYMBOLS

    results = []
    for name, sym in symbols.items():
        log.info(f"[TA] Analyzing {name} ({sym}) ...")
        close = _fetch_history(sym, period=period)

        if len(close) < 50:
            log.warning(f"[TA] {name} insufficient data ({len(close)} bars)")
            results.append({
                "name": name, "symbol": sym, "close": None,
                "rsi": None, "rsi_label": "N/A",
                "macd_signal": "N/A",
                "ma200_pct": None, "ma200_label": "N/A",
                "ath_pct": None,   "ath_label": "N/A",
            })
            continue

        rsi      = _calc_rsi(close)
        macd_sig = _calc_macd_signal(close)
        ma200p   = _calc_ma200_pct(close)
        athp     = _calc_ath_pct(sym, close)

        results.append({
            "name":        name,
            "symbol":      sym,
            "close":       round(float(close.iloc[-1]), 2),
            "rsi":         rsi,
            "rsi_label":   _rsi_label(rsi),
            "macd_signal": macd_sig,
            "ma200_pct":   ma200p,
            "ma200_label": _ma200_label(ma200p),
            "ath_pct":     athp,
            "ath_label":   _ath_label(athp),
        })
        log.info(
            f"[TA] {name}: Close={close.iloc[-1]:.2f}  "
            f"RSI={rsi}  MACD={macd_sig}  "
            f"MA200={ma200p}%  ATH={athp}%"
        )

    return results


# ── 相關性矩陣 ────────────────────────────────────────────────────

CORR_SYMBOLS = {
    "SPX":   "^GSPC",
    "NDX":   "^NDX",
    "SOX":   "^SOX",
    "KOSPI": "^KS11",
    "TWII":  "^TWII",
    "N225":  "^N225",
}


def get_correlation_matrix(symbols=None, window=30, period="6mo") -> dict:
    """
    計算滾動 30 日相關係數矩陣（最後一個滾動窗口）。

    Returns
    -------
    dict  {
        "matrix":  pd.DataFrame,
        "latest":  dict,
        "window":  int,
        "as_of":   str,
        "interpretation": str,
    }
    """
    if symbols is None:
        symbols = CORR_SYMBOLS

    closes = {}
    for name, sym in symbols.items():
        s = _fetch_history(sym, period=period)
        if not s.empty:
            closes[name] = s
        else:
            log.warning(f"[Corr] {name} ({sym}) no data, skipped.")

    if len(closes) < 2:
        log.warning("[Corr] Not enough data for correlation matrix.")
        return {"matrix": None, "latest": {}, "window": window,
                "as_of": "N/A", "interpretation": "Insufficient data"}

    df   = pd.DataFrame(closes).dropna()
    rets = df.pct_change().dropna()

    if len(rets) < window:
        corr_matrix = rets.corr()
        as_of = str(rets.index[-1].date()) if not rets.empty else "N/A"
    else:
        corr_matrix = rets.tail(window).corr()
        as_of = str(rets.index[-1].date())

    corr_matrix = corr_matrix.round(3)

    # 自動解讀
    cols = list(corr_matrix.columns)
    high_pairs, low_pairs = [], []
    for i, a in enumerate(cols):
        for b in cols[i+1:]:
            val = corr_matrix.loc[a, b]
            if val >= 0.85:
                high_pairs.append(f"{a}/{b}={val:.2f}")
            elif val <= 0.40:
                low_pairs.append(f"{a}/{b}={val:.2f}")

    interp_lines = []
    if high_pairs:
        interp_lines.append(f"高度同步 (>=0.85): {', '.join(high_pairs)} → 分散效果有限")
    if low_pairs:
        interp_lines.append(f"低相關 (<=0.40): {', '.join(low_pairs)} → 具分散效果")
    if not interp_lines:
        interp_lines.append("相關性中等，市場同步性正常。")

    interpretation = "  |  ".join(interp_lines)
    log.info(f"[Corr] Matrix ({window}D) as of {as_of}: {interpretation}")

    return {
        "matrix":         corr_matrix,
        "latest":         corr_matrix.to_dict(),
        "window":         window,
        "as_of":          as_of,
        "interpretation": interpretation,
    }


# ════════════════════════════════════════════════════════════════
#  Console 摘要輸出
# ════════════════════════════════════════════════════════════════

def format_ta_summary(ta_results: list) -> str:
    lines = ["", "-" * 120,
             f"  {'Index':<8} {'Close':>10}  {'RSI (14)':>22}  {'MACD':<14}  {'vs MA200':>18}  {'vs ATH':>18}",
             "-" * 120]
    for r in ta_results:
        close_str = f"{r['close']:>10.2f}" if r["close"] else f"{'N/A':>10}"
        lines.append(
            f"  {r['name']:<8} {close_str}  "
            f"{r['rsi_label']:>22}  "
            f"{r['macd_signal']:<14}  "
            f"{r['ma200_label']:>18}  "
            f"{r['ath_label']:>18}"
        )
    lines.append("-" * 120)
    return "\n".join(lines)


def format_corr_summary(corr: dict) -> str:
    if corr["matrix"] is None:
        return "[Corr] Insufficient data."
    lines = [
        "",
        f"  Correlation Matrix ({corr['window']}D rolling, as of {corr['as_of']})",
        "-" * 72,
    ]
    mat = corr["matrix"]
    header = f"  {'':<8}" + "".join(f"{c:>8}" for c in mat.columns)
    lines.append(header)
    for idx in mat.index:
        row = f"  {idx:<8}" + "".join(f"{mat.loc[idx, c]:>8.3f}" for c in mat.columns)
        lines.append(row)
    lines.append("-" * 72)
    lines.append(f"  {corr['interpretation']}")
    lines.append("-" * 72)
    return "\n".join(lines)


# ════════════════════════════════════════════════════════════════
#  整合進 main.py 的 patch 說明（見下方註解）
# ════════════════════════════════════════════════════════════════
#
#  # Step 1：在 main() 的 macro fetch 區段後加入：
#  from technical_analysis import (
#      get_index_technical_analysis, get_correlation_matrix,
#      format_ta_summary, format_corr_summary
#  )
#  ta_results = get_index_technical_analysis()
#  corr       = get_correlation_matrix()
#
#  # Step 2：console 印出：
#  print(format_ta_summary(ta_results))
#  print(format_corr_summary(corr))
#
#  # Step 3：傳入 build_html_report() 與 save_to_excel()：
#  html_body = build_html_report(
#      results, runtime, vix_value, fear_greed_data,
#      kospi_data, chuanhu_data, copper_data,
#      ta_results=ta_results,
#      corr=corr,
#  )
#
#  # Step 4：save_to_excel() 新增兩個 Sheet：
#  df_ta = pd.DataFrame(ta_results)[[
#      "name","close","rsi","macd_signal","ma200_pct","ma200_label","ath_pct","ath_label"
#  ]]
#  df_ta.to_excel(writer, sheet_name="Index TA", index=False)
#  if corr["matrix"] is not None:
#      corr["matrix"].to_excel(writer, sheet_name="Correlation 30D")


if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    print("\n====  Index Technical Analysis  ====")
    ta = get_index_technical_analysis()
    print(format_ta_summary(ta))

    print("\n====  Correlation Matrix (30D)  ====")
    corr = get_correlation_matrix()
    print(format_corr_summary(corr))
