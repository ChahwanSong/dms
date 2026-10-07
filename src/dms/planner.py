"""planner: Pending 요청을 어드미션 게이트를 거쳐 계획된 data_job으로 emit하는 루프 본체."""
import sys
import time
from dataclasses import asdict

from .db import iso_plus, utc_now_iso
from .domain import Operation, RequestState, valid_owner_username
from .identity import (LDAP_TICK_BUDGET_SECONDS, MAX_SUPPLEMENTARY_GROUPS, SUPP_OVER_LIMIT,
                       IdentityDeadlineExceeded, IdentityLookupInvalid, IdentityRejected, IdentityUnavailable,
                       LdapCircuitOpen, check_chown_group, owner_override_allowed, privilege_policy,
                       resolve_job_identity, tick_resolve_deadline)
from .repositories.storages import storage_open_to_users
from .repositories.sync_pairs import sync_pair_allowed
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


# _plan_one 의 resolver 인자 기본값 표식(= self._resolver). None 은 "미구성" 이라는 뜻이 따로 있어 쓸 수 없다.
_SELF = object()


class _TickCircuit:
    """틱 단위 LDAP 서킷(2026-10-07): 한 틱에서 LDAP 불가가 한 번 나면 같은 틱의 나머지 조회는 LDAP 에 가지 않고
    LdapCircuitOpen 을 올린다 -- 요청은 Pending 으로 남아 다음 틱에 다시 본다. 없으면 틱마다 대기 요청(최대 50)이
    각자 타임아웃 × URI 수만큼 기다려, 단일 스레드 컨트롤러의 stepper·pod-gc·rollout 이 몇 분씩 멈춘다. 서킷을
    연 첫 요청은 지금처럼 ldap_unavailable 로 거부된다(계획 시점 실패의 기존 의미).

    틱 예산(2026-10-07): 한 틱의 resolve 누적 시간이 LDAP_TICK_BUDGET_SECONDS 를 넘으면 서킷과 같이 연다(남은 요청은
    Pending, 다음 틱). 보조 그룹 페이징(D14)으로 resolve 하나의 최악이 검색 2회 → 최대 21회로 커져, 예산 없이는 틱당
    대기 50건 × resolve 가 루프 리스 30s 를 넘어 두 번째 컨트롤러(RollingUpdate surge·replicas>1)가 planner 를
    동시에 돌 수 있다. 남은 예산은 deadline 으로 리졸버에 넘겨 resolve 하나도 그 안에서만 새 연산을 시작한다.
    남은 몫이 resolve 를 멈추면(IdentityDeadlineExceeded -- LDAP 가 멀쩡해도 예산 경계에 걸친 요청) 예산 소진과 같이
    LdapCircuitOpen 으로 바꿔 그 요청도 Pending 으로 둔다(2026-10-08 리뷰: 예전엔 plain IdentityUnavailable 이라
    ldap_unavailable 로 **종단 거부** -- 대기 요청이 많은 틱마다 정상 요청 하나가 영구히 거부됐다). 틱 첫 resolve 는
    남은 몫 = 예산 = 자체 마감이라 deadline 을 넘기지 않아(identity.tick_resolve_deadline) 진짜 장애·느림은 그대로
    첫 요청이 ldap_unavailable 로 거부된다 -- 매 틱 진행이 보장되고 영구 Pending 이 생기지 않는다.
    IdentityLookupInvalid(사용자 엔트리 중복 등 이 사용자의 데이터 문제)는 서킷을 열지 않는다 -- 그 요청만
    ldap_unavailable 로 거부되고 같은 틱의 다른 사용자 요청은 계속 계획된다."""
    def __init__(self, inner, *, monotonic=time.monotonic, budget=LDAP_TICK_BUDGET_SECONDS):
        self._inner, self._mono, self._budget = inner, monotonic, budget
        self.open = False
        self._spent = 0.0

    def resolve(self, username, *, deadline=None):
        if self.open:
            raise LdapCircuitOpen(username)
        remaining = self._budget - self._spent
        if remaining <= 0:
            self.open = True                 # 예산 소진 = 서킷과 같은 처리(요청 Pending, 다음 틱)
            raise LdapCircuitOpen(username)
        t0 = self._mono()
        try:
            return self._inner.resolve(username, deadline=tick_resolve_deadline(t0, remaining))
        except IdentityLookupInvalid:
            raise                            # 사용자별 데이터 오류 -- 그 요청만 ldap_unavailable, 서킷 유지
        except IdentityDeadlineExceeded as exc:
            self.open = True                 # 예산의 남은 몫이 멈췄다 = 예산 소진(요청 Pending, 다음 틱 온전한 예산)
            raise LdapCircuitOpen(username) from exc
        except IdentityUnavailable:
            self.open = True
            raise
        finally:
            self._spent += self._mono() - t0


class Planner:
    def __init__(self, repos, resolver, *, settings, monotonic=None):
        self._repos = repos
        self._resolver = resolver
        self._settings = settings
        self._monotonic = monotonic or time.monotonic   # 틱 LDAP 예산 시계(테스트 주입용)

    def run_once(self, limit: int = 50, *, now_iso=None) -> dict:
        pending = self._repos.requests.list_pending(limit)
        results = {}
        # 틱마다 새 서킷·예산 -- 다음 틱은 다시 LDAP 를 시도한다(LDAP 가 돌아오면 바로 이어진다).
        circuit = (None if self._resolver is None
                   else _TickCircuit(self._resolver, monotonic=self._monotonic))
        deferred = 0
        for row in pending:
            rid = row["request_id"]
            try:
                results[rid] = self._plan_one(rid, now_iso, resolver=circuit)
            except LdapCircuitOpen:
                results[rid] = "deferred:ldap_circuit_open"
                deferred += 1
            except Exception as exc:  # 한 요청 실패가 다음을 막지 않는다
                print(f"planner error on {rid}: {type(exc).__name__}: {exc}",
                      file=sys.stderr)
                # 이 실패는 전이를 남기지 않는다(예외가 어느 set_state 호출보다도 먼저
                # 터질 수 있다) -- stderr만으로는 파드 재시작에 사라지므로 events에 남긴다.
                self._repos.observability.record_event(
                    component="planner", severity="error", event_type="plan_error",
                    message=f"{type(exc).__name__}: {exc}"[:500], request_id=rid)
        if deferred:
            print(f"planner: ldap circuit open (outage or tick budget {LDAP_TICK_BUDGET_SECONDS}s spent) -- "
                  f"{deferred} request(s) left Pending for the next tick", file=sys.stderr)
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

    def _plan_one(self, rid, now_iso, *, resolver=_SELF):
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
        # 요청자가 관리자인가(2026-09-30 스토리지 사용 범위): 지금의 계정 역할, 또는 API 가 실제로
        # 만드는 토큰 요청의 모양(auth_method=token + requester shared-token/node:*)뿐이다 --
        # "token" 표식만으로 통과시키면 API 가 만들 수 없는 조합(token + alice, DB 직접 삽입)이
        # 샌다. 계정 행이 없으면 비관리자로 본다(fail-closed). 배치 자식은 배치 행의 생성 시점
        # auth_method 를 물려받으므로(batch_orchestrator._materialize) 같은 규칙으로 판정된다.
        token_admin = (req.get("auth_method") == "token"
                       and (req["requester_id"] == "shared-token"
                            or str(req["requester_id"]).startswith("node:")))
        requester_is_admin = token_admin or (account is not None and account["role"] == "admin")
        # sync 허용 쌍의 면제(3b)는 여기에 배치 자식을 더한다: 쌍 정책은 "사용자" 규칙이고 배치는
        # 관리자 전용 라우트(routes_batches, require_admin)에서만 생긴다 -- 생성자 계정이 뒤에
        # 바뀌어도 이미 만든 배치 항목이 사용자 규칙에 걸리지 않게. 관리자 전용 스토리지 판정은
        # 이 면제를 쓰지 않는다(d142 불변식: 계정 역할 기준 -- 강등된 생성자의 배치는 거부).
        pair_exempt = requester_is_admin or bool(req.get("batch_id"))
        # 3. storage admission
        for name in _required_storages(req["operation"], payload):
            storage = self._repos.storages.get(name)
            if storage is None:
                return self._reject(rid, "storage_missing")
            if not storage["enabled"]:
                return self._reject(rid, "storage_disabled")
            # 관리자 전용(2026-09-30 사용 범위): 비관리자 요청은 계획 시점에도 거부 -- 제출
            # 게이트(routes_requests)를 통과한 뒤 대기 중에 관리자 전용으로 바뀐 요청·DB 직접
            # 삽입(신뢰 경계) 방어.
            if not storage_open_to_users(storage) and not requester_is_admin:
                return self._reject(rid, "storage_admin_only")
            if storage["status"] not in ("Ready", "Degraded"):
                return self._reject(rid, "storage_not_ready")
        # 3b. 사용자 sync 허용 스토리지 쌍(2026-09-30, repositories/sync_pairs.py): 기본 전부 불가.
        #     제출 게이트를 통과한 뒤 대기 중에 허용이 빠진 요청·DB 직접 삽입을 계획 시점에 거부.
        if (req["operation"] == Operation.SYNC.value and not pair_exempt
                and not sync_pair_allowed(self._repos, payload.get("source_storage"),
                                          payload.get("destination_storage"))):
            return self._reject(rid, "sync_pair_not_allowed")
        # 3c. 실행 신원 지정(owner_username) -- API 를 거치지 않은 DB 직접 쓰기(신뢰 경계) 방어(2026-10-07).
        #     모양 선검사: "" 는 resolve 가 '본인'으로 읽고, 비문자열은 .strip() AttributeError(매 틱 plan_error,
        #     영구 Pending)로 새던 것을 정확한 사유로 끊는다. 그다음 API(routes_requests.submit)와 **같은 술어**로
        #     자격을 다시 본다 -- 요청 행의 payload 만 owner=alice 로 바꾼 bob 의 요청이 alice 의 uid 와 이제 보조
        #     그룹까지 얻는 경로를 막는다. 배치 자식은 생성자(관리자, 세션·목록 게이트 통과)가 요청자라 통과하고,
        #     생성자가 강등·목록에서 빠지면 자식도 거부된다(storage_admin_only 와 같은 방향).
        owner = payload.get("owner_username")
        if owner is not None and not valid_owner_username(owner):
            return self._reject(rid, "invalid_owner_username")
        if not owner_override_allowed(owner_username=owner, requester_id=req["requester_id"],
                                      requester_is_admin=requester_is_admin,
                                      allow_privileged=self._settings.allow_privileged_requesters,
                                      privileged_requesters=self._settings.privileged_requesters):
            return self._reject(rid, "privileged_not_authorized")
        # 4. identity
        # (포탈의 "관리자 기본 root" 도 제출 바디의 명시 run_as_root: true 로 온다 -- 서버는 생략을
        #  root 로 읽지 않는다, routes_requests.submit.)
        # root 실행은 payload 가 명시할 때만(2026-09-30 프로덕션 사고, identity.resolve_job_identity
        # docstring): 단건 요청은 payload 의 run_as_root 가 True 일 때만 root 를 요구하고
        # (자격 없으면 거부), 없으면 실행 신원의 LDAP uid/gid 로 돈다 -- 관리자 계정이라도.
        # 배치 자식(관리자 전용 화면)만 종전대로 "자격 있으면 root". 규칙은
        # identity.privilege_policy 하나 -- stepper 가 제출 직전에 같은 함수로 재확인한다.
        privilege = privilege_policy(req)
        try:
            identity = resolve_job_identity(
                self._repos.control, self._resolver if resolver is _SELF else resolver,
                requester_id=req["requester_id"],
                owner_username=payload.get("owner_username"),
                allow_privileged=self._settings.allow_privileged_requesters,
                privileged_requesters=self._settings.privileged_requesters,
                # 요청을 만든 인증 방식. token(또는 컬럼 미채움/NULL)은 특권을 못
                # 얻는다 -- 기배포 DB 의 구형 행은 NULL 이라 자동으로 비특권이다.
                session_authenticated=(req.get("auth_method") == "session"),
                privilege=privilege,
                # 보조 그룹(D9, 계획 시점 전용 스위치). 설정 스텁에 필드가 없으면 꺼짐(fail-closed) -- 실
                # Settings 의 기본은 켬이다.
                supplementary_groups=getattr(self._settings, "identity_supplementary_groups", False))
            # D7: 명시 chown 의 gid 는 실행 신원이 속한 그룹(주 gid ∪ 적용된 보조 gid)이어야 한다 -- 예전엔 비소속
            # gid 면 dsync 가 데이터를 다 복사한 뒤 EPERM 으로 Failed 였다. 자동 chown(uid:주 gid)은 그대로다.
            if req["operation"] == Operation.SYNC.value:
                opts = payload.get("options")
                if isinstance(opts, dict) and "chown" in opts:
                    check_chown_group(opts["chown"], identity=identity)
        except IdentityRejected as exc:
            if exc.reason_code == "ldap_unavailable":
                # 운영자 추적(2026-10-08 리뷰): 같은 사유 코드로 접히는 원인(사용자 엔트리 중복·결과 코드 4/11/32·그룹
                # 페이지 상한·URI 연결 오류)을 가를 근거 -- deploy/README §2c '배포 직후 확인'. LDAP 원문(URI·소켓
                # 오류)은 stderr 에만 둔다: 요청 결과·이벤트는 비관리자 요청자에게도 반환된다(사유 코드만).
                print(f"planner: {rid} rejected ldap_unavailable: {exc.detail}"[:500], file=sys.stderr)
            return self._reject(rid, exc.reason_code)
        # 5. tool + candidates
        fresh = self._repos.agents.fresh_reports(
            stale_seconds=self._settings.agent_report_stale_seconds, now_iso=now_iso)
        try:
            # 노드 배치 제외(repositories/node_exclusions.py): 관리자가 막은 노드는 후보에서 빠진다(k8s 스케줄 불가는
            # 보고의 k8s_node 에서 placement 가 직접 읽는다). 일부만 남으면 남은 노드로 바로 계획하고, 0대면
            # nodes_excluded 로 즉시 거부한다 -- 기다려도 풀리지 않는 사유라 유예하지 않는다(_GRACE_REASONS 밖).
            placement = select_tool_and_candidates(
                req["operation"], fresh, storage_name=payload.get("storage"),
                source_storage=payload.get("source_storage"),
                destination_storage=payload.get("destination_storage"),
                owner=identity.username, privileged=identity.privileged,
                excluded_nodes=self._repos.node_exclusions.excluded_names())
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
        # 7. emit -- 스냅숏 4키(supplementary_gids·_status·_excluded·_found)는 JSON 그대로의 모양(list·str·int|None)
        #    으로 얼린다. 리졸버 원시값(group_gids)은 싣지 않는다(실행은 걸러진 목록만 본다).
        identity_dict = {**asdict(identity), "groups": list(identity.groups),
                         "supplementary_gids": list(identity.supplementary_gids),
                         "supplementary_gids_excluded": list(identity.supplementary_gids_excluded)}
        identity_dict.pop("group_gids", None)
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
        self._record_group_events(rid, identity)
        return "planned"

    def _record_group_events(self, rid, identity):
        """보조 그룹 진단 이벤트 -- 계획 **성공 뒤 1회만**(유예 틱 identity_propagating·awaiting_requested_nodes 에는
        남지 않는다: 그 틱들은 여기까지 오지 않는다). record_event 는 예외를 삼키므로 계획 결과를 바꾸지 않는다."""
        if identity.supplementary_gids_status == SUPP_OVER_LIMIT or identity.supplementary_gids_excluded:
            # D4: 통째로 미적용 / 기술적 무효 gidNumber 제외 -- 화면(잡 상세)과 같은 사실을 요청 이벤트로도 남긴다.
            self._repos.observability.record_event(
                component="planner", severity="info", event_type="identity_groups_filtered",
                message=(f"보조 그룹 미적용: {identity.supplementary_gids_found}개 > 상한 {MAX_SUPPLEMENTARY_GROUPS}"
                         if identity.supplementary_gids_status == SUPP_OVER_LIMIT
                         else f"유효하지 않은 gidNumber 제외: {list(identity.supplementary_gids_excluded)}"),
                payload={"status": identity.supplementary_gids_status,
                         "found": identity.supplementary_gids_found,
                         "limit": MAX_SUPPLEMENTARY_GROUPS,
                         "excluded": list(identity.supplementary_gids_excluded)},
                request_id=rid)
        if 0 in identity.supplementary_gids:
            # D3 로 인정(거부 안 함) -- 운영자 가시성만: root 그룹 권한이 비 root 잡에 실린다.
            self._repos.observability.record_event(
                component="planner", severity="warning", event_type="identity_groups_root_group",
                message="보조 그룹에 gid 0(root 그룹)이 포함됨 -- root:root 770 디렉터리가 이 잡에 열린다",
                payload={"gids": list(identity.supplementary_gids)}, request_id=rid)
