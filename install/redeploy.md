# 소스 수정 후 재배포 (DMS 코어 · Portal)

이미 설치된 클러스터에 **소스코드 변경**을 반영하는 절차다. 최초 설치는
[dms-02-core.md](dms-02-core.md) / [portal-01-setup.md](portal-01-setup.md), 무중단·drain·백업·rollback
까지 포함한 **정식 업그레이드 절차**는 [../docs/operations-runbook.md](../docs/operations-runbook.md) §8~§10
을 따른다. 이 문서는 "빌드 → 이미지 교체(rollout)"의 빠른 참조다.

## 0. 어떤 소스 → 어떤 이미지 → 어떤 워크로드

| 수정한 소스 | 재빌드 이미지 (`IMAGES` 토큰) | 반영 대상 |
|---|---|---|
| `src/dms/` (api·planner·workers·adapters·query·repositories 등) | `dms` | ns `dms` Deployment: `dms-api`·`dms-planner`·`dms-dm-worker`·`dms-retention`·`dms-sanity-reconciler` |
| `src/dms/agent*.py` (노드 agent) | `agent` (dms 위에 빌드되므로 `dms`도 함께) | ns `dms` DaemonSet: `dms-dm-agent` |
| DM 잡 도구 이미지(mpifileutils) | `mpifileutils` | DM 잡 런타임 이미지(Deployment 아님; dm-worker가 잡 생성 시 참조) |
| `src/portal/` (BFF `backend/` · SPA `frontend/`) | `portal` | ns `dms-portal` Deployment: `dms-portal` |
| DB **schema**(`src/dms/migrations.py`) 변경 동반 | `dms` | 이미지 교체 **전에** migrate — 아래 §2 주의 |

> 컨테이너명 규약: ns `dms` Deployment는 이름에서 `dms-`를 뗀 것(`dms-api`→`api`,
> `dms-dm-worker`→`dm-worker` …), agent DaemonSet은 `agent`, 포탈은 `portal`.

## 1. 공통: 이미지 빌드 · push

빌드 컨텍스트는 **repo root**이고 **작업 트리 그대로** 이미지에 담긴다(커밋 없이도 빌드되지만, 배포하는
커밋은 남기는 것을 권장). 수정 범위에 해당하는 `IMAGES`만 빌드한다.

```bash
export REGISTRY=<registry>      # 예: pkg-01:5000
export TAG=<새 태그>            # 프로젝트 관례: 단조 증가 vNNN (예: v206→v207) 또는 git short SHA

# 포탈만 수정
REGISTRY=$REGISTRY TAG=$TAG IMAGES="portal" PUSH=1 ./install/docker/build-images.sh
# DMS 코어 수정 (agent 코드도 바뀌었으면 "dms agent")
REGISTRY=$REGISTRY TAG=$TAG IMAGES="dms" PUSH=1 ./install/docker/build-images.sh
# DM 잡 도구 이미지 수정
REGISTRY=$REGISTRY TAG=$TAG IMAGES="mpifileutils" PUSH=1 ./install/docker/build-images.sh
```

- **프록시 전용망**: `PROXY=http://127.0.0.1:7227` 를 추가하면 빌드 시에만 프록시를 타고 런타임 이미지엔
  남지 않는다(메커니즘은 [dms-02-core.md](dms-02-core.md) §1, [portal-01-setup.md](portal-01-setup.md) §5).
- **push 전 로컬 점검**: `PUSH` 생략 후 `docker run --rm -p 18090:8090 -e PORTAL_ALLOW_INSECURE_DEFAULTS=1 <이미지> &`
  → `curl /healthz`.

## 2. DMS 코어 재배포 (ns `dms`)

### 2.1 코드만 변경 (schema·env 무변경) — 이미지 교체

`dms` 이미지를 쓰는 **모든 Deployment**를 새 태그로 교체한다. 현재 대상 목록을 먼저 확인한다(테스트베드는
Deployment가 늘 수 있다):

```bash
NEW="$REGISTRY/dms:$TAG"
kubectl -n dms get deploy -o wide | grep -E "/dms:"     # dms 이미지를 쓰는 Deployment 확인

kubectl -n dms set image deploy/dms-api               api=$NEW
kubectl -n dms set image deploy/dms-api-internal      api=$NEW   # agent 전용 내부 API(동일 dms 이미지)
kubectl -n dms set image deploy/dms-planner           planner=$NEW
kubectl -n dms set image deploy/dms-dm-worker         dm-worker=$NEW
kubectl -n dms set image deploy/dms-retention         retention=$NEW
kubectl -n dms set image deploy/dms-sanity-reconciler sanity-reconciler=$NEW

for d in dms-api dms-api-internal dms-planner dms-dm-worker dms-retention dms-sanity-reconciler; do
  kubectl -n dms rollout status deploy/$d --timeout=180s
done
```

> **누락 주의**: `dms-dm-worker`·`dms-retention`·`dms-sanity-reconciler`를 빠뜨리면 스테일 이미지로 남는다.
> 운영 런북 §9는 핵심 4개만 나열하므로, 반드시 위 `grep /dms:` 로 실제 대상 전체를 교체한다.

### 2.2 schema · env · Secret 변경을 동반할 때

- **schema(`migrations.py`) 변경**: 이미지 교체 **전에** 두 DB 백업(런북 §8) → migrate Job 재생성·실행
  (런북 §9 ③). 새 코드가 없는 스키마로 뜨지 않도록 순서를 지킨다.
- **env / Secret 변경**: `kubernetes/control-plane.yaml`(ConfigMap·Secret) 수정·apply 후 `dms-api`와
  `dms-sanity-reconciler`를 rollout restart 해야 반영된다(런북 §3·§9).
- **무중단·drain·복구·rollback 포함 정식 절차는 [../docs/operations-runbook.md](../docs/operations-runbook.md)
  §9(업그레이드)를 따른다.**

### 2.3 agent 코드 변경 (DaemonSet)

`agent` 이미지는 `dms` + `mpifileutils` 위에 빌드되므로 `IMAGES="dms agent"`로 함께 빌드한다
(`dms-mpifileutils:$TAG`가 로컬에 없으면 운영 중인 태그를 pull 후 `docker tag`로 재태그 — 도구
이미지는 agent 코드 변경과 무관하므로 재컴파일 불필요). `dms-dm-agent`는 **`dms-agent`
이미지**(dms + mfu 도구)를 쓴다 — plain `dms` 이미지로 바꾸면 안 된다.

```bash
kubectl -n dms set image daemonset/dms-dm-agent agent=$REGISTRY/dms-agent:$TAG
kubectl -n dms rollout status daemonset/dms-dm-agent --timeout=180s
```

## 3. Portal 재배포 (ns `dms-portal`)

### 3.1 코드만 변경 (env·Secret·manifest 무변경) — **권장, Secret 보존**

포탈은 단일 Deployment다. **`kubectl set image`로 이미지만 교체**하면 라이브 Secret은 그대로 유지된다
(`kubectl apply`를 쓰지 않으므로 재주입 불필요).

```bash
kubectl -n dms-portal set image deployment/dms-portal portal=$REGISTRY/dms-portal:$TAG
kubectl -n dms-portal rollout status deployment/dms-portal --timeout=120s

# 검증: dms/db 연결 OK
kubectl -n dms-portal exec deploy/dms-portal -- \
  python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8090/healthz').read().decode())"
# → {"status":"ok","dms_configured":true,"db_configured":true}
```

- 포탈 DB(`portal` 스키마)의 테이블/컬럼 추가는 **기동 시 idempotent DDL로 자동 반영**되므로 별도 migrate가
  없다(신규 컬럼·테이블도 재기동만으로 생성).

### 3.2 manifest(env·Secret·리소스) 변경을 동반할 때

`kubernetes/portal.yaml`을 바꿨을 때만 apply한다. **`kubectl apply`는 Secret 값을 placeholder로 덮으므로**
apply 후 반드시 [portal-01-setup.md](portal-01-setup.md) §7.2 로 실값(`PORTAL_SESSION_SECRET`·
`PORTAL_OPERATOR_USERS`·`PORTAL_DMS_TOKEN`·`PORTAL_ADMIN_TOKEN`·`PORTAL_DB_URL`, 사내 메일 연동 시
그 자격증명 키)을 재주입하고 §7.3 rollout restart 한다. **코드만 바뀌었으면 apply 하지 말고
§3.1의 set image만 쓴다.**

> **env만 추가하면 되는 경우**는 apply 대신 `kubectl -n dms-portal set env deploy/dms-portal KEY=VALUE`를 쓰면
> Secret을 건드리지 않아 재주입이 필요 없다(예: 사용자 인증용 `PORTAL_EMAIL_DOMAIN`·`PORTAL_EMAIL_DELIVERY`·
> `PORTAL_SIGNUP_ALLOWLIST`). 비밀값만 `kubectl patch secret`으로 넣는다
> ([portal-02-user-auth.md](portal-02-user-auth.md) §6).

## 4. RM 제거 릴리스로 올릴 때 (일회성 정리)

RM(Resource Management) 기능이 제거된 릴리스로 처음 올리는 **기존 배포**는 아래를 한 번 수동으로
정리한다. **DMS는 이 정리를 자동으로 하지 않는다** — 특히 DB 삭제는 되돌릴 수 없으므로 운영자가
의도적으로 실행해야 한다.

### 4.1 워크로드 제거 (필수)

새 매니페스트에는 이 오브젝트들이 더 이상 없고, `kubectl apply`는 사라진 오브젝트를 지우지 않는다:

```bash
kubectl -n dms delete deployment/dms-rm-worker --ignore-not-found
kubectl -n dms delete daemonset/dms-rm-agent  --ignore-not-found
kubectl -n dms delete serviceaccount/dms-rm-worker --ignore-not-found
kubectl -n dms delete secret/dms-ssh-client --ignore-not-found   # RM host-exec 전용이었음
```

대상 클러스터마다(멀티 클러스터면 전부) 이름이 바뀌면서 고아가 된 예전 ClusterRole도 지운다 —
`target-cluster-rbac.yaml`은 `dms-remote-inventory`로 이름이 바뀌었고, apply는 예전 이름을 정리하지
않는다:

```bash
kubectl --context <target> delete clusterrolebinding dms-remote-resource-management --ignore-not-found
kubectl --context <target> delete clusterrole        dms-remote-resource-management --ignore-not-found
```

### 4.2 DB 정리 (수동 SQL — 운영자가 직접 실행)

**(a) 필수 — 예전 RM 노드가 대시보드에 영구 stale로 남는 것을 막는다.** `agent_node_current`는 노드별
최신 1행을 보관하는 테이블이고 retention loop는 `agent_reports`(이력)만 prune한다. 따라서
`dms-rm-agent`를 지워도 그 노드 행은 **영원히 남아** 대시보드에 `Stale`로 계속 뜬다:

```sql
-- 운영 DB (DMS_DATABASE_URL)
DELETE FROM agent_node_current WHERE worker_role = 'RM';
```

**(b) 사실상 필수 — 멈춰 있는 RM `run`은 drain/resume을 영구히 막는다.** `resume_blockers()`와
`drain_status().ready_for_shutdown`은 `runs`를 **worker_role 구분 없이** 스캔한다
(`src/dms/query.py`). 따라서 rm-worker가 apply 도중 죽어 남긴 `RecoveryNeeded` /
`UnknownAfterSideEffect` / `BackendApplyFailed` run이 하나라도 있으면, RM이 사라진 뒤에도
`dms-planned-shutdown.sh`는 타임아웃까지 기다리다 `exit 3`으로 끝나고 `:resume`은 계속 blocker를
보고한다. **더 이상 이 run을 해소할 워커가 없으므로** 반드시 정리한다.

먼저 남아 있는지 확인한다:

```sql
SELECT r.run_id, r.state, q.operation
FROM runs r JOIN requests q ON q.request_id = r.request_id
WHERE r.state IN ('RecoveryNeeded','UnknownAfterSideEffect','BackendApplyFailed')
  AND (q.operation LIKE 'filesystem.%' OR q.operation LIKE 'kubernetes.namespace_quota.%');
```

한 건이라도 나오면 아래 (c)를 실행하거나, 이력을 남기고 싶으면 최소한 run만 종료 상태로 옮긴다:

```sql
UPDATE runs SET state = 'Cancelled', updated_at = <now-iso>
WHERE state IN ('RecoveryNeeded','UnknownAfterSideEffect','BackendApplyFailed')
  AND request_id IN (SELECT request_id FROM requests
    WHERE operation LIKE 'filesystem.%' OR operation LIKE 'kubernetes.namespace_quota.%');
```

**(c) 선택 — 남은 RM 행 완전 삭제.** 이력 감사가 필요 없으면 지운다:

```sql
-- 기본 쿼터 정책 테이블(더 이상 쓰이지 않음)
DROP TABLE IF EXISTS default_quota_policies;

-- 레거시 RM operation/resource 행.
-- 주의: results/plans/runs는 모두 requests(request_id)를 FK로 참조하므로 requests를 마지막에
-- 지운다. 그리고 results는 반드시 **request_id 기준**으로 지워야 한다 — planner가 거부한
-- 요청(Conflict/Rejected)과 인증 실패 기록은 plan_id·run_id가 NULL인 results 행을 남기므로
-- run_id 기준으로 지우면 그 행들이 살아남아 마지막 DELETE가 FK 위반으로 실패한다.
DELETE FROM results WHERE request_id IN (
  SELECT request_id FROM requests
  WHERE operation LIKE 'filesystem.%' OR operation LIKE 'kubernetes.namespace_quota.%');
DELETE FROM runs WHERE request_id IN (
  SELECT request_id FROM requests
  WHERE operation LIKE 'filesystem.%' OR operation LIKE 'kubernetes.namespace_quota.%');
DELETE FROM plans WHERE request_id IN (
  SELECT request_id FROM requests
  WHERE operation LIKE 'filesystem.%' OR operation LIKE 'kubernetes.namespace_quota.%');
DELETE FROM state_transitions WHERE request_id IN (
  SELECT request_id FROM requests
  WHERE operation LIKE 'filesystem.%' OR operation LIKE 'kubernetes.namespace_quota.%');
DELETE FROM requests
  WHERE operation LIKE 'filesystem.%' OR operation LIKE 'kubernetes.namespace_quota.%';
DELETE FROM resources
  WHERE resource_kind IN ('filesystem', 'kubernetes_namespace_quota', 'default_quota_policy');
```

> **실행 전 백업.** 어떤 경우든 [../docs/operations-runbook.md](../docs/operations-runbook.md) §8의
> 두 DB 백업을 먼저 뜬다. 위 DELETE는 되돌릴 수 없다. 전체를 **하나의 트랜잭션**으로 감싸면
> 중간 실패 시 반쪽 정리 상태가 남지 않는다.

### 4.3 스토리지 매핑 필드 정리 (선택)

기존 매핑의 `backend_template`에 남아 있는 RM 전용 키(`rm_worker_nodes`·`ssh_host`·`command_runner`·
`quota_scope`·`mutation_mode`·`control_host` 등)는 더 이상 읽히지 않으므로 그대로 둬도 무해하다.
정리하고 싶으면 PATCH로 **전체 `backend_template`을 round-trip**하면서 뺀다
([../docs/api/storage-mappings.md](../docs/api/storage-mappings.md) §5).

## 5. Rollback

이미지 교체 후 문제가 있으면 직전 리비전으로 되돌린다.

```bash
# DMS 코어 (교체한 Deployment 전부)
for d in dms-api dms-planner dms-dm-worker dms-retention dms-sanity-reconciler; do
  kubectl -n dms rollout undo deploy/$d
done
# Portal
kubectl -n dms-portal rollout undo deployment/dms-portal
```

schema를 바꾼 배포의 rollback은 데이터 마이그레이션 역방향을 수반할 수 있으니 반드시
[../docs/operations-runbook.md](../docs/operations-runbook.md) §10을 따른다.

## 참조

- [docker/build-images.sh](docker/build-images.sh) — 이미지 빌드(프록시/캐시/PUSH 옵션)
- [dms-02-core.md](dms-02-core.md) §1 — 코어 이미지 3종 빌드·push (프록시 빌드 포함)
- [portal-01-setup.md](portal-01-setup.md) §5·§6·§7 — 포탈 빌드·manifest·Secret 주입·rollout
- [../docs/operations-runbook.md](../docs/operations-runbook.md) §8 백업 · §9 업그레이드 · §10 rollback
