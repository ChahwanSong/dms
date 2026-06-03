from __future__ import annotations

import json
import os
import time
from typing import Any
from urllib import error, request


API_URL = os.environ["DMS_PHASE19_API_URL"].rstrip("/")
TOKEN = os.environ["DMS_PHASE19_AUTH_TOKEN"]
REQUESTER = os.environ.get("DMS_PHASE19_REQUESTER", "phase19-alice")
MISSING_REQUESTER = os.environ.get("DMS_PHASE19_MISSING_REQUESTER", "phase19-missing")
STORAGE_NAME = os.environ.get("DMS_PHASE19_STORAGE_NAME", "cephfs-a")
TARGET_PATH = os.environ["DMS_PHASE19_TARGET_PATH"]
EXPECTED_MIN_FILES = int(os.environ.get("DMS_PHASE19_EXPECTED_MIN_FILES", "2"))


def main() -> int:
    headers = actor_headers("phase19-operator")
    wait_for_health(headers)
    wait_for_agent_report(headers)
    upsert_identity(headers)
    upsert_storage_mapping(headers)
    if _truthy(os.environ.get("DMS_PHASE19_EXPECT_UNSUPPORTED_MUTATIONS")):
        before_sync_rm = data_job_count(headers, operations={"data.sync", "data.rm"})
        assert_unsupported_mutations(headers)
        after_sync_rm = data_job_count(headers, operations={"data.sync", "data.rm"})
        assert_equal(
            after_sync_rm,
            before_sync_rm,
            "sync/rm unsupported requests created no jobs",
        )

    request_id = submit_scan(headers, requester=REQUESTER, target_path=TARGET_PATH)
    job = wait_for_scan(headers, request_id=request_id, expected_state="Succeeded")
    summary = job["result_summary"]["summary"]
    assert_true(summary["file_count"] >= EXPECTED_MIN_FILES, "scan counted fixture files")
    assert_true(summary["directory_count"] >= 1, "scan counted fixture directory")
    assert_true(summary["total_bytes"] > 0, "scan counted bytes")
    assert_equal(summary["scan_root"], TARGET_PATH, "scan root recorded")
    assert_true(job["volcano_job_ref"]["job_ref"].startswith("volcano://"), "Volcano job ref recorded")
    runtime_check = job["preflight_result"]["runtime_permission_check"]
    assert_equal(runtime_check["status"], "Ready", "runtime POSIX preflight passed")
    assert_true(job["result_summary"]["report_uri"].endswith("/dscan-report.json"), "report URI recorded")
    assert_true(job["result_summary"]["summary_uri"].endswith("/summary.json"), "summary URI recorded")
    assert_true(job["artifact_uri"].startswith("file://"), "file artifact URI recorded")

    missing_request_id = submit_scan(headers, requester=MISSING_REQUESTER, target_path=TARGET_PATH)
    missing_job = wait_for_scan(
        headers,
        request_id=missing_request_id,
        expected_state="PreflightFailed",
    )
    assert_equal(
        missing_job["preflight_result"]["reason"],
        "missing_active_identity_mapping",
        "missing identity rejected before Volcano submission",
    )
    assert_equal(missing_job.get("volcano_job_ref") or {}, {}, "missing identity has no Volcano ref")
    issues = api_json("GET", "/api/v1/operations/action-required", headers=headers)
    assert_true(
        any(issue["issue_type"] == "data_job_missing_identity_mapping" for issue in issues),
        "missing identity appears in action-required",
    )

    print(
        json.dumps(
            {
                "scan_job_id": job["job_id"],
                "scan_request_id": request_id,
                "state": job["state"],
                "summary": summary,
                "artifact_uri": job["artifact_uri"],
                "report_uri": job["result_summary"]["report_uri"],
                "summary_uri": job["result_summary"]["summary_uri"],
                "volcano_job_ref": job["volcano_job_ref"],
                "missing_identity_job_id": missing_job["job_id"],
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
            if report["worker_role"] == "DM" and report["cluster_name"] == "cluster-a":
                payload = report.get("report") or {}
                if _has_ready_dscan(payload) and _has_ready_identity(payload, "alice"):
                    return
        time.sleep(3)
    raise AssertionError("fresh DM Agent report with dscan and alice identity not observed")


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


def upsert_storage_mapping(headers: dict[str, str]) -> None:
    body = {
        "storage_name": STORAGE_NAME,
        "backend_template": {
            "backend_type": "cephfs",
            "mount_path": os.environ["DMS_PHASE19_CEPH_MOUNT"],
            "managed_root": os.environ["DMS_PHASE19_CEPH_MOUNT"],
        },
        "cluster_name": "cluster-a",
        "storage_class_name": "testbed-cephfs",
    }
    result = api_json(
        "POST",
        "/api/v1/resource-management/storage-mappings",
        body=body,
        headers=headers,
    )
    readiness = result["mapping"]["readiness"]
    assert_equal(readiness["data_management"], "Ready", "storage mapping data readiness")


def assert_unsupported_mutations(headers: dict[str, str]) -> None:
    sync = api_error(
        "POST",
        "/api/v1/data-management/sync",
        body={
            "requester_id": REQUESTER,
            "storage_name": STORAGE_NAME,
            "source_path": TARGET_PATH,
            "destination_path": f"{TARGET_PATH}-copy",
        },
        headers=headers,
    )
    rm = api_error(
        "POST",
        "/api/v1/data-management/rm",
        body={
            "requester_id": REQUESTER,
            "storage_name": STORAGE_NAME,
            "target_path": TARGET_PATH,
        },
        headers=headers,
    )
    assert_equal(sync["status"], 501, "sync returns unsupported")
    assert_equal(rm["status"], 501, "rm returns unsupported")
    assert_equal(sync["body"]["detail"]["reason"], "unsupported_until_phase20", "sync reason")
    assert_equal(rm["body"]["detail"]["reason"], "unsupported_until_phase20", "rm reason")


def submit_scan(headers: dict[str, str], *, requester: str, target_path: str) -> str:
    response = api_json(
        "POST",
        "/api/v1/data-management/scan",
        body={
            "requester_id": requester,
            "target": {"storage_name": STORAGE_NAME, "path": target_path},
            "priority": "Mid",
            "options": {"summary_only": True},
        },
        headers=headers,
    )
    return response["request_id"]


def wait_for_scan(
    headers: dict[str, str], *, request_id: str, expected_state: str
) -> dict[str, Any]:
    terminal = {"Succeeded", "Failed", "PreflightFailed", "TimedOut", "Cancelled"}
    deadline = time.monotonic() + 240
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        jobs = api_json(
            "GET",
            f"/api/v1/operations/data-jobs?operation=data.scan&limit=20",
            headers=headers,
        )
        for job in jobs:
            if job["request_id"] != request_id:
                continue
            last = api_json(
                "GET",
                f"/api/v1/data-management/scan/jobs/{job['job_id']}",
                headers=headers,
            )
            if last["state"] == expected_state:
                return last
            if last["state"] in terminal:
                raise AssertionError(
                    f"scan {request_id} reached {last['state']} instead of {expected_state}: "
                    f"{json.dumps(last, sort_keys=True)}"
                )
        time.sleep(3)
    raise AssertionError(f"scan {request_id} did not reach {expected_state}; last={last}")


def data_job_count(headers: dict[str, str], *, operations: set[str]) -> int:
    total = 0
    for operation in operations:
        jobs = api_json(
            "GET",
            f"/api/v1/operations/data-jobs?operation={operation}&limit=100",
            headers=headers,
        )
        total += len(jobs)
    return total


def api_json(
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    headers: dict[str, str],
) -> Any:
    payload = None if body is None else json.dumps(body).encode("utf-8")
    req = request.Request(f"{API_URL}{path}", data=payload, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="replace")
        raise AssertionError(f"{method} {path} failed HTTP {exc.code}: {response_body}") from exc


def api_error(
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None,
    headers: dict[str, str],
) -> dict[str, Any]:
    payload = None if body is None else json.dumps(body).encode("utf-8")
    req = request.Request(f"{API_URL}{path}", data=payload, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=10) as response:
            response_body = response.read().decode("utf-8", errors="replace")
            return {"status": response.status, "body": json.loads(response_body)}
    except error.HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="replace")
        return {"status": exc.code, "body": json.loads(response_body)}


def _has_ready_dscan(payload: dict[str, Any]) -> bool:
    for tool in payload.get("tools") or []:
        if isinstance(tool, dict) and tool.get("name") == "dscan":
            return tool.get("status") == "Ready" or tool.get("healthy") is True
    return False


def _has_ready_identity(payload: dict[str, Any], username: str) -> bool:
    for user in (payload.get("identity_evidence") or {}).get("users") or []:
        if user.get("username") == username:
            return user.get("status") == "Ready" and user.get("uid") == 10000
    return False


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def assert_equal(actual: Any, expected: Any, message: str) -> None:
    if actual != expected:
        raise AssertionError(f"{message}: expected {expected!r}, got {actual!r}")


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
