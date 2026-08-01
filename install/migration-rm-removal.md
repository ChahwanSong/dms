# 일회성 마이그레이션 — RM(Resource Management) 제거 릴리스로 올리기

> **적용 대상**: RM 기능이 아직 있던 릴리스에서 이 릴리스로 **처음** 올라오는 기존 배포.
> 이미 올린 배포에는 해당 없다.
>
> **이 문서의 수명**: RM 이전 버전을 돌리는 배포가 하나도 남지 않으면 **이 파일을 삭제해도 된다.**
> 본문 재배포 절차는 [`redeploy.md`](redeploy.md)에 있다.

RM(파일시스템 프로비저닝 + Kubernetes 네임스페이스 쿼터)이 제거되면서, 기존 배포에는 코드가
더 이상 관리하지 않는 워크로드·DB 행·매핑 필드가 남는다. 아래를 **한 번** 수동으로 정리한다.
**DMS는 이 정리를 자동으로 하지 않는다** — 특히 DB 삭제는 되돌릴 수 없으므로 운영자가
의도적으로 실행해야 한다.

> **순서 — 이 정리를 [`redeploy.md`](redeploy.md)의 이미지 교체(§2 코어 · §3 포탈)보다 먼저 한다.**
> - **아래 §1을 `redeploy.md` §2보다 먼저**: `redeploy.md` §2.1의 `grep -E "/dms:"` 는 아직 살아 있는
>   `dms-rm-worker`도 잡아내고, 새 이미지에는 `dms rm-worker` 서브커맨드가 없어 그대로 교체하면
>   CrashLoopBackOff에 빠진다.
> - **dms-api를 포탈보다 먼저**: 포탈 BFF는 스토리지 매핑 쓰기를 `/api/v1/storage-mappings`
>   (신규 경로)로 호출한다. 포탈을 먼저 올리면 옛 dms-api가 그 경로를 모르므로 스토리지 인벤토리의
>   생성·수정·삭제·재검사가 **404**가 된다(읽기는 `/operations/storage-mappings`라 영향 없음).
>   반대 순서(dms-api 먼저)는 안전하다 — 옛 포탈이 호출하던 `/api/v1/resource-management/...`가
>   404가 되지만, 이는 포탈을 올리는 짧은 구간에만 해당한다. 두 이미지를 연달아 교체한다.
> - 관리 대상 클러스터에 별도로 띄운 `dms-rm-worker-local`이 있으면 그것도 지운다. 남아 있으면
>   `recovery-sweeper` 리더 리스를 계속 잡아 dm-worker의 복구 스윕을 굶긴다:
>   `kubectl --context <managed> -n <ns> delete deploy/dms-rm-worker-local --ignore-not-found`

## 1. 워크로드 제거 (필수)

새 매니페스트에는 이 오브젝트들이 더 이상 없고, `kubectl apply`는 사라진 오브젝트를 지우지 않는다:

```bash
kubectl -n dms delete deployment/dms-rm-worker --ignore-not-found
kubectl -n dms delete daemonset/dms-rm-agent  --ignore-not-found
kubectl -n dms delete serviceaccount/dms-rm-worker --ignore-not-found
```

> **`dms-ssh-client` Secret은 여기서 지우지 않는다.** RM host-exec 전용이 아니다 —
> **업그레이드 전 파드 스펙**의 `dms-api`·`dms-api-internal`·`dms-sanity-reconciler`가 이 Secret을
> 마운트하고 있고(현재 매니페스트에는 없다),
> **`DMS_CLUSTER_CONTROL_HOSTS_JSON`에 등록된 클러스터는 `DMS_KUBERNETES_INVENTORY_MODE`와
> 무관하게** `ssh <host> kubectl`로 읽히기 때문이다(`src/dms/adapters/inventory.py`의
> per-cluster transport). 이를 먼저 지우면 다음 두 가지가 동시에 발생한다:
>
> 1. **파드가 기동 불가.** 아직 옛 파드 스펙(`prepare-ssh-client` initContainer + `ssh-client`
>    볼륨)을 가진 `dms-api`/`dms-sanity-reconciler`가 `MountVolume.SetUp failed ... secret
>    "dms-ssh-client" not found`로 멈춘다.
> 2. **`kubectl rollout status`가 성공이라고 거짓 보고.** 새 파드가 Pending이어도 **옛 파드가
>    가용성을 채우고 있으면** rollout이 완료로 뜬다. 그 사이 옛 이미지 파드가 계속 돌면서
>    `missing_rm_readiness` 같은 옛 데이터를 다시 써 넣는다(sanity-reconciler에서 발생).
>
> **올바른 순서:** ① 새 매니페스트(`control-plane.yaml`·`sanity-reconciler.yaml`)를 먼저 반영해
> 파드 스펙에서 ssh 마운트를 없애고 → ② `DMS_CLUSTER_CONTROL_HOSTS_JSON`이 비어 있고
> `DMS_KUBERNETES_INVENTORY_MODE`가 `ssh-kubectl`이 아님을 확인한 뒤 → ③ 그때 Secret을 지운다.
> 둘 중 하나라도 SSH를 쓰고 있으면 **Secret을 유지**한다.
>
> 참고: kubeconfig로 직접 도달 가능한 클러스터라면 `DMS_CLUSTER_CONTROL_HOSTS_JSON`에서 빼는
> 편이 낫다. SSH 의존이 사라지고 인벤토리는 kubeconfig로 그대로 동작한다.

**롤아웃 검증은 `rollout status`만 믿지 말고 실제 파드 이미지로 확인한다:**

```bash
kubectl -n dms get pods -o json | python3 -c "
import json,sys
bad=[(p['metadata']['name'], c['image'])
     for p in json.load(sys.stdin)['items']
     for c in p['spec']['containers'] + p['spec'].get('initContainers',[])
     if '/dms' in c['image'] and '<새태그>' not in c['image'] and 'mpifileutils' not in c['image']]
print('구버전 이미지 파드:', bad or '없음')"

# 옛 ReplicaSet이 replicas>0으로 남아 있는지도 본다 (남아 있으면 옛 파드가 계속 돈다)
kubectl -n dms get rs -o custom-columns='NAME:.metadata.name,DESIRED:.spec.replicas,IMAGE:.spec.template.spec.containers[0].image' | awk '$2>0'
```

대상 클러스터마다(멀티 클러스터면 전부) 이름이 바뀌면서 고아가 된 예전 ClusterRole도 지운다 —
`target-cluster-rbac.yaml`은 `dms-remote-inventory`로 이름이 바뀌었고, apply는 예전 이름을 정리하지
않는다:

```bash
kubectl --context <target> delete clusterrolebinding dms-remote-resource-management --ignore-not-found
kubectl --context <target> delete clusterrole        dms-remote-resource-management --ignore-not-found
```

## 2. DB 정리 (수동 SQL — 운영자가 직접 실행)

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

> `default_quota_policies` 테이블은 여기 없다 — `dms migrate`가 자동으로 DROP한다
> (`identity_mappings`와 동일한 처리). 아래는 이력 행만 다룬다.

```sql
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

## 3. 스토리지 매핑 필드 정리 (선택)

기존 매핑의 `backend_template`에 남아 있는 RM 전용 키(`rm_worker_nodes`·`ssh_host`·`command_runner`·
`command_timeout_seconds`·`quota_scope`·`fileset_name_template`·`mutation_mode`·`control_host`·
`weka_profile`·`weka_credentials`)는 더 이상 읽히지 않는다.

**`weka_credentials`는 수동 정리가 필요 없다** — `dms migrate`가 모든 `backend_template`에서
자동으로 제거한다(idempotent, §2의 migrate Job에 포함). 평문 자격증명이 DB에 남는 문제라 운영자가
SQL을 기억하는 데 맡기지 않는다.

나머지 키는 그대로 둬도 무해하다. 굳이 지우려면 PATCH로 **전체 `backend_template`을 round-trip**
하면서 뺀다([../docs/api/storage-mappings.md](../docs/api/storage-mappings.md) §5).

## 4. 스토리지 매핑 sanity 재검사 (권장)

`readiness`는 **마지막 검사 결과가 저장된 값**이다. 업그레이드 직후 기존 매핑은 여전히 옛 축
(`resource_management`·`kubernetes_mutation`)과 옛 `missing_rm_readiness` 경고를 들고 있고,
그 경고 때문에 파일시스템 매핑이 `Degraded`로 남는다. sanity-reconciler가 다음 스윕에서 정리하지만,
바로 반영하려면 전 매핑을 재검사한다:

```bash
for s in $(curl -sS "${CURL_MTLS[@]}" "$DMS_API_URL/api/v1/operations/storage-mappings" | jq -r '.[].storage_name'); do
  curl -sS "${CURL_MTLS[@]}" -X POST "$DMS_API_URL/api/v1/storage-mappings/${s}:check" | jq -c '{storage_name, status}'
done
```

정리 후에는 `readiness`가 `data_management`·`inventory` **두 축만** 남아야 한다. RM 경고만이
원인이던 매핑은 이 재검사로 `Degraded` → `Ready`가 된다.
