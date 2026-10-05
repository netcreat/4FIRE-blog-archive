from pathlib import Path
import json
import re


POSTS_DIR = Path("posts")
INDEX_PATH = Path("index.json")


moved = 0


# ============================================
# 1. flat 구조의 md 파일을 연도 폴더로 이동
# ============================================

for path in list(
    POSTS_DIR.glob("*.md")
):

    match = re.match(
        r"(\d{4})-\d{2}-\d{2}-",
        path.name
    )

    if not match:
        print(
            f"SKIP: cannot determine year: "
            f"{path.name}"
        )
        continue

    year = match.group(1)

    year_dir = POSTS_DIR / year

    year_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    destination = (
        year_dir / path.name
    )

    if destination.exists():
        print(
            f"EXISTS: {destination}"
        )

        # 같은 파일이 이미 있으면
        # flat 파일 제거
        path.unlink()

        continue

    path.rename(destination)

    moved += 1

    print(
        f"MOVED: "
        f"{path.name}"
        f" -> "
        f"{destination}"
    )


# ============================================
# 2. index.json의 archive 경로 수정
# ============================================

if INDEX_PATH.exists():

    data = json.loads(
        INDEX_PATH.read_text(
            encoding="utf-8"
        )
    )

    for item in data.get(
        "posts",
        []
    ):

        archive = item.get(
            "archive",
            ""
        )

        # 이미 연도 구조면 건드리지 않는다.
        if re.match(
            r"posts/\d{4}/",
            archive
        ):
            continue

        filename = Path(
            archive
        ).name

        match = re.match(
            r"(\d{4})-\d{2}-\d{2}-",
            filename
        )

        if not match:
            continue

        year = match.group(1)

        item["archive"] = (
            f"posts/{year}/{filename}"
        )

    INDEX_PATH.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )


print()
print(
    f"Reorganization complete."
)

print(
    f"Moved {moved} files."
)
