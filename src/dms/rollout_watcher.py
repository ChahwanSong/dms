"""릴리스를 클러스터에 적용하고 수렴을 판정하는 컨트롤러 루프.

루프 안의 예외는 controller.run_all_once가 삼켜 stderr로만 내보낸다 -- 실패는
예외로 새지 않고 반드시 releases.state/reason_code로 드러나야 한다(설계 §2).
리스는 갱신되지 않으므로(간격 10초 -> 리스 30초) 모든 경로가 API 호출 한두 번으로
즉시 반환한다 -- 어디서도 기다리지 않는다.

이 루프의 특수성: dms-controller를 패치하면 이 루프를 실행 중인 파드 자신이 죽는다.
그래서 "완료"는 절대 patch 호출 사실로 판정하지 않고 반드시 클러스터 관찰로만
판정한다 -- 관찰만이 프로세스 죽음을 넘어 살아남는 유일한 근거다."""
import logging

from .db import iso_plus, utc_now_iso
from .execution import ExecutionError
from .repositories.releases import COMPONENTS
from .rollout_status import assess_daemonset, assess_deployment

logger = logging.getLogger(__name__)

# Deployment의 실패 확정은 progressDeadlineSeconds(600, 진행 시 리셋)가 한다 --
# 벽시계를 같은 600으로 걸면 PDE보다 짧아질 수 있어 설계 §3("자체 상한을 두지
# 않는다")과 충돌한다. 3배는 상태 조회가 지속 실패할 때(RBAC 오설정 등) 배치가
# 영원히 rollout_in_progress로 잠기는 것을 푸는 최후 수단이다.
_DEPLOY_TIMEOUT_FACTOR = 3


class RolloutWatcher:
    def __init__(self, repos, runner, *, timeout_seconds):
        self._repos = repos
        self._runner = runner
        self._timeout_seconds = timeout_seconds

    def _fail(self, head, *, reason_code) -> None:
        # reason_code는 반드시 **키워드**로 받는다. tests/test_reason_codes_coverage.py
        # 의 추출기가 detail=/reason_code= 키워드 리터럴만 읽으므로, 위치 인자로 넘기면
        # 이 모듈의 사유 코드가 통째로 가드에 안 보인다 -- "새 사유 코드는
        # REASON_MESSAGES와 reasonCodes.json 양쪽에"라는 슬라이스 불변식이 여기서만
        # 강제되지 않게 된다. 같은 이유로 절단(200자)도 호출부의 [:200] 슬라이스가
        # 아니라 여기서 한다: 슬라이스 표현식은 리터럴도 f-string도 아니라 추출기가
        # 못 읽는다.
        reason_code = reason_code[:200]
        # 한 컴포넌트가 실패하면 뒤 Pending까지 닫아 배치를 끝낸다 -- 안 닫으면
        # active()가 영원히 비지 않아 rollout_in_progress가 새 롤아웃을 막는다.
        #
        # 두 쓰기를 한 트랜잭션으로 묶는다. 각각 자동커밋이면 그 사이에 프로세스가
        # 죽었을 때 (a) 남은 Pending이 살아남아 다음 틱의 head가 되어 패치된다 --
        # "앞이 실패하면 뒤는 중단"이 정확히 반대로 깨진다. (b) 단일 컴포넌트
        # 배치에서는 finish 직후 active()가 잠깐 비어, 그 틈에 통과한 새 배치를
        # 뒤이은 abort_pending이 rollout_aborted로 죽인다. 묶으면 실패 기록 전체가
        # 원자적으로 보이거나 아예 안 보인다 -- 후자면 head는 Applying으로 남아
        # 다음 틱이 같은 판정을 다시 한다.
        #
        # 이 안에서 patch를 부르지 않는다: record-then-patch는 "기록이 커밋된
        # 뒤에 patch"이고, 트랜잭션 안의 patch는 커밋 전 호출이 되어 계약을 깬다.
        # _fail은 실패 기록 전용이라 지금은 그 위험이 없다.
        with self._repos.db.transaction():
            self._repos.releases.finish(head["id"], state="Failed",
                                        reason_code=reason_code)
            self._repos.releases.abort_pending(reason_code="rollout_aborted")

    def _stuck_detail(self, spec) -> str:
        """멈춘 노드와 사유(ImagePullBackOff 등)를 회수 사유에 싣는다.

        회수는 릴리스당 최대 한 번이고 그 순간이 지나면(파드가 다음 롤아웃으로
        교체되면) 원인의 유일한 증거가 사라진다 -- 특히 DaemonSet은 conditions가
        없어 "왜 멈췄나"를 담을 다른 필드가 아예 없다. 목록 조회 1회(_request_timeout
        10초)를 종단 경로에서만 치르는 것이 그 진단 가치보다 싸다. 실패해도 회수
        자체는 진행한다 -- 진단 때문에 회수를 포기하면 배치가 영원히 잠긴다."""
        try:
            briefs = self._runner.pod_briefs(selector=spec["selector"])
        except Exception as exc:      # best-effort -- 회수 판정을 막지 않는다
            logger.warning("pod briefs failed selector=%s: %s", spec["selector"], exc)
            return ""
        return ",".join(sorted({f"{b.get('node')}:{b.get('waiting_reason')}"
                                for b in briefs if b.get("waiting_reason")}))

    def _note_daemonset_progress(self, head, obs, now) -> None:
        """DaemonSet 회수 시계를 '정체' 기준으로 다시 건다(I6).

        DaemonSet에는 conditions도 progressDeadlineSeconds도 없어 벽시계가 유일한
        실패 수단인데, applied_at(mark_applying 때 한 번 설정)부터 절대 시각으로
        재면 정상적인 순차 롤아웃이 회수된다 -- 실 클러스터는 5노드에
        maxUnavailable=1이라 노드 하나씩 차례로 교체되고, 전체가 600초 안에 끝나야
        한다는 뜻이 되기 때문이다. 게다가 dms-agent가 ROLLOUT_ORDER 첫째라 회수되면
        abort_pending이 dms-api/dms-controller까지 rollout_aborted로 죽인다.

        updated_number_scheduled가 늘어난 틱마다 시계를 다시 건다. 이전 값은
        releases.progress로 지속시킨다 -- 이 워처는 틱마다 새로 생성되므로
        (controller.build_loops의 람다) 인스턴스 변수는 다음 틱까지 살아남지 않고,
        컨트롤러 파드가 교체되면 더더욱 그렇다.

        Deployment에는 하지 않는다: 그쪽 applied_at은 sticky PDE 판별의 기준
        시각이라(rollout_status._is_stale_pde) 앞당기면 이 롤아웃이 만든 진짜 PDE
        조건까지 stale로 읽혀 유일한 종단 수단이 사라진다. 애초에 Deployment는
        progressDeadlineSeconds가 진행마다 리셋되므로 이 보정이 필요 없다."""
        # 세대 게이트를 먼저 건다: 패치 직후 첫 관찰이 아직 옛 세대 status를 실으면
        # updated_number_scheduled == desired(=5)라, 그 값을 믿고 progress를 5로
        # 올려버리면 이후의 진짜 진행(1->2->3->4)이 전부 updated <= progress가 되어
        # 시계를 한 번도 리셋하지 못한다 -- 이 보정이 겨냥한 바로 그 시나리오가
        # 조용히 무효화된다. assess_daemonset이 쓰는 것과 같은 게이트다.
        if obs["observed_generation"] < obs["generation"]:
            return
        updated = obs["updated_number_scheduled"]
        if updated <= (head["progress"] or 0):
            return
        self._repos.releases.note_progress(head["id"], progress=updated, now=now)

    def _reclaim(self, head, spec, now) -> bool:
        """벽시계 회수. observe보다 먼저 -- 조회가 지속 실패해도 회수는 돼야 한다.

        진행 시 갱신되는 applied_at(_note_daemonset_progress)을 기준으로 재므로
        observe가 지속 실패하면 시계는 리셋되지 않고 그대로 흘러 회수된다."""
        factor = 1 if spec["kind"] == "DaemonSet" else _DEPLOY_TIMEOUT_FACTOR
        if head["applied_at"] >= iso_plus(now, -self._timeout_seconds * factor):
            return False
        stuck = self._stuck_detail(spec)
        if stuck:
            self._fail(head, reason_code=f"rollout_timeout:{stuck}")
        else:
            self._fail(head, reason_code="rollout_timeout")
        return True

    def run_once(self, *, now_iso=None) -> dict:
        patched = finished = 0
        now = now_iso or utc_now_iso()
        active = self._repos.releases.active()
        if not active:
            return {"patched": 0, "finished": 0}
        # 순서 강제: head(최소 seq)만 진행한다. dms-agent -> dms-api ->
        # dms-controller 순서가 seq로 지속돼 있어(Task 1), 자기 갱신으로 죽은
        # 컨트롤러의 후임도 이미 끝낸 패치를 다시 하지 않는다(설계 §2).
        head = active[0]
        try:
            # 좌표 조회는 반드시 try 안이다. 밖에 두고 KeyError가 나면 그것은
            # ExecutionError가 아니라 run_all_once까지 올라가 로그만 남고, 아래
            # 벽시계 회수는 이 줄 하류라 도달조차 못 해 배치가 영원히
            # rollout_in_progress로 잠긴다. 지금은 create_batch의
            # ROLLOUT_ORDER.index()가 먼저 막지만, ROLLOUT_ORDER에만 컴포넌트를
            # 더하면 즉시 살아나는 경로다.
            spec = COMPONENTS.get(head["component"])
            if spec is None:
                # 재시도로 고쳐질 수 없는 영구 오류다(다음 틱에도 표는 그대로다).
                # 그래서 예외를 삼켜 다음 틱에 맡기지 않고 즉시 _fail로 종단시킨다 --
                # 회수는 Applying 행만 보므로 Pending head가 여기 걸리면 벽시계도
                # 못 푼다. 배치를 닫는 것만이 잠김을 막는다.
                self._fail(head, reason_code="unknown_component")
                return {"patched": patched, "finished": finished + 1}

            if head["state"] == "Pending":
                # 1단계: 기록을 먼저 커밋한다. "방금 patch를 불렀다"는 사실은
                # 프로세스 죽음(특히 컨트롤러 자기 갱신)을 넘지 못한다(설계 §2).
                self._repos.releases.mark_applying(head["id"])
                self._runner.patch_image(kind=spec["kind"], name=spec["workload"],
                                         container=spec["container"],
                                         image=head["image"])
                # 패치 직후 반드시 반환한다. dms-controller를 패치했다면 이 프로세스는
                # 곧 SIGTERM을 받는다 -- 그 뒤의 어떤 관찰/DB 쓰기도 중간에 잘릴 수
                # 있어 결과를 신뢰할 수 없고, 방금 패치한 워크로드를 같은 틱에 관찰해
                # 봐야 아직 옛 세대라 판정도 못 한다. 판정은 다음 틱(또는 후임 파드)의
                # 관찰에 맡긴다 -- 그것이 이 루프가 재개 가능한 이유다.
                return {"patched": patched + 1, "finished": finished}

            # state == Applying
            if self._reclaim(head, spec, now):
                return {"patched": patched, "finished": finished + 1}
            obs = self._runner.observe(kind=spec["kind"], name=spec["workload"])
            if obs is None:
                # 404. 워크로드 이름이 바뀌었거나 네임스페이스가 다르다 -- 기다려도
                # 저절로 생기지 않으므로 즉시 종단시킨다.
                self._fail(head, reason_code="workload_not_found")
                return {"patched": patched, "finished": finished + 1}
            if obs["images"].get(spec["container"]) != head["image"]:
                # 크래시 복구: 행은 Applying인데 spec 이미지가 목표가 아니다 --
                # record 후 patch 전에 죽었다. 같은 이미지 재패치는 새 ReplicaSet을
                # 만들지 않으므로 그냥 다시 패치한다(설계 §2 멱등성 요구).
                self._runner.patch_image(kind=spec["kind"], name=spec["workload"],
                                         container=spec["container"],
                                         image=head["image"])
                return {"patched": patched + 1, "finished": finished}
            if spec["kind"] == "Deployment":
                # applied_at을 기준 시각으로 넘긴다 -- 이 값보다 오래된
                # ProgressDeadlineExceeded 조건은 앞 롤아웃이 남긴 sticky 잔재라
                # 이 롤아웃의 실패가 아니다(rollout_status._is_stale_pde).
                verdict, detail = assess_deployment(obs, since=head["applied_at"])
            else:
                # 판정 전에 시계를 다시 건다 -- 이 틱이 progressing으로 끝나도
                # 다음 틱의 회수 기준은 "마지막 진행"이어야 한다.
                self._note_daemonset_progress(head, obs, now)
                verdict, detail = assess_daemonset(obs)
            if verdict == "applied":
                self._repos.releases.finish(head["id"], state="Applied")
                finished += 1
            elif verdict == "failed":
                if detail:
                    self._fail(head, reason_code=f"rollout_failed:{detail}")
                else:
                    self._fail(head, reason_code="rollout_failed")
                finished += 1
            # progressing이면 아무것도 안 한다 -- 다음 틱이 다시 본다
        except ExecutionError as exc:
            # 일시 오류(apiserver 재시작 등)로 즉시 Failed를 박지 않는다 --
            # build_watcher I6와 같은 관용구. 영구 오류는 위 벽시계가 회수한다.
            logger.warning("rollout watcher error release=%s: %s", head["id"], exc)
        return {"patched": patched, "finished": finished}
