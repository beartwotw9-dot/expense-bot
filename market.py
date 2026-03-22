import yfinance as yf

TICKERS = [
    ("VT",        "VT 全球股票"),
    ("VOO",       "VOO S&P 500"),
    ("QQQ",       "QQQ Nasdaq"),
    ("0050.TW",   "0050 台灣 50"),
    ("006208.TW", "006208 富邦台50"),
    ("^TWII",     "台灣大盤"),
    ("GLD",       "GLD 黃金"),
]


def fetch_market_data() -> list[dict]:
    """抓取所有標的行情，回傳 list of dict。"""
    results = []
    for symbol, name in TICKERS:
        try:
            hist = yf.Ticker(symbol).history(period="5d")
            if len(hist) < 2:
                results.append({"symbol": symbol, "name": name, "error": "資料不足"})
                continue
            price = float(hist["Close"].iloc[-1])
            prev  = float(hist["Close"].iloc[-2])
            chg   = price - prev
            pct   = chg / prev * 100
            results.append({
                "symbol": symbol,
                "name":   name,
                "price":  price,
                "change": chg,
                "pct":    pct,
            })
        except Exception as e:
            results.append({"symbol": symbol, "name": name, "error": str(e)})
    return results


def format_line(d: dict) -> str:
    """格式化單一標的為 Discord 顯示字串。"""
    if "error" in d:
        return f"❓ **{d['name']}**：資料取得失敗"
    icon  = "🟢" if d["pct"] >= 0 else "🔴"
    arrow = "▲" if d["pct"] >= 0 else "▼"
    sign  = "+" if d["pct"] >= 0 else ""
    price_str = f"{d['price']:,.0f}" if d["price"] > 999 else f"{d['price']:.2f}"
    return f"{icon} **{d['name']}**　`{price_str}`　{arrow}{sign}{d['pct']:.2f}%"


def rule_comment(data: list[dict]) -> str:
    """純規則產生市場摘要，不呼叫 AI。"""
    valid = [d for d in data if "error" not in d]
    if not valid:
        return "今日市場資料暫不可用"
    up  = sum(1 for d in valid if d["pct"] >= 0)
    dn  = len(valid) - up
    avg = sum(d["pct"] for d in valid) / len(valid)
    if up >= len(valid) * 0.7:
        return f"今日多數標的上漲，平均 {avg:+.2f}%"
    if dn >= len(valid) * 0.7:
        return f"今日多數標的下跌，平均 {avg:+.2f}%"
    return f"今日漲跌互見：{up} 漲 {dn} 跌，平均 {avg:+.2f}%"


# 保留向後相容，不再呼叫 AI
def get_ai_comment(gemini_client, data: list[dict]) -> str:
    return rule_comment(data)
