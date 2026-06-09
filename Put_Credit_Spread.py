# =================================================================
# 1. 🔥【絕對核心】必須放在檔案最頂端（第 1 行），確保任何導入前就修正完成 🔥
# =================================================================
import asyncio
import warnings

class Python314FixPolicy(asyncio.DefaultEventLoopPolicy):
    def __init__(self):
        super().__init__()
        self._loop = None
    def get_event_loop(self):
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        return self._loop

asyncio.set_event_loop_policy(Python314FixPolicy())
warnings.filterwarnings('ignore', category=DeprecationWarning)
warnings.filterwarnings('ignore', category=UserWarning)

# =================================================================
# 2. 安全導入其他套件
# =================================================================
import sys
import smtplib
import schedule
import time
import math
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from scipy.stats import norm

import yfinance as yf
from ib_insync import IB, Option, Contract, ComboLeg, LimitOrder  

# ─────────────────────────────────────────────
# ★ 使用者設定區
# ─────────────────────────────────────────────
TWS_HOST       = "127.0.0.1"
TWS_PORT       = 7497             
CLIENT_ID      = 10
SYMBOLS        = ["NOK", "ONDS", "RCAT", "RBLX", "JOBY", "SOFI"]
MAX_DTE        = 60               

EMAIL_SENDER   = "jimmu20121010@gmail.com"
EMAIL_PASSWORD = "vqpb dpmh tjby bprl"  
EMAIL_RECEIVER = "jimmu20121010@gmail.com"

SPREAD_WIDTHS   = [1, 2, 3]        
MIN_EV          = 0.1             # 高於此期望值的期權組合才會被列出與發送
RISK_FREE_RATE  = 0.045            
TRADE_SIZE      = 0                # 0 表示純監控不下單，調整為 1 以上可自動下單

DEFAULT_IVS = {
    "NOK": 0.35, "ONDS": 0.65, "RCAT": 0.80, 
    "RBLX": 0.50, "JOBY": 0.60, "SOFI": 0.55
}

# ─────────────────────────────────────────────
# 1. Black-Scholes 計算 (Put 專用機率邏輯)
# ─────────────────────────────────────────────
def bs_prob_itm_put(S, K, T, r, sigma) -> float:
    """計算期權到期時，股價低於 K (Put ITM，即跌破該履約價) 的機率"""
    if T <= 0 or sigma <= 0:
        return 0.5
    try:
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        return norm.cdf(-d2)  # 取得股價低於 K 的累積機率
    except Exception:
        return 0.5

def calculate_credit_ev(symbol: str, short_bid: float, long_ask: float,
                        short_strike: float, long_strike: float,
                        iv: float, S: float, T_years: float) -> dict:
    """計算 Bull Put Credit Spread 的期望值"""
    # 賣高買低，淨收入權利金 (Credit) = Short Bid - Long Ask
    credit = round(short_bid - long_ask, 4)
    width = short_strike - long_strike
    
    max_profit = credit  
    max_loss   = round(width - credit, 4)
    breakeven  = short_strike - credit  # 跌破 Short K 減去收到的保護金才是虧損起點

    if credit <= 0 or max_loss <= 0:
        return {}

    r = RISK_FREE_RATE
    # 計算各個關鍵點位被跌破的機率
    p_below_short = bs_prob_itm_put(S, short_strike, T_years, r, iv)  
    p_below_long  = bs_prob_itm_put(S, long_strike, T_years, r, iv)   
    
    p_full_loss   = max(0.0, p_below_long)                        # 兩檔皆跌破 = 全賠
    p_partial     = max(0.0, p_below_short - p_below_long)        # 介於兩者之間 = 部分損益
    p_full_profit = max(0.0, 1.0 - p_below_short)                 # 股價維持在 Short 之上 = 全賺

    avg_partial = (max_profit - max_loss) * 0.5
    
    ev = (p_full_profit * max_profit + p_partial * avg_partial - p_full_loss * max_loss)
    ev = round(ev, 4)

    return {
        "symbol":         symbol,
        "short_strike":   short_strike,  
        "long_strike":    long_strike,   
        "credit":         credit,         
        "max_profit":     max_profit,
        "max_loss":       max_loss,
        "breakeven":      round(breakeven, 2),
        "need_move_pct":  round((breakeven - S) / S * 100, 2),
        "p_full_profit":  round(p_full_profit * 100, 1),
        "p_partial":      round(p_partial     * 100, 1),
        "p_loss":         round(p_full_loss   * 100, 1), # 全賠機率
        "ev":             ev,
        "ev_pct":         round(ev / max_loss * 100, 1), 
        "max_return_pct": round(max_profit / max_loss * 100, 1), # 報酬率 = 最大獲利 / 保證金風險
        "iv":             round(iv * 100, 1),
        "short_bid":      short_bid,
        "long_ask":       long_ask
    }

# ─────────────────────────────────────────────
# 2. Email 發送
# ─────────────────────────────────────────────
def send_multi_symbol_email(all_results_list: list):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    table_rows = ""
    
    for r in all_results_list:
        order_status = f"<br><span style='color:#ff9999;font-size:11px;'>{r.get('order_msg','')}</span>" if 'order_msg' in r else ""
        table_rows += f"""
        <tr style="background:#1e1e1e; text-align:center;">
          <td style="padding:10px; color:#ffcc00; font-weight:bold;">{r['symbol']}</td>
          <td style="color:#00ccff">${r['spot']:.2f}</td>
          <td style="color:#ff9900; background:#1f1911;"><b>{r['expiry']}</b><br><span style="font-size:11px;color:#aaa;">({r['dte']} 天後)</span></td>
          <td><b style="color:#5cd65c;">Sell {r['short_strike']} (OTM)</b> / Buy {r['long_strike']} (OTM){order_status}</td>
          <td style="color:#00ff88;">${r['credit']:.2f}</td>
          <td style="color:#ff4d4d;">${r['max_loss']:.2f}</td>
          <td style="background:#153320; color:#00ff88; font-weight:bold;">+${r['ev']:.4f} ({r['ev_pct']}%)</td>
          <td>{r['p_full_profit']}% / {r['p_loss']}%</td>
          <td>${r['breakeven']} ({'+' if r['need_move_pct']>=0 else ''}{r['need_move_pct']}%)</td>
          <td style="color:#00ff88">+{r['max_return_pct']}%</td>
        </tr>
        """

    html_body = f"""
    <html><body style="background:#121212;color:#e0e0e0;font-family:Consolas,monospace;padding:20px">
      <h2 style="color:#00ccff"> 📈 yfinance + IBKR 60天 Bull Put Credit Spread 監控報告 (正統 OTM 賣方策略版)</h2>
      <p style="color:#888">掃描時間：{ts} CST | <b>核心規則：強制 Short Put 與 Long Put 皆為價外 (OTM) ➔ 排序：1. 到期日 ➔ 2. 期望值 EV</b></p>
      <table style="border-collapse:collapse;width:100%; font-size:14px; border:1px solid #333;">
        <tr style="background:#2a2a2a;color:#aaa; height:35px;">
          <th>股票代碼</th><th>股票現價</th><th>期權到期日 (主排序)</th><th>價差履約價組合 (Put)</th><th style="color:#00ff88">淨收入(Credit)</th><th style="color:#ff4d4d">最大風險</th>
          <th style="color:#00ff88">期望值 EV (次排序)</th><th>P(全賺)/P(全賠)</th><th>損益平衡(距現價)</th><th>最大報酬率</th>
        </tr>
        {table_rows}
      </table>
      <p style="color:#888; font-size:12px; margin-top:15px;">註：本系統已自動過濾所有非標準形態之組合，僅留存「高勝率價外正賣方收租」的正統正 EV 機會。</p>
    </body></html>
    """

    msg = MIMEMultipart("alternative")
    distinct_symbols = sorted(list(set([x['symbol'] for x in all_results_list])))
    msg["Subject"] = f"[Bull Put正統監控] {', '.join(distinct_symbols)} 共 {len(all_results_list)} 組正統正 EV 報告"
    msg["From"]    = EMAIL_SENDER
    msg["To"]      = EMAIL_RECEIVER
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())
        print(f"[OK] 正統 Bull Put 報告已發送至 {EMAIL_RECEIVER}")
    except Exception as e:
        print(f"[ERROR] Email 發送失敗: {e}")

# ─────────────────────────────────────────────
# 3. yfinance 核心分析端 (加入正統 OTM 賣權核心安全機制)
# ─────────────────────────────────────────────
def run_yfinance_analysis(symbol: str) -> list:
    print(f"\n[分析] 開始透過 yfinance 掃描 {symbol} (限正統 OTM 區間) ...")
    symbol_all_ev_combos = []
    try:
        ticker = yf.Ticker(symbol)
        fast_info = ticker.fast_info
        S = fast_info.get('lastPrice', None)
        
        if not S or math.isnan(S) or S <= 0:
            hist = ticker.history(period="1d")
            if not hist.empty:
                S = hist['Close'].iloc[-1]

        if not S or math.isnan(S) or S <= 0:
            print(f"[ERROR] 無法獲取 {symbol} 的正股價格，跳過")
            return []
            
        print(f"[OK] {symbol} 目前股價: ${S:.2f}")

        today = datetime.now().date()
        valid_expiries = []
        
        for opt_date_str in ticker.options:
            try:
                exp_date = datetime.strptime(opt_date_str, "%Y-%m-%d").date()
                dte = (exp_date - today).days
                if 0 < dte <= MAX_DTE:
                    valid_expiries.append((opt_date_str, dte))
            except ValueError:
                continue

        if not valid_expiries:
            print(f"[WARN] {symbol} 找不到任何在 {MAX_DTE} 天內到期的期權鏈")
            return []
        
        for expiry_str, dte in valid_expiries:
            try:
                opt_chain = ticker.option_chain(expiry_str)
                puts = opt_chain.puts  # 切換為 Puts 賣權數據鏈
                
                # 篩選安全下行 OTM 區間（股價下方 30% 到現價之間）
                lo_strike = S * 0.70
                hi_strike = S * 1.05
                df_puts = puts[(puts['strike'] >= lo_strike) & (puts['strike'] <= hi_strike)]
                
                if df_puts.empty:
                    continue

                strike_quotes = {}
                for _, row in df_puts.iterrows():
                    strike = float(row['strike'])
                    ask_val = float(row['ask']) if row['ask'] > 0 else float(row['lastPrice'])
                    bid_val = float(row['bid']) if row['bid'] > 0 else float(row['lastPrice']) * 0.95
                    
                    iv = float(row['impliedVolatility']) if row['impliedVolatility'] > 0 else None
                    if not iv or math.isnan(iv):
                        iv = DEFAULT_IVS.get(symbol, 0.50)

                    strike_quotes[strike] = {"bid": bid_val, "ask": ask_val, "iv": iv}

                T_years = dte / 365.0
                sorted_strikes = sorted(strike_quotes.keys())

                # 建立正統 Bull Put Credit Spread (賣高履約價 Short K，買低履約價 Long K)
                for i, short_k in enumerate(sorted_strikes):
                    
                    # 💡【核心修改點 1】強迫高履約價的賣方 (Short Put) 必須是「價外 (OTM)」
                    if short_k >= S:
                        continue
                        
                    short_q = strike_quotes[short_k]
                    for width in SPREAD_WIDTHS:
                        long_k = short_k - width  # 買方履約價更低
                        if long_k not in strike_quotes:
                            continue
                            
                        # 💡【核心修改點 2】買方 (Long Put) 自然也必須是「價外 (OTM)」
                        # 由於 long_k < short_k < S，此條件已自動滿足
                        long_q = strike_quotes[long_k]
                        
                        res = calculate_credit_ev(
                            symbol=symbol, short_bid=short_q["bid"], long_ask=long_q["ask"],
                            short_strike=short_k, long_strike=long_k, iv=short_q["iv"], S=S, T_years=T_years
                        )
                        if res and res["ev"] >= MIN_EV:
                            res["expiry"] = expiry_str
                            res["dte"]    = dte
                            res["spot"]   = S
                            symbol_all_ev_combos.append(res)
            except Exception:
                continue

        return symbol_all_ev_combos

    except Exception as e:
        print(f"[ERROR] yfinance 解析 {symbol} 失敗: {e}")
        return []

# ─────────────────────────────────────────────
# 4. IBKR 交易執行端 
# ─────────────────────────────────────────────
def execute_ib_orders(ib: IB, best_combo: dict):
    if TRADE_SIZE <= 0:
        return "[純監控不下單]"

    symbol = best_combo['symbol']
    short_k = best_combo['short_strike']  
    long_k = best_combo['long_strike']    
    net_credit = round(best_combo['max_profit'], 2)  
    ib_expiry = best_combo['expiry'].replace("-", "")

    try:
        short_contract = Option(symbol=symbol, lastTradeDateOrContractMonth=ib_expiry, strike=short_k, right='P', exchange='SMART')
        long_contract = Option(symbol=symbol, lastTradeDateOrContractMonth=ib_expiry, strike=long_k, right='P', exchange='SMART')
        
        ib.qualifyContracts(short_contract, long_contract)

        combo_contract = Contract()
        combo_contract.symbol = symbol
        combo_contract.secType = 'BAG'
        combo_contract.currency = 'USD'
        combo_contract.exchange = 'SMART'
        
        # 賣高買低：組合單為淨收入（Credit Spread 下單時在 IB 屬於 SELL 組合，或以負數限價買入）
        # 正統 BAG 組合標準作法：
        leg1 = ComboLeg(conId=short_contract.conId, ratio=1, action='SELL', exchange='SMART')
        leg2 = ComboLeg(conId=long_contract.conId, ratio=1, action='BUY', exchange='SMART')
        combo_contract.comboLegs = [leg1, leg2]

        # 在 IBKR 中，BAG 組合單如果是收權利金，Limit Price 請給負數 (例如收 $0.30 則輸入 -0.30)
        # 或者 action 用 'SELL' 搭配正數。這裡使用國際標準的 BUY 搭配負數限價 (收錢)
        order = LimitOrder(action='BUY', totalQuantity=TRADE_SIZE, lmtPrice=-net_credit)
        order.outsideRth = True  

        trade = ib.placeOrder(combo_contract, order)
        print(f"[SUCCESS] {symbol} 正統 Bull Put 組合收租單已送出！({best_combo['expiry']})")
        return "[已自動送單排隊]"

    except Exception as e:
        print(f"[ERROR] {symbol} IBKR 下單失敗: {e}")
        return f"[下單失敗: {e}]"

# ─────────────────────────────────────────────
# 5. 主排程控制迴圈
# ─────────────────────────────────────────────
def job():
    print("\n" + "="*60)
    print(f"啟動新一輪 正統 Bull Put (OTM收租型) 掃描：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)
    
    global_all_valid_combos = []
    
    for sym in SYMBOLS:
        symbol_list = run_yfinance_analysis(sym)
        if symbol_list:
            global_all_valid_combos.extend(symbol_list)
        time.sleep(1.0) 

    if global_all_valid_combos:
        global_all_valid_combos.sort(key=lambda x: (x['dte'], -x['ev']))

        print("\n" + "★"*10 + " 終端機安全預覽 (正統 OTM 區間 ➔ 1.到期日 ➔ 2.EV由大到小) " + "★"*10)
        for item in global_all_valid_combos:
            print(f" 到期日: {item['expiry']} ({item['dte']:2d}天) | EV: +${item['ev']:.4f} ({item['ev_pct']:.1f}%) | 全賺勝率: {item['p_full_profit']}% | 組合: Sell ${item['short_strike']} (OTM) / Buy ${item['long_strike']} (OTM)")
        print("★"*65 + "\n")

        ib = IB()
        try:
            print(f"[IBKR] 正在連線至 TWS (Port: {TWS_PORT}) ...")
            ib.connect(TWS_HOST, TWS_PORT, clientId=CLIENT_ID)
            
            best_ev_per_symbol = {}
            for item in global_all_valid_combos:
                sym = item['symbol']
                if sym not in best_ev_per_symbol or item['ev'] > best_ev_per_symbol[sym]['ev']:
                    best_ev_per_symbol[sym] = item

            triggered_symbols = set()
            for item in global_all_valid_combos:
                sym = item['symbol']
                if item == best_ev_per_symbol[sym] and sym not in triggered_symbols:
                    order_msg = execute_ib_orders(ib, item)
                    item['order_msg'] = order_msg
                    triggered_symbols.add(sym)
                else:
                    item['order_msg'] = "[觀測組合 - 未重疊下單]"
                ib.sleep(0.1)

        except Exception as e:
            print(f"[CRITICAL] TWS 連線失敗: {e}")
            for item in global_all_valid_combos:
                item['order_msg'] = "[TWS連線失敗]"
        finally:
            if ib.isConnected():
                ib.disconnect()

        send_multi_symbol_email(global_all_valid_combos)
    else:
        print("\n[INFO] 本輪完成：目前無任何符合「正統 Bull Put OTM 形態 且 正 EV」的組合。\n")


if __name__ == "__main__":
    print("=" * 75)
    print("[啟動成功] Bull Put Credit 正統 OTM 賣方收租監控程式已開始運作...")
    print("=" * 75)
    
    job()
    schedule.every(10).minutes.do(job)