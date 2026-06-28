"""BFF _refine_attention — severity backfill (incl. CRITICAL), live/history category,
field passthrough, and sort order for the 조치 필요 panel."""

from __future__ import annotations

from portal.backend.routers.dashboard import _SEVERITY_RANK, _refine_attention


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
