import os
from datetime import date
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
COMBINED_SCOPES = SCOPES + ["https://www.googleapis.com/auth/calendar.readonly"]


def _get_creds(token_path: str, scopes: list) -> Credentials | None:
    creds = None
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, scopes)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(token_path, "w") as f:
                f.write(creds.to_json())
        else:
            return None
    return creds


def get_service(credentials_path: str, token_path: str):
    creds = _get_creds(token_path, SCOPES)
    if not creds:
        raise RuntimeError("Gmail token 不存在或失效，請執行 python setup_gmail.py")
    return build("gmail", "v1", credentials=creds)


def get_important_unread(credentials_path: str, token_path: str, max_results: int = 5) -> list[dict]:
    """回傳今日未讀重要郵件，每筆含 subject 和 from。"""
    service   = get_service(credentials_path, token_path)
    today_str = date.today().strftime("%Y/%m/%d")

    result = service.users().messages().list(
        userId="me",
        q=f"is:unread is:important after:{today_str}",
        maxResults=max_results
    ).execute()

    emails = []
    for msg in result.get("messages", []):
        detail = service.users().messages().get(
            userId="me", id=msg["id"],
            format="metadata",
            metadataHeaders=["Subject", "From"]
        ).execute()
        headers = {h["name"]: h["value"] for h in detail["payload"]["headers"]}
        sender  = headers.get("From", "（未知）")
        if "<" in sender:
            sender = sender.split("<")[0].strip().strip('"').strip()
        emails.append({
            "subject": headers.get("Subject", "（無主旨）"),
            "from":    sender or "（未知）",
        })
    return emails


def get_today_events(credentials_path: str, token_path: str) -> str:
    """回傳今日 Google Calendar 主行事曆的行程，格式化為字串。
    需要重新執行 setup_gmail.py（含 Calendar scope）後才能使用。
    """
    try:
        creds = _get_creds(token_path, COMBINED_SCOPES)
        if not creds:
            return "（Calendar 未授權，請刪除 token.json 並重新執行 setup_gmail.py）"

        cal   = build("calendar", "v3", credentials=creds)
        today = date.today().isoformat()
        result = cal.events().list(
            calendarId="primary",
            timeMin=f"{today}T00:00:00+08:00",
            timeMax=f"{today}T23:59:59+08:00",
            singleEvents=True,
            orderBy="startTime",
            maxResults=10,
        ).execute()

        events = result.get("items", [])
        if not events:
            return "今天沒有行程"

        lines = []
        for ev in events:
            summary = ev.get("summary", "（無標題）")
            start   = ev["start"].get("dateTime", ev["start"].get("date", ""))
            time_str = start[11:16] if "T" in start else "全天"
            lines.append(f"{time_str} {summary}")
        return "、".join(lines)

    except Exception:
        return "（Calendar 未設定，請刪除 token.json 並重新執行 setup_gmail.py）"
