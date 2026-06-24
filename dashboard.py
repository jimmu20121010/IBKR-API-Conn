import argparse
import asyncio
import math
import os
import csv
from contextlib import suppress
from datetime import datetime, timezone

import pandas as pd

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False

asyncio.set_event_loop(asyncio.new_event_loop())
from ib_async import IB, Stock, Index

try:
    from email_report import send_scan_report
except ImportError as exc:
    raise ImportError("找不到 email_report.py，請確認與 dashboard.py 在同一資料夾") from exc

try:
    from LINE import send_line_message
    LINE_AVAILABLE = True
except ImportError:
    LINE_AVAILABLE = False


# ══════════════════════════════════════════════════════
#  helpers
# ══════════════════════════════════════════════════════

def safe_float(value):
    try:
        x = float(value)
        return None if (math.isnan(x) or math.isinf(x)) else x
    except Exception:
        return None


def fmt(value, digits=4):
    if value is None:
        return "None"
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return "None"
        return f"{value:.{digits}f}"
    return str(value)


def choose_price(ticker):
    for field in ("last", "delayedLast", "marketPrice", "delayedBid", "close", "delayedClose"):
        value = safe_float(getattr(ticker, field, None))
        if value is not None and value > 0:
            return value, field
    return None, None


# ══════════════════════════════════════════════════════
#  Watchlist loader
# ══════════════════════════════════════════════════════

def load_watchlist(filepath: str) -> list[dict]:
    symbols = []
    if not os.path.isfile(filepath):
        raise FileNotFoundError(f"Watchlist 檔案不存在：{filepath}")
    with open(filepath, newline="", encoding="utf-8") as f:
        for row in csv.reader(f):
            if not row:
                continue
            first = row[0].strip()
            if first.startswith("#") or first == "":
                continue
            sym      = first.upper()
            exchange = row[1].strip().upper() if len(row) > 1 and row[1].strip() else "SMART"
            currency = row[2].strip().upper() if len(row) > 2 and row[2].strip() else "USD"
            symbols.append({"symbol": sym, "exchange": exchange, "currency": currency})
    if not symbols:
        raise ValueError(f"Watchlist 檔案內沒有有效的 symbol：{filepath}")
    return symbols


# ══════════════════════════════════════════════════════
#  MA / BB
# ══════════════════════════════════════════════════════

def calculate_ma(close_series, period):
    return close_series.rolling(window=period).mean()


def bb_position(close_series, period=20, num_std=2.0):
    ma    = close_series.rolling(window=period).mean()
    std   = close_series.rolling(window=period).std()
    upper = ma + num_std * std
    lower = ma - num_std * std
    lc = last_value(close_series)
    lu = last_value(upper)
    ll = last_value(lower)
    lm = last_value(ma)
    pct_b = bw = None
    if lu is not None and ll is not None and lu != ll:
        pct_b = (lc - ll) / (lu - ll) * 100
        bw = (lu - ll) / lm * 100 if lm and lm != 0 else None
    return lu, lm, ll, pct_b, bw


def bb_signal(pct_b):
    if pct_b is None:      return ""
    if pct_b >= 100:       return "  ▲ above upper band"
    if pct_b >= 80:        return "  ↑ near upper band"
    if pct_b <= 0:         return "  ▼ below lower band"
    if pct_b <= 20:        return "  ↓ near lower band"
    if 45 <= pct_b <= 55:  return "  → near middle band"
    return ""


# ══════════════════════════════════════════════════════
#  RSI family
# ══════════════════════════════════════════════════════

def calculate_rsi(close_series, period=14):
    delta    = close_series.diff()
    gain     = delta.clip(lower=0)
    loss     = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    rs       = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def calculate_streak(close_series):
    streak = [0]
    for i in range(1, len(close_series)):
        if pd.isna(close_series.iloc[i]) or pd.isna(close_series.iloc[i - 1]):
            streak.append(0)
        elif close_series.iloc[i] > close_series.iloc[i - 1]:
            prev = streak[-1]
            streak.append(prev + 1 if prev > 0 else 1)
        elif close_series.iloc[i] < close_series.iloc[i - 1]:
            prev = streak[-1]
            streak.append(prev - 1 if prev < 0 else -1)
        else:
            streak.append(0)
    return pd.Series(streak, index=close_series.index, dtype="float64")


def percent_rank(series, period=100):
    result = [None] * len(series)
    for i in range(len(series)):
        if i < period:
            result[i] = None
            continue
        window  = series.iloc[i - period:i]
        current = series.iloc[i]
        if pd.isna(current) or window.isna().all():
            result[i] = None
            continue
        result[i] = (window.lt(current).sum() / len(window)) * 100.0
    return pd.Series(result, index=series.index, dtype="float64")


def calculate_connors_rsi(close_series, rsi_period=3, streak_rsi_period=2, rank_period=100):
    price_rsi      = calculate_rsi(close_series, rsi_period)
    streak         = calculate_streak(close_series)
    streak_rsi     = calculate_rsi(streak, streak_rsi_period)
    one_day_return = close_series.diff()
    pr = percent_rank(one_day_return, rank_period)
    return (price_rsi + streak_rsi + pr) / 3.0


def calculate_stoch_rsi(close_series, rsi_period=14, stoch_period=14):
    rsi         = calculate_rsi(close_series, rsi_period)
    lowest_rsi  = rsi.rolling(window=stoch_period).min()
    highest_rsi = rsi.rolling(window=stoch_period).max()
    return ((rsi - lowest_rsi) / (highest_rsi - lowest_rsi)) * 100.0


def rsi_signal(v):
    if v is None: return ""
    if v >= 70:   return "  ▲ overbought"
    if v <= 30:   return "  ▼ oversold"
    return ""


# ══════════════════════════════════════════════════════
#  MACD (MTF)
# ══════════════════════════════════════════════════════

def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()


def calculate_macd(close_series, fast=12, slow=26, signal=9):
    macd_line   = ema(close_series, fast) - ema(close_series, slow)
    signal_line = ema(macd_line, signal)
    histogram   = macd_line - signal_line
    return macd_line, signal_line, histogram


TF_MAP = {
    "1h":  ("5 D",  "1 hour",  60),
    "4h":  ("20 D", "4 hours", 240),
    "1d":  ("1 Y",  "1 day",   1440),
    "1w":  ("2 Y",  "1 week",  10080),
}


async def fetch_macd_tf(ib, contract, tf_label, fast, slow, signal_len, timeout):
    if tf_label not in TF_MAP:
        return None, None, None, f"unknown TF '{tf_label}'"
    duration, bar_size, _ = TF_MAP[tf_label]
    try:
        bars = await historical_with_timeout(
            ib, contract, duration, bar_size, "TRADES", True, timeout)
        if not bars:
            return None, None, None, "no bars"
        df = bars_to_df(bars)
        ml, sl, h = calculate_macd(df["close"], fast, slow, signal_len)
        return last_value(ml), last_value(sl), last_value(h), None
    except Exception as e:
        return None, None, None, str(e)


# ══════════════════════════════════════════════════════
#  HV
# ══════════════════════════════════════════════════════

def calculate_hv(close_series, period=30):
    log_ret = (close_series / close_series.shift(1)).apply(math.log)
    return log_ret.rolling(window=period).std() * math.sqrt(252)


# ══════════════════════════════════════════════════════
#  Pivot Points
# ══════════════════════════════════════════════════════

def calculate_pivot_points(price_df):
    df = price_df.dropna(subset=["high", "low", "close"])
    if len(df) < 2:
        return None
    prev  = df.iloc[-2]
    H, L, C = float(prev["high"]), float(prev["low"]), float(prev["close"])
    pivot = (H + L + C) / 3.0
    r1 = 2 * pivot - L;  s1 = 2 * pivot - H
    r2 = pivot + (H - L); s2 = pivot - (H - L)
    r3 = H + 2.0 * (pivot - L); s3 = L - 2.0 * (H - pivot)
    return dict(pivot=pivot, r1=r1, r2=r2, r3=r3, s1=s1, s2=s2, s3=s3, H=H, L=L, C=C)


def pivot_proximity_tag(price, level, pct_threshold=0.5):
    if price is None or level is None:
        return ""
    return "  ← price near here" if abs(price - level) / level * 100 <= pct_threshold else ""


# ══════════════════════════════════════════════════════
#  IV metrics
# ══════════════════════════════════════════════════════

def iv_rank_percentile(iv_series, trading_days):
    iv_series = iv_series.dropna()
    window    = iv_series if len(iv_series) < trading_days else iv_series.iloc[-trading_days:]
    if window.empty:
        return None, None, None, None, None
    current_iv      = iv_series.iloc[-1]
    iv_low, iv_high = window.min(), window.max()
    ivr = 0.0 if iv_high == iv_low else ((current_iv - iv_low) / (iv_high - iv_low)) * 100.0
    ivp = (window.lt(current_iv).sum() / len(window)) * 100.0
    return current_iv, iv_low, iv_high, ivr, ivp


def iv_hv_signal(ratio):
    if ratio is None: return ""
    if ratio > 1.2:   return "  → IV rich  (sell side favoured)"
    if ratio < 0.8:   return "  → IV cheap (buy side favoured)"
    return "  → IV near fair value"


# ══════════════════════════════════════════════════════
#  Earnings
# ══════════════════════════════════════════════════════

def get_next_earnings(sym):
    if not YFINANCE_AVAILABLE:
        return None, None
    try:
        tk  = yf.Ticker(sym)
        cal = tk.calendar
        if cal is None:
            return None, None
        if isinstance(cal, dict):
            ed = cal.get("Earnings Date")
            if ed is None:
                return None, None
            if isinstance(ed, (list, tuple)):
                ed = ed[0]
        elif hasattr(cal, "T"):
            try:
                ed = cal.T["Earnings Date"].iloc[0]
            except Exception:
                return None, None
        else:
            return None, None
        if hasattr(ed, "date"):
            ed = ed.date()
        elif hasattr(ed, "to_pydatetime"):
            ed = ed.to_pydatetime().date()
        today     = datetime.now(timezone.utc).date()
        days_away = (ed - today).days
        return str(ed), days_away
    except Exception:
        return None, None


def earnings_tag(days_away):
    if days_away is None: return ""
    if days_away < 0:     return f"  (已過 {-days_away} 天)"
    if days_away == 0:    return "  ⚠️  今天!"
    if days_away <= 7:    return f"  ⚠️  {days_away} 天後!"
    if days_away <= 30:   return f"  ({days_away} 天後)"
    return f"  ({days_away} 天後)"


# ══════════════════════════════════════════════════════
#  Bull Call Debit Signal
# ══════════════════════════════════════════════════════

def check_bull_call_debit(
    sym, stock_price, ma_values,
    rsi14, rsi5, rsi55,
    macd_results, ivr_52w, iv_hv30_ratio,
    bb_pct_b, earnings_days_away,
):
    passed, failed = [], []

    def chk(cond, lp, lf):
        (passed if cond else failed).append(lp if cond else lf)
        return cond

    ma9  = ma_values.get(9)
    ma20 = ma_values.get(20)
    ma50 = ma_values.get(50)

    trend_ma = (stock_price and ma9 and ma20 and ma50 and
                stock_price > ma9 > ma20 > ma50)
    chk(trend_ma,
        f"✅ 趨勢(MA): price({fmt(stock_price,2)}) > MA9({fmt(ma9,2)}) > MA20({fmt(ma20,2)}) > MA50({fmt(ma50,2)})",
        f"❌ 趨勢(MA): 均線未多頭排列 price={fmt(stock_price,2)} MA9={fmt(ma9,2)} MA20={fmt(ma20,2)} MA50={fmt(ma50,2)}")

    r1d = macd_results.get("1d", {})
    chk((not r1d.get("err")) and (r1d.get("hist") or 0) > 0,
        f"✅ MACD(1d): Histogram={fmt(r1d.get('hist'),5)} > 0 (多頭)",
        f"❌ MACD(1d): Histogram={fmt(r1d.get('hist'),5)} <= 0 (空頭)")

    chk(rsi14 is not None and 50 <= rsi14 <= 70,
        f"✅ RSI(14)={fmt(rsi14,2)} 在 50~70",
        f"❌ RSI(14)={fmt(rsi14,2)} 不在 50~70（{'超買' if rsi14 and rsi14>70 else '偏弱'}）")

    chk(rsi55 is not None and rsi55 > 50,
        f"✅ RSI(55)={fmt(rsi55,2)} > 50 長週期多頭",
        f"❌ RSI(55)={fmt(rsi55,2)} <= 50 長週期偏空")

    chk(rsi5 is not None and rsi5 > 50,
        f"✅ RSI(5)={fmt(rsi5,2)} > 50 短期動能偏多",
        f"❌ RSI(5)={fmt(rsi5,2)} <= 50 短期動能不足")

    chk(ivr_52w is not None and ivr_52w < 30,
        f"✅ IVR(52W)={fmt(ivr_52w,1)}% < 30% IV 便宜",
        f"❌ IVR(52W)={fmt(ivr_52w,1)}% >= 30% IV 偏高")

    chk(iv_hv30_ratio is not None and iv_hv30_ratio < 1.2,
        f"✅ IV/HV(30)={fmt(iv_hv30_ratio,3)} < 1.2 IV 未過度溢價",
        f"❌ IV/HV(30)={fmt(iv_hv30_ratio,3)} >= 1.2 IV 偏貴")

    chk(bb_pct_b is not None and 30 <= bb_pct_b <= 80,
        f"✅ BB %B={fmt(bb_pct_b,1)}% 在 30~80 進場時機合適",
        f"❌ BB %B={fmt(bb_pct_b,1)}% 不在 30~80（{'超買區' if bb_pct_b and bb_pct_b>80 else '過低/下跌'}）")

    if earnings_days_away is None:
        chk(True, "✅ 財報日未知（預設通過）", "")
    else:
        chk(earnings_days_away > 14,
            f"✅ 財報日距今 {earnings_days_away} 天，風險可控",
            f"❌ 財報日距今僅 {earnings_days_away} 天，IV 膨脹風險高")

    return len(failed) == 0, passed, failed


# ══════════════════════════════════════════════════════
#  IB helpers
# ══════════════════════════════════════════════════════

async def connect_ib(host, port, client_id, timeout):
    ib = IB()
    try:
        await asyncio.wait_for(ib.connectAsync(host, port, clientId=client_id), timeout=timeout)
        if not ib.isConnected():
            raise ConnectionError("ib.isConnected() is False after connect.")
        return ib
    except asyncio.TimeoutError:
        with suppress(Exception): ib.disconnect()
        raise TimeoutError(f"Connect timeout after {timeout}s.")
    except Exception as e:
        with suppress(Exception): ib.disconnect()
        raise ConnectionError(f"IB connection failed: {e}")


async def qualify_with_timeout(ib, contracts, timeout):
    try:
        return await asyncio.wait_for(ib.qualifyContractsAsync(*contracts), timeout=timeout)
    except asyncio.TimeoutError:
        raise TimeoutError(f"Qualify timeout after {timeout}s.")


async def historical_with_timeout(ib, contract, duration, bar_size, what_to_show, use_rth, timeout):
    try:
        return await asyncio.wait_for(
            ib.reqHistoricalDataAsync(
                contract, endDateTime="", durationStr=duration,
                barSizeSetting=bar_size, whatToShow=what_to_show,
                useRTH=use_rth, formatDate=1,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        raise TimeoutError(f"Historical data ({what_to_show}) timeout after {timeout}s.")


def bars_to_df(bars):
    df = pd.DataFrame([{
        "date": b.date, "open": b.open, "high": b.high,
        "low": b.low, "close": b.close,
    } for b in bars])
    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def bars_to_series(bars):
    df = pd.DataFrame([{"date": b.date, "close": b.close} for b in bars])
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    return df["close"]


def last_value(series):
    s = series.dropna()
    return s.iloc[-1] if not s.empty else None


# ══════════════════════════════════════════════════════
#  scan_symbol
# ══════════════════════════════════════════════════════

async def scan_symbol(ib, sym_info: dict, args) -> dict:
    sym      = sym_info["symbol"]
    exchange = sym_info.get("exchange", "SMART")
    currency = sym_info.get("currency", "USD")

    result = {
        "symbol": sym, "triggered": False, "error": None,
        "price": None, "rsi14": None, "rsi5": None, "rsi55": None,
        "ivr_52w": None, "iv_hv30": None, "bb_pct_b": None,
        "earnings_date": None, "earnings_days": None,
        "passed_list": [], "failed_list": [],
        "ma_values": {}, "bb_upper": None, "bb_mid": None, "bb_lower": None,
        "bb_bw": None, "hv30": None, "hv60": None, "hv_ib": None,
        "iv_metrics": {}, "current_iv": None, "iv_hv60": None,
        "connors": None, "stoch_rsi": None, "pivot_data": None,
        "macd_results": {}, "vix_value": None,
    }

    stock_ticker = vix_ticker = None

    try:
        stock        = Stock(sym, exchange, currency)
        vix_contract = Index("VIX", "CBOE")

        qualified = await qualify_with_timeout(
            ib, [stock, vix_contract], args.qualify_timeout)
        if len(qualified) < 2:
            raise RuntimeError(f"Could not qualify {sym} and VIX.")
        qualified_stock, qualified_vix = qualified[0], qualified[1]

        ib.reqMarketDataType(args.market_data_type)
        await asyncio.sleep(1)
        stock_ticker = ib.reqMktData(qualified_stock, "", False, False)
        vix_ticker   = ib.reqMktData(qualified_vix,   "", False, False)

        stock_price = vix_value = None
        for _ in range(20):
            await asyncio.sleep(1)
            if stock_price is None:
                stock_price, _ = choose_price(stock_ticker)
            if vix_value is None:
                vix_value, _   = choose_price(vix_ticker)
            if stock_price is not None and vix_value is not None:
                break

        result["price"]     = stock_price
        result["vix_value"] = vix_value

        price_bars = await historical_with_timeout(
            ib, qualified_stock, "1 Y", "1 day", "TRADES", True, args.historical_timeout)
        iv_bars = await historical_with_timeout(
            ib, qualified_stock, "1 Y", "1 day", "OPTION_IMPLIED_VOLATILITY", True,
            args.historical_timeout)
        try:
            hv_bars = await historical_with_timeout(
                ib, qualified_stock, "1 Y", "1 day", "HISTORICAL_VOLATILITY", True,
                args.historical_timeout)
        except Exception:
            hv_bars = []

        if not price_bars:
            raise RuntimeError("No historical price bars.")
        if not iv_bars:
            raise RuntimeError("No OPTION_IMPLIED_VOLATILITY bars.")

        price_df  = bars_to_df(price_bars)
        close     = price_df["close"]
        iv_series = bars_to_series(iv_bars)

        ma_values = {p: last_value(calculate_ma(close, p)) for p in args.ma_periods}
        result["ma_values"] = ma_values

        bb_upper, bb_mid, bb_lower, bb_pct_b, bb_bw = bb_position(
            close, args.bb_period, args.bb_std)
        result.update(bb_upper=bb_upper, bb_mid=bb_mid, bb_lower=bb_lower,
                      bb_pct_b=bb_pct_b, bb_bw=bb_bw)

        hv30  = last_value(calculate_hv(close, 30))
        hv60  = last_value(calculate_hv(close, 60))
        hv_ib = last_value(bars_to_series(hv_bars)) if hv_bars else None
        result.update(hv30=hv30, hv60=hv60, hv_ib=hv_ib)

        iv_metrics = {}
        for label, td in [("13W", 65), ("26W", 130), ("52W", 252)]:
            current_iv, iv_low, iv_high, ivr, ivp = iv_rank_percentile(iv_series, td)
            iv_metrics[label] = dict(current_iv=current_iv, iv_low=iv_low,
                                     iv_high=iv_high, ivr=ivr, ivp=ivp)
        current_iv    = iv_metrics["52W"]["current_iv"]
        ivr_52w       = iv_metrics["52W"]["ivr"]
        iv_hv30_ratio = (current_iv / hv30) if (current_iv and hv30 and hv30 > 0) else None
        iv_hv60_ratio = (current_iv / hv60) if (current_iv and hv60 and hv60 > 0) else None
        result.update(iv_metrics=iv_metrics, current_iv=current_iv,
                      ivr_52w=ivr_52w, iv_hv30=iv_hv30_ratio, iv_hv60=iv_hv60_ratio)

        rsi5      = last_value(calculate_rsi(close, 5))
        rsi14     = last_value(calculate_rsi(close, 14))
        rsi55     = last_value(calculate_rsi(close, 55))
        connors   = last_value(calculate_connors_rsi(close, 3, 2, 100))
        stoch_rsi = last_value(calculate_stoch_rsi(close, 14, 14))
        result.update(rsi5=rsi5, rsi14=rsi14, rsi55=rsi55,
                      connors=connors, stoch_rsi=stoch_rsi)

        result["pivot_data"] = calculate_pivot_points(price_df)

        macd_results = {}
        for tf in args.macd_tf:
            m, s, h, err = await fetch_macd_tf(
                ib, qualified_stock, tf,
                args.macd_fast, args.macd_slow, args.macd_signal,
                args.historical_timeout)
            macd_results[tf] = {"macd": m, "signal": s, "hist": h, "err": err}
        result["macd_results"] = macd_results

        earnings_date, earnings_days = get_next_earnings(sym)
        result.update(earnings_date=earnings_date, earnings_days=earnings_days)

        triggered, passed_list, failed_list = check_bull_call_debit(
            sym=sym, stock_price=stock_price, ma_values=ma_values,
            rsi14=rsi14, rsi5=rsi5, rsi55=rsi55,
            macd_results=macd_results, ivr_52w=ivr_52w,
            iv_hv30_ratio=iv_hv30_ratio, bb_pct_b=bb_pct_b,
            earnings_days_away=earnings_days,
        )
        result.update(triggered=triggered, passed_list=passed_list, failed_list=failed_list)

    except Exception as e:
        result["error"] = str(e)
    finally:
        with suppress(Exception):
            if stock_ticker: ib.cancelMktData(stock_ticker.contract)
        with suppress(Exception):
            if vix_ticker:   ib.cancelMktData(vix_ticker.contract)

    return result


# ══════════════════════════════════════════════════════
#  print_result
# ══════════════════════════════════════════════════════

def print_result(r: dict, args):
    sym = r["symbol"]
    if r.get("error"):
        print(f"\n{'═'*60}\n  {sym}  ── ERROR: {r['error']}\n{'═'*60}")
        return

    print(f"\n{'═'*60}\n  Dashboard: {sym}\n{'═'*60}")
    print(f"\nMarket snapshot")
    print(f"  VIX:          {fmt(r.get('vix_value'), 2):>8}")
    print(f"  {sym} price:  {fmt(r.get('price'), 2):>8}")

    print("\nMoving Averages  (daily close)")
    for period in args.ma_periods:
        val = r["ma_values"].get(period)
        tag = ""
        if r.get("price") and val:
            tag = f"  ({(r['price']-val)/val*100:+.2f}% vs price)"
        print(f"  MA({period:3d}):  {fmt(val, 2):>10}{tag}")

    print(f"\nBollinger Bands  ({args.bb_period}, {args.bb_std}σ)")
    print(f"  Upper:      {fmt(r.get('bb_upper'), 2):>10}")
    print(f"  Middle:     {fmt(r.get('bb_mid'), 2):>10}")
    print(f"  Lower:      {fmt(r.get('bb_lower'), 2):>10}")
    if r.get("bb_pct_b") is not None:
        print(f"  %B:         {fmt(r['bb_pct_b'], 2):>10}{bb_signal(r['bb_pct_b'])}")
    if r.get("bb_bw") is not None:
        print(f"  Bandwidth:  {fmt(r['bb_bw'], 2):>10}%")

    print("\nEarnings Date")
    if r.get("earnings_date"):
        print(f"  Next:  {r['earnings_date']}{earnings_tag(r.get('earnings_days'))}")
    elif not YFINANCE_AVAILABLE:
        print("  N/A  (pip install yfinance to enable)")
    else:
        print("  N/A")

    print("\nImplied Volatility")
    print(f"  Current IV:   {fmt(r.get('current_iv'), 4):>8}")
    iv_m = r.get("iv_metrics", {})
    print(f"\n  {'Period':6}  {'IV':>8}  {'Low':>8}  {'High':>8}  {'IVR':>7}  {'IVP':>7}")
    print(f"  {'─'*6}  {'─'*8}  {'─'*8}  {'─'*8}  {'─'*7}  {'─'*7}")
    for label in ("13W", "26W", "52W"):
        m = iv_m.get(label, {})
        print(f"  {label:6}  {fmt(m.get('current_iv'),4):>8}  "
              f"{fmt(m.get('iv_low'),4):>8}  {fmt(m.get('iv_high'),4):>8}  "
              f"{fmt(m.get('ivr'),2):>7}  {fmt(m.get('ivp'),2):>7}")

    print("\nHistorical Volatility")
    print(f"  HV(30):  {fmt(r.get('hv30'), 4):>8}")
    print(f"  HV(60):  {fmt(r.get('hv60'), 4):>8}")
    if r.get("hv_ib"):
        print(f"  HV(IB):  {fmt(r['hv_ib'], 4):>8}")

    print("\nIV / HV ratio")
    print(f"  IV / HV(30):  {fmt(r.get('iv_hv30'), 3):>8}{iv_hv_signal(r.get('iv_hv30'))}")
    print(f"  IV / HV(60):  {fmt(r.get('iv_hv60'), 3):>8}{iv_hv_signal(r.get('iv_hv60'))}")

    print("\nMomentum  [RSI]")
    print(f"  RSI(5):       {fmt(r.get('rsi5'),  2):>8}{rsi_signal(r.get('rsi5'))}")
    print(f"  RSI(14):      {fmt(r.get('rsi14'), 2):>8}{rsi_signal(r.get('rsi14'))}")
    print(f"  RSI(55):      {fmt(r.get('rsi55'), 2):>8}{rsi_signal(r.get('rsi55'))}  ← 長週期")
    print(f"  Connors RSI:  {fmt(r.get('connors'), 2):>8}{rsi_signal(r.get('connors'))}")
    print(f"  StochRSI:     {fmt(r.get('stoch_rsi'), 2):>8}{rsi_signal(r.get('stoch_rsi'))}")

    macd_r = r.get("macd_results", {})
    print(f"\nMTF MACD  [{args.macd_fast}/{args.macd_slow}/{args.macd_signal}]")
    print(f"  {'TF':6}  {'MACD':>10}  {'Signal':>10}  {'Hist':>10}  Note")
    print(f"  {'─'*6}  {'─'*10}  {'─'*10}  {'─'*10}  {'─'*20}")
    for tf in args.macd_tf:
        rr = macd_r.get(tf, {})
        if rr.get("err"):
            print(f"  {tf:6}  ERROR  {rr['err']}")
        else:
            note = "▲ bullish" if (rr.get("hist") or 0) > 0 else "▼ bearish"
            print(f"  {tf:6}  {fmt(rr.get('macd'),5):>10}  "
                  f"{fmt(rr.get('signal'),5):>10}  "
                  f"{fmt(rr.get('hist'),5):>10}  {note}")

    pivot_data = r.get("pivot_data")
    print("\nSupport / Resistance  [Pivot Points]")
    if pivot_data:
        pd_ = pivot_data
        print(f"  Prev bar:  H={fmt(pd_['H'],2)}  L={fmt(pd_['L'],2)}  C={fmt(pd_['C'],2)}")
        print(f"\n  {'Level':8}  {'Price':>10}  Note")
        print(f"  {'─'*8}  {'─'*10}  {'─'*20}")
        for name, val in [
            ("R3",pd_["r3"]),("R2",pd_["r2"]),("R1",pd_["r1"]),
            ("Pivot",pd_["pivot"]),("S1",pd_["s1"]),("S2",pd_["s2"]),("S3",pd_["s3"]),
        ]:
            print(f"  {name:8}  {fmt(val,2):>10}{pivot_proximity_tag(r.get('price'), val)}")
    else:
        print("  Not enough bars.")

    print(f"\n{'═'*60}\n  Bull Call Debit 策略訊號\n{'═'*60}")
    label = "🟢 全部條件通過！" if r["triggered"] else f"🔴 未達條件（{len(r['failed_list'])} 項未通過）"
    print(f"  結果：{label}\n")
    for item in r["passed_list"]:
        print(f"  {item}")
    for item in r["failed_list"]:
        print(f"  {item}")
    print(f"{'═'*60}")


# ══════════════════════════════════════════════════════
#  main
# ══════════════════════════════════════════════════════

async def main():
    def env_int(key, default):
        try:
            return int(os.environ.get(key, default))
        except Exception:
            return default

    parser = argparse.ArgumentParser(
        description="Bull Call Debit Scanner — 批次掃描 watchlist，結果以 Email 寄送。"
    )
    parser.add_argument("--host",               default=os.environ.get("IBKR_HOST", "127.0.0.1"))
    parser.add_argument("--port",               type=int, default=env_int("IBKR_PORT", 4002))
    parser.add_argument("--client-id",          type=int, default=env_int("IBKR_CLIENT_ID", 12))
    parser.add_argument("--market-data-type",   type=int, default=env_int("IBKR_MARKET_DATA_TYPE", 3),
                        choices=[1, 2, 3, 4])
    parser.add_argument("--watchlist",          default=os.environ.get("WATCHLIST_FILE", "watchlist.csv"))
    parser.add_argument("--symbol",             default=None,
                        help="指定單一 symbol（覆蓋 --watchlist）")
    parser.add_argument("--connect-timeout",    type=int, default=env_int("CONNECT_TIMEOUT", 15))
    parser.add_argument("--qualify-timeout",    type=int, default=env_int("QUALIFY_TIMEOUT", 10))
    parser.add_argument("--historical-timeout", type=int, default=env_int("HISTORICAL_TIMEOUT", 25))
    parser.add_argument("--sleep-between",      type=int, default=env_int("SCAN_SLEEP_BETWEEN", 3))
    parser.add_argument("--macd-fast",          type=int, default=12)
    parser.add_argument("--macd-slow",          type=int, default=26)
    parser.add_argument("--macd-signal",        type=int, default=9)
    parser.add_argument("--macd-tf",   nargs="+", default=["1h", "4h", "1d"],
                        choices=list(TF_MAP.keys()))
    parser.add_argument("--ma-periods",nargs="+", type=int, default=[9, 20, 50, 200])
    parser.add_argument("--bb-period", type=int,   default=20)
    parser.add_argument("--bb-std",    type=float, default=2.0)
    parser.add_argument("--no-email",     action="store_true")
    parser.add_argument("--email-always", action="store_true")
    parser.add_argument("--notify-line",  action="store_true")
    args = parser.parse_args()

    if args.symbol:
        sym_list = [{"symbol": args.symbol.upper(), "exchange": "SMART", "currency": "USD"}]
        print(f"單一模式：掃描 {args.symbol.upper()}")
    else:
        sym_list = load_watchlist(args.watchlist)
        print(f"Watchlist 模式：讀取 {args.watchlist}，共 {len(sym_list)} 支")

    print(f"IBKR  {args.host}:{args.port}  ClientId={args.client_id}")
    print(f"Market data type = {args.market_data_type}\n")

    ib = None
    all_results = []

    try:
        ib = await connect_ib(args.host, args.port, args.client_id, args.connect_timeout)
        print(f"Connected: {ib.isConnected()}\n")

        for i, sym_info in enumerate(sym_list, 1):
            sym = sym_info["symbol"]
            print(f"[{i}/{len(sym_list)}] 掃描 {sym} ...")
            result = await scan_symbol(ib, sym_info, args)
            all_results.append(result)
            print_result(result, args)
            if i < len(sym_list):
                print(f"  （等待 {args.sleep_between} 秒）")
                await asyncio.sleep(args.sleep_between)

        triggered_syms = [r["symbol"] for r in all_results if r.get("triggered")]
        error_syms     = [r["symbol"] for r in all_results if r.get("error")]
        print(f"\n{'═'*60}")
        print(f"  掃描完畢  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print(f"{'═'*60}")
        print(f"  總計掃描：{len(all_results)} 支")
        print(f"  符合訊號：{len(triggered_syms)} 支  {triggered_syms}")
        print(f"  擷取錯誤：{len(error_syms)} 支  {error_syms}")
        print(f"{'═'*60}")

        should_email = (not args.no_email) and (args.email_always or len(triggered_syms) > 0)
        if should_email:
            try:
                send_scan_report(all_results)
            except Exception as e:
                print(f"⚠️  Email 寄送失敗：{e}")
        elif args.no_email:
            print("\n（--no-email 已略過 Email 寄送）")
        else:
            print("\n（無符合訊號，Email 未寄出；加上 --email-always 可強制寄送）")

        if args.notify_line and LINE_AVAILABLE and triggered_syms:
            for r in all_results:
                if r.get("triggered"):
                    try:
                        send_line_message(
                            f"📊 Bull Call Debit 訊號\n"
                            f"股票：{r['symbol']}  現價：{fmt(r.get('price'),2)}\n"
                            f"RSI14={fmt(r.get('rsi14'))}  IVR52W={fmt(r.get('ivr_52w'),1)}%\n"
                            f"IV/HV30={fmt(r.get('iv_hv30'),3)}  BB%B={fmt(r.get('bb_pct_b'),1)}%\n"
                            + (f"財報日：{r['earnings_date']} ({r['earnings_days']}天後)\n"
                               if r.get("earnings_date") else "")
                            + "⚠️ 僅供參考，請自行評估風險。"
                        )
                    except Exception as e:
                        print(f"⚠️  LINE 發送失敗（{r['symbol']}）：{e}")
        elif args.notify_line and not LINE_AVAILABLE:
            print("⚠️  找不到 LINE.py，LINE 通知略過。")

    except (TimeoutError, ConnectionError) as e:
        print(f"\n連線錯誤：{e}")
    except FileNotFoundError as e:
        print(f"\n檔案錯誤：{e}")
    except Exception as e:
        print(f"\n未預期錯誤：{e}")
    finally:
        if ib is not None:
            with suppress(Exception):
                if ib.isConnected():
                    ib.disconnect()
                    print("\nDisconnected.")


if __name__ == "__main__":
    asyncio.run(main())