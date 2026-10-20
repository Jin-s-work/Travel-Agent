# 발표와 데모 출처

2026-10-08 편집. 슬라이드의 남색 표지, 밝은 배경, Pretendard 글꼴과 화면 배치는 [기존 발표](../going-class-presentation.pptx)를 바탕으로 했습니다. 기존 사용자 Keynote와 PDF는 보존했습니다.

데모는 2026-10-07에 로컬 앱에서 합성 계정과 가상 여행으로 촬영한 **실제 화면 캡처의 편집본**입니다. 실시간 조작 영상, 새 모델의 실행 기록, 운영 로그인 검증 영상이 아닙니다. 자막과 화면 전환을 추가했고 클릭이나 분석 진행 상태를 만들어 넣지 않았습니다. 음악·음성은 없습니다.

- 화면 원본: [`docs/screenshots`](../../screenshots/). 답변 근거 패널은 기존 발표 5번 슬라이드의 원본 이미지를 사용했습니다.
- 가상 메일·예약: [`examples/mail-test-pack`](../../../examples/mail-test-pack/). `TEST-HOTEL-002`는 가상 예약번호입니다. 메일 분석 촬영은 기본 로컬 분석이며 유료 AI 호출은 없었습니다.
- 일정은 고정 예약과 이동시간 미확인·충돌을 보여주는 결과입니다. 검증 완료 일정으로 제시하지 않습니다.
- 장소 화면은 마드리드의 실제 식당 자료입니다. 미래 방문일의 영업·인원·잔여석은 확인 필요 상태입니다.
- 자세한 출처와 기존 촬영 범위: [기존 자료 출처](../CREDITS.md).

## 사진

슬라이드 6과 데모 42~54초에 아래 사진이 들어갑니다. 앱 화면의 축소·크롭 상태를 유지했습니다. 각 사진의 라이선스와 동일조건은 해당 사진 및 그 수정물에 유지되며, 다른 발표 요소 전체의 라이선스를 바꾼다는 뜻은 아닙니다.

| 사진 | 저작자 | 원본과 라이선스 |
|---|---|---|
| Barra de Casa Lucio, 2010-01-04 촬영 | Javier Lastras | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Barra_de_Casa_Lucio_(4245090907).jpg), [CC BY 2.0](https://creativecommons.org/licenses/by/2.0/) |
| Gallina en Pepitoria (Ciriaco), 2012-01-29 촬영 | Tamorlan | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Gallina_en_Pepitoria_(Ciriaco).JPG), [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/) |

사진은 촬영 당시의 모습이며 현재 메뉴·실내·예약 가능 여부를 보증하지 않습니다.

## 기술 설명

- [추천 모델과 초기 가중치](../../service-v4/RECOMMENDATION_MODEL.md)
- [합성 개발 평가의 조건과 한계](../../service-v4/reports/hybrid-v4-benchmark.md)
- [예약과 교정](../../service-v2/FOUNDATION_RUNBOOK.md)
- [일정 검증과 편집](../../service-v2/ITINERARY_RUNBOOK.md)
- [Render와 Supabase 구성](../../operations/RENDER_SUPABASE.md)

엄격 현지어 추천은 실제 리뷰 이용 범위와 언어 품질 검증 전 OFF입니다. 실제 사용자 정확도·방문 만족도는 미측정입니다. 100개 도시 입력 지원과 도시별 추천 자료 확보 수준은 구분합니다.
