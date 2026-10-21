/* v6 stage 3: agent answer cards in the question screen (vm extraction, no browser). */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/js/foundation.js', 'utf8');

function load(names, globals = {}) {
  const context = vm.createContext({window: {}, ...globals});
  for (const name of names) {
    const pattern = new RegExp('^  (?:async )?function ' + name + '\\(', 'm');
    const start = pattern.exec(source)?.index;
    assert.notEqual(start, undefined, name + ' exists in real browser source');
    const next = /\n  (?:async )?function |\n  (?:const|let) |\n  \$\(/g;
    next.lastIndex = start + 3;
    const end = next.exec(source)?.index ?? source.length;
    vm.runInContext(source.slice(start, end), context);
  }
  return context;
}

class Node {
  constructor(tag, cls = '', text) {
    this.tag = tag; this.className = cls || ''; this.children = []; this.attributes = {}; this.listeners = [];
    this.disabled = false; this.textContent = text == null ? '' : String(text);
  }
  append(...nodes) { this.children.push(...nodes); }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  addEventListener(type, fn) { if (type === 'click') this.listeners.push(fn); }
  click() { if (!this.disabled) return Promise.all(this.listeners.map(fn => fn())); }
  scrollIntoView() {}
  remove() {}
  all() { return [this, ...this.children.flatMap(child => child instanceof Node ? child.all() : [])]; }
  text() { return this.all().map(node => node.textContent).join(' '); }
  find(predicate) { return this.all().find(predicate); }
}
const make = (tag, cls, text) => new Node(tag, cls, text);
const button = (label, action, cls = 'secondary') => { const b = make('button', cls, label); b.addEventListener('click', action); return b; };
const buttonNamed = (root, label) => root.find(node => node.tag === 'button' && node.textContent === label);

const PREVIEW = {preview_id: 'prev-1', itinerary_id: 'it-1', base_version: 1, expires_at: '2026-11-01T00:10:00+00:00',
  can_apply: true, kind: 'edit', added: [], removed: [], changed: ['item-a'], affected_dates: ['2026-11-06'], conflicts: [],
  applied: false, apply_requires_user_click: true};
const DRAFT = {task_id: 'task-1', language: 'es', text: 'Hola, quisiera confirmar la reserva.', korean: '예약을 확인하고 싶습니다.',
  template_fallback: false, sent: false};

function world(api = async () => { throw new Error('api must not be called'); }) {
  const thread = make('div', 'thread');
  const state = {trip: {id: 'trip-a', title: 'A · 바르셀로나 테스트'}, epoch: 3, asking: false, history: [],
    itineraries: {loaded: true, displayed: {itinerary_id: 'it-1', version: 1, items: [{item_id: 'item-a', name: '합성 미술관'}]},
      active: {itinerary_id: 'it-1', version: 1}}};
  const calls = [], notices = [], rendered = [], tabs = [], reloads = [];
  const dom = new Map([['#thread', thread]]);
  const nodeFor = id => { if (!dom.has(id)) dom.set(id, make('div')); return dom.get(id); };
  const context = load(['agentWeatherUsed', 'agentPreviewName', 'agentPreviewCard', 'applyAgentPreview', 'agentDraftCard',
    'copyAgentDraft', 'agentAnswerExtras', 'bubble', 'submitQuestion', 'itineraryId'], {
    state, make, button, $: nodeFor, calls, notices,
    api: (...args) => { calls.push(args); return api(...args); },
    tripPath: trip => '/api/v2/trips/' + trip.id, reviewDate: value => value || '미확인',
    itineraryItemId: item => item.item_id || item.id, itineraryItemName: item => item.name || '항목',
    itineraryLocal: value => String(value).replace('T', ' ').slice(0, 16),
    renderItinerary: () => rendered.push(state.itineraries.displayed?.version),
    notice: message => notices.push(message), setTab: name => tabs.push(name), loadItineraries: async options => { reloads.push(options); },
    fail: error => { throw error; }, showError() {}, sourcesDialog() {}, loadJobs: async () => {},
    navigator: {clipboard: {writeText: async text => notices.push('copied:' + text)}},
  });
  return {context, state, calls, notices, rendered, tabs, reloads, thread, dom};
}

async function ask(w, response) {
  w.dom.set('#question', Object.assign(make('textarea'), {value: '일정에서 첫 장소를 늦추는 안을 보여줘'}));
  const queue = [response];
  const original = w.context.api;
  w.context.api = (...args) => { w.calls.push(args); return Promise.resolve(queue.shift()); };
  const ok = await w.context.submitQuestion();
  w.context.api = original;
  return ok;
}

test('agent answer shows checked scope, sources, preview card and draft card without extra API calls', async () => {
  const w = world();
  const ok = await ask(w, {answer: '미리보기를 만들었어요.', checked_scope: '예약 3건 · 저장한 곳 5곳 확인',
    sources: [{booking_id: 'b1'}, {booking_id: 'b2'}], tools_used: ['trip_agent', 'get_itinerary', 'preview_itinerary_change'],
    previews: [PREVIEW], drafts: [DRAFT]});
  assert.equal(ok, true);
  assert.equal(w.calls.length, 1, 'only the ask request');
  assert.equal(w.calls[0][0], '/api/v2/trips/trip-a/ask');
  const [user, answer, cards] = w.thread.children.filter(node => node.className !== 'bubble assistant pending');
  assert.equal(user.className, 'bubble user');
  const scope = answer.find(node => node.className === 'agent-scope');
  assert.equal(scope.textContent, '예약 3건 · 저장한 곳 5곳 확인');
  assert.ok(buttonNamed(answer, '근거 2건 보기'));
  assert.equal(answer.find(node => node.className === 'agent-attribution'), undefined, 'no weather credit without weather');
  assert.equal(cards.className, 'agent-cards');
  const [preview, draft] = cards.children;
  assert.match(preview.text(), /아직 적용하지 않았습니다/);
  assert.match(preview.text(), /변경 예정 1개/);
  assert.match(preview.text(), /합성 미술관/);
  assert.equal(buttonNamed(preview, '검증한 변경 적용').disabled, false);
  assert.equal(draft.attributes['aria-label'], '문의 초안(보내지 않음)');
  assert.match(draft.text(), /Hola, quisiera confirmar la reserva\./);
  assert.match(draft.text(), /보내지 않습니다/);
  assert.equal(w.calls.length, 1, 'rendering cards never calls the API');
});

test('apply sends only the existing PATCH with preview_id and base_version, once', async () => {
  let resolve;
  const w = world(() => new Promise(r => { resolve = r; }));
  const card = w.context.agentPreviewCard({...PREVIEW}, w.state.trip, w.state.epoch);
  assert.equal(w.calls.length, 0, 'no API call before the user clicks');
  const apply = buttonNamed(card, '검증한 변경 적용');
  const first = apply.click();
  await apply.click();  // disabled while pending: no second request
  assert.equal(w.calls.length, 1);
  const [path, options] = w.calls[0];
  assert.equal(path, '/api/v2/trips/trip-a/itineraries/it-1');
  assert.equal(options.method, 'PATCH');
  assert.deepEqual(JSON.parse(JSON.stringify(options.body)), {preview_id: 'prev-1', expected_version: 1});
  resolve({version: 2, items: []});
  await first;
  assert.deepEqual(w.rendered, [2]);
  assert.equal(w.state.itineraries.active.version, 2);
  assert.equal(apply.disabled, true);
  assert.equal(apply.textContent, '적용함');
  assert.match(card.text(), /새 일정 버전\(2\)/);
  assert.deepEqual(w.reloads, []);
});

test('applying a preview of another itinerary keeps the viewed one and reloads it with history', async () => {
  const w = world(async () => ({itinerary_id: 'it-2', version: 5, items: []}));
  const viewed = w.state.itineraries.displayed;
  const card = w.context.agentPreviewCard({...PREVIEW, itinerary_id: 'it-2', base_version: 4}, w.state.trip, w.state.epoch);
  await buttonNamed(card, '검증한 변경 적용').click();
  assert.equal(w.calls[0][0], '/api/v2/trips/trip-a/itineraries/it-2');
  assert.equal(w.state.itineraries.displayed, viewed, 'the viewed itinerary is not silently replaced');
  assert.equal(w.state.itineraries.active.itinerary_id, 'it-1');
  assert.deepEqual(w.rendered, []);
  assert.deepEqual(JSON.parse(JSON.stringify(w.reloads)), [{id: 'it-1'}]);
  assert.match(card.text(), /새 일정 버전\(5\)/);
});

test('409 keeps the preview unapplied and points to the latest itinerary', async () => {
  const w = world(async () => { const e = new Error('version'); e.status = 409; e.code = 'VERSION_CONFLICT'; throw e; });
  const card = w.context.agentPreviewCard({...PREVIEW}, w.state.trip, w.state.epoch);
  await buttonNamed(card, '검증한 변경 적용').click();
  assert.equal(w.calls.length, 1);
  assert.equal(buttonNamed(card, '검증한 변경 적용').disabled, true);
  assert.match(card.text(), /최신 일정을 확인한 뒤 다시 물어봐 주세요/);
  await buttonNamed(card, '최신 일정 확인').click();
  assert.deepEqual(w.tabs, ['itinerary']);
  assert.deepEqual(w.rendered, []);
});

test('a non-applicable preview, a stale trip and a plan B card never call the API', async () => {
  const w = world();
  const blocked = w.context.agentPreviewCard({...PREVIEW, can_apply: false, conflicts: ['OVERLAP']}, w.state.trip, w.state.epoch);
  assert.equal(buttonNamed(blocked, '검증한 변경 적용').disabled, true);
  assert.match(blocked.text(), /충돌 1건/);
  await buttonNamed(blocked, '검증한 변경 적용').click();
  const stale = w.context.agentPreviewCard({...PREVIEW}, w.state.trip, w.state.epoch - 1);
  await buttonNamed(stale, '검증한 변경 적용').click();
  const planB = w.context.agentPreviewCard({...PREVIEW, kind: 'plan_b', name: '합성 카페', local_start: '2026-11-06T15:00',
    indoor: {status: 'indoor_likely', basis: 'category_default'}}, w.state.trip, w.state.epoch);
  assert.match(planB.text(), /대체안 미리보기 · 합성 카페/);
  assert.doesNotMatch(planB.text(), /실내/, 'indoor is shown only for a rain reason, as in the alternatives screen');
  assert.match(planB.text(), /대체할 원래 항목은 이 카드에 없습니다/);
  assert.match(planB.text(), /대체안 비교에서 확인한 뒤 적용하세요/);
  const rain = w.context.agentPreviewCard({...PREVIEW, kind: 'plan_b', name: '합성 카페', local_start: '2026-11-06T15:00',
    reason: 'rain', before: {name: '합성 공원', local_start: '2026-11-06T15:00'}, unresolved: ['PRICE_UNKNOWN'],
    indoor: {status: 'indoor_likely', basis: 'category_default'}}, w.state.trip, w.state.epoch);
  assert.match(rain.text(), /2026-11-06 15:00 합성 공원 → 2026-11-06 15:00 합성 카페/);
  assert.match(rain.text(), /실내로 추정\(미확인\)/);
  assert.match(rain.text(), /미확인 조건 1건/);
  assert.equal(w.calls.length, 0);
});

test('weather credit links to Open-Meteo only when weather data was actually received; copy never sends', async () => {
  const w = world();
  for (const res of [{tools_used: ['trip_agent', 'get_weather']},
    {tools_used: ['trip_agent', 'get_weather'], weather: {status: 'unknown', reason_codes: ['WEATHER_DISABLED']}},
    {weather: {status: 'unknown', provider: 'open_meteo'}}, {weather: {status: 'ok', provider: 'other'}}]) {
    const none = make('div', 'bubble assistant');
    w.context.agentAnswerExtras(none, {previews: [], drafts: [], ...res}, w.state.trip);
    assert.equal(none.find(node => node.tag === 'a'), undefined, JSON.stringify(res));
  }
  const viaSource = make('div', 'bubble assistant');
  w.context.agentAnswerExtras(viaSource, {sources: [{provider: 'open_meteo'}], previews: [], drafts: []}, w.state.trip);
  assert.equal(viaSource.find(node => node.tag === 'a').textContent, 'Weather data by Open-Meteo.com');
  const answer = make('div', 'bubble assistant');
  w.context.agentAnswerExtras(answer, {tools_used: ['trip_agent', 'get_weather'], weather: {status: 'ok', provider: 'open_meteo'},
    previews: [], drafts: [DRAFT]}, w.state.trip);
  const link = answer.find(node => node.tag === 'a');
  assert.equal(link.textContent, 'Weather data by Open-Meteo.com');
  assert.equal(link.href, 'https://open-meteo.com/');
  assert.equal(link.rel, 'noopener noreferrer');
  // CC BY 4.0 asks for the licence next to the data, not only the source link.
  const license = answer.find(node => node.tag === 'a' && node.textContent === 'CC BY 4.0');
  assert.equal(license.href, 'https://creativecommons.org/licenses/by/4.0/');
  const cards = w.thread.children.at(-1);
  await buttonNamed(cards, '초안 복사').click();
  assert.deepEqual(w.notices.slice(0, 1), ['copied:' + DRAFT.text]);
  assert.equal(w.calls.length, 0);
  const plain = make('div', 'bubble assistant');
  assert.equal(w.context.agentAnswerExtras(plain, {answer: 'x', sources: []}, w.state.trip), null);
  assert.equal(plain.children.length, 0);
});
