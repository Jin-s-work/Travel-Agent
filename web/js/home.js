// @ts-check
/* Recommendation-first home. Reads existing trip results; never creates a run on render. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.GoingHome=api;})(typeof window==='undefined'?globalThis:window,function(){
  'use strict';
  let ui,cardSignature=null;
  function previews(run,limit=3){
    const groups=previewSections(run,limit),result=[];for(let i=0;i<limit;i++)for(const kind of ['landmark','local_discovery','reference']){while(groups[kind].length&&result.some(p=>p.place_id===groups[kind][0].place_id))groups[kind].shift();const item=groups[kind].shift();if(item)result.push(item);}return result.slice(0,limit);
  }
  function previewSections(run,limit=2){const result={};for(const kind of ['local_discovery','landmark','reference']){const seen=new Set();result[kind]=['items','needs_confirmation'].flatMap(g=>run?.result?.sections?.[kind]?.[g]||[]).filter(item=>{if(seen.has(item.place_id)||item.eligibility==='ineligible'||item.synthetic||item.selection_status==='limit_deferred')return false;seen.add(item.place_id);return true;}).slice(0,limit);}return result;}

  function savedPlace(bookmarks,id){return (bookmarks||[]).find(b=>!b.deleted_at&&(b.matched_place_id===id||b.place?.id===id||b.place?.place_id===id||b.input_kind==='place'&&b.input_value===id));}
  function init(context){
    ui=context;
    document.getElementById('homeFindPlaces').addEventListener('click',()=>ui.setTab('explore'));
    document.getElementById('homeAskForm').addEventListener('submit',event=>{
      event.preventDefault();const input=/** @type {HTMLTextAreaElement} */(document.getElementById('homeQuestion'));
      if(!input.value.trim()||!ui.state.trip||ui.state.asking)return;
      /** @type {HTMLTextAreaElement} */(document.getElementById('question')).value=input.value;ui.setTab('ask');ui.submitQuestion(input.value).then(ok=>{if(ok)input.value='';});
    });
    const host=document.getElementById('homeAskSuggestions');
    for(const question of ['첫날 예약을 모두 알려줘','체크아웃 시간은 언제야?','취소·환불 규정을 알려줘'])host.append(ui.button(question,()=>{/** @type {HTMLTextAreaElement} */(document.getElementById('homeQuestion')).value=question;document.getElementById('homeQuestion').focus();},'chip'));
  }
  function clear(){cardSignature=null;if(!ui)return;/** @type {HTMLTextAreaElement} */(document.getElementById('homeQuestion')).value='';document.getElementById('homeRecommendations').replaceChildren();}
  function render(){
    if(!ui)return;const {$,make,button,state}=ui,trip=state.trip;
    $('#homeDiscovery').hidden=!trip;$('#homeAssistant').hidden=!trip;
    $('#homeQuestion').disabled=!trip||state.asking;$('#homeAskSubmit').disabled=!trip||state.asking;
    $('#homeAskSubmit').textContent=state.asking?'답변 확인 중…':'물어보기';
    if(!trip)return;
    const city=ui.destinationLabel(state.discovery.conditions?.conditions?.city||trip.stops?.[0]?.city);
    $('#homeDiscoveryTitle').textContent=city==='도시 미정'?'이번 여행, 어디로 가볼까요?':`${city}에서 가볼 곳을 찾아보세요`;
    const r=state.recommendations,run=r.displayed,items=previews(run),busy=r.submitting||r.resultRecoveryPending||['queued','running'].includes(r.active?.state);
    $('#homeFindPlaces').textContent=busy?'추천 진행 상황 보기':items.length?'추천 전체 보기':'추천 받기';
    const summary=busy?'장소와 방문 조건을 확인하고 있어요. 다른 화면을 봐도 계속 진행됩니다.':items.length?(run.input_status==='stale'||run.data_status==='stale'?'이전 조건의 결과예요. 추천 화면에서 현재 조건으로 다시 찾을 수 있어요.':'최근 살펴본 장소예요. 상세를 확인하거나 바로 저장하세요.'):r.connectionError?'이전 추천을 불러오지 못했어요. 추천 화면에서 다시 연결할 수 있어요.':'여행의 도시와 날짜로 찾을 수 있어요. 인원·취향·예산은 필요할 때만 입력하세요.';
    $('#homeDiscoveryNote').textContent=summary;
    const host=$('#homeRecommendations');
    const signature=JSON.stringify([state.epoch,trip.id,run?.run_id,items,state.discovery.bookmarks?.map(b=>[b.id,b.matched_place_id,b.input_kind,b.input_value,b.deleted_at]),[...(state.savingPlaces||[])]]);
    if(signature===cardSignature){if(items.length&&state.tab==='trip')window.DiscoveryFlow?.photos(items);return;}cardSignature=signature;
    const focused=host.contains(document.activeElement)?document.activeElement:null,focusedId=/** @type {HTMLElement|null|undefined} */(focused?.closest('[data-place-id]'))?.dataset.placeId,focusedLabel=focused?.getAttribute('aria-label')||focused?.textContent;
    host.replaceChildren();
    for(const [kind,title] of [['local_discovery','현지어 리뷰'],['landmark','유명한 곳']]){const section=make('section','home-evidence-section');section.append(make('h3','',title));const group=make('div','home-evidence-grid');section.append(group);host.append(section);const entries=previewSections(run)[kind];if(!entries.length){const stateCopy=globalThis.DiscoveryExperience?.sectionStateCopy(kind,run?.result?.section_status?.[kind]||{});section.append(make('p','hint',stateCopy?.title||(kind==='local_discovery'?'이 도시의 현지어 리뷰를 준비하고 있어요.':'이 도시의 유명한 곳을 준비하고 있어요.')));}
    for(const item of entries){const card=make('article','home-place-card');card.dataset.placeId=item.place_id;card.append(ui.placePhotoGallery(item));
      const body=make('div','home-place-body');body.append(make('h3','',item.name),make('p','hint',item.address||city));
      const actions=make('div','actions');actions.append(button('상세 보기',()=>ui.discoveryPlaceDetail(item.place_id,run),'text-button'),ui.placeSaveButton(item,run));body.append(actions);card.append(body);globalThis.ProductTools?.observe(card,item.place_id,run.run_id);group.append(card);
    }
    section.append(button('더 살펴보기',()=>{ui.setTab('explore');window.DiscoveryExperience?.selectMode(kind);},'text-button'));}
    if(focusedId)for(const card of host.querySelectorAll('[data-place-id]'))if(card.dataset.placeId===focusedId){const action=[...card.querySelectorAll('button')].find(b=>(b.getAttribute('aria-label')||b.textContent)===focusedLabel);action?.focus({preventScroll:true});}
    if(items.length&&state.tab==='trip')window.DiscoveryFlow?.photos(items);
  }
  return {init,render,clear,previews,previewSections,savedPlace};
});
