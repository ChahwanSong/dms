# 사용자 로그인 연동 가이드 (Portal)

사용자(end-user) 로그인은 **아이디/비밀번호 + 회사메일 6자리 인증번호**다. 이 문서는 (A) 테스트용 Gmail
릴레이에서 **사내 메일 서버로 전환**할 때, (B) 나중에 **AD/SSO로 확장**할 때 각각 무엇을 고치는지 정리한다.

- 설치·설정 절차 본문: [portal-01-setup.md](portal-01-setup.md) §10.2
- 재배포: [redeploy.md](redeploy.md) §3

## 0. 현재 상태

| 항목 | 값 |
|---|---|
| 사용자 로그인 | `POST /api/auth/user/login` — 아이디/비밀번호, 저장소 `portal.user_accounts`(PBKDF2) |
| 계정 생성 | `POST /api/auth/user/request-code` → 메일 인증번호 → `POST /api/auth/user/register` |
| 비밀번호 재설정 | 동일 인증번호 방식 → `POST /api/auth/user/reset-password` |
| 아이디 규칙 | 회사메일 local-part (`test1.user@samsung.com` → `test1.user`) |
| 인증번호 | 6자리, **유효 10분**, HMAC-SHA256(+pepper)으로만 저장, 재발송 쿨다운 60초 |
| 메일 발송 | `PORTAL_SMTP_*` (stdlib smtplib, STARTTLS) |

> **이전의 더미 AD 로그인(`/api/auth/login/ad`)과 `PORTAL_ALLOW_DUMMY_AD`는 제거되었다.** 자격증명을 전혀
> 검증하지 않고 입력 아이디로 로그인시켰고, 그 아이디가 DMS 실행 신원(`requester_id`)이 되므로 인증 우회였다.

## A. 사내 메일 서버로 전환 (다음 단계)

**코드 수정이 필요 없다.** env만 바꾼다.

| 변수 | 테스트(Gmail) | 사내 전환 |
|---|---|---|
| `PORTAL_EMAIL_DOMAIN` | `gmail.com` | **`samsung.com`** (회사 도메인) |
| `PORTAL_SMTP_HOST` / `_PORT` | `smtp.gmail.com` / `587` | 사내 릴레이 호스트 / 포트 |
| `PORTAL_SMTP_SECURITY` | `starttls` | 릴레이 정책에 맞춰 `starttls`\|`ssl`\|`none` |
| `PORTAL_SMTP_USER` / `_PASSWORD` | 계정 / 앱 비밀번호 | 계정 인증이면 그대로, **IP allowlist 릴레이면 둘 다 비운다** |
| `PORTAL_SMTP_FROM` | (빈 값 = USER) | `no-reply@samsung.com` 등 발신 전용 주소 |
| `PORTAL_SIGNUP_ALLOWLIST` | `skychahwan` (필수) | **비워도 된다** — 회사 도메인 자체가 소속 증명이므로 |

```bash
kubectl -n dms-portal set env deploy/dms-portal \
  PORTAL_EMAIL_DOMAIN=samsung.com \
  PORTAL_SMTP_HOST=<사내릴레이> PORTAL_SMTP_PORT=25 PORTAL_SMTP_SECURITY=none \
  PORTAL_SIGNUP_ALLOWLIST-        # 회사 도메인 전환 시 allowlist 해제
kubectl -n dms-portal patch secret portal-secrets --type merge \
  -p '{"stringData":{"PORTAL_SMTP_USER":"","PORTAL_SMTP_PASSWORD":""}}'   # IP allowlist 릴레이인 경우
kubectl -n dms-portal rollout restart deploy/dms-portal
```

**전환 전 확인**: Pod에서 릴레이 포트로 egress가 열려 있어야 한다(HTTP 프록시는 SMTP에 쓸 수 없다).

```bash
kubectl -n dms-portal exec deploy/dms-portal -- \
  python -c "import socket;s=socket.create_connection(('<릴레이>',25),8);print('reachable');s.close()"
kubectl -n dms-portal exec deploy/dms-portal -- \
  python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8090/healthz').read().decode())"
# email_configured=true, email_domain=samsung.com 확인
```

### 도메인 전환 시 기존 계정

기존 계정의 `user_accounts.email`은 예전 도메인(`...@gmail.com`)으로 남아 있지만, **비밀번호 재설정을 한 번
하면 새 도메인으로 자동 수렴**한다(재설정 SQL이 인증 레코드의 주소로 `email`을 갱신). 별도 백필 스크립트가
필요 없다. 아이디(local-part) 자체는 바뀌지 않으므로 로그인은 계속 동작한다.

## B. AD / SSO로 확장 (선택, 향후)

메일 인증 방식을 유지한 채 **AD 로그인을 추가**하는 형태를 권장한다(계정 저장소를 갈아엎지 않아도 된다).

| 파일 | 내용 |
|---|---|
| `src/portal/backend/auth.py` | `POST /api/auth/user/login` 옆에 SSO 콜백 또는 LDAP bind 라우트를 **추가**. 성공 시 `session_user(<id>, ROLE_USER, method=...)` — **`ROLE_USER` 하드코딩 유지** |
| `src/portal/backend/config.py` | AD 서버/base DN 등 `PORTAL_*` 설정 추가 |
| `pyproject.toml` | LDAP simple bind면 `ldap3` (`ldap` extra 존재) |
| `src/portal/frontend/src/pages/Login.tsx` · `api.ts` | 사용자 탭에 "SSO 로그인" 버튼 추가 |

**반드시 지킬 것**

- **`RESERVED_USERNAMES` 검사를 우회하지 말 것.** AD가 내려준 아이디라도 `root`면 DMS에서 uid 0으로 실행된다
  (`src/dms/config.py`: `dm_allow_root_requester=True`, `dm_privileged_requesters={'root'}`). 신원 출처와 무관하게
  `src/portal/backend/email_codes.py`의 검사를 통과시킨다.
- **로그인 성공 시 `request.session.clear()` 후 대입.** 세션 고정 방지.
- **`method` 필드로 인가 판단 금지.** 역할은 "어느 계정 저장소에서 매칭됐는가"로 결정한다
  (`src/portal/backend/security.py` docstring).
- AD 아이디와 사내 POSIX 계정명이 다르면 `user_accounts.posix_username`에 후자를 넣는다. DMS 실행 신원은
  이 값이 우선된다(`src/portal/backend/routers/user_sync.py: _actor`).

## C. 재배포

env/Secret만 바꿨으면 코드 재빌드가 필요 없다.

```bash
kubectl -n dms-portal rollout restart deploy/dms-portal
kubectl -n dms-portal rollout status deploy/dms-portal --timeout=120s
```

코드를 고쳤다면 [redeploy.md](redeploy.md) §1(빌드) + §3.1(`set image`)을 따른다. `kubectl apply`를 쓴 경우
Secret이 placeholder로 덮이므로 [portal-01-setup.md](portal-01-setup.md) §7.2로 **전체 키를 재주입**한다
(`PORTAL_SMTP_USER`·`PORTAL_SMTP_PASSWORD` 포함).
