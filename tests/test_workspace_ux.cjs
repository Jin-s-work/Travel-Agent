const test=require('node:test');
const assert=require('node:assert/strict');
const {bookingTimeProposal,bookingCorrection,summaryEvent,insertionDay,createDraftStore}=require('../web/js/workspace.js');
const clone=value=>JSON.parse(JSON.stringify(value));
function proposal(booking,changed={},eventChanged={}){return bookingTimeProposal(booking,{date:booking.date||null,time:booking.time||null,date_end:booking.date_end||null,time_end:booking.time_end||null,...changed},booking.events.map(e=>({...e,...eventChanged[e.id]})));}
test('one restaurant time input proposes matching summary and event without changing timezone',()=>{
  const booking={date:'2026-11-06',time:'18:00',events:[{id:'dinner',start_local:'2026-11-06T18:00',end_local:'2026-11-06T19:30',start_timezone:'Asia/Tokyo',end_timezone:'Asia/Tokyo'}]};
  const result=proposal(booking,{time:'19:00'});assert.equal(result.conflicts.length,0);assert.equal(result.events[0].start_local,'2026-11-06T19:00');assert.equal(result.events[0].end_local,'2026-11-06T19:30');assert.equal(result.events[0].end_timezone,'Asia/Tokyo');assert.deepEqual(result.changes.map(c=>c.field_path),['time','events.dinner.start_local']);assert.equal(booking.time,'18:00');
});
test('editing outbound leg updates its matched summary while preserving return and arrival timezone',()=>{
  const booking={date:'2026-11-06',time:'10:00',date_end:'2026-11-12',time_end:'15:00',events:[{id:'out',start_local:'2026-11-06T10:00',end_local:'2026-11-06T18:00',start_timezone:'Asia/Seoul',end_timezone:'Europe/Madrid'},{id:'return',start_local:'2026-11-11T17:00',end_local:'2026-11-12T15:00',start_timezone:'Europe/Madrid',end_timezone:'Asia/Seoul'}]};
  const result=proposal(booking,{}, {out:{start_local:'2026-11-06T11:00'}});assert.equal(result.summary.time,'11:00');assert.deepEqual(result.events[1],booking.events[1]);assert.equal(result.events[0].end_timezone,'Europe/Madrid');assert.equal(result.changes.some(c=>c.field_path.includes('return')),false);
});
test('date-only correction does not invent midnight and ambiguous multi-leg mapping stays blocked',()=>{
  const booking={date:'2026-11-06',events:[{id:'a',start_local:'2026-11-06'}]};assert.equal(proposal(booking,{date:'2026-11-07'}).events[0].start_local,'2026-11-07');
  booking.events.push({id:'b',start_local:'2026-11-06'});assert.equal(summaryEvent(booking,'start'),null);assert.equal(proposal(booking,{date:'2026-11-07'}).conflicts.length,1);
});
test('contradictory explicit summary and leg edits cannot silently overwrite either input',()=>{
  const booking={date:'2026-11-06',time:'18:00',events:[{id:'a',start_local:'2026-11-06T18:00'}]};const value=proposal(booking,{time:'19:00'},{a:{start_local:'2026-11-06T20:00'}});assert.equal(value.conflicts.length,1);assert.equal(value.summary.time,'19:00');assert.equal(value.events[0].start_local,'2026-11-06T20:00');
});
function correctionFixture(booking){
  const control=value=>{const wrapper={hidden:false};return {value:value||'',closest:()=>wrapper};};
  const inputs=Object.fromEntries(['date','date_end','time','time_end'].map(key=>[key,control(booking[key])]));
  const eventInputs=booking.events.map(event=>({event,inputs:Object.fromEntries(['start_local','end_local','start_timezone','end_timezone'].map(key=>[key,control(event[key])]))}));
  const submit={};let changed;const form={insertBefore(){},querySelector:()=>submit,addEventListener:(_name,callback)=>{changed=callback;}};
  const make=()=>({append(){},replaceChildren(){},setAttribute(){},scrollIntoView(){}});
  const correction=bookingCorrection({booking,inputs,eventInputs,form,footer:{},make});
  return {correction,inputs,eventInputs,edit(input,value){input.value=value;changed({target:input});}};
}
test('repeated one-input booking previews distinguish hidden proposals from user edits',()=>{
  const booking={date:'2026-11-06',time:'18:00',events:[{id:'a',start_local:'2026-11-06T18:00',start_timezone:'Asia/Tokyo',end_timezone:'Asia/Tokyo'}]},f=correctionFixture(booking);
  f.edit(f.inputs.time,'19:00');assert.equal(f.correction.prepare(),false);assert.equal(f.eventInputs[0].inputs.start_local.value,'2026-11-06T19:00');
  f.edit(f.inputs.time,'19:30');assert.equal(f.correction.prepare(),false);assert.equal(f.inputs.time.value,'19:30');assert.equal(f.eventInputs[0].inputs.start_local.value,'2026-11-06T19:30');assert.equal(f.correction.prepare(),true);assert.equal(booking.time,'18:00');assert.equal(booking.events[0].start_local,'2026-11-06T18:00');
});
test('reverting a preview to the original clears its hidden proposal without changing the booking',()=>{
  const booking={date:'2026-11-06',time:'18:00',events:[{id:'a',start_local:'2026-11-06T18:00'}]},f=correctionFixture(booking);
  f.edit(f.inputs.time,'19:00');assert.equal(f.correction.prepare(),false);f.edit(f.inputs.time,'18:00');assert.equal(f.correction.prepare(),true);assert.equal(f.eventInputs[0].inputs.start_local.value,'2026-11-06T18:00');assert.equal(booking.events[0].start_local,'2026-11-06T18:00');
});
test('repeated outbound edits replace only the proposed summary and keep return timezone intact',()=>{
  const booking={date:'2026-11-06',time:'18:00',events:[{id:'out',start_local:'2026-11-06T18:00',start_timezone:'Asia/Seoul',end_timezone:'Europe/Madrid'},{id:'return',start_local:'2026-11-12T18:00',start_timezone:'Europe/Madrid',end_timezone:'Asia/Seoul'}]},f=correctionFixture(booking);
  f.edit(f.eventInputs[0].inputs.start_local,'2026-11-06T19:00');assert.equal(f.correction.prepare(),false);assert.equal(f.inputs.time.value,'19:00');f.edit(f.eventInputs[0].inputs.start_local,'2026-11-06T19:30');assert.equal(f.correction.prepare(),false);assert.equal(f.inputs.time.value,'19:30');assert.equal(f.eventInputs[1].inputs.start_local.value,'2026-11-12T18:00');assert.equal(f.eventInputs[1].inputs.end_timezone.value,'Asia/Seoul');
});
test('a user edit to both representations remains explicit and is preserved on conflict',()=>{
  const booking={date:'2026-11-06',time:'18:00',events:[{id:'out',start_local:'2026-11-06T18:00'},{id:'return',start_local:'2026-11-12T18:00'}]},f=correctionFixture(booking);
  f.edit(f.inputs.time,'19:00');assert.equal(f.correction.prepare(),false);f.edit(f.eventInputs[0].inputs.start_local,'2026-11-06T20:00');assert.throws(()=>f.correction.prepare(),/수정할 시각/);assert.equal(f.inputs.time.value,'19:00');assert.equal(f.eventInputs[0].inputs.start_local.value,'2026-11-06T20:00');assert.equal(booking.time,'18:00');
});
test('first insertion follows exploration and an explicit insertion date wins later',()=>{
  assert.equal(insertionDay('2026-11-06',null,'2026-11-01'),'2026-11-06');assert.equal(insertionDay('2026-11-07','2026-11-08','2026-11-01'),'2026-11-08');
});
function fixture(request,options={}){let trip='A',epoch=1,value={context:{tab:'explore',visit_date:'2026-11-06'},selected_places:[],conditions_draft:null};const restored=[],statuses=[];const store=createDraftStore({request,snapshot:()=>value,restore:v=>{value=v;restored.push(clone(v));},status:(...v)=>statuses.push(v),currentTrip:()=>trip,currentEpoch:()=>epoch,delay:100000,...options});return {store,restored,statuses,set value(v){value=v},get value(){return value},switch(id){trip=id;epoch++;}};}
test('an initial draft response preserves new inputs and untouched saved fields, then saves with its returned version',async()=>{
  let release;const writes=[];
  const base={conditions:{visit:{date:'2026-11-06',local_time:null,timezone:'Asia/Tokyo'},party:{adults:2},categories:['restaurant'],preferred:{tags:[]}},overrides:{},base_conditions_version:3,base_trip_version:4,filters:{strict:false}};
  const server={version:7,context:{tab:'explore',visit_date:'2026-11-07',categories:['cafe']},selected_places:[{place_id:'saved',duration_minutes:90}],conditions_draft:{...clone(base),conditions:{...clone(base.conditions),visit:{...base.conditions.visit,date:'2026-11-07'},party:{adults:4},categories:['cafe'],preferred:{tags:['quiet']}},overrides:{party:{adults:4}},filters:{strict:true}}};
  const f=fixture(async(_path,options)=>{if(!options)return new Promise(r=>release=r);writes.push(clone(options.body));return {version:8};},{conditionsSnapshot:()=>base});
  const loading=f.store.load();f.value.context.visit_date='2026-11-08';f.value.conditions_draft=clone(base);f.value.conditions_draft.conditions.visit.date='2026-11-08';f.value.conditions_draft.overrides.visit={date:'2026-11-08'};f.value.selected_places.push({place_id:'new',duration_minutes:60});f.store.changed();await f.store.save();assert.equal(writes.length,0);
  release(server);await loading;
  assert.equal(f.value.context.visit_date,'2026-11-08');assert.deepEqual(f.value.context.categories,['cafe']);assert.equal(f.value.conditions_draft.conditions.visit.date,'2026-11-08');assert.equal(f.value.conditions_draft.conditions.party.adults,4);assert.deepEqual(f.value.conditions_draft.conditions.preferred.tags,['quiet']);assert.deepEqual(f.value.conditions_draft.overrides,{party:{adults:4},visit:{date:'2026-11-08'}});assert.equal(f.value.conditions_draft.filters.strict,true);assert.deepEqual(f.value.selected_places.map(p=>p.place_id),['saved','new']);
  await f.store.save();assert.equal(writes.length,1);assert.equal(writes[0].expected_version,7);assert.equal(writes[0].conditions_draft.conditions.visit.date,'2026-11-08');f.store.clear();
});
test('preparing before the draft request captures edits made while other initial requests finish',async()=>{
  const f=fixture(async()=>({version:5,context:{tab:'mail',visit_date:'2026-11-07'},selected_places:[{place_id:'saved'}]}));f.store.prepare();f.value.context.tab='itinerary';f.value.selected_places.push({place_id:'new'});f.store.changed();await f.store.load();assert.equal(f.value.context.tab,'itinerary');assert.equal(f.value.context.visit_date,'2026-11-07');assert.deepEqual(f.value.selected_places.map(p=>p.place_id),['saved','new']);f.store.clear();
});
test('pending draft selection edits remove only known places and retain untouched server fields',async()=>{
  let release;const f=fixture(()=>new Promise(r=>release=r));f.value.selected_places=[{place_id:'removed',duration_minutes:60},{place_id:'edited',duration_minutes:60,duration_origin:'default'}];const loading=f.store.load();f.value.selected_places=[{place_id:'edited',duration_minutes:75,duration_origin:'user'}];f.store.changed();release({version:2,context:{},selected_places:[{place_id:'removed',duration_minutes:60},{place_id:'edited',duration_minutes:60,duration_origin:'default',selection_run_id:'server-run'},{place_id:'unseen',duration_minutes:45}]});await loading;assert.deepEqual(f.value.selected_places,[{place_id:'edited',duration_minutes:75,duration_origin:'user',selection_run_id:'server-run'},{place_id:'unseen',duration_minutes:45}]);f.store.clear();
});
test('raw form fields edited during draft loading override only the same saved form fields',async()=>{
  let release;const base={conditions:{party:{adults:2}},overrides:{},filters:{strict:false}},f=fixture(()=>new Promise(r=>release=r),{conditionsSnapshot:()=>base});f.store.prepare();f.store.prepareConditionsForm({'party.adults':'2','preferred.tags':''});const loading=f.store.load();f.value.conditions_draft={...clone(base),filters:{strict:false,form_values:{'party.adults':'5','preferred.tags':''}}};f.store.changed();release({version:2,context:{},selected_places:[],conditions_draft:{...clone(base),filters:{strict:true,form_values:{'party.adults':'3','preferred.tags':'quiet'}}}});await loading;assert.deepEqual(f.value.conditions_draft.filters.form_values,{'party.adults':'5','preferred.tags':'quiet'});assert.equal(f.value.conditions_draft.filters.strict,true);f.store.clear();
});
test('late typed conditions fill untouched open form fields without losing a new raw input',async()=>{
  let release;const base={conditions:{visit:{date:'2026-11-06'},party:{adults:2},preferred:{tags:[]}},overrides:{},filters:{strict:false}},f=fixture(()=>new Promise(r=>release=r),{conditionsSnapshot:()=>base});f.store.prepare();f.store.prepareConditionsForm({'visit.date':'2026-11-06','party.adults':'2','preferred.tags':''});const loading=f.store.load();f.value.conditions_draft={...clone(base),filters:{strict:false,form_values:{'visit.date':'2026-11-06','party.adults':'5','preferred.tags':''}}};f.store.changed();release({version:2,context:{},selected_places:[],conditions_draft:{...clone(base),conditions:{visit:{date:'2026-11-07'},party:{adults:3},preferred:{tags:['quiet']}}}});await loading;assert.deepEqual(f.value.conditions_draft.filters.form_values,{'visit.date':'2026-11-07','party.adults':'5','preferred.tags':'quiet'});f.store.clear();
});
test('restoring a draft does not interpret render callbacks as new user edits',async()=>{
  let store,writes=0,value={context:{tab:'trip'},selected_places:[],conditions_draft:null};store=createDraftStore({request:async(_path,options)=>{if(options)writes++;return {version:2,context:{tab:'mail'},selected_places:[]};},snapshot:()=>value,restore:v=>{value=v;store.changed();},status(){},currentTrip:()=> 'A',currentEpoch:()=>1,delay:100000});await store.load();await store.save();assert.equal(writes,0);assert.equal(value.context.tab,'mail');store.clear();
});
test('an expired draft still supplies the CAS version for new input captured during loading',async()=>{
  let release;const writes=[];const f=fixture(async(_path,options)=>{if(!options)return new Promise(r=>release=r);writes.push(options.body);return {version:10};});const loading=f.store.load();f.value.context.visit_date='2026-11-09';f.store.changed();release({version:9,expired:true,context:{},selected_places:[]});await loading;await f.store.save();assert.equal(writes[0].expected_version,9);assert.equal(f.value.context.visit_date,'2026-11-09');f.store.clear();
});
test('merging unseen saved places never drops a new selection or sends more than thirty places',async()=>{
  let release;const writes=[];const f=fixture(async(_path,options)=>{if(!options)return new Promise(r=>release=r);writes.push(options.body);return {version:4};});const loading=f.store.load();f.value.selected_places=[{place_id:'new'}];f.store.changed();release({version:3,context:{},selected_places:Array.from({length:30},(_,i)=>({place_id:'saved-'+i}))});await loading;assert.equal(f.value.selected_places.length,31);await f.store.save();assert.equal(writes.length,0);assert.match(f.statuses.at(-1)[0],/30곳 이하/);f.value.selected_places.shift();f.store.changed();await f.store.save();assert.equal(writes[0].expected_version,3);assert.equal(writes[0].selected_places.length,30);assert.equal(writes[0].selected_places.at(-1).place_id,'new');f.store.clear();
});
test('the real UI adapter captures edits while initial workspace restoration is suspended',async()=>{
  const fs=require('node:fs'),vm=require('node:vm');let release;const writes=[],conditions={visit:{date:'2026-11-06',timezone:'Asia/Tokyo'},party:{adults:2},categories:['restaurant'],recommendation_types:['local_discovery']};
  const state={epoch:1,trip:{id:'A',version:2},tab:'explore',workspaceSuspended:true,discovery:{view:'recommend',conditions:{version:3,conditions:clone(conditions)},draft:{conditions:clone(conditions),overrides:{},stop_id:null},dirty:false},recommendations:{},itineraries:{selected:new Map()}};
  const node=()=>({prepend(){},append(){},replaceChildren(){},setAttribute(){}}),main=node(),status=node();const context=vm.createContext({module:{exports:{}},setTimeout,clearTimeout,window:{addEventListener(){}},document:{querySelector:selector=>selector==='#main'?main:selector==='#workspaceDraftStatus'?status:null,addEventListener(){}}});vm.runInContext(fs.readFileSync('web/js/workspace.js','utf8'),context);const ux=context.module.exports;
  ux.init({state,api:async(_path,options)=>{if(!options)return new Promise(r=>release=r);writes.push(clone(options.body));return {version:5};},make:node,button:node,filters:()=>({strict:false}),restoreFilters(){},candidates:()=>new Map(),render(){},setExploreView:value=>{state.discovery.view=value;ux.changed();},setTab:value=>{state.tab=value;ux.changed();},notice(){}});
  ux.prepare();const loading=ux.load();state.discovery.draft.conditions.visit.date='2026-11-08';state.discovery.draft.overrides.visit={date:'2026-11-08'};state.discovery.dirty=true;ux.changed();release({version:4,context:{tab:'explore',visit_date:'2026-11-07'},selected_places:[],conditions_draft:{conditions:{...clone(conditions),party:{adults:4}},overrides:{party:{adults:4}},filters:{strict:false},base_conditions_version:3,base_trip_version:2}});await loading;assert.equal(state.discovery.draft.conditions.visit.date,'2026-11-08');assert.equal(state.discovery.draft.conditions.party.adults,4);state.workspaceSuspended=false;await ux.leave();assert.equal(writes[0].expected_version,4);ux.clear();
});
test('server drafts restore A/B/refresh without sharing selected places',async()=>{
  const server=new Map();const f=fixture(async(path,options)=>{if(options?.method==='PUT'){const saved={version:options.body.expected_version+1,...clone(options.body)};server.set(path,saved);return saved;}return server.get(path)||{version:0,context:{},selected_places:[]};});
  await f.store.load();f.value={context:{tab:'explore'},selected_places:[{place_id:'a'}],conditions_draft:null};f.store.changed();await f.store.save();f.switch('B');await f.store.load();assert.equal(f.value.selected_places.length,0);f.value={context:{tab:'mail'},selected_places:[{place_id:'b'}],conditions_draft:null};f.store.changed();await f.store.save();f.switch('A');await f.store.load();assert.equal(f.value.selected_places[0].place_id,'a');f.store.clear();await f.store.load();assert.equal(f.value.selected_places[0].place_id,'a');f.store.clear();
});
test('late draft response from previous trip or cleared session is ignored',async()=>{
  let resolve;const f=fixture(()=>new Promise(r=>resolve=r));const pending=f.store.load();f.switch('B');resolve({version:2,context:{tab:'mail'},selected_places:[{place_id:'private-A'}]});await pending;assert.equal(f.restored.length,0);
  const later=f.store.load();f.store.clear();resolve({version:3,context:{},selected_places:[{place_id:'private-B'}]});await later;assert.equal(f.restored.length,0);
});
test('switching A to B and back before the initial GET completes retains A input without applying its old response',async()=>{
  const requests=[],writes=[];const f=fixture(async(path,options)=>{if(options){writes.push(options.body);return {version:5};}return new Promise(resolve=>requests.push({path,resolve}));});const first=f.store.load();f.value.selected_places=[{place_id:'new-a'}];f.value.context.visit_date='2026-11-09';f.store.changed();f.switch('B');f.value={context:{tab:'mail'},selected_places:[],conditions_draft:null};const other=f.store.load();requests[1].resolve({version:1,context:{tab:'mail'},selected_places:[{place_id:'b'}]});await other;f.switch('A');f.value={context:{tab:'explore',visit_date:'2026-11-06'},selected_places:[],conditions_draft:null};const returned=f.store.load();requests[0].resolve({version:2,context:{tab:'mail'},selected_places:[{place_id:'obsolete'}]});await first;assert.equal(f.value.context.tab,'explore');requests[2].resolve({version:4,context:{tab:'explore',visit_date:'2026-11-07'},selected_places:[{place_id:'saved-a'}]});await returned;assert.equal(f.value.context.visit_date,'2026-11-09');assert.deepEqual(f.value.selected_places.map(p=>p.place_id),['saved-a','new-a']);await f.store.save();assert.equal(writes[0].expected_version,4);f.store.clear();
});
test('409 preserves current input and does not retry until explicit comparison choice',async()=>{
  let writes=0;const f=fixture(async(path,options)=>{if(options?.method==='PUT'){writes++;const error=new Error('conflict');error.status=409;throw error;}return {version:1,context:{},selected_places:[]};});await f.store.load();f.value={context:{tab:'explore'},selected_places:[{place_id:'mine'}],conditions_draft:null};f.store.changed();await f.store.save();assert.equal(f.value.selected_places[0].place_id,'mine');await f.store.save();assert.equal(writes,1);assert.match(f.statuses.at(-1)[0],/현재|입력/);f.store.clear();
});
test('changes made while a save is in flight are serialized with the returned version',async()=>{
  let release;const bodies=[];const f=fixture(async(path,options)=>{if(!options)return {version:0,context:{},selected_places:[]};bodies.push(clone(options.body));if(bodies.length===1)await new Promise(r=>release=r);return {version:bodies.length};});await f.store.load();f.value={context:{tab:'explore'},selected_places:[{place_id:'one'}],conditions_draft:null};f.store.changed();const saving=f.store.save();f.value={context:{tab:'explore'},selected_places:[{place_id:'two'}],conditions_draft:null};f.store.changed();release();await saving;assert.equal(bodies.length,2);assert.equal(bodies[1].expected_version,1);assert.equal(bodies[1].selected_places[0].place_id,'two');f.store.clear();
});
test('absent draft and an explicitly saved empty selection remain distinguishable',async()=>{
  const seen=[];let version=0,expired=false;const store=createDraftStore({request:async()=>({version,expired,context:{},selected_places:[]}),snapshot:()=>({}),restore:(_value,meta)=>seen.push(meta.saved),status(){},currentTrip:()=> 'A',currentEpoch:()=>1});await store.load();version=2;await store.load();expired=true;await store.load();assert.deepEqual(seen,[false,true,false]);store.clear();
});

test('manual trip switch keeps the chosen screen while restoring that trips saved input',async()=>{
  for(const override of [null,'explore','mail']){
    const fs=require('node:fs'),vm=require('node:vm'),state={epoch:1,trip:{id:'B'},tab:'explore',workspaceNavigationOverride:override,workspaceSuspended:true,discovery:{view:'recommend',conditions:{}},recommendations:{},itineraries:{selected:new Map()}};
    const node=()=>({prepend(){},append(){},replaceChildren(){},setAttribute(){}}),main=node(),status=node(),ctx=vm.createContext({module:{exports:{}},setTimeout,clearTimeout,window:{addEventListener(){}},document:{querySelector:s=>s==='#main'?main:s==='#workspaceDraftStatus'?status:null,addEventListener(){}}});vm.runInContext(fs.readFileSync('web/js/workspace.js','utf8'),ctx);const ux=ctx.module.exports;
    ux.init({state,api:async()=>({version:3,context:{tab:'ask'},selected_places:[{place_id:'b-only',duration_minutes:60}],conditions_draft:null}),make:node,button:node,filters:()=>({}),restoreFilters(){},candidates:()=>new Map(),render(){},setExploreView:v=>state.discovery.view=v,setTab:v=>state.tab=v,notice(){}});
    await ux.load();assert.equal(state.tab,override||'ask');assert.deepEqual([...state.itineraries.selected.keys()],['b-only']);ux.clear();
  }
});
