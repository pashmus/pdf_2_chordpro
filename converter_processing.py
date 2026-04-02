"""
Логика обработки секций и слияния аккордов с текстом для PDF -> ChordPro.
"""

import re

from converter_utils import INDENT_BY_CHORD_LEN, INDENT_DEFAULT, split_chord_word_by_chords


def classify_section_start(text):
    """Классифицирует старт секции и возвращает `(type, label)`."""
    if text.startswith("Intro"):
        return ("grid", "Intro")
    if text.startswith("Instrumental") or text.startswith("Instr"):
        return ("grid", "Instr.")
    if text.startswith("Outro"):
        return ("grid", "Outro")

    verse_match = re.match(r"^(\d+\.)", text)
    if verse_match:
        return ("verse", verse_match.group(1))

    if "Пр." in text or "Припев" in text:
        return ("chorus", text.split(":")[0] + ":" if ":" in text else text)

    keywords = ["Пре-припев", "Пред-припев", "Пре-пр", "Пред-пр"]
    if any(k in text for k in keywords):
        if ":" in text:
            num_match = re.search(r"(\d+)", text)
            num_suffix = f".{num_match.group(1)}" if num_match else "."
            normalized_label = f"Пре-пр{num_suffix}:"
            return ("chorus", normalized_label)
        return ("chorus", text)

    if "Bridge" in text or "Бридж" in text:
        return ("bridge", text.split(":")[0] + ":" if ":" in text else text)

    if text.startswith("Tag"):
        return ("tag", "Tag:")
    if text.startswith("End"):
        return ("tag", "End:")

    return (None, None)


def flush_section(converter, section_type, label, lines):
    """Сбрасывает накопленную секцию в ChordPro-строки."""
    if not lines:
        return []

    if section_type == "grid":
        return process_grid_block(lines, label)

    if section_type == "unknown":
        if len(lines) >= 4:
            return process_verse_chorus_block(converter, lines, "verse", label)
        return process_comment_block(converter, lines)

    return process_verse_chorus_block(converter, lines, section_type, label)


def process_comment_block(converter, lines):
    """Обрабатывает блок комментариев."""
    output = []
    if len(lines) > 1:
        line_preview = lines[0]["text"][:30] if lines else "Empty"
        converter.log_issue(
            f"--WARNING--: Обнаружен многострочный блок комментария ({len(lines)} строк): '{line_preview}...'"
        )

    for line in lines:
        text = line["text"].strip()
        if text:
            output.append(f"{{comment: {text}}}")
    return output


def process_grid_block(block, label):
    """Обрабатывает блок сетки аккордов (Intro/Instr/Outro и т.п.)."""
    output = []
    if not label.endswith(":"):
        label += ":"
    output.append(f"{{start_of_grid: {label}}}")

    for line in block:
        text = line["text"]
        clean_text = re.sub(
            r"^(Intro|Instrumental|Instr|Outro|Вступление|Проигрыш|Tag|End|Кода)[:\s]*",
            "",
            text,
            flags=re.IGNORECASE,
        ).strip()

        if not clean_text:
            continue

        formatted = clean_text.replace("//:", "|:").replace("://", ":|")
        formatted = re.sub(r"(?<!:)\|(?!:)", " | ", formatted)
        formatted = re.sub(r"\|\:\s*", "|: ", formatted)
        formatted = re.sub(r"\s*\:\|", " :|", formatted)
        formatted = re.sub(r"\s+", " ", formatted).strip()
        output.append(formatted)

    output.append("{end_of_grid}")
    return output


def process_verse_chorus_block(converter, block, block_type, label_text):
    """Обрабатывает куплет/припев/бридж/тэг блок."""
    output = []

    start_tag = "{start_of_verse}"
    end_tag = "{end_of_verse}"
    is_ref = False

    if label_text:
        if ":" in label_text:
            display_label = label_text
            if display_label.startswith("End:"):
                display_label = "Tag:"

            if block_type == "chorus":
                start_tag = f"{{start_of_chorus: {display_label}}}"
                end_tag = "{end_of_chorus}"
            elif block_type == "bridge":
                start_tag = f"{{start_of_bridge: {display_label}}}"
                end_tag = "{end_of_bridge}"
            elif block_type == "tag":
                start_tag = f"{{start_of_chorus: {display_label}}}"
                end_tag = "{end_of_chorus}"
        elif re.match(r"^\d+\.", label_text):
            start_tag = f"{{start_of_verse: {label_text}}}"
        else:
            is_ref = True
            start_tag = f"{{comment: {label_text}}}"

            content_lines_count = 0
            content_lines = []
            for line_obj in block:
                if line_obj["text"].strip() != label_text.strip():
                    content_lines_count += 1
                    content_lines.append(line_obj["text"].strip())

            if content_lines_count > 0:
                header_preview = block[0]["text"].strip() if block else ""
                if len(header_preview) > 60:
                    header_preview = header_preview[:60] + "..."
                mismatch_note = (
                    " (метка секции отличается от первой строки)"
                    if block and label_text.strip() != block[0]["text"].strip()
                    else ""
                )
                converter.log_issue(
                    f"--WARNING--: Блок Reference/Comment содержит дополнительный контент "
                    f"({content_lines_count} строк) помимо заголовка: '{header_preview}'{mismatch_note}"
                )

    output.append(start_tag)

    if is_ref:
        if "content_lines" in locals() and content_lines:
            for line_text in content_lines:
                if line_text:
                    output.append(f"{{comment: {line_text}}}")
        return output

    block_indent = calculate_block_indent(block)

    i = 0
    while i < len(block):
        line = block[i]
        is_chord = line["is_chord_line"]

        if label_text and (not is_chord):
            raw_text = line["text"].strip()
            if ":" in raw_text:
                detected_type, detected_label = classify_section_start(raw_text)
                if detected_type == block_type and detected_label and (":" in detected_label):
                    after_colon = raw_text.split(":", 1)[1].strip()
                    if after_colon:
                        output.append(after_colon)
                    i += 1
                    continue

        if label_text and line["text"].strip() == label_text.strip():
            i += 1
            continue

        if is_chord:
            if i + 1 < len(block):
                next_line = block[i + 1]
                if not next_line["is_chord_line"]:
                    merged = merge_chords_and_lyrics(line, next_line, label_text, block_indent)
                    output.append(merged)
                    i += 2
                    continue
                output.append(merge_chords_and_lyrics(line, None, block_indent=block_indent))
                i += 1
            else:
                output.append(merge_chords_and_lyrics(line, None, block_indent=block_indent))
                i += 1
        else:
            text = line["text"]
            if label_text:
                clean_text = text.strip()
                if clean_text.startswith(label_text):
                    text = text.replace(label_text, "", 1).strip()
                elif label_text == "Tag:" and clean_text.startswith("End:"):
                    text = text.replace("End:", "", 1).strip()

            if block_indent > 0:
                text = (" " * block_indent) + text.strip()

            output.append(text)
            i += 1

    output.append(end_tag)

    if block_indent > 0:
        converter.current_song_rule14_sections.append("\n".join(output))

    return output


def calculate_block_indent(block):
    """Вычисляет единый отступ блока по правилу 14."""
    max_indent = 0
    i = 0
    while i < len(block):
        line = block[i]
        if line["is_chord_line"] and i + 1 < len(block):
            next_line = block[i + 1]
            if not next_line["is_chord_line"]:
                indent = get_line_indent_requirement(line, next_line)
                if indent > max_indent:
                    max_indent = indent
        i += 1
    return max_indent


def get_line_indent_requirement(chord_line, lyric_line):
    """Считает требуемый отступ для пары аккорды/текст."""
    if not chord_line or not lyric_line:
        return 0

    c_words = chord_line.get("words", [])
    l_words = lyric_line.get("words", [])
    if not c_words:
        return 0

    first_chord_x = c_words[0][0]
    first_lyric_x = None

    if "chars" in lyric_line and lyric_line["chars"]:
        for c in lyric_line["chars"]:
            if c["char"].strip():
                first_lyric_x = c["x0"]
                break
    elif l_words:
        first_lyric_x = l_words[0][0]

    if first_lyric_x is None:
        return 0

    if first_chord_x >= first_lyric_x - 5.0:
        return 0

    first_chord_end_x = c_words[0][2]
    first_letter_end_x = None
    if "chars" in lyric_line and lyric_line["chars"]:
        for c in lyric_line["chars"]:
            if c["char"].strip():
                first_letter_end_x = c["x1"]
                break
    elif l_words:
        w0, w2, w4 = l_words[0][0], l_words[0][2], l_words[0][4]
        n = max(1, len(w4))
        first_letter_end_x = w0 + (w2 - w0) / n

    if first_letter_end_x is not None and first_chord_end_x >= first_letter_end_x:
        return 0

    chord_text = c_words[0][4].strip("[]()|")
    return INDENT_BY_CHORD_LEN.get(len(chord_text), INDENT_DEFAULT)


def merge_chords_and_lyrics(chord_line, lyric_line, label_to_strip="", block_indent=0):
    """Сливает линию аккордов и линию текста в одну строку ChordPro."""
    if lyric_line and "chars" in lyric_line:
        return merge_using_chars(chord_line, lyric_line, label_to_strip, block_indent)
    return merge_using_words(chord_line, lyric_line, label_to_strip, block_indent)


def merge_using_words(chord_line, lyric_line, label_to_strip="", block_indent=0):
    """Legacy-сшивка на основе координат слов."""
    chord_words = [list(w) for w in chord_line["words"]] if chord_line else []
    lyric_words = [list(w) for w in lyric_line["words"]] if lyric_line else []

    for w in lyric_words:
        w[4] = w[4].replace("//:", "||:").replace("://", ":||")
    for w in chord_words:
        w[4] = w[4].replace("//:", "||:").replace("://", ":||")

    if lyric_line and label_to_strip:
        full_text = lyric_line["text"]
        label_clean = label_to_strip.strip()
        if full_text.strip().startswith("End:"):
            full_text = full_text.replace("End:", "", 1).strip()
            if lyric_words and "End" in lyric_words[0][4]:
                lyric_words = lyric_words[1:]
        if full_text.strip().startswith(label_clean):
            if lyric_words and (lyric_words[0][4].strip() == label_clean or label_clean in lyric_words[0][4]):
                lyric_words = lyric_words[1:]
            elif lyric_words and len(lyric_words) > 1 and (
                lyric_words[0][4] + lyric_words[1][4]
            ).replace(" ", "") == label_clean.replace(" ", ""):
                lyric_words = lyric_words[2:]

    is_leading = False
    if chord_words and lyric_words:
        first_chord_x = chord_words[0][0]
        first_chord_end_x = chord_words[0][2]
        first_lyric_x = lyric_words[0][0]
        w0, w2, w4 = lyric_words[0][0], lyric_words[0][2], lyric_words[0][4]
        first_letter_end_x = w0 + (w2 - w0) / max(1, len(w4))
        if first_chord_x < first_lyric_x - 5.0 and first_chord_end_x < first_letter_end_x:
            is_leading = True

    events = []
    delayed_chords = []

    for w in lyric_words:
        events.append({"type": "lyric", "x": w[0], "end": w[2], "text": w[4]})

    wi = 0
    while wi < len(chord_words):
        w = chord_words[wi]
        raw_text = w[4]
        x = w[0]

        is_parenthesized_volt1 = False
        volt1_content = ""
        tokens_consumed = 0

        if raw_text.startswith("(") and not raw_text.startswith("(:"):
            if ")" in raw_text:
                inner = raw_text[1:].split(")")[0]
                if not inner.strip().startswith(":"):
                    is_parenthesized_volt1 = True
                    volt1_content = inner
                    tokens_consumed = 0
            else:
                found_closing = False
                future_content = []
                idx_offset = 1
                while wi + idx_offset < len(chord_words):
                    next_w_text = chord_words[wi + idx_offset][4]
                    future_content.append(next_w_text)
                    if ")" in next_w_text:
                        found_closing = True
                        break
                    idx_offset += 1

                if found_closing:
                    full_group = raw_text + "".join(future_content)
                    if not full_group.replace(" ", "").startswith("(:"):
                        is_parenthesized_volt1 = True
                        start_p = full_group.find("(")
                        end_p = full_group.rfind(")")
                        if start_p != -1 and end_p != -1:
                            volt1_content = full_group[start_p + 1 : end_p].strip()
                            tokens_consumed = idx_offset

        if is_parenthesized_volt1:
            target_x_for_volt1 = x - 10.0
            events.append({"type": "chord", "x": target_x_for_volt1, "text": "[(1.]"})

            volt1_parts = split_chord_word_by_chords(volt1_content)
            for idx, part in enumerate(volt1_parts):
                if idx == len(volt1_parts) - 1:
                    events.append({"type": "chord", "x": x, "text": f"[{part})]"})
                else:
                    events.append({"type": "chord", "x": x, "text": f"[{part}]"})

            wi += 1 + tokens_consumed
            continue

        if "(:" in raw_text:
            parts = raw_text.split("(:", 1)
            first_chord = parts[0].strip()
            remainder = parts[1]

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
                    volt2_content_raw += " " + next_text.strip()
                    wi += 1
            second_content = volt2_content_raw

            if first_chord:
                x_volt1 = x - 10.0
                if lyric_words:
                    for lw in lyric_words:
                        l_start, l_end = lw[0], lw[2]
                        if l_start <= x <= l_end:
                            ln = len(lw[4])
                            if ln > 0:
                                char_width = (l_end - l_start) / ln
                                x_volt1 = x - 2 * char_width
                            break
                events.append({"type": "chord", "x": x_volt1, "text": "[(1.]"})
                first_volt_parts = split_chord_word_by_chords(first_chord)
                for idx, part in enumerate(first_volt_parts):
                    if idx == len(first_volt_parts) - 1:
                        events.append({"type": "chord", "x": x, "text": f"[{part})]"})
                    else:
                        events.append({"type": "chord", "x": x, "text": f"[{part}]"})

            raw_tokens = re.split(r"(?<!/)(?=[A-H])", second_content)
            blocks = []
            current_block_content = "(2."
            for token in raw_tokens:
                if not token:
                    continue
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
            parts = split_chord_word_by_chords(raw_text)
            for part in parts:
                events.append({"type": "chord", "x": x, "text": f"[{part}]"})
            wi += 1

    events.sort(key=lambda e: e["x"])

    if not lyric_words:
        line_str = (" " * block_indent) + " ".join([e["text"] for e in events if e["type"] == "chord"])
        if delayed_chords:
            line_str += "".join(delayed_chords)
        return line_str

    combined_text = ""
    chord_queue = [e for e in events if e["type"] == "chord"]

    if is_leading and chord_queue:
        c = chord_queue.pop(0)
        combined_text += c["text"]
        combined_text += " " * block_indent
        if not combined_text.endswith(" "):
            combined_text += " "

    for w in lyric_words:
        w_start = w[0]
        w_end = w[2]
        w_text = w[4]

        while chord_queue and chord_queue[0]["x"] < w_start:
            c = chord_queue.pop(0)
            combined_text += c["text"]
            if not combined_text.endswith(" "):
                combined_text += " "

        inserts = []
        while chord_queue and chord_queue[0]["x"] >= w_start and chord_queue[0]["x"] <= w_end:
            c = chord_queue.pop(0)
            rel = (c["x"] - w_start) / (w_end - w_start)
            idx = int(round(len(w_text) * rel))
            inserts.append((idx, c["text"]))

        by_idx = {}
        for idx, txt in inserts:
            by_idx.setdefault(idx, []).append(txt)
        inserts = [(idx, "".join(texts)) for idx, texts in by_idx.items()]

        inserts.sort(key=lambda x: x[0], reverse=True)
        for idx, txt in inserts:
            w_text = w_text[:idx] + txt + w_text[idx:]

        combined_text += w_text + " "

    combined_text = combined_text.rstrip()
    while chord_queue:
        c = chord_queue.pop(0)
        combined_text += c["text"]

    if delayed_chords:
        combined_text = combined_text.rstrip()
        combined_text += "".join(delayed_chords)

    if is_leading:
        return combined_text.strip()
    return (" " * block_indent) + combined_text.strip()


def merge_using_chars(chord_line, lyric_line, label_to_strip="", block_indent=0):
    """Точное слияние на уровне символов (CHARS mode)."""
    lyric_chars = []
    if lyric_line and "chars" in lyric_line:
        lyric_chars = lyric_line["chars"]

    start_idx = 0
    for i, c in enumerate(lyric_chars):
        if c["char"].strip():
            start_idx = i
            break
    else:
        if lyric_chars:
            start_idx = len(lyric_chars)

    lyric_chars = lyric_chars[start_idx:]

    if label_to_strip:
        clean_label = label_to_strip.strip()
        matched_idx = -1
        temp_str = ""
        for i, c in enumerate(lyric_chars):
            if c["char"].strip() == "":
                continue
            temp_str += c["char"]
            if temp_str == clean_label or temp_str.startswith(clean_label):
                matched_idx = i
                break
            if len(temp_str) > len(clean_label) + 5:
                break

        if matched_idx >= 0:
            lyric_chars = lyric_chars[matched_idx + 1 :]
            while lyric_chars and not lyric_chars[0]["char"].strip():
                lyric_chars.pop(0)

    is_line_leading = False
    if block_indent > 0 and chord_line and lyric_chars:
        c_words = chord_line.get("words", [])
        if c_words:
            first_chord_x = c_words[0][0]
            first_chord_end_x = c_words[0][2]

            first_lyric_x = None
            first_letter_end_x = None
            for c in lyric_chars:
                if c["char"].strip():
                    first_lyric_x = c["x0"]
                    first_letter_end_x = c["x1"]
                    break

            if first_lyric_x is not None:
                if first_chord_x < first_lyric_x - 5.0 and first_chord_end_x < first_letter_end_x:
                    is_line_leading = True

    chord_words = [list(w) for w in chord_line["words"]] if chord_line else []
    for w in chord_words:
        w[4] = w[4].replace("//:", "||:").replace("://", ":||")

    chord_events = []
    delayed_chords = []

    wi = 0
    while wi < len(chord_words):
        w = chord_words[wi]
        raw_text = w[4]
        x = w[0]

        is_parenthesized_volt1 = False
        volt1_content = ""
        tokens_to_skip = 0

        if raw_text.startswith("(") and not raw_text.startswith("(:"):
            if ")" in raw_text:
                inner_parts = raw_text.split("(", 1)[1].split(")", 1)
                if inner_parts:
                    inner = inner_parts[0].strip()
                    if not inner.startswith(":"):
                        is_parenthesized_volt1 = True
                        volt1_content = inner
                        tokens_to_skip = 0
            else:
                collected_text = raw_text
                temp_skip = 0
                found_closing = False

                idx = 1
                while wi + idx < len(chord_words):
                    next_w = chord_words[wi + idx]
                    next_text = next_w[4]
                    collected_text += next_text
                    temp_skip += 1
                    if ")" in next_text:
                        found_closing = True
                        break
                    idx += 1

                if found_closing:
                    start_p = collected_text.find("(")
                    end_p = collected_text.find(")", start_p)

                    if start_p != -1 and end_p != -1:
                        inner_full = collected_text[start_p + 1 : end_p].strip()
                        if not inner_full.startswith(":"):
                            is_parenthesized_volt1 = True
                            volt1_content = inner_full
                            tokens_to_skip = temp_skip

        if is_parenthesized_volt1:
            volt1_parts = split_chord_word_by_chords(volt1_content)

            target_x_for_volt1 = x - 10.0
            if lyric_chars:
                closest_char_idx = -1
                min_dist = float("inf")
                for ci, c in enumerate(lyric_chars):
                    dist = abs(c["x0"] - x)
                    if dist < min_dist:
                        min_dist = dist
                        closest_char_idx = ci
                if closest_char_idx != -1:
                    target_idx = closest_char_idx - 2
                    if target_idx >= 0:
                        target_x_for_volt1 = lyric_chars[target_idx]["x0"]
                    elif target_idx == -1:
                        if lyric_chars:
                            w_char = lyric_chars[0]["x1"] - lyric_chars[0]["x0"]
                            target_x_for_volt1 = lyric_chars[0]["x0"] - w_char
                    else:
                        if lyric_chars:
                            w_char = lyric_chars[0]["x1"] - lyric_chars[0]["x0"]
                            target_x_for_volt1 = lyric_chars[0]["x0"] - (2 * w_char)

            chord_events.append({"x": target_x_for_volt1, "text": "[(1.]"})
            for idx, part in enumerate(volt1_parts):
                if idx == len(volt1_parts) - 1:
                    chord_events.append({"x": x, "text": f"[{part})]"})
                else:
                    chord_events.append({"x": x, "text": f"[{part}]"})
            wi += 1 + tokens_to_skip
            continue

        if "(:" in raw_text:
            parts = raw_text.split("(:", 1)
            pre_text = parts[0].strip()
            remainder = parts[1]

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
                    volt2_content_raw += " " + next_text.strip()
                    wi += 1
            second_content = volt2_content_raw

            if pre_text:
                target_x_for_volt1 = x - 10.0
                if lyric_chars:
                    closest_char_idx = -1
                    min_dist = float("inf")
                    for ci, c in enumerate(lyric_chars):
                        dist = abs(c["x0"] - x)
                        if dist < min_dist:
                            min_dist = dist
                            closest_char_idx = ci
                    if closest_char_idx != -1:
                        target_idx = closest_char_idx - 2
                        if target_idx >= 0:
                            target_x_for_volt1 = lyric_chars[target_idx]["x0"]
                        elif target_idx == -1:
                            if lyric_chars:
                                w_char = lyric_chars[0]["x1"] - lyric_chars[0]["x0"]
                                target_x_for_volt1 = lyric_chars[0]["x0"] - w_char
                        else:
                            if lyric_chars:
                                w_char = lyric_chars[0]["x1"] - lyric_chars[0]["x0"]
                                target_x_for_volt1 = lyric_chars[0]["x0"] - (2 * w_char)

                chord_events.append({"x": target_x_for_volt1, "text": "[(1.]"})
                first_volt_parts = split_chord_word_by_chords(pre_text)
                for idx, part in enumerate(first_volt_parts):
                    if idx == len(first_volt_parts) - 1:
                        chord_events.append({"x": x, "text": f"[{part})]"})
                    else:
                        chord_events.append({"x": x, "text": f"[{part}]"})

            raw_tokens = re.split(r"(?<!/)(?=[A-H])", second_content)
            blocks = []
            current_block_content = "(2."
            for token in raw_tokens:
                if not token:
                    continue
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

        parts = split_chord_word_by_chords(raw_text)
        for part in parts:
            chord_events.append({"x": x, "text": f"[{part}]"})
        wi += 1

    chord_events.sort(key=lambda e: e["x"])

    if not lyric_chars:
        result_str = ""
        for ch in chord_events:
            result_str += ch["text"]
        final_res = (" " * block_indent) + result_str
        if delayed_chords:
            final_res += "".join(delayed_chords)
        return final_res.replace("//:", "||:").replace("://", ":||")

    result_str = ""
    processed_chords = [False] * len(chord_events)
    indent_inserted = False

    for i, c in enumerate(lyric_chars):
        c_center = (c["x0"] + c["x1"]) / 2
        c_start = c["x0"]

        for ch_i, chord in enumerate(chord_events):
            if processed_chords[ch_i]:
                continue

            should_insert = False
            if chord["x"] < c_start:
                should_insert = True
            elif chord["x"] >= c_start and chord["x"] < c["x1"]:
                if chord["x"] < c_center:
                    should_insert = True

            if should_insert:
                result_str += chord["text"]
                processed_chords[ch_i] = True

        if is_line_leading and not indent_inserted:
            result_str += " " * block_indent
            indent_inserted = True

        if i > 0:
            prev_c = lyric_chars[i - 1]
            dist = c_start - prev_c["x1"]
            if dist > 2.0 and c["char"] != " " and prev_c["char"] != " ":
                result_str += " "

        result_str += c["char"]

    result_str = result_str.rstrip()
    for ch_i, chord in enumerate(chord_events):
        if not processed_chords[ch_i]:
            result_str += chord["text"]

    if delayed_chords:
        result_str = result_str.rstrip()
        result_str += "".join(delayed_chords)

    if not indent_inserted and block_indent > 0:
        result_str = (" " * block_indent) + result_str

    return result_str.rstrip().replace("//:", "||:").replace("://", ":||")
