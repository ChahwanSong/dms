"""planner 1b 경로 겹침(2026-10-27, Planner._path_conflict -- 사유 path_conflict).

규칙(사용자 결정, BACKLOG 「rm 경로의 다른 요청·배치와의 겹침 판정」): 계획하려는 요청 R 보다 앞선(commit_order) 비종단 요청
-- 또는 이미 계획된(Pending 이 아닌) 요청 -- 이 같은 스토리지에서 R 과 경로가 같거나 조상·자손이고 한쪽이 rm 이면 R 은
Conflict(path_conflict)로 끝난다. 상대 요청 id 는 path_conflict 이벤트 payload 에 남는다(요청 상세의 링크). 배치 자식도
planner 를 지나므로 같은 규칙이다(항목은 Failed path_conflict). 겹침 판정 자체의 표는 test_path_overlap.py.
"""
import json

import pytest

from dms.batch_cancel import record_item_terminal
from dms.domain import ROLE_ADMIN, RequestState, build_data_payload
from dms.planner import Planner
from dms.repositories import Repositories

NOW = "2026-08-02T10:00:00Z"
OPS = "ops"


class _Settings:
    agent_report_stale_seconds = 300
    allow_privileged_requesters = True
    privileged_requesters = frozenset({OPS})


def _seed(repos, storages=("s1", "s2")):
    for name in storages:
        repos.storages.create(storage_name=name, mount_path=f"/mnt/{name}", managed_root=f"/mnt/{name}/dms",
                              backend_type="cephfs", actor="admin")
        repos.storages.set_status(name, "Ready", "ready_nodes=1")
    repos.agents.ingest("n1", {
        "node_name": "n1",
        "mounts": [{"storage_name": s, "mount_path": f"/mnt/{s}", "status": "Ready", "writable": True}
                   for s in storages],
        "tools": [{"name": t, "status": "Ready"} for t in ("dscan", "dsync", "nsync", "drm")]},
        reported_at="2026-08-02T09:59:00Z")
    if repos.accounts.get(OPS) is None:
        repos.accounts.create(OPS, "pw", ROLE_ADMIN)


def _submit(repos, operation, *, batch_id=None, **paths):
    options = {"recursive": True} if operation == "rm" else {}
    payload, key = build_data_payload(operation, options=options, **paths)
    payload.update(run_as_root=True, owner_username=None)
    return repos.requests.create(operation=operation, requester_id=OPS, actor=OPS, resource_key=key,
                                 payload=payload, priority="mid", auth_method="session", batch_id=batch_id)


def rm(repos, target, storage="s1", **kw):
    return _submit(repos, "rm", storage=storage, target=target, **kw)


def scan(repos, target, storage="s1", **kw):
    return _submit(repos, "scan", storage=storage, target=target, **kw)


def sync(repos, source, destination, src="s1", dst="s2", **kw):
    return _submit(repos, "sync", source_storage=src, source=source, destination_storage=dst,
                   destination=destination, **kw)


def _plan(repos):
    return Planner(repos, settings=_Settings()).run_once(now_iso=NOW)


def _state(repos, rid):
    return repos.requests.get(rid)["state"]


def _conflict_events(repos, rid):
    return [e for e in repos.observability.events_for_request(rid) if e["event_type"] == "path_conflict"]


def _assert_path_conflict(repos, result, rid, other):
    assert result[rid] == "conflict:path_conflict"
    assert _state(repos, rid) == RequestState.CONFLICT.value
    assert repos.requests.result(rid)["reason_code"] == "path_conflict"
    assert repos.requests.last_reason_code(rid) == "path_conflict"
    assert repos.data_jobs.list_jobs(request_id=rid) == []
    events = _conflict_events(repos, rid)
    assert len(events) == 1
    payload = events[0]["payload"]
    assert payload["conflict_request_id"] == other
    assert payload["conflict_operation"] == repos.requests.get(other)["operation"]
    assert other[:8] in events[0]["message"]


@pytest.fixture
def repos(db):
    r = Repositories(db)
    _seed(r)
    return r


# ---- 잠금을 쥔 앞선 요청이 늦은 요청을 막는다 -------------------------------------------------------------------------

def test_earlier_active_rm_blocks_later_descendant_rm(repos):
    first = rm(repos, "a")
    second = rm(repos, "a/b/c")
    result = _plan(repos)
    assert result[first] == "planned"
    _assert_path_conflict(repos, result, second, first)


def test_earlier_active_rm_blocks_later_ancestor_rm(repos):
    first = rm(repos, "a/b")
    second = rm(repos, "a")
    result = _plan(repos)
    assert result[first] == "planned"
    _assert_path_conflict(repos, result, second, first)


def test_same_rm_target_stays_resource_conflict(repos):
    # 정확히 같은 대상(resource_key 같음)은 기존 1단계가 먼저 -- 사유는 그대로 resource_conflict.
    first = rm(repos, "a")
    second = rm(repos, "a")
    result = _plan(repos)
    assert result[first] == "planned" and result[second] == "conflict"
    assert repos.requests.result(second)["reason_code"] == "resource_conflict"


def test_rm_under_running_sync_source_conflicts(repos):
    s = sync(repos, "data", "backup")
    r = rm(repos, "data/old")
    result = _plan(repos)
    assert result[s] == "planned"
    _assert_path_conflict(repos, result, r, s)


def test_rm_over_running_sync_destination_conflicts(repos):
    s = sync(repos, "data", "backup/team")
    r = rm(repos, "backup", storage="s2")
    result = _plan(repos)
    assert result[s] == "planned"
    _assert_path_conflict(repos, result, r, s)


def test_sync_after_rm_conflicts_symmetrically(repos):
    # 대칭 판정 -- sync 계획 때도 rm 을 본다(BACKLOG 「대칭 판정이 없으면 반쪽」).
    r = rm(repos, "data")
    s = sync(repos, "data/sub", "x")
    result = _plan(repos)
    assert result[r] == "planned"
    _assert_path_conflict(repos, result, s, r)


def test_sync_destination_after_rm_conflicts(repos):
    r = rm(repos, "incoming", storage="s2")
    s = sync(repos, "data", "incoming/today")
    result = _plan(repos)
    assert result[r] == "planned"
    _assert_path_conflict(repos, result, s, r)


def test_scan_after_rm_conflicts_and_rm_after_scan_conflicts(repos):
    r = rm(repos, "a")
    sc = scan(repos, "a/b")
    result = _plan(repos)
    assert result[r] == "planned"
    _assert_path_conflict(repos, result, sc, r)
    sc2 = scan(repos, "z")
    r2 = rm(repos, "z/old")
    result = _plan(repos)
    assert result[sc2] == "planned"
    _assert_path_conflict(repos, result, r2, sc2)


# ---- 충돌이 아닌 것 ----------------------------------------------------------------------------------------------

def test_sync_vs_sync_overlap_is_not_a_path_conflict(repos):
    a = sync(repos, "data", "backup")
    b = sync(repos, "data/sub", "backup/sub")
    result = _plan(repos)
    assert result[a] == "planned" and result[b] == "planned"


def test_scan_vs_sync_overlap_is_not_a_path_conflict(repos):
    a = scan(repos, "data")
    b = sync(repos, "data/sub", "x")
    result = _plan(repos)
    assert result[a] == "planned" and result[b] == "planned"


def test_component_prefix_and_siblings_do_not_conflict(repos):
    a = rm(repos, "a/b")
    b = rm(repos, "a/bc")
    c = rm(repos, "a/c")
    result = _plan(repos)
    assert [result[x] for x in (a, b, c)] == ["planned"] * 3


def test_same_path_on_another_storage_does_not_conflict(repos):
    a = rm(repos, "a", storage="s1")
    b = rm(repos, "a/b", storage="s2")
    result = _plan(repos)
    assert result[a] == "planned" and result[b] == "planned"


# ---- 잠금이 풀리는 경우 --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("terminal", [RequestState.SUCCEEDED, RequestState.FAILED, RequestState.CANCELLED,
                                      RequestState.REJECTED, RequestState.CONFLICT])
def test_terminal_earlier_request_frees_the_path(repos, terminal):
    first = rm(repos, "a")
    assert _plan(repos)[first] == "planned"
    repos.requests.set_state_with_result(first, terminal, reason_code=None, actor="test")
    second = rm(repos, "a/b")
    assert _plan(repos)[second] == "planned"


def test_cancelled_pending_earlier_frees_the_path(repos):
    first = rm(repos, "a")
    assert repos.requests.cancel_if_pending(first, reason_code=None, actor="test")
    second = rm(repos, "a/b")
    assert _plan(repos)[second] == "planned"


def test_earlier_rejected_in_the_same_tick_frees_the_path(repos):
    # 앞선 요청이 같은 틱에 다른 사유로 거부되면(종단) 뒤 요청은 막히지 않는다.
    first = rm(repos, "a", storage="gone")          # 없는 스토리지 -- storage_missing
    second = rm(repos, "a/b", storage="gone")
    result = _plan(repos)
    assert result[first] == "rejected:storage_missing"
    assert result[second] == "rejected:storage_missing"   # path_conflict 가 아니다
    assert repos.requests.result(second)["reason_code"] == "storage_missing"


def test_two_pending_in_one_tick_earlier_wins(repos):
    # commit_order 가 작은 쪽이 이긴다 -- 같은 틱에 둘 다 Pending 이어도 list_pending 이 commit_order 순이라 앞선 쪽이 먼저
    # 계획(잠금)되고, 뒤 요청은 그 Planned 를 본다. 자손이 먼저 와도 마찬가지다(먼저 온 쪽이 이긴다).
    earlier = rm(repos, "q/r")
    later = rm(repos, "q")
    result = _plan(repos)
    assert result[earlier] == "planned"
    _assert_path_conflict(repos, result, later, earlier)


def test_already_planned_later_request_also_holds_the_lock(repos, db):
    # 계획 오류 등으로 Pending 에 남은 앞선 요청 R 이, 그 사이 계획된 뒤 요청 L 과 겹치면 R 이 Conflict 다(잠금은 계획된 쪽).
    r = rm(repos, "a")
    planned_later = rm(repos, "a/b")
    # R 을 건너뛰고 L 만 계획된 상태를 만든다(옛 행·경합의 모양).
    db.execute("UPDATE requests SET state = 'Planned' WHERE request_id = :r", {"r": planned_later})
    result = _plan(repos)
    _assert_path_conflict(repos, result, r, planned_later)


def test_later_pending_does_not_block_earlier(repos, db):
    # 뒤에 온 Pending 은 잠금이 아니다 -- 앞선 요청이 이긴다(find_active 와 같은 방향).
    earlier = rm(repos, "a")
    later = rm(repos, "a/b")
    candidates = repos.requests.overlap_candidates(
        request_id=earlier, commit_order=repos.requests.get(earlier)["commit_order"], destructive_only=False)
    assert [c["request_id"] for c in candidates] == []
    candidates = repos.requests.overlap_candidates(
        request_id=later, commit_order=repos.requests.get(later)["commit_order"], destructive_only=False)
    assert [c["request_id"] for c in candidates] == [earlier]


# ---- 배치 자식 -----------------------------------------------------------------------------------------------------

def _batch(repos, operation, targets):
    return repos.batches.create(operation=operation, requester_id=OPS, actor=OPS, max_concurrency=4,
                                options={"recursive": True} if operation == "rm" else {}, note=None,
                                items=[{"storage": "s1", "target": t} for t in targets], status="Running",
                                auth_method="session")


def test_rm_batch_child_overlapping_active_sync_becomes_failed_item(repos, db):
    s = sync(repos, "data", "backup")
    assert _plan(repos)[s] == "planned"
    bid = _batch(repos, "rm", ["data/old", "other"])
    c1 = rm(repos, "data/old", batch_id=bid)
    c2 = rm(repos, "other", batch_id=bid)
    for seq, rid in ((0, c1), (1, c2)):
        db.execute("UPDATE batch_items SET status = 'Materialized', request_id = :r WHERE batch_id = :b AND seq = :s",
                   {"r": rid, "b": bid, "s": seq})
    result = _plan(repos)
    _assert_path_conflict(repos, result, c1, s)
    assert result[c2] == "planned"
    # 배치는 그 항목을 Failed path_conflict 로 보인다(orchestrator·취소 그물이 쓰는 record_item_terminal 그대로).
    assert record_item_terminal(repos, bid, 0, c1, _state(repos, c1))
    item = [i for i in repos.batches.list_items(bid) if i["seq"] == 0][0]
    assert item["status"] == "Failed" and item["reason_code"] == "path_conflict"
    assert repos.batches.get(bid)["failed_count"] == 1


def test_single_rm_after_active_batch_child_conflicts(repos):
    bid = _batch(repos, "sync", ["x"])
    child = sync(repos, "proj", "mirror", batch_id=bid)
    assert _plan(repos)[child] == "planned"
    single = rm(repos, "proj/tmp")
    result = _plan(repos)
    _assert_path_conflict(repos, result, single, child)


def test_sibling_batch_children_are_serialized_too(repos):
    # 생성 검증(batch_rm_targets_overlap)이 막는 모양이지만(재실행·DB 쓰기), 같은 배치의 형제끼리도 같은 규칙이다.
    bid = _batch(repos, "rm", ["a", "b"])
    c1 = rm(repos, "a", batch_id=bid)
    c2 = rm(repos, "a/b", batch_id=bid)
    result = _plan(repos)
    assert result[c1] == "planned"
    _assert_path_conflict(repos, result, c2, c1)


# ---- 신뢰 경계 ----------------------------------------------------------------------------------------------------

def test_unreadable_candidate_payload_is_skipped(repos, db):
    # 깨진 payload 의 상대는 planner 가 건너뛴다(잡 행으로 stepper 실행 관문이 본다) -- 변조 행 때문에 종단 Conflict 를 내지
    # 않는다.
    bad = rm(repos, "a")
    db.execute("UPDATE requests SET payload = :p, state = 'Planned' WHERE request_id = :r", {"p": "{not json", "r": bad})
    later = rm(repos, "a/b")
    assert _plan(repos)[later] == "planned"


def test_own_malformed_path_is_treated_as_storage_root(repos, db):
    # 자기 경로가 깨졌으면(변조 -- '..' 성분) 루트로 본다: 같은 스토리지의 활성 작업 무엇과도 겹친다(fail-closed).
    active = scan(repos, "deep/inside")
    assert _plan(repos)[active] == "planned"
    tampered = rm(repos, "x")
    row = repos.requests.get(tampered)
    payload = dict(row["payload"], target="x/../..")
    db.execute("UPDATE requests SET payload = :p WHERE request_id = :r",
               {"p": json.dumps(payload), "r": tampered})
    result = _plan(repos)
    _assert_path_conflict(repos, result, tampered, active)


def test_non_destructive_requests_only_read_rm_candidates(repos):
    # 비파괴 요청(scan·sync)의 후보는 rm(·미지 연산)뿐이다 -- 겹치는 scan 이 활성이어도 읽지 않는다.
    a = scan(repos, "a")
    r = rm(repos, "zz")
    first = _plan(repos)
    assert first[a] == "planned" and first[r] == "planned"
    b = sync(repos, "a/b", "x")
    cands = repos.requests.overlap_candidates(
        request_id=b, commit_order=repos.requests.get(b)["commit_order"], destructive_only=True)
    assert [c["request_id"] for c in cands] == [r]
    all_cands = repos.requests.overlap_candidates(
        request_id=b, commit_order=repos.requests.get(b)["commit_order"], destructive_only=False)
    assert [c["request_id"] for c in all_cands] == [a, r]
    assert _plan(repos)[b] == "planned"
