"""job-stepper: 계획된 data_job을 비블로킹 스텝으로 전진시키는 루프 본체. 실행은 어댑터 뒤."""
import hashlib
import json
import posixpath
import sys

from .artifact_base import resolve_artifact_base
from .db import iso_plus, utc_now_iso
from .domain import DataJobState, TERMINAL_DATA_JOB_STATES
from .execution import ExecStatus, ExecutionError, JobSpec
from .execution_manifests import parse_preflight_reason
from .placement import TOOL_TO_POLICY


def _summary_fingerprint(summary):
    if not summary:
        return None
    payload = json.dumps(summary, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


# 슬라이스 25 §2.2: 진단 로그 박제 상한. 파드당 꼬리 16KB x 항목 4 = 총 64KB --
# builds.LOG_TEXT_MAX(64KB)와 같은 총량 규약이다(계약 테스트가 곱을 고정한다).
# 상한 없는 박제는 다행 조회에서 이미 격리했더라도(리포지토리 몫) DB 자체를
# 부풀린다 -- 꼬리를 남기는 이유는 트레이스백·실패 사유가 끝에 몰리기 때문.
DIAG_TAIL_BYTES = 16 * 1024
DIAG_MAX_ENTRIES = 4


def _diag_entry(pod, log):
    """박제 항목 하나. log=None(얻을 수 없었다)은 None 그대로 저장한다 --
    "박제 시점에 이미 없었다"는 사실 자체가 진단이다. 빈 문자열은 정상값이라
    truthy 검사를 쓰지 않는다(설계 §4). 꼬리 자르기는 바이트 기준이다."""
    if log is None:
        return {"pod": pod, "log": None, "truncated": False}
    raw = log.encode()
    if len(raw) <= DIAG_TAIL_BYTES:
        return {"pod": pod, "log": log, "truncated": False}
    tail = raw[-DIAG_TAIL_BYTES:]
    # 경계에서 코드포인트 가운데가 잘렸으면 조각을 **버리고** 물러난다. 그냥
    # errors="replace" 로 넘기면 1~3바이트 조각이 U+FFFD(3바이트)로 부풀어
    # 결과가 도리어 DIAG_TAIL_BYTES 를 넘는다 -- 상한이 상한이 아니게 된다.
    # 게다가 원본에 없던 깨진 글자를 진단 로그에 심는 셈이라(한국어 실패 사유가
    # 흔하다) 버리는 쪽이 정직하다. log 는 str 이라 raw 는 항상 정상 UTF-8 이고,
    # 선두 연속 바이트를 걷어낸 접미사는 그대로 디코드된다 -- replace 는 방어용.
    cut = 0
    while cut < len(tail) and 0x80 <= tail[cut] < 0xC0:
        cut += 1
    return {"pod": pod,
            "log": tail[cut:].decode("utf-8", errors="replace"),
            "truncated": True}


class StorageMissingAtStep(Exception):
    """_abs 가 storage 행/managed_root 를 찾지 못했다 -- 요청 시점엔 있었는데
    스텝 시점에 없다는 뜻이다(행 삭제 또는 직접 DB 조작; 라우트 update 는 가드가
    막는다 -- 슬라이스 24 §2.4). 예전 폴백(상대경로 반환, 로그 0건)은 dsync 를
    launcher cwd 기준 컨테이너 오버레이에 쓰고 SUCCEEDED 로 끝내는 조용한 데이터
    증발이었고 drm 이면 cwd 기준 상대 삭제였다 -- 예외로 끊고 종단시킨다."""

    def __init__(self, storage_name):
        self.storage_name = storage_name
        super().__init__(f"storage {storage_name!r} missing at step time")


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
                # 전이를 남기지 못한 실패 -- stderr로만 새면 파드 재시작에 사라진다.
                self._repos.observability.record_event(
                    component="stepper", severity="error", event_type="step_error",
                    message=f"{type(exc).__name__}: {exc}"[:500],
                    request_id=job.get("request_id"))
        return results

    def _abs(self, storage_name, rel):
        storage = self._repos.storages.get(storage_name)
        root = (storage or {}).get("managed_root")
        if root is None or root == "":
            # 컬럼이 NOT NULL(migrations.py:209)이라 여기 도달은 사실상 "행
            # 삭제"와 직접 DB 조작뿐이다(설계 §1-8). 폴백 금지 -- fail-closed.
            raise StorageMissingAtStep(storage_name)
        # f-string 결합이 아니라 join(설계 §2.2): 검증 이전에 DB 에 남아 있을 수
        # 있는 root "/" 행에서 f"{root}/{rel}" 은 "//rel" 을 만들고 POSIX 는
        # "//" 를 구현 정의로 취급한다(문자열 비교 계열 -- 감사 로그·아티팩트
        # 표시 -- 와도 어긋난다). normpath 후처리는 "//x" 를 보존해서(실측)
        # 대안이 못 된다. 정상 root 에선 출력이 동일하다(test_stepper_enrich 앵커).
        # lstrip("/") 이 붙는 이유: join 은 둘째 인자가 절대경로면 root 를 **버린다**
        # (join("/cephfs/dms", "/etc") == "/etc"). 요청 경로는 validate_relative_path
        # (domain.py:79)가 절대경로를 막지만 create_job 은 무검증 INSERT 라 DB 가
        # 신뢰 경계다(§1-1) -- 변조된 절대 target 이 그대로 실리면 drm 이
        # managed_root 밖을 지운다. 기존 f-string 은 "/cephfs/dms//etc" 로 안에
        # 가뒀었고, 그 봉쇄를 join 치환의 부수효과로 잃을 수는 없다.
        return posixpath.join(root, rel.lstrip("/"))

    def _artifact_base(self):
        # 슬라이스 18: DB 가 env 를 이긴다(설계 §2.1). JobStepper 는 매 틱
        # 재생성되고 정책도 매 틱 DB 재조회라 이 조회가 새 비용을 만들지 않는다.
        # 스냅숏을 들고 있으면 base 변경이 컨트롤러 재시작 전까지 반영되지
        # 않는다(설계 §1-7).
        return resolve_artifact_base(self._repos.control, self._settings)

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
        # 미지 tool 은 _step_one 층1 가드(슬라이스 24)가 이미 종단시켰으므로 여기서
        # 직접 인덱싱해도 KeyError 불능이다. policy None 은 이제 "정책 행이 지워진"
        # 운영 조작뿐이라 크래시 대신 타임아웃 없음으로 관용한다(기존 동작 유지).
        policy = self._repos.control.get_policy(TOOL_TO_POLICY[job["tool"]])
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
            artifact_base=self._artifact_base(), timeout_seconds=timeout,
            ttl_seconds=self._settings.vcjob_ttl_seconds)

    def _finalize(self, job, job_state, *, reason_code=None, summary=None, diag=None):
        # 슬라이스 25 §2.2: diag=(phase, ref) 가 오면 종단 전이 **전에** 박제한다.
        # 순서가 계약이다 -- 박제 후 크래시하면 잡이 비종단으로 남아 다음 틱이
        # finalize 를 재시도하고(archive 는 IS NULL 이 중복을 막는다), 역순이면
        # 종단 잡은 다시 스텝되지 않아 박제 기회가 영영 사라진다.
        if diag is not None:
            self._archive_diag(job, *diag)
        self._repos.data_jobs.set_job_state(job["job_id"], job_state,
                                            reason_code=reason_code, actor="stepper")
        self._repos.requests.finalize_from_job(
            job["request_id"], job_state, reason_code=reason_code, summary=summary,
            actor="stepper")

    def _archive_diag(self, job, phase, ref):
        """실패 종단 시점 파드 로그 박제(설계 §2.2). 어댑터가 launcher 를 앞에
        놓으므로 [:DIAG_MAX_ENTRIES] 상한이 잘라도 launcher 가 산다. 박제 실패는
        finalize 를 막지 않는다 -- 한 잡의 로그 때문에 종단 전이가 막히면 잡이
        낀다 -- 대신 이벤트로 표면화한다(조용한 실패 금지, 설계 §4).

        반환값은 read_log 원본(못 읽었으면 None)이다 -- preflight 경로가 같은
        조회 결과에서 사유 마커까지 뽑는다(_preflight_reason). 두 번 읽으면
        박제된 로그와 사유가 어긋날 수 있고(그 사이 파드 GC) k8s 조회도 공짜가
        아니다. raw 를 먼저 잡아두는 이유: 박제(archive_diag_logs)만 실패해도
        사유 승격은 살아야 한다."""
        raw = None
        try:
            raw = self._exec.read_log(ref)
            entries = [_diag_entry(pod, log)
                       for pod, log, _wr in raw[:DIAG_MAX_ENTRIES]]
            self._repos.data_jobs.archive_diag_logs(job["job_id"], phase=phase,
                                                    entries=entries)
        except Exception as exc:
            self._repos.observability.record_event(
                component="stepper", severity="warning",
                event_type="diag_archive_failed",
                message=f"{type(exc).__name__}: {exc}"[:500],
                payload={"phase": phase, "ref": ref},
                request_id=job.get("request_id"))
        return raw

    def _preflight_reason(self, job, phase, ref, *, reason_code):
        """preflight 실패의 사유 결정 + 진단 로그 박제(한 번의 read_log 로 둘 다).

        인자 이름이 fallback 이 아니라 reason_code 인 이유: 사유 코드 커버리지
        그물(tests/test_reason_codes_coverage.py)은 `reason_code=` 키워드 리터럴만
        AST 로 추출한다 -- 이름을 바꾸면 폴백 두 코드가 그물 밖으로 빠진다.

        스크립트가 찍은 DMS_PREFLIGHT_REASON= 마커를 잡 사유로 승격한다. 승격
        경로가 없던 동안 모든 preflight 실패는 fallback 하나로 뭉개졌다 --
        "목적지 부모가 없다"와 "소스를 읽을 수 없다"가 화면에서 구분되지 않아
        운영자가 파드 로그를 직접 열어야 했고, 파드는 GC 로 시한부다.

        마커가 없거나 화이트리스트(execution_manifests.PREFLIGHT_REASONS) 밖이면
        넘어온 reason_code 로 접는다 -- 파드 로그는 신뢰 입력이 아니라(설계 §4)
        임의 문자열을 사유에 박으면 프론트 매핑이 없어 원문 코드가 그대로
        노출된다. 박제 순서는 그대로 유지된다: 여기서 박제한 뒤 _finalize 가
        전이하므로 "박제 -> set_job_state" 계약이 깨지지 않는다."""
        raw = self._archive_diag(job, phase, ref)
        promoted = parse_preflight_reason(raw)
        return reason_code if promoted is None else promoted

    def _surface_failed_artifact(self, job, ref):
        """실패 잡의 summary 표면화(설계 §2.4). 러너는 도구 비0 종료에도
        summary.json 을 쓰고 exit 하므로(§1-7) returncode 가 카드에 뜬다.
        read_summary 예외는 None(모름)으로 접는다 -- 실패 잡의 보강은 best-effort
        고, 여기서 던지면 종단 전이 자체가 막혀 run_once 의 step_error 루프(매 틱
        재시도)에 낀다. None 이면 기록하지 않는다 -- artifact_uri 를 지어내면
        포탈이 존재하지 않는 디렉터리를 가리킨다. 비교가 `is not None` 인 것은
        이 슬라이스의 규칙 그대로다: 빈 summary({})는 "러너가 쓰긴 썼다"는
        정상값이라 모름과 뭉개지 않는다. metrics 는 오염되지 않는다(합계가
        state='Succeeded' 필터 -- §1-12)."""
        try:
            summary = self._exec.read_summary(ref)
        except Exception:
            summary = None
        if summary is not None:
            self._repos.data_jobs.set_artifact(
                job["job_id"],
                artifact_uri=f"{self._artifact_base()}/{job['job_id']}",
                result_summary=summary)

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
        except ExecutionError as exc:
            # best-effort -- 잡은 이미 종단이라 더 기록할 상태가 없다. 그래도 고아
            # 리소스가 남았을 수 있으니 진단 채널에는 남긴다.
            self._repos.observability.record_event(
                component="stepper", severity="warning", event_type="terminate_failed",
                message=exc.reason_code, payload={"ref": ref},
                request_id=job.get("request_id"))
        return state

    # FAILED 로 갈리는 상태: 실행 자원이 이미 붙었다(execution vcjob 제출 이후).
    # 그 전 단계는 REJECTED -- preflight_submit_failed→REJECTED /
    # execution_submit_failed→FAILED 의 기존 대칭을 그대로 따른다(설계 §2.1).
    _EXEC_STATES = (DataJobState.EXECUTING.value, DataJobState.RUNNING.value)

    def _fail_closed(self, job, *, reason_code):
        """신뢰 경계가 깨진 잡(미지 tool·스텝 시점 스토리지 결측)의 종단 처리.

        살아 있을 수 있는 phase_refs 는 _reclaim_if_terminal 관례대로 best-effort
        terminate 하고, 실패는 terminate_failed 이벤트로 남긴다 -- 고아 리소스를
        조용히 두지 않는다(설계 §4). 이미 끝난 파드의 terminate 는 무해하다."""
        target = (DataJobState.FAILED if job["state"] in self._EXEC_STATES
                  else DataJobState.REJECTED)
        for ref in (job["phase_refs"] or {}).values():
            try:
                self._exec.terminate(ref)
            except ExecutionError as exc:
                self._repos.observability.record_event(
                    component="stepper", severity="warning",
                    event_type="terminate_failed", message=exc.reason_code,
                    payload={"ref": ref}, request_id=job.get("request_id"))
        self._finalize(job, target, reason_code=reason_code)
        return target.value

    def _step_one(self, job) -> str:
        # 슬라이스 24 §2.1 층1: tool 의 유일한 정상 원천은 placement 의 리터럴
        # 4종이고 create_job 은 무검증 INSERT 다(§1-1) -- DB 가 신뢰 경계다.
        # 미지 tool 이 층2 이전의 fall-through 를 타면 drm 꼴 argv(파괴적)로
        # 실행되므로 제출 전에 종단시킨다. 이 가드로 _build_spec 의 "미지 tool ->
        # 타임아웃 없음" 관용 분기는 도달 불능이 되어 제거했다 -- 그 주석이
        # 걱정한 "매 틱 예외로 영구히 낀 잡"은 종단이라 애초에 생기지 않는다.
        if job["tool"] not in TOOL_TO_POLICY:
            return self._fail_closed(job, reason_code="unknown_tool")
        try:
            return self._dispatch(job)
        except StorageMissingAtStep as exc:
            # 종단 전이의 reason_code 만으론 "어느 스토리지가 없었는지"가 남지
            # 않는다 -- 이벤트로 보강한다(설계 §2.4). run_once 의 step_error
            # (매 틱 재시도 루프)와 달리 여기는 종단이라 한 번만 남는다.
            self._repos.observability.record_event(
                component="stepper", severity="error",
                event_type="storage_missing_at_step",
                message=f"storage={exc.storage_name} job={job['job_id']}",
                request_id=job.get("request_id"))
            return self._fail_closed(job, reason_code="storage_missing_at_step")

    def _dispatch(self, job) -> str:
        state = job["state"]
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
        # diag= 를 넘기지 않는 이유: _preflight_reason 이 이미 박제했다(전이 전).
        reason = self._preflight_reason(job, "preflight", ref,
                                        reason_code="preflight_failed")
        self._finalize(job, DataJobState.REJECTED, reason_code=reason)
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
        # 슬라이스 20(설계 §2.2, 플랜 D1): 스케줄 대기의 앵커 = "execution vcjob
        # 제출 직후". 전이 행(Preflight→Running/Executing→Executing)의 at 을 나중에
        # 해석하는 대신 여기서 컬럼에 직접 남긴다 -- 세 모듈 교차 불변식(자기 전이
        # 유일성)에 측정이 얹히지 않고, write-once 는 SQL 술어가 강제한다.
        # preview(_submit_preview)/preflight 제출은 앵커를 남기지 않는다.
        self._repos.data_jobs.mark_exec_submitted(jid)
        reclaimed = self._reclaim_if_terminal(job, ref)
        if reclaimed is not None:
            return reclaimed
        self._repos.data_jobs.set_job_state(jid, running_state, actor="stepper")
        return running_state.value

    def _poll_execution(self, job):
        ref = (job["phase_refs"] or {}).get("execution")
        status = self._exec.poll(ref)
        if status == ExecStatus.RUNNING:
            # 슬라이스 20(설계 §2.3, 플랜 D2): execution vcjob 의 첫 RUNNING 관측
            # -- 스케줄 대기를 write-once 기록한다. execution ref 를 폴링하는
            # 함수는 여기뿐이라(preview 는 _poll_preview, preflight 는
            # _poll_preflight) preview 대기가 섞일 경로가 없다. 이미 기록된 잡은
            # 스냅샷 선독으로 no-op. 기록 실패는 run_once 의 잡 단위 try/except 로
            # 격리되고 다음 틱의 RUNNING 관측이 재시도한다(설계 §4). Completing
            # 등도 RUNNING 으로 접히므로(_VCJOB_PHASE) 이 값은 근사다(설계 §2.2).
            self._repos.data_jobs.record_sched_wait(job)
        if status in (ExecStatus.PENDING, ExecStatus.RUNNING):
            return job["state"]
        if status == ExecStatus.SUCCEEDED:
            summary = self._exec.read_summary(ref)
            if summary is None:
                # 정상 잡은 job-runner가 summary.json을 항상 쓴다. None은 컨트롤러가
                # artifact_base 파일시스템을 못 읽는 배포 오구성을 뜻한다 — vcjob phase가
                # 권위이므로 SUCCEEDED는 유지하되, null을 조용히 묻지 않고 가시화한다.
                summary = {"summary_unavailable": True}
                self._repos.observability.record_event(
                    component="stepper", severity="warning",
                    event_type="summary_unreadable", payload={"ref": ref},
                    request_id=job.get("request_id"))
            # 성공 경로도 URI를 남긴다 — preview를 거치지 않는 scan 잡은 여기서만
            # 기록되고, 없으면 포탈이 아티팩트를 가리킬 수 없다.
            self._repos.data_jobs.set_artifact(
                job["job_id"],
                artifact_uri=f"{self._artifact_base()}/{job['job_id']}",
                result_summary=summary)
            self._finalize(job, DataJobState.SUCCEEDED, summary=summary)
            return "Succeeded"
        target = (DataJobState.TIMED_OUT if status == ExecStatus.TIMED_OUT
                  else DataJobState.FAILED)
        self._surface_failed_artifact(job, ref)
        self._finalize(job, target, reason_code="execution_failed",
                       diag=("execution", ref))
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
            artifact = f"{self._artifact_base()}/{jid}"
            self._repos.data_jobs.set_preview(jid, fingerprint=fingerprint,
                                              expires_at=expires, artifact_uri=artifact)
            self._repos.data_jobs.set_job_state(jid, DataJobState.CONFIRM_PENDING,
                                                actor="stepper")
            return "ConfirmPending"
        if status == ExecStatus.TIMED_OUT:
            self._surface_failed_artifact(job, ref)
            self._finalize(job, DataJobState.TIMED_OUT, reason_code="preview_timed_out",
                           diag=("preview", ref))
            return "TimedOut"
        self._surface_failed_artifact(job, ref)
        self._finalize(job, DataJobState.FAILED, reason_code="preview_failed",
                       diag=("preview", ref))
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
        # 재검증도 같은 preflight 스크립트다 -- 미리보기와 confirm 사이에 목적지가
        # 파일로 바뀌는 TOCTOU 가 여기서 잡히므로 사유를 뭉개지 않는다.
        reason = self._preflight_reason(job, "exec_preflight", refs["exec_preflight"],
                                        reason_code="execution_recheck_failed")
        self._finalize(job, DataJobState.REJECTED, reason_code=reason)
        return "Rejected"
