import asyncio
from ib_async import IB, Stock, MarketOrder, util

async def execute_portfolio_orders(weights_df, total_investment=10000):
    """
    weights_df: 剛剛 Riskfolio 算出來的 w
    total_investment: 這次組合要投入的總金額（美金）
    """
    ib = IB()
    print("正在連線到 TWS 以執行組合調倉...")
    # 使用之前成功的 Python 3.14 兼容連線方式
    await ib.connectAsync('127.0.0.1', 7497, clientId=1)
    
    try:
        print("\n====== 開始執行優化組合下單 ======")
        # 尋訪 Riskfolio 計算出來的每一檔股票與權重
        for symbol, row in weights_df.iterrows():
            weight = row['weights']
            
            # 如果權重太低（例如小於 0.1%），就跳過不買
            if weight < 0.001:
                continue
                
            # 1. 定義合約
            contract = Stock(symbol, 'SMART', 'USD')
            await ib.qualifyContractsAsync(contract)
            
            # 2. 獲取當前市價以計算股數（這裡用 marketPrice 估算）
            # 實戰中也可以抓取 ticker.marketPrice()
            tickers = await ib.reqTickersAsync(contract)
            market_price = tickers[0].marketPrice()
            
            if market_price is None or market_price <= 0:
                print(f"無法取得 {symbol} 的即時價格，跳過此標的。")
                continue
            
            # 3. 依權重計算分配金額與下單股數
            allocated_money = total_investment * weight
            quantity = int(allocated_money / market_price)
            
            if quantity > 0:
                print(f"標的: {symbol} | 權重: {weight:.2%} | 分配金額: ${allocated_money:.2f} | 預計購買股數: {quantity} 股")
                
                # 4. 送出市價單或進場限價單 (此處以市價單示範)
                order = MarketOrder('BUY', quantity)
                trade = ib.placeOrder(contract, order)
                
                # 提示：你也可以在這裡把進場單改成你之前寫的 ib.bracketOrder(...)！
                
        print("\n組合訂單已全數送出，請至 TWS 確認。")
        await asyncio.sleep(5)
        
    except Exception as e:
        print(f"下單過程中發生錯誤: {e}")
    finally:
        ib.disconnect()
        print("已中斷 TWS 連線。")

# 執行下單整合的呼叫方式（示意）：
# weights_df 的結構通常是 index=股票代碼, column=['weights']
# await execute_portfolio_orders(w, total_investment=20000)