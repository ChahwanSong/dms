# DMS testbed deployment runbook

Deploys the clean-slate DMS control plane (`dms api` / `dms controller` /
`dms agent`, execution backend = Volcano) onto the `dms` testbed. All
manifests here are written against the **new** CLI/config (`src/dms/cli.py`,
`src/dms/config.py`) -- do not confuse with `legacy/install/`, which targets
the old implementation and is read-only design reference only.

Testbed facts baked into these assets (see repo `CLAUDE.md` for the source of
truth):

| thing | value |
|---|---|
| build/registry node | `pkg-01` (10.10.10.30), has `podman` |
| registry | `pkg-01:5000`, insecure |
| PostgreSQL | `postgresql://dmsapp:AppPass123!@10.10.10.30:5432/dmsdb` |
| LDAP | `ldap://10.10.10.30:389`, base `dc=dms,dc=local` |
| CephFS `cephfs-dms` | `/cephfs`, mounted on w1-5 |
| CephFS `cephfs-third` | `/cephfs-third`, mounted on w1-3 |
| CephFS `cephfs-secondary` | `/cephfs-secondary`, mounted on w4-5 |
| artifact base | `file:///cephfs/dms/artifacts` |
| namespace | `dms`, PSA enforce=privileged |
| Volcano | v1.15.0 installed; queue/priority classes NOT yet installed |

---

## 이미지 빌드 전 게이트: 로컬 풀스택 e2e (수기, 슬라이스 23)

이미지를 만들기 **전에** 개발 머신에서 아래를 돌리고 초록을 확인한다 — §1(비상 빌드)
이든 §8(포탈 빌드)이든 똑같이 적용된다. 빨강이면 빌드를 제출하지 않는다.

```bash
cd frontend && npm run test:e2e
# = npm run build && tsc -p tsconfig.e2e.json && playwright test  (빌드까지 한 방)
```

싸다 — 시나리오 9건에 **약 30초**(빌드 포함, 2026-08-12 실측). 건너뛸 이유가 되는
비용이 아니다. 하네스가 백엔드를 띄우고 끝나면 스스로 죽인다(tmp DB 포함 정리).

**이 게이트는 수기다 — CI 는 없다.** 이 저장소에는 GitHub Actions 도, 이 스위트를
자동으로 돌려주는 어떤 것도 없다. 아무도 대신 돌려주지 않으니 **사람이 빌드 전에
돌리는 것**이 유일한 강제력이다(CI 구축은 별도 슬라이스 몫이다 — 그 사실을 숨기지
않으려고 이 문단을 여기 둔다).

**무엇을 잡는가.** 프론트 단위 테스트(`npx vitest run`, 228건)가 구조적으로 못 보는
네 가지다:

- **기하** — 넓은 표가 레이아웃을 밀어내는 유형의 회귀. 실제로 두 번 났다
  (`9fbef86` 계정 표, `6bc2ecb` 사이드바 밀림). jsdom 에는 레이아웃이 없어 단위
  테스트로는 영원히 안 잡힌다.
- **실 HTTP 왕복** — 세션 쿠키 로그인, 그리고 **`dist` 서빙 + SPA fallback**
  (`api/app.py`). vite dev 서버가 이 코드 경로를 가리므로 개발 중엔 절대 안 돈다.
- **폴링** — 요청 목록의 수렴, 상세 잡의 **종단 뒤 중지**(안 멈추면 무한 폴링).
- **풀스택 부팅** — `migrate` → `api` → `controller` → `agent` 리포트 → 스토리지
  `Ready` 까지가 한 번에 서는지. 하네스가 이걸 못 세우면 스위트는 skip 이 아니라
  실패한다.

클러스터도 PostgreSQL 도 쓰지 않는다(tmp sqlite + `execution_backend=stub`). 그래서
**§7 의 실 클러스터 시나리오를 대체하지 않는다** — 그 앞단에서 싸게 거르는 그물이다.

**전제.**

- 개발 머신에 **Google Chrome**. 러너는 시스템 크롬을 쓴다
  (`frontend/playwright.config.ts` 의 `channel:"chrome"`) — 브라우저 다운로드 0 이
  기본이다. 크롬이 없는 머신에서만 `npx playwright install chromium` 으로 번들
  크로미엄을 받고, 채널을 **빈 값**으로 비워 실행한다:
  `DMS_E2E_BROWSER_CHANNEL= npm run test:e2e`.
- `frontend/node_modules`(`npm ci`)와 저장소 루트 `.venv` 의 `dms` 편집 설치 —
  하네스가 `<repo>/.venv/bin/dms` 를 찾아 백엔드를 직접 띄운다.
- **포트 8093 이 비어 있을 것.** 누가 듣고 있으면 하네스가 즉시 실패한다. 낡은 서버에
  붙어 초록이 나는 것이 e2e 에서 가장 조용한 거짓말이라 의도적으로 거절한다 — 포트를
  바꾸지 말고 이전 실행의 잔재(python/node)를 정리한 뒤 다시 돌린다.

## 0. Registry setup (once)

On `pkg-01`, start the insecure registry and point every cluster node's
CRI-O at it:

```bash
# on pkg-01:
./deploy/docker/registry-setup.sh registry

# from anywhere with ssh to the k8s nodes (or kubectl --context dms configured):
./deploy/docker/registry-setup.sh nodes
```

Verify: `crictl pull pkg-01:5000/dms:dev` from any cluster node should not
error with "http: server gave HTTP response to HTTPS client".

## 1. Build and push images (비상용 — 평상시엔 §8 포탈 빌드를 쓴다)

> 슬라이스 21 부터 **평상시 빌드는 포탈**에서 한다(§8). 이 절은 **비상용**이다 —
> 클러스터가 아직 없거나(최초 부트스트랩), 포탈/컨트롤러가 죽어 빌드를 제출할 수
> 없을 때만 쓴다. 포탈 빌드와 달리 적합성 프리플라이트도, 리소스 봉투도 없다.

빌드하기 전에 위 「이미지 빌드 전 게이트」(`cd frontend && npm run test:e2e`)를
돌린다 — 비상 빌드라고 건너뛰지 않는다.

Also on `pkg-01` (build context = repo root, run from anywhere in the repo):

```bash
REGISTRY=pkg-01:5000 TAG=dev ./deploy/docker/build-and-push.sh
```

Builds and pushes, in order (agent depends on the other two):
`pkg-01:5000/dms-mpifileutils:dev`, `pkg-01:5000/dms:dev`,
`pkg-01:5000/dms-agent:dev`.

If you use a `TAG` other than `dev`, update it in `deploy/k8s/20-config.yaml`
(`DMS_JOB_IMAGE`) and the `image:` fields in `30-migrate-job.yaml`,
`40-api.yaml`, `41-controller.yaml`, `50-agent-daemonset.yaml` to match --
these are static manifests, not templated.

## 2. Mount CephFS on the worker nodes

If not already mounted (testbed IaC target):

```bash
cd ~/dms-dev/testbed
make cephfs-nsync   # or whatever combination of cephfs-* targets mounts
                     # cephfs-dms/cephfs-third/cephfs-secondary on w1-5/w1-3/w4-5
```

Then, on any node with `/cephfs` mounted, create the directories the
manifests/seed step below assume exist:

```bash
mkdir -p /cephfs/dms/artifacts /cephfs/managed
mkdir -p /cephfs-third/managed /cephfs-secondary/managed
```

(`managed_root` for each registered storage below lives under its
`mount_path` -- `StoragesRepository._validate` in
`src/dms/repositories/storages.py` requires `managed_root == mount_path` or
a subdirectory of it. `dms-controller`/`dms-api` need `/cephfs/dms/artifacts`
readable -- see `DMS_ARTIFACT_BASE_URI`.)

## 3. Apply manifests, in order

```bash
kubectl apply -f deploy/k8s/00-namespace.yaml
kubectl apply -f deploy/k8s/05-volcano-queue-priorityclass.yaml
kubectl apply -f deploy/k8s/10-rbac.yaml
kubectl apply -f deploy/k8s/20-config.yaml
```

`20-config.yaml` holds only non-secret config, so it is safe to re-apply on a
running cluster. Never apply `20-secret.example.yaml` — it carries
placeholders, and applying it over a live `dms-secrets` replaces the real
credentials (the api/controller then CrashLoopBackOff on
`password authentication failed for user "dmsapp"`).

## 3b. Create the secret (once, out-of-band)

`dms-secrets` is never committed. Create it directly, with the real DB
password and freshly generated tokens:

```bash
kubectl -n dms create secret generic dms-secrets \
  --from-literal=DMS_DATABASE_URL='postgresql://dmsapp:<DB_PASSWORD>@10.10.10.30:5432/dmsdb' \
  --from-literal=DMS_SHARED_TOKEN="$(openssl rand -hex 24)" \
  --from-literal=DMS_ADMIN_TOKEN="$(openssl rand -hex 24)" \
  --from-literal=DMS_SESSION_SECRET="$(openssl rand -base64 32)" \
  --from-literal=DMS_LDAP_BIND_DN='' \
  --from-literal=DMS_LDAP_BIND_PW=''
```

`DMS_SHARED_TOKEN` grants `role=admin` on every API call (`Bearer <token>`),
so generate it — don't copy one from the repo. Startup rejects any value
containing `CHANGE_ME` or `REPLACE_WITH_` (`src/dms/config.py`
`_is_placeholder`), so a skipped or half-filled secret fails loud instead of
coming up with a publicly known admin token.

Read the value back later with:

```bash
kubectl -n dms get secret dms-secrets \
  -o jsonpath='{.data.DMS_SHARED_TOKEN}' | base64 -d; echo
```

## 4. Migrate

```bash
kubectl apply -f deploy/k8s/30-migrate-job.yaml
kubectl wait --for=condition=complete job/dms-migrate -n dms --timeout=120s
kubectl logs job/dms-migrate -n dms   # expect: "migrated"
```

Re-running: `kubectl delete job/dms-migrate -n dms` then re-apply (Job specs
are immutable, so a stale completed Job blocks re-apply).

## 5. Bring up api / controller / agent

```bash
kubectl apply -f deploy/k8s/40-api.yaml
kubectl apply -f deploy/k8s/41-controller.yaml
kubectl apply -f deploy/k8s/50-agent-daemonset.yaml

kubectl -n dms rollout status deployment/dms-api
kubectl -n dms rollout status deployment/dms-controller
kubectl -n dms rollout status daemonset/dms-agent

kubectl -n dms port-forward svc/dms-api 8080:8080 &
curl -sf http://localhost:8080/healthz   # {"status":"ok"}
```

Give the agent DaemonSet 1-2 report cycles (`DMS_AGENT_INTERVAL_SECONDS=30`)
before seeding -- the planner's admission gate needs at least one fresh
`/api/agent/report` per node before any storage/tool/identity reads as
"Ready" (`src/dms/placement.py::eligible_nodes`).

## 6. Seed storages and policies

All admin calls below authenticate with `Authorization: Bearer
$DMS_SHARED_TOKEN` -- `auth.py::current_identity` maps that bearer straight
to `Identity(role="admin")`, no signup/login needed for scripted seeding.

```bash
# Read the live token instead of pasting one -- never commit a real token.
export DMS_SHARED_TOKEN=$(kubectl -n dms get secret dms-secrets \
  -o jsonpath='{.data.DMS_SHARED_TOKEN}' | base64 -d)
export API=http://localhost:8080
AUTH=(-H "Authorization: Bearer $DMS_SHARED_TOKEN" -H "x-dms-actor: seed-script")

# storages (backend_type must be one of cephfs/gpfs/wekafs; managed_root
# must be mount_path or a subdirectory of it)
curl -sf -X POST "$API/api/admin/storages" "${AUTH[@]}" -H 'content-type: application/json' -d '{
  "storage_name": "cephfs-dms", "mount_path": "/cephfs",
  "managed_root": "/cephfs/managed", "backend_type": "cephfs"}'

curl -sf -X POST "$API/api/admin/storages" "${AUTH[@]}" -H 'content-type: application/json' -d '{
  "storage_name": "cephfs-third", "mount_path": "/cephfs-third",
  "managed_root": "/cephfs-third/managed", "backend_type": "cephfs"}'

curl -sf -X POST "$API/api/admin/storages" "${AUTH[@]}" -H 'content-type: application/json' -d '{
  "storage_name": "cephfs-secondary", "mount_path": "/cephfs-secondary",
  "managed_root": "/cephfs-secondary/managed", "backend_type": "cephfs"}'

# policies -- tool names per repositories/control.py POLICY_TOOLS =
# ("scan", "dsync", "nsync", "rm")  (note: "dsync", not "sync")
for tool in scan dsync nsync rm; do
  curl -sf -X PUT "$API/api/admin/policies/$tool" "${AUTH[@]}" -H 'content-type: application/json' -d '{
    "max_nodes": 3, "procs_per_node": 2, "queue": "dms-data",
    "default_priority": "mid", "max_priority": "high",
    "execution_timeout_seconds": 3600, "enabled": true}'
done

curl -sf "$API/api/admin/storages" "${AUTH[@]}" | python3 -m json.tool
curl -sf "$API/api/admin/policies" "${AUTH[@]}" | python3 -m json.tool
```

## 7. Scenarios

Same `AUTH`/`API` env as above. Every request goes through
`planner -> job-stepper` on `dms-controller`'s loops (default
`DMS_PLANNER_INTERVAL_SECONDS=10`, `DMS_STEPPER_INTERVAL_SECONDS=5`) --
poll `GET .../requests/{id}` a few times rather than expecting an instant
terminal state.

### scan (single storage, no confirm step)

`target`은 storage의 `managed_root` 아래 **실재하는** 상대 경로여야 한다 -- 없으면
preflight가 `target_not_readable`로 잡을 Rejected 시킨다(슬라이스 18 실증에서
`managed`가 실재하지 않아 한 번 밟았다). 현재 `cephfs-dms`(`/cephfs/dms`) 아래에
있는 것: `team`, `artifacts`, `s16-verify`, `s17-live` 등 -- `ls`로 먼저 확인할 것.
그리고 `x-dms-actor`는 **LDAP에 있는 사용자**여야 한다(`alice` 등); 임의 문자열이면
플래너가 `ldap_identity_not_found`로 거절한다.

```bash
RID=$(curl -sf -X POST "$API/api/user/requests" "${AUTH[@]}" -H 'content-type: application/json' -d '{
  "operation": "scan", "storage": "cephfs-dms", "target": "team",
  "priority": "mid"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["request_id"])')

for i in $(seq 1 12); do curl -sf "$API/api/user/requests/$RID" "${AUTH[@]}"; echo; sleep 5; done
# state progression: Pending -> Planned -> ... job: Pending -> Preflight -> Running -> Succeeded
kubectl -n dms get pods,vcjob -l dms.io/job-id  # (job_id from the jobs list below)
curl -sf "$API/api/user/requests/$RID/jobs" "${AUTH[@]}" | python3 -m json.tool
```

### sync (same-node candidates -> tool=dsync, needs confirm)

`cephfs-dms` (w1-5) and `cephfs-third` (w1-3) overlap on w1-3, so
`select_tool_and_candidates` (`src/dms/placement.py`) picks co-located nodes
and tool `dsync`:

```bash
RID=$(curl -sf -X POST "$API/api/user/requests" "${AUTH[@]}" -H 'content-type: application/json' -d '{
  "operation": "sync", "source_storage": "cephfs-dms", "source": "managed/scratch",
  "destination_storage": "cephfs-third", "destination": "managed/copy",
  "priority": "mid"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["request_id"])')

# poll until job state == ConfirmPending, then read preview_fingerprint:
JOB=$(curl -sf "$API/api/user/requests/$RID/jobs" "${AUTH[@]}" | python3 -c 'import sys,json;print(json.dumps(json.load(sys.stdin)[0]))')
JOB_ID=$(echo "$JOB" | python3 -c 'import sys,json;print(json.load(sys.stdin)["job_id"])')
FP=$(echo "$JOB" | python3 -c 'import sys,json;print(json.load(sys.stdin)["preview_fingerprint"])')

curl -sf -X POST "$API/api/user/jobs/$JOB_ID:confirm" "${AUTH[@]}" -H 'content-type: application/json' \
  -d "{\"fingerprint\": \"$FP\"}"
# -> Executing -> Succeeded
```

### nsync (disjoint node pools -> tool=nsync, gang-scheduled)

`cephfs-third` (w1-3) and `cephfs-secondary` (w4-5) share NO node, so
`select_tool_and_candidates` falls through to `nsync` with
`candidates={"source": [...w1-3], "destination": [...w4-5]}` -- this is the
`_build_nsync_job` path in `execution_manifests.py` (separate
source-worker/destination-worker Volcano tasks):

```bash
RID=$(curl -sf -X POST "$API/api/user/requests" "${AUTH[@]}" -H 'content-type: application/json' -d '{
  "operation": "sync", "source_storage": "cephfs-third", "source": "managed/scratch",
  "destination_storage": "cephfs-secondary", "destination": "managed/copy",
  "priority": "mid"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["request_id"])')

# same confirm flow as above once ConfirmPending; then:
kubectl -n dms get vcjob -o wide   # expect tasks: launcher, source-worker, destination-worker
```

### cancel

```bash
# while a job is Executing/Running/PreviewRunning:
curl -sf -X POST "$API/api/user/jobs/$JOB_ID:cancel" "${AUTH[@]}"
# -> {"state": "Cancelled"}; verify the Volcano Job/preflight Pod is gone:
kubectl -n dms get vcjob,pods -l "dms.io/job-id=$JOB_ID"
```

## 8. 포탈에서 이미지 빌드 (슬라이스 11)

> **동작한다(슬라이스 21, 2026-08-11 실증).** 이 문서는 한동안 "구조적으로 불가"라고
> 적고 있었다 -- 빌드 노드(=dms 워커)에 인터넷이 없다는 이유였다. 운영 방식이
> 정해지면서 그 전제가 바뀌었다: **빌드할 때만 운영자가 그 워커에 인터넷을 열고**,
> 포탈이 착수 전에 **적합성 프리플라이트**로 실제 개방 여부를 확인한다(§8-7).
> 빌드 `824ce0e2` 가 `pkg-01:5000/dms:b824ce0e2` 를 실제로 push 했다.
>
> §1 의 pkg-01 podman 경로는 **비상용으로 남긴다** -- 클러스터가 없거나 포탈이 죽은
> 상태에서 이미지를 만들어야 할 때만 쓴다. 평상시 빌드는 포탈에서 한다.

**빌드를 제출하기 전에 위 「이미지 빌드 전 게이트」(`cd frontend && npm run test:e2e`)를
돌린다.** 포탈 빌드는 GitHub 에 push 된 커밋을 대상으로 하지만(아래 1번), 그 커밋이
게이트를 통과했는지 확인해 주는 CI 는 없다 — 제출자가 로컬에서 돌리는 것이 전부다.

포탈이 `dms-mpifileutils`/`dms`/`dms-agent` 이미지를 빌드 노드 위 bare Pod로 빌드해
`DMS_BUILD_REGISTRY`(기본 `pkg-01:5000`)로 push하는 기능. `20-config.yaml`의
`DMS_BUILD_*` 4개 키가 이 기능의 설정 전부이고, 빌드 노드 자체는 ConfigMap에 **없다**
(아래 참고).

**0) 빌드 노드와 소스 경로를 먼저 지정한다.** 포탈 「컨트롤 상태」 화면(`PUT
/api/admin/control-state`, `build_node_name`·`build_source_path`)에서 지정하며,
`control_state` 테이블에 저장된다 — ConfigMap이 아니다, 운영자가 포탈에서 언제든
바꾸는 값이라 재적용마다 되돌아가면 안 되기 때문이다. 지정 전에 「빌드」 화면에서
제출하면 API가 `422 build_node_not_set`/`build_source_not_set`으로 거절한다.

**1) 빌드는 빌드 노드의 로컬 소스에서 뜬다(슬라이스 33).** 빌드 파드가
`build_source_path`(예: `/home/mason/dms-dev/dms`)를 hostPath 로 **읽기 전용**
마운트하고, 시작 시점에 tar 스냅샷을 떠 `/src` 에서 빌드한다
(`src/dms/build_manifests.py`). git 연동이 없다 — **커밋·push 하지 않은 작업
트리도 그대로 빌드된다.** 커밋 SHA 는 마운트의 `.git` 에서 읽어 기록하고, 작업
트리에 미커밋 변경이 있으면 `-dirty` 접미를 붙인다(워크트리 경로처럼 SHA 를 읽을
수 없으면 `unknown`). 테스트베드에서 이 경로는 호스트 작업 트리의 NFS ro 마운트다
(`testbed` 저장소 `make storage`) — 실 클러스터에서는 빌드 노드의 로컬 체크아웃을
그대로 지정하면 된다.

**2) 태그는 지정하거나 파생된다(드리프트 방지 내장, 슬라이스 34).** 「빌드」 폼의
(선택) 태그 입력에 관례 태그(예: `d75`)를 지정하면 그 태그로 push 된다. **빌드는
빌드하는 이미지의 동봉 매니페스트(`deploy/k8s`) 태그를 이 빌드 태그로 자동
스탬프**하므로(`build_manifests._SCRIPT`), 그 태그로 배포하면 **live == 동봉
매니페스트가 되어 드리프트 배지가 안 뜬다** — 예전처럼 손으로 `deploy/k8s` 를 먼저
bump 해 빌드하지 않아도 된다. 단 그 태그를 실제로 굴리려면 `deploy/k8s` 의 **git
값**도 그 태그로 맞춰 `kubectl apply` 해야 새 태그가 배포된다(이미지 안 스탬프는
드리프트 판정용, git 값은 apply 대상). 태그를 비우면 `b<build_id 앞 8자>`
(`build_tag()`)가 파생된다 — 이 자동 태그도 스탬프되므로 릴리스 화면으로 굴리면
드리프트가 없지만, 관례 태그(dNN)를 권한다. 주의: `30-migrate-job.yaml`/
`40-api.yaml`/`41-controller.yaml`/`50-agent-daemonset.yaml`이 전부
`imagePullPolicy: IfNotPresent`이므로, **이미 노드에 있는 태그를 다시 push 해도
클러스터는 새로 집어오지 않는다** — 재빌드는 새 태그로.

**2b) 이미지·이력 정리(슬라이스 34).** 「빌드 > 이미지 관리」 화면에서 레지스트리
태그를 열람·삭제한다(`GET/DELETE /api/admin/registry/images`). **사용 중 태그**
(지금 배포돼 도는 또는 매니페스트가 가리키는)는 서버가 409 로 막는다. 삭제는
레지스트리의 태그(매니페스트)만 지운다 — 디스크 블롭 회수(`registry
garbage-collect`)와 노드 pull 캐시(`crictl rmi`)는 별개의 운영자 작업이고, 시간
기반 자동 GC 는 두지 않는다. 레지스트리 삭제가 `405` 면 pkg-01 의 registry 에
`storage.delete.enabled`(env `REGISTRY_STORAGE_DELETE_ENABLED=true`)가 꺼진
것이다. 빌드 이력 행은 「빌드 이력」 화면에서 다중 선택 삭제(종단 빌드만).

**3) 빌드 노드는 인터넷 egress가 필요하다.** 빌드가 hermetic하지 않다 — Buildah
빌드(privileged 컨테이너) 안에서 npm install(포탈 프론트엔드), `dl.k8s.io`(kubectl 등
설치), PyPI(파이썬 의존성), Debian bookworm 미러(apt 패키지), docker.io(베이스
이미지)에 접근한다. **빌드할 때만 운영자가 그 워커에 인터넷을 열면 된다** — 상시
개방이 아니어도 된다.

**필요한 egress 는 전부 빌드 파드 경로다.** 빌더 이미지는
`pkg-01:5000/buildah:stable` **로컬 미러**를 쓰므로(`20-config.yaml`) 노드(kubelet/
CRI-O)는 인터넷 없이도 빌더를 받는다. 슬라이스 21 실증에서 이걸 안 하면 무슨 일이
나는지 확인했다: 프리플라이트 프로브는 **파드 네트워크**로 검사하는데 빌더 이미지
pull 은 **노드 네트워크**로 일어나, 노드 egress 만 막으면 프로브는 통과하고 빌드
파드가 `ImagePullBackOff` 로 앉는다. 미러 갱신은 `20-config.yaml` 주석의 3줄.

**3b) 착수 전에 적합성 프리플라이트가 돈다(슬라이스 21, 슬라이스 33에서 소스 검사
추가).** 제출하면 빌드 노드 위에 단발 프로브 파드(`dms-build-pf-<build_id[:12]>`,
job image 라 인터넷 없이도 뜬다)가 네 가지를 검사하고 실패하면 **수 초~수십 초
안에** 고유 사유 코드로 끝낸다 — 2시간 generic 타임아웃을 기다리지 않는다:
- 소스 경로에 `deploy/docker/Dockerfile.dms` 존재 → `build_source_unavailable`
  (경로 오타·마운트 소실을 빌드 전에 잡는다)
- egress(`quay.io`·`registry-1.docker.io` TCP 443) → `build_node_no_egress`
  (로그에 실패 호스트 전부)
- `pkg-01:5000` 도달 → `build_registry_unreachable`
- 노드 fs 여유(`avail ≥ 0.15·total + 12GiB`) → `build_node_disk_low`(실측 바이트 기록)

실증(슬라이스 21 당시, 소스가 git 이던 시절): 인터넷을 막은 상태로 제출하니 **45초**
만에 `build_node_no_egress` + `unreachable_443=github.com,quay.io,registry-1.docker.io`.
지금 검사 대상은 `quay.io`·`registry-1.docker.io` 둘이다(소스는 로컬이라 빠졌다).

**3c) 빌드는 데이터 잡과 같은 워커에서 동시에 돈다.** 빌드 노드를 잡 풀에서 빼지
않는다. 빌드 파드는 봉투(cpu 250m/1000m, mem 128Mi/1Gi, eph 10Gi/12Gi)와
PriorityClass `dms-build`(10 < `dms-low` 50)를 달고 돈다 — cpu limit 이 실질
보호막이고(allocatable 1800m 중 최소 800m 이 남는다), 노드가 압박받으면 **빌드가
먼저 죽는다**. 실증: 빌드 중 scan 잡이 평시와 동일한 대기(`sched_wait=5`)로 완료,
잡 파드 축출 0건.

**4) 만들어진 태그를 실제로 쓰려면 매니페스트를 손으로 바꿔 apply해야 한다.**
빌드 성공은 레지스트리에 새 태그를 push하는 것으로 끝난다 — 그 태그로의 자동
롤아웃(매니페스트의 `image:`를 갱신해서 재배포하는 것)은 **이 슬라이스 범위 밖**이다
(다음 슬라이스에서 다룬다). 새로 빌드한 `dms:b<...>`를 실제로 띄우려면, 위 §1의
방식대로 `40-api.yaml`/`41-controller.yaml`/`30-migrate-job.yaml`의 `image:`
필드를 그 태그로 직접 고치고 `kubectl apply`한 뒤 `kubectl rollout restart`(또는
재적용에 따른 자연 롤아웃)로 반영해야 한다. `dms-agent`/`DMS_JOB_IMAGE`는 별도
태그 계열(`dms-agent:dev5`, `dms-mpifileutils:job3`)이라 이 흐름과 독립적이다.

**5) `dms-mpifileutils`는 기본 선택이 아니다.** mpifileutils를 소스에서
`make -j2`로 컴파일하기 때문에 다른 두 이미지보다 훨씬 오래 걸린다 — 「빌드」 화면에서
기본으로 체크돼 있지 않다, 필요할 때(예: mpifileutils 자체를 바꿨을 때)만 명시적으로
포함시킬 것. `dms-agent`는 앞의 두 이미지를 `FROM`하므로, `dms-agent`만 새로 빌드하려면
그 두 태그가 이미 레지스트리에 있어야 한다 — `BUILD_IMAGES` 순서
(`dms-mpifileutils` → `dms` → `dms-agent`, `src/dms/repositories/builds.py`)가 실행
순서를 강제하고, 빌드 스크립트가 `dms-agent`를 빌드할 때 `--build-arg
DMS_IMAGE=$DMS_BUILD_REGISTRY/dms:$DMS_BUILD_TAG`/`MFU_IMAGE=...`로 **이 빌드가 push할
바로 그 태그**를 `FROM`에 고정한다(`src/dms/build_manifests.py`) — 그래서 앞의 둘이 같은
빌드 안에 없거나 그 태그가 레지스트리에 없으면 buildah가 pull에 실패해 시끄럽게 죽는다
(Dockerfile.agent의 `ARG` 기본값 `:dev`로 조용히 폴백하지 않는다).

**6) 동시 빌드는 하나로 제한된다.** 이미 진행 중(`Pending`/`Running`)인 빌드가 있는
채로 `POST /api/admin/builds`를 부르면 `409 build_in_progress`. 새 빌드를 넣기
전에 「빌드 이력」에서 이전 빌드가 종단 상태(`Succeeded`/`Failed`)인지 확인할 것 —
진행 중인 빌드가 있으면 「빌드하기」 화면 위에 배너로도 먼저 알려 준다.

화면: 「빌드」는 하위 페이지 둘이다 — 「빌드하기」(`/admin/builds`, 기본, 제출 폼)와
「빌드 이력」(`/admin/builds/history`, 목록·상태 필터). 제출에 성공하면 이력으로
넘어간다. 빌드 상세(`/admin/builds/:buildId`)에 로그 뷰어가 있고, 빌드 노드 지정은
「컨트롤 상태」 화면.

## 9. 포탈에서 릴리스(롤아웃) (슬라이스 13)

§8의 「빌드」가 레지스트리에 태그를 만드는 데서 끝났다면, 「릴리스」 화면
(`/admin/releases`)은 그 태그를 **실제로 클러스터에 올린다** — 운영자가
`kubectl apply`/`rollout restart` 없이 포탈 안에서 워크로드의 `image:`를 갱신한다.
설정은 `20-config.yaml`의 `DMS_ROLLOUT_INTERVAL_SECONDS`/`DMS_ROLLOUT_TIMEOUT_SECONDS`
둘뿐이고, 레지스트리는 빌드와 같은 `DMS_BUILD_REGISTRY`를 쓴다.

**1) 흐름: 한 배치로 제출하고, 순서는 서버가 강제한다.** 「릴리스」 화면은 세 컴포넌트
(`dms-agent`/`dms-api`/`dms-controller`) 행마다 select를 주고, 올리고 싶은 것만 태그를
골라 **한 번에** 제출한다(`POST /api/admin/releases`). 제출 순서가 무엇이든 서버가
`ROLLOUT_ORDER = ("dms-agent", "dms-api", "dms-controller")`로 정렬해
`releases.seq`에 **DB로 지속**시키고(`src/dms/repositories/releases.py`), 컨트롤러의
RolloutWatcher가 그 seq 순서대로 하나씩 patch → 수렴 확인 → 다음으로 넘어간다. 순서가
행에 박혀 있으므로 배치 중간에 컨트롤러가 죽어도 새 파드가 seq만 보고 이어간다. 동시
롤아웃은 하나로 제한된다 — 진행 중인 배치가 있으면 `409 rollout_in_progress`.

**2) `dms-controller`를 갱신하면 컨트롤러가 자기 자신을 재시작시킨다.** 그래서
`dms-controller`가 배치의 **마지막**이다. 컨트롤러가 자기 Deployment를 patch하는 순간
옛 파드는 종료되고, 「릴리스」 화면의 갱신이 리스 재획득(최대 ~30초 + 파드 기동)만큼
멈춘다 — **장애가 아니다.** 컨트롤러는 patch 전에 행을 `Applying`으로 먼저 기록하므로
(record-then-patch), 새 파드가 그 `Applying` 행을 이어받아 수렴을 확인하고 `Applied`로
닫는다. 화면이 잠시 얼어 있어도 기다릴 것 — 이것이 이 기능의 정상 동작이다.
간격을 늘리면(`DMS_ROLLOUT_INTERVAL_SECONDS`) per-loop 리스가 함께 길어져 이 정지
구간이 몇 배로 늘어난다.

**3) 같은 태그 재롤아웃은 거절된다(`same_tag`).** 현재 워크로드에 걸린 것과 같은 태그를
고르면 `422 same_tag`로 막는다 — 모든 매니페스트가 `imagePullPolicy: IfNotPresent`라
같은 태그를 다시 밀어봐야 파드 스펙이 그대로여서 아무 일도 일어나지 않기 때문이다(§8-2와
같은 함정). 레지스트리에 없는 태그는 `422 unknown_tag`, 모르는 컴포넌트는
`422 unknown_component`.

**4) 롤아웃 성공 후 매니페스트의 `image:`를 손으로 맞춰야 한다(설계 §9).** 정적 YAML이
여전히 **선언적 진실**이다. 롤아웃은 살아 있는 클러스터 오브젝트만 바꾸므로, 파일을
그대로 두면 다음 `kubectl apply -f deploy/k8s/`가 클러스터를 옛 태그로 **되돌린다.**
성공한 배치마다:

- `dms` 계보: `30-migrate-job.yaml`/`40-api.yaml`/`41-controller.yaml` 세 파일 모두
  (하나라도 빠지면 그 컴포넌트만 옛 이미지로 돈다)
- `dms-agent` 계보: `50-agent-daemonset.yaml`

이 슬라이스는 파일을 자동으로 고치지 않는다 — 컨트롤러 파드 안에 저장소가 없다.
어긋남을 화면에 표시하는 것은 슬라이스 14 대시보드의 몫이다. Helm/kustomize는 도입하지
않는다 — 이 README에 기록된 설계 결정이다(§1, "Unresolved values" 참고).

**5) 태그 계보 세 개는 서로 독립이고, `DMS_JOB_IMAGE`는 롤아웃 대상이 아니다.**
`dms:`(api/controller/migrate가 공유), `dms-agent:`, `dms-mpifileutils:`는 각각 다른
레지스트리 리포이고 버전이 같이 갈 이유가 없다 — 「릴리스」 화면은 컴포넌트별로 자기
리포의 태그 목록만 보여준다(`COMPONENTS[*].repository`). `DMS_JOB_IMAGE`
(`dms-mpifileutils`)는 **롤아웃 대상이 아니다**: 워크로드 이미지 패치가 아니라 ConfigMap
갱신 + 소비자 재시작이 필요해서 범위 밖이다(설계 §10). 바꾸려면 지금처럼
`20-config.yaml`을 고쳐 apply한다.

**6) RBAC은 세 워크로드로 좁혀져 있다.** 컨트롤러 Role의 apps `get`/`patch`는
`resourceNames: ["dms-api", "dms-controller", "dms-agent"]`로 한정된다
(`10-rbac.yaml`) — 컨트롤러가 네임스페이스의 임의 워크로드를 건드릴 수 없다.
api Role은 같은 세 이름에 **읽기 전용 `get`만** 받는다(「릴리스」 화면이 현재
이미지를 보여주기 위한 것) — patch는 컨트롤러에만 있다. `list`와 `*/status`
규칙은 **두지 않는다**: 코드는 `read_namespaced_deployment`/`_daemon_set`으로
메인 리소스만 읽고(상태는 그 안에 담겨 온다) 어디서도 apps를 list하지 않는다.
특히 `list`는 `resourceNames`를 따르지 않아, 두면 Role이 네임스페이스의 모든
워크로드로 조용히 넓어져 위의 "세 워크로드로 좁혀져 있다"가 사실이 아니게 된다.
새 태그를 쓰기 전에 `10-rbac.yaml`을 apply해야 한다. 확인:

```bash
kubectl --context dms auth can-i patch deployments.apps/dms-controller \
  --as=system:serviceaccount:dms:dms-controller -n dms      # yes
kubectl --context dms auth can-i patch deployments.apps \
  --as=system:serviceaccount:dms:dms-api -n dms             # no
kubectl --context dms auth can-i list deployments.apps \
  --as=system:serviceaccount:dms:dms-controller -n dms      # no (의도적)
```

**7) 없는 태그를 강제로 넣으면 시끄럽게 실패한다.** 레지스트리가 다운이면 태그 검증이
fail-open이라(설계 §7) 존재하지 않는 태그가 통과할 수 있다. 그 경우 파드가
`ImagePullBackOff`에 빠지고, Deployment는 `ProgressDeadlineExceeded`로, DaemonSet은
`DMS_ROLLOUT_TIMEOUT_SECONDS` 벽시계로 `Failed`가 된다(DaemonSet에는
`progressDeadlineSeconds`가 없어 이 값이 유일한 실패 수단이다). 실패하면 배치의 남은
컴포넌트는 `rollout_aborted`로 닫히고 반쯤 섞인 버전 조합이 생기지 않는다 — 복구는
이력에서 옛 태그를 골라 다시 롤아웃하면 된다(별도 롤백 버튼이 없는 이유).

### 실증 체크리스트 (설계 §11 — 테스트베드에서 수행)

- [ ] 1. `GET /api/admin/releases/targets`가 세 컴포넌트의 **현재 이미지**와 레지스트리
      태그 목록을 준다.
- [ ] 2. 현재와 같은 태그 제출이 `same_tag`로 거절된다(`IfNotPresent` 함정).
- [ ] 3. 레지스트리에 없는 태그가 `unknown_tag`로 거절된다.
- [ ] 4. **`dms-agent` 롤아웃**(`dev5` → 새 태그) — DaemonSet 세대 게이트와 4조건 수렴
      판정이 실제로 동작해 `Applied`로 넘어간다. 5노드 순차 롤링이 600초를 정상적으로
      넘긴다면 `DMS_ROLLOUT_TIMEOUT_SECONDS`를 올릴 것(여기서 실측한다).
- [ ] 5. **`dms-api` 롤아웃** — Deployment 조건 기반 판정(세대 게이트 → PDE → 3조건).
- [ ] 6. **`dms-controller` 자기 갱신 — 이 슬라이스의 핵심 실증.** 컨트롤러가 자기
      Deployment를 patch해 죽은 뒤, **새 파드가 `Applying` 행을 이어받아 `Applied`로
      수렴**시킨다. 화면 정지 구간(리스 재획득 ~30초 + 기동)을 실제로 재고, 그 뒤
      배치가 스스로 닫히는지 확인한다.
- [ ] 7. 감사 로그에 `mutation_class=release`가 남는다.
- [ ] 8. 존재하지 않는 태그로 강제 패치 시 `ImagePullBackOff` 후 타임아웃 또는
      `ProgressDeadlineExceeded`로 `Failed`가 된다.

화면: 「릴리스」(`/admin/releases`) — 컴포넌트 3행 + 태그 select, 한 배치 제출, 진행
중에는 폴링, 아래에 롤아웃 이력.

---

## 10. 포탈 HTTPS 노출 — nginx ingress + TLS + BGP VIP (2026-08-19 프로덕션 자세 완증)

경로: `브라우저 → https://dms.local (공인 VIP 10.20.20.100, 노드와 다른 서브넷)
→ 라우터(BGP 학습 경로, ECMP) → 노드 → ingress-nginx(TLS 종단, replicas 2) →
svc dms-api:8080`. 앱 구조 무변경 — FastAPI 가 지금처럼 SPA+API 를 서빙하고
ingress 는 프록시만 한다.

선행 컴포넌트(설치 완료, 이미지는 pkg-01:5000 미러): MetalLB v0.16.0,
ingress-nginx v1.15.1(IngressClass `nginx`). 테스트베드의 "라우터"는
luminous(10.10.10.1)의 FRR(AS 64500, listen range 10.10.10.0/24) — 실환경에선
네트워크팀 라우터와 피어링만 바꾼다.

절차:

1. TLS secret (git 밖 — 개인키). 리허설은 자체 CA, 프로덕션은 사내 PKI 발급분:
   `kubectl -n dms create secret tls dms-portal-tls --cert=tls.crt --key=tls.key`
   (SAN: DNS dms.local + IP 10.20.20.100. 리허설 CA·인증서 사본:
   luminous `~/.claude/jobs/b182a2ed/tmp/tls/`)
2. `kubectl apply -f deploy/k8s/47-metallb-bgp.yaml` (공인 풀 autoAssign=false ·
   BGPPeer · BGPAdvertisement) + ingress svc 에 풀 지정:
   `kubectl -n ingress-nginx annotate svc ingress-nginx-controller metallb.io/address-pool=dms-public-pool`
3. `kubectl apply -f deploy/k8s/46-ingress.yaml` — 어노테이션(20m 바디·300s
   타임아웃)은 그 파일 주석에.
4. 앱: 20-config 의 `DMS_SESSION_COOKIE_SECURE: "true"`(세션 쿠키 Secure) 적용
   후 api 롤아웃. 평문 NodePort 서비스는 제거됐다(구 45-api-nodeport.yaml) —
   자동화·비상 접근은 Bearer 토큰(shared/admin) 또는 https://dms.local.
5. 클라이언트 DNS/hosts 에 `10.20.20.100 dms.local`.
6. 검증(전부 실증됨): FRR `show bgp summary` = 노드 6 피어 Established,
   `ip route show 10.20.20.100` = BGP ECMP(컨트롤러 노드 2개 next-hop —
   svc 가 externalTrafficPolicy=Local 이라 컨트롤러 있는 노드만 광고),
   `curl --cacert ca.crt https://dms.local/` = 200(검증 통과), `http://` = 308,
   로그인 Set-Cookie 에 `Secure`, 로그인·admin API·SPA 딥링크 https 로 200.

ingress-nginx 컨트롤러가 죽거나 노드가 내려가면 그 노드의 VIP 광고가 BGP 로
자동 철회되고 남은 레플리카 노드로만 라우팅된다 — L2 모드의 ARP 재광고보다
빠르고 결정적이다.

---

## Unresolved values to fill in during live validation

- **`DMS_LDAP_BIND_DN` / `DMS_LDAP_BIND_PW`** (Secret `dms-secrets`, shape in
  `deploy/k8s/20-secret.example.yaml`): empty (anonymous bind). Only `DMS_LDAP_URI`,
  `DMS_LDAP_USER_BASE`, `DMS_LDAP_GROUP_BASE` were given as testbed facts --
  the bind account/password for `ldap://10.10.10.30:389` needs the real
  value. A wrong value fails soft (`IdentityRejected`/`IdentityUnavailable`
  at plan time, not a container crash), so this can be fixed and rolled out
  without redeploying anything else.
- **`DMS_ALLOW_PRIVILEGED_REQUESTERS` / `DMS_PRIVILEGED_REQUESTERS`**
  (`deploy/k8s/20-config.yaml`, ConfigMap): **default is `true` /
  `root,admin`** (also the code default in `src/dms/config.py`). A request
  whose authenticated `requester_id` (x-dms-actor) is `root` or `admin` runs
  as **uid 0/gid 0 (root)** and skips the LDAP node-identity check; everyone
  else runs as their own resolved LDAP identity. The gate keys on the
  authenticated `requester_id`, NOT the client-supplied `owner_username`, so a
  normal user cannot escalate (owner_username != actor -> 403
  `privileged_not_authorized`). Note the shared-token lets a caller set
  x-dms-actor freely, so a shared-token holder can pick `root`/`admin` and get
  root -- use session-based actors in production, and keep the allowlist
  minimal. To disable entirely: `DMS_ALLOW_PRIVILEGED_REQUESTERS: "false"`
  (or `DMS_PRIVILEGED_REQUESTERS: ""` -- an explicit empty string overrides
  the default to no privileged requesters).
- **`DMS_JOB_IMAGE` / image tags**: manifests hardcode tags across three
  independent lineages -- `pkg-01:5000/dms:<tag>` (api/controller/migrate,
  currently `d23`), `pkg-01:5000/dms-agent:<tag>` (agent DaemonSet, `dev5`),
  `pkg-01:5000/dms-mpifileutils:<tag>` (`DMS_JOB_IMAGE` ConfigMap value,
  `job3`). Keep `build-and-push.sh`'s `TAG` and the manifests' tags in sync
  manually (no templating layer here by design -- see CLAUDE.md's
  "legacy/install/ 미러 금지" instruction, which ruled out introducing e.g.
  Helm/kustomize for this pass). Portal-driven rollout (§9) patches the live
  `dms:` and `dms-agent:` workloads but does NOT rewrite these files, so after
  a rollout you must hand-edit the tag here to keep repo and cluster aligned.
- **`/cephfs` scheduling assumption on `dms-api`/`dms-controller`**: both
  Deployments hostPath-mount `/cephfs` with `type: Directory` (fails pod
  admission on a node without that mount). This is safe today because the
  only schedulable nodes are w1-5, all of which mount `cephfs-dms`. If a
  non-cephfs node joins the schedulable pool, add a nodeSelector/affinity or
  relax to `DirectoryOrCreate`.
- **DaemonSet node heterogeneity**: `cephfs-third` volume is
  `DirectoryOrCreate` so the agent pod still starts on w4-5 (which never had
  `/cephfs-third`) -- it just shadows an empty dir there, and
  `probe_mounts()` correctly reports that storage `Missing` on those nodes.
  Confirm this reads as expected once agents are actually running.
- **Agent os-metrics network figures**: `probe_os_metrics()` reads the HOST
  network namespace's counters, so `network_rx_bytes`/`network_tx_bytes` are
  real node throughput. The DaemonSet bind-mounts `/proc/1/net/dev` (PID 1 =
  host netns) as a hostPath `type: File` at `/host/proc/1/net/dev` and points
  the probe there with `DMS_AGENT_NET_DEV_PATH` -- the same injection
  convention `DMS_AGENT_MOUNTINFO_PATH` already uses. A plain `/host/proc`
  directory mount would NOT fix it: `/proc/net/*` reflects the *reader's*
  netns, so it has to be the PID 1 file.
  **Do not adopt `hostNetwork: true`** as an alternative (design §2.5 rejected
  it): without a matching `dnsPolicy: ClusterFirstWithHostNet` the agent pod
  loses cluster DNS, can no longer resolve `dms-api`, and stops reporting
  **permanently and silently** -- the report loop is fail-soft, so nothing
  surfaces the breakage. It also widens the agent's network exposure for no
  gain over the bind mount.
- **Agent os-metrics counts PHYSICAL interfaces only** (design §2.6): summing
  every non-`lo` interface in the host netns double-counts cross-node pod
  traffic (`cilium_vxlan` *and* `eth0` see the same bytes) and mixes in pod
  veth host ends (`lxc*`). Detection is by kernel registration site, not by
  name -- the kernel registers virtual interfaces under
  `/sys/devices/virtual/net/<name>`, so an interface in `/proc/net/dev` with
  no directory there is physical. Prefix blocklists (`lxc*`/`cilium_*`/
  `cali*`/...) differ per CNI and fail silently. The DaemonSet mounts that
  directory read-only as hostPath `type: Directory` at
  `/host/sys/devices/virtual/net` and points the probe at it with
  `DMS_AGENT_VIRTUAL_NET_PATH`.
  **`DMS_AGENT_VIRTUAL_NET_PATH` defaults to UNSET on purpose -- never set it
  to the in-container `/sys/devices/virtual/net`.** A pod has that path too,
  but what it holds is the *pod netns's* virtual interfaces. Pointing the
  probe there filters the **host's** interface list through a set from a
  **different namespace** -- the two were never comparable, so **any** host
  interface whose name collides with a pod-side one is misjudged virtual and
  dropped. `eth0` is merely where that collision is most likely (CNIs name the
  pod interface `eth0` by convention, and VM/cloud hosts often name the
  physical NIC `eth0` too), but a host on `ens192`/`enp5s0`/`eno1` breaks the
  same way if the name collides. Unset (or an unreadable path) means no
  filtering: the probe keeps today's all-but-`lo` sum rather than losing the
  metric.
