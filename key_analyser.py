import os
import re
import music21
from pathlib import Path

def extract_chords(text):
    """Extracts chords from a text block using regex."""
    if not text:
        return []
    # Find all content inside square brackets
    return re.findall(r'\[(.*?)\]', text)

def parse_chordpro(file_path):
    """
    Parses a ChordPro file and extracts chords from the first verse and first chorus.
    Returns a list of chords.
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        print(f"Error reading {file_path}: {e}")
        return []

    chords = []

    # Извлекаем первый куплет (директивы могут быть с меткой: {start_of_verse: 1.} или без)
    verse_match = re.search(
        r'\{(?:sov|start_of_verse)(?::[^}]*)?\}(.*?)\{(?:eov|end_of_verse)(?::[^}]*)?\}',
        content, re.DOTALL | re.IGNORECASE
    )
    if verse_match:
        verse_content = verse_match.group(1)
        chords.extend(extract_chords(verse_content))

    # Извлекаем первый припев (директивы могут быть с меткой: {start_of_chorus: Пр. 1:} или без)
    chorus_match = re.search(
        r'\{(?:soc|start_of_chorus)(?::[^}]*)?\}(.*?)\{(?:eoc|end_of_chorus)(?::[^}]*)?\}',
        content, re.DOTALL | re.IGNORECASE
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
        r'\{(?:sov|start_of_verse)(?::[^}]*)?\}(.*?)\{(?:eov|end_of_verse)(?::[^}]*)?\}',
        content, re.DOTALL | re.IGNORECASE
    )
    if verse_match:
        chords.extend(extract_chords(verse_match.group(1)))
    chorus_match = re.search(
        r'\{(?:soc|start_of_chorus)(?::[^}]*)?\}(.*?)\{(?:eoc|end_of_chorus)(?::[^}]*)?\}',
        content, re.DOTALL | re.IGNORECASE
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
        # Бемоль в скобках: строчная "b" после ноты (A–G) → "-" (в т.ч. в басу, напр. D/Bb)
        c_str = re.sub(r'([A-G])b', r'\1-', c_str)

        try:
            # music21.harmony.ChordSymbol parses the chord string (e.g. "Am", "G7", "D/F#")
            h = music21.harmony.ChordSymbol(c_str)

            # CRITICAL FIX: explicit creation of Chord object from pitches
            # analyze('key') works better on Chord objects with explicit pitches in the stream
            c = music21.chord.Chord(h.pitches)

            # Set duration (quarterLength) to give it some weight, though 1.0 is default
            c.quarterLength = 1.0

            s.append(c)
            cleaned_chords.append(c_str)
        except Exception:
            # Try simplifying slash chords if full parsing fails: D/F# -> D
            if '/' in c_str:
                simple_c = c_str.split('/')[0]
                try:
                    h = music21.harmony.ChordSymbol(simple_c)
                    c = music21.chord.Chord(h.pitches)
                    c.quarterLength = 1.0
                    s.append(c)
                    cleaned_chords.append(simple_c)
                except:
                    continue
            else:
                continue

    if not cleaned_chords:
        return None, None, "Could not parse any chords"

    try:
        key = s.analyze('key')
        # Формат как у аккордов: "E"/"Em"; бемоли через "b" (music21 даёт "B-"), диезы "#" не трогаем
        tonic = key.tonic.name.replace('-', 'b')
        tonic = tonic[0].upper() + tonic[1:] if len(tonic) > 1 else tonic.upper()
        key_str = tonic if key.mode == 'major' else tonic + 'm'
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

    files = sorted([f for f in output_dir.iterdir() if f.suffix == '.cho'])

    print(f"Found {len(files)} .cho files. Starting analysis...")

    for file_path in files:
        print(f"Analyzing {file_path.name}...")
        chords = parse_chordpro(file_path)
        key, _, note = analyze_key(chords)

        results.append({
            "filename": file_path.name,
            "chords": ", ".join(chords[:10]) + ("..." if len(chords) > 10 else ""), # Show first 10 chords
            "key": key if key else "Unknown",
            "note": note
        })

    # Generate Markdown Report
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write("# Key Analysis Report\n\n")
        f.write("| Filename | Detected Key | Notes | Sample Chords |\n")
        f.write("|---|---|---|---|\n")
        for r in results:
            f.write(f"| {r['filename']} | **{r['key']}** | {r['note']} | {r['chords']} |\n")

    print(f"Analysis complete. Report saved to {report_file}")

if __name__ == "__main__":
    main()
