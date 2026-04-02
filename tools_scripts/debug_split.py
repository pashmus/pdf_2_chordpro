"""
Отладочный скрипт: проверяет разбиение строк с аккордами на токены.
Используется для локальной проверки случаев вроде "(E7)" и "E(:A2-E)".
"""

import re

CHORD_TOKEN_PATTERN = re.compile(
    r"[A-H](?:b|#)?(?:2|5|m|maj|min|dim|aug|sus|add)?(?:[0-9]{1,2})?(?:/[A-H](?:b|#)?)?"
)


def split_chord_word_by_chords(text):
    if not text:
        return [text]
    matches = list(CHORD_TOKEN_PATTERN.finditer(text))
    if not matches:
        return [text]
    parts = []
    if matches[0].start() > 0:
        parts.append(text[: matches[0].start()])
    for i, match_obj in enumerate(matches):
        start = match_obj.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        parts.append(text[start:end])
    return parts


print(f"'(E7)' -> {split_chord_word_by_chords('(E7)')}")
print(f"'E(:A2-E)' -> {split_chord_word_by_chords('E(:A2-E)')}")
