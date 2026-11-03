"""공용 스토리지 동작 프로브(deploy/shared-storage-probe.py, 2026-11-03 ③ -- 설계 v6 3-2 G1~G9 · 8-1 S7) 계약.

클러스터 없이 세 층으로 본다:
- 인자·형식: --context 필수(파드를 만드는 스크립트 -- current-context 금지), SHARED_DIR 형식(render.sh 와 같은 규칙), 이미지 출처.
- dry-run(실제 python3 + PATH 앞 kubectl 심): 모든 클러스터 호출이 --context 를 달고, 만들기·exec·삭제가 없고, 파드 매니페스트가
  hostPath <SHARED_DIR>/dms(type Directory)·toleration Exists·nodeName·root·cap CHOWN/FOWNER/DAC_READ_SEARCH 모양이다.
- 판정(파드 안 도우미를 **로컬에서 실제로** 돌린다 -- exec 를 바꿔치기, 도우미 코드는 stdin 으로): 같은 디렉터리 = 같은 스토리지면
  ①⑤⑥ PASS, 서로 다른 디렉터리면 ① 「다른 노드 파일이 안 보임」 FAIL(다수 쪽이 있으면 소수 쪽만), 포함 마운트가 '/' 면 「마운트
  없음」 FAIL(장치 번호 대조 -- 심링크 SHARED_DIR 는 PASS), G3 지연·단계 오류는 WARN(FAIL·모름 아님), FS 종류·옵션만으로는 FAIL 하지
  않음, 교차 노드 읽기는 재시도 뒤 판정, `.dms-shared-id` 가 전부 없으면 「센티널 없음(S0 미실행)」 FAIL(WARN = exit 3 은 G3 만),
  끝나면 .probe-<nonce> 를 지운다(스토리지마다 -- 프로브가 쓰기 못 하게 남긴 디렉터리도 되찾아서). 테스트는 root 가 아니라 ③ G4 는 「root 소유
  변경(squash)」 FAIL 로 나온다(root_squash 음성 대조와 같은 길) -- root 로 돌면 PASS 다. root 결과가 필요한 판정(남 소유 0700 통과,
  ID 매핑 분류)은 도우미 결과를 바꿔치기해 본다.
"""
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "deploy" / "shared-storage-probe.py"
_spec = importlib.util.spec_from_file_location("shared_storage_probe", SCRIPT)
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)

ROOT = os.geteuid() == 0
NODES_JSON = json.dumps({"items": [
    {"metadata": {"name": "cp1", "labels": {"node-role.kubernetes.io/control-plane": ""}},
     "status": {"conditions": [{"type": "Ready", "status": "True"}]}},
    {"metadata": {"name": "w1", "labels": {}}, "status": {"conditions": [{"type": "Ready", "status": "True"}]}},
]})


def _mountinfo(tmp_path, target="/dms_shared", fstype="nfs4", options="rw,relatime", root_dev="999:1",
               mount_dev="999:50", extra=()):
    # 장치 번호는 실제 장치와 겹치지 않는 값이 기본이다(도우미가 파드 디렉터리의 st_dev 를 대조한다 -- 안 맞으면 경로 판정).
    lines = [f"22 1 {root_dev} / / rw,relatime shared:1 - ext4 /dev/sda1 rw"]
    if target:
        lines.append(f"98 22 {mount_dev} / {target} {options} shared:60 - {fstype} 10.0.0.1:/data/dms_shared rw,vers=4.2")
    lines.extend(extra)
    path = tmp_path / "mountinfo"
    path.write_text("\n".join(lines) + "\n")
    return str(path)


def _dev(path) -> str:
    st = os.stat(path)
    return f"{os.major(st.st_dev)}:{os.minor(st.st_dev)}"


class LocalExec:
    """kubectl exec 대신 도우미를 이 호스트에서 돌린다. pods = {pod: 그 파드의 <SHARED_DIR>/dms 로컬 디렉터리}.
    override(pod, op, args) 가 dict 를 주면 그 JSON 을, (rc, out, err) 튜플을 주면 그대로(exec 실패 흉내) 돌려준다."""

    def __init__(self, dirs: dict, mountinfo: str, override=None):
        self.dirs, self.mountinfo, self.override = dirs, mountinfo, override or (lambda pod, op, args: None)
        self.calls = []

    def exec(self, pod, argv, timeout, stdin=None):
        # 도우미 코드는 stdin 으로만 간다(argv 에 실으면 exec URL 이 길어 프록시가 414 -- 2026-11-03 리뷰).
        assert argv[:2] == ["python3", "-"] and stdin == probe.HELPER
        assert probe.HELPER not in argv and sum(len(a) for a in argv) < 1000
        op, args = argv[2], argv[3:]
        self.calls.append((pod, op))
        fake = self.override(pod, op, args)
        if isinstance(fake, tuple):
            return fake
        if fake is not None:
            return 0, json.dumps(fake) + "\n", ""
        mapped = [self.dirs[pod] if a == probe.POD_DMS else self.mountinfo if a == probe.POD_MOUNTINFO else a
                  for a in args]
        r = subprocess.run([sys.executable, "-", op, *mapped], input=stdin, capture_output=True, text=True,
                           timeout=timeout)
        return r.returncode, r.stdout, r.stderr


def _dms(tmp_path, name="shared"):
    d = tmp_path / name / "dms"
    d.mkdir(parents=True)
    return str(d)


def _run(execer, pods, **kw):
    report = probe.Report(stream=io.StringIO())
    p = probe.Probe(execer, report, shared_dir="/dms_shared", nonce="0123456789abcdef",
                    retry_seconds=kw.pop("retry", 1.0), g3_rounds=kw.pop("rounds", 2))
    p.run(pods)
    return report


def _status(report, node, check_prefix):
    return [i["status"] for i in report.items if i["node"] == node and i["check"].startswith(check_prefix)]


# --- 인자·형식 -----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["", "dms_shared", "/", "/a/", "//a", "/a//b", "/a/./b", "/a/../b", "/a/.", "/a/..",
                                 "/a b", "/a|b", "/a&b", "/한글", "/a:b"])
def test_shared_dir_format_matches_the_render_rule(bad):
    assert probe.shared_dir_problem(bad)


@pytest.mark.parametrize("good", ["/dms_shared", "/srv/dms-shared.v2/x_y", "/mgmt_storage/dms"])
def test_well_formed_shared_dir(good):
    assert probe.shared_dir_problem(good) is None


class _NoCluster:
    def __init__(self, *_a):
        raise AssertionError("클러스터 객체를 만들기 전에 거부해야 한다")


@pytest.mark.parametrize("argv", [
    ["--shared-dir", "/dms_shared", "--image", "r/dms:d1"],                          # --context 없음
    ["--context", "c", "--image", "r/dms:d1"],                                       # SHARED_DIR 없음
    ["--context", "c", "--shared-dir", "dms_shared", "--image", "r/dms:d1"],         # 상대 경로
    ["--context", "c", "--shared-dir", "/dms_shared"],                               # 이미지 출처 없음
    ["--context", "c", "--shared-dir", "/dms_shared", "--image", "x", "--overlay", "y"],
    ["--context", "c", "--shared-dir", "/dms_shared", "--image", "x", "--g3-rounds", "0"],
    ["--bogus"],
])
def test_usage_errors_exit_2_before_touching_a_cluster(argv, monkeypatch):
    monkeypatch.delenv("KUBE_CONTEXT", raising=False)
    monkeypatch.delenv("SHARED_DIR", raising=False)
    assert probe.main(argv, kube_factory=_NoCluster) == 2


def test_result_and_exit_codes():
    rep = probe.Report(stream=io.StringIO())
    assert rep.result() == "UNKNOWN"                        # 아무것도 못 쟀다 = 모름(PASS 아님)
    rep.add("a", "x", "INFO", "")
    rep.add("a", "x", "PASS", "")
    assert rep.result() == "PASS"
    rep.add("b", "② G3", "WARN", "")
    assert rep.result() == "WARN"
    rep.add("c", "y", "UNKNOWN", "")
    assert rep.result() == "UNKNOWN"
    rep.add("d", "z", "FAIL", "")
    assert rep.result() == "FAIL"
    assert probe.EXIT == {"PASS": 0, "FAIL": 1, "WARN": 3, "UNKNOWN": 4}


# --- dry-run(실제 스크립트 + kubectl 심) --------------------------------------------------------------------------------

_SHIM = """#!/bin/sh
printf '%s\\n' "$*" >> '{log}'
if [ "$1" = "config" ] && [ "$2" = "get-contexts" ]; then [ "$3" = "known" ] && exit 0; exit 1; fi
case "$*" in *"get nodes -o json"*) cat '{nodes}'; exit 0;; esac
exit 0
"""


def _shim_env(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log, nodes = tmp_path / "kubectl.log", tmp_path / "nodes.json"
    nodes.write_text(NODES_JSON)
    shim = bindir / "kubectl"
    shim.write_text(_SHIM.format(log=log, nodes=nodes))
    shim.chmod(0o755)
    env = {"PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}", "HOME": str(tmp_path)}
    return env, log


def _script(env, *args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=60)


def test_dry_run_only_reads_nodes_with_the_named_context_and_prints_the_pod_shape(tmp_path):
    env, log = _shim_env(tmp_path)
    r = _script(env, "--context", "known", "--shared-dir", "/dms_shared", "--image", "pkg-01:5001/dms:d16",
                "--dry-run", "--json")
    assert r.returncode == 0, (r.stdout, r.stderr)
    calls = log.read_text().splitlines()
    cluster = [c for c in calls if not c.startswith("config get-contexts")]
    assert cluster == ["--context known get nodes -o json"], calls       # 만들기·exec·삭제 없음
    out = json.loads(r.stdout)
    assert out["result"] == "DRY-RUN"
    pods = out["manifest"]["items"]
    assert [p["spec"]["nodeName"] for p in pods] == ["cp1", "w1"]           # control-plane 포함
    for p in pods:
        spec = p["spec"]
        assert spec["tolerations"] == [{"operator": "Exists"}]
        vols = {v["name"]: v["hostPath"] for v in spec["volumes"]}
        assert vols["shared-dms"] == {"path": "/dms_shared/dms", "type": "Directory"}
        assert vols["host-mountinfo"] == {"path": "/proc/1/mountinfo", "type": "File"}
        c = spec["containers"][0]
        assert c["image"] == "pkg-01:5001/dms:d16" and c["command"][0] == "sleep"
        assert c["securityContext"]["runAsUser"] == 0
        assert c["securityContext"]["capabilities"] == {"drop": ["ALL"], "add": ["CHOWN", "FOWNER", "DAC_READ_SEARCH"]}
        assert spec["activeDeadlineSeconds"] > 0 and spec["restartPolicy"] == "Never"
        assert p["metadata"]["labels"][probe.NONCE_LABEL]
    assert "DirectoryOrCreate" not in r.stdout + SCRIPT.read_text()


def test_dry_run_with_explicit_nodes_touches_no_cluster(tmp_path):
    env, log = _shim_env(tmp_path)
    r = _script(env, "--context=known", "--shared-dir", "/dms_shared", "--image", "r/dms:d1", "--nodes", "a,b",
                "--dry-run")
    assert r.returncode == 0, r.stderr
    assert [c for c in log.read_text().splitlines() if not c.startswith("config get-contexts")] == []
    assert r.stdout.strip().splitlines()[-1] == "RESULT: DRY-RUN"


def test_unknown_context_stops_before_any_cluster_call(tmp_path):
    env, log = _shim_env(tmp_path)
    r = _script(env, "--context", "nope", "--shared-dir", "/dms_shared", "--image", "r/dms:d1", "--dry-run")
    assert r.returncode == 2 and "nope" in r.stderr
    assert [c for c in log.read_text().splitlines() if not c.startswith("config get-contexts")] == []


def test_context_comes_from_env_when_no_flag(tmp_path):
    env, log = _shim_env(tmp_path)
    r = _script({**env, "KUBE_CONTEXT": "known", "SHARED_DIR": "/dms_shared"}, "--image", "r/dms:d1", "--dry-run")
    assert r.returncode == 0, r.stderr
    assert "--context known get nodes -o json" in log.read_text()


@pytest.mark.skipif(shutil.which("kubectl") is None, reason="kubectl 없음")
def test_overlay_image_resolution_uses_the_rendered_dms_image():
    # 레지스트리는 커밋된 values.env 의 REGISTRY(오버레이 newName 과 같다 — 렌더러가 대조) — 2026-11-03 T4 16 에 pkg-01:5001 → 10.112.0.50:5000.
    tb = REPO_ROOT / "deploy" / "overlays" / "testbed-k8s"
    registry = re.search(r"(?m)^REGISTRY=(\S+)$", (tb / "values.env").read_text()).group(1)
    assert probe.resolve_image(str(tb)).startswith(f"{registry}/dms:")
    with pytest.raises(RuntimeError):                       # prod 템플릿은 미렌더(REPLACE_) -- --image 나 렌더본을 쓴다
        probe.resolve_image(str(REPO_ROOT / "deploy" / "overlays" / "prod"))


# --- 판정(도우미를 로컬에서 실제로) -------------------------------------------------------------------------------------

def test_same_storage_passes_g1_fsync_g9_and_cleans_up(tmp_path):
    d = _dms(tmp_path)
    Path(d, ".dms-shared-id").write_text("abc\n")
    report = _run(LocalExec({"p1": d, "p2": d}, _mountinfo(tmp_path)), {"n1": "p1", "n2": "p2"})
    for n in ("n1", "n2"):
        assert _status(report, n, "① G1 같은 스토리지") == ["INFO", "PASS"], report.items
        assert _status(report, n, "① G1 센티널") == ["PASS"]
        assert _status(report, n, "⑤ fsync") == ["PASS"]
        assert _status(report, n, "⑥ G9 POSIX") == ["PASS"]
        assert _status(report, n, "③ G4") == (["PASS"] if ROOT else ["FAIL"])
    assert _status(report, "n2", "② G3") == ["PASS"]
    assert _status(report, "n1+n2", "⑥ G9 O_EXCL") == ["PASS"]
    assert _status(report, "n1", "⑥ G9 rename") == ["PASS"]
    if not ROOT:
        g4 = [i["message"] for i in report.items if i["check"].startswith("③ G4")][0]
        assert "root 소유 변경(squash)" in g4 and "no_root_squash" in g4      # NFS 힌트(포함 마운트가 nfs4)
        assert report.result() == "FAIL"
    assert sorted(os.listdir(d)) == [".dms-shared-id"]                    # .probe-<nonce> 를 지웠다


def test_different_storage_on_each_node_fails_g1_with_the_visibility_reason(tmp_path):
    # 두 노드가 서로 다른 스토리지 -- 다수 쪽을 가릴 수 없으니 둘 다 FAIL.
    d1, d2 = _dms(tmp_path, "a"), _dms(tmp_path, "b")
    report = _run(LocalExec({"p1": d1, "p2": d2}, _mountinfo(tmp_path)), {"n1": "p1", "n2": "p2"}, retry=0.5)
    fails = [i for i in report.items if i["status"] == "FAIL" and i["check"] == "① G1 같은 스토리지"]
    assert [i["node"] for i in fails] == ["n1", "n2"], report.items
    assert all("다른 노드 파일이 안 보임" in f["message"] and "다수 쪽을 가릴 수 없다" in f["message"] for f in fails)
    assert report.result() == "FAIL"
    assert os.listdir(d1) == [] and os.listdir(d2) == []                  # 두 스토리지의 프로브 디렉터리를 다 지웠다


def test_sentinel_mismatch_fails_and_absence_everywhere_also_fails(tmp_path):
    d1, d2 = _dms(tmp_path, "a"), _dms(tmp_path, "b")
    Path(d1, ".dms-shared-id").write_text("x")
    Path(d2, ".dms-shared-id").write_text("y")
    report = _run(LocalExec({"p1": d1, "p2": d2}, _mountinfo(tmp_path)), {"n1": "p1", "n2": "p2"}, retry=0.5)
    assert set(_status(report, "n1", "① G1 센티널")) == {"FAIL"}
    # 전부 없음 = 「dms 는 있는데 센티널이 없음」(파드의 hostPath <SHARED_DIR>/dms 는 type Directory) = S0 미실행·미완료 -- WARN(exit 3,
    # 설치 계속)이 아니라 FAIL 이다(2026-11-03 리뷰; exit 3 은 G3 만). 비어 있는 센티널도 스토리지를 못 가려 FAIL.
    d = _dms(tmp_path, "c")
    report = _run(LocalExec({"p1": d, "p2": d}, _mountinfo(tmp_path)), {"n1": "p1", "n2": "p2"})
    assert _status(report, "-", "① G1 센티널") == ["FAIL"]
    msg = [i["message"] for i in report.items if i["check"].startswith("① G1 센티널")][0]
    assert "센티널 없음(S0 미실행)" in msg and "shared-dir-init.sh /dms_shared" in msg
    assert report.result() == "FAIL"
    Path(d, ".dms-shared-id").write_text("\n")
    report = _run(LocalExec({"p1": d, "p2": d}, _mountinfo(tmp_path)), {"n1": "p1", "n2": "p2"})
    assert _status(report, "n1", "① G1 센티널") == ["FAIL"] and _status(report, "n2", "① G1 센티널") == ["FAIL"]
    assert all("비어 있다" in i["message"] for i in report.items if i["check"].startswith("① G1 센티널"))


def test_no_mount_under_shared_dir_is_a_mount_missing_fail(tmp_path):
    d = _dms(tmp_path)
    report = _run(LocalExec({"p1": d}, _mountinfo(tmp_path, target=None)), {"n1": "p1"})
    assert _status(report, "n1", "① G1 같은 스토리지") == ["FAIL"]
    assert "마운트 없음" in report.items[0]["message"]
    assert os.listdir(d) == []                                            # 아무것도 쓰지 않았다


def test_only_the_odd_first_node_fails_when_the_others_share_storage(tmp_path):
    # 이름순 첫 노드(A)만 다른 스토리지 -- 예전처럼 A 가 디렉터리를 만들면 정상인 나머지가 FAIL 로 찍혔다(2026-11-03 리뷰).
    odd, shared = _dms(tmp_path, "odd"), _dms(tmp_path, "shared")
    report = _run(LocalExec({"p1": odd, "p2": shared, "p3": shared}, _mountinfo(tmp_path)),
                  {"n1": "p1", "n2": "p2", "n3": "p3"}, retry=0.5)
    assert _status(report, "n1", "① G1 같은 스토리지") == ["INFO", "FAIL"], report.items
    msg = [i["message"] for i in report.items if i["node"] == "n1" and i["status"] == "FAIL"][0]
    assert "n2, n3 가 쓴 파일" in msg and "다수 쪽(n2, n3)과 다른 스토리지" in msg and "이 노드뿐" in msg
    for n in ("n2", "n3"):
        assert _status(report, n, "① G1 같은 스토리지") == ["INFO", "PASS"], report.items
    # 이후 단계는 다수 쪽(A = n2)에서 돈다.
    assert _status(report, "n3", "② G3") == ["PASS"] and _status(report, "n1", "② G3") == []
    assert _status(report, "n2+n3", "⑥ G9 O_EXCL") == ["PASS"]
    assert os.listdir(odd) == [] and os.listdir(shared) == []             # 스토리지마다 지웠다


def test_symlinked_shared_dir_is_found_by_device_number_not_a_false_mount_missing(tmp_path):
    # SHARED_DIR=/dms_shared 가 심링크(-> /gpfs/fs1/shared)라 경로 문자열로는 '/' 가 잡힌다 -- 파드 디렉터리의 st_dev 가 /gpfs/fs1
    # 줄의 major:minor 와 맞으면 그 마운트다(findmnt -T 처럼 경로를 풀어 본 것과 같은 답).
    d = _dms(tmp_path)
    mi = _mountinfo(tmp_path, target=None, extra=[f"120 22 {_dev(d)} / /gpfs/fs1 rw,relatime - gpfs fs1 rw"])
    report = _run(LocalExec({"p1": d, "p2": d}, mi), {"n1": "p1", "n2": "p2"})
    info = [i for i in report.items if i["node"] == "n1" and i["check"] == "① G1 같은 스토리지" and i["status"] == "INFO"]
    assert info and "/gpfs/fs1" in info[0]["message"] and "심링크" in info[0]["message"], report.items
    assert _status(report, "n1", "① G1 같은 스토리지") == ["INFO", "PASS"]


def test_device_on_the_root_filesystem_is_mount_missing_even_if_the_path_looks_mounted(tmp_path):
    # 경로 문자열로는 /dms_shared 마운트 아래지만(예: <SHARED_DIR>/dms 가 로컬 디렉터리로의 심링크) 실제 장치는 루트 FS.
    d = _dms(tmp_path)
    mi = _mountinfo(tmp_path, root_dev=_dev(d))
    report = _run(LocalExec({"p1": d}, mi), {"n1": "p1"})
    assert _status(report, "n1", "① G1 같은 스토리지") == ["FAIL"]
    assert "마운트 없음" in report.items[0]["message"] and "루트 파일시스템" in report.items[0]["message"]
    assert os.listdir(d) == []


def _root_like(extra=None):
    """비 root 테스트에서 root 결과가 필요한 판정을 보려고 owner·owner_check 를 root 가 돌린 정상 결과로 바꿔치기한다."""
    good_owner = {"errors": {}, "root": [0, 0], "mode": "0o600", "traverse": True,
                  "id26": [26, 26], "id65532": [65532, 65532]}
    good_check = {"ok": True, "seen": {}, "traverse": True, "traverse_error": None, "waited": 0}

    def override(pod, op, args):
        if extra is not None:
            got = extra(pod, op, args)
            if got is not None:
                return got
        return {"owner": good_owner, "owner_check": good_check}.get(op)
    return override


def test_g4_requires_root_to_traverse_someone_elses_0700_directory(tmp_path):
    # root 가 만든 0700 은 소유자 비트로 늘 통과해 아무것도 재지 못한다 -- 26:26 0700 을 통과 못 하면(소유는 0 으로 남지만 root
    # 특권을 주지 않는 export) ③ FAIL. 다른 노드의 통과 실패도 ③ 이다.
    d = _dms(tmp_path)

    def same_node(pod, op, _args):
        if pod == "p1" and op == "owner":
            return {"errors": {"traverse": "EACCES"}, "root": [0, 0], "mode": "0o600",
                    "id26": [26, 26], "id65532": [65532, 65532]}
        return None
    report = _run(LocalExec({"p1": d, "p2": d}, _mountinfo(tmp_path), override=_root_like(same_node)),
                  {"n1": "p1", "n2": "p2"})
    g4 = [i for i in report.items if i["node"] == "n1" and i["check"].startswith("③ G4")]
    assert [i["status"] for i in g4] == ["FAIL"], report.items
    assert "남(26:26) 소유 0700" in g4[0]["message"] and "EACCES" in g4[0]["message"]
    assert "no_root_squash" in g4[0]["message"]                           # nfs4 힌트

    def other_node(pod, op, _args):
        if pod == "p2" and op == "owner_check":
            return {"ok": True, "seen": {}, "traverse": False, "traverse_error": "EACCES", "waited": 1}
        return None
    report = _run(LocalExec({"p1": d, "p2": d}, _mountinfo(tmp_path), override=_root_like(other_node)),
                  {"n1": "p1", "n2": "p2"})
    assert _status(report, "n1", "③ G4") == ["PASS"]
    assert _status(report, "n2", "③ G4") == ["PASS", "FAIL"], report.items
    assert _status(report, "n2", "④ G5") == ["PASS"]


def test_chown_numbers_changing_while_root_stays_0_is_id_mapping_not_squash(tmp_path):
    # NFSv4 idmap 도메인 불일치: root 는 0:0 으로 남는데 chown 26 이 nobody(65534)로 보인다 -- ③ squash 가 아니라 ④ 「ID 매핑」.
    d = _dms(tmp_path)

    def idmap(pod, op, _args):
        if pod == "p1" and op == "owner":
            return {"errors": {}, "root": [0, 0], "mode": "0o600", "traverse": True,
                    "id26": [65534, 65534], "id65532": [65534, 65534]}
        return None
    report = _run(LocalExec({"p1": d, "p2": d}, _mountinfo(tmp_path), override=_root_like(idmap)),
                  {"n1": "p1", "n2": "p2"})
    assert _status(report, "n1", "③ G4") == ["PASS"]
    g5 = [i for i in report.items if i["node"] == "n1" and i["check"].startswith("④ G5")]
    assert [i["status"] for i in g5] == ["FAIL"] and g5[0]["message"].startswith("ID 매핑 -- 같은 노드에서")
    assert "idmap" in g5[0]["message"] and "no_root_squash" not in g5[0]["message"]


def test_g3_step_errors_are_a_warn_not_unknown(tmp_path):
    # G3 는 필수가 아니다(#47) -- 단계 실행 오류(exec 일시 실패)가 결과를 「모름」(exit 4)으로 만들어 설치를 멈추면 안 된다.
    d = _dms(tmp_path)
    Path(d, ".dms-shared-id").write_text("abc\n")

    def flaky(pod, op, _args):
        if pod == "p2" and op == "g3_check":
            return 1, "", "error: unable to upgrade connection"
        return None
    report = _run(LocalExec({"p1": d, "p2": d}, _mountinfo(tmp_path), override=_root_like(flaky)),
                  {"n1": "p1", "n2": "p2"})
    g3 = [i for i in report.items if i["check"].startswith("② G3")]
    assert [i["status"] for i in g3] == ["WARN"] and "G3 확인 못 함" in g3[0]["message"]
    assert report.result() == "WARN", report.items


def test_excl_start_time_comes_from_pod_a_clock_and_the_wait_is_capped(tmp_path, monkeypatch):
    # 설치 호스트 시계가 아니라 A 파드의 시계로 시작 시각을 정한다. A 시계가 10분 앞서 있어도(= 다른 노드와 어긋남) 도우미는
    # EXCL_MAX_WAIT 이상 자지 않아 exec 시간 초과로 「모름」이 되지 않는다.
    monkeypatch.setattr(probe, "EXCL_MAX_WAIT", 0.5)
    d = _dms(tmp_path)

    def skewed(pod, op, _args):
        return {"now": time.time() + 600} if op == "now" else None
    execer = LocalExec({"p1": d, "p2": d}, _mountinfo(tmp_path), override=skewed)
    report = probe.Report(stream=io.StringIO())
    p = probe.Probe(execer, report, shared_dir="/dms_shared", nonce="0123456789abcdef", retry_seconds=1.0, g3_rounds=1)
    os.mkdir(os.path.join(d, p.probe), 0o700)
    t0 = time.monotonic()
    p.check_excl({"n1": "p1", "n2": "p2"}, ["n1", "n2"])
    assert time.monotonic() - t0 < 10
    assert ("p1", "now") in execer.calls and ("p2", "now") not in execer.calls
    assert [i["status"] for i in report.items] == ["PASS"], report.items


def test_cleanup_failure_is_reported_with_the_path_to_remove(tmp_path, capsys):
    d = _dms(tmp_path)

    def stuck(pod, op, _args):
        return {"ok": False, "error": "EBUSY"} if op == "cleanup" else None
    report = _run(LocalExec({"p1": d, "p2": d}, _mountinfo(tmp_path), override=_root_like(stuck)),
                  {"n1": "p1", "n2": "p2"})
    info = [i for i in report.items if i["check"] == "정리"]
    assert len(info) == 1 and info[0]["status"] == "INFO" and info[0]["node"] == "n1,n2"
    assert "rm -rf /dms_shared/dms/.probe-0123456789abcdef" in info[0]["message"] and "EBUSY" in info[0]["message"]
    assert "주의:" in capsys.readouterr().err


def _helper(op, *args):
    r = subprocess.run([sys.executable, "-", op, *args], input=probe.HELPER, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_cleanup_reclaims_directories_it_cannot_write_and_never_follows_links(tmp_path):
    # ③ 이 남긴 dir-<tag> 는 26:26 0700 이다 -- DAC_OVERRIDE 없는 프로브 파드는 그 안의 파일을 못 지워, 권한을 로컬에서 판정하는
    # FS(CephFS 커널·GPFS·Lustre)에서 매번 .probe-<nonce> 가 남았다(2026-11-03 리뷰; NFS no_root_squash 는 서버가 판정해 숨었다).
    # 비 root 테스트에서는 같은 모양을 「쓰기 불가(0500) 디렉터리 + 안의 파일」(중첩 포함)로 만든다. 프로브 디렉터리 밖을 가리키는
    # 링크는 따라가지 않는다(밖의 0500 디렉터리는 모드·내용 그대로).
    d = _dms(tmp_path)
    p = Path(d, ".probe-0123456789abcdef")
    p.mkdir(mode=0o700)
    sub, deep = p / "dir-t0", p / "dir-t0" / "deeper"
    deep.mkdir(parents=True)
    (sub / "inner").write_text("y")
    (deep / "f").write_text("z")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("k")
    os.symlink(outside, p / "ln-out")
    for path in (deep, sub, outside):
        path.chmod(0o500)
    try:
        assert _helper("cleanup", d, p.name) == {"ok": True}
        assert os.listdir(d) == []
        assert (outside / "keep").read_text() == "k" and outside.stat().st_mode & 0o777 == 0o500
    finally:
        for path in (outside, sub, deep):
            if path.exists():
                path.chmod(0o700)


def test_parallel_interrupt_kills_running_kubectl_and_reraises_quickly():
    # SIGTERM(KeyboardInterrupt) 중에 스레드 풀이 진행 중 exec 의 시간 초과(최대 수 분)를 기다리지 않는다.
    release = threading.Event()

    class Blocking:
        interrupted = False

        def exec(self, pod, argv, timeout, stdin=None):
            if pod == "boom":
                raise KeyboardInterrupt("signal 15")
            release.wait(30)
            return 137, "", "killed"

        def interrupt(self):
            Blocking.interrupted = True
            release.set()

    p = probe.Probe(Blocking(), probe.Report(stream=io.StringIO()), shared_dir="/dms_shared", nonce="0123456789abcdef",
                    retry_seconds=1.0, g3_rounds=1)
    t0 = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        p.parallel({"a": ("boom", "now"), "b": ("slow", "now")})
    assert Blocking.interrupted and time.monotonic() - t0 < 10


def test_kube_interrupt_kills_a_running_kubectl():
    kube = probe.Kube("c", "dms", kubectl="sh")
    threading.Timer(0.5, kube.interrupt).start()
    t0 = time.monotonic()
    r = kube._run(["-c", "sleep 30"], cluster=False, timeout=60)
    assert r.returncode != 0 and time.monotonic() - t0 < 10


_EXEC_SHIM = """#!/bin/sh
printf '%s\\n' "$*" >> '{log}'
cat > '{data}'
echo '{{"items": []}}'
"""


def test_kube_exec_sends_the_helper_on_stdin_and_events_are_narrowed(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log, data = tmp_path / "argv.log", tmp_path / "stdin.bin"
    shim = bindir / "kubectl"
    shim.write_text(_EXEC_SHIM.format(log=log, data=data))
    shim.chmod(0o755)
    kube = probe.Kube("known", "dms", kubectl=str(shim))
    rc, _out, _err = kube.exec("pod-1", ["python3", "-", "now"], 30, stdin=probe.HELPER)
    assert rc == 0
    assert data.read_text() == probe.HELPER
    assert log.read_text().splitlines()[0] == "--context known -n dms exec -i pod-1 -c probe -- python3 - now"
    kube.events()
    assert "involvedObject.kind=Pod,reason=FailedMount" in log.read_text().splitlines()[1]


def test_overlay_values_env_supplies_shared_dir_when_no_flag_or_env(tmp_path, monkeypatch, capsys):
    # testbed-k8s 의 커밋된 values.env(SHARED_DIR=/dms_shared)를 --overlay 로 읽는다. 플래그·env 가 이긴다.
    monkeypatch.delenv("SHARED_DIR", raising=False)
    monkeypatch.setattr(probe, "resolve_image", lambda overlay: "r/dms:d1")

    class Ctx:
        def __init__(self, *_a):
            pass

        def context_exists(self):
            return True

    overlay = str(REPO_ROOT / "deploy" / "overlays" / "testbed-k8s")
    assert probe.overlay_shared_dir(overlay) == "/dms_shared"
    rc = probe.main(["--context", "c", "--overlay", overlay, "--nodes", "a", "--dry-run", "--json"], kube_factory=Ctx)
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["manifest"]["items"][0]["spec"]["volumes"][0]["hostPath"]["path"] == "/dms_shared/dms"
    rc = probe.main(["--context", "c", "--overlay", overlay, "--shared-dir", "/other", "--nodes", "a", "--dry-run",
                     "--json"], kube_factory=Ctx)
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["manifest"]["items"][0]["spec"]["volumes"][0]["hostPath"]["path"] == "/other/dms"
    empty = tmp_path / "ovl"
    empty.mkdir()
    assert probe.main(["--context", "c", "--overlay", str(empty), "--dry-run"], kube_factory=Ctx) == 2


def test_fs_type_and_options_alone_never_fail(tmp_path):
    # 설계: 판정은 동작으로만 -- 낯선 FS 종류·lookupcache=all 같은 옵션은 보고(INFO)일 뿐이다.
    d = _dms(tmp_path)
    Path(d, ".dms-shared-id").write_text("abc\n")                         # S0 뒤(센티널 없음은 그 자체로 FAIL)
    mi = _mountinfo(tmp_path, fstype="weirdfs", options="rw,lookupcache=all,nocto")
    report = _run(LocalExec({"p1": d, "p2": d}, mi), {"n1": "p1", "n2": "p2"})
    mount = [i for i in report.items if i["check"].startswith("① G1 같은 스토리지") and i["status"] == "INFO"]
    assert mount and "weirdfs" in mount[0]["message"]
    assert not [i for i in report.items if i["status"] == "FAIL" and not i["check"].startswith(("③", "④"))]


def test_g3_delay_is_a_warn_with_impact_not_a_fail(tmp_path):
    d = _dms(tmp_path)
    Path(d, ".dms-shared-id").write_text("abc\n")

    def lagging(pod, op, _args):
        if pod == "p2" and op == "g3_check":
            return {"new_ok": False, "repl_ok": True, "new_at": None, "repl_at": 0.01}
        return None

    report = _run(LocalExec({"p1": d, "p2": d}, _mountinfo(tmp_path), override=lagging), {"n1": "p1", "n2": "p2"},
                  rounds=3)
    g3 = [i for i in report.items if i["check"].startswith("② G3")]
    assert [i["status"] for i in g3] == ["WARN"]
    assert "3회 중 새 이름 3회" in g3[0]["message"] and "ENOENT" in g3[0]["message"]
    assert "lookupcache=positive" in g3[0]["message"]                      # nfs4 힌트
    assert not [i for i in report.items if i["check"].startswith(("①", "⑤", "⑥")) and i["status"] == "FAIL"]
    if ROOT:
        assert report.result() == "WARN"                                  # G3 지연만 = exit 3(설치 계속)


def test_cross_node_reads_retry_before_judging(tmp_path):
    # 교차 노드 원칙: 못 보면 바로 FAIL 이 아니라 retry 초까지 다시 본다(캐시 지연을 G1 실패로 오판하지 않게).
    d = _dms(tmp_path)
    os.mkdir(os.path.join(d, ".probe-0123456789abcdef"), 0o700)
    target = os.path.join(d, ".probe-0123456789abcdef", "late")
    threading.Timer(1.0, lambda: Path(target).write_text("v")).start()
    t0 = time.monotonic()
    r = subprocess.run([sys.executable, "-c", probe.HELPER, "read_many", d, ".probe-0123456789abcdef", "5", "late=v"],
                       capture_output=True, text=True, timeout=30)
    res = json.loads(r.stdout.strip().splitlines()[-1])
    assert res["ok"] is True and res["seen"] == {"late": True}
    assert 0.9 <= time.monotonic() - t0 < 5


def test_helper_refuses_a_probe_name_outside_the_nonce_shape(tmp_path):
    d = _dms(tmp_path)
    r = subprocess.run([sys.executable, "-c", probe.HELPER, "cleanup", d, ".."], capture_output=True, text=True,
                       timeout=30)
    assert r.returncode != 0 and os.path.isdir(d)


# --- main 전체 흐름(가짜 클러스터) ---------------------------------------------------------------------------------------

class FakeCluster(LocalExec):
    """create·pods·events·delete 를 흉내 낸다. bad = {node: "mount"|"pull"} -- 그 노드 파드는 Running 이 되지 않는다."""

    instances = []

    def __init__(self, context, namespace, *, dirs, mountinfo, bad=None):
        super().__init__({}, mountinfo)
        self.node_dirs, self.bad, self.created, self.deleted = dirs, bad or {}, [], []
        FakeCluster.instances.append(self)

    def context_exists(self):
        return True

    def nodes(self):
        return [{"name": n, "ready": True, "control_plane": n == "cp1"} for n in sorted(self.node_dirs)]

    def create(self, manifest):
        for item in manifest["items"]:
            self.created.append(item)
            self.dirs[item["metadata"]["name"]] = self.node_dirs[item["spec"]["nodeName"]]

    def pods(self, nonce):
        out = []
        for item in self.created:
            node = item["spec"]["nodeName"]
            if self.bad.get(node) == "mount":
                st = {"phase": "Pending", "containerStatuses": []}
            elif self.bad.get(node) == "pull":
                st = {"phase": "Pending", "containerStatuses": [{"state": {"waiting": {"reason": "ImagePullBackOff"}}}]}
            else:
                st = {"phase": "Running", "containerStatuses": [{"state": {"running": {}}}]}
            out.append({"metadata": item["metadata"], "status": st})
        return out

    def events(self):
        return [{"involvedObject": {"name": i["metadata"]["name"]}, "reason": "FailedMount",
                 "message": "hostPath type check failed: /dms_shared/dms is not a directory"}
                for i in self.created if self.bad.get(i["spec"]["nodeName"]) == "mount"]

    def delete(self, nonce):
        self.deleted.append(nonce)
        return getattr(self, "delete_ok", None)


def _main(tmp_path, dirs, bad=None, *extra):
    mi = _mountinfo(tmp_path)

    def factory(context, namespace):
        return FakeCluster(context, namespace, dirs=dirs, mountinfo=mi, bad=bad)

    rc = probe.main(["--context", "c", "--shared-dir", "/dms_shared", "--image", "r/dms:d1", "--retry-seconds", "0.5",
                     "--g3-rounds", "1", "--start-timeout", "3", "--json", *extra], kube_factory=factory)
    return rc, FakeCluster.instances[-1]


def test_main_runs_end_to_end_deletes_pods_and_exits_with_the_verdict(tmp_path, capsys):
    d = _dms(tmp_path)
    Path(d, ".dms-shared-id").write_text("abc\n")
    rc, cluster = _main(tmp_path, {"cp1": d, "w1": d})
    out = json.loads(capsys.readouterr().out)
    assert rc == probe.EXIT[out["result"]]
    assert out["result"] == ("PASS" if ROOT else "FAIL")     # 비 root 테스트는 ③ G4 만 FAIL(아래)
    assert {i["status"] for i in out["items"] if i["status"] == "FAIL"} <= ({"FAIL"} if not ROOT else set())
    assert not [i for i in out["items"] if i["status"] == "FAIL" and not i["check"].startswith("③")]
    assert cluster.deleted == [out["nonce"]] and len(cluster.created) == 2
    assert os.listdir(d) == [".dms-shared-id"]                            # .probe-<nonce> 만 지웠다


def test_main_reports_a_failed_pod_delete_with_the_command(tmp_path, capsys, monkeypatch):
    d = _dms(tmp_path)
    monkeypatch.setattr(FakeCluster, "delete_ok", False, raising=False)
    rc, cluster = _main(tmp_path, {"cp1": d})
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    info = [i for i in out["items"] if i["check"] == "정리"]
    assert info and f"-l {probe.NONCE_LABEL}={out['nonce']}" in info[0]["message"]
    assert "kubectl --context c -n dms delete pod" in captured.err


def test_main_reports_missing_mount_as_fail_and_pull_failure_as_unknown(tmp_path, capsys):
    d = _dms(tmp_path)
    rc, cluster = _main(tmp_path, {"cp1": d, "w1": d, "w2": d}, {"w1": "mount", "w2": "pull"})
    out = json.loads(capsys.readouterr().out)
    by_node = {}
    for i in out["items"]:
        by_node.setdefault(i["node"], []).append(i)
    assert [i["status"] for i in by_node["w1"]] == ["FAIL"] and "마운트 없음" in by_node["w1"][0]["message"]
    assert [i["status"] for i in by_node["w2"]] == ["UNKNOWN"] and "pull" in by_node["w2"][0]["message"]
    assert rc == 1 and out["result"] == "FAIL"
    assert cluster.deleted == [out["nonce"]]
