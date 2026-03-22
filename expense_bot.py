import os
import json
import httpx
from datetime import date
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

gemini = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

NOTION_TOKEN = os.environ["NOTION_TOKEN"]
NOTION_DATABASE_ID = os.environ["NOTION_DATABASE_ID"]
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

CATEGORIES = [
    "收入_遠距薪資",
    "收入_人資實習",
    "必須_餐費與日用",
    "必須_交通",
    "真實_軟體與AI訂閱",
    "真實_進修與IPAS",
    "真實_專案實作材料",
    "真實_醫療保健",
    "快樂_飲料與咖啡",
    "快樂_娛樂與購物",
]

ACCOUNTS = [
    "華南銀行",
    "國泰信用卡",
    "國泰證券",
    "國泰存款",
    "國泰儲蓄",
    "外幣",
    "現金",
]

SYSTEM_INSTRUCTION = f"""你是一個記帳助理。使用者會輸入消費紀錄文字，
你需要解析出以下欄位並以 JSON 格式回傳：

- item: 品項名稱（字串）
- amount: 金額（正整數）
- category: 分類，只能從以下選項選一個：{json.dumps(CATEGORIES, ensure_ascii=False)}
- account: 帳戶，只能從以下選項選一個：{json.dumps(ACCOUNTS, ensure_ascii=False)}
  若文字中無法判斷帳戶，預設填「現金」

只回傳 JSON，不要有其他說明。"""


def parse_expense(text: str) -> dict:
    """用 Gemini AI 解析消費文字，回傳品項、金額、分類、帳戶。"""
    response = gemini.models.generate_content(
        model="gemini-2.5-flash",
        contents=text,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            response_schema={
                "type": "object",
                "properties": {
                    "item":     {"type": "string"},
                    "amount":   {"type": "number"},
                    "category": {"type": "string", "enum": CATEGORIES},
                    "account":  {"type": "string", "enum": ACCOUNTS},
                },
                "required": ["item", "amount", "category", "account"]
            }
        )
    )
    return json.loads(response.text)


def add_to_notion(item: str, amount: float, category: str, account: str) -> str:
    """新增一筆記錄到 Notion 資料庫，回傳頁面 URL。"""
    # 收入類別金額存成負數
    final_amount = -abs(amount) if category.startswith("收入") else abs(amount)

    payload = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": {
            "名稱": {
                "title": [{"text": {"content": item}}]
            },
            "金額": {
                "number": final_amount
            },
            "日期": {
                "date": {"start": date.today().isoformat()}
            },
            "分類": {
                "select": {"name": category}
            },
            "帳戶": {
                "select": {"name": account}
            },
        }
    }

    r = httpx.post("https://api.notion.com/v1/pages", headers=NOTION_HEADERS, json=payload)
    r.raise_for_status()
    return r.json()["url"]


def record(text: str):
    """解析文字並記錄到 Notion。"""
    print(f"輸入：{text}")
    data = parse_expense(text)

    item     = data["item"]
    amount   = data["amount"]
    category = data["category"]
    account  = data["account"]

    url = add_to_notion(item, amount, category, account)

    sign = "-" if category.startswith("收入") else ""
    print(f"品項：{item}")
    print(f"金額：{sign}${abs(amount)}")
    print(f"分類：{category}")
    print(f"帳戶：{account}")
    print(f"Notion：{url}")
    print("-" * 40)


def main():
    test_cases = [
        "金園排骨 260",
        "711 買咖啡跟麵包 85 國泰信用卡",
        "計程車費用350元",
        "遠距薪資入帳 28000 華南銀行",
    ]

    print("=== 記帳 Bot ===\n")
    for text in test_cases:
        record(text)


if __name__ == "__main__":
    main()
