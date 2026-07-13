from __future__ import annotations

import csv
import hashlib
import html
import os
import re
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from PIL import Image


CATEGORIES = {
    "form_input": "表单与输入",
    "data_dashboard": "数据、目录与表格",
    "commerce": "商品与交易",
    "article_content": "文章与内容",
    "media_gallery": "图片与媒体",
    "general_landing": "通用页面与导航",
}

CATEGORY_STANDARDS = {
    "form_input": "页面应以登录、申请、联系或反馈等输入任务为核心，字段和提交路径清楚。",
    "data_dashboard": "页面应以结构化数据、日程、目录、排名或报告为核心，信息便于扫描和比较。",
    "commerce": "页面应明确呈现商品、价格、购物车或购买动作，交易意图容易理解。",
    "article_content": "页面应以文章或说明内容为主体，标题、正文和阅读层级完整。",
    "media_gallery": "页面应以摄影、艺术或影音作品为主体，媒体浏览结构清楚。",
    "general_landing": "页面应清楚介绍个人、企业或服务，并具有典型的首页信息层级和导航。",
}

# Automatic labels remain in the inventory as audit evidence. This fixed shortlist
# was additionally checked against each HTML title and body before visual review.
CURATED_STANDARD = {
    "form_input": [
        ("10486", "登录表单", "公益机构登录页，包含账号、密码、登录操作和辅助入口。"),
        ("11707", "登录表单", "客户旅程分析平台登录页，包含账号认证和密码重置入口。"),
        ("14423", "申请表单", "内容生产者申请页，包含多组身份、业务和联系方式字段。"),
        ("10564", "支持请求表单", "软件服务支持请求页，用于填写问题信息并提交技术支持工单。"),
        ("988", "反馈表单", "网站反馈页，包含评价、意见和联系方式等输入项。"),
        ("3501", "联系表单", "印刷服务联系页，包含公司信息、咨询字段和提交操作。"),
    ],
    "data_dashboard": [
        ("3747", "活动日程", "舞蹈博物馆活动日程页，以日期和活动条目组织计划信息。"),
        ("16526", "站点资料", "全球冰冻圈观测站信息页，以字段和表格展示站点资料。"),
        ("9326", "人员目录", "高校计算机系博士校友目录，以姓名和个人信息组织人员列表。"),
        ("10297", "项目归档", "乡村项目归档目录，按项目和年份组织历史记录。"),
        ("10824", "排名信息", "律师事务所排名页，按奖项和年份组织排名信息。"),
        ("13772", "定期报告", "水库钓鱼报告页，以日期、鱼情和报告条目组织观测信息。"),
    ],
    "commerce": [
        ("10303", "商品目录", "复活节礼篮商店页，展示多种礼篮商品和购买入口。"),
        ("2997", "购物车", "购物车页面，集中展示已选商品、数量、价格和结算操作。"),
        ("15385", "促销商品", "有机戒指促销页，包含商品展示、价格和销售操作。"),
        ("14347", "服装商店", "服装商店页面，以商品图片、分类和购买入口组织内容。"),
        ("58", "商品详情", "钥匙扣商品详情页，展示产品信息、价格和购买选项。"),
        ("10685", "玩具商店", "动画玩具销售页，展示商品列表、价格和交易入口。"),
    ],
    "article_content": [
        ("13625", "教育博客", "在线教育博客页，以文章标题、摘要和阅读入口组织内容。"),
        ("1037", "技术文章", "Consul 技术博客文章页，包含标题、正文和代码相关说明。"),
        ("6820", "指南文章", "古董锡器鉴别指南，以分段正文和配图讲解主题。"),
        ("8723", "操作说明", "报告权限操作说明页，以步骤和长文本解释配置方法。"),
        ("16888", "发布说明", "软件版本发布文章，集中介绍更新内容和相关说明。"),
        ("4512", "产品评测", "iPhone 软件与应用评测页，以正文和评价信息为主体。"),
    ],
    "media_gallery": [
        ("2504", "摄影作品集", "摄影师 Bruce Hucko 的作品展示页，以照片浏览为核心。"),
        ("13312", "个人作品集", "Dale Johnson 作品集页面，以项目图片和作品导航组织内容。"),
        ("13872", "演出摄影", "音乐会摄影作品页，以现场照片和摄影项目为主体。"),
        ("13412", "艺术作品集", "Graham Llewellyn 艺术展示页，以绘画作品和分类浏览为核心。"),
        ("3876", "艺术画廊", "Zans 艺术作品页，以多幅作品图片构成画廊式浏览。"),
        ("2095", "音乐软件展示", "键盘音乐软件展示页，围绕音乐产品和媒体内容组织页面。"),
    ],
    "general_landing": [
        ("15864", "产品落地页", "电子健康记录解决方案首页，介绍产品价值、能力和访问入口。"),
        ("17633", "企业服务首页", "软件与网站开发机构首页，介绍服务、案例和联系入口。"),
        ("2387", "设计机构首页", "建筑与设计事务所首页，展示机构定位、项目和导航。"),
        ("5672", "本地服务首页", "清洁服务公司首页，介绍服务范围、优势和咨询入口。"),
        ("10942", "商业解决方案", "商业解决方案首页，按能力和服务模块组织公司信息。"),
        ("1009", "个人主页", "软件工程师个人首页，展示个人简介、技能和内容入口。"),
    ],
}

CURATED_HARD = [
    ("g37", "复杂构建器", "游戏角色构建器，包含大量属性、装备和计算控件。"),
    ("g33", "配置生成器", "Godot 构建选项生成器，包含密集配置项和生成结果。"),
    ("g78", "实时数据页", "YouTube 实时订阅数页面，包含搜索、实时数字和频道信息。"),
    ("g36", "大型数据表", "Hadoop 生态系统对照表，以大型矩阵组织组件和类别。"),
    ("g59", "交易应用", "去中心化交易前端，包含资产、交易操作和状态区域。"),
    ("g51", "内容应用", "在线笔记板应用，包含导航、内容编辑和板块组织。"),
    ("g48", "博客首页", "Android 开发者博客首页，包含多篇文章卡片和导航。"),
    ("g61", "技术文档", "编程语言设计文档站，包含章节目录和长篇技术内容。"),
    ("g5", "项目排行榜", "中国开源项目排行榜，以排名、项目和指标组织数据。"),
    ("g44", "数据目录", "网络服务数据收集目录，按服务展示隐私数据条目。"),
    ("g67", "会议网站", "Linux 技术会议网站，包含议程、讲者和会议信息。"),
    ("g53", "开源项目站", "OpenIPC 开源固件项目站，包含产品介绍、文档和社区入口。"),
]


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: Counter[str] = Counter()
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.attribute_parts: list[str] = []
        self.style_chars = 0
        self.in_title = False
        self.in_style = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags[tag.lower()] += 1
        self.in_title = tag.lower() == "title"
        self.in_style = tag.lower() == "style"
        for name, value in attrs:
            if value and name.lower() in {"id", "class", "name", "type", "placeholder", "aria-label", "alt"}:
                self.attribute_parts.append(value)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self.in_title = False
        if tag.lower() == "style":
            self.in_style = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data.strip())
        if self.in_style:
            self.style_chars += len(data)
        stripped = " ".join(data.split())
        if stripped and len(self.text_parts) < 400:
            self.text_parts.append(stripped)


def write_csv_atomic(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def image_info(path: Path) -> tuple[bool, int, int, str, str]:
    try:
        with Image.open(path) as image:
            width, height = image.size
            mode = image.mode
            image.verify()
        with Image.open(path) as image:
            grayscale = image.convert("L").resize((9, 8))
            pixels = list(grayscale.get_flattened_data())
        value = 0
        for row in range(8):
            for column in range(8):
                value = (value << 1) | int(pixels[row * 9 + column] > pixels[row * 9 + column + 1])
        return True, width, height, mode, f"{value:016x}"
    except (OSError, ValueError):
        return False, 0, 0, "", ""


def term_score(context: str, terms: tuple[str, ...], weight: int) -> int:
    return sum(weight * len(re.findall(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", context)) for term in terms)


def classify(parser: PageParser) -> dict[str, Any]:
    tags = parser.tags
    text = " ".join(parser.text_parts).lower()
    title = " ".join(parser.title_parts).lower()
    attributes = " ".join(parser.attribute_parts).lower()
    context = f" {title} {text[:10000]} {attributes[:5000]} "
    form_fields = tags["input"] + tags["select"] + tags["textarea"]
    scores = {
        "form_input": (
            tags["form"] * 5
            + form_fields * 4
            + term_score(context, ("sign in", "log in", "register", "create account", "contact us", "book now", "reservation", "submit", "password", "message"), 5)
        ),
        "data_dashboard": (
            tags["table"] * 9
            + tags["canvas"] * 7
            + term_score(context, ("dashboard", "analytics", "statistics", "report", "metrics", "admin panel", "data table", "chart"), 7)
        ),
        "commerce": term_score(
            context,
            ("product", "products", "shop", "shopping", "store", "cart", "checkout", "buy now", "add to cart", "order", "sale", "pricing", "menu"),
            5,
        ),
        "article_content": (
            tags["article"] * 9
            + min(len(text) // 900, 7)
            + term_score(context, ("article", "blog", "news", "read more", "posted by", "author", "story", "documentation", "chapter"), 5)
        ),
        "media_gallery": (
            tags["video"] * 9
            + tags["audio"] * 9
            + max(0, tags["img"] - 3) * 2
            + term_score(context, ("gallery", "portfolio", "photography", "photos", "video", "music", "album", "playlist", "creative work"), 6)
        ),
        "general_landing": (
            tags["nav"] * 4
            + tags["header"] * 3
            + tags["section"] * 2
            + term_score(context, ("features", "services", "about us", "our team", "get started", "learn more", "welcome", "home"), 3)
        ),
    }
    if form_fields < 2 and not term_score(context, ("sign in", "log in", "register", "contact us", "reservation"), 1):
        scores["form_input"] = min(scores["form_input"], 3)
    ranked = sorted(scores.items(), key=lambda item: (-item[1], list(CATEGORIES).index(item[0])))
    category, top_score = ranked[0]
    margin = top_score - ranked[1][1]

    subtype = "通用页面"
    if category == "form_input":
        subtype = "登录或注册表单" if any(term in context for term in ("sign in", "log in", "register", "password")) else "联系与咨询表单" if any(term in context for term in ("contact", "message", "inquiry")) else "预约或预订表单" if any(term in context for term in ("book now", "reservation", "appointment")) else "信息填写表单"
    elif category == "data_dashboard":
        subtype = "数据仪表盘" if any(term in context for term in ("dashboard", "analytics", "metrics", "statistics")) else "表格与数据列表"
    elif category == "commerce":
        subtype = "商品详情或商品目录" if any(term in context for term in ("product", "shop", "store", "cart")) else "价格与交易页面"
    elif category == "article_content":
        subtype = "新闻或博客文章" if any(term in context for term in ("news", "blog", "article", "posted by")) else "长文本说明页面"
    elif category == "media_gallery":
        subtype = "作品集或图片画廊" if any(term in context for term in ("gallery", "portfolio", "photography", "photos")) else "视频或音频内容页"
    elif category == "general_landing":
        subtype = "企业或服务落地页" if any(term in context for term in ("services", "features", "about us", "our team")) else "首页与导航页面"

    display_title = " ".join(parser.title_parts).strip() or next((part for part in parser.text_parts if 3 <= len(part) <= 100), "未命名页面")
    display_title = display_title[:90]
    summary = f"这是一个“{display_title}”相关的{subtype}，可用于参考其页面分区、内容层级和前端组件组织。"
    return {
        "category": category,
        "score": top_score,
        "margin": margin,
        "subtype": subtype,
        "summary": summary,
        "text_preview": " | ".join(parser.text_parts[:12])[:800],
        "all_scores": ";".join(f"{key}={value}" for key, value in scores.items()),
    }


def audit_subset(root: Path, project_root: Path, dataset: str, subset: str) -> list[dict[str, Any]]:
    html_files = {path.stem: path for path in root.glob("*.html") if path.is_file()}
    png_files = {path.stem: path for path in root.glob("*.png") if path.is_file()}
    rows: list[dict[str, Any]] = []
    for sample_id in sorted(set(html_files) | set(png_files)):
        html_path = html_files.get(sample_id)
        png_path = png_files.get(sample_id)
        parser = PageParser()
        html_valid = False
        html_bytes = b""
        if html_path:
            try:
                html_bytes = html_path.read_bytes()
                parser.feed(html_bytes.decode("utf-8", errors="replace"))
                html_valid = bool(parser.tags["html"] and parser.tags["body"])
            except (OSError, UnicodeError, ValueError):
                html_valid = False
        image_valid, width, height, mode, dhash = image_info(png_path) if png_path else (False, 0, 0, "", "")
        controls = sum(parser.tags[tag] for tag in ("input", "select", "textarea", "button", "form"))
        complexity = (
            sum(parser.tags.values())
            + min(parser.style_chars // 80, 300)
            + parser.tags["script"] * 8
            + controls * 4
            + parser.tags["table"] * 8
            + parser.tags["img"] * 2
        )
        classification = classify(parser)
        quality_score = (
            min(sum(parser.tags.values()), 180)
            + min(parser.style_chars // 100, 120)
            + (12 if " ".join(parser.title_parts).strip() else 0)
            + min(int(classification["score"]), 80)
            + min(int(classification["margin"]), 30)
        )
        rows.append(
            {
                "dataset": dataset,
                "subset": subset,
                "sample_id": sample_id,
                "html_path": html_path.relative_to(project_root).as_posix() if html_path else "",
                "png_path": png_path.relative_to(project_root).as_posix() if png_path else "",
                "pair_status": "complete" if html_path and png_path else "incomplete",
                "html_bytes": len(html_bytes),
                "png_bytes": png_path.stat().st_size if png_path else 0,
                "html_valid": html_valid,
                "image_valid": image_valid,
                "image_width": width,
                "image_height": height,
                "image_mode": mode,
                "title": " ".join(parser.title_parts).strip(),
                "tag_count": sum(parser.tags.values()),
                "style_chars": parser.style_chars,
                "script_count": parser.tags["script"],
                "form_control_count": controls,
                "table_count": parser.tags["table"],
                "nav_count": parser.tags["nav"],
                "image_tag_count": parser.tags["img"],
                "link_count": parser.tags["a"],
                "complexity_score": complexity,
                "quality_score": quality_score,
                "layout_category": classification["category"],
                "category_score": classification["score"],
                "category_margin": classification["margin"],
                "page_subtype": classification["subtype"],
                "content_summary": classification["summary"],
                "category_scores": classification["all_scores"],
                "text_preview": classification["text_preview"],
                "image_dhash": dhash,
                "html_sha256": sha256_bytes(html_bytes) if html_bytes else "",
            }
        )
    duplicate_counts = Counter(row["image_dhash"] for row in rows if row["image_dhash"])
    for row in rows:
        row["same_image_hash_count"] = duplicate_counts.get(row["image_dhash"], 0)
    return rows


def content_candidates(rows: list[dict[str, Any]], count: int, prefer_complex: bool = False) -> list[dict[str, Any]]:
    ranked = sorted(
        rows,
        key=lambda row: (
            -int(row["category_score"]),
            -int(row["category_margin"]),
            -int(row["quality_score"]),
            -int(row["complexity_score"]) if prefer_complex else int(row["same_image_hash_count"]),
            row["sample_id"],
        ),
    )
    chosen: list[dict[str, Any]] = []
    hashes: set[str] = set()
    title_signatures: set[str] = set()
    for row in ranked:
        signature = re.sub(r"[^a-z0-9]+", " ", str(row["title"]).lower()).strip()[:45]
        if row["image_dhash"] in hashes or signature and signature in title_signatures:
            continue
        chosen.append(row)
        hashes.add(row["image_dhash"])
        if signature:
            title_signatures.add(signature)
        if len(chosen) == count:
            break
    if len(chosen) < count:
        for row in ranked:
            if row["image_dhash"] in hashes:
                continue
            chosen.append(row)
            hashes.add(row["image_dhash"])
            if len(chosen) == count:
                break
    return chosen


def main() -> int:
    project_root = Path(__file__).resolve().parent.parent
    processed = project_root / "data/processed"
    inventory_path = processed / "design2code_inventory.csv"
    candidate_path = processed / "design2code_review_candidates.csv"
    review_path = processed / "design2code_review.html"

    rows = audit_subset(
        project_root / "data/raw/design2code/Design2Code", project_root, "design2code", "standard"
    )
    rows.extend(
        audit_subset(
            project_root / "data/raw/design2code_hard/Design2Code-HARD",
            project_root,
            "design2code_hard",
            "hard",
        )
    )
    fields = list(rows[0])
    write_csv_atomic(inventory_path, rows, fields)

    valid = [
        row
        for row in rows
        if row["pair_status"] == "complete" and row["html_valid"] and row["image_valid"]
    ]
    by_sample = {(row["subset"], row["sample_id"]): row for row in valid}
    candidates: list[dict[str, Any]] = []
    for category, curated_rows in CURATED_STANDARD.items():
        for index, (sample_id, subtype, summary) in enumerate(curated_rows, 1):
            row = by_sample[("standard", sample_id)]
            item = dict(row)
            item["review_group"] = category
            item["review_id"] = f"D2C-{category[:3].upper()}-{index:02d}"
            item["required_count"] = "2"
            item["review_status"] = "pending"
            item["screening_method"] = "html_semantic_review"
            item["page_subtype"] = subtype
            item["content_summary"] = summary
            candidates.append(item)

    for index, (sample_id, subtype, summary) in enumerate(CURATED_HARD, 1):
        row = by_sample[("hard", sample_id)]
        item = dict(row)
        item["review_group"] = "hard"
        item["review_id"] = f"HARD-{index:02d}"
        item["required_count"] = "4"
        item["review_status"] = "pending"
        item["screening_method"] = "html_semantic_review"
        item["page_subtype"] = subtype
        item["content_summary"] = summary
        candidates.append(item)
    candidate_fields = ["review_id", "review_group", "required_count", "review_status", "screening_method"] + fields
    write_csv_atomic(candidate_path, candidates, candidate_fields)

    sections: list[str] = []
    groups = [(key, label, 2) for key, label in CATEGORIES.items()] + [("hard", "Design2Code-HARD 压力测试", 4)]
    for group_id, label, quota in groups:
        cards: list[str] = []
        for row in (item for item in candidates if item["review_group"] == group_id):
            image_source = Path(os.path.relpath(project_root / row["png_path"], review_path.parent)).as_posix()
            html_source = Path(os.path.relpath(project_root / row["html_path"], review_path.parent)).as_posix()
            cards.append(
                f"""<article class="candidate"><div class="card-head"><h3>{html.escape(row['review_id'])} · {html.escape(row['sample_id'])}</h3><label><input type="checkbox" value="{html.escape(row['review_id'])}"> 选择</label></div><a class="image-link" href="{html.escape(image_source)}"><img src="{html.escape(image_source)}" alt="{html.escape(row['review_id'])}" loading="lazy"></a><div class="description"><strong>{html.escape(row['page_subtype'])}</strong><p class="summary-text">{html.escape(row['content_summary'])}</p><p>原始标题：{html.escape(row['title'] or '无标题')}<br>已通过 HTML 标题、正文与组件语义初筛 · HTML 标签 {row['tag_count']} · 复杂度 {row['complexity_score']}</p><a href="{html.escape(html_source)}">打开配对 HTML</a></div></article>"""
            )
        standard = CATEGORY_STANDARDS[group_id] if group_id != "hard" else "选择视觉结构复杂但仍能理解的页面，用于测试模型处理密集布局、复杂样式和多区域结构的能力。"
        sections.append(
            f"""<section data-quota="{quota}"><div class="group-head"><div><h2>{html.escape(label)}</h2><p>{html.escape(standard)}</p></div><strong>请选择 {quota} 张 · 已选 <span class="group-count">0</span> / {quota}</strong></div><div class="grid">{''.join(cards)}</div></section>"""
        )

    total_required = sum(quota for _, _, quota in groups)
    document = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Design2Code 人工挑选</title><style>
:root{{font-family:"Segoe UI","Microsoft YaHei",sans-serif;color:#17202a}}*{{box-sizing:border-box}}body{{margin:0;background:#f3f5f6}}header{{position:sticky;top:0;z-index:5;display:grid;grid-template-columns:minmax(0,1fr) minmax(430px,.8fr);gap:18px;padding:14px 24px;background:#fff;border-bottom:1px solid #bcc7cc}}h1{{margin:0;font-size:22px}}header p{{margin:5px 0 0;color:#596970}}.summary{{display:grid;grid-template-columns:auto 1fr auto;gap:9px;align-items:center}}textarea{{width:100%;min-height:68px;border:1px solid #8e9da4;padding:7px;font:12px Consolas,monospace}}button{{min-height:36px;border:1px solid #68777e;background:#fff;padding:7px 11px}}main{{width:min(1560px,100%);margin:auto;padding:20px 24px 44px}}section{{margin-bottom:42px}}.group-head{{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-bottom:12px;padding:14px;background:#fff;border:1px solid #c6d0d4;border-left:5px solid #176b52}}.group-head.done{{border-left-color:#18845d}}.group-head.over{{border-left-color:#b33a2f}}h2{{margin:0}}.group-head p{{margin:5px 0 0;color:#5d6d74}}.grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}}.candidate{{display:grid;grid-template-rows:auto minmax(390px,54vh) auto;background:#fff;border:1px solid #c5cfd3;min-width:0}}.candidate.selected{{border:3px solid #18845d}}.card-head{{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:10px 12px;border-bottom:1px solid #d5dcdf}}h3{{margin:0;font-size:15px}}label{{font-weight:700;white-space:nowrap}}input{{width:18px;height:18px;vertical-align:middle}}.image-link{{display:block;background:#e8edef;overflow:hidden}}img{{display:block;width:100%;height:100%;object-fit:contain}}.description{{padding:12px;line-height:1.5}}.description p{{margin:6px 0;font-size:12px;color:#58686f}}.description .summary-text{{min-height:54px;color:#27363c;font-size:13px}}@media(max-width:1150px){{header{{position:static;grid-template-columns:1fr}}.grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}@media(max-width:680px){{header,main{{padding:14px}}.summary{{grid-template-columns:1fr}}.group-head{{align-items:flex-start;flex-direction:column}}.grid{{grid-template-columns:1fr}}}}</style></head><body>
<header><div><h1>Design2Code 内容初筛后的人工挑选</h1><p>候选已通过 HTML 内容判定和语义复核；你只需判断截图是否视觉典型。普通集每类从 6 个中选 2 个，HARD 从 12 个中选 4 个。</p></div><div class="summary"><strong>已选 <span id="total">0</span> / {total_required}</strong><textarea id="result" readonly></textarea><button id="clear" type="button">清空</button></div></header><main>{''.join(sections)}</main>
<script>const key='design2code-review-v4';const boxes=[...document.querySelectorAll('input[type="checkbox"]')];function update(){{let total=0;const lines=[];document.querySelectorAll('section').forEach(section=>{{const quota=Number(section.dataset.quota);const picked=[...section.querySelectorAll('input:checked')];total+=picked.length;section.querySelector('.group-count').textContent=picked.length;const head=section.querySelector('.group-head');head.classList.toggle('done',picked.length===quota);head.classList.toggle('over',picked.length>quota);lines.push(`${{section.querySelector('h2').textContent}}: ${{picked.map(x=>x.value).join(', ')}}`);}});boxes.forEach(x=>x.closest('.candidate').classList.toggle('selected',x.checked));document.getElementById('total').textContent=total;document.getElementById('result').value=lines.join('\\n');localStorage.setItem(key,JSON.stringify(boxes.filter(x=>x.checked).map(x=>x.value)));}}boxes.forEach(x=>x.addEventListener('change',update));document.getElementById('clear').addEventListener('click',()=>{{boxes.forEach(x=>x.checked=false);update();}});try{{const saved=new Set(JSON.parse(localStorage.getItem(key)||'[]'));boxes.forEach(x=>x.checked=saved.has(x.value));}}catch(_){{}}update();</script></body></html>"""
    review_path.write_text(document, encoding="utf-8")

    valid_count = len(valid)
    print(f"Inventory rows: {len(rows)}")
    print(f"Valid pairs: {valid_count}")
    print(f"Invalid/incomplete: {len(rows) - valid_count}")
    print(f"Review candidates: {len(candidates)}")
    print(f"Standard categories: {dict(Counter(row['layout_category'] for row in rows if row['subset'] == 'standard'))}")
    print(f"Inventory: {inventory_path}")
    print(f"Review page: {review_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
