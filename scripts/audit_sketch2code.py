from __future__ import annotations

import csv
import hashlib
import html
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from PIL import Image


CATEGORY_LABELS = {
    "form_input": "表单与输入",
    "data_dashboard": "数据、目录与表格",
    "commerce": "商品与交易",
    "article_content": "文章与内容",
    "media_gallery": "图片与媒体",
    "general_landing": "通用页面与导航",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv_atomic(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def image_info(path: Path | None) -> tuple[bool, int, int, str]:
    if not path:
        return False, 0, 0, ""
    try:
        with Image.open(path) as image:
            width, height = image.size
            mode = image.mode
            image.verify()
        return True, width, height, mode
    except (OSError, ValueError):
        return False, 0, 0, ""


def file_sha256(path: Path | None) -> str:
    if not path:
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    dataset_root = root / "data/raw/sketch2code/Sketch2Code"
    sketches_root = dataset_root / "sketches"
    webpages_root = dataset_root / "webpages"
    processed = root / "data/processed"
    inventory_path = processed / "sketch2code_inventory.csv"
    candidate_path = processed / "sketch2code_review_candidates.csv"
    review_path = processed / "sketch2code_review.html"

    sketches_by_page: dict[str, list[Path]] = defaultdict(list)
    invalid_names: list[str] = []
    for path in sketches_root.glob("*.png"):
        match = re.fullmatch(r"(.+)_([0-9]+)", path.stem)
        if not match:
            invalid_names.append(path.name)
            continue
        sketches_by_page[match.group(1)].append(path)

    html_by_page = {path.stem: path for path in webpages_root.glob("*.html")}
    screenshot_by_page = {path.stem: path for path in webpages_root.glob("*.png")}
    all_page_ids = sorted(set(sketches_by_page) | set(html_by_page) | set(screenshot_by_page))

    inventory: list[dict[str, Any]] = []
    for page_id in all_page_ids:
        html_path = html_by_page.get(page_id)
        screenshot_path = screenshot_by_page.get(page_id)
        sketch_paths = sorted(sketches_by_page.get(page_id, []), key=lambda path: path.stem)
        target_valid, target_width, target_height, target_mode = image_info(screenshot_path)
        sketch_checks = [image_info(path) for path in sketch_paths]
        html_valid = False
        if html_path:
            content = html_path.read_text(encoding="utf-8", errors="replace").lower()
            html_valid = "<html" in content and "<body" in content
        inventory.append(
            {
                "dataset": "sketch2code",
                "webpage_id": page_id,
                "html_path": html_path.relative_to(root).as_posix() if html_path else "",
                "target_screenshot_path": screenshot_path.relative_to(root).as_posix() if screenshot_path else "",
                "sketch_count": len(sketch_paths),
                "sketch_paths": "|".join(path.relative_to(root).as_posix() for path in sketch_paths),
                "pair_status": "complete" if html_path and screenshot_path and sketch_paths else "incomplete",
                "html_valid": html_valid,
                "target_image_valid": target_valid,
                "all_sketches_valid": bool(sketch_checks) and all(check[0] for check in sketch_checks),
                "target_width": target_width,
                "target_height": target_height,
                "target_mode": target_mode,
                "target_sha256": file_sha256(screenshot_path),
            }
        )
    inventory_fields = list(inventory[0])
    write_csv_atomic(inventory_path, inventory, inventory_fields)

    selected_d2c = [
        row
        for row in read_csv(processed / "design2code_final_selections.csv")
        if row["subset"] == "standard"
    ]
    inventory_by_id = {row["webpage_id"]: row for row in inventory}
    candidates: list[dict[str, Any]] = []
    for source in selected_d2c:
        page_id = source["sample_id"]
        inventory_row = inventory_by_id[page_id]
        if not (
            inventory_row["pair_status"] == "complete"
            and inventory_row["html_valid"]
            and inventory_row["target_image_valid"]
            and inventory_row["all_sketches_valid"]
        ):
            raise ValueError(f"selected Design2Code page is not complete in Sketch2Code: {page_id}")
        for sketch_path_text in inventory_row["sketch_paths"].split("|"):
            sketch_path = root / sketch_path_text
            valid, width, height, mode = image_info(sketch_path)
            sketch_index = sketch_path.stem.rsplit("_", 1)[1]
            candidates.append(
                {
                    "review_id": f"S2C-{page_id}-{sketch_index}",
                    "webpage_id": page_id,
                    "sketch_id": sketch_index,
                    "source_design2code_review_id": source["review_id"],
                    "category": source["category"],
                    "category_label": CATEGORY_LABELS[source["category"]],
                    "page_subtype": source["page_subtype"],
                    "content_summary": source["content_summary"],
                    "sketch_path": sketch_path_text,
                    "target_screenshot_path": inventory_row["target_screenshot_path"],
                    "html_path": inventory_row["html_path"],
                    "sketch_width": width,
                    "sketch_height": height,
                    "sketch_mode": mode,
                    "sketch_valid": valid,
                    "sketch_sha256": file_sha256(sketch_path),
                    "review_status": "pending",
                }
            )
    candidate_fields = list(candidates[0])
    write_csv_atomic(candidate_path, candidates, candidate_fields)

    groups: list[str] = []
    for source in selected_d2c:
        page_id = source["sample_id"]
        rows = [row for row in candidates if row["webpage_id"] == page_id]
        target_source = Path(os.path.relpath(root / rows[0]["target_screenshot_path"], review_path.parent)).as_posix()
        html_source = Path(os.path.relpath(root / rows[0]["html_path"], review_path.parent)).as_posix()
        sketch_cards = []
        for row in rows:
            sketch_source = Path(os.path.relpath(root / row["sketch_path"], review_path.parent)).as_posix()
            sketch_cards.append(
                f"""<label class="sketch-card"><a href="{html.escape(sketch_source)}"><img src="{html.escape(sketch_source)}" alt="{html.escape(row['review_id'])}" loading="lazy"></a><span><input type="checkbox" value="{html.escape(row['review_id'])}"> {html.escape(row['review_id'])}</span><small>{row['sketch_width']} × {row['sketch_height']}</small></label>"""
            )
        groups.append(
            f"""<section data-page-id="{html.escape(page_id)}"><div class="group-head"><div><h2>{html.escape(source['review_id'])} · {html.escape(source['page_subtype'])}</h2><p><strong>{html.escape(CATEGORY_LABELS[source['category']])}</strong> · {html.escape(source['content_summary'])}</p></div><span>本组最多选 1 张</span></div><div class="comparison"><div class="target"><h3>目标网页截图</h3><a href="{html.escape(target_source)}"><img src="{html.escape(target_source)}" alt="目标网页 {html.escape(page_id)}" loading="lazy"></a><a href="{html.escape(html_source)}">打开配对 HTML</a></div><div class="sketches"><h3>人工草图候选</h3><div class="sketch-grid">{''.join(sketch_cards)}</div></div></div></section>"""
        )

    document = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Sketch2Code 人工挑选</title><style>
:root{{font-family:"Segoe UI","Microsoft YaHei",sans-serif;color:#172126}}*{{box-sizing:border-box}}body{{margin:0;background:#f2f4f5}}header{{position:sticky;top:0;z-index:5;display:grid;grid-template-columns:minmax(0,1fr) minmax(460px,.8fr);gap:20px;padding:14px 24px;background:#fff;border-bottom:1px solid #bcc6ca}}h1{{margin:0;font-size:22px}}header p{{margin:5px 0;color:#59676d}}.result{{display:grid;grid-template-columns:auto 1fr auto;gap:10px;align-items:center}}textarea{{width:100%;min-height:64px;padding:7px;border:1px solid #84949a;font:12px Consolas,monospace}}button{{min-height:36px;background:#fff;border:1px solid #6d7c82;padding:7px 12px}}main{{width:min(1500px,100%);margin:auto;padding:20px 24px 48px}}section{{margin-bottom:32px;background:#fff;border:1px solid #c4ced2}}section.selected{{border:3px solid #167554}}.group-head{{display:flex;justify-content:space-between;align-items:flex-start;gap:18px;padding:14px 16px;border-bottom:1px solid #d1d9dc}}h2,h3{{margin:0}}.group-head p{{margin:6px 0 0;color:#536269}}.comparison{{display:grid;grid-template-columns:minmax(360px,.8fr) minmax(0,1.5fr);gap:18px;padding:16px}}.target,.sketches{{min-width:0}}.target>a:first-of-type{{display:block;height:430px;margin:10px 0;background:#e7ebed}}.target img,.sketch-card img{{display:block;width:100%;height:100%;object-fit:contain}}.sketch-grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-top:10px}}.sketch-card{{display:grid;grid-template-rows:360px auto auto;border:1px solid #bdc8cc;background:#f8f9f9;min-width:0}}.sketch-card>a{{display:block;background:#e7ebed;overflow:hidden}}.sketch-card>span{{padding:9px;font-weight:700}}.sketch-card small{{padding:0 9px 9px;color:#617078}}input{{width:18px;height:18px;vertical-align:middle}}@media(max-width:1050px){{header{{position:static;grid-template-columns:1fr}}.comparison{{grid-template-columns:1fr}}}}@media(max-width:720px){{header,main{{padding:14px}}.result{{grid-template-columns:1fr}}.sketch-grid{{grid-template-columns:1fr}}.sketch-card{{grid-template-rows:420px auto auto}}}}</style></head><body>
<header><div><h1>Sketch2Code 草图精筛</h1><p>12 个目标网页已经通过 Design2Code 人工确认。每组只比较草图是否清楚表达目标布局；从 12 组中选择 8 组，每组最多选 1 张草图。</p></div><div class="result"><strong>已选 <span id="count">0</span> / 8</strong><textarea id="output" readonly></textarea><button id="clear" type="button">清空</button></div></header><main>{''.join(groups)}</main>
<script>const key='sketch2code-review-v1';const boxes=[...document.querySelectorAll('input[type="checkbox"]')];function update(){{const picked=boxes.filter(box=>box.checked);document.querySelectorAll('section').forEach(section=>section.classList.toggle('selected',section.querySelectorAll('input:checked').length===1));document.getElementById('count').textContent=picked.length;document.getElementById('output').value=picked.map(box=>box.value).join(', ');localStorage.setItem(key,JSON.stringify(picked.map(box=>box.value)));}}boxes.forEach(box=>box.addEventListener('change',()=>{{if(box.checked)box.closest('section').querySelectorAll('input').forEach(other=>{{if(other!==box)other.checked=false;}});update();}}));document.getElementById('clear').addEventListener('click',()=>{{boxes.forEach(box=>box.checked=false);update();}});try{{const saved=new Set(JSON.parse(localStorage.getItem(key)||'[]'));boxes.forEach(box=>box.checked=saved.has(box.value));}}catch(_){{}}update();</script></body></html>"""
    review_path.write_text(document, encoding="utf-8")

    complete = sum(
        row["pair_status"] == "complete"
        and row["html_valid"]
        and row["target_image_valid"]
        and row["all_sketches_valid"]
        for row in inventory
    )
    print(f"Webpage inventory rows: {len(inventory)}")
    print(f"Complete webpage/sketch groups: {complete}")
    print(f"Sketch files: {sum(int(row['sketch_count']) for row in inventory)}")
    print(f"Sketches per webpage: {dict(Counter(int(row['sketch_count']) for row in inventory))}")
    print(f"Invalid sketch names: {len(invalid_names)}")
    print(f"Review target pages: {len(selected_d2c)}")
    print(f"Review sketch candidates: {len(candidates)}")
    print(f"Review page: {review_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
