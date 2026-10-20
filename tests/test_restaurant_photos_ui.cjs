/* Exercise the shipped gallery, including stale rights and independent image failures. */
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const crypto=require('node:crypto');
const source=fs.readFileSync('web/js/foundation.js','utf8');
class Node {
  constructor(tag='',cls='',text=''){this.tag=tag;this.className=cls;this.textContent=text;this.children=[];this.dataset={};this.attributes={};this.events={};this.hidden=false;this.open=false;this.parentNode=null;this.captures=[];this.classList={add:name=>{this.className+=' '+name;}};}
  append(...nodes){for(const node of nodes)node.parentNode=this;this.children.push(...nodes);}
  replaceChildren(...nodes){for(const node of this.children)node.parentNode=null;this.children=[];this.append(...nodes);}
  setAttribute(name,value){this.attributes[name]=String(value);}
  getAttribute(name){return this.attributes[name]??null;}
  addEventListener(name,fn){(this.events[name]??=[]).push(fn);}
  emit(name,event={}){for(const fn of this.events[name]||[])fn({target:this,...event});}
  closest(selector){const tags=selector.split(',');for(let node=this;node;node=node.parentNode)if(tags.includes(node.tag))return node;return null;}
  setPointerCapture(pointerId){this.captures.push(pointerId);}
  set src(value){this.attributes.src=value;}
  get src(){return this.attributes.src;}
}
const make=(tag,cls,text)=>new Node(tag,cls,text);
const button=(label,action,cls)=>{const n=make('button',cls,label);n.addEventListener('click',action);return n;};
function load(){const context=vm.createContext({URL,Date,Set,Number,make,button});for(const name of ['safeDiscoveryLink','placePhotoUrl','placePhotoItems','placePhotoDate','placePhotoGallery']){const start=new RegExp('^  function '+name+'\\(','m').exec(source)?.index;assert.notEqual(start,undefined,name);const next=/\n  (?:async )?function |\n  (?:const|let) |\n  \$\(/g;next.lastIndex=start+3;const end=next.exec(source)?.index??source.length;vm.runInContext(source.slice(start,end),context);}return context;}
const text=node=>[node.textContent,...node.children.map(text)].join(' ');
const all=node=>[node,...node.children.flatMap(all)];
const byClass=(node,cls)=>all(node).filter(n=>n.className.split(' ').includes(cls));
const photo=(patch={})=>({id:'photo-1',url:'https://thumb.wikimedia.org/wikipedia/commons/thumb/a/ab/Restaurant.jpg/960px-Restaurant.jpg',source_url:'https://commons.wikimedia.org/wiki/File:Restaurant.jpg',author:'Photographer',license:'CC BY-SA 4.0',license_url:'https://creativecommons.org/licenses/by-sa/4.0/',alt:'실제 식당 지점 외관',kind:'exterior',checked_at:'2026-10-06T08:00:00Z',expires_at:'2099-10-06T08:00:00Z',taken_at:'2009-03-21',width:960,height:720,...patch});
const other=()=>photo({id:'photo-2',url:'https://upload.wikimedia.org/wikipedia/commons/b/bc/Restaurant_interior.jpg',source_url:'https://commons.wikimedia.org/wiki/File:Restaurant_interior.jpg',author:'Second Photographer',alt:'실제 식당 지점 내부',kind:'interior',taken_at:'2011'});
const third=()=>photo({id:'photo-3',url:'https://upload.wikimedia.org/wikipedia/commons/c/cd/Restaurant_food.jpg',source_url:'https://commons.wikimedia.org/wiki/File:Restaurant_food.jpg',author:'Food Photographer',alt:'해당 지점에서 촬영한 요리',kind:'food',taken_at:'2020-06'});
const fourth=()=>photo({id:'photo-4',url:'https://upload.wikimedia.org/wikipedia/commons/d/de/Restaurant_table.jpg',source_url:'https://commons.wikimedia.org/wiki/File:Restaurant_table.jpg',kind:'other'});
const place=photos=>({name:'검토한 식당',native_name:'Actual restaurant',photos,photo_status:{state:'available'}});

test('photo URLs allow approved Commons raster hosts and license documents only',()=>{const c=load();for(const host of ['thumb.wikimedia.org','upload.wikimedia.org'])assert(c.placePhotoUrl(`https://${host}/wikipedia/commons/a/ab/Restaurant.jpg`,'image'));for(const url of ['javascript:alert(1)','data:image/png;base64,x','http://upload.wikimedia.org/wikipedia/commons/a/ab/R.jpg','https://upload.wikimedia.org.evil.test/wikipedia/commons/a/ab/R.jpg','https://secret@upload.wikimedia.org/wikipedia/commons/a/ab/R.jpg','https://upload.wikimedia.org/wikipedia/commons/a/ab/R.svg','https://upload.wikimedia.org/wikipedia/commons/a/ab/R.jpg?token=secret','https://127.0.0.1/R.jpg','https://thumb.wikimedia.org/other/R.jpg'])assert.equal(c.placePhotoUrl(url,'image'),null,url);assert.equal(c.placePhotoUrl('https://evil.test/wiki/File:R.jpg','source'),null);assert.equal(c.placePhotoUrl('https://creativecommons.org/other','license'),null);assert(c.placePhotoUrl('https://creativecommons.org/publicdomain/zero/1.0/','license'));});

test('revoked, expired, malformed attribution and duplicate photos cannot become card images',()=>{const c=load(),now=Date.parse('2026-10-06T09:00:00Z');assert.equal(c.placePhotoItems({...place([photo()]),photo_status:{state:'unavailable'}},now).length,0);assert.equal(c.placePhotoItems({...place([photo()]),synthetic:true},now).length,0);for(const patch of [{expires_at:'2026-10-06T08:59:59Z'},{checked_at:'invalid'},{author:''},{license_url:'javascript:alert(1)'},{alt:''},{kind:''},{kind:undefined},{kind:'menu'},{kind:'<script>'}])assert.equal(c.placePhotoItems(place([photo(patch)]),now).length,0);assert.equal(c.placePhotoItems(place([photo(),photo(),other(),third(),fourth()]),now).length,3);const allowed=c.placePhotoItems(place([photo(),other(),third(),fourth()]),now);assert.deepEqual(Array.from(allowed,photo=>photo.kind),['exterior','interior','food']);assert.equal(c.placePhotoItems(place([fourth()]),now)[0].kind,'other');});

test('two-photo gallery initially requests only its first image and keeps rights available in disclosure',()=>{const c=load(),gallery=c.placePhotoGallery(place([photo(),other()]));const images=byClass(gallery,'place-photo-image');assert.equal(images.length,2);assert.equal(images[0].src,photo().url);assert.equal(images[1].src,undefined);assert.equal(images[0].loading,'lazy');assert.equal(images[0].decoding,'async');assert.equal(images[0].referrerPolicy,'no-referrer');assert.equal(images[0].draggable,false);assert.equal(images[0].width,960);assert.equal(images[0].height,720);assert.match(images[0].alt,/2009\. 03\. 21 촬영/);const credits=byClass(gallery,'place-photo-credits')[0],disclosure=byClass(gallery,'place-photo-credit-details')[0];assert.equal(disclosure.open,false);assert.equal(disclosure.children[0].tag,'summary');assert.equal(disclosure.children[0].textContent,'사진 정보');assert.match(text(credits),/Photographer/);assert.match(text(credits),/CC BY-SA 4.0/);assert.match(text(credits),/현재 모습과 다를 수/);assert.match(text(credits),/일부 잘라 표시/);const links=all(credits).filter(n=>n.tag==='a');assert.equal(links.length,2);assert(links.every(n=>n.target==='_blank'&&n.rel==='noopener noreferrer'));assert.equal(byClass(gallery,'place-photo-counter')[0].attributes['aria-live'],'polite');});

test('navigation preserves controls and provides keyboard previous/next with current attribution',()=>{const c=load(),gallery=c.placePhotoGallery(place([photo(),other()])),next=byClass(gallery,'place-photo-next')[0],nav=byClass(gallery,'place-photo-navigation')[0],slides=byClass(gallery,'place-photo-slide');next.emit('click');assert.equal(slides[0].hidden,true);assert.equal(slides[1].hidden,false);assert.equal(byClass(gallery,'place-photo-image')[1].src,other().url);assert.match(text(byClass(gallery,'place-photo-credits')[0]),/Second Photographer · 2011 촬영/);assert.equal(byClass(gallery,'place-photo-counter')[0].textContent,'2 / 2');let prevented=false;nav.emit('keydown',{key:'ArrowLeft',preventDefault(){prevented=true;}});assert.equal(prevented,true);assert.equal(slides[0].hidden,false);assert.equal(byClass(gallery,'place-photo-next')[0],next);assert.match(next.attributes['aria-label'],/다음 사진/);});

test('first-image failure retains the second actual photo and hides unnecessary navigation',()=>{const c=load(),gallery=c.placePhotoGallery(place([photo(),other()])),images=byClass(gallery,'place-photo-image');images[0].emit('error');assert.equal(images[1].src,other().url);assert.equal(byClass(gallery,'place-photo-slide')[1].hidden,false);assert.equal(byClass(gallery,'place-photo-navigation')[0].hidden,true);assert.equal(byClass(gallery,'place-photo-counter')[0].textContent,'1 / 1');assert.equal(byClass(gallery,'place-photo-unavailable')[0].hidden,true);assert.match(text(byClass(gallery,'place-photo-credits')[0]),/Second Photographer/);});

test('second-image failure does not discard the earlier valid image or its attribution',()=>{const c=load(),gallery=c.placePhotoGallery(place([photo(),other()]));byClass(gallery,'place-photo-next')[0].emit('click');byClass(gallery,'place-photo-image')[1].emit('error');assert.equal(byClass(gallery,'place-photo-slide')[0].hidden,false);assert.equal(byClass(gallery,'place-photo-unavailable')[0].hidden,true);assert.match(text(byClass(gallery,'place-photo-credits')[0]),/Photographer · 2009/);});

test('all images failing keeps the stable frame and an honest source-link fallback',()=>{const c=load(),gallery=c.placePhotoGallery(place([photo(),other()])),frame=byClass(gallery,'place-photo-frame')[0];for(const image of byClass(gallery,'place-photo-image'))image.emit('error');assert.equal(byClass(gallery,'place-photo-frame')[0],frame);assert(byClass(gallery,'place-photo-slide').every(n=>n.hidden));const fallback=byClass(gallery,'place-photo-unavailable')[0];assert.equal(fallback.hidden,false);assert.match(text(fallback),/불러오지 못했/);assert.equal(all(fallback).find(n=>n.tag==='a').href,photo().source_url);assert.equal(byClass(gallery,'place-photo-counter')[0].hidden,true);});

test('unavailable photos stay compact without fake venue imagery or download requests',()=>{const c=load(),gallery=c.placePhotoGallery(place([]));assert.equal(byClass(gallery,'place-photo-gallery-empty').length,1);assert.equal(byClass(gallery,'place-photo-image').length,0);assert.equal(byClass(gallery,'place-photo-frame').length,0);assert.match(text(gallery),/사진은 지도에서 확인해 주세요/);assert.equal(c.placePhotoDate(photo({taken_at:null})),'촬영일 미확인');assert.doesNotMatch(c.placePhotoDate(photo({taken_at:null})),/2026/);});

test('photo text cannot inject HTML and checking a date never claims when a photo was taken',()=>{const c=load(),payload=photo({author:'<img src=x onerror=alert(1)>',alt:'<script>alert(1)</script>',taken_at:null}),gallery=c.placePhotoGallery(place([payload]));const nodes=all(gallery);assert.equal(nodes.filter(n=>n.tag==='img').length,1);assert.equal(nodes.filter(n=>n.tag==='script').length,0);assert.equal(nodes.some(n=>n.innerHTML!==undefined),false);assert.match(text(byClass(gallery,'place-photo-credits')[0]),/촬영일 미확인/);assert.doesNotMatch(text(gallery),/2026 촬영/);});

test('curated photo manifest uses metadata the real browser can display without provider calls',()=>{const c=load(),manifest=JSON.parse(fs.readFileSync('src/discovery/restaurant_photos.json','utf8'));let photos=0;for(const entry of manifest.places){const expected=entry.photos.filter(photo=>photo.enabled);if(!expected.length)continue;const clock=Date.parse(expected[0].checked_at)+60000;const actual=c.placePhotoItems(place(expected),clock);assert.equal(actual.length,expected.length,entry.external_id);assert(actual.length<=3);photos+=actual.length;}assert(photos>0);});

const current=gallery=>byClass(gallery,'place-photo-counter')[0].textContent;
const pointerEvent=(patch={})=>({pointerId:1,isPrimary:true,button:0,clientX:150,clientY:120,...patch});
function swipe(gallery,{dx=-80,dy=0,start={},end={}}={}){
  const frame=byClass(gallery,'place-photo-frame')[0];
  frame.emit('pointerdown',pointerEvent(start));
  frame.emit('pointerup',pointerEvent({clientX:150+dx,clientY:120+dy,...end}));
  return frame;
}

test('three-photo gallery lazily loads each view and updates type, caption and source together',()=>{
  const c=load(),gallery=c.placePhotoGallery(place([photo(),other(),third(),fourth()])),images=byClass(gallery,'place-photo-image');
  assert.equal(images.length,3);assert.equal(gallery.dataset.photoCount,'3');
  assert.deepEqual(images.map(n=>n.src),[photo().url,undefined,undefined]);
  assert.equal(current(gallery),'1 / 3');assert.equal(byClass(gallery,'place-photo-type')[0].textContent,'외관');
  const next=byClass(gallery,'place-photo-next')[0];next.emit('click');
  assert.deepEqual(images.map(n=>n.src),[photo().url,other().url,undefined]);
  assert.equal(byClass(gallery,'place-photo-type')[0].textContent,'실내');
  next.emit('click');assert.equal(images[2].src,third().url);assert.equal(current(gallery),'3 / 3');
  assert.equal(byClass(gallery,'place-photo-type')[0].textContent,'음식');
  assert.match(byClass(gallery,'place-photo-caption')[0].textContent,/2020\. 06 촬영/);
  const body=byClass(gallery,'place-photo-credit-body')[0];
  assert.match(text(body),/Food Photographer/);assert.equal(all(body).find(n=>n.tag==='a').href,third().source_url);
  next.emit('click');assert.equal(current(gallery),'1 / 3');
  assert.equal(byClass(gallery,'place-photo-type')[0].textContent,'외관');
});

test('credit disclosure keeps its open state and element identity through navigation and image failure',()=>{
  const c=load(),gallery=c.placePhotoGallery(place([photo(),other(),third()]));
  const details=byClass(gallery,'place-photo-credit-details')[0],summary=details.children[0];details.open=true;
  byClass(gallery,'place-photo-next')[0].emit('click');
  assert.equal(byClass(gallery,'place-photo-credit-details')[0],details);assert.equal(details.children[0],summary);assert.equal(details.open,true);
  assert.match(text(details),/Second Photographer/);assert.doesNotMatch(text(details),/Food Photographer/);
  byClass(gallery,'place-photo-image')[1].emit('error');
  assert.equal(details.open,true);assert.match(text(details),/Photographer · 2009/);
  details.open=false;byClass(gallery,'place-photo-next')[0].emit('click');
  assert.equal(details.open,false);assert.match(text(details),/Food Photographer/);
});

test('photo kind other stays generic, single view has no unnecessary navigation, detail reuses gallery',()=>{
  const c=load(),gallery=c.placePhotoGallery(place([fourth()]),'detail');
  assert.equal(byClass(gallery,'place-photo-gallery-detail').length,1);
  assert.equal(byClass(gallery,'place-photo-type')[0].textContent,'사진');
  assert.equal(byClass(gallery,'place-photo-navigation')[0].hidden,true);
  assert.equal(current(gallery),'1 / 1');
  swipe(gallery);assert.equal(current(gallery),'1 / 1');
});

test('primary horizontal pointer swipe advances once in each direction and captures the active pointer',()=>{
  const c=load(),gallery=c.placePhotoGallery(place([photo(),other(),third()]));
  const frame=swipe(gallery);assert.equal(current(gallery),'2 / 3');assert.deepEqual(frame.captures,[1]);
  swipe(gallery,{dx:90,dy:10});assert.equal(current(gallery),'1 / 3');
  swipe(gallery,{dx:45,dy:0});assert.equal(current(gallery),'3 / 3');
  swipe(gallery,{dx:-45,dy:0});assert.equal(current(gallery),'1 / 3');
  assert.equal(byClass(gallery,'place-photo-next').length,1);
});

test('vertical, diagonal and below-threshold gestures preserve the current photo',()=>{
  const c=load(),gallery=c.placePhotoGallery(place([photo(),other(),third()]));
  for(const delta of [{dx:0,dy:-100},{dx:50,dy:90},{dx:50,dy:40},{dx:44,dy:0},{dx:-44,dy:0}]){
    swipe(gallery,delta);assert.equal(current(gallery),'1 / 3',JSON.stringify(delta));
  }
  assert.equal(byClass(gallery,'place-photo-image')[1].src,undefined);
});

test('pointer cancellation and lost capture prevent a late release from changing the photo',()=>{
  for(const cancellation of ['pointercancel','lostpointercapture']){
    const c=load(),gallery=c.placePhotoGallery(place([photo(),other(),third()])),frame=byClass(gallery,'place-photo-frame')[0];
    frame.emit('pointerdown',pointerEvent());frame.emit(cancellation,pointerEvent());
    frame.emit('pointerup',pointerEvent({clientX:40}));assert.equal(current(gallery),'1 / 3');
    swipe(gallery);assert.equal(current(gallery),'2 / 3');
  }
});

test('secondary pointers and non-primary buttons cannot replace an active gesture',()=>{
  const c=load(),gallery=c.placePhotoGallery(place([photo(),other(),third()])),frame=byClass(gallery,'place-photo-frame')[0];
  swipe(gallery,{start:{isPrimary:false},end:{isPrimary:false}});assert.equal(current(gallery),'1 / 3');
  swipe(gallery,{start:{button:2},end:{button:2}});assert.equal(current(gallery),'1 / 3');
  assert.deepEqual(frame.captures,[]);
  frame.emit('pointerdown',pointerEvent());
  frame.emit('pointerdown',pointerEvent({pointerId:2,isPrimary:false,clientX:300}));
  frame.emit('pointerup',pointerEvent({pointerId:2,isPrimary:false,clientX:20}));
  assert.equal(current(gallery),'1 / 3');assert.deepEqual(frame.captures,[1]);
  frame.emit('pointerup',pointerEvent({clientX:40}));assert.equal(current(gallery),'2 / 3');
});

test('dragging a button, link or summary never also triggers the photo swipe',()=>{
  for(const tag of ['button','a','summary']){
    const c=load(),gallery=c.placePhotoGallery(place([photo(),other(),third()])),frame=byClass(gallery,'place-photo-frame')[0];
    const control=make(tag),inner=make('span');control.append(inner);
    swipe(gallery,{start:{target:inner},end:{target:inner}});assert.equal(current(gallery),'1 / 3',tag);assert.deepEqual(frame.captures,[]);
  }
  const c=load(),gallery=c.placePhotoGallery(place([photo(),other(),third()])),next=byClass(gallery,'place-photo-next')[0];
  swipe(gallery,{start:{target:next},end:{target:next}});assert.equal(current(gallery),'1 / 3');
  next.emit('click');assert.equal(current(gallery),'2 / 3');
});

test('failed views are skipped by swipe and all-failed gallery hides type and credit disclosure',()=>{
  const c=load(),gallery=c.placePhotoGallery(place([photo(),other(),third()])),images=byClass(gallery,'place-photo-image');
  images[1].emit('error');swipe(gallery);assert.equal(current(gallery),'2 / 2');assert.equal(images[2].src,third().url);
  assert.equal(byClass(gallery,'place-photo-type')[0].textContent,'음식');
  images[0].emit('error');images[2].emit('error');
  assert.equal(byClass(gallery,'place-photo-type')[0].hidden,true);
  assert.equal(byClass(gallery,'place-photo-credit-details')[0].hidden,true);
  assert.equal(byClass(gallery,'place-photo-unavailable')[0].hidden,false);
});

test('self-hosted font keeps upstream bytes and license with consistent Korean app name',()=>{
  const font=fs.readFileSync('web/fonts/PretendardVariable-v1.3.9.woff2');
  assert.equal(font.subarray(0,4).toString(),'wOF2');assert.equal(font.length,2057688);
  assert.equal(crypto.createHash('sha256').update(font).digest('hex'),'9599f12fd42fc0bce1cd50b47a0c022e108d7aa64dd0d1bb0ed44f3282d900b4');
  assert.match(fs.readFileSync('web/fonts/Pretendard-OFL.txt','utf8'),/SIL OPEN FONT LICENSE Version 1\.1/);
  const html=fs.readFileSync('web/index.html','utf8'),css=fs.readFileSync('web/css/design-system.css','utf8');
  assert.match(html,/<link rel="preload" href="\/fonts\/PretendardVariable-v1\.3\.9\.woff2" as="font" type="font\/woff2" crossorigin>/);
  assert.match(css,/font-family:"Pretendard Variable"/);assert.match(css,/font-display:swap/);
  assert.match(html,/<title>고잉 · 나의 여행<\/title>/);
  const manifest=JSON.parse(fs.readFileSync('web/manifest.webmanifest','utf8'));
  assert.equal(manifest.name,'고잉');assert.equal(manifest.short_name,'고잉');
});

 test('unavailable transport is not presented as an absent photo',()=>{const c=load();const failed=c.placePhotoGallery({...place([]),photo_status:{state:'unavailable',reason_codes:['PHOTO_PROVIDER_UNAVAILABLE']}},'detail');assert.match(text(failed),/응답을 받지 못했/);assert.doesNotMatch(text(failed),/등록된 사진이 없/);const missing=c.placePhotoGallery({...place([]),photo_status:{state:'unavailable',reason_codes:['NO_LINKED_PHOTO']}});assert.match(text(missing),/연결된 사진이 없어요/);});
