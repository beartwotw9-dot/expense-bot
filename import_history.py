import os
import time
import httpx
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

NOTION_TOKEN = os.environ["NOTION_TOKEN"]
NOTION_DATABASE_ID = os.environ["NOTION_DATABASE_ID"]
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

RAW_DATA = """Mar 2, 2026|45|快樂_飲料與咖啡|路易莎|國泰信用卡
Mar 2, 2026|209|必須_餐費與日用|午餐|國泰信用卡
Mar 2, 2026|209|快樂_娛樂與購物|無印良品|國泰信用卡
Mar 2, 2026|282|必須_餐費與日用|晚餐|國泰信用卡
Mar 3, 2026|194|必須_餐費與日用|午餐|國泰信用卡
Mar 3, 2026|80|必須_餐費與日用|晚餐|國泰信用卡
Mar 3, 2026|30|必須_餐費與日用|香蕉|國泰信用卡
Mar 3, 2026|35|必須_餐費與日用|晚餐|國泰信用卡
Mar 4, 2026|45|快樂_飲料與咖啡|路易莎|國泰信用卡
Mar 4, 2026|90|快樂_飲料與咖啡|三總|國泰信用卡
Mar 5, 2026|125|必須_餐費與日用|早餐|國泰信用卡
Mar 5, 2026|152|必須_餐費與日用|三總|國泰信用卡
Mar 5, 2026|190|必須_餐費與日用|晚餐|國泰信用卡
Mar 5, 2026|1200|必須_交通|定期票|國泰存款
Mar 5, 2026|550|必須_交通|彰化|國泰信用卡
Mar 5, 2026|60|快樂_飲料與咖啡|樂法|國泰信用卡
Mar 5, 2026|200|必須_餐費與日用|晚餐|國泰存款
Mar 6, 2026|371|必須_交通|彰化|國泰信用卡
Mar 6, 2026|500|必須_交通|台中回程|國泰信用卡
Mar 6, 2026|210|快樂_娛樂與購物|彰化禮品|國泰信用卡
Mar 6, 2026|142|必須_餐費與日用|晚餐|國泰信用卡
Mar 6, 2026|180|必須_餐費與日用|早餐|國泰信用卡
Mar 6, 2026|120|必須_交通|彰化台中交通|國泰存款
Mar 6, 2026|1680|快樂_娛樂與購物|項鍊|國泰信用卡
Mar 8, 2026|144|必須_餐費與日用|筆|國泰信用卡
Mar 8, 2026|50|必須_餐費與日用|置物櫃|國泰信用卡
Mar 8, 2026|-3125|收入_遠距薪資|備註|國泰存款
Mar 8, 2026|130|必須_餐費與日用|備註午餐|國泰信用卡
Mar 8, 2026|60|快樂_飲料與咖啡|飲料|國泰信用卡
Mar 8, 2026|200|必須_餐費與日用|午餐|國泰信用卡
Mar 10, 2026|70|快樂_飲料與咖啡|路易莎|國泰信用卡
Mar 10, 2026|45|快樂_飲料與咖啡|路易莎|國泰信用卡
Mar 10, 2026|500|真實_進修與IPAS|Pro360|國泰信用卡
Mar 10, 2026|179|必須_餐費與日用|午餐|國泰信用卡
Mar 10, 2026|50|快樂_飲料與咖啡|50嵐|國泰信用卡
Mar 10, 2026|190|必須_餐費與日用|晚餐|國泰存款
Mar 10, 2026|30|必須_餐費與日用|晚餐|國泰存款
Mar 10, 2026|35|快樂_飲料與咖啡|咖啡|國泰信用卡
Mar 10, 2026|-14790|收入_人資實習|備註|華南銀行
Mar 10, 2026|195|必須_餐費與日用|午餐|國泰信用卡
Mar 10, 2026|99|必須_餐費與日用|摩斯|國泰信用卡
Mar 11, 2026|202|必須_餐費與日用|晚餐|國泰信用卡
Mar 11, 2026|45|快樂_飲料與咖啡|路易莎|國泰信用卡
Mar 11, 2026|35|快樂_飲料與咖啡|咖啡|國泰信用卡
Mar 11, 2026|154|必須_餐費與日用|午餐|國泰信用卡
Mar 11, 2026|227|必須_餐費與日用|晚餐|國泰信用卡
Mar 11, 2026|200|真實_進修與IPAS|菁英|國泰信用卡
Mar 11, 2026|620|真實_醫療保健|三總|國泰信用卡
Mar 13, 2026|45|快樂_飲料與咖啡|飲料|國泰信用卡
Mar 13, 2026|110|必須_餐費與日用|午餐|國泰存款
Mar 13, 2026|299|快樂_娛樂與購物|華山 玩偶|國泰信用卡
Mar 13, 2026|158|必須_餐費與日用|晚餐|國泰存款
Mar 14, 2026|160|必須_餐費與日用|午餐|國泰信用卡
Mar 14, 2026|295|必須_餐費與日用|晚餐 生日快樂|國泰信用卡
Mar 14, 2026|296|真實_進修與IPAS|英文|國泰存款
Mar 16, 2026|35|快樂_飲料與咖啡|咖啡|國泰信用卡
Mar 16, 2026|105|必須_餐費與日用|午餐|國泰信用卡
Mar 16, 2026|141|必須_餐費與日用|晚餐|國泰信用卡
Mar 16, 2026|71|必須_餐費與日用|甜甜圈|國泰信用卡
Mar 18, 2026|99|必須_餐費與日用|午餐|國泰信用卡
Mar 18, 2026|320|必須_餐費與日用|晚餐|國泰存款
Mar 18, 2026|45|快樂_飲料與咖啡|路易莎|國泰信用卡
Mar 18, 2026|35|快樂_飲料與咖啡|咖啡|國泰信用卡
Mar 18, 2026|150|必須_餐費與日用|晚餐|國泰信用卡
Mar 20, 2026|35|快樂_飲料與咖啡|咖啡|國泰信用卡
Mar 20, 2026|159|必須_餐費與日用|午餐|國泰信用卡
Mar 20, 2026|145|必須_餐費與日用|晚餐|國泰信用卡"""


def setup_database():
    """新增「月份」select 欄位和「淨結餘」formula 欄位。"""
    payload = {
        "properties": {
            "月份": {"select": {}},
            "淨結餘": {"formula": {"expression": 'prop("金額")'}},
        }
    }
    r = httpx.patch(
        f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}",
        headers=NOTION_HEADERS,
        json=payload,
        timeout=30,
    )
    r.raise_for_status()
    print("欄位建立完成：月份（select）、淨結餘（formula）\n")


def parse_rows(raw: str) -> list[dict]:
    """解析原始資料，回傳 list of dict。"""
    rows = []
    for line in raw.strip().splitlines():
        date_str, amount_str, category, name, account = line.split("|")
        dt = datetime.strptime(date_str.strip(), "%b %d, %Y")
        rows.append({
            "date":     dt.strftime("%Y-%m-%d"),
            "month":    dt.strftime("%Y-%m"),
            "amount":   int(amount_str),
            "category": category,
            "name":     name,
            "account":  account,
        })
    return rows


def create_page(row: dict) -> str:
    """新增一筆記錄到 Notion，回傳頁面 URL。"""
    payload = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": {
            "名稱":  {"title": [{"text": {"content": row["name"]}}]},
            "金額":  {"number": row["amount"]},
            "日期":  {"date": {"start": row["date"]}},
            "分類":  {"select": {"name": row["category"]}},
            "帳戶":  {"select": {"name": row["account"]}},
            "月份":  {"select": {"name": row["month"]}},
        }
    }
    r = httpx.post(
        "https://api.notion.com/v1/pages",
        headers=NOTION_HEADERS,
        json=payload,
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["url"]


def main():
    print("=== Notion 歷史資料匯入 ===\n")

    # 1. 建立新欄位
    setup_database()

    # 2. 解析資料
    rows = parse_rows(RAW_DATA)
    total = len(rows)
    print(f"共 {total} 筆資料，開始匯入...\n")

    # 3. 逐筆寫入（每筆間隔 0.35 秒，避免觸發 rate limit）
    success = 0
    for i, row in enumerate(rows, 1):
        try:
            url = create_page(row)
            print(f"[{i:>3}/{total}] {row['date']}  {row['name']:<16} {row['amount']:>7}  {row['category']}")
            success += 1
        except Exception as e:
            print(f"[{i:>3}/{total}] 失敗：{row['name']} → {e}")
        time.sleep(0.35)

    print(f"\n完成！成功 {success}/{total} 筆")


if __name__ == "__main__":
    main()
