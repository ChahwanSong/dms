from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))

from dms.config import Settings  # noqa: E402
from dms.db import Database  # noqa: E402
from dms.domain import LifecycleState, OperationKind, ResourceKind  # noqa: E402
from dms.repositories import DmsRepository, ObservabilityRepository  # noqa: E402
from scripts import phase10_ceph_host_filesystem_rm as phase10  # noqa: E402
from scripts import phase12_cephfs_quota_import as phase12  # noqa: E402
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

    def get(self, path: str, *, headers: dict[str, str] | None = None) -> HttpResponse:
        return self.request("GET", path, headers=headers)

    def post(
        self,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        json: dict | None = None,
    ) -> HttpResponse:
        return self.request("POST", path, headers=headers, json=json)

    def request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        json: dict | None = None,
    ) -> HttpResponse:
        body = None
        request_headers = dict(headers or {})
        if json is not None:
            body = json_dumps(json).encode("utf-8")
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
    observability = ObservabilityRepository(Database(settings.observability_database_url))
    headers = {"x-dms-actor": "api-client"}
    if settings.auth_shared_token:
        headers["authorization"] = f"Bearer {settings.auth_shared_token}"
    phase10.API_HEADERS.clear()
    phase10.API_HEADERS.update(headers)

    api_url = os.environ["DMS_PHASE13_API_URL"].rstrip("/")
    namespace = os.getenv("DMS_PHASE13_NAMESPACE", "dms-phase13")
    client = HttpClient(api_url)
    token = uuid4().hex[:8]
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
    c2 = phase10.FilesystemTarget(
        storage_name="cephfs-b",
        cluster_name="cluster-b",
        node_name=os.getenv("DMS_PHASE13_C2_NODE", os.getenv("DMS_PHASE10_C2_NODE", "c2-worker")),
        mount_path=os.getenv(
            "DMS_PHASE13_C2_CEPH_MOUNT_PATH",
            os.getenv("DMS_PHASE10_C2_CEPH_MOUNT_PATH", "/mnt/testbed-cephfs-c2"),
        ),
    )
    ldap_password = os.environ["DMS_LDAP_BIND_PASSWORD"]
    created_users: list[str] = []
    created_directories: list[tuple[phase10.FilesystemTarget, str]] = []
    created_groups: list[str] = []

    patch_phase12_waiters(repository)
    try:
        host_mounts = [phase10.check_host_mount(c1), phase10.check_host_mount(c2)]
        quota_tools = [phase12.ensure_quota_tools(c1), phase12.ensure_quota_tools(c2)]
        quota_probe = [phase12.probe_cephfs_quota(c1, token), phase12.probe_cephfs_quota(c2, token)]
        users = phase10.ensure_ldap_users(token=token, ldap_password=ldap_password)
        created_users = users["created_users"]
        phase10.verify_sssd_users(
            [c1.node_name, c2.node_name],
            users["allowed_users"] + [users["denied_user"]],
        )
        reports = phase10.wait_for_phase10_reports(client, [c1, c2])
        mapping_summaries = [phase10.upsert_mapping(client, c1), phase10.upsert_mapping(client, c2)]

        deployment_before = deployment_snapshot(namespace)
        lifecycle_summaries = []
        for base, suffix in [(c1, "a"), (c2, "b")]:
            directory_name = f"phase13-quota-{suffix}-{token}"
            target = phase12.Phase12Target(base, directory_name, f"dms-phase13-{directory_name}")
            created_directories.append((base, directory_name))
            lifecycle_summaries.append(
                phase12.verify_quota_lifecycle(
                    client=client,
                    repository=repository,
                    rm_worker=None,
                    target=target,
                    allowed_users=users["allowed_users"],
                    denied_user=users["denied_user"],
                    headers=headers,
                )
            )
            created_directories.pop()

        scale_summary = verify_worker_scale(namespace, client, repository, c1, users, headers, token)

        assign_target = phase12.Phase12Target(
            c1,
            f"phase13-assign-{token}",
            f"dms-phase13-assign-{token}",
        )
        created_directories.append((c1, assign_target.directory_name))
        assign_summary = phase12.verify_assign_quota(
            client=client,
            repository=repository,
            rm_worker=None,
            target=assign_target,
            headers=headers,
        )
        created_directories.pop()

        import_target = phase12.Phase12Target(
            c2,
            f"phase13-import-{token}",
            f"dms-phase13-import-{token}",
        )
        created_groups.append(import_target.group_name)
        created_directories.append((c2, import_target.directory_name))
        import_summary = phase12.verify_full_import(
            client=client,
            repository=repository,
            rm_worker=None,
            target=import_target,
            allowed_users=users["allowed_users"],
            denied_user=users["denied_user"],
            headers=headers,
            settings=settings,
        )
        created_directories.pop()

        unsafe_summary = phase12.verify_unsafe_nested_path_rejected(
            client=client,
            repository=repository,
            headers=headers,
            storage_name=c1.storage_name,
        )
        restart_summary = restart_worker(namespace)
        stale_summary = verify_stale_query(client, repository, headers, namespace, c1, token)

        summary = {
            "status": "ok",
            "operational_database_url": mask_url(settings.database_url),
            "observability_database_url": mask_url(settings.observability_database_url),
            "api_url": api_url,
            "host_mounts": host_mounts,
            "quota_tools": quota_tools,
            "quota_probe": quota_probe,
            "ldap_users": {
                "allowed_users": users["allowed_users"],
                "denied_user": users["denied_user"],
                "created_users": created_users,
            },
            "agent_reports": reports,
            "storage_mappings": mapping_summaries,
            "deployment_before": deployment_before,
            "quota_lifecycle": lifecycle_summaries,
            "worker_scale": scale_summary,
            "assign_quota": assign_summary,
            "full_import": import_summary,
            "unsafe_case": unsafe_summary,
            "worker_restart": restart_summary,
            "stale_query": stale_summary,
            "runs": summarize_runs(repository),
            "gpfs_live_verification": {
                "status": "skipped",
                "reason": "testbed has no IBM GPFS / IBM Storage Scale cluster",
            },
        }
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0
    finally:
        for target, directory_name in created_directories:
            phase10.cleanup_directory(target, directory_name)
        for group_name in created_groups:
            phase12.delete_ldap_group(group_name, ldap_password)
        for username in created_users:
            phase10.delete_ldap_user(username, ldap_password)


def patch_phase12_waiters(repository: DmsRepository) -> None:
    def run_success(repository_arg, rm_worker, request_id: str, label: str) -> None:
        wait_request_status(repository, request_id, LifecycleState.SUCCEEDED.value, label)
        run = latest_run_for_request(repository, request_id)
        assert_true(run is not None, f"{label} run recorded")
        assert_true(run["worker_id"], f"{label} worker id recorded")

    def run_rejected(repository_arg, request_id: str, label: str) -> None:
        wait_request_status(repository, request_id, LifecycleState.REJECTED.value, label)

    phase12.run_success = run_success
    phase12.run_planner_rejected = run_rejected


def wait_request_status(
    repository: DmsRepository,
    request_id: str,
    expected_status: str,
    label: str,
    *,
    timeout_seconds: int = 240,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_status = ""
    while time.monotonic() < deadline:
        request = repository.get_request(request_id)
        last_status = request["status"]
        if last_status == expected_status:
            return
        if last_status in TERMINAL_STATES and last_status != expected_status:
            results = repository.get_results(request_id)
            raise AssertionError(
                f"{label}: expected {expected_status}, got {last_status}: {results}"
            )
        time.sleep(2)
    raise AssertionError(f"{label}: timed out waiting for {expected_status}, last={last_status}")


def latest_run_for_request(repository: DmsRepository, request_id: str) -> dict | None:
    for run in repository.list_runs(limit=200):
        if run["request_id"] == request_id:
            return run
    return None


def verify_worker_scale(
    namespace: str,
    client: HttpClient,
    repository: DmsRepository,
    target_base: phase10.FilesystemTarget,
    users: dict,
    headers: dict[str, str],
    token: str,
) -> dict:
    check_request_ids = []
    targets = []
    for idx in range(2):
        target = phase12.Phase12Target(
            target_base,
            f"phase13-scale-{idx}-{token}",
            f"dms-phase13-scale-{idx}-{token}",
        )
        targets.append(target)
        seed_filesystem_resource(repository, target)

    try:
        kubectl("cluster-a", f"kubectl -n {namespace} scale deployment/dms-rm-worker --replicas=2")
        kubectl(
            "cluster-a",
            f"kubectl -n {namespace} rollout status deployment/dms-rm-worker --timeout=180s",
        )
        for target in targets:
            check_request_ids.append(phase12.check_quota(client=client, target=target, headers=headers))
        for idx, request_id in enumerate(check_request_ids):
            wait_request_status(
                repository,
                request_id,
                LifecycleState.SUCCEEDED.value,
                f"scale check {idx}",
            )
        runs_by_request = {
            request_id: [
                run
                for run in repository.list_runs(limit=200)
                if run["request_id"] == request_id
            ]
            for request_id in check_request_ids
        }
        for request_id, runs in runs_by_request.items():
            assert_equal(len(runs), 1, f"{request_id} has one claimed run")
        worker_ids = sorted({runs[0]["worker_id"] for runs in runs_by_request.values()})
    finally:
        kubectl("cluster-a", f"kubectl -n {namespace} scale deployment/dms-rm-worker --replicas=1")
        kubectl(
            "cluster-a",
            f"kubectl -n {namespace} rollout status deployment/dms-rm-worker --timeout=180s",
        )
    return {
        "check_request_ids": check_request_ids,
        "worker_ids": worker_ids,
    }


def restart_worker(namespace: str) -> dict:
    before = kubectl("cluster-a", f"kubectl -n {namespace} get pods -l app.kubernetes.io/name=dms-rm-worker -o name")
    first = before.splitlines()[0].strip()
    kubectl("cluster-a", f"kubectl -n {namespace} delete {first}")
    kubectl("cluster-a", f"kubectl -n {namespace} rollout status deployment/dms-rm-worker --timeout=180s")
    after = kubectl("cluster-a", f"kubectl -n {namespace} get pods -l app.kubernetes.io/name=dms-rm-worker -o name")
    return {"deleted_pod": first, "pods_after": after.splitlines()}


def verify_stale_query(
    client: HttpClient,
    repository: DmsRepository,
    headers: dict[str, str],
    namespace: str,
    target_base: phase10.FilesystemTarget,
    token: str,
) -> dict:
    kubectl("cluster-a", f"kubectl -n {namespace} scale deployment/dms-rm-worker --replicas=0")
    wait_worker_replicas(namespace, 0)
    target = phase12.Phase12Target(
        target_base,
        f"phase13-stale-{token}",
        f"dms-phase13-stale-{token}",
    )
    seed_filesystem_resource(repository, target)
    request_id = phase12.check_quota(client=client, target=target, headers=headers)
    plan = wait_plan(repository, request_id, "stale fixture planned")
    run_id = repository.claim_plan(
        plan_id=plan["plan_id"],
        worker_id="phase13-expired-worker",
        executor_id="phase13-expired-worker",
        lease_seconds=-1,
    )
    kubectl("cluster-a", f"kubectl -n {namespace} scale deployment/dms-rm-worker --replicas=1")
    kubectl("cluster-a", f"kubectl -n {namespace} rollout status deployment/dms-rm-worker --timeout=180s")
    wait_request_status(
        repository,
        request_id,
        LifecycleState.STALE_CLAIM.value,
        "stale fixture lease expiry",
    )
    response = client.get("/api/v1/operations/runs/stale", headers=headers)
    assert_equal(response.status_code, 200, "stale run query")
    stale_runs = response.json()
    assert_true(
        any(run["run_id"] == run_id for run in stale_runs),
        "expired run appears in stale query",
    )
    return {
        "fixture_request_id": request_id,
        "plan_id": plan["plan_id"],
        "run_id": run_id,
        "query_count": len(stale_runs),
        "request_status": repository.get_request(request_id)["status"],
    }


def seed_filesystem_resource(repository: DmsRepository, target: phase12.Phase12Target) -> None:
    desired = {
        "storage_name": target.base.storage_name,
        "directory_name": target.directory_name,
        "resource_kind": ResourceKind.FILESYSTEM.value,
        "resource_key": target.resource_key,
        "quota": {
            "capacity_bytes": phase12.INITIAL_QUOTA_BYTES,
            "file_count": phase12.FILE_COUNT_QUOTA,
        },
    }
    repository.upsert_resource(
        resource_kind=ResourceKind.FILESYSTEM.value,
        resource_key=target.resource_key,
        desired_state=desired,
        applied_state={"seeded_for": "phase13-stale-lease"},
        observed_state={
            "adapter": "phase13-stale-fixture",
            "path": target.directory_path,
            "exists": True,
        },
        status=LifecycleState.SUCCEEDED.value,
    )


def wait_plan(
    repository: DmsRepository,
    request_id: str,
    label: str,
    *,
    timeout_seconds: int = 120,
) -> dict:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        plan = repository.get_plan_by_request(request_id)
        if plan is not None:
            return plan
        request = repository.get_request(request_id)
        if request["status"] in TERMINAL_STATES:
            raise AssertionError(f"{label}: request reached {request['status']} before plan")
        time.sleep(2)
    raise AssertionError(f"{label}: timed out waiting for plan")


def wait_worker_replicas(namespace: str, expected: int, *, timeout_seconds: int = 180) -> list[str]:
    deadline = time.monotonic() + timeout_seconds
    last_pods: list[str] = []
    while time.monotonic() < deadline:
        output = kubectl(
            "cluster-a",
            f"kubectl -n {namespace} get pods -l app.kubernetes.io/name=dms-rm-worker "
            "--field-selector=status.phase=Running -o name",
        )
        last_pods = [line.strip() for line in output.splitlines() if line.strip()]
        if len(last_pods) == expected:
            return last_pods
        time.sleep(2)
    raise AssertionError(
        f"timed out waiting for {expected} running rm-worker pod(s), last={last_pods}"
    )


def deployment_snapshot(namespace: str) -> dict:
    return {
        "cluster_a": kubectl("cluster-a", f"kubectl -n {namespace} get deploy,pods -o wide"),
        "cluster_b": kubectl("cluster-b", f"kubectl -n {namespace} get ds,pods -o wide"),
    }


def summarize_runs(repository: DmsRepository) -> list[dict]:
    return [
        {
            "run_id": run["run_id"],
            "request_id": run["request_id"],
            "worker_id": run["worker_id"],
            "state": run["state"],
        }
        for run in repository.list_runs(limit=50)
    ]


def kubectl(cluster: str, command: str) -> str:
    host = "c1-control" if cluster == "cluster-a" else "c2-control"
    completed = subprocess.run(
        ["ssh", host, command],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr.strip() or completed.stdout.strip())
    return completed.stdout.strip()


def json_dumps(payload: dict) -> str:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


if __name__ == "__main__":
    raise SystemExit(main())
