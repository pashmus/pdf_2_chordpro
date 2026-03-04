import re

CHORD_TOKEN_PATTERN = re.compile(
    r'[A-H](?:b|#)?(?:2|5|m|maj|min|dim|aug|sus|add)?(?:[0-9]{1,2})?(?:/[A-H](?:b|#)?)?'
)

def _split_chord_word_by_chords(text):
    if not text:
        return [text]
    matches = list(CHORD_TOKEN_PATTERN.finditer(text))
    if not matches:
        return [text]
    parts = []
    # Leading prefix
    if matches[0].start() > 0:
        parts.append(text[: matches[0].start()])
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        parts.append(text[start:end])
    return parts

print(f"'(E7)' -> {_split_chord_word_by_chords('(E7)')}")
print(f"'E(:A2-E)' -> {_split_chord_word_by_chords('E(:A2-E)')}")
