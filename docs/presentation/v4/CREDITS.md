# 발표 자료 출처

2026-10-07. 앱 이미지와 수치는 검증 범위를 구분한다.

- 1장: 고잉 저장소의 `web/icon.svg`. `brand-icon.png`는 동일 원본의 PNG 변환이며 새 디자인이나 외부 로고가 아니다. SVG 가져오기에서 Keynote가 표시하지 못하는 문제 때문에 래스터 사본을 사용했다.
- 2·3·7장: 기존 `docs/screenshots/going-home.png`, `going-mail.png`, `going-itinerary.png`. 2026-10-07 합성 계정·가상 메일로 촬영한 이전 화면. 새 v4 브라우저 실행 증거가 아니다. 촬영과 기존 공개 사진의 상세 출처는 [이전 출처 기록](../CREDITS.md)에 있다. 새 발표에는 식당 사진을 추가하지 않았다.
- 4·6장: [v4 추천 모델 계약](../../service-v4/RECOMMENDATION_MODEL.md), [실제 구현](../../service-v4/reports/stage2-ranking.md).
- 5장: T=200, C=190, U=10, L=150, K=4의 합성 계산. 150/190=78.947…%, 4/190=2.105…%, 150/200=75%, 14/200=7%. 주민 비율·모집단 비율·통계적 신뢰구간이 아니다.
- 7장: 120분 빈 구간에 체류90분+양쪽 이동30분씩=150분은 들어갈 수 없다는 합성 제약 예시.
- 8장: [1단계 동일 fixture 계측](../../service-v4/reports/stage1-discovery-validation.md), [원본 JSON](../../service-v4/reports/stage1-discovery-metrics.json). 후보100개 GET의 SQL 호출1,639→32회. SQLite/임시 PostgreSQL의 제한된 계측이며 운영 지연 개선 비율이 아니다. 차트에는 동일 값의 편집 가능한 workbook snapshot을 넣었다.
- 9·10장: [현재 검증 범위](../../service-v4/reports/stage2-validation.md), [공급자 공식 문서 확인](../../service-v4/reports/stage2-provider-verification.md), [12곳 실제 후보 팩](../../service-v4/reports/stage2-pilot-catalog.md). 실제 리뷰 수집·언어 평가·만족도는 미측정이다.

글꼴: 프로젝트에서 사용하는 Pretendard. 기존 라이선스 안내는 [프로젝트 출처 기록](../CREDITS.md)을 따른다. 폰트 파일을 PPTX/Keynote에 내장하지 않았다.
