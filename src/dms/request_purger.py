"""컨트롤러 request-purge 루프 -- 삭제한 요청(request_purges 아웃박스 행)의 DB 밖 잔재를 멱등하게 정리한다.

API 는 요청·잡 행을 한 트랜잭션으로 지우면서 정리 열쇠(job_id·phase_refs·삭제 시점 base)를 아웃박스로 옮긴다
(repositories/request_purges.py -- 설계 D1). 이 루프가 그 행을 단계별로 수렴시키고, 끝나면 finish 로 지운다(행 = 정리 대기).

단계(설계 §5.7):
  k8s      기록된 ref(그 잡의 DMS 명명일 때만 -- purge_runner.parse_ref) + 라벨 스윕(Pod·vcjob·launcher)을 지우고 다시
           센다. **남은 객체가 0(Terminating 포함)이 되기 전에는 파일로 넘어가지 않는다**(안전 > 진행 -- 종료 중 launcher 가
           `<job_id>/<phase>` 를 다시 만든다). 이 단계의 첫 스윕부터 10분이 지나도 남으면 purge_waiting_pods 로 지연
           표면화(계속 기다린다 -- 삭제 시각부터 재면 큰 배치 삭제의 대기열 시간이 거짓 지연으로 찍힌다).
  files    base 검사 → `<base>/<job_id>` 를 `.dms-trash/<job_id>` 로 renameat(artifact_trash.detach). 행의 base 가 지금
           base 와 다르면(그 사이 force 변경) **지우지 않고** left_old_base + artifact_left_at_old_base 경고(설계 D11 --
           DB 값만 믿고 다른 경로를 지우지 않는다).
  purging  전역 purge 파드 하나가 trash 를 비운다(purge_runner.build_purge_pod). 완료 = FS 재확인으로 base·trash 둘 다
           없음 → finish(늦게 들어온 events·results·digest·전이 최종 scrub + 아웃박스 행 삭제, 한 트랜잭션) +
           request_purged. 끝나지 않는 파드(죽은 노드·D 상태 rm -- 데드라인 + 유예 초과)는 그 뒤에 막힌 행을
           purge_pod_stuck 으로 표면화한다(두 번째 파드는 띄우지 않는다). 끝난 파드의 실패 귀속은 둘로 나뉜다:
           파드 탓(purge_pod_failed -- 이미지 pull·Pending 상한·하나도 못 지움, 다음에도 묶어 싣는다)과 그 항목 탓
           (purge_entry_failed -- 같은 파드의 다른 이름은 지워졌는데 이 행의 이름이 남았다, 다음부터 혼자 싣는다).

규약:
  - 진행 판정은 DB 장부가 아니라 FS·k8s 실상태에서 한다(설계 D4) -- 어느 지점에서 크래시해도 다음 틱이 같은 결론에 이른다.
  - 아웃박스 행이 가리키는 job_id 외에는 지우지 않는다(설계 D12 -- 「DB 에 없는 디렉터리 = 고아」 추론 금지).
  - 지운 요청·잡 id 가 requests/data_jobs 에 다시 있으면(DB 복원·변조) k8s·FS 무접촉 + purge_target_still_present.
  - 행 모양이 깨졌으면(jobs JSON·job_id 형식·단계 문자열 -- DB 가 신뢰 경계) 아무것도 하지 않고 purge_row_invalid.
  - **모든 purge 이벤트는 request_id=NULL**(id 는 payload) -- 지운 id 로 쓰면 어디서도 안 보이는 고아가 되고 finish 의
    scrub 이 지운다. 실패 이벤트(purge_failed)는 last_error 가 **바뀔 때만**(스팸 억제).
  - 포기 상태는 없다: 실패는 min(interval × 2^min(attempts,6), 900s) 백오프로 계속 재시도하고 지연(stalled)으로 보인다
    (GET /api/admin/request-purges 가 유일한 운영 표면).
  - drain 이면 아무것도 하지 않는다(stepper 와 같은 규칙 -- 새 파드를 띄우지 않는다). 틱 예산 20초(리스 45초 안).
"""
import posixpath
import sys
import time

from . import artifact_trash
from .artifact_base import resolve_artifact_base, strip_scheme
from .artifact_files import JOB_ID_RE
from .artifact_trash import TrashError
from .db import iso_epoch, utc_now_iso
from .purge_runner import MAX_PURGE_NAMES, PurgeError
from .repositories.node_exclusions import blocked_nodes, k8s_unschedulable
from .repositories.request_purges import STAGES

COMPONENT = "request-purge"
TICK_BUDGET_SECONDS = 20           # 리스 = max(15*3, 30) = 45s(controller.run_all_once) 안에서 끝난다
ROW_LIMIT = 20                     # 한 틱에 k8s·파일 단계를 진행하는 행 상한
WAITING_PODS_STALL_SECONDS = 600   # k8s 단계 대기(첫 스윕부터)가 이보다 길면 purge_waiting_pods 로 지연 표면화
# outcomes 안의 k8s 단계 첫 스윕 시각(대기 시간의 기준 -- _step_k8s). 단계를 떠나면 지운다.
K8S_SINCE = "k8s_waiting_since"
# 지연(purge_waiting_pods)이 된 대기 행의 재확인 간격 상한. 간격 = 기다린 시간의 1/10(최소 루프 간격) -- 죽은 노드의
# Terminating 파드는 몇 시간씩 남는데, 그 행마다 매 틱 k8s 호출 ~8회를 반복하지 않게(2026-10-09 검증 지적).
WAITING_BACKOFF_MAX_SECONDS = 300
# 혼자 실을 행(purge_entry_failed)은 묶어 실을 새 행이 없을 때 간다 -- 다만 due 가 된 지 이만큼 지났으면 새 행 묶음보다
# 먼저(삭제가 끊임없이 들어와도 영영 밀리지 않게).
ISOLATED_TURN_SECONDS = 300
# 끝난 상태(phase)로 끝난 파드 -- 스크립트가 돌았다(대기 중 회수된 Pending 파드는 한 이름도 시도하지 않았다).
_RAN_PHASES = frozenset({"Succeeded", "Failed"})
# 운영자 확인이 필요한(재시도로 풀리지 않는) 실패 -- 이벤트 severity error.
_ERROR_CODES = frozenset({"purge_target_still_present", "purge_row_invalid"})


def _uri_matches(uri: str, base: str, job_id: str) -> bool:
    """artifact_uri 가 <base>/<job_id>(또는 그 아래)를 가리키나(표시용 비교 -- 열거나 마운트하지 않는다, 규칙 2)."""
    path = posixpath.normpath(strip_scheme(uri))
    want = posixpath.normpath(f"{base}/{job_id}")
    return path == want or path.startswith(want + "/")


class RequestPurger:
    def __init__(self, repos, runner, *, settings, clock=None, monotonic=None):
        self._repos = repos
        self._purges = repos.request_purges
        self._runner = runner
        self._settings = settings
        self._interval = settings.request_purge_interval_seconds
        self._clock = clock or utc_now_iso
        self._monotonic = monotonic or time.monotonic

    # ---- 틱 ----

    def run_once(self) -> dict:
        control = self._repos.control.control_state()
        if control and control["drain"]:
            return {}
        t0 = self._monotonic()
        base_now = strip_scheme(resolve_artifact_base(self._repos.control, self._settings))
        results = {}
        # 순서가 계약이다: (1) 끝난 purge 파드 수거·실패 귀속은 이번 틱의 떼어냄(detach) **전에** -- 파드가 지운 뒤
        # 같은 이름이 다시 trash 로 들어오면(pending_trash 해소) 그 새 사본을 「파드가 못 지운 것」으로 오인한다.
        # (2) k8s·파일 단계. (3) 새 파드 생성과 완료 판정.
        may_launch = self._reap(base_now, results)
        # purging 행은 아래 전역 단계(_settle)가 다룬다 -- 파드를 기다리는 행들이 상한을 채워 새 삭제를 굶기지 않게.
        # 같은 이유로 due 는 재시도 시각 순이다(방금 기다린 행은 뒤로 -- 오래 막힌 행 20개가 매 틱 상한을 채우지 않는다).
        for row in self._purges.due(self._clock(), limit=ROW_LIMIT, skip_stages=("purging",)):
            if self._monotonic() - t0 > TICK_BUDGET_SECONDS:
                results["_budget_exhausted"] = True
                break
            rid = row["request_id"]
            try:
                results[rid] = self._step_row(row, base_now)
            except (PurgeError, TrashError) as exc:
                self._fail(row, exc.reason_code, detail=exc.detail)
                results[rid] = f"failed:{exc.reason_code}"
            except Exception as exc:       # 한 행이 루프를 죽이지 않는다
                print(f"request-purge error on {rid}: {type(exc).__name__}: {exc}", file=sys.stderr)
                self._fail(row, reason_code="purge_failed", detail=f"{type(exc).__name__}: {exc}")
                results[rid] = f"error:{type(exc).__name__}"
        self._settle(base_now, results, may_launch=may_launch)
        return results

    def _step_row(self, row, base_now) -> str:
        problem = self._row_problem(row)
        if problem is not None:
            raise PurgeError("purge_row_invalid", problem)
        if self._purges.target_still_present(row["request_id"], row["job_ids"]):
            # 살아 있는 행의 파드·아티팩트를 아웃박스가 지우게 두지 않는다 -- k8s·FS 무접촉.
            self._fail(row, reason_code="purge_target_still_present")
            return "failed:purge_target_still_present"
        stage = row["stage"]
        if stage == "k8s":
            stage = self._step_k8s(row)
        if stage == "files":
            stage = self._step_files(row, base_now)
        return stage

    # ---- 검증·기록 ----

    @staticmethod
    def _row_problem(row) -> "str | None":
        """아웃박스 행 모양(DB 신뢰 경계). None = 진행 가능."""
        if row["stage"] not in STAGES:
            return f"unknown stage {row['stage']!r}"[:200]
        jobs = row["jobs"]
        if not isinstance(jobs, list):
            return "jobs unreadable"
        for j in jobs:
            if not isinstance(j, dict) or not isinstance(j.get("job_id"), str) \
                    or not JOB_ID_RE.fullmatch(j["job_id"]):
                return f"bad job entry {str(j)[:100]!r}"
        base = row["artifact_base"]
        if base is not None and (not isinstance(base, str) or not base.startswith("/")):
            return f"bad artifact_base {str(base)[:100]!r}"
        return None

    def _event(self, event_type, severity, message, payload) -> None:
        # 모든 purge 이벤트는 request_id=NULL(모듈 docstring) -- id 는 payload 에.
        self._repos.observability.record_event(
            component=COMPONENT, severity=severity, event_type=event_type,
            message=str(message)[:500], payload=payload, request_id=None)

    def _fail(self, row, reason_code=None, *, detail="") -> None:
        # 호출부는 reason_code= 키워드 리터럴로 부른다(AST 커버리지 그물). 예외에서 온 코드는 raise 지점의 리터럴이 그물에 걸린다.
        rid = row["request_id"]
        changed = self._purges.fail(rid, reason_code=reason_code, interval=self._interval, now=self._clock())
        if changed:
            self._event("purge_failed", "error" if reason_code in _ERROR_CODES else "warning",
                        f"request {rid} purge {row.get('stage')}: {reason_code}" + (f" ({detail})" if detail else ""),
                        {"request_id": rid, "stage": row.get("stage"), "reason_code": reason_code,
                         "detail": str(detail)[:300] or None})

    @staticmethod
    def _outcomes(row) -> "tuple[dict, dict]":
        """(outcomes 전체, 그 안의 jobs 사본). 모양이 깨졌으면 빈 것부터(정보용 -- 진행 판정에 안 쓴다)."""
        out = dict(row["outcomes"] or {})
        jobs = out.get("jobs")
        return out, dict(jobs) if isinstance(jobs, dict) else {}

    # ---- k8s 단계 ----

    def _step_k8s(self, row) -> str:
        rid = row["request_id"]
        deleted = remaining = 0
        rejected = []
        for job in row["jobs"]:
            jid = job["job_id"]
            refs = job.get("phase_refs")
            accepted = []
            if isinstance(refs, dict):
                for phase, ref in refs.items():
                    if ref is None or ref == "":
                        continue
                    if self._runner.ref_belongs_to_job(ref, jid):
                        accepted.append(ref)
                    else:
                        rejected.append({"job_id": jid, "phase": str(phase)[:64], "ref": str(ref)[:200]})
            elif refs is not None:
                rejected.append({"job_id": jid, "phase": None, "ref": str(refs)[:200]})
            r = self._runner.sweep(jid, accepted)
            deleted += r["deleted"]
            remaining += r["remaining"]
        outcomes, jobs_out = self._outcomes(row)
        prior = outcomes.get("k8s_deleted")
        outcomes["k8s_deleted"] = (prior if isinstance(prior, int) and not isinstance(prior, bool) else 0) + deleted
        outcomes["jobs"] = jobs_out
        now = self._clock()
        # 기다림은 이 단계의 **첫 스윕**부터 잰다(requested_at 이 아니다). 큰 배치 삭제는 아웃박스 행을 수백~수천 개
        # 만드는데 루프는 틱당 ROW_LIMIT 행만 진행해서, 대기열에서 10분 넘게 기다린 행은 첫 스윕(방금 지운 파드가 아직
        # Terminating)에 곧장 purge_waiting_pods 로 찍혔다 -- 파드는 한 틱 안에 다 끝났는데 툴바엔 「지연 N건」·「노드 상태를
        # 확인하세요」가 떴다(2026-10-10 검증 지적: 1000자식 배치에서 260건). 스탬프가 없거나 깨졌으면(변조) 지금을 첫
        # 스윕으로 본다. 파일 단계에서 k8s 로 되돌아갈 때(_complete 의 artifact_reappeared)는 스탬프를 지운다.
        since = outcomes.get(K8S_SINCE)
        try:
            waited = iso_epoch(now) - iso_epoch(since) if isinstance(since, str) else None
        except (TypeError, ValueError):
            waited = None
        if waited is None:
            outcomes[K8S_SINCE] = now
            waited = 0
        if remaining == 0:
            outcomes.pop(K8S_SINCE, None)  # 스탬프는 k8s 단계에 있는 동안만 -- 다시 k8s 로 오면 새로 잰다
            if rejected:
                # 진행할 때 한 번만 남긴다(대기 틱마다 같은 ref 를 반복 기록하지 않는다). 거른 ref 는 무접촉이고, 같은 잡의
                # 실제 객체는 라벨 스윕이 회수했다.
                self._event("purge_ref_rejected", "warning",
                            f"request {rid}: {len(rejected)} ref(s) not named for their job -- left untouched",
                            {"request_id": rid, "refs": rejected})
            self._purges.advance(rid, "files", outcomes=outcomes, now=now)
            row["outcomes"] = outcomes     # 같은 틱의 파일 단계가 k8s_deleted 를 덮어쓰지 않게
            return "files"
        if waited > WAITING_PODS_STALL_SECONDS:
            seconds = min(max(self._interval, int(waited) // 10), WAITING_BACKOFF_MAX_SECONDS)
            changed = self._purges.defer(rid, seconds=seconds, reason_code="purge_waiting_pods",
                                         outcomes=outcomes, now=now)
            if changed:
                self._event("purge_failed", "warning",
                            f"request {rid}: {remaining} k8s object(s) still terminating after {int(waited)}s",
                            {"request_id": rid, "stage": "k8s", "reason_code": "purge_waiting_pods",
                             "remaining": remaining})
        else:
            self._purges.defer(rid, seconds=self._interval, reason_code=None, outcomes=outcomes, now=now)
        return "k8s"

    # ---- 파일 단계 ----

    def _step_files(self, row, base_now) -> str:
        rid = row["request_id"]
        job_ids = row["job_ids"]
        outcomes, jobs_out = self._outcomes(row)
        if not job_ids:
            # 잡 없는 요청(planner 거부 등) -- 볼 파일이 없다. base 를 열 필요도 없다.
            self._purges.advance(rid, "purging", now=self._clock())
            return "purging"
        base = row["artifact_base"]
        if base is None:
            raise PurgeError("purge_base_unavailable", "artifact base unknown at delete time")
        if base != base_now:
            for jid in job_ids:
                jobs_out[jid] = "left_old_base"
            self._event("artifact_left_at_old_base", "warning",
                        f"request {rid}: artifact base changed since delete -- {len(job_ids)} job dir(s) left at "
                        f"{base}",
                        {"request_id": rid, "artifact_base": base, "current_base": base_now, "job_ids": job_ids})
            self._purges.advance(rid, "purging", outcomes={**outcomes, "jobs": jobs_out}, now=self._clock())
            return "purging"
        pending = False
        elsewhere = []
        for job in row["jobs"]:
            jid = job["job_id"]
            result = artifact_trash.detach(base_now, jid)
            if result == "pending_trash":
                pending = True             # 같은 이름이 trash 에 아직 있다 -- 이번 틱 purge 파드가 비운 뒤 다시
                continue
            if result == "detached":
                outcome = "deleted"
            elif jobs_out.get(jid) == "deleted":
                outcome = "deleted"        # 앞 틱(pending_trash 로 머문 행)에 이미 옮겼고 파드가 비웠다 -- 낮추지 않는다
            else:                          # absent -- 앞 틱이 옮긴 뒤 크래시했으면 trash 에 있다(설계 D4)
                outcome = "deleted" if artifact_trash.entry_state(base_now, jid)[1] else "absent"
            uri = job.get("artifact_uri")
            if isinstance(uri, str) and not _uri_matches(uri, base, jid):
                # 잡이 다른 base 에 썼다(강제 base 변경 이전 잡) -- 그 경로는 **표시용으로만** 남긴다(열지 않는다).
                elsewhere.append({"job_id": jid, "artifact_uri": uri[:500]})
                if outcome == "absent":
                    outcome = "left_old_base"
            jobs_out[jid] = outcome
        if pending:
            # 행은 계속 due 로 둔다(seconds=0) -- 이번 틱 purge 파드에 그 trash 사본이 실리고, 비워지면 다음 틱에 다시
            # 떼어낸다. 이미 옮긴 잡의 결과는 남긴다(파드가 trash 를 비우면 다음 틱엔 「absent」로 보인다). last_error 는
            # 그대로(앞선 실패 표시를 지우지 않는다).
            self._purges.defer(rid, seconds=0, reason_code=row["last_error"],
                               outcomes={**outcomes, "jobs": jobs_out}, now=self._clock())
            return "files"
        if elsewhere:
            self._event("artifact_left_at_old_base", "warning",
                        f"request {rid}: {len(elsewhere)} job(s) wrote artifacts outside the current base -- left",
                        {"request_id": rid, "artifact_base": base, "jobs": elsewhere})
        self._purges.advance(rid, "purging", outcomes={**outcomes, "jobs": jobs_out}, now=self._clock())
        return "purging"

    # ---- purge 파드 + 완료(전역) ----

    def _live_rows(self, base_now, *, report: bool) -> "tuple[list, list]":
        """(purging 행, files 행) 중 정리해도 되는 것(모양 정상·원 행 부재). report=True 면 막힌 purging 행 중 due 인
        것을 실패로 기록한다(틱당 한 번 -- _settle). files 행은 due 루프가 이미 검증·기록한다(여기선 거르기만)."""
        now = self._clock()
        purging = []
        in_purging, in_files = self._purges.in_stage("purging"), self._purges.in_stage("files")
        # 원 행 재등장 확인은 묶음 한 번(present_targets) -- 행마다 두 문장이면 큰 배치 삭제의 대기열(수천 행)에서 틱이
        # 행 수에 비례해 느려져 같은 프로세스의 다른 루프 틱까지 밀렸다(2026-10-11 검증 지적).
        present = self._purges.present_targets([*in_purging, *in_files])
        for row in in_purging:
            due = row["next_attempt_at"] <= now
            problem = self._row_problem(row)
            if problem is not None:
                if report and due:
                    self._fail(row, reason_code="purge_row_invalid", detail=problem)
                continue
            if row["job_ids"] and row["artifact_base"] is None:
                # 파일 단계가 base 모름을 통과시키지 않으므로 변조 행이다 -- 「옛 base 에 남음」으로 접어 끝내지 않는다.
                if report and due:
                    self._fail(row, reason_code="purge_base_unavailable")
                continue
            if row["request_id"] in present:
                if report and due:
                    self._fail(row, reason_code="purge_target_still_present")
                continue
            purging.append(row)
        files = [r for r in in_files if self._row_problem(r) is None and r["request_id"] not in present]
        return purging, files

    @staticmethod
    def _carriers(rows, base_now) -> list:
        """purge 파드에 trash 항목을 실을 수 있는 행 -- 지금 base 의 행만(옛 base 는 지우지 않는다, 설계 D11).
        files 행도 든다: pending_trash(같은 이름의 앞 사본이 trash 에 있음)를 이 파드가 비워야 떼어냄이 진행한다."""
        return [r for r in rows if r["artifact_base"] == base_now and r["job_ids"]]

    def _reap(self, base_now, results) -> bool:
        """틱 맨 앞: 끝난 purge 파드를 지우고, 그 파드에 실렸는데 trash 항목이 남은 행을 실패로 기록한다 -- 그 항목
        탓이라는 근거가 있으면 purge_entry_failed(다음부터 혼자 -- _entry_failures), 아니면 purge_pod_failed(파드 탓 --
        계속 묶어 싣는다). 끝나지 않는 파드(stuck -- purge_runner.PURGE_POD_STUCK_SECONDS)는 지우지도 하나 더 띄우지도
        않고, 그 파드 뒤에 막힌 행을 purge_pod_stuck 지연으로 표면화한다(_surface_stuck).
        반환 = 이번 틱에 새 파드를 만들어도 되나(진행 중·종료 중 파드가 없고, 방금 지운 파드도 없다 -- 파드는 언제나
        1개, 지운 파드가 사라진 다음 틱에 새로 만든다). 나열 실패면 False(모르는 채로 두 번째 파드를 띄우지 않는다)."""
        purging, files = self._live_rows(base_now, report=False)
        cands = self._carriers([*purging, *files], base_now)
        if not cands:
            return True                    # 지울 것이 없다 -- k8s 를 부르지 않는다(남은 파드는 다음 정리 때 수거)
        now = self._clock()
        try:
            pods = self._runner.list_purge_pods()
            busy = [p for p in pods if p["deleting"] or not (p["failed"] or p["phase"] == "Succeeded")]
            finished = [p for p in pods if not p["deleting"] and (p["failed"] or p["phase"] == "Succeeded")]
            states = None
            blamed, own = set(), set()
            if finished:
                # 상태는 나열 **뒤에** 본다 -- 끝난 파드가 지운 뒤의 FS 를 봐야 한다.
                states = artifact_trash.entry_states(base_now, [j for r in cands for j in r["job_ids"]])
                for p in finished:
                    self._runner.delete_purge_pod(p["name"])
                    names = p["names"]
                    for r in cands:
                        # 이름을 모르면(names None) trash 가 남은 후보 전부 -- 조용히 넘기지 않는다.
                        if any(states[j][1] and (names is None or j in names) for j in r["job_ids"]):
                            blamed.add(r["request_id"])
                    failed = self._entry_failures(p, states)
                    own |= {r["request_id"] for r in cands if any(j in failed for j in r["job_ids"])}
                for r in cands:
                    if r["request_id"] not in blamed or r["next_attempt_at"] > now:
                        continue
                    # 이미 혼자 가던 행(앞선 근거)은 혼자 남는다 -- 혼자 간 파드에선 「다른 이름이 지워졌다」는 근거가
                    # 다시 나올 수 없고, 묶음으로 돌아가면 그 항목이 다시 함께 실린 행들의 데드라인을 먹는다.
                    if r["request_id"] in own or r["last_error"] == "purge_entry_failed":
                        self._fail(r, reason_code="purge_entry_failed")
                    else:
                        self._fail(r, reason_code="purge_pod_failed")
            self._surface_stuck([p for p in busy if p.get("stuck") is True],
                                [r for r in cands if r["request_id"] not in blamed], base_now, states, now)
            return not busy and not finished
        except (PurgeError, TrashError) as exc:
            for r in cands:
                if r["stage"] == "purging" and r["next_attempt_at"] <= now:
                    self._fail(r, exc.reason_code, detail=exc.detail)
            results["_purge_pod"] = f"failed:{exc.reason_code}"
            return False
        except Exception as exc:
            print(f"request-purge reap error: {type(exc).__name__}: {exc}", file=sys.stderr)
            results["_purge_pod"] = f"error:{type(exc).__name__}"
            return False

    @staticmethod
    def _entry_failures(pod, states) -> set:
        """끝난 파드의 이름 중 **그 항목 탓**으로 남았다는 근거가 있는 것. 스크립트는 이름을 command 순서대로 하나씩
        지우고 실패해도 다음 이름으로 간다(purge_runner._purge_script) -- 그래서
          - 마지막으로 지워진 이름보다 **앞**에 남은 이름: 시도했는데 지워지지 않았다(EIO·마운트 경계 등).
          - 그 **바로 뒤** 첫 남은 이름: 실패했거나, 데드라인·축출로 끊길 때 지우던 중이던 거대 트리다.
        그 뒤의 남은 이름은 시도조차 못 했을 수 있어 근거가 아니다(끊긴 파드의 무고한 행 -- 묶음 유지). 하나도 못
        지웠거나(파드 수준 실패와 구별할 수 없다), 스크립트가 돌지 않았거나(이미지 pull·Pending 상한으로 대기 중 회수),
        이름을 모르면 빈 집합 -- 근거 없이 혼자 보내면 한 번의 레지스트리 장애가 함께 실린 최대 100행을 한 행씩
        직렬로 되돌린다(2026-10-09 검증 지적). states 에 없는 이름(후보 밖)은 모름 -- 지워진 것으로도 남은 것으로도
        세지 않는다."""
        names = pod.get("names")
        if names is None or pod.get("phase") not in _RAN_PHASES:
            return set()
        known = [(i, j) for i, j in enumerate(names) if j in states]
        last = max((i for i, j in known if not states[j][1]), default=None)
        if last is None:
            return set()
        failed = {j for i, j in known if i < last and states[j][1]}
        cut = next((j for i, j in known if i > last and states[j][1]), None)
        if cut is not None:
            failed.add(cut)
        return failed

    def _surface_stuck(self, stuck, cands, base_now, states, now) -> None:
        """끝나지 않는 purge 파드 뒤에 막힌 행 = trash 에 항목이 있어 파드가 필요한 due 행 -- purge_pod_stuck 으로 대기
        (defer: 실패가 아니라 attempts 는 그대로, 바뀔 때만 purge_failed 이벤트 1건). 파드는 지우지 않는다: 죽은 노드의
        파드는 지워도 Terminating 에 남을 뿐이고, 살아 있는 노드의 D 상태 rm 을 강제로 떼면 무엇이 남는지 모른다 --
        운영자가 노드를 확인하고 판단한다(deploy/README §12). 막힌 파드가 사라지면(강제 삭제·노드 복구) 표시를 거둔다 --
        다음 파드가 이어서 비운다."""
        if not stuck:
            for r in cands:
                if r["last_error"] == "purge_pod_stuck":
                    self._purges.defer(r["request_id"], seconds=0, reason_code=None, now=now)
            return
        due = [r for r in cands if r["next_attempt_at"] <= now]
        if not due:
            return
        if states is None:
            states = artifact_trash.entry_states(base_now, [j for r in due for j in r["job_ids"]])
        pods = [{"name": p["name"], "phase": p["phase"], "deleting": p["deleting"],
                 "age_seconds": int(p["age_seconds"]) if isinstance(p["age_seconds"], (int, float)) else None}
                for p in stuck]
        for r in due:
            if not any(states[j][1] for j in r["job_ids"]):
                continue                   # 파드가 필요 없다(trash 비어 있음) -- 완료 판정이 끝낸다
            rid = r["request_id"]
            if self._purges.defer(rid, seconds=self._interval, reason_code="purge_pod_stuck", now=now):
                self._event("purge_failed", "warning",
                            f"request {rid}: purge pod {', '.join(p['name'] for p in pods)} has not finished "
                            f"(deadline + grace passed) -- check the node",
                            {"request_id": rid, "stage": r["stage"], "reason_code": "purge_pod_stuck",
                             "pods": pods})

    def _settle(self, base_now, results, *, may_launch: bool) -> None:
        """틱 끝: (파드가 없으면) due 행들의 trash 항목으로 새 purge 파드를 만들고, purging 행의 완료를 FS 로 판정한다."""
        purging, files = self._live_rows(base_now, report=True)
        if may_launch:
            try:
                self._launch(self._carriers([*purging, *files], base_now), base_now)
            except (PurgeError, TrashError) as exc:
                now = self._clock()
                for r in purging:
                    if r["next_attempt_at"] <= now and r["artifact_base"] == base_now and r["job_ids"]:
                        self._fail(r, exc.reason_code, detail=exc.detail)
                results["_purge_pod"] = f"failed:{exc.reason_code}"
            except Exception as exc:
                print(f"request-purge launch error: {type(exc).__name__}: {exc}", file=sys.stderr)
                results["_purge_pod"] = f"error:{type(exc).__name__}"
        for row in purging:
            try:
                state = self._complete(row, base_now)
            except Exception as exc:       # 완료 판정 실패는 그 행만 -- 다음 틱 재시도(FS 오류는 due 때 _fail 로 표면화)
                print(f"request-purge complete error on {row['request_id']}: {type(exc).__name__}: {exc}",
                      file=sys.stderr)
                state = f"error:{type(exc).__name__}"
            if state is not None:
                results[row["request_id"]] = state

    def _launch(self, cands, base_now) -> None:
        """due 행들(실패 백오프 중이 아닌)의 trash 항목 최대 MAX_PURGE_NAMES 개로 purge 파드 1개(설계 §5.4). trash 에
        **실제로 있는** 이름만 싣는다(완료 판정은 파드가 아니라 FS 재확인).

        그 항목 탓으로 남은 행(last_error purge_entry_failed -- _entry_failures)은 다른 행과 한 파드에 싣지 않는다: 그
        항목이 데드라인을 다 먹으면(거대 트리) 함께 실린 행의 이름이 시도되지 못하고 같은 실패로 매번 다시 묶인다
        (2026-10-09 검증 지적 -- 귀속 상관 끊기). 그런 행은 **묶어 실을 행이 없을 때** 혼자 간다(가장 오래된 것부터) --
        혼자 갈 행이 맨 앞이라고 새 삭제 묶음을 세우지 않는다. 단 due 가 된 지 ISOLATED_TURN_SECONDS 가 지났으면 먼저
        간다(굶김 상한). 파드 탓 실패(purge_pod_failed)는 근거가 없어 계속 묶는다 -- 한 번의 이미지 pull 실패가 함께
        실린 행 전부를 한 행씩 직렬로 되돌리던 결함(2026-10-09 검증 지적)."""
        if not cands:
            return
        now = self._clock()
        due = [r for r in cands if r["next_attempt_at"] <= now]
        if not due:
            return
        states = artifact_trash.entry_states(base_now, [j for r in due for j in r["job_ids"]])
        batch, alone = [], []              # (행, trash 에 실제로 있는 이름) -- 오래된 삭제부터
        for r in sorted(due, key=lambda r: (r["requested_at"], r["request_id"])):
            mine = [j for j in r["job_ids"] if states[j][1]]
            if mine:
                (alone if r["last_error"] == "purge_entry_failed" else batch).append((r, mine))
        if not batch and not alone:
            return
        names, carriers = [], []
        if alone and (not batch or self._overdue(alone[0][0], now)):
            head, head_names = alone[0]
            names, carriers = head_names[:MAX_PURGE_NAMES], [head]
        else:
            for r, mine in batch:
                mine = [j for j in mine if j not in names]
                if not mine:
                    continue
                if len(names) + len(mine) > MAX_PURGE_NAMES:
                    if not names:          # 한 행이 상한보다 많으면 앞에서 상한만큼(나머지는 다음 파드)
                        names.extend(mine[:MAX_PURGE_NAMES])
                        carriers.append(r)
                    break
                names.extend(mine)
                carriers.append(r)
        if not names:
            return
        nodes = self._eligible_nodes(base_now)
        if not nodes:
            for r in carriers:
                self._fail(r, reason_code="purge_no_node")
            return
        # 만들기 직전에 한 번 더 본다 -- 틱 앞(_reap)이 후보가 없어 나열을 건너뛰었으면 진행 중 파드를 모른다.
        if any(p["deleting"] or not (p["failed"] or p["phase"] == "Succeeded")
               for p in self._runner.list_purge_pods()):
            return
        self._runner.create_purge_pod(base=base_now, names=names, nodes=nodes)

    @staticmethod
    def _overdue(row, now) -> bool:
        """혼자 갈 행이 due 가 된 지 ISOLATED_TURN_SECONDS 가 지났나. 시각을 못 읽으면 모름 -- 「오래 기다림」으로 접지
        않는다(그 행은 묶어 실을 행이 없을 때 간다)."""
        try:
            waited = iso_epoch(now) - iso_epoch(row["next_attempt_at"])
        except (TypeError, ValueError):
            return False
        return waited >= ISOLATED_TURN_SECONDS

    def _eligible_nodes(self, base_now) -> list:
        """purge 파드를 올릴 노드: 신선한 에이전트 보고가 지금 base 를 exists·writable 로 확인한 노드
        (routes_artifact_base._node_checks 와 같은 근거 -- 모름은 제외), 그중 배치 제외·cordon(blocked_nodes)과 지금
        스케줄 불가(k8s_unschedulable -- planner 와 같이 새 배치에선 일시 조건도 피한다)를 뺀다."""
        names = []
        for node in self._repos.agents.list_nodes(stale_seconds=self._settings.agent_report_stale_seconds):
            if not node["fresh"]:
                continue
            report = node["report"] if isinstance(node["report"], dict) else {}
            ab = report.get("artifact_base")
            if not isinstance(ab, dict) or ab.get("path") != base_now:
                continue
            if ab.get("exists") is not True or ab.get("writable") is not True:
                continue
            if k8s_unschedulable(report):
                continue
            names.append(node["node_name"])
        blocked = blocked_nodes(self._repos, {"primary": names})
        return [n for n in names if n not in blocked]

    def _complete(self, row, base_now) -> "str | None":
        """purging 행의 완료 판정(FS 재확인만). 반환 = 결과 표시(없으면 None)."""
        rid = row["request_id"]
        ids = row["job_ids"]
        outcomes, jobs_out = self._outcomes(row)
        if ids and row["artifact_base"] != base_now:
            # 정리 도중 base 가 force 로 바뀌었다 -- 옛 base 의 trash 는 확인·삭제하지 않는다(설계 D11).
            newly = [j for j in ids if jobs_out.get(j) not in ("absent", "left_old_base")]
            for j in newly:
                jobs_out[j] = "left_old_base"
            if newly:
                self._event("artifact_left_at_old_base", "warning",
                            f"request {rid}: artifact base changed during purge -- {len(newly)} job(s) may be left "
                            f"under {row['artifact_base']}/{artifact_trash.TRASH}",
                            {"request_id": rid, "artifact_base": row["artifact_base"], "current_base": base_now,
                             "job_ids": newly, "in_trash": True})
            return self._finish(row, {**outcomes, "jobs": jobs_out})
        if ids:
            try:
                states = artifact_trash.entry_states(base_now, ids)
            except TrashError:
                return None                # base 문제 -- 파드 단계·due 의 _fail 이 표면화한다
            if any(in_base for in_base, _ in states.values()):
                # 떼어낸 뒤 base 에 다시 생겼다(강제 삭제된 파드가 분할 노드에서 쓴 것 등) -- 무언가 아직 쓰고 있을 수
                # 있으니 k8s 단계부터 다시(객체 0 확인 → 다시 떼어냄).
                self._event("artifact_reappeared", "warning",
                            f"request {rid}: job dir reappeared under the base after detach -- re-running k8s stage",
                            {"request_id": rid, "job_ids": [j for j, (b, _) in states.items() if b]})
                # 대기 시계는 새로 잰다(단계를 떠날 때 지운 스탬프 -- 변조로 남았어도 여기서 뺀다).
                self._purges.advance(rid, "k8s", outcomes={k: v for k, v in outcomes.items() if k != K8S_SINCE},
                                     now=self._clock())
                return "k8s"
            if any(in_trash for _, in_trash in states.values()):
                return None                # purge 파드 대기
        return self._finish(row, outcomes)

    def _finish(self, row, outcomes) -> "str | None":
        rid = row["request_id"]
        if not self._purges.finish(rid, row["job_ids"]):
            return None                    # 그 사이 원 행이 되살아났다 -- due 때 purge_target_still_present
        jobs = outcomes.get("jobs") if isinstance(outcomes.get("jobs"), dict) else {}
        k8s_deleted = outcomes.get("k8s_deleted")
        self._event("request_purged", "info", f"request {rid} purged",
                    {"request_id": rid, "job_ids": row["job_ids"], "outcomes": jobs,
                     "k8s_deleted": k8s_deleted if isinstance(k8s_deleted, int) else None})
        return "purged"
