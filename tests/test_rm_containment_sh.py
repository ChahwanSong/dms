"""rm 대상 봉쇄(2026-10-21 리뷰 [0])를 **실제 sh**(이 호스트 /bin/sh = dash)로 돌린다.

배치 rm 은 root 라 preflight·exec_preflight 의 `test -w "$(dirname target)"` 가 늘 참이었다 -- 사용자가 상위 디렉터리를
심링크로 바꿔 두면(users/alice/data -> ../bob) 확인 창의 「실제 경로」와 다른 트리를 root drm 이 지웠다. 봉쇄 조각
(execution_manifests._RM_CONTAINMENT_CHECK + _PATH_CONTAINED_FN)은 `set -e` 아래 case·명령 치환·AND-OR 의 상호작용이라
문자열 계약으로는 증명할 수 없다 -- 실 파드와 같은 명령(["sh", "-c", script, "sh", *args])을 임시 트리에 대고 돌린다. 같은
트리로 러너의 drm 직전 재확인(dms_job_runner.runner.rm_target_redirected)도 돌려 두 판정이 갈라지지 않음을 고정한다.
2026-10-28(사용자 결정 -- 10-27 의 「대상 자신이 링크여도 거부」를 뒤집음): 대상 **자신**이 심링크면 drm 이 링크만 지운다(포크
7a3189c drm 은 대상을 lstat). 그래서 봉쇄는 부모에 걸고, 순서(대상 문자열 검사 → managed_root → 부모 봉쇄)가 계약이다 -- 문자열
검사가 빠지면 변조 행 「…/x/..」 를 drm 이 접어 managed_root 를 지운다. 부모는 `${1%/*}`(셸)·os.path.dirname(Python) -- `$(dirname)`
은 끝 개행을 지워 「x\n」 링크 성분을 실제 디렉터리 「x」로 봤다.
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
    (m / "users/alice/lastlink").symlink_to("../bob/old")                     # 마지막 성분이 링크
    (m / "users/alice/dangling").symlink_to("../nowhere")                     # 끊긴 링크
    (m / "users/alice/filelink").symlink_to("../bob/old/keep")                # 파일을 가리키는 링크
    (m / "users/alice/rootlink").symlink_to(str(m))                           # managed_root 를 가리키는 링크
    (m / "users/alice/nl").mkdir()                                             # 실제 디렉터리 「nl」 옆에
    (m / "users/alice/nl\n").symlink_to("../bob")                            # 이름이 개행으로 끝나는 링크(-> 남의 디렉터리)
    (m / "toplink").symlink_to("users/bob")                                   # managed_root 바로 아래 링크
    (m / "shared/b\n").mkdir()                                                # 이름이 개행으로 끝나는 **실제** 디렉터리
    (m / "shared/b\n/old").mkdir()
    (m / "shared/b").symlink_to("b\n")                                        # 개행 없는 링크 -> 그 이웃(realpath 끝 개행 함정)
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
    ("m", "dir with space/x*[y]/old"),         # 공백·글롭 문자 -- case 패턴의 "..." 는 글자 그대로
    # 대상 자신이 링크 -- drm 이 링크만 지운다(2026-10-28). 무엇을 가리키든(남의 디렉터리·파일·끊김·managed_root·밖) 같다.
    ("m", "users/alice/lastlink"),
    ("m", "users/alice/dangling"),
    ("m", "users/alice/redirect"),
    ("m", "users/alice/filelink"),
    ("m", "users/alice/rootlink"),
    ("m", "users/alice/outside"),
    ("m", "toplink"),                          # 부모 == managed_root
    ("mlink", "users/alice/lastlink"),
    ("m", "shared/b\n/old"),                   # 개행으로 끝나는 실제 디렉터리 아래 -- 셸도 Python 도 통과(이름의 개행을 지우지 않는다)
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
    ("m", "users/alice/lastlink/sub"),         # 링크 아래 없는 경로 -- 가장 깊은 「있는」 성분이 링크(중간 성분)
    ("m", "users/alice/lastlink/"),            # 끝 슬래시 -- 셸 dirname·os.path.dirname 이 갈리는 모양, 문자열 검사가 먼저 막는다
    ("m", "users/alice/data/old/.."),          # drm 이 「..」를 접어 상위(data)를 지운다
    ("m", "toplink/.."),                       # drm 이 managed_root 자신으로 접는다
    ("m", "users/alice/nl\n/old"),            # 개행으로 끝나는 링크 성분 -- $(dirname) 이면 「nl」로 보여 통과했다
    ("m", "users/alice/nl\n/new"),            # 같은 링크 아래 아직 없는 경로
    ("m", "shared/b/old"),                     # 부모 「b」 가 이웃 「b\n」 링크 -- $(realpath) 가 끝 개행을 지우면 통과했다(d194 리뷰)
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
    # 부모 쓰기가 봉쇄보다 먼저다 -- 오타 경로를 「다른 곳을 가리킨다」로 말하지 않는다. 러너는 봉쇄만 본다: 없는 경로는
    # 가장 깊은 「있는」 상위(users)를 보고 통과 -- 지울 것이 없으면 drm 이 스스로 실패한다(preflight 가 먼저 막는다).
    root = str(tree / "m")
    rc, out = _preflight(f"{root}/users/nobody/x", root)
    assert rc == 1 and "DMS_PREFLIGHT_REASON=parent_not_writable" in out and _REDIRECTED not in out
    assert rm_target_redirected(f"{root}/users/nobody/x", root) is False


def test_the_managed_root_itself_is_never_a_target(tree):
    # stepper 는 managed_root **아래**만 조합한다 -- 루트 자체(끝 슬래시 표기 포함)는 변조 행이다.
    root = str(tree / "m")
    for target in (root, root + "/", root + "/.", root + "//"):
        rc, out = _preflight(target, root)
        assert rc == 1 and _REDIRECTED in out, out
        assert rm_target_redirected(target, root) is True


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
            # drm 요약은 포크 7a3189c 의 실제 모양(MFU 접두사 + "Removed N items in") -- 러너 파서가 줄 머리에 맞춘다(d192).
            stdout = ("10.0.0.1 w\n" if command[0] == "getent"
                      else "[2026-10-27T13:52:58] Removed 1 items in 0.001 seconds (1000.000 items/sec)\n")
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
    # 통과한 경로는 정상 경로 summary(v2 -- 핵심 3키 + 상세)다. 거부 경로(위)는 3키 그대로다.
    summary = json.loads(rec.writes[f"{tree / 'art'}/summary.json"])
    assert (summary["files"], summary["removed"], summary["detail_version"]) == (1, 1, 2)


def test_runner_runs_drm_for_a_terminal_link_and_reports_its_kind(tree):
    # 대상 자신이 링크면 drm 이 링크만 지운다(2026-10-28) -- 러너는 막지 않고, lstat 한 종류를 summary 에 싣는다(포탈이
    # 「링크만 지움」을 말한다). 가리키는 곳(bob)은 러너가 건드리지 않는다.
    root = str(tree / "m")
    rc, rec = _run_drm(_drm_env(f"{root}/users/alice/lastlink", str(tree / "art"), DMS_JR_RM_ROOT=root))
    assert rc == 0 and any("mpirun" in cmd for cmd in rec.ran)
    summary = json.loads(rec.writes[f"{tree / 'art'}/summary.json"])
    assert summary["target_kind"] == "link"
    assert (tree / "m/users/bob/old/keep").read_text() == "bob"
    rc, rec = _run_drm(_drm_env(f"{root}/users/alice/data", str(tree / "art"), DMS_JR_RM_ROOT=root))
    assert json.loads(rec.writes[f"{tree / 'art'}/summary.json"])["target_kind"] == "dir"


@pytest.mark.parametrize("target_rel,expect,runs", [
    ("users/alice/lastlink", "link", True),     # 미리보기와 같은 종류 -- 돈다
    ("users/alice/data", "dir", True),
    ("users/alice/data", "link", False),        # 링크로 확인했는데 실행 때 디렉터리 -- 트리를 지우지 않는다
    ("users/alice/lastlink", "dir", False),     # 디렉터리로 확인했는데 링크가 됐다 -- 「1개 성공」으로 조용히 끝나지 않는다
    ("users/alice/gone", "dir", False),         # 대상이 없어졌다
    ("users/alice/gone", "missing", True),      # 미리보기 때도 없었고 지금도 없다 -- 돈다(drm 이 지울 것이 없다)
    ("users/alice/data", "missing", False),     # 「지울 것이 없음」으로 확인했는데 그 자리에 트리가 생겼다 -- 지우지 않는다
    ("users/alice/data", "bogus", True),        # 범위 밖 기대값(변조 env)은 묶지 않는다(제어면도 싣지 않는다)
])
def test_runner_binds_the_execution_to_the_previewed_kind(tree, capsys, target_rel, expect, runs):
    root = str(tree / "m")
    rc, rec = _run_drm(_drm_env(f"{root}/{target_rel}", str(tree / "art"), DMS_JR_RM_ROOT=root,
                                DMS_JR_RM_EXPECT_KIND=expect))
    ran = any("mpirun" in cmd for cmd in rec.ran)
    assert ran is runs
    out = capsys.readouterr().out
    if not runs:
        assert rc == 1 and "DMS_EXEC_REASON=target_changed_since_preview" in out
        assert f"expected={expect}" in out
        assert json.loads(rec.writes[f"{tree / 'art'}/summary.json"]) == {"returncode": None, "files": None,
                                                                          "bytes": None}
    else:
        assert "target_changed_since_preview" not in out


def test_launcher_env_carries_the_previewed_kind_only_when_known():
    def env(paths_extra, phase="execution"):
        spec = _spec("/c/m/a/b", "/c/m", phase=phase, dryrun=(phase == "preview"))
        spec.paths.update(paths_extra)
        return _launcher_env(spec)
    assert env({"expect_target_kind": "link"})["DMS_JR_RM_EXPECT_KIND"] == "link"
    assert env({"expect_target_kind": "missing"})["DMS_JR_RM_EXPECT_KIND"] == "missing"
    assert "DMS_JR_RM_EXPECT_KIND" not in env({})                           # 옛 미리보기·대상 없음 -- 묶지 않는다
    assert "DMS_JR_RM_EXPECT_KIND" not in env({"expect_target_kind": "rm -rf"})   # 범위 밖(변조 행)


def test_kind_values_and_reason_match_the_control_plane():
    from dms.execution_manifests import RM_TARGET_KINDS
    assert set(runner.RM_TARGET_KINDS) == RM_TARGET_KINDS == {"link", "dir", "file", "other", "missing"}
    assert runner.TARGET_CHANGED_SINCE_PREVIEW == "target_changed_since_preview" and \
        runner.TARGET_CHANGED_SINCE_PREVIEW in EXECUTION_REASONS


def test_target_kind_never_follows_the_link(tree):
    m = tree / "m"
    assert runner.target_kind(str(m / "users/alice/lastlink")) == "link"     # 디렉터리를 가리켜도 link
    assert runner.target_kind(str(m / "users/alice/filelink")) == "link"
    assert runner.target_kind(str(m / "users/alice/dangling")) == "link"
    assert runner.target_kind(str(m / "users/bob/old/keep")) == "file"
    assert runner.target_kind(str(m / "users/alice/data")) == "dir"
    assert runner.target_kind(str(m / "users/alice/none")) == "missing"
    assert runner.target_kind(str(m / "users/bob/old/keep/x")) == "missing"    # 상위가 파일(ENOTDIR)
    assert runner.target_kind(str(m / "users/alice/data"), lstat=lambda p: (_ for _ in ()).throw(PermissionError(13, "x"))) is None


def test_preview_summary_carries_the_target_kind(tree):
    # 미리보기(dry-run) summary 의 target_kind 가 묶기의 유일한 재료다(stepper 가 실행 spec 으로 넘긴다) -- 미리보기 argv 로 고정한다.
    root = str(tree / "m")
    for rel, want in (("users/alice/lastlink", "link"), ("users/alice/data", "dir"), ("users/alice/gone", "missing")):
        target = f"{root}/{rel}"
        rc, rec = _run_drm(_drm_env(target, str(tree / "art"), DMS_JR_RM_ROOT=root, DMS_JR_PHASE="preview",
                                    DMS_JR_DRYRUN="1", DMS_JR_ARGV=json.dumps(["--dryrun", target])))
        summary = json.loads(rec.writes[f"{tree / 'art'}/summary.json"])
        assert summary["dryrun"] is True and summary["target_kind"] == want, (rel, summary)


def test_target_kind_survives_a_failed_detail_parse(tree, monkeypatch):
    # 상세 파싱이 실패해 핵심 3키만 남는 fail-soft 경로에도 종류는 남는다 -- 묶기가 도구 출력 파싱에 기대지 않는다.
    monkeypatch.setattr(runner, "parse_tool_detail", lambda *a, **k: None)
    s = runner._build_summary("drm", "", 0, "/x", dryrun=True, rm_kind="link")
    assert s["target_kind"] == "link" and "detail_version" not in s
    assert "target_kind" not in runner._build_summary("dsync", "", 0, "/x", rm_kind="link")


def test_runner_without_the_env_key_keeps_the_old_behaviour(tree):
    # 이 검사 전의 제어면(롤링 업그레이드)이 만든 매니페스트 -- 그 제어면의 preflight 도 보지 않았다. 빈 값은 키가 있으니 막는다.
    target = f"{tree / 'm'}/users/alice/redirect/old"
    rc, rec = _run_drm(_drm_env(target, str(tree / "art")))
    assert rc == 0 and any("mpirun" in cmd for cmd in rec.ran)
    rc, rec = _run_drm(_drm_env(target, str(tree / "art"), DMS_JR_RM_ROOT=""))
    assert rc == 1 and not any("mpirun" in cmd for cmd in rec.ran)
