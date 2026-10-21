# mail_gold_v1 — 메일 추출 평가 (synthetic)

메일 추출이 "잘 되는 것 같다"가 아니라 **같은 정답셋에서 숫자로** 나오게 하고, 나빠지면 CI가 막는다.
모든 사례·메일·replay 기록은 **합성(synthetic)** 이다. 실존 인물·실제 예약번호·실제 이메일 주소는 없다.

| 무엇 | 어디 |
| --- | --- |
| 정답셋 (사례당 JSON 1개, 35건·예약 45개) | `evals/mail/gold/` · 검증기 `src/evals/gold.py` |
| 신규 합성 메일 원문 | `evals/mail/cases/` (기존 두 팩은 `examples/` 원래 경로를 `file`로 가리킴) |
| 평가기 | `src/evals/mail.py` (`python -m src.evals.mail`) |
| replay 기록 (손으로 만든 것만) | `evals/replay/mail/` · 생성기 `src/evals/replay_build.py` |
| CI 기준선·규칙 | `evals/thresholds.json` · 게이트 `src/evals/gate.py` |
| 실행 산출물 (git 무시) | `data/eval_runs/` |

## 바로 실행

```bash
export PYTHON_DOTENV_DISABLED=1 TRAVEL_TEST_POSTGRES_DSN=
python -m src.evals.mail --mode local  --out data/eval_runs/mail-local.json    # 규칙 파서, 외부 호출 0
python -m src.evals.mail --mode replay --out data/eval_runs/mail-replay.json   # 실제 parser 경로 + replay 기록, 키·네트워크 0
python -m src.evals.qa --mode local  --out data/eval_runs/qa-local.json       # 질문 평가 (게이트 필수)
python -m src.evals.qa --mode replay --out data/eval_runs/qa-replay.json
python -m src.evals.agent --mode replay --out data/eval_runs/agent-replay.json # 에이전트 안전 (게이트 필수)
python -m src.evals.gate --thresholds evals/thresholds.json \
  --mail-local data/eval_runs/mail-local.json --mail-replay data/eval_runs/mail-replay.json \
  --qa-local data/eval_runs/qa-local.json --qa-replay data/eval_runs/qa-replay.json \
  --agent-replay data/eval_runs/agent-replay.json   # 다섯 결과 중 하나라도 빠지면 FAIL
make ci-eval        # 위 평가 다섯 개를 data/eval_runs/ci/ 에 쓰고 게이트까지 (CI eval job과 같음)
```

결과는 기계용 JSON과 사람용 `.md`(같은 경로)로 나온다. 둘 다 `synthetic: true`를 적는다.

## 세 가지 모드

| 모드 | 추출기 | 외부 호출 | 숫자의 뜻 |
| --- | --- | --- | --- |
| `local` | `src.foundation.local_mail.parse_local_document` | 0 | 규칙 파서 기준선 |
| `replay` | `src.parser.extract_document_reservations` (예산 gateway·수리 1회 포함) | 0 | **파이프라인 시험, 모델 품질 아님** |
| `live` | 위와 같은 경로 + OpenAI | 유료 (대표만 실행) | 실제 모델 결과 (입력은 합성) |

replay 모드는 임시 SQLite에 평가용 합성 사용자·여행을 만들고 `Budget`(가격 `replay/<EXTRACTION_MODEL>` 0원 + 시험용 `fake/*`) →
`ProviderGateway` → `metered_context` → `adapter_scope(ReplayAdapter(ReplayStore('evals/replay/mail')))` 순서로 실제 운영 경로를 태운다.
비용·지연은 사례별로 `llm_calls` 계측에서 읽는다(local은 계측이 없어 `null`, replay 지연은 로컬 처리 시간이라 모델 지연이 아님).

## 채점 규칙 요약

1. **정규화** — 예측을 정답 형식으로 바꾼다. 항공 예약에 flight/outbound/return 이벤트가 2개 이상이면 구간마다 예약 1개로 펼친다.
   시작·끝은 첫·마지막 이벤트(없으면 date/time), `HH:MM:SS`는 `HH:MM`으로 자른다.
   party.children은 children_status가 unknown → null, none → 0, present → 인원수.
   status는 로컬 review_reasons로만 정한다(취소 → cancelled, 변경 요청 → changed, 그 외 confirmed). AI 출력에는 상태·인원·금액이 없어 null이다.
2. **매칭** — 유형·시작일 기준 최적 매칭(헝가리안). 쌍 점수 = 2·같은 시작일 + 2·같은 type + 1·같은 예약번호 + 0.5·제공자 일치.
   시작일과 type이 둘 다 다르면 매칭하지 않는다. 남는 예측은 false positive 예약, 남는 정답은 놓친 예약.
3. **필드 비교** — provider: NFKC·casefold·괄호 제거·영숫자만 남긴 뒤 같거나, 짧은 쪽 4자 이상이면 포함 관계.
   예약번호: 공백 제거·대소문자 무시. amount/deposit: Decimal 정규화(`"214.50" == "214.5"`). 나머지는 정확히 같아야 한다.
4. **집계** — 필드별·전체 micro P/R/F1. 정답값 있음+일치 = TP, 정답값 있음+예측 없음/불일치 = FN, 예측값 있음+(정답 null 또는 불일치) = FP.
   `unscored` 필드는 빼고, `unscored`에 없는 정답 null은 "추출하면 안 됨"이라 값을 내면 FP다.
5. **사례 지표** — 예약 수 일치, forbidden 위반(decoy 값으로 시작하면 1건, 규칙당 사례마다 최대 1건), must_review 충족(매칭된 예측에서 그 필드가 null이거나,
   정답에 시각이 있는데 날짜만 남겼거나, review_reasons가 그 필드를 덮을 때), 근거 없는 예약번호(원문에 없는 번호 수), 실패 유형
   (`extractor_error:<code>` `count_mismatch` `missed_booking` `false_positive_booking` `field_mismatch:<field>` `forbidden:<rule>` `missed_review:<field>`).
6. **측정 불가** — 추출기가 아예 내지 않는 필드에 decoy가 걸린 forbidden 규칙은 **위반 0이 아니라 `forbidden_unmeasurable`** 로 따로 센다.
   결과 JSON의 `unmeasurable_fields`에 그 모드의 필드 목록이 실린다. JSON·md·게이트 출력에 모두 따로 나온다.
   - **replay·live** — AI 추출 스키마에는 `status`·`party.*`·`amount`·`currency`·`deposit`이 없어 항상 null이다(`AI_ABSENT_FIELDS`).
     `cancelled_as_active` syn-02·syn-14, `deposit_as_total` mtp-03·syn-17 4건이 측정 불가다.
     must_review도 이 필드(status 2건·party.adults 1건)는 null이라 공짜로 충족되므로 분자·분모에서 빼고 `must_review.unmeasurable`로 센다.
     **live의 "forbidden 0"은 이 4개 규칙을 보장하지 않는다.**
   - **local** — 규칙 파서(`parse_local_document`)는 `amount`·`currency`·`deposit` 키를 내지 않는다(`LOCAL_ABSENT_FIELDS`, 시험이 실제 출력 키와 대조한다).
     그래서 `deposit_as_total` mtp-03·syn-17 2건이 측정 불가다. **local의 "forbidden 0"은 13개 규칙 중 측정 가능한 11개에 대한 값이다.**
     규칙 파서가 보증금을 총액으로 넣는 회귀는 금액 필드가 생기기 전까지 이 평가로 잡을 수 없다. local must_review에는 이 필드가 없어 측정 불가 0건이다.

## replay 기록 (손으로 만든 것만)

`python -m src.evals.replay_build`가 다시 만든다(`--check`는 저장본과 같은지만 확인). 실제 parser 경로를 스크립트 응답으로 태워
요청 키를 잡으므로, **프롬프트나 스키마를 바꾸면 이 명령을 다시 돌려야** replay가 `REPLAY_MISS`로 실패하지 않는다.
기록에는 요청 메시지가 없고 요청의 hash(`request_key`, `input_sha256`)와 응답만 있다. 단 응답의 `raw_snippet`에는 **합성 원문의 한 줄**(제목 줄 등)이 그대로 들어 있다(35개 응답 모두).

| source | 사례 | 내용 | 평가기에서 보이는 것 |
| --- | --- | --- | --- |
| `gold_oracle` | 나머지 31건 | 정답을 `DOCUMENT_RESERVATIONS_SCHEMA`로 옮긴 응답 (식당은 kind null) | oracle 필드는 FP 0 |
| `handmade_fault` | mtp-02-tokyo-hotel | 첫 응답 날짜 형식 위반 → 수리 응답이 oracle | 수리 1회 후 성공 |
| `handmade_fault` | mtp-06-date-only-unknown-party | 첫 응답 JSON 깨짐 → 수리 응답도 원문에 없는 발췌 | `extractor_error:EXTRACTION_INVALID` |
| `handmade_fault` | syn-01-ja-ryokan-payment-date | 결제일을 방문일로 | `forbidden:payment_date_as_visit` |
| `handmade_fault` | syn-13-es-change-request-pending | 변경 요청 시각을 확정 시각으로 | `forbidden:change_as_new_confirmed` |

AI 스키마에 status·party·금액·식당 종류가 없으므로 replay에서 그 필드 F1은 구조적으로 0이다. 모델 품질 숫자로 쓰지 않는다.

## CI 게이트 (`evals/thresholds.json`)

- micro·필드별 F1, 예약 수 정확도, must_review 재현율이 기준선보다 **0.02 넘게** 떨어지면 실패.
- **사례별 허용 목록(`expected_failures`, 주 검사)** — forbidden 위반은 `case_id:규칙`, 추출기 오류는 `case_id:오류코드` 단위로 본다.
  목록에 없는 위반·오류가 **하나라도** 나오면 실패한다(다른 위반이 같이 사라져 총합이 같아도).
  replay는 `exact: true`: 심어 둔 결함(syn-01 `payment_date_as_visit`, syn-13 `change_as_new_confirmed`, mtp-06 `EXTRACTION_INVALID`, 수리 사례 mtp-02·mtp-06,
  측정 불가 4건)이 **관측되지 않아도** 실패한다 — 평가기가 결함을 못 잡게 됐다는 뜻이다.
  local은 `exact: false`: 목록에서 사라진 항목은 note로만 알린다(개선이면 목록에서 지운다).
  단 `exact_kinds: ["forbidden_unmeasurable"]`이라 측정 불가 2건(mtp-03·syn-17 `deposit_as_total`)만은 양방향으로 고정된다 —
  목록 밖 측정 불가가 생겨도, 목록의 2건이 사라져도(평가기가 측정 불가를 다시 "위반 0"으로 세게 되면) 실패한다.
- **정답셋 범위(`gold`)** — 사례 수 35·정답 예약 45·정렬한 case_id 목록의 sha256이 다르면 실패한다. `--max-cases`/`--cases` 부분 실행 결과나 사례가 빠진 결과는 통과하지 못한다.
  내용도 고정한다: `gold_sha256`(정답셋 파일을 case_id 순으로 정규화한 JSON의 sha256, `src/evals/gold.py` `gold_content_sha256`)이 다르면 실패한다.
  같은 id로 기대값·forbidden·decoys·must_review를 약하게 고쳐도 통과하지 못한다. 의도한 변경이면 thresholds의 hash도 같이 갱신한다.
- 개수 상한(`max`)은 보조 검사로 남겼다. 원칙은 0이고, 0이 아닌 값은 `max_reason`에 이유가 있다
  (local 추출기 포기 5건, replay의 심어 둔 forbidden 2건·오류 1건).
- 2026-10-21 재측정: 다른 트랙이 작업 트리에서 고친 `src/foundation/local_mail.py`(취소 문구 정규식) 때문에 local의 `cancelled_as_active` 2건(syn-02·syn-14)이 사라졌다.
  그래서 local forbidden 허용은 0(측정 가능한 11개 규칙 기준, 측정 불가 2건은 위 `forbidden_unmeasurable`), micro F1 기준선 0.8018이다.
  그 변경이 빠지면 local 게이트가 실패하는 것이 맞다.
- **replay의 known gap 두 가지**(기준을 느슨하게 둔 것이 아니라 심어 둔 결함·구조적 한계를 그대로 적은 값이다):
  - `forbidden_violations` 2 — 심어 둔 결함 syn-01 `payment_date_as_visit`·syn-13 `change_as_new_confirmed`. `max` 2로 개수를, `expected_failures`(exact)로 사례·규칙을 양방향 고정한다.
  - `must_review_recall` 0.0 — 측정 가능한 3건(mtp-08·mtp-10·syn-13의 `start_local`)이 모두 미충족(0/3)이다. 아래 한계 참고.
- `qa_local`·`qa_replay`·`agent_replay`는 `required: true`다. 결과 파일을 하나라도 빼면 게이트가 실패한다(QA 기준선·허용 목록은 `evals/qa/README.md`).

## live 평가 (대표 실행 — 유료)

```bash
PYTHON_DOTENV_DISABLED=1 TRAVEL_TEST_POSTGRES_DSN= OPENAI_API_KEY=<환경변수로만> \
python -m src.evals.mail --mode live --max-cases 5 --max-cost-usd 0.20 \
  --pricing deploy/render-supabase/pricing-openai.json \
  --out data/eval_runs/mail-live.json [--cases id1,id2] [--record-replay data/replay-live/mail]
```

- **사례 선택** — `--cases`가 없으면 언어 → (AI가 어길 수 있는) forbidden 규칙 → 태그 순으로 겹치지 않게 고른다(결정적).
  5건이면 `mtp-06`(en, 자정 발명) · `syn-12`(es, 결제일) · `syn-04`(ja, 주입) · `mtime-01`(mixed) · `syn-06`(ko)이다. 고른 id는 실행 전 출력과 결과 JSON(`selected_case_ids`)에 남는다.
  5건으로는 변경·취소 사례가 빠진다. 필요하면 `--cases`로 직접 지정한다.
- **원장 보관** — live는 임시 디렉터리를 쓰지 않는다. `--out` 옆 `<이름>-work-<UTC시각>/`(data/ 아래, git 무시)에 `eval.sqlite3`(예약 원장·`llm_calls`)와 `receipts/`를 남긴다.
  결과 JSON의 `ledger`에 예약 상태별 수, 정산 비용, **미정산(unknown 등) 예약의 추정 비용 상한**을 따로 적는다. `cost_usd_total`은 정산된 호출만 더한 값이라 실제 지출보다 작을 수 있다.
- **가격 파일 존중** — 가격 파일이 `halted: true`면 호출 전에 거부한다. 지출 한도는 `min(--max-cost-usd, 파일의 각 한도)`라 파일 한도를 올리지 않는다.
  현재 파일(`user_daily` 0.25 USD)로는 35건 전체(상한 1.295246 USD)가 호출 전에 거부된다. 전체를 돌리려면 운영자가 한도를 올린 가격 파일을 따로 준다.

- `--max-cost-usd`·`--max-cases`·`--pricing` 필수. 실행 전에 예상 상한을 출력하고, 상한이 cap을 넘으면 호출 없이 거부한다. `--dry-run`은 상한만 출력한다.
- 예상 상한 = 사례마다 (첫 호출 + 수리 1회) × (출력 상한 4096토큰 × 단가 + 입력 추정: UTF-8 바이트 + 256, 수리는 이전 출력 최대 16000자×4바이트와 오류 목록 추가).
- 2026-10-21 `--dry-run` 측정(gpt-5-mini, 가격 파일 `openai-mail-2026-10-07-v1` 기준, 호출 0): **분산 선택 5건 0.18513 USD, 35건 전체 1.295246 USD**. 실제 비용은 보통 이보다 훨씬 작다(최악 가정).
- 같은 cap이 임시 DB의 `Budget` 상한(일·월·전역 4종)으로도 들어가, 추정이 틀려도 gateway가 예약 단계에서 막는다(`BUDGET_EXHAUSTED`).
- 키는 환경변수 `OPENAI_API_KEY`로만 받는다. `PYTHON_DOTENV_DISABLED=1`이 없으면 실행하지 않는다(.env를 읽지 않기 위해).
- `--record-replay`로 남긴 기록은 `source: live`다. 수리 호출 기록에는 수리 프롬프트(`mail_extraction_repair`)의 id·hash가 붙는다. 공개 저장소이므로 올리기 전에 내용을 확인한다.

## 한계

- 정답셋 35건은 작다. 필드 하나의 F1이 사례 1건으로 0.02 넘게 움직일 수 있다.
- 근거 없는 원자는 지금 예약번호만 본다(날짜 표기가 언어마다 달라 날짜는 아직 검사하지 않음).
- 같은 점수의 매칭이 여러 개면 알고리즘이 고른 하나로 채점한다.
- live 모드는 구현만 했고 실행한 적이 없다.
- replay·live의 micro F1에는 AI 스키마에 없는 필드의 구조적 0이 그대로 들어 있다(필드별 표에서 구분해 읽는다).
- replay must_review 재현율 기준선 0.0은 측정 가능한 start_local 3건(0/3) 기준이라 더 떨어질 수 없다. 이 지표는 지금 replay에서 회귀를 막지 못한다.
  원인은 replay 기록이다. must_review 충족은 예측 시각이 null이거나, 날짜만 남았거나, review_reasons가 그 필드를 덮을 때뿐이다(`_review_ok`).
  mtp-08·mtp-10의 `gold_oracle` 기록은 정답 시각(13:30, 02:30)을 그대로 내고 review_reasons도 없어서 **oracle replay로는 구조상 충족될 수 없다**.
  syn-13은 변경 요청 시각을 확정 시각으로 쓴 심어 둔 결함이라 미충족이 맞다. 나머지 3건(status 2·party.adults 1)은 AI 스키마에 없는 필드라 측정 불가로 따로 센다.
  replay에서 이 지표를 살리려면 "확인 필요"를 내는 기록(시각 null 또는 날짜만)을 따로 만들어야 한다(미실행).
