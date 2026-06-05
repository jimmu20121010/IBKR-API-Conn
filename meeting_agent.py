# self.llm = ChatGoogleGenAI(model="gemini-2.0-flash")
# self.llm = ChatGroq(model="llama-3.3-70b-versatile")
# self.llm = ChatDeepSeek(model="deepseek-reasoner")
import os
from langchain_core.prompts import ChatPromptTemplate
# 💡 改用 Groq 的套件
from langchain_groq import ChatGroq 

class MeetingMinutesAgent:
    def __init__(self):
        # 💡 初始化 Groq，並使用 Meta 目前最強的開源模型 Llama 3.3
        self.llm = ChatGroq(
            model="llama-3.3-70b-versatile", 
            temperature=0.2,                  # 保持低隨機性
            api_key="gsk_AIPKEY" # 👈 直接貼上你的 Groq 免費 Key！
        )
        
    def generate_minutes(self, transcript: str) -> str:
        prompt_template = ChatPromptTemplate.from_messages([
            ("system", """
            你是一個專業的進階會議紀錄祕書 Agent。
            請仔細閱讀以下會議的原始逐字稿，並將其整理成專業、結構清晰的繁體中文會議紀錄。
            
            輸出格式必須嚴格包含以下結構：
            ## 🎯 會議基本信息
            - **會議名稱**: (若逐字稿未提及則根據內容推導)
            - **會議日期**: (若未知請寫未知)
            
            ## 📝 會議核心摘要
            (請用條列式概述會議討論的核心重點、各方觀點，避免流水帳)
            
            ## 💡 關鍵決策
            (紀錄會議中達成的所有共識與最終決策)
            
            ## 🚀 行動項目與負責人 (Action Items)
            - [ ] **任務內容** | 👤 **負責人** | 📅 **截止日期** (若未提及請寫未定)
            """),
            ("user", "這是會議的原始逐字稿：\n\n{transcript}")
        ])
        
        chain = prompt_template | self.llm
        response = chain.invoke({"transcript": transcript})
        return response.content

# 測試執行
if __name__ == "__main__":
    mock_transcript = """
    張經理：大家早，今天主要討論我們新產品的上市時程。行銷部的 Sandy，英文版廣告預算出來了嗎？
    Sandy：經理早，預算已經估好了，大約是 50 萬台幣，我預計下週三前會把詳細的媒體投放企劃書提報上來。
    張經理：很好。另外技術部的阿豪，系統串接進度怎麼樣？
    阿豪：我們正在串接 IBKR API，目前下單測試成功了，但期權報價還有點小 Bug。我預計這週五下班前會把 Bug 修好並部署上線。
    張經理：沒問題，那今天會議就到這邊。
    """
    
    agent = MeetingMinutesAgent()
    print("Agent 正在處理會議紀錄...")
    result = agent.generate_minutes(mock_transcript)
    
    print("\n" + "="*40 + "\n Agent 生成結果 \n" + "="*40)
    print(result)
