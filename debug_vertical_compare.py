import fitz
import os
from pathlib import Path

# Отдельный коэффициент порога для отладчика,
# чтобы можно было экспериментировать независимо от основного конвертера.
GAP_THRESHOLD_RATIO = 0.20

def get_lines_words(page):
    words = page.get_text("words")
    TOLERANCE_Y = 3
    lines = {}

    for w in words:
        y_center = (w[1] + w[3]) / 2
        found_y = None
        for y in lines.keys():
            if abs(y - y_center) < TOLERANCE_Y:
                found_y = y
                break

        if found_y is None:
            found_y = y_center
            lines[found_y] = []

        lines[found_y].append(w)

    sorted_ys = sorted(lines.keys())
    processed_lines = []

    prev_bottom = 0
    prev_height = 0

    for y in sorted_ys:
        line_words = sorted(lines[y], key=lambda w: w[0])
        text_content = " ".join([w[4] for w in line_words])

        y0s = [w[1] for w in line_words]
        y1s = [w[3] for w in line_words]
        line_top = min(y0s)
        line_bottom = max(y1s)
        line_height = line_bottom - line_top

        gap = line_top - prev_bottom if prev_bottom > 0 else 0
        thresh = max(prev_height, line_height) * GAP_THRESHOLD_RATIO if prev_height > 0 else 0

        processed_lines.append({
            'text': text_content,
            'top': line_top,
            'bottom': line_bottom,
            'height': line_height,
            'gap': gap,
            'thresh': thresh
        })
        prev_bottom = line_bottom
        prev_height = line_height

    return processed_lines

def get_lines_chars(page):
    raw = page.get_text("rawdict")
    TOLERANCE_Y = 3
    lines_map = {}

    blocks = raw.get("blocks", [])
    for block in blocks:
        if "lines" not in block: continue
        for line in block["lines"]:
            spans = line["spans"]
            for span in spans:
                chars = span.get("chars", [])
                for c_obj in chars:
                    c = c_obj["c"]
                    # if not c.strip(): continue # Removed filter to see spaces

                    bbox = c_obj["bbox"]
                    # Используем нижнюю границу (y1) как в основном конвертере,
                    # чтобы надстрочные/подстрочные части аккордов (например, "7")
                    # оказывались в той же строке.
                    y_baseline = bbox[3]

                    found_y = None
                    for y in lines_map.keys():
                        if abs(y - y_baseline) < TOLERANCE_Y:
                            found_y = y
                            break

                    if found_y is None:
                        found_y = y_baseline
                        lines_map[found_y] = []

                    lines_map[found_y].append({
                        'char': c,
                        'x0': bbox[0],
                        'y0': bbox[1],
                        'y1': bbox[3]
                    })

    sorted_ys = sorted(lines_map.keys())
    processed_lines = []

    prev_bottom = 0
    prev_height = 0

    for y in sorted_ys:
        # Сортируем символы по x, чтобы текст шёл слева направо
        chars = sorted(lines_map[y], key=lambda c: c['x0'])
        text_content = "".join([c['char'] for c in chars])

        if not chars: continue
        if not text_content.strip(): continue # Skip lines with only whitespace

        line_top = min(c['y0'] for c in chars)
        line_bottom = max(c['y1'] for c in chars)
        line_height = line_bottom - line_top

        gap = line_top - prev_bottom if prev_bottom > 0 else 0
        thresh = max(prev_height, line_height) * GAP_THRESHOLD_RATIO if prev_height > 0 else 0

        processed_lines.append({
            'text': text_content,
            'top': line_top,
            'bottom': line_bottom,
            'height': line_height,
            'gap': gap,
            'thresh': thresh
        })
        prev_bottom = line_bottom
        prev_height = line_height

    return processed_lines

def debug_compare():
    input_dir = Path("input_pdf")
    if not input_dir.exists():
        print("No input_pdf dir")
        return

    pdf_files = list(input_dir.glob("*.pdf"))
    if not pdf_files:
        print("No pdf files")
        return

    # Краткий вывод в консоль только для первого файла (первая страница)
    first_pdf = pdf_files[0]
    print(f"Analyzing (console preview only) {first_pdf.name}...\n")

    first_doc = fitz.open(first_pdf)
    first_page = first_doc[0]
    words_lines_first = get_lines_words(first_page)
    chars_lines_first = get_lines_chars(first_page)

    print("=== MODE: WORDS (Legacy) [Page 1] ===")
    print(f"{'Text (start)':<25} | {'Top':<8} | {'Bottom':<8} | {'Height':<8} | {'Gap':<8} | {'Thresh'}")
    print("-" * 95)
    for l in words_lines_first[:20]:
        print(
            f"{l['text'][:25]:<25} | {l['top']:<8.2f} | {l['bottom']:<8.2f} | "
            f"{l['height']:<8.2f} | {l['gap']:<8.2f} | {l.get('thresh', 0.0):.2f}"
        )

    print("\n" + "="*95 + "\n")

    print("=== MODE: CHARS (New) [Page 1] ===")
    print(f"{'Text (start)':<25} | {'Top':<8} | {'Bottom':<8} | {'Height':<8} | {'Gap':<8} | {'Thresh'}")
    print("-" * 95)
    for l in chars_lines_first[:20]:
        print(
            f"{l['text'][:25]:<25} | {l['top']:<8.2f} | {l['bottom']:<8.2f} | "
            f"{l['height']:<8.2f} | {l['gap']:<8.2f} | {l.get('thresh', 0.0):.2f}"
        )

    # Полный отчёт в файл по всем PDF и всем страницам
    out_path = Path("debug_vertical_compare_report.txt")
    with out_path.open("w", encoding="utf-8") as f:
        f.write(f"GAP_THRESHOLD_RATIO = {GAP_THRESHOLD_RATIO}\n\n")

        for pdf_path in pdf_files:
            doc = fitz.open(pdf_path)
            f.write("############################\n")
            f.write(f"FILE: {pdf_path.name}\n")
            f.write(f"Pages: {len(doc)}\n\n")

            for page_index in range(len(doc)):
                page = doc[page_index]
                words_lines = get_lines_words(page)
                chars_lines = get_lines_chars(page)

                f.write(f"===== PAGE {page_index + 1} / {len(doc)} =====\n\n")

                f.write("=== MODE: WORDS (Legacy) ===\n")
                f.write(
                    f"{'Text (start)':<40} | {'Top':<8} | {'Bottom':<8} | "
                    f"{'Height':<8} | {'Gap':<8} | {'Thresh'}\n"
                )
                f.write("-" * 110 + "\n")
                for l in words_lines:
                    f.write(
                        f"{l['text'][:40]:<40} | {l['top']:<8.2f} | {l['bottom']:<8.2f} | "
                        f"{l['height']:<8.2f} | {l['gap']:<8.2f} | {l.get('thresh', 0.0):.2f}\n"
                    )

                f.write("\n" + "="*110 + "\n\n")

                f.write("=== MODE: CHARS (New) ===\n")
                f.write(
                    f"{'Text (start)':<40} | {'Top':<8} | {'Bottom':<8} | "
                    f"{'Height':<8} | {'Gap':<8} | {'Thresh'}\n"
                )
                f.write("-" * 110 + "\n")
                for l in chars_lines:
                    f.write(
                        f"{l['text'][:40]:<40} | {l['top']:<8.2f} | {l['bottom']:<8.2f} | "
                        f"{l['height']:<8.2f} | {l['gap']:<8.2f} | {l.get('thresh', 0.0):.2f}\n"
                    )

                f.write("\n\n")

    print(f"\nFull report (all files, all pages) saved to: {out_path}")

if __name__ == "__main__":
    debug_compare()
