"""Existing nine-tempo studies can continue with six without losing ratings."""
import csv
import io
from pathlib import Path
import sys
import tempfile
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from store import Store, TEMPOS, Finished


class ActiveTempoTests(unittest.TestCase):
    def test_history_progress_retired_trial_and_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'study.sqlite3'
            store = Store(db)
            token, key = store.start('existing-listener')
            with store.connect() as c:
                for bpm, status in ((60, 'rated'), (150, 'rated'), (165, 'ready')):
                    c.execute("INSERT INTO trials (id,username_key,target_bpm,seed,status,source,label_bpm,created_at) VALUES (?,?,?,?,?,'recorded-folder',?,0)",
                              (str(bpm), key, bpm, bpm, status, bpm))
                    if status == 'rated':
                        c.execute('INSERT INTO responses VALUES (?,?,?,?)', (str(bpm), 'existing-listener', 4, bpm))
            before = store.export_csv()
            store = Store(db)
            self.assertEqual(store.identify(token), key)
            self.assertEqual(store.export_csv(), before)
            self.assertEqual(store.get('165')['status'], 'failed')
            self.assertEqual(store.get('150')['status'], 'rated')
            p = store.progress(key)
            self.assertEqual((p['rated'], p['attempted'], p['remaining'], p['total'], p['current_id']), (1, 1, 5, 6, None))
            # A stale request is retired, so it cannot reopen the excluded clip.
            stale, created = store.reserve(key, '165')
            self.assertFalse(created)
            self.assertEqual(stale['status'], 'failed')
            assigned = []
            for _ in range(5):
                trial, created = store.reserve(key, str(uuid.uuid4()))
                self.assertTrue(created)
                assigned.append(trial['target_bpm'])
                self.assertTrue(store.accept_labeled(trial['id'], trial['target_bpm'], trial['id'], 'test.wav'))
                store.rate(key, trial['id'], 3)
            self.assertEqual(set(assigned), set(TEMPOS) - {60})
            self.assertTrue(store.progress(key)['complete'])
            self.assertEqual(store.progress(key)['rated'], 6)
            with self.assertRaises(Finished):
                store.reserve(key, str(uuid.uuid4()))
            rows = list(csv.DictReader(io.StringIO(store.export_csv())))
            self.assertEqual(len(rows), 7)
            self.assertIn(150, {float(r['tempo']) for r in rows})
            self.assertEqual(Store(db).export_csv(), store.export_csv())


if __name__ == '__main__':
    unittest.main()
