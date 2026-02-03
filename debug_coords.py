import fitz  # PyMuPDF
import re
from pathlib import Path

def analyze_pdf_coordinates():
    input_dir = Path("input_pdf")
    if not input_dir.exists():
        print(f"Directory {input_dir} does not exist.")
        return

    pdf_files = list(input_dir.glob("*.pdf"))
    if not pdf_files:
        print(f"No PDF files found in {input_dir}")
        return

    # Pick the first file for testing
    pdf_path = pdf_files[0]
    print(f"Analyzing file: {pdf_path.name}")
    print("-" * 80)

    doc = fitz.open(pdf_path)

    processed_lines_all_pages = []

    for page_num, page in enumerate(doc):
        print(f"Page {page_num + 1}")
        words = page.get_text("words")
        # words structure: (x0, y0, x1, y1, "text", block_no, line_no, word_no)

        TOLERANCE_Y = 3
        lines = {}

        # Group words into lines based on Y center
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

        # Calculate geometry for each line
        page_lines = []
        for y in sorted_ys:
            line_words = sorted(lines[y], key=lambda w: w[0])
            text_content = " ".join([w[4] for w in line_words])

            # Calculate Top and Bottom for the line
            # w[1] is y0 (top), w[3] is y1 (bottom)
            y0s = [w[1] for w in line_words]
            y1s = [w[3] for w in line_words]

            line_top = min(y0s)
            line_bottom = max(y1s)
            line_height = line_bottom - line_top

            page_lines.append({
                'text': text_content,
                'top': line_top,
                'bottom': line_bottom,
                'height': line_height,
                'y_center': y
            })

        processed_lines_all_pages.extend(page_lines)

    # Analyze gaps
    for i in range(len(processed_lines_all_pages)):
        curr = processed_lines_all_pages[i]

        gap_info = ""
        is_break = False

        if i > 0:
            prev = processed_lines_all_pages[i-1]

            # Check if we are on a new page (rough heuristic: if current top is less than previous bottom by a lot, likely new page)
            # But here we just flattened the list. Let's assume standard flow.
            # If gap is negative, it means we probably jumped page or column, ignore for this simple test or handle it.
            # Usually in simple song PDFs, flow is vertical.

            if curr['top'] < prev['bottom']:
                gap = 0 # New page or weird overlap
                gap_str = "NEW PAGE?"
            else:
                gap = curr['top'] - prev['bottom']

                # Threshold logic: 75% of max height of the two lines
                max_height = max(prev['height'], curr['height'])
                threshold = max_height * 0.5

                gap_str = f"{gap:.1f}"

                if gap > threshold:
                    is_break = True
                    gap_info = f" | Gap: {gap_str} (Thresh: {threshold:.1f}) >>> BREAK?"
                else:
                    gap_info = f" | Gap: {gap_str} (Thresh: {threshold:.1f})"

        print(f"[Top: {curr['top']:6.1f} | Bot: {curr['bottom']:6.1f} | H: {curr['height']:4.1f}{gap_info}] {curr['text']}")

if __name__ == "__main__":
    analyze_pdf_coordinates()
