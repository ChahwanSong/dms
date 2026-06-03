from __future__ import annotations

import json
import os
import time
from typing import Any
from urllib import error, request


API_URL = os.environ["DMS_PHASE20_API_URL"].rstrip("/")
TOKEN = os.environ["DMS_PHASE20_AUTH_TOKEN"]
REQUESTER = os.environ.get("DMS_PHASE20_REQUESTER", "phase20-alice")
MISSING_REQUESTER = os.environ.get("DMS_PHASE20_MISSING_REQUESTER", "phase20-missing")
STORAGE_NAME = os.environ.get("DMS_PHASE20_STORAGE_NAME", "cephfs-a")
SOURCE_PATH = os.environ["DMS_PHASE20_SYNC_SOURCE_PATH"]
DESTINATION_PATH = os.environ["DMS_PHASE20_SYNC_DESTINATION_PATH"]
RM_TARGET_PATH = os.environ["DMS_PHASE20_RM_TARGET_PATH"]


def main() -> int:
    headers = actor_headers("phase20-operator")
    wait_for_health(headers)
    wait_for_agent_report(headers)
    upsert_identity(headers)
    upsert_storage_mapping(headers)
    assert_negative_intake_guards(headers)

    sync_request_id = submit_sync(headers)
    sync_preview = wait_for_job(
        headers,
        operation="data.sync",
        request_id=sync_request_id,
        expected_state="ConfirmPending",
    )
    assert_equal(sync_preview["selected_tool"], "dsync", "sync selected dsync")
    sync_preview_entry = sync_preview["result_summary"]["preview"]
    assert_equal(
        sync_preview_entry["summary"]["dry_run"], True, "sync preview is dry-run"
    )
    assert_true(
        sync_preview_entry["summary_uri"].endswith("/preview/summary.json"),
        "sync preview summary URI recorded",
    )
    confirm_without_flag(headers, sync_preview["job_id"])
    confirm_job(headers, sync_preview)
    sync_done = wait_for_job(
        headers,
        operation="data.sync",
        request_id=sync_request_id,
        expected_state="Succeeded",
    )
    sync_execution = sync_done["result_summary"]["execution"]
    assert_equal(
        sync_execution["summary"]["dry_run"], False, "sync execution is not dry-run"
    )
    assert_true(
        sync_execution["summary_uri"].endswith("/execution/summary.json"),
        "sync execution summary URI recorded",
    )

    rm_request_id = submit_rm(headers, requester=REQUESTER, target_path=RM_TARGET_PATH)
    rm_preview = wait_for_job(
        headers,
        operation="data.rm",
        request_id=rm_request_id,
        expected_state="ConfirmPending",
    )
    assert_equal(rm_preview["selected_tool"], "drm", "rm selected drm")
    assert_equal(
        rm_preview["result_summary"]["preview"]["summary"]["dry_run"],
        True,
        "rm preview is dry-run",
    )
    confirm_job(headers, rm_preview)
    rm_done = wait_for_job(
        headers,
        operation="data.rm",
        request_id=rm_request_id,
        expected_state="Succeeded",
    )
    assert_equal(
        rm_done["result_summary"]["execution"]["summary"]["target_absent"],
        True,
        "rm execution reports target absent",
    )

    missing_request_id = submit_rm(
        headers, requester=MISSING_REQUESTER, target_path=f"{RM_TARGET_PATH}-missing-id"
    )
    missing_job = wait_for_job(
        headers,
        operation="data.rm",
        request_id=missing_request_id,
        expected_state="PreflightFailed",
    )
    assert_equal(
        missing_job["preflight_result"]["reason"],
        "missing_active_identity_mapping",
        "missing identity rejected before preview",
    )
    assert_equal(
        missing_job.get("volcano_job_ref") or {},
        {},
        "missing identity job has no Volcano ref",
    )
    issues = api_json("GET", "/api/v1/operations/action-required", headers=headers)
    assert_true(
        any(issue["issue_type"] == "data_job_missing_identity_mapping" for issue in issues),
        "missing identity appears in action-required",
    )

    expired_request_id = submit_sync(headers, destination_path=f"{DESTINATION_PATH}-expired")
    expired_preview = wait_for_job(
        headers,
        operation="data.sync",
        request_id=expired_request_id,
        expected_state="ConfirmPending",
    )
    sleep_seconds = int(os.environ.get("DMS_PHASE20_PREVIEW_EXPIRY_SLEEP_SECONDS", "17"))
    time.sleep(sleep_seconds)
    expired_confirm = api_error(
        "POST",
        f"/api/v1/data-management/jobs/{expired_preview['job_id']}:confirm",
        body={
            "requester_id": REQUESTER,
            "confirm": True,
            "preview_observed_hash": expired_preview["result_summary"]["preview"][
                "fingerprint"
            ],
        },
        headers=headers,
    )
    assert_equal(expired_confirm["status"], 409, "expired preview confirm is rejected")
    expired_job = api_json(
        "GET",
        f"/api/v1/data-management/sync/jobs/{expired_preview['job_id']}",
        headers=headers,
    )
    assert_equal(
        expired_job["state"], "PreviewExpired", "expired preview marks Data Job"
    )

    output = {
        "sync_job_id": sync_done["job_id"],
        "sync_request_id": sync_request_id,
        "sync_selected_tool": sync_done["selected_tool"],
        "sync_artifact_uri": sync_done["artifact_uri"],
        "sync_preview": sync_done["result_summary"]["preview"],
        "sync_execution": sync_done["result_summary"]["execution"],
        "sync_volcano_job_ref": sync_done["volcano_job_ref"],
        "rm_job_id": rm_done["job_id"],
        "rm_request_id": rm_request_id,
        "rm_selected_tool": rm_done["selected_tool"],
        "rm_artifact_uri": rm_done["artifact_uri"],
        "rm_preview": rm_done["result_summary"]["preview"],
        "rm_execution": rm_done["result_summary"]["execution"],
        "rm_volcano_job_ref": rm_done["volcano_job_ref"],
        "missing_identity_job_id": missing_job["job_id"],
        "expired_job_id": expired_job["job_id"],
        "expired_state": expired_job["state"],
    }
    print(json.dumps(output, indent=2, sort_keys=True))
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
                _has_ready_tool(payload, tool) for tool in ("dscan", "dsync", "drm")
            )
            if tools_ready and _has_ready_identity(payload, "alice"):
                return
        time.sleep(3)
    raise AssertionError("fresh DM Agent report with dsync/drm and alice identity not observed")


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
            "mount_path": os.environ["DMS_PHASE20_CEPH_MOUNT"],
            "managed_root": os.environ["DMS_PHASE20_CEPH_MOUNT"],
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


def assert_negative_intake_guards(headers: dict[str, str]) -> None:
    under_source = api_error(
        "POST",
        "/api/v1/data-management/sync",
        body={
            "requester_id": REQUESTER,
            "storage_name": STORAGE_NAME,
            "source_path": "phase20-negative",
            "destination_path": "phase20-negative/subdir",
        },
        headers=headers,
    )
    rm_root = api_error(
        "POST",
        "/api/v1/data-management/rm",
        body={
            "requester_id": REQUESTER,
            "target": {"storage_name": STORAGE_NAME, "path": "."},
            "options": {"recursive": True},
        },
        headers=headers,
    )
    raw_options = api_error(
        "POST",
        "/api/v1/data-management/rm",
        body={
            "requester_id": REQUESTER,
            "target": {"storage_name": STORAGE_NAME, "path": RM_TARGET_PATH},
            "options": {"recursive": True, "raw_options": "--aggressive"},
        },
        headers=headers,
    )
    assert_equal(under_source["status"], 422, "sync destination under source rejected")
    assert_equal(rm_root["status"], 422, "rm storage root rejected")
    assert_equal(raw_options["status"], 422, "raw rm options rejected")


def submit_sync(headers: dict[str, str], *, destination_path: str | None = None) -> str:
    response = api_json(
        "POST",
        "/api/v1/data-management/sync",
        body={
            "requester_id": REQUESTER,
            "source": {"storage_name": STORAGE_NAME, "path": SOURCE_PATH},
            "destination": {
                "storage_name": STORAGE_NAME,
                "path": destination_path or DESTINATION_PATH,
            },
            "priority": "Mid",
            "options": {"contents": True},
        },
        headers=headers,
    )
    return response["request_id"]


def submit_rm(headers: dict[str, str], *, requester: str, target_path: str) -> str:
    response = api_json(
        "POST",
        "/api/v1/data-management/rm",
        body={
            "requester_id": requester,
            "target": {"storage_name": STORAGE_NAME, "path": target_path},
            "priority": "Mid",
            "options": {"recursive": True},
        },
        headers=headers,
    )
    return response["request_id"]


def wait_for_job(
    headers: dict[str, str],
    *,
    operation: str,
    request_id: str,
    expected_state: str,
) -> dict[str, Any]:
    terminal = {
        "Succeeded",
        "Failed",
        "PreflightFailed",
        "TimedOut",
        "Cancelled",
        "PreviewExpired",
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
                f"/api/v1/data-management/{operation.split('.')[-1]}/jobs/{job['job_id']}",
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


def confirm_without_flag(headers: dict[str, str], job_id: str) -> None:
    response = api_error(
        "POST",
        f"/api/v1/data-management/jobs/{job_id}:confirm",
        body={"requester_id": REQUESTER},
        headers=headers,
    )
    assert_equal(response["status"], 409, "confirm without explicit flag rejected")


def confirm_job(headers: dict[str, str], job: dict[str, Any]) -> None:
    response = api_json(
        "POST",
        f"/api/v1/data-management/jobs/{job['job_id']}:confirm",
        body={
            "requester_id": REQUESTER,
            "confirm": True,
            "preview_observed_hash": job["result_summary"]["preview"]["fingerprint"],
        },
        headers=headers,
    )
    assert_equal(response["status"], "Confirmed", "job confirmed")


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
        with request.urlopen(req, timeout=15) as response:
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
        with request.urlopen(req, timeout=15) as response:
            response_body = response.read().decode("utf-8", errors="replace")
            return {"status": response.status, "body": json.loads(response_body)}
    except error.HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="replace")
        return {"status": exc.code, "body": json.loads(response_body)}


def _has_ready_tool(payload: dict[str, Any], tool_name: str) -> bool:
    for tool in payload.get("tools") or []:
        if isinstance(tool, dict) and tool.get("name") == tool_name:
            return tool.get("status") == "Ready" or tool.get("healthy") is True
    return False


def _has_ready_identity(payload: dict[str, Any], username: str) -> bool:
    for user in (payload.get("identity_evidence") or {}).get("users") or []:
        if user.get("username") == username:
            return user.get("status") == "Ready" and user.get("uid") == 10000
    return False


def assert_equal(actual: Any, expected: Any, message: str) -> None:
    if actual != expected:
        raise AssertionError(f"{message}: expected {expected!r}, got {actual!r}")


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
