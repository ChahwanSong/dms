"""sync 경로 봉쇄(2026-10-27, 포크 보고 ④ -- 사용자 「경로의 모든 단계를 보고 한번 아니라 여러번 보고 거부」)를 **실제 sh**로
돌린다.

요청 경로 자체(또는 managed_root 아래 중간 성분)가 심링크면 도구가 그 링크를 따라간다: root sync 가 남의 디렉터리에 쓰고 소유를
바꿨고(C12), 소스 경로가 링크인 `delete` sync 는 목적지를 통째로 지웠다(nsync C13 -- dsync 는 목적지를 링크로 바꿨다). 규칙은
셸 함수 하나(execution_manifests._PATH_CONTAINED_FN)이고 세 자리에서 돈다: 미리보기 preflight, 실행 직전 exec_preflight(같은
_preflight_script), 워커 셸(sshd 앞 -- 도구가 경로를 열기 직전). 러너의 rm 재확인은 같은 규칙의 Python 미러
(dms_job_runner.runner.path_redirected) -- 같은 트리로 두 판정을 대조한다.
"""
import dataclasses
import os
import subprocess

import pytest

from dms.execution import JobSpec
from dms.execution_manifests import (EXECUTION_REASONS, PREFLIGHT_REASONS, _PATH_CONTAINED_FN, _identity_materialize_stmt,
                                     _preflight_script, _worker_command_script, build_preflight_pod, build_volcano_job)
from dms_job_runner.runner import path_redirected

_SRC = "DMS_PREFLIGHT_REASON=source_path_redirected"
_DST = "DMS_PREFLIGHT_REASON=destination_path_redirected"


@pytest.fixture
def tree(tmp_path):
    """tmp/m = managed_root. alice 의 실제 디렉터리와 남의 디렉터리, 그리고 여러 모양의 심링크."""
    m = tmp_path / "m"
    for d in ("users/alice/proj/src", "users/alice/proj/dst", "users/bob/secret", "shared/latest-real", "dir with space/x*[y]"):
        (m / d).mkdir(parents=True)
    (m / "users/bob/secret/victim.txt").write_text("bob")
    (m / "users/alice/proj/src/f.txt").write_text("a")
    (m / "users/alice/to-bob").symlink_to("../bob/secret")                    # 끝 성분 링크(C12 모양)
    (m / "users/alice/via").symlink_to("../bob")                              # 중간 성분 링크
    (m / "users/alice/latest").symlink_to(str(m / "shared/latest-real"))      # 절대 링크(C13 모양)
    (m / "users/alice/dangling").symlink_to("../nowhere")                     # 끊긴 링크
    (m / "users/alice/outside").symlink_to(str(tmp_path))                     # managed_root 밖
    (tmp_path / "mlink").symlink_to(str(m))                                   # managed_root 자신이 링크
    (tmp_path / "up").symlink_to(str(tmp_path))                               # managed_root 위 성분이 링크
    return tmp_path


def _contained_sh(path, root):
    proc = subprocess.run(["sh", "-c", _PATH_CONTAINED_FN + 'if dms_path_contained "$1" "$2"; then echo IN; else echo OUT; fi',
                           "sh", path, root], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip() == "IN"


# (root 상대 표기, 경로 상대 표기) -> 통과 여부
_IN = [
    ("m", "users/alice/proj/src"),               # 정상
    ("m", "users/alice/proj/dst/new/deeper"),    # 아직 없는 새 목적지 -- 있는 가장 깊은 상위(dst)를 본다
    ("m", "users/alice/newdir"),                 # 없는 끝 한 단계
    ("m", ""),                                   # managed_root 자체(스토리지 전체 sync)
    ("m/", "users/alice/proj/src"),              # managed_root 끝 슬래시(DB 표기 그대로)
    ("mlink", "users/alice/proj/src"),           # managed_root 자신이 링크
    ("up/m", "users/alice/proj/src"),            # managed_root 위 성분이 링크
    ("m", "dir with space/x*[y]"),               # 공백·글롭 문자 -- 글자 그대로
]
_OUT = [
    ("m", "users/alice/to-bob"),                 # 끝 성분이 남의 디렉터리 링크(C12)
    ("m", "users/alice/via/secret"),             # 중간 성분 링크
    ("m", "users/alice/via/new"),                # 중간 링크 아래 아직 없는 경로
    ("m", "users/alice/latest"),                 # 소스 「최신판」 절대 링크(C13)
    ("m", "users/alice/dangling"),               # 끊긴 링크(lstat 으로는 있다)
    ("m", "users/alice/dangling/new"),           # 끊긴 링크 아래
    ("m", "users/alice/outside/m"),              # managed_root 밖으로
    ("mlink", "users/alice/to-bob"),             # managed_root 가 링크여도 그 아래 링크는 막는다
    ("m", "users/alice/../bob/secret"),          # 변조 행(「..」)
    ("m", "users/alice/proj/src/"),              # 끝 슬래시
    ("m", "users/alice/proj/./src"),
    ("m", "users//alice"),
]


def _abs(tree, root_rel, rel):
    root = str(tree / root_rel) + ("/" if root_rel.endswith("/") else "")
    path = root.rstrip("/") if rel == "" else os.path.join(root, rel) if not root.endswith("/") else root + rel
    return path, root


@pytest.mark.parametrize("root_rel,rel", _IN)
def test_contained_paths_pass_in_shell_and_runner(tree, root_rel, rel):
    path, root = _abs(tree, root_rel, rel)
    assert _contained_sh(path, root) is True
    assert path_redirected(path, root) is False


@pytest.mark.parametrize("root_rel,rel", _OUT)
def test_redirected_paths_fail_in_shell_and_runner(tree, root_rel, rel):
    path, root = _abs(tree, root_rel, rel)
    assert _contained_sh(path, root) is False
    assert path_redirected(path, root) is True


def test_path_outside_the_given_root_and_missing_root_are_refused(tree):
    m = str(tree / "m")
    assert _contained_sh(f"{m}/users/bob/secret", f"{m}/users/alice") is False      # 다른 root 아래(변조·조합 회귀)
    assert path_redirected(f"{m}/users/bob/secret", f"{m}/users/alice") is True
    assert _contained_sh(f"{tree}/nope/x", f"{tree}/nope") is False                 # root 가 없다
    assert path_redirected(f"{tree}/nope/x", f"{tree}/nope") is True
    assert path_redirected(f"{m}/x", "") is True


# ---- preflight(미리보기·실행 직전 같은 스크립트) ----

def _spec(source, destination, src_root, dst_root, *, phase="preflight", privileged=True, primary=True):
    ident = ({"uid": 0, "gid": 0, "username": "root", "privileged": True} if privileged
             else {"uid": os.getuid(), "gid": os.getgid(), "username": "u", "privileged": False})
    cands = {"primary": ["n1"]} if primary else {"source": ["n1"], "destination": ["n2"]}
    return JobSpec(job_id="j1", phase=phase, operation="sync", tool="dsync" if primary else "nsync", dryrun=False,
                   identity=ident, paths={"source": source, "source_storage": "s1", "source_root": src_root,
                                          "destination": destination, "destination_storage": "s2",
                                          "destination_root": dst_root},
                   options={}, candidates=cands, process_count=1, queue="q", priority_class="p",
                   artifact_base="file:///cephfs/dms/artifacts")


def _run(spec, role=None):
    pod = build_preflight_pod(spec, job_image="i", namespace="dms",
                              volumes=[{"name": "s", "hostPath": {"path": "/"}, "mountPath": "/"}], node="n1", role=role)
    proc = subprocess.run(pod["spec"]["containers"][0]["command"], capture_output=True, text=True)
    return proc.returncode, proc.stdout


def test_preflight_rejects_a_destination_root_that_is_a_symlink_to_someone_else(tree):
    # C12: root 실행이라 쓰기 검사는 늘 통과 -- 봉쇄가 유일한 막이다.
    m = str(tree / "m")
    rc, out = _run(_spec(f"{m}/users/alice/proj/src", f"{m}/users/alice/to-bob", m, m))
    assert rc == 1 and _DST in out and "DMS_PREFLIGHT_OK" not in out, out
    assert (tree / "m/users/bob/secret/victim.txt").read_text() == "bob"


def test_preflight_rejects_a_source_root_that_is_a_symlink(tree):
    # C13: 소스가 링크면 도구는 루트를 「링크」로 보고 나열하지 않는다 -- delete 면 목적지 전체가 「목적지 전용」이었다.
    m = str(tree / "m")
    rc, out = _run(_spec(f"{m}/users/alice/latest", f"{m}/users/alice/proj/dst", m, m))
    assert rc == 1 and _SRC in out, out


def test_preflight_rejects_a_symlink_in_the_middle_of_either_path(tree):
    m = str(tree / "m")
    rc, out = _run(_spec(f"{m}/users/alice/via/secret", f"{m}/users/alice/proj/dst", m, m))
    assert rc == 1 and _SRC in out
    rc, out = _run(_spec(f"{m}/users/alice/proj/src", f"{m}/users/alice/via/newdst", m, m))
    assert rc == 1 and _DST in out


def test_preflight_passes_a_contained_sync_to_a_new_destination(tree):
    m = str(tree / "m")
    rc, out = _run(_spec(f"{m}/users/alice/proj/src", f"{m}/users/alice/proj/dst/new", m, m))
    assert (rc, out.strip().splitlines()[-1]) == (0, "DMS_PREFLIGHT_OK"), out


def test_containment_comes_before_the_follow_the_link_checks(tree):
    # 링크 너머가 읽기 불가·파일이어도 사유는 「다른 곳을 가리킨다」 -- 따라간 검사의 사유로 진짜 원인을 가리지 않는다.
    m = tree / "m"
    (m / "users/alice/to-file").symlink_to("../bob/secret/victim.txt")
    rc, out = _run(_spec(f"{m}/users/alice/proj/src", f"{m}/users/alice/to-file", str(m), str(m)))
    assert rc == 1 and _DST in out and "destination_not_directory" not in out
    # 없는 소스(오타)는 봉쇄를 통과하고 종전 사유를 낸다
    rc, out = _run(_spec(f"{m}/users/alice/typo", f"{m}/users/alice/proj/dst", str(m), str(m)))
    assert rc == 1 and "DMS_PREFLIGHT_REASON=source_not_readable" in out and _SRC not in out


@pytest.mark.parametrize("phase", ["preflight", "exec_preflight"])
def test_nsync_role_scripts_check_their_own_side(tree, phase):
    m = str(tree / "m")
    spec = _spec(f"{m}/users/alice/latest", f"{m}/users/alice/to-bob", m, m, phase=phase, primary=False)
    rc, out = _run(spec, role="source")
    assert rc == 1 and _SRC in out and _DST not in out
    rc, out = _run(spec, role="destination")
    assert rc == 1 and _DST in out and _SRC not in out
    script, args = _preflight_script(spec, role="source")
    assert args == [f"{m}/users/alice/latest", m] and m not in script
    script, args = _preflight_script(spec, role="destination")
    assert args == [f"{m}/users/alice/to-bob", "root", m]


def test_non_root_sync_is_contained_too(tree):
    # 비 root 는 커널 권한이 남의 쓰기를 막지만, 자기 권한 안의 다른 곳으로 새는 것도 확인 창과 다른 일이다 -- 같이 막는다.
    m = str(tree / "m")
    rc, out = _run(_spec(f"{m}/users/alice/proj/src", f"{m}/users/alice/via/new", m, m, privileged=False))
    assert rc == 1 and _DST in out


def test_reasons_are_registered_for_preflight_and_execution():
    for code in ("source_path_redirected", "destination_path_redirected"):
        assert code in PREFLIGHT_REASONS and code in EXECUTION_REASONS


def test_sync_without_roots_is_refused_by_the_builders():
    spec = _spec("/c/a", "/c/b", "/c", "/c")
    paths = dict(spec.paths)
    del paths["destination_root"]
    bad = dataclasses.replace(spec, paths=paths)
    with pytest.raises(ValueError):
        _preflight_script(bad)
    with pytest.raises(ValueError):
        build_volcano_job(bad, job_image="i", namespace="dms", volumes=[])


# ---- 워커 셸(도구가 경로를 열기 직전) ----

def _worker_env(job, task):
    t = next(t for t in job["spec"]["tasks"] if t["name"] == task)
    return {e["name"]: e["value"] for e in t["template"]["spec"]["containers"][0]["env"]}


def test_worker_env_carries_each_side_only_where_it_is_mounted():
    spec = _spec("/c/m/a", "/d/m/b", "/c/m", "/d/m", phase="execution", primary=False)
    vol = [{"name": "s", "hostPath": {"path": "/c"}, "mountPath": "/c"}]
    job = build_volcano_job(spec, job_image="i", namespace="dms", volumes=[],
                            role_volumes={"source": vol, "destination": vol})
    src, dst = _worker_env(job, "source-worker"), _worker_env(job, "destination-worker")
    assert (src["DMS_JR_SYNC_SOURCE"], src["DMS_JR_SYNC_SOURCE_ROOT"]) == ("/c/m/a", "/c/m") and "DMS_JR_SYNC_DEST" not in src
    assert (dst["DMS_JR_SYNC_DEST"], dst["DMS_JR_SYNC_DEST_ROOT"]) == ("/d/m/b", "/d/m") and "DMS_JR_SYNC_SOURCE" not in dst
    launcher = _worker_env(job, "launcher")
    assert not any(k.startswith("DMS_JR_SYNC_") for k in launcher)            # 런처는 데이터 경로를 마운트하지 않는다
    primary = build_volcano_job(_spec("/c/m/a", "/c/m/b", "/c/m", "/c/m", phase="preview"), job_image="i",
                                namespace="dms", volumes=vol)
    w = _worker_env(primary, "worker")
    assert {"DMS_JR_SYNC_SOURCE", "DMS_JR_SYNC_DEST"} <= set(w)


def _worker_checks(env):
    # 워커 스크립트의 봉쇄 부분만(신원 물질화 앞) -- 전체는 sshd 를 띄운다.
    head = _worker_command_script().split(_identity_materialize_stmt())[0]
    proc = subprocess.run(["sh", "-c", head + "\necho WORKER_CONTINUES"], capture_output=True, text=True,
                          env={"PATH": os.environ["PATH"], **env})
    return proc.returncode, proc.stdout


def test_worker_refuses_a_path_swapped_after_the_preflight(tree):
    m = str(tree / "m")
    rc, out = _worker_checks({"DMS_JR_SYNC_DEST": f"{m}/users/alice/to-bob", "DMS_JR_SYNC_DEST_ROOT": m})
    assert rc == 1 and "DMS_EXEC_REASON=destination_path_redirected" in out and "WORKER_CONTINUES" not in out
    rc, out = _worker_checks({"DMS_JR_SYNC_SOURCE": f"{m}/users/alice/latest", "DMS_JR_SYNC_SOURCE_ROOT": m})
    assert rc == 1 and "DMS_EXEC_REASON=source_path_redirected" in out
    rc, out = _worker_checks({"DMS_JR_SYNC_SOURCE": f"{m}/users/alice/proj/src", "DMS_JR_SYNC_SOURCE_ROOT": m,
                              "DMS_JR_SYNC_DEST": f"{m}/users/alice/proj/dst/new", "DMS_JR_SYNC_DEST_ROOT": m})
    assert rc == 0 and "WORKER_CONTINUES" in out and "DMS_EXEC_REASON" not in out
    rc, out = _worker_checks({})                                              # sync 밖(rm·scan) -- 그대로 지나간다
    assert rc == 0 and "WORKER_CONTINUES" in out
