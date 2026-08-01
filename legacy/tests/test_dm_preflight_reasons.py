"""Precise DM preflight failure reasons.

The POSIX preflight pod runs `[ test ] || fail <reason>` checks and prints a
`DMS_PREFLIGHT_REASON=<reason>` marker on the first failing check. The worker
parses that marker so a failed job reports e.g. source_not_found /
destination_parent_missing instead of a generic posix_permission_denied.
"""

from __future__ import annotations

import types

from dms.adapters import volcano
from dms.adapters.volcano import (
    KubernetesVolcanoAdapter,
    _parse_preflight_reason,
    _preflight_script,
    _PREFLIGHT_REASON_MARKER,
)
from dms.config import Settings
from dms.domain import OperationKind


def _adapter() -> KubernetesVolcanoAdapter:
    return KubernetesVolcanoAdapter(
        Settings(
            database_url="sqlite:///:memory:",
            observability_database_url="sqlite:///:memory:",
            dm_job_image="registry.local/dms-mpifileutils:test",
            dm_artifact_base_uri="file:///artifacts/dms",
            dm_kubernetes_mode="cluster",
        )
    )


def _sync_job(tool: str, worker_pool: dict):
    plan = {
        "desired_state": {
            "source": {"storage_name": "cephfs-dms", "path": "dms/src"},
            "destination": {"storage_name": "cephfs-dms-secondary", "path": "dms/dst"},
            "options": {},
        },
        "execution_metadata": {"phase": "preview"},
    }
    data_job = {
        "job_id": "job-pf",
        "request_id": "req-pf",
        "operation": OperationKind.DATA_SYNC.value,
        "storage_name": "cephfs-dms",
        "normalized_target": {
            "source": {"storage_name": "cephfs-dms", "path": "dms/src"},
            "destination": {"storage_name": "cephfs-dms-secondary", "path": "dms/dst"},
        },
        "selected_tool": tool,
        "worker_pool": worker_pool,
    }
    preflight = {"identity_mapping": {"uid": 10003, "gid": 10000, "posix_username": "u"}}
    return plan, data_job, preflight


def _cmd(manifest: dict) -> str:
    return manifest["spec"]["containers"][0]["command"][2]


# --- _parse_preflight_reason -------------------------------------------------


def test_parse_reason_from_pod_logs_dict():
    logs = {"returncode": 1, "stdout": "DMS_PREFLIGHT_REASON=source_not_found\n", "stderr": ""}
    assert _parse_preflight_reason(logs) == "source_not_found"


def test_parse_reason_from_string():
    assert _parse_preflight_reason("x\nDMS_PREFLIGHT_REASON=destination_parent_missing\n") == (
        "destination_parent_missing"
    )


def test_parse_reason_last_marker_wins():
    text = "DMS_PREFLIGHT_REASON=a\nnoise\nDMS_PREFLIGHT_REASON=b\n"
    assert _parse_preflight_reason(text) == "b"


def test_parse_reason_none_when_absent():
    assert _parse_preflight_reason("scan preflight ok: /x\n") is None
    assert _parse_preflight_reason("") is None
    assert _parse_preflight_reason(None) is None
    assert _parse_preflight_reason({"stdout": "", "stderr": ""}) is None


# --- script construction -----------------------------------------------------


def test_preflight_script_defines_fail_and_marker():
    script = _preflight_script(
        setup=["x=1"],
        checks=['[ -e "$x" ] || fail thing_missing'],
        ok_print="printf ok",
    )
    assert "fail() {" in script
    assert _PREFLIGHT_REASON_MARKER in script
    assert '[ -e "$x" ] || fail thing_missing' in script
    assert script.strip().endswith("printf ok")


def test_sync_preflight_manifest_has_precise_reason_tokens():
    adapter = _adapter()
    worker_pool = {
        "selected_candidates": [
            {
                "node_name": "w1",
                "source_mount_path": "/cephfs",
                "destination_mount_path": "/cephfs",
            }
        ]
    }
    plan, data_job, preflight = _sync_job("dsync", worker_pool)
    manifests = adapter._data_preflight_manifests(plan, data_job, preflight, "preview")
    cmd = _cmd(manifests[0][1])
    for token in [
        "fail source_not_found",
        "fail source_not_readable",
        "fail source_not_traversable",
        "fail destination_parent_missing",
        "fail destination_parent_not_writable",
        "fail destination_parent_not_traversable",
        "fail destination_not_writable",
    ]:
        assert token in cmd, token


def test_nsync_role_manifests_have_precise_reason_tokens():
    adapter = _adapter()
    worker_pool = {
        "source_candidates": [{"node_name": "w1", "mount_path": "/cephfs"}],
        "destination_candidates": [{"node_name": "w4", "mount_path": "/cephfs-secondary"}],
        "selected_candidates": [
            {"node_name": "w1", "mount_path": "/cephfs"},
            {"node_name": "w4", "mount_path": "/cephfs-secondary"},
        ],
    }
    plan, data_job, preflight = _sync_job("nsync", worker_pool)
    by_role = dict(adapter._data_preflight_manifests(plan, data_job, preflight, "preview"))
    assert "fail source_not_found" in _cmd(by_role["source"])
    assert "fail destination_parent_missing" in _cmd(by_role["destination"])
    # roles must not cross-check the other side's path
    assert "destination_parent" not in _cmd(by_role["source"])
    assert "/dms/source" not in _cmd(by_role["destination"])


# --- _run_preflight_pod reason mapping (mocked kubectl) ----------------------


def _stub_run(*_a, **_k):
    return types.SimpleNamespace(returncode=0, stdout="", stderr="")


def test_run_preflight_pod_reports_parsed_reason(monkeypatch):
    adapter = _adapter()
    monkeypatch.setattr(volcano.subprocess, "run", _stub_run)
    monkeypatch.setattr(adapter, "_wait_for_pod_terminal", lambda ns, n: {"phase": "Failed"})
    monkeypatch.setattr(
        adapter,
        "_pod_logs",
        lambda ns, n: {"returncode": 1, "stdout": "DMS_PREFLIGHT_REASON=source_not_found\n", "stderr": ""},
    )
    result = adapter._run_preflight_pod({"metadata": {"name": "p", "namespace": "dms"}}, "preview")
    assert result["status"] == "Rejected"
    assert result["reason"] == "source_not_found"


def test_run_preflight_pod_falls_back_without_marker(monkeypatch):
    adapter = _adapter()
    monkeypatch.setattr(volcano.subprocess, "run", _stub_run)
    monkeypatch.setattr(adapter, "_wait_for_pod_terminal", lambda ns, n: {"phase": "Failed"})
    monkeypatch.setattr(
        adapter,
        "_pod_logs",
        lambda ns, n: {"returncode": 137, "stdout": "", "stderr": "OOMKilled"},
    )
    result = adapter._run_preflight_pod({"metadata": {"name": "p", "namespace": "dms"}}, "preview")
    assert result["reason"] == "posix_permission_denied"


def test_run_preflight_pod_ready_on_success(monkeypatch):
    adapter = _adapter()
    monkeypatch.setattr(volcano.subprocess, "run", _stub_run)
    monkeypatch.setattr(adapter, "_wait_for_pod_terminal", lambda ns, n: {"phase": "Succeeded"})
    monkeypatch.setattr(adapter, "_pod_logs", lambda ns, n: {"stdout": "ok", "stderr": ""})
    result = adapter._run_preflight_pod({"metadata": {"name": "p", "namespace": "dms"}}, "preview")
    assert result["status"] == "Ready"
