import pytz
import asyncio
from ib_async import IB, Stock, util

# ----------------------------------------------------
# Python 3.14 + Thonny 專用終極補丁
# ----------------------------------------------------
async def patched_wait_for(fut, timeout):
    """強行取代 asyncio.wait_for，避開 Python 3.14 的 Task 限制"""
    return await asyncio.ensure_future(fut)

# 執行替換
asyncio.wait_for = patched_wait_for
util.patchAsyncio()
# ----------------------------------------------------

def main():
    ib = IB()
    
    print("正在連線到交易伺服器（已套用 Python 3.14 補丁）...")
    # 使用同步連線，底層會被我們的補丁保護
    ib.connect('127.0.0.1', 7497, clientId=1)
    
    try:
        # 獲取使用者輸入
        symbol = input('請輸入股票代碼 (例如 AAPL): ').strip().upper()
        contract = Stock(symbol, 'SMART', 'USD')
        
        print(f"正在驗證 {symbol} 合約資訊...")
        ib.qualifyContracts(contract)
        
        entryPrice = float(input("請輸入做多價格（進場限價）: "))
        quantity = int(input("請輸入下單股數 (例如 1): "))
        profit_ratio = float(input("請輸入止盈比率 % (例如 5): "))
        stop_ratio = float(input("請輸入止損比率 % (例如 3): "))
        
        takeProfitPrice = round(entryPrice * (1 + profit_ratio / 100), 2)
        stopLossPrice = round(entryPrice * (1 - stop_ratio / 100), 2)
        
        print("\n--- 訂單預覽 ---")
        print(f"進場限價: {entryPrice}")
        print(f"止盈價格: {takeProfitPrice} (+{profit_ratio}%)")
        print(f"止損價格: {stopLossPrice} (-{stop_ratio}%)")
        print("----------------\n")
        
        confirm = input("確認要送出此 Bracket 括號訂單嗎？(y/n): ").strip().lower()
        if confirm != 'y':
            print("訂單已取消。")
            return
            
        # 建立 Bracket Order
        brackets = ib.bracketOrder(
            action='BUY',               
            quantity=quantity,          
            limitPrice=entryPrice,      
            takeProfitPrice=takeProfitPrice,  
            stopLossPrice=stopLossPrice       
        )
        
        # 送出這一組訂單
        print("正在送出括號訂單到 TWS...")
        for order in brackets:
            order.tif = 'GTC'  # 设置订单的有效期为 "Good Till Canceled"
            ib.placeOrder(contract, order)
    
        print("訂單已成功送出！請至 TWS 介面查看連動圖表。")
        
        print("等待 5 秒讓訂單同步...")
        ib.sleep(5)
        
    except Exception as e:
        print(f"發生錯誤: {e}")
        
    finally:
        ib.disconnect()
        print("已斷開與伺服器的連線。")

if __name__ == "__main__":
    main()