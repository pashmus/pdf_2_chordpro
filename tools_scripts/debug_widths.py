"""
Отладочный скрипт: анализирует ширину символов и интервалы между ними в PDF.
Нужен для подбора порогов разбиения текста на «слова» в основном конвертере.
"""

from pathlib import Path

import fitz

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent


def analyze_widths():
    input_dir = PROJECT_ROOT / "input_pdf_test"
    output_file = SCRIPT_DIR / "debug_widths.txt"

    if not input_dir.exists():
        with open(output_file, "w", encoding="utf-8") as f:
            f.write("Directory input_pdf_test does not exist.")
        return

    pdf_files = list(input_dir.glob("*.pdf"))
    if not pdf_files:
        with open(output_file, "w", encoding="utf-8") as f:
            f.write("No PDF files found in input_pdf_test.")
        return

    pdf_path = pdf_files[0]

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(f"Analyzing {pdf_path.name}...\n\n")
        f.write(f"{'Char':<6} | {'Width':<8} | {'Gap After':<10}\n")
        f.write("-" * 35 + "\n")

        try:
            doc = fitz.open(pdf_path)
            page = doc[0]
            raw = page.get_text("rawdict")

            count = 0
            limit = 50000

            for block in raw["blocks"]:
                if "lines" not in block:
                    continue
                for line in block["lines"]:
                    for span in line["spans"]:
                        chars = span.get("chars", [])
                        for i, char in enumerate(chars):
                            c = char["c"]
                            bbox = char["bbox"]
                            width = bbox[2] - bbox[0]

                            gap = 0.0
                            if i < len(chars) - 1:
                                next_char = chars[i + 1]
                                gap = next_char["bbox"][0] - bbox[2]

                            gap_str = f"{gap:.4f}" if gap > 0.001 else "-"
                            display_char = f"'{c}'" if c != " " else "'[SPACE]'"
                            f.write(f"{display_char:<8} | {width:.4f}   | {gap_str}\n")
                            count += 1

                            if count >= limit:
                                break
                        if count >= limit:
                            break
                    if count >= limit:
                        break
                if count >= limit:
                    break

        except Exception as exc:
            f.write(f"\nError: {exc}")

    print(f"Analysis saved to {output_file}")


if __name__ == "__main__":
    analyze_widths()
