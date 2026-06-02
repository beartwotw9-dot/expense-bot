import os, json, base64, httpx, pytz, sys
from datetime import date, datetime, timedelta
from dotenv import load_dotenv
load_dotenv()
TAIPEI = pytz.timezone("Asia/Taipei")
WEEKDAYS = ["一","二","三","四","五","六","日"]
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN","")
DISCORD_CHANNEL_ID = os.environ.get("DISCORD_CHANNEL_ID","")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL","")
NOTION_TOKEN = os.environ["NOTION_TOKEN"]
NOTION_DATABASE_ID = os.environ["NOTION_DATABASE_ID"]
NOTION_MORNING_DB = os.environ.get("NOTION_MORNING_DB","")
NOTION_HEADERS = {"Authorization":f"Bearer {NOTION_TOKEN}","Notion-Version":"2022-06-28","Content-Type":"application/json"}
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import market as mkt
def restore_gmail_token():
    for env_var, filename in [("GMAIL_TOKEN_B64","token.json"),("GMAIL_CREDENTIALS_B64","credentials.json")]:
        val = os.environ.get(env_var,"")
        if val:
            with open(filename,"w") as f:
                f.write(base64.b64decode(val).decode())
def fetch_market():
    data = mkt.fetch_market_data()
    return data, mkt.rule_comment(data), [mkt.format_line(d) for d in data]
def fetch_emails():
    try:
        import gmail_helper
        emails = gmail_helper.get_important_unread("credentials.json","token.json",max_results=3)
        if emails:
            return "\n".join(f"📨 **{e['subject'][:50]}**\n　　_{e['from']}_" for e in emails)
        return "今天沒有重要郵件 ✨"
    except Exception as e:
        return f"Gmail 讀取失敗：{e}"
def query_yesterday(yesterday):
    r = httpx.post(f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query",
        headers=NOTION_HEADERS,json={"filter":{"property":"日期","date":{"equals":yesterday}}},timeout=30)
    r.raise_for_status()
    pages = [p for p in r.json()["results"]
             if not (p["properties"]["分類"]["select"] or {}).get("name","").startswith("收入")]
    if not pages: return 0, "昨天沒有記帳喔！請補記 🙈"
    def amt(p): return p["properties"]["金額"]["number"] or 0
    def name(p): return (p["properties"]["名稱"]["title"] or [{}])[0].get("plain_text","?")
    total = sum(abs(amt(p)) for p in pages)
    big = max(pages, key=lambda p: abs(amt(p)))
    return total, f"總計 **${total:,}**（共 {len(pages)} 筆）\n最大一筆：{name(big)} **${abs(amt(big)):,}**"
def clean_source_text(text, label):
    if "invalid_grant" in text or "讀取失敗" in text:
        return f"未同步。請重新授權 {label}。"
    if "Unauthorized" in text or "查詢失敗" in text:
        return f"未同步。請檢查 {label} 權限或 GitHub Secret。"
    return text
def checkbox_line(text):
    return f"☐ {text}"
def template_summary(comment, email_text, expense_text):
    clean_email = clean_source_text(email_text, "Gmail")
    clean_expense = clean_source_text(expense_text, "Notion")
    return "\n".join([
        f"- 市場：{comment}",
        f"- 信箱：{clean_email.splitlines()[0][:90]}",
        f"- 記帳：{clean_expense.splitlines()[0][:90]}",
    ])
def template_actions(email_text, expense_text):
    items = ["打開 Today Hub，選 1 件最重要的事開始", "晚上補一行今日回顧"]
    if "invalid_grant" in email_text or "讀取失敗" in email_text:
        items.append("重新授權 Gmail，恢復信箱摘要")
    if "Unauthorized" in expense_text or "查詢失敗" in expense_text:
        items.append("更新 Notion token / database 權限，恢復花費回顧")
    return "\n".join(checkbox_line(item) for item in items)
def notion_rich_text(text):
    return [{"type":"text","text":{"content":text[:2000]}}]
def notion_heading(text, level=2):
    key = "heading_2" if level == 2 else "heading_3"
    return {"object":"block","type":key,key:{"rich_text":notion_rich_text(text)}}
def notion_paragraph(text):
    return {"object":"block","type":"paragraph","paragraph":{"rich_text":notion_rich_text(text)}}
def notion_bullets(lines):
    return [
        {"object":"block","type":"bulleted_list_item","bulleted_list_item":{"rich_text":notion_rich_text(line.lstrip("- ").strip())}}
        for line in lines if line.strip()
    ]
def notion_todos(lines):
    return [
        {"object":"block","type":"to_do","to_do":{"rich_text":notion_rich_text(line.replace("☐ ","",1).strip()),"checked":False}}
        for line in lines if line.strip()
    ]
def build_notion_children(today_str, weekday, comment, market_text, email_text, expense_text):
    summary = template_summary(comment, email_text, expense_text)
    actions = template_actions(email_text, expense_text)
    return [
        notion_heading("🌅 每日晨報模板"),
        notion_paragraph(f"日期：{today_str}（週{weekday}）"),
        notion_heading("📌 今日摘要"),
        *notion_bullets(summary.splitlines()),
        notion_heading("✅ 今日行動"),
        *notion_todos(actions.splitlines()),
        notion_heading("📈 市場觀察"),
        notion_paragraph(clean_source_text(market_text, "Market")),
        notion_heading("📬 信箱摘要"),
        notion_paragraph(clean_source_text(email_text, "Gmail")),
        notion_heading("💰 昨日花費"),
        notion_paragraph(clean_source_text(expense_text, "Notion")),
        notion_heading("📝 晚間回顧"),
        notion_paragraph("今天完成了：\n今天卡住了：\n明天第一步："),
    ]
def build_discord_embed(now, today_str, weekday, comment, market_text, market_preview, email_text, expense_text):
    summary = template_summary(comment, email_text, expense_text)
    actions = template_actions(email_text, expense_text)
    return {
        "title": f"🌅 {today_str} Morning Page",
        "description": f"Notion-style daily template｜週{weekday}",
        "color": 15844367,
        "fields": [
            {"name":"📌 今日摘要","value":summary[:1024],"inline":False},
            {"name":"✅ 今日行動","value":actions[:1024],"inline":False},
            {"name":"📈 市場觀察","value":f"{comment}\n\n{market_preview}"[:1024],"inline":False},
            {"name":"📬 信箱摘要","value":clean_source_text(email_text, "Gmail")[:1024],"inline":False},
            {"name":"💰 昨日花費","value":clean_source_text(expense_text, "Notion")[:1024],"inline":False},
            {"name":"📝 晚間回顧模板","value":"今天完成了：\n今天卡住了：\n明天第一步：","inline":False},
        ],
        "footer":{"text":f"{today_str} GitHub Actions → Discord / Notion"},
    }
def save_notion(today_str, weekday, comment, market_text, email_text, expense_text, expense):
    if not NOTION_MORNING_DB: return
    try:
        httpx.post("https://api.notion.com/v1/pages",headers=NOTION_HEADERS,timeout=30,json={
            "parent":{"database_id":NOTION_MORNING_DB},
            "properties":{
                "標題":{"title":[{"text":{"content":f"{today_str} 晨報"}}]},
                "日期":{"date":{"start":today_str}},
                "市場摘要":{"rich_text":[{"text":{"content":market_text[:2000]}}]},
                "信箱摘要":{"rich_text":[{"text":{"content":email_text[:2000]}}]},
                "昨日花費":{"number":expense},
            },
            "children":build_notion_children(today_str, weekday, comment, market_text, email_text, expense_text),
        }).raise_for_status()
        print("✅ Notion 寫入成功")
    except Exception as e:
        print(f"⚠️ Notion 失敗：{e}")
def discord_headers():
    token = DISCORD_TOKEN.strip()
    authorization = token if token.lower().startswith("bot ") else f"Bot {token}"
    return {"Authorization":authorization,"Content-Type":"application/json"}
def send_discord(embed):
    if DISCORD_WEBHOOK_URL:
        r = httpx.post(DISCORD_WEBHOOK_URL,json={"embeds":[embed]},timeout=30)
    elif DISCORD_TOKEN and DISCORD_CHANNEL_ID:
        r = httpx.post(f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL_ID}/messages",
            headers=discord_headers(),json={"embeds":[embed]},timeout=30)
    else:
        print("⚠️ Discord 略過：請設定 DISCORD_WEBHOOK_URL，或同時設定 DISCORD_TOKEN / DISCORD_CHANNEL_ID")
        return
    if r.status_code == 401:
        print("⚠️ Discord 授權失敗：請更新 GitHub Secret DISCORD_TOKEN，或改用 DISCORD_WEBHOOK_URL")
        return
    if r.status_code == 403:
        print("⚠️ Discord 權限不足：請確認 bot 已加入伺服器且可在該 channel 發訊息")
        return
    r.raise_for_status()
    print("✅ Discord 發送成功")
def main():
    restore_gmail_token()
    now = datetime.now(TAIPEI)
    today_str = now.strftime("%Y-%m-%d")
    weekday = WEEKDAYS[now.weekday()]
    yesterday = (now.date()-timedelta(days=1)).isoformat()
    print(f"🌅 發送晨報：{today_str} 週{weekday}")
    try: _, comment, lines = fetch_market(); mv = "\n".join(lines)[:1024]; mt = "\n".join(lines)
    except Exception as e: mv = mt = comment = f"市場失敗：{e}"
    email_text = fetch_emails()
    try: yest_total, expense_text = query_yesterday(yesterday)
    except Exception as e: yest_total=0; expense_text=f"查詢失敗：{e}"
    embed = build_discord_embed(now, today_str, weekday, comment, mt, mv, email_text, expense_text)
    save_notion(today_str, weekday, comment, mt, email_text, expense_text, yest_total)
    send_discord(embed)
if __name__=="__main__":
    main()
