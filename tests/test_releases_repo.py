import pytest
from dms.db import Database
from dms.domain import DomainValidationError
from dms.migrations import migrate
from dms.repositories import Repositories
from dms.repositories.releases import COMPONENTS, ROLLOUT_ORDER


@pytest.fixture
def repos(tmp_path):
    db = Database.connect(f"sqlite:///{tmp_path}/t.db")
    migrate(db)
    return Repositories(db)


def _items(*components):
    return [{"component": c, "image": f"pkg-01:5000/x:{c}-t1", "tag": f"{c}-t1"}
            for c in components]


def test_rollout_order_is_agent_api_controller():
    # 컨트롤러 자기 갱신이 마지막이어야 앞의 둘이 이미 종단이다(설계 §2)
    assert ROLLOUT_ORDER == ("dms-agent", "dms-api", "dms-controller")


def test_components_carry_real_container_names():
    # 컨테이너 이름은 워크로드 이름에서 유도되지 않는다(dms-controller → controller)
    assert COMPONENTS["dms-agent"] == {
        "kind": "DaemonSet", "workload": "dms-agent", "container": "agent",
        "repository": "dms-agent", "selector": "app.kubernetes.io/name=dms-agent"}
    assert COMPONENTS["dms-api"]["container"] == "api"
    assert COMPONENTS["dms-api"]["repository"] == "dms"
    assert COMPONENTS["dms-controller"]["container"] == "controller"
    assert COMPONENTS["dms-controller"]["kind"] == "Deployment"


def test_create_batch_persists_rollout_order(repos):
    # 제출 순서가 아니라 ROLLOUT_ORDER가 seq를 결정한다 — 배치 중간에 죽은
    # 컨트롤러가 DB의 seq만 보고 이어가야 하므로 순서는 반드시 지속돼야 한다
    rows = repos.releases.create_batch(
        items=_items("dms-controller", "dms-agent"), actor="ops")
    assert [r["component"] for r in rows] == ["dms-agent", "dms-controller"]
    assert rows[0]["seq"] < rows[1]["seq"]
    assert all(r["state"] == "Pending" for r in rows)


def test_create_batch_rejects_concurrent_rollout(repos):
    repos.releases.create_batch(items=_items("dms-api"), actor="ops")
    with pytest.raises(DomainValidationError) as e:
        repos.releases.create_batch(items=_items("dms-agent"), actor="ops")
    assert e.value.reason_code == "rollout_in_progress"


def test_create_batch_writes_release_audit(repos):
    repos.releases.create_batch(items=_items("dms-api"), actor="ops")
    entries = repos.control.audit_entries(limit=5)
    assert any(e["mutation_class"] == "release" and e["actor"] == "ops"
               for e in entries)


def test_active_and_transitions(repos):
    rows = repos.releases.create_batch(items=_items("dms-agent", "dms-api"),
                                       actor="ops")
    head = rows[0]
    repos.releases.mark_applying(head["id"])
    active = repos.releases.active()
    assert [r["state"] for r in active] == ["Applying", "Pending"]
    repos.releases.finish(head["id"], state="Applied")
    assert [r["id"] for r in repos.releases.active()] == [rows[1]["id"]]


def test_mark_applying_updates_applied_at(repos):
    row = repos.releases.create_batch(items=_items("dms-api"), actor="ops")[0]
    repos.releases.mark_applying(row["id"])
    after = repos.releases.get(row["id"])
    assert after["state"] == "Applying"
    assert after["applied_at"] >= row["applied_at"]


def test_note_progress_moves_applied_at_and_keeps_the_row_resumable(repos):
    # I6: 진행 중인 DaemonSet의 회수 시계를 다시 건다. 재개 판정은 state만 보므로
    # applied_at을 앞당겨도 "Applying이면 이어받는다"는 계약은 그대로여야 한다.
    row = repos.releases.create_batch(items=_items("dms-agent"), actor="ops")[0]
    repos.releases.mark_applying(row["id"])
    repos.releases.note_progress(row["id"], progress=3, now="2030-01-01T00:00:00Z")
    after = repos.releases.get(row["id"])
    assert (after["applied_at"], after["progress"]) == ("2030-01-01T00:00:00Z", 3)
    assert after["state"] == "Applying"
    assert [r["id"] for r in repos.releases.active()] == [row["id"]]


def test_note_progress_never_touches_non_applying_rows(repos):
    # 종단 행의 시각을 되돌리거나 아직 패치도 안 나간 Pending을 건드리면 안 된다.
    rows = repos.releases.create_batch(items=_items("dms-agent", "dms-api"),
                                       actor="ops")
    repos.releases.note_progress(rows[1]["id"], progress=9, now="2030-01-01T00:00:00Z")
    pending = repos.releases.get(rows[1]["id"])
    assert (pending["progress"], pending["applied_at"]) == (None, rows[1]["applied_at"])

    repos.releases.mark_applying(rows[0]["id"])
    repos.releases.finish(rows[0]["id"], state="Applied")
    done = repos.releases.get(rows[0]["id"])
    repos.releases.note_progress(rows[0]["id"], progress=9, now="2030-01-01T00:00:00Z")
    assert repos.releases.get(rows[0]["id"]) == done


def test_null_seq_rows_sort_last_not_first(repos):
    # seq는 nullable이고(SQLite ALTER 제약) ORDER BY seq만 두면 SQLite는 NULL을
    # 먼저, PostgreSQL은 나중에 놓는다 -- 그 갈림은 "누가 head인가"를 뒤집는다.
    # 구형 DB에서 ALTER로 보강된 NULL seq 행이 head를 가로채면 ROLLOUT_ORDER가
    # 조용히 깨지므로, 어느 백엔드에서도 맨 뒤여야 한다.
    rows = repos.releases.create_batch(items=_items("dms-agent", "dms-api"),
                                       actor="ops")
    repos.releases._db.execute(
        """INSERT INTO releases (component, image, tag, digest, state, reason_code,
               seq, actor, applied_at)
           VALUES ('dms-controller', 'i', 't', NULL, 'Pending', NULL, NULL, 'ops',
                   '2020-01-01T00:00:00Z')""")
    active = repos.releases.active()
    assert [r["id"] for r in active[:2]] == [rows[0]["id"], rows[1]["id"]]
    assert active[-1]["seq"] is None


def test_finish_is_terminal_guarded(repos):
    row = repos.releases.create_batch(items=_items("dms-api"), actor="ops")[0]
    repos.releases.mark_applying(row["id"])
    repos.releases.finish(row["id"], state="Applied")
    repos.releases.finish(row["id"], state="Failed", reason_code="rollout_timeout")
    assert repos.releases.get(row["id"])["state"] == "Applied"   # 종단은 못 덮는다


def test_abort_pending_only_touches_pending(repos):
    rows = repos.releases.create_batch(items=_items("dms-agent", "dms-api"),
                                       actor="ops")
    repos.releases.mark_applying(rows[0]["id"])
    repos.releases.finish(rows[0]["id"], state="Failed", reason_code="rollout_timeout")
    n = repos.releases.abort_pending(reason_code="rollout_aborted")
    assert n == 1
    assert repos.releases.get(rows[0]["id"])["reason_code"] == "rollout_timeout"
    tail = repos.releases.get(rows[1]["id"])
    assert (tail["state"], tail["reason_code"]) == ("Failed", "rollout_aborted")


def test_current_is_max_id_per_component(repos):
    a = repos.releases.create_batch(items=_items("dms-api"), actor="ops")[0]
    repos.releases.mark_applying(a["id"])
    repos.releases.finish(a["id"], state="Applied")
    b = repos.releases.create_batch(items=_items("dms-api"), actor="ops")[0]
    current = repos.releases.current()
    assert current["dms-api"]["id"] == b["id"]      # 상태 무관, MAX(id)가 "현재"


def test_list_is_newest_first(repos):
    a = repos.releases.create_batch(items=_items("dms-api"), actor="ops")[0]
    repos.releases.finish(a["id"], state="Failed", reason_code="rollout_timeout")
    b = repos.releases.create_batch(items=_items("dms-agent"), actor="ops")[0]
    ids = [r["id"] for r in repos.releases.list(limit=10)]
    assert ids.index(b["id"]) < ids.index(a["id"])
