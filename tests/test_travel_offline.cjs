const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm'),fs=require('node:fs'),{webcrypto,randomUUID}=require('node:crypto');
const {IDBFactory,IDBObjectStore}=require('fake-indexeddb');
function fixture(overrides={}){const local=new Map(),ctx={indexedDB:new IDBFactory(),setTimeout,clearTimeout,TextEncoder,Date,Promise,Uint8Array,Error,Object,JSON,Set,crypto:{subtle:webcrypto.subtle,randomUUID},localStorage:{getItem:k=>local.get(k),setItem:(k,v)=>local.set(k,v),removeItem:k=>local.delete(k)},...overrides};ctx.globalThis=ctx;vm.runInNewContext(fs.readFileSync('web/js/offline-store.js','utf8'),ctx);return {store:ctx.TravelOffline,ctx};}
function bundle(owner='A',title='before'){return {schema_version:1,namespace:owner,trip:{id:'trip',title},schedules:[],tasks:[],notes:[],read_only:true,live_verification:false,generated_at:new Date().toISOString(),expires_at:new Date(Date.now()+3600000).toISOString()};}
test('staged snapshots atomically activate and isolate accounts',async()=>{const {store}=fixture();await store.bind('A');await store.put(bundle(),'A');assert.equal((await store.list())[0].trip.title,'before');await store.put(bundle('A','after'),'A');assert.equal((await store.list())[0].trip.title,'after');await store.bind('B');assert.equal((await store.list()).length,0);await assert.rejects(store.put(bundle(),'B'));});
test('quota failure during final activation preserves the previous valid snapshot',async()=>{const {store}=fixture();await store.bind('A');await store.put(bundle(),'A');const original=IDBObjectStore.prototype.put;IDBObjectStore.prototype.put=function(...args){if(this.name==='bundles')throw new DOMException('Synthetic quota exceeded','QuotaExceededError');return original.apply(this,args);};try{await assert.rejects(store.put(bundle('A','new'),'A'));}finally{IDBObjectStore.prototype.put=original;}assert.equal((await store.list())[0].trip.title,'before');});
test('logout fences a download even after staging has completed',async()=>{const {store,ctx}=fixture();await store.bind('A');let calls=0,release,staged;const ready=new Promise(r=>staged=r),gate=new Promise(r=>release=r);ctx.crypto.subtle={digest:async(...args)=>{if(++calls===2){staged();await gate;}return webcrypto.subtle.digest(...args);}};const pending=store.put(bundle(),'A');await ready;await store.purge();release();await assert.rejects(pending);assert.equal((await store.list()).length,0);});
test('expiry, checksum corruption and sensitive fields fail closed',async()=>{const {store}=fixture();await store.bind('A');const expired=bundle();expired.expires_at='2000-01-01T00:00:00Z';await assert.rejects(store.put(expired,'A'));await assert.rejects(store.put({...bundle(),session:'private'},'A'));await store.put(bundle(),'A');const db=await store.open();await new Promise((resolve,reject)=>{const t=db.transaction('bundles','readwrite'),s=t.objectStore('bundles'),r=s.get('A:trip');r.onsuccess=()=>s.put({...r.result,checksum:'bad'},'A:trip');t.oncomplete=resolve;t.onerror=reject;});assert.equal((await store.list()).length,0);});
test('trip removal and revoked marker block local reads',async()=>{const {store,ctx}=fixture();await store.bind('A');await store.put(bundle(),'A');await store.remove('trip');assert.equal((await store.list()).length,0);await store.put(bundle(),'A');ctx.localStorage.setItem('travel-offline-revoked','1');assert.equal((await store.list()).length,0);await store.bind('A');assert.equal((await store.list()).length,0);});
test('an unresponsive fresh device store times out and closes a late open instead of blocking login',async()=>{
  const requests=[],timers=[];let closed=0;
  const {store}=fixture({indexedDB:{open(){const r={result:{close(){closed++;}}};requests.push(r);return r;}},setTimeout(fn){timers.push(fn);return timers.length;},clearTimeout(){}});
  const pending=store.bind('A');const rejected=assert.rejects(pending,/응답하지/);timers.shift()();await rejected;
  requests[0].onsuccess();assert.equal(closed,1);
  const retry=store.open();assert.equal(requests.length,2);requests[1].onsuccess();await retry;
});
test('a blocked open cannot later install a handle after reporting failure',async()=>{
  let request,closed=0;const {store}=fixture({indexedDB:{open(){return request={result:{close(){closed++;}}};}}});
  const opening=store.open(),rejected=assert.rejects(opening,/다른 창/);request.onblocked();await rejected;request.onsuccess();assert.equal(closed,1);
});
test('a stalled storage transaction aborts and revocation remains in force',async()=>{
  const timers=[];let aborted=0,closed=0,transaction;
  const db={close(){closed++;},transaction(){return transaction={objectStore(){return {put(){},clear(){}};},abort(){aborted++;this.onabort?.();}};}};
  let request;const {store,ctx}=fixture({indexedDB:{open(){return request={result:db};}},setTimeout(fn){timers.push(fn);return timers.length;},clearTimeout(){}});
  const opening=store.open();request.onsuccess();await opening;timers.shift();
  const purging=store.purge();await new Promise(resolve=>setImmediate(resolve));const rejected=assert.rejects(purging,/응답/);timers.shift()();await rejected;
  transaction.oncomplete();assert.equal(aborted,1);assert.equal(closed,1);assert.equal(ctx.localStorage.getItem('travel-offline-revoked'),'1');assert.equal((await store.list()).length,0);
});
