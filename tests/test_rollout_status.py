import pytest
from dms.rollout_status import (assess_daemonset, assess_deployment,
                                normalize_daemonset, normalize_deployment)

# 같은 Deployment를 두 표기로 만든다 -- to_dict()는 snake_case, 원시 dict는
# camelCase. 정규화가 이 차이를 흡수하지 못하면 페어는 통과하고 프로덕션만
# None을 읽어 "영원히 수렴 안 함"이 된다(설계 §4).
DEPLOY_SNAKE = {
    "metadata": {"generation": 3},
    "spec": {"replicas": 2, "template": {"spec": {"containers": [
        {"name": "api", "image": "pkg-01:5000/dms:d23"}]}}},
    "status": {"observed_generation": 3, "replicas": 2, "updated_replicas": 2,
               "ready_replicas": 2,
               "conditions": [{"type": "Progressing", "status": "True",
                               "reason": "NewReplicaSetAvailable", "message": "ok"}]},
}
DEPLOY_CAMEL = {
    "metadata": {"generation": 3},
    "spec": {"replicas": 2, "template": {"spec": {"containers": [
        {"name": "api", "image": "pkg-01:5000/dms:d23"}]}}},
    "status": {"observedGeneration": 3, "replicas": 2, "updatedReplicas": 2,
               "readyReplicas": 2,
               "conditions": [{"type": "Progressing", "status": "True",
                               "reason": "NewReplicaSetAvailable", "message": "ok"}]},
}


@pytest.mark.parametrize("obj", [DEPLOY_SNAKE, DEPLOY_CAMEL])
def test_both_notations_normalize_identically(obj):
    norm = normalize_deployment(obj)
    assert norm == normalize_deployment(DEPLOY_SNAKE)
    assert norm["observed_generation"] == 3
    assert norm["images"] == {"api": "pkg-01:5000/dms:d23"}


def test_converged_deployment_is_applied():
    assert assess_deployment(normalize_deployment(DEPLOY_SNAKE)) == ("applied", None)


def test_generation_gate_blocks_stale_success():
    # 패치 직후: 상태 필드는 전부 패치 이전(수렴한 옛 ReplicaSet) 값이다.
    # 게이트가 없으면 여기서 "applied"가 나온다 -- 전형적인 거짓 성공(설계 §3).
    stale = {**DEPLOY_SNAKE, "metadata": {"generation": 4}}
    assert assess_deployment(normalize_deployment(stale)) == ("progressing", None)


def test_progress_deadline_exceeded_is_failed():
    obj = {**DEPLOY_SNAKE, "status": {
        "observed_generation": 3, "replicas": 2, "updated_replicas": 1,
        "ready_replicas": 1,
        "conditions": [{"type": "Progressing", "status": "False",
                        "reason": "ProgressDeadlineExceeded", "message": "exceeded"}]}}
    verdict, detail = assess_deployment(normalize_deployment(obj))
    assert verdict == "failed"
    assert "ProgressDeadlineExceeded" in detail


# --- I1: sticky ProgressDeadlineExceeded -------------------------------------
# 세대 게이트는 observedGeneration만 증명한다. conditions는 배포 컨트롤러가 세대를
# 넘겨 그대로 이어 나르므로, 패치 직후 첫 status 쓰기가 "새 세대 + 옛 PDE"를 함께
# 실을 수 있다. 그 창에 관찰이 떨어지면 정상 진행 중인 복구 롤아웃이 실패로
# 판정되고 _fail이 남은 컴포넌트까지 rollout_aborted로 죽인다(README §9-7 복구 경로).
APPLIED_AT = "2026-08-06T12:00:00Z"
STALE_PDE = {"type": "Progressing", "status": "False",
             "reason": "ProgressDeadlineExceeded", "message": "old failure",
             "lastUpdateTime": "2026-08-06T11:00:00Z"}      # applied_at 이전
FRESH_PDE = {"type": "Progressing", "status": "False",
             "reason": "ProgressDeadlineExceeded", "message": "this rollout",
             "lastUpdateTime": "2026-08-06T12:10:00Z"}      # applied_at 이후


def _deploy_with(conditions, *, updated=2, ready=2, status_replicas=2):
    return {**DEPLOY_SNAKE, "status": {
        "observed_generation": 3, "replicas": status_replicas,
        "updated_replicas": updated, "ready_replicas": ready,
        "conditions": conditions}}


def test_converged_with_stale_pde_is_applied():
    # (a) 완전히 수렴했는데 옛 PDE 조건이 아직 붙어 있다 -- 수렴 검사가 PDE 스캔
    # 앞에 있어야 이 절반이 닫힌다. 진짜 timeout된 배포는 절대 수렴하지 않는다.
    norm = normalize_deployment(_deploy_with([STALE_PDE]))
    assert assess_deployment(norm, since=APPLIED_AT) == ("applied", None)


def test_converged_with_a_fresh_pde_is_still_applied():
    # (a') 수렴 검사가 PDE 스캔보다 **앞**이어야만 통과한다 -- staleness 게이트는
    # 여기서 도와주지 않는다(조건이 이 롤아웃 것이다). 마감을 넘긴 뒤 뒤늦게 파드가
    # 뜨면(느린 이미지 풀) 카운터는 이미 수렴인데 조건 갱신이 한 박자 늦는다.
    # 전 레플리카가 새 이미지로 Ready인 배포를 실패로 박을 근거는 없다.
    norm = normalize_deployment(_deploy_with([FRESH_PDE]))
    assert assess_deployment(norm, since=APPLIED_AT) == ("applied", None)


def test_unconverged_with_fresh_pde_is_failed():
    # (b) 이 롤아웃이 실제로 마감을 넘겼다 -- 종단시켜야 한다
    norm = normalize_deployment(_deploy_with([FRESH_PDE], updated=1, ready=0))
    verdict, detail = assess_deployment(norm, since=APPLIED_AT)
    assert verdict == "failed"
    assert "this rollout" in detail


def test_unconverged_with_stale_pde_is_progressing():
    # (c) 패치 직후 창: 새 세대인데 옛 PDE가 아직 안 지워졌다. lastUpdateTime이
    # applied_at 이전이므로 이 롤아웃의 실패가 아니다 -- 기다린다.
    norm = normalize_deployment(_deploy_with([STALE_PDE], updated=1, ready=0))
    assert assess_deployment(norm, since=APPLIED_AT) == ("progressing", None)


def test_pde_without_since_is_still_failed():
    # 기준 시각을 안 넘기면(순수 함수 단독 사용) 옛 동작 그대로 -- 판별 근거가
    # 없을 때 실패를 삼키면 Deployment의 유일한 종단 수단이 사라진다.
    norm = normalize_deployment(_deploy_with([FRESH_PDE], updated=1, ready=0))
    assert assess_deployment(norm)[0] == "failed"


def test_pde_without_last_update_time_is_still_failed():
    # SetDeploymentCondition은 lastUpdateTime을 항상 채우지만, 없으면 stale임을
    # 증명할 수 없다 -- 증명 못 하는 쪽을 실패로 본다(위와 같은 이유).
    bare = {k: v for k, v in FRESH_PDE.items() if k != "lastUpdateTime"}
    norm = normalize_deployment(_deploy_with([bare], updated=1, ready=0))
    assert assess_deployment(norm, since=APPLIED_AT)[0] == "failed"


@pytest.mark.parametrize("key", ["lastUpdateTime", "last_update_time"])
def test_condition_last_update_time_normalizes_in_both_notations(key):
    cond = {"type": "Progressing", "status": "False",
            "reason": "ProgressDeadlineExceeded", "message": "m",
            key: "2026-08-06T11:00:00Z"}
    norm = normalize_deployment(_deploy_with([cond], updated=1, ready=0))
    assert norm["conditions"][0]["last_update_time"] == "2026-08-06T11:00:00Z"


def test_datetime_last_update_time_is_folded_to_the_applied_at_format():
    # to_dict()는 metav1.Time을 datetime으로 준다 -- 문자열로 접지 않으면
    # applied_at(utc_now_iso 포맷)과의 사전식 비교가 TypeError로 터진다.
    from datetime import datetime, timedelta, timezone
    cond = {"type": "Progressing", "status": "False",
            "reason": "ProgressDeadlineExceeded", "message": "m",
            "last_update_time": datetime(2026, 8, 6, 11, 0, 0, tzinfo=timezone.utc)}
    norm = normalize_deployment(_deploy_with([cond], updated=1, ready=0))
    assert norm["conditions"][0]["last_update_time"] == "2026-08-06T11:00:00Z"
    assert assess_deployment(norm, since=APPLIED_AT)[0] == "progressing"
    # UTC가 아닌 tz로 와도 UTC로 접는다(비교 기준이 한 축이어야 한다)
    other = datetime(2026, 8, 6, 20, 0, 0,
                     tzinfo=timezone(timedelta(hours=9)))     # == 11:00Z
    norm2 = normalize_deployment(_deploy_with(
        [{**cond, "last_update_time": other}], updated=1, ready=0))
    assert norm2["conditions"][0]["last_update_time"] == "2026-08-06T11:00:00Z"


def test_replica_failure_condition_surfaces_as_detail():
    # /cephfs hostPath type:Directory가 없는 노드의 admission 오류가 여기 실린다
    obj = {**DEPLOY_SNAKE, "status": {
        "observed_generation": 3, "replicas": 2, "updated_replicas": 1,
        "ready_replicas": 1,
        "conditions": [{"type": "ReplicaFailure", "status": "True",
                        "reason": "FailedCreate", "message": "hostPath missing"}]}}
    verdict, detail = assess_deployment(normalize_deployment(obj))
    assert verdict == "progressing"
    assert "hostPath missing" in detail


# I2: 수렴 조건 세 개를 각각 하나씩만 깨서 고정한다. DaemonSet 쪽
# test_daemonset_each_gate_blocks_applied의 Deployment 판이다 -- 한 조건만 깨야
# "그 조건을 지워도 다른 조건이 대신 막아준다"는 위장 통과가 안 생긴다.
@pytest.mark.parametrize("spec_patch,status_patch,why", [
    # updated != replicas: 새 파드가 아직 다 안 떴다(스케일업 중)
    ({"replicas": 3}, {}, "updated_replicas == replicas"),
    # status_replicas != updated: 옛 파드가 아직 남아 있다
    ({}, {"replicas": 3}, "status_replicas == updated_replicas"),
    # ready != updated: Recreate 전략이나 노드 실패로 아무것도 서빙하지 않는다
    ({}, {"ready_replicas": 0}, "ready_replicas == updated_replicas"),
])
def test_deployment_each_gate_blocks_applied(spec_patch, status_patch, why):
    obj = {**DEPLOY_SNAKE,
           "spec": {**DEPLOY_SNAKE["spec"], **spec_patch},
           "status": {**DEPLOY_SNAKE["status"], **status_patch}}
    assert assess_deployment(normalize_deployment(obj))[0] == "progressing", why


def test_old_pods_still_around_is_progressing():
    # updated == desired 여도 status.replicas > updated면 옛 파드가 남아 있다
    obj = {**DEPLOY_SNAKE, "status": {
        "observed_generation": 3, "replicas": 3, "updated_replicas": 2,
        "ready_replicas": 2, "conditions": []}}
    assert assess_deployment(normalize_deployment(obj))[0] == "progressing"


def test_missing_observed_generation_never_converges():
    obj = {"metadata": {"generation": 1}, "spec": {"replicas": 1},
           "status": {"replicas": 1, "updated_replicas": 1, "ready_replicas": 1}}
    norm = normalize_deployment(obj)
    assert norm["observed_generation"] == -1
    assert assess_deployment(norm)[0] == "progressing"


DS_CAMEL = {
    "metadata": {"generation": 5},
    "spec": {"template": {"spec": {"containers": [
        {"name": "agent", "image": "pkg-01:5000/dms-agent:dev6"}]}}},
    "status": {"observedGeneration": 5, "desiredNumberScheduled": 5,
               "updatedNumberScheduled": 5, "numberReady": 5,
               "numberMisscheduled": 0},
}


def test_daemonset_unset_unavailable_counts_as_zero():
    # numberUnavailable은 0이면 아예 빠진다 -- unset을 0으로 안 읽으면 영원히 progressing
    norm = normalize_daemonset(DS_CAMEL)
    assert norm["number_unavailable"] == 0
    assert assess_daemonset(norm) == ("applied", None)


def test_daemonset_generation_gate_applies():
    stale = {**DS_CAMEL, "metadata": {"generation": 6}}
    assert assess_daemonset(normalize_daemonset(stale))[0] == "progressing"


@pytest.mark.parametrize("patch,expected", [
    ({"updatedNumberScheduled": 4}, "progressing"),   # 아직 옛 파드가 있는 노드
    ({"numberReady": 4}, "progressing"),              # 새 파드가 Ready가 아님
    ({"numberUnavailable": 1}, "progressing"),
    ({"numberMisscheduled": 1}, "progressing"),
])
def test_daemonset_each_gate_blocks_applied(patch, expected):
    obj = {**DS_CAMEL, "status": {**DS_CAMEL["status"], **patch}}
    assert assess_daemonset(normalize_daemonset(obj))[0] == expected


def test_daemonset_snake_case_status_normalizes_too():
    snake = {"metadata": {"generation": 5},
             "spec": {"template": {"spec": {"containers": [
                 {"name": "agent", "image": "i"}]}}},
             "status": {"observed_generation": 5, "desired_number_scheduled": 5,
                        "updated_number_scheduled": 5, "number_ready": 5,
                        "number_misscheduled": 0}}
    assert assess_daemonset(normalize_daemonset(snake)) == ("applied", None)
