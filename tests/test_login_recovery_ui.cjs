const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('web/js/foundation.js','utf8');
function load(names,globals){const ctx=vm.createContext({URL,Set,Headers,AbortController,...globals});for(const name of names){const start=new RegExp('^  (?:async )?function '+name+'\\(', 'm').exec(source).index;const next=/\n  (?:async )?function |\n  (?:const|let) |\n  \$\(/g;next.lastIndex=start+3;const end=next.exec(source)?.index??source.length;vm.runInContext(source.slice(start,end),ctx);}return ctx;}
function fixture({session,tripError,sessionError,old}={}){
  const nodes=new Map(),$=key=>{if(!nodes.has(key))nodes.set(key,{hidden:true,disabled:false,textContent:''});return nodes.get(key);};
  const state={session:old,tab:'trip',trip:null},messages=[];let clears=0,loads=0;
  const c=load(['authScreen','boot'],{$,state,window:{scrollTo(){},TravelTools:{purge:async()=>{},bind:async()=>{}},ProductTools:{bind(){}}},showError:(node,text)=>{node.textContent=text;messages.push(text);},sessionCheck:false,loginFeedback:'고정된 로그인 오류 안내',api:async()=>{if(sessionError)throw sessionError;return session;},clearPrivate(){clears++;state.authRevision=(state.authRevision||0)+1;state.session=null;state.trip=null;$('#app').hidden=true;},armSessionExpiry:()=>true,loadDestinations:async()=>{},notice(){},navigationMemory:()=>({}),loadTrips:async()=>{loads++;if(tripError)throw tripError;state.trip={id:'trip'};},loadUsage:async()=>{},loadReviews:async()=>{},setTab(){}});
  return {c,state,$,messages,get clears(){return clears;},get loads(){return loads;}};
}
const session=()=>({authenticated:true,auth_configured:true,user:{id:'A',display_name:'Synthetic A'}});
test('authenticated trip read failure keeps login and retries only the workspace',async()=>{
  const f=fixture({session:session(),tripError:new Error('database busy')});await f.c.boot();
  assert.equal(f.state.session.authenticated,true);assert.equal(f.clears,0);assert.equal(f.$('#auth').hidden,true);assert.equal(f.$('#workspaceRecovery').hidden,false);assert.equal(f.$('#retryWorkspace').disabled,false);
  f.c.loadTrips=async()=>{f.state.trip={id:'trip'};};await f.c.boot();assert.equal(f.$('#workspaceRecovery').hidden,true);assert.equal(f.state.workspaceNeedsReload,false);
});
test('session transport failure leaves a working login action without claiming configuration is missing',async()=>{
  const f=fixture({sessionError:new Error('network')});await f.c.boot();assert.equal(f.$('#loginButton').disabled,false);assert.equal(f.$('#auth').hidden,false);assert.match(f.messages.at(-1),/연결/);assert.doesNotMatch(f.$('#authMessage').textContent,/설정을 준비/);
});
test('explicitly unconfigured auth remains closed and expired identity cannot keep a workspace',async()=>{
  const f=fixture({session:{authenticated:false,auth_configured:false},old:session()});await f.c.boot();assert.equal(f.$('#loginButton').disabled,true);assert.equal(f.$('#app').hidden,true);assert.equal(f.loads,0);
});
test('a valid login in a fresh device with no remembered trip still loads its server trips',async()=>{
  const f=fixture({session:session()});await f.c.boot();assert.equal(f.loads,1);assert.equal(f.state.trip.id,'trip');assert.equal(f.$('#app').hidden,false);
});
test('callback errors use fixed text and a bounded request id, never provider error text',()=>{
  const c=load(['readLoginFeedback'],{});
  assert.match(c.readLoginFeedback('https://test/?auth_error=cookie_missing'),/쿠키/);
  assert.match(c.readLoginFeedback('https://test/?auth_error=state_expired'),/만료/);
  assert.match(c.readLoginFeedback('https://test/?auth_error=invitation_required'),/바로 시작/);
  assert.doesNotMatch(c.readLoginFeedback('https://test/?auth_error=%3Cscript%3E&auth_request=secret-token'),/script|secret-token/);
  assert.equal(c.readLoginFeedback('https://test/'),'');
});
test('session verification has a bounded network wait and can be retried',async()=>{
  let timeout;const state={controllers:new Set()};const c=load(['api'],{state,setTimeout(fn){timeout=fn;return 1;},clearTimeout(){},FormData,fetch:async(_url,options)=>new Promise((_,reject)=>options.signal.addEventListener('abort',()=>reject(Object.assign(new Error('aborted'),{name:'AbortError'}))))});
  const pending=c.api('/session');const rejected=assert.rejects(pending,e=>e.code==='SERVER_UNAVAILABLE');timeout();await rejected;assert.equal(state.controllers.size,0);
});
test('trip reads also time out, while a write is never automatically retried',async()=>{
  let timeout;const state={controllers:new Set()};let calls=0;
  const c=load(['api'],{state,setTimeout(fn){timeout=fn;return 1;},clearTimeout(){},FormData,fetch:async(_url,options)=>{calls++;return new Promise((_,reject)=>options.signal.addEventListener('abort',()=>reject(Object.assign(new Error('aborted'),{name:'AbortError'}))));}});
  const reading=c.api('/trips'),rejected=assert.rejects(reading,e=>e.code==='SERVER_UNAVAILABLE');timeout();await rejected;assert.equal(calls,1);
  timeout=null;c.fetch=async()=>{calls++;return {ok:true,status:204};};await c.api('/trips',{method:'POST',body:{title:'synthetic'}});assert.equal(timeout,null);assert.equal(calls,2);
});
test('logout during offline initialization cannot reactivate a stale verified session',async()=>{
  const f=fixture({session:session()});let release;
  f.c.window.TravelTools.bind=()=>new Promise(resolve=>release=resolve);
  const boot=f.c.boot();await new Promise(resolve=>setImmediate(resolve));
  f.state.authRevision=1;f.state.session=null;release();await boot;
  assert.equal(f.state.session,null);assert.equal(f.loads,0);
});

test('a newly verified account clears previous account data before reloading its own trips',async()=>{
  const f=fixture({session:session(),old:{...session(),user:{id:'B'}}});f.state.trip={id:'private-B'};await f.c.boot();assert.equal(f.clears,1);assert.equal(f.state.session.user.id,'A');assert.equal(f.loads,1);assert.equal(f.state.trip.id,'trip');
});


test('first-time login asks for no invitation and has a direct Google action',()=>{
  const html=fs.readFileSync('web/index.html','utf8');
  const login=html.slice(html.indexOf('<main id="auth"'),html.indexOf('<main id="offlineApp"'));
  assert.doesNotMatch(login,/초대|id="invitation"|<input/);
  assert.match(login,/Google 계정으로 로그인/);
  assert.match(login,/로그인과 함께 계정이 만들어져요/);
  assert.doesNotMatch(source,/\$\(['"]#invitation['"]\)/);
  const f=fixture({session:{authenticated:false,auth_configured:true}});
  f.state.session={authenticated:false,auth_configured:true};f.c.authScreen();
  assert.equal(f.$('#loginButton').disabled,false);
  assert.match(f.$('#authMessage').textContent,/계정이 만들어져요/);
});
