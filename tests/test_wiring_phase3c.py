from dms.build_runner import BuildRunner, StubBuildRunner
from dms.config import Settings
from dms.execution import StubExecutionAdapter
from dms.execution_volcano import VolcanoExecutionAdapter
from dms.queue_reader import StubQueueReader, VolcanoQueueReader
from dms.repositories import Repositories
from dms.rollout_runner import RolloutRunner, StubRolloutRunner
from dms.wiring import (build_build_runner, build_execution_adapter,
                        build_identity_resolver, build_queue_reader,
                        build_rollout_runner)

BASE = {"DMS_DATABASE_URL": "sqlite:///tmp/x.db", "DMS_SHARED_TOKEN": "t",
        "DMS_ADMIN_TOKEN": "a", "DMS_SESSION_SECRET": "s"}


def test_stub_backend_default(db):
    settings = Settings.from_env(BASE)
    adapter = build_execution_adapter(settings, Repositories(db))
    assert isinstance(adapter, StubExecutionAdapter)
    assert build_identity_resolver(settings) is None


def test_volcano_backend_builds_adapter(db):
    settings = Settings.from_env({**BASE, "DMS_EXECUTION_BACKEND": "volcano",
                                  "DMS_JOB_IMAGE": "reg/img:1"})
    adapter = build_execution_adapter(settings, Repositories(db))
    assert isinstance(adapter, VolcanoExecutionAdapter)


def test_ldap_resolver_built_when_configured(db):
    settings = Settings.from_env({**BASE, "DMS_LDAP_URI": "ldap://x:389",
        "DMS_LDAP_USER_BASE": "ou=People", "DMS_LDAP_GROUP_BASE": "ou=Groups"})
    r = build_identity_resolver(settings)
    assert r is not None and hasattr(r, "resolve")


def test_build_runner_is_stub_when_backend_is_not_volcano(db):
    settings = Settings.from_env(BASE)
    assert isinstance(build_build_runner(settings, Repositories(db)), StubBuildRunner)


def test_build_runner_reads_timeout_from_settings(db):
    # C2(a): wiring이 settings.build_timeout_seconds를 BuildRunner에 실제로
    # 전달하는지 -- 빠지면 파드에 activeDeadlineSeconds가 안 실린다.
    settings = Settings.from_env({**BASE, "DMS_EXECUTION_BACKEND": "volcano",
                                  "DMS_JOB_IMAGE": "reg/img:1",
                                  "DMS_BUILD_TIMEOUT_SECONDS": "111"})
    runner = build_build_runner(settings, Repositories(db))
    assert isinstance(runner, BuildRunner)
    assert runner._timeout_seconds == 111


def test_rollout_runner_is_stub_when_backend_is_not_volcano():
    settings = Settings.from_env(BASE)
    assert isinstance(build_rollout_runner(settings), StubRolloutRunner)


def test_rollout_runner_builds_runner_when_volcano():
    settings = Settings.from_env({**BASE, "DMS_EXECUTION_BACKEND": "volcano",
                                  "DMS_JOB_IMAGE": "reg/img:1"})
    runner = build_rollout_runner(settings)
    assert isinstance(runner, RolloutRunner)
    assert runner._ns == settings.k8s_namespace


def test_queue_reader_is_stub_when_backend_is_not_volcano():
    # 기본 백엔드(stub)에서 스텁 페어가 안 꽂히면 conftest 의 create_app 경로
    # 전부가 /api/admin/metrics/queue 에서 500 이다(설계 §2.5).
    settings = Settings.from_env(BASE)
    assert isinstance(build_queue_reader(settings), StubQueueReader)


def test_queue_reader_builds_volcano_reader_when_volcano():
    settings = Settings.from_env({**BASE, "DMS_EXECUTION_BACKEND": "volcano",
                                  "DMS_JOB_IMAGE": "reg/img:1"})
    reader = build_queue_reader(settings)
    assert isinstance(reader, VolcanoQueueReader)
    assert reader._namespace == settings.k8s_namespace
    assert reader._queue == "dms-data"


# --- 제어면 root 전환(2026-09-09): 컨트롤러 summary 읽기가 봉쇄 사슬로 배선돼 있는가 ----
# 배선은 wiring.build_execution_adapter 의 한 줄(read_text = build_summary_reader(repos))
# 이다. 예전 open(path).read() 헬퍼로 되돌리면 root 컨트롤러가 심링크를 따라가고
# FIFO 에 멈추는데 다른 어떤 테스트도 빨간불이 아니다(리뷰 재현) -- 여기서 고정한다.
import os  # noqa: E402

import dms.wiring as wiring_mod  # noqa: E402
from dms.domain import RequestState  # noqa: E402

VOLCANO = {**BASE, "DMS_EXECUTION_BACKEND": "volcano", "DMS_JOB_IMAGE": "reg/img:1"}


def test_volcano_adapter_reads_summaries_through_the_contained_reader(db, monkeypatch):
    sentinel = object()
    monkeypatch.setattr(wiring_mod, "build_summary_reader", lambda repos: sentinel)
    adapter = build_execution_adapter(Settings.from_env(VOLCANO), Repositories(db))
    assert adapter._read_text is sentinel


def test_volcano_adapter_summary_symlink_is_none_and_regular_file_is_parsed(db, tmp_path):
    repos = Repositories(db)
    rid = repos.requests.create(operation="scan", requester_id="alice", actor="alice",
        resource_key="k", payload={"storage": "s1", "target": "a"}, priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="scan", priority="mid",
        storage_name="s1", target="a", options={}, tool="dscan",
        worker_pool={"identity": {"uid": os.getuid(), "gid": os.getgid(),
                                  "username": "alice"}, "candidates": {"primary": ["n1"]}},
        precondition={}, actor="planner")
    settings = Settings.from_env({**VOLCANO, "DMS_ARTIFACT_BASE_URI": f"file://{tmp_path}"})
    adapter = build_execution_adapter(settings, repos)
    d = tmp_path / jid / "execution"
    d.mkdir(parents=True)
    path = f"{tmp_path}/{jid}/execution/summary.json"
    adapter._summary_paths["vcjob/x"] = path        # k8s 를 건드리지 않는 빠른 경로
    # 양성 대조군: 정규 summary 는 파싱된다(항상 None 인 리더가 통과하지 못하도록)
    (d / "summary.json").write_text('{"returncode": 0, "files": 1, "bytes": 2}')
    assert adapter.read_summary("vcjob/x") == {"returncode": 0, "files": 1, "bytes": 2}
    # 심링크는 root 여도 따라가지 않는다
    (d / "summary.json").unlink()
    victim = tmp_path / "victim.json"
    victim.write_text('{"secret": "TOP-SECRET"}')
    os.symlink(victim, d / "summary.json")
    assert adapter.read_summary("vcjob/x") is None
