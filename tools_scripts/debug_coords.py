"""
Отладочный скрипт: анализирует координаты строк PDF и расстояния между строками.
Помогает проверять эвристику определения разрыва секций по вертикальным отступам.
"""

from pathlib import Path

import fitz

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent


def analyze_pdf_coordinates():
    input_dir = PROJECT_ROOT / "input_pdf"
    if not input_dir.exists():
        print(f"Directory {input_dir} does not exist.")
        return

    pdf_files = list(input_dir.glob("*.pdf"))
    if not pdf_files:
        print(f"No PDF files found in {input_dir}")
        return

    pdf_path = pdf_files[0]
    print(f"Analyzing file: {pdf_path.name}")
    print("-" * 80)

    doc = fitz.open(pdf_path)
    processed_lines_all_pages = []

    for page_num, page in enumerate(doc):
        print(f"Page {page_num + 1}")
        words = page.get_text("words")
        tolerance_y = 3
        lines = {}

        for w in words:
            y_center = (w[1] + w[3]) / 2
            found_y = None
            for y in lines.keys():
                if abs(y - y_center) < tolerance_y:
                    found_y = y
                    break

            if found_y is None:
                found_y = y_center
                lines[found_y] = []

            lines[found_y].append(w)

        sorted_ys = sorted(lines.keys())

        for y in sorted_ys:
            line_words = sorted(lines[y], key=lambda w: w[0])
            text_content = " ".join([w[4] for w in line_words])

            y0s = [w[1] for w in line_words]
            y1s = [w[3] for w in line_words]

            line_top = min(y0s)
            line_bottom = max(y1s)
            line_height = line_bottom - line_top

            processed_lines_all_pages.append(
                {
                    "text": text_content,
                    "top": line_top,
                    "bottom": line_bottom,
                    "height": line_height,
                    "y_center": y,
                }
            )

    for i, curr in enumerate(processed_lines_all_pages):
        gap_info = ""

        if i > 0:
            prev = processed_lines_all_pages[i - 1]

            if curr["top"] < prev["bottom"]:
                gap_info = " | Gap: NEW PAGE?"
            else:
                gap = curr["top"] - prev["bottom"]
                max_height = max(prev["height"], curr["height"])
                threshold = max_height * 0.5

                if gap > threshold:
                    gap_info = f" | Gap: {gap:.1f} (Thresh: {threshold:.1f}) >>> BREAK?"
                else:
                    gap_info = f" | Gap: {gap:.1f} (Thresh: {threshold:.1f})"

        print(
            f"[Top: {curr['top']:6.1f} | Bot: {curr['bottom']:6.1f} | "
            f"H: {curr['height']:4.1f}{gap_info}] {curr['text']}"
        )


if __name__ == "__main__":
    analyze_pdf_coordinates()
