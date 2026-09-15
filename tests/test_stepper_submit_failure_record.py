"""제출 실패 원문 보존(2026-09-15 프로덕션 사고).

apiserver 가 볼륨 이름 RFC 1123 위반으로 422 를 냈는데 포탈은 `submit_failed` 코드만,
컨트롤러 로그·events·DB 어디에도 causes 가 없어 컨트롤러 파드 안에서 제출 경로를
재현해야 했다. 이제 stepper 는 어댑터의 ExecutionError.detail 을 (1) 관측 이벤트
submit_failed 와 (2) diag_logs 합성 항목(pod="submit:<phase>")으로 남긴다."""
import json

from dms.domain import RequestState
from dms.execution import ExecutionError, StubExecutionAdapter
from dms.repositories import Repositories
from dms.stepper import JobStepper

DETAIL = ('422 Unprocessable Entity: spec.volumes[0].name: Invalid value: "mgmt_storage": '
          "a lowercase RFC 1123 label must consist of lower case alphanumeric characters or '-'")


class _Settings:
    agent_report_stale_seconds = 300
    preview_ttl_seconds = 86400
    artifact_base_uri = "file:///art"
    allow_privileged_requesters = False
    privileged_requesters = frozenset()
    vcjob_ttl_seconds = 86400


class _RejectingAdapter(StubExecutionAdapter):
    """실 어댑터처럼 create 예외를 submit_failed + str(exc)[:200] 으로 접어 던진다."""

    def submit(self, spec):
        raise ExecutionError("submit_failed", DETAIL[:200])


def _scan_job(repos, storage="s1"):
    if repos.storages.get(storage) is None:
        repos.storages.create(storage_name=storage, mount_path=f"/{storage}",
                              managed_root=f"/{storage}/dms", backend_type="cephfs",
                              actor="test")
    rid = repos.requests.create(operation="scan", requester_id="alice", actor="alice",
        resource_key="k", payload={"storage": storage, "target": "a"}, priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="scan", priority="mid",
        storage_name=storage, target="a", options={}, tool="dscan",
        worker_pool={"tool": "dscan", "identity": {"uid": 10001, "gid": 10000,
            "username": "alice", "groups": [], "privileged": False},
            "candidates": {"primary": ["n1"]}, "process_count": 8,
            "queue": "dms-data", "priority_class": "dms-mid"},
        precondition={}, actor="planner")
    return rid, jid


def test_preflight_submit_failure_keeps_the_apiserver_detail(db):
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    result = JobStepper(repos, _RejectingAdapter(), settings=_Settings()).run_once()
    assert result[jid] == "Rejected"
    job = repos.data_jobs.get_job(jid)
    assert job["reason_code"] == "preflight_submit_failed:submit_failed"

    # (1) 운영 콘솔(이벤트): 원문이 message 에 그대로.
    events = [e for e in repos.observability.events_for_request(rid)
              if e["event_type"] == "submit_failed"]
    assert len(events) == 1
    assert "RFC 1123" in events[0]["message"] and events[0]["message"].startswith("preflight: ")
    assert events[0]["payload"] == {"phase": "preflight", "reason_code": "submit_failed"}
    assert events[0]["request_id"] == rid

    # (2) 요청 상세(진단 로그): phase_ref 가 없어도 API 가 돌려줄 합성 항목.
    doc = json.loads(job["diag_logs"])
    assert doc["phase"] == "preflight"
    assert doc["entries"] == [{"pod": "submit:preflight",
                               "log": "submit_failed: " + DETAIL[:200], "truncated": False}]


def test_record_failure_never_blocks_finalize(db, monkeypatch):
    """박제·이벤트 기록이 죽어도 잡은 종단돼야 한다 -- 진단이 본 기능을 막으면 잡이 낀다."""
    repos = Repositories(db)
    rid, jid = _scan_job(repos)

    def boom(*a, **k):
        raise RuntimeError("db gone")
    monkeypatch.setattr(repos.data_jobs, "archive_diag_logs", boom)
    result = JobStepper(repos, _RejectingAdapter(), settings=_Settings()).run_once()
    assert result[jid] == "Rejected"
    assert repos.data_jobs.get_job(jid)["reason_code"] == "preflight_submit_failed:submit_failed"
