#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup


# ==========================================================
# CONFIG
# ==========================================================

INDEX_PATH = Path("index.json")
MANIFEST_PATH = Path("substack_manifest.json")

STATUS_JSON_PATH = Path("substack_status.json")
STATUS_MD_PATH = Path("SUBSTACK_STATUS.md")

DEFAULT_SUBSTACK_BASE = "https://netcreat.substack.com"

RECENT_DAYS = 90

REQUEST_TIMEOUT = 30
REQUEST_RETRIES = 3

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/153.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9,ko;q=0.8",
}


# ==========================================================
# BASIC HELPERS
# ==========================================================

def load_json(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Required file not found: {path}")

    return json.loads(path.read_text(encoding="utf-8"))


def atomic_write(path: Path, content: str) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(content, encoding="utf-8")
    temp.replace(path)


def canonical_url(value: str) -> str:
    """
    Normalize a public URL for comparison:
    - remove query string
    - remove fragment
    - remove trailing slash except site root
    """
    value = (value or "").strip()
    if not value:
        return ""

    parts = urlsplit(value)

    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/")

    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            path,
            "",
            "",
        )
    )


def md_escape(value) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\n", " ").replace("\r", " ")
    text = text.replace("|", r"\|")
    return text.strip()


def parse_date(value: str | None) -> date | None:
    if not value:
        return None

    try:
        return date.fromisoformat(str(value)[:10])
    except Exception:
        return None


def status_sort_key(item: dict):
    # Newest Naver posts first.
    return (
        item["naver"].get("published_date") or "",
        item["naver"].get("logNo") or "",
    )


# ==========================================================
# HTTP
# ==========================================================

def get_html(url: str) -> str:
    last_error = None

    for attempt in range(REQUEST_RETRIES):
        try:
            response = requests.get(
                url,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            return response.text

        except Exception as exc:
            last_error = exc

            if attempt < REQUEST_RETRIES - 1:
                wait = 2 ** attempt
                print(f"Retrying {url} in {wait}s: {exc}")
                time.sleep(wait)

    raise RuntimeError(f"Could not fetch {url}: {last_error}")


# ==========================================================
# SUBSTACK PUBLIC SITEMAP
# ==========================================================

def discover_substack_posts(base_url: str) -> tuple[dict, dict]:
    """
    Returns:
      public_posts:
        canonical_url -> {
            "title": "...",
            "url": "...",
            "sitemap_year": "2026"
        }

      fetch_info:
        metadata about whether the sitemap fetch succeeded.

    Substack exposes:
      /sitemap
      /sitemap/YYYY

    This allows us to detect all PUBLIC posts without logging in.
    Drafts and scheduled/unpublished posts are intentionally invisible.
    """

    base_url = canonical_url(base_url)

    fetch_info = {
        "ok": False,
        "root_url": f"{base_url}/sitemap",
        "year_pages": [],
        "error": None,
    }

    try:
        root_html = get_html(f"{base_url}/sitemap")
        root_soup = BeautifulSoup(root_html, "html.parser")

        year_pages = []

        for anchor in root_soup.find_all("a", href=True):
            href = canonical_url(
                urljoin(
                    f"{base_url}/sitemap",
                    anchor["href"],
                )
            )

            if re.fullmatch(
                re.escape(base_url) + r"/sitemap/\d{4}",
                href,
            ):
                year_pages.append(href)

        year_pages = sorted(set(year_pages), reverse=True)

        # Fallback in case Substack changes the root sitemap HTML.
        if not year_pages:
            current_year = datetime.now(timezone.utc).year
            year_pages = [
                f"{base_url}/sitemap/{current_year}"
            ]

        public_posts = {}

        for year_url in year_pages:
            html = get_html(year_url)
            soup = BeautifulSoup(html, "html.parser")

            year_match = re.search(r"/sitemap/(\d{4})$", year_url)
            sitemap_year = year_match.group(1) if year_match else None

            for anchor in soup.find_all("a", href=True):
                href = canonical_url(
                    urljoin(year_url, anchor["href"])
                )

                if not href.startswith(f"{base_url}/p/"):
                    continue

                title = anchor.get_text(
                    " ",
                    strip=True,
                )

                public_posts[href] = {
                    "title": title or None,
                    "url": href,
                    "sitemap_year": sitemap_year,
                }

        fetch_info["ok"] = True
        fetch_info["year_pages"] = year_pages

        return public_posts, fetch_info

    except Exception as exc:
        fetch_info["error"] = str(exc)
        return {}, fetch_info


# ==========================================================
# MANIFEST
# ==========================================================

def get_manifest_mapping(manifest: dict) -> dict:
    """
    Manifest v2:

    {
      "version": 2,
      "mappings": {
        "224430550598": {
          "status": "published",
          "relationship": "one_to_one",
          "substack": [
            {
              "title": "...",
              "url": "...",
              "published_date": "2026-10-04"
            }
          ]
        },

        "224xxxx": {
          "status": "skip",
          "reason": "Naver-only short post"
        }
      }
    }

    IMPORTANT:
    A Naver logNo not present in mappings is automatically MISSING.
    """

    version = manifest.get("version")

    if version != 2:
        raise RuntimeError(
            "substack_manifest.json must use version 2. "
            "Replace it with the supplied v2 manifest."
        )

    mappings = manifest.get("mappings")

    if not isinstance(mappings, dict):
        raise RuntimeError(
            "substack_manifest.json: 'mappings' must be an object."
        )

    return mappings


# ==========================================================
# BUILD STATUS
# ==========================================================

def build_status(
    naver_index: dict,
    manifest: dict,
    public_posts: dict,
    sitemap_ok: bool,
) -> tuple[list[dict], list[dict]]:

    mappings = get_manifest_mapping(manifest)

    rows = []
    referenced_public_urls = set()

    for post in naver_index.get("posts", []):

        log_no = str(post.get("logNo") or "").strip()

        if not log_no:
            continue

        naver = {
            "published_date": post.get("date"),
            "title": post.get("title"),
            "logNo": log_no,
            "url": post.get("source")
                or f"https://blog.naver.com/4-fire/{log_no}",
            "github_archive": post.get("archive"),
        }

        mapping = mappings.get(log_no)

        # --------------------------------------------------
        # No mapping = Missing
        # --------------------------------------------------

        if not mapping:

            rows.append({
                "naver": naver,
                "status": "missing",
                "status_label": "❌ Missing",
                "relationship": None,
                "substack": [],
                "reason": None,
            })

            continue

        mapping_status = str(
            mapping.get("status") or ""
        ).lower().strip()

        # --------------------------------------------------
        # Explicit skip
        # --------------------------------------------------

        if mapping_status == "skip":

            rows.append({
                "naver": naver,
                "status": "skip",
                "status_label": "⏭ Skip",
                "relationship": "skip",
                "substack": [],
                "reason": mapping.get("reason"),
            })

            continue

        # --------------------------------------------------
        # Published mapping
        # --------------------------------------------------

        if mapping_status == "published":

            relationship = (
                mapping.get("relationship")
                or "one_to_one"
            )

            substack_items = []

            for entry in mapping.get(
                "substack",
                [],
            ):

                raw_url = entry.get("url")
                url = canonical_url(raw_url)

                if url:
                    referenced_public_urls.add(url)

                public = public_posts.get(url)

                # If sitemap fetch failed, do not downgrade
                # a known good manifest entry.
                if not sitemap_ok:
                    public_state = "unverified"

                elif public:
                    public_state = "public"

                else:
                    public_state = "not_in_public_sitemap"

                substack_items.append({
                    "title":
                        entry.get("title")
                        or (
                            public.get("title")
                            if public
                            else None
                        ),

                    "url":
                        raw_url,

                    "published_date":
                        entry.get(
                            "published_date"
                        ),

                    "part":
                        entry.get("part"),

                    "public_state":
                        public_state,
                })

            if not substack_items:

                rows.append({
                    "naver": naver,
                    "status": "check",
                    "status_label": "⚠️ Check",
                    "relationship": relationship,
                    "substack": [],
                    "reason": (
                        "Manifest says published "
                        "but has no Substack URL."
                    ),
                })

                continue

            if (
                sitemap_ok
                and any(
                    item["public_state"]
                    != "public"
                    for item in substack_items
                )
            ):

                status = "check"
                status_label = "⚠️ Check"

            elif relationship == "split":

                status = "published"
                status_label = "🔵 Split / Published"

            else:

                status = "published"
                status_label = "✅ Published"

            rows.append({
                "naver": naver,
                "status": status,
                "status_label": status_label,
                "relationship": relationship,
                "substack": substack_items,
                "reason": mapping.get("reason"),
            })

            continue

        # --------------------------------------------------
        # Unknown status in manifest
        # --------------------------------------------------

        rows.append({
            "naver": naver,
            "status": "check",
            "status_label": "⚠️ Check",
            "relationship": mapping.get(
                "relationship"
            ),
            "substack": mapping.get(
                "substack",
                [],
            ),
            "reason": (
                "Unknown manifest status: "
                f"{mapping_status!r}"
            ),
        })

    rows.sort(
        key=status_sort_key,
        reverse=True,
    )

    # ------------------------------------------------------
    # Public Substack posts that are not mapped to any
    # Naver logNo.
    # ------------------------------------------------------

    unmatched_substack = []

    if sitemap_ok:

        for url, item in sorted(
            public_posts.items(),
            key=lambda kv: (
                kv[1].get("sitemap_year") or "",
                kv[1].get("title") or "",
            ),
            reverse=True,
        ):

            if url in referenced_public_urls:
                continue

            unmatched_substack.append(
                item
            )

    return rows, unmatched_substack


# ==========================================================
# SUMMARY
# ==========================================================

def count_status(rows: list[dict]) -> dict:
    result = {
        "total": len(rows),
        "published": 0,
        "missing": 0,
        "skip": 0,
        "check": 0,
    }

    for row in rows:
        status = row.get("status")

        if status in result:
            result[status] += 1

    return result


def get_recent_rows(
    rows: list[dict],
) -> tuple[list[dict], str | None, str | None]:

    dates = [
        parse_date(
            row["naver"].get(
                "published_date"
            )
        )
        for row in rows
    ]

    dates = [
        value
        for value in dates
        if value is not None
    ]

    if not dates:
        return [], None, None

    latest = max(dates)

    cutoff = (
        latest
        - timedelta(
            days=RECENT_DAYS
        )
    )

    recent = []

    for row in rows:

        value = parse_date(
            row["naver"].get(
                "published_date"
            )
        )

        if value is None:
            continue

        if value >= cutoff:
            recent.append(row)

    return (
        recent,
        cutoff.isoformat(),
        latest.isoformat(),
    )


# ==========================================================
# MARKDOWN TABLE
# ==========================================================

def substack_cell(row: dict) -> str:
    items = row.get("substack") or []

    if not items:
        return "—"

    links = []

    for item in items:
        title = (
            item.get("title")
            or item.get("url")
            or "Substack"
        )

        url = item.get("url")

        part = item.get("part")

        if part is not None:
            title = f"Part {part}: {title}"

        if url:
            links.append(
                f"[{md_escape(title)}]"
                f"({url})"
            )
        else:
            links.append(
                md_escape(title)
            )

    return "<br>".join(links)


def naver_cell(row: dict) -> str:
    naver = row["naver"]

    title = md_escape(
        naver.get("title")
        or "(untitled)"
    )

    archive = naver.get(
        "github_archive"
    )

    if archive:
        return (
            f"[{title}]"
            f"({archive})"
        )

    url = naver.get("url")

    if url:
        return (
            f"[{title}]"
            f"({url})"
        )

    return title


def table_for_rows(rows: list[dict]) -> str:
    lines = [
        "| Naver date | Naver post | Status | Substack | logNo |",
        "|---|---|---|---|---|",
    ]

    for row in rows:

        lines.append(
            "| "
            + md_escape(
                row["naver"].get(
                    "published_date"
                )
            )
            + " | "
            + naver_cell(row)
            + " | "
            + md_escape(
                row.get(
                    "status_label"
                )
            )
            + " | "
            + substack_cell(row)
            + " | `"
            + md_escape(
                row["naver"].get(
                    "logNo"
                )
            )
            + "` |"
        )

    return "\n".join(lines)


# ==========================================================
# OUTPUT
# ==========================================================

def build_markdown(
    rows: list[dict],
    unmatched_substack: list[dict],
    sitemap_info: dict,
) -> str:

    summary = count_status(
        rows
    )

    (
        recent_rows,
        recent_start,
        recent_end,
    ) = get_recent_rows(
        rows
    )

    recent_summary = count_status(
        recent_rows
    )

    now = datetime.now(
        timezone.utc
    ).replace(
        microsecond=0
    ).isoformat()

    lines = []

    lines.append(
        "# Naver ↔ Substack Publishing Status"
    )

    lines.append("")
    lines.append(
        f"_Automatically generated: {now}_"
    )

    lines.append("")
    lines.append(
        "Naver `index.json` is the source of truth. "
        "If a Naver `logNo` has no confirmed mapping in "
        "`substack_manifest.json`, it is shown as **❌ Missing**."
    )

    if not sitemap_info.get("ok"):

        lines.append("")
        lines.append(
            "> ⚠️ Substack public sitemap could not be fetched "
            "during this run. Existing manifest mappings were "
            "kept, but live public-link validation was skipped."
        )

    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(
        "| Scope | Naver posts | Published | Missing | Skip | Check |"
    )
    lines.append(
        "|---|---:|---:|---:|---:|---:|"
    )
    lines.append(
        f"| All | {summary['total']} | "
        f"{summary['published']} | "
        f"{summary['missing']} | "
        f"{summary['skip']} | "
        f"{summary['check']} |"
    )

    if recent_start and recent_end:

        lines.append(
            f"| Recent {RECENT_DAYS} days "
            f"({recent_start} → {recent_end}) | "
            f"{recent_summary['total']} | "
            f"{recent_summary['published']} | "
            f"{recent_summary['missing']} | "
            f"{recent_summary['skip']} | "
            f"{recent_summary['check']} |"
        )

    # ------------------------------------------------------
    # Recent rows: quickest human dashboard
    # ------------------------------------------------------

    lines.append("")
    lines.append(
        f"## Recent {RECENT_DAYS} days"
    )
    lines.append("")

    if recent_rows:
        lines.append(
            table_for_rows(
                recent_rows
            )
        )
    else:
        lines.append(
            "_No recent Naver posts found._"
        )

    # ------------------------------------------------------
    # Missing only
    # ------------------------------------------------------

    missing_rows = [
        row
        for row in rows
        if row["status"] == "missing"
    ]

    lines.append("")
    lines.append(
        "## Missing from Substack"
    )
    lines.append("")

    if missing_rows:

        lines.append(
            f"Total missing: **{len(missing_rows)}**"
        )

        lines.append("")

        lines.append(
            table_for_rows(
                missing_rows
            )
        )

    else:

        lines.append(
            "✅ No missing Naver posts."
        )

    # ------------------------------------------------------
    # Unmatched Substack URLs
    # ------------------------------------------------------

    lines.append("")
    lines.append(
        "## Unmatched public Substack posts"
    )
    lines.append("")

    if not sitemap_info.get("ok"):

        lines.append(
            "_Not calculated because the public sitemap fetch failed._"
        )

    elif unmatched_substack:

        lines.append(
            "These are public Substack posts that exist in the sitemap "
            "but are not referenced by any Naver `logNo` in the manifest."
        )

        lines.append("")
        lines.append(
            "| Substack post | Public sitemap year |"
        )
        lines.append(
            "|---|---|"
        )

        for item in unmatched_substack:

            title = md_escape(
                item.get("title")
                or item.get("url")
            )

            url = item.get("url")

            lines.append(
                f"| [{title}]({url}) | "
                f"{md_escape(item.get('sitemap_year'))} |"
            )

    else:

        lines.append(
            "✅ Every public Substack post is currently mapped."
        )

    # ------------------------------------------------------
    # Full table in collapsible section
    # ------------------------------------------------------

    lines.append("")
    lines.append(
        "## Full Naver archive"
    )
    lines.append("")
    lines.append("<details>")
    lines.append(
        f"<summary>Show all {len(rows)} Naver posts</summary>"
    )
    lines.append("")
    lines.append(
        table_for_rows(
            rows
        )
    )
    lines.append("")
    lines.append("</details>")
    lines.append("")

    return "\n".join(lines)


# ==========================================================
# MAIN
# ==========================================================

def main() -> int:

    print(
        "Loading Naver index..."
    )

    naver_index = load_json(
        INDEX_PATH
    )

    print(
        "Loading Substack manifest..."
    )

    manifest = load_json(
        MANIFEST_PATH
    )

    base_url = (
        manifest.get(
            "substack_base_url"
        )
        or DEFAULT_SUBSTACK_BASE
    )

    print(
        f"Reading public Substack sitemap: "
        f"{base_url}"
    )

    public_posts, sitemap_info = (
        discover_substack_posts(
            base_url
        )
    )

    if sitemap_info.get("ok"):

        print(
            f"Public Substack posts found: "
            f"{len(public_posts)}"
        )

    else:

        print(
            "WARNING: Substack sitemap fetch failed:"
        )

        print(
            sitemap_info.get(
                "error"
            )
        )

    rows, unmatched_substack = (
        build_status(
            naver_index,
            manifest,
            public_posts,
            sitemap_info.get(
                "ok",
                False,
            ),
        )
    )

    summary = count_status(
        rows
    )

    recent_rows, recent_start, recent_end = (
        get_recent_rows(
            rows
        )
    )

    recent_summary = count_status(
        recent_rows
    )

    output_json = {
        "generated_at":
            datetime.now(
                timezone.utc
            ).replace(
                microsecond=0
            ).isoformat(),

        "sources": {
            "naver_index":
                str(INDEX_PATH),

            "substack_manifest":
                str(MANIFEST_PATH),

            "substack_base_url":
                base_url,

            "substack_sitemap":
                sitemap_info,
        },

        "summary":
            summary,

        "recent_summary": {
            "days":
                RECENT_DAYS,

            "start":
                recent_start,

            "end":
                recent_end,

            **recent_summary,
        },

        "unmatched_substack_posts":
            unmatched_substack,

        "posts":
            rows,
    }

    atomic_write(
        STATUS_JSON_PATH,
        json.dumps(
            output_json,
            ensure_ascii=False,
            indent=2,
        ),
    )

    markdown = build_markdown(
        rows,
        unmatched_substack,
        sitemap_info,
    )

    atomic_write(
        STATUS_MD_PATH,
        markdown,
    )

    print()
    print(
        "=" * 60
    )

    print(
        "SUBSTACK STATUS UPDATED"
    )

    print(
        "=" * 60
    )

    print(
        f"Naver total : "
        f"{summary['total']}"
    )

    print(
        f"Published   : "
        f"{summary['published']}"
    )

    print(
        f"Missing     : "
        f"{summary['missing']}"
    )

    print(
        f"Skip        : "
        f"{summary['skip']}"
    )

    print(
        f"Check       : "
        f"{summary['check']}"
    )

    if sitemap_info.get("ok"):

        print(
            f"Public Substack: "
            f"{len(public_posts)}"
        )

        print(
            f"Unmatched Substack: "
            f"{len(unmatched_substack)}"
        )

    print(
        "=" * 60
    )

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())

    except Exception as exc:

        print(
            f"ERROR: {exc}",
            file=sys.stderr,
        )

        raise
