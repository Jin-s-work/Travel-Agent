/* Execute the service worker in a fake browser scope: private responses must never be cached. */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
function worker() {
  const listeners = {}, deleted = [], writes = [];
  const source=fs.readFileSync(path.join(__dirname,'../web/sw.js'),'utf8');
  const currentCache=/const CACHE = ['"]([^'"]+)['"]/.exec(source)?.[1];
  assert.ok(currentCache, 'worker declares a versioned cache');
  vm.runInNewContext(source, {
    URL, Response, Promise,
    self: { location: { origin:'https://travel.test' }, addEventListener:(name,fn)=>listeners[name]=fn, skipWaiting:async()=>{}, clients:{claim:async()=>{}} },
    caches: { keys:async()=>['old-private-cache',currentCache], delete:async key=>deleted.push(key), open:async()=>({addAll:async()=>{},put:async(req)=>writes.push(req.url)}), match:async()=>undefined },
    fetch:async()=>({ok:true,type:'basic',redirected:false,headers:new Headers(),clone:()=>({})}),
  });
  return {listeners,deleted,writes};
}
test('all private API, download, query, cross-origin and mutation requests bypass the cache',async()=>{
  const {listeners,writes}=worker();
  for(const [method,url] of [['GET','https://travel.test/api/v2/session'],['GET','https://travel.test/api/v2/trips/t/documents/d/content'],['GET','https://travel.test/api/bookings'],['POST','https://travel.test/'],['GET','https://travel.test/?invitation=secret'],['GET','https://other.test/index.html']]) {
    let responded=false;
    listeners.fetch({request:{method,url},respondWith:()=>{responded=true;}});
    assert.equal(responded,false,method+' '+url);
  }
  assert.deepEqual(writes,[]);
});
test('activation removes prior caches, and only a public shell response can be cached',async()=>{
  const {listeners,deleted,writes}=worker();let done;
  listeners.activate({waitUntil:p=>done=p});await done;
  assert.deepEqual(deleted,['old-private-cache']);
  listeners.fetch({request:{method:'GET',url:'https://travel.test/js/foundation.js'},respondWith:p=>done=p});await done;await Promise.resolve();
  assert.deepEqual(writes,['https://travel.test/js/foundation.js']);
});
test('public asset URLs carry their current content hash to prevent mixed HTML and stale scripts',()=>{
  const {createHash}=require('node:crypto');
  const html=fs.readFileSync(path.join(__dirname,'../web/index.html'),'utf8');
  const {listeners}=worker();
  for(const asset of ['js/foundation.js','js/travel-tools.js','js/offline-store.js','css/foundation.css']){
    const hash=createHash('sha256').update(fs.readFileSync(path.join(__dirname,'../web',asset))).digest('hex').slice(0,12);
    assert.ok(html.includes('/'+asset+'?v='+hash),'Run python3 scripts/version_web_assets.py after editing '+asset);
    let intercepted=false;
    listeners.fetch({request:{method:'GET',url:'https://travel.test/'+asset+'?v='+hash},respondWith:()=>{intercepted=true;}});
    assert.equal(intercepted,true,'exact current hash has its own offline cache entry');
    let unrecognized=false;listeners.fetch({request:{method:'GET',url:'https://travel.test/'+asset+'?v=unknown'},respondWith:()=>{unrecognized=true;}});assert.equal(unrecognized,false);
  }
});
