from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))

from dms.config import Settings  # noqa: E402
from dms.db import Database  # noqa: E402
from dms.domain import LifecycleState, ResourceKind, StorageMappingInput  # noqa: E402
from dms.repositories import DmsRepository  # noqa: E402
from scripts import phase10_ceph_host_filesystem_rm as phase10  # noqa: E402
from scripts.phase6_kubernetes_multi_storage_quota import (  # noqa: E402
    assert_equal,
    assert_true,
    mask_url,
)


TERMINAL_STATES = {
    LifecycleState.SUCCEEDED.value,
    LifecycleState.REJECTED.value,
    LifecycleState.FAILED.value,
    LifecycleState.BACKEND_APPLY_FAILED.value,
    LifecycleState.VERIFICATION_FAILED.value,
    LifecycleState.UNKNOWN_AFTER_SIDE_EFFECT.value,
}


@dataclass
class HttpResponse:
    status_code: int
    body: str

    def json(self):
        return json.loads(self.body or "null")


@dataclass
class HttpClient:
    base_url: str

    def get(
        self,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, object] | None = None,
    ) -> HttpResponse:
        if params:
            path = f"{path}?{urlencode(params)}"
        return self.request("GET", path, headers=headers)

    def post(
        self,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        json: dict | None = None,
        json_body: dict | None = None,
    ) -> HttpResponse:
        if json_body is None:
            json_body = json
        return self.request("POST", path, headers=headers, json_body=json_body)

    def patch(
        self,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        json: dict | None = None,
        json_body: dict | None = None,
    ) -> HttpResponse:
        if json_body is None:
            json_body = json
        return self.request("PATCH", path, headers=headers, json_body=json_body)

    def request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: dict | None = None,
    ) -> HttpResponse:
        body = None
        request_headers = dict(headers or {})
        if json_body is not None:
            body = json.dumps(json_body, sort_keys=True).encode("utf-8")
            request_headers["content-type"] = "application/json"
        request = Request(
            f"{self.base_url}{path}",
            data=body,
            headers=request_headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=20) as response:
                return HttpResponse(response.status, response.read().decode("utf-8"))
        except HTTPError as exc:
            return HttpResponse(exc.code, exc.read().decode("utf-8"))


def main() -> int:
    settings = Settings.from_env()
    repository = DmsRepository(Database(settings.database_url))
    client = HttpClient(os.environ["DMS_PHASE13_API_URL"].rstrip("/"))
    token = uuid4().hex[:8]
    headers = {"x-dms-actor": "api-client"}
    if settings.auth_shared_token:
        headers["authorization"] = f"Bearer {settings.auth_shared_token}"
    phase10.API_HEADERS.clear()
    phase10.API_HEADERS.update(headers)

    assert_true(
        settings.database_url.startswith("postgresql://"),
        "Phase 15 live verification must use operational PostgreSQL",
    )

    c1 = phase10.FilesystemTarget(
        storage_name="cephfs-a",
        cluster_name="cluster-a",
        node_name=os.getenv("DMS_PHASE13_C1_NODE", os.getenv("DMS_PHASE10_C1_NODE", "c1-worker")),
        mount_path=os.getenv(
            "DMS_PHASE13_C1_CEPH_MOUNT_PATH",
            os.getenv("DMS_PHASE10_C1_CEPH_MOUNT_PATH", "/mnt/testbed-cephfs"),
        ),
        storage_class_name="testbed-cephfs",
        csi_driver="rook-ceph.cephfs.csi.ceph.com",
    )
    fs_directory = f"phase15-fs-{token}"
    k8s_namespace = f"phase15-quota-{token}"
    import_namespace = f"phase15-import-{token}"
    sweep_namespace = f"phase15-expire-{token}"
    created_users: list[str] = []

    try:
        users = phase10.ensure_ldap_users(token=token, ldap_password=os.environ["DMS_LDAP_BIND_PASSWORD"])
        created_users = users["created_users"]
        phase10.verify_sssd_users(
            [c1.node_name],
            users["allowed_users"] + [users["denied_user"]],
        )
        ensure_filesystem_mapping(client, repository, c1)
        register_longhorn_mapping(repository, storage_name="phase15-longhorn-b")

        filesystem_summary = verify_filesystem_expiry_update(
            client=client,
            repository=repository,
            headers=headers,
            target=c1,
            directory_name=fs_directory,
            allowed_users=users["allowed_users"],
            denied_user=users["denied_user"],
        )
        k8s_summary = verify_kubernetes_expiry_lifecycle(
            client=client,
            repository=repository,
            headers=headers,
            storage_name="phase15-longhorn-b",
            namespace_name=k8s_namespace,
            import_namespace=import_namespace,
            sweep_namespace=sweep_namespace,
        )
        summary = {
            "status": "ok",
            "operational_database_url": mask_url(settings.database_url),
            "filesystem": filesystem_summary,
            "kubernetes": k8s_summary,
        }
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0
    finally:
        phase10.cleanup_directory(c1, fs_directory)
        for namespace in [k8s_namespace, import_namespace, sweep_namespace]:
            subprocess.run(
                [
                    "ssh",
                    "c2-control",
                    "kubectl",
                    "delete",
                    "namespace",
                    namespace,
                    "--ignore-not-found=true",
                ],
                check=False,
            )
        for username in created_users:
            phase10.delete_ldap_user(username, os.environ["DMS_LDAP_BIND_PASSWORD"])


def verify_filesystem_expiry_update(
    *,
    client: HttpClient,
    repository: DmsRepository,
    headers: dict[str, str],
    target: phase10.FilesystemTarget,
    directory_name: str,
    allowed_users: list[str],
    denied_user: str,
) -> dict[str, object]:
    create_expires = future_iso(days=60)
    create_response = client.post(
        "/api/v1/resource-management/filesystems",
        headers=headers,
        json_body={
            "requester_id": "phase15-user",
            "payload": {
                "storage_name": target.storage_name,
                "directory_name": directory_name,
                "resource_type": "user",
                "users": allowed_users,
                "validation_denied_users": [denied_user],
                "quota": {"capacity_bytes": 8 * 1024 * 1024, "file_count": 32},
                "expires_at": create_expires,
            },
        },
    )
    assert_equal(create_response.status_code, 202, "filesystem create submit status")
    create_terminal = wait_request(repository, create_response.json()["request_id"])
    assert_equal(create_terminal["status"], LifecycleState.SUCCEEDED.value, "filesystem create terminal")

    update_expires = future_iso(days=120)
    update_response = client.patch(
        f"/api/v1/resource-management/filesystems/{target.storage_name}/{directory_name}",
        headers=headers,
        json_body={
            "requester_id": "phase15-user",
            "payload": {"expires_at": update_expires, "reason": "phase15 expiry update"},
        },
    )
    assert_equal(update_response.status_code, 202, "filesystem expiry update submit status")
    update_terminal = wait_request(repository, update_response.json()["request_id"])
    assert_equal(update_terminal["status"], LifecycleState.SUCCEEDED.value, "filesystem update terminal")

    resource = repository.get_resource(ResourceKind.FILESYSTEM.value, f"{target.storage_name}:{directory_name}")
    assert_equal(
        resource["desired_state"]["expires_at"],
        normalize_z(update_expires),
        "filesystem desired expires_at",
    )
    return {
        "resource_key": resource["resource_key"],
        "create_request": create_terminal["request_id"],
        "update_request": update_terminal["request_id"],
        "expires_at": resource["desired_state"]["expires_at"],
    }


def verify_kubernetes_expiry_lifecycle(
    *,
    client: HttpClient,
    repository: DmsRepository,
    headers: dict[str, str],
    storage_name: str,
    namespace_name: str,
    import_namespace: str,
    sweep_namespace: str,
) -> dict[str, object]:
    create_expires = future_iso(days=30)
    create_response = submit_namespace_quota(
        client,
        headers,
        storage_name=storage_name,
        namespace_name=namespace_name,
        expires_at=create_expires,
    )
    create_terminal = wait_request(repository, create_response["request_id"])
    assert_equal(create_terminal["status"], LifecycleState.SUCCEEDED.value, "kubernetes quota create terminal")
    live_quota = kubectl_json(
        "c2-control",
        ["kubectl", "-n", namespace_name, "get", "resourcequota", "dms-storage-quota", "-o", "json"],
    )
    assert_equal(
        live_quota["metadata"]["annotations"]["dms.io/expires-at"],
        normalize_z(create_expires),
        "ResourceQuota expires annotation",
    )

    update_response = client.patch(
        f"/api/v1/resource-management/kubernetes/namespace-quotas/cluster-b/{namespace_name}",
        headers=headers,
        json_body={
            "requester_id": "phase15-user",
            "payload": {
                "quota": {"requests_storage_bytes": 256 * 1024 * 1024, "pvc_count": 4},
                "storage_class_quotas": [
                    {
                        "storage_name": storage_name,
                        "requests_storage_bytes": 256 * 1024 * 1024,
                        "pvc_count": 4,
                    }
                ],
            },
        },
    )
    assert_equal(update_response.status_code, 202, "kubernetes quota update submit status")
    update_terminal = wait_request(repository, update_response.json()["request_id"])
    assert_equal(update_terminal["status"], LifecycleState.SUCCEEDED.value, "kubernetes quota update terminal")
    resource = repository.get_resource(ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value, f"cluster-b:{namespace_name}")
    assert_equal(
        resource["desired_state"]["expires_at"],
        normalize_z(create_expires),
        "kubernetes update preserved expires_at",
    )

    prepare_import_resourcequota(import_namespace, storage_name)
    import_response = client.post(
        f"/api/v1/resource-management/kubernetes/namespace-quotas/cluster-b/{import_namespace}:import",
        headers=headers,
        json_body={
            "requester_id": "phase15-user",
            "payload": {
                "resource_quota_name": "dms-storage-quota",
                "storage_class_quotas": [{"storage_name": storage_name}],
            },
        },
    )
    assert_equal(import_response.status_code, 202, "kubernetes quota import submit status")
    import_terminal = wait_request(repository, import_response.json()["request_id"])
    assert_equal(import_terminal["status"], LifecycleState.SUCCEEDED.value, "kubernetes quota import terminal")
    imported = repository.get_resource(ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value, f"cluster-b:{import_namespace}")
    assert_true(bool(imported["desired_state"].get("expires_at")), "import default expires_at recorded")

    sweep_expires = future_iso(seconds=20)
    sweep_response = submit_namespace_quota(
        client,
        headers,
        storage_name=storage_name,
        namespace_name=sweep_namespace,
        expires_at=sweep_expires,
    )
    sweep_create = wait_request(repository, sweep_response["request_id"])
    assert_equal(sweep_create["status"], LifecycleState.SUCCEEDED.value, "sweep target create terminal")
    wait_until_after(sweep_expires)

    expiring_response = client.get(
        "/api/v1/operations/kubernetes/namespace-quotas/expiring",
        headers=headers,
        params={
            "cluster_name": "cluster-b",
            "namespace_name": sweep_namespace,
            "status": "expired",
        },
    )
    assert_equal(expiring_response.status_code, 200, "kubernetes expiring query status")
    expired_rows = expiring_response.json()
    assert_true(any(row["resource_key"] == f"cluster-b:{sweep_namespace}" for row in expired_rows), "expired quota listed")

    action_required = client.get("/api/v1/operations/action-required", headers=headers)
    assert_equal(action_required.status_code, 200, "action-required status")
    assert_true(
        any(issue.get("issue_type") == "kubernetes_quota_expired_unblocked" and issue.get("namespace_name") == sweep_namespace for issue in action_required.json()),
        "expired unblocked quota appears in action-required",
    )

    sweep_submit = client.post(
        "/api/v1/resource-management/kubernetes/namespace-quotas:expiration-sweep",
        headers=headers,
        json_body={
            "requester_id": "phase15-user",
            "payload": {
                "scope": {"cluster_name": "cluster-b", "namespace_name": sweep_namespace},
                "action": "block",
                "dry_run": False,
                "max_targets": 10,
                "reason": "phase15 expiry sweep",
            },
        },
    )
    assert_equal(sweep_submit.status_code, 202, "expiration sweep submit status")
    sweep_terminal = wait_request(repository, sweep_submit.json()["request_id"])
    assert_equal(sweep_terminal["status"], LifecycleState.SUCCEEDED.value, "expiration sweep terminal")
    blocked_quota = kubectl_json(
        "c2-control",
        ["kubectl", "-n", sweep_namespace, "get", "resourcequota", "dms-storage-quota", "-o", "json"],
    )
    hard = blocked_quota["spec"]["hard"]
    assert_equal(hard["requests.storage"], "0", "sweep zeroed requests.storage")
    assert_equal(hard["persistentvolumeclaims"], "0", "sweep zeroed pvc count")

    return {
        "create_request": create_terminal["request_id"],
        "update_request": update_terminal["request_id"],
        "import_request": import_terminal["request_id"],
        "sweep_request": sweep_terminal["request_id"],
        "expired_query_count": len(expired_rows),
        "sweep_hard": hard,
    }


def ensure_filesystem_mapping(
    client: HttpClient,
    repository: DmsRepository,
    target: phase10.FilesystemTarget,
) -> dict:
    mapping = repository.get_storage_mapping(target.storage_name)
    if mapping and (mapping.get("readiness") or {}).get("resource_management") == "Ready":
        return {
            "storage_name": target.storage_name,
            "status": mapping.get("sanity_status"),
            "readiness": mapping.get("readiness"),
            "reused": True,
        }
    summary = phase10.upsert_mapping(client, target)
    summary["reused"] = False
    return summary


def register_longhorn_mapping(repository: DmsRepository, *, storage_name: str) -> None:
    readiness = {
        "resource_management": "Ready",
        "data_management": "Ready",
        "inventory": "Ready",
    }
    repository.upsert_storage_mapping(
        StorageMappingInput(
            storage_name=storage_name,
            backend_template={"backend_type": "longhorn", "csi_driver": "driver.longhorn.io"},
            cluster_name="cluster-b",
            storage_class_name="testbed-longhorn",
            sanity_status="Ready",
        ),
        actor="phase15-verifier",
        sanity_result={
            "storage_name": storage_name,
            "status": "Ready",
            "readiness": readiness,
            "kubernetes_observed": {
                "cluster_name": "cluster-b",
                "storage_class_name": "testbed-longhorn",
                "storage_class_exists": True,
                "provisioner": "driver.longhorn.io",
            },
            "agent_observed": {
                "rm_readiness": "Ready",
                "dm_readiness": "Ready",
                "rm_candidates": [{"cluster_name": "cluster-b", "node_name": "worker"}],
                "dm_candidates": [{"cluster_name": "cluster-b", "node_name": "worker"}],
            },
            "checks": [],
            "warnings": [],
            "errors": [],
        },
        readiness=readiness,
    )


def submit_namespace_quota(
    client: HttpClient,
    headers: dict[str, str],
    *,
    storage_name: str,
    namespace_name: str,
    expires_at: str,
) -> dict:
    response = client.post(
        "/api/v1/resource-management/kubernetes/namespace-quotas",
        headers=headers,
        json_body={
            "requester_id": "phase15-user",
            "payload": {
                "cluster_name": "cluster-b",
                "namespace_name": namespace_name,
                "allow_namespace_create": True,
                "resource_type": "user",
                "quota": {"requests_storage_bytes": 128 * 1024 * 1024, "pvc_count": 2},
                "storage_class_quotas": [
                    {
                        "storage_name": storage_name,
                        "requests_storage_bytes": 128 * 1024 * 1024,
                        "pvc_count": 2,
                    }
                ],
                "expires_at": expires_at,
            },
        },
    )
    assert_equal(response.status_code, 202, "namespace quota submit status")
    return response.json()


def prepare_import_resourcequota(namespace_name: str, storage_name: str) -> None:
    resource_key = f"cluster-b:{namespace_name}"
    manifest = {
        "apiVersion": "v1",
        "kind": "ResourceQuota",
        "metadata": {
            "name": "dms-storage-quota",
            "namespace": namespace_name,
            "labels": {
                "app.kubernetes.io/managed-by": "dms",
                "dms.io/resource-kind": "kubernetes-namespace-quota",
            },
            "annotations": {
                "dms.io/resource-key": resource_key,
                "dms.io/request-id": "phase15-manual-import-prep",
                "dms.io/storage-names": storage_name,
            },
        },
        "spec": {
            "hard": {
                "requests.storage": "128Mi",
                "persistentvolumeclaims": "2",
                "testbed-longhorn.storageclass.storage.k8s.io/requests.storage": "128Mi",
            }
        },
    }
    run_checked(["ssh", "c2-control", "kubectl", "create", "namespace", namespace_name])
    run_checked(
        ["ssh", "c2-control", "kubectl", "apply", "-f", "-"],
        input_text=json.dumps(manifest, sort_keys=True),
    )


def wait_request(
    repository: DmsRepository, request_id: str, *, timeout_seconds: int = 240
) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    last = None
    while time.monotonic() < deadline:
        last = repository.get_request(request_id)
        if last["status"] in TERMINAL_STATES:
            return last
        time.sleep(2)
    raise AssertionError(f"request {request_id} did not reach terminal state: {last}")


def wait_until_after(timestamp: str) -> None:
    expires = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone(UTC)
    while datetime.now(UTC) <= expires + timedelta(seconds=2):
        time.sleep(1)


def future_iso(*, days: int = 0, seconds: int = 0) -> str:
    return (datetime.now(UTC) + timedelta(days=days, seconds=seconds)).isoformat()


def normalize_z(value: str) -> str:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC).isoformat()


def kubectl_json(host: str, command: list[str]) -> dict:
    completed = run_checked(["ssh", host, *command])
    return json.loads(completed.stdout)


def run_checked(
    command: list[str], *, input_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"command failed ({completed.returncode}): {' '.join(command)}\n"
            f"stdout={completed.stdout}\nstderr={completed.stderr}"
        )
    return completed


if __name__ == "__main__":
    raise SystemExit(main())
