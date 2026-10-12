"""취소의 공통 규칙. 상위 스펙 §5: Volcano 잡 종료가 성공한 뒤에만 DB 를 Cancelled 로
기록한다 — 거짓 취소 금지. 그래서 종료는 DB 변경과 분리된 이 헬퍼가 담당하고, 호출자는
여기서 예외가 나오면 DB 를 건드리지 않고 실패를 보고한다.

커밋 뒤 회수(sweep_after_cancel, 2026-10-12): 취소는 잡을 읽고(스냅숏) → 그 ref 를 끝내고 → Cancelled 를 커밋한다. 그 사이
stepper 가 다음 단계(파드·vcjob)를 제출하면 그 ref 는 스냅숏에 없어 아무도 끝내지 않았다(실행 vcjob 이 Cancelled 잡 밑에서
끝까지 돌았다 -- pod_gc 는 파드만 치운다). stepper 는 set_phase_ref 커밋 **뒤** 상태를 다시 읽고(_reclaim_if_terminal),
취소는 Cancelled 커밋 **뒤** ref 를 다시 읽는다 -- 커밋에는 전순서가 있으므로 둘 중 하나는 반드시 상대의 쓰기를 본다.
"""
from ..domain import DataJobState, TERMINAL_DATA_JOB_STATES
from ..execution import ExecutionError


def terminate_job(adapter, job) -> None:
    if DataJobState(job["state"]) in TERMINAL_DATA_JOB_STATES:
        return
    for ref in (job.get("phase_refs") or {}).values():
        if ref:
            adapter.terminate(ref)


def sweep_after_cancel(repos, adapter, snapshot) -> "str | None":
    """Cancelled 를 커밋한 **뒤** 잡을 다시 읽어 지금 상태를 돌려준다(None = 행 없음).

    지금 상태가 Cancelled 면 스냅숏에 없던 ref(취소 도중 stepper 가 제출한 단계)를 끝낸다 -- 이미 커밋했으니 실패는 되돌릴
    수 없어 숨기지 않고 terminate_failed 이벤트(error)로 남긴다. Cancelled 가 아니면(stepper 가 먼저 종단 -- 성공·실패)
    아무것도 끝내지 않는다: 그 잡의 산출물을 지우지 않고, 호출자가 실제 결과로 요청을 맞추고 409 를 준다(거짓 취소 금지)."""
    cur = repos.data_jobs.get_job(snapshot["job_id"])
    if cur is None:
        return None
    if cur["state"] != DataJobState.CANCELLED.value:
        return cur["state"]
    seen = {ref for ref in (snapshot.get("phase_refs") or {}).values() if ref}
    for phase, ref in (cur.get("phase_refs") or {}).items():
        if ref and ref not in seen:
            try:
                adapter.terminate(ref)
            except ExecutionError as exc:
                repos.observability.record_event(
                    component="api", severity="error", event_type="terminate_failed",
                    message=f"{exc.reason_code}: {phase} {ref} after cancel"[:500],
                    payload={"job_id": snapshot["job_id"], "phase": phase, "ref": ref, "after_cancel": True},
                    request_id=snapshot.get("request_id"))
    return cur["state"]
