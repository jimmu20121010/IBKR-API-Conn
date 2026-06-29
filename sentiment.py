#!/usr/bin/env python3
"""
sentiment.py — 市場情緒層 v4.0
真正分開 NYSE / Nasdaq 廣度：
  - 啟動時從 Nasdaq 官方 API 動態下載兩個交易所的 symbol 清單
  - 對 CBOE cone CSV 每個 symbol 標記交易所後分組統計
  - 無需維護靜態清單，每次執行自動更新
"""

import csv
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from io import StringIO

import requests
from bs4 import BeautifulSoup
from xml.etree import ElementTree as ET

log = logging.getLogger(__name__)

_EDGAR_HDR = {"User-Agent": "MarketAnalyzer contact@example.com"}
_WEB_HDR = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
}
_JSON_HDR = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

CBOE_CONE_URL = (
    "https://www.cboe.com/us/options/market_statistics/symbol_data/csv/?mkt=cone"
)

# Index symbols（排除，不計入股票廣度）
IDX_SYMS = {
    "SPX","SPXW","XSP","VIX","RUT","NDX","OEX","DJX","MXEA","MXEF",
    "SPY","QQQ","IWM","DIA","SDS","SSO","UVXY","SVXY","VIXY","SQQQ","TQQQ",
}

BUY_CODES  = {"P"}
SELL_CODES = {"S"}

# ── module-level cache ──
_cboe_rows_cache   = None   # CBOE cone CSV rows
_nasdaq_syms_cache = None   # Nasdaq 上市 symbol set
_nyse_syms_cache   = None   # NYSE 上市 symbol set


# ══════════════════════════════════════════════════════════════════════════════
# 工具函式
# ══════════════════════════════════════════════════════════════════════════════

def _safe_float(s, fallback=None):
    try:
        return float(str(s).replace("%", "").replace(",", "").strip())
    except Exception:
        return fallback


# ══════════════════════════════════════════════════════════════════════════════
# 0. 交易所 Symbol 清單（動態從 Nasdaq 官方 API 下載）
# ══════════════════════════════════════════════════════════════════════════════

def _fetch_exchange_symbols(exchange: str) -> set:
    """
    從 Nasdaq 官方 Screener API 下載指定交易所的全部上市 symbol。
    exchange: "NASDAQ" | "NYSE" | "AMEX"
    回傳 set[str]
    """
    syms = set()
    for offset in range(0, 8000, 1000):
        try:
            url = (
                f"https://api.nasdaq.com/api/screener/stocks"
                f"?tableonly=true&limit=1000&offset={offset}&exchange={exchange}"
            )
            r = requests.get(url, headers=_JSON_HDR, timeout=15)
            r.raise_for_status()
            rows = r.json().get("data", {}).get("table", {}).get("rows", [])
            if not rows:
                break
            for row in rows:
                sym = row.get("symbol", "").strip()
                if sym:
                    syms.add(sym)
            time.sleep(0.2)
        except Exception as e:
            log.warning(f"[ExchSyms] {exchange} offset={offset} failed: {e}")
            break
    log.info(f"[ExchSyms] {exchange}: {len(syms):,} symbols loaded")
    return syms


def _get_nasdaq_syms() -> set:
    global _nasdaq_syms_cache
    if _nasdaq_syms_cache is None:
        _nasdaq_syms_cache = _fetch_exchange_symbols("NASDAQ")
    return _nasdaq_syms_cache


def _get_nyse_syms() -> set:
    global _nyse_syms_cache
    if _nyse_syms_cache is None:
        # NYSE + AMEX 合併（都是非 Nasdaq 的傳統交易所）
        nyse  = _fetch_exchange_symbols("NYSE")
        amex  = _fetch_exchange_symbols("AMEX")
        _nyse_syms_cache = nyse | amex
    return _nyse_syms_cache


# ══════════════════════════════════════════════════════════════════════════════
# 1. AAII 散戶情緒
# ══════════════════════════════════════════════════════════════════════════════

def _label_aaii(bull, bear):
    spread = bull - bear
    if bull >= 50:    return "過度樂觀 ⚠️ (逆指標偏空)"
    if bear >= 45:    return "極度悲觀 🔴 (逆指標偏多)"
    if spread >= 20:  return "Bullish 🟡"
    if spread <= -20: return "Bearish 🔴"
    return "Neutral ⚪"


def fetch_aaii() -> dict:
    log.info("[Sentiment] Fetching AAII...")
    EMPTY = {
        "bullish": None, "neutral": None, "bearish": None,
        "bull_bear_spread": None, "survey_date": "N/A", "label": "N/A",
    }

    # 路徑 A：lxml table 解析
    try:
        resp = requests.get(
            "https://www.aaii.com/sentimentsurvey/sent_results",
            headers=_WEB_HDR, timeout=15,
        )
        if resp.status_code == 200:
            soup  = BeautifulSoup(resp.text, "lxml")
            table = soup.find("table")
            if table:
                rows = table.find_all("tr")
                if len(rows) >= 2:
                    cells = [
                        c.get_text(strip=True)
                        for c in rows[1].find_all(["td", "th"])
                    ]
                    if len(cells) >= 4:
                        date_str = cells[0]
                        bull = _safe_float(cells[1])
                        neut = _safe_float(cells[2])
                        bear = _safe_float(cells[3])
                        if bull is not None and bear is not None:
                            spread = round(bull - bear, 1)
                            label  = _label_aaii(bull, bear)
                            log.info(
                                f"[AAII] {date_str} Bull={bull}% Bear={bear}% "
                                f"Spread={spread:+}% → {label}"
                            )
                            return {
                                "bullish": bull, "neutral": neut, "bearish": bear,
                                "bull_bear_spread": spread,
                                "survey_date": date_str, "label": label,
                            }
    except Exception as e:
        log.warning(f"[AAII] Path A failed: {e}")

    # 路徑 B：regex 備援
    try:
        resp = requests.get(
            "https://www.aaii.com/sentimentsurvey/sent_results",
            headers=_WEB_HDR, timeout=15,
        )
        text   = resp.text
        bull_m = re.search(r'Bullish[^%]{0,40}?(\d{1,2}\.\d)\s*%', text, re.I)
        neut_m = re.search(r'Neutral[^%]{0,40}?(\d{1,2}\.\d)\s*%', text, re.I)
        bear_m = re.search(r'Bearish[^%]{0,40}?(\d{1,2}\.\d)\s*%', text, re.I)
        date_m = re.search(
            r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}', text
        )
        if bull_m and bear_m:
            bull     = float(bull_m.group(1))
            neut     = float(neut_m.group(1)) if neut_m else None
            bear     = float(bear_m.group(1))
            spread   = round(bull - bear, 1)
            label    = _label_aaii(bull, bear)
            date_str = date_m.group(0) if date_m else "N/A"
            log.info(f"[AAII] regex {date_str} Bull={bull}% Bear={bear}% → {label}")
            return {
                "bullish": bull, "neutral": neut, "bearish": bear,
                "bull_bear_spread": spread, "survey_date": date_str, "label": label,
            }
    except Exception as e:
        log.warning(f"[AAII] Path B failed: {e}")

    log.warning("[AAII] All paths failed.")
    return EMPTY


# ══════════════════════════════════════════════════════════════════════════════
# 2. Put/Call Ratio — CBOE cone CSV
# ══════════════════════════════════════════════════════════════════════════════

def _label_pcr(v):
    if v is None:   return "N/A"
    if v >= 1.5:    return "極度恐慌 🔴 (強烈逆指標看多)"
    if v >= 1.2:    return "Fear ⚠️ (偏空情緒，逆指標偏多)"
    if v >= 0.9:    return "Neutral ⚪"
    if v >= 0.7:    return "Greed 🟡 (偏多情緒，注意過熱)"
    return          "Extreme Greed 🔴 (逆指標偏空)"


def _load_cboe_cone() -> list:
    resp = requests.get(CBOE_CONE_URL, headers=_WEB_HDR, timeout=30)
    resp.raise_for_status()
    reader = csv.DictReader(StringIO(resp.text))
    return list(reader)


def _get_cboe_rows() -> list:
    global _cboe_rows_cache
    if _cboe_rows_cache is None:
        _cboe_rows_cache = _load_cboe_cone()
    return _cboe_rows_cache


def fetch_pcr() -> dict:
    log.info("[Sentiment] Fetching Put/Call Ratio...")
    try:
        rows  = _get_cboe_rows()
        tot_p = tot_c = eq_p = eq_c = idx_p = idx_c = 0
        for r in rows:
            try:
                sym = r.get("Symbol", "")
                cp  = r.get("Call/Put", "")
                vol = int(r.get("Volume", "0") or 0)
            except Exception:
                continue
            if cp == "P":
                tot_p += vol
            elif cp == "C":
                tot_c += vol
            if sym.upper() in IDX_SYMS:
                if cp == "P": idx_p += vol
                if cp == "C": idx_c += vol
            else:
                if cp == "P": eq_p += vol
                if cp == "C": eq_c += vol

        total  = round(tot_p / tot_c, 3) if tot_c else None
        equity = round(eq_p  / eq_c,  3) if eq_c  else None
        index  = round(idx_p / idx_c, 3) if idx_c  else None
        label  = _label_pcr(total)
        log.info(
            f"[P/C] CBOE cone: Total={total} Equity={equity} "
            f"Index={index} → {label}"
        )
        return {"total": total, "equity": equity, "index": index, "label": label}
    except Exception as e:
        log.warning(f"[PCR] CBOE cone failed: {e}")

    return {"total": None, "equity": None, "index": None, "label": "N/A"}


# ══════════════════════════════════════════════════════════════════════════════
# 3. Market Breadth — CBOE cone × 動態 Exchange Symbol 清單（真正分開）
# ══════════════════════════════════════════════════════════════════════════════

def _label_breadth(pct):
    if pct is None: return "N/A"
    if pct >= 70:   return "強勢 💪 (市場廣度健康)"
    if pct >= 55:   return "偏強 🟢"
    if pct >= 45:   return "中性 ⚪"
    if pct >= 30:   return "偏弱 🟡"
    return          "弱勢 🔴 (市場廣度惡化)"


def _build_breadth_block(bull: int, bear: int, source: str) -> dict:
    total = bull + bear
    pct   = round(bull / total * 100, 1) if total else None
    return {
        "advances":    bull,
        "declines":    bear,
        "breadth_pct": pct,
        "label":       _label_breadth(pct),
        "source":      source,
    }


def _breadth_from_cboe_cone() -> dict:
    """
    CBOE cone CSV × Nasdaq/NYSE 官方 symbol 清單
    → 真正分開 NYSE 廣度 與 Nasdaq 廣度
    """
    rows        = _get_cboe_rows()
    nasdaq_syms = _get_nasdaq_syms()
    nyse_syms   = _get_nyse_syms()

    # 彙整每個 symbol 的 Call / Put 成交量
    sym_vol: dict[str, dict] = {}
    for r in rows:
        sym = r.get("Symbol", "").upper()
        cp  = r.get("Call/Put", "")
        try:
            vol = int(r.get("Volume", "0") or 0)
        except Exception:
            continue
        if sym in IDX_SYMS:
            continue
        if sym not in sym_vol:
            sym_vol[sym] = {"P": 0, "C": 0, "exch": "OTHER"}
        sym_vol[sym][cp] = sym_vol[sym].get(cp, 0) + vol

    # 標記交易所
    for sym in sym_vol:
        if sym in nasdaq_syms:
            sym_vol[sym]["exch"] = "NASDAQ"
        elif sym in nyse_syms:
            sym_vol[sym]["exch"] = "NYSE"

    # 分組統計
    nq_bull = nq_bear = 0
    ny_bull = ny_bear = 0

    for sym, v in sym_vol.items():
        total_vol = v["C"] + v["P"]
        if total_vol == 0:
            continue
        is_bull = v["C"] >= v["P"]
        exch    = v["exch"]
        if exch == "NASDAQ":
            if is_bull: nq_bull += 1
            else:       nq_bear += 1
        elif exch == "NYSE":
            if is_bull: ny_bull += 1
            else:       ny_bear += 1
        # OTHER 不計入任一交易所

    log.info(
        f"[Breadth] NYSE: {ny_bull} bull / {ny_bear} bear → "
        f"{round(ny_bull/(ny_bull+ny_bear)*100,1) if ny_bull+ny_bear else 'N/A'}%"
    )
    log.info(
        f"[Breadth] Nasdaq: {nq_bull} bull / {nq_bear} bear → "
        f"{round(nq_bull/(nq_bull+nq_bear)*100,1) if nq_bull+nq_bear else 'N/A'}%"
    )

    source = "CBOE 選擇權廣度 × Nasdaq 官方上市清單"
    return {
        "nyse":   _build_breadth_block(ny_bull, ny_bear, source),
        "nasdaq": _build_breadth_block(nq_bull, nq_bear, source),
    }


def fetch_breadth() -> dict:
    log.info("[Sentiment] Fetching Market Breadth...")

    try:
        res = _breadth_from_cboe_cone()
        ny_pct = res.get("nyse",   {}).get("breadth_pct")
        nq_pct = res.get("nasdaq", {}).get("breadth_pct")
        if ny_pct is not None or nq_pct is not None:
            return res
    except Exception as e:
        log.warning(f"[Breadth] CBOE cone failed: {e}")

    log.warning("[Breadth] All sources failed.")
    empty = {"advances": None, "declines": None, "breadth_pct": None, "label": "N/A"}
    return {"nyse": empty.copy(), "nasdaq": empty.copy()}


# ══════════════════════════════════════════════════════════════════════════════
# 4. Insider — EDGAR RSS + ownership.xml regex
# ══════════════════════════════════════════════════════════════════════════════

def _label_insider(buy, sell):
    total = buy + sell
    if total == 0: return "資料不足 ⚪"
    ratio = buy / total
    if ratio >= 0.70: return "Insider 積極買入 🟢 (偏多)"
    if ratio >= 0.55: return "Insider 偏買 🟡"
    if ratio >= 0.45: return "Insider 均衡 ⚪"
    if ratio >= 0.30: return "Insider 偏賣 🟡 (偏空)"
    return "Insider 積極賣出 🔴 (偏空)"


def _get_form4_codes_from_index(href: str) -> list:
    ir   = requests.get(href, headers=_EDGAR_HDR, timeout=12)
    soup = BeautifulSoup(ir.text, "lxml")
    xml_url = None
    for a in soup.find_all("a", href=True):
        h = a["href"]
        if "ownership.xml" in h.lower() and "xsl" not in h.lower():
            xml_url = "https://www.sec.gov" + h if h.startswith("/") else h
            break
    if not xml_url:
        for a in soup.find_all("a", href=True):
            h = a["href"]
            if h.endswith(".xml") and "xsl" not in h.lower():
                xml_url = "https://www.sec.gov" + h if h.startswith("/") else h
                break
    if not xml_url:
        return []
    xr    = requests.get(xml_url, headers=_EDGAR_HDR, timeout=12)
    codes = re.findall(
        r'<transactionCode>\s*([A-Za-z])\s*</transactionCode>',
        xr.text, re.IGNORECASE,
    )
    return [c.upper() for c in codes]


def _parse_edgar_form4(days_back: int = 3, max_entries: int = 80) -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
    url = (
        "https://www.sec.gov/cgi-bin/browse-edgar"
        f"?action=getcurrent&type=4&dateb=&owner=include"
        f"&count={max_entries}&output=atom"
    )
    r    = requests.get(url, headers=_EDGAR_HDR, timeout=20)
    root = ET.fromstring(r.content)
    ns   = {"atom": "http://www.w3.org/2005/Atom"}
    entries = root.findall("atom:entry", ns)

    buy = sell = other = processed = 0
    for entry in entries:
        upd = entry.find("atom:updated", ns)
        if upd is not None:
            try:
                dt = datetime.fromisoformat(
                    upd.text.strip().replace("Z", "+00:00")
                )
                if dt < cutoff:
                    continue
            except Exception:
                pass

        link_tag = entry.find("atom:link", ns)
        if link_tag is None:
            continue
        href = link_tag.get("href", "")
        try:
            codes = _get_form4_codes_from_index(href)
            processed += 1
            for c in codes:
                if   c in BUY_CODES:  buy   += 1
                elif c in SELL_CODES: sell  += 1
                else:                 other += 1
        except Exception:
            pass
        time.sleep(0.05)

    bias = _label_insider(buy, sell)
    log.info(
        f"[Insider] EDGAR Form4 {days_back}d ({processed} filings): "
        f"Buy={buy} Sell={sell} Other={other} → {bias}"
    )
    return {
        "buy_count": buy, "sell_count": sell, "other_count": other,
        "total_filings": processed, "net_bias": bias,
    }


def fetch_insider() -> dict:
    log.info("[Sentiment] Fetching Insider Activity (SEC EDGAR)...")
    try:
        result = _parse_edgar_form4(days_back=3, max_entries=80)
        if result.get("total_filings", 0) > 0:
            return result
    except Exception as e:
        log.warning(f"[Insider] EDGAR failed: {e}")

    return {
        "buy_count": 0, "sell_count": 0, "other_count": 0,
        "total_filings": 0, "net_bias": "資料不足 ⚪",
    }


# ══════════════════════════════════════════════════════════════════════════════
# 統一入口
# ══════════════════════════════════════════════════════════════════════════════

def fetch_all_sentiment() -> dict:
    """
    執行順序：
    1. 預先載入 Nasdaq / NYSE symbol 清單（動態 API，共用快取）
    2. 預先載入 CBOE cone CSV（PCR + Breadth 共用）
    3. 依序抓 AAII / PCR / Breadth / Insider
    """
    global _cboe_rows_cache

    # Step 1：下載交易所 symbol 清單（Breadth 分組用）
    log.info("[Sentiment] Loading exchange symbol lists...")
    _get_nasdaq_syms()
    _get_nyse_syms()

    # Step 2：下載 CBOE cone CSV（PCR + Breadth 共用）
    log.info("[Sentiment] Pre-loading CBOE cone CSV...")
    try:
        _cboe_rows_cache = _load_cboe_cone()
        log.info(f"[Sentiment] CBOE cone loaded: {len(_cboe_rows_cache):,} rows")
    except Exception as e:
        log.warning(f"[Sentiment] CBOE cone pre-load failed: {e}")
        _cboe_rows_cache = None

    # Step 3：各指標
    return {
        "aaii":    fetch_aaii(),
        "pcr":     fetch_pcr(),
        "breadth": fetch_breadth(),
        "insider": fetch_insider(),
    }


if __name__ == "__main__":
    import json
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    result = fetch_all_sentiment()
    print(json.dumps(result, ensure_ascii=False, indent=2))
