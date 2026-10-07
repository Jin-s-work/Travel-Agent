<div align="center">

<img src="docs/banner.svg" alt="고잉 — 예약 메일부터 하루 일정까지" width="100%">

**현지어 리뷰로 찾거나, 유명한 곳을 고르거나. 내 여행의 일정으로 이어집니다.**

<p>
<img src="https://img.shields.io/badge/Python-3.13-25485A?style=flat-square&logo=python&logoColor=white" alt="Python 3.13">
<img src="https://img.shields.io/badge/FastAPI-API%20%2B%20PWA-315D6B?style=flat-square&logo=fastapi&logoColor=white" alt="FastAPI와 PWA">
<img src="https://img.shields.io/badge/Supabase-PostgreSQL%20%2B%20Storage-3F706F?style=flat-square&logo=supabase&logoColor=white" alt="Supabase">
<img src="https://img.shields.io/badge/Search-pgvector%20%2F%20Chroma-45646D?style=flat-square" alt="검색 저장소">
<img src="https://img.shields.io/badge/OpenAI-gpt--5--mini-25485A?style=flat-square&logo=openai&logoColor=white" alt="OpenAI gpt-5-mini">
</p>

**[서비스 열기](https://travel-inbox-rag.onrender.com)** &nbsp;·&nbsp;
[화면으로 보기](#사용-흐름) &nbsp;·&nbsp;
[작동 원리](#작동-원리) &nbsp;·&nbsp;
[평가 결과](#평가-결과) &nbsp;·&nbsp;
[발표 자료](docs/presentation/README.md)

<br>

<img src="docs/screenshots/going-home.png" alt="고잉 홈 — 현재 여행의 도시·기간과 예약 업로드, 장소 탐색, 일정 만들기" width="100%">

</div>

> **소수 지인용 비공개 베타 · 2026-10-07 기준**
>
> 초대된 Google 계정으로 사용합니다. Render Free에서 실행하고 Supabase에 데이터를 보관합니다.
> 무료 서버의 첫 기동과 장소 검색에는 대기가 있을 수 있습니다.
> **v4 코드·시험과 운영 배포는 별도입니다. 엄격 언어 추천은 아직 OFF입니다.**
> [최신 구현·검증 기록](docs/service-v4/IMPLEMENTATION_STATUS.md) · [기존 배포 기록](docs/service-v3/reports/going-design-search-validation.md)

## 개요

여행을 준비하면 항공권, 숙소, 투어의 확인 메일이 서로 다른 곳에서 옵니다. 예약은 끝났지만
체크아웃 시간이나 환불 규정을 찾으려면 메일함을 다시 검색해야 합니다. 식당을 정할 때는
지도에서 위치를 보고, 예약 방법을 확인하고, 이미 잡힌 일정과 겹치지 않는지 다시 비교합니다.

고잉은 이 정보를 **여행 하나를 중심으로 이어서 사용하기 위해** 만들었습니다. 예약 메일을
구조화하고 출처와 함께 답하는 기능에서 시작해, 장소 탐색·비교와 일정 생성·편집까지 확장했습니다.
도시와 날짜는 다음 화면에서 이어받고, 숙소나 세부 조건은 필요할 때 추가합니다.

장소를 고르는 기준도 나눴습니다. **현지어 원문 리뷰가 충분하고 한국어가 적게 관측된 곳**을
찾는 방식과, **유명한 식당·대표 명소**를 찾는 방식입니다. 리뷰의 언어를 주민 비율로 바꾸어
설명하지 않으며, 원문과 이용 범위·언어 품질을 검증한 자료만 엄격 추천에 사용하도록 설계했습니다.

AI가 읽은 값은 사용자가 확인하고 고칠 수 있습니다. 영업시간·이동시간·예약 가능 여부를
확인하지 못했다면 미확인으로 남깁니다. 예약의 소유권, 날짜 조회, 일정 충돌, 비용 한도처럼
정확한 검사가 필요한 부분은 서버 코드로 처리합니다.

## 사용 흐름

**여행 만들기 → 예약 정리·질문 → 장소 탐색·비교 → 일정 생성·편집**으로 이어집니다.
메일 없이 수동 예약이나 장소 탐색부터 시작할 수도 있습니다.

아래 화면은 v4 변경 전인 2026-10-07에 **합성 계정·가상 메일·예약**으로 촬영한 기존 화면입니다.
이번 두 추천 탭의 브라우저 검증 증거는 아니며, 새 UI의 자동 시험과 구분합니다. 식당은 공개 출처가 있는
실제 지점입니다. 촬영용 메일은 무료 기본 분석을 사용했으며, 운영 OpenAI 검증과 구분합니다.
사진의 저작자와 라이선스는 [자료 출처](docs/presentation/CREDITS.md)에 기록했습니다.

### 1. 메일을 올리고 예약을 확인합니다

`.txt`·`.eml`에서 날짜·시간·장소를 추출합니다. 한 메일의 여러 예약과 왕복 항공 구간을 나눠
보관하고, 파일마다 실제 분석 단계와 실패 이유를 보여줍니다. 새로고침 후에도 기존 작업을 확인할 수 있습니다.

![메일 업로드와 파일별 처리 상태](docs/screenshots/going-mail.png)

추출값과 사용자 교정값을 분리합니다. 날짜만 있는 정보에 임의의 시각을 넣지 않고,
재분석이 실패했을 때는 이전 활성 예약을 유지합니다. 직접 입력한 예약도 같은 여행에서 관리합니다.

<details>
<summary>예약 상세와 교정 화면 보기</summary>

![가상 호텔 예약 상세 — 추출값과 확인 필요 상태](docs/screenshots/going-booking-detail.png)

</details>

### 2. 질문하고 근거를 확인합니다

“2일차 예약을 전부 알려줘”는 해당 날짜의 예약을 SQL로 조회합니다. 의미가 비슷한 문서 몇 개로
결과를 자르지 않으며, 수동 예약도 포함합니다. 환불 규정처럼 본문의 설명이 필요한 질문은
검색 근거를 사용하고 원문을 연결합니다.

![날짜별 질문 — 가상 예약 5개와 근거 보기](docs/screenshots/going-ask.png)

### 3. 장소를 살펴보고 비교합니다

**현지어 리뷰로 찾기 / 유명한 곳**과 **음식점 / 카페 / 볼거리**를 독립적으로 선택합니다. 카드에서
사진·추천 이유·중요한 미확인을 보고, 상세에서 운영·예약·가격의 출처를 확인합니다.
같은 방문일·인원 조건으로 서로 다른 최대 3곳을 비교할 수 있습니다.

취향·예산을 비워도 확인된 근거로 정렬하는 일반 모델을 사용합니다. 선택 입력이 없다는 이유로
추천을 0건으로 만들거나, 미확인 점수를 좋은 값으로 채우지 않습니다. 공개지도 후보는
**주변 장소 참고하기**로 분리하고 엄격 추천의 부족한 개수를 대신 채우지 않습니다.

위치가 확인된 숙소를 기준으로 거리 조건을 적용할 수 있습니다. 직선거리와 실제 경로 시간은
구분하며, 모든 장소에 사진이나 방문 가능 정보가 확보되어 있다고 가정하지 않습니다.

<img src="docs/screenshots/going-discovery.png" alt="마드리드 실제 식당의 실내·음식 사진과 방문 전 확인 사항" width="780">

### 4. 확정 예약을 지키면서 일정을 편집합니다

고정 예약을 먼저 배치하고, 후보를 넣을 때 이전·다음 장소의 이동과 체류·영업·준비 시간을
함께 검사합니다. **미리보기 → 적용 → 되돌리기**로 변경 영향을 확인합니다.
고정 예약끼리 충돌하면 자동으로 옮기지 않고 충돌 사유를 보여줍니다.

![고정 예약과 이동·준비 시간 충돌을 표시한 합성 예시 일정](docs/screenshots/going-itinerary.png)

이 화면은 충돌과 미확인을 보여주는 예시이며 검증 완료 일정이 아닙니다.
일정에서 항목을 지워도 외부 예약이 취소되지는 않습니다.

### 여행 전후에 사용하는 기능

| 기능 | 제공하는 내용 |
| :--- | :--- |
| 장소 보관함 | 지도·공식 링크·이름·개인 메모 저장, 지점 식별 상태 구분 |
| 예약 준비 | 예약 할 일·오픈 규칙·ICS 다운로드·문의문 초안 |
| Plan B·오늘 보기 | 영향을 받는 구간의 대체안, 현지 날짜 기준 다음 일정 |
| 피드백·오류 신고 | 추천 제외와 방문 경험 분리, 사실별 검토 요청 |
| 예상 지출 | 통화별 알려진 가격 범위와 미확인 항목 수. 보증금·포함 항목 중복 방지 |

자동 예약·결제·메시지 발송은 하지 않습니다. 읽기 전용 오프라인 기능은 구현되어 있지만
현재 운영 활성화와 기기별 검증은 별도입니다.

## 두 추천 방식의 차이

| | 현지어 리뷰로 찾기 | 유명한 곳 | 주변 장소 참고하기 |
| :--- | :--- | :--- | :--- |
| 선택 기준 | 검증한 관측 리뷰의 원문 언어 | 공식 대표성·동일 플랫폼 인기·편집 선정 | 공개 지점과 위치 |
| 첫 사용 | 언어 근거가 통과한 후보만 정렬 | 취향 없이도 근거 유형별로 정렬 | 사용자가 별도로 열어 확인 |
| 자료 부족 | 이유를 보여주고 부족한 수 유지 | 근거가 있는 소그룹만 제공 | 영업·예약 등은 미확인 유지 |
| 실제 준비 상태 | **엄격 추천 OFF** | 코드·검수용 실제 후보 팩 준비 | 기존 공개지도 연동 재사용 |

리뷰는 최신순·최근 180일·최대 200개 고유 기록을 기본 관측 범위로 합니다. 별점만 있는 기록도
수집 상한에 포함하지만, 원문 언어 비율의 분모에는 넣지 않습니다. 원문 미확보와 실제 별점 전용
리뷰를 구분하고, 공급자 결과가 끝났다고 전체 Google 리뷰를 확보했다고 표현하지 않습니다.

**계산 예시 — 실제 식당의 수치가 아닙니다.** 본문 확인 200건(T), 언어 판별 190건(C),
미판별 10건(U), 현지어 150건(L), 한국어 4건(K)이라면:

| 지표 | 계산 | 의미 |
| :--- | :--- | :--- |
| 표시용 현지어·한국어 비율 | 150/190 = 78.9% · 4/190 = 2.1% | 판별한 리뷰 안의 구성 |
| 현지어 보수적 하한 | 150/200 = 75% | 미판별을 현지어로 인정하지 않음 |
| 한국어 보수적 상한 | (4+10)/200 = 7% | 미판별을 한국어일 수 있다고 봄 |

초기 엄격 조건은 C≥100, U/T≤10%, L/T≥60%, (K+U)/T≤10%와 동일 플랫폼 평점 4.2/5·
전체 평가 200개 이상입니다. 이 수치는 초기 가설이며 주민 비율이나 통계적 신뢰구간이 아닙니다.
사용자가 언어 비율·평점·평가 수를 완화하면 **직접 설정** 결과로 표시합니다. 지점·이용 권한·
원문·언어 품질 조건까지 완화하지는 않습니다.

## 현재 지원 범위

| 구분 | 확인한 범위 | 남아 있는 제한 |
| :--- | :--- | :--- |
| 도시 입력 | 100개 도시·국가·시간대 레지스트리 | 100개 도시의 언어 품질 통과를 뜻하지 않음 |
| 새 실제 후보 팩 | 도쿄·바르셀로나 각 6곳, 음식점 3 + 볼거리 3 | 공식·편집 출처 재확인, 운영 DB 자동 반영 안 함 |
| 기존 검수 장소 | 도쿄·바르셀로나·마드리드 기존 팩 보존 | 동일 이름을 보고 새 지점 ID와 자동 합치지 않음 |
| 새 도시 탐색 | Photon/OSM 공개 지점 조회와 유효 캐시 | 도심 기준 수집. 숙소 주변 전수 검색 아님 |
| 사진 | 지점·이용 권한을 확인한 자료가 있는 장소, 최대 3장 | 모든 장소의 사진 수와 종류를 보장하지 않음 |
| 리뷰 언어 | 지점 연결·공급자 계약·집계·도시별 평가 도구 | 실제 수집·이용·언어 품질 검증 미실행, strict OFF |
| 실제 경로 | 공급자 연동과 일정 검증 로직 | 운영 활성화 제한. 미확인 시간을 0분으로 바꾸지 않음 |

관측한 원문 언어는 작성자의 거주지나 국적을 뜻하지 않습니다. 공개지도 자료만으로 영업·가격·
리뷰·특정 날짜의 잔여석을 알 수는 없습니다. 실제 후보와 출처는
[12곳 검수 팩](docs/service-v4/data/iconic-pilot-2026-10-07.json), 준비 상태는
[검증 보고서](docs/service-v4/reports/stage2-validation.md)에서 확인할 수 있습니다.

## 작동 원리

```text
브라우저 · PWA
  └─ FastAPI / 로그인·사용자·여행 범위 검사
       ├─ 메일 → 저장된 job → 추출 → 예약·이벤트 → 새 검색 세대 활성화
       ├─ 날짜 질문 → SQL 현재 예약 조회 → 출처
       ├─ 본문 질문 → 여행별 검색 → 근거 기반 답변
       ├─ 공급자 지점 → 관리자 연결·계약 검증 → 도시별 리뷰 근거
       ├─ 여행 조건 → 자격 검사 → 구획별 일반 모델 → 추천·비교
       └─ 예약·장소 → 일정 검증 → preview → apply → 새 revision

운영  Render Free: 단일 worker·dispatcher
      Supabase: PostgreSQL / 비공개 Storage / pgvector
로컬  SQLite / 비공개 원문 디렉터리 / Chroma
```

메일의 구조화 추출은 `gpt-5-mini`, 검색 임베딩은 `text-embedding-3-small`을 사용합니다.
`MAIL_ANALYSIS_MODE`와 외부 호출·예산 설정에 따라 AI 또는 기본 로컬 분석을 사용합니다.
기본 분석은 지원하는 형식이 제한되어 있어 원문 확인·교정·수동 입력이 필요합니다.

개인 예약 문서와 공용 장소·리뷰 자료는 검색 범위를 분리합니다. 추천 후보·자격·점수와 일정
제약은 서버에서 결정하고, AI는 구조화 추출과 근거 설명을 맡습니다.

| 영역 | 주요 코드 |
| :--- | :--- |
| 인증·여행·예약·원문 | [`src/foundation/`](src/foundation/) |
| 작업 복구·검색 세대·예산 | [`src/reliability/`](src/reliability/) |
| 리뷰 관측·언어 품질 | [`src/research/`](src/research/) |
| 장소·지점·사진 | [`src/discovery/`](src/discovery/) |
| 추천 모델·버전·설명 | [`src/recommendations/`](src/recommendations/) |
| 일정 생성·검증·편집 | [`src/itineraries/`](src/itineraries/) |
| 웹 화면 | [`web/`](web/) |

## 해결 내용

### 질문의 종류에 맞게 조회 방법을 나눴습니다

초기에는 예약이 없는 렌터카 질문에 투어 집합 시각을 답한 사례가 있었습니다. 유사도 임계값만
높이면 정상 질문도 탈락해, 예약 종류와 날짜로 먼저 범위를 제한하도록 했습니다.
하루 전체 질문에는 날짜 범위 조회를 사용합니다. 하루에 예약이 8개라면 상위 몇 건만 찾는
검색으로는 충분하지 않기 때문입니다. 답변의 시각은 사용자의 현재 교정값을 사용합니다.

### 재분석·재시작·동시 작업에서 데이터를 지켰습니다

추출이나 임베딩에 실패했다고 기존 예약을 지우지 않습니다. 새 검색 세대를 준비·검증한 뒤
SQL의 활성 포인터를 전환합니다. 작업 상태, 실행 소유권, 재개 지점을 DB에 저장하며 같은 입력의
중복 요청은 서버에서 제어합니다. 삭제한 여행을 이전 작업의 늦은 완료가 되살리지 못하도록 검사합니다.

### 지도 검색의 실제 실패 원인을 확인했습니다

운영 Render에서 기존 Overpass 연결이 `ECONNREFUSED`로 거절되는 것을 확인했습니다.
이를 한도 소진이라고 안내하는 대신 오류를 구분하고 Photon 경로를 적용했습니다. 런던에서
음식점 후보 12곳을 표시했고, 같은 조건의 재조회에서 추가 외부 지도 호출이 없음을 확인했습니다.

다만 캐시가 전체 지연을 없애지는 않았습니다. 첫 요청 52.89초, 캐시 재조회 44.45초가 걸렸습니다.
각각 한 번의 이전 운영 관측이며 평균·보장값이 아닙니다.

v4에서는 후보별 사실 조회를 일괄 조회로 바꿨습니다. 같은 합성 후보 100개를 읽는 GET의 SQL 호출은
**1,639→32회**, DB checkout은 **309→4회**, 쓰기는 **0회**였습니다. 숙소 근처의 13번째 후보와
식당 100개 뒤 카페가 표시 제한 전에 탈락하던 문제도 수정했습니다. 이는 SQLite·임시 PostgreSQL의
고정 시험 결과이며, 운영 지연이 같은 비율로 줄었다는 주장은 아닙니다.
[시험 경계와 재현 방법](docs/service-v4/reports/stage1-discovery-validation.md)

### 근거가 없는 정보를 확정하지 않습니다

총좌석 수와 특정 날짜의 예약 가능 인원은 다릅니다. 운영시간·예약 방법·최대 인원·가격·실제 슬롯을
구분하고 각각 출처·확인일·유효기간을 붙였습니다. 일정도 모르는 이동시간을 0분으로 계산하거나,
높은 평점으로 확인된 휴무·인원 위반을 상쇄하지 않습니다.

초기 검색·생성 실험은 [이전 README](docs/history/README-before-going.md)에 보존했습니다.
해당 문서의 배포·모델 활성화·평가 수치는 당시 기록입니다.

## 평가 결과

v4 코드의 전체 회귀 시험과 단계별 PostgreSQL 시험을 분리해 기록합니다.
최종 명령·결과·제한은 [2단계 검증 보고서](docs/service-v4/reports/stage2-validation.md)에 있습니다.

| 항목 | 이번 검증 | 범위 |
| :--- | :--- | :--- |
| Python 전체 | 1,134 passed · 17 skipped · 0 failed | 외부 환경 의존 skip을 통과에 포함하지 않음 |
| JavaScript | 175 passed · 0 failed | 새 탭·근거·관리자·동의·맥락 복원 계약 |
| SQL/권한/복구 | SQLite 및 폐기 가능한 PostgreSQL | 운영 DB·사용자 자료와 분리 |
| 고정 입력 모델 비교 | 외부 호출 0회 · JSON/Markdown | 순위·결측·다양성·조건 위반. 실제 만족도 아님 |
| 새 화면 실제 브라우저 | 저장된 로컬 로그인 접근 차단으로 미검증 | 기존 화면 촬영 기록과 분리 |
| 실제 언어 정확도·NDCG·만족도 | 미측정 | 합성 통과를 실사용 성과로 바꾸지 않음 |

```bash
PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m pytest tests -q
node --test tests/*.cjs
PYTHON_DOTENV_DISABLED=1 .venv/bin/python scripts/version_web_assets.py --check

# 조건·후보·사실·정책·시각을 고정한 모델 비교: 유료 호출 없음
PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m src.product.evaluation \
  --fixture docs/service-v4/fixtures/general-v3-frozen-synthetic.json \
  --candidate-version general-v3 \
  --output /tmp/going-model-evaluation.json
```

이전 v1/v2 모델의 snapshot replay를 유지합니다. 신규 기본 모델의 계산 근거는
[모델 registry](docs/service-v4/fixtures/model-registry-v3.json), 비교 결과는
[평가 보고서](docs/service-v4/reports/stage2-model-evaluation.md)에 있습니다.
개인 선호 반영과 분석 참여를 분리하고, 학습 기반 개인화는 자료 부족으로 사용하지 않습니다.

## 시작하기

### 화면을 먼저 확인하려면

초대된 계정은 [배포 서비스](https://travel-inbox-rag.onrender.com)를 이용합니다.
개발 환경에서는 외부 인증·유료 호출 없이 예시 화면을 볼 수 있습니다.

```bash
git clone https://github.com/Jin-s-work/Travel-Agent.git
cd Travel-Agent
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
PYTHON_DOTENV_DISABLED=1 .venv/bin/python scripts/presentation_browser_fixture.py
```

`http://127.0.0.1:8766`에서 합성 사용자 A로 로그인합니다. 임시 DB에 가상 여행·메일·예약을
준비하고 기본 분석 후 예시 일정을 생성합니다. 로컬 시험용 인증이며 운영에 배포하지 않습니다.
이 스크립트는 기존 흐름의 예시입니다. v4 연결 흐름의 fixture는
[`scripts/stage1_browser_fixture.py`](scripts/stage1_browser_fixture.py)입니다.
자세한 발표 순서는 [새 발표 대본](docs/presentation/v4/SCRIPT.md)에 있습니다.

### 직접 로그인과 AI를 설정하려면

```bash
# 기존 설정 파일을 덮어쓰지 않습니다.
test -e .env || cp .env.example .env
```

`.env`의 `PUBLIC_BASE_URL`, `SESSION_SECRET`, OIDC 설정을 채우고 초대 계정을 등록합니다.
인증과 서비스 가입 허용 여부를 별도로 검사하며, 인증 미설정 상태에서는 개인 API가 닫힙니다.
[인증·초대 안내](docs/service-v2/FOUNDATION_RUNBOOK.md)를 따릅니다.

```bash
.venv/bin/python -m uvicorn api:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

브라우저 주소는 `http://localhost:8000`, OIDC callback은
`http://localhost:8000/api/v2/auth/callback`입니다. 단일 worker·dispatcher 구조를 유지합니다.
메일 연습 자료는 [`examples/mail-test-pack/`](examples/mail-test-pack/)에 있으며 왕복 항공, 호텔,
복수 예약, 하루 8건, 날짜만 있는 예약, HTML, 같은 파일명, 변경 메일, DST 사례와 기대 결과를 포함합니다.

## API 키 관리

비밀값은 Git에서 제외한 `.env` 또는 호스팅 secret 설정에 둡니다.
설정 이름은 [`.env.example`](.env.example), 배포는 [Render + Supabase 안내](docs/operations/RENDER_SUPABASE.md)를 기준으로 합니다.

| 설정 | 용도 |
| :--- | :--- |
| `OPENAI_API_KEY`, `MAIL_ANALYSIS_MODE` | AI 호출과 auto / local / ai 분석 모드 |
| `PRICING_CONFIG` | 공급자 단가·사용자·전역 예산 정책 |
| `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `SESSION_SECRET` | 로그인·세션 |
| `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_SECRET_KEY` | 운영 DB·비공개 파일·검색 |
| `PUBLIC_DISCOVERY_ENABLED` | 무료 공개지도 신규 조회 허용 |
| `GOOGLE_MAPS_API_KEY`, `LOCATION_PROVIDER_CONFIG` | 선택 경로 공급자 설정 |

운영 OpenAI 정책은 전체 **일 $0.50 / 월 $3**, 사용자 **일 $0.25 / 월 $1** 상한입니다.
이는 설정된 AI 호출 한도이며 모든 서비스의 총 청구액을 뜻하지 않습니다. 실제 정책은
[예산 파일](deploy/render-supabase/pricing-openai.json)에서 관리합니다. 호출 전에 비용을 예약하고,
응답 유실로 과금 여부가 불명확하면 임의로 환불하지 않습니다. 예산이 소진되어도 저장된 예약 조회는 유지합니다.

## 향후 계획

1. **실제 리뷰 검증** — 도쿄·바르셀로나부터 이용 범위·원문·정렬·언어 품질을 확인합니다. Apify의 dataset 페이지를 원천 리뷰의 연속성으로 오해하지 않습니다.
2. **브라우저와 운영 성능 확인** — 저장된 권한 차단 해제 후 새 UI의 전체 흐름과 운영 지연을 다시 측정합니다.
3. **자료가 부족한 이유 줄이기** — 지점과 공식 출처를 대조하고 영업·가격·예약 정보를 필드별로 보강합니다.
4. **소수 사용자의 실제 선택 관찰** — 저장·일정 채택과 방문 경험을 나눠 보고 입력 부담을 줄입니다. 아직 성과 수치는 없습니다.

새 구조와 완료 범위는 [service-v4 진행 기록](docs/service-v4/IMPLEMENTATION_STATUS.md),
기존 기반은 [service-v2](docs/service-v2/IMPLEMENTATION_STATUS.md)와
[service-v3](docs/service-v3/IMPLEMENTATION_STATUS.md), 백업·복구는
[운영 절차](docs/operations/RUNBOOK.md)에 기록했습니다.

## 발표 자료

**최신 v4 · 10장 · 8분 30초 목표.** 두 추천 방식과 리뷰 계산, 예약·일정 연결, 구현과 실제 검증의
차이를 자연스럽게 설명합니다. 각 슬라이드에 발표자 메모를 넣었고 5분 축약·10분 시연안도 제공합니다.

- [Keynote 다운로드](docs/presentation/v4/going-v4-class-presentation.key?raw=1) · [PowerPoint 다운로드](docs/presentation/v4/going-v4-class-presentation.pptx?raw=1)
- [발표 대본·예상 질문](docs/presentation/v4/SCRIPT.md) · [새 발표 자료 안내](docs/presentation/v4/README.md)
- [근거·렌더링 검증](docs/presentation/v4/REVIEW.md) · [기존 화면·사진 출처](docs/presentation/CREDITS.md)

![고잉 v4 발표 10장 미리보기](docs/presentation/v4/preview.webp)
