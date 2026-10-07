"""보조 그룹(2026-10-07) stepper: 스냅숏 모양 검사 · 제출 직전 LDAP 재확인(D1) · LDAP 장애 보류 3번(D2) ·
같은 틱 서킷·예산 · vcjob 큐 대기 재확인(2패스) · 보류 중 사라진 파드.

하네스는 test_stepper_fail_closed 와 같다(스텁 어댑터 + 실 DB). JobStepper 는 틱마다 새로 만든다(컨트롤러와 같다 --
인스턴스 필드 = 틱 상태). 시계는 둘을 주입한다: D2 간격용 ISO clock(실시간과 동떨어진 2001년에서 시작 -- 판정이
행의 at 이 아니라 이 clock 기준임을 드러낸다)과 틱 예산용 monotonic. 리졸버는 호출을 세고, 호출마다 monotonic 을
전진시킬 수 있다(느린 LDAP).
"""
import itertools
from datetime import datetime, timezone

import pytest

from dms.domain import DataJobState, RequestState
from dms.execution import ExecStatus, StubExecutionAdapter
from dms.identity import (LDAP_TICK_BUDGET_SECONDS, IdentityLookupInvalid, IdentityUnavailable,
                          ResolvedIdentity)
from dms.repositories import Repositories
from dms.stepper import JobStepper, identity_problem

_SECRET = "ldap://secret-host:389"
_keys = itertools.count()


class _Settings:
    agent_report_stale_seconds = 300
    preview_ttl_seconds = 86400
    artifact_base_uri = "file:///art"
    allow_privileged_requesters = False
    privileged_requesters = frozenset()
    vcjob_ttl_seconds = 86400


class _Clock:
    def __init__(self, epoch=1_000_000_000):          # 2001-09-09 -- 실시간(행의 at)과 동떨어진 값
        self.epoch = epoch

    def __call__(self):
        return datetime.fromtimestamp(self.epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def advance(self, seconds):
        self.epoch += seconds


class _Mono:
    def __init__(self):
        self.t = 5000.0

    def __call__(self):
        return self.t


class _CountingResolver:
    """호출(username, deadline)을 센다. down = 전송 장애(plain IdentityUnavailable -- 서킷을 연다; down_users 는 그
    사용자 조회에서만), invalid = 사용자별 조회 오류(IdentityLookupInvalid -- 서킷을 열지 않는다), boom = 예상 밖 예외."""

    def __init__(self, users, *, mono=None, cost=0.0):
        self.users = dict(users)
        self.calls = []
        self.down = False
        self.down_users = set()
        self.invalid = set()
        self.boom = set()
        self.mono = mono
        self.cost = cost

    def resolve(self, username, *, deadline=None):
        self.calls.append((username, deadline))
        if self.mono is not None:
            self.mono.t += self.cost
        if self.down or username in self.down_users:
            raise IdentityUnavailable(f"{_SECRET} [Errno 111] Connection refused")
        if username in self.invalid:
            raise IdentityLookupInvalid(f"duplicate user entries: 2 at {_SECRET}")
        if username in self.boom:
            raise RuntimeError("unexpected")
        return self.users.get(username)

    def names(self):
        return [u for u, _d in self.calls]


class _Adapter(StubExecutionAdapter):
    def __init__(self):
        super().__init__()
        self.terminated = []

    def terminate(self, ref):
        self.terminated.append(ref)
        super().terminate(ref)


def _fresh(username="alice", uid=10001, gid=10000, gids=(10010, 20001)):
    return ResolvedIdentity(username, uid, gid, ("dmsproj",), False, group_gids=tuple(gids))


def _ident(username="alice", uid=10001, gid=10000, gids=(10010,), status="applied"):
    return {"uid": uid, "gid": gid, "username": username, "groups": ["dmsproj"], "privileged": False,
            "supplementary_gids": list(gids), "supplementary_gids_status": status,
            "supplementary_gids_excluded": [], "supplementary_gids_found": len(gids)}


class _H:
    def __init__(self, db, users=None, *, resolver="default", cost=0.0):
        self.db = db
        self.repos = Repositories(db)
        self.adapter = _Adapter()
        self.clock = _Clock()
        self.mono = _Mono()
        if resolver == "default":
            resolver = _CountingResolver(users if users is not None else
                                         {"alice": _fresh(), "bob": _fresh("bob", uid=10002)},
                                         mono=self.mono, cost=cost)
        self.res = resolver
        for name in ("s1", "src", "dst"):
            if self.repos.storages.get(name) is None:
                self.repos.storages.create(storage_name=name, mount_path=f"/{name}",
                                           managed_root=f"/{name}/dms", backend_type="cephfs", actor="test")

    def tick(self):
        return JobStepper(self.repos, self.adapter, settings=_Settings(), identity_resolver=self.res,
                          clock=self.clock, monotonic=self.mono).run_once()

    def job(self, op="scan", ident=None):
        ident = _ident() if ident is None else ident
        payload = ({"storage": "s1", "target": "a"} if op == "scan" else
                   {"source_storage": "src", "source": "a", "destination_storage": "dst", "destination": "b"})
        rid = self.repos.requests.create(operation=op, requester_id="alice", actor="alice",
                                         resource_key=f"k-{next(_keys)}", payload=payload, priority="mid")
        self.repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
        self.repos.requests.set_state(rid, RequestState.RUNNING, actor="planner")
        plan_id = self.repos.data_jobs.create_plan(rid, actor="planner")
        tool = "dscan" if op == "scan" else "dsync"
        wp = {"tool": tool, "identity": ident, "candidates": {"primary": ["n1"]}, "process_count": 8,
              "queue": "dms-data", "priority_class": "dms-mid"}
        if op == "scan":
            jid = self.repos.data_jobs.create_job(rid, plan_id, operation="scan", priority="mid",
                                                  storage_name="s1", target="a", options={}, tool=tool,
                                                  worker_pool=wp, precondition={}, actor="planner")
        else:
            jid = self.repos.data_jobs.create_job(rid, plan_id, operation="sync", priority="mid",
                                                  source_storage="src", source="a", destination_storage="dst",
                                                  destination="b", options={}, tool=tool, worker_pool=wp,
                                                  precondition={}, actor="planner")
        return rid, jid

    def state(self, jid):
        return self.repos.data_jobs.get_job(jid)["state"]

    def reason(self, jid):
        return self.repos.data_jobs.job_transitions(jid)[-1]["reason_code"]

    def events(self, rid, event_type=None):
        return [e for e in self.repos.observability.events_for_request(rid)
                if event_type is None or e["event_type"] == event_type]

    def set_updated_at(self, jid, at):
        self.db.execute("UPDATE data_jobs SET updated_at = :a WHERE job_id = :j", {"a": at, "j": jid})

    def forge_identity(self, jid, **changes):
        job = self.repos.data_jobs.get_job(jid)
        wp = dict(job["worker_pool"])
        wp["identity"] = {**wp["identity"], **changes}
        from dms.db import dump_json
        self.db.execute("UPDATE data_jobs SET worker_pool = :w WHERE job_id = :j",
                        {"w": dump_json(wp), "j": jid})

    def shrink(self, username="alice"):
        fresh = self.res.users[username]
        self.res.users[username] = ResolvedIdentity(fresh.username, fresh.uid, fresh.gid, fresh.groups,
                                                    False, group_gids=(20001,))

    def to_executing(self, jid):
        """sync 잡을 ConfirmPending 까지 굴린 뒤 confirm 게이트를 최소 재현(라우트 없이 Executing 직접 전이)."""
        self.tick()                                   # Pending → Preflight
        self.tick()                                   # Preflight ok → PreviewRunning
        self.tick()                                   # Preview ok → ConfirmPending
        assert self.state(jid) == "ConfirmPending"
        self.repos.data_jobs.set_job_state(jid, DataJobState.EXECUTING, actor="test")


# ---- 모양(identity_problem 확장 -- 단일 장소) ----

@pytest.mark.parametrize("changes, problem", [
    ({"supplementary_gids": "10010"}, "supplementary_gids_not_list"),
    ({"supplementary_gids": [10010, 10010]}, "supplementary_gids_unsorted"),
    ({"supplementary_gids": [20001, 10010]}, "supplementary_gids_unsorted"),
    ({"supplementary_gids": [10000]}, "supplementary_gid_is_primary"),
    ({"supplementary_gids": [-1]}, "supplementary_gid_invalid"),
    ({"supplementary_gids": [True]}, "supplementary_gid_invalid"),
    ({"supplementary_gids": list(range(1, 258))}, "supplementary_gids_too_many"),
    ({"supplementary_gids_status": "none"}, "supplementary_gids_status_mismatch"),            # 목록 ≠ [] 인데 none
    ({"supplementary_gids": [], "supplementary_gids_status": "applied"}, "supplementary_gids_status_mismatch"),
    ({"supplementary_gids": [], "supplementary_gids_status": "privileged"}, "supplementary_gids_status_mismatch"),
    ({"gid": 0, "supplementary_gids": []}, "root_group_without_privilege"),                    # D5 백스톱
])
def test_shape_problems_terminate_before_any_submission_or_ldap(db, changes, problem):
    h = _H(db)
    ident = {**_ident(), **changes}
    if ident["supplementary_gids"] == [] and "supplementary_gids_status" not in changes:
        ident["supplementary_gids_status"] = "none"
    rid, jid = h.job(ident=ident)
    assert h.tick()[jid] == "Rejected"
    assert h.reason(jid) == "identity_missing_at_step"
    assert h.adapter.submitted_specs() == [] and h.res.calls == []
    (ev,) = h.events(rid, "identity_missing_at_step")
    assert problem in ev["message"]


def test_identity_problem_backstops_gid_zero_but_keeps_root_jobs():
    assert identity_problem({"uid": 10001, "gid": 0, "username": "a"}) == "root_group_without_privilege"
    assert identity_problem({"uid": 0, "gid": 0, "username": "root", "privileged": True,
                             "supplementary_gids": [], "supplementary_gids_status": "privileged"}) is None
    assert identity_problem({"uid": 10001, "gid": 10000, "username": "a"}) is None   # 배포 전 잡(키 없음)


def test_absent_key_means_no_groups_no_ldap(db):
    h = _H(db)
    rid, jid = h.job(ident={"uid": 10001, "gid": 10000, "username": "alice", "groups": [], "privileged": False})
    assert h.tick()[jid] == "Preflight"
    assert h.res.calls == []
    (spec,) = h.adapter.submitted_specs()
    assert spec.identity["supplementary_gids"] == []            # 정규화 사본은 키를 항상 싣는다
    assert h.events(rid, "identity_groups_checked") == []


# ---- D1 제출 직전 재확인 ----

def test_recheck_passes_and_spec_carries_snapshot_not_fresh_superset(db):
    h = _H(db, {"alice": _fresh(gids=(10010, 20001, 30001))})
    rid, jid = h.job(ident=_ident(gids=(10010,)))
    assert h.tick()[jid] == "Preflight"
    assert h.res.names() == ["alice"]
    (spec,) = h.adapter.submitted_specs()
    assert spec.identity["supplementary_gids"] == [10010]        # 최신값이 늘어도 스냅숏 그대로(권한은 늘지 않는다)
    (ev,) = h.events(rid, "identity_groups_checked")
    assert ev["payload"] == {"job_id": jid, "phase": "preflight", "gids": [10010]}


@pytest.mark.parametrize("row", ["preflight", "scan_execution", "preview", "exec_preflight", "execution"])
def test_shrink_terminates_per_state(db, row):
    # 계획 §6.7 의 제출 5행: 실행 자원 이전(Pending·Preflight)은 Rejected, Executing 은 Failed.
    h = _H(db)
    op = "scan" if row in ("preflight", "scan_execution") else "sync"
    rid, jid = h.job(op=op)
    if row in ("scan_execution", "preview"):
        h.tick()                                                  # Pending → Preflight(통과)
    elif row == "exec_preflight":
        h.to_executing(jid)
    elif row == "execution":
        h.to_executing(jid)
        h.tick()                                                  # exec_preflight 제출(통과)
    before_specs = len(h.adapter.submitted_specs())
    before_state = h.state(jid)
    refs = dict(h.repos.data_jobs.get_job(jid)["phase_refs"] or {})
    h.shrink()
    h.tick()
    want = {"preflight": "Rejected", "scan_execution": "Rejected", "preview": "Rejected",
            "exec_preflight": "Failed", "execution": "Failed"}[row]
    assert (before_state, h.state(jid), h.reason(jid)) == (
        {"preflight": "Pending", "scan_execution": "Preflight", "preview": "Preflight",
         "exec_preflight": "Executing", "execution": "Executing"}[row], want, "identity_changed_at_step")
    assert len(h.adapter.submitted_specs()) == before_specs      # 제출 없음
    assert set(h.adapter.terminated) == set(refs.values())       # 살아 있을 수 있는 ref 는 회수
    (ev,) = h.events(rid, "identity_changed_at_step")
    phase = {"preflight": "preflight", "scan_execution": "execution", "preview": "preview",
             "exec_preflight": "exec_preflight", "execution": "execution"}[row]
    assert ev["payload"] == {"job_id": jid, "phase": phase, "queued": False, "user_missing": False,
                             "missing_gids": [10010], "uid_changed": False, "gid_changed": False}
    assert h.repos.requests.last_reason_code(rid) == "identity_changed_at_step"


@pytest.mark.parametrize("fresh, flags", [
    (_fresh(uid=20002), {"user_missing": False, "missing_gids": [], "uid_changed": True, "gid_changed": False}),
    (_fresh(gid=20000), {"user_missing": False, "missing_gids": [], "uid_changed": False, "gid_changed": True}),
    (None, {"user_missing": True, "missing_gids": [10010], "uid_changed": None, "gid_changed": None}),
])
def test_uid_change_gid_change_user_deleted_terminate(db, fresh, flags):
    h = _H(db, {"alice": fresh})
    rid, jid = h.job()
    assert h.tick()[jid] == "Rejected"
    assert h.reason(jid) == "identity_changed_at_step"
    (ev,) = h.events(rid, "identity_changed_at_step")
    assert {k: ev["payload"][k] for k in flags} == flags


def test_resolver_none_is_ldap_not_configured(db):
    h = _H(db, resolver=None)
    rid, jid = h.job()
    assert h.tick()[jid] == "Rejected"
    assert h.reason(jid) == "ldap_not_configured"
    assert h.adapter.submitted_specs() == []
    (ev,) = h.events(rid, "identity_recheck_failed")
    assert ev["payload"]["reason"] == "ldap_not_configured"


# ---- D2 보류 · 재시도 3번 · 간격 ≥ 60s ----

def test_first_ldap_failure_holds_without_state_change(db):
    h = _H(db)
    rid, jid = h.job()
    h.set_updated_at(jid, "2000-01-01T00:00:00Z")
    transitions = len(h.repos.data_jobs.job_transitions(jid))
    h.res.down = True
    assert h.tick()[jid] == "Pending"
    job = h.repos.data_jobs.get_job(jid)
    assert job["state"] == "Pending" and job["updated_at"] > "2000-01-01T00:00:00Z"   # 큐 뒤로
    assert len(h.repos.data_jobs.job_transitions(jid)) == transitions                  # 전이 없음
    assert h.adapter.submitted_specs() == []
    (ev,) = h.events(rid, "identity_recheck_deferred")
    assert ev["payload"] == {"job_id": jid, "phase": "preflight", "attempt": 1, "max_attempts": 4,
                             "attempted_at_epoch": h.clock.epoch}


def test_retry_within_60s_skips_ldap(db):
    h = _H(db)
    rid, jid = h.job()
    h.res.down = True
    h.tick()
    h.clock.advance(59)
    h.tick()
    assert len(h.res.calls) == 1                                 # 간격 보류는 LDAP 를 부르지 않는다
    assert len(h.events(rid, "identity_recheck_deferred")) == 1  # 미계수·무이벤트
    assert h.state(jid) == "Pending"


def test_fourth_failure_terminates_ldap_unavailable(db):
    h = _H(db)
    rid, jid = h.job()
    h.res.down = True
    for attempt in (1, 2, 3):
        assert h.tick()[jid] == "Pending"
        assert [e["payload"]["attempt"] for e in h.events(rid, "identity_recheck_deferred")] == list(
            range(1, attempt + 1))
        h.clock.advance(61)
    assert h.tick()[jid] == "Rejected"                           # 첫 실패 + 재시도 3회
    assert h.reason(jid) == "ldap_unavailable"
    assert len(h.res.calls) == 4
    (ev,) = h.events(rid, "identity_recheck_failed")
    assert ev["payload"] == {"job_id": jid, "phase": "preflight", "attempts": 4, "reason": "ldap_unavailable"}


def test_success_resets_streak(db):
    h = _H(db)
    rid, jid = h.job()
    h.res.down = True
    h.tick()                                                     # attempt 1
    h.clock.advance(61)
    h.res.down = False
    assert h.tick()[jid] == "Preflight"                          # 통과 → identity_groups_checked(리셋)
    h.res.down = True
    h.clock.advance(1)                                           # 리셋 뒤라 간격 판정도 없다
    assert h.tick()[jid] == "Preflight"                          # execution 제출 직전 보류
    attempts = [(e["payload"]["phase"], e["payload"]["attempt"])
                for e in h.events(rid, "identity_recheck_deferred")]
    assert attempts == [("preflight", 1), ("execution", 1)]      # 다음 단계는 다시 1부터


def test_circuit_holds_second_job_uncounted(db):
    h = _H(db)
    rid_a, a = h.job(ident=_ident("alice"))
    rid_b, b = h.job(ident=_ident("bob", uid=10002))
    h.set_updated_at(a, "2000-01-01T00:00:00Z")
    h.set_updated_at(b, "2000-01-01T00:00:01Z")
    h.res.down = True
    h.tick()
    assert h.res.names() == ["alice"]                            # 한 번 불가를 보면 그 틱 나머지는 부르지 않는다
    assert len(h.events(rid_a, "identity_recheck_deferred")) == 1
    assert h.events(rid_b) == []                                 # 미계수·무이벤트
    assert h.repos.data_jobs.get_job(b)["updated_at"] > "2000-01-01T00:00:01Z"   # 그래도 큐 뒤로


def test_held_job_moves_to_back_of_claim_queue(db):
    h = _H(db)
    _rid, a = h.job()
    h.set_updated_at(a, "2000-01-01T00:00:00Z")
    h.res.down = True
    h.tick()
    _rid_b, b = h.job(ident={"uid": 10002, "gid": 10000, "username": "bob", "groups": [], "privileged": False})
    h.set_updated_at(b, "2000-01-01T00:00:01Z")                  # 보류 전의 a 보다 늦고 보류 뒤의 a 보다 이르다
    assert [j["job_id"] for j in h.repos.data_jobs.claim_steppable()] == [b, a]


def test_counter_write_failure_fails_closed(db, monkeypatch):
    h = _H(db)
    rid, jid = h.job()
    h.res.down = True

    def _boom(**_kw):
        raise RuntimeError("events table locked")
    monkeypatch.setattr(h.repos.observability, "record_event_strict", _boom)
    assert h.tick()[jid] == "Rejected"                           # 카운터 없이는 "3번만"을 보장 못 한다
    assert h.reason(jid) == "ldap_unavailable"
    (ev,) = h.events(rid, "identity_recheck_failed")
    assert ev["payload"]["reason"] == "counter_unwritable"


def test_spacing_uses_payload_epoch_not_row_time(db):
    # clock 은 2001년, 행의 at 은 실시간(2026년). 판정이 at 을 썼다면 '61s 뒤'가 음수 차이라 영영 보류였을 것이다.
    h = _H(db)
    rid, jid = h.job()
    h.res.down = True
    h.tick()
    h.clock.advance(61)
    h.tick()
    assert len(h.res.calls) == 2
    epochs = [e["payload"]["attempted_at_epoch"] for e in h.events(rid, "identity_recheck_deferred")]
    assert epochs == [1_000_000_000, 1_000_000_061]


# ---- 순서: 캐시 → 간격 → 서킷·예산 → resolve ----

def test_cached_success_bypasses_spacing(db):
    h = _H(db)
    _rid_a, a = h.job()
    h.res.down = True
    h.tick()                                                     # a: attempt 1
    h.res.down = False
    h.clock.advance(10)                                          # 간격(60s) 안
    _rid_b, b = h.job()                                          # 같은 사용자의 다른 잡이 이 틱에 먼저 조회한다
    h.set_updated_at(b, "2000-01-01T00:00:00Z")
    h.tick()
    assert h.state(b) == "Preflight" and h.state(a) == "Preflight"   # a 는 캐시로 통과(60s 를 더 기다리지 않는다)
    assert h.res.names() == ["alice", "alice"]                   # 첫 틱 1회 + 이번 틱 1회(b)


def test_lookup_invalid_does_not_open_circuit_and_counts_same_user_jobs(db):
    h = _H(db)
    h.res.invalid.add("alice")
    rid_a1, a1 = h.job()
    rid_a2, a2 = h.job()
    rid_v, v = h.job(ident=_ident("bob", uid=10002))
    for i, jid in enumerate((a1, a2, v)):
        h.set_updated_at(jid, f"2000-01-01T00:00:0{i}Z")
    h.tick()
    assert h.res.names() == ["alice", "bob"]                     # alice 는 캐시(재호출 없음), 서킷은 닫힌 채
    assert [e["payload"]["attempt"] for e in h.events(rid_a1, "identity_recheck_deferred")] == [1]
    assert [e["payload"]["attempt"] for e in h.events(rid_a2, "identity_recheck_deferred")] == [1]
    assert h.state(v) == "Preflight"


# ---- 틱 예산 ----

def test_tick_budget_skips_resolve_after_budget(db):
    h = _H(db, cost=LDAP_TICK_BUDGET_SECONDS + 1)               # resolve 한 번이 예산을 다 쓴다(느린 LDAP)
    _rid_a, a = h.job()
    rid_b, b = h.job(ident=_ident("bob", uid=10002))
    h.set_updated_at(a, "2000-01-01T00:00:00Z")
    h.set_updated_at(b, "2000-01-01T00:00:01Z")
    h.tick()
    assert h.state(a) == "Preflight"
    assert h.res.names() == ["alice"]                            # bob 은 예산 보류 -- LDAP 미호출
    assert h.state(b) == "Pending" and h.events(rid_b) == []     # 미계수·무이벤트


def test_deadline_passed_to_resolver(db):
    h = _H(db, cost=3.0)
    _rid_a, a = h.job()
    _rid_b, b = h.job(ident=_ident("bob", uid=10002))
    h.set_updated_at(a, "2000-01-01T00:00:00Z")
    h.set_updated_at(b, "2000-01-01T00:00:01Z")
    start = h.mono.t
    h.tick()
    # 첫 호출은 남은 몫 = 예산 = 자체 마감이라 deadline 없음(identity.tick_resolve_deadline -- 그 마감의 중단은 진짜
    # 판정), 3s 쓴 뒤 두 번째는 남은 몫 7s 가 묶는다: now(start+3) + 7 = start+10.
    assert [d for _u, d in h.res.calls] == [None, start + LDAP_TICK_BUDGET_SECONDS]


def test_tick_cache_one_resolve_per_user(db):
    h = _H(db)
    jobs = [h.job()[1] for _ in range(3)]
    h.tick()
    assert h.res.names() == ["alice"]
    assert all(h.state(j) == "Preflight" for j in jobs)


def test_all_four_submit_paths_recheck(db):
    h = _H(db)
    rid, jid = h.job(op="sync")
    h.to_executing(jid)
    h.tick()                                                     # exec_preflight 제출
    h.tick()                                                     # exec_preflight ok → execution 제출
    assert h.state(jid) == "Executing"
    assert [e["payload"]["phase"] for e in h.events(rid, "identity_groups_checked")] == [
        "preflight", "preview", "exec_preflight", "execution"]
    assert all(s.identity["supplementary_gids"] == [10010] for s in h.adapter.submitted_specs())


def test_event_messages_have_no_ldap_detail(db):
    # 요청 이벤트는 비관리자 요청자에게도 반환된다 -- LDAP URI·소켓 오류는 로그에만.
    h = _H(db)
    rid, jid = h.job()
    h.res.down = True                                            # 전송 장애 4번 → 종단
    for _ in range(4):
        h.tick()
        h.clock.advance(61)
    h.res.down = False
    rid2, jid2 = h.job(ident=_ident("bob", uid=10002))
    h.res.invalid.add("bob")                                     # 사용자별 조회 오류 4번 → 종단
    for _ in range(4):
        h.tick()
        h.clock.advance(61)
    assert h.reason(jid) == "ldap_unavailable" and h.reason(jid2) == "ldap_unavailable"
    assert len(h.events(rid, "identity_recheck_deferred")) == 3
    assert len(h.events(rid2, "identity_recheck_deferred")) == 3
    rows = h.db.query("SELECT message, payload FROM events")
    assert rows and not any(_SECRET in (r["message"] or "") or _SECRET in (r["payload"] or "") for r in rows)
    assert not any("secret-host" in (r["message"] or "") + (r["payload"] or "") for r in rows)


# ---- 큐 대기(vcjob PENDING) 재확인 -- 2패스 ----

def _scan_running_pending(h, ident=None):
    rid, jid = h.job(ident=ident)
    h.adapter.script(f"stub-execution-{jid}", [ExecStatus.PENDING] * 20)
    h.tick()                                                     # Pending → Preflight
    h.tick()                                                     # Preflight ok → Running(execution 제출)
    assert h.state(jid) == "Running"
    return rid, jid


@pytest.mark.parametrize("row", ["preview", "sync_execution", "scan_execution"])
def test_queued_shrink_terminates_in_vcjob_pending_branches(db, row):
    h = _H(db)
    if row == "scan_execution":
        rid, jid = _scan_running_pending(h)
        phase, want = "execution", "Failed"
    elif row == "preview":
        rid, jid = h.job(op="sync")
        h.adapter.script(f"stub-preview-{jid}", [ExecStatus.PENDING] * 20)
        h.tick()
        h.tick()
        assert h.state(jid) == "PreviewRunning"
        phase, want = "preview", "Rejected"
    else:
        rid, jid = h.job(op="sync")
        h.to_executing(jid)
        h.adapter.script(f"stub-execution-{jid}", [ExecStatus.PENDING] * 20)
        h.tick()                                                 # exec_preflight 제출
        h.tick()                                                 # execution 제출
        assert h.state(jid) == "Executing"
        phase, want = "execution", "Failed"
    specs = len(h.adapter.submitted_specs())
    h.shrink()
    h.tick()
    assert (h.state(jid), h.reason(jid)) == (want, "identity_changed_at_step")
    assert len(h.adapter.submitted_specs()) == specs
    (ev,) = h.events(rid, "identity_changed_at_step")
    assert ev["payload"]["queued"] is True and ev["payload"]["phase"] == phase
    assert ev["payload"]["missing_gids"] == [10010]
    assert f"stub-{phase}-{jid}" in h.adapter.terminated


def test_queued_ldap_outage_is_noop(db):
    h = _H(db)
    rid, jid = _scan_running_pending(h)
    events = len(h.events(rid))
    h.res.down = True
    assert h.tick()[jid] == "Running"
    assert h.state(jid) == "Running" and len(h.events(rid)) == events   # 미계수·무이벤트(제출 때 확인됨)


@pytest.mark.parametrize("forged", [0, False, "", {}, [10010, 10010]])
def test_queued_shape_forgery_terminates_immediately(db, forged):
    # null ≠ 0: falsy 비리스트를 '없음'으로 뭉개지 않는다 -- 제출 뒤 변조도 도구 시작 전이라 즉시 종단.
    h = _H(db)
    rid, jid = _scan_running_pending(h)
    calls = len(h.res.calls)
    h.forge_identity(jid, supplementary_gids=forged)
    assert h.tick()[jid] == "Failed"
    assert h.reason(jid) == "identity_missing_at_step"
    assert len(h.res.calls) == calls


def test_submit_attempt_runs_before_queued_recheck_in_tick(db):
    h = _H(db)
    rid_a, a = _scan_running_pending(h)                          # 큐 대기 잡(alice)
    rid_b, b = h.job(ident=_ident("bob", uid=10002))             # 제출 잡(bob)
    h.set_updated_at(a, "2000-01-01T00:00:00Z")                  # 폴링 잡이 claim 앞쪽
    h.set_updated_at(b, "2000-01-01T00:00:01Z")
    events_a = len(h.events(rid_a))
    calls = len(h.res.calls)
    h.res.down = True
    h.tick()
    assert h.res.names()[calls:] == ["bob"]                      # 틱의 첫(유일한) LDAP 사용은 계수되는 제출 시도
    assert [e["payload"]["attempt"] for e in h.events(rid_b, "identity_recheck_deferred")] == [1]
    assert len(h.events(rid_a)) == events_a


def test_queued_recheck_skipped_after_submit_opened_circuit(db):
    h = _H(db)
    _rid_a, a = _scan_running_pending(h)
    _rid_b, b = h.job(ident=_ident("bob", uid=10002))
    h.shrink("alice")                                            # 재확인됐다면 a 는 종단됐을 것이다
    h.res.down_users.add("bob")                                  # 제출(bob)의 장애가 그 틱의 서킷을 연다
    calls = len(h.res.calls)
    h.set_updated_at(a, "2000-01-01T00:00:00Z")
    h.set_updated_at(b, "2000-01-01T00:00:01Z")
    h.tick()
    assert h.res.names()[calls:] == ["bob"]                      # 서킷이 열려 2패스는 LDAP 를 부르지 않는다
    assert h.state(a) == "Running"                               # 건너뜀(미계수) -- 다음 틱에 다시 본다
    h.res.down_users.clear()
    h.tick()
    assert (h.state(a), h.reason(a)) == ("Failed", "identity_changed_at_step")


def test_queued_recheck_uses_submit_cache(db):
    h = _H(db)
    _rid_a, a = _scan_running_pending(h)
    _rid_b, b = h.job()                                          # 같은 사용자의 제출 잡
    calls = len(h.res.calls)
    h.tick()
    assert h.res.names()[calls:] == ["alice"]                    # 제출이 받은 최신값을 2패스가 재사용
    assert h.state(a) == "Running" and h.state(b) == "Preflight"


def test_pod_stage_pending_has_no_queued_recheck(db):
    # 파드 단계(preflight·exec_preflight, nsync 복합 ref 진행 중)는 바로 뒤 제출 재확인이 덮는다 -- LDAP 부하만 는다.
    h = _H(db)
    _r1, pf = h.job()
    h.adapter.script(f"stub-preflight-{pf}", [ExecStatus.PENDING] * 5)
    _r2, ep = h.job(op="sync")
    h.to_executing(ep)
    h.adapter.script(f"stub-exec_preflight-{ep}", [ExecStatus.PENDING] * 5)
    _r3, ns = h.job(op="sync")
    h.tick()                                                     # pf: preflight 제출, ep: exec_preflight 제출, ns: preflight 제출
    h.repos.data_jobs.set_phase_ref(ns, "preflight", "pods/a,b")
    h.adapter.script("pods/a,b", [ExecStatus.RUNNING] * 5)
    assert (h.state(pf), h.state(ep), h.state(ns)) == ("Preflight", "Executing", "Preflight")
    calls = len(h.res.calls)
    h.shrink()
    h.tick()
    assert len(h.res.calls) == calls
    assert (h.state(pf), h.state(ep), h.state(ns)) == ("Preflight", "Executing", "Preflight")


def test_running_not_rechecked(db):
    # RUNNING 은 프로세스 자격이 이미 고정이다(노드 재검사와 같은 원칙) -- 재확인하지 않는다.
    h = _H(db)
    rid, jid = h.job()
    h.adapter.script(f"stub-execution-{jid}", [ExecStatus.RUNNING] * 5)
    h.tick()
    h.tick()
    calls = len(h.res.calls)
    h.shrink()
    assert h.tick()[jid] == "Running"
    assert len(h.res.calls) == calls


def test_queued_recheck_error_is_isolated_per_job(db):
    h = _H(db, {"alice": _fresh(), "bob": _fresh("bob", uid=10002)})
    rid_a, a = _scan_running_pending(h)
    rid_b, b = _scan_running_pending(h, ident=_ident("bob", uid=10002))
    h.set_updated_at(a, "2000-01-01T00:00:00Z")
    h.set_updated_at(b, "2000-01-01T00:00:01Z")
    h.res.boom.add("alice")
    h.shrink("bob")
    results = h.tick()
    assert results[a] == "error:RuntimeError"
    assert [e["event_type"] for e in h.events(rid_a)][-1] == "step_error"
    assert (h.state(a), h.state(b)) == ("Running", "Failed")    # 한 잡의 오류가 다음 잡을 막지 않는다
    assert h.reason(b) == "identity_changed_at_step"


# ---- 보류 중 사라진 Succeeded 파드 ----

def test_held_job_with_vanished_succeeded_pod_in_preflight(db):
    h = _H(db)
    rid, jid = h.job(op="sync")
    h.tick()                                                     # Pending → Preflight(통과)
    h.res.down = True
    assert h.tick()[jid] == "Preflight"                          # preview 제출 직전 보류(attempt 1)
    h.adapter.script(f"stub-preflight-{jid}", [ExecStatus.FAILED])   # 수동 삭제·terminated-pod GC
    assert h.tick()[jid] == "Rejected"
    assert h.reason(jid) == "ldap_unavailable"                   # preflight_failed 로 오표시하지 않는다
    (ev,) = h.events(rid, "identity_recheck_failed")
    assert ev["payload"] == {"job_id": jid, "phase": "preflight", "reason": "held_pod_vanished"}


def test_held_job_with_vanished_succeeded_pod_in_executing(db):
    h = _H(db)
    rid, jid = h.job(op="sync")
    h.to_executing(jid)
    h.tick()                                                     # exec_preflight 제출(통과)
    h.res.down = True
    h.clock.advance(1)
    assert h.tick()[jid] == "Executing"                          # execution 제출 직전 보류
    h.adapter.script(f"stub-exec_preflight-{jid}", [ExecStatus.FAILED])
    assert h.tick()[jid] == "Rejected"
    assert h.reason(jid) == "ldap_unavailable"
    (ev,) = h.events(rid, "identity_recheck_failed")
    assert ev["payload"]["reason"] == "held_pod_vanished" and ev["payload"]["phase"] == "exec_preflight"


def test_genuine_preflight_failure_after_pass_keeps_fallback(db):
    h = _H(db)
    rid, jid = h.job()
    h.res.down = True
    h.tick()                                                     # 제출 직전 보류(파드 없음, streak 1)
    h.res.down = False
    h.clock.advance(61)
    h.tick()                                                     # 통과(리셋) → preflight 제출
    h.adapter.script(f"stub-preflight-{jid}", [ExecStatus.FAILED])
    assert h.tick()[jid] == "Rejected"
    assert h.reason(jid) == "preflight_failed"
    assert h.events(rid, "identity_recheck_failed") == []


# ---- 2026-10-08 리뷰 G1/G3: 틱 예산의 남은 몫이 멈춘 resolve 는 미계수 보류 -- 실 LdapIdentityResolver + connect_first ----
# 예전 _CountingResolver 는 deadline 을 무시해, 예산 경계에 걸친 resolve 가 plain IdentityUnavailable 로 D2 계수되던
# 경로(LDAP 가 멀쩡해도 4번이면 ldap_unavailable 종단)를 보지 못했다.

class _LV:
    def __init__(self, v):
        self.value = v


class _LEntry:
    def __init__(self, dn, attrs):
        self.entry_dn, self._a = dn, attrs

    def __getitem__(self, k):
        return _LV(self._a[k])


class _LConn:
    """건강한 LDAP: 검색마다 monotonic 을 cost 초 전진. 사용자는 필터의 uid 로 찾는다(uid 표 uids)."""
    def __init__(self, mono, cost, uids):
        self.mono, self.cost, self.uids = mono, cost, uids
        self.entries, self.result = [], {"result": 0}

    def search(self, base, flt, attributes=None, paged_size=None, paged_cookie=None):
        self.mono.t += self.cost
        name = flt.split("=", 1)[1].rstrip(")")
        if flt.startswith("(uid="):
            self.entries = ([_LEntry(f"uid={name},ou=p", {"uidNumber": self.uids[name], "gidNumber": 10000})]
                            if name in self.uids else [])
        else:
            self.entries = [_LEntry("cn=dmsproj,ou=g", {"cn": "dmsproj", "gidNumber": 10010,
                                                         "objectClass": ["posixGroup"]})]
        self.result = {"result": 0}

    def unbind(self):
        pass


class _RealLdap:
    """실 LdapIdentityResolver(+ 실 connect_first, 가짜 monotonic)를 감싸 호출을 센다(_CountingResolver.names 호환)."""
    def __init__(self, mono, uids, *, uris=("ldap://b",), dead=(), connect_cost=0.05, search_cost=0.05,
                 dead_cost=5.0, rotation=None, refuse=False):
        from dms.identity_ldap import LdapIdentityResolver, connect_first
        self.calls, self.tried = [], []

        def open_one(uri):
            self.tried.append(uri)
            if refuse:
                raise ConnectionRefusedError(f"{_SECRET} [Errno 111] Connection refused")
            if uri in dead:
                mono.t += dead_cost
                raise OSError("timed out")
            mono.t += connect_cost
            return _LConn(mono, search_cost, uids)
        self._real = LdapIdentityResolver(
            connect=lambda deadline=None: connect_first(list(uris), open_one, deadline=deadline, monotonic=mono,
                                                        rotation=rotation),
            user_base="ou=p", group_base="ou=g", group_member_attr="memberUid", monotonic=mono)

    def resolve(self, username, *, deadline=None):
        self.calls.append((username, deadline))
        return self._real.resolve(username, deadline=deadline)

    def names(self):
        return [u for u, _d in self.calls]


def _many(h, n):
    uids = {f"u{i}": 20000 + i for i in range(n)}
    jobs = []
    for i, name in enumerate(uids):
        rid, jid = h.job(ident=_ident(name, uid=uids[name]))
        h.set_updated_at(jid, f"2000-01-01T00:00:{i:02d}Z")
        jobs.append((rid, jid))
    return uids, jobs


def test_budget_straddle_is_uncounted_hold_on_healthy_ldap(db):
    # G1: 11 사용자, resolve 1.1s(연결 0.3 + 검색 0.4 × 2), 전부 성공할 LDAP. 9건 뒤 10번째는 남은 몫 0.1s 로 시작해
    # 그 마감에 걸린다 -- 예전엔 identity_recheck_deferred 1/4(계수)·서킷 개방, 이제 미계수 예산 보류.
    h = _H(db, resolver=None)
    uids, jobs = _many(h, 11)
    h.res = _RealLdap(h.mono, uids, connect_cost=0.3, search_cost=0.4)
    h.tick()
    assert [h.state(j) for _r, j in jobs[:9]] == ["Preflight"] * 9
    assert [h.state(j) for _r, j in jobs[9:]] == ["Pending"] * 2
    assert all(h.events(r, "identity_recheck_deferred") == [] for r, _j in jobs)   # 아무도 계수되지 않았다
    assert len(h.res.calls) == 10                              # 11번째는 예산 게이트(ldap_budget) -- LDAP 미호출
    _d, deadline = h.res.calls[9]
    assert deadline is not None                                # 10번째만 남은 몫이 묶었다(첫 호출은 None)
    assert h.res.calls[0][1] is None
    # 같은 잡이 경계에 몇 번 걸려도 계수되지 않으니 종단되지 않는다 -- 다음 틱엔 먼저 클레임돼 통과한다.
    for _r, j in jobs[:9]:
        h.set_updated_at(j, "2099-01-01T00:00:00Z")
    h.clock.advance(61)
    h.tick()
    assert [h.state(j) for _r, j in jobs[9:]] == ["Preflight"] * 2


def test_first_resolve_outage_with_real_resolver_still_counts(db):
    # 진행 보장의 반대쪽: 틱 첫 resolve 의 전송 장애는 여전히 D2 계수 실패(서킷 개방)다.
    h = _H(db, resolver=None)
    uids, jobs = _many(h, 2)
    h.res = _RealLdap(h.mono, uids, refuse=True)
    h.tick()
    (rid_a, a), (rid_b, b) = jobs
    assert [e["payload"]["attempt"] for e in h.events(rid_a, "identity_recheck_deferred")] == [1]
    assert h.events(rid_b) == [] and h.res.names() == ["u0"]  # 서킷 -- 두 번째는 미호출·미계수


def test_dead_primary_uri_with_sticky_start_never_holds(db):
    # G4 형상(stepper): p1 타임아웃형 장애(5s), r3 정상, 시작 URI 기억 -- 첫 resolve 가 r3 에 붙은 뒤로 p1 을 다시
    # 기다리지 않아 같은 틱의 잡이 전부 통과한다.
    h = _H(db, resolver=None)
    uids, jobs = _many(h, 4)
    h.res = _RealLdap(h.mono, uids, uris=("ldap://p1", "ldap://r3"), dead={"ldap://p1"}, rotation={"start": 0})
    h.tick()
    assert [h.state(j) for _r, j in jobs] == ["Preflight"] * 4
    assert h.res.tried == ["ldap://p1", "ldap://r3", "ldap://r3", "ldap://r3", "ldap://r3"]


def test_dead_primary_without_sticky_start_is_uncounted_budget_hold(db):
    # G3 (c): 기억 없이 매 resolve 가 p1 부터면 두 번째 잡은 남은 몫(~4.8s)이 p1 의 5s 에 먼저 끝나 r3 를 못 가 본다
    # -- 호출자 마감의 중단 = 예산 보류(미계수·무이벤트), 계수 실패가 아니다.
    h = _H(db, resolver=None)
    uids, jobs = _many(h, 3)
    h.res = _RealLdap(h.mono, uids, uris=("ldap://p1", "ldap://r3"), dead={"ldap://p1"})
    h.tick()
    assert [h.state(j) for _r, j in jobs] == ["Preflight", "Pending", "Pending"]
    assert all(h.events(r, "identity_recheck_deferred") == [] for r, _j in jobs)
    assert h.res.names() == ["u0", "u1"]                       # 세 번째는 예산 게이트 -- LDAP 미호출


def test_deadline_exceeded_from_resolver_is_not_cached(db):
    # 예산 보류는 캐시하지 않는다 -- 같은 틱 같은 사용자의 다음 잡도 예산 게이트로 보류될 뿐 '실패' 로 계수되지 않는다.
    from dms.identity import IdentityDeadlineExceeded

    class _Cut:
        def __init__(self):
            self.calls = []

        def resolve(self, username, *, deadline=None):
            self.calls.append(username)
            raise IdentityDeadlineExceeded("deadline exceeded")
    h = _H(db, resolver=None)
    h.res = _Cut()
    rid_a, a = h.job()
    rid_b, b = h.job()
    h.set_updated_at(a, "2000-01-01T00:00:00Z")
    h.set_updated_at(b, "2000-01-01T00:00:01Z")
    h.tick()
    assert h.res.calls == ["alice"]                            # b 는 예산 게이트(ldap_budget) -- 재호출 없음
    assert h.state(a) == "Pending" and h.state(b) == "Pending"
    assert h.events(rid_a) == [] and h.events(rid_b) == []


# ---- 2026-10-08 리뷰 G2: 그룹 잡의 artifact base 정적 관문(stepper._build_spec) ----

import errno as _errno  # noqa: E402
import os as _os  # noqa: E402


def _base_at(h, path):
    h.repos.control.set_artifact_base(f"file://{path}", actor="test")


def _fake_default_acl(monkeypatch, path):
    real = _os.getxattr

    def fake(p, name, *a, **k):
        if str(p) != str(path):
            return real(p, name, *a, **k)
        if name == "system.posix_acl_default":
            return b"\x02\x00\x00\x00"                    # 있다는 사실만으로 거부(내용 무관)
        raise OSError(_errno.ENODATA, "no data")
    monkeypatch.setattr(_os, "getxattr", fake)


def test_group_job_blocked_when_base_has_default_acl(db, artifact_base_dir, monkeypatch):
    # base 755(preflight test -w 는 통과) + default ACL 만 -- 러너가 만드는 <job_id>/<phase> 가 상속해 그 그룹의 다른
    # 사용자가 rank.sh 를 바꿔치기할 수 있었다. 컨트롤러가 제출 전에 끊는다(LDAP 재확인보다 먼저 -- 싼 검사).
    h = _H(db)
    _base_at(h, artifact_base_dir)
    _fake_default_acl(monkeypatch, artifact_base_dir)
    rid, jid = h.job()
    assert h.tick()[jid] == "Rejected"
    assert h.reason(jid) == "artifact_base_group_writable"
    (ev,) = h.events(rid, "artifact_base_unsafe_at_step")
    assert ev["payload"] == {"job_id": jid, "problem": "artifact_base_group_writable"}
    assert str(artifact_base_dir) not in ev["message"]          # 관리자 설정 경로는 싣지 않는다
    assert h.res.calls == [] and not h.repos.data_jobs.get_job(jid)["phase_refs"]   # 제출 0건


@pytest.mark.parametrize("mode, reason", [(0o775, "artifact_base_group_writable"),
                                          (0o750, "artifact_base_not_traversable")])
def test_group_job_blocked_on_base_mode(db, artifact_base_dir, mode, reason):
    h = _H(db)
    _base_at(h, artifact_base_dir)
    artifact_base_dir.chmod(mode)
    _rid, jid = h.job()
    h.tick()
    assert (h.state(jid), h.reason(jid)) == ("Rejected", reason)


def test_group_job_on_clean_base_proceeds(db, artifact_base_dir):
    h = _H(db)
    _base_at(h, artifact_base_dir)                              # 0755, ACL 없음
    _rid, jid = h.job()
    h.tick()
    assert h.state(jid) == "Preflight"


def test_job_without_groups_skips_the_static_gate(db, artifact_base_dir):
    # 그룹이 없는 잡은 종전 그대로(기존 흐름 무변경) -- 그 잡의 쓰기 범위는 주 gid 뿐이고 preflight 가 본다.
    h = _H(db)
    _base_at(h, artifact_base_dir)
    artifact_base_dir.chmod(0o775)
    _rid, jid = h.job(ident=_ident(gids=(), status="none"))
    h.tick()
    assert h.state(jid) == "Preflight"


def test_unreadable_base_defers_to_preflight(db, tmp_path):
    # 컨트롤러가 base 를 stat 하지 못하면(마운트 없음 등) 통과로 치지도, 종단하지도 않는다 -- preflight 에 맡긴다.
    h = _H(db)
    _base_at(h, tmp_path / "missing")
    rid, jid = h.job()
    h.tick()
    assert h.state(jid) == "Preflight"
    assert h.events(rid, "artifact_base_unsafe_at_step") == []
