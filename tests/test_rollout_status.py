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
