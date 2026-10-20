# 콘텐츠·여행 조건 추천 모델 검증

2026-10-08 · `5da54dd` 이후 로컬 구현. 이 작업에서 운영 배포나 GitHub push를 실행하지 않았다.

## 변경

- 기존 `general_v3` 규칙 모델에 취향이 실제 순위로 연결되지 않던 부분을 확인했다. 새로운 요청은 `hybrid_v4` 콘텐츠·조건 모델로 처리한다. 이전 버전은 재현 가능하게 유지한다.
- TF-IDF 코사인 태그 유사도, 같은 플랫폼·도시·분류 내 수축 평점, 명시한 출발점의 직선거리를 결합한다. 입력별 프로필과 가중치·상수·후보 corpus 해시를 저장한다.
- 미확인 성분은 null, 비중 재분배 없음. 순위용 utility는 별도 진단에 보관하고 기존 장소 총점은 계속 null이다. 공식 자료의 자격 통과를 맛 100점으로 표시하지 않는다.
- 언어·지점·사용권·휴무·인원·예산·이동 필수 조건, 방문 미확인 그룹, 근거 소그룹, 다양성 제한을 재사용한다. 사용자가 반영을 선택한 피드백만 우선순위를 낮춘다.
- 출처 철회 시 후보 집단에 의존하는 파생 점수까지 숨긴다. 원본 추천 snapshot과 이전 모델 결과는 수정하지 않는다.
- 카드에 계산 근거 대화상자를 연결했다. UI 계약 시험이며 실제 브라우저 시각 검증과는 구분한다.

## 자동 시험

| 범위 | 실제 결과 |
|---|---|
| 최종 Python 전체 | **1,156 passed / 17 skipped / 0 failed / 2 warnings / 182.21초** |
| 최종 JavaScript 전체 | **177 passed / 0 failed / 0 skipped** |
| 웹 자산 해시 | 통과 |
| 유료 공급자·운영 API 호출 | 0회 |
| PostgreSQL 별도 통합 | 이번 변경에서는 미실행. 앞 단계 기록과 분리 |
| 실제 로그인 브라우저·모바일 | 이번 변경에서는 미검증. 저장된 브라우저 권한 차단을 우회하지 않음 |
| 실제 독립 관련성·방문 만족도 | 미측정 |
| 엄격 현지어 기능 | OFF 유지 |

Python 시험에는 새 모델 22개 시험, 기존 추천·메일·일정·인증·jobs·복구 회귀를 포함한다. 신규 API 경로로 모델 snapshot 저장과 반복 조회를 확인했다. 자격을 통과하지 못한 장소가 취향 점수로 구제되지 않는지, 결측과 0이 구분되는지, 다른 플랫폼을 합치지 않는지, 명시적 피드백 반영, 철회 후 점수 차단을 검증했다.

원본 작업 디렉터리의 첫 전체 실행은 **1,147 passed / 8 failed / 17 skipped**였다. 7건은 새 순위 utility를 기존 장소 점수 필드에 노출하던 계약 문제였고 필드를 분리해 수정했다. 나머지는 강제 종료 복구 시험의 자식 프로세스 30초 타임아웃이었다. JavaScript는 기존 `fake-indexeddb` 모듈을 읽을 때 constructor 오류가 발생했다. 원본 Git pack을 읽지 못하는 현상도 관찰했다.

검증은 `/private/tmp/going-hybrid-verify.uPktYX`에 GitHub `5da54dd`를 새로 받아 변경 파일의 바이트·SHA256을 대조한 사본으로 옮겼다. `.env`·운영 자료·원본 node_modules를 복사하지 않고 잠금 파일의 `fake-indexeddb 6.2.5`만 `npm ci --ignore-scripts`로 설치했다. 기존 작업 디렉터리·사용자 Keynote 변경·Git 객체를 수정하거나 복구하지 않았다. **lease·heartbeat·테스트 타임아웃을 늘리지 않았다.** 같은 수정 코드의 전체 시험이 위 결과로 통과했다. 경고 2건은 기존 Starlette/Authlib의 httpx deprecation이다.

최종 로그: [Python](hybrid-v4-python-tests.log), [JavaScript](hybrid-v4-js-tests.log). 초기 Python 실패 기록도 [별도 보존](hybrid-v4-initial-python.log)했다.

```sh
# /private/tmp/going-hybrid-verify.uPktYX에서 실제 실행
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHON_DOTENV_DISABLED=1 PYTHONDONTWRITEBYTECODE=1 \
TRAVEL_TEST_POSTGRES_DSN= HTTP_PROXY=http://127.0.0.1:9 \
HTTPS_PROXY=http://127.0.0.1:9 ALL_PROXY=http://127.0.0.1:9 \
/private/tmp/going-stage1-native313/bin/python -m pytest tests -q -p no:cacheprovider --tb=short

node --test tests/*.cjs
/private/tmp/going-stage1-native313/bin/python scripts/version_web_assets.py --check
```

## 추천 성능의 범위

[합성 개발 비교](hybrid-v4-benchmark.md)는 동일한 12개 질의·후보·clock으로 `general_v3`와 `hybrid_v4`를 비교한다. NDCG@3는 **0.4155 → 0.9954**, Precision@3는 **0.3889 → 0.7778**이었다. 필수 조건 위반은 두 모델 모두 0건, 순서 변경 후 결정성은 12/12이다. 자료의 취향·거리 기대사항을 실제 순위로 연결했다는 개발 증거다.

**이 수치를 99.5% 정확도, 실사용 품질 향상, 학습 성과라고 발표하지 않는다.** 합성 시나리오와 정답은 개발자가 작성했고 독립 holdout은 0개다. 기존 규칙 모델은 애초 취향 순위를 최적화하지 않았다는 비교 한계도 있다. 실제 후보·독립 평가자가 매긴 정답으로 같은 도구를 재실행해야 한다. 함수 지연은 추가 계산 때문에 기존 모델보다 약간 증가할 수 있으며 정확도·속도 모두 개선됐다고 주장하지 않는다.

재실행·수식·실제 평가 절차·발표 설명은 [모델 설명서](../RECOMMENDATION_MODEL.md)에 있다. 새로운 모델은 추가 유료 호출을 하지 않으며, 리뷰 수집과 실제 언어 품질 검증의 미완료를 해결했다고 주장하지 않는다.
