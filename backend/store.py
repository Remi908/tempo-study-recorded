"""Persistent study rules. Standard library only; independent of the model/API."""
import csv
import hashlib
import io
import math
import re
import secrets
import sqlite3
import time
import unicodedata
from contextlib import contextmanager
from pathlib import Path

TEMPOS = (60, 75, 90, 105, 120, 135, 150, 165, 180)
TOLERANCE = 5

class StudyError(Exception):
    pass

class Finished(StudyError):
    pass


def normalize_username(value):
    name = unicodedata.normalize('NFKC', str(value)).strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]{2,32}', name):
        raise StudyError('Use 2–32 letters, numbers, underscores or hyphens.')
    return name, name.lower()


def within_tolerance(measured, target):
    return isinstance(measured, (int, float)) and not isinstance(measured, bool) and math.isfinite(measured) and measured > 0 and abs(measured - target) <= TOLERANCE


class Store:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.execute('PRAGMA journal_mode=WAL')
            c.executescript('''
CREATE TABLE IF NOT EXISTS participants (username_key TEXT PRIMARY KEY, username TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, username_key TEXT NOT NULL REFERENCES participants(username_key));
CREATE TABLE IF NOT EXISTS trials (
 id TEXT PRIMARY KEY, username_key TEXT NOT NULL REFERENCES participants(username_key),
 target_bpm INTEGER NOT NULL CHECK(target_bpm IN (60,75,90,105,120,135,150,165,180)),
 seed INTEGER NOT NULL UNIQUE, status TEXT NOT NULL CHECK(status IN ('queued','generating','ready','rated','failed')),
 measured_bpm REAL, audio_hash TEXT, audio_path TEXT, source TEXT, error TEXT, created_at INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_trial ON trials(username_key) WHERE status IN ('queued','generating','ready');
CREATE TABLE IF NOT EXISTS tempo_slots (
 username_key TEXT NOT NULL REFERENCES participants(username_key),
 target_bpm INTEGER NOT NULL, trial_id TEXT NOT NULL UNIQUE REFERENCES trials(id),
 PRIMARY KEY(username_key,target_bpm)
);
CREATE TABLE IF NOT EXISTS heard_audio (
 username_key TEXT NOT NULL, audio_hash TEXT NOT NULL, trial_id TEXT NOT NULL UNIQUE REFERENCES trials(id),
 PRIMARY KEY(username_key,audio_hash)
);
CREATE TABLE IF NOT EXISTS responses (
 trial_id TEXT PRIMARY KEY REFERENCES trials(id), username TEXT NOT NULL,
 rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5), tempo REAL NOT NULL CHECK(tempo>0)
);
''')
            # Add a separate label field; historical measured BPM and responses stay intact.
            columns = {row['name'] for row in c.execute('PRAGMA table_info(trials)')}
            if 'label_bpm' not in columns:
                c.execute('ALTER TABLE trials ADD COLUMN label_bpm INTEGER')

    @contextmanager
    def connect(self):
        c = sqlite3.connect(str(self.path), timeout=30)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    def start(self, username):
        name, key = normalize_username(username)
        token = secrets.token_urlsafe(32)
        with self.connect() as c:
            c.execute('INSERT OR IGNORE INTO participants VALUES (?,?)', (key, name))
            c.execute('INSERT INTO sessions VALUES (?,?)', (hashlib.sha256(token.encode()).hexdigest(), key))
        return token, key

    def identify(self, token):
        with self.connect() as c:
            r = c.execute('SELECT username_key FROM sessions WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        if not r:
            raise StudyError('Your session was not found. Enter your username again.')
        return r['username_key']

    def progress(self, key):
        with self.connect() as c:
            c.execute('BEGIN')  # one consistent view while another tab may reserve
            name = c.execute('SELECT username FROM participants WHERE username_key=?', (key,)).fetchone()['username']
            delivered = c.execute("SELECT count(*) FROM trials WHERE username_key=? AND status IN ('ready','rated')", (key,)).fetchone()[0]
            rated = c.execute('SELECT count(*) FROM responses r JOIN trials t ON t.id=r.trial_id WHERE t.username_key=?', (key,)).fetchone()[0]
            current = c.execute("SELECT id FROM trials WHERE username_key=? AND status IN ('queued','generating','ready')", (key,)).fetchone()
            attempted = c.execute('SELECT count(DISTINCT target_bpm) FROM trials WHERE username_key=?', (key,)).fetchone()[0]
        return dict(username=name, delivered=delivered, rated=rated, attempted=attempted,
                    remaining=len(TEMPOS)-attempted, total=len(TEMPOS),
                    complete=attempted==len(TEMPOS) and current is None,
                    current_id=current['id'] if current else None)

    def reserve(self, key, request_id):
        """Choose uniformly from unused tempos inside one serialized transaction."""
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            existing = c.execute('SELECT * FROM trials WHERE id=?', (request_id,)).fetchone()
            if existing:
                if existing['username_key'] != key:
                    raise StudyError('Request ID belongs to another session.')
                return dict(existing), False
            current = c.execute("SELECT * FROM trials WHERE username_key=? AND status IN ('queued','generating','ready')", (key,)).fetchone()
            if current:
                return dict(current), False  # resume, never create another clip at that tempo
            # Include failed attempts, including those made by older versions
            # that deleted their tempo_slots rows. No database reset is needed.
            used = {row[0] for row in c.execute('SELECT DISTINCT target_bpm FROM trials WHERE username_key=?', (key,))}
            remaining = [bpm for bpm in TEMPOS if bpm not in used]
            if not remaining:
                raise Finished('All nine tempos have been attempted. Your session is finished. Thank you!')
            if c.execute("SELECT count(*) FROM trials WHERE status IN ('queued','generating')").fetchone()[0] >= 16:
                raise StudyError('The audio request queue is full. Please try again shortly.')
            bpm = secrets.choice(remaining)
            seed = secrets.randbelow(2**31-1)
            while c.execute('SELECT 1 FROM trials WHERE seed=?', (seed,)).fetchone():
                seed = secrets.randbelow(2**31-1)
            c.execute("INSERT INTO trials (id,username_key,target_bpm,seed,status,created_at) VALUES (?,?,?,?,'queued',?)", (request_id,key,bpm,seed,int(time.time())))
            c.execute('INSERT INTO tempo_slots VALUES (?,?,?)', (key,bpm,request_id))
            return dict(c.execute('SELECT * FROM trials WHERE id=?', (request_id,)).fetchone()), True

    def get(self, request_id, key=None):
        with self.connect() as c:
            row = c.execute('SELECT * FROM trials WHERE id=?', (request_id,)).fetchone()
        if not row or key is not None and row['username_key'] != key:
            raise StudyError('Audio request not found.')
        return dict(row)

    def generating(self, request_id):
        with self.connect() as c:
            c.execute("UPDATE trials SET status='generating' WHERE id=? AND status='queued'", (request_id,))

    def accept(self, request_id, measured_bpm, audio_hash, audio_path, source):
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            trial = c.execute('SELECT * FROM trials WHERE id=?', (request_id,)).fetchone()
            if not trial or trial['status'] not in ('queued','generating') or not within_tolerance(measured_bpm,trial['target_bpm']):
                return False
            if c.execute('SELECT 1 FROM heard_audio WHERE username_key=? AND audio_hash=?', (trial['username_key'],audio_hash)).fetchone():
                return False
            c.execute('INSERT INTO heard_audio VALUES (?,?,?)', (trial['username_key'],audio_hash,request_id))
            c.execute("UPDATE trials SET status='ready', measured_bpm=?,audio_hash=?,audio_path=?,source=? WHERE id=?", (measured_bpm,audio_hash,str(audio_path),source,request_id))
            return True

    def accept_labeled(self, request_id, label_bpm, audio_hash, audio_path):
        """Accept a researcher folder label without pretending it was measured."""
        if type(label_bpm) is not int or label_bpm not in TEMPOS:
            return False
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            trial = c.execute('SELECT * FROM trials WHERE id=?', (request_id,)).fetchone()
            if not trial or trial['status'] not in ('queued','generating') or label_bpm != trial['target_bpm']:
                return False
            if c.execute('SELECT 1 FROM heard_audio WHERE username_key=? AND audio_hash=?', (trial['username_key'],audio_hash)).fetchone():
                return False
            c.execute('INSERT INTO heard_audio VALUES (?,?,?)', (trial['username_key'],audio_hash,request_id))
            c.execute("UPDATE trials SET status='ready', measured_bpm=NULL,label_bpm=?,audio_hash=?,audio_path=?,source='recorded-folder' WHERE id=?",
                      (label_bpm,audio_hash,str(audio_path),request_id))
            return True

    def fail(self, request_id, message):
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute("UPDATE trials SET status='failed',error=? WHERE id=? AND status IN ('queued','generating')", (message,request_id))
            # Every assigned tempo stays used, even when no audio was available.
            # Transport retries still resume the same trial instead of allocating.

    def recover(self):
        with self.connect() as c:
            ids = [r[0] for r in c.execute("SELECT id FROM trials WHERE status IN ('queued','generating')")]
        for request_id in ids:
            self.fail(request_id,'The audio service restarted before playback was ready. This tempo remains used. Continue with a different tempo if any remain.')

    def rate(self, key, request_id, rating):
        if type(rating) is not int or not 1 <= rating <= 5:
            raise StudyError('Choose a rating from 1 to 5.')
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            r = c.execute('SELECT t.*,p.username FROM trials t JOIN participants p ON p.username_key=t.username_key WHERE t.id=? AND t.username_key=?', (request_id,key)).fetchone()
            if not r or r['status'] not in ('ready','rated'):
                raise StudyError('Listen to a prepared clip before rating.')
            tempo = r['label_bpm'] if r['source'] == 'recorded-folder' else r['measured_bpm']
            c.execute('INSERT OR IGNORE INTO responses VALUES (?,?,?,?)', (request_id,r['username'],rating,tempo))
            c.execute("UPDATE trials SET status='rated' WHERE id=?", (request_id,))
            return dict(c.execute('SELECT username,rating,tempo FROM responses WHERE trial_id=?', (request_id,)).fetchone())

    def export_csv(self):
        with self.connect() as c:
            rows = c.execute('SELECT username,rating,tempo FROM responses ORDER BY rowid').fetchall()
        out = io.StringIO(newline='')
        writer = csv.writer(out)
        writer.writerow(('username','rating','tempo'))
        for name,rating,tempo in rows:
            writer.writerow(("'"+name if name.startswith(('=','+','-','@')) else name,rating,tempo))
        return out.getvalue()
