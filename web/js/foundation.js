/* Private trip workspace. Personal data lives in memory only; API responses are never cached. */
(() => {
  'use strict';
  const $ = s => document.querySelector(s);
  const $$ = s => [...document.querySelectorAll(s)];
  const make = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
  const button = (label, action, cls = 'secondary') => { const b = make('button', cls, label); b.type = 'button'; b.addEventListener('click', action); return b; };
  const showError = (el, message = '') => { el.textContent = message; el.hidden = !message; };
  const valueText = v => v == null || v === '' ? '미확인' : typeof v === 'object' ? JSON.stringify(v) : String(v);
  const uid = () => crypto.randomUUID();
  const kindMark = { '항공': '↗', '숙소': '⌂', '렌터카': '↔', '투어': '◎', '음식점': '♧', '기타': '◇' };
  const statusText = { queued:'대기 중', pending:'대기 중', running:'분석 중', processing:'분석 중', succeeded:'분석 완료', active:'분석 완료', ready:'분석 완료', partial:'일부 확인 필요', failed:'분석 실패', error:'분석 실패', duplicate:'중복 · 기존 메일 사용', rejected:'업로드 거절', needs_review:'확인 필요', extracted:'메일에서 추출', source_verified:'메일 근거 확인', user_confirmed:'직접 확인', confirmed:'확인됨', cancelled:'취소됨' };
  const state = { session:null, mailCapabilities:null, trips:[], trip:null, bookings:[], documents:[], history:[], tab:'trip', epoch:0, controllers:new Set(), uploads:[], files:[], asking:false, uploading:false, reprocessing:new Set(), jobs:new Map(), watchers:new Map(), intents:new Map(), reviews:{places:[],policies:[],runs:[],controls:null,evidence:[]}, discovery:{conditions:null,bookmarks:[],loaded:false,serial:0,packs:[]}, itineraries:{runs:[],active:null,displayed:null,serial:0,submitting:false,loaded:false,day:null,selected:new Map()}, recommendations:{runs:[],active:null,displayed:null,serial:0,submitting:false,optionsDirty:false,loaded:false} };
  let toastTimer, sessionExpiryTimer, reviewPollTimer, discoveryPollTimer, recommendationPollTimer, itineraryPollTimer, dialogReturnFocus, dialogBusy = false, sessionCheck = false, installPrompt = null;
  const channel = 'BroadcastChannel' in window ? new BroadcastChannel('travel-inbox-session') : null;

  let toastRemaining = 0, toastStarted = 0;
  function resumeNotice() {
    clearTimeout(toastTimer);
    if (document.hidden || $('#notice').hidden || toastRemaining <= 0) return;
    toastStarted = Date.now();
    toastTimer = setTimeout(() => { $('#notice').hidden = true; toastRemaining = 0; }, toastRemaining);
  }
  function notice(message) {
    clearTimeout(toastTimer); $('#notice').textContent = message; $('#notice').hidden = false;
    toastRemaining = 5500; resumeNotice();
  }
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { clearTimeout(toastTimer); toastRemaining = Math.max(0, toastRemaining - (Date.now() - toastStarted)); }
    else resumeNotice();
  });
  document.addEventListener('pointerdown', () => { document.documentElement.dataset.input = 'pointer'; }, {passive:true});
  document.addEventListener('keydown', () => { document.documentElement.dataset.input = 'keyboard'; });
  function closeJobsDialog() { $('#jobsDialog').close(); $('#openJobs').focus(); }
  $('#openJobs').addEventListener('click', () => { $('#jobsDialog').showModal(); $('#closeJobs').focus(); });
  $('#closeJobs').addEventListener('click', closeJobsDialog);
  $('#jobsDialog').addEventListener('cancel', event => { event.preventDefault(); closeJobsDialog(); });
  function cancelRequests() { state.epoch++; for (const c of state.controllers) c.abort(); state.controllers.clear(); }
  function closeDialog(force = false) { if (dialogBusy && !force) return; $('#dialog').close(); window.WorkspaceUX?.dialogClosed();window.ProductTools?.resumeExposure(); $('#dialogBody').replaceChildren(); $('#dialogBody').classList.remove('comparison-body','discovery-filter-body'); $('#dialogTitle').textContent = ''; if(window.WorkspaceUX)window.WorkspaceUX.restoreFocus(dialogReturnFocus);else dialogReturnFocus?.isConnected&&dialogReturnFocus.focus(); dialogBusy = false; if(state.recommendations.conditionsDraft){state.recommendations.conditionsDraft=Boolean(state.discovery.dirty);renderRecommendationResults();} }
  function openDialog(title, build) { const returnTo=$('#dialog').open?dialogReturnFocus:document.activeElement;if(!$('#dialog').open)window.WorkspaceUX?.rememberFocus(returnTo);if ($('#dialog').open) closeDialog(true); dialogReturnFocus = returnTo; $('#dialogTitle').textContent = title; build($('#dialogBody')); window.ProductTools?.suspendExposure(); $('#dialog').showModal(); const initial=[...$('#dialogBody').querySelectorAll('form input:not([disabled]),form select:not([disabled]),form textarea:not([disabled]),.more-menu button')].find(n=>n.getClientRects().length)||$('#dialogTitle');initial.focus({preventScroll:true});$('#dialog').scrollTop=0; }
  $('#closeDialog').addEventListener('click', () => closeDialog());
  $('#dialog').addEventListener('keydown',event=>{if(event.key!=='Tab')return;const nodes=[...$('#dialog').querySelectorAll('button,a[href],input,select,textarea,summary,[tabindex]')].filter(n=>!n.disabled&&n.tabIndex>=0&&n.getClientRects().length);const first=nodes[0],last=nodes.at(-1);if(!first)return;if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}});
  $('#dialog').addEventListener('cancel', e => { e.preventDefault(); closeDialog(); });
  $('#dialog').addEventListener('click', e => { if (e.target === $('#dialog')) { const r = e.target.getBoundingClientRect(); if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) closeDialog(); } });

  function clearScope() {
    state.workspaceSuspended=true;
    window.AccommodationTools?.clear();
    window.TravelTools?.clear();
    window.ProductTools?.clear();
    window.DiscoveryExperience?.clear(); window.ReviewLab?.clear();
    clearReviewScope(); clearDiscoveryScope(); clearRecommendationScope(); clearItineraryScope();
    stopWatchers(); state.jobs.clear(); $('#jobList').replaceChildren(); $('#jobsPanel').hidden=true; $('#jobsDialog').close(); delete $('#bookingDays').dataset.choices; delete $('#itineraryDays').dataset.choices;
    cancelRequests(); closeDialog(true);
    state.bookings = []; state.documents = []; state.history = []; state.uploads = []; state.files = []; state.asking = false; state.uploading = false; state.reprocessing.clear();
    ['#bookingDays','#itineraryDays','#bookingList','#documentList','#thread','#uploadResults','#selectedFiles'].forEach(s => $(s).replaceChildren());
    $('#question').value = ''; $('#mailFiles').value = ''; $('#dateFilter').value = ''; $('#kindFilter').value = ''; $('#needsReview').checked = false;
    ['#questionError','#uploadError','#pageError'].forEach(s => showError($(s)));
    $('#sendQuestion').disabled = false; $('#uploadButton').disabled = false; $('#question').readOnly = false;
  }
  function clearPrivate() {
    clearTimeout(sessionExpiryTimer); sessionExpiryTimer = null;
    window.WorkspaceUX?.clear();purgeJourneyMemory();clearScope(); state.mailCapabilities=null;renderMailCapabilities();state.intents.clear(); state.trip = null; state.trips = []; state.session = null;
    $('#app').hidden = true; $('#tripSelect').replaceChildren(); $('#tripSummary').replaceChildren();
    $('#usageSummary').replaceChildren(); $('#tripTitle').textContent = '나의 여행'; $('#tripSubtitle').textContent = ''; $('#tripScope').textContent = ''; $('#accountName').textContent = ''; $('#sessionExpiry').textContent = ''; $('#bookingCount').textContent = '';
    $$('.scope-text').forEach(n => n.textContent = ''); clearTimeout(toastTimer); toastRemaining = 0; $('#notice').hidden = true; $('#notice').textContent = ''; document.title = '고잉';
  }
  function authScreen(message = '') {
    $('#boot').hidden = true; $('#app').hidden = true; $('#auth').hidden = false;
    window.scrollTo({top:0,behavior:'instant'});
    const configured = state.session?.auth_configured === true;
    $('#loginButton').disabled = !configured;
    $('#authMessage').textContent = configured ? '초대받은 이메일 계정으로 로그인해 주세요.' : '로그인 제공자 설정을 준비 중입니다. 운영자 설정 후 초대받은 계정으로 이용할 수 있어요.';
    showError($('#authError'), message);
  }
  function expire(message = '세션이 만료되었습니다. 다시 로그인해 주세요.') {
    const config = state.session?.auth_configured;
    window.TravelTools?.purge().catch(()=>{});
    clearPrivate(); state.session = { authenticated:false, auth_configured:config === true }; authScreen(message);
  }
  function sessionDeadlinePassed() {
    if(!state.session?.authenticated)return false;
    const deadline=Date.parse(state.session.expires_at || '');
    if(Number.isFinite(deadline)&&deadline<=Date.now()){
      expire('세션이 만료되어 화면의 개인 정보를 지웠습니다. 다시 로그인해 주세요.');
      return true;
    }
    return false;
  }
  function armSessionExpiry() {
    clearTimeout(sessionExpiryTimer);sessionExpiryTimer=null;
    if(sessionDeadlinePassed())return false;
    const deadline=Date.parse(state.session?.expires_at || '');
    if(state.session?.authenticated&&Number.isFinite(deadline)){
      // Background tabs can throttle timers; visibility/focus recheck the absolute deadline.
      sessionExpiryTimer=setTimeout(()=>{if(!sessionDeadlinePassed())armSessionExpiry();},Math.min(Math.max(deadline-Date.now(),1),2147483647));
    }
    return true;
  }
  async function api(path, options = {}) {
    const controller = new AbortController(); state.controllers.add(controller);
    const headers = new Headers(options.headers || {});
    const method = options.method || 'GET';
    if (!['GET','HEAD'].includes(method) && state.session?.csrf_token) headers.set('X-CSRF-Token', state.session.csrf_token);
    let body = options.body;
    if (body != null && !(body instanceof FormData)) { headers.set('Content-Type','application/json'); body = JSON.stringify(body); }
    try {
      const response = await fetch('/api/v2' + path, { ...options, method, headers, body, credentials:'same-origin', cache:'no-store', signal:controller.signal });
      if (options.blob && response.ok) return await response.blob();
      if (response.status !== 204 && !(response.headers.get('content-type') || '').includes('json')) {
        const err = new Error('서버가 다시 시작 중이거나 잠시 응답하지 않습니다. 약 1분 뒤 다시 연결해 주세요. 저장·분석 요청은 자동으로 재전송하지 않았습니다.');
        err.status = response.status; err.code = 'SERVER_UNAVAILABLE'; throw err;
      }
      const data = response.status === 204 ? {} : await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = data.error || {};
        const err = new Error(detail.message || (typeof data.detail === 'string' ? data.detail : `요청을 처리하지 못했습니다 (${response.status}).`));
        err.status = response.status; err.code = detail.code; err.details = detail.details || (Array.isArray(data.detail) ? data.detail : []); err.requestId = detail.request_id;
        if (response.status === 401 && path !== '/session') expire();
        throw err;
      }
      return data;
    } finally { state.controllers.delete(controller); }
  }
  function fail(err, target = $('#pageError')) { if (err.name !== 'AbortError' && err.status !== 401) showError(target, err.message); }
  async function allPages(path) {
    const items = [], seen = new Set(); let cursor = null;
    do {
      const page = await api(path + (cursor ? (path.includes('?') ? '&' : '?') + 'cursor=' + encodeURIComponent(cursor) : ''));
      items.push(...(page.items || [])); cursor = page.next_cursor;
      if (cursor && seen.has(cursor)) throw new Error('목록을 이어 불러오지 못했습니다. 다시 확인해 주세요.');
      if (cursor) seen.add(cursor);
    } while (cursor);
    return items;
  }
  const tripPath = (trip = state.trip) => '/trips/' + encodeURIComponent(trip.id);
  const documentPath = (id, trip = state.trip) => tripPath(trip) + '/documents/' + encodeURIComponent(id);
  function tripDates(t) { return `${t.start_date || '날짜 미정'} — ${t.end_date || '날짜 미정'}`; }
  function effective(b) { return b.effective ? { ...b, ...b.effective } : b; }
  const isReview = b => ['needs_review','provisional','unknown'].includes(b.status) || Boolean(b.needs_review) || Boolean(b.time_conflicts?.length) || !b.date;
  function renderTrip() {
    if(state.tab==='product')window.ProductTools?.load();
    if(['today','preparation'].includes(state.tab)&&state.trip)window.TravelTools?.load(state.tab);
    const t = state.trip; $('#tripSelect').replaceChildren();
    if (!state.trips.length) $('#tripSelect').append(new Option('아직 여행이 없어요', ''));
    state.trips.forEach(x => $('#tripSelect').append(new Option(x.title + ' · ' + (x.start_date || '날짜 미정'), x.id)));
    $('#tripSelect').value = t?.id || ''; $('#tripSelect').disabled = !t;
    $('#tripTitle').textContent = t?.title || '나의 여행'; $('#tripSubtitle').textContent = t ? tripDates(t) : '새 여행을 만들고 예약을 정리해 보세요.';
    $('#noTrip').hidden = !!t; $('#bookingArea').hidden = !t; $('#tripSummary').replaceChildren();
    ['#editTrip','#addBooking','#refreshBookings','#deleteTrip','#mailFiles','#uploadButton','#sendQuestion','#question','#refreshDocuments'].forEach(s => $(s).disabled = !t);
    $$('.scope-text').forEach(n => n.textContent = t ? `${t.title} · ${tripDates(t)}` : '먼저 여행을 만들어 주세요.');
    renderHomePlaces();
    if (t) {
      const p = t.party || t.conditions?.party || { adults:1, children:[] };
      const cities = tripCityLabel(t);
      for (const [name,v] of [['여행 도시',cities || '도시 미정'],['함께하는 인원',`성인 ${p.adults || 1}명${p.children?.length ? ` · 아동 ${p.children.length}명` : ''}`],['저장된 예약',`${state.bookings.length}건`]]) {
        const card = make('div','summary-card'),value=make('strong','',v); if(name==='여행 도시')value.id='tripCitySummary'; card.append(make('span','',name),value); $('#tripSummary').append(card);
      }
      document.title = `${t.title} · 고잉`;
    }
    renderTripCityLabels();
  }
  function tripCityLabel(trip){return (trip?.stops||[]).map(stop=>destinationLabel(stop.city)).join(' → ');}
  function renderTripCityLabels(){
    const trip=state.trip,cities=tripCityLabel(trip);
    $('#tripScope').textContent=trip?`${tripDates(trip)}${cities?' · '+cities:''}`:'';
    const summary=$('#tripCitySummary');if(summary)summary.textContent=cities||'도시 미정';
  }
  async function loadTrips(preferred) {
    const epoch = state.epoch; const trips = await allPages('/trips'); if (epoch !== state.epoch) return;
    state.trips = trips; await selectTrip(trips.find(t => t.id === (preferred||navigationMemory().trip_id)) || trips[0] || null);
  }
  async function selectTrip(trip) {
    await window.WorkspaceUX?.leave();
    clearScope(); state.workspaceScroll={};state.workspaceReturn={};state.trip = trip;navigationMemory({trip_id:trip?.id||null,tab:state.tab}); window.ProductTools?.bind(); renderTrip(); renderBookings(); renderDocuments(); resetChat();
    if (state.tab === 'reviews') loadReviews().catch(e => fail(e, $('#reviewError')));
    if (state.tab === 'mail') loadMailCapabilities();
    if (!trip) { renderDiscoveryConditions(); renderBookmarks(); renderRecommendationResults(); renderItinerary(); return; }
    const epoch = state.epoch; $('#bookingList').replaceChildren(make('p','loading','예약을 불러오는 중…'));
    const results = await Promise.allSettled([loadBookings(),loadDocuments(),loadJobs(),loadDiscovery()]);
    if (epoch !== state.epoch) return;
    results.forEach(r => { if (r.status === 'rejected') fail(r.reason); });
    await window.WorkspaceUX?.load();if(epoch!==state.epoch)return;state.workspaceSuspended=false;
    if (state.tab === 'explore') loadRecommendations().catch(e => fail(e, $('#recommendationError')));
    if (state.tab === 'itinerary') loadItineraries().catch(e=>fail(e,$('#itineraryError')));
  }
  async function loadBookings() {
    if (!state.trip) return; const epoch = state.epoch;
    const bookings = await allPages(tripPath() + '/bookings'); if (epoch !== state.epoch) return;
    const fresh=await api(tripPath()); if(epoch!==state.epoch)return; state.trip=fresh; state.trips=state.trips.map(t=>t.id===fresh.id?fresh:t);
    state.bookings = bookings.map(effective); renderTrip(); renderBookings();
  }
  async function loadDocuments() {
    if (!state.trip) return; const epoch = state.epoch;
    const docs = await allPages(tripPath() + '/documents'); if (epoch !== state.epoch) return;
    state.documents = docs; renderDocuments();
  }
  function setTab(tab) {
    window.WorkspaceUX?.beforeTab();
    state.navigationRevision=(state.navigationRevision||0)+1;
    const aliases={home:'trip',reservations:'mail',offline:'today'};tab=aliases[tab]||tab;
    if(tab==='product')window.ProductTools?.load();
    if(['preparation','today'].includes(tab))window.TravelTools?.load(tab);
    const parent={today:'itinerary',preparation:'mail'}[tab]||tab;
    state.tab = tab;navigationMemory({trip_id:state.trip?.id||null,tab}); $$('.view').forEach(n => n.hidden = n.id !== 'view-' + tab);
    $$('[data-tab]').forEach(n => { if ((n.closest('.sidebar')?n.dataset.tab===parent:n.dataset.tab===tab)) n.setAttribute('aria-current','page'); else n.removeAttribute('aria-current'); });
    $('#openMore').toggleAttribute('data-current', !['trip','explore','itinerary','mail'].includes(parent));
    $('#openMore').setAttribute('aria-label', !['trip','explore','itinerary','mail'].includes(parent) ? '더보기 · ' + (document.querySelector('.sidebar [data-tab="'+tab+'"]')?.textContent.trim()||'여행 도구') : '더보기');
    clearTimeout(reviewPollTimer); clearTimeout(discoveryPollTimer); clearTimeout(recommendationPollTimer); clearTimeout(itineraryPollTimer);
    if (tab === 'explore') { Promise.all([loadDiscovery(),loadRecommendations()]).then(()=>window.DiscoveryFlow?.enter()).catch(e=>fail(e,$('#recommendationError'))); }
    if (tab === 'itinerary') {loadDiscovery().catch(e=>fail(e,$('#itineraryError')));loadItineraries().catch(e=>fail(e,$('#itineraryError')));}
    if (tab === 'reviews') { loadReviews().catch(e => fail(e, $('#reviewError'))); loadDiscovery().catch(e=>fail(e,$('#reviewError'))); }
    if (tab === 'mail') loadMailCapabilities();
    $$('.section-switch[data-sections]').forEach(n=>n.hidden=!n.dataset.sections.split(',').includes(tab));
    if(window.WorkspaceUX)window.WorkspaceUX.afterTab();else window.scrollTo({top:0,behavior:'instant'});
  }
  $$('[data-tab]').forEach(b => b.addEventListener('click', () => setTab(b.dataset.tab)));
  $('#openMore').addEventListener('click', () => openDialog('여행 도구', body => {
    body.append(make('p','hint','여행에 필요한 도구를 골라 주세요.'));
    const menu=make('div','more-menu');
    const descriptions={today:'오늘 일정과 이 기기의 오프라인 저장',preparation:'예약 오픈일·할 일·문의문',product:'예상 비용·방문 기록·정보 신고',ask:'내 예약에 대해 근거와 함께 질문',mail:'예약 메일 추가·분석·출처 확인',reviews:'관측한 리뷰 언어와 수집 근거',settings:'계정·화면·서비스 설정'};
    $$('.sidebar .nav-secondary:not(.nav-alias)').forEach(source => {
      const target=source.dataset.tab;
      const item=button('',()=>{closeDialog();setTab(target);$('#main').focus({preventScroll:true});},'more-menu-item');
      const copy=make('span');copy.append(make('strong','',source.textContent.trim()),make('span','hint',descriptions[target]));
      item.append(source.querySelector('svg').cloneNode(true),copy);
      if(state.tab===target)item.setAttribute('aria-current','page');menu.append(item);
    });
    body.append(menu);
  }));
  $('#tripSelect').addEventListener('change', e => selectTrip(state.trips.find(t => t.id === e.target.value)).catch(fail));
  $('#refreshBookings').addEventListener('click', () => loadBookings().then(() => notice('예약을 새로 불러왔습니다.')).catch(fail));
  $('#refreshDocuments').addEventListener('click', () => loadDocuments().catch(fail));

  function field(grid, name, label, value = '', options = {}) {
    const wrap = make('div','field' + (options.wide ? ' wide' : '')); const id = 'field-' + name.replaceAll('.','-');
    const lab = make('label','',label); lab.htmlFor = id;
    const input = make(options.select ? 'select' : options.textarea ? 'textarea' : 'input');
    input.id = id; input.name = name; input.dataset.field = name;
    if (options.select) { options.select.forEach(o => input.append(new Option(Array.isArray(o) ? o[1] : o,Array.isArray(o) ? o[0] : o))); if(value && ![...input.options].some(o=>o.value===String(value))) input.append(new Option(String(value),String(value))); }
    else if (!options.textarea) input.type = options.type || 'text';
    if(input.type==='date'||input.type==='datetime-local'){input.min=input.type==='date'?'0001-01-01':'0001-01-01T00:00';input.max=input.type==='date'?'9999-12-31':'9999-12-31T23:59';}
    input.value = value == null ? '' : value; if (options.required) input.required = true;
    if (options.min != null) input.min = options.min; if (options.max != null) input.max = options.max;
    if (options.maxLength) input.maxLength = options.maxLength; if (options.placeholder) input.placeholder = options.placeholder;
    const error = make('p','error'); error.id = id + '-error'; error.hidden = true; input.setAttribute('aria-describedby', error.id);
    input.addEventListener('invalid',()=>{for(let details=input.closest('details');details;details=details.parentElement?.closest('details'))details.open=true;error.hidden=false;error.textContent=input.type==='date'?'연도 네 자리와 실제 날짜를 확인해 주세요 (0001~9999).':input.validationMessage;input.setAttribute('aria-invalid','true');});input.addEventListener('input',()=>{error.hidden=true;input.removeAttribute('aria-invalid');});
    wrap.append(lab,input); if (options.hint) {const hint=make('p','hint',options.hint);hint.id=id+'-hint';input.setAttribute('aria-describedby',hint.id+' '+error.id);wrap.append(hint);} wrap.append(error); grid.append(wrap); return input;
  }
  function formError(form, err) {
    form.querySelectorAll('[aria-invalid]').forEach(n => n.removeAttribute('aria-invalid')); form.querySelectorAll('.field .error').forEach(n => showError(n));
    showError(form.querySelector('.form-error'), err.status === 409 ? '다른 곳에서 내용이 변경되었습니다. 입력은 유지했어요. 최신 내용을 확인한 후 다시 저장해 주세요.' : err.message);
    let details = err.details;
    if (!Array.isArray(details)) details = details?.field || details?.field_path || details?.path ? [details] : details?.fields || details?.errors || [];
    if (!Array.isArray(details)) details = Object.entries(details).map(([field,message]) => ({field,message}));
    const unmatched=[];
    details.forEach(d => {
      let key = d.field || d.field_path || d.path || (d.loc || []).filter(v => v !== 'body').join('.');
      if(Array.isArray(key))key=key.filter(v=>v!=='body').join('.');if(typeof key==='string')key=key.replace(/^commands\.\d+\.(?:(?:add|move|remove|lock|unlock)\.)?/,'');
      let input = [...form.elements].find(x => x.dataset.field === key || x.name === key);
      if(input?.type==='hidden')input=input.closest('.field')?.querySelector('input[type="checkbox"]')||input;
      const eventKey=/^events\.(\d+)\.(.+)$/.exec(key);
      if(!input&&eventKey){const row=form.querySelectorAll('.event-row')[Number(eventKey[1])];input=[...(row?.querySelectorAll('[data-field]')||[])].find(x=>x.dataset.field.endsWith('.'+eventKey[2]));}
      const message=d.message || d.msg || '입력값을 확인해 주세요.';
      if (input) { input.setAttribute('aria-invalid','true'); showError(input.closest('.field').querySelector('.error'),message); }else unmatched.push(message);
    });
    if(unmatched.length&&err.status!==409)showError(form.querySelector('.form-error'),[err.message,...new Set(unmatched)].join(' '));
    const invalid=form.querySelector('[aria-invalid="true"]');if(invalid?.closest('details'))invalid.closest('details').open=true;const panel=invalid?.closest('.filter-panel');if(panel?.hidden)form.querySelector('[aria-controls="'+panel.id+'"]')?.click();invalid?.focus();
  }
  function formBase(body, submitText, handler) {
    const form = make('form'); form.noValidate = false;
    const grid = make('div','form-grid'); const error = make('p','form-error error'); error.setAttribute('role','alert'); error.hidden = true;
    const footer = make('div','form-footer'); const submit = make('button','primary',submitText); submit.type = 'submit'; footer.append(button('취소', () => closeDialog()),submit);
    form.append(grid,error,footer); body.append(form);
    form.addEventListener('submit', async e => { e.preventDefault(); if (dialogBusy || !form.reportValidity()) return; dialogBusy = true; submit.disabled = true; showError(error); try { await handler(form); } catch (err) { if (err.name !== 'AbortError' && err.status !== 401) formError(form,err); } finally { dialogBusy = false; submit.disabled = form.dataset.blocked==='true'; } });
    return { form, grid, footer };
  }
  function localError(form, name, message) { const err = new Error(message); err.details = [{field:name,message}]; formError(form,err); return false; }
  function confirmAction(title, text, label, action) {
    openDialog(title, body => { body.append(make('p','muted',text)); const error = make('p','error'); error.setAttribute('role','alert'); const footer = make('div','form-footer'); const yes = button(label, async () => { if (dialogBusy) return; dialogBusy = true; yes.disabled = true; try { await action(); closeDialog(true); } catch (err) { fail(err,error); } finally { dialogBusy = false; yes.disabled = false; } },'danger'); footer.append(button('취소', () => closeDialog()),yes); body.append(error,footer); });
  }
  function tripForm(trip = null) {
    openDialog(trip ? '여행 수정' : '어디로, 언제 떠나나요?', body => {
      let expectedVersion = trip?.version;
      const {form,grid,footer} = formBase(body,trip ? '변경 저장' : '여행 만들기', async () => {
        const stops = [...stopHost.children].map((row,i) => ({...(row.dataset.stopId?{id:row.dataset.stopId}:{}),city:row.querySelector('[data-key="city"]').value.trim(),sequence:i+1,start_date:stopHost.children.length===1?start.value:row.querySelector('[data-key="start_date"]').value || start.value,end_date:stopHost.children.length===1?end.value:row.querySelector('[data-key="end_date"]').value || end.value,timezone:row.querySelector('[data-key="timezone"]').value.trim(),base_location:row.querySelector('[data-key="base_location"]').value.trim() || null}));
        if (end.value < start.value) return localError(form,'end_date','종료일은 시작일 이후여야 합니다.');
        const ages = children.value.trim() ? children.value.split(',').map(s => s.trim()).map(s => s === '?' ? null : s === '' ? NaN : Number(s)) : [];
        if (ages.some(a => a !== null && (!Number.isInteger(a) || a < 0 || a > 17))) return localError(form,'party.children','나이는 0~17 또는 ?로 입력해 주세요. 여러 명은 쉼표로 구분합니다.');
        const payload = {title:title.value.trim()||((stops[0]?.city||'새')+' 여행'),start_date:start.value,end_date:end.value,party:{adults:Number(adults.value),children:ages.map(age => ({age})),children_status:ages.length?'present':childStatus.value},stops};
        if (trip) {
          const baseline={title:trip.title,start_date:trip.start_date,end_date:trip.end_date,party:trip.party||trip.conditions?.party||{adults:1,children:[]},stops:(trip.stops||[]).map((s,i)=>({id:s.id,city:s.city,sequence:i+1,start_date:s.start_date,end_date:s.end_date,timezone:s.timezone,base_location:s.base_location||null}))};
          Object.keys(baseline).forEach(k=>{if(JSON.stringify(payload[k])===JSON.stringify(baseline[k]))delete payload[k];});
          if(!Object.keys(payload).length){notice('변경된 내용이 없습니다.');return;}
          payload.expected_version=expectedVersion;
        }
        try {
          const saved = await api(trip ? tripPath(trip) : '/trips',{method:trip?'PATCH':'POST',body:payload});
          const outside = state.bookings.filter(b => b.date && (b.date < start.value || b.date > end.value));
          closeDialog(true); await loadTrips(saved.id || trip?.id); notice(trip && outside.length ? `여행을 수정했습니다. 기간 밖 예약 ${outside.length}건은 그대로 보존했습니다.` : trip ? '여행을 수정했습니다.' : '여행을 만들었어요. 이제 가고 싶은 곳을 찾아보세요.');
        } catch (err) {
          if (err.status === 409 && !form.querySelector('.conflict-action')) {
            const reload = button('최신 여행 정보 확인',async () => { try { const latest = await api(tripPath(trip)); const p = make('p','form-note',`현재 서버: ${latest.title} · ${tripDates(latest)} · ${tripCityLabel(latest)} · 성인 ${latest.party?.adults||1}명 / 아동 ${latest.party?.children?.length||0}명. 입력한 내용은 유지했습니다. 내용을 확인하고 다시 저장하면 이 최신 버전에 적용됩니다.`); form.insertBefore(p,footer); expectedVersion = latest.version; reload.remove(); } catch (e) { formError(form,e); } },'secondary conflict-action'); footer.prepend(reload);
          } throw err;
        }
      });
      const title = field(grid,'title','여행 이름',trip?.title || '',{wide:true,maxLength:120,placeholder:'비우면 도시 이름으로 만들어요'});
      const start = field(grid,'start_date','시작일',trip?.start_date || '',{type:'date',required:true});
      const end = field(grid,'end_date','종료일',trip?.end_date || '',{type:'date',required:true});
      const party = trip?.party || trip?.conditions?.party || {};
      const adults = field(grid,'party.adults','성인 인원',party.adults || 1,{type:'number',required:true,min:1,max:30});
      const childStatus=field(grid,'party.children_status','아동 동반',party.children_status||(party.children?.length?'present':'unknown'),{select:[['unknown','아직 미확인'],['none','아동 없음'],['present','아동 있음']]});
      const children = field(grid,'party.children','아동 나이',(party.children || []).map(c => c.age == null ? '?' : c.age).join(', '),{hint:'예: 7, 12 / 나이를 모르면 ?로 입력해 주세요.'});
      const optional=make('section','discovery-extra field wide');optional.append(make('h3','','여행 이름 · 아동 인원'));const optionalGrid=make('div','form-grid');optionalGrid.append(title.closest('.field'),childStatus.closest('.field'),children.closest('.field'));const syncChildren=()=>{children.closest('.field').hidden=childStatus.value!=='present';children.required=childStatus.value==='present';if(childStatus.value==='none')children.value='';};childStatus.addEventListener('change',syncChildren);syncChildren();optional.append(optionalGrid);grid.append(optional);
      const impact = make('div','field wide'); grid.append(impact);
      function showImpact(){ impact.replaceChildren(); if(!trip || !start.value || !end.value)return; const outside=state.bookings.filter(b=>b.date&&(b.date<start.value||b.date>end.value)); if(outside.length){ impact.append(make('p','form-note',`변경한 기간 밖의 예약 ${outside.length}건도 그대로 보존합니다.`));const list=make('ul','warning-list');outside.forEach(b=>list.append(make('li','',`${b.date} · ${b.provider||'이름 미확인'}`)));impact.append(list); }}
      start.addEventListener('input',showImpact);end.addEventListener('input',showImpact);showImpact();
      const stopWrap = make('div','field wide'); const h = make('div','section-heading'); h.append(make('h3','','방문 도시')); const stopHost = make('div');
      function renumberStops(){stopHost.querySelectorAll('.row-heading').forEach(h=>h.hidden=stopHost.children.length===1);[...stopHost.children].forEach((row,i)=>row.querySelectorAll('[data-key]').forEach(input=>{const name=`stops.${i}.${input.dataset.key}`;const id='field-'+name.replaceAll('.','-');input.name=name;input.dataset.field=name;input.id=id;input.closest('.field').querySelector('label').htmlFor=id;input.closest('.field').querySelector('.error').id=id+'-error';input.setAttribute('aria-describedby',id+'-error');}));}
      function addStop(stop = {}) {
        const row = make('div','stop-row');if(stop.id)row.dataset.stopId=stop.id; const index = stopHost.children.length;
        const heading = make('div','row-heading'); heading.append(make('h3','','도시 구간'),button('구간 삭제',() => { if (stopHost.children.length > 1) {row.remove();renumberStops();} else notice('방문 도시는 최소 1개 필요합니다.'); },'text-button'));
        const g = make('div','form-grid'); const fields = [ ['city','도시',stop.city || '',{required:true}], ['timezone','시간대',stop.timezone || '',{required:true,placeholder:'도시를 선택하면 자동 입력',hint:'100개 도시의 현지 시간대를 자동 입력합니다. 목록 밖 도시는 직접 지정할 수 있어요.'}], ['start_date','구간 시작일',stop.start_date || '',{type:'date',hint:'비우면 여행 시작일'}], ['end_date','구간 종료일',stop.end_date || '',{type:'date',hint:'비우면 여행 종료일'}], ['base_location','숙소·출발 기준점',stop.base_location || '',{wide:true}] ];
        fields.forEach(([key,label,v,o]) => { const i = field(g,`stops.${index}.${key}`,label,v,o); i.dataset.key = key; });
        const cityInput=g.querySelector('[data-key="city"]'),zoneInput=g.querySelector('[data-key="timezone"]');
        const suggestions=make('datalist');suggestions.id='cities-'+uid();for(const city of destinationCatalog){const option=make('option');option.value=city.name_ko;option.label=city.name_en+' · '+city.country_code;suggestions.append(option);}cityInput.setAttribute('list',suggestions.id);cityInput.placeholder='마드리드, Tokyo, Paris…';g.append(suggestions);cityPicker(cityInput,zoneInput);
        const updateCityZone=()=>{const city=knownDestination(cityInput.value);if(city){zoneInput.value=city.timezone;zoneInput.closest('.field').querySelector('.hint').textContent='도시의 현지 시간대를 자동으로 적용했어요.';}};cityInput.addEventListener('input',updateCityZone);cityInput.addEventListener('change',updateCityZone);
        const dates=make('section','field wide stay-date-fields');dates.append(make('h4','','도시별 체류 정보')); const dateGrid=make('div','form-grid');for(const key of ['start_date','end_date','timezone','base_location'])dateGrid.append(g.querySelector('[data-key="'+key+'"]').closest('.field'));dates.append(dateGrid);g.append(dates);
        row.append(heading,g); stopHost.append(row); renumberStops();for(const section of stopHost.querySelectorAll('details'))section.open=stopHost.children.length>1;
      }
      h.append(button('＋ 도시 추가',() => addStop(),'text-button')); stopWrap.append(h,stopHost); grid.prepend(stopWrap);
      (trip?.stops?.length ? trip.stops : [{}]).forEach(addStop);
      if (trip) { const note = make('p','form-note','날짜를 변경해도 예약은 삭제하지 않습니다. 여행 기간 밖의 예약은 목록에 별도로 표시합니다.'); body.prepend(note); }
    });
  }
  $('#createTrip').addEventListener('click', () => tripForm()); $('#emptyCreateTrip').addEventListener('click', () => tripForm()); $('#editTrip').addEventListener('click', () => state.trip && tripForm(state.trip));
  $('#deleteTrip').addEventListener('click', () => { const t = state.trip; if (!t) return; confirmAction('이 여행을 삭제할까요?',`${t.title}의 예약과 메일에 더 이상 접근할 수 없게 됩니다. 외부 서비스의 실제 예약은 취소되지 않습니다.`, '여행 삭제',async () => { await window.TravelTools?.remove(t.id);await api(tripPath(t),{method:'DELETE'});window.WorkspaceUX?.forget(t.id); await loadTrips(); notice('여행 접근을 차단하고 삭제를 요청했습니다.'); }); });

  function calendarDate(value) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value || '') || value.startsWith('0000-')) return null;
    const stamp = Date.parse(value + 'T00:00:00Z');
    return Number.isFinite(stamp) && new Date(stamp).toISOString().slice(0,10) === value ? stamp : null;
  }
  function journeyDays(start, end, limit = 31) {
    const first = calendarDate(start), last = calendarDate(end);
    if (first == null || last == null || last < first || (last-first)/86400000 + 1 > limit) return [];
    return Array.from({length:(last-first)/86400000+1}, (_, index) => new Date(first+index*86400000).toISOString().slice(0,10));
  }
  function renderDayStrip(host, choices, selected, onSelect) {
    const signature = JSON.stringify(choices);
    // Preserve the active button and horizontal scroll when only the selection changes.
    if (host.dataset.choices !== signature) {
      host.replaceChildren(); host.dataset.choices = signature;
      for (const choice of choices) {
        const control = button('', () => onSelect(choice.value), 'day-choice');
        control.dataset.date = choice.value;
        control.setAttribute('aria-label', choice.label);
        control.append(make('span','day-kicker',choice.top), make('strong','',choice.title), make('span','day-caption',choice.bottom));
        host.append(control);
      }
    }
    for (const control of host.children) control.setAttribute('aria-pressed', String(control.dataset.date === selected));
  }
  function dayChoice(date, index, count, noun) {
    const weekday = ['일','월','화','수','목','금','토'][new Date(calendarDate(date)).getUTCDay()];
    return {value:date, top:`${index+1}일차`, title:`${Number(date.slice(5,7))}.${Number(date.slice(8,10))}`, bottom:`${weekday} · ${count}${noun}`, label:`${date} ${weekday}요일 · ${count}${noun}`};
  }
  function renderBookingDays() {
    const host = $('#bookingDays'), trip = state.trip;
    host.hidden = !trip;
    if (!trip) { host.replaceChildren(); delete host.dataset.choices; return; }
    const dates = journeyDays(trip.start_date, trip.end_date);
    const choices = [{value:'',top:'여행 전체',title:'전체',bottom:`예약 ${state.bookings.length}건`,label:'모든 날짜의 예약'}];
    dates.forEach((date,index) => choices.push(dayChoice(date,index,state.bookings.filter(booking=>bookingOnDate(booking,date)).length,'건')));
    renderDayStrip(host, choices, $('#dateFilter').value, date => { $('#dateFilter').value = date; renderBookings(); });
  }
  function bookingOnDate(b,date){
    const events=(b.events||[]).filter(e=>e.start_local||e.end_local);
    const eventMatch=events.some(e=>{const start=(e.start_local||'').slice(0,10),end=(e.end_local||e.start_local||'').slice(0,10);return start ? start<=date&&end>=date : end===date;});
    const summaryMatch=b.date&&b.date<=date&&(b.date_end||b.date)>=date;
    return eventMatch || ((['숙소','hotel','lodging','accommodation'].includes(b.kind)||!events.length)&&summaryMatch);
  }
  function bookingKindLabel(kind) {
    if (kind == null) return null;
    const value=String(kind).trim(), alias=value.toLowerCase().replace(/[\s-]+/g,'_');
    const labels={flight:'항공',air:'항공',hotel:'숙소',lodging:'숙소',accommodation:'숙소',car_rental:'렌터카',rental_car:'렌터카',rentcar:'렌터카',tour:'투어',activity:'투어',restaurant:'음식점',dining:'음식점',cafe:'음식점',other:'기타',etc:'기타','식당':'음식점','호텔':'숙소','항공편':'항공'};
    return Object.hasOwn(labels,alias) ? labels[alias] : value;
  }
  function renderBookings() {
    renderBookingDays();
    const host = $('#bookingList'); host.replaceChildren(); if (!state.trip) return;
    const date = $('#dateFilter').value, kind = $('#kindFilter').value, review = $('#needsReview').checked;
    const list = state.bookings.filter(b => (!date || bookingOnDate(b,date)) && (!kind || bookingKindLabel(b.kind) === kind) && (!review || isReview(b))).sort((a,b) => (a.date || '9999').localeCompare(b.date || '9999') || (a.time || '').localeCompare(b.time || ''));
    $('#bookingFilterBrief').textContent = [date,kind,review?'확인 필요만':''].filter(Boolean).join(' · ');
    $('#bookingCount').textContent = `${list.length} / ${state.bookings.length}건`;
    if (!list.length) { const empty = make('div','panel empty'); empty.append(make('h2','',state.bookings.length ? '이 조건에 맞는 예약이 없어요' : '아직 예약이 없어요'),make('p','',state.bookings.length ? '날짜나 종류 필터를 바꿔보세요.' : '예약 메일을 추가하거나 예약을 직접 입력해 보세요.')); if (!state.bookings.length) empty.append(button('메일 추가하기',() => setTab('mail'),'primary')); else empty.append(button('모든 예약 보기',()=>{ $('#dateFilter').value=''; $('#kindFilter').value=''; $('#needsReview').checked=false; renderBookings(); },'secondary')); host.append(empty); return; }
    let previous;
    list.forEach(b => {
      const day = b.date || '날짜 확인 필요'; if (day !== previous) { host.append(make('h3','date-heading',day)); previous = day; }
      const card = button('',() => bookingDetail(b),'booking-card'); const icon = make('span','kind-mark',kindMark[bookingKindLabel(b.kind)] || '◇'); icon.setAttribute('aria-hidden','true');
      const mid = make('div','booking-body'); mid.append(make('strong','',b.provider || '이름 확인 필요'),make('p','',[bookingKindLabel(b.kind),b.location].filter(Boolean).join(' · ')));
      const badges = make('div','badges'); if (!b.document_id) badges.append(make('span','badge','직접 입력'));
      if (isReview(b)) badges.append(make('span','badge warn','확인 필요'));
      if (b.date && (b.date < state.trip.start_date || b.date > state.trip.end_date)) badges.append(make('span','badge warn','여행 기간 밖'));
      if (Object.keys(b.overrides || {}).length) badges.append(make('span','badge','사용자 수정'));
      mid.append(badges); card.append(icon,mid,make('span','booking-time',b.time || '시간 미확인'),make('span','arrow','›')); host.append(card);
    });
  }
  $('#bookingFilters').addEventListener('submit',e => e.preventDefault()); ['#dateFilter','#kindFilter','#needsReview'].forEach(s => $(s).addEventListener('change',renderBookings));
  $('#clearFilters').addEventListener('click',() => { $('#dateFilter').value=''; $('#kindFilter').value=''; $('#needsReview').checked=false; renderBookings(); });

  function pair(host,label,value,note) { const row = make('div','detail-pair'); const dd = make('dd','',valueText(value)); if (note) dd.append(make('small','',note)); row.append(make('dt','',label),dd); host.append(row); }
  async function downloadDocument(id, filename, trip = state.trip) {
    try { const epoch = state.epoch; const blob = await api(documentPath(id,trip) + '/content',{blob:true}); if (epoch !== state.epoch) return;
      const url = URL.createObjectURL(blob); const a = make('a'); a.href=url; a.download=(filename || '예약-원문.txt').replace(/[\\/]/g,'_'); document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url),1000);
    } catch (err) { if (err.status === 404) notice('원문을 찾을 수 없습니다. 삭제되었거나 접근 권한이 없어요.'); else if (err.name !== 'AbortError' && err.status !== 401) notice(err.message); }
  }
  async function viewDocument(id, filename, trip = state.trip, back = null) {
    const epoch=state.epoch;
    const loading=make('p','loading','원문을 안전하게 불러오는 중…');
    openDialog('예약 메일 원문',body=>body.append(loading));
    try {
      const blob=await api(documentPath(id,trip)+'/content',{blob:true});
      if(blob.size>1024*1024)throw new Error('1 MiB를 넘는 원문은 화면에 표시하지 않습니다. 다운로드로 확인해 주세요.');
      const content=await blob.text();
      if(epoch!==state.epoch||!loading.isConnected||!$('#dialog').open)return;
      const body=$('#dialogBody');body.replaceChildren();
      body.append(make('h3','',filename||'예약 메일'),make('p','hint','업로드한 파일을 텍스트로 표시합니다. 메일의 링크·HTML·스크립트는 실행하지 않습니다. .eml 파일은 메일 헤더나 인코딩된 본문이 포함될 수 있습니다.'));
      const preview=make('pre','source-preview',content);preview.tabIndex=0;preview.setAttribute('aria-label','예약 메일 원문 텍스트');body.append(preview);
      const footer=make('div','form-footer');if(back)footer.append(button('이전 내용으로',back,'text-button'));footer.append(button('원문 다운로드',()=>downloadDocument(id,filename,trip)),button('닫기',()=>closeDialog(),'primary'));body.append(footer);
      preview.focus();
    } catch(err) {
      if(epoch!==state.epoch||!loading.isConnected)return;
      loading.className='error';loading.setAttribute('role','alert');loading.textContent=err.status===404?'원문을 찾을 수 없습니다. 삭제되었거나 접근 권한이 없어요.':err.message;
      const actions=make('div','form-footer');if(back)actions.append(button('이전 내용으로',back));actions.append(button('다시 확인',()=>viewDocument(id,filename,trip,back),'primary'));$('#dialogBody').append(actions);
    }
  }
  function renderBookingTimeConflicts(body, conflicts){
    if(!conflicts?.length)return;
    const box=make('div','form-note');box.setAttribute('role','status');
    box.append(make('strong','','예약 시각을 함께 확인해 주세요'),make('p','','대표 날짜·시각과 구간별 시각이 달라요. 예약 수정에서 두 값을 맞추기 전에는 일정을 확정할 수 없습니다.'));
    conflicts.forEach(conflict=>{const summary=conflict.summary||{};const side=conflict.side==='end'?'종료':'시작';box.append(make('p','',`${side} · 대표: ${summary.date||'날짜 미확인'} ${summary.time||'시각 미확인'} · 구간: ${conflict.event_local?.replace('T',' ')||'대응 구간 확인 필요'}`));});
    body.append(box);
  }
  function openBookingFromJourney(id,eventId=null){
    const booking=state.bookings.find(item=>item.id===id);if(!booking){notice('연결된 예약을 찾지 못했어요. 최신 예약을 불러온 뒤 다시 확인해 주세요.');return;}
    if(eventId){bookingForm(booking);const input=[...$('#dialogBody').querySelectorAll('[data-field]')].find(n=>n.dataset.field===`events.${eventId}.start_local`&&n.getClientRects().length)||$('#field-time');input?.focus();input?.scrollIntoView({block:'nearest'});}else bookingDetail(booking);
  }
  function bookingDetail(booking) {
    const b = effective(booking), t = state.trip;
    openDialog(b.provider || '예약 상세', body => {
      renderBookingTimeConflicts(body,b.time_conflicts);
      const labels = {kind:'예약 종류',status:'확인 상태',date:'시작 날짜',date_end:'종료 날짜',time:'시작 시각',time_end:'종료 시각',location:'장소',confirmation_number:'예약번호',refund_policy:'취소·환불'};
      const dl = make('dl','detail-list');
      Object.entries(labels).forEach(([key,label]) => {
        let note=''; const extracted = b.extracted || b.extracted_json || {};
        if (Object.hasOwn(extracted,key) && JSON.stringify(extracted[key]) !== JSON.stringify(b[key])) note = '원문 추출: ' + valueText(key==='status' ? statusText[extracted[key]]||extracted[key] : key==='kind' ? bookingKindLabel(extracted[key]) : extracted[key]) + ' · 사용자 수정 적용';
        pair(dl,label,key === 'status' ? statusText[b[key]] || b[key] : key==='kind' ? bookingKindLabel(b[key]) : b[key],note);
      }); body.append(dl);
      if (b.events?.length) { body.append(make('h3','detail-section','예약 구간')); b.events.forEach((e,i) => { const box=make('div','source-card'); box.append(make('strong','',`${i+1}. ${({outbound:'가는 편',return:'오는 편',flight:'항공 이동',stay:'숙박',pickup:'대여·반납',activity:'방문'})[e.event_type] || e.event_type || bookingKindLabel(b.kind) || '구간'}`)); const ev=make('dl','detail-list'); pair(ev,'출발·시작',e.start_local,e.start_timezone || '시간대 미확인'); pair(ev,'도착·종료',e.end_local,e.end_timezone || '시간대 미확인'); if(e.location)pair(ev,'장소',e.location); box.append(ev); body.append(box); }); }
      const conflicts = b.conflicts || b.review_conflicts || [];
      if (conflicts.length) { body.append(make('h3','detail-section','새 추출값과 수정값 비교')); conflicts.forEach(c => body.append(make('p','form-note',`${c.field_path || c.field}: 새 추출 ${valueText(c.new_extracted ?? c.extracted_value ?? c.extracted)} · 내 수정 ${valueText(c.override_value ?? c.override)}`))); }
      const source = make('div','source-card');
      if (b.document_id) { source.append(make('p','',b.source_file || b.source?.filename || '예약 메일 원문'),button('원문 보기',() => viewDocument(b.document_id,b.source_file,t,()=>bookingDetail(b)),'primary'),button('원문 다운로드',() => downloadDocument(b.document_id,b.source_file,t),'secondary'),button('다시 분석',() => reprocessDocument(b.document_id,b.source_file),'text-button')); }
      else source.append(make('p','muted','직접 입력한 예약입니다. 메일 원문은 없습니다.'));
      body.append(source); const footer=make('div','form-footer'); footer.append(button('예약 수정',()=>bookingForm(b),'primary'),button('이 예약 삭제',()=>confirmAction('이 예약을 삭제할까요?','같은 메일의 다른 예약은 유지됩니다. 외부 서비스의 실제 예약은 취소되지 않습니다.','예약 삭제',async()=>{await api(tripPath(t)+'/bookings/'+encodeURIComponent(b.id),{method:'DELETE'});await loadBookings();notice('이 예약을 삭제했습니다.');}),'danger-outline')); body.append(footer);
    });
  }
  function bookingForm(booking = null) {
    if (!state.trip) return; const trip=state.trip; const b=booking ? effective(booking) : {}; let expectedVersion=b.version;
    openDialog(booking ? '예약 내용 수정' : '예약 직접 추가',body => {
      body.append(make('p','form-note',booking ? '원문 추출값은 보존하고 수정값을 별도로 저장합니다. 시각을 모르면 비워두세요.' : '날짜만 알아도 저장할 수 있어요. 시각·시간대를 모르면 임의로 채우지 않습니다.'));
      const inputs = {}, eventInputs = [];let correction=null;const kind=bookingKindLabel(b.kind);const semantic=kind==='음식점'?{date:'방문 날짜',time:'방문 시각',date_end:'방문 종료 날짜 · 선택',time_end:'방문 종료 시각 · 선택'}:kind==='숙소'?{date:'체크인 날짜',time:'체크인 시각',date_end:'체크아웃 날짜',time_end:'체크아웃 시각'}:kind==='항공'?{date:'대표 출발 날짜',time:'대표 출발 시각',date_end:'대표 도착 날짜',time_end:'대표 도착 시각'}:{};
      const {form,grid,footer}=formBase(body,booking?'수정 저장':'예약 추가',async()=>{
        if(correction&&!correction.prepare())return;
        const v={}; Object.entries(inputs).forEach(([key,input]) => v[key]=input.value.trim() || null);
        if(v.date_end&&v.date&&v.date_end<v.date)return localError(form,'date_end','종료 날짜는 시작 날짜 이후여야 합니다.');
        const events=eventInputs.map(({inputs:ei,event})=>{const x={};Object.entries(ei).forEach(([key,input])=>x[key]=input.value.trim()||null);if(event?.id)x.id=event.id;return x;});
        let payload;
        if(booking){ const changes=[];Object.entries(v).forEach(([field_path,value])=>{if(field_path!=='reason'&&JSON.stringify(value)!==JSON.stringify(b[field_path]||null))changes.push({field_path,value});});
          eventInputs.forEach(({inputs:ei,event})=>Object.entries(ei).forEach(([key,input])=>{const value=input.value.trim()||null;if(event?.id&&JSON.stringify(value)!==JSON.stringify(event[key]||null))changes.push({field_path:`events.${event.id}.${key}`,value});}));
          if(!changes.length){notice('변경된 내용이 없습니다.');return;}payload={expected_version:expectedVersion,changes,reason:v.reason};
        } else {delete v.reason;payload={...v,events:events.filter(e=>e.start_local||e.end_local)};}
        try {const saved=await api(tripPath(trip)+'/bookings'+(booking?'/'+encodeURIComponent(b.id):''),{method:booking?'PATCH':'POST',body:payload});closeDialog(true);await loadBookings();notice(booking?'수정 내용을 저장했습니다.':'예약을 추가했습니다.'); if(booking){const current=state.bookings.find(x=>x.id===(saved.id||b.id));if(current)bookingDetail(current);}}
        catch(err){if(err.status===409&&!form.querySelector('.conflict-action')){const reload=button('최신 예약과 비교',async()=>{try{const list=await allPages(tripPath(trip)+'/bookings');const latest=effective(list.find(x=>x.id===b.id)||{});if(!latest.id)throw new Error('예약이 삭제되었거나 접근할 수 없습니다.');const comparison=make('div','source-card');comparison.append(make('h3','','변경된 서버 내용과 내 입력 비교'));
          const dl=make('dl','detail-list');
          const labels={kind:'예약 종류',status:'확인 상태',provider:'예약 이름·업체',date:'시작 날짜',date_end:'종료 날짜',time:'시작 시각',time_end:'종료 시각',location:'장소',confirmation_number:'예약번호',refund_policy:'취소·환불',event_type:'구간 종류',start_local:'시작 현지 날짜·시각',end_local:'종료 현지 날짜·시각',start_timezone:'시작 시간대',end_timezone:'종료 시간대'};
          const renderValue=(key,value)=>valueText(key==='status'?(statusText[value]||value):key==='kind'?bookingKindLabel(value):value);
          function compareValue(label,key,oldValue,currentValue,inputValue){
            pair(dl,label,`기존 값: ${renderValue(key,oldValue)}\n서버 최신 값: ${renderValue(key,currentValue)}\n내 입력: ${renderValue(key,inputValue)}`);
          }
          Object.keys(inputs).filter(k=>k!=='reason').forEach(k=>{
            if(JSON.stringify(latest[k]??null)!==JSON.stringify(b[k]??null))compareValue(labels[k]||k,k,b[k],latest[k],inputs[k].value.trim()||null);
          });
          const oldEvents=new Map((b.events||[]).map(event=>[event.id,event]));
          const currentEvents=new Map((latest.events||[]).map(event=>[event.id,event]));
          const eventIds=new Set([...oldEvents.keys(),...currentEvents.keys()]);
          let removedEditedEvent=false;
          [...eventIds].forEach((id,index)=>{
            const oldEvent=oldEvents.get(id),currentEvent=currentEvents.get(id),entry=eventInputs.find(item=>item.event?.id===id);
            const label=`구간 ${index+1} · ${{outbound:'가는 편',return:'오는 편',flight:'항공 이동',stay:'숙박',activity:'방문',pickup:'대여·반납'}[event.event_type]||kind||'예약'}`;
            if(!oldEvent){pair(dl,label,'서버에서 새로 추가된 구간입니다. 이 폼에서는 변경하지 않습니다.');}
            if(!currentEvent){pair(dl,label,'서버에서 삭제된 구간입니다. 입력은 유지했지만 삭제된 구간에 수정값을 적용할 수 없습니다.');if(entry)removedEditedEvent=true;}
            ['event_type','start_local','end_local','start_timezone','end_timezone','location'].forEach(key=>{
              if(JSON.stringify(oldEvent?.[key]??null)!==JSON.stringify(currentEvent?.[key]??null)){
                const entered=entry?.inputs[key] ? entry.inputs[key].value.trim()||null : oldEvent?.[key]??null;
                compareValue(`${label} · ${labels[key]}`,key,oldEvent?.[key],currentEvent?.[key],entered);
              }
            });
          });
          comparison.append(dl,make('p','hint',removedEditedEvent?'삭제된 구간이 있어 이 편집을 그대로 적용할 수 없습니다. 입력을 확인한 뒤 취소하고 최신 예약을 다시 열어 주세요.':'입력은 바꾸지 않았습니다. 세 값을 비교한 뒤 직접 다시 저장하면 입력한 수정값을 최신 버전에 적용합니다.'));
          form.insertBefore(comparison,footer);
          if(removedEditedEvent){form.dataset.blocked='true';form.querySelector('[type="submit"]').disabled=true;}else expectedVersion=latest.version;
          reload.remove();}catch(e){formError(form,e);}},'secondary conflict-action');footer.prepend(reload);}throw err;}
      });
      const fields=[['kind','예약 종류',b.kind||'기타',{select:Object.keys(kindMark).map(label=>[b.kind&&label===bookingKindLabel(b.kind)?b.kind:label,label]),required:true}],['status','확인 상태',b.status||'user_confirmed',{select:[['needs_review','확인 필요'],['user_confirmed','사용자가 직접 확인'],...(b.document_id?[['source_verified','메일 근거 확인']]:[]),['cancelled','취소됨']]}],['provider','예약 이름·업체',b.provider||'',{wide:true,required:true,maxLength:300}],['date','시작 날짜',b.date||'',{type:'date'}],['date_end','종료 날짜',b.date_end||'',{type:'date'}],['time','시작 시각',b.time||'',{type:'time'}],['time_end','종료 시각',b.time_end||'',{type:'time'}],['location','장소',b.location||'',{wide:true,maxLength:1000}],['confirmation_number','예약번호',b.confirmation_number||'',{wide:true,maxLength:200}],['refund_policy','취소·환불 규정',b.refund_policy||'',{wide:true,textarea:true,maxLength:5000}]];
      fields.forEach(([key,label,v,o])=>inputs[key]=field(grid,key,semantic[key]||label,v,o));
      const evHost=make('div','field wide');evHost.append(make('h3','detail-section','구간별 시각과 시간대'),make('p','hint','대표 시각과 대응하는 구간의 변경안을 저장 전에 함께 보여드려요. 왕복 항공은 아래에서 해당 편만 수정하세요. 출발지·도착지 시간대는 각각 유지합니다.'));
      const rows=make('div');evHost.append(rows);
      let eventIndex=0;
      function addEvent(event={}){const index=eventIndex++;const row=make('div','event-row');const head=make('div','row-heading');head.append(make('h3','',`구간 ${index+1}`));const g=make('div','form-grid');const ei={};
        const f=[['start_local','시작 현지 날짜·시각',event.start_local||'',{placeholder:'2026-11-07T19:30 또는 2026-11-07'}],['end_local','종료 현지 날짜·시각',event.end_local||'',{placeholder:'2026-11-07T21:00 또는 2026-11-07'}],['start_timezone','시작 시간대',event.start_timezone||'',{placeholder:'Asia/Tokyo'}],['end_timezone','종료 시간대',event.end_timezone||'',{placeholder:'Europe/Madrid'}],['location','구간 장소',event.location||'',{wide:true}]];
        f.forEach(([key,label,value,o])=>ei[key]=field(g,`events.${event.id||index}.${key}`,label,value,o));
        const ref={inputs:ei,event};eventInputs.push(ref);if(!booking)head.append(button('구간 삭제',()=>{eventInputs.splice(eventInputs.indexOf(ref),1);row.remove();},'text-button'));row.append(head,g);rows.append(row);}
      (b.events||[]).forEach(addEvent);if(!booking)evHost.append(button('＋ 구간 추가',()=>addEvent(),'text-button'));grid.append(evHost);
      if(booking)inputs.reason=field(grid,'reason','수정 이유','',{wide:true,maxLength:500,placeholder:'예: 예약 변경 메일 확인'});
      if(booking&&typeof window!=='undefined'&&window.WorkspaceUX)correction=window.WorkspaceUX.bookingCorrection({booking:b,inputs,eventInputs,form,footer,make});
    });
  }
  $('#addBooking').addEventListener('click',()=>bookingForm());

  function mailAnalysisDescription(capabilities){
    if(!capabilities)return {label:'분석 방식 확인 중',description:'메일에 적힌 내용을 예약으로 정리합니다.'};
    if(capabilities.unavailable)return {label:'설정을 확인하지 못했어요',description:'잠시 후 예약 화면을 다시 열어 주세요. 저장된 메일과 예약은 유지됩니다.'};
    if(capabilities.analysis_mode==='local')return {label:'기본 분석 · 추가 비용 없음',description:'날짜·업체 등 명시된 항목을 정리합니다. 복잡한 형식과 빠진 정보는 직접 확인해 주세요.'};
    return {label:'AI 상세 분석',description:'메일 내용을 분석해 예약으로 정리합니다. 사용 한도가 적용되며, 저장 후 원문과 비교해 주세요.'};
  }
  function renderMailCapabilities(){
    const host=$('#mailAnalysisInfo');if(!host)return;const copy=mailAnalysisDescription(state.mailCapabilities);
    host.replaceChildren(make('strong','mail-analysis-label',copy.label),make('p','hint',copy.description));
  }
  async function loadMailCapabilities(){
    const epoch=state.epoch;if(!state.session?.authenticated)return;renderMailCapabilities();
    try{const data=await api('/mail-capabilities');if(epoch!==state.epoch)return;state.mailCapabilities=data;}
    catch(error){if(epoch!==state.epoch||error.status===401||error.name==='AbortError')return;state.mailCapabilities={unavailable:true};}
    renderMailCapabilities();
  }
  function documentProcessing(id){
    return state.reprocessing.has(id)||[...state.jobs.values()].some(job=>['queued','running'].includes(job.state)&&[...(job.submission?.accepted||[]),...(job.files||[])].some(file=>file.document_id===id));
  }
  function documentAnalysisLabel(document){
    const generation=document.latest_generation;
    if(generation?.parse_version?.startsWith('local-mail')&&generation.status==='active'&&['active','needs_review','succeeded','ready'].includes(document.status))return '기본 분석 완료 · 내용 확인 필요';
    return statusText[document.status]||document.status||'저장됨';
  }
  function renderDocuments(){
    const host=$('#documentList');host.replaceChildren();if(!state.trip){host.append(make('p','muted','여행을 먼저 만들어 주세요.'));return;}
    if(!state.documents.length){host.append(make('p','muted','추가한 메일이 여기에 보입니다.'));return;}
    state.documents.forEach(d=>{
      const row=make('div','document-row'),mid=make('div');
      mid.append(make('strong','',d.display_filename||d.filename||'예약 메일'),make('p','hint',documentAnalysisLabel(d)));
      const latest=d.latest_generation;
      if(latest?.status==='failed'&&d.active_generation_id)mid.append(make('p','hint','다시 분석하지 못했습니다. 이전 예약과 직접 수정한 내용은 유지됩니다.'));
      if(latest?.status==='processing')mid.append(make('p','hint','새 분석을 처리 중입니다.'));
      const errorCode=d.error_code||latest?.error_code;if(errorCode)mid.append(make('p','error',fileError(errorCode)));
      const actions=make('div','actions');
      actions.append(button('원문 보기',()=>viewDocument(d.id,d.display_filename)),button('다운로드',()=>downloadDocument(d.id,d.display_filename),'text-button'));
      const retry=button(documentProcessing(d.id)?'분석 중…':'다시 분석',()=>reprocessDocument(d.id,d.display_filename),'text-button');retry.disabled=documentProcessing(d.id);actions.append(retry);
      if(['LOCAL_EXTRACTION_UNSUPPORTED','MAIL_BODY_EMPTY'].includes(errorCode))actions.append(button('예약 직접 입력',()=>bookingForm(),'text-button'));
      row.append(mid,actions);host.append(row);
    });
  }
  function fileError(code){return ({LOCAL_EXTRACTION_UNSUPPORTED:'이 메일은 기본 분석으로 예약을 구분하기 어려워요. 원문은 보관했습니다. 예약을 직접 입력해 주세요.',MAIL_BODY_EMPTY:'메일 본문을 찾지 못했어요. 첨부 파일만 있는 메일은 지원하지 않습니다.',AI_KEY_NOT_CONFIGURED:'AI 연결이 준비되지 않았습니다. 기본 분석을 사용하거나 운영 설정을 확인해 주세요.',UNSUPPORTED_TYPE:'지원하는 형식은 .txt와 .eml입니다.',UNSUPPORTED_EXTENSION:'지원하는 형식은 .txt와 .eml입니다.',MIME_MISMATCH:'확장자와 파일 형식이 일치하지 않습니다.',EMPTY_FILE:'파일이 비어 있습니다.',INVALID_ENCODING:'UTF-8 텍스트 파일로 저장해 주세요.',INVALID_EMAIL:'메일 형식을 확인할 수 없습니다.',INVALID_CONTENT:'메일 본문으로 사용할 수 없는 파일입니다.',PROCESSING_FAILED:'분석 또는 검색 준비에 실패했습니다. 기존 예약은 유지됩니다.',FILE_TOO_LARGE:'파일당 최대 1 MiB입니다.',PARSE_FAILED:'예약 내용을 분석하지 못했습니다. 원문을 확인한 뒤 다시 시도해 주세요.',EMBEDDING_FAILED:'검색 준비에 실패했습니다. 기존 예약은 유지됩니다.',AUTH_REQUIRED:'다시 로그인해 주세요.',PROVIDER_UNAVAILABLE:'외부 요청을 보내지 못했습니다. 이 파일만 다시 시도할 수 있어요.',PROVIDER_OUTCOME_UNKNOWN:'공급자 응답·과금 여부를 확인하고 있습니다.',CALL_OUTCOME_UNKNOWN:'과금 확인 전에는 다시 실행하지 않습니다.',BUDGET_NOT_CONFIGURED:'운영자가 단가와 예산을 설정해야 합니다.',BUDGET_EXHAUSTED:'사용 가능한 외부 호출 예산을 모두 사용했습니다.',GLOBAL_BUDGET_STOPPED:'운영 예산 검토로 외부 호출이 중단되었습니다.',SEARCH_REBUILDING:'검색 자료를 복구 중입니다. 날짜별 예약 조회는 계속 사용할 수 있어요.',INDEX_REBUILD_INPUT_REQUIRED:'기존 메일의 검색 자료를 함께 준비해야 합니다. 작업 내역에서 실패한 파일을 재시도하면 완료된 분석부터 이어집니다.',REEXTRACTION_REQUIRED:'기존 메일에 저장된 분석 자료가 없습니다. 해당 메일을 다시 분석해 주세요.',CHECKPOINT_INCOMPATIBLE:'분석 설정이 변경되었습니다. 저장된 메일에서 다시 분석해 주세요.',AMBIGUOUS_MATCH:'이전 예약과 새 추출 결과의 대응을 확인해 주세요.'})[code]||code||'다시 시도해 주세요.';}
  function chooseFiles(files){if(!state.trip)return;state.files=[...files];$('#selectedFiles').replaceChildren();state.files.forEach(f=>{const row=make('div','file-row');row.append(make('strong','',f.name),make('span','status',`${Math.ceil(f.size/1024)} KiB`));$('#selectedFiles').append(row);});showError($('#uploadError'));}
  $('#mailFiles').addEventListener('change',e=>chooseFiles(e.target.files));
  ['dragenter','dragover'].forEach(type=>$('#dropzone').addEventListener(type,e=>{e.preventDefault();if(state.trip)$('#dropzone').classList.add('is-over');}));
  ['dragleave','drop'].forEach(type=>$('#dropzone').addEventListener(type,e=>{e.preventDefault();$('#dropzone').classList.remove('is-over');if(type==='drop'&&!state.uploading)chooseFiles(e.dataTransfer.files);}));
  function mailReviewReasonText(reasons){
    const labels={REQUEST_NOT_CONFIRMATION:'변경 요청은 확정된 예약이 아니에요. 원문에서 확정 여부를 확인해 주세요.',RESERVATION_CANCELLED_REVIEW:'취소 관련 내용이 있어요. 실제 취소 여부를 확인하고 예약 상태를 수정해 주세요.',DATE_NEEDS_REVIEW:'방문 날짜를 확정하지 못했어요. 결제일·취소 기한과 구분해 확인해 주세요.',AMBIGUOUS_LOCAL_TIME:'현지 시각이 모호해요. 시간대와 실제 예약 시각을 확인해 주세요.',EVENT_ORDER_UNCONFIRMED:'구간의 시작·종료 순서를 확인해 주세요.'};
    return [...new Set(reasons||[])].map(reason=>labels[reason]).filter(Boolean).join(' ');
  }
  function renderUploads(){const host=$('#uploadResults');host.replaceChildren();if(!state.uploads.length)return;host.append(make('h2','','파일별 처리 결과'));state.uploads.forEach(r=>{const row=make('div','file-row');const mid=make('div');mid.append(make('strong','',r.filename||'예약 메일'),make('p','hint',r.analysis_mode==='local'&&['succeeded','active','needs_review'].includes(r.state)?'기본 분석 완료 · 내용 확인 필요':statusText[r.state]||r.state||'대기 중'));if(r.error_code||r.reason)mid.append(make('p','error',fileError(r.error_code||r.reason)));const reviewNote=mailReviewReasonText(r.review_reasons);if(reviewNote)mid.append(make('p','hint',reviewNote));if(r.bookings_count!=null)mid.append(make('p','hint',`예약 ${r.bookings_count}건`));row.append(mid);if(['failed','error','rejected'].includes(r.state)&&!['LOCAL_EXTRACTION_UNSUPPORTED','MAIL_BODY_EMPTY'].includes(r.error_code||r.reason)&&!budgetCodes.has(r.error_code||r.reason)){row.append(button('이 파일만 재시도',()=>{if(r.document_id)retryUpload(r);else if(r.file)uploadFiles([r.file]);else{$('#mailFiles').click();}},'text-button'));}if(r.document_id&&['LOCAL_EXTRACTION_UNSUPPORTED','MAIL_BODY_EMPTY'].includes(r.error_code||r.reason))row.append(button('원문 보기',()=>viewDocument(r.document_id,r.filename),'text-button'),button('예약 직접 입력',()=>bookingForm(),'text-button'));if(r.bookings_count>0&&['succeeded','active','needs_review'].includes(r.state))row.append(button('예약 확인',()=>{$('#dateFilter').value='';$('#kindFilter').value='';$('#needsReview').checked=false;renderBookings();$('#bookingArea').scrollIntoView({block:'start'});$('#bookingArea').focus({preventScroll:true});},'secondary'));host.append(row);});}
  function updateUpload(file,{authoritative=true}={}){
    // Filenames are display labels, never identity: two different mails can have the same name.
    let r=file.document_id ? state.uploads.find(x=>x.document_id===file.document_id) : file.file ? state.uploads.find(x=>x.file===file.file) : null;
    if(!r)r=state.uploads.find(x=>!x.document_id&&x.filename===file.filename&&(!file.file||x.file===file.file));
    if(r){
      const newAttempt=file.job_id&&file.job_id!==r.job_id;
      // Polling can jump directly to a retry's final result. Only same-job partial
      // snapshots may inherit omitted fields; final results describe the new outcome.
      if(newAttempt||file.state==='queued'||(authoritative&&file.state&&file.state!=='running')){
        Object.assign(r,{error_code:null,reason:null,bookings_count:null,review_reasons:[],analysis_mode:null});
      }
      Object.assign(r,file);
    }else state.uploads.push({...file});
  }
  const terminal = j => ['succeeded','partial','failed','cancelled'].includes(j.state);
  const stageText = {queued:'실행 대기',running:'작업 준비',recovering:'중단된 작업 복구',extracting:'원문 분석',reviewing_facts:'예약 항목 확인',resuming_files:'중단된 파일 이어서 처리',embedding:'검색 자료 준비',building_index:'검색 데이터 구성',activating:'결과 저장',document_complete:'파일 처리',rebuilding_index:'검색 복구',cleanup:'삭제 정리',candidate_snapshot:'저장된 장소 확인',route_snapshot:'출발점과 이동 확인',constraints_and_scoring:'방문 조건과 추천 근거 확인',source_revalidation:'출처와 확인 기한 재확인',recommendation_complete:'추천 결과 저장',result_ready:'추천 결과 준비',fixed_booking_validation:'고정 예약 확인',itinerary_saved:'검증한 일정 저장'};
  const budgetCodes = new Set(['BUDGET_NOT_CONFIGURED','BUDGET_EXHAUSTED','GLOBAL_BUDGET_EXHAUSTED','BUDGET_GLOBAL_STOP','GLOBAL_OPERATIONS_STOPPED','PROVIDER_OUTCOME_UNKNOWN','PROVIDER_RECONCILIATION_REQUIRED','USAGE_PENDING_RECONCILIATION','CALL_OUTCOME_UNKNOWN','GLOBAL_BUDGET_STOPPED','RECONCILIATION_REQUIRED']);
  function canRetryJob(j){if([...state.jobs.values()].some(child=>child.submission?.resume_from===j.job_id))return false;if(j.operation==='reindex')return j.state==='failed'&&!budgetCodes.has(j.error_code);return ['partial','failed','cancelled'].includes(j.state)&&!budgetCodes.has(j.error_code)&&(j.files||[]).some(f=>f.state==='failed'&&!budgetCodes.has(f.error_code));}
  function nextAction(j){const code=j.error_code||(j.files||[]).find(f=>budgetCodes.has(f.error_code))?.error_code;if(code?.includes('BUDGET')||code==='GLOBAL_OPERATIONS_STOPPED')return '새로운 외부 호출이 중단되었습니다. 운영자가 단가·예산을 확인한 뒤 새 작업을 요청할 수 있습니다. 저장된 예약·일정은 계속 볼 수 있어요.';if(code?.includes('UNKNOWN')||code?.includes('RECONCILIATION'))return '공급자의 처리·과금 여부를 확인 중입니다. 중복 과금을 피하기 위해 자동 재시도하지 않습니다.';if(j.cancel_requested_at&&!terminal(j))return '취소를 요청했습니다. 이미 전송된 외부 요청은 끝날 수 있으며 비용이 발생할 수 있습니다.';if(j.state==='cancelled')return '작업이 중단되었습니다. 이미 저장된 결과는 유지됩니다.';if(j.state==='partial'){if(j.operation==='itinerary_generate')return '일정에서 미배치 장소와 확인이 필요한 조건을 살펴보세요. 고정 예약은 유지됩니다.';if(j.operation==='recommendation_generate')return '확인된 후보만 저장했습니다. 추천 화면에서 부족한 근거와 조건을 확인해 주세요.';return '저장된 결과와 확인이 필요한 항목을 확인해 주세요.';}return '';}
  function stopWatchers(){for(const w of state.watchers.values()){w.stream?.close();clearTimeout(w.timer);}state.watchers.clear();}
  function mergeJob(j){state.jobs.set(j.job_id,j);
    const generation=state.itineraries?.generationPreview;if(generation?.jobId===j.job_id)generation.update(j).catch(error=>fail(error,$('#itineraryError')));
    const files=[...(j.files||[])];
    if(j.operation==='documents'&&!terminal(j))for(const accepted of j.submission?.accepted||[]){
      if(!files.some(file=>file.document_id===accepted.document_id))files.push({...accepted,state:j.state});
    }
    files.forEach(f=>{const current=state.uploads.find(x=>x.document_id===f.document_id);const prior=current?.job_id&&state.jobs.get(current.job_id);if(!prior||prior.created_at<=j.created_at)updateUpload({...f,job_id:j.job_id},{authoritative:terminal(j)});});renderJobs();renderUploads();if(j.operation==='documents')renderDocuments();
    const active=state.recommendations.active;
    if(active&&(active.job_id||active.job?.job_id)===j.job_id){
      const finished=terminal(j)&&!terminal(active);active.job=j;active.state=j.state;state.recommendations.connectionError=null;
      if(finished)state.recommendations.resultRecoveryPending=['succeeded','partial'].includes(j.state)&&!active.result&&active.data_status!=='stale';
      renderRecommendationProgress();
      if(finished&&state.tab==='explore')loadRecommendations({runId:active.run_id}).catch(()=>{});
    }
  }
  function renderJobs(){const host=$('#jobList');host.replaceChildren();$('#jobsPanel').hidden=!state.trip||!state.jobs.size;const active=[...state.jobs.values()].filter(j=>!terminal(j)).length,attention=[...state.jobs.values()].filter(j=>['failed','partial'].includes(j.state)).length;$('#jobSummary').textContent=active?`진행 ${active}${attention?' · 확인 '+attention:''}`:attention?`확인 필요 ${attention}`:`작업 ${state.jobs.size}`;$('#openJobs').setAttribute('aria-label',$('#jobSummary').textContent+'건 · 여행 작업 내역');$('#jobsPanel').dataset.status=attention?'attention':active?'active':'complete';for(const j of [...state.jobs.values()].sort((a,b)=>b.created_at.localeCompare(a.created_at)).slice(0,12)){const row=make('div','job-row');const text=make('div');text.append(make('strong','',j.operation==='reindex'?'검색 데이터 복구':j.operation==='place_photos'?'장소 사진 확인':j.operation==='bookmark_resolve'?'장소 지점 확인':['recommendation','recommendations','recommendation_generate'].includes(j.operation)?'장소 추천 준비':['itinerary','itinerary_generate'].includes(j.operation)?'여행 일정 준비':'예약 메일 분석'));text.append(make('p','hint',j.cancel_requested_at&&!terminal(j)?'취소 요청됨':statusText[j.state]||j.state));if(!terminal(j))text.append(make('p','hint',(stageText[j.stage]||'작업 처리 중')+(j.total_count!=null?` · ${j.done_count}/${j.total_count}${['recommendation','recommendations','recommendation_generate','bookmark_resolve','itinerary','itinerary_generate'].includes(j.operation)?'건':'개 파일'}`:'')));const hint=nextAction(j);if(hint)text.append(make('p','hint',hint));if(j.error_code)text.append(make('p','error',fileError(j.error_code)));for(const f of j.files||[]){const detail=make('p','hint',`${f.filename} · ${statusText[f.state]||f.state}${f.error_code?' · '+fileError(f.error_code):''}`);text.append(detail);}row.append(text);const actions=make('div','actions');if(j.can_cancel)actions.append(button('작업 취소',async()=>{try{mergeJob(await api('/jobs/'+j.job_id+'/cancel',{method:'POST'}));}catch(e){fail(e);}},'text-button'));if(canRetryJob(j))actions.append(button(j.operation==='reindex'?'검색 복구 다시 시도':'실패한 파일만 재시도',async()=>{try{const next=await api('/jobs/'+j.job_id+'/retry',{method:'POST',headers:{'Idempotency-Key':uid()}});mergeJob(next);watchJob(next.job_id,state.epoch);}catch(e){fail(e);}},'secondary'));row.append(actions);host.append(row);}}
  async function loadJobs(){if(!state.trip)return;const epoch=state.epoch;const rows=await allPages(tripPath()+'/jobs');if(epoch!==state.epoch)return;for(const j of rows){mergeJob(j);if(!terminal(j))watchJob(j.job_id,epoch);}renderJobs();}
  async function loadUsage(){try{const usage=await api('/usage');if(!state.session?.authenticated)return;const host=$('#usageSummary');host.replaceChildren(make('p','hint',usage.configured?'일·월 예산은 UTC 기준으로 계산합니다.':'유료 외부 호출은 설정되지 않았습니다. 기본 메일 분석과 저장된 예약 조회는 무료로 사용할 수 있어요.'));for(const row of usage.currencies||[])host.append(make('p','hint',`${row.currency} · ${row.state==='unknown'?'과금 확인 중':row.state} · ${row.calls}회 · 예상 ${(row.estimated_micros/1000000).toFixed(6)} / 실제 ${row.actual_micros==null?'미정산':(row.actual_micros/1000000).toFixed(6)}`));}catch(e){if(e.status!==401)$('#usageSummary').textContent='사용량을 불러오지 못했습니다.';}}
  function watchJob(id,epoch){if(state.watchers.has(id)||epoch!==state.epoch)return;const watcher={stream:null,timer:null,errors:0,busy:false};state.watchers.set(id,watcher);
    const refresh=async()=>{if(epoch!==state.epoch||watcher.busy)return;watcher.busy=true;try{const j=await api('/jobs/'+encodeURIComponent(id));if(epoch!==state.epoch)return;mergeJob(j);if(terminal(j)){watcher.stream?.close();clearTimeout(watcher.timer);state.watchers.delete(id);await Promise.allSettled([loadBookings(),loadDocuments(),loadUsage()]);}}catch(e){if(e.status===404){watcher.stream?.close();clearTimeout(watcher.timer);state.watchers.delete(id);}else fail(e);}finally{watcher.busy=false;}};
    const poll=async()=>{await refresh();if(state.watchers.has(id)&&epoch===state.epoch)watcher.timer=setTimeout(poll,3000);};
    if(!('EventSource' in window)){poll();return;}
    watcher.stream=new EventSource('/api/v2/jobs/'+encodeURIComponent(id)+'/events');
    ['progress','partial_result','needs_review','completed','failed','cancelled','snapshot'].forEach(name=>watcher.stream.addEventListener(name,()=>{refresh();}));
    watcher.stream.addEventListener('access_revoked',()=>{watcher.stream.close();clearScope();state.trip=null;boot();});
    watcher.stream.onerror=()=>{if(!state.watchers.has(id))return;if(++watcher.errors>=3){watcher.stream.close();poll();}};
    refresh();
  }
  async function pollJob(id,epoch){if(epoch!==state.epoch)return;const j=await api('/jobs/'+encodeURIComponent(id));if(epoch!==state.epoch)return;mergeJob(j);if(!terminal(j))watchJob(id,epoch);else await Promise.allSettled([loadBookings(),loadDocuments(),loadUsage()]);}
  $('#refreshJobs').addEventListener('click',()=>loadJobs().catch(fail));
  function intent(kind,fingerprint){const name=state.session.user.id+':'+state.trip.id+':'+kind+':'+fingerprint;if(!state.intents.has(name))state.intents.set(name,uid());return {name,key:state.intents.get(name)};}
  async function uploadFingerprint(files){const parts=[];for(const file of files){const digest=await crypto.subtle.digest('SHA-256',await file.arrayBuffer());parts.push([file.name,[...new Uint8Array(digest)].map(x=>x.toString(16).padStart(2,'0')).join('')]);}return JSON.stringify(parts);}
  async function uploadFiles(files){if(!state.trip||state.uploading||!files.length)return;state.uploading=true;$('#uploadButton').disabled=true;const epoch=state.epoch;const path=tripPath();showError($('#uploadError'));const accepted=[];
    files.forEach(f=>{let reason='';if(!/\.(txt|eml)$/i.test(f.name))reason='UNSUPPORTED_TYPE';else if(f.size>1024*1024)reason='FILE_TOO_LARGE';else if(!f.size)reason='빈 파일은 분석할 수 없습니다.';const row={filename:f.name,file:f,state:reason?'rejected':'queued',reason};updateUpload(row);if(!reason)accepted.push(f);});renderUploads();
    try{if(files.length>10)throw new Error('한 번에 최대 10개 파일을 선택해 주세요.');if(!accepted.length)return;const submission=intent('upload',await uploadFingerprint(accepted));if(epoch!==state.epoch)return;const body=new FormData();accepted.forEach(f=>body.append('files',f));const result=await api(path+'/documents',{method:'POST',headers:{'Idempotency-Key':submission.key},body});state.intents.delete(submission.name);if(epoch!==state.epoch)return;
      (result.accepted||[]).forEach(f=>updateUpload({...f,state:'queued'}));(result.rejected||[]).forEach(f=>updateUpload({...f,state:'rejected'}));(result.duplicates||[]).forEach(f=>updateUpload({...f,state:'duplicate'}));renderUploads();
      state.files=files.filter(f=>state.uploads.some(r=>r.filename===f.name&&r.state==='rejected'));$('#mailFiles').value='';$('#selectedFiles').replaceChildren();
      if(result.job_id)await pollJob(result.job_id,epoch);else await Promise.allSettled([loadBookings(),loadDocuments()]);
    }catch(err){if(epoch===state.epoch){accepted.forEach(f=>{const r=state.uploads.find(x=>x.file===f);if(r&&r.state==='queued'){r.state='failed';r.reason='업로드 결과를 확인하지 못했습니다. 저장된 메일을 확인한 뒤 재시도해 주세요.';}});if(Array.isArray(err.details?.rejected))err.details.rejected.forEach(f=>updateUpload({...f,state:'rejected'}));renderUploads();fail(err,$('#uploadError'));}}
    finally{if(epoch===state.epoch){state.uploading=false;$('#uploadButton').disabled=!state.trip;}}
  }
  $('#uploadForm').addEventListener('submit',e=>{e.preventDefault();if(!state.files.length){showError($('#uploadError'),'분석할 메일 파일을 선택해 주세요.');return;}uploadFiles(state.files);});
  async function retryUpload(file){
    const job=file.job_id&&state.jobs.get(file.job_id);
    // A one-file resume reuses the persisted provider artifacts. Multi-file
    // jobs retain their explicit group retry in the job dialog.
    const failed=(job?.files||[]).filter(f=>f.state==='failed');
    if(job&&failed.length===1&&failed[0].document_id===file.document_id&&canRetryJob(job)){
      if(documentProcessing(file.document_id))return;
      const epoch=state.epoch;state.reprocessing.add(file.document_id);updateUpload({...file,state:'queued'});renderUploads();renderDocuments();
      try{const next=await api('/jobs/'+job.job_id+'/retry',{method:'POST',headers:{'Idempotency-Key':uid()}});
        if(epoch===state.epoch)await pollJob(next.job_id,epoch);
      }catch(error){if(epoch===state.epoch){updateUpload({...file,state:'failed'});renderUploads();fail(error,$('#uploadError'));}}
      finally{if(epoch===state.epoch){state.reprocessing.delete(file.document_id);renderDocuments();}}
      return;
    }
    return reprocessDocument(file.document_id,file.filename);
  }
  async function reprocessDocument(id,filename){if(!state.trip)return;if(documentProcessing(id)){notice('이 메일은 이미 분석 중입니다.');return;}state.reprocessing.add(id);renderDocuments();const epoch=state.epoch;closeDialog();setTab('mail');updateUpload({document_id:id,filename:filename||'예약 메일',state:'queued'});renderUploads();try{const submission=intent('reprocess',id);const result=await api(documentPath(id)+'/reprocess',{method:'POST',headers:{'Idempotency-Key':submission.key},body:{}});state.intents.delete(submission.name);if(epoch===state.epoch&&result.job_id)await pollJob(result.job_id,epoch);}catch(err){if(epoch===state.epoch){updateUpload({document_id:id,filename,state:'failed',reason:err.message});renderUploads();fail(err,$('#uploadError'));}}finally{if(epoch===state.epoch){state.reprocessing.delete(id);renderDocuments();}}}

  function bubble(role,text){const b=make('div','bubble '+role,text);$('#thread').append(b);return b;}
  function resetChat(){state.history=[];$('#thread').replaceChildren();if(state.trip)bubble('assistant',`${state.trip.title}의 예약에 대해 물어보세요. 날짜별 예약은 빠짐없이 확인하고, 답할 근거가 없으면 확인할 수 없다고 알려드립니다.`);else bubble('assistant','먼저 여행을 만들어 주세요.');}
  $('#clearChat').addEventListener('click',()=>{if(state.asking){notice('답변을 받은 뒤 대화를 지울 수 있어요.');return;}resetChat();$('#question').value='';showError($('#questionError'));});
  ['1일차 예약을 전부 알려줘','체크아웃 시간은 언제야?','취소·환불 규정을 알려줘'].forEach(q=>$('#suggestions').append(button(q,()=>{if(!state.trip)return;$('#question').value=q;$('#question').focus();},'chip')));
  function sourcesDialog(sources,trip){openDialog('이 답변의 근거',body=>{sources.forEach(s=>{const row=make('div','source-card');row.append(make('strong','',s.provider||'예약 근거'),make('p','hint',s.source_file||(!s.document_id?'직접 입력한 예약':'예약 메일')));if(s.document_id)row.append(button('원문 보기',()=>viewDocument(s.document_id,s.source_file,trip,()=>sourcesDialog(sources,trip)),'primary'),button('원문 다운로드',()=>downloadDocument(s.document_id,s.source_file,trip)));if(s.booking_id){const b=state.bookings.find(x=>x.id===s.booking_id);if(b)row.append(button('예약 상세',()=>bookingDetail(b),'text-button'));}body.append(row);});body.append(make('p','hint','현재 여행에서 답변에 사용한 예약의 근거입니다. 직접 입력한 예약에는 메일 원문이 없습니다.'));});}
  $('#questionForm').addEventListener('submit',async e=>{e.preventDefault();if(!state.trip||state.asking)return;const q=$('#question').value.trim();if(!q)return;state.asking=true;const epoch=state.epoch,trip=state.trip;$('#sendQuestion').disabled=true;$('#question').readOnly=true;showError($('#questionError'));const user=bubble('user',q);const progress=bubble('assistant pending','이 여행의 예약과 근거를 확인하는 중…');
    try{const history=[];let size=0;for(const m of [...state.history].reverse()){if(history.length>=20||size+m.content.length>12000)break;history.unshift(m);size+=m.content.length;}
      const res=await api(tripPath(trip)+'/ask',{method:'POST',body:{question:q,history}});if(epoch!==state.epoch)return;progress.remove();const answer=bubble('assistant',res.answer||'확인할 수 있는 답변이 없습니다.');if(res.sources?.length){const sources=make('div','source-links');sources.append(button(`근거 ${res.sources.length}건 보기`,()=>sourcesDialog(res.sources,trip)));answer.append(sources);}state.history.push({role:'user',content:q},{role:'assistant',content:(res.answer||'').slice(0,4000)});state.history=state.history.slice(-20);$('#question').value='';answer.scrollIntoView({block:'nearest'});
    }catch(err){progress.remove();if(epoch===state.epoch){user.remove();fail(err,$('#questionError'));if(err.code==='SEARCH_REBUILDING')loadJobs().catch(fail);}}
    finally{if(epoch===state.epoch){state.asking=false;$('#sendQuestion').disabled=false;$('#question').readOnly=false;}}
  });

  // Review evidence is cached only on the server; navigation never submits a collection.
  const reviewReasonText = {
    NO_REVIEW_OBSERVATION:'이 장소에서 사용할 수 있는 리뷰 관측 결과가 아직 없습니다.',REVIEW_PRODUCTION_DISABLED:'사용자용 리뷰 근거 기능이 아직 꺼져 있습니다.',PLACE_IDENTITY_UNCONFIRMED:'지점 이름과 주소 확인이 필요합니다.',DISPLAY_RIGHTS_UNAVAILABLE:'리뷰 근거를 표시할 이용 범위를 확인해야 합니다.',REVIEW_STALE:'관측 결과의 유효기간이 지났습니다.',REVIEW_RETENTION_OR_POLICY_WITHDRAWN:'보관 기한이나 이용 범위에 따라 결과를 제공하지 않습니다.',EVIDENCE_LOAD_FAILED:'저장된 근거를 불러오지 못했습니다. 새로고침해 주세요.',
    REMOTE_RETENTION_UNREVIEWED:'공급자의 원문 저장 권한과 삭제 기한을 먼저 확인해야 합니다.',REMOTE_DELETE_UNCONFIRMED:'공급자 원문 삭제를 아직 확인하지 못했습니다.',
    RIGHTS_UNVERIFIED:'리뷰 계산·표시 이용 범위를 확인해야 합니다.',
    POLICY_UNVERIFIED:'이용 범위를 확인해야 합니다.', POLICY_REVOKED:'이용 범위가 철회되었습니다.',
    POLICY_EXPIRED:'이용 범위의 확인 기한이 지났습니다.',
    RESEARCH_DISABLED:'관리자 조사 기능이 꺼져 있습니다.', PRODUCTION_DISABLED:'사용자용 리뷰 근거 기능이 아직 켜지지 않았습니다.',
    PRODUCT_DISABLED:'사용자용 리뷰 근거 기능이 아직 켜지지 않았습니다.',
    STRICT_FEATURE_DISABLED:'엄격 언어 조건 기능이 아직 켜지지 않았습니다.',
    CLASSIFICATION_QUALITY_UNVERIFIED:'도시별 원문 언어 판별 품질이 아직 검증되지 않았습니다.',
    INSUFFICIENT_CLASSIFIED_TEXTS:'언어를 판별한 본문이 100건 미만입니다.',
    TOO_MANY_UNKNOWN:'본문 중 언어 미판별 비중이 기준을 넘습니다.',
    NO_TEXT_REVIEWS:'본문이 있는 리뷰를 확인하지 못했습니다.', NO_CLASSIFIED_TEXTS:'원문 언어를 판별한 본문이 없습니다.',
    UNEXTRACTABLE_RECORDS:'본문 유무나 추출 성공을 확인하지 못한 기록이 있습니다.',
    PARTIAL_COLLECTION:'수집이 일부만 끝나 관측 범위를 완결하지 못했습니다.',
    INELIGIBLE_SELECTION:'관련도·언어·검색어 등으로 선택된 자료는 엄격 언어 조건에 사용할 수 없습니다.',
    TIME_BASIS_MISMATCH:'정렬 시간과 기간 경계의 기준이 다릅니다.',
    ORDERING_SEMANTICS_UNVERIFIED:'리뷰가 어떤 날짜 기준으로 정렬됐는지 미확인입니다.',
    PLACE_IDENTITY_UNVERIFIED:'이 지점의 이름과 주소 확인이 필요합니다.',
    OBSERVATION_EXPIRED:'관측 결과의 유효기간이 지났습니다.',
    NO_ACTIVE_AGGREGATE:'이 장소에서 사용할 수 있는 리뷰 관측 결과가 아직 없습니다.',
    LOCAL_SHARE_BELOW_MIN:'미판별을 포함한 현지어 하한이 기준에 미달합니다.',
    KOREAN_SHARE_ABOVE_MAX:'미판별을 포함한 한국어 상한이 기준을 초과합니다.',
    BUDGET_EXHAUSTED:'사용 가능한 조사 예산을 모두 사용했습니다.',
    PROVIDER_OUTCOME_UNKNOWN:'공급자 처리·과금 여부를 확인하고 있습니다. 새 실행 전 확인이 필요합니다.',
    POPULATION_DESIGN_UNSUPPORTED:'이 자료로 장소 전체 리뷰의 비율을 추정하지 않습니다.',
    SYNTHETIC_DATA:'합성 검증 자료이며 실제 장소의 수집 결과가 아닙니다.'
  };
  const reviewStateText = {available:'관측 근거 확인',unavailable:'이용 불가 · 근거 확인 필요',stale:'유효기간 만료',empty:'아직 관측 자료 없음',blocked:'이용 중지',queued:'수집 대기',running:'수집 중',succeeded:'작업 완료',partial:'일부 수집',failed:'수집 실패',cancelled:'취소 완료',verified:'지점 확인됨',needs_confirmation:'지점 확인 필요'};
  const stopReasonText = {record_cap:'고유 기록 상한 도달',date_boundary:'요청 기간 경계 도달',exhausted:'공급자 결과 종료',page_cap:'페이지 상한 · 부분 수집',time_cap:'시간 상한 · 부분 수집',budget_cap:'예산 상한 · 부분 수집',provider_blocked:'공급자 접근 차단',cursor_repeated:'페이지 연결 반복',cursor_expired:'페이지 연결 만료',parse_error:'응답 해석 실패',ordering_inversion:'정렬 역전',cancelled:'취소 요청 반영'};
  const reviewPlaceStatus = place => place?.identity_status || place?.status;
  const reviewId = value => value?.id || value?.place_id;
  const reviewDate = value => value ? (Number.isNaN(Date.parse(value)) ? String(value) : new Date(value).toLocaleString('ko-KR')) : '미확인';
  const reviewReasons = value => value?.evaluation?.reason_codes || value?.reason_codes || [];
  const percentage = value => typeof value === 'number' && Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : '미판별';
  function reviewAdminAllowed(){ return state.session?.user?.role === 'admin'; }
  function clearReviewScope(){clearTimeout(reviewPollTimer);reviewPollTimer=null;state.reviews={places:[],policies:[],runs:[],controls:null,evidence:[]};['#reviewEvidence','#reviewControls','#reviewPlaces','#reviewPolicies','#reviewRuns'].forEach(s=>$(s)?.replaceChildren());if($('#reviewAdmin'))$('#reviewAdmin').hidden=true;if($('#reviewError'))showError($('#reviewError'));}
  function reasonList(host,codes){if(!codes?.length)return;const list=make('ul','review-reasons');[...new Set(codes)].forEach(code=>list.append(make('li','',reviewReasonText[code]||`확인 필요: ${code}`)));host.append(list);}
  function reviewCounts(host,counts,includeLanguage=false){if(!counts)return;const grid=make('div','review-metrics');const fields=[['고유 관측','observed_records'],['본문 확인','text_count'],['언어 판별','classified_count'],['언어 미판별','unknown_count'],['별점만 확인','rating_only_count'],['추출 불명','extraction_unknown_count']];if(includeLanguage)fields.push(['현지어 판별','local_count'],['한국어 판별','korean_count']);for(const [label,key] of fields){if(counts[key]!=null){const box=make('div');box.append(make('span','',label),make('strong','',`${counts[key]}건`));grid.append(box);}}host.append(grid);}
  function reviewScope(host,coverage,checkedAt,expiresAt){const window=coverage||{};const from=window.observed_oldest_at,to=window.observed_newest_at;host.append(make('p','hint',`실제 관측 기간: ${reviewDate(from)} — ${reviewDate(to)}`),make('p','hint',`확인일: ${reviewDate(checkedAt)}${expiresAt?' · 유효 기한: '+reviewDate(expiresAt):''}`));if(window.requested_start||window.requested_end)host.append(make('p','hint',`요청 기간: ${reviewDate(window.requested_start)} — ${reviewDate(window.requested_end)}`));if(window.ordering_semantics)host.append(make('p','hint',`정렬 기준: ${{published_at:'리뷰 발행일',edited_at:'리뷰 수정일',unknown:'미확인'}[window.ordering_semantics]||window.ordering_semantics} · ${stopReasonText[window.stop_reason]||window.stop_reason||'종료 사유 미확인'}`));}
  function reviewAttribution(host,attribution){if(!attribution?.url)return;try{const url=new URL(attribution.url);if(url.protocol!=='https:')return;const a=make('a','review-source',attribution.label||'장소 출처');a.href=url.href;a.target='_blank';a.rel='noopener noreferrer';host.append(a);}catch{}}
  function renderReviewEvidence(){const host=$('#reviewEvidence');host.replaceChildren();$('#linkReviewPlace').disabled=!state.trip;if(!state.trip){host.append(make('div','panel empty','여행을 만들면 공유받은 장소를 추가하고 리뷰 근거를 확인할 수 있어요.'));return;}if(!state.reviews.evidence.length){host.append(make('div','panel empty','아직 이 여행에 추가한 장소가 없습니다. 관리자가 확인한 장소 ID를 받아 추가해 보세요.'));return;}for(const evidence of state.reviews.evidence){const card=make('article','panel review-card');const place=evidence.place||{};card.append(make('h3','',place.name||'장소 근거'),make('p','hint',place.address||''));const badges=make('div','badges');badges.append(make('span','badge',reviewStateText[evidence.state]||evidence.state||'확인 중'));if(evidence.synthetic)badges.append(make('span','badge warn','합성 검증 · 실제 수집 아님'));card.append(badges);reasonList(card,reviewReasons(evidence));reviewCounts(card,evidence.counts);const metrics=evidence.metrics,counts=evidence.counts;if(evidence.state==='available'&&metrics&&counts){const c=counts.classified_count,t=counts.text_count,l=counts.local_count,k=counts.korean_count;card.append(make('p','review-ratio',`판별한 본문 ${c}건 중 현지어 ${l}건 · ${percentage(metrics.classified_local_share)}`),make('p','review-ratio',`판별한 본문 ${c}건 중 한국어 ${k}건 · ${percentage(metrics.classified_korean_share)}`));card.append(make('p','hint',`미판별을 포함한 본문 ${t}건 기준: 현지어 하한 ${percentage(metrics.local_share_lower_bound)} · 한국어 상한 ${percentage(metrics.korean_share_upper_bound)}. 이 범위는 미판별만 고려하며 분류 오류나 전체 리뷰의 통계적 오차를 보정하지 않습니다.`));if(evidence.evaluation?.strict_pass)card.append(make('p','form-note','최근 관측한 리뷰의 엄격 언어 조건을 충족합니다. 평점·전체 평가 수·이동 경로는 별도로 확인해야 합니다.'));}else if(!counts){card.append(make('p','muted','확인되지 않은 자료를 한국어 0%로 표시하지 않습니다.'));}reviewScope(card,evidence.coverage,evidence.checked_at,evidence.expires_at);reviewAttribution(card,evidence.attribution);host.append(card);}}
  async function loadReviews({quiet=false}={}){if(!state.session?.authenticated||state.tab!=='reviews')return;clearTimeout(reviewPollTimer);const epoch=state.epoch,trip=state.trip;$('#reviewAdmin').hidden=!reviewAdminAllowed();if(!reviewAdminAllowed()){state.reviews.places=[];state.reviews.policies=[];state.reviews.runs=[];state.reviews.controls=null;['#reviewControls','#reviewPlaces','#reviewPolicies','#reviewRuns'].forEach(s=>$(s).replaceChildren());}$('#linkReviewPlace').disabled=!trip;if(!quiet){showError($('#reviewError'));$('#reviewEvidence').replaceChildren(make('p','loading','저장된 리뷰 근거를 확인하는 중…'));}const requests=[];if(trip)requests.push((async()=>{const list=await api(tripPath(trip)+'/places');const results=await Promise.allSettled((list.items||[]).map(p=>api(tripPath(trip)+'/places/'+encodeURIComponent(reviewId(p))+'/review-evidence')));if(epoch!==state.epoch)return;state.reviews.evidence=results.map((r,i)=>r.status==='fulfilled'?r.value:{place:list.items[i],state:'unavailable',reason_codes:['EVIDENCE_LOAD_FAILED']});renderReviewEvidence();})());else renderReviewEvidence();if(reviewAdminAllowed())requests.push((async()=>{const [controls,places,policies,runs]=await Promise.all(['/admin/review-controls','/admin/review-places','/admin/review-policies','/admin/review-collection-runs'].map(p=>api(p)));if(epoch!==state.epoch)return;state.reviews.controls=controls;state.reviews.places=places.items||[];state.reviews.policies=policies.items||[];const runResults=await Promise.allSettled((runs.items||[]).slice(0,20).map(run=>api('/admin/review-collection-runs/'+encodeURIComponent(run.run_id||run.id))));if(epoch!==state.epoch)return;state.reviews.runs=runResults.map((r,i)=>r.status==='fulfilled'?r.value:runs.items[i]);renderReviewAdmin();})());const results=await Promise.allSettled(requests);if(epoch!==state.epoch)return;results.forEach(r=>{if(r.status==='rejected')fail(r.reason,$('#reviewError'));});if(state.tab==='reviews'&&state.reviews.runs.some(run=>['queued','running'].includes(run.state)))reviewPollTimer=setTimeout(()=>loadReviews({quiet:true}).catch(e=>fail(e,$('#reviewError'))),3000);}
  function reviewUsageText(usage){if(!usage)return '비용 미확인';if(Array.isArray(usage.currencies)){if(!usage.currencies.length)return '외부 호출 비용 기록 없음';return usage.currencies.map(row=>`${row.currency} · 예약 ${(row.reserved_micros/1e6).toFixed(6)} · 실제 ${(row.actual_micros/1e6).toFixed(6)}${row.unknown?' · 과금 확인 중':''}`).join(' / ')+` · 호출 ${usage.calls??'미확인'}회 · 가격 확인 ${reviewDate(usage.price_checked_at)}`;}const actual=usage.actual_micros??usage.cost_actual_micros;const reserved=usage.estimated_micros??usage.reserved_micros;const currency=usage.currency||'USD';const actualText=actual!=null?(actual/1e6).toFixed(6):usage.actual_cost_usd??usage.cost_actual??'미정산';const reserveText=reserved!=null?(reserved/1e6).toFixed(6):usage.reserved_cost_usd??usage.estimated_cost??'미확인';return `${currency} · 예약 ${reserveText} · 실제 ${actualText}${usage.unknown_micros||usage.state==='unknown'?' · 과금 확인 중':''}`;}
  function renderReviewAdmin(){if(!reviewAdminAllowed())return;const controls=state.reviews.controls||{};const host=$('#reviewControls');host.replaceChildren(make('h3','','공급자와 기능 상태'));host.append(make('p','',`${controls.provider||'공급자 미설정'} · ${controls.provider_configured?'호출 설정 있음':'호출 설정 없음'}`),make('p','hint',`관리자 조사 ${controls.research_enabled?'켜짐':'꺼짐'} · 사용자 근거 ${controls.production_enabled?'켜짐':'꺼짐'}`),make('p','hint','기술 실행·이용 범위·언어 품질·사용자 활성화는 각각 확인합니다. 기능을 켜도 검증되지 않은 자료에는 엄격 배지를 붙이지 않습니다.'),button('기능 상태 관리',reviewControlsForm,'secondary'));host.append(make('p','hint',`단가·예산 설정: ${controls.pricing_configured?'확인됨':'미설정'} · 가격 확인: ${reviewDate(controls.pricing_checked_at)}${controls.price_version?' · '+controls.price_version:''}`));for(const budget of controls.budget_status||[]){const values=['user_daily_remaining_micros','user_monthly_remaining_micros','global_daily_remaining_micros','global_monthly_remaining_micros'].map(key=>budget[key]);const remaining=values.every(v=>typeof v==='number'&&Number.isFinite(v)&&v>=0)?new Intl.NumberFormat('ko-KR',{maximumFractionDigits:6}).format(Math.min(...values)/1e6):'미확인';host.append(make('p','hint',`확인 시점 남은 호출 예산: ${budget.currency} ${remaining} · 사용자·전역 일/월 상한 중 최솟값 · 날짜 경계 UTC`));}if(controls.remaining_budget)host.append(make('p','hint',`남은 예산: ${valueText(controls.remaining_budget)}`));if(controls.adapter_version)host.append(make('p','hint',`어댑터 ${controls.adapter_version} · 설정 버전 ${controls.version}`));const places=$('#reviewPlaces');places.replaceChildren();if(!state.reviews.places.length)places.append(make('p','muted','등록한 지점이 없습니다. 이름·주소·외부 장소 ID를 확인해 등록하세요.'));for(const place of state.reviews.places){const card=make('article','panel review-card');card.append(make('h3','',place.name),make('p','hint',`${place.city==='tokyo'?'도쿄':'바르셀로나'} · ${place.address}`),make('p','hint',reviewStateText[reviewPlaceStatus(place)]||reviewPlaceStatus(place)||'지점 확인 필요'));const details=make('details','review-details');details.append(make('summary','','지점 식별 정보'),make('p','hint',`장소 ID: ${reviewId(place)}`),make('p','hint',`원천 ID: ${place.external_place_id}`));reviewAttribution(details,{url:place.source_url,label:'등록한 지도 지점 열기'});card.append(details);const actions=make('div','actions');actions.append(button('지점 확인 기록',()=>reviewPlaceVerificationForm(place),'text-button'));if(reviewPlaceStatus(place)==='verified'){actions.append(button('범위를 정해 수집',()=>reviewCollectForm(place),'primary'));if(state.trip)actions.append(button('이 여행에 추가',()=>linkReviewPlace(reviewId(place)).catch(()=>{}),'secondary'));}actions.append(button('지점 삭제',()=>confirmAction('공용 지점 삭제','이 지점의 진행 중인 수집과 근거 표시를 중단합니다. 개인 예약은 삭제하지 않습니다.','지점 삭제',async()=>{await api('/admin/review-places/'+encodeURIComponent(reviewId(place)),{method:'DELETE'});await loadReviews();}),'text-button'));card.append(actions);places.append(card);}const policies=$('#reviewPolicies');policies.replaceChildren();if(!state.reviews.policies.length)policies.append(make('p','muted','기록된 이용 범위가 없습니다. 수집 전에 용도별 권한을 확인하세요.'));for(const policy of state.reviews.policies){const row=make('div','review-policy');row.append(make('strong','',`${policy.provider} · ${policy.version}`),make('p','hint',policy.purpose||''),make('p','hint',`확인 ${reviewDate(policy.reviewed_at)} · 만료 ${reviewDate(policy.expires_at)}`));row.append(make('p','hint',`원격 공급자 원문 보관 기한: ${policy.remote_raw_ttl_seconds??0}초 · 이 앱의 본문 보관: 없음`));const rights=policy.rights||{};row.append(make('p','hint',Object.entries(reviewRightsLabels).map(([key,label])=>`${label} ${rights[key]===true?'허용 확인':'미허용'}`).join(' · ')));if(policy.revoked_at||policy.status==='revoked')row.append(make('p','error','철회된 이용 범위'));else row.append(button('이용 범위 철회',()=>confirmAction('이용 범위 철회','이 정책을 사용하는 새 수집과 파생 근거 표시를 중지합니다.','철회',async()=>{await api('/admin/review-policies/'+encodeURIComponent(policy.id),{method:'DELETE'});await loadReviews();}),'text-button'));policies.append(row);}renderReviewRuns();window.ReviewLab?.load();}
  function reviewRemoteCleanup(card,run){const cleanup=run.remote_cleanup;if(!cleanup)return;const box=make('div','form-note');const labels={pending:'삭제 대기',running:'삭제 확인 중',failed:'삭제 미확인 · 조치 필요',succeeded:'삭제 확인 완료'};box.append(make('strong','',`공급자 원문 · ${labels[cleanup.state]||cleanup.state||'미확인'}`),make('p','hint',`삭제 기한: ${reviewDate(cleanup.deadline_at)} · 데이터셋 ${cleanup.dataset_deleted?'삭제 확인':'미확인'} · 실행 기록 ${cleanup.run_deleted?'삭제 확인':'미확인'}`));if(cleanup.error_code)reasonList(box,[cleanup.error_code]);if(['pending','failed'].includes(cleanup.state)&&(!['queued','running'].includes(run.state)||(cleanup.deadline_at&&Date.parse(cleanup.deadline_at)<=Date.now()))){const retry=button('공급자 원문 삭제 다시 확인',async()=>{retry.disabled=true;try{await api('/admin/review-collection-runs/'+encodeURIComponent(run.run_id||run.id)+'/remote-cleanup',{method:'POST'});await loadReviews({quiet:true});}catch(e){fail(e,$('#reviewError'));}finally{retry.disabled=false;}},'secondary');box.append(retry,make('p','hint','원문 삭제만 재확인합니다. 리뷰를 새로 수집하지 않습니다.'));}card.append(box);}
  function renderReviewRuns(){const host=$('#reviewRuns');host.replaceChildren();if(!state.reviews.runs.length){host.append(make('p','muted','아직 수집 작업이 없습니다. 저장된 결과 조회에는 수집 비용이 들지 않습니다.'));return;}for(const run of state.reviews.runs){const card=make('article','panel review-card');card.append(make('h3','',run.place?.name||'리뷰 수집'),make('p','hint',`${reviewStateText[run.state]||run.state||'상태 확인 중'}${run.synthetic?' · 합성 공급자':''}`));reviewRemoteCleanup(card,run);const coverage=run.coverage||{},job=run.job||{};if(['queued','running'].includes(run.state)){const stages={queued:'실행 대기',review_start:'공급자 실행 준비',provider_running:'공급자 수집 진행',review_pages:'페이지 확인·언어 분석',running:'작업 준비'};card.append(make('p','',`${stages[job.stage]||'작업 상태 확인 중'}${job.done_count!=null?' · 처리 '+job.done_count+'건':''}${job.total_count!=null?' / 상한 '+job.total_count+'건':''}`));}card.append(make('p','',`수신 ${coverage.fetched_count??'미확인'}건 · 고유 ${coverage.unique_count??'미확인'}건 · ${coverage.pages_count??coverage.page_count??'미확인'}페이지`));reviewCounts(card,run.counts,true);card.append(make('p','hint',reviewUsageText(run.usage)));reviewScope(card,coverage,run.checked_at||run.finished_at);reasonList(card,reviewReasons(run));if(run.error_code||job.error_code)reasonList(card,[run.error_code||job.error_code]);if(run.state==='partial')card.append(make('p','form-note','중단 전 확보한 범위만 남았습니다. 엄격 언어 조건에는 사용하지 않습니다. 원인을 확인한 뒤 새 실행에서 범위와 최대 비용을 다시 정하세요.'));if(job.cancel_requested_at)card.append(make('p','hint','취소 요청됨 · 외부 실행이 이미 진행됐다면 응답과 비용이 발생할 수 있습니다.'));const details=make('details','review-details');details.append(make('summary','','수집 설정과 추적 정보'),make('p','hint',`실행 ID: ${run.run_id||run.id}`),make('p','hint',`연속성: ${coverage.continuity_status||'미확인'} · 종료: ${stopReasonText[coverage.stop_reason]||coverage.stop_reason||'진행 중'}`));for(const [label,key] of [['어댑터','adapter_version'],['정책','policy_version'],['판별기','detector_version'],['집계 설정','config_version']]){const value=run[key]||run.provenance?.[key]||run.evaluation?.[key];if(value)details.append(make('p','hint',`${label}: ${value}`));}card.append(details);const actions=make('div','actions');const jobId=run.job_id||job.job_id||job.id;if(jobId&&['queued','running'].includes(run.state)&&!job.cancel_requested_at)actions.append(button('수집 취소 요청',async()=>{try{await api('/jobs/'+encodeURIComponent(jobId)+'/cancel',{method:'POST'});await loadReviews({quiet:true});}catch(e){fail(e,$('#reviewError'));}},'text-button'));if(['partial','failed','cancelled'].includes(run.state)){const place=state.reviews.places.find(p=>reviewId(p)===(run.place_id||reviewId(run.place)));if(reviewPlaceStatus(place)==='verified')actions.append(button('범위·비용 확인 후 새 실행',()=>reviewCollectForm(place),'secondary'));}card.append(actions);host.append(card);}}
  function reviewCheckbox(grid,name,label,checked=false){const wrap=make('div','field review-check');const input=make('input');input.type='checkbox';input.id='field-'+name;input.name=name;input.dataset.field=name;input.checked=checked;const labelNode=make('label','',label);labelNode.htmlFor=input.id;const error=make('p','error');error.hidden=true;wrap.append(input,labelNode,error);grid.append(wrap);return input;}
  function reviewControlsForm(){openDialog('리뷰 기능 상태',body=>{const controls=state.reviews.controls||{};body.append(make('p','form-note','조사와 사용자 표시를 별도로 관리합니다. 실제 수집·이용 범위·도시별 분류 평가가 확인되기 전에는 사용자 기능을 켜지 마세요.'));let research,production;const {grid}=formBase(body,'설정 저장',async()=>{await api('/admin/review-controls',{method:'PATCH',body:{expected_version:controls.version,research_enabled:research.checked,production_enabled:production.checked}});closeDialog(true);await loadReviews();});research=reviewCheckbox(grid,'research_enabled','관리자 조사 허용',controls.research_enabled);production=reviewCheckbox(grid,'production_enabled','사용자 근거 표시 허용',controls.production_enabled);});}
  function reviewPlaceForm(){openDialog('지점 등록',body=>{body.append(make('p','form-note','공식 카탈로그의 장소와 같은 지점이면 ‘같은 지점 연결’을 먼저 사용하세요. 별도 조사 지점에서는 동일 상호의 다른 지점·이전 주소·폐업 여부를 대조해 주세요. 링크를 등록해도 서버가 해당 페이지를 자동으로 가져오지 않습니다.'));let inputs={};const {grid}=formBase(body,'지점 등록',async()=>{const data={};for(const key of ['provider','external_place_id','city','name','address','source_url'])data[key]=inputs[key].value.trim();data.rating=inputs.rating.value===''?null:Number(inputs.rating.value);data.total_rating_count=inputs.total_rating_count.value===''?null:Number(inputs.total_rating_count.value);await api('/admin/review-places',{method:'POST',body:data});closeDialog(true);await loadReviews();});inputs.provider=field(grid,'provider','공급자',state.reviews.controls?.provider||'apify',{required:true});inputs.city=field(grid,'city','도시','tokyo',{select:[['tokyo','도쿄'],['barcelona','바르셀로나']]});inputs.name=field(grid,'name','지점 이름','',{required:true,maxLength:200});inputs.external_place_id=field(grid,'external_place_id','검증할 외부 장소 ID','',{required:true,maxLength:256});inputs.address=field(grid,'address','주소','',{required:true,wide:true,maxLength:400});inputs.source_url=field(grid,'source_url','Google Maps 지점 URL','',{type:'url',required:true,wide:true,hint:'단축 링크 대신 google.com/maps 또는 maps.google.com의 전체 지점 링크'});inputs.rating=field(grid,'rating','현재 평점 · 미확인 시 비워두기','',{type:'number',min:0,max:5});inputs.rating.step='0.1';inputs.total_rating_count=field(grid,'total_rating_count','전체 별점 평가 수 · 미확인 시 비워두기','',{type:'number',min:0});});}
  function reviewPlaceVerificationForm(place){openDialog('지점 확인 기록',body=>{body.append(make('p','form-note',`${place.name} · ${place.address}`));reviewAttribution(body,{url:place.source_url,label:'등록한 지도 지점 확인'});let status,evidence;const {grid}=formBase(body,'확인 기록 저장',async()=>{await api('/admin/review-places/'+encodeURIComponent(reviewId(place)),{method:'PATCH',body:{expected_version:place.version,status:status.value,evidence:evidence.value.trim()}});closeDialog(true);await loadReviews();});status=field(grid,'status','확인 결과',reviewPlaceStatus(place)||'needs_confirmation',{select:[['needs_confirmation','추가 확인 필요'],['verified','이름·주소·지점 일치 확인'],['blocked','사용 중지']]});evidence=field(grid,'evidence','확인한 근거','',{textarea:true,wide:true,required:true,maxLength:1000,hint:'이름·주소 대조와 폐업·이전 여부, 확인한 출처를 기록해 주세요.'});});}
  const reviewRightsLabels={access:'접근',collect:'수집',calculate:'로컬 계산',raw_store:'원문 저장 · 공급자 포함',aggregate_store:'집계 저장',id_store:'리뷰 ID·해시 보관',llm:'LLM 전달',display:'사용자 표시'};
  function reviewPolicyForm(){openDialog('리뷰 이용 범위 기록',body=>{body.append(make('p','form-note','각 동작에 대한 확인 근거를 기록합니다. 수집 허용은 저장·집계·표시 허용을 뜻하지 않습니다. 보관 기간은 권한에서 허용한 범위를 넘길 수 없습니다. 기본값은 허가가 아닙니다.'));let inputs={},rights={};const {grid,form}=formBase(body,'이용 범위 저장',async()=>{let evidence;try{evidence=inputs.evidence.value.split(/\n/).map(v=>v.trim()).filter(Boolean);if(!evidence.length||evidence.some(v=>new URL(v).protocol!=='https:'))throw new Error();}catch{return localError(form,'evidence','https 공식 문서·계약 근거 URL을 한 줄에 하나씩 입력해 주세요.');}if(inputs.provider.value.trim()==='apify'&&(!rights.raw_store.checked||Number(inputs.remote_raw_ttl_seconds.value)<=0))return localError(form,'remote_raw_ttl_seconds','Apify는 공급자 데이터셋에 원문을 저장합니다. 공급자를 포함한 원문 저장 권한과 0초보다 긴 삭제 기한을 확인해 주세요.');const reviewed=new Date(inputs.reviewed_at.value),expires=new Date(inputs.expires_at.value);if(!Number.isFinite(reviewed.getTime())||!Number.isFinite(expires.getTime())||expires<=reviewed)return localError(form,'expires_at','확인일 이후의 유효 기한을 입력해 주세요.');await api('/admin/review-policies',{method:'POST',body:{provider:inputs.provider.value.trim(),version:inputs.version.value.trim(),purpose:inputs.purpose.value.trim(),evidence,reviewed_at:reviewed.toISOString(),expires_at:expires.toISOString(),aggregate_ttl_seconds:Number(inputs.aggregate_ttl_seconds.value),id_ttl_seconds:Number(inputs.id_ttl_seconds.value),remote_raw_ttl_seconds:Number(inputs.remote_raw_ttl_seconds.value),rights:Object.fromEntries(Object.entries(rights).map(([key,input])=>[key,input.checked]))}});closeDialog(true);await loadReviews();});inputs.provider=field(grid,'provider','공급자',state.reviews.controls?.provider||'apify',{required:true});inputs.version=field(grid,'version','검토 버전','',{required:true,maxLength:100,placeholder:'예: review-access-2026-10-01'});inputs.purpose=field(grid,'purpose','검토한 이용 목적','',{required:true,wide:true,maxLength:1000});inputs.evidence=field(grid,'evidence','확인 근거 URL','',{textarea:true,required:true,wide:true});inputs.reviewed_at=field(grid,'reviewed_at','권한 확인일','',{type:'datetime-local',required:true});inputs.expires_at=field(grid,'expires_at','권한 확인 유효 기한','',{type:'datetime-local',required:true});inputs.aggregate_ttl_seconds=field(grid,'aggregate_ttl_seconds','허용 집계 보관 기간 · 초',604800,{type:'number',min:1,max:2592000,required:true});inputs.id_ttl_seconds=field(grid,'id_ttl_seconds','허용 ID·해시 보관 기간 · 초',86400,{type:'number',min:1,max:86400,required:true});inputs.remote_raw_ttl_seconds=field(grid,'remote_raw_ttl_seconds','원격 공급자 원문 보관 기한 · 초',0,{type:'number',min:0,max:86400,required:true,wide:true,hint:'Apify 사용 시 원격 원문 저장 권한과 1~86,400초의 삭제 기한이 필요합니다. 이 앱에는 리뷰 본문을 저장하지 않습니다.'});for(const [key,label] of Object.entries(reviewRightsLabels))rights[key]=reviewCheckbox(grid,'rights.'+key,`${label} 허용을 근거로 확인함`,false);});}
  async function submitReviewCollection(payload,epoch){if(epoch!==state.epoch)return;const intentName=state.session.user.id+':review-collect:'+JSON.stringify(payload);if(!state.intents.has(intentName))state.intents.set(intentName,uid());const result=await api('/admin/review-collection-runs',{method:'POST',headers:{'Idempotency-Key':state.intents.get(intentName)},body:payload});if(epoch!==state.epoch)return;state.intents.delete(intentName);closeDialog(true);notice(`수집 작업을 접수했습니다. ${result.run_id||''}`);await loadReviews();}
  function reviewCollectForm(place){openDialog('리뷰 수집 범위와 최대 비용',body=>{body.append(make('p','form-note',`${place.name} · ${place.address}`),make('p','muted','최신순 · 최근 180일 · 언어·검색어·별점 선택 없음. 별점만 있는 기록도 상한에 포함합니다. 추천 화면을 열거나 새로고침할 때마다 재수집하지 않습니다.'),make('p','hint','기본 재시도는 페이지당 최초 요청 포함 총 2회입니다. 응답을 잃어 과금이 불명확한 경우 자동으로 새 유료 실행을 시작하지 않습니다.'));let inputs={},inputVersion=0;const epoch=state.epoch;const {grid,form}=formBase(body,'범위·예산 미리보기',async()=>{if(!inputs.policy_id.value)return localError(form,'policy_id','사용 가능한 이용 범위를 먼저 기록해 주세요.');const payload={place_id:reviewId(place),policy_id:inputs.policy_id.value,max_review_records:Number(inputs.max_review_records.value),max_pages:Number(inputs.max_pages.value),max_elapsed_seconds:Number(inputs.max_elapsed_seconds.value),max_total_charge_usd:inputs.max_total_charge_usd.value};const version=inputVersion;await window.ReviewLab.collectionPreview(body,payload,()=>submitReviewCollection(payload,epoch),()=>epoch===state.epoch&&form.isConnected&&version===inputVersion);});const policies=state.reviews.policies.filter(p=>p.provider===place.provider&&!p.revoked_at&&p.status!=='revoked');inputs.policy_id=field(grid,'policy_id','적용할 이용 범위','',{select:[['','이용 범위 선택'],...policies.map(p=>[p.id,`${p.version} · ${reviewDate(p.expires_at)}`])],required:true,wide:true});inputs.max_review_records=field(grid,'max_review_records','최대 고유 기록 수',200,{type:'number',min:1,max:200,required:true,hint:'smoke 단계는 최대 100건'});inputs.max_pages=field(grid,'max_pages','최대 페이지 수',20,{type:'number',min:1,max:20,required:true});inputs.max_elapsed_seconds=field(grid,'max_elapsed_seconds','최대 실행 시간 · 초',300,{type:'number',min:1,max:900,required:true});inputs.max_total_charge_usd=field(grid,'max_total_charge_usd','이번 실행의 최대 비용 · USD','0',{type:'number',min:0,max:3,required:true,hint:'실제 공급자는 최신 단가·최소 결제를 확인한 허용 범위만 입력하세요. 실험 전체 예산은 서버에서도 별도 검사합니다.'});inputs.max_total_charge_usd.step='0.000001';form.addEventListener('input',()=>{inputVersion++;body.querySelector('.review-collection-preview')?.remove();});});}
  async function linkReviewPlace(id){if(!state.trip)return;try{await api(tripPath()+'/places',{method:'POST',body:{place_id:id}});closeDialog(true);notice('현재 여행에 장소를 추가했습니다.');await loadReviews();}catch(e){fail(e,$('#reviewError'));throw e;}}
  function reviewLinkForm(){if(!state.trip)return;openDialog('공유받은 장소 추가',body=>{body.append(make('p','muted','관리자가 지점을 확인한 장소 ID를 입력하면 저장된 리뷰 근거를 볼 수 있습니다. 이 동작은 새 리뷰 수집을 실행하지 않습니다.'));let input;const {grid}=formBase(body,'이 여행에 추가',async()=>{await linkReviewPlace(input.value.trim());});input=field(grid,'place_id','공유받은 장소 ID','',{required:true,wide:true,maxLength:100});});}
  $('#refreshReviews').addEventListener('click',()=>loadReviews().catch(e=>fail(e,$('#reviewError'))));
  $('#linkReviewPlace').addEventListener('click',reviewLinkForm);
  $('#createReviewPlace').addEventListener('click',reviewPlaceForm);
  $('#createReviewPolicy').addEventListener('click',reviewPolicyForm);

  // Place bookmarks and visit conditions are private to the selected trip.
  const discoveryCityNames = {tokyo:'도쿄',barcelona:'바르셀로나'};
  let destinationCatalog=[];
  function knownDestination(value){const key=(value||'').normalize('NFKC').trim().toLocaleLowerCase();return destinationCatalog.find(c=>[c.id,c.name_ko,c.name_en,...c.aliases].some(v=>v.normalize('NFKC').toLocaleLowerCase()===key));}
  function destinationLabel(value,fallback='도시 미정'){const label=typeof value==='string'?value.trim():'';return knownDestination(label)?.name_ko||label||fallback;}
  async function loadDestinations(){if(destinationCatalog.length)return;const data=await api('/cities');destinationCatalog=data.cities;for(const city of destinationCatalog)discoveryCityNames[city.id]=city.name_ko;if(state.trip)renderTripCityLabels();}
  const discoveryCategoryNames = {restaurant:'음식점',cafe:'카페',attraction:'볼거리'};
  const bookmarkStateNames = {unresolved:'저장됨 · 지점 미확인',resolving:'지점 확인 중',resolved:'지점 확인 완료',ambiguous:'여러 지점 중 선택 필요',unsupported:'자동 확인 미지원',failed:'지점 확인 실패'};
  const discoveryReasonNames = {RESOLVE_UNAVAILABLE:'이 링크나 이름을 자동으로 확인할 자료가 없습니다.',PROVIDER_UNAVAILABLE:'외부 지점 확인 서비스를 사용할 수 없습니다. 저장한 입력과 메모는 유지됩니다.',POLICY_UNAVAILABLE:'지점 확인에 필요한 자료 이용 범위를 준비 중입니다.',BUDGET_EXHAUSTED:'현재 호출 예산이 부족합니다. 저장한 장소는 계속 확인할 수 있습니다.',AMBIGUOUS_PLACE:'같은 이름의 지점이 여러 개입니다. 주소를 보고 선택해 주세요.',UNSUPPORTED_URL:'이 링크의 자동 지점 확인을 지원하지 않습니다.',PLACE_UNVERIFIED:'지점과 주소를 먼저 확인해야 합니다.',VERSION_CONFLICT:'다른 화면에서 수정한 내용이 있습니다. 입력을 보존했으니 최신 내용을 확인해 주세요.'};
  function clearDiscoveryScope(){if($('#discoveryConditionBrief'))$('#discoveryConditionBrief').textContent='선택한 여행의 조건을 불러오고 있어요…';$('#discoveryQuick')?.replaceChildren();clearTimeout(discoveryPollTimer);discoveryPollTimer=null;state.discovery={conditions:null,bookmarks:[],loaded:false,serial:0,packs:[]};setExploreView('recommend');['#discoveryConditions','#bookmarkList','#homePlaces','#discoveryPacks'].forEach(s=>$(s)?.replaceChildren());if($('#discoveryAdmin'))$('#discoveryAdmin').hidden=true;if($('#discoveryAdminError'))showError($('#discoveryAdminError'));if($('#bookmarkCount'))$('#bookmarkCount').textContent='';if($('#bookmarkFilter'))$('#bookmarkFilter').value='all';if($('#discoveryError'))showError($('#discoveryError'));if($('#discoveryMessage')){$('#discoveryMessage').hidden=true;$('#discoveryMessage').textContent='';}}
  function discoveryMessage(message){$('#discoveryMessage').textContent=message;$('#discoveryMessage').hidden=!message;}
  function renderHomePlaces(){
    const host=$('#homePlaces');if(!host)return;host.hidden=!state.trip;host.replaceChildren();
    ['#addBookmark','#editDiscoveryConditions','#refreshBookmarks'].forEach(s=>{if($(s))$(s).disabled=!state.trip;});
    if(!state.trip)return;
    // Primary journeys stay in the same place after the first reservation.
    host.classList.remove('has-bookings');
    const bookmarks=state.discovery.bookmarks,loaded=state.discovery.loaded,pending=bookmarks.filter(item=>item.resolve_state!=='resolved').length;
    const intro=make('div','home-places-copy');intro.append(make('h2','','어디부터 준비할까요?'),make('p','hint',loaded?`보관함 ${bookmarks.length}곳 · 지점 확인 필요 ${pending}곳`:'장소 보관함을 확인하고 있습니다.'));
    host.append(intro);
    const shortcuts=make('div','journey-shortcuts');
    for(const [tab,title,note] of [['mail','예약 메일 업로드','메일 또는 직접 입력'],['explore','가고 싶은 곳 찾기','현지어 리뷰 · 유명한 곳'],['itinerary','하루 일정 만들기','예약을 지키면서 동선 정리']]){
      const action=button('',()=>{setTab(tab);if(tab==='mail')$('#uploadForm').scrollIntoView({block:'start'});},'journey-shortcut');
      action.append(document.querySelector('.sidebar [data-tab="'+tab+'"] svg').cloneNode(true),make('strong','',title),make('span','hint',note),make('span','shortcut-arrow','↗'));
      shortcuts.append(action);
    }
    host.append(shortcuts);
    const visit=ensureDiscoveryDraft()?.conditions?.visit?.date||state.trip.start_date,bookings=state.bookings.filter(b=>b.status!=='cancelled').sort((a,b)=>Math.abs((calendarDate(a.date)||0)-(calendarDate(visit)||0))-Math.abs((calendarDate(b.date)||0)-(calendarDate(visit)||0))),actions=[];
    for(const booking of bookings){if(booking.time_conflicts?.length||booking.status==='needs_review'){const conflict=booking.time_conflicts?.[0];actions.push({id:'booking:'+booking.id,title:(booking.provider||bookingKindLabel(booking.kind))+' 확인',note:conflict?'대표 시각과 해당 구간이 달라요.':(booking.date||'날짜 미확인')+' · 예약 내용을 확인해 주세요.',open:()=>openBookingFromJourney(booking.id,conflict?.event_id)});}}
    for(const bookmark of bookmarks.filter(item=>['ambiguous','failed','unresolved','needs_confirmation'].includes(item.resolve_state))){actions.push({id:'bookmark:'+bookmark.id,title:(bookmark.place?.display_name||bookmark.input_value)+' 지점 확인',note:'저장은 완료됐어요. 주소로 정확한 지점을 확인하세요.',open:()=>{setTab('explore');setExploreView('saved');if(bookmark.resolve_state==='ambiguous')bookmarkCandidates(bookmark);else {focusBookmark(bookmark.id);document.querySelector('[data-bookmark-id="'+CSS.escape(bookmark.id)+'"]')?.scrollIntoView({block:'center'});}}});}
    const seen=new Set(),nextActions=actions.filter(item=>{if(seen.has(item.id))return false;seen.add(item.id);return true;}).slice(0,3);
    if(nextActions.length){host.append(make('h3','detail-section','지금 확인할 일'));const list=make('div','home-next-actions');for(const item of nextActions){const action=button('',item.open,'home-next-booking'),copy=make('span');copy.append(make('strong','',item.title),make('span','hint',item.note));action.dataset.workspaceFocus=item.id;action.append(copy,make('span','hint','확인 →'));list.append(action);}host.append(list);}
    else {const next=bookings.find(b=>b.date&&b.date>=visit);if(next){const action=button('',()=>openBookingFromJourney(next.id),'home-next-booking'),copy=make('span');copy.append(make('span','hint',`방문일 가까운 예약 · ${next.date} ${next.time||'시각 미확인'}`),make('strong','',next.provider||bookingKindLabel(next.kind)));action.dataset.workspaceFocus='booking:'+next.id;action.append(copy,make('span','hint','상세 보기 →'));host.append(action);}}

  }
  async function loadDiscovery({quiet=false}={}){
    clearTimeout(discoveryPollTimer);if(!state.session?.authenticated)return;loadDiscoveryAdmin().catch(e=>fail(e,$('#discoveryAdminError')));if(!state.trip){renderDiscoveryConditions();renderBookmarks();renderHomePlaces();return;}
    const epoch=state.epoch,serial=++state.discovery.serial,path=tripPath();
    if(!quiet&&!state.discovery.loaded)$('#bookmarkList').replaceChildren(make('p','loading','이 여행의 보관함을 불러오는 중…'));
    const [conditions,bookmarks]=await Promise.all([api(path+'/discovery-conditions'),allPages(path+'/bookmarks')]);
    if(epoch!==state.epoch||serial!==state.discovery.serial)return;
    const initial=!state.discovery.loaded;state.discovery.conditions=conditions;if(!state.discovery.dirty)state.discovery.draft=null;state.discovery.bookmarks=bookmarks;state.discovery.loaded=true;
    if(initial){ensureDiscoveryDraft();window.WorkspaceUX?.prepare();}
    renderDiscoveryConditions();renderBookmarks();renderHomePlaces();renderRecommendationResults();renderItinerarySelection();window.WorkspaceUX?.afterRender();window.DiscoveryExperience?.loadCapabilities();
    window.AccommodationTools?.load();
    if(state.tab==='explore'&&bookmarks.some(item=>item.resolve_state==='resolving'))discoveryPollTimer=setTimeout(()=>loadDiscovery({quiet:true}).catch(e=>fail(e,$('#discoveryError'))),2500);
  }
  function formatBudget(budget){if(!budget)return '예산 미입력';const range=budget.amount_min==null&&budget.amount_max==null?'금액 미입력':budget.amount_min===budget.amount_max?valueText(budget.amount_min):`${budget.amount_min??'하한 없음'}–${budget.amount_max??'상한 없음'}`;return `${budget.currency} ${range} · ${budget.basis==='group'?'일행 전체':'1인'} · ${budget.period==='day'?'하루':budget.period==='visit'?'방문 1회':'한 끼'}`;}
  function renderDiscoveryConditions(){const host=$('#discoveryConditions');host.replaceChildren();$('#discoveryAvailability').replaceChildren();$('#discoveryConditionBrief').textContent='';if(!state.trip){host.append(make('p','muted','여행을 먼저 만들어 주세요.'));return;}const envelope=state.discovery.conditions;if(!envelope){host.append(make('p','muted','저장한 조건을 불러오는 중입니다.'));return;}renderDiscoveryQuick();const c=ensureDiscoveryDraft()?.conditions||envelope.conditions||{},visit=c.visit||{},party=c.party||{},children=party.children||[];$('#discoveryConditionBrief').textContent=`${discoveryCityNames[c.city]||envelope.unsupported_cities?.join(' · ')||'도시 미선택'} · ${visit.date||'날짜 미선택'} · 성인 ${party.adults??'미입력'}명${children.length?' · 아동 '+children.length+'명':''}`;const extra=[];if(c.budget)extra.push(formatBudget(c.budget));if(c.radius_m)extra.push('이동 범위 '+(c.radius_m/1000)+'km');if(c.visit?.local_time)extra.push('방문 '+c.visit.local_time);const required=[...(c.required?.dietary||[]),...(c.required?.accessibility||[])];const preferred=[...(c.preferred?.tags||[]),...(c.preferred?.dietary||[])];if(required.length)extra.push('필수: '+required.map(v=>window.WorkspaceUX?.conditionLabel(v)||v).join(' · '));if(preferred.length)extra.push('선호: '+preferred.join(' · '));if(extra.length)host.append(make('p','hint',extra.join(' · ')));const unsupported=envelope.context_state==='unsupported_city';$('#discoveryAvailability').hidden=!unsupported&&!envelope.catalog_availability?.synthetic_test_candidates;$('#editDiscoveryConditions').disabled=unsupported;
    if(unsupported){$('#discoveryAvailability').append(make('p','form-note',`${envelope.unsupported_cities.join(' · ')}의 자동 추천은 준비 중입니다. 도시를 바꿀 필요 없이 장소 링크나 이름을 보관함에 저장할 수 있어요.`));return;}
    if(envelope.context_state==='outdated')$('#discoveryAvailability').append(make('p','form-note','여행 정보가 바뀌었습니다. 방문 조건에서 도시와 날짜를 다시 확인해 주세요.'));
    const availability=envelope.catalog_availability;if(availability){$('#discoveryAvailability').append(make('p','form-note',availability.real_reviewed_candidates?`운영 검토를 마친 실제 후보 ${availability.real_reviewed_candidates}곳 · 방문일과 인원 적합성은 추천 시 별도로 확인합니다.`:'이 도시의 실제 추천 자료를 준비 중입니다. 여행·예약·개인 장소 보관함은 사용할 수 있습니다.'));if(availability.synthetic_test_candidates)$('#discoveryAvailability').append(make('p','hint',`개발 시험용 합성 후보 ${availability.synthetic_test_candidates}곳이 있습니다. 실제 여행지 추천이 아닙니다.`));}}
  function discoveryCheckGroup(grid,name,label,options,selected){const wrapper=make('fieldset','discovery-check-group field wide');wrapper.append(make('legend','',label));const values=new Map();for(const [value,text] of options){const row=make('label','check');const input=make('input');input.type='checkbox';input.value=value;input.name=name;input.dataset.field=name;input.checked=(selected||[]).includes(value);row.append(input,make('span','',text));wrapper.append(row);values.set(value,input);}const error=make('p','error');error.hidden=true;wrapper.append(error);grid.append(wrapper);return ()=>[...values].filter(([,input])=>input.checked).map(([value])=>value);}
  function commaItems(value){return [...new Set(value.split(/[,，\n]/).map(item=>item.trim()).filter(Boolean))];}
  function discoveryStayChoice(stays,city,date,trip){
    const relevant=stays.filter(stay=>stay.city===city),match=relevant.find(stay=>stay.start_date<=date&&date<=stay.end_date),first=relevant[0];
    return {date:match?date:first?first.start_date:date||trip.start_date,min:relevant.length?relevant.map(s=>s.start_date).sort()[0]:trip.start_date,max:relevant.length?relevant.map(s=>s.end_date).sort().at(-1):trip.end_date,timezone:first?.timezone||(city==='barcelona'?'Europe/Madrid':'Asia/Tokyo'),label:relevant.length?'체류 기간: '+relevant.map(s=>s.start_date+' ~ '+s.end_date).join(' / '):'여행 기간: '+trip.start_date+' ~ '+trip.end_date};
  }
  function cityPicker(input, zone) {
    const host=input.closest('.field'),list=make('div','city-options');list.id='city-options-'+uid();list.setAttribute('role','listbox');list.hidden=true;host.append(list);
    input.removeAttribute('list');input.setAttribute('role','combobox');input.setAttribute('aria-autocomplete','list');input.setAttribute('aria-controls',list.id);input.setAttribute('aria-expanded','false');input.autocomplete='off';
    let selected=-1,items=[];
    function hide(){list.hidden=true;input.setAttribute('aria-expanded','false');input.removeAttribute('aria-activedescendant');selected=-1;}
    function choose(city){input.value=city.name_ko;zone.value=city.timezone;hide();input.dispatchEvent(new Event('change',{bubbles:true}));}
    function draw(){const query=input.value.normalize('NFKC').toLocaleLowerCase().trim();items=destinationCatalog.filter(c=>[c.id,c.name_ko,c.name_en,c.country_code,...c.aliases].some(s=>s.normalize('NFKC').toLocaleLowerCase().includes(query))).slice(0,query?20:8);list.replaceChildren();selected=-1;for(const [i,city] of items.entries()){const option=button('',()=>choose(city),'city-option');option.tabIndex=-1;option.id=list.id+'-'+i;option.setAttribute('role','option');option.setAttribute('aria-selected','false');option.append(make('strong','',city.name_ko),make('span','hint',city.name_en+' · '+city.country_code));option.addEventListener('mousedown',e=>e.preventDefault());list.append(option);}if(!items.length)list.append(make('p','hint','목록 밖 도시는 이름과 시간대를 직접 입력할 수 있어요. 자동 추천 지원은 별도입니다.'));list.hidden=false;input.setAttribute('aria-expanded','true');}
    input.addEventListener('input',draw);input.addEventListener('focus',draw);input.addEventListener('blur',hide);
    input.addEventListener('keydown',e=>{if(e.key==='Escape'){hide();e.stopPropagation();return;}if(['ArrowDown','ArrowUp'].includes(e.key)){e.preventDefault();if(list.hidden)draw();if(!items.length)return;selected=(selected+(e.key==='ArrowDown'?1:-1)+items.length)%items.length;[...list.children].forEach((node,i)=>node.setAttribute('aria-selected',String(i===selected)));const active=list.children[selected];input.setAttribute('aria-activedescendant',active.id);active.scrollIntoView({block:'nearest'});}else if(e.key==='Enter'&&!list.hidden&&selected>=0){e.preventDefault();choose(items[selected]);}});
  }
  function navigationMemory(value){const key='travel-nav:'+state.session?.user?.id;try{if(value){sessionStorage.setItem(key,JSON.stringify(value));return value;}return JSON.parse(sessionStorage.getItem(key)||'{}');}catch{return {};}}
  function purgeJourneyMemory(){try{for(const key of Object.keys(sessionStorage))if(key.startsWith('travel-nav:')||key.startsWith('travel-intent:'))sessionStorage.removeItem(key);}catch{}}
  async function rememberDiscoveryKey(name,key){try{const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(name));const ref='travel-intent:'+Array.from(new Uint8Array(digest),v=>v.toString(16).padStart(2,'0')).join('');const saved=sessionStorage.getItem(ref);if(saved)return saved;sessionStorage.setItem(ref,key);}catch{}return key;}
  function ensureDiscoveryDraft(){const d=state.discovery;if(!d.conditions)return null;if(!d.draft){const e=d.conditions;d.draft={conditions:structuredClone(e.conditions),overrides:structuredClone(e.overrides||{}),stop_id:e.trip_context?.stop_id||e.stay_options?.find(s=>s.city===e.conditions.city&&s.start_date<=e.conditions.visit.date&&e.conditions.visit.date<=s.end_date)?.stop_id||null};}return d.draft;}
  function draftChanged(){window.DiscoveryFlow?.changed();const prior=document.activeElement;const focus=prior?.closest('#discoveryQuick')?(prior.getAttribute('aria-label')||prior.textContent):null;state.discovery.draftRevision=(state.discovery.draftRevision||0)+1;state.recommendations.conditionsDraft=true;state.discovery.dirty=true;window.WorkspaceUX?.changed();window.AccommodationTools?.contextChanged();renderDiscoveryConditions();renderRecommendationResults();if(focus){const target=[...$('#discoveryQuick').querySelectorAll('button,input,select')].find(n=>(n.getAttribute('aria-label')||n.textContent)===focus);target?.focus({preventScroll:true});}}
  function sparseChanges(previous,next){const changes={};for(const key of Object.keys(next)){if(JSON.stringify(previous?.[key])===JSON.stringify(next[key]))continue;changes[key]=next[key]&&typeof next[key]==='object'&&!Array.isArray(next[key])&&previous?.[key]&&typeof previous[key]==='object'?sparseChanges(previous[key],next[key]):next[key];}return changes;}
  function mergeDraft(base,changes){const result=structuredClone(base);for(const [key,value] of Object.entries(changes)){result[key]=value&&typeof value==='object'&&!Array.isArray(value)&&result[key]&&typeof result[key]==='object'?mergeDraft(result[key],value):structuredClone(value);}return result;}
  function setDiscoveryOverride(key,value){const d=ensureDiscoveryDraft();d.overrides[key]=value;d.conditions[key]=structuredClone(value);draftChanged();}
  function resetInheritedDiscovery(){const d=ensureDiscoveryDraft(),e=state.discovery.conditions,stop=(state.trip.stops||[]).find(s=>s.id===d.stop_id)||state.trip.stops?.[0];for(const key of ['city','visit','party','origin','origin_selection'])delete d.overrides[key];d.conditions.origin_selection={kind:'automatic'};d.conditions.party=structuredClone(state.trip.party);if(stop){d.stop_id=stop.id;d.conditions.city=knownDestination(stop.city)?.id||null;d.conditions.visit={date:stop.start_date,local_time:null,timezone:stop.timezone};d.conditions.origin=stop.base_location?{label:stop.base_location,latitude:null,longitude:null,place_id:null}:null;}draftChanged();discoveryMessage('여행의 도시·날짜·인원을 다시 가져왔어요. 새 조건은 장소 찾아보기를 누르면 적용됩니다.');}
  function renderDiscoveryQuick(){const host=$('#discoveryQuick');if(!host)return;host.replaceChildren();const d=ensureDiscoveryDraft();if(!d||!state.trip)return;const e=state.discovery.conditions,c=d.conditions,stays=e.stay_options||[];const head=make('div','quick-context');
    if(stays.length>1){const label=make('label','','어느 도시에서?');const select=make('select');select.setAttribute('aria-label','탐색 도시 구간');for(const s of stays)select.append(new Option(`${discoveryCityNames[s.city]||s.label} · ${s.start_date} ~ ${s.end_date}`,s.stop_id));select.value=d.stop_id||'';select.addEventListener('change',()=>{const s=stays.find(v=>v.stop_id===select.value);d.stop_id=s.stop_id;d.conditions.city=s.city;delete d.overrides.city;const day=s.start_date<=c.visit.date&&c.visit.date<=s.end_date?c.visit.date:s.start_date;d.conditions.visit={...c.visit,date:day,timezone:s.timezone};delete d.overrides.visit;if(day!==s.start_date)d.overrides.visit={date:day};if(c.origin)d.conditions.origin={...c.origin,latitude:null,longitude:null,place_id:null};draftChanged();discoveryMessage('선택한 도시 체류일로 맞췄어요. 위치는 다시 확인할 때까지 미확인입니다.');});label.append(select);head.append(label);}else head.append(make('strong','quick-city',discoveryCityNames[c.city]||e.unsupported_cities?.join(' · ')||'여행 도시 확인'));
    const origin=button('출발점 변경',()=>window.AccommodationTools?.editOrigin(),'text-button');head.append(origin);
    const party=button(`성인 ${c.party.adults}명${c.party.children?.length?' · 아동 '+c.party.children.length+'명':''}`,discoveryPartyForm,'secondary compact');party.setAttribute('aria-label','방문 인원 변경');head.append(party);host.append(head);
    const stop=stays.find(s=>s.stop_id===d.stop_id),start=stop?.start_date||state.trip.start_date,end=stop?.end_date||state.trip.end_date;
    const dateHost=make('div','discovery-dates');const duration=calendarDate(end)-calendarDate(start),days=Math.floor(duration/86400000)+1;
    const selectDate=value=>{if(calendarDate(value)===null||value<start||value>end){showError($('#recommendationError'),'선택한 도시 체류일에서 날짜를 골라 주세요.');return;}d.conditions.visit.date=value;d.overrides.visit={...(d.overrides.visit||{}),date:value};draftChanged();};
    if(days>0&&days<=14){dateHost.setAttribute('role','group');dateHost.setAttribute('aria-label','방문 날짜');for(let i=0;i<days;i++){const date=new Date(calendarDate(start)+i*86400000).toISOString().slice(0,10);const b=button(date.slice(5).replace('-','/'),()=>selectDate(date),'date-chip');b.setAttribute('aria-label',date);b.setAttribute('aria-pressed',String(date===c.visit.date));dateHost.append(b);}}else{const wrap=make('div');const date=field(wrap,'quick.visit.date','방문 날짜',c.visit.date,{type:'date',required:true});date.min=start;date.max=end;date.addEventListener('change',()=>{if(date.reportValidity())selectDate(date.value);});dateHost.append(wrap,make('p','hint',`${start} ~ ${end} · 달력에서 전체 기간 선택`));}host.append(dateHost);
    const row=make('div','quick-filters');
    if(window.DiscoveryExperience)window.DiscoveryExperience.quickFilters(row,c);
    else
    for(const [key,title,options] of [['categories','무엇을 찾나요?',[['restaurant','음식점'],['cafe','카페'],['attraction','볼거리']]],['recommendation_types','어떤 곳이 좋나요?',[['local_discovery','현지 탐색'],['landmark','대표 명소']]]]){
      const label=make('label','quick-choice',title),select=make('select'); select.setAttribute('aria-label',title);
      select.append(new Option(key==='categories'?'모든 장소':'현지 탐색과 대표 명소','all'));
      for(const [value,text] of options)select.append(new Option(text,value));
      const selected=c[key]||[];
      if(selected.length>1&&selected.length!==options.length)select.append(new Option(selected.map(v=>options.find(o=>o[0]===v)?.[1]||v).join(' · '),'custom'));
      select.value=selected.length===1?selected[0]:selected.length===options.length?'all':'custom';
      select.addEventListener('change',()=>{if(select.value!=='custom')setDiscoveryOverride(key,select.value==='all'?options.map(o=>o[0]):[select.value]);});
      label.append(select);row.append(label);
    }
    host.append(row);
    const required=[...(c.required?.dietary||[]),...(c.required?.accessibility||[])];if(required.length)host.append(make('p','form-note','필수: '+required.map(v=>window.WorkspaceUX?.conditionLabel(v)||v).join(' · ')));

    if(e.validation?.length&&!state.discovery.dirty){const warn=make('div','form-note');warn.append(make('p','',e.validation.map(v=>v.message).join(' ')),button('여행 기본값으로 맞추기',resetInheritedDiscovery,'secondary'));host.append(warn);}else if(Object.keys(d.overrides).some(k=>['city','visit','party','origin'].includes(k)))host.append(button('도시·날짜·인원 기본값으로',resetInheritedDiscovery,'text-button'));
    for(const warning of e.warnings||[])host.append(make('p','hint',warning.message));
  }
  function discoveryIntentPayload(){const e=state.discovery.conditions,d=ensureDiscoveryDraft(),filters=recommendationInput();delete filters.trip_version;delete filters.conditions_version;return {expected_trip_version:state.trip.version,expected_conditions_version:e.version,stop_id:d.stop_id,visit_date:d.conditions.visit.date,overrides:d.overrides,filters,...(window.DiscoveryExperience?{overrides:{...d.overrides,recommendation_types:['local_discovery','landmark']}}:{})};}
  function setExploreView(view){state.discovery.view=view;$('#bookmarkWorkspace').hidden=view!=='saved';$('#recommendationWorkspace').hidden=view==='saved';$('#discoveryQuick').hidden=view==='saved';$('.discovery-conditions').hidden=view==='saved';$('#discoveryAvailability').hidden=view==='saved'||(state.discovery.conditions?.context_state!=='unsupported_city'&&!state.discovery.conditions?.catalog_availability?.synthetic_test_candidates);$$('[data-explore-view]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.exploreView===view)));window.WorkspaceUX?.changed();if(view!=='saved')window.DiscoveryFlow?.enter();}

  function recommendationFilterValues(){return {strict:$('#reviewStrictFilter').checked,ratingEnabled:$('#ratingFilterEnabled').checked,minRating:Number($('#recommendationMinRating').value),minCount:Number($('#recommendationMinCount').value),limit:Number($('#recommendationLimit').value),...window.DiscoveryExperience?.filters()};}
  function recommendationFilterSummary(){const options=recommendationFilterValues();if(window.DiscoveryExperience)return window.DiscoveryExperience.filterSummary(options);return [options.strict?'검증된 리뷰 언어만':'리뷰 언어 제한 없음',options.ratingEnabled?`${options.minRating}점 · 평가 ${options.minCount.toLocaleString('ko-KR')}개 이상`:'평점 제한 없음',`관점별 최대 ${options.limit}곳`].join(' · ');}
  function commitRecommendationFilters(next){const previous=recommendationFilterValues();window.DiscoveryExperience?.restoreFilters(next);$('#reviewStrictFilter').checked=next.strict;$('#ratingFilterEnabled').checked=next.ratingEnabled;$('#recommendationMinRating').value=String(next.minRating);$('#recommendationMinCount').value=String(next.minCount);$('#recommendationLimit').value=String(next.limit);if(JSON.stringify(previous)!==JSON.stringify(next)){state.recommendations.optionsDirty=true;state.recommendations.filterRevision=(state.recommendations.filterRevision||0)+1;}}
  function discoveryFilterTabs(form,grid){
    const nav=make('div','filter-tablist field wide');nav.setAttribute('role','tablist');nav.setAttribute('aria-label','추천 조건 분류');grid.append(nav);
    const panels={},tabs=[];let firstInvalid=null;form.noValidate=true;
    form.addEventListener('submit',()=>{firstInvalid=null;},true);
    function activate(key,focus=false){for(const tab of tabs){const active=tab.dataset.panel===key;tab.setAttribute('aria-selected',String(active));tab.tabIndex=active?0:-1;panels[tab.dataset.panel].hidden=!active;if(active&&focus)tab.focus();}}
    for(const [key,label] of [['visit','방문·인원'],['taste','취향·예산'],['movement','이동·필수'],['quality','리뷰 기준']]){
      const panel=make('section','filter-panel field wide');panel.id='discovery-filter-'+key;panel.setAttribute('role','tabpanel');panel.setAttribute('aria-labelledby',panel.id+'-tab');
      const tab=button(label,()=>activate(key),'filter-tab');tab.id=panel.id+'-tab';tab.dataset.panel=key;tab.setAttribute('role','tab');tab.setAttribute('aria-controls',panel.id);
      tab.addEventListener('keydown',event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();const current=tabs.indexOf(tab),index=event.key==='Home'?0:event.key==='End'?tabs.length-1:(current+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;activate(tabs[index].dataset.panel,true);});
      tabs.push(tab);panels[key]=panel;nav.append(tab);grid.append(panel);
    }
    // Native validation must reveal a hidden tab before the browser focuses its field.
    form.addEventListener('invalid',event=>{if(firstInvalid){event.preventDefault();return;}firstInvalid=event.target;const panel=event.target.closest('.filter-panel');if(panel)activate(panel.id.replace('discovery-filter-',''));},true);
    activate('visit');return panels;
  }
  function discoveryFilterChoice(host,name,title,options,selected){
    const group=make('fieldset','filter-choice-group field wide');group.append(make('legend','',title));const inputs=[];
    for(const [value,label,note] of options){const row=make('label','filter-choice'),radio=make('input');radio.type='radio';radio.name=name;radio.value=value;radio.dataset.field=name;radio.checked=value===selected;const copy=make('span');copy.append(make('strong','',label));if(note)copy.append(make('span','hint',note));row.append(radio,copy);group.append(row);inputs.push(radio);}
    const error=make('p','error');error.hidden=true;group.append(error);host.append(group);return {group,value:()=>inputs.find(input=>input.checked)?.value};
  }
  function discoveryPartyForm(){
    const draft=ensureDiscoveryDraft();if(!draft)return;const epoch=state.epoch,party=structuredClone(draft.conditions.party||{});
    openDialog('이번 방문 인원',body=>{body.append(make('p','hint','이번 방문의 인원만 바꿔요. 여행 전체 인원은 그대로입니다.'));let adults,status,ages;const {form,grid}=formBase(body,'인원 반영',()=>{
      if(epoch!==state.epoch)return;const children=[];if(status.value==='present')for(const part of ages.value.split(/[,，\n]/).map(v=>v.trim()).filter(Boolean)){if(part==='?'||part==='미확인')children.push({age:null});else if(/^\d{1,2}$/.test(part)&&Number(part)<=17)children.push({age:Number(part)});else return localError(form,'party.children','0~17세를 쉼표로 구분해 주세요. 나이를 모르면 ?로 남길 수 있어요.');}
      if(status.value==='present'&&!children.length)return localError(form,'party.children','함께 가는 아동의 나이를 입력하거나 ?로 남겨 주세요.');
      closeDialog(true);setDiscoveryOverride('party',{adults:Number(adults.value),children,children_status:status.value});notice('이번 방문 인원으로 장소를 찾고 있어요.');
    });adults=field(grid,'party.adults','성인',party.adults||1,{type:'number',min:1,max:30,required:true});status=field(grid,'party.children_status','아동 동반',party.children_status||'unknown',{select:[['unknown','아직 정하지 않았어요'],['none','없어요'],['present','함께 가요']]});ages=field(grid,'party.children','아동 나이',(party.children||[]).map(c=>c.age??'?').join(', '),{wide:true,hint:'예: 5, 9 · 나이를 모르면 ?로 남겨 주세요.'});const sync=()=>{ages.closest('.field').hidden=status.value!=='present';ages.required=status.value==='present';};status.addEventListener('change',sync);sync();});
  }
  function discoveryQuickFilterForm(){
    const draft=ensureDiscoveryDraft();if(!draft||!state.trip)return;
    const original=structuredClone(draft.conditions),epoch=state.epoch,version=state.discovery.conditions.version,tripVersion=state.trip.version;
    openDialog('원하는 곳 찾기',body=>{
      body.append(make('p','filter-section-note','도시·날짜·인원은 여행에서 가져왔어요. 아래는 모두 선택 사항이에요.'));
      let tags,meal,maximum;
      const currency=original.budget?.currency||state.discovery.conditions.city_metadata?.currency||'JPY',basis=original.budget?.basis||'per_person',period=original.budget?.period||'meal';
      function collect(form){
        if(epoch!==state.epoch)return false;
        if(version!==state.discovery.conditions?.version||tripVersion!==state.trip.version){localError(form,'preferred.tags','여행 조건이 바뀌었어요. 이 창을 닫고 다시 열어 주세요.');return false;}
        const amount=maximum.value.trim();if(amount&&!/^\d+(?:\.\d{1,2})?$/.test(amount)){localError(form,'budget.amount_max','0 이상의 금액을 입력해 주세요.');return false;}
        if(amount&&original.budget?.amount_min!=null&&Number(amount)<Number(original.budget.amount_min)){localError(form,'budget.amount_max','기존 최소 금액보다 작아요. 세부 조건에서 함께 변경해 주세요.');return false;}
        const budget=amount||original.budget?.amount_min!=null?{...original.budget,currency,basis,period,amount_min:original.budget?.amount_min??null,amount_max:amount||null}:null;
        const conditions={...original,preferred:{...original.preferred,tags:commaItems(tags.value)},meal_time:meal.value||null,budget};
        return conditions;
      }
      const {form,grid}=formBase(body,'적용하기',async()=>{const conditions=collect(form);if(!conditions)return;const current=ensureDiscoveryDraft();current.overrides=mergeDraft(current.overrides,sparseChanges(original,conditions));current.conditions=conditions;closeDialog(true);draftChanged();await applyRecommendations();});
      tags=field(grid,'preferred.tags','좋아하는 음식·분위기',(original.preferred?.tags||[]).join(', '),{wide:true,hint:'예: 해산물, 조용한 곳'});
      meal=field(grid,'meal_time','식사 시간대',original.meal_time||'',{select:[['','상관없어요'],['breakfast','아침'],['lunch','점심'],['dinner','저녁']]});
      maximum=field(grid,'budget.amount_max',`${basis==='group'?'일행 전체':'1인당'} ${period==='day'?'하루':period==='visit'?'방문':'한 끼'} 최대 금액 · ${currency}`,original.budget?.amount_max??'',{hint:'비워 두면 상한 없이 찾아요.'});maximum.inputMode='decimal';
      const advanced=button('이동·필수 조건·리뷰 기준',()=>{if(!form.reportValidity())return;const conditions=collect(form);if(!conditions)return;closeDialog(true);discoveryConditionsForm(conditions);},'text-button');grid.append(advanced);advanced.classList.add('wide');
    });
  }
  function discoveryConditionsForm(prefill=null){
    if(!state.trip||!state.discovery.conditions||state.discovery.conditions.context_state==='unsupported_city')return;
    const trip=state.trip,tripVersion=trip.version,envelope=state.discovery.conditions,baseline=structuredClone(ensureDiscoveryDraft().conditions||{}),original=structuredClone(prefill||baseline),originalFilters=recommendationFilterValues(),epoch=state.epoch;
    openDialog('추천 조건',body=>{
      body.classList.add('discovery-filter-body');body.append(make('p','filter-section-note',`${discoveryCityNames[original.city]||'여행 도시'} · ${original.visit?.date||trip.start_date}. 필요한 조건만 바꾸세요.`));
      const input={};let reviewChoice,ratingChoice;
      const {form,grid}=formBase(body,'이 조건으로 찾아보기',async()=>{
        if(epoch!==state.epoch||state.trip?.id!==trip.id)return;
        if(state.trip.version!==tripVersion||state.discovery.conditions?.version!==envelope.version){const error=new Error('방문 조건이 변경되었습니다. 입력을 확인하고 최신 조건에서 다시 시도해 주세요.');error.status=409;throw error;}
        const matching=(envelope.stay_options||[]).filter(s=>s.city===input.city.value);if(matching.length&&!matching.some(s=>s.start_date<=input.date.value&&input.date.value<=s.end_date))return localError(form,'visit.date','선택한 도시의 체류 기간에서 방문일을 골라 주세요.');
        const childParts=input.children.value.split(/[,，\n]/).map(part=>part.trim()).filter(Boolean),children=[];for(const part of childParts){if(part==='?'||part==='미확인')children.push({age:null});else if(/^\d{1,2}$/.test(part)&&Number(part)<=17)children.push({age:Number(part)});else return localError(form,'party.children','아동 나이는 0~17세 또는 ?로, 쉼표로 구분해 주세요.');}
        const minimum=input.amount_min.value.trim(),maximum=input.amount_max.value.trim();if([minimum,maximum].some(v=>v&&!/^\d+(?:\.\d{1,2})?$/.test(v)))return localError(form,'budget.amount_max','예산은 0 이상의 금액을 숫자로 입력해 주세요.');if(minimum&&maximum&&Number(minimum)>Number(maximum))return localError(form,'budget.amount_max','예산 상한은 하한 이상이어야 합니다.');
        const lat=input.origin_lat.value===''?null:Number(input.origin_lat.value),lon=input.origin_lon.value===''?null:Number(input.origin_lon.value);if((lat==null)!==(lon==null))return localError(form,'origin.longitude','좌표를 사용할 때에는 위도와 경도를 함께 입력해 주세요.');
        const originLabel=input.origin_label.value.trim(),conditions={...original,city:input.city.value,visit:{date:input.date.value,local_time:input.local_time.value||null,timezone:input.timezone.value.trim()},party:{adults:Number(input.adults.value),children,children_status:children.length?'present':input.children_status.value},budget:minimum||maximum?{currency:input.currency.value,amount_min:minimum||null,amount_max:maximum||null,basis:input.basis.value,period:input.period.value}:null,origin:originLabel||lat!=null?{label:originLabel,latitude:lat,longitude:lon,place_id:original.origin?.label===originLabel?original.origin?.place_id||null:null}:null,density:input.density.value,transport:input.transport.value,meal_time:input.meal_time.value||null,radius_m:input.radius_m.value===''?null:Number(input.radius_m.value),required:{dietary:commaItems(input.required_dietary.value),accessibility:commaItems(input.required_accessibility.value)},preferred:{tags:commaItems(input.tags.value),dietary:commaItems(input.preferred_dietary.value)}};
        if(JSON.stringify(conditions.origin)!==JSON.stringify(original.origin)||conditions.city!==original.city)conditions.origin_selection={kind:conditions.origin?'manual':conditions.city!==original.city?'automatic':'none',accommodation_id:null,expected_version:null};
        const ratingEnabled=ratingChoice.value()==='required',filters={...originalFilters,strict:reviewChoice.value()==='required',ratingEnabled,minRating:ratingEnabled?Number(input.min_rating.value):originalFilters.minRating,minCount:ratingEnabled?Number(input.min_count.value):originalFilters.minCount,limit:Number(input.limit.value),...window.DiscoveryExperience?.readQuality(input)};
        const draft=ensureDiscoveryDraft();draft.overrides=mergeDraft(draft.overrides,sparseChanges(baseline,conditions));draft.conditions=conditions;const selected=(envelope.stay_options||[]).find(s=>s.city===conditions.city&&s.start_date<=conditions.visit.date&&conditions.visit.date<=s.end_date);if(selected)draft.stop_id=selected.stop_id;
        commitRecommendationFilters(filters);closeDialog(true);draftChanged();await applyRecommendations();
      });
      form.classList.add('discovery-filter-form');const panels=discoveryFilterTabs(form,grid);
      form.addEventListener('input',()=>{state.recommendations.conditionsDraft=true;window.WorkspaceUX?.captureConditionsForm(form);renderRecommendationResults();});
      function section(key,title,note){const panel=panels[key];panel.append(make('h3','filter-section-heading',title));if(note)panel.append(make('p','filter-section-note',note));const fields=make('div','form-grid');panel.append(fields);return fields;}
      const visit=section('visit','누구와, 언제 방문하나요?','여행에 저장된 날짜와 인원을 가져왔어요. 이번 방문에만 적용됩니다.');
      const stays=envelope.stay_options||[],cities=[...new Set(stays.map(s=>s.city))];
      input.city=field(visit,'city','도시',original.city&&(!cities.length||cities.includes(original.city))?original.city:cities[0]||'',{select:cities.length?cities.map(city=>[city,discoveryCityNames[city]]):[['','도시 선택'],...destinationCatalog.map(c=>[c.id,c.name_ko+' · '+c.name_en])],required:true});
      input.date=field(visit,'visit.date','방문일',original.visit?.date||trip.start_date,{type:'date',required:true});input.date.min=trip.start_date;input.date.max=trip.end_date;
      input.local_time=field(visit,'visit.local_time','방문 시각 · 선택',original.visit?.local_time||'',{type:'time',hint:'시설 현지 시각으로 입력해 주세요.'});
      input.timezone=field(visit,'visit.timezone','현지 시간대',original.visit?.timezone||(knownDestination(original.city)?.timezone||'Asia/Tokyo'),{select:[...new Set(destinationCatalog.map(c=>c.timezone))],required:true});input.timezone.closest('.field').hidden=true;
      input.adults=field(visit,'party.adults','성인',original.party?.adults??trip.party?.adults??1,{type:'number',min:1,max:30,required:true});
      input.children_status=field(visit,'party.children_status','아동 동반',original.party?.children_status||'unknown',{select:[['unknown','아직 정하지 않았어요'],['none','없어요'],['present','함께 가요']]});
      input.children=field(visit,'party.children','아동 나이',(original.party?.children||[]).map(child=>child.age??'?').join(', '),{hint:'쉼표로 구분해 주세요. 예: 5, 9 · 나이 미확인은 ?'});
      const syncChild=()=>{input.children.closest('.field').hidden=input.children_status.value!=='present';input.children.required=input.children_status.value==='present';if(input.children_status.value!=='present')input.children.value='';};input.children_status.addEventListener('change',syncChild);syncChild();
      const stayHint=make('p','hint field wide');stayHint.setAttribute('role','status');visit.append(stayHint);
      function syncStay(){const choice=discoveryStayChoice(stays,input.city.value,input.date.value,trip);input.date.min=choice.min;input.date.max=choice.max;input.timezone.value=stays.length?choice.timezone:knownDestination(input.city.value)?.timezone||choice.timezone;const changed=input.date.value!==choice.date;input.date.value=choice.date;stayHint.textContent=choice.label+(changed?' · 선택한 도시의 체류일로 맞췄어요.':'');}
      input.city.addEventListener('change',()=>{syncStay();if(input.city.value!==original.city){input.origin_label.value='';input.origin_lat.value='';input.origin_lon.value='';}});syncStay();
      const taste=section('taste','취향과 예산','선호는 순위에 참고해요. 꼭 지켜야 하는 조건은 ‘이동·필수’에서 정할 수 있어요.');
      input.tags=field(taste,'preferred.tags','좋아하는 음식·분위기',(original.preferred?.tags||[]).join(', '),{wide:true,hint:'예: 해산물, 조용한 곳 · 쉼표로 구분'});
      input.meal_time=field(taste,'meal_time','식사 시간대',original.meal_time||'',{select:[['','상관없어요'],['breakfast','아침'],['lunch','점심'],['dinner','저녁']]});
      input.density=field(taste,'density','여행 속도',original.density||'balanced',{select:[['relaxed','여유롭게'],['balanced','보통'],['packed','알차게']]});
      input.currency=field(taste,'budget.currency','예산 통화',original.budget?.currency||envelope.city_metadata?.currency||'JPY',{select:[...new Set(destinationCatalog.map(c=>c.currency))].sort()});
      input.basis=field(taste,'budget.basis','인원 기준',original.budget?.basis||'per_person',{select:[['per_person','1인당'],['group','일행 전체']]});
      input.amount_min=field(taste,'budget.amount_min','최소 금액 · 선택',original.budget?.amount_min??'',{});input.amount_min.inputMode='decimal';
      input.amount_max=field(taste,'budget.amount_max','최대 금액 · 선택',original.budget?.amount_max??'',{});input.amount_max.inputMode='decimal';
      input.period=field(taste,'budget.period','이 금액의 기준',original.budget?.period||'meal',{select:[['meal','한 끼'],['day','하루'],['visit','방문 1회']]});
      input.preferred_dietary=field(taste,'preferred.dietary','가능하면 선호하는 식단',(original.preferred?.dietary||[]).join(', '),{wide:true,hint:'필수 조건이 아닌 선호로 반영해요.'});
      const movement=section('movement','이동과 꼭 필요한 조건','입력한 필수 조건의 근거가 없으면 확인이 필요한 장소로 구분해요.');
      input.origin_label=field(movement,'origin.label','숙소 또는 출발점',original.origin?.label||'',{wide:true,maxLength:300,hint:'숙소의 지도 위치를 확인하면 이동 거리를 비교할 수 있어요.'});
      input.radius_m=field(movement,'radius_m','최대 이동 범위 · m',original.radius_m??'',{type:'number',min:100,max:50000,hint:'예: 1000m = 1km · 직선거리와 실제 이동시간은 달라요.'});
      input.transport=field(movement,'transport','이동 수단',original.transport||'walking',{select:[['walking','도보'],['transit','대중교통'],['car','자동차']]});
      input.required_dietary=field(movement,'required.dietary','꼭 지켜야 할 식단',(original.required?.dietary||[]).join(', '),{wide:true,hint:'반드시 지켜야 하는 조건만 선택해 주세요. 자료가 없으면 확인 필요로 구분합니다.'});
      input.required_accessibility=field(movement,'required.accessibility','꼭 필요한 이동·접근 조건',(original.required?.accessibility||[]).join(', '),{wide:true,hint:'자료가 없으면 조건을 충족한 것으로 간주하지 않아요.'});
      window.WorkspaceUX?.requiredChoices(input.required_dietary,['vegetarian','vegan','gluten_free','nut_free','halal'],{make});
      window.WorkspaceUX?.requiredChoices(input.required_accessibility,['wheelchair_accessible','step_free'],{make});
      const coordinateFields=make('div','form-grid field wide');coordinateFields.hidden=true;
      input.origin_lat=field(coordinateFields,'origin.latitude','출발점 위도',original.origin?.latitude??'',{type:'number',min:-90,max:90});input.origin_lat.step='any';
      input.origin_lon=field(coordinateFields,'origin.longitude','출발점 경도',original.origin?.longitude??'',{type:'number',min:-180,max:180});input.origin_lon.step='any';
      const coordinates=button('지도 좌표 직접 입력',()=>{coordinateFields.hidden=false;coordinates.hidden=true;input.origin_lat.focus();},'text-button');movement.append(coordinates,coordinateFields);
      input.origin_label.addEventListener('input',()=>{input.origin_lat.value='';input.origin_lon.value='';});
      const quality=section('quality','리뷰를 추천에 반영하는 방법','현지 탐색에만 적용해요. 대표 명소의 추천 기준은 바뀌지 않아요.');
      if(window.DiscoveryExperience){const choices=window.DiscoveryExperience.qualityFields(quality,input,originalFilters);reviewChoice=choices.review;ratingChoice=choices.rating;}else {
      reviewChoice=discoveryFilterChoice(quality,'review_language_filter','리뷰 언어',[['all','언어 제한 없이 살펴보기','리뷰 언어 자료가 없는 장소도 함께 살펴봐요.'],['required','검증된 언어 조건을 통과한 곳만','최근 관측 리뷰의 현지어·한국어 기준을 통과한 곳만 찾아요.']],originalFilters.strict?'required':'all');
      quality.append(make('p','hint field wide','언어 기능이 꺼져 있거나 검증 자료가 없으면 결과가 없을 수 있어요. 한국어 리뷰가 없다는 뜻이나 현지 주민의 비율로 해석하지 않아요.'));
      ratingChoice=discoveryFilterChoice(quality,'rating_filter','평점과 평가 수',[['all','평점 제한 없이 살펴보기','평점 자료가 없는 장소도 함께 살펴봐요.'],['required','최소 평점과 평가 수 정하기','같은 플랫폼의 5점 척도 자료만 비교해요.']],originalFilters.ratingEnabled?'required':'all');
      input.min_rating=field(quality,'rating_filter.min_rating','최소 평점 · 5점 만점',originalFilters.minRating,{type:'number',min:0,max:5,required:true});input.min_rating.step='0.1';
      input.min_count=field(quality,'rating_filter.min_count','최소 전체 평가 수',originalFilters.minCount,{type:'number',min:0,max:100000000,required:true});input.min_count.step='1';
      function syncRating(){const enabled=ratingChoice.value()==='required';for(const control of [input.min_rating,input.min_count]){control.closest('.field').hidden=!enabled;control.disabled=!enabled;}}
      ratingChoice.group.addEventListener('change',syncRating);syncRating();
      }
      function syncRating(){if(window.DiscoveryExperience)return;const enabled=ratingChoice.value()==='required';for(const control of [input.min_rating,input.min_count]){control.closest('.field').hidden=!enabled;control.disabled=!enabled;}}
      input.limit=field(quality,'limit','관점별 최대 추천 수',originalFilters.limit,{select:Array.from({length:12},(_,i)=>[String(i+1),`${i+1}곳`]),required:true});
      quality.append(make('p','hint field wide','전체 평가 수와 언어를 관측한 리뷰 수는 달라요. 조건을 통과한 장소가 적으면 실제 찾은 만큼만 보여드려요.'));
      form.addEventListener('workspace-restored',()=>{Object.assign(original,structuredClone(ensureDiscoveryDraft()?.conditions||{}));Object.assign(originalFilters,recommendationFilterValues());syncChild();syncRating();});
      window.WorkspaceUX?.restoreConditionsForm(form);syncChild();syncRating();
    });
  }
  function safeDiscoveryLink(host,url,label,cls='discovery-link'){if(!url)return;try{const parsed=new URL(url);if(!['https:','http:'].includes(parsed.protocol)||parsed.username||parsed.password)return;const link=make('a',cls,label);link.href=parsed.href;link.target='_blank';link.rel='noopener noreferrer';host.append(link);return link;}catch{}}
  function renderBookmarks(){const host=$('#bookmarkList');host.replaceChildren();$('#bookmarkCount').textContent=state.discovery.loaded?`${state.discovery.bookmarks.length}`:'';if(!state.trip){host.append(make('div','panel empty','여행을 먼저 만들면 지도 링크나 장소 이름을 저장할 수 있어요.'));return;}const filter=$('#bookmarkFilter').value;const bookmarks=state.discovery.bookmarks.filter(item=>filter==='all'||filter==='resolved'&&item.resolve_state==='resolved'||filter==='needs_confirmation'&&item.resolve_state!=='resolved'||filter==='excluded'&&item.excluded);if(!bookmarks.length){const empty=make('div','panel empty');empty.append(make('h2','',state.discovery.bookmarks.length?'이 조건에 맞는 저장 장소가 없어요':'마음에 둔 장소를 보관하세요'),make('p','',state.discovery.bookmarks.length?'보관함 표시 조건을 바꿔보세요.':'지도 링크나 이름을 먼저 저장하고, 어느 지점인지 확인할 수 있습니다. 링크 저장과 지점 확인은 별개입니다.'));if(!state.discovery.bookmarks.length)empty.append(button('첫 장소 저장',()=>bookmarkForm(),'primary'));host.append(empty);return;}for(const item of bookmarks){const card=make('article','panel bookmark-card');card.dataset.bookmarkId=item.id;const name=item.place?.display_name||item.place?.name||item.input_value;card.append(make('h3','',name),make('p','bookmark-status',bookmarkStateNames[item.resolve_state]||item.resolve_state));if(item.place?.address)card.append(make('p','hint',item.place.address));if(item.excluded)card.append(make('p','form-note','이 여행의 추천에서 제외했습니다. 저장한 메모는 유지됩니다.'));if(item.note)card.append(make('p','bookmark-note',item.note));if(item.input_kind==='url')safeDiscoveryLink(card,item.input_value,'저장한 링크 열기');if(item.reason_codes?.length){const reasons=make('ul','review-reasons');item.reason_codes.forEach(code=>reasons.append(make('li','',discoveryReasonNames[code]||`확인 필요: ${code}`)));card.append(reasons);}const actions=make('div','actions');if(item.resolve_state==='resolved'&&item.matched_place_id){if(!item.excluded)actions.append(button('일정에 넣기',()=>addPlaceToItinerary({place_id:item.matched_place_id,name,category:item.place?.category}),'secondary'));actions.append(button('장소 상세',()=>discoveryPlaceDetail(item.matched_place_id),'primary'),button(item.excluded?'추천 제외 해제':'이 여행 추천에서 제외',()=>toggleDiscoveryExclusion(item),'text-button'));}else if(item.resolve_state==='ambiguous')actions.append(button('주소를 보고 지점 선택',()=>bookmarkCandidates(item),'primary'));else if(item.resolve_state==='resolving')actions.append(make('span','hint','새로고침 후에도 같은 확인 작업을 이어봅니다.'));else actions.append(button(item.resolve_state==='failed'?'지점 확인 재시도':'저장한 지점 확인',()=>resolveBookmark(item),'secondary'));actions.append(button('개인 메모 수정',()=>bookmarkForm(item),'text-button'),button('보관함에서 삭제',()=>confirmAction('저장한 장소 삭제','이 여행의 저장 항목과 개인 메모를 삭제합니다. 다른 사람의 자료와 공용 지점은 유지됩니다.','삭제',async()=>{await api(tripPath()+'/bookmarks/'+encodeURIComponent(item.id),{method:'DELETE'});await loadDiscovery({quiet:true});}),'text-button'));card.append(actions);host.append(card);}}
  function bookmarkForm(item=null){if(!state.trip)return;const trip=state.trip,epoch=state.epoch;openDialog(item?'개인 메모 수정':'장소 저장',body=>{body.append(make('p','form-note','이 여행의 나만의 보관함입니다. 링크·이름을 저장한 다음 지점 확인을 별도로 실행합니다.'));let kind,value,note;const {grid}=formBase(body,item?'메모 저장':'보관함에 저장',async()=>{const payload=item?{expected_version:item.version,note:note.value}:{input_kind:/^https?:\/\//i.test(value.value.trim())?'url':'name',input_value:value.value.trim(),note:note.value};const result=await api(tripPath(trip)+'/bookmarks'+(item?'/'+encodeURIComponent(item.id):''),{method:item?'PATCH':'POST',body:payload});if(epoch!==state.epoch)return;closeDialog(true);await loadDiscovery({quiet:true});setExploreView('saved');focusBookmark(result.id||item?.id);discoveryMessage(result.duplicate?'이미 저장한 장소입니다. 기존 개인 메모는 덮어쓰지 않았습니다.':item?'개인 메모를 저장했습니다.':'보관함에 저장했습니다. 아직 어느 지점인지 확인한 것은 아닙니다.');});if(!item){value=field(grid,'input_value','저장할 링크 또는 이름','',{required:true,wide:true,maxLength:2000,hint:'원문 URL은 저장 후 안전 검사를 거친 지점 확인에만 사용합니다.'});}else grid.append(make('p','field wide',item.input_value));note=field(grid,'note','개인 메모',item?.note||'',{textarea:true,wide:true,maxLength:3000,hint:'개인 메모는 공용 장소 정보나 공개 추천 근거에 합쳐지지 않습니다.'});});}
  function focusBookmark(id){const card=[...$('#bookmarkList').children].find(node=>node.dataset.bookmarkId===id);(card?.querySelector('button')||$('#addBookmark')).focus();}
  async function resolveBookmark(item){if(!state.trip)return;const trip=state.trip,epoch=state.epoch,name=state.session.user.id+':'+trip.id+':resolve:'+item.id+':'+item.version;if(!state.intents.has(name))state.intents.set(name,uid());showError($('#discoveryError'));try{const result=await api(tripPath(trip)+'/bookmarks/'+encodeURIComponent(item.id)+'/resolve',{method:'POST',headers:{'Idempotency-Key':state.intents.get(name)},body:{expected_version:item.version}});if(epoch!==state.epoch)return;state.intents.delete(name);discoveryMessage('지점 확인을 접수했습니다. 저장한 링크와 메모는 그대로 유지됩니다.');await loadDiscovery({quiet:true});if(result.job_id)loadJobs().catch(fail);}catch(error){if(epoch===state.epoch)fail(error,$('#discoveryError'));}}
  function bookmarkCandidates(item){if(!state.trip)return;const trip=state.trip,epoch=state.epoch;openDialog('저장한 장소의 지점 선택',body=>{body.append(make('p','form-note','동일한 이름의 다른 지점일 수 있습니다. 도시와 주소를 대조한 뒤 하나를 선택해 주세요.'));let selected;const {grid,form}=formBase(body,'이 지점으로 연결',async()=>{if(!selected)return localError(form,'place_id','주소를 확인하고 지점을 선택해 주세요.');await api(tripPath(trip)+'/bookmarks/'+encodeURIComponent(item.id)+'/select',{method:'POST',body:{expected_version:item.version,place_id:selected}});if(epoch!==state.epoch)return;closeDialog(true);await loadDiscovery({quiet:true});discoveryMessage('선택한 지점에 연결했습니다. 예약 가능 여부는 장소 상세의 근거를 확인해 주세요.');focusBookmark(item.id);});const options=make('fieldset','field wide candidate-options');options.append(make('legend','','연결할 지점'));for(const candidate of item.candidates||[]){const label=make('label','candidate-option');const radio=make('input');radio.type='radio';radio.name='place_id';radio.dataset.field='place_id';radio.value=candidate.place_id;radio.addEventListener('change',()=>{selected=candidate.place_id;});const content=make('span');content.append(make('strong','',candidate.display_name||candidate.name),make('span','hint',`${discoveryCityNames[candidate.city]||candidate.city} · ${candidate.address}`));label.append(radio,content);options.append(label);}const error=make('p','error');error.hidden=true;options.append(error);grid.append(options);if(!(item.candidates||[]).length)options.append(make('p','muted','지점 후보가 없습니다. 저장한 이름을 다시 확인해 주세요.'));});}
  async function toggleDiscoveryExclusion(item){if(!state.trip||!item.matched_place_id)return;const epoch=state.epoch;try{await api(tripPath()+'/excluded-places/'+encodeURIComponent(item.matched_place_id),{method:item.excluded?'DELETE':'POST',...(item.excluded?{}:{body:{}})});if(epoch!==state.epoch)return;await loadDiscovery({quiet:true});discoveryMessage(item.excluded?'추천 제외를 해제했습니다.':'이 여행의 추천에서 제외했습니다. 개인 메모는 유지됩니다.');}catch(error){fail(error,$('#discoveryError'));}}
  const discoveryFactNames={public_map_tags:'공개 지도 등록 정보',closed:'휴무·폐업 여부',local_evidence:'지역 탐색 근거',iconic_evidence:'대표 명소 근거',opening_hours:'영업시간',opening_schedule:'영업시간',regular_hours:'통상 영업시간',holiday_exceptions:'휴무·예외 일정',break_times:'브레이크타임',last_order:'마지막 주문',last_entry:'마지막 입장',reservation_required:'예약 필요 여부',reservation_url:'예약 페이지',booking_url:'예약 페이지',reservation_methods:'전화·현장 접수',booking_open_rule:'예약 오픈 규칙',reservation_open_rule:'예약 오픈 규칙',min_party:'최소 예약 인원',max_party:'1예약 최대 인원',children_rule:'아동 이용 조건',facility_capacity:'시설 전체 정원',min_party_size:'최소 예약 인원',max_party_size:'1예약 최대 인원',min_people:'최소 예약 인원',max_people_per_booking:'1예약 최대 인원',children_policy:'아동 이용 조건',child_policy:'아동 이용 조건',total_capacity:'시설 전체 정원',capacity:'시설 전체 정원',cancellation_policy:'취소 규칙',deposit:'보증금',availability:'실시간 잔여석',live_availability:'실시간 잔여석',reservable:'예약 접수 기능',good_for_groups:'플랫폼의 단체 이용 표시',price:'가격',pricing:'가격',dietary:'식단 정보',accessibility:'이동·접근 정보',rating:'평점',total_rating_count:'플랫폼 전체 평가 수'};
  const factValueLabels={type:'규칙 종류',days_before_visit:'방문일 며칠 전',months_before_visit:'방문일 몇 달 전',day_of_month:'매월 오픈일',explicit_local_time:'시설 현지 오픈 시각',explicit_date:'명시한 날짜',currency:'통화',amount_min:'최소 금액',amount_max:'최대 금액',basis:'인원 기준',period:'이용 범위',per_person_or_group:'인원 기준',tax_status:'세금',tax:'세금',deposit:'보증금',deposit_scope:'보증금 기준',checked_at:'확인일',date:'날짜',local_time:'현지 시각',timezone:'시간대',party:'일행 조건',adults:'성인',children:'아동',day:'요일',weekday:'요일',start:'시작',end:'종료',opens:'여는 시각',closes:'닫는 시각',next_day:'다음 날 종료',closed:'휴무',breaks:'브레이크',last_order:'마지막 주문',last_entry:'마지막 입장',valid_for_date:'적용 방문일',value:'내용',method:'방식',phone:'전화',url:'링크',min:'최소',max:'최대',weekly:'요일별 시간',exceptions:'예외 일정',allowed:'이용 허용',minimum_age:'최소 나이',maximum_age:'최대 나이',source_groups:'독립 출처 묶음',direct_confirmation:'직접 대조 확인',level:'범위',platform:'평점 플랫폼',scale:'만점',rating:'평점',total_rating_count:'전체 별점 평가 수',comparison_cohort:'비교 자료 범위',usage_permitted:'이용 범위 확인',vegetarian:'채식',vegan:'비건',nut_free:'견과류 제외',gluten_free:'글루텐 제외',step_free:'계단 없는 접근',wheelchair_accessible:'휠체어 접근'};
  const factTermNames={rolling_days:'방문 N일 전',rolling_months:'방문 N개월 전',monthly:'매월 지정일',explicit:'지정 날짜·시각',per_person:'1인당',group:'일행 전체',meal:'한 끼',day:'하루',visit:'방문 1회',included:'포함',excluded:'별도',official_website:'공식 웹사이트',phone:'전화',walk_in:'현장 접수',city:'도시 대표',national:'국가 대표',regional:'지역 대표',synthetic:'합성 검증 자료',vegetarian:'채식',vegan:'비건',nut_free:'견과류 제외',gluten_free:'글루텐 제외',step_free:'계단 없는 접근',wheelchair_accessible:'휠체어 접근'};
  function discoveryHoursIntervals(value,empty='휴무'){
    if(!Array.isArray(value))return discoveryFactValue(value);
    if(!value.length)return empty;
    return value.map(interval=>{
      if(Array.isArray(interval)&&interval.length===2&&interval.every(item=>typeof item==='string'))return interval[0]+'–'+(interval[1]<=interval[0]?'다음 날 ':'')+interval[1];
      if(!interval||Array.isArray(interval)||typeof interval!=='object'||typeof interval.start!=='string'||typeof interval.end!=='string')return discoveryFactValue(interval);
      const clock=interval.start+'–'+(interval.end_day_offset===1?'다음 날 ':'')+interval.end;
      const extra=Object.entries(interval).filter(([key,v])=>!['start','end'].includes(key)&&!(key==='end_day_offset'&&[0,1].includes(v))).map(([key,v])=>`${{last_order:'마지막 주문',last_entry:'마지막 입장',breaks:'브레이크',notes:'추가 안내',end_day_offset:'종료일 차이'}[key]||factValueLabels[key]||key}: ${key==='breaks'?discoveryHoursIntervals(v,'등록된 브레이크 없음'):discoveryFactValue(v,key)}`);
      return clock+(extra.length?' ('+extra.join(' · ')+')':'');
    }).join(' · ');
  }
  function discoveryWeeklyHours(value){
    if(!value||Array.isArray(value)||typeof value!=='object')return discoveryFactValue(value);
    const days=['monday','tuesday','wednesday','thursday','friday','saturday','sunday'],labels=['월요일','화요일','수요일','목요일','금요일','토요일','일요일'];
    const index=key=>days.includes(key.toLowerCase())?days.indexOf(key.toLowerCase()):/^[0-6]$/.test(key)?Number(key):7;
    return Object.entries(value).sort(([a],[b])=>index(a)-index(b)).map(([day,ranges])=>`${labels[index(day)]||day}: ${discoveryHoursIntervals(ranges)}`).join('\n')||'요일별 시간 미확인';
  }
  function discoveryOpeningHours(value){
    const lines=[],known=new Set(['scope','timezone','weekly','exceptions','notes']);
    if(Object.hasOwn(value,'scope'))lines.push(value.scope==='published_regular_schedule'?'통상 영업 안내':`안내 범위: ${discoveryFactValue(value.scope)}`);
    if(Object.hasOwn(value,'timezone'))lines.push(`시설 현지 시간 · ${discoveryFactValue(value.timezone)}`);
    if(Object.hasOwn(value,'weekly'))lines.push(discoveryWeeklyHours(value.weekly));
    if(Object.hasOwn(value,'exceptions')){
      const exceptions=value.exceptions;
      const label=Array.isArray(exceptions)?exceptions.length?exceptions.map(item=>{
        if(!item||Array.isArray(item)||typeof item!=='object')return discoveryFactValue(item);
        const parts=[];if(item.closed===true)parts.push('휴무');else if(Object.hasOwn(item,'closed'))parts.push(`휴무: ${discoveryFactValue(item.closed)}`);
        if(Object.hasOwn(item,'intervals'))parts.push(discoveryHoursIntervals(item.intervals));
        for(const [key,v] of Object.entries(item))if(!['date','closed','intervals'].includes(key))parts.push(`${{notes:'추가 안내',breaks:'브레이크'}[key]||factValueLabels[key]||key}: ${key==='breaks'?discoveryHoursIntervals(v,'등록된 브레이크 없음'):discoveryFactValue(v,key)}`);
        return (Object.hasOwn(item,'date')?discoveryFactValue(item.date)+': ':'')+parts.join(' · ');
      }).join('\n'):'등록된 예외 일정 없음':discoveryFactValue(exceptions);
      lines.push('예외 일정: '+label);
    }
    if(Object.hasOwn(value,'notes')&&(!Array.isArray(value.notes)||value.notes.length))lines.push('추가 안내: '+discoveryFactValue(value.notes));
    for(const [key,v] of Object.entries(value))if(!known.has(key))lines.push(`${factValueLabels[key]||key}: ${['breaks','intervals'].includes(key)?discoveryHoursIntervals(v,key==='breaks'?'등록된 브레이크 없음':'등록된 시간 없음'):discoveryFactValue(v,key)}`);
    return lines.join('\n')||'영업시간 미확인';
  }
  function discoveryFactValue(value,key=''){
    if(value==null)return '미확인';if(typeof value==='boolean')return value?'예':'아니요';
    if(typeof value!=='object')return factTermNames[value]||String(value);
    if(key==='public_map_tags'&&!Array.isArray(value)){const labels={cuisine:'음식 종류',opening_hours:'통상 영업 안내 원문',wheelchair:'휠체어 이용',"diet:vegetarian":'채식',"diet:vegan":'비건',phone:'전화',"contact:phone":'전화',website:'등록된 웹사이트'};const lines=Object.entries(value.tags||{}).map(([field,v])=>`${labels[field]||field}: ${String(v)}`);return ['공개 지도 등록값 · 방문일 적용 및 최신 정보 확인 필요',...lines].join('\n');}
    if(key==='opening_hours'&&!Array.isArray(value))return discoveryOpeningHours(value);
    if(key==='weekly')return discoveryWeeklyHours(value);
    if(Array.isArray(value))return value.length?value.map(item=>discoveryFactValue(item)).join(' · '):'확인된 항목 없음';
    return Object.entries(value).map(([name,v])=>`${factValueLabels[name]||name}: ${discoveryFactValue(v,name)}`).join('\n');
  }
  async function discoveryPlaceDetail(placeId,run=null){if(!state.trip)return;const trip=state.trip,epoch=state.epoch;const loading=make('p','loading','확인한 사실과 출처를 불러오는 중…');openDialog('장소 상세',body=>body.append(loading));try{const detail=await api(tripPath(trip)+'/places/'+encodeURIComponent(placeId)+'/detail');if(epoch!==state.epoch||!$('#dialog').open||!loading.isConnected)return;const body=$('#dialogBody');body.replaceChildren();const place=detail.place||{},visit=run?.conditions_snapshot?.visit||state.discovery.conditions?.conditions?.visit||{},party=run?.conditions_snapshot?.party||state.discovery.conditions?.conditions?.party||trip.party||{};$('#dialogTitle').textContent=place.display_name||place.name||'장소 상세';if(place.native_name&&place.native_name!==(place.display_name||place.name))body.append(make('p','muted',place.native_name));body.append(placePhotoGallery(place,'detail'));if(place.source_kind==='public_map'){const credit=make('p','form-note');credit.append(make('span','','공개 지도 자료 · 지점과 방문 조건은 확인이 필요해요. '));safeDiscoveryLink(credit,'https://www.openstreetmap.org/copyright','© OpenStreetMap 기여자 · ODbL');body.append(credit);}body.append(make('p','',place.address||'주소 미확인'),make('p','form-note',`방문 기준 ${visit.date||'날짜 미선택'}${visit.local_time?' '+visit.local_time:''} · 성인 ${party.adults??'미확인'}명 · 아동 ${party.children_status==='unknown'?'미확인':(party.children||[]).length+'명'}. 미확인 정보는 이용 가능으로 간주하지 않습니다.`));if(place.synthetic||detail.synthetic||(detail.sources||[]).some(source=>source.source_type==='synthetic'))body.append(make('p','badge warn','합성 검증 자료 · 실제 장소 추천 아님'));const savedPlace=state.discovery.bookmarks.some(b=>b.matched_place_id===placeId);const save=button(savedPlace?'저장한 장소':'보관함에 저장',async()=>{save.disabled=true;try{const result=await api(tripPath(trip)+'/bookmarks',{method:'POST',body:{input_kind:'place',input_value:placeId,note:''}});if(epoch!==state.epoch)return;save.textContent='저장한 장소';await loadDiscovery({quiet:true});notice(result.duplicate?'이미 저장한 장소예요.':'보관함에 저장했어요.');}catch(e){save.disabled=false;const error=make('p','error',e.message);save.after(error);}},'primary');save.disabled=savedPlace;body.append(save,button('일정에 넣기',()=>addPlaceToItinerary({...place,place_id:placeId},run),'secondary'));const movement=comparisonCandidates(run).get(placeId)?.movement;if(movement)body.append(make('p','form-note',recommendationMovement(movement)));if(run?.origin_status==='stale')body.append(make('p','form-note','이전 숙소 기준의 이동 자료입니다. 현재 출발점으로 추천을 다시 확인해 주세요.'));safeDiscoveryLink(body,place.canonical_url||place.source_url,'장소 지도·공식 링크');const sources=new Map((detail.sources||[]).map(source=>[source.id,source]));const facts=detail.facts||[];const categories=[['운영과 방문',field=>/hour|schedule|break|last_order|entry|holiday|closed|accessibility|dietary/.test(field)],['예약과 인원',field=>/reserv|booking|party|people|child|capacity|cancellation|deposit|availability|group/.test(field)],['가격과 평가',field=>/price|pricing|rating/.test(field)]];const shown=new Set();for(const [heading,matches] of categories){const section=make('section','detail-section fact-group');section.open=heading==='운영과 방문';section.append(make('h3','',heading));const rows=facts.filter(fact=>matches(fact.field)&&!shown.has(fact.id));if(!rows.length)section.append(make('p','muted','이 항목의 확인된 자료가 아직 없습니다.'));for(const fact of rows){shown.add(fact.id);renderDiscoveryFact(section,fact,sources.get(fact.source_id),visit,placeId);}body.append(section);}const rest=facts.filter(fact=>!shown.has(fact.id));if(rest.length){const section=make('section','detail-section fact-group');section.append(make('h3','','추가 근거'));rest.forEach(fact=>renderDiscoveryFact(section,fact,sources.get(fact.source_id),visit,placeId));body.append(section);}body.append(make('p','form-note','시설 정원·단체 이용 표시는 이 인원으로 예약할 수 있다는 확인이 아닙니다. 날짜·시각·인원을 지정한 실제 슬롯이 없으면 잔여석은 미확인입니다. 외부 예약 링크 이동은 예약 완료가 아닙니다.'));window.ProductTools?.feedbackButton(body,placeId);const review=detail.review_evidence;if(window.DiscoveryExperience)window.DiscoveryExperience.evidenceDetail(body,review,place,run);else if(review){const section=make('section','detail-section');section.append(make('h3','','최근 리뷰 언어'));reviewCounts(section,review.counts);reasonList(section,reviewReasons(review));if(!review.counts)section.append(make('p','muted','현재 표시할 수 있는 관측 근거가 없습니다. 미확인을 한국어 0%로 표시하지 않습니다.'));reviewScope(section,review.coverage,review.checked_at,review.expires_at);body.append(section);}const sourceSection=make('section','detail-section fact-group');sourceSection.append(make('h3','','출처와 확인 시점'));if(!sources.size)sourceSection.append(make('p','muted','연결된 출처가 아직 없습니다.'));for(const source of sources.values()){const row=make('div','source-card');row.append(make('strong','',source.source_type==='official'?'공식 출처':source.source_group==='OpenStreetMap'?'공개 지도 자료':source.source_type||'자료 출처'),make('p','hint',`확인 ${reviewDate(source.checked_at)} · ${(source.read_confirmed===true||source.read_confirmed===1)?'원문 대조 확인':source.status==='active'?'자료 사용 가능 · 원문 대조 별도 확인':source.status||'확인 필요'}`));window.ProductTools?.sourceLink(safeDiscoveryLink(row,source.url,'출처 열기'),placeId,source.id,run?.run_id||null);sourceSection.append(row);}body.append(sourceSection);}catch(error){if(epoch===state.epoch&&$('#dialog').open&&loading.isConnected){$('#dialogBody').replaceChildren(make('p','error',error.message));}}}
  function renderDiscoveryFact(host,fact,source,visit,placeId){const row=make('div','discovery-fact');const status={verified:'근거 대조 확인',provisional:'잠정 정보 · 확인 필요',unknown:'미확인',conflict:'근거 상충 · 확인 필요'}[fact.status]||'미확인';const freshness=typeof fact.freshness==='object'?fact.freshness?.state:fact.freshness;const stale=['expired','stale'].includes(freshness)||Boolean(fact.expires_at&&Date.parse(fact.expires_at)<=Date.now());row.append(make('h4','',discoveryFactNames[fact.field]||fact.field),make('p','fact-state',status+(stale?' · 확인 기한 경과':'')));if(fact.status==='unknown')row.append(make('p','muted','확인할 수 있는 값이 없습니다.'));else row.append(make('p','fact-value',discoveryFactValue(fact.value,fact.field)));if(['capacity','facility_capacity','total_capacity'].includes(fact.field))row.append(make('p','hint','시설 전체 정원이며, 한 번에 예약 가능한 인원이나 잔여석이 아닙니다.'));if(/hour|schedule/.test(fact.field)&&fact.valid_for_date!==visit.date)row.append(make('p','hint','통상 안내입니다. 미래 방문일의 확정 영업시간을 뜻하지 않습니다.'));row.append(make('p','hint',`확인 ${reviewDate(fact.checked_at)}${fact.valid_for_date?' · 적용일 '+fact.valid_for_date:''}${fact.expires_at?' · 유효 기한 '+reviewDate(fact.expires_at):''}`));if(fact.reason_codes?.length)reasonList(row,fact.reason_codes);if(source)window.ProductTools?.sourceLink(safeDiscoveryLink(row,source.url,'이 사실의 출처 확인'),placeId,source.id);window.ProductTools?.factButton(row,placeId,fact);if(['reservation_url','booking_url'].includes(fact.field)&&fact.status==='verified'&&!stale&&fact.usable===true&&source?.status==='active'&&(source?.read_confirmed===true||source?.read_confirmed===1)&&(source?.display_permitted===true||source?.display_permitted===1)){const url=typeof fact.value==='string'?fact.value:fact.value?.url;safeDiscoveryLink(row,url,'확인한 외부 예약 페이지로 이동','discovery-booking-link');}host.append(row);}
  async function loadDiscoveryAdmin(){const allowed=state.session?.user?.role==='admin';$('#discoveryAdmin').hidden=!allowed;if(!allowed){state.discovery.packs=[];$('#discoveryPacks').replaceChildren();return;}if(state.tab!=='explore')return;const epoch=state.epoch;const result=await api('/admin/discovery-packs');if(epoch!==state.epoch||state.session?.user?.role!=='admin')return;state.discovery.packs=result.items||[];renderDiscoveryPacks();}
  function renderDiscoveryPacks(){const host=$('#discoveryPacks');host.replaceChildren();if(!state.discovery.packs.length){host.append(make('p','muted','아직 등록된 후보 자료가 없습니다. 도시별 실제 지점과 근거를 검수한 뒤 등록하세요.'));return;}for(const pack of state.discovery.packs){const card=make('article','panel discovery-pack');const places=pack.places||[];const issues=places.flatMap(place=>place.facts||[]).filter(fact=>fact.status==='unknown'||fact.status==='conflict'||fact.usable===false);card.append(make('h3','',`${discoveryCityNames[pack.city]||pack.city} · ${pack.version}`),make('p','',`${pack.synthetic?'합성 검증 자료 · 실제 후보 아님':'실제 지점 조사 자료'} · ${places.length}곳 · ${{approved:'승인됨',needs_review:'검수 필요',disabled:'사용 중지'}[pack.status]||pack.status}`),make('p','hint',`미확인·상충·사용 불가 사실 ${issues.length}건. 값이 없다는 이유로 조건을 충족했다고 판단하지 않습니다.`));const details=make('details','review-details');details.append(make('summary','','지점·운영·예약 근거 검수표'));for(const place of places){const row=make('section','candidate-review');row.append(make('h4','',place.display_name||place.name||'후보 지점'),make('p','hint',place.address||'주소 미확인'));safeDiscoveryLink(row,place.canonical_url||place.source_url,'지점 링크 확인');const facts=place.facts||[];if(!facts.length)row.append(make('p','muted','등록된 운영·예약 사실이 없습니다.'));for(const fact of facts){const factLine=make('p','hint',`${discoveryFactNames[fact.field]||fact.field}: ${{verified:'근거 대조 확인',provisional:'잠정 정보',unknown:'미확인',conflict:'근거 상충'}[fact.status]||fact.status}${fact.usable===false?' · 사용 불가':''} · 확인 ${reviewDate(fact.checked_at)}`);row.append(factLine);}for(const source of place.sources||[]){const sourceCard=make('div','source-card');sourceCard.append(make('strong','',source.source_type||'출처'),make('p','hint',`독립 출처 묶음: ${source.source_group||'미확인'} · 원문 ${source.read_confirmed?'대조 확인':'미확인'} · 표시 ${source.display_permitted?'허용 확인':'미허용'} · ${source.status||'상태 미확인'}`));safeDiscoveryLink(sourceCard,source.url,'출처 열기');if(source.id)sourceCard.append(button('출처 사용·확인 상태 관리',()=>discoverySourceForm(source,pack),'text-button'));row.append(sourceCard);}details.append(row);}card.append(details,button('후보 자료 승인·중지',()=>discoveryPackApproval(pack),'secondary'));host.append(card);}}
  function importDiscoveryPackForm(){openDialog('후보 자료 가져오기',body=>{body.append(make('p','form-note','지점별 이름·주소·운영·예약 사실과 출처를 포함한 검수용 JSON을 가져옵니다. 등록만으로 승인되지 않습니다. 실제 자료와 합성 검증 자료의 표시를 구분해 주세요.'));let content;const {grid,form}=formBase(body,'검수 대기 상태로 등록',async()=>{let data;try{data=JSON.parse(content.value);if(!data||Array.isArray(data)||typeof data!=='object')throw new Error();}catch{return localError(form,'pack','올바른 후보 자료 JSON 객체를 입력해 주세요.');}await api('/admin/discovery-packs',{method:'POST',body:data});closeDialog(true);await loadDiscoveryAdmin();discoveryMessage('후보 자료를 등록했습니다. 지점·출처를 검수한 뒤 별도로 승인해 주세요.');});const wrapper=make('div','field wide');const label=make('label','','후보 자료 JSON 파일 · 최대 1 MiB');const file=make('input');file.type='file';file.accept='.json,application/json';file.id='discoveryPackFile';label.htmlFor=file.id;file.addEventListener('change',async()=>{const selected=file.files?.[0];if(!selected)return;if(selected.size>1024*1024){localError(form,'pack','후보 자료 파일은 최대 1 MiB입니다.');return;}try{content.value=await selected.text();}catch{localError(form,'pack','파일을 읽을 수 없습니다. 내용을 직접 붙여 넣어 주세요.');}});wrapper.append(label,file);grid.append(wrapper);content=field(grid,'pack','후보 자료 JSON','',{textarea:true,wide:true,required:true,maxLength:1000000,hint:'각 사실의 확인일·유효 기한, 출처의 원문 대조·표시 권한을 함께 기록합니다.'});content.rows=12;});}
  function discoveryPackApproval(pack){openDialog('후보 자료 승인·중지',body=>{body.append(make('p','form-note',`${discoveryCityNames[pack.city]||pack.city} · ${pack.version}${pack.synthetic?' · 합성 검증 전용':''}`),make('p','muted','후보 승인은 지점과 출처를 검수했다는 기록입니다. 리뷰 언어 품질 통과나 특정 날짜·인원의 예약 가능을 대신하지 않습니다.'));let status,evidence;const {grid}=formBase(body,'검수 결과 저장',async()=>{await api('/admin/discovery-packs/'+encodeURIComponent(pack.id||pack.pack_id),{method:'PATCH',body:{status:status.value,evidence:evidence.value.trim()}});closeDialog(true);await loadDiscoveryAdmin();});status=field(grid,'status','검수 결과',pack.status||'needs_review',{select:[['needs_review','검수 필요'],['approved','후보 사용 승인'],['disabled','사용 중지']]});evidence=field(grid,'evidence','지점·출처·미확인 항목 검수 기록','',{textarea:true,required:true,wide:true,maxLength:1000,hint:'10자 이상 · 승인 또는 중지한 이유를 구체적으로 남겨 주세요.'});evidence.minLength=10;});}
  function discoverySourceForm(source,pack){openDialog('출처 사용·확인 상태',body=>{safeDiscoveryLink(body,source.url,'출처 원문 확인');body.append(make('p','form-note','URL이 있다는 사실과 원문을 실제로 읽고 대조했다는 사실은 다릅니다. 자료 사용 범위를 철회하면 관련 근거는 사용할 수 없게 됩니다.'));let status,version,evidence,read,display;const {grid}=formBase(body,'출처 검토 저장',async()=>{await api('/admin/discovery-sources/'+encodeURIComponent(source.id),{method:'PATCH',body:{expected_version:source.version,status:status.value,read_confirmed:read.checked,display_permitted:display.checked,policy_version:version.value.trim(),evidence:evidence.value.trim()}});closeDialog(true);await loadDiscoveryAdmin();if(state.trip)await loadDiscovery({quiet:true});});status=field(grid,'status','자료 사용 상태',source.status||'pending',{select:[['pending','확인 대기'],['active','사용 가능'],['revoked','사용 철회']]});version=field(grid,'policy_version','이용 범위 검토 버전',source.policy_version||pack.version,{required:true,maxLength:100,hint:'기존 사실과 연결된 정책 버전도 함께 확인해 주세요.'});read=reviewCheckbox(grid,'read_confirmed','원문을 읽고 지점·기간을 대조함',source.read_confirmed===true||source.read_confirmed===1);display=reviewCheckbox(grid,'display_permitted','제품에 표시할 이용 범위를 확인함',source.display_permitted===true||source.display_permitted===1);evidence=field(grid,'evidence','확인 또는 철회 근거','',{textarea:true,required:true,wide:true,maxLength:1000});evidence.minLength=10;});}
  $('#importDiscoveryPack').addEventListener('click',importDiscoveryPackForm);

  $$('[data-explore-view]').forEach(b=>b.addEventListener('click',()=>setExploreView(b.dataset.exploreView)));
  $('#editDiscoveryConditions').addEventListener('click',discoveryQuickFilterForm);
  $('#addBookmark').addEventListener('click',()=>bookmarkForm());
  $('#refreshBookmarks').addEventListener('click',()=>loadDiscovery().catch(error=>fail(error,$('#discoveryError'))));
  $('#bookmarkFilter').addEventListener('change',renderBookmarks);

  const recommendationTypeNames={local_discovery:'현지어 리뷰로 찾기',landmark:'유명한 곳',reference:'주변 장소 참고하기'};
  const recommendationReasonNames={
    PUBLIC_MAP_UNVERIFIED:'공개 지도에 등록된 장소입니다. 지점과 영업 상태를 확인해 주세요.',ORIGIN_CHANGED:'숙소 또는 출발점이 변경되었습니다. 현재 기준으로 다시 확인해 주세요.',ORIGIN_COORDINATES_UNKNOWN:'출발점 위치가 아직 확인되지 않았습니다.',ORIGIN_OR_COORDINATES_UNKNOWN:'출발점이나 장소 좌표가 미확인입니다.',ROUTE_PROVIDER_DISABLED:'경로 제공자가 꺼져 있어 실제 도보시간은 미확인입니다.',ROUTE_EXPIRED:'이동 근거가 만료되었습니다.',ROUTE_POLICY_CHANGED:'경로 자료의 사용 범위가 변경되었습니다.',ROUTE_ACCESSIBILITY_UNKNOWN:'이 경로의 이동·접근 조건을 확인하지 못했습니다.',ROUTE_CONTEXT_OR_FRESHNESS_UNCONFIRMED:'현재 출발점·수단·시각에 맞는 경로 근거가 필요합니다.',ROUTE_NOT_CHECKED:'실제 경로를 아직 확인하지 않았습니다.',ROUTE_DATA_INVALID:'사용할 수 있는 경로 자료가 없습니다.',NO_ROUTE_ELIGIBLE_CANDIDATES:'경로를 확인할 수 있는 후보가 없습니다.',

    NO_CANDIDATES:'이 도시에서 검수한 후보 자료가 아직 없습니다.',INSUFFICIENT_RESULTS:'조건을 확인한 후보가 요청한 수보다 적습니다.',
    REVIEW_PRODUCTION_DISABLED:'최근 리뷰 언어 기능이 아직 꺼져 있습니다.',REVIEW_LANGUAGE_UNAVAILABLE:'엄격 언어 조건에 사용할 검증된 관측 근거가 없습니다.',REVIEW_LANGUAGE_UNSUPPORTED:'현재 자료로 엄격 언어 조건을 적용할 수 없습니다.',REVIEW_GATE_UNAVAILABLE:'리뷰 언어 자료의 품질·이용 범위를 확인해야 합니다.',
    REVIEW_STRICT_UNAVAILABLE:'엄격 리뷰 언어 조건은 현재 미지원입니다.',REVIEW_STRICT_FAILED:'최근 관측 리뷰가 선택한 엄격 언어 조건을 충족하지 않습니다.',
    REVIEW_UNAVAILABLE:'사용 가능한 리뷰 관측 자료가 없습니다.',CLASSIFICATION_QUALITY_UNVERIFIED:'도시별 원문 언어 품질이 검증되지 않았습니다.',
    CLOSED:'휴무·폐업 근거가 있어 이번 후보에서 제외합니다.',PLACE_CLOSED:'휴무·폐업 근거가 있어 이번 후보에서 제외합니다.',CLOSED_FOR_VISIT:'선택한 방문 시각에는 영업하지 않습니다.',
    OPENING_HOURS_UNKNOWN:'방문일의 영업시간을 확인해야 합니다.',HOURS_UNVERIFIED_FOR_DATE:'이 방문일에 적용되는 영업시간을 확인해야 합니다.',FUTURE_HOURS_UNCONFIRMED:'통상 영업표를 미래 방문일의 확정 정보로 사용할 수 없습니다.',
    PARTY_TOO_LARGE:'한 번에 예약할 수 있는 인원 상한을 넘습니다.',PARTY_TOO_SMALL:'최소 예약 인원보다 적습니다.',PARTY_CAPACITY_UNKNOWN:'이 인원으로 이용할 수 있는지 확인해야 합니다.',MAX_PARTY_UNKNOWN:'1예약 최대 인원을 확인해야 합니다.',
    CHILD_RULE_UNKNOWN:'아동 이용 조건을 확인해야 합니다.',CHILD_AGE_UNKNOWN:'아동 나이가 미확인이라 인원 적합성을 확정할 수 없습니다.',CHILD_NOT_ALLOWED:'확인한 아동 이용 조건과 맞지 않습니다.',
    DIETARY_UNKNOWN:'필수 식단 조건에 대한 근거가 없습니다.',DIETARY_MISMATCH:'필수 식단 조건과 맞지 않습니다.',ACCESSIBILITY_UNKNOWN:'필수 이동·접근 조건을 확인해야 합니다.',ACCESSIBILITY_MISMATCH:'필수 이동·접근 조건과 맞지 않습니다.',
    ORIGIN_UNKNOWN:'출발점 위치를 확인하지 못해 이동 기준을 계산할 수 없습니다.',LOCATION_UNKNOWN:'장소 위치 근거가 부족합니다.',MOVEMENT_UNAVAILABLE:'이동 근거가 부족합니다.',ROUTE_ESTIMATE_ONLY:'실제 경로가 아닌 직선거리 추정입니다.',RADIUS_EXCEEDED:'선택한 이동 범위를 벗어납니다.',
    PRICE_UNKNOWN:'같은 통화·인원·이용 범위로 비교할 가격 자료가 없습니다.',PRICE_BASIS_MISMATCH:'예산과 가격의 인원·이용 범위가 다릅니다.',PRICE_CURRENCY_MISMATCH:'예산과 가격의 통화가 다릅니다.',BUDGET_EXCEEDED:'확인된 가격이 예산 상한을 넘습니다.',
    RATING_UNKNOWN:'같은 플랫폼·척도의 평점 자료가 없습니다.',RATING_BELOW_MIN:'선택한 최소 평점에 미달합니다.',RATING_COUNT_BELOW_MIN:'선택한 전체 평가 수 기준에 미달합니다.',RATING_POLICY_UNAVAILABLE:'평점 자료의 이용 범위를 확인해야 합니다.',
    SOURCE_POLICY_UNAVAILABLE:'출처의 원문 대조·사용 범위를 확인해야 합니다.',SOURCE_POLICY_CHANGED:'출처의 이용 범위가 변경되었습니다.',SOURCE_DATA_CHANGED:'근거의 유효 기간·이용 범위가 변경되어 이전 결과를 표시할 수 없습니다. 현재 자료로 새 요청을 만들어 주세요.',FACT_STALE:'사실 확인 기한이 지났습니다.',FACT_CONFLICT:'출처의 사실이 상충해 확인이 필요합니다.',
    USER_EXCLUDED:'이 여행에서 직접 제외한 장소입니다.',DIVERSITY_LIMIT:'같은 체인·권역으로의 쏠림을 줄이기 위해 제외했습니다.',MISSING_SCORE_COMPONENT:'추천 순위를 계산할 일부 근거가 부족합니다.',INSUFFICIENT_EVIDENCE:'추천 근거가 부족합니다.',
    BUDGET_EXHAUSTED:'새 호출 예산이 부족합니다. 저장된 결과는 계속 읽을 수 있습니다.',PROVIDER_UNAVAILABLE:'자료 공급자가 응답하지 않았습니다. 저장된 결과는 유지됩니다.',POLICY_UNAVAILABLE:'이 자료를 사용할 권한을 확인해야 합니다.'
  };
  Object.assign(recommendationReasonNames,{
    PROVIDER_CONTRACT_UNVERIFIED:'현재 공급자 build의 원문·정렬·페이지 계약을 아직 검증하지 못했습니다.',EXTERNAL_LINK_REVOKED:'공식 지점과 리뷰 지점의 연결이 철회되었습니다.',CITY_LANGUAGE_PROFILE_UNREVIEWED:'이 도시의 현지어 범위를 검수 중입니다.',OVERLAPPING_LANGUAGE_SETS:'현지어와 한국어가 겹치는 도시는 현재 언어 기준을 지원하지 않습니다.',NO_REVIEW_OBSERVATION:'유효한 리뷰 관측 자료가 없습니다.',SYNTHETIC_PROVIDER:'합성 공급자는 실제 리뷰 기능을 활성화하지 않습니다.',REVIEW_QUALITY_INSUFFICIENT:'언어 품질 평가 표본이 기준에 미달합니다.',REVIEW_STALE:'리뷰 근거가 만료되어 재확인이 필요합니다.',LANGUAGE_THRESHOLD_FAILED:'관측한 언어 비율이 이번 요청의 기준에 미달합니다.',ICONIC_EVIDENCE_MISSING:'공식·플랫폼·편집 자료에서 유명함 근거를 확인하지 못했습니다.',

    DURING_BREAK:'방문 시각이 확인된 브레이크타임에 해당합니다.',AFTER_LAST_ORDER:'선택한 시각이 마지막 주문 시간 이후입니다.',AFTER_LAST_ENTRY:'선택한 시각이 마지막 입장 시간 이후입니다.',BREAK_TIMES_UNKNOWN:'브레이크타임을 확인해야 합니다.',LAST_ORDER_UNKNOWN:'마지막 주문 시각을 확인해야 합니다.',LAST_ENTRY_UNKNOWN:'마지막 입장 시각을 확인해야 합니다.',LIVE_AVAILABILITY_NOT_CONFIRMED:'이 방문일·시각·인원의 실제 잔여석은 확인되지 않았습니다.',
    REVIEW_REQUIRED_UNSUPPORTED:'선택한 엄격 리뷰 언어 조건에 사용할 검증된 자료가 없습니다.',REVIEW_LANGUAGE_FAILED:'관측 리뷰가 선택한 엄격 언어 기준을 충족하지 않습니다.',REVIEW_NOT_QUALIFIED:'리뷰 언어 자료의 품질·이용 범위 검증이 필요합니다.',QUALIFIED_LANGUAGE_SIGNAL_UNKNOWN:'검증된 관측 리뷰의 언어 지표가 없습니다.',
    MIN_PARTY_UNKNOWN:'최소 예약 인원을 확인해야 합니다.',MAX_PARTY_UNKNOWN:'한 번에 예약할 수 있는 최대 인원을 확인해야 합니다.',MIN_PARTY_MISMATCH:'선택한 인원이 최소 예약 인원보다 적습니다.',MAX_PARTY_MISMATCH:'선택한 인원이 1예약 최대 인원을 넘습니다.',PARTY_LIMIT_CONFLICT:'예약 인원에 대한 근거가 상충합니다.',
    CHILD_PARTY_UNKNOWN:'아동 동반 여부를 확인해야 이 장소의 연령 조건을 판단할 수 있습니다.',CHILDREN_RULE_UNKNOWN:'아동 이용 조건을 확인해야 합니다.',CHILDREN_NOT_ALLOWED:'해당 근거에서는 아동 이용을 허용하지 않습니다.',CHILD_AGE_RULE_UNKNOWN:'아동 최소 나이 기준을 확인해야 합니다.',CHILD_AGE_MISMATCH:'아동 나이가 확인된 이용 기준과 맞지 않습니다.',CHILD_PRICE_UNKNOWN:'아동 가격 기준을 확인해야 합니다.',
    HOURS_UNKNOWN:'영업시간을 확인해야 합니다.',HOURS_DATE_UNCONFIRMED:'이 방문일에 적용되는 영업시간을 확인해야 합니다.',HOURS_CONFLICT:'영업시간에 대한 근거가 상충합니다.',HOURS_EXCEPTION_UNRESOLVED:'방문일의 휴무·예외 일정을 확인해야 합니다.',HOURS_INVALID:'영업시간 형식을 확인하지 못했습니다.',HOURS_WEEKDAY_UNKNOWN:'방문 요일의 영업시간이 없습니다.',HOURS_TIMEZONE_MISMATCH:'영업시간과 방문 조건의 시간대가 다릅니다.',CLOSED_ON_VISIT:'방문일에 휴무인 근거가 있습니다.',OUTSIDE_OPENING_HOURS:'선택한 시각이 확인된 영업시간 밖입니다.',VISIT_TIME_UNKNOWN:'방문 시각이 없어 시간 적합성을 확인하지 못했습니다.',VISIT_TIME_INVALID:'방문 시각을 확인해야 합니다.',VISIT_TIME_AMBIGUOUS:'현지 시간 변경으로 방문 시각을 하나로 확정할 수 없습니다.',
    ORIGIN_OR_COORDINATES_UNKNOWN:'출발점 또는 장소 좌표가 없어 이동 근거가 부족합니다.',DISTANCE_ESTIMATE:'이동은 직선거리 추정이며 실제 경로·시간은 확인하지 않았습니다.',OUTSIDE_RADIUS:'선택한 이동 범위를 벗어납니다.',RADIUS_UNKNOWN:'좌표가 없어 이동 범위를 확인하지 못했습니다.',
    OUTSIDE_BUDGET:'확인된 가격이 선택한 예산 범위 밖입니다.',BUDGET_UNKNOWN:'비교할 예산 범위가 입력되지 않았습니다.',PRICE_UNIT_MISMATCH:'가격과 예산의 통화·인원·이용 범위가 달라 비교할 수 없습니다.',PRICE_RANGE_UNCONFIRMED:'가격 범위의 일부가 미확인입니다.',
    RATING_BELOW_THRESHOLD:'같은 플랫폼의 평점 또는 전체 평가 수 기준에 미달합니다.',RATING_PLATFORM_MISMATCH:'평점 플랫폼이 달라 같은 기준으로 비교할 수 없습니다.',RATING_UNSUPPORTED:'평점의 척도·사용 범위를 확인해야 합니다.',QUALITY_COMPARISON_UNSUPPORTED:'같은 플랫폼의 평점 비교 자료가 부족합니다.',
    PREFERENCE_UNKNOWN:'좋아하는 음식·분위기를 입력하면 취향 기준을 계산할 수 있습니다.',INDEPENDENT_LOCAL_EVIDENCE_UNKNOWN:'독립된 지역 자료가 부족합니다.',ICONIC_EVIDENCE_UNKNOWN:'대표 명소로 판단할 근거가 부족합니다.',EVIDENCE_UNKNOWN:'추천 순위 계산에 필요한 근거가 부족합니다.',FACT_UNCONFIRMED:'해당 사실의 원문·지점·확인 시점을 검증해야 합니다.',
    PLACE_IDENTITY_UNVERIFIED:'지점과 주소가 확인되지 않았습니다.',PACK_UNAPPROVED:'관리자 검수가 끝나지 않은 후보 자료입니다.',BUSINESS_STATUS_CONFLICT:'휴무·폐업 상태에 대한 근거가 상충합니다.',CITY_MISMATCH:'선택한 도시와 다릅니다.',CATEGORY_MISMATCH:'선택한 장소 분류와 다릅니다.',CHAIN_LIMIT:'같은 체인의 반복을 줄이기 위해 이번 순위에서 제외했습니다.',NEIGHBORHOOD_LIMIT:'특정 동네에 쏠리지 않도록 이번 순위에서 제외했습니다.',CATEGORY_LIMIT:'한 장소 분류의 편중을 줄이기 위해 이번 순위에서 제외했습니다.',RESULT_LIMIT:'조건을 통과했지만 요청한 최대 결과 수 밖입니다.'
  });
  function clearRecommendationScope(){clearTimeout(recommendationPollTimer);recommendationPollTimer=null;state.recommendations={runs:[],active:null,displayed:null,serial:0,submitting:false,optionsDirty:false,conditionsDraft:false,loaded:false};if($('#recommendationRunStatus'))delete $('#recommendationRunStatus').dataset.progressKey;['#recommendationResults','#recommendationRunStatus','#recommendationHistory','#comparisonTray'].forEach(s=>$(s)?.replaceChildren());if($('#recommendationHistoryWrap'))$('#recommendationHistoryWrap').hidden=true;if($('#comparisonTray'))$('#comparisonTray').hidden=true;if($('#recommendationError'))showError($('#recommendationError'));if($('#recommendationDraft'))showError($('#recommendationDraft'));if($('#reviewStrictFilter'))$('#reviewStrictFilter').checked=false;if($('#ratingFilterEnabled'))$('#ratingFilterEnabled').checked=false;if($('#recommendationMinRating'))$('#recommendationMinRating').value='4.2';if($('#recommendationMinCount'))$('#recommendationMinCount').value='200';if($('#recommendationLimit'))$('#recommendationLimit').value='6';}
  function recommendationInput(){return {trip_version:state.trip.version,conditions_version:state.discovery.conditions.version,review_language_filter:{required:$('#reviewStrictFilter').checked,apply_only_if_qualified:true,apply_to:['local_discovery']},rating_filter:{enabled:$('#ratingFilterEnabled').checked,min_rating:Number($('#recommendationMinRating').value),min_count:Number($('#recommendationMinCount').value),apply_to:['local_discovery']},limit:Number($('#recommendationLimit').value),...window.DiscoveryExperience?.requestFilters()};}
  function restoreRecommendationOptions(run){const request=run?.request||{};if(state.recommendations.optionsDirty)return;window.DiscoveryExperience?.restoreRequest(request);if(request.review_language_filter)$('#reviewStrictFilter').checked=request.review_language_filter.required===true;if(request.rating_filter){$('#ratingFilterEnabled').checked=request.rating_filter.enabled===true;$('#recommendationMinRating').value=request.rating_filter.min_rating??'4.2';$('#recommendationMinCount').value=request.rating_filter.min_count??'200';}if(request.limit)$('#recommendationLimit').value=request.limit;}
  function recommendationStale(run){return run&&(run.input_status==='stale'||run.conditions_version!=null&&run.conditions_version!==state.discovery.conditions?.version||run.request?.conditions_version!=null&&run.request.conditions_version!==state.discovery.conditions?.version||run.trip_version!=null&&run.trip_version!==state.trip?.version);}
  async function loadRecommendations({runId=null,resultAttempt=0}={}){
    if(!state.session?.authenticated||!state.trip){clearTimeout(recommendationPollTimer);renderRecommendationResults();return;}
    const r=state.recommendations,requestedId=runId||r.active?.run_id||null;
    // A job event or repeated refresh must not supersede the same in-flight read.
    if(r.resultLoad?.epoch===state.epoch&&r.resultLoad.runId===requestedId)return;
    clearTimeout(recommendationPollTimer);
    const epoch=state.epoch,serial=++r.serial,path=tripPath(),read={epoch,serial,runId:requestedId};r.resultLoad=read;
    if(r.active?.run_id===requestedId&&['succeeded','partial'].includes(r.active.state)&&!r.active.result&&r.active.data_status!=='stale')r.resultRecoveryPending=true;
    renderRecommendationProgress();
    try{
      const history=await allPages(path+'/recommendations');
      if(epoch!==state.epoch||serial!==state.recommendations.serial)return;state.recommendations.runs=history;
      const id=runId||state.recommendations.active?.run_id||history[0]?.run_id||history[0]?.id;
      if(!id){state.recommendations.loaded=true;state.recommendations.connectionError=null;state.recommendations.resultRecoveryPending=false;renderRecommendationResults();return;}
      read.runId=id;
      const active=await api(path+'/recommendations/'+encodeURIComponent(id));if(epoch!==state.epoch||serial!==state.recommendations.serial)return;
      // An older GET can arrive after a terminal job event; do not regress that state.
      const known=state.recommendations.active;
      if(known?.run_id===id&&['succeeded','partial','failed','cancelled'].includes(known.state)&&!['succeeded','partial','failed','cancelled'].includes(active.state)){active.state=known.state;active.job=known.job;active.error_code=known.error_code||known.job?.error_code;}
      state.recommendations.active=active;state.recommendations.runs=history.map(run=>(run.run_id||run.id)===id?{...run,state:active.state}:run);state.recommendations.loaded=true;state.recommendations.connectionError=null;restoreRecommendationOptions(active);
      // The job can finish after the API read its result row. Re-read that run, never submit another job.
      const resultPending=['succeeded','partial'].includes(active.state)&&!active.result&&active.data_status!=='stale';
      state.recommendations.resultRecoveryPending=resultPending&&resultAttempt<3;
      if(active.result)state.recommendations.displayed=active;
      else {const priorId=state.recommendations.displayed?.run_id;state.recommendations.displayed=null;const previous=history.find(run=>(run.run_id||run.id)===priorId&&(run.run_id||run.id)!==id)||history.find(run=>['succeeded','partial'].includes(run.state)&&(run.run_id||run.id)!==id);if(previous){const retained=await api(path+'/recommendations/'+encodeURIComponent(previous.run_id||previous.id));if(epoch!==state.epoch||serial!==state.recommendations.serial)return;if(retained.result)state.recommendations.displayed=retained;}}
      renderRecommendationResults();
      if(['queued','running'].includes(active.state)){
        if(active.job_id)watchJob(active.job_id,epoch);
        if(state.tab==='explore')recommendationPollTimer=setTimeout(()=>loadRecommendations({runId:active.run_id}).catch(()=>{}),2200);
      }else if(state.recommendations.resultRecoveryPending&&state.tab==='explore'){
        recommendationPollTimer=setTimeout(()=>{if(epoch!==state.epoch||serial!==state.recommendations.serial||state.tab!=='explore')return;return loadRecommendations({runId:id,resultAttempt:resultAttempt+1}).catch(()=>{});},1200);
      }
    }catch(error){
      if(epoch!==state.epoch||serial!==state.recommendations.serial||error.status===401||error.name==='AbortError')return;
      state.recommendations.connectionError=error.code||'CONNECTION_INTERRUPTED';
      const recoverResult=state.recommendations.resultRecoveryPending&&resultAttempt<3;
      if(!recoverResult)state.recommendations.resultRecoveryPending=false;renderRecommendationProgress();
      // Reconnect to the same saved run with GET only; never repeat a submission on a timer.
      if(state.tab==='explore'&&['queued','running'].includes(state.recommendations.active?.state))recommendationPollTimer=setTimeout(()=>loadRecommendations({runId:runId||state.recommendations.active?.run_id}).catch(()=>{}),4000);
      else if(state.tab==='explore'&&recoverResult)recommendationPollTimer=setTimeout(()=>{if(epoch!==state.epoch||serial!==state.recommendations.serial||state.tab!=='explore')return;return loadRecommendations({runId:runId||state.recommendations.active?.run_id,resultAttempt:resultAttempt+1}).catch(()=>{});},4000);
      throw error;
    }finally{
      if(r.resultLoad===read){r.resultLoad=null;if(epoch===state.epoch&&r===state.recommendations)renderRecommendationProgress();}
    }
  }
  async function applyRecommendations(){
    if(state.recommendations.submitting||state.recommendations.resultRecoveryPending||['queued','running'].includes(state.recommendations.active?.state)){notice('이미 추천을 찾고 있어요. 진행 상황을 이어서 볼 수 있어요.');return;}
    if(!state.trip||!state.discovery.conditions){showError($('#recommendationError'),'여행과 저장한 방문 조건을 먼저 확인해 주세요.');return;}
    if(!state.session?.authenticated){showError($('#recommendationError'),'로그인 상태를 다시 확인해 주세요.');return;}
    if(['unsupported_city'].includes(state.discovery.conditions.context_state)){showError($('#recommendationError'),state.discovery.conditions.context_state==='unsupported_city'?'이 도시의 자동 추천은 준비 중입니다. 장소 보관함을 이용해 주세요.':'여행 정보가 바뀌었습니다. 방문 조건의 도시와 날짜를 확인해 주세요.');return;}
    if(state.discovery.conditions.context_state==='outdated'&&!state.discovery.dirty){showError($('#recommendationError'),'여행 정보가 바뀌었습니다. 체류일을 고르거나 여행 기본값으로 맞춰 주세요.');return;}
    for(const id of ['#recommendationMinRating','#recommendationMinCount','#recommendationLimit'])if(!$(id).reportValidity())return;
    window.DiscoveryFlow?.applied();
    const epoch=state.epoch,trip=state.trip,draftRevision=state.discovery.draftRevision||0,filterRevision=state.recommendations.filterRevision||0;
    try{
      const payload=discoveryIntentPayload();const intentName=state.session.user.id+':'+trip.id+':recommendation:'+JSON.stringify(payload);
      if(!state.intents.has(intentName))state.intents.set(intentName,uid());
      state.recommendations.submitting=true;state.recommendations.submissionStage='validating';state.recommendations.requestError=null;state.recommendations.connectionError=null;state.recommendations.needsInputRefresh=false;showError($('#recommendationError'));renderRecommendationResults();$('#recommendationRunStatus')?.scrollIntoView?.({block:'nearest',behavior:'auto'});
      const stableKey=await rememberDiscoveryKey(intentName,state.intents.get(intentName));if(epoch!==state.epoch)return;state.intents.set(intentName,stableKey);
      const result=await api(tripPath(trip)+'/discovery-intents',{method:'POST',headers:{'Idempotency-Key':state.intents.get(intentName)},body:payload});
      if(epoch!==state.epoch)return;state.intents.delete(intentName);state.recommendations.submissionStage='accepted';state.recommendations.active={...result,request:payload,conditions_version:result.conditions_version,trip_version:payload.expected_trip_version};renderRecommendationResults();if(filterRevision===(state.recommendations.filterRevision||0))state.recommendations.optionsDirty=false;if(draftRevision===(state.discovery.draftRevision||0)){state.recommendations.conditionsDraft=false;state.discovery.dirty=false;state.discovery.draft=null;}await loadDiscovery({quiet:true});if(epoch!==state.epoch)return;
      state.recommendations.active={...result,request:payload,conditions_version:result.conditions_version,trip_version:payload.expected_trip_version};
      renderRecommendationResults();await loadRecommendations({runId:result.run_id});if(result.job_id)loadJobs().catch(fail);
    }catch(error){if(epoch===state.epoch){if(state.recommendations.submissionStage==='accepted')state.recommendations.connectionError=error.code||'CONNECTION_INTERRUPTED';else state.recommendations.requestError={code:error.code||'REQUEST_UNCONFIRMED',status:error.status};fail(error,$('#recommendationError'));if(error.status===409){state.recommendations.needsInputRefresh=true;discoveryMessage('여행이나 방문 조건이 변경되었습니다. 입력은 유지했습니다. 최신 저장 조건을 불러온 후 다시 적용해 주세요.');}}}
    finally{if(epoch===state.epoch){state.recommendations.submitting=false;state.recommendations.submissionStage=null;renderRecommendationResults();}}
  }
  function recommendationCounts(result){
    if(!result)return {qualified:0,confirmation:0,reference:0,displayable:0,excluded:0};
    const ids={items:new Set(),needs_confirmation:new Set(),insufficient_data:new Set(),excluded:new Set()};
    const auxiliary=new Set();for(const [sectionName,section] of Object.entries(result.sections||{})){if(sectionName==='reference'){for(const key of ['items','needs_confirmation','insufficient_data'])for(const item of section[key]||[])if(item.place_id)auxiliary.add(item.place_id);continue;}for(const key of Object.keys(ids))for(const item of section[key]||[])if(item.place_id)ids[key].add(item.place_id);}
    const displayable=new Set([...ids.items,...ids.needs_confirmation,...ids.insufficient_data]);
    const confirmation=[...ids.needs_confirmation].filter(id=>!ids.items.has(id));
    const reference=[...new Set([...ids.insufficient_data,...auxiliary])].filter(id=>!ids.items.has(id)&&!ids.needs_confirmation.has(id));
    return {qualified:ids.items.size,confirmation:confirmation.length,reference:reference.length,displayable:new Set([...displayable,...auxiliary]).size,excluded:[...ids.excluded].filter(id=>!displayable.has(id)).length};
  }
  function recommendationProgressModel(r){
    const active=r.active,job=active?.job||{},counts=recommendationCounts(active?.result),pending=r.submitting&&r.submissionStage!=='accepted';
    const code=r.requestError?.code||active?.error_code||job.error_code;
    const model={state:'idle',busy:false,step:null,title:'',description:'',count:null,action:null};
    if(pending)return {...model,state:'validating',busy:true,step:0,title:'여행 조건을 확인하고 있어요',description:'도시·방문일·인원과 선택한 조건을 적용해요.'};
    if(r.connectionError)return {...model,state:'reconnecting',busy:false,title:'진행 상태를 다시 연결하고 있어요',description:'저장된 요청을 이어서 확인해요. 추천 요청은 다시 보내지 않아요.',action:'refresh'};
    if(r.requestError)return {...model,state:budgetCodes.has(code)?'budget':'failed',title:budgetCodes.has(code)?'새로운 조회를 잠시 멈췄어요':r.requestError.status===409?'여행 조건을 다시 확인해 주세요':'요청 상태를 확인해 주세요',description:budgetCodes.has(code)?'조회 예산을 모두 사용했어요. 저장된 장소와 일정은 계속 볼 수 있어요.':r.requestError.status===409?'입력한 내용은 그대로 있어요. 최신 여행 정보를 확인한 뒤 다시 찾아보세요.':'서버에서 요청을 받았는지 확인이 필요해요. 먼저 저장된 요청을 확인해 주세요.',action:budgetCodes.has(code)?'saved':r.requestError.status===409?'conditions':'refresh'};
    if(!active)return model;
    if(active.data_status==='stale')return {...model,state:'partial',title:'장소 근거를 다시 확인해야 해요',description:'출처가 바뀌었거나 확인 기한이 지났어요. 현재 자료로 다시 추천을 찾아보세요.',action:'retry'};
    if(['queued','running'].includes(active.state)){
      const stage=job.stage||active.state,step=['source_revalidation','result_ready','recommendation_complete','activating'].includes(stage)?2:1;
      const titles={queued:'차례가 되면 바로 시작해요',recovering:'저장된 단계부터 다시 이어가요',retry_wait:'자료 연결을 다시 준비해요',running:'여행에 맞는 장소를 찾고 있어요',public_discovery:'공개 지도에서 이 도시의 식당을 찾고 있어요',candidate_snapshot:'저장된 장소와 지점을 확인하고 있어요',route_snapshot:'숙소와 장소 사이 이동을 확인하고 있어요',constraints_and_scoring:'방문 조건과 추천 근거를 비교하고 있어요',source_revalidation:'출처와 확인 기한을 한 번 더 살펴봐요',result_ready:'찾은 장소를 정리하고 있어요',recommendation_complete:'추천 결과를 저장하고 있어요',activating:'추천 결과를 저장하고 있어요'};
      let count=null;if(Number.isInteger(job.done_count)&&Number.isInteger(job.total_count)&&job.total_count>0){const noun=stage==='route_snapshot'?'이동 구간':stage==='constraints_and_scoring'?'장소':'처리';count=`${noun} ${job.done_count} / ${job.total_count}건`;}
      return {...model,state:active.state,busy:true,step,title:job.cancel_requested_at?'추천 중단을 요청했어요':titles[stage]||titles.running,description:job.cancel_requested_at?'실행 중인 처리가 끝날 수 있어요. 이미 사용한 조회 비용은 취소되지 않아요.':r.displayed?.result?'새 결과가 준비될 때까지 이전 결과를 볼 수 있어요.':'화면을 새로고침해도 같은 요청의 진행 상황을 이어서 볼 수 있어요.',count};
    }
    if(active.state==='cancelled')return {...model,state:'cancelled',title:'추천 찾기를 멈췄어요',description:'기존에 저장한 장소와 일정은 그대로예요. 준비되면 다시 찾아보세요.',action:'retry'};
    if(active.state==='failed')return {...model,state:budgetCodes.has(code)?'budget':'failed',title:budgetCodes.has(code)?'조회 예산을 모두 사용했어요':'추천을 끝까지 확인하지 못했어요',description:budgetCodes.has(code)?'추가 조회를 멈췄어요. 저장된 장소와 일정은 계속 볼 수 있어요.':code==='SOURCE_DATA_CHANGED'?'확인 중 장소 근거가 바뀌었어요. 최신 자료로 다시 찾아보세요.':'입력한 조건과 이전 결과는 남아 있어요. 잠시 후 다시 시도해 주세요.',action:budgetCodes.has(code)?'saved':'retry'};
    if(!active.result&&r.resultRecoveryPending)return {...model,state:'finalizing',busy:true,step:2,title:'찾은 장소를 불러오고 있어요',description:'완료된 요청의 저장 결과를 자동으로 확인해요. 새 추천 요청은 보내지 않아요.'};
    if(!active.result)return {...model,state:'partial',title:'저장된 결과를 다시 확인해 주세요',description:'자동 확인이 잠시 지연됐어요. 저장된 요청을 확인하면 이어서 볼 수 있어요.',action:'refresh'};
    if(!counts.displayable){const empty=active.result.summary?.empty_state;return {...model,state:'empty',step:2,title:empty?.title||'지금 조건에 맞춰 보여드릴 장소가 없어요',description:empty?.description||'이유를 아래에서 확인하고 방문 조건을 바꿔보세요. 장소 링크를 직접 저장할 수도 있어요.',retryAt:empty?.retry_at||active.result.public_discovery?.retry_at,action:empty?.code==='CATALOG_EMPTY'||active.result.public_discovery?.state==='unavailable'?'save':'conditions'};}
    return {...model,state:active.state==='partial'||counts.qualified===0?'partial':'complete',step:2,title:counts.qualified===0?`살펴볼 장소 ${counts.displayable}곳을 찾았어요`:`추천 장소 ${counts.qualified}곳을 찾았어요`,description:counts.qualified===0?'영업·인원·이동 등 확인할 정보를 함께 표시했어요. 아직 방문 조건을 모두 통과한 추천은 아니에요.':active.state==='partial'?'확인된 장소부터 볼 수 있어요. 일부 자료는 추가 확인이 필요해요.':'장소별 추천 이유와 방문 전 확인할 정보를 살펴보세요.'};
  }
  function renderRecommendationProgress(){
    const r=state.recommendations||{},model=recommendationProgressModel(r),host=$('#recommendationRunStatus'),submit=$('#applyRecommendations');if(!host||!submit)return;
    const busy=r.submitting||r.resultRecoveryPending||['queued','running'].includes(r.active?.state);submit.disabled=!state.trip||!state.discovery.conditions||state.discovery.conditions?.context_state==='unsupported_city'||busy;submit.setAttribute('aria-busy',String(busy));submit.textContent=busy?'찾는 중…':r.displayed?.result?'다시 찾기':'장소 찾아보기';
    const progressKey=JSON.stringify([model,r.active?.run_id,r.active?.job?.cancel_requested_at,Boolean(r.cancelPending),Boolean(r.needsInputRefresh),r.active?.error_code,r.active?.job?.error_code,r.active?.reason_codes]);
    if(host.dataset.progressKey===progressKey)return;host.dataset.progressKey=progressKey;
    host.replaceChildren();host.hidden=model.state==='idle';host.className='recommendation-run-status recommendation-progress';host.dataset.state=model.state;host.setAttribute('aria-live','polite');host.setAttribute('aria-atomic','true');if(model.state==='idle')return;
    if(['complete','partial'].includes(model.state)&&r.displayed?.result&&r.displayed.data_status!=='stale'&&!r.connectionError&&!r.requestError&&!recommendationStale(r.displayed)){host.hidden=true;return;}
    const heading=make('div','recommendation-progress-heading');if(model.busy){const dots=make('span','recommendation-loader');dots.setAttribute('aria-hidden','true');for(let i=0;i<3;i++)dots.append(make('span'));heading.append(dots);}
    const text=make('div');text.append(make('h3','recommendation-progress-title',model.title),make('p','hint',model.description));heading.append(text);host.append(heading);
    if(model.busy&&model.step!=null){const steps=make('ol','recommendation-steps');steps.setAttribute('aria-label','추천 진행 단계');['여행 조건','장소·이동 확인','결과 정리'].forEach((label,index)=>{const step=make('li');step.dataset.state=index<model.step?'complete':index===model.step?'current':'pending';if(index===model.step)step.setAttribute('aria-current','step');const mark=make('span','recommendation-step-mark',index<model.step?'✓':String(index+1));mark.setAttribute('aria-hidden','true');step.append(mark,make('span','',label));steps.append(step);});host.append(steps);}
    if(model.retryAt){const retry=new Date(model.retryAt);if(Number.isFinite(retry.getTime()))host.append(make('p','hint','다시 찾을 수 있는 시각 · '+retry.toLocaleString('ko-KR',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'})+' (이 기기의 시간대)'));}
    if(model.count)host.append(make('p','recommendation-progress-count',model.count));
    const actions=make('div','actions'),active=r.active,job=active?.job||{},jobId=active?.job_id||job.job_id;
    if(model.state!=='validating'&&['queued','running'].includes(active?.state)&&jobId&&!job.cancel_requested_at&&!r.cancelPending){actions.append(button('그만 찾기',async()=>{r.cancelPending=true;renderRecommendationProgress();try{const cancelled=await api('/jobs/'+encodeURIComponent(jobId)+'/cancel',{method:'POST'});if(state.recommendations!==r)return;if(r.active?.run_id===active.run_id)r.active.job=cancelled;await loadRecommendations({runId:active.run_id});}catch(error){if(state.recommendations===r)fail(error,$('#recommendationError'));}finally{if(state.recommendations===r){r.cancelPending=false;renderRecommendationProgress();}}},'text-button'));}
    if(model.action==='refresh')actions.append(button('저장된 요청 확인',()=>{r.requestError=null;loadRecommendations().catch(error=>fail(error,$('#recommendationError')));},'secondary'));
    if(model.action==='retry')actions.append(button('다시 추천 찾기',applyRecommendations,'secondary'));
    if(model.action==='conditions')actions.append(button('방문 조건 확인',discoveryConditionsForm,'secondary'));
    if(model.action==='save')actions.append(button('장소 링크·이름 저장',()=>bookmarkForm(),'secondary'));
    if(model.action==='saved')actions.append(button('저장한 장소 보기',()=>setExploreView('saved'),'secondary'));
    if(r.needsInputRefresh)actions.append(button('최신 여행·조건 확인',async()=>{try{await Promise.all([loadBookings(),loadDiscovery({quiet:true})]);if(state.recommendations!==r)return;r.needsInputRefresh=false;renderRecommendationResults();}catch(e){fail(e,$('#recommendationError'));}},'secondary'));
    if(actions.children.length)host.append(actions);
    if(model.state!=='validating'){if(active?.error_code||job.error_code)appendRecommendationReasons(host,[active?.error_code||job.error_code]);appendRecommendationReasons(host,active?.reason_codes||[]);}
  }
  function renderRecommendationSummary(host,result){
    const counts=recommendationCounts(result);if(!counts.displayable)return;
    if(window.DiscoveryExperience){const sections=result.sections||{},core=['local_discovery','landmark'].map(key=>{const section=sections[key]||{};return recommendationTypeNames[key]+' '+new Set([...(section.items||[]),...(section.needs_confirmation||[])].map(p=>p.place_id)).size+'곳';});const note=make('p','recommendation-result-counts',core.join(' · ')+' · 주변 참고는 별도 목록');host.append(note);return;}
    const summary=make('section','recommendation-result-summary');summary.append(make('strong','',`살펴볼 장소 ${counts.displayable}곳`),make('p','recommendation-result-counts',`조건을 확인한 추천 ${counts.qualified}곳 · 방문 전 확인할 장소 ${counts.confirmation+counts.reference}곳`));
    host.append(summary);
  }
  function renderPublicDiscovery(host,data){
    if(!data||data.provider!=='openstreetmap'||!['ready','empty'].includes(data.state))return;
    const box=make('div','public-discovery-note');
    const reasons={PUBLIC_REVIEW_FILTER_UNSUPPORTED:'공개 지도에는 리뷰 언어를 판단할 자료가 없어요. 주변 장소 참고하기에서 별도로 살펴볼 수 있어요.',PUBLIC_DISCOVERY_DAILY_LIMIT:'오늘의 공개 지도 조회 한도에 도달했어요. 저장된 장소는 계속 볼 수 있어요.',PUBLIC_DISCOVERY_COOLDOWN:'공개 지도 연결을 잠시 쉬고 있어요. 잠시 후 다시 찾아보세요.',OPERATIONS_PAUSED:'새 자료 조회가 잠시 중단됐어요. 저장된 장소는 계속 볼 수 있어요.'};
    box.append(make('p','hint',data.state==='ready'?`도심 주변 3km의 공개 지도 자료${data.cache_hit?' · 저장된 자료 재사용':''} · 확인 ${reviewDate(data.fetched_at)}. 검수된 추천 순위나 숙소 주변 전체 검색이 아니에요.`:data.state==='empty'?'도심 주변 3km에서 표시할 식당·카페를 찾지 못했어요. 지도 링크로 장소를 직접 저장할 수 있어요.':reasons[data.reason]||'공개 지도 자료를 가져오지 못했어요. 저장된 장소를 살펴보거나 잠시 후 다시 찾아보세요.'));
    const credit=make('p','hint');safeDiscoveryLink(credit,'https://www.openstreetmap.org/copyright','© OpenStreetMap 기여자 · ODbL');if(data.query_service==='Photon')safeDiscoveryLink(credit,'https://photon.komoot.io/',' · 검색 Photon');credit.append(make('span','',' · 도시 기준점 '));safeDiscoveryLink(credit,'https://www.geonames.org/','GeoNames · CC BY 4.0');box.append(credit);host.append(box);
  }
  function appendRecommendationReasons(host,codes){if(!codes?.length)return;const list=make('ul','recommendation-reasons');[...new Set(codes)].slice(0,6).forEach(code=>list.append(make('li','',recommendationReasonNames[code]||reviewReasonText[code]||'이 조건에 사용할 근거가 아직 충분하지 않습니다.')));host.append(list);}
  function recommendationConditionsLabel(conditions){if(!conditions)return '저장된 방문 조건';return `${discoveryCityNames[conditions.city]||conditions.city||'도시 미확인'} · ${conditions.visit?.date||'날짜 미확인'}${conditions.visit?.local_time?' '+conditions.visit.local_time:''} · 성인 ${conditions.party?.adults??'미확인'}명 · 아동 ${conditions.party?.children_status==='unknown'?'미확인':(conditions.party?.children||[]).length+'명'}`;}
  function renderRecommendationResults(){const host=$('#recommendationResults');if(!host)return;const focused=host.contains(document.activeElement)?document.activeElement:null,focusPlace=focused?.closest?.('.recommendation-card')?.dataset.placeId,focusLabel=focused?.getAttribute('aria-label')||focused?.textContent;const r=state.recommendations||{},active=r.active,displayed=r.displayed;const submitting=r.submitting||r.resultRecoveryPending||['queued','running'].includes(active?.state);$('#applyRecommendations').disabled=!state.trip||!state.discovery.conditions||['unsupported_city'].includes(state.discovery.conditions?.context_state)||submitting;$('#reviewStrictFilter').disabled=!state.trip;$('#ratingFilterEnabled').disabled=!state.trip;
    $('#recommendationFilterSummary').textContent=recommendationFilterSummary();
    const dirty=r.optionsDirty||r.conditionsDraft||displayed?.origin_status==='stale';$('#recommendationDraft').textContent=displayed?.origin_status==='stale'?'숙소가 바뀌어 이전 출발점 기준의 결과입니다. 현재 조건으로 장소 찾아보기를 눌러 주세요.':dirty?'입력 중인 조건은 아직 적용되지 않았습니다. 저장된 이전 결과를 유지하고 있습니다.':'';$('#recommendationDraft').hidden=!dirty;
    renderRecommendationProgress();window.DiscoveryExperience?.renderMode();window.DiscoveryFlow?.settled();
    const history=$('#recommendationHistory');history.replaceChildren();$('#recommendationHistoryWrap').hidden=true;for(const run of r.runs||[])history.append(new Option(`${reviewDate(run.created_at)} · ${statusText[run.state]||run.state}`,run.run_id||run.id));history.value=active?.run_id||'';
    host.replaceChildren();if(!submitting&&!displayed?.result&&state.trip&&state.discovery.conditions?.catalog_availability?.real_reviewed_candidates===0&&!state.discovery.conditions?.catalog_availability?.synthetic_test_candidates&&!state.discovery.conditions?.catalog_availability?.public_discovery_enabled){const empty=make('div','panel empty');empty.append(make('h3','','이 도시의 추천 자료를 준비하고 있어요'),make('p','hint','도시와 날짜는 저장됐어요. 검수된 장소가 아직 없어 추천은 0곳입니다. 지도 링크나 장소 이름을 먼저 저장할 수 있어요.'),button('장소 링크·이름 저장',()=>bookmarkForm(),'secondary'));host.append(empty);if(!displayed?.result||displayed.conditions_snapshot?.city===state.discovery.conditions?.conditions?.city){renderComparisonTray();return;}}if(!displayed?.result)renderComparisonTray();if(!state.trip){host.append(make('p','muted','여행을 만들고 방문 조건을 정하면 장소 근거를 확인할 수 있어요.'));return;}if(!displayed?.result){host.append(make('div','panel empty',submitting?'후보를 확인하고 있습니다. 이 화면을 새로고침해도 같은 요청을 이어봅니다.':state.discovery.conditions?.context_state==='unsupported_city'?'가고 싶은 장소의 지도 링크나 이름을 저장해 여행을 준비해 보세요.':state.discovery.conditions?.context_state==='outdated'?'방문 조건에서 변경된 여행 도시와 날짜를 확인해 주세요.':state.discovery.conditions?.catalog_availability?.public_discovery_enabled&&!state.discovery.conditions?.catalog_availability?.real_reviewed_candidates?'도시와 날짜는 준비됐어요. ‘장소 찾아보기’를 누르면 도심 주변 3km의 식당·카페를 공개 지도에서 찾아요. 영업·가격·평점은 별도 확인이 필요해요.':'여행의 도시·날짜·인원을 가져왔어요. 필요하면 방문 조건을 바꾸고 ‘장소 찾아보기’를 눌러 주세요.'));return;}
    const result=displayed.result,old=displayed.run_id!==active?.run_id||recommendationStale(displayed)||dirty;
    const snapshot=make('div',old?'form-note recommendation-stale':'recommendation-result-context');snapshot.append(make('strong','',old?'이전 조건의 저장된 결과':'이 결과의 방문 기준'),make('p','',recommendationConditionsLabel(displayed.conditions_snapshot)));if(old)snapshot.append(make('p','hint','현재 입력·최신 요청과 다를 수 있습니다. 새 조건의 적합 결과로 해석하지 마세요.'));host.append(snapshot);
    const allUnsupported=result.unsupported_constraints||displayed.unsupported_constraints||[];const unsupported=allUnsupported.filter(value=>{const code=typeof value==='string'?value:value.reason||value.reason_code||value.code;return !['FACT_UNCONFIRMED','PREFERENCE_UNKNOWN','PRICE_UNKNOWN','ORIGIN_OR_COORDINATES_UNKNOWN','OPENING_HOURS_UNKNOWN','HOURS_UNKNOWN','VISIT_TIME_UNKNOWN','LIVE_AVAILABILITY_NOT_CONFIRMED','MIN_PARTY_UNKNOWN','MAX_PARTY_UNKNOWN','PARTY_LIMIT_UNKNOWN'].includes(code);});if(unsupported.length){const warning=make('section','panel recommendation-warning');warning.append(make('h3','','확인이 필요한 조건 · '+unsupported.length+'개'));appendRecommendationReasons(warning,unsupported.map(value=>typeof value==='string'?value:value.reason||value.reason_code||value.code));warning.append(make('p','hint','미지원 조건을 자동으로 완화하지 않았습니다. 조건을 직접 바꾸거나 자료를 확인한 뒤 새 요청을 만들어 주세요.'));host.append(warning);}
    if(!window.DiscoveryExperience)renderRecommendationSummary(host,result);
    if(!window.DiscoveryExperience||window.DiscoveryExperience.filters().discoveryMode==='reference')renderPublicDiscovery(host,result.public_discovery||result.external_discovery);
    const sections=result.sections||{};if(window.DiscoveryExperience)window.DiscoveryExperience.renderSections(host,displayed);else for(const type of ['local_discovery','landmark']){const section=sections[type];if(!section)continue;if(!recommendationCounts(result).displayable&&result.summary?.empty_state)continue;const block=make('section','recommendation-section');const title=make('div','section-heading');title.append(make('h2','',recommendationTypeNames[type]),make('span','hint',(section.items||[]).length?`${section.items.length}곳 추천${(section.needs_confirmation||[]).length+(section.insufficient_data||[]).length?' · '+((section.needs_confirmation||[]).length+(section.insufficient_data||[]).length)+'곳 확인 필요':''}`:`${(section.needs_confirmation||[]).length+(section.insufficient_data||[]).length}곳 살펴보기`));block.append(title);if(!(section.items||[]).length&&!(section.needs_confirmation||[]).length&&!(section.insufficient_data||[]).length){const empty=make('div','panel empty');empty.append(make('h3','','확인된 적합 후보가 없습니다'),make('p','','자료가 없는 후보로 결과 수를 채우지 않았습니다. 방문 조건을 직접 조정하거나 저장한 장소의 자료를 확인해 주세요.'));appendRecommendationReasons(empty,section.reason_codes||[]);empty.append(button('방문 조건 확인',discoveryConditionsForm,'secondary'));block.append(empty);}else if((section.items||[]).length){const cards=make('div','recommendation-grid');section.items.forEach((item,index)=>cards.append(recommendationCard(item,displayed,index+1)));block.append(cards);const wanted=displayed.request?.limit;if(wanted&&(section.items||[]).length<wanted)block.append(make('p','form-note',`요청한 최대 ${wanted}곳 중 ${(section.items||[]).length}곳만 반환했습니다. 나머지를 미확인 후보로 채우지 않습니다.`));}
      for(const [key,titleText,description] of [['needs_confirmation','방문 전 확인할 장소','영업·인원 등 아직 확인하지 못한 조건이 있어요. 상세 정보와 공식 출처를 살펴보세요.'],['insufficient_data','먼저 살펴볼 장소','지점과 출처를 먼저 살펴보세요. 이동·가격 등 추천 순위를 정할 정보는 더 확인해야 해요.'],['excluded','이번 조건에서 제외한 장소','확인된 불일치와 직접 제외한 장소를 확인할 수 있습니다.']]){const items=section[key]||[];if(!items.length)continue;const details=make('section','recommendation-reference');details.open=key!=='excluded'&&!(section.items||[]).length;details.append(make('h3','',`${titleText} · ${items.length}곳`),make('p','hint',description));const cards=make('div','recommendation-grid');items.forEach(item=>{const card=recommendationCard(item,displayed,null,key!=='excluded');card.classList.add('recommendation-card-reference');card.dataset.referenceKind=key;cards.append(card);});details.append(cards);if(key==='excluded')block.append(button(`제외한 장소 ${items.length}곳과 이유`,()=>openDialog('이번 조건에서 제외한 장소',body=>body.append(details)),'text-button'));else block.append(details);}host.append(block);}
    if(displayed?.result){const tools=make('div','recommendation-footer actions');tools.append(button('이전 추천 기록',()=>openDialog('이전 추천 기록',body=>{for(const run of r.runs||[])body.append(button(`${reviewDate(run.created_at)} · ${statusText[run.state]||run.state}`,()=>{closeDialog(true);loadRecommendations({runId:run.run_id||run.id}).catch(fail);},'secondary'));}),'text-button'));if(state.session?.user?.role==='admin')tools.append(button('추천 버전 비교',()=>window.ProductTools?.evaluateRun(displayed),'text-button'));host.append(tools);}
    renderComparisonTray();renderItinerarySelection();
    if(focusPlace){const card=[...host.querySelectorAll('.recommendation-card')].find(el=>el.dataset.placeId===focusPlace);[...(card?.querySelectorAll('button,a')||[])].find(el=>(el.getAttribute('aria-label')||el.textContent)===focusLabel)?.focus({preventScroll:true});}
  }
  function recommendationPrice(value){if(!value)return '가격 미확인';const currency=value.currency||'통화 미확인',min=value.amount_min,max=value.amount_max;const amount=min==null&&max==null?'금액 미확인':min===max?String(min):`${min??'하한 미확인'}–${max??'상한 미확인'}`;return `${currency} ${amount} · ${value.basis==='group'||value.per_person_or_group==='group'?'일행 전체':value.basis==='per_person'||value.per_person_or_group==='per_person'?'1인당':'인원 기준 미확인'} · ${{meal:'한 끼',day:'하루',visit:'방문 1회'}[value.period]||'이용 범위 미확인'}`;}
  function recommendationMovement(movement){
    if(!movement)return '거리·도보시간 미확인';
    const label=movement.origin?.label?movement.origin.label+'에서 ':'출발점에서 ';
    const raw=movement.straight_line_m??(movement.method==='haversine_straight_line'||movement.kind==='estimate'?movement.distance_m:null);
    const distance=raw==null?'직선거리 미확인':'직선거리 '+(raw>=1000?(raw/1000).toFixed(1)+'km':Math.round(raw)+'m');
    const route=movement.route;
    if(route?.status==='ok'&&route.duration_seconds!=null){const mode={walking:'도보',car:'자동차',transit:'대중교통'}[route.mode]||'이동';return `${label}${mode} 약 ${Math.ceil(route.duration_seconds/60)}분 · ${distance} · 확인 ${reviewDate(route.checked_at)}${route.usage_permission?.attribution?' · '+route.usage_permission.attribution:''}`;}
    return `${label}${distance} · 도보시간 미확인`;
  }
  function recommendationUnknowns(host,codes){
    const unique=[...new Set(codes||[])];if(!unique.length)return;
    host.append(make('strong','recommendation-unknown-heading','방문 전 확인 필요'));
    const visible=unique.slice(0,2),availability='LIVE_AVAILABILITY_NOT_CONFIRMED';
    // Always keep live seat uncertainty visible, even when other details are collapsed.
    if(unique.includes(availability)&&!visible.includes(availability))visible.push(availability);
    appendRecommendationReasons(host,visible);
    const remainder=unique.filter(code=>!visible.includes(code));if(!remainder.length)return;
    host.append(button(`확인할 정보 ${remainder.length}개 더 보기`,()=>openDialog('방문 전 확인할 정보',body=>{const list=make('ul','recommendation-reasons');for(const code of unique)list.append(make('li','',recommendationReasonNames[code]||reviewReasonText[code]||'이 조건에 사용할 근거가 아직 충분하지 않습니다.'));body.append(list);}),'text-button'));
  }
  function placePhotoUrl(value,kind){
    if(typeof value!=='string'||value.length>2000)return null;
    try{
      const url=new URL(value);
      if(url.protocol!=='https:'||url.username||url.password||url.port||url.search||url.hash)return null;
      if(kind==='image'&&(['upload.wikimedia.org','thumb.wikimedia.org'].includes(url.hostname))&&/^\/wikipedia\/commons\/(?:thumb\/)?[a-f0-9]\/[a-f0-9]{2}\/.+\.(?:jpe?g|png|webp)$/i.test(url.pathname))return url.href;
      if(kind==='source'&&url.hostname==='commons.wikimedia.org'&&url.pathname.startsWith('/wiki/File:'))return url.href;
      if(kind==='license'&&url.hostname==='creativecommons.org'&&/^\/(?:licenses\/(?:by|by-sa)\/(?:1\.0|2\.0|2\.5|3\.0|4\.0)|publicdomain\/(?:zero|mark)\/1\.0)\/$/.test(url.pathname))return url.href;
    }catch{}
    return null;
  }
  function placePhotoItems(place,now=Date.now()){
    if(place?.synthetic||place?.photo_status?.state==='unavailable'||!Array.isArray(place?.photos))return [];
    const items=[],seen=new Set();
    for(const photo of place.photos){
      if(!photo||typeof photo!=='object')continue;
      const url=placePhotoUrl(photo.url,'image'),source=placePhotoUrl(photo.source_url,'source'),license=placePhotoUrl(photo.license_url,'license');
      const checked=Date.parse(photo.checked_at||''),expires=Date.parse(photo.expires_at||'');
      if(!url||!source||!license||seen.has(url)||!Number.isFinite(checked)||!Number.isFinite(expires)||expires<=now||expires<=checked)continue;
      if(!['author','license','alt'].every(field=>typeof photo[field]==='string'&&photo[field].trim()))continue;
      if(!['food','interior','exterior','other'].includes(photo.kind))continue;
      seen.add(url);items.push({...photo,url,source_url:source,license_url:license});
      if(items.length===3)break;
    }
    return items;
  }
  function placePhotoDate(photo){
    const taken=typeof photo?.taken_at==='string'?photo.taken_at:'';
    if(/^\d{4}(?:-\d{2}(?:-\d{2})?)?$/.test(taken))return taken.replaceAll('-','. ')+' 촬영';
    return '촬영일 미확인';
  }
  function placePhotoGallery(place,mode='card'){
    const photos=placePhotoItems(place),name=place?.native_name||place?.name||place?.display_name||'장소';
    const gallery=make('figure','place-photo-gallery'+(mode==='detail'?' place-photo-gallery-detail':''));
    gallery.dataset.photoCount=String(photos.length);gallery.setAttribute('aria-label',name+' 장소 사진');
    if(!photos.length){
      gallery.classList.add('place-photo-gallery-empty');gallery.append(make('p','place-photo-unavailable','등록된 사진이 없어요.'));
      const map='https://www.google.com/maps/search/?api=1&query='+encodeURIComponent([name,place.address].filter(Boolean).join(' '));safeDiscoveryLink(gallery,map,'지도에서 사진 보기','text-button');
      return gallery;
    }
    const frame=make('div','place-photo-frame'),navigation=make('div','place-photo-navigation'),counter=make('span','place-photo-counter'),type=make('span','place-photo-type'),credits=make('figcaption','place-photo-credits'),unavailable=make('div','place-photo-unavailable');
    const creditDetails=make('details','place-photo-credit-details'),creditSummary=make('summary','','사진 정보'),creditBody=make('div','place-photo-credit-body'),caption=make('span','place-photo-caption');
    creditDetails.append(creditSummary,creditBody);credits.append(caption,creditDetails);
    unavailable.hidden=true;unavailable.append(make('p','','사진을 불러오지 못했어요. 출처에서 확인해 주세요.'));
    safeDiscoveryLink(unavailable,photos[0].source_url,'사진 출처 열기');
    counter.setAttribute('role','status');counter.setAttribute('aria-live','polite');counter.setAttribute('aria-atomic','true');
    const failed=new Set(),slides=[],images=[];let current=0,pointer=null;
    const previous=button('‹',()=>advance(-1),'place-photo-previous'),next=button('›',()=>advance(1),'place-photo-next');
    previous.setAttribute('aria-label',name+' 이전 사진');next.setAttribute('aria-label',name+' 다음 사진');
    navigation.append(previous,next);
    for(const [index,photo] of photos.entries()){
      const slide=make('div','place-photo-slide'),image=make('img','place-photo-image');
      slide.hidden=index!==0;image.loading='lazy';image.decoding='async';image.referrerPolicy='no-referrer';image.draggable=false;
      image.width=Number.isSafeInteger(photo.width)&&photo.width>0?photo.width:960;image.height=Number.isSafeInteger(photo.height)&&photo.height>0?photo.height:720;
      image.alt=photo.alt+' · '+placePhotoDate(photo);image.addEventListener('error',()=>{
        failed.add(index);const remaining=photos.map((_,i)=>i).filter(i=>!failed.has(i));
        if(failed.has(current)&&remaining.length)current=remaining[0];render();
      },{once:true});
      slide.append(image);slides.push(slide);images.push(image);frame.append(slide);
    }
    function render(){
      const available=photos.map((_,index)=>index).filter(index=>!failed.has(index));
      slides.forEach((slide,index)=>{slide.hidden=!available.length||index!==current;});
      navigation.hidden=available.length<2;counter.hidden=!available.length;type.hidden=!available.length;unavailable.hidden=Boolean(available.length);creditBody.replaceChildren();
      creditDetails.hidden=!available.length;
      if(!available.length){caption.textContent='사진은 원출처에서 확인해 주세요.';return;}
      const photo=photos[current],image=images[current];if(!image.getAttribute('src'))image.src=photo.url;
      counter.textContent=`${available.indexOf(current)+1} / ${available.length}`;counter.setAttribute('aria-label',`${name} 사진 ${available.indexOf(current)+1} / ${available.length}`);
      type.textContent={food:'음식',interior:'실내',exterior:'외관',other:'사진'}[photo.kind];caption.textContent=placePhotoDate(photo);
      creditBody.append(make('span','place-photo-credit-line',`${photo.author} · ${placePhotoDate(photo)}`));
      safeDiscoveryLink(creditBody,photo.source_url,'사진 출처');safeDiscoveryLink(creditBody,photo.license_url,photo.license);
      creditBody.append(make('p','place-photo-date','현재 모습과 다를 수 있으며, 화면에 맞게 일부 잘라 표시해요.'));
    }
    function advance(direction){
      const available=photos.map((_,index)=>index).filter(index=>!failed.has(index));if(available.length<2)return;
      current=available[(available.indexOf(current)+direction+available.length)%available.length];render();
    }
    navigation.addEventListener('keydown',event=>{
      if(event.key==='ArrowLeft'||event.key==='ArrowRight'){event.preventDefault();advance(event.key==='ArrowLeft'?-1:1);}
    });
    frame.addEventListener('pointerdown',event=>{
      if(pointer||event.isPrimary===false||event.button!==0||event.target?.closest?.('button,a,summary'))return;
      pointer={id:event.pointerId,x:event.clientX,y:event.clientY};frame.setPointerCapture?.(event.pointerId);
    });
    frame.addEventListener('pointerup',event=>{
      if(!pointer||pointer.id!==event.pointerId)return;
      const dx=event.clientX-pointer.x,dy=event.clientY-pointer.y;pointer=null;
      if(Math.abs(dx)>=45&&Math.abs(dx)>Math.abs(dy)*1.3)advance(dx<0?1:-1);
    });
    frame.addEventListener('pointercancel',()=>{pointer=null;});
    frame.addEventListener('lostpointercapture',()=>{pointer=null;});
    frame.append(unavailable,navigation,counter,type);gallery.append(frame,credits);render();return gallery;
  }
  function recommendationCard(item,run,rank,comparable=true){
    const card=make('article','panel recommendation-card');card.dataset.placeId=item.place_id;card.append(placePhotoGallery(item));
    const heading=make('div','recommendation-card-heading');if(rank)heading.append(make('span','recommendation-rank',String(rank)));
    const names=make('div');names.append(make('h3','',item.name||'장소 이름 미확인'));
    if(item.native_name&&item.native_name!==item.name)names.append(make('p','hint',item.native_name));
    const tagFacts=(item.facts||[]).filter(fact=>fact.field==='tags');
    const tags=tagFacts.some(fact=>fact.status==='conflict')?[]:[...new Set(tagFacts.filter(fact=>fact.status==='verified'&&fact.usable===true&&Array.isArray(fact.value)).flatMap(fact=>fact.value).filter(value=>typeof value==='string'&&value.trim()&&value.trim().length<=40).map(value=>value.trim()))].slice(0,2);
    if(tags.length)names.append(make('p','hint recommendation-food-tags',tags.join(' · ')));
    names.append(make('p','hint',`${discoveryCategoryNames[item.category]||item.category||'분류 미확인'} · ${item.address||'주소 미확인'}`));heading.append(names);card.append(heading);

    if(item.synthetic)card.append(make('p','badge warn','합성 검증 자료 · 실제 추천 아님'));
    const reasons=(item.reason_sentences||item.supported_reasons||[]).filter(reason=>(typeof reason==='string'?reason:reason.text)!=='공개 지도에 등록된 장소입니다. 지점과 영업 상태를 확인해 주세요.').filter(reason=>!((item.important_unknowns||[]).length&&(typeof reason==='string'?reason:reason.text)==='방문 조건과 추천 근거를 추가로 확인해야 합니다.'));
    if(reasons.length){const list=make('ul','recommendation-reasons');reasons.slice(0,3).forEach(reason=>list.append(make('li','',typeof reason==='string'?reason:reason.text)));card.append(list);}
    const mustCheck=(item.important_unknowns||[]).filter(code=>!/^(OPENING|OPERATING|LIVE_AVAILABILITY|PARTY|PRICE|ORIGIN|FACT|CHILDREN_UNKNOWN|MIN_PARTY|MAX_PARTY|PUBLIC_MAP|HOURS_UNKNOWN|VISIT_TIME_UNKNOWN)/.test(code));
    if(mustCheck.length)recommendationUnknowns(card,mustCheck);
    else if(item.important_unknowns?.length)card.append(make('p','hint','영업·예약은 방문 전 확인해 주세요.'));
    const specificReasons=(window.DiscoveryFlow?.cardReasons(item)||item.reason_codes||[]).filter(code=>!(item.important_unknowns||[]).includes(code));
    if(item.eligibility==='ineligible'&&specificReasons.length){const reasonDetails=make('section','card-options');reasonDetails.append(make('h4','','이번 목록에서 제외한 이유'));appendRecommendationReasons(reasonDetails,specificReasons);card.append(reasonDetails);}
    const factChecked=(item.facts||[]).filter(fact=>fact.usable===true&&['verified','provisional'].includes(fact.status)&&Number.isFinite(Date.parse(fact.checked_at||''))).sort((a,b)=>Date.parse(b.checked_at)-Date.parse(a.checked_at))[0]?.checked_at;
    const checkedLabel=item.checked_at?`추천 근거 확인 ${reviewDate(item.checked_at)}`:factChecked?`자료 확인 ${reviewDate(factChecked)}`:'자료 확인일 미확인';
    if(item.price_basis)card.append(make('p','recommendation-basis',recommendationPrice(item.price_basis)));
    if(item.movement?.straight_line_m!=null||item.movement?.distance_m!=null||item.movement?.route?.duration_seconds!=null)card.append(make('p','recommendation-basis',recommendationMovement(item.movement)));
    if(item.checked_at||factChecked)card.append(make('p','hint',checkedLabel));
    const actions=make('div','actions recommendation-primary-actions'),selection=make('div','actions recommendation-selection-actions'),more=make('div','card-secondary'),secondary=make('div','actions');

    actions.append(button('상세 보기',()=>discoveryPlaceDetail(item.place_id,run),'primary'),button('저장',()=>saveRecommendedPlace(item,run),'secondary'));
    if(comparable){selection.append(button('일정에 넣기',()=>addPlaceToItinerary(item,run),'secondary'));const compare=button('비교에 추가',()=>toggleComparison(item,run),'secondary compare-toggle');compare.dataset.placeId=item.place_id;compare.setAttribute('aria-pressed','false');selection.append(compare);}
    if(item.source_refs?.length)secondary.append(button(`추천 근거 ${item.source_refs.length}개`,()=>recommendationSources(item),'text-button'));
    window.ProductTools?.feedbackButton(secondary,item.place_id,{kind:'preference',run_id:run.run_id});if(rank!=null)window.ProductTools?.observe(card,item.place_id,run.run_id);
    const extra=button('더 보기',()=>openDialog(item.name||'장소 선택',body=>{if(comparable)body.append(selection);body.append(secondary);window.DiscoveryExperience?.cardEvidence(body,item,run);if(specificReasons.length){body.append(make('h3','','추천에 사용하지 못한 정보'));appendRecommendationReasons(body,specificReasons);}}),'text-button');
    actions.append(extra);card.append(actions);return card;
  }
  async function saveRecommendedPlace(item,run){if(!state.trip)return;const epoch=state.epoch;try{const saved=await api(tripPath()+'/bookmarks',{method:'POST',body:{input_kind:'place',input_value:item.place_id,note:'',run_id:run?.run_id||null}});if(epoch!==state.epoch)return;await loadDiscovery({quiet:true});notice(saved.duplicate?'이미 보관함에 저장되어 있습니다. 개인 메모는 유지했습니다.':'현재 여행의 보관함에 저장했습니다.');}catch(error){fail(error,$('#recommendationError'));}}
  function recommendationSources(item){openDialog('추천 이유에 사용한 근거',body=>{body.append(make('h3','',item.name));window.DiscoveryExperience?.evidenceDetail(body,item.review_evidence,item,state.recommendations.displayed);for(const source of item.source_refs||[]){const row=make('div','source-card');row.append(make('strong','',source.source_type==='official'?'공식 출처':source.source_group==='OpenStreetMap'?'공개 지도 자료':source.source_type==='synthetic'?'합성 검증 출처':source.source_type||'자료 출처'),make('p','hint',`확인 ${reviewDate(source.checked_at)}`));window.ProductTools?.sourceLink(safeDiscoveryLink(row,source.url,'이 출처 열기'),item.place_id,source.id,state.recommendations.displayed?.run_id);body.append(row);}body.append(make('p','hint','같은 내용의 재전재는 독립 근거 여러 개로 간주하지 않습니다. 출처가 있다는 사실은 특정 날짜의 예약 가능을 뜻하지 않습니다.'));});}
  function comparisonSelection(){const r=state.recommendations;if(!r.comparison)r.comparison={runId:null,selected:[],pending:false};return r.comparison;}
  function comparisonCandidates(run){const result=new Map();for(const section of Object.values(run?.result?.sections||{}))for(const key of ['items','needs_confirmation','insufficient_data'])for(const item of section[key]||[])if(!result.has(item.place_id))result.set(item.place_id,item);return result;}
  function toggleComparison(item,run){const selection=comparisonSelection();if(selection.pending)return;if(selection.runId&&selection.runId!==run.run_id){selection.selected=[];notice('같은 추천 요청의 방문 조건으로 비교합니다. 이전 선택을 초기화했습니다.');}selection.runId=run.run_id;const index=selection.selected.indexOf(item.place_id);if(index>=0)selection.selected.splice(index,1);else if(selection.selected.length>=3){notice('비교는 서로 다른 장소를 최대 3곳까지 선택할 수 있습니다. 한 곳을 해제해 주세요.');return;}else selection.selected.push(item.place_id);renderComparisonTray();}
  function renderComparisonTray(){const host=$('#comparisonTray');if(!host)return;const selection=comparisonSelection(),displayed=state.recommendations.displayed;if(selection.runId&&selection.runId!==displayed?.run_id){selection.selected=[];selection.runId=null;}const candidates=comparisonCandidates(displayed);selection.selected=selection.selected.filter(id=>candidates.has(id));host.replaceChildren();host.hidden=!selection.selected.length;for(const node of $$('.compare-toggle')){const chosen=selection.selected.includes(node.dataset.placeId);node.setAttribute('aria-pressed',String(chosen));node.textContent=chosen?'비교 선택 해제':'비교에 추가';node.disabled=selection.pending;}if(!selection.selected.length)return;host.append(make('strong','',`서로 다른 장소 ${selection.selected.length}/3곳 선택`),make('p','hint','저장된 같은 방문일·인원 기준으로 비교합니다. 두 곳 이상 선택해 주세요.'));const list=make('ul','comparison-selected');for(const id of selection.selected){const row=make('li');row.append(make('span','',candidates.get(id).name||'장소'),button('해제',()=>{selection.selected=selection.selected.filter(value=>value!==id);renderComparisonTray();},'text-button'));list.append(row);}host.append(list);const actions=make('div','actions');const submit=button(selection.pending?'비교 자료 확인 중…':'선택한 장소 비교',createComparison,'primary');submit.disabled=selection.selected.length<2||selection.pending;actions.append(submit,button('모두 해제',()=>{selection.selected=[];renderComparisonTray();},'text-button'));host.append(actions);}
  async function createComparison(){const selected=comparisonSelection();if(!state.trip||selected.pending||selected.selected.length<2||selected.selected.length>3)return;const epoch=state.epoch,path=tripPath(),payload={run_id:selected.runId,place_ids:[...selected.selected]};selected.pending=true;renderComparisonTray();try{const result=await api(path+'/comparisons',{method:'POST',body:payload});if(epoch!==state.epoch)return;selected.id=result.comparison_id;showComparison(result);}catch(error){if(epoch===state.epoch)fail(error,$('#recommendationError'));}finally{if(epoch===state.epoch){selected.pending=false;renderComparisonTray();}}}
  function comparisonFact(item,field){const rows=(item.facts||[]).filter(fact=>fact.field===field);if(rows.some(fact=>fact.status==='conflict'))return null;return rows.find(fact=>fact.status==='verified'&&fact.usable===true&&fact.value!=null)?.value??null;}
  function comparisonRating(item){const rating=comparisonFact(item,'rating');if(!rating||typeof rating!=='object')return '미확인';return `${rating.platform||'플랫폼 미확인'} · ${rating.rating??'평점 미확인'} / ${rating.scale??'척도 미확인'}\n전체 별점 평가 ${rating.total_rating_count??'수 미확인'}${rating.total_rating_count==null?'':'건'}`;}
  function comparisonLanguage(item){const evidence=item.review_evidence;if(evidence?.state!=='available'||!evidence.counts||!evidence.metrics)return '미확인 · 사용할 수 있는 리뷰 언어 관측 근거 없음';const counts=evidence.counts;return `관측 본문 ${counts.text_count??'미확인'}건 · 판별 ${counts.classified_count??'미확인'}건 · 미판별 ${counts.unknown_count??'미확인'}건\n판별한 본문 기준 현지어 ${percentage(evidence.metrics.classified_local_share)} · 한국어 ${percentage(evidence.metrics.classified_korean_share)}\n관측 기간 밖의 전체 리뷰나 현지 주민 비율이 아닙니다.`;}
  function comparisonFit(item){const label={eligible:'입력한 필수 방문 조건 확인',needs_confirmation:'필수 방문 조건 확인 필요',ineligible:'이번 방문 조건과 불일치'}[item.eligibility]||'미확인';return label+(item.visit_fit?`\n필요한 조건 ${item.visit_fit.confirmed_count??'미확인'}/${item.visit_fit.required_count??'미확인'}개 확인`:'');}
  function comparisonField(item,field){const value=comparisonFact(item,field);return value==null?'미확인':discoveryFactValue(value,field);}
  function showComparison(data){openDialog('같은 방문 조건으로 장소 비교',body=>{body.classList.add('comparison-body');const conditions=data.conditions||{};body.append(make('p','form-note',recommendationConditionsLabel(conditions)));if(data.origin_status==='stale')body.append(make('p','form-note','숙소가 변경된 뒤의 이전 비교입니다. 이전 출발점과 이동 근거를 현재 값으로 해석하지 마세요.'));body.append(make('p','hint',`같은 출발점 · ${{walking:'도보',car:'자동차',transit:'대중교통'}[data.comparison_basis?.transport||conditions.transport]||'이동 수단 미확인'} 기준으로 비교합니다.`));if(data.input_status==='stale')body.append(make('p','form-note','이전 방문 조건의 저장된 비교입니다. 현재 조건에 맞는 결과로 해석하지 마세요.'));if(data.data_status==='stale'||!(data.items||[]).length){body.append(make('p','muted','근거의 이용 범위·확인 기한이 변경되어 비교 자료를 표시할 수 없습니다. 현재 자료로 추천을 다시 요청해 주세요.'));appendRecommendationReasons(body,data.reason_codes||[]);return;}body.append(make('p','hint','미확인 칸은 0점이나 이용 가능으로 처리하지 않습니다. 평점 플랫폼과 가격 단위를 함께 확인하세요.'));body.append(make('p','hint comparison-scroll-hint','비교표를 좌우로 밀어 다른 장소를 확인하세요. 키보드는 표에 초점을 맞춘 뒤 방향키를 사용할 수 있어요.'));const wrap=make('div','comparison-table-wrap');wrap.tabIndex=0;wrap.setAttribute('role','region');wrap.setAttribute('aria-label','장소 비교표 · 작은 화면에서는 가로로 이동할 수 있습니다.');const table=make('table','comparison-table'),head=make('thead'),heading=make('tr');heading.append(make('th','','비교 항목'));for(const item of data.items||[]){const cell=make('th');cell.scope='col';cell.append(make('strong','',item.name||'장소 이름 미확인'));if(item.native_name&&item.native_name!==item.name)cell.append(make('p','hint',item.native_name));if(item.synthetic)cell.append(make('p','badge warn','합성 검증 자료'));heading.append(cell);}head.append(heading);table.append(head);const rows=make('tbody');const definitions=[['분류·주소',item=>`${discoveryCategoryNames[item.category]||'분류 미확인'}\n${item.address||'주소 미확인'}`],['방문 조건',comparisonFit],['영업시간',item=>comparisonField(item,'opening_hours')],['최소·최대 예약 인원',item=>`최소 ${comparisonField(item,'min_party')} · 1예약 최대 ${comparisonField(item,'max_party')}\n시설 전체 정원과 별도 기준입니다.`],['아동 조건',item=>comparisonField(item,'children_rule')],['가격·인원·이용 범위',item=>recommendationPrice(item.price_basis)],['평점·전체 평가 수',comparisonRating],['이동 근거',item=>recommendationMovement(item.movement)],['최근 리뷰 언어',comparisonLanguage],['실시간 잔여석',()=> '미확인 · 이 방문일·시각·인원의 실제 슬롯을 확인하지 않았습니다.'],['방문 전 확인',item=>(item.important_unknowns||[]).map(code=>recommendationReasonNames[code]||reviewReasonText[code]||'관련 근거 확인 필요').join('\n')||'추가 확인 항목 없음'],['근거 확인일',item=>reviewDate(item.checked_at)]];for(const [title,format] of definitions){const row=make('tr'),label=make('th','',title);label.scope='row';row.append(label);for(const item of data.items||[])row.append(make('td','',format(item)));rows.append(row);}const sourceRow=make('tr'),sourceTitle=make('th','','출처·상세');sourceTitle.scope='row';sourceRow.append(sourceTitle);for(const item of data.items||[]){const cell=make('td');for(const [i,source] of (item.source_refs||[]).entries())safeDiscoveryLink(cell,source.url,`${i+1}. ${source.source_type==='official'?'공식 출처':source.source_group==='OpenStreetMap'?'공개 지도 자료':source.source_type==='synthetic'?'합성 출처':'확인한 출처'}`);cell.append(button('장소 상세',()=>discoveryPlaceDetail(item.place_id),'secondary'));sourceRow.append(cell);}rows.append(sourceRow);table.append(rows);wrap.append(table);body.append(wrap,make('p','form-note','통상 영업표는 미래 방문일의 확정 정보가 아닙니다. 외부 예약 페이지 이동은 예약 완료가 아닙니다.'));const id=data.comparison_id;body.append(button('저장된 비교 상태 새로고침',async()=>{const epoch=state.epoch;try{const refreshed=await api(tripPath()+'/comparisons/'+encodeURIComponent(id));if(epoch===state.epoch)showComparison(refreshed);}catch(error){fail(error,$('#recommendationError'));}},'text-button'));});}

  $('#applyRecommendations').addEventListener('click',applyRecommendations);
  $('#refreshRecommendations').addEventListener('click',()=>loadRecommendations().catch(error=>fail(error,$('#recommendationError'))));
  $('#recommendationHistory').addEventListener('change',event=>{state.recommendations.displayed=null;loadRecommendations({runId:event.target.value}).catch(error=>fail(error,$('#recommendationError')));});
  ['#reviewStrictFilter','#ratingFilterEnabled','#recommendationMinRating','#recommendationMinCount','#recommendationLimit'].forEach(selector=>$(selector).addEventListener('input',()=>{state.recommendations.optionsDirty=true;state.recommendations.filterRevision=(state.recommendations.filterRevision||0)+1;renderRecommendationResults();}));

  const itineraryStatusNames={validated:'검증한 일정',provisional:'잠정 초안 · 확인 필요',conflicted:'충돌 있음',empty:'배치된 일정 없음'};
  const itineraryReasonNames={CONFLICTING_LOCKS:'고정 예약·잠금 항목끼리 시간이 겹칩니다. 어느 예약도 자동으로 이동하지 않았습니다.',NO_TIME_WINDOW:'체류·양쪽 이동·버퍼를 모두 넣을 시간 구간이 없습니다.',PARTY_MISMATCH:'선택한 인원이 확인된 이용 기준과 맞지 않습니다.',NO_ROUTE:'이 구간의 이동 경로를 확인하지 못했습니다.',UNKNOWN_TRAVEL:'이동시간을 확인하지 못했습니다.',UNKNOWN_OPENING_HOURS:'방문 전체 시간에 적용되는 영업시간이 미확인입니다.',MISSING_REQUIRED_FACT:'필수 조건을 판단할 사실이 부족합니다.',BOOKING_TIME_CONFLICT:'예약의 대표 시각과 구간별 시각이 다릅니다. 예약 화면에서 두 값을 확인해 주세요.',BOOKING_CHANGED:'원 예약이 변경되었습니다. 현재 예약으로 다시 검증해야 합니다.',SOURCE_DATA_CHANGED:'출처의 유효 기간·이용 범위가 변경되었습니다.',TRIP_VERSION_CONFLICT:'여행 조건이 변경되었습니다. 최신 내용을 확인해 주세요.',VERSION_CONFLICT:'다른 화면에서 일정을 수정했습니다. 입력을 보존했으니 최신 일정을 확인해 주세요.',FIXED_BOOKING_READONLY:'고정 예약은 일정 편집으로 바꿀 수 없습니다. 예약 화면에서 원 예약을 확인해 주세요.',LOCKED_ITEM:'잠근 항목입니다. 먼저 이 항목의 잠금 해제를 명시적으로 미리보기·적용해 주세요.',PREVIEW_EXPIRED:'변경 미리보기의 유효 시간이 지났습니다. 입력을 유지하고 다시 미리보기 해 주세요.',OUTSIDE_TRIP:'여행 또는 도시 구간 밖입니다.',UNSUPPORTED_CITY:'이 도시의 장소 일정 생성을 아직 지원하지 않습니다.',BUDGET_EXHAUSTED:'새 외부 호출 예산이 부족합니다. 저장된 일정은 계속 읽을 수 있습니다.',DUPLICATE_PLACE:'같은 장소를 중복 배치하지 않았습니다.',CANDIDATE_LIMIT:'이번 일정의 후보 수 상한에 도달했습니다.',EXECUTION_LIMIT:'이번 요청의 실행 상한에 도달했습니다.',UNKNOWN_AIRPORT_BUFFER:'공항 접근·출국 또는 입국 버퍼를 확인해야 합니다.'};
  Object.assign(itineraryReasonNames,{TIME_OVERLAP:'일정 항목의 시간이 겹칩니다.',INSUFFICIENT_TRAVEL_TIME:'이전·다음 항목 사이의 이동과 준비 버퍼가 부족합니다.',BREAK_TIME_OVERLAP:'체류 중 확인된 브레이크타임과 겹칩니다.',OUTSIDE_OPENING_HOURS:'전체 체류시간이 확인된 영업 구간 안에 들어가지 않습니다.',OUTSIDE_ACTIVITY_WINDOW:'설정한 하루 활동 시간 밖입니다.',OUTSIDE_TRIP_WINDOW:'여행 또는 도시 구간 밖입니다.',EXCEPTIONAL_CLOSURE:'방문일에 특별 휴무가 확인되었습니다.',FUTURE_OPENING_UNCONFIRMED:'통상 영업표이며 미래 방문일에 적용되는지 확인해야 합니다.',UNKNOWN_WEEKDAY_HOURS:'해당 요일의 영업시간을 확인해야 합니다.',UNKNOWN_CLOSURE_RULE:'예외 휴무 규칙을 확인해야 합니다.',PROVISIONAL_CLOSURE:'휴무 정보가 잠정 상태입니다.',UNKNOWN_CHILD_AGE:'아동 나이를 확인해야 합니다.',UNKNOWN_CHILD_RULE:'아동 이용 조건을 확인해야 합니다.',ESTIMATED_TRAVEL:'이동시간은 실제 경로가 아닌 계획 추정입니다.',WALKING_ROUTE_UNVERIFIED:'실제 도보 경로·횡단 가능 여부가 미확인입니다.',ROUTE_COORDINATES_UNKNOWN:'출발지나 목적지의 사용 가능한 좌표가 없습니다.',ROUTE_DISTANCE_REQUIRES_PROVIDER:'이 거리는 직선거리 추정으로 도보 시간을 확인할 수 없습니다.',ROUTE_POLICY_UNAVAILABLE:'이동 자료의 사용 범위가 미확인입니다.',ROUTE_RESPONSE_STALE:'경로 자료의 확인 기한이 지났습니다.',ROUTE_RESPONSE_INVALID:'경로 응답을 검증하지 못했습니다.',ROUTE_ELEMENT_LIMIT:'경로 조회 요소 수 상한에 도달했습니다.',ROUTE_TIME_LIMIT:'경로 조회 시간 상한에 도달했습니다.',TRAVEL_MODE_UNSUPPORTED:'선택한 교통수단의 경로를 확인할 수 없습니다.',TRAVEL_DEPARTURE_UNKNOWN:'이동 출발 시각을 확인해야 합니다.',MISSING_TRAVEL_LEG:'항목 사이의 이동 구간이 미확인입니다.',NONEXISTENT_LOCAL_TIME:'서머타임 전환으로 존재하지 않는 현지 시각입니다.',AMBIGUOUS_LOCAL_TIME:'두 번 존재하는 현지 시각입니다. 첫 번째·두 번째 시각을 선택해 주세요.',MISSING_TIMEZONE:'현지 시간대가 없어 시각을 확정할 수 없습니다.',MISSING_LOCAL_TIME:'현지 시각을 확인해야 합니다.',MISSING_REQUIRED_TIME:'고정 예약의 시작·종료 시각을 확인해야 합니다.',DATE_ONLY:'날짜만 있는 예약입니다. 임의의 자정 시각을 넣지 않았습니다.',UNRESOLVED_FIXED_BOOKING:'고정 예약의 시각이 미확인이라 주변 장소를 배치하지 않았습니다.',INVALID_LOCAL_TIME:'현지 날짜·시각을 확인해 주세요.',INVALID_INTERVAL:'종료가 시작 이후인지 확인해 주세요.',BOOKING_EDIT_FORBIDDEN:'고정 예약은 일정 편집에서 변경할 수 없습니다.',BOOKING_LOCK_REQUIRED:'원 예약의 고정 상태는 유지해야 합니다.',BOOKING_REMOVED:'참조하던 원 예약이 삭제되었습니다.',FIXED_BOOKING_MISSING:'현재 고정 예약의 참조를 확인해야 합니다.',ITEM_LOCKED:'사용자가 잠근 항목입니다. 먼저 명시적으로 잠금을 해제해 주세요.',ITEM_NOT_FOUND:'변경할 항목이 현재 일정에 없습니다.',PLACE_UNAVAILABLE:'이 장소의 현재 사용 권한·근거를 확인해야 합니다.',PLANNING_DEFAULT_DURATION:'체류시간은 제품의 기본 계획 제안입니다.',DEFAULT_STAY_DURATION:'체류시간은 제품의 기본 계획 제안입니다.',DEFAULT_ACTIVITY_WINDOW:'하루 활동 시간은 기본 계획 제안입니다.',SEARCH_LIMIT_REACHED:'탐색 상한에 도달해 가능한 범위까지만 배치했습니다.',STOP_BOUNDARY_UNSUPPORTED:'한 도시 체류 구간 안에서 일정을 만들어 주세요. 다른 도시 예약은 유지됩니다.',DATE_OUTSIDE_TRIP:'여행 날짜 안에서 일정을 만들어 주세요.'});
  Object.assign(itineraryReasonNames,{PLANNED_REST:'저장한 휴식 선호에 따라 둔 쉬는 시간입니다.',REST_PREFERENCE:'휴식 간격과 길이는 사용자가 검토할 계획 선호입니다.',DAILY_VISIT_TARGET:'여행 밀도에 맞춘 하루 방문 수 목표이며 필수 조건을 완화하지 않습니다.',DEFAULT_MEAL_WINDOW:'식사 시간대는 기본 계획 제안입니다.',SAME_PLACE_NO_TRAVEL:'같은 장소 식별 근거가 있어 별도 이동을 넣지 않았습니다.',IDENTICAL_LOCATION:'같은 위치로 확인되어 별도 이동을 넣지 않았습니다.',NO_REST_WINDOW:'방문 후 필요한 휴식 시간을 넣을 구간이 없습니다.',REQUIRED_REST_MISSING:'필수로 지정한 휴식 시간이 부족합니다.',LODGING_TIME_UNCONFIRMED:'숙소의 체크인·체크아웃 시각을 확인해야 합니다.',ROUTE_IMPOSSIBLE:'해당 구간의 이동이 불가능하다는 근거가 있습니다.',MAXIMUM_DISTANCE_EXCEEDED:'필수로 지정한 최대 이동량을 넘습니다.',UNKNOWN_REQUIRED_DISTANCE:'필수 최대 이동량을 검증할 거리 정보가 없습니다.'});
  function itineraryReasonText(value){if(typeof value==='string')return itineraryReasonNames[value]||recommendationReasonNames[value]||reviewReasonText[value]||'관련 조건의 근거를 확인해야 합니다.';const reason=value?.reason||value?.message;if(reason&&!/^[A-Z0-9_]+$/.test(reason))return reason;return itineraryReasonText(value?.code||value?.reason_code||reason||'UNKNOWN');}
  function itineraryReasons(host,values){if(!values?.length)return;const list=make('ul','itinerary-reasons');values.forEach(value=>list.append(make('li','',itineraryReasonText(value))));host.append(list);}
  function clearItineraryScope(){clearTimeout(itineraryPollTimer);state.itineraries={runs:[],active:null,displayed:null,serial:0,submitting:false,loaded:false,day:null,selected:new Map()};['#itinerarySelectionSummary','#itineraryJobStatus','#itineraryHistory','#itineraryValidation','#itineraryTimeline','#itineraryUnplaced','#exploreItinerarySelection'].forEach(id=>$(id)?.replaceChildren());['#itineraryHistoryWrap','#itineraryDayControls','#itineraryUnplaced','#exploreItinerarySelection'].forEach(id=>{if($(id))$(id).hidden=true;});if($('#itineraryError'))showError($('#itineraryError'));}
  function itineraryCandidatePool(){const pool=new Map();for(const bookmark of state.discovery.bookmarks||[])if(bookmark.resolve_state==='resolved'&&bookmark.matched_place_id&&!bookmark.excluded)pool.set(bookmark.matched_place_id,{place_id:bookmark.matched_place_id,name:bookmark.place?.display_name||bookmark.place?.name||bookmark.input_value,address:bookmark.place?.address,category:bookmark.place?.category});for(const [id,item] of comparisonCandidates(state.recommendations.displayed))if(item.eligibility!=='ineligible'&&!pool.has(id))pool.set(id,{...item,selection_run_id:state.recommendations.displayed.run_id});for(const [id,item] of state.itineraries.selected)if(!pool.has(id))pool.set(id,item);return pool;}
  function toggleItineraryPlace(item,runId=null){const selected=state.itineraries.selected,id=item.place_id||item.id;if(selected.has(id))selected.delete(id);else if(selected.size>=30){notice('한 일정의 장소 후보는 최대 30곳입니다.');return;}else selected.set(id,{...item,selection_run_id:runId,place_id:id,duration_minutes:item.category==='attraction'?90:item.category==='cafe'?45:60,duration_origin:'default'});renderItinerarySelection();window.WorkspaceUX?.changed();}
  function renderItinerarySelection(){const r=state.itineraries;if(!r)return;$('#itineraryCandidateCount').textContent=r.selected.size?`${r.selected.size}곳 선택`:'고정 예약만으로도 시작할 수 있어요';const host=$('#itinerarySelectionSummary'),explore=$('#exploreItinerarySelection');host.replaceChildren();$('#createItinerary').disabled=!state.trip||r.submitting;$('#pickItineraryPlaces').disabled=!state.trip;if(!state.trip){host.append(make('p','muted','여행을 먼저 만들어 주세요.'));return;}host.append(make('p','',r.selected.size?`${r.selected.size}곳을 후보로 선택했습니다. 배치할 수 없는 장소는 이유를 남깁니다.`:'장소를 고르거나, 고정 예약만으로 일정 초안을 만들 수 있습니다.'));if(r.selected.size){const list=make('ul','itinerary-selected-list');for(const item of r.selected.values())list.append(make('li','',`${item.name||'선택한 장소'} · ${item.duration_minutes}분 · ${item.duration_origin==='user'?'사용자 입력':'기본 제안'}`));host.append(list);}host.append(button('탐색에서 장소 고르기',()=>setTab('explore'),'text-button'));explore.replaceChildren();explore.hidden=!r.selected.size;if(r.selected.size)explore.append(make('p','',`일정 후보 ${r.selected.size}곳을 선택했습니다. 체류시간과 생성 조건을 확인해 주세요.`),button('일정에서 선택 확인',()=>setTab('itinerary'),'secondary'));for(const node of $$('.itinerary-select-toggle')){const chosen=r.selected.has(node.dataset.placeId);node.textContent=chosen?'일정 후보 해제':'일정 후보 선택';node.setAttribute('aria-pressed',String(chosen));}}
  function itineraryConditions(data){return data?.snapshot?.conditions||data?.snapshot?.input?.conditions||state.discovery.conditions?.conditions||{};}
  function itineraryId(data){return data?.itinerary_id||data?.id;}
  async function loadItineraries({id=null}={}){clearTimeout(itineraryPollTimer);if(!state.trip||!state.session?.authenticated){renderItinerary();return;}const epoch=state.epoch,serial=++state.itineraries.serial,path=tripPath();const history=await allPages(path+'/itineraries');if(epoch!==state.epoch||serial!==state.itineraries.serial)return;const r=state.itineraries;r.runs=history;r.loaded=true;const selected=id||itineraryId(r.active)||itineraryId(history[0]);if(!selected){renderItinerary();window.WorkspaceUX?.afterRender();return;}const active=await api(path+'/itineraries/'+encodeURIComponent(selected));if(epoch!==state.epoch||serial!==state.itineraries.serial)return;r.active=active;if(!r.selectionRestored){r.selectionRestored=true;if(!r.selected.size){const pool=itineraryCandidatePool();for(const item of active.snapshot?.selected||[])r.selected.set(item.place_id,{...pool.get(item.place_id),...item,name:pool.get(item.place_id)?.name||'저장된 선택 장소'});}}if(active.active_revision_id&&active.data_status!=='unavailable')r.displayed=active;else{const previousId=itineraryId(r.displayed);r.displayed=null;const previous=history.find(run=>itineraryId(run)===previousId&&itineraryId(run)!==selected)||history.find(run=>['succeeded','partial'].includes(run.state||run.job?.state)&&itineraryId(run)!==selected);if(previous){const saved=await api(path+'/itineraries/'+encodeURIComponent(itineraryId(previous)));if(epoch!==state.epoch||serial!==state.itineraries.serial)return;if(saved.active_revision_id&&saved.data_status!=='unavailable')r.displayed=saved;}}renderItinerary();window.WorkspaceUX?.afterRender();if(['queued','running'].includes(active.state||active.job?.state)&&state.tab==='itinerary')itineraryPollTimer=setTimeout(()=>loadItineraries({id:selected}).catch(error=>fail(error,$('#itineraryError'))),2000);}
  function itineraryRecommendationRef(selected,run){if(!selected.length||!run?.result||run.input_status!=='current'||run.data_status!=='current'||recommendationStale(run))return null;const candidates=comparisonCandidates(run);return selected.every(item=>item.selection_run_id===run.run_id&&candidates.has(item.place_id))?run.run_id:null;}
  function itineraryGenerationForm(preselectedPlace=null){
    if(!state.trip||!state.discovery.conditions){showError($('#itineraryError'),'여행과 저장한 방문 조건을 먼저 확인해 주세요.');return;}
    if(preselectedPlace?.type==='click')preselectedPlace=null;
    const trip=state.trip,epoch=state.epoch,conditions=state.discovery.conditions.conditions||{},pool=itineraryCandidatePool(),current=ensureDiscoveryDraft()?.conditions||conditions;
    const suggestedDate=window.WorkspaceUX?.insertionDay(current.visit?.date,state.workspaceReturn?.insertion_date,trip.start_date)||current.visit?.date||trip.start_date;
    openDialog(preselectedPlace?'일정에 장소 넣기':'첫 일정 미리보기',body=>{
      body.append(make('p','form-note',preselectedPlace?.name||recommendationConditionsLabel(conditions)),make('p','hint','날짜·시각·체류시간을 확인하세요. 배치와 충돌을 미리 본 다음 적용하며, 외부 예약은 변경하지 않습니다.'));
      if(state.discovery.dirty)body.append(make('p','form-note','탐색에서 바꾼 조건은 아직 적용 전입니다. 이번 미리보기에는 마지막으로 저장한 인원·출발점·필수 조건을 사용해요. 필요하면 탐색에서 조건을 먼저 적용해 주세요.'));
      let start,end,dayStart,dayEnd,provisional,general,airportBefore,airportAfter,restMinutes,afterVisits,preview=null,requestRevision=0,jobBusy=false,tripVersion=trip.version,conditionsVersion=state.discovery.conditions.version;
      const previewHost=make('section','itinerary-preview');previewHost.setAttribute('aria-live','polite');const durations=new Map();
      const {form,grid}=formBase(body,'일정 영향 미리보기',async()=>{
        if(jobBusy||epoch!==state.epoch)return;
        if(end.value<start.value)return localError(form,'end_date','종료일은 시작일 이후여야 합니다.');
        if(Date.parse(end.value)-Date.parse(start.value)>=14*86400000)return localError(form,'end_date','한 번에 최대 14일의 일정을 만들어 주세요.');
        if(dayEnd.value<=dayStart.value)return localError(form,'activity_end','하루 활동 종료는 시작 이후여야 합니다.');
        const chosen=[...state.itineraries.selected.values()];const selected=chosen.map((item,index)=>({place_id:item.place_id,duration_minutes:item.duration_minutes,duration_origin:item.duration_origin,priority:index}));
        const payload={trip_version:tripVersion,conditions_version:conditionsVersion,itinerary_request_version:1,recommendation_run_id:itineraryRecommendationRef(chosen,state.recommendations.displayed),start_date:start.value,end_date:end.value,selected,activity_start:dayStart.value,activity_end:dayEnd.value,allow_provisional:provisional.checked,buffers:{general_minutes:Number(general.value),booking_before_minutes:0,booking_after_minutes:0,airport_before_minutes:airportBefore.value===''?null:Number(airportBefore.value),airport_after_minutes:airportAfter.value===''?null:Number(airportAfter.value),unknown_travel_allowance_minutes:30},rest_preferences:{minutes:Number(restMinutes.value),after_visits:Number(afterVisits.value)}};
        const intent='itinerary-preview:'+trip.id+':'+JSON.stringify(payload);if(!state.intents.has(intent))state.intents.set(intent,uid());const revision=++requestRevision;
        preview=null;jobBusy=true;previewHost.replaceChildren(make('p','loading','미리보기 요청을 보내고 있어요…'));
        try{const result=await api(tripPath(trip)+'/itineraries/generation-previews',{method:'POST',headers:{'Idempotency-Key':state.intents.get(intent)},body:payload});if(epoch!==state.epoch)return;
          const id=itineraryId(result),jobId=result.job_id||result.job?.job_id;state.intents.set('generation-receipt:'+id,intent);state.workspaceReturn={...state.workspaceReturn,generation_preview_id:id,generation_job_id:jobId||null};window.WorkspaceUX?.changed();
          if(result.job)mergeJob(result.job);bindJob(id,jobId,revision);if(jobId)watchJob(jobId,epoch);await refresh(id,revision);
        }catch(error){jobBusy=false;previewHost.replaceChildren(make('p','form-note','미리보기를 준비하지 못했습니다. 입력은 유지했어요. 같은 입력으로 다시 요청하면 기존 접수 결과를 확인합니다.'));if(error.status===409)previewHost.append(button('최신 여행 조건 확인 · 입력 유지',reloadConditions,'secondary'));throw error;}
      });
      start=field(grid,'start_date','넣을 날짜',suggestedDate,{type:'date',required:true});start.min=trip.start_date;start.max=trip.end_date;
      dayStart=field(grid,'activity_start','시작 시각 · 제안',state.workspaceReturn?.insertion_time||(current.meal_time==='dinner'?'18:00':'12:00'),{type:'time',required:true,hint:'실제 배치 시각은 고정 예약과 이동을 검증한 미리보기에서 확인해 주세요.'});
      const selectedHost=make('div','field wide');grid.append(selectedHost);
      function drawSelected(){selectedHost.replaceChildren();for(const item of state.itineraries.selected.values()){const row=make('div','form-grid');const input=field(row,'duration.'+item.place_id,(item.name||pool.get(item.place_id)?.name||'선택한 장소')+' · 체류시간(분)',item.duration_minutes,{type:'number',min:5,max:720,required:true,wide:true,hint:item.duration_origin==='user'?'직접 입력한 계획 시간입니다.':'기본 제안이며 실제 체류시간이나 예약 확정을 뜻하지 않습니다.'});input.step=1;input.addEventListener('input',()=>{item.duration_minutes=Number(input.value);item.duration_origin='user';window.WorkspaceUX?.changed();});selectedHost.append(row);durations.set(item.place_id,input);}if(!state.itineraries.selected.size)selectedHost.append(make('p','hint','선택한 장소가 없어 고정 예약을 먼저 확인합니다.'));
      }drawSelected();
      provisional=reviewCheckbox(grid,'allow_provisional','미확인 영업·이동을 표시한 잠정 일정도 검토하기',false);
      const more=make('details','discovery-extra field wide');more.append(make('summary','','다른 후보 · 여러 날짜 · 계획 가정'));const extra=make('div','form-grid');more.append(extra);grid.append(more);
      end=field(extra,'end_date','일정 종료일',suggestedDate,{type:'date',required:true,hint:'한 번에 최대 14일, 한 도시 체류 구간 안에서 검증합니다.'});end.min=trip.start_date;end.max=trip.end_date;
      dayEnd=field(extra,'activity_end','하루 활동 종료','21:00',{type:'time',required:true});
      for(const item of pool.values()){const row=make('label','check field wide'),check=make('input');check.type='checkbox';check.checked=state.itineraries.selected.has(item.place_id);check.addEventListener('change',()=>{toggleItineraryPlace(item,item.selection_run_id||null);check.checked=state.itineraries.selected.has(item.place_id);drawSelected();});row.append(check,make('span','',item.name||'저장한 장소'));extra.append(row);}
      general=field(extra,'buffers.general_minutes','방문 사이 일반 여유 · 분',10,{type:'number',min:0,max:180,required:true,hint:'기본 제안이며 실제 경로 시간이 아닙니다.'});airportBefore=field(extra,'buffers.airport_before_minutes','출발 공항 준비 여유 · 분','',{type:'number',min:0,max:480,hint:'비어 있으면 미확인입니다.'});airportAfter=field(extra,'buffers.airport_after_minutes','도착 공항 준비 여유 · 분','',{type:'number',min:0,max:480});restMinutes=field(extra,'rest_preferences.minutes','휴식 시간 · 분',20,{type:'number',min:0,max:180,required:true});afterVisits=field(extra,'rest_preferences.after_visits','방문 몇 회마다 쉴지',2,{type:'number',min:1,max:10,required:true});
      extra.append(make('p','hint field wide','잠정 이동은 계획 여유 30분을 사용합니다. 실제 이동시간으로 표시하지 않으며 확인된 휴무·필수 조건 불충족·예약 충돌은 넘지 않습니다.'));
      start.addEventListener('change',()=>{end.value=start.value;state.workspaceReturn={...state.workspaceReturn,insertion_date:start.value};window.WorkspaceUX?.changed();});dayStart.addEventListener('change',()=>{state.workspaceReturn={...state.workspaceReturn,insertion_time:dayStart.value};window.WorkspaceUX?.changed();});
      body.append(previewHost);form.addEventListener('input',()=>{requestRevision++;preview=null;jobBusy=false;previewHost.replaceChildren(make('p','hint','입력이 바뀌었어요. 변경된 조건을 다시 미리보기 해 주세요.'));});
      const recovery=state.workspaceReturn?.generation_preview_id;if(recovery){const action=button('이전에 요청한 미리보기 이어보기',async()=>{const revision=++requestRevision;bindJob(recovery,state.workspaceReturn?.generation_job_id,revision);try{await refresh(recovery,revision);if(state.workspaceReturn?.generation_job_id)watchJob(state.workspaceReturn.generation_job_id,epoch);}catch(error){formError(form,error);}},'secondary');previewHost.append(make('p','hint','아직 적용하지 않은 미리보기가 있어요. 저장 당시 조건을 확인한 뒤 적용할 수 있습니다.'),action);}
      function valid(revision){return epoch===state.epoch&&previewHost.isConnected&&$('#dialog').open&&revision===requestRevision;}
      function bindJob(id,jobId,revision){state.itineraries.generationPreview={jobId,update:async job=>{if(!valid(revision))return;jobBusy=!terminal(job);previewHost.replaceChildren(make('p','',terminal(job)?'미리보기 결과를 확인하고 있어요.':job.state==='queued'?'미리보기 대기 중':'예약·이동·운영 조건 확인 중'));if(job.stage)previewHost.append(make('p','hint',(stageText[job.stage]||'조건 검증 중')+(job.total_count!=null?` · ${job.done_count||0}/${job.total_count}건`:'')));if(['failed','cancelled'].includes(job.state)){previewHost.append(make('p','form-note',nextAction(job)||'미리보기를 완료하지 못했습니다. 입력은 유지됩니다. 조건을 확인하고 다시 요청해 주세요.'));return;}if(terminal(job))await refresh(id,revision);}};}
      async function reloadConditions(){try{await Promise.all([loadBookings(),loadDiscovery({quiet:true})]);if(epoch!==state.epoch||!previewHost.isConnected)return;tripVersion=state.trip.version;conditionsVersion=state.discovery.conditions.version;requestRevision++;preview=null;jobBusy=false;start.min=end.min=state.trip.start_date;start.max=end.max=state.trip.end_date;previewHost.replaceChildren(make('p','form-note','최신 여행 조건을 확인했습니다. 날짜·시각·체류 입력은 유지했어요. 다시 미리보기 해 주세요.'),make('p','hint',recommendationConditionsLabel(state.discovery.conditions.conditions)));}catch(error){formError(form,error);}}
      async function refresh(id,revision){const value=await api(tripPath(trip)+'/itineraries/'+encodeURIComponent(id)+'/generation-preview');if(!valid(revision))return;
        if(value.applied_revision_id){jobBusy=false;preview=null;previewHost.replaceChildren(make('p','form-note','이 미리보기는 이미 일정에 적용됐어요. 저장된 결과를 확인할 수 있습니다.'),button('저장된 일정 열기',async()=>{try{const saved=await api(tripPath(trip)+'/itineraries/'+encodeURIComponent(id));if(!valid(revision))return;delete state.workspaceReturn.generation_preview_id;delete state.workspaceReturn.generation_job_id;state.itineraries.active=saved;state.itineraries.displayed=saved;window.WorkspaceUX?.changed();closeDialog(true);setTab('itinerary');await loadItineraries({id});}catch(error){formError(form,error);}},'primary'));return;}
        if(['expired','stale'].includes(value.state)){const prior=state.intents.get('generation-receipt:'+id);if(prior)state.intents.delete(prior);state.intents.delete('generation-receipt:'+id);}

        if(!value.result){previewHost.replaceChildren(make('p','loading','미리보기를 준비하고 있어요. 작업 상태와 함께 결과가 갱신됩니다.'),button('진행 상태 다시 확인',()=>{const jobId=state.workspaceReturn?.generation_job_id;if(jobId)pollJob(jobId,epoch).catch(error=>formError(form,error));refresh(id,revision).catch(error=>formError(form,error));},'text-button'));return;}
        jobBusy=false;preview=value;renderItineraryPreview(previewHost,{...value.result,...value},{items:[]},()=>apply(id,value,revision),()=>refresh(id,revision));const snapshot=value.snapshot;previewHost.prepend(make('p','form-note',`검증한 입력: ${snapshot?.start_date||''} ${snapshot?.activity_start||''} · ${(snapshot?.selected||[]).length}곳 · ${snapshot?.allow_provisional?'잠정 일정 허용':'엄격 검증'}`));if(value.state==='expired'||value.state==='stale')previewHost.append(make('p','error','미리보기의 조건이나 근거가 바뀌었어요. 현재 입력으로 다시 미리보기 해 주세요.'),button('최신 여행 조건 확인 · 입력 유지',reloadConditions,'secondary'));if(value.result.unplaced?.length){previewHost.append(make('h3','','넣지 못한 장소'));for(const item of value.result.unplaced){previewHost.append(make('p','',item.name||pool.get(item.place_id)?.name||'선택한 장소'));itineraryReasons(previewHost,item.reason_codes||[item.code]);}}
      }
      async function apply(id,value,revision){if(!valid(revision)||preview!==value||dialogBusy)return;dialogBusy=true;const applying=previewHost.querySelector('.itinerary-apply');if(applying){applying.disabled=true;applying.textContent='적용 중…';}try{const result=await api(tripPath(trip)+'/itineraries/'+encodeURIComponent(id)+'/generation-preview/apply',{method:'POST',body:{preview_id:value.preview_id,expected_version:0}});if(epoch!==state.epoch)return;delete state.workspaceReturn.generation_preview_id;delete state.workspaceReturn.generation_job_id;state.itineraries.active=result;state.itineraries.displayed=result;window.WorkspaceUX?.changed();closeDialog(true);setTab('itinerary');await loadItineraries({id:itineraryId(result)});notice('확인한 일정을 적용했어요. 외부 예약은 변경되지 않았습니다.');}catch(error){if(valid(revision)){preview=null;formError(form,error);previewHost.append(make('p','form-note','적용하지 못했습니다. 입력을 유지했어요. 최신 조건으로 다시 미리보기 해 주세요.'));}}finally{dialogBusy=false;}}
    });
  }
  function itineraryDates(data){const first=data.snapshot?.start_date,last=data.snapshot?.end_date;if(first&&last){const start=Date.parse(first+'T00:00:00Z'),end=Date.parse(last+'T00:00:00Z');if(Number.isFinite(start)&&Number.isFinite(end)&&end>=start&&end-start<14*86400000)return Array.from({length:(end-start)/86400000+1},(_,index)=>new Date(start+index*86400000).toISOString().slice(0,10));}return [...new Set((data.items||[]).map(item=>(item.local_start||item.local_date||item.date||'').slice(0,10)).filter(Boolean))].sort();}
  function itineraryItemId(item){return item.item_id||item.id;}
  function itineraryItemName(item){return item.name||item.display_name||item.title||item.place_name||({booking:'고정 예약',place:'방문 장소',meal:'식사',rest:'휴식',travel:'이동'}[item.item_type])||'일정 항목';}
  function itineraryLocal(value){return value?String(value).replace('T',' ').slice(0,16):'시각 미정';}
  function itineraryFixed(item){return item.item_type==='booking'||['booking','booking_event','source_booking'].includes(item.lock_origin);}
  function itineraryStatusSummary(host,values,label){if(!values?.length)return;const section=make('section','itinerary-alert');section.append(make('h3','',`${label} · ${values.length}건`));itineraryReasons(section,values);host.append(section);}
  function renderItinerary(){const r=state.itineraries,active=r.active,data=r.displayed;renderItinerarySelection();const status=$('#itineraryJobStatus');status.replaceChildren();if(active){const stateName=active.state||active.job?.state;status.append(make('p','',({queued:'일정 생성 대기',running:'예약·이동·운영 조건 확인 중',succeeded:'일정 생성 작업 완료',partial:'일정 생성 일부 완료',failed:'새 일정 생성 실패',cancelled:'일정 생성 취소됨'})[stateName]||'일정 상태 확인 중'));const job=active.job||{};if(['queued','running'].includes(stateName)){status.append(make('p','hint',`${job.done_count!=null?'처리 '+job.done_count+'건':''}${job.total_count!=null?' / 대상 '+job.total_count+'건':''} · 완료할 때까지 저장된 이전 일정을 유지합니다.`));const jobId=active.job_id||job.job_id;if(jobId&&!job.cancel_requested_at)status.append(button('생성 취소 요청',async()=>{try{await api('/jobs/'+encodeURIComponent(jobId)+'/cancel',{method:'POST'});await loadItineraries({id:itineraryId(active)});}catch(error){fail(error,$('#itineraryError'));}},'text-button'));}if(job.error_code)itineraryReasons(status,[job.error_code]);}const history=$('#itineraryHistory');history.replaceChildren();$('#itineraryHistoryWrap').hidden=!r.runs.length;for(const run of r.runs)history.append(new Option(`${reviewDate(run.created_at)} · ${itineraryStatusNames[run.validation_status]||statusText[run.state]||'저장된 요청'}`,itineraryId(run)));history.value=itineraryId(active)||'';const summary=$('#itineraryValidation');summary.replaceChildren();const timeline=$('#itineraryTimeline');timeline.replaceChildren();$('#itineraryDayControls').hidden=!data;$('#itineraryDays').hidden=!data;$('#itineraryUnplaced').hidden=true;if(!state.trip){timeline.append(make('div','panel empty','여행을 만들고 저장한 예약과 장소를 일정으로 정리해 보세요.'));return;}if(!data){timeline.append(make('div','panel empty','저장된 일정이 없습니다. ‘일정 만들기’에서 날짜·후보·검증 방식을 확인해 주세요.'));return;}const stale=data.input_status==='stale'||data.data_status==='stale'||itineraryId(active)!==itineraryId(data);summary.append(make('h2','',itineraryStatusNames[data.validation_status]||'일정 확인 필요'),make('p','hint',`일정 버전 ${data.version} · ${recommendationConditionsLabel(itineraryConditions(data))}`));if(stale)summary.append(make('p','form-note','이전 입력·예약·자료를 참조하는 일정입니다. 최신 조건으로 다시 검증하기 전 방문 가능으로 간주하지 마세요.'));itineraryStatusSummary(summary,data.conflicts,'충돌');itineraryStatusSummary(summary,data.unresolved_conditions,'확인 필요');if(data.booking_changes?.length)itineraryStatusSummary(summary,data.booking_changes,'원 예약 변경');if(data.assumptions?.length){const details=make('details','review-details');details.append(make('summary','','이번 일정의 계획 가정'));for(const assumption of data.assumptions)details.append(make('p','hint',typeof assumption==='string'?assumption:assumption.description||assumption.reason||assumption.label||itineraryReasonText(assumption)));summary.append(details);}const items=data.items||[],dates=itineraryDates(data);if(!dates.length)dates.push(data.snapshot?.start_date||itineraryConditions(data).visit?.date||state.trip.start_date);if(!dates.includes(r.day))r.day=dates[0];const select=$('#itineraryDay');select.replaceChildren();dates.forEach(date=>select.append(new Option(date,date)));select.value=r.day;renderDayStrip($('#itineraryDays'),dates.map((date,index)=>dayChoice(date,index,items.filter(item=>(item.local_start||item.local_date||item.date||'').slice(0,10)===date).length,'개')),r.day,date=>{r.day=date;state.workspaceReturn={...state.workspaceReturn,itinerary_day:date};window.WorkspaceUX?.changed();renderItinerary();});$('#undoItinerary').disabled=!data.can_undo;$('#editItineraryAdd').disabled=!data.active_revision_id;timeline.append(make('h2','itinerary-date-heading',r.day+' · 현지 시각'));const today=items.filter(item=>(item.local_start||item.local_date||item.date||r.day).slice(0,10)===r.day).sort((a,b)=>(a.local_start||'').localeCompare(b.local_start||''));if(!today.length)timeline.append(make('div','panel empty','이 날짜에 배치된 항목이 없습니다. 휴식과 여유 시간을 남길 수 있습니다.'));for(const item of today){for(const leg of data.legs||[])if((leg.to_item_id||leg.to_id)===itineraryItemId(item))timeline.append(itineraryLeg(leg));timeline.append(itineraryItemCard(item,data));}const unplaced=$('#itineraryUnplaced');unplaced.replaceChildren();if(data.unplaced?.length){unplaced.hidden=false;unplaced.append(make('h2','',`넣지 못한 장소 · ${data.unplaced.length}곳`),make('p','hint','모든 후보를 넣기 위해 필수 조건을 완화하지 않았습니다.'));const pool=itineraryCandidatePool();for(const candidate of data.unplaced){const row=make('section');row.append(make('h3','',candidate.name||pool.get(candidate.place_id)?.name||'선택한 장소'));itineraryReasons(row,candidate.reason_codes||[candidate.code]);if(candidate.missing_facts?.length)row.append(make('p','hint','확인할 사실: '+candidate.missing_facts.map(field=>discoveryFactNames[field]||'관련 조건').join(' · ')));unplaced.append(row);}}}
  function itinerarySamePlaceLeg(leg){return (typeof leg.basis==='object'?leg.basis?.kind:leg.basis)!=='unknown'&&leg.duration_minutes===0&&(leg.reason_codes||[]).some(code=>['SAME_PLACE_NO_TRAVEL','IDENTICAL_LOCATION'].includes(code));}
  function itineraryReservationText(item){if(item.item_type==='rest'||item.reservation_status==='not_applicable'||!item.reservation_status)return null;return {confirmed:'예약 확인',source_verified:'예약 원문 확인',user_confirmed:'사용자가 확인한 예약',unconfirmed:'예약 미확인',not_reserved:'미예약',not_booked:'미예약',cancelled:'예약 취소',unknown:'미확인'}[item.reservation_status]||'원 예약 확인 필요';}
  function itineraryLeg(leg){if(leg.filter_only){const proof=make('div','itinerary-leg');proof.append(make('p','hint',`숙소 기준 도보 조건 검증 · ${leg.duration_minutes==null?'시간 미확인':leg.duration_minutes+'분'} · 실제 일정 이동과 비용 합계에서 제외`));return proof;}const row=make('div','itinerary-leg'),basis=typeof leg.basis==='object'?leg.basis.kind:leg.basis;const duration=leg.duration_minutes??leg.duration,samePlace=itinerarySamePlaceLeg(leg);row.append(make('strong','',samePlace?'같은 장소에서 이어짐 · 별도 이동 없음':`이동 · ${basis==='provider'?'경로 공급자 근거':basis==='estimate'?'계획 추정':'시간 미확인'}`),make('p','',`${duration==null?'실제 이동시간 미확인':duration+'분'}${leg.mode?' · '+({walking:'도보',transit:'대중교통',car:'자동차'}[leg.mode]||leg.mode):''}${(leg.distance_m??leg.distance_meters)==null?'':' · 거리 '+Math.round(leg.distance_m??leg.distance_meters)+'m'}`));if(leg.planning_allowance_minutes!=null)row.append(make('p','hint','미확인 이동에 둔 계획 여유 '+leg.planning_allowance_minutes+'분 · 실제 이동시간이 아닙니다.'));if(leg.buffer_minutes!=null)row.append(make('p','hint','별도 준비 버퍼 '+leg.buffer_minutes+'분'));if(basis!=='provider')row.append(make('p','hint',basis==='estimate'?'추정값이며 실제 출입구·환승·대기 시간을 확인한 경로가 아닙니다.':'0분으로 처리하지 않습니다. 잠정 계획 여유와 실제 경로 시간을 구분해 주세요.'));row.append(make('p','hint',`${samePlace?'위치 대조':'경로 확인'} ${reviewDate(leg.checked_at)}${leg.expires_at?' · 유효 기한 '+reviewDate(leg.expires_at):''}`));if(leg.synthetic||/fake|synthetic/i.test(leg.provider||''))row.append(make('p','badge warn','합성 경로 · 테스트 전용'));for(const source of leg.source_refs||[])if(source?.url)safeDiscoveryLink(row,source.url,'이동 근거 출처');if(leg.reason_codes?.length)itineraryReasons(row,leg.reason_codes.filter(code=>!['SAME_PLACE_NO_TRAVEL','IDENTICAL_LOCATION'].includes(code)));return row;}
  function itineraryTimeRange(item) {
    const start = item.local_start, end = item.local_end;
    const full = `${itineraryLocal(start)} → ${itineraryLocal(end)}`;
    const sameZone = (item.start_timezone || item.timezone) === (item.end_timezone || item.start_timezone || item.timezone);
    const clock = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/;
    return sameZone && clock.test(start || '') && clock.test(end || '') && start.slice(0,10) === end.slice(0,10)
      ? `${start.slice(11,16)} → ${end.slice(11,16)}` : full;
  }
  function itineraryItemCard(item,data){const fixed=itineraryFixed(item),card=make('article','panel itinerary-item');card.dataset.itemId=itineraryItemId(item);card.append(make('p','itinerary-item-time',itineraryTimeRange(item)),make('h3','',itineraryItemName(item)),make('p','hint',`${item.start_timezone||item.timezone||'시간대 미확인'}${item.end_timezone&&item.end_timezone!==item.start_timezone?' → '+item.end_timezone:''}${item.duration_minutes!=null&&!item.lodging_anchor?' · '+(item.item_type==='rest'?'휴식 ':'체류 ')+item.duration_minutes+'분':''}`));if(item.synthetic)card.append(make('p','badge warn','합성 검증 일정 · 실제 여행지 검증 아님'));if(!fixed&&item.duration_minutes!=null)card.append(make('p','hint',(item.item_type==='rest'?'휴식시간 기준: 저장한 휴식 선호':'체류시간 기준: '+(item.duration_origin==='user'?'사용자 입력':'기본 계획 제안'))));if(item.assumptions?.length)itineraryReasons(card,item.assumptions);if(item.native_name&&item.native_name!==itineraryItemName(item))card.append(make('p','hint',item.native_name));if(item.lodging_anchor)card.append(make('p','form-note','숙박 기간입니다. 여러 밤의 낮 시간을 전부 막지 않습니다. 체크인·체크아웃·짐 보관 조건은 따로 확인해 주세요.'));const status=make('p','itinerary-item-state',`${fixed?'고정 예약 · 일정에서 수정 불가':item.locked?'사용자가 잠근 항목 · 예약 확정과 별개':item.item_type==='rest'?'휴식 선호로 배치한 계획':'이동 가능한 계획 항목'} · ${item.item_type==='rest'&&item.verification_status==='verified'?'계획 조건 확인':({verified:'근거 확인',validated:'검증됨',provisional:'잠정 · 확인 필요',unknown:'확인 필요',conflicted:'충돌 있음'}[item.verification_status]||'상태 확인 필요')}`);card.append(status);const reservationText=itineraryReservationText(item);if(reservationText)card.append(make('p','hint','예약 상태: '+reservationText));const issues=[...(data.conflicts||[]),...(data.unresolved_conditions||[])].filter(value=>(value.item_ids||[]).includes(itineraryItemId(item))||value.item_id===itineraryItemId(item));itineraryReasons(card,issues);if(item.reason_codes?.length)itineraryReasons(card,item.reason_codes);const actions=make('div','actions');const more=make('details','card-options itinerary-options'),extra=make('div','actions');more.append(make('summary','','시간 빠른 조정 · 잠금 · 삭제'));if(fixed){actions.append(button('예약 상세에서 확인',()=>openBookingFromJourney(item.booking_id||item.source_refs?.booking_id,item.booking_event_id),'secondary'));}else if(item.item_type!=='travel'){if(item.locked)actions.append(button('잠금 해제 미리보기',()=>itineraryEditForm(item,'unlock'),'secondary'));else{actions.append(button('시각·체류시간 편집',()=>itineraryEditForm(item,'move'),'primary'));extra.append(button('↑ 30분 앞당기기',()=>itineraryEditForm(item,'move',shiftItineraryLocal(item.local_start,-30)),'secondary'),button('↓ 30분 늦추기',()=>itineraryEditForm(item,'move',shiftItineraryLocal(item.local_start,30)),'secondary'),button('잠금 미리보기',()=>itineraryEditForm(item,'lock'),'text-button'),button('삭제 미리보기',()=>itineraryEditForm(item,'remove'),'text-button'));}}if(item.place_id){actions.append(button('장소 근거 확인',()=>discoveryPlaceDetail(item.place_id),'text-button'));window.ProductTools?.feedbackButton(actions,item.place_id,{itinerary_id:itineraryId(data),item_id:itineraryItemId(item),run_id:data.snapshot?.recommendation_run_id||null});}card.append(actions);if(extra.children.length){more.append(extra);card.append(more);}return card;}
  function shiftItineraryLocal(value,minutes){if(!value)return '';const m=/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(value);if(!m)return value;return new Date(Date.UTC(+m[1],+m[2]-1,+m[3],+m[4],+m[5]+minutes)).toISOString().slice(0,16);}
  async function addPlaceToItinerary(item,run=null){
    if(!state.trip)return;const epoch=state.epoch;
    if(!state.itineraries.selected.has(item.place_id))toggleItineraryPlace(item,run?.run_id||null);
    try{await loadItineraries();if(epoch!==state.epoch)return;
      if(state.itineraries.displayed?.active_revision_id){const day=window.WorkspaceUX?.insertionDay(ensureDiscoveryDraft()?.conditions?.visit?.date,state.workspaceReturn?.insertion_date,state.trip.start_date)||state.discovery.conditions?.conditions?.visit?.date;itineraryEditForm(null,'add',day+'T'+(state.workspaceReturn?.insertion_time||(state.discovery.conditions?.conditions?.meal_time==='dinner'?'18:00':'12:00')),item);}
      else{itineraryGenerationForm(item);}
    }catch(error){fail(error);}
  }
  function itineraryEditForm(item,op='move',suggestedTime=null,preselectedPlace=null){const data=state.itineraries.displayed;if(!state.trip||!data?.active_revision_id)return;if(item&&itineraryFixed(item)){notice('고정 예약은 예약 화면에서 확인해 주세요.');return;}const trip=state.trip,epoch=state.epoch,iid=itineraryId(data);let expectedVersion=data.version,latest=data,preview=null;const title={move:'일정 시각 변경',add:'일정에 장소 추가',remove:'일정 항목 삭제',lock:'일정 항목 잠금',unlock:'이 항목 잠금 해제',undo:'이전 편집 되돌리기'}[op];openDialog(title,body=>{body.append(make('p','form-note',`${item?itineraryItemName(item)+' · ':''}일정 버전 ${expectedVersion}. 먼저 영향을 확인한 뒤 별도로 적용합니다. 외부 예약은 변경되지 않습니다.`));let localStart,duration,place,fold,steps;const previewHost=make('section','itinerary-preview');const {form,grid}=formBase(body,'변경 영향 미리보기',async()=>{const command={op};if(item)command.item_id=itineraryItemId(item);if(op==='move'||op==='add'){command.local_start=localStart.value;command.duration_minutes=Number(duration.value);if(fold?.value)command.fold=Number(fold.value);}if(op==='add'){command.place_id=place.value;command.duration_origin='user';}const path=tripPath(trip)+'/itineraries/'+encodeURIComponent(iid);let value;try{value=await api(path+(op==='undo'?'/undo-previews':'/edit-previews'),{method:'POST',body:op==='undo'?{expected_version:expectedVersion,steps:Number(steps.value)}:{expected_version:expectedVersion,commands:[command]}});}catch(error){if(error.status===409){preview=null;previewHost.replaceChildren(make('p','form-note','현재 입력을 유지했습니다. 최신 일정으로 다시 미리보기 해 주세요.'),button('최신 일정 확인 · 입력 유지',refresh,'secondary'));}throw error;}if(epoch!==state.epoch)return;preview=value;renderItineraryPreview(previewHost,value,latest,()=>apply(value),()=>refresh());});if(op==='move'||op==='add'){if(op==='add'){const candidates=[...itineraryCandidatePool().values()];place=field(grid,'place_id','추가할 장소',preselectedPlace?.place_id||'',{select:[['','장소 선택'],...candidates.map(value=>[value.place_id,value.name||'선택한 장소'])],required:true,wide:true});}localStart=field(grid,'local_start','현지 시작 날짜·시각',suggestedTime||item?.local_start?.slice(0,16)||(state.workspaceReturn?.insertion_date||ensureDiscoveryDraft()?.conditions?.visit?.date||state.trip.start_date)+'T'+(state.workspaceReturn?.insertion_time||'12:00'),{type:'datetime-local',required:true,wide:true,hint:'장소·도시의 IANA 시간대로 서버에서 다시 검증합니다.'});duration=field(grid,'duration_minutes','체류시간 · 분',item?.duration_minutes||60,{type:'number',min:5,max:720,required:true});duration.step=1;fold=field(grid,'fold','현지 시각이 두 번 있는 경우','',{select:[['','미선택 · 모호하면 확인 필요'],['0','첫 번째 시각'],['1','두 번째 시각']],hint:'바르셀로나 서머타임 종료일 등의 중복 시각에만 선택하세요.'});}else if(op==='undo'){steps=field(grid,'steps','되돌릴 사용자 편집 수',1,{type:'number',min:1,max:10,required:true,hint:'현재 예약·권한·사실로 다시 검증한 새 버전을 만듭니다.'});}else grid.append(make('p','form-note field wide',op==='unlock'?'이 항목의 사용자 잠금을 해제하는 요청입니다. 다른 잠금이나 예약은 자동으로 해제하지 않습니다.':op==='remove'?'계획에서 이 항목을 빼는 미리보기입니다. 원 예약·장소 보관함·개인 메모를 삭제하지 않습니다.':'배치와 시간을 고정할 사용자 잠금입니다. 예약 완료로 바뀌지 않습니다.'));body.append(previewHost);form.addEventListener('input',event=>{if(op==='add'&&event.target===localStart){state.workspaceReturn={...state.workspaceReturn,insertion_date:localStart.value.slice(0,10),insertion_time:localStart.value.slice(11,16)};window.WorkspaceUX?.changed();}if(preview){preview=null;previewHost.replaceChildren(make('p','form-note','입력이 바뀌었습니다. 변경 영향을 다시 미리보기 해 주세요.'));}});async function refresh(){try{const current=await api(tripPath(trip)+'/itineraries/'+encodeURIComponent(iid));if(epoch!==state.epoch)return;expectedVersion=current.version;latest=current;state.itineraries.active=current;state.itineraries.displayed=current;renderItinerary();preview=null;previewHost.replaceChildren(make('p','form-note',`최신 일정 버전 ${expectedVersion}을 불러왔습니다. 위 입력은 유지했습니다. 다시 미리보기 해 주세요.`));}catch(error){formError(form,error);}}async function apply(value){if(!preview||preview.preview_id!==value.preview_id||dialogBusy)return;dialogBusy=true;const btn=previewHost.querySelector('.itinerary-apply');if(btn)btn.disabled=true;try{const result=await api(tripPath(trip)+'/itineraries/'+encodeURIComponent(iid),{method:'PATCH',body:{preview_id:value.preview_id,expected_version:value.base_version??expectedVersion}});if(epoch!==state.epoch)return;state.itineraries.active=result;state.itineraries.displayed=result;closeDialog(true);renderItinerary();notice('검증한 변경을 새 일정 버전으로 저장했습니다. 외부 예약은 변경되지 않았습니다.');}catch(error){if(epoch===state.epoch){formError(form,error);preview=null;previewHost.append(make('p','form-note','변경을 적용하지 않았습니다. 입력은 위에 유지했습니다. 최신 일정을 확인한 뒤 다시 미리보기 해 주세요.'),button('최신 일정 확인 · 입력 유지',refresh,'secondary'));}}finally{dialogBusy=false;}}});}
  function renderItineraryPreview(host,preview,before,apply,refresh){host.replaceChildren();host.append(make('h3','','변경 영향 미리보기'),make('p','hint',`기준 버전 ${preview.base_version} · 미리보기 만료 ${reviewDate(preview.expires_at)} · 아직 저장하지 않았습니다.`));const beforeMap=new Map((before.items||[]).map(item=>[itineraryItemId(item),item])),afterMap=new Map((preview.items||preview.after?.items||[]).map(item=>[itineraryItemId(item),item]));const diff=make('div','itinerary-diff');let changes=0;for(const id of new Set([...beforeMap.keys(),...afterMap.keys()])){const old=beforeMap.get(id),next=afterMap.get(id);if(old&&next&&old.local_start===next.local_start&&old.local_end===next.local_end&&old.duration_minutes===next.duration_minutes&&old.locked===next.locked)continue;changes++;const row=make('div','source-card');row.append(make('strong','',itineraryItemName(next||old)),make('p','',!old?'추가 예정':!next?'삭제 예정':`이전: ${itineraryLocal(old.local_start)} → ${itineraryLocal(old.local_end)}${old.locked?' · 잠금':''}`));if(next)row.append(make('p','',`변경: ${itineraryLocal(next.local_start)} → ${itineraryLocal(next.local_end)}${next.locked?' · 잠금':''}`));diff.append(row);}if(!changes)diff.append(make('p','hint','항목의 시각·체류·잠금 변경이 없습니다. 검증 결과와 이동 영향을 확인해 주세요.'));host.append(diff);const previousLegs=(before.legs||[]).filter(l=>!l.filter_only);const nextLegs=(preview.legs||preview.after?.legs||[]).filter(l=>!l.filter_only);const knownBefore=previousLegs.filter(leg=>leg.duration_minutes!=null).reduce((sum,leg)=>sum+leg.duration_minutes,0),knownAfter=nextLegs.filter(leg=>leg.duration_minutes!=null).reduce((sum,leg)=>sum+leg.duration_minutes,0);if(previousLegs.length||nextLegs.length)host.append(make('p','form-note',`수치가 있는 이동 합계: 이전 ${knownBefore}분 → 변경 ${knownAfter}분. 미확인 이동 ${previousLegs.filter(leg=>leg.duration_minutes==null).length} → ${nextLegs.filter(leg=>leg.duration_minutes==null).length}구간은 합계에 포함하지 않습니다. 추정과 실제 경로는 아래에서 구분합니다.`));const prices=preview.price_delta;if(prices){const costs=make('div','form-note');costs.append(make('strong','','예상 지출 변화 · 통화별'));for(const [currency,p] of Object.entries(prices.currencies||{}))costs.append(make('p','',`${currency} 이전 ${p.before_known_lower??'미확인'}–${p.before_upper??'미확인'} → 변경 ${p.after_known_lower??'미확인'}–${p.after_upper??'미확인'} · 차이 ${p.delta_lower??'미확인'}–${p.delta_upper??'미확인'}`));costs.append(make('p','hint',`가격 미확인 항목 ${prices.before_unknown_items} → ${prices.after_unknown_items}개. 환급 예치금·선결제와 이용료를 구분하며 환산 총액은 만들지 않습니다.`));host.append(costs);} const affected=preview.affected_dates||preview.diff?.affected_dates;if(affected?.length)host.append(make('p','hint','영향 날짜: '+affected.join(' · ')));itineraryStatusSummary(host,preview.conflicts,'적용을 막는 충돌');itineraryStatusSummary(host,preview.unresolved_conditions,'확인 필요');const legs=preview.legs||preview.after?.legs||[];if(legs.length){const details=make('details','review-details');details.append(make('summary','','변경 후 이동 구간 확인'));legs.forEach(leg=>details.append(itineraryLeg(leg)));host.append(details);}const actions=make('div','actions');const submit=button('검증한 변경 적용',apply,'primary itinerary-apply');submit.disabled=preview.can_apply!==true;actions.append(submit,button('최신 일정 다시 확인',refresh,'secondary'));host.append(make('p','form-note',preview.can_apply===true?'검증 결과를 확인한 뒤 적용하세요. 이 버튼을 눌러야 새 일정 버전이 저장됩니다.':'현재 검증으로는 적용할 수 없습니다. 위 입력을 수정하거나 최신 일정을 확인한 뒤 다시 미리보기 해 주세요.'),actions);}
  $('#createItinerary').addEventListener('click',itineraryGenerationForm);
  $('#pickItineraryPlaces').addEventListener('click',itineraryGenerationForm);
  $('#refreshItineraries').addEventListener('click',()=>loadItineraries().catch(error=>fail(error,$('#itineraryError'))));
  $('#itineraryHistory').addEventListener('change',event=>loadItineraries({id:event.target.value}).catch(error=>fail(error,$('#itineraryError'))));
  $('#itineraryDay').addEventListener('change',event=>{state.itineraries.day=event.target.value;state.workspaceReturn={...state.workspaceReturn,itinerary_day:event.target.value};window.WorkspaceUX?.changed();renderItinerary();});
  $('#itineraryDayTop').addEventListener('click',()=>$('#itineraryTimeline').scrollIntoView({block:'start',behavior:'instant'}));
  $('#editItineraryAdd').addEventListener('click',()=>itineraryEditForm(null,'add'));
  $('#undoItinerary').addEventListener('click',()=>itineraryEditForm(null,'undo'));

  window.DiscoveryFlow?.init({state,api,tripPath,applyRecommendations,loadRecommendations});
  window.DiscoveryExperience?.init({state,api,make,button,field,openDialog,closeDialog,notice,fail,tripPath,ensureDiscoveryDraft,setDiscoveryOverride,renderRecommendationResults,recommendationCard,discoveryPlaceDetail,appendRecommendationReasons,reviewDate,reviewCounts,reviewScope,reviewAttribution,reasonList,reviewReasons,percentage,reviewAdminAllowed,discoveryConditionsForm,bookmarkForm,applyRecommendations,commitRecommendationFilters,filterValues:recommendationFilterValues});
  window.ReviewLab?.init({state,api,make,button,field,formBase,openDialog,closeDialog,notice,fail,confirmAction,reviewDate,safeDiscoveryLink,loadReviews,reviewCollectForm,reviewControlsForm,reviewPolicyForm});
  window.WorkspaceUX?.init({state,api,make,button,openDialog,closeDialog,notice,fail,setTab,setExploreView,filters:recommendationFilterValues,restoreFilters:commitRecommendationFilters,candidates:itineraryCandidatePool,render(){renderDiscoveryConditions();renderBookmarks();renderItinerarySelection();}});
  window.AccommodationTools?.init({state,api,make,button,field,formBase,openDialog,closeDialog,confirmAction,safeDiscoveryLink,fail,notice,tripPath,ensureDiscoveryDraft,draftChanged,setTab});
  window.TravelTools?.init({state,api,make,button,field,formBase,openDialog,closeDialog,fail,notice,loadItineraries,setTab,knownDestination,destinations:()=>destinationCatalog});
  window.ProductTools?.init({state,api,make,button,field,formBase,openDialog,closeDialog,fail,notice,destinations:()=>destinationCatalog});
  async function boot(){if(sessionCheck)return;const navigationAtStart=state.navigationRevision||0;sessionCheck=true;$('#retrySession').disabled=true;
    try{const old=state.session?.user?.id;const s=await api('/session');if(!s.authenticated){await window.TravelTools?.purge().catch(()=>{});clearPrivate();state.session=s;authScreen();return;}if(old&&old!==s.user?.id)clearPrivate();await window.TravelTools?.bind(s.user.id).catch(()=>{});state.session=s;window.ProductTools?.bind();if(!armSessionExpiry())return;$('#boot').hidden=true;$('#auth').hidden=true;$('#app').hidden=false;$('#accountName').textContent=s.user?.display_name||s.user?.name||s.user?.email||'내 계정';$('#sessionExpiry').textContent=s.expires_at?`세션 만료: ${new Date(s.expires_at).toLocaleString('ko-KR')}`:'개인 여행은 로그인한 계정만 볼 수 있습니다.';await loadDestinations().catch(()=>notice('도시 목록을 불러오지 못했습니다. 기존 여행은 계속 볼 수 있어요.'));if(!old||!state.trip){const previous=navigationMemory();await loadTrips(previous.trip_id);if((state.navigationRevision||0)===navigationAtStart&&['trip','explore','itinerary','mail','today','preparation','product','ask','reviews','settings'].includes(previous.tab))setTab(previous.tab);}await loadUsage();if(state.tab==='reviews')await loadReviews({quiet:true});
    }catch(err){if(err.name!=='AbortError'){clearPrivate();authScreen(err.code==='SERVER_UNAVAILABLE'?err.message:'서버에 연결하지 못했습니다. 잠시 후 다시 연결을 확인해 주세요.');}}
    finally{sessionCheck=false;$('#retrySession').disabled=false;}
  }
  $('#loginForm').addEventListener('submit',()=>{$('#invitation').value=$('#invitation').value.trim();});
  $('#retrySession').addEventListener('click',boot);
  $('#logout').addEventListener('click',async()=>{const b=$('#logout');b.disabled=true;await window.TravelTools?.purge().catch(()=>{});channel?.postMessage('logout');let message='로그아웃했습니다.';try{await api('/auth/logout',{method:'POST',body:{}});channel?.postMessage('logout');}catch(err){if(err.status!==401)message='화면의 개인 정보는 지웠지만 서버 로그아웃을 확인하지 못했습니다. 연결 후 다시 로그인 상태를 확인해 주세요.';}finally{const configured=state.session?.auth_configured;clearPrivate();state.session={authenticated:false,auth_configured:configured===true};authScreen(message==='로그아웃했습니다.'?'':message);if(message==='로그아웃했습니다.')notice(message);b.disabled=false;}});
  if(channel)channel.onmessage=e=>{if(e.data==='logout')expire('다른 창에서 로그아웃했습니다.');};
  window.addEventListener('focus',()=>{if(!sessionDeadlinePassed()&&state.session?.authenticated)boot();});
  document.addEventListener('visibilitychange',()=>{if(!document.hidden&&!sessionDeadlinePassed()&&state.session?.authenticated)boot();});
  window.addEventListener('pageshow',e=>{if(e.persisted){clearPrivate();$('#auth').hidden=true;$('#boot').hidden=false;boot();}});
  function theme(v){if(v==='auto')document.documentElement.removeAttribute('data-theme');else document.documentElement.dataset.theme=v;$('#theme').value=v;try{localStorage.setItem('travel-inbox-theme',v);}catch{}}
  let savedTheme='auto';try{savedTheme=localStorage.getItem('travel-inbox-theme')||'auto';}catch{}theme(['auto','light','dark'].includes(savedTheme)?savedTheme:'auto');$('#theme').addEventListener('change',e=>theme(e.target.value));
  function textSize(value){const setting=value==='large'?'large':'normal';if(setting==='large')document.documentElement.dataset.textSize='large';else document.documentElement.removeAttribute('data-text-size');$('#textSize').value=setting;try{localStorage.setItem('travel-inbox-text-size',setting);}catch{}}
  let savedTextSize='normal';try{savedTextSize=localStorage.getItem('travel-inbox-text-size')||'normal';}catch{}textSize(savedTextSize);$('#textSize').addEventListener('change',event=>textSize(event.target.value));
  window.addEventListener('beforeinstallprompt',e=>{e.preventDefault();installPrompt=e;});$('#installApp').addEventListener('click',()=>{if(installPrompt){installPrompt.prompt();installPrompt=null;}else notice('브라우저 메뉴에서 ‘홈 화면에 추가’를 선택해 주세요.');});
  if('serviceWorker'in navigator)navigator.serviceWorker.register('/sw.js').catch(()=>{});
  boot();
})();
