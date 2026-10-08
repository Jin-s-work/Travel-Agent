# Google 지도 리뷰 연결

리뷰 출처는 Google 지도이며 수집 도구는 Apify의 `compass/google-maps-reviews-scraper`다. Places API의 최대 5개는 웹 수집의 기술적 상한이 아니다. 추천 화면의 언어 비율은 관측한 리뷰에만 적용한다.

1. Apify의 Settings → Integrations에서 API 토큰을 준비한다.
2. Git에서 제외한 `deploy/render-supabase/.env`의 `APIFY_TOKEN`에 저장한다. Render에는 같은 이름의 secret으로 전달한다.
3. `python scripts/check_apify_setup.py --env deploy/render-supabase/.env`로 연결·계정 한도·현재 Actor 단가를 확인한다. 이 명령은 Actor를 실행하지 않는다.
4. 전체 env 가져오기가 필요하면 `python scripts/prepare_render_env.py --profile openai --with-apify --check` 후 같은 명령에서 `--check`를 빼서 생성한다. 기존 OpenAI 분석 설정은 유지한다. 토큰 입력만으로 수집이나 운영 추천이 켜지지 않는다.

2026-10-08 계정 확인: Free, 월 크레딧 $5, 월 사용 상한 $5. 당시 Free 단가는 리뷰 1건 $0.0006, 시작 이벤트 $0.00005/GB(최소 1GB). 3곳×200건의 리뷰 이벤트는 최대 $0.36이며 조회·저장·시작 비용을 포함한 총액이 아니다. 실제 실행 전 재조회하며 무료 잔액 내에서만 시작한다. 자동 충전·유료 플랜은 변경하지 않는다.

첫 검증안: 도쿄 3곳, 지점 이름·주소·Google Place ID 대조 후 최신순·최근 180일·장소당 최대 200개. `reviewsOrigin=google`, `personalData=false`, 언어/키워드/평점 필터 없음. `language`는 화면/번역 언어이며 원문 언어로 복사하지 않는다. 최신 빌드는 0.0.528로 확인했지만 기존 어댑터에 자동 적용하지 않는다.

추천 기본 조건은 판별 100건 이상, 미판별 10% 이하, 현지어 하한 60%, 한국어 상한 10%, 동일 플랫폼 평점 4.2/5 이상·전체 평가 200개 이상이다. ‘관측 리뷰 모두 현지어’는 현지어 하한 100%·한국어 상한 0%로 더 엄격하게 검사한다. 빈 결과를 일반 지도 장소로 채우지 않는다.

현재 한계: 계정 연결 확인과 실제 수집·제품 활성화는 별도다. 현재 Apify 어댑터는 dataset 페이지와 Google 원천 페이지의 연속성을 동일시하지 않는다. 원문 분리·정렬·수집 구간·언어 평가·이용 범위를 확인하기 전에는 엄격 추천을 켜지 않는다. 관리자에서 실제 지점을 연결하고 수집 미리보기·예산을 검토하는 기존 흐름을 사용한다.

공식 자료: [Actor](https://apify.com/compass/google-maps-reviews-scraper), [입력 계약](https://apify.com/compass/google-maps-reviews-scraper/input-schema), [API 토큰](https://docs.apify.com/integrations/api).
