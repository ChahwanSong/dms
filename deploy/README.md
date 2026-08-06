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

## 1. Build and push images

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

```bash
RID=$(curl -sf -X POST "$API/api/user/requests" "${AUTH[@]}" -H 'content-type: application/json' -d '{
  "operation": "scan", "storage": "cephfs-dms", "target": "managed",
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

포탈이 `dms-mpifileutils`/`dms`/`dms-agent` 이미지를 빌드 노드 위 bare Pod로 빌드해
`DMS_BUILD_REGISTRY`(기본 `pkg-01:5000`)로 push하는 기능. `20-config.yaml`의
`DMS_BUILD_*` 4개 키가 이 기능의 설정 전부이고, 빌드 노드 자체는 ConfigMap에 **없다**
(아래 참고).

**0) 빌드 노드를 먼저 지정한다.** 포탈 「컨트롤 상태」 화면(`PUT
/api/admin/control-state`, `build_node_name`)에서 지정하며, `control_state`
테이블에 저장된다 — ConfigMap이 아니다, 운영자가 포탈에서 언제든 바꾸는 값이라
재적용마다 되돌아가면 안 되기 때문이다. 지정 전에 「빌드」 화면에서 제출하면 API가
`422 build_node_not_set`으로 거절한다.

**1) 빌드는 GitHub에 push된 커밋만 대상이다.** 빌드 파드는 빌드 노드 위에서
`git clone --depth 1 --branch "$DMS_BUILD_REF" "$DMS_BUILD_REPO"`로 소스를 가져온다
(`src/dms/build_manifests.py`). `--depth 1 --branch`는 브랜치/태그 **이름**만
받는다 — 임의의 커밋 SHA는 clone 대상이 될 수 없다. 즉 로컬에만 있는 커밋(이 저장소를
포함해서)은 빌드되지 않는다: 먼저 `git push origin <branch>`로 GitHub에 올려야 포탈
빌드가 그 내용을 볼 수 있다. 「빌드」 상세 화면이 clone 대상 ref와 실제로 해석된 commit
SHA를 함께 보여주는 이유가 이것이다 — 화면에 보이는 SHA가 기대한 커밋인지 항상 확인할 것.

**2) 빌드마다 새 태그가 나온다.** 태그는 `b<build_id 앞 8자>`(`build_tag()`,
`src/dms/repositories/builds.py`) — 커밋 SHA가 아니라 빌드 고유 id에서 뽑는다(같은
커밋을 두 번 빌드하는 것도 정상 동작이다). `30-migrate-job.yaml`/`40-api.yaml`/
`41-controller.yaml`/`50-agent-daemonset.yaml`이 전부 `imagePullPolicy:
IfNotPresent`이므로, **같은 태그를 다시 push해도 클러스터는 절대 새로 집어오지
않는다** — 이미 그 태그의 이미지를 가진 노드는 로컬 캐시를 그대로 쓴다. 빌드마다 새
태그가 나오는 이유가 바로 이것이다.

**3) 빌드 노드는 인터넷 egress가 필요하다.** 빌드가 hermetic하지 않다 — Buildah
빌드(`quay.io/buildah/stable` 컨테이너, privileged) 안에서 npm install(포탈
프론트엔드), `dl.k8s.io`(kubectl 등 설치), `github.com`(소스 clone), PyPI(파이썬
의존성), Debian bookworm 미러(apt 패키지)에 접근한다. 방화벽/프록시로 격리된 빌드
노드에서는 실패한다 — 사전에 해당 egress를 열어둘 것.

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
(`dms-mpifileutils` → `dms` → `dms-agent`, `src/dms/repositories/builds.py`)가 이
의존 관계를 강제한다.

**6) 동시 빌드는 하나로 제한된다.** 이미 진행 중(`Pending`/`Running`)인 빌드가 있는
채로 `POST /api/admin/builds`를 부르면 `409 build_in_progress`. 새 빌드를 넣기
전에 「빌드」 화면에서 이전 빌드가 종단 상태(`Succeeded`/`Failed`)인지 확인할 것.

화면: 「빌드」(`/admin/builds`, 목록/제출) 와 빌드 상세(`/admin/builds/:buildId`,
로그 뷰어 포함). 빌드 노드 지정은 「컨트롤 상태」 화면.

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
- **`DMS_JOB_IMAGE` / image tags**: all manifests hardcode `:dev`. Keep
  `build-and-push.sh`'s `TAG` and the manifests' tags in sync manually (no
  templating layer here by design -- see CLAUDE.md's "legacy/install/ 미러
  금지" instruction, which ruled out introducing e.g. Helm/kustomize for this
  pass).
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
- **Agent os-metrics network figures**: `probe_os_metrics()` reads
  `/proc/net/dev` from the container's own (pod) network namespace, not the
  host's -- `network_rx_bytes`/`network_tx_bytes` in agent reports will
  reflect the pod's veth, not real node throughput. `hostNetwork: true`
  would fix this but was not added (out of scope for this pass / changes the
  agent's security posture); flagging for follow-up if that metric matters.
