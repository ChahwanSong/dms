"""PV 주소 봉쇄(2026-10-26 보안 리뷰 — dms-k8s 「모든 잡 root + namespace 단위 권한」)를 **실제 sh** 로 돌린다.

모든 잡이 root 라 커널 DAC 가 없다. 사용자 파드가 자기 PV 안에 남의 PV 를 가리키는 심링크를 심으면 PV A 권한만으로 root
dsync·dscan 이 PV B 를 읽거나 덮어쓸 수 있었다. 2026-10-28 부터 규칙은 sync·rm 과 같은 하나(execution_manifests._PATH_CONTAINED_FN,
기준 managed_root)이고 PV 주소 쪽은 「PV 루트 아래」 글자 경계(_PV_WITHIN_FN)만 더한다 — 사용자 결정 「한 규칙으로 통일」. 봉쇄
조각은 `set -e` 아래 함수·case·명령 치환의 상호작용이라 문자열 계약으로는 증명할 수 없다 — 실 파드와 같은 명령(["sh", "-c",
script, "sh", *args])을 임시 트리에 대고 돌리고, 같은 트리로 러너의 재확인(dms_job_runner.contain.entry_redirected)도 돌려 두
판정이 갈라지지 않음을 고정한다. 기준점은 PV 루트가 아니라 managed_root 다(2차 보안 리뷰 — PV 루트 자신·managed_root 와 PV 루트
사이가 링크인 경우). nsync 는 rank 가 자기 역할 쪽을 본다(rank.sh → python3 -m dms_job_runner.contain). 직접 경로 sync 도 같은
봉쇄를 받고(tests/test_sync_containment_sh.py), 직접 경로 scan 만 봉쇄 밖이다(맨 아래).
"""
import dataclasses
import json
import os
import subprocess
import sys

import pytest

from dms.execution import JobSpec
from dms.execution_manifests import _launcher_env, _preflight_script, build_preflight_pod
from dms_job_runner import contain
from dms_job_runner.runner import entry_redirected, run_job

_REDIRECTED = "DMS_PREFLIGHT_REASON=target_path_redirected"
_SRC_REDIRECTED = "DMS_PREFLIGHT_REASON=source_path_redirected"
_DST_REDIRECTED = "DMS_PREFLIGHT_REASON=destination_path_redirected"
_EXEC_REDIRECTED = "DMS_EXEC_REASON=target_path_redirected"
_EXEC_SRC_REDIRECTED = "DMS_EXEC_REASON=source_path_redirected"
_EXEC_MARKERS = (_EXEC_REDIRECTED, _EXEC_SRC_REDIRECTED, "DMS_EXEC_REASON=destination_path_redirected")
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
    (a / "data/x\n").symlink_to(str(b))                            # 이름이 줄바꿈으로 끝나는 링크(옆에 실제 data/x, 2026-10-28)
    (a / "data\n").mkdir()                                         # 이름이 줄바꿈으로 끝나는 실제 디렉터리
    return tmp_path


A = "m/pvc-a/pvc-a-data"
M = "m"

# (기준 managed_root, PV 루트, PV 루트 기준 상대경로) -> 통과 여부. 경로는 존재해도 없어도 된다(없으면 있는 가장 깊은 상위로 판정).
_PASS = [
    (M, A, "data/x"),                   # 정상(있음)
    (M, A, ""),                         # PV 루트 자체(sync 소스·목적지·scan 허용)
    (M, A, "new-dest"),                 # 없는 목적지 -- 가장 깊은 상위(=루트)로 판정
    (M, A, "out/new"),                  # 없는 목적지 -- 상위 out
    (M, A, "out/new/deeper"),           # 없는 끝이 둘 -- 가장 깊은 「있는」 상위 out(2026-10-28 통일 -- 예전 PV 규칙은 부모 하나만)
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
    (M, A, "dangling/new"),             # 끊긴 링크 아래 없는 경로 -- 가장 깊은 「있는」 성분이 링크
    (M, A, "to-b/new-dest"),            # 없는 목적지의 상위가 링크
    (M, A, "data/x/../../../pvc-b"),    # 변조 행(「..」) -- 실제 위치와 글자가 맞을 수 없다
    (M, A, "data/x/"),                  # 끝 슬래시
    (M, A, "data/."),
    (M, A, "data//x"),                  # 「//」
    (M, "m/pvc-c/pvc-c-data", "secret"),   # PV 루트 자신이 링크 -- PV 루트를 기준점으로 쓰면 통과했다(2차 리뷰)
    (M, "m/pvc-c/pvc-c-data", ""),
    (M, "m/pvc-d/pvc-b-data", "secret"),   # managed_root 와 PV 루트 사이 성분이 링크
    ("mlink", A, "data/x"),             # 경로가 기준(managed_root) 밖 -- 글자로 막는다
    # 줄바꿈이 든 경로는 셸·러너 둘 다 막는다(2026-10-28 리뷰 -- 셸 $(dirname)·$(realpath) 가 끝 줄바꿈을 지워 「x\n」 링크
    # 아래를 옆의 실제 「x」로 읽어 통과시켰고, 실제 디렉터리 「data\n」은 셸에서만 막혔다; 제출은 unsafe_path 로 거부한다)
    (M, A, "data/x\n/new-dest"),        # 없는 끝 -- 셸이 상위로 걸어 올라가며 「x\n」을 실제 「x」로 읽었다(예전 셸 통과)
    (M, A, "data\n"),
]


def _case(tree, anchor_rel, root_rel, rel):
    root = str(tree / root_rel)
    path = root if rel == "" else f"{root}/{rel}"
    return path, root, str(tree / anchor_rel)


def _scan(path, root, anchor):
    return _spec("scan", {"target": path, "storage": "s", "managed_root": anchor, "contain": {"target": {"root": root}}})


@pytest.mark.parametrize("anchor_rel,root_rel,rel", _PASS)
def test_scan_and_runner_pass(tree, anchor_rel, root_rel, rel):
    path, root, anchor = _case(tree, anchor_rel, root_rel, rel)
    if rel in ("new-dest", "out/new", "out/new/deeper"):
        pytest.skip("scan 대상은 있어야 한다 -- 없는 경로는 아래 sync 목적지에서 본다")
    rc, out = _run(_scan(path, root, anchor))
    assert rc == 0 and out.strip().endswith("DMS_PREFLIGHT_OK"), out
    assert entry_redirected(path, root, anchor) is False


@pytest.mark.parametrize("anchor_rel,root_rel,rel", _REDIRECT)
def test_scan_and_runner_redirect(tree, anchor_rel, root_rel, rel):
    path, root, anchor = _case(tree, anchor_rel, root_rel, rel)
    rc, out = _run(_scan(path, root, anchor))
    # 봉쇄가 첫 검사라(2026-10-28) 링크 대상은 늘 이 사유다. 없는 대상만 target_not_readable(기존 사유) -- 그 경우도 실행은 막힌다.
    assert rc != 0 and (_REDIRECTED in out or "target_not_readable" in out), out
    if rel in ("to-b", "lastlink", "dangling", "to-b/secret"):
        assert _REDIRECTED in out, out
    assert entry_redirected(path, root, anchor) is True


def _sync_paths(src, dest, src_root, dest_root, src_anchor, dest_anchor, *, pv=("source", "destination")):
    bounds = {"source": src_root, "destination": dest_root}
    return {"source": src, "source_storage": "s", "source_root": src_anchor,
            "destination": dest, "destination_storage": "s", "destination_root": dest_anchor,
            "contain": {side: {"root": bounds[side]} for side in pv}}


@pytest.mark.parametrize("anchor_rel,root_rel,rel", _PASS + _REDIRECT)
def test_shell_and_runner_agree_for_sync_destination(tree, anchor_rel, root_rel, rel):
    """목적지(없어도 되는 쪽)로 같은 트리를 셸·러너가 같게 판정하는가 — 소스는 PV A 의 멀쩡한 디렉터리."""
    dest, root, anchor = _case(tree, anchor_rel, root_rel, rel)
    src, src_root, src_anchor = _case(tree, M, A, "data/x")
    rc, out = _run(_spec("sync", _sync_paths(src, dest, src_root, root, src_anchor, anchor)))
    shell_redirected = _DST_REDIRECTED in out
    if rc != 0 and not shell_redirected:
        pytest.skip(f"기존 목적지 검사가 먼저 막았다: {out.strip()}")
    assert shell_redirected is entry_redirected(dest, root, anchor), out


def _sync_spec(tree, src_rel, dest_rel, *, tool="dsync"):
    src, root, anchor = _case(tree, M, A, src_rel)
    dest, _, _ = _case(tree, M, A, dest_rel)
    return _spec("sync", _sync_paths(src, dest, root, root, anchor, anchor), tool=tool)


def test_sync_source_link_to_another_pv_is_blocked(tree):
    rc, out = _run(_sync_spec(tree, "to-b/secret", "out/copy"))
    assert rc != 0 and _SRC_REDIRECTED in out and _DST_REDIRECTED not in out


def test_sync_containment_is_the_first_check_on_each_side(tree):
    """링크를 따라간 읽기·종류 검사가 진짜 원인을 가리지 않는다 — 목적지가 남의 PV 의 **파일** 로 가는 링크여도 사유는
    destination_not_directory 가 아니라 봉쇄다(2026-10-28 -- 예전 PV 봉쇄는 기존 검사 **뒤**에 붙었다)."""
    a = tree / A
    (a / "to-file").symlink_to("../../pvc-b/pvc-b-data/secret/keep")
    rc, out = _run(_sync_spec(tree, "data/x", "to-file"))
    assert rc != 0 and _DST_REDIRECTED in out and "destination_not_directory" not in out, out


def test_nsync_role_preflight_checks_only_its_side(tree):
    spec = _sync_spec(tree, "to-b/secret", "out/copy", tool="nsync")
    assert _SRC_REDIRECTED in _run(spec, role="source")[1]
    rc, out = _run(spec, role="destination")
    assert rc == 0 and out.strip().endswith("DMS_PREFLIGHT_OK"), out


def test_empty_or_bad_values_block(tree):
    path, root, anchor = _case(tree, M, A, "data/x")
    for c in ({"root": ""}, {"root": None}, {}, "not-a-dict"):
        rc, out = _run(_spec("scan", {"target": path, "storage": "s", "managed_root": anchor, "contain": {"target": c}}))
        assert rc != 0 and _REDIRECTED in out, c
    for bad_anchor in ("", None):      # 기준 managed_root 가 없다(변조 행) -- 봉쇄가 막는다
        rc, out = _run(_spec("scan", {"target": path, "storage": "s", "managed_root": bad_anchor,
                                      "contain": {"target": {"root": root}}}))
        assert rc != 0 and _REDIRECTED in out, bad_anchor
    assert entry_redirected(path, "", anchor) is True
    assert entry_redirected(path, root, "") is True
    assert entry_redirected(path, None, anchor) is True


def test_path_outside_its_pv_root_is_blocked(tree):
    """managed_root 안이라도 고른 PV 루트 밖이면 막는다(글자 경계 — 변조 행)."""
    path, _, anchor = _case(tree, M, "m/pvc-b/pvc-b-data", "secret")
    root = str(tree / A)
    rc, out = _run(_scan(path, root, anchor))
    assert rc != 0 and _REDIRECTED in out
    assert entry_redirected(path, root, anchor) is True
    # sync 목적지도 같다 -- 사유는 그 쪽 코드
    src, _, _ = _case(tree, M, A, "data/x")
    rc, out = _run(_spec("sync", _sync_paths(src, path, root, root, anchor, anchor)))
    assert rc != 0 and _DST_REDIRECTED in out


def _rm(target, root, anchor):
    return _spec("rm", {"target": target, "storage": "s", "managed_root": anchor, "contain": {"target": {"root": root}}})


def test_pv_rm_keeps_the_managed_root_anchor_and_adds_the_pv_bound(tree):
    """PV 주소 rm: 심링크 검사는 rm 봉쇄(대상의 상위에 같은 규칙, 기준 managed_root)가, PV 경계는 글자 검사(dms_within — 대상의
    상위가 PV 루트 안 = 대상이 PV 루트 **아래**)가 맡는다. 대상 자신이 링크면 링크만 지운다(rm 끝 링크 허용 -- 2026-10-28 사용자 결정)."""
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
    # PV 루트 자신(제출이 pv_root_rm_forbidden 으로 먼저 막는다 -- 변조 행) -> 글자 경계(대상의 상위가 PV 루트 밖)
    rc, out = _run(_rm(root, root, anchor))
    assert rc != 0 and _REDIRECTED in out
    # 빈 PV 루트 -> 막음
    rc, out = _run(_rm(f"{root}/data/x", "", anchor))
    assert rc != 0 and _REDIRECTED in out
    # 정상 대상·마지막 성분이 링크인 대상(drm 은 링크만 지운다)·끊긴 링크 -> 통과
    for target in (f"{root}/data/x", f"{root}/lastlink", f"{root}/to-b", f"{root}/dangling"):
        rc, out = _run(_rm(target, root, anchor))
        assert rc == 0 and out.strip().endswith("DMS_PREFLIGHT_OK"), (target, out)
    assert _launcher_env(_rm(f"{root}/data/x", root, anchor))["DMS_JR_RM_ROOT"] == anchor


class _Rec:
    def __init__(self, mpirun_stdout="ok\n", mpirun_rc=None, mpirun_stderr=""):
        self.writes, self.ran = {}, []
        self.mpirun_stdout = mpirun_stdout
        self.mpirun_rc = mpirun_rc
        self.mpirun_stderr = mpirun_stderr

    def write_text(self, path, content, *, append=False):
        self.writes[path] = content

    def run(self, command):
        self.ran.append(command)
        out = "10.0.0.1 w\n" if command[0] == "getent" else self.mpirun_stdout if "mpirun" in command else "ok\n"

        rc = self.mpirun_rc if "mpirun" in command and self.mpirun_rc is not None else (
            1 if "mpirun" in command and any(m in out for m in _EXEC_MARKERS) else 0)
        err = self.mpirun_stderr if "mpirun" in command else ""

        class R:
            returncode = rc
            stdout = out
            stderr = err
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
    capsys.readouterr()
    # dsync 는 쪽별 사유를 찍는다(2026-10-28) -- 소스가 남의 PV 로 가는 링크
    rc, rec = _run_tool(_env("dsync", [src, ok + "-copy"], str(tree / "art"), DMS_JR_CONTAIN=json.dumps(
        [_entry("source", src, root, anchor), _entry("destination", ok + "-copy", root, anchor)])))
    out = capsys.readouterr().out.split("\n")
    assert rc == 1 and _EXEC_SRC_REDIRECTED in out and _EXEC_REDIRECTED not in out
    assert any(ln.startswith("DMS_JR_PATH_REDIRECTED side='source' ") for ln in out)


def _nsync_env(tree, entries, **kw):
    return _env("nsync", ["/s", "/d"], str(tree / "art"), DMS_JR_PROCESSES_PER_NODE="2", DMS_JR_PROCESS_COUNT="6",
                DMS_JR_SOURCE_NODES=json.dumps(["s1", "s2"]), DMS_JR_DEST_NODES=json.dumps(["d1"]),
                **({} if entries is None else {"DMS_JR_CONTAIN": json.dumps(entries)}), **kw)


def _rank_sh(rec, tree):
    return rec.writes[f"{tree / 'art'}/rank.sh"]


def test_nsync_rank_script_checks_its_side_before_exec(tree):
    """nsync 런처는 데이터 경로를 볼 수 없다 — rank.sh 가 도구 exec 직전에 자기 역할 쪽을 본다(소스 rank 수 = 소스 노드 2 ×
    slot 2 = 4). 실 sh 로 rank.sh 를 돌린다: 소스 rank 는 막히고(소스 사유 마커), 목적지 rank 는 검사를 지나 도구 exec 로 간다."""
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
            assert proc.returncode == 1 and _EXEC_SRC_REDIRECTED in proc.stdout.split("\n"), (rank, proc.stdout)
        else:   # 검사를 지나 exec nsync -- 이 호스트엔 nsync 가 없어 127
            assert proc.returncode == 127 and "DMS_EXEC_REASON=" not in proc.stdout, (rank, proc.stdout, proc.stderr)


def test_contain_main_fails_closed():
    good = [["source", "/x/a", "/x", "/x"]]
    assert contain.main(["1", "not-json"], {"OMPI_COMM_WORLD_RANK": "0"}, out=_Sink()) == 1
    assert contain.main(["1", json.dumps(good)], {}, out=_Sink()) == 1                  # rank 모름
    assert contain.main(["x", json.dumps(good)], {"OMPI_COMM_WORLD_RANK": "0"}, out=_Sink()) == 1
    assert contain.main([], {"OMPI_COMM_WORLD_RANK": "0"}, out=_Sink()) == 1
    sink = _Sink()
    contain.main(["1", "not-json"], {"OMPI_COMM_WORLD_RANK": "0"}, out=sink)
    assert sink.text.startswith(_EXEC_REDIRECTED + "\n")                                 # 쪽을 모르면 target 코드
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
    rec = _Rec(mpirun_stdout=f"{_EXEC_SRC_REDIRECTED}\nDMS_JR_PATH_REDIRECTED side='source' path='/s' root='/s' bound='/s'\n"
                             "mpirun detected that one or more processes exited with non-zero status\n")
    rc, rec = _run_tool(_nsync_env(tree, entries), rec)
    out = capsys.readouterr().out
    assert rc == 1 and _EXEC_SRC_REDIRECTED in out.split("\n") and "side='source'" in out
    assert json.loads(rec.writes[f"{tree / 'art'}/summary.json"]) == {"returncode": None, "files": None, "bytes": None}
    # 두 쪽 rank 가 다 막혔으면 나온 순서대로 둘 다(stepper 는 첫 줄을 쓴다)
    both = f"{_EXEC_SRC_REDIRECTED}\nDMS_EXEC_REASON=destination_path_redirected\n{_EXEC_SRC_REDIRECTED}\n"
    rc, rec = _run_tool(_nsync_env(tree, entries), _Rec(mpirun_stdout=both))
    lines = [ln for ln in capsys.readouterr().out.split("\n") if ln.startswith("DMS_EXEC_REASON=")]
    assert rc == 1 and lines == [_EXEC_SRC_REDIRECTED, "DMS_EXEC_REASON=destination_path_redirected"]
    # 키가 없는 nsync(이 검사 전의 제어면)는 같은 출력이 와도 끌어올리지 않는다(종전 요약 경로). 줄 단위로 본다(d192 이식):
    # rc≠0 이면 러너가 도구 출력 꼬리를 "  | " 접두로 다시 찍어 그 글자가 파드 로그에 남지만, 마커 줄(strip 뒤 줄 머리)은 아니다 --
    # stepper 의 _parse_marker 도 같은 줄 머리 비교라 승격되지 않는다.
    rc, rec = _run_tool(_nsync_env(tree, None), _Rec(mpirun_stdout=f"{_EXEC_SRC_REDIRECTED}\n"))
    out = capsys.readouterr().out
    assert rc == 1 and not any(ln.strip().startswith("DMS_EXEC_REASON=") for ln in out.split("\n")), out
    assert f"  | {_EXEC_SRC_REDIRECTED}" in out.split("\n")                 # 꼬리 발췌로만 남는다
    # 성공한 nsync 의 출력 경로에 마커 글자가 들어 있어도(줄 전체가 아님·rc 0) 봉쇄로 오인하지 않는다(2차 리뷰)
    ok_out = f"Requested target: /k8s_pv/nfs/team-a/d/{_EXEC_SRC_REDIRECTED}\nExecution completed successfully\n"
    rc, rec = _run_tool(_nsync_env(tree, entries), _Rec(mpirun_stdout=ok_out, mpirun_rc=0))
    assert rc == 0 and _EXEC_SRC_REDIRECTED not in capsys.readouterr().out.split("\n")
    # rc 가 0 이 아니어도 마커가 줄 전체가 아니면 끌어올리지 않는다(도구 자체의 실패 -- 종전 요약 경로)
    rc, rec = _run_tool(_nsync_env(tree, entries), _Rec(mpirun_stdout=ok_out, mpirun_rc=2))
    assert rc == 2 and _EXEC_SRC_REDIRECTED not in capsys.readouterr().out.split("\n")


@pytest.mark.parametrize("stdout_tail,stderr", [
    ("", "MPI_ABORT was invoked on rank 1 in communicator MPI_COMM_WORLD with errorcode 1\n"),     # → tool_aborted
    ("", "ABORT: nsync rank 1 failed before MPI_Init\n"),                                            # → tool_aborted
    # 다른 rank 의 nsync 가 항목 실패를 찍었다(→ sync_items_failed -- 「일부 반영」)
    ("[2026-10-28T09:00:00] [0] [/release/mpifileutils/src/nsync/nsync.c:897] ERROR: Failed items: 1 (copy 1); "
     "listed below: 1\n", ""),
], ids=["mpi_abort", "abort_line", "nsync_failed_items"])
def test_rank_marker_is_lifted_before_the_tool_failure_report(tree, capsys, stdout_tail, stderr):
    """7b(rank 마커 끌어올리기)는 8(summary v2)·9(도구 사유 마커 + 출력 꼬리)보다 **앞**이다(runner 7b 주석, 2026-10-28 리뷰 --
    위 테스트의 mpirun 출력은 도구 사유가 나오지 않아 두 순서를 가르지 못했다). 여기는 도구 사유가 나오는 출력(MPI_ABORT →
    tool_aborted, nsync 항목 실패 → sync_items_failed)을 같이 준다: 순서가 뒤집히면 9 가 도구 사유 마커를 먼저 찍어 stepper 의 첫
    마커 승격을 빼앗고, 데이터를 만지기 전의 봉쇄 거부가 「도구 중단」·「일부 반영」으로 보인다."""
    from dms.execution_manifests import parse_execution_reason
    entries = [_entry("source", "/s", "/s", "/s"), _entry("destination", "/d", "/d", "/d")]
    dst = "DMS_EXEC_REASON=destination_path_redirected"
    detail = "DMS_JR_PATH_REDIRECTED side='destination' path='/d' root='/d' bound='/d'"
    rec = _Rec(mpirun_stdout=f"{dst}\n{detail}\n{stdout_tail}", mpirun_rc=1, mpirun_stderr=stderr)
    rc, rec = _run_tool(_nsync_env(tree, entries), rec)
    out = capsys.readouterr().out
    assert rc == 1
    markers = [ln for ln in out.split("\n") if ln.startswith("DMS_EXEC_REASON=")]
    assert markers and markers[0] == dst, out                          # 첫 마커가 쪽별 봉쇄 사유
    assert markers == [dst], out                                        # 도구 사유 마커는 아예 찍히지 않는다
    assert parse_execution_reason([("launcher", out, None)], operation="sync") == "destination_path_redirected"
    assert json.loads(rec.writes[f"{tree / 'art'}/summary.json"]) == {"returncode": None, "files": None, "bytes": None}
    assert "DMS_JR_TOOL_OUTPUT" not in out                              # 도구 출력 꼬리(9)도 없다 -- 도구는 시작 전에 끊겼다
    assert detail in out.split("\n")


def test_direct_path_scan_stays_uncontained_and_direct_sync_is_contained(tree):
    """직접 경로 scan(특권 운영자 전용·읽기만)은 봉쇄 밖 -- 스크립트·인자·러너 env 가 이 기능 이전과 같다. 직접 경로 sync 는
    2026-10-28 부터 PV 와 같은 봉쇄(경계 = 기준이라 글자 경계 함수는 싣지 않는다), 직접 경로 rm 은 PV 경계 없이 rm 봉쇄만."""
    scan = _spec("scan", {"target": "/x/a", "storage": "s", "managed_root": "/x"})
    script, args = _preflight_script(scan)
    assert script == ('set -e; test -r "$1" || { echo DMS_PREFLIGHT_REASON=target_not_readable; exit 1; }; '
                      'echo DMS_PREFLIGHT_OK') and args == ["/x/a"]
    assert "DMS_JR_CONTAIN" not in _launcher_env(scan)
    sync = _spec("sync", {"source": "/x/a", "source_storage": "s", "source_root": "/x",
                          "destination": "/y/b", "destination_storage": "t", "destination_root": "/y"})
    script, args = _preflight_script(sync)
    assert "dms_path_contained" in script and "dms_within" not in script
    assert args == ["/x/a", "/y/b", "root", "/x", "/y"]
    assert json.loads(_launcher_env(sync)["DMS_JR_CONTAIN"]) == [["source", "/x/a", "/x", "/x"],
                                                                 ["destination", "/y/b", "/y", "/y"]]
    rm = _spec("rm", {"target": "/x/a", "storage": "s", "managed_root": "/x"})
    script, args = _preflight_script(rm)
    assert "dms_path_contained" in script and "dms_within" not in script and args == ["/x/a", "/x"]
    assert "DMS_JR_CONTAIN" not in _launcher_env(rm)
    pod = build_preflight_pod(_scan("/x/a", "/x", "/x"), job_image="i", namespace="dms", volumes=[], node="n1")
    assert "dms_path_contained" in " ".join(pod["spec"]["containers"][0]["command"])
    # PV 주소 쪽은 env 에 PV 루트를 경계로 싣는다(직접 경로 쪽은 경계 = 기준)
    mixed = _spec("sync", {"source": "/x/a", "source_storage": "s", "source_root": "/x",
                           "destination": "/y/pv/b", "destination_storage": "t", "destination_root": "/y",
                           "contain": {"destination": {"root": "/y/pv"}}})
    mixed = dataclasses.replace(mixed, result_owner="0:0", result_owner_rule="storage")   # PV 목적지 결과 소유(_auto_chown)
    assert json.loads(_launcher_env(mixed)["DMS_JR_CONTAIN"]) == [["source", "/x/a", "/x", "/x"],
                                                                  ["destination", "/y/pv/b", "/y/pv", "/y"]]
    script, args = _preflight_script(mixed)
    assert "dms_within" in script and args == ["/x/a", "/y/pv/b", "root", "/x", "/y", "/y/pv"]
