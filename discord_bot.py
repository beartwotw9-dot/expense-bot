#!/usr/bin/env python3
"""
Hua Personal Discord Bot
========================
Features:
  - 晨報 at 08:00 Asia/Taipei (auto + manual /晨報)
  - Expense recording with smart categorization
  - Notes channel → Notion (AI auto-tag via Claude)
  - /維護 remote maintenance (owner only)
"""

import os
import re
import asyncio
import subprocess
import datetime
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands, tasks
from discord import app_commands
import httpx
from dotenv import load_dotenv

load_dotenv()

# ============================================================
# Config
# ============================================================
def _int_env(key: str) -> int:
    return int(os.getenv(key, "") or "0")

TOKEN               = os.environ["DISCORD_BOT_TOKEN"]
GUILD_ID            = _int_env("DISCORD_GUILD_ID")
MORNING_CH_ID       = _int_env("DISCORD_MORNING_CHANNEL_ID")
EXPENSE_CH_ID       = _int_env("DISCORD_EXPENSE_CHANNEL_ID")
NOTES_CH_ID         = _int_env("DISCORD_NOTES_CHANNEL_ID")
DIARY_CH_ID         = _int_env("DISCORD_DIARY_CHANNEL_ID")
OWNER_ID            = _int_env("DISCORD_OWNER_ID")
DASHBOARD_URL       = os.getenv("DASHBOARD_URL", "http://localhost:8787")
WEBHOOK_SECRET      = os.getenv("WEBHOOK_SECRET", "")
NOTION_TOKEN        = os.getenv("NOTION_TOKEN", "")
NOTION_NOTES_DB     = os.getenv("NOTION_NOTES_DB_ID", "")
NOTION_DIARY_DB     = os.getenv("NOTION_DIARY_DB_ID", "")
ANTHROPIC_API_KEY   = os.getenv("ANTHROPIC_API_KEY", "")
NEWS_RSS_URL        = os.getenv("NEWS_RSS_URL", "https://www.cna.com.tw/rss/aall.aspx")

TZ = ZoneInfo("Asia/Taipei")


# ============================================================
# Feature 3 — Expense categorization rules
# ============================================================
EXPENSE_RULES: list[tuple[list[str], str]] = [
    (
        ["路易莎", "星巴克", "50嵐", "五十嵐", "手搖飲", "飲料", "咖啡", "拿鐵", "珍奶", "茶飲"],
        "快樂_飲料與咖啡",
    ),
    (
        ["電話費", "網路費", "電費", "水費", "瓦斯費"],
        "必須_餐費與日用",
    ),
    (
        ["捷運", "公車", "計程車", "Uber", "uber", "油費", "停車費", "火車", "高鐵"],
        "必須_交通",
    ),
]

# Shortcuts: only when the user says JUST this word (account field)
ACCOUNT_SHORTCUTS: dict[str, str] = {
    "信用卡": "國泰信用卡",
    "存款":   "國泰存款",
}


def detect_category(text: str) -> str:
    for keywords, cat in EXPENSE_RULES:
        if any(kw in text for kw in keywords):
            return cat
    return "其他"


def resolve_account(token: str) -> str | None:
    """Map a shorthand account keyword to the full account name."""
    return ACCOUNT_SHORTCUTS.get(token.strip())


# ---- Expense message parser ----
# Accepted formats:
#   描述 金額 [帳戶]   e.g.  珍奶 55 信用卡
#   金額 描述 [帳戶]   e.g.  55 珍奶
_EXP_DESC_FIRST  = re.compile(r"^(.+?)\s+(\d+(?:\.\d+)?)(?:\s+(\S+))?$")
_EXP_AMT_FIRST   = re.compile(r"^(\d+(?:\.\d+)?)\s+(.+?)(?:\s+(\S+))?$")


def parse_expense(text: str) -> dict | None:
    """Return {desc, amount, category, account} or None."""
    m = _EXP_DESC_FIRST.match(text)
    if m:
        desc, amount, acct_hint = m.group(1), float(m.group(2)), m.group(3) or ""
    else:
        m2 = _EXP_AMT_FIRST.match(text)
        if not m2:
            return None
        amount, desc, acct_hint = float(m2.group(1)), m2.group(2), m2.group(3) or ""

    return {
        "desc":     desc,
        "amount":   amount,
        "category": detect_category(desc),
        "account":  resolve_account(acct_hint),
    }


# ============================================================
# Feature 4 helpers — Notion notes
# ============================================================
async def classify_tag(text: str) -> str:
    """Classify note into one of 6 tags. Uses Claude if key is set."""
    if ANTHROPIC_API_KEY:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=8,
                messages=[{
                    "role": "user",
                    "content": (
                        "將以下筆記分類為：工作、生活、學習、想法、待辦、其他。"
                        "只回答分類名稱，不要任何其他文字。\n\n" + text
                    ),
                }],
            )
            tag = resp.content[0].text.strip()
            if tag in ("工作", "生活", "學習", "想法", "待辦", "其他"):
                return tag
        except Exception:
            pass

    # Keyword fallback
    if any(w in text for w in ["工作", "專案", "會議", "報告", "客戶", "deadline", "同事"]):
        return "工作"
    if any(w in text for w in ["買", "吃", "玩", "旅", "電影", "朋友", "家人"]):
        return "生活"
    if any(w in text for w in ["學", "讀", "課", "練習", "筆記", "複習", "考試"]):
        return "學習"
    if any(w in text for w in ["想", "覺得", "感覺", "如果", "或許", "應該", "要不要"]):
        return "想法"
    if any(w in text for w in ["記得", "要", "待辦", "todo", "需要", "別忘", "提醒"]):
        return "待辦"
    return "其他"


async def ai_note_comment(text: str) -> str:
    """Return a short, casual one-line reply about the note."""
    if not ANTHROPIC_API_KEY:
        return ""
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=40,
            messages=[{
                "role": "user",
                "content": (
                    "針對這則個人筆記，給一句15字以內的輕鬆繁體中文回應（不要加引號）：\n" + text
                ),
            }],
        )
        return resp.content[0].text.strip()
    except Exception:
        return ""


async def notion_save(title: str, content: str, tag: str) -> bool:
    if not NOTION_TOKEN or not NOTION_NOTES_DB:
        return False
    try:
        from notion_client import AsyncClient
        notion = AsyncClient(auth=NOTION_TOKEN)
        now_iso = datetime.datetime.now(TZ).isoformat()
        await notion.pages.create(
            parent={"database_id": NOTION_NOTES_DB},
            properties={
                "標題": {"title": [{"text": {"content": title}}]},
                "內容": {"rich_text": [{"text": {"content": content}}]},
                "時間": {"date": {"start": now_iso}},
                "標籤": {"select": {"name": tag}},
            },
        )
        return True
    except Exception as e:
        print(f"[Notion] save failed: {e}")
        return False


async def notion_search(query: str) -> list[dict]:
    if not NOTION_TOKEN or not NOTION_NOTES_DB:
        return []
    try:
        from notion_client import AsyncClient
        notion = AsyncClient(auth=NOTION_TOKEN)
        res = await notion.databases.query(
            database_id=NOTION_NOTES_DB,
            filter={"or": [
                {"property": "標題", "title":     {"contains": query}},
                {"property": "內容", "rich_text": {"contains": query}},
            ]},
            sorts=[{"property": "時間", "direction": "descending"}],
            page_size=5,
        )
        return res.get("results", [])
    except Exception as e:
        print(f"[Notion] search failed: {e}")
        return []


async def notion_list(tag_filter: str | None = None) -> list[dict]:
    if not NOTION_TOKEN or not NOTION_NOTES_DB:
        return []
    try:
        from notion_client import AsyncClient
        notion = AsyncClient(auth=NOTION_TOKEN)
        kwargs: dict = {
            "database_id": NOTION_NOTES_DB,
            "sorts": [{"property": "時間", "direction": "descending"}],
            "page_size": 10,
        }
        if tag_filter:
            kwargs["filter"] = {"property": "標籤", "select": {"equals": tag_filter}}
        res = await notion.databases.query(**kwargs)
        return res.get("results", [])
    except Exception as e:
        print(f"[Notion] list failed: {e}")
        return []


def fmt_pages(pages: list[dict]) -> str:
    if not pages:
        return "（找不到筆記）"
    lines = []
    for p in pages:
        try:
            title = p["properties"]["標題"]["title"][0]["text"]["content"]
            date  = p["properties"]["時間"]["date"]["start"][:10]
            sel   = p["properties"].get("標籤", {}).get("select") or {}
            tag   = f" `{sel['name']}`" if sel.get("name") else ""
            lines.append(f"• {date}{tag} {title}")
        except (KeyError, IndexError):
            continue
    return "\n".join(lines) or "（找不到筆記）"


def _no_notion_warn() -> str:
    return "\n⚠️ Notion 未設定（請在 .env 填入 NOTION_TOKEN 和 NOTION_NOTES_DB_ID）"


# ============================================================
# News helpers — 晨報新聞區塊
# ============================================================
async def fetch_news_headlines(n: int = 5) -> list[dict]:
    """Fetch top N headlines from Taiwan RSS (中央社 by default)."""
    try:
        import feedparser
        feed = await asyncio.to_thread(feedparser.parse, NEWS_RSS_URL)
        items = []
        for entry in feed.entries[:n]:
            items.append({
                "title":   entry.get("title", "").strip(),
                "link":    entry.get("link", ""),
                "summary": entry.get("summary", "")[:120].strip(),
            })
        return items
    except Exception as e:
        print(f"[News] RSS fetch failed: {e}")
        return []


async def ai_news_impact(headlines: list[dict]) -> str:
    """Use Claude to write a 2-3 sentence impact summary for today's news."""
    if not ANTHROPIC_API_KEY or not headlines:
        return ""
    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        headline_text = "\n".join(f"・{h['title']}" for h in headlines)
        resp = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=180,
            messages=[{
                "role": "user",
                "content": (
                    "以下是今日台灣新聞頭條，請用 2-3 句話分析這些新聞對一般上班族今天的日常生活"
                    "可能有什麼影響或需要注意的事（繁體中文，口吻輕鬆直接，不要列點）：\n\n"
                    + headline_text
                ),
            }],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        print(f"[News AI] impact failed: {e}")
        return ""


# ============================================================
# Diary helpers — 日記 → Notion
# ============================================================
_DIARY_MOODS = ["開心", "普通", "低落", "焦慮", "充實", "平靜"]
_MOOD_EMOJI  = {"開心": "😊", "普通": "😐", "低落": "😔",
                "焦慮": "😰", "充實": "💪", "平靜": "😌"}


async def ai_diary_mood(text: str) -> str:
    """Classify diary entry into one of the 6 moods."""
    if ANTHROPIC_API_KEY:
        try:
            import anthropic
            client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
            moods = "、".join(_DIARY_MOODS)
            resp = await client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=8,
                messages=[{
                    "role": "user",
                    "content": (
                        f"根據以下日記內容，從「{moods}」中選出最符合的心情，"
                        "只回答心情名稱，不加任何說明：\n\n" + text
                    ),
                }],
            )
            mood = resp.content[0].text.strip()
            if mood in _DIARY_MOODS:
                return mood
        except Exception:
            pass

    # Keyword fallback
    if any(w in text for w in ["開心", "高興", "快樂", "爽", "棒", "讚", "超好"]):
        return "開心"
    if any(w in text for w in ["累", "煩", "難過", "傷心", "哭", "慘", "失落"]):
        return "低落"
    if any(w in text for w in ["焦慮", "緊張", "壓力", "擔心", "怕", "不安"]):
        return "焦慮"
    if any(w in text for w in ["充實", "滿足", "完成", "成就", "努力"]):
        return "充實"
    if any(w in text for w in ["平靜", "還好", "一般", "普通", "平常"]):
        return "平靜"
    return "普通"


async def ai_diary_reflection(text: str) -> str:
    """Warm, brief reflection on a diary entry (≤20 chars)."""
    if not ANTHROPIC_API_KEY:
        return ""
    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        resp = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=60,
            messages=[{
                "role": "user",
                "content": (
                    "對以下日記內容，給一句溫暖且有感的繁體中文回應（20字以內，不加引號）：\n"
                    + text
                ),
            }],
        )
        return resp.content[0].text.strip()
    except Exception:
        return ""


async def notion_diary_save(title: str, content: str, mood: str) -> bool:
    """Save diary entry to Notion diary database."""
    if not NOTION_TOKEN or not NOTION_DIARY_DB:
        return False
    try:
        from notion_client import AsyncClient
        notion = AsyncClient(auth=NOTION_TOKEN)
        now_iso = datetime.datetime.now(TZ).isoformat()
        await notion.pages.create(
            parent={"database_id": NOTION_DIARY_DB},
            properties={
                "標題": {"title": [{"text": {"content": title}}]},
                "內容": {"rich_text": [{"text": {"content": content}}]},
                "日期": {"date": {"start": now_iso}},
                "心情": {"select": {"name": mood}},
            },
        )
        return True
    except Exception as e:
        print(f"[Notion Diary] save failed: {e}")
        return False


# ============================================================
# Feature 5 — Maintenance command map
# ============================================================
SAFE_CMDS: dict[str, str] = {
    "查看日誌":   "journalctl -u hua-dashboard --no-pager -n 50 2>/dev/null || echo '(no systemd journal)'",
    "查看磁碟":   "df -h",
    "查看記憶體": "free -h 2>/dev/null || vm_stat 2>/dev/null || echo 'memory info unavailable'",
    "備份資料庫": (
        "cp hua_dashboard.db "
        "hua_dashboard_backup_$(date +%Y%m%d_%H%M%S).db 2>/dev/null "
        "&& echo '✅ 備份完成' || echo '⚠️ 找不到 hua_dashboard.db（可能用 PostgreSQL）'"
    ),
    "清除快取":   "find . -name '__pycache__' -exec rm -rf {} + 2>/dev/null; echo '✅ 快取已清除'",
    "列出進程":   "ps aux | grep -E 'uvicorn|python' | grep -v grep || echo '（未找到相關進程）'",
    "查看版本":   "python --version && pip show fastapi sqlmodel | grep -E 'Name|Version'",
    "查看錯誤":   "journalctl -u hua-dashboard -p err --no-pager -n 20 2>/dev/null || echo '(no systemd journal)'",
}


async def interpret_cmd(request: str) -> tuple[str | None, str]:
    """Return (cmd_key, explanation)."""
    # Direct match
    for key in SAFE_CMDS:
        if key in request:
            return key, f"執行：{key}"

    if not ANTHROPIC_API_KEY:
        opts = "、".join(SAFE_CMDS)
        return None, f"無法解析。可用指令：{opts}"

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        opts = "、".join(SAFE_CMDS)
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=15,
            messages=[{
                "role": "user",
                "content": (
                    f"從下列選項選出最符合需求的指令，只回答名稱，不加任何說明：\n"
                    f"選項：{opts}\n需求：{request}"
                ),
            }],
        )
        key = resp.content[0].text.strip()
        if key in SAFE_CMDS:
            return key, f"AI 解析：{key}"
    except Exception:
        pass

    opts = "、".join(SAFE_CMDS)
    return None, f"無法對應指令。可用：{opts}"


# ============================================================
# Bot setup
# ============================================================
class HuaBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        # Sync slash commands
        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        morning_task.start()

    async def on_ready(self):
        print(f"✅ Bot 上線：{self.user}  (guild={GUILD_ID})")
        await self.change_presence(activity=discord.Activity(
            type=discord.ActivityType.watching, name="Hua Command Center"
        ))


bot = HuaBot()


# ============================================================
# Feature 2 — Morning report at 08:00 Taipei
# ============================================================
_MORNING_TIME = datetime.time(8, 0, tzinfo=TZ)
_WEEKDAYS     = ["一", "二", "三", "四", "五", "六", "日"]


@tasks.loop(time=_MORNING_TIME)
async def morning_task():
    await _send_morning_report()


@morning_task.before_loop
async def _before_morning():
    await bot.wait_until_ready()


async def _send_morning_report():
    if not MORNING_CH_ID:
        print("[晨報] DISCORD_MORNING_CHANNEL_ID 未設定，跳過")
        return
    ch = bot.get_channel(MORNING_CH_ID)
    if ch is None:
        print(f"[晨報] 找不到頻道 {MORNING_CH_ID}")
        return

    now = datetime.datetime.now(TZ)
    day = _WEEKDAYS[now.weekday()]

    embed = discord.Embed(
        title=f"☀️ 晨報　{now.strftime('%Y/%m/%d')}（週{day}）",
        color=0x4D96FF,
        timestamp=now,
    )

    # Fetch active projects
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{DASHBOARD_URL}/projects/api")
            projects = r.json() if r.is_success else []
        active = [p for p in projects if p.get("status") == "active"]
        if active:
            lines = "\n".join(
                f"• `{p['name']}` — {p['progress']}%  {_progress_bar(p['progress'])}"
                for p in active[:6]
            )
            embed.add_field(name="📁 進行中專案", value=lines, inline=False)
    except Exception as e:
        embed.add_field(name="📁 進行中專案", value=f"（無法取得：{e}）", inline=False)

    # Fetch today's calendar events
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{DASHBOARD_URL}/calendar/events?days=1")
            events = r.json() if r.is_success else []
        if events:
            lines = "\n".join(
                f"• {'`全天`' if e['all_day'] else e['start'][11:16]}  {e['title']}"
                + (f"  📍{e['location']}" if e.get("location") else "")
                for e in events[:8]
            )
            embed.add_field(name="📅 今日行程", value=lines, inline=False)
        else:
            embed.add_field(name="📅 今日行程", value="今天沒有行程 🎉", inline=False)
    except Exception:
        embed.add_field(name="📅 今日行程", value="（無法取得）", inline=False)

    # Fetch today's news + AI impact analysis
    headlines = await fetch_news_headlines(5)
    if headlines:
        news_lines = "\n".join(f"・{h['title']}" for h in headlines)
        impact     = await ai_news_impact(headlines)
        news_value = news_lines
        if impact:
            news_value += f"\n\n💡 {impact}"
        embed.add_field(name="📰 今日新聞", value=news_value[:1024], inline=False)

    embed.set_footer(text="Hua Command Center • 每日 08:00 自動發送")
    await ch.send(embed=embed)


def _progress_bar(pct: int, width: int = 8) -> str:
    filled = round(pct / 100 * width)
    return "█" * filled + "░" * (width - filled)


# Slash command: /晨報
@bot.tree.command(name="晨報", description="手動觸發今日晨報")
async def slash_morning(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=False)
    await _send_morning_report()
    await interaction.followup.send("✅ 晨報已發送！")


# Prefix command: !晨報
@bot.command(name="晨報")
async def prefix_morning(ctx: commands.Context):
    await _send_morning_report()
    await ctx.reply("✅ 晨報已發送！", mention_author=False)


# ============================================================
# Feature 5 — /維護 (owner only)
# ============================================================
@bot.tree.command(name="維護", description="遠端執行維護動作（僅限 Bot 擁有者）")
@app_commands.describe(指令="例如：查看日誌、備份資料庫、查看磁碟、清除快取")
async def slash_maintenance(interaction: discord.Interaction, 指令: str):
    if OWNER_ID and interaction.user.id != OWNER_ID:
        await interaction.response.send_message("⛔ 此指令僅限 Bot 擁有者使用", ephemeral=True)
        return

    await interaction.response.defer()

    key, explanation = await interpret_cmd(指令)
    if not key:
        await interaction.followup.send(f"❓ {explanation}")
        return

    await interaction.followup.send(f"🔧 收到，開始處理\n> {explanation}")

    bot_dir = os.path.dirname(os.path.abspath(__file__))
    try:
        result = subprocess.run(
            SAFE_CMDS[key],
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=bot_dir,
        )
        output = (result.stdout + result.stderr).strip()
        if output:
            # Discord message limit 2000 chars
            for chunk in [output[i:i+1900] for i in range(0, min(len(output), 3800), 1900)]:
                await interaction.followup.send(f"```\n{chunk}\n```")
        else:
            await interaction.followup.send("✅ 執行完成（無輸出）")
    except subprocess.TimeoutExpired:
        await interaction.followup.send("⏰ 執行逾時（30 秒）")
    except Exception as e:
        await interaction.followup.send(f"❌ 執行失敗：{e}")


# ============================================================
# on_message — Expense (Feature 3) + Notes (Feature 4)
# ============================================================
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    text = message.content.strip()

    # ---- Diary channel ----
    if DIARY_CH_ID and message.channel.id == DIARY_CH_ID:
        await _handle_diary(message, text)
        return

    # ---- Notes channel (Feature 4) ----
    if NOTES_CH_ID and message.channel.id == NOTES_CH_ID:
        await _handle_notes(message, text)
        return  # don't fall through to expense or commands

    # ---- Expense channel or 記帳 prefix (Feature 3) ----
    if EXPENSE_CH_ID and message.channel.id == EXPENSE_CH_ID:
        await _handle_expense(message, text)
        return

    if text.startswith("記帳 "):
        await _handle_expense(message, text[3:].strip())
        return

    await bot.process_commands(message)


async def _handle_expense(message: discord.Message, text: str):
    exp = parse_expense(text)
    if not exp:
        await message.reply("❓ 格式：`描述 金額` 或 `金額 描述`，例如：`珍奶 55`", mention_author=False)
        return

    payload = {
        "type": "expense",
        "secret": WEBHOOK_SECRET,
        "data": {
            "amount":   exp["amount"],
            "category": exp["category"],
            "note":     exp["desc"],
        },
    }
    if exp["account"]:
        payload["data"]["account"] = exp["account"]

    try:
        async with httpx.AsyncClient(timeout=5) as c:
            await c.post(f"{DASHBOARD_URL}/webhook/discord", json=payload)
    except Exception:
        pass

    acct_str = f"（{exp['account']}）" if exp["account"] else ""
    await message.reply(
        f"💰 **{exp['desc']}** NT${int(exp['amount'])} → `{exp['category']}`{acct_str}",
        mention_author=False,
    )


async def _handle_notes(message: discord.Message, text: str):
    # ---- Commands ----
    if text.startswith("搜尋 "):
        query = text[3:].strip()
        pages = await notion_search(query)
        body  = fmt_pages(pages)
        suffix = "" if (NOTION_TOKEN and NOTION_NOTES_DB) else _no_notion_warn()
        await message.reply(f"🔍 **搜尋「{query}」**\n{body}{suffix}", mention_author=False)
        return

    if text == "最近筆記":
        pages = await notion_list()
        body  = fmt_pages(pages)
        suffix = "" if (NOTION_TOKEN and NOTION_NOTES_DB) else _no_notion_warn()
        await message.reply(f"📋 **最近 10 筆筆記**\n{body}{suffix}", mention_author=False)
        return

    if text == "待辦":
        pages = await notion_list(tag_filter="待辦")
        body  = fmt_pages(pages)
        suffix = "" if (NOTION_TOKEN and NOTION_NOTES_DB) else _no_notion_warn()
        await message.reply(f"✅ **待辦事項**\n{body}{suffix}", mention_author=False)
        return

    # ---- Save note ----
    tag   = await classify_tag(text)
    title = text[:50] + ("…" if len(text) > 50 else "")
    ok    = await notion_save(title, text, tag)

    comment = await ai_note_comment(text)
    reply = f"📝 記下來了！`[{tag}]`"
    if comment:
        reply += f"\n> {comment}"
    if not ok and (NOTION_TOKEN or NOTION_NOTES_DB):
        reply += "\n⚠️ Notion 儲存失敗，請確認設定"
    elif not ok:
        reply += _no_notion_warn()

    await message.reply(reply, mention_author=False)


async def _handle_diary(message: discord.Message, text: str):
    """Save diary entry to Notion, reply with mood + reflection."""
    now   = datetime.datetime.now(TZ)
    mood  = await ai_diary_mood(text)
    emoji = _MOOD_EMOJI.get(mood, "📔")

    # Title = date + first 30 chars of entry
    preview = text[:30] + ("…" if len(text) > 30 else "")
    title   = f"{now.strftime('%Y/%m/%d')} {preview}"

    ok = await notion_diary_save(title, text, mood)

    reflection = await ai_diary_reflection(text)

    reply = f"{emoji} 日記記錄了！心情：`{mood}`"
    if reflection:
        reply += f"\n> {reflection}"
    if not ok and (NOTION_TOKEN or NOTION_DIARY_DB):
        reply += "\n⚠️ Notion 儲存失敗，請確認設定"
    elif not ok:
        reply += "\n⚠️ Notion 未設定（請填入 NOTION_TOKEN 和 NOTION_DIARY_DB_ID）"

    await message.reply(reply, mention_author=False)


# ============================================================
if __name__ == "__main__":
    bot.run(TOKEN)
