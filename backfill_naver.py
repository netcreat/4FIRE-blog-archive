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


# ==========================================================
# CONFIG
# ==========================================================

BLOG_ID = "4-fire"

LIST_URL = (
    f"https://m.blog.naver.com/api/blogs/"
    f"{BLOG_ID}/post-list"
)

POST_URL = (
    "https://m.blog.naver.com/PostView.naver"
)

POSTS_DIR = Path("posts")
INDEX_PATH = Path("index.json")
FAILURE_PATH = Path("backfill_failures.json")

KST = ZoneInfo("Asia/Seoul")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/153.0 Safari/537.36"
    ),
    "Accept-Language": (
        "ko-KR,ko;q=0.9,en;q=0.8"
    ),
    "Referer": (
        "https://m.blog.naver.com/"
    ),
}

POSTS_DIR.mkdir(
    parents=True,
    exist_ok=True,
)


# ==========================================================
# HTTP REQUEST WITH RETRY
# ==========================================================

def request_with_retry(
    url,
    params=None,
    attempts=4,
):

    last_error = None

    for attempt in range(attempts):

        try:

            response = requests.get(
                url,
                params=params,
                headers=HEADERS,
                timeout=30,
            )

            if (
                response.status_code == 429
                or response.status_code >= 500
            ):

                raise RuntimeError(
                    f"HTTP "
                    f"{response.status_code}"
                )

            response.raise_for_status()

            return response

        except Exception as error:

            last_error = error

            if attempt == attempts - 1:
                break

            wait = 2 ** attempt

            print(
                f"Retry "
                f"{attempt + 1}/"
                f"{attempts}: "
                f"{error}"
            )

            print(
                f"Waiting {wait}s..."
            )

            time.sleep(wait)

    raise RuntimeError(
        str(last_error)
    )


# ==========================================================
# FIND POST-LIST ITEMS
# ==========================================================

def find_items(obj):

    if isinstance(obj, dict):

        value = obj.get(
            "items"
        )

        if isinstance(
            value,
            list,
        ):

            if not value:
                return value

            if any(
                isinstance(item, dict)
                and "logNo" in item
                for item in value
            ):

                return value

        for value in obj.values():

            found = find_items(
                value
            )

            if found is not None:
                return found

    elif isinstance(obj, list):

        for value in obj:

            found = find_items(
                value
            )

            if found is not None:
                return found

    return None


# ==========================================================
# CLEAN TITLE
# ==========================================================

def clean_title(value):

    if not value:
        return "Untitled"

    soup = BeautifulSoup(
        str(value),
        "html.parser",
    )

    return html.unescape(
        soup.get_text(
            " ",
            strip=True,
        )
    )


# ==========================================================
# DATE
# ==========================================================

def parse_add_date(value):

    if value is None:

        return (
            "unknown-date",
            "",
        )

    # ------------------------------------------------------
    # Epoch seconds / milliseconds
    # ------------------------------------------------------

    try:

        number = int(
            str(value)
        )

        if number > 10_000_000_000:

            number = (
                number / 1000
            )

        dt = datetime.fromtimestamp(
            number,
            tz=timezone.utc,
        ).astimezone(
            KST
        )

        return (
            dt.strftime(
                "%Y-%m-%d"
            ),
            dt.isoformat(),
        )

    except Exception:
        pass

    # ------------------------------------------------------
    # String date fallback
    # ------------------------------------------------------

    text = str(
        value
    ).strip()

    possible_formats = [
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y.%m.%d. %H:%M",
        "%Y.%m.%d.",
        "%Y-%m-%d",
    ]

    for date_format in possible_formats:

        try:

            dt = datetime.strptime(
                text,
                date_format,
            )

            dt = dt.replace(
                tzinfo=KST
            )

            return (
                dt.strftime(
                    "%Y-%m-%d"
                ),
                dt.isoformat(),
            )

        except Exception:
            pass

    return (
        "unknown-date",
        text,
    )


# ==========================================================
# CLEAN MARKDOWN
# ==========================================================

def clean_markdown(text):

    if not text:
        return ""

    text = text.replace(
        "\u200b",
        "",
    )

    text = text.replace(
        "\xa0",
        " ",
    )

    text = re.sub(
        r"\n[ \t]+\n",
        "\n\n",
        text,
    )

    text = re.sub(
        r"\n{3,}",
        "\n\n",
        text,
    )

    return text.strip()


# ==========================================================
# VIDEO DETECTION
# ==========================================================

def extract_video_markdown(body):

    video_blocks = []

    selectors = [
        ".se-module-video",
        ".se-component-video",
        "[class*='video']",
    ]

    elements = []

    seen_html = set()

    for selector in selectors:

        for element in body.select(
            selector
        ):

            marker = str(
                element
            )

            if marker in seen_html:
                continue

            seen_html.add(
                marker
            )

            elements.append(
                element
            )

    for element in elements:

        parts = []

        # --------------------------------------------------
        # Thumbnail
        # --------------------------------------------------

        img = element.find(
            "img"
        )

        if img:

            image_url = (
                img.get(
                    "data-lazy-src"
                )
                or img.get(
                    "data-src"
                )
                or img.get(
                    "src"
                )
            )

            if image_url:

                if image_url.startswith(
                    "//"
                ):

                    image_url = (
                        "https:"
                        + image_url
                    )

                parts.append(
                    "![Naver video thumbnail]"
                    f"({image_url})"
                )

        # --------------------------------------------------
        # Native video
        # --------------------------------------------------

        media_url = None

        video = element.find(
            "video"
        )

        if video:

            media_url = (
                video.get("src")
                or video.get(
                    "data-src"
                )
            )

            if not media_url:

                source = video.find(
                    "source"
                )

                if source:

                    media_url = (
                        source.get(
                            "src"
                        )
                    )

        # --------------------------------------------------
        # iframe
        # --------------------------------------------------

        if not media_url:

            iframe = element.find(
                "iframe"
            )

            if iframe:

                media_url = (
                    iframe.get(
                        "src"
                    )
                )

        if media_url:

            if media_url.startswith(
                "//"
            ):

                media_url = (
                    "https:"
                    + media_url
                )

            parts.append(
                "[Embedded Naver video]"
                f"({media_url})"
            )

        # --------------------------------------------------
        # Video component exists,
        # but URL is dynamically generated
        # --------------------------------------------------

        if not parts:

            parts.append(
                "Naver video embedded "
                "in this post."
            )

        video_blocks.append(
            "\n\n".join(
                parts
            )
        )

    # ------------------------------------------------------
    # data-module fallback
    # ------------------------------------------------------

    if not video_blocks:

        for element in body.find_all(
            attrs={
                "data-module": True
            }
        ):

            raw = element.get(
                "data-module",
                "",
            )

            if "video" in raw.lower():

                video_blocks.append(
                    "Naver video embedded "
                    "in this post."
                )

                break

    if not video_blocks:
        return ""

    return (
        "## Video\n\n"
        + "\n\n".join(
            video_blocks
        )
    )


# ==========================================================
# FETCH FULL NAVER POST
# ==========================================================

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
        "html.parser",
    )

    # ------------------------------------------------------
    # SmartEditor ONE
    # ------------------------------------------------------

    body = soup.select_one(
        ".se-main-container"
    )

    # ------------------------------------------------------
    # Old SmartEditor
    # ------------------------------------------------------

    if body is None:

        body = soup.select_one(
            ".se3_view"
        )

    # ------------------------------------------------------
    # Older posts
    # ------------------------------------------------------

    if body is None:

        body = soup.select_one(
            "#postViewArea"
        )

    if body is None:

        raise RuntimeError(
            "Could not find "
            "Naver post body"
        )

    # ------------------------------------------------------
    # Normalize image URLs
    # ------------------------------------------------------

    for img in body.find_all(
        "img"
    ):

        src = (
            img.get(
                "data-lazy-src"
            )
            or img.get(
                "data-src"
            )
            or img.get(
                "src"
            )
        )

        if src:

            if src.startswith(
                "//"
            ):

                src = (
                    "https:"
                    + src
                )

            img["src"] = src

    # ------------------------------------------------------
    # Detect video before conversion
    # ------------------------------------------------------

    video_markdown = (
        extract_video_markdown(
            body
        )
    )

    # ------------------------------------------------------
    # Remove unnecessary HTML
    # ------------------------------------------------------

    for tag in body.select(
        "script, style, "
        "noscript, button"
    ):

        tag.decompose()

    # ------------------------------------------------------
    # HTML -> Markdown
    # ------------------------------------------------------

    markdown = md(
        str(body),
        heading_style="ATX",
        bullets="-",
    )

    markdown = clean_markdown(
        markdown
    )

    # ------------------------------------------------------
    # Add video information
    # ------------------------------------------------------

    if video_markdown:

        if markdown:
            markdown += "\n\n"

        markdown += (
            video_markdown
        )

    markdown = clean_markdown(
        markdown
    )

    # ------------------------------------------------------
    # Video-only / empty-content post
    #
    # Do NOT fail.
    # ------------------------------------------------------

    if not markdown:

        markdown = (
            "_This post contains no "
            "extractable text or image "
            "content._"
        )

    return markdown


# ==========================================================
# LOAD EXISTING INDEX
# ==========================================================

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

    for item in data.get(
        "posts",
        [],
    ):

        log_no = item.get(
            "logNo"
        )

        if log_no:

            result[
                str(log_no)
            ] = item

    return result


# ==========================================================
# BUILD EXISTING FILE MAP
#
# Scan the entire posts tree ONCE.
# ==========================================================

def build_existing_post_map():

    result = {}

    for path in POSTS_DIR.rglob(
        "*.md"
    ):

        match = re.search(
            r"-(\d+)\.md$",
            path.name,
        )

        if not match:
            continue

        log_no = match.group(
            1
        )

        result[
            log_no
        ] = path

    return result


# ==========================================================
# FULL-POST CHECK
# ==========================================================

def is_full_archive(path):

    if (
        path is None
        or not path.exists()
    ):

        return False

    try:

        # Only front matter needs to be inspected.
        with path.open(
            "r",
            encoding="utf-8",
        ) as file:

            head = file.read(
                3000
            )

        return (
            'content_source: "naver_post"'
            in head
        )

    except Exception:

        return False


# ==========================================================
# BODY CHARACTER COUNT
# ==========================================================

def body_character_count(path):

    try:

        text = path.read_text(
            encoding="utf-8"
        )

        parts = text.split(
            "\n---\n",
            2,
        )

        if len(parts) >= 3:

            return len(
                parts[-1]
            )

        return len(
            text
        )

    except Exception:

        return 0


# ==========================================================
# SAFE WRITE
# ==========================================================

def atomic_write(
    path,
    content,
):

    temp = path.with_suffix(
        path.suffix + ".tmp"
    )

    temp.write_text(
        content,
        encoding="utf-8",
    )

    temp.replace(
        path
    )


# ==========================================================
# GET ENTIRE BLOG POST LIST
# ==========================================================

def collect_all_post_metadata():

    all_posts = []

    seen = set()

    page = 1

    while True:

        print(
            f"Reading post list "
            f"page {page}..."
        )

        response = (
            request_with_retry(
                LIST_URL,
                params={
                    "categoryNo": 0,
                    "itemCount": 30,
                    "page": page,
                },
            )
        )

        data = response.json()

        items = find_items(
            data
        )

        if items is None:

            raise RuntimeError(
                "Could not find items "
                "in Naver post-list "
                "response"
            )

        if not items:

            print(
                "Empty page reached."
            )

            print(
                "Post list complete."
            )

            break

        new_on_page = 0

        for item in items:

            log_no = str(
                item.get(
                    "logNo",
                    "",
                )
            ).strip()

            if not log_no:
                continue

            if log_no in seen:
                continue

            seen.add(
                log_no
            )

            new_on_page += 1

            # ----------------------------------------------
            # Exclude "this day years ago" cards
            # ----------------------------------------------

            if item.get(
                "thisDayPostInfo"
            ):

                continue

            all_posts.append(
                item
            )

        print(
            f"  total discovered: "
            f"{len(all_posts)}"
        )

        # Prevent infinite loop if Naver
        # repeats the same page
        if new_on_page == 0:

            print(
                "No new logNo found."
            )

            print(
                "Stopping pagination."
            )

            break

        page += 1

        # Gentle list-page delay
        time.sleep(
            0.25
        )

    return all_posts


# ==========================================================
# MAIN
# ==========================================================

print()
print(
    "=" * 60
)

print(
    "4FIRE NAVER BLOG BACKFILL"
)

print(
    "=" * 60
)

print()


# ==========================================================
# 1. COLLECT FULL POST LIST
# ==========================================================

metadata = (
    collect_all_post_metadata()
)

print()
print(
    f"Discovered "
    f"{len(metadata)} "
    f"posts."
)

print()


# Oldest -> newest
metadata.reverse()


# ==========================================================
# 2. LOAD EXISTING ARCHIVE
# ==========================================================

index_map = load_index()

existing_posts = (
    build_existing_post_map()
)

print(
    f"Existing archive files: "
    f"{len(existing_posts)}"
)

print()


failures = []

saved = 0
skipped = 0
moved = 0


# ==========================================================
# 3. PROCESS EVERY POST
# ==========================================================

for number, item in enumerate(
    metadata,
    start=1,
):

    log_no = str(
        item.get(
            "logNo",
            "",
        )
    ).strip()

    if not log_no:
        continue

    # ------------------------------------------------------
    # TITLE
    # ------------------------------------------------------

    title = clean_title(
        item.get(
            "titleWithInspectMessage"
        )
        or item.get(
            "title"
        )
        or "Untitled"
    )

    # ------------------------------------------------------
    # DATE
    # ------------------------------------------------------

    date_string, published_string = (
        parse_add_date(
            item.get(
                "addDate"
            )
            or item.get(
                "createdDate"
            )
        )
    )

    # ------------------------------------------------------
    # SOURCE
    # ------------------------------------------------------

    source = (
        f"https://blog.naver.com/"
        f"{BLOG_ID}/{log_no}"
    )

    # ------------------------------------------------------
    # YEAR DIRECTORY
    # ------------------------------------------------------

    year = (
        date_string[:4]
        if date_string
        != "unknown-date"
        else "unknown"
    )

    year_dir = (
        POSTS_DIR / year
    )

    year_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    desired_path = (
        year_dir
        / f"{date_string}-"
        f"{log_no}.md"
    )

    existing = (
        existing_posts.get(
            log_no
        )
    )

    print(
        f"[{number}/"
        f"{len(metadata)}] "
        f"{date_string} "
        f"{title}"
    )

    # ======================================================
    # 3A. EXISTING FULL POST
    # ======================================================

    if (
        existing
        and is_full_archive(
            existing
        )
    ):

        # --------------------------------------------------
        # Existing file is still flat?
        # Move it into the correct year directory.
        # --------------------------------------------------

        if existing != desired_path:

            # Move only when destination does not exist
            if not desired_path.exists():

                existing.rename(
                    desired_path
                )

                print(
                    f"  MOVE: "
                    f"{existing}"
                    f" -> "
                    f"{desired_path}"
                )

                moved += 1

                existing = (
                    desired_path
                )

            else:

                # Destination already exists.
                # Prefer hierarchical file.
                if (
                    existing.parent
                    == POSTS_DIR
                ):

                    existing.unlink()

                    print(
                        "  REMOVE DUPLICATE "
                        f"FLAT FILE: "
                        f"{existing.name}"
                    )

                existing = (
                    desired_path
                )

        print(
            "  SKIP: "
            "already archived"
        )

        skipped += 1

        existing_posts[
            log_no
        ] = existing

        index_map[
            log_no
        ] = {
            "date":
                date_string,

            "published":
                published_string,

            "title":
                title,

            "logNo":
                log_no,

            "source":
                source,

            "archive":
                existing.as_posix(),

            "content_source":
                "naver_post",

            "characters":
                body_character_count(
                    existing
                ),
        }

        continue


    # ======================================================
    # 3B. DOWNLOAD MISSING / INCOMPLETE POST
    # ======================================================

    try:

        content = (
            fetch_full_post(
                log_no
            )
        )

        title_yaml = (
            json.dumps(
                title,
                ensure_ascii=False,
            )
        )

        source_yaml = (
            json.dumps(
                source,
                ensure_ascii=False,
            )
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
            desired_path,
            markdown,
        )

        # --------------------------------------------------
        # Remove old flat duplicate if it existed
        # --------------------------------------------------

        if (
            existing
            and existing.exists()
            and existing
            != desired_path
        ):

            existing.unlink()

            print(
                "  REMOVE OLD: "
                f"{existing}"
            )

        existing_posts[
            log_no
        ] = desired_path

        index_map[
            log_no
        ] = {
            "date":
                date_string,

            "published":
                published_string,

            "title":
                title,

            "logNo":
                log_no,

            "source":
                source,

            "archive":
                desired_path.as_posix(),

            "content_source":
                "naver_post",

            "characters":
                len(content),
        }

        saved += 1

        print(
            f"  SAVED: "
            f"{desired_path} "
            f"({len(content):,} chars)"
        )

    except Exception as error:

        print(
            f"  FAILED: "
            f"{error}"
        )

        failures.append({
            "logNo":
                log_no,

            "date":
                date_string,

            "title":
                title,

            "source":
                source,

            "error":
                str(error),
        })

    # ------------------------------------------------------
    # Gentle delay only after a real post request
    # ------------------------------------------------------

    time.sleep(
        0.7
    )


# ==========================================================
# 4. REBUILD INDEX
# ==========================================================

index = list(
    index_map.values()
)

index.sort(
    key=lambda item:
        item.get(
            "published",
            "",
        ),
    reverse=True,
)


output = {
    "blog":
        "4FIRE",

    "blogId":
        BLOG_ID,

    "totalArchivedPosts":
        len(index),

    "backfillFailures":
        len(failures),

    "posts":
        index,
}


atomic_write(
    INDEX_PATH,
    json.dumps(
        output,
        ensure_ascii=False,
        indent=2,
    ),
)


# ==========================================================
# 5. SAVE FAILURE LIST
# ==========================================================

atomic_write(
    FAILURE_PATH,
    json.dumps(
        failures,
        ensure_ascii=False,
        indent=2,
    ),
)


# ==========================================================
# 6. FINAL SUMMARY
# ==========================================================

print()
print(
    "=" * 60
)

print(
    "BACKFILL COMPLETE"
)

print(
    "=" * 60
)

print(
    f"Discovered       : "
    f"{len(metadata)}"
)

print(
    f"Already archived : "
    f"{skipped}"
)

print(
    f"Moved to year dir: "
    f"{moved}"
)

print(
    f"New saved        : "
    f"{saved}"
)

print(
    f"Failures         : "
    f"{len(failures)}"
)

print(
    f"Index total      : "
    f"{len(index)}"
)

print(
    "=" * 60
)
