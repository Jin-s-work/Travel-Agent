// Stage 4 D1/D2 UI: explicit feedback effect and the '적용 중' indicator. No network.
const test=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const discovery=require('../web/js/discovery-experience.js');

test('applied indicator shows only explicit choices frozen into the run',()=>{
  assert.equal(discovery.appliedFeedback(undefined),null);
  assert.equal(discovery.appliedFeedback({soft_avoid_place_ids:[],feedback_inputs:{soft_avoid_places:0,excluded_places:0,carried_from_other_trips:0},saved_taste:{applied:false,added_tags:[]}}),null);
  const copy=discovery.appliedFeedback({feedback_inputs:{soft_avoid_places:2,excluded_places:1,carried_from_other_trips:1},saved_taste:{applied:true,added_tags:['ramen','coffee']}});
  assert.match(copy,/^적용 중: 덜 보기 2곳 · 제외 1곳/);assert.match(copy,/다음 여행에도/);assert.match(copy,/저장한 곳 기반 취향 ramen · coffee/);assert.match(copy,/철회/);
  assert.match(discovery.appliedFeedback({soft_avoid_place_ids:['a']}),/덜 보기 1곳/); // runs saved before stage 4
  assert.equal(discovery.appliedFeedback({feedback_inputs:{soft_avoid_places:0,excluded_places:0},saved_taste:{applied:false,added_tags:['x']}}),null);
});

async function form(kind='preference'){
  const calls=[],window={},forms=[],body=element('div');
  function element(tag,cls='',text=''){return {tag,className:cls,textContent:text,children:[],events:{},value:'',append(...kids){this.children.push(...kids);},addEventListener(key,fn){this.events[key]=fn;},querySelectorAll(){return [];}};}
  const context=vm.createContext({window,document:{visibilityState:'visible',querySelector:()=>null,addEventListener(){}},navigator:{onLine:true},crypto:require('node:crypto').webcrypto,Date,Map,Set,setTimeout,clearTimeout});
  vm.runInContext(fs.readFileSync('web/js/product.js','utf8'),context);const tools=window.ProductTools;
  tools.init({state:{epoch:1,trip:{id:'t'},tab:'explore'},api:async(path,options)=>{if(path==='/product-preferences')return {analytics_enabled:false};calls.push({path,...options});return {};},make:element,button:(label,fn)=>{const b=element('button','',label);b.events.click=fn;return b;},openDialog:(_t,render)=>render(body),formBase:(host,_t,handler)=>{const grid=element('div');host.append(grid);forms.push(handler);return {grid};},field:(host,name,_label,value,opts={})=>{const n=element(opts.select?'select':'input');n.name=name;n.value=value;n.options=opts.select;host.append(n);return n;},closeDialog(){},notice(){}});
  await tools.bind();tools.feedbackButton(body,'p',{kind});body.children[0].events.click();
  const all=node=>[node,...node.children.flatMap(all)];
  const reflect=all(body).find(n=>n.tag==='label'&&n.children.some(c=>c.textContent.includes('취향 추천')))?.children[0];
  const effect=all(body).find(n=>n.name==='preference_effect');
  return {calls,forms,reflect,effect,all,body};
}

test('effect defaults to this-trip less and is ignored unless reflection is chosen',async()=>{
  const f=await form();assert.equal(f.effect.value,'less:this_trip');assert.equal(f.reflect.checked,false);
  assert.equal(JSON.stringify(f.effect.options.map(o=>o[0])),JSON.stringify(['less:this_trip','exclude:this_trip','less:future','exclude:future']));
  f.effect.value='exclude:future';await f.forms[0]();
  assert.equal(f.calls[0].body.reflect_preference,false);assert.equal(f.calls[0].body.preference_action,'less');assert.equal(f.calls[0].body.preference_scope,'this_trip');
});

test('explicit reflection sends the chosen action and scope',async()=>{
  const f=await form();f.reflect.checked=true;f.effect.value='exclude:future';await f.forms[0]();
  assert.equal(f.calls[0].body.reflect_preference,true);assert.equal(f.calls[0].body.preference_action,'exclude');assert.equal(f.calls[0].body.preference_scope,'future');
  assert(f.all(f.body).some(n=>n.textContent.includes('기록만 하면 추천은 바뀌지 않아요')));
});

test('visit records carry no preference effect fields',async()=>{
  const f=await form('visit');assert.equal(f.effect,undefined);
});

test('applied indicator counts only choices that meet this run candidates',()=>{
  const snapshot={feedback_inputs:{soft_avoid_places:1,excluded_places:1,carried_from_other_trips:1},saved_taste:{applied:false,added_tags:[]}};
  // A future-scoped Tokyo choice on a Barcelona run: recorded, but not shown as applied.
  assert.equal(discovery.appliedFeedback(snapshot,{soft_avoid_in_candidates:0,excluded_in_candidates:0,soft_avoid_total:1,excluded_total:1}),null);
  const copy=discovery.appliedFeedback(snapshot,{soft_avoid_in_candidates:1,excluded_in_candidates:0,soft_avoid_total:1,excluded_total:1});
  assert.match(copy,/^적용 중: 덜 보기 1곳/);assert.doesNotMatch(copy,/제외 1곳/);assert.match(copy,/이번 후보에 없는 선택 1곳은 영향이 없어요/);
});

test('carried choice withdraws on the trip that holds it',async()=>{
  const calls=[],window={},body=element('div'),box=element('section');
  function element(tag,cls='',text=''){return {tag,className:cls,textContent:text,children:[],events:{},value:'',append(...kids){this.children.push(...kids);},addEventListener(key,fn){this.events[key]=fn;},querySelectorAll(){return [];}};}
  const context=vm.createContext({window,document:{visibilityState:'visible',querySelector:()=>null,addEventListener(){}},navigator:{onLine:true},crypto:require('node:crypto').webcrypto,Date,Map,Set,setTimeout,clearTimeout});
  vm.runInContext(fs.readFileSync('web/js/product.js','utf8'),context);const tools=window.ProductTools;
  tools.init({state:{epoch:1,trip:{id:'current-trip'},tab:'explore'},api:async(path,options)=>{calls.push({path,...options});return {items:[]};},make:element,button:(label,fn)=>{const b=element('button','',label);b.events.click=fn;return b;},openDialog:(_t,render)=>render(body),closeDialog(){},fail(){},notice(){}});
  tools.renderCarried(box,[{id:'fb1',trip_id:'origin trip',version:3,place_name:'P',preference_action:'exclude',preference_scope:'future',reason_codes:[]}]);
  const all=node=>[node,...node.children.flatMap(all)];
  assert(all(box).some(n=>n.textContent.includes('이 여행 추천에도 적용 중')));
  all(box).find(n=>n.tag==='button'&&n.textContent==='철회').events.click();
  await all(body).find(n=>n.tag==='button'&&n.textContent==='선택 철회').events.click();
  assert.equal(calls[0].path,'/trips/origin%20trip/feedback/fb1');assert.equal(calls[0].method,'DELETE');assert.equal(calls[0].body.expected_version,3);
});
