.PHONY: help install run index seed ask eval test clean

help:
	@echo "make install   의존성 설치"
	@echo "make serve     웹앱 실행 (http://localhost:8000)"
	@echo "make index     샘플 메일 인덱싱"
	@echo "make seed      데모 메일을 다시 파싱해 시드 갱신 (LLM 호출)"
	@echo "make ask Q=... 질문 하나 던지기"
	@echo "make eval      평가 실행"
	@echo "make test      테스트 실행"
	@echo "make clean     인덱스와 캐시 삭제"

install:
	pip install -r requirements.txt

serve:
	uvicorn api:app --reload --port 8000 --no-access-log

index:
	python -m src.indexer --sample

seed:
	python -m src.seed --build

ask:
	@python -m src.agent "$(Q)"

eval:
	python -m src.evaluate

test:
	pytest tests/ -v

clean:
	rm -rf data/chroma __pycache__ src/__pycache__ .pytest_cache

# --- CI: .github/workflows/ci.yml runs these same targets with PYTHON=python ---
# Local `make ci` uses .venv. PostgreSQL runs only when TRAVEL_TEST_POSTGRES_DSN
# points at a disposable loopback database (tests/postgres_plugin.py enforces it).
.PHONY: ci ci-python ci-js ci-types ci-assets ci-postgres ci-safety e2e e2e-evidence
PYTHON ?= .venv/bin/python
# ZERO_SPEND is not set globally: tests drive metered fake providers and set ZERO_SPEND
# themselves where they test it. CI holds no secrets, so no paid call can authenticate.
CI_ENV = PYTHON_DOTENV_DISABLED=1
# Contract suites re-run on PostgreSQL through tests.postgres_plugin.
PG_PLUGIN_TESTS = tests/test_foundation_auth.py tests/test_foundation_api.py tests/test_reliability_api.py \
	tests/test_itinerary_api.py tests/test_recommendation_api.py tests/test_discovery_foundation.py \
	tests/test_stage1_migration.py tests/test_stage1_workspace.py tests/test_stage1_itinerary.py \
	tests/test_stage1_discovery.py tests/test_stage2_review_contracts.py tests/test_stage2_review_migration.py \
	tests/test_stage2_postgres_migration.py tests/test_public_place_photos.py tests/test_hybrid_recommender.py \
	tests/test_public_discovery.py tests/test_product_feedback.py tests/test_accommodations_api.py
# These build their own PostgreSQL schema and must run without the plugin.
PG_SELF_TESTS = tests/test_cloud_storage.py tests/test_postgres_migration_concurrency.py

ci: ci-safety ci-assets ci-js ci-types ci-python ci-postgres ci-eval

# The SQLite suite never sees the DSN; PostgreSQL suites run only in ci-postgres.
ci-python:
	TRAVEL_TEST_POSTGRES_DSN= $(CI_ENV) $(PYTHON) -m pytest tests -q -p no:cacheprovider

ci-js:
	node --test tests/*.cjs

# Browser JS type check without a build step: only files starting with // @ts-check (tsconfig.json).
ci-types:
	./node_modules/.bin/tsc -p tsconfig.json

ci-assets:
	$(PYTHON) scripts/version_web_assets.py --check

ci-safety:
	$(PYTHON) scripts/check_publish_safety.py --ci

ci-postgres:
	@if [ -z "$$TRAVEL_TEST_POSTGRES_DSN" ]; then \
		echo "ci-postgres: TRAVEL_TEST_POSTGRES_DSN unset - skipped (not run)"; \
	else \
		$(CI_ENV) $(PYTHON) -m pytest -p tests.postgres_plugin $(PG_PLUGIN_TESTS) -q -p no:cacheprovider; plugin=$$?; \
		$(CI_ENV) $(PYTHON) -m pytest $(PG_SELF_TESTS) -q -p no:cacheprovider; self=$$?; \
		test $$plugin -eq 0 -a $$self -eq 0; \
	fi

# Opt-in stage 1 E2E on the synthetic fixture (free ports, users a/b). Browser scenarios
# need playwright (requirements-dev.txt) and: $(PYTHON) -m playwright install --only-shell chromium
# Failed tests leave Playwright traces in .e2e-traces/ (git-ignored).
e2e:
	GOING_E2E=1 TRAVEL_TEST_POSTGRES_DSN= $(CI_ENV) $(PYTHON) -m pytest tests/e2e -q -rs -p no:cacheprovider

# Same run, also saving synthetic-data screenshots as evidence in the stage 1 report folder.
e2e-evidence:
	GOING_E2E_SCREENSHOTS=docs/service-v6/reports/e2e $(MAKE) e2e

# --- v6 evaluation gate (synthetic gold set; no key, no paid call, no network) ---
# local = rule parser, replay = real parser path on hand-made records (pipeline test, not
# model quality). Results go to git-ignored data/eval_runs/ci, then src.evals.gate compares
# them with evals/thresholds.json. Live evaluation is never run here (see evals/mail/README.md).
.PHONY: ci-eval
EVAL_OUT ?= data/eval_runs/ci
EVAL_ENV = TRAVEL_TEST_POSTGRES_DSN= $(CI_ENV)
ci-eval:
	@mkdir -p $(EVAL_OUT)
	$(EVAL_ENV) $(PYTHON) -m src.evals.mail --mode local --out $(EVAL_OUT)/mail-local.json
	$(EVAL_ENV) $(PYTHON) -m src.evals.mail --mode replay --out $(EVAL_OUT)/mail-replay.json
	@# Trip agent safety: scripted replay (scenario+step, source handmade) on a temporary synthetic world.
	$(EVAL_ENV) $(PYTHON) -m src.evals.agent --mode replay --out $(EVAL_OUT)/agent-replay.json
	@# Question set (qa_v1): required by the gate (thresholds qa_local/qa_replay required: true).
	$(EVAL_ENV) $(PYTHON) -m src.evals.qa --mode local --out $(EVAL_OUT)/qa-local.json
	$(EVAL_ENV) $(PYTHON) -m src.evals.qa --mode replay --out $(EVAL_OUT)/qa-replay.json
	$(EVAL_ENV) $(PYTHON) -m src.evals.gate --thresholds evals/thresholds.json \
		--mail-local $(EVAL_OUT)/mail-local.json --mail-replay $(EVAL_OUT)/mail-replay.json \
		--qa-local $(EVAL_OUT)/qa-local.json --qa-replay $(EVAL_OUT)/qa-replay.json \
		--agent-replay $(EVAL_OUT)/agent-replay.json
