"""Recorded-audio study. Run one Uvicorn worker; frontend can live on GitHub Pages."""
import asyncio
import hashlib
import hmac
import json
import logging
import secrets
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Depends, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictInt

import settings as cfg
from store import Store, StudyError, Finished, TEMPOS

cfg.STATE.mkdir(parents=True,exist_ok=True)
store = Store(cfg.DATABASE)
pool = ThreadPoolExecutor(max_workers=1)  # serialize clip selection; no model generation
catalog = []
service_ready = False
log = logging.getLogger('tempo-study')


def initialize_audio():
    global catalog, service_ready
    catalog, service_ready = [], False
    try:
        if not cfg.AUDIO_FOLDER.is_dir():
            log.error('Recorded audio folder is missing: %s', cfg.AUDIO_FOLDER)
            return
        # Index only on startup. No checkpoint, dataset, generation script or GPU.
        index = cfg.STATE / 'folder-index.json'
        subprocess.run([cfg.AUDIO_PYTHON, str(Path(__file__).with_name('audio_catalog.py')),
                        '--folder', str(cfg.AUDIO_FOLDER), '--output', str(index),
                        '--cache', str(cfg.STATE / 'recorded-cache')], check=True, timeout=3600)
        catalog = json.loads(index.read_text())
        missing = []
        for bpm in TEMPOS:
            count = sum(clip.get('label_bpm') == bpm for clip in catalog)
            log.warning('Folder coverage: label=%s BPM, usable clips=%s', bpm, count)
            if not count:
                missing.append(bpm)
        if missing:
            log.error('Study disabled: no usable recorded clips in tempo folders %s. '
                      'Check tempo folder names and WAV/FLAC files inside AUDIO_FOLDER, then restart. Run diagnose_audio.py.', missing)
            return
        service_ready = True
    except Exception:
        log.exception('Recorded audio initialization failed; study collection remains disabled.')


@asynccontextmanager
async def lifespan(app):
    store.recover()
    await asyncio.to_thread(initialize_audio)
    yield
    pool.shutdown(wait=True)


app = FastAPI(title='Tempo study — recorded audio',docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=cfg.ORIGINS,allow_credentials=False,
                   allow_methods=['GET','POST'],allow_headers=['Authorization','Content-Type'],expose_headers=['Content-Disposition'])


@app.exception_handler(StudyError)
async def study_error(request, exc):
    return JSONResponse({'detail':str(exc)},status_code=409)


class StartRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str = Field(min_length=2,max_length=32)

class TrialRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')  # a client-supplied BPM is rejected
    request_id: uuid.UUID

class RatingRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: uuid.UUID
    rating: StrictInt = Field(ge=1,le=5)


def participant(authorization: str = Header(default='')):
    if not authorization.startswith('Bearer '):
        raise HTTPException(401,'Enter your username to start.')
    try:
        return store.identify(authorization[7:])
    except StudyError as exc:
        raise HTTPException(401,str(exc))


def view(trial):
    # The assigned target is never accepted from the browser or shown before rating.
    return {'id':trial['id'],'status':trial['status'],'error':trial['error']}


def accept_candidate(trial, clip):
    if clip.get('tempo_source') != 'folder' or type(clip.get('label_bpm')) is not int or clip['label_bpm'] != trial['target_bpm']:
        return False
    path=Path(clip['path']).resolve()
    path.relative_to(cfg.STATE)
    data=path.read_bytes()
    digest=hashlib.sha256(data).hexdigest()
    if digest != clip['sha256'] or len(data)<44 or data[:4]!=b'RIFF' or data[8:12]!=b'WAVE':
        return False
    return store.accept_labeled(trial['id'],clip['label_bpm'],digest,path)


def run_trial(trial):
    request_id=trial['id']
    store.generating(request_id)
    try:
        # Keep the assigned tempo; select only unused matching recorded clips.
        candidates=[clip for clip in catalog if clip.get('label_bpm') == trial['target_bpm']]
        secrets.SystemRandom().shuffle(candidates)
        for clip in candidates:
            try:
                if accept_candidate(trial,clip):
                    return
            except Exception:
                log.exception('Skipping an unavailable recorded candidate.')
        log.warning('No eligible recording: trial=%s target=%s indexed=%s tempo_matches=%s. Run diagnose_audio.py for details.',
                    request_id,trial['target_bpm'],len(catalog),len(candidates))
        store.fail(request_id,'No suitable unplayed audio was available for this tempo. This tempo will not be selected again. Continue with a different tempo if any remain.')
    except Exception:
        log.exception('Trial failed: %s',request_id)
        store.fail(request_id,'Audio could not be prepared. This tempo will not be selected again. Continue with a different tempo if any remain.')


@app.get('/api/health')
def health():
    return {'ready':service_ready,'total_tempos':9,'duration_seconds':30,'audio_mode':'recorded','tempo_source':'folder'}


@app.post('/api/start')
def start(body:StartRequest):
    token,key=store.start(body.username)
    return {'token':token,**store.progress(key)}


@app.get('/api/progress')
def progress(key=Depends(participant)):
    return store.progress(key)


@app.post('/api/trials')
def create_trial(body:TrialRequest,key=Depends(participant)):
    if not service_ready:
        raise HTTPException(503,'The recorded audio collection is not ready. Please contact the researcher.')
    try:
        trial,created=store.reserve(key,str(body.request_id))
    except Finished as exc:
        return {'complete':True,'message':str(exc),**store.progress(key)}
    if created:
        pool.submit(run_trial,trial)
    return view(trial)


@app.get('/api/trials/{request_id}')
def get_trial(request_id:uuid.UUID,key=Depends(participant)):
    return view(store.get(str(request_id),key))


@app.get('/api/audio/{request_id}')
def get_audio(request_id:uuid.UUID,key=Depends(participant)):
    trial=store.get(str(request_id),key)
    if trial['status'] not in ('ready','rated') or not trial['audio_path']:
        raise HTTPException(404,'Audio is not ready.')
    path=Path(trial['audio_path']).resolve()
    try:
        path.relative_to(cfg.STATE)
    except ValueError:
        raise HTTPException(404,'Audio unavailable.')
    if not path.is_file():
        raise HTTPException(404,'Audio file unavailable. Please contact the researcher.')
    return FileResponse(str(path),media_type='audio/wav',headers={'Cache-Control':'private, no-store'})


@app.post('/api/ratings')
def rating(body:RatingRequest,key=Depends(participant)):
    saved=store.rate(key,str(body.request_id),body.rating)
    return {'saved':saved,**store.progress(key)}


@app.get('/api/admin/responses.csv')
def export(authorization:str=Header(default='')):
    if len(cfg.ADMIN_TOKEN)<32 or not hmac.compare_digest(authorization,'Bearer '+cfg.ADMIN_TOKEN):
        raise HTTPException(401,'Unauthorized')
    return Response(store.export_csv(),media_type='text/csv',headers={
        'Content-Disposition':'attachment; filename="tempo-responses.csv"','Cache-Control':'no-store'})

# Same-origin local viewing works too. GitHub Pages serves this folder separately.
app.mount('/',StaticFiles(directory=str(Path(__file__).resolve().parent.parent/'frontend'),html=True),name='frontend')
