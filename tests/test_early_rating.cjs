// Frontend event-flow regression test; no browser or npm dependencies required.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.resolve(__dirname,'../frontend');
class Element{
 constructor(){this.listeners={};this.children=[];this.style={};this.attributes={};this.disabled=false;this.hidden=false;this.value='';this.textContent='';this.dataset={};this.classes=new Set();this.classList={add:n=>this.classes.add(n),remove:n=>this.classes.delete(n),toggle:(n,on)=>on?this.classes.add(n):this.classes.delete(n)};}
 addEventListener(event,listener){(this.listeners[event]??=[]).push(listener);}
 async emit(event){for(const listener of this.listeners[event]||[])await listener({preventDefault(){}});}
 setAttribute(k,v){this.attributes[k]=v;}
 getAttribute(k){return this.attributes[k];}
 removeAttribute(k){delete this.attributes[k];}
 replaceChildren(...c){this.children=c;}
 append(c){this.children.push(c);}
 querySelector(){return this.span??=new Element();}
 focus(){this.focused=true;}
}
(async()=>{
 const html=fs.readFileSync(path.join(root,'index.html'),'utf8');
 const elements=Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map(m=>[m[1],new Element()]));
 assert.match(html,/id="stop-and-rate"/);
 const choices=[1,2,3,4,5].map(value=>{const e=new Element();e.dataset.rating=String(value);return e;});
 const audio=elements.audio;audio.paused=true;audio.currentTime=0;
 audio.pause=()=>{audio.paused=true;void audio.emit('pause');};
 audio.play=async()=>{audio.paused=false;await audio.emit('play');};
 audio.load=()=>{audio.currentTime=0;};
 elements.volume.value='0.6';elements.username.value='test-listener';
 let rated=0,delivered=0,current=null;const saved=[];
 const progress=()=>({username:'test-listener',rated,delivered,attempted:delivered,remaining:6-delivered,total:6,complete:rated===6,current_id:current});
 const fetch=async(url,options)=>{
  let data;const name=url.replace('/api/','');
  if(name==='health')data={ready:true,total_tempos:6,audio_mode:'recorded',tempo_source:'folder'};
  else if(name==='start')data={token:'test',...progress()};
  else if(name==='progress')data=progress();
  else if(name==='trials'){delivered++;current=JSON.parse(options.body).request_id;data={id:current,status:'ready',error:null};}
  else if(name.startsWith('trials/'))data={id:current,status:'ready',error:null};
  else if(name.startsWith('audio/'))return {ok:true,blob:async()=>new Blob(['test'])};
  else if(name==='ratings'){const rating=JSON.parse(options.body).rating;saved.push(rating);rated++;current=null;data={saved:{rating},...progress()};}
  else throw new Error('Unexpected URL '+url);
  return {ok:true,json:async()=>data};
 };
 const storage=()=>{const m=new Map();return {getItem:k=>m.get(k)||null,setItem:(k,v)=>m.set(k,v),removeItem:k=>m.delete(k)};};
 const ctx={window:{TEMPO_CONFIG:{apiBase:''},addEventListener(){}},document:{getElementById:id=>elements[id],querySelectorAll:()=>choices,createElement:()=>new Element()},fetch,AbortController,URL,Blob,crypto:require('node:crypto').webcrypto,localStorage:storage(),sessionStorage:storage(),setTimeout,clearTimeout,setInterval:()=>0,console};
 vm.runInNewContext(fs.readFileSync(path.join(root,'app.js'),'utf8'),ctx);
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(elements.progress.textContent,'6 listening moments');
 await elements['join-form'].emit('submit');
 for(let i=0;i<6;i++){
  await elements.generate.emit('click');
  assert.equal(elements['stop-and-rate'].disabled,true);assert(choices.every(e=>e.disabled));
  await elements.play.emit('click');
  assert.equal(elements['stop-and-rate'].disabled,true);
  if(i===1){audio.currentTime=30;await audio.emit('ended');}
  else{audio.currentTime=2;await audio.emit('timeupdate');assert.equal(elements['stop-and-rate'].disabled,false);await elements['stop-and-rate'].emit('click');assert.equal(audio.paused,true);}
  assert(choices.every(e=>!e.disabled));assert.equal(elements.submit.disabled,true);
  await choices[3].emit('click');assert.equal(elements.submit.disabled,false);
  if(i===0)await elements.play.emit('click'); // A replay must also stop on submission.
  await elements.submit.emit('click');assert.equal(audio.paused,true);
  assert.equal(elements.progress.textContent,`${i+1} of 6 responses saved`);
 }
 assert.deepEqual(saved,[4,4,4,4,4,4]);assert.equal(elements.generate.disabled,true);
 assert.equal(elements.generate.querySelector('span').textContent,'Session finished');
 assert.equal(elements['progress-dots'].children.length,6);
 console.log('PASS: early stop and rating, natural end, pause on submit, next-clip reset, and six-tempo completion.');
})().catch(error=>{console.error(error);process.exitCode=1;});
