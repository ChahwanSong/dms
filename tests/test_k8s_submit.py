"""k8s 신원 모드의 제출·권한(dms-k8s RBAC·PV 설계 §6·D1~D4·D12~D17, 2026-10-25 슬라이스 2).

dmsk8s 테스트베드 실데이터(tests/fixtures/k8s/testbed-*.json -- 인식 바인딩: team-a junwoo.song admin·alice edit, team-b
alice admin·cocoa.song edit, team-c 사용자 없음; PVC team-a models(gpfs)·datasets(nfs)·kraken-kv-cache(onefs)·checkpoints
(Pending), team-b models(gpfs)·scratch(nfs), team-c archive(nfs))를 실제 동기화 경로(claim → receive → apply)로 적용한 DB 위에서,
제출 → 계획 → (미리보기 대기) → 컨펌까지 끝에서 끝으로 고정한다. 해석된 경로가 테스트베드 워커 경로와 같은지도 본다.
"""
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dms import k8s_access
from dms.agent.k8s_fetch import project_pv, project_pvc, project_role_binding, project_storage_class
from dms.api.app import create_app
from dms.config import Settings, SettingsError
from dms.db import utc_now_iso
from dms.domain import DataJobState, DomainValidationError, _ACCOUNT_ID_RE, valid_owner_username
from dms.identity import (PRIVILEGE_IF_ELIGIBLE, PRIVILEGE_REQUESTED, IdentityRejected, resolve_job_identity,
                          root_allowed)
from dms.k8s_sync import normalize_snapshot
from dms.planner import Planner
from dms.repositories import Repositories
from dms.repositories.accounts import _USERNAME_RE as ACCOUNT_USERNAME_RE

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "k8s"
_FILES = {"storage_classes": ("testbed-storageclasses.json", project_storage_class),
          "pvcs": ("testbed-pvcs.json", project_pvc), "pvs": ("testbed-pvs.json", project_pv),
          "role_bindings": ("testbed-rolebindings.json", project_role_binding)}
CLUSTER = "dmsk8s"
# 테스트베드 스토리지 등록(설계 §8) -- (이름, 마운트 = managed_root, backend, kind)
STORAGES = (("k8s-pv-nfs", "/k8s_pv/nfs", "nfs", "pv"), ("k8s-pv-gpfs", "/k8s_pv/gpfs", "gpfs", "both"),
            ("k8s-pv-onefs", "/k8s_pv/onefs", "onefs", "pv"), ("cephfs-dms", "/cephfs/dms", "cephfs", "filesystem"))
SC_TO_STORAGE = {"tb-nfs": "k8s-pv-nfs", "tb-gpfs": "k8s-pv-gpfs", "tb-onefs": "k8s-pv-onefs"}
TOKEN = {"Authorization": "Bearer tok-shared"}
FP = "sha256:preview"


def _k8s_settings(**kw):
    base = dict(database_url="unused", shared_token="tok-shared", admin_token="tok-admin", session_secret="sess-secret",
                account_verification_required=False, identity_mode="k8s",
                user_allowed_operations=frozenset({"sync", "scan", "rm"}),
                privileged_requesters=frozenset({"root", "admin", "ops"}))
    base.update(kw)
    return Settings(**base)


def _snapshot():
    out = {}
    for key, (name, project) in _FILES.items():
        out[key] = [project(i) for i in json.loads((FIXTURES / name).read_text())["items"]]
    return normalize_snapshot(out)


def _sync_cluster(repos, db, name=CLUSTER, data=None):
    repos.k8s.create_cluster(name=name, api_server="https://10.10.10.20:6443", tls_server_name=None, ca_pem=None,
                             insecure_skip_tls_verify=False, token_enc="v1:x", enabled=True, actor="ops")
    db.execute("UPDATE k8s_clusters SET created_at = '2026-10-25T00:00:00Z' WHERE name = :n", {"n": name})
    got = repos.k8s.claim(node_name="k8s-w1", role="primary", now="2026-10-25T13:50:00Z", interval=300,
                          primary_configured=True, token_ok=lambda e: True, deadline_seconds=120)
    run_id = got["run"]["run_id"]
    repos.k8s.receive(run_id, node_name="k8s-w1", now="2026-10-25T13:50:05Z", ok=True,
                      data=data if data is not None else _snapshot(), fingerprint="fp")
    assert repos.k8s.apply_run(run_id)["outcome"] == "applied"


def _node_report(repos, node="k8s-w1"):
    repos.agents.ingest(node, {
        "node_name": node,
        "mounts": [{"storage_name": n, "mount_path": m, "status": "Ready", "writable": True}
                   for n, m, _, _ in STORAGES],
        "tools": [{"name": t, "status": "Ready"} for t in ("dscan", "dsync", "nsync", "drm")]},
        reported_at=utc_now_iso())


@pytest.fixture
def world(db):
    """dmsk8s 실데이터 동기화 + 스토리지 4종(종류 포함) + SC 매핑 + 정책 + 노드 + 계정."""
    repos = Repositories(db)
    repos.k8s.update_settings(primary_node="k8s-w1", secondary_node="k8s-w2", interval_seconds=None, actor="ops")
    _sync_cluster(repos, db)
    for name, root, backend, kind in STORAGES:
        repos.storages.create(storage_name=name, mount_path=root, managed_root=root, backend_type=backend,
                              kind=kind, actor="ops")
        repos.storages.set_status(name, "Ready", "ready_nodes=1")
    for sc, storage in SC_TO_STORAGE.items():
        repos.k8s.set_sc_map(sc, storage_name=storage, path_template=None, actor="ops")
    for tool in ("scan", "rm", "dsync", "nsync"):
        repos.control.upsert_policy(tool, max_nodes=2, procs_per_node=4, queue="dms-data", default_priority="mid",
                                    max_priority="high", preview_timeout_seconds=3600,
                                    execution_timeout_seconds=3600, enabled=True, actor="ops")
    _node_report(repos)
    for name, role in (("alice", "user"), ("junwoo.song", "user"), ("cocoa.song", "user"),
                       ("ops.admin", "admin"), ("ops", "admin")):
        repos.accounts.create(name, "pw", role)
    return repos


@pytest.fixture
def kclient(db, world):
    return TestClient(create_app(_k8s_settings(), db))


def _login(client, name):
    client.cookies.clear()
    r = client.post("/api/auth/login", json={"username": name, "password": "pw"})
    assert r.status_code == 200, r.text
    return client


def _pv(ns, pvc, cluster=CLUSTER):
    return {"cluster": cluster, "namespace": ns, "pvc": pvc}


def _pv_sync(src=("team-a", "datasets"), src_path="sample", dst=("team-b", "scratch"), dst_path="copy", **extra):
    return {"operation": "sync", "source_pv": _pv(*src), "source": src_path, "destination_pv": _pv(*dst),
            "destination": dst_path, **extra}


def _plan(repos, settings=None):
    return Planner(repos, None, settings=settings or _k8s_settings()).run_once()


def _to_confirm_pending(repos, rid):
    jobs = repos.data_jobs.list_jobs(request_id=rid)
    assert len(jobs) == 1, jobs
    jid = jobs[0]["job_id"]
    repos.data_jobs.set_preview(jid, fingerprint=FP, expires_at="2099-01-01T00:00:00Z", artifact_uri="file:///art/j")
    repos.data_jobs.set_job_state(jid, DataJobState.CONFIRM_PENDING, actor="stepper")
    return jid


def _state(repos, rid):
    return repos.requests.get(rid)["state"], repos.requests.last_reason_code(rid)


# --- 설정(D1) ---------------------------------------------------------------------------------------

_ENV = {"DMS_DATABASE_URL": "postgresql://u:p@h/d", "DMS_SHARED_TOKEN": "s", "DMS_ADMIN_TOKEN": "a",
        "DMS_SESSION_SECRET": "x"}


def test_identity_mode_defaults_to_ldap_and_accepts_k8s():
    assert Settings.from_env(_ENV).identity_mode == "ldap"
    assert Settings.from_env({**_ENV, "DMS_IDENTITY_MODE": "k8s"}).identity_mode == "k8s"


@pytest.mark.parametrize("env,needle", [
    ({"DMS_IDENTITY_MODE": "K8S"}, "DMS_IDENTITY_MODE must be one of"),
    ({"DMS_IDENTITY_MODE": "kubernetes"}, "DMS_IDENTITY_MODE must be one of"),
    ({"DMS_IDENTITY_MODE": "k8s", "DMS_LDAP_URI": "ldap://10.10.10.30:389"}, "remove DMS_LDAP_URI"),
    ({"DMS_IDENTITY_MODE": "k8s", "DMS_ALLOW_PRIVILEGED_REQUESTERS": "false"}, "must not be false"),
])
def test_identity_mode_misconfigurations_refuse_to_start(env, needle):
    with pytest.raises(SettingsError) as e:
        Settings.from_env({**_ENV, **env})
    assert any(needle in p for p in e.value.problems), e.value.problems


def test_ldap_mode_keeps_its_ldap_uri():
    assert Settings.from_env({**_ENV, "DMS_LDAP_URI": "ldap://h"}).ldap_uri == "ldap://h"


# --- 단일 술어(D3) ------------------------------------------------------------------------------------

def test_root_allowed_is_session_in_k8s_mode_and_privilege_eligible_in_ldap_mode():
    kw = dict(requester_id="alice", allow_privileged=True, privileged_requesters=frozenset({"ops"}))
    assert root_allowed(identity_mode="k8s", session_authenticated=True, **kw) is True
    assert root_allowed(identity_mode="k8s", session_authenticated=False, **kw) is False      # 공유 토큰
    assert root_allowed(identity_mode="k8s", session_authenticated=True,
                        **{**kw, "allow_privileged": False}) is False                        # 특권 허용 스위치
    assert root_allowed(identity_mode="ldap", session_authenticated=True, **kw) is False     # 목록 밖
    assert root_allowed(identity_mode="ldap", session_authenticated=True,
                        **{**kw, "requester_id": "ops"}) is True


class _Control:
    def __init__(self, groups=(), names=()):
        self._groups, self._names = set(groups), set(names)

    def has_group_denies(self):
        return bool(self._groups)

    def is_denied(self, *, requester, owner, groups):
        for n in (requester, owner):
            if n in self._names:
                return n
        return next((g for g in groups if g in self._groups), None)

    def register_probe_target(self, owner):
        raise AssertionError("root 경로는 프로브 대상을 등록하지 않는다")


def test_resolve_k8s_requested_is_root_for_any_session_user_and_ignores_group_rules():
    ident = resolve_job_identity(_Control(groups={"dmsusers"}), None, requester_id="alice", owner_username=None,
                                 allow_privileged=True, privileged_requesters=frozenset({"ops"}),
                                 session_authenticated=True, privilege=PRIVILEGE_REQUESTED, identity_mode="k8s")
    assert (ident.username, ident.uid, ident.gid, ident.privileged) == ("alice", 0, 0, True)
    # 같은 그룹 규칙이 ldap 모드에선 리졸버 없이 ldap_not_configured -- k8s 모드에서 모든 잡을 막던 결합(리뷰)
    with pytest.raises(IdentityRejected) as e:
        resolve_job_identity(_Control(groups={"dmsusers"}), None, requester_id="ops", owner_username=None,
                             allow_privileged=True, privileged_requesters=frozenset({"ops"}),
                             session_authenticated=True, privilege=PRIVILEGE_REQUESTED)
    assert e.value.reason_code == "ldap_not_configured"


def test_resolve_k8s_still_refuses_tokens_and_honours_name_rules():
    common = dict(owner_username=None, allow_privileged=True, privileged_requesters=frozenset({"ops"}),
                  privilege=PRIVILEGE_REQUESTED, identity_mode="k8s")
    with pytest.raises(IdentityRejected) as e:
        resolve_job_identity(_Control(), None, requester_id="shared-token", session_authenticated=False, **common)
    assert e.value.reason_code == "privileged_not_authorized"
    with pytest.raises(IdentityRejected) as e:
        resolve_job_identity(_Control(names={"alice"}), None, requester_id="alice", session_authenticated=True,
                             **common)
    assert e.value.reason_code == "identity_denied"


def test_resolve_k8s_batch_children_still_need_the_privileged_list():
    # IF_ELIGIBLE(배치 자식)은 세션만으로 root 가 되지 않는다 -- 목록에서 빠진 생성자의 자식은 비 root 경로 → 리졸버 없음.
    with pytest.raises(IdentityRejected) as e:
        resolve_job_identity(_Control(), None, requester_id="gone", owner_username=None, allow_privileged=True,
                             privileged_requesters=frozenset({"ops"}), session_authenticated=True,
                             privilege=PRIVILEGE_IF_ELIGIBLE, identity_mode="k8s")
    assert e.value.reason_code == "ldap_not_configured"
    ident = resolve_job_identity(_Control(), None, requester_id="ops", owner_username=None, allow_privileged=True,
                                 privileged_requesters=frozenset({"ops"}), session_authenticated=True,
                                 privilege=PRIVILEGE_IF_ELIGIBLE, identity_mode="k8s")
    assert ident.privileged is True


# --- 수용: 일반 사용자 PV→PV sync 제출 → 계획 → 미리보기 대기 → 컨펌 ---------------------------------------

def test_user_pv_to_pv_sync_runs_end_to_end_without_any_sync_pair(kclient, world):
    repos = world
    assert repos.sync_pairs.list() == []                       # 허용 쌍 0개(기본 전부 불가) -- D14 면제 세 지점
    _login(kclient, "alice")
    r = kclient.post("/api/user/requests", json=_pv_sync())     # run_as_root 생략 = root(k8s 모드 D2)
    assert r.status_code == 202, r.text
    rid = r.json()["request_id"]
    req = repos.requests.get(rid)
    p = req["payload"]
    assert p["run_as_root"] is True
    assert (p["source_storage"], p["source"]) == ("k8s-pv-nfs", "team-a/datasets/sample")
    assert (p["destination_storage"], p["destination"]) == ("k8s-pv-nfs", "team-b/scratch/copy")
    src_vol = p["source_pv"]["volume"]
    assert p["source_pv"] == {"cluster": CLUSTER, "namespace": "team-a", "pvc": "datasets", "volume": src_vol,
                              "storage_class": "tb-nfs", "root": "team-a/datasets", "path": "sample"}
    assert src_vol.startswith("pvc-")
    assert p["destination_pv"]["root"] == "team-b/scratch" and p["destination_pv"]["path"] == "copy"
    assert req["target_label"] == "datasets — team-a (tb-nfs) / sample → scratch — team-b (tb-nfs) / copy"
    assert req["auth_method"] == "session"
    # 계획: 맵핑 재확인(3a) 통과, 쌍 게이트(3b) 면제, root 신원(세션)
    assert _plan(repos)[rid] == "planned"
    job = repos.data_jobs.list_jobs(request_id=rid)[0]
    ident = job["worker_pool"]["identity"]
    assert (ident["username"], ident["uid"], ident["gid"], ident["privileged"]) == ("alice", 0, 0, True)
    jid = _to_confirm_pending(repos, rid)
    r = kclient.post(f"/api/user/jobs/{jid}:confirm", json={"fingerprint": FP})
    assert r.status_code == 200, r.text
    assert repos.data_jobs.get_job(jid)["state"] == "Executing"


def test_pv_addresses_resolve_to_the_testbed_worker_paths(kclient, world):
    # 테스트베드 실제 경로 맵(워커 /k8s_pv/…) -- 세 벤더 프리셋이 제출 payload 의 (스토리지, 경로)로 그대로 흐른다.
    _login(kclient, "junwoo.song")
    expect = {}
    for pvc, (storage, root_fn) in {
            "models": ("k8s-pv-gpfs", lambda v: f"{v}/{v}-data"),
            "datasets": ("k8s-pv-nfs", lambda v: "team-a/datasets"),
            "kraken-kv-cache": ("k8s-pv-onefs", lambda v: f"data/csi/{v}")}.items():
        r = kclient.post("/api/user/requests", json={"operation": "scan", "pv": _pv("team-a", pvc), "target": ""})
        assert r.status_code == 202, (pvc, r.text)
        p = world.requests.get(r.json()["request_id"])["payload"]
        expect[pvc] = (p["storage"], p["target"], root_fn(p["pv"]["volume"]))
        assert p["storage"] == storage and p["target"] == root_fn(p["pv"]["volume"]), pvc
    assert expect["kraken-kv-cache"][1] == "data/csi/k8s-3fa9c2d71e"


# --- 제출 게이트 -------------------------------------------------------------------------------------

def test_raw_paths_are_refused_for_users_and_unlisted_admins(kclient, world):
    for who in ("alice", "ops.admin"):
        _login(kclient, who)
        for storage in ("cephfs-dms", "k8s-pv-nfs"):
            r = kclient.post("/api/user/requests", json={"operation": "scan", "storage": storage, "target": "x"})
            assert r.status_code == 403 and r.json()["detail"] == "raw_path_not_allowed", (who, storage)
        # 한쪽만 직접 경로인 sync 도
        body = _pv_sync()
        body.pop("destination_pv")
        body["destination_storage"] = "k8s-pv-nfs"
        r = kclient.post("/api/user/requests", json=body)
        assert r.status_code == 403 and r.json()["detail"] == "raw_path_not_allowed", who


def test_neither_storage_nor_pv_still_gets_the_precise_validation_error(kclient, world):
    _login(kclient, "alice")
    r = kclient.post("/api/user/requests", json={"operation": "scan", "target": "x"})
    assert r.status_code == 422 and r.json()["detail"] == "missing_storage"


def test_shared_token_submissions_are_refused_in_k8s_mode(kclient, world):
    for body in ({"operation": "scan", "storage": "cephfs-dms", "target": "x", "run_as_root": True},
                 {"operation": "scan", "pv": _pv("team-a", "datasets"), "target": "x"}):
        r = kclient.post("/api/user/requests", json=body, headers=TOKEN)
        assert r.status_code == 403 and r.json()["detail"] == "privileged_not_authorized", body


def test_explicit_non_root_is_refused_and_omitted_or_true_is_root(kclient, world):
    _login(kclient, "alice")
    r = kclient.post("/api/user/requests", json=_pv_sync(run_as_root=False))
    assert r.status_code == 422 and r.json()["detail"] == "run_as_root_required"
    r = kclient.post("/api/user/requests", json=_pv_sync(run_as_root=True))
    assert r.status_code == 202
    assert world.requests.get(r.json()["request_id"])["payload"]["run_as_root"] is True


def test_no_mapping_is_403_and_no_snapshot_is_409(kclient, world, db):
    _login(kclient, "cocoa.song")                     # cocoa.song 은 team-b 만
    r = kclient.post("/api/user/requests", json={"operation": "scan", "pv": _pv("team-a", "datasets"), "target": ""})
    assert r.status_code == 403 and r.json()["detail"] == "k8s_access_denied"
    _login(kclient, "alice")                          # team-c 에는 사용자 바인딩이 없다
    r = kclient.post("/api/user/requests", json={"operation": "scan", "pv": _pv("team-c", "archive"), "target": ""})
    assert r.status_code == 403 and r.json()["detail"] == "k8s_access_denied"
    # 등록 안 된 클러스터·등록만 되고 한 번도 동기화되지 않은 클러스터 = 모름(409) -- 「없음」(pv_not_found)이 아니다
    world.k8s.create_cluster(name="fresh", api_server="https://10.0.0.9:6443", tls_server_name=None, ca_pem=None,
                             insecure_skip_tls_verify=False, token_enc="v1:x", enabled=True, actor="ops")
    for cluster in ("nope", "fresh"):
        r = kclient.post("/api/user/requests",
                         json={"operation": "scan", "pv": _pv("team-a", "datasets", cluster=cluster), "target": ""})
        assert r.status_code == 409 and r.json()["detail"] == "k8s_mapping_unknown", cluster


@pytest.mark.parametrize("target", ["", ".", "./", "sample/..", None])
def test_rm_of_the_pv_root_is_refused_however_it_is_spelled(kclient, world, target):
    _login(kclient, "alice")
    body = {"operation": "rm", "pv": _pv("team-a", "datasets"), "options": {"recursive": True}}
    if target is not None:
        body["target"] = target
    r = kclient.post("/api/user/requests", json=body)
    expected = "unsafe_path" if target == "sample/.." else "pv_root_rm_forbidden"
    assert r.status_code == 422 and r.json()["detail"] == expected, target


def test_rm_inside_a_pv_is_accepted(kclient, world):
    _login(kclient, "alice")
    r = kclient.post("/api/user/requests", json={"operation": "rm", "pv": _pv("team-a", "datasets"),
                                                 "target": "./sample/nested/", "options": {"recursive": True}})
    assert r.status_code == 202, r.text
    p = world.requests.get(r.json()["request_id"])["payload"]
    assert (p["target"], p["pv"]["path"]) == ("team-a/datasets/sample/nested", "sample/nested")


@pytest.mark.parametrize("body,detail", [
    ({"operation": "scan", "pv": _pv("team-a", "datasets"), "storage": "k8s-pv-nfs", "target": ""},
     "pv_and_storage_conflict"),
    ({"operation": "scan", "pv": _pv("team-a", "ghost"), "target": ""}, "pv_not_found"),
    ({"operation": "scan", "pv": _pv("team-a", "checkpoints"), "target": ""}, "pv_not_bound"),
    ({"operation": "scan", "pv": _pv("team-a", "datasets"), "target": "/etc"}, "unsafe_path"),
])
def test_pv_resolution_errors_are_422(kclient, world, body, detail):
    _login(kclient, "alice")
    r = kclient.post("/api/user/requests", json=body)
    assert r.status_code == 422 and r.json()["detail"] == detail


def test_unmapped_or_unresolvable_storage_class_is_422(kclient, world):
    _login(kclient, "junwoo.song")
    world.k8s.delete_sc_map("tb-onefs", actor="ops")
    r = kclient.post("/api/user/requests", json={"operation": "scan", "pv": _pv("team-a", "kraken-kv-cache"),
                                                 "target": ""})
    assert r.status_code == 422 and r.json()["detail"] == "pv_storageclass_unmapped"
    world.k8s.set_sc_map("tb-gpfs", storage_name="k8s-pv-gpfs", path_template="{attr:nope}", actor="ops")
    r = kclient.post("/api/user/requests", json={"operation": "scan", "pv": _pv("team-a", "models"), "target": ""})
    assert r.status_code == 422 and r.json()["detail"] == "pv_path_unresolved"


def test_admin_only_storage_applies_to_the_resolved_storage(kclient, world, db):
    db.execute("UPDATE storages SET user_enabled = 0 WHERE storage_name = 'k8s-pv-nfs'")
    _login(kclient, "alice")
    r = kclient.post("/api/user/requests", json={"operation": "scan", "pv": _pv("team-a", "datasets"), "target": ""})
    assert r.status_code == 403 and r.json()["detail"] == "storage_admin_only"


def test_operation_allowlist_still_applies(db, world):
    client = TestClient(create_app(_k8s_settings(user_allowed_operations=frozenset({"sync"})), db))
    _login(client, "alice")
    r = client.post("/api/user/requests", json={"operation": "scan", "pv": _pv("team-a", "datasets"), "target": ""})
    assert r.status_code == 403 and r.json()["detail"] == "operation_admin_only"


def test_unrestricted_admin_mixes_raw_and_pv_and_any_namespace(kclient, world):
    _login(kclient, "ops")                            # 특권 목록 + 세션 관리자 = 면제
    body = {"operation": "sync", "source_storage": "cephfs-dms", "source": "src",
            "destination_pv": _pv("team-c", "archive"), "destination": "in", "run_as_root": True}
    r = kclient.post("/api/user/requests", json=body)
    assert r.status_code == 202, r.text
    rid = r.json()["request_id"]
    req = world.requests.get(rid)
    assert "source_pv" not in req["payload"] and req["payload"]["destination_pv"]["namespace"] == "team-c"
    assert req["target_label"] == "cephfs-dms:src → archive — team-c (tb-nfs) / in"
    assert _plan(world)[rid] == "planned"
    # 직접 경로만인 요청은 target_label 이 없다(검색은 resource_key 표기로)
    r = kclient.post("/api/user/requests", json={"operation": "scan", "storage": "cephfs-dms", "target": "x"})
    assert r.status_code == 202 and world.requests.get(r.json()["request_id"])["target_label"] is None


def test_k8s_owner_username_follows_the_account_id_rule(kclient, world):
    assert _ACCOUNT_ID_RE.pattern == ACCOUNT_USERNAME_RE.pattern
    assert valid_owner_username("1user", identity_mode="k8s") and not valid_owner_username("1user")
    assert not valid_owner_username("Alice", identity_mode="k8s") and valid_owner_username("Alice")
    _login(kclient, "ops")
    r = kclient.post("/api/user/requests", json={"operation": "scan", "pv": _pv("team-a", "datasets"), "target": "",
                                                 "owner_username": "1user"})
    assert r.status_code == 202, r.text
    rid = r.json()["request_id"]
    assert _plan(world)[rid] == "planned"             # planner 3c 도 같은 규칙
    r = kclient.post("/api/user/requests", json={"operation": "scan", "pv": _pv("team-a", "datasets"), "target": "",
                                                 "owner_username": "Alice"})
    assert r.status_code == 422 and r.json()["detail"] == "invalid_owner_username"


# --- 그룹 denylist(D3) ------------------------------------------------------------------------------

def test_group_denylist_rules_are_refused_and_existing_ones_are_harmless(kclient, world):
    _login(kclient, "ops")
    r = kclient.put("/api/admin/identity-denylist/group/dmsusers", json={})
    assert r.status_code == 422 and r.json()["detail"] == "denylist_group_unsupported"
    assert kclient.put("/api/admin/identity-denylist/requester/mallory", json={}).status_code == 201
    world.control.deny("group", "dmsusers", "모드 전환 전 규칙", "ops")        # 모드 전환 전에 남은 그룹 규칙
    _login(kclient, "alice")
    rid = kclient.post("/api/user/requests", json=_pv_sync()).json()["request_id"]
    assert _plan(world)[rid] == "planned"            # 리졸버 없이도 계획된다(그룹 규칙 미평가)
    world.control.deny("requester", "alice", "이름 규칙은 그대로", "ops")
    rid = kclient.post("/api/user/requests", json=_pv_sync(src_path="other")).json()["request_id"]
    _plan(world)
    assert _state(world, rid) == ("Rejected", "identity_denied")


def test_group_rules_are_still_accepted_in_ldap_mode(client):
    r = client.put("/api/admin/identity-denylist/group/dmsusers", json={}, headers=TOKEN)
    assert r.status_code == 201


# --- 계획·컨펌 재확인(D13) ------------------------------------------------------------------------------

@pytest.fixture
def submitted(kclient, world):
    """alice 의 PV→PV sync 요청 하나(Pending)."""
    _login(kclient, "alice")
    r = kclient.post("/api/user/requests", json=_pv_sync())
    assert r.status_code == 202, r.text
    return r.json()["request_id"]


def _revoke_alice_on_team_b(db):
    db.execute("DELETE FROM k8s_role_bindings WHERE namespace = 'team-b' AND dms_user = 'alice'")


def _move_scratch(db):
    # PVC 를 지우고 다시 만든 모양 -- 같은 이름, 새 VOLUME. tb-nfs 의 {subdir} 는 namespace/pvc 라 같은 경로로 렌더되므로,
    # 경로가 VOLUME 에 묶인 SC 템플릿 덮어쓰기로 바꾼다(GPFS·OneFS 프리셋과 같은 성질).
    db.execute("UPDATE k8s_storage_class_maps SET path_template = '{volume}' WHERE storage_class = 'tb-nfs'")


@pytest.mark.parametrize("tamper,reason", [
    (_revoke_alice_on_team_b, "k8s_access_denied"),
    (_move_scratch, "pv_moved"),
    (lambda db: db.execute("DELETE FROM k8s_pvcs WHERE namespace = 'team-b' AND name = 'scratch'"), "pv_not_found"),
    (lambda db: db.execute("DELETE FROM k8s_clusters"), "pv_not_found"),
])
def test_planner_rechecks_mapping_and_location(world, db, submitted, tamper, reason):
    tamper(db)
    _plan(world)
    assert _state(world, submitted) == ("Rejected", reason)


def _tamper_payload(db, rid, mutate):
    row = db.query_one("SELECT payload FROM requests WHERE request_id = :r", {"r": rid})
    p = json.loads(row["payload"])
    mutate(p)
    db.execute("UPDATE requests SET payload = :p WHERE request_id = :r", {"p": json.dumps(p), "r": rid})


@pytest.mark.parametrize("mutate,reason", [
    # 자기 PV 블록 + 남의 target(DB 직접 변조) -- 경로가 PV 루트 밖
    (lambda p: p.update(destination="team-a/models"), "pv_target_outside"),
    (lambda p: p.update(destination="team-b/scratch/../../team-c/archive"), "pv_target_outside"),
    # pv 블록을 떼어 낸 비면제 행(raw)
    (lambda p: p.pop("destination_pv"), "raw_path_not_allowed"),
    # 블록 모양 변조
    (lambda p: p.update(destination_pv={"cluster": CLUSTER}), "pv_not_found"),
    # 블록의 root 만 바꾼 행
    (lambda p: p["destination_pv"].update(root="team-c/archive"), "pv_moved"),
])
def test_planner_treats_the_request_row_as_untrusted(world, db, submitted, mutate, reason):
    _tamper_payload(db, submitted, mutate)
    _plan(world)
    assert _state(world, submitted) == ("Rejected", reason)


def test_planner_refuses_a_block_less_row_inserted_for_a_user(world):
    rid = world.requests.create(operation="scan", requester_id="alice", actor="alice", resource_key="k-raw",
                                payload={"storage": "cephfs-dms", "target": "x", "options": {}, "run_as_root": True},
                                priority="mid", auth_method="session")
    _plan(world)
    assert _state(world, rid) == ("Rejected", "raw_path_not_allowed")


def test_planner_rm_root_recheck(world, db, kclient):
    _login(kclient, "alice")
    rid = kclient.post("/api/user/requests", json={"operation": "rm", "pv": _pv("team-a", "datasets"),
                                                   "target": "sample", "options": {"recursive": True}}
                       ).json()["request_id"]
    _tamper_payload(db, rid, lambda p: p.update(target="team-a/datasets"))
    _plan(world)
    assert _state(world, rid) == ("Rejected", "pv_root_rm_forbidden")


@pytest.mark.parametrize("tamper,reason", [
    (_revoke_alice_on_team_b, "k8s_access_denied"),
    (_move_scratch, "pv_moved"),
])
def test_confirm_rechecks_and_terminates(kclient, world, db, submitted, tamper, reason):
    assert _plan(world)[submitted] == "planned"
    jid = _to_confirm_pending(world, submitted)
    tamper(db)
    r = kclient.post(f"/api/user/jobs/{jid}:confirm", json={"fingerprint": FP})
    assert r.status_code == 409 and r.json()["detail"] == reason
    assert world.data_jobs.get_job(jid)["state"] == "Rejected"
    assert _state(world, submitted) == ("Rejected", reason)


@pytest.mark.parametrize("mutate,reason", [
    (lambda p: p.update(destination="team-a/models"), "pv_target_outside"),
    (lambda p: p.pop("destination_pv"), "raw_path_not_allowed"),
])
def test_confirm_treats_the_request_row_as_untrusted(kclient, world, db, submitted, mutate, reason):
    # 계획 뒤(ConfirmPending 동안) 요청 행이 변조돼도 컨펌이 planner 와 같은 함수로 다시 본다.
    assert _plan(world)[submitted] == "planned"
    jid = _to_confirm_pending(world, submitted)
    _tamper_payload(db, submitted, mutate)
    r = kclient.post(f"/api/user/jobs/{jid}:confirm", json={"fingerprint": FP})
    assert r.status_code == 409 and r.json()["detail"] == reason
    assert world.data_jobs.get_job(jid)["state"] == "Rejected"


def test_confirm_recheck_applies_even_when_an_admin_confirms(kclient, world, db, submitted):
    assert _plan(world)[submitted] == "planned"
    jid = _to_confirm_pending(world, submitted)
    _revoke_alice_on_team_b(db)
    _login(kclient, "ops")
    r = kclient.post(f"/api/user/jobs/{jid}:confirm", json={"fingerprint": FP})
    assert r.status_code == 409 and r.json()["detail"] == "k8s_access_denied"


def test_confirm_of_a_planned_root_job_needs_a_session_request(kclient, world, db, submitted):
    # 컨펌의 REQUESTED 재검사는 root_allowed(k8s = 세션) -- 특권 목록 밖 사용자도 통과하고(수용 테스트), 요청 행의
    # auth_method 가 토큰이면 같은 자리에서 종단한다.
    assert _plan(world)[submitted] == "planned"
    jid = _to_confirm_pending(world, submitted)
    db.execute("UPDATE requests SET auth_method = 'token' WHERE request_id = :r", {"r": submitted})
    r = kclient.post(f"/api/user/jobs/{jid}:confirm", json={"fingerprint": FP})
    assert r.status_code == 409 and r.json()["detail"] == "privileged_not_authorized"


# --- sync 허용 쌍 면제(D14) 세 지점 --------------------------------------------------------------------

def test_pair_gate_exempt_predicate():
    k8s, ldap = _k8s_settings(), _k8s_settings(identity_mode="ldap")
    both = {"source_pv": {"cluster": "c"}, "destination_pv": {"cluster": "c"}}
    assert k8s_access.pair_gate_exempt(both, k8s) is True
    assert k8s_access.pair_gate_exempt(both, ldap) is False
    assert k8s_access.pair_gate_exempt({"source_pv": {"cluster": "c"}}, k8s) is False
    assert k8s_access.pair_gate_exempt(None, k8s) is False


def test_pv_sync_in_ldap_mode_still_needs_a_pair_at_submit_plan_and_confirm(db, world):
    ldap = _k8s_settings(identity_mode="ldap")
    client = TestClient(create_app(ldap, db))
    _login(client, "alice")
    r = client.post("/api/user/requests", json=_pv_sync())
    assert r.status_code == 403 and r.json()["detail"] == "sync_pair_not_allowed"
    world.sync_pairs.add("k8s-pv-nfs", "k8s-pv-nfs", actor="ops")
    r = client.post("/api/user/requests", json=_pv_sync())
    assert r.status_code == 202
    world.sync_pairs.remove("k8s-pv-nfs", "k8s-pv-nfs", actor="ops")
    _plan(world, ldap)
    assert _state(world, r.json()["request_id"]) == ("Rejected", "sync_pair_not_allowed")


def test_k8s_confirm_is_not_blocked_by_a_missing_pair(kclient, world, submitted):
    # 수용 테스트와 같은 경로를 쌍이 「있다가 빠진」 모양으로 -- 면제는 쌍 유무와 무관하다.
    world.sync_pairs.add("k8s-pv-nfs", "k8s-pv-nfs", actor="ops")
    assert _plan(world)[submitted] == "planned"
    jid = _to_confirm_pending(world, submitted)
    world.sync_pairs.remove("k8s-pv-nfs", "k8s-pv-nfs", actor="ops")
    assert kclient.post(f"/api/user/jobs/{jid}:confirm", json={"fingerprint": FP}).status_code == 200


# --- me(D4·D15) ----------------------------------------------------------------------------------

def test_me_tells_the_portal_the_mode_the_exemption_and_the_operations(kclient, world):
    assert _login(kclient, "alice").get("/api/auth/me").json() == {
        "actor": "alice", "role": "user", "can_run_as_root": False, "identity_mode": "k8s", "unrestricted": False,
        "user_operations": ["scan", "sync", "rm"]}
    me = _login(kclient, "ops").get("/api/auth/me").json()
    assert (me["unrestricted"], me["can_run_as_root"], me["user_operations"]) == (True, True, ["scan", "sync", "rm"])
    me = _login(kclient, "ops.admin").get("/api/auth/me").json()
    assert (me["unrestricted"], me["role"]) == (False, "admin")


def test_me_in_ldap_mode_lists_the_default_user_operations(client):
    client.post("/api/auth/signup", json={"username": "bob", "password": "p"})
    client.post("/api/auth/login", json={"username": "bob", "password": "p"})
    me = client.get("/api/auth/me").json()
    assert (me["identity_mode"], me["unrestricted"], me["user_operations"]) == ("ldap", False, ["sync"])


# --- 경로 검색(D17) -------------------------------------------------------------------------------

def test_path_search_matches_the_pv_label(kclient, world):
    _login(kclient, "alice")
    rid = kclient.post("/api/user/requests", json=_pv_sync()).json()["request_id"]
    for q in ("datasets", "TEAM-B", "tb-nfs", "scratch copy", "team-a/datasets/sample"):
        rows = kclient.get("/api/user/requests", params={"path": q}).json()
        assert [r["request_id"] for r in rows] == [rid], q
    assert kclient.get("/api/user/requests", params={"path": "models"}).json() == []


# --- 스캔 경로(§6) ---------------------------------------------------------------------------------

def test_scan_paths_are_for_unrestricted_users_in_k8s_mode(kclient, world):
    _login(kclient, "alice")
    r = kclient.post("/api/user/scan-paths", json={"storage_name": "k8s-pv-nfs", "path": "team-c/archive"})
    assert r.status_code == 422 and r.json()["detail"] == "scan_path_unrestricted_only"
    assert kclient.get("/api/user/scan-paths").status_code == 200          # 목록·삭제는 둔다
    _login(kclient, "ops")
    r = kclient.post("/api/user/scan-paths", json={"storage_name": "k8s-pv-nfs", "path": "team-c/archive"})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    world.scan_paths.add("alice", "k8s-pv-nfs", "team-c/archive")
    alice_pid = next(p["id"] for p in world.scan_paths.list_for("alice"))
    _login(kclient, "alice")
    r = kclient.get(f"/api/user/scan-paths/{alice_pid}/stats")
    assert r.status_code == 422 and r.json()["detail"] == "scan_path_unrestricted_only"
    assert pid != alice_pid


# --- 단위: 표기·하위 경로 --------------------------------------------------------------------------

def test_side_label_and_subpath_normalization():
    block = {"pvc": "models", "namespace": "team-a", "storage_class": "tb-gpfs", "path": ""}
    assert k8s_access.side_label(block) == "models — team-a (tb-gpfs)"
    assert k8s_access.side_label({**block, "path": "ckpt/x"}) == "models — team-a (tb-gpfs) / ckpt/x"
    for raw, norm in (("", ""), (".", ""), ("./", ""), ("a//b/", "a/b"), ("./a/./b", "a/b")):
        assert k8s_access.normalize_subpath(raw) == norm, raw
    for bad in ("/abs", "a/../b", "..", "a\x00b"):
        with pytest.raises(DomainValidationError) as e:
            k8s_access.normalize_subpath(bad)
        assert e.value.reason_code == "unsafe_path"


def test_recheck_is_a_noop_for_raw_requests_in_ldap_mode(world):
    req = {"operation": "scan", "requester_id": "alice", "payload": {"storage": "cephfs-dms", "target": "x"}}
    assert k8s_access.recheck_request(world, req, settings=_k8s_settings(identity_mode="ldap")) is None
    with pytest.raises(DomainValidationError) as e:
        k8s_access.recheck_request(world, req, settings=_k8s_settings())
    assert e.value.reason_code == "raw_path_not_allowed"


def test_unrestricted_request_matches_can_run_as_root(world):
    s = _k8s_settings()
    row = {"requester_id": "ops", "auth_method": "session", "payload": {}}
    assert k8s_access.unrestricted_request(row, settings=s, repos=world) is True
    assert k8s_access.unrestricted_request({**row, "auth_method": "token"}, settings=s, repos=world) is False
    assert k8s_access.unrestricted_request({**row, "requester_id": "ops.admin"}, settings=s, repos=world) is False
    assert k8s_access.unrestricted_request({**row, "requester_id": "alice"}, settings=s, repos=world) is False
    assert k8s_access.unrestricted_request({**row, "requester_id": "alice", "batch_id": "b"},
                                           settings=s, repos=world) is True
    assert k8s_access.unrestricted_request({**row, "requester_id": "ghost"}, settings=s, repos=world) is False


# --- root nsync 의 PV 목적지(2026-10-26 — 2차 보안 리뷰로 막았다가 dms-mpifileutils 2a10a18 로 풀었다) ----------------------

def _split_mounts(repos):
    """nfs·cephfs 는 k8s-w1 에만, gpfs 는 k8s-w2 에만 -- 두 스토리지를 함께 마운트한 노드가 없어 nsync 가 골라진다."""
    for node, names in (("k8s-w1", ("k8s-pv-nfs", "cephfs-dms")), ("k8s-w2", ("k8s-pv-gpfs",))):
        repos.agents.ingest(node, {
            "node_name": node,
            "mounts": [{"storage_name": n, "mount_path": m, "status": "Ready", "writable": True}
                       for n, m, _, _ in STORAGES if n in names],
            "tools": [{"name": t, "status": "Ready"} for t in ("dscan", "dsync", "nsync", "drm")]},
            reported_at=utc_now_iso())


def test_root_nsync_into_a_pv_destination_is_planned(kclient, world):
    """두 스토리지를 함께 마운트한 노드가 없으면 PV 목적지 sync 도 nsync 로 계획된다(2026-10-26). 한때 `pv_sync_needs_shared_node`
    로 막았던 경로다 -- nsync 목적지 쪽이 상위 디렉터리·파일을 링크를 따라 열어 목적지 PV 안의 `sub → 남의 PV` 링크 너머에 root
    로 썼다. dms-mpifileutils 2a10a18 이 목적지 루트 아래를 성분별 O_NOFOLLOW 로 걷게 고쳐 가드를 풀었다(아래 REF 고정 테스트)."""
    repos = world
    _split_mounts(repos)
    _login(kclient, "alice")
    r = kclient.post("/api/user/requests", json=_pv_sync(src=("team-a", "datasets"), dst=("team-a", "models")))
    assert r.status_code == 202, r.text
    rid = r.json()["request_id"]
    assert _plan(repos)[rid] == "planned"
    assert repos.data_jobs.list_jobs(request_id=rid)[0]["tool"] == "nsync"
    # 목적지가 직접 경로(면제 관리자)여도 nsync 그대로 -- PV 소스는 읽기만 한다
    _login(kclient, "ops")
    r = kclient.post("/api/user/requests", json={
        "operation": "sync", "source_pv": _pv("team-a", "datasets"), "source": "",
        "destination_storage": "k8s-pv-gpfs", "destination": "admin-copy", "run_as_root": True})
    assert r.status_code == 202, r.text
    rid2 = r.json()["request_id"]
    assert _plan(repos)[rid2] == "planned"
    assert repos.data_jobs.list_jobs(request_id=rid2)[0]["tool"] == "nsync"


# 목적지 링크를 따라가던 nsync 의 고정값들 -- 위 계획이 안전한 전제는 잡 이미지의 nsync 가 고쳐진 커밋 이후라는 것이다.
_NSYNC_FOLLOWS_DST_LINKS = {"faefdaf05b8da9386671c2cee00db75efc0e9cd7", "1b93d5404d078c55b46194cdd02732417ed856dc"}
# 링크는 따라가지 않지만 없는 목적지 루트를 못 만들어 새 목적지로의 sync 가 전부 실패하던 고정값(d1 라이브 실증 -- 2a10a18 이 고침).
_NSYNC_BREAKS_NEW_DESTINATIONS = {"8cd72f30a7bfb075795b2128f4a3dbb74c8b31f8"}


def test_job_image_pins_an_nsync_that_does_not_follow_destination_links():
    """root nsync 의 PV 목적지를 허용하는 것(planner 에 가드 없음)과 잡 이미지의 mpifileutils 고정값은 짝이다 -- 고정값을 옛 커밋으로
    되돌리면 그 짝이 깨진다. 새 커밋으로 올릴 때는 그 커밋이 nsync 목적지 걷기(5958e4e·8cd72f3)와 새 목적지 루트 만들기(2a10a18)를
    포함하는지 확인한다."""
    text = (Path(__file__).resolve().parent.parent / "deploy" / "docker" / "Dockerfile.mpifileutils").read_text()
    refs = re.findall(r"^ARG MPIFILEUTILS_REF=([0-9a-f]{40})$", text, flags=re.M)
    assert len(refs) == 1, refs
    assert refs[0] not in _NSYNC_FOLLOWS_DST_LINKS
    assert refs[0] not in _NSYNC_BREAKS_NEW_DESTINATIONS
