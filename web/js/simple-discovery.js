/* Small context editor. The server owns date/stop resolution; drafts stay in this account's memory. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.SimpleDiscovery=api;})(typeof window==='undefined'?globalThis:window,function(){
  'use strict';
  let ui, draft=null;
  function enabled(){return ui?.state.session?.features?.simple_discovery===true;}
  function clear(){draft=null;}
  function init(context){ui=context;}
  function configure(){if(!ui)return;document.documentElement.classList.toggle('simple-discovery',enabled());const b=document.getElementById('editDiscoveryConditions');if(b)b.textContent=enabled()?'조건 수정':'필터';}
  function renderQuick(host){if(!enabled())return false;host.replaceChildren();const d=ui.ensureDiscoveryDraft(),e=ui.state.discovery.conditions;if(!d)return true;
    const origin=e.origin_context;host.append(ui.make('p','context-origin',origin?.origin?.label||origin?.label||'숙소 없이 도시 주변에서 찾기'));
    if(origin?.status==='ambiguous')host.append(ui.make('p','hint','이 날짜에 숙소가 여러 곳이에요. 조건 수정에서 출발점을 골라 주세요.'));
    const errors=e.validation||[];if(errors.length){const note=ui.make('div','context-repair');note.append(ui.make('p','',errors[0].message),ui.button('날짜·도시 확인',open,'secondary'));host.append(note);}
    const filters=ui.make('div','quick-filters');window.DiscoveryExperience?.quickFilters(filters,d.conditions);host.append(filters);return true;
  }
  function open(){if(!enabled())return ui.advanced();if(!ui.state.trip)return;
    const epoch=ui.state.epoch,trip=ui.state.trip;let envelope=ui.state.discovery.conditions,initial=ui.ensureDiscoveryDraft();
    if(!draft||draft.epoch!==epoch)draft={epoch,overrides:structuredClone(initial.overrides),stop_id:initial.stop_id,date:initial.conditions.visit.date,adults:initial.conditions.party.adults};
    const local=structuredClone(draft);let sequence=0,resolved=envelope,day,stop,adults,origin,errors;
    ui.openDialog('어디에서, 언제 찾을까요?',body=>{
      body.append(ui.make('p','hint','여행의 날짜·인원·숙소를 가져왔어요. 바꿀 내용만 입력하세요. 닫으면 입력은 이 여행의 임시 초안으로 남고 기존 결과는 유지됩니다.'));
      const {form,grid}=ui.formBase(body,'적용하고 찾기',async()=>{
        const result=await resolve();if(!result)return;if(result.validation?.length){ui.localError(form,result.validation[0].field,result.validation[0].message);return;}
        const current=ui.ensureDiscoveryDraft();current.conditions=structuredClone(result.conditions);current.overrides=structuredClone(result.overrides);current.stop_id=result.trip_context.stop_id;
        ui.state.discovery.conditions={...envelope,...result};ui.state.discovery.dirty=true;ui.state.recommendations.conditionsDraft=true;ui.state.discovery.draftRevision=(ui.state.discovery.draftRevision||0)+1;
        ui.closeDialog(true);draft=null;if(ui.changed)ui.changed();else ui.render();await ui.applyRecommendations();
      });
      day=ui.field(grid,'visit.date','방문일',local.date,{type:'date',required:true});day.min=trip.start_date;day.max=trip.end_date;
      stop=ui.field(grid,'stop_id','방문 도시',local.stop_id||'',{select:[['','날짜에 맞춰 자동 선택'],...(envelope.stay_options||[]).map(s=>[s.stop_id,`${s.label} · ${s.start_date} ~ ${s.end_date}`])]});
      adults=ui.field(grid,'party.adults','성인 인원 · 선택',local.adults??'',{type:'number',min:1,max:50,hint:'비워 두면 미정으로 남겨요.'});
      origin=ui.field(grid,'origin_selection','출발점',local.overrides.origin_selection?.accommodation_id||local.overrides.origin_selection?.kind||'automatic',{select:[['automatic','이 날짜의 숙소 자동 사용'],['none','숙소 없이 도시 주변에서 찾기']]});
      errors=ui.make('p','form-note');errors.setAttribute('role','status');grid.append(errors);
      const more=ui.make('div','context-more');more.append(ui.button('취향·가격·방문 상세 조건',async()=>{remember();const result=await resolve();if(result&&!result.validation?.length)ui.advanced(result.conditions);},'text-button'),ui.button('숙소·직접 출발점 관리',()=>{remember();window.AccommodationTools?.editOrigin();},'text-button'));body.append(more);
      function remember(){local.date=day.value;local.stop_id=stop.value||null;local.adults=adults.value===''?null:Number(adults.value);draft={...local,epoch};}
      function drawOrigin(result){const previous=origin.value,selection=local.overrides.origin_selection;const choices=result.origin_context?.candidates||[];origin.replaceChildren(new Option('이 날짜의 숙소 자동 사용','automatic'),new Option('숙소 없이 도시 주변에서 찾기','none'));if(selection?.kind==='manual')origin.append(new Option('직접 선택한 출발점','manual'));for(const item of choices)origin.append(new Option(item.display_name,item.accommodation_id));origin.value=[...origin.options].some(o=>o.value===previous)?previous:'automatic';}
      async function resolve(){remember();const revision=++sequence;if(!day.validity.valid||!adults.validity.valid)return null;errors.textContent='여행 정보를 확인하고 있어요…';
        const overrides=structuredClone(local.overrides);overrides.visit={...overrides.visit,date:day.value};delete overrides.city;if(overrides.visit)delete overrides.visit.timezone;
        if(local.adults!==initial.conditions.party.adults)overrides.party={...overrides.party,adults:local.adults};
        const candidate=resolved.origin_context?.candidates?.find(c=>c.accommodation_id===origin.value);
        if(candidate)overrides.origin_selection={kind:'accommodation',accommodation_id:candidate.accommodation_id,expected_version:candidate.version};else if(['automatic','none','manual'].includes(origin.value))overrides.origin_selection={kind:origin.value};
        try{const next=await ui.api(ui.tripPath(trip)+'/discovery-context/preview',{method:'POST',body:{expected_trip_version:trip.version,expected_conditions_version:envelope.version,stop_id:local.stop_id,visit_date:day.value,overrides}});if(epoch!==ui.state.epoch||revision!==sequence||!form.isConnected)return null;
          resolved=next;local.overrides=overrides;local.stop_id=next.trip_context.stop_id;stop.value=local.stop_id||'';drawOrigin(next);errors.textContent=(next.validation||[]).map(e=>e.message).join(' ');remember();return next;
        }catch(error){if(epoch!==ui.state.epoch||revision!==sequence)return null;errors.textContent=error.message;ui.formError(form,error);if(error.status===409&&!form.querySelector?.('.context-conflict')){const compare=ui.button('최신 조건 확인 · 내 입력 유지',async()=>{try{const fresh=await ui.api(ui.tripPath(trip)+'/discovery-conditions');if(epoch!==ui.state.epoch||!form.isConnected)return;envelope=fresh;trip.version=fresh.trip_version;errors.textContent=`현재 서버: ${fresh.conditions.visit.date} · 성인 ${fresh.conditions.party.adults??'미정'}명. 입력은 유지했어요. 확인 후 다시 적용해 주세요.`;compare.remove();}catch(e){ui.formError(form,e);}},'secondary context-conflict');body.append(compare);}return null;}
      }
      day.addEventListener('change',()=>{stop.value='';resolve();});for(const input of [stop,adults,origin])input.addEventListener('change',resolve);
      for(const input of [day,stop,adults,origin])input.addEventListener('input',remember);
      drawOrigin(envelope);resolve();
    });
  }
  return {init,enabled,configure,renderQuick,open,clear};
});
