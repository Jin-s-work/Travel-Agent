const test=require('node:test'),assert=require('node:assert/strict');
const simple=require('../web/js/simple-discovery.js'),pages=require('../web/js/recommendation-pages.js'),home=require('../web/js/home.js');
function node(tag='div',cls='',text=''){return {tag,className:cls,textContent:text,value:'',children:[],events:{},isConnected:true,validity:{valid:true},append(...v){this.children.push(...v)},replaceChildren(...v){this.children=v},setAttribute(){},addEventListener(k,f){this.events[k]=f},get options(){return this.children}};}
function fixture(){const fields=new Map(),calls=[],errors=[],requests=[],body=node();let submit,closed=0,applies=0;
  const conditions={city:'tokyo',visit:{date:'2026-11-06'},party:{adults:null},origin:null};const envelope={version:1,conditions,stay_options:[{stop_id:'s',label:'Tokyo',start_date:'2026-11-06',end_date:'2026-11-10'}],trip_context:{stop_id:'s'},origin_context:{candidates:[]}};
  const state={epoch:1,trip:{id:'a',version:1,start_date:'2026-11-06',end_date:'2026-11-10'},session:{features:{simple_discovery:true}},discovery:{conditions:envelope,draft:{conditions:structuredClone(conditions),overrides:{},stop_id:'s'}},recommendations:{displayed:{run_id:'old'}}};
  global.Option=function(text,value){return {text,value}};global.window={};
  const ui={state,make:node,button:(label,fn)=>({textContent:label,click:fn}),ensureDiscoveryDraft:()=>state.discovery.draft,openDialog:(_t,build)=>build(body),formBase:(host,_label,fn)=>{submit=fn;const form=node('form'),grid=node();host.append(form);form.append(grid);return {form,grid}},field:(grid,name,label,value,opts)=>{const field=node();field.value=String(value??'');if(opts.select)field.children=opts.select.map(([value,text])=>({value,text}));fields.set(name,field);grid.append(field);return field},api:(path,opts)=>{calls.push({path,opts});return new Promise((resolve,reject)=>requests.push({resolve,reject,body:opts.body}));},tripPath:()=>'/trips/a',localError:(_f,k,m)=>errors.push({k,m}),formError:(_f,e)=>errors.push(e),render(){},advanced(){},closeDialog:force=>{assert.equal(force,true);closed++},applyRecommendations:async()=>applies++};
  simple.init(ui);simple.clear();const respond=(i,patch={})=>requests[i].resolve({...envelope,conditions:{...structuredClone(conditions),visit:{date:requests[i].body.visit_date}},overrides:requests[i].body.overrides,context_state:'ready',validation:[],...patch});return {ui,state,fields,calls,requests,body,respond,errors,open:()=>simple.open(),submit:()=>submit(),applies:()=>applies,closed:()=>closed};
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));
test('context editor preserves result and commits only after server preview then applies exactly once',async()=>{const t=fixture();t.open();assert.equal(t.applies(),0);t.respond(0);await flush();t.fields.get('party.adults').value='3';const saving=t.submit();assert.equal(t.applies(),0);t.respond(1,{conditions:{city:'tokyo',visit:{date:'2026-11-06'},party:{adults:3}}});await saving;assert.equal(t.applies(),1);assert.equal(t.closed(),1);assert.equal(t.state.discovery.draft.conditions.party.adults,3);assert.equal(t.state.recommendations.displayed.run_id,'old');assert(t.calls.every(c=>c.path.endsWith('/discovery-context/preview')));});
test('three reverse context responses cannot replace the newest date or resolve another account',async()=>{const t=fixture();t.open();t.fields.get('visit.date').value='2026-11-07';t.fields.get('visit.date').events.change();t.fields.get('visit.date').value='2026-11-08';t.fields.get('visit.date').events.change();assert.equal(t.requests.length,3);t.respond(2,{trip_context:{stop_id:'new-stop'}});await flush();t.respond(1,{trip_context:{stop_id:'old-stop'}});t.respond(0);await flush();assert.equal(t.fields.get('stop_id').value,'new-stop');assert.equal(t.fields.get('visit.date').value,'2026-11-08');t.state.epoch++;t.fields.get('visit.date').events.change();t.respond(3,{trip_context:{stop_id:'wrong-account'}});await flush();assert.notEqual(t.fields.get('stop_id').value,'wrong-account');assert.equal(t.applies(),0);});
test('server date-gap and version conflict preserve inputs and never submit work',async()=>{const t=fixture();t.open();t.respond(0);await flush();t.fields.get('visit.date').value='2026-11-09';const saving=t.submit();t.respond(1,{validation:[{field:'visit.date',message:'도시 구간을 확인해 주세요.'}]});await saving;assert.equal(t.applies(),0);assert.equal(t.fields.get('visit.date').value,'2026-11-09');const retry=t.submit();t.requests[2].reject(Object.assign(Error('version conflict'),{status:409}));await retry;assert.equal(t.fields.get('visit.date').value,'2026-11-09');assert.equal(t.state.recommendations.displayed.run_id,'old');assert.equal(t.closed(),0);});
test('unapplied input survives reopening within scope and clears on account/trip reset',async()=>{const t=fixture();t.open();t.respond(0);await flush();t.fields.get('party.adults').value='7';t.fields.get('party.adults').events.input();t.open();assert.equal(t.fields.get('party.adults').value,'7');simple.clear();t.state.epoch++;t.open();assert.equal(t.fields.get('party.adults').value,'');});
test('home gives both evidence sections room even with three famous places first',()=>{const p=id=>({place_id:id});const run={result:{sections:{landmark:{items:[p('f1'),p('f2'),p('f3')]},local_discovery:{items:[p('local')]}}}};assert(home.previews(run).some(p=>p.place_id==='local'));assert.equal(home.previewSections(run).landmark.length,2);assert.equal(home.previewSections(run).local_discovery.length,1);});
test('refresh retains appended order only for the same current run and snapshot',()=>{const build=(ids)=>({run_id:'r',snapshot_version:'s',freshness_state:'current',pages:{reference:{returned:12,next_cursor:'c'}},result:{sections:{reference:{items:ids.map(place_id=>({place_id})),needs_confirmation:[]}}}});const prior=build(Array.from({length:24},(_,n)=>'p'+n)),next=build(Array.from({length:12},(_,n)=>'p'+n));assert.equal(pages.merge(prior,next).result.sections.reference.items.length,24);assert.equal(pages.merge(prior,{...build(['new']),snapshot_version:'other'}).result.sections.reference.items.length,1);assert.equal(pages.merge(prior,{...build(['current']),freshness_state:'stale'}).result.sections.reference.items.length,1);});

function savingFixture(api, refresh = async () => {}) {
  const fs = require('node:fs'), vm = require('node:vm');
  const source = fs.readFileSync('web/js/foundation.js', 'utf8');
  const start = source.indexOf('  async function saveRecommendedPlace(');
  const end = source.indexOf('\n  function recommendationSources(', start);
  assert(start >= 0 && end > start);
  const state = {trip:{id:'a'},epoch:1,tab:'explore',discovery:{bookmarks:[]}};
  const errors = [], notices = [];
  const context = vm.createContext({state,api,tripPath:()=>'/trips/'+state.trip.id,
    loadDiscovery:refresh,renderRecommendationResults(){},notice:msg=>notices.push(msg),
    fail:error=>errors.push(error),$:()=>({})});
  vm.runInContext(source.slice(start,end),context);
  return {state,errors,notices,save:()=>context.saveRecommendedPlace({place_id:'p'},{run_id:'r'})};
}
test('save double click sends one request and refresh failure preserves server-confirmed saved state',async()=>{
  const calls=[];let resolve;
  const t=savingFixture((path,options)=>{calls.push({path,options});return new Promise(r=>resolve=r)},async()=>{throw Error('read failed')});
  const pending=t.save();await t.save();assert.equal(calls.length,1);assert.equal(t.state.discovery.bookmarks.length,0);
  resolve({id:'b',matched_place_id:'p'});await pending;
  assert.equal(t.state.discovery.bookmarks.length,1);assert.equal(t.state.discovery.bookmarks[0].id,'b');
  assert.equal(t.errors.length,0);assert.equal(t.state.savingPlaces.size,0);assert(t.notices.some(n=>n.includes('저장은 완료')));
  assert.equal(home.savedPlace(t.state.discovery.bookmarks,'p').id,'b');
});
test('failed save remains unsaved and an explicit retry can recover a deduplicated server record',async()=>{
  let attempts=0;
  const t=savingFixture(async()=>{if(++attempts===1)throw Error('lost response');return {duplicate:true,bookmark:{id:'b',matched_place_id:'p'}}});
  await t.save();assert.equal(t.errors.length,1);assert.equal(t.state.discovery.bookmarks.length,0);assert.equal(t.state.savingPlaces.size,0);
  await t.save();assert.equal(attempts,2);assert.equal(t.state.discovery.bookmarks.length,1);assert(t.notices.some(n=>n.includes('이미 보관함')));
});
test('late successful save cannot populate another trip after a scope change',async()=>{
  let resolve,reads=0;const t=savingFixture(()=>new Promise(r=>resolve=r),async()=>reads++);
  const pending=t.save();t.state.epoch++;t.state.trip={id:'b'};t.state.discovery={bookmarks:[]};
  resolve({id:'from-a',matched_place_id:'p'});await pending;
  assert.equal(t.state.discovery.bookmarks.length,0);assert.equal(reads,0);assert.equal(t.notices.length,0);
});
