"""
Вспомогательные константы и функции для конвертера PDF -> ChordPro.
"""

import re

# --- Константы ---
TOLERANCE_Y = 3
CHORD_PATTERN = re.compile(
    r"^[A-H](?:b|#)?(?:2|5|m|maj|min|dim|aug|sus|add)?(?:[0-9]{1,2})?(?:/[A-H](?:b|#)?)?$"
)
# Паттерн одного аккорда без привязки к границам — для поиска первого аккорда в строке
CHORD_TOKEN_PATTERN = re.compile(
    r"[A-H](?:b|#)?(?:2|5|m|maj|min|dim|aug|sus|add)?(?:[0-9]{1,2})?(?:/[A-H](?:b|#)?)?"
)
CHORD_LINE_RATIO = 0.4
GAP_THRESHOLD_RATIO = 0.32
WORD_GAP_PT = 2.0
REFERENCE_LINE_HEIGHT = 12.0  # средняя высота строки для шрифта ~10pt (используется)
WORD_GAP_RATIO = 0.3  # относительный порог разрыва между символами
INDENT_BY_CHORD_LEN = {1: 2, 2: 4, 3: 5, 4: 6, 5: 7}
INDENT_DEFAULT = 8
STRUCTURAL_CHARS = frozenset(["//:", "://", "|", "|:", ":|"])
KEY_CONFIDENCE_THRESHOLD = 0.75


def normalize_chord_for_key_compare(chord_str):
    """
    Нормализует аккорд для сравнения с определённой тональностью:
    - убирает бас после слэша (G/B -> G),
    - отбрасывает дополнения (7, sus4, add9 и т.п.),
    - оставляет только тонику + признак минора (m), если он есть.
    Возвращает строку вида "C", "F#", "Ebm" или None, если распарсить не удалось.
    """
    if not chord_str:
        return None

    s = chord_str.strip().strip("[](){}|.,;:!?")
    if not s:
        return None

    # Убираем басовый аккорд
    s = s.split("/", 1)[0].strip()
    if not s:
        return None

    m = re.match(r"^([A-G](?:b|#)?)(.*)$", s)
    if not m:
        return None

    root = m.group(1)
    tail = m.group(2).lower()

    # Минор считаем только если хвост начинается с m/min, но не maj
    is_minor = tail.startswith("min") or (tail.startswith("m") and not tail.startswith("maj"))

    return root + ("m" if is_minor else "")


def german_to_standard_in_brackets(content):
    """Внутри каждой пары [...] заменяет германскую нотацию на стандартную: H→B, B→Bb."""

    def replace_inside(match_obj):
        s = match_obj.group(1)
        temp = "###TEMP###"
        s = s.replace("H", temp)
        s = s.replace("B", "Bb")
        s = s.replace(temp, "B")
        return "[" + s + "]"

    return re.sub(r"\[(.*?)\]", replace_inside, content)


def split_chord_word_by_chords(text):
    """
    Разбивает уже полученное «слово» по границам начала аккордов.
    Ведущий неаккордный префикс — отдельный фрагмент; каждый аккорд —
    вместе с символами после него в этом же слове до начала следующего аккорда.
    """
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


def calculate_adaptive_gap_threshold(chars, line_height):
    """
    Вычисляет адаптивный порог разрыва между символами на основе размера шрифта.
    """
    if not chars:
        return WORD_GAP_PT

    char_widths = []
    for ch in chars:
        if ch["char"].strip():
            width = ch["x1"] - ch["x0"]
            if width > 0:
                char_widths.append(width)

    if not char_widths:
        return WORD_GAP_PT

    char_widths_sorted = sorted(char_widths)
    median_idx = len(char_widths_sorted) // 2
    if len(char_widths_sorted) % 2 == 0:
        avg_char_width = (char_widths_sorted[median_idx - 1] + char_widths_sorted[median_idx]) / 2
    else:
        avg_char_width = char_widths_sorted[median_idx]

    threshold_by_width = avg_char_width * WORD_GAP_RATIO
    threshold_by_height = WORD_GAP_PT * (line_height / REFERENCE_LINE_HEIGHT)
    adaptive_threshold = 0.7 * threshold_by_width + 0.3 * threshold_by_height

    min_threshold = WORD_GAP_PT * 0.5
    max_threshold = WORD_GAP_PT * 5.0
    return max(min_threshold, min(adaptive_threshold, max_threshold))


def chars_to_simulated_words(chars, gap_threshold=None):
    """
    Собирает из списка символов (chars) «слова» по пробелам и по расстоянию между символами.
    Возвращает список кортежей (x0, y0, x1, y1, text) как в page.get_text("words").
    """
    if gap_threshold is None:
        gap_threshold = WORD_GAP_PT

    words_simulated = []
    current_word_chars = []

    for i, ch in enumerate(chars):
        is_space = ch["char"] == " "
        if i > 0 and not is_space:
            prev_ch = chars[i - 1]
            dist = ch["x0"] - prev_ch["x1"]
            if dist > gap_threshold:
                if current_word_chars:
                    _flush_word_maybe_split_chords(current_word_chars, words_simulated)
                    current_word_chars = []

        if is_space:
            if current_word_chars:
                _flush_word_maybe_split_chords(current_word_chars, words_simulated)
                current_word_chars = []
        else:
            current_word_chars.append(ch)

    if current_word_chars:
        _flush_word_maybe_split_chords(current_word_chars, words_simulated)
    return words_simulated


def _flush_word(current_word_chars, out_list):
    """Добавляет текущее «слово» из current_word_chars в out_list в формате (x0,y0,x1,y1,text)."""
    wx0 = current_word_chars[0]["x0"]
    wy0 = min(ch["y0"] for ch in current_word_chars)
    wx1 = current_word_chars[-1]["x1"]
    wy1 = max(ch["y1"] for ch in current_word_chars)
    wtext = "".join(ch["char"] for ch in current_word_chars)
    out_list.append((wx0, wy0, wx1, wy1, wtext))


def _flush_word_maybe_split_chords(current_word_chars, out_list):
    """
    Сбрасывает накопленное слово в out_list. Если в слове несколько аккордов — разбивает по границам
    аккордов и добавляет каждый фрагмент отдельно с реальными координатами из символов.
    """
    text = "".join(c["char"] for c in current_word_chars)
    parts = split_chord_word_by_chords(text)
    if len(parts) == 1:
        _flush_word(current_word_chars, out_list)
        return
    idx = 0
    for part in parts:
        chunk = current_word_chars[idx : idx + len(part)]
        idx += len(part)
        if chunk:
            _flush_word(chunk, out_list)
