# Tempo — recorded audio with folder labels

The website plays your existing recordings. It reads tempo from the recording's folder name. It does not generate music, estimate BPM, or apply a ±5 BPM gate.

## Apply the update

Upload `use_folder_tempos.py` into `/data/VAE/HarmonAI/` on your server. Stop the new website's Uvicorn process with Ctrl+C, then run:

```bash
/home/local/ASURITE/rmselvam/.conda/envs/tempo-study-web/bin/python -s \
  /data/VAE/HarmonAI/use_folder_tempos.py \
  /data/VAE/HarmonAI/tempo-study-recorded
```

The updater backs up changed source files and, if present, the configured SQLite database. It preserves `.env`, `frontend/config.js`, original recordings, participant data and no-repeat history. The existing database gains a nullable `label_bpm` field on the next server startup; previously measured BPM and responses are not rewritten. No fresh database or cleared history is required.

Start the updated website:

```bash
cd /data/VAE/HarmonAI/tempo-study-recorded/backend
/home/local/ASURITE/rmselvam/.conda/envs/tempo-study-web/bin/python -s \
  -m uvicorn app:app --host 127.0.0.1 --port 8001 --workers 1 --no-access-log
```

Forward port 8001 in VS Code and open its forwarded address. Hard-refresh an already open page. For a separately hosted GitHub Pages frontend, publish the updated `frontend/app.js` and `frontend/index.html` to that repository as well.

## Folder layout

Keep `AUDIO_FOLDER` set to the parent that contains all the tempo folders:

```dotenv
AUDIO_FOLDER='/data/VAE/model_e_rating_site/audio'
```

Examples:

| Recording path relative to AUDIO_FOLDER | Used tempo label |
| --- | --- |
| `60/clip.wav` | 60 BPM |
| `75/clip.wav` | 75 BPM |
| `90 BPM/take1.wav` | 90 BPM |
| `105bpm/piano/take2.flac` | 105 BPM |
| `BPM_120/clip.wav` | 120 BPM |
| `tempo_135/clip.wav` | 135 BPM |

Simple numeric folder names are recommended. The six active labels are 60, 75, 90, 105, 120 and 135. Recordings labeled 150, 165 or 180 are skipped. Nested non-tempo subfolders are allowed. Filenames do not affect labels. Paths with conflicting tempo folders (such as `60/75/clip.wav`) are skipped. Identical playback audio found under different tempo labels is excluded with a diagnostic message; duplicates under the same label count as one recording.

All six categories need at least one usable recording before the study enables new trials. WAV and FLAC files must contain at least 30 seconds of finite non-silent mono/stereo audio. These are playback checks, not tempo measurements.

## Preserved study flow

- The participant supplies a username, receives a random unused tempo, listens for up to 30 seconds, can select **Stop and rate** after playback starts, and rates calmingness from 1 to 5.
- Tempos remain hidden in the participant flow. Another tab, refresh, or HTTP retry resumes an existing active request.
- Every attempted tempo remains used for the same username, including failures. The session ends after six distinct active-tempo attempts and completion of any active trial.
- The same recording cannot be assigned twice to one username. Different participants may receive the same recording. A participant can replay the current clip.
- Source files stay untouched. The first 30 seconds are prepared as stereo 8-kHz, 16-bit WAV with DC removal and peak limiting, as in the earlier website. No time stretching or music generation occurs.

The existing audio interpreter (`AUDIO_PYTHON`, or legacy `MODEL_PYTHON`) supplies NumPy, SoundFile and Librosa for playback preparation. Librosa is only used for sample-rate conversion when needed. No evaluator repository, checkpoint, training dataset, GPU or model-trust setting is used. Old unused model settings in `.env` may remain; they have no effect on this version.

Existing saved ratings at every tempo remain in the database and CSV. Progress counts only the six active tempos. On startup, unfinished requests outside this set are marked failed so returning users can continue. The CSV does not record listening duration.

## Responses and provenance

The CSV columns remain exactly `username,rating,tempo`. For newly assigned recordings, **tempo is the folder label supplied by the researcher**, not an independent measurement of the recording. The application does not validate whether that label is musically correct.

New trials store `label_bpm` and `source='recorded-folder'`; their `measured_bpm` stays null. Existing responses and existing ready trials from the earlier version retain their measured values and source tags. If the same database contains both versions, the three-column CSV contains those original measured values alongside new folder labels; consult the trial source fields to distinguish their provenance.

Run `export_responses.py` from `backend/` using the web Python to export the configured database. `backup_database.py /private/backup.sqlite3` makes a consistent SQLite backup.

## Check startup

```bash
curl http://127.0.0.1:8001/api/health
```

A ready folder-based backend reports `ready: true`, `audio_mode: recorded`, and `tempo_source: folder`. Startup prints usable recording counts for each folder label. To inspect the current index:

```bash
cd /data/VAE/HarmonAI/tempo-study-recorded/backend
/home/local/ASURITE/rmselvam/.conda/envs/tempo-study-web/bin/python -s diagnose_audio.py
```

Restart after adding or changing recordings. `STATE_DIR/folder-index.json` is rebuilt at startup; the earlier measured index is not reused. Keep state/cache outside AUDIO_FOLDER.

## Verification

Run `python -m unittest discover -s tests -v` with the test dependencies (web requirements plus httpx, NumPy, SoundFile, Librosa), and `node --check frontend/app.js`. Tests exercise folder parsing, label conflicts, actual indexing/playback, six-tempo non-repeat flow, data migration and export. Audio fixtures are only test data; actual server recordings must still pass startup playback checks.

Run `node tests/test_early_rating.cjs` to check frontend stopping, early rating, playback completion, and reset behavior with simulated browser events.
