"""워커 CPU 요청 = 워커당 rank 수(2026-10-16 사용자 결정 「프로세스 개수만큼 코어, 옵션이 아니라 항상」).

고정하는 것: ① 모든 워커 컨테이너(primary worker·nsync source/destination-worker, 미리보기·실행 공통)의 requests.cpu 가
hostfile slots(DMS_JR_PROCESSES_PER_NODE)와 같은 값 -- 단일 원천 execution_manifests._processes_per_node ② limits 없음
③ launcher 100m(node_cpu.LAUNCHER_CPU_REQUEST_MILLICORES) ④ preflight·exec_preflight 맨 Pod 는 BestEffort 그대로 ⑤ DB 신뢰
경계 -- process_count 가 int(bool 제외)·≥ 1 이 아니거나 워커당 rank 가 위생 상한을 넘으면 ValueError(어댑터가 submit_failed
로 접는다)."""
import json

import pytest

from dms.execution import JobSpec
from dms.execution_manifests import (MAX_PROCESSES_PER_WORKER, build_preflight_pod, build_volcano_job,
                                     processes_per_node, worker_pod_count)
from dms.node_cpu import LAUNCHER_CPU_REQUEST_MILLICORES

_VOL = [{"name": "cephfs", "hostPath": {"path": "/cephfs"}, "mountPath": "/cephfs"}]
_NSYNC_PATHS = {"source": "/a", "source_storage": "s1", "destination": "/b", "destination_storage": "s2", "source_root": "/", "destination_root": "/"}


def _spec(**kw):
    base = dict(job_id="j1", phase="execution", operation="scan", tool="dscan",
                dryrun=False, identity={"uid": 10001, "gid": 10000, "username": "alice"},
                paths={"target": "/cephfs/x"}, options={},
                candidates={"primary": ["n1"]}, process_count=8, queue="dms-data",
                priority_class="dms-mid", artifact_base="file:///cephfs/dms/artifacts")
    base.update(kw)
    return JobSpec(**base)


def _build(**kw):
    # role_volumes 는 nsync 만 쓴다(primary 는 무시) -- 여기선 볼륨 모양이 아니라 CPU 요청을 본다.
    return build_volcano_job(_spec(**kw), job_image="i", namespace="dms", volumes=_VOL,
                             role_volumes={"source": _VOL, "destination": _VOL})


def _tasks(m):
    return {t["name"]: t for t in m["spec"]["tasks"]}


def _c(task):
    (c,) = task["template"]["spec"]["containers"]
    return c


def _env(c):
    return {e["name"]: e["value"] for e in c["env"]}


@pytest.mark.parametrize("phase,dryrun", [("preview", True), ("execution", False)])
def test_primary_worker_requests_cpu_equal_to_slots(phase, dryrun):
    t = _tasks(_build(phase=phase, dryrun=dryrun, candidates={"primary": ["n1", "n2", "n3"]}, process_count=6))
    w = _c(t["worker"])
    assert w["resources"] == {"requests": {"cpu": "2"}}                 # limits 키 자체가 없다
    assert _env(w)["DMS_JR_PROCESSES_PER_NODE"] == "2"
    assert _env(_c(t["launcher"]))["DMS_JR_PROCESSES_PER_NODE"] == "2"
    assert _c(t["launcher"])["resources"] == {"requests": {"cpu": f"{LAUNCHER_CPU_REQUEST_MILLICORES}m"}}
    assert _c(t["launcher"])["resources"] == {"requests": {"cpu": "100m"}}


def test_nsync_both_worker_tasks_request_cpu_and_launcher_is_small():
    t = _tasks(_build(operation="sync", tool="nsync", paths=_NSYNC_PATHS,
                      candidates={"source": ["n1", "n2"], "destination": ["n4"]}, process_count=9))
    for name in ("source-worker", "destination-worker"):
        assert _c(t[name])["resources"] == {"requests": {"cpu": "3"}}
    launcher = _c(t["launcher"])
    assert launcher["resources"] == {"requests": {"cpu": "100m"}}
    assert _env(launcher)["DMS_JR_PROCESSES_PER_NODE"] == "3"
    assert json.loads(_env(launcher)["DMS_JR_SOURCE_NODES"]) == ["n1", "n2"]


@pytest.mark.parametrize("candidates,pc", [
    ({"primary": ["n1"]}, 1), ({"primary": ["n1", "n2"]}, 16), ({"primary": ["a", "b", "c", "d"]}, 4),
    ({"source": ["s1"], "destination": ["d1", "d2"]}, 6),
])
def test_every_worker_requests_exactly_its_hostfile_slots(candidates, pc):
    # 불변식: 요청 코어 = slots = _processes_per_node. 어느 워커 task 든, env 의 slots 와 requests.cpu 가 같은 문자열.
    op = {"operation": "sync", "tool": "nsync", "paths": _NSYNC_PATHS} if "source" in candidates else {}
    for task in _build(candidates=candidates, process_count=pc, **op)["spec"]["tasks"]:
        c = _c(task)
        if task["name"] == "launcher":
            continue
        assert c["resources"]["requests"]["cpu"] == _env(c)["DMS_JR_PROCESSES_PER_NODE"]
        assert set(c["resources"]) == {"requests"} and set(c["resources"]["requests"]) == {"cpu"}


@pytest.mark.parametrize("phase", ["preflight", "exec_preflight"])
def test_preflight_pods_stay_best_effort(phase):
    # 워커가 코어를 다 예약한 노드에서도 다음 잡의 점검이 서야 한다 -- resources 키 자체가 없다.
    pod = build_preflight_pod(_spec(phase=phase), job_image="i", namespace="dms", volumes=_VOL, node="n1")
    (c,) = pod["spec"]["containers"]
    assert "resources" not in c


def test_launcher_resources_are_not_shared_between_manifests():
    a = _c(_tasks(_build())["launcher"])
    a["resources"]["requests"]["cpu"] = "999"
    assert _c(_tasks(_build())["launcher"])["resources"] == {"requests": {"cpu": "100m"}}


@pytest.mark.parametrize("bad", [None, 0, -1, -3, True, False, 8.0, "8", [8], 10 ** 9])
def test_tampered_process_count_raises_instead_of_submitting(bad):
    # 예전: 0·음수는 1 로, True 는 1 로 조용히 통과, 8.0 은 "8.0" 으로 러너 int() 에서 크래시, 10**9 는 rank 폭주.
    with pytest.raises(ValueError):
        _build(process_count=bad)


def test_processes_per_node_is_the_single_source():
    assert processes_per_node(16, {"primary": ["a", "b"]}) == 8
    assert processes_per_node(1, {"primary": ["a", "b"]}) == 1            # 내림이지만 ≥ 1(기존 동작)
    assert processes_per_node(9, {"source": ["a"], "destination": ["a", "b"]}) == 3
    assert processes_per_node(5, {"primary": []}) == 5                   # 후보가 비면 1대로(스텁 경로)
    assert processes_per_node(MAX_PROCESSES_PER_WORKER, {"primary": ["a"]}) == MAX_PROCESSES_PER_WORKER
    with pytest.raises(ValueError):
        processes_per_node(MAX_PROCESSES_PER_WORKER + 1, {"primary": ["a"]})
    assert worker_pod_count({"source": ["a"], "destination": ["a", "b"]}) == 3
    assert worker_pod_count({"primary": ["a", "b"]}) == 2
