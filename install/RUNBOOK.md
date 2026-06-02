# DMS 운영 런북

## 설치 직후 첫 점검

Control cluster에서 DMS workload 상태를 확인한다.

```bash
kubectl --context dms-control -n dms get pods,jobs,svc,ingress
kubectl --context dms-control -n dms wait --for=condition=complete job/dms-migrate --timeout=180s
kubectl --context dms-control -n dms rollout status deploy/dms-api --timeout=180s
kubectl --context dms-control -n dms rollout status deploy/dms-planner --timeout=180s
kubectl --context dms-control -n dms rollout status deploy/dms-rm-worker --timeout=180s
```

문제가 있으면 바로 describe/log를 본다.

```bash
kubectl --context dms-control -n dms describe pod -l app.kubernetes.io/name=dms-api
kubectl --context dms-control -n dms logs deploy/dms-api --tail=200
kubectl --context dms-control -n dms logs job/dms-migrate --tail=200
```

Target cluster RBAC와 kubeconfig 권한을 확인한다.

```bash
KUBECONFIG=/tmp/dms-kubeconfigs/cluster-a.kubeconfig kubectl get nodes
KUBECONFIG=/tmp/dms-kubeconfigs/cluster-a.kubeconfig kubectl get storageclass
KUBECONFIG=/tmp/dms-kubeconfigs/cluster-a.kubeconfig kubectl auth can-i create resourcequotas --all-namespaces
KUBECONFIG=/tmp/dms-kubeconfigs/cluster-a.kubeconfig kubectl auth can-i patch namespaces
```

`no`가 나오면 `install/kubernetes/target-cluster-rbac.yaml` 적용 여부와 ServiceAccount token을 다시 확인한다.

## 일일 Health Check

DMS API에 접근할 수 있는 operator workstation에서 실행한다.

```bash
export DMS_API_URL="https://dms.example.internal"
export DMS_TOKEN="replace-with-secret"
export DMS_CLIENT_CERT="client.crt"
export DMS_CLIENT_KEY="client.key"
export DMS_CA_CERT="dms-api-ca.crt"

curl_dms() {
  curl -fsS --cert "$DMS_CLIENT_CERT" --key "$DMS_CLIENT_KEY" --cacert "$DMS_CA_CERT" "$@"
}

install/scripts/verify-install.sh
```

운영 mTLS profile에서는 `DMS_ACTOR`를 설정하지 않는다. DMS API는 ingress/edge proxy가 검증한 client certificate subject에서 actor를 derive한다.

인증 경계를 직접 확인한다.

```bash
curl -v "$DMS_API_URL/api/v1/operations/action-required" \
  --cert "$DMS_CLIENT_CERT" \
  --key "$DMS_CLIENT_KEY" \
  --cacert "$DMS_CA_CERT" \
  -H "authorization: Bearer $DMS_TOKEN"
```

정상이라면 HTTP 200과 JSON 배열이 나온다. client certificate 없이 같은 endpoint를 호출하면 실패해야 한다.

```bash
curl -v "$DMS_API_URL/api/v1/operations/action-required" \
  --cacert "$DMS_CA_CERT" \
  -H "authorization: Bearer $DMS_TOKEN"
```

수동 확인:

```bash
kubectl -n dms get pods,jobs,svc
kubectl -n dms logs deploy/dms-planner --tail=100
kubectl -n dms logs deploy/dms-rm-worker --tail=100
curl_dms "$DMS_API_URL/api/v1/operations/action-required" \
  -H "authorization: Bearer $DMS_TOKEN"
```

정상 steady state:

- `dms-api` 사용 가능.
- `dms-planner` 실행 중.
- `dms-rm-worker` 실행 중.
- Live DM execution이 구현될 때까지 `dms-dm-worker`는 0으로 scale되어 있다.
- Storage-capable RM/DM node마다 agent report가 fresh 상태다.
- 운영 request가 사용하는 storage mapping은 `readiness.resource_management=Ready`를 표시한다.
- 해결되지 않은 action-required 항목이 없다.

## mTLS/API 인증 문제

증상별 확인:

| 증상 | 확인할 것 |
| --- | --- |
| TLS handshake 실패 | client cert가 `dms-client-ca`로 발급됐는지, ingress Secret 이름이 맞는지 확인 |
| HTTP 401 `invalid token` | `DMS_TOKEN`과 `DMS_AUTH_SHARED_TOKEN`이 같은지 확인 |
| HTTP 401 `mtls_subject_required` | ingress가 upstream evidence header를 전달하는지 확인 |
| HTTP 401 `mtls_verify_failed` | ingress verify result가 `SUCCESS`인지 확인 |
| API Pod startup 실패 | `DMS_DEFAULT_ACTOR`가 비어 있는지, mTLS env 조합이 맞는지 확인 |

Ingress annotation 확인:

```bash
kubectl --context dms-control -n dms get ingress dms-api -o yaml
```

필수 annotation:

```yaml
nginx.ingress.kubernetes.io/auth-tls-secret: dms/dms-client-ca
nginx.ingress.kubernetes.io/auth-tls-verify-client: "on"
nginx.ingress.kubernetes.io/auth-tls-pass-certificate-to-upstream: "true"
```

API direct access가 열려 있으면 mTLS evidence header spoofing 위험이 있다. NetworkPolicy를 확인한다.

```bash
kubectl --context dms-control -n dms get networkpolicy dms-api-from-ingress-only -o yaml
```

## Storage Mapping Readiness

요청이 `storage_mapping_sanity`로 거절되면 mapping state를 확인한다.

```bash
curl_dms "$DMS_API_URL/api/v1/operations/storage-mappings" \
  -H "authorization: Bearer $DMS_TOKEN"
```

Mapping 하나를 refresh한다.

```bash
curl_dms -X POST "$DMS_API_URL/api/v1/resource-management/storage-mappings/<storage_name>:check" \
  -H "authorization: Bearer $DMS_TOKEN"
```

흔한 원인:

- Mount를 볼 수 있는 node에서 Agent DaemonSet이 실행 중이 아니다.
- `DMS_AGENT_CLUSTER_NAME`이 storage mapping의 `cluster_name`과 일치하지 않는다.
- `storage_class_name` 또는 `csi_driver`가 live StorageClass와 일치하지 않는다.
- Agent report가 stale 상태다. `DMS_AGENT_REPORT_STALE_SECONDS`를 확인한다.
- Kubernetes inventory mode가 target cluster를 읽을 수 없다.

Mapping check를 수동으로 다시 실행한다.

```bash
curl_dms -X POST "$DMS_API_URL/api/v1/resource-management/storage-mappings/<storage_name>:check" \
  -H "authorization: Bearer $DMS_TOKEN"
```

Agent report freshness도 같이 확인한다.

```bash
curl_dms "$DMS_API_URL/api/v1/operations/agent-reports" \
  -H "authorization: Bearer $DMS_TOKEN"

curl_dms "$DMS_API_URL/api/v1/operations/worker-agent-health" \
  -H "authorization: Bearer $DMS_TOKEN"
```

## Kubernetes Namespace Quota Incident

Namespace quota 하나를 check한다.

```bash
curl_dms -X POST "$DMS_API_URL/api/v1/resource-management/kubernetes/namespace-quotas/<cluster>/<namespace>:check" \
  -H "authorization: Bearer $DMS_TOKEN" \
  -H "content-type: application/json" \
  --data '{"requester_id":"operator","payload":{"include_effective_quota":true}}'
```

Scope를 audit한다.

```bash
curl_dms -X POST "$DMS_API_URL/api/v1/resource-management/kubernetes/namespace-quotas:audit" \
  -H "authorization: Bearer $DMS_TOKEN" \
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

Target cluster live object 확인:

```bash
kubectl --context <target-context> -n <namespace> get resourcequota dms-storage-quota -o yaml
kubectl --context <target-context> -n <namespace> describe resourcequota dms-storage-quota
```

Planner/RM Worker 처리 흐름 확인:

```bash
curl_dms "$DMS_API_URL/api/v1/operations/requests?requester_id=<requester>&limit=20" \
  -H "authorization: Bearer $DMS_TOKEN"

kubectl --context dms-control -n dms logs deploy/dms-planner --tail=200
kubectl --context dms-control -n dms logs deploy/dms-rm-worker --tail=200
```

## Expiry 처리

만료된 filesystem resource를 나열한다.

```bash
curl_dms "$DMS_API_URL/api/v1/operations/filesystems/expiring?status=expired" \
  -H "authorization: Bearer $DMS_TOKEN"
```

만료된 Kubernetes namespace quota resource를 나열한다.

```bash
curl_dms "$DMS_API_URL/api/v1/operations/kubernetes/namespace-quotas/expiring?status=expired" \
  -H "authorization: Bearer $DMS_TOKEN"
```

Dry-run sweep:

```bash
curl_dms -X POST "$DMS_API_URL/api/v1/resource-management/kubernetes/namespace-quotas:expiration-sweep" \
  -H "authorization: Bearer $DMS_TOKEN" \
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

실제 sweep:

```bash
curl_dms -X POST "$DMS_API_URL/api/v1/resource-management/kubernetes/namespace-quotas:expiration-sweep" \
  -H "authorization: Bearer $DMS_TOKEN" \
  -H "content-type: application/json" \
  --data '{
    "requester_id": "operator",
    "payload": {
      "dry_run": false,
      "action": "block",
      "scope": {"cluster_name": "cluster-a"},
      "max_targets": 20
    }
  }'
```

Filesystem expiration sweep도 같은 원칙으로 dry-run 후 실행한다.

```bash
curl_dms -X POST "$DMS_API_URL/api/v1/resource-management/filesystems:expiration-sweep" \
  -H "authorization: Bearer $DMS_TOKEN" \
  -H "content-type: application/json" \
  --data '{
    "requester_id": "operator",
    "payload": {
      "dry_run": true,
      "action": "block",
      "storage_name": "cephfs-a",
      "max_targets": 20
    }
  }'
```

## Worker Recovery

Stale run을 확인한다.

```bash
curl_dms "$DMS_API_URL/api/v1/operations/runs/stale" \
  -H "authorization: Bearer $DMS_TOKEN"
```

현재 RM worker는 stale run을 표시해서 다른 worker가 planning/execution을 계속할 수 있게 한다. 다만 long-running backend call은 side effect가 발생했을 수 있으므로 operator 검토가 여전히 필요하다. 요청을 다시 제출하기 전에 `action-required`와 backend live state를 확인한다.

Worker를 안전하게 재시작하는 기본 순서:

```bash
kubectl --context dms-control -n dms scale deploy/dms-rm-worker --replicas=0
curl_dms "$DMS_API_URL/api/v1/operations/runs/stale" \
  -H "authorization: Bearer $DMS_TOKEN"
kubectl --context dms-control -n dms scale deploy/dms-rm-worker --replicas=1
kubectl --context dms-control -n dms rollout status deploy/dms-rm-worker --timeout=180s
```

Active backend mutation 중에는 무작정 scale-down하지 않는다. 먼저 action-required, target live state, 최근 logs를 확인한다.

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

명령 예시:

```bash
kubectl --context dms-control -n dms scale deploy/dms-planner --replicas=0
kubectl --context dms-control -n dms scale deploy/dms-rm-worker --replicas=0

pg_dump "$DMS_DATABASE_URL" > dms-operational-$(date +%Y%m%d%H%M%S).sql
pg_dump "$DMS_OBSERVABILITY_DATABASE_URL" > dms-observability-$(date +%Y%m%d%H%M%S).sql

# /tmp/dms-control-plane.yaml의 image 값을 먼저 $NEW_DMS_IMAGE로 바꾼다.
kubectl --context dms-control -n dms delete job dms-migrate --ignore-not-found=true
kubectl --context dms-control apply -f /tmp/dms-control-plane.yaml
kubectl --context dms-control -n dms wait --for=condition=complete job/dms-migrate --timeout=180s

kubectl --context dms-control -n dms set image deploy/dms-api api="$NEW_DMS_IMAGE"
kubectl --context dms-control -n dms set image deploy/dms-planner planner="$NEW_DMS_IMAGE"
kubectl --context dms-control -n dms set image deploy/dms-rm-worker rm-worker="$NEW_DMS_IMAGE"

kubectl --context dms-control -n dms scale deploy/dms-planner --replicas=1
kubectl --context dms-control -n dms scale deploy/dms-rm-worker --replicas=1
install/scripts/verify-install.sh
```

`job/dms-migrate`는 immutable field가 많으므로 새 image로 바꿀 때 기존 Job을 삭제하고 manifest로 다시 만드는 편이 단순하다.

## Rollback

새 image rollout 후 문제가 있으면 직전 image로 되돌린다.

```bash
kubectl --context dms-control -n dms rollout undo deploy/dms-api
kubectl --context dms-control -n dms rollout undo deploy/dms-planner
kubectl --context dms-control -n dms rollout undo deploy/dms-rm-worker
kubectl --context dms-control -n dms rollout status deploy/dms-api --timeout=180s
kubectl --context dms-control -n dms rollout status deploy/dms-planner --timeout=180s
kubectl --context dms-control -n dms rollout status deploy/dms-rm-worker --timeout=180s
```

Schema migration이 이미 실행된 뒤라면 image rollback만으로 충분하지 않을 수 있다. schema-changing release는 staging DB restore로 먼저 검증한다.

Maintenance/drain control state는 DB model에 존재하지만 full runtime enforcement는 최신 design보다 늦을 수 있다. 운영 drain mechanism으로 Kubernetes scaling과 ingress control을 사용한다.

## 알려진 운영 공백

- Non-stub Volcano adapter가 설치되고 검증될 때까지 Data Management live execution은 disabled 상태로 유지해야 한다.
- 운영 Helm/Kustomize packaging은 아직 완성되지 않았다. 여기 있는 manifest는 명시적 YAML template이다.
- `DMS_WORKER_LEASE_SECONDS`를 초과할 것으로 예상되는 operation을 실행하기 전에 매우 긴 backend call에 대한 worker lease renewal을 검토해야 한다.
- 서로 다른 mount를 가진 multiple local filesystem RM worker는 storage-aware worker claiming 없이는 안전하지 않다.
- WEKA filesystem backend는 아직 구현되지 않았다.
