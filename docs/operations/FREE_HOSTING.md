> **2026-10-06 적용 변경:** 사용자가 기존 Render Free + Supabase Free(`hii`)를 선택했다. 운영 DB는 PostgreSQL, 원문은 private Storage, 검색은 pgvector로 전환한다. 아래 SQLite/Chroma/영구 VM 설명은 로컬 모드와 이전 설계 이력이다. 현재 배포·비용·백업 계약은 [전환 안내](RENDER_SUPABASE.md)를 우선 적용한다. 새 버전 실제 배포는 별도 검증 상태다.

# 비용 0원 우선 배포안

2026-10-06 사용자 결정: **새 유료 리소스와 유료 API 사용을 하지 않는다.** 앞서 제안한 Render 월$7.25는 보류다. 계정과 실제 무료 자원 여유를 확인하기 전에는 서버를 생성하지 않는다. 현재 실제 운영 URL은 없으며 아래는 코드·로컬 검사와 외부 준비를 구분한다.

## 선택지

| 구성 | 현재 코드 재사용 | 지속 저장 / 중단 | 판정 |
| --- | --- | --- | --- |
| Render Free 단독 | FastAPI는 실행 가능하지만 현재 운영 launcher는 영구 mount가 없어 기동을 거절 | 15분 유휴 후 sleep, 재시작·재배포·sleep 시 SQLite와 로컬 업로드 유실, 영구 disk 불가 | 여행 자료 보존 요구에 맞지 않음 |
| Oracle Always Free VM | Docker·SQLite·Chroma·단일 dispatcher 재사용 | VM의 지속 disk에 보존. 무료 자원 부족·idle 회수·SLA 없음 | 계정 사용 가능하면 우선 후보 |
| Render Free + Supabase Free | DB SQL/트랜잭션/파일/벡터/작업복구 이관 필요 | 앱 cold start, 외부 DB/file 지속. Supabase Free도1주 미활동 시 pause | 카드 없는 경로를 선호할 때 별도 개편안, 현재 미구현 |

[Render 공식 제약](https://render.com/docs/free), [Oracle Always Free](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm), [Supabase Free](https://supabase.com/pricing). Supabase 공개 무료 범위는 DB500MB/file1GB, 프로젝트2개다. 자동 백업은 별도 준비가 필요하다. SQLite를 object storage나 GitHub에 주기적으로 덮어쓰는 방식을 운영 DB로 사용하지 않는다.

Oracle 현재 공개 A1 무료 범위는 **월1,500 OCPU-hour/9,000GB-hour**로2OCPU/12GB 상당이다. 이전4OCPU/24GB 정보로 리소스를 생성하지 않는다. boot+block 총200GB 한도는 계정 전체가 공유한다. 제안 시작은 A1 1OCPU/2GB와50GB boot volume, home region의 Always Free Eligible Linux 이미지다. 여유가 없다면 생성 실패를 유료 전환으로 해결하지 않는다. 오직 무료 자원인지 콘솔에서 재확인한다. 이 문서는 quota 보증이나 계정 생성 승인이 아니다.

신규 Oracle 가입은 카드 본인 확인과 임시 승인 보류가 있을 수 있다. FAQ에 따르면 본인 확인 보류는 실제 청구가 아니지만 은행에서 해제되기까지 시간이 걸릴 수 있다. 가입 약관/카드 입력은 사용자가 직접 처리한다. 무료 VM은 idle 회수와 공급 부족이 있어 ‘항상 켜짐 보장’은 제공하지 않는다. [Oracle 가입·카드·SLA FAQ](https://www.oracle.com/cloud/free/faq/)

## 현재 준비한 무료 VM 구성

`deploy/free-vm/compose.yaml`과 선택 HTTPS proxy인 `https.yaml`을 추가했다. VM 자체를 만들거나 클라우드 결제 API를 호출하지 않는다. Caddy이미지는 digest고정이며 양쪽 컨테이너가 UID1000으로 실행된다. 앱 image는 read-only, `/tmp`만 제한 tmpfs, SQL/원문/벡터는 명시한 host persistent directory에 bind한다. 앱7860은 외부에 공개하지 않고 proxy80/443만 사용한다. 일반 HTTP access log를 켜지 않아 callback/개인 URL을 로그에 쌓지 않는다. [Caddy HTTPS](https://caddyserver.com/docs/automatic-https), [내부 포트 설정](https://caddyserver.com/docs/caddyfile/options#https-port)

가격 파일은 `pricing-zero.json`: halted=true, 사용자/전역 일/월 상한 모두0이다. 이 profile은 OpenAI/Tavily/Apify/Maps API key를 빈 값으로 강제한다. `.env`에 우연히 키가 들어 있어도 compose 설정이 덮어쓴다. 가격 파일은 read-only다. 유료 계정으로 자동 전환하거나 무료 sleep을 피하려고 불필요한 heartbeat 요청을 보내지 않는다.

무료 VM이어도 유료 AI API가 무료로 바뀌지는 않는다. 이번0원 profile에서:

- 로그인·초대·여행·수동 예약·개인 보관함·저장 자료 조회·SQL 날짜 질문은 유지한다.
- 기존 추천/일정 저장값 조회와 비용 없는 결정적 계산은 사용할 수 있다. 실제 후보 승인·사실·경로 조건은 계속 별도 검사한다.
- OpenAI 메일 자동 추출·임베딩·자유형 AI 답변·유료 지도/리뷰 호출은 차단된다. 업로드와 자동 분석 성공을 혼동하지 않는다.
- 무료 AI 모델로 자동 교체하거나 데이터가 없는 도시를 합성 카드로 채우지 않는다. 무료 모델 도입은 품질·데이터 이용 범위·일일 quota를 따로 검증해야 한다.

## VM 준비 후 실행

실제 무료 VM과 계정이 준비되면 서버에서만 아래 단계를 수행한다. 무료 계정 가입·리소스 생성·방화벽 공개는 아직 실행하지 않았다. Docker Engine/Compose는 [공식 설치 안내](https://docs.docker.com/engine/install/ubuntu/)를 따른다.

1. Always Free home region/shape/현재 계정 총사용량을 확인한다. Ubuntu image와 persistent boot disk를 사용한다. 기존 disk를 초기화/포맷하지 않는다. 유료 marketplace image·추가IP·NAT·로드밸런서·도메인을 만들지 않는다.
2. 무료 계정 조건 또는 기존 무료 DNS에서 소유한 hostname을 확보한다. 외부80/443은 HTTPS발급/접속용, SSH22는 본인IP로 제한한다. PUBLIC_BASE_URL과 OIDC callback은 같은 hostname을 사용한다. 아직 hostname/DNS/인증서는 준비되지 않았다.
3. 코드만 VM으로 배치한다. 로컬`.env`, private mail, DB는 전송하지 않는다. 기존 자료 이관은 소유권을 명시하고 별도 복원 절차를 따른다.
4. 서버 디렉터리를 만들고 runtime UID1000에게 필요한 경로만 소유권을 준다. `chmod777`로 해결하지 않는다.

```bash
# 이미 준비된 VM에서, 존재하는 자료를 덮어쓰지 않는 신규 디렉터리 기준
sudo install -d -o 1000 -g 1000 -m 0700 /srv/travel-agent/data /srv/travel-agent/tls/data /srv/travel-agent/tls/config
cd deploy/free-vm
cp .env.example .env
chmod 600 .env
# .env에 PUBLIC_HOST, PUBLIC_BASE_URL, SESSION_SECRET, 실제 OIDC 값을 서버에서 입력
# OIDC callback: https://실제호스트/api/v2/auth/callback
# SESSION_SECRET 등 값은 채팅·공용 로그·Git에 붙여넣지 않는다.
docker compose --env-file .env -f compose.yaml -f https.yaml config --quiet
docker compose --env-file .env -f compose.yaml -f https.yaml up -d --build
```

`.env`가 이미 있으면 `cp`로 덮어쓰지 않는다. 별도로 확인한 기존값을 유지한다. 공개 호스트가 비었거나 host directory가 없거나 인증 설정이 잘못되면 기동을 거절한다. 상세 `docker compose config` 출력은 secret을 노출할 수 있어 `--quiet`를 사용한다. port상태/ready health와 초대 계정을 확인한다. Compose restart policy는 VM 중단/회수를 복구하는 기능이 아니다.

## 무료 백업과 복원

기존 AES-GCM/offhost 기능을 재사용한다. 초기 설정은 BACKUP_ENABLED=0이며 외부 bucket 설정과 왕복 복원이 확인되기 전에는 지인 확대를 하지 않는다. Oracle Object Storage의 무료 한도/요청 수 또는 기존 B2 무료 잔여량은 실제 계정에서 확인해야 한다. snapshot8일+5분 checkpoint8일의 총용량, API건수, 다른 앱 사용량을 합산한다. free credit를 영구 무료로 계산하지 않는다.

Oracle Object Storage는 boot disk와 별도 저장 장애 범위지만 같은 계정 정지까지 독립적인 백업은 아니다. 운영자 로컬에도 암호화 사본을 주기적으로 보관한다. 계정무료한도·tombstone 최신성을 확인하지 않고 자동 저장을 켜지 않는다. 백업·rollback·복원 read-only gate는 [RUNBOOK](RUNBOOK.md)을 따른다. Always Free 라벨과 무료 계정이 확인되지 않은 유료 bucket으로 대체하지 않는다.

이 profile의 `/tmp`는128MiB다. 백업의 임시 파일도 이 공간을 사용하므로 실제 자료 크기로 백업·복원을 검사한 뒤 활성화한다. 공간이 부족하면 메모리 예산 안에서 staging 구성을 조정해야 하며 현재 설정을 대용량 백업에 검증한 것으로 취급하지 않는다.

## 검증/남은 일

`check_free_vm.py`는 임시 Docker project/host directory에서 실제 production launcher·read-only image·비루트 실행을 확인하고, 합성 사용자/수동예약 저장→재시작→유지와0원 policy의 공급자 전송 전 차단을 검사한다. 테스트용 합성 identity는 script 내부 주입이며 운영 authentication bypass가 아니다. [실행 JSON](../service-v2/reports/free-vm-profile.json)

```bash
.venv/bin/python scripts/check_free_vm.py --report docs/service-v2/reports/free-vm-profile.json
```

실행 결과는 **passed=true**이며 컨테이너 재시작 후 여행1개·수동예약1개가 유지됐다. 공급자 호출 전에 예산 차단을 확인했고 실제 유료 공급자 호출은0회다. Caddy configuration validation도 통과했다. DNS·실제 공개 HTTPS 인증서·외부 OIDC·Oracle VM재기동·실제 offhost는 미검증이다. 현재 추가 지출0, 신규 클라우드 리소스0이다. Oracle계정이 없거나 카드 등록을 원하지 않으면 Render+Supabase 개편 범위를 확정한 뒤 진행한다. 기존 무료 계정의 존재만으로 결제/사용 범위를 추정하지 않는다.
