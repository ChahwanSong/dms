from __future__ import annotations

import json
import os
import time
from typing import Any
from urllib import error, request


API_URL = os.environ.get("DMS_PHASE21_API_URL") or os.environ["DMS_PHASE20_API_URL"]
API_URL = API_URL.rstrip("/")
TOKEN = os.environ.get("DMS_PHASE21_AUTH_TOKEN") or os.environ["DMS_PHASE20_AUTH_TOKEN"]
REQUESTER = os.environ.get("DMS_PHASE21_REQUESTER", "phase21-alice")
STORAGE_NAME = os.environ.get("DMS_PHASE21_STORAGE_NAME", "cephfs-a")
SPLIT_SOURCE_STORAGE = os.environ.get("DMS_PHASE21_SPLIT_SOURCE_STORAGE", "phase21-src")
SPLIT_DESTINATION_STORAGE = os.environ.get(
    "DMS_PHASE21_SPLIT_DESTINATION_STORAGE", "phase21-dst"
)
CEPH_MOUNT = os.environ.get("DMS_PHASE21_CEPH_MOUNT") or os.environ["DMS_PHASE20_CEPH_MOUNT"]
SCAN_TARGET_PATH = os.environ["DMS_PHASE21_SCAN_TARGET_PATH"]
SYNC_SOURCE_PATH = os.environ["DMS_PHASE21_SYNC_SOURCE_PATH"]
SYNC_DESTINATION_PATH = os.environ["DMS_PHASE21_SYNC_DESTINATION_PATH"]
RM_TARGET_PATH = os.environ["DMS_PHASE21_RM_TARGET_PATH"]
NSYNC_SOURCE_PATH = os.environ.get("DMS_PHASE21_NSYNC_SOURCE_PATH", "input")
NSYNC_DESTINATION_PATH = os.environ.get("DMS_PHASE21_NSYNC_DESTINATION_PATH", "output")


def main() -> int:
    headers = actor_headers("phase21-operator")
    wait_for_health(headers)
    wait_for_agent_report(headers)
    upsert_identity(headers)
    upsert_storage_mapping(headers, STORAGE_NAME)
    assert_resource_fields_rejected(headers)

    scan_request_id = submit_scan(headers)
    scan_done = wait_for_job(
        headers,
        operation="data.scan",
        detail_kind="scan",
        request_id=scan_request_id,
        expected_state="Succeeded",
    )
    assert_equal(scan_done["selected_tool"], "dscan", "scan selected dscan")
    assert_phase21_resource_evidence(scan_done, "scan")
    assert_true(
        scan_done["result_summary"]["scan_report_uri"].endswith("/dscan-report.json"),
        "scan report URI recorded",
    )

    sync_request_id = submit_sync(headers)
    sync_preview = wait_for_job(
        headers,
        operation="data.sync",
        detail_kind="sync",
        request_id=sync_request_id,
        expected_state="ConfirmPending",
    )
    assert_equal(sync_preview["selected_tool"], "dsync", "sync selected dsync")
    assert_phase21_resource_evidence(sync_preview, "sync preview")
    confirm_job(headers, sync_preview)
    sync_done = wait_for_job(
        headers,
        operation="data.sync",
        detail_kind="sync",
        request_id=sync_request_id,
        expected_state="Succeeded",
    )
    assert_phase21_resource_evidence(sync_done, "sync execution")
    assert_equal(
        sync_done["result_summary"]["execution"]["summary"]["dry_run"],
        False,
        "sync execution is not dry-run",
    )

    rm_request_id = submit_rm(headers)
    rm_preview = wait_for_job(
        headers,
        operation="data.rm",
        detail_kind="rm",
        request_id=rm_request_id,
        expected_state="ConfirmPending",
    )
    assert_equal(rm_preview["selected_tool"], "drm", "rm selected drm")
    assert_phase21_resource_evidence(rm_preview, "rm preview")
    confirm_job(headers, rm_preview)
    rm_done = wait_for_job(
        headers,
        operation="data.rm",
        detail_kind="rm",
        request_id=rm_request_id,
        expected_state="Succeeded",
    )
    assert_phase21_resource_evidence(rm_done, "rm execution")
    assert_equal(
        rm_done["result_summary"]["execution"]["summary"]["target_absent"],
        True,
        "rm execution reports target absent",
    )

    submit_split_role_agent_report(headers, node_name="phase21-src", storage_name=SPLIT_SOURCE_STORAGE)
    submit_split_role_agent_report(
        headers, node_name="phase21-dst", storage_name=SPLIT_DESTINATION_STORAGE
    )
    upsert_storage_mapping(
        headers,
        SPLIT_SOURCE_STORAGE,
        storage_class_name=f"testbed-cephfs-{SPLIT_SOURCE_STORAGE}",
    )
    upsert_storage_mapping(
        headers,
        SPLIT_DESTINATION_STORAGE,
        storage_class_name=f"testbed-cephfs-{SPLIT_DESTINATION_STORAGE}",
    )
    nsync_request_id = submit_nsync_deferred(headers)
    nsync_job = wait_for_job(
        headers,
        operation="data.sync",
        detail_kind="sync",
        request_id=nsync_request_id,
        expected_state="PreflightFailed",
    )
    assert_equal(nsync_job["selected_tool"], "nsync", "split role selected nsync")
    assert_equal(
        nsync_job["preflight_result"]["reason"],
        "nsync_live_execution_deferred",
        "nsync deferred before mutation",
    )
    assert_equal(nsync_job.get("volcano_job_ref") or {}, {}, "nsync deferred has no Volcano ref")
    issues = api_json("GET", "/api/v1/operations/action-required", headers=headers)
    assert_true(
        any(
            issue["issue_type"] == "data_job_nsync_deferred"
            and issue["job_id"] == nsync_job["job_id"]
            for issue in issues
        ),
        "nsync deferred appears in action-required",
    )

    print(
        json.dumps(
            {
                "scan": phase21_job_output(scan_done),
                "sync": phase21_job_output(sync_done),
                "rm": phase21_job_output(rm_done),
                "nsync_deferred": {
                    "job_id": nsync_job["job_id"],
                    "request_id": nsync_request_id,
                    "state": nsync_job["state"],
                    "reason": nsync_job["preflight_result"]["reason"],
                    "selected_source_node": nsync_job["preflight_result"][
                        "selected_source_candidates"
                    ][0]["node_name"],
                    "selected_destination_node": nsync_job["preflight_result"][
                        "selected_destination_candidates"
                    ][0]["node_name"],
                },
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def actor_headers(actor: str) -> dict[str, str]:
    return {
        "authorization": f"Bearer {TOKEN}",
        "content-type": "application/json",
        "x-dms-actor": actor,
    }


def wait_for_health(headers: dict[str, str]) -> None:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            health = api_json("GET", "/healthz", headers=headers)
            if health.get("status") == "ok":
                return
        except Exception:
            pass
        time.sleep(2)
    raise AssertionError("DMS API health check did not become ready")


def wait_for_agent_report(headers: dict[str, str]) -> None:
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        reports = api_json(
            "GET",
            "/api/v1/operations/agent-reports?worker_role=DM&freshness=Fresh",
            headers=headers,
        )
        for report in reports:
            if report["worker_role"] != "DM" or report["cluster_name"] != "cluster-a":
                continue
            payload = report.get("report") or {}
            tools_ready = all(
                has_ready_tool(payload, tool) for tool in ("dscan", "dsync", "drm")
            )
            if tools_ready and has_ready_identity(payload, "alice"):
                return
        time.sleep(3)
    raise AssertionError("fresh DM Agent report with dscan/dsync/drm and alice not observed")


def upsert_identity(headers: dict[str, str]) -> None:
    body = {
        "requester_id": REQUESTER,
        "identity_provider": "ldap-main",
        "posix_username": "alice",
        "expected_uid": 10000,
        "expected_primary_gid": 10000,
        "expected_groups": ["developers"],
    }
    result = api_json(
        "PUT",
        f"/api/v1/identity-mappings/ldap-main/{REQUESTER}",
        body=body,
        headers=headers,
    )
    assert_equal(result["status"], "Active", "identity mapping is active")


def upsert_storage_mapping(
    headers: dict[str, str], storage_name: str, *, storage_class_name: str | None = None
) -> None:
    if storage_class_name is None:
        storage_class_name = "testbed-cephfs"
    body = {
        "storage_name": storage_name,
        "backend_template": {
            "backend_type": "cephfs",
            "mount_path": CEPH_MOUNT,
            "managed_root": CEPH_MOUNT,
        },
        "cluster_name": "cluster-a",
        "storage_class_name": storage_class_name,
    }
    result = api_json(
        "POST",
        "/api/v1/resource-management/storage-mappings",
        body=body,
        headers=headers,
    )
    readiness = result["mapping"]["readiness"]
    assert_equal(readiness["data_management"], "Ready", f"{storage_name} DM readiness")


def assert_resource_fields_rejected(headers: dict[str, str]) -> None:
    bad_scan = api_error(
        "POST",
        "/api/v1/data-management/scan",
        body={
            "requester_id": REQUESTER,
            "target": {"storage_name": STORAGE_NAME, "path": SCAN_TARGET_PATH},
            "node_count": 2,
        },
        headers=headers,
    )
    bad_sync_option = api_error(
        "POST",
        "/api/v1/data-management/sync",
        body={
            "requester_id": REQUESTER,
            "source": {"storage_name": STORAGE_NAME, "path": SYNC_SOURCE_PATH},
            "destination": {"storage_name": STORAGE_NAME, "path": SYNC_DESTINATION_PATH},
            "options": {"rank_count": 2},
        },
        headers=headers,
    )
    assert_equal(bad_scan["status"], 422, "top-level node_count is rejected")
    assert_equal(bad_sync_option["status"], 422, "rank_count option is rejected")


def submit_scan(headers: dict[str, str]) -> str:
    response = api_json(
        "POST",
        "/api/v1/data-management/scan",
        body={
            "requester_id": REQUESTER,
            "target": {"storage_name": STORAGE_NAME, "path": SCAN_TARGET_PATH},
            "priority": "Mid",
            "options": {"summary_only": True},
        },
        headers=headers,
    )
    return response["request_id"]


def submit_sync(headers: dict[str, str]) -> str:
    response = api_json(
        "POST",
        "/api/v1/data-management/sync",
        body={
            "requester_id": REQUESTER,
            "source": {"storage_name": STORAGE_NAME, "path": SYNC_SOURCE_PATH},
            "destination": {"storage_name": STORAGE_NAME, "path": SYNC_DESTINATION_PATH},
        },
        headers=headers,
    )
    return response["request_id"]


def submit_rm(headers: dict[str, str]) -> str:
    response = api_json(
        "POST",
        "/api/v1/data-management/rm",
        body={
            "requester_id": REQUESTER,
            "target": {"storage_name": STORAGE_NAME, "path": RM_TARGET_PATH},
            "options": {"recursive": True},
        },
        headers=headers,
    )
    return response["request_id"]


def submit_nsync_deferred(headers: dict[str, str]) -> str:
    response = api_json(
        "POST",
        "/api/v1/data-management/sync",
        body={
            "requester_id": REQUESTER,
            "source": {"storage_name": SPLIT_SOURCE_STORAGE, "path": NSYNC_SOURCE_PATH},
            "destination": {
                "storage_name": SPLIT_DESTINATION_STORAGE,
                "path": NSYNC_DESTINATION_PATH,
            },
        },
        headers=headers,
    )
    return response["request_id"]


def submit_split_role_agent_report(
    headers: dict[str, str], *, node_name: str, storage_name: str
) -> None:
    body = {
        "schema_version": "phase21.v1",
        "reported_at": None,
        "cluster_name": "cluster-a",
        "node_name": node_name,
        "node_uid": f"uid-{node_name}",
        "worker_role": "DM",
        "mounts": [
            {
                "storage_name": storage_name,
                "mount_path": CEPH_MOUNT,
                "status": "Ready",
                "readable": True,
            }
        ],
        "tools": [
            {"name": "dscan", "status": "Ready"},
            {"name": "dsync", "status": "Ready"},
            {"name": "drm", "status": "Ready"},
            {"name": "nsync", "status": "Ready"},
        ],
        "credentials": [{"name": "kubernetes-service-account", "status": "Ready"}],
        "networks": [{"name": "storage-net", "status": "Ready"}],
        "identity_evidence": {
            "source": "phase21-verifier",
            "users": [
                {
                    "username": "alice",
                    "status": "Ready",
                    "uid": 10000,
                    "gid": 10000,
                    "groups": ["developers"],
                }
            ],
        },
        "csi": [],
    }
    result = api_json(
        "POST",
        "/api/v1/agent/reports",
        body=body,
        headers=actor_headers(f"node:cluster-a:{node_name}"),
    )
    assert_equal(result["status"], "Fresh", f"{node_name} synthetic DM report accepted")


def wait_for_job(
    headers: dict[str, str],
    *,
    operation: str,
    detail_kind: str,
    request_id: str,
    expected_state: str,
) -> dict[str, Any]:
    terminal = {
        "Succeeded",
        "Failed",
        "PreflightFailed",
        "TimedOut",
        "Cancelled",
        "ConfirmPending",
    }
    deadline = time.monotonic() + 300
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        jobs = api_json(
            "GET",
            f"/api/v1/operations/data-jobs?operation={operation}&limit=50",
            headers=headers,
        )
        for job in jobs:
            if job["request_id"] != request_id:
                continue
            last = api_json(
                "GET",
                f"/api/v1/data-management/{detail_kind}/jobs/{job['job_id']}",
                headers=headers,
            )
            if last["state"] == expected_state:
                return last
            if last["state"] in terminal and last["state"] != expected_state:
                raise AssertionError(
                    f"{operation} {request_id} reached {last['state']} instead of "
                    f"{expected_state}: {json.dumps(last, sort_keys=True)}"
                )
        time.sleep(3)
    raise AssertionError(f"{operation} {request_id} did not reach {expected_state}; last={last}")


def confirm_job(headers: dict[str, str], job: dict[str, Any]) -> None:
    preview = job["result_summary"]["preview"]
    result = api_json(
        "POST",
        f"/api/v1/data-management/jobs/{job['job_id']}:confirm",
        body={
            "requester_id": REQUESTER,
            "confirm": True,
            "preview_observed_hash": preview["fingerprint"],
        },
        headers=headers,
    )
    assert_equal(result["status"], "Confirmed", f"{job['job_id']} confirmed")


def assert_phase21_resource_evidence(job: dict[str, Any], label: str) -> None:
    summary = job["result_summary"]
    assert_true(summary.get("selected_node"), f"{label} selected node recorded")
    assert_equal(summary.get("selected_node_count"), 1, f"{label} selected node count")
    assert_equal(summary.get("worker_pod_count"), 1, f"{label} worker pod count")
    assert_equal(summary.get("process_count"), 1, f"{label} process count")
    pod_summary = summary.get("pod_summary") or {}
    assert_equal(pod_summary.get("worker_pod_count"), 1, f"{label} observed pod count")


def phase21_job_output(job: dict[str, Any]) -> dict[str, Any]:
    summary = job["result_summary"]
    return {
        "job_id": job["job_id"],
        "request_id": job["request_id"],
        "state": job["state"],
        "selected_tool": job["selected_tool"],
        "selected_node": summary.get("selected_node"),
        "worker_pod_count": summary.get("worker_pod_count"),
        "process_count": summary.get("process_count"),
        "artifact_uri": job.get("artifact_uri"),
        "volcano_job_ref": job.get("volcano_job_ref"),
    }


def has_ready_tool(payload: dict[str, Any], name: str) -> bool:
    for tool in payload.get("tools") or []:
        if isinstance(tool, dict) and tool.get("name") == name:
            return tool.get("status") == "Ready" or tool.get("healthy") is True
        if tool == name:
            return True
    return False


def has_ready_identity(payload: dict[str, Any], username: str) -> bool:
    for user in (payload.get("identity_evidence") or {}).get("users") or []:
        if user.get("username") == username and (
            user.get("status") == "Ready" or user.get("uid") is not None
        ):
            return True
    return False


def api_json(
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    headers: dict[str, str],
) -> Any:
    data = None if body is None else json.dumps(body).encode()
    req = request.Request(
        f"{API_URL}{path}",
        data=data,
        method=method,
        headers=headers,
    )
    try:
        with request.urlopen(req, timeout=20) as response:
            payload = response.read().decode()
            return json.loads(payload) if payload else {}
    except error.HTTPError as exc:
        payload = exc.read().decode()
        raise AssertionError(
            f"{method} {path} failed with {exc.code}: {payload}"
        ) from exc


def api_error(
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    headers: dict[str, str],
) -> dict[str, Any]:
    data = None if body is None else json.dumps(body).encode()
    req = request.Request(
        f"{API_URL}{path}",
        data=data,
        method=method,
        headers=headers,
    )
    try:
        with request.urlopen(req, timeout=20) as response:
            payload = response.read().decode()
            return {
                "status": response.status,
                "body": json.loads(payload) if payload else {},
            }
    except error.HTTPError as exc:
        payload = exc.read().decode()
        return {"status": exc.code, "body": json.loads(payload) if payload else {}}


def assert_equal(actual: Any, expected: Any, message: str) -> None:
    if actual != expected:
        raise AssertionError(f"{message}: expected {expected!r}, got {actual!r}")


def assert_true(value: Any, message: str) -> None:
    if not value:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
