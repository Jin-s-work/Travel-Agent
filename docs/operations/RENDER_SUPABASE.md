# 기존 Render Free + Supabase Free 전환

2026-10-06 선택한 운영 경로다. 기존 Render `travel-inbox-rag`와 Supabase `hii`를 재사용한다. **코드 전환·로컬 PostgreSQL 검증과 실제 배포를 구분한다.** 현재 URL https://travel-inbox-rag.onrender.com 은 이전 배포이며 이 문서의 개편이 적용됐다는 뜻이 아니다. `sta-saju`는 변경하지 않는다. 신규 유료 리소스·유료 API·자동 증설은 사용하지 않는다.

## 데이터가 남는 위치

| 자료 | 영속 저장 | Render 임시 디스크 삭제 영향 |
| --- | --- | --- |
| 사용자·초대·세션·여행·예약·교정·일정·편집 이력 | Supabase PostgreSQL `travel` 전용 스키마 | 없음 |
| jobs·lease·fencing·SSE 이벤트·비용 예약/정산 | 같은 PostgreSQL | 다음 기동에서 남은 작업 복구 |
| 추출/임베딩 checkpoint·공급자 응답 영수증 | PostgreSQL BYTEA | 완료된 외부 호출 결과 재사용 |
| 메일 원문 | private Storage `travel-private` + SQL manifest/hash | 없음 |
| 여행별 검색 collection·벡터·활성 포인터 | PostgreSQL pgvector + SQL | 변경 전/후 세대 격리 유지 |
| 임시 파일·프로세스 잠금 | `/tmp/travel-cache` | 재생성 |

`STORAGE_BACKEND=local`은 기존 SQLite/Chroma 개발·영구 VM 경로를 유지한다. cloud 설정 누락을 SQLite로 자동 대체하지 않는다. SQLite schema8은 후보 정렬 순서를 명시적인 `sort_order`로 보존한다. cloud schema1은 같은 업무 모델에 객체/벡터/영수증 테이블을 추가한다.

작은 베타를 위해 SQL writer를 PostgreSQL transaction advisory lock으로 직렬화한다. 배포 중 두 프로세스가 잠시 겹쳐도 예산·작업 획득·버전 검사 순서가 보존된다. DB pool 최대5개와 검색 reader별 별도 session connection을 사용한다. 검색은 작은 여행별 collection의 정확 cosine 계산이며 대규모 ANN 처리량을 주장하지 않는다. `pgvector` extension은 `extensions` 스키마가 필요하며 기존 다른 설치를 임의 이동하지 않는다.

## 무료 범위와 기능

확인일 2026-10-06. [Render 공식 Free 문서](https://render.com/docs/free): 웹 서비스 유휴15분 뒤 sleep, 다음 요청에서 기동에 약1분이 걸릴 수 있다. 로컬 파일은 재시작/재배포에 유지되지 않으며 Free에는 persistent disk가 없다. 750 instance-hour는 workspace의 다른 무료 서비스와 공유된다. 외부 DB/Storage 트래픽·계정 한도 및 서비스 정지 조건도 콘솔에서 확인한다.

[Supabase 가격표](https://supabase.com/pricing): Free DB500MB, Storage1GB, egress5GB, 최대2개 무료 프로젝트, 비활동1주 후 pause 가능. 자동 백업은 Free 제공 사항으로 간주하지 않는다. 실제 프로젝트의 다른 자료·사용량도 포함되며 무료 크레딧과 상시 무료 한도를 혼동하지 않는다. Render Oregon↔hii Tokyo 지연/egress는 실제 배포 후 측정해야 한다. 비용0원은 무제한 가동 보장이 아니다.

`pricing-zero.json`: 유료 호출 halted=true, 사용자/전역 일/월 한도0. 메일 자동 추출, 새 임베딩, 자유형 AI 답변, 유료 지도·검색·리뷰 수집은 OFF. 저장된 예약/일정 조회, 수동 예약, 개인 장소 보관, SQL 날짜 질문, 비용 없는 결정적 계산은 유지한다. 업로드 저장 성공과 분석 성공은 다르다. 실제 후보는 도쿄6/바르셀로나6 검수 자료가 있지만 승인된 운영 팩은 각각0이므로 공개 추천 지원을 주장하지 않는다. 엄격 리뷰도 OFF다.

작업은 서버가 깨어 있을 때 처리한다. 잠든 서버를 깨우기 위한 인위적 ping/가짜 keepalive는 만들지 않는다. 새로고침은 기존 job을 다시 조회하며 새 외부 호출을 만들지 않는다. HTML 형태의 cold-start 오류도 화면에 재접속 안내로 표시하고 POST를 자동 재전송하지 않는다.

## 기존 계정 준비 순서

1. `hii`가 Healthy인지 확인한다. 기존 public 테이블/다른 버킷을 삭제하지 않는다. Storage에 **private** `travel-private` 버킷을 만든다. 브라우저용 공개 읽기 정책을 만들지 않는다. 파일 제한은1MiB, 파일 내용은 서버에서도 txt/eml 검사한다. 서버는 octet-stream으로 전송하므로 MIME 제한을 추가하면 이를 허용해야 한다.
2. Connect에서 **Session pooler / port5432 / IPv4** 연결을 선택한다. Transaction pooler6543은 session reader pin과 맞지 않아 거절한다. [연결 방식](https://supabase.com/docs/guides/database/connecting-to-postgres). DB 비밀번호를 모르면 사용자가 직접 관리해야 한다. 다른 앱의 비밀번호를 임의 초기화하지 않는다.
3. 기존 Render 서비스 Environment에 `deploy/render-supabase/.env.example`의 값을 입력한다. 비밀을 채팅/Git에 붙이지 않는다. DB URL의 비밀번호는 URL 인코딩한다. `sslmode=verify-full&sslrootcert=/app/deploy/render-supabase/prod-ca-2021.crt`를 사용하며 인증서 검증을 끄지 않는다.
4. `SUPABASE_URL=https://whudlguhvmrbxudybnme.supabase.co`, `SUPABASE_STORAGE_BUCKET=travel-private`. `SUPABASE_SECRET_KEY`는 서버 전용 secret(또는 기존 service_role)이다. anon/publishable 키로 대체하지 않는다. [API key](https://supabase.com/docs/guides/getting-started/api-keys). 이 자격 증명은 프로젝트 전체에 강한 권한이 있으므로 Render secret에만 보관한다. 브라우저는 FastAPI 인증 API만 호출한다.
5. OIDC 등록값과 긴 `SESSION_SECRET`을 넣는다. `PUBLIC_BASE_URL=https://travel-inbox-rag.onrender.com`, OAuth callback `https://travel-inbox-rag.onrender.com/api/v2/auth/callback`. Supabase 저장소를 쓴다고 기존 초대 Authlib 인증을 Supabase Auth로 바꾸지 않는다. 개발 인증 우회는 없다. 인증 설정 없으면 production launcher가 HTTP 포트를 열지 않는다.
6. 검증된 코드를 Git에 반영하고 기존 Blueprint의 변경 diff를 검토한다. `plan: free`, disk 없음, instance1, autoDeploy off를 유지한다. 기존 Blueprint에서 `sync:false` 비밀값이 자동 추가된다고 가정하지 말고 Environment에서 확인한다. 새 서비스를 중복 생성하지 않는다. 본 작업의 로컬 변경만으로 Render 코드가 갱신되지는 않는다.
7. schema migration은 배포 기동 때 transaction으로 적용한다. DB/schema 오류는 기동 실패다. 공개 bucket은 거절하고, Storage 일시 장애는 원문 기능만 unavailable로 두어 SQL 예약은 계속 조회한다. `/health/live`와 `/health/ready`는 유료 API를 호출하지 않는다.
8. 관리자 초대 → 준비된 계정 로그인 → 합성 자료로 원문 업로드/수동 예약/날짜 조회/일정 조회 → 재시작 후 재조회 순서로 실환경을 검증한다. 초대 메시지는 자동 발송하지 않는다. 실제 OAuth·HTTPS 쿠키·CSRF·소유권·외부 백업 왕복이 확인되기 전 최대5명 확대를 하지 않는다.

전용 스키마는 PostgREST 노출 대상에 추가하지 않는다. 모든 테이블에 RLS를 활성화하고 anon/authenticated의 schema/table 권한을 회수한다. 서버 DB 소유자는 RLS 우회 권한이 있으므로 **서버의 user/trip 소유권 검사가 실질적인 tenant 경계**다. “RLS만으로 사용자 분리 완료”라고 설명하지 않는다. 기존 bucket 공개 전환은 원문 노출 위험이 있으므로 금지한다.

## 기존 자료 이관

기존 로컬 원문7개는 소유자 미확정 inventory 상태다. 이를 임의 사용자에게 넣지 않는다. 아래 도구는 소유권이 있는 schema7/8 SQLite snapshot만 처리한다. 기존 Chroma 자체를 그대로 가져오지 않고 SQL 예약을 먼저 유지하며 검색 재구축을 별도 표시한다.

```bash
# 읽기 전용, cloud 연결 없이 실행 가능
.venv/bin/python -m src.storage.transfer import-sqlite \
  --source /absolute/snapshot/service.sqlite3 \
  --documents-root /absolute/snapshot/documents
# 소스 writer를 정지하고, 비어 있는 목적 스키마/비공개 버킷을 확인한 후
.venv/bin/python -m src.storage.transfer import-sqlite \
  --source /absolute/snapshot/service.sqlite3 \
  --documents-root /absolute/snapshot/documents --apply --offline
```

secret 환경 변수는 운영자 터미널/호스팅 비밀 저장소에서 공급한다. dry-run은 파일/hash/소유권/크기/DB integrity/FK와 건수를 확인한다. apply는 목적을 maintenance로 닫고 원문 객체 manifest를 먼저 기록한 뒤 SQL을 한 번에 등록한다. 실패한 업로드는 고아 manifest로 남아 재검사할 수 있다. 기존 사용자가 있는 목적은 거절하며 덮어쓰지 않는다. 성공 후 read_only, 세션/미사용 초대 회수, 미완료 작업 취소, 미정산 비용 unknown, 검색 rebuild_required, 리뷰 OFF로 둔다. 확인 전 자동 정상 모드 전환은 없다.

## 무료 백업·복원

Render 임시 디스크와 같은 Supabase 프로젝트는 각각 독립 백업이 아니다. 운영자 컴퓨터 또는 **다른 장애 범위의 기존 저장소**에 암호화 파일을 둔다. 계정 없는 새 유료 bucket은 만들지 않는다. `BACKUP_ENABLED=0`은 기존 SQLite 전용 scheduler가 cloud에서 실행되지 않게 한다. 수동 운영자 backup만으로 “자동 백업 완료”라고 주장하지 않는다.

```bash
# 키는 별도로 안전하게 보관한 base64 32byte BACKUP_ENCRYPTION_KEY
.venv/bin/python -m src.storage.transfer snapshot --output /safe-offhost/travel-full.enc
.venv/bin/python -m src.storage.transfer checkpoint --output /safe-offhost/travel-latest-checkpoint.enc
# 폐쇄된 새 경로에 복원. 원 운영 DB를 덮어쓰지 않음
.venv/bin/python -m src.storage.transfer restore-local \
  --archive /safe-offhost/travel-full.enc \
  --checkpoint /safe-offhost/travel-latest-checkpoint.enc \
  --destination /isolated/restore
```

PostgreSQL repeatable-read snapshot + 원문 hash 검사 + 활성 세대 manifest를 AES-GCM 암호화한다. 벡터와 진행 중 provider receipt는 full restore에서 재활성화하지 않는다. 최신 삭제/권한/비용 checkpoint를 적용하고 폐쇄 상태로 검사한다. 예약·교정·일정이 보존되고 삭제 자료가 없는지 확인한 뒤, 위 import 명령으로 **빈 목적 스키마**에 옮긴다. 복원 상태에서는 검색 재생성 비용도 승인 전 차단한다. 단순 Render 재시작에서는 원격 벡터/receipt가 그대로 유지되므로 재호출하지 않는다.

가장 최근 full snapshot 이후 신규 자료는 잃을 수 있다. 가장 최근 offhost checkpoint 이후 삭제는 입증할 수 없으므로 개방 전에 대조한다. 현 cloud profile에는5분 자동 tombstone 외부 전송이 연결되어 있지 않다. 운영 RPO/RTO 약속을 하지 않으며 지인 확대 전 자동화·실제 복원 측정이 필요하다. Render가 잠든 동안의 작업 실행/백업도 보장하지 않는다.

기능 OFF는 SQL controls로 외부 작업을 중지한다. image rollback은 동일 cloud schema를 이해하는 이미지에서만 가능하다. SQLite 전용 구형 이미지는 cloud DB를 읽을 수 없으므로 rollback 대상으로 쓰지 않는다. DB restore는 별도 격리·검증 절차이며 image rollback과 다르다.

## 자원 상한과 검증 경계

원문은 앱 전체 pending/active/orphan을 포함100MiB, 파일당1MiB로 제한한다. 이는 Supabase 전체 프로젝트/egress 한도를 보장하는 기능이 아니다. PostgreSQL의 벡터·job events·artifact가 DB500MB를 함께 소비하므로 실제 사용량을 관측하고 한도 전에 운영 read_only로 전환한다. 자동 증설은 없다. 외부 네트워크가 없는 로컬 pgvector 시험의 지연·메모리는 Supabase/Render 실서비스 수치로 사용하지 않는다.

재현 명령·결과·코드/실배포 구분은 [검증 보고서](../service-v2/reports/render-supabase-validation.md), [진행 기록](../service-v2/IMPLEMENTATION_STATUS.md)을 따른다.
