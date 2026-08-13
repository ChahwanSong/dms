"""planner: Pending 요청을 어드미션 게이트를 거쳐 계획된 data_job으로 emit하는 루프 본체."""
import sys
from dataclasses import asdict

from .db import iso_plus, utc_now_iso
from .domain import Operation, RequestState
from .identity import IdentityRejected, resolve_job_identity
from .placement import (
    PlacementError, TOOL_TO_POLICY, resolve_fanout, select_tool_and_candidates)


_IDENTITY_PENDING = "identity_not_ready_on_node"
# 유예를 검토할 사유 -- "후보 0"을 뜻하는 두 코드뿐이다. missing_policy·policy_disabled·
# invalid_operation 은 신원과 무관하고 노드별 사유 자체가 없다(설계 §2.3).
_GRACE_REASONS = ("no_eligible_nodes", "no_ready_sync_candidate")


def _identity_pending_nodes(rejections) -> set:
    """사유가 정확히 identity_not_ready_on_node 인 노드 이름들.

    "하나라도 있으면 유예"로 충분한 근거는 eligible_nodes 의 구조에 있다: 노드마다
    **첫 실패 사유 하나**만 기록하고 검사 순서가 mount -> writable -> tool ->
    **identity(마지막)** 다. 즉 사유가 identity 인 노드는 마운트·쓰기·도구를 이미
    통과했고 신원 전파만 남았다 -- 전파되면 그 노드는 반드시 적격이 된다. "모든 노드"를
    요구하면 실 테스트베드 형상(일부 노드는 애초에 미마운트)에서 유예가 아예 발동하지
    않는다.

    rejections shape 는 둘이다 -- scan/rm 은 flat {node: reason}, sync 는
    {"source": {...}, "destination": {...}} 중첩이라 두 쪽을 합집합으로 본다.
    받아들이는 트레이드오프: source 에만 신원 대기 노드가 있고 destination 이 전부
    미마운트면 전파돼도 끝내 실패하지만, grace 만큼 Pending 했다가 거부된다 -- 영구
    오거부보다 낫고 스스로 수렴한다. 값이 문자열도 dict 도 아닌 미지의 형태면 어느
    비교에도 걸리지 않아 자연히 "증거 없음"(=즉시 거부)이 된다.
    """
    nodes = set()
    for key, value in rejections.items():
        if isinstance(value, dict):          # sync: source/destination 중첩
            nodes |= {n for n, r in value.items() if r == _IDENTITY_PENDING}
        elif value == _IDENTITY_PENDING:
            nodes.add(key)
    return nodes


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
        # 슬라이스 30: set_state + record_result 별도 커밋(비원자 쌍)을 레포의
        # 원자 메서드로 -- 사이 크래시가 만들던 "Rejected 인데 results 없음"(고아
        # 스윕 시야 밖의 영구 결손)이 전부-또는-전무가 된다. finalize(슬라이스
        # 27, 실증 5/5)와 동일 처방.
        self._repos.requests.set_state_with_result(
            rid, RequestState.REJECTED, reason_code=reason, actor="planner")
        return f"rejected:{reason}"

    def _within_identity_grace(self, req, now_iso):
        """신원 전파 유예 창 판정. 앵커는 요청 created_at -- placement 실패 경로
        (_identity_grace_active)와 성공 경로의 요청 수 대기(A')가 같은 창을 쓴다.

        grace 를 짧게(기본 300s -- 최악 전파 130s 의 2배 남짓) 두는 이유: 같은
        resource_key 의 후속 요청이 find_active 에 걸려 Conflict 가 되므로 무한정
        붙잡으면 안 된다."""
        now = now_iso or utc_now_iso()
        return now < iso_plus(req["created_at"],
                              self._settings.planner_identity_grace_seconds)

    def _identity_grace_active(self, req, exc, now_iso):
        """설계 §2.3의 유예 조건: (a) 사유가 "후보 0"이고 (b) 탈락 사유가 정확히
        identity_not_ready_on_node 인 노드가 **하나라도** 있고 (c) 요청 나이 < grace.
        방향은 "증명되면 유예, 아니면 거부"다 -- rejections 가 비었거나(신선한 리포트
        0건) 신원 대기 노드가 없으면 유예하지 않는다. 알 수 없는 사유에 유예를 걸면
        진짜 결격이 조용히 매달린다."""
        if exc.reason_code not in _GRACE_REASONS:
            return False
        if not _identity_pending_nodes(exc.rejections):
            return False
        return self._within_identity_grace(req, now_iso)

    def _record_defer_event(self, rid, event_type, message, payload):
        """유예는 관측 가능해야 하지만, 틱마다(기본 10s) 남기면 grace 300s 동안 요청
        하나에 최대 30건이 쌓여 요청 상세의 이벤트 목록(limit 100)을 유예 잡음으로
        덮는다. 사유가 바뀔 때만(첫 유예 포함) 남긴다 -- 같은 사유의 연속 유예는
        새 정보가 없다. identity_propagating 과 awaiting_requested_nodes 가 같은
        관례를 공유한다(억제는 event_type 별로 따로 본다)."""
        try:
            prior = [e for e in self._repos.observability.events_for_request(rid)
                     if e["event_type"] == event_type]
        except Exception:
            # 중복 억제는 진단 편의일 뿐이다 -- 조회가 실패했다고 유예 자체를 깨지
            # 않는다(record_event 가 절대 예외를 올리지 않는 것과 같은 이유).
            prior = []
        if prior and prior[-1]["payload"] == payload:
            return
        self._repos.observability.record_event(
            component="planner", severity="info", event_type=event_type,
            message=message[:500], payload=payload, request_id=rid)

    def _record_identity_defer(self, rid, exc):
        nodes = ", ".join(sorted(_identity_pending_nodes(exc.rejections)))
        self._record_defer_event(
            rid, "identity_propagating", f"identity not ready on: {nodes}",
            {"rejections": exc.rejections})

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
            # 슬라이스 30: _reject 와 같은 원자화(비원자 쌍의 두 번째).
            self._repos.requests.set_state_with_result(
                rid, RequestState.CONFLICT, reason_code="resource_conflict",
                actor="planner")
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
                privileged_requesters=self._settings.privileged_requesters,
                # 요청을 만든 인증 방식. token(또는 컬럼 미채움/NULL)은 특권을 못
                # 얻는다 -- 기배포 DB 의 구형 행은 NULL 이라 자동으로 비특권이다.
                session_authenticated=(req.get("auth_method") == "session"))
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
            # 신원 전파를 기다리면 적격이 될 노드가 있고 grace 안이면 아무 상태도
            # 바꾸지 않는다 -- 요청은 Pending 으로 남아 다음 틱(list_pending)에
            # 재계획된다(설계 §2.3). scan/rm 과 sync 양쪽 모두 대상이다.
            if self._identity_grace_active(req, exc, now_iso):
                self._record_identity_defer(rid, exc)
                return "deferred:identity_propagating"
            return self._reject(rid, exc.reason_code)
        # 6. policy fan-out
        policy = self._repos.control.get_policy(TOOL_TO_POLICY[placement["tool"]])
        # payload 는 신뢰 경계 밖(DB 무검증 INSERT 전제) — node_count 가 **있는데**
        # 비정상(비int·bool·<1)이면 fail-closed 거부한다(stepper 층1 unknown_tool
        # 관례: 변조 증거를 조용히 삼키지 않는다). 키 부재(None)는 정상 — 정책값.
        requested = payload.get("node_count")
        if requested is not None and (
                not isinstance(requested, int) or isinstance(requested, bool)
                or requested < 1):
            return self._reject(rid, "invalid_node_count")
        # procs_per_node 도 같은 방어 — 있는데 비정상이면 fail-closed 거부.
        requested_ppn = payload.get("procs_per_node")
        if requested_ppn is not None and (
                not isinstance(requested_ppn, int) or isinstance(requested_ppn, bool)
                or requested_ppn < 1):
            return self._reject(rid, "invalid_procs_per_node")
        try:
            fanout = resolve_fanout(policy, placement["candidates"],
                                    priority=req["priority"],
                                    requested_node_count=requested,
                                    requested_procs_per_node=requested_ppn)
        except PlacementError as exc:
            return self._reject(rid, exc.reason_code)
        # 6b. 요청 노드 수 유예 내 대기(A'): planner 는 적격 >= 1 이면 즉시 계획하는데,
        #     신원 전파 왕복(~130s) 중 1대만 준비된 틱에 계획되면 요청 node_count=2
        #     여도 1대로 박제된다(실측 사고). 세 조건 전부 만족일 때만 기다린다:
        #     (a) 요청이 node_count 를 명시했고(미지정은 현행 즉시 계획 -- null != 0),
        #     (b) 적격 수 < 목표 = min(요청, 정책 max_nodes) -- 정책이 허용 안 하는
        #         수를 기다리지 않는다. sync 는 max_nodes 가 면당 상한(resolve_fanout)
        #         이므로 목표도 면당 동일 규칙: 어느 면이든 부족하면 대기,
        #     (c) 부족이 개선 가능 -- 탈락 사유에 identity_not_ready_on_node 가 하나
        #         이상(마운트·도구 결손은 기다려도 안 늘어난다 -- 그 경우 즉시 계획).
        #     유예 창은 placement 실패 경로와 같은 앵커(created_at + grace)다 --
        #     만료 후 첫 틱엔 있는 만큼으로 계획한다(마감 있는 최선).
        if requested is not None and self._within_identity_grace(req, now_iso):
            target = min(requested, policy["max_nodes"])
            cand = placement["candidates"]
            if "primary" in cand:
                eligible = len(cand["primary"])
                short = eligible < target
            else:
                eligible = {"source": len(cand["source"]),
                            "destination": len(cand["destination"])}
                short = (eligible["source"] < target
                         or eligible["destination"] < target)
            not_ready = sorted(_identity_pending_nodes(placement["rejections"]))
            if short and not_ready:
                self._record_defer_event(
                    rid, "awaiting_requested_nodes",
                    f"eligible {eligible} < target {target}, "
                    f"identity not ready on: {', '.join(not_ready)}",
                    {"eligible": eligible, "target": target,
                     "not_ready_nodes": not_ready})
                return "deferred:awaiting_requested_nodes"
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
