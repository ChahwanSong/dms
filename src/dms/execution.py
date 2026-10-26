"""실행 어댑터 경계. 잡 제출/폴링/아티팩트 읽기/종료를 추상화. 3b는 결정적 stub, 3c는 live Volcano."""
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class ExecStatus(StrEnum):
    PENDING = "Pending"
    RUNNING = "Running"
    SUCCEEDED = "Succeeded"
    FAILED = "Failed"
    TIMED_OUT = "TimedOut"


@dataclass(frozen=True)
class JobSpec:
    job_id: str
    phase: str
    operation: str
    tool: str
    dryrun: bool
    identity: dict
    paths: dict
    options: dict
    candidates: dict
    process_count: int
    queue: str
    priority_class: str
    artifact_base: str
    timeout_seconds: int | None = None
    ttl_seconds: int | None = None
    # PV 목적지 root sync 의 결과 소유자 "uid:gid"(2026-10-26 사용자 결정 -- execution_manifests._auto_chown). 규칙
    # (result_owner_rule)이 "storage" 면 제출이 요청의 destination_pv 블록에 기록한 목적지 스토리지 기본 소유자(블록 owner),
    # 그 밖(운영자 옵션 "pv_root"·규칙 이전 옛 행 None)이면 stepper 가 preflight 에서 잰 PV 루트 소유자(worker_pool.dest_pv_owner)
    # 다. 그 밖의 잡은 None.
    result_owner: str | None = None
    # 결과 소유 규칙(요청 destination_pv 블록의 owner_rule 그대로 -- "storage" | "pv_root" | None(옛 행)). "storage" 만 측정을
    # 건너뛴다(execution_manifests.pv_owner_measured) -- 나머지는 종전처럼 PV 루트 소유자를 잰다.
    result_owner_rule: str | None = None
    # 직접 경로 목적지의 스토리지 기본 소유자 규칙인가(2026-10-26 사용자 결정 (4) -- 요청 payload.dest_owner 의 rule "storage",
    # stepper 가 PV 목적지 블록이 없을 때만 True 로 싣는다). True 면 result_owner_rule 은 "storage"·result_owner 는 그 블록 owner 이고,
    # paths["contain"] 에 목적지가 없어도 --chown 을 싣는다(execution_manifests.direct_dest_owner_applies -- 측정은 없다).
    result_owner_direct: bool = False


class ExecutionError(Exception):
    def __init__(self, reason_code: str, detail: str = ""):
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(f"{reason_code}: {detail}" if detail else reason_code)


class ExecutionAdapter(Protocol):
    def submit(self, spec: JobSpec) -> str: ...
    def poll(self, ref: str) -> ExecStatus: ...
    def read_summary(self, ref: str) -> "dict | None": ...
    def terminate(self, ref: str) -> None: ...
    def read_log(self, ref: str) -> "list[tuple[str, str | None, str | None]]": ...


class StubExecutionAdapter:
    def __init__(self):
        self._jobs = {}
        self._scripts = {}
        self._summaries = {}
        self._logs = {}
        self._fail_submit_phase = None
        self._fail_terminate_refs = set()
        self._submitted = []

    def submit(self, spec: JobSpec) -> str:
        if self._fail_submit_phase is not None and spec.phase == self._fail_submit_phase:
            raise ExecutionError("submit_failed", spec.phase)
        ref = f"stub-{spec.phase}-{spec.job_id}"
        self._jobs[ref] = {"terminated": False}
        self._submitted.append(spec)
        return ref

    def poll(self, ref: str) -> ExecStatus:
        if self._jobs.get(ref, {}).get("terminated"):
            return ExecStatus.FAILED
        queue = self._scripts.get(ref)
        if queue:
            return queue.pop(0)
        return ExecStatus.SUCCEEDED

    def read_summary(self, ref: str):
        return self._summaries.get(ref, {"files": 0, "bytes": 0})

    def terminate(self, ref: str) -> None:
        if ref in self._fail_terminate_refs:
            raise ExecutionError("terminate_failed", ref)
        if ref in self._jobs:
            self._jobs[ref]["terminated"] = True

    def read_log(self, ref: str):
        # 3-튜플 (pod, log, waiting_reason) -- 실 어댑터와 같은 계약(설계 §4).
        # 스텁은 클러스터가 없으므로 waiting_reason 을 알 수 없다 -- None.
        return self._logs.get(ref, [(ref, "", None)])

    # --- test helpers ---
    def script(self, ref, statuses):
        self._scripts[ref] = list(statuses)

    def set_summary(self, ref, summary):
        self._summaries[ref] = summary

    def set_log(self, ref, entries):
        self._logs[ref] = list(entries)

    def fail_submit(self, phase):
        self._fail_submit_phase = phase

    def fail_terminate(self, ref):
        self._fail_terminate_refs.add(ref)

    def submitted_specs(self):
        return list(self._submitted)
