"""PV 목적지 root sync 의 결과 소유자(2026-10-26 사용자 결정·지시, execution_manifests.pv_dest_owner_applies·pv_owner_measured·
_auto_chown, stepper._owner_rule·_record_pv_owner).

규칙(요청 destination_pv 블록의 owner_rule -- 제출이 PV 목적지 sync 마다 싣는다, routes_requests.submit):
  - "storage"(기본, 일반 사용자는 늘 이것): 결과 = 목적지 스토리지 기본 소유자(스토리지 항목의 default_uid:default_gid, 기본
    0:0). 제출이 블록 owner 에 기록한 값을 stepper 가 JobSpec.result_owner 로 싣고 미리보기·실행 argv 에 --chown 으로 넣는다 --
    재지 않는다(preflight 는 측정 전과 같은 봉쇄만). 0:0 이어도 --chown 0:0 을 싣는다(포크가 --chown 이면 setuid/setgid 를 지운다).
  - "pv_root"(특권 운영자 옵션)·규칙 없음(이 기능 전에 제출된 옛 행): 목적지 쪽 preflight 가 봉쇄 확인 뒤 PV 루트의 uid:gid 를
    마커(DMS_PV_OWNER=)로 찍고 → stepper 가 잡 worker_pool.dest_pv_owner 에 남기고 → --chown 으로 싣는다. exec_preflight 가 다시
    재서 바뀌었으면 거부한다(확인 창과 실행이 갈라지지 않게). 마커가 없거나 틀리면 「모름」을 소스 소유 보존으로 접지 않고 거부한다.
"""
import dataclasses
import json
import os
import subprocess

import pytest

from dms.domain import DataJobState, RequestState
from dms.execution import ExecStatus, JobSpec, StubExecutionAdapter
from dms.execution_manifests import (PREFLIGHT_REASONS, _preflight_script, parse_pv_owner, pv_dest_owner_applies,
                                     pv_owner_measured, tool_argv, valid_owner)
from dms.repositories import Repositories
from dms.stepper import JobStepper

_ROOT = {"uid": 0, "gid": 0, "username": "ops", "privileged": True}
_USER = {"uid": 10001, "gid": 10000, "username": "alice", "privileged": False}
_ABS = {"source": "/s/a", "destination": "/d/pv/out"}


def _spec(*, identity=_ROOT, contain=True, options=None, owner=None, rule="pv_root", tool="dsync", dryrun=False,
          phase="preview"):
    paths = {"source": "/s/a", "source_storage": "s", "destination": "/d/pv/out", "destination_storage": "d"}
    if contain:
        paths["contain"] = {"destination": {"root": "/d/pv", "anchor": "/d"}}
    return JobSpec(job_id="j1", phase=phase, operation="sync", tool=tool, dryrun=dryrun, identity=identity,
                   paths=paths, options=options or {}, candidates={"primary": ["n1"]}, process_count=1,
                   queue="dms-data", priority_class="dms-mid", artifact_base="file:///art", result_owner=owner,
                   result_owner_rule=rule)


# --- 술어·argv ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("rule", ["storage", "pv_root", None])
def test_rule_applies_to_root_sync_into_a_pv_without_explicit_chown(rule):
    assert pv_dest_owner_applies(_spec(rule=rule)) is True
    assert pv_dest_owner_applies(_spec(rule=rule, tool="nsync")) is True
    assert pv_dest_owner_applies(_spec(rule=rule, contain=False)) is False             # 직접 경로 목적지 -- 소스 소유 보존
    assert pv_dest_owner_applies(_spec(rule=rule, identity=_USER)) is False            # 비 root -- 종전(실행 신원)
    assert pv_dest_owner_applies(_spec(rule=rule, options={"chown": "5:5"})) is False   # 명시 chown 이 이긴다
    src_only = _spec(rule=rule, contain=False)
    src_only.paths["contain"] = {"source": {"root": "/s", "anchor": "/s"}}
    assert pv_dest_owner_applies(src_only) is False                                     # PV 소스 → 직접 경로 목적지


def test_only_the_pv_root_option_and_legacy_rows_measure():
    """측정 술어 = 적용 술어 ∧ 규칙이 "storage" 아님 -- 기본 규칙은 값이 요청에 기록돼 있어 재지 않는다."""
    assert pv_owner_measured(_spec(rule="storage")) is False
    assert pv_owner_measured(_spec(rule="pv_root")) is True
    assert pv_owner_measured(_spec(rule=None)) is True                                  # 규칙 이전 옛 행 -- 종전 동작
    assert pv_owner_measured(_spec(rule="bogus")) is True                               # 변조 규칙은 측정 갈래(재확인이 끊는다)
    for kw in ({"contain": False}, {"identity": _USER}, {"options": {"chown": "5:5"}}):
        assert pv_owner_measured(_spec(rule="pv_root", **kw)) is False, kw


@pytest.mark.parametrize("rule", ["storage", "pv_root", None])
def test_argv_carries_the_result_owner_for_preview_and_execution(rule):
    for tool in ("dsync", "nsync"):
        for dryrun in (True, False):
            argv = tool_argv(_spec(rule=rule, tool=tool, owner="1000:100", dryrun=dryrun), abs_paths=_ABS)
            assert argv.count("--chown") == 1 and argv[argv.index("--chown") + 1] == "1000:100", argv
    # 루트 소유(0:0)·nobody 여도 규칙 그대로(확인 창에 그 값이 보인다)
    assert "65534:65534" in tool_argv(_spec(rule=rule, owner="65534:65534"), abs_paths=_ABS)


def test_storage_default_root_still_emits_chown_root():
    """스토리지 기본 소유자 0:0(사용자 지정 기본 root:root)도 「개입 없음」(소스 소유 보존)이 아니라 --chown 0:0 이다 --
    dms-mpifileutils 포크는 --chown 이 있으면 일반 파일의 setuid/setgid 를 지운다(소스의 setuid-root 파일이 PV 에 그대로 떨어지지
    않게). 같은 root 실행이라도 직접 경로 목적지는 개입이 없다."""
    argv = tool_argv(_spec(rule="storage", owner="0:0"), abs_paths=_ABS)
    assert argv[argv.index("--chown"):argv.index("--chown") + 2] == ["--chown", "0:0"]
    assert "--chown" not in tool_argv(_spec(rule="storage", owner="0:0", contain=False), abs_paths=_ABS)


@pytest.mark.parametrize("rule", ["storage", "pv_root", None])
@pytest.mark.parametrize("owner", [None, "", "abc", "1000", "1000:", ":100", "-1:5", "1:2:3", "4294967295:1", 1000])
def test_argv_refuses_a_pv_sync_without_a_valid_owner(rule, owner):
    with pytest.raises(ValueError):
        tool_argv(_spec(rule=rule, owner=owner), abs_paths=_ABS)


def test_existing_branches_are_unchanged():
    for rule in ("storage", "pv_root", None):
        assert "--chown" not in tool_argv(_spec(rule=rule, contain=False), abs_paths=_ABS)      # root, 직접 경로
        assert tool_argv(_spec(rule=rule, contain=False, identity=_USER), abs_paths=_ABS)[:2] == ["--chown",
                                                                                                 "10001:10000"]
        argv = tool_argv(_spec(rule=rule, options={"chown": "7:7"}, owner="1000:100"), abs_paths=_ABS)  # 명시 chown 우선
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


def _tree_spec(tmp_path, *, tool="dsync", identity=_ROOT, options=None, root_rel="pvc-a/data", rule="pv_root"):
    m = tmp_path / "m"
    (m / "pvc-a/data/out").mkdir(parents=True)
    (m / "src").mkdir()
    root = str(m / root_rel)
    paths = {"source": str(m / "src"), "source_storage": "s", "destination": root + "/out/new",
             "destination_storage": "d", "contain": {"destination": {"root": root, "anchor": str(m)}}}
    return JobSpec(job_id="j1", phase="preflight", operation="sync", tool=tool, dryrun=False, identity=identity,
                   paths=paths, options=options or {}, candidates={"primary": ["n1"]}, process_count=1,
                   queue="dms-data", priority_class="dms-mid", artifact_base="file:///art",
                   result_owner_rule=rule), m


@pytest.mark.parametrize("rule", ["pv_root", None])
def test_destination_preflight_prints_the_pv_root_owner(tmp_path, rule):
    spec, m = _tree_spec(tmp_path, rule=rule)
    st = os.stat(m / "pvc-a/data")
    rc, out, _ = _run(spec)
    assert rc == 0, out
    assert parse_pv_owner([("p", out, None)]) == f"{st.st_uid}:{st.st_gid}"
    assert out.strip().endswith("DMS_PREFLIGHT_OK")
    # nsync 역할 파드: 목적지만 찍고 소스는 찍지 않는다
    spec, _ = _tree_spec(tmp_path / "n", tool="nsync", rule=rule)
    assert "DMS_PV_OWNER=" in _run(spec, role="destination")[1]
    assert "DMS_PV_OWNER=" not in _run(spec, role="source")[1]


def test_no_measurement_when_not_required(tmp_path):
    for i, kw in enumerate(({"options": {"chown": "5:5"}}, {"identity": _USER}, {"rule": "storage"})):
        spec, _ = _tree_spec(tmp_path / str(i), **kw)
        rc, out, script = _run(spec)
        assert "dms_pv_owner" not in script and "DMS_PV_OWNER=" not in out, kw
        assert rc == 0 and out.strip().endswith("DMS_PREFLIGHT_OK"), (kw, out)


@pytest.mark.parametrize("role", [None, "source", "destination"])
def test_storage_rule_preflight_is_the_containment_only_script(tmp_path, role):
    """기본 규칙의 preflight 는 측정 기능 이전과 바이트 동일(봉쇄만) -- 같은 root·PV 목적지에서 측정하지 않는 다른 갈래(명시
    chown)와 스크립트·인자가 같다. 봉쇄(dms_contain)는 그대로 붙는다."""
    storage, _ = _tree_spec(tmp_path, tool="nsync", rule="storage")
    explicit = dataclasses.replace(storage, result_owner_rule=None, options={"chown": "5:5"})
    assert _preflight_script(storage, role=role) == _preflight_script(explicit, role=role)
    script = _preflight_script(storage, role=role)[0]
    assert "dms_pv_owner" not in script and "DMS_PV_OWNER" not in script
    if role != "source":
        assert "dms_contain \"$" in script
    measured = dataclasses.replace(storage, result_owner_rule="pv_root")
    if role == "source":
        assert _preflight_script(measured, role=role) == _preflight_script(storage, role=role)
    else:
        assert "dms_pv_owner" in _preflight_script(measured, role=role)[0]


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


def _job(repos, *, pv=True, chown=None, key="k", rule="storage", owner="1500:1500"):
    """PV 목적지 root nsync 잡 하나(Planned). rule = destination_pv 블록의 owner_rule(None = 규칙 이전 옛 행 -- 키 없음),
    owner = 블록에 기록된 스토리지 기본 소유자(규칙 "storage" 일 때 제출이 싣는다; None 이면 키 없음)."""
    _seed(repos, "src")
    _seed(repos, "dst")
    payload = {"source_storage": "src", "source": "a", "destination_storage": "dst", "destination": "pv/out",
               "run_as_root": True, "options": {"chown": chown} if chown else {}}
    if pv:
        block = {"cluster": "c", "namespace": "n", "pvc": "p", "storage": "dst", "root": "pv"}
        if rule is not None:
            block["owner_rule"] = rule
        if rule == "storage" and owner is not None:
            block["owner"] = owner
        payload["destination_pv"] = block
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
    stepper.run_once()                                       # preflight 성공 → (측정 규칙이면 소유자 기록) → 미리보기 제출
    return stepper


def _specs(adapter, phase):
    return [s for s in adapter.submitted_specs() if s.phase == phase]


class _CountingAdapter(StubExecutionAdapter):
    def __init__(self):
        super().__init__()
        self.log_reads = 0

    def read_log(self, ref):
        self.log_reads += 1
        return super().read_log(ref)


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
    stepper.run_once()                                       # 성공 → (측정 규칙이면 재측정) → 실행 제출(또는 거부)
    return repos.data_jobs.get_job(jid)


# 측정 규칙: 운영자 옵션 "pv_root" 와 규칙 이전 옛 행(None -- 종전 동작을 그대로 유지한다)
MEASURED = ["pv_root", None]


# --- 기본 규칙 "storage": 재지 않고 기록된 스토리지 기본 소유자로 --------------------------------------------------------

@pytest.mark.parametrize("owner", ["1500:1500", "0:0"])
def test_storage_rule_chowns_to_the_recorded_owner_without_measuring(db, owner):
    """preflight 는 측정 마커 없이(봉쇄만) 돌고 로그를 읽지 않으며, 미리보기 spec 이 블록에 기록된 값을 싣는다 -- 0:0 이어도
    --chown 0:0(포크가 setuid/setgid 를 지운다)."""
    repos = Repositories(db)
    adapter = _CountingAdapter()
    rid, jid = _job(repos, owner=owner)
    _through_preflight(repos, adapter, jid, None)
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == DataJobState.PREVIEW_RUNNING.value
    assert adapter.log_reads == 0 and "dest_pv_owner" not in job["worker_pool"]
    pre = _specs(adapter, "preflight")[0]
    assert (pre.result_owner_rule, pre.result_owner) == ("storage", owner)
    for role in ("source", "destination"):
        assert "DMS_PV_OWNER" not in _preflight_script(pre, role=role)[0]
    spec = _specs(adapter, "preview")[0]
    assert (spec.result_owner, spec.result_owner_rule) == (owner, "storage")
    argv = tool_argv(spec, abs_paths=spec.paths)
    assert argv[argv.index("--chown"):argv.index("--chown") + 2] == ["--chown", owner] and "--dryrun" in argv
    assert not [e for e in repos.observability.events_for_request(rid) if e["event_type"] == "pv_owner_measured"]


def test_storage_rule_runs_exec_preflight_without_measuring(db):
    repos = Repositories(db)
    adapter = _CountingAdapter()
    _, jid = _job(repos)
    job = _to_exec_preflight(repos, adapter, jid, None, [("dst", "DMS_PREFLIGHT_OK", None)])
    assert job["state"] == DataJobState.EXECUTING.value and adapter.log_reads == 0
    spec = _specs(adapter, "execution")[0]
    assert (spec.result_owner, spec.result_owner_rule) == ("1500:1500", "storage")


def test_storage_rule_ignores_a_measured_value_on_the_job_row(db):
    """규칙이 "storage" 면 잡 행에 남은 dest_pv_owner(변조·다른 규칙의 잔재)를 쓰지 않는다 -- 요청 블록의 기록값만."""
    repos = Repositories(db)
    adapter = StubExecutionAdapter()
    _, jid = _job(repos)
    job = _to_exec_preflight(repos, adapter, jid, "7:7", [("dst", "DMS_PV_OWNER=7:7\nDMS_PREFLIGHT_OK", None)])
    assert job["state"] == DataJobState.EXECUTING.value
    assert _specs(adapter, "execution")[0].result_owner == "1500:1500"


@pytest.mark.parametrize("owner", [None, "root:root", "4294967295:0", "-1:0"])
def test_storage_rule_with_a_bad_recorded_owner_fails_closed_at_the_builder(db, owner):
    """기록값이 없거나 틀리면(planner·컨펌 뒤 변조) spec 에 None 으로 실려 빌더가 ValueError(어댑터 submit_failed) -- root
    개입 없음(소스 소유 보존)으로 접지 않는다."""
    repos = Repositories(db)
    adapter = StubExecutionAdapter()
    _, jid = _job(repos, owner=owner)
    _through_preflight(repos, adapter, jid, None)
    spec = _specs(adapter, "preview")[0]
    assert (spec.result_owner, spec.result_owner_rule) == (None, "storage")
    with pytest.raises(ValueError):
        tool_argv(spec, abs_paths=spec.paths)


def test_explicit_chown_wins_over_the_recorded_storage_owner(db):
    """명시 chown(특권 운영자)이 있어도 제출은 규칙 "storage" + owner 를 기록한다 -- 실행에서는 chown 이 이긴다."""
    repos = Repositories(db)
    adapter = _CountingAdapter()
    _, jid = _job(repos, chown="7:7")
    _through_preflight(repos, adapter, jid, None)
    assert adapter.log_reads == 0
    spec = _specs(adapter, "preview")[0]
    argv = tool_argv(spec, abs_paths=spec.paths)
    assert argv.count("--chown") == 1 and argv[argv.index("--chown") + 1] == "7:7"


# --- 측정 규칙(운영자 옵션 pv_root·옛 행): 잰 PV 루트 소유자로 ---------------------------------------------------------

@pytest.mark.parametrize("rule", MEASURED)
def test_preflight_owner_is_recorded_and_reaches_the_preview(db, rule):
    repos = Repositories(db)
    adapter = StubExecutionAdapter()
    rid, jid = _job(repos, rule=rule)
    _through_preflight(repos, adapter, jid, _owner_log("1000:100"))
    job = repos.data_jobs.get_job(jid)
    assert job["worker_pool"]["dest_pv_owner"] == "1000:100"
    assert job["state"] == DataJobState.PREVIEW_RUNNING.value
    spec = _specs(adapter, "preview")[0]
    assert (spec.result_owner, spec.result_owner_rule) == ("1000:100", rule)
    events = [e for e in repos.observability.events_for_request(rid) if e["event_type"] == "pv_owner_measured"]
    assert events and events[0]["payload"]["owner"] == "1000:100"
    assert "DMS_PV_OWNER" in _preflight_script(_specs(adapter, "preflight")[0], role="destination")[0]


@pytest.mark.parametrize("rule", MEASURED)
@pytest.mark.parametrize("log", [[("dst", "DMS_PREFLIGHT_OK", None)],
                                 [("dst", "DMS_PV_OWNER=1:1\nDMS_PV_OWNER=2:2", None)],
                                 [("src", "DMS_PREFLIGHT_OK", None), ("dst", "DMS_PV_OWNER=root:root", None)]])
def test_unknown_owner_rejects_instead_of_preserving_the_source_owner(db, log, rule):
    repos = Repositories(db)
    adapter = StubExecutionAdapter()
    _, jid = _job(repos, rule=rule)
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
    _, jid = _job(repos, rule="pv_root")
    stepper = _through_preflight(repos, adapter, jid, [("src", "DMS_PREFLIGHT_OK", None), ("dst", None, None)])
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == DataJobState.PREFLIGHT.value and job["worker_pool"]["dest_pv_owner_read_misses"] == 1
    adapter.set_log(f"stub-preflight-{jid}", _owner_log("1000:100"))   # 다음 틱에 읽힌다
    stepper.run_once()
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == DataJobState.PREVIEW_RUNNING.value and job["worker_pool"]["dest_pv_owner"] == "1000:100"
    # 끝내 못 읽으면 상한에서 거부
    _, jid2 = _job(repos, key="k2", rule="pv_root")
    stepper = _through_preflight(repos, adapter, jid2, [("dst", None, None)])
    for _ in range(PV_OWNER_READ_ATTEMPTS):
        stepper.run_once()
    job2 = repos.data_jobs.get_job(jid2)
    assert (job2["state"], job2["reason_code"]) == (DataJobState.REJECTED.value, "pv_owner_unknown")


def test_preview_argv_built_from_the_stepper_spec_carries_the_owner(db):
    """stepper 가 만든 spec 그대로 argv 를 만들면 --chown <잰 값> 이 실린다(스텁은 argv 를 안 만들어 따로 묶는다)."""
    repos = Repositories(db)
    adapter = StubExecutionAdapter()
    _, jid = _job(repos, rule="pv_root")
    _through_preflight(repos, adapter, jid, _owner_log("1000:100"))
    spec = _specs(adapter, "preview")[0]
    argv = tool_argv(spec, abs_paths=spec.paths)
    assert argv[argv.index("--chown") + 1] == "1000:100" and "--dryrun" in argv


@pytest.mark.parametrize("rule", MEASURED)
def test_exec_preflight_same_owner_runs_with_it(db, rule):
    repos = Repositories(db)
    adapter = StubExecutionAdapter()
    _, jid = _job(repos, rule=rule)
    job = _to_exec_preflight(repos, adapter, jid, "1000:100", _owner_log("1000:100"))
    assert job["state"] == DataJobState.EXECUTING.value
    assert _specs(adapter, "execution")[0].result_owner == "1000:100"


@pytest.mark.parametrize("rule", MEASURED)
def test_exec_preflight_changed_owner_is_rejected(db, rule):
    repos = Repositories(db)
    adapter = StubExecutionAdapter()
    rid, jid = _job(repos, rule=rule)
    job = _to_exec_preflight(repos, adapter, jid, "1000:100", _owner_log("2000:200"))
    assert (job["state"], job["reason_code"]) == (DataJobState.REJECTED.value, "pv_owner_changed")
    assert _specs(adapter, "execution") == []
    ev = [e for e in repos.observability.events_for_request(rid) if e["event_type"] == "pv_owner_changed"]
    assert ev and ev[0]["payload"]["before"] == "1000:100" and ev[0]["payload"]["after"] == "2000:200"


@pytest.mark.parametrize("rule", MEASURED)
def test_exec_preflight_adopts_when_nothing_was_stored(db, rule):
    """배포 전에 미리보기까지 간 잡(저장값 없음)은 끊지 않고 그때 잰 값을 쓴다."""
    repos = Repositories(db)
    adapter = StubExecutionAdapter()
    _, jid = _job(repos, rule=rule)
    job = _to_exec_preflight(repos, adapter, jid, None, _owner_log("1000:100"))
    assert job["state"] == DataJobState.EXECUTING.value
    assert job["worker_pool"]["dest_pv_owner"] == "1000:100"
    assert _specs(adapter, "execution")[0].result_owner == "1000:100"


@pytest.mark.parametrize("kw", [{"pv": False}, {"chown": "5:5", "rule": "pv_root"}, {"chown": "5:5", "rule": None}])
def test_jobs_outside_the_rule_never_read_the_preflight_log(db, kw):
    repos = Repositories(db)
    adapter = _CountingAdapter()
    _, jid = _job(repos, **kw)
    _through_preflight(repos, adapter, jid, None)
    assert adapter.log_reads == 0
    spec = _specs(adapter, "preview")[0]
    assert spec.result_owner is None


# --- 잡 행 변조 -------------------------------------------------------------------------------------------------

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
