import pytest
from dms.db import Database, iso_plus, utc_now_iso
from dms.execution import ExecutionError
from dms.migrations import migrate
from dms.repositories import Repositories
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

    def patch_image(self, *, kind, name, container, image):
        if self.fail_patch:
            raise ExecutionError("patch_failed", "boom")
        self.patched.append((kind, name, container, image))

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


def _converged_daemonset(image, container="agent", desired=5):
    return {"kind": "DaemonSet", "generation": 1, "observed_generation": 1,
            "desired_number_scheduled": desired, "updated_number_scheduled": desired,
            "number_ready": desired, "number_unavailable": 0,
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

    def spy(*, kind, name, container, image):
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
