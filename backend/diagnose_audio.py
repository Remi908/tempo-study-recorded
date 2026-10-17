"""Read-only recorded-audio coverage diagnostics; no participant details printed."""
import json
from pathlib import Path
import settings as cfg
from store import TEMPOS


def main():
    print('Audio mode: recorded files with folder tempo labels — no BPM estimation')
    print('Recorded audio folder:', cfg.AUDIO_FOLDER)
    print('Audio playback preparation Python:', cfg.AUDIO_PYTHON)
    print('State directory:', cfg.STATE)
    supported = [p for p in cfg.AUDIO_FOLDER.rglob('*') if p.is_file() and not p.is_symlink()
                 and p.suffix.lower() in ('.wav', '.flac')] if cfg.AUDIO_FOLDER.is_dir() else []
    print('WAV/FLAC source files:', len(supported))
    try:
        catalog = json.loads((cfg.STATE / 'folder-index.json').read_text())
    except (OSError, ValueError) as exc:
        print('Cannot read recorded index:', exc)
        print('Start the backend once and inspect its startup output.')
        return
    print('Valid unique indexed recordings:', len(catalog))
    missing = []
    for bpm in TEMPOS:
        matches = [clip for clip in catalog if clip.get('label_bpm') == bpm]
        present = sum(Path(clip['path']).is_file() for clip in matches)
        print('%3d BPM: %d matching recordings, %d cached files present' % (bpm, len(matches), present))
        if not present:
            missing.append(bpm)
    print('Missing tempo coverage:', missing or 'none')
    print('Folder tempo label by recording:')
    for clip in catalog:
        print('  %.3f BPM | %s' % (clip['label_bpm'], clip.get('source_name', '(unknown source)')))
    print('Restart after adding or changing files. Source files are never overwritten.')
    print('Requirements: WAV/FLAC, at least 30 seconds, finite non-silent mono/stereo audio.')


if __name__ == '__main__':
    main()
