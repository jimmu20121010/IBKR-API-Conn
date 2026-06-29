IBKR-API-Conn
使用美股券商盈透證券API串接進行下單
使用Yahoo Finance API 挑選符合正EV>1且60天內的期權組合, 並發送EMAIL


# Global Market Analyzer

## 功能

| 模組 | 內容 |
|------|------|
| 全球股票指數 | 亞太(台股/韓/日/港/滬)、歐洲(英/德/法/荷)、美股(道瓊/那指/S&P500/費半) |
| 殖利率曲線   | 美債 2Y / 10Y / 30Y + 2Y-10Y 利差倒掛警示 |
| 商品         | 黃金 / WTI / Brent / 銅 / 白銀 / 天然氣 |
| 匯率         | DXY / EUR / JPY / CNY / TWD / KRW / GBP |
| 市場情緒     | VIX + CNN Fear & Greed |
| 輸出         | Excel (多 Sheet + 條件色)、SQLite、Email HTML 報告 |

## 安裝

```bash
pip install -r requirements.txt
cp .env.example .env   # 填入你的 Gmail App Password
```

## 執行

```bash
python main.py
```

## Excel Sheet 說明

- **全球股票指數**：現價 / 單日% / 52W高 / 距高點%（自動上色）
- **殖利率曲線**：美債各期利率 + 利差（倒掛時整列紅色）
- **商品**：黃金/原油/銅等期貨
- **匯率**：DXY / 主要貨幣對
- **Run Info**：本次執行摘要

## .env 參數說明

```
IB_HOST        IB Gateway IP（預設 127.0.0.1）
IB_PORT        IB Gateway Port（Paper=4002 / Live=4001）
IB_CLIENT_ID   客戶端 ID，避免與 TWS 衝突
EMAIL_FROM     Gmail 寄件帳號
SMTP_PASS      Gmail App Password（16碼，可含空格）
EMAIL_TO       收件人 Email
```