/* Paging only reads the same run. No provider, mutation or ranking calls. */
(function(root,factory){const value=factory();if(typeof module==='object'&&module.exports)module.exports=value;else root.RecommendationPages=value;})(typeof window==='undefined'?globalThis:window,function(){
  'use strict';let ui;
  function init(context){ui=context;}
  function merge(prior,next){if(!prior||prior.run_id!==next.run_id||prior.snapshot_version!==next.snapshot_version||next.freshness_state!=='current')return next;
    for(const kind of Object.keys(next.pages||{})){const before=prior.result?.sections?.[kind],after=next.result?.sections?.[kind];if(!before||!after)continue;const oldSize=['items','needs_confirmation'].reduce((n,k)=>n+(before[k]?.length||0),0);if(oldSize<=next.pages[kind].returned)continue;
      for(const group of ['items','needs_confirmation']){const current=new Map((after[group]||[]).map(i=>[i.place_id,i]));after[group]=(before[group]||[]).map(i=>current.get(i.place_id)||i);for(const item of current.values())if(!after[group].some(i=>i.place_id===item.place_id))after[group].push(item);}
      next.pages[kind]=prior.pages[kind];
    }return next;
  }
  function appendControl(host,run,kind){const info=run.pages?.[kind];if(!info)return;const box=ui.make('div','result-page-actions');const count=()=>['items','needs_confirmation'].reduce((n,k)=>n+(run.result.sections[kind][k]?.length||0),0);const status=ui.make('p','hint',`${count()} / ${info.total}곳`);status.setAttribute('role','status');box.append(status);host.append(box);if(!info.next_cursor)return;
    const more=ui.button('12곳 더 보기',async()=>{if(more.disabled)return;more.disabled=true;more.textContent='저장된 장소를 읽고 있어요…';const epoch=ui.state.epoch,serial=ui.state.recommendations.serial,path=ui.tripPath(),cursor=run.pages[kind].next_cursor;
      try{const next=await ui.api(path+'/recommendations/'+encodeURIComponent(run.run_id)+'?page_size=12&section='+kind+'&cursor='+encodeURIComponent(cursor));if(epoch!==ui.state.epoch||serial!==ui.state.recommendations.serial||ui.state.recommendations.displayed!==run||!box.isConnected)return;
        if(next.run_id!==run.run_id||next.snapshot_version!==run.snapshot_version)throw Error('추천 조건이 바뀌었어요. 현재 결과를 다시 확인해 주세요.');
        const seen=new Set(['items','needs_confirmation'].flatMap(k=>run.result.sections[kind][k]||[]).map(i=>i.place_id));const cards=ui.make('div','recommendation-grid');let first;
        for(const key of ['items','needs_confirmation'])for(const item of next.result.sections[kind][key]||[]){if(seen.has(item.place_id))continue;seen.add(item.place_id);run.result.sections[kind][key].push(item);const card=ui.recommendationCard(item,run,null,true);cards.append(card);first??=card;}
        host.insertBefore(cards,box);run.pages[kind]=next.pages[kind];status.textContent=`${count()} / ${info.total}곳`;more.textContent='12곳 더 보기';more.disabled=false;
        if(!next.pages[kind].next_cursor){more.hidden=true;first?.querySelector('button')?.focus({preventScroll:true});}
      }catch(error){if(epoch===ui.state.epoch&&box.isConnected){status.textContent=error.message+' 기존 목록은 유지됩니다.';more.textContent='이어보기 다시 시도';more.disabled=false;}}
    },'secondary');box.append(more);
  }
  return {init,merge,appendControl};
});
