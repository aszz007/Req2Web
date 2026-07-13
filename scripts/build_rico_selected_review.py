from __future__ import annotations

import csv
import hashlib
import html
import os
import tempfile
from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from PIL import Image


VERIFICATION_FIELDS = (
    "screen_id",
    "recommended_category",
    "candidate_screenshot_path",
    "inventory_screenshot_path",
    "file_exists",
    "filename_matches_screen_id",
    "candidate_matches_inventory",
    "image_width",
    "image_height",
    "sha256",
    "verified",
    "manual_notes",
)


class ReviewImageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.images: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "img":
            return
        values = dict(attrs)
        self.images.append((values.get("data-screen-id", "") or "", values.get("src", "") or ""))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def write_csv_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    handle, temporary_name = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=path.parent)
    os.close(handle)
    temporary_path = Path(temporary_name)
    try:
        with temporary_path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=VERIFICATION_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def main() -> int:
    project_root = Path(__file__).resolve().parent.parent
    processed_root = project_root / "data/processed"
    candidate_path = processed_root / "rico_candidate_pool.csv"
    inventory_path = processed_root / "rico_combined_inventory.csv"
    verification_path = processed_root / "rico_selected_image_verification.csv"
    review_path = processed_root / "rico_selected_review.html"

    candidate_rows = [
        row for row in read_csv(candidate_path) if row.get("manual_status") == "keep"
    ]
    inventory_by_id = {
        row["screen_id"]: row for row in read_csv(inventory_path) if row.get("screen_id")
    }
    verification_rows: list[dict[str, Any]] = []

    for row in candidate_rows:
        screen_id = row["screen_id"]
        candidate_relative = row["screenshot_path"]
        inventory_relative = inventory_by_id.get(screen_id, {}).get("screenshot_path", "")
        image_path = project_root / candidate_relative
        file_exists = image_path.is_file()
        filename_matches = image_path.stem == screen_id
        paths_match = candidate_relative == inventory_relative
        width = 0
        height = 0
        digest = ""
        if file_exists:
            with Image.open(image_path) as image:
                width, height = image.size
                image.verify()
            digest = sha256_file(image_path)
        verified = file_exists and filename_matches and paths_match and bool(digest)
        verification_rows.append(
            {
                "screen_id": screen_id,
                "recommended_category": row["recommended_category"],
                "candidate_screenshot_path": candidate_relative,
                "inventory_screenshot_path": inventory_relative,
                "file_exists": file_exists,
                "filename_matches_screen_id": filename_matches,
                "candidate_matches_inventory": paths_match,
                "image_width": width,
                "image_height": height,
                "sha256": digest,
                "verified": verified,
                "manual_notes": row["manual_notes"],
            }
        )

    write_csv_atomic(verification_path, verification_rows)

    verification_by_id = {row["screen_id"]: row for row in verification_rows}
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in candidate_rows:
        grouped[row["recommended_category"]].append(row)

    sections: list[str] = []
    for category in sorted(grouped):
        items: list[str] = []
        for row in grouped[category]:
            screen_id = row["screen_id"]
            verification = verification_by_id[screen_id]
            image_path = project_root / row["screenshot_path"]
            image_source = Path(os.path.relpath(image_path, review_path.parent)).as_posix()
            items.append(
                f"""
                <article class="item">
                  <a href="{html.escape(image_source)}" title="打开原始图片 {html.escape(screen_id)}">
                    <img src="{html.escape(image_source)}" data-screen-id="{html.escape(screen_id)}" alt="RICO screen {html.escape(screen_id)}" loading="lazy">
                  </a>
                  <div class="meta">
                    <h3>Screen ID {html.escape(screen_id)}</h3>
                    <p>{html.escape(row['manual_notes'])}</p>
                    <dl>
                      <dt>原图路径</dt><dd>{html.escape(row['screenshot_path'])}</dd>
                      <dt>尺寸</dt><dd>{verification['image_width']} x {verification['image_height']}</dd>
                      <dt>SHA-256</dt><dd>{html.escape(str(verification['sha256']))}</dd>
                    </dl>
                  </div>
                </article>
                """
            )
        sections.append(
            f"""
            <section>
              <h2>{html.escape(category)} <span>{len(items)} 条</span></h2>
              <div class="grid">{''.join(items)}</div>
            </section>
            """
        )

    verified_count = sum(bool(row["verified"]) for row in verification_rows)
    document = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>RICO 已选图片核验</title>
  <style>
    :root {{ color-scheme: light; font-family: "Segoe UI", "Microsoft YaHei", sans-serif; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; color: #17202a; background: #f4f6f7; }}
    header {{ position: sticky; top: 0; z-index: 2; padding: 14px 24px; background: #ffffff; border-bottom: 1px solid #cfd8dc; }}
    header h1 {{ margin: 0; font-size: 22px; font-weight: 650; }}
    header p {{ margin: 5px 0 0; color: #52616b; font-size: 14px; }}
    main {{ width: min(1500px, 100%); margin: 0 auto; padding: 18px 24px 40px; }}
    section {{ margin: 0 0 30px; }}
    section h2 {{ margin: 0 0 12px; font-size: 19px; }}
    section h2 span {{ color: #607d8b; font-size: 14px; font-weight: 500; }}
    .grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }}
    .item {{ display: grid; grid-template-columns: minmax(150px, 42%) minmax(0, 1fr); min-height: 430px; background: #ffffff; border: 1px solid #cfd8dc; }}
    .item > a {{ display: block; min-width: 0; background: #eef1f2; }}
    img {{ display: block; width: 100%; height: 100%; max-height: 520px; object-fit: contain; }}
    .meta {{ min-width: 0; padding: 14px; }}
    h3 {{ margin: 0 0 10px; font-size: 17px; }}
    p {{ margin: 0 0 14px; line-height: 1.55; }}
    dl {{ margin: 0; font-size: 12px; }}
    dt {{ margin-top: 9px; color: #607d8b; }}
    dd {{ margin: 2px 0 0; overflow-wrap: anywhere; font-family: Consolas, monospace; }}
    @media (max-width: 1100px) {{ .grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} }}
    @media (max-width: 720px) {{
      header {{ position: static; padding: 12px 14px; }}
      main {{ padding: 14px; }}
      .grid {{ grid-template-columns: 1fr; }}
      .item {{ grid-template-columns: minmax(135px, 42%) minmax(0, 1fr); min-height: 360px; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>RICO 已选图片核验</h1>
    <p>{verified_count}/{len(verification_rows)} 条通过 screen_id、candidate、inventory、原图文件名和 SHA-256 交叉校验。点击图片打开对应原始 JPG。</p>
  </header>
  <main>{''.join(sections)}</main>
</body>
</html>
"""
    review_path.write_text(document, encoding="utf-8")

    parser = ReviewImageParser()
    parser.feed(review_path.read_text(encoding="utf-8"))
    html_references_valid = len(parser.images) == len(verification_rows) and all(
        screen_id
        and source
        and (review_path.parent / source).resolve().is_file()
        and (review_path.parent / source).resolve().stem == screen_id
        for screen_id, source in parser.images
    )

    print(f"Selected rows: {len(verification_rows)}")
    print(f"Verified rows: {verified_count}")
    print(f"HTML image references: {len(parser.images)}")
    print(f"HTML references valid: {html_references_valid}")
    print(f"Verification CSV: {verification_path}")
    print(f"Review page: {review_path}")
    return 0 if verified_count == len(verification_rows) and html_references_valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
