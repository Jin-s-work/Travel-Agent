"""SQLite-only syntax in application SQL must be translated before it reaches PostgreSQL.

Found 2026-10-20 by the stage 1 PostgreSQL run: the v5 review-contract lookup used
json_extract(evidence_json,'$.observation_profile'), which PostgreSQL rejects (42883), so
the admin review endpoints returned 500 on the cloud backend. No database is needed here.
"""
from pathlib import Path
import re

from src.storage.postgres import translate

ROOT = Path(__file__).resolve().parents[1]
CALL = re.compile(r"json_extract\([^)]*\)(?:=1)?")


def test_review_contract_profile_lookup_is_translated():
    sql = ("SELECT * FROM review_provider_contracts WHERE provider=? AND status='approved' AND expires_at>?"
           " AND COALESCE(json_extract(evidence_json,'$.observation_profile'),'strict_source_v1')=?")
    translated = translate(sql)
    assert 'json_extract' not in translated
    assert "COALESCE((evidence_json::jsonb->>'observation_profile'),'strict_source_v1')=%s" in translated


def test_every_json_extract_in_application_sql_has_a_translation():
    calls = set()
    for path in (ROOT / 'src').rglob('*.py'):
        if path.name == 'postgres.py' and path.parent.name == 'storage':
            continue
        calls.update(CALL.findall(path.read_text()))
    assert calls, 'expected the known json_extract call sites'
    missing = sorted(call for call in calls if 'json_extract' in translate('SELECT 1 WHERE ' + call))
    assert not missing, missing
