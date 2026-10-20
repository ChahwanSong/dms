"""rm 대상 봉쇄(2026-10-21 리뷰 [0])를 **실제 sh**(이 호스트 /bin/sh = dash)로 돌린다.

배치 rm 은 root 라 preflight·exec_preflight 의 `test -w "$(dirname target)"` 가 늘 참이었다 -- 사용자가 상위 디렉터리를
심링크로 바꿔 두면(users/alice/data -> ../bob) 확인 창의 「실제 경로」와 다른 트리를 root drm 이 지웠다. 봉쇄 조각
(execution_manifests._RM_CONTAINMENT_CHECK)은 `set -e` 아래 case·명령 치환·AND-OR 의 상호작용이라 문자열 계약으로는
증명할 수 없다 -- 실 파드와 같은 명령(["sh", "-c", script, "sh", *args])을 임시 트리에 대고 돌린다. 같은 트리로 러너의
drm 직전 재확인(dms_job_runner.runner.rm_target_redirected)도 돌려 두 판정이 갈라지지 않음을 고정한다.
"""
import json
import os
import subprocess

import pytest

from dms.execution import JobSpec
from dms.execution_manifests import (EXECUTION_REASONS, PREFLIGHT_REASONS, _launcher_env, _preflight_script,
                                     build_preflight_pod, build_volcano_job, tool_argv)
from dms_job_runner import runner
from dms_job_runner.runner import rm_target_redirected, run_job

_REDIRECTED = "DMS_PREFLIGHT_REASON=target_path_redirected"


def _spec(target, root, *, phase="preflight", privileged=False, dryrun=False):
    ident = ({"uid": 0, "gid": 0, "username": "root", "privileged": True} if privileged
             else {"uid": 10001, "gid": 10000, "username": "alice", "privileged": False})
    return JobSpec(job_id="j1", phase=phase, operation="rm", tool="drm", dryrun=dryrun, identity=ident,
                   paths={"target": target, "storage": "s1", "managed_root": root}, options={"recursive": True},
                   candidates={"primary": ["n1"]}, process_count=1, queue="dms-data", priority_class="dms-mid",
                   artifact_base="file:///cephfs/dms/artifacts")


def _cmd(spec):
    pod = build_preflight_pod(spec, job_image="i", namespace="dms",
                              volumes=[{"name": "s", "hostPath": {"path": "/"}, "mountPath": "/"}], node="n1")
    return pod["spec"]["containers"][0]["command"]


def _preflight(target, root):
    proc = subprocess.run(_cmd(_spec(target, root)), capture_output=True, text=True)
    return proc.returncode, proc.stdout


@pytest.fixture
def tree(tmp_path):
    """tmp/m = managed_root. users/alice/data/old, users/bob/old(남의 데이터), 그리고 심링크 몇 개."""
    m = tmp_path / "m"
    for d in ("users/alice/data/old", "users/bob/old", "shared/datasets/old", "dir with space/x*[y]/old"):
        (m / d).mkdir(parents=True)
    (m / "users/bob/old/keep").write_text("bob")
    (m / "users/alice/redirect").symlink_to("../bob")                         # 상대 링크 -> 남의 디렉터리
    (m / "users/alice/absredirect").symlink_to(str(m / "shared/datasets"))    # 절대 링크 -> 같은 스토리지 다른 곳
    (m / "users/alice/outside").symlink_to(str(tmp_path))                      # managed_root 밖
    (m / "users/alice/lastlink").symlink_to("../bob/old")                     # 마지막 성분이 링크(링크만 지운다)
    (tmp_path / "mlink").symlink_to(str(m))                                   # managed_root 자신이 링크
    (tmp_path / "up").symlink_to(str(tmp_path))                               # managed_root 위 성분이 링크
    return tmp_path


# (root 상대 표기, 대상 상대경로) -> 통과 여부
_PASS = [
    ("m", "users/alice/data/old"),             # 정상
    ("m", "users/alice/data"),                 # 한 단계 위
    ("m", "old-top"),                          # 상위 경로 == managed_root(대상은 없어도 된다 -- drm 이 판정)
    ("m/", "users/alice/data/old"),            # managed_root 끝 슬래시(DB 표기 그대로)
    ("mlink", "users/alice/data/old"),         # managed_root 자신이 심링크 -- 양쪽을 같은 realpath 로 본다
    ("up/m", "users/alice/data/old"),          # managed_root 위 성분이 심링크
    ("m", "users/alice/lastlink"),             # 마지막 성분 심링크 -- drm 은 링크 자체만 지운다
    ("m", "dir with space/x*[y]/old"),         # 공백·글롭 문자 -- case 패턴의 "..." 는 글자 그대로
]
_REDIRECT = [
    ("m", "users/alice/redirect/old"),         # 상위가 상대 링크(-> ../bob)
    ("m", "users/alice/absredirect/old"),      # 상위가 절대 링크(-> 같은 스토리지 shared/datasets)
    ("m", "users/alice/outside/m"),            # 상위가 managed_root 밖으로
    ("mlink", "users/alice/redirect/old"),     # managed_root 가 링크여도 그 아래 링크는 막는다
    ("m", "users/alice/../bob/old"),           # 변조 행(「..」) -- 실제 위치와 글자가 맞을 수 없다
    ("m", "users/alice/data/.."),              # 끝이 「..」 -- drm 이 상위를 지우게 된다
    ("m", "users/alice/data/."),
    ("m", "users/alice/data/"),                # 끝 슬래시 -- 마지막 성분 링크를 따라가게 된다
]


@pytest.mark.parametrize("root_rel,target_rel", _PASS)
def test_preflight_passes_when_no_symlink_between_managed_root_and_parent(tree, root_rel, target_rel):
    root = str(tree / root_rel) + ("/" if root_rel.endswith("/") else "")
    target = os.path.join(root, target_rel)
    rc, out = _preflight(target, root)
    assert (rc, out.strip().splitlines()[-1]) == (0, "DMS_PREFLIGHT_OK"), out
    assert rm_target_redirected(target, root) is False


@pytest.mark.parametrize("root_rel,target_rel", _REDIRECT)
def test_preflight_rejects_a_redirected_parent(tree, root_rel, target_rel):
    root = str(tree / root_rel)
    target = f"{root}/{target_rel}"
    rc, out = _preflight(target, root)
    assert rc == 1 and _REDIRECTED in out and "DMS_PREFLIGHT_OK" not in out, out
    assert rm_target_redirected(target, root) is True        # 러너 재확인도 같은 판정
    assert (tree / "m/users/bob/old/keep").read_text() == "bob"


def test_managed_root_with_glob_characters_is_matched_literally(tmp_path):
    # 글롭 문자(* [ ] 공백)가 든 managed_root 도 그 이름 그대로다 -- 통과는 자기 아래만, 글롭으로 읽으면 맞는 이웃은 막힌다.
    # (case 의 "..." 따옴표가 빠져도 뒤의 realpath 비교가 한 번 더 막는다 -- 이 테스트는 두 겹의 합을 본다.)
    root = tmp_path / "r*[x] y"
    (root / "a/b").mkdir(parents=True)
    other = tmp_path / "rZx y"   # 글롭 「r*[x] y」 에 맞는 이름
    (other / "a/b").mkdir(parents=True)
    rc, out = _preflight(f"{root}/a/b", str(root))
    assert (rc, "DMS_PREFLIGHT_OK" in out) == (0, True), out
    rc, out = _preflight(f"{other}/a/b", str(root))
    assert rc == 1 and _REDIRECTED in out
    assert rm_target_redirected(f"{other}/a/b", str(root)) is True


def test_missing_parent_keeps_the_parent_not_writable_reason(tree):
    # 부모 쓰기가 봉쇄보다 먼저다 -- 오타 경로를 「다른 곳을 가리킨다」로 말하지 않는다. 러너는 막기만 한다(사유는 하나).
    root = str(tree / "m")
    rc, out = _preflight(f"{root}/users/nobody/x", root)
    assert rc == 1 and "DMS_PREFLIGHT_REASON=parent_not_writable" in out and _REDIRECTED not in out
    assert rm_target_redirected(f"{root}/users/nobody/x", root) is True


def test_target_not_under_the_given_managed_root_is_rejected(tree):
    # stepper 는 target 을 이 root 로 조합한다 -- 접두가 다르면(변조·조합 규칙 회귀) 막는다.
    rc, out = _preflight(str(tree / "m/users/bob/old"), str(tree / "m/users/alice"))
    assert rc == 1 and _REDIRECTED in out
    assert rm_target_redirected(str(tree / "m/users/bob/old"), str(tree / "m/users/alice")) is True


def test_paths_are_positional_never_inlined(tree):
    # 셸 인젝션 차단 관례: 대상·managed_root 모두 positional. 따옴표·$(…) 가 든 이름도 명령이 되지 않는다.
    evil = tree / "m" / 'a"$(touch PWNED)`touch PWNED2`'
    (evil / "sub").mkdir(parents=True)
    root = str(tree / "m")
    target = f"{evil}/sub"
    cmd = _cmd(_spec(target, root))
    assert target not in cmd[2] and root not in cmd[2]
    assert cmd[4:] == [target, root]
    rc, out = _preflight(target, root)
    assert (rc, "DMS_PREFLIGHT_OK" in out) == (0, True), out
    assert not (tree / "PWNED").exists() and not os.path.exists("PWNED") and not os.path.exists("PWNED2")


@pytest.mark.parametrize("phase", ["preflight", "exec_preflight"])
@pytest.mark.parametrize("privileged", [False, True])
def test_containment_runs_for_root_and_non_root_in_both_preflight_phases(phase, privileged):
    # root 는 `test -w` 가 늘 참이라 봉쇄가 유일한 검사다 -- 신원·단계와 무관하게 같은 스크립트·같은 인자여야 한다.
    # 단건 rm 과 배치 rm 자식은 같은 stepper._build_spec → JobSpec 이라 여기서 갈리지 않는다(test_stepper_enrich 가 paths 를 본다).
    spec = _spec("/c/m/a/b", "/c/m", phase=phase, privileged=privileged)
    script, args = _preflight_script(spec)
    base, _ = _preflight_script(_spec("/c/m/a/b", "/c/m"))
    assert script == base and "target_path_redirected" in script
    assert args == ["/c/m/a/b", "/c/m"]
    assert _cmd(spec)[2].endswith(script)


def test_rm_without_managed_root_is_refused_by_the_builders():
    # 봉쇄 기준 없이 root drm 을 내보내지 않는다 -- 빌더 ValueError → 어댑터 submit_failed(fail-closed).
    spec = JobSpec(job_id="j1", phase="preflight", operation="rm", tool="drm", dryrun=False,
                   identity={"uid": 0, "gid": 0, "username": "root", "privileged": True},
                   paths={"target": "/c/m/a", "storage": "s1"}, options={"recursive": True},
                   candidates={"primary": ["n1"]}, process_count=1, queue="q", priority_class="p",
                   artifact_base="file:///b")
    with pytest.raises(ValueError):
        _preflight_script(spec)
    with pytest.raises(ValueError):
        _launcher_env(spec)


def test_drm_launcher_env_carries_the_root_and_the_target_is_the_last_argv():
    # 러너 계약: 대상 = DMS_JR_ARGV 마지막(tool_argv), 기준 = DMS_JR_RM_ROOT. 둘 다 stepper 가 조합한 같은 값.
    for dryrun in (True, False):
        spec = _spec("/c/m/a/b", "/c/m", phase="preview" if dryrun else "execution", dryrun=dryrun)
        m = build_volcano_job(spec, job_image="i", namespace="dms", volumes=[])
        launcher = next(t for t in m["spec"]["tasks"] if t["name"] == "launcher")
        env = {e["name"]: e["value"] for e in launcher["template"]["spec"]["containers"][0]["env"]}
        assert env["DMS_JR_RM_ROOT"] == "/c/m"
        assert json.loads(env["DMS_JR_ARGV"])[-1] == "/c/m/a/b" == tool_argv(spec, abs_paths=spec.paths)[-1]
    scan = JobSpec(job_id="j1", phase="execution", operation="scan", tool="dscan", dryrun=False,
                   identity={"uid": 1, "gid": 1, "username": "a"}, paths={"target": "/c/m/a"}, options={},
                   candidates={"primary": ["n1"]}, process_count=1, queue="q", priority_class="p",
                   artifact_base="file:///b")
    assert "DMS_JR_RM_ROOT" not in _launcher_env(scan)          # drm 밖은 매니페스트가 그대로다


def test_reason_is_registered_for_both_preflight_and_runner_markers():
    assert "target_path_redirected" in PREFLIGHT_REASONS
    assert runner.TARGET_PATH_REDIRECTED in EXECUTION_REASONS


# ---- 러너: drm 직전 재확인 ----

class _Rec:
    def __init__(self):
        self.writes, self.ran = {}, []

    def write_text(self, path, content, *, append=False):
        self.writes[path] = content

    def run(self, command):
        self.ran.append(command)

        class R:
            returncode = 0
            stdout = "10.0.0.1 w\n" if command[0] == "getent" else "Removed 1 items\n"
            stderr = ""
        return R()


def _drm_env(target, artifact_dir, **kw):
    env = {"DMS_JR_TOOL": "drm", "DMS_JR_OPERATION": "rm", "DMS_JR_PHASE": "execution", "DMS_JR_DRYRUN": "0",
           "DMS_JR_PROCESS_COUNT": "1", "DMS_JR_PROCESSES_PER_NODE": "1", "DMS_JR_UID": "0", "DMS_JR_GID": "0",
           "DMS_JR_USERNAME": "root", "DMS_JR_ARTIFACT_DIR": artifact_dir, "DMS_JR_ARGV": json.dumps([target])}
    env.update(kw)
    return env


def _run_drm(env):
    rec = _Rec()
    rc = run_job(env, run=rec.run, write_text=rec.write_text, read_text=lambda p: "", sleep=lambda s: None,
                 wait_hostfile=lambda: (["w0"], "/tmp/hostfile"))
    return rc, rec


def test_runner_refuses_drm_when_the_parent_was_swapped_for_a_symlink(tree, capsys):
    root = str(tree / "m")
    rc, rec = _run_drm(_drm_env(f"{root}/users/alice/redirect/old", str(tree / "art"), DMS_JR_RM_ROOT=root))
    assert rc == 1
    assert not any("mpirun" in cmd for cmd in rec.ran)                  # drm 을 돌리지 않았다
    out = capsys.readouterr().out
    assert "DMS_EXEC_REASON=target_path_redirected" in out              # stepper 가 사유로 승격하는 마커
    assert json.loads(rec.writes[f"{tree / 'art'}/summary.json"]) == {"returncode": None, "files": None, "bytes": None}


def test_runner_runs_drm_for_a_contained_target(tree):
    root = str(tree / "m")
    rc, rec = _run_drm(_drm_env(f"{root}/users/alice/data/old", str(tree / "art"), DMS_JR_RM_ROOT=root))
    assert rc == 0 and any("mpirun" in cmd for cmd in rec.ran)


def test_runner_without_the_env_key_keeps_the_old_behaviour(tree):
    # 이 검사 전의 제어면(롤링 업그레이드)이 만든 매니페스트 -- 그 제어면의 preflight 도 보지 않았다. 빈 값은 키가 있으니 막는다.
    target = f"{tree / 'm'}/users/alice/redirect/old"
    rc, rec = _run_drm(_drm_env(target, str(tree / "art")))
    assert rc == 0 and any("mpirun" in cmd for cmd in rec.ran)
    rc, rec = _run_drm(_drm_env(target, str(tree / "art"), DMS_JR_RM_ROOT=""))
    assert rc == 1 and not any("mpirun" in cmd for cmd in rec.ran)
