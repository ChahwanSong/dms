"""preflight 실패 사유 세분화: 스크립트 마커를 잡 reason_code 로 승격한다.

실증(d65): 목적지 부모가 없는 중첩 경로 sync 는 preflight 에서 정확히 막혔는데
포탈에 뜬 사유는 `preflight_failed` 하나였다 -- 스크립트는 이미
`DMS_PREFLIGHT_REASON=destination_parent_not_writable` 를 찍고 있었지만 스테퍼가
그 마커를 읽는 경로 자체가 없었다(빌드 쪽 BuildWatcher 에만 있었다). 운영자는
파드 로그를 직접 열어야 원인을 알 수 있었고, 파드는 GC 로 시한부다.

승격은 화이트리스트로만 한다. 파드 로그는 신뢰 입력이 아니라(설계 §4) 임의
문자열을 reason_code 에 박으면 프론트 매핑(REASON_MESSAGES)이 없어 화면에 원문
코드가 그대로 뜬다 -- 미지 마커는 기존 폴백으로 접는다.
"""
import json

import pytest

from dms.domain import DataJobState, RequestState
from dms.execution import ExecStatus, ExecutionError, StubExecutionAdapter
from dms.execution_manifests import PREFLIGHT_REASONS
from dms.repositories import Repositories
from dms.stepper import JobStepper


class _Settings:
    agent_report_stale_seconds = 300
    preview_ttl_seconds = 86400
    artifact_base_uri = "file:///art"
    allow_privileged_requesters = False
    privileged_requesters = frozenset()
    vcjob_ttl_seconds = 86400


def _seed_storage(repos, name):
    if repos.storages.get(name) is None:
        repos.storages.create(storage_name=name, mount_path=f"/{name}",
                              managed_root=f"/{name}/dms", backend_type="cephfs",
                              actor="test")


def _pool(tool):
    return {"tool": tool, "identity": {"uid": 10001, "gid": 10000,
            "username": "alice", "groups": [], "privileged": False},
            "candidates": {"primary": ["n1"]}, "process_count": 8,
            "queue": "dms-data", "priority_class": "dms-mid"}


def _sync_job(repos, key="k-sync"):
    _seed_storage(repos, "src")
    _seed_storage(repos, "dst")
    rid = repos.requests.create(operation="sync", requester_id="alice", actor="alice",
        resource_key=key, payload={"source_storage": "src", "source": "a",
        "destination_storage": "dst", "destination": "b"}, priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="sync", priority="mid",
        source_storage="src", source="a", destination_storage="dst", destination="b",
        options={}, tool="dsync", worker_pool=_pool("dsync"), precondition={},
        actor="planner")
    return rid, jid


def _stepper(repos, adapter):
    return JobStepper(repos, adapter, settings=_Settings())


def _fail_preflight(repos, adapter, jid, log_entries):
    """Pending -> Preflight 로 한 틱 굴린 뒤 preflight 를 실패시킨다."""
    stepper = _stepper(repos, adapter)
    stepper.run_once()
    ref = f"stub-preflight-{jid}"
    adapter.script(ref, [ExecStatus.FAILED])
    if log_entries is not None:
        adapter.set_log(ref, log_entries)
    stepper.run_once()
    return repos.data_jobs.get_job(jid)


# ---- 마커 승격 ----

@pytest.mark.parametrize("marker", sorted(PREFLIGHT_REASONS))
def test_every_script_marker_is_promoted_to_the_job_reason_code(db, marker):
    repos = Repositories(db)
    rid, jid = _sync_job(repos, key=f"k-{marker}")
    adapter = StubExecutionAdapter()
    job = _fail_preflight(repos, adapter, jid,
                          [("pf-pod", f"DMS_PREFLIGHT_REASON={marker}\n", None)])
    assert (job["state"], job["reason_code"]) == ("Rejected", marker)


def test_destination_not_directory_reaches_the_request_side_too(db):
    # 배치 항목의 「사유」 열은 요청 전이의 사유(last_reason_code)를 읽는다 --
    # 잡 행에만 남으면 배치 화면은 그대로 뭉뚱그린다.
    repos = Repositories(db)
    rid, jid = _sync_job(repos)
    adapter = StubExecutionAdapter()
    _fail_preflight(repos, adapter, jid,
                    [("pf-pod", "DMS_PREFLIGHT_REASON=destination_not_directory", None)])
    assert repos.requests.last_reason_code(rid) == "destination_not_directory"


def test_marker_is_found_in_a_later_pod_of_a_multi_pod_preflight(db):
    # nsync 는 소스 노드·목적지 노드 파드가 따로다(ref="pods/a,b") -- 한쪽만
    # 실패하는 것이 정상 경로라 첫 항목만 보면 사유를 놓친다.
    repos = Repositories(db)
    rid, jid = _sync_job(repos)
    adapter = StubExecutionAdapter()
    job = _fail_preflight(repos, adapter, jid, [
        ("pf-src", "DMS_PREFLIGHT_OK\n", None),
        ("pf-dst", "noise\nDMS_PREFLIGHT_REASON=destination_not_directory\n", None)])
    assert job["reason_code"] == "destination_not_directory"


# ---- fail-safe 폴백 ----

def test_unknown_marker_folds_to_preflight_failed(db):
    # 화이트리스트 밖 문자열을 사유로 쓰면 프론트 매핑이 깨진다.
    repos = Repositories(db)
    rid, jid = _sync_job(repos)
    adapter = StubExecutionAdapter()
    job = _fail_preflight(repos, adapter, jid,
                          [("pf-pod", "DMS_PREFLIGHT_REASON=totally_made_up\n", None)])
    assert (job["state"], job["reason_code"]) == ("Rejected", "preflight_failed")


def test_empty_marker_value_folds_to_preflight_failed(db):
    repos = Repositories(db)
    rid, jid = _sync_job(repos)
    adapter = StubExecutionAdapter()
    job = _fail_preflight(repos, adapter, jid,
                          [("pf-pod", "DMS_PREFLIGHT_REASON=\n", None)])
    assert job["reason_code"] == "preflight_failed"


def test_no_marker_at_all_folds_to_preflight_failed(db):
    # OOMKilled·이미지 pull 실패처럼 스크립트가 아예 못 돈 실패다.
    repos = Repositories(db)
    rid, jid = _sync_job(repos)
    adapter = StubExecutionAdapter()
    job = _fail_preflight(repos, adapter, jid, [("pf-pod", "", None)])
    assert job["reason_code"] == "preflight_failed"


def test_null_log_folds_to_preflight_failed(db):
    # log=None 은 "얻을 수 없었다"(파드 GC)다 -- 빈 문자열과 다르지만 둘 다
    # "마커 없음"이고, 사유를 지어내지 않는다.
    repos = Repositories(db)
    rid, jid = _sync_job(repos)
    adapter = StubExecutionAdapter()
    job = _fail_preflight(repos, adapter, jid, [("pf-pod", None, None)])
    assert job["reason_code"] == "preflight_failed"


class _LogRaisingAdapter(StubExecutionAdapter):
    def read_log(self, ref):
        raise ExecutionError("poll_failed", "apiserver down")


def test_log_read_failure_still_finalizes_with_the_fallback(db):
    # 로그 조회 실패가 종단 전이를 막으면 잡이 낀다(기존 박제 규약과 동일).
    repos = Repositories(db)
    rid, jid = _sync_job(repos)
    adapter = _LogRaisingAdapter()
    job = _fail_preflight(repos, adapter, jid, None)
    assert (job["state"], job["reason_code"]) == ("Rejected", "preflight_failed")


# ---- 박제와 사유는 같은 한 번의 read_log 에서 나온다 ----

class _CountingAdapter(StubExecutionAdapter):
    def __init__(self):
        super().__init__()
        self.read_log_calls = 0

    def read_log(self, ref):
        self.read_log_calls += 1
        return super().read_log(ref)


def test_reason_and_diag_come_from_a_single_log_read(db):
    # 두 번 읽으면 (파드가 그 사이 GC 되는 등) 박제된 로그와 사유가 어긋날 수
    # 있고, k8s 조회도 공짜가 아니다.
    repos = Repositories(db)
    rid, jid = _sync_job(repos)
    adapter = _CountingAdapter()
    job = _fail_preflight(repos, adapter, jid,
                          [("pf-pod", "DMS_PREFLIGHT_REASON=source_not_readable", None)])
    assert job["reason_code"] == "source_not_readable"
    doc = json.loads(job["diag_logs"])
    assert doc["phase"] == "preflight"
    assert "source_not_readable" in doc["entries"][0]["log"]
    assert adapter.read_log_calls == 1


# ---- confirm 후 재검증(exec_preflight)도 같은 스크립트다 ----

def _to_executing(repos, adapter, jid):
    stepper = _stepper(repos, adapter)
    adapter.set_summary(f"stub-preview-{jid}", {"files": 3, "bytes": 9})
    stepper.run_once()                                   # Pending -> Preflight
    stepper.run_once()                                   # Preflight ok -> PreviewRunning
    stepper.run_once()                                   # Preview ok -> ConfirmPending
    repos.data_jobs.set_job_state(jid, DataJobState.EXECUTING, actor="test")
    stepper.run_once()                                   # exec_preflight 제출
    return stepper


def test_exec_preflight_marker_is_promoted_too(db):
    # 미리보기와 confirm 사이에 목적지가 파일로 바뀌는 TOCTOU 가 여기서 잡힌다 --
    # 같은 스크립트가 도는데 사유만 execution_recheck_failed 로 뭉개질 이유가 없다.
    repos = Repositories(db)
    rid, jid = _sync_job(repos)
    adapter = StubExecutionAdapter()
    stepper = _to_executing(repos, adapter, jid)
    ref = f"stub-exec_preflight-{jid}"
    adapter.script(ref, [ExecStatus.FAILED])
    adapter.set_log(ref, [("re-pf", "DMS_PREFLIGHT_REASON=destination_not_directory", None)])
    stepper.run_once()
    job = repos.data_jobs.get_job(jid)
    assert (job["state"], job["reason_code"]) == ("Rejected", "destination_not_directory")


def test_exec_preflight_without_marker_keeps_execution_recheck_failed(db):
    repos = Repositories(db)
    rid, jid = _sync_job(repos)
    adapter = StubExecutionAdapter()
    stepper = _to_executing(repos, adapter, jid)
    ref = f"stub-exec_preflight-{jid}"
    adapter.script(ref, [ExecStatus.FAILED])
    adapter.set_log(ref, [("re-pf", "DMS_PREFLIGHT_REASON=totally_made_up", None)])
    stepper.run_once()
    job = repos.data_jobs.get_job(jid)
    assert (job["state"], job["reason_code"]) == ("Rejected", "execution_recheck_failed")
