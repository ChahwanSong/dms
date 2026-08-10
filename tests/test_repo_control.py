import json

import pytest
from dms.domain import DomainValidationError
from dms.repositories.control import ControlRepository


def test_policy_upsert_and_get(db):
    repo = ControlRepository(db)
    db.execute("DELETE FROM policies WHERE tool = 'dsync'")  # 시드된 기본 정책 제거
    assert repo.get_policy("dsync") is None
    repo.upsert_policy("dsync", max_nodes=4, procs_per_node=8, queue="dms-data",
                       default_priority="mid", max_priority="high",
                       preview_timeout_seconds=3600, execution_timeout_seconds=259200,
                       enabled=True, actor="admin")
    assert repo.get_policy("dsync")["max_nodes"] == 4
    with pytest.raises(DomainValidationError):
        repo.upsert_policy("dcp", max_nodes=1, procs_per_node=1, queue="q",
                           default_priority="mid", max_priority="high",
                           preview_timeout_seconds=None, execution_timeout_seconds=60,
                           enabled=True, actor="admin")


def test_denylist_matching(db):
    repo = ControlRepository(db)
    repo.deny("requester", "Mallory", reason="incident", actor="admin")
    repo.deny("group", "blocked-team", reason=None, actor="admin")
    assert repo.is_denied(requester="mallory", owner="x", groups=[]) == "mallory"
    assert repo.is_denied(requester="a", owner="b", groups=["Blocked-Team"]) == "blocked-team"
    assert repo.is_denied(requester="a", owner="b", groups=["ok"]) is None
    repo.allow("requester", "Mallory", actor="admin")
    assert repo.is_denied(requester="mallory", owner="x", groups=[]) is None


def test_control_state_roundtrip(db):
    repo = ControlRepository(db)
    assert repo.control_state()["maintenance"] == 0
    repo.set_control_state(maintenance=True, drain=False, reason="upgrade", actor="admin")
    st = repo.control_state()
    assert st["maintenance"] == 1 and st["reason"] == "upgrade"


def test_lease_semantics(db):
    repo = ControlRepository(db)
    assert repo.try_acquire_lease("planner", "h1", 30, now_iso="2026-08-02T10:00:00Z")
    assert not repo.try_acquire_lease("planner", "h2", 30, now_iso="2026-08-02T10:00:10Z")
    assert repo.try_acquire_lease("planner", "h1", 30, now_iso="2026-08-02T10:00:10Z")
    assert repo.try_acquire_lease("planner", "h2", 30, now_iso="2026-08-02T10:00:41Z")


def test_denylist_case_insensitive_write_paths(db):
    repo = ControlRepository(db)
    repo.deny("requester", "Mallory", reason="incident", actor="admin")
    repo.deny("requester", "MALLORY", reason="dup", actor="admin")  # 케이스 다른 중복 — 1행 유지
    rows = db.query("SELECT subject FROM identity_denylist WHERE subject_type = 'requester'")
    assert rows == [{"subject": "mallory"}]
    repo.allow("requester", "mAlLoRy", actor="admin")  # 케이스 달라도 삭제됨
    assert repo.is_denied(requester="Mallory", owner="x", groups=[]) is None


def test_audit_entries_returns_latest_first(db):
    repo = ControlRepository(db)
    repo.deny("requester", "u1", reason=None, actor="admin")
    repo.set_control_state(maintenance=True, drain=False, reason="r", actor="admin")
    entries = repo.audit_entries(limit=10)
    assert [e["mutation_class"] for e in entries[:2]] == ["control_state", "denylist"]
    assert repo.audit_entries(limit=1)[0]["mutation_class"] == "control_state"


def test_set_artifact_base_touches_only_its_column(db):
    # 설계 §2.1: set_control_state 의 UPDATE 는 build_node_name = :bn 을 무조건
    # 쓴다 -- 인자를 생략한 호출이 기존 값을 조용히 NULL 로 지우는 함정이다(지금은
    # 라우트가 항상 넘겨 잠복). 같은 UPDATE 에 컬럼을 얹으면 그 함정이 복제되므로,
    # 전용 UPDATE 는 자기 컬럼 밖을 만질 수 없어야 한다.
    repo = ControlRepository(db)
    repo.set_control_state(maintenance=True, drain=False, reason="r",
                           build_node_name=None, actor="admin")
    repo.set_artifact_base("file:///new/base", actor="admin")
    st = repo.control_state()
    assert st["artifact_base_uri"] == "file:///new/base"
    assert st["maintenance"] == 1 and st["reason"] == "r"   # 다른 컬럼 무변경
    # 반대 방향: set_control_state 재호출이 artifact_base_uri 를 지우지 않는다
    repo.set_control_state(maintenance=False, drain=False, reason=None, actor="admin")
    assert repo.control_state()["artifact_base_uri"] == "file:///new/base"


def test_set_artifact_base_audits_forced_and_affected_jobs(db):
    # force 통과는 감사에 반드시 남는다(설계 §2.3) -- "이 잡들의 아티팩트·로그
    # 열람이 깨진다"는 사실이 나중에 추적 가능해야 한다.
    repo = ControlRepository(db)
    repo.set_artifact_base("file:///new/base", actor="ops", forced=True,
                           affected_jobs=3)
    entry = repo.audit_entries(limit=1)[0]
    assert entry["mutation_class"] == "artifact_base"
    assert entry["actor"] == "ops"
    after = json.loads(entry["after_state"])
    assert after == {"artifact_base_uri": "file:///new/base", "forced": True,
                     "affected_jobs": 3}


def test_set_artifact_base_check_writes_check_columns_only(db):
    # 컨트롤러 관점 검증(설계 §2.4c)은 주기 기록이다 -- 운영자 변경 표시
    # (changed_by/changed_at)나 감사 로그를 오염시키면 안 된다.
    repo = ControlRepository(db)
    repo.set_artifact_base_check(uri="file:///x", ok=False,
                                 reason="artifact_base_missing",
                                 now_iso="2026-08-10T00:00:00Z")
    st = repo.control_state()
    assert st["artifact_base_check_uri"] == "file:///x"
    assert st["artifact_base_check_ok"] == 0
    assert st["artifact_base_check_reason"] == "artifact_base_missing"
    assert st["artifact_base_check_at"] == "2026-08-10T00:00:00Z"
    assert st["changed_by"] is None
    assert repo.audit_entries(limit=10) == []   # 감사 없음(주기 기록)
