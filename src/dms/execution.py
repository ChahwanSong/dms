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
    def read_log(self, ref: str) -> "list[tuple[str, str | None]]": ...


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
        return self._logs.get(ref, [(ref, "")])

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
