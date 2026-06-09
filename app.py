import streamlit as st
import yfinance as yf

st.title("📈 現價查詢器")
symbol = st.text_input("輸入代碼（如 SOFI）：", "SOFI")

if st.button("開始查詢"):
    ticker = yf.Ticker(symbol)
    price = ticker.fast_info.get('lastPrice', 0)
    st.success(f"{symbol} 目前股價為: ${price:.2f}")