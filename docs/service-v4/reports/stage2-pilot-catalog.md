# 2단계 실제 파일럿 장소 카탈로그 검증

2026-10-07 · 검증 대상: [`iconic-pilot-2026-10-07.json`](../data/iconic-pilot-2026-10-07.json). 이 보고서는 해당 파일에 기록된 지점·출처와 격리 API 검증 결과를 정리한다. 보고서 작성 중 출처를 새로 조회하거나 리뷰를 수집하지 않았다.

## 현재 준비 상태

| 범위 | 도쿄 | 바르셀로나 | 합계 |
|---|---:|---:|---:|
| 파일에 준비한 실제 지점 | 6 | 6 | 12 |
| 식당 | 3 | 3 | 6 |
| 볼거리 | 3 | 3 | 6 |
| 공식 대표 장소 근거 | 3 | 3 | 6 |
| 편집·관광 자료의 식당 소개 근거 | 3 | 3 | 6 |
| 격리 API의 유명한 곳: 방문 조건 확인 필요 | 6 | 6 | 12 |
| 격리 API의 방문 조건까지 확인된 결과 | 0 | 0 | 0 |
| 격리 API의 엄격 현지어 추천 결과 | 0 | 0 | 0 |

파일럿 식당 3곳·볼거리 3곳이라는 **출처 카탈로그 준비 목표**는 두 도시 모두 충족했다. 현재 영업, 방문 인원 수용, 예약 가능 여부까지 검증된 목표를 충족했다는 뜻은 아니다. 현지어 리뷰를 수집·평가한 결과가 없으므로 현지어 추천 완료로 표시하지 않는다.

## 지점과 출처

아래 ID는 파일의 안정적인 `external_id`이며, 서버가 생성하는 canonical `place_id`와 다르다. 기존 `manual_official` 지점의 같은 외부 ID를 재사용하는 추가 pack이며, 이름만 비슷한 별도 ID를 자동 병합하지 않는다. 격리 테스트의 내부 ID는 임시 DB에서 생성되므로 운영 ID라고 공개하지 않는다.

### 도쿄

Pack: `iconic-pilot-tokyo-2026-10-07-v1`

| 지점 · 원어명 | 분류 | external_id | 확인한 지점 주소 | 근거 |
|---|---|---|---|---|
| 간다 마쓰야 · 神田まつや | 식당 | `kanda-matsuya-sudacho` | 東京都千代田区神田須田町1-13 | 편집·관광 자료 소개 · [관광 자료](https://visit-chiyoda.tokyo/app/spot/detail/359) |
| 니혼바시 타마이 본점 · 日本橋 玉ゐ 本店 | 식당 | `nihonbashi-tamai-honten` | 東京都中央区日本橋2-9-9 | 편집·관광 자료 소개 · [편집 자료](https://www.nihonbashi-tokyo.jp/restaurants/gourmet-201308/) · [공식](https://anago-tamai.com/nihonbashi/) |
| 다이코쿠야 본점 · 大黒家 | 식당 | `daikokuya-asakusa13810` | 東京都台東区浅草1-38-10 | 편집·관광 자료 소개 · [편집 자료](https://asakusa-shoren.jp/stores/819/) |
| 센소지 · 浅草寺 | 볼거리 | `sensoji-asakusa-main-hall` | 東京都台東区浅草2-3-1 | 공식 대표 장소 · [공식](https://www.senso-ji.jp/guide/) |
| 도쿄국립박물관 · 東京国立博物館 | 볼거리 | `tokyo-national-museum-ueno` | 13-9 Ueno Park, Taito-ku, Tokyo 110-8712, Japan | 공식 대표 장소 · [공식](https://www.tnm.jp/modules/r_free_page/index.php?id=113&lang=en) |
| 하마리큐 온시 정원 · 浜離宮恩賜庭園 | 볼거리 | `hamarikyu-gardens-chuo` | 東京都中央区浜離宮庭園1-1 | 공식 대표 장소 · [공식](https://www.tokyo-park.or.jp/park/hama-rikyu/index.html) |

### 바르셀로나

Pack: `iconic-pilot-barcelona-2026-10-07-v1`

| 지점 · 원어명 | 분류 | external_id | 확인한 지점 주소 | 근거 |
|---|---|---|---|---|
| 칸 쿨레레테스 · Can Culleretes | 식당 | `can-culleretes-quintana5` | Carrer d’en Quintana, 5, 08002 Barcelona | 편집·관광 자료 소개 · [관광 자료](https://thisisbarcelona.com/gastronomy/restaurants/can-culleretes) · [공식](https://culleretes.com/en/contact/) |
| 세븐 포르테스 · 7 Portes | 식당 | `7-portes-isabel14` | Passeig Isabel II, 14, 08003 Barcelona | 편집·관광 자료 소개 · [관광 자료](https://thisisbarcelona.com/fr/gastronomie/restaurants/7-portes) · [공식](https://7portes.com/en/) |
| 바 라 플라타 · Bar La Plata | 식당 | `bar-la-plata-merce28` | Carrer de la Mercè, 28, 08002 Barcelona | 편집·관광 자료 소개 · [편집 자료](https://cadenaser.com/cataluna/2026/09/28/80-anys-del-bar-la-plata-un-reflex-de-la-historia-de-barcelona-des-de-la-postguerra-fins-avui-sercat/) · [공식](https://barlaplata.com/ubicacion-y-contacto/) |
| 사그라다 파밀리아 · Basílica de la Sagrada Família | 볼거리 | `sagrada-familia-basilica` | Carrer de Mallorca, 401, 08013 Barcelona; visitor entrance: Carrer de la Marina | 공식 대표 장소 · [공식](https://sagradafamilia.org/en/) · [공식](https://sagradafamilia.org/en/sagrada-familia-ticket?inheritRedirect=true) |
| 구엘 공원 · Park Güell | 볼거리 | `park-guell-monumental` | Park Güell, Barcelona; entrances at Carretera del Carmel and Avinguda del Santuari de Sant Josep de la Muntanya | 공식 대표 장소 · [공식](https://parkguell.barcelona/en/planning-your-visit/prices-and-times) |
| 카탈루냐 음악당 · Palau de la Música Catalana | 볼거리 | `palau-musica-catalana-main` | Carrer del Palau de la Música, 4-6, 08003 Barcelona | 공식 대표 장소 · [공식](https://www.palaumusica.cat/en/practical-information_1642451) |

## 보존한 미확인 범위

- 12개 지점 모두 파일의 `latitude`·`longitude`는 null이다. 주소가 있다고 좌표를 추정하지 않았으며, API 카드의 직선거리와 이동시간도 null이다.
- 파일은 `category`와 `iconic_evidence`만 사실로 제공한다. `opening_hours`, `rating`, `live_availability`, `min_party`, `max_party`, `reservation_methods`, `price`를 추가하지 않았다. 해당 값은 미확인이며, 없음이나 무료라는 뜻이 아니다.
- 공식 안내·티켓 페이지 링크가 있어도 특정 날짜의 운영, 잔여석, 4인 예약, 가격을 확정하지 않는다. 카드에는 `HOURS_UNKNOWN`과 `LIVE_AVAILABILITY_NOT_CONFIRMED`를 유지한다.
- 관광 자료의 식당 소개는 `editorial_recognition`이다. 그 출처가 관광청이라는 이유로 식당을 공식 대표 명소나 인기 순위 1위로 바꾸지 않는다.
- 같은 파일에 평점·전체 평가 수·관측 리뷰가 없다. 유명한 곳 결과에 가상의 평점이나 언어 비율을 붙이지 않는다. 리뷰 상세의 수치도 미제공 상태다.
- 실제 출처의 확인·만료 시각은 원본 JSON에 저장되어 있다. 최초 검증 당시의 근거이며, 만료 이후에는 운영 재검수가 필요하다.

## 실행한 검증과 하지 않은 작업

`tests/test_stage2_pilot_catalog.py`는 합성 사용자와 임시 DB에서 다음 정상 API 순서를 실행했다. 자료 내용은 위 실제 카탈로그를 사용했지만 운영 자료를 쓰거나 운영 DB에 접속하지 않았다.

1. 관리자 pack import → 출처 상태 승인 → pack 승인.
2. 도시별 여행 생성 → 선호·숙소·예산 미입력 방문 조건 저장 → 추천 job 실행.
3. 같은 canonical 지점의 추천 카드와 상세 조회.
4. 식당 3·볼거리 3의 근거 유지, 현지어 결과 0건, 미확인값 보존 검사.
5. 다른 사용자의 추천 조회가 404인지 확인.

**실제 결과: 2 passed, 2 warnings, 1.75s.** 이후 관련 모델·경로·탐색 회귀와 함께 실행한 집합은 **77 passed, 2 warnings, 14.63s**이며, 두 집합은 중복된다. 로그: [`stage2-ranking-audit-tests.log`](stage2-ranking-audit-tests.log). 경고는 기존 Starlette/httpx와 Authlib 호환성 경고다.

외부 공급자 호출과 유료 호출은 0회였다. 실제 리뷰 수집, 원문 언어 품질 평가, 운영 데이터베이스 import, Render 배포, 실제 사용자 세션 검증은 이 검증 범위에서 실행하지 않았다. 따라서 이 파일이 GitHub에 올라가더라도 운영 DB의 추천 자료가 자동으로 갱신되었다고 판단하면 안 된다.

재실행:

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHON_DOTENV_DISABLED=1 \
HTTP_PROXY=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9 ALL_PROXY=http://127.0.0.1:9 \
.venv/bin/python -m pytest tests/test_stage2_pilot_catalog.py -q -p no:cacheprovider --tb=short
```

이번 실행은 지원 Python 3.13 임시 환경 `/private/tmp/going-stage1-native313/bin/python`을 사용했다. 카탈로그의 날짜를 임의 연장하지 않는 시험이므로 원본 근거가 만료된 이후 실패하면 실제 출처를 다시 확인하고 새 버전의 pack을 만드는 절차가 필요하다.
