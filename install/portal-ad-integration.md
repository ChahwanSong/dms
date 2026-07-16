# 회사 AD 로그인 실제 연동 가이드 (Portal)

사용자(end-user) 로그인은 현재 **임시 더미 스텁**이다. 이 문서는 실제 AD/LDAP/OIDC로 교체할 때
**수정할 파일·내용**과 **수정 후 재배포/재실행 방법**을 정리한다. (재배포 일반은 [redeploy.md](redeploy.md),
포탈 설치·Secret은 [portal-01-setup.md](portal-01-setup.md) §5~§7·§11.2.)

## 0. 현재 상태 (더미)

- 사용자 로그인: `POST /api/auth/login/ad` → `src/portal/backend/auth.py`의 **`authenticate_ad()`** 스텁.
  **자격증명 검증 없이** 입력 아이디(비우면 `ad-user`)로 로그인. 로그인 화면 "사용자" 탭 = "임시 더미 로그인" 버튼.
- `PORTAL_ALLOW_DUMMY_AD`(기본 `true`)로 더미 경로 on/off. `false`면 더미 로그인은 `401`(fail-closed).
- ⚠️ **프로덕션 전 반드시 실제 AD로 교체**(더미는 아무나 임의 사용자로 로그인 가능).

## 1. 수정할 파일 요약

| 순위 | 파일 | 위치 | 내용 |
|---|---|---|---|
| **핵심** | `src/portal/backend/auth.py` | `authenticate_ad(settings, username, password)` | 더미 블록을 실제 AD 인증으로 교체 (§2) |
| 설정 | `src/portal/backend/config.py` | `Settings` 필드 + `from_env` | AD 서버/DN 등 `PORTAL_*` 추가 (§3) |
| 의존성 | `pyproject.toml` | `[project.optional-dependencies]` | LDAP면 `ldap3` (이미 `ldap` extra 존재) (§4) |
| 프론트 | `src/portal/frontend/src/pages/Login.tsx` · `src/portal/frontend/src/api.ts` | 사용자 탭 · `loginAd` | 방식에 따라 password 필드 또는 SSO 리다이렉트 (§5) |

> 가장 단순한 경로(LDAP simple bind)는 **`auth.py` + `config.py` 2곳**이면 된다.

## 2. 백엔드 핵심 — `authenticate_ad()` 교체

현재 스텁(요약):

```python
def authenticate_ad(settings, username, password) -> dict | None:
    # DUMMY: allow_dummy_ad일 때 아무 아이디로 성공.
    if not settings.allow_dummy_ad:
        return None
    return {"username": (username or "").strip() or "ad-user", "dummy": True}
```

**반환 규약(이대로 유지)**: 성공 → `{"username": <AD 신원>, "display_name"?: str, "dummy": False}`,
실패 → `None`(라우트가 `401 ad_auth_failed` 처리). 예외는 잡아 `None`으로.

### 2-A. LDAP simple bind (예시)

```python
from ldap3 import Server, Connection, ALL
from ldap3.core.exceptions import LDAPException

def authenticate_ad(settings, username, password):
    if not username or not password:
        return None
    if not settings.ad_server:            # 미설정 방어(더미 fallback 원치 않으면 None)
        return None
    try:
        server = Server(settings.ad_server, use_ssl=settings.ad_use_ssl, get_info=ALL, connect_timeout=5)
        # UPN 예: "{username}@corp.example.com" / DN 예: "uid={username},ou=users,dc=corp,dc=example,dc=com"
        user_dn = settings.ad_user_dn_template.format(username=username)
        conn = Connection(server, user=user_dn, password=password, receive_timeout=5)
        if not conn.bind():               # 자격증명 오류 → 로그인 실패
            return None
        conn.unbind()
    except LDAPException:
        return None
    return {"username": username, "dummy": False}
```

- **주의**: bind 실패/예외는 반드시 `None`. TLS 사용(`use_ssl`/LDAPS 또는 StartTLS), 타임아웃 설정.
  (선택) bind 후 그룹·표시명 조회로 `display_name`·권한 파생.

### 2-B. OIDC/SAML (SSO, 리다이렉트 방식)

자격증명을 이 함수에서 받지 않고 **IdP 리다이렉트**로 처리하는 구조가 맞다. `authenticate_ad` 대신:

- `auth.py`에 **콜백 라우트 추가**: `GET /api/auth/login/ad/start`(IdP 로그인 URL로 302) +
  `GET /api/auth/ad/callback`(code/assertion 검증 → `session_user(<AD 아이디>, ROLE_USER, method="ad")` 설정).
- `Login.tsx` 버튼을 `window.location.href = "/api/auth/login/ad/start"`로 바꾼다.
- 검증 라이브러리(예: `authlib`)를 `pyproject.toml` portal extra에 추가.

## 3. 설정 추가 — `config.py`

`@dataclass(frozen=True) Settings`에 필드를 추가하고 `from_env`에서 `PORTAL_*`를 읽는다(기존 패턴 그대로):

```python
# Settings(...) 필드
ad_server: str | None = None                 # 예: "ldaps://ad.corp.example.com"
ad_use_ssl: bool = True
ad_user_dn_template: str = "{username}"       # UPN 또는 DN 템플릿

# from_env(...) 안
ad_server=env.get("PORTAL_AD_SERVER") or None,
ad_use_ssl=_env_bool(env.get("PORTAL_AD_USE_SSL"), True),
ad_user_dn_template=env.get("PORTAL_AD_USER_DN_TEMPLATE", defaults.ad_user_dn_template),
```

연동 완료 후 **`PORTAL_ALLOW_DUMMY_AD=false`**로 더미를 끈다. (AD bind용 서비스 계정 비밀번호 등 비밀값은
env가 아니라 **Secret**으로 주입 — §6-C.)

## 4. 의존성 (LDAP인 경우)

`pyproject.toml`에 이미 `ldap = ["ldap3>=2.9"]` extra가 있다(DMS용). 포탈에서도 쓰려면 둘 중 하나:

- 포탈 설치 시 **`pip install -e ".[portal,ldap]"`** (기존 extra 재사용), 또는
- `portal` extra에 `"ldap3>=2.9"` 추가.

두 경우 모두 **이미지 재빌드가 필요**하다(§6-B).

## 5. 프론트 (인증 방식에 따라)

- **LDAP(비밀번호)**: `Login.tsx` 사용자 탭에 **비밀번호 input** 추가, `api.ts`의 `loginAd(username, password)`로
  전달. "임시 더미 로그인" 문구/힌트를 정식 문구로 교체.
- **SSO**: 버튼을 IdP 리다이렉트로(§2-B). 아이디/비번 입력 불필요.

## 6. 수정 후 재배포 · 재실행 (무엇을 바꿨느냐에 따라)

포탈은 ns `dms-portal`의 단일 Deployment `dms-portal`(컨테이너 `portal`)다.

### 6-A. 코드만 변경 (auth.py · config.py 필드 · 프론트) → 이미지 재빌드 + rollout

```bash
export REGISTRY=<registry>   # 예: pkg-01:5000
export TAG=<새 태그>         # 기존에서 +1 (vNNN) 또는 git short SHA
REGISTRY=$REGISTRY TAG=$TAG IMAGES="portal" PUSH=1 ./install/docker/build-images.sh
kubectl -n dms-portal set image deployment/dms-portal portal=$REGISTRY/dms-portal:$TAG
kubectl -n dms-portal rollout status deployment/dms-portal --timeout=120s
```

- `set image`는 **라이브 Secret을 보존**한다(apply를 쓰지 않으므로 재주입 불필요).

### 6-B. 의존성 추가 (`pyproject.toml`에 ldap3/authlib 등)

→ **반드시 이미지 재빌드**(§6-A). `set image`만으로는 새 파이썬 패키지가 들어가지 않는다.

### 6-C. env / Secret 추가 (`PORTAL_AD_*`, `PORTAL_ALLOW_DUMMY_AD=false`, AD bind 비밀번호 등)

1. `kubernetes/portal.yaml`에 새 env 키를 추가했으면 `kubectl apply -f kubernetes/portal.yaml`.
   **apply는 Secret 값을 placeholder로 덮으므로** 이후 [portal-01-setup.md](portal-01-setup.md) §7.2로
   비밀값(AD 서비스계정 비번 포함)을 **라이브 Secret에 재주입**한다.
2. env는 **기동 시 로드**되므로 값 변경 후 재기동이 필요하다:

```bash
# 비밀값은 Secret에 patch (예: AD bind 비번), 평문 설정은 ConfigMap/Deployment env
kubectl -n dms-portal patch secret portal-secrets --type merge \
  -p '{"stringData":{"PORTAL_AD_BIND_PASSWORD":"<...>","PORTAL_ALLOW_DUMMY_AD":"false"}}'
kubectl -n dms-portal rollout restart deploy/dms-portal
kubectl -n dms-portal rollout status  deploy/dms-portal --timeout=120s
```

> 코드/이미지 변경 없이 **env만** 바꿨다면 `set image` 없이 위 patch + `rollout restart`만으로 반영된다.

### 6-D. 검증

```bash
# 1) health
kubectl -n dms-portal exec deploy/dms-portal -- \
  python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8090/healthz').read().decode())"
# 2) 실제 AD 계정으로 로그인 성공 / 틀린 비번 401
curl -fsS -X POST "$BASE/api/auth/login/ad" -H 'content-type: application/json' \
  -d '{"username":"<AD계정>","password":"<비번>"}'
# 3) 더미 차단 확인(PORTAL_ALLOW_DUMMY_AD=false): 빈/임의 아이디 로그인이 401 이어야 함
```

로그인 화면 "사용자" 탭에서 실제 AD 계정으로 로그인되고, 더미 버튼이 사라졌거나 401이면 완료.

## 7. Rollback

문제가 있으면 직전 리비전으로:

```bash
kubectl -n dms-portal rollout undo deployment/dms-portal
```

## 참조

- `src/portal/backend/auth.py` — `authenticate_ad()` 함수 위 배너 주석(구현 방법)
- [redeploy.md](redeploy.md) §3 — 포탈 재배포(코드/manifest 구분)
- [portal-01-setup.md](portal-01-setup.md) §7.2(Secret 주입)·§11.2(더미 AD 주의)
