"""PV 주소 봉쇄(2026-10-26 보안 리뷰 — dms-k8s 「모든 잡 root + namespace 단위 권한」)를 **실제 sh** 로 돌린다.

모든 잡이 root 라 커널 DAC 가 없다. 사용자 파드가 자기 PV 안에 남의 PV 를 가리키는 심링크를 심으면 PV A 권한만으로 root
dsync·dscan 이 PV B 를 읽거나 덮어쓸 수 있었다. 봉쇄 조각(execution_manifests._PV_CONTAIN_FN)은 `set -e` 아래 함수·case·
명령 치환의 상호작용이라 문자열 계약으로는 증명할 수 없다 — 실 파드와 같은 명령(["sh", "-c", script, "sh", *args])을 임시
트리에 대고 돌리고, 같은 트리로 러너의 재확인(dms_job_runner.runner.pv_path_redirected)도 돌려 두 판정이 갈라지지 않음을 고정한다.
PV 가 아닌 잡은 스크립트·인자·러너 env 가 이 기능 이전과 같다(맨 아래).
"""
import json
import subprocess

import pytest

from dms.execution import JobSpec
from dms.execution_manifests import _launcher_env, _preflight_script, build_preflight_pod
from dms_job_runner.runner import pv_path_redirected, run_job

_REDIRECTED = "DMS_PREFLIGHT_REASON=target_path_redirected"
_ROOT_ID = {"uid": 0, "gid": 0, "username": "alice", "privileged": True}


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
    for d in (a / "data/x", a / "out", b / "secret", a / "dir with space/x*[y]"):
        d.mkdir(parents=True)
    (b / "secret/keep").write_text("bob")
    (a / "data/x/f").write_text("alice")
    (a / "to-b").symlink_to("../../pvc-b/pvc-b-data")              # 상대 링크 -> 남의 PV
    (a / "abs-b").symlink_to(str(b))                               # 절대 링크 -> 남의 PV
    (a / "lastlink").symlink_to("data")                            # 마지막 성분이 링크(PV 안을 가리켜도 막는다)
    (a / "dangling").symlink_to("nowhere")                         # 끊긴 링크
    (a / "inner").mkdir()
    (a / "inner/link-out").symlink_to(str(b / "secret"))           # 트리 **안쪽** 링크 -- 도구가 따라가지 않는다(통과)
    (tmp_path / "alink").symlink_to(str(a))                        # PV 루트 자신이 링크
    return tmp_path


A = "m/pvc-a/pvc-a-data"

# (PV 루트 상대 표기, 경로 상대(그 루트 기준)) -> 통과 여부. 경로는 존재해도 없어도 된다(없으면 부모로 판정).
_PASS = [
    (A, "data/x"),                  # 정상(있음)
    (A, ""),                        # PV 루트 자체(sync 소스·목적지·scan 허용)
    (A, "new-dest"),                # 없는 목적지 -- 부모(=루트)로 판정
    (A, "out/new"),                 # 없는 목적지 -- 부모 out
    (A, "inner"),                   # 안쪽에 링크가 있는 디렉터리(그 링크는 도구가 따라가지 않는다)
    ("alink", "data/x"),            # PV 루트 자신이 링크 -- 양쪽을 같은 realpath 로 본다
    (A, "dir with space/x*[y]"),    # 공백·글롭 문자
]
_REDIRECT = [
    (A, "to-b/secret"),             # 상위가 상대 링크(-> 남의 PV)
    (A, "abs-b/secret"),            # 상위가 절대 링크
    (A, "to-b"),                    # 마지막 성분이 남의 PV 를 가리키는 링크
    (A, "lastlink"),                # 마지막 성분 링크(PV 안을 가리켜도) -- 도구가 따라가므로 막는다
    (A, "dangling"),                # 끊긴 링크
    (A, "to-b/new-dest"),           # 없는 목적지의 부모가 링크
    (A, "data/x/../../../pvc-b"),   # 변조 행(「..」) -- 실제 위치와 글자가 맞을 수 없다
    (A, "data/x/"),                 # 끝 슬래시
    (A, "data/."),
]


def _paths(tree, root_rel, rel):
    root = str(tree / root_rel)
    path = root if rel == "" else f"{root}/{rel}"
    return path, root


@pytest.mark.parametrize("root_rel,rel", _PASS)
def test_scan_and_runner_pass(tree, root_rel, rel):
    path, root = _paths(tree, root_rel, rel)
    if rel in ("new-dest", "out/new"):
        pytest.skip("scan 대상은 있어야 한다 -- 없는 경로는 아래 sync 목적지에서 본다")
    rc, out = _run(_spec("scan", {"target": path, "storage": "s", "managed_root": str(tree / "m"),
                                  "contain": {"target": root}}))
    assert rc == 0 and out.strip().endswith("DMS_PREFLIGHT_OK"), out
    assert pv_path_redirected(path, root) is False


@pytest.mark.parametrize("root_rel,rel", _REDIRECT)
def test_scan_and_runner_redirect(tree, root_rel, rel):
    path, root = _paths(tree, root_rel, rel)
    rc, out = _run(_spec("scan", {"target": path, "storage": "s", "managed_root": str(tree / "m"),
                                  "contain": {"target": root}}))
    # 없는 대상은 target_not_readable 이 먼저다(기존 사유 우선) -- 그 경우도 실행은 막힌다.
    assert rc != 0 and (_REDIRECTED in out or "target_not_readable" in out), out
    assert pv_path_redirected(path, root) is True


@pytest.mark.parametrize("root_rel,rel", _PASS + _REDIRECT)
def test_shell_and_runner_agree_for_sync_destination(tree, root_rel, rel):
    """목적지(없어도 되는 쪽)로 같은 트리를 셸·러너가 같게 판정하는가 — 소스는 PV A 의 멀쩡한 디렉터리."""
    dest, root = _paths(tree, root_rel, rel)
    src, _ = _paths(tree, A, "data/x")
    rc, out = _run(_spec("sync", {"source": src, "source_storage": "s", "destination": dest,
                                  "destination_storage": "s", "contain": {"source": str(tree / A),
                                                                           "destination": root}}))
    shell_redirected = _REDIRECTED in out
    if rc != 0 and not shell_redirected:
        pytest.skip(f"기존 목적지 검사가 먼저 막았다: {out.strip()}")
    assert shell_redirected is pv_path_redirected(dest, root), out


def test_sync_source_link_to_another_pv_is_blocked(tree):
    src, root = _paths(tree, A, "to-b/secret")
    dest, _ = _paths(tree, A, "out/copy")
    rc, out = _run(_spec("sync", {"source": src, "source_storage": "s", "destination": dest,
                                  "destination_storage": "s", "contain": {"source": root, "destination": root}}))
    assert rc != 0 and _REDIRECTED in out


def test_nsync_role_preflight_checks_only_its_side(tree):
    src, root = _paths(tree, A, "to-b/secret")
    dest, _ = _paths(tree, A, "out/copy")
    spec = _spec("sync", {"source": src, "source_storage": "s", "destination": dest, "destination_storage": "s",
                          "contain": {"source": root, "destination": root}}, tool="nsync")
    assert _REDIRECTED in _run(spec, role="source")[1]
    rc, out = _run(spec, role="destination")
    assert rc == 0 and out.strip().endswith("DMS_PREFLIGHT_OK"), out


def test_empty_or_bad_root_blocks(tree):
    path, _ = _paths(tree, A, "data/x")
    rc, out = _run(_spec("scan", {"target": path, "storage": "s", "managed_root": "/", "contain": {"target": ""}}))
    assert rc != 0 and _REDIRECTED in out
    assert pv_path_redirected(path, "") is True
    rc, out = _run(_spec("scan", {"target": path, "storage": "s", "managed_root": "/", "contain": {"target": None}}))
    assert rc != 0 and _REDIRECTED in out


def test_pv_rm_is_anchored_at_the_pv_root(tree):
    """PV 주소 rm 은 봉쇄 기준이 managed_root 가 아니라 PV 루트 — PV 안 링크로 남의 PV 를 지우지 못한다."""
    target = str(tree / A / "to-b/secret")
    spec = _spec("rm", {"target": target, "storage": "s", "managed_root": str(tree / "m"),
                        "contain": {"target": str(tree / A)}, "containment_root": str(tree / A)})
    rc, out = _run(spec)
    assert rc != 0 and _REDIRECTED in out
    assert _launcher_env(spec)["DMS_JR_RM_ROOT"] == str(tree / A)


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


def _env(tool, argv, art, **kw):
    env = {"DMS_JR_TOOL": tool, "DMS_JR_OPERATION": "scan" if tool == "dscan" else "sync", "DMS_JR_PHASE": "execution",
           "DMS_JR_DRYRUN": "0", "DMS_JR_PROCESS_COUNT": "1", "DMS_JR_PROCESSES_PER_NODE": "1", "DMS_JR_UID": "0",
           "DMS_JR_GID": "0", "DMS_JR_USERNAME": "alice", "DMS_JR_ARTIFACT_DIR": art, "DMS_JR_ARGV": json.dumps(argv)}
    env.update(kw)
    return env


def _run_tool(env):
    rec = _Rec()
    rc = run_job(env, run=rec.run, write_text=rec.write_text, read_text=lambda p: "", sleep=lambda s: None,
                 wait_hostfile=lambda: (["w0"], "/tmp/hostfile"))
    return rc, rec


def test_runner_rechecks_before_mpirun(tree, capsys):
    src, root = _paths(tree, A, "to-b/secret")
    rc, rec = _run_tool(_env("dscan", [src], str(tree / "art"), DMS_JR_CONTAIN=json.dumps([[src, root]])))
    assert rc == 1 and not any("mpirun" in cmd for cmd in rec.ran)
    assert "DMS_EXEC_REASON=target_path_redirected" in capsys.readouterr().out
    assert json.loads(rec.writes[f"{tree / 'art'}/summary.json"]) == {"returncode": None, "files": None, "bytes": None}
    # 봉쇄 안 경로는 그대로 돈다, 키가 JSON 이 아니면 막는다(fail-closed), 키가 없으면 종전대로(PV 아닌 잡).
    ok, _ = _paths(tree, A, "data/x")
    rc, rec = _run_tool(_env("dsync", [ok, ok + "-copy"], str(tree / "art"),
                             DMS_JR_CONTAIN=json.dumps([[ok, root], [ok + "-copy", root]])))
    assert rc == 0 and any("mpirun" in cmd for cmd in rec.ran)
    rc, rec = _run_tool(_env("dscan", [ok], str(tree / "art"), DMS_JR_CONTAIN="not-json"))
    assert rc == 1 and not any("mpirun" in cmd for cmd in rec.ran)
    rc, rec = _run_tool(_env("dscan", [src], str(tree / "art")))
    assert rc == 0 and any("mpirun" in cmd for cmd in rec.ran)


def test_non_pv_jobs_are_byte_identical(tree):
    """PV 가 아닌 잡: 스크립트에 봉쇄 함수가 없고 인자·러너 env 도 그대로다(이 기능 이전과 같은 매니페스트)."""
    for spec in (_spec("scan", {"target": "/x/a", "storage": "s", "managed_root": "/x"}),
                 _spec("sync", {"source": "/x/a", "source_storage": "s", "destination": "/x/b",
                                "destination_storage": "s"})):
        script, args = _preflight_script(spec)
        assert "dms_contain" not in script
        assert "DMS_JR_CONTAIN" not in _launcher_env(spec)
    script, args = _preflight_script(_spec("scan", {"target": "/x/a", "storage": "s", "managed_root": "/x"}))
    assert args == ["/x/a"]
    pod = build_preflight_pod(_spec("scan", {"target": "/x/a", "storage": "s", "managed_root": "/x",
                                             "contain": {"target": "/x"}}), job_image="i", namespace="dms",
                              volumes=[], node="n1")
    assert "dms_contain" in " ".join(pod["spec"]["containers"][0]["command"])
