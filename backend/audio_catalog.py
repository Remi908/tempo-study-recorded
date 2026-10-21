#!/usr/bin/env python3
"""Prepare existing WAV/FLAC recordings using their tempo folder labels (Python 3.8+).
No tempo estimator, model evaluator or music generator is used.
"""
import argparse
import hashlib
import io
import json
import re
from pathlib import Path

TEMPOS=(60,75,90,105,120,135)


def canonical(path):
    import librosa
    import numpy as np
    import soundfile as sf
    audio,sr=sf.read(str(path),dtype='float64',always_2d=True)
    if audio.shape[1] not in (1,2) or len(audio)<30*sr or not np.isfinite(audio).all():
        raise ValueError('Require at least 30 seconds of finite mono/stereo audio')
    audio=audio[:30*sr]
    if sr!=8000:
        audio=librosa.resample(audio.T,orig_sr=sr,target_sr=8000).T
    if audio.shape[1]==1:
        audio=np.repeat(audio,2,axis=1)
    audio=audio[:240000]
    audio-=audio.mean(axis=0,keepdims=True)
    peak=float(np.max(np.abs(audio)))
    if peak>0.98:
        audio*=0.98/peak
    if float(np.sqrt(np.mean(audio**2)))<1e-5:
        raise ValueError('Audio is silent')
    out=io.BytesIO()
    sf.write(out,audio,8000,format='WAV',subtype='PCM_16')
    data=out.getvalue()
    served,_=sf.read(io.BytesIO(data),always_2d=True,dtype='float64')
    return data,served


def folder_tempo(path, root):
    """Read complete directory names, never filenames or arbitrary embedded digits."""
    relative = path.resolve().relative_to(root.resolve())
    names = [root.resolve().name] + list(relative.parts[:-1])
    labels = set()
    for name in names:
        match = re.fullmatch(r"(?:(?:tempo|bpm)[ _-]*)?(\d+(?:\.0+)?)(?:[ _-]*bpm)?", name.strip(), re.IGNORECASE)
        if match:
            value = int(float(match.group(1)))
            if value in (60,75,90,105,120,135,150,165,180):
                labels.add(value)
    if not labels:
        raise ValueError('No tempo folder found; use 60, 75, 90, 105, 120 or 135')
    if len(labels) != 1:
        raise ValueError('Conflicting tempo folders in path: ' + str(sorted(labels)))
    tempo = labels.pop()
    if tempo not in TEMPOS:
        raise ValueError('Tempo is outside the active 60–135 BPM range')
    return tempo


def pack(path, cache, tempo):
    data, _ = canonical(path)
    digest = hashlib.sha256(data).hexdigest()
    cache.mkdir(parents=True, exist_ok=True)
    destination = (cache / (digest + '.wav')).resolve()
    destination.write_bytes(data)
    return {'path': str(destination), 'sha256': digest, 'label_bpm': tempo,
            'tempo_source': 'folder'}


def index_folder(args):
    result, conflicts = {}, set()
    for path in sorted(args.folder.rglob('*')):
        if not path.is_file() or path.is_symlink() or path.suffix.lower() not in ('.wav', '.flac'):
            continue
        try:
            tempo = folder_tempo(path, args.folder)
            clip = pack(path, args.cache, tempo)
            clip['source_name'] = str(path.relative_to(args.folder))
            digest = clip['sha256']
            if digest in conflicts:
                continue
            previous = result.get(digest)
            if previous and previous['label_bpm'] != tempo:
                del result[digest]
                conflicts.add(digest)
                print('Conflicting labels for identical audio; excluding both: %s and %s' %
                      (previous['source_name'], clip['source_name']), flush=True)
                continue
            result[digest] = clip
        except Exception as exc:
            print('Recording skipped: %s (%s: %s)' % (path.relative_to(args.folder), type(exc).__name__, exc), flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(list(result.values()), allow_nan=False))
    print('Indexed %d unique recorded clips using folder tempo labels (no BPM estimation)' % len(result), flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--folder', type=Path, required=True)
    p.add_argument('--cache', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if not args.folder.is_dir():
        p.error('Audio folder does not exist: ' + str(args.folder))
    # Keep playback cache outside the input tree, so restart never reindexes it.
    try:
        args.cache.resolve().relative_to(args.folder.resolve())
    except ValueError:
        pass
    else:
        p.error('The playback cache must be outside AUDIO_FOLDER')
    # Fail visibly for a missing dependency instead of reporting every file unusable.
    import librosa, numpy, soundfile
    index_folder(args)

if __name__ == '__main__':
    main()
