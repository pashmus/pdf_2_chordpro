"""
Конвертер PDF в ChordPro.
Извлекает текст и аккорды из PDF и формирует .cho файлы.
"""
import fitz  # PyMuPDF
import re
import os
import argparse
from pathlib import Path
from database_manager import DatabaseManager

# --- Константы ---
TOLERANCE_Y = 3
CHORD_PATTERN = re.compile(
    r'^[A-H](?:b|#)?(?:2|5|m|maj|min|dim|aug|sus|add)?(?:[0-9]{1,2})?(?:/[A-H](?:b|#)?)?$'
)
# Паттерн одного аккорда без привязки к границам — для поиска первого аккорда в строке
CHORD_TOKEN_PATTERN = re.compile(
    r'[A-H](?:b|#)?(?:2|5|m|maj|min|dim|aug|sus|add)?(?:[0-9]{1,2})?(?:/[A-H](?:b|#)?)?'
)
CHORD_LINE_RATIO = 0.4
GAP_THRESHOLD_RATIO = 0.3
WORD_GAP_PT = 2.0
# Референсные значения для "нормального" шрифта (примерно 10-12pt)
REFERENCE_CHAR_WIDTH = 6.0  # средняя ширина символа в поинтах для шрифта ~10pt (справочная инфа)
REFERENCE_LINE_HEIGHT = 12.0  # средняя высота строки для шрифта ~10pt (используется)
WORD_GAP_RATIO = 0.3  # относительный порог: разрыв считается границей слова, если он > 30% от ширины символа
KEY_SCAN_MAX_LINES = 20
INDENT_BY_CHORD_LEN = {1: 2, 2: 4, 3: 5, 4: 6, 5: 7}
INDENT_DEFAULT = 8
STRUCTURAL_CHARS = frozenset(["//:", "://", "|", "|:", ":|"])


def _split_chord_word_by_chords(text):
    """
    Разбивает уже полученное «слово» (оно уже разбито по пробелам/разрывам) по границам
    начала аккордов. Ведущий неаккордный префикс — отдельный фрагмент; каждый аккорд —
    вместе с символами после него в этом же слове до начала следующего аккорда (часто "|").
    Например: "|A2" -> ["|", "A2"]; "|A2|E" -> ["|", "A2|", "E"].
    """
    if not text:
        return [text]
    matches = list(CHORD_TOKEN_PATTERN.finditer(text))
    if not matches:
        return [text]
    parts = []
    # Ведущий префикс до первого аккорда
    if matches[0].start() > 0:
        parts.append(text[: matches[0].start()])
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        parts.append(text[start:end])
    return parts


def _calculate_adaptive_gap_threshold(chars, line_height):
    """
    Вычисляет адаптивный порог разрыва между символами на основе размера шрифта.

    Args:
        chars: список символов строки
        line_height: высота строки в поинтах

    Returns:
        Порог разрыва в поинтах
    """
    if not chars:
        return WORD_GAP_PT

    # Вычисляем среднюю ширину не-пробельных символов
    char_widths = []
    for c in chars:
        if c['char'].strip():  # Пропускаем пробелы
            width = c['x1'] - c['x0']
            if width > 0:
                char_widths.append(width)

    if not char_widths:
        return WORD_GAP_PT

    # Используем медиану для устойчивости к выбросам
    char_widths_sorted = sorted(char_widths)
    median_idx = len(char_widths_sorted) // 2
    if len(char_widths_sorted) % 2 == 0:
        avg_char_width = (char_widths_sorted[median_idx - 1] + char_widths_sorted[median_idx]) / 2
    else:
        avg_char_width = char_widths_sorted[median_idx]

    # Метод 1: На основе ширины символа (более точный для горизонтальных разрывов)
    threshold_by_width = avg_char_width * WORD_GAP_RATIO

    # Метод 2: На основе высоты строки (индикатор размера шрифта)
    threshold_by_height = WORD_GAP_PT * (line_height / REFERENCE_LINE_HEIGHT)

    # Комбинируем оба метода (взвешенное среднее)
    # Больше веса даём методу по ширине, т.к. он более релевантен для горизонтальных разрывов
    adaptive_threshold = 0.7 * threshold_by_width + 0.3 * threshold_by_height

    # Ограничиваем снизу и сверху разумными значениями
    min_threshold = WORD_GAP_PT * 0.5  # Не меньше половины базового
    max_threshold = WORD_GAP_PT * 5.0  # Не больше 5x базового

    return max(min_threshold, min(adaptive_threshold, max_threshold))


def _chars_to_simulated_words(chars, gap_threshold=None):
    """
    Собирает из списка символов (chars) «слова» по пробелам и по расстоянию между символами.
    gap_threshold: порог разрыва в поинтах (если None, используется WORD_GAP_PT)
    Возвращает список кортежей (x0, y0, x1, y1, text) как в page.get_text("words").
    """
    if gap_threshold is None:
        gap_threshold = WORD_GAP_PT

    words_simulated = []
    current_word_chars = []

    for i, c in enumerate(chars):
        is_space = c['char'] == ' '
        if i > 0 and not is_space:
            prev_c = chars[i - 1]
            dist = c['x0'] - prev_c['x1']
            if dist > gap_threshold:  # Используем переданный порог
                if current_word_chars:
                    _flush_word_maybe_split_chords(current_word_chars, words_simulated)
                    current_word_chars = []

        if is_space:
            if current_word_chars:
                _flush_word_maybe_split_chords(current_word_chars, words_simulated)
                current_word_chars = []
        else:
            current_word_chars.append(c)

    if current_word_chars:
        _flush_word_maybe_split_chords(current_word_chars, words_simulated)
    return words_simulated


def _flush_word(current_word_chars, out_list):
    """Добавляет текущее «слово» из current_word_chars в out_list в формате (x0,y0,x1,y1,text)."""
    wx0 = current_word_chars[0]['x0']
    wy0 = min(ch['y0'] for ch in current_word_chars)
    wx1 = current_word_chars[-1]['x1']
    wy1 = max(ch['y1'] for ch in current_word_chars)
    wtext = "".join(ch['char'] for ch in current_word_chars)
    out_list.append((wx0, wy0, wx1, wy1, wtext))


def _flush_word_maybe_split_chords(current_word_chars, out_list):
    """
    Сбрасывает накопленное слово в out_list. Если в слове несколько аккордов — разбивает по границам
    аккордов и добавляет каждый фрагмент отдельно с реальными координатами из символов.
    """
    text = "".join(c['char'] for c in current_word_chars)
    parts = _split_chord_word_by_chords(text)
    if len(parts) == 1:
        _flush_word(current_word_chars, out_list)
        return
    idx = 0
    for part in parts:
        chunk = current_word_chars[idx : idx + len(part)]
        idx += len(part)
        if chunk:
            _flush_word(chunk, out_list)


class PdfToChordProConverter:
    """Конвертирует PDF-файлы песен в формат ChordPro (.cho)."""

    def __init__(self, input_dir="input_pdf", output_dir="output_cho", use_word_mode=False):
        self.input_dir = Path(input_dir)
        self.output_dir = Path(output_dir)
        self.db_manager = DatabaseManager()
        self.parsing_report = []
        self.rule14_report = []  # New list for Rule 14 report
        self.use_word_mode = use_word_mode

    def log(self, message):
        self.parsing_report.append(message)
        print(message)

    def log_rule14(self, message):
        self.rule14_report.append(message)

    def save_report(self):
        with open("parsing_report.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(self.parsing_report))

        if self.rule14_report:
            with open("rule14_report.txt", "w", encoding="utf-8") as f:
                f.write("\n".join(self.rule14_report))

    def process_all(self):
        if not self.input_dir.exists():
            self.log(f"Директория {self.input_dir} не найдена.")
            return

        pdf_files = list(self.input_dir.glob("*.pdf"))
        if not pdf_files:
            self.log(f"В директории {self.input_dir} не найдено PDF файлов.")
            return

        self.log(f"Найдено {len(pdf_files)} PDF файлов.")
        if self.use_word_mode:
            self.log("Режим: WORDS (Классический)")
        else:
            self.log("Режим: CHARS (Высокая точность)")

        for pdf_file in pdf_files:
            try:
                self.process_file(pdf_file)
            except Exception as e:
                self.log(f"ОШИБКА обработки {pdf_file.name}: {e}")
                import traceback
                traceback.print_exc()

        self.save_report()

    def process_file(self, pdf_path):
        self.log(f"Обработка {pdf_path.name}...")

        song_num = self._extract_song_number(pdf_path.name)
        metadata = {}
        if song_num:
            metadata = self.db_manager.get_song_metadata(song_num)

        doc = fitz.open(pdf_path)
        all_lines = []
        for page_num, page in enumerate(doc):
            page_lines = self._extract_lines_from_page(page, pdf_path.name, page_num=page_num)
            all_lines.extend(page_lines)

        chordpro_content = self._convert_lines_to_chordpro(all_lines, metadata, pdf_path.name)

        output_path = self.output_dir / (pdf_path.stem + ".cho")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(chordpro_content)
        self.log(f"  Сохранено в {output_path}")

    def _extract_song_number(self, filename):
        match = re.match(r'^(\d+)', filename)
        if match:
            return int(match.group(1))
        return None

    def _extract_lines_from_page(self, page, filename="", page_num=0):
        # Dispatcher
        if self.use_word_mode:
            return self._extract_lines_from_page_words(page)

        # Try chars first
        lines = self._extract_lines_from_page_chars(page, page_num=page_num)

        # Heuristic check: if we got lines but they seem empty of content or weird (e.g. no spaces found ever), fallback?
        # For now, let's rely on the extraction logic itself to return None/Empty if it fails to find chars.

        # Check if we actually found distinct chars with spaces
        has_spaces = False
        total_chars = 0
        for l in lines:
            for c in l.get('chars', []):
                total_chars += 1
                if c['char'] == ' ':
                    has_spaces = True
                    break
            if has_spaces: break

        if lines and total_chars > 0 and not has_spaces:
             # WARNING: Rawdict found chars but NO spaces. This might be a PDF where spaces are gaps.
             # However, our logic relies on explicit spaces or gaps being detected.
             # If rawdict didn't report spaces, maybe we should fallback?
             # Let's log a warning but proceed unless it's critical.
             # Actually, the user said "if spaces are missing... fallback".
             self.log(f"--WARNING--: В файле {filename} не найдены явные пробелы (rawdict). Переключение в режим WORDS.")
             return self._extract_lines_from_page_words(page)

        if not lines and total_chars == 0:
             # Fallback if rawdict returns nothing (e.g. scanned image pdf?)
             # Words mode might handle it better or at least fail same way.
             return self._extract_lines_from_page_words(page)

        return lines

    def _extract_lines_from_page_words(self, page):
        words = page.get_text("words")
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
        for y in sorted_ys:
            line_words = sorted(lines[y], key=lambda w: w[0])
            line_words = self._refine_chord_line_words(line_words)
            text_content = " ".join([w[4] for w in line_words])

            # Calculate geometry
            y0s = [w[1] for w in line_words]
            y1s = [w[3] for w in line_words]
            line_top = min(y0s)
            line_bottom = max(y1s)
            line_height = line_bottom - line_top

            processed_lines.append({
                'y': y,
                'top': line_top,
                'bottom': line_bottom,
                'height': line_height,
                'words': line_words,
                'text': text_content,
                'is_chord_line': self._check_is_chord_line(line_words)
            })

        return processed_lines

    def _extract_lines_from_page_chars(self, page, page_num=0):
        raw = page.get_text("rawdict")
        lines_map = {}  # y -> list of char objects

        blocks = raw.get("blocks", [])
        for block in blocks:
            if "lines" not in block: continue
            for line in block["lines"]:
                spans = line["spans"]
                for span in spans:
                    chars = span.get("chars", [])
                    for c_obj in chars:
                        c = c_obj["c"]
                        bbox = c_obj["bbox"]
                        # bbox: x0, y0, x1, y1
                        # Группируем по нижней границе (y1), чтобы подстрочные индексы попадали в ту же строку
                        y1 = bbox[3]

                        # Поиск строки по y1 (базовая линия)
                        found_y = None
                        for y in lines_map.keys():
                            if abs(y - y1) < TOLERANCE_Y:
                                found_y = y
                                break

                        if found_y is None:
                            found_y = y1
                            lines_map[found_y] = []

                        lines_map[found_y].append({
                            'char': c,
                            'x0': bbox[0],
                            'y0': bbox[1],
                            'x1': bbox[2],
                            'y1': bbox[3]
                        })

        sorted_ys = sorted(lines_map.keys())
        processed_lines = []

        for y_idx, y in enumerate(sorted_ys):
            chars = sorted(lines_map[y], key=lambda c: c['x0'])

            # Reconstruct text
            text_content = "".join([c['char'] for c in chars])

            if not text_content.strip(): continue # Skip lines with only whitespace

            if not chars:
                continue

            # Вычисляем высоту строки ДО разбиения на слова (нужна для адаптивного порога)
            line_top = min(c['y0'] for c in chars)
            line_bottom = max(c['y1'] for c in chars)
            line_height = line_bottom - line_top

            # Вычисляем адаптивный порог для этой строки на основе размера шрифта
            adaptive_gap_threshold = _calculate_adaptive_gap_threshold(chars, line_height)

            # Совместимость с check_is_chord_line: «слова» из символов
            # Передаём адаптивный порог в функцию разбиения на слова
            words_simulated = _chars_to_simulated_words(chars, gap_threshold=adaptive_gap_threshold)

            processed_lines.append({
                'y': y,
                'top': line_top,
                'bottom': line_bottom,
                'height': line_height,
                'chars': chars,        # NEW field
                'words': words_simulated, # Compatibility field
                'text': text_content,
                'is_chord_line': self._check_is_chord_line(words_simulated)
            })

        return processed_lines

    def _check_is_chord_line(self, words):
        if not words:
            return False
        chord_count = sum(
            1 for w in words
            if CHORD_PATTERN.match(w[4].strip(".,;:()[]|")) or w[4].strip() in STRUCTURAL_CHARS
        )
        total_tokens = len(words)
        return (chord_count / total_tokens) >= CHORD_LINE_RATIO if total_tokens > 0 else False

    def _refine_chord_line_words(self, line_words):
        """
        Для строки аккордов разбивает каждое слово по границам аккордов и назначает каждому
        фрагменту виртуальный bbox пропорционально смещению в слове (в WORDS нет посимвольных координат).
        """
        if not line_words or not self._check_is_chord_line(line_words):
            return line_words
        refined = []
        for w in line_words:
            wx0, wy0, wx1, wy1 = w[0], w[1], w[2], w[3]
            text = w[4]
            parts = _split_chord_word_by_chords(text)
            if len(parts) == 1:
                refined.append(w)
                continue
            width = wx1 - wx0
            L = len(text)
            if L <= 0:
                refined.append(w)
                continue
            offset = 0
            for part in parts:
                x0_part = wx0 + width * (offset / L)
                x1_part = wx0 + width * ((offset + len(part)) / L)
                offset += len(part)
                refined.append((x0_part, wy0, x1_part, wy1, part))
        return refined

    def _build_chordpro_headers(self, metadata, filename, lines):
        """Формирует список строк заголовка ChordPro: title, tempo, time, key."""
        out = []
        title = metadata.get('title')
        if not title:
            title = re.sub(r'^\d+\s+', '', Path(filename).stem)
        out.append(f"{{title: {title}}}")
        if metadata.get('tempo'):
            out.append(f"{{tempo: {metadata['tempo']}}}")
        if metadata.get('time'):
            out.append(f"{{time: {metadata['time']}}}")
        key = self._detect_key_global(lines)
        if key:
            out.append(f"{{key: {key}}}")
        return out

    def _filter_capo_from_lines(self, lines):
        """Убирает строки с Capo из списка и возвращает (отфильтрованные строки, список директив capo)."""
        filtered = []
        capo_directives = []
        for line in lines:
            if "Capo" in line['text'] and len(line['text']) < 20:
                m = re.search(r'Capo\s+(\d+)', line['text'])
                if m:
                    capo_directives.append(f"{{capo: {m.group(1)}}}")
            else:
                filtered.append(line)
        return filtered, capo_directives

    def _vertical_gap(self, above_line, below_line):
        """Возвращает вертикальный зазор между строками, если он больше порога, иначе 0.0."""
        raw = below_line['top'] - above_line['bottom']
        if raw <= 0:
            return 0.0
        thresh = max(above_line['height'], below_line['height']) * GAP_THRESHOLD_RATIO
        return raw if raw > thresh else 0.0

    def _convert_lines_to_chordpro(self, lines, metadata, filename):
        self.current_filename = filename
        self.current_song_rule14_sections = []
        output = []

        output.extend(self._build_chordpro_headers(metadata, filename, lines))
        lines, capo_directives = self._filter_capo_from_lines(lines)
        output.extend(capo_directives)
        output.append("")

        # --- Linear State Machine ---
        current_section_type = None # 'grid', 'verse', 'chorus', 'bridge', 'tag'
        current_section_lines = []
        current_label = ""

        # Track lines (indices) that were already processed as look-ahead markers
        processed_marker_indices = set()

        i = 0
        while i < len(lines):
            line = lines[i]
            text = line['text'].strip()

            # If this line was already processed as a marker for the previous section (via lookahead), skip classification
            if i in processed_marker_indices:
                # It's already part of the current section (the one we just opened)
                current_section_lines.append(line)
                i += 1
                continue

            # --- Hybrid Section Detection ---

            # 1. Keyword Trigger
            keyword_type, keyword_label = self._classify_section_start(text)

            # Rule 18: Marker might be on NEXT line (if current is chords)
            # Only check if current line itself is NOT a marker
            # AND there is NO visual break between current chords and next marker
            if not keyword_type and line['is_chord_line'] and i + 1 < len(lines):
                next_line = lines[i+1]
                next_text = next_line['text'].strip()

                gap_to_next = self._vertical_gap(line, next_line)

                # Only look ahead if NO break
                if gap_to_next == 0.0:
                    ns_type, ns_label = self._classify_section_start(next_text)
                    if ns_type:
                        keyword_type = ns_type
                        keyword_label = ns_label
                        # Mark next line as processed marker so we don't trigger a new section on it
                        processed_marker_indices.add(i + 1)

            # 2. Visual Break Trigger
            visual_break = False
            gap = 0.0
            if i > 0:
                gap = self._vertical_gap(lines[i - 1], line)
                visual_break = gap > 0

            # 3. Decision Logic
            new_section_type = None
            new_label = ""

            # Case A: Both Keyword and Visual Break -> Confirmed new section
            if keyword_type and visual_break:
                new_section_type = keyword_type
                new_label = keyword_label

            # Case B: Only Visual Break -> Force new section (Unknown) + Warning
            elif visual_break and not keyword_type:
                new_section_type = 'unknown'
                new_label = ""
                self.log(f"--WARNING-- [{filename}:Строка {i}]: Обнаружен визуальный разрыв (Gap: {gap:.1f}) без ключевого слова в строке '{text[:30]}...'. Начало новой секции.")

            # Case C: Only Keyword -> New section + Warning
            elif keyword_type and not visual_break:
                new_section_type = keyword_type
                new_label = keyword_label
                # Don't warn on very first line or if it looks like start of file logic might apply
                if i > 0:
                     self.log(f"--WARNING-- [{filename}:Строка {i+1}]: Найдено ключевое слово '{text[:30]}...' без визуального разрыва (Gap: {gap:.1f}). Проверьте форматирование.")

            if new_section_type:
                # Close previous section
                if current_section_type:
                    output.extend(self._flush_section(current_section_type, current_label, current_section_lines))
                    output.append("")

                # Start new section
                current_section_type = new_section_type
                current_label = new_label
                current_section_lines = []

                current_section_lines.append(line)
                i += 1
                continue

            # If no new section start, add to current
            if not current_section_type:
                # Default to 'unknown' instead of 'verse'
                current_section_type = 'unknown'
                current_label = ""

            current_section_lines.append(line)
            i += 1

        # Flush last section
        if current_section_type:
             output.extend(self._flush_section(current_section_type, current_label, current_section_lines))

        # Rule 14 Report generation for this file
        if self.current_song_rule14_sections:
             title = Path(filename).stem
             report_msg = f"{'-'*30}\nПесня: {title}\n" + "\n\n".join(self.current_song_rule14_sections)
             self.log_rule14(report_msg)

        return "\n".join(output)

    def _classify_section_start(self, text):
        # Returns (type, label) or (None, None)
        if text.startswith("Intro"): return ('grid', "Intro")
        if text.startswith("Instrumental") or text.startswith("Instr"): return ('grid', "Instr.")
        if text.startswith("Outro"): return ('grid', "Outro")

        if re.match(r'^\d+\.', text): return ('verse', re.match(r'^(\d+\.)', text).group(1))

        if "Пр." in text or "Припев" in text:
            # Check if it has colon
            return ('chorus', text.split(':')[0] + ":" if ":" in text else text)

        # Rule 9: Normalize Pre-Chorus
        keywords = ["Пре-припев", "Пред-припев", "Пре-пр", "Пред-пр"]
        if any(k in text for k in keywords):
             # If colon exists -> It's a Header -> Normalize to "Пре-пр.X:"
             if ":" in text:
                 num_match = re.search(r'(\d+)', text)
                 num_suffix = f".{num_match.group(1)}" if num_match else "."
                 normalized_label = f"Пре-пр{num_suffix}:"
                 return ('chorus', normalized_label)
             else:
                 # If NO colon -> It's a Reference -> Return as is (no colon)
                 return ('chorus', text)

        if "Bridge" in text or "Бридж" in text:
             return ('bridge', text.split(':')[0] + ":" if ":" in text else text)

        if text.startswith("Tag"): return ('tag', "Tag:")
        if text.startswith("End"): return ('tag', "End:") # Keep real label for differentiation

        return (None, None)

    def _flush_section(self, section_type, label, lines):
        if not lines: return []

        if section_type == 'grid':
            return self._process_grid_block(lines, label)

        if section_type == 'unknown':
            # Heuristic Rule 17:
            # If >= 4 lines -> Verse
            # If < 4 lines -> Comment Block
            if len(lines) >= 4:
                return self._process_verse_chorus_block(lines, 'verse', label)
            else:
                return self._process_comment_block(lines)

        return self._process_verse_chorus_block(lines, section_type, label)

    def _process_comment_block(self, lines):
        output = []
        # Warning for multiline comments
        if len(lines) > 1:
            line_preview = lines[0]['text'][:30] if lines else "Empty"
            self.log(f"--WARNING--: Обнаружен многострочный блок комментария ({len(lines)} строк): '{line_preview}...'")

        for line in lines:
            text = line['text'].strip()
            if text:
                output.append(f"{{comment: {text}}}")
        return output

    def _process_grid_block(self, block, label):
        output = []
        if not label.endswith(":"): label += ":"
        output.append(f"{{start_of_grid: {label}}}")

        for line in block:
            text = line['text']
            # Clean header from line (e.g. "Intro: | A |" -> "| A |")
            clean_text = re.sub(r'^(Intro|Instrumental|Instr|Outro|Вступление|Проигрыш|Tag|End|Кода)[:\s]*', '', text, flags=re.IGNORECASE).strip()

            if not clean_text: continue

            # Format
            formatted = clean_text.replace("//:", "|:").replace("://", ":|")
            # Replace pipe with space-pipe-space only if NOT preceded by colon (start repeat) AND NOT followed by colon (end repeat)
            formatted = re.sub(r'(?<!:)\|(?!:)', ' | ', formatted)

            # Normalize spaces around repeat signs
            formatted = re.sub(r'\|\:\s*', '|: ', formatted)
            formatted = re.sub(r'\s*\:\|', ' :|', formatted)
            formatted = re.sub(r'\s+', ' ', formatted).strip()

            output.append(formatted)

        output.append("{end_of_grid}")
        return output

    def _process_verse_chorus_block(self, block, block_type, label_text):
        output = []

        # Tags
        start_tag = "{start_of_verse}"
        end_tag = "{end_of_verse}"
        is_ref = False

        if label_text:
            if ":" in label_text:
                 # Normalize 'End:' to 'Tag:' for output only
                 display_label = label_text
                 if display_label.startswith("End:"):
                     display_label = "Tag:" # Standardize

                 if block_type == 'chorus': start_tag = f"{{start_of_chorus: {display_label}}}"; end_tag = "{end_of_chorus}"
                 elif block_type == 'bridge': start_tag = f"{{start_of_bridge: {display_label}}}"; end_tag = "{end_of_bridge}"
                 elif block_type == 'tag': start_tag = f"{{start_of_chorus: {display_label}}}"; end_tag = "{end_of_chorus}"
            elif re.match(r'^\d+\.', label_text):
                 start_tag = f"{{start_of_verse: {label_text}}}"
            else:
                 is_ref = True
                 start_tag = f"{{comment: {label_text}}}"

                 # Check if the block has more content (lines) than just the header
                 content_lines_count = 0
                 content_lines = []
                 for l in block:
                     if l['text'].strip() != label_text.strip():
                         content_lines_count += 1
                         content_lines.append(l['text'].strip())

                 if content_lines_count > 0:
                      header_preview = block[0]['text'].strip() if block else ''
                      if len(header_preview) > 60:
                          header_preview = header_preview[:60] + "..."
                      mismatch_note = " (метка секции отличается от первой строки)" if block and label_text.strip() != block[0]['text'].strip() else ""
                      self.log(f"--WARNING--: Блок Reference/Comment содержит дополнительный контент ({content_lines_count} строк) помимо заголовка: '{header_preview}'{mismatch_note}")

        output.append(start_tag)

        # If it is a reference, append extra lines as separate comments
        if is_ref:
            if 'content_lines' in locals() and content_lines:
                for line_text in content_lines:
                    if line_text:
                        output.append(f"{{comment: {line_text}}}")
            return output

        # --- Rule 14: Calculate Block Indentation ---
        block_indent = self._calculate_block_indent(block, filename=self.current_filename if hasattr(self, 'current_filename') else "")
        # --------------------------------------------

        i = 0
        while i < len(block):
            line = block[i]
            is_chord = line['is_chord_line']

            # Правило: строка с двоеточием и ключевым словом — это заголовок секции из PDF.
            # Её нельзя выводить как обычный текст (иначе получится дублирование после {start_of_*: ...}),
            # особенно если display-метка была нормализована/сокращена (например, "Пре-припев 1:" -> "Пре-пр.1:").
            if label_text and (not is_chord):
                raw_text = line['text'].strip()
                if ":" in raw_text:
                    detected_type, detected_label = self._classify_section_start(raw_text)
                    if detected_type == block_type and detected_label and (":" in detected_label):
                        # Если в этой же строке после двоеточия есть текст — оставляем только его.
                        after_colon = raw_text.split(":", 1)[1].strip()
                        if after_colon:
                            output.append(after_colon)
                        i += 1
                        continue

            # Check if this line is just the header (e.g. "Пр.1:")
            if label_text and line['text'].strip() == label_text.strip():
                i += 1
                continue

            if is_chord:
                if i + 1 < len(block):
                    next_line = block[i+1]
                    if not next_line['is_chord_line']:
                        # Merge
                        merged = self._merge_chords_and_lyrics(line, next_line, label_text, block_indent)
                        output.append(merged)
                        i += 2
                        continue
                    else:
                        output.append(self._merge_chords_and_lyrics(line, None, block_indent=block_indent))
                        i += 1
                else:
                    output.append(self._merge_chords_and_lyrics(line, None, block_indent=block_indent))
                    i += 1
            else:
                # Lyric line
                text = line['text']
                # Strip label if it's at the start
                if label_text:
                     # Remove label from text
                     clean_text = text.strip()
                     if clean_text.startswith(label_text):
                          text = text.replace(label_text, "", 1).strip()
                     elif label_text == "Tag:" and clean_text.startswith("End:"):
                          text = text.replace("End:", "", 1).strip()

                # Apply block indent to lyric-only lines too
                if block_indent > 0:
                     text = (" " * block_indent) + text.strip()

                output.append(text)
                i += 1

        output.append(end_tag)

        if block_indent > 0:
            self.current_song_rule14_sections.append("\n".join(output))

        return output

    def _calculate_block_indent(self, block, filename=""):
        max_indent = 0
        i = 0
        while i < len(block):
            line = block[i]
            if line['is_chord_line'] and i + 1 < len(block):
                next_line = block[i+1]
                if not next_line['is_chord_line']:
                    # Potential pairing
                    indent = self._get_line_indent_requirement(line, next_line)
                    if indent > max_indent:
                        max_indent = indent
            i += 1
        return max_indent

    def _get_line_indent_requirement(self, chord_line, lyric_line):
        # Helper to calculate indent if leading chord exists
        if not chord_line or not lyric_line: return 0

        c_words = chord_line.get('words', [])
        l_words = lyric_line.get('words', [])

        if not c_words: return 0

        first_chord_x = c_words[0][0]

        # Determine first lyric text X (ignoring spaces)
        first_lyric_x = None

        if 'chars' in lyric_line and lyric_line['chars']:
             for c in lyric_line['chars']:
                 if c['char'].strip(): # Found non-space char
                     first_lyric_x = c['x0']
                     break
        elif l_words:
             # Words usually don't contain leading spaces if parsed by pymupdf words
             first_lyric_x = l_words[0][0]

        if first_lyric_x is None:
             return 0

        # Правило 14: ведущий аккорд — только если левее текста больше чем на ширину одной буквы (~12 pt)
        if first_chord_x >= first_lyric_x - 5.0:
            return 0

        first_chord_end_x = c_words[0][2]
        # Конец первой буквы текста: по chars точно, иначе приближение по первому слову
        first_letter_end_x = None
        if 'chars' in lyric_line and lyric_line['chars']:
            for c in lyric_line['chars']:
                if c['char'].strip():
                    first_letter_end_x = c['x1']
                    break
        elif l_words:
            w0, w2, w4 = l_words[0][0], l_words[0][2], l_words[0][4]
            n = max(1, len(w4))
            first_letter_end_x = w0 + (w2 - w0) / n

        if first_letter_end_x is not None and first_chord_end_x >= first_letter_end_x:
            # Аккорд по смыслу на первой букве (сдвиг в PDF из-за верстки) — правило 14 не применяем
            return 0

        chord_text = c_words[0][4].strip("[]()|")
        return INDENT_BY_CHORD_LEN.get(len(chord_text), INDENT_DEFAULT)

    def _detect_key_global(self, lines):
        count = 0
        key_prefix = re.compile(r'^([A-H](?:b|#)?)')
        for line in lines:
            if count >= KEY_SCAN_MAX_LINES:
                break
            if line['is_chord_line']:
                count += 1
                for w in line['words']:
                    m = key_prefix.match(w[4].strip(".,;:()[]|"))
                    if m:
                        return m.group(1)
        return None

    def _merge_chords_and_lyrics(self, chord_line, lyric_line, label_to_strip="", block_indent=0):
        # Dispatch to appropriate method
        if lyric_line and 'chars' in lyric_line:
            return self._merge_using_chars(chord_line, lyric_line, label_to_strip, block_indent)
        else:
            return self._merge_using_words(chord_line, lyric_line, label_to_strip, block_indent)

    def _merge_using_words(self, chord_line, lyric_line, label_to_strip="", block_indent=0):
        # Original logic (Legacy)
        chord_words = [list(w) for w in chord_line['words']] if chord_line else []
        lyric_words = [list(w) for w in lyric_line['words']] if lyric_line else []

        for w in lyric_words:
            w[4] = w[4].replace("//:", "||:").replace("://", ":||")
        for w in chord_words:
            w[4] = w[4].replace("//:", "||:").replace("://", ":||")

        # Strip label
        if lyric_line and label_to_strip:
             full_text = lyric_line['text']
             label_clean = label_to_strip.strip()
             if full_text.strip().startswith("End:"):
                 full_text = full_text.replace("End:", "", 1).strip()
                 if lyric_words and "End" in lyric_words[0][4]:
                      lyric_words = lyric_words[1:]
             if full_text.strip().startswith(label_clean):
                  if lyric_words and (lyric_words[0][4].strip() == label_clean or label_clean in lyric_words[0][4]):
                       lyric_words = lyric_words[1:]
                  elif lyric_words and len(lyric_words) > 1 and (lyric_words[0][4] + lyric_words[1][4]).replace(" ", "") == label_clean.replace(" ", ""):
                       lyric_words = lyric_words[2:]

        # Check for leading chord (правило 14: порог 12 pt ≈ одна буква; не leading, если аккорд заканчивается не раньше конца первой буквы)
        is_leading = False
        if chord_words and lyric_words:
            first_chord_x = chord_words[0][0]
            first_chord_end_x = chord_words[0][2]
            first_lyric_x = lyric_words[0][0]
            w0, w2, w4 = lyric_words[0][0], lyric_words[0][2], lyric_words[0][4]
            first_letter_end_x = w0 + (w2 - w0) / max(1, len(w4))
            if first_chord_x < first_lyric_x - 5.0 and first_chord_end_x < first_letter_end_x:
                is_leading = True

        # Events
        events = []
        delayed_chords = []

        for w in lyric_words:
            events.append({'type': 'lyric', 'x': w[0], 'end': w[2], 'text': w[4]})

        wi = 0
        while wi < len(chord_words):
            w = chord_words[wi]
            raw_text = w[4]
            x = w[0]
            if "(:" in raw_text:
                parts = raw_text.split("(:", 1)
                first_chord = parts[0].strip()
                remainder = parts[1]

                # Сбор полного текста второй вольты (до первой ")"), если разбито на несколько слов
                if ")" in remainder:
                    volt2_content_raw = remainder.split(")")[0].rstrip(")").strip()
                else:
                    volt2_content_raw = remainder.strip()
                    wi += 1
                    while wi < len(chord_words):
                        next_w = chord_words[wi]
                        next_text = next_w[4]
                        if ")" in next_text:
                            volt2_content_raw += " " + next_text.split(")")[0].rstrip(")").strip()
                            break
                        else:
                            volt2_content_raw += " " + next_text.strip()
                        wi += 1
                second_content = volt2_content_raw

                # Первая вольта: [(1.] отодвигаем влево на два символа (как в CHARS)
                x_volt1 = x - 10.0
                if lyric_words:
                    for lw in lyric_words:
                        l_start, l_end = lw[0], lw[2]
                        if l_start <= x <= l_end:
                            L = len(lw[4])
                            if L > 0:
                                char_width = (l_end - l_start) / L
                                x_volt1 = x - 2 * char_width
                            break
                events.append({'type': 'chord', 'x': x_volt1, 'text': "[(1.]"})
                if first_chord:
                    events.append({'type': 'chord', 'x': x, 'text': f"[{first_chord})]"})

                # Вторая вольта — в delayed_chords
                raw_tokens = re.split(r'(?<!/)(?=[A-H])', second_content)
                blocks = []
                current_block_content = "(2."
                for token in raw_tokens:
                    if not token: continue
                    if token[0] in "ABCDEFGH":
                        blocks.append(current_block_content)
                        current_block_content = token
                    else:
                        current_block_content += token
                blocks.append(current_block_content)
                volt2_parts = []
                for idx, b in enumerate(blocks):
                    s = b.strip()
                    if idx == len(blocks) - 1:
                        volt2_parts.append(f"[{s})]")
                    else:
                        volt2_parts.append(f"[{s}]")
                delayed_chords.extend(volt2_parts)
                wi += 1
            else:
                # Разбиваем слово по границам аккордов (напр. "|A2|E" -> ["|", "A2|", "E"])
                parts = _split_chord_word_by_chords(raw_text)
                for part in parts:
                    events.append({'type': 'chord', 'x': x, 'text': f"[{part}]"})
                wi += 1

        events.sort(key=lambda e: e['x'])

        if not lyric_words:
             line_str = (" " * block_indent) + " ".join([e['text'] for e in events if e['type']=='chord'])
             if delayed_chords: line_str += "".join(delayed_chords)
             return line_str

        combined_text = ""
        chord_queue = [e for e in events if e['type'] == 'chord']

        # Handling leading chord specifically
        if is_leading and chord_queue:
             # Take the first chord (leading)
             c = chord_queue.pop(0)
             combined_text += c['text']

             # Pad to reach block_indent
             # We assume block_indent corresponds to column index
             current_len = len(combined_text)
             if current_len < block_indent:
                 combined_text += " " * (block_indent - current_len)

             if not combined_text.endswith(" "): combined_text += " "

        for w in lyric_words:
            w_start = w[0]
            w_end = w[2]
            w_text = w[4]

            while chord_queue and chord_queue[0]['x'] < w_start:
                c = chord_queue.pop(0)
                combined_text += c['text']
                if not combined_text.endswith(" "): combined_text += " "

            inserts = []
            while chord_queue and chord_queue[0]['x'] >= w_start and chord_queue[0]['x'] <= w_end:
                 c = chord_queue.pop(0)
                 rel = (c['x'] - w_start) / (w_end - w_start)
                 idx = int(round(len(w_text) * rel))
                 inserts.append((idx, c['text']))

            # Части одного «слова» аккордов имеют один x → один idx; объединяем в один блок, чтобы порядок сохранился
            by_idx = {}
            for idx, txt in inserts:
                by_idx.setdefault(idx, []).append(txt)
            inserts = [(idx, "".join(texts)) for idx, texts in by_idx.items()]

            inserts.sort(key=lambda x: x[0], reverse=True)
            for idx, txt in inserts:
                w_text = w_text[:idx] + txt + w_text[idx:]

            combined_text += w_text + " "

        # Оставшиеся аккорды (после последнего слова) — без пробелов между блоками
        combined_text = combined_text.rstrip()
        while chord_queue:
            c = chord_queue.pop(0)
            combined_text += c['text']

        if delayed_chords:
             combined_text = combined_text.rstrip()  # убрать пробел перед второй вольтой
             combined_text += "".join(delayed_chords)

        if is_leading:
            return combined_text.strip()
        else:
            return (" " * block_indent) + combined_text.strip()

    def _merge_using_chars(self, chord_line, lyric_line, label_to_strip="", block_indent=0):
        # NEW logic using character precision

        # 1. Сначала готовим символы текста (lyric_chars), так как они нужны для расчета вольты
        lyric_chars = []
        if lyric_line and 'chars' in lyric_line:
             lyric_chars = lyric_line['chars']

        # Пропускаем пробелы в начале, чтобы избежать двойного отступа
        start_idx = 0
        for i, c in enumerate(lyric_chars):
            if c['char'].strip():
                start_idx = i
                break
        else:
            if lyric_chars: start_idx = len(lyric_chars)

        lyric_chars = lyric_chars[start_idx:]

        # Логика удаления метки (Label stripping)
        if label_to_strip:
             clean_label = label_to_strip.strip()
             matched_idx = -1
             temp_str = ""
             for i, c in enumerate(lyric_chars):
                 if c['char'].strip() == "": continue
                 temp_str += c['char']
                 if temp_str == clean_label or temp_str.startswith(clean_label):
                     matched_idx = i
                     break
                 if len(temp_str) > len(clean_label) + 5: break

             if matched_idx >= 0:
                 lyric_chars = lyric_chars[matched_idx+1:]
                 while lyric_chars and not lyric_chars[0]['char'].strip():
                     lyric_chars.pop(0)

        # 2. Теперь готовим аккорды
        chord_words = [list(w) for w in chord_line['words']] if chord_line else []
        for w in chord_words:
            w[4] = w[4].replace("//:", "||:").replace("://", ":||")

        chord_events = []
        delayed_chords = []

        wi = 0
        while wi < len(chord_words):
            w = chord_words[wi]
            raw_text = w[4]
            x = w[0]
            formatted_chord = ""
            if "(:" in raw_text:
                parts = raw_text.split("(:", 1)
                pre_text = parts[0].strip()
                remainder = parts[1]

                # Сбор полного текста второй вольты (до первой ")"), если разбито на несколько слов
                if ")" in remainder:
                    volt2_content_raw = remainder.split(")")[0].rstrip(")").strip()
                else:
                    volt2_content_raw = remainder.strip()
                    wi += 1
                    while wi < len(chord_words):
                        next_w = chord_words[wi]
                        next_text = next_w[4]
                        if ")" in next_text:
                            volt2_content_raw += " " + next_text.split(")")[0].rstrip(")").strip()
                            break
                        else:
                            volt2_content_raw += " " + next_text.strip()
                        wi += 1
                second_content = volt2_content_raw

                # --- 1. Первая вольта (1. ---
                target_x_for_volt1 = x - 10.0
                if lyric_chars:
                    closest_char_idx = -1
                    min_dist = float('inf')
                    for ci, c in enumerate(lyric_chars):
                        dist = abs(c['x0'] - x)
                        if dist < min_dist:
                            min_dist = dist
                            closest_char_idx = ci
                    if closest_char_idx != -1:
                        target_idx = closest_char_idx - 2
                        if target_idx >= 0:
                            target_x_for_volt1 = lyric_chars[target_idx]['x0']
                        elif target_idx == -1:
                            if lyric_chars:
                                w_char = lyric_chars[0]['x1'] - lyric_chars[0]['x0']
                                target_x_for_volt1 = lyric_chars[0]['x0'] - w_char
                        else:
                            if lyric_chars:
                                w_char = lyric_chars[0]['x1'] - lyric_chars[0]['x0']
                                target_x_for_volt1 = lyric_chars[0]['x0'] - (2 * w_char)

                if pre_text:
                    # Аккорд в том же слове что и "(:"
                    chord_events.append({'x': target_x_for_volt1, 'text': "[(1.]"})
                    chord_events.append({'x': x, 'text': f"[{pre_text})]"})
                else:
                    # "(: " отдельным словом — модифицируем предыдущий аккорд
                    if chord_events:
                        last = chord_events[-1]
                        t = last['text'].strip("[] ")
                        last['text'] = f"[{t})]"
                        prev_x = last['x']
                        target_x_prev = prev_x - 10.0
                        if lyric_chars:
                            closest_ci = -1
                            min_d = float('inf')
                            for ci, c in enumerate(lyric_chars):
                                d = abs(c['x0'] - prev_x)
                                if d < min_d:
                                    min_d = d
                                    closest_ci = ci
                            if closest_ci != -1:
                                t_idx = closest_ci - 2
                                if t_idx >= 0:
                                    target_x_prev = lyric_chars[t_idx]['x0']
                                elif t_idx == -1 and lyric_chars:
                                    w_c = lyric_chars[0]['x1'] - lyric_chars[0]['x0']
                                    target_x_prev = lyric_chars[0]['x0'] - w_c
                                else:
                                    if lyric_chars:
                                        w_c = lyric_chars[0]['x1'] - lyric_chars[0]['x0']
                                        target_x_prev = lyric_chars[0]['x0'] - (2 * w_c)
                        chord_events.append({'x': target_x_prev, 'text': "[(1.]"})
                    else:
                        chord_events.append({'x': target_x_for_volt1, 'text': "[(1.]"})

                # --- 2. Вторая вольта (2. ---
                raw_tokens = re.split(r'(?<!/)(?=[A-H])', second_content)
                blocks = []
                current_block_content = "(2."
                for token in raw_tokens:
                    if not token: continue
                    is_chord_start = token[0] in "ABCDEFGH"
                    if is_chord_start:
                        blocks.append(current_block_content)
                        current_block_content = token
                    else:
                        current_block_content += token
                blocks.append(current_block_content)
                volt2_parts = []
                for idx, b in enumerate(blocks):
                    s = b.strip()
                    if idx == len(blocks) - 1:
                        volt2_parts.append(f"[{s})]")
                    else:
                        volt2_parts.append(f"[{s}]")
                delayed_chords.extend(volt2_parts)
                wi += 1
                continue

            # Разбиваем слово по границам аккордов (напр. "|A2|E" -> ["|", "A2|", "E"])
            parts = _split_chord_word_by_chords(raw_text)
            for part in parts:
                chord_events.append({'x': x, 'text': f"[{part}]"})
            wi += 1

        chord_events.sort(key=lambda e: e['x'])

        if not lyric_chars:
             # Если текста нет, возвращаем только аккорды
             result_str = ""
             for ch in chord_events:
                 result_str += ch['text']
             final_res = (" " * block_indent) + result_str
             if delayed_chords: final_res += "".join(delayed_chords)
             return final_res.replace("//:", "||:").replace("://", ":||")

        # 3. Слияние (Merge Logic)
        result_str = ""
        processed_chords = [False] * len(chord_events)
        indent_inserted = False

        for i, c in enumerate(lyric_chars):
            c_center = (c['x0'] + c['x1']) / 2
            c_start = c['x0']

            # Вставляем аккорды перед символом
            for ch_i, chord in enumerate(chord_events):
                if processed_chords[ch_i]: continue

                should_insert = False
                if chord['x'] < c_start:
                    should_insert = True
                elif chord['x'] >= c_start and chord['x'] < c['x1']:
                    if chord['x'] < c_center:
                         should_insert = True

                if should_insert:
                    result_str += chord['text']
                    processed_chords[ch_i] = True

            # Вставляем отступ
            if not indent_inserted:
                result_str += " " * block_indent
                indent_inserted = True

            # Умный пробел
            if i > 0:
                prev_c = lyric_chars[i-1]
                dist = c_start - prev_c['x1']
                if dist > 2.0 and c['char'] != ' ' and prev_c['char'] != ' ':
                     result_str += " "

            result_str += c['char']

        # Добавляем оставшиеся аккорды (после последней буквы — без пробелов между блоками)
        result_str = result_str.rstrip()
        for ch_i, chord in enumerate(chord_events):
            if not processed_chords[ch_i]:
                result_str += chord['text']

        if delayed_chords:
             result_str = result_str.rstrip()  # убрать пробел перед второй вольтой
             result_str += "".join(delayed_chords)

        if not indent_inserted:
             result_str = (" " * block_indent) + result_str

        return result_str.rstrip().replace("//:", "||:").replace("://", ":||")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Convert PDF to ChordPro")
    parser.add_argument("-w", "--words-mode", action="store_true", help="Use legacy word-level parsing (no space detection)")
    args = parser.parse_args()

    converter = PdfToChordProConverter(use_word_mode=args.words_mode)
    converter.process_all()
