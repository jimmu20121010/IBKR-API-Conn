# self.llm = ChatGoogleGenAI(model="gemini-2.0-flash")
# self.llm = ChatGroq(model="llama-3.3-70b-versatile")
# self.llm = ChatDeepSeek(model="deepseek-reasoner")

from ib_async import IB, Stock, MarketOrder, LimitOrder
ib = IB()
ib.connect('127.0.0.1', 7497, clientId=1)

# 定義交易標的
stock = Stock('TSLA', 'SMART', 'USD')
# 確保合約已經被IB識別
ib.qualifyContracts(stock)

# 1. 提交巿價單
market_order = MarketOrder('BUY', 10)
market_trade = ib.placeOrder(stock, market_order)
print(f"巿價單已提交: {market_trade}")

# 2. 提交限價單
limit_order = LimitOrder('BUY', 10, 430)
limit_trade = ib.placeOrder(stock, limit_order)
print(f"限價單已提交: {limit_trade}")
