import os
import sys
from pathlib import Path
from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / '.env')
STATE = Path(os.getenv('STATE_DIR', str(BASE / 'state'))).expanduser().resolve()
DATABASE = Path(os.getenv('DATABASE_PATH', str(STATE / 'study.sqlite3'))).expanduser().resolve()
# Legacy interpreter setting is accepted; this process only prepares playback audio.
AUDIO_PYTHON = os.getenv('AUDIO_PYTHON', os.getenv('MODEL_PYTHON', sys.executable))
AUDIO_FOLDER = Path(os.getenv('AUDIO_FOLDER', str(BASE / 'audio'))).expanduser().resolve()
ORIGINS = [s.strip().rstrip('/') for s in os.getenv('FRONTEND_ORIGINS', 'http://localhost:5500,http://127.0.0.1:5500').split(',') if s.strip()]
ADMIN_TOKEN = os.getenv('ADMIN_TOKEN','')
