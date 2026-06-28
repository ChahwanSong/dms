"""KubernetesVolcanoAdapter.volcano_job_metrics + its pure parse helpers.

Verifies cpu/mem/timestamp parsing, vcjob resource sums, pod↔job correlation, and the
three lifecycle latencies, against fake kubectl JSON (no cluster)."""

from __future__ import annotations

from dms.adapters.volcano import (
    KubernetesVolcanoAdapter,
    _job_resource_requests,
    _parse_cpu_cores,
    _parse_mem_bytes,
    _secs_between,
)
from dms.config import Settings


def test_parse_cpu_cores():
    assert _parse_cpu_cores("500m") == 0.5
    assert _parse_cpu_cores("2") == 2.0
    assert _parse_cpu_cores(None) == 0.0
    assert _parse_cpu_cores("") == 0.0


def test_parse_mem_bytes():
    assert _parse_mem_bytes("2Gi") == 2 * 1024**3
    assert _parse_mem_bytes("512Mi") == 512 * 1024**2
    assert _parse_mem_bytes("1G") == 1000**3
    assert _parse_mem_bytes("1048576") == 1048576
    assert _parse_mem_bytes(None) == 0


def test_secs_between():
    assert _secs_between("2026-06-28T00:00:00Z", "2026-06-28T00:00:05Z") == 5.0
    assert _secs_between("2026-06-28T00:00:05Z", "2026-06-28T00:00:00Z") is None  # negative
    assert _secs_between(None, "2026-06-28T00:00:05Z") is None


def test_job_resource_requests():
    spec = {"tasks": [
        {"replicas": 2, "template": {"spec": {"containers": [
            {"resources": {"requests": {"cpu": "500m", "memory": "1Gi"}}}]}}},
        {"replicas": 1, "template": {"spec": {"containers": [
            {"resources": {"requests": {"cpu": "1", "memory": "2Gi"}}}]}}},
    ]}
    cpu, mem, pods = _job_resource_requests(spec)
    assert cpu == 2.0                          # 2*0.5 + 1*1
    assert mem == 2 * 1024**3 + 2 * 1024**3    # 2*1Gi + 1*2Gi
    assert pods == 3


def test_volcano_job_metrics_correlation_and_latencies():
    adapter = KubernetesVolcanoAdapter(
        settings=Settings(
            database_url="sqlite:///:memory:",
            observability_database_url="sqlite:///:memory:",
        )
    )
    vcjob = {
        "metadata": {"name": "job-a", "creationTimestamp": "2026-06-28T00:00:00Z",
                     "labels": {"dms.openai.com/data-tool": "dsync"}},
        "spec": {"queue": "dms-data", "minAvailable": 2, "tasks": [
            {"replicas": 2, "template": {"spec": {"containers": [
                {"resources": {"requests": {"cpu": "1", "memory": "1Gi"}}}]}}}]},
        "status": {"state": {"phase": "Completed"}, "succeeded": 2, "failed": 0},
    }
    pod1 = {
        "metadata": {"name": "job-a-0", "creationTimestamp": "2026-06-28T00:00:02Z",
                     "labels": {"volcano.sh/job-name": "job-a"}},
        "spec": {"containers": [{"env": [
            {"name": "DMS_SYNC_SOURCE_STORAGE", "value": "cephfs-dms"},
            {"name": "DMS_SYNC_DESTINATION_STORAGE", "value": "cephfs-third"},
            {"name": "DMS_SYNC_SOURCE_PATH", "value": "e2e/src"}]}]},
        "status": {"startTime": "2026-06-28T00:00:10Z",
                   "conditions": [{"type": "PodScheduled", "lastTransitionTime": "2026-06-28T00:00:05Z"}],
                   "containerStatuses": [{"state": {"terminated": {"finishedAt": "2026-06-28T00:05:00Z"}}}]},
    }
    pod2 = {
        "metadata": {"name": "job-a-1", "creationTimestamp": "2026-06-28T00:00:03Z",
                     "labels": {"volcano.sh/job-name": "job-a"}},
        "status": {"startTime": "2026-06-28T00:00:12Z",
                   "conditions": [{"type": "PodScheduled", "lastTransitionTime": "2026-06-28T00:00:06Z"}],
                   "containerStatuses": [{"state": {"terminated": {"finishedAt": "2026-06-28T00:06:00Z"}}}]},
    }

    def fake_get(args, timeout=10.0):
        if "vcjob" in args:
            return [vcjob]
        if "pods" in args:
            return [pod1, pod2]
        return []

    adapter._kubectl_get_items = fake_get  # type: ignore[assignment]
    out = adapter.volcano_job_metrics()
    assert out["error"] is None
    assert len(out["jobs"]) == 1
    j = out["jobs"][0]
    assert (j["name"], j["queue"], j["phase"]) == ("job-a", "dms-data", "Completed")
    assert j["tool"] == "dsync"
    assert j["src_storage"] == "cephfs-dms"
    assert j["dst_storage"] == "cephfs-third"
    assert j["src_path"] == "e2e/src"
    assert j["req_cpu_cores"] == 2.0
    assert j["req_mem_bytes"] == 2 * 1024**3
    assert j["req_pods"] == 2
    assert j["succeeded"] == 2
    lat = j["latencies"]
    assert lat["job_to_pod_s"] == 2.0    # 00:00:00 -> min pod created 00:00:02
    assert lat["pod_to_sched_s"] == 4.0  # min created 00:00:02 -> max scheduled 00:00:06
    assert lat["run_s"] == 350.0         # min start 00:00:10 -> max finish 00:06:00
