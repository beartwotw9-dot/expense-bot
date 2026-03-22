import os
import re
import json
import base64
import asyncio
import httpx
import pytz
import discord
from collections import deque
from datetime import date, datetime, timedelta
from dotenv import load_dotenv, set_key
from google import genai
from google.genai import types
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

import market as mkt
import gmail_helper

load_dotenv()

# ── Gmail Token 還原（Railway 雲端用）────────────────────────────────────────
import base64 as _b64
def _restore_credentials():
    for env_var, filename in [("GMAIL_TOKEN_B64","token.json"),("GMAIL_CREDENTIALS_B64","credentials.json")]:
        val = os.environ.get(env_var,"")
        if val:
            with open(filename,"w") as f:
                f.write(_b64.b64decode(val).decode())
_restore_credentials()
# ─────────────────────────────────────────────────────────────────────────────

TAIPEI   = pytz.timezone("Asia/Taipei")
WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"]
ENV_PATH          = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
MARKET_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "market_cache.json")
EMAIL_CACHE_FILE  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "email_cache.json")

# ── Clients ────────────────────────────────────────────────────────────────────
gemini = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

NOTION_TOKEN       = os.environ["NOTION_TOKEN"]
NOTION_DATABASE_ID = os.environ["NOTION_DATABASE_ID"]
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

_ch = os.environ.get("DISCORD_CHANNEL_ID", "0")
DISCORD_CHANNEL_ID   = int(_ch) if _ch.isdigit() else 0
_dch = os.environ.get("DISCORD_DIARY_CHANNEL_ID", "0")
DIARY_CHANNEL_ID     = int(_dch) if _dch.isdigit() else 0
GMAIL_CREDENTIALS    = os.environ.get("GMAIL_CREDENTIALS_PATH", "credentials.json")
GMAIL_TOKEN          = os.environ.get("GMAIL_TOKEN_PATH",       "token.json")
NOTION_MORNING_DB_ID = os.environ.get("NOTION_MORNING_DB", "")
NOTION_DIARY_DB_ID   = os.environ.get("NOTION_DIARY_DB",   "")

# ── Constants ──────────────────────────────────────────────────────────────────
CATEGORIES = [
    "收入_遠距薪資", "收入_人資實習",
    "必須_餐費與日用", "必須_交通",
    "真實_軟體與AI訂閱", "真實_進修與IPAS", "真實_專案實作材料", "真實_醫療保健",
    "快樂_飲料與咖啡", "快樂_娛樂與購物",
]

ACCOUNTS = [
    "華南銀行", "國泰信用卡", "國泰證券", "國泰存款", "國泰儲蓄", "外幣", "現金",
]

CATEGORY_KEYWORDS = {
    "餐費": ("必須_餐費與日用", "🍱"),
    "日用": ("必須_餐費與日用", "🍱"),
    "交通": ("必須_交通",        "🚗"),
    "飲料": ("快樂_飲料與咖啡",  "☕"),
    "咖啡": ("快樂_飲料與咖啡",  "☕"),
    "娛樂": ("快樂_娛樂與購物",  "🛍️"),
    "購物": ("快樂_娛樂與購物",  "🛍️"),
    "軟體": ("真實_軟體與AI訂閱","💻"),
    "AI":   ("真實_軟體與AI訂閱","💻"),
    "進修": ("真實_進修與IPAS",  "📚"),
    "IPAS": ("真實_進修與IPAS",  "📚"),
    "專案": ("真實_專案實作材料","🔧"),
    "醫療": ("真實_醫療保健",    "🏥"),
    "保健": ("真實_醫療保健",    "🏥"),
}

CATEGORY_EMOJI = {
    "必須_餐費與日用":  "🍱",
    "必須_交通":        "🚗",
    "快樂_飲料與咖啡":  "☕",
    "快樂_娛樂與購物":  "🛍️",
    "真實_軟體與AI訂閱":"💻",
    "真實_進修與IPAS":  "📚",
    "真實_專案實作材料":"🔧",
    "真實_醫療保健":    "🏥",
    "收入_遠距薪資":    "💼",
    "收入_人資實習":    "🏢",
}

# 每個頻道保留最近 5 輪對話 (user + model 各算一則)
channel_history: dict[int, deque] = {}
MAX_HISTORY = 10  # 5 輪 × 2

# ── Rule-based Parsing ─────────────────────────────────────────────────────────
_RECORD_RE   = re.compile(r'^(.+?)\s+(\d+(?:\.\d+)?)\s*(.*)$')
_NO_PARSE_KW = {"多少", "查詢", "明細", "結餘", "本月", "行情", "大盤", "漲", "跌", "薪水", "收入"}

_MARKET_KW  = {"行情", "大盤", "股市", "ETF", "漲跌", "VOO", "VT", "QQQ",
               "黃金", "GLD", "台指", "0050", "006208", "現在行情", "市場"}
_BALANCE_KW = {"結餘", "剩多少", "還剩", "收支狀況", "餘額", "淨收入"}
_DETAIL_KW  = {"明細", "清單", "都買什麼", "買了什麼", "消費記錄", "花了什麼"}
_INCOME_KW  = {"收入多少", "入帳多少", "薪資多少", "本月收入", "這個月收入"}
_EXPENSE_KW = {"花多少", "花了多少", "支出", "消費多少", "本月支出", "這個月花"}


def try_parse_expense_regex(text: str) -> dict | None:
    """格式清楚（品項 金額 [帳戶]）時直接解析，不呼叫 AI。"""
    if len(text) > 25 or any(kw in text for kw in _NO_PARSE_KW):
        return None
    m = _RECORD_RE.match(text.strip())
    if not m:
        return None
    item, amt_str, rest = m.group(1).strip(), m.group(2), m.group(3).strip()
    try:
        amount = float(amt_str)
    except ValueError:
        return None
    if amount <= 0 or amount > 500_000 or not item:
        return None
    account  = next((a for a in ACCOUNTS if a in rest), "現金")
    category = next((cat for kw, (cat, _) in CATEGORY_KEYWORDS.items() if kw in item),
                    "必須_餐費與日用")
    # 偵測收入
    if any(kw in item for kw in ("薪資", "薪水", "工資", "人資", "遠距", "實習")):
        category = "收入_遠距薪資"
    return {"item": item, "amount": amount, "category": category, "account": account}


def quick_classify(text: str) -> dict | None:
    """關鍵字快速分類；無法判斷回傳 None（交給 Gemini）。"""
    if any(kw in text for kw in _MARKET_KW):
        return {"intent": "market", "category": ""}
    if any(kw in text for kw in _BALANCE_KW):
        return {"intent": "balance", "category": ""}
    if any(kw in text for kw in _DETAIL_KW):
        return {"intent": "detail", "category": ""}
    if any(kw in text for kw in _INCOME_KW):
        return {"intent": "income", "category": ""}
    if any(kw in text for kw in _EXPENSE_KW):
        cat = next((c for kw, (c, _) in CATEGORY_KEYWORDS.items() if kw in text), "")
        return {"intent": "expense", "category": cat}
    return None

SYSTEM_INSTRUCTION = (
    f"解析消費紀錄，只回 JSON：\n"
    f"item(品項)、amount(正整數)、"
    f"category({'/'.join(CATEGORIES)})、"
    f"account({'/'.join(ACCOUNTS)}，預設「現金」)"
)

# ── Embed Colors ───────────────────────────────────────────────────────────────
GREEN  = discord.Color.green()
BLUE   = discord.Color.blue()
RED    = discord.Color.red()
GOLD   = discord.Color.gold()
PURPLE = discord.Color.purple()

# ── Notion Write ───────────────────────────────────────────────────────────────
def parse_expense(text: str) -> dict:
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
    final_amount = -abs(amount) if category.startswith("收入") else abs(amount)
    today = date.today()
    payload = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": {
            "名稱":  {"title": [{"text": {"content": item}}]},
            "金額":  {"number": final_amount},
            "日期":  {"date": {"start": today.isoformat()}},
            "分類":  {"select": {"name": category}},
            "帳戶":  {"select": {"name": account}},
            "月份":  {"select": {"name": today.strftime("%Y-%m")}},
        }
    }
    r = httpx.post("https://api.notion.com/v1/pages", headers=NOTION_HEADERS, json=payload, timeout=30)
    r.raise_for_status()
    return r.json()["url"]


# ── Notion Query ───────────────────────────────────────────────────────────────
def query_by_filter(notion_filter: dict) -> list[dict]:
    pages, cursor = [], None
    while True:
        body = {"filter": notion_filter, "page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        r = httpx.post(
            f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query",
            headers=NOTION_HEADERS, json=body, timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        pages.extend(data["results"])
        if not data.get("has_more"):
            break
        cursor = data["next_cursor"]
    return pages


def query_this_month(extra_filter: dict | None = None) -> list[dict]:
    month        = date.today().strftime("%Y-%m")
    month_filter = {"property": "月份", "select": {"equals": month}}
    f = {"and": [month_filter, extra_filter]} if extra_filter else month_filter
    return query_by_filter(f)


def query_date(target_date: str) -> list[dict]:
    return query_by_filter({"property": "日期", "date": {"equals": target_date}})


def extract_amount(page: dict) -> int:
    return page["properties"]["金額"]["number"] or 0

def extract_name(page: dict) -> str:
    titles = page["properties"]["名稱"]["title"]
    return titles[0]["plain_text"] if titles else "（無名稱）"

def extract_category(page: dict) -> str:
    sel = page["properties"]["分類"]["select"]
    return sel["name"] if sel else ""

def extract_date(page: dict) -> str:
    d = page["properties"]["日期"]["date"]
    return d["start"] if d else ""


# ── Embed Builders ─────────────────────────────────────────────────────────────
def embed_record(item: str, amount: float, category: str, account: str) -> discord.Embed:
    sign     = "-" if category.startswith("收入") else ""
    disp_amt = f"{sign}${abs(int(amount)):,}"
    emoji    = CATEGORY_EMOJI.get(category, "📌")
    e = discord.Embed(title="✅ 記帳成功", color=GREEN)
    e.add_field(name="品項", value=item,                      inline=True)
    e.add_field(name="金額", value=disp_amt,                  inline=True)
    e.add_field(name="分類", value=f"{emoji} {category}",     inline=True)
    e.add_field(name="帳戶", value=account,                   inline=True)
    e.set_footer(text=date.today().isoformat())
    return e


def embed_error(msg: str) -> discord.Embed:
    return discord.Embed(title="❌ 發生錯誤", description=str(msg), color=RED)


def embed_monthly_expense(pages: list[dict]) -> discord.Embed:
    expense_pages = [p for p in pages if not extract_category(p).startswith("收入")]
    total = sum(abs(extract_amount(p)) for p in expense_pages)
    by_cat: dict[str, int] = {}
    for p in expense_pages:
        cat = extract_category(p)
        by_cat[cat] = by_cat.get(cat, 0) + abs(extract_amount(p))
    lines = [
        f"{CATEGORY_EMOJI.get(cat, '📌')} {cat}：**${amt:,}**"
        for cat, amt in sorted(by_cat.items(), key=lambda x: -x[1])
    ]
    e = discord.Embed(title=f"💰 本月支出：${total:,}", color=BLUE)
    e.description = "\n".join(lines) if lines else "（本月尚無支出）"
    e.set_footer(text=f"{date.today().strftime('%Y-%m')}｜共 {len(expense_pages)} 筆")
    return e


def embed_monthly_income(pages: list[dict]) -> discord.Embed:
    income_pages = [p for p in pages if extract_category(p).startswith("收入")]
    total = sum(abs(extract_amount(p)) for p in income_pages)
    by_cat: dict[str, int] = {}
    for p in income_pages:
        cat = extract_category(p)
        by_cat[cat] = by_cat.get(cat, 0) + abs(extract_amount(p))
    lines = [
        f"{CATEGORY_EMOJI.get(cat, '💵')} {cat}：**${amt:,}**"
        for cat, amt in sorted(by_cat.items(), key=lambda x: -x[1])
    ]
    e = discord.Embed(title=f"💵 本月收入：${total:,}", color=BLUE)
    e.description = "\n".join(lines) if lines else "（本月尚無收入）"
    e.set_footer(text=f"共 {len(income_pages)} 筆")
    return e


def embed_monthly_balance(pages: list[dict]) -> discord.Embed:
    income  = sum(abs(extract_amount(p)) for p in pages if extract_category(p).startswith("收入"))
    expense = sum(abs(extract_amount(p)) for p in pages if not extract_category(p).startswith("收入"))
    balance = income - expense
    sign    = "+" if balance >= 0 else ""
    icon    = "✅" if balance >= 0 else "⚠️"
    e = discord.Embed(title="📊 本月結餘", color=BLUE)
    e.add_field(name="💵 收入", value=f"${income:,}",                      inline=True)
    e.add_field(name="💸 支出", value=f"${expense:,}",                     inline=True)
    e.add_field(name=f"{icon} 結餘", value=f"**{sign}${abs(balance):,}**", inline=False)
    return e


def embed_category_total(pages: list[dict], cat_name: str, emoji: str) -> discord.Embed:
    matched = [p for p in pages if extract_category(p) == cat_name]
    total   = sum(abs(extract_amount(p)) for p in matched)
    lines = [
        f"`{extract_date(p)[5:]}` {extract_name(p)}　${abs(extract_amount(p)):,}"
        for p in sorted(matched, key=extract_date)
    ]
    detail = "\n".join(lines) if lines else "（本月無記錄）"
    if len(detail) > 1024:
        detail = detail[:1020] + "\n..."
    e = discord.Embed(title=f"{emoji} {cat_name}：${total:,}", color=BLUE)
    e.add_field(name="明細", value=detail, inline=False)
    e.set_footer(text=f"{date.today().strftime('%Y-%m')}｜共 {len(matched)} 筆")
    return e


def embed_monthly_detail(pages: list[dict]) -> discord.Embed:
    sorted_ = sorted(pages, key=extract_date)
    lines = [
        f"`{extract_date(p)[5:]}` {CATEGORY_EMOJI.get(extract_category(p), '📌')} "
        f"{extract_name(p)}　{'−' if extract_amount(p) < 0 else ''}${abs(extract_amount(p)):,}"
        for p in sorted_
    ]
    content = "\n".join(lines) if lines else "（本月尚無記錄）"
    if len(content) > 4096:
        content = content[:4090] + "\n..."
    e = discord.Embed(title=f"📋 {date.today().strftime('%Y-%m')} 本月明細", color=BLUE)
    e.description = content
    e.set_footer(text=f"共 {len(pages)} 筆")
    return e


def embed_market(data: list[dict], comment: str) -> discord.Embed:
    e = discord.Embed(title="📈 市場行情", color=BLUE)
    e.description = "\n".join(mkt.format_line(d) for d in data)
    e.add_field(name="💬 AI 觀察", value=comment, inline=False)
    e.set_footer(text=datetime.now(TAIPEI).strftime("%Y-%m-%d %H:%M Taipei"))
    return e


# ── Rate Limit & Market Cache ──────────────────────────────────────────────────
def _is_rate_limit(e: Exception) -> bool:
    s = str(e).lower()
    return "429" in s or "resource_exhausted" in s or "quota" in s


def get_market_cache() -> tuple[list[dict], str] | None:
    """若今天已有快取，回傳 (data, comment)；否則回傳 None。"""
    try:
        if not os.path.exists(MARKET_CACHE_FILE):
            return None
        with open(MARKET_CACHE_FILE, encoding="utf-8") as f:
            cache = json.load(f)
        if cache.get("date") == date.today().isoformat():
            return cache["data"], cache["comment"]
    except Exception:
        pass
    return None


def save_market_cache(data: list[dict], comment: str):
    try:
        with open(MARKET_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({"date": date.today().isoformat(), "data": data, "comment": comment},
                      f, ensure_ascii=False)
    except Exception:
        pass


def fetch_market_with_cache() -> tuple[list[dict], str]:
    """優先讀快取；無快取才呼叫 yfinance，摘要純規則不用 AI。"""
    cached = get_market_cache()
    if cached:
        return cached
    data    = mkt.fetch_market_data()
    comment = mkt.rule_comment(data)   # 純規則，零 AI token
    save_market_cache(data, comment)
    return data, comment


def get_email_cache() -> str | None:
    """同一天已快取 email 摘要時回傳；否則 None。"""
    try:
        if not os.path.exists(EMAIL_CACHE_FILE):
            return None
        with open(EMAIL_CACHE_FILE, encoding="utf-8") as f:
            cache = json.load(f)
        if cache.get("date") == date.today().isoformat():
            return cache["text"]
    except Exception:
        pass
    return None


def save_email_cache(text: str):
    try:
        with open(EMAIL_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({"date": date.today().isoformat(), "text": text}, f, ensure_ascii=False)
    except Exception:
        pass


def fetch_emails_with_cache() -> str:
    """優先讀快取，同一天不重複呼叫 Gmail API。"""
    cached = get_email_cache()
    if cached is not None:
        return cached
    try:
        emails = gmail_helper.get_important_unread(GMAIL_CREDENTIALS, GMAIL_TOKEN, max_results=3)
        if emails:
            text = "\n".join(
                f"📨 **{em['subject'][:50]}**\n　　_{em['from']}_"
                for em in emails
            )
        else:
            text = "今天沒有重要郵件 ✨"
    except Exception:
        text = "Gmail 未設定，請執行 python setup_gmail.py"
    save_email_cache(text)
    return text


# ── Notion Morning DB ──────────────────────────────────────────────────────────
async def ensure_morning_db():
    """若 NOTION_MORNING_DB_ID 未設，自動建立晨報資料庫。"""
    global NOTION_MORNING_DB_ID
    if NOTION_MORNING_DB_ID:
        return
    try:
        r = httpx.get(
            f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}",
            headers=NOTION_HEADERS, timeout=15
        )
        parent_block = r.json()["parent"]["block_id"]
        payload = {
            "parent": {"type": "page_id", "page_id": parent_block},
            "title":  [{"text": {"content": "晨報記錄"}}],
            "properties": {
                "標題":     {"title": {}},
                "日期":     {"date": {}},
                "市場摘要": {"rich_text": {}},
                "信箱摘要": {"rich_text": {}},
                "昨日花費": {"number": {"format": "number"}},
            }
        }
        r2 = httpx.post(
            "https://api.notion.com/v1/databases",
            headers=NOTION_HEADERS, json=payload, timeout=30
        )
        r2.raise_for_status()
        NOTION_MORNING_DB_ID = r2.json()["id"].replace("-", "")
        set_key(ENV_PATH, "NOTION_MORNING_DB", NOTION_MORNING_DB_ID)
        print(f"✅ 晨報資料庫已建立並儲存至 .env：{NOTION_MORNING_DB_ID}")
    except Exception as e:
        print(f"⚠️ 晨報資料庫建立失敗（不影響其他功能）：{e}")


def save_morning_to_notion(today_str: str, market_text: str, email_text: str, expense: int):
    if not NOTION_MORNING_DB_ID:
        return
    try:
        payload = {
            "parent": {"database_id": NOTION_MORNING_DB_ID},
            "properties": {
                "標題":     {"title": [{"text": {"content": f"{today_str} 晨報"}}]},
                "日期":     {"date": {"start": today_str}},
                "市場摘要": {"rich_text": [{"text": {"content": market_text[:2000]}}]},
                "信箱摘要": {"rich_text": [{"text": {"content": email_text[:2000]}}]},
                "昨日花費": {"number": expense},
            }
        }
        r = httpx.post(
            "https://api.notion.com/v1/pages",
            headers=NOTION_HEADERS, json=payload, timeout=30
        )
        r.raise_for_status()
    except Exception as e:
        print(f"⚠️ 晨報寫入 Notion 失敗：{e}")


# ── Morning Report ─────────────────────────────────────────────────────────────
async def send_morning_report():
    channel = bot.get_channel(DISCORD_CHANNEL_ID)
    if not channel:
        print(f"⚠️ 找不到頻道 {DISCORD_CHANNEL_ID}，晨報未發送")
        return

    now       = datetime.now(TAIPEI)
    today_str = now.strftime("%Y-%m-%d")
    weekday   = WEEKDAYS[now.weekday()]
    yesterday = (now.date() - timedelta(days=1)).isoformat()

    # 1. Market data（有快取直接用）
    try:
        market_data, market_comment = fetch_market_with_cache()
        market_lines = [mkt.format_line(d) for d in market_data]
        market_text  = "\n".join(market_lines)
    except Exception as e:
        market_lines   = [f"市場資料取得失敗：{e}"]
        market_comment = "無法取得市場資料"
        market_text    = market_comment

    # 2. Gmail（快取，同天不重複呼叫）
    email_text = fetch_emails_with_cache()

    # 3. Yesterday's expense
    try:
        yest_pages   = query_date(yesterday)
        yest_expense = [p for p in yest_pages if not extract_category(p).startswith("收入")]
        yest_total   = sum(abs(extract_amount(p)) for p in yest_expense)
        if not yest_expense:
            expense_text = "昨天沒有記帳喔！請補記 🙈"
        else:
            biggest      = max(yest_expense, key=lambda p: abs(extract_amount(p)))
            expense_text = (
                f"總計 **${yest_total:,}**（共 {len(yest_expense)} 筆）\n"
                f"最大一筆：{extract_name(biggest)} **${abs(extract_amount(biggest)):,}**"
            )
    except Exception as e:
        yest_total   = 0
        expense_text = f"查詢失敗：{e}"

    # Build Embed
    e = discord.Embed(
        title=f"🌅 早安 Hua！",
        description=f"今天是 **{now.strftime('%Y年%m月%d日')}**（週{weekday}）",
        color=GOLD
    )
    e.add_field(name="📈 市場行情",   value="\n".join(market_lines)[:1024], inline=False)
    e.add_field(name="💬 AI 市場觀察", value=market_comment,                inline=False)
    e.add_field(name="📧 信箱摘要",   value=email_text[:1024],              inline=False)
    e.add_field(name="💰 昨日花費",   value=expense_text,                   inline=False)
    e.set_footer(text=f"{today_str} 由記帳 Bot 自動發送")

    await channel.send(embed=e)
    save_morning_to_notion(today_str, market_text, email_text, yest_total)
    print(f"✅ 晨報已發送：{today_str}")


# ── AI Intent ─────────────────────────────────────────────────────────────────
_INTENT_SYSTEM = (
    "判斷意圖，只回 JSON {\"intent\":\"\",\"category\":\"\"}。\n"
    "intent: record(記帳)/expense(查支出)/income(查收入)/balance(查結餘)"
    "/detail(看明細)/market(看行情)/chat(閒聊)/unclear\n"
    f"若 expense 且有指定分類，category 填其一：{'/'.join(CATEGORIES)}，否則空字串。"
)

_CHAT_SYSTEM = """你是 Hua 的記帳小幫手，個性輕鬆友善。
Hua 是在台灣的年輕人，說繁體中文。
回覆要自然、簡短，語氣貼近台灣年輕人，可以用少量表情符號。
不需要幫忙記帳或查詢，這則訊息就是普通閒聊，直接聊天就好。"""


def classify_intent(text: str, history: deque) -> dict:
    """用 Gemini 判斷用戶意圖，回傳 {"intent": ..., "category": ...}。"""
    # 把最近對話加到 prompt 裡當作上下文
    ctx_lines = [
        f"[{'用戶' if h['role'] == 'user' else 'Bot'}]: {h['content']}"
        for h in history
    ]
    ctx = ("對話上下文（最近 5 則）：\n" + "\n".join(ctx_lines) + "\n\n") if ctx_lines else ""
    prompt = f"{ctx}用戶說：{text}"

    resp = gemini.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=_INTENT_SYSTEM,
            response_mime_type="application/json",
            response_schema={
                "type": "object",
                "properties": {
                    "intent":   {"type": "string",
                                 "enum": ["record","expense","income","balance",
                                          "detail","market","chat","unclear"]},
                    "category": {"type": "string"},
                },
                "required": ["intent", "category"]
            }
        )
    )
    return json.loads(resp.text)


def chat_reply(text: str, history: deque) -> str:
    """用 Gemini 產生輕鬆的閒聊回覆，帶入對話 context。"""
    contents = [
        {"role": h["role"], "parts": [{"text": h["content"]}]}
        for h in history
    ]
    contents.append({"role": "user", "parts": [{"text": text}]})

    resp = gemini.models.generate_content(
        model="gemini-2.5-flash",
        contents=contents,
        config=types.GenerateContentConfig(system_instruction=_CHAT_SYSTEM)
    )
    return resp.text.strip()


def _push_history(cid: int, role: str, content: str):
    if cid not in channel_history:
        channel_history[cid] = deque(maxlen=MAX_HISTORY)
    channel_history[cid].append({"role": role, "content": content})


# ── Diary Constants ────────────────────────────────────────────────────────────
DIARY_MOODS = ["😊 開心", "😌 平靜", "😔 低落", "😰 焦慮", "😤 煩躁", "😴 疲憊", "🥰 溫暖", "😢 難過"]
DIARY_TAGS  = ["出遊", "看診", "工作", "休息", "社交", "購物", "飲食", "藥物", "運動", "居家"]

_DIARY_SYSTEM = """你是李醫師的數位助手，也是 Dora 最親密的諮商師。

【Dora 的背景】
- 診斷：F41.1 廣泛性焦慮症 + F32.2 重度憂鬱症
- 症狀：撕嘴皮（強迫行為）、嚴重嗜睡（藥物殘留）、體重焦慮、Brain Fog
- 病程自 2025/04/29 開始

【書寫原則】
- 回覆要有溫度、有細節，不要精簡，每個區塊都要完整展開
- 若 Dora 提到負面情緒或痛苦，先好好停留、接住她的感受，不要急著鼓勵或找解法
- 食物描述溫柔，不批判熱量或份量
- 圖片中的外出場景，強調快樂瞬間和美好細節
- 生理症狀（撕嘴皮、嗜睡）保持理解，不評判

根據 Dora 今天分享的內容，回傳以下 JSON（所有欄位必須完整填寫，資訊不足請溫柔推測）：
{
  "title": "一句話總結今天（15字以內）",
  "mood": "從以下選一：😊 開心/😌 平靜/😔 低落/😰 焦慮/😤 煩躁/😴 疲憊/🥰 溫暖/😢 難過",
  "tags": ["從以下選1-3個：出遊/看診/工作/休息/社交/購物/飲食/藥物/運動/居家"],
  "body_weather": "2-3個emoji加短句描述今日身心狀態，有細節",
  "doctor_notes": "醫師的巡房紀錄：完整的症狀解讀與藥物副作用觀察，3-4句，理解不說教",
  "image_notes": "若有圖片則細膩解讀，強調情感與美好瞬間；否則填空字串",
  "q1_food": "今天飲食，溫柔詳細描述，不批判",
  "q2_body": "生理狀況（撕嘴皮/排便/月經），若未提及寫「今天沒有特別記錄」",
  "q3_energy": "身體狀態與精力，有細節",
  "q4_mood": "心情指數 0-10 加完整描述，若有負面情緒多停留接住",
  "q5_events": "當天發生的事，有細節有溫度",
  "q6_meds": "用藥感受（若未提及寫「今天藥物狀況待記錄」）",
  "goodnight": "諮商師的晚安語：真誠有感，至少3-4句，無條件接納，若今天辛苦多給一點溫柔"
}"""

_DIARY_SCHEMA = {
    "type": "object",
    "properties": {
        "title":        {"type": "string"},
        "mood":         {"type": "string"},
        "tags":         {"type": "array", "items": {"type": "string"}},
        "body_weather": {"type": "string"},
        "doctor_notes": {"type": "string"},
        "image_notes":  {"type": "string"},
        "q1_food":      {"type": "string"},
        "q2_body":      {"type": "string"},
        "q3_energy":    {"type": "string"},
        "q4_mood":      {"type": "string"},
        "q5_events":    {"type": "string"},
        "q6_meds":      {"type": "string"},
        "goodnight":    {"type": "string"},
    },
    "required": ["title", "mood", "tags", "body_weather", "doctor_notes", "image_notes",
                 "q1_food", "q2_body", "q3_energy", "q4_mood", "q5_events", "q6_meds", "goodnight"],
}

_DIARY_DATE_RE = re.compile(
    r"(?:(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})|(\d{1,2})[/\-](\d{1,2})|昨天|前天)日記"
)


# ── Diary: Helpers ─────────────────────────────────────────────────────────────
async def get_taipei_weather() -> str:
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get("https://wttr.in/Taipei?format=j1")
        data  = r.json()
        cur   = data["current_condition"][0]
        desc  = cur["weatherDesc"][0]["value"]
        temp  = cur["temp_C"]
        feels = cur["FeelsLikeC"]
        humid = cur["humidity"]
        return f"{desc}，{temp}°C（體感 {feels}°C），濕度 {humid}%"
    except Exception:
        return "天氣資料取得失敗"


def get_today_expense_summary() -> tuple[int, str]:
    today         = date.today().isoformat()
    pages         = query_date(today)
    expense_pages = [p for p in pages if not extract_category(p).startswith("收入")]
    total         = sum(abs(extract_amount(p)) for p in expense_pages)
    if not expense_pages:
        return 0, "今天還沒有記帳"
    items = [f"{extract_name(p)} ${abs(extract_amount(p)):,}" for p in expense_pages]
    return total, "、".join(items)


def _parse_diary_date(m: re.Match, text: str) -> str:
    today = date.today()
    if "昨天" in text:
        return (today - timedelta(days=1)).isoformat()
    if "前天" in text:
        return (today - timedelta(days=2)).isoformat()
    if m.group(1):  # YYYY/MM/DD
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return f"{today.year}-{int(m.group(4)):02d}-{int(m.group(5)):02d}"  # MM/DD


# ── Diary: Generate ────────────────────────────────────────────────────────────
async def generate_diary_content(
    text: str,
    images: list[tuple[bytes, str]],
    weather: str,
    emails_text: str,
    calendar_text: str,
    expense_text: str,
) -> dict:
    now     = datetime.now(TAIPEI)
    weekday = WEEKDAYS[now.weekday()]
    context = (
        f"今天是 {now.strftime('%Y/%m/%d')} 週{weekday}。\n\n"
        f"📍 台北天氣：{weather}\n"
        f"📧 今日重要郵件：{emails_text}\n"
        f"📅 今日行程：{calendar_text}\n"
        f"💰 今日花費：{expense_text}\n\n"
        f"Dora 說：\n{text or '（今天沒有文字，只有圖片）'}"
    )
    parts = [types.Part.from_text(text=context)]
    for img_bytes, mime_type in images:
        parts.append(types.Part.from_bytes(data=img_bytes, mime_type=mime_type))

    resp = gemini.models.generate_content(
        model="gemini-2.5-flash",
        contents=parts,
        config=types.GenerateContentConfig(
            system_instruction=_DIARY_SYSTEM,
            response_mime_type="application/json",
            response_schema=_DIARY_SCHEMA,
        ),
    )
    return json.loads(resp.text)


# ── Diary: Embed ───────────────────────────────────────────────────────────────
def build_diary_text(d: dict, date_str: str, weekday: str, weather: str) -> str:
    tags_str = "　".join(d.get("tags", []))
    mood_str = d.get("mood", "")
    image_line = f"\n📸 影像解讀\n{d['image_notes']}\n" if d.get("image_notes") else ""
    return (
        f"📔 {d.get('title', '今日日記')}　{date_str} 週{weekday}\n"
        f"{'─' * 30}\n"
        f"☁️ 氣象與環境\n{d.get('body_weather', '')}　{weather}\n\n"
        f"🧠 醫師的巡房紀錄\n{d.get('doctor_notes', '—')}\n"
        f"{image_line}\n"
        f"📝 每日身心關懷\n"
        f"1. 🍽️ 飲食　{d.get('q1_food', '—')}\n"
        f"2. 🩸 生理　{d.get('q2_body', '—')}\n"
        f"3. 🔋 身體　{d.get('q3_energy', '—')}\n"
        f"4. 🌧️ 心情　{d.get('q4_mood', '—')}\n"
        f"5. 📅 事件　{d.get('q5_events', '—')}\n"
        f"6. 💊 用藥　{d.get('q6_meds', '—')}\n\n"
        f"🫂 諮商師的晚安語\n{d.get('goodnight', '—')}\n\n"
        f"{'─' * 30}\n"
        f"{mood_str}　{tags_str}"
    )


# ── Diary: Notion DB ───────────────────────────────────────────────────────────
async def ensure_diary_db():
    global NOTION_DIARY_DB_ID
    if NOTION_DIARY_DB_ID:
        return
    try:
        r = httpx.get(
            f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}",
            headers=NOTION_HEADERS, timeout=15,
        )
        parent_block = r.json()["parent"]["block_id"]
        payload = {
            "parent": {"type": "page_id", "page_id": parent_block},
            "title":  [{"text": {"content": "日記"}}],
            "properties": {
                "標題":    {"title": {}},
                "日期":    {"date": {}},
                "心情":    {"select": {"options": [{"name": m} for m in DIARY_MOODS]}},
                "標籤":    {"multi_select": {"options": [{"name": t} for t in DIARY_TAGS]}},
                "當日花費": {"number": {"format": "number"}},
            },
        }
        r2 = httpx.post(
            "https://api.notion.com/v1/databases",
            headers=NOTION_HEADERS, json=payload, timeout=30,
        )
        r2.raise_for_status()
        NOTION_DIARY_DB_ID = r2.json()["id"].replace("-", "")
        set_key(ENV_PATH, "NOTION_DIARY_DB", NOTION_DIARY_DB_ID)
        print(f"✅ 日記資料庫已建立：{NOTION_DIARY_DB_ID}")
    except Exception as ex:
        print(f"⚠️ 日記資料庫建立失敗（不影響其他功能）：{ex}")


def save_diary_to_notion(date_str: str, title: str, mood: str, tags: list, expense: int):
    if not NOTION_DIARY_DB_ID:
        return
    try:
        payload = {
            "parent": {"database_id": NOTION_DIARY_DB_ID},
            "properties": {
                "標題":    {"title": [{"text": {"content": title}}]},
                "日期":    {"date": {"start": date_str}},
                "心情":    {"select": {"name": mood}},
                "標籤":    {"multi_select": [{"name": t} for t in tags if t in DIARY_TAGS]},
                "當日花費": {"number": expense},
            },
        }
        r = httpx.post(
            "https://api.notion.com/v1/pages",
            headers=NOTION_HEADERS, json=payload, timeout=30,
        )
        r.raise_for_status()
    except Exception as ex:
        print(f"⚠️ 日記寫入 Notion 失敗：{ex}")


# ── Diary: Query ───────────────────────────────────────────────────────────────
def query_diary_pages(days: int = 7) -> list[dict]:
    if not NOTION_DIARY_DB_ID:
        return []
    since = (date.today() - timedelta(days=days - 1)).isoformat()
    r = httpx.post(
        f"https://api.notion.com/v1/databases/{NOTION_DIARY_DB_ID}/query",
        headers=NOTION_HEADERS,
        json={
            "filter": {"property": "日期", "date": {"on_or_after": since}},
            "sorts":  [{"property": "日期", "direction": "descending"}],
            "page_size": days,
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["results"]


def query_diary_by_date(date_str: str) -> list[dict]:
    if not NOTION_DIARY_DB_ID:
        return []
    r = httpx.post(
        f"https://api.notion.com/v1/databases/{NOTION_DIARY_DB_ID}/query",
        headers=NOTION_HEADERS,
        json={"filter": {"property": "日期", "date": {"equals": date_str}}},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["results"]


def embed_diary_list(pages: list[dict]) -> discord.Embed:
    e = discord.Embed(title="📔 最近日記", color=PURPLE)
    if not pages:
        e.description = "最近 7 天沒有日記記錄"
        return e
    lines = []
    for p in pages:
        props    = p["properties"]
        d_val    = props["日期"]["date"]
        d_str    = d_val["start"] if d_val else "？"
        title_   = props["標題"]["title"]
        title    = title_[0]["plain_text"] if title_ else "（無標題）"
        mood_sel = props["心情"]["select"]
        mood     = mood_sel["name"] if mood_sel else "—"
        lines.append(f"`{d_str}` {mood}　{title}")
    e.description = "\n".join(lines)
    return e


# ── Diary: Reminder ────────────────────────────────────────────────────────────
async def send_diary_reminder():
    if not DIARY_CHANNEL_ID:
        return
    ch = bot.get_channel(DIARY_CHANNEL_ID)
    if ch:
        await ch.send("📔 Dora，今天過得怎樣？可以跟我說說，也可以傳圖片給我 🌙")


# ── Diary: Message Handler ─────────────────────────────────────────────────────
async def handle_diary_message(message: discord.Message):
    text = message.content.strip()

    # "看日記" → 最近 7 天列表
    if "看日記" in text and not message.attachments:
        async with message.channel.typing():
            try:
                pages = query_diary_pages(7)
                await message.reply(embed=embed_diary_list(pages))
            except Exception as ex:
                await message.reply(embed=embed_error(str(ex)))
        return

    # "[日期]日記" → 查特定日期
    dm = _DIARY_DATE_RE.search(text)
    if dm and not message.attachments:
        target = _parse_diary_date(dm, text)
        async with message.channel.typing():
            try:
                pages = query_diary_by_date(target)
                em = (embed_diary_list(pages) if pages
                      else discord.Embed(description=f"📔 {target} 沒有日記記錄", color=PURPLE))
                await message.reply(embed=em)
            except Exception as ex:
                await message.reply(embed=embed_error(str(ex)))
        return

    # 沒有內容且沒有圖片 → 忽略
    if not text and not message.attachments:
        return

    # 生成日記（最多重試 2 次）
    for attempt in range(3):
        try:
            async with message.channel.typing():
                # 下載圖片附件
                images: list[tuple[bytes, str]] = []
                async with httpx.AsyncClient(timeout=20) as hc:
                    for att in message.attachments:
                        if att.content_type and att.content_type.startswith("image/"):
                            r = await hc.get(att.url)
                            images.append((r.content, att.content_type.split(";")[0]))

                # 取得各項上下文
                weather = await get_taipei_weather()

                emails_text = fetch_emails_with_cache()

                try:
                    calendar_text = gmail_helper.get_today_events(GMAIL_CREDENTIALS, GMAIL_TOKEN)
                except Exception:
                    calendar_text = "（Calendar 未設定）"

                expense_total, expense_text = get_today_expense_summary()

                # Gemini 生成日記
                now     = datetime.now(TAIPEI)
                weekday = WEEKDAYS[now.weekday()]
                d       = await generate_diary_content(
                    text, images, weather, emails_text, calendar_text, expense_text
                )
                diary_text = build_diary_text(d, now.strftime("%Y/%m/%d"), weekday, weather)

                # 寫入 Notion
                save_diary_to_notion(
                    date_str=now.strftime("%Y-%m-%d"),
                    title=d.get("title", "今日日記"),
                    mood=d.get("mood", DIARY_MOODS[1]),
                    tags=d.get("tags", []),
                    expense=expense_total,
                )

                await message.reply(diary_text)
            break  # 成功

        except Exception as ex:
            if _is_rate_limit(ex) and attempt < 2:
                await message.channel.send("⏳ AI 額度暫時用完，34 秒後自動重試...")
                await asyncio.sleep(34)
            else:
                await message.reply(embed=embed_error(str(ex)))
                break


# ── Discord Bot ────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)

scheduler = AsyncIOScheduler(timezone=TAIPEI)
scheduler.add_job(send_morning_report,  CronTrigger(hour=8,  minute=0))
scheduler.add_job(send_diary_reminder,  CronTrigger(hour=21, minute=30))


@bot.event
async def on_ready():
    print(f"✅ Bot 已上線：{bot.user}（ID: {bot.user.id}）")
    await ensure_morning_db()
    await ensure_diary_db()
    if not scheduler.running:
        scheduler.start()
    if DISCORD_CHANNEL_ID:
        print(f"📅 晨報排程：每天 08:00 (Asia/Taipei) → 頻道 {DISCORD_CHANNEL_ID}")
    else:
        print("⚠️ 未設定 DISCORD_CHANNEL_ID，晨報功能停用")
    if DIARY_CHANNEL_ID:
        print(f"📔 日記提醒：每天 21:30 (Asia/Taipei) → 頻道 {DIARY_CHANNEL_ID}")
    else:
        print("⚠️ 未設定 DIARY_CHANNEL_ID，日記提醒停用")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    text = message.content.strip()
    cid  = message.channel.id

    # ── Diary 頻道 ────────────────────────────────────────────────────
    if DIARY_CHANNEL_ID and cid == DIARY_CHANNEL_ID:
        await handle_diary_message(message)
        return

    # ── 日記指令（任意頻道）──────────────────────────────────────────
    if "看日記" in text:
        async with message.channel.typing():
            try:
                pages = query_diary_pages(7)
                await message.reply(embed=embed_diary_list(pages))
            except Exception as ex:
                await message.reply(embed=embed_error(str(ex)))
        return

    dm = _DIARY_DATE_RE.search(text)
    if dm:
        target = _parse_diary_date(dm, text)
        async with message.channel.typing():
            try:
                pages = query_diary_by_date(target)
                em = (embed_diary_list(pages) if pages
                      else discord.Embed(description=f"📔 {target} 沒有日記記錄", color=PURPLE))
                await message.reply(embed=em)
            except Exception as ex:
                await message.reply(embed=embed_error(str(ex)))
        return

    # ── 一般記帳頻道 ──────────────────────────────────────────────────
    if not text:
        return

    history     = channel_history.get(cid, deque())
    embed       = None
    bot_summary = ""

    async def _process() -> bool:
        """執行核心處理，回傳是否成功。"""
        nonlocal embed, bot_summary

        # ① regex 直接解析（格式明確的記帳，零 AI）
        parsed = try_parse_expense_regex(text)
        if parsed:
            item, amount, cat, acc = parsed["item"], parsed["amount"], parsed["category"], parsed["account"]
            add_to_notion(item, amount, cat, acc)
            embed       = embed_record(item, amount, cat, acc)
            bot_summary = f"已記帳：{item} ${abs(int(amount))}"
            return True

        # ② 關鍵字快速分類（常見查詢/行情，零 AI）
        intent_data = quick_classify(text)

        # ③ 無法判斷 → Gemini 分類（唯一 AI 呼叫點）
        if intent_data is None:
            intent_data = classify_intent(text, history)

        intent   = intent_data.get("intent", "unclear")
        category = intent_data.get("category", "")

        if intent == "record":
            data   = parse_expense(text)
            item, amount, cat, acc = data["item"], data["amount"], data["category"], data["account"]
            add_to_notion(item, amount, cat, acc)
            embed       = embed_record(item, amount, cat, acc)
            bot_summary = f"已記帳：{item} ${abs(int(amount))}"

        elif intent == "expense":
            pages = query_this_month()
            if category and category in CATEGORIES:
                emoji       = CATEGORY_EMOJI.get(category, "📌")
                embed       = embed_category_total(pages, category, emoji)
                bot_summary = f"查詢 {category} 支出"
            else:
                embed         = embed_monthly_expense(pages)
                expense_total = sum(abs(extract_amount(p)) for p in pages
                                    if not extract_category(p).startswith("收入"))
                bot_summary = f"本月支出 ${expense_total:,}"

        elif intent == "income":
            pages        = query_this_month()
            embed        = embed_monthly_income(pages)
            income_total = sum(abs(extract_amount(p)) for p in pages
                               if extract_category(p).startswith("收入"))
            bot_summary = f"本月收入 ${income_total:,}"

        elif intent == "balance":
            pages   = query_this_month()
            embed   = embed_monthly_balance(pages)
            income  = sum(abs(extract_amount(p)) for p in pages if extract_category(p).startswith("收入"))
            expense = sum(abs(extract_amount(p)) for p in pages if not extract_category(p).startswith("收入"))
            bot_summary = f"本月結餘 ${income - expense:,}"

        elif intent == "detail":
            pages       = query_this_month()
            embed       = embed_monthly_detail(pages)
            bot_summary = f"顯示本月明細（{len(pages)} 筆）"

        elif intent == "market":
            data, comment = fetch_market_with_cache()
            embed         = embed_market(data, comment)
            bot_summary   = f"市場行情：{comment}"

        elif intent == "chat":
            reply_text  = chat_reply(text, history)
            embed       = discord.Embed(description=reply_text, color=GREEN)
            bot_summary = reply_text

        else:  # unclear
            hint        = "可以說得更清楚嗎？😊\n例如：「路易莎 45」記帳、「花多少了」查支出、「大盤怎樣」看行情"
            embed       = discord.Embed(description=hint, color=GOLD)
            bot_summary = "請求不明確"

        return True

    # 執行，遇到 429 等 35 秒重試一次
    async with message.channel.typing():
        try:
            await _process()
        except Exception as e:
            if _is_rate_limit(e):
                await message.channel.send("⏳ 稍等一下，AI 忙碌中，35 秒後自動重試...")
                await asyncio.sleep(35)
                try:
                    await _process()
                except Exception as e2:
                    embed       = discord.Embed(description="⏳ 稍等一下，AI 忙碌中", color=GOLD)
                    bot_summary = f"錯誤：{e2}"
            else:
                embed       = embed_error(str(e))
                bot_summary = f"錯誤：{e}"

    # 更新對話 history
    _push_history(cid, "user",  text)
    _push_history(cid, "model", bot_summary)

    if embed:
        await message.reply(embed=embed)


if __name__ == "__main__":
    bot.run(os.environ["DISCORD_TOKEN"])
