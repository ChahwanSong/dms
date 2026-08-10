import pytest
from dms.db import Database, iso_plus, utc_now_iso
from dms.execution import ExecutionError
from dms.migrations import migrate
from dms.repositories import Repositories
from dms.repositories import releases as releases_mod
from dms.rollout_watcher import RolloutWatcher


class _Runner:
    """관찰 결과를 (kind, name)별로 스크립트하는 페어."""
    def __init__(self):
        self.patched = []
        self.observations = {}     # (kind, name) -> 정규화 dict | None
        self.fail_patch = False
        self.fail_observe = False
        self.fail_briefs = False
        self.briefs = []
        self.init_args = []        # (name, init_container)

    def patch_image(self, *, kind, name, container, image, init_container=None):
        if self.fail_patch:
            raise ExecutionError("patch_failed", "boom")
        self.patched.append((kind, name, container, image))
        # initContainer 전달은 따로 기록한다 -- patched 튜플 모양을 바꾸면 순서/멱등성
        # 단언이 전부 흔들린다.
        self.init_args.append((name, init_container))

    def observe(self, *, kind, name):
        if self.fail_observe:
            raise ExecutionError("observe_failed", "down")
        return self.observations.get((kind, name))

    def pod_briefs(self, *, selector):
        if self.fail_briefs:
            raise ExecutionError("list_failed", "no pods for you")
        return self.briefs


def _converged_deploy(image, container="api"):
    return {"kind": "Deployment", "generation": 2, "observed_generation": 2,
            "replicas": 1, "status_replicas": 1, "updated_replicas": 1,
            "ready_replicas": 1, "conditions": [], "images": {container: image}}


def _progressing_deploy(image, container="api"):
    return {"kind": "Deployment", "generation": 2, "observed_generation": 2,
            "replicas": 1, "status_replicas": 2, "updated_replicas": 1,
            "ready_replicas": 0, "conditions": [], "images": {container: image}}


def _pde_deploy(image, container="api"):
    return {"kind": "Deployment", "generation": 2, "observed_generation": 2,
            "replicas": 1, "status_replicas": 1, "updated_replicas": 0,
            "ready_replicas": 0, "images": {container: image},
            "conditions": [{"type": "Progressing", "status": "False",
                            "reason": "ProgressDeadlineExceeded", "message": "x"}]}


def _stale_pde_deploy(image, at, container="api"):
    """앞 롤아웃이 남긴 sticky PDE 조건(lastUpdateTime이 at)을 단 미수렴 관찰."""
    return {"kind": "Deployment", "generation": 2, "observed_generation": 2,
            "replicas": 1, "status_replicas": 1, "updated_replicas": 0,
            "ready_replicas": 0, "images": {container: image},
            "conditions": [{"type": "Progressing", "status": "False",
                            "reason": "ProgressDeadlineExceeded",
                            "message": "old", "last_update_time": at}]}


def _converged_daemonset(image, container="agent", desired=5):
    return {"kind": "DaemonSet", "generation": 1, "observed_generation": 1,
            "desired_number_scheduled": desired, "updated_number_scheduled": desired,
            "number_ready": desired, "number_unavailable": 0,
            "number_misscheduled": 0, "images": {container: image}}


def _progressing_daemonset(updated, image="pkg-01:5000/dms-agent:new1",
                           container="agent", desired=5):
    """순차 롤아웃 도중: updated 노드까지 새 파드로 교체됐다."""
    return {"kind": "DaemonSet", "generation": 1, "observed_generation": 1,
            "desired_number_scheduled": desired, "updated_number_scheduled": updated,
            "number_ready": updated, "number_unavailable": desired - updated,
            "number_misscheduled": 0, "images": {container: image}}


@pytest.fixture
def repos(tmp_path):
    db = Database.connect(f"sqlite:///{tmp_path}/t.db")
    migrate(db)
    return Repositories(db)


def _batch(repos, *components):
    items = [{"component": c,
              "image": f"pkg-01:5000/{'dms-agent' if c == 'dms-agent' else 'dms'}:new1",
              "tag": "new1"} for c in components]
    return repos.releases.create_batch(items=items, actor="ops")


def _watch(repos, runner, timeout=600):
    return RolloutWatcher(repos, runner, timeout_seconds=timeout)


def test_pending_head_is_recorded_then_patched(repos):
    rows = _batch(repos, "dms-agent", "dms-controller")
    runner = _Runner()
    out = _watch(repos, runner).run_once()
    assert out["patched"] == 1
    # 순서 강제: head(dms-agent)만 나간다 -- 컨트롤러는 아직 Pending
    assert runner.patched == [("DaemonSet", "dms-agent", "agent",
                               "pkg-01:5000/dms-agent:new1")]
    assert repos.releases.get(rows[0]["id"])["state"] == "Applying"
    assert repos.releases.get(rows[1]["id"])["state"] == "Pending"


@pytest.mark.parametrize("component,expected_init", [
    ("dms-agent", None),
    ("dms-api", "migrate"),
    ("dms-controller", "migrate"),
])
def test_patch_carries_component_scoped_init_container(repos, component,
                                                       expected_init):
    # 워처 -> 러너 배선. 두 Deployment 는 migrate initContainer 를 본 컨테이너와 같은
    # 새 이미지로 함께 갱신해야 한다(안 그러면 새 파드가 구 이미지로 migrate 한 뒤 신
    # 앱을 구식 스키마 위에 띄운다). DaemonSet 은 반드시 None -- 없는 initContainer 를
    # 패치하면 strategic merge 가 에이전트 파드에 없던 컨테이너를 새로 만든다.
    _batch(repos, component)
    runner = _Runner()
    _watch(repos, runner).run_once()
    assert runner.init_args == [(component, expected_init)]


def test_state_is_committed_before_patch_is_called(tmp_path):
    # record-then-patch의 핵심 계약: patch가 나가는 순간 이미 다른 커넥션에서도
    # Applying이 보여야 한다. 같은 커넥션으로 읽으면 미커밋 쓰기도 보이므로
    # (즉 record를 트랜잭션으로 감싸는 회귀를 못 잡는다) 별도 커넥션으로 읽는다 --
    # 프로세스가 patch 도중 죽어도 남아야 하는 사실이 바로 이것이다(설계 §2).
    url = f"sqlite:///{tmp_path}/commit.db"
    db = Database.connect(url)
    migrate(db)
    repos = Repositories(db)
    rows = _batch(repos, "dms-api")
    observer = Repositories(Database.connect(url))
    seen = {}
    runner = _Runner()

    def spy(*, kind, name, container, image, init_container=None):
        seen["state"] = observer.releases.get(rows[0]["id"])["state"]

    runner.patch_image = spy
    _watch(repos, runner).run_once()
    assert seen["state"] == "Applying"


def test_record_survives_patch_failure(repos):
    # record-then-patch: patch가 죽어도 행은 이미 Applying으로 커밋돼 있다 --
    # 다음 틱의 재패치 경로가 이어받는다(즉시 Failed를 박지 않는다)
    rows = _batch(repos, "dms-api")
    runner = _Runner()
    runner.fail_patch = True
    _watch(repos, runner).run_once()
    assert repos.releases.get(rows[0]["id"])["state"] == "Applying"


def test_applying_with_wrong_image_is_repatched(repos):
    # 크래시 복구: 기록은 있는데 패치가 안 나갔다 -- 관찰 이미지가 목표와 다르면
    # 재패치한다(같은 이미지 재패치는 멱등, 설계 §2)
    rows = _batch(repos, "dms-api")
    repos.releases.mark_applying(rows[0]["id"])
    runner = _Runner()
    runner.observations[("Deployment", "dms-api")] = _converged_deploy("pkg-01:5000/dms:old")
    out = _watch(repos, runner).run_once()
    assert out["patched"] == 1
    assert runner.patched[0][3] == "pkg-01:5000/dms:new1"
    assert repos.releases.get(rows[0]["id"])["state"] == "Applying"


def test_applying_with_target_image_converges_to_applied(repos):
    # "행은 Applying인데 클러스터가 이미 목표 이미지"는 정상 케이스다 --
    # 그것이 정확히 patch 직후 죽은 상태(컨트롤러 자기 갱신의 핵심 경로)
    rows = _batch(repos, "dms-api")
    repos.releases.mark_applying(rows[0]["id"])
    runner = _Runner()
    runner.observations[("Deployment", "dms-api")] = _converged_deploy("pkg-01:5000/dms:new1")
    out = _watch(repos, runner).run_once()
    assert out["finished"] == 1
    assert repos.releases.get(rows[0]["id"])["state"] == "Applied"
    assert runner.patched == []


def test_controller_self_update_resumes_after_restart(repos):
    # 자기 갱신 전체 시나리오: 컨트롤러가 자기 Deployment를 패치하고 죽는다 ->
    # 새 파드가 같은 행을 이어받아 관찰만으로 Applied로 수렴한다. 재패치도, 중복
    # 패치도 없어야 한다.
    rows = _batch(repos, "dms-controller")
    runner = _Runner()
    _watch(repos, runner).run_once()                     # 여기서 프로세스가 죽는다
    assert runner.patched == [("Deployment", "dms-controller", "controller",
                               "pkg-01:5000/dms:new1")]
    # 새 파드가 이어받는다: 클러스터는 이미 목표 이미지를 돌리고 있다
    runner.observations[("Deployment", "dms-controller")] = _converged_deploy(
        "pkg-01:5000/dms:new1", container="controller")
    _watch(repos, runner).run_once()
    assert repos.releases.get(rows[0]["id"])["state"] == "Applied"
    assert len(runner.patched) == 1                      # 재패치 없음
    assert repos.releases.active() == []


def test_progressing_leaves_state_and_is_seen_again_next_tick(repos):
    rows = _batch(repos, "dms-api")
    repos.releases.mark_applying(rows[0]["id"])
    runner = _Runner()
    runner.observations[("Deployment", "dms-api")] = _progressing_deploy("pkg-01:5000/dms:new1")
    w = _watch(repos, runner)
    assert w.run_once() == {"patched": 0, "finished": 0}
    assert repos.releases.get(rows[0]["id"])["state"] == "Applying"
    # 다음 틱에 수렴하면 그때 Applied가 된다 -- 관찰이 판정하지 patch 호출이 아니다
    runner.observations[("Deployment", "dms-api")] = _converged_deploy("pkg-01:5000/dms:new1")
    w.run_once()
    assert repos.releases.get(rows[0]["id"])["state"] == "Applied"


def test_next_component_starts_only_after_head_is_terminal(repos):
    rows = _batch(repos, "dms-agent", "dms-api")
    repos.releases.mark_applying(rows[0]["id"])
    runner = _Runner()
    runner.observations[("DaemonSet", "dms-agent")] = _converged_daemonset(
        "pkg-01:5000/dms-agent:new1")
    w = _watch(repos, runner)
    w.run_once()                       # agent -> Applied
    assert repos.releases.get(rows[0]["id"])["state"] == "Applied"
    w.run_once()                       # 다음 틱에야 api가 나간다
    assert ("Deployment", "dms-api", "api", "pkg-01:5000/dms:new1") in runner.patched


def test_head_still_progressing_blocks_the_next_component(repos):
    # 순서 강제의 반대 증거: head가 종단이 아니면 뒤 행은 절대 패치되지 않는다.
    rows = _batch(repos, "dms-agent", "dms-api")
    repos.releases.mark_applying(rows[0]["id"])
    runner = _Runner()
    runner.observations[("DaemonSet", "dms-agent")] = {
        "kind": "DaemonSet", "generation": 1, "observed_generation": 1,
        "desired_number_scheduled": 5, "updated_number_scheduled": 3,
        "number_ready": 3, "number_unavailable": 2, "number_misscheduled": 0,
        "images": {"agent": "pkg-01:5000/dms-agent:new1"}}
    w = _watch(repos, runner)
    w.run_once()
    w.run_once()
    assert runner.patched == []
    assert repos.releases.get(rows[1]["id"])["state"] == "Pending"


def test_pde_fails_release_and_aborts_the_rest(repos):
    rows = _batch(repos, "dms-api", "dms-controller")
    repos.releases.mark_applying(rows[0]["id"])
    runner = _Runner()
    runner.observations[("Deployment", "dms-api")] = _pde_deploy("pkg-01:5000/dms:new1")
    _watch(repos, runner).run_once()
    head = repos.releases.get(rows[0]["id"])
    assert head["state"] == "Failed"
    assert head["reason_code"].startswith("rollout_failed:")
    tail = repos.releases.get(rows[1]["id"])
    # 배치를 닫지 않으면 rollout_in_progress가 영원히 새 롤아웃을 막는다
    assert (tail["state"], tail["reason_code"]) == ("Failed", "rollout_aborted")
    assert repos.releases.active() == []


def test_stale_pde_from_a_previous_rollout_does_not_fail_the_new_one(repos):
    # README §9-7 복구 경로 그대로: 나쁜 태그로 PDE -> Failed -> 운영자가 옛 태그로
    # 다시 롤아웃. PDE 조건은 sticky라 패치 직후 첫 status 쓰기에 "새 세대 + 옛
    # 조건"이 함께 실린다. 워처가 릴리스 행의 applied_at을 기준 시각으로 넘기지
    # 않으면 정상 진행 중인 복구 롤아웃이 실패로 판정되고, _fail이 뒤 컴포넌트까지
    # rollout_aborted로 죽여 3컴포넌트 복구가 첫 컴포넌트에서 멈춘다.
    rows = _batch(repos, "dms-api", "dms-controller")
    repos.releases.mark_applying(rows[0]["id"])
    applied_at = repos.releases.get(rows[0]["id"])["applied_at"]
    runner = _Runner()
    runner.observations[("Deployment", "dms-api")] = _stale_pde_deploy(
        "pkg-01:5000/dms:new1", iso_plus(applied_at, -3600))
    assert _watch(repos, runner).run_once() == {"patched": 0, "finished": 0}
    assert repos.releases.get(rows[0]["id"])["state"] == "Applying"
    assert repos.releases.get(rows[1]["id"])["state"] == "Pending"   # 중단되지 않았다


def test_pde_raised_by_this_rollout_still_fails(repos):
    # 반대 증거: applied_at 이후에 갱신된 PDE는 이 롤아웃의 실패다 -- staleness
    # 게이트가 종단 수단을 통째로 삼키면 안 된다.
    rows = _batch(repos, "dms-api")
    repos.releases.mark_applying(rows[0]["id"])
    applied_at = repos.releases.get(rows[0]["id"])["applied_at"]
    runner = _Runner()
    runner.observations[("Deployment", "dms-api")] = _stale_pde_deploy(
        "pkg-01:5000/dms:new1", iso_plus(applied_at, 601))
    _watch(repos, runner).run_once()
    row = repos.releases.get(rows[0]["id"])
    assert (row["state"], row["reason_code"].startswith("rollout_failed:")) == ("Failed", True)


def test_fail_writes_are_atomic(repos, monkeypatch):
    # finish와 abort_pending이 각각 자동커밋이면 그 사이에 프로세스가 죽었을 때
    # 남은 Pending이 살아남아 다음 틱의 head가 되어 패치된다 -- "앞이 실패하면
    # 뒤는 중단"이라는 불변식이 정확히 반대로 깨진다. 한 트랜잭션으로 묶였으면
    # head의 Failed도 함께 롤백되어 Applying으로 남고, 다음 틱이 판정을 다시 한다.
    rows = _batch(repos, "dms-api", "dms-controller")
    repos.releases.mark_applying(rows[0]["id"])
    runner = _Runner()
    runner.observations[("Deployment", "dms-api")] = _pde_deploy("pkg-01:5000/dms:new1")

    def die(*, reason_code):
        raise RuntimeError("abort_pending 도중 프로세스가 죽었다")

    monkeypatch.setattr(repos.releases, "abort_pending", die)
    with pytest.raises(RuntimeError):
        _watch(repos, runner).run_once()
    assert repos.releases.get(rows[0]["id"])["state"] == "Applying"
    assert repos.releases.get(rows[1]["id"])["state"] == "Pending"


def test_unknown_component_is_terminated_not_locked(repos, monkeypatch):
    # ROLLOUT_ORDER에만 컴포넌트를 더하고 COMPONENTS를 빠뜨리면 살아나는 경로다.
    # 조회가 예외로 새면 ExecutionError가 아니라서 run_all_once가 로그만 남기고,
    # 벽시계 회수는 그 줄 하류라 도달조차 못 해 배치가 영원히
    # rollout_in_progress로 잠긴다 -- 잠기지 않고 종단되는 것을 고정한다.
    monkeypatch.setattr(releases_mod, "ROLLOUT_ORDER",
                        releases_mod.ROLLOUT_ORDER + ("dms-newthing",))
    rows = _batch(repos, "dms-newthing")
    runner = _Runner()
    out = _watch(repos, runner).run_once()
    row = repos.releases.get(rows[0]["id"])
    assert (row["state"], row["reason_code"]) == ("Failed", "unknown_component")
    assert out == {"patched": 0, "finished": 1}
    assert runner.patched == []
    assert repos.releases.active() == []


def test_missing_workload_is_failed(repos):
    rows = _batch(repos, "dms-api", "dms-controller")
    repos.releases.mark_applying(rows[0]["id"])
    _watch(repos, _Runner()).run_once()     # observations 비어 있음 -> None
    row = repos.releases.get(rows[0]["id"])
    assert (row["state"], row["reason_code"]) == ("Failed", "workload_not_found")
    assert repos.releases.active() == []    # 뒤 Pending도 닫힌다


def test_transient_observe_error_leaves_state_for_next_tick(repos):
    rows = _batch(repos, "dms-api")
    repos.releases.mark_applying(rows[0]["id"])
    runner = _Runner()
    runner.fail_observe = True
    _watch(repos, runner).run_once()        # 예외가 새 나가면 안 된다
    assert repos.releases.get(rows[0]["id"])["state"] == "Applying"


def test_daemonset_wallclock_reclaims_even_when_observe_fails(repos):
    # DaemonSet에는 conditions가 없어 벽시계가 유일한 실패 수단이고(설계 §3),
    # observe가 지속 실패해도 회수돼야 배치가 영원히 잠기지 않는다
    rows = _batch(repos, "dms-agent")
    repos.releases.mark_applying(rows[0]["id"])
    runner = _Runner()
    runner.fail_observe = True
    runner.briefs = [{"name": "p", "node": "dms-w3", "images": {},
                      "phase": "Pending", "waiting_reason": "ImagePullBackOff"}]
    late = iso_plus(utc_now_iso(), 601)
    _watch(repos, runner, timeout=600).run_once(now_iso=late)
    row = repos.releases.get(rows[0]["id"])
    assert row["state"] == "Failed"
    assert row["reason_code"].startswith("rollout_timeout")
    assert "ImagePullBackOff" in row["reason_code"]


def test_wallclock_reclaims_even_when_pod_briefs_fails(repos):
    # 진단 수집은 best-effort다 -- 그것이 실패했다고 회수를 포기하면 배치가
    # 영원히 잠긴다(build_watcher의 read_log 실패 처리와 같은 관용구).
    rows = _batch(repos, "dms-agent", "dms-api")
    repos.releases.mark_applying(rows[0]["id"])
    runner = _Runner()
    runner.fail_observe = True
    runner.fail_briefs = True
    _watch(repos, runner, timeout=600).run_once(now_iso=iso_plus(utc_now_iso(), 601))
    row = repos.releases.get(rows[0]["id"])
    assert (row["state"], row["reason_code"]) == ("Failed", "rollout_timeout")
    assert repos.releases.active() == []


def test_deployment_wallclock_is_three_times_longer(repos):
    # Deployment의 실패 확정은 PDE 몫 -- 벽시계는 3배로 물려 최후 수단으로만 쓴다
    rows = _batch(repos, "dms-api")
    repos.releases.mark_applying(rows[0]["id"])
    runner = _Runner()
    runner.fail_observe = True
    w = _watch(repos, runner, timeout=600)
    w.run_once(now_iso=iso_plus(utc_now_iso(), 601))
    assert repos.releases.get(rows[0]["id"])["state"] == "Applying"   # 아직
    w.run_once(now_iso=iso_plus(utc_now_iso(), 1801))
    assert repos.releases.get(rows[0]["id"])["state"] == "Failed"


def test_daemonset_progress_pushes_back_the_wallclock(repos):
    # I6: 실 클러스터는 5노드 DaemonSet에 maxUnavailable=1이라 순차 롤아웃이다.
    # 벽시계가 applied_at(=mark_applying 시각)부터 절대 시각으로 재면 전체가
    # 600초 안에 끝나야 하고, dms-agent는 ROLLOUT_ORDER 첫째라 회수되면
    # abort_pending이 dms-api/dms-controller까지 rollout_aborted로 죽인다.
    # 시계는 "지속시간"이 아니라 "정체"를 재야 한다.
    rows = _batch(repos, "dms-agent", "dms-api")
    repos.releases.mark_applying(rows[0]["id"])
    start = repos.releases.get(rows[0]["id"])["applied_at"]
    runner = _Runner()
    w = _watch(repos, runner, timeout=600)
    for node in range(1, 5):        # 노드당 500초 -> 전체 2000초, 마감의 세 배 넘음
        runner.observations[("DaemonSet", "dms-agent")] = _progressing_daemonset(node)
        w.run_once(now_iso=iso_plus(start, 500 * node))
        assert repos.releases.get(rows[0]["id"])["state"] == "Applying", node
    # 마지막 진행(=2000초) 이후로는 같은 관찰만 온다 -- 그때부터 600초를 잰다
    w.run_once(now_iso=iso_plus(start, 2000 + 599))
    assert repos.releases.get(rows[0]["id"])["state"] == "Applying"
    w.run_once(now_iso=iso_plus(start, 2000 + 601))
    row = repos.releases.get(rows[0]["id"])
    assert (row["state"], row["reason_code"]) == ("Failed", "rollout_timeout")
    assert repos.releases.get(rows[1]["id"])["state"] == "Failed"   # 배치는 닫힌다


def test_stalled_daemonset_is_still_reclaimed(repos):
    # 반대 증거: 진행이 멈추면 시계는 그대로 흐른다 -- 정체 기반 회수가
    # "영원히 안 죽는다"가 되면 배치가 rollout_in_progress로 잠긴다.
    rows = _batch(repos, "dms-agent")
    repos.releases.mark_applying(rows[0]["id"])
    start = repos.releases.get(rows[0]["id"])["applied_at"]
    runner = _Runner()
    runner.observations[("DaemonSet", "dms-agent")] = _progressing_daemonset(2)
    w = _watch(repos, runner, timeout=600)
    w.run_once(now_iso=iso_plus(start, 100))          # 2노드까지 진행 -> 시계 리셋
    w.run_once(now_iso=iso_plus(start, 400))          # 같은 관찰 -> 리셋 없음
    assert repos.releases.get(rows[0]["id"])["state"] == "Applying"
    w.run_once(now_iso=iso_plus(start, 100 + 601))    # 마지막 진행 +601초
    assert repos.releases.get(rows[0]["id"])["state"] == "Failed"


def test_stale_generation_observation_does_not_poison_daemonset_progress(repos):
    # I6 회귀: 패치 직후 첫 관찰이 아직 옛 세대 status를 실으면
    # observed_generation < generation 이고 updated_number_scheduled == desired(=5)다.
    # 세대 게이트 없이 그 값을 믿으면 progress가 즉시 5로 올라, 이후의 진짜
    # 진행(1->2->3->4)이 전부 updated <= progress 라 시계를 한 번도 리셋하지 못한다.
    # -> I6이 겨냥한 "5노드 순차 2000초" 시나리오가 조용히 무효화되어 회수된다.
    rows = _batch(repos, "dms-agent", "dms-api")
    repos.releases.mark_applying(rows[0]["id"])
    start = repos.releases.get(rows[0]["id"])["applied_at"]
    runner = _Runner()
    w = _watch(repos, runner, timeout=600)
    # 패치 직후: 옛 세대(gen 1, observed 0)인데 updated == desired == 5
    stale = _progressing_daemonset(5)
    stale["generation"], stale["observed_generation"] = 2, 1
    runner.observations[("DaemonSet", "dms-agent")] = stale
    w.run_once(now_iso=iso_plus(start, 5))
    # progress가 오염되지 않아야 이후 정상 진행이 시계를 리셋할 수 있다
    for node in range(1, 5):
        runner.observations[("DaemonSet", "dms-agent")] = _progressing_daemonset(node)
        w.run_once(now_iso=iso_plus(start, 500 * node))
        assert repos.releases.get(rows[0]["id"])["state"] == "Applying", node


def test_deployment_progress_does_not_move_the_pde_baseline(repos):
    # Deployment의 applied_at은 sticky PDE 판별(I1)의 기준 시각이다 -- 진행 중에
    # 앞당기면 이 롤아웃이 만든 진짜 PDE 조건까지 stale로 읽혀 유일한 종단 수단이
    # 사라진다. Deployment는 PDE가 진행마다 리셋되므로 애초에 필요도 없다.
    rows = _batch(repos, "dms-api")
    repos.releases.mark_applying(rows[0]["id"])
    start = repos.releases.get(rows[0]["id"])["applied_at"]
    runner = _Runner()
    runner.observations[("Deployment", "dms-api")] = _progressing_deploy(
        "pkg-01:5000/dms:new1")
    _watch(repos, runner).run_once(now_iso=iso_plus(start, 60))
    assert repos.releases.get(rows[0]["id"])["applied_at"] == start


def test_terminal_rows_are_not_reprocessed(repos):
    # 멱등: 배치가 끝난 뒤 계속 도는 틱이 종단 행을 다시 건드리면 안 된다.
    rows = _batch(repos, "dms-api")
    repos.releases.mark_applying(rows[0]["id"])
    runner = _Runner()
    runner.observations[("Deployment", "dms-api")] = _converged_deploy("pkg-01:5000/dms:new1")
    w = _watch(repos, runner)
    w.run_once()
    before = repos.releases.get(rows[0]["id"])
    assert w.run_once() == {"patched": 0, "finished": 0}
    assert w.run_once() == {"patched": 0, "finished": 0}
    assert repos.releases.get(rows[0]["id"]) == before
    assert runner.patched == []


def test_run_once_is_idempotent_when_nothing_active(repos):
    assert _watch(repos, _Runner()).run_once() == {"patched": 0, "finished": 0}
