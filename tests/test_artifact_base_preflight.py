"""아티팩트 base 통과 검사(2026-09-30 아티팩트 쓰기 감사).

러너는 도구를 요청자 uid·주 gid 만(보조 그룹 없음)으로 돌리고, 도구는 <base>/<job>/<phase> 의
mpi-hostfile·rank.sh 를 읽고 dscan 리포트를 쓴다. 전용 마운트는 base 의 부모 권한을 건너뛰지만
base 자체의 x 는 커널이 본다 -- base 가 root 700/750/770 이면 비 root 잡은 preview/execution
에서야 "unable to open the hostfile" 로 죽고, 제어면 3홉 검사는 root 관점이라 못 본다. 그래서
preflight(같은 uid·gid)가 먼저 artifact_base_not_traversable 로 거부한다.
"""
import os
import subprocess

import pytest

from dms.artifact_base import ARTIFACT_MOUNT
from dms.execution import JobSpec
from dms.execution_manifests import _ARTIFACT_BASE_CHECK, build_preflight_pod

_STORAGE = {"name": "cephfs", "hostPath": {"path": "/cephfs"}, "mountPath": "/cephfs"}
_BASE = {"name": "dms-artifact-base", "hostPath": {"path": "/cephfs/dms/artifacts"},
         "mountPath": ARTIFACT_MOUNT}


def _spec(operation="sync", privileged=False):
    paths = ({"source": "/cephfs/a", "source_storage": "s1", "destination": "/cephfs/b",
              "destination_storage": "s1"} if operation == "sync"
             else {"target": "/cephfs/a", "storage": "s1"})
    tool = {"sync": "dsync", "scan": "dscan", "rm": "drm"}[operation]
    return JobSpec(job_id="j1", phase="preflight", operation=operation, tool=tool,
                   dryrun=False,
                   identity={"uid": 0 if privileged else 10001, "gid": 0 if privileged else 10000,
                             "privileged": privileged},
                   paths=paths, options={}, candidates={"primary": ["n1"]}, process_count=8,
                   queue="q", priority_class="p", artifact_base="file:///cephfs/dms/artifacts")


def _script(spec, volumes, role=None):
    pod = build_preflight_pod(spec, job_image="i", namespace="dms", volumes=volumes,
                              node="n1", role=role)
    return pod["spec"]["containers"][0]["command"][2]


@pytest.mark.parametrize("operation, role", [
    ("sync", None), ("sync", "source"), ("sync", "destination"), ("scan", None), ("rm", None)])
def test_check_runs_first_whenever_the_base_is_mounted(operation, role):
    # 모든 연산·nsync 양쪽 노드 파드 -- 워커는 소스·목적지 노드 모두에서 rank.sh 를 읽는다.
    script = _script(_spec(operation), [_STORAGE, _BASE], role=role)
    assert script.startswith(_ARTIFACT_BASE_CHECK)
    assert script.count("artifact_base_not_traversable") == 1


def test_no_check_without_the_base_mount():
    # 검사할 대상이 없다 -- 마운트 없는 파드에서 없는 경로를 보고 오탐하지 않는다.
    assert "artifact_base_not_traversable" not in _script(_spec(), [_STORAGE])


def test_check_uses_the_constant_mount_path_only():
    # 사용자 입력(경로)은 positional 로만 -- 이 조각엔 상수 마운트 경로 말고는 없다.
    assert _ARTIFACT_BASE_CHECK.startswith(f"test -x {ARTIFACT_MOUNT} || ")
    assert "$" not in _ARTIFACT_BASE_CHECK


def _run_snippet(base_dir):
    # 상수 경로를 임시 디렉터리로 바꿔 **실제 셸**에서 조각의 판정만 본다.
    snippet = _ARTIFACT_BASE_CHECK.replace(ARTIFACT_MOUNT, str(base_dir)) + "echo DMS_PREFLIGHT_OK"
    return subprocess.run(["sh", "-c", snippet], capture_output=True, text=True)


@pytest.mark.skipif(os.geteuid() == 0, reason="root 는 권한 검사를 우회한다")
def test_shell_rejects_a_base_the_run_identity_cannot_traverse(tmp_path):
    base = tmp_path / "artifacts"; base.mkdir()
    base.chmod(0o600)              # 실행 신원에게 x 없음(운영 700 root 소유의 비 root 관점)
    try:
        proc = _run_snippet(base)
    finally:
        base.chmod(0o755)
    assert proc.returncode == 1
    assert "DMS_PREFLIGHT_REASON=artifact_base_not_traversable" in proc.stdout


def test_shell_passes_a_traversable_base(tmp_path):
    base = tmp_path / "artifacts"; base.mkdir()
    base.chmod(0o711)              # 목록은 못 봐도 통과(x)면 충분하다
    proc = _run_snippet(base)
    assert (proc.returncode, proc.stdout.strip()) == (0, "DMS_PREFLIGHT_OK")
