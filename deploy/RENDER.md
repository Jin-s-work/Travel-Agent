# Render 배포

무료 티어에 Docker로 올립니다. 저장소에 `render.yaml`과 `Dockerfile`이 있어
대시보드에서 저장소만 연결하면 됩니다.

> [!NOTE]
> Hugging Face Spaces는 2026년 기준 Docker Space에 PRO 구독이 필요합니다.
> 무료는 정적 Space뿐이라 FastAPI 백엔드를 올릴 수 없어 Render를 씁니다.

## 절차

1. https://render.com 가입 후 GitHub 계정 연결
2. **New → Blueprint** 선택
3. `Jin-s-work/Travel-Agent` 저장소 선택 → `render.yaml`이 자동으로 읽힙니다
4. 생성 직후 **Environment** 탭에서 키를 넣습니다

   | 키 | 필수 |
   | --- | --- |
   | `OPENAI_API_KEY` | 필수 |
   | `TAVILY_API_KEY` | 선택 (없으면 웹 검색만 비활성) |

5. 저장하면 자동으로 다시 빌드됩니다

`sync: false`로 표시해 두었기 때문에 키는 블루프린트가 아니라 대시보드에만
저장됩니다. 저장소에 키가 들어갈 일이 없습니다.

## 무료 티어에서 알아둘 것

- **15분 동안 요청이 없으면 잠듭니다.** 다시 깨어나는 데 1분 가까이 걸립니다.
  무거운 모듈을 지연 로딩하므로 깨어난 뒤 첫 질문은 더 느립니다.
- **디스크가 영구 저장이 아닙니다.** 재배포하거나 인스턴스가 재시작하면
  업로드한 메일과 벡터 인덱스가 사라집니다.
  기동 시 인덱스가 비어 있으면 `seed/parsed.json`으로 데모 예약을 자동으로
  채우므로 데모 링크는 항상 동작합니다(LLM 파싱 없이 임베딩만 하므로 수 초).
  다만 **사용자가 직접 올린 메일은 복구되지 않습니다.** 그것까지 유지하려면
  유료 디스크를 붙이거나 외부 벡터 DB로 옮겨야 합니다.
  자동 복구를 끄려면 환경변수 `SEED_ON_EMPTY=0`을 설정합니다.
- 헬스체크(`/api/health`)는 무거운 모듈을 건드리지 않으므로 빠르게 응답합니다.

## 확인

배포 후 이 순서로 봅니다.

```
https://<서비스명>.onrender.com/api/health     → {"ok": true}
https://<서비스명>.onrender.com/api/bookings   → 시드가 채워지면 count 7
https://<서비스명>.onrender.com/               → 앱 화면
```

기동 직후에는 시딩이 끝나기 전이라 `count 0`이 나올 수 있습니다. 로그에
`시드 완료: 청크 7개`가 찍히면 완료입니다. 자기 메일로 시험하려면 메일 탭에서
직접 올리면 되고, 그 경우 인덱스에 데이터가 있으므로 시드는 동작하지 않습니다.

## 로그

빌드나 실행이 실패하면 Render 대시보드의 **Logs** 탭을 봅니다. 자주 나오는 것:

- `ModuleNotFoundError` → `requirements.txt` 확인
- 포트 관련 오류 → `Dockerfile`의 `CMD`가 `${PORT:-7860}`을 쓰는지 확인.
  Render는 `PORT`를 주입하므로 하드코딩하면 헬스체크가 실패합니다.
- `OPENAI_API_KEY가 설정되지 않았습니다` → Environment 탭에 키 등록
