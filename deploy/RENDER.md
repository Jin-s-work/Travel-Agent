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
  업로드한 메일과 벡터 인덱스가 사라집니다. 계속 쓰려면 유료 디스크를 붙이거나
  외부 벡터 DB로 옮겨야 합니다.
- 헬스체크(`/api/health`)는 무거운 모듈을 건드리지 않으므로 빠르게 응답합니다.

## 확인

배포 후 이 순서로 봅니다.

```
https://<서비스명>.onrender.com/api/health     → {"ok": true}
https://<서비스명>.onrender.com/api/bookings   → 인덱스가 비어 있으면 count 0
https://<서비스명>.onrender.com/               → 앱 화면
```

첫 화면에서 "아직 등록된 예약이 없습니다"가 나오면 정상입니다. 메일 탭에서
`tests/sample_emails/`의 파일을 올려 인덱싱하면 일정이 채워집니다.

## 로그

빌드나 실행이 실패하면 Render 대시보드의 **Logs** 탭을 봅니다. 자주 나오는 것:

- `ModuleNotFoundError` → `requirements.txt` 확인
- 포트 관련 오류 → `Dockerfile`의 `CMD`가 `${PORT:-7860}`을 쓰는지 확인.
  Render는 `PORT`를 주입하므로 하드코딩하면 헬스체크가 실패합니다.
- `OPENAI_API_KEY가 설정되지 않았습니다` → Environment 탭에 키 등록
