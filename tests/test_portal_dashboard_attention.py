"""BFF _refine_attention — severity backfill (incl. CRITICAL), live/history category,
field passthrough, and sort order for the 조치 필요 panel."""

from __future__ import annotations

import json

from portal.backend.routers.dashboard import (
    _MAX_ATTENTION_FIELD_BYTES,
    _compact_value,
    _refine_attention,
    _SEVERITY_RANK,
)


def test_severity_rank_orders_critical_first():
    assert (
        _SEVERITY_RANK["CRITICAL"]
        < _SEVERITY_RANK["ERROR"]
        < _SEVERITY_RANK["WARN"]
        < _SEVERITY_RANK["INFO"]
    )


def test_refine_category_severity_passthrough_and_sort():
    items = [
        {"issue_type": "data_job_failed", "severity": "ERROR",
         "resource_kind": "data_job", "recommended_action": "fix and retry"},
        {"issue_type": "quota_usage_critical", "severity": "CRITICAL", "namespace_name": "ns1"},
        {"issue_type": "request_attention", "status": "Blocked"},  # no severity → WARN, live
        {"issue_type": "filesystem_soft_deleted", "severity": "INFO"},  # live exception
        {"issue_type": "filesystem_quota_drifted", "severity": "WARN"},  # history
    ]
    out = _refine_attention(items)
    by_type = {x["issue_type"]: x for x in out}

    # live vs history categorization
    assert by_type["quota_usage_critical"]["category"] == "live"
    assert by_type["request_attention"]["category"] == "live"
    assert by_type["filesystem_soft_deleted"]["category"] == "live"
    assert by_type["data_job_failed"]["category"] == "history"
    assert by_type["filesystem_quota_drifted"]["category"] == "history"

    # severity backfill + passthrough of detail fields
    assert by_type["request_attention"]["severity"] == "WARN"
    assert by_type["data_job_failed"]["recommended_action"] == "fix and retry"
    assert by_type["quota_usage_critical"]["namespace_name"] == "ns1"

    # sort: live first, CRITICAL ahead of WARN/INFO → critical live item is first
    assert out[0]["issue_type"] == "quota_usage_critical"


def test_terminated_data_jobs_forced_to_info():
    """A preflight gate failure or a cancellation is a notification, not action-
    required: force INFO (hidden by default) regardless of the severity DMS sent.
    Genuinely actionable preflight causes (their own issue_types) keep their severity."""
    items = [
        {"issue_type": "data_job_preflight_failed", "severity": "ERROR", "resource_kind": "data_job"},
        {"issue_type": "data_job_cancelled", "severity": "WARN", "resource_kind": "data_job"},
        {"issue_type": "data_job_volcano_failed", "severity": "ERROR", "resource_kind": "data_job"},
        {"issue_type": "data_job_permission_denied", "severity": "ERROR", "resource_kind": "data_job"},
    ]
    by_type = {r["issue_type"]: r for r in _refine_attention(items)}
    assert by_type["data_job_preflight_failed"]["severity"] == "INFO"
    assert by_type["data_job_cancelled"]["severity"] == "INFO"
    # actionable data-job issues are NOT downgraded
    assert by_type["data_job_volcano_failed"]["severity"] == "ERROR"
    assert by_type["data_job_permission_denied"]["severity"] == "ERROR"
    # still categorized as history (terminated jobs)
    assert all(r["category"] == "history" for r in by_type.values())


def test_request_attention_is_live_even_for_data_job():
    # a stuck data.sync REQUEST (RecoveryNeeded) is actionable NOW — must be 'live',
    # not bucketed into 과거 이력 just because resource_kind is data_job.
    items = [
        {"issue_type": "request_attention", "status": "RecoveryNeeded",
         "resource_kind": "data_job", "request_id": "r1"},
        # a terminated data-job notification stays history
        {"issue_type": "data_job_preflight_failed", "resource_kind": "data_job",
         "severity": "ERROR", "job_id": "j1"},
    ]
    by_type = {r["issue_type"]: r for r in _refine_attention(items)}
    assert by_type["request_attention"]["category"] == "live"
    assert by_type["data_job_preflight_failed"]["category"] == "history"


def test_compact_value_trims_large_nested_but_keeps_scalars():
    # a huge preflight_result-like blob: per-node nested detail
    big = {
        "status": "Failed",
        "reason": "no ready candidate",
        "nodes": [{"node": f"w{i}", "detail": "x" * 200} for i in range(50)],
        "checks": {f"c{i}": {"ok": False, "msg": "y" * 100} for i in range(50)},
    }
    assert len(json.dumps(big)) > _MAX_ATTENTION_FIELD_BYTES
    out = _compact_value(big)
    # scalar top-level keys are preserved (operator still sees status/reason)
    assert out["status"] == "Failed"
    assert out["reason"] == "no ready candidate"
    # heavy nested fields are dropped, noted in _trimmed
    assert "nodes" not in out and "checks" not in out
    assert "nested field(s) omitted" in out["_trimmed"]
    # small values pass through untouched
    assert _compact_value({"a": 1}) == {"a": 1}
    assert _compact_value("short") == "short"


def test_refine_attention_shrinks_payload():
    heavy = {"issue_type": "data_job_preflight_failed", "resource_kind": "data_job",
             "job_id": "j1", "severity": "ERROR",
             "preflight_result": {"status": "Failed",
                                  "nodes": [{"n": i, "d": "z" * 300} for i in range(60)]},
             "result_summary": {"summary": {"files": 10},
                                "per_path": [{"p": i, "e": "e" * 300} for i in range(60)]}}
    before = len(json.dumps(heavy))
    [out] = _refine_attention([heavy])
    after = len(json.dumps(out))
    assert after < before / 10          # ~50x in practice; assert at least 10x
    # the scalar status is still reachable for the detail view
    assert out["preflight_result"]["status"] == "Failed"
    # identity/scalars intact
    assert out["job_id"] == "j1" and out["fingerprint"] == "data_job_preflight_failed|j1"
