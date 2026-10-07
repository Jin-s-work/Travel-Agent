/* State/transport regressions complement the real browser acceptance run. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/js/foundation.js', 'utf8');
function load(names, globals={}) {
  const context = vm.createContext(globals);
  for (const name of names) {
    const pattern = new RegExp('^  (?:async )?function '+name+'\\(', 'm');
    const start = pattern.exec(source)?.index;
    assert.notEqual(start, undefined, name+' exists in real browser source');
    const next = /\n  (?:async )?function |\n  (?:const|let) |\n  \$\(/g;
    next.lastIndex = start + 3;
    const end = next.exec(source)?.index ?? source.length;
    vm.runInContext(source.slice(start,end),context);
  }
  return context;
}
function submitContext(api) {
  const dom = new Map();
  const state={trip:{id:'trip-a',version:2},discovery:{conditions:{version:3}},recommendations:{},session:{authenticated:true,user:{id:'a'}},epoch:1,intents:new Map()};
  const node=()=>({reportValidity:()=>true,append(){},textContent:'',hidden:false});
  const errors=[];
  const context=load(['applyRecommendations'],{state,$:id=>{if(!dom.has(id))dom.set(id,node());return dom.get(id);},notice(){},showError(el,msg){el.textContent=msg||'';},discoveryIntentPayload:()=>({expected_trip_version:2,expected_conditions_version:3,visit_date:'2026-11-06',stop_id:'stop-a',overrides:{},filters:{limit:6}}),loadDiscovery:async()=>{},uid:()=> 'stable-intent',rememberDiscoveryKey:async(_name,key)=>key,renderRecommendationResults(){},make:()=>node(),api,tripPath:trip=>'/trips/'+trip.id,loadRecommendations:async()=>{},loadJobs:async()=>{},fail:e=>errors.push(e),discoveryMessage(){},button:()=>node()});
  return {context,state,errors,dom};
}
test('apply posts the captured trip and versions once while a request is pending', async()=>{
  let resolve; const calls=[]; const {context,state}=submitContext((...args)=>{calls.push(args);return new Promise(r=>resolve=r);});
  const first=context.applyRecommendations(); await context.applyRecommendations();
  assert.equal(calls.length,1); assert.equal(calls[0][0],'/trips/trip-a/discovery-intents');
  assert.equal(calls[0][1].body.expected_conditions_version,3);assert.equal(calls[0][1].headers['Idempotency-Key'],'stable-intent');
  resolve({run_id:'run-a',state:'queued'});await first;
  assert.equal(state.recommendations.active.run_id,'run-a');assert.equal(state.recommendations.submitting,false);
});
test('unknown submit outcome preserves the idempotency key for an explicit retry', async()=>{
  const keys=[];const {context,state}=submitContext(async(_url,opts)=>{keys.push(opts.headers['Idempotency-Key']);throw new Error('network interrupted');});
  await context.applyRecommendations();await context.applyRecommendations();
  assert.deepEqual(keys,['stable-intent','stable-intent']);assert.equal(state.intents.size,1);
});
test('late submit response cannot activate results in another trip scope', async()=>{
  let resolve;const {context,state}=submitContext(()=>new Promise(r=>resolve=r));
  const pending=context.applyRecommendations();await new Promise(done=>setImmediate(done));state.epoch++;state.trip={id:'trip-b'};state.recommendations={};
  resolve({run_id:'run-a',state:'succeeded'});await pending;
  assert.equal(state.recommendations.active,undefined);assert.equal(state.trip.id,'trip-b');
});
test('comparison selection deduplicates, caps at three, and resets for another run',()=>{
  const notices=[];const state={recommendations:{}};
  const context=load(['comparisonSelection','toggleComparison'],{state,notice:m=>notices.push(m),renderComparisonTray(){}});
  for(const id of ['a','b','c','d'])context.toggleComparison({place_id:id},{run_id:'r1'});
  assert.deepEqual(Array.from(state.recommendations.comparison.selected),['a','b','c']);assert.equal(notices.length,1);
  context.toggleComparison({place_id:'b'},{run_id:'r1'});assert.deepEqual(Array.from(state.recommendations.comparison.selected),['a','c']);
  context.toggleComparison({place_id:'d'},{run_id:'r2'});assert.deepEqual(Array.from(state.recommendations.comparison.selected),['d']);
});
test('comparison facts never display unusable or conflicting numeric evidence as zero',()=>{
  const context=load(['comparisonFact']);
  const fact={field:'price',status:'verified',usable:false,value:0};
  assert.equal(context.comparisonFact({facts:[fact]},'price'),null);
  assert.equal(context.comparisonFact({facts:[{...fact,usable:true},{...fact,status:'conflict'}]},'price'),null);
  assert.equal(context.comparisonFact({facts:[{...fact,usable:true}]},'price'),0);
});
test('unavailable review evidence does not become a 0 percent language claim',()=>{
  const context=load(['comparisonLanguage']);
  const label=context.comparisonLanguage({review_evidence:{state:'unavailable',counts:{text_count:200},metrics:{classified_korean_share:0}}});
  assert.match(label,/미확인/);assert.doesNotMatch(label,/0%/);
});
test('saved bookmark selections never inherit an unrelated or stale recommendation run',()=>{
  const context=load(['itineraryRecommendationRef'],{recommendationStale:run=>run.changed===true,comparisonCandidates:()=>new Map([['p1',{}]])});
  const run={run_id:'r1',result:{},input_status:'current',data_status:'current'};
  assert.equal(context.itineraryRecommendationRef([{place_id:'p1'}],run),null);
  assert.equal(context.itineraryRecommendationRef([{place_id:'p1',selection_run_id:'r1'}],run),'r1');
  for(const patch of [{data_status:'stale'},{input_status:'stale'},{changed:true},{result:null}])assert.equal(context.itineraryRecommendationRef([{place_id:'p1',selection_run_id:'r1'}],{...run,...patch}),null);
});
test('timeline dates include empty days through DST without device timezone arithmetic',()=>{
  const context=load(['itineraryDates']);
  assert.deepEqual(Array.from(context.itineraryDates({snapshot:{start_date:'2026-10-24',end_date:'2026-10-26'},items:[]})),['2026-10-24','2026-10-25','2026-10-26']);
});
test('keyboard time movement leaves local clocks for server DST validation',()=>{
  const context=load(['shiftItineraryLocal']);
  assert.equal(context.shiftItineraryLocal('2026-10-25T02:30:00',0),'2026-10-25T02:30');
  assert.equal(context.shiftItineraryLocal('2026-12-31T23:45',30),'2027-01-01T00:15');
});
test('booking locks remain read only while user-locked places can use explicit unlock',()=>{
  const context=load(['itineraryFixed']);
  assert.equal(context.itineraryFixed({item_type:'booking',lock_origin:'booking'}),true);
  assert.equal(context.itineraryFixed({item_type:'place',locked:true,lock_origin:'user'}),false);
});
test('structured conflict codes are rendered as user explanations instead of raw codes',()=>{
  const context=load(['itineraryReasonText'],{itineraryReasonNames:{TIME_OVERLAP:'시간이 겹칩니다.'},recommendationReasonNames:{},reviewReasonText:{}});
  assert.equal(context.itineraryReasonText({code:'TIME_OVERLAP',reason:'TIME_OVERLAP'}),'시간이 겹칩니다.');
  assert.equal(context.itineraryReasonText({code:'TIME_OVERLAP',reason:'고정 예약을 확인해 주세요.'}),'고정 예약을 확인해 주세요.');
});
test('refresh restores an existing itinerary by GET without creating another job',async()=>{
  const methods=[],state={session:{authenticated:true},trip:{id:'t1'},epoch:1,tab:'trip',itineraries:{serial:0,runs:[],selected:new Map(),displayed:null}};
  const context=load(['itineraryId','loadItineraries'],{window:{},state,itineraryPollTimer:null,clearTimeout,tripPath:()=>'/trips/t1',allPages:async()=>[{id:'i1',state:'succeeded'}],api:async(path,options)=>{methods.push(options?.method||'GET');return {id:'i1',version:1,active_revision_id:'rev1',state:'succeeded',data_status:'current',snapshot:{selected:[{place_id:'p1',duration_minutes:90,duration_origin:'user'}]},items:[]};},renderItinerary(){},itineraryCandidatePool:()=>new Map([['p1',{name:'저장한 장소'}]])});
  await context.loadItineraries();assert.deepEqual(methods,['GET']);assert.equal(state.itineraries.displayed.id,'i1');assert.equal(state.itineraries.selected.get('p1').duration_minutes,90);assert.equal(state.itineraries.selected.get('p1').duration_origin,'user');
});

test('large text persists only a public preference and invalid values fall back to normal',()=>{
  const saved=[],element={dataset:{},removeAttribute(name){if(name==='data-text-size')delete this.dataset.textSize;}};
  const control={};const context=load(['textSize'],{document:{documentElement:element},$:()=>control,localStorage:{setItem:(...args)=>saved.push(args)}});
  context.textSize('large');assert.equal(element.dataset.textSize,'large');assert.equal(control.value,'large');
  context.textSize('unexpected');assert.equal(element.dataset.textSize,undefined);assert.equal(control.value,'normal');
  assert.deepEqual(saved,[['travel-inbox-text-size','large'],['travel-inbox-text-size','normal']]);
});
test('planned rest has no reservation assertion while a place retains its booking state',()=>{
  const context=load(['itineraryReservationText']);
  assert.equal(context.itineraryReservationText({item_type:'rest',reservation_status:'not_applicable'}),null);
  assert.equal(context.itineraryReservationText({item_type:'rest',reservation_status:'unknown'}),null);
  assert.equal(context.itineraryReservationText({item_type:'place',reservation_status:'not_booked'}),'미예약');
  assert.equal(context.itineraryReservationText({item_type:'booking',reservation_status:'user_confirmed'}),'사용자가 확인한 예약');
});
test('zero travel is labeled same-place only with explicit identity evidence, never unknown duration',()=>{
  const context=load(['itinerarySamePlaceLeg']);
  assert.equal(context.itinerarySamePlaceLeg({duration_minutes:0,provider:'identity',reason_codes:['SAME_PLACE_NO_TRAVEL']}),true);
  assert.equal(context.itinerarySamePlaceLeg({duration_minutes:null,reason_codes:['SAME_PLACE_NO_TRAVEL']}),false);
  assert.equal(context.itinerarySamePlaceLeg({duration_minutes:0,reason_codes:['NO_ROUTE']}),false);
});
test('stale unknown travel hides retained identity claims while valid zero-minute legs keep them',()=>{
  const make=(_tag,_class,text)=>({text:text??'',children:[],append(...nodes){this.children.push(...nodes);}});
  const flatten=node=>[node.text,...node.children.map(flatten)].join(' ');
  const context=load(['itinerarySamePlaceLeg','itineraryLeg'],{
    make,reviewDate:()=> '미확인',safeDiscoveryLink(){},
    itineraryReasons:(host,codes)=>codes.forEach(code=>host.append(make('p','',code)))
  });
  for(const code of ['SAME_PLACE_NO_TRAVEL','IDENTICAL_LOCATION']){
    const stale=flatten(context.itineraryLeg({basis:'unknown',duration_minutes:null,reason_codes:[code,'SOURCE_DATA_CHANGED']}));
    assert.match(stale,/실제 이동시간 미확인/);
    assert.doesNotMatch(stale,/같은 장소|별도 이동 없음|SAME_PLACE_NO_TRAVEL|IDENTICAL_LOCATION/);
    assert.match(stale,/SOURCE_DATA_CHANGED/);
    const valid=flatten(context.itineraryLeg({basis:'provider',provider:'identity',duration_minutes:0,reason_codes:[code]}));
    assert.match(valid,/같은 장소에서 이어짐 · 별도 이동 없음/);
  }
  assert.equal(context.itinerarySamePlaceLeg({basis:'unknown',duration_minutes:0,reason_codes:['IDENTICAL_LOCATION']}),false);
});


test('switching cities selects an actual stay date, preserving valid return visits',()=>{
  const context=load(['discoveryStayChoice']);const trip={start_date:'2026-11-06',end_date:'2026-11-11'};
  const stays=[{city:'tokyo',start_date:'2026-11-06',end_date:'2026-11-07'},{city:'barcelona',start_date:'2026-11-08',end_date:'2026-11-09'},{city:'tokyo',start_date:'2026-11-10',end_date:'2026-11-11'}];
  assert.equal(context.discoveryStayChoice(stays,'barcelona','2026-11-06',trip).date,'2026-11-08');
  assert.equal(context.discoveryStayChoice(stays,'barcelona','2026-11-06',trip).timezone,'Europe/Madrid');
  assert.equal(context.discoveryStayChoice(stays,'tokyo','2026-11-10',trip).date,'2026-11-10');
  assert.equal(context.discoveryStayChoice(stays,'tokyo','2026-11-08',trip).date,'2026-11-06');
});
test('unsupported and outdated contexts never submit a recommendation job',async()=>{
  let calls=0;const {context,state}=submitContext(async()=>{calls++;});
  for(const context_state of ['unsupported_city','outdated']){state.discovery.conditions.context_state=context_state;await context.applyRecommendations();}
  assert.equal(calls,0);
});
test('calendar dates reject zero and six-digit years while allowing leap dates',()=>{
  const context=load(['calendarDate']);
  for(const value of ['0000-01-01','202626-10-10','2026-02-29','2026-13-01'])assert.equal(context.calendarDate(value),null);
  assert.notEqual(context.calendarDate('2028-02-29'),null);
  assert.notEqual(context.calendarDate('0001-01-01'),null);
});

 test('response to an older draft preserves filters edited during submission', async()=>{
  let resolve;const {context,state}=submitContext(()=>new Promise(r=>resolve=r));
  const pending=context.applyRecommendations();await new Promise(done=>setImmediate(done));
  state.discovery.draftRevision=1;state.discovery.dirty=true;state.discovery.draft={conditions:{party:{adults:5}}};
  state.recommendations.filterRevision=1;state.recommendations.optionsDirty=true;
  resolve({run_id:'older-run',state:'queued',conditions_version:4});await pending;
  assert.equal(state.discovery.dirty,true);assert.equal(state.discovery.draft.conditions.party.adults,5);
  assert.equal(state.recommendations.optionsDirty,true);
 });
 test('sparse changes preserve explicit null and omit unchanged inherited values',()=>{
  const context=load(['sparseChanges','mergeDraft'],{structuredClone});
  const next=context.sparseChanges({party:{adults:1,children:[]},origin:{label:'old'}},{party:{adults:2,children:[]},origin:null});
  assert.equal(JSON.stringify(next),JSON.stringify({party:{adults:2},origin:null}));
  const merged=context.mergeDraft({required:{dietary:['vegan']}},next);
  assert.equal(merged.required.dietary[0],'vegan');assert.equal(merged.origin,null);
 });

test('trip city labels use the entire destination catalog, including aliases and return stays',()=>{
  const cities=JSON.parse(fs.readFileSync('src/destinations/cities.json','utf8')).cities;
  const context=load(['knownDestination','destinationLabel','tripCityLabel'],{destinationCatalog:cities});
  assert(cities.length>=100);for(const city of cities){assert.equal(context.destinationLabel(city.id),city.name_ko);assert.equal(context.destinationLabel(city.name_en),city.name_ko);for(const alias of city.aliases)assert.equal(context.destinationLabel(alias),city.name_ko);}
  assert.equal(context.tripCityLabel({stops:[{city:'madrid'},{city:'Barcelona'},{city:'madrid'}]}),'마드리드 → 바르셀로나 → 마드리드');
  assert.equal(context.destinationLabel('사용자가 적은 작은 마을'),'사용자가 적은 작은 마을');assert.equal(context.destinationLabel(null),'도시 미정');
});
test('late city catalog recovery relabels the current trip without changing its stored city or title',async()=>{
  const cities=JSON.parse(fs.readFileSync('src/destinations/cities.json','utf8')).cities,nodes={'#tripScope':{},'#tripCitySummary':{}},trip={id:'a',title:'내가 적은 여행 이름',start_date:'2026-11-06',end_date:'2026-11-09',stops:[{city:'madrid'}]},state={trip};let resolve,calls=0;
  const context=load(['tripDates','knownDestination','destinationLabel','tripCityLabel','renderTripCityLabels','loadDestinations'],{destinationCatalog:[],discoveryCityNames:{},state,$:selector=>nodes[selector],api:()=>{calls++;return new Promise(done=>resolve=done);}});
  context.renderTripCityLabels();assert.match(nodes['#tripScope'].textContent,/madrid/);assert.equal(nodes['#tripCitySummary'].textContent,'madrid');
  const loading=context.loadDestinations();state.trip={...trip,id:'b',stops:[{city:'barcelona'}]};resolve({cities});await loading;
  assert.equal(nodes['#tripScope'].textContent,'2026-11-06 — 2026-11-09 · 바르셀로나');assert.equal(nodes['#tripCitySummary'].textContent,'바르셀로나');assert.equal(trip.stops[0].city,'madrid');assert.equal(state.trip.stops[0].city,'barcelona');assert.equal(state.trip.title,'내가 적은 여행 이름');
  await context.loadDestinations();assert.equal(calls,1);
});
