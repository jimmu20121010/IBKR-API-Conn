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
#SYMBOLS        = ["NOK", "ONDS", "RCAT", "RBLX", "JOBY", "SOFI"]
SYMBOLS        = ["NOK"]
MAX_DTE        = 60               

EMAIL_SENDER   = "jimmu20121010@gmail.com"
EMAIL_PASSWORD = "vqpb dpmh tjby bprl"  
EMAIL_RECEIVER = "jimmu20121010@gmail.com"

SPREAD_WIDTHS   = [1, 2, 3]        
MIN_EV          = 0.05             # 高於此期望值的期權組合才會被列出與發送
RISK_FREE_RATE  = 0.045            
TRADE_SIZE      = 0                # 0 表示純監控不下單，調整為 1 以上可自動下單

DEFAULT_IVS = {
    "NOK": 0.35, "ONDS": 0.65, "RCAT": 0.80, 
    "RBLX": 0.50, "JOBY": 0.60, "SOFI": 0.55
}

# ─────────────────────────────────────────────
# 1. Black-Scholes 計算
# ─────────────────────────────────────────────
def bs_prob_itm(S, K, T, r, sigma) -> float:
    if T <= 0 or sigma <= 0:
        return 0.5
    try:
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        return norm.cdf(d2)
    except Exception:
        return 0.5

def calculate_ev(symbol: str, long_ask: float, short_bid: float,
                 long_strike: float, short_strike: float,
                 iv: float, S: float, T_years: float) -> dict:
    debit      = round(long_ask - short_bid, 4)
    width      = short_strike - long_strike
    max_profit = round(width - debit, 4)
    breakeven  = long_strike + debit

    if debit <= 0 or max_profit <= 0:
        return {}

    r = RISK_FREE_RATE
    p_above_short  = bs_prob_itm(S, short_strike, T_years, r, iv)
    p_above_long   = bs_prob_itm(S, long_strike,  T_years, r, iv)

    p_full_profit = max(0.0, p_above_short)
    p_partial     = max(0.0, p_above_long - p_above_short)
    p_loss        = max(0.0, 1 - p_above_long)

    avg_partial = max_profit * 0.5
    ev = (p_full_profit * max_profit + p_partial * avg_partial - p_loss * debit)
    ev = round(ev, 4)

    return {
        "symbol":         symbol,
        "long_strike":    long_strike,
        "short_strike":   short_strike,
        "debit":          debit,
        "max_profit":     max_profit,
        "max_loss":       debit,
        "breakeven":      round(breakeven, 2),
        "need_move_pct":  round((breakeven - S) / S * 100, 2),
        "p_full_profit":  round(p_full_profit * 100, 1),
        "p_partial":      round(p_partial     * 100, 1),
        "p_loss":         round(p_loss        * 100, 1),
        "ev":             ev,
        "ev_pct":         round(ev / debit * 100, 1),
        "max_return_pct": round(max_profit / debit * 100, 1),
        "iv":             round(iv * 100, 1),
        "long_ask":       long_ask,
        "short_bid":      short_bid
    }

# ─────────────────────────────────────────────
# 2. Email 發送 (優化排序版)
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
          <td><b>{r['long_strike']} / {r['short_strike']}</b>{order_status}</td>
          <td>${r['debit']:.2f}</td>
          <td style="background:#153320; color:#00ff88; font-weight:bold;">+${r['ev']:.4f} ({r['ev_pct']}%)</td>
          <td>{r['p_full_profit']}% / {r['p_loss']}%</td>
          <td>${r['breakeven']} ({'+' if r['need_move_pct']>=0 else ''}{r['need_move_pct']}%)</td>
          <td style="color:#00ff88">+{r['max_return_pct']}%</td>
        </tr>
        """

    html_body = f"""
    <html><body style="background:#121212;color:#e0e0e0;font-family:Consolas,monospace;padding:20px">
      <h2 style="color:#00ccff"> 📊 yfinance + IBKR 60天 Call Debit Spread 監控報告</h2>
      <p style="color:#888">掃描時間：{ts} CST | <b>排序規則：1. 到期日(由近到遠) ➔ 2. 期望值EV(由大到小)</b></p>
      <table style="border-collapse:collapse;width:100%; font-size:14px; border:1px solid #333;">
        <tr style="background:#2a2a2a;color:#aaa; height:35px;">
          <th>股票代碼</th><th>股票現價</th><th>期權到期日 (主排序)</th><th>價差履約價組合</th><th>淨成本(Debit)</th>
          <th style="color:#00ff88">期望值 EV (次排序)</th><th>P(全賺)/P(全賠)</th><th>損益平衡(距現價)</th><th>最大報酬率</th>
        </tr>
        {table_rows}
      </table>
      <p style="color:#888; font-size:12px; margin-top:15px;">註：為避免重複下單與資金過度暴險，若開啟自動下單，系統每輪僅會挑選各標的「EV最高」的那個組合進行送單，其餘組合僅供觀測分析。</p>
    </body></html>
    """

    msg = MIMEMultipart("alternative")
    distinct_symbols = sorted(list(set([x['symbol'] for x in all_results_list])))
    msg["Subject"] = f"[Call Debit Spread監控] {', '.join(distinct_symbols)} 共 {len(all_results_list)} 組正 EV 報告"
    msg["From"]    = EMAIL_SENDER
    msg["To"]      = EMAIL_RECEIVER
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())
        print(f"[OK] 排序報告已發送至 {EMAIL_RECEIVER}")
    except Exception as e:
        print(f"[ERROR] Email 發送失敗: {e}")

# ─────────────────────────────────────────────
# 3. yfinance 核心分析端
# ─────────────────────────────────────────────
def run_yfinance_analysis(symbol: str) -> list:
    print(f"\n[分析] 開始透過 yfinance 掃描 {symbol} (60天內所有期權日期) ...")
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

        print(f"[INFO] {symbol} 偵測到 {len(valid_expiries)} 個合規到期日...")
        
        for expiry_str, dte in valid_expiries:
            try:
                opt_chain = ticker.option_chain(expiry_str)
                calls = opt_chain.calls
                
                lo_strike = S * 0.80
                hi_strike = S * 1.25
                df_calls = calls[(calls['strike'] >= lo_strike) & (calls['strike'] <= hi_strike)]
                
                if df_calls.empty:
                    continue

                strike_quotes = {}
                for _, row in df_calls.iterrows():
                    strike = float(row['strike'])
                    ask_val = float(row['ask']) if row['ask'] > 0 else float(row['lastPrice'])
                    bid_val = float(row['bid']) if row['bid'] > 0 else float(row['lastPrice']) * 0.95
                    
                    iv = float(row['impliedVolatility']) if row['impliedVolatility'] > 0 else None
                    if not iv or math.isnan(iv):
                        iv = DEFAULT_IVS.get(symbol, 0.50)

                    strike_quotes[strike] = {"bid": bid_val, "ask": ask_val, "iv": iv}

                T_years = dte / 365.0
                sorted_strikes = sorted(strike_quotes.keys())

                for i, long_k in enumerate(sorted_strikes):
                    long_q = strike_quotes[long_k]
                    for width in SPREAD_WIDTHS:
                        short_k = long_k + width
                        if short_k not in strike_quotes:
                            continue
                        short_q = strike_quotes[short_k]
                        
                        res = calculate_ev(
                            symbol=symbol, long_ask=long_q["ask"], short_bid=short_q["bid"],
                            long_strike=long_k, short_strike=short_k, iv=long_q["iv"], S=S, T_years=T_years
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
# 4. IBKR 交易執行端 (安全防禦：每隻股票只下單最高的一組)
# ─────────────────────────────────────────────
def execute_ib_orders(ib: IB, best_combo: dict):
    if TRADE_SIZE <= 0:
        return "[純監控不下單]"

    symbol = best_combo['symbol']
    long_k = best_combo['long_strike']
    short_k = best_combo['short_strike']
    net_debit = round(best_combo['debit'], 2)  
    ib_expiry = best_combo['expiry'].replace("-", "")

    try:
        long_contract = Option(symbol=symbol, lastTradeDateOrContractMonth=ib_expiry, strike=long_k, right='C', exchange='SMART')
        short_contract = Option(symbol=symbol, lastTradeDateOrContractMonth=ib_expiry, strike=short_k, right='C', exchange='SMART')
        
        ib.qualifyContracts(long_contract, short_contract)

        combo_contract = Contract()
        combo_contract.symbol = symbol
        combo_contract.secType = 'BAG'
        combo_contract.currency = 'USD'
        combo_contract.exchange = 'SMART'
        
        leg1 = ComboLeg(conId=long_contract.conId, ratio=1, action='BUY', exchange='SMART')
        leg2 = ComboLeg(conId=short_contract.conId, ratio=1, action='SELL', exchange='SMART')
        combo_contract.comboLegs = [leg1, leg2]

        order = LimitOrder(action='BUY', totalQuantity=TRADE_SIZE, lmtPrice=net_debit)
        order.outsideRth = True  

        trade = ib.placeOrder(combo_contract, order)
        print(f"[SUCCESS] {symbol} 最佳組合單已送出交易系統！({best_combo['expiry']})")
        return "[已自動送單排隊]"

    except Exception as e:
        print(f"[ERROR] {symbol} IBKR 下單失敗: {e}")
        return f"[下單失敗: {e}]"

# ─────────────────────────────────────────────
# 5. 主排程控制迴圈 (導入雙重排序邏輯)
# ─────────────────────────────────────────────
def job():
    print("\n" + "="*60)
    print(f"啟動新一輪雙重排序清單掃描：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)
    
    global_all_valid_combos = []
    
    # 搜集所有股票符合條件的資料
    for sym in SYMBOLS:
        symbol_list = run_yfinance_analysis(sym)
        if symbol_list:
            global_all_valid_combos.extend(symbol_list)
        time.sleep(1.0) 

    if global_all_valid_combos:
        # 💡 🔥【核心修改點】雙重排序控制邏輯：
        # x['dte'] 正序表示到期日由小到大 (1.主排序)
        # -x['ev'] 負序表示期望值由大到小 (2.次排序)
        global_all_valid_combos.sort(key=lambda x: (x['dte'], -x['ev']))

        print("\n" + "★"*10 + " 終端機預覽 (1.到期日由小到大 ➔ 2.EV由大到小) " + "★"*10)
        for item in global_all_valid_combos:
            print(f" 到期日: {item['expiry']} ({item['dte']:2d}天) | EV: +${item['ev']:.4f} ({item['ev_pct']:.1f}%) | 標的: {item['symbol']:5s} | 組合: Buy ${item['long_strike']} / Sell ${item['short_strike']}")
        print("★"*65 + "\n")

        # 連接交易端 (保持下單防禦：每隻股票每輪只會下單自身 EV 最高的一組)
        ib = IB()
        try:
            print(f"[IBKR] 正在連線至 TWS (Port: {TWS_PORT}) ...")
            ib.connect(TWS_HOST, TWS_PORT, clientId=CLIENT_ID)
            
            # 為了找出每檔股票真正的最高 EV 組合，我們先對局部做標的過濾
            # 雖然清單已經為了 Email 改成按日期排，但自動下單的權重依然要選該股票 EV 最高者
            best_ev_per_symbol = {}
            for item in global_all_valid_combos:
                sym = item['symbol']
                if sym not in best_ev_per_symbol or item['ev'] > best_ev_per_symbol[sym]['ev']:
                    best_ev_per_symbol[sym] = item

            # 根據剛才算出的局部最高 EV 對象，在主清單中進行下單狀態標記
            triggered_symbols = set()
            for item in global_all_valid_combos:
                sym = item['symbol']
                # 檢查目前這個合約是不是該標的在全場中 EV 最高的那一個
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

        # 發送按照「1.到期日小到大 ➔ 2.EV大到小」精心排序的 Email
        send_multi_symbol_email(global_all_valid_combos)
    else:
        print("\n[INFO] 本輪完成：目前無任何正 EV 組合。\n")


if __name__ == "__main__":
    print("=" * 75)
    print("[啟動成功] 雙重排序優化監控程式已開始運作...")
    print("=" * 75)
    
    job()
    schedule.every(10).minutes.do(job)
    
'''    while True:
        try:
            schedule.run_pending()
            time.sleep(1)
        except KeyboardInterrupt:
            print("\n[INFO] 使用者手動終止程式。")
            break
        except Exception as e:
            print(f"[排程主迴圈異常]: {e}")
            time.sleep(10) '''