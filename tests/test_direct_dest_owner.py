"""직접 경로 sync 목적지의 결과 소유(2026-10-26 사용자 결정 (4) + 권장 다듬기 -- k8s_access.direct_dest_owner·_recheck_dest_owner,
repositories.k8s.pv_roots·pv_root_covering, stepper._dest_owner, execution_manifests.direct_dest_owner_applies).

규칙: 목적지가 직접 경로(PV 주소 아님)인 root sync 는 목적지 스토리지의 종류로 결과 소유가 갈린다 --
  pv      늘 그 스토리지의 기본 소유자(default_uid:default_gid)로 --chown(측정 없음 -- PV 주소 목적지의 기본 규칙과 같은 값)
  both    목적지 경로가 **지금** 인벤토리의 그 스토리지 PV 루트와 같거나 그 아래일 때만 그 값으로, 아니면 종전(소스 소유 보존)
  그 밖   종전(소스 소유 보존)
제출(단건)·자식 생성(배치)이 판정을 payload["dest_owner"] 에 얼리고({"rule": "storage" | "none", ...}), planner 3a·단건 컨펌·배치
자식 컨펌이 다시 계산해 다르면 storage_owner_changed(지금 값이 무효면 invalid_storage_owner). 명시 chown 이 이기고 기록하지 않는다.
키가 없는 옛 행은 새 검사 없이 종전대로 돈다.
"""
import dataclasses

import pytest

from dms.batch_orchestrator import BatchOrchestrator
from dms.domain import DataJobState
from dms.execution import JobSpec
from dms.execution_manifests import (_preflight_script, direct_dest_owner_applies, pv_dest_owner_applies,
                                     pv_owner_measured, tool_argv)
from dms.repositories import Repositories
from dms.repositories import k8s as k8s_repo
# 같은 세계(dmsk8s 실데이터 스냅숏 + 스토리지 4종 + SC 매핑 + 정책 + 노드 + 계정)를 쓴다 -- 픽스처·헬퍼는 test_k8s_submit 의 것.
from test_k8s_submit import (FP, NFS_OWNER, _k8s_settings, _login, _plan, _pv, _state, _tamper_payload,  # noqa: F401
                             _to_confirm_pending, kclient, world)
from test_k8s_submit import _stepper_through_preview

GPFS = "k8s-pv-gpfs"      # both(기본 소유는 아래 픽스처가 2500:2600 으로)
NFS = "k8s-pv-nfs"        # pv(기본 소유 1500:1500)
CEPH = "cephfs-dms"       # filesystem
GPFS_OWNER = "2500:2600"


@pytest.fixture
def gpfs_roots(world, db):
    """both 스토리지(gpfs)의 지금 PV 루트 두 개(team-a/models·team-b/models -- GPFS 프리셋 템플릿). 기본 소유는 0 이 아닌 값으로."""
    db.execute("UPDATE storages SET default_uid = 2500, default_gid = 2600 WHERE storage_name = :n", {"n": GPFS})
    roots = world.k8s.pv_roots(GPFS)
    assert len(roots) == 2 and all(r.startswith("pvc-") for r in roots), roots
    return roots


def _raw_sync(dst_storage, dst, *, src_storage=CEPH, src="src", **extra):
    return {"operation": "sync", "source_storage": src_storage, "source": src, "destination_storage": dst_storage,
            "destination": dst, "run_as_root": True, **extra}


def _submit(kclient, body, status=202):
    _login(kclient, "ops")                    # 특권 목록 + 세션 관리자 = 직접 경로를 쓸 수 있는 면제자
    r = kclient.post("/api/user/requests", json=body)
    assert r.status_code == status, r.text
    return r.json()["request_id"] if status == 202 else r.json()["detail"]


def _dest_owner(world, rid):
    return world.requests.get(rid)["payload"].get("dest_owner", "<absent>")


def _storage(rule_owner, storage, pv_root=None):
    return {"rule": "storage", "owner": rule_owner, "storage": storage, "pv_root": pv_root}


def _none(storage):
    return {"rule": "none", "storage": storage}


# --- 저장소: PV 루트 목록·덮는 루트 -------------------------------------------------------------------------------

def test_pv_roots_lists_the_selectable_roots_mapped_to_the_storage(world):
    assert world.k8s.pv_roots(NFS) == ("team-a/datasets", "team-b/scratch", "team-c/archive")
    assert len(world.k8s.pv_roots(GPFS)) == 2
    assert world.k8s.pv_roots(CEPH) == ()                 # filesystem -- PV 를 담지 않는다
    assert world.k8s.pv_roots("no-such-storage") == ()


def test_pv_root_covering_is_a_path_boundary_match(world, gpfs_roots):
    root = gpfs_roots[0]
    assert world.k8s.pv_root_covering(GPFS, root) == root
    assert world.k8s.pv_root_covering(GPFS, root + "/a/b") == root
    assert world.k8s.pv_root_covering(GPFS, root + "x") is None            # 글자 경계 -- 이웃 이름은 아니다
    assert world.k8s.pv_root_covering(GPFS, root.split("/")[0]) is None    # 루트의 부모는 PV 밖
    for bad in (None, "", 5):
        assert world.k8s.pv_root_covering(GPFS, bad) is None
    assert world.k8s.pv_root_covering(NFS, root) is None                   # 다른 스토리지의 루트


def test_pv_root_covering_picks_the_deepest_root(world, monkeypatch):
    monkeypatch.setattr(world.k8s, "pv_roots", lambda name: ("a", "a/b", "c"))
    assert world.k8s.pv_root_covering("x", "a/b/c") == "a/b"
    assert world.k8s.pv_root_covering("x", "a/bb") == "a"


def test_pv_roots_memo_follows_its_inputs(world, db, monkeypatch):
    """메모는 입력 지문(SC 매핑 템플릿·스토리지 종류·클러스터 applied_run_seq)이 바뀌면 다시 계산하고, 지문이 같아도 수명이 지나면
    다시 계산한다(DB 를 직접 고친 행 -- 신뢰 경계)."""
    calls = []
    real = k8s_repo.resolve_volume
    monkeypatch.setattr(k8s_repo, "resolve_volume", lambda *a: calls.append(1) or real(*a))
    first = world.k8s.pv_roots(GPFS)
    n = len(calls)
    assert n >= 2 and world.k8s.pv_roots(GPFS) == first and len(calls) == n          # 같은 지문 -- 메모
    db.execute("UPDATE k8s_storage_class_maps SET path_template = '{namespace}/{pvc}' WHERE storage_class = 'tb-gpfs'")
    assert world.k8s.pv_roots(GPFS) == ("team-a/models", "team-b/models") and len(calls) > n   # 템플릿 변경
    n = len(calls)
    db.execute("UPDATE k8s_clusters SET applied_run_seq = applied_run_seq + 100")
    world.k8s.pv_roots(GPFS)
    assert len(calls) > n                                                            # 새 스냅숏 적용
    n = len(calls)
    db.execute("UPDATE storages SET kind = 'filesystem' WHERE storage_name = :n", {"n": GPFS})
    assert world.k8s.pv_roots(GPFS) == ()                                            # PV 를 담지 않게 됨
    db.execute("UPDATE storages SET kind = 'both' WHERE storage_name = :n", {"n": GPFS})
    db.execute("DELETE FROM k8s_pvcs WHERE namespace = 'team-a' AND name = 'models'")  # 지문 밖 변경(직접 수정)
    assert world.k8s.pv_roots(GPFS) == ("team-a/models", "team-b/models")            # 수명 안 -- 메모
    monkeypatch.setattr(k8s_repo, "PV_ROOTS_MEMO_SECONDS", 0.0)
    assert world.k8s.pv_roots(GPFS) == ("team-b/models",)                            # 수명이 지나면 반영


# --- 제출: 판정을 얼린다 -------------------------------------------------------------------------------------------

def test_pv_kind_storage_always_freezes_the_storage_default(kclient, world):
    rid = _submit(kclient, _raw_sync(NFS, "anywhere/out"))
    assert _dest_owner(world, rid) == _storage(NFS_OWNER, NFS)
    # PV 루트 아래든 밖이든 같다(스토리지 전체가 PV 용)
    rid = _submit(kclient, _raw_sync(NFS, "team-b/scratch/raw"))
    assert _dest_owner(world, rid) == _storage(NFS_OWNER, NFS)


def test_both_storage_under_a_pv_root_freezes_the_storage_default(kclient, world, gpfs_roots):
    for root in gpfs_roots:
        for dst, frozen_path in ((root, root), (root + "/out/x", root + "/out/x"), (root + "/./out//y", root + "/out/y")):
            rid = _submit(kclient, _raw_sync(GPFS, dst))
            req = world.requests.get(rid)
            assert req["payload"]["destination"] == frozen_path
            assert req["payload"]["dest_owner"] == _storage(GPFS_OWNER, GPFS, root), dst


def test_both_storage_outside_the_pv_roots_preserves_the_source_owner(kclient, world, gpfs_roots):
    root = gpfs_roots[0]
    for dst in ("plain/dir", root + "x", root.split("/")[0], "team-a/models"):
        rid = _submit(kclient, _raw_sync(GPFS, dst))
        assert _dest_owner(world, rid) == _none(GPFS), dst


def test_filesystem_storage_records_none(kclient, world):
    rid = _submit(kclient, _raw_sync(CEPH, "copy", src_storage=GPFS, src="plain/src"))
    assert _dest_owner(world, rid) == _none(CEPH)


def test_explicit_chown_wins_and_records_nothing(kclient, world):
    rid = _submit(kclient, _raw_sync(NFS, "x", options={"chown": "7:7"}))
    assert _dest_owner(world, rid) == "<absent>"


def test_pv_destination_and_other_operations_record_nothing(kclient, world):
    rid = _submit(kclient, {"operation": "sync", "source_storage": CEPH, "source": "src",
                            "destination_pv": _pv("team-c", "archive"), "destination": "in", "run_as_root": True})
    payload = world.requests.get(rid)["payload"]
    assert "dest_owner" not in payload and payload["destination_pv"]["owner_rule"] == "storage"
    rid = _submit(kclient, {"operation": "rm", "storage": NFS, "target": "x/y", "options": {"recursive": True}})
    assert _dest_owner(world, rid) == "<absent>"


def test_invalid_stored_owner_is_409_only_where_it_applies(kclient, world, db, gpfs_roots):
    """저장된 기본 소유자가 무효(변조)면 적용되는 목적지에서만 409 -- root·소스 소유로 뭉개지 않는다. 적용되지 않는 경로(both 의
    PV 밖)는 그 값을 쓰지 않으니 받는다."""
    db.execute("UPDATE storages SET default_uid = -1 WHERE storage_name IN (:a, :b)", {"a": NFS, "b": GPFS})
    assert _submit(kclient, _raw_sync(NFS, "x"), status=409) == "invalid_storage_owner"
    assert _submit(kclient, _raw_sync(GPFS, gpfs_roots[0] + "/x"), status=409) == "invalid_storage_owner"
    rid = _submit(kclient, _raw_sync(GPFS, "plain/x"))
    assert _dest_owner(world, rid) == _none(GPFS)


# --- 재확인: planner 3a ------------------------------------------------------------------------------------------

def _set(db, storage, sql):
    db.execute(f"UPDATE storages SET {sql} WHERE storage_name = :n", {"n": storage})


@pytest.mark.parametrize("dst_storage,dst_kind,change,reason", [
    # 기본 소유자 변경 -- 확인 창에 보인 소유자와 실행이 갈라지지 않게
    (NFS, "any", lambda db: _set(db, NFS, "default_gid = 2000"), "storage_owner_changed"),
    (NFS, "any", lambda db: _set(db, NFS, "default_uid = -5"), "invalid_storage_owner"),
    # 종류 변경: 적용 대상이 아니게 됨 / 적용 대상이 됨(none 으로 얼린 행)
    (NFS, "any", lambda db: _set(db, NFS, "kind = 'filesystem'"), "storage_owner_changed"),
    (CEPH, "any", lambda db: _set(db, CEPH, "kind = 'pv'"), "storage_owner_changed"),
    # both: 경로를 덮던 PV 루트가 옮겨 감(SC 템플릿 변경) / PV 밖이던 경로가 PV 루트 아래가 됨
    (GPFS, "under", lambda db: db.execute(
        "UPDATE k8s_storage_class_maps SET path_template = '{namespace}/{pvc}' WHERE storage_class = 'tb-gpfs'"),
     "storage_owner_changed"),
    (GPFS, "team-a/models/out", lambda db: db.execute(
        "UPDATE k8s_storage_class_maps SET path_template = '{namespace}/{pvc}' WHERE storage_class = 'tb-gpfs'"),
     "storage_owner_changed"),
    # 덮는 PV 루트가 바뀌었어도(경로의 위 디렉터리가 새 루트) 결과(같은 스토리지의 같은 기본 소유자)가 같으면 막지 않는다
    (GPFS, "under", lambda db: db.execute(
        "UPDATE k8s_storage_class_maps SET path_template = '{volume}' WHERE storage_class = 'tb-gpfs'"), None),
    # 변경 없음
    (NFS, "any", lambda db: None, None),
    (GPFS, "under", lambda db: None, None),
    (CEPH, "any", lambda db: _set(db, CEPH, "default_uid = 9"), None),          # 적용 밖 스토리지의 값은 무관
])
def test_planner_rechecks_the_direct_destination_owner(kclient, world, db, gpfs_roots, dst_storage, dst_kind, change,
                                                       reason):
    dst = gpfs_roots[0] + "/out" if dst_kind == "under" else "copy" if dst_kind == "any" else dst_kind
    src = (GPFS, "plain/src") if dst_storage == CEPH else (CEPH, "src")
    rid = _submit(kclient, _raw_sync(dst_storage, dst, src_storage=src[0], src=src[1]))
    change(db)
    result = _plan(world)[rid]
    if reason is None:
        assert result == "planned", _state(world, rid)
    else:
        assert _state(world, rid) == ("Rejected", reason)


@pytest.mark.parametrize("dst_storage,mutate,reason", [
    (NFS, lambda p: p["dest_owner"].update(rule="bogus"), "pv_owner_not_applicable"),
    (NFS, lambda p: p["dest_owner"].update(rule=["storage"]), "pv_owner_not_applicable"),
    (NFS, lambda p: p.update(dest_owner="storage"), "pv_owner_not_applicable"),
    (NFS, lambda p: p["dest_owner"].update(owner="0:0"), "storage_owner_changed"),
    (NFS, lambda p: p["dest_owner"].pop("owner"), "storage_owner_changed"),
    (NFS, lambda p: p.update(dest_owner={"rule": "none", "storage": NFS}), "storage_owner_changed"),
    (CEPH, lambda p: p.update(dest_owner={"rule": "storage", "owner": "0:0", "storage": CEPH}), "storage_owner_changed"),
    # 표시용 칸(storage·pv_root)은 대조하지 않는다
    (NFS, lambda p: p["dest_owner"].update(storage="x", pv_root="y"), None),
    # 키 부재 = 이 기능 전에 제출된 옛 행 -- 새 검사 없음(부재를 거부로 「강화」하지 않는다)
    (NFS, lambda p: p.pop("dest_owner"), None),
    # 명시 chown(특권 운영자)이 붙으면 결과는 chown 값이라 대조하지 않는다
    (NFS, lambda p: (p["dest_owner"].update(owner="0:0"), p["options"].update(chown="7:7")), None),
])
def test_planner_treats_the_dest_owner_block_as_untrusted(kclient, world, db, dst_storage, mutate, reason):
    src = (GPFS, "plain/src") if dst_storage == CEPH else (CEPH, "src")
    rid = _submit(kclient, _raw_sync(dst_storage, "copy", src_storage=src[0], src=src[1]))
    _tamper_payload(db, rid, mutate)
    result = _plan(world)[rid]
    if reason is None:
        assert result == "planned", _state(world, rid)
    else:
        assert _state(world, rid) == ("Rejected", reason)


def test_dest_owner_on_a_pv_destination_request_is_tamper(kclient, world, db):
    rid = _submit(kclient, {"operation": "sync", "source_storage": CEPH, "source": "src",
                            "destination_pv": _pv("team-c", "archive"), "destination": "in", "run_as_root": True})
    _tamper_payload(db, rid, lambda p: p.update(dest_owner=_storage(NFS_OWNER, NFS)))
    _plan(world)
    assert _state(world, rid) == ("Rejected", "pv_owner_not_applicable")


def test_legacy_row_without_dest_owner_runs_with_the_source_owner(kclient, world, db):
    """키가 없는 행은 기본 소유자가 바뀌어도 계획·컨펌을 통과하고, 실행은 종전(root -- 소스 소유 보존, --chown 없음)."""
    rid = _submit(kclient, _raw_sync(NFS, "copy"))
    _tamper_payload(db, rid, lambda p: p.pop("dest_owner"))
    _set(db, NFS, "default_gid = 2000")
    assert _plan(world)[rid] == "planned"
    adapter, job = _stepper_through_preview(world, rid)
    preview = [s for s in adapter.submitted_specs() if s.phase == "preview"][0]
    assert (preview.result_owner_direct, preview.result_owner_rule) == (False, None)
    assert "--chown" not in tool_argv(preview, abs_paths=preview.paths)


# --- 재확인: 단건 컨펌 --------------------------------------------------------------------------------------------

def test_confirm_rechecks_the_direct_destination_owner(kclient, world, db):
    rid = _submit(kclient, _raw_sync(NFS, "copy"))
    assert _plan(world)[rid] == "planned"
    jid = _to_confirm_pending(world, rid)
    _set(db, NFS, "default_uid = 1600")
    r = kclient.post(f"/api/user/jobs/{jid}:confirm", json={"fingerprint": FP})
    assert r.status_code == 409 and r.json()["detail"] == "storage_owner_changed"
    assert world.data_jobs.get_job(jid)["state"] == "Rejected"
    assert _state(world, rid) == ("Rejected", "storage_owner_changed")


def test_confirm_passes_when_nothing_changed(kclient, world, gpfs_roots):
    rid = _submit(kclient, _raw_sync(GPFS, gpfs_roots[1] + "/in"))
    assert _plan(world)[rid] == "planned"
    jid = _to_confirm_pending(world, rid)
    r = kclient.post(f"/api/user/jobs/{jid}:confirm", json={"fingerprint": FP})
    assert r.status_code == 200, r.text


# --- 실행: stepper spec·argv --------------------------------------------------------------------------------------

@pytest.mark.parametrize("storage,owner", [(NFS, NFS_OWNER), (GPFS, GPFS_OWNER), (GPFS, "0:0")])
def test_stepper_chowns_a_direct_destination_to_the_frozen_owner_without_measuring(kclient, world, db, gpfs_roots,
                                                                                   storage, owner):
    if owner == "0:0":
        _set(db, GPFS, "default_uid = 0, default_gid = 0")
    dst = "copy" if storage == NFS else gpfs_roots[0] + "/copy"
    rid = _submit(kclient, _raw_sync(storage, dst))
    assert _plan(world)[rid] == "planned"
    adapter, job = _stepper_through_preview(world, rid)
    assert job["state"] == DataJobState.PREVIEW_RUNNING.value, job          # 측정 마커를 기다리지 않는다
    pre = [s for s in adapter.submitted_specs() if s.phase == "preflight"][0]
    assert (pre.result_owner_direct, pre.result_owner_rule, pre.result_owner) == (True, "storage", owner)
    assert "destination" not in (pre.paths.get("contain") or {})
    assert pv_owner_measured(pre) is False
    for role in (None, "source", "destination"):
        assert "DMS_PV_OWNER" not in _preflight_script(pre, role=role)[0]
    preview = [s for s in adapter.submitted_specs() if s.phase == "preview"][0]
    argv = tool_argv(preview, abs_paths=preview.paths)
    # 0:0 이어도 --chown 0:0(포크가 setuid/setgid 를 지운다 -- 「개입 없음」과 다르다)
    assert argv.count("--chown") == 1 and argv[argv.index("--chown") + 1] == owner and "--dryrun" in argv


def test_pv_source_into_a_direct_destination_gets_the_rule_too(kclient, world):
    """PV 소스 → 직접 경로 목적지(면제자의 혼합): 소스 쪽 PV 블록은 이 규칙과 무관 -- 목적지 스토리지(nfs = pv)로 판정한다. contain 에는
    소스만 실려(봉쇄) PV 목적지 술어는 거짓이고 직접 경로 술어가 --chown 을 넣는다."""
    rid = _submit(kclient, {"operation": "sync", "source_pv": _pv("team-a", "models"), "source": "s",
                            "destination_storage": NFS, "destination": "plain/out", "run_as_root": True})
    assert _dest_owner(world, rid) == _storage(NFS_OWNER, NFS)
    assert _plan(world)[rid] == "planned"
    adapter, _ = _stepper_through_preview(world, rid)
    preview = [s for s in adapter.submitted_specs() if s.phase == "preview"][0]
    assert set(preview.paths["contain"]) == {"source"}
    assert pv_dest_owner_applies(preview) is False and direct_dest_owner_applies(preview) is True
    argv = tool_argv(preview, abs_paths=preview.paths)
    assert argv[argv.index("--chown") + 1] == NFS_OWNER


def test_execution_phase_carries_the_same_owner(kclient, world):
    """컨펌 뒤 exec_preflight·실행 제출도 같은 _build_spec 을 지난다 -- 실행 spec 의 argv 에 같은 --chown."""
    from dms.execution import StubExecutionAdapter
    from dms.stepper import JobStepper
    rid = _submit(kclient, _raw_sync(NFS, "copy"))
    assert _plan(world)[rid] == "planned"
    jid = _to_confirm_pending(world, rid)
    world.data_jobs.set_job_state(jid, DataJobState.EXECUTING, actor="test")
    adapter = StubExecutionAdapter()
    stepper = JobStepper(world, adapter, settings=_k8s_settings())
    stepper.run_once()                                       # exec_preflight 제출
    stepper.run_once()                                       # 성공 → 실행 제출
    specs = {s.phase: s for s in adapter.submitted_specs()}
    assert set(specs) >= {"exec_preflight", "execution"}, list(specs)
    for phase in ("exec_preflight", "execution"):
        s = specs[phase]
        assert (s.result_owner_direct, s.result_owner_rule, s.result_owner) == (True, "storage", NFS_OWNER)
    argv = tool_argv(specs["execution"], abs_paths=specs["execution"].paths)
    assert argv[argv.index("--chown") + 1] == NFS_OWNER and "--dryrun" not in argv


def test_stepper_leaves_none_rule_destinations_alone(kclient, world, gpfs_roots):
    rid = _submit(kclient, _raw_sync(GPFS, "plain/copy"))
    assert _plan(world)[rid] == "planned"
    adapter, _ = _stepper_through_preview(world, rid)
    preview = [s for s in adapter.submitted_specs() if s.phase == "preview"][0]
    assert (preview.result_owner_direct, preview.result_owner_rule) == (False, None)
    assert "--chown" not in tool_argv(preview, abs_paths=preview.paths)


@pytest.mark.parametrize("bad", ["root:root", "4294967295:0", None, 5])
def test_stepper_fails_closed_on_a_bad_frozen_owner(kclient, world, db, bad):
    """planner 를 지난 뒤 기록값이 변조되면 spec 에 None 으로 실려 빌더가 ValueError(어댑터 submit_failed) -- 소스 소유 보존으로
    접지 않는다."""
    rid = _submit(kclient, _raw_sync(NFS, "copy"))
    assert _plan(world)[rid] == "planned"
    _tamper_payload(db, rid, lambda p: p["dest_owner"].update(owner=bad))
    adapter, _ = _stepper_through_preview(world, rid)
    pre = [s for s in adapter.submitted_specs() if s.phase == "preflight"][0]
    assert (pre.result_owner_direct, pre.result_owner_rule, pre.result_owner) == (True, "storage", None)
    with pytest.raises(ValueError):
        tool_argv(pre, abs_paths={"source": "/s", "destination": "/d"})


# --- 술어(빌더) ---------------------------------------------------------------------------------------------------

_ROOT = {"uid": 0, "gid": 0, "username": "ops", "privileged": True}


def _spec(**kw):
    base = dict(job_id="j1", phase="preview", operation="sync", tool="dsync", dryrun=False, identity=_ROOT,
                paths={"source": "/s/a", "source_storage": "s", "destination": "/d/out", "destination_storage": "d"},
                options={}, candidates={"primary": ["n1"]}, process_count=1, queue="dms-data", priority_class="dms-mid",
                artifact_base="file:///art", result_owner="1500:1500", result_owner_rule="storage",
                result_owner_direct=True)
    base.update(kw)
    return JobSpec(**base)


def test_direct_predicate_and_argv():
    spec = _spec()
    assert direct_dest_owner_applies(spec) is True and pv_dest_owner_applies(spec) is False
    assert pv_owner_measured(spec) is False                                         # 측정은 없다
    for tool in ("dsync", "nsync"):                                                 # nsync 는 모든 rank 가 같은 argv
        for dryrun in (True, False):
            argv = tool_argv(_spec(tool=tool, dryrun=dryrun), abs_paths=spec.paths)
            assert argv[argv.index("--chown"):argv.index("--chown") + 2] == ["--chown", "1500:1500"]
    for kw in ({"result_owner_direct": False}, {"result_owner_rule": None}, {"result_owner_rule": "pv_root"},
               {"identity": {"uid": 10001, "gid": 10000, "privileged": False}}, {"options": {"chown": "7:7"}},
               {"operation": "rm", "tool": "drm", "paths": {"target": "/d/x"}}):
        assert direct_dest_owner_applies(_spec(**kw)) is False, kw
    # 명시 chown 이 이긴다(중복 주입 없음)
    argv = tool_argv(_spec(options={"chown": "7:7"}), abs_paths=spec.paths)
    assert argv.count("--chown") == 1 and argv[argv.index("--chown") + 1] == "7:7"
    # PV 목적지(contain 의 destination)면 PV 규칙이 먼저다 -- 직접 경로 술어는 거짓, 적용은 PV 술어로
    pv = dataclasses.replace(spec, paths={**spec.paths, "contain": {"destination": {"root": "/d", "anchor": "/d"}}})
    assert direct_dest_owner_applies(pv) is False and pv_dest_owner_applies(pv) is True
    # 직접 경로 플래그가 없으면 종전 그대로(root -- 개입 없음)
    assert "--chown" not in tool_argv(_spec(result_owner_direct=False), abs_paths=spec.paths)


@pytest.mark.parametrize("owner", [None, "", "abc", "1000", "-1:5", "4294967295:1"])
def test_direct_predicate_refuses_a_missing_or_bad_owner(owner):
    with pytest.raises(ValueError):
        tool_argv(_spec(result_owner=owner), abs_paths={"source": "/s", "destination": "/d"})


# --- 배치 자식 ----------------------------------------------------------------------------------------------------

def _batch(world, items, *, status="Previewing"):
    return world.batches.create(operation="sync", requester_id="ops", actor="ops", max_concurrency=10, options={},
                                items=items, note=None, status=status, auth_method="session")


def _orch(world):
    return BatchOrchestrator(world, settings=_k8s_settings())


def _child_payloads(world, bid):
    return [world.requests.get(it["request_id"])["payload"] if it["request_id"] else None
            for it in world.batches.list_items(bid)]


def test_batch_children_freeze_the_direct_destination_owner(world, gpfs_roots):
    root = gpfs_roots[0]
    bid = _batch(world, [
        {"source_storage": CEPH, "source": "a", "destination_storage": NFS, "destination": "b"},
        {"source_storage": CEPH, "source": "a2", "destination_storage": GPFS, "destination": root + "/c"},
        {"source_storage": CEPH, "source": "a3", "destination_storage": GPFS, "destination": "plain/d"},
        {"source_storage": NFS, "source": "a4", "destination_storage": CEPH, "destination": "e"},
    ])
    _orch(world).run_once()
    got = [p["dest_owner"] for p in _child_payloads(world, bid)]
    assert got == [_storage(NFS_OWNER, NFS), _storage(GPFS_OWNER, GPFS, root), _none(GPFS), _none(CEPH)]


def test_batch_with_chown_records_nothing(world):
    bid = world.batches.create(operation="sync", requester_id="ops", actor="ops", max_concurrency=10,
                               options={"chown": "7:7"}, note=None, status="Previewing", auth_method="session",
                               items=[{"source_storage": CEPH, "source": "a", "destination_storage": NFS,
                                       "destination": "b"}])
    _orch(world).run_once()
    assert "dest_owner" not in _child_payloads(world, bid)[0]


def test_batch_child_with_an_invalid_storage_owner_is_rejected_alone(world, db):
    _set(db, NFS, "default_uid = -1")
    bid = _batch(world, [
        {"source_storage": CEPH, "source": "a", "destination_storage": NFS, "destination": "b"},
        {"source_storage": NFS, "source": "a2", "destination_storage": CEPH, "destination": "c"},
    ])
    _orch(world).run_once()
    items = world.batches.list_items(bid)
    assert (items[0]["status"], items[0]["reason_code"], items[0]["request_id"]) == ("Rejected", "invalid_storage_owner",
                                                                                     None)
    assert items[1]["status"] == "Materialized"
    assert world.batches.get(bid)["failed_count"] == 1


def test_batch_child_planner_recheck(world, db):
    bid = _batch(world, [{"source_storage": CEPH, "source": "a", "destination_storage": NFS, "destination": "b"}])
    _orch(world).run_once()
    rid = world.batches.list_items(bid)[0]["request_id"]
    _set(db, NFS, "default_gid = 2000")
    _plan(world)
    assert _state(world, rid) == ("Rejected", "storage_owner_changed")


def _confirmed_child(world, bid):
    """자식 하나를 계획 → ConfirmPending → 배치 확인(도장)까지."""
    _orch(world).run_once()                                      # 자식 생성
    rid = world.batches.list_items(bid)[0]["request_id"]
    assert _plan(world)[rid] == "planned"
    jid = _to_confirm_pending(world, rid)
    _orch(world).run_once()                                      # 전원 미리보기 → PreviewReady(회차 1)
    assert world.batches.get(bid)["status"] == "PreviewReady"
    assert world.batches.confirm(bid, actor="ops", summary={}, expected_round=1) is True
    return rid, jid


def test_batch_child_confirm_rechecks_the_direct_destination_owner(world, db):
    """배치 자식은 단건 컨펌 라우트를 지나지 않는다(batch_child_confirm_via_batch) -- 배치 확인 뒤 orchestrator 가 실행으로 넘기는
    자리(_confirm_child)가 같은 재확인을 한다. 미리보기 뒤 기본 소유자가 바뀌면 그 자식은 실행되지 않고 종단, 다음 틱에 항목이
    Rejected 로 기록된다."""
    bid = _batch(world, [{"source_storage": CEPH, "source": "a", "destination_storage": NFS, "destination": "b"}])
    rid, jid = _confirmed_child(world, bid)
    _set(db, NFS, "default_gid = 2000")
    _orch(world).run_once()                                      # Running -- 도장 찍힌 자식을 실행으로 넘기려다 재확인
    job = world.data_jobs.get_job(jid)
    assert (job["state"], job["reason_code"]) == ("Rejected", "storage_owner_changed")
    assert _state(world, rid) == ("Rejected", "storage_owner_changed")
    _orch(world).run_once()
    item = world.batches.list_items(bid)[0]
    assert item["status"] == "Rejected"
    events = db.query("SELECT event_type FROM events WHERE request_id = :r", {"r": rid})
    assert "batch_child_recheck_rejected" in [e["event_type"] for e in events]


def test_batch_child_confirm_runs_when_nothing_changed(world, gpfs_roots):
    bid = _batch(world, [{"source_storage": CEPH, "source": "a", "destination_storage": GPFS,
                          "destination": gpfs_roots[0] + "/b"}])
    _, jid = _confirmed_child(world, bid)
    _orch(world).run_once()
    assert world.data_jobs.get_job(jid)["state"] == DataJobState.EXECUTING.value


def test_repositories_instance_is_isolated_per_database(db):
    """메모는 저장소 인스턴스(= 한 DB 연결)에 묶인다 -- 다른 Repositories 는 자기 메모를 쓴다(테스트의 앱·world 가 따로 계산)."""
    a, b = Repositories(db), Repositories(db)
    assert a.k8s._pv_roots_memo is not b.k8s._pv_roots_memo
