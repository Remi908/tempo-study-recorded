"""Create a consistent SQLite backup, including committed WAL transactions."""
import sqlite3
import sys
from pathlib import Path
import settings
if len(sys.argv)!=2:
    raise SystemExit('Usage: python backup_database.py /private/path/study-backup.sqlite3')
target=Path(sys.argv[1]).resolve()
if target.exists():
    raise SystemExit('Choose a new backup path; no existing file was overwritten.')
target.parent.mkdir(parents=True,exist_ok=True)
with sqlite3.connect(str(settings.DATABASE)) as source:
    with sqlite3.connect(str(target)) as destination:
        source.backup(destination)
print('Saved '+str(target))
