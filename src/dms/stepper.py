"""job-stepper: 계획된 data_job을 비블로킹 스텝으로 전진시키는 루프 본체. 실행은 어댑터 뒤."""
import hashlib
import json
import sys

from .db import iso_plus, utc_now_iso
from .domain import DataJobState, TERMINAL_DATA_JOB_STATES
from .execution import ExecStatus, ExecutionError, JobSpec
from .placement import TOOL_TO_POLICY


def _summary_fingerprint(summary):
    if not summary:
        return None
    payload = json.dumps(summary, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


class JobStepper:
    def __init__(self, repos, execution_adapter, *, settings):
        self._repos = repos
        self._exec = execution_adapter
        self._settings = settings

    def run_once(self) -> dict:
        control = self._repos.control.control_state()
        if control and control["drain"]:
            return {}
        results = {}
        for job in self._repos.data_jobs.claim_steppable():
            jid = job["job_id"]
            try:
                results[jid] = self._step_one(job)
            except Exception as exc:
                print(f"stepper error on {jid}: {type(exc).__name__}: {exc}",
                      file=sys.stderr)
                results[jid] = f"error:{type(exc).__name__}"
        return results

    def _abs(self, storage_name, rel):
        storage = self._repos.storages.get(storage_name)
        if storage and storage.get("managed_root"):
            return f"{storage['managed_root']}/{rel}"
        return rel

    def _build_spec(self, job, phase, dryrun):
        wp = job["worker_pool"] or {}
        op = job["operation"]
        if op == "sync":
            paths = {"source": self._abs(job["source_storage"], job["source"]),
                     "source_storage": job["source_storage"],
                     "destination": self._abs(job["destination_storage"], job["destination"]),
                     "destination_storage": job["destination_storage"]}
        else:
            paths = {"target": self._abs(job["storage_name"], job["target"]),
                     "storage": job["storage_name"]}
        # job["tool"]은 실행 파일 이름(dscan/dsync/nsync/drm)이지 정책 키(scan/dsync/
        # nsync/rm)가 아니다 -- planner.py가 policy를 조회할 때 쓰는 것과 동일한
        # TOOL_TO_POLICY 매핑을 거쳐야 scan/rm 잡의 정책을 정확히 찾는다.
        # 알 수 없는 tool은 정책 조회를 KeyError로 터뜨리는 대신 "타임아웃 없음"으로
        # 다룬다 — 여기서 raise하면 그 잡은 매 틱 같은 예외로 스텝이 막혀 영구히 낀다.
        policy_key = TOOL_TO_POLICY.get(job["tool"])
        policy = (self._repos.control.get_policy(policy_key)
                  if policy_key is not None else None)
        if policy is None:
            timeout = None
        elif phase == "execution":
            timeout = policy["execution_timeout_seconds"]
        else:
            timeout = policy["preview_timeout_seconds"]
        return JobSpec(
            job_id=job["job_id"], phase=phase, operation=op, tool=job["tool"],
            dryrun=dryrun, identity=wp.get("identity", {}), paths=paths,
            options=job["options"] or {}, candidates=wp.get("candidates", {}),
            process_count=wp.get("process_count", 1), queue=wp.get("queue", "dms-data"),
            priority_class=wp.get("priority_class", "dms-mid"),
            artifact_base=self._settings.artifact_base_uri, timeout_seconds=timeout,
            ttl_seconds=self._settings.vcjob_ttl_seconds)

    def _finalize(self, job, job_state, *, reason_code=None, summary=None):
        self._repos.data_jobs.set_job_state(job["job_id"], job_state,
                                            reason_code=reason_code, actor="stepper")
        self._repos.requests.finalize_from_job(
            job["request_id"], job_state, reason_code=reason_code, summary=summary,
            actor="stepper")

    def _reclaim_if_terminal(self, job, ref):
        """제출 직후 잡이 이미 종단이면(= claim과 제출 사이에 취소가 들어왔다) 방금 만든
        Pod/vcjob을 즉시 회수하고 현재 상태를 돌려준다. None이면 계속 진행해도 된다.

        claim_steppable의 스냅샷에는 잠금이 없다 — 커넥션이 autocommit이라
        FOR UPDATE SKIP LOCKED가 곧바로 풀린다. 그 창에서 취소된 잡도 _step_one이
        그대로 제출해 버리고, 뒤따르는 set_job_state는 종단 가드가 삼키므로 클러스터에만
        고아가 남는다. 그 고아는 아무도 못 치운다 — cancel_job은 종단 잡에 409,
        terminate_job은 종단 잡에 no-op이기 때문. 그래서 여기서 한 번 더 읽는다."""
        current = self._repos.data_jobs.get_job(job["job_id"])
        state = (current or job)["state"]
        if DataJobState(state) not in TERMINAL_DATA_JOB_STATES:
            return None
        try:
            self._exec.terminate(ref)
        except ExecutionError:
            pass  # best-effort — 잡은 이미 종단이라 더 기록할 상태가 없다
        return state

    def _step_one(self, job) -> str:
        state = job["state"]
        jid = job["job_id"]
        if state == DataJobState.PENDING.value:
            return self._submit_preflight(job)
        if state == DataJobState.PREFLIGHT.value:
            return self._poll_preflight(job)
        if state == DataJobState.RUNNING.value:
            return self._poll_execution(job)
        if state == DataJobState.PREVIEW_RUNNING.value:
            return self._poll_preview(job)
        if state == DataJobState.EXECUTING.value:
            return self._poll_or_submit_execution(job)
        return state

    def _submit_preflight(self, job):
        jid = job["job_id"]
        try:
            ref = self._exec.submit(self._build_spec(job, "preflight", dryrun=False))
        except ExecutionError as exc:
            self._finalize(job, DataJobState.REJECTED,
                           reason_code=f"preflight_submit_failed:{exc.reason_code}")
            return "Rejected"
        self._repos.data_jobs.set_phase_ref(jid, "preflight", ref)
        reclaimed = self._reclaim_if_terminal(job, ref)
        if reclaimed is not None:
            return reclaimed
        self._repos.data_jobs.set_job_state(jid, DataJobState.PREFLIGHT, actor="stepper")
        return "Preflight"

    def _poll_preflight(self, job):
        jid = job["job_id"]
        ref = (job["phase_refs"] or {}).get("preflight")
        status = self._exec.poll(ref)
        if status in (ExecStatus.PENDING, ExecStatus.RUNNING):
            return "Preflight"
        if status == ExecStatus.SUCCEEDED:
            # scan: 바로 execution. (sync/rm preview는 Task 7)
            if job["operation"] == "scan":
                return self._submit_execution(job, DataJobState.RUNNING)
            return self._submit_preview(job)  # Task 7에서 구현
        self._finalize(job, DataJobState.REJECTED, reason_code="preflight_failed")
        return "Rejected"

    def _submit_execution(self, job, running_state):
        jid = job["job_id"]
        try:
            ref = self._exec.submit(self._build_spec(job, "execution", dryrun=False))
        except ExecutionError as exc:
            self._finalize(job, DataJobState.FAILED,
                           reason_code=f"execution_submit_failed:{exc.reason_code}")
            return "Failed"
        self._repos.data_jobs.set_phase_ref(jid, "execution", ref)
        reclaimed = self._reclaim_if_terminal(job, ref)
        if reclaimed is not None:
            return reclaimed
        self._repos.data_jobs.set_job_state(jid, running_state, actor="stepper")
        return running_state.value

    def _poll_execution(self, job):
        ref = (job["phase_refs"] or {}).get("execution")
        status = self._exec.poll(ref)
        if status in (ExecStatus.PENDING, ExecStatus.RUNNING):
            return job["state"]
        if status == ExecStatus.SUCCEEDED:
            summary = self._exec.read_summary(ref)
            if summary is None:
                # 정상 잡은 job-runner가 summary.json을 항상 쓴다. None은 컨트롤러가
                # artifact_base 파일시스템을 못 읽는 배포 오구성을 뜻한다 — vcjob phase가
                # 권위이므로 SUCCEEDED는 유지하되, null을 조용히 묻지 않고 가시화한다.
                summary = {"summary_unavailable": True}
            # 성공 경로도 URI를 남긴다 — preview를 거치지 않는 scan 잡은 여기서만
            # 기록되고, 없으면 포탈이 아티팩트를 가리킬 수 없다.
            self._repos.data_jobs.set_artifact(
                job["job_id"],
                artifact_uri=f"{self._settings.artifact_base_uri}/{job['job_id']}",
                result_summary=summary)
            self._finalize(job, DataJobState.SUCCEEDED, summary=summary)
            return "Succeeded"
        target = (DataJobState.TIMED_OUT if status == ExecStatus.TIMED_OUT
                  else DataJobState.FAILED)
        self._finalize(job, target, reason_code="execution_failed")
        return target.value

    def _submit_preview(self, job):
        jid = job["job_id"]
        try:
            ref = self._exec.submit(self._build_spec(job, "preview", dryrun=True))
        except ExecutionError as exc:
            self._finalize(job, DataJobState.FAILED,
                           reason_code=f"preview_submit_failed:{exc.reason_code}")
            return "Failed"
        self._repos.data_jobs.set_phase_ref(jid, "preview", ref)
        reclaimed = self._reclaim_if_terminal(job, ref)
        if reclaimed is not None:
            return reclaimed
        self._repos.data_jobs.set_job_state(jid, DataJobState.PREVIEW_RUNNING,
                                            actor="stepper")
        return "PreviewRunning"

    def _poll_preview(self, job):
        jid = job["job_id"]
        ref = (job["phase_refs"] or {}).get("preview")
        status = self._exec.poll(ref)
        if status in (ExecStatus.PENDING, ExecStatus.RUNNING):
            return "PreviewRunning"
        if status == ExecStatus.SUCCEEDED:
            summary = self._exec.read_summary(ref)
            fingerprint = _summary_fingerprint(summary)
            if fingerprint is None:
                self._finalize(job, DataJobState.REJECTED, reason_code="empty_preview")
                return "Rejected"
            expires = iso_plus(utc_now_iso(), self._settings.preview_ttl_seconds)
            artifact = f"{self._settings.artifact_base_uri}/{jid}"
            self._repos.data_jobs.set_preview(jid, fingerprint=fingerprint,
                                              expires_at=expires, artifact_uri=artifact)
            self._repos.data_jobs.set_job_state(jid, DataJobState.CONFIRM_PENDING,
                                                actor="stepper")
            return "ConfirmPending"
        if status == ExecStatus.TIMED_OUT:
            self._finalize(job, DataJobState.TIMED_OUT, reason_code="preview_timed_out")
            return "TimedOut"
        self._finalize(job, DataJobState.FAILED, reason_code="preview_failed")
        return "Failed"

    def _poll_or_submit_execution(self, job):
        jid = job["job_id"]
        refs = job["phase_refs"] or {}
        if "execution" in refs:
            return self._poll_execution(job)
        # confirm 후 execution 전 preflight 재검증 (Phase 3b 파킹 백로그).
        # phase="exec_preflight"(초기 preflight의 "preflight"와 구분) — build_preflight_pod의
        # 파드 이름이 phase를 포함하므로, 초기 preflight 파드가 아직 남아 있어도 이름이
        # 충돌하지 않는다(안 그러면 create가 AlreadyExists→submit_failed로 실패).
        if "exec_preflight" not in refs:
            try:
                ref = self._exec.submit(self._build_spec(job, "exec_preflight", dryrun=False))
            except ExecutionError as exc:
                self._finalize(job, DataJobState.FAILED,
                               reason_code=f"execution_recheck_submit_failed:{exc.reason_code}")
                return "Failed"
            self._repos.data_jobs.set_phase_ref(jid, "exec_preflight", ref)
            reclaimed = self._reclaim_if_terminal(job, ref)
            if reclaimed is not None:
                return reclaimed
            return "Executing"
        status = self._exec.poll(refs["exec_preflight"])
        if status in (ExecStatus.PENDING, ExecStatus.RUNNING):
            return "Executing"
        if status == ExecStatus.SUCCEEDED:
            return self._submit_execution(job, DataJobState.EXECUTING)
        self._finalize(job, DataJobState.REJECTED, reason_code="execution_recheck_failed")
        return "Rejected"
