/* Actual UI functions: durable progress, honest candidate counts and read-only reconnection. */
const test=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const vm=require('node:vm');
const source=fs.readFileSync('web/js/foundation.js','utf8');
function load(names,globals={}){const context=vm.createContext({console,Set,Map,...globals});for(const name of names){const start=new RegExp('^  (?:async )?function '+name+'\\(', 'm').exec(source)?.index;assert.notEqual(start,undefined,name);const next=/\n  (?:async )?function |\n  (?:const|let) |\n  \$\(/g;next.lastIndex=start+3;const end=next.exec(source)?.index??source.length;vm.runInContext(source.slice(start,end),context);}return context;}
const budgets=new Set(['BUDGET_EXHAUSTED','PROVIDER_OUTCOME_UNKNOWN']);
function models(){return load(['recommendationCounts','recommendationProgressModel'],{budgetCodes:budgets});}
function result(groups){return {sections:{local_discovery:{items:[],needs_confirmation:[],insufficient_data:[],excluded:[],...groups}}};}
function node(tag='',cls='',text=''){return {tag,className:cls,textContent:text,dataset:{},children:[],hidden:false,attributes:{},append(...children){this.children.push(...children);},replaceChildren(...children){this.children=[...children];},setAttribute(k,v){this.attributes[k]=v;},classList:{add(){}}};}
function textContent(n){return [n.textContent,...n.children.map(textContent)].join(' ');}

test('immediate submission is visible even while the prior result is successful',()=>{const c=models();const r={submitting:true,submissionStage:'validating',active:{state:'succeeded',result:result({items:[{place_id:'old'}]})}};const m=c.recommendationProgressModel(r);assert.equal(m.state,'validating');assert.equal(m.busy,true);assert.equal(m.step,0);assert.match(m.title,/여행 조건/);assert.equal(m.count,null);});
test('running stage and count come from the job, never elapsed time or a guessed percentage',()=>{const c=models();const r={active:{state:'running',job:{stage:'constraints_and_scoring',done_count:2,total_count:8}}};const m=c.recommendationProgressModel(r);assert.equal(m.count,'장소 2 / 8건');assert.equal(m.step,1);assert.doesNotMatch(JSON.stringify(m),/%/);r.active.job={stage:'source_revalidation',done_count:0,total_count:1};assert.equal(c.recommendationProgressModel(r).step,2);r.active.job={stage:'route_snapshot',done_count:0,total_count:0};assert.equal(c.recommendationProgressModel(r).count,null);});
test('unranked places are visible candidates without becoming qualified recommendations',()=>{const c=models();const data=result({needs_confirmation:[{place_id:'a'}],insufficient_data:[{place_id:'b'}]});const counts=c.recommendationCounts(data);assert.equal(counts.qualified,0);assert.equal(counts.displayable,2);const m=c.recommendationProgressModel({active:{state:'succeeded',result:data}});assert.equal(m.state,'partial');assert.match(m.title,/살펴볼 장소 2곳/);assert.match(m.description,/아직 방문 조건을 모두 통과한 추천은 아니에요/);});
test('candidate totals deduplicate two sections and give qualified evidence priority',()=>{const c=models();const data=result({items:[{place_id:'a'}],needs_confirmation:[{place_id:'b'}],excluded:[{place_id:'a'},{place_id:'c'}]});data.sections.landmark={items:[],needs_confirmation:[{place_id:'a'}],insufficient_data:[{place_id:'b'},{place_id:'d'}]};assert.equal(JSON.stringify(c.recommendationCounts(data)),JSON.stringify({qualified:1,confirmation:1,reference:1,displayable:3,excluded:1}));});
test('empty, revoked data, cancellation and budget exhaustion have distinct recovery actions',()=>{const c=models();const empty=result({});empty.summary={empty_state:{code:'CATALOG_EMPTY',title:'이 도시 자료를 준비하고 있어요',description:'장소 링크를 저장해 주세요.'}};assert.equal(c.recommendationProgressModel({active:{state:'succeeded',result:empty}}).action,'save');assert.equal(c.recommendationProgressModel({active:{state:'cancelled'}}).action,'retry');assert.equal(c.recommendationProgressModel({active:{state:'failed',error_code:'BUDGET_EXHAUSTED'}}).action,'saved');assert.equal(c.recommendationProgressModel({active:{state:'succeeded',data_status:'stale'}}).state,'partial');assert.equal(c.recommendationProgressModel({connectionError:'NETWORK'}).state,'reconnecting');});
test('unknown submission remains an explicit read-first recovery, preserving retry keys elsewhere',()=>{const c=models();const m=c.recommendationProgressModel({requestError:{code:'REQUEST_UNCONFIRMED'}});assert.equal(m.action,'refresh');assert.match(m.description,/먼저 저장된 요청/);assert.equal(m.busy,false);});
test('progress region announces real state and does not replace unchanged cancel controls',()=>{const nodes=new Map([['#recommendationRunStatus',node()],['#applyRecommendations',node()]]);const state={trip:{id:'t'},discovery:{conditions:{}},recommendations:{active:{run_id:'r',job_id:'j',state:'running',job:{stage:'route_snapshot',done_count:2,total_count:4}}}};const c=load(['recommendationCounts','recommendationProgressModel','renderRecommendationProgress'],{state,$:id=>nodes.get(id),budgetCodes:budgets,make:node,button:(label,fn,cls)=>({...node('button',cls,label),action:fn}),appendRecommendationReasons(){}});c.renderRecommendationProgress();const host=nodes.get('#recommendationRunStatus');assert.equal(nodes.get('#applyRecommendations').attributes['aria-busy'],'true');assert.equal(nodes.get('#applyRecommendations').disabled,true);assert.equal(host.attributes['aria-live'],'polite');assert.match(textContent(host),/이동 구간 2 \/ 4건/);const first=host.children;c.renderRecommendationProgress();assert.equal(host.children,first);assert.equal(host.children[1].children.filter(n=>n.attributes['aria-current']==='step').length,1);});
test('refresh resumes a persisted job by GET and watches its existing id without submitting',async()=>{const calls=[],watched=[],timers=[];const state={session:{authenticated:true},trip:{id:'t'},epoch:1,tab:'explore',recommendations:{serial:0}};const c=load(['loadRecommendations'],{state,recommendationPollTimer:null,clearTimeout(){},setTimeout(fn,ms){timers.push({fn,ms});},tripPath:()=>'/trips/t',allPages:async()=>[{run_id:'r',state:'running'}],api:async(path,opts)=>{calls.push([path,opts?.method||'GET']);return {run_id:'r',job_id:'j',state:'running',job:{stage:'candidate_snapshot'}};},restoreRecommendationOptions(){},renderRecommendationResults(){},renderRecommendationProgress(){},watchJob:(...args)=>watched.push(args)});await c.loadRecommendations();assert.deepEqual(calls,[['/trips/t/recommendations/r','GET']]);assert.deepEqual(watched,[['j',1]]);assert.equal(timers.length,1);assert.equal(state.recommendations.active.run_id,'r');});
test('lost connection retains old results and schedules only a GET retry of the same job',async()=>{const old={run_id:'old',result:result({items:[{place_id:'p'}]})},timers=[];const state={session:{authenticated:true},trip:{id:'t'},epoch:1,tab:'explore',recommendations:{serial:0,active:{run_id:'r',state:'running'},displayed:old}};const c=load(['loadRecommendations'],{state,recommendationPollTimer:null,clearTimeout(){},setTimeout(fn,ms){timers.push({fn,ms});},tripPath:()=>'/trips/t',allPages:async()=>{throw new Error('network');},renderRecommendationProgress(){}});await assert.rejects(c.loadRecommendations({runId:'r'}),/network/);assert.equal(state.recommendations.displayed,old);assert.equal(state.recommendations.connectionError,'CONNECTION_INTERRUPTED');assert.equal(timers.length,1);assert.equal(timers[0].ms,4000);});
test('existing queued job prevents duplicate submits even when click dispatch bypasses disabled state',async()=>{let calls=0;const c=load(['applyRecommendations'],{state:{recommendations:{active:{state:'queued'}}},notice(){},api:()=>calls++});await c.applyRecommendations();assert.equal(calls,0);});

test('uncertainties keep seat status on the card and reveal every reason in an explicit dialog',()=>{
  const names={A:'영업시간 미확인',B:'예약인원 미확인',C:'이동시간 미확인',LIVE_AVAILABILITY_NOT_CONFIRMED:'이 날짜·인원의 잔여석은 미확인이에요'};
  let dialog;
  const c=load(['appendRecommendationReasons','recommendationUnknowns'],{make:node,recommendationReasonNames:names,reviewReasonText:{},button:(label,action)=>({...node('button','',label),action}),openDialog:(title,build)=>{dialog=node();build(dialog)}});
  const host=node(); c.recommendationUnknowns(host,['A','B','C','LIVE_AVAILABILITY_NOT_CONFIRMED']);
  assert.match(textContent(host),/영업시간 미확인/);assert.match(textContent(host),/잔여석은 미확인/);assert.doesNotMatch(textContent(host),/이동시간 미확인/);
  host.children.find(n=>n.tag==='button').action();assert.match(textContent(dialog),/이동시간 미확인/);assert.equal(dialog.children[0].children.length,4);
});
test('uncertainty dialog does not truncate long lists or duplicate reasons',()=>{
  const names=Object.fromEntries(Array.from({length:11},(_,i)=>['R'+i,'조건 '+i]));let dialog;
  const c=load(['appendRecommendationReasons','recommendationUnknowns'],{make:node,recommendationReasonNames:names,reviewReasonText:{},button:(label,action)=>({...node('button','',label),action}),openDialog:(title,build)=>{dialog=node();build(dialog)}});
  const host=node();c.recommendationUnknowns(host,[...Object.keys(names),'R0']);const more=host.children.find(n=>n.tag==='button');assert.match(textContent(more),/확인할 정보 9개/);more.action();assert.equal(dialog.children[0].children.length,11);assert.match(textContent(dialog),/조건 10/);
});

function hoursFormatter(){return load(['discoveryHoursIntervals','discoveryWeeklyHours','discoveryOpeningHours','discoveryFactValue'],{factTermNames:{},factValueLabels:{last_order:'마지막 주문',last_entry:'마지막 입장',breaks:'브레이크',intervals:'영업 구간',timezone:'시간대'}});}
test('opening hours use ordered Korean weekdays, split windows and explicit overnight labels',()=>{const c=hoursFormatter();const value={scope:'published_regular_schedule',timezone:'Europe/Madrid',weekly:{sunday:[],friday:[{start:'11:00',end:'14:30',end_day_offset:0},{start:'17:00',end:'21:30',end_day_offset:0}],monday:[{start:'22:00',end:'02:00',end_day_offset:1}]}};const text=c.discoveryFactValue(value,'opening_hours');assert.match(text,/통상 영업 안내/);assert.match(text,/시설 현지 시간 · Europe\/Madrid/);assert.match(text,/금요일: 11:00–14:30 · 17:00–21:30/);assert.match(text,/월요일: 22:00–다음 날 02:00/);assert.match(text,/일요일: 휴무/);assert(text.indexOf('월요일')<text.indexOf('금요일'));assert.doesNotMatch(text,/end_day_offset|published_regular_schedule|friday/);});
test('hours keep exceptions, breaks, order cutoff, original notes and unknown fields visible',()=>{const c=hoursFormatter();const text=c.discoveryFactValue({weekly:{'0':[{start:'09:00',end:'22:00',end_day_offset:0,last_order:'21:30',breaks:[['14:00','17:00']],custom_interval:'구간 메모'}]},exceptions:[{date:'2026-12-25',closed:true,notes:'Fermé exceptionnellement'},{date:'2026-12-26',intervals:[{start:'12:00',end:'16:00',end_day_offset:0}],last_entry:'15:00'}],notes:['Last order 30 minutes before closing.'],breaks:[['13:00','14:00']],custom_rule:{unparsed:'원문 규칙'}},'opening_hours');for(const pattern of [/마지막 주문: 21:30/,/브레이크: 14:00–17:00/,/custom_interval: 구간 메모/,/2026-12-25: 휴무/,/Fermé exceptionnellement/,/2026-12-26: 12:00–16:00/,/마지막 입장: 15:00/,/Last order 30 minutes before closing\./,/브레이크: 13:00–14:00/,/custom_rule: unparsed: 원문 규칙/])assert.match(text,pattern);});
test('all nine current official restaurant schedules render without internal weekday and offset codes',()=>{const c=hoursFormatter();const payload=JSON.parse(fs.readFileSync('docs/service-v3/data/official-restaurants-2026-10-06.json','utf8'));const facts=(payload.packs||[payload]).flatMap(pack=>pack.places).flatMap(place=>place.facts).filter(fact=>fact.field==='opening_hours');assert.equal(facts.length,9);for(const fact of facts){const text=c.discoveryFactValue(fact.value,'opening_hours');assert.match(text,/월요일:/);assert.match(text,/통상 영업 안내/);assert.doesNotMatch(text,/published_regular_schedule|end_day_offset|monday|friday/);for(const note of fact.value.notes||[])assert(text.includes(note));}});
test('missing weekday stays unknown and non-hours facts keep their ordinary fallback',()=>{const c=hoursFormatter();const text=c.discoveryFactValue({weekly:{monday:null},exceptions:[]},'opening_hours');assert.match(text,/월요일: 미확인/);assert.doesNotMatch(text,/화요일|휴무/);assert.match(text,/등록된 예외 일정 없음/);assert.equal(c.discoveryFactValue({currency:'JPY',amount_min:0},'price'),'currency: JPY\namount_min: 0');});

test('a new submission never shows a previous run cancel action or stale run errors',()=>{for(const previousState of ['failed','succeeded','cancelled','queued','running']){const nodes=new Map([['#recommendationRunStatus',node()],['#applyRecommendations',node()]]);const state={trip:{id:'t'},discovery:{conditions:{}},recommendations:{submitting:true,submissionStage:'validating',active:{run_id:'previous',job_id:'previous-job',state:previousState,data_status:'stale',error_code:'SOURCE_DATA_CHANGED',reason_codes:['SOURCE_DATA_CHANGED'],job:{error_code:'OLD_PROVIDER_ERROR'}}}};const reasons=[];const c=load(['recommendationCounts','recommendationProgressModel','renderRecommendationProgress'],{state,$:id=>nodes.get(id),budgetCodes:budgets,make:node,button:(label,action,cls)=>({...node('button',cls,label),action}),appendRecommendationReasons:(_host,codes)=>reasons.push(...codes)});c.renderRecommendationProgress();const host=nodes.get('#recommendationRunStatus');assert.match(textContent(host),/여행 조건을 확인하고 있어요/);assert.doesNotMatch(textContent(host),/그만 찾기/);assert.deepEqual(reasons,[]);assert.equal(nodes.get('#applyRecommendations').attributes['aria-busy'],'true');}});
test('the newly accepted queued job exposes its own cancellation after validation completes',()=>{const nodes=new Map([['#recommendationRunStatus',node()],['#applyRecommendations',node()]]);const state={trip:{id:'t'},discovery:{conditions:{}},recommendations:{submitting:true,submissionStage:'accepted',active:{run_id:'new',job_id:'new-job',state:'queued',job:{stage:'queued'}}}};const c=load(['recommendationCounts','recommendationProgressModel','renderRecommendationProgress'],{state,$:id=>nodes.get(id),budgetCodes:budgets,make:node,button:(label,action,cls)=>({...node('button',cls,label),action}),appendRecommendationReasons(){}});c.renderRecommendationProgress();assert.match(textContent(nodes.get('#recommendationRunStatus')),/그만 찾기/);});

function cardContext(){return load(['recommendationCard'],{make:node,button:(label,action,cls)=>({...node('button',cls,label),action}),window:{},placePhotoGallery:()=>node('figure','photo'),placeSaveButton:()=>node('button','secondary','저장'),discoveryCategoryNames:{restaurant:'음식점'},recommendationUnknowns:(host,codes)=>{for(const code of codes||[])host.append(node('p','important-unknown',code));},appendRecommendationReasons:(host,codes)=>{for(const code of codes||[])host.append(node('p','reason',code));},reviewDate:value=>value?.slice(0,10)||'미확인',recommendationPrice:()=> '가격 미확인',recommendationMovement:()=> '거리 미확인'});}
function cardItem(patch={}){return {place_id:'p1',name:'식당',category:'restaurant',recommendation_type:'local_discovery',important_unknowns:['영업 미확인','잔여석 미확인'],reason_sentences:['방문 조건과 추천 근거를 추가로 확인해야 합니다.'],facts:[{field:'tags',status:'verified',usable:true,value:['마드리드 요리','타파스','전통 음식'],checked_at:'2026-10-06T09:00:00Z'}],visit_fit:{confirmed_count:0,required_count:3},...patch};}
test('cards keep primary actions visible and move secondary actions to an explicit menu',()=>{const c=cardContext(),card=c.recommendationCard(cardItem(),{run_id:'r'},null),visible=card.children.filter(n=>n.tag!=='details').map(textContent).join(' '),more=card.children.find(n=>n.tag==='details'&&textContent(n).includes('더 보기'));assert.match(visible,/마드리드 요리 · 타파스/);assert.doesNotMatch(visible,/전통 음식|방문 조건과 추천 근거를 추가로|0\/3/);assert.match(visible,/영업 미확인/);assert.match(visible,/잔여석 미확인/);assert.match(visible,/자료 확인 2026-10-06/);assert.match(visible,/상세 보기.*저장.*더 보기/);assert.doesNotMatch(visible,/지역 자료 기반|일정에 넣기/);assert.equal(card.children.some(n=>n.tag==='details'),false);});
test('card tags and fallback check dates do not promote unusable or conflicting facts',()=>{const c=cardContext();for(const facts of [[{field:'tags',status:'verified',usable:false,value:['허용 안 된 태그'],checked_at:'2026-10-06'}],[{field:'tags',status:'provisional',usable:true,value:['잠정 태그']}],[{field:'tags',status:'verified',usable:true,value:['상충 태그']},{field:'tags',status:'conflict',usable:true,value:['다른 태그']}]]){const card=c.recommendationCard(cardItem({facts}),{run_id:'r'},null);assert.doesNotMatch(textContent(card),/허용 안 된 태그|잠정 태그|상충 태그|다른 태그/);assert.doesNotMatch(textContent(card),/자료 확인 2026/);}});
test('ranked card reasons and actual checked date remain, and a generic fallback is omitted even without unknowns',()=>{const c=cardContext(),card=c.recommendationCard(cardItem({checked_at:'2026-10-05T09:00:00Z',reason_sentences:['지역 공식 자료가 뒷받침해요.','선택한 취향에 맞아요.'],visit_fit:{confirmed_count:3,required_count:3}}),{run_id:'r'},1);assert.match(textContent(card),/지역 공식 자료가 뒷받침해요/);assert.match(textContent(card),/선택한 취향에 맞아요/);assert.match(textContent(card),/추천 근거 확인 2026-10-05/);assert.doesNotMatch(textContent(c.recommendationCard(cardItem({important_unknowns:[]}),{run_id:'r'},null)),/방문 조건과 추천 근거를 추가로 확인해야 합니다/);});

function resultRecoveryContext(responses){
  const calls=[],timers=[],watched=[],state={session:{authenticated:true},trip:{id:'t'},epoch:1,tab:'explore',recommendations:{serial:0}};
  const context=load(['loadRecommendations'],{state,recommendationPollTimer:null,clearTimeout(){},setTimeout(fn,ms){timers.push({fn,ms});return timers.length;},tripPath:()=>'/trips/t',allPages:async path=>{calls.push([path,'GET']);return [{run_id:'r',state:'succeeded'}];},api:async(path,options)=>{calls.push([path,options?.method||'GET']);const response=responses.length>1?responses.shift():responses[0];if(response instanceof Error)throw response;return response;},restoreRecommendationOptions(){},renderRecommendationResults(){},renderRecommendationProgress(){},watchJob:(...args)=>watched.push(args)});
  return {context,state,calls,timers,watched};
}
test('terminal job without a result automatically re-reads that saved run and displays its cards',async()=>{
  const complete={run_id:'r',state:'succeeded',job_id:'j',result:result({needs_confirmation:[{place_id:'p'}]})};
  const {context,state,calls,timers,watched}=resultRecoveryContext([{run_id:'r',state:'succeeded',job_id:'j',result:null},complete]);
  await context.loadRecommendations({runId:'r'});assert.equal(state.recommendations.resultRecoveryPending,true);assert.equal(timers.length,1);assert.equal(timers[0].ms,1200);
  await timers.shift().fn();assert.equal(state.recommendations.displayed,complete);assert.equal(state.recommendations.resultRecoveryPending,false);assert.equal(timers.length,0);assert.deepEqual(watched,[]);assert(calls.every(([,method])=>method==='GET'));assert.equal(calls.filter(([path])=>path==='/trips/t/recommendations/r').length,2);
});
test('missing terminal results stop after three automatic retries and offer an explicit saved-request check',async()=>{
  const {context,state,calls,timers}=resultRecoveryContext([{run_id:'r',state:'partial',result:null}]);await context.loadRecommendations({runId:'r'});
  for(let attempt=0;attempt<3;attempt++){assert.equal(timers.length,1);await timers.shift().fn();}
  assert.equal(timers.length,0);assert.equal(state.recommendations.resultRecoveryPending,false);assert.equal(calls.filter(([path])=>path==='/trips/t/recommendations/r').length,4);assert(calls.every(([,method])=>method==='GET'));
  const progress=models().recommendationProgressModel(state.recommendations);assert.equal(progress.action,'refresh');assert.equal(progress.busy,false);
});
test('terminal recovery cannot query an old run after a trip, tab or newer request changes',async()=>{
  for(const change of [state=>{state.epoch++;state.trip={id:'other'};},state=>{state.tab='ask';},state=>{state.recommendations.serial++;}]){const {context,state,calls,timers}=resultRecoveryContext([{run_id:'r',state:'succeeded',result:null}]);await context.loadRecommendations({runId:'r'});const before=calls.length;change(state);await timers.shift().fn();assert.equal(calls.length,before);}
  const stale=resultRecoveryContext([{run_id:'r',state:'succeeded',data_status:'stale',result:null}]);await stale.context.loadRecommendations();assert.equal(stale.timers.length,0);assert.equal(stale.state.recommendations.resultRecoveryPending,false);
});
test('connection loss during terminal recovery still consumes the same three-read retry bound',async()=>{
  const {context,state,calls,timers}=resultRecoveryContext([{run_id:'r',state:'succeeded',result:null},new Error('network')]);await context.loadRecommendations();
  for(let attempt=0;attempt<3;attempt++){assert.equal(timers.length,1);await timers.shift().fn();}
  assert.equal(timers.length,0);assert.equal(state.recommendations.resultRecoveryPending,false);assert.equal(calls.filter(([path])=>path==='/trips/t/recommendations/r').length,4);assert(calls.every(([,method])=>method==='GET'));
});
test('result handoff shows an honest final-stage loader and rejects a second recommendation submission',async()=>{
  const recommendations={active:{run_id:'r',state:'succeeded',result:null},resultRecoveryPending:true};const progress=models().recommendationProgressModel(recommendations);assert.equal(progress.state,'finalizing');assert.equal(progress.busy,true);assert.equal(progress.step,2);assert.equal(progress.action,null);
  let submits=0;const c=load(['applyRecommendations'],{state:{recommendations},notice(){},api(){submits++;}});await c.applyRecommendations();assert.equal(submits,0);
});


test('public discovery shows the city-center scope and attribution without upgrading cache or missing data',()=>{
  const c=load(['renderPublicDiscovery'],{make:node,reviewDate:v=>v?.slice(0,10)||'미확인',safeDiscoveryLink:(host,url,label)=>host.append({...node('a','',label),href:url})});
  const ready=node();c.renderPublicDiscovery(ready,{provider:'openstreetmap',state:'ready',cache_hit:true,fetched_at:'2026-10-07T00:00:00Z'});
  assert.match(textContent(ready),/도심 주변 3km/);assert.match(textContent(ready),/저장된 자료 재사용/);assert.match(textContent(ready),/숙소 주변 전체 검색이 아니에요/);assert.match(textContent(ready),/OpenStreetMap/);
  const capped=node();c.renderPublicDiscovery(capped,{provider:'openstreetmap',state:'unavailable',reason:'PUBLIC_DISCOVERY_DAILY_LIMIT'});assert.equal(capped.children.length,0); // Failure appears once, in the progress region.
  const empty=node();c.renderPublicDiscovery(empty,{provider:'openstreetmap',state:'empty'});assert.match(textContent(empty),/찾지 못했/);
  const none=node();c.renderPublicDiscovery(none,{state:'unavailable',reason:'EXTERNAL_DISCOVERY_NOT_CONFIGURED'});assert.equal(none.children.length,0);c.renderPublicDiscovery(none,{provider:'openstreetmap',state:'not_needed'});assert.equal(none.children.length,0);
});
test('public discovery progress describes the actual provider stage, without a guessed percentage',()=>{
  const m=models().recommendationProgressModel({active:{state:'running',job:{stage:'public_discovery',done_count:0,total_count:1}}});
  assert.match(m.title,/공개 지도/);assert.equal(m.busy,true);assert.doesNotMatch(JSON.stringify(m),/%/);
});

test('public map tag formatting labels raw hours as unconfirmed source material',()=>{const c=hoursFormatter();const text=c.discoveryFactValue({tags:{cuisine:'french',opening_hours:'Mo-Fr 12:00-22:00'},address_status:'missing'},'public_map_tags');assert.match(text,/음식 종류: french/);assert.match(text,/통상 영업 안내 원문/);assert.match(text,/방문일 적용 및 최신 정보 확인 필요/);assert.doesNotMatch(text,/address_status|예약 가능|검증 완료/);});

function delayedResultContext(){
  let release,reject;const response=new Promise((resolve,fail)=>{release=resolve;reject=fail;});
  const calls=[],timers=[],progress=[],state={session:{authenticated:true},trip:{id:'t'},epoch:1,tab:'explore',jobs:new Map(),uploads:[],recommendations:{serial:0,active:{run_id:'r',job_id:'j',state:'running',result:null}}};
  const context=load(['recommendationCounts','recommendationProgressModel','loadRecommendations','mergeJob','applyRecommendations'],{state,budgetCodes:budgets,terminal:job=>['succeeded','partial','failed','cancelled'].includes(job.state),recommendationPollTimer:null,clearTimeout(){},setTimeout(fn,ms){timers.push({fn,ms});return timers.length;},tripPath:()=>'/trips/t',allPages:async path=>{calls.push([path,'GET']);return [{run_id:'r',state:'running'}];},api:async(path,options)=>{calls.push([path,options?.method||'GET']);return response;},restoreRecommendationOptions(){},renderRecommendationResults(){},renderRecommendationProgress(){progress.push(context.recommendationProgressModel(state.recommendations));},renderJobs(){},renderUploads(){},watchJob(){},notice(){}});
  return {context,state,calls,timers,progress,release,reject};
}
test('terminal event keeps a delayed same-run GET busy and deduplicates refresh and submission',async()=>{
  const {context,state,calls,progress,release}=delayedResultContext();
  const pending=context.loadRecommendations({runId:'r'});await new Promise(setImmediate);
  assert.equal(calls.filter(([path])=>path==='/trips/t/recommendations/r').length,1);
  context.mergeJob({job_id:'j',state:'succeeded',stage:'recommendation_complete'});
  assert.equal(progress.at(-1).state,'finalizing');assert.equal(progress.at(-1).busy,true);
  assert.equal(state.recommendations.resultRecoveryPending,true);
  await context.loadRecommendations({runId:'r'});await context.applyRecommendations();
  assert.equal(state.recommendations.serial,1);assert.equal(calls.length,2);assert(calls.every(([,method])=>method==='GET'));
  const complete={run_id:'r',job_id:'j',state:'succeeded',result:result({needs_confirmation:[{place_id:'p'}]})};release(complete);await pending;
  assert.equal(state.recommendations.displayed,complete);assert.equal(state.recommendations.resultRecoveryPending,false);assert.equal(state.recommendations.resultLoad,null);assert.equal(progress.at(-1).busy,false);
});
test('an older running GET cannot undo a terminal event and schedules the bounded result recovery',async()=>{
  const {context,state,timers,release}=delayedResultContext();
  const pending=context.loadRecommendations({runId:'r'});await new Promise(setImmediate);
  context.mergeJob({job_id:'j',state:'succeeded',stage:'recommendation_complete'});
  release({run_id:'r',job_id:'j',state:'running',job:{stage:'source_revalidation'},result:null});await pending;
  assert.equal(state.recommendations.active.state,'succeeded');assert.equal(state.recommendations.active.job.stage,'recommendation_complete');
  assert.equal(state.recommendations.resultRecoveryPending,true);assert.equal(state.recommendations.resultLoad,null);assert.equal(timers.length,1);assert.equal(timers[0].ms,1200);
});
test('failed or cancelled terminal events clear the loader even when an older GET is pending',async()=>{
  for(const terminalState of ['failed','cancelled']){
    const {context,state,timers,progress,release}=delayedResultContext();
    const pending=context.loadRecommendations({runId:'r'});await new Promise(setImmediate);
    context.mergeJob({job_id:'j',state:terminalState,error_code:terminalState==='failed'?'SOURCE_DATA_CHANGED':null});
    assert.equal(state.recommendations.resultRecoveryPending,false);assert.equal(progress.at(-1).busy,false);
    release({run_id:'r',job_id:'j',state:'running',result:null});await pending;
    assert.equal(state.recommendations.active.state,terminalState);assert.equal(state.recommendations.resultLoad,null);assert.equal(timers.length,0);
  }
});
test('manual terminal refresh shows loading before its response and an old trip read cannot change the new scope',async()=>{
  const {context,state,progress,release}=delayedResultContext();state.recommendations.active.state='succeeded';
  const old=state.recommendations,pending=context.loadRecommendations({runId:'r'});
  assert.equal(old.resultRecoveryPending,true);assert.equal(progress.at(-1).state,'finalizing');
  await new Promise(setImmediate);state.epoch++;state.trip={id:'other'};state.recommendations={serial:0,active:null,resultRecoveryPending:false};
  const before=progress.length;release({run_id:'r',state:'succeeded',result:result({items:[{place_id:'old'}]})});await pending;
  assert.equal(state.recommendations.active,null);assert.equal(state.recommendations.resultRecoveryPending,false);assert.equal(old.resultLoad,null);assert.equal(progress.length,before);
});

test('map failure preserves exact retry time and offers saved places, not irrelevant condition edits',()=>{const c=models(),value=result({});value.public_discovery={state:'unavailable',retry_at:'2026-10-07T03:00:00Z'};value.summary={empty_state:{title:'지도 서버에 연결하지 못했어요',description:'다시 연결해 주세요.'}};const m=c.recommendationProgressModel({active:{state:'succeeded',result:value}});assert.equal(m.action,'save');assert.equal(m.retryAt,value.public_discovery.retry_at);assert.equal(m.title,value.summary.empty_state.title);});

test('completed detail state updates a history row fetched before the job completed',async()=>{
 const finished={run_id:'r',state:'succeeded',result:result({items:[{place_id:'p'}]})};
 const state={session:{authenticated:true},trip:{id:'t'},epoch:1,tab:'explore',recommendations:{serial:0}};
 const c=load(['loadRecommendations'],{state,recommendationPollTimer:null,clearTimeout(){},setTimeout(){throw Error('No finished job polling');},tripPath:()=>'/trips/t',allPages:async()=>[{run_id:'r',state:'running'}],api:async()=>finished,restoreRecommendationOptions(){},renderRecommendationResults(){},renderRecommendationProgress(){}});
 await c.loadRecommendations();assert.equal(state.recommendations.runs[0].state,'succeeded');
});
