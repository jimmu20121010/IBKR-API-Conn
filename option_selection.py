# =================================================================
# 🔥 頂部修補：必須放在任何導入的最前面，防止 Streamlit 多線程 asyncio 崩潰
# =================================================================
import asyncio
import sys
import time

try:
    loop = asyncio.get_event_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

# -----------------------------------------------------------------
# 修正完 Event Loop 後，導入其他必要套件
# -----------------------------------------------------------------
import streamlit as st
import math
import pandas as pd
from datetime import datetime, date
from scipy.stats import norm

# 引入 IBKR API 核心元件
from ib_insync import IB, Contract, ComboLeg, MarketOrder, LimitOrder, Stock, Option

# =================================================================
# 1. 核心數學模組 (Black-Scholes 勝率計算法)
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

# =================================================================
# 2. 策略金流與期望值 (EV) 計算過濾器
# =================================================================

def check_bull_call(S, long_k, short_k, long_ask, short_bid, iv, T_years, r, min_ev):
    """計算並篩選正統 Bull Call Debit Spread (買方)"""
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
        "strike_1": long_k, "strike_2": short_k,
        "action_1": "BUY", "action_2": "SELL", "right_1": "C", "right_2": "C"
    }

def check_bull_put(S, short_k, long_k, short_bid, long_ask, iv, T_years, r, min_ev):
    """計算並篩選正統 Bull Put Credit Spread (賣方)"""
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
        "strike_1": short_k, "strike_2": long_k,
        "action_1": "SELL", "action_2": "BUY", "right_1": "P", "right_2": "P"
    }

# =================================================================
# 3. Streamlit 前端網頁介面
# =================================================================

st.set_page_config(page_title="全 IBKR 智慧期權交易系統", layout="wide")
st.title("⚡ 全 IBKR 驅動 - 期權策略智慧篩選與下單系統")

# 側邊欄設定區
st.sidebar.header("⚙️ 1. 系統參數設定")
ticker_input = st.sidebar.text_input("輸入股票代號", value="SOFI").upper().strip()
max_dte = st.sidebar.slider("最大到期天數 (DTE)", min_value=7, max_value=120, value=45, step=7)
min_ev_threshold = st.sidebar.number_input("最低期望值門檻 (MIN_EV)", min_value=0.01, max_value=1.00, value=0.05, step=0.01)
risk_free_rate = st.sidebar.number_input("無風險利率", min_value=0.0, max_value=0.1, value=0.045, step=0.005)
spread_widths = st.sidebar.multiselect("允許的價差寬度 (Width)", options=[0.5, 1.0, 2.0, 3.0, 5.0], default=[0.5, 1.0, 2.0])

st.sidebar.header("🔌 2. IBKR 連線設定")
ib_host = st.sidebar.text_input("IBKR 主機 IP", value="127.0.0.1")
ib_port = st.sidebar.number_input("IBKR 監聽 Port", min_value=1, max_value=65535, value=7497, help="TWS 模擬帳戶通常為 7497，真實帳戶為 7496；IB Gateway 模擬為 4002，真實為 4001")

if st.sidebar.button("🚀 開始連線 IBKR 並掃描"):
    if not ticker_input:
        st.error("請輸入有效的股票代號！")
    else:
        # 實例化 IB 物件
        ib = IB()
        with st.spinner(f"正在嘗試與 IBKR ({ib_host}:{ib_port}) 建立安全連線..."):
            try:
                ib.connect(ib_host, int(ib_port), clientId=12)
                st.toast("✅ 已成功連接至 IBKR TWS/Gateway！", icon="🔌")
                
                # --- 步驟 A: 獲取正股合約與最新現價 ---
                stock_contract = Stock(ticker_input, 'SMART', 'USD')
                ib.qualifyContracts(stock_contract)
                
                # 請求即時市場快照數據
                ticker_data = ib.reqMktData(stock_contract, '', False, False)
                ib.sleep(1.5) # 等待 API 回傳報價
                
                # 優先抓取 Last 價格，若非交易時段則嘗試取 Close 或主動詢問市場
                S = ticker_data.last if (ticker_data.last and not math.isnan(ticker_data.last)) else ticker_data.close
                if not S or math.isnan(S) or S <= 0:
                    S = ticker_data.marketPrice()
                
                if not S or math.isnan(S) or S <= 0:
                    st.error(f"❌ 無法從 IBKR 獲取 {ticker_input} 的現價。請確認此代號正確或市場是否有報價權限。")
                    ib.disconnect()
                    st.stop()
                    
                st.metric(label=f"📊 IBKR 即時回傳 {ticker_input} 正股現價", value=f"${S:.2f}")
                
                # --- 步驟 B: 獲取該股所有期權鏈合約 ---
                st.text("正在從 IBKR 下載並解析期權鏈合約...")
                # 請求該正股對應的全部選擇權合約清單
                chains = ib.reqSecDefOptParams(stock_contract.symbol, '', stock_contract.secType, stock_contract.conId)
                
                if not chains:
                    st.error("❌ 無法取得該股票的期權合約鏈資訊。")
                    ib.disconnect()
                    st.stop()
                
                # 選擇 SMART 交易所或主交易所的合約鏈
                chain = next(c for c in chains if c.exchange == 'SMART' or c.exchange == '')
                
                today = date.today()
                valid_expiries = []
                for exp_str in chain.expirations:
                    # IBKR 回傳的格式通常為 '20260619'
                    exp_date = datetime.strptime(exp_str, "%Y%m%d").date()
                    dte = (exp_date - today).days
                    if 0 < dte <= max_dte:
                        valid_expiries.append((exp_str, dte))
                
                if not valid_expiries:
                    st.warning(f"⚠️ 在指定的最大天數 {max_dte} 天內，找不到符合的到期日合適合約。")
                    ib.disconnect()
                    st.stop()
                
                # --- 步驟 C: 批次獲取期權市場細節報價 (TWS 內網批次獲取極快，不限流)
                all_golden_combos = []
                st.text(f"正在掃描 {len(valid_expiries)} 個符合條件的到期日報價...")
                
                for exp_str, dte in valid_expiries:
                    T_years = dte / 365.0
                    
                    # 過濾出在現價上下 30% 區間的合理履約價，減少不必要的合約抓取，優化效能
                    filtered_strikes = [sk for sk in chain.strikes if S * 0.70 <= sk <= S * 1.30]
                    
                    # 打包建立這一批要請求報價的 Option 物件
                    option_contracts = []
                    for sk in filtered_strikes:
                        option_contracts.append(Option(ticker_input, exp_str, sk, 'C', 'SMART', 'USD'))
                        option_contracts.append(Option(ticker_input, exp_str, sk, 'P', 'SMART', 'USD'))
                    
                    # 補全合約 ConId
                    qualified_options = ib.qualifyContracts(*option_contracts)
                    
                    # 批次大量請求報價快照
                    tickers_list = ib.reqTickers(*qualified_options)
                    
                    # 整理出對應的字典格式方便提取
                    call_quotes = {}
                    put_quotes = {}
                    
                    for t in tickers_list:
                        k = t.contract.strike
                        # 抓取買賣價
                        ask = t.ask if (t.ask and not math.isnan(t.ask) and t.ask > 0) else t.lastPrice
                        bid = t.bid if (t.bid and not math.isnan(t.bid) and t.bid > 0) else (t.lastPrice * 0.95 if t.lastPrice else 0.01)
                        # 讀取 IBKR 計算好的隱含波動率 (Model IV)
                        iv = t.modelGreeks.impliedVol if (t.modelGreeks and t.modelGreeks.impliedVol) else 0.4
                        
                        if not ask or math.isnan(ask): continue
                        
                        if t.contract.right == 'C':
                            call_quotes[k] = {"ask": ask, "bid": bid, "iv": iv}
                        else:
                            put_quotes[k] = {"ask": ask, "bid": bid, "iv": iv}
                    
                    # --- 進行策略組合配對篩選 ---
                    # Call Spread 買方配對
                    for long_k in sorted(call_quotes.keys()):
                        for w in spread_widths:
                            short_k = long_k + w
                            if short_k in call_quotes:
                                res = check_bull_call(S, long_k, short_k, call_quotes[long_k]["ask"], call_quotes[short_k]["bid"], call_quotes[long_k]["iv"], T_years, risk_free_rate, min_ev_threshold)
                                if res:
                                    res["到期日"] = f"{exp_str[:4]}-{exp_str[4:6]}-{exp_str[6:]}" # 轉成網頁易讀格式 YYYY-MM-DD
                                    res["DTE"] = dte
                                    res["raw_expiry"] = exp_str
                                    res["下單"] = False
                                    all_golden_combos.append(res)
                                    
                    # Put Spread 賣方配對
                    for short_k in sorted(put_quotes.keys()):
                        for w in spread_widths:
                            long_k = short_k - w
                            if long_k in put_quotes:
                                res = check_bull_put(S, short_k, long_k, put_quotes[short_k]["bid"], put_quotes[long_k]["ask"], put_quotes[short_k]["iv"], T_years, risk_free_rate, min_ev_threshold)
                                if res:
                                    res["到期日"] = f"{exp_str[:4]}-{exp_str[4:6]}-{exp_str[6:]}"
                                    res["DTE"] = dte
                                    res["raw_expiry"] = exp_str
                                    res["下單"] = False
                                    all_golden_combos.append(res)

                # --- 步驟 D: 將數據呈現在互動表格中 ---
                if all_golden_combos:
                    df_res = pd.DataFrame(all_golden_combos)
                    df_res = df_res.sort_values(by=["DTE", "期望值 EV"], ascending=[True, False])
                    
                    display_cols = ["策略類型", "到期日", "DTE", "組合形態", "期望值 EV", "EV%", "全賺勝率", "淨收付", "最大獲利", "最大風險", "損益平衡點", "下單"]
                    df_display = df_res[display_cols].copy()

                    st.success(f"🎯 篩選完成！全 IBKR 報價系統共計找出 {len(df_display)} 組符合條件的頂級正 EV 組合。")
                    
                    edited_df = st.data_editor(
                        df_display,
                        key="ib_combo_table",
                        use_container_width=True,
                        hide_index=True,
                        disabled=["策略類型", "到期日", "DTE", "組合形態", "期望值 EV", "EV%", "全賺勝率", "淨收付", "最大獲利", "最大風險", "損益平衡點"],
                        column_config={
                            "下單": st.column_config.ButtonColumn(
                                "🚀 下單",
                                help="立刻發送該跨價組合（Combo Bag）委託至您開著的 IBKR 交易終端",
                                default=False,
                                button_style="primary"
                            )
                        }
                    )
                    
                    # 偵測並觸發即時下單
                    for idx, row in edited_df.iterrows():
                        if row["下單"] == True:
                            # 逆向找出該列在原資料組中的所有下單控制變數
                            matched = df_res[(df_res['到期日'] == row['到期日']) & (df_res['組合形態'] == row['組合形態'])].iloc[0]
                            
                            st.info(f"⚡ 正在建立 IBKR 雙腿組合單結構並發送： {row['組合形態']} ({row['到期日']})...")
                            
                            try:
                                # 1. 初始化兩條獨立腳位合約
                                leg1 = Option(ticker_input, matched["raw_expiry"], matched["strike_1"], matched["right_1"], 'SMART', 'USD')
                                leg2 = Option(ticker_input, matched["raw_expiry"], matched["strike_2"], matched["right_2"], 'SMART', 'USD')
                                
                                # 驗證與資格化這兩個期權合約
                                qc = ib.qualifyContracts(leg1, leg2)
                                if len(qc) < 2:
                                    st.error("無法在 IBKR 交易所資格化這兩個合約，下單終止。")
                                    continue
                                
                                # 2. 建立 BAG 組合單母合約
                                bag = Contract(symbol=ticker_input, secType='BAG', exchange='SMART', currency='USD')
                                combo_leg1 = ComboLeg(conId=qc[0].conId, ratio=1, action=matched["action_1"], exchange='SMART')
                                combo_leg2 = ComboLeg(conId=qc[1].conId, ratio=1, action=matched["action_2"], exchange='SMART')
                                bag.comboLegs = [combo_leg1, combo_leg2]
                                
                                # 3. 建立市價單（或可改為 LimitOrder）送出
                                order = MarketOrder(action='BUY', totalQuantity=1)
                                trade = ib.placeOrder(bag, order)
                                ib.sleep(1) # 給予緩衝時間發送
                                
                                st.success(f"🎉 跨價單發送成功！IB 訂單狀態: **{trade.orderStatus.status}**")
                            except Exception as order_err:
                                st.error(f"下單執行期間發生錯誤: {order_err}")
                else:
                    st.info("💡 掃描完畢：目前 IBKR 的報價中，暫時沒有符合您的期望值門檻與寬度設定的期權組合。")
                    
            except Exception as conn_e:
                st.error(f"❌ 無法連接至 IBKR 或執行分析失敗: {conn_e}")
            finally:
                # 確保在運作結束或被手動停止後，必定中斷與 TWS 的 Socket 連線，不佔用 ClientId
                if ib.isConnected():
                    ib.disconnect()