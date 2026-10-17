"""Use isolated test audio and databases; never invoke a music generator."""
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid
import wave
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
_import_state = tempfile.TemporaryDirectory()
os.environ['STATE_DIR'] = _import_state.name
os.environ['DATABASE_PATH'] = str(Path(_import_state.name) / 'import.sqlite3')
import app as web
from fastapi.testclient import TestClient
from store import Store, TEMPOS


def wav_bytes(value):
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(struct.pack('<hh', value, -value) * 240000)
    return output.getvalue()


class RecordedAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        web.cfg.STATE = root / 'state'
        web.cfg.STATE.mkdir()
        web.cfg.DATABASE = root / 'study.sqlite3'
        web.cfg.AUDIO_FOLDER = root / 'recordings'
        web.cfg.AUDIO_FOLDER.mkdir()
        web.cfg.AUDIO_PYTHON = sys.executable
        web.cfg.ADMIN_TOKEN = 'test-only-token-' * 4
        web.store = Store(web.cfg.DATABASE)
        web.pool = ThreadPoolExecutor(max_workers=1)
        self.addCleanup(web.pool.shutdown, wait=True)
        self.clips = []
        for i, bpm in enumerate(TEMPOS):
            data = wav_bytes(100 + i)
            path = web.cfg.STATE / ('clip-%s.wav' % bpm)
            path.write_bytes(data)
            self.clips.append(dict(path=str(path), sha256=hashlib.sha256(data).hexdigest(), label_bpm=bpm, tempo_source='folder'))

    def index(self, command, **kwargs):
        self.assertEqual(Path(command[1]).name, 'audio_catalog.py')
        self.assertNotIn('generate', command)
        self.assertNotIn('--evaluator-repo', command)
        Path(command[command.index('--output') + 1]).write_text(json.dumps(self.clips))

    def join(self, client, name='listener'):
        r = client.post('/api/start', json={'username': name})
        self.assertEqual(r.status_code, 200)
        return {'Authorization': 'Bearer ' + r.json()['token']}

    def trial(self, client, headers):
        r = client.post('/api/trials', headers=headers, json={'request_id': str(uuid.uuid4())})
        self.assertEqual(r.status_code, 200)
        trial = r.json()
        if trial.get('complete'):
            return trial
        for _ in range(100):
            trial = client.get('/api/trials/' + trial['id'], headers=headers).json()
            if trial['status'] in ('ready', 'failed'):
                return trial
            time.sleep(.01)
        self.fail('Trial did not finish')

    def test_nine_recordings_no_generation_retries_export_and_exhaustion(self):
        with patch.object(web.subprocess, 'run', side_effect=self.index) as process:
            with TestClient(web.app) as client:
                self.assertEqual(client.get('/api/health').json()['audio_mode'], 'recorded')
                self.assertTrue(client.get('/api/health').json()['ready'])
                headers = self.join(client)
                targets, hashes = set(), set()
                for _ in TEMPOS:
                    trial = self.trial(client, headers)
                    self.assertEqual(trial['status'], 'ready')
                    self.assertNotIn('target_bpm', trial)
                    stored = web.store.get(trial['id'])
                    self.assertEqual(stored['source'], 'recorded-folder')
                    self.assertIsNone(stored['measured_bpm'])
                    self.assertEqual(stored['label_bpm'], stored['target_bpm'])
                    targets.add(stored['target_bpm'])
                    hashes.add(stored['audio_hash'])
                    retry = client.post('/api/trials', headers=headers, json={'request_id': str(uuid.uuid4())}).json()
                    self.assertEqual(retry['id'], trial['id'])
                    response = client.get('/api/audio/' + trial['id'], headers=headers)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(hashlib.sha256(response.content).hexdigest(), stored['audio_hash'])
                    for _ in range(2):
                        rated = client.post('/api/ratings', headers=headers, json={'request_id': trial['id'], 'rating': 4})
                        self.assertEqual(rated.status_code, 200)
                self.assertEqual(targets, set(TEMPOS))
                self.assertEqual(len(hashes), 9)
                self.assertTrue(self.trial(client, headers)['complete'])
                progress = client.get('/api/progress', headers=self.join(client, 'LISTENER')).json()
                self.assertEqual(progress['rated'], 9)
                self.assertTrue(progress['complete'])
                export = client.get('/api/admin/responses.csv', headers={'Authorization': 'Bearer ' + web.cfg.ADMIN_TOKEN})
                rows = list(csv.reader(io.StringIO(export.text)))
                self.assertEqual(rows[0], ['username', 'rating', 'tempo'])
                self.assertEqual(len(rows), 10)
                self.assertEqual({float(row[2]) for row in rows[1:]}, set(TEMPOS))
                self.assertEqual(client.get('/api/admin/responses.csv').status_code, 401)
                self.assertEqual(process.call_count, 1, 'Only startup indexing may invoke another Python process')

    def test_missing_tempo_blocks_trials_before_consuming_tempos(self):
        self.clips.pop()
        with patch.object(web.subprocess, 'run', side_effect=self.index):
            with TestClient(web.app) as client:
                self.assertFalse(client.get('/api/health').json()['ready'])
                headers = self.join(client)
                r = client.post('/api/trials', headers=headers, json={'request_id': str(uuid.uuid4())})
                self.assertEqual(r.status_code, 503)
                self.assertEqual(client.get('/api/progress', headers=headers).json()['attempted'], 0)

    def test_corrupt_cache_fails_without_generation_or_tempo_reuse(self):
        with patch.object(web.subprocess, 'run', side_effect=self.index) as process:
            with TestClient(web.app) as client:
                for clip in self.clips:
                    Path(clip['path']).write_bytes(b'corrupt')
                headers = self.join(client)
                a, b = self.trial(client, headers), self.trial(client, headers)
                self.assertEqual(a['status'], 'failed')
                self.assertEqual(b['status'], 'failed')
                self.assertNotEqual(web.store.get(a['id'])['target_bpm'], web.store.get(b['id'])['target_bpm'])
                self.assertEqual(process.call_count, 1)

    def test_recordings_reusable_between_participants_but_private_requests(self):
        with patch.object(web.subprocess, 'run', side_effect=self.index):
            with TestClient(web.app) as client:
                a, b = self.join(client, 'person-a'), self.join(client, 'person-b')
                with patch('store.secrets.choice', side_effect=lambda values: values[0]):
                    ta, tb = self.trial(client, a), self.trial(client, b)
                self.assertEqual(web.store.get(ta['id'])['audio_hash'], web.store.get(tb['id'])['audio_hash'])
                self.assertEqual(client.get('/api/audio/' + ta['id'], headers=b).status_code, 409)
                self.assertEqual(client.post('/api/trials', headers=a, json={'request_id': str(uuid.uuid4()), 'bpm': 90}).status_code, 422)

    def test_real_folder_indexing_and_playback_without_evaluator(self):
        import numpy as np
        import soundfile as sf
        for index, bpm in enumerate(TEMPOS):
            folder = web.cfg.AUDIO_FOLDER / str(bpm)
            folder.mkdir()
            # Test tones have folder labels but no musical beat to estimate.
            audio = .1 * np.sin(2 * np.pi * (220 + index * 11) * np.arange(240000) / 8000)
            sf.write(folder / 'recording.wav', audio, 8000)
        with TestClient(web.app) as client:
            health = client.get('/api/health').json()
            self.assertTrue(health['ready'])
            self.assertEqual(health['tempo_source'], 'folder')
            headers = self.join(client)
            for bpm in TEMPOS:
                with patch('store.secrets.choice', return_value=bpm):
                    trial = self.trial(client, headers)
                stored = web.store.get(trial['id'])
                self.assertEqual(stored['label_bpm'], bpm)
                self.assertIsNone(stored['measured_bpm'])
                self.assertEqual(client.get('/api/audio/' + trial['id'], headers=headers).status_code, 200)
                response = client.post('/api/ratings', headers=headers, json={'request_id': trial['id'], 'rating': 3})
                self.assertEqual(response.json()['saved']['tempo'], bpm)


if __name__ == '__main__':
    unittest.main()
