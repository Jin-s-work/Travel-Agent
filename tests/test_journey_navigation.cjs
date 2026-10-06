/* Calendar navigation must use civil dates, retain focus and keep the full list accessible. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/js/foundation.js','utf8');
function load(names, globals={}) {
  const context = vm.createContext(globals);
  for (const name of names) {
    const start = source.search(new RegExp('^  function '+name+'\\(','m'));
    assert.ok(start >= 0);
    const next = source.indexOf('\n  function ',start+3);
    vm.runInContext(source.slice(start,next),context);
  }
  return context;
}
const calendar = load(['calendarDate','journeyDays','dayChoice']);
test('civil dates retain every day across DST, leap day and year boundaries',()=>{
  assert.equal(Array.from(calendar.journeyDays('2026-03-28','2026-03-30')).join(','),'2026-03-28,2026-03-29,2026-03-30');
  assert.equal(Array.from(calendar.journeyDays('2028-02-28','2028-03-01')).join(','),'2028-02-28,2028-02-29,2028-03-01');
  assert.equal(Array.from(calendar.journeyDays('2026-12-31','2027-01-01')).join(','),'2026-12-31,2027-01-01');
  assert.equal(calendar.dayChoice('2026-11-07',1,8,'건').bottom,'토 · 8건');
});
test('invalid, reversed and long date ranges use the unrestricted date filter instead of truncating',()=>{
  for (const date of ['2026-02-29','2026-04-31','not-a-date','2026-1-01','']) assert.equal(calendar.calendarDate(date),null);
  assert.equal(calendar.journeyDays('2026-11-02','2026-11-01').length,0);
  assert.equal(calendar.journeyDays('2026-11-01','2026-12-02').length,0);
  assert.equal(calendar.journeyDays('2026-11-01','2026-12-01').length,31);
});
function node() { return {dataset:{},children:[],attrs:{},append(...children){this.children.push(...children);},replaceChildren(){this.children=[];},setAttribute(key,value){this.attrs[key]=value;}}; }
test('changing selected day preserves the same button nodes and reports pressed state',()=>{
  const ctx=load(['renderDayStrip'],{make:()=>node(),button:(_label,action)=>Object.assign(node(),{action})});
  const host=node(),choices=[{value:'',label:'all'},{value:'2026-11-07',label:'day 2'}];
  let selected;
  ctx.renderDayStrip(host,choices,'',value=>{selected=value;});
  const focused=host.children[1];focused.action();
  ctx.renderDayStrip(host,choices,selected,()=>{});
  assert.equal(host.children[1],focused);
  assert.equal(focused.attrs['aria-pressed'],'true');
  assert.equal(host.children[0].attrs['aria-pressed'],'false');
});
test('day counts include all 8 reservations, manual entries and multi-day stays',()=>{
  const ctx=load(['bookingOnDate']);
  const bookings=Array.from({length:8},(_,index)=>({date:'2026-11-07',document_id:index?'mail':null}));
  bookings.push({kind:'숙소',date:'2026-11-06',date_end:'2026-11-09'});
  assert.equal(bookings.filter(b=>ctx.bookingOnDate(b,'2026-11-07')).length,9);
  assert.equal(bookings.filter(b=>ctx.bookingOnDate(b,'2026-11-08')).length,1);
});
test('toast time pauses in a hidden tab and starts a fresh duration for a replacement',()=>{
  let now=1000, callback, delay;
  const notice={hidden:true,textContent:''}, listeners={};
  const doc={hidden:false,addEventListener:(event,fn)=>{listeners[event]=fn;}};
  const context=vm.createContext({document:doc,$:()=>notice,Date:{now:()=>now},setTimeout:(fn,ms)=>{callback=fn;delay=ms;return 1;},clearTimeout:()=>{callback=null;}});
  const start=source.indexOf('  let toastRemaining'),end=source.indexOf("  document.addEventListener('pointerdown'",start);
  vm.runInContext('let toastTimer;\n'+source.slice(start,end),context);
  context.notice('saved'); assert.equal(delay,5500);
  now+=1500; doc.hidden=true; listeners.visibilitychange(); assert.equal(callback,null);
  now+=60000; doc.hidden=false; listeners.visibilitychange(); assert.equal(delay,4000); assert.equal(notice.hidden,false);
  context.notice('new'); assert.equal(delay,5500); assert.equal(notice.textContent,'new');
  callback(); assert.equal(notice.hidden,true);
});
test('timeline shortens same-day clocks but keeps dates across midnight and flight timezones',()=>{
  const ctx=load(['itineraryLocal','itineraryTimeRange']);
  const base={local_start:'2026-11-06T10:00',local_end:'2026-11-06T11:00',timezone:'Asia/Tokyo'};
  assert.equal(ctx.itineraryTimeRange(base),'10:00 → 11:00');
  assert.equal(ctx.itineraryTimeRange({...base,local_end:'2026-11-07T01:00'}),'2026-11-06 10:00 → 2026-11-07 01:00');
  assert.equal(ctx.itineraryTimeRange({...base,start_timezone:'Asia/Tokyo',end_timezone:'Europe/Madrid'}),'2026-11-06 10:00 → 2026-11-06 11:00');
  assert.equal(ctx.itineraryTimeRange({...base,local_end:null}),'2026-11-06 10:00 → 시각 미정');
});
