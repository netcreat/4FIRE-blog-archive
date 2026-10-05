from pathlib import Path
import calendar
import feedparser
import requests
import hashlib
import html
import json
import re

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


RSS_URL = "https://rss.blog.naver.com/4-fire.xml"
TIMEZONE = ZoneInfo("Australia/Sydney")

HEADERS = {
    "User-Agent": "4fire-blog-archive/1.0"
}


def clean_html(value):
    if not value:
        return ""

    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"</p\s*>", "\n\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", "", value)

    return html.unescape(value).strip()


response = requests.get(
    RSS_URL,
    headers=HEADERS,
    timeout=30
)

response.raise_for_status()

# RSS 원본도 그대로 보관
Path("feed.xml").write_bytes(response.content)

feed = feedparser.parse(response.content)

if feed.bozo and not feed.entries:
    raise RuntimeError(
        f"RSS parsing failed: {feed.bozo_exception}"
    )

posts_dir = Path("posts")
posts_dir.mkdir(exist_ok=True)

index = []

for entry in feed.entries:

    title = entry.get("title", "Untitled").strip()
    link = entry.get("link", "").strip()

    if entry.get("published_parsed"):
        timestamp = calendar.timegm(entry.published_parsed)

        published_dt = datetime.fromtimestamp(
            timestamp,
            tz=timezone.utc
        ).astimezone(TIMEZONE)

        date_string = published_dt.strftime("%Y-%m-%d")
        published_string = published_dt.isoformat()

    else:
        date_string = "unknown-date"
        published_string = ""

    # URL을 기준으로 중복되지 않는 파일명 생성
    unique_id = hashlib.sha256(
        link.encode("utf-8")
    ).hexdigest()[:12]

    filename = f"{date_string}-{unique_id}.md"

    raw_content = ""

    # RSS가 content 필드를 제공하면 우선 사용
    if entry.get("content"):
        raw_content = entry.content[0].get("value", "")

    # 없으면 summary/description 사용
    if not raw_content:
        raw_content = entry.get(
            "summary",
            entry.get("description", "")
        )

    text_content = clean_html(raw_content)

    markdown = f"""# {title}

Published: {published_string}

Original Naver post: {link}

---

{text_content}
"""

    filepath = posts_dir / filename
    filepath.write_text(
        markdown,
        encoding="utf-8"
    )

    index.append({
        "date": date_string,
        "published": published_string,
        "title": title,
        "source": link,
        "archive": f"posts/{filename}",
        "characters": len(text_content)
    })


# 최신 글부터 정렬
index.sort(
    key=lambda item: item["published"],
    reverse=True
)

output = {
    "blog": "4FIRE",
    "rss": RSS_URL,
    "posts": index
}

Path("index.json").write_text(
    json.dumps(
        output,
        ensure_ascii=False,
        indent=2
    ),
    encoding="utf-8"
)

print(f"Saved {len(index)} posts.")
