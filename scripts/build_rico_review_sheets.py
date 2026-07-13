from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps


PER_CATEGORY = 5
THUMBNAIL_WIDTH = 300
THUMBNAIL_HEIGHT = 534
COLUMNS = 3
LABEL_HEIGHT = 72
GAP = 20
MARGIN = 24
HEADER_HEIGHT = 58


def load_font(size: int) -> ImageFont.ImageFont:
    candidates = (
        Path("C:/Windows/Fonts/segoeui.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    )
    for path in candidates:
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def main() -> int:
    project_root = Path(__file__).resolve().parent.parent
    candidate_path = project_root / "data/processed/rico_candidate_pool.csv"
    output_root = project_root / "data/processed/rico_review_sheets"
    output_root.mkdir(parents=True, exist_ok=True)

    with candidate_path.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))

    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row["recommended_category"]].append(row)

    title_font = load_font(25)
    label_font = load_font(18)
    small_font = load_font(14)
    generated = 0

    for category, category_rows in grouped.items():
        selected = sorted(
            category_rows,
            key=lambda row: (-int(row["candidate_score"]), int(row["screen_id"])),
        )[:PER_CATEGORY]
        row_count = (len(selected) + COLUMNS - 1) // COLUMNS
        width = MARGIN * 2 + COLUMNS * THUMBNAIL_WIDTH + (COLUMNS - 1) * GAP
        height = (
            MARGIN * 2
            + HEADER_HEIGHT
            + row_count * (THUMBNAIL_HEIGHT + LABEL_HEIGHT)
            + (row_count - 1) * GAP
        )
        sheet = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(sheet)
        draw.text((MARGIN, MARGIN), f"{category} | top {len(selected)}", fill="#111827", font=title_font)

        for index, row in enumerate(selected):
            column = index % COLUMNS
            line = index // COLUMNS
            x = MARGIN + column * (THUMBNAIL_WIDTH + GAP)
            y = MARGIN + HEADER_HEIGHT + line * (THUMBNAIL_HEIGHT + LABEL_HEIGHT + GAP)
            screenshot = project_root / row["screenshot_path"]
            with Image.open(screenshot) as image:
                image = ImageOps.fit(
                    image.convert("RGB"),
                    (THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT),
                    method=Image.Resampling.LANCZOS,
                )
            sheet.paste(image, (x, y))
            draw.rectangle(
                (x, y, x + THUMBNAIL_WIDTH - 1, y + THUMBNAIL_HEIGHT - 1),
                outline="#9ca3af",
                width=1,
            )
            label_y = y + THUMBNAIL_HEIGHT + 7
            draw.text(
                (x, label_y),
                f"ID {row['screen_id']}  score {row['candidate_score']}",
                fill="#111827",
                font=label_font,
            )
            package = row["package_name"]
            if len(package) > 38:
                package = package[:35] + "..."
            draw.text((x, label_y + 28), package, fill="#4b5563", font=small_font)

        output_path = output_root / f"{category}.jpg"
        sheet.save(output_path, quality=92, optimize=True)
        generated += 1

    print(f"Generated {generated} review sheets in {output_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
