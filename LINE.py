from pathlib import Path
import os
import requests
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / '.env'

load_dotenv(ENV_PATH)

LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
LINE_USER_ID = os.getenv('LINE_USER_ID')

if not LINE_CHANNEL_ACCESS_TOKEN:
    raise ValueError('缺少 LINE_CHANNEL_ACCESS_TOKEN，請在 .env 設定')

if not LINE_USER_ID:
    raise ValueError('缺少 LINE_USER_ID，請在 .env 設定')


def send_line_message(text: str):
    url = 'https://api.line.me/v2/bot/message/push'
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {LINE_CHANNEL_ACCESS_TOKEN}'
    }
    payload = {
        'to': LINE_USER_ID,
        'messages': [
            {
                'type': 'text',
                'text': text
            }
        ]
    }

    response = requests.post(url, headers=headers, json=payload, timeout=15)
    response.raise_for_status()

    if response.text.strip():
        return response.json()
    return {'status_code': response.status_code}


if __name__ == '__main__':
    result = send_line_message('測試通知：LINE .env 串接成功')
    print(result)