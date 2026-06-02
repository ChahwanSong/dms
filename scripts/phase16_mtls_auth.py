from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shlex
import ssl
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))

from dms.config import Settings  # noqa: E402
from dms.db import Database  # noqa: E402
from dms.domain import DataJobState, OperationKind, ResourceKind, WorkerRole  # noqa: E402
from dms.repositories import DmsRepository, ObservabilityRepository  # noqa: E402
from scripts.phase6_kubernetes_multi_storage_quota import (  # noqa: E402
    assert_equal,
    assert_true,
    mask_url,
)


@dataclass
class HttpResponse:
    status_code: int
    body: str
    transport_error: str | None = None

    def json(self):
        return json.loads(self.body or "null")


def main() -> int:
    settings = Settings.from_env()
    repository = DmsRepository(Database(settings.database_url))
    observability = ObservabilityRepository(Database(settings.observability_database_url))
    token = settings.auth_shared_token
    assert_true(token is not None, "Phase 16 verification requires DMS_AUTH_SHARED_TOKEN")
    assert_true(
        settings.require_mtls_header is True,
        "Phase 16 verification requires DMS_REQUIRE_MTLS_HEADER=true",
    )
    assert_true(
        settings.require_mtls_verified_header is True,
        "Phase 16 verification requires DMS_REQUIRE_MTLS_VERIFIED_HEADER=true",
    )
    assert_true(
        settings.database_url.startswith("postgresql://"),
        "Phase 16 live verification must use operational PostgreSQL",
    )

    proxy_url = os.environ["DMS_PHASE16_MTLS_PROXY_URL"].rstrip("/")
    direct_url = os.environ["DMS_PHASE16_DIRECT_API_URL"].rstrip("/")
    namespace = os.environ["DMS_PHASE16_NAMESPACE"]
    valid_context = _client_context(
        ca_cert=os.environ["DMS_PHASE16_CA_CERT"],
        cert=os.environ["DMS_PHASE16_CLIENT_CERT"],
        key=os.environ["DMS_PHASE16_CLIENT_KEY"],
    )
    no_client_context = _client_context(ca_cert=os.environ["DMS_PHASE16_CA_CERT"])
    bad_client_context = _client_context(
        ca_cert=os.environ["DMS_PHASE16_CA_CERT"],
        cert=os.environ["DMS_PHASE16_BAD_CLIENT_CERT"],
        key=os.environ["DMS_PHASE16_BAD_CLIENT_KEY"],
    )

    missing_cert = _get(proxy_url, "/healthz", context=no_client_context)
    assert_true(
        missing_cert.status_code == 0,
        "mTLS proxy should reject requests without a client certificate",
    )
    bad_cert = _get(proxy_url, "/healthz", context=bad_client_context)
    assert_true(
        bad_cert.status_code == 0,
        "mTLS proxy should reject untrusted client certificates",
    )

    missing_token = _get(
        proxy_url,
        "/api/v1/operations/action-required",
        context=valid_context,
    )
    assert_equal(missing_token.status_code, 401, "valid mTLS without token returns 401")
    assert_equal(missing_token.json()["detail"], "invalid token", "missing token reason")

    wrong_token = _get(
        proxy_url,
        "/api/v1/operations/action-required",
        context=valid_context,
        headers={"authorization": "Bearer wrong-token"},
    )
    assert_equal(wrong_token.status_code, 401, "valid mTLS with wrong token returns 401")
    assert_equal(wrong_token.json()["detail"], "invalid token", "wrong token reason")

    accepted = _post(
        proxy_url,
        "/api/v1/resource-management/filesystems",
        context=valid_context,
        headers={"authorization": f"Bearer {token}"},
        json_body={
            "requester_id": "phase16-user",
            "payload": {
                "storage_name": "cephfs-a",
                "directory_name": f"phase16-auth-{os.getpid()}",
                "resource_type": "user",
                "users": ["alice", "bob"],
                "expires_at": "2099-01-01T00:00:00Z",
            },
        },
    )
    assert_equal(accepted.status_code, 202, "valid mTLS and token accepts RM request")
    request_id = accepted.json()["request_id"]
    request = repository.get_request(request_id)
    assert_true(
        request["actor"].startswith("mtls:"),
        "accepted request actor must be derived from mTLS subject",
    )
    assert_true(
        "phase16-client" in request["actor"],
        "accepted request actor should contain client certificate subject",
    )

    actor_conflict = _get(
        proxy_url,
        "/api/v1/operations/action-required",
        context=valid_context,
        headers={
            "authorization": f"Bearer {token}",
            "x-dms-actor": "api-client",
        },
    )
    assert_equal(actor_conflict.status_code, 401, "conflicting x-dms-actor is rejected")
    assert_equal(
        actor_conflict.json()["detail"],
        "actor_evidence_conflict",
        "conflicting x-dms-actor reason",
    )

    verify_failed = _get(
        direct_url,
        "/api/v1/operations/action-required",
        headers={
            "authorization": f"Bearer {token}",
            "x-dms-client-cert-subject": "CN=phase16-client,O=testbed",
            "x-dms-client-cert-verify": "FAILED",
        },
    )
    assert_equal(
        verify_failed.status_code,
        401,
        "DMS API rejects direct FAILED mTLS verify evidence",
    )
    assert_equal(
        verify_failed.json()["detail"],
        "mtls_verify_failed",
        "FAILED verify evidence reason",
    )

    _apply_direct_access_network_policy(namespace)
    direct_spoof = _verify_direct_spoof_from_pod(namespace, token)

    proxy_still_allowed = _get(
        proxy_url,
        "/api/v1/operations/action-required",
        context=valid_context,
        headers={"authorization": f"Bearer {token}"},
    )
    assert_equal(
        proxy_still_allowed.status_code,
        200,
        "mTLS proxy remains allowed after direct-access NetworkPolicy",
    )

    health = _get(proxy_url, "/healthz", context=valid_context)
    assert_equal(health.status_code, 200, "healthz is reachable through protected mTLS edge")

    endpoint_matrix = _verify_protected_endpoint_matrix(
        proxy_url=proxy_url,
        context=valid_context,
        token=token,
        repository=repository,
    )

    events = observability.list_events()
    auth_rejections = [
        event for event in events if event["event_type"] == "authentication_rejected"
    ]
    assert_true(
        auth_rejections,
        "Phase 16 verifier should record authentication_rejected diagnostics",
    )
    forbidden_payload = json.dumps(auth_rejections, sort_keys=True)
    assert_true(token not in forbidden_payload, "auth diagnostics must not include token value")
    assert_true(
        "BEGIN CERTIFICATE" not in forbidden_payload,
        "auth diagnostics must not include raw certificate material",
    )
    summary = {
        "status": "ok",
        "operational_database_url": mask_url(settings.database_url),
        "observability_database_url": mask_url(settings.observability_database_url),
        "accepted_request_id": request_id,
        "accepted_actor": request["actor"],
        "missing_client_certificate": missing_cert.transport_error,
        "bad_client_certificate": bad_cert.transport_error,
        "direct_spoof_pod_output": direct_spoof,
        "authentication_rejected_events": len(auth_rejections),
        "protected_endpoint_matrix": endpoint_matrix,
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def _client_context(
    *,
    ca_cert: str,
    cert: str | None = None,
    key: str | None = None,
) -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=ca_cert)
    context.check_hostname = False
    if cert and key:
        context.load_cert_chain(certfile=cert, keyfile=key)
    return context


def _get(
    base_url: str,
    path: str,
    *,
    context: ssl.SSLContext | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 10,
) -> HttpResponse:
    return _request(
        base_url,
        "GET",
        path,
        context=context,
        headers=headers,
        timeout=timeout,
    )


def _post(
    base_url: str,
    path: str,
    *,
    context: ssl.SSLContext | None = None,
    headers: dict[str, str] | None = None,
    json_body: dict | None = None,
    timeout: int = 10,
) -> HttpResponse:
    return _request(
        base_url,
        "POST",
        path,
        context=context,
        headers=headers,
        json_body=json_body,
        timeout=timeout,
    )


def _request(
    base_url: str,
    method: str,
    path: str,
    *,
    context: ssl.SSLContext | None = None,
    headers: dict[str, str] | None = None,
    json_body: dict | None = None,
    timeout: int,
) -> HttpResponse:
    body = None
    request_headers = dict(headers or {})
    if json_body is not None:
        body = json.dumps(json_body, sort_keys=True).encode("utf-8")
        request_headers["content-type"] = "application/json"
    request = Request(
        f"{base_url}{path}",
        data=body,
        headers=request_headers,
        method=method,
    )
    try:
        with urlopen(request, timeout=timeout, context=context) as response:
            return HttpResponse(response.status, response.read().decode("utf-8"))
    except HTTPError as exc:
        return HttpResponse(exc.code, exc.read().decode("utf-8"))
    except (URLError, TimeoutError, ssl.SSLError, OSError) as exc:
        return HttpResponse(0, "", transport_error=str(exc))


def _apply_direct_access_network_policy(namespace: str) -> None:
    manifest = f"""
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: dms-api-phase16-mtls-proxy-only
  namespace: {namespace}
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/name: dms-api
  policyTypes:
    - Ingress
  ingress:
    - from:
        - podSelector:
            matchLabels:
              app.kubernetes.io/name: dms-mtls-proxy
      ports:
        - protocol: TCP
          port: 8080
"""
    subprocess.run(
        ["ssh", "c1-control", "kubectl", "apply", "-f", "-"],
        input=manifest,
        text=True,
        check=True,
    )
    time.sleep(5)


def _verify_direct_spoof_from_pod(namespace: str, token: str) -> str:
    image = os.environ["DMS_PHASE16_K8S_IMAGE"]
    code = f"""
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

request = Request(
    "http://dms-api/api/v1/operations/action-required",
    headers={{
        "authorization": "Bearer {token}",
        "x-dms-client-cert-subject": "CN=phase16-client,O=testbed",
        "x-dms-client-cert-verify": "SUCCESS",
    }},
    method="GET",
)
try:
    with urlopen(request, timeout=5) as response:
        status = response.status
        body = response.read().decode("utf-8")
except HTTPError as exc:
    status = exc.code
    body = exc.read().decode("utf-8")
except (URLError, TimeoutError, OSError) as exc:
    print(json.dumps({{"blocked": True, "error": str(exc)}}))
    sys.exit(0)
print(json.dumps({{"blocked": status != 200, "status": status, "body": body}}))
sys.exit(0 if status != 200 else 2)
"""
    name = f"phase16-direct-spoof-{os.getpid()}"
    remote = (
        f"kubectl -n {shlex.quote(namespace)} run {shlex.quote(name)} "
        f"--image={shlex.quote(image)} --restart=Never --rm -i --quiet "
        f"--command -- python3 -c {shlex.quote(code)}"
    )
    result = subprocess.run(
        ["ssh", "c1-control", remote],
        text=True,
        capture_output=True,
        timeout=90,
    )
    output = (result.stdout + result.stderr).strip()
    assert_equal(
        result.returncode,
        0,
        f"direct spoofed mTLS evidence should be blocked by NetworkPolicy: {output}",
    )
    return output


def _verify_protected_endpoint_matrix(
    *,
    proxy_url: str,
    context: ssl.SSLContext,
    token: str,
    repository: DmsRepository,
) -> dict[str, int]:
    headers = {"authorization": f"Bearer {token}"}
    seeded_request_id = repository.create_request(
        requester_id="phase16-mtls-matrix",
        actor="phase16-verifier",
        operation=OperationKind.FILESYSTEM_CHECK.value,
        resource_kind=ResourceKind.FILESYSTEM.value,
        resource_key="cephfs-a:phase16-seeded",
        payload={"storage_name": "cephfs-a", "directory_name": "phase16-seeded"},
    )
    confirm_job_id = _seed_data_job(repository, state=DataJobState.CONFIRM_PENDING)
    cancel_job_id = _seed_data_job(repository, state=DataJobState.PENDING)
    cases: list[tuple[str, str, dict | None, set[int]]] = [
        (
            "POST",
            "/api/v1/resource-management/storage-mappings",
            {
                "storage_name": "phase16-mtls-storage",
                "backend_template": {"backend_type": "cephfs"},
            },
            {200},
        ),
        ("POST", "/api/v1/resource-management/storage-mappings/phase16-mtls-storage:check", None, {200}),
        (
            "POST",
            "/api/v1/resource-management/default-quota-policies",
            {
                "resource_kind": ResourceKind.FILESYSTEM.value,
                "resource_type": "user",
                "quota": {"capacity_bytes": 1024},
            },
            {200},
        ),
        (
            "PUT",
            "/api/v1/identity-mappings/ldap-main/phase16-user",
            {
                "requester_id": "phase16-user",
                "identity_provider": "ldap-main",
                "posix_username": "alice",
                "expected_uid": 10000,
                "expected_primary_gid": 10000,
            },
            {200, 404, 503},
        ),
        ("POST", "/api/v1/identity-mappings/ldap-main/phase16-user:refresh", None, {200, 404, 503}),
        ("POST", "/api/v1/identity-mappings/ldap-main/phase16-user:disable", {"reason": "phase16"}, {200}),
        ("GET", "/api/v1/identity-mappings", None, {200}),
        (
            "POST",
            "/api/v1/resource-management/requests",
            {
                "requester_id": "phase16-mtls-matrix",
                "operation": OperationKind.FILESYSTEM_CHECK.value,
                "resource_kind": ResourceKind.FILESYSTEM.value,
                "resource_key": "cephfs-a:generic",
                "payload": {"storage_name": "cephfs-a", "directory_name": "generic"},
            },
            {202},
        ),
        ("POST", "/api/v1/resource-management/filesystems", _filesystem_body(), {202}),
        ("PATCH", "/api/v1/resource-management/filesystems/cephfs-a/fs-update", _mutating_body({"expires_at": "2099-01-01T00:00:00Z"}), {202}),
        ("POST", "/api/v1/resource-management/filesystems/cephfs-a/fs-block:block", _mutating_body({"block": True}), {202}),
        ("POST", "/api/v1/resource-management/filesystems/cephfs-a/fs-init:initialize", _mutating_body({"quota": {"capacity_bytes": 1024}}), {202}),
        ("DELETE", "/api/v1/resource-management/filesystems/cephfs-a/fs-delete", _mutating_body({}), {202}),
        ("POST", "/api/v1/resource-management/filesystems/cephfs-a/fs-assign:assign-quota", _mutating_body({"quota": {"capacity_bytes": 1024}}), {202}),
        ("POST", "/api/v1/resource-management/filesystems/cephfs-a/fs-import:import", _mutating_body({"expires_at": "2099-01-01T00:00:00Z"}), {202}),
        ("POST", "/api/v1/resource-management/filesystems/cephfs-a/fs-check:check", _mutating_body({}), {202}),
        ("POST", "/api/v1/resource-management/filesystems/cephfs-a/fs-sync:sync", _mutating_body({}), {202}),
        ("POST", "/api/v1/resource-management/filesystems:expiration-sweep", _mutating_body({"dry_run": True}), {202}),
        ("POST", "/api/v1/resource-management/kubernetes/namespace-quotas", _mutating_body(_kubernetes_quota_payload("phase16-create")), {202}),
        ("PATCH", "/api/v1/resource-management/kubernetes/namespace-quotas/cluster-a/phase16-update", _mutating_body({"expires_at": "2099-01-01T00:00:00Z"}), {202}),
        ("POST", "/api/v1/resource-management/kubernetes/namespace-quotas/cluster-a/phase16-block:block", _mutating_body({"block": True}), {202}),
        ("DELETE", "/api/v1/resource-management/kubernetes/namespace-quotas/cluster-a/phase16-delete", _mutating_body({}), {202}),
        ("POST", "/api/v1/resource-management/kubernetes/namespace-quotas/cluster-a/phase16-sync:sync", _mutating_body({}), {202}),
        ("POST", "/api/v1/resource-management/kubernetes/namespace-quotas/cluster-a/phase16-import:import", _mutating_body({"expires_at": "2099-01-01T00:00:00Z"}), {202}),
        ("POST", "/api/v1/resource-management/kubernetes/namespace-quotas/cluster-a/phase16-check:check", _mutating_body({}), {202}),
        ("POST", "/api/v1/resource-management/kubernetes/namespace-quotas:expiration-sweep", _mutating_body({"dry_run": True}), {202}),
        ("POST", "/api/v1/resource-management/kubernetes/namespace-quotas:audit", _mutating_body({"scope": {"cluster_name": "cluster-a"}}), {202}),
        ("POST", "/api/v1/data-management/sync", _data_job_body(source_path="src", destination_path="dst"), {202}),
        ("POST", "/api/v1/data-management/rm", _data_job_body(target_path="target"), {202}),
        ("POST", "/api/v1/data-management/scan", _data_job_body(target_path="target"), {202}),
        ("POST", f"/api/v1/data-management/jobs/{confirm_job_id}:confirm", None, {200}),
        ("POST", f"/api/v1/data-management/jobs/{cancel_job_id}:cancel", None, {200}),
        ("POST", "/api/v1/agent/reports", _agent_report(), {403}),
        ("GET", "/api/v1/operations/action-required", None, {200}),
        ("GET", "/api/v1/operations/inventory", None, {200}),
        ("GET", "/api/v1/operations/agent-reports", None, {200}),
        ("GET", "/api/v1/operations/storage-mappings", None, {200}),
        ("GET", "/api/v1/operations/storage-mappings/phase16-mtls-storage", None, {200}),
        ("GET", "/api/v1/operations/requests?requester_id=phase16-mtls-matrix&limit=20", None, {200}),
        ("GET", f"/api/v1/operations/requests/{seeded_request_id}", None, {200}),
        ("GET", "/api/v1/operations/resources", None, {200}),
        ("GET", "/api/v1/operations/filesystems/expiring?status=expired", None, {200}),
        ("GET", "/api/v1/operations/kubernetes/namespace-quotas/cluster-a/phase16-create", None, {200}),
        ("GET", "/api/v1/operations/kubernetes/namespace-quotas/expiring?status=expired", None, {200}),
        ("GET", "/api/v1/operations/runs/stale", None, {200}),
        ("GET", "/api/v1/operations/worker-agent-health", None, {200}),
        ("GET", "/api/v1/operations/identity-issues", None, {200}),
        ("GET", "/api/v1/operations/data-jobs", None, {200}),
        ("GET", f"/api/v1/operations/data-jobs/{cancel_job_id}", None, {200}),
        ("GET", f"/api/v1/operations/diagnostics/{seeded_request_id}", None, {200}),
    ]
    status_counts: dict[str, int] = {}
    for method, path, body, expected in cases:
        response = _request(
            proxy_url,
            method,
            path,
            context=context,
            headers=headers,
            json_body=body,
            timeout=20,
        )
        assert_true(response.status_code != 0, f"{method} {path} transport error: {response.transport_error}")
        assert_true(response.status_code != 401, f"{method} {path} was blocked by authentication")
        assert_true(
            response.status_code in expected,
            f"{method} {path} returned {response.status_code}, expected {sorted(expected)}: {response.body}",
        )
        status_counts[str(response.status_code)] = status_counts.get(str(response.status_code), 0) + 1
    help_response = _get(proxy_url, "/api/v1/data-management/help", context=context)
    assert_equal(help_response.status_code, 200, "data-management help remains public")
    return {"checked": len(cases), **status_counts}


def _filesystem_body() -> dict:
    return {
        "requester_id": "phase16-mtls-matrix",
        "payload": {
            "storage_name": "cephfs-a",
            "directory_name": f"phase16-matrix-{os.getpid()}",
            "resource_type": "user",
            "users": ["alice", "bob"],
            "expires_at": "2099-01-01T00:00:00Z",
        },
    }


def _mutating_body(payload: dict) -> dict:
    return {"requester_id": "phase16-mtls-matrix", "payload": payload}


def _kubernetes_quota_payload(namespace_name: str) -> dict:
    return {
        "cluster_name": "cluster-a",
        "namespace_name": namespace_name,
        "storage_class_quotas": [{"storage_name": "phase16-mtls-storage"}],
        "quota": {"requests_storage_bytes": 1024, "pvc_count": 1},
        "expires_at": "2099-01-01T00:00:00Z",
    }


def _data_job_body(
    *,
    source_path: str | None = None,
    destination_path: str | None = None,
    target_path: str | None = None,
) -> dict:
    body = {"requester_id": "phase16-mtls-matrix", "storage_name": "cephfs-a"}
    if source_path is not None:
        body["source_path"] = source_path
    if destination_path is not None:
        body["destination_path"] = destination_path
    if target_path is not None:
        body["target_path"] = target_path
    return body


def _agent_report() -> dict:
    return {
        "schema_version": "phase8.v1",
        "reported_at": "2026-06-02T00:00:00+00:00",
        "cluster_name": "cluster-a",
        "node_name": "c1-worker",
        "node_uid": "uid-c1-worker",
        "worker_role": "RM",
        "mounts": [],
        "csi": [],
        "tools": [],
        "credentials": [],
        "networks": [],
        "identity_evidence": {"source": "phase16-mtls-matrix"},
    }


def _seed_data_job(repository: DmsRepository, *, state: DataJobState) -> str:
    request_id = repository.create_request(
        requester_id="phase16-mtls-matrix",
        actor="phase16-verifier",
        operation=OperationKind.DATA_SYNC.value,
        resource_kind=ResourceKind.DATA_JOB.value,
        resource_key="cephfs-a:data.sync:src:dst:",
        payload={
            "storage_name": "cephfs-a",
            "source_path": "src",
            "destination_path": "dst",
            "priority": 100,
        },
    )
    job_id = repository.create_data_job(
        request_id=request_id,
        operation=OperationKind.DATA_SYNC.value,
        storage_name="cephfs-a",
        source="src",
        destination="dst",
        target=None,
        priority=100,
        worker_pool={},
        state=state,
    )
    repository.create_plan(
        request_id=request_id,
        worker_role=WorkerRole.DM,
        operation_kind=OperationKind.DATA_SYNC.value,
        resource_key="cephfs-a:data.sync:src:dst:",
        desired_state={"storage_name": "cephfs-a"},
        precondition={"job_id": job_id},
        execution_metadata={"job_id": job_id, "phase": "preview"},
    )
    return job_id


if __name__ == "__main__":
    raise SystemExit(main())
