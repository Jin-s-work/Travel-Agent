# 고잉 — 브랜드·여백·공개지도 검색 개선 (2026-10-07)

사용자 지정 서비스명은 **고잉**이다. 기존 여정 이름은 역사 문서를 제외한 로그인·내비게이션·브라우저 제목·PWA에서 교체했다. README는 변경하지 않는다. 기존 OpenAI 모델·한도·무료 호스팅·인증·개인 데이터 계약은 유지한다.

| Before | After | Why |
| --- | --- | --- |
| 화살표/원 장식의 여정 아이콘 | 한 획의 G 모노그램과 고잉 워드마크 | 작은 크기에서도 읽히는 단정한 브랜드 |
| 80px 메뉴·152px 바로가기·중복 하단 패딩 | 64px 메뉴·104px 카드·모바일 80px 카드·하단 안전영역 한 번 | 정보 밀도와 터치 영역을 함께 확보 |
| 모바일 홈 제목 아래 분리된 수정 버튼과 반복 날짜 | 제목 옆 수정 버튼, 날짜는 상단 여행 문맥에 유지 | 불필요한 세로 이동 감소 |
| 숙소가 없어도 근처 저녁·거리 조건 노출 | 위치/기존 조건이 있을 때만 관련 조작 노출 | 준비되지 않은 기능을 먼저 요구하지 않음 |
| 같은 지도 실패를 여러 영역에서 반복 | 한 상태 영역, 실패 종류와 재시도 시각 | 원인에 맞는 다음 행동 제공 |
| 다음 예약이 단순 안내문 | 예약 화면으로 가는 다음 예약 행 | 안내를 바로 행동으로 연결 |

## 지도 진단·변경

운영 SQL 사용량을 읽기 전용으로 조사했다. 당일 지도 호출 2건 모두 약3.2초 후 `FETCH_FAILED`였으며, 당일 한도5건에 도달하지 않았다. 운영에는 도쿄/바르셀로나/마드리드의 검수 후보 각3곳만 있고 공개지도 캐시는 없었다. 로컬과 동일 Python3.13 Docker 이미지의 공식 Overpass `/api/status` 연결은200이었다. 따라서 운영 상세 원인을 식별하기 전에 원격 서버 전체 장애나 한도 소진이라고 단정하지 않는다.

- DNS 주소 정렬 때문에 항상 같은 첫 IP에 접속하던 구현을 고쳤다. DNS 순서를 유지하고 HTTP 요청 전 연결 실패에 한해 검증된 다른 DNS 주소에 제한된 연결 시도를 한다. SSRF·연결 대상 IP·TLS 검증·총 기한·본문 상한은 유지한다.
- HTTP429/406,403,502/503/504, TLS 검증, 연결 실패, 응답 중단, 시간 초과를 구분한다. HTTP 거절 후 다른 IP/미러로 우회하지 않는다. 서버 `Retry-After`를 저장하고 공급자 거절은 다른 도시 요청에도 적용한다.
- 일시 연결 실패는2분, HTTP 거절은 최소15분(서버 지시가 더 길면 그 기간)을 기다린다. 실제 남은 초와 UTC 하루 한도 초기화 시각을 반환한다. 오류 수신량 미확인은 null이며350000바이트로 허위 기록하지 않는다.
- 기존7일 유효 캐시·사용자5회/일·전체20회/일·유료API 차단은 보존한다. 만료 자료를 최신 장소로 재노출하지 않는다. 공개지도는 도심3km 후보이며 리뷰·영업·숙소 주변 전수 검색을 보증하지 않는다.
- 공식 운영 지침: [Overpass 자원·한도](https://dev.overpass-api.de/overpass-doc/en/preface/commons.html), [공개 API 운영 정보](https://wiki.openstreetmap.org/wiki/Overpass_API). 무료 공유 서버는 상용 무중단 백엔드의 보장이 아니다.

## 로컬 검증

실행 명령(운영 `.env` 자동 로드 차단):

```
PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m pytest -q
PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m pytest tests/test_public_discovery.py tests/test_discovery_fetch.py -q
node --test tests/*.cjs
node --check web/js/foundation.js
node --check web/js/accommodations.js
PYTHON_DOTENV_DISABLED=1 .venv/bin/python scripts/version_web_assets.py --check
git diff --check
```

전체 Python 1033 pass /15 skip /1 fail: 신규 테스트 자체의7자리 Idempotency-Key가8자리 계약을 위반했다. 시험 입력을 고친 뒤 지도·전송 관련52개 재실행 모두 통과했다. 제품 코드에서 테스트 실패를 숨기지 않았다. JavaScript123개 모두 통과. Python 기존 경고2개, 외부환경 skip15개는 유지된다.

Chrome/macOS 합성 로그인→마드리드 여행→추천3곳·사진 표시→홈 이동을 실행했다. 실제 CSS390px에서 가로 넘침0, 모바일 하단 중복 패딩0 확인. 아직 실물 모바일 시험은 아니다. 운영 검색·배포 결과는 아래에 후속 기록한다.
