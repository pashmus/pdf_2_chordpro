"""
Конвертер PDF в ChordPro.
Извлекает текст и аккорды из PDF и формирует .cho файлы.

Доступные флаги CLI:
- `-w`, `--words-mode` — включить legacy-режим парсинга на уровне слов.
- `-db`, `--write-db` — записывать результат в БД (если поле `song.chordpro` пустое).
"""
import re
from pathlib import Path

import fitz  # PyMuPDF

from converter_cli import parse_args
from converter_processing import (
    calculate_block_indent,
    classify_section_start,
    flush_section,
    get_line_indent_requirement,
    merge_chords_and_lyrics,
    merge_using_chars,
    merge_using_words,
    process_comment_block,
    process_grid_block,
    process_verse_chorus_block,
)
from converter_utils import (
    CHORD_LINE_RATIO,
    CHORD_PATTERN,
    GAP_THRESHOLD_RATIO,
    KEY_CONFIDENCE_THRESHOLD,
    STRUCTURAL_CHARS,
    TOLERANCE_Y,
    calculate_adaptive_gap_threshold,
    chars_to_simulated_words,
    german_to_standard_in_brackets,
    normalize_chord_for_key_compare,
    split_chord_word_by_chords,
)
from database_manager import DatabaseManager
from key_analyser import analyze_key, parse_chordpro_content

# Директории, которые можно вручную менять перед запуском.
input_dir = "input_pdf"
output_dir = "output_cho"


class PdfToChordProConverter:
    """Конвертирует PDF-файлы песен в формат ChordPro (.cho)."""

    def __init__(self, input_dir=input_dir, output_dir=output_dir, use_word_mode=False, write_db=False):
        self.input_dir = Path(input_dir)
        self.output_dir = Path(output_dir)
        self.db_manager = DatabaseManager()
        self.parsing_report = []
        self.rule14_report = []
        self.use_word_mode = use_word_mode
        self.write_db = write_db
        self.files_processed = 0
        self.db_updated_count = 0

    def log(self, message):
        self.parsing_report.append(message)
        print(message)

    def log_issue(self, message):
        """Добавляет сообщение в буфер аномалий текущего файла (выводится только при наличии проблем)."""
        self.current_file_issues.append(message)

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

        self.files_processed = 0
        self.db_updated_count = 0
        for pdf_file in pdf_files:
            try:
                self.process_file(pdf_file)
            except Exception as e:
                self.log("----------------------------")
                self.log(f"Обработка {pdf_file.name}...")
                self.log(f"ОШИБКА обработки {pdf_file.name}: {e}")
                import traceback
                traceback.print_exc()

        self.log(f"Обработано файлов: {self.files_processed}.")
        if self.write_db:
            self.log(f"Записано в БД: {self.db_updated_count} песен.")
        self.save_report()

    def process_file(self, pdf_path):
        self.current_file_issues = []

        song_num = self._extract_song_number(pdf_path.name)
        metadata = {}
        if song_num:
            metadata = self.db_manager.get_song_metadata(song_num)
            if metadata is None:
                msg = f"ПРЕДУПОЖДЕНИЕ: песня с номером {song_num} не найдена в БД ({pdf_path.name})."
                self.log_issue(msg)
                metadata = {}

        doc = fitz.open(pdf_path)
        all_lines = []
        for page_num, page in enumerate(doc):
            page_lines = self._extract_lines_from_page(page, pdf_path.name, page_num=page_num)
            all_lines.extend(page_lines)

        chordpro_content = self._convert_lines_to_chordpro(all_lines, metadata, pdf_path.name)
        chordpro_content = german_to_standard_in_brackets(chordpro_content)

        chords = parse_chordpro_content(chordpro_content)
        key_str, confidence, note = analyze_key(chords)

        if key_str is None:
            self.log_issue("--WARNING--: тональность не определена")
        else:
            if confidence is not None and confidence < KEY_CONFIDENCE_THRESHOLD:
                self.log_issue(
                    f"--WARNING--: низкая уверенность определения тональности "
                    f"({confidence:.2f} < {KEY_CONFIDENCE_THRESHOLD:.2f}), тональность {key_str}"
                )

            # Сверяем с первым аккордом из того же набора, который использовался для анализа тональности.
            # Сравнение строгое: только точное совпадение после нормализации аккорда.
            first_chord_normalized = None
            first_chord_raw = None
            for c in chords:
                if c and c.strip():
                    first_chord_raw = c.strip()
                    first_chord_normalized = normalize_chord_for_key_compare(first_chord_raw)
                    if first_chord_normalized:
                        break

            if first_chord_normalized and key_str != first_chord_normalized:
                self.log_issue(
                    f"--WARNING--: определённая тональность ({key_str}) не совпадает с первым аккордом "
                    f"из набора анализа ({first_chord_raw} -> {first_chord_normalized})."
                )

            # Вставка {key: ...} после {time: ...} или {tempo: ...} или {title: ...} (как раньше, перед capo)
            key_line = "\n{key: " + key_str + "}"
            if re.search(r'\{time:[^}]+\}', chordpro_content):
                chordpro_content = re.sub(r'(\{time:[^}]+\})', r'\1' + key_line, chordpro_content, count=1)
            elif re.search(r'\{tempo:[^}]+\}', chordpro_content):
                chordpro_content = re.sub(r'(\{tempo:[^}]+\})', r'\1' + key_line, chordpro_content, count=1)
            else:
                chordpro_content = re.sub(r'(\{title:[^}]+\})', r'\1' + key_line, chordpro_content, count=1)

        output_path = self.output_dir / (pdf_path.stem + ".cho")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(chordpro_content)

        # Запись chordpro в БД только при флаге -db/--write-db и только если поле NULL
        if self.write_db:
            if song_num:
                try:
                    updated = self.db_manager.update_song_chordpro_if_null(song_num, chordpro_content)
                    if updated:
                        self.db_updated_count += 1
                    else:
                        self.log_issue("  --WARNING--: chordpro в БД не обновлён (поле уже заполнено или запись не выполнена).")
                except Exception as e:
                    self.log_issue(f"  Ошибка записи chordpro в БД: {e}")
            else:
                self.log_issue("  --WARNING--: chordpro в БД не добавлено (в имени файла нет номера песни).")

        if self.current_file_issues:
            self.log("----------------------------")
            self.log(f"Обработка {pdf_path.name}...")
            for msg in self.current_file_issues:
                self.log(msg)

        self.files_processed += 1

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
             self.log_issue(f"--WARNING--: В файле {filename} не найдены явные пробелы (rawdict). Переключение в режим WORDS.")
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
            adaptive_gap_threshold = calculate_adaptive_gap_threshold(chars, line_height)

            # Совместимость с check_is_chord_line: «слова» из символов
            # Передаём адаптивный порог в функцию разбиения на слова
            words_simulated = chars_to_simulated_words(chars, gap_threshold=adaptive_gap_threshold)

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
            parts = split_chord_word_by_chords(text)
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
        """Формирует список строк заголовка ChordPro: title, tempo, time (key добавляется позже из key_analyser)."""
        out = []
        title = metadata.get('title')
        if not title:
            title = re.sub(r'^\d+\s+', '', Path(filename).stem)
        out.append(f"{{title: {title}}}")
        if metadata.get('tempo'):
            out.append(f"{{tempo: {metadata['tempo']}}}")
        if metadata.get('time'):
            out.append(f"{{time: {metadata['time']}}}")
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

            keyword_source_text = None
            keyword_source_index = None

            # If this line was already processed as a marker for the previous section (via lookahead), skip classification
            if i in processed_marker_indices:
                # It's already part of the current section (the one we just opened)
                current_section_lines.append(line)
                i += 1
                continue

            # --- Hybrid Section Detection ---

            # 1. Keyword Trigger
            keyword_type, keyword_label = self._classify_section_start(text)
            if keyword_type:
                keyword_source_text = text
                keyword_source_index = i

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
                        keyword_source_text = next_text
                        keyword_source_index = i + 1
                        # Mark next line as processed marker so we don't trigger a new section on it
                        processed_marker_indices.add(i + 1)

            # 2. Visual Break Trigger
            visual_break = False
            gap = 0.0
            raw_gap = 0.0
            thresh = 0.0
            if i > 0:
                prev_line = lines[i - 1]
                raw_gap = line['top'] - prev_line['bottom']
                if raw_gap > 0:
                    thresh = max(prev_line['height'], line['height']) * GAP_THRESHOLD_RATIO
                gap = self._vertical_gap(prev_line, line)
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
                self.log_issue(
                    f"--WARNING-- [{filename}:Строка {i}]: Обнаружен визуальный разрыв "
                    f"(Gap_eff: {gap:.1f}, Gap_raw: {raw_gap:.1f}, Thresh: {thresh:.1f}) "
                    f"без ключевого слова в строке '{text[:30]}...'. Начало новой секции."
                )

            # Case C: Only Keyword -> New section + Warning
            elif keyword_type and not visual_break:
                new_section_type = keyword_type
                new_label = keyword_label
                # Don't warn on very first line or if it looks like start of file logic might apply
                if i > 0:
                    kw_line_no = (keyword_source_index + 1) if keyword_source_index is not None else (i + 1)
                    kw_preview = (keyword_source_text or text)[:30]
                    self.log_issue(
                        f"--WARNING-- [{filename}:Строка {kw_line_no}]: Найдено ключевое слово "
                        f"'{kw_preview}...' без значимого визуального разрыва "
                        f"(Gap_eff: {gap:.1f}, Gap_raw: {raw_gap:.1f}, Thresh: {thresh:.1f}). "
                        f"Проверьте форматирование."
                    )

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
        return classify_section_start(text)

    def _flush_section(self, section_type, label, lines):
        return flush_section(self, section_type, label, lines)

    def _process_comment_block(self, lines):
        return process_comment_block(self, lines)

    def _process_grid_block(self, block, label):
        return process_grid_block(block, label)

    def _process_verse_chorus_block(self, block, block_type, label_text):
        return process_verse_chorus_block(self, block, block_type, label_text)

    def _calculate_block_indent(self, block, filename=""):
        return calculate_block_indent(block)

    def _get_line_indent_requirement(self, chord_line, lyric_line):
        return get_line_indent_requirement(chord_line, lyric_line)

    def _merge_chords_and_lyrics(self, chord_line, lyric_line, label_to_strip="", block_indent=0):
        return merge_chords_and_lyrics(chord_line, lyric_line, label_to_strip, block_indent)

    def _merge_using_words(self, chord_line, lyric_line, label_to_strip="", block_indent=0):
        return merge_using_words(chord_line, lyric_line, label_to_strip, block_indent)

    def _merge_using_chars(self, chord_line, lyric_line, label_to_strip="", block_indent=0):
        return merge_using_chars(chord_line, lyric_line, label_to_strip, block_indent)

if __name__ == "__main__":
    args = parse_args()

    converter = PdfToChordProConverter(use_word_mode=args.words_mode, write_db=args.write_db)
    converter.process_all()
