"""Stage 4 D4 online measurement design: consented events only, small samples unmeasured.

Arms are read from ``product_run_metrics`` (written only for analytics-consented
owners, see ``events.capture_run``):

* ``saved_taste``: runs whose request opted in and actually added a saved-place term
  vs. all other runs;
* ``feedback``: runs with at least one explicit less/exclude choice applied vs. none.

Per arm, the same definitions as the product report: exposure-to-save = saved
(run, place) pairs viewed first / viewed pairs; itinerary adoption = runs with an
``itinerary_add`` after a view / runs with a view. An arm with fewer than
``MIN_EXPOSED_RUNS`` exposed runs or ``MIN_EXPOSURES`` exposures is reported as
미측정 with a null ratio. The arms are opt-in and self-selected, so a difference
is descriptive only, never a causal or promotion claim.
"""
from datetime import datetime, timedelta, timezone
import json
from src.foundation.repository import DomainError
from .events import consented, purge

MIN_EXPOSURES = 100
MIN_EXPOSED_RUNS = 30
ARMS = {'saved_taste': 'saved_taste_applied', 'feedback': 'feedback_applied'}


def _ratio(n, d, enough):
    if not enough:
        return {'numerator': n, 'denominator': d, 'ratio': None, 'display': '미측정', 'reason': 'SAMPLE_TOO_SMALL'}
    return {'numerator': n, 'denominator': d, 'ratio': n / d, 'display': f'{n}/{d}건', 'reason': None}


def online_effect(db, *, start, end, synthetic=False, min_exposures=MIN_EXPOSURES, min_runs=MIN_EXPOSED_RUNS):
    a, b = datetime.fromisoformat(start), datetime.fromisoformat(end)
    if not a.tzinfo or not b.tzinfo or a >= b or b - a > timedelta(days=90):
        raise DomainError('INVALID_PERIOD', 'UTC offset을 포함한 최대 90일 기간을 입력해 주세요.', 422)
    start, end = a.astimezone(timezone.utc).isoformat(), b.astimezone(timezone.utc).isoformat()
    with db.connect() as con:
        con.execute('BEGIN IMMEDIATE'); purge(con)
        runs = {}
        for row in con.execute('SELECT * FROM product_run_metrics WHERE created_at>=? AND created_at<?', (start, end)):
            summary = json.loads(row['summary_json'])
            if summary.get('synthetic') != synthetic or not consented(con, row['owner_id']):
                continue
            runs[row['run_id']] = summary
        views, saves, adds = {}, [], []
        for e in con.execute('SELECT * FROM discovery_events WHERE created_at>=? AND created_at<? AND schema_version=1 AND exclusion_reason IS NULL', (start, end)):
            if e['run_id'] not in runs or not consented(con, e['owner_id']):
                continue
            key = (e['run_id'], e['place_id'])
            if e['event'] == 'recommendation_view':
                views[key] = min(views.get(key, e['created_at']), e['created_at'])
            elif e['event'] == 'recommendation_save':
                saves.append((key, e['created_at']))
            elif e['event'] == 'itinerary_add':
                adds.append((key, e['created_at']))
    saved = {k for k, at in saves if k in views and views[k] <= at}
    adopted = {k[0] for k, at in adds if k in views and views[k] <= at}
    exposed_runs = {k[0] for k in views}
    output = {'kind': 'feedback_loop_online_effect', 'period': {'start': start, 'end': end, 'interval': '[start,end)'},
              'synthetic': synthetic, 'cohort': 'analytics_consented_only',
              'minimums': {'exposures': min_exposures, 'exposed_runs': min_runs}, 'arms': {},
              'limits': ['선택한 사용자만 켜는 옵션이라 무작위 배정이 아니다. 차이는 기술 통계이며 인과·승격 근거가 아니다.',
                         '분석에 동의하지 않은 사용자의 실행·이벤트는 세지 않는다.']}
    for name, flag in ARMS.items():
        arms = {}
        for label, on in (('on', True), ('off', False)):
            ids = {r for r, s in runs.items() if bool(s.get(flag)) is on}
            pairs = [k for k in views if k[0] in ids]
            ex_runs = exposed_runs & ids
            enough = len(pairs) >= min_exposures and len(ex_runs) >= min_runs
            arms[label] = {'completed_runs': len(ids), 'exposed_runs': len(ex_runs), 'exposures': len(pairs),
                           'exposure_to_save': _ratio(sum(k in saved for k in pairs), len(pairs), enough),
                           'itinerary_adoption': _ratio(len(adopted & ex_runs), len(ex_runs), enough)}
        output['arms'][name] = arms
    return output
