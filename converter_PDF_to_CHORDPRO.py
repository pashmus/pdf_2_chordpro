import fitz  # PyMuPDF
import re
import os
import argparse
from pathlib import Path
from database_manager import DatabaseManager

class PdfToChordProConverter:
    def __init__(self, input_dir="input_pdf", output_dir="output_cho", use_word_mode=False):
        self.input_dir = Path(input_dir)
        self.output_dir = Path(output_dir)
        self.db_manager = DatabaseManager()
        self.parsing_report = []
        self.use_word_mode = use_word_mode

    def log(self, message):
        self.parsing_report.append(message)
        print(message)

    def save_report(self):
        with open("parsing_report.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(self.parsing_report))

    def process_all(self):
        if not self.input_dir.exists():
            self.log(f"Directory {self.input_dir} does not exist.")
            return

        pdf_files = list(self.input_dir.glob("*.pdf"))
        if not pdf_files:
            self.log(f"No PDF files found in {self.input_dir}")
            return

        self.log(f"Found {len(pdf_files)} PDF files.")
        if self.use_word_mode:
            self.log("Mode: WORDS (Classic)")
        else:
            self.log("Mode: CHARS (High Precision)")

        for pdf_file in pdf_files:
            try:
                self.process_file(pdf_file)
            except Exception as e:
                self.log(f"ERROR processing {pdf_file.name}: {e}")
                import traceback
                traceback.print_exc()

        self.save_report()

    def process_file(self, pdf_path):
        self.log(f"Processing {pdf_path.name}...")

        song_num = self._extract_song_number(pdf_path.name)
        metadata = {}
        if song_num:
            metadata = self.db_manager.get_song_metadata(song_num)

        doc = fitz.open(pdf_path)
        all_lines = []
        for page in doc:
            page_lines = self._extract_lines_from_page(page, pdf_path.name)
            all_lines.extend(page_lines)

        chordpro_content = self._convert_lines_to_chordpro(all_lines, metadata, pdf_path.name)

        output_path = self.output_dir / (pdf_path.stem + ".cho")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(chordpro_content)
        self.log(f"  Saved to {output_path}")

    def _extract_song_number(self, filename):
        match = re.match(r'^(\d+)', filename)
        if match:
            return int(match.group(1))
        return None

    def _extract_lines_from_page(self, page, filename=""):
        # Dispatcher
        if self.use_word_mode:
            return self._extract_lines_from_page_words(page)
        
        # Try chars first
        lines = self._extract_lines_from_page_chars(page)
        
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
             self.log(f"WARNING: No explicit space characters found in {filename} (rawdict). Falling back to WORDS mode.")
             return self._extract_lines_from_page_words(page)

        if not lines and total_chars == 0:
             # Fallback if rawdict returns nothing (e.g. scanned image pdf?)
             # Words mode might handle it better or at least fail same way.
             return self._extract_lines_from_page_words(page)

        return lines

    def _extract_lines_from_page_words(self, page):
        words = page.get_text("words")
        TOLERANCE_Y = 3
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

    def _extract_lines_from_page_chars(self, page):
        raw = page.get_text("rawdict")
        TOLERANCE_Y = 3
        lines_map = {} # y -> list of char objects

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
                        
                        y_center = (bbox[1] + bbox[3]) / 2
                        
                        # Find line
                        found_y = None
                        for y in lines_map.keys():
                            if abs(y - y_center) < TOLERANCE_Y:
                                found_y = y
                                break
                        
                        if found_y is None:
                            found_y = y_center
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
        
        for y in sorted_ys:
            chars = sorted(lines_map[y], key=lambda c: c['x0'])
            
            # Reconstruct text
            text_content = "".join([c['char'] for c in chars])
            if not text_content.strip(): continue # Skip lines with only whitespace

            # Reconstruct 'words' for compatibility with check_is_chord_line
            # Simple splitter by space + Smart space detection based on distance
            words_simulated = []
            current_word_chars = []
            
            for i, c in enumerate(chars):
                is_space = (c['char'] == ' ')
                
                # Check distance to previous char (Smart Space)
                if i > 0 and not is_space:
                    prev_c = chars[i-1]
                    dist = c['x0'] - prev_c['x1']
                    # Threshold: if gap is > 2.0 (heuristic), consider it a word break
                    if dist > 2.0:
                        # Flush word if exists
                        if current_word_chars:
                            wx0 = current_word_chars[0]['x0']
                            wy0 = min(ch['y0'] for ch in current_word_chars)
                            wx1 = current_word_chars[-1]['x1']
                            wy1 = max(ch['y1'] for ch in current_word_chars)
                            wtext = "".join(ch['char'] for ch in current_word_chars)
                            words_simulated.append((wx0, wy0, wx1, wy1, wtext))
                            current_word_chars = []

                if is_space:
                    if current_word_chars:
                        # Flush word
                        wx0 = current_word_chars[0]['x0']
                        wy0 = min(ch['y0'] for ch in current_word_chars)
                        wx1 = current_word_chars[-1]['x1']
                        wy1 = max(ch['y1'] for ch in current_word_chars)
                        wtext = "".join(ch['char'] for ch in current_word_chars)
                        words_simulated.append((wx0, wy0, wx1, wy1, wtext))
                        current_word_chars = []
                else:
                    current_word_chars.append(c)
            
            if current_word_chars:
                wx0 = current_word_chars[0]['x0']
                wy0 = min(ch['y0'] for ch in current_word_chars)
                wx1 = current_word_chars[-1]['x1']
                wy1 = max(ch['y1'] for ch in current_word_chars)
                wtext = "".join(ch['char'] for ch in current_word_chars)
                words_simulated.append((wx0, wy0, wx1, wy1, wtext))

            if not chars: continue

            line_top = min(c['y0'] for c in chars)
            line_bottom = max(c['y1'] for c in chars)
            line_height = line_bottom - line_top
            
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
        if not words: return False
        chord_count = 0
        chord_pattern = r'^[A-H](?:b|#)?(?:2|5|m|maj|min|dim|aug|sus|add)?(?:[0-9]{1,2})?(?:/[A-H](?:b|#)?)?$'
        total_tokens = len(words)
        for w in words:
            # w is tuple (x0, y0, x1, y1, text, ...)
            text_val = w[4]
            clean = text_val.strip(".,;:()[]|")
            # Treat structural chars as valid for "chord line" detection
            if re.match(chord_pattern, clean) or text_val.strip() in ["//:", "://", "|", "|:", ":|"]:
                chord_count += 1
        return (chord_count / total_tokens) >= 0.4 if total_tokens > 0 else False

    def _convert_lines_to_chordpro(self, lines, metadata, filename):
        output = []

        # Headers
        title = metadata.get('title')
        if not title:
             title = Path(filename).stem
             title = re.sub(r'^\d+\s+', '', title)
        output.append(f"{{title: {title}}}")

        if metadata:
            if metadata.get('tempo'):
                output.append(f"{{tempo: {metadata['tempo']}}}")
            if metadata.get('time'):
                output.append(f"{{time: {metadata['time']}}}")

        # Rule 20: Key Detection (Global scan of first few chords)
        key = self._detect_key_global(lines)
        if key:
            output.append(f"{{key: {key}}}")

        # Pre-process: Capo
        lines_clean = []
        for line in lines:
            if "Capo" in line['text'] and len(line['text']) < 20:
                match = re.search(r'Capo\s+(\d+)', line['text'])
                if match: output.append(f"{{capo: {match.group(1)}}}")
            else:
                lines_clean.append(line)
        lines = lines_clean

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

                # Check gap to next line
                gap_to_next = 0.0
                raw_gap_next = next_line['top'] - line['bottom']
                if raw_gap_next > 0:
                    max_h_next = max(line['height'], next_line['height'])
                    thresh_next = max_h_next * 0.5
                    if raw_gap_next > thresh_next:
                        gap_to_next = raw_gap_next # There is a break!

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
                prev_line = lines[i-1]
                # Calculate gap
                raw_gap = line['top'] - prev_line['bottom']

                # Only consider positive gaps (no overlap)
                if raw_gap > 0:
                    max_h = max(line['height'], prev_line['height'])
                    threshold = max_h * 0.5

                    if raw_gap > threshold:
                        visual_break = True
                        gap = raw_gap

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
                self.log(f"WARNING [{filename}:Line {i}]: Found visual break (Gap: {gap:.1f}) without keyword trigger at line '{text[:30]}...'. Starting new section.")

            # Case C: Only Keyword -> New section + Warning
            elif keyword_type and not visual_break:
                new_section_type = keyword_type
                new_label = keyword_label
                # Don't warn on very first line or if it looks like start of file logic might apply
                if i > 0:
                     self.log(f"WARNING [{filename}:Line {i+1}]: Found keyword '{text[:30]}...' without visual break (Gap: {gap:.1f}). Check formatting.")

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
            self.log(f"WARNING: Multiline comment block found ({len(lines)} lines): '{line_preview}...'")

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
                     display_label = "Tag:" + display_label[4:] # Or just "Tag:"?
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
                      self.log(f"WARNING: Reference/Comment block has extra content ({content_lines_count} lines) besides header: '{label_text}'")

        output.append(start_tag)
        
        # If it is a reference, append extra lines as separate comments
        if is_ref: 
            if 'content_lines' in locals() and content_lines:
                for line_text in content_lines:
                    if line_text:
                        output.append(f"{{comment: {line_text}}}")
            return output

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
                        merged = self._merge_chords_and_lyrics(line, next_line, label_text)
                        output.append(merged)
                        i += 2
                        continue
                    else:
                        output.append(self._merge_chords_and_lyrics(line, None))
                        i += 1
                else:
                    output.append(self._merge_chords_and_lyrics(line, None))
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

                output.append(text)
                i += 1

        output.append(end_tag)
        return output

    def _detect_key_global(self, lines):
        # Scan first 20 chord lines
        count = 0
        chord_pattern = r'^([A-H](?:b|#)?)'
        for line in lines:
            if count > 20: break
            if line['is_chord_line']:
                count += 1
                for w in line['words']:
                     clean = w[4].strip(".,;:()[]|")
                     match = re.match(chord_pattern, clean)
                     if match: return match.group(1)
        return None

    def _merge_chords_and_lyrics(self, chord_line, lyric_line, label_to_strip=""):
        # Dispatch to appropriate method
        if lyric_line and 'chars' in lyric_line:
            return self._merge_using_chars(chord_line, lyric_line, label_to_strip)
        else:
            return self._merge_using_words(chord_line, lyric_line, label_to_strip)

    def _merge_using_words(self, chord_line, lyric_line, label_to_strip=""):
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

        # Indentation
        indent_spaces = 0
        if chord_words and lyric_words:
            first_chord_x = chord_words[0][0]
            first_lyric_x = lyric_words[0][0]
            if first_chord_x < first_lyric_x - 2.0:
                chord_text = chord_words[0][4]
                l = len(chord_text.strip("[]()|"))
                if l == 1: indent_spaces = 2
                elif l == 2: indent_spaces = 4
                elif l == 3: indent_spaces = 5
                elif l == 4: indent_spaces = 6
                elif l == 5: indent_spaces = 7
                else: indent_spaces = 8
        indent_str = " " * indent_spaces

        # Events
        events = []
        delayed_chords = []

        for w in lyric_words:
            events.append({'type': 'lyric', 'x': w[0], 'end': w[2], 'text': w[4]})

        chord_pattern = r'^[A-H](?:b|#)?(?:2|5|m|maj|min|dim|aug|sus|add)?(?:[0-9]{1,2})?(?:/[A-H](?:b|#)?)?$'
        for w in chord_words:
            raw_text = w[4]
            x = w[0]
            formatted_chord = ""
            if "(:" in raw_text:
                parts = raw_text.split("(:")
                first = parts[0]
                second = parts[1].replace(")", "")
                if first: formatted_chord = f"[(1.][{first})]"
                else: formatted_chord = "[(1.]"
                delayed_chords.append(f"[(2.][{second})]")
            elif re.match(chord_pattern, raw_text.strip(".,;:()[]|")):
                 if raw_text.startswith("(") and raw_text.endswith(")"): formatted_chord = f"[{raw_text}]"
                 else: formatted_chord = f"[{raw_text}]"
            else:
                 formatted_chord = f"[{raw_text}]"
            events.append({'type': 'chord', 'x': x, 'text': formatted_chord})

        events.sort(key=lambda e: e['x'])

        if not lyric_words:
             line_str = indent_str + " ".join([e['text'] for e in events if e['type']=='chord'])
             if delayed_chords: line_str += "".join(delayed_chords)
             return line_str

        combined_text = ""
        chord_queue = [e for e in events if e['type'] == 'chord']

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

            inserts.sort(key=lambda x: x[0], reverse=True)
            for idx, txt in inserts:
                w_text = w_text[:idx] + txt + w_text[idx:]

            combined_text += w_text + " "

        while chord_queue:
            c = chord_queue.pop(0)
            combined_text += c['text'] + " "

        if delayed_chords:
             combined_text += "".join(delayed_chords)

        return indent_str + combined_text.strip()

    def _merge_using_chars(self, chord_line, lyric_line, label_to_strip=""):
        # NEW logic using character precision
        
        # Prepare chords
        chord_words = [list(w) for w in chord_line['words']] if chord_line else []
        for w in chord_words:
            w[4] = w[4].replace("//:", "||:").replace("://", ":||")

        chord_events = []
        delayed_chords = []
        chord_pattern = r'^[A-H](?:b|#)?(?:2|5|m|maj|min|dim|aug|sus|add)?(?:[0-9]{1,2})?(?:/[A-H](?:b|#)?)?$'

        for w in chord_words:
            raw_text = w[4]
            x = w[0]
            formatted_chord = ""
            if "(:" in raw_text:
                parts = raw_text.split("(:")
                first = parts[0]
                second = parts[1].replace(")", "")
                if first: formatted_chord = f"[(1.][{first})]"
                else: formatted_chord = "[(1.]"
                delayed_chords.append(f"[(2.][{second})]")
            elif re.match(chord_pattern, raw_text.strip(".,;:()[]|")):
                 if raw_text.startswith("(") and raw_text.endswith(")"): formatted_chord = f"[{raw_text}]"
                 else: formatted_chord = f"[{raw_text}]"
            else:
                 formatted_chord = f"[{raw_text}]"
            chord_events.append({'x': x, 'text': formatted_chord})
        
        chord_events.sort(key=lambda e: e['x'])

        # Indentation (same logic)
        indent_spaces = 0
        if chord_words and lyric_line and lyric_line['chars']:
            first_chord_x = chord_words[0][0]
            first_lyric_x = lyric_line['chars'][0]['x0']
            if first_chord_x < first_lyric_x - 2.0:
                 chord_text = chord_words[0][4]
                 l = len(chord_text.strip("[]()|"))
                 if l == 1: indent_spaces = 2
                 elif l == 2: indent_spaces = 4
                 elif l == 3: indent_spaces = 5
                 elif l == 4: indent_spaces = 6
                 elif l == 5: indent_spaces = 7
                 else: indent_spaces = 8
        indent_str = " " * indent_spaces

        if not lyric_line:
             line_str = indent_str + " ".join([c['text'] for c in chord_events])
             if delayed_chords: line_str += "".join(delayed_chords)
             return line_str

        # Flatten lyrics chars
        # We need to filter out label if needed
        # Since we are working with raw chars, stripping label is harder.
        # But we constructed 'words' in lines too, we can use heuristic:
        # If 'text' starts with label, just drop N chars.
        
        lyric_chars = lyric_line['chars']
        
        if label_to_strip:
             full_text = lyric_line['text']
             label_clean = label_to_strip.strip()
             
             # Similar logic to words mode but on text
             chars_to_skip = 0
             text_to_check = full_text.strip()
             prefix_found = False
             
             if text_to_check.startswith("End:"):
                  prefix_found = True
                  # Find length of "End:" in chars? 
                  # Simple approach: rebuild text and skip
                  # This assumes chars are in order.
                  pass 

             # Simpler: just use full_text to decide how many chars to skip from start
             if full_text.lstrip().startswith(label_clean):
                 # Find index in full_text where label ends
                 start_idx = full_text.find(label_clean)
                 end_idx = start_idx + len(label_clean)
                 # Map this to char list. Chars might have spaces or not (spaces are chars now!)
                 # We need to find the char index that corresponds to text index.
                 
                 # Let's count
                 current_len = 0
                 cut_index = 0
                 for i, c in enumerate(lyric_chars):
                     current_len += len(c['char'])
                     if current_len > end_idx:
                         cut_index = i + 1
                         break
                 
                 # Refine: if the chars are just "1", ".", " " -> skip them
                 # It's safer to skip chars until we pass the label
                 
                 # Actually, let's just use the 'words' stripping logic to know WHICH words to skip, 
                 # then map words back to chars? No, too complex.
                 
                 # Let's stick to X coordinate!
                 # If we detected a label, we know its text.
                 # Any char that is "part of the label" should be skipped.
                 # Label is usually at the start.
                 
                 # Scan chars from start
                 match_chars = list(label_clean.replace(" ", ""))
                 # This is getting complicated with spaces.
                 
                 # Fallback: if label present, skip first few chars that look like label
                 pass

        # For now, let's assume label stripping is less critical for the specific char positioning 
        # or implement a simple X-based skip if needed.
        # But wait, if we don't strip label, we might insert chords into the label!
        # The user wants "1. Lyrics [Am]".
        
        # Let's try to match text prefix.
        if label_to_strip:
             clean_label = label_to_strip.strip()
             # Try to match chars at start
             matched_idx = -1
             temp_str = ""
             for i, c in enumerate(lyric_chars):
                 if c['char'].strip() == "": continue
                 temp_str += c['char']
                 if temp_str == clean_label or temp_str.startswith(clean_label):
                     matched_idx = i
                     break
                 # loose match?
                 if len(temp_str) > len(clean_label) + 5: break
             
             if matched_idx >= 0:
                 lyric_chars = lyric_chars[matched_idx+1:]


        # Merge Logic
        result_str = ""
        
        # We interleave chords into char stream
        # Algorithm:
        # iterate chars. For each char, check if any chord should be placed BEFORE it.
        # Position logic:
        # If chord.x < char.center -> place before char
        # If chord.x >= char.center -> place after char (which is before next char)
        
        # Actually, standard logic: place chord at specific point.
        # We iterate chords and insert them into text.
        
        current_char_idx = 0
        
        # We can reconstruct text by appending chars and chords
        
        # Add a sentinel char at the end (infinity X)
        lyric_chars_with_sentinel = lyric_chars + [{'x0': 999999, 'x1': 999999, 'char': ''}]
        
        last_x_end = 0
        if lyric_chars: last_x_end = lyric_chars[0]['x0']

        processed_chords = [False] * len(chord_events)
        
        for i, c in enumerate(lyric_chars):
            c_center = (c['x0'] + c['x1']) / 2
            c_start = c['x0']
            
            # Check for chords that belong BEFORE this char
            # A chord belongs before this char if:
            # 1. It is the first char and chord is before it.
            # 2. Chord is between prev char and this char.
            # 3. Chord is ON this char but in left half.
            
            # Find all chords that haven't been processed and should appear here
            for ch_i, chord in enumerate(chord_events):
                if processed_chords[ch_i]: continue
                
                should_insert = False
                
                # If chord is way before (e.g. indentation or previous space gap)
                if chord['x'] < c_start:
                    should_insert = True
                    
                # If chord is on the char
                elif chord['x'] >= c_start and chord['x'] < c['x1']:
                    # Check center
                    if chord['x'] < c_center:
                         should_insert = True
                    # else: wait for next iteration (insert after) or next char?
                    # If we don't insert now, we will visit this chord again on next char?
                    # No, if it's in right half, it should be inserted AFTER this char.
                    # which effectively means BEFORE next char.
                    # So we just skip it now.
                
                if should_insert:
                    result_str += chord['text']
                    processed_chords[ch_i] = True
            
            result_str += c['char']
            
        # Append remaining chords (at the end of line)
        for ch_i, chord in enumerate(chord_events):
            if not processed_chords[ch_i]:
                result_str += chord['text']

        if delayed_chords:
             result_str += "".join(delayed_chords)

        return indent_str + result_str.strip()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Convert PDF to ChordPro")
    parser.add_argument("-w", "--words-mode", action="store_true", help="Use legacy word-level parsing (no space detection)")
    args = parser.parse_args()

    converter = PdfToChordProConverter(use_word_mode=args.words_mode)
    converter.process_all()
