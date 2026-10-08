# 자료와 화면 출처

2026-10-08 개정. [v6 발표](../v6/README.md)의 남색 표지, 밝은 본문, Pretendard 글꼴과 기본 배치를 유지했습니다. 발표 문안과 메모는 최신 기능에 맞춰 고쳤고 이전 사용자 Keynote와 PDF는 보존했습니다.

## 앱 화면

| 사용 위치 | 촬영 범위 |
|---|---|
| 슬라이드 3, 메인 이미지 | 2026-10-08 운영 서비스의 가상 도쿄 여행. 최근 장소·AI 입력·저장 상태 |
| 슬라이드 5, 답변과 출처 | 2026-10-08 운영 서비스. 합성 수동 예약 1건의 날짜 조회와 직접 입력 출처 |
| 슬라이드 4, 메일 예약 상세 | 2026-10-07 로컬 합성 메일의 기본 분석 결과 |
| 슬라이드 6, 사진 예시 | 2026-10-07 이전 마드리드 화면. 실제 지점, 방문 조건 미확인 |
| 슬라이드 8, 일정 | 2026-10-07 고정 예약과 이동 미확인·충돌을 보여주는 합성 예시 |

최신 원본은 [`docs/screenshots/*-v7.jpg`](../../screenshots/), 이전 화면과 사진은 [기존 출처](../CREDITS.md)에 연결했습니다. 화면은 개인정보가 없는 가상 여행과 공개 지점 자료입니다. 운영 화면의 로그인 계정 정보나 비밀값은 포함하지 않았습니다.

공개 장소 자료는 [OpenStreetMap 기여자, ODbL](https://www.openstreetmap.org/copyright), 검색은 [Photon](https://photon.komoot.io/), 도시 기준점은 [GeoNames, CC BY 4.0](https://www.geonames.org/)를 사용합니다. 자료 범위와 지점·시점은 앱의 출처 표시를 따릅니다.

## 사진

슬라이드 6에 아래 사진이 포함됩니다. 기존 앱 화면의 축소·크롭 상태를 유지했습니다. 동일조건 라이선스는 해당 사진과 그 수정물에 유지되며 발표 전체에 적용된다는 뜻은 아닙니다.

| 사진 | 저작자 | 원본과 라이선스 |
|---|---|---|
| Barra de Casa Lucio | Javier Lastras | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Barra_de_Casa_Lucio_(4245090907).jpg), [CC BY 2.0](https://creativecommons.org/licenses/by/2.0/) |
| Gallina en Pepitoria (Ciriaco) | Tamorlan | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Gallina_en_Pepitoria_(Ciriaco).JPG), [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/) |

사진은 촬영 당시 모습이며 현재 메뉴·실내·예약 가능 여부를 보증하지 않습니다. 최신 무료 후보에 사진을 모두 확보했다는 설명으로 사용하지 않습니다.

## 영상

70초 H.264 MP4, 음성과 음악 없음. 정지 화면에 자막과 전환을 더한 편집 영상입니다. 처리 시간·클릭·진행률을 만들어 넣지 않았습니다.

| 구간 | 화면 |
|---|---|
| 0~10초 | 최신 홈의 장소와 저장 |
| 10~20초 | 최신 홈의 AI 질문 입력 |
| 20~30초 | 가상 예약 1건의 날짜별 답변 |
| 30~38초 | 직접 입력한 예약의 출처 |
| 38~54초 | 최신 도쿄 공개 장소 결과 |
| 54~59초 | 이전 로컬 메일 분석 예시 |
| 59~64초 | 이전 로컬 일정 예시 |
| 64~70초 | 최신 메인과 마무리 |

각 장면에 촬영 날짜와 범위를 표시했습니다. 화면 가독성을 위해 일부를 크롭·확대했으며 내용은 합성하지 않았습니다. 가상 예약 파일은 [메일 테스트 팩](../../../examples/mail-test-pack/)을 참고합니다.

## 구현 근거

- [v5 변경과 운영 검증](../../service-v3/MAIN_RECOMMENDATION_UX.md)
- [현재 추천 코드](../../../src/recommendations/hybrid_v5.py)
- [v4 합성 개발 평가](../../service-v4/reports/hybrid-v4-benchmark.md), 현재 v5의 성능 결과가 아님
- [예약·교정](../../service-v2/FOUNDATION_RUNBOOK.md)
- [일정 검증·편집](../../service-v2/ITINERARY_RUNBOOK.md)
- [Render·Supabase 구성](../../operations/RENDER_SUPABASE.md)

엄격 현지어 추천은 실제 자료 이용 범위·언어 품질 검증 전 OFF입니다. 독립 추천 평가와 실제 사용자 만족도는 미측정입니다. 사진 공백과 모바일 실기기 미검증, 간헐적 서버 오류의 추가 진단도 남아 있습니다.
