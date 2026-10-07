"""Apply later withdrawals when restoring an older offline database snapshot."""
from src.discovery.schema import scrub_trip


def merge_tombstones(db, items):
    if len(items) > 100000:
        raise ValueError('Too many discovery tombstones')
    for item in items:
        if set(item) != {'kind', 'target_id', 'reason', 'created_at'} or item['kind'] not in {'source', 'pack', 'bookmark', 'feedback', 'analytics_owner'}:
            raise ValueError('Invalid discovery tombstone')
        if not isinstance(item['target_id'], str) or len(item['target_id']) > 100:
            raise ValueError('Invalid discovery target')
    with db.connect() as con:
        con.execute('BEGIN IMMEDIATE')
        for item in items:
            con.execute('INSERT INTO discovery_tombstones VALUES(?,?,?,?) ON CONFLICT(kind,target_id) DO UPDATE SET created_at=excluded.created_at,reason=excluded.reason WHERE excluded.created_at>discovery_tombstones.created_at',
                tuple(item[k] for k in ('kind','target_id','reason','created_at')))
        con.execute("UPDATE evidence_sources SET status='revoked',display_permitted=0 WHERE id IN (SELECT target_id FROM discovery_tombstones WHERE kind='source')")
        con.execute("UPDATE candidate_packs SET status='disabled' WHERE id IN (SELECT target_id FROM discovery_tombstones WHERE kind='pack')")
        con.execute("UPDATE research_candidates SET status='disabled' WHERE pack_id IN (SELECT target_id FROM discovery_tombstones WHERE kind='pack')")
        con.execute("UPDATE bookmarks SET deleted_at=COALESCE(deleted_at,(SELECT created_at FROM discovery_tombstones WHERE kind='bookmark' AND target_id=bookmarks.id)),note='',input_value='',normalized_input='',candidates_json='[]' WHERE id IN (SELECT target_id FROM discovery_tombstones WHERE kind='bookmark')")
        import json
        for tombstone in con.execute("SELECT * FROM discovery_tombstones WHERE kind IN ('feedback','analytics_owner')").fetchall():
            if tombstone['kind']=='feedback':
                con.execute("UPDATE visit_feedback SET private_note='',payload_json='{}',withdrawn_at=COALESCE(withdrawn_at,?) WHERE id=?",(tombstone['created_at'],tombstone['target_id']))
                for event in con.execute('SELECT id,detail_json FROM discovery_events').fetchall():
                    if json.loads(event['detail_json']).get('feedback_id')==tombstone['target_id']:con.execute('DELETE FROM discovery_events WHERE id=?',(event['id'],))
            else:
                con.execute('DELETE FROM discovery_events WHERE owner_id=? AND created_at<=?',(tombstone['target_id'],tombstone['created_at']))
                con.execute('DELETE FROM product_run_metrics WHERE owner_id=? AND created_at<=?',(tombstone['target_id'],tombstone['created_at']))
                con.execute('UPDATE product_preferences SET analytics_enabled=0 WHERE owner_id=? AND updated_at<=?',(tombstone['target_id'],tombstone['created_at']))
        for row in con.execute('SELECT id FROM trips WHERE deleted_at IS NOT NULL').fetchall():
            scrub_trip(con, row['id'])
            from src.recommendations.schema import scrub_trip as scrub_recommendations
            from src.itineraries.schema import scrub_trip as scrub_itineraries
            scrub_recommendations(con,row['id'])
            scrub_itineraries(con,row['id'])
            from src.product.schema import scrub_trip as scrub_product
            scrub_product(con,row['id'])
    return {'applied': len(items)}
