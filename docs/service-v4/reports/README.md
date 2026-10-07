# 감사 증거와 재실행

구현 후 최신 결과는 [stage1-validation.md](stage1-validation.md), [후보/SQL 세부 결과](stage1-discovery-validation.md), [계측 JSON](stage1-discovery-metrics.json), [schema 13 이관](stage1-migration.md)을 본다. 아래의 최초 감사 로그와 구현 후 회귀 결과를 구분한다.

2026-10-07의 기록이다. 앱 코드 변경 전에 재현한 **결함의 존재**를 보존한다. discovery 재현의 pass는 제품 수정 완료가 아니다.

저장소 루트에서 아래 명령을 사용한다. 기본 테스트는 임시 DB/fake provider를 사용한다. 기록된 결과 JSON과 로그를 덮지 않도록 새 경로로 출력한다.

```sh
PYTHON_DOTENV_DISABLED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. .venv/bin/python docs/service-v4/reports/backend-probe.py > /tmp/going-backend-probe-new.json
PYTHON_DOTENV_DISABLED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. .venv/bin/python -m pytest docs/service-v4/reports/discovery-audit-reproduction.py -q -s -p no:cacheprovider -p tests.conftest
PYTHON_DOTENV_DISABLED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=docs/service-v4/reports:. .venv/bin/python -m pytest tests -q -p no:cacheprovider -p going_test_probe
```

진단 plugin은 기본으로 새 임시 디렉터리에 결과를 쓴다. 필요하면 GOING_AUDIT_OUTPUT_DIR로 출력 폴더를 명시한다. 재현 스크립트는 경로를 이 문서 위치/저장소에 맞게 옮겼고, 원래 결함 재현 assertion은 유지했다. 앱 수정 뒤에는 일부 assertion이 의도적으로 실패할 수 있다. 실제 회귀 테스트는 정상 기대값으로 별도 작성한다.

- backend-audit.md: SQL/저장결과/leader/원문정리 코드 및 재현 근거.
- discovery-audit.md: 후보/리뷰/일정 연결과 데이터 범위.
- ux-audit.md: 정적 UI 감사. 실제 브라우저 재현을 주장하지 않는다.
- stage1-test-investigation.md: 전체7실패와 단독/순서재현 성공의 해석.
- pytest.log, node-tests.log: 최초 전체 실행의 원본 로그.

로그와 fixture는 합성 데이터다. 브라우저는 로그인 첫 화면까지만 관측했으며, 인증 후 흐름은 자동 승인 거절로 미검증이다.
