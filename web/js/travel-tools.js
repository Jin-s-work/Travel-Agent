/* Supplemental private views. Server scope and expected versions remain authoritative. */
(() => {
  'use strict';
  let c, serial=0, features={}, tasks=[];
  const statuses={needs_confirmation:'확인 필요',waiting_open:'오픈 대기',in_progress:'진행 중',user_completed:'사용자 완료',evidence_verified:'증빙 확인',cancelled:'취소'};
  const $=s=>document.querySelector(s);
  const path=()=>'/trips/'+encodeURIComponent(c.state.trip.id);
  const reasonNames={RULE_UNCONFIRMED:'공식 예약 오픈 규칙 미확인',RULE_EVIDENCE_UNAVAILABLE:'규칙의 출처·적용 기간 재확인 필요',RULE_NOT_FOUND:'현재 규칙 자료 없음',PLACE_UNAVAILABLE:'지점 자료 사용 불가',PARTY_CHANGED:'여행 인원 변경됨',EVIDENCE_CHANGED:'예약 증빙 변경됨',DATE_OUTSIDE_TRIP:'여행 기간 밖의 방문일',INDOOR_UNCONFIRMED:'실내 장소 여부 미확인',RADIUS_EXCEEDED:'선택한 반경 초과',RADIUS_UNCONFIRMED:'거리 미확인',PLACE_CLOSED:'휴무·폐업 확인',PARTY_MISMATCH:'방문 인원 조건 불일치',INSUFFICIENT_TRAVEL_TIME:'앞뒤 이동 시간 부족',UNKNOWN_TRAVEL:'이동 경로 미확인',FUTURE_OPENING_UNCONFIRMED:'방문일의 영업 미확인',FATIGUE_TRAVEL_NOT_REDUCED:'이동 부담을 줄일 수 없음',TIME_OVERLAP:'다음 일정과 시간이 겹침'};
  const reasonText=code=>reasonNames[code]||'조건을 충족하는 근거를 추가로 확인해야 합니다';
  const ruleLabel=r=>r?.type==='rolling_days'?`방문 ${r.days_before_visit}일 전${r.explicit_local_time?' '+r.explicit_local_time:''}`:r?.type==='monthly_release'?`방문 ${r.target_month_offset}개월 전, ${r.release_day}일${r.explicit_local_time?' '+r.explicit_local_time:''}`:r?.type==='fixed_datetime'?`${r.explicit_local_date} ${r.explicit_local_time||'시각 미확인'}`:'공식 규칙 확인 필요';
  const priceLabel=p=>p?`${p.currency} ${p.amount_min??'?'}–${p.amount_max??'?'} · ${p.basis==='per_person'?'인당':'그룹'} · ${p.period==='meal'?'식사':p.period==='day'?'하루':'방문'} 기준 · ${p.tax==='included'?'세금 포함':'세금 확인 필요'}${p.deposit?' · 보증금 '+JSON.stringify(p.deposit):''}`:'가격 미확인';
  function clear(){serial++;tasks=[];todayData=null;todayChoice=null;todayDay=null;['#preparationList','#todayContent'].forEach(s=>$(s)?.replaceChildren());}
  async function load(tab){
    if(!c.state.trip){$('#newPreparation').disabled=true;const root=$(tab==='preparation'?'#preparationList':'#todayContent');root.replaceChildren(c.make('p','empty panel','여행을 먼저 만들거나 선택해 주세요.'));if(tab==='today')root.append(c.button('기기 저장본 확인',openOffline));return;}
    const seq=++serial,base=path();
    try {
      features=await c.api('/travel-tools/features');
      if(seq!==serial)return;
      if(tab==='preparation'){
        $('#newPreparation').disabled=!features.preparation;
        if(!features.preparation){$('#preparationList').textContent='예약 준비 기능이 꺼져 있습니다.';return;}
        const value=await c.api(base+'/reservation-tasks');
        if(seq!==serial)return; tasks=value.items;render();
      } else if(tab==='today') await loadToday(seq,base);
    }catch(e){if(seq===serial)c.fail(e,$(tab==='preparation'?'#preparationError':'#todayError'));}
  }
  function render(){
    const root=$('#preparationList');root.replaceChildren();
    if(!tasks.length)root.append(c.make('p','empty panel','아직 준비할 예약이 없어요. 예약 전 확인할 일을 추가해 보세요.'));
    for(const task of tasks){
      const card=c.make('article','panel');
      card.append(c.make('h2','',task.title),c.make('p','',`${task.visit_date} · 성인 ${task.party.adults} · 아동 ${task.party.children.length} · ${task.status==='waiting_open'&&task.release_timing==='past'?'오픈 예정 시점 지남 · 현재 확인 필요':statuses[task.status]}`));
      const calc=task.calculation;
      card.append(c.make('p','muted',calc.due_precision==='unknown'?'오픈·기한 확인 필요':`준비 알림: ${calc.due_date}${calc.due_at?' · '+new Date(calc.due_at).toLocaleString('ko-KR',{timeZone:task.timezone}):' · 시각 미확인'} (${task.timezone})`));
      if(task.release_timing==='due_today')card.append(c.make('p','hint','오늘이 예정일입니다. 오픈 시각과 실제 접수 여부를 확인해 주세요.'));
      if(task.rule)card.append(c.make('p','hint',`출처 확인일: ${task.rule.checked_at}`));
      card.append(c.button('상세·확인',()=>detail(task)));root.append(card);
    }
  }
  async function form(task=null){
    if(!c.state.trip){c.notice('여행을 먼저 선택해 주세요.');return;}
    const seq=serial,base=path();let catalog;
    try{catalog=(await c.api(base+'/discovery-catalog')).items;}catch(e){c.fail(e,$('#preparationError'));return;}
    if(seq!==serial)return;
    const key=crypto.randomUUID();
    c.openDialog(task?'예약 준비 수정':'예약 준비 추가',body=>{
      body.append(c.make('p','hint','이 목록은 실제 예약과 별도입니다. 지점의 공식 규칙이 없으면 오픈일은 확인 필요로 남습니다.'));
      let title,place,day,clock,zone,rule,requests,adults,children,kind;const requestChecks={};
      const {grid}=c.formBase(body,'준비 목록 저장',async()=>{
        const ages=children.value.trim()?children.value.split(',').map(a=>({age:a.trim()==='?'?null:Number(a.trim())})):[];
        const payload={task_kind:kind.value,title:title.value,place_id:place.value||null,visit_date:day.value,requested_time:clock.value||null,timezone:zone.value,rule_fact_id:rule.value||null,requests:requests.value,request_keys:Object.keys(requestChecks).filter(k=>requestChecks[k].checked),party:adults.value?{adults:Number(adults.value),children:ages}:null};
        await c.api(base+'/reservation-tasks'+(task?'/'+task.id:''),{method:task?'PATCH':'POST',headers:{'Idempotency-Key':key},body:task?{expected_version:task.version,action:'update',changes:payload}:payload});
        if(seq!==serial)return;c.closeDialog(true);await load('preparation');
      });
      kind=c.field(grid,'task_kind','업무 종류',task?.task_kind||'reserve',{select:[['reserve','예약 준비'],['open_check','오픈일 확인'],['party_inquiry','인원·아동 문의'],['cancel_check','외부 예약 취소 여부 확인']]});
      title=c.field(grid,'title','준비할 예약',task?.title||'',{required:true,maxLength:200});
      place=c.field(grid,'place_id','검증한 지점',task?.place_id||'',{select:[['','아직 확인하지 않음'],...catalog.map(p=>[p.place_id,p.name])]});
      day=c.field(grid,'visit_date','방문일',task?.visit_date||c.state.trip.start_date,{type:'date',required:true});
      clock=c.field(grid,'requested_time','희망 시각 · 미확정',task?.requested_time||'',{type:'time'});
      zone=c.field(grid,'timezone','시설 시간대',task?.timezone||c.state.trip.stops?.[0]?.timezone||'Asia/Tokyo',{select:[...new Set(c.destinations().map(city=>city.timezone))]});
      adults=c.field(grid,'party.adults','성인 수 · 비우면 여행 인원 사용',task?.party?.adults||'',{type:'number',min:1,max:50});
      children=c.field(grid,'party.children','아동 나이 · 쉼표로 구분, 미확인 ?',task?.party?.children?.map(a=>a.age??'?').join(',')||'');
      rule=c.field(grid,'rule_fact_id','공식 예약 오픈 규칙','',{select:[['','규칙 미확인']]});
      function rules(){const selected=catalog.find(p=>p.place_id===place.value);rule.replaceChildren(new Option('규칙 미확인',''));for(const f of selected?.facts||[])if(f.field==='booking_open_rule'&&f.usable&&f.status==='verified')rule.append(new Option(ruleLabel(f.value),f.id));rule.value=task?.rule_fact_id||'';if(!task&&selected)zone.value=c.knownDestination(selected.city)?.timezone||zone.value;}
      place.addEventListener('change',rules);rules();
      for(const [k,l] of [['vegetarian','채식'],['vegan','비건'],['nut_allergy','견과류 알레르기'],['gluten_free','글루텐 제외'],['step_free','계단 없는 접근']]){const label=c.make('label','check'),input=c.make('input');input.type='checkbox';input.checked=!!task?.request_keys?.includes(k);requestChecks[k]=input;label.append(input,c.make('span','',l+' 문의'));grid.append(label);}
      requests=c.field(grid,'requests','추가 요청 원문 · 문의문에서 번역 확인 필요',task?.requests||'',{textarea:true,maxLength:1000,wide:true});
    });
  }
  function detail(task){
    const epoch=c.state.epoch,base=path();
    c.openDialog('예약 준비 · '+task.title,body=>{
      const error=c.make('p','error');error.setAttribute('role','alert');
      body.append(c.make('p','',`${statuses[task.status]} · 버전 ${task.version}`),c.make('p','hint','링크 클릭은 완료가 아닙니다. 사용자 완료와 메일 증빙 확인은 별도 상태입니다. 취소는 준비 업무의 취소이며 외부 예약은 취소되지 않습니다.'));
      if(task.rule){const link=c.make('a','','규칙 출처 확인');link.href=task.rule.source.url;link.target='_blank';link.rel='noopener noreferrer';body.append(link,c.make('p','',ruleLabel(task.rule.value)),c.make('p','hint','출처 확인일: '+task.rule.checked_at+' · 만료: '+task.rule.expires_at));}
      if(task.confirmation_reasons?.length)body.append(c.make('p','form-note',[...new Set(task.confirmation_reasons.map(reasonText))].join(' · ')));
      const actions=c.make('div','actions');
      async function run(action,booking_id){try{const value=await c.api(path()+'/reservation-tasks/'+task.id,{method:'PATCH',body:{expected_version:task.version,action,booking_id}});if(epoch!==c.state.epoch)return;await load('preparation');if(epoch===c.state.epoch)detail(value);}catch(e){c.fail(e,error);}}
      if(['needs_confirmation','waiting_open'].includes(task.status))actions.append(c.button('진행 시작',()=>run('start')));
      if(['needs_confirmation','waiting_open','in_progress'].includes(task.status))actions.append(c.button('사용자 완료 표시',()=>run('report_complete')));
      if(task.status==='user_completed')actions.append(c.button('메일 증빙 대조',()=>evidence(task)));
      if(task.status!=='cancelled')actions.append(c.button('준비 업무 취소',()=>run('cancel')));
      if(['user_completed','evidence_verified','cancelled'].includes(task.status))actions.append(c.button('다시 확인',()=>run('reopen')));
      actions.append(c.button('내용 수정',()=>form(task)),c.button('규칙 재검증',()=>run('revalidate')));
      if(task.calculation.due_precision!=='unknown')actions.append(c.button('ICS 다운로드',async()=>{try{const blob=await c.api(path()+'/reservation-tasks/'+task.id+'/calendar.ics',{blob:true});const url=URL.createObjectURL(blob);const link=c.make('a');link.href=url;link.download='preparation.ics';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){c.fail(e,error);}}));
      for(const [language,label] of [['ja','일본어'],['es','스페인어'],['ca','카탈루냐어']])actions.append(c.button(label+' 문의 초안',async()=>{try{const draft=await c.api(path()+'/reservation-tasks/'+task.id+'/inquiry-drafts',{method:'POST',body:{expected_version:task.version,language}});if(epoch!==c.state.epoch)return;c.openDialog(label+' 문의문 · 발송하지 않음',b=>{b.append(c.make('p','form-note','희망 일시이며 예약 확정이 아닙니다. 추가 요청은 원문으로 보존합니다. 아래에서 편집해 복사할 수 있습니다.'));const g=c.make('div','form-grid');c.field(g,'inquiry','문의문',draft.text,{textarea:true,wide:true});c.field(g,'korean','한국어 확인본',draft.korean,{textarea:true,wide:true});b.append(g,c.make('p','hint','자동 발송·전화·예약·결제는 실행하지 않습니다.'));});}catch(e){c.fail(e,error);}}));
      body.append(actions,c.make('p','hint','ICS는 다운로드 시점의 복사본입니다. 자동 갱신 구독이 아닙니다.'),error);
    });
  }
  function evidence(task){
    const epoch=c.state.epoch,base=path();
    c.openDialog('예약 증빙의 날짜·지점·인원 대조',body=>{
      body.append(c.make('p','hint','메일 원문을 확인하고 추출값이 잘못되었으면 예약 화면에서 먼저 교정해 주세요. 아래 연결 정보는 사용자 교정으로 기록합니다. 수동 예약은 증빙 확인 대상이 아닙니다.'));
      let choice,checked;
      const {grid}=c.formBase(body,'대조값 저장 후 증빙 확인',async()=>{
        if(!checked.checked)throw new Error('메일 원문 대조 확인이 필요합니다.');
        const b=c.state.bookings.find(x=>x.id===choice.value);if(!b)throw new Error('메일에서 추출한 예약을 선택해 주세요.');
        const p=path()+'/bookings/'+b.id;
        const saved=await c.api(p,{method:'PATCH',body:{expected_version:b.version,changes:[{field_path:'place_id',value:task.place_id},{field_path:'party',value:task.party}]}});
        if(epoch!==c.state.epoch)return;b.version=saved.version;
        const taskPath=base+'/reservation-tasks/'+task.id;
        let latest=await c.api(taskPath);
        if(epoch!==c.state.epoch)return;
        if(latest.visit_date!==task.visit_date||latest.place_id!==task.place_id||JSON.stringify(latest.party)!==JSON.stringify(task.party))throw new Error('준비 조건이 바뀌었습니다. 교정은 저장했으며 최신 조건과 원문을 다시 대조해 주세요.');
        // Correcting evidence increments the trip version. Reaffirm the user's
        // just-confirmed completion against that fresh version before matching.
        if(latest.status==='needs_confirmation')latest=await c.api(taskPath,{method:'PATCH',body:{expected_version:latest.version,action:'report_complete'}});
        if(epoch!==c.state.epoch)return;
        const value=await c.api(taskPath,{method:'PATCH',body:{expected_version:latest.version,action:'verify_evidence',booking_id:b.id}});
        if(epoch!==c.state.epoch)return;c.closeDialog(true);await load('preparation');if(epoch===c.state.epoch)detail(value);
      });
      choice=c.field(grid,'booking_id','메일에서 추출한 예약','',{select:[['','예약 선택'],...c.state.bookings.filter(b=>b.document_id).map(b=>[b.id,(b.effective?.provider||b.id)+' · '+(b.effective?.date||'날짜 미확인')])]});
      const label=c.make('label','check');checked=c.make('input');checked.type='checkbox';label.append(checked,c.make('span','','원문에서 이 지점과 인원 일치를 직접 확인했습니다.'));grid.append(label);
    });
  }
  let todayData=null, todayChoice=null, todayDay=null, offlineVisible=false, offlineExpiryTimer;
  const offline=()=>window.TravelOffline;
  async function loadToday(seq,base){
    const query=new URLSearchParams();if(todayChoice)query.set('itinerary_id',todayChoice);if(todayDay)query.set('day',todayDay);
    const value=await c.api(base+'/today?'+query);if(seq!==serial)return;todayData=value;
    const root=$('#todayContent');root.replaceChildren();
    const controls=c.make('div','filters'),pick=c.field(controls,'today_schedule','일정',value.selected_itinerary,{select:value.schedules.map(x=>[x.id,x.city+' · 일정 '+x.version])});
    pick.addEventListener('change',()=>{todayChoice=pick.value;todayDay=null;load('today');});
    const day=c.field(controls,'today_day','현지 날짜',value.date,{type:'date'});day.addEventListener('change',()=>{todayDay=day.value;load('today');});
    root.append(controls,c.make('p','hint',value.timezone+' · 위치 권한 없이 저장한 출발점으로 이용합니다. 이동 시간이 없으면 출발 권장 시각도 미확인입니다.'));
    for(const item of value.items){const card=itemCard(item,value.timezone);if(item.item_id===value.next_item_id)card.prepend(c.make('strong','badge','다음 일정'));
      card.append(c.make('p','',item.recommended_departure?'출발 권장 '+new Date(item.recommended_departure).toLocaleTimeString('ko-KR',{timeZone:value.timezone}):'출발 권장 시각 미확인'));
      if(features.plan_b)card.append(c.button('상황별 대체안',()=>planB(value,item)));
      root.append(card);}
    if(!value.items.length)root.append(c.make('p','empty panel','선택한 현지 날짜에 배치된 일정이 없습니다. 일정 화면에서 계획을 만들거나 다른 날짜를 선택해 주세요.'));
    const tasks=c.make('section','panel');tasks.append(c.make('h2','','확인할 예약 준비'));
    for(const task of value.tasks.filter(t=>!['cancelled','evidence_verified'].includes(t.status)))tasks.append(c.make('p','',task.visit_date+' · '+task.title+' · '+statuses[task.status]));
    tasks.append(c.button('예약 준비 목록',()=>c.setTab('preparation')));root.append(tasks);
    const box=c.make('section','panel');box.append(c.make('h2','','이 여행을 오프라인에 저장'),c.make('p','form-note','개인 기기에서만 선택해 주세요. 최대 24시간 읽기 전용 복사본입니다. 최신 영업·예약을 확인한 자료가 아니며 오프라인 기기의 원격 삭제는 즉시 반영되지 않습니다.'));
    box.append(c.button(features.offline?'저장 범위 확인':'오프라인 기능 꺼짐',()=>{if(features.offline)downloadForm();}),c.button('기기 저장본 열기',openOffline),c.button('이 기기의 모든 저장본 삭제',async()=>{await purge();c.notice('기기 저장본을 삭제했습니다.');}));root.append(box);
    if(c.state.session?.user?.role==='admin'&&features.offline)root.append(c.button('관리자 · 출처별 오프라인 허용',permissionForm));
  }
  function itemCard(item,zone,snapshot=false){const card=c.make('article','panel');card.append(c.make('h2','',item.name||'일정'),c.make('p','',item.native_name||''),c.make('p','',`${item.local_start||'시각 미확인'} → ${item.local_end||'시각 미확인'} · ${item.start_timezone||zone}`),c.make('p','',`${item.locked?'잠금 · ':''}${item.confirmed_booking?'예약 기록 · ':''}${snapshot?'저장 당시 상태 · 현장 재확인 필요':item.verification_status==='verified'?'검증됨':'확인 필요'}`));if(item.address)card.append(c.make('p','',item.address));else card.append(c.make('p','hint','주소 미확인 또는 오프라인 저장 미허용'));if(item.map_url){const a=c.make('a','','지도 링크 열기');a.href=item.map_url;a.rel='noopener noreferrer';a.target='_blank';card.append(a);}if(item.travel)card.append(c.make('p','hint',`이동: ${item.travel.duration_minutes==null?'미확인':item.travel.duration_minutes+'분'} · ${item.travel.basis} · ${item.travel.mode}`));return card;}
  function planB(value,item){
    const epoch=c.state.epoch;
    const base=path()+'/itineraries/'+value.selected_itinerary;
    c.openDialog('영향 구간만 바꾸는 대체안',body=>{
      body.append(c.make('p','form-note','선택 항목의 시작 시각을 유지하고 양쪽 이동과 다음 예약을 검사합니다. 원 일정은 적용 전까지 바뀌지 않습니다. 확정 예약은 여기에서 취소되지 않습니다.'));
      let reason,basis,duration,radius,origin,lat,lon;
      const {grid}=c.formBase(body,'대체안 미리보기',async()=>{
        const payload={expected_version:value.selected_version,item_id:item.item_id,reason:reason.value,basis:basis.value,duration_minutes:duration.value?Number(duration.value):null,radius_m:radius.value?Number(radius.value):null};
        if(lat.value||lon.value)payload.origin={label:origin.value,latitude:Number(lat.value),longitude:Number(lon.value)};
        const result=await c.api(base+'/alternatives',{method:'POST',body:payload});
        if(epoch===c.state.epoch)showAlternatives(base,result);
      });
      reason=c.field(grid,'reason','상황','rain',{select:[['rain','비'],['closed','휴무'],['long_queue','긴 대기'],['fatigue','피로'],['reservation_failed','예약 실패']]});
      basis=c.field(grid,'basis','상황의 근거','user_report',{select:[['user_report','내가 확인한 상황'],['assumption','가정으로 검토']]});
      duration=c.field(grid,'duration_minutes','체류 분 · 비우면 기존 값','',{type:'number',min:5,max:720});radius=c.field(grid,'radius_m','반경 m · 기존 조건보다 좁힐 때만','',{type:'number',min:100,max:50000});
      origin=c.field(grid,'origin.label','직접 고른 출발점 · 비우면 저장한 출발점','');lat=c.field(grid,'origin.latitude','출발점 위도','',{type:'number',min:-90,max:90});lon=c.field(grid,'origin.longitude','출발점 경도','',{type:'number',min:-180,max:180});lat.step=lon.step='any';
    });
  }
  function showAlternatives(base,result){const epoch=c.state.epoch;c.openDialog('대체안 비교 · 아직 적용하지 않음',body=>{
    body.append(c.make('p','hint','실시간 날씨·대기·잔여석은 조회하지 않았습니다. 가격은 출처의 기준이며 총 추가 비용은 미확인입니다.'));
    if(!result.alternatives.length){body.append(c.make('p','form-note',result.next_action||'조건을 만족하는 대안이 없습니다. 반경·예산·필수 조건을 자동 완화하지 않았습니다.'),c.make('p','hint',[...new Set((result.rejected||[]).flatMap(x=>x.reason_codes).map(reasonText))].join(' · ')));return;}
    for(const p of result.alternatives){const card=c.make('article','panel');card.append(c.make('h2','',p.comparison.after.name),c.make('p','',`${p.comparison.before.local_start} ${p.comparison.before.name} → ${p.comparison.after.local_start} ${p.comparison.after.name}`),c.make('p','',`종료: ${p.comparison.after.local_end} · ${p.validation_status==='validated'?'검증한 변경안':'잠정 변경안'}`),c.make('p','hint','이전 가격: '+priceLabel(p.comparison.price_before)+' → 대체 가격: '+priceLabel(p.comparison.price)));
      for(const leg of p.legs.filter(l=>[l.from_item_id,l.to_item_id].includes(p.plan_b.added_item_id)))card.append(c.make('p','',`연결 이동 ${leg.duration_minutes==null?'미확인':leg.duration_minutes+'분'} + 버퍼 ${leg.buffer_minutes}분 · ${leg.basis}`));
      card.append(c.make('p','hint','앞뒤 다른 일정과 확정 예약의 시각은 유지합니다. 미확인: '+(p.unresolved_conditions.map(x=>reasonText(x.code)).join(', ')||'없음')));
      const err=c.make('p','error');card.append(c.button('이 대체안 적용',async()=>{try{await c.api(base,{method:'PATCH',body:{expected_version:p.base_version,preview_id:p.preview_id}});if(epoch!==c.state.epoch)return;c.closeDialog(true);await load('today');c.notice('대체안을 적용했습니다. 일정 화면에서 되돌릴 수 있습니다.');}catch(e){c.fail(e,err);}}),err);body.append(card);}
  });}
  function downloadForm(){c.openDialog('오프라인 저장 범위 확인',body=>{
    body.append(c.make('p','form-note','도시별 가장 최근 일정의 시각·잠금·확인 상태와 저장 허용된 장소명·주소만 저장합니다. 메일 원문, 예약번호, 결제, 세션, 리뷰, 사진과 지도 타일은 제외합니다.'));
    let device,notes;const {grid}=c.formBase(body,'저장할 내용 미리보기',async()=>{
      if(!device.checked)throw new Error('공용 기기에는 저장할 수 없습니다. 개인 기기 확인이 필요합니다.');
      const owner=c.state.session.user.id,base=path(),epoch=c.state.epoch;
      const bundle=await c.api(base+'/offline-bundles',{method:'POST',body:{private_device_confirmed:true,include_notes:notes.checked}});
      if(epoch!==c.state.epoch||owner!==c.state.session?.user?.id)throw new Error('계정 또는 여행이 바뀌었습니다.');
      c.openDialog('기기에 저장할 내용 미리보기',preview=>{
        preview.append(c.make('p','form-note',bundle.trip.title+' · 최대 24시간 읽기 전용 · 메일·예약번호·결제·세션 제외'));
        for(const schedule of bundle.schedules){preview.append(c.make('h3','',schedule.city+' · '+schedule.timezone));for(const item of schedule.items)preview.append(c.make('p','',`${item.local_start||'시각 미확인'} · ${item.name} · ${item.address||'주소 저장 미허용'}`));}
        preview.append(c.make('p','hint',`예약 준비 ${bundle.tasks.length}개 · 개인 메모 ${bundle.notes.length}개`));for(const n of bundle.notes)preview.append(c.make('p','',n.note));
        const error=c.make('p','error'),save=c.button('이 내용 기기에 저장',async()=>{save.disabled=true;try{if(epoch!==c.state.epoch||owner!==c.state.session?.user?.id)throw new Error('계정 또는 여행이 바뀌었습니다.');await offline().put(bundle,owner);if(epoch!==c.state.epoch)return;c.closeDialog(true);c.notice('오프라인 저장을 완료했습니다. 저장본은 최대 24시간 유효합니다.');}catch(e){c.fail(e,error);save.disabled=false;}});
        preview.append(save,error,c.make('p','hint','창을 닫으면 이 미리보기는 기기에 저장되지 않습니다.'));
      });
    });
    for(const [text,key]of [['내 개인 기기입니다','device'],['개인 메모도 포함합니다','notes']]){const label=c.make('label','check'),input=c.make('input');input.type='checkbox';label.append(input,c.make('span','',text));grid.append(label);if(key==='device')device=input;else notes=input;}
  });}
  async function permissionForm(){try{const catalog=(await c.api(path()+'/discovery-catalog')).items;
    c.openDialog('관리자 · 오프라인 이용 범위 검토',body=>{body.append(c.make('p','form-note','출처의 이용 조건을 직접 확인한 필드만 허용하세요. 온라인 표시 허용과 별도이며, 출처 버전 변경·철회 시 재검토가 필요합니다.'));
      const all=catalog.flatMap(p=>p.sources.filter(s=>s.status==='active').map(s=>({...s,label:p.name+' · '+s.url})));let source,days;const checks={};const {grid}=c.formBase(body,'검토한 허용 범위 저장',async()=>{const s=all.find(x=>x.id===source.value);await c.api('/admin/offline-source-permissions',{method:'POST',body:{source_id:s.id,source_version:s.version,policy_version:s.policy_version,fields:Object.keys(checks).filter(k=>checks[k].checked),expires_at:new Date(Date.now()+Number(days.value)*86400000).toISOString()}});c.closeDialog(true);c.notice('출처별 오프라인 허용 범위를 저장했습니다.');});
      source=c.field(grid,'source_id','출처','',{select:all.map(s=>[s.id,s.label])});days=c.field(grid,'expires_at','허용 유효 기간 · 일',7,{type:'number',min:1,max:365,required:true});
      for(const [k,l]of [['name','장소명'],['native_name','원어명'],['address','주소'],['map_url','지도 링크']]){const label=c.make('label','check'),input=c.make('input');input.type='checkbox';checks[k]=input;label.append(input,c.make('span','',l));grid.append(label);}
    });}catch(e){c.fail(e,$('#todayError'));}}
  function hideOffline(){clearTimeout(offlineExpiryTimer);offlineVisible=false;$('#offlineApp').hidden=true;$('#offlineApp').replaceChildren();}
  async function purge(){hideOffline();return offline()?.purge();}
  async function bind(owner){hideOffline();await offline()?.bind(owner);}
  async function openOffline(){
    hideOffline();const root=$('#offlineApp');root.hidden=false;root.append(c.make('p','', '연결과 저장본을 확인하고 있습니다…'));$('#auth').hidden=true;$('#app').hidden=true;
    try {
      let session=null,network=false;
      try{const response=await fetch('/api/v2/session',{credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(8000)});if(!response.ok||!(response.headers.get('content-type')||'').includes('json'))throw Object.assign(new Error('서버의 권한 확인을 기다린 뒤 다시 시도해 주세요.'),{serverResponse:true});session=await response.json();network=true;}catch(e){if(e.serverResponse)throw e;if(!['TypeError','TimeoutError'].includes(e.name))throw e;}
      if(network&&!session.authenticated){await purge();throw new Error('로그인 권한을 확인하지 못해 저장본을 삭제했습니다. 다시 로그인해 주세요.');}
      if(network)await offline().bind(session.user.id);
      let bundles=await offline().list();
      if(network){const valid=[];for(const b of bundles){const res=await fetch('/api/v2/trips/'+encodeURIComponent(b.trip.id)+'/offline-bundles/validate',{method:'POST',credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf_token},body:JSON.stringify({manifest:b.manifest,include_notes:b.include_notes})});if([401,403].includes(res.status)){await purge();throw new Error('권한이 만료되어 저장본을 삭제했습니다.');}if(res.status===404){await offline().remove(b.trip.id);continue;}if(!res.ok)throw new Error('서버에서 삭제·권한 상태를 확인하지 못했습니다.');const check=await res.json();if(check.valid&&check.namespace===b.namespace)valid.push(b);else await offline().remove(b.trip.id);}bundles=valid;}
      root.replaceChildren();offlineVisible=true;root.hidden=false;root.append(c.make('h1','','기기에 저장한 여행'),c.make('p','form-note',`${network?'연결 후 권한 확인 완료':'오프라인'} · 읽기 전용 · 최신 영업·예약 확인 자료가 아닙니다. 오프라인 기기에는 원격 삭제가 즉시 반영되지 않습니다.`));
      if(!bundles.length)root.append(c.make('p','','유효한 저장본이 없습니다. 연결 후 다시 다운로드해 주세요.'));
      for(const b of bundles){const group=c.make('section','panel');group.append(c.make('h2','',b.trip.title),c.make('p','hint',`생성 ${b.generated_at} · 만료 ${b.expires_at}`));for(const s of b.schedules){group.append(c.make('h3','',s.city+' · '+s.timezone));for(const item of s.items)group.append(itemCard({...item,travel:s.legs.find(l=>l.to_item_id===item.item_id)},s.timezone,true));}for(const task of b.tasks)group.append(c.make('p','',`${task.visit_date} · 예약 준비 · ${statuses[task.status]}`));for(const note of b.notes)group.append(c.make('p','',note.note));root.append(group);}
      if(bundles.length)offlineExpiryTimer=setTimeout(()=>{hideOffline();$('#auth').hidden=false;c.notice('오프라인 저장본이 만료되었습니다. 연결 후 다시 다운로드해 주세요.');},Math.max(1,Math.min(...bundles.map(b=>Date.parse(b.expires_at)))-Date.now()));
      root.append(c.button('연결·권한 다시 확인',openOffline),c.button('로그인 화면으로',()=>{hideOffline();$('#auth').hidden=false;}),c.button('저장본 삭제하고 닫기',async()=>{await purge();$('#auth').hidden=false;}));
    }catch(e){root.hidden=false;root.replaceChildren(c.make('p','error',e.message),c.button('로그인 화면으로',()=>{hideOffline();$('#auth').hidden=false;}));}
  }

  function init(context){c=context;offline()?.onPurge(hideOffline);$('#openOffline').addEventListener('click',openOffline);window.addEventListener('online',()=>{if(offlineVisible)openOffline();});document.addEventListener('visibilitychange',()=>{if(!document.hidden&&offlineVisible)openOffline();});$('#newPreparation').addEventListener('click',()=>form());$('#refreshPreparation').addEventListener('click',()=>load('preparation'));}
  window.TravelTools={init,load,clear,purge,bind,hideOffline,remove:trip=>offline()?.remove(trip)};
})();
