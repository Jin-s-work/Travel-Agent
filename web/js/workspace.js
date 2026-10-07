/* Small private-workspace interactions. No browser storage and no provider calls. */
(function (root, factory) {
  const value = factory();
  if (typeof module === 'object' && module.exports) module.exports = value;
  else root.WorkspaceUX = value;
})(typeof window === 'undefined' ? globalThis : window, function () {
  'use strict';
  const labels = {vegan:'비건', vegetarian:'채식', gluten_free:'글루텐 제외', nut_free:'견과류 제외', halal:'할랄', wheelchair_accessible:'휠체어 접근', step_free:'계단 없는 접근'};
  const copy = value => value == null ? value : JSON.parse(JSON.stringify(value));
  const parts = value => ({date:value?.slice(0,10)||null,time:value?.length>10?value.slice(11,16):null});
  const clock = value => value?.slice(0,5)||null;
  function conditionLabel(value) { return labels[value] || '기존 조건: '+value; }
  function requiredChoices(input, choices, ui) {
    const wrap=input.closest('.field'); if(!wrap)return;
    const selected=new Set(input.value.split(/[,，\n]/).map(v=>v.trim()).filter(Boolean));
    const group=ui.make('fieldset','discovery-check-group');
    const title=wrap.querySelector('label');group.append(ui.make('legend','',title?.textContent||'필수 조건'));
    const hint=wrap.querySelector('.hint'); if(hint?.id)group.setAttribute('aria-describedby',hint.id);
    for(const value of new Set([...choices,...selected])) {
      const row=ui.make('label','check'),check=ui.make('input');check.type='checkbox';check.checked=selected.has(value);check.value=value;
      if(hint?.id)check.setAttribute('aria-describedby',hint.id);
      check.addEventListener('change',()=>{input.value=[...group.querySelectorAll('input:checked')].map(n=>n.value).join(', ');input.dispatchEvent(new Event('input',{bubbles:true}));});
      row.append(check,ui.make('span','',conditionLabel(value)));group.append(row);
    }
    input.type='hidden';if(title)title.hidden=true;wrap.insertBefore(group,hint||input.nextSibling);
  }
  function summaryEvent(booking,side) {
    const events=booking.events||[],source=booking.extracted||booking.extracted_json||booking;
    const originals=source.events||events,key=side==='start'?'start_local':'end_local';
    const date=source[side==='start'?'date':'date_end'],time=source[side==='start'?'time':'time_end'];
    const matches=originals.filter(e=>(date||time)&&(!date||parts(e[key]).date===date)&&(!time||clock(parts(e[key]).time)===clock(time)));
    const id=(matches.length===1?matches[0]:originals.length===1?originals[0]:null)?.id;
    return events.find(e=>e.id===id)||null;
  }
  function bookingTimeProposal(booking,values,eventValues) {
    const summary=copy(values),events=copy(eventValues),changes=[],conflicts=[];
    for(const side of ['start','end']) {
      const dateKey=side==='start'?'date':'date_end',timeKey=side==='start'?'time':'time_end',localKey=side==='start'?'start_local':'end_local';
      const summaryChanged=(summary[dateKey]||null)!==(booking[dateKey]||null)||clock(summary[timeKey])!==clock(booking[timeKey]);
      const target=summaryEvent(booking,side),entered=target&&events.find(e=>e.id===target.id);
      const eventChanged=entered&&(entered[localKey]||null)!==(target[localKey]||null);
      if(summaryChanged&&booking.events?.length&&!target){conflicts.push({field:dateKey,message:'대표 시각에 대응하는 구간을 정할 수 없습니다. 아래에서 해당 구간을 직접 수정하고 대표값과 비교해 주세요.'});continue;}
      if(!target||!entered||(!summaryChanged&&!eventChanged))continue;
      const desired=summary[dateKey]?(summary[dateKey]+(summary[timeKey]?'T'+summary[timeKey]:'')):null;
      if(summaryChanged&&eventChanged&&((parts(entered[localKey]).date||null)!==(summary[dateKey]||null)||clock(parts(entered[localKey]).time)!==clock(summary[timeKey]))){conflicts.push({field:dateKey,message:'대표 시각과 해당 구간에 서로 다른 값을 입력했습니다. 적용할 시각을 하나로 맞춰 주세요.'});continue;}
      if(summaryChanged)entered[localKey]=desired;
      else {const p=parts(entered[localKey]);summary[dateKey]=p.date;summary[timeKey]=p.time;}
    }
    for(const key of ['date','date_end','time','time_end'])if((summary[key]||null)!==(booking[key]||null))changes.push({field_path:key,value:summary[key]||null});
    for(const event of events){const old=(booking.events||[]).find(e=>e.id===event.id);if(!old)continue;for(const key of ['start_local','end_local','start_timezone','end_timezone'])if((event[key]||null)!==(old[key]||null))changes.push({field_path:`events.${event.id}.${key}`,value:event[key]||null});}
    return {summary,events,changes,conflicts};
  }
  function bookingCorrection({booking,inputs,eventInputs,form,footer,make}) {
    const preview=make('section','booking-correction-preview');preview.setAttribute('aria-live','polite');form.insertBefore(preview,footer);
    let approved='';const submit=form.querySelector('[type="submit"]'),proposedFields=new Map();
    // A proposed counterpart is not a second user edit. Rebuild from the user's
    // inputs on every preview, even when the previous proposal is still visible.
    const enteredValue=input=>{const proposed=proposedFields.get(input);return proposed&&input.value===proposed.value?proposed.entered:input.value;};
    const read=()=>({summary:Object.fromEntries(Object.entries(inputs).map(([k,v])=>[k,enteredValue(v).trim()||null])),events:eventInputs.map(({event,inputs:fields})=>({id:event?.id,...Object.fromEntries(Object.entries(fields).map(([k,v])=>[k,enteredValue(v).trim()||null]))}))});
    form.addEventListener('input',event=>{proposedFields.delete(event.target);approved='';preview.replaceChildren();submit.textContent='수정 내용 확인';});
    // The summary is the single editor for a uniquely identified one-leg booking.
    if(eventInputs.length===1){for(const key of ['start_local','end_local'])eventInputs[0].inputs[key].closest('.field').hidden=true;}
    function prepare(){
      const current=read(),proposal=bookingTimeProposal(booking,current.summary,current.events);
      if(proposal.conflicts.length){const error=new Error('수정할 시각을 확인해 주세요.');error.details=proposal.conflicts;throw error;}
      const fingerprint=JSON.stringify(proposal);if(approved===fingerprint)return true;
      // Materialize both representations only after showing their exact proposed changes.
      function propose(input,value){const entered=enteredValue(input),next=value||'';if(next!==entered)proposedFields.set(input,{entered,value:next});else proposedFields.delete(input);input.value=next;}
      for(const key of ['date','date_end','time','time_end'])propose(inputs[key],proposal.summary[key]);
      for(const event of proposal.events){const row=eventInputs.find(e=>e.event?.id===event.id);if(row)for(const key of ['start_local','end_local'])propose(row.inputs[key],event[key]);}
      // Reverting the visible editor to the original also removes its old hidden
      // proposal, so a later save cannot accidentally retain the earlier time.
      if(!proposal.changes.length){approved='';preview.replaceChildren();return true;}
      approved=fingerprint;
      preview.replaceChildren(make('h3','','시각 수정 미리보기'),make('p','hint','원문을 보존하고 아래 대표값과 해당 구간에만 수정값을 저장합니다. 다른 구간과 출발·도착 시간대는 유지됩니다.'));
      const extracted=booking.extracted||booking.extracted_json||{};
      for(const change of proposal.changes){const match=/^events\.([^.]+)\.(.+)$/.exec(change.field_path),key=match?match[2]:change.field_path;
        const old=match?(booking.events||[]).find(e=>e.id===match[1])?.[key]:booking[key];const original=match?(extracted.events||[]).find(e=>e.id===match[1])?.[key]:extracted[key];
        const label=match?`구간 ${(booking.events||[]).findIndex(e=>e.id===match[1])+1} · ${{start_local:'시작',end_local:'종료',start_timezone:'출발 시간대',end_timezone:'도착 시간대'}[key]}`:({date:'대표 시작 날짜',date_end:'대표 종료 날짜',time:'대표 시작 시각',time_end:'대표 종료 시각'}[key]);
        const row=make('div','source-card');row.append(make('strong','',label),make('p','',`원문: ${original||'미확인'} · 현재 유효값: ${old||'미확인'}`),make('p','',`저장할 수정값: ${change.value||'미확인으로 비움'}`));preview.append(row);
      }
      submit.textContent='확인한 수정 저장';preview.scrollIntoView({block:'nearest'});return false;
    }
    submit.textContent='수정 내용 확인';return {prepare};
  }
  function insertionDay(exploreDate,explicitDate,tripStart) { return explicitDate||exploreDate||tripStart; }
  // Only fields changed since the initial screen was shown win over a late GET.
  // Arrays are one field, except selections: adding a place must not erase saved
  // places that had not reached the screen yet.
  function mergeInput(server,baseline,current){
    if(JSON.stringify(baseline)===JSON.stringify(current))return copy(server);
    const object=value=>value!=null&&typeof value==='object'&&!Array.isArray(value);
    if(!object(baseline)||!object(current))return copy(current);
    const result=object(server)?copy(server):{};
    for(const key of new Set([...Object.keys(baseline),...Object.keys(current)])){
      if(JSON.stringify(baseline[key])===JSON.stringify(current[key]))continue;
      if(!Object.hasOwn(current,key))delete result[key];else result[key]=mergeInput(result[key],baseline[key],current[key]);
    }
    return result;
  }
  function formValuesForDraft(draft,fallback){
    const values={},conditions=draft.conditions||{},filters=draft.filters||{};
    const filterValues={review_language_filter:filters.strict?'required':'all',rating_filter:filters.ratingEnabled?'required':'all','rating_filter.min_rating':filters.minRating,'rating_filter.min_count':filters.minCount,'review_language_filter.min_local_share':filters.minLocal==null?60:filters.minLocal*100,'review_language_filter.max_korean_share':filters.maxKorean==null?10:filters.maxKorean*100,ordering_profile:filters.ordering||'evidence',limit:filters.limit};
    for(const key of Object.keys(fallback)){
      const value=Object.hasOwn(filterValues,key)?filterValues[key]:key.split('.').reduce((value,part)=>value?.[part],conditions);
      values[key]=key==='party.children'?(value||[]).map(child=>child.age??'?').join(', '):Array.isArray(value)?value.join(', '):value==null?(['budget.currency','budget.basis','budget.period'].includes(key)?fallback[key]:''):String(value);
    }
    return values;
  }
  function mergeInitialInput(server,capture){
    const {baseline,current,conditionsBaseline}=capture;
    const result=mergeInput(server,baseline,current);
    const before=new Map((baseline.selected_places||[]).map(p=>[p.place_id,p])),after=new Map((current.selected_places||[]).map(p=>[p.place_id,p]));
    const selected=new Map((server.selected_places||[]).map(p=>[p.place_id,copy(p)]));
    for(const id of before.keys())if(!after.has(id))selected.delete(id);
    for(const [id,item] of after)if(!before.has(id)||JSON.stringify(before.get(id))!==JSON.stringify(item))selected.set(id,mergeInput(selected.get(id),before.get(id),item));
    result.selected_places=[...selected.values()];
    if(current.conditions_draft&&JSON.stringify(baseline.conditions_draft)!==JSON.stringify(current.conditions_draft)){
      const base=copy(baseline.conditions_draft||conditionsBaseline),remote=copy(server.conditions_draft||base),local=current.conditions_draft;
      const formBase=conditionsBaseline?.filters?.form_values;
      if(formBase&&local.filters?.form_values){
        base.filters??={};base.filters.form_values??=formBase;remote.filters??={};
        remote.filters.form_values={...formValuesForDraft(remote,formBase),...remote.filters.form_values};
      }
      result.conditions_draft=mergeInput(remote,base,local);
      if(formBase&&local.filters?.form_values){
        const before=formValuesForDraft(base,formBase),after=formValuesForDraft(local,formBase),merged=formValuesForDraft(result.conditions_draft,formBase);
        for(const key of Object.keys(formBase))if(before[key]!==after[key]&&local.filters.form_values[key]===formBase[key])result.conditions_draft.filters.form_values[key]=merged[key];
      }
    }
    return result;
  }
  function createDraftStore({request,snapshot,conditionsSnapshot=()=>null,restore,status,currentTrip,currentEpoch,delay=700}) {
    const records=new Map();let generation=0,restoring=false;
    const path=id=>'/trips/'+encodeURIComponent(id)+'/workspace-draft';
    function record(id){if(!records.has(id))records.set(id,{version:0,loaded:false,dirty:false,blocked:false,data:null,timer:null,pending:null,revision:0,capture:null,deferredCaptures:[]});return records.get(id);}
    function restoreInput(data,meta){restoring=true;try{restore(copy(data),meta);}finally{restoring=false;}}
    function prepare(){const id=currentTrip();if(!id||restoring)return;const entry=record(id);if(entry.capture?.epoch===currentEpoch())return;if(entry.capture?.dirty)entry.deferredCaptures.push(entry.capture);const baseline=copy(snapshot());clearTimeout(entry.timer);entry.capture={epoch:currentEpoch(),baseline,current:copy(baseline),conditionsBaseline:copy(conditionsSnapshot()),dirty:false};}
    function prepareConditionsForm(values){const capture=records.get(currentTrip())?.capture;if(!capture?.conditionsBaseline)return;const filters=capture.conditionsBaseline.filters??={};if(!filters.form_values)filters.form_values=copy(values);}
    function schedule(id,entry){clearTimeout(entry.timer);if(entry.loaded&&entry.dirty&&!entry.blocked&&!entry.capture)entry.timer=setTimeout(()=>save(id),delay);}
    function notify(id,message,error=false){if(currentTrip()===id)status(message,error);}
    async function save(id=currentTrip()){
      const entry=records.get(id);if(!entry?.loaded||!entry.dirty||entry.blocked||entry.capture)return;
      if(entry.data.selected_places?.length>30){notify(id,'저장된 선택과 새 선택을 모두 유지했어요. 초안을 저장하려면 일정 후보를 30곳 이하로 줄여 주세요.',true);return;}
      clearTimeout(entry.timer);if(entry.pending){await entry.pending;return save(id);}
      const revision=entry.revision,token=generation,body={expected_version:entry.version,...copy(entry.data)};
      notify(id,'초안을 저장하고 있어요.');
      entry.pending=(async()=>{try{const value=await request(path(id),{method:'PUT',body});if(token!==generation||records.get(id)!==entry)return;entry.version=value.version;entry.dirty=entry.revision!==revision;notify(id,entry.dirty?'새 입력을 저장하고 있어요.':'초안 저장됨 · 이 로그인에서 이어볼 수 있어요.');}
        catch(error){if(token!==generation||records.get(id)!==entry||error.status===401||error.name==='AbortError')return;entry.blocked=error.status===409;notify(id,entry.blocked?'다른 창에서 초안이 바뀌었습니다. 내 입력을 유지했어요. 최신 초안과 비교해 주세요.':'초안을 저장하지 못했어요. 이 화면의 입력은 유지됩니다. 다시 저장해 주세요.',true);}
        finally{entry.pending=null;}})();
      await entry.pending;if(entry.dirty&&!entry.blocked&&entry.revision!==revision)return save(id);
    }
    function changed(){const id=currentTrip();if(!id||restoring)return;const entry=record(id),data=snapshot();if(entry.capture){entry.capture.current=copy(data);entry.capture.dirty=JSON.stringify(data)!==JSON.stringify(entry.capture.baseline);return;}if(!entry.loaded||JSON.stringify(data)===JSON.stringify(entry.data))return;entry.data=copy(data);entry.dirty=true;entry.revision++;schedule(id,entry);}
    async function load(){const id=currentTrip(),epoch=currentEpoch(),token=generation;if(!id)return;prepare();const entry=record(id),capture=entry.capture;try{const value=await request(path(id));if(token!==generation||id!==currentTrip()||epoch!==currentEpoch()||records.get(id)!==entry||entry.capture!==capture)return;
      const server={context:value.context||{},selected_places:value.selected_places||[],conditions_draft:value.conditions_draft||null};
      if(entry.dirty&&value.version!==entry.version){entry.blocked=true;notify(id,'다른 창의 초안과 현재 입력이 달라요. 내 입력을 유지했습니다.',true);}
      const previous=entry.dirty?entry.data:server;
      if(!entry.dirty)entry.version=value.version;
      const captures=[...entry.deferredCaptures,capture].filter(value=>value.dirty);
      entry.data=captures.reduce((data,value)=>mergeInitialInput(data,value),previous);
      if(captures.length&&JSON.stringify(entry.data)!==JSON.stringify(previous)){entry.dirty=true;entry.revision++;}
      entry.deferredCaptures=[];
      entry.capture=null;entry.loaded=true;
      restoreInput(entry.data,{saved:entry.dirty||value.version>0&&!value.expired});
      if(!entry.blocked)notify(id,captures.length?'새 입력을 유지하고 저장된 초안의 나머지를 이어왔어요.':value.expired?'이전 초안의 보관 기간이 끝났어요. 저장한 예약과 일정은 유지됩니다.':value.version?'저장한 탐색과 선택을 이어왔어요.':'');
      schedule(id,entry);
    }catch(error){if(token!==generation||id!==currentTrip()||epoch!==currentEpoch()||error.status===401)return;notify(id,'초안을 불러오지 못했어요. 연결을 확인한 뒤 다시 불러와 주세요.',true);}}
    async function latest(){const id=currentTrip();if(!id)return null;const epoch=currentEpoch(),token=generation;const value=await request(path(id));return token===generation&&id===currentTrip()&&epoch===currentEpoch()?value:null;}
    function useLatest(value){const id=currentTrip();if(!id)return;const entry=record(id);clearTimeout(entry.timer);entry.version=value.version;entry.dirty=false;entry.blocked=false;entry.loaded=true;entry.capture=null;entry.deferredCaptures=[];entry.data={context:value.context||{},selected_places:value.selected_places||[],conditions_draft:value.conditions_draft||null};restoreInput(entry.data,{saved:value.version>0&&!value.expired});notify(id,value.expired?'초안 보관 기간이 끝났어요. 저장된 일정에서 다시 이어갈 수 있습니다.':'최신 초안을 불러왔어요.');}
    async function keepCurrent(value){const entry=record(currentTrip());entry.version=value.version;entry.blocked=false;entry.loaded=true;entry.capture=null;entry.deferredCaptures=[];entry.data=copy(snapshot());entry.dirty=true;entry.revision++;await save();}
    function clear(){generation++;for(const entry of records.values())clearTimeout(entry.timer);records.clear();}
    function forget(id){clearTimeout(records.get(id)?.timer);records.delete(id);}
    return {prepare,prepareConditionsForm,changed,save,load,latest,useLatest,keepCurrent,clear,forget,isCollecting:()=>Boolean(records.get(currentTrip())?.capture),isReady:()=>Boolean(records.get(currentTrip())?.loaded)};
  }
  let ui,store,pendingScroll=null,returnLocator=null;
  function formValues(form){return Object.fromEntries([...form.querySelectorAll('input[data-field],select[data-field],textarea[data-field]')].filter(n=>n.type!=='checkbox'&&(n.type!=='radio'||n.checked)).map(n=>[n.dataset.field,n.value]));}
  function captureConditionsForm(form){if(!ui)return;ui.state.workspaceConditionForm=formValues(form);changed();}
  function restoreConditionsForm(form,{announce=true}={}){store?.prepareConditionsForm(formValues(form));const values=ui?.state.workspaceConditionForm;if(!values)return;for(const input of form.querySelectorAll('[data-field]')){if(Object.hasOwn(values,input.dataset.field)){if(input.type==='radio')input.checked=input.value===values[input.dataset.field];else if(input.type!=='checkbox')input.value=values[input.dataset.field];}}for(const input of form.querySelectorAll('input[type="hidden"][data-field]')){const selected=input.value.split(/[,，\n]/).map(v=>v.trim());for(const check of input.closest('.field')?.querySelectorAll('input[type="checkbox"]')||[])check.checked=selected.includes(check.value);}if(announce)ui.notice('이전에 입력하던 조건을 이어왔어요. 확인한 뒤 적용해 주세요.');}
  function dialogClosed(){if(ui?.state.workspaceConditionForm&&document.querySelector('#dialogBody .discovery-filter-form')){ui.state.workspaceConditionForm=null;changed();}}
  function draftSnapshot(includeConditions=false){const s=ui.state,d=s.discovery;return {context:{tab:s.tab,...(window.DiscoveryExperience?{discovery_mode:window.DiscoveryExperience.filters().discoveryMode}:{}),explore_view:d.view==='recommend'?'recommendations':d.view||'recommendations',visit_date:d.draft?.conditions?.visit?.date||d.conditions?.conditions?.visit?.date||null,categories:d.draft?.conditions?.categories||[],recommendation_types:d.draft?.conditions?.recommendation_types||[],scroll_by_tab:s.workspaceScroll||{},return_context:s.workspaceReturn||{}},selected_places:[...s.itineraries.selected.values()].map(p=>({place_id:p.place_id,duration_minutes:Number.isInteger(p.duration_minutes)&&p.duration_minutes>=5&&p.duration_minutes<=720?p.duration_minutes:60,duration_origin:p.duration_origin||'default',selection_run_id:p.selection_run_id||null})),conditions_draft:(includeConditions||d.dirty||s.workspaceConditionForm)&&d.draft?{...copy(d.draft),base_conditions_version:d.conditions?.version,base_trip_version:s.trip?.version,filters:{...ui.filters(),...(s.workspaceConditionForm?{form_values:s.workspaceConditionForm}:{})}}:null};}
  function restoreDraft(value,{saved=true}={}){const s=ui.state,context=value.context||{},draft=value.conditions_draft;
    s.workspaceScroll=context.scroll_by_tab||{};s.workspaceReturn=context.return_context||{};
    s.workspaceConditionForm=draft?.filters?.form_values||null;
    if(draft){s.discovery.draft={conditions:copy(draft.conditions),overrides:copy(draft.overrides||{}),stop_id:draft.stop_id||null};s.discovery.dirty=true;s.recommendations.conditionsDraft=true;if(draft.filters)ui.restoreFilters(draft.filters);if(draft.base_conditions_version!==s.discovery.conditions?.version||draft.base_trip_version!==s.trip?.version)ui.notice('저장한 초안 이후 여행 조건이 바뀌었어요. 입력을 유지했습니다. 날짜·인원·출발점을 확인하고 적용해 주세요.');}
    else {s.discovery.draft=null;s.discovery.dirty=false;s.recommendations.conditionsDraft=false;}
    if(context.discovery_mode)window.DiscoveryExperience?.restoreMode(context.discovery_mode);
    const pool=ui.candidates();s.itineraries.selected=new Map((value.selected_places||[]).map(item=>[item.place_id,{...pool.get(item.place_id),...item,name:pool.get(item.place_id)?.name||'저장한 선택 장소'}]));s.itineraries.selectionRestored=saved;
    if(s.workspaceReturn.itinerary_day)s.itineraries.day=s.workspaceReturn.itinerary_day;
    ui.render();const form=document.querySelector('#dialogBody .discovery-filter-form');if(form){restoreConditionsForm(form,{announce:false});form.dispatchEvent(new Event('workspace-restored'));}if(context.explore_view)ui.setExploreView(context.explore_view==='saved'?'saved':'recommend');if(['trip','explore','itinerary','mail','today','preparation','product','ask','reviews','settings'].includes(context.tab))ui.setTab(context.tab);
  }
  function status(message,error){const host=document.querySelector('#workspaceDraftStatus');if(!host||!ui)return;host.replaceChildren();host.hidden=!message;if(!message)return;host.append(ui.make('span','hint',message));if(error){host.append(ui.button(store.isReady()?'초안 비교 · 다시 저장':'초안 다시 불러오기',async()=>{try{if(!store.isReady()){await store.load();return;}const latest=await store.latest();if(!latest)return;ui.openDialog('저장된 초안과 비교',body=>{const current=draftSnapshot(),describe=d=>`${d.context?.visit_date||'날짜 미선택'} · 선택 장소 ${d.selected_places?.length||0}곳 · ${d.conditions_draft?'변경 중인 조건 있음':'저장된 조건 사용'}`;body.append(ui.make('p','form-note','다른 창의 내용을 자동으로 덮어쓰지 않습니다. 이어갈 초안을 선택해 주세요.'),ui.make('p','','서버 최신: '+describe(latest)),ui.make('p','','현재 입력: '+describe(current)),ui.button('최신 초안으로 바꾸기',()=>{ui.closeDialog();store.useLatest(latest);},'secondary'),ui.button('현재 입력으로 저장',async()=>{ui.closeDialog();await store.keepCurrent(latest);},'primary'));});}catch(e){ui.fail(e);}},'text-button'));}}
  function init(value){ui=value;store=createDraftStore({request:ui.api,snapshot:draftSnapshot,conditionsSnapshot:()=>draftSnapshot(true).conditions_draft,restore:restoreDraft,status,currentTrip:()=>ui.state.trip?.id,currentEpoch:()=>ui.state.epoch});
    const host=ui.make('div','workspace-draft-status');host.id='workspaceDraftStatus';host.hidden=true;host.setAttribute('role','status');document.querySelector('#main').prepend(host);
    window.addEventListener('scroll',()=>{if(!ui.state.trip||pendingScroll||ui.state.workspaceSuspended||!store.isReady())return;ui.state.workspaceScroll??={};ui.state.workspaceScroll[ui.state.tab]=Math.max(0,Math.round(window.scrollY));store.changed();},{passive:true});
    window.addEventListener('wheel',()=>{pendingScroll=null;},{passive:true});window.addEventListener('touchstart',()=>{pendingScroll=null;},{passive:true});
    document.addEventListener('visibilitychange',()=>{if(document.hidden){changed();store.save();}});
  }
  function changed(){if(!ui?.state.workspaceSuspended||store?.isCollecting())store?.changed();}
  async function leave(){if(!store)return;changed();await store.save();}
  function beforeTab(){if(!ui?.state.trip||ui.state.workspaceSuspended)return;ui.state.workspaceScroll??={};ui.state.workspaceScroll[ui.state.tab]=Math.max(0,Math.round(window.scrollY));}
  function afterRender(){if(!ui||!pendingScroll)return;const {epoch,tab,top}=pendingScroll,s=ui.state;if(epoch!==s.epoch||tab!==s.tab){pendingScroll=null;return;}if(tab==='explore'&&!s.discovery.loaded||tab==='itinerary'&&!s.itineraries.loaded)return;pendingScroll=null;window.scrollTo({top,behavior:'instant'});}
  function afterTab(){if(!ui)return;const state=ui.state;pendingScroll={epoch:state.epoch,tab:state.tab,top:state.workspaceScroll?.[state.tab]||0};requestAnimationFrame(()=>{if(!pendingScroll)return;document.querySelector('#main')?.focus({preventScroll:true});afterRender();});changed();}
  function rememberFocus(element){if(!element)return;const card=element.closest('[data-bookmark-id],[data-place-id],[data-item-id],[data-workspace-focus]');returnLocator={id:element.id||null,card:card?Object.entries(card.dataset).find(([key])=>['bookmarkId','placeId','itemId','workspaceFocus'].includes(key)):null,text:element.textContent};}
  function restoreFocus(element){if(element?.isConnected){element.focus({preventScroll:true});return;}let target=returnLocator?.id&&document.getElementById(returnLocator.id);if(!target&&returnLocator?.card){const [key,value]=returnLocator.card;const attribute='data-'+key.replace(/[A-Z]/g,c=>'-'+c.toLowerCase());const card=[...document.querySelectorAll('['+attribute+']')].find(n=>n.getAttribute(attribute)===value);target=card?.matches('button')?card:[...card?.querySelectorAll('button')||[]].find(n=>n.textContent===returnLocator.text)||card?.querySelector('button');}(target||document.querySelector('#main'))?.focus({preventScroll:true});}
  return {conditionLabel,requiredChoices,summaryEvent,bookingTimeProposal,bookingCorrection,insertionDay,createDraftStore,init,changed,leave,beforeTab,afterTab,afterRender,rememberFocus,restoreFocus,captureConditionsForm,restoreConditionsForm,dialogClosed,prepare:()=>store?.prepare(),load:()=>store?.load(),clear:()=>{store?.clear();pendingScroll=null;returnLocator=null;if(ui){ui.state.workspaceScroll={};ui.state.workspaceReturn={};ui.state.workspaceConditionForm=null;status('');}},forget:id=>store?.forget(id)};
});
