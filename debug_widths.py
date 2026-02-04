import fitz
import os
from pathlib import Path

def analyze_widths():
    input_dir = Path("input_pdf_test")
    output_file = Path("debug_widths.txt")

    if not input_dir.exists():
        with open(output_file, "w", encoding="utf-8") as f:
            f.write("Directory input_pdf does not exist.")
        return

    pdf_files = list(input_dir.glob("*.pdf"))
    if not pdf_files:
        with open(output_file, "w", encoding="utf-8") as f:
            f.write("No PDF files found in input_pdf.")
        return

    pdf_path = pdf_files[0]

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(f"Analyzing {pdf_path.name}...\n\n")
        f.write(f"{'Char':<6} | {'Width':<8} | {'Gap After':<10}\n")
        f.write("-" * 35 + "\n")

        try:
            doc = fitz.open(pdf_path)
            # Analyze first page
            page = doc[0]
            raw = page.get_text("rawdict")

            count = 0
            limit = 50000 # Analyze first 200 characters to get a good sample

            blocks = raw["blocks"]
            for block in blocks:
                if "lines" not in block: continue
                for line in block["lines"]:
                    spans = line["spans"]
                    for span in spans:
                        chars = span.get("chars", [])
                        for i, char in enumerate(chars):
                            c = char["c"]
                            bbox = char["bbox"]
                            width = bbox[2] - bbox[0]

                            # Gap calculation
                            gap = 0.0
                            if i < len(chars) - 1:
                                next_char = chars[i+1]
                                gap = next_char["bbox"][0] - bbox[2]

                            # Log everything, including spaces
                            gap_str = f"{gap:.4f}" if gap > 0.001 else "-"
                            display_char = f"'{c}'" if c != " " else "'[SPACE]'"
                            f.write(f"{display_char:<8} | {width:.4f}   | {gap_str}\n")
                            count += 1

                            if count >= limit:
                                break
                        if count >= limit: break
                    if count >= limit: break
                if count >= limit: break

        except Exception as e:
            f.write(f"\nError: {e}")

    print(f"Analysis saved to {output_file}")

if __name__ == "__main__":
    analyze_widths()
