# Supabase DB CA

공개 루트 인증서이며 비밀키가 아니다. 2026-10-06 hii Dashboard → Database → Settings → SSL configuration → Download certificate에서 내려받았다.

- 공식 다운로드: https://supabase-downloads.s3-ap-southeast-1.amazonaws.com/prod/ssl/prod-ca-2021.crt
- 안내: https://supabase.com/docs/guides/platform/ssl-enforcement
- Subject/Issuer: Supabase Root 2021 CA, Supabase Inc
- 유효 기간: 2021-04-28 10:56:53 UTC ~ 2031-04-26 10:56:53 UTC
- DER SHA-256: `807025ad50d4ed219d2c9c7d299c004f824eb00cf7f65afef607d07b72e6cafa`

Docker 이미지의 `/app/deploy/render-supabase/prod-ca-2021.crt`로 복사한다. DB URL에서 `sslmode=verify-full`과 해당 `sslrootcert`를 지정해 CA와 호스트 이름을 모두 검증한다. TLS 검증을 끄거나 OS 전체의 인증서 저장소를 수정하지 않는다. 공급자가 인증서를 교체하면 공식 HTTPS 배포본의 지문·만료를 확인하고 실제 libpq 연결을 재검증한다.
