"""
Модуль анализа тональности для основного конвертера.
Также может запускаться отдельно для анализа .cho файлов в output_cho_test.
"""

import re
from pathlib import Path

import music21


def extract_chords(text):
    """Extracts chords from a text block using regex."""
    if not text:
        return []
    return re.findall(r"\[(.*?)\]", text)


def parse_chordpro(file_path):
    """
    Parses a ChordPro file and extracts chords from the first verse and first chorus.
    Returns a list of chords.
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        print(f"Error reading {file_path}: {e}")
        return []

    chords = []

    verse_match = re.search(
        r"\{(?:sov|start_of_verse)(?::[^}]*)?\}(.*?)\{(?:eov|end_of_verse)(?::[^}]*)?\}",
        content,
        re.DOTALL | re.IGNORECASE,
    )
    if verse_match:
        verse_content = verse_match.group(1)
        chords.extend(extract_chords(verse_content))

    chorus_match = re.search(
        r"\{(?:soc|start_of_chorus)(?::[^}]*)?\}(.*?)\{(?:eoc|end_of_chorus)(?::[^}]*)?\}",
        content,
        re.DOTALL | re.IGNORECASE,
    )
    if chorus_match:
        chorus_content = chorus_match.group(1)
        chords.extend(extract_chords(chorus_content))

    return chords


def parse_chordpro_content(content):
    """
    То же, что parse_chordpro, но по переданной строке (без чтения файла).
    Извлекает аккорды из первого куплета и первого припева.
    """
    if not content:
        return []
    chords = []
    verse_match = re.search(
        r"\{(?:sov|start_of_verse)(?::[^}]*)?\}(.*?)\{(?:eov|end_of_verse)(?::[^}]*)?\}",
        content,
        re.DOTALL | re.IGNORECASE,
    )
    if verse_match:
        chords.extend(extract_chords(verse_match.group(1)))
    chorus_match = re.search(
        r"\{(?:soc|start_of_chorus)(?::[^}]*)?\}(.*?)\{(?:eoc|end_of_chorus)(?::[^}]*)?\}",
        content,
        re.DOTALL | re.IGNORECASE,
    )
    if chorus_match:
        chords.extend(extract_chords(chorus_match.group(1)))
    return chords


def analyze_key(chords):
    """
    Анализирует тональность по списку аккордов (music21).
    Возвращает (key_str, confidence, note): key_str или None, confidence (float/None), строка примечания.
    """
    if not chords:
        return None, None, "No chords found in first verse/chorus"

    s = music21.stream.Stream()
    cleaned_chords = []
    for chord_str in chords:
        c_str = chord_str.strip()
        if not c_str:
            continue
        c_str = re.sub(r"([A-G])b", r"\1-", c_str)

        try:
            h = music21.harmony.ChordSymbol(c_str)
            c = music21.chord.Chord(h.pitches)
            c.quarterLength = 1.0
            s.append(c)
            cleaned_chords.append(c_str)
        except Exception:
            if "/" in c_str:
                simple_c = c_str.split("/")[0]
                try:
                    h = music21.harmony.ChordSymbol(simple_c)
                    c = music21.chord.Chord(h.pitches)
                    c.quarterLength = 1.0
                    s.append(c)
                    cleaned_chords.append(simple_c)
                except Exception:
                    continue
            else:
                continue

    if not cleaned_chords:
        return None, None, "Could not parse any chords"

    try:
        key = s.analyze("key")
        tonic = key.tonic.name.replace("-", "b")
        tonic = tonic[0].upper() + tonic[1:] if len(tonic) > 1 else tonic.upper()
        key_str = tonic if key.mode == "major" else tonic + "m"
        note = f"Confidence: {key.correlationCoefficient:.2f}"
        return key_str, key.correlationCoefficient, note
    except Exception as e:
        return None, None, f"Analysis error: {e}"


def main():
    output_dir = Path("output_cho_test")
    report_file = "key_analysis_report.md"

    results = []

    if not output_dir.exists():
        print(f"Directory {output_dir} does not exist.")
        return

    files = sorted([f for f in output_dir.iterdir() if f.suffix == ".cho"])
    print(f"Found {len(files)} .cho files. Starting analysis...")

    for file_path in files:
        print(f"Analyzing {file_path.name}...")
        chords = parse_chordpro(file_path)
        key, confidence, note = analyze_key(chords)

        results.append(
            {
                "filename": file_path.name,
                "chords": ", ".join(chords[:10]) + ("..." if len(chords) > 10 else ""),
                "key": key if key else "Unknown",
                "note": note,
                "confidence": confidence,
            }
        )

    def confidence_sort_key(item):
        c = item.get("confidence")
        return (c is None, c if c is not None else float("inf"))

    results.sort(key=confidence_sort_key)

    with open(report_file, "w", encoding="utf-8") as f:
        f.write("# Key Analysis Report\n\n")
        f.write("| Filename | Detected Key | Notes | Sample Chords |\n")
        f.write("|---|---|---|---|\n")
        for r in results:
            f.write(f"| {r['filename']} | **{r['key']}** | {r['note']} | {r['chords']} |\n")

    print(f"Analysis complete. Report saved to {report_file}")


if __name__ == "__main__":
    main()
