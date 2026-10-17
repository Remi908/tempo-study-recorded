(() => {
 'use strict';
 const $=id=>document.getElementById(id);
 const config=window.TEMPO_CONFIG||{};
 const base=(config.apiBase||'').replace(/\/$/,'');
 const audio=$('audio');
 const choices=Array.from(document.querySelectorAll('[data-rating]'));
 const state={token:'',joined:false,ready:false,busy:false,current:null,rated:false,ended:false,rating:null,saving:false,progress:null,blob:null,retryAudio:false};
 let polling=null;
 function message(text){$('message').textContent=text||'';$('message').hidden=!text;}
 function storageGet(key){try{return localStorage.getItem(key);}catch{return null;}}
 function storageSet(key,value){try{localStorage.setItem(key,value);}catch{/* The database still preserves progress across reloads. */}}
 function pendingGet(){try{return sessionStorage.getItem('tempo-recorded-pending-request');}catch{return null;}}
 function pendingSet(value){try{if(value)sessionStorage.setItem('tempo-recorded-pending-request',value);else sessionStorage.removeItem('tempo-recorded-pending-request');}catch{}}
 async function request(path,body,blob=false,publicRequest=false){
  const controller=new AbortController(), timeout=setTimeout(()=>controller.abort(),25000);
  try{
   const headers={};if(body!==undefined)headers['Content-Type']='application/json';if(state.token&&!publicRequest)headers.Authorization='Bearer '+state.token;
   const response=await fetch(base+'/api/'+path,{method:body===undefined?'GET':'POST',headers,body:body===undefined?undefined:JSON.stringify(body),signal:controller.signal,cache:'no-store',credentials:'omit'});
   if(!response.ok){let detail='The audio service could not complete this request.';try{const d=await response.json();if(typeof d.detail==='string')detail=d.detail;}catch{}throw new Error(detail);}
   return blob?await response.blob():await response.json();
  }catch(error){if(error.name==='AbortError')throw new Error('The connection timed out. Please retry; your progress is saved.');throw error;}finally{clearTimeout(timeout);}
 }
 function render(){
  const p=state.progress;const complete=!!p?.complete;
  $('username').readOnly=state.joined;
  $('join').hidden=state.joined;
  $('join').disabled=state.busy;
  $('generate').disabled=!state.joined||!state.ready||state.busy||complete||(state.current?.status==='ready'&&!state.rated&&!state.retryAudio);
  $('generate').querySelector('span').textContent=complete?'Session finished':state.busy?'Preparing your audio…':state.retryAudio?'Load audio again':state.current&&['queued','generating'].includes(state.current.status)?'Resume audio request':'Load next audio';
  $('button-note').textContent=complete?'Thank you for taking part.':!state.joined?'Enter your username to begin.':!state.ready?'The researcher is preparing the audio service.':state.current?.status==='ready'&&!state.rated?'Listen and rate to continue.':'Your next tempo will be selected automatically.';
  $('progress').textContent=p?`${p.rated} of 9 responses saved`:'9 listening moments';
  $('progress-caption').textContent=complete?`All nine tempos attempted · ${p.rated} responses saved`:p?`${p.attempted??p.delivered} of 9 tempos attempted · ${p.remaining??(9-p.delivered)} new tempos left`:'Nine tempos. Nine different moments.';
  $('progress-dots').replaceChildren(...Array.from({length:9},(_,i)=>{const dot=document.createElement('i');if(i<(p?.rated||0))dot.className='done';return dot;}));
  choices.forEach(button=>{const value=Number(button.dataset.rating);button.disabled=!state.ended||state.rated||state.saving;button.classList.toggle('chosen',value===state.rating);button.setAttribute('aria-checked',String(value===state.rating));});
  $('submit').disabled=!state.ended||!state.rating||state.rated||state.saving;
  $('submit').textContent=state.saving?'Saving…':state.rated?'Rating saved ✓':'Submit rating ✓';
  $('rating-title').textContent=complete?'Thank you for listening.':state.rated?'Your response is saved.':'How calming did it feel?';
  $('rating-description').textContent=complete?(p.rated===9?'You’ve heard all nine tempos. Your listening session is complete.':`All nine tempos have been attempted. ${p.rated} responses were saved; some audio requests were unavailable.`):state.rated?'Ready for another moment? A different tempo is waiting.':'There’s no right answer. Go with how this piece felt to you.';
  $('rating-note').textContent=state.rated?'Your username, rating, and audio tempo have been saved.':state.ended?'Choose the response that feels right.':'Listen to the full clip, then share your response.';
  $('audio-art').classList.toggle('busy',state.busy);
 }
 async function checkHealth(){
  try{const h=await request('health',undefined,false,true);state.ready=h.ready&&h.audio_mode==='recorded'&&h.tempo_source==='folder';$('connection').textContent=h.audio_mode!=='recorded'||h.tempo_source!=='folder'?'Connect this page to the folder-tempo audio service':state.ready?'The listening room is ready':'The audio collection is being prepared';$('connection-dot').classList.toggle('live',state.ready);}
  catch{state.ready=false;$('connection').textContent='Audio service not connected';$('connection-dot').classList.remove('live');}
  render();
 }
 async function refreshProgress(){state.progress=await request('progress');render();return state.progress;}
 function clearPlayer(){audio.pause();audio.removeAttribute('src');audio.load();if(state.blob)URL.revokeObjectURL(state.blob);state.blob=null;state.ended=false;state.rating=null;state.rated=false;state.retryAudio=false;$('play').disabled=true;$('elapsed').textContent='0:00';}
 async function loadAudio(trial){
  state.current=trial;state.busy=true;state.retryAudio=false;render();
  try{const blob=await request('audio/'+trial.id,undefined,true);if(state.blob)URL.revokeObjectURL(state.blob);state.blob=URL.createObjectURL(blob);audio.src=state.blob;audio.volume=Number($('volume').value);audio.load();$('play').disabled=false;$('track-title').textContent='A moment, just for you';$('track-description').textContent='Press play. Take your time. Notice how it feels.';pendingSet(null);}
  catch(error){state.retryAudio=true;message('Audio could not be loaded. Select “Load audio again” to resume the same clip.');}
  finally{state.busy=false;await refreshProgress().catch(()=>{});render();}
 }
 async function poll(id){
  clearTimeout(polling);state.busy=true;render();
  try{const trial=await request('trials/'+id);state.current=trial;
   if(trial.status==='ready'){await loadAudio(trial);return;}
   if(trial.status==='rated'){pendingSet(null);state.rated=true;state.busy=false;await refreshProgress();render();return;}
   if(trial.status==='failed'){pendingSet(null);state.current=null;state.busy=false;message(trial.error||'The clip could not be prepared. Please try again.');$('track-title').textContent='Let’s try another moment';$('track-description').textContent='No audio was played for this request.';await refreshProgress();render();return;}
   $('track-title').textContent='Your next recording is on its way';$('track-description').textContent=trial.status==='queued'?'Your audio request is in the queue…':'Selecting your recorded audio…';polling=setTimeout(()=>poll(id),3000);
  }catch(error){state.busy=false;message(error.message+' Select “Resume audio request” to continue.');render();}
 }
 $('join-form').addEventListener('submit',async event=>{
  event.preventDefault();if(state.busy||state.joined)return;message('');state.busy=true;render();
  try{const joined=await request('start',{username:$('username').value.trim()},false,true);state.token=joined.token;state.joined=true;state.progress=joined;$('username').value=joined.username;storageSet('tempo-recorded-session',JSON.stringify({token:joined.token,username:joined.username}));state.busy=false;render();if(joined.current_id)await poll(joined.current_id);}
  catch(error){state.busy=false;message(error.message);render();}
 });
 $('generate').addEventListener('click',async()=>{
  if(state.busy||state.progress?.complete)return;message('');
  if(state.retryAudio&&state.current){await loadAudio(state.current);return;}
  if(state.current&&['queued','generating'].includes(state.current.status)){await poll(state.current.id);return;}
  clearPlayer();state.current=null;state.busy=true;render();
  const requestId=pendingGet()||crypto.randomUUID();pendingSet(requestId);
  try{const trial=await request('trials',{request_id:requestId});if(trial.complete){pendingSet(null);state.progress=trial;state.busy=false;render();return;}state.current=trial;await poll(trial.id);}
  catch(error){state.busy=false;message(error.message);render();}
 });
 $('play').addEventListener('click',async()=>{try{if(audio.paused)await audio.play();else audio.pause();}catch{message('Playback could not start. Please try pressing play again.');}});
 audio.addEventListener('play',()=>{$('play').textContent='Ⅱ';$('play').setAttribute('aria-label','Pause audio');$('audio-art').classList.add('is-playing');});
 audio.addEventListener('pause',()=>{$('play').textContent='▶';$('play').setAttribute('aria-label','Play audio');$('audio-art').classList.remove('is-playing');});
 audio.addEventListener('ended',()=>{state.ended=true;render();});
 audio.addEventListener('error',()=>{if(audio.getAttribute('src')){state.retryAudio=true;message('Audio playback failed. Select “Load audio again”.');render();}});
 audio.addEventListener('timeupdate',()=>{const t=Math.floor(audio.currentTime||0);$('elapsed').textContent=`${Math.floor(t/60)}:${String(t%60).padStart(2,'0')}`;Array.from($('waveform').children).forEach((bar,i)=>bar.classList.toggle('elapsed',i/50<audio.currentTime/30));});
 $('volume').addEventListener('input',()=>{audio.volume=Number($('volume').value);});
 choices.forEach(button=>button.addEventListener('click',()=>{state.rating=Number(button.dataset.rating);render();}));
 $('submit').addEventListener('click',async()=>{
  if(!state.current||!state.rating||!state.ended||state.rated||state.saving)return;state.saving=true;message('');render();
  try{const saved=await request('ratings',{request_id:state.current.id,rating:state.rating});state.rating=saved.saved.rating;state.rated=true;state.progress=saved;state.current.status='rated';}
  catch(error){message(error.message);}
  finally{state.saving=false;render();}
 });
 for(let i=0;i<50;i++){const bar=document.createElement('i');bar.style.height=`${8+(Math.sin(i*1.7)+1)*7+(Math.cos(i*.41)+1)*9}px`;$('waveform').append(bar);}
 async function initialize(){
  await checkHealth();
  try{const saved=JSON.parse(storageGet('tempo-recorded-session')||'null');if(saved?.token){state.token=saved.token;state.progress=await request('progress');state.joined=true;$('username').value=state.progress.username;render();if(state.progress.current_id)await poll(state.progress.current_id);}}
  catch{state.token='';state.joined=false;message('Enter the same username to resume your saved progress.');render();}
 }
 initialize();setInterval(checkHealth,30000);
 window.addEventListener('pagehide',()=>{clearTimeout(polling);if(state.blob)URL.revokeObjectURL(state.blob);});
})();
