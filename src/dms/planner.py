"""planner: Pending 요청을 어드미션 게이트를 거쳐 계획된 data_job으로 emit하는 루프 본체.

dms-k8s 는 k8s 신원 모드 전용이다(2026-10-26 LDAP 코드 삭제): 계획된 잡의 신원은 늘 root(uid 0·gid 0)이고, 노드 선택은 마운트·
쓰기·도구·배치 제외·CPU 상한만 본다. (LDAP 시절의 틱 서킷·예산, 신원 전파 유예(identity_propagating), 요청 노드 수 대기
(awaiting_requested_nodes), 보조 그룹 스냅숏·chown 그룹 검사는 지웠다 -- 기다려서 늘어날 노드가 없고 그룹이 없다.)
"""
import sys

from . import k8s_access
from .domain import (DomainValidationError, Operation, RequestState, destructive_operation, overlap_sides,
                     path_conflict, sync_overlap_problem, valid_owner_username)
from .identity import IdentityRejected, owner_override_allowed, privilege_policy, resolve_job_identity
from .repositories.storages import storage_open_to_users
from .privileged import privileged_names
from .repositories.sync_pairs import sync_pair_allowed
from .node_cpu import DEFAULT_NODE_CPU_RESERVE_MILLICORES
from .placement import (
    PlacementError, TOOL_TO_POLICY, dms_node_load, resolve_fanout, select_tool_and_candidates)


def _required_storages(operation, payload):
    if operation == Operation.SYNC.value:
        return [payload["source_storage"], payload["destination_storage"]]
    return [payload["storage"]]


def _payload_sides(operation, payload) -> tuple:
    """요청 payload 의 겹침 판정 쪽들(domain.overlap_sides). dict 가 아니면(깨진 행) 빈 목록 -- 판정할 경로가 없다."""
    if not isinstance(payload, dict):
        return ()
    return overlap_sides(operation, storage=payload.get("storage"), target=payload.get("target"),
                         source_storage=payload.get("source_storage"), source=payload.get("source"),
                         destination_storage=payload.get("destination_storage"),
                         destination=payload.get("destination"))


class Planner:
    def __init__(self, repos, *, settings):
        self._repos = repos
        self._settings = settings
        # 노드별 DMS 예약 코어(placement.dms_node_load) -- 틱마다 처음 필요할 때 한 번 읽고, 이 틱에 계획한 잡을 더해 간다
        # (_node_load·_account_load). None = 아직 안 읽음.
        self._load = None

    def _node_load(self) -> dict:
        """노드 선택 순서의 입력(placement.resolve_fanout docstring). 계획할 요청이 실제로 노드 선택까지 온 틱에만 읽는다
        (비종단 잡의 worker_pool 만 -- data_jobs.active_worker_pools). 읽기 실패는 순서를 이름순으로 되돌릴 뿐 계획을
        막지 않는다(어림값이다)."""
        if self._load is None:
            try:
                self._load = dms_node_load(self._repos.data_jobs.active_worker_pools())
            except Exception as exc:  # noqa: BLE001 -- 순서 어림값 읽기 실패가 계획을 막으면 안 된다
                print(f"planner: node load read failed: {type(exc).__name__}: {exc}", file=sys.stderr)
                self._load = {}
        return self._load

    def _account_load(self, fanout):
        """방금 계획한 잡의 몫을 이 틱의 부하에 더한다 -- 같은 틱에 계획되는 다음 요청이 같은 노드로 몰리지 않게."""
        load = self._node_load()
        for nodes in fanout["candidates"].values():
            for n in nodes:
                load[n] = load.get(n, 0) + fanout["procs_per_node"]

    def run_once(self, limit: int = 50, *, now_iso=None) -> dict:
        pending = self._repos.requests.list_pending(limit)
        results = {}
        self._load = None            # 틱마다 새로 읽는다(그 사이 끝난 잡·다른 경로로 생긴 잡)
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

    def _path_conflict(self, req):
        """1b 의 판정 -- 겹치는 상대가 없으면 None, 있으면 (상대 요청 행, (내 쪽, 상대 쪽)). 상대는 commit_order 순으로 보므로
        가장 앞선 것이 먼저 걸린다. 상대 payload 가 깨진 행은 건너뛴다: 그 행이 실제로 돌고 있다면 stepper 실행 관문이 **잡
        행**(실행이 쓰는 경로)으로 보고 보류한다 -- 계획 시점의 종단 Conflict 를 변조 행 때문에 내지 않는다. 내 경로가 깨졌으면
        (overlap_path 가 루트로 본다) 같은 스토리지의 모든 활성 작업과 겹친다(fail-closed -- 어차피 실행 전에 거부될 행이다)."""
        mine = _payload_sides(req["operation"], req.get("payload"))
        if not mine:
            return None
        destructive = destructive_operation(req["operation"])
        for other in self._repos.requests.overlap_candidates(
                request_id=req["request_id"], commit_order=req["commit_order"], destructive_only=not destructive):
            theirs = _payload_sides(other["operation"], other["payload"])
            hit = path_conflict(destructive, mine, destructive_operation(other["operation"]), theirs)
            if hit is not None:
                return other, hit
        return None

    def _plan_one(self, rid, now_iso):
        # "gone" = 요청이 계획 도중 Pending 을 떠났다(취소·관리자 삭제) -- 아무것도 남기지 않았고 이벤트도 쓰지 않는다
        # (삭제된 id 로 쓴 이벤트는 어디서도 안 보이는 고아다).
        req = self._repos.requests.get(rid)
        if req is None:
            return "gone"                   # list_pending 뒤에 지워졌다
        # 취소된 배치의 자식은 계획하지 않고 끝낸다(2026-10-16 사용자 보고: 「배치작업 Cancel 했는데 몇분 후에 ... 아직
        # 돌아가고 있는 launcher/worker pod」 -- batch_cancel 모듈 docstring). 예전엔 planner 가 배치 상태를 보지 않아 취소
        # 경합으로 남은 Pending 자식을 계획했고 stepper 가 취소 뒤에 vcjob 을 만들어 끝까지 돌렸다. Pending CAS 라 그 사이
        # 다른 경로(취소 라우트)가 먼저 끝냈으면 아무것도 하지 않는다.
        if req.get("batch_id"):
            batch = self._repos.batches.get(req["batch_id"])
            if batch is not None and batch["status"] in ("Cancelling", "Cancelled"):
                if self._repos.requests.cancel_if_pending(rid, reason_code="cancelled_by_batch", actor="planner"):
                    return "cancelled:cancelled_by_batch"
                return "gone"
        # 멱등: 이미 emit된 잡이 있으면(옛 비원자 emit 의 크래시 잔재) 상태만 정리 -- 조건부(Pending 일 때만)라 그 사이
        # 취소된 요청을 Planned 로 되살리지 않는다.
        if self._repos.data_jobs.list_jobs(request_id=rid):
            if self._repos.requests.mark_planned_if_pending(rid, actor="planner"):
                return "planned"
            return "gone"
        payload = req["payload"]
        # 1. conflict: 앞선 비터미널 동일 resource_key
        prior = self._repos.requests.find_active(req["resource_key"])
        if prior is not None and prior["commit_order"] < req["commit_order"]:
            # 슬라이스 30: _reject 와 같은 원자화(비원자 쌍의 두 번째).
            self._repos.requests.set_state_with_result(
                rid, RequestState.CONFLICT, reason_code="resource_conflict",
                actor="planner")
            return "conflict"
        # 1b. 경로 겹침(2026-10-27, domain.path_conflict 주석): 앞선 비종단 요청(또는 이미 계획된 요청)과 같은 스토리지에서
        #     경로가 같거나 조상·자손이고 한쪽이 rm 이면 늦게 온 이 요청을 Conflict 로 끝낸다(배치 자식도 같다 -- 항목은
        #     Failed path_conflict 로 보인다). 대기열에 두지 않는 이유: 앞선 작업은 확인 대기(ConfirmPending)로 미리보기 TTL
        #     동안 머물 수 있고, 사람이 본 미리보기가 그 사이의 rm·sync 로 무효가 된다 -- 「끝난 뒤 다시 제출」이 정직하다.
        clash = self._path_conflict(req)
        if clash is not None:
            other, (mine, theirs) = clash
            self._repos.requests.set_state_with_result(
                rid, RequestState.CONFLICT, reason_code="path_conflict", actor="planner")
            # 상대 요청 id 는 이벤트에만 남는다(전이 행엔 상세 칸이 없다) -- 요청 상세가 이 이벤트로 그 요청 링크를 그린다
            # (RequestActivity.conflictRequestId). 상대의 경로는 싣지 않는다: 남의 요청 경로(내 PV 위 조상 디렉터리)를
            # 보이지 않고, 내 쪽 경로·상대의 연산·쪽 이름만으로 무엇이 겹쳤는지 말한다. record_event 는 예외를 삼킨다 --
            # 실패해도 종단 사유는 남는다(링크만 빠진다).
            self._repos.observability.record_event(
                component="planner", severity="warning", event_type="path_conflict",
                message=(f"다른 작업(요청 {other['request_id'][:8]})과 경로가 겹칩니다 — 겹치는 작업이 끝난 뒤 다시 "
                         f"제출하세요 ({mine[1]}:{mine[2] or '/'} ↔ {other['operation']} {theirs[0]})"),
                payload={"conflict_request_id": other["request_id"], "conflict_operation": other["operation"],
                         "conflict_side": theirs[0], "side": mine[0], "storage": mine[1], "path": mine[2]},
                request_id=rid)
            return "conflict:path_conflict"
        # 2. requester 계정 admission: 계정 row가 존재 & disabled일 때만 거부한다.
        #    row가 없으면 "무판정"(맵핑 면제는 아니다 -- 3a 의 k8s_access.unrestricted_request 는 계정 행을 본다).
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
        storage_rows = {}
        for name in _required_storages(req["operation"], payload):
            storage = self._repos.storages.get(name)
            storage_rows[name] = storage
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
        # 3a. PV 주소·k8s 맵핑 재확인(2026-10-25 dms-k8s 설계 D13, k8s_access.recheck_request -- 컨펌과 같은 함수): pv 블록을
        #     지금 스냅숏으로 다시 해석해 (스토리지, 루트)가 바뀌었으면 pv_moved, PVC·클러스터가 사라졌으면 pv_not_found, target 이
        #     PV 루트 밖이면 pv_target_outside. 비면제 요청은 모든 쪽이 PV 주소여야 하고(raw_path_not_allowed) 요청자의
        #     맵핑을 다시 본다(k8s_access_denied -- 대기 중 권한 회수). DB 가 신뢰 경계다(create_job 무검증 INSERT).
        try:
            k8s_access.recheck_request(self._repos, req, settings=self._settings)
        except DomainValidationError as exc:
            return self._reject(rid, reason=exc.reason_code)
        # 3b. 사용자 sync 허용 스토리지 쌍(2026-09-30, repositories/sync_pairs.py): 기본 전부 불가.
        #     제출 게이트를 통과한 뒤 대기 중에 허용이 빠진 요청·DB 직접 삽입을 계획 시점에 거부. 양쪽이 PV 주소면 면제(설계
        #     D14 -- 제출·컨펌과 같은 술어; 3a 가 그 pv 블록을 방금 확인했다).
        if (req["operation"] == Operation.SYNC.value and not pair_exempt
                and not k8s_access.pair_gate_exempt(payload)
                and not sync_pair_allowed(self._repos, payload.get("source_storage"),
                                          payload.get("destination_storage"))):
            return self._reject(rid, "sync_pair_not_allowed")
        # 3c. 실행 신원 지정(owner_username) -- API 를 거치지 않은 DB 직접 쓰기(신뢰 경계) 방어(2026-10-07).
        #     모양 선검사: "" 는 resolve 가 '본인'으로 읽고, 비문자열은 .strip() AttributeError(매 틱 plan_error,
        #     영구 Pending)로 새던 것을 정확한 사유로 끊는다. 그다음 API(routes_requests.submit)와 **같은 술어**로
        #     자격을 다시 본다 -- 요청 행의 payload 만 owner=alice 로 바꾼 bob 의 요청이 alice 의 이름(실행 신원 기록)을
        #     얻는 경로를 막는다. 배치 자식은 생성자(관리자, 세션·목록 게이트 통과)가 요청자라 통과하고,
        #     생성자가 강등·목록에서 빠지면 자식도 거부된다(storage_admin_only 와 같은 방향).
        owner = payload.get("owner_username")
        if owner is not None and not valid_owner_username(owner):
            return self._reject(rid, "invalid_owner_username")
        if not owner_override_allowed(owner_username=owner, requester_id=req["requester_id"],
                                      requester_is_admin=requester_is_admin,
                                      allow_privileged=self._settings.allow_privileged_requesters,
                                      privileged_requesters=privileged_names(self._settings, self._repos)):
            return self._reject(rid, "privileged_not_authorized")
        # 4. identity -- 통과하면 늘 root(identity.resolve_job_identity docstring). 단건 요청은 payload 의 run_as_root 가
        #    True 일 때(제출이 늘 싣는다 -- 설계 D2) 세션 요청이면 root, 배치 자식(관리자 전용 화면)은 "자격 있으면 root",
        #    근거가 없거나 자격이 없으면 privileged_not_authorized(낮출 비 root 신원이 없다). 규칙은
        #    identity.privilege_policy 하나 -- stepper 가 제출 직전에 같은 함수로 재확인한다.
        privilege = privilege_policy(req)
        try:
            identity = resolve_job_identity(
                self._repos.control,
                requester_id=req["requester_id"],
                owner_username=payload.get("owner_username"),
                allow_privileged=self._settings.allow_privileged_requesters,
                privileged_requesters=privileged_names(self._settings, self._repos),
                # 요청을 만든 인증 방식. token(또는 컬럼 미채움/NULL)은 특권을 못
                # 얻는다 -- 기배포 DB 의 구형 행은 NULL 이라 자동으로 비특권이다.
                session_authenticated=(req.get("auth_method") == "session"),
                privilege=privilege)
        except IdentityRejected as exc:
            return self._reject(rid, exc.reason_code)
        # 5. tool + candidates
        fresh = self._repos.agents.fresh_reports(
            stale_seconds=self._settings.agent_report_stale_seconds, now_iso=now_iso)
        try:
            # 노드 배치 제외(repositories/node_exclusions.py): 관리자가 막은 노드는 후보에서 빠진다(k8s 스케줄 불가는
            # 보고의 k8s_node 에서 placement 가 직접 읽는다). 일부만 남으면 남은 노드로 바로 계획하고, 0대면
            # nodes_excluded 로 즉시 거부한다.
            placement = select_tool_and_candidates(
                req["operation"], fresh, storage_name=payload.get("storage"),
                source_storage=payload.get("source_storage"),
                destination_storage=payload.get("destination_storage"),
                excluded_nodes=self._repos.node_exclusions.excluded_names(),
                # 노드 CPU 상한(node_cpu): 1코어도 못 담는 노드는 빠지고, 0대면 node_cpu_insufficient 로 즉시 거부한다
                # (유예하지 않는다 -- allocatable 은 기다려도 늘지 않는다). 설정 스텁에 필드가 없으면 코드 기본값.
                cpu_reserve_m=getattr(self._settings, "node_cpu_reserve_millicores",
                                      DEFAULT_NODE_CPU_RESERVE_MILLICORES))
        except PlacementError as exc:
            return self._reject(rid, exc.reason_code)
        # 5b. sync 실제 경로 겹침(2026-10-12): 목적지가 소스와 같거나 그 아래면 거부 -- managed_root 를 붙인 **실제 경로**로 본다
        #     (다른 스토리지끼리 같은 상대경로를 막던 사용자 보고의 수정, 루트가 포개진 두 스토리지는 막는다). 같은 이름 공간일
        #     때만 뜻이 있다: dsync 는 두 스토리지를 함께 마운트한 노드에서 돌아 두 경로가 한 파일시스템 트리에 있지만, nsync
        #     (함께 마운트한 노드가 없을 때)는 소스·목적지가 서로 다른 노드의 다른 파일시스템이라 문자열이 같아도 다른 곳이다
        #     (검증 지적 -- CPU 노드의 /home 과 GPU 노드의 /home/gpu1). 그래서 도구가 정해진 **뒤**, dsync 에서만 본다. 같은
        #     스토리지의 상대경로 검사는 제출 시점(build_data_payload)이 이미 했다.
        if req["operation"] == Operation.SYNC.value and placement["tool"] == "dsync":
            src_row = storage_rows.get(payload.get("source_storage"))
            dst_row = storage_rows.get(payload.get("destination_storage"))
            if src_row is not None and dst_row is not None:
                problem = sync_overlap_problem(src_row.get("managed_root"), payload.get("source"),
                                               dst_row.get("managed_root"), payload.get("destination"))
                if problem is not None:
                    return self._reject(rid, problem)
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
                                    requested_procs_per_node=requested_ppn,
                                    node_cpu_caps=placement["node_cpu_caps"],
                                    # 노드 선택 순서: 상한 충족 → DMS 예약 코어 적은 순 → 이름(placement._pick_nodes)
                                    node_load=self._node_load())
        except PlacementError as exc:
            return self._reject(rid, exc.reason_code)
        # 7. emit -- 신원 스냅숏은 다섯 키(username·uid·gid·groups·privileged -- stepper.identity_problem 이 이 모양을 다시
        #    본다). 요청 노드 수(node_count)는 있는 만큼으로 계획한다(LDAP 시절의 「요청 노드 수 대기」는 지웠다).
        identity_dict = {"username": identity.username, "uid": identity.uid, "gid": identity.gid,
                         "groups": list(identity.groups), "privileged": identity.privileged}
        # candidates 는 fanout 이 고른 노드다(상한 충족 → DMS 예약 적은 순 → 이름) -- CPU 상한을 본 노드 집합과 굳히는 노드
        # 집합이 같은 한 곳(placement.resolve_fanout docstring). procs_per_node·procs_per_node_requested·node_cpu_caps·
        # cpu_cap_known 도 함께.
        worker_pool = {"tool": placement["tool"], "identity": identity_dict,
                       "rejections": placement["rejections"], **fanout}
        precondition = {"requester_id": req["requester_id"],
                        "owner": identity.username, "operation": req["operation"]}
        # plan·job INSERT + Pending→Planned 를 한 트랜잭션 + CAS 로(data_jobs.create_plan_and_job docstring): 계획
        # 도중 취소·삭제된 요청에 잡을 만들거나 Cancelled 를 Planned 로 되살리지 않는다.
        job_id = self._repos.data_jobs.create_plan_and_job(
            rid, operation=req["operation"], priority=req["priority"],
            storage_name=payload.get("storage"),
            source_storage=payload.get("source_storage"),
            destination_storage=payload.get("destination_storage"),
            source=payload.get("source"), destination=payload.get("destination"),
            target=payload.get("target"), options=payload.get("options", {}),
            tool=placement["tool"], worker_pool=worker_pool,
            precondition=precondition, actor="planner")
        if job_id is None:
            return "gone"
        self._account_load(fanout)
        self._record_cpu_clamp_event(rid, fanout)
        return "planned"

    def _record_cpu_clamp_event(self, rid, fanout):
        """노드 CPU 상한으로 노드당 프로세스를 줄였으면 계획 성공 뒤 1회 남긴다(조용히 줄이지 않는다 -- 화면은 worker_pool 의
        procs_per_node_requested → procs_per_node 를 그린다). record_event 는 예외를 삼키므로 계획 결과를 바꾸지 않는다."""
        if fanout["procs_per_node"] >= fanout["procs_per_node_requested"]:
            return
        self._repos.observability.record_event(
            component="planner", severity="info", event_type="procs_clamped_to_node_cpu",
            message=(f"노드당 프로세스 {fanout['procs_per_node_requested']} → {fanout['procs_per_node']}"
                     f" (노드 CPU 상한)"),
            payload={"requested": fanout["procs_per_node_requested"], "applied": fanout["procs_per_node"],
                     "node_cpu_caps": fanout["node_cpu_caps"]},
            request_id=rid)
