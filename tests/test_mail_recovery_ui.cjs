const test=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const vm=require('node:vm');
const source=fs.readFileSync('web/js/foundation.js','utf8');
function load(names,globals={}){const c=vm.createContext({Set,Map,...globals});for(const name of names){const start=new RegExp('^  (?:async )?function '+name+'\\(', 'm').exec(source)?.index;assert.notEqual(start,undefined);const next=/\n  (?:async )?function |\n  (?:const|let) |\n  \$\(/g;next.lastIndex=start+3;const end=next.exec(source)?.index??source.length;vm.runInContext(source.slice(start,end),c);}return c;}
test('local mail mode states no external cost and does not claim arbitrary AI understanding',()=>{const c=load(['mailAnalysisDescription']);const copy=c.mailAnalysisDescription({analysis_mode:'local'});assert.match(copy.label,/추가 비용 없음/);assert.match(copy.description,/복잡한 형식.*직접 확인/);assert.doesNotMatch(copy.label,/AI/);assert.match(c.mailAnalysisDescription({unavailable:true}).description,/유지/);});
test('failed re-extraction cannot be presented as completed local analysis',()=>{const c=load(['documentAnalysisLabel'],{statusText:{active:'이전 결과 유지'}});assert.equal(c.documentAnalysisLabel({status:'active',latest_generation:{status:'failed',parse_version:'local-mail-v1'}}),'이전 결과 유지');assert.match(c.documentAnalysisLabel({status:'needs_review',latest_generation:{status:'active',parse_version:'local-mail-v1'}}),/기본 분석 완료.*확인 필요/);});
test('persisted queued/running document job blocks duplicate reprocessing after refresh',()=>{const state={reprocessing:new Set(),jobs:new Map([['j',{state:'running',files:[{document_id:'doc'}]}]])};const c=load(['documentProcessing'],{state});assert.equal(c.documentProcessing('doc'),true);assert.equal(c.documentProcessing('other'),false);state.jobs.get('j').state='failed';assert.equal(c.documentProcessing('doc'),false);state.reprocessing.add('doc');assert.equal(c.documentProcessing('doc'),true);});
test('unsupported/empty body errors give specific recovery actions',()=>{const c=load(['fileError']);assert.match(c.fileError('LOCAL_EXTRACTION_UNSUPPORTED'),/원문은 보관.*직접 입력/);assert.match(c.fileError('MAIL_BODY_EMPTY'),/본문.*첨부 파일/);assert.match(c.fileError('PROCESSING_FAILED'),/기존 예약은 유지/);});
test('capability reply cannot cross a changed user or trip scope',async()=>{const state={epoch:1,session:{authenticated:true},mailCapabilities:null};let resolve;const response=new Promise(r=>resolve=r);const c=load(['loadMailCapabilities'],{state,api:()=>response,renderMailCapabilities(){}});const pending=c.loadMailCapabilities();state.epoch=2;resolve({analysis_mode:'ai'});await pending;assert.equal(state.mailCapabilities,null);});

test('pending and cancellation review reasons stay explicit without claiming action',()=>{const c=load(['mailReviewReasonText']);assert.match(c.mailReviewReasonText(['REQUEST_NOT_CONFIRMATION']),/확정된 예약이 아니/);assert.match(c.mailReviewReasonText(['RESERVATION_CANCELLED_REVIEW']),/실제 취소 여부를 확인/);assert.equal(c.mailReviewReasonText(['BASIC_EXTRACTION_REVIEW','unknown']), '');});

test('time conflict view compares both representations and provides a correction path',()=>{function make(tag,cls='',text=''){return {tag,text,children:[],append(...v){this.children.push(...v)},setAttribute(){}}}const c=load(['renderBookingTimeConflicts'],{make});const body=make('div');c.renderBookingTimeConflicts(body,[{side:'start',summary:{date:'2026-11-06',time:'16:00'},event_local:'2026-11-06T15:00:00'}]);const content=n=>[n.text,...n.children.map(content)].join(' ');assert.match(content(body),/16:00/);assert.match(content(body),/15:00/);assert.match(content(body),/두 값을 맞추기 전에는 일정을 확정할 수 없/);});

test('retry and success clear a previous failure while retaining new errors',()=>{const state={uploads:[{document_id:'d',filename:'same.eml',state:'failed',error_code:'PROCESSING_FAILED',reason:'old failure',bookings_count:8,review_reasons:['DATE_NEEDS_REVIEW']}]};const c=load(['updateUpload'],{state});c.updateUpload({document_id:'d',state:'queued'});assert.equal(state.uploads[0].error_code,null);assert.equal(state.uploads[0].reason,null);assert.equal(state.uploads[0].bookings_count,null);c.updateUpload({document_id:'d',state:'needs_review',bookings_count:8});assert.equal(state.uploads[0].error_code,null);c.updateUpload({document_id:'d',state:'failed',error_code:'LOCAL_EXTRACTION_UNSUPPORTED'});assert.equal(state.uploads[0].error_code,'LOCAL_EXTRACTION_UNSUPPORTED');});

test('job result snapshots clear older attempts but retain same-job partial details',()=>{
  const state={uploads:[{document_id:'d',job_id:'old',state:'needs_review',bookings_count:8,review_reasons:['RESERVATION_CANCELLED_REVIEW'],error_code:'OLD_ERROR',analysis_mode:'local'}],jobs:new Map([['old',{created_at:'1'}]]),recommendations:{active:null}};
  const c=load(['updateUpload','mergeJob'],{state,terminal:j=>['succeeded','partial','failed','cancelled'].includes(j.state),renderJobs(){},renderUploads(){},renderDocuments(){}});
  // A retry can finish before polling ever observes queued/running.
  c.mergeJob({job_id:'new',created_at:'2',state:'succeeded',files:[{document_id:'d',state:'succeeded',bookings_count:2}]});
  assert.equal(state.uploads[0].bookings_count,2);assert.equal(state.uploads[0].error_code,null);assert.equal(state.uploads[0].review_reasons.length,0);assert.equal(state.uploads[0].analysis_mode,null);
  c.mergeJob({job_id:'newer',created_at:'3',state:'running',files:[{document_id:'d',state:'needs_review',bookings_count:1,review_reasons:['DATE_NEEDS_REVIEW'],analysis_mode:'local'}]});
  c.mergeJob({job_id:'newer',created_at:'3',state:'running',files:[{document_id:'d',state:'needs_review',bookings_count:1}]});
  assert.equal(state.uploads[0].review_reasons[0],'DATE_NEEDS_REVIEW');assert.equal(state.uploads[0].analysis_mode,'local');
  c.mergeJob({job_id:'newer',created_at:'3',state:'failed',files:[{document_id:'d',state:'failed',error_code:'LOCAL_EXTRACTION_UNSUPPORTED'}]});
  assert.equal(state.uploads[0].bookings_count,null);assert.equal(state.uploads[0].review_reasons.length,0);assert.equal(state.uploads[0].error_code,'LOCAL_EXTRACTION_UNSUPPORTED');
  c.mergeJob({job_id:'last',created_at:'4',state:'succeeded',files:[{document_id:'d',state:'succeeded',bookings_count:0,review_reasons:[]}]});
  assert.equal(state.uploads[0].bookings_count,0);assert.equal(state.uploads[0].error_code,null);
});

function node(tag,cls='',text=''){return {tag,className:cls,text,children:[],value:'',append(...v){this.children.push(...v)},replaceChildren(...v){this.children=v},setAttribute(){}};}
function content(n){return [n.text,...n.children.map(content)].join(' ');}
const marks={'항공':'↗','숙소':'⌂','렌터카':'↔','투어':'◎','음식점':'♧','기타':'◇'};
test('food filter includes parser aliases and cards display Korean without changing stored kinds',()=>{
  const nodes=Object.fromEntries(['bookingList','dateFilter','kindFilter','needsReview','bookingFilterBrief','bookingCount'].map(id=>['#'+id,node('div')]));nodes['#kindFilter'].value='음식점';
  const state={trip:{start_date:'2026-11-01',end_date:'2026-11-10'},bookings:[{id:'1',kind:'restaurant',provider:'Restaurant A',date:'2026-11-02'},{id:'2',kind:'음식점',provider:'Restaurant B',date:'2026-11-02'},{id:'3',kind:'hotel',provider:'Hotel',date:'2026-11-02'}]};
  const c=load(['bookingKindLabel','renderBookings'],{state,$:id=>nodes[id],renderBookingDays(){},isReview:()=>false,make:node,button:(text,action,cls)=>node('button',cls,text),kindMark:marks});
  c.renderBookings();assert.equal(nodes['#bookingCount'].textContent,'2 / 3건');assert.match(content(nodes['#bookingList']),/음식점/);assert.doesNotMatch(content(nodes['#bookingList']),/restaurant|hotel/);assert.equal(state.bookings[0].kind,'restaurant');assert.equal(c.bookingKindLabel('car-rental'),'렌터카');assert.equal(c.bookingKindLabel('unrecognized'),'unrecognized');assert.equal(c.bookingKindLabel('constructor'),'constructor');
});

test('editing an English-kind booking labels its option in Korean without silently rewriting kind',async()=>{
  let submit,payload;const inputs={},options={};const booking={id:'b',kind:'restaurant',provider:'Original name',status:'needs_review',version:2};const state={trip:{id:'t'},bookings:[]};
  const c=load(['bookingKindLabel','bookingForm'],{state,effective:x=>x,openDialog:(title,build)=>build(node('div')),make:node,kindMark:marks,formBase:(body,label,handler)=>{submit=handler;return {form:node('form'),grid:node('div'),footer:node('div')}},field:(grid,name,label,value,config)=>{options[name]=config;return inputs[name]={value}},api:async(path,request)=>{payload=request.body;return {id:'b'}},tripPath:()=>'/trips/t',closeDialog(){},loadBookings:async()=>{},notice(){}});
  c.bookingForm(booking);assert.equal(inputs.kind.value,'restaurant');assert.equal(options.kind.select.find(([value])=>value==='restaurant')[1],'음식점');inputs.provider.value='Corrected name';await submit();assert.equal(payload.changes.length,1);assert.equal(payload.changes[0].field_path,'provider');assert.equal(payload.changes[0].value,'Corrected name');assert.equal(booking.kind,'restaurant');
});


test('accepted documents stay busy before the first file result arrives',()=>{
  const state={reprocessing:new Set(),jobs:new Map([['j',{state:'running',submission:{accepted:[{document_id:'d'}]},files:[]}]])};
  const c=load(['documentProcessing'],{state});assert.equal(c.documentProcessing('d'),true);assert.equal(c.documentProcessing('other'),false);
  state.jobs.get('j').state='succeeded';assert.equal(c.documentProcessing('d'),false);
});

test('single failed file resumes its original job instead of paying for a new extraction',async()=>{
  const state={epoch:1,reprocessing:new Set(),jobs:new Map([['j',{job_id:'j',files:[{document_id:'d',state:'failed'}]}]])};let path,polled,fresh=0;
  const c=load(['retryUpload'],{state,canRetryJob:()=>true,documentProcessing:()=>false,renderDocuments(){},uid:()=> 'retry-id',
    api:async p=>{path=p;return {job_id:'next'}},pollJob:async id=>{polled=id},updateUpload(){},renderUploads(){},reprocessDocument:()=>{fresh++}});
  await c.retryUpload({document_id:'d',filename:'test.eml',job_id:'j'});
  assert.equal(path,'/jobs/j/retry');assert.equal(polled,'next');assert.equal(fresh,0);assert.equal(state.reprocessing.size,0);
});


test('refresh and retry show accepted pending mail before per-file completion',()=>{
  const state={uploads:[{document_id:'d',job_id:'old',state:'failed',error_code:'OLD'}],jobs:new Map([['old',{created_at:'1'}]]),recommendations:{active:null}};
  const c=load(['updateUpload','mergeJob'],{state,terminal:j=>['succeeded','partial','failed','cancelled'].includes(j.state),renderJobs(){},renderUploads(){},renderDocuments(){}});
  c.mergeJob({job_id:'new',created_at:'2',operation:'documents',state:'running',submission:{accepted:[{document_id:'d',filename:'test.eml'}]},files:[]});
  assert.equal(state.uploads[0].state,'running');assert.equal(state.uploads[0].error_code,null);assert.equal(state.uploads[0].job_id,'new');
  c.mergeJob({job_id:'new',created_at:'2',operation:'documents',state:'succeeded',files:[{document_id:'d',state:'succeeded',bookings_count:8}]});
  assert.equal(state.uploads[0].state,'succeeded');assert.equal(state.uploads[0].bookings_count,8);
});
