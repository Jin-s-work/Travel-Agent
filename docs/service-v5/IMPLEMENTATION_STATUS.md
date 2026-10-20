# 고잉 v5 진행 기록

최종 갱신: **2026-10-08**. 1단계 흐름을 재사용해 2단계 후보 확보·관측 계약·사진 상태·모델 평가 도구를 구현했다. **기본 모델은 hybrid_v5, 실제 리뷰 공개는 OFF**다. 실제 도시별200곳 확보, 실제 언어/추천 정확도, 로그인 이후 브라우저, 운영 배포는 완료로 표시하지 않는다.

## 단일 최신 상태

| 영역 | code | synthetic / 자동 시험 | browser | live_provider | rights / quality / city_enabled |
| --- | --- | --- | --- | --- | --- |
| 1단계 여행→탐색→저장→일정 | 완료, 기존 context·12개 cursor·preview/apply/undo 재사용 | 전체 회귀 포함 통과 | 로그인 페이지만 확인, 이후 차단 | 이번 외부 예약/AI 호출 없음 | 소유권·삭제·고정 예약 보호 유지 |
| A 최대200 후보 | 공개 지리 분할·raw400·deadline·checkpoint·pool 전체 평가·고정 결과 GET | 4조회/240수신→200평가,37고유,마지막 최적 후보/추가 조회0 통과 | 후속 흐름 미검증 | 실제 저장 도쿄50/바르셀로나3, 새 지도 호출0 | 공개 샘플 범위. 숙소 좌표 외부전송0. 실제200곳/도시는 미확보 |
| B 리뷰 관측 | strict와 bounded 연구 계약 분리, admin UI·도구 준비 | 분모·날짜·프로필·복구·한도·철회 통과 | 관리자 UI 미검증 | Apify 계정/현재 가격 read-only 확인, 새 run0 | 정책0·계약0·연결0·실제 품질평가0. strict/B consumer OFF |
| C 사진 |7상태·지점/권리/만료·최대3장·lazy load 연결 | Python/DOM 회귀 통과 | 실제 img load 성공률 미측정 | 현재 허용 도쿄2곳5장/바르셀로나2곳6장 | 추가 유료 공급자 OFF. 사진 없는 장소에 대체 이미지를 넣지 않음 |
| D 추천 모델 | v6 실험/optional shadow, v5 기본 유지 | 고정 입력12합성 개발질의·회귀 통과 | 기존 카드 UI 재사용, 미검증 | 추가 model/embedding 호출0 | 실제 사람 라벨0·holdout0, 실제 향상 미측정. 자동 승격 없음 |
| 동시 사용/내구성 | 원점의 무관한 여행version 충돌 수정, 기존 작업/예산 사용 | 합성5명/10job,오류0·재시도0, SIGKILL/fencing/예산 경합 기존 회귀 포함 | 전체5인 브라우저 미실행 | 실측은 로컬 fake. Render cold start 미측정 | 계정·삭제·과금 unknown 보호 유지 |
| 접근성/출시 | 기존 토큰/디자인 유지 | DOM harness 통과 | **390px/200%/키보드/로그인 이후 E2E 미검증** | 운영 Google 로그인 미검증 | 출시 검증 완료 판정 불가 |
| 배포 / GitHub | 이번 요청에서 실행하지 않음 | 해당 없음 | 운영 반영 없음 | 새 지출/계정/충전 없음 | README·발표·대본 수정 없음 |

## 실제 시험 결과

- native 전체 Python **1264 passed / 17 skipped**,187.88초. 별도 PostgreSQL/pgvector 등 환경 시험은 미실행.
- native 및 원본 전체 JavaScript **각219 passed / 0 failed**.
- 마지막 보완 후 영향 범위 Python **89 passed**(후보·관측·계약·복구·언어·파일럿 상한), **64 passed**(모델·원격정리·숙소·5인 동시 작업).
- 원본 반영 후 핵심 API/추천/관측/사진/1단계 연결 Python **138 passed**,128.80초. 공개 자산 hash 및 변경 범위 `git diff --check` 통과. 코드·시험·보고서54파일이 native/원본에서 일치한다.
- 원본에서는 전체 Python을 다시 실행하지 않았다. native 전체 시험이나 원본 회귀 통과를 운영 배포 성공으로 해석하지 않는다.
- 실제 read-only 데이터 확인: 도쿄 식당50,바르셀로나 식당3, 실제 리뷰 집계0, 허용 사진4곳11장. 새 리뷰/지도 수집0회.
- 사용자5명 합성 동시 시험: 저장 결과 GET p95 614.35ms, 큐 p95 3378.73ms, peakRSS 약336.7MiB. 네트워크 미모사/cooldown 가속이므로 운영 성능이 아니다.

## 산출물

- [2단계 구현·명령·시험·롤백](reports/STAGE2_IMPLEMENTATION.md)
- [리뷰 관측 ADR·승인/파일럿 조건](reports/REVIEW_OBSERVATION_ADR.md)
- [실제 후보·리뷰·사진 범위](reports/DATA_COVERAGE.md)
- [모델/언어 평가·블라인드 검토·측정](reports/RECOMMENDATION_EVALUATION.md)
- [안전한 실측 요약 JSON](reports/stage2-measurements.json)
- [1단계 구현 기록](reports/STAGE1_IMPLEMENTATION.md) — 당시 상태와 실패/수정 이력 보존

## 다음 실행 조건

1. 브라우저 도구가 저장한 `http://127.0.0.1:8767` 차단을 사용자 설정에서 해제한 후 같은 합성 fixture로 전체 UI 검증. 도구에는 이 설정을 바꾸는 API가 없어 직접 해제하거나 다른 경로로 우회하지 않았다.
2. 실제 지점·이용/원격 보관 정책·공급자 계약·현재 단가/기존 승인 한도를 마련한 뒤 1×40→3×200→10×200 파일럿. 현재 실행 상한은0이다. 제안$1은 새 지출 승인이 아니다.
3. 독립 원문 언어 라벨과 두 도시60질의 목표의 추천 평가. 실제 증거 전에는v5/리뷰OFF 유지.
4. disposable PostgreSQL 회귀와 운영 환경 확인. 배포/GitHub push는 이번 단계에 포함하지 않았다.

## 기록 원칙

이 표만 최신 상태로 갱신한다. 과거 보고서는 당시 실행 사실을 보존한다. code / synthetic / browser / live_provider / rights / quality / city_enabled를 합치지 않는다. 후보200곳과 지점별 리뷰200건은 별도 상한이다. 분모0·자료 없음은 미측정이며 합성 데이터나 목표값으로 채우지 않는다. 사용자 A/B 자료 분리·삭제 부활 방지·고정 예약·비용 보호는 어떤 기능 플래그로도 약화하지 않는다.
