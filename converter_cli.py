"""
CLI-обвязка для запуска PDF -> ChordPro конвертера.
"""

import argparse


def parse_args():
    """Парсит аргументы командной строки."""
    parser = argparse.ArgumentParser(description="Convert PDF to ChordPro")
    parser.add_argument(
        "-w",
        "--words-mode",
        action="store_true",
        help="Use legacy word-level parsing (no space detection)",
    )
    parser.add_argument(
        "-db",
        "--write-db",
        action="store_true",
        help="Записывать chordpro в БД (поле song.chordpro только если NULL). Без флага — только .cho файлы.",
    )
    parser.add_argument(
        "-rbc",
        "--rbc",
        action="store_true",
        help="Включить режим обработки RBC-оформления (заголовки секций и нотация).",
    )
    return parser.parse_args()
