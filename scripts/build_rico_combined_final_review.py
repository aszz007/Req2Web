from __future__ import annotations

import csv
import hashlib
import html
import os
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

from PIL import Image


CATEGORIES = {
    "login_auth": (
        "登录与认证",
        [
            "能够快速辨认登录、注册、身份验证或账号恢复任务。",
            "字段标签、输入控件和主要操作具有清楚的视觉层级。",
            "登录、注册、找回密码等主次入口关系明确。",
            "结构可迁移到常见产品，避免依赖大量品牌宣传内容。",
        ],
    ),
    "empty_error": (
        "空状态与异常状态",
        [
            "明确表达当前没有什么内容或发生了什么问题，并能与加载状态区分。",
            "保留页面标题或导航上下文，使状态所属业务区域可以判断。",
            "优先具有解释文字和下一步操作，帮助生成可恢复的交互。",
            "组内样本应覆盖不同业务状态，避免只替换文案的重复页面。",
        ],
    ),
    "settings": (
        "设置与账户配置",
        [
            "能够辨认偏好设置、账户配置或系统选项等任务。",
            "分组标题、配置项、当前状态和控件类型关系清楚。",
            "页面导航和进入子设置的路径明确。",
            "避免把普通内容列表或帮助文章误作设置页。",
        ],
    ),
    "map_location": (
        "地图与位置",
        [
            "位置查找、路线、地址或附近服务等任务目标明确。",
            "地图、列表、搜索、筛选和标记之间的关系容易理解。",
            "定位、切换视图、选择地点等核心操作入口清楚。",
            "布局可用于常见位置业务，不依赖难理解的专业领域背景。",
        ],
    ),
    "dashboard_data": (
        "数据面板",
        [
            "页面目标和核心指标能在较短时间内理解。",
            "至少包含汇总数值、趋势图、统计表或分组数据中的两类。",
            "图表、数字、标签和时间范围之间关系明确。",
            "避免只有空白容器、缺少数据上下文或装饰多于信息的界面。",
        ],
    ),
    "commerce": (
        "电商与交易",
        [
            "能够辨认商品浏览、购物车、订单或支付等交易任务。",
            "商品信息、价格、状态和主要行动按钮层级清楚。",
            "布局代表常见交易流程，可转化为页面与组件约束。",
            "避免只展示品牌广告、缺少交易上下文或同构重复的页面。",
        ],
    ),
    "messaging_social": (
        "社交与消息",
        [
            "能明确辨认个人中心、消息、联系人、动态、评论或通知任务。",
            "用户身份、内容主体、时间状态和可执行操作层级清楚。",
            "导航和主要互动入口明确，可推导查看、回复、关注或管理流程。",
            "避免信息过度拥挤、用途不清或依赖大幅人物照片才能成立的页面。",
        ],
    ),
    "search": (
        "搜索、筛选与结果",
        [
            "具有明确的关键词输入或筛选条件，并能看出它们与结果的关系。",
            "至少体现输入查询、选择条件、应用筛选或展示结果中的完整任务链路。",
            "搜索、清除、应用、重置和选择结果等操作入口明确。",
            "不能仅凭孤立的搜索图标或普通列表归入本类。",
        ],
    ),
    "form_input": (
        "表单与输入",
        [
            "表单填写目标明确，用户能够理解提交什么信息。",
            "字段标签、控件类型、必填关系和字段分组清楚。",
            "主要提交操作以及验证提示或步骤进度容易识别。",
            "组内应覆盖不同表单结构，避免用途过于生僻或字段含义不明。",
        ],
    ),
    "dialog_overlay": (
        "弹窗与浮层",
        [
            "前景弹窗与背景页面边界清楚，能够确认这是覆盖层而非完整页面。",
            "触发背景或当前任务上下文可以理解。",
            "确认、取消、拒绝或关闭等操作具有明确主次关系。",
            "优先选择可复用的模态模式，避免把普通页面误分类为弹窗。",
        ],
    ),
    "list_feed": (
        "列表与信息流",
        [
            "重复条目结构稳定，能够快速辨认列表或信息流任务。",
            "条目主体、辅助信息、状态和操作层级清楚。",
            "导航、筛选、新增或批量操作等相关入口容易识别。",
            "组内样本应有结构差异，避免只更换内容的同模板页面。",
        ],
    ),
    "media_content": (
        "媒体内容",
        [
            "能够快速辨认媒体类型及浏览、播放、管理、搜索或选择任务。",
            "媒体条目具有稳定结构，如标题、缩略图、时长、文件名或元数据。",
            "播放、搜索、分类和更多操作等主要命令位置明确。",
            "避免艳丽图标堆叠、缺少内容层级或核心操作不清的界面。",
        ],
    ),
    "navigation_home": (
        "首页与导航",
        [
            "能够明确辨认首页、主入口或主要导航页面的角色。",
            "信息架构、导航分组和主要业务入口层级清楚。",
            "页面覆盖产品的关键模块或高频任务，而非单一内容详情。",
            "避免仅偶然带有导航栏、但主体仍是普通内容页的样本。",
        ],
    ),
    "general": (
        "通用信息页面",
        [
            "用途可归纳为文章、帮助、说明、条款或知识内容等常见任务。",
            "标题、分节、正文和返回导航具有清楚的阅读顺序。",
            "排版易读，文本宽度、字号、段落间距和留白合理。",
            "结构能迁移到其他业务，不依赖特殊视觉效果或生僻操作。",
        ],
    ),
}

REPLACEMENT_CATEGORY = {
    "dashboard": "dashboard_data",
    "empty_state": "empty_error",
    "general": "general",
    "media": "media_content",
    "social": "messaging_social",
}

SECOND_REPLACED_IDS = {
    "4157",
    "12309",
    "2404",
    "54304",
    "603",
    "2702",
    "3001",
    "51494",
    "3938",
    "6962",
    "1386",
    "1294",
}


class ReviewParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.images: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "img":
            values = dict(attrs)
            self.images.append((values.get("data-screen-id", "") or "", values.get("src", "") or ""))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv_atomic(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    processed = root / "data/processed"
    output_csv = processed / "rico_combined_final_selection.csv"
    output_html = processed / "rico_combined_final_review.html"

    inventory = {row["screen_id"]: row for row in read_csv(processed / "rico_combined_inventory.csv")}
    rows: list[dict[str, str]] = []

    for candidate in read_csv(processed / "rico_candidate_pool.csv"):
        if candidate["manual_status"] != "keep":
            continue
        rows.append(
            {
                "category": candidate["recommended_category"],
                "screen_id": candidate["screen_id"],
                "image_summary": candidate["manual_notes"],
                "selection_source": "original_selection",
            }
        )

    for replacement in read_csv(processed / "rico_replacement_selections.csv"):
        rows.append(
            {
                "category": REPLACEMENT_CATEGORY[replacement["group_id"]],
                "screen_id": replacement["screen_id"],
                "image_summary": replacement["image_summary"],
                "selection_source": "replacement_selection",
            }
        )

    rows = [row for row in rows if row["screen_id"] not in SECOND_REPLACED_IDS]
    for row in rows:
        if row["screen_id"] == "18537":
            row["category"] = "search"
            row["selection_source"] = "moved_to_search_filter"

    for replacement in read_csv(processed / "rico_second_replacement_selections.csv"):
        rows.append(
            {
                "category": replacement["group_id"],
                "screen_id": replacement["screen_id"],
                "image_summary": replacement["image_summary"],
                "selection_source": "second_replacement_selection",
            }
        )

    seen: set[str] = set()
    verified: list[dict[str, str]] = []
    for row in rows:
        screen_id = row["screen_id"]
        if screen_id in seen:
            raise ValueError(f"duplicate screen_id: {screen_id}")
        seen.add(screen_id)
        inventory_row = inventory.get(screen_id)
        if not inventory_row:
            raise ValueError(f"screen_id missing from inventory: {screen_id}")
        relative = inventory_row["screenshot_path"]
        image_path = root / relative
        if not image_path.is_file() or image_path.stem != screen_id:
            raise ValueError(f"invalid image mapping: {screen_id} -> {image_path}")
        with Image.open(image_path) as image:
            width, height = image.size
            image.verify()
        verified.append(
            {
                **row,
                "category_label": CATEGORIES[row["category"]][0],
                "screenshot_path": relative,
                "image_width": str(width),
                "image_height": str(height),
                "sha256": sha256_file(image_path),
                "final_review_status": "pending",
                "final_review_notes": "",
            }
        )

    counts = Counter(row["category"] for row in verified)
    if len(verified) != 42 or set(counts) != set(CATEGORIES) or any(value != 3 for value in counts.values()):
        raise ValueError(f"expected 14 categories x 3 images, got {len(verified)} rows: {dict(counts)}")

    write_csv_atomic(
        output_csv,
        verified,
        [
            "category",
            "category_label",
            "screen_id",
            "image_summary",
            "selection_source",
            "screenshot_path",
            "image_width",
            "image_height",
            "sha256",
            "final_review_status",
            "final_review_notes",
        ],
    )

    sections: list[str] = []
    for category, (label, standards) in CATEGORIES.items():
        category_rows = [row for row in verified if row["category"] == category]
        standard_items = "".join(f"<li>{html.escape(item)}</li>" for item in standards)
        cards: list[str] = []
        for row in category_rows:
            image_path = root / row["screenshot_path"]
            source = Path(os.path.relpath(image_path, output_html.parent)).as_posix()
            cards.append(
                f"""
                <article class="candidate" data-screen-id="{row['screen_id']}">
                  <div class="candidate-head">
                    <h3>Screen ID {row['screen_id']}</h3>
                    <label><input type="checkbox" value="{row['screen_id']}"> 符合本类标准</label>
                  </div>
                  <a class="image-link" href="{html.escape(source)}" title="打开 combined 原始 JPG {row['screen_id']}">
                    <img src="{html.escape(source)}" data-screen-id="{row['screen_id']}" alt="RICO screen {row['screen_id']}" loading="lazy">
                  </a>
                  <div class="description">
                    <strong>内容概括</strong>
                    <p>{html.escape(row['image_summary'])}</p>
                    <small>{html.escape(row['screenshot_path'])}<br>{row['image_width']} x {row['image_height']} · SHA {row['sha256'][:12]}</small>
                  </div>
                </article>"""
            )
        sections.append(
            f"""
            <section data-category="{category}">
              <div class="standard">
                <div class="standard-title">
                  <div><h2>{html.escape(label)}</h2><p>只依据 Req2Web 的 UI 检索与前端生成目标制定，不随当前图片调整。</p></div>
                  <div class="group-actions"><strong>本类 <span class="group-count">0</span> / 3</strong><button type="button" class="select-group">本类全部通过</button></div>
                </div>
                <ol>{standard_items}</ol>
              </div>
              <div class="grid">{''.join(cards)}</div>
            </section>"""
        )

    document = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>RICO combined 第一版 42 张全量终审</title>
  <style>
    :root {{ color-scheme: light; font-family: "Segoe UI", "Microsoft YaHei", sans-serif; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; color: #17202a; background: #f3f5f6; }}
    header {{ position: sticky; top: 0; z-index: 4; display: grid; grid-template-columns: minmax(0, 1fr) minmax(360px, .8fr); gap: 20px; padding: 14px 24px; background: #fff; border-bottom: 1px solid #c5cdd1; }}
    h1 {{ margin: 0; font-size: 22px; }}
    header p {{ margin: 5px 0 0; color: #52616b; font-size: 14px; }}
    .summary {{ display: grid; grid-template-columns: auto minmax(0, 1fr) auto; gap: 8px 12px; align-items: center; }}
    .summary strong {{ font-size: 18px; white-space: nowrap; }}
    textarea {{ width: 100%; min-height: 40px; resize: none; border: 1px solid #98a6ad; padding: 8px; font: 13px Consolas, monospace; }}
    button {{ min-height: 36px; border: 1px solid #68777e; background: #fff; color: #17202a; padding: 7px 11px; cursor: pointer; font-weight: 650; }}
    button:hover {{ background: #edf2f1; }}
    main {{ width: min(1560px, 100%); margin: 0 auto; padding: 20px 24px 44px; }}
    section {{ margin-bottom: 40px; scroll-margin-top: 110px; }}
    .standard {{ margin-bottom: 14px; padding: 16px; background: #fff; border: 1px solid #c9d2d5; border-left: 5px solid #176b52; }}
    .standard-title {{ display: flex; align-items: center; justify-content: space-between; gap: 18px; margin-bottom: 10px; }}
    .standard h2 {{ margin: 0; font-size: 20px; }}
    .standard p {{ margin: 5px 0 0; color: #617179; font-size: 13px; }}
    .standard ol {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 5px 28px; margin: 0; padding-left: 23px; line-height: 1.55; }}
    .group-actions {{ display: flex; align-items: center; gap: 12px; white-space: nowrap; }}
    .grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }}
    .candidate {{ display: grid; grid-template-rows: auto minmax(440px, 58vh) auto; min-width: 0; background: #fff; border: 1px solid #c7d0d4; }}
    .candidate.confirmed {{ border: 3px solid #18845d; }}
    .candidate-head {{ display: flex; min-height: 50px; align-items: center; justify-content: space-between; gap: 10px; padding: 10px 12px; border-bottom: 1px solid #d7dde0; }}
    .candidate-head h3 {{ margin: 0; font-size: 16px; }}
    .candidate-head label {{ cursor: pointer; font-weight: 650; white-space: nowrap; }}
    .candidate-head input {{ width: 19px; height: 19px; margin-right: 6px; vertical-align: middle; }}
    .image-link {{ display: block; min-width: 0; background: #edf0f1; overflow: hidden; }}
    img {{ display: block; width: 100%; height: 100%; object-fit: contain; }}
    .description {{ padding: 13px; line-height: 1.55; border-top: 1px solid #d7dde0; }}
    .description p {{ min-height: 48px; margin: 6px 0 12px; }}
    .description small {{ display: block; color: #5c6b72; font: 11px/1.5 Consolas, monospace; overflow-wrap: anywhere; }}
    @media (max-width: 1150px) {{ header {{ grid-template-columns: 1fr; position: static; }} .grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} .standard ol {{ grid-template-columns: 1fr; }} }}
    @media (max-width: 720px) {{ header, main {{ padding: 14px; }} .summary {{ grid-template-columns: 1fr; }} .standard-title {{ align-items: flex-start; flex-direction: column; }} .grid {{ grid-template-columns: 1fr; }} .candidate {{ grid-template-rows: auto minmax(420px, 65vh) auto; }} }}
  </style>
</head>
<body>
  <header>
    <div><h1>RICO combined 第一版 42 张全量终审</h1><p>14 类各 3 张。逐张对照固定标准；点击图片会打开对应的 combined 原始 JPG。</p></div>
    <div class="summary"><strong>已确认 <span id="confirmed-count">0</span> / 42</strong><textarea id="confirmed-ids" readonly aria-label="已确认 Screen ID"></textarea><button type="button" id="clear-all">清空勾选</button></div>
  </header>
  <main>{''.join(sections)}</main>
  <script>
    const storageKey = 'rico-combined-final-review-v2';
    const boxes = [...document.querySelectorAll('input[type="checkbox"]')];
    function update() {{
      const ids = boxes.filter(box => box.checked).map(box => box.value);
      boxes.forEach(box => box.closest('.candidate').classList.toggle('confirmed', box.checked));
      document.querySelectorAll('section').forEach(section => {{
        const groupBoxes = [...section.querySelectorAll('input[type="checkbox"]')];
        section.querySelector('.group-count').textContent = groupBoxes.filter(box => box.checked).length;
      }});
      document.getElementById('confirmed-count').textContent = ids.length;
      document.getElementById('confirmed-ids').value = ids.join(', ');
      localStorage.setItem(storageKey, JSON.stringify(ids));
    }}
    boxes.forEach(box => box.addEventListener('change', update));
    document.querySelectorAll('.select-group').forEach(button => button.addEventListener('click', () => {{
      button.closest('section').querySelectorAll('input[type="checkbox"]').forEach(box => box.checked = true);
      update();
    }}));
    document.getElementById('clear-all').addEventListener('click', () => {{ boxes.forEach(box => box.checked = false); update(); }});
    try {{
      const saved = new Set(JSON.parse(localStorage.getItem(storageKey) || '[]'));
      boxes.forEach(box => box.checked = saved.has(box.value));
    }} catch (_) {{}}
    update();
  </script>
</body>
</html>
"""
    output_html.write_text(document, encoding="utf-8")

    parser = ReviewParser()
    parser.feed(document)
    valid = len(parser.images) == 42 and all(
        screen_id
        and source
        and (output_html.parent / source).resolve().is_file()
        and (output_html.parent / source).resolve().stem == screen_id
        for screen_id, source in parser.images
    )
    print(f"Final review rows: {len(verified)}")
    print(f"Category counts: {dict(counts)}")
    print(f"HTML image mappings valid: {valid}")
    print(f"Selection CSV: {output_csv}")
    print(f"Review page: {output_html}")
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
