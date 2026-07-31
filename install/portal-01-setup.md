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
- **역할 모델(계정 저장소 = role)** — 두 역할 모두 아이디/비밀번호로 로그인하므로 **로그인 방식이
  아니라 자격증명이 어느 테이블에 매칭됐는지**가 역할을 결정한다(`backend/security.py`).
  - `operator` — `portal.operator_users` 계정(다중 계정, 관리 토큰으로 생성/재설정 — §10). 운영자 콘솔.
  - `user` — `portal.user_accounts` 계정. 회사 메일 6자리 인증번호로 셀프서비스 가입/재설정(§10.2).
    사용자 인터페이스.
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
`x-dms-actor`로 실어 나르는데**, mTLS 프로필은 actor를 (하나뿐인) BFF 인증서 subject로
덮어써 모든 운영자를 단일 actor로 뭉갠다. 그래서 **포탈은 노드 에이전트와 같은 내부 신뢰 평면
`dms-api-internal`로 접속한다** — mTLS **off** + shared token + NetworkPolicy(agent + `dms-portal` ns만,
ClusterIP). 근거·구조는 [`dms-05 §1·§7`](dms-05-configuration.md), 매니페스트
`install/kubernetes/dms-api-internal.yaml`.

- **`PORTAL_DMS_API_URL` = `http://dms-api-internal.dms.svc.cluster.local`**(portal.yaml 기본값).
- **`PORTAL_DMS_TOKEN` = DMS `DMS_AUTH_SHARED_TOKEN`**과 동일해야 인증된다(내부 API의 shared-token 게이트).
- 로그인한 운영자/사용자는 BFF가 실어 보내는 `x-dms-actor`로 DMS audit에 남는다(per-operator 보존).
  DM 잡(scan/sync/rm) 경로만 `PORTAL_BACKUP_ACTOR_PREFIX`(기본 `mtls:`)가 붙어 `mtls:<username>`이 되고,
  스토리지 인벤토리 등 나머지 운영자 route는 username을 그대로 보낸다.
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
| DMS audit actor 기본값 | `operator` | `PORTAL_DMS_ACTOR`(요청마다 로그인 운영자로 override) |
| **클러스터 이름**(화면 표시) | `테스트베드` / `운영` / `dms-prod` 등 | `PORTAL_CLUSTER_NAME`(§6.1 — 환경마다 다르게) |
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

- **미설정(`PORTAL_DB_URL` 없음)** — DB 없이 동작하는 것은 **스토리지 인벤토리처럼 DMS를 그대로 프록시하는
  read/write 뿐**이다. 데이터 백업·스캔·Sync·rm·VOC·대시보드 추세, 그리고 **사용자 계정 라우트 전부**가
  `503 portal_db_not_configured`가 된다. 운영자 로그인만 `PORTAL_OPERATOR_USERS`(env 저장소)로 처리된다.
- **설정** — 기동 시 `PORTAL_DB_SCHEMA`(기본 `portal`) 스키마에 자신의 테이블 14개
  (`operator_users`·`user_accounts`·`email_verifications`·`backup_*`·`scan_*`·`sync_jobs`·`rm_jobs`·
  `dashboard_samples` 등)를 자동 생성한다. 운영자 로그인은 DB가 source of truth가 되고(최초 1회
  `PORTAL_OPERATOR_USERS`로 시드), 위 기능이 모두 활성화된다.

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
  - `PORTAL_DMS_API_URL` — BFF가 호출할 DMS API base. **기본값
    `http://dms-api-internal.dms.svc.cluster.local`**(§2)을 그대로 쓴다. 외부 mTLS `dms-api`에 직결하는
    특수 케이스만 §6.3.
  - `PORTAL_SESSION_HTTPS_ONLY` — **운영(TLS 서빙)이면 `"true"`**(기본값·fail-closed). 평문 HTTP NodePort로만
    노출하는 경우에 한해 `"false"`.
  - `PORTAL_DMS_ACTOR` — DMS audit 기본 actor(BFF가 요청마다 로그인 운영자 username으로 override).
  - `PORTAL_CLUSTER_NAME` — **이 포탈이 담당하는 클러스터 이름.** manifest 기본값은
    `CHANGE_ME_CLUSTER_NAME`이므로 **설치 시 반드시 환경에 맞게 바꾼다**(예: `운영`, `테스트베드`).

  manifest는 **개발 전용 메일 설정 4개를 그대로 싣고 있다.** 그냥 apply하면 운영 포탈이 인증번호를
  평문 로그로 뿌리는 `log` 모드로 뜨고 dev 기본 세션 시크릿 허용 플래그까지 켜진 채 배포되므로,
  아래 4개도 반드시 함께 고친다(상세 → [portal-02-user-auth.md](portal-02-user-auth.md) §3·§5):

  - `PORTAL_EMAIL_DOMAIN` — `gmail.com`(연동 전 테스트값) → **회사 메일 도메인**.
  - `PORTAL_EMAIL_DELIVERY` — `log`(개발 전용) → 사내 연동 후 `company`, 아직이면 `none`.
  - `PORTAL_ALLOW_INSECURE_DEFAULTS` — `"1"` → **운영에서는 이 env 자체를 삭제한다.**
  - `PORTAL_SIGNUP_ALLOWLIST` — 특정 아이디가 박혀 있다. 회사 도메인으로 바꾼 뒤에는 비우고,
    공용 도메인을 계속 쓴다면 허용 아이디 목록으로 채운다.

전체 env 변수는 `src/portal/backend/config.py`가 정의한다. 주요 항목:

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `PORTAL_DMS_API_URL` | (없음) | DMS API base. 미설정이면 DMS 연동 route가 `503`. |
| `PORTAL_DMS_VERIFY_TLS` | `true` | DMS가 https일 때 서버 cert 검증(운영 mTLS에서 `true` 유지, CA로 검증). |
| `PORTAL_DMS_TOKEN` | (없음) | **필수** shared bearer token(내부 API의 shared-token 게이트, DMS `DMS_AUTH_SHARED_TOKEN`과 동일 값). Secret으로 주입. |
| `PORTAL_DMS_ACTOR` | `operator` | DMS audit 기본 actor. |
| `PORTAL_CLUSTER_NAME` | (빈 값) | 로그인 화면·상단 타이틀 옆에 칩으로 표시되는 **클러스터 이름**. manifest는 `CHANGE_ME_CLUSTER_NAME`이므로 설치 시 교체한다. 빈 값이면 미표시. 여러 환경의 포탈을 동시에 열어두었을 때 혼동을 막는 용도다. |
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
  PORTAL_DMS_TOKEN: "REPLACE_WITH_DMS_TOKEN"    # 필수 — DMS의 DMS_AUTH_SHARED_TOKEN과 동일 값
                                                #        (내부 평면 dms-api-internal의 유일한 인증)
  PORTAL_ADMIN_TOKEN: "REPLACE_WITH_ADMIN_TOKEN"  # 선택(§10). 안 쓰면 비워 둔다.
  PORTAL_DB_URL: "REPLACE_WITH_DB_URL"          # 선택(§4). 안 쓰면 비워 둔다.
```

### 6.3 (참고) BFF → 외부 mTLS dms-api 직결은 지원하지 않는다

**기본 배포는 §2의 내부 신뢰 평면(`dms-api-internal` + shared token)을 쓴다.** 포탈을 외부 mTLS
`dms-api`에 직접 붙이려면 BFF가 client certificate을 제시해야 하는데, 현재 DMS 클라이언트
(`backend/dms_client.py`)는 bearer + `x-dms-actor`만 붙이고 cert을 로드하지 않는다. 굳이 붙인다면
mTLS를 종단하는 egress proxy/sidecar를 앞에 둬야 하고, 그렇게 하면 **모든 운영자가 단일 BFF 인증서
actor로 뭉개져** per-operator audit이 사라진다. 그래서 내부 평면을 쓰는 것이다.

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

### 8.1 (권장) Ingress + 서버 TLS

**ingress-nginx + MetalLB 설치, 포탈 ingress 적용, 운영 TLS 전환(쿠키 Secure 포함) 절차는
[dms-06-ingress-metallb.md](dms-06-ingress-metallb.md) §5·§7** 에 있다(이미지 미러링, IP 풀 선정,
`host:` 유무에 따른 IP 접속 404 함정, `/healthz` 선점 이슈 포함).

참고 manifest는 `src/portal/deploy/kubernetes/portal-ingress.example.yaml` — host, TLS secret,
`ingressClassName`을 환경에 맞춰 조정한다.

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

# 1) health — ingress-nginx가 `/healthz`를 선점하므로(빈 body 200, dms-06 §5) 파드에서 직접 본다
kubectl -n dms-portal exec deploy/dms-portal -- \
  python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8090/healthz').read().decode())"

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

- `/healthz` → 항상 `status`·`dms_configured`·`db_configured`·`email_configured`·`email_domain`·
  `cluster_name` **6개 키**가 오며, 키 유무가 아니라 **값**으로 판단한다. 정상 설치면
  `dms_configured`·`db_configured`(포탈 DB 사용 시)·`email_configured`(메일 설정 시)가 모두 `true`이고
  `cluster_name`이 §6.1에서 지정한 값이다
- 운영자 로그인 → `{"user":{...,"role":"operator"}}`
- 목록 → DMS에 등록된 storage mapping 개수
- 사용자 인증 설정 → `{"available":true,"email_domain":"...","code_ttl_seconds":600,"email_delivery":"log|company",...}`
  (배송 수단 미설정이면 `"available":false` — 인증요청은 `503`으로 fail-closed)
- **`root` 가입요청 → `422`** (예약 아이디)
- 사용자 세션 → operator API `403`

## 10. 계정 운영

### 10.1 운영자 계정 — 로그인 화면에서 생성·비밀번호 재설정

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
> 엔드포인트는 `503`을 반환한다. apply 후 재주입은 §7.2 참조.

### 10.2 사용자 계정 — 회사메일 인증(6자리) 셀프서비스

사용자(end-user)는 **아이디/비밀번호**로 로그인하고, 계정 생성·비밀번호 재설정은 `<아이디>@<회사도메인>`
으로 받은 **6자리 인증번호**로 본인 확인한다. 운영자 토큰은 쓰지 않는다 — **메일함 통제권이 곧 인가**다.
저장소는 포탈 DB `portal.user_accounts`(PBKDF2) + `portal.email_verifications`이며 `PORTAL_DB_URL`이
필요하다(§4).

설치 시 정해야 할 값은 `PORTAL_EMAIL_DOMAIN`·`PORTAL_EMAIL_DELIVERY`·`PORTAL_SIGNUP_ALLOWLIST`
세 개다(§6.1). **전체 설정 항목, 사내 메일 발송 연동 코드 작성법, 도메인·허용목록 운영, 보안 불변식·
트레이드오프는 → [portal-02-user-auth.md](portal-02-user-auth.md).**

## 11. 보안 주의

- **시크릿을 commit하지 않는다.** manifest의 `PORTAL_SESSION_SECRET`/`PORTAL_OPERATOR_USERS`/`PORTAL_DMS_TOKEN`/
  `PORTAL_ADMIN_TOKEN`/`PORTAL_DB_URL`은 placeholder로 두고, 실값은 라이브 Secret에 patch로만 주입한다(§7.2).
- **세션 시크릿은 강한 랜덤값**(`openssl rand -hex 32`). 알려진 서명키는 운영자 세션 위조 벡터다. BFF는 dev
  기본 세션 시크릿으로는 기동을 거부한다(`PORTAL_ALLOW_INSECURE_DEFAULTS=1`로만 우회, 로컬 dev 전용).
- **운영은 TLS로 서빙**하고 `PORTAL_SESSION_HTTPS_ONLY=true`(기본값)를 유지한다. `false`는 세션 쿠키가 평문으로
  오가 스니핑/재전송 위험이 있어 평문 HTTP 노출 시에만 쓴다.
- **운영자 비밀번호**는 강한 값으로 설정/회전한다(`admin/admin1234`는 데모용).
- Portal Pod에 DMS 토큰 외의 control-plane 자격증명(kubeconfig/SSH key 등)을 주지 않는다.

### 11.1 사용자 셀프서비스 메뉴(데이터 Sync · 데이터 스캔)에서 주의할 점

사용자 인터페이스에는 셀프서비스 **데이터 Sync**(단일 복사)와 **데이터 스캔**(운영자 스캔 결과 조회)
메뉴가 있다. Sync는 운영자와 동일한 preview→승인→실행 흐름이되 옵션이 고정되고(사용자는 `--delete`·
`contents`만 선택), 요청은 운영자 포탈의 데이터 Sync 목록에 `[사용자]` 라벨로 함께 노출된다.
주의할 보안 사항:

- **실행 신원**: 파일시스템↔파일시스템 sync는 **사용자 본인 신원**(`requester_id=<user>`)으로 실행된다(DMS가
  LDAP로 uid 해석 → POSIX 권한 적용). 로그인 아이디(또는 `posix_username`)가 LDAP에 없으면 프리뷰 단계에서
  실패한다. 파일시스템↔PVC 혼합은 **차단**(사용자가 운영자에게 요청).
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

### 11.2 사용자 로그인 — 회사메일 인증 계정

사용자 로그인은 이제 **아이디/비밀번호**이고, 계정 생성·비밀번호 재설정은 **회사 메일로 받은 6자리 인증번호**로
본인 확인한다(운영자 토큰 불필요). 설정 절차는 **§10.2**.

- **예약 아이디 차단(중요).** `root`·`admin`·`operator`·`dms` 등은 가입이 거부된다(`422 username_reserved`).
  특히 `root`는 DMS의 `dm_privileged_requesters`(uid/gid 0, `dm_min_uid` 우회)이므로, 자가 가입을 허용하면
  곧바로 root 권한 데이터 작업이 된다. 목록·상세는 portal-02 §7.3.
- **함께 좁힐 DMS 설정(운영 과제).** `DMS_DM_PRIVILEGED_OPERATORS`가 비어 있으면 "mTLS actor 전원 허용"이다.
  §11.1과 함께 실제 운영자 목록으로 좁힌다(DMS 측 env이므로 포탈 배포와 별개로 진행).
- 비밀번호 변경은 즉시 기존 세션을 끊지 않는다(쿠키 세션, 최대 8시간). 즉시 차단이 필요하면 세션 시크릿을
  회전한다(§7.2).
- 도메인 파생 금지·재설정 일시 잠금 등 **보안 불변식과 알려진 트레이드오프는 portal-02 §8**에 있다.

## 12. 자주 발생하는 문제

- **`/healthz`의 `dms_configured`가 false** — `PORTAL_DMS_API_URL`이 비어 있다. Deployment env 확인(§6.1).
- **DMS 연동 route가 `503 dms_not_configured`** — `PORTAL_DMS_API_URL` 미설정.
- **DMS 연동 route가 `502 dms_unreachable`** — Portal Pod에서 DMS API에 도달 불가. service DNS 도달성과,
  `dms-api-internal`의 NetworkPolicy `dms-api-internal-trusted-clients`에 `dms-portal` 네임스페이스가
  포함돼 있는지 확인한다(`install/kubernetes/dms-api-internal.yaml`).
- **DMS 호출이 `401`/`403`** — 대부분 `PORTAL_DMS_TOKEN`이 DMS의 `DMS_AUTH_SHARED_TOKEN`과 다른 경우다.
  §7.2로 재주입 후 재기동. (외부 mTLS `dms-api`에 직결하도록 바꿨다면 §6.3.)
- **운영자 로그인 `401`** — 포탈 DB를 쓰지 않으면 `PORTAL_OPERATOR_USERS`에 계정이 없거나 비밀번호가
  다른 것이다(라이브 Secret 값 확인). **`PORTAL_DB_URL`을 설정한 배포에서는 `portal.operator_users`가
  source of truth**라 env는 최초 시드일 뿐이므로, Secret만 고쳐도 계속 `401`이다 — §10.1의 비밀번호
  재설정(관리 토큰)으로 바꾼다.
- **Pod가 `CrashLoopBackOff`** — `create_app`의 부팅 가드 3개 중 하나다(`kubectl -n dms-portal logs
  deploy/dms-portal --tail=50`): ① `PORTAL_SESSION_SECRET is unset/default` → §7.2로 실값 주입,
  ② `PORTAL_EMAIL_DELIVERY=log ...` → 운영이면 `company`/`none`으로, 개발이면
  `PORTAL_ALLOW_INSECURE_DEFAULTS=1` 동반(§6.1), ③ `PORTAL_EMAIL_DOMAIN is not a valid domain` →
  도메인 오타.
- **apply 후 갑자기 DMS 호출 `401`/로그인 불가** — `kubectl apply`가 Secret을 placeholder로 덮었다. §7.2
  재패치 + §7.3 재기동.
- **데이터 백업 탭이 `503` / `db_configured`가 false** — `PORTAL_DB_URL`이 비어 있다(또는 placeholder). §7.2로
  실값 주입 후 재기동(§4).

## 다음 문서

- [install/README.md](README.md) — 설치 가이드 인덱스
- [dms-02-core.md](dms-02-core.md) — DMS core 배포(mTLS 인증서·ingress·shared token — 포탈이 의존하는 대상)
- [dms-05-configuration.md](dms-05-configuration.md) — DMS 환경변수 레퍼런스
- [../docs/api/README.md](../docs/api/README.md) — DMS API 개요 + 인증(포탈이 소비하는 계약)
- [../docs/operations-runbook.md](../docs/operations-runbook.md) — 운영 런북
