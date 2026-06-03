# DMS Phase 16 Implementation Prompt: External API mTLS Validation and Auth Boundary Hardening

이 문서는 `docs/dms-phase15.md` 완료 이후 열여섯 번째 구현 에이전트에게 전달할 compact 구현 프롬프트다. Phase 16의 목표는 Data Management 구현으로 넘어가기 전에 **DMS 외부 API request 인증 경계에서 mTLS validation을 실제 구현하고**, 현재 bootstrap 수준의 token/actor header 인증을 production 배포에서 오해 없이 사용할 수 있게 hardening하는 것이다.

구현 전 기준:

- `src/dms/auth.py`의 `AuthVerifier`는 `DMS_AUTH_SHARED_TOKEN`이 설정된 경우 `Authorization: Bearer <token>`만 검사한다.
- actor는 `x-dms-actor` header 또는 `DMS_DEFAULT_ACTOR`에서 가져온다.
- `DMS_AUTH_SHARED_TOKEN`이 설정되지 않은 개발/testbed profile에서는 `x-dms-actor`만 있어도 인증을 통과할 수 있다.
- mTLS 관련 설정(`DMS_REQUIRE_MTLS_HEADER`, `DMS_REQUIRE_MTLS_VERIFIED_HEADER`)은 `docs/dms-design.md`에 언급되어 있지만 실제 `Settings`와 `AuthVerifier`에는 없다.
- ingress 또는 edge proxy가 전달하는 client certificate subject/verify result header를 읽거나 검증하지 않는다.
- `install/kubernetes/ingress.example.yaml`은 일반 TLS ingress 예시이며 client certificate 검증 annotation이 없다.
- authorization policy는 아직 real RBAC가 아니며, Phase 16의 주 범위는 authentication boundary다.

Phase 16은 DMS API가 trusted ingress/edge proxy 뒤에 배포된다는 설계 전제를 코드, 설정, 설치 예시, 테스트베드 검증으로 연결한다.

구현 완료 후 현재 상태:

- `src/dms/config.py`는 `DMS_REQUIRE_MTLS_HEADER`, `DMS_REQUIRE_MTLS_VERIFIED_HEADER`, `DMS_MTLS_ACTOR_PREFIX`를 읽는다.
- `DMS_REQUIRE_MTLS_VERIFIED_HEADER=true`인데 `DMS_REQUIRE_MTLS_HEADER=true`가 아니면 startup configuration error로 실패한다.
- `DMS_REQUIRE_MTLS_HEADER=true`이고 `DMS_DEFAULT_ACTOR`가 비어 있지 않으면 startup configuration error로 실패한다.
- `src/dms/auth.py`는 DMS edge proxy header family와 ingress-nginx header family를 모두 검증한다.
- mTLS-required mode에서는 actor를 certificate subject에서 `DMS_MTLS_ACTOR_PREFIX + subject`로 derive한다.
- mTLS-required mode에서 `x-dms-actor`가 들어오면 derived actor와 정확히 같을 때만 허용한다.
- 인증 실패는 operational request lifecycle에 넣지 않고 `authentication_rejected` observability event만 best-effort로 기록한다.
- `install/` manifest와 helper script는 production profile에서 mTLS evidence validation과 shared bearer token을 함께 쓰도록 업데이트되어 있다.
- 테스트베드 검증은 현재 testbed에 ingress-nginx가 없어 short-lived Python mTLS edge proxy와 NetworkPolicy로 수행했다. `install/kubernetes/ingress.example.yaml`의 ingress-nginx manifest는 운영 배포 예시이며, ingress-nginx controller 자체의 live termination 검증은 별도 staging 항목으로 남긴다.

## Phase 16 목표

Phase 16의 핵심 기능은 다음 일곱 가지다.

1. **mTLS evidence header validation**
2. **actor derivation and spoofing guard**
3. **shared bearer token production-mode hardening**
4. **authentication failure observability contract 유지**
5. **install manifest와 configuration 문서 업데이트**
6. **local regression tests**
7. **testbed live mTLS ingress validation**

구현 완료 기준:

- `Settings`에 mTLS validation 관련 runtime 설정을 추가한다.
- `AuthVerifier`는 trusted ingress/edge proxy가 upstream으로 전달한 mTLS evidence header를 검증할 수 있다.
- mTLS-required mode에서 client certificate subject가 없으면 인증 실패한다.
- mTLS-required-and-verified mode에서 verify result가 `SUCCESS`가 아니면 인증 실패한다.
- 지원하는 header family는 DMS edge proxy style과 ingress-nginx style이다.
- 두 header family가 동시에 들어오고 subject 또는 verify result가 충돌하면 인증 실패한다.
- mTLS-required mode에서는 actor를 client certificate subject에서 derive한다.
- mTLS-required mode에서 `x-dms-actor`가 들어오고 derived actor와 충돌하면 인증 실패한다.
- mTLS-required mode에서는 `DMS_DEFAULT_ACTOR` fallback을 사용하지 않는다.
- `DMS_AUTH_SHARED_TOKEN`이 설정된 경우 bearer token 검증은 기존처럼 필수다.
- production install 예시는 mTLS header validation과 shared token을 모두 켠다.
- 인증 실패 요청은 기존 contract처럼 operational request lifecycle에 들어가지 않고 observability diagnostic event만 남긴다.
- `/healthz`는 unauthenticated internal readiness/liveness endpoint로 유지하되, external ingress route에서 제외하거나 별도 보호하도록 설치 문서에 명시한다.
- API direct access header spoof를 막기 위한 NetworkPolicy 또는 동등한 제어를 install 문서/manifest에 반영한다.
- 검증 결과는 `docs/dms-phase16-verification.md`와 `docs/dms-done.md`에 command/output 포함해 기록한다.

## Runtime Settings

권장 설정:

```text
DMS_REQUIRE_MTLS_HEADER=false
DMS_REQUIRE_MTLS_VERIFIED_HEADER=false
DMS_MTLS_ACTOR_PREFIX=mtls:
DMS_AUTH_SHARED_TOKEN=<optional shared token>
DMS_DEFAULT_ACTOR=<dev/test only>
```

설정 의미:

- `DMS_REQUIRE_MTLS_HEADER`
  - `true`이면 request마다 supported mTLS evidence header가 있어야 한다.
  - `false`이면 기존 개발/test mode처럼 token 또는 actor header 기반 skeleton 인증을 사용할 수 있다.
- `DMS_REQUIRE_MTLS_VERIFIED_HEADER`
  - `true`이면 mTLS verify result가 `SUCCESS`여야 한다.
  - 운영 profile에서는 `DMS_REQUIRE_MTLS_HEADER=true`와 함께 `true`로 둔다.
- `DMS_MTLS_ACTOR_PREFIX`
  - derived actor를 일반 header actor와 구분하기 위한 prefix다.
  - 기본값은 `mtls:`를 권장한다.
  - 예: certificate subject `CN=portal,O=example` -> actor `mtls:CN=portal,O=example`
- `DMS_AUTH_SHARED_TOKEN`
  - 값이 있으면 기존처럼 `Authorization: Bearer <token>`이 필수다.
  - 운영 profile에서는 mTLS validation과 함께 반드시 설정한다.
- `DMS_DEFAULT_ACTOR`
  - 개발/test fallback 전용이다.
  - mTLS-required mode에서는 사용하지 않는다.

Startup validation:

- `DMS_REQUIRE_MTLS_VERIFIED_HEADER=true`인데 `DMS_REQUIRE_MTLS_HEADER=false`이면 startup에서 configuration error로 실패한다.
- `DMS_REQUIRE_MTLS_HEADER=true`이고 `DMS_DEFAULT_ACTOR`가 비어 있지 않으면 startup에서 configuration error로 실패한다.
- `DMS_DEFAULT_ACTOR=`처럼 빈 값은 설정되지 않은 것과 동일하게 처리한다.
- production install 예시는 `DMS_AUTH_SHARED_TOKEN`을 비워 두지 않는다.

## Supported mTLS Evidence Headers

Phase 16은 다음 두 header family를 지원한다.

### DMS Edge Proxy Header Family

```text
X-DMS-Client-Cert-Subject: CN=portal,O=example
X-DMS-Client-Cert-Verify: SUCCESS
```

Optional evidence:

```text
X-DMS-Client-Cert-Issuer: CN=dms-client-ca,O=example
X-DMS-Client-Cert-SAN: DNS:portal.example.internal
```

### ingress-nginx Header Family

```text
ssl-client-subject-dn: CN=portal,O=example
ssl-client-verify: SUCCESS
```

Optional evidence:

```text
ssl-client-issuer-dn: CN=dms-client-ca,O=example
ssl-client-cert: <PEM or escaped certificate>
```

Validation rules:

- Header names are case-insensitive as HTTP headers.
- Subject header is required when `DMS_REQUIRE_MTLS_HEADER=true`.
- Verify header is required when `DMS_REQUIRE_MTLS_VERIFIED_HEADER=true`.
- Verify result must be exactly `SUCCESS` after whitespace trimming.
- `FAILED`, `NONE`, empty string, missing header, or unknown value is rejected.
- If both header families are present and equivalent, the request may be accepted.
- If both header families are present and subject or verify result conflicts, reject with reason `mtls_evidence_conflict`.
- Do not parse `ssl-client-cert` as the primary source of truth in Phase 16. The source of truth is the edge/ingress verification result and subject header.
- The ingress/edge proxy must remove client-supplied evidence headers before setting trusted upstream headers.

Implemented reject reasons:

```text
mtls_verify_required
mtls_verify_failed
mtls_subject_required
mtls_evidence_conflict
actor_evidence_conflict
invalid token
missing actor evidence
```

`mtls_evidence_required`는 defensive internal reason으로 남아 있지만, `Settings.from_env()` startup validation을 통과한 정상 runtime configuration에서는 `mtls_subject_required` 또는 verify 관련 reason이 먼저 사용된다.

## Actor Derivation

Current implementation trusts `x-dms-actor`. That is acceptable only in explicit dev/test mode or behind a trusted caller that already strips spoofed headers.

Phase 16 actor rules:

```text
mTLS required, valid subject present
  -> actor = DMS_MTLS_ACTOR_PREFIX + normalized_subject

mTLS required, x-dms-actor absent
  -> accept derived actor

mTLS required, x-dms-actor present and equals derived actor
  -> accept derived actor

mTLS required, x-dms-actor present and differs from derived actor
  -> reject actor_evidence_conflict

mTLS required, DMS_DEFAULT_ACTOR set
  -> do not use fallback; reject if no valid mTLS actor exists

mTLS not required, DMS_AUTH_SHARED_TOKEN set
  -> validate token and use x-dms-actor or DMS_DEFAULT_ACTOR

mTLS not required, no token configured
  -> existing dev/test behavior may remain, but install docs must mark it unsafe for production
```

Subject normalization:

- Trim surrounding whitespace.
- Preserve certificate DN string content otherwise.
- Do not lower-case the whole subject because DN case may be meaningful in logs and policy matching.
- Reject empty subject.
- Store the final actor in the operational request `actor` field exactly as used for authorization.

## Token Boundary

Phase 16 does not need to implement JWT/OIDC. However, it must make the existing shared token behavior explicit and hard to misconfigure.

Rules:

- If `DMS_AUTH_SHARED_TOKEN` is set, every protected API request must provide `Authorization: Bearer <token>`.
- Token comparison should remain exact, but implementation should avoid logging token values.
- Wrong token, missing token, malformed scheme, and extra whitespace around the full credential should all fail.
- Auth failure observability payload must include reason/path but not token content.
- Install examples must keep `DMS_AUTH_SHARED_TOKEN` as a Kubernetes Secret value, never ConfigMap.

Out of scope:

- JWT signature validation
- OIDC discovery
- token issuer/audience/expiry claims
- token rotation controller
- per-client scoped tokens

## API Surface

All existing API paths that already call `authenticated_actor()` should be covered by the new verifier. This includes:

- Resource Management mutating requests
- Data Management request/confirm/cancel paths
- storage mapping management
- identity mapping management
- agent reports
- operational query endpoints

`/healthz` remains unauthenticated, but production ingress should not expose it as a public route unless the ingress itself enforces the same mTLS/token boundary.

Authentication failure contract:

- Return `401 Unauthorized`.
- Record best-effort observability event `authentication_rejected`.
- Do not create an operational request row.
- Do not create plan/run/result.
- Preserve Phase 14 observability safe-write behavior.

Authorization failure contract remains unchanged:

- Authenticated request that fails operation authorization returns `403`.
- Operational request is recorded as `AuthorizationFailed`.
- No plan/run/backend side effect is created.

## Install Manifest and Documentation Updates

Update these files during Phase 16:

- `install/CONFIGURATION.md`
- `install/README.md`
- `install/config/dms-runtime.env.example`
- `install/kubernetes/control-plane.yaml`
- `install/kubernetes/ingress.example.yaml`

Recommended ingress-nginx example annotations:

```yaml
metadata:
  annotations:
    nginx.ingress.kubernetes.io/proxy-body-size: "2m"
    nginx.ingress.kubernetes.io/auth-tls-secret: "dms/dms-client-ca"
    nginx.ingress.kubernetes.io/auth-tls-verify-client: "on"
    nginx.ingress.kubernetes.io/auth-tls-verify-depth: "2"
    nginx.ingress.kubernetes.io/auth-tls-pass-certificate-to-upstream: "true"
```

Recommended ConfigMap additions:

```yaml
data:
  DMS_REQUIRE_MTLS_HEADER: "true"
  DMS_REQUIRE_MTLS_VERIFIED_HEADER: "true"
  DMS_MTLS_ACTOR_PREFIX: "mtls:"
```

Recommended Secret additions:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: dms-client-ca
  namespace: dms
type: Opaque
stringData:
  ca.crt: |
    # Client certificate CA bundle for ingress mTLS validation.
```

Direct API access control:

- Add or document a NetworkPolicy that only allows DMS API Pod ingress from the ingress controller namespace, DMS internal components that need direct API access, and explicitly allowed test namespaces.
- The policy must prevent a random in-cluster Pod from reaching `dms-api` directly with spoofed `ssl-client-*` or `X-DMS-Client-Cert-*` headers.
- If the cluster CNI does not enforce NetworkPolicy, document the equivalent control required by the operator.

## Implementation Hints

Suggested code shape:

- Extend `Settings` in `src/dms/config.py`.
- Add a small `MtlsEvidence` dataclass in `src/dms/auth.py`.
- Keep parsing/normalization inside `AuthVerifier` or a helper used only by it.
- Keep authorization policy separate from authentication validation.
- Avoid route-level mTLS checks; route handlers should continue to call `authenticated_actor()`.
- Keep tests free of real TLS by unit-testing header evidence parsing and API contract locally.
- Live TLS handshake belongs in the testbed verifier.

Pseudo-flow:

```text
AuthVerifier.verify(request):
  token_result = verify_shared_token_if_configured(request)
  mtls_result = verify_mtls_evidence_if_required(request)
  actor = derive_actor(mtls_result, request.headers.get("x-dms-actor"), default_actor)
  if any required evidence failed:
    return AuthResult(False, reason=...)
  if no actor:
    return AuthResult(False, reason="missing actor evidence")
  return AuthResult(True, actor=actor)
```

## Minimum Local Tests

Add or update tests for:

- Existing dev/test request with only `x-dms-actor` still works when mTLS is not required.
- `DMS_AUTH_SHARED_TOKEN` set and correct bearer token accepts request.
- `DMS_AUTH_SHARED_TOKEN` set and missing/wrong bearer token returns 401.
- `DMS_REQUIRE_MTLS_HEADER=true` with no mTLS evidence returns 401 and creates no operational request.
- mTLS subject present and verify `SUCCESS` derives `mtls:<subject>` actor.
- verify result `FAILED`, `NONE`, empty, or missing returns 401 when verified header is required.
- DMS edge proxy header family works.
- ingress-nginx header family works.
- conflicting DMS edge and ingress-nginx header families return 401.
- mTLS-required mode ignores `DMS_DEFAULT_ACTOR`.
- mTLS-required mode rejects mismatched `x-dms-actor`.
- mTLS-required mode accepts matching `x-dms-actor`.
- auth rejection observability safe-write contract is preserved when observability repository fails.
- `/healthz` behavior is unchanged.
- agent report endpoint requires valid mTLS evidence and token in production-mode settings.
- operational query endpoints require the same auth boundary.

## Testbed Live Verification

Phase 16 live verification uses the existing Vagrant multi-cluster testbed.

Before verification:

- Read `/home/mason/workspace/testbed/testbed-summary.json`.
- Read `/home/mason/workspace/testbed/testbed-info.json`.
- Read `/home/mason/workspace/testbed/TOPOLOGY.md`.
- Reuse existing ingress-nginx installation if present.
- If ingress-nginx or cert tooling is missing, install the smallest practical test fixture and document it in the testbed directory.

Recommended verifier:

```text
scripts/verify-phase16-testbed.sh
scripts/phase16_mtls_auth.py
```

검증 항목:

1. Fresh PostgreSQL operational/observability DB를 사용한다.
2. DMS API를 `DMS_REQUIRE_MTLS_HEADER=true`, `DMS_REQUIRE_MTLS_VERIFIED_HEADER=true`, `DMS_AUTH_SHARED_TOKEN=<test token>`으로 배포한다.
3. ingress-nginx 또는 testbed edge proxy에 client certificate CA를 설정한다.
4. client certificate 없이 ingress URL을 호출하면 ingress 또는 DMS API에서 reject된다.
5. wrong/untrusted client certificate으로 호출하면 reject된다.
6. valid client certificate이 있지만 bearer token이 없으면 DMS API에서 401이다.
7. valid client certificate과 wrong bearer token이면 DMS API에서 401이다.
8. valid client certificate과 correct bearer token이면 Resource Management request가 202로 접수되고 operational request actor가 `mtls:<subject>` 형태로 저장된다.
9. valid client certificate request에 conflicting `x-dms-actor`를 넣으면 401이다.
10. ingress/edge가 전달한 verify result가 `SUCCESS`가 아닌 fixture request는 401이다.
11. in-cluster disallowed namespace Pod에서 DMS Service로 direct request를 보내며 spoofed mTLS evidence header를 넣어도 NetworkPolicy 또는 동등 제어에 의해 차단된다.
12. allowed internal component path가 필요한 경우에는 별도 service account/namespace allow rule로만 접근 가능함을 확인한다.
13. `/healthz`는 내부 readiness 용도로만 접근 가능하거나, external route에서 제외되어 있음을 확인한다.
14. observability event에는 `authentication_rejected`가 남고 token/certificate raw secret material은 기록되지 않는다.

검증 결과는 `docs/dms-phase16-verification.md`와 `docs/dms-done.md`에 기록한다.

## Phase 16에서 하지 않을 것

다음은 Phase 16 범위가 아니다.

- JWT/OIDC provider integration
- token issuer/audience/scope validation
- full RBAC/authorization policy schema
- `DMS_ADMIN_ACTORS` 기반 production authorization enforcement
- certificate revocation/OCSP/CRL
- automated client certificate issuance/rotation
- per-agent certificate provisioning
- Data Management `scan/sync/rm` live execution
- VolcanoJob create/watch/terminate
- DM Worker long-running runtime 검증
- worker lease/heartbeat renewal과 stale recovery guard
- maintenance/drain enforcement와 control API/CLI
- production Helm chart 완성
- GPFS live testbed 구축
- WekaFS/Lustre live implementation

## Phase 16 이후 다음 작업 리스트

Phase 16으로 external API authentication boundary를 production deployment에 맞게 닫은 뒤, Resource Management 정합성을 먼저 닫는 `docs/dms-phase17.md`가 진행됐다. Phase 17의 목표는 Kubernetes namespace quota Resource Management 경로를 모든 CSI StorageClass backend에 대해 하나의 live `ResourceQuota` adapter로 통합하는 것이다.

### Phase 17: Kubernetes ResourceQuota Live Adapter Unification

- GPFS CSI namespace quota가 `KubernetesNamespaceQuotaLiveAdapter`를 타도록 수정 완료
- CephFS, GPFS, Longhorn, WEKA 등 모든 CSI StorageClass namespace quota를 backend-neutral live ResourceQuota path로 통합 완료
- `BackendAdapterRegistry.kubernetes_for_plan()`에서 backend type 기반 Kubernetes quota adapter 분기 제거 완료
- mixed backend multi-StorageClass quota가 하나의 DMS-managed `ResourceQuota/dms-storage-quota`로 렌더링되는지 회귀 검증 완료
- filesystem backend adapter selection은 기존처럼 backend-specific/fail-closed로 유지

### Phase 18A: Data Management Read-only Scan Preflight

- filesystem resource boundary를 read-only scan target으로 사용
- DM Agent report 기반 candidate pool
- POSIX identity/mount/tool preflight
- VolcanoJob 이전 local scan preflight 검증

### Phase 18B: DM Worker Runtime and VolcanoJob Skeleton

- `dms dm-worker --loop` Deployment
- VolcanoJob create/watch/delete skeleton
- job lease/recovery
- artifact URI and preview lifecycle

### Phase 18C: Filesystem Policy and Initialize

- filesystem default quota policy
- `filesystem.initialize`
- `reset_quota_to_default=true`
- quota clear/unlimited lifecycle

권장 순서는 Phase 17 Kubernetes ResourceQuota live adapter 통합 완료 이후, Phase 18A로 Data Management read-only scan preflight를 구현한 뒤 Phase 18B로 DM Worker/VolcanoJob live execution을 여는 것이다.
