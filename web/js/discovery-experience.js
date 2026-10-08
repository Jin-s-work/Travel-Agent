/* Two evidence-led discovery experiences. All personal state remains session scoped. */
(function(root,factory){const value=factory();if(typeof module==='object'&&module.exports)module.exports=value;else root.DiscoveryExperience=value;})(typeof window==='undefined'?globalThis:window,function(){
  'use strict';
  const modes={local_discovery:{title:'현지어 리뷰로 찾기',note:'원문 언어를 확인한 리뷰에서 찾는 곳'},landmark:{title:'유명한 곳',note:'공식·플랫폼·편집 자료로 알려진 곳'},reference:{title:'둘러보기',note:'이 도시의 장소를 먼저 살펴보세요'}};
  const iconicTypes={official_landmark:'공식 대표 장소',platform_popular:'같은 플랫폼에서 평가가 많은 곳',editorial_recognition:'편집 자료 선정'};
  let ui,capabilities=null,loading=false,serial=0,selected='reference',criteria={minLocal:0.6,maxKorean:0.1,ordering:'evidence'};
  function safeNumber(value,fallback){return typeof value==='number'&&Number.isFinite(value)?value:fallback;}
  function percent(n){return n==null?'미측정':new Intl.NumberFormat('ko-KR',{style:'percent',maximumFractionDigits:1}).format(n);}
  function evidenceData(e){
    if(e?.state!=='available'||!e.counts)return null;
    const c=e.counts,T=c.text_count,C=c.classified_count,U=c.unknown_count??c.unclassified_count,L=c.local_count,K=c.korean_count;
    if(![T,C,U,L,K].every(n=>Number.isInteger(n)&&n>=0)||T!==C+U||L+K>C)return null;
    return {T,C,U,L,K,local:C?L/C:null,korean:C?K/C:null,lower:T?L/T:null,upper:T?(K+U)/T:null,unknown:T?U/T:null};
  }
  function evidenceLines(e){const x=evidenceData(e);if(!x)return ['현재 표시할 수 있는 검증된 언어 관측이 없습니다.'];return [`본문 T ${x.T}건 · 판별 C ${x.C}건 · 미판별 U ${x.U}건`,`현지어 ${x.L} / ${x.C}건 · ${percent(x.local)} · 한국어 ${x.K} / ${x.C}건 · ${percent(x.korean)}`,`미판별 포함 본문 ${x.T}건 기준: 현지어 하한 ${percent(x.lower)} · 한국어 상한 ${percent(x.upper)}`];}
  function sectionStateCopy(type,meta){
    const states={disabled:['현지어 리뷰 추천이 아직 꺼져 있어요','실제 자료·이용 범위·언어 품질을 확인한 후 제공합니다.'],expired:['이 추천 근거의 확인 기한이 지났어요','새 근거를 확인하기 전에는 이전 비율을 사용하지 않습니다.'],budget_exhausted:['자료 확인 예산이 부족해요','저장된 여행과 사용할 수 있는 다른 추천 근거는 유지합니다.'],provider_failed:['리뷰 공급자 연결을 마치지 못했어요','현재 읽기는 새 유료 수집을 실행하지 않습니다.'],quality_insufficient:['원문 언어 품질을 검증하고 있어요','표본·원문 대조·한국어 recall과 현지어 precision을 각각 확인합니다.'],criteria_not_met:['이번에 선택한 기준을 통과한 곳이 없어요','원하면 비율·평점·평가 수 기준을 직접 바꿔 새 요청을 만들 수 있어요. 품질 기준은 유지합니다.']};
    if(states[meta.state])return {title:states[meta.state][0],note:states[meta.state][1]};
    return {title:type==='local_discovery'?'검증된 리뷰 자료를 준비하고 있어요':type==='landmark'?'이 도시의 유명함 근거를 준비하고 있어요':'이 조건의 참고 장소가 없어요',note:'다른 기준의 장소로 결과 수를 채우지 않았어요. 날짜·종류를 바꾸거나 원하는 장소를 직접 저장할 수 있습니다.'};
  }
  function sectionGroups(section){return [
    ['items','조건을 확인한 곳',''],['needs_confirmation','살펴볼 장소','방문일의 영업·예약 가능 여부는 상세에서 확인해 주세요.'],
    ['insufficient_data','근거 확인이 필요한 곳','추천 기준에 통과한 목록이 아닙니다.'],['excluded','이번 조건에서 제외한 곳','확인된 위반이나 직접 제외한 장소입니다.']
  ].flatMap(([key,title,note])=>{const all=section?.[key]||[];return [false,true].map(custom=>({key,title:custom?'직접 설정한 기준'+(key==='needs_confirmation'?' · 방문 전 확인':''):title,note:custom?'기본 엄격 기준과 다른 새 요청입니다. 지점·권한·언어 품질 기준은 유지합니다.':note,custom,items:all.filter(item=>(item.criteria_group==='custom_criteria')===custom)})).filter(g=>g.items.length);});}
  function capabilityFor(data,city){const list=Array.isArray(data)?data:data?.cities||data?.items||[];return list.find(value=>(value.city||value.city_id||value.id)===city)||null;}
  function capabilityState(value){if(!value)return {title:'이 도시의 리뷰 자료를 확인 중이에요',note:'확인되지 않은 자료를 0%로 표시하지 않아요.'};const enabled=value.enabled===true||value.observed_local?.enabled===true||value.observed_local?.status==='available'||value.language?.enabled===true||value.state==='available';return enabled?{title:'이 도시의 언어 근거를 사용할 수 있어요',note:'장소별 관측과 방문 조건은 각각 다시 확인합니다.'}:{title:'이 도시의 현지어 리뷰 추천은 준비 중이에요',note:'지점 연결·사용 범위·원문 언어 품질을 모두 통과한 자료만 표시해요. 유명한 곳은 별도로 살펴볼 수 있어요.'};}
  function init(context){ui=context;document.querySelector('#showReferencePlaces')?.addEventListener('click',()=>selectMode('reference'));}
  function clear(){serial++;capabilities=null;loading=false;selected='reference';criteria={minLocal:0.6,maxKorean:0.1,ordering:'evidence'};document.querySelector('#discoveryModes')?.replaceChildren();document.querySelector('#discoveryCapability')?.replaceChildren();}
  function filters(){return {minLocal:criteria.minLocal,maxKorean:criteria.maxKorean,ordering:criteria.ordering,discoveryMode:selected};}
  function restoreFilters(value){criteria={minLocal:safeNumber(value.minLocal,0.6),maxKorean:safeNumber(value.maxKorean,0.1),ordering:value.ordering==='nearby'?'nearby':'evidence'};if(modes[value.discoveryMode])selected=value.discoveryMode;}
  function restoreMode(mode){if(modes[mode])selected=mode;renderMode();}
  function requestFilters(){return {review_language_filter:{required:true,apply_only_if_qualified:true,apply_to:['local_discovery'],min_local_share:criteria.minLocal,max_korean_share:criteria.maxKorean},rating_filter:{enabled:true,min_rating:Number(document.querySelector('#recommendationMinRating').value),min_count:Number(document.querySelector('#recommendationMinCount').value),apply_to:['local_discovery']},ordering_profile:criteria.ordering};}
  function restoreRequest(request){criteria={minLocal:safeNumber(request.review_language_filter?.min_local_share,0.6),maxKorean:safeNumber(request.review_language_filter?.max_korean_share,0.1),ordering:request.ordering_profile==='nearby'?'nearby':'evidence'};}
  function filterSummary(options){return criteria.ordering==='nearby'?'가까운 곳 우선':'여행 조건으로 찾고 있어요';}
  function selectMode(type){if(!modes[type])return;selected=type;window.WorkspaceUX?.changed();ui.renderRecommendationResults();document.querySelector('#discoveryModes [aria-selected="true"]')?.focus({preventScroll:true});}
  function renderMode(){if(!ui)return;const host=document.querySelector('#discoveryModes');if(!host)return;const focusId=document.activeElement?.id?.startsWith('discovery-mode-')?document.activeElement.id:null;host.replaceChildren();host.setAttribute('role','tablist');host.setAttribute('aria-label','추천 근거');
    const tabs=[];for(const key of ['reference','local_discovery','landmark']){const mode=modes[key],tab=ui.button('',()=>selectMode(key),'discovery-mode');tab.id='discovery-mode-'+key;tab.setAttribute('role','tab');tab.setAttribute('aria-selected',String(selected===key));tab.setAttribute('aria-controls','recommendationResults');tab.tabIndex=selected===key?0:-1;tab.append(ui.make('strong','',mode.title),ui.make('span','hint',mode.note));tab.addEventListener('keydown',event=>{if(['ArrowRight','ArrowLeft','Home','End'].includes(event.key)){event.preventDefault();const order=['reference','local_discovery','landmark'];selectMode(order[event.key==='Home'?0:event.key==='End'?2:(order.indexOf(key)+(event.key==='ArrowRight'?1:-1)+3)%3]);}});tabs.push(tab);host.append(tab);}if(focusId)tabs.find(t=>t.id===focusId)?.focus({preventScroll:true});
    const panel=document.querySelector('#recommendationResults');panel?.setAttribute('role','tabpanel');panel?.setAttribute('aria-labelledby','discovery-mode-'+selected);
    const aux=document.querySelector('#showReferencePlaces');if(aux)aux.setAttribute('aria-pressed',String(selected==='reference'));
    const support=document.querySelector('#discoveryCapability');support?.replaceChildren();if(!support)return;
    const city=ui.ensureDiscoveryDraft()?.conditions?.city,value=capabilityFor(capabilities,city),copy=capabilityState(value);
    support.hidden=selected!=='local_discovery';
    if(selected==='local_discovery')support.append(ui.make('p','hint',copy.title+' · '+copy.note));
  }
  async function loadCapabilities(){if(!ui?.state.session?.authenticated||loading)return;const request=++serial,epoch=ui.state.epoch;loading=true;try{const data=await ui.api('/review-capabilities');if(request!==serial||epoch!==ui.state.epoch)return;capabilities=data;renderMode();}catch(error){if(request===serial&&epoch===ui.state.epoch){capabilities=null;renderMode();const host=document.querySelector('#discoveryCapability');host?.append(ui.make('p','hint','자료 준비 상태를 불러오지 못했습니다. 저장된 여행과 유명한 곳은 계속 확인할 수 있어요.'));}}finally{if(request===serial)loading=false;}}
  function quickFilters(host,conditions){const group=ui.make('div','discovery-category-chips');group.setAttribute('role','group');group.setAttribute('aria-label','장소 종류');for(const [key,label] of [['all','전체'],['restaurant','음식점'],['cafe','카페'],['attraction','볼거리']]){const chosen=conditions.categories||[],active=key==='all'?chosen.length===3:chosen.length===1&&chosen[0]===key;const b=ui.button(label,()=>ui.setDiscoveryOverride('categories',key==='all'?['restaurant','cafe','attraction']:[key]),'category-chip');b.setAttribute('aria-pressed',String(active));group.append(b);}host.append(group);}
  function qualityFields(host,input,options){host.append(ui.make('p','form-note field wide','현지어 리뷰 탭에 적용합니다. 원문·지점·사용권·언어 판별 품질은 완화할 수 없어요. 기본보다 완화하면 ‘직접 설정한 기준’에 따로 표시합니다.'));
    input.min_local=ui.field(host,'review_language_filter.min_local_share','현지어 하한 · 본문 전체 기준 %',100*(options.minLocal??0.6),{type:'number',min:0,max:100,required:true});
    input.max_korean=ui.field(host,'review_language_filter.max_korean_share','한국어 상한 · 미판별 포함 %',100*(options.maxKorean??0.1),{type:'number',min:0,max:100,required:true});
    input.min_rating=ui.field(host,'rating_filter.min_rating','최소 평점 · 같은 플랫폼 5점 척도',options.minRating,{type:'number',min:0,max:5,required:true});input.min_rating.step='0.1';
    input.min_count=ui.field(host,'rating_filter.min_count','최소 전체 평가 수',options.minCount,{type:'number',min:0,max:100000000,required:true});
    input.ordering=ui.field(host,'ordering_profile','추천 순서',options.ordering||'evidence',{select:[['evidence','근거별 기본 순서'],['nearby','가까운 곳 우선']],wide:true});
    return {review:{value:()=> 'required'},rating:{value:()=> 'required'}};
  }
  function readQuality(input){return {minLocal:Number(input.min_local.value)/100,maxKorean:Number(input.max_korean.value)/100,ordering:input.ordering.value};}
  function browseSection(sections){
    // A read-only all-places view also supports saved runs from the old UI.
    // Qualification stays on each item and the two evidence tabs remain separate.
    const result={items:[],needs_confirmation:[],insufficient_data:[],excluded:[]},seen=new Set();
    for(const key of Object.keys(result))for(const name of ['landmark','local_discovery','reference'])for(const item of sections?.[name]?.[key]||[]){if(seen.has(item.place_id))continue;seen.add(item.place_id);result[key].push(item);}
    return result;
  }
  function renderSections(host,run){const section=selected==='reference'?browseSection(run.result?.sections):run.result?.sections?.[selected]||{},meta=run.result?.section_status?.[selected]||{};
    const block=ui.make('section','recommendation-section stage2-section');block.append(ui.make('h2','',modes[selected].title));
    if(selected==='local_discovery'){const applied=run.result?.applied_constraints||{},language=applied.review_language_filter||{},rating=applied.rating_filter||{};block.append(ui.make('p','hint',`이 요청의 기준: 현지어 하한 ${percent(language.min_local_share??0.6)} · 한국어 상한 ${percent(language.max_korean_share??0.1)} · 평점 ${rating.min_rating??4.2}/5 · 전체 평가 ${rating.min_count??200}건. 판별 100건·미판별 10% 이하 등 품질 조건 유지.`));}
    const groups=sectionGroups(section),visible=groups.filter(g=>g.key!=='excluded');if(!visible.length){const empty=ui.make('div','panel empty');const copy=sectionStateCopy(selected,meta);empty.append(ui.make('h3','',copy.title),ui.make('p','hint',copy.note));ui.appendRecommendationReasons(empty,meta.reason_codes||section.reason_codes||[]);const actions=ui.make('div','actions');if(selected==='local_discovery')actions.append(ui.button('유명한 곳 살펴보기',()=>selectMode('landmark'),'secondary'));actions.append(ui.button('장소 직접 저장',()=>ui.bookmarkForm(),'text-button'));empty.append(actions);block.append(empty);}
    for(const group of groups){const render=container=>{if(group.key!=='items'||group.custom)container.append(ui.make('h3','',group.title+' · '+group.items.length+'곳'));if(group.note)container.append(ui.make('p','hint',group.note));const cards=ui.make('div','recommendation-grid');let previous=null;for(const item of group.items){const type=item.iconic_kind||item.iconic_evidence_type||item.evidence_group;if(selected==='landmark'&&type&&type!==previous){const tag=ui.make('h4','iconic-group-label',iconicTypes[type]||'출처 유형별 근거');cards.append(tag);previous=type;}const card=ui.recommendationCard(item,run,null,group.key!=='excluded');card.dataset.criteriaGroup=group.custom?'custom_criteria':'default';cards.append(card);}container.append(cards);};if(group.key==='excluded')block.append(ui.button('제외한 '+group.items.length+'곳과 이유',()=>ui.openDialog('이번 조건에서 제외한 곳',render),'text-button'));else{const container=ui.make('section','recommendation-evidence-group');render(container);block.append(container);}}
    host.append(block);
    window.DiscoveryFlow?.photos(visible.flatMap(group=>group.items));
  }
  function modelDetail(host,item){
    const data=item.ranking_diagnostics;if(!data)return;
    host.append(ui.make('p','form-note','선택한 취향·출발점과 확인된 장소 자료를 비교한 추천입니다. 점수는 순서를 정하기 위한 값이며 정확도나 만족할 확률이 아닙니다.'));
    const labels={core:'추천 구획의 근거',preference:'선택한 취향과의 유사도',proximity:data.proximity_reference==='city_center'?'도심 기준 직선거리':'출발점에서의 직선거리'};
    for(const [key,part] of Object.entries(data.components||{})){
      const value=key==='core'&&data.core_kind==='evidence_gate_only'?'자료 자격 통과':typeof part.value==='number'?Math.round(part.value*100)+'/100':'자료 미확인';
      host.append(ui.make('p','',`${labels[key]||key}: ${value} · 비중 ${Math.round(part.weight*100)}%`));
    }
    if(data.matched_tags?.length)host.append(ui.make('p','hint','일치하는 장소 태그: '+data.matched_tags.join(' · ')));
    if(data.rating){const r=data.rating;host.append(ui.make('p','hint',`평점 ${r.raw_rating}/5 · 전체 평가 ${r.count}건. 같은 플랫폼·도시·분류의 ${r.cohort_places}곳을 비교하며 평가 수가 적은 평점의 영향을 줄입니다.`));}
    if(data.missing_components?.length)host.append(ui.make('p','hint','미확인 성분은 점수를 채우거나 다른 항목으로 비중을 옮기지 않았어요.'));
    if(data.soft_avoid_multiplier===0.5)host.append(ui.make('p','hint','반영하도록 선택한 피드백으로 우선순위를 낮췄어요.'));
    host.append(ui.make('p','hint','직선거리는 도보시간이 아닙니다. 영업·인원·언어 자료 자격은 점수와 별도로 검사합니다.'),ui.make('p','hint',`모델 ${data.version} · ${data.profile} · 사용자 행동 학습 없음`));
  }
  function cardEvidence(card,item,run){if(!ui)return;if(item.ranking_diagnostics)card.append(ui.button('이 추천의 계산 근거',()=>ui.openDialog('이 장소를 추천한 기준',host=>modelDetail(host,item)),'text-button'));if(item.recommendation_type==='landmark'){const type=item.iconic_kind||item.iconic_evidence_type||item.evidence_group;if(type)card.append(ui.make('p','evidence-label',iconicTypes[type]||'출처 유형별 유명함 근거'));return;}if(item.recommendation_type!=='local_discovery')return;
    const evidence=item.review_evidence,data=evidenceData(evidence),box=ui.make('div','review-card-evidence');if(data){box.append(ui.make('strong','',`현지어 ${data.L}건 · 한국어 ${data.K}건`),ui.make('p','hint',`관측 본문 T ${data.T} · 판별 C ${data.C} · 미판별 U ${data.U}`));ui.reviewScope(box,evidence.coverage,evidence.checked_at,evidence.expires_at);box.append(ui.button('언어 근거 보기',()=>ui.discoveryPlaceDetail(item.place_id,run),'text-button'));}else box.append(ui.make('p','hint','표시 가능한 리뷰 언어 근거 미확인'));if(item.criteria_group==='custom_criteria')box.append(ui.make('span','badge','직접 설정한 기준'));else if(data&&item.strict_badge===true)box.append(ui.make('span','badge','기본 언어 기준 통과'));card.append(box);
  }
  function evidenceDetail(host,evidence,place,run){const section=ui.make('section','detail-section language-evidence');section.append(ui.make('h3','','최근 관측한 원문 리뷰 언어'));const data=evidenceData(evidence);for(const line of evidenceLines(evidence))section.append(ui.make('p',data?'':'muted',line));
    if(data){section.append(ui.make('p','hint',`미판별 비율 ${percent(data.unknown)}. 미판별의 가능 범위이며 통계적 신뢰구간이나 분류 오류 보정이 아닙니다.`));const total=evidence.place?.total_rating_count??evidence.total_rating_count??place.total_rating_count;section.append(ui.make('p','hint',`플랫폼 전체 평가 수: ${total??'미확인'} · 언어 비율의 분모가 아닙니다.`));}
    ui.reasonList(section,ui.reviewReasons(evidence||{}));ui.reviewScope(section,evidence?.coverage,evidence?.checked_at,evidence?.expires_at);ui.reviewAttribution(section,evidence?.attribution);const provenance=evidence?.provenance||{};const model=evidence?.detector_version||provenance.detector_version||evidence?.evaluation?.detector_version,policy=evidence?.policy_version||provenance.policy_version||evidence?.dependencies?.policy_version;section.append(ui.make('p','hint',`현지어 범위: ${(evidence?.local_languages||evidence?.evaluation?.local_languages||[]).join(' · ')||'도시 프로필 확인 필요'} · 추천 모델 ${run?.result?.section_models?.local_discovery||place.ranker_version||'저장된 실행에서 확인'}`));if(model||policy)section.append(ui.make('p','hint',`언어 판별기 ${model||'미확인'} · 정책 ${policy||'미확인'}`));section.append(ui.make('p','form-note','리뷰 원문의 언어를 관측한 결과입니다. 작성자의 거주지·국적·맛의 보장이 아니며, 공급자 자료 종료는 Google 전체 리뷰 확보를 뜻하지 않습니다.'));
    if(!data&&(place?.id||place?.place_id)){const request=ui.button('이 장소의 리뷰 검토 요청',async()=>{request.disabled=true;const epoch=ui.state.epoch;try{const result=await ui.api(ui.tripPath()+'/places/'+encodeURIComponent(place.place_id||place.id)+'/review-request',{method:'POST',body:{}});if(epoch!==ui.state.epoch)return;request.textContent=result.duplicate?'이미 검토 요청한 장소예요':'검토 요청을 접수했어요';}catch(error){if(epoch===ui.state.epoch){request.disabled=false;section.append(ui.make('p','error',error.message));}}},'secondary');section.append(request,ui.make('p','hint','검토 대기열에만 추가합니다. 유료 수집이나 알림 발송은 자동 실행하지 않아요.'));}host.append(section);
  }
  return {init,clear,filters,restoreFilters,restoreMode,requestFilters,restoreRequest,filterSummary,selectMode,renderMode,loadCapabilities,quickFilters,qualityFields,readQuality,renderSections,cardEvidence,modelDetail,evidenceDetail,evidenceData,evidenceLines,sectionGroups,browseSection,sectionStateCopy,capabilityFor,capabilityState};
});
