from ib_async import IB, util

# 初始化 IB 物件
ib = IB()

# 建議：使用 util.run 確保非同步事件迴圈正常運作
with ib.connect('127.0.0.1', 7497, clientId=1):
    
    # === 1. 讀取持倉資訊 ===
    positions = ib.positions()
    print("====== 當前持倉資訊 ======")
    if not positions:
        print("目前沒有持倉。")
    for position in positions:
        # 計算總投入成本
        total_cost = position.position * position.avgCost
        print(f"合約: {position.contract.symbol:<5} | "
              f"持有數量: {position.position:<6} | "
              f"平均成本: {position.avgCost:.2f} | "
              f"總投入成本: {total_cost:.2f}")
    
    print("\n" + "="*30 + "\n")
    
    # === 2. 讀取帳戶真正市值與資產（帳戶資產總覽） ===
    print("====== 帳戶資產總覽 ======")
    # 透過 accountSummary 抓取淨資產價值與可用資金
    summary = ib.accountSummary()
    
    for item in summary:
        # 我們只篩選出比較關鍵的幾個數據（如淨資產、總現金、股票市值）
        if item.tag in ['NetLiquidation', 'TotalCashValue', 'StockMarketValue', 'GrossPositionValue']:
            # 翻譯一下標籤讓輸出更好讀
            tag_name = {
                'NetLiquidation': '帳戶總資產 (淨清算價值)',
                'TotalCashValue': '目前總現金',
                'StockMarketValue': '股票目前總市值',
                'GrossPositionValue': '總持倉部位價值'
            }.get(item.tag, item.tag)
            
            print(f"{tag_name}: {item.value} {item.currency}")