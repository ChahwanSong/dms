"""PV 주소 봉쇄(2026-10-26 보안 리뷰 — dms-k8s 「모든 잡 root + namespace 단위 권한」)를 **실제 sh** 로 돌린다.

모든 잡이 root 라 커널 DAC 가 없다. 사용자 파드가 자기 PV 안에 남의 PV 를 가리키는 심링크를 심으면 PV A 권한만으로 root
dsync·dscan 이 PV B 를 읽거나 덮어쓸 수 있었다. 봉쇄 조각(execution_manifests._PV_CONTAIN_FN)은 `set -e` 아래 함수·case·
명령 치환의 상호작용이라 문자열 계약으로는 증명할 수 없다 — 실 파드와 같은 명령(["sh", "-c", script, "sh", *args])을 임시
트리에 대고 돌리고, 같은 트리로 러너의 재확인(dms_job_runner.contain.pv_path_redirected)도 돌려 두 판정이 갈라지지 않음을 고정한다.
기준점은 PV 루트가 아니라 managed_root 다(2차 보안 리뷰 — PV 루트 자신·managed_root 와 PV 루트 사이가 링크인 경우).
nsync 는 rank 가 자기 역할 쪽을 본다(rank.sh → python3 -m dms_job_runner.contain). PV 가 아닌 잡은 스크립트·인자·러너 env·
rank.sh 가 이 기능 이전과 같다(맨 아래).
"""
import json
import os
import subprocess
import sys

import pytest

from dms.execution import JobSpec
from dms.execution_manifests import _launcher_env, _preflight_script, build_preflight_pod
from dms_job_runner import contain
from dms_job_runner.runner import pv_path_redirected, run_job

_REDIRECTED = "DMS_PREFLIGHT_REASON=target_path_redirected"
_EXEC_REDIRECTED = "DMS_EXEC_REASON=target_path_redirected"
_ROOT_ID = {"uid": 0, "gid": 0, "username": "alice", "privileged": True}
_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")


def _spec(op, paths, *, tool=None):
    return JobSpec(job_id="j1", phase="preflight", operation=op, tool=tool or {"sync": "dsync", "scan": "dscan",
                   "rm": "drm"}[op], dryrun=False, identity=_ROOT_ID, paths=paths,
                   options={"recursive": True} if op == "rm" else {}, candidates={"primary": ["n1"]},
                   process_count=1, queue="dms-data", priority_class="dms-mid",
                   artifact_base="file:///cephfs/dms/artifacts")


def _run(spec, role=None):
    script, args = _preflight_script(spec, role=role)
    proc = subprocess.run(["sh", "-c", script, "sh", *args], capture_output=True, text=True)
    return proc.returncode, proc.stdout


@pytest.fixture
def tree(tmp_path):
    """tmp/m = managed_root. PV A = m/pvc-a/pvc-a-data, PV B = m/pvc-b/pvc-b-data(남의 namespace), 그리고 심링크들."""
    m = tmp_path / "m"
    a = m / "pvc-a/pvc-a-data"
    b = m / "pvc-b/pvc-b-data"
    for d in (a / "data/x", a / "out", b / "secret", a / "dir with space/x*[y]", m / "pvc-c"):
        d.mkdir(parents=True)
    (b / "secret/keep").write_text("bob")
    (a / "data/x/f").write_text("alice")
    (a / "to-b").symlink_to("../../pvc-b/pvc-b-data")              # 상대 링크 -> 남의 PV
    (a / "abs-b").symlink_to(str(b))                               # 절대 링크 -> 남의 PV
    (a / "lastlink").symlink_to("data")                            # 마지막 성분이 링크(PV 안을 가리켜도 막는다)
    (a / "dangling").symlink_to("nowhere")                         # 끊긴 링크
    (a / "inner").mkdir()
    (a / "inner/link-out").symlink_to(str(b / "secret"))           # 트리 **안쪽** 링크 -- 도구가 따라가지 않는다(통과)
    (m / "pvc-c/pvc-c-data").symlink_to("../pvc-b/pvc-b-data")     # PV 루트 **자신**이 남의 PV 로 가는 링크(2차 리뷰)
    (m / "pvc-d").symlink_to("pvc-b")                              # managed_root 와 PV 루트 **사이** 성분이 링크
    (tmp_path / "mlink").symlink_to(str(m))                        # managed_root 자신이 링크 -- 괜찮다
    return tmp_path


A = "m/pvc-a/pvc-a-data"
M = "m"

# (기준 managed_root, PV 루트, PV 루트 기준 상대경로) -> 통과 여부. 경로는 존재해도 없어도 된다(없으면 부모로 판정).
_PASS = [
    (M, A, "data/x"),                   # 정상(있음)
    (M, A, ""),                         # PV 루트 자체(sync 소스·목적지·scan 허용)
    (M, A, "new-dest"),                 # 없는 목적지 -- 부모(=루트)로 판정
    (M, A, "out/new"),                  # 없는 목적지 -- 부모 out
    (M, A, "inner"),                    # 안쪽에 링크가 있는 디렉터리(그 링크는 도구가 따라가지 않는다)
    ("mlink", "mlink/pvc-a/pvc-a-data", "data/x"),   # managed_root 자신이 링크 -- 양쪽을 같은 realpath 로 본다
    (M, A, "dir with space/x*[y]"),     # 공백·글롭 문자
]
_REDIRECT = [
    (M, A, "to-b/secret"),              # 상위가 상대 링크(-> 남의 PV)
    (M, A, "abs-b/secret"),             # 상위가 절대 링크
    (M, A, "to-b"),                     # 마지막 성분이 남의 PV 를 가리키는 링크
    (M, A, "lastlink"),                 # 마지막 성분 링크(PV 안을 가리켜도) -- 도구가 따라가므로 막는다
    (M, A, "dangling"),                 # 끊긴 링크
    (M, A, "to-b/new-dest"),            # 없는 목적지의 부모가 링크
    (M, A, "data/x/../../../pvc-b"),    # 변조 행(「..」) -- 실제 위치와 글자가 맞을 수 없다
    (M, A, "data/x/"),                  # 끝 슬래시
    (M, A, "data/."),
    (M, "m/pvc-c/pvc-c-data", "secret"),   # PV 루트 자신이 링크 -- PV 루트를 기준점으로 쓰면 통과했다(2차 리뷰)
    (M, "m/pvc-c/pvc-c-data", ""),
    (M, "m/pvc-d/pvc-b-data", "secret"),   # managed_root 와 PV 루트 사이 성분이 링크
    ("mlink", A, "data/x"),             # 경로가 기준(managed_root) 밖 -- 글자로 막는다
]


def _case(tree, anchor_rel, root_rel, rel):
    root = str(tree / root_rel)
    path = root if rel == "" else f"{root}/{rel}"
    return path, root, str(tree / anchor_rel)


def _scan(path, root, anchor):
    return _spec("scan", {"target": path, "storage": "s", "managed_root": anchor,
                          "contain": {"target": {"root": root, "anchor": anchor}}})


@pytest.mark.parametrize("anchor_rel,root_rel,rel", _PASS)
def test_scan_and_runner_pass(tree, anchor_rel, root_rel, rel):
    path, root, anchor = _case(tree, anchor_rel, root_rel, rel)
    if rel in ("new-dest", "out/new"):
        pytest.skip("scan 대상은 있어야 한다 -- 없는 경로는 아래 sync 목적지에서 본다")
    rc, out = _run(_scan(path, root, anchor))
    assert rc == 0 and out.strip().endswith("DMS_PREFLIGHT_OK"), out
    assert pv_path_redirected(path, root, anchor) is False


@pytest.mark.parametrize("anchor_rel,root_rel,rel", _REDIRECT)
def test_scan_and_runner_redirect(tree, anchor_rel, root_rel, rel):
    path, root, anchor = _case(tree, anchor_rel, root_rel, rel)
    rc, out = _run(_scan(path, root, anchor))
    # 없는 대상은 target_not_readable 이 먼저다(기존 사유 우선) -- 그 경우도 실행은 막힌다.
    assert rc != 0 and (_REDIRECTED in out or "target_not_readable" in out), out
    assert pv_path_redirected(path, root, anchor) is True


@pytest.mark.parametrize("anchor_rel,root_rel,rel", _PASS + _REDIRECT)
def test_shell_and_runner_agree_for_sync_destination(tree, anchor_rel, root_rel, rel):
    """목적지(없어도 되는 쪽)로 같은 트리를 셸·러너가 같게 판정하는가 — 소스는 PV A 의 멀쩡한 디렉터리."""
    dest, root, anchor = _case(tree, anchor_rel, root_rel, rel)
    src, src_root, src_anchor = _case(tree, M, A, "data/x")
    rc, out = _run(_spec("sync", {"source": src, "source_storage": "s", "destination": dest,
                                  "destination_storage": "s",
                                  "contain": {"source": {"root": src_root, "anchor": src_anchor},
                                              "destination": {"root": root, "anchor": anchor}}}))
    shell_redirected = _REDIRECTED in out
    if rc != 0 and not shell_redirected:
        pytest.skip(f"기존 목적지 검사가 먼저 막았다: {out.strip()}")
    assert shell_redirected is pv_path_redirected(dest, root, anchor), out


def _sync_spec(tree, src_rel, dest_rel, *, tool="dsync"):
    src, root, anchor = _case(tree, M, A, src_rel)
    dest, _, _ = _case(tree, M, A, dest_rel)
    c = {"root": root, "anchor": anchor}
    return _spec("sync", {"source": src, "source_storage": "s", "destination": dest, "destination_storage": "s",
                          "contain": {"source": c, "destination": c}}, tool=tool)


def test_sync_source_link_to_another_pv_is_blocked(tree):
    rc, out = _run(_sync_spec(tree, "to-b/secret", "out/copy"))
    assert rc != 0 and _REDIRECTED in out


def test_nsync_role_preflight_checks_only_its_side(tree):
    spec = _sync_spec(tree, "to-b/secret", "out/copy", tool="nsync")
    assert _REDIRECTED in _run(spec, role="source")[1]
    rc, out = _run(spec, role="destination")
    assert rc == 0 and out.strip().endswith("DMS_PREFLIGHT_OK"), out


def test_empty_or_bad_values_block(tree):
    path, root, anchor = _case(tree, M, A, "data/x")
    for c in ({"root": "", "anchor": anchor}, {"root": None, "anchor": anchor}, {"root": root, "anchor": ""},
              {"root": root}, "not-a-dict"):
        rc, out = _run(_spec("scan", {"target": path, "storage": "s", "managed_root": anchor,
                                      "contain": {"target": c}}))
        assert rc != 0 and _REDIRECTED in out, c
    assert pv_path_redirected(path, "", anchor) is True
    assert pv_path_redirected(path, root, "") is True
    assert pv_path_redirected(path, None, anchor) is True


def test_path_outside_its_pv_root_is_blocked(tree):
    """managed_root 안이라도 고른 PV 루트 밖이면 막는다(글자 경계 — 변조 행)."""
    path, _, anchor = _case(tree, M, "m/pvc-b/pvc-b-data", "secret")
    root = str(tree / A)
    rc, out = _run(_scan(path, root, anchor))
    assert rc != 0 and _REDIRECTED in out
    assert pv_path_redirected(path, root, anchor) is True


def _rm(target, root, anchor):
    return _spec("rm", {"target": target, "storage": "s", "managed_root": anchor,
                        "contain": {"target": {"root": root, "anchor": anchor}}})


def test_pv_rm_keeps_the_managed_root_anchor_and_adds_the_pv_bound(tree):
    """PV 주소 rm: 심링크 검사는 기존 rm 봉쇄(기준 managed_root)가, PV 경계는 글자 검사(dms_within)가 맡는다."""
    anchor, root = str(tree / M), str(tree / A)
    # PV 안 링크를 거쳐 남의 PV 를 지우려 함 -> rm 봉쇄
    rc, out = _run(_rm(f"{root}/to-b/secret", root, anchor))
    assert rc != 0 and _REDIRECTED in out
    # PV 루트 자신이 링크(2차 리뷰) -> rm 봉쇄(기준이 managed_root 라 잡힌다)
    rc, out = _run(_rm(str(tree / "m/pvc-c/pvc-c-data/secret"), str(tree / "m/pvc-c/pvc-c-data"), anchor))
    assert rc != 0 and _REDIRECTED in out
    # managed_root 안이지만 PV 루트 밖 -> 글자 경계
    rc, out = _run(_rm(str(tree / "m/pvc-b/pvc-b-data/secret"), root, anchor))
    assert rc != 0 and _REDIRECTED in out
    # 빈 PV 루트 -> 막음
    rc, out = _run(_rm(f"{root}/data/x", "", anchor))
    assert rc != 0 and _REDIRECTED in out
    # 정상 대상·마지막 성분이 링크인 대상(drm 은 링크만 지운다) -> 통과
    for target in (f"{root}/data/x", f"{root}/lastlink"):
        rc, out = _run(_rm(target, root, anchor))
        assert rc == 0 and out.strip().endswith("DMS_PREFLIGHT_OK"), (target, out)
    assert _launcher_env(_rm(f"{root}/data/x", root, anchor))["DMS_JR_RM_ROOT"] == anchor


class _Rec:
    def __init__(self, mpirun_stdout="ok\n", mpirun_rc=None):
        self.writes, self.ran = {}, []
        self.mpirun_stdout = mpirun_stdout
        self.mpirun_rc = mpirun_rc

    def write_text(self, path, content, *, append=False):
        self.writes[path] = content

    def run(self, command):
        self.ran.append(command)
        out = "10.0.0.1 w\n" if command[0] == "getent" else self.mpirun_stdout if "mpirun" in command else "ok\n"

        rc = self.mpirun_rc if "mpirun" in command and self.mpirun_rc is not None else (
            1 if "mpirun" in command and _EXEC_REDIRECTED in out else 0)

        class R:
            returncode = rc
            stdout = out
            stderr = ""
        return R()


def _env(tool, argv, art, **kw):
    env = {"DMS_JR_TOOL": tool, "DMS_JR_OPERATION": "scan" if tool == "dscan" else "sync", "DMS_JR_PHASE": "execution",
           "DMS_JR_DRYRUN": "0", "DMS_JR_PROCESS_COUNT": "1", "DMS_JR_PROCESSES_PER_NODE": "1", "DMS_JR_UID": "0",
           "DMS_JR_GID": "0", "DMS_JR_USERNAME": "alice", "DMS_JR_ARTIFACT_DIR": art, "DMS_JR_ARGV": json.dumps(argv)}
    env.update(kw)
    return env


def _run_tool(env, rec=None):
    rec = rec or _Rec()
    rc = run_job(env, run=rec.run, write_text=rec.write_text, read_text=lambda p: "", sleep=lambda s: None,
                 wait_hostfile=lambda role=None: (["w0"], "/tmp/hostfile"))
    return rc, rec


def _entry(side, path, root, anchor):
    return [side, path, root, anchor]


def test_runner_rechecks_before_mpirun(tree, capsys):
    src, root, anchor = _case(tree, M, A, "to-b/secret")
    rc, rec = _run_tool(_env("dscan", [src], str(tree / "art"),
                             DMS_JR_CONTAIN=json.dumps([_entry("target", src, root, anchor)])))
    assert rc == 1 and not any("mpirun" in cmd for cmd in rec.ran)
    assert _EXEC_REDIRECTED in capsys.readouterr().out
    assert json.loads(rec.writes[f"{tree / 'art'}/summary.json"]) == {"returncode": None, "files": None, "bytes": None}
    # 봉쇄 안 경로는 그대로 돈다, 키가 JSON 이 아니거나 모양이 틀리면(옛 2칸 모양 포함) 막는다, 키가 없으면 종전대로.
    ok, _, _ = _case(tree, M, A, "data/x")
    rc, rec = _run_tool(_env("dsync", [ok, ok + "-copy"], str(tree / "art"), DMS_JR_CONTAIN=json.dumps(
        [_entry("source", ok, root, anchor), _entry("destination", ok + "-copy", root, anchor)])))
    assert rc == 0 and any("mpirun" in cmd for cmd in rec.ran)
    for bad in ("not-json", json.dumps([[ok, root]]), json.dumps({"target": ok})):
        rc, rec = _run_tool(_env("dscan", [ok], str(tree / "art"), DMS_JR_CONTAIN=bad))
        assert rc == 1 and not any("mpirun" in cmd for cmd in rec.ran), bad
    rc, rec = _run_tool(_env("dscan", [src], str(tree / "art")))
    assert rc == 0 and any("mpirun" in cmd for cmd in rec.ran)


def _nsync_env(tree, entries, **kw):
    return _env("nsync", ["/s", "/d"], str(tree / "art"), DMS_JR_PROCESSES_PER_NODE="2", DMS_JR_PROCESS_COUNT="6",
                DMS_JR_SOURCE_NODES=json.dumps(["s1", "s2"]), DMS_JR_DEST_NODES=json.dumps(["d1"]),
                **({} if entries is None else {"DMS_JR_CONTAIN": json.dumps(entries)}), **kw)


def _rank_sh(rec, tree):
    return rec.writes[f"{tree / 'art'}/rank.sh"]


def test_nsync_rank_script_checks_its_side_before_exec(tree):
    """nsync 런처는 데이터 경로를 볼 수 없다 — rank.sh 가 도구 exec 직전에 자기 역할 쪽을 본다(소스 rank 수 = 소스 노드 2 ×
    slot 2 = 4). 실 sh 로 rank.sh 를 돌린다: 소스 rank 는 막히고(마커), 목적지 rank 는 검사를 지나 도구 exec 로 간다."""
    bad, root, anchor = _case(tree, M, A, "to-b/secret")
    good, _, _ = _case(tree, M, A, "out/copy")
    entries = [_entry("source", bad, root, anchor), _entry("destination", good, root, anchor)]
    rc, rec = _run_tool(_nsync_env(tree, entries))
    script = _rank_sh(rec, tree)
    assert script.startswith("#!/bin/sh\npython3 -m dms_job_runner.contain 4 '")
    assert "|| exit 1\nexec nsync --role-mode map" in script
    env = {**os.environ, "PYTHONPATH": _SRC}
    for rank, blocked in (("0", True), ("3", True), ("4", False), ("5", False)):
        proc = subprocess.run(["sh", "-c", script.replace("python3 ", f"{sys.executable} ", 1)],
                              capture_output=True, text=True, env={**env, "OMPI_COMM_WORLD_RANK": rank})
        if blocked:
            assert proc.returncode == 1 and _EXEC_REDIRECTED in proc.stdout, (rank, proc.stdout, proc.stderr)
        else:   # 검사를 지나 exec nsync -- 이 호스트엔 nsync 가 없어 127
            assert proc.returncode == 127 and _EXEC_REDIRECTED not in proc.stdout, (rank, proc.stdout, proc.stderr)


def test_contain_main_fails_closed():
    good = [["source", "/x/a", "/x", "/x"]]
    assert contain.main(["1", "not-json"], {"OMPI_COMM_WORLD_RANK": "0"}, out=_Sink()) == 1
    assert contain.main(["1", json.dumps(good)], {}, out=_Sink()) == 1                  # rank 모름
    assert contain.main(["x", json.dumps(good)], {"OMPI_COMM_WORLD_RANK": "0"}, out=_Sink()) == 1
    assert contain.main([], {"OMPI_COMM_WORLD_RANK": "0"}, out=_Sink()) == 1
    assert contain.rank_side("0", 2) == "source" and contain.rank_side("2", 2) == "destination"
    assert contain.rank_side("-1", 2) is None and contain.rank_side(None, 2) is None


class _Sink:
    def __init__(self):
        self.text = ""

    def write(self, s):
        self.text += s

    def flush(self):
        pass


def test_runner_lifts_the_rank_marker_into_the_pod_log(tree, capsys):
    """rank 의 마커는 mpirun 출력에만 있다 — 런처가 파드 로그로 다시 찍어야 stepper 가 승격한다(summary 는 null 3키)."""
    entries = [_entry("source", "/s", "/s", "/s")]
    rec = _Rec(mpirun_stdout=f"{_EXEC_REDIRECTED}\nDMS_JR_PV_PATH_REDIRECTED side='source' path='/s' root='/s'\n"
                             "mpirun detected that one or more processes exited with non-zero status\n")
    rc, rec = _run_tool(_nsync_env(tree, entries), rec)
    out = capsys.readouterr().out
    assert rc == 1 and _EXEC_REDIRECTED in out.split("\n") and "side='source'" in out
    assert json.loads(rec.writes[f"{tree / 'art'}/summary.json"]) == {"returncode": None, "files": None, "bytes": None}
    # PV 가 아닌 nsync 는 같은 출력이 와도 끌어올리지 않는다(rank 가 마커를 낼 일이 없다 -- 종전 요약 경로)
    rc, rec = _run_tool(_nsync_env(tree, None), _Rec(mpirun_stdout=f"{_EXEC_REDIRECTED}\n"))
    assert _EXEC_REDIRECTED not in capsys.readouterr().out
    # 성공한 nsync 의 출력 경로에 마커 글자가 들어 있어도(줄 전체가 아님·rc 0) 봉쇄로 오인하지 않는다(2차 리뷰)
    ok_out = f"Requested target: /k8s_pv/nfs/team-a/d/{_EXEC_REDIRECTED}\nExecution completed successfully\n"
    rc, rec = _run_tool(_nsync_env(tree, entries), _Rec(mpirun_stdout=ok_out, mpirun_rc=0))
    assert rc == 0 and _EXEC_REDIRECTED not in capsys.readouterr().out.split("\n")
    # rc 가 0 이 아니어도 마커가 줄 전체가 아니면 끌어올리지 않는다(도구 자체의 실패 -- 종전 요약 경로)
    rc, rec = _run_tool(_nsync_env(tree, entries), _Rec(mpirun_stdout=ok_out, mpirun_rc=2))
    assert rc == 2 and _EXEC_REDIRECTED not in capsys.readouterr().out.split("\n")


def test_non_pv_jobs_are_byte_identical(tree):
    """PV 가 아닌 잡: 스크립트에 봉쇄 함수가 없고 인자·러너 env·rank.sh 도 그대로다(이 기능 이전과 같은 매니페스트)."""
    for spec in (_spec("scan", {"target": "/x/a", "storage": "s", "managed_root": "/x"}),
                 _spec("sync", {"source": "/x/a", "source_storage": "s", "destination": "/x/b",
                                "destination_storage": "s"}),
                 _spec("sync", {"source": "/x/a", "source_storage": "s", "destination": "/x/b",
                                "destination_storage": "s"}, tool="nsync"),
                 _spec("rm", {"target": "/x/a", "storage": "s", "managed_root": "/x"})):
        script, args = _preflight_script(spec)
        assert "dms_contain" not in script and "dms_within" not in script
        assert "DMS_JR_CONTAIN" not in _launcher_env(spec)
    script, args = _preflight_script(_spec("scan", {"target": "/x/a", "storage": "s", "managed_root": "/x"}))
    assert args == ["/x/a"]
    script, args = _preflight_script(_spec("rm", {"target": "/x/a", "storage": "s", "managed_root": "/x"}))
    assert args == ["/x/a", "/x"]
    rc, rec = _run_tool(_nsync_env(tree, None))
    assert _rank_sh(rec, tree).startswith("#!/bin/sh\nexec nsync ")
    pod = build_preflight_pod(_scan("/x/a", "/x", "/x"), job_image="i", namespace="dms", volumes=[], node="n1")
    assert "dms_contain" in " ".join(pod["spec"]["containers"][0]["command"])
