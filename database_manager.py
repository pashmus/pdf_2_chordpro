import sys
import os
from pathlib import Path
import psycopg2
from psycopg2.extras import RealDictCursor
import importlib.util

# Настройка путей
current_dir = Path(__file__).resolve().parent
# .../!pdf_2_chordpro_V2 -> .../CHORD_PRO_PROJECT -> .../Sbornik_samara_bot
project_root = current_dir.parent.parent

# Вставляем корень проекта в начало sys.path
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

print(f"Project root added to path: {project_root}")

load_config = None

# Попытка 1: Стандартный импорт
try:
    from config_data.config import load_config
    print("Successfully imported config using standard import.")
except ImportError as e:
    print(f"Standard import failed: {e}")
    
    # Попытка 2: Прямая загрузка файла через spec
    try:
        config_path = project_root / "config_data" / "config.py"
        print(f"Attempting direct load from: {config_path}")
        
        if config_path.exists():
            spec = importlib.util.spec_from_file_location("config_data.config", config_path)
            config_module = importlib.util.module_from_spec(spec)
            sys.modules["config_data.config"] = config_module
            spec.loader.exec_module(config_module)
            load_config = config_module.load_config
            print("Successfully imported config using importlib.")
        else:
            print(f"Config file not found at {config_path}")
    except Exception as e2:
        print(f"Direct load failed: {e2}")

class DatabaseManager:
    def __init__(self):
        if load_config:
            try:
                self.config = load_config()
                self.db_config = {
                    'host': self.config.db.db_host,
                    'database': self.config.db.db_name,
                    'user': self.config.db.db_user,
                    'password': self.config.db.db_password
                }
            except Exception as e:
                 print(f"Error loading config values: {e}")
                 self.db_config = {}
        else:
            print("Warning: load_config is not available. DB connection will fail.")
            self.db_config = {}
        self.conn = None

    def connect(self):
        if not self.db_config:
            print("DB Config is empty.")
            return False
            
        try:
            self.conn = psycopg2.connect(**self.db_config)
            return True
        except Exception as e:
            print(f"Database connection error: {e}")
            return False

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None

    def get_song_metadata(self, song_number: int):
        """
        Возвращает словарь {'title': str, 'tempo': int, 'time': str} или None
        """
        if not self.conn:
            if not self.connect():
                return None
        
        try:
            with self.conn.cursor(cursor_factory=RealDictCursor) as cursor:
                query = """
                    SELECT 
                        CASE WHEN alt_name IS NULL THEN name ELSE alt_name END as title,
                        tempo,
                        time_signature as time
                    FROM songs 
                    WHERE num = %s
                """
                cursor.execute(query, (song_number,))
                result = cursor.fetchone()
                return result
        except Exception as e:
            print(f"Error fetching metadata for song {song_number}: {e}")
            self.close()
            return None
