"""Old measured records stay unchanged; new folder labels are stored separately."""
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from store import Store


class LabelMigrationTests(unittest.TestCase):
    def test_upgrade_preserves_old_responses_and_adds_label_without_measurement(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'study.sqlite3'
            # The original trials schema, before label_bpm existed.
            with sqlite3.connect(db) as c:
                c.execute('CREATE TABLE participants (username_key TEXT PRIMARY KEY, username TEXT NOT NULL)')
                c.execute('''CREATE TABLE trials (
                    id TEXT PRIMARY KEY, username_key TEXT NOT NULL REFERENCES participants(username_key),
                    target_bpm INTEGER NOT NULL, seed INTEGER NOT NULL UNIQUE, status TEXT NOT NULL,
                    measured_bpm REAL, audio_hash TEXT, audio_path TEXT, source TEXT, error TEXT, created_at INTEGER NOT NULL)''')
                c.execute('CREATE TABLE responses (trial_id TEXT PRIMARY KEY, username TEXT NOT NULL, rating INTEGER NOT NULL, tempo REAL NOT NULL)')
                c.execute("INSERT INTO participants VALUES ('legacy','Legacy')")
                c.execute("INSERT INTO trials VALUES ('old','legacy',75,1,'rated',73.8,'old-hash','old.wav','recorded',NULL,1)")
                c.execute("INSERT INTO responses VALUES ('old','Legacy',4,73.8)")
            store = Store(db)
            old = store.get('old')
            self.assertEqual(old['measured_bpm'], 73.8)
            self.assertIsNone(old['label_bpm'])
            token, key = store.start('legacy')
            trial, _ = store.reserve(key, str(uuid.uuid4()))
            self.assertNotEqual(trial['target_bpm'], 75)
            wrong = 75
            self.assertFalse(store.accept_labeled(trial['id'], wrong, 'new-hash', 'new.wav'))
            self.assertTrue(store.accept_labeled(trial['id'], trial['target_bpm'], 'new-hash', 'new.wav'))
            row = store.get(trial['id'])
            self.assertIsNone(row['measured_bpm'])
            self.assertEqual(row['source'], 'recorded-folder')
            self.assertEqual(store.rate(key, trial['id'], 5)['tempo'], trial['target_bpm'])
            # Reopening is idempotent and does not relabel existing responses.
            reopened = Store(db)
            self.assertEqual(reopened.identify(token), key)
            self.assertIn('Legacy,4,73.8', reopened.export_csv())
            self.assertEqual(reopened.progress(key)['rated'], 2)


if __name__ == '__main__':
    unittest.main()
