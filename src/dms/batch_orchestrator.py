"""배치 오케스트레이터: 배치 자식(item)을 실제 request로 throttle-materialize하고,
자식 종단 상태를 집계해 배치를 완료시키는 controller-loop.

스캔 경로: Running 배치에서 max_concurrency - in_flight 만큼 Queued item을
materialize하고, 자식 request가 종단이면 item을 종단화 + counts를 bump하며,
전 item이 종단이면 배치를 Completed로 전이한다.

sync 경로: Previewing 배치에서 Queued item을 쓰로틀 materialize해 preview를 진행시키고,
Queued 도 미리보기 단계 자식도 없고 확인할 미리보기(ConfirmPending)가 하나라도 있으면 배치를
PreviewReady로 전이한다(mark_preview_ready -- 한 문장 CAS + 확인 회차 +1). 이미 확인받아 실행 중인
자식(Executing)은 PreviewReady 를 막지 않는다 -- Running 에서 항목 추가 등으로 Previewing 에 돌아온
배치가 가장 긴 자식이 끝날 때까지 재확인조차 못 하던 정체(2026-10-07 리뷰). 확인할 미리보기 없이
실행 중 자식만 남았으면(추가한 항목이 지워졌거나 미리보기가 거부됨) 확인 대기가 아니라 Running 으로
돌려 마저 기록·완료한다. 슬롯은 실행 중 자식도 차지한다(동시 실행 상한 유지). 확인 대기(PreviewReady)
배치도 루프가 **기록만** 하러 돈다(list_awaiting_confirm): 그 사이 끝난 실행 중 자식을 항목에 남기고 전부
끝났으면 완료한다 -- 확인 대기는 사람이 누를 때까지 길어서, 기록하지 않으면 실제로 돈 자식이 Materialized
로 남아 실패분 재실행·완료가 막히고 취소가 "취소됨" 으로 덮었다. 운영자가 배치를 Running으로 confirm하면(확인 회차 CAS + 그 순간의
ConfirmPending 자식에 확인 도장) 남은 슬롯만큼 **도장이 지금 미리보기와 같은** 자식을 쓰로틀
confirm(`_confirm_child`)한다 -- 새 미리보기는 하나도 만들지 않는다: Running sync 배치에 Queued·미리보기
단계 자식·도장 없는 미리보기가 보이면(라우트의 Previewing 복귀를 놓친 경합, 업그레이드 전 옛 코드가 남긴
행) 아무것도 컨펌하지 않고 배치를 Previewing 으로 되돌린다(사람이 보지 않은 미리보기의 자동 컨펌 차단).

미리보기 만료: 배치 자식도 stepper expire_previews 가 PreviewExpired → 요청 Rejected(preview_expired)로
끝내고, 다음 틱 _record_terminal 이 항목을 Rejected·실패 1 로 센다(Previewing·PreviewReady·Running 모두 --
stepper 가 같은 컨트롤러 패스에서 먼저 돈다). 다시 미리보기는 자동으로 하지 않는다: 재실행은 :rerun-failed
→ Previewing → 재확인. Running 에선 만료된 미리보기를 컨펌하지 않는다(슬롯도 차지하지 않는다).
"""
from .db import load_json, utc_now_iso
from .domain import (DomainValidationError, Operation, RequestState, TERMINAL_REQUEST_STATES,
                     DataJobState, build_data_payload, resolve_priority)

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


class BatchOrchestrator:
    def __init__(self, repos, *, settings):
        self._repos = repos
        self._settings = settings

    def run_once(self):
        for batch in self._repos.batches.list_active() + self._repos.batches.list_awaiting_confirm():
            self._drive(batch)

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
        with self._repos.db.transaction():
            if req_state == RequestState.SUCCEEDED.value:
                self._repos.batches.set_item_status(batch_id, item["seq"], "Succeeded")
                self._repos.batches.bump_counts(batch_id, succeeded=1)
            elif req_state == RequestState.CANCELLED.value:
                # 취소는 성공도 실패도 아니다 — 카운터를 올리지 않는다.
                # reason_code 는 상태값을 되풀이하지 않고 요청의 실제 종단 사유를 옮긴다
                # (없으면 NULL — 바로 옆 StatusPill과 중복 표시하지 않는다).
                self._repos.batches.set_item_status(
                    batch_id, item["seq"], "Cancelled",
                    reason_code=self._repos.requests.last_reason_code(item["request_id"]))
            else:
                status = "Rejected" if req_state == RequestState.REJECTED.value else "Failed"
                self._repos.batches.set_item_status(
                    batch_id, item["seq"], status,
                    reason_code=self._repos.requests.last_reason_code(item["request_id"]))
                self._repos.batches.bump_counts(batch_id, failed=1)

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
            return                                    # 확인 대기: 위 기록·완료만(만들기·컨펌·전이 없음)
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
            if batch["operation"] == Operation.SYNC.value:
                unstamped = [job for _, job in previewed if not _stamped(job)]
                if queued or previewing or unstamped:
                    # 확인된 sync 배치에 Queued·미리보기 단계 자식·도장 없는 미리보기 = 아무도 보지 않은 경로. 컨펌하면
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

    def _confirm_child(self, item, job, now):
        # 운영자의 배치 확인이 찍은 도장(BatchesRepository.confirm)이 지금 미리보기와 같은 자식만 실행한다 -- 지문을
        # 여기서 새로 찍지 않는다(예전엔 지금 지문을 그대로 찍어 "본 적 없는 미리보기" 와 구별이 없었다).
        if _preview_expired(job, now) or not _stamped(job):
            return
        self._repos.data_jobs.set_job_state(job["job_id"], DataJobState.EXECUTING, actor="batch-orchestrator")
