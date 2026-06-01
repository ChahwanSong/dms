# DMS 운영 런북

## 일일 Health Check

DMS API에 접근할 수 있는 operator workstation에서 실행한다.

```bash
export DMS_API_URL="https://dms.example.internal"
export DMS_TOKEN="replace-with-secret"
export DMS_ACTOR="operator@example.internal"

install/scripts/verify-install.sh
```

수동 확인:

```bash
kubectl -n dms get pods,jobs,svc
kubectl -n dms logs deploy/dms-planner --tail=100
kubectl -n dms logs deploy/dms-rm-worker --tail=100
curl -fsS "$DMS_API_URL/api/v1/operations/action-required" \
  -H "authorization: Bearer $DMS_TOKEN" \
  -H "x-dms-actor: $DMS_ACTOR"
```

정상 steady state:

- `dms-api` 사용 가능.
- `dms-planner` 실행 중.
- `dms-rm-worker` 실행 중.
- Live DM execution이 구현될 때까지 `dms-dm-worker`는 0으로 scale되어 있다.
- Storage-capable RM/DM node마다 agent report가 fresh 상태다.
- 운영 request가 사용하는 storage mapping은 `readiness.resource_management=Ready`를 표시한다.
- 해결되지 않은 action-required 항목이 없다.

## Storage Mapping Readiness

요청이 `storage_mapping_sanity`로 거절되면 mapping state를 확인한다.

```bash
curl -fsS "$DMS_API_URL/api/v1/operations/storage-mappings" \
  -H "authorization: Bearer $DMS_TOKEN" \
  -H "x-dms-actor: $DMS_ACTOR"
```

Mapping 하나를 refresh한다.

```bash
curl -fsS -X POST "$DMS_API_URL/api/v1/resource-management/storage-mappings/<storage_name>:check" \
  -H "authorization: Bearer $DMS_TOKEN" \
  -H "x-dms-actor: $DMS_ACTOR"
```

흔한 원인:

- Mount를 볼 수 있는 node에서 Agent DaemonSet이 실행 중이 아니다.
- `DMS_AGENT_CLUSTER_NAME`이 storage mapping의 `cluster_name`과 일치하지 않는다.
- `storage_class_name` 또는 `csi_driver`가 live StorageClass와 일치하지 않는다.
- Agent report가 stale 상태다. `DMS_AGENT_REPORT_STALE_SECONDS`를 확인한다.
- Kubernetes inventory mode가 target cluster를 읽을 수 없다.

## Kubernetes Namespace Quota Incident

Namespace quota 하나를 check한다.

```bash
curl -fsS -X POST "$DMS_API_URL/api/v1/resource-management/kubernetes/namespace-quotas/<cluster>/<namespace>:check" \
  -H "authorization: Bearer $DMS_TOKEN" \
  -H "x-dms-actor: $DMS_ACTOR" \
  -H "content-type: application/json" \
  --data '{"requester_id":"operator","payload":{"include_effective_quota":true}}'
```

Scope를 audit한다.

```bash
curl -fsS -X POST "$DMS_API_URL/api/v1/resource-management/kubernetes/namespace-quotas:audit" \
  -H "authorization: Bearer $DMS_TOKEN" \
  -H "x-dms-actor: $DMS_ACTOR" \
  -H "content-type: application/json" \
  --data '{
    "requester_id": "operator",
    "payload": {
      "scope": {"cluster_name": "cluster-a"},
      "include_non_dms": true,
      "include_usage_pressure": true
    }
  }'
```

Live ResourceQuota가 없지만 DB desired state가 있으면 update 또는 block/unblock으로 다시 적용한다. Live state가 operator에 의해 의도적으로 변경된 경우에는 drift를 검토한 뒤에만 sync를 실행한다.

## Expiry 처리

만료된 filesystem resource를 나열한다.

```bash
curl -fsS "$DMS_API_URL/api/v1/operations/filesystems/expiring?status=expired" \
  -H "authorization: Bearer $DMS_TOKEN" \
  -H "x-dms-actor: $DMS_ACTOR"
```

만료된 Kubernetes namespace quota resource를 나열한다.

```bash
curl -fsS "$DMS_API_URL/api/v1/operations/kubernetes/namespace-quotas/expiring?status=expired" \
  -H "authorization: Bearer $DMS_TOKEN" \
  -H "x-dms-actor: $DMS_ACTOR"
```

Dry-run sweep:

```bash
curl -fsS -X POST "$DMS_API_URL/api/v1/resource-management/kubernetes/namespace-quotas:expiration-sweep" \
  -H "authorization: Bearer $DMS_TOKEN" \
  -H "x-dms-actor: $DMS_ACTOR" \
  -H "content-type: application/json" \
  --data '{
    "requester_id": "operator",
    "payload": {
      "dry_run": true,
      "action": "block",
      "scope": {"cluster_name": "cluster-a"},
      "max_targets": 100
    }
  }'
```

실제 sweep은 dry-run target을 검토한 뒤에만 실행한다. System/admin resource는 policy에 의해 skip된다.

## Worker Recovery

Stale run을 확인한다.

```bash
curl -fsS "$DMS_API_URL/api/v1/operations/runs/stale" \
  -H "authorization: Bearer $DMS_TOKEN" \
  -H "x-dms-actor: $DMS_ACTOR"
```

현재 RM worker는 stale run을 표시해서 다른 worker가 planning/execution을 계속할 수 있게 한다. 다만 long-running backend call은 side effect가 발생했을 수 있으므로 operator 검토가 여전히 필요하다. 요청을 다시 제출하기 전에 `action-required`와 backend live state를 확인한다.

## PostgreSQL Backup

업그레이드 전과 schema-changing code를 적용하기 전에 두 database를 모두 백업한다.

```bash
pg_dump "$DMS_DATABASE_URL" > dms-operational-$(date +%Y%m%d%H%M%S).sql
pg_dump "$DMS_OBSERVABILITY_DATABASE_URL" > dms-observability-$(date +%Y%m%d%H%M%S).sql
```

운영 rollout 전에 staging DB에 먼저 restore하고 새 image로 `dms migrate`를 실행한다.

## 업그레이드 절차

1. 가능하면 ingress에서 새 external write를 중지한다.
2. `dms-planner`와 `dms-rm-worker`를 0으로 scale한다.
3. Active RM run이 완료되거나 stale/action-required가 될 때까지 기다린다.
4. PostgreSQL을 backup한다.
5. 새 image를 `dms-migrate` Job에 적용하고 실행한다.
6. `dms-api`를 roll한다.
7. `dms-planner`와 `dms-rm-worker`를 roll한다.
8. `install/scripts/verify-install.sh`를 실행한다.
9. External write를 다시 활성화한다.

Maintenance/drain control state는 DB model에 존재하지만 full runtime enforcement는 최신 design보다 늦을 수 있다. 운영 drain mechanism으로 Kubernetes scaling과 ingress control을 사용한다.

## 알려진 운영 공백

- Non-stub Volcano adapter가 설치되고 검증될 때까지 Data Management live execution은 disabled 상태로 유지해야 한다.
- 운영 Helm/Kustomize packaging은 아직 완성되지 않았다. 여기 있는 manifest는 명시적 YAML template이다.
- `DMS_WORKER_LEASE_SECONDS`를 초과할 것으로 예상되는 operation을 실행하기 전에 매우 긴 backend call에 대한 worker lease renewal을 검토해야 한다.
- 서로 다른 mount를 가진 multiple local filesystem RM worker는 storage-aware worker claiming 없이는 안전하지 않다.
- WEKA filesystem backend는 아직 구현되지 않았다.
