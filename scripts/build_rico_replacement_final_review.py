from __future__ import annotations

import csv
import hashlib
import html
import os
from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path

from PIL import Image


GROUP_STANDARDS = {
    "dashboard": {
        "title": "数据面板选择标准",
        "items": [
            "页面目标和核心指标应能在较短时间内理解，不依赖大量领域背景。",
            "具有清楚的指标层级，至少包含汇总数值、趋势图、统计表或分组数据中的两类。",
            "图表、数字、标签和时间范围之间关系明确，可转化为前端组件与布局约束。",
            "避免只有空白容器、缺少数据上下文或装饰明显多于信息表达的界面。",
        ],
    },
    "empty_state": {
        "title": "空状态选择标准",
        "items": [
            "必须明确表达当前没有什么内容，并能与加载中、系统错误和权限受限状态区分。",
            "保留所在功能的页面标题或导航上下文，使 Agent 知道空状态属于哪个业务区域。",
            "优先包含简洁图形、解释文字和下一步操作中的至少两项。",
            "同组多个样本应覆盖不同业务场景或不同处理方式，避免只更换文案的重复页面。",
        ],
    },
    "general": {
        "title": "通用信息页面选择标准",
        "items": [
            "页面用途明确，可归纳为文章、帮助、说明、条款或知识内容等常见信息展示任务。",
            "标题、分节、正文和返回导航具有清楚的信息层级与阅读顺序。",
            "排版应便于阅读，文本宽度、字号、段落间距和留白不过度拥挤。",
            "结构能够迁移到其他业务，不以特殊视觉效果或难理解的领域操作作为主要价值。",
        ],
    },
    "media": {
        "title": "媒体内容选择标准",
        "items": [
            "能够快速辨认媒体类型及当前任务，例如浏览、播放、管理、搜索或选择内容。",
            "媒体条目应具有稳定结构，如标题、缩略图、时长、文件名或其他必要元数据。",
            "播放、搜索、分类、更多操作等主要命令位置明确，可用于生成交互约束。",
            "避免仅靠艳丽图标堆叠、缺少内容层级或无法看出核心操作的界面。",
        ],
    },
    "social": {
        "title": "社交与消息选择标准",
        "items": [
            "必须能明确辨认是个人中心、消息、联系人、动态、评论或通知等社交任务。",
            "用户身份、内容主体、时间状态和可执行操作之间层级清楚。",
            "导航和主要互动入口明确，能够支持 Agent 推导查看、回复、关注或管理流程。",
            "避免信息过度拥挤、主体用途不清、依赖私人内容或大幅人物照片才能成立的页面。",
        ],
    },
}


class ReviewParser(HTMLParser):
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
    selection_path = processed_root / "rico_replacement_selections.csv"
    inventory_path = processed_root / "rico_combined_inventory.csv"
    review_path = processed_root / "rico_replacement_final_review.html"

    selections = read_csv(selection_path)
    inventory = {row["screen_id"]: row for row in read_csv(inventory_path)}
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    verified: list[dict[str, str]] = []

    for selection in selections:
        screen_id = selection["screen_id"]
        inventory_row = inventory.get(screen_id)
        if not inventory_row:
            raise ValueError(f"screen_id missing from inventory: {screen_id}")
        relative = inventory_row["screenshot_path"]
        image_path = project_root / relative
        if not image_path.is_file() or image_path.stem != screen_id:
            raise ValueError(f"invalid image mapping for {screen_id}: {image_path}")
        with Image.open(image_path) as image:
            width, height = image.size
            image.verify()
        row = dict(selection)
        row.update(
            {
                "screenshot_path": relative,
                "width": str(width),
                "height": str(height),
                "sha256": sha256_file(image_path),
            }
        )
        grouped[selection["group_id"]].append(row)
        verified.append(row)

    sections: list[str] = []
    for group_id, rows in grouped.items():
        standard = GROUP_STANDARDS[group_id]
        standard_items = "".join(f"<li>{html.escape(item)}</li>" for item in standard["items"])
        cards: list[str] = []
        for row in rows:
            image_path = project_root / row["screenshot_path"]
            source = Path(os.path.relpath(image_path, review_path.parent)).as_posix()
            cards.append(
                f"""
                <article class="candidate" data-screen-id="{html.escape(row['screen_id'])}">
                  <div class="candidate-head">
                    <h3>Screen ID {html.escape(row['screen_id'])}</h3>
                    <label><input type="checkbox" value="{html.escape(row['screen_id'])}"> 符合本组标准</label>
                  </div>
                  <a class="image-link" href="{html.escape(source)}" title="打开原始 JPG {html.escape(row['screen_id'])}">
                    <img src="{html.escape(source)}" data-screen-id="{html.escape(row['screen_id'])}" alt="RICO screen {html.escape(row['screen_id'])}" loading="lazy">
                  </a>
                  <div class="description">
                    <strong>内容概括</strong>
                    <p>{html.escape(row['image_summary'])}</p>
                    <small>{html.escape(row['screenshot_path'])}<br>{row['width']} x {row['height']} · SHA {html.escape(row['sha256'][:12])}</small>
                  </div>
                </article>
                """
            )
        sections.append(
            f"""
            <section>
              <div class="standard">
                <div><h2>{html.escape(standard['title'])}</h2><p>以下标准仅根据 Req2Web 的 UI 检索与前端生成目标制定。</p></div>
                <ul>{standard_items}</ul>
              </div>
              <div class="grid">{''.join(cards)}</div>
            </section>
            """
        )

    document = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>RICO 替换图片最终核验</title>
  <style>
    :root {{ color-scheme: light; font-family: "Segoe UI", "Microsoft YaHei", sans-serif; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; color: #17202a; background: #f3f5f6; }}
    header {{ position: sticky; top: 0; z-index: 4; display: grid; grid-template-columns: minmax(0, 1fr) minmax(340px, 0.8fr); gap: 20px; padding: 14px 24px; background: #ffffff; border-bottom: 1px solid #c5cdd1; }}
    h1 {{ margin: 0; font-size: 22px; }}
    header p {{ margin: 5px 0 0; color: #52616b; font-size: 14px; }}
    .summary {{ display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 8px 12px; align-items: center; }}
    .summary strong {{ font-size: 18px; white-space: nowrap; }}
    #confirmed-ids {{ width: 100%; min-height: 38px; resize: none; border: 1px solid #98a6ad; padding: 8px; font: 14px Consolas, monospace; }}
    main {{ width: min(1500px, 100%); margin: 0 auto; padding: 20px 24px 44px; }}
    section {{ margin-bottom: 38px; }}
    .standard {{ display: grid; grid-template-columns: minmax(240px, 0.45fr) minmax(0, 1fr); gap: 20px; margin-bottom: 14px; padding: 16px; background: #ffffff; border-left: 5px solid #176b52; border-top: 1px solid #c9d2d5; border-right: 1px solid #c9d2d5; border-bottom: 1px solid #c9d2d5; }}
    .standard h2 {{ margin: 0; font-size: 20px; }}
    .standard p {{ margin: 6px 0 0; color: #617179; font-size: 13px; }}
    .standard ul {{ margin: 0; padding-left: 20px; line-height: 1.65; }}
    .grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }}
    .candidate {{ display: grid; grid-template-columns: minmax(190px, 44%) minmax(0, 1fr); grid-template-rows: auto minmax(420px, 520px); min-width: 0; background: #ffffff; border: 1px solid #c7d0d4; }}
    .candidate.confirmed {{ border: 3px solid #18845d; }}
    .candidate-head {{ grid-column: 1 / -1; display: flex; min-height: 50px; align-items: center; justify-content: space-between; gap: 12px; padding: 10px 12px; border-bottom: 1px solid #d7dde0; }}
    .candidate-head h3 {{ margin: 0; font-size: 17px; }}
    .candidate-head label {{ cursor: pointer; font-weight: 650; }}
    .candidate-head input {{ width: 19px; height: 19px; margin-right: 7px; vertical-align: middle; }}
    .image-link {{ display: block; min-width: 0; background: #edf0f1; }}
    img {{ display: block; width: 100%; height: 100%; object-fit: contain; }}
    .description {{ padding: 16px; line-height: 1.6; }}
    .description p {{ margin: 7px 0 18px; }}
    .description small {{ display: block; color: #5c6b72; font: 11px/1.55 Consolas, monospace; overflow-wrap: anywhere; }}
    @media (max-width: 1050px) {{ header {{ grid-template-columns: 1fr; }} .standard {{ grid-template-columns: 1fr; }} .grid {{ grid-template-columns: 1fr; }} }}
    @media (max-width: 680px) {{ header {{ position: static; padding: 12px 14px; }} main {{ padding: 14px; }} .candidate {{ grid-template-columns: minmax(145px, 42%) minmax(0, 1fr); grid-template-rows: auto minmax(360px, 460px); }} }}
  </style>
</head>
<body>
  <header>
    <div><h1>RICO 替换图片最终核验</h1><p>逐张对照本组标准。点击图片打开 combined 原始 JPG；确认后勾选“符合本组标准”。</p></div>
    <div class="summary"><strong>已确认 <span id="confirmed-count">0</span> / {len(verified)}</strong><textarea id="confirmed-ids" readonly aria-label="已确认的 Screen ID"></textarea></div>
  </header>
  <main>{''.join(sections)}</main>
  <script>
    const storageKey = 'rico-replacement-final-v1';
    const boxes = [...document.querySelectorAll('input[type="checkbox"]')];
    function update() {{
      const ids = boxes.filter(box => box.checked).map(box => box.value);
      boxes.forEach(box => box.closest('.candidate').classList.toggle('confirmed', box.checked));
      document.getElementById('confirmed-count').textContent = ids.length;
      document.getElementById('confirmed-ids').value = ids.join(', ');
      localStorage.setItem(storageKey, JSON.stringify(ids));
    }}
    boxes.forEach(box => box.addEventListener('change', update));
    try {{
      const saved = new Set(JSON.parse(localStorage.getItem(storageKey) || '[]'));
      boxes.forEach(box => box.checked = saved.has(box.value));
    }} catch (_) {{}}
    update();
  </script>
</body>
</html>
"""
    review_path.write_text(document, encoding="utf-8")

    parser = ReviewParser()
    parser.feed(review_path.read_text(encoding="utf-8"))
    valid = len(parser.images) == len(verified) and all(
        screen_id
        and source
        and (review_path.parent / source).resolve().is_file()
        and (review_path.parent / source).resolve().stem == screen_id
        for screen_id, source in parser.images
    )
    print(f"Final replacement images: {len(verified)}")
    print(f"HTML references valid: {valid}")
    print(f"Review page: {review_path}")
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
