"""아티팩트 base 통과 검사(2026-09-30 아티팩트 쓰기 감사) + 보조 그룹 잡의 자격·base 검사(2026-10-07).

도구는 요청자 uid·주 gid 로 <base>/<job>/<phase> 의 mpi-hostfile·rank.sh 를 읽고 dscan 리포트를 쓴다.
전용 마운트는 base 의 부모 권한을 건너뛰지만 base 자체의 x 는 커널이 본다 -- base 가 root 700/750/770
이면 비 root 잡은 preview/execution 에서야 "unable to open the hostfile" 로 죽는다. 그래서 preflight(같은
uid·gid)가 먼저 artifact_base_not_traversable 로 거부한다.

보조 그룹이 실린 잡(2026-10-07)은 preflight·워커 rank 가 LDAP 보조 그룹을 갖지만 launcher 의 mpirun 은 그룹
없이 hostfile 을 읽는다 -- 그룹을 가진 preflight 의 `test -x` 는 그룹 x 로 거짓 통과할 수 있어 other-x 비트를
직접 보고, base 가 그 그룹으로 **쓰기 가능**한지도 본다(access(2) -- 모든 ACL 종류 반영). 맨 앞에는 자기 그룹이
계획된 집합과 **같은지**(양방향 + id -g) 자기검증이 선다.
"""
import os
import subprocess
import sys

import pytest

from dms.artifact_base import ARTIFACT_MOUNT
from dms.execution import JobSpec
from dms.execution_manifests import (_ARTIFACT_BASE_CHECK, _ARTIFACT_BASE_NOT_WRITABLE_CHECK,
                                     _ARTIFACT_BASE_OTHER_X_CHECK, _SUPP_GIDS_SELF_CHECK,
                                     build_preflight_pod)

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


# ---- 보조 그룹 잡(2026-10-07): 자격 자기검증(같은 집합) · base other-x · base 쓰기 불가 ----

_ID_SHIM = r'''import os, sys
a = sys.argv[1:]
if a == ["-g"]:
    print(os.environ.get("T_SELF_GID", "0")); sys.exit(0)
if a == ["-G"]:
    print(os.environ.get("T_SELF_GROUPS", "0")); sys.exit(0)
sys.exit(2)
'''


def _self_check(tmp_path, **env):
    # 파드 안의 `id -g`·`id -G` 를 심으로 흉내 낸다(그 파드가 실제로 받은 그룹을 T_SELF_* 로 지정).
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    shim = bindir / "id"
    shim.write_text(f"#!{sys.executable}\n{_ID_SHIM}")
    shim.chmod(0o755)
    base = {"PATH": f"{bindir}:{os.environ['PATH']}", "DMS_JR_GID": "10000",
            "DMS_JR_SUPP_GIDS": "10010,20001", "T_SELF_GID": "10000"}
    base.update(env)
    proc = subprocess.run(["sh", "-c", _SUPP_GIDS_SELF_CHECK + "echo DMS_PREFLIGHT_OK"],
                          env=base, capture_output=True, text=True)
    return proc.returncode, proc.stdout


@pytest.mark.parametrize("groups", ["10000 10010 20001", "20001 10000 10010"])
def test_self_check_passes_on_the_exact_set_in_any_order(tmp_path, groups):
    assert _self_check(tmp_path, T_SELF_GROUPS=groups) == (0, "DMS_PREFLIGHT_OK\n")


@pytest.mark.parametrize("env", [
    {"T_SELF_GROUPS": "10000 10010"},                                   # 누락(클러스터가 그룹을 버림)
    {"T_SELF_GROUPS": "10000 10010 20001 4242"},                        # 기대 밖 gid(웹훅·fsGroup·CRI Merge)
    {"T_SELF_GID": "4242", "T_SELF_GROUPS": "4242 10010 20001"},        # 주 gid 다름(runAsGroup 변경)
    {"T_SELF_GROUPS": "10010 20001"},                                   # -G 에 주 gid 없음
    {"DMS_JR_SUPP_GIDS": "1*", "T_SELF_GROUPS": "10000 1"},             # 기형 목록(글롭 문자)
    {"DMS_JR_SUPP_GIDS": "", "T_SELF_GROUPS": "10000"},                 # 빈 목록(그룹 잡에만 붙는 조각)
    {"DMS_JR_GID": "10000;id", "T_SELF_GROUPS": "10000 10010 20001"},   # 기형 주 gid
    {"DMS_JR_GID": "", "T_SELF_GROUPS": "10000 10010 20001"},           # 빈 주 gid
])
def test_self_check_rejects_anything_but_the_exact_set(tmp_path, env):
    rc, out = _self_check(tmp_path, **env)
    assert rc == 1
    assert "DMS_PREFLIGHT_REASON=identity_groups_not_applied" in out and "DMS_PREFLIGHT_OK" not in out


def _run_base_snippet(snippet, base_dir):
    return subprocess.run(["sh", "-c", snippet.replace(ARTIFACT_MOUNT, str(base_dir)) + "echo DMS_PREFLIGHT_OK"],
                          capture_output=True, text=True)


@pytest.mark.parametrize("mode, ok", [(0o755, True), (0o711, True), (0o1755, True), (0o1754, False),
                                      (0o750, False), (0o710, False), (0o770, False), (0o700, False)])
def test_other_x_check_reads_the_mode_bits(tmp_path, mode, ok):
    # 비트를 직접 본다(access(2) 가 아니다) -- 그룹을 가진 preflight 가 그룹 x 로 통과해도 launcher 는 그룹이 없다.
    base = tmp_path / "artifacts"; base.mkdir()
    base.chmod(mode)
    try:
        proc = _run_base_snippet(_ARTIFACT_BASE_OTHER_X_CHECK, base)
    finally:
        base.chmod(0o755)
    if ok:
        assert (proc.returncode, proc.stdout.strip()) == (0, "DMS_PREFLIGHT_OK")
    else:
        assert proc.returncode == 1
        assert "DMS_PREFLIGHT_REASON=artifact_base_not_traversable" in proc.stdout


def test_other_x_check_fails_closed_when_stat_fails(tmp_path):
    proc = _run_base_snippet(_ARTIFACT_BASE_OTHER_X_CHECK, tmp_path / "missing")
    assert proc.returncode == 1 and "artifact_base_not_traversable" in proc.stdout


@pytest.mark.skipif(os.geteuid() == 0, reason="root 는 권한 검사를 우회한다")
@pytest.mark.parametrize("mode, writable", [(0o775, True), (0o755, True), (0o555, False)])
def test_not_writable_check_uses_access(tmp_path, mode, writable):
    # 테스트 사용자는 소유자라 0775·0755 는 쓰기 가능(파드에선 그룹·ACL 로 쓰기 가능한 경우와 같은 판정), 0555 는 불가.
    base = tmp_path / "artifacts"; base.mkdir()
    base.chmod(mode)
    try:
        proc = _run_base_snippet(_ARTIFACT_BASE_NOT_WRITABLE_CHECK, base)
    finally:
        base.chmod(0o755)
    if writable:
        assert proc.returncode == 1
        assert "DMS_PREFLIGHT_REASON=artifact_base_group_writable" in proc.stdout
    else:
        assert (proc.returncode, proc.stdout.strip()) == (0, "DMS_PREFLIGHT_OK")


def test_base_checks_use_the_constant_mount_path_only():
    # 사용자 입력은 positional·env 로만 -- base 조각 셋에 `$` 는 other-x 의 stat 명령 치환 하나뿐이다.
    assert "$" not in _ARTIFACT_BASE_CHECK and "$" not in _ARTIFACT_BASE_NOT_WRITABLE_CHECK
    assert _ARTIFACT_BASE_OTHER_X_CHECK.count("$") == 1
    for snippet in (_ARTIFACT_BASE_CHECK, _ARTIFACT_BASE_OTHER_X_CHECK, _ARTIFACT_BASE_NOT_WRITABLE_CHECK):
        assert ARTIFACT_MOUNT in snippet


def _group_spec(operation="scan"):
    spec = _spec(operation)
    return JobSpec(**{**spec.__dict__, "identity": {
        **spec.identity, "username": "alice", "supplementary_gids": [10010],
        "supplementary_gids_status": "applied"}})


@pytest.mark.parametrize("operation, role", [
    ("sync", None), ("sync", "source"), ("sync", "destination"), ("scan", None), ("rm", None)])
def test_group_jobs_get_self_check_then_all_three_base_checks(operation, role):
    script = _script(_group_spec(operation), [_STORAGE, _BASE], role=role)
    assert script.startswith(_SUPP_GIDS_SELF_CHECK + _ARTIFACT_BASE_CHECK + _ARTIFACT_BASE_OTHER_X_CHECK
                             + _ARTIFACT_BASE_NOT_WRITABLE_CHECK)


def test_jobs_without_groups_get_only_the_x_check():
    # 그룹 없는 잡(배포 전 잡 포함)은 이 기능 이전과 같다 -- other-x·쓰기 불가 검사는 그룹 잡에만.
    script = _script(_spec("scan"), [_STORAGE, _BASE])
    assert _ARTIFACT_BASE_OTHER_X_CHECK not in script and _ARTIFACT_BASE_NOT_WRITABLE_CHECK not in script
    assert _SUPP_GIDS_SELF_CHECK not in script
