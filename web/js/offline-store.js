/* Explicit snapshots only. IndexedDB staging never replaces a valid bundle until verified. */
((root) => {
  'use strict';
  const DB='travel-minimal-offline-v1', VERSION=1, MAX=512000;
  let handle=null, revocation=0;
  const channel=typeof BroadcastChannel!=='undefined'?new BroadcastChannel('travel-offline-control'):null;
  const subscribers=new Set();
  function revoked(){try{return localStorage.getItem('travel-offline-revoked')==='1';}catch{return false;}}
  function mark(value){try{if(value)localStorage.setItem('travel-offline-revoked','1');else localStorage.removeItem('travel-offline-revoked');}catch{}}
  function signal(){for(const f of subscribers)f();}
  if(channel)channel.onmessage=e=>{if(e.data==='purge'){purge(false).catch(()=>{});}};
  const error=message=>new Error(message);
  function valid(b,owner){
    if(!b||b.schema_version!==VERSION||b.namespace!==owner||!b.read_only||b.live_verification!==false||!b.trip?.id||!Array.isArray(b.schedules)||!Array.isArray(b.tasks)||!Array.isArray(b.notes))throw error('저장본 형식 또는 계정이 다릅니다.');
    const created=Date.parse(b.generated_at),expires=Date.parse(b.expires_at);
    if(!Number.isFinite(created)||!Number.isFinite(expires)||created>Date.now()+60000||expires<=Date.now()||expires-created>86460000)throw error('저장본이 만료되었거나 날짜를 확인할 수 없습니다.');
    const raw=JSON.stringify(b);
    if(new TextEncoder().encode(raw).length>MAX)throw error('저장본이 너무 큽니다.');
    // Defense in depth against accidentally adding a sensitive DTO field.
    const forbidden=/^(raw_snippet|confirmation_number|payment|session|csrf_token|raw_reviews|raw_response|photo|map_tiles|document_content|booking_id)$/;
    function scan(value){if(!value||typeof value!=='object')return;for(const [key,item]of Object.entries(value)){if(forbidden.test(key))throw error('오프라인 허용 범위 밖의 필드입니다.');scan(item);}}
    scan(b);return raw;
  }
  async function hash(value){return [...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(value)))].map(x=>x.toString(16).padStart(2,'0')).join('');}
  function open(){if(handle)return Promise.resolve(handle);return new Promise((resolve,reject)=>{const r=indexedDB.open(DB,1);r.onupgradeneeded=()=>{for(const n of ['meta','bundles','staging'])r.result.createObjectStore(n);};r.onsuccess=()=>{handle=r.result;handle.onversionchange=()=>{handle.close();handle=null;};resolve(handle);};r.onerror=()=>reject(r.error);r.onblocked=()=>reject(error('다른 창의 저장소를 닫은 뒤 다시 시도해 주세요.'));});}
  async function tx(stores,mode,fn){const db=await open();return new Promise((resolve,reject)=>{const t=db.transaction(stores,mode);let result;try{fn(t,x=>{result=x;});}catch(e){t.abort();reject(e);return;}t.oncomplete=()=>resolve(result);t.onerror=()=>reject(t.error||error('저장 공간이 부족하거나 저장하지 못했습니다. 이전 저장본을 유지합니다.'));t.onabort=()=>reject(t.error||error('저장이 중단되었습니다. 이전 저장본을 유지합니다.'));});}
  async function meta(){return tx(['meta'],'readonly',(t,set)=>{const r=t.objectStore('meta').get('identity');r.onsuccess=()=>set(r.result);});}
  async function purge(broadcast=true){mark(true);revocation++;signal();if(broadcast)channel?.postMessage('purge');return tx(['meta','bundles','staging'],'readwrite',t=>{t.objectStore('meta').put({owner:null,epoch:crypto.randomUUID()},'identity');t.objectStore('bundles').clear();t.objectStore('staging').clear();});}
  async function bind(owner){if(revoked())await purge(false);const m=await meta();if(m?.owner&&m.owner!==owner)await purge();await tx(['meta'],'readwrite',t=>{const s=t.objectStore('meta'),r=s.get('identity');r.onsuccess=()=>s.put({owner,epoch:r.result?.epoch||crypto.randomUUID()},'identity');});mark(false);}
  async function put(bundle,owner){
    const fence=revocation,raw=valid(bundle,owner),checksum=await hash(raw),m=await meta();
    if(revoked()||m?.owner!==owner||fence!==revocation)throw error('계정이 바뀌어 저장을 중단했습니다.');
    const stage=crypto.randomUUID(),key=owner+':'+bundle.trip.id;
    await tx(['staging'],'readwrite',t=>t.objectStore('staging').put({bundle,checksum,epoch:m.epoch},stage));
    try {
      const staged=await tx(['staging'],'readonly',(t,set)=>{const r=t.objectStore('staging').get(stage);r.onsuccess=()=>set(r.result);});
      if(!staged||await hash(valid(staged.bundle,owner))!==staged.checksum||fence!==revocation)throw error('임시 저장 검증에 실패했습니다.');
      await tx(['meta','bundles','staging'],'readwrite',t=>{const r=t.objectStore('meta').get('identity');r.onsuccess=()=>{if(r.result?.epoch!==m.epoch||r.result?.owner!==owner||fence!==revocation){t.abort();return;}t.objectStore('bundles').put(staged,key);t.objectStore('staging').delete(stage);};});
    }catch(e){await tx(['staging'],'readwrite',t=>t.objectStore('staging').delete(stage)).catch(()=>{});throw e;}
  }
  async function list(){if(revoked())return [];const fence=revocation,m=await meta();if(!m?.owner)return [];const values=await tx(['bundles'],'readonly',(t,set)=>{const r=t.objectStore('bundles').getAll();r.onsuccess=()=>set(r.result);});const out=[];for(const v of values){try{valid(v.bundle,m.owner);if(v.epoch===m.epoch&&await hash(JSON.stringify(v.bundle))===v.checksum)out.push(v.bundle);}catch{}}return fence===revocation?out:[];}
  async function remove(trip){const m=await meta();if(m?.owner)await tx(['bundles'],'readwrite',t=>t.objectStore('bundles').delete(m.owner+':'+trip));signal();}
  const api={valid,open,bind,put,list,purge,remove,onPurge:fn=>subscribers.add(fn)};
  root.TravelOffline=api;
  if(typeof module!=='undefined')module.exports=api;
})(typeof window==='undefined'?globalThis:window);
