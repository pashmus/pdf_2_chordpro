import fitz  # PyMuPDF
import re
import os
from pathlib import Path
from database_manager import DatabaseManager

class PdfToChordProConverter:
    def __init__(self, input_dir="input_pdf", output_dir="output_cho"):
        self.input_dir = Path(input_dir)
        self.output_dir = Path(output_dir)
        self.db_manager = DatabaseManager()
        self.parsing_report = []

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
            page_lines = self._extract_lines_from_page(page)
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

    def _extract_lines_from_page(self, page):
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

    def _check_is_chord_line(self, words):
        if not words: return False
        chord_count = 0
        chord_pattern = r'^[A-H](?:b|#)?(?:2|5|m|maj|min|dim|aug|sus|add)?(?:[0-9]{1,2})?(?:/[A-H](?:b|#)?)?$'
        total_tokens = len(words)
        for w in words:
            clean = w[4].strip(".,;:()[]|")
            # Treat structural chars as valid for "chord line" detection
            if re.match(chord_pattern, clean) or w[4].strip() in ["//:", "://", "|", "|:", ":|"]:
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

            # Check if this line is just the header (e.g. "Пр.1:")
            if label_text and line['text'].strip() == label_text.strip():
                i += 1
                continue

            if is_chord:
                if i + 1 < len(block):
                    next_line = block[i+1]
                    if not next_line['is_chord_line']:
                        # Check if next line is actually a header for NEXT section?
                        # No, flushing logic handles that boundaries.

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
                     # "1. Text" -> "Text"
                     # "Пр.1: Text" -> "Text"

                     # Also strip "End:" if we are in a 'tag' block but outputting 'Tag:'
                     # (Handled by generic stripper below if label_text was passed correctly)

                     # The label_text passed here is "End:" (from classify).

                     # Clean text logic:
                     clean_text = text.strip()
                     if clean_text.startswith(label_text):
                          text = text.replace(label_text, "", 1).strip()
                     elif label_text == "Tag:" and clean_text.startswith("End:"):
                          # Fallback if somehow mismatched?
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
        # Convert tuples to lists to allow modification
        chord_words = [list(w) for w in chord_line['words']] if chord_line else []
        lyric_words = [list(w) for w in lyric_line['words']] if lyric_line else []

        # Pre-process repeat signs in raw text (before merge)
        # Convert //: to ||: and :// to :|| in both lyrics and chords
        for w in lyric_words:
            w[4] = w[4].replace("//:", "||:").replace("://", ":||")
        
        for w in chord_words:
            w[4] = w[4].replace("//:", "||:").replace("://", ":||")

        # 1. Strip label from lyrics
        if lyric_line and label_to_strip:
             # Heuristic: Drop words until we pass the length of label
             full_text = lyric_line['text']
             # Ensure we strip ignoring case and potential suffix
             label_clean = label_to_strip.strip()

             # Also try stripping "End:" if it's there but not in label_to_strip
             # (This handles the case where "End:" is in text but label was "Tag:")
             if full_text.strip().startswith("End:"):
                 full_text = full_text.replace("End:", "", 1).strip()
                 # We need to adjust lyric_words too.
                 if lyric_words and "End" in lyric_words[0][4]:
                      lyric_words = lyric_words[1:] # Rough removal

             if full_text.strip().startswith(label_clean):
                  # This is tricky with raw words.
                  # Simple: if first word is the label (e.g. "1."), drop it.
                  if lyric_words and (lyric_words[0][4].strip() == label_clean or label_clean in lyric_words[0][4]):
                       lyric_words = lyric_words[1:]
                  elif lyric_words and len(lyric_words) > 1 and (lyric_words[0][4] + lyric_words[1][4]).replace(" ", "") == label_clean.replace(" ", ""):
                       lyric_words = lyric_words[2:]

        # 2. Indentation (Rule 14)
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

        # 3. Events
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
                 idx = int(len(w_text) * rel)
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

if __name__ == "__main__":
    converter = PdfToChordProConverter()
    converter.process_all()
