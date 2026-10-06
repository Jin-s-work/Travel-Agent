# 여정 — 이름·디자인·식당 사진·100도시 탐색 검증

2026-10-07. 첨부 Airbnb DESIGN.md는 시각 참고 자료로 사용했다. 별도 명령으로 실행하지 않았고 Airbnb 상표·전용 글꼴·사진을 복제하지 않았다. 기존 개인 여행 데이터와 저장 키를 유지한다.

## 구현

- 제품명 **여정**. 로그인·브라우저 제목·PWA·아이콘·본문 안내에 적용. 기존 URL, 저장소 이름, 개인 브라우저 namespace와 외부 OAuth 설정은 유지한다.
- 따뜻한 흰색 `#FAF9F6`, 잉크 `#252B2F`, 짙은 청색 `#25485A`. SUIT Variable v2.0.5 원본 WOFF2 자체 호스팅, OFL 라이선스 포함. 흰 글자/강조색 대비9.75:1. 다크·큰 글자·reduced motion 토큰 유지. [디자인 원칙과 Before/After/Why](../DESIGN_SYSTEM.md).
- 반복 홍보 문구·완료 패널·방문 기준 안내를 줄이고 사진과 기본 행동을 우선한다. 카드의 중요 미확인·실시간 잔여석 미확인은 유지하고 추가 정보는 펼치기. 빈 이유 펼침 제거. 장소 찾기→상세 보기→저장 문구 통일.
- 사진 `kind` 음식/실내/외관/기타, 최대3장, 음식·실내 우선, 같은 Commons 원본 중복 차단. 첫 사진만 lazy 요청, 나머지는 조작 시 요청. 좌우 버튼·키보드·수평 스와이프, 촬영일/출처/라이선스, 사진별 오류 복구. 개인 offline bundle에는 사진을 포함하지 않는다.
- 실제 검수 사진 **7곳19장**(음식4·실내8·외관7), **5곳3장 /2곳2장**. 2곳은 사용 가능한 사진 미확보. 다른 지점·무허가·임의 대체 사진을 채우지 않는다. 과거 촬영 사진임을 표시한다. [사진별 원출처와 제외 이유](restaurant-food-interior-photos.md).
- 등록된100도시에 무료 공개 지도 검색 연결. 공식 후보가 없는 도시의 도심3km 내 식당·카페를 요청 시 조회한다. 숙소 위치를 외부로 보내지 않으며 도시 전체/숙소 주변 전체를 조사했다는 의미가 아니다. 공식 검수 후보3도시9곳 유지. [제공자·설정·한도·라이선스](public-discovery.md).
- 공개 지도 후보는 방문 전 확인/점수null. 영업·평점·리뷰·예약을 만들어내지 않는다. 자료/필터/제외를 적용한 뒤12곳까지 표시한다. 새로고침·입력 타이핑은 외부 조회를 실행하지 않는다. 도심 캐시7일, 사용자5회/UTC일·전체20회/UTC일·15초 간격. 유료 공급자 OFF/기존예산0 유지.
- DB schema12 유지, DDL 없음. SW public-shell-v19·해시 갱신·self-host font만 공용 cache에 추가. 개인 API와 원문은 캐시 제외.

## 실행 결과

```sh
PYTHON_DOTENV_DISABLED=1 PUBLIC_DISCOVERY_ENABLED=0 .venv/bin/python -m pytest -q
node --test tests/*.cjs
python3 scripts/version_web_assets.py --check
git diff --check
```

전체 Python **958 passed /15 skipped /2 warnings /114.68s**. skip은 외부 PostgreSQL 환경 의존11·migration동시성3·upgrade1이며 별도 임시PG 검증과 구분한다. 경고는 기존 Starlette/Authlib의 httpx 호환 종료 예정 안내. Node 최종 **95passed /216.89ms**. 기존 웹 제목 시험의 Travel 기대값을 여정으로 갱신했다. 실패 이력은 이름 검사 수정 전이며 운영 코드 오류를 숨기기 위해 시험을 제거하지 않았다.

```sh
PYTHON_DOTENV_DISABLED=1 PUBLIC_DISCOVERY_ENABLED=0 \
 TRAVEL_TEST_POSTGRES_DSN="$DISPOSABLE_LOOPBACK_DSN" \
 .venv/bin/python -m pytest -p tests.postgres_plugin \
 tests/test_restaurant_photos.py tests/test_recommendation_api.py \
 tests/test_discovery_foundation.py tests/test_catalog_registration.py -q
```

임시 PostgreSQL17/pgvector **94passed /2warnings /48.38s**. 운영 Supabase에 합성 데이터를 쓰지 않았다. 마지막 검토에서 카테고리/제외/출처보다 먼저12개를 제한하던 문제를 수정했다. 이후 공개 검색 최종 **SQLite20passed, PostgreSQL20passed**, 기존 추천·보관함 **45passed**. 카페가13번째에 있어도 찾고, 제외·철회 후보 이후 다른 후보를 보충하며 조건 불일치를 공급자 장애로 오표시하지 않는다. 집합들은 중복되므로 합산하지 않는다.

## 브라우저·실제 자료

Chrome/macOS에서 합성 OIDC/여행과 실제 검수 식당 자료를 사용했다. 파리 여행 생성→공개지도 실제 조회→11개 확인 필요 후보 표시→Chez Marianne 상세→보관함 저장→새로고침 후 여행 유지 확인. 공개지도 원문 결과는 별도 공급자 smoke에서 파리/런던/서울 각60개를 수신했다. 뉴욕1회는 HTTP_UNAVAILABLE로 실패했다. 100도시 전체를 실조회하거나 영업/맛집 품질을 검증한 것은 아니다.

최종 마드리드 화면은 검수3곳만 반환하며 불필요한 공개지도 호출을 생략했다. 카드 첫 사진3장 naturalWidth960, 둘째/셋째는 src 미지정 상태를 확인했다. Casa Lucio1→2→3→1을 버튼/방향키로 조작했고 유형·날짜·사진이 바뀌면서 펼친 출처 상태가 유지됐다. 사진19장은 별도 Chrome 검수21개 후보 중 지점/권리 조건을 통과한 자료다.

- SUIT font 로드 확인. 동일 CSS viewport2322에서 첫 카드 위치1397→975px로 약422px 줄어듦.
- 실제 CSS960px에서2열,390px에서1열·가로넘침0. 사진 버튼 약44×44px. 390px 상세clientWidth=scrollWidth350px, 사진310.5px. Escape 후 상세 버튼으로 포커스 복귀.
- 현재 Chrome 연결의 viewport override는 설정값과 CSS 폭이1.5배 차이여서 실제 innerWidth로 검증했다. 작은 화면의 캡처가 일부 잘리는 도구 제약이 있어 모바일 결과는 DOM기하/조작 검증이며 완전한 시각 검수나 실기기 검증으로 주장하지 않는다. 실물 iOS/Android·Safari·터치 스와이프는 미검증; swipe의 방향·취소·다중pointer·수직scroll 예외는 자동 시험이다.

## 배포·운영

코드 준비/로컬 검증 완료. 기존 Render Free + Supabase Free에 수동 배포 후 아래에 실제 revision·상태를 추가한다. 새 계정·유료 서비스·증설·지인 메시지 발송 없음. 외부 API 유료 호출0. 공개 지도 검색은 제한된 무료 보조 경로이며 엄격 리뷰 언어 기능·실시간 잔여석·유료 경로 조회·모든 도시 사진을 지원한다고 표시하지 않는다.

복구: 기존 런타임801922d로 Render 이미지 rollback 가능. 새 무료 검색만 끄려면 PUBLIC_DISCOVERY_ENABLED=0 후 재배포한다. 캐시와 저장한 장소는 기존 권한/유효기간 검사를 거쳐 읽을 수 있다. 이번 변경은 DB 복원이나 사용자 데이터 삭제가 필요 없다.
