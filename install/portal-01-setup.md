# DMS Portal 설치

DMS Portal은 운영자/사용자용 웹 UI다. **DMS `/api/v1/` HTTP API만 소비하는 별도 애플리케이션**이며, DMS
backend(`src/dms/`)나 그 DB를 직접 건드리지 않는다. 따라서 이 문서는 **DMS core가 이미 배포·동작 중**인
상태(→ [dms-02-core.md](dms-02-core.md))를 전제로 하고, 그 뒤에 Portal을 설치한다. 모든 절차는 production
기준이며, 테스트베드/dev 옵션은 부연설명으로만 다룬다.

명령은 DMS repository root에서 실행한다고 가정한다.

```bash
cd <dms-repo-root>
```

## 1. 개요

- **구조** — FastAPI BFF(`src/portal/backend/`) + Vite/React SPA(`src/portal/frontend/`). 하나의 컨테이너
  이미지가 빌드된 SPA 정적 자산과 BFF API(`/api/...`)를 같은 포트(`8090`)로 서빙한다.
- **순수 DMS API 클라이언트** — 브라우저는 **BFF만** 호출하고, BFF가 DMS `/api/v1/...`를 호출한다. 브라우저는
  DMS 자격증명(client cert/token)을 절대 갖지 않는다. DMS와 통신하는 코드는 `backend/dms_client.py` 한 곳뿐이다.
- **역할 모델(로그인 방식 = role)**
  - `operator` — **ID/비밀번호** 로그인(운영자 전용, 다중 계정). 운영자 콘솔.
  - `user` — **회사 AD 계정** 로그인(현재 더미 stand-in). 사용자 인터페이스.
- **배치** — DMS와 **별도 namespace `dms-portal`**에 둔다. Portal Pod는 어떤 클러스터 변경 권한도 갖지 않으며
  kubeconfig/SSH secret을 mount하지 않는다(순수 API 클라이언트). 모든 변경은 DMS API를 통해서만 일어난다.

편집·참고 파일:

- `src/portal/deploy/Dockerfile` — multi-stage(node SPA build → python BFF runtime) 이미지
- `src/portal/deploy/kubernetes/portal.yaml` — Namespace / Secret / Deployment / Service(NodePort)
- `src/portal/deploy/kubernetes/portal-ingress.example.yaml` — (운영) ingress 참고 manifest

> 운영자 UI 화면은 자명하므로 이 가이드는 **설치·구성**만 다룬다. 개별 기능 사용법 문서는 별도로 두지 않는다.

## 2. 인증 모델 — production은 mTLS

**운영에서 DMS는 mTLS-verified header profile로 노출된다**(control-plane.yaml:
`DMS_REQUIRE_MTLS_HEADER=true` + `DMS_REQUIRE_MTLS_VERIFIED_HEADER=true`). 신뢰 ingress가 client
certificate을 검증해 upstream으로 넘기고, DMS는 **인증서 subject에서 actor를 파생**한다(`DMS_MTLS_ACTOR_PREFIX`,
기본 `mtls:`). 평문 `x-dms-actor`는 신뢰되지 않고 `DMS_DEFAULT_ACTOR`는 비어 있어야 한다.

따라서 **포탈 BFF는 DMS에 portal 전용 client certificate을 제시하는 mTLS 클라이언트**로 접속해야 한다(§6.3).
- 로그인한 운영자는 인증서가 대변하는 `mtls:<…>` actor로 DMS audit에 기록된다.
- shared bearer token(`PORTAL_DMS_TOKEN` = DMS의 `DMS_AUTH_SHARED_TOKEN`)은 mTLS 위에 **선택적으로** 겹칠 수 있다.
- 브라우저↔BFF 세션 auth(세션 쿠키)는 BFF 자체 관심사이며, BFF↔DMS mTLS와 분리된다.

> **(부연) 테스트베드/dev profile.** DMS가 `DMS_REQUIRE_MTLS_VERIFIED_HEADER=false`로 떠 있으면 client cert
> 없이 shared bearer token + `x-dms-actor`만으로 접속한다(`PORTAL_DMS_TOKEN` + `PORTAL_DMS_ACTOR`).
> `portal.yaml` 기본값이 이 프로필(요청/응답 shape 확인·읽기 편의용)이다. **운영 DMS(mTLS-only)에는 이 경로만으로는
> 접속되지 않으므로** §6.3의 client cert 경로가 필요하다.

## 3. 설치 전에 정할 값

| 항목 | 예시 | 어디에 사용 |
| --- | --- | --- |
| Portal namespace | `dms-portal` | 모든 portal manifest namespace |
| Container registry | `registry.example.internal` | portal image push/pull |
| Portal image ref | `registry.example.internal/dms-portal:2026-06-23-abcdef0` | Deployment image |
| DMS API base(BFF→DMS) | `https://dms.example.internal`(mTLS ingress) 또는 `http://dms-api.dms.svc.cluster.local`(§6.3) | `PORTAL_DMS_API_URL` |
| **Portal client cert / key**(운영 mTLS) | `client.crt` / `client.key` | Secret `portal-dms-mtls` → `/etc/portal/tls`(§6.3) |
| **DMS CA**(운영 mTLS) | `ca.crt`(DMS 서버 cert 서명 CA) | 같은 Secret, TLS 검증용 |
| DMS shared token(선택) | `DMS_AUTH_SHARED_TOKEN`과 동일 값 | `PORTAL_DMS_TOKEN` |
| DMS audit actor 기본값 | `operator` | `PORTAL_DMS_ACTOR`(요청마다 로그인 운영자로 override) |
| 세션 서명 시크릿 | `openssl rand -hex 32` 출력 | `PORTAL_SESSION_SECRET` |
| 운영자 계정 | `admin:<strong-pw>,ops2:<strong-pw>` | `PORTAL_OPERATOR_USERS` |
| 운영자 계정 관리 토큰(선택) | `openssl rand -hex 24` 출력 | `PORTAL_ADMIN_TOKEN`(§10) |
| 포탈 DB(선택) | DMS Postgres 재사용 시 `DMS_DATABASE_URL`과 동일 값 | `PORTAL_DB_URL`(§4) |
| 외부 노출 | (운영) ingress + 서버 TLS / (간단) NodePort `30090` | Service / Ingress(§8) |

## 4. (선택) 포탈 DB

포탈은 기본적으로 **상태가 없는** API 클라이언트지만, **DB 기반 운영자 로그인**(§10)과 **데이터 백업 배치**
기능을 쓰려면 포탈 전용 Postgres(`PORTAL_DB_URL`)가 필요하다.

- **미설정(`PORTAL_DB_URL` 없음)** — 스토리지 인벤토리 등 DMS-연동 기능은 그대로 동작한다. 로그인은
  `PORTAL_OPERATOR_USERS`(env 저장소)로 처리되고, 데이터 백업 route는 `503`으로 비활성된다.
- **설정** — 기동 시 `PORTAL_DB_SCHEMA`(기본 `portal`) 스키마에 자신의 테이블(operator_users /
  backup_batches / backup_requests)을 자동 생성한다. 운영자 로그인은 DB가 source of truth가 되고(최초 1회
  `PORTAL_OPERATOR_USERS`로 시드), 데이터 백업 탭이 활성화된다.

DMS Postgres를 **전용 스키마(`portal`)로 재사용**할 수 있다. 이때 `PORTAL_DB_URL` = DMS의 `DMS_DATABASE_URL`
값(§7.2에서 라이브 Secret로 주입).

## 5. 이미지 빌드·push

빌드 컨텍스트는 **repository root**다. Dockerfile이 node로 SPA를 빌드한 뒤 python runtime에 BFF와 정적
자산을 함께 담는다(단일 이미지). DMS core 이미지 3종과 무관한 별도 이미지다.

```bash
export PORTAL_IMAGE="registry.example.internal/dms-portal:$(git rev-parse --short HEAD)"
docker build -f src/portal/deploy/Dockerfile -t "$PORTAL_IMAGE" .
docker push "$PORTAL_IMAGE"
```

> **프록시 전용 네트워크에서 빌드.** 인터넷이 프록시(예: `127.0.0.1:7227`)로만 되는 환경이면 위
> `docker build` 대신 래퍼 `install/docker/build-images.sh`로 빌드한다(프록시 build-arg +
> `--network=host` 자동 — node `npm install`은 `npm_config_proxy`, python `pip`은 `https_proxy`로
> 프록시를 타고 **런타임 이미지엔 프록시가 남지 않음** = 서빙되는 BFF/노드 호출은 프록시로 새지
> 않음). 메커니즘·함정 설명은 [dms-02 §1](dms-02-core.md)에 있다.
>
> ```bash
> REGISTRY=registry.example.internal TAG="$(git rev-parse --short HEAD)" \
>   PROXY=http://127.0.0.1:7227 IMAGES=portal PUSH=1 ./install/docker/build-images.sh
> ```

이미지가 정상 기동하는지 로컬에서 빠르게 확인(SPA가 빌드돼 있으면 `/healthz`가 200):

```bash
docker run --rm -e PORTAL_ALLOW_INSECURE_DEFAULTS=1 -p 18090:8090 "$PORTAL_IMAGE" &
sleep 3 && curl -fsS http://127.0.0.1:18090/healthz && echo
kill %1
```

> `PORTAL_ALLOW_INSECURE_DEFAULTS=1`은 로컬 점검용이다. BFF는 dev 기본 세션 시크릿으로는 기동을 거부하므로,
> 이 플래그 없이 점검하려면 `-e PORTAL_SESSION_SECRET=<임의값>`을 준다(§11).

## 6. portal.yaml 편집

운영에서는 원본을 직접 수정하기보다 복사본을 만든다.

```bash
cp src/portal/deploy/kubernetes/portal.yaml /tmp/dms-portal.yaml
```

### 6.1 이미지 · Deployment env

`/tmp/dms-portal.yaml`에서 다음을 환경에 맞춘다(파일 → 리소스 → 키).

- **Deployment `dms-portal` → container `image`**: `registry.example.internal/dms-portal:CHANGE_ME`
  → 실제 push한 `$PORTAL_IMAGE`로.

  ```bash
  sed -i "s#registry.example.internal/dms-portal:CHANGE_ME#$PORTAL_IMAGE#g" /tmp/dms-portal.yaml
  ```

- **Deployment `dms-portal` → `env`**:
  - `PORTAL_DMS_API_URL` — BFF가 호출할 DMS API base. (운영 mTLS ingress면 `https://dms.example.internal`,
    in-cluster 신뢰 경계면 `http://dms-api.dms.svc.cluster.local` — §6.3.)
  - `PORTAL_SESSION_HTTPS_ONLY` — **운영(TLS 서빙)이면 `"true"`**(기본값·fail-closed). 평문 HTTP NodePort로만
    노출하는 경우에 한해 `"false"`.
  - `PORTAL_DMS_ACTOR` — DMS audit 기본 actor(BFF가 요청마다 로그인 운영자 username으로 override).

전체 env 변수는 `src/portal/backend/config.py`가 정의한다. 주요 항목:

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `PORTAL_DMS_API_URL` | (없음) | DMS API base. 미설정이면 DMS 연동 route가 `503`. |
| `PORTAL_DMS_VERIFY_TLS` | `true` | DMS가 https일 때 서버 cert 검증(운영 mTLS에서 `true` 유지, CA로 검증). |
| `PORTAL_DMS_TOKEN` | (없음) | (선택) shared bearer token. Secret으로 주입. |
| `PORTAL_DMS_ACTOR` | `operator` | DMS audit 기본 actor. |
| `PORTAL_SESSION_SECRET` | (dev 기본값) | 세션 쿠키 서명키. Secret으로 주입. dev 기본값이면 기동 거부. |
| `PORTAL_SESSION_HTTPS_ONLY` | `true` | 세션 쿠키 Secure 플래그. 평문 HTTP 노출이면 `false`. |
| `PORTAL_OPERATOR_USERS` | `admin:admin1234` | 운영자 ID/PW 저장소. Secret으로 주입. `user:pw,user2:pw2`. |
| `PORTAL_ADMIN_TOKEN` | (없음) | (선택) 운영자 계정 관리 잠금해제 토큰. `PORTAL_DMS_TOKEN`과 별개. §10. |
| `PORTAL_DB_URL` | (없음) | (선택) 포탈 DB Postgres URL. Secret으로 주입. §4. |
| `PORTAL_DB_SCHEMA` | `portal` | 포탈 테이블 스키마. |

### 6.2 Secret placeholder (`portal-secrets`)

`Secret/portal-secrets`의 값은 **전부 placeholder**다. 실제 자격증명을 git working tree/commit에 남기지
않는다(실값은 §7.2에서 라이브 Secret에 주입).

```yaml
stringData:
  PORTAL_SESSION_SECRET: "REPLACE_WITH_SESSION_SECRET"
  PORTAL_OPERATOR_USERS: "REPLACE_WITH_OPERATOR_CREDS"
  PORTAL_DMS_TOKEN: "REPLACE_WITH_DMS_TOKEN"    # 선택(bearer 레이어). 안 쓰면 비워 둔다.
  PORTAL_ADMIN_TOKEN: "REPLACE_WITH_ADMIN_TOKEN"  # 선택(§10). 안 쓰면 비워 둔다.
  PORTAL_DB_URL: "REPLACE_WITH_DB_URL"          # 선택(§4). 안 쓰면 비워 둔다.
```

### 6.3 (운영) BFF → DMS mTLS client cert

운영 DMS는 mTLS-verified header profile이므로(§2), BFF는 DMS에 **portal 전용 client certificate을 제시**해야
한다. 인증서·키·CA를 별도 Secret으로 만들어 Portal 컨테이너에 read-only로 mount한다.

```bash
kubectl -n dms-portal create secret generic portal-dms-mtls \
  --from-file=client.crt=./portal-client.crt \
  --from-file=client.key=./portal-client.key \
  --from-file=ca.crt=./dms-ca.crt
```

`/tmp/dms-portal.yaml`의 Deployment `dms-portal`에 volume/volumeMount를 추가한다(파일 → 리소스 → 키).

```yaml
# Deployment dms-portal → spec.template.spec.containers[portal]
volumeMounts:
  - name: dms-mtls
    mountPath: /etc/portal/tls
    readOnly: true
# Deployment dms-portal → spec.template.spec
volumes:
  - name: dms-mtls
    secret:
      secretName: portal-dms-mtls
```

그리고 `PORTAL_DMS_API_URL`을 mTLS를 강제하는 DMS 엔드포인트로, `PORTAL_DMS_VERIFY_TLS=true`(마운트한
`ca.crt`로 DMS 서버 cert 검증)로 둔다. DMS는 client cert subject에서 actor를 파생한다(`mtls:` prefix).

> **client cert 제시 경로(중요).** 현재 in-tree BFF DMS 클라이언트(`backend/dms_client.py`)는 bearer +
> `x-dms-actor`만 붙이고 client cert을 스스로 로드하지는 않는다. mTLS-only DMS에 맞추려면 다음 중 하나로
> 배포한다.
> - **(a) egress에서 mTLS 종단** — BFF의 DMS egress를 client cert(`/etc/portal/tls/client.{crt,key}`)을
>   제시하고 `ca.crt`를 신뢰하는 mTLS-terminating proxy/sidecar 뒤에 둔다. `PORTAL_DMS_API_URL`은 그
>   proxy(예: `http://127.0.0.1:<port>`)를 가리키고, proxy가 DMS mTLS ingress로 relay한다.
> - **(b) in-cluster 신뢰 경계** — BFF가 DMS Service(`http://dms-api.dms.svc.cluster.local`)에 직접
>   접속하고, NetworkPolicy 신뢰 경계 + DMS 측 verified-header 처리로 보호한다. 사용자별 actor 전달 계약은
>   DMS owner와 확인한다.
>
> 어느 쪽을 택할지는 `CLAUDE.md`의 Portal 작업 규칙(필요 시 backend 변경을 이슈로 제기 후 공동구현)에 따라 DMS
> owner와 합의한다. 데이터 백업 등 privileged(root) DM 잡은 BFF가 이미 actor를 `mtls:<operator>`로
> 접두(prefix)하므로(`PORTAL_BACKUP_ACTOR_PREFIX`), 위 mTLS 신원과 결합돼 DMS가 verified operator로 인식한다.

## 7. 배포

### 7.1 apply

```bash
kubectl -n dms-portal apply -f /tmp/dms-portal.yaml
kubectl -n dms-portal get deploy,svc,secret
```

이 시점의 Pod는 placeholder 시크릿을 들고 있으므로, 다음 단계에서 실값을 주입한 뒤 재기동한다.

### 7.2 Secret 실값 주입 (out-of-band)

라이브 Secret `portal-secrets`에 실값을 patch한다. 세션 시크릿은 새로 랜덤 생성한다.

```bash
SESSION_SECRET="$(openssl rand -hex 32)"
DMS_TOKEN="REPLACE_WITH_DMS_AUTH_SHARED_TOKEN"   # (선택) DMS의 DMS_AUTH_SHARED_TOKEN과 동일 값

kubectl -n dms-portal patch secret portal-secrets --type merge -p "$(cat <<JSON
{"stringData":{
  "PORTAL_SESSION_SECRET":"${SESSION_SECRET}",
  "PORTAL_OPERATOR_USERS":"admin:REPLACE_WITH_STRONG_OPERATOR_PW",
  "PORTAL_DMS_TOKEN":"${DMS_TOKEN}"
}}
JSON
)"
```

포탈 DB(§4)를 쓰면 `PORTAL_DB_URL`도 함께 주입한다(DMS DB 재사용 예):

```bash
DBURL="$(kubectl -n dms get secret dms-secrets -o jsonpath='{.data.DMS_DATABASE_URL}' | base64 -d)"
kubectl -n dms-portal patch secret portal-secrets --type merge \
  -p "{\"stringData\":{\"PORTAL_DB_URL\":\"${DBURL}\"}}"
```

> **재패치 주의.** `kubectl apply -f portal.yaml`을 다시 실행하면 이 값들이 다시 placeholder로 덮인다.
> manifest를 apply할 때마다 위 patch를 다시 수행하고 §7.3으로 재기동한다.

### 7.3 rollout

Secret 변경은 자동 재기동을 트리거하지 않으므로 명시적으로 재기동한다.

```bash
kubectl -n dms-portal rollout restart deploy/dms-portal
kubectl -n dms-portal rollout status  deploy/dms-portal --timeout=120s
```

## 8. 외부 노출

### 8.1 (운영) Ingress + 서버 TLS

운영에서는 ingress + 서버 TLS로 노출하고 사용자 인증(AD/OIDC)을 BFF에 붙인다. TLS로 서빙하면
`PORTAL_SESSION_HTTPS_ONLY=true`(기본값)로 두어 세션 쿠키를 Secure-only로 만든다. 참고 manifest는
`src/portal/deploy/kubernetes/portal-ingress.example.yaml`(host, TLS secret, `ingressClassName`을 환경에
맞춰 조정). 백엔드는 Service `dms-portal:80`.

### 8.2 (간단) NodePort

템플릿 Service는 NodePort `30090`이다(DMS API NodePort와 충돌하지 않음). 클러스터 노드에 직접 도달 가능한
네트워크에서 바로 접근한다.

```bash
kubectl -n dms-portal get svc dms-portal
# http://<node-ip>:30090
```

평문 HTTP NodePort로 노출할 때만 `PORTAL_SESSION_HTTPS_ONLY=false`가 필요하다(§11).

## 9. 설치 검증

```bash
BASE="https://portal.example.internal"   # 또는 http://<node-ip>:30090
JAR="$(mktemp)"

# 1) health (dms_configured=true 여야 함; 포탈 DB면 db_configured=true도)
curl -fsS "$BASE/healthz"; echo

# 2) 운영자 로그인 (PORTAL_OPERATOR_USERS의 계정)
curl -fsS -c "$JAR" -X POST "$BASE/api/auth/login" \
  -H 'content-type: application/json' \
  -d '{"username":"admin","password":"REPLACE_WITH_STRONG_OPERATOR_PW"}'; echo

# 3) storage mapping 목록 (DMS 연동 동작 확인)
curl -fsS -b "$JAR" "$BASE/api/operator/storage-mappings" | jq 'length'

# 4) 역할 게이트: AD(user) 세션은 operator API에서 403
JAR2="$(mktemp)"
curl -fsS -c "$JAR2" -X POST "$BASE/api/auth/login/ad" >/dev/null
curl -s -o /dev/null -w "user->operator API: %{http_code}\n" -b "$JAR2" "$BASE/api/operator/storage-mappings"
```

기대값:

- `/healthz` → `{"status":"ok","dms_configured":true}` (포탈 DB면 `"db_configured":true` 포함)
- 운영자 로그인 → `{"user":{...,"role":"operator"}}`
- 목록 → DMS에 등록된 storage mapping 개수
- AD 사용자 → operator API `403`

## 10. 운영자 계정 — 로그인 화면에서 생성·비밀번호 재설정

운영자 계정 생성/비밀번호 재설정은 **로그인 화면**(로그인 전)에서 **운영용 비밀 토큰(`PORTAL_ADMIN_TOKEN`)**을
함께 입력해 수행한다. 계정 저장소는 포탈 DB `portal.operator_users`(PBKDF2 해시)이며 `PORTAL_DB_URL`이 설정된
경우에만 동작한다(§4).

- 로그인 화면 **운영자(ID/PW)** 탭에 서브탭 **로그인 / 계정 만들기 / 비밀번호 재설정**이 뜬다(뒤 둘은
  `PORTAL_ADMIN_TOKEN`이 설정된 경우에만 표시).
- **계정 만들기** — 아이디 + 비밀번호 + **비밀 토큰**. 신규 아이디는 **`admin_` 접두어**(소문자/숫자/밑줄, 예:
  `admin_ops`)를 강제한다. 부트스트랩 `admin`(env 시드)은 grandfather로 계속 로그인 가능.
- **비밀번호 재설정** — 아이디 + 새 비밀번호 + **비밀 토큰**. 단방향 해시라 복구가 아니라 **재설정**이다(최소 8자).
- 토큰은 **BFF 서버에서만 검증**(`hmac.compare_digest`)되고 브라우저에 저장/반환되지 않는다. **`PORTAL_DMS_TOKEN`과
  별개**다(DMS API 자격증명을 계정 게이트로 재사용하지 않는다).
- 공개 엔드포인트(세션 불필요): `POST /api/auth/register`, `POST /api/auth/reset-password`,
  `GET /api/auth/account-token-required`(탭 노출 판단용).

토큰 설정(라이브 Secret out-of-band):

```bash
ADMTOK="$(openssl rand -hex 24)"
kubectl -n dms-portal patch secret portal-secrets --type merge \
  -p "{\"stringData\":{\"PORTAL_ADMIN_TOKEN\":\"${ADMTOK}\"}}"
kubectl -n dms-portal rollout restart deploy/dms-portal   # env는 기동 시 로드
echo "비밀 토큰: $ADMTOK"   # 안전 채널로만 전달·보관
```

> 이 토큰을 아는 사람은 로그인 없이 운영자 계정을 만들거나 비밀번호를 재설정할 수 있다(의도된 부트스트랩/복구
> 경로). 반드시 강한 랜덤값을 쓰고 안전 채널로만 공유한다. 토큰 미설정(빈 값)이면 두 서브탭이 숨겨지고 두
> 엔드포인트는 `503`을 반환한다. `kubectl apply`가 Secret을 placeholder로 덮으므로 apply 후 재주입한다.

## 11. 보안 주의

- **시크릿을 commit하지 않는다.** manifest의 `PORTAL_SESSION_SECRET`/`PORTAL_OPERATOR_USERS`/`PORTAL_DMS_TOKEN`/
  `PORTAL_ADMIN_TOKEN`/`PORTAL_DB_URL`은 placeholder로 두고, 실값은 라이브 Secret에 patch로만 주입한다(§7.2).
- **세션 시크릿은 강한 랜덤값**(`openssl rand -hex 32`). 알려진 서명키는 운영자 세션 위조 벡터다. BFF는 dev
  기본 세션 시크릿으로는 기동을 거부한다(`PORTAL_ALLOW_INSECURE_DEFAULTS=1`로만 우회, 로컬 dev 전용).
- **운영은 TLS로 서빙**하고 `PORTAL_SESSION_HTTPS_ONLY=true`(기본값)를 유지한다. `false`는 세션 쿠키가 평문으로
  오가 스니핑/재전송 위험이 있어 평문 HTTP 노출 시에만 쓴다.
- **운영자 비밀번호**는 강한 값으로 설정/회전한다(`admin/admin1234`는 데모용).
- Portal Pod에 mTLS client cert(§6.3)·DMS 토큰 외의 control-plane 자격증명(kubeconfig/SSH key 등)을 주지 않는다.

## 12. 자주 발생하는 문제

- **`/healthz`의 `dms_configured`가 false** — `PORTAL_DMS_API_URL`이 비어 있다. Deployment env 확인(§6.1).
- **DMS 연동 route가 `503 dms_not_configured`** — `PORTAL_DMS_API_URL` 미설정.
- **DMS 연동 route가 `502 dms_unreachable`** — Portal Pod에서 DMS API에 도달 불가. service DNS/NodePort
  도달성과 NetworkPolicy를 확인한다(`dms-portal`을 dms-api 허용 목록에 포함).
- **DMS 호출이 `401`/`403`** — 운영 mTLS profile에 client cert 없이 접속 중이거나(§6.3), (부연 profile에서)
  `PORTAL_DMS_TOKEN`이 DMS의 `DMS_AUTH_SHARED_TOKEN`과 다르다. §7.2로 재주입 후 재기동.
- **운영자 로그인 `401`** — `PORTAL_OPERATOR_USERS`에 계정이 없거나 비밀번호가 다르다. 라이브 Secret 값 확인.
- **Pod가 `CrashLoopBackOff`** — 로그에 `PORTAL_SESSION_SECRET is unset/default`면 세션 시크릿이 dev
  기본값(또는 미주입)이다. §7.2로 실값 주입 후 재기동. `kubectl -n dms-portal logs deploy/dms-portal --tail=50`.
- **apply 후 갑자기 DMS 호출 `401`/로그인 불가** — `kubectl apply`가 Secret을 placeholder로 덮었다. §7.2
  재패치 + §7.3 재기동.
- **데이터 백업 탭이 `503` / `db_configured`가 false** — `PORTAL_DB_URL`이 비어 있다(또는 placeholder). §7.2로
  실값 주입 후 재기동(§4).

## 다음 문서

- [install/README.md](README.md) — 설치 가이드 인덱스
- [dms-02-core.md](dms-02-core.md) — DMS core 배포(mTLS 인증서·ingress·shared token — 포탈이 의존하는 대상)
- [dms-06-configuration.md](dms-06-configuration.md) — DMS 환경변수 레퍼런스
- [../docs/api/README.md](../docs/api/README.md) — DMS API 개요 + 인증(포탈이 소비하는 계약)
- [../docs/operations-runbook.md](../docs/operations-runbook.md) — 운영 런북
