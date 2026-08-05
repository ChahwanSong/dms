"""취소의 공통 규칙. 상위 스펙 §5: Volcano 잡 종료가 성공한 뒤에만 DB 를 Cancelled 로
기록한다 — 거짓 취소 금지. 그래서 종료는 DB 변경과 분리된 이 헬퍼가 담당하고, 호출자는
여기서 예외가 나오면 DB 를 건드리지 않고 실패를 보고한다."""
from ..domain import DataJobState, TERMINAL_DATA_JOB_STATES


def terminate_job(adapter, job) -> None:
    if DataJobState(job["state"]) in TERMINAL_DATA_JOB_STATES:
        return
    for ref in (job.get("phase_refs") or {}).values():
        if ref:
            adapter.terminate(ref)
