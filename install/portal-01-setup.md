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

## 2. 인증 모델 — 포탈은 내부 신뢰 평면을 쓴다

**운영에서 외부 DMS(`dms-api`)는 mTLS-verified header profile로 노출된다**(control-plane.yaml:
`DMS_REQUIRE_MTLS_HEADER=true` + `DMS_REQUIRE_MTLS_VERIFIED_HEADER=true`) — 신뢰 ingress가 client
certificate을 검증해 upstream으로 넘기고 DMS는 **인증서 subject에서 actor를 파생**한다(`mtls:<subject>`),
평문 `x-dms-actor`는 거부. 이 프로필은 **개별 인증서를 가진 직접 운영자·자동화**에 맞다.

**그런데 포탈 BFF는 이 평면을 쓸 수 없다.** BFF는 다중 운영자를 대신해 **per-operator 신원을
`x-dms-actor: mtls:<operator>`로 실어 나르는데**, mTLS 프로필은 actor를 (하나뿐인) BFF 인증서 subject로
덮어써 모든 운영자를 단일 actor로 뭉갠다. 그래서 **포탈은 노드 에이전트와 같은 내부 신뢰 평면
`dms-api-internal`로 접속한다** — mTLS **off** + shared token + NetworkPolicy(agent + `dms-portal` ns만,
ClusterIP). 근거·구조는 [`dms-06 §1·§8`](dms-06-configuration.md), 매니페스트
`install/kubernetes/dms-api-internal.yaml`.

- **`PORTAL_DMS_API_URL` = `http://dms-api-internal.dms.svc.cluster.local`**(portal.yaml 기본값).
- **`PORTAL_DMS_TOKEN` = DMS `DMS_AUTH_SHARED_TOKEN`**과 동일해야 인증된다(내부 API의 shared-token 게이트).
- 로그인한 운영자는 BFF가 실어 보내는 `mtls:<operator>` actor로 DMS audit에 남는다(per-operator 보존).
- 브라우저↔BFF 세션 auth(세션 쿠키)는 BFF 자체 관심사로, BFF↔DMS 경로와 분리된다.

> **보안.** 내부 API는 `x-dms-actor`를 신뢰하므로 노출을 NetworkPolicy + shared token으로 한정한다(외부
> 트래픽은 절대 닿지 않음). 외부 `dms-api`는 별도로 **cert 종단 ingress만** 닿게 해야 evidence 헤더
> 스푸핑을 막는다(dms-02 참조). 포탈은 클러스터 변경 권한이 없는 순수 API 클라이언트다.

## 3. 설치 전에 정할 값

| 항목 | 예시 | 어디에 사용 |
| --- | --- | --- |
| Portal namespace | `dms-portal` | 모든 portal manifest namespace |
| Container registry | `registry.example.internal` | portal image push/pull |
| Portal image ref | `registry.example.internal/dms-portal:2026-06-23-abcdef0` | Deployment image |
| DMS API base(BFF→DMS) | **`http://dms-api-internal.dms.svc.cluster.local`**(내부 신뢰 평면, 기본 — §2) | `PORTAL_DMS_API_URL` |
| **DMS shared token**(필수) | DMS `DMS_AUTH_SHARED_TOKEN`과 **동일 값** | `PORTAL_DMS_TOKEN` |
| Portal client cert / key(선택, 외부 mTLS 직결 시만 — §6.3) | `client.crt` / `client.key` | Secret `portal-dms-mtls` → `/etc/portal/tls` |
| DMS audit actor 기본값 | `operator` | `PORTAL_DMS_ACTOR`(요청마다 로그인 운영자로 override) |
| 세션 서명 시크릿 | `openssl rand -hex 32` 출력 | `PORTAL_SESSION_SECRET` |
| 운영자 계정 | `admin:<strong-pw>,ops2:<strong-pw>` | `PORTAL_OPERATOR_USERS` |
| 운영자 계정 관리 토큰(선택) | `openssl rand -hex 24` 출력 | `PORTAL_ADMIN_TOKEN`(§10) |
| 포탈 DB(선택) | DMS Postgres 재사용 시 `DMS_DATABASE_URL`과 동일 값 | `PORTAL_DB_URL`(§4) |
| **회사 메일 도메인**(사용자 가입) | `samsung.com` (연동 전 테스트는 `gmail.com`) | `PORTAL_EMAIL_DOMAIN`(§10.2) |
| **인증메일 배송 수단** | `none` \| `log`(개발) \| `company`(사내 연동) | `PORTAL_EMAIL_DELIVERY`(§10.2) |
| 가입 허용 아이디(공용 도메인 사용 중 **필수**) | `aaa.bbb,ccc.ddd` | `PORTAL_SIGNUP_ALLOWLIST`(§10.2) |
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
>
> **사내 CA가 필요하면** (TLS 가로채기 프록시 / 사내 HTTPS) `CA_CERT=/path/to/corp-root.crt`를 함께
> 준다 — node `npm install`과 python `pip`이 그 CA를 신뢰하고, **런타임 이미지에도 유지**돼 BFF가
> 사내 엔드포인트를 신뢰한다. 상세는 [dms-02 §1](dms-02-core.md).
>
> ```bash
> REGISTRY=registry.example.internal TAG="$(git rev-parse --short HEAD)" \
>   CA_CERT=/etc/pki/corp-root.crt IMAGES=portal PUSH=1 ./install/docker/build-images.sh
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
| `PORTAL_DMS_TOKEN` | (없음) | **필수** shared bearer token(내부 API의 shared-token 게이트, DMS `DMS_AUTH_SHARED_TOKEN`과 동일 값). Secret으로 주입. |
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

### 6.3 (선택) BFF → 외부 mTLS dms-api 직결

> **기본 배포에는 필요 없다.** 포탈은 §2대로 내부 신뢰 평면 `dms-api-internal`(shared token)을 쓴다 —
> per-operator `x-dms-actor`가 mTLS 프로필에서 뭉개지기 때문. 아래는 포탈을 **외부 mTLS `dms-api`에
> 직접** 붙이려는 특수 케이스만을 위한 것이고, 그러면 per-operator 신원이 단일 BFF cert actor로
> 합쳐진다는 트레이드오프가 있다. 게다가 현 BFF DMS 클라이언트는 client cert을 스스로 로드하지 않으므로
> (bearer + `x-dms-actor`만) 코드/사이드카 보강이 별도로 필요하다.

포탈을 외부 mTLS dms-api에 직결하려면, BFF는 DMS에 **portal 전용 client certificate을 제시**해야
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
DMS_TOKEN="REPLACE_WITH_DMS_AUTH_SHARED_TOKEN"   # 필수 — DMS의 DMS_AUTH_SHARED_TOKEN과 동일 값

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

# 4) 사용자 인증 설정 노출 (도메인/발송 경로)
curl -fsS "$BASE/api/auth/user/config"; echo

# 5) 예약 아이디는 가입 차단 (root = DMS 특권 요청자 → uid 0 실행 방지)
curl -s -o /dev/null -w "root 가입요청: %{http_code}\n" -X POST "$BASE/api/auth/user/request-code" \
  -H 'content-type: application/json' -d '{"username":"root","purpose":"register"}'

# 6) 역할 게이트: 사용자 세션은 operator API에서 403 (계정이 있을 때)
JAR2="$(mktemp)"
curl -fsS -c "$JAR2" -X POST "$BASE/api/auth/user/login" \
  -H 'content-type: application/json' -d '{"username":"<사용자아이디>","password":"<pw>"}' >/dev/null
curl -s -o /dev/null -w "user->operator API: %{http_code}\n" -b "$JAR2" "$BASE/api/operator/storage-mappings"
```

기대값:

- `/healthz` → `{"status":"ok","dms_configured":true}` (포탈 DB면 `"db_configured":true`, 메일 설정 시
  `"email_configured":true,"email_domain":"<회사도메인>"` 포함)
- 운영자 로그인 → `{"user":{...,"role":"operator"}}`
- 목록 → DMS에 등록된 storage mapping 개수
- 사용자 인증 설정 → `{"available":true,"email_domain":"...","code_ttl_seconds":600,"email_delivery":"log|company",...}`
  (배송 수단 미설정이면 `"available":false` — 인증요청은 `503`으로 fail-closed)
- **`root` 가입요청 → `422`** (예약 아이디)
- 사용자 세션 → operator API `403`

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

## 10.2 사용자 계정 — 회사메일 인증(6자리) 셀프서비스

사용자(end-user)는 **아이디/비밀번호**로 로그인하고, 계정 생성·비밀번호 재설정은 **회사 메일로 받은 6자리
인증번호**로 본인 확인한다. 운영자 토큰은 쓰지 않는다 — **메일함 통제권이 곧 인가**다. 저장소는 포탈 DB
`portal.user_accounts`(PBKDF2) + `portal.email_verifications`이며 `PORTAL_DB_URL` 필요(§4).

**흐름**: 아이디 + 비밀번호 입력 → `[인증요청]` → `<아이디>@<도메인>`으로 인증번호 메일 → 인증번호 입력 →
계정 생성 완료. 비밀번호 재설정도 동일. 인증번호 **유효시간 10분**, 재발송 쿨다운 60초.

### 설정 · 사내 메일 연동 · 허용목록

**전체 설정 항목, 사내 메일 발송 연동 코드 작성법, 도메인·허용목록 운영, 보안 불변식은
→ [portal-02-user-auth.md](portal-02-user-auth.md) 를 참고한다.**

최소 설정만 옮기면:

| 변수 | 값 | 설명 |
| --- | --- | --- |
| `PORTAL_EMAIL_DOMAIN` | `samsung.com` | 아이디 뒤에 붙는 **회사 메일 도메인**. 구축 시 설정. 빈 값이면 기능 비활성 |
| `PORTAL_EMAIL_DELIVERY` | `none` \| `log` \| `company` | 인증메일 배송 수단. 사내 연동 전에는 `log`(개발 전용) |
| `PORTAL_SIGNUP_ALLOWLIST` | `aaa.bbb,ccc.ddd` | 가입 허용 아이디. **공용 도메인(gmail.com 등) 사용 중에는 필수** |

> **사내 메일 발송은 아직 미구현이다.** `src/portal/backend/mailer.py`의
> `deliver_company_mail()` 함수 하나를 구현하고 `PORTAL_EMAIL_DELIVERY=company`로 바꾸면 된다
> (다른 파일 수정 불필요). 상세: [portal-02-user-auth.md](portal-02-user-auth.md) §5.

연동 전 화면·플로우를 테스트하려면 `PORTAL_EMAIL_DELIVERY=log` + `PORTAL_ALLOW_INSECURE_DEFAULTS=1`로
인증번호를 서버 로그에서 확인한다. 인증번호는 **HTTP 응답에 절대 포함되지 않는다.**
`PORTAL_EMAIL_DELIVERY=log`인데 `PORTAL_ALLOW_INSECURE_DEFAULTS`가 없으면 **기동을 거부**한다
(반대로 `PORTAL_ALLOW_INSECURE_DEFAULTS`만 있는 것은 정상 — 이 플래그는 dev 세션 시크릿 허용에도 쓰인다):

```bash
kubectl -n dms-portal logs deploy/dms-portal | grep "DEV CODE ECHO" | tail -1
```

## 11. 보안 주의

- **시크릿을 commit하지 않는다.** manifest의 `PORTAL_SESSION_SECRET`/`PORTAL_OPERATOR_USERS`/`PORTAL_DMS_TOKEN`/
  `PORTAL_ADMIN_TOKEN`/`PORTAL_DB_URL`은 placeholder로 두고, 실값은 라이브 Secret에 patch로만 주입한다(§7.2).
- **세션 시크릿은 강한 랜덤값**(`openssl rand -hex 32`). 알려진 서명키는 운영자 세션 위조 벡터다. BFF는 dev
  기본 세션 시크릿으로는 기동을 거부한다(`PORTAL_ALLOW_INSECURE_DEFAULTS=1`로만 우회, 로컬 dev 전용).
- **운영은 TLS로 서빙**하고 `PORTAL_SESSION_HTTPS_ONLY=true`(기본값)를 유지한다. `false`는 세션 쿠키가 평문으로
  오가 스니핑/재전송 위험이 있어 평문 HTTP 노출 시에만 쓴다.
- **운영자 비밀번호**는 강한 값으로 설정/회전한다(`admin/admin1234`는 데모용).
- Portal Pod에 mTLS client cert(§6.3)·DMS 토큰 외의 control-plane 자격증명(kubeconfig/SSH key 등)을 주지 않는다.

### 11.1 사용자(AD) 셀프서비스 메뉴 — 데이터 Sync · 데이터 스캔

사용자(AD 로그인) 인터페이스에 셀프서비스 **데이터 Sync**(단일 복사)와 **데이터 스캔**(운영자 스캔 결과 조회)
메뉴가 있다. Sync는 운영자와 동일한 preview→승인→실행 흐름이되 옵션이 고정된다(우선순위 중간, 병렬 노드 정책
기본값, open_noatime ON, batch_files 100000, bufsize 4MiB, chmod/chown 미제공; 사용자는 `--delete`·`contents`만
선택). 사용자 Sync 요청은 운영자 포탈의 **데이터 Sync 목록에 `[사용자]` 라벨과 함께** 노출되어 운영자가 확인·관리할
수 있다(공유 `sync_jobs` 테이블). 주의할 보안 사항:

- **실행 신원**: 파일시스템↔파일시스템 sync는 **사용자 본인 신원**(`requester_id=<user>`)으로 실행된다(DMS가
  LDAP로 uid 해석 → POSIX 권한 적용). 현재 더미 AD(`ad-user`)는 LDAP에 없어 프리뷰 단계에서 실패할 수 있다(정상 —
  실제 AD 연동 후 동작). 파일시스템↔PVC 혼합은 **차단**(사용자가 운영자에게 요청).
- **PVC↔PVC sync는 현재 root(uid 0)로 실행**된다(PVC 소속 네임스페이스 권한 사전 확인 기능은 추후 추가 예정).
  파괴적 `--delete`는 이 경로에서 **금지**되어 있다(root 미러 삭제 방지). 그럼에도 root 복사 자체가 가능하므로,
  DMS의 특권 요청 게이트를 반드시 좁힌다: `DMS_DM_PRIVILEGED_OPERATORS`(빈 값=모든 mTLS actor 허용)와
  `DMS_DM_PRIVILEGED_SCOPES`(빈 값=모든 스토리지)를 **비워 두지 말고** 허용 스토리지/경로로 제한하거나, 사용자
  셀프서비스를 쓰지 않는다면 `DMS_DM_ALLOW_ROOT_REQUESTER=false`로 root 요청 자체를 차단한다. (BFF는 모든 사용자를
  `mtls:<username>` actor로 표현하므로 빈 allowlist에서는 사용자·운영자가 구분되지 않는다.)
- **데이터 스캔은 읽기 전용**이다 — 사용자는 (스토리지, 경로) 항목을 등록만 하고, 운영자가 배치로 실행한 최신 스캔
  결과를 당겨와 본다(운영자 스캔 시 다음 조회에 자동 반영). DMS 스캔 조회는 actor 스코프가 없어, 사용자가 **접근
  권한 없는 경로의 스캔 메타데이터**(파일 수·용량·atime 히스토그램; 파일 내용 아님)까지 조회할 수 있다. 경로 단위
  권한 확인은 위 PVC 네임스페이스 권한 확인과 함께 추후 과제다.

### 11.2 사용자 로그인 — 회사메일 인증 계정 (더미 AD 제거됨)

사용자 로그인은 이제 **아이디/비밀번호**이고, 계정 생성·비밀번호 재설정은 **회사 메일로 받은 6자리 인증번호**로
본인 확인한다(운영자 토큰 불필요). 설정 절차는 **§10.2**.

> **이전 더미 AD 로그인(`POST /api/auth/login/ad`)은 삭제되었다.** 자격증명을 전혀 검증하지 않고 입력한
> 아이디로 로그인시켰는데, 그 아이디가 DMS의 실행 신원(`requester_id`)으로 그대로 전달되므로 사실상 인증
> 우회였다. `PORTAL_ALLOW_DUMMY_AD` 설정도 함께 제거했다(env 하나로 되살아나는 우회 경로를 남기지 않기 위함).

- **아이디 = 회사 메일 local-part.** `test1.user@samsung.com` → 아이디 `test1.user`. 인증번호는 항상
  `<아이디>@PORTAL_EMAIL_DOMAIN`으로 발송되며, 도메인은 **서버 설정에서만** 오고 사용자 입력에서 파생하지
  않는다(파생하면 임의 외부 주소로 인증번호를 배달시킬 수 있다).
- **예약 아이디 차단(중요).** `root`·`admin`·`operator`·`dms` 등은 가입이 거부된다(`422 username_reserved`).
  특히 `root`는 DMS의 `dm_privileged_requesters`(uid/gid 0, `dm_min_uid` 우회)이므로, 자가 가입을 허용하면
  곧바로 root 권한 데이터 작업이 된다. 목록은 `src/portal/backend/email_codes.py: RESERVED_USERNAMES`.
- **함께 점검할 DMS 설정(운영 과제).** `DMS_DM_PRIVILEGED_OPERATORS`가 비어 있으면 "mTLS actor 전원 허용"이다.
  실제 운영자 목록으로 좁히는 것을 권장한다(DMS 측 env이므로 포탈 배포와 별개로 진행).
- **세션 시크릿 회전.** 더미 AD로 발급된 기존 세션 쿠키는 라우트를 지워도 서명 유효기간(기본 8시간) 동안
  살아 있다. 이번 업그레이드 배포 시 `PORTAL_SESSION_SECRET`을 **함께 교체**해 전부 무효화한다(§7.2).
- 비밀번호 변경은 즉시 기존 세션을 끊지 않는다(쿠키 세션, 최대 8시간). 즉시 차단이 필요하면 세션 시크릿을
  회전한다.
- **알려진 트레이드오프 — 재설정 일시 잠금.** 인증번호 무차별 대입을 막기 위해 누적 실패 예산
  (`PORTAL_EMAIL_MAX_FAILURES_PER_WINDOW`, 기본 20회/시간)을 두는데, 대상 아이디를 아는 제3자가 일부러
  틀린 인증번호를 반복 입력해 **해당 사용자의 비밀번호 재설정을 최대 1시간 막을 수 있다**. 로그인 자체는
  영향받지 않으며, 예산을 없애면 무차별 대입이 열리므로 의도적으로 감수한 설계다. 급하면 운영자가
  `portal.email_verifications`에서 해당 행을 지우면 즉시 해제된다.

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
