# PDF to ChordPro Converter

Проект конвертирует PDF-файлы песен в формат ChordPro (`.cho`) и при необходимости записывает результат в БД.

## Быстрый старт

1. Положите исходные PDF в папку `input_pdf` (или измените путь в `converter_PDF_to_CHORDPRO.py`).
2. Запустите:
   - `python converter_PDF_to_CHORDPRO.py`
3. Результат будет сохранён в `output_cho`.

## Флаги запуска

- `-w`, `--words-mode` — legacy-режим парсинга на уровне слов (меньше точности, но полезно для сравнения).
- `-db`, `--write-db` — запись `chordpro` в БД только для песен, где поле `song.chordpro` равно `NULL`.

Примеры:

- `python converter_PDF_to_CHORDPRO.py -w`
- `python converter_PDF_to_CHORDPRO.py -db`
- `python converter_PDF_to_CHORDPRO.py -w -db`

## Структура после рефакторинга

- `converter_PDF_to_CHORDPRO.py` — основной конвертер и точка входа.
- `converter_utils.py` — константы и вспомогательные функции парсинга/нормализации.
- `converter_processing.py` — обработка секций и слияние аккордов/текста.
- `converter_cli.py` — разбор CLI-флагов.
- `database_manager.py` — работа с PostgreSQL.
- `key_analyser.py` — анализ тональности по аккордам.
- `tools_scripts/` — отладочные скрипты и их вспомогательные отчёты.

## Где менять директории

В начале файла `converter_PDF_to_CHORDPRO.py`:

- `input_dir = "input_pdf"`
- `output_dir = "output_cho"`

## Отчёты

Во время обработки конвертер пишет служебные отчёты в корень проекта:

- `parsing_report.txt`
- `rule14_report.txt`

## Отладочные скрипты

Папка `tools_scripts` содержит утилиты для диагностики:

- `debug_widths.py` — анализ ширины символов/интервалов.
- `debug_vertical_compare.py` — сравнение сегментации строк WORDS vs CHARS.
- `debug_coords.py` — анализ вертикальных координат строк.
- `debug_pdf.py` — просмотр структуры страницы по Y-координатам.
- `debug_split.py` — проверка токенизации аккордов в сложных строках.
- `test_key_from_chords.py` — локальный тест определения тональности по списку аккордов.
- `merge_all_docx.py` — конвертация `.doc` в `.docx` и объединение docx-песен в один файл.

### Пути в tools_scripts

Все скрипты в `tools_scripts` привязаны к путям через:

- `SCRIPT_DIR` — папка самого скрипта;
- `PROJECT_ROOT` — корень проекта.

Это позволяет запускать скрипты из любой текущей директории без ручного `cd`.

### Файлы отчётов в tools_scripts

Отладочные скрипты сохраняют свои отчёты в `tools_scripts`:

- `debug_widths.txt`
- `debug_vertical_compare_report.txt`
