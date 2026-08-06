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
