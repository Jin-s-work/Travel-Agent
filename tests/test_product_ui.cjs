const test=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');

async function fixture(enabled=true){
  const timers=new Map(),calls=[],listeners={},window={};let index=0,callback;
  class Observer{constructor(cb){callback=cb;}observe(){}disconnect(){}}
  window.IntersectionObserver=Observer;
  const document={visibilityState:'visible',querySelector:()=>null,addEventListener:(event,cb)=>listeners[event]=cb};
  const navigator={onLine:true};const state={epoch:1,trip:{id:'trip-a'}};
  const context=vm.createContext({window,document,navigator,IntersectionObserver:Observer,crypto:require('node:crypto').webcrypto,Date,Map,Set,setTimeout:(cb,ms)=>{assert.equal(ms,1000);timers.set(++index,cb);return index;},clearTimeout:id=>timers.delete(id)});
  vm.runInContext(fs.readFileSync('web/js/product.js','utf8'),context);
  const tools=window.ProductTools;tools.init({state,api:async(path,opts)=>{if(path==='/product-preferences')return {analytics_enabled:enabled};calls.push({path,...opts});}});await tools.bind();
  const card={dataset:{},isConnected:true};tools.observe(card,'place-a','run-a');
  const intersect=ratio=>callback([{target:card,isIntersecting:ratio>0,intersectionRatio:ratio}]);
  const tick=async()=>{const pending=[...timers.values()];timers.clear();for(const cb of pending)await cb();await Promise.resolve();};
  return {tools,state,card,calls,timers,document,navigator,intersect,tick,entries:values=>callback(values),hide(){document.visibilityState='hidden';listeners.visibilitychange();}};
}
test('exposure requires half a card for a continuous second and deduplicates a run/place',async()=>{
  const f=await fixture();f.intersect(.49);await f.tick();assert.equal(f.calls.length,0);
  f.intersect(.7);f.intersect(.1);await f.tick();assert.equal(f.calls.length,0);
  f.intersect(.5);await f.tick();assert.equal(f.calls.length,1);
  assert.equal(f.calls[0].body.event_name,'recommendation_view');assert.equal(f.calls[0].body.run_id,'run-a');
  f.intersect(1);await f.tick();assert.equal(f.calls.length,1);
  assert.deepEqual(Object.keys(f.calls[0].body).sort(),['client_at','event_id','event_name','place_id','run_id','schema_version','source_id']);
});
test('opt-out, hidden tab, detached card, offline and changed trip do not emit views',async()=>{
  for(const mode of ['opt-out','hidden','detached','offline','trip-change','clear']){
    const f=await fixture(mode!=='opt-out');f.intersect(1);
    if(mode==='hidden')f.hide();if(mode==='detached')f.card.isConnected=false;if(mode==='offline')f.navigator.onLine=false;
    if(mode==='trip-change'){f.state.epoch++;f.state.trip.id='trip-b';}if(mode==='clear')f.tools.clear();
    await f.tick();assert.equal(f.calls.length,0,mode);
  }
});
test('an offscreen duplicate cannot cancel a visible card; opening a modal cancels exposure',async()=>{
  const f=await fixture(),other={dataset:{},isConnected:true};f.tools.observe(other,'place-a','run-a');
  f.entries([{target:f.card,isIntersecting:true,intersectionRatio:1},{target:other,isIntersecting:false,intersectionRatio:0}]);
  await f.tick();assert.equal(f.calls.length,1);
  const g=await fixture();g.intersect(1);g.tools.suspendExposure();await g.tick();assert.equal(g.calls.length,0);
});
