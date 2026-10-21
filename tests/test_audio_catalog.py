import argparse
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import audio_catalog


class CatalogTests(unittest.TestCase):
    def test_prepares_first_30_seconds_and_preserves_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'recording.wav'
            samples = .1 * np.sin(2 * np.pi * 440 * np.arange(31 * 8000) / 8000) + .05
            sf.write(path, samples, 8000, subtype='FLOAT')
            original = path.read_bytes()
            data, audio = audio_catalog.canonical(path)
            with sf.SoundFile(io.BytesIO(data)) as handle:
                self.assertEqual((handle.frames, handle.samplerate, handle.channels, handle.subtype),
                                 (240000, 8000, 2, 'PCM_16'))
            self.assertTrue(np.array_equal(audio[:, 0], audio[:, 1]))
            self.assertLess(abs(audio.mean()), .0001)
            self.assertEqual(path.read_bytes(), original)

    def test_index_deduplicates_and_rejects_short_silent_and_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / 'source'
            folder.mkdir()
            labeled = folder / '90'
            labeled.mkdir()
            samples = .1 * np.sin(2 * np.pi * 440 * np.arange(30 * 8000) / 8000)
            sf.write(labeled / 'one.wav', samples, 8000)
            (labeled / 'duplicate.wav').write_bytes((labeled / 'one.wav').read_bytes())
            (labeled / 'linked.wav').symlink_to(labeled / 'one.wav')
            sf.write(labeled / 'short.wav', samples[:8000], 8000)
            sf.write(labeled / 'silent.wav', np.zeros_like(samples), 8000)
            args = argparse.Namespace(folder=folder, cache=root / 'cache', output=root / 'index.json')
            audio_catalog.index_folder(args)
            result = json.loads(args.output.read_text())
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]['label_bpm'], 90)
            data = Path(result[0]['path']).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), result[0]['sha256'])

    def test_filename_does_not_override_folder_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / '60'
            folder.mkdir()
            path = folder / 'seed_45_120bpm.wav'
            sf.write(path, .1 * np.sin(np.arange(240000) * .1), 8000)
            args = argparse.Namespace(folder=root, cache=root.parent / (root.name + '-cache'), output=root / 'index.json')
            import shutil
            self.addCleanup(shutil.rmtree, args.cache, True)
            with patch.dict(sys.modules, {'evaluate_model_e_30s_tempo': None}):
                audio_catalog.index_folder(args)
            clip = json.loads(args.output.read_text())[0]
            self.assertEqual(clip['label_bpm'], 60)
            self.assertEqual(clip['tempo_source'], 'folder')
            self.assertNotIn('measured_bpm', clip)

    def test_supported_folder_names_and_ambiguous_or_missing_labels(self):
        root = Path('/recordings')
        for name in ('60', '60 BPM', '60bpm', 'BPM_60', 'tempo-60', '60.0'):
            self.assertEqual(audio_catalog.folder_tempo(root / name / 'take' / 'clip.wav', root), 60)
        self.assertEqual(audio_catalog.folder_tempo(root / '135' / 'clip.wav', root), 135)
        for path in ('seed60/clip.wav', 'unknown/60bpm.wav', '70/clip.wav', '60/75/clip.wav', '150/clip.wav', '165/clip.wav', '180/clip.wav', '60/150/clip.wav'):
            with self.assertRaises(ValueError):
                audio_catalog.folder_tempo(root / path, root)

    def test_identical_audio_with_conflicting_labels_is_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'audio'
            for tempo in (60, 75):
                folder = source / str(tempo)
                folder.mkdir(parents=True)
                sf.write(folder / 'same.wav', .1 * np.sin(np.arange(240000) * .1), 8000)
            args = argparse.Namespace(folder=source, cache=root / 'cache', output=root / 'index.json')
            audio_catalog.index_folder(args)
            self.assertEqual(json.loads(args.output.read_text()), [])


if __name__ == '__main__':
    unittest.main()
