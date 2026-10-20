"""배치 오케스트레이터: 배치 자식(item)을 실제 request로 throttle-materialize하고,
자식 종단 상태를 집계해 배치를 완료시키는 controller-loop.

스캔 경로: Running 배치에서 max_concurrency - in_flight 만큼 Queued item을
materialize하고, 자식 request가 종단이면 item을 종단화 + counts를 bump하며,
전 item이 종단이면 배치를 Completed로 전이한다.

확인 게이트 경로(sync·rm -- domain.confirm_gated, rm 은 2026-10-21 부터): Previewing 배치에서 Queued item을 쓰로틀
materialize해 preview를 진행시키고, Queued 도 미리보기 단계 자식도 없고 확인할 미리보기(ConfirmPending)가 하나라도
있으면 배치를 PreviewReady로 전이한다(mark_preview_ready -- 한 문장 CAS + 확인 회차 +1). 이미 확인받아 실행 중인
자식(Executing)은 PreviewReady 를 막지 않는다 -- Running 에서 항목 추가 등으로 Previewing 에 돌아온
배치가 가장 긴 자식이 끝날 때까지 재확인조차 못 하던 정체(2026-10-07 리뷰). 확인할 미리보기 없이
실행 중 자식만 남았으면(추가한 항목이 지워졌거나 미리보기가 거부됨) 확인 대기가 아니라 Running 으로
돌려 마저 기록·완료한다. 슬롯은 실행 중 자식도 차지한다(동시 실행 상한 유지). 확인 대기(PreviewReady)
배치도 루프가 **기록만** 하러 돈다(list_awaiting_confirm): 그 사이 끝난 실행 중 자식을 항목에 남기고 전부
끝났으면 완료한다 -- 확인 대기는 사람이 누를 때까지 길어서, 기록하지 않으면 실제로 돈 자식이 Materialized
로 남아 실패분 재실행·완료가 막히고 취소가 "취소됨" 으로 덮었다. 운영자가 배치를 Running으로 confirm하면(확인 회차 CAS + 그 순간의
ConfirmPending 자식에 확인 도장) 남은 슬롯만큼 **도장이 지금 미리보기와 같은** 자식을 쓰로틀
confirm(`_confirm_child`)한다 -- 새 미리보기는 하나도 만들지 않는다: Running 확인 게이트 배치에 Queued·미리보기
단계 자식·도장 없는 미리보기가 보이면(라우트의 Previewing 복귀를 놓친 경합, 업그레이드 전 옛 코드가 남긴
행) 아무것도 컨펌하지 않고 배치를 Previewing 으로 되돌린다(사람이 보지 않은 미리보기의 자동 컨펌 차단).

미리보기 만료: 배치 자식도 stepper expire_previews 가 PreviewExpired → 요청 Rejected(preview_expired)로
끝내고, 다음 틱 _record_terminal 이 항목을 Rejected·실패 1 로 센다(Previewing·PreviewReady·Running 모두 --
stepper 가 같은 컨트롤러 패스에서 먼저 돈다). 다시 미리보기는 자동으로 하지 않는다: 재실행은 :rerun-failed
→ Previewing → 재확인. Running 에선 만료된 미리보기를 컨펌하지 않는다(슬롯도 차지하지 않는다).

자동 확인(2026-10-11 사용자 결정 -- "배치 sync 의 경우 자동 승인을 옵션으로, 기본 false"): 특권 세션 관리자가 생성
(또는 종단 배치의 실행 설정 변경)에서 켠 sync 배치(batches.auto_confirm_by)는 PreviewReady 에서 루프가 **사람의 「배치
확인」과 같은 저장소 함수**(BatchesRepository.confirm -- 회차·Queued 없음 CAS + 그 순간 ConfirmPending 자식 도장 +
감사 행)를 대신 부른다. 새 경로는 없다: 도장·회차·Queued 둑과 Running 분기의 「도장 없는 미리보기면 Previewing 복귀」는
그대로라, 확인은 여전히 한 회차의 미리보기 전부를 대표한다 -- 바뀐 것은 누르는 주체(켠 사람의 사전 동의)뿐이다. 확인
직전에 켠 사람의 자격을 다시 보고(auto_confirm_hold), 안 되면 확인 대기에 남겨 사람이 누르게 한다(보류). 단건 sync 는
여전히 사람이 컨펌한다(이 경로 밖 -- 배치 자식만 여기서 실행된다). rm 배치에는 자동 확인이 없다(2026-10-21 결정 --
되돌릴 수 없는 root 삭제라 늘 사람이 「배치 확인」을 누른다): 켜는 라우트가 422(auto_confirm_sync_only)고, DB 에
auto_confirm_by 를 써 넣어도 PreviewReady 분기의 sync 검사와 auto_confirm_hold 가 두 겹으로 거른다.
"""
from .db import load_json, utc_now_iso
from .domain import (DomainValidationError, Operation, TERMINAL_REQUEST_STATES, confirm_gated,
                     DataJobState, build_data_payload, resolve_priority, validate_options)
from .identity import privilege_eligible
from .batch_cancel import record_item_terminal, sweep_cancelled
from .privileged import privileged_names

_ITEM_TERMINAL = {"Succeeded", "Failed", "Rejected", "Cancelled"}
# repositories.batches._ACTIVE(list_active 가 고르는 상태) + 확인 대기(기록만) -- _drive 의 재확인용.
_BATCH_ACTIVE = {"Previewing", "Running"}
_BATCH_DRIVEN = _BATCH_ACTIVE | {"PreviewReady"}
_REQ_TERMINAL = {s.value for s in TERMINAL_REQUEST_STATES}
# 자식 잡이 아직 미리보기(dry-run) 단계인 상태. 이 밖의 비종단 자식(Executing, scan 의 Running, 잡은 종단인데
# 요청 종단화 전)은 "실행 중" 이다 -- 미리보기 단계만 PreviewReady 를 막는다.
_PREVIEW_PHASE = {DataJobState.PENDING.value, DataJobState.PREFLIGHT.value, DataJobState.PREVIEW_RUNNING.value}


class _ClaimLost(Exception):
    """자식 요청 INSERT 뒤 항목 claim 이 0 행 -- 트랜잭션을 롤백시키는 신호(밖으로 새지 않는다)."""


def _preview_expired(job, now) -> bool:
    return bool(job.get("preview_expires_at")) and job["preview_expires_at"] < now


def _stamped(job) -> bool:
    """배치 확인 도장이 지금 미리보기 지문과 같은가(None 은 도장 없음 -- 확인 전이거나 옛 코드가 만든 미리보기)."""
    return job.get("confirmed_fingerprint") is not None and job["confirmed_fingerprint"] == job.get("preview_fingerprint")


def preview_files(items) -> dict:
    """ConfirmPending 항목 preview_summary.files 의 합(아는 값만)과 모르는 항목 수 -- null≠0: 하나도 모르면 files_total 은
    None 이고 0 은 정상값이다(rm 의 0 = 대상 경로 없음). 포탈 확인 대화상자의 「삭제·복사 대상」과 같은 집계다. DB 가 신뢰
    경계라 bool·음수·비정수는 모름으로 센다(stepper 의 _as_count 와 같은 결). items = BatchesRepository.list_items_detail.

    **사람의 확인(routes_batches.confirm_batch)과 자동 확인(_auto_confirm)이 같이 쓴다**(2026-10-21 리뷰): 감사 행
    `batch/confirm` 의 모양이 누르는 주체와 무관해야 「같은 confirm, 같은 기록」이다 -- 자동 확인이 자기 요약을 따로
    만들면서 이 두 키가 자동 확인 행에만 빠져 있었다."""
    pending = [it for it in items if it.get("job_state") == DataJobState.CONFIRM_PENDING.value]
    known = []
    for it in pending:
        s = it.get("preview_summary")
        v = s.get("files") if isinstance(s, dict) else None
        if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
            known.append(v)
    return {"files_total": sum(known) if known else None, "files_unknown": len(pending) - len(known)}


# 자동 확인 감사 행의 actor. 계정 이름 규칙(repositories/accounts._USERNAME_RE -- [a-z0-9][a-z0-9._-]*)에 없는 ':' 를
# 넣어, 같은 이름의 사람 계정이 확인한 기록이 「자동 확인」으로 읽히는 일이 없게 한다. 켠 사람은 after_state 에 남는다.
AUTO_CONFIRM_ACTOR = "system:auto-confirm"


# 보류 경고를 남긴 회차(배치 id → 회차). 확인 대기는 사람이 누를 때까지 매 틱 다시 판정되므로 회차마다 한 번만 남긴다.
# **모듈 수준**이다: 컨트롤러는 틱마다 BatchOrchestrator 를 새로 만든다(controller.py 의 Loop 람다) -- 인스턴스 속성이면
# 매 틱 비어 5초마다 같은 경고가 쌓였다(2026-10-11 리뷰). 프로세스 메모리라 컨트롤러 재시작 뒤 한 번 더 남을 수 있다 --
# 무해하다. 확인되면 그 배치 키를 지운다(크기는 보류된 적 있는 배치 수로 묶인다).
_HELD_ROUND: dict[str, int] = {}


def _hold(*, reason_code: str) -> str:
    """보류 사유 코드. 사유 코드 커버리지 추출기는 `reason_code=` 키워드 리터럴만 읽는다 -- 반환 리터럴로만 쓰면 포탈
    문구 목록(reasonCodes.json) 계약 밖으로 샌다."""
    return reason_code


def auto_confirm_hold(repos, settings, batch) -> str | None:
    """자동 확인이 켜진 sync 배치를 지금 사람 대신 확인해도 되나 -- None 이면 된다, 아니면 보류 사유(확인 대기에 남고
    사람이 「배치 확인」을 누른다). orchestrator(확인 직전)와 배치 상세 GET(화면 안내)이 같은 술어를 쓴다 -- 두 곳이
    규칙을 따로 들면 한쪽만 바뀌는 순간 화면과 동작이 어긋난다.

    DB 가 신뢰 경계라 켜 둔 값을 그대로 믿지 않고 확인하는 순간 다시 본다(생성·확인 라우트의 특권 3중 게이트와 같은 결):
    켠 사람이 지금도 특권 목록에 있고(허용 설정 포함), 계정이 활성 관리자다. 세션 조건은 켜는 라우트(생성·실행 설정
    변경 -- _require_batch_privilege)가 그 순간 봤다 -- 토큰은 켤 수 없다. 옵션도 지금 규칙으로 다시 검증한다(사람의
    확인 라우트 _reject_stale_options 와 같은 이유 -- 규칙이 바뀐 옛 배치를 실행 단계에서 하나씩 거부하지 않게)."""
    by = batch.get("auto_confirm_by")
    if batch.get("operation") != Operation.SYNC.value:
        return _hold(reason_code="auto_confirm_sync_only")
    if not settings.allow_privileged_requesters:
        # 사이트 전체 특권(root) 실행이 꺼졌다 -- 켠 사람 탓이 아니라 사람이 확인해도 root 로 돌지 못한다(문구가 다르다).
        return _hold(reason_code="auto_confirm_privileged_disabled")
    if not privilege_eligible(requester_id=by, allow_privileged=settings.allow_privileged_requesters,
                              privileged_requesters=privileged_names(settings, repos),
                              session_authenticated=True):
        return _hold(reason_code="auto_confirm_not_privileged")
    account = repos.accounts.get(by)
    if account is None or account["disabled"] or account["role"] != "admin":
        return _hold(reason_code="auto_confirm_not_admin")
    try:
        validate_options(batch["operation"], batch["options"])
    except DomainValidationError as exc:
        return exc.reason_code
    return None


class BatchOrchestrator:
    def __init__(self, repos, *, settings, execution_adapter=None):
        self._repos = repos
        self._settings = settings
        # 취소된 배치의 살아 있는 자식을 끝내는 그물(batch_cancel.sweep_cancelled, 2026-10-16)에만 쓴다 -- 컨트롤러가 준다.
        # None(단위 시험의 좁은 생성)이면 그물을 건너뛴다.
        self._exec = execution_adapter

    def run_once(self):
        for batch in self._repos.batches.list_active() + self._repos.batches.list_awaiting_confirm():
            self._drive(batch)
        if self._exec is not None:
            # 취소 라우트가 정상 경로에서 다 끝내므로 보통 0건이다 -- 부분 실패·경합의 그물(batch_cancel 모듈 docstring).
            # 한 자식의 실패는 그 안에서 격리되고, 질의 자체의 예외는 루프 래퍼(error:<Type>)가 다음 틱으로 넘긴다.
            sweep_cancelled(self._repos, self._exec)

    def _child_state(self, request_id):
        """("terminal", 요청 상태) | ("previewed", 잡) | ("previewing", 잡|None) | ("executing", 잡).
        previewing = 잡이 아직 없거나(계획 전) 미리보기 단계. 요청 행이 없으면(모름) previewing 으로 -- 확인으로
        넘어가지 못하게 보수적으로 읽는다."""
        req = self._repos.requests.get(request_id)
        if req is None:
            return ("previewing", None)
        if req["state"] in _REQ_TERMINAL:
            return ("terminal", req["state"])
        jobs = self._repos.data_jobs.list_jobs(request_id=request_id)
        job = jobs[0] if jobs else None
        if job is None or job["state"] in _PREVIEW_PHASE:
            return ("previewing", job)
        if job["state"] == DataJobState.CONFIRM_PENDING.value:
            return ("previewed", job)
        return ("executing", job)

    def _record_terminal(self, batch_id, item, req_state):
        # 항목 CAS(Materialized 일 때만) + 바꾼 쪽만 집계 -- 취소 라우트·컨트롤러 그물과 같은 함수(batch_cancel.
        # record_item_terminal). 취소는 성공도 실패도 아니다(집계 없음). 사유는 요청의 실제 종단 사유를 옮긴다(없으면
        # NULL -- 바로 옆 StatusPill 과 중복 표시하지 않는다).
        record_item_terminal(self._repos, batch_id, item["seq"], item["request_id"], req_state)

    def _materialize(self, batch, item):
        # 자식 생성 시점의 재검증 실패(규칙이 바뀐 뒤 남은 옛 배치 -- 2026-10-01 chown 이름 거부가 첫 사례)는
        # 그 항목만 Rejected 로 종단한다. 예외를 올리면 run_once 가 배치마다 돌다 멈춰 **모든 배치**가 매 틱
        # 막히고, 조용히 건너뛰면 항목이 Queued 로 영원히 남는다. 사유는 항목 reason_code 와 이벤트로.
        try:
            payload, key = build_data_payload(batch["operation"], options=batch["options"],
                                              **item["payload"])
        except DomainValidationError as exc:
            if not self._repos.batches.reject_queued_item(batch["batch_id"], item["seq"],
                                                          reason_code=exc.reason_code,
                                                          expected_payload=item["payload"]):
                return          # 그 사이 지워졌거나 고쳐졌거나 더는 Queued 가 아니다 -- 다음 틱이 새 값으로 판정
            self._repos.observability.record_event(
                component="batch-orchestrator", severity="warning", event_type="batch_item_rejected",
                message=f"batch {batch['batch_id']} item {item['seq']}: {exc.reason_code} -- {exc}"[:500])
            return
        # node_count 는 build_data_payload 에 넣지 않고 build 후 주입한다 — build 의
        # 반환 payload 는 단건 제출 계약(정확 일치 테스트)이고 resource_key 산식에도
        # 영향을 주면 안 된다(실행 제어값은 대상 식별자가 아니다).
        # 미지정(None)은 키 자체를 싣지 않는다 — null(모름) ≠ 0, planner 는 키
        # 부재 = 정책 기본으로 읽는다.
        if batch.get("node_count") is not None:
            payload["node_count"] = batch["node_count"]
        # procs_per_node 도 node_count 와 같은 규칙으로 build 후 주입 — 실행
        # 제어값은 대상 식별자가 아니라 resource_key 산식에 못 들어간다.
        if batch.get("procs_per_node") is not None:
            payload["procs_per_node"] = batch["procs_per_node"]
        # 배치 특권 실행: owner_username 은 node_count 와 같은 규칙으로 build 후
        # 주입한다(단건 payload 계약·resource_key 산식 무영향). None(비특권)은 키
        # 자체를 싣지 않는다 -- null(모름) ≠ 0, 기존 자식 payload 모양 그대로.
        if batch.get("owner_username") is not None:
            payload["owner_username"] = batch["owner_username"]
        priority = resolve_priority(self._repos, batch["operation"], batch.get("priority"))
        # 자식의 auth_method 는 배치 생성 시점의 인증 방식(batches.auth_method)을
        # 물려받는다 -- 기계 고정 "token" 은 세션 배치의 자식까지 비특권으로 눌러
        # LDAP 밖 로컬 admin 배치를 전부 ldap_identity_not_found 로 거부시켰다
        # (실사고). 특권 재검증은 여전히 planner 몫(allowlist + session)이라 이
        # 상속만으로 승격되지 않는다. 구형 배치 행(컬럼 NULL)은 token 폴백 --
        # null(모름) ≠ "session", 모름을 특권 쪽으로 읽지 않는다(fail-closed).
        auth_method = batch.get("auth_method")
        if auth_method is None:
            auth_method = "token"
        # 자식 요청 INSERT 와 항목 claim(→ Materialized)은 한 트랜잭션이다(2026-10-07 리뷰). 스냅샷(list_items)
        # 뒤 관리자가 항목을 고쳤거나 지웠으면 옛 payload 로 자식을 만들고 항목은 새 payload 를 보이는 기록 불일치가
        # 났다 -- 운영자는 화면의 새 경로를 보고 옛 경로의 미리보기를 확인하게 된다. 그래서 트랜잭션 안에서 저장값을
        # 다시 읽어 스냅샷과 같을 때만 만들고, claim 은 같은 저장값 + Queued 가드로 한다(0 행 = 롤백, 다음 틱이 새 값).
        try:
            with self._repos.db.transaction():
                raw = self._repos.batches.queued_item_payload_raw(batch["batch_id"], item["seq"])
                if raw is None or load_json(raw) != item["payload"]:
                    return
                rid = self._repos.requests.insert_pending(
                    operation=batch["operation"], requester_id=batch["requester_id"],
                    actor=batch["actor"], resource_key=key, payload=payload,
                    priority=priority, batch_id=batch["batch_id"],
                    auth_method=auth_method)
                if not self._repos.batches.claim_queued_item(batch["batch_id"], item["seq"], rid,
                                                             payload_raw=raw):
                    raise _ClaimLost()
        except _ClaimLost:
            return

    def _drive(self, batch):
        bid = batch["batch_id"]
        # 틱 스냅샷(list_active)은 앞 배치들을 굴리는 동안 낡는다 -- 그 사이 API 가 취소 → 실행 설정 변경(종단 배치
        # 한정, routes_batches.patch_batch_execution) → 재실행을 끝냈으면, 스냅샷의 옛 동시 실행 상한·옵션·노드 수로
        # 자식을 만들게 된다(적대적 리뷰 2026-10-02 재현). 그래서 굴리기 직전에 배치 행을 다시 읽고, 이미 활성이
        # 아니면(그 사이 취소됨) 건드리지 않는다 -- 낡은 스냅샷으로 취소된 배치를 Completed 로 덮던 창도 함께 좁힌다.
        fresh = self._repos.batches.get(bid)
        if fresh is None or fresh["status"] not in _BATCH_DRIVEN:
            return
        batch = fresh
        items = self._repos.batches.list_items(bid)
        queued, previewing, executing, previewed, terminal = [], [], [], [], 0
        for item in items:
            st = item["status"]
            if st in _ITEM_TERMINAL:
                terminal += 1; continue
            if st == "Queued":
                queued.append(item); continue
            kind, info = self._child_state(item["request_id"])
            if kind == "terminal":
                self._record_terminal(bid, item, info); terminal += 1
            elif kind == "previewed":
                previewed.append((item, info))
            elif kind == "executing":
                executing.append(item)
            else:
                previewing.append(item)
        total = len(items)
        busy = len(previewing) + len(executing)        # 슬롯을 차지하는 자식(미리보기 중 + 실행 중)
        # 상태 전이는 전부 CAS 다(2026-10-07 리뷰): 판정은 위 스냅샷이지만, 그 뒤 API 가 항목을 추가·재실행했거나
        # 배치를 취소했으면 무조건 쓰기가 그것을 덮는다(Queued 를 둔 채 완료·확인 대기가 되면 그 항목은 아무도 집지
        # 않거나, 확인에 묻어 미리보기 없이 실행된다). 0 행이면 다음 틱이 새 스냅샷으로 다시 판정한다.
        if terminal == total:
            self._repos.batches.complete_if_all_terminal(bid, from_status=batch["status"])
            return
        if batch["status"] == "PreviewReady":
            # 확인 대기: 위 기록·완료만(만들기·컨펌·전이 없음). 자동 확인이 켜진 sync 배치만 사람 대신 확인한다 -- rm 은
            # 되돌릴 수 없는 삭제라 늘 사람이 확인한다(auto_confirm_by 를 DB 에 써 넣어도 여기서 제외, 모듈 docstring).
            if batch.get("auto_confirm") is True and batch["operation"] == Operation.SYNC.value:
                self._auto_confirm(batch, total=total, previewed=len(previewed))
            return
        now = utc_now_iso()
        if batch["status"] == "Previewing":
            if not queued and not previewing:         # 전원 previewed·실행 중(또는 종단)
                if previewed:                         # 확인할 미리보기가 있다 → 확인 대기
                    self._repos.batches.mark_preview_ready(bid)
                else:
                    # 확인할 것 없이 이미 확인된 실행 중 자식만 남았다 -- Running 으로 돌려 마저 기록·완료한다.
                    # 안전하다: Running sync 분기는 도장 찍힌 미리보기만 컨펌하고, 새것이 보이면 다시 Previewing.
                    self._repos.batches.set_status_if(bid, "Running", from_states=("Previewing",))
                return
            slots = batch["max_concurrency"] - busy
            for item in queued[:max(0, slots)]:
                self._materialize(batch, item)
            return
        if batch["status"] == "Running":
            # 확인 게이트 연산(sync·rm -- domain.confirm_gated): 도장 찍힌 미리보기만 컨펌한다. 2026-10-21 전엔 sync 만
            # 여기였다 -- rm 이 아래 scan 분기로 떨어지면 확인된 ConfirmPending 자식이 영영 실행되지 않고(만료까지 정체),
            # Running 중 Queued 는 사람 확인 없이 미리보기됐다.
            if confirm_gated(batch["operation"]):
                unstamped = [job for _, job in previewed if not _stamped(job)]
                if queued or previewing or unstamped:
                    # 확인된 배치에 Queued·미리보기 단계 자식·도장 없는 미리보기 = 아무도 보지 않은 경로. 컨펌하면
                    # root 로 돈다 -- 라우트가 이미 Previewing 으로 돌렸어야 하지만(_reopen_after_new_queued) 그 사이
                    # 경합·업그레이드 전 옛 행을 위해 루프가 마지막으로 되돌린다. 실행 중 자식은 그대로 끝까지 돈다.
                    self._repos.batches.set_status_if(bid, "Previewing", from_states=("Running",))
                    return
                slots = batch["max_concurrency"] - busy
                # 만료된 미리보기는 컨펌하지 않고(슬롯도 차지하지 않는다) stepper 가 Rejected 로 끝내게 둔다.
                live = [(item, job) for item, job in previewed if not _preview_expired(job, now)]
                for item, job in live[:max(0, slots)]:
                    self._confirm_child(item, job, now)
                return
            slots = batch["max_concurrency"] - busy
            for item in queued[:max(0, slots)]:
                self._materialize(batch, item)

    def _auto_confirm(self, batch, *, total, previewed):
        """사람의 「배치 확인」(routes_batches.confirm_batch)을 대신한다 -- 같은 저장소 함수, 같은 회차 CAS. 회차는 이 틱에
        다시 읽은 배치 행의 것이다: 그 뒤 항목 추가·재실행으로 Previewing 을 거쳐 회차가 바뀌었으면 0 행이고, 새 회차는
        다음 틱이 판정한다. 0 행이면 라우트처럼 Queued 가 남은 확인 대기를 Previewing 으로 돌린다(자가 치유).
        확인은 이 틱이 본 동의(켠 사람)에 묶는다(expected_auto_confirm_by -- 같은 UPDATE 문장): 읽은 뒤 「자동 실행 끄기」가
        커밋됐거나 켠 사람이 강등·비활성화됐으면 0 행이라 사람 확인을 기다린다."""
        bid, rnd = batch["batch_id"], batch["preview_round"]
        hold = auto_confirm_hold(self._repos, self._settings, batch)
        if hold is not None:
            if _HELD_ROUND.get(bid) != rnd:
                _HELD_ROUND[bid] = rnd
                self._repos.observability.record_event(
                    component="batch-orchestrator", severity="warning", event_type="batch_auto_confirm_held",
                    message=(f"batch {bid} round {rnd}: auto-confirm held ({hold}) -- "
                             f"enabled by {batch.get('auto_confirm_by')}, waiting for a manual batch confirm")[:500])
            return
        # 확인한 미리보기의 대상 수 합계 -- 사람의 확인과 같은 함수·같은 입력(list_items_detail)이라 감사 행 모양이 같다.
        summary = {"previewed": previewed, "items": total, "options": batch["options"],
                   "auto_confirm_by": batch["auto_confirm_by"],
                   **preview_files(self._repos.batches.list_items_detail(bid))}
        if self._repos.batches.confirm(bid, actor=AUTO_CONFIRM_ACTOR, summary=summary, expected_round=rnd,
                                       expected_auto_confirm_by=batch["auto_confirm_by"]):
            _HELD_ROUND.pop(bid, None)
            self._repos.observability.record_event(
                component="batch-orchestrator", severity="info", event_type="batch_auto_confirmed",
                message=(f"batch {bid} round {rnd}: auto-confirmed {previewed}/{total} previewed items "
                         f"(enabled by {batch['auto_confirm_by']})")[:500])
        else:
            self._repos.batches.reopen_if_queued(bid)

    def _confirm_child(self, item, job, now):
        # 운영자의 배치 확인이 찍은 도장(BatchesRepository.confirm)이 지금 미리보기와 같은 자식만 실행한다 -- 지문을
        # 여기서 새로 찍지 않는다(예전엔 지금 지문을 그대로 찍어 "본 적 없는 미리보기" 와 구별이 없었다).
        if _preview_expired(job, now) or not _stamped(job):
            return
        self._repos.data_jobs.set_job_state(job["job_id"], DataJobState.EXECUTING, actor="batch-orchestrator")
