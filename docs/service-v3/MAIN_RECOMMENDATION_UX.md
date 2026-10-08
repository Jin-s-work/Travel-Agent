# 메인 추천과 AI 접근 개선 — 2026-10-08

추천을 보려고 다시 조건을 입력하거나, 예약 질문을 하려고 더보기를 여는 부담을 줄인다. 저장된 여행 조건과 추천 결과를 재사용하며 엄격한 리뷰 자격은 유지한다.

| Before | After | Why |
| --- | --- | --- |
| 홈에서 예약 준비 행동만 먼저 보임 | 최근 추천 3곳, 추천 전체 보기, 상세·바로 저장 | 핵심 기능을 여행 선택 직후 사용 |
| AI 질문이 더보기에 있음 | 주 메뉴의 AI 대화 + 홈 질문 입력 | 예약 질문을 한 번 제출해 대화와 출처로 연결 |
| 저장한 장소에도 계속 저장 버튼 | 저장 중 중복 차단, 저장됨 → 보관함 | 처리 결과와 다음 행동을 명확히 표시 |
| 도심 공개 후보에 취향이 잘 반영되지 않음 | 출처·기한을 통과한 공개 음식 종류를 잠정 취향 자료로 반영 | 임의의 메뉴·식단 적합성 추정 없이 순서 개선 |
| 출발점 없으면 ID 순서로 동률 | snapshot에 고정한 도심 기준 직선거리로 정렬 | 추가 입력 없이 기본 순서에 의미 부여 |
| 한 사진 출처의 403/429가 모든 출처를 막음 | 호스트별 15분 대기, 선택적 Wikidata 실패 시 확보한 OSM 파일 링크 유지 | 같은 서버의 제한은 존중하면서 독립 출처의 정상 조회 유지 |

## 구현

- `web/js/home.js`: 현재 여행의 저장된 결과만 읽는 추천 홈. 렌더링만으로 새 추천·유료 AI 요청을 만들지 않는다. 동일 카드 렌더 생략, 여행/로그아웃 시 입력 초기화, 질문 실패 시 입력 보존.
- 홈 질문은 기존 `/ask`를 사용한다. 예약 메일·수동 예약에 대한 근거 답변이며, 새 식당이나 예약 가능 여부를 만들어 내는 범용 상담으로 표현하지 않는다.
- 사진은 최대 3장 및 기존 이용 조건·지점·만료 검사 유지. 기존 무료 사진 작업은 보이는 후보만 처리하고, 화면/계정 변경으로 과거 응답이 덮어쓰지 않게 한다.
- Wikimedia 요청은 프로젝트 연락 URL을 가진 식별 가능한 User-Agent를 사용한다. 브라우저 위장·프록시·제한 우회는 사용하지 않는다.
- `hybrid_v5`: 결정적인 비학습 콘텐츠/거리 모델. v4는 별도 보존해 이전 입력 재현 가능. 같은 입력·후보·출처·clock에서 같은 결과, 추가 공급자 호출 0회.
- 기존 TF-IDF 취향, 플랫폼별 평점 보정, 거리 성분을 유지하고 한국어/일본어/스페인어 음식 별칭을 확장했다. 공개 cuisine은 허용·유효한 provisional 원자료만 사용하고 표시에도 잠정임을 명시한다.
- 숙소/출발점 좌표가 있으면 그 위치를 사용한다. 출발점을 아예 고르지 않은 경우만 GeoNames 도심 좌표·버전·출처를 snapshot에 저장한다. 사용자가 고른 위치가 미확인이면 도심으로 조용히 대체하지 않는다. 모두 직선거리이며 도보 시간/숙소 주변 검색 반경이라는 주장은 하지 않는다.
- 현지어 리뷰, 유명한 곳, 참고 장소의 자격 검사와 폐업/인원 위반 제외는 유지한다. 취향·거리 점수로 자료 자격을 상쇄하지 않는다. 원자료 없는 언어 비율과 평점을 생성하지 않는다.

## 검증

격리 checkout: `/private/tmp/going-hybrid-verify.uPktYX`.

```sh
PYTHON_DOTENV_DISABLED=1 DATABASE_URL= /private/tmp/going-stage1-native313/bin/python -m pytest -q
node --test tests/*.cjs
python3 scripts/version_web_assets.py --check
```

- Python 전체: **1,183 passed / 17 skipped**, 144.65초. 외부 키/환경을 요구하는 skip과 라이브 검증을 구분한다. 기존 httpx 의존성 deprecation 경고 2개.
- Node: **194 passed**. 홈 질문/실패 입력, 저장 중복·여행 전환, 이전 결과 복구, 사진 fallback, 모바일 관련 기존 논리 검증 포함. 실제 모바일 브라우저 검증과는 다르다.
- 신규 모델/사진 집중 검증: **49 passed**. 실제 사용자 추천 정확도를 측정한 값이 아니다.
- PostgreSQL 및 배포 UI: 아래 후속 실측 기록에서 결과를 추가한다.
- 실제 공개 OSM 지점 `node/677610918` → Wikidata 지점 좌표 대조 → Commons 사진 메타데이터 1장 확인. 3회 공개 호출, 명시된 CC BY-SA 2.0·작가·지점 링크 확인. 이는 로컬 메타데이터 검증이며 배포 이미지 표시를 대신하지 않는다.

## 남은 데이터 범위

무료 공개지도 후보는 사진이나 음식 태그가 없는 경우가 많다. 개선한 요청 처리만으로 모든 식당의 사진 3장을 확보했다고 보장하지 않는다. Google Places Photos 추가 연동은 키·예산·표시 정책을 별도로 설정해야 하며 이번 변경은 유료 공급자를 켜지 않는다.

공식 문서 확인:
- [Wikimedia User-Agent policy](https://foundation.wikimedia.org/wiki/Policy:Wikimedia_Foundation_User-Agent_Policy)
- [MediaWiki API etiquette](https://www.mediawiki.org/wiki/API:Etiquette)
- [Google Places Photos](https://developers.google.com/maps/documentation/places/web-service/place-photos)
- [Google Places 사용 정책](https://developers.google.com/maps/documentation/places/web-service/policies)

정확도/만족도는 실사용 평가 전이므로 미측정이다. 입력 절약과 후보 정렬 계약 개선을 추천 품질 실측 상승으로 표현하지 않는다.
