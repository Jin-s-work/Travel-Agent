"""v6 보고서 머리줄의 커밋 상태 문장은 시점을 밝힌다.

‘커밋 없음’을 시점 없이 쓰면 로컬 커밋 뒤에 틀린 문장이 된다. 그래서 머리줄은
‘작성 시점 … 미커밋’처럼 쓰고, 시점 없는 ‘커밋 없음’ 단정이 돌아오지 않게 막는다.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = {
    'docs/service-v6/reports/STAGE2.md': '이 보고서 작성 시점(2026-10-21) 미커밋',
    'docs/service-v6/IMPLEMENTATION_STATUS.md': '이 기록 작성 시점(2026-10-21) 미커밋',
}


def _header(path):
    lines = (ROOT / path).read_text(encoding='utf-8').splitlines()
    return next(line for line in lines if line.startswith(('2026-', '최종 갱신')))


def test_commit_state_in_headers_names_the_point_in_time():
    for path, phrase in REPORTS.items():
        header = _header(path)
        assert phrase in header, path
        assert '커밋 없음' not in header, path
        assert '로컬 커밋까지만' not in header, path
