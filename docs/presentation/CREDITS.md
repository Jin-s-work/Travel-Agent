# 발표·README 자료 출처

기준일: 2026-10-07. 화면은 실제 앱 코드를 로컬에서 실행하고 합성 계정·가상 예약·가상 메일로 촬영했다. 운영 사용자 정보·비밀키는 포함하지 않는다. 화면 크롭과 축소만 했으며 없는 상태를 합성하지 않았다.

## 화면과 데이터

- 현재 UI: `web/`, 여행가방 로고: `web/icon.svg`. 앱 주색 `#25485A`, 발표 배경 `#FAF9F6`.
- 촬영 fixture: [`scripts/presentation_browser_fixture.py`](../../scripts/presentation_browser_fixture.py).
- 메일: [`examples/mail-test-pack`](../../examples/mail-test-pack/). `TEST-HOTEL-002` 등은 가상 예약번호다.
- 메일 촬영: 기본 로컬 분석, 외부 AI 호출 0회. 3개 메일에서 예약 3개와 직접 입력 3개, 2일차 조회 5개를 확인했다. 한 항공 예약 안의 구간과 예약 개수는 구분한다.
- 일정 촬영: 고정 예약 사이의 이동·준비 여유 부족과 미확인을 표시한 실제 결과다. 검증 완료 일정으로 표현하지 않는다.
- 마드리드 촬영: 현지 탐색에서 Casa Lucio·Casa Ciriaco 2곳의 방문 전 확인 후보. 저장소의 도시별 실제 팩 3곳과 화면의 필터별 후보 수는 다르다.
- 장소 사실: [`official-restaurants-2026-10-06.json`](../service-v3/data/official-restaurants-2026-10-06.json). 확인일·유효기간에 따라 재검수가 필요하다.

## 화면 속 사진

아래 사진은 `going-discovery.png`, 발표 6번 슬라이드와 그 미리보기에 포함된다. 앱 화면에 맞게 축소·크롭한 상태다. 사진의 원 라이선스를 유지하며, 이미지가 현재 실내·메뉴·예약 가능성을 보증하지 않는다. CC BY-SA 사진을 재사용·수정할 때에는 해당 원 라이선스의 출처·동일조건을 함께 따른다. 발표 파일의 다른 요소에 사진 라이선스가 일괄 적용된다고 주장하지 않는다.

| 사진 | 저작자 | 촬영일 | 출처·라이선스 |
| --- | --- | --- | --- |
| Barra de Casa Lucio | Javier Lastras from España/Spain | 2010-01-04 | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Barra_de_Casa_Lucio_(4245090907).jpg) · [CC BY 2.0](https://creativecommons.org/licenses/by/2.0/) |
| Gallina en Pepitoria (Ciriaco) | Tamorlan | 2012-01-29 | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Gallina_en_Pepitoria_(Ciriaco).JPG) · [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/) |

지점 대조·개별 사진 메타데이터: [`restaurant_photos.json`](../../src/discovery/restaurant_photos.json).

## 구현·수치 근거

- [현재 서비스 진행 기록](../service-v3/IMPLEMENTATION_STATUS.md)
- [지도 운영 검증·전체 회귀 시험·로고 배포](../service-v3/reports/going-design-search-validation.md)
- [OpenAI 복구와 UI 검증](../service-v3/reports/journey-layout-openai-validation.md)
- [인증·예약 계약](../service-v2/FOUNDATION_RUNBOOK.md)
- [리뷰 데이터 계약](../service-v2/REVIEW_DATA_SPEC.md)
- [일정 생성·편집 계약](../service-v2/ITINERARY_RUNBOOK.md)
- [Render + Supabase 저장 구조](../operations/RENDER_SUPABASE.md)

Python 1,039 / JavaScript 124는 2026-10-07 이전 구현 검증에서 기록한 회귀 시험 통과 수다. 이번 문서 작업에서 전체 시험을 재실행했다는 뜻이 아니다. 추천 만족도·여행자 일반 선호·전 세계 도시 품질은 미측정이다.

## 글꼴

발표는 Pretendard를 사용한다. 앱의 [`web/fonts/`](../../web/fonts/)에 라이선스가 포함되어 있다. 다른 PC에서는 글꼴 설치 여부를 확인하고 발표 전 레이아웃을 점검한다. 폰트를 파일에 내장했다고 주장하지 않는다.

## 재검토에서 추가한 설명

8번 슬라이드의 120분 창과 150분 필요 시간은 일정 제약을 설명하기 위한 합성 계산 예시다. 오른쪽 앱 화면은 기존 합성 예약의 고정 예약·충돌 안내를 확대 크롭했다. 화면에 새로운 상태나 값을 합성하지 않았다.
