"""sync 「목적지가 소스 하위 경로일 수 없습니다」 판정을 실제 경로로(2026-10-12 사용자 보고).

보고: 소스 스토리지 /home/gpu1, 목적지 스토리지 /mirr24/gpu1, 경로 둘 다 cocoa.song → 422 sync_destination_inside_source.
원인: domain.validate_sync_paths 가 스토리지를 보지 않고 **상대경로끼리만** 비교했다. 이제
- 같은 스토리지: 지금처럼 상대경로 포함 검사(build_data_payload),
- 다른 스토리지: managed_root 를 붙인 실제 경로로(domain.sync_overlap_problem) -- planner 가 도구를 정한 뒤 dsync(한 노드가
  둘 다 마운트 -- 같은 이름 공간)에서만 본다. 루트가 포개진 두 스토리지(/c/m 의 t/x 와 /c/m/t 의 x)처럼 실제로 같은 곳이면
  막고, nsync(서로 다른 노드의 다른 파일시스템)에서는 비교하지 않는다(검증 지적).
"""
import pytest

from dms.domain import (DomainValidationError, build_data_payload, managed_abs_path, sync_overlap_problem,
                        validate_sync_paths)
from dms.repositories import Repositories

# --- 순수 판정 ---------------------------------------------------------------------------------------------------

def test_different_storages_do_not_compare_relative_paths():
    assert validate_sync_paths("cocoa.song", "cocoa.song", same_storage=False) == ("cocoa.song", "cocoa.song")
    with pytest.raises(DomainValidationError) as e:                       # 같은 스토리지는 그대로 막는다
        validate_sync_paths("cocoa.song", "cocoa.song", same_storage=True)
    assert e.value.reason_code == "sync_destination_inside_source"


def test_build_data_payload_uses_the_storage_pair():
    payload, _ = build_data_payload("sync", source_storage="gpu1-home", source="cocoa.song",
                                    destination_storage="gpu1-mirr", destination="cocoa.song", options={})
    assert (payload["source"], payload["destination"]) == ("cocoa.song", "cocoa.song")
    with pytest.raises(DomainValidationError) as e:
        build_data_payload("sync", source_storage="s", source="a", destination_storage="s", destination="a/b",
                           options={})
    assert e.value.reason_code == "sync_destination_inside_source"


@pytest.mark.parametrize("src_root,src,dst_root,dst,expected", [
    ("/home/gpu1", "cocoa.song", "/mirr24/gpu1", "cocoa.song", None),              # 사용자 사례
    ("/c/m", "t/x", "/c/m/t", "x", "sync_destination_inside_source"),             # 포개진 루트, 실제로 같은 곳
    ("/c/m", "t", "/c/m/t", "x", "sync_destination_inside_source"),               # 목적지가 소스 아래
    ("/c/m/t", "x", "/c/m", "t", None),                                           # 소스가 목적지 아래(방향 유지)
    ("/c/m", "a", "/c/m", "ab", None),                                            # 이름 앞부분만 같다
    ("/", "a", "/", "a/b", "sync_destination_inside_source"),                     # 남은 root "/" 행도 판정
    ("/c/m", None, "/d", "x", "unsafe_path"),                                     # DB 변조(비문자열)
])
def test_sync_overlap_problem_table(src_root, src, dst_root, dst, expected):
    assert sync_overlap_problem(src_root, src, dst_root, dst) == expected


def test_managed_abs_path_matches_the_stepper_rule():
    assert managed_abs_path("/cephfs/dms", "a/b") == "/cephfs/dms/a/b"
    assert managed_abs_path("/cephfs/dms", "/etc") == "/cephfs/dms/etc"          # 절대 rel 은 안에 가둔다(_abs 와 같다)


# --- API -------------------------------------------------------------------------------------------------------

def _storage(repos, name, mount, root):
    repos.storages.create(storage_name=name, mount_path=mount, managed_root=root, backend_type="cephfs", actor="t")


def _sync(src_storage, src, dst_storage, dst):
    return {"operation": "sync", "source_storage": src_storage, "source": src,
            "destination_storage": dst_storage, "destination": dst, "options": {}, "priority": "mid"}


def _admin_session(client):
    # 직접 경로 제출은 면제자(특권 세션 관리자 -- 기본 특권 목록의 admin)만이다(k8s 신원 모드).
    client.app.state.repos.accounts.create("admin", "pw", "admin", actor="t")
    client.post("/api/auth/login", json={"username": "admin", "password": "pw"})


def test_submit_user_case_is_accepted(client):
    repos = client.app.state.repos
    _admin_session(client)
    _storage(repos, "gpu1-home", "/home/gpu1", "/home/gpu1")
    _storage(repos, "gpu1-mirr", "/mirr24/gpu1", "/mirr24/gpu1")
    r = client.post("/api/user/requests", json=_sync("gpu1-home", "cocoa.song", "gpu1-mirr", "cocoa.song"))
    assert r.status_code == 202, r.text


def test_submit_leaves_cross_storage_overlap_to_the_planner(client):
    # 다른 스토리지의 실제 경로 겹침은 같은 이름 공간(dsync -- 한 노드가 둘 다 마운트)일 때만 뜻이 있어, 도구가 정해지는
    # planner 가 본다(nsync 는 서로 다른 노드의 다른 파일시스템 -- 검증 지적). 제출은 받는다.
    repos = client.app.state.repos
    _admin_session(client)
    _storage(repos, "outer", "/c", "/c/m")
    _storage(repos, "inner", "/c", "/c/m/t")
    r = client.post("/api/user/requests", json=_sync("outer", "t/x", "inner", "x"))
    assert r.status_code == 202, r.text


def test_batch_with_the_user_case_is_accepted(client):
    repos = client.app.state.repos
    _admin_session(client)
    _storage(repos, "gpu1-home", "/home/gpu1", "/home/gpu1")
    _storage(repos, "gpu1-mirr", "/mirr24/gpu1", "/mirr24/gpu1")
    item = {"source_storage": "gpu1-home", "source": "cocoa.song",
            "destination_storage": "gpu1-mirr", "destination": "cocoa.song"}
    r = client.post("/api/admin/batches", json={"operation": "sync", "max_concurrency": 1, "items": [item]})
    assert r.status_code == 202, r.text


# --- planner(도구가 정해진 뒤 -- dsync 에서만 실제 경로 판정) ---------------------------------------------------------

from dms.planner import Planner  # noqa: E402

NOW = "2026-10-12T00:00:00Z"


class _S:
    # 요청자는 특권 세션 관리자(ops) -- 직접 경로는 면제자만이고 모든 잡은 root 다(k8s 신원 모드).
    agent_report_stale_seconds = 300
    allow_privileged_requesters = True
    privileged_requesters = frozenset({"ops"})
    planner_identity_grace_seconds = 300


def _planner(repos):
    if repos.accounts.get("ops") is None:
        repos.accounts.create("ops", "pw", "admin")
    return Planner(repos, settings=_S())


def _ready(repos, name, mount, root):
    _storage(repos, name, mount, root)
    repos.storages.set_status(name, "Ready", "ready_nodes=1")


def _report(repos, node, storages):
    repos.agents.ingest(node, {"node_name": node,
        "mounts": [{"storage_name": n, "mount_path": m, "status": "Ready", "writable": True} for n, m in storages],
        "tools": [{"name": t, "status": "Ready"} for t in ("dsync", "nsync")]}, reported_at="2026-10-11T23:59:00Z")


def _policies(repos):
    for tool in ("dsync", "nsync"):
        repos.control.upsert_policy(tool, max_nodes=3, procs_per_node=8, queue="dms-data", default_priority="mid",
                                    max_priority="high", preview_timeout_seconds=3600,
                                    execution_timeout_seconds=3600, enabled=True, actor="admin")


def _sync_request(repos, src, s_rel, dst, d_rel, key):
    return repos.requests.create(operation="sync", requester_id="ops", actor="ops", resource_key=key,
                                 payload={"source_storage": src, "source": s_rel, "destination_storage": dst,
                                          "destination": d_rel, "options": {}, "owner_username": None,
                                          "run_as_root": True},
                                 priority="mid", auth_method="session")


def test_planner_rejects_overlap_when_one_node_mounts_both(db):
    # dsync(한 노드가 두 스토리지를 함께 마운트) -- 두 실제 경로가 한 트리에 있다: 루트가 포개져 같은 곳이면 거부.
    repos = Repositories(db)
    _ready(repos, "outer", "/c", "/c/m"); _ready(repos, "inner", "/c", "/c/m/t"); _policies(repos)
    _report(repos, "n1", [("outer", "/c"), ("inner", "/c")])
    rid = _sync_request(repos, "outer", "t/x", "inner", "x", "k-dsync")
    result = _planner(repos).run_once(now_iso=NOW)
    assert result[rid] != "planned"
    assert repos.requests.get(rid)["state"] == "Rejected"
    assert repos.requests.last_reason_code(rid) == "sync_destination_inside_source"


def test_planner_does_not_compare_paths_across_separate_nodes(db):
    # nsync(함께 마운트한 노드 없음) -- 소스·목적지는 서로 다른 노드의 다른 파일시스템이라 문자열이 같아도 다른 곳이다.
    repos = Repositories(db)
    _ready(repos, "outer", "/c", "/c/m"); _ready(repos, "inner", "/c", "/c/m/t"); _policies(repos)
    _report(repos, "n1", [("outer", "/c")]); _report(repos, "n2", [("inner", "/c")])
    rid = _sync_request(repos, "outer", "t/x", "inner", "x", "k-nsync")
    assert _planner(repos).run_once(now_iso=NOW)[rid] == "planned"
    assert repos.data_jobs.list_jobs(request_id=rid)[0]["tool"] == "nsync"


def test_planner_plans_the_user_case_on_one_node(db):
    repos = Repositories(db)
    _ready(repos, "gpu1-home", "/home/gpu1", "/home/gpu1"); _ready(repos, "gpu1-mirr", "/mirr24/gpu1", "/mirr24/gpu1")
    _policies(repos)
    _report(repos, "n1", [("gpu1-home", "/home/gpu1"), ("gpu1-mirr", "/mirr24/gpu1")])
    rid = _sync_request(repos, "gpu1-home", "cocoa.song", "gpu1-mirr", "cocoa.song", "k-user")
    assert _planner(repos).run_once(now_iso=NOW)[rid] == "planned"
    assert repos.data_jobs.list_jobs(request_id=rid)[0]["tool"] == "dsync"
