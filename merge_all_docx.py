import os
import glob
from docx import Document
from docxcompose.composer import Composer

# Попытка импорта win32com для конвертации .doc
try:
    import win32com.client as win32
    WIN32_AVAILABLE = True
except ImportError:
    WIN32_AVAILABLE = False
    print("ВНИМАНИЕ: Библиотека pywin32 не найдена. Файлы .doc не будут обработаны.")
    print("Установите её командой: pip install pywin32")

def convert_doc_to_docx(folder_path):
    """Ищет .doc файлы и конвертирует их в .docx в той же папке навсегда"""
    doc_files = glob.glob(os.path.join(folder_path, "*.doc"))
    
    if not doc_files:
        return
        
    if not WIN32_AVAILABLE:
        print(f"Найдено {len(doc_files)} файлов .doc, но нет pywin32 для конвертации.")
        return

    # Проверяем, какие файлы действительно нужно конвертировать
    files_to_convert = []
    for doc_file in doc_files:
        docx_path = os.path.splitext(doc_file)[0] + ".docx"
        if not os.path.exists(docx_path):
            files_to_convert.append(doc_file)

    if not files_to_convert:
        # print("Все .doc файлы уже имеют .docx версии.")
        return

    print(f"Найдено {len(files_to_convert)} новых файлов .doc. Конвертируем в .docx...")
    
    word = None
    try:
        word = win32.Dispatch("Word.Application")
        word.Visible = False
        word.DisplayAlerts = False 
        
        for i, doc_file in enumerate(files_to_convert, 1):
            filename = os.path.basename(doc_file)
            new_path = os.path.splitext(doc_file)[0] + ".docx"
            
            abs_doc_path = os.path.abspath(doc_file)
            abs_new_path = os.path.abspath(new_path)
            
            print(f"[{i}/{len(files_to_convert)}] Конвертация: {filename}")
            
            try:
                doc = word.Documents.Open(abs_doc_path)
                doc.SaveAs2(abs_new_path, FileFormat=16) # 16 = wdFormatXMLDocument
                doc.Close()
            except Exception as e:
                print(f"  Ошибка конвертации {filename}: {e}")
                
    except Exception as e:
        print(f"Ошибка при работе с Word: {e}")
    finally:
        if word:
            try:
                word.Quit()
            except:
                pass

def merge_docx_files():
    # Название папки с песнями
    folder_name = "Все песни docx (для объединения)"
    # Имя итогового файла
    output_filename = "!Сборник_всех_песен.docx"

    # Пути
    base_dir = os.path.dirname(os.path.abspath(__file__))
    source_dir = os.path.join(base_dir, folder_name)
    output_path = os.path.join(source_dir, output_filename)

    if not os.path.exists(source_dir):
        print(f"Ошибка: Папка '{folder_name}' не найдена в {base_dir}")
        return

    # 1. Сначала конвертируем все .doc в .docx (сохраняя навсегда)
    convert_doc_to_docx(source_dir)

    # 2. Теперь ищем только .docx файлы
    files = glob.glob(os.path.join(source_dir, "*.docx"))
    
    # Фильтруем список
    files_to_merge = []
    for f in files:
        filename = os.path.basename(f)
        # Исключаем временные файлы (~$), сам сборник (!)
        if not filename.startswith('~$') and \
           not filename.startswith('!') and \
           filename != output_filename:
            files_to_merge.append(f)

    # Сортируем файлы по алфавиту
    files_to_merge.sort(key=lambda x: os.path.basename(x))

    if not files_to_merge:
        print("Нет .docx файлов для объединения.")
        return

    print(f"\nНайдено файлов для объединения: {len(files_to_merge)}")

    # 3. Объединение
    try:
        # Берем первый файл за основу
        first_file = files_to_merge[0]
        print(f"Основа: {os.path.basename(first_file)}")
        
        master_doc = Document(first_file)
        composer = Composer(master_doc)

        for i, filepath in enumerate(files_to_merge[1:], start=1):
            filename = os.path.basename(filepath)
            print(f"[{i}/{len(files_to_merge)-1}] Добавление: {filename}")
            
            try:
                master_doc.add_page_break()
                doc_to_append = Document(filepath)
                composer.append(doc_to_append)
            except Exception as e:
                print(f"Ошибка при добавлении файла {filename}: {e}")

        composer.save(output_path)
        print("-" * 30)
        print(f"Готово! Объединенный файл создан:\n{output_path}")

    except Exception as e:
        print(f"Критическая ошибка при объединении: {e}")

if __name__ == "__main__":
    merge_docx_files()
