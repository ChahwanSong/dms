"""PV 목적지 root sync 의 결과 소유자 = 목적지 PV 루트 소유자(2026-10-26 사용자 결정 「아주아주 중요 — 목적지 PV 소유자로 자동
chown」, execution_manifests.pv_owner_measured·_auto_chown, stepper._record_pv_owner).

흐름: 목적지 쪽 preflight 가 봉쇄 확인 뒤 PV 루트의 uid:gid 를 마커(DMS_PV_OWNER=)로 찍고 → stepper 가 잡 worker_pool.dest_pv_owner
에 남기고 → 미리보기·실행 argv 에 --chown 으로 싣는다. exec_preflight 가 다시 재서 바뀌었으면 거부한다(확인 창과 실행이 갈라지지
않게). 마커가 없거나 틀리면 「모름」을 소스 소유 보존으로 접지 않고 거부한다.
"""
import json
import os
import subprocess

import pytest

from dms.domain import DataJobState, RequestState
from dms.execution import ExecStatus, JobSpec, StubExecutionAdapter
from dms.execution_manifests import (PREFLIGHT_REASONS, _preflight_script, parse_pv_owner, pv_owner_measured,
                                     tool_argv, valid_owner)
from dms.repositories import Repositories
from dms.stepper import JobStepper

_ROOT = {"uid": 0, "gid": 0, "username": "ops", "privileged": True}
_USER = {"uid": 10001, "gid": 10000, "username": "alice", "privileged": False}
_ABS = {"source": "/s/a", "destination": "/d/pv/out"}


def _spec(*, identity=_ROOT, contain=True, options=None, owner=None, tool="dsync", dryrun=False, phase="preview"):
    paths = {"source": "/s/a", "source_storage": "s", "destination": "/d/pv/out", "destination_storage": "d"}
    if contain:
        paths["contain"] = {"destination": {"root": "/d/pv", "anchor": "/d"}}
    return JobSpec(job_id="j1", phase=phase, operation="sync", tool=tool, dryrun=dryrun, identity=identity,
                   paths=paths, options=options or {}, candidates={"primary": ["n1"]}, process_count=1,
                   queue="dms-data", priority_class="dms-mid", artifact_base="file:///art", result_owner=owner)


# --- 술어·argv ------------------------------------------------------------------------------------------------

def test_measured_only_for_root_sync_into_a_pv_without_explicit_chown():
    assert pv_owner_measured(_spec()) is True
    assert pv_owner_measured(_spec(tool="nsync")) is True
    assert pv_owner_measured(_spec(contain=False)) is False             # 직접 경로 목적지 -- 종전(소스 소유 보존)
    assert pv_owner_measured(_spec(identity=_USER)) is False            # 비 root -- 종전(실행 신원)
    assert pv_owner_measured(_spec(options={"chown": "5:5"})) is False   # 명시 chown 이 이긴다
    src_only = _spec(contain=False)
    src_only.paths["contain"] = {"source": {"root": "/s", "anchor": "/s"}}
    assert pv_owner_measured(src_only) is False                          # PV 소스 → 직접 경로 목적지


def test_argv_carries_the_measured_owner_for_preview_and_execution():
    for tool in ("dsync", "nsync"):
        for dryrun in (True, False):
            argv = tool_argv(_spec(tool=tool, owner="1000:100", dryrun=dryrun), abs_paths=_ABS)
            assert argv.count("--chown") == 1 and argv[argv.index("--chown") + 1] == "1000:100", argv
    # 루트 소유(0:0)·nobody 여도 규칙 그대로(확인 창에 그 값이 보인다)
    assert "0:0" in tool_argv(_spec(owner="0:0"), abs_paths=_ABS)
    assert "65534:65534" in tool_argv(_spec(owner="65534:65534"), abs_paths=_ABS)


@pytest.mark.parametrize("owner", [None, "", "abc", "1000", "1000:", ":100", "-1:5", "1:2:3", "4294967295:1", 1000])
def test_argv_refuses_a_pv_sync_without_a_valid_measured_owner(owner):
    with pytest.raises(ValueError):
        tool_argv(_spec(owner=owner), abs_paths=_ABS)


def test_existing_branches_are_unchanged():
    assert "--chown" not in tool_argv(_spec(contain=False), abs_paths=_ABS)                       # root, 직접 경로
    assert tool_argv(_spec(contain=False, identity=_USER), abs_paths=_ABS)[:2] == ["--chown", "10001:10000"]
    argv = tool_argv(_spec(options={"chown": "7:7"}, owner="1000:100"), abs_paths=_ABS)           # 명시 chown 우선
    assert argv.count("--chown") == 1 and argv[argv.index("--chown") + 1] == "7:7"


def test_valid_owner():
    assert valid_owner("0:0") and valid_owner("4294967294:4294967294")
    for bad in (None, "", "1", "a:b", " 1:1", "1:1 ", "4294967295:0", "1:2:3", 5):
        assert not valid_owner(bad)


# --- 마커 파서 ---------------------------------------------------------------------------------------------------

def test_parse_pv_owner_needs_exactly_one_well_formed_marker():
    ok = [("dst-pod", "DMS_PV_OWNER=1000:100\nDMS_PREFLIGHT_OK\n", None)]
    assert parse_pv_owner(ok) == "1000:100"
    assert parse_pv_owner([("src-pod", "DMS_PREFLIGHT_OK", None)] + ok) == "1000:100"   # nsync: 목적지 파드만 찍는다
    assert parse_pv_owner([("p", "DMS_PREFLIGHT_OK", None)]) is None                     # 옛 파드 -- 없음
    assert parse_pv_owner([("p", None, None)]) is None                                   # 로그를 못 읽음
    assert parse_pv_owner(None) is None
    assert parse_pv_owner([("p", "DMS_PV_OWNER=1:1\nDMS_PV_OWNER=1:1", None)]) is None   # 둘 -- 위조 의심
    assert parse_pv_owner([("p", "DMS_PV_OWNER=root:root", None)]) is None
    assert parse_pv_owner([("p", "DMS_PV_OWNER=99999999999:1", None)]) is None


def test_pv_owner_unknown_is_a_whitelisted_preflight_reason():
    assert "pv_owner_unknown" in PREFLIGHT_REASONS


# --- preflight 스크립트(실 sh) -------------------------------------------------------------------------------------

def _run(spec, role=None):
    script, args = _preflight_script(spec, role=role)
    proc = subprocess.run(["sh", "-c", script, "sh", *args], capture_output=True, text=True)
    return proc.returncode, proc.stdout, script


def _tree_spec(tmp_path, *, tool="dsync", identity=_ROOT, options=None, root_rel="pvc-a/data"):
    m = tmp_path / "m"
    (m / "pvc-a/data/out").mkdir(parents=True)
    (m / "src").mkdir()
    root = str(m / root_rel)
    paths = {"source": str(m / "src"), "source_storage": "s", "destination": root + "/out/new",
             "destination_storage": "d", "contain": {"destination": {"root": root, "anchor": str(m)}}}
    return JobSpec(job_id="j1", phase="preflight", operation="sync", tool=tool, dryrun=False, identity=identity,
                   paths=paths, options=options or {}, candidates={"primary": ["n1"]}, process_count=1,
                   queue="dms-data", priority_class="dms-mid", artifact_base="file:///art"), m


def test_destination_preflight_prints_the_pv_root_owner(tmp_path):
    spec, m = _tree_spec(tmp_path)
    st = os.stat(m / "pvc-a/data")
    rc, out, _ = _run(spec)
    assert rc == 0, out
    assert parse_pv_owner([("p", out, None)]) == f"{st.st_uid}:{st.st_gid}"
    assert out.strip().endswith("DMS_PREFLIGHT_OK")
    # nsync 역할 파드: 목적지만 찍고 소스는 찍지 않는다
    spec, _ = _tree_spec(tmp_path / "n", tool="nsync")
    assert "DMS_PV_OWNER=" in _run(spec, role="destination")[1]
    assert "DMS_PV_OWNER=" not in _run(spec, role="source")[1]


def test_no_measurement_when_not_required(tmp_path):
    for i, kw in enumerate(({"options": {"chown": "5:5"}}, {"identity": _USER})):
        spec, _ = _tree_spec(tmp_path / str(i), **kw)
        _, out, script = _run(spec)
        assert "dms_pv_owner" not in script and "DMS_PV_OWNER=" not in out


def test_measurement_runs_after_containment(tmp_path):
    """PV 루트 자신이 링크면 봉쇄(dms_contain)가 먼저 막는다 -- 링크의 소유자를 재지 않는다."""
    spec, m = _tree_spec(tmp_path)
    (m / "pvc-b").mkdir()
    (m / "pvc-b/data").symlink_to(str(m / "pvc-a/data"))
    spec2, _ = _tree_spec(tmp_path / "x")
    root = str(m / "pvc-b/data")
    spec2.paths.update({"destination": root + "/out/new",
                        "contain": {"destination": {"root": root, "anchor": str(m)}}})
    rc, out, script = _run(spec2)
    assert rc != 0 and "DMS_PREFLIGHT_REASON=target_path_redirected" in out and "DMS_PV_OWNER=" not in out
    assert script.index("dms_contain \"$") < script.index("dms_pv_owner \"$")


def test_owner_function_refuses_a_non_directory_root(tmp_path):
    from dms.execution_manifests import _PV_OWNER_FN
    f = tmp_path / "file"
    f.write_text("x")
    link = tmp_path / "link"
    link.symlink_to(str(tmp_path))
    for target in (str(f), str(link), str(tmp_path / "missing")):
        proc = subprocess.run(["sh", "-c", "set -e; " + _PV_OWNER_FN + 'dms_pv_owner "$1"; echo OK', "sh", target],
                              capture_output=True, text=True)
        assert proc.returncode != 0 and "DMS_PREFLIGHT_REASON=pv_owner_unknown" in proc.stdout, target


# --- stepper 흐름 ------------------------------------------------------------------------------------------------

class _Settings:
    agent_report_stale_seconds = 300
    preview_ttl_seconds = 86400
    artifact_base_uri = "file:///art"
    allow_privileged_requesters = True
    privileged_requesters = frozenset({"ops"})
    vcjob_ttl_seconds = 86400


def _seed(repos, name):
    if repos.storages.get(name) is None:
        repos.storages.create(storage_name=name, mount_path=f"/{name}", managed_root=f"/{name}/m",
                              backend_type="nfs", actor="test")


def _job(repos, *, pv=True, chown=None, key="k"):
    _seed(repos, "src")
    _seed(repos, "dst")
    payload = {"source_storage": "src", "source": "a", "destination_storage": "dst", "destination": "pv/out",
               "run_as_root": True, "options": {"chown": chown} if chown else {}}
    if pv:
        payload["destination_pv"] = {"cluster": "c", "namespace": "n", "pvc": "p", "storage": "dst", "root": "pv"}
    rid = repos.requests.create(operation="sync", requester_id="ops", actor="ops", resource_key=key,
                                payload=payload, priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    pool = {"tool": "nsync", "identity": dict(_ROOT), "candidates": {"source": ["n1"], "destination": ["n2"]},
            "process_count": 2, "queue": "dms-data", "priority_class": "dms-mid"}
    jid = repos.data_jobs.create_job(rid, plan_id, operation="sync", priority="mid", source_storage="src",
                                     source="a", destination_storage="dst", destination="pv/out",
                                     options={"chown": chown} if chown else {}, tool="nsync", worker_pool=pool,
                                     precondition={}, actor="planner")
    return rid, jid


def _owner_log(owner):
    return [("dst", f"DMS_PV_OWNER={owner}\nDMS_PREFLIGHT_OK", None), ("src", "DMS_PREFLIGHT_OK", None)]


def _through_preflight(repos, adapter, jid, log):
    stepper = JobStepper(repos, adapter, settings=_Settings())
    stepper.run_once()                                       # preflight 제출
    if log is not None:
        adapter.set_log(f"stub-preflight-{jid}", log)
    stepper.run_once()                                       # preflight 성공 → 소유자 기록 → 미리보기 제출
    return stepper


def _specs(adapter, phase):
    return [s for s in adapter.submitted_specs() if s.phase == phase]


def test_preflight_owner_is_recorded_and_reaches_the_preview(db):
    repos = Repositories(db)
    adapter = StubExecutionAdapter()
    rid, jid = _job(repos)
    _through_preflight(repos, adapter, jid, _owner_log("1000:100"))
    job = repos.data_jobs.get_job(jid)
    assert job["worker_pool"]["dest_pv_owner"] == "1000:100"
    assert job["state"] == DataJobState.PREVIEW_RUNNING.value
    assert _specs(adapter, "preview")[0].result_owner == "1000:100"
    events = [e for e in repos.observability.events_for_request(rid) if e["event_type"] == "pv_owner_measured"]
    assert events and events[0]["payload"]["owner"] == "1000:100"


@pytest.mark.parametrize("log", [[("dst", "DMS_PREFLIGHT_OK", None)],
                                 [("dst", "DMS_PV_OWNER=1:1\nDMS_PV_OWNER=2:2", None)],
                                 [("src", "DMS_PREFLIGHT_OK", None), ("dst", "DMS_PV_OWNER=root:root", None)]])
def test_unknown_owner_rejects_instead_of_preserving_the_source_owner(db, log):
    repos = Repositories(db)
    adapter = StubExecutionAdapter()
    _, jid = _job(repos)
    _through_preflight(repos, adapter, jid, log)
    job = repos.data_jobs.get_job(jid)
    assert (job["state"], job["reason_code"]) == (DataJobState.REJECTED.value, "pv_owner_unknown")
    assert _specs(adapter, "preview") == []


def test_unreadable_log_is_retried_before_giving_up(db):
    """로그를 못 읽은 것(apiserver 순간 오류 -- read_log 의 log None)은 「마커 없음」과 다르다: 상태를 그대로 두고 다음 틱에 다시
    읽고(성공한 preflight 파드는 잡 종단까지 남는다), 상한(PV_OWNER_READ_ATTEMPTS)을 넘기면 그때 모름으로 거부한다."""
    from dms.stepper import PV_OWNER_READ_ATTEMPTS
    repos = Repositories(db)
    adapter = StubExecutionAdapter()
    _, jid = _job(repos)
    stepper = _through_preflight(repos, adapter, jid, [("src", "DMS_PREFLIGHT_OK", None), ("dst", None, None)])
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == DataJobState.PREFLIGHT.value and job["worker_pool"]["dest_pv_owner_read_misses"] == 1
    adapter.set_log(f"stub-preflight-{jid}", _owner_log("1000:100"))   # 다음 틱에 읽힌다
    stepper.run_once()
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == DataJobState.PREVIEW_RUNNING.value and job["worker_pool"]["dest_pv_owner"] == "1000:100"
    # 끝내 못 읽으면 상한에서 거부
    _, jid2 = _job(repos, key="k2")
    stepper = _through_preflight(repos, adapter, jid2, [("dst", None, None)])
    for _ in range(PV_OWNER_READ_ATTEMPTS):
        stepper.run_once()
    job2 = repos.data_jobs.get_job(jid2)
    assert (job2["state"], job2["reason_code"]) == (DataJobState.REJECTED.value, "pv_owner_unknown")


def test_preview_argv_built_from_the_stepper_spec_carries_the_owner(db):
    """stepper 가 만든 spec 그대로 argv 를 만들면 --chown <잰 값> 이 실린다(스텁은 argv 를 안 만들어 따로 묶는다)."""
    repos = Repositories(db)
    adapter = StubExecutionAdapter()
    _, jid = _job(repos)
    _through_preflight(repos, adapter, jid, _owner_log("1000:100"))
    spec = _specs(adapter, "preview")[0]
    argv = tool_argv(spec, abs_paths=spec.paths)
    assert argv[argv.index("--chown") + 1] == "1000:100" and "--dryrun" in argv


class _K8sSettings(_Settings):
    identity_mode = "k8s"


def test_job_row_chown_or_chmod_differing_from_the_request_row_fails_closed(db):
    """k8s 모드: 잡 행의 chown·chmod 만 변조(create_job 무검증 INSERT)하면 chown_not_allowed·PV 소유자 규칙을 함께 건너뛴다 --
    _build_spec 이 요청 행(판정받은 값)과 다르면 끊는다."""
    for key, value in (("chown", "0:0"), ("chmod", "F4755")):
        repos = Repositories(db)
        adapter = StubExecutionAdapter()
        _, jid = _job(repos, key=f"k-{key}")
        db.execute("UPDATE data_jobs SET options = :o WHERE job_id = :j", {"o": json.dumps({key: value}), "j": jid})
        JobStepper(repos, adapter, settings=_K8sSettings()).run_once()
        job = repos.data_jobs.get_job(jid)
        assert (job["state"], job["reason_code"]) == (DataJobState.REJECTED.value, "chown_not_allowed"), key
        assert adapter.submitted_specs() == []


def _to_exec_preflight(repos, adapter, jid, stored_owner, log):
    """미리보기를 건너뛰고 컨펌된 잡(EXECUTING)으로 만든 뒤 exec_preflight 를 굴린다."""
    job = repos.data_jobs.get_job(jid)
    wp = dict(job["worker_pool"])
    if stored_owner is not None:
        wp["dest_pv_owner"] = stored_owner
    repos.data_jobs.set_worker_pool(jid, wp, expected_state=job["state"])
    repos.data_jobs.set_job_state(jid, DataJobState.EXECUTING, actor="test")
    stepper = JobStepper(repos, adapter, settings=_Settings())
    stepper.run_once()                                       # exec_preflight 제출
    adapter.set_log(f"stub-exec_preflight-{jid}", log)
    stepper.run_once()                                       # 성공 → 재측정 → 실행 제출(또는 거부)
    return repos.data_jobs.get_job(jid)


def test_exec_preflight_same_owner_runs_with_it(db):
    repos = Repositories(db)
    adapter = StubExecutionAdapter()
    _, jid = _job(repos)
    job = _to_exec_preflight(repos, adapter, jid, "1000:100", _owner_log("1000:100"))
    assert job["state"] == DataJobState.EXECUTING.value
    assert _specs(adapter, "execution")[0].result_owner == "1000:100"


def test_exec_preflight_changed_owner_is_rejected(db):
    repos = Repositories(db)
    adapter = StubExecutionAdapter()
    rid, jid = _job(repos)
    job = _to_exec_preflight(repos, adapter, jid, "1000:100", _owner_log("2000:200"))
    assert (job["state"], job["reason_code"]) == (DataJobState.REJECTED.value, "pv_owner_changed")
    assert _specs(adapter, "execution") == []
    ev = [e for e in repos.observability.events_for_request(rid) if e["event_type"] == "pv_owner_changed"]
    assert ev and ev[0]["payload"]["before"] == "1000:100" and ev[0]["payload"]["after"] == "2000:200"


def test_exec_preflight_adopts_when_nothing_was_stored(db):
    """배포 전에 미리보기까지 간 잡(저장값 없음)은 끊지 않고 그때 잰 값을 쓴다."""
    repos = Repositories(db)
    adapter = StubExecutionAdapter()
    _, jid = _job(repos)
    job = _to_exec_preflight(repos, adapter, jid, None, _owner_log("1000:100"))
    assert job["state"] == DataJobState.EXECUTING.value
    assert job["worker_pool"]["dest_pv_owner"] == "1000:100"
    assert _specs(adapter, "execution")[0].result_owner == "1000:100"


class _CountingAdapter(StubExecutionAdapter):
    def __init__(self):
        super().__init__()
        self.log_reads = 0

    def read_log(self, ref):
        self.log_reads += 1
        return super().read_log(ref)


@pytest.mark.parametrize("kw", [{"pv": False}, {"chown": "5:5"}])
def test_jobs_outside_the_rule_never_read_the_preflight_log(db, kw):
    repos = Repositories(db)
    adapter = _CountingAdapter()
    _, jid = _job(repos, **kw)
    _through_preflight(repos, adapter, jid, None)
    assert adapter.log_reads == 0
    spec = _specs(adapter, "preview")[0]
    assert spec.result_owner is None
