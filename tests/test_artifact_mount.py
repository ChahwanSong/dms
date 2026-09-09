"""아티팩트 base 전용 마운트(artifact_base.ARTIFACT_MOUNT, 2026-09-09 d129).

공용 디렉터리(base 의 부모, /cephfs/dms)를 root:root 770 으로 잠가도 잡이 돌아야 한다.
러너(launcher)는 root 지만 도구·rank.sh 는 요청자 uid 라 파드 안 경로의 모든 부모를
통과해야 한다 -- base 를 전용 hostPath 볼륨으로 받으면 마운트 루트가 base 자체라
호스트 부모 권한을 보지 않는다. 계약: (a) 어떤 스토리지 구성에서도 base 볼륨은
항상 존재하고 mountPath 는 ARTIFACT_MOUNT, (b) 러너 경로(DMS_JR_ARTIFACT_DIR)와 preflight
파드 볼륨도 같은 마운트를 쓴다, (c) 스토리지 mount_path 는 그 경로와 겹칠 수 없다.
"""
import pytest

from dms.artifact_base import ARTIFACT_MOUNT
from dms.domain import DomainValidationError
from dms.execution import JobSpec
from dms.execution_manifests import build_preflight_pod, build_volcano_job
from dms.execution_volcano import VolcanoExecutionAdapter
from dms.repositories.storages import _validate

JOB = "b" * 32


def _spec(**over):
    base = dict(job_id=JOB, phase="execution", operation="scan", tool="dscan",
                dryrun=False, identity={"uid": 10001, "gid": 10000, "username": "alice"},
                paths={"target": "/cephfs/managed/t", "storage": "s1"},
                options={}, candidates={"primary": ["n1"]}, process_count=1,
                queue="dms-data", priority_class="dms-mid",
                artifact_base="file:///cephfs/dms/artifacts")
    base.update(over)
    return JobSpec(**base)


def _adapter(mount_path="/cephfs"):
    return VolcanoExecutionAdapter(
        object(), job_image="img", namespace="dms",
        storages_lookup=lambda n: {"mount_path": mount_path, "managed_root": mount_path},
        read_text=lambda p: None, artifact_base="file:///cephfs/dms/artifacts")


@pytest.mark.parametrize("mount_path", ["/cephfs", "/data", "/cephfs/dms/artifacts"])
def test_artifact_base_is_always_a_dedicated_volume(mount_path):
    vols = _adapter(mount_path)._volumes(_spec())
    art = [v for v in vols if v["mountPath"] == ARTIFACT_MOUNT]
    assert len(art) == 1
    assert art[0]["hostPath"]["path"] == "/cephfs/dms/artifacts"
    assert art[0]["name"] == "dms-artifact-base"
    # 스토리지 마운트는 그대로 자기 경로에(파드 안 데이터 경로는 호스트와 같다)
    assert {v["mountPath"] for v in vols if v["mountPath"] != ARTIFACT_MOUNT} == {mount_path}
    assert len({v["name"] for v in vols}) == len(vols), "볼륨 이름 충돌"


def test_runner_and_preflight_see_the_same_mount():
    a = _adapter()
    spec = _spec()
    vols = a._volumes(spec)
    job = build_volcano_job(spec, job_image="img", namespace="dms", volumes=vols)
    for task in job["spec"]["tasks"]:
        pod = task["template"]["spec"]
        mounts = {m["mountPath"] for m in pod["containers"][0]["volumeMounts"]}
        assert ARTIFACT_MOUNT in mounts, task["name"]
        hosts = {v["hostPath"]["path"] for v in pod["volumes"]}
        assert "/cephfs/dms/artifacts" in hosts
    launcher = job["spec"]["tasks"][0]
    env = {e["name"]: e["value"] for e in launcher["template"]["spec"]["containers"][0]["env"]}
    assert env["DMS_JR_ARTIFACT_DIR"] == f"{ARTIFACT_MOUNT}/{JOB}/execution"
    pod = build_preflight_pod(_spec(phase="preflight"), job_image="img", namespace="dms",
                              volumes=a._volumes(_spec(phase="preflight")), node="n1")
    mounts = {m["mountPath"] for m in pod["spec"]["containers"][0]["volumeMounts"]}
    assert ARTIFACT_MOUNT in mounts


def test_storage_mount_path_may_not_collide_with_the_artifact_mount():
    for bad in (ARTIFACT_MOUNT, ARTIFACT_MOUNT + "/x"):
        with pytest.raises(DomainValidationError) as e:
            _validate("s1", bad, bad, "cephfs")
        assert e.value.reason_code == "invalid_storage"
    _validate("s1", "/cephfs", "/cephfs/managed", "cephfs")   # 정상은 통과
