#!/usr/bin/env python3
"""Read-only existing Supabase public-catalog counts. No private trip/body export."""
import argparse,json,sys
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

def read(env):
    from dotenv import dotenv_values
    from scripts.prepare_render_env import render_values,DB_CA_PATH
    import psycopg
    values=render_values(dotenv_values(env,interpolate=False),profile='openai',with_apify=True)
    ca=Path(__file__).resolve().parents[1]/'deploy/render-supabase/prod-ca-2021.crt'
    url=values['DATABASE_URL'].replace(DB_CA_PATH,str(ca))
    now=datetime.now(timezone.utc).isoformat();report={'checked_at':now,'writes':0,'provider_calls':0,'cities':{}}
    with psycopg.connect(url,connect_timeout=12) as con:
        con.execute('SET TRANSACTION READ ONLY');con.execute('SET LOCAL statement_timeout=10000')
        con.execute('SET LOCAL search_path TO travel,pg_catalog')
        for city in ('tokyo','barcelona'):
            counts={}
            counts['stored_canonical_places']=con.execute('SELECT count(*) FROM place_identities WHERE city=%s AND deleted_at IS NULL',(city,)).fetchone()[0]
            counts['current_catalog_by_category']={k:n for k,n in con.execute("SELECT c.category,count(DISTINCT c.place_id) FROM research_candidates c JOIN place_identities p ON p.id=c.place_id JOIN candidate_packs k ON k.id=c.pack_id WHERE p.city=%s AND p.deleted_at IS NULL AND c.status IN ('approved','public_data') AND k.status IN ('approved','public_data') AND k.synthetic=0 AND EXISTS(SELECT 1 FROM evidence_sources s WHERE s.place_id=p.id AND s.status='active' AND s.read_confirmed=1 AND s.display_permitted=1) GROUP BY c.category",(city,))}
            counts['places_with_unexpired_facts']=con.execute("SELECT count(DISTINCT f.place_id) FROM place_facts f JOIN place_identities p ON p.id=f.place_id JOIN evidence_sources s ON s.id=f.source_id WHERE p.city=%s AND p.deleted_at IS NULL AND f.expires_at>%s AND s.status='active' AND s.read_confirmed=1 AND s.display_permitted=1",(city,now)).fetchone()[0]
            counts['active_real_review_aggregates']=con.execute("SELECT count(*) FROM review_aggregates a JOIN place_identities p ON p.id=a.place_id JOIN review_collection_runs r ON r.id=a.run_id WHERE p.city=%s AND p.deleted_at IS NULL AND a.id=p.active_aggregate_id AND a.invalidated_at IS NULL AND a.expires_at>%s AND r.provider!='fake' AND r.deleted_at IS NULL",(city,now)).fetchone()[0]
            counts['approved_review_links']=con.execute("SELECT count(*) FROM place_external_links l JOIN place_identities p ON p.id=l.canonical_place_id WHERE p.city=%s AND p.deleted_at IS NULL AND l.status='approved' AND l.expires_at>%s",(city,now)).fetchone()[0]
            counts['photo_cache_states']={}
            for payload,expiry in con.execute("SELECT x.payload_json,x.expires_at FROM place_photo_cache x JOIN place_identities p ON p.id=x.place_id WHERE p.city=%s AND p.deleted_at IS NULL",(city,)):
                data=json.loads(payload);
                from src.discovery.photos import photo_availability
                state='expired' if expiry<=now else 'available' if data.get('photos') else photo_availability(data.get('photo_status',{}).get('reason_codes',[]))
                counts['photo_cache_states'][state]=counts['photo_cache_states'].get(state,0)+1
            from src.discovery.photos import for_places
            from psycopg.rows import dict_row
            class ReadOnlyConnection:
                def execute(self,sql,params=()):
                    if not sql.lstrip().upper().startswith('SELECT'):raise ValueError('READ_ONLY_QUERY_REQUIRED')
                    return con.cursor(row_factory=dict_row).execute(sql.replace('?', '%s'),params)
            ids=[r[0] for r in con.execute('SELECT id FROM place_identities WHERE city=%s AND deleted_at IS NULL',(city,))]
            photos=list(for_places(ReadOnlyConnection(),ids,clock=datetime.fromisoformat(now)).values())
            counts['currently_permitted_photo_places']=sum(bool(p['photos']) for p in photos)
            counts['currently_permitted_photo_assets']=sum(len(p['photos']) for p in photos)
            counts['current_photo_availability']={}
            for p in photos:
                state=p['photo_status']['availability_state']
                counts['current_photo_availability'][state]=counts['current_photo_availability'].get(state,0)+1
            counts['actual_browser_img_load_success_rate']=None
            report['cities'][city]=counts
        report['review_controls']=dict(zip(('research_enabled','production_enabled'),con.execute('SELECT research_enabled,production_enabled FROM review_controls WHERE singleton=1').fetchone()))
        report['active_apify_policies']=con.execute("SELECT count(*) FROM provider_policies WHERE provider='apify' AND status='active' AND expires_at>%s",(now,)).fetchone()[0]
        report['approved_provider_contracts']=con.execute("SELECT count(*) FROM review_provider_contracts WHERE provider='apify' AND status='approved' AND expires_at>%s",(now,)).fetchone()[0]
        report['actual_quality_evaluations']=con.execute("SELECT count(*) FROM review_quality_evaluations WHERE synthetic=0 AND passed=1 AND expires_at>%s",(now,)).fetchone()[0]
    return report

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--env',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    try:report=read(args.env)
    except Exception:
        print(json.dumps({'status':'unavailable','reason':'READ_ONLY_DATABASE_AUDIT_FAILED','writes':0}));return 1
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps(report,ensure_ascii=False));return 0
if __name__=='__main__':raise SystemExit(main())
