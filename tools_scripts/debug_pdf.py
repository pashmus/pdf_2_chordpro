"""
Отладочный скрипт: печатает структуру PDF по строкам (Y-координата, шаг, текст).
Используется для визуальной проверки группировки слов в строки.
"""

from pathlib import Path

import fitz

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent


def analyze_pdf_structure(pdf_path):
    doc = fitz.open(pdf_path)
    print(f"Analyzing {pdf_path}")

    for page_num, page in enumerate(doc):
        print(f"--- Page {page_num + 1} ---")
        words = page.get_text("words")

        lines = {}
        for w in words:
            y_center = (w[1] + w[3]) / 2
            found = False
            for y in lines:
                if abs(y - y_center) < 3:
                    lines[y].append(w)
                    found = True
                    break
            if not found:
                lines[y_center] = [w]

        sorted_ys = sorted(lines.keys())
        prev_y = 0
        for y in sorted_ys:
            line_words = sorted(lines[y], key=lambda w: w[0])
            text = " ".join([w[4] for w in line_words])
            diff = y - prev_y if prev_y else 0
            print(f"Y={y:.1f} | Diff={diff:.1f} | {text}")
            prev_y = y


if __name__ == "__main__":
    pdf_dir = PROJECT_ROOT / "input_pdf"
    files = list(pdf_dir.glob("*.pdf"))
    if files:
        analyze_pdf_structure(files[0])
    else:
        print("No PDF files found")
