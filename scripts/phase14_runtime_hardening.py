from __future__ import annotations

import json
import os
from dataclasses import dataclass
from subprocess import run
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from dms.config import Settings
from dms.db import Database
from dms.domain import LifecycleState, StorageMappingInput
from dms.repositories import DmsRepository


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

    def json(self) -> dict:
        return json.loads(self.body or "{}")


@dataclass
class HttpClient:
    base_url: str

    def get(self, path: str, *, headers: dict[str, str] | None = None) -> HttpResponse:
        return self.request("GET", path, headers=headers)

    def post(
        self,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: dict | None = None,
    ) -> HttpResponse:
        return self.request("POST", path, headers=headers, json_body=json_body)

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

    namespace_name = f"dms-phase14-quota-{token}"
    unknown_directory = f"dms-phase14-unknown-{token}"
    summary: dict[str, object] = {"token": token}
    try:
        _register_mapping(
            repository,
            storage_name="phase14-longhorn-b",
            backend_template={
                "backend_type": "longhorn",
                "csi_driver": "driver.longhorn.io",
            },
            cluster_name="cluster-b",
            storage_class_name="testbed-longhorn",
        )
        longhorn_mapping = repository.get_storage_mapping("phase14-longhorn-b")
        assert_equal("longhorn mapping status", longhorn_mapping["sanity_status"], "Ready")
        summary["longhorn_mapping"] = {
            "storage_name": longhorn_mapping["storage_name"],
            "backend_type": longhorn_mapping["backend_template"]["backend_type"],
            "readiness": longhorn_mapping["readiness"],
        }

        quota_request = create_namespace_quota(
            client,
            headers,
            storage_name="phase14-longhorn-b",
            namespace_name=namespace_name,
        )
        quota_request_id = quota_request["request_id"]
        quota_terminal = wait_request(repository, quota_request_id)
        assert_equal(
            "longhorn quota request terminal",
            quota_terminal["status"],
            LifecycleState.SUCCEEDED.value,
        )
        live_quota = kubectl_json(
            "c2-control",
            [
                "kubectl",
                "-n",
                namespace_name,
                "get",
                "resourcequota",
                "dms-storage-quota",
                "-o",
                "json",
            ],
        )
        hard = live_quota.get("spec", {}).get("hard", {})
        assert_equal("live ResourceQuota requests.storage", hard.get("requests.storage"), "128Mi")
        assert_equal(
            "live ResourceQuota storageclass hard",
            hard.get("testbed-longhorn.storageclass.storage.k8s.io/requests.storage"),
            "128Mi",
        )
        summary["quota_request"] = quota_terminal
        summary["live_resourcequota_hard"] = hard

        _register_mapping(
            repository,
            storage_name="phase14-unknown-a",
            backend_template={
                "backend_type": "cephfss",
                "csi_driver": "rook-ceph.cephfs.csi.ceph.com",
                "mount_path": "/mnt/testbed-cephfs",
                "managed_root": "/mnt/testbed-cephfs/dms-phase14",
            },
            cluster_name="cluster-a",
            storage_class_name="testbed-cephfs",
        )
        unknown_mapping = repository.get_storage_mapping("phase14-unknown-a")
        assert_equal("unknown mapping status", unknown_mapping["sanity_status"], "Ready")
        unknown_request = create_filesystem(
            client,
            headers,
            storage_name="phase14-unknown-a",
            directory_name=unknown_directory,
        )
        unknown_terminal = wait_request(repository, unknown_request["request_id"])
        assert_equal(
            "unknown backend terminal",
            unknown_terminal["status"],
            LifecycleState.BACKEND_APPLY_FAILED.value,
        )
        action_required = client.get("/api/v1/operations/action-required", headers=headers)
        assert_equal("action-required status", action_required.status_code, 200)
        action_issues = action_required.json()
        assert_true(
            "unknown backend appears in action-required",
            any(
                issue.get("request_id") == unknown_request["request_id"]
                and issue.get("status") == LifecycleState.BACKEND_APPLY_FAILED.value
                for issue in action_issues
            ),
        )
        summary["unknown_backend_request"] = unknown_terminal
        summary["action_required_match_count"] = sum(
            1 for issue in action_issues if issue.get("request_id") == unknown_request["request_id"]
        )

        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0
    finally:
        run(
            ["ssh", "c2-control", "kubectl", "delete", "namespace", namespace_name, "--ignore-not-found=true"],
            check=False,
        )


def _register_mapping(
    repository: DmsRepository,
    *,
    storage_name: str,
    backend_template: dict,
    cluster_name: str,
    storage_class_name: str,
) -> None:
    sanity = {
        "storage_name": storage_name,
        "status": "Ready",
        "checked_at": "2026-05-31T00:00:00+09:00",
        "kubernetes_observed": {
            "cluster_name": cluster_name,
            "storage_class_name": storage_class_name,
            "storage_class_exists": True,
            "provisioner": backend_template.get("csi_driver"),
        },
        "agent_observed": {
            "fresh_reports": 1,
            "stale_reports": 0,
            "rm_readiness": "Ready",
            "dm_readiness": "Ready",
            "rm_candidates": [{"cluster_name": cluster_name, "node_name": "rm-phase14"}],
            "dm_candidates": [{"cluster_name": cluster_name, "node_name": "dm-phase14"}],
        },
        "readiness": {
            "resource_management": "Ready",
            "data_management": "Ready",
            "inventory": "Ready",
        },
        "checks": [],
        "warnings": [],
        "errors": [],
    }
    repository.upsert_storage_mapping(
        StorageMappingInput(
            storage_name=storage_name,
            backend_template=backend_template,
            cluster_name=cluster_name,
            storage_class_name=storage_class_name,
            sanity_status="Ready",
        ),
        actor="phase14-verifier",
        sanity_result=sanity,
        readiness=sanity["readiness"],
    )


def create_namespace_quota(
    client: HttpClient,
    headers: dict[str, str],
    *,
    storage_name: str,
    namespace_name: str,
) -> dict:
    response = client.post(
        "/api/v1/resource-management/kubernetes/namespace-quotas",
        headers=headers,
        json_body={
            "requester_id": "phase14-user",
            "payload": {
                "cluster_name": "cluster-b",
                "namespace_name": namespace_name,
                "allow_namespace_create": True,
                "storage_class_quotas": [
                    {
                        "storage_name": storage_name,
                        "requests_storage_bytes": 128 * 1024 * 1024,
                        "pvc_count": 2,
                    }
                ],
                "quota": {
                    "requests_storage_bytes": 128 * 1024 * 1024,
                    "pvc_count": 2,
                },
            },
        },
    )
    assert_equal("namespace quota submit status", response.status_code, 202)
    return response.json()


def create_filesystem(
    client: HttpClient,
    headers: dict[str, str],
    *,
    storage_name: str,
    directory_name: str,
) -> dict:
    response = client.post(
        "/api/v1/resource-management/filesystems",
        headers=headers,
        json_body={
            "requester_id": "phase14-user",
            "payload": {
                "storage_name": storage_name,
                "directory_name": directory_name,
                "resource_type": "user",
                "users": ["alice", "bob"],
            },
        },
    )
    assert_equal("filesystem submit status", response.status_code, 202)
    return response.json()


def wait_request(
    repository: DmsRepository, request_id: str, *, timeout_seconds: int = 180
) -> dict:
    deadline = time.monotonic() + timeout_seconds
    last = None
    while time.monotonic() < deadline:
        last = repository.get_request(request_id)
        if last["status"] in TERMINAL_STATES:
            return last
        time.sleep(2)
    raise TimeoutError(f"request {request_id} did not finish, last={last}")


def kubectl_json(host: str, argv: list[str]) -> dict:
    completed = run(["ssh", host, *argv], check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)


def assert_equal(label: str, actual, expected) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def assert_true(label: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(label)


if __name__ == "__main__":
    raise SystemExit(main())
