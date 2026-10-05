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


# ==========================================================
# CONFIG
# ==========================================================

BLOG_ID = "4-fire"

RSS_URL = (
    f"https://rss.blog.naver.com/"
    f"{BLOG_ID}.xml"
)

TIMEZONE = ZoneInfo(
    "Australia/Sydney"
)

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
}


# ==========================================================
# BASIC CLEANING
# ==========================================================

def clean_rss_html(value):

    if not value:
        return ""

    value = re.sub(
        r"<br\s*/?>",
        "\n",
        value,
        flags=re.I,
    )

    value = re.sub(
        r"</p\s*>",
        "\n\n",
        value,
        flags=re.I,
    )

    value = re.sub(
        r"<[^>]+>",
        "",
        value,
    )

    return html.unescape(
        value
    ).strip()


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
# NAVER LOG NUMBER
# ==========================================================

def extract_log_no(link):

    # PostView.naver?...&logNo=123
    match = re.search(
        r"[?&]logNo=(\d+)",
        link,
    )

    if match:
        return match.group(1)

    # blog.naver.com/4-fire/123456789
    match = re.search(
        rf"/{re.escape(BLOG_ID)}/(\d+)",
        link,
    )

    if match:
        return match.group(1)

    return None


# ==========================================================
# VIDEO DETECTION
# ==========================================================

def extract_video_markdown(body):

    blocks = []

    selectors = [
        ".se-module-video",
        ".se-component-video",
        "[class*='video']",
    ]

    elements = []
    seen = set()

    for selector in selectors:

        for element in body.select(
            selector
        ):

            element_id = id(element)

            if element_id in seen:
                continue

            seen.add(element_id)
            elements.append(element)

    for element in elements:

        parts = []

        # ----------------------------------------------
        # thumbnail
        # ----------------------------------------------

        img = element.find("img")

        if img:

            src = (
                img.get("data-lazy-src")
                or img.get("data-src")
                or img.get("src")
            )

            if src:

                if src.startswith("//"):
                    src = "https:" + src

                parts.append(
                    f"![Naver video thumbnail]"
                    f"({src})"
                )

        # ----------------------------------------------
        # <video>
        # ----------------------------------------------

        media_url = None

        video = element.find(
            "video"
        )

        if video:

            media_url = (
                video.get("src")
                or video.get("data-src")
            )

            if not media_url:

                source = video.find(
                    "source"
                )

                if source:
                    media_url = (
                        source.get("src")
                    )

        # ----------------------------------------------
        # iframe
        # ----------------------------------------------

        if not media_url:

            iframe = element.find(
                "iframe"
            )

            if iframe:
                media_url = (
                    iframe.get("src")
                )

        if media_url:

            if media_url.startswith("//"):
                media_url = (
                    "https:" + media_url
                )

            parts.append(
                f"[Embedded Naver video]"
                f"({media_url})"
            )

        # ----------------------------------------------
        # video exists but URL unavailable
        # ----------------------------------------------

        if not parts:

            parts.append(
                "Naver video embedded "
                "in this post."
            )

        blocks.append(
            "\n\n".join(parts)
        )

    # ----------------------------------------------
    # data-module based Naver video
    # ----------------------------------------------

    if not blocks:

        for element in body.find_all(
            attrs={"data-module": True}
        ):

            raw = element.get(
                "data-module",
                "",
            )

            if "video" in raw.lower():

                blocks.append(
                    "Naver video embedded "
                    "in this post."
                )

                break

    if not blocks:
        return ""

    return (
        "## Video\n\n"
        + "\n\n".join(blocks)
    )


# ==========================================================
# FETCH FULL NAVER POST
# ==========================================================

def fetch_full_post(log_no):

    url = (
        "https://m.blog.naver.com/"
        "PostView.naver"
        f"?blogId={BLOG_ID}"
        f"&logNo={log_no}"
    )

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=30,
    )

    response.raise_for_status()

    soup = BeautifulSoup(
        response.text,
        "html.parser",
    )

    # SmartEditor ONE
    body = soup.select_one(
        ".se-main-container"
    )

    # Old SmartEditor
    if body is None:
        body = soup.select_one(
            ".se3_view"
        )

    # Older posts
    if body is None:
        body = soup.select_one(
            "#postViewArea"
        )

    if body is None:

        raise RuntimeError(
            "Could not find "
            "Naver post body"
        )

    # ----------------------------------------------
    # remove unnecessary elements
    # ----------------------------------------------

    for tag in body.select(
        "script, style, noscript, button"
    ):
        tag.decompose()

    # ----------------------------------------------
    # normalize image URLs
    # ----------------------------------------------

    for img in body.find_all(
        "img"
    ):

        src = (
            img.get("data-lazy-src")
            or img.get("data-src")
            or img.get("src")
        )

        if src:

            if src.startswith("//"):
                src = (
                    "https:" + src
                )

            img["src"] = src

    # ----------------------------------------------
    # extract video before markdown conversion
    # ----------------------------------------------

    video_markdown = (
        extract_video_markdown(
            body
        )
    )

    # ----------------------------------------------
    # convert HTML -> Markdown
    # ----------------------------------------------

    markdown = md(
        str(body),
        heading_style="ATX",
        bullets="-",
    )

    markdown = clean_markdown(
        markdown
    )

    # ----------------------------------------------
    # append video information
    # ----------------------------------------------

    if video_markdown:

        if markdown:
            markdown += "\n\n"

        markdown += video_markdown

    markdown = clean_markdown(
        markdown
    )

    # ----------------------------------------------
    # Allow video-only posts
    # ----------------------------------------------

    if not markdown:

        markdown = (
            "_This post contains no "
            "extractable text or image content._"
        )

    return markdown


# ==========================================================
# EXISTING INDEX
# ==========================================================

def load_existing_index():

    path = Path(
        "index.json"
    )

    if not path.exists():
        return {}

    try:

        data = json.loads(
            path.read_text(
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

        key = (
            item.get("logNo")
            or item.get("source")
            or item.get("archive")
        )

        if key:

            result[
                str(key)
            ] = item

    return result


# ==========================================================
# BUILD EXISTING POST MAP
#
# Scan posts/ once instead of rglob() for every RSS item.
# Much faster when archive becomes large.
# ==========================================================

def build_existing_post_map(
    posts_dir
):

    result = {}

    for path in posts_dir.rglob(
        "*.md"
    ):

        match = re.search(
            r"-(\d+)\.md$",
            path.name,
        )

        if not match:
            continue

        log_no = (
            match.group(1)
        )

        result[
            log_no
        ] = path

    return result


# ==========================================================
# CHECK WHETHER EXISTING FILE IS FULL NAVER POST
# ==========================================================

def is_full_post(path):

    if not path:
        return False

    if not path.exists():
        return False

    try:

        # front matter is near top,
        # no need to read gigantic files repeatedly
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
# RSS DOWNLOAD
# ==========================================================

print(
    "Downloading Naver RSS..."
)

response = requests.get(
    RSS_URL,
    headers=HEADERS,
    timeout=30,
)

response.raise_for_status()

# Save original RSS
Path(
    "feed.xml"
).write_bytes(
    response.content
)

feed = feedparser.parse(
    response.content
)

if (
    feed.bozo
    and not feed.entries
):

    raise RuntimeError(
        f"RSS parsing failed: "
        f"{feed.bozo_exception}"
    )


# ==========================================================
# PREPARE ARCHIVE
# ==========================================================

posts_dir = Path(
    "posts"
)

posts_dir.mkdir(
    exist_ok=True
)

index_map = (
    load_existing_index()
)

existing_posts = (
    build_existing_post_map(
        posts_dir
    )
)

print(
    f"RSS entries: "
    f"{len(feed.entries)}"
)

print(
    f"Existing archived posts: "
    f"{len(existing_posts)}"
)

print()


new_full_posts = 0
rss_fallback_posts = 0
skipped_posts = 0


# ==========================================================
# PROCESS RSS
# ==========================================================

for entry in feed.entries:

    title = entry.get(
        "title",
        "Untitled",
    ).strip()

    link = entry.get(
        "link",
        "",
    ).strip()

    log_no = extract_log_no(
        link
    )

    # ------------------------------------------------------
    # DATE
    # ------------------------------------------------------

    if entry.get(
        "published_parsed"
    ):

        timestamp = calendar.timegm(
            entry.published_parsed
        )

        published_dt = (
            datetime.fromtimestamp(
                timestamp,
                tz=timezone.utc,
            )
            .astimezone(
                TIMEZONE
            )
        )

        date_string = (
            published_dt.strftime(
                "%Y-%m-%d"
            )
        )

        published_string = (
            published_dt.isoformat()
        )

    else:

        date_string = (
            "unknown-date"
        )

        published_string = ""


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
        posts_dir / year
    )

    year_dir.mkdir(
        parents=True,
        exist_ok=True,
    )


    # ------------------------------------------------------
    # FILE NAME
    # ------------------------------------------------------

    if log_no:

        filename = (
            f"{date_string}-"
            f"{log_no}.md"
        )

    else:

        unique_id = hashlib.sha256(
            link.encode(
                "utf-8"
            )
        ).hexdigest()[:12]

        filename = (
            f"{date_string}-"
            f"{unique_id}.md"
        )

    filepath = (
        year_dir / filename
    )

    key = (
        str(log_no)
        if log_no
        else link
    )


    # ------------------------------------------------------
    # EXISTING FULL POST -> IMMEDIATE SKIP
    # ------------------------------------------------------

    existing = None

    if log_no:

        existing = (
            existing_posts.get(
                str(log_no)
            )
        )

    if (
        existing
        and is_full_post(
            existing
        )
    ):

        previous = (
            index_map.get(
                key,
                {},
            )
        )

        index_map[
            key
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
                link,

            "archive":
                existing.as_posix(),

            "content_source":
                "naver_post",

            "characters":
                previous.get(
                    "characters",
                    0,
                ),
        }

        skipped_posts += 1

        print(
            f"SKIP: "
            f"{date_string} "
            f"{log_no} "
            f"{title}"
        )

        continue


    # ------------------------------------------------------
    # REMOVE OLD HASH-NAME FILE FROM EARLY VERSION
    # ------------------------------------------------------

    old_hash = hashlib.sha256(
        link.encode(
            "utf-8"
        )
    ).hexdigest()[:12]

    old_filename = (
        f"{date_string}-"
        f"{old_hash}.md"
    )

    for old_file in (
        posts_dir.rglob(
            old_filename
        )
    ):

        if old_file != filepath:

            print(
                f"REMOVE OLD: "
                f"{old_file}"
            )

            old_file.unlink()


    # ------------------------------------------------------
    # FETCH FULL POST
    # ------------------------------------------------------

    content_source = (
        "naver_post"
    )

    try:

        if not log_no:

            raise RuntimeError(
                "No logNo found"
            )

        text_content = (
            fetch_full_post(
                log_no
            )
        )

        new_full_posts += 1

        print(
            f"NEW: "
            f"{date_string} "
            f"{log_no} "
            f"{title}"
        )

        # Only sleep after an actual Naver PostView request
        time.sleep(
            0.5
        )

    except Exception as error:

        print(
            f"FALLBACK RSS: "
            f"{title}: "
            f"{error}"
        )

        raw_content = ""

        if entry.get(
            "content"
        ):

            raw_content = (
                entry.content[0]
                .get(
                    "value",
                    "",
                )
            )

        if not raw_content:

            raw_content = (
                entry.get(
                    "summary",
                    entry.get(
                        "description",
                        "",
                    ),
                )
            )

        text_content = (
            clean_rss_html(
                raw_content
            )
        )

        content_source = (
            "rss_summary"
        )

        rss_fallback_posts += 1


    # ------------------------------------------------------
    # FRONT MATTER
    # ------------------------------------------------------

    title_yaml = json.dumps(
        title,
        ensure_ascii=False,
    )

    source_yaml = json.dumps(
        link,
        ensure_ascii=False,
    )

    log_no_yaml = (
        json.dumps(
            str(log_no)
        )
        if log_no
        else "null"
    )


    # ------------------------------------------------------
    # MARKDOWN FILE
    # ------------------------------------------------------

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
        encoding="utf-8",
    )


    # ------------------------------------------------------
    # UPDATE MAP
    # ------------------------------------------------------

    if log_no:

        existing_posts[
            str(log_no)
        ] = filepath


    # ------------------------------------------------------
    # UPDATE INDEX
    # ------------------------------------------------------

    index_map[
        key
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
            link,

        "archive":
            filepath.as_posix(),

        "content_source":
            content_source,

        "characters":
            len(
                text_content
            ),
    }


# ==========================================================
# SORT INDEX
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


# ==========================================================
# WRITE INDEX.JSON
# ==========================================================

output = {
    "blog":
        "4FIRE",

    "blogId":
        BLOG_ID,

    "rss":
        RSS_URL,

    "totalArchivedPosts":
        len(index),

    "posts":
        index,
}


Path(
    "index.json"
).write_text(
    json.dumps(
        output,
        ensure_ascii=False,
        indent=2,
    ),
    encoding="utf-8",
)


# ==========================================================
# SUMMARY
# ==========================================================

print()
print(
    "=" * 60
)

print(
    "SYNC COMPLETE"
)

print(
    "=" * 60
)

print(
    f"RSS entries       : "
    f"{len(feed.entries)}"
)

print(
    f"Already archived  : "
    f"{skipped_posts}"
)

print(
    f"New full posts    : "
    f"{new_full_posts}"
)

print(
    f"RSS fallback      : "
    f"{rss_fallback_posts}"
)

print(
    f"Total archive     : "
    f"{len(index)}"
)
