# v4 발표·README 검증

2026-10-07. 새 발표는 Artifact Tool로 작성하고 macOS Keynote에서 실제 `.key`로 저장했다. 사용자 수정이 있던 상위 폴더의 이전 Keynote는 덮어쓰지 않았다.

## 확인한 것

- PPTX 10장, 16:9, 대본 배분 합계510초. 슬라이드별 발표자 메모와 Markdown 대본의 일치 검사.
- 전체10장의 PNG를 실제로 읽어 여백·글자 잘림·이미지·단위 확인. 3장의 이미지가 페이지 번호를 가리던 배치를 수정했다.
- Keynote의10장을 모두 화면에서 검수하고 메모 포함을 확인했다. 8장 SQL 그림은 native 2D 가로형 차트로 인식한다. 값1,639와32, 전후 범주가 맞다.
- Keynote에서 SVG 로고가 사라지는 문제를 원본 PNG 변환으로 해결했다. 테마 기본 Aptos 누락 경고도 기본 Pretendard 설정으로 해결하고 다시 가져와 저장했다.
- 패키지 무결성·텍스트 배치·제목 fit·폰트 정책·차트 workbook 연결·첫 Artifact Tool 재가져오기를 통과했다. [도구 영수증](artifact-validation.json).
- README의 현지어/유명한 곳/주변 참고 구분, 실제12곳 팩·100도시 입력·strict OFF, 최신 시험과 과거 운영 계측을 구분했다.
- README와 발표 문서의 로컬 링크 및 최종 파일 hash는 [문서 검사](validation.json)에 기록한다.

## 표현의 경계

- 75%와7%는 합성 미판별 경계 계산이며 실제 식당 수치·주민 비율이 아니다.
- SQL 감소는 같은 합성 fixture의 application query 횟수다. 운영 응답시간의 같은 비율 개선을 주장하지 않는다.
- 수집 어댑터·관리자 화면 구현과 실제 리뷰·이용·언어 품질 완료는 별개다. 엄격 언어 추천 OFF를 슬라이드와 대본 양쪽에 남겼다.
- 기존 앱 screenshot은 새 UI의 브라우저 검증 증거가 아니다.

## 아직 확인하지 않은 것

실제 낭독 시간, 강의실 프로젝터, Windows PowerPoint·웹 Google Slides·모바일 Keynote의 렌더링, native 차트 값을 바꾸거나 크기 조절한 뒤 저장하는 편집 호환성은 미검증이다. macOS Keynote에서 열기·화면·메모·native chart 인식·저장을 확인한 범위로 한정한다.

재실행:

```bash
python3 scripts/validate_presentation_docs.py --v4 --write-manifest
```
