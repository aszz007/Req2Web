from __future__ import annotations

import csv
import html
import json
import os
from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path

from PIL import Image


PATTERNS = {
    "tap_short": ("短点击流程", "2-3 步连续点击，适合单一功能入口或简单页面跳转。", "TS"),
    "tap_medium": ("中等点击流程", "4-6 步连续点击，适合完整但不冗长的功能操作。", "TM"),
    "swipe_short": ("短滑动流程", "2-3 步以滑动为主，画面变化应能体现浏览或翻页。", "SS"),
    "swipe_medium": ("中等滑动流程", "4-6 步以滑动为主，适合连续浏览较长内容。", "SM"),
    "mixed_short": ("短混合流程", "2-3 步同时包含点击和滑动，任务目标应容易理解。", "MS"),
    "mixed_medium": ("中等混合流程", "4-6 步同时包含点击和滑动，状态变化应连贯。", "MM"),
}

NOISE_TOKENS = (
    "ads.",
    "adactivity",
    "unity3d",
    "browseractivity",
    "chooseractivity",
    "resolveractivity",
    "packageinstaller",
)


class ReviewParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.images: list[tuple[str, str]] = []
        self.cards = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "article" and "trace-card" in (values.get("class") or ""):
            self.cards += 1
        if tag == "img":
            self.images.append((values.get("data-step-id", "") or "", values.get("src", "") or ""))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv_atomic(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def quality_score(row: dict[str, str]) -> tuple[int, list[str]]:
    activities = row["activity_names"].lower()
    noise = [token for token in NOISE_TOKENS if token in activities]
    score = int(row["candidate_score"]) * 10
    score += int(row["distinct_image_count"]) * 3
    score += int(row["screen_annotation_link_count"])
    score -= len(noise) * 35
    return score, noise


def normalized_point(value: object) -> tuple[float, float] | None:
    if not isinstance(value, list) or len(value) < 2:
        return None
    try:
        x = min(1.0, max(0.0, float(value[0])))
        y = min(1.0, max(0.0, float(value[1])))
    except (TypeError, ValueError):
        return None
    return x, y


def gesture_markup(points: object, terminal: bool = False) -> tuple[str, str]:
    valid = [point for value in points if (point := normalized_point(value))] if isinstance(points, list) else []
    if len(valid) == 1:
        x, y = valid[0]
        overlay = f'<span class="tap-marker" style="left:{x * 100:.3f}%;top:{y * 100:.3f}%" title="点击坐标 {x:.3f}, {y:.3f}"><b>点</b></span>'
        return overlay, f"点击位置 ({x * 100:.1f}%, {y * 100:.1f}%)"
    if len(valid) >= 2:
        coordinates = " ".join(f"{x * 1000:.2f},{y * 1000:.2f}" for x, y in valid)
        start_x, start_y = valid[0]
        end_x, end_y = valid[-1]
        overlay = (
            '<svg class="swipe-overlay" viewBox="0 0 1000 1000" preserveAspectRatio="none" aria-hidden="true">'
            f'<polyline points="{coordinates}" />'
            f'<circle class="swipe-start" cx="{start_x * 1000:.2f}" cy="{start_y * 1000:.2f}" r="18" />'
            f'<circle class="swipe-end" cx="{end_x * 1000:.2f}" cy="{end_y * 1000:.2f}" r="22" />'
            '</svg>'
        )
        label = f"滑动 ({start_x * 100:.1f}%, {start_y * 100:.1f}%) → ({end_x * 100:.1f}%, {end_y * 100:.1f}%)"
        return overlay, label
    if terminal:
        return '<span class="gesture-missing gesture-terminal">终点</span>', "流程终点，无需执行动作"
    return '<span class="gesture-missing">无坐标</span>', "未找到有效手势坐标"


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    processed = root / "data/processed"
    source_path = processed / "rico_trace_candidate_pool.csv"
    output_csv = processed / "rico_trace_review_candidates.csv"
    output_html = processed / "rico_trace_review.html"

    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in read_csv(source_path):
        score, noise = quality_score(row)
        row = dict(row)
        row["auto_quality_score"] = str(score)
        row["noise_flags"] = "|".join(noise)
        grouped[row["recommended_pattern"]].append(row)

    selected: list[dict[str, str]] = []
    for pattern, (_, _, prefix) in PATTERNS.items():
        ranked = sorted(
            grouped[pattern],
            key=lambda row: (-int(row["auto_quality_score"]), row["sample_id"]),
        )[:6]
        for index, row in enumerate(ranked, 1):
            row["review_id"] = f"{prefix}-{index:02d}"
            row["review_status"] = "pending"
            selected.append(row)

    if len(selected) != 36 or len({row["sample_id"] for row in selected}) != 36:
        raise ValueError("expected 36 unique trace review candidates")
    fields = ["review_id", "auto_quality_score", "noise_flags", "review_status"] + list(read_csv(source_path)[0])
    write_csv_atomic(output_csv, selected, fields)

    sections: list[str] = []
    for pattern, (label, standard, _) in PATTERNS.items():
        cards: list[str] = []
        for row in (item for item in selected if item["recommended_pattern"] == pattern):
            screenshots = row["screenshot_paths"].split("|")
            screen_ids = row["screen_ids"].split("|")
            gestures = row["gesture_types"].split("|")
            gesture_path = root / row["trace_path"] / "gestures.json"
            with gesture_path.open("r", encoding="utf-8") as stream:
                gesture_points = json.load(stream)
            steps: list[str] = []
            for index, screenshot in enumerate(screenshots):
                absolute = root / screenshot
                source = Path(os.path.relpath(absolute, output_html.parent)).as_posix()
                gesture = gestures[index] if index < len(gestures) else "unknown"
                screen_id = screen_ids[index] if index < len(screen_ids) else str(index + 1)
                with Image.open(absolute) as image:
                    width, height = image.size
                    image.verify()
                terminal = index == len(screenshots) - 1
                points = gesture_points.get(screen_id, [])
                overlay, coordinate_label = gesture_markup(points, terminal=terminal)
                if terminal:
                    transition_label = "最后记录动作，后续画面未包含" if points else "该画面是流程终点"
                else:
                    transition_label = "当前图执行动作后进入右侧下一图"
                steps.append(
                    f"""<figure><div class="image-stage" style="aspect-ratio:{width}/{height}"><a href="{html.escape(source)}"><img src="{html.escape(source)}" data-step-id="{html.escape(screen_id)}" alt="{html.escape(row['review_id'])} step {index + 1}" loading="lazy"></a>{overlay}</div><figcaption><strong>步骤 {index + 1} · {html.escape(gesture)}</strong><span>{html.escape(coordinate_label)}</span><small>{html.escape(transition_label)} · screen {html.escape(screen_id)}</small></figcaption></figure>"""
                )
            noise_text = "无明显广告或系统跳转信号" if not row["noise_flags"] else f"自动降权信号：{row['noise_flags']}"
            cards.append(
                f"""<article class="trace-card" data-sample-id="{html.escape(row['sample_id'])}">
                <div class="card-head"><div><h3>{html.escape(row['review_id'])}</h3><code>{html.escape(row['sample_id'])}</code></div><label><input type="checkbox" value="{html.escape(row['review_id'])}"> 选择此流程</label></div>
                <div class="steps">{''.join(steps)}</div>
                <div class="meta"><span>{row['step_count']} 步</span><span>{row['distinct_image_count']} 个不同画面</span><span>{html.escape(row['gesture_types'])}</span><span>{html.escape(noise_text)}</span></div>
                </article>"""
            )
        sections.append(
            f"""<section data-pattern="{pattern}" data-quota="2"><div class="group-head"><div><h2>{html.escape(label)}</h2><p>{html.escape(standard)}选择时还要确认流程用途容易理解、前后状态连续、无广告劫持。</p></div><strong>请选择 2 条 · 已选 <span class="group-count">0</span> / 2</strong></div><div class="trace-list">{''.join(cards)}</div></section>"""
        )

    document = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>RICO filtered_traces 人工挑选</title>
<style>:root{{font-family:"Segoe UI","Microsoft YaHei",sans-serif;color:#17202a}}*{{box-sizing:border-box}}body{{margin:0;background:#f3f5f6}}header{{position:sticky;top:0;z-index:5;display:grid;grid-template-columns:minmax(0,1fr) minmax(430px,.8fr);gap:20px;padding:14px 24px;background:#fff;border-bottom:1px solid #bcc7cc}}h1{{margin:0;font-size:22px}}header p{{margin:5px 0 0;color:#596970}}.legend{{display:flex;flex-wrap:wrap;gap:12px;margin-top:8px;font-size:12px}}.legend span{{display:flex;align-items:center;gap:5px}}.tap-key,.start-key,.end-key{{display:inline-block;width:14px;height:14px;border-radius:50%;border:3px solid #fff;box-shadow:0 0 0 2px #111}}.tap-key,.end-key{{background:#e02d2d}}.start-key{{background:#1687d9}}.summary{{display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:9px;align-items:center}}.summary strong{{font-size:18px;white-space:nowrap}}textarea{{width:100%;min-height:72px;resize:none;border:1px solid #8e9da4;padding:7px;font:12px/1.4 Consolas,monospace}}button{{min-height:36px;border:1px solid #697980;background:#fff;padding:7px 11px;cursor:pointer;font-weight:650}}main{{width:min(1600px,100%);margin:auto;padding:20px 24px 44px}}section{{margin-bottom:44px;scroll-margin-top:125px}}.group-head{{display:flex;justify-content:space-between;gap:20px;align-items:center;margin-bottom:12px;padding:15px;background:#fff;border:1px solid #c6d0d4;border-left:5px solid #176b52}}.group-head.done{{border-left-color:#18845d}}.group-head.over{{border-left-color:#b33a2f}}h2{{margin:0;font-size:20px}}.group-head p{{margin:5px 0 0;color:#5d6d74}}.trace-list{{display:grid;gap:14px}}.trace-card{{background:#fff;border:1px solid #c5cfd3}}.trace-card.selected{{border:3px solid #18845d}}.card-head{{display:flex;justify-content:space-between;align-items:center;gap:15px;padding:11px 13px;border-bottom:1px solid #d5dcdf}}.card-head h3{{display:inline;margin:0 10px 0 0;font-size:17px}}code{{font-size:12px;color:#516169}}label{{font-weight:700;cursor:pointer;white-space:nowrap}}input{{width:19px;height:19px;vertical-align:middle}}.steps{{display:grid;grid-auto-flow:column;grid-auto-columns:minmax(210px,1fr);gap:8px;padding:10px;overflow-x:auto;background:#e8edef}}figure{{display:grid;grid-template-rows:auto auto;margin:0;background:#fff;border:1px solid #c5cfd3;align-self:start}}.image-stage{{position:relative;width:100%;background:#111;overflow:hidden}}.image-stage>a{{position:absolute;inset:0;display:block}}figure img{{display:block;width:100%;height:100%;object-fit:fill}}.tap-marker{{position:absolute;z-index:2;width:34px;height:34px;transform:translate(-50%,-50%);border:4px solid #fff;border-radius:50%;background:rgba(224,45,45,.88);box-shadow:0 0 0 3px #111,0 0 12px #fff;pointer-events:none}}.tap-marker:before,.tap-marker:after{{content:"";position:absolute;background:#fff;left:50%;top:50%;transform:translate(-50%,-50%)}}.tap-marker:before{{width:22px;height:2px}}.tap-marker:after{{width:2px;height:22px}}.tap-marker b{{position:absolute;left:38px;top:2px;padding:2px 5px;background:#111;color:#fff;font-size:11px;white-space:nowrap}}.swipe-overlay{{position:absolute;inset:0;z-index:2;width:100%;height:100%;pointer-events:none;filter:drop-shadow(0 0 3px #fff)}}.swipe-overlay polyline{{fill:none;stroke:#ffd449;stroke-width:12;stroke-linecap:round;stroke-linejoin:round;vector-effect:non-scaling-stroke}}.swipe-overlay circle{{stroke:#fff;stroke-width:8;vector-effect:non-scaling-stroke}}.swipe-start{{fill:#1687d9}}.swipe-end{{fill:#e02d2d}}.gesture-missing{{position:absolute;left:8px;top:8px;z-index:2;padding:4px 7px;background:#111;color:#fff;font-size:11px}}figcaption{{display:grid;gap:3px;padding:8px;text-align:center;font-size:12px}}figcaption span{{color:#9a261f;font-weight:700}}figcaption small{{color:#65747a;font-size:10px}}.meta{{display:flex;flex-wrap:wrap;gap:8px;padding:10px 12px}}.meta span{{border:1px solid #c7d0d4;padding:5px 8px;background:#f8fafb;font-size:12px}}@media(max-width:1000px){{header{{position:static;grid-template-columns:1fr}}}}@media(max-width:700px){{header,main{{padding:14px}}.summary{{grid-template-columns:1fr}}.group-head,.card-head{{align-items:flex-start;flex-direction:column}}}}</style></head><body>
<header><div><h1>RICO filtered_traces 人工挑选</h1><p>6 组各选择 2 条，共 12 条。当前截图上的标记表示执行该动作后进入右侧下一张截图。</p><div class="legend"><span><i class="tap-key"></i>红色靶标：点击</span><span><i class="start-key"></i>蓝点：滑动起点</span><span><i class="end-key"></i>红点：滑动终点</span><span>黄线：滑动路径</span></div></div><div class="summary"><strong>已选 <span id="total">0</span> / 12</strong><textarea id="result" readonly aria-label="已选择的流程编号"></textarea><button id="clear" type="button">清空</button></div></header><main>{''.join(sections)}</main>
<script>const key='rico-trace-review-v2';const boxes=[...document.querySelectorAll('input[type="checkbox"]')];function update(){{let total=0;const lines=[];document.querySelectorAll('section').forEach(section=>{{const selected=[...section.querySelectorAll('input:checked')];total+=selected.length;section.querySelector('.group-count').textContent=selected.length;const head=section.querySelector('.group-head');head.classList.toggle('done',selected.length===2);head.classList.toggle('over',selected.length>2);lines.push(`${{section.querySelector('h2').textContent}}: ${{selected.map(x=>x.value).join(', ')}}`);}});boxes.forEach(box=>box.closest('.trace-card').classList.toggle('selected',box.checked));document.getElementById('total').textContent=total;document.getElementById('result').value=lines.join('\\n');localStorage.setItem(key,JSON.stringify(boxes.filter(x=>x.checked).map(x=>x.value)));}}boxes.forEach(x=>x.addEventListener('change',update));document.getElementById('clear').addEventListener('click',()=>{{boxes.forEach(x=>x.checked=false);update();}});try{{const saved=new Set(JSON.parse(localStorage.getItem(key)||'[]'));boxes.forEach(x=>x.checked=saved.has(x.value));}}catch(_){{}}update();</script></body></html>"""
    output_html.write_text(document, encoding="utf-8")

    parser = ReviewParser()
    parser.feed(document)
    valid = parser.cards == 36 and all(
        step_id and source and (output_html.parent / source).resolve().is_file()
        for step_id, source in parser.images
    )
    print(f"Trace review candidates: {len(selected)}")
    print(f"Trace screenshots referenced: {len(parser.images)}")
    print(f"HTML image mappings valid: {valid}")
    print(f"Review page: {output_html}")
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
