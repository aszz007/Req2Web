from __future__ import annotations

import csv
import html
import json
import os
from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path

from PIL import Image

from audit_rico_traces import deep_candidate, gesture_type, index_step_files, numeric_key, parse_gestures
from build_rico_trace_review import NOISE_TOKENS, gesture_markup


TARGETS = {
    "swipe_short": ("补选短滑动流程", "RSS", 2, "选择 2 条：滑动目标清楚、前后画面变化连续，不是单纯广告或浏览器页面。"),
    "mixed_short": ("补选短混合流程", "RMS", 1, "选择 1 条：点击和滑动共同组成一个容易理解的短任务。"),
}


class ReviewParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.cards = 0
        self.images: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "article" and "trace-card" in (values.get("class") or ""):
            self.cards += 1
        if tag == "img":
            self.images.append(values.get("src", "") or "")


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


def classify(gesture_types: str, step_count: int) -> str:
    known = {value for value in gesture_types.split("|") if value in {"tap", "swipe"}}
    family = "tap" if known == {"tap"} else "swipe" if known == {"swipe"} else "mixed"
    return f"{family}_{'short' if step_count <= 3 else 'medium'}"


def rebuild_detail(trace_path: Path) -> dict[str, object]:
    screenshots = index_step_files(trace_path / "screenshots", ".jpg")
    hierarchies = index_step_files(trace_path / "view_hierarchies", ".json")
    gestures, issue = parse_gestures(trace_path / "gestures.json")
    if issue:
        raise ValueError(f"{trace_path}: {issue}")
    all_ids = set(screenshots) | set(hierarchies) | set(gestures)
    ordered_ids = list(gestures)
    ordered_ids.extend(sorted(all_ids - set(gestures), key=numeric_key))
    return {
        "trace_path": trace_path,
        "screenshots": screenshots,
        "hierarchies": hierarchies,
        "gestures": gestures,
        "ordered_ids": ordered_ids,
        "gesture_types_list": [gesture_type(gestures[screen_id]) for screen_id in gestures],
    }


def noise_flags(activity_names: str) -> list[str]:
    lowered = activity_names.lower()
    return [token for token in NOISE_TOKENS if token in lowered]


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    processed = root / "data/processed"
    output_csv = processed / "rico_trace_replacement_candidates.csv"
    output_html = processed / "rico_trace_replacement_review.html"

    excluded = {row["sample_id"] for row in read_csv(processed / "rico_trace_candidate_pool.csv")}
    inventory = read_csv(processed / "rico_traces_inventory.csv")
    preliminary: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in inventory:
        if row["candidate_eligible"] != "True" or row["sample_id"] in excluded:
            continue
        pattern = classify(row["gesture_types"], int(row["screenshot_count"]))
        if pattern in TARGETS:
            preliminary[pattern].append(row)

    deep_rows: dict[str, list[dict[str, str]]] = defaultdict(list)
    for pattern in TARGETS:
        ranked = sorted(
            preliminary[pattern],
            key=lambda row: (
                -(int(row["screen_annotation_link_count"]) + int(row["rico_semantics_link_count"])),
                row["sample_id"],
            ),
        )[:80]
        for row in ranked:
            detail = rebuild_detail(root / row["trace_path"])
            try:
                candidate = deep_candidate(row, detail, root)
            except (AttributeError, TypeError, ValueError):
                continue
            if not candidate["images_valid"] or not candidate["hierarchies_valid"]:
                continue
            if int(candidate["distinct_image_count"]) < 2:
                continue
            candidate["noise_flags"] = "|".join(noise_flags(str(candidate["activity_names"])))
            candidate["auto_quality_score"] = str(
                int(candidate["candidate_score"]) * 10
                + int(candidate["distinct_image_count"]) * 3
                - len(noise_flags(str(candidate["activity_names"]))) * 40
            )
            deep_rows[pattern].append(candidate)

    chosen: list[dict[str, str]] = []
    for pattern, (_, prefix, _, _) in TARGETS.items():
        ranked = sorted(
            deep_rows[pattern],
            key=lambda row: (
                bool(row["noise_flags"]),
                -int(row["auto_quality_score"]),
                row["sample_id"],
            ),
        )
        app_ids: set[str] = set()
        group: list[dict[str, str]] = []
        for row in ranked:
            if row["app_id"] in app_ids:
                continue
            group.append(row)
            app_ids.add(row["app_id"])
            if len(group) == 6:
                break
        if len(group) != 6:
            raise ValueError(f"not enough replacement candidates for {pattern}: {len(group)}")
        for index, row in enumerate(group, 1):
            row["replacement_id"] = f"{prefix}-{index:02d}"
            row["review_status"] = "pending"
            chosen.append(row)

    fields = ["replacement_id", "auto_quality_score", "noise_flags", "review_status"] + [
        key for key in chosen[0] if key not in {"replacement_id", "auto_quality_score", "noise_flags", "review_status"}
    ]
    write_csv_atomic(output_csv, chosen, fields)

    sections: list[str] = []
    for pattern, (label, _, quota, standard) in TARGETS.items():
        cards: list[str] = []
        for row in (item for item in chosen if item["recommended_pattern"] == pattern):
            screenshots = row["screenshot_paths"].split("|")
            screen_ids = row["screen_ids"].split("|")
            gesture_types = row["gesture_types"].split("|")
            with (root / row["trace_path"] / "gestures.json").open("r", encoding="utf-8") as stream:
                gestures = json.load(stream)
            figures: list[str] = []
            for index, screenshot in enumerate(screenshots):
                absolute = root / screenshot
                source = Path(os.path.relpath(absolute, output_html.parent)).as_posix()
                screen_id = screen_ids[index]
                gesture = gesture_types[index] if index < len(gesture_types) else "unknown"
                terminal = index == len(screenshots) - 1
                points = gestures.get(screen_id, [])
                overlay, coordinate_label = gesture_markup(points, terminal=terminal)
                transition = "最后记录动作，后续画面未包含" if terminal and points else "该画面是流程终点" if terminal else "执行后进入右侧下一图"
                with Image.open(absolute) as image:
                    width, height = image.size
                    image.verify()
                figures.append(
                    f"""<figure><div class="image-stage" style="aspect-ratio:{width}/{height}"><a href="{html.escape(source)}"><img src="{html.escape(source)}" alt="{html.escape(row['replacement_id'])} step {index + 1}" loading="lazy"></a>{overlay}</div><figcaption><b>步骤 {index + 1} · {html.escape(gesture)}</b><span>{html.escape(coordinate_label)}</span><small>{html.escape(transition)}</small></figcaption></figure>"""
                )
            noise = "无明显广告或系统跳转信号" if not row["noise_flags"] else f"自动降权：{row['noise_flags']}"
            cards.append(
                f"""<article class="trace-card"><div class="card-head"><div><h3>{html.escape(row['replacement_id'])}</h3><code>{html.escape(row['sample_id'])}</code></div><label><input type="checkbox" value="{html.escape(row['replacement_id'])}"> 选择</label></div><div class="steps">{''.join(figures)}</div><div class="meta">{row['step_count']} 步 · {html.escape(row['gesture_types'])} · {html.escape(noise)}</div></article>"""
            )
        sections.append(
            f"""<section data-quota="{quota}"><div class="group-head"><div><h2>{html.escape(label)}</h2><p>{html.escape(standard)}</p></div><strong>已选 <span class="group-count">0</span> / {quota}</strong></div>{''.join(cards)}</section>"""
        )

    document = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>RICO trace 第二轮补选</title><style>
:root{{font-family:"Segoe UI","Microsoft YaHei",sans-serif;color:#17202a}}*{{box-sizing:border-box}}body{{margin:0;background:#f3f5f6}}header{{position:sticky;top:0;z-index:5;display:grid;grid-template-columns:minmax(0,1fr) minmax(420px,.8fr);gap:18px;padding:14px 24px;background:#fff;border-bottom:1px solid #bcc7cc}}h1{{margin:0;font-size:22px}}header p{{margin:5px 0 0;color:#596970}}.summary{{display:grid;grid-template-columns:auto 1fr;gap:9px;align-items:center}}textarea{{width:100%;min-height:58px;border:1px solid #8e9da4;padding:7px;font:12px Consolas,monospace}}main{{width:min(1600px,100%);margin:auto;padding:20px 24px}}section{{margin-bottom:42px}}.group-head,.card-head{{display:flex;justify-content:space-between;align-items:center;gap:16px;padding:13px;background:#fff;border:1px solid #c6d0d4}}.group-head{{border-left:5px solid #176b52;margin-bottom:12px}}.group-head.done{{border-left-color:#18845d}}.group-head.over{{border-left-color:#b33a2f}}h2,h3{{margin:0}}.group-head p{{margin:5px 0 0;color:#5d6d74}}.trace-card{{margin-bottom:14px;background:#fff;border:1px solid #c5cfd3}}.trace-card.selected{{border:3px solid #18845d}}code{{font-size:12px;color:#56666d}}label{{font-weight:700}}input{{width:19px;height:19px;vertical-align:middle}}.steps{{display:grid;grid-auto-flow:column;grid-auto-columns:minmax(210px,1fr);gap:8px;padding:10px;overflow-x:auto;background:#e8edef}}figure{{display:grid;grid-template-rows:auto auto;margin:0;background:#fff;border:1px solid #c5cfd3;align-self:start}}.image-stage{{position:relative;width:100%;background:#111;overflow:hidden}}.image-stage>a{{position:absolute;inset:0}}img{{display:block;width:100%;height:100%}}.tap-marker{{position:absolute;z-index:2;width:34px;height:34px;transform:translate(-50%,-50%);border:4px solid #fff;border-radius:50%;background:#e02d2d;box-shadow:0 0 0 3px #111;pointer-events:none}}.tap-marker b{{position:absolute;left:37px;padding:2px 5px;background:#111;color:#fff;font-size:11px}}.swipe-overlay{{position:absolute;inset:0;z-index:2;width:100%;height:100%;pointer-events:none;filter:drop-shadow(0 0 3px #fff)}}.swipe-overlay polyline{{fill:none;stroke:#ffd449;stroke-width:12;stroke-linecap:round;vector-effect:non-scaling-stroke}}.swipe-overlay circle{{stroke:#fff;stroke-width:8;vector-effect:non-scaling-stroke}}.swipe-start{{fill:#1687d9}}.swipe-end{{fill:#e02d2d}}.gesture-missing{{position:absolute;left:8px;top:8px;z-index:2;padding:4px 7px;background:#111;color:#fff;font-size:11px}}figcaption{{display:grid;gap:3px;padding:8px;text-align:center;font-size:12px}}figcaption span{{color:#9a261f;font-weight:700}}figcaption small{{color:#65747a}}.meta{{padding:10px 13px;font-size:12px}}@media(max-width:950px){{header{{position:static;grid-template-columns:1fr}}}}@media(max-width:700px){{header,main{{padding:14px}}.summary{{grid-template-columns:1fr}}.group-head,.card-head{{align-items:flex-start;flex-direction:column}}}}</style></head><body>
<header><div><h1>RICO trace 第二轮补选</h1><p>只补选第一轮空缺：短滑动 2 条、短混合 1 条。手势标记含义与第一轮相同。</p></div><div class="summary"><strong>已选 <span id="total">0</span> / 3</strong><textarea id="result" readonly></textarea></div></header><main>{''.join(sections)}</main>
<script>const key='rico-trace-replacement-v1';const boxes=[...document.querySelectorAll('input[type="checkbox"]')];function update(){{let total=0;const lines=[];document.querySelectorAll('section').forEach(section=>{{const quota=Number(section.dataset.quota);const picked=[...section.querySelectorAll('input:checked')];total+=picked.length;section.querySelector('.group-count').textContent=picked.length;const head=section.querySelector('.group-head');head.classList.toggle('done',picked.length===quota);head.classList.toggle('over',picked.length>quota);lines.push(`${{section.querySelector('h2').textContent}}: ${{picked.map(x=>x.value).join(', ')}}`);}});boxes.forEach(x=>x.closest('.trace-card').classList.toggle('selected',x.checked));document.getElementById('total').textContent=total;document.getElementById('result').value=lines.join('\\n');localStorage.setItem(key,JSON.stringify(boxes.filter(x=>x.checked).map(x=>x.value)));}}boxes.forEach(x=>x.addEventListener('change',update));try{{const saved=new Set(JSON.parse(localStorage.getItem(key)||'[]'));boxes.forEach(x=>x.checked=saved.has(x.value));}}catch(_){{}}update();</script></body></html>"""
    output_html.write_text(document, encoding="utf-8")
    parser = ReviewParser()
    parser.feed(document)
    valid = parser.cards == 12 and all((output_html.parent / source).resolve().is_file() for source in parser.images)
    print(f"Replacement candidates: {len(chosen)}")
    print(f"Images referenced: {len(parser.images)}")
    print(f"Noise-free candidates: {sum(not row['noise_flags'] for row in chosen)}")
    print(f"HTML mappings valid: {valid}")
    print(f"Review page: {output_html}")
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
