from __future__ import annotations

import csv
import hashlib
import html
import os
from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path

from PIL import Image


REJECTED_BY_GROUP = {
    "dashboard": "11628",
    "empty_state": "35028, 33318",
    "general": "9688",
    "media": "28723",
    "social": "47328, 28020",
}


class ReplacementParser(HTMLParser):
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


def main() -> int:
    project_root = Path(__file__).resolve().parent.parent
    processed_root = project_root / "data/processed"
    source_path = processed_root / "rico_replacement_candidates.csv"
    inventory_path = processed_root / "rico_combined_inventory.csv"
    review_path = processed_root / "rico_replacement_review.html"

    source_rows = read_csv(source_path)
    inventory = {row["screen_id"]: row for row in read_csv(inventory_path)}
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    verified_rows: list[dict[str, str]] = []

    for source in source_rows:
        screen_id = source["screen_id"]
        inventory_row = inventory.get(screen_id)
        if not inventory_row:
            raise ValueError(f"screen_id missing from RICO inventory: {screen_id}")
        relative = inventory_row["screenshot_path"]
        image_path = project_root / relative
        if not image_path.is_file() or image_path.stem != screen_id:
            raise ValueError(f"invalid screenshot mapping for {screen_id}: {image_path}")
        with Image.open(image_path) as image:
            width, height = image.size
            image.verify()
        row = dict(source)
        row.update(
            {
                "screenshot_path": relative,
                "width": str(width),
                "height": str(height),
                "sha256": sha256_file(image_path),
            }
        )
        grouped[source["group_id"]].append(row)
        verified_rows.append(row)

    sections: list[str] = []
    total_required = 0
    for group_id, rows in grouped.items():
        group_label = rows[0]["group_label"]
        required = int(rows[0]["required_count"])
        total_required += required
        items: list[str] = []
        for row in rows:
            image_path = project_root / row["screenshot_path"]
            source = Path(os.path.relpath(image_path, review_path.parent)).as_posix()
            items.append(
                f"""
                <article class="candidate" data-screen-id="{html.escape(row['screen_id'])}">
                  <div class="candidate-head">
                    <label><input type="checkbox" value="{html.escape(row['screen_id'])}"> 选择 Screen ID {html.escape(row['screen_id'])}</label>
                  </div>
                  <a class="image-link" href="{html.escape(source)}" title="打开原始 JPG {html.escape(row['screen_id'])}">
                    <img src="{html.escape(source)}" data-screen-id="{html.escape(row['screen_id'])}" alt="RICO screen {html.escape(row['screen_id'])}" loading="lazy">
                  </a>
                  <div class="file-meta">{html.escape(row['screenshot_path'])}<br>{row['width']} x {row['height']} · SHA {html.escape(row['sha256'][:12])}</div>
                </article>
                """
            )
        sections.append(
            f"""
            <section class="review-group" data-group="{html.escape(group_id)}" data-limit="{required}">
              <div class="section-title">
                <div><h2>{html.escape(group_label)}</h2><p>替换原 ID：{html.escape(REJECTED_BY_GROUP[group_id])}</p></div>
                <strong>本组选 <span class="group-selected">0</span> / {required}</strong>
              </div>
              <div class="grid">{''.join(items)}</div>
            </section>
            """
        )

    document = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>RICO 替换候选选择</title>
  <style>
    :root {{ color-scheme: light; font-family: "Segoe UI", "Microsoft YaHei", sans-serif; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; color: #17202a; background: #f3f5f6; }}
    header {{ position: sticky; top: 0; z-index: 4; display: grid; grid-template-columns: minmax(0, 1fr) minmax(320px, 0.7fr); gap: 20px; padding: 14px 24px; background: #ffffff; border-bottom: 1px solid #c5cdd1; }}
    h1 {{ margin: 0; font-size: 22px; }}
    header p {{ margin: 5px 0 0; color: #52616b; font-size: 14px; }}
    .selection-summary {{ display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 8px 12px; align-items: center; }}
    .selection-summary strong {{ font-size: 18px; white-space: nowrap; }}
    #selected-ids {{ width: 100%; min-height: 38px; resize: none; border: 1px solid #98a6ad; padding: 8px; font: 14px Consolas, monospace; }}
    #reset {{ justify-self: start; border: 1px solid #9aa7ad; background: #ffffff; padding: 6px 12px; cursor: pointer; }}
    main {{ width: min(1500px, 100%); margin: 0 auto; padding: 20px 24px 44px; }}
    section {{ margin-bottom: 34px; }}
    .section-title {{ display: flex; align-items: end; justify-content: space-between; gap: 16px; margin-bottom: 12px; }}
    .section-title h2 {{ margin: 0; font-size: 20px; }}
    .section-title p {{ margin: 4px 0 0; color: #60727a; font-size: 13px; }}
    .section-title strong {{ color: #0b6847; font-size: 16px; }}
    .grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }}
    .candidate {{ display: grid; grid-template-rows: auto 520px auto; min-width: 0; background: #ffffff; border: 1px solid #c7d0d4; }}
    .candidate.selected {{ border: 3px solid #18845d; }}
    .candidate-head {{ min-height: 48px; display: flex; align-items: center; padding: 10px 12px; border-bottom: 1px solid #d7dde0; font-weight: 650; }}
    .candidate-head label {{ cursor: pointer; }}
    .candidate-head input {{ width: 19px; height: 19px; margin: 0 8px 0 0; vertical-align: middle; }}
    .image-link {{ display: block; min-width: 0; background: #edf0f1; }}
    img {{ display: block; width: 100%; height: 100%; object-fit: contain; }}
    .file-meta {{ min-height: 54px; padding: 8px 10px; color: #5c6b72; font: 11px/1.5 Consolas, monospace; overflow-wrap: anywhere; }}
    @media (max-width: 1050px) {{ header {{ grid-template-columns: 1fr; }} .grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} }}
    @media (max-width: 680px) {{ header {{ position: static; padding: 12px 14px; }} main {{ padding: 14px; }} .grid {{ grid-template-columns: 1fr; }} .candidate {{ grid-template-rows: auto 480px auto; }} }}
  </style>
</head>
<body>
  <header>
    <div><h1>RICO 替换候选选择</h1><p>按每组右侧要求勾选。点击图片打开对应 combined 原始 JPG；选择结果会保存在当前浏览器。</p></div>
    <div class="selection-summary">
      <strong>已选 <span id="total-selected">0</span> / {total_required}</strong>
      <textarea id="selected-ids" readonly aria-label="已选择的 Screen ID"></textarea>
      <button id="reset" type="button">清空选择</button>
    </div>
  </header>
  <main>{''.join(sections)}</main>
  <script>
    const storageKey = 'rico-replacement-review-v1';
    const groups = [...document.querySelectorAll('.review-group')];
    const allBoxes = [...document.querySelectorAll('input[type="checkbox"]')];
    const totalTarget = {total_required};

    function update() {{
      groups.forEach(group => {{
        const boxes = [...group.querySelectorAll('input[type="checkbox"]')];
        const selected = boxes.filter(box => box.checked);
        group.querySelector('.group-selected').textContent = selected.length;
        boxes.forEach(box => box.closest('.candidate').classList.toggle('selected', box.checked));
      }});
      const selectedIds = allBoxes.filter(box => box.checked).map(box => box.value);
      document.getElementById('total-selected').textContent = selectedIds.length;
      document.getElementById('selected-ids').value = selectedIds.join(', ');
      localStorage.setItem(storageKey, JSON.stringify(selectedIds));
    }}

    allBoxes.forEach(box => box.addEventListener('change', event => {{
      const group = event.target.closest('.review-group');
      const limit = Number(group.dataset.limit);
      const selected = [...group.querySelectorAll('input[type="checkbox"]:checked')];
      if (selected.length > limit) {{
        event.target.checked = false;
        alert(`本组只需要选择 ${{limit}} 张。`);
      }}
      update();
    }}));

    document.getElementById('reset').addEventListener('click', () => {{
      allBoxes.forEach(box => box.checked = false);
      update();
    }});

    try {{
      const saved = new Set(JSON.parse(localStorage.getItem(storageKey) || '[]'));
      allBoxes.forEach(box => box.checked = saved.has(box.value));
    }} catch (_) {{}}
    update();
  </script>
</body>
</html>
"""
    review_path.write_text(document, encoding="utf-8")

    parser = ReplacementParser()
    parser.feed(review_path.read_text(encoding="utf-8"))
    valid = len(parser.images) == len(verified_rows) and all(
        screen_id
        and source
        and (review_path.parent / source).resolve().is_file()
        and (review_path.parent / source).resolve().stem == screen_id
        for screen_id, source in parser.images
    )
    print(f"Replacement candidates: {len(verified_rows)}")
    print(f"Required selections: {total_required}")
    print(f"HTML references valid: {valid}")
    print(f"Review page: {review_path}")
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
