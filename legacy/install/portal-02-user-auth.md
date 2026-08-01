# 포탈 사용자 인증 (회사메일 인증 계정) — 설정 · 연동 · 구현 가이드

사용자(end-user)는 **아이디/비밀번호**로 로그인하고, 계정 생성·비밀번호 재설정은 **회사 메일로 받은
6자리 인증번호**로 본인 확인한다. 운영자 토큰을 쓰지 않는다 — **회사 메일함 통제권이 곧 인가**다.

이 문서 하나로 (a) 동작 원리, (b) 설정 항목, (c) **사내 메일 발송 연동 코드 작성법**, (d) 도메인·허용목록
운영, (e) 보안 불변식을 모두 다룬다. 코드 에이전트가 이 기능을 이어받을 때 여기부터 읽으면 된다.

- 포탈 설치 전반: [portal-01-setup.md](portal-01-setup.md) (§10.2가 이 문서를 가리킨다)
- 재배포: [redeploy.md](redeploy.md) §3

---

## 1. 한눈에 보기

```
[사용자] 아이디+비밀번호 입력 → [인증요청]
            │
            ├─ 서버: 아이디 정규화·예약어/허용목록 검사 → 6자리 코드 생성
            │        → HMAC으로 DB 저장(평문 저장 안 함) → 배송 provider로 발송
            │        → 항상 202 (계정 존재 여부를 응답으로 노출하지 않음)
            ▼
[사용자] 메일에서 인증번호 확인 → 화면에 입력 → [계정 만들기]
            │
            └─ 서버: 코드 대조 + 계정 생성을 단일 SQL(CTE)로 원자 처리 → 201
```

**아이디 = 회사 메일 local-part.** `test1.user@samsung.com` → 아이디 `test1.user`.
인증번호는 항상 `<아이디>@PORTAL_EMAIL_DOMAIN`으로 발송된다.

| 항목 | 값 | 근거 |
|---|---|---|
| 인증번호 자릿수 | 6 | `PORTAL_EMAIL_CODE_LENGTH` |
| 유효시간 | **10분** (600초) | `PORTAL_EMAIL_CODE_TTL_SECONDS` |
| 재발송 쿨다운 | 60초 | `PORTAL_EMAIL_RESEND_COOLDOWN_SECONDS` |
| 코드당 오답 허용 | 5회 | `PORTAL_EMAIL_CODE_MAX_ATTEMPTS` |
| 계정 저장소 | `portal.user_accounts` (PBKDF2-SHA256 240k) | `db.py` |
| 인증번호 저장소 | `portal.email_verifications` (HMAC-SHA256 + pepper) | `db.py` |

> 두 테이블 모두 **기동 시 idempotent DDL로 자동 생성**된다. 별도 migrate 명령이 없다.

## 2. API

모두 `/api/auth/user/` 하위이며 **세션이 필요 없다**(로그인 전 화면에서 호출).

| 메서드 | 경로 | 성공 | 설명 |
|---|---|---|---|
| GET | `/api/auth/user/config` | 200 | 로그인 화면이 쓰는 공개 설정. **자격증명은 절대 미포함** |
| POST | `/api/auth/user/request-code` | **202** | `{username, purpose}` → 인증번호 발송. `purpose`=`register`\|`reset` |
| POST | `/api/auth/user/register` | **201** | `{username, password, code}` |
| POST | `/api/auth/user/reset-password` | 200 | `{username, new_password, code}` |
| POST | `/api/auth/user/login` | 200 | `{username, password}` → `role: "user"` 세션 |

`GET /user/config` 응답 필드(8개):

```json
{"available": true, "email_domain": "samsung.com", "code_length": 6, "code_ttl_seconds": 600,
 "resend_cooldown_seconds": 60, "min_password_len": 8, "signup_enabled": true,
 "email_delivery": "company"}
```

> `email_delivery`는 **실제 사용 가능할 때만** 설정값(`log`/`company`)을 그대로 보고하고, 그렇지
> 않으면(`available:false`) 항상 `"none"`이다. 프론트는 `available`로 [인증요청] 버튼을 게이팅한다.

`POST /user/request-code`는 목적과 계정 존재 여부에 따라 **메일 내용만** 3가지로 갈린다(응답은 동일):

| 경우 | 발송 | 전역 발송 카운터 |
|---|---|---|
| `register` + 계정 없음 / `reset` + 계정 있음 | **인증번호** | +1 |
| `register` + 계정 **있음** | "이미 계정이 있습니다" 안내 (코드 아님) | +1 |
| `reset` + 계정 **없음** | 발송 없음 | +0 (§8.8) |

에러 코드(형식: `snake_case (한국어 힌트)`):

| 코드 | 상태 | 발생 지점 |
|---|---|---|
| `invalid_purpose` | 422 | `purpose`가 `register`/`reset`이 아님 |
| `invalid_local_part` | 422 | 아이디 형식 위반 |
| `invalid_email_domain` | 422 | 설정 도메인과 다른 주소를 입력 |
| `username_reserved` | 422 | 예약 아이디 / `admin_` 접두어 (§7.3) |
| `password_too_short` | 422 | 비밀번호 8자 미만 (register/reset) |
| `signup_not_allowed` | 403 | 허용목록 밖 (register만) |
| `signup_disabled` | 403 | `PORTAL_USER_SIGNUP_ENABLED=false` |
| `email_not_configured` | 503 | 배송 수단 미설정/미사용가능 |
| `portal_db_not_configured` | 503 | `PORTAL_DB_URL` 미설정 |
| `resend_too_soon` | 429 | 쿨다운·발송 상한·실패 예산 (`Retry-After` 헤더 동반) |
| `send_quota_exceeded` | 429 | 전역 시간당 상한 |
| `invalid_code` | 400 | 인증번호 오류/만료/미발급 (셋을 구분하지 않음 — 의도적) |
| `code_attempts_exhausted` | 429 | 코드당 오답 한도 초과 |
| `username_exists` | 409 | 동시 가입 경합 (register) |
| `user_not_found` | 404 | 코드는 맞으나 계정이 없거나 비활성 (reset) |
| `invalid_credentials` | 401 | 로그인 실패 (사유 구분 없음 — 의도적) |

> **`request-code`의 응답은 계정 존재 여부에 전혀 의존하지 않는다.** 요청이 검증을 통과하면
> 존재/부재 어느 쪽이든 **동일한 202와 동일한 본문**(`{"requested":true,"expires_in":600,"resend_after":60}`)
> 이 나간다. 존재 여부는 응답이 아니라 **메일 내용**으로만 구분된다 — 이미 가입된 주소에는 코드 대신
> "이미 계정이 있습니다" 안내가 가고, 없는 계정의 재설정 요청은 아무 메일도 보내지 않는다.
> 이 성질을 깨는 변경(존재하면 404/409를 준다든지)은 계정 열거 취약점이므로 하지 말 것.
>
> 202가 아닌 응답은 **계정 상태와 무관한 사유**뿐이다: `422`(purpose 오류·아이디 형식·예약어·도메인
> 불일치), `403`(허용목록 밖·가입 비활성), `503`(배송 수단 미설정·포탈 DB 미설정), `429`(쿨다운·발송
> 상한·전역 상한). **레이트리밋 상태는 존재/부재 양쪽 모두에 동일하게 기록되므로 429 역시 오라클이
> 되지 않는다** (§8.3).

## 3. 설정 항목 (`PORTAL_*`)

| 변수 | 기본값 | 설명 |
|---|---|---|
| `PORTAL_EMAIL_DOMAIN` | `""` | **아이디 뒤에 붙는 회사 메일 도메인.** 빈 값이면 기능 전체 비활성. 형식이 틀리면 **기동 거부** |
| `PORTAL_EMAIL_DELIVERY` | `none` | 배송 수단: `none` \| `log` \| `company` (§4). 알 수 없는 값은 **`none`으로 폴백** |
| `PORTAL_EMAIL_SEND_TIMEOUT_SECONDS` | `10.0` | 사내 발송 구현이 쓸 타임아웃 상한 |
| `PORTAL_USER_SIGNUP_ENABLED` | `true` | 가입 킬스위치. false면 가입 인증요청이 403 |
| `PORTAL_SIGNUP_ALLOWLIST` | `()` (제한 없음) | 가입 허용 아이디(콤마 구분). **공용 도메인 사용 중 필수** (§7) |
| `PORTAL_EMAIL_CODE_TTL_SECONDS` | `600` | 인증번호 유효시간(10분) |
| `PORTAL_EMAIL_CODE_LENGTH` | `6` | 인증번호 자릿수 |
| `PORTAL_EMAIL_CODE_MAX_ATTEMPTS` | `5` | 코드 1건당 오답 허용 횟수 |
| `PORTAL_EMAIL_RESEND_COOLDOWN_SECONDS` | `60` | 재발송 최소 간격 |
| `PORTAL_EMAIL_SEND_WINDOW_SECONDS` | `3600` | 발송 제한 시간창 |
| `PORTAL_EMAIL_MAX_SENDS_PER_WINDOW` | `5` | 아이디·목적당 시간창 내 **요청** 상한. 메일을 보내지 않는 요청(미존재 아이디의 reset)도 포함해서 센다(`sends` 컬럼) — 그래야 응답이 존재 여부를 노출하지 않는다 |
| `PORTAL_EMAIL_MAX_FAILURES_PER_WINDOW` | `20` | **재발급으로 리셋되지 않는** 누적 오답 예산 |
| `PORTAL_EMAIL_FAILURE_WINDOW_SECONDS` | `3600` | 위 예산의 시간창 |
| `PORTAL_EMAIL_GLOBAL_MAX_SENDS_PER_HOUR` | `200` | **전역 실제 발송** 상한(릴레이 남용 킬스위치). `mailed` 컬럼을 세며(§8.8), 시간창은 이름과 달리 고정 1시간이 아니라 `PORTAL_EMAIL_SEND_WINDOW_SECONDS`를 따른다 |
| `PORTAL_EMAIL_CODE_PEPPER` | `None` | 인증번호 HMAC 키. 미설정 시 `PORTAL_SESSION_SECRET`에서 파생 |

이 기능이 **의존하는 다른 설정**(별도 섹션에 정의되어 있지만 없으면 동작하지 않는다):

| 변수 | 이 기능에서의 역할 |
|---|---|
| `PORTAL_DB_URL` | **필수.** 계정/인증번호 저장소. 없으면 사용자 인증 라우트가 전부 `503 portal_db_not_configured` |
| `PORTAL_ALLOW_INSECURE_DEFAULTS` | `PORTAL_EMAIL_DELIVERY=log`의 **필수 동반 플래그**. 없으면 기동 거부 |
| `PORTAL_SESSION_SECRET` | 세션 서명 + (pepper 미설정 시) 인증번호 HMAC 키의 파생 원본 |
| `PORTAL_BACKUP_REQUESTER` / `PORTAL_DMS_ACTOR` | 이 값들은 **런타임 예약 아이디**가 된다 (§7.3) |

`PORTAL_EMAIL_DELIVERY`·`PORTAL_EMAIL_DOMAIN`은 **비밀값이 아니므로 Deployment env**로 두고,
사내 연동에 자격증명이 생기면 그것만 **Secret**에 넣는다(§6).

## 4. 배송 provider (`PORTAL_EMAIL_DELIVERY`)

인증 로직 전체(코드 생성·HMAC 저장·레이트리밋·열거 방지)는 provider와 무관하다.
**바뀌는 것은 "메일이 어떻게 나가는가" 하나뿐**이며, 그 지점이
`src/portal/backend/mailer.py`다.

| 값 | 동작 | 용도 |
|---|---|---|
| `none` | 인증요청이 **503 `email_not_configured`** (fail-closed) | 기본값. 기능 미사용 |
| `log` | 인증번호를 **서버 로그로만** 출력 | **개발 전용.** 메일 연동 전 화면·플로우 테스트 |
| `company` | `deliver_company_mail()` 호출 | **사내 메일 발송 (§5에서 구현)** |

### `log` 모드 (현재 테스트베드 설정)

인증번호가 로그에 평문으로 남으므로 **`PORTAL_ALLOW_INSECURE_DEFAULTS`와 이중 게이트**다.
`log`만 켜고 이 값을 안 주면 `create_app()`이 **기동을 거부**한다(테스트베드 설정이 그대로 운영에
복사되는 사고를 배포 단계에서 드러내기 위함). 인증번호는 **HTTP 응답에는 절대 포함되지 않는다.**

```bash
kubectl -n dms-portal logs deploy/dms-portal | grep "DEV CODE ECHO" | tail -1
# SECURITY: DEV CODE ECHO (개발 전용) to=someone@gmail.com subject=... code=123456
```

> 파드가 재시작(롤아웃·env 변경)되면 이전 로그는 사라진다. 화면에서 `[재발송]`으로 새로 받으면 된다.

## 5. ★ 사내 메일 발송 연동 — 구현 가이드

**구현할 것은 함수 하나뿐이다:**

```
src/portal/backend/mailer.py  →  async def deliver_company_mail(settings, email)
```

다른 파일은 건드릴 필요가 없다. 라우터·DB·레이트리밋·프론트는 provider를 모른다.

### 5.1 함수 계약

```python
async def deliver_company_mail(settings: "Settings", email: OutboundEmail) -> None:
```

| 항목 | 규칙 |
|---|---|
| **성공** | 그냥 반환 |
| **실패** | **예외를 던진다.** 호출자가 방금 발급한 인증번호를 무효화해서 "메일은 안 갔는데 코드는 살아있는" 상태를 막는다 |
| **논블로킹** | 포탈은 단일 이벤트 루프에서 백업/스캔/싱크 오케스트레이터와 대시보드 샘플러를 함께 돌린다. 동기 라이브러리(`smtplib`, `requests`)를 쓰면 **반드시** `await asyncio.to_thread(...)`로 감싸고 **타임아웃을 지정**한다 (`to_thread`는 취소가 불가능해 타임아웃이 유일한 상한) |
| **로그 금지** | 자격증명·수신자 전체 주소·인증번호를 로그에 남기지 않는다. Pod 로그는 광범위하게 열람된다 |

### 5.2 `OutboundEmail` (입력)

```python
@dataclass(frozen=True)
class OutboundEmail:
    to_addr: str          # 수신자 = <아이디>@PORTAL_EMAIL_DOMAIN
    subject: str          # 렌더된 한국어 제목
    body: str             # 렌더된 한국어 본문 (평문)
    kind: str             # "verification_code" | "already_registered"
    code: str | None = None   # 인증번호. 사내 API가 본문 대신 템플릿 변수를 받을 때 사용
```

> `code`는 **`kind == "verification_code"`일 때만 채워진다.** "이미 가입됨" 안내 메일은 `code=None`이니,
> 템플릿 변수 방식으로 구현한다면 `kind`로 분기하라.

평문·무첨부로 고정한 이유는 **모든 배송 채널(SMTP/REST/큐)이 받아들일 수 있는 최소 형태**이기
때문이다. 본문 문구를 바꾸려면 `build_code_message()` / `build_already_registered_message()`를
고친다(provider와 무관하게 공통 적용된다).

### 5.3 구현 예시 A — 사내 REST API

```python
async def deliver_company_mail(settings, email):
    import httpx
    async with httpx.AsyncClient(timeout=settings.email_send_timeout_seconds) as c:
        r = await c.post(
            settings.company_mail_api_url,
            headers={"Authorization": f"Bearer {settings.company_mail_api_token}"},
            json={"to": email.to_addr, "subject": email.subject, "body": email.body},
        )
        r.raise_for_status()      # 실패 시 예외 → 코드 자동 무효화
```

`httpx`는 이미 포탈 의존성이다(`pyproject.toml`의 `portal` extra). 호출량이 많아지면 매 요청
클라이언트를 만들지 말고 `app.state`에 하나 만들어 재사용한다(`dms_client.py`가 같은 패턴).

### 5.4 구현 예시 B — 사내 SMTP 릴레이

```python
async def deliver_company_mail(settings, email):
    import smtplib
    from email.message import EmailMessage

    def _send():                                   # BLOCKING
        msg = EmailMessage()
        msg["From"] = settings.company_mail_from
        msg["To"] = email.to_addr
        msg["Subject"] = email.subject
        msg["Auto-Submitted"] = "auto-generated"   # 자동응답 루프 방지
        msg.set_content(email.body)
        with smtplib.SMTP(settings.company_smtp_host, settings.company_smtp_port,
                          timeout=settings.email_send_timeout_seconds) as s:
            s.starttls()                           # 릴레이 정책에 따라
            s.send_message(msg, to_addrs=[email.to_addr])

    await asyncio.to_thread(_send)                 # ← 반드시 to_thread
```

> `smtplib.set_debuglevel()`은 **절대 켜지 말 것** — AUTH base64(자격증명)가 Pod 로그에 남는다.
> Pod에서 릴레이 포트로 **egress가 열려 있어야** 한다(HTTP 프록시는 SMTP에 쓸 수 없다):
> ```bash
> kubectl -n dms-portal exec deploy/dms-portal -- \
>   python -c "import socket;s=socket.create_connection(('<릴레이>',25),8);print('ok');s.close()"
> ```

### 5.5 새 설정이 필요할 때

`src/portal/backend/config.py`의 `Settings`에 필드를 추가하고 `from_env()`에서 `PORTAL_*`로 읽는다
(frozen dataclass — 기존 패턴 그대로). 비밀값은 §6.

### 5.6 전환 절차

```bash
# 1) deliver_company_mail 구현 후 이미지 빌드·push (redeploy.md §1)
# 2) provider 전환 + 개발용 로그 경로 제거
kubectl -n dms-portal set env deploy/dms-portal \
  PORTAL_EMAIL_DELIVERY=company \
  PORTAL_EMAIL_DOMAIN=<회사도메인> \
  PORTAL_ALLOW_INSECURE_DEFAULTS-      # log 모드용 승인 플래그 제거
# 3) 확인
kubectl -n dms-portal exec deploy/dms-portal -- \
  python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8090/healthz').read().decode())"
# → "email_configured":true, "email_domain":"<회사도메인>"
```

**검증**: 본인 아이디로 계정 만들기 → 실제 메일 수신 확인 → 인증번호 입력 → 로그인.
실패하면 `kubectl -n dms-portal logs deploy/dms-portal | grep -i "delivery failed"`.

## 6. 자격증명(Secret) 취급

사내 연동에 토큰/비밀번호가 필요하면:

1. `src/portal/deploy/kubernetes/portal.yaml`의 Secret에는 **placeholder만** 커밋한다
   (`REPLACE_WITH_*`). **작동하는 자격증명을 저장소에 넣지 않는다.**
2. 실값은 라이브 Secret에 patch로만 주입한다:
   ```bash
   kubectl -n dms-portal patch secret portal-secrets --type merge \
     -p '{"stringData":{"PORTAL_COMPANY_MAIL_TOKEN":"<실값>"}}'
   kubectl -n dms-portal rollout restart deploy/dms-portal
   ```
3. **`kubectl apply`는 Secret을 placeholder로 되덮는다.** apply 후에는
   [portal-01-setup.md](portal-01-setup.md) §7.2로 **전체 키를 재주입**한다. 새 자격증명 키를
   추가했다면 **그 §7.2의 patch 예시에도 키를 추가**해 둔다(안 그러면 다음 apply 때 조용히 누락된다).
4. **`/healthz`의 `email_configured`로는 Secret 재주입 누락을 알 수 없다.** 이 값은
   `PORTAL_EMAIL_DOMAIN`·`PORTAL_EMAIL_DELIVERY`(Deployment env)만으로 계산되므로, 자격증명이
   placeholder여도 `true`로 나온다. 자격증명 확인은 **실제 발송을 시도**해야 한다:
   ```bash
   # placeholder 잔존 검사
   kubectl -n dms-portal get secret portal-secrets -o json \
     | python3 -c "import sys,json,base64; d=json.load(sys.stdin)['data']; \
       print([k for k,v in d.items() if base64.b64decode(v).decode().startswith('REPLACE_WITH')])"
   # 실제 발송 확인: 본인 아이디로 인증요청 후 메일 수신 + 발송 실패 로그 확인
   kubectl -n dms-portal logs deploy/dms-portal | grep -i "delivery failed"
   ```

## 7. 도메인 · 허용목록 운영

### 7.1 회사 도메인 설정

`PORTAL_EMAIL_DOMAIN`은 **DMS 구축 시 회사 도메인으로 설정**한다. 이 값은 **서버 설정에서만** 오고
사용자 입력에서 절대 파생하지 않는다 — 파생을 허용하면 인증번호를 임의 외부 도메인으로 배달시킬 수
있어 "회사 메일 소유 증명"이라는 전제가 무너진다. 형식이 틀리면 **기동을 거부**한다.

사용자가 전체 주소(`test1.user@samsung.com`)를 붙여넣어도 **설정 도메인과 같을 때만** local-part를
잘라 쓰고, 다르면 `422 invalid_email_domain`으로 거부한다.

### 7.2 허용목록(`PORTAL_SIGNUP_ALLOWLIST`)

**가입(register)에만 적용**되고 비밀번호 재설정에는 적용되지 않는다. 재설정에도 걸면 (a) 목록 소속
여부가 응답으로 새고, (b) 나중에 목록에서 빠진 기존 사용자가 자기 비밀번호를 못 바꾸게 된다.

| 상황 | 권장 |
|---|---|
| 도메인이 **공용**(gmail.com 등) | **반드시 채운다.** 비우면 "그 메일 서비스 계정 보유자 전원"이 가입 자격을 갖고, 가입 사용자는 DMS 데이터 작업 실행 경로를 얻는다. 기동 시 경고 로그가 뜬다 |
| 도메인이 **회사 도메인** | 비워도 된다. 도메인 자체가 소속 증명이다 |

```bash
kubectl -n dms-portal set env deploy/dms-portal PORTAL_SIGNUP_ALLOWLIST=aaa.bbb,ccc.ddd
kubectl -n dms-portal set env deploy/dms-portal PORTAL_SIGNUP_ALLOWLIST-   # 해제
```

> `set env`는 라이브 Deployment만 바꾸므로 `kubectl apply` 시 매니페스트 값으로 되돌아간다.
> 계속 쓸 값이면 `portal.yaml`에도 반영한다.

### 7.3 예약 아이디 (변경 금지)

`root`·`admin`·`administrator`·`operator`·`daemon`·`postgres`·`noreply`·`dms`·`portal` 등은 가입이
거부된다(`422 username_reserved`). 목록: `src/portal/backend/email_codes.py: RESERVED_USERNAMES`.
정적 목록 외에 **`PORTAL_BACKUP_REQUESTER`(기본 `root`)와 `PORTAL_DMS_ACTOR`(기본 `operator`)의 현재
값도 런타임에 예약**된다 — 이 둘은 DMS 호출 시 특권 신원으로 쓰이므로 사용자가 사칭하면 안 된다.

**특히 `root`가 중요하다.** DMS는 `dm_allow_root_requester=true` + `dm_privileged_requesters={'root'}`로
동작하고, 포탈은 FS↔FS sync에서 사용자 신원을 DMS `requester_id`로 그대로 넘긴다. 즉 `root` 가입을
허용하면 **uid/gid 0으로 데이터 작업이 실행되고 `dm_min_uid`(1000) 하한이 무력화**된다.
`admin_` 접두어도 운영자 네임스페이스라 거부된다.

> 관련 운영 과제(포탈 범위 밖): DMS의 `DMS_DM_PRIVILEGED_OPERATORS`가 비어 있으면 "모든 mTLS actor
> 허용"이다. 실제 운영자 목록으로 좁히는 것을 권장한다.

## 8. 보안 불변식 (변경 시 반드시 유지)

이 기능을 수정하는 코드 에이전트는 아래를 깨지 않는지 확인해야 한다.

> **⚠️ 테스트만 믿지 말 것.** 대부분은 `tests/test_portal_user_accounts.py`에 회귀 테스트로 고정되어
> 있지만 **전부는 아니다.** 특히 **불변식 6(단일 CTE 원자성)은 어떤 단위 테스트도 잡지 못한다** —
> 테스트는 in-memory FakeDb로 돌기 때문에 `db.py`의 실제 SQL이 한 번도 실행되지 않는다. CTE를 두
> 문장으로 쪼개도 pytest는 전부 통과한다. SQL을 고쳤다면 **실제 PostgreSQL에 배포해 e2e로 확인**해야
> 한다(§5.6의 검증 절차). 불변식 12는 `tests/test_portal_user_sync_scan.py`에도 걸쳐 있다.

1. **인증번호 평문 저장 금지** — `code_digest()`(HMAC-SHA256 + pepper)만 저장. pepper는 DB에 없다.
   PBKDF2를 쓰지 않는 이유: 6자리는 후보가 10⁶뿐이라 반복 해시가 DB 유출을 막지 못하고, 오히려
   미인증 엔드포인트에서 CPU 증폭기가 된다. HMAC은 결정적이라 **대조를 SQL WHERE에 넣을 수 있어**
   "조회→검증→소비" TOCTOU가 구조적으로 사라진다.
2. **인증번호를 HTTP 응답에 넣지 않는다.** `log` 모드도 로그 전용이다.
3. **`request-code`는 항상 동일한 202.** 존재/부재 두 경로 모두 `issue_verification`을 거쳐
   레이트리밋 상태를 남긴다(한쪽만 남기면 202 vs 429로 존재가 노출된다).
4. **비밀번호는 인증요청이 아니라 확인 단계에서만 받는다.** `RequestCodeRequest`는
   `extra="forbid"`라 `password`가 오면 422. 요청 단계에서 받으면 "코드를 받은 사람"과 "비밀번호를
   정한 사람"이 갈라져 계정 탈취 경로가 생긴다.
5. **아이디 정규식은 `\Z`로 앵커한다.** Python의 `$`는 **후행 개행 직전에도 매치**되므로
   `^...$`는 `"root\n"`을 통과시키고, 그 문자열은 `RESERVED_USERNAMES`(`"root"`) 검사를 빠져나가면서
   메일 헤더에 개행을 실어 나른다. `@` 분리 후 재-strip도 함께 유지한다.
6. **코드 소비와 계정 생성/변경은 단일 SQL(CTE).** 커넥션 풀이 autocommit이라 두 문장은 원자적이지
   않다.
7. **재발송은 UPSERT로 이전 코드를 덮는다.** PK가 `(username, purpose)`이므로 **아이디·목적당 살아있는
   코드는 항상 1개**다(같은 사람이 register용·reset용 코드를 동시에 가질 수는 있다 — 서로 다른 행이고,
   HMAC에 purpose가 바인딩되어 교차 사용도 불가). 재발급이 `attempts`를 리셋하므로, **재발급으로
   리셋되지 않는** `failures` 예산을 별도로 둔다. 또한 발송 실패 시 `discard_verification`은 행을
   **지우지 않고 `expires_at`만 앞당긴다** — 행을 지우면 레이트리밋 상태가 초기화되어, 발송 실패를
   유도하는 것만으로 제한을 우회할 수 있다.
8. **전역 상한은 `mailed`(실제 발송)를 센다.** `sends`(요청 수)를 세면 메일을 보내지 않는 요청으로도
   시간당 예산을 소진시켜 가입/재설정을 마비시킬 수 있다.
9. **PBKDF2는 코드 검증 이후에.** 확인 라우트는 `verification_matches()`로 먼저 걸러낸 뒤 해싱한다
   (미인증 CPU 증폭 방지). 원자성은 `consume_*`가 다시 검사하므로 훼손되지 않는다.
10. **사용자 로그인은 `user_accounts`만 조회하고 `ROLE_USER`를 하드코딩한다.** 이메일 인증으로
    operator를 만들 수 없어야 한다(계정 저장소가 분리된 이유).
11. **로그인 성공 시 `request.session.clear()` 후 세션 대입** (세션 고정 방지).
12. **`_actor`(소유권 키)와 `_dms_requester`(실행 신원)를 섞지 않는다** —
    `routers/user_sync.py`. 섞으면 `posix_username` 설정 시 사용자의 기존 잡이 고아가 된다.

### 알려진 트레이드오프

대상 아이디를 아는 제3자가 틀린 인증번호를 반복 입력해 **해당 사용자의 비밀번호 재설정을 최대 1시간
지연**시킬 수 있다(`email_max_failures_per_window`). 예산을 없애면 무차별 대입이 열리므로 감수한
설계이며, **로그인 자체는 영향받지 않는다.** 즉시 해제하려면 운영자가 해당 행을 지운다:

```sql
-- 스키마명은 PORTAL_DB_SCHEMA (기본 'portal')
DELETE FROM portal.email_verifications WHERE username = '<아이디>' AND purpose = 'reset';
```

## 9. 트러블슈팅

| 증상 | 원인 · 조치 |
|---|---|
| 인증요청이 `503 email_not_configured` | `PORTAL_EMAIL_DELIVERY=none`이거나 `PORTAL_EMAIL_DOMAIN`이 비었다. `log`는 `PORTAL_ALLOW_INSECURE_DEFAULTS`도 필요 |
| 인증요청이 `503 portal_db_not_configured` | `PORTAL_DB_URL` 미설정. 계정/인증번호 저장소가 없다([portal-01-setup.md](portal-01-setup.md) §4) |
| `403 signup_not_allowed` | 허용목록에 없는 아이디 (§7.2) |
| `422 username_reserved` | 예약 아이디 (§7.3). **정상 동작이다** |
| `422 invalid_email_domain` | `PORTAL_EMAIL_DOMAIN`과 다른 도메인의 주소를 입력 |
| `403 signup_disabled` | `PORTAL_USER_SIGNUP_ENABLED=false` |
| `429 resend_too_soon` | 60초 쿨다운 / **아이디·목적당** 시간창 요청 상한(5) / 누적 실패 예산(20) 중 하나. 셋을 구분하지 않는 것은 의도적이다 |
| `429 send_quota_exceeded` | **전역** 실제 발송 상한(기본 200/시간창) 도달. 로그에 `email send quota exceeded` 경고가 함께 남는다 |
| Pod가 `CrashLoopBackOff` | 로그에 `PORTAL_EMAIL_DELIVERY=log ...`면 `PORTAL_ALLOW_INSECURE_DEFAULTS=1` 누락. `PORTAL_EMAIL_DOMAIN is not a valid domain`이면 도메인 오타 |
| 인증번호 메일이 안 옴 | `logs deploy/dms-portal \| grep -i "delivery failed"`. 발송 실패 시 코드는 자동 무효화된다. 단 **레이트리밋 행은 남으므로**(§8-7) 즉시 재발송하면 `429`가 날 수 있다 — 쿨다운(60초) 후 재시도 |
| `log` 모드인데 로그에 코드가 없음 | 파드가 재시작되어 이전 로그가 사라졌다. 화면에서 `[재발송]` |

## 10. 관련 파일

| 파일 | 역할 |
|---|---|
| `src/portal/backend/mailer.py` | **배송 provider seam** (`deliver_company_mail`이 연동 지점) |
| `src/portal/backend/email_codes.py` | 코드 생성·HMAC·아이디 검증·예약어 |
| `src/portal/backend/auth.py` | **사용자 라우트 5개**(`/user/*`) + 레이트리밋/열거 방지 흐름. 같은 파일에 운영자 라우트 6개(`/login`·`/logout`·`/me`·`/account-token-required`·`/register`·`/reset-password`)가 함께 있으니 **혼동하지 말 것** — 운영자 것은 admin token 게이트다 |
| `src/portal/backend/db.py` | `user_accounts` / `email_verifications` 스키마 + 원자 SQL |
| `src/portal/backend/config.py` | `PORTAL_*` 설정 |
| `src/portal/backend/app.py` | 부팅 가드(도메인 형식·log 이중게이트), `/healthz` |
| `src/portal/frontend/src/pages/Login.tsx` | 사용자 탭 3서브탭 + 2단계(폼→코드) UI |
| `tests/test_portal_user_accounts.py` | §8 불변식 회귀 테스트 (**불변식 6은 미커버** — FakeDb라 실제 SQL 미실행) |
| `tests/test_portal_user_sync_scan.py` | 불변식 12(소유권 키 vs 실행 신원)의 라우트 레벨 커버리지 |
