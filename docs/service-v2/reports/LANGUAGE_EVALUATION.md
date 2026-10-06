# 로컬 언어 판별 실행 결과

**운영 엄격 언어 필터는 미검증 상태로 유지한다.** 실제 모델로 공개 일반 문장 진단을 실행했지만, 도쿄·바르셀로나 음식점 리뷰의 독립 라벨 평가가 아니다. 실제 도시별 리뷰 평가 건수는 각각 0건이다.

실행 시각: 2026-10-01T07:29:22.087727+00:00. Python 3.13.5, macOS-26.6.2-arm64-arm-64bit-Mach-O.
모델: `lingua-2.2.0/review-language-rules-v1`. 설치 binary(모델 포함) SHA256 `51fbd102097b5ec99ef3d7d98c27d6b03adf75132e7901059a457caa1e7b7e07`. 크기 307,235,408 bytes. 모델 패키지 라이선스 Apache-2.0.
프로세스 peak RSS: 226.44 MiB. 분류 실행 10.150초. RSS는 Python/모델/평가 코드 전체 최고값이며 서버 전체 메모리나 여러 worker 합산값이 아니다.

[Lingua 공식 문서](https://github.com/pemistahl/lingua-py)는 offline API와 mixed-language 기능을 설명한다. 혼합 판별을 확정 진실로 취급하지 않고 짧은 문구·낮은 점수·의미 있는 혼합은 unknown으로 보존한다. confidence는 정답 확률이나 현지인 확률이 아니다.

## 데이터와 분리

[FLORES-200 공식 안내](https://github.com/facebookresearch/flores/blob/main/flores200/README.md)의 공개 일반 문장을 사용했다. 원 배포자는 NLLB Team(2022)이며 [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) 조건을 따른다. 이 자료는 식당 리뷰가 아니며 지역·방문자 분포를 나타내지 않는다.

공식 dev에서 언어별 50건, 별도 devtest에서 언어별 200건(ja/ko/es/ca/en)을 선택했다. 개발·heldout 정규화 본문 해시 겹침은 0건이다. 이 실행에서 기준 조정은 하지 않았다. 모델 학습 데이터와의 겹침은 조사하지 않았으므로 학습 독립성도 주장하지 않는다.

## Heldout 진단

| 현지어 프로필 | 전체 문장 | 현지어 precision | 한국어 recall | 한국어 precision | 한국어 TP/FP/FN | unknown | 운영 필터 |
| --- | ---: | ---: | ---: | ---: | --- | ---: | --- |
| tokyo_profile | 1000 | 100.00% | 94.50% | 100.00% | 189/0/11 | 51 (5.10%) | 미검증 |
| barcelona_profile | 1000 | 100.00% | 94.50% | 100.00% | 189/0/11 | 51 (5.10%) | 미검증 |

| 정답 언어 | 정답 지원 수 | TP | FP | FN(unknown 포함) | unknown | precision | recall |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| ca | 200 | 188 | 0 | 12 | 11 | 100.00% | 94.00% |
| en | 200 | 182 | 1 | 18 | 18 | 99.45% | 91.00% |
| es | 200 | 191 | 0 | 9 | 8 | 100.00% | 95.50% |
| ja | 200 | 197 | 0 | 3 | 3 | 100.00% | 98.50% |
| ko | 200 | 189 | 0 | 11 | 11 | 100.00% | 94.50% |
| la | 0 | 0 | 1 | 0 | 0 | 0.00% | null |

혼동 행렬(정답 → 판정, unknown 포함):

```json
{
  "ca": {
    "ca": 188,
    "en": 1,
    "unknown": 11
  },
  "en": {
    "en": 182,
    "unknown": 18
  },
  "es": {
    "es": 191,
    "la": 1,
    "unknown": 8
  },
  "ja": {
    "ja": 197,
    "unknown": 3
  },
  "ko": {
    "ko": 189,
    "unknown": 11
  },
  "la": {}
}
```

한국어 정답이 없는 별도 평가 입력은 recall=null, support_sufficient=false로 계산한다. unknown도 한국어 false negative 분모에 포함한다. 합성 fixture는 규칙·분모·예외 처리 테스트이며 언어 정확도 증거로 집계하지 않는다.

## 재현과 남은 검증

```bash
.venv/bin/python scripts/review_language_eval.py --fetch-corpus
# 이후 캐시와 체크섬을 재사용하는 오프라인 실행
.venv/bin/python scripts/review_language_eval.py
OPENAI_API_KEY=test .venv/bin/python -m pytest tests/test_review_language.py -q
```

다운로드는 위 명시적 CLI 옵션으로만 수행한다. 서버 시작/판별 함수는 모델이나 자료를 다운로드하지 않는다. 파일별 원본 URL·크기·SHA256, 전체 평가 ID와 오류 ID는 LANGUAGE_EVALUATION.json에 있다. 원문은 Git 제외 `data/review-language-eval`에 보존하며 외부 API로 전송하지 않는다.

다음 승인 조건은 도시별 최소 100개 이상의 독립 음식점 리뷰 라벨, 실제 한국어 지원 사례, 현지어 precision/한국어 recall 각각 95% 목표와 언어별 오류·unknown 검토다. 이 보고서의 일반문장 결과만으로 classification_evaluation을 passed로 설정하면 안 된다.
