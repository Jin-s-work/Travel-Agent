---
title: Travel Inbox RAG
emoji: 🧳
colorFrom: indigo
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
short_description: 여행 예약 메일을 일정으로 정리하고 근거와 함께 답합니다
---

# Travel Inbox RAG

> [!IMPORTANT]
> Hugging Face Spaces는 Docker Space에 PRO 구독을 요구합니다(무료는 정적 Space만).
> 무료 배포는 [Render](RENDER.md)를 쓰고, 이 파일은 PRO 구독 시 사용합니다.


여행 예약 확인 메일(항공·숙소·렌터카·투어)을 올리면 일정으로 정리하고,
"셋째 날 체크아웃 몇 시야?" 같은 질문에 **근거 메일과 함께** 답합니다.

홈 화면에 추가하면 앱처럼 실행됩니다.

## 사용법

1. 메일 탭에서 예약 확인 메일(`.txt` / `.eml`)을 올립니다.
2. 인덱싱이 끝나면 일정 탭에 카드가 날짜순으로 나타납니다.
3. 질문 탭에서 물어보면 근거와 함께 답합니다.

메일 1건을 인덱싱할 때 LLM을 한 번 호출합니다.

## 필요한 Secrets

Settings → Variables and secrets 에 등록하세요.

| 이름 | 필수 | 용도 |
| --- | --- | --- |
| `OPENAI_API_KEY` | 필수 | 정보 추출·임베딩·답변 생성 |
| `TAVILY_API_KEY` | 선택 | 날씨·환율 등 일반 정보 웹 검색 |

`TAVILY_API_KEY`가 없으면 웹 검색 도구만 비활성화되고 나머지는 정상 동작합니다.

## 알려진 제약

- **Space를 재시작하면 업로드한 메일과 인덱스가 사라집니다.** Spaces의 파일
  시스템은 영구 저장이 아닙니다. 계속 쓰려면 Persistent Storage를 켜거나
  외부 벡터 DB로 바꿔야 합니다.
- 무료 CPU 티어에서는 첫 질문에 LangChain 로딩으로 수십 초가 걸립니다.
  서버 기동 자체는 즉시 되지만 첫 응답이 느립니다.
- 여러 사용자가 동시에 쓰면 같은 인덱스를 공유합니다. 개인 사용을 전제로
  만들었으니 공개 Space에는 실제 예약 메일을 올리지 마세요.
- 오프라인에서는 앱 화면만 열립니다. 예약 데이터는 서버가 필요합니다.

## 구성

`api.py`(FastAPI)가 `/api/*` JSON과 `web/`의 정적 파일을 한 프로세스에서
서빙합니다. 같은 출처라 CORS 설정이 없습니다.

전체 설계 문서와 평가 결과는 GitHub 저장소의 README를 참고하세요.
