import streamlit as st
import yfinance as yf
import math
import pandas as pd
from datetime import datetime
from scipy.stats import norm

# =================================================================
# 1. 核心數學與策略計算模組 (Black-Scholes & EV Logic)
# =================================================================

def bs_prob_itm_call(S, K, T, r, sigma) -> float:
    """計算到期時，股價高於 K (Call ITM) 的機率"""
    if T <= 0 or sigma <= 0:
        return 0.5
    try:
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        return norm.cdf(d2)
    except Exception:
        return 0.5

def bs_prob_itm_put(S, K, T, r, sigma) -> float:
    """計算到期時，股價低於 K (Put ITM) 的機率"""
    if T <= 0 or sigma <= 0:
        return 0.5
    try:
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        return norm.cdf(-d2)
    except Exception:
        return 0.5

def check_bull_call(symbol, S, long_k, short_k, long_ask, short_bid, iv, T_years, r, min_ev):
    """計算並篩選標準 Bull Call Debit Spread"""
    # 核心安全規則：買方必須在價內/價平(<=S)，賣方必須在價外(>S)
    if long_k > S or short_k <= S:
        return None
        
    debit = round(long_ask - short_bid, 4)
    width = short_k - long_k
    max_profit = round(width - debit, 4)
    max_loss = debit
    breakeven = long_k + debit

    if debit <= 0 or max_profit <= 0:
        return None

    p_above_long = bs_prob_itm_call(S, long_k, T_years, r, iv)
    p_above_short = bs_prob_itm_call(S, short_k, T_years, r, iv)

    p_full_profit = max(0.0, p_above_short)
    p_partial = max(0.0, p_above_long - p_above_short)
    p_loss = max(0.0, 1.0 - p_above_long)

    avg_partial = (max_profit - max_loss) * 0.5
    ev = (p_full_profit * max_profit + p_partial * avg_partial - p_loss * max_loss)
    
    if ev < min_ev:
        return None

    return {
        "策略類型": "Bull Call (買方爆發)",
        "組合形態": f"Buy ${long_k} / Sell ${short_k}",
        "淨收付(金)": f"付出 ${debit:.2f}",
        "最大獲利": f"${max_profit:.2f}",
        "最大風險": f"${max_loss:.2f}",
        "期望值 EV": round(ev, 4),
        "EV% (投報率)": f"{round(ev / max_loss * 100, 1)}%",
        "全賺勝率": f"{round(p_full_profit * 100, 1)}%",
        "全賠機率": f"{round(p_loss * 100, 1)}%",
        "損益平衡點": f"${round(breakeven, 2)} ({'+' if breakeven>=S else ''}{round((breakeven-S)/S*100, 2)}%)",
        "最大報酬率": f"+{round(max_profit / max_loss * 100, 1)}%"
    }

def check_bull_put(symbol, S, short_k, long_k, short_bid, long_ask, iv, T_years, r, min_ev):
    """計算並篩選標準 Bull Put Credit Spread"""
    # 核心安全規則：賣方與買方都必須在價外(下方安全防線 < S)
    if short_k >= S or long_k >= short_k:
        return None

    credit = round(short_bid - long_ask, 4)
    width = short_k - long_k
    max_profit = credit
    max_loss = round(width - credit, 4)
    breakeven = short_k - credit

    if credit <= 0 or max_loss <= 0:
        return None

    p_below_short = bs_prob_itm_put(S, short_k, T_years, r, iv)
    p_below_long = bs_prob_itm_put(S, long_k, T_years, r, iv)

    p_full_loss = max(0.0, p_below_long)
    p_partial = max(0.0, p_below_short - p_below_long)
    p_full_profit = max(0.0, 1.0 - p_below_short)

    avg_partial = (max_profit - max_loss) * 0.5
    ev = (p_full_profit * max_profit + p_partial * avg_partial - p_full_loss * max_loss)

    if ev < min_ev:
        return None

    return {
        "策略類型": "Bull Put (賣方收租)",
        "組合形態": f"Sell ${short_k} / Buy ${long_k}",
        "淨收付(金)": f"收入 ${credit:.2f}",
        "最大獲利": f"${max_profit:.2f}",
        "最大風險": f"${max_loss:.2f}",
        "期望值 EV": round(ev, 4),
        "EV% (投報率)": f"{round(ev / max_loss * 100, 1)}%",
        "全賺勝率": f"{round(p_full_profit * 100, 1)}%",
        "全賠機率": f"{round(p_full_loss * 100, 1)}%",
        "損益平衡點": f"${round(breakeven, 2)} ({'+' if breakeven>=S else ''}{round((breakeven-S)/S*100, 2)}%)",
        "最大報酬率": f"+{round(max_profit / max_loss * 100, 1)}%"
    }

# =================================================================
# 2. Streamlit 前端網頁介面
# =================================================================

st.set_page_config(page_title="頂級期權組合篩選器", layout="wide")

st.title("📈 頂級期權策略組合智能篩選系統")
st.markdown("輸入美股代號，系統將自動抓取即時選擇權鏈，透過 **Black-Scholes 數學模型** 篩選出具備**正期望值 (Positive EV)** 且符合正統技術形態的頂級交易組合。")

# 側邊欄設定區
st.sidebar.header("⚙️ 篩選參數設定")
ticker_input = st.sidebar.text_input("輸入股票代號", value="SOFI").upper().strip()
max_dte = st.sidebar.slider("最大到期天數 (DTE)", min_value=7, max_value=120, value=60, step=7)
min_ev_threshold = st.sidebar.number_input("最低期望值門檻 (MIN_EV)", min_value=0.01, max_value=1.00, value=0.10, step=0.01)
risk_free_rate = st.sidebar.number_input("無風險利率", min_value=0.0, max_value=0.1, value=0.045, step=0.005)
spread_widths = st.sidebar.multiselect("允許的價差寬度 (Width)", options=[0.5, 1.0, 2.0, 3.0, 5.0], default=[1.0, 2.0, 3.0])

if st.sidebar.button("🚀 開始分析期權鏈"):
    if not ticker_input:
        st.error("請輸入有效的股票代號！")
    else:
        with st.spinner(f"正在從 yfinance 獲取 {ticker_input} 最新數據中..."):
            try:
                ticker = yf.Ticker(ticker_input)
                # 取得即時現價
                fast_info = ticker.fast_info
                S = fast_info.get('lastPrice', None)
                if not S or math.isnan(S) or S <= 0:
                    hist = ticker.history(period="1d")
                    if not hist.empty:
                        S = hist['Close'].iloc[-1]
                
                if not S or math.isnan(S) or S <= 0:
                    st.error(f"無法取得 {ticker_input} 的現價，可能代號輸入錯誤。")
                    st.stop()
                
                st.metric(label=f"📊 {ticker_input} 目前正股現價", value=f"${S:.2f}")
                
                today = datetime.now().date()
                options_list = ticker.options
                
                valid_expiries = []
                for exp_str in options_list:
                    exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
                    dte = (exp_date - today).days
                    if 0 < dte <= max_dte:
                        valid_expiries.append((exp_str, dte))
                
                if not valid_expiries:
                    st.warning(f"找不到在 {max_dte} 天內到期的合適期權鏈。")
                    st.stop()
                
                all_golden_combos = []
                
                # 遍歷每個到期日
                for expiry_str, dte in valid_expiries:
                    T_years = dte / 365.0
                    opt_chain = ticker.option_chain(expiry_str)
                    
                    # --- 處理 Call Spread (買方策略) ---
                    calls = opt_chain.calls
                    # 限縮合理價格區間 (現價上下 30%)
                    df_calls = calls[(calls['strike'] >= S * 0.70) & (calls['strike'] <= S * 1.30)]
                    call_quotes = {}
                    for _, row in df_calls.iterrows():
                        k = float(row['strike'])
                        ask = float(row['ask']) if row['ask'] > 0 else float(row['lastPrice'])
                        bid = float(row['bid']) if row['bid'] > 0 else float(row['lastPrice']) * 0.95
                        iv = float(row['impliedVolatility']) if row['impliedVolatility'] > 0 else 0.5
                        call_quotes[k] = {"ask": ask, "bid": bid, "iv": iv}
                    
                    sorted_call_strikes = sorted(call_quotes.keys())
                    for long_k in sorted_call_strikes:
                        for w in spread_widths:
                            short_k = long_k + w
                            if short_k in call_quotes:
                                res = check_bull_call(
                                    ticker_input, S, long_k, short_k, 
                                    call_quotes[long_k]["ask"], call_quotes[short_k]["bid"],
                                    call_quotes[long_k]["iv"], T_years, risk_free_rate, min_ev_threshold
                                )
                                if res:
                                    res["到期日"] = expiry_str
                                    res["天數 (DTE)"] = dte
                                    all_golden_combos.append(res)

                    # --- 處理 Put Spread (賣方策略) ---
                    puts = opt_chain.puts
                    df_puts = puts[(puts['strike'] >= S * 0.70) & (puts['strike'] <= S * 1.05)]
                    put_quotes = {}
                    for _, row in df_puts.iterrows():
                        k = float(row['strike'])
                        ask = float(row['ask']) if row['ask'] > 0 else float(row['lastPrice'])
                        bid = float(row['bid']) if row['bid'] > 0 else float(row['lastPrice']) * 0.95
                        iv = float(row['impliedVolatility']) if row['impliedVolatility'] > 0 else 0.5
                        put_quotes[k] = {"ask": ask, "bid": bid, "iv": iv}
                    
                    sorted_put_strikes = sorted(put_quotes.keys())
                    for short_k in sorted_put_strikes:
                        for w in spread_widths:
                            long_k = short_k - w
                            if long_k in put_quotes:
                                res = check_bull_put(
                                    ticker_input, S, short_k, long_k,
                                    put_quotes[short_k]["bid"], put_quotes[long_k]["ask"],
                                    put_quotes[short_k]["iv"], T_years, risk_free_rate, min_ev_threshold
                                )
                                if res:
                                    res["到期日"] = expiry_str
                                    res["天數 (DTE)"] = dte
                                    all_golden_combos.append(res)
                
                # 展示篩選結果
                if all_golden_combos:
                    df_res = pd.DataFrame(all_golden_combos)
                    # 雙重排序：1. 到期天數由近到遠 2. 期望值由大到小
                    df_res = df_res.sort_values(by=["天數 (DTE)", "期望值 EV"], ascending=[True, False])
                    
                    # 重新排列欄位順序美化輸出
                    cols_order = ["策略類型", "到期日", "天數 (DTE)", "組合形態", "期望值 EV", "EV% (投報率)", "全賺勝率", "淨收付(金)", "最大獲利", "最大風險", "損益平衡點", "最大報酬率"]
                    df_res = df_res[cols_order]

                    st.success(f"🎯 掃描完成！為您找出共 {len(df_res)} 組符合正統型態且期望值大於 ${min_ev_threshold} 的黃金組合：")
                    
                    # 分策略分頁籤（Tabs）呈現
                    tab1, tab2 = st.tabs(["📊 所有推薦組合清單", "💡 核心挑選教學指南"])
                    with tab1:
                        st.dataframe(df_res, use_container_width=True, hide_index=True)
                    with tab2:
                        st.markdown("""
                        ### 💡 拿到清單後，該怎麼挑選最頂級的那個？
                        1. **如果你喜歡穩健收租（勝率控）：**
                           請過濾「策略類型」為 **Bull Put (賣方收租)**。這類策略全賺勝率通常高達 75% 以上，只要到期時股價「不要大跌跌破防線」就能全拿獲利。
                        2. **如果你想要以小博大（高回報）：**
                           請選擇 **Bull Call (買方爆發)**。這類策略付出的成本極低（最大風險低），只要股票在到期前發動突破上漲，最大報酬率往往是 100% ~ 300% 起跳！
                        3. **關鍵指標 - EV% (期望報酬率)：**
                           期望值 EV 雖然是正數，但別忘了看 `EV%`。如果 EV% 高達 15% 以上，代表該組合性價比極高，是市場定價出現微幅失衡的絕佳進場點。
                        """)
                else:
                    st.info(f"💡 本輪掃描完成：目前市場報價中，無任何符合「期望值門檻 > {min_ev_threshold}」的正統策略組合。您可以嘗試在側邊欄調低期望值門檻。")
                    
            except Exception as e:
                st.error(f"分析過程中發生預期外錯誤: {e}")