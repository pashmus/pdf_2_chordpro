"""
Тест: тональность по списку аккордов (music21).
Вывод — как возвращает библиотека, без преобразований.
"""
import music21


def analyze_key_from_chord_list(chord_list):
    """Список аккордов -> Stream -> analyze('key'). Возвращает объект Key или None."""
    if not chord_list:
        return None

    s = music21.stream.Stream()
    for c_str in chord_list:
        c_str = c_str.strip()
        if not c_str:
            continue
        try:
            h = music21.harmony.ChordSymbol(c_str)
            c = music21.chord.Chord(h.pitches)
            c.quarterLength = 1.0
            s.append(c)
        except Exception:
            continue

    if len(s) == 0:
        return None

    try:
        return s.analyze("key")
    except Exception:
        return None


# Бемоли: Bb major
CHORDS_B_FLAT_MAJOR = [
    "Eb", "Ebsus4", "Eb", "Ebsus4", "Fm7", "Bb", "Eb", "Bb/D", "Ab/C",
]

# Диезы: E major
CHORDS_E_MAJOR = [
    "F#", "F#sus4", "F#", "F#sus4", "G#m", "C#", "F#", "C#/F", "B/D#",
]

# Диезы: A major
CHORDS_A_MAJOR = [
    "A", "D", "E", "A", "F#m", "Bm", "D", "E", "A",
]


if __name__ == "__main__":
    print("=== Тест: тональность по списку аккордов (music21) ===\n")

    for label, chords in [
        ("1. Бемоли (Bb)", CHORDS_B_FLAT_MAJOR),
        ("2. Диезы (E)", CHORDS_E_MAJOR),
        ("3. Диезы (A)", CHORDS_A_MAJOR),
    ]:
        print(label)
        print("   Аккорды:", chords)
        key = analyze_key_from_chord_list(chords)
        if key is None:
            print("   Тональность: не определена\n")
        else:
            print("   key.name:", key.name)
            print("   key.tonic.name:", key.tonic.name)
            print("   key.mode:", key.mode)
            print("   key.sharps:", key.sharps)
            print("   key.correlationCoefficient:", key.correlationCoefficient)
            print()

    print("Готово.")
