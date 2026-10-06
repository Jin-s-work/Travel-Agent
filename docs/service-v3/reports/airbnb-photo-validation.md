# Airbnb 참고 디자인·식당 사진 검증

2026-10-06. 사용자 DESIGN.md는 시각 참고로 적용했다. 자체 브랜드·기존 개인 여행 계약을 유지하며 Airbnb 상표·로고·전용 폰트·숙소 사진을 복제하지 않았다.

## 구현

- 화이트/잉크/코랄 #E00B41의 단일 의미 토큰, 검정 선택 날짜·필터, 가벼운 카드·밑줄 탭·줄인 구분선. 밝은 CTA의 흰 글자 대비4.89:1. 다크 모드는 차콜/로즈. PWA 아이콘·theme·캐시v18까지 일치.
- 실제 식당 사진을 카드와 상세에 최대2장. 고정4:3 영역, 첫 장 lazy·둘째 조작 시 요청, 화살표/방향키·44px, 원출처·저작자·라이선스·촬영일·잘림 안내. 한 장 실패는 다른 장 유지, 모두 실패 시 출처 안내. 이미지가 없는 장소는 일반 음식/AI 사진으로 채우지 않는다.
- 검증된 음식 태그·날짜별 조건·사진이 먼저 보이도록 정리했다. 중복 안내를 줄이고 부가 행동을 더보기로 옮겼다. 필수 미확인·잔여석 미확인은 보존한다. 원자료 확인과 순위 근거 확인을 구분한다.
- 100도시 목록의 한국어 이름으로 상단과 홈을 표시한다. 여행 원값이나 사용자의 제목·도시를 바꾸지 않는다.
- 사진의 지점·라이선스·만료·현재 출처 권한을 별도로 검사한다. 기존 추천 GET에도 현재 사진만 붙이고 순위/snapshot/job은 바꾸지 않는다. 개인 데이터·DB schema12는 변경하지 않는다.

사진 출처와 범위는 [출처 보고서](restaurant-photo-sources.md), Before/After/Why는 [디자인 시스템](../DESIGN_SYSTEM.md).

## 실행과 결과

```sh
PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m pytest -q
node --test tests/*.cjs
python3 scripts/version_web_assets.py --check
git diff --check
```

최종 Python **922 passed /12 skipped /2warnings /111.68s**. Node **77passed /191.11ms**. skip12는 PostgreSQL 전용 cloud11개·별도upgrade1개. 경고2는 기존Starlette/Authlib httpx 호환 종료 예정 안내다.

```sh
PYTHON_DOTENV_DISABLED=1 TRAVEL_TEST_POSTGRES_DSN="$DISPOSABLE_LOOPBACK_DSN" \
 .venv/bin/python -m pytest -p tests.postgres_plugin \
 tests/test_restaurant_photos.py tests/test_recommendation_api.py \
 tests/test_discovery_foundation.py tests/test_catalog_registration.py -q
```

임시pgvector/pg17 **85passed /2warnings /51.04s**. 매시험 격리schema를 삭제한다. 운영 Supabase를 시험 DB로 사용하지 않았다. 이 시험 집합은 전체와 중복되므로 통과 수를 합산하지 않는다.

초기 전체 실행은921pass/12skip/1fail이었다. 공개 manifest를 docs/data에 COPY한 설정이 비공개 data 제외 검사에 걸렸고 .dockerignore에도 포함되지 않는 문제를 발견했다. 단일 정본을 src/discovery/restaurant_photos.json으로 옮겨 기존 COPYsrc로 포함했다. 제외 규칙을 완화하지 않고 위 최종 전체 시험을 다시 통과했다.

## 실제 브라우저

Chrome/macOS, 합성 OIDC 계정·여행 + 공식 공개 식당 자료를 사용했다. 로그인→추천의 실제 단계→사진1/2·방향키→상세→저장 성공→두 장소 비교→새로고침 복원. 운영용 코드에 지연을 넣지 않았고 기존 로컬 fixture만 진행 관측용 지연이 있다.

- 공개 갤러리에서13장 지점·내용을 화면 대조했고 앱에서 실제 이미지 naturalWidth960을 확인했다. 카드는 아직 열지 않은 두 번째사진의src가 비어 있어 미리 다운로드하지 않는다.
- 실제 viewport390px 및319px에서 가로 넘침0. 319px/글자200%(body30px,h1 56px)의 상세 모달252px/content252px,사진212px. Escape 후 상세버튼으로포커스복귀. 다크차콜/로즈 토큰 적용. 모바일viewport도구의스크롤후단일캡처가백지로출력되는한계가있어DOM기하·조작과전체페이지/초기화면검수를구분했다. 실물모바일·Safari는미검증이다.
- 사진 오류/철회/만료·다른 지점·작성자누락·2장제한·중복·오프라인/SW미캐시는 자동시험으로 검증했다. 브라우저의 외부 CDN 장애를 강제로 만들지는 않았다.

## 비용·배포·복구

추가 유료 API0. 기존 RenderFree/Supabasehii와 지도·경로·엄격리뷰OFF 유지. 새 외부 식당 검색은 이번 범위가 아니다. 3도시9곳 중7곳13장사진이며 나머지97도시 실제 후보 확보와 별개다.

개인 DB 스키마와 장소 사실은 변경하지 않았다. 운영 검증용 추천 실행은 아래 별도로 기록한다. 이전 런타임 3a78fdf로 이미지 rollback할 수 있다. 사진만 끄려면 src/discovery/restaurant_photos.json의 각 사진 enabled를 false로 바꿔 재배포한다. 기존 예약 조회·추천 순위에는 영향을 주지 않는다. 비밀 설정 변경은 없다. 배포 결과는 진행기록의 후속 운영반영 절에 기록한다.

## 배포 기동 진단 (2026-10-07)

- `60fedf8`의 `dep-db2gn717lnhs73et6l60`과 재시도 `dep-db2gqbvlk1mc73bcentg`가 `startup_failed`로 종료됐다. 기존 `3a78fdf`는 계속 Live이며 ready/live는 200, schema12와 자료 건수는 유지됐다. 새 UI 성공으로 보고하지 않았다.
- 두 번 모두 Authlib import 안내 후 약 10~11초에 발생했다. 이 변경의 사진 읽기는 조회 시점에 실행되므로 기동 원인으로 단정하지 않는다. 실제 예외가 기존 로그에서 생략돼 비밀값 없는 진단을 추가한다.

- 진단 보완 commit `5415b1f` / `dep-db2gtes9v7es73c4inv0`는 **2026-10-07 00:09:04 KST Live**. 설정·기동 검증을 완화하지 않았으며 앞선 실패의 정확한 원인은 확정하지 못했다. 새 진단은 예외 클래스·검증된 SQLSTATE·프로젝트 코드 위치만 기록한다. 관련 시험16개, 마지막 진단 단독11개 통과.
- 라이브 HTTPS ready/live200, 개인API401, JS/CSS/SW5종 해시 일치. 배포 직후 사용자·여행·예약·원문·숙소·tombstone·후보 수가 배포 전과 같았다.
- 실제 세션에서 추천 1회 실행 후 마드리드3곳 및 카드/상세 사진을 확인했다. 사진2장 naturalWidth960, 새 실행1건은 성공/시도1회. 처리49.7초(직전 같은 도시 실행52.1초)이며 전후1회 관측이므로 성능 개선 근거로 사용하지 않는다. 유료 공급자 실행 없음.
- 이 검증에서 완료 직후 결과 없는 응답에 자동조회가 멈추는 경계 상황을 발견했다. 버튼으로 저장된 요청을 확인하면 정상 복원됐으며, 추가 사용자 행동 없이 같은GET을 제한적으로 다시 읽도록 후속 보완한다.

- 완료 응답과 결과 행 조회의 시점 차이는 같은run GET을 최대3회 자동 재조회해 복구한다. 새POST/새job은 만들지 않는다. 여행·탭·직렬 요청 변경/자료 stale는 중단하며, 상한 이후에만 수동 확인을 제공한다. 복구 중 진행 표시와 중복 제출 방지도 유지한다. 신규 회귀5개 포함 Node전체 **82passed /203.34ms**. 공개 캐시v18 및 자산 해시 갱신.

- 후속 UI commit `cece6ad`의 `dep-db2h1h2jnfac73cr7670`는 `DeadlockDetected` / `40P01` / `postgres.py:_migrate:218`로 실패했다. 새 진단으로 기존 테이블에 매번 RLS를 다시 적용하는 DDL과 이전 서버 작업의 잠금 충돌임을 확인했다. 앞선 두 실패가 동일 원인이었는지는 당시 상세 진단이 없어 확정하지 않는다. 권한을 완화하지 않고 불필요한 RLS DDL을 건너뛰며, 필요한 DDL은 기존 쓰기 작업과 직렬화하는 후속 수정을 검증한다.

- 수정 후 임시 PostgreSQL에서 기존 writer와 동시 기동3시험(2.39초), 클라우드 저장/안전 진단22시험(9.69초), 실제 schema11→12/사진34시험(5.72초) 모두 통과. 총59개는 앞선 시험 일부와 중복된다. 기동은 WRITE→MIGRATION 잠금을 DDL 전에 확보하며, 이미 RLS가 켜진 테이블의 ALTER는 생략한다. 새/누락 테이블 RLS와 브라우저 역할 권한 회수는 그대로 검증했다. 전용 임시 컨테이너는 삭제했고 운영 DSN은 시험에 사용하지 않았다.
