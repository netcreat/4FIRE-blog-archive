from pathlib import Path
import requests
import json
import re
import html
import time

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup
from markdownify import markdownify as md


BLOG_ID = "4-fire"

LIST_URL = (
    f"https://m.blog.naver.com/api/blogs/"
    f"{BLOG_ID}/post-list"
)

POST_URL = (
    "https://m.blog.naver.com/PostView.naver"
)

KST = ZoneInfo("Asia/Seoul")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/153.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Referer": "https://m.blog.naver.com/",
}

POSTS_DIR = Path("posts")
POSTS_DIR.mkdir(exist_ok=True)

INDEX_PATH = Path("index.json")
FAILURE_PATH = Path("backfill_failures.json")


def request_with_retry(url, params=None, attempts=4):
    last_error = None

    for attempt in range(attempts):
        try:
            response = requests.get(
                url,
                params=params,
                headers=HEADERS,
                timeout=30,
            )

            # 429 / 5xx는 재시도
            if response.status_code == 429 or response.status_code >= 500:
                raise RuntimeError(
                    f"HTTP {response.status_code}"
                )

            response.raise_for_status()
            return response

        except Exception as error:
            last_error = error

            if attempt == attempts - 1:
                break

            wait = 2 ** attempt

            print(
                f"Retry {attempt + 1}/{attempts}: "
                f"{error} — waiting {wait}s"
            )

            time.sleep(wait)

    raise RuntimeError(str(last_error))


def find_items(obj):
    """
    네이버 응답 envelope가 바뀌어도
    logNo를 가진 items 배열을 찾아낸다.
    """

    if isinstance(obj, dict):

        value = obj.get("items")

        if isinstance(value, list):
            if not value:
                return value

            if any(
                isinstance(item, dict)
                and "logNo" in item
                for item in value
            ):
                return value

        for value in obj.values():
            found = find_items(value)

            if found is not None:
                return found

    elif isinstance(obj, list):

        for value in obj:
            found = find_items(value)

            if found is not None:
                return found

    return None


def clean_title(value):
    if not value:
        return "Untitled"

    soup = BeautifulSoup(
        str(value),
        "html.parser"
    )

    return html.unescape(
        soup.get_text(" ", strip=True)
    )


def parse_add_date(value):
    """
    현재 addDate는 epoch milliseconds 형식.
    예상 밖 형식이면 unknown-date 반환.
    """

    if value is None:
        return "unknown-date", ""

    try:
        number = int(str(value))

        # milliseconds → seconds
        if number > 10_000_000_000:
            number = number / 1000

        dt = datetime.fromtimestamp(
            number,
            tz=timezone.utc
        ).astimezone(KST)

        return (
            dt.strftime("%Y-%m-%d"),
            dt.isoformat()
        )

    except Exception:
        return "unknown-date", str(value)


def clean_markdown(text):
    text = text.replace("\u200b", "")
    text = text.replace("\xa0", " ")

    text = re.sub(
        r"\n[ \t]+\n",
        "\n\n",
        text
    )

    text = re.sub(
        r"\n{3,}",
        "\n\n",
        text
    )

    return text.strip()


def fetch_full_post(log_no):
    response = request_with_retry(
        POST_URL,
        params={
            "blogId": BLOG_ID,
            "logNo": log_no,
        },
    )

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    # 최신 SmartEditor ONE
    body = soup.select_one(
        ".se-main-container"
    )

    # 구형 SmartEditor
    if body is None:
        body = soup.select_one(
            ".se3_view"
        )

    # 더 오래된 글 fallback
    if body is None:
        body = soup.select_one(
            "#postViewArea"
        )

    if body is None:
        raise RuntimeError(
            "Could not find post body"
        )

    for tag in body.select(
        "script, style, noscript, button"
    ):
        tag.decompose()

    # 이미지 lazy-load URL 보존
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

    if len(markdown) < 50:
        raise RuntimeError(
            f"Content too short: "
            f"{len(markdown)} chars"
        )

    return markdown


def collect_all_post_metadata():
    """
    최신 글부터 과거 글까지
    모든 공개 게시물 목록 수집.
    """

    all_posts = []
    seen = set()

    page = 1

    while True:

        print(
            f"Reading post list page {page}..."
        )

        response = request_with_retry(
            LIST_URL,
            params={
                "categoryNo": 0,
                "itemCount": 30,
                "page": page,
            },
        )

        data = response.json()

        items = find_items(data)

        if items is None:
            raise RuntimeError(
                "Could not find items "
                "in Naver post-list response"
            )

        if not items:
            print(
                "Empty page reached. "
                "Post list complete."
            )
            break

        new_on_page = 0

        for item in items:

            log_no = str(
                item.get("logNo", "")
            ).strip()

            if not log_no:
                continue

            if log_no in seen:
                continue

            seen.add(log_no)
            new_on_page += 1

            # 'N년 전 오늘' 같은 자동 노출 항목 제외
            if item.get("thisDayPostInfo"):
                continue

            all_posts.append(item)

        print(
            f"  total discovered: "
            f"{len(all_posts)}"
        )

        # 서버가 같은 페이지를 반복 반환할 경우
        if new_on_page == 0:
            print(
                "No new logNo on this page. "
                "Stopping."
            )
            break

        page += 1

        time.sleep(0.25)

    return all_posts


def find_existing_file(log_no):
    matches = list(
        POSTS_DIR.glob(
            f"*-{log_no}.md"
        )
    )

    if matches:
        return matches[0]

    return None


def is_full_archive(path):
    if not path or not path.exists():
        return False

    try:
        text = path.read_text(
            encoding="utf-8"
        )

        return (
            'content_source: "naver_post"'
            in text
        )

    except Exception:
        return False


def body_character_count(path):
    try:
        text = path.read_text(
            encoding="utf-8"
        )

        parts = text.split(
            "\n---\n",
            2
        )

        if len(parts) >= 3:
            return len(parts[-1])

        return len(text)

    except Exception:
        return 0


def atomic_write(path, content):
    temp = path.with_suffix(
        path.suffix + ".tmp"
    )

    temp.write_text(
        content,
        encoding="utf-8"
    )

    temp.replace(path)


def load_index():
    if not INDEX_PATH.exists():
        return {}

    try:
        data = json.loads(
            INDEX_PATH.read_text(
                encoding="utf-8"
            )
        )

    except Exception:
        return {}

    result = {}

    for item in data.get("posts", []):

        log_no = item.get("logNo")

        if log_no:
            result[str(log_no)] = item

    return result


# ==================================================
# 1. 전체 글 목록 취득
# ==================================================

metadata = collect_all_post_metadata()

print()
print(
    f"Discovered {len(metadata)} "
    f"real posts."
)

# 오래된 글 → 최신 글 순서
metadata.reverse()


# ==================================================
# 2. 기존 index 로드
# ==================================================

index_map = load_index()

failures = []

saved = 0
skipped = 0


# ==================================================
# 3. 전체 게시물 백필
# ==================================================

for number, item in enumerate(
    metadata,
    start=1
):

    log_no = str(
        item.get("logNo")
    )

    title = clean_title(
        item.get(
            "titleWithInspectMessage"
        )
    )

    date_string, published_string = (
        parse_add_date(
            item.get("addDate")
        )
    )

    source = (
        f"https://blog.naver.com/"
        f"{BLOG_ID}/{log_no}"
    )

    existing = find_existing_file(
        log_no
    )

    print()
    print(
        f"[{number}/{len(metadata)}] "
        f"{date_string} "
        f"{title}"
    )

    # 이미 전체 본문이 존재하면 재요청 안 함
    if is_full_archive(existing):

        print(
            f"  SKIP: already archived "
            f"{existing.name}"
        )

        skipped += 1

        index_map[log_no] = {
            "date": date_string,
            "published": published_string,
            "title": title,
            "logNo": log_no,
            "source": source,
            "archive": (
                f"posts/{existing.name}"
            ),
            "content_source": (
                "naver_post"
            ),
            "characters": (
                body_character_count(
                    existing
                )
            ),
        }

        continue

    # 기존 파일이 없다면 새 이름 사용
    filepath = (
        existing
        if existing
        else POSTS_DIR
        / f"{date_string}-{log_no}.md"
    )

    try:

        content = fetch_full_post(
            log_no
        )

        title_yaml = json.dumps(
            title,
            ensure_ascii=False
        )

        source_yaml = json.dumps(
            source,
            ensure_ascii=False
        )

        markdown = f"""---
title: {title_yaml}
date: "{date_string}"
published: "{published_string}"
blog_id: "{BLOG_ID}"
logNo: "{log_no}"
source: {source_yaml}
content_source: "naver_post"
---

# {title}

Published: {published_string}

Original Naver post: {source}

---

{content}
"""

        atomic_write(
            filepath,
            markdown
        )

        index_map[log_no] = {
            "date": date_string,
            "published": published_string,
            "title": title,
            "logNo": log_no,
            "source": source,
            "archive": (
                f"posts/{filepath.name}"
            ),
            "content_source": (
                "naver_post"
            ),
            "characters": len(content),
        }

        saved += 1

        print(
            f"  SAVED: {filepath.name} "
            f"({len(content):,} chars)"
        )

    except Exception as error:

        print(
            f"  FAILED: {error}"
        )

        failures.append({
            "logNo": log_no,
            "date": date_string,
            "title": title,
            "source": source,
            "error": str(error),
        })

    # 네이버 서버에 과도한 요청 방지
    time.sleep(0.7)


# ==================================================
# 4. index.json 재생성
# ==================================================

index = list(
    index_map.values()
)

index.sort(
    key=lambda item:
    item.get("published", "")
)

output = {
    "blog": "4FIRE",
    "blogId": BLOG_ID,
    "totalArchivedPosts": len(index),
    "backfillFailures": len(failures),
    "posts": index,
}

atomic_write(
    INDEX_PATH,
    json.dumps(
        output,
        ensure_ascii=False,
        indent=2
    )
)


# ==================================================
# 5. 실패 목록 저장
# ==================================================

atomic_write(
    FAILURE_PATH,
    json.dumps(
        failures,
        ensure_ascii=False,
        indent=2
    )
)


print()
print("=" * 60)
print("BACKFILL COMPLETE")
print("=" * 60)

print(
    f"Discovered : {len(metadata)}"
)

print(
    f"Already had: {skipped}"
)

print(
    f"New saved  : {saved}"
)

print(
    f"Failures   : {len(failures)}"
)

print(
    f"Index total: {len(index)}"
)
