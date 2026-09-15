"""hostPath 볼륨 이름은 RFC 1123 label 이어야 한다(2026-09-15 프로덕션 사고).

mount_path=/mgmt_storage 인 사이트에서 볼륨 이름이 "mgmt_storage"(밑줄) 로 만들어져
apiserver 가 422 로 거부했고, 모든 잡이 preflight submit_failed 로 죽었다. 테스트베드
(/cephfs)에선 드러나지 않았다. 이름 규칙: [a-z0-9]([-a-z0-9]*[a-z0-9])?, 63자 이하."""
import re

import pytest

from dms.execution import JobSpec
from dms.execution_volcano import VolcanoExecutionAdapter, volume_name

RFC1123 = re.compile(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?$")


@pytest.mark.parametrize("path", [
    "/mgmt_storage",                      # 사고 경로(밑줄)
    "/Data/Team_A",                       # 대문자·밑줄
    "/mnt/ceph.fs",                       # 점
    "/cephfs",                            # 테스트베드
    "/한글/경로",                          # 비ASCII 만 -> 슬러그 비어도 이름은 유효
    "/",                                  # 저장소 검증이 막지만 함수는 견뎌야
    "/" + "x" * 120,                      # 길이 상한
    "/a//b_/",                            # 연속 구분자
])
def test_volume_name_is_rfc1123_label(path):
    name = volume_name(path)
    assert RFC1123.fullmatch(name), name
    assert len(name) <= 63


def test_volume_name_is_deterministic_and_collision_free():
    assert volume_name("/mgmt_storage") == volume_name("/mgmt_storage")
    # 슬러그만 쓰면 둘 다 "data-1" -- 한 파드에 같이 실리면 중복 이름으로 422.
    assert volume_name("/data_1") != volume_name("/data-1")
    assert volume_name("/cephfs").startswith("cephfs-")


class _K8s:
    def __init__(self):
        self.created = []

    def create(self, manifest):
        self.created.append(manifest)


def _spec(phase, op="sync"):
    paths = ({"source": "/mgmt_storage/managed/backup", "source_storage": "cephfs-pvs",
              "destination": "/mgmt_storage/managed/backup2", "destination_storage": "cephfs-pvs"}
             if op == "sync" else {"target": "/mgmt_storage/managed/a", "storage": "cephfs-pvs"})
    return JobSpec(job_id="job123456789abc", phase=phase, operation=op,
                   tool="dsync" if op == "sync" else "dscan", dryrun=(phase == "preview"),
                   identity={"uid": 10001, "gid": 10000, "username": "alice"},
                   paths=paths, options={}, candidates={"primary": ["n1"]},
                   process_count=8, queue="dms-data", priority_class="dms-mid",
                   artifact_base="file:///mgmt_storage/dms/artifacts")


def _all_volumes_and_mounts(manifest):
    if manifest["kind"] == "Pod":
        pods = [manifest["spec"]]
    else:  # Volcano Job
        pods = [t["template"]["spec"] for t in manifest["spec"]["tasks"]]
    for pod in pods:
        vols = pod["volumes"]
        mounts = [m for c in pod["containers"] for m in c.get("volumeMounts", [])]
        yield vols, mounts


@pytest.mark.parametrize("phase,op", [("preflight", "sync"), ("execution", "sync"),
                                      ("preflight", "scan"), ("execution", "scan")])
def test_underscore_mount_path_renders_valid_volume_names(phase, op):
    """사고 재현: /mgmt_storage 스토리지로 preflight Pod·Volcano Job 을 만들면 모든
    볼륨 이름이 규칙을 지키고 volumeMounts 가 실제 볼륨 이름을 가리킨다(422 의 두
    번째 cause 가 'volumeMounts[0].name: Not found' 였다)."""
    k8s = _K8s()
    adapter = VolcanoExecutionAdapter(
        k8s, job_image="reg/img:1", namespace="dms",
        storages_lookup=lambda n: {"mount_path": "/mgmt_storage",
                                   "managed_root": "/mgmt_storage/managed"},
        read_text=lambda p: None, artifact_base="file:///mgmt_storage/dms/artifacts")
    adapter.submit(_spec(phase, op))
    assert k8s.created
    for vols, mounts in _all_volumes_and_mounts(k8s.created[0]):
        names = [v["name"] for v in vols]
        assert len(names) == len(set(names)), names
        for n in names:
            assert RFC1123.fullmatch(n) and len(n) <= 63, n
        assert "_" not in "".join(names)
        assert {v["hostPath"]["path"] for v in vols} == {"/mgmt_storage", "/mgmt_storage/dms/artifacts"}
        for m in mounts:
            assert m["name"] in names, m
