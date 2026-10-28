"""sync 경로 봉쇄(dms-ssc d191 → dms-k8s 2026-10-28 이식, 사용자 결정 「한 규칙으로 통일」)를 **실제 sh**(이 호스트 /bin/sh =
dash)로 돌린다.

요청 경로 자체(또는 managed_root 아래 중간 성분)가 심링크면 도구가 그 링크를 따라간다: root sync 가 남의 디렉터리에 쓰고 소유를
바꿨고(포크 보고 ④ C12), 소스 경로가 링크인 `delete` sync 는 목적지를 통째로 지웠다(nsync C13 -- dsync 는 목적지를 링크로 바꿨다).
dms-k8s 는 모든 잡이 root 라 커널 DAC 가 없고, 2026-10-28 전엔 PV 주소 쪽만 봉쇄했다 -- 직접 경로 sync 가 봉쇄 밖이었다. 규칙은
셸 함수 하나(execution_manifests._PATH_CONTAINED_FN)이고 PV·직접 경로 공통이다(PV 쪽은 글자 경계만 더한다 --
tests/test_pv_containment_sh.py). 도는 자리: 미리보기 preflight, 실행 직전 exec_preflight(같은 _preflight_script), 그리고 도구가
경로를 열기 직전 -- dsync 는 런처(mpirun 직전), nsync 는 각 rank(도구 exec 직전). 러너의 판정은 같은 규칙의 Python 미러
(dms_job_runner.contain.path_redirected) -- 같은 트리로 두 판정을 대조한다. dms-ssc 의 워커 셸(sshd 앞) 재확인은 들이지 않는다.
"""
import dataclasses
import json
import os
import subprocess
import sys

import pytest

from dms.execution import JobSpec
from dms.execution_manifests import (EXECUTION_REASONS, PREFLIGHT_REASONS, _PATH_CONTAINED_FN, _launcher_env,
                                     _preflight_script, _worker_command_script, build_preflight_pod, build_volcano_job)
from dms_job_runner import contain
from dms_job_runner.runner import path_redirected, run_job

_SRC = "DMS_PREFLIGHT_REASON=source_path_redirected"
_DST = "DMS_PREFLIGHT_REASON=destination_path_redirected"
_PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")


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
    # 이름이 줄바꿈으로 끝나는 성분(2026-10-28 리뷰): 링크 「x\n」 옆에 실제 「x」 -- 셸 $(dirname) 이 끝 줄바꿈을 지워 x 로 읽었다.
    (m / "users/alice/x").mkdir()
    (m / "users/alice/x\n").symlink_to("../bob/secret")
    (m / "users/alice/data\n").mkdir()                                        # 실제 디렉터리 -- 셸 $(realpath) 만 막았다
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
    # 줄바꿈이 든 경로 -- 셸·러너 둘 다 막는다(2026-10-28 리뷰, fail-closed 로 맞춘 대칭). 예전엔 셸만 「x\n/new」를 통과시켰고
    # (명령 치환이 끝 줄바꿈을 지워 옆의 실제 x 로 판정 -- fail-open) 「data\n」은 셸만 막았다(realpath 결과가 잘려 오탐).
    ("m", "users/alice/x\n/new"),                # 끝이 줄바꿈인 링크 성분 아래(옆에 실제 x) -- 예전 셸 IN
    ("m", "users/alice/x\n"),                    # 그 링크 자신
    ("m", "users/alice/data\n"),                 # 끝이 줄바꿈인 실제 디렉터리 -- 예전 러너 IN
    ("m", "users/alice/data\n/new"),             # 그 아래 아직 없는 경로 -- 예전엔 둘 다 IN
    ("m", "users/alice/a\nb"),                   # 가운데 줄바꿈(없는 경로) -- 줄바꿈은 어디에 있든 막는다
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
    assert _contained_sh(f"{m}/x", "") is False                                      # 빈 root(변조 행)
    assert path_redirected(f"{m}/x", "") is True


# ---- preflight(미리보기·실행 직전 같은 스크립트) -- 직접 경로 sync ----

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


@pytest.mark.parametrize("phase", ["preflight", "exec_preflight"])
def test_preflight_rejects_a_destination_root_that_is_a_symlink_to_someone_else(tree, phase):
    # C12: root 실행이라 쓰기 검사는 늘 통과 -- 봉쇄가 유일한 막이다. 미리보기·실행 직전 같은 스크립트.
    m = str(tree / "m")
    rc, out = _run(_spec(f"{m}/users/alice/proj/src", f"{m}/users/alice/to-bob", m, m, phase=phase))
    assert rc == 1 and _DST in out and "DMS_PREFLIGHT_OK" not in out, out
    assert (tree / "m/users/bob/secret/victim.txt").read_text() == "bob"


def test_preflight_rejects_a_source_root_that_is_a_symlink(tree):
    # C13: 소스가 링크면 도구는 루트를 「링크」로 보고 나열하지 않는다 -- delete 면 목적지 전체가 「목적지 전용」이었다.
    m = str(tree / "m")
    rc, out = _run(_spec(f"{m}/users/alice/latest", f"{m}/users/alice/proj/dst", m, m))
    assert rc == 1 and _SRC in out and _DST not in out, out


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


def test_preflight_uses_each_sides_own_root(tree):
    # 두 쪽은 각자의 스토리지 managed_root 를 기준으로 본다 -- 목적지 경로가 소스 root 아래가 아니어도 된다.
    m = tree / "m"
    rc, out = _run(_spec(f"{m}/users/alice/proj/src", f"{m}/shared/latest-real/new", f"{m}/users", f"{m}/shared"))
    assert (rc, out.strip().splitlines()[-1]) == (0, "DMS_PREFLIGHT_OK"), out
    rc, out = _run(_spec(f"{m}/users/alice/proj/src", f"{m}/users/alice/proj/dst", f"{m}/users", f"{m}/shared"))
    assert rc == 1 and _DST in out                                               # 목적지가 자기 root 밖


def test_containment_comes_before_the_follow_the_link_checks(tree):
    # 링크 너머가 읽기 불가·파일이어도 사유는 「다른 곳을 가리킨다」 -- 따라간 검사의 사유로 진짜 원인을 가리지 않는다.
    m = tree / "m"
    (m / "users/alice/to-file").symlink_to("../bob/secret/victim.txt")
    rc, out = _run(_spec(f"{m}/users/alice/proj/src", f"{m}/users/alice/to-file", str(m), str(m)))
    assert rc == 1 and _DST in out and "destination_not_directory" not in out
    # 없는 소스(오타)는 봉쇄를 통과하고 종전 사유를 낸다
    rc, out = _run(_spec(f"{m}/users/alice/typo", f"{m}/users/alice/proj/dst", str(m), str(m)))
    assert rc == 1 and "DMS_PREFLIGHT_REASON=source_not_readable" in out and _SRC not in out
    # 없는 목적지 상위(오타)도 종전 사유
    rc, out = _run(_spec(f"{m}/users/alice/proj/src", f"{m}/users/alice/nope/dst", str(m), str(m)))
    assert rc == 1 and "DMS_PREFLIGHT_REASON=destination_parent_not_writable" in out and _DST not in out


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


def test_newline_names_are_refused_by_preflight_and_runner_alike(tree):
    # 2026-10-28 리뷰: 링크 「x\n」(옆에 실제 x)를 지나는 목적지를 dsync preflight·nsync 목적지 역할 preflight 가 OK 로 통과시켰다
    # (러너 6d·rank 의 Python 재확인만 막았다). 이제 셸·러너가 같은 사유로 막는다 -- 제출은 이 이름을 unsafe_path 로 거부한다.
    m = str(tree / "m")
    dst = f"{m}/users/alice/x\n/new"
    for spec, role in ((_spec(f"{m}/users/alice/proj/src", dst, m, m), None),
                       (_spec(f"{m}/users/alice/proj/src", dst, m, m, primary=False), "destination")):
        rc, out = _run(spec, role=role)
        assert rc == 1 and _DST in out and "DMS_PREFLIGHT_OK" not in out, (role, out)
        entries = contain.parse_entries(_launcher_env(spec)["DMS_JR_CONTAIN"])
        assert contain.first_blocked(entries) == ("destination", dst, m, m)
    # 반대 방향(예전 셸만의 오탐): 실제 디렉터리 「data\n」 소스 -- 이제 러너도 막는다(같은 판정)
    src = f"{m}/users/alice/data\n"
    rc, out = _run(_spec(src, f"{m}/users/alice/proj/dst/new", m, m))
    assert rc == 1 and _SRC in out, out
    assert path_redirected(src, m) is True
    assert (tree / "m/users/bob/secret/victim.txt").read_text() == "bob"


def test_non_root_sync_is_contained_too(tree):
    # dms-k8s 는 모든 잡이 root 지만 빌더는 입력을 믿지 않는다 -- 비 root 스냅숏(방어 기본값 경로)도 같은 봉쇄를 받는다.
    m = str(tree / "m")
    rc, out = _run(_spec(f"{m}/users/alice/proj/src", f"{m}/users/alice/via/new", m, m, privileged=False))
    assert rc == 1 and _DST in out


def test_paths_and_roots_are_positional_never_inlined(tree):
    evil = tree / "m" / 'a"$(touch PWNED)`touch PWNED2`'
    (evil / "src").mkdir(parents=True)
    m = str(tree / "m")
    spec = _spec(f"{evil}/src", f"{evil}/dst", m, m)
    script, args = _preflight_script(spec)
    assert str(evil) not in script and m not in script
    assert args == [f"{evil}/src", f"{evil}/dst", "root", m, m]
    rc, out = _run(spec)
    assert (rc, out.strip().splitlines()[-1]) == (0, "DMS_PREFLIGHT_OK"), out
    assert not (tree / "PWNED").exists() and not os.path.exists("PWNED") and not os.path.exists("PWNED2")


def test_reasons_are_registered_for_preflight_and_execution():
    for code in ("source_path_redirected", "destination_path_redirected"):
        assert code in PREFLIGHT_REASONS and code in EXECUTION_REASONS
    # 러너 상수와 제어면 화이트리스트가 같다(러너는 dms 를 import 하지 않는다 -- 계약)
    assert set(contain.REASON_BY_SIDE.values()) <= EXECUTION_REASONS
    assert {f"DMS_EXEC_REASON={c}" for c in contain.REASON_BY_SIDE.values()} == contain.REDIRECT_LINES


@pytest.mark.parametrize("missing", ["source_root", "destination_root"])
def test_sync_without_roots_is_refused_by_the_builders(missing):
    spec = _spec("/c/a", "/c/b", "/c", "/c")
    paths = dict(spec.paths)
    del paths[missing]
    for bad in (paths, {**paths, missing: ""}, {**paths, missing: None}):
        bad_spec = dataclasses.replace(spec, paths=bad)
        with pytest.raises(ValueError):
            _preflight_script(bad_spec)
        with pytest.raises(ValueError):
            build_volcano_job(bad_spec, job_image="i", namespace="dms", volumes=[])
        with pytest.raises(ValueError):
            _launcher_env(bad_spec)


# ---- 도구가 경로를 열기 직전: dsync 는 런처(mpirun 직전), nsync 는 rank(도구 exec 직전) ----

def _task_env(job, task):
    t = next(t for t in job["spec"]["tasks"] if t["name"] == task)
    return {e["name"]: e["value"] for e in t["template"]["spec"]["containers"][0]["env"]}


def test_worker_shell_does_not_recheck_the_runner_points_do():
    # dms-ssc 는 워커 셸(sshd 앞)에서 다시 본다 -- dms-k8s 는 그보다 늦은 런처·rank 자리라 들이지 않는다.
    assert "dms_path_contained" not in _worker_command_script() and "DMS_EXEC_REASON" not in _worker_command_script()
    spec = _spec("/c/m/a", "/d/m/b", "/c/m", "/d/m", phase="execution", primary=False)
    vol = [{"name": "s", "hostPath": {"path": "/c"}, "mountPath": "/c"}]
    job = build_volcano_job(spec, job_image="i", namespace="dms", volumes=[], role_volumes={"source": vol, "destination": vol})
    for task in ("source-worker", "destination-worker"):
        assert "DMS_JR_CONTAIN" not in _task_env(job, task)
    launcher = _task_env(job, "launcher")
    assert json.loads(launcher["DMS_JR_CONTAIN"]) == [["source", "/c/m/a", "/c/m", "/c/m"],
                                                      ["destination", "/d/m/b", "/d/m", "/d/m"]]
    primary = build_volcano_job(_spec("/c/m/a", "/c/m/b", "/c/m", "/c/m", phase="preview"), job_image="i",
                                namespace="dms", volumes=vol)
    assert json.loads(_task_env(primary, "launcher")["DMS_JR_CONTAIN"])[1] == ["destination", "/c/m/b", "/c/m", "/c/m"]


class _Rec:
    def __init__(self):
        self.writes, self.ran = {}, []

    def write_text(self, path, content, *, append=False):
        self.writes[path] = content

    def run(self, command):
        self.ran.append(command)

        class R:
            returncode = 0
            stdout = "10.0.0.1 w\n" if command[0] == "getent" else "ok\n"
            stderr = ""
        return R()


def _launcher_run(spec, art):
    job = build_volcano_job(spec, job_image="i", namespace="dms", volumes=[])
    env = {**_task_env(job, "launcher"), "DMS_JR_ARTIFACT_DIR": art}
    rec = _Rec()
    rc = run_job(env, run=rec.run, write_text=rec.write_text, read_text=lambda p: "", sleep=lambda s: None,
                 wait_hostfile=lambda role=None: (["w0"], "/tmp/hostfile"))
    return rc, rec


def test_dsync_launcher_refuses_a_path_swapped_after_the_preflight(tree, capsys):
    # 직접 경로 dsync: 실행 vcjob 의 런처 env 그대로 러너를 돌린다 -- preflight 뒤 목적지를 링크로 바꿨으면 mpirun 없이 끝낸다.
    m = str(tree / "m")
    rc, rec = _launcher_run(_spec(f"{m}/users/alice/proj/src", f"{m}/users/alice/to-bob", m, m, phase="execution"),
                            str(tree / "art"))
    out = capsys.readouterr().out.split("\n")
    assert rc == 1 and not any("mpirun" in cmd for cmd in rec.ran)
    assert "DMS_EXEC_REASON=destination_path_redirected" in out
    assert json.loads(rec.writes[f"{tree / 'art'}/summary.json"]) == {"returncode": None, "files": None, "bytes": None}
    rc, rec = _launcher_run(_spec(f"{m}/users/alice/latest", f"{m}/users/alice/proj/dst", m, m, phase="preview"),
                            str(tree / "art"))
    assert rc == 1 and "DMS_EXEC_REASON=source_path_redirected" in capsys.readouterr().out.split("\n")
    rc, rec = _launcher_run(_spec(f"{m}/users/alice/proj/src", f"{m}/users/alice/proj/dst/new", m, m, phase="execution"),
                            str(tree / "art"))
    assert rc == 0 and any("mpirun" in cmd for cmd in rec.ran)


def test_nsync_rank_refuses_a_path_swapped_after_the_preflight(tree):
    # 직접 경로 nsync: 런처는 데이터를 마운트하지 않는다 -- rank.sh 첫 줄이 자기 역할 쪽만 본다(소스 rank 0, 목적지 rank 1).
    m = str(tree / "m")
    spec = _spec(f"{m}/users/alice/proj/src", f"{m}/users/alice/to-bob", m, m, phase="execution", primary=False)
    vol = [{"name": "s", "hostPath": {"path": "/"}, "mountPath": "/"}]
    job = build_volcano_job(spec, job_image="i", namespace="dms", volumes=[], role_volumes={"source": vol, "destination": vol})
    env = {**_task_env(job, "launcher"), "DMS_JR_ARTIFACT_DIR": str(tree / "art")}
    rec = _Rec()
    run_job(env, run=rec.run, write_text=rec.write_text, read_text=lambda p: "", sleep=lambda s: None,
            wait_hostfile=lambda role=None: (["w0"], "/tmp/hostfile"))
    script = rec.writes[f"{tree / 'art'}/rank.sh"]
    assert script.startswith("#!/bin/sh\npython3 -m dms_job_runner.contain 1 '")
    penv = {**os.environ, "PYTHONPATH": _PKG}
    for rank, marker in (("0", None), ("1", "DMS_EXEC_REASON=destination_path_redirected")):
        proc = subprocess.run(["sh", "-c", script.replace("python3 ", f"{sys.executable} ", 1)],
                              capture_output=True, text=True, env={**penv, "OMPI_COMM_WORLD_RANK": rank})
        if marker is None:      # 소스 쪽은 멀쩡하다 -- 검사를 지나 exec nsync(이 호스트엔 없어 127)
            assert proc.returncode == 127 and "DMS_EXEC_REASON=" not in proc.stdout, (rank, proc.stdout, proc.stderr)
        else:
            assert proc.returncode == 1 and marker in proc.stdout.split("\n"), (rank, proc.stdout)


def test_direct_entries_keep_the_old_runner_shape():
    # 옛 잡 이미지(2026-10-28 전 러너)도 같은 env 키를 4칸 문자열 모양으로 읽는다 -- 직접 경로 쪽은 경계 = 기준이라 그 규칙(PV
    # 루트 = 기준)에서도 같은 뜻이 된다. 모양이 틀리면 옛 러너가 모든 sync 를 막았을 것이다.
    env = _launcher_env(_spec("/c/m/a", "/d/m/b", "/c/m", "/d/m", phase="execution"))
    entries = contain.parse_entries(env["DMS_JR_CONTAIN"])
    assert all(len(e) == 4 and all(isinstance(v, str) and v for v in e) for e in entries)
    assert all(e[2] == e[3] for e in entries)
