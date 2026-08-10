"""planner: Pending 요청을 어드미션 게이트를 거쳐 계획된 data_job으로 emit하는 루프 본체."""
import sys
from dataclasses import asdict

from .db import iso_plus, utc_now_iso
from .domain import Operation, RequestState
from .identity import IdentityRejected, resolve_job_identity
from .placement import (
    PlacementError, TOOL_TO_POLICY, resolve_fanout, select_tool_and_candidates)


def _required_storages(operation, payload):
    if operation == Operation.SYNC.value:
        return [payload["source_storage"], payload["destination_storage"]]
    return [payload["storage"]]


class Planner:
    def __init__(self, repos, resolver, *, settings):
        self._repos = repos
        self._resolver = resolver
        self._settings = settings

    def run_once(self, limit: int = 50, *, now_iso=None) -> dict:
        pending = self._repos.requests.list_pending(limit)
        results = {}
        for row in pending:
            rid = row["request_id"]
            try:
                results[rid] = self._plan_one(rid, now_iso)
            except Exception as exc:  # 한 요청 실패가 다음을 막지 않는다
                print(f"planner error on {rid}: {type(exc).__name__}: {exc}",
                      file=sys.stderr)
                # 이 실패는 전이를 남기지 않는다(예외가 어느 set_state 호출보다도 먼저
                # 터질 수 있다) -- stderr만으로는 파드 재시작에 사라지므로 events에 남긴다.
                self._repos.observability.record_event(
                    component="planner", severity="error", event_type="plan_error",
                    message=f"{type(exc).__name__}: {exc}"[:500], request_id=rid)
        return results

    def _reject(self, rid, reason):
        self._repos.requests.set_state(rid, RequestState.REJECTED,
                                       reason_code=reason, actor="planner")
        self._repos.requests.record_result(rid, RequestState.REJECTED,
                                            reason_code=reason)
        return f"rejected:{reason}"

    def _identity_grace_active(self, req, exc, now_iso):
        """설계 §2.3의 3중 조건: (a) 사유가 no_eligible_nodes, (b) 모든 노드의 탈락
        사유가 identity_not_ready_on_node, (c) 요청 나이 < grace. 셋 다 참일 때만
        유예한다. rejections 가 비면(신선한 리포트 0건) 신원 문제라는 증거가 없다 --
        유예하지 않는다. grace 를 짧게(기본 300s -- 최악 전파 130s 의 2배 남짓) 두는
        이유: 같은 resource_key 의 후속 요청이 find_active 에 걸려 Conflict 가 되므로
        무한정 붙잡으면 안 된다."""
        if exc.reason_code != "no_eligible_nodes" or not exc.rejections:
            return False
        if any(r != "identity_not_ready_on_node" for r in exc.rejections.values()):
            return False
        now = now_iso or utc_now_iso()
        return now < iso_plus(req["created_at"],
                              self._settings.planner_identity_grace_seconds)

    def _plan_one(self, rid, now_iso):
        req = self._repos.requests.get(rid)
        # 멱등: 이미 emit된 잡이 있으면(크래시 복구) 상태만 정리
        if self._repos.data_jobs.list_jobs(request_id=rid):
            if req["state"] != "Planned":
                self._repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
            return "planned"
        payload = req["payload"]
        # 1. conflict: 앞선 비터미널 동일 resource_key
        prior = self._repos.requests.find_active(req["resource_key"])
        if prior is not None and prior["commit_order"] < req["commit_order"]:
            self._repos.requests.set_state(rid, RequestState.CONFLICT,
                                           reason_code="resource_conflict",
                                           actor="planner")
            self._repos.requests.record_result(rid, RequestState.CONFLICT,
                                                reason_code="resource_conflict")
            return "conflict"
        # 2. requester 계정 admission: 계정 row가 존재 & disabled일 때만 거부한다.
        #    row가 없으면 "무판정" — 포탈 계정이 없는 LDAP 신원의 잡은 그대로 통과.
        account = self._repos.accounts.get(req["requester_id"])
        if account is not None and account["disabled"]:
            return self._reject(rid, "requester_disabled")
        # 3. storage admission
        for name in _required_storages(req["operation"], payload):
            storage = self._repos.storages.get(name)
            if storage is None:
                return self._reject(rid, "storage_missing")
            if not storage["enabled"]:
                return self._reject(rid, "storage_disabled")
            if storage["status"] not in ("Ready", "Degraded"):
                return self._reject(rid, "storage_not_ready")
        # 4. identity
        try:
            identity = resolve_job_identity(
                self._repos.control, self._resolver,
                requester_id=req["requester_id"],
                owner_username=payload.get("owner_username"),
                allow_privileged=self._settings.allow_privileged_requesters,
                privileged_requesters=self._settings.privileged_requesters)
        except IdentityRejected as exc:
            return self._reject(rid, exc.reason_code)
        # 5. tool + candidates
        fresh = self._repos.agents.fresh_reports(
            stale_seconds=self._settings.agent_report_stale_seconds, now_iso=now_iso)
        try:
            placement = select_tool_and_candidates(
                req["operation"], fresh, storage_name=payload.get("storage"),
                source_storage=payload.get("source_storage"),
                destination_storage=payload.get("destination_storage"),
                owner=identity.username, privileged=identity.privileged)
        except PlacementError as exc:
            # 신원 전파만이 원인이고 grace 안이면 아무 상태도 바꾸지 않는다 -- 요청은
            # Pending 으로 남아 다음 틱(list_pending)에 재계획된다(설계 §2.3).
            if self._identity_grace_active(req, exc, now_iso):
                # 유예는 관측 가능해야 한다 -- 매 유예마다 이벤트. record_event 는
                # 절대 예외를 올리지 않으므로(observability 계약) 유예 자체는 안전하다.
                self._repos.observability.record_event(
                    component="planner", severity="info",
                    event_type="identity_propagating",
                    message=("identity not ready on: "
                             + ", ".join(sorted(exc.rejections)))[:500],
                    payload={"rejections": exc.rejections}, request_id=rid)
                return "deferred:identity_propagating"
            return self._reject(rid, exc.reason_code)
        # 6. policy fan-out
        policy = self._repos.control.get_policy(TOOL_TO_POLICY[placement["tool"]])
        try:
            fanout = resolve_fanout(policy, placement["candidates"],
                                    priority=req["priority"])
        except PlacementError as exc:
            return self._reject(rid, exc.reason_code)
        # 7. emit
        identity_dict = {**asdict(identity), "groups": list(identity.groups)}
        cand = placement["candidates"]
        if "primary" in cand:
            cand = {"primary": cand["primary"][:fanout["node_count"]]}
        else:
            cand = {"source": cand["source"][:fanout["source_count"]],
                    "destination": cand["destination"][:fanout["destination_count"]]}
        worker_pool = {"tool": placement["tool"], "identity": identity_dict,
                       "candidates": cand,
                       "rejections": placement["rejections"], **fanout}
        precondition = {"requester_id": req["requester_id"],
                        "owner": identity.username, "operation": req["operation"]}
        plan_id = self._repos.data_jobs.create_plan(rid, actor="planner")
        self._repos.data_jobs.create_job(
            rid, plan_id, operation=req["operation"], priority=req["priority"],
            storage_name=payload.get("storage"),
            source_storage=payload.get("source_storage"),
            destination_storage=payload.get("destination_storage"),
            source=payload.get("source"), destination=payload.get("destination"),
            target=payload.get("target"), options=payload.get("options", {}),
            tool=placement["tool"], worker_pool=worker_pool,
            precondition=precondition, actor="planner")
        self._repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
        return "planned"
