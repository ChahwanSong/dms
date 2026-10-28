"""root 실행은 명시적일 때만 + 목적지 자체 권한 검사(2026-09-30 프로덕션 사고).

사고: 특권 목록의 관리자 계정이 작업 신청 화면에서 "실행 신원 = 일반 사용자(903436)" 로
src → dst_fail(다른 사용자 903333 소유, 700) sync 를 냈다. 잡은 uid 0 으로 돌아(특권 요청자면
무조건 root 였다) preflight 를 통과했고, dsync 가 소스 최상위의 소유·권한을 기존 목적지에
적용해 dst_fail 의 소유자가 903436:104 로 바뀐 채 성공했다. 그 사용자 권한으로는 들어갈 수도
없는 디렉터리다. 테스트베드에서 같은 구조로 재현(identity uid 0, dst_fail bob→alice).

부수 결함(같은 조사): preflight 가 기존 목적지에서도 **부모** 쓰기만 봐서 그룹 쓰기 가능한 남의
목적지는 통과시켜, 데이터를 복사한 뒤 chmod()/utime() EPERM 으로 Failed(부분 복사)가 됐다(실측).
부모 쓰기 검사 자체는 목적지가 있어도 유지한다 -- dsync 가 부모 W_OK 를 요구하고 실패하면 복사
0건·종료 코드 0 이다(d139 실증에서 한때 건너뛰었다가 "아무것도 안 한 Succeeded" 로 잡음).

dms-k8s(k8s 신원 모드 전용, 2026-10-26 LDAP 코드 삭제): 모든 단건 잡이 root 이고 서버가 run_as_root 를 싣는다(생략 = root,
명시 false = 422). 사고의 교훈은 「root 근거(run_as_root·배치 자식)가 없는 신원을 root 로 올리지 않는다」로 남는다 -- 근거
없는 행은 낮출 비 root 신원이 없어 거부(privileged_not_authorized)이고, 얼린 root 신원은 stepper 가 근거를 다시 본다.
"""
import os
import subprocess

import pytest

from dms.config import Settings
from dms.domain import RequestState
from dms.execution import JobSpec, StubExecutionAdapter
from dms.execution_manifests import build_preflight_pod
from dms.identity import (PRIVILEGE_IF_ELIGIBLE, PRIVILEGE_NEVER, PRIVILEGE_REQUESTED,
                          IdentityRejected, privilege_policy, resolve_job_identity)
from dms.planner import Planner
from dms.repositories import Repositories
from dms.repositories.control import ControlRepository
from dms.stepper import JobStepper

OPS = frozenset({"ops"})


# --- 신원 정책 ----------------------------------------------------------------

def _resolve(db, *, privilege=None, session=True, requester="ops", owner="alice"):
    kw = {} if privilege is None else {"privilege": privilege}
    return resolve_job_identity(ControlRepository(db),
                                requester_id=requester, owner_username=owner,
                                allow_privileged=True, privileged_requesters=OPS,
                                session_authenticated=session, **kw)


def test_no_root_basis_is_refused_even_for_an_eligible_admin(db):
    # 사고의 핵심(root 는 명시일 때만) -- 근거가 없으면 자격이 있어도 root 로 올리지 않고, 낮출 비 root 신원도 없어 거부한다.
    for kw in ({}, {"privilege": PRIVILEGE_NEVER}):
        with pytest.raises(IdentityRejected) as e:
            _resolve(db, **kw)
        assert e.value.reason_code == "privileged_not_authorized"


def test_explicit_root_request_is_root_with_the_run_identity_name(db):
    out = _resolve(db, privilege=PRIVILEGE_REQUESTED)
    assert (out.uid, out.gid, out.privileged, out.username) == (0, 0, True, "alice")


def test_explicit_root_request_without_a_session_is_rejected(db):
    with pytest.raises(IdentityRejected) as e:
        _resolve(db, privilege=PRIVILEGE_REQUESTED, session=False)
    assert e.value.reason_code == "privileged_not_authorized"


def test_batch_children_are_root_only_when_eligible(db):
    assert _resolve(db, privilege=PRIVILEGE_IF_ELIGIBLE).privileged is True
    with pytest.raises(IdentityRejected) as e:
        _resolve(db, privilege=PRIVILEGE_IF_ELIGIBLE, session=False)
    assert e.value.reason_code == "privileged_not_authorized"


# --- API 게이트 ------------------------------------------------------------------

def _client_with(db, **overrides):
    from fastapi.testclient import TestClient
    from dms.api.app import create_app
    base = {"DMS_DATABASE_URL": "unused", "DMS_SHARED_TOKEN": "tok-shared",
            "DMS_ADMIN_TOKEN": "tok-admin", "DMS_SESSION_SECRET": "sess",
            "DMS_ACCOUNT_VERIFICATION_REQUIRED": "false",
            "DMS_PASSWORD_ENCRYPTION_REQUIRED": "false",
            "DMS_ALLOW_PRIVILEGED_REQUESTERS": "true", "DMS_PRIVILEGED_REQUESTERS": "ops",
            **overrides}
    client = TestClient(create_app(Settings.from_env(base), db))
    repos = client.app.state.repos
    if repos.storages.get("s1") is None:
        repos.storages.create(storage_name="s1", mount_path="/mnt/s1", managed_root="/mnt/s1/dms",
                              backend_type="cephfs", actor="test")
    return client


SYNC = {"operation": "sync", "source_storage": "s1", "source": "src",
        "destination_storage": "s1", "destination": "dst"}


def _login_admin(client, name="ops"):
    client.post("/api/admin/accounts", json={"username": name, "password": "p"},
                headers={"x-admin-token": "tok-admin"})
    client.post("/api/auth/login", json={"username": name, "password": "p"})


def _stored_payload(db):
    import json
    row = db.query_one("SELECT payload FROM requests ORDER BY created_at DESC LIMIT 1")
    return json.loads(row["payload"])


def test_run_as_root_over_the_shared_token_is_refused(db):
    # 특권 승격은 세션 인증만(슬라이스 19) -- 토큰(관리자 역할)으로는 어떤 제출도 root 가 될 수 없어 제출 자체가 403.
    # (x-dms-actor 로 "ops" 를 사칭하는 것은 actor 게이트가 이미 400 으로 막는다 --
    #  여기선 그 게이트를 통과한 토큰 요청(actor=shared-token, role admin)을 본다.)
    client = _client_with(db, DMS_PRIVILEGED_REQUESTERS="ops,shared-token")
    for extra in ({}, {"run_as_root": True}):
        r = client.post("/api/user/requests", json={**SYNC, **extra},
                        headers={"Authorization": "Bearer tok-shared"})
        assert r.status_code == 403 and r.json()["detail"] == "privileged_not_authorized", extra


@pytest.mark.parametrize("value", ["yes", "true", 1])
def test_run_as_root_must_be_a_real_boolean(db, value):
    client = _client_with(db)
    _login_admin(client)
    r = client.post("/api/user/requests", json={**SYNC, "run_as_root": value})
    assert r.status_code == 422


# --- 모든 단건 잡은 root(설계 D2) -- 서버가 run_as_root 를 싣는다 ------------------------------------

@pytest.mark.parametrize("extra", [
    {},                                                 # 생략 = root
    {"owner_username": "ops"},
    {"owner_username": "alice"},                        # 다른 실행 신원(이름)이어도 root -- 권한 판정은 k8s 맵핑
    {"run_as_root": True},
    {"owner_username": "alice", "run_as_root": True},
])
def test_every_single_request_is_stored_as_an_explicit_root_request(db, extra):
    client = _client_with(db)
    _login_admin(client)
    r = client.post("/api/user/requests", json={**SYNC, **extra})
    assert r.status_code == 202, r.text
    assert _stored_payload(db)["run_as_root"] is True


def test_explicit_non_root_is_refused_not_silently_promoted(db):
    # 명시 false 는 화면이 「비 root」라고 믿고 보낸 값 -- 조용히 root 로 바꾸지 않는다.
    client = _client_with(db)
    _login_admin(client)
    r = client.post("/api/user/requests", json={**SYNC, "run_as_root": False})
    assert r.status_code == 422 and r.json()["detail"] == "run_as_root_required"


@pytest.mark.parametrize("who, expected", [
    ("ops", True), ("ops2", False), ("user", False), ("token", False)])
def test_me_tells_the_portal_whether_root_is_available(db, who, expected):
    # can_run_as_root(= 맵핑 면제 unrestricted, 같은 함수 api.auth.can_run_as_root) -- 포탈이 직접 경로·전체 PV 를 여는 근거.
    client = _client_with(db, DMS_PRIVILEGED_REQUESTERS="ops,shared-token")
    headers = {}
    if who == "user":
        client.post("/api/auth/signup", json={"username": "mallory", "password": "p"})
        client.post("/api/auth/login", json={"username": "mallory", "password": "p"})
    elif who == "token":
        headers = {"Authorization": "Bearer tok-shared"}
    else:
        _login_admin(client, name=who)
    me = client.get("/api/auth/me", headers=headers).json()
    assert (me["can_run_as_root"], me["unrestricted"]) == (expected, expected)


# --- planner ---------------------------------------------------------------------

class _PrivSettings:
    agent_report_stale_seconds = 300
    allow_privileged_requesters = True
    privileged_requesters = OPS


NOW = "2026-08-02T10:00:00Z"


def _seed(repos):
    repos.storages.create(storage_name="s1", mount_path="/mnt/s1", managed_root="/mnt/s1/dms",
                          backend_type="cephfs", actor="admin")
    repos.storages.set_status("s1", "Ready", "ready_nodes=1")
    repos.control.upsert_policy("scan", max_nodes=3, procs_per_node=8, queue="dms-data",
                                default_priority="mid", max_priority="high",
                                preview_timeout_seconds=3600, execution_timeout_seconds=3600,
                                enabled=True, actor="admin")
    repos.agents.ingest("n1", {
        "node_name": "n1",
        "mounts": [{"storage_name": "s1", "mount_path": "/mnt/s1", "status": "Ready",
                    "writable": True}],
        "tools": [{"name": t, "status": "Ready"} for t in ("dscan", "dsync", "nsync", "drm")]},
        reported_at="2026-08-02T09:59:00Z")


def _plan(db, payload_extra=None, batch_id=None, key="k"):
    repos = Repositories(db)
    _seed(repos)
    payload = {"storage": "s1", "target": "a", "options": {}, "owner_username": "alice",
               **(payload_extra or {})}
    kw = {"batch_id": batch_id} if batch_id else {}
    # 요청자 ops 는 관리자 계정이다 -- 다른 실행 신원(alice) 지정은 planner 도 API 와 같은 술어(계정 역할 +
    # 목록)로 다시 본다(2026-10-07). 계정 행이 없으면 비관리자로 보아 privileged_not_authorized 다.
    repos.accounts.create("ops", "pw", "admin")
    rid = repos.requests.create(operation="scan", requester_id="ops", actor="ops",
                                resource_key=key, payload=payload, priority="mid",
                                auth_method="session", **kw)
    result = Planner(repos, settings=_PrivSettings()).run_once(now_iso=NOW)
    jobs = repos.data_jobs.list_jobs(request_id=rid)
    return result[rid], (jobs[0]["worker_pool"]["identity"] if jobs else None)


def test_planner_refuses_a_request_without_a_root_basis(db):
    # DB 직접 삽입·옛 행(run_as_root 없음) -- 자격 있는 관리자의 요청이어도 근거가 없으면 계획하지 않는다.
    assert _plan(db) == ("rejected:privileged_not_authorized", None)


def test_planner_honours_an_explicit_root_request(db):
    outcome, ident = _plan(db, {"run_as_root": True})
    assert outcome == "planned" and (ident["uid"], ident["privileged"], ident["username"]) == (0, True, "alice")


def test_planner_never_escalates_on_a_tampered_non_boolean_flag(db):
    # payload 는 DB 신뢰 경계 밖 -- `is True` 만 승격한다(그 밖은 근거 없음 = 거부).
    assert _plan(db, {"run_as_root": "true"}) == ("rejected:privileged_not_authorized", None)


def test_planner_keeps_batch_children_on_the_if_eligible_semantics(db):
    outcome, ident = _plan(db, batch_id="b-1")
    assert outcome == "planned" and (ident["uid"], ident["privileged"]) == (0, True)


# --- preflight: 목적지 자체 권한(실제 셸로) -----------------------------------------

_VOL = [{"name": "cephfs", "hostPath": {"path": "/cephfs"}, "mountPath": "/cephfs"}]
root_user = pytest.mark.skipif(os.geteuid() == 0, reason="root 는 권한 검사를 우회한다")


def _preflight(src, dst, *, privileged=False, role=None, fake_owner=None, bindir=None):
    """실제 셸로 preflight 스크립트를 돌린다. fake_owner: PATH 앞에 `stat` 대역을 둬서
    목적지 소유 uid 를 흉내 낸다 -- 비 root 테스트 러너는 "쓰기 가능한 부모 아래 남 소유
    디렉터리"를 만들 수 없어서다(스크립트는 `stat -c %u "$D"` 만 쓴다)."""
    env = None
    if fake_owner is not None:
        fake = bindir / "stat"
        fake.write_text(f"#!/bin/sh\necho {fake_owner}\n")
        fake.chmod(0o755)
        env = {**os.environ, "PATH": f"{bindir}:{os.environ.get('PATH', '/usr/bin:/bin')}"}
    spec = JobSpec(job_id="j1", phase="preflight", operation="sync", tool="dsync",
                   dryrun=False,
                   identity={"uid": 0 if privileged else os.getuid(),
                             "gid": 0 if privileged else os.getgid(),
                             "privileged": privileged},
                   paths={"source": src, "source_storage": "s1", "destination": dst,
                          "destination_storage": "s2", "source_root": "/", "destination_root": "/"},
                   options={}, candidates={"primary": ["n1"]}, process_count=8,
                   queue="dms-data", priority_class="dms-mid",
                   artifact_base="file:///cephfs/dms/artifacts")
    cmd = build_preflight_pod(spec, job_image="i", namespace="dms", volumes=_VOL,
                              node="n1", role=role)["spec"]["containers"][0]["command"]
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    return proc.returncode, proc.stdout


def _marker(out):
    for line in out.splitlines():
        if line.startswith("DMS_PREFLIGHT_REASON="):
            return line.split("=", 1)[1]
    return "OK" if "DMS_PREFLIGHT_OK" in out else None


@root_user
@pytest.mark.parametrize("role", [None, "destination"])
def test_own_destination_under_a_non_writable_parent_is_still_rejected(tmp_path, role):
    # dsync 는 목적지가 있어도 부모 W_OK 를 요구하고, 실패하면 복사 0건으로 **종료 코드 0**
    # 이다(포크 dsync.c "Destination parent directory is not writable"). 목적지가 있다고
    # 부모 검사를 건너뛰면 이 경우가 "Succeeded(아무것도 안 함)" 가 된다 -- d139 실증에서
    # alice 의 본인 dst(부모 dms_test = root 755) sync 가 정확히 그렇게 끝났다.
    src = tmp_path / "src"; src.mkdir()
    parent = tmp_path / "dms_test"; parent.mkdir()
    dst = parent / "dst"; dst.mkdir()
    parent.chmod(0o555)
    try:
        rc, out = _preflight(str(src), str(dst), role=role)
    finally:
        parent.chmod(0o755)
    assert (rc, _marker(out)) == (1, "destination_parent_not_writable")


@root_user
@pytest.mark.parametrize("role", [None, "destination"])
def test_existing_destination_without_write_permission_is_rejected(tmp_path, role):
    src = tmp_path / "src"; src.mkdir()
    dst = tmp_path / "dst_fail"; dst.mkdir()
    dst.chmod(0o500)
    try:
        rc, out = _preflight(str(src), str(dst), role=role)
    finally:
        dst.chmod(0o755)
    assert (rc, _marker(out)) == (1, "destination_not_writable")


@root_user
@pytest.mark.parametrize("role", [None, "destination"])
def test_writable_destination_owned_by_someone_else_is_rejected_for_a_user(tmp_path, role):
    # 사고 조사 (b): 그룹/모두 쓰기 가능한 남의 디렉터리(여기선 root 소유 1777 인 /tmp) --
    # 비특권 sync 는 데이터를 복사한 뒤 최상위 chmod/utime EPERM 으로 Failed 가 된다.
    # 목적지 자체 검사가 부모 검사보다 앞이라 /tmp 의 부모(/, 쓰기 불가)보다 소유가 먼저 걸린다.
    assert os.stat("/tmp").st_uid != os.getuid()
    src = tmp_path / "src"; src.mkdir()
    rc, out = _preflight(str(src), "/tmp", role=role)
    assert (rc, _marker(out)) == (1, "destination_not_owned")


def _foreign_grp(tmp_path):
    # 쓰기 가능한 부모 아래, 쓰기 가능한 목적지 -- 소유만 남(fake_owner)으로 흉내 낸다.
    src = tmp_path / "src"; src.mkdir()
    dst = tmp_path / "dst" / "grp"; dst.mkdir(parents=True)
    bindir = tmp_path / "bin"; bindir.mkdir()
    return src, dst, bindir


@pytest.mark.parametrize("role", [None, "destination"])
def test_group_writable_foreign_destination_is_rejected_for_a_user(tmp_path, role):
    # d139 실증 V3 의 단위 등가물(bob 소유 770 dst/grp, alice 는 그룹원).
    src, dst, bindir = _foreign_grp(tmp_path)
    rc, out = _preflight(str(src), str(dst), role=role, fake_owner=os.getuid() + 4242,
                         bindir=bindir)
    assert (rc, _marker(out)) == (1, "destination_not_owned")
    rc, out = _preflight(str(src), str(dst), role=role, fake_owner=os.getuid(), bindir=bindir)
    assert (rc, _marker(out)) == (0, "OK")


@pytest.mark.parametrize("role", [None, "destination"])
def test_root_mode_skips_the_owner_check(tmp_path, role):
    # 명시적 root 실행은 소유 검사를 하지 않는다(root 는 어느 디렉터리든 다룬다 -- 화면이
    # 그 결과를 경고한다). 여기선 실행 사용자가 root 가 아니어도 모드만 본다.
    src, dst, bindir = _foreign_grp(tmp_path)
    rc, out = _preflight(str(src), str(dst), privileged=True, role=role,
                         fake_owner=os.getuid() + 4242, bindir=bindir)
    assert (rc, _marker(out)) == (0, "OK")


def test_missing_destination_still_checks_the_parent(tmp_path):
    src = tmp_path / "src"; src.mkdir()
    rc, out = _preflight(str(src), str(tmp_path / "a" / "b"))
    assert (rc, _marker(out)) == (1, "destination_parent_not_writable")
    rc, out = _preflight(str(src), str(tmp_path / "new"))
    assert (rc, _marker(out)) == (0, "OK")


def test_mode_is_passed_positionally_never_inlined(tmp_path):
    spec_cmd = build_preflight_pod(
        JobSpec(job_id="j1", phase="preflight", operation="sync", tool="dsync", dryrun=False,
                identity={"uid": 10001, "gid": 10000, "privileged": False},
                paths={"source": "/cephfs/a", "source_storage": "s1",
                       "destination": "/cephfs/b", "destination_storage": "s1", "source_root": "/", "destination_root": "/"},
                options={}, candidates={"primary": ["n1"]}, process_count=8, queue="q",
                priority_class="p", artifact_base="file:///cephfs/dms/artifacts"),
        job_image="i", namespace="dms", volumes=_VOL, node="n1")["spec"]["containers"][0]["command"]
    assert spec_cmd[4:] == ["/cephfs/a", "/cephfs/b", "user", "/", "/"]   # + 봉쇄 기준(source_root·destination_root)
    assert "user" not in spec_cmd[2].split('M="$3"')[0]


# --- privilege_policy: planner·stepper 가 공유하는 단일 규칙 ------------------------

@pytest.mark.parametrize("req, expected", [
    (None, PRIVILEGE_NEVER),
    ("not-a-row", PRIVILEGE_NEVER),
    ({"payload": None}, PRIVILEGE_NEVER),
    ({"payload": ["run_as_root"]}, PRIVILEGE_NEVER),
    ({"payload": {"run_as_root": "true"}}, PRIVILEGE_NEVER),
    ({"payload": {"run_as_root": 1}}, PRIVILEGE_NEVER),
    ({"payload": {"run_as_root": False}}, PRIVILEGE_NEVER),
    ({"payload": {"run_as_root": True}}, PRIVILEGE_REQUESTED),
    ({"payload": {}, "batch_id": "b-1"}, PRIVILEGE_IF_ELIGIBLE),
    # 배치 자식 payload 의 run_as_root 는 변조다 -- 세션만 보는 REQUESTED 가 아니라 특권 목록 판정(IF_ELIGIBLE)으로
    ({"payload": {"run_as_root": True}, "batch_id": "b-1"}, PRIVILEGE_IF_ELIGIBLE),
    ({"payload": {}, "batch_id": ""}, PRIVILEGE_NEVER),
])
def test_privilege_policy_is_fail_closed(req, expected):
    assert privilege_policy(req) == expected


# --- stepper: 얼린 root 신원의 근거 재확인 -----------------------------------------
# 신원은 planner 가 한 번 정해 worker_pool 에 얼린다. 규칙 변경 전에 계획된 "관리자가 실행
# 신원=일반 사용자로 낸 sync"(ConfirmPending 으로 최대 preview TTL 대기)는 배포 뒤에도 uid 0
# 이라, 제출 직전 재확인이 없으면 배포 뒤에 사고가 그대로 재현된다(리뷰 발견).

class _StepSettings:
    agent_report_stale_seconds = 300
    preview_ttl_seconds = 86400
    artifact_base_uri = "file:///art"
    allow_privileged_requesters = True
    privileged_requesters = OPS
    vcjob_ttl_seconds = 86400


_ROOT = {"uid": 0, "gid": 0, "username": "alice", "groups": [], "privileged": True}
_USER = {"uid": 10001, "gid": 10000, "username": "alice", "groups": [], "privileged": False}


def _frozen_sync_job(db, *, identity, payload_extra=None, batch_id=None):
    repos = Repositories(db)
    for name in ("src", "dst"):
        if repos.storages.get(name) is None:
            repos.storages.create(storage_name=name, mount_path=f"/{name}",
                                  managed_root=f"/{name}/dms", backend_type="cephfs",
                                  actor="test")
    kw = {"batch_id": batch_id} if batch_id else {}
    rid = repos.requests.create(
        operation="sync", requester_id="ops", actor="ops", resource_key="k-frozen",
        payload={"source_storage": "src", "source": "a", "destination_storage": "dst",
                 "destination": "b", "owner_username": "alice", **(payload_extra or {})},
        priority="mid", auth_method="session", **kw)
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(
        rid, plan_id, operation="sync", priority="mid", source_storage="src", source="a",
        destination_storage="dst", destination="b", options={}, tool="dsync",
        worker_pool={"tool": "dsync", "identity": dict(identity),
                     "candidates": {"primary": ["n1"]}, "process_count": 8,
                     "queue": "dms-data", "priority_class": "dms-mid"},
        precondition={}, actor="planner")
    return repos, rid, jid


def test_frozen_root_identity_without_a_basis_is_stopped_before_any_submission(db):
    repos, rid, jid = _frozen_sync_job(db, identity=_ROOT)
    adapter = StubExecutionAdapter()
    result = JobStepper(repos, adapter, settings=_StepSettings()).run_once()
    assert result[jid] == "Rejected"
    assert repos.data_jobs.job_transitions(jid)[-1]["reason_code"] == "privilege_not_requested"
    assert repos.requests.last_reason_code(rid) == "privilege_not_requested"
    assert adapter.submitted_specs() == []          # root 로 한 번도 제출되지 않았다
    events = [e for e in repos.observability.events_for_request(rid)
              if e["event_type"] == "privilege_not_requested"]
    assert len(events) == 1


def test_confirmed_frozen_root_job_fails_at_exec_preflight_not_after(db):
    # 사고 시나리오 그대로: 규칙 변경 전 계획 → ConfirmPending → 배포 뒤 컨펌 → Executing.
    # 재확인이 없으면 exec_preflight 가 mode=root 로 돌고 --chown 도 빠진다.
    repos, rid, jid = _frozen_sync_job(db, identity=_ROOT)
    db.execute("UPDATE data_jobs SET state = 'Executing' WHERE job_id = :j", {"j": jid})
    adapter = StubExecutionAdapter()
    result = JobStepper(repos, adapter, settings=_StepSettings()).run_once()
    assert result[jid] == "Failed"
    assert repos.data_jobs.job_transitions(jid)[-1]["reason_code"] == "privilege_not_requested"
    assert adapter.submitted_specs() == []


@pytest.mark.parametrize("payload_extra, batch_id", [
    ({"run_as_root": True}, None),       # 명시 root
    (None, "b-1"),                        # 배치 자식(자격 있으면 root)
])
def test_root_identity_with_a_basis_is_submitted(db, payload_extra, batch_id):
    repos, _, jid = _frozen_sync_job(db, identity=_ROOT, payload_extra=payload_extra,
                                     batch_id=batch_id)
    adapter = StubExecutionAdapter()
    result = JobStepper(repos, adapter, settings=_StepSettings()).run_once()
    assert result[jid] == "Preflight"
    assert [s.identity["uid"] for s in adapter.submitted_specs()] == [0]


def test_non_root_identity_is_stopped_before_any_submission(db):
    # dms-k8s(k8s 신원 모드 전용, 2026-10-26): 비 root 신원은 정상 producer 가 없다(LDAP 시절의 옛 행·변조 행) -- 근거와
    # 무관하게 제출 전에 identity_missing_at_step 으로 끊는다(권한 판정이 k8s 맵핑뿐인 라인에서 비 root 경로를 열지 않는다).
    repos, rid, jid = _frozen_sync_job(db, identity=_USER, payload_extra={"run_as_root": True})
    adapter = StubExecutionAdapter()
    result = JobStepper(repos, adapter, settings=_StepSettings()).run_once()
    assert result[jid] == "Rejected"
    assert repos.requests.last_reason_code(rid) == "identity_missing_at_step"
    assert adapter.submitted_specs() == []
