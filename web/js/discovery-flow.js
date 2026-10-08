/* A visit uses existing trip defaults. Only deliberate filter edits create a new run. */
(function(root,factory){const value=factory();if(typeof module==='object'&&module.exports)module.exports=value;else root.DiscoveryFlow=value;})(typeof window==='undefined'?globalThis:window,function(){
  'use strict';
  const common=new Set(['FACT_UNCONFIRMED','PREFERENCE_UNKNOWN','ORIGIN_OR_COORDINATES_UNKNOWN','PRICE_UNKNOWN','EVIDENCE_UNKNOWN','INDEPENDENT_LOCAL_EVIDENCE_UNKNOWN','ICONIC_EVIDENCE_UNKNOWN','DISTANCE_ESTIMATE']);
  function cardReasons(item){return [...new Set(item.reason_codes||[])].filter(code=>item.eligibility==='ineligible'||!common.has(code));}
  function create({read,submit,later=setTimeout,cancel=clearTimeout}){
    let timer=null,pending=null,scope=null,started=false;
    function sync(){const s=read(),key=s.epoch+':'+s.tripId;if(key!==scope){if(timer)cancel(timer);timer=null;pending=null;scope=key;started=false;}return s;}
    function busy(s){return s.busy||s.connectionError||s.requestError||!s.ready||!s.visible;}
    function flush(){timer=null;const s=sync();if(!pending||busy(s))return;pending=null;started=true;Promise.resolve(submit()).catch(()=>{});}
    function enter(){const s=sync();if(!started&&!s.hasRun&&!s.dirty&&s.ready&&s.visible){started=true;pending=scope;timer=later(flush,0);}}
    function changed(){const s=sync();if(!s.visible)return;pending=scope;if(timer)cancel(timer);timer=later(flush,350);}
    function settled(){sync();if(pending&&!timer)timer=later(flush,0);}
    function applied(){sync();pending=null;if(timer)cancel(timer);timer=null;started=true;}
    return {enter,changed,settled,applied};
  }
  let ui,auto,photoScope=null,requested=new Set(),photoPending=false,photoTimer=null;
  function init(context){ui=context;auto=create({read:()=>{const s=ui.state,r=s.recommendations;return {epoch:s.epoch,tripId:s.trip?.id,visible:s.tab==='explore'&&s.discovery.view!=='saved',ready:Boolean(s.session?.authenticated&&s.trip&&s.discovery.loaded&&r.loaded&&s.discovery.conditions?.context_state==='ready'),hasRun:Boolean(r.active||r.displayed||r.runs?.length),dirty:s.discovery.dirty,busy:r.submitting||r.resultRecoveryPending||['queued','running'].includes(r.active?.state),connectionError:r.connectionError,requestError:r.requestError};},submit:ui.applyRecommendations});}
  function photoReset(){const key=ui.state.epoch+':'+ui.state.trip?.id;if(photoScope===key)return;photoScope=key;requested=new Set();photoPending=false;clearTimeout(photoTimer);photoTimer=null;}
  async function photos(items){
    if(!ui||!['trip','explore'].includes(ui.state.tab)||!ui.state.session?.authenticated)return;
    photoReset();if(photoPending)return;
    const ids=[...new Set(items.filter(p=>p.source_kind==='public_map'&&!p.photos?.length&&!requested.has(p.place_id)).map(p=>p.place_id))].slice(0,6);
    if(!ids.length)return;
    ids.forEach(id=>requested.add(id));photoPending=true;const epoch=ui.state.epoch,path=ui.tripPath();
    const finish=async(refresh=true)=>{if(epoch!==ui.state.epoch)return;photoPending=false;if(refresh)await ui.loadRecommendations().catch(()=>{});};
    try{
      const result=await ui.api(path+'/place-photos',{method:'POST',body:{place_ids:ids}});
      if(epoch!==ui.state.epoch)return;
      if(!result.job_id){await finish(result.state==='succeeded');return;}
      let attempts=0;
      const poll=async()=>{if(epoch!==ui.state.epoch)return;try{const job=await ui.api('/jobs/'+encodeURIComponent(result.job_id));if(epoch!==ui.state.epoch)return;if(['succeeded','partial','failed','cancelled'].includes(job.state)){await finish();return;}if(++attempts<90)photoTimer=setTimeout(poll,2000);else await finish(false);}catch{await finish(false);}};
      await poll();
    }catch{await finish(false);}
  }
  return {init,create,cardReasons,photos,enter:()=>auto?.enter(),changed:()=>auto?.changed(),settled:()=>auto?.settled(),applied:()=>auto?.applied()};
});
