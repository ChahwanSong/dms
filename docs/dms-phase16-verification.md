# DMS Phase 16 Verification

Date: 2026-06-02 19:13 +0900

Phase 16 verifies external API mTLS validation and auth boundary hardening from `docs/dms-phase16.md`. The live verifier also re-runs the existing long-running Resource Management and expiry lifecycle coverage from earlier phases by chaining the Phase 15 verifier, which itself chains the Phase 13 long-running runtime verifier.

## Testbed

Source metadata:

- `/home/mason/workspace/testbed/testbed-summary.json`
- `/home/mason/workspace/testbed/testbed-info.json`
- `/home/mason/workspace/testbed/TOPOLOGY.md`

Relevant testbed state:

- Kubernetes: v1.34.6 on independent `cluster-a` and `cluster-b`.
- CNI: Cilium 1.19.3.
- PostgreSQL: `192.168.56.11:30432`.
- OpenLDAP/SSSD: `ldap://ldap.testbed.local`, SSSD on Kubernetes nodes.
- CephFS host mounts: `cluster-a/c1-worker:/mnt/testbed-cephfs`, `cluster-b/c2-worker:/mnt/testbed-cephfs-c2`.
- Longhorn: `cluster-b/testbed-longhorn`.

## Commands

Local regression:

```bash
cd /home/mason/workspace/dms
python3 -m py_compile src/dms/auth.py src/dms/config.py scripts/phase16_mtls_auth.py tests/test_phase16_mtls_auth.py
bash -n scripts/verify-phase16-testbed.sh scripts/verify-phase15-testbed.sh scripts/verify-phase14-testbed.sh scripts/verify-phase13-testbed.sh install/scripts/register-storage-mappings.sh install/scripts/register-default-quota-policies.sh install/scripts/register-identity-mappings.sh install/scripts/verify-install.sh
pytest -q
git diff --check
```

Testbed live verification:

```bash
cd /home/mason/workspace/dms
./scripts/verify-phase16-testbed.sh
```

The run built and copied `testbed-registry:5000/dms:phase16` into the local testbed registry. Docker push to the HTTP registry failed in the expected way and the verifier used the existing `docker save` plus `skopeo copy` fallback.

## Local Results

```text
py_compile: passed
bash -n: passed
pytest -q tests/test_phase16_mtls_auth.py: 26 passed in 13.14s
pytest -q: 128 passed in 77.15s (0:01:17)
git diff --check: passed
```

The Phase 16 local tests cover:

- existing dev/test `x-dms-actor` behavior when mTLS is not required
- shared bearer token accept/reject behavior
- missing mTLS evidence rejection without operational request creation
- DMS edge proxy header family
- ingress-nginx header family
- mTLS verify result `SUCCESS` requirement
- conflicting mTLS header families
- default actor ignored in mTLS-required mode
- mismatched and matching `x-dms-actor` behavior
- auth rejection safe-write behavior when observability writes fail
- `/healthz` unauthenticated behavior
- agent report and operational query auth boundary coverage
- invalid `DMS_REQUIRE_MTLS_VERIFIED_HEADER=true` startup configuration
- invalid `DMS_DEFAULT_ACTOR` with `DMS_REQUIRE_MTLS_HEADER=true` startup configuration
- all 50 protected API endpoints reach their route handler with
  `DMS_REQUIRE_MTLS_HEADER=true`, `DMS_REQUIRE_MTLS_VERIFIED_HEADER=true`, and a
  valid bearer token
- `/api/v1/data-management/help` is currently unauthenticated, like `/healthz`
- `/api/v1/agent/reports` passes mTLS authentication but returns node identity
  mismatch with the default `mtls:<subject>` actor unless agent certificate
  subject-to-node actor mapping is configured or implemented

## Testbed Results

Fresh PostgreSQL databases:

- operational: `dms_phase13_phase16_20260602190603`
- observability: `dms_phase13_obs_phase16_20260602190603`

The verifier first ran the Phase 13 long-running runtime flow on the Phase 16 image and completed with `status: ok`.

Phase 13 evidence included:

- API, Planner, RM Worker, and Agent DaemonSet deployed on the testbed.
- Phase 12 CephFS quota/import/check/sync flows processed through deployed long-running Planner/RM Worker.
- Actual CephFS capacity and file count quota enforcement returned `Disk quota exceeded`.
- RM Worker 2-replica scale-out used two worker ids.
- RM Worker Pod restart was exercised.
- stale claim query returned one `StaleClaim` fixture.
- GPFS live verification was explicitly skipped because the testbed has no IBM GPFS / IBM Storage Scale cluster.

The verifier then ran the Phase 15 resource expiry flow and completed with `status: ok`.

Phase 15 summary:

```json
{
  "filesystem": {
    "create_request": "req_cbf30253a24548ae928fd488ae87cbd6",
    "expires_at": "2026-09-30T10:09:48.544028+00:00",
    "resource_key": "cephfs-a:phase15-fs-00abaa34",
    "update_request": "req_774fe43411e24acfb4a8f9de8595064f"
  },
  "kubernetes": {
    "create_request": "req_0ca4efbd285344dbb10b2ce62f77abab",
    "expired_query_count": 1,
    "import_request": "req_208ba2af08854970a9d5383d1d81a617",
    "sweep_request": "req_d52f8053e4964ef194e6a8aad44e7d71",
    "update_request": "req_ff2f5d17c1a74677a67e2876fdd1bf05"
  },
  "status": "ok"
}
```

Phase 16 then switched the deployed API to:

```text
DMS_REQUIRE_MTLS_HEADER=true
DMS_REQUIRE_MTLS_VERIFIED_HEADER=true
DMS_MTLS_ACTOR_PREFIX=mtls:
DMS_DEFAULT_ACTOR removed
```

The same runtime contract is reflected in `install/config/dms-runtime.env.example`,
`install/kubernetes/control-plane.yaml`, `install/kubernetes/ingress.example.yaml`,
and the install helper scripts. Production callers should use a client
certificate, client private key, API server CA, and bearer token. They should
not set `DMS_ACTOR` unless they intentionally send the exact actor derived from
the mTLS subject.

A short-lived testbed mTLS edge proxy was deployed in the DMS namespace. The proxy required a client certificate signed by the Phase 16 test CA, stripped client-provided mTLS evidence headers, injected `X-DMS-Client-Cert-Subject`, injected `X-DMS-Client-Cert-Verify: SUCCESS`, and forwarded requests to `dms-api`.

The current verifier also pauses Planner and RM Worker after the Phase 13/15
live flows are complete, then runs an auth-only protected endpoint matrix through
the mTLS edge proxy. This avoids backend side effects while verifying that the
external mTLS/token boundary reaches each protected DMS API route.

Phase 16 output summary:

```json
{
  "accepted_actor": "mtls:CN=phase16-client,O=testbed",
  "accepted_request_id": "req_187f99d9fa7d4108ba3c89a17f35b7b4",
  "authentication_rejected_events": 4,
  "bad_client_certificate": "[SSL: TLSV1_ALERT_UNKNOWN_CA] tlsv1 alert unknown ca (_ssl.c:2559)",
  "direct_spoof_pod_output": "{\"blocked\": true, \"error\": \"<urlopen error timed out>\"}",
  "missing_client_certificate": "[SSL: TLSV13_ALERT_CERTIFICATE_REQUIRED] tlsv13 alert certificate required (_ssl.c:2559)",
  "status": "ok"
}
```

Verified live behavior:

- Request without client certificate failed TLS handshake.
- Request with untrusted client certificate failed TLS handshake.
- Valid client certificate without bearer token returned 401.
- Valid client certificate with wrong bearer token returned 401.
- Valid client certificate with correct bearer token accepted a Resource Management request.
- Accepted request actor was derived from client certificate subject: `mtls:CN=phase16-client,O=testbed`.
- Conflicting `x-dms-actor` with valid mTLS evidence returned 401.
- Direct `FAILED` mTLS verify evidence was rejected by the API.
- In-cluster direct spoof of trusted evidence headers from a non-proxy Pod was blocked by NetworkPolicy.
- The mTLS proxy remained allowed after NetworkPolicy.
- `/healthz` remained available through the protected mTLS edge.
- Observability recorded `authentication_rejected` diagnostics without token value or raw certificate material.

## Latest All-endpoint mTLS Rerun Attempt

The verifier was updated to run all protected API endpoint checks through the
testbed mTLS proxy after the Phase 13/15 live flows finish. The latest local
endpoint matrix passed, but the live all-endpoint matrix did not complete yet.

Rerun attempts on 2026-06-02:

- First rerun reached the Phase 16 mTLS matrix and failed because the temporary
  Python mTLS proxy fixture did not forward `PUT`; `PUT` and `DELETE` forwarding
  were added to `scripts/verify-phase16-testbed.sh`.
- Second rerun failed before reaching the Phase 16 matrix, inside the chained
  Phase 13 CephFS quota create verification. The failure was
  `UnknownAfterSideEffect` with `allowed user access failed for alice:
  Permission denied` on a newly created CephFS directory.
- The second failure is not an mTLS authentication failure. It occurred in the
  older Phase 13 backend permission check that runs before Phase 16.
- Phase-scoped namespace and cluster-scoped RBAC leftovers were deleted from
  both testbed clusters after the failed rerun.

Current verification status for "all DMS requests under mTLS":

- Local route matrix: passed for all 50 protected API endpoints with
  mTLS-required settings and bearer token.
- Live mTLS handshake/proxy boundary: passed for representative Phase 16
  requests in the earlier successful run.
- Live all-endpoint mTLS proxy matrix: pending a clean testbed chain or a
  dedicated Phase16-only verifier that does not rerun superseded backend phases.

Cleanup evidence:

- `Namespace/dms-phase16` was deleted on both clusters.
- Phase-scoped cluster roles and cluster role bindings were removed on both clusters.
- Phase-scoped Longhorn namespaces from the Phase 15 verifier were deleted from `cluster-b`.

## Notes

This phase implements trusted ingress/edge mTLS evidence validation in the DMS API. It does not make the FastAPI server terminate client TLS directly; ingress or an edge proxy still performs the TLS handshake and client certificate validation.

The testbed live verification used a small Python mTLS edge proxy instead of installing ingress-nginx because the current testbed did not have an ingress-nginx namespace or IngressClass. This still verifies the production-relevant DMS boundary: only trusted edge-injected mTLS evidence is accepted, and direct spoofing of that evidence is blocked by NetworkPolicy.

Phase 16 still does not implement JWT/OIDC, token issuer/audience/scope validation, certificate revocation, automatic certificate issuance/rotation, full RBAC/authorization policy, Data Management live execution, VolcanoJob create/watch/terminate, production Helm chart completion, GPFS live staging validation, or WekaFS/Lustre live adapters.
