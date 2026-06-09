import streamlit as st
import yfinance as yf
import math
import pandas as pd
from datetime import datetime
from scipy.stats import norm

# 引入 IBKR API 必備套件
from ib_insync import IB, Contract, ComboLeg, MarketOrder, LimitOrder

# =================================================================
# 1. IBKR 下單核心功能
# =================================================================

def place_ibkr_combo_order(strategy_type, symbol, expiry_str, strike_1, strike_2, action_1, action_2, right_1, right_2):
    """
    連接 IBKR 並送出跨價組合單 (Combo Order)
    """
    ib = IB()
    try:
        # 連接本地的 TWS (7496) 或 IB Gateway (7497)
        # 如果部署到雲端，此處需更改為您雲端伺服器或自建網閘的 IP 與 Port
        ib.connect('127.0.0.1', 7497, clientId=10)
        
        # 轉換日期格式為 IBKR 格式 (YYYYMMDD)
        expiry_ib = expiry_str.replace('-', '')
        
        # 建立第一隻腳 (Leg 1)
        leg1 = Contract(symbol=symbol, secType='OPT', lastTradeDateOrContractMonth=expiry_ib, strike=strike_1, right=right_1, exchange='SMART', currency='USD')
        # 建立第二隻腳 (Leg 2)
        leg2 = Contract(symbol=symbol, secType='OPT', lastTradeDateOrContractMonth=expiry_ib, strike=strike_2, right=right_2, exchange='SMART', currency='USD')
        
        # 必須先向 IB 請求資格合約，獲取 ConId
        qualified_contracts = ib.qualifyContracts(leg1, leg2)
        if len(qualified_contracts) < 2:
            return False, "無法在 IBKR 找到對應的期權合約，請確認該合約是否具備流動性。"
            
        c1, c2 = qualified_contracts[0], qualified_contracts[1]
        
        # 建立組合單合約 (Bag Contract)
        bag = Contract()
        bag.symbol = symbol
        bag.secType = 'BAG'
        bag.exchange = 'SMART'
        bag.currency = 'USD'
        
        # 定義組合單的兩隻腳
        combo_leg1 = ComboLeg(conId=c1.conId, ratio=1, action=action_1, exchange='SMART')
        combo_leg2 = ComboLeg(conId=c2.conId, ratio=1, action=action_2, exchange='SMART')
        bag.comboLegs = [combo_leg1, combo_leg2]
        
        # 下單：此處示範使用「市價單 (MarketOrder)」
        # 警示：實務上期權建議改用 LimitOrder(action='BUY', lmtPrice=...) 以防滑價
        order = MarketOrder(action='BUY', totalQuantity=1)
        
        trade = ib.placeOrder(bag, order)
        ib.sleep(1) # 等待一秒讓訂單送出
        
        return True, f"下單成功！已送出 {strategy_type} 組合單，訂單狀態: {trade.orderStatus.status}"
        
    except Exception as e:
        return False, f"IBKR 連線或下單失敗: {str(e)}"
    finally:
        if ib.isConnected():
            ib.disconnect()

# =================================================================
# 2. 核心數學與策略計算模組 (Black-Scholes & EV Logic)
# =================================================================

def bs_prob_itm_call(S, K, T, r, sigma) -> float:
    if T <= 0 or sigma <= 0: return 0.5
    try:
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        return norm.cdf(d2)
    except Exception: return 0.5

def bs_prob_itm_put(S, K, T, r, sigma) -> float:
    if T <= 0 or sigma <= 0: return 0.5
    try:
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        return norm.cdf(-d2)
    except Exception: return 0.5

def check_bull_call(symbol, S, long_k, short_k, long_ask, short_bid, iv, T_years, r, min_ev):
    if long_k > S or short_k <= S: return None
    debit = round(long_ask - short_bid, 4)
    width = short_k - long_k
    max_profit = round(width - debit, 4)
    max_loss = debit
    breakeven = long_k + debit
    if debit <= 0 or max_profit <= 0: return None

    p_above_long = bs_prob_itm_call(S, long_k, T_years, r, iv)
    p_above_short = bs_prob_itm_call(S, short_k, T_years, r, iv)
    p_full_profit = max(0.0, p_above_short)
    p_partial = max(0.0, p_above_long - p_above_short)
    p_loss = max(0.0, 1.0 - p_above_long)

    avg_partial = (max_profit - max_loss) * 0.5
    ev = (p_full_profit * max_profit + p_partial * avg_partial - p_loss * max_loss)
    if ev < min_ev: return None

    return {
        "策略類型": "Bull Call (買方爆發)",
        "組合形態": f"Buy ${long_k} / Sell ${short_k}",
        "期望值 EV": round(ev, 4),
        "EV%": f"{round(ev / max_loss * 100, 1)}%",
        "全賺勝率": f"{round(p_full_profit * 100, 1)}%",
        "淨收付": f"付出 ${debit:.2f}",
        "最大獲利": f"${max_profit:.2f}",
        "最大風險": f"${max_loss:.2f}",
        "損益平衡點": f"${round(breakeven, 2)}",
        # 後台下單隱藏參數
        "strike_1": long_k, "strike_2": short_k,
        "action_1": "BUY", "action_2": "SELL", "right_1": "C", "right_2": "C"
    }

def check_bull_put(symbol, S, short_k, long_k, short_bid, long_ask, iv, T_years, r, min_ev):
    if short_k >= S or long_k >= short_k: return None
    credit = round(short_bid - long_ask, 4)
    width = short_k - long_k
    max_profit = credit
    max_loss = round(width - credit, 4)
    breakeven = short_k - credit
    if credit <= 0 or max_loss <= 0: return None

    p_below_short = bs_prob_itm_put(S, short_k, T_years, r, iv)
    p_below_long = bs_prob_itm_put(S, long_k, T_years, r, iv)
    p_full_loss = max(0.0, p_below_long)
    p_partial = max(0.0, p_below_short - p_below_long)
    p_full_profit = max(0.0, 1.0 - p_below_short)

    avg_partial = (max_profit - max_loss) * 0.5
    ev = (p_full_profit * max_profit + p_partial * avg_partial - p_full_loss * max_loss)
    if ev < min_ev: return None

    return {
        "策略類型": "Bull Put (賣方收租)",
        "組合形態": f"Sell ${short_k} / Buy ${long_k}",
        "期望值 EV": round(ev, 4),
        "EV%": f"{round(ev / max_loss * 100, 1)}%",
        "全賺勝率": f"{round(p_full_profit * 100, 1)}%",
        "淨收付": f"收入 ${credit:.2f}",
        "最大獲利": f"${max_profit:.2f}",
        "最大風險": f"${max_loss:.2f}",
        "損益平衡點": f"${round(breakeven, 2)}",
        # 後台下單隱藏參數
        "strike_1": short_k, "strike_2": long_k,
        "action_1": "SELL", "action_2": "BUY", "right_1": "P", "right_2": "P"
    }

# =================================================================
# 3. Streamlit 前端網頁介面
# =================================================================

st.set_page_config(page_title="IBKR 期權自動化下單系統", layout="wide")
st.title("⚡ 頂級期權策略組合智慧篩選與自動下單系統")

st.sidebar.header("⚙️ 篩選參數設定")
ticker_input = st.sidebar.text_input("輸入股票代號", value="SOFI").upper().strip()
max_dte = st.sidebar.slider("最大到期天數 (DTE)", min_value=7, max_value=120, value=60, step=7)
min_ev_threshold = st.sidebar.number_input("最低期望值門檻 (MIN_EV)", min_value=0.01, max_value=1.00, value=0.10, step=0.01)
risk_free_rate = st.sidebar.number_input("無風險利率", min_value=0.0, max_value=0.1, value=0.045, step=0.005)
spread_widths = st.sidebar.multiselect("允許的價差寬度 (Width)", options=[0.5, 1.0, 2.0, 3.0, 5.0], default=[1.0, 2.0, 3.0])

# 初始化 Session State 用於記錄點擊下單的狀態
if "order_status" not in st.session_state:
    st.session_state.order_status = ""

if st.sidebar.button("🚀 開始分析期權鏈"):
    st.session_state.order_status = "" # 清空上次狀態
    if not ticker_input:
        st.error("請輸入有效的股票代號！")
    else:
        with st.spinner(f"正在從 yfinance 獲取 {ticker_input} 最新數據中..."):
            try:
                ticker = yf.Ticker(ticker_input)
                fast_info = ticker.fast_info
                S = fast_info.get('lastPrice', None)
                if not S or math.isnan(S) or S <= 0:
                    hist = ticker.history(period="1d")
                    if not hist.empty: S = hist['Close'].iloc[-1]
                
                if not S or math.isnan(S) or S <= 0:
                    st.error(f"無法取得 {ticker_input} 的現價。")
                    st.stop()
                
                st.metric(label=f"📊 {ticker_input} 目前正股現價", value=f"${S:.2f}")
                
                today = datetime.now().date()
                valid_expiries = []
                for exp_str in ticker.options:
                    exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
                    dte = (exp_date - today).days
                    if 0 < dte <= max_dte: valid_expiries.append((exp_str, dte))
                
                if not valid_expiries:
                    st.warning(f"找不到在 {max_dte} 天內到期的合適期權鏈。")
                    st.stop()
                
                all_golden_combos = []
                
                for expiry_str, dte in valid_expiries:
                    T_years = dte / 365.0
                    opt_chain = ticker.option_chain(expiry_str)
                    
                    # Call Spread
                    calls = opt_chain.calls
                    df_calls = calls[(calls['strike'] >= S * 0.70) & (calls['strike'] <= S * 1.30)]
                    call_quotes = {float(r['strike']): {"ask": float(r['ask'] if r['ask']>0 else r['lastPrice']), "bid": float(r['bid'] if r['bid']>0 else r['lastPrice']*0.95), "iv": float(r['impliedVolatility'])} for _, r in df_calls.iterrows()}
                    for long_k in sorted(call_quotes.keys()):
                        for w in spread_widths:
                            short_k = long_k + w
                            if short_k in call_quotes:
                                res = check_bull_call(ticker_input, S, long_k, short_k, call_quotes[long_k]["ask"], call_quotes[short_k]["bid"], call_quotes[long_k]["iv"], T_years, risk_free_rate, min_ev_threshold)
                                if res:
                                    res["到期日"] = expiry_str
                                    res["DTE"] = dte
                                    res["下單"] = False # 初始化按鈕狀態值
                                    all_golden_combos.append(res)

                    # Put Spread
                    puts = opt_chain.puts
                    df_puts = puts[(puts['strike'] >= S * 0.70) & (puts['strike'] <= S * 1.05)]
                    put_quotes = {float(r['strike']): {"ask": float(r['ask'] if r['ask']>0 else r['lastPrice']), "bid": float(r['bid'] if r['bid']>0 else r['lastPrice']*0.95), "iv": float(r['impliedVolatility'])} for _, r in df_puts.iterrows()}
                    for short_k in sorted(put_quotes.keys()):
                        for w in spread_widths:
                            long_k = short_k - w
                            if long_k in put_quotes:
                                res = check_bull_put(ticker_input, S, short_k, long_k, put_quotes[short_k]["bid"], put_quotes[long_k]["ask"], put_quotes[short_k]["iv"], T_years, risk_free_rate, min_ev_threshold)
                                if res:
                                    res["到期日"] = expiry_str
                                    res["DTE"] = dte
                                    res["下單"] = False # 初始化按鈕狀態值
                                    all_golden_combos.append(res)
                
                if all_golden_combos:
                    df_res = pd.DataFrame(all_golden_combos)
                    df_res = df_res.sort_values(by=["DTE", "期望值 EV"], ascending=[True, False])
                    
                    # 調整顯示順序
                    display_cols = ["策略類型", "到期日", "DTE", "組合形態", "期望值 EV", "EV%", "全賺勝率", "淨收付", "最大獲利", "最大風險", "損益平衡點", "下單"]
                    df_display = df_res[display_cols].copy()

                    st.success(f"🎯 篩選完成！共找到 {len(df_display)} 組頂級正 EV 組合。")
                    
                    # 👇 🔥 【核心改動：使用 st.data_editor 渲染互動式表格並嵌入下單按鈕】
                    edited_df = st.data_editor(
                        df_display,
                        key="combo_table",
                        use_container_width=True,
                        hide_index=True,
                        disabled=["策略類型", "到期日", "DTE", "組合形態", "期望值 EV", "EV%", "全賺勝率", "淨收付", "最大獲利", "最大風險", "損益平衡點"], # 鎖定其他欄位不可編輯
                        column_config={
                            "下單": st.column_config.ButtonColumn(
                                "下單",
                                help="點擊直接發送此跨價期權單至 IBKR 帳戶",
                                default=False,
                                button_style="primary"
                            )
                        }
                    )
                    
                    # 偵測使用者點擊了哪一列的下單按鈕
                    for idx, row in edited_df.iterrows():
                        if row["下單"] == True:
                            # 找出對應原始資料中的隱藏參數（如 strike, action 等）
                            matched_origin = df_res[(df_res['到期日'] == row['到期日']) & (df_res['組合形態'] == row['組合形態'])].iloc[0]
                            
                            st.info(f"正在連線 IBKR 送出委託單： {row['組合形態']} ({row['到期日']})...")
                            
                            # 呼叫 IBKR 下單函式
                            success, message = place_ibkr_combo_order(
                                strategy_type=matched_origin["策略類型"],
                                symbol=ticker_input,
                                expiry_str=matched_origin["到期日"],
                                strike_1=matched_origin["strike_1"],
                                strike_2=matched_origin["strike_2"],
                                action_1=matched_origin["action_1"],
                                action_2=matched_origin["action_2"],
                                right_1=matched_origin["right_1"],
                                right_2=matched_origin["right_2"]
                            )
                            
                            if success:
                                st.success(message)
                            else:
                                st.error(message)
                else:
                    st.info("💡 目前市場報價中，無任何符合條件的策略組合。")
                    
            except Exception as e:
                st.error(f"分析過程中發生預期外錯誤: {e}")

# 顯示最後下單狀態
if st.session_state.order_status:
    st.warning(st.session_state.order_status)