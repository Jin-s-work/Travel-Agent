/* Filter dialog transactions and accessibility; uses the real production functions. */
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('web/js/foundation.js','utf8');
function load(names,globals={}){const context=vm.createContext(globals);for(const name of names){const start=source.search(new RegExp('^  (?:async )?function '+name+'\\(','m'));assert(start>=0,name);const boundary=/\n  (?:async )?function |\n  (?:const|let) |\n  \$\(/g;boundary.lastIndex=start+3;const end=boundary.exec(source)?.index??source.length;vm.runInContext(source.slice(start,end),context);}return context;}
function node(tag='div',cls='',text=''){
  const n={tag,children:[],attrs:{},dataset:{},className:cls,textContent:text,events:{},hidden:false,value:'',checked:false,disabled:false,
    append(...children){for(const child of children){child.parentElement=this;this.children.push(child);}},
    replaceChildren(...children){this.children=[];this.append(...children);},
    setAttribute(key,value){this.attrs[key]=String(value);},
    addEventListener(name,handler){(this.events[name]??=[]).push(handler);},
    fire(name,patch={}){const event={target:this,preventDefault(){this.defaultPrevented=true;},...patch};for(const fn of this.events[name]||[])fn(event);return event;},
    closest(selector){let current=this;while(current){if(selector.startsWith('.')&&current.className.split(' ').includes(selector.slice(1)))return current;current=current.parentElement;}return null;},
    focus(){this.focused=true;},close(){this.open=false;},
  };n.classList={add(...classes){n.className=[...new Set([...n.className.split(' '),...classes])].join(' ');},remove(...classes){n.className=n.className.split(' ').filter(value=>!classes.includes(value)).join(' ');}};return n;
}
function descendants(root){return root.children.flatMap(child=>[child,...descendants(child)]);}
function setup(){
  const nodes=new Map(),fields=new Map(),forms=[],errors=[],requests=[];const body=node(),dialog=node('dialog');dialog.open=true;
  nodes.set('#dialogBody',body);nodes.set('#dialog',dialog);nodes.set('#dialogTitle',node());
  const initial={strict:false,ratingEnabled:false,minRating:4.2,minCount:200,limit:6};
  for(const [id,value] of [['#reviewStrictFilter',false],['#ratingFilterEnabled',false],['#recommendationMinRating','4.2'],['#recommendationMinCount','200'],['#recommendationLimit','6']]){const input=node('input');typeof value==='boolean'?input.checked=value:input.value=value;nodes.set(id,input);}
  const conditions={city:'tokyo',visit:{date:'2026-11-07',timezone:'Asia/Tokyo',local_time:null},party:{adults:2,children:[],children_status:'none'},categories:['restaurant','cafe'],recommendation_types:['local_discovery','landmark'],origin:{label:'숙소 A',latitude:35.1,longitude:139.2,place_id:'stay-a'},preferred:{tags:['quiet'],dietary:[]},required:{dietary:[],accessibility:[]},radius_m:1000};
  const stays=[{city:'tokyo',stop_id:'stop-a',start_date:'2026-11-06',end_date:'2026-11-08',timezone:'Asia/Tokyo'},{city:'barcelona',stop_id:'stop-b',start_date:'2026-11-09',end_date:'2026-11-11',timezone:'Europe/Madrid'}];
  const state={epoch:1,trip:{id:'trip-a',version:2,title:'여행',start_date:'2026-11-06',end_date:'2026-11-11',party:structuredClone(conditions.party)},discovery:{conditions:{version:3,conditions:structuredClone(conditions),stay_options:stays},draft:{conditions:structuredClone(conditions),overrides:{},stop_id:'stop-a'},dirty:false},recommendations:{optionsDirty:false}};
  let changed=0;
  const context=load(['recommendationFilterValues','recommendationFilterSummary','commitRecommendationFilters','discoveryFilterTabs','discoveryFilterChoice','sparseChanges','mergeDraft','discoveryStayChoice','discoveryConditionsForm','closeDialog'],{
    state,$:id=>nodes.get(id),make:node,structuredClone,window:{},dialogBusy:false,dialogReturnFocus:null,
    button:(label,action,cls)=>{const b=node('button',cls,label);b.addEventListener('click',action);b.type='button';return b;},
    openDialog:(_title,build)=>build(body),
    ensureDiscoveryDraft:()=>state.discovery.draft,
    formBase:(host,_label,handler)=>{const form=node('form'),grid=node('div','form-grid');form.append(grid);host.append(form);forms.push({form,grid,handler});return {form,grid};},
    field:(grid,name,_label,value='',options={})=>{const wrap=node('div','field'+(options.wide?' wide':'')),input=node('input');input.value=value==null?'':String(value);input.type=options.type||'text';input.name=name;input.dataset.field=name;wrap.append(input);grid.append(wrap);fields.set(name,input);return input;},
    knownDestination:value=>({timezone:value==='tokyo'?'Asia/Tokyo':'Europe/Madrid'}),
    destinationCatalog:[{id:'tokyo',name_ko:'도쿄',timezone:'Asia/Tokyo',currency:'JPY'},{id:'barcelona',name_ko:'바르셀로나',timezone:'Europe/Madrid',currency:'EUR'}],
    discoveryCityNames:{tokyo:'도쿄',barcelona:'바르셀로나'},
    commaItems:value=>value.split(',').map(value=>value.trim()).filter(Boolean),
    localError:(_form,field,message)=>{errors.push({field,message});return false;},
    draftChanged:()=>{state.discovery.dirty=true;changed++;},renderRecommendationResults(){},
    applyRecommendations:async()=>requests.push(structuredClone({draft:state.discovery.draft,filters:context.recommendationFilterValues()})),
  });
  const selectRadio=(name,value)=>{for(const input of descendants(body).filter(n=>n.name===name))input.checked=input.value===value;const radio=descendants(body).find(n=>n.name===name&&n.value===value);radio.closest('.filter-choice-group').fire('change',{target:radio});};
  return {context,state,initial,nodes,fields,forms,body,errors,requests,selectRadio,changed:()=>changed};
}
test('editing and cancelling filters preserves all committed values and the displayed result',()=>{
  const t=setup(),before=JSON.stringify(t.state.discovery.draft);t.state.recommendations.displayed={run_id:'existing'};t.context.discoveryConditionsForm();
  t.fields.get('party.adults').value='4';t.selectRadio('review_language_filter','required');t.selectRadio('rating_filter','required');t.fields.get('rating_filter.min_rating').value='4.8';t.forms[0].form.fire('input');
  assert.equal(JSON.stringify(t.context.recommendationFilterValues()),JSON.stringify(t.initial));assert.equal(JSON.stringify(t.state.discovery.draft),before);assert.equal(t.requests.length,0);
  t.context.closeDialog();assert.equal(JSON.stringify(t.state.discovery.draft),before);assert.equal(t.state.recommendations.conditionsDraft,false);assert.equal(t.state.recommendations.displayed.run_id,'existing');assert.equal(t.nodes.get('#dialog').open,false);
});
test('explicit filter apply includes strict choices once and preserves independent category and viewpoint choices',async()=>{
  const t=setup();t.context.discoveryConditionsForm();t.fields.get('party.adults').value='4';t.selectRadio('review_language_filter','required');t.selectRadio('rating_filter','required');t.fields.get('rating_filter.min_rating').value='4.5';t.fields.get('rating_filter.min_count').value='350';t.fields.get('limit').value='3';await t.forms[0].handler();
  assert.equal(t.requests.length,1);assert.deepEqual(t.requests[0].filters,{strict:true,ratingEnabled:true,minRating:4.5,minCount:350,limit:3});assert.deepEqual(t.requests[0].draft.conditions.categories,['restaurant','cafe']);assert.deepEqual(t.requests[0].draft.conditions.recommendation_types,['local_discovery','landmark']);assert.equal(t.requests[0].draft.conditions.party.adults,4);assert.equal(t.state.discovery.conditions.conditions.party.adults,2);assert.equal(t.state.recommendations.filterRevision,1);
});
test('a changed trip version does not apply old dialog edits or silently close the form',async()=>{
  const t=setup();t.context.discoveryConditionsForm();t.fields.get('party.adults').value='5';t.selectRadio('review_language_filter','required');t.state.trip.version++;
  await assert.rejects(t.forms[0].handler(),error=>error.status===409);assert.equal(t.requests.length,0);assert.equal(t.fields.get('party.adults').value,'5');assert.equal(t.context.recommendationFilterValues().strict,false);assert.equal(t.state.discovery.draft.conditions.party.adults,2);assert.equal(t.nodes.get('#dialog').open,true);
});
test('invalid budgets preserve the edited inputs and do not execute or relax filters',async()=>{
  const t=setup();t.context.discoveryConditionsForm();t.fields.get('budget.amount_min').value='200';t.fields.get('budget.amount_max').value='100';t.selectRadio('review_language_filter','required');await t.forms[0].handler();
  assert.equal(t.requests.length,0);assert.equal(t.errors[0].field,'budget.amount_max');assert.equal(t.fields.get('budget.amount_max').value,'100');assert.equal(t.context.recommendationFilterValues().strict,false);
});
test('switching city clears the old origin and chooses a date in the new city stay',async()=>{
  const t=setup();t.context.discoveryConditionsForm();t.fields.get('city').value='barcelona';t.fields.get('city').fire('change');await t.forms[0].handler();
  assert.equal(t.requests[0].draft.conditions.visit.date,'2026-11-09');assert.equal(t.requests[0].draft.conditions.visit.timezone,'Europe/Madrid');assert.equal(t.requests[0].draft.conditions.origin,null);assert.equal(t.requests[0].draft.stop_id,'stop-b');assert.equal(t.requests[0].draft.conditions.origin_selection.kind,'automatic');
});
test('renaming an origin removes stale coordinates instead of reusing another place location',async()=>{
  const t=setup();t.context.discoveryConditionsForm();t.fields.get('origin.label').value='숙소 B';t.fields.get('origin.label').fire('input');await t.forms[0].handler();assert.equal(t.requests[0].draft.conditions.origin.latitude,null);assert.equal(t.requests[0].draft.conditions.origin.longitude,null);assert.equal(t.requests[0].draft.conditions.origin.place_id,null);assert.equal(t.requests[0].draft.conditions.origin_selection.kind,'manual');
});
test('tabs support arrow, Home and End navigation with one focus target and matching panels',()=>{
  const t=setup();t.context.discoveryConditionsForm();const tabs=descendants(t.body).filter(n=>n.attrs.role==='tab'),panels=descendants(t.body).filter(n=>n.attrs.role==='tabpanel');
  assert.equal(tabs.length,4);assert.equal(tabs[0].attrs['aria-selected'],'true');tabs[0].fire('keydown',{key:'ArrowRight'});assert.equal(tabs[1].attrs['aria-selected'],'true');assert.equal(tabs[1].focused,true);assert.equal(panels[1].hidden,false);assert.equal(panels[0].hidden,true);tabs[1].fire('keydown',{key:'End'});assert.equal(tabs[3].tabIndex,0);tabs[3].fire('keydown',{key:'Home'});assert.equal(tabs[0].tabIndex,0);assert.equal(tabs.filter(n=>n.tabIndex===0).length,1);
});
test('validation reveals the first invalid panel rather than moving focus to the last hidden error',()=>{
  const t=setup();t.context.discoveryConditionsForm();const form=t.forms[0].form,panels=descendants(t.body).filter(n=>n.attrs.role==='tabpanel');assert.equal(form.noValidate,true);
  form.fire('submit');form.fire('invalid',{target:t.fields.get('budget.amount_min')});assert.equal(panels[1].hidden,false);const later=form.fire('invalid',{target:t.fields.get('rating_filter.min_count')});assert.equal(later.defaultPrevented,true);assert.equal(panels[1].hidden,false);form.fire('submit');form.fire('invalid',{target:t.fields.get('rating_filter.min_count')});assert.equal(panels[3].hidden,false);
});
test('filter summary states the selected constraints without switch jargon or inferred review percentages',()=>{
  const t=setup();assert.match(t.context.recommendationFilterSummary(),/리뷰 언어 제한 없음.*평점 제한 없음.*최대 6곳/);t.context.commitRecommendationFilters({strict:true,ratingEnabled:true,minRating:4.5,minCount:350,limit:3});assert.match(t.context.recommendationFilterSummary(),/검증된 리뷰 언어만.*4.5점.*350개.*최대 3곳/);assert.doesNotMatch(t.context.recommendationFilterSummary(),/ON|OFF|0%/);
});
