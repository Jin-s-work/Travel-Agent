"""Stage 4 bundle D 알려진 한계 고정: 다른 여행 실행 스냅숏에 복사된 place_id.

docs/service-v6/FEEDBACK_LOOP.md ‘알려진 한계’와 feedback_loop 모듈 docstring 이 말하는
지금 동작을 고정한다. 동작을 바꾸면 문서와 이 시험을 함께 고친다.
합성 카탈로그만 쓴다. 네트워크·공급자 호출 없음.
"""
import json
from pathlib import Path
import time

from tests.test_foundation_api import service  # noqa: F401 (discovery 가 쓰는 fixture)
from tests.test_discovery_foundation import discovery  # noqa: F401 (fixture)
from tests.test_recommendation_persistence import setup
from tests.test_v6_feedback_loop import feedback, new_trip, run
from src.recommendations import feedback_loop

ROOT = Path(__file__).resolve().parents[1]


def _run_row(discovery, run_id):
    with discovery.app.state.db.connect() as con:
        return con.execute('SELECT data_status,snapshot_json FROM recommendation_runs WHERE id=?', (run_id,)).fetchone()


def _wait_deleted(discovery, run_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        row = _run_row(discovery, run_id)
        if row['data_status'] == 'deleted':
            return row
        time.sleep(.01)
    return _run_row(discovery, run_id)


def test_future_place_id_stays_in_other_trip_snapshot_until_that_trip_is_deleted(discovery):
    pack, trip, path, _ = setup(discovery)
    place = pack['places'][1]['place_id']
    first = feedback(discovery, path, place, 'limit-future-1', reflect_preference=True,
                     preference_action='exclude', preference_scope='future', private_note='메모')
    assert first.status_code == 201, first.text
    trip2, path2 = new_trip(discovery)
    before = run(discovery, path2, trip2, 'limit-run-1')
    assert before['snapshot']['feedback_excluded_place_ids'] == [place]
    # 스냅숏에는 장소 ID와 건수만. 원본 여행 ID·개인 메모는 없다.
    dumped = json.dumps(before['snapshot'], ensure_ascii=False)
    assert trip['id'] not in dumped and '메모' not in dumped

    # 철회: 이후 실행은 쓰지 않지만, 이미 만든 다른 여행 실행의 스냅숏은 그대로다.
    item = first.json()
    assert discovery.client.request('DELETE', path + '/feedback/' + item['id'],
                                    json={'expected_version': item['version']}).status_code == 200
    assert run(discovery, path2, trip2, 'limit-run-2')['snapshot']['feedback_excluded_place_ids'] == []
    kept = discovery.client.get(path2 + '/recommendations/' + before['run_id']).json()
    assert kept['snapshot']['feedback_excluded_place_ids'] == [place]

    # 원본 여행 삭제: 원본 여행 실행은 정리되지만, 다른 여행 실행의 복사본은 남는다.
    again = feedback(discovery, path, place, 'limit-future-2', reflect_preference=True,
                     preference_action='exclude', preference_scope='future')
    assert again.status_code == 201, again.text
    origin_run = run(discovery, path, trip, 'limit-origin-run')
    copied = run(discovery, path2, trip2, 'limit-run-3')
    assert copied['snapshot']['feedback_excluded_place_ids'] == [place]
    assert discovery.client.delete(path).status_code == 202
    assert tuple(_wait_deleted(discovery, origin_run['run_id'])) == ('deleted', '{}')
    still = discovery.client.get(path2 + '/recommendations/' + copied['run_id']).json()
    assert still['snapshot']['feedback_excluded_place_ids'] == [place]

    # 그 다른 여행을 지우면 그쪽 스냅숏도 함께 지워진다.
    assert discovery.client.delete(path2).status_code == 202
    for run_id in (before['run_id'], copied['run_id']):
        assert tuple(_wait_deleted(discovery, run_id)) == ('deleted', '{}')


def test_feedback_loop_docs_state_the_snapshot_limit():
    doc = (ROOT / 'docs/service-v6/FEEDBACK_LOOP.md').read_text(encoding='utf-8')
    assert '## 알려진 한계' in doc
    limit = doc.split('## 알려진 한계', 1)[1].split('\n## ', 1)[0]
    for phrase in ("preference_scope='future'", 'recommendation_runs.snapshot_json',
                   '피드백을 철회해도, 기록한 원본 여행을 지워도', '그 다른 여행을 지우면'):
        assert phrase in limit, phrase
    # 예전처럼 철회가 ‘모든 여행에서’ 기록을 없앤다고 읽히는 문장이 돌아오지 않게 한다.
    assert '철회하면 모든 여행에서 반영을 멈춘다' not in doc
    docstring = feedback_loop.__doc__
    assert 'Neither one rewrites' in docstring and 'snapshot_json' in docstring
