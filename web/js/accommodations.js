/* Private, trip-scoped accommodation controls. No browser storage or automatic provider calls. */
(() => {
  'use strict';
  let ui, items=[], suggestions=[], provider={}, context=null, epoch=0, serial=0, contextSerial=0, lastContext='', timer=null, loading=false;
  const pending=new Set();
  const labels={unresolved:'위치 미확인',candidates:'지점 선택 필요',confirmed:'지점 확인됨',needs_reconfirmation:'지점 재확인 필요',unavailable:'현재 위치 확인 불가'};
  const reasons={ACCOMMODATION_NOT_ADDED:'숙소를 추가하면 그날의 출발점으로 사용할 수 있어요.',ACCOMMODATION_LOCATION_UNKNOWN:'숙소는 저장됐어요. 정확한 지점을 확인하면 거리도 볼 수 있어요.',ACCOMMODATION_OVERLAP:'이 날짜에 숙소가 겹쳐요. 이번 방문의 출발점을 골라 주세요.',ACCOMMODATION_GAP:'이 날짜에 해당하는 숙소가 없어요. 다른 출발점을 선택하거나 일반 탐색을 계속하세요.',STAY_DATES_UNKNOWN:'숙박 날짜를 확인해 주세요.',CHECKOUT_TIME_OR_SELECTION_REQUIRED:'체크아웃 당일이에요. 출발 시각을 확인하거나 이번 방문의 숙소를 직접 골라 주세요.',ACCOMMODATION_COORDINATES_EXPIRED:'숙소 위치 자료가 만료됐어요. 지점을 다시 확인해 주세요.',ORIGIN_VERSION_CHANGED:'숙소가 바뀌었어요. 출발점을 다시 골라 주세요.',SELECTED_ACCOMMODATION_NOT_APPLICABLE:'선택한 숙소가 이 날짜·도시 구간에 해당하지 않아요.',ORIGIN_EXPLICITLY_UNSET:'출발점 없이 탐색합니다. 이동 시간은 미확인입니다.'};
  function active(){return ui?.state.trip;}
  function path(trip=active()){return ui.tripPath(trip)+'/accommodations';}
  function clear(){epoch++;serial++;contextSerial++;clearTimeout(timer);timer=null;items=[];suggestions=[];provider={};context=null;lastContext='';loading=false;pending.clear();render();}
  async function load(){
    if(!active())return;const trip=active(),e=epoch,s=++serial;loading=true;
    try{const data=await ui.api(path(trip));if(e!==epoch||s!==serial)return;items=data.items;suggestions=data.booking_suggestions;provider=data.provider;loading=false;render();await contextChanged(true);await recoverJobs();}
    catch(error){if(e!==epoch)return;loading=false;ui.fail(error);render();}
  }
  async function recoverJobs(){
    const e=epoch;let completed=false;for(const id of new Set(items.map(x=>x.job_id).filter(Boolean))){try{const job=await ui.api('/jobs/'+encodeURIComponent(id));if(e!==epoch)return;if(['queued','running'].includes(job.state))pending.add(id);else{pending.delete(id);completed=true;}}catch(error){if(error.status===401)ui.fail(error);}}
    if(completed&&e===epoch){const data=await ui.api(path());if(e!==epoch)return;items=data.items;render();await contextChanged(true);}
    if(pending.size&&!timer)timer=setTimeout(poll,1200);
  }
  async function poll(){timer=null;if(!active()||!pending.size)return;await load();}
  async function contextChanged(force=false){
    const draft=ui?.ensureDiscoveryDraft();if(!active()||!draft?.conditions?.visit?.date)return;
    const selection=draft.conditions.origin_selection||{kind:'automatic'};
    if(selection.kind==='manual'||selection.kind==='none'){
      contextSerial++;lastContext='';
      const point=draft.conditions.origin;const valid=point&&Number.isFinite(point.latitude)&&Number.isFinite(point.longitude)&&Math.abs(point.latitude)<=90&&Math.abs(point.longitude)<=180;context={status:selection.kind==='none'?'missing':valid?'ready':'unresolved',origin:selection.kind==='none'?null:point,reason_codes:selection.kind==='none'?['ORIGIN_EXPLICITLY_UNSET']:valid?[]:['ACCOMMODATION_LOCATION_UNKNOWN']};render();return;
    }
    const query=new URLSearchParams({visit_date:draft.conditions.visit.date});if(draft.stop_id)query.set('stop_id',draft.stop_id);if(draft.conditions.visit.local_time)query.set('local_time',draft.conditions.visit.local_time);if(selection.accommodation_id){query.set('accommodation_id',selection.accommodation_id);if(selection.expected_version!=null)query.set('expected_version',selection.expected_version);}
    const key=active().id+query.toString();if(!force&&lastContext===key){render();return;}lastContext=key;const s=++contextSerial,e=epoch;
    try{const value=await ui.api(ui.tripPath()+'/origin-context?'+query);if(e!==epoch||s!==contextSerial)return;context=value;render();}
    catch(error){if(e!==epoch||s!==contextSerial)return;context={status:'unknown',reason_codes:[],origin:null,error:'출발점을 확인하지 못했어요. 다시 확인하거나 일반 탐색을 계속하세요.'};lastContext='';render();if(error.status===401)ui.fail(error);}
  }
  function mutateDraft(values){const draft=ui.ensureDiscoveryDraft();if(!draft)return;for(const [key,value] of Object.entries(values)){draft.conditions[key]=structuredClone(value);draft.overrides[key]=structuredClone(value);}ui.draftChanged();}
  function dinner(){
    const draft=ui.ensureDiscoveryDraft();if(!draft)return;mutateDraft({meal_time:'dinner',prefer_nearby:true,visit:{...draft.conditions.visit,local_time:'18:00'}});ui.setTab('explore');
    ui.notice(context?.status==='ready'?'저녁·가까운 곳 우선으로 준비했어요. 날짜와 출발점을 확인하고 추천 보기를 눌러 주세요.':'저녁 조건을 준비했어요. 숙소 위치는 미확인입니다. 일반 탐색도 계속할 수 있어요.');
  }
  function render(){
    if(!ui)return;
    for(const selector of ['#homeOrigin','#exploreOrigin']){
      const host=document.querySelector(selector);if(!host)continue;const prior=host.contains(document.activeElement)?document.activeElement.dataset.action:null;host.replaceChildren();host.hidden=!active();if(!active())continue;
      const {make,button}=ui;const top=make('div','stay-summary-row'),copy=make('div','stay-summary-copy');copy.append(make('p','eyebrow','방문일의 출발점'));
      const title=context?.status==='ready'&&context.origin?context.origin.label:items.length?'숙소 저장됨 · 위치 확인 필요':'숙소를 기준으로 가볍게 탐색';
      copy.append(make('h3','',title));const detail=context?.error||reasons[context?.reason_codes?.[0]]||(context?.status==='ready'?'이 출발점으로 거리와 이동을 확인합니다. 숙박 예약 완료를 뜻하지는 않아요.':loading?'저장된 숙소를 불러오는 중…':'이름이나 지도 링크만 저장하세요. 날짜는 여행에서 가져옵니다.');copy.append(make('p','hint',detail));
      const actions=make('div','actions');const manage=button(items.length?`숙소 관리 (${items.length})`:'숙소 추가',()=>items.length?manager():editor(),'secondary');manage.dataset.action='manage';actions.append(manage);const fast=button('숙소 근처 저녁',dinner,'primary');fast.dataset.action='dinner';actions.append(fast);top.append(copy,actions);host.append(top);
      const draft=ui.ensureDiscoveryDraft();if(selector==='#exploreOrigin'&&draft){
        const controls=make('div','stay-explore-controls');
        if(items.length){const label=make('label','','이번 방문의 출발점');const select=make('select');select.dataset.action='origin';select.setAttribute('aria-label','이번 방문의 출발점');select.append(new Option('날짜에 맞는 숙소 자동 선택','automatic'));for(const stay of items.filter(x=>!draft.stop_id||x.stop_id===draft.stop_id))select.append(new Option(`${stay.display_name} · ${labels[stay.identity_state]}`,stay.id));select.append(new Option('출발점 없이 탐색','none'));if(draft.conditions.origin_selection?.kind==='manual')select.append(new Option('직접 입력한 출발점','manual'));select.value=draft.conditions.origin_selection?.accommodation_id||draft.conditions.origin_selection?.kind||'automatic';select.addEventListener('change',()=>{const stay=items.find(x=>x.id===select.value);mutateDraft({origin_selection:stay?{kind:'accommodation',accommodation_id:stay.id,expected_version:stay.version}:{kind:select.value}});});label.append(select);controls.append(label);}
        const filterLabel=make('label','','거리 조건');const filter=make('select');filter.dataset.action='distance';filter.setAttribute('aria-label','거리 조건');const options=[['none','거리 제한 없음'],['straight_line','직선거리 1km 이내'],['walking','도보 15분 이내 · 경로 확인 필요']];for(const [value,label]of options)filter.append(new Option(label,value));const current=draft.conditions.distance_filter;if(current&&(current.kind==='straight_line'&&current.max_distance_m!==1000||current.kind==='walking'&&current.max_duration_minutes!==15))filter.append(new Option(current.kind==='walking'?`도보 ${current.max_duration_minutes}분 이내`:`직선거리 ${current.max_distance_m}m 이내`,'custom'));filter.value=current?(current.kind==='straight_line'&&current.max_distance_m!==1000||current.kind==='walking'&&current.max_duration_minutes!==15?'custom':current.kind):'none';filter.addEventListener('change',()=>{if(filter.value==='custom')return;mutateDraft({distance_filter:filter.value==='none'?null:filter.value==='walking'?{kind:'walking',max_duration_minutes:15}:{kind:'straight_line',max_distance_m:1000}});});filterLabel.append(filter);controls.append(filterLabel);host.append(controls);
        if(draft.conditions.radius_m!=null)host.append(make('p','hint',`기존 직선 반경 ${draft.conditions.radius_m}m 조건도 유지 중입니다. 변경은 필터에서 할 수 있어요.`));
        if(draft.conditions.prefer_nearby)host.append(make('p','stay-draft-note','가까운 곳 우선'+(draft.conditions.meal_time==='dinner'?' · 저녁 18:00':'')+(ui.state.discovery.dirty?' · 추천 보기를 누르면 적용':' · 현재 추천 조건')));
      }
      if(prior)host.querySelector(`[data-action="${prior}"]`)?.focus({preventScroll:true});
    }
  }
  function editor(stay=null,suggestion=null){
    const trip=active();if(!trip)return;if(!trip.stops?.length){ui.notice('여행 정보에서 도시 구간을 먼저 추가해 주세요. 일반 장소 보관함은 계속 사용할 수 있어요.');return;}const e=epoch;ui.openDialog(stay?'숙소 수정':'숙소 추가',body=>{
      const {make,field}=ui;let input,stop,start,end,checkin,checkout,note,confirmed;
      if(!stay&&suggestions.length&&!suggestion){const list=make('div','stay-booking-suggestions');list.append(make('p','hint','예약에서 찾은 숙소 · 직접 고르면 입력을 가져와요'));for(const s of suggestions)list.append(ui.button(s.display_name||'숙박 예약',()=>editor(null,s),'secondary'));body.append(list);}
      const d=ui.ensureDiscoveryDraft();const initialStop=trip.stops.find(x=>x.id===(stay?.stop_id||d?.stop_id))||trip.stops[0];
      const {form,grid,footer}=ui.formBase(body,stay?'변경 저장':'숙소 저장',async()=>{
        const raw=input.value.trim(),selectedStop=trip.stops.find(s=>s.id===stop.value);const payload={stop_id:stop.value,input_kind:suggestion?'booking':/^https?:\/\//i.test(raw)?'map_url':'name',input_value:raw,booking_id:suggestion?.booking_id||stay?.booking_id||null,private_note:note.value,checkin_date:start.value||null,checkout_date:end.value||null,checkin_time:checkin.value||null,checkout_time:checkout.value||null,facility_timezone:selectedStop.timezone,dates_confirmed:confirmed.checked};if(stay)payload.expected_version=stay.version;
        if(end.value&&start.value&&end.value<=start.value){end.setCustomValidity('체크아웃은 체크인 다음 날짜부터 선택해 주세요.');end.reportValidity();end.addEventListener('input',()=>end.setCustomValidity(''),{once:true});return;}
        await ui.api(path(trip)+(stay?'/'+encodeURIComponent(stay.id):''),{method:stay?'PATCH':'POST',body:payload});if(e!==epoch)return;ui.closeDialog(true);await load();ui.notice(stay?'숙소를 수정했어요. 이전 추천의 출발점은 다시 확인해 주세요.':'숙소를 저장했어요. 정확한 지점 확인은 별도로 할 수 있어요.');manager();
      });
      input=field(grid,'input_value','숙소 이름 또는 지도 링크',stay?.input_value||suggestion?.display_name||'',{required:!suggestion,wide:true,maxLength:2000,placeholder:'예: 호텔 이름, Google 지도 링크'});
      const details=make('details','stay-more');details.append(make('summary','','숙박 날짜·메모 확인'));const more=make('div','form-grid');details.append(more);form.insertBefore(details,footer);
      stop=field(more,'stop_id','도시 구간',initialStop?.id,{select:trip.stops.map(x=>[x.id,`${x.city} · ${x.start_date} ~ ${x.end_date}`]),required:true,wide:true});
      start=field(more,'checkin_date','체크인 날짜',stay?.checkin_date||suggestion?.checkin_date||initialStop?.start_date||'',{type:'date'});end=field(more,'checkout_date','체크아웃 날짜',stay?.checkout_date||suggestion?.checkout_date||initialStop?.end_date||'',{type:'date'});
      const confirmation=make('label','stay-confirmation');confirmed=make('input');confirmed.type='checkbox';confirmed.checked=Boolean(stay?.dates_confirmed);confirmation.append(confirmed,make('span','','실제 체크인·체크아웃 날짜를 확인했어요'));more.append(confirmation);
      checkin=field(more,'checkin_time','체크인 시각 · 알 때만',stay?.checkin_time||'',{type:'time'});checkout=field(more,'checkout_time','체크아웃 시각 · 알 때만',stay?.checkout_time||'',{type:'time'});note=field(more,'private_note','나만 보는 메모',stay?.private_note||'',{textarea:true,wide:true,maxLength:2000});
      for(const el of [start,end])el.addEventListener('input',()=>{confirmed.checked=false;});
      stop.addEventListener('change',()=>{const value=trip.stops.find(x=>x.id===stop.value);if(!stay){start.value=value.start_date;end.value=value.end_date;confirmed.checked=false;}});
      body.prepend(make('p','hint',`${initialStop?.start_date||''} ~ ${initialStop?.end_date||''} · 여행의 날짜를 제안합니다. 예약 완료나 실제 투숙 날짜로 확정하지 않아요.`));
    });
  }
  async function manager(){
    const e=epoch;if(!active())return;await load();if(e!==epoch||!active())return;ui.openDialog('여행 숙소',body=>{const {make,button}=ui;body.append(make('p','hint','숙소 저장 → 정확한 지점 선택. 예약 완료 여부는 예약 화면에서 따로 확인해요.'));
      if(!provider.enabled)body.append(make('p','form-note','지도 제공자 OFF · 이름과 날짜는 저장할 수 있어요. 위치가 확인되기 전에는 거리·도보시간을 미확인으로 표시합니다.'));
      for(const stay of items){const card=make('article','stay-card');card.append(make('h3','',stay.display_name),make('p','stay-state',labels[stay.identity_state]),make('p','hint',`${stay.checkin_date||'체크인 미확인'} → ${stay.checkout_date||'체크아웃 미확인'} · ${stay.dates_confirmed?'날짜 직접 확인':'날짜 제안'}`));if(stay.identity?.address)card.append(make('p','',stay.identity.address));const actions=make('div','actions');if(stay.identity_state==='candidates')actions.append(button('지점 후보 비교·선택',()=>candidates(stay),'primary'));else{const resolve=button(stay.identity_state==='confirmed'?'지점 다시 확인':'정확한 지점 찾기',()=>resolveDialog(stay),'secondary');resolve.disabled=!provider.enabled||pending.has(stay.job_id);if(pending.has(stay.job_id))resolve.textContent='지점 확인 중…';actions.append(resolve);}actions.append(button('수정',()=>editor(stay),'quiet'),button('삭제',()=>remove(stay),'quiet'));card.append(actions);body.append(card);}
      body.append(button('＋ 숙소 추가',()=>editor(),'primary'));if(pending.size)body.append(make('p','hint','다른 화면으로 이동해도 작업이 이어집니다. 잠시 후 숙소 관리를 다시 열어 결과를 확인하세요.'));
    });
  }
  function remove(stay){ui.confirmAction('이 숙소를 삭제할까요?','숙소의 개인 입력을 지웁니다. 연결된 예약과 외부 예약은 유지되며, 이전 추천의 이동 근거는 다시 확인해야 합니다.','숙소 삭제',async()=>{await ui.api(path()+'/'+encodeURIComponent(stay.id)+'?expected_version='+stay.version,{method:'DELETE'});await load();ui.notice('숙소를 삭제했어요. 외부 예약은 변경하지 않았습니다.');});}
  function resolveDialog(stay){const e=epoch;ui.openDialog('정확한 숙소 지점 찾기',body=>{body.append(ui.make('p','',stay.display_name),ui.make('p','form-note',provider.disclosure));let consent;const {grid}=ui.formBase(body,'지점 후보 찾기',async()=>{const result=await ui.api(path()+'/'+encodeURIComponent(stay.id)+'/resolve',{method:'POST',headers:{'Idempotency-Key':key},body:{expected_version:stay.version,consent_to_provider:consent.checked,max_candidates:5}});if(e!==epoch)return;pending.add(result.job_id);ui.closeDialog(true);ui.notice('숙소는 저장된 상태로 지점 확인을 시작했어요. 다른 일을 계속할 수 있어요.');await load();});const key=crypto.randomUUID();const label=ui.make('label','stay-confirmation');consent=ui.make('input');consent.type='checkbox';consent.required=true;label.append(consent,ui.make('span','','숙소 이름·링크와 도시를 지도 제공자에게 보내 지점을 확인합니다.'));grid.append(label);});}
  function candidates(stay){const e=epoch;ui.openDialog('같은 이름의 지점을 확인해 주세요',body=>{body.append(ui.make('p','hint','원어명·주소·도시가 일치하는 지점을 직접 골라 주세요. 첫 번째 결과를 자동 선택하지 않습니다.'));const errors=ui.make('p','error');errors.setAttribute('role','alert');for(const c of stay.candidates||[]){const card=ui.make('article','stay-card');card.append(ui.make('h3','',c.native_name||c.name||c.display_name),ui.make('p','',c.address||'주소 미확인'),ui.make('p','hint',`${c.city||stay.city} · ${c.provider_place_id||'외부 ID 미확인'}`));for(const [label,url]of [['지도에서 지점 확인',c.map_url||c.canonical_url],['공식 사이트',c.official_url]]){ui.safeDiscoveryLink(card,url,label);}if(c.usage_permission?.attribution)card.append(ui.make('p','hint','자료: '+c.usage_permission.attribution));if(c.checked_at)card.append(ui.make('p','hint','확인: '+new Date(c.checked_at).toLocaleDateString('ko-KR')));const choose=ui.button('이 지점 선택',async()=>{choose.disabled=true;try{await ui.api(path()+'/'+encodeURIComponent(stay.id)+'/select',{method:'POST',body:{expected_version:stay.version,candidate_id:c.candidate_id}});if(e!==epoch)return;ui.closeDialog(true);await load();ui.notice('숙소 지점을 선택했어요. 해당 날짜의 출발점에 반영했습니다.');}catch(error){if(e===epoch)ui.fail(error,errors);}finally{choose.disabled=false;}},'primary');card.append(choose);body.append(card);}body.append(errors,ui.button('숙소 입력 수정',()=>editor(stay),'secondary'));});}
  window.AccommodationTools={init(value){ui=value;},load,clear,render,contextChanged,dinner};
})();
