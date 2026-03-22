"""
Gmail OAuth 一次性授權設定腳本。
執行前請先完成 Google Cloud Console 設定（詳見 README），
然後執行：python setup_gmail.py
"""
import os
from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow

load_dotenv()

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
]
CREDENTIALS_PATH = os.environ.get("GMAIL_CREDENTIALS_PATH", "credentials.json")
TOKEN_PATH       = os.environ.get("GMAIL_TOKEN_PATH",       "token.json")

if not os.path.exists(CREDENTIALS_PATH):
    print(f"❌ 找不到 credentials 檔案：{CREDENTIALS_PATH}")
    print("請先從 Google Cloud Console 下載 OAuth 2.0 用戶端憑證，儲存為 credentials.json")
    exit(1)

print("🔑 即將開啟瀏覽器進行 Google 帳號授權...")
flow  = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
creds = flow.run_local_server(port=0)

with open(TOKEN_PATH, "w") as f:
    f.write(creds.to_json())

print(f"✅ Gmail + Google Calendar 授權完成！token 已儲存至 {TOKEN_PATH}")
