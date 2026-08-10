"""resolve_artifact_base(슬라이스 18 설계 §2.1)와 소비자 4곳 재배선의 계약.

핵심: DB(control_state.artifact_base_uri)가 env 를 이기고, NULL 이면 env 로
떨어진다(하위호환 -- 기존 배포 무변화). 소비자가 설정 스냅숏을 캡처해 두면 base
변경이 재시작 전까지 반영되지 않는다(설계 §1-7) -- 어댑터에는 가변 callable 을
주입해 호출 시점 해석을, stepper 에는 DB 값 주입으로 스냅숏 부재를 단언한다."""
import json

from dms.artifact_base import resolve_artifact_base, strip_scheme
from dms.domain import DataJobState
from dms.execution import StubExecutionAdapter
from dms.execution_volcano import VolcanoExecutionAdapter
from dms.repositories import Repositories
from dms.stepper import JobStepper

ADMIN = {"Authorization": "Bearer tok-shared", "x-dms-actor": "ops"}


class _Settings:
    agent_report_stale_seconds = 300
    preview_ttl_seconds = 86400
    artifact_base_uri = "file:///env/base"
    allow_privileged_requesters = False
    privileged_requesters = frozenset()
    vcjob_ttl_seconds = 86400


def test_resolve_falls_back_to_env_when_db_null(db):
    repos = Repositories(db)
    assert resolve_artifact_base(repos.control, _Settings()) == "file:///env/base"


def test_resolve_prefers_db_value(db):
    repos = Repositories(db)
    repos.control.set_artifact_base("file:///db/base", actor="ops")
    assert resolve_artifact_base(repos.control, _Settings()) == "file:///db/base"


def test_strip_scheme_strips_prefix_only():
    # 전체 치환(replace) 계열과의 차이가 이 함수의 존재 이유다(설계 §2.2):
    # 접두가 아닌 위치의 file:// 는 경로의 일부로 보존돼야 한다.
    assert strip_scheme("file:///a/b") == "/a/b"
    assert strip_scheme("/a/b") == "/a/b"
    assert strip_scheme("file:///a/file://b") == "/a/file://b"


# ---- 소비자 ① stepper (3사용처: _build_spec / 성공 execution / 성공 preview) ----

def _scan_job(repos):
    from dms.domain import RequestState
    rid = repos.requests.create(operation="scan", requester_id="alice", actor="alice",
        resource_key="k", payload={"storage": "s1", "target": "a"}, priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    return repos.data_jobs.create_job(rid, plan_id, operation="scan", priority="mid",
        storage_name="s1", target="a", options={}, tool="dscan",
        worker_pool={"identity": {}, "candidates": {"primary": ["n1"]},
                     "process_count": 1, "queue": "dms-data",
                     "priority_class": "dms-mid"},
        precondition={}, actor="planner")


def test_stepper_builds_specs_with_db_base(db):
    # 소비자 ①(stepper._build_spec): 매 틱 재조회 -- 정책 재조회와 같은 패턴.
    # env 스냅숏이 남아 있으면 file:///env/base 로 깨진다.
    repos = Repositories(db)
    repos.control.set_artifact_base("file:///db/base", actor="ops")
    _scan_job(repos)
    adapter = StubExecutionAdapter()
    JobStepper(repos, adapter, settings=_Settings()).run_once()
    assert adapter.submitted_specs()[0].artifact_base == "file:///db/base"


def test_stepper_records_artifact_uri_under_db_base(db):
    # 소비자 ①-보강: artifact_uri 기록(stepper 성공 execution 경로)이 env 로
    # 남으면 포탈이 옛 경로를 가리킨다. StubExecutionAdapter 는 poll 기본
    # Succeeded 라 3틱이면 Pending -> Preflight -> Running -> Succeeded 다.
    repos = Repositories(db)
    repos.control.set_artifact_base("file:///db/base", actor="ops")
    jid = _scan_job(repos)
    adapter = StubExecutionAdapter()
    adapter.set_summary(f"stub-execution-{jid}", {"files": 1})
    stepper = JobStepper(repos, adapter, settings=_Settings())
    stepper.run_once()   # Pending -> Preflight
    stepper.run_once()   # Preflight -> Running
    stepper.run_once()   # Running -> Succeeded
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == "Succeeded"
    assert job["artifact_uri"] == f"file:///db/base/{jid}"


# ---- 소비자 ② VolcanoExecutionAdapter: 호출 시점 해석 ----

def test_adapter_reconstructs_summary_path_at_call_time():
    # 생성자 캡처가 남아 있으면 base 변경 후 컨트롤러 재시작 시 in-flight 잡의
    # summary 를 옛 경로에서 찾는다(설계 §1-7) -- callable 주입으로 호출 시점
    # 해석을 고정한다.
    class _K8s:
        def get(self, kind, name, namespace):
            return {"metadata": {"labels": {"dms.io/job-id": "a" * 32,
                                            "dms.io/phase": "execution"}}}
    current = {"base": "file:///old"}
    read_paths = []
    adapter = VolcanoExecutionAdapter(
        _K8s(), job_image="img", namespace="dms", storages_lookup=lambda n: None,
        read_text=lambda p: read_paths.append(p) or None,
        artifact_base=lambda: current["base"])
    adapter.read_summary("vcjob/dms-scan-execution-x")
    current["base"] = "file:///new"
    adapter.read_summary("vcjob/dms-scan-execution-x")
    assert read_paths == [f"/old/{'a' * 32}/execution/summary.json",
                          f"/new/{'a' * 32}/execution/summary.json"]


def test_adapter_still_accepts_a_fixed_string_base():
    # 기존 테스트 10여 곳과 스텁 조립 경로 호환: 문자열이면 고정 base 로 동작한다.
    adapter = VolcanoExecutionAdapter(
        object(), job_image="img", namespace="dms", storages_lookup=lambda n: None,
        read_text=lambda p: None, artifact_base="file:///fixed")
    assert adapter._artifact_base_fn() == "file:///fixed"


# ---- 소비자 ③④ 읽기 라우트 2곳 ----

def test_api_base_helper_resolves_from_db(db):
    from types import SimpleNamespace
    from dms.api.routes_artifacts import _base
    repos = Repositories(db)
    repos.control.set_artifact_base("file:///db/base", actor="ops")
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        repos=repos, settings=_Settings())))
    assert _base(request) == "/db/base"


def test_scan_path_stats_reads_under_db_base(client, db, tmp_path):
    # 소비자 ④(routes_scan_paths): DB base 아래 실제 리포트를 두고 통계가 그것을
    # 읽는지. 재배선이 빠지면 env(file:///artifacts/dms)를 보고 404
    # no_covering_scan 으로 조용히 퇴행한다(설계 §1-5).
    repos = Repositories(db)
    admin = ADMIN
    client.post("/api/admin/storages", json={
        "storage_name": "ceph-a", "mount_path": "/mnt/ceph",
        "managed_root": "/mnt/ceph/dms", "backend_type": "cephfs"}, headers=admin)
    client.post("/api/auth/signup", json={"username": "alice", "password": "p"})
    client.post("/api/auth/login", json={"username": "alice", "password": "p"})
    path_id = client.post("/api/user/scan-paths",
                          json={"storage_name": "ceph-a", "path": "team"}).json()["id"]
    rid = repos.requests.create(operation="scan", requester_id="alice", actor="alice",
        resource_key="rk", payload={"storage": "ceph-a", "target": "team"},
        priority="mid")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="scan", priority="mid",
        storage_name="ceph-a", target="team", options={}, tool="dscan",
        worker_pool={}, precondition={}, actor="planner")
    repos.data_jobs.set_job_state(jid, DataJobState.SUCCEEDED, actor="stepper")
    d = tmp_path / jid / "execution"
    d.mkdir(parents=True)
    (d / "dscan-report.json").write_text(json.dumps({
        "generated_at_epoch": 1, "summary": {"total_entries": 1},
        "file_size_histogram": [], "time_histograms": {}}))
    repos.control.set_artifact_base(f"file://{tmp_path}", actor="ops")
    r = client.get(f"/api/user/scan-paths/{path_id}/stats")
    assert r.status_code == 200
    assert r.json()["summary"] == {"total_entries": 1}
