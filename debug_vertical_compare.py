import fitz
import os
from pathlib import Path

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

    for y in sorted_ys:
        line_words = sorted(lines[y], key=lambda w: w[0])
        text_content = " ".join([w[4] for w in line_words])

        y0s = [w[1] for w in line_words]
        y1s = [w[3] for w in line_words]
        line_top = min(y0s)
        line_bottom = max(y1s)
        line_height = line_bottom - line_top

        gap = line_top - prev_bottom if prev_bottom > 0 else 0

        processed_lines.append({
            'text': text_content,
            'top': line_top,
            'bottom': line_bottom,
            'height': line_height,
            'gap': gap
        })
        prev_bottom = line_bottom

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
                    y_center = (bbox[1] + bbox[3]) / 2

                    found_y = None
                    for y in lines_map.keys():
                        if abs(y - y_center) < TOLERANCE_Y:
                            found_y = y
                            break

                    if found_y is None:
                        found_y = y_center
                        lines_map[found_y] = []

                    lines_map[found_y].append({
                        'char': c,
                        'y0': bbox[1],
                        'y1': bbox[3]
                    })

    sorted_ys = sorted(lines_map.keys())
    processed_lines = []

    prev_bottom = 0

    for y in sorted_ys:
        chars = lines_map[y]
        text_content = "".join([c['char'] for c in chars])
        
        if not chars: continue
        if not text_content.strip(): continue # Skip lines with only whitespace

        line_top = min(c['y0'] for c in chars)
        line_bottom = max(c['y1'] for c in chars)
        line_height = line_bottom - line_top

        gap = line_top - prev_bottom if prev_bottom > 0 else 0

        processed_lines.append({
            'text': text_content,
            'top': line_top,
            'bottom': line_bottom,
            'height': line_height,
            'gap': gap
        })
        prev_bottom = line_bottom

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

    pdf_path = pdf_files[0]
    print(f"Analyzing {pdf_path.name}...\n")

    doc = fitz.open(pdf_path)
    page = doc[0]

    print("=== MODE: WORDS (Legacy) ===")
    print(f"{'Text (start)':<25} | {'Top':<8} | {'Bottom':<8} | {'Height':<8} | {'Gap (to prev)'}")
    print("-" * 75)
    words_lines = get_lines_words(page)
    for l in words_lines[:20]: # Show first 20 lines
        print(f"{l['text'][:25]:<25} | {l['top']:<8.2f} | {l['bottom']:<8.2f} | {l['height']:<8.2f} | {l['gap']:.2f}")

    print("\n" + "="*75 + "\n")

    print("=== MODE: CHARS (New) ===")
    print(f"{'Text (start)':<25} | {'Top':<8} | {'Bottom':<8} | {'Height':<8} | {'Gap (to prev)'}")
    print("-" * 75)
    chars_lines = get_lines_chars(page)
    for l in chars_lines[:20]: # Show first 20 lines
        print(f"{l['text'][:25]:<25} | {l['top']:<8.2f} | {l['bottom']:<8.2f} | {l['height']:<8.2f} | {l['gap']:.2f}")

if __name__ == "__main__":
    debug_compare()
