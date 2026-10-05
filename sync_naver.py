from pathlib import Path
import calendar
import feedparser
import requests
import hashlib
import html
import json
import re
import time

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from markdownify import markdownify as md


BLOG_ID = "4-fire"
RSS_URL = f"https://rss.blog.naver.com/{BLOG_ID}.xml"
TIMEZONE = ZoneInfo("Australia/Sydney")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/153.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}


def clean_rss_html(value):
    if not value:
        return ""

    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"</p\s*>", "\n\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", "", value)

    return html.unescape(value).strip()


def clean_markdown(text):
    if not text:
        return ""

    # zero-width / NBSP 정리
    text = text.replace("\u200b", "")
    text = text.replace("\xa0", " ")

    # 지나치게 많은 빈 줄 제거
    text = re.sub(r"\n[ \t]+\n", "\n\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def extract_log_no(link):
    # PostView.naver?...&logNo=123
    match = re.search(r"[?&]logNo=(\d+)", link)
    if match:
        return match.group(1)

    # blog.naver.com/4-fire/123456789
    match = re.search(
        rf"/{re.escape(BLOG_ID)}/(\d+)",
        link
    )
    if match:
        return match.group(1)

    return None


def fetch_full_post(log_no):
    """
    공개 모바일 PostView 페이지에서 본문을 가져온다.
    성공 시 Markdown 반환.
    실패하면 예외 발생.
    """

    url = (
        "https://m.blog.naver.com/PostView.naver"
        f"?blogId={BLOG_ID}&logNo={log_no}"
    )

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=30
    )

    response.raise_for_status()

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    # SmartEditor ONE / 최신 에디터
    body = soup.select_one(".se-main-container")

    # 구형 SmartEditor fallback
    if body is None:
        body = soup.select_one(".se3_view")

    # 추가 fallback
    if body is None:
        body = soup.select_one("#postViewArea")

    if body is None:
        raise RuntimeError(
            "Could not find Naver post body"
        )

    # 불필요한 요소 제거
    for tag in body.select(
        "script, style, noscript, button"
    ):
        tag.decompose()

    # lazy-loading 이미지 URL 정상화
    for img in body.find_all("img"):
        src = (
            img.get("data-lazy-src")
            or img.get("data-src")
            or img.get("src")
        )

        if src:
            if src.startswith("//"):
                src = "https:" + src

            img["src"] = src

    markdown = md(
        str(body),
        heading_style="ATX",
        bullets="-"
    )

    markdown = clean_markdown(markdown)

    if len(markdown) < 100:
        raise RuntimeError(
            f"Extracted content too short: {len(markdown)} chars"
        )

    return markdown


def load_existing_index():
    path = Path("index.json")

    if not path.exists():
        return {}

    try:
        data = json.loads(
            path.read_text(encoding="utf-8")
        )
    except Exception:
        return {}

    result = {}

    for item in data.get("posts", []):
        key = (
            item.get("logNo")
            or item.get("source")
            or item.get("archive")
        )

        if key:
            result[str(key)] = item

    return result


# --------------------------------------------------
# RSS 다운로드
# --------------------------------------------------

response = requests.get(
    RSS_URL,
    headers=HEADERS,
    timeout=30
)

response.raise_for_status()

# RSS 원본 자체도 보관
Path("feed.xml").write_bytes(response.content)

feed = feedparser.parse(response.content)

if feed.bozo and not feed.entries:
    raise RuntimeError(
        f"RSS parsing failed: {feed.bozo_exception}"
    )


# --------------------------------------------------
# 저장 디렉터리 / 기존 index
# --------------------------------------------------

posts_dir = Path("posts")
posts_dir.mkdir(exist_ok=True)

index_map = load_existing_index()

new_full_posts = 0
rss_fallback_posts = 0


# --------------------------------------------------
# RSS 게시물 처리
# --------------------------------------------------

for entry in feed.entries:

    title = entry.get(
        "title",
        "Untitled"
    ).strip()

    link = entry.get(
        "link",
        ""
    ).strip()

    log_no = extract_log_no(link)

    if entry.get("published_parsed"):

        timestamp = calendar.timegm(
            entry.published_parsed
        )

        published_dt = datetime.fromtimestamp(
            timestamp,
            tz=timezone.utc
        ).astimezone(TIMEZONE)

        date_string = published_dt.strftime(
            "%Y-%m-%d"
        )

        published_string = (
            published_dt.isoformat()
        )

    else:
        date_string = "unknown-date"
        published_string = ""

    # --------------------------------------------------
    # 새 파일명
    # --------------------------------------------------

    if log_no:
        filename = (
            f"{date_string}-{log_no}.md"
        )
    else:
        unique_id = hashlib.sha256(
            link.encode("utf-8")
        ).hexdigest()[:12]
    year = (
        date_string[:4]
        if date_string != "unknown-date"
        else "unknown"
    )
    
    year_dir = posts_dir / year
    year_dir.mkdir(
        parents=True,
        exist_ok=True
    )
        filename = (
            f"{date_string}-{unique_id}.md"
        )

    filepath = year_dir / filename

    # --------------------------------------------------
    # 이전 버전의 hash 파일이 있으면 제거
    # --------------------------------------------------

    old_hash = hashlib.sha256(
        link.encode("utf-8")
    ).hexdigest()[:12]

    old_filename = (
        f"{date_string}-{old_hash}.md"
    )

    old_filepath = posts_dir / old_filename

    if (
        log_no
        and old_filepath.exists()
        and old_filepath != filepath
    ):
        old_filepath.unlink()

    # --------------------------------------------------
    # 이미 전체 본문 저장이 완료된 파일이면
    # 다시 Naver에 요청하지 않는다.
    # --------------------------------------------------

    already_full = False

    if filepath.exists():
        try:
            existing_text = filepath.read_text(
                encoding="utf-8"
            )

            if (
                'content_source: "naver_post"'
                in existing_text
            ):
                already_full = True

        except Exception:
            pass
        def find_existing_post(log_no):
            matches = list(
                posts_dir.rglob(
                    f"*-{log_no}.md"
                )
            )
        
            if matches:
                return matches[0]
        
            return None
    content_source = "naver_post"

    # --------------------------------------------------
    # 전체 본문 가져오기
    # --------------------------------------------------

    if already_full:

        saved_text = filepath.read_text(
            encoding="utf-8"
        )

        body_match = re.search(
            r"---\n\n(.*)",
            saved_text,
            flags=re.S
        )

        text_content = (
            body_match.group(1).strip()
            if body_match
            else saved_text
        )

    else:

        try:

            if not log_no:
                raise RuntimeError(
                    "No logNo found"
                )

            text_content = fetch_full_post(
                log_no
            )

            new_full_posts += 1

            print(
                f"FULL: {date_string} "
                f"{log_no} {title}"
            )

            # 첫 대량 실행 시 서버에 과도한 요청 방지
            time.sleep(0.5)

        except Exception as error:

            print(
                f"FALLBACK RSS: "
                f"{title}: {error}"
            )

            raw_content = ""

            if entry.get("content"):
                raw_content = (
                    entry.content[0]
                    .get("value", "")
                )

            if not raw_content:
                raw_content = entry.get(
                    "summary",
                    entry.get(
                        "description",
                        ""
                    )
                )

            text_content = clean_rss_html(
                raw_content
            )

            content_source = "rss_summary"
            rss_fallback_posts += 1

    # --------------------------------------------------
    # Markdown front matter
    # --------------------------------------------------

    title_yaml = json.dumps(
        title,
        ensure_ascii=False
    )

    source_yaml = json.dumps(
        link,
        ensure_ascii=False
    )

    log_no_yaml = (
        json.dumps(str(log_no))
        if log_no
        else "null"
    )

    markdown = f"""---
title: {title_yaml}
date: "{date_string}"
published: "{published_string}"
blog_id: "{BLOG_ID}"
logNo: {log_no_yaml}
source: {source_yaml}
content_source: "{content_source}"
---

# {title}

Published: {published_string}

Original Naver post: {link}

---

{text_content}
"""

    filepath.write_text(
        markdown,
        encoding="utf-8"
    )

    # --------------------------------------------------
    # index 업데이트
    # --------------------------------------------------

    key = (
        str(log_no)
        if log_no
        else link
    )

    index_map[key] = {
        "date": date_string,
        "published": published_string,
        "title": title,
        "logNo": log_no,
        "source": link,
        "archive": f"posts/{year}/{filename}",
        "content_source": content_source,
        "characters": len(text_content)
    }


# --------------------------------------------------
# 과거 index 포함 전체 정렬
# --------------------------------------------------

index = list(index_map.values())

index.sort(
    key=lambda item: item.get(
        "published",
        ""
    ),
    reverse=True
)

output = {
    "blog": "4FIRE",
    "blogId": BLOG_ID,
    "rss": RSS_URL,
    "totalArchivedPosts": len(index),
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

print()
print(
    f"Total archived: {len(index)}"
)

print(
    f"New full posts: {new_full_posts}"
)

print(
    f"RSS fallback: {rss_fallback_posts}"
)
