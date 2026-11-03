"""SELinux 노드 프로브(deploy/selinux-probe.py, 2026-11-03 ⑦ — 설계 v6 2-3 「SELinux 전제」·8-1 「S2b 세부」·9 테스트 ⑦) 계약.

클러스터 없이 세 층으로 본다:
- 판정 행렬(judge — 순수 함수): `1` → FAIL, `0` + config enforcing → FAIL(재부팅하면 enforcing), `0` → PASS, selinuxfs 없음 + cgroup 보임 →
  PASS, 읽기 오류·cgroup 안 보임 → 모름, DMS 밖 노드는 정보. 부팅 인자(selinux=0·enforcing=)는 libselinux 우선순위대로 config 보다 앞선다.
- 파드 안 스크립트를 **실제로**(dash) 가짜 호스트 트리에 돌려 judge 와 맞물리는지 — 부팅 인자는 selinux=·enforcing= 낱말만 찍는다.
- main(가짜 클러스터): privileged·Never·hostPath /sys/fs·/etc(읽기 전용 — /etc/selinux 직접 금지)·toleration Exists 모양, 판정은 DMS 노드만
  (pg-path-probe.dms_nodes 와 같은 정의), 끝나면(실패·중단에도) DaemonSet 삭제, 종료 코드 0/1/4/2, dry-run 은 읽기만(kubectl 심 — 모든
  호출에 --context).
"""
import ast
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "deploy" / "selinux-probe.py"
_spec = importlib.util.spec_from_file_location("selinux_probe", SCRIPT)
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)
_pg_spec = importlib.util.spec_from_file_location("pg_path_probe_for_selinux", REPO_ROOT / "deploy" / "pg-path-probe.py")
pg_probe = importlib.util.module_from_spec(_pg_spec)
_pg_spec.loader.exec_module(pg_probe)

ROOT = os.geteuid() == 0
CILIUM = "quay.io/cilium/cilium:v1.19.4@sha256:" + "a" * 64


def test_script_is_python39_syntax():
    ast.parse(SCRIPT.read_text(encoding="utf-8"), feature_version=(3, 9))


def test_pod_script_is_posix_sh():
    sh = shutil.which("dash") or shutil.which("sh")
    r = subprocess.run([sh, "-n"], input=probe.POD_SCRIPT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


# --- 판정 행렬 ------------------------------------------------------------------------------------------------------------

def _raw(**kw):
    base = {"done": True, "cgroup": "visible", "etc": "visible", "cmdline": "", "config_selinux": []}
    base.update(kw)
    return base


@pytest.mark.parametrize("raw, status, code", [
    # 설계 8-1 「S2b 세부」 행렬
    (_raw(selinuxfs="present", enforce="1", config="present", config_selinux=["enforcing"]), "FAIL", "enforcing"),
    (_raw(selinuxfs="present", enforce="1", config="present", config_selinux=["permissive"]), "FAIL", "enforcing"),
    (_raw(selinuxfs="present", enforce="0", config="present", config_selinux=["enforcing"]), "FAIL", "reboot-enforcing"),
    (_raw(selinuxfs="present", enforce="0", config="present", config_selinux=["permissive"]), "PASS", "permissive"),
    (_raw(selinuxfs="present", enforce="0", config="present", config_selinux=["disabled"]), "PASS", "permissive"),
    (_raw(selinuxfs="present", enforce="0", config="absent"), "PASS", "permissive"),
    (_raw(selinuxfs="absent"), "PASS", "no-selinux"),
    (_raw(selinuxfs="empty"), "PASS", "no-selinux"),
    # 부팅 인자가 config 보다 앞선다(libselinux selinux_init_load_policy) — selinux=0·enforcing=0 이면 config enforcing 이어도 PASS
    (_raw(selinuxfs="present", enforce="0", config="present", config_selinux=["enforcing"], cmdline="enforcing=0"), "PASS", "permissive"),
    (_raw(selinuxfs="present", enforce="0", config="present", config_selinux=["enforcing"], cmdline="selinux=0"), "PASS", "permissive"),
    # 확장: 부팅 인자 enforcing=1 은 config 가 permissive 여도 재부팅하면 enforcing(마지막 값이 이긴다)
    (_raw(selinuxfs="present", enforce="0", config="present", config_selinux=["permissive"], cmdline="enforcing=1"), "FAIL",
     "reboot-enforcing"),
    (_raw(selinuxfs="present", enforce="0", config="present", config_selinux=["enforcing"], cmdline="enforcing=1 enforcing=0"), "PASS",
     "permissive"),
    # 모름 — 읽기 오류·cgroup 안 보임·출력 미완
    (_raw(selinuxfs="present", enforce_error="cat: /host/sys/fs/selinux/enforce: Permission denied"), "UNKNOWN", "read-error"),
    (_raw(selinuxfs_error="ls: cannot open directory: Permission denied"), "UNKNOWN", "read-error"),
    (_raw(selinuxfs="absent", cgroup=None, cgroup_error="ls: cannot access '/host/sys/fs/cgroup': No such file"), "UNKNOWN",
     "rbind-unverified"),
    (_raw(selinuxfs="empty", cgroup="empty"), "UNKNOWN", "rbind-unverified"),
    (_raw(selinuxfs="present", enforce="0", config=None, config_error="읽을 수 없다"), "UNKNOWN", "read-error"),
    (_raw(selinuxfs="present", enforce="0", etc="missing", config="absent"), "UNKNOWN", "read-error"),
    (_raw(selinuxfs="present", enforce="0", config="present", config_selinux=["enforcing"], cmdline=None,
          cmdline_error="Permission denied"), "UNKNOWN", "read-error"),
    (_raw(selinuxfs="present", enforce="2"), "UNKNOWN", "read-error"),
    ({"done": False, "selinuxfs": "absent", "cgroup": "visible"}, "UNKNOWN", "incomplete"),
])
def test_judge_matrix(raw, status, code):
    raw = {k: v for k, v in raw.items() if v is not None}
    got = probe.judge(raw)
    assert (got["status"], got["code"]) == (status, code), got
    assert got["message"]


def test_fail_messages_name_the_two_reasons():
    assert "enforcing" in probe.judge(_raw(selinuxfs="present", enforce="1"))["message"]
    msg = probe.judge(_raw(selinuxfs="present", enforce="0", config="present", config_selinux=["enforcing"]))["message"]
    assert "재부팅하면 enforcing" in msg


def test_fail_guide_is_the_design_2_3_text():
    for needle in ("SELINUX=permissive", "setenforce 0", "grubby --update-kernel ALL --args selinux=0"):
        assert needle in probe.FAIL_GUIDE
    for needle in ("getenforce", "grep ^SELINUX= /etc/selinux/config"):
        assert needle in probe.UNKNOWN_GUIDE


@pytest.mark.parametrize("values, mode", [
    (["enforcing"], "enforcing"),
    (["Enforcing"], "enforcing"),                     # strncasecmp
    (["enforcing  # 주석"], "enforcing"),              # 앞쪽 일치(libselinux 도 접두만 본다)
    (["bogus", "permissive"], "permissive"),          # 모르는 값은 건너뛰고 다음 줄
    (["permissive", "enforcing"], "permissive"),      # 첫 유효 줄이 이긴다
    (['"enforcing"'], None),                          # 따옴표는 libselinux 도 벗기지 않는다 → 무시
    ([], None),
])
def test_config_mode_follows_libselinux(values, mode):
    assert probe.config_mode(values) == mode


def test_cmdline_last_value_wins_and_atoi():
    assert probe.cmdline_args("enforcing=1 enforcing=0") == {"selinux": None, "enforcing": 0}
    assert probe.cmdline_args("selinux=0") == {"selinux": 0, "enforcing": None}
    assert probe.cmdline_args("enforcing=abc") == {"selinux": None, "enforcing": 0}
    assert probe.cmdline_args("") == {"selinux": None, "enforcing": None}


def test_verdict_counts_only_judged_nodes():
    r = lambda s: {"status": s}  # noqa: E731
    assert probe.verdict([]) == "UNKNOWN"                                  # 아무것도 못 쟀다 = 모름
    assert probe.verdict([r("INFO")]) == "UNKNOWN"
    assert probe.verdict([r("PASS"), r("INFO")]) == "PASS"
    assert probe.verdict([r("PASS"), r("UNKNOWN")]) == "UNKNOWN"
    assert probe.verdict([r("UNKNOWN"), r("FAIL")]) == "FAIL"
    assert probe.EXIT == {"PASS": 0, "FAIL": 1, "UNKNOWN": 4}


def test_parse_logs_keeps_the_last_run_and_needs_done():
    text = "SEPROBE begin\nSEPROBE selinuxfs=present\nSEPROBE enforce=1\nSEPROBE done\nSEPROBE begin\nSEPROBE selinuxfs=absent\n"
    got = probe.parse_logs(text)
    assert got["selinuxfs"] == "absent" and "enforce" not in got and not got["done"]
    got = probe.parse_logs("noise\r\nSEPROBE begin\r\nSEPROBE config_selinux=enforcing\r\nSEPROBE config_selinux=x\r\nSEPROBE done\r\n")
    assert got["done"] and got["config_selinux"] == ["enforcing", "x"]


# --- 파드 스크립트를 실제로(가짜 호스트 트리) ------------------------------------------------------------------------------

def _host(tmp_path, *, selinuxfs=None, enforce=None, config=None, cgroup=True, etc=True):
    host = tmp_path / "host"
    (host / "sys" / "fs").mkdir(parents=True)
    if cgroup:
        (host / "sys" / "fs" / "cgroup").mkdir()
        (host / "sys" / "fs" / "cgroup" / "cgroup.controllers").write_text("cpu memory\n")
    if selinuxfs is not None:
        d = host / "sys" / "fs" / "selinux"
        d.mkdir()
        if selinuxfs == "present":
            (d / "enforce").write_text(enforce)
    (host / "etc").mkdir()
    if etc:
        (host / "etc" / "passwd").write_text("root:x:0:0::/root:/bin/sh\n")
    if config is not None:
        (host / "etc" / "selinux").mkdir()
        (host / "etc" / "selinux" / "config").write_text(config)
    return host


def _run_pod_script(tmp_path, host, cmdline="BOOT_IMAGE=/vmlinuz root=UUID=1234 ro quiet secretpw=hunter2\n"):
    cmd = tmp_path / "cmdline"
    cmd.write_text(cmdline)
    script = probe.POD_SCRIPT.replace("exec sleep 86400", ":")
    assert script != probe.POD_SCRIPT
    sh = shutil.which("dash") or shutil.which("sh")
    r = subprocess.run([sh, "-c", script], capture_output=True, text=True, timeout=30,
                       env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "SEPROBE_HOST": str(host), "SEPROBE_CMDLINE": str(cmd)})
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_pod_script_ubuntu_like_host_passes(tmp_path):
    out = _run_pod_script(tmp_path, _host(tmp_path))
    assert "secretpw" not in out and "UUID" not in out and "BOOT_IMAGE" not in out     # 부팅 인자는 selinux=·enforcing= 만
    got = probe.judge(probe.parse_logs(out))
    assert (got["status"], got["code"]) == ("PASS", "no-selinux"), (out, got)


def test_pod_script_rocky_permissive_passes_and_setenforce_only_fails(tmp_path):
    host = _host(tmp_path, selinuxfs="present", enforce="0", config="# comment\nSELINUX=permissive\nSELINUXTYPE=targeted\n")
    assert probe.judge(probe.parse_logs(_run_pod_script(tmp_path, host)))["status"] == "PASS"
    (host / "etc" / "selinux" / "config").write_text("SELINUX=enforcing\nSELINUXTYPE=targeted\n")
    got = probe.judge(probe.parse_logs(_run_pod_script(tmp_path, host)))
    assert (got["status"], got["code"]) == ("FAIL", "reboot-enforcing")
    got = probe.judge(probe.parse_logs(_run_pod_script(tmp_path, host, cmdline="ro enforcing=0 quiet\n")))
    assert got["status"] == "PASS"


def test_pod_script_enforcing_host_fails(tmp_path):
    host = _host(tmp_path, selinuxfs="present", enforce="1", config="SELINUX=enforcing\n")
    got = probe.judge(probe.parse_logs(_run_pod_script(tmp_path, host)))
    assert (got["status"], got["code"]) == ("FAIL", "enforcing")


def test_pod_script_empty_selinuxfs_without_cgroup_is_unknown(tmp_path):
    host = _host(tmp_path, selinuxfs="empty", cgroup=False)
    got = probe.judge(probe.parse_logs(_run_pod_script(tmp_path, host)))
    assert (got["status"], got["code"]) == ("UNKNOWN", "rbind-unverified")


@pytest.mark.skipif(ROOT, reason="root 는 모드 000 파일도 읽는다")
def test_pod_script_unreadable_enforce_is_unknown_not_pass(tmp_path):
    host = _host(tmp_path, selinuxfs="present", enforce="1")
    (host / "sys" / "fs" / "selinux" / "enforce").chmod(0)
    try:
        got = probe.judge(probe.parse_logs(_run_pod_script(tmp_path, host)))
    finally:
        (host / "sys" / "fs" / "selinux" / "enforce").chmod(0o644)
    assert (got["status"], got["code"]) == ("UNKNOWN", "read-error")


# --- DMS 노드 정의(pg-path-probe 와 하나) ----------------------------------------------------------------------------------

def _node(name, *, cp=False, taints=(), ready=True, os_image="Ubuntu 24.04.4 LTS"):
    labels = {"node-role.kubernetes.io/control-plane": ""} if cp else {}
    return {"metadata": {"name": name, "labels": labels}, "spec": {"taints": list(taints)},
            "status": {"conditions": [{"type": "Ready", "status": "True" if ready else "False"}], "nodeInfo": {"osImage": os_image}}}


NODES = [
    _node("cp1", cp=True, taints=[{"key": "node-role.kubernetes.io/control-plane", "effect": "NoSchedule"}]),
    _node("w1", os_image="Rocky Linux 9.6 (Blue Onyx)"),
    _node("w2", taints=[{"key": "node.kubernetes.io/unschedulable", "effect": "NoSchedule"}]),     # cordon — 여전히 DMS 노드
    _node("gpu1", taints=[{"key": "team", "value": "ml", "effect": "NoSchedule"}]),                # 다른 팀 — 판정 밖
]


def test_dms_nodes_is_the_pg_path_probe_definition():
    assert probe.dms_nodes(NODES) == pg_probe.dms_nodes(NODES) == (["cp1", "w1", "w2"], ["gpu1"])


# --- main(가짜 클러스터) ---------------------------------------------------------------------------------------------------

def _log(**kw):
    lines = ["SEPROBE begin"] + [f"SEPROBE {k}={v}" for k, v in kw.items()] + ["SEPROBE done"]
    return "\n".join(lines) + "\n"


UBUNTU = _log(cgroup="visible", selinuxfs="absent", etc="visible", config="absent", cmdline="")
ROCKY_OK = _log(cgroup="visible", selinuxfs="present", enforce="0", etc="visible", config="present", config_selinux="permissive",
                cmdline="")
ROCKY_REBOOT = _log(cgroup="visible", selinuxfs="present", enforce="0", etc="visible", config="present", config_selinux="enforcing",
                    cmdline="")
ENFORCING = _log(cgroup="visible", selinuxfs="present", enforce="1", etc="visible", config="present", config_selinux="enforcing",
                 cmdline="")


class FakeCluster:
    instances = []

    def __init__(self, context, namespace, *, nodes=NODES, logs=None, waiting=None, cilium=True, ns_labels=None,
                 leftovers=(), interrupt=False, create_error=None):
        self.context, self.namespace = context, namespace
        self.node_items, self.node_logs, self.waiting = nodes, logs or {}, waiting or {}
        self.cilium, self.ns_labels, self.left = cilium, ns_labels, list(leftovers)
        self.interrupt, self.create_error = interrupt, create_error
        self.created, self.deleted, self.calls = [], [], []
        FakeCluster.instances.append(self)

    def context_exists(self):
        return self.context == "known"

    def nodes(self):
        self.calls.append("nodes")
        return self.node_items

    def namespace_labels(self):
        self.calls.append("ns")
        return {"pod-security.kubernetes.io/enforce": "privileged"} if self.ns_labels is None else self.ns_labels

    def cilium_daemonsets(self):
        self.calls.append("cilium")
        if not self.cilium:
            return {"items": []}
        return {"items": [{"metadata": {"name": "cilium"}, "spec": {"template": {"spec": {"containers": [
            {"name": "cilium-agent", "image": CILIUM}]}}}}]}

    def leftovers(self):
        return list(self.left)

    def create(self, manifest):
        self.calls.append("create")
        if self.create_error:
            raise RuntimeError(self.create_error)
        self.created.append(manifest)

    def pods(self, nonce):
        if self.interrupt:
            self.interrupt = False                     # 한 번만(정리 중 pods 는 정상)
            raise KeyboardInterrupt
        if not self.created or [r for r in self.deleted if r.startswith("daemonset/")]:     # 우리 DS 를 지웠으면(옛 프로브 삭제는 별개)
            return []
        out = []
        for item in self.node_items:
            name = item["metadata"]["name"]
            if name in self.waiting:
                st = {"phase": "Pending", "containerStatuses": [{"state": {"waiting": {"reason": self.waiting[name], "message": "x"}}}]}
            elif name in self.node_logs:
                st = {"phase": "Running", "containerStatuses": [{"state": {"running": {}}}]}
            else:
                continue
            out.append({"metadata": {"name": f"probe-{name}"}, "spec": {"nodeName": name}, "status": st})
        return out

    def logs(self, pod):
        return self.node_logs.get(pod[len("probe-"):], "")

    def events(self, kind, name):
        return []

    def delete(self, ref):
        self.deleted.append(ref)
        return True


def _main(argv, **kw):
    FakeCluster.instances = []
    rc = probe.main(["--context", "known", "--json", *argv], kube_factory=lambda c, n: FakeCluster(c, n, **kw))
    return rc, FakeCluster.instances[-1]


def test_main_all_pass_creates_the_designed_daemonset_and_deletes_it(capsys):
    rc, cluster = _main([], logs={"cp1": UBUNTU, "w1": ROCKY_OK, "w2": UBUNTU, "gpu1": UBUNTU})
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["result"] == "PASS"
    assert {n["node"]: n["status"] for n in out["nodes"]} == {"cp1": "PASS", "w1": "PASS", "w2": "PASS", "gpu1": "INFO"}
    [ds] = cluster.created
    assert ds["kind"] == "DaemonSet" and ds["metadata"]["namespace"] == "dms"
    assert cluster.deleted == [f"daemonset/{ds['metadata']['name']}"]                       # 끝나면 지운다
    spec = ds["spec"]["template"]["spec"]
    assert spec["tolerations"] == [{"operator": "Exists"}]
    [c] = spec["containers"]
    assert c["image"] == CILIUM and c["imagePullPolicy"] == "Never"                         # 레지스트리 전 — pull 하지 않는다
    assert c["securityContext"]["privileged"] is True and c["securityContext"]["runAsUser"] == 0
    assert {v["hostPath"]["path"]: v["hostPath"]["type"] for v in spec["volumes"]} == {"/sys/fs": "Directory", "/etc": "Directory"}
    assert all(m["readOnly"] is True for m in c["volumeMounts"])
    assert "/etc/selinux" not in [v["hostPath"]["path"] for v in spec["volumes"]]           # CRI-O 가 Ubuntu 에 만들어 버린다
    assert ds["spec"]["selector"]["matchLabels"][probe.NONCE_LABEL] == ds["spec"]["template"]["metadata"]["labels"][probe.NONCE_LABEL]


def test_main_fails_on_a_dms_node_and_prints_the_guide(capsys):
    rc, cluster = _main([], logs={"cp1": UBUNTU, "w1": ROCKY_REBOOT, "w2": ENFORCING, "gpu1": UBUNTU})
    cap = capsys.readouterr()
    out = json.loads(cap.out)
    by = {n["node"]: n for n in out["nodes"]}
    assert rc == 1 and out["result"] == "FAIL"
    assert by["w1"]["code"] == "reboot-enforcing" and by["w2"]["code"] == "enforcing"
    assert "grubby --update-kernel ALL --args selinux=0" in cap.err and "w1, w2" in cap.err
    assert out["fail_guide"] == probe.FAIL_GUIDE
    assert cluster.deleted


def test_main_non_dms_node_is_info_only(capsys):
    rc, _ = _main([], logs={"cp1": UBUNTU, "w1": UBUNTU, "w2": UBUNTU, "gpu1": ENFORCING})
    out = json.loads(capsys.readouterr().out)
    gpu = [n for n in out["nodes"] if n["node"] == "gpu1"][0]
    assert rc == 0 and gpu["status"] == "INFO" and gpu["measured"] == "FAIL" and gpu["dms"] is False


def test_main_image_missing_on_a_node_is_unknown_with_the_getenforce_guide(capsys):
    rc, cluster = _main(["--timeout", "5"], logs={"cp1": UBUNTU, "w1": UBUNTU, "gpu1": UBUNTU}, waiting={"w2": "ErrImageNeverPull"})
    cap = capsys.readouterr()
    out = json.loads(cap.out)
    w2 = [n for n in out["nodes"] if n["node"] == "w2"][0]
    assert rc == 4 and out["result"] == "UNKNOWN" and w2["status"] == "UNKNOWN" and "--image" in w2["message"]
    assert "getenforce" in cap.err and out["unknown_guide"] == probe.UNKNOWN_GUIDE
    assert cluster.deleted


def test_main_pod_never_appears_is_unknown_after_timeout(capsys, monkeypatch):
    monkeypatch.setattr(probe, "POLL_SECONDS", 0.01)
    rc, cluster = _main(["--timeout", "0.2"], logs={"cp1": UBUNTU, "w1": UBUNTU, "gpu1": UBUNTU})
    out = json.loads(capsys.readouterr().out)
    w2 = [n for n in out["nodes"] if n["node"] == "w2"][0]
    assert rc == 4 and w2["status"] == "UNKNOWN" and "생기지 않았다" in w2["message"]
    assert cluster.deleted


def test_main_not_ready_dms_node_is_unknown(capsys):
    nodes = NODES[:1] + [_node("w1", ready=False)]
    rc, _ = _main([], nodes=nodes, logs={"cp1": UBUNTU, "w1": UBUNTU})
    out = json.loads(capsys.readouterr().out)
    assert rc == 4 and [n["status"] for n in out["nodes"] if n["node"] == "w1"] == ["UNKNOWN"]


def test_main_deletes_the_daemonset_on_interrupt_and_on_create_failure(capsys):
    rc, cluster = _main([], interrupt=True)
    out = json.loads(capsys.readouterr().out)
    assert rc == 4 and out["nodes"][0]["code"] == "aborted" and len(cluster.deleted) == 1
    rc, cluster = _main([], create_error="admission webhook denied")
    out = json.loads(capsys.readouterr().out)
    assert rc == 4 and "admission webhook denied" in out["nodes"][0]["message"] and len(cluster.deleted) == 1


def test_main_deletes_leftover_probes_first(capsys):
    rc, cluster = _main([], logs={n["metadata"]["name"]: UBUNTU for n in NODES}, leftovers=["daemonset.apps/dms-selinux-probe-old"])
    assert rc == 0 and cluster.deleted[0] == "daemonset.apps/dms-selinux-probe-old" and len(cluster.deleted) == 2


def test_main_image_override_skips_cilium_discovery(capsys):
    rc, cluster = _main(["--image", "r/sh:1"], logs={n["metadata"]["name"]: UBUNTU for n in NODES})
    assert rc == 0 and "cilium" not in cluster.calls
    assert cluster.created[0]["spec"]["template"]["spec"]["containers"][0]["image"] == "r/sh:1"


def test_usage_errors_exit_2_before_creating_anything(capsys, monkeypatch):
    monkeypatch.delenv("KUBE_CONTEXT", raising=False)

    class _NoCluster:
        def __init__(self, *_a):
            raise AssertionError("클러스터 객체를 만들기 전에 거부해야 한다")

    assert probe.main([], kube_factory=_NoCluster) == 2                               # --context 없음
    assert probe.main(["--context", "known", "--timeout", "0"], kube_factory=_NoCluster) == 2
    assert probe.main(["--bogus"], kube_factory=_NoCluster) == 2
    for kw, needle in (({"cilium": False}, "--image"), ({"ns_labels": {"pod-security.kubernetes.io/enforce": "baseline"}}, "privileged"),
                       ({"nodes": [_node("gpu1", taints=[{"key": "team", "effect": "NoSchedule"}])]}, "DMS 노드가 없다")):
        rc, cluster = _main([], **kw)
        assert rc == 2 and needle in capsys.readouterr().err and cluster.created == [] and cluster.deleted == []
    FakeCluster.instances = []
    rc = probe.main(["--context", "nope"], kube_factory=lambda c, n: FakeCluster(c, n))
    assert rc == 2 and FakeCluster.instances[-1].calls == []


def test_missing_namespace_is_a_usage_error(capsys):
    class NoNs(FakeCluster):
        def namespace_labels(self):
            return None

    FakeCluster.instances = []
    rc = probe.main(["--context", "known"], kube_factory=lambda c, n: NoNs(c, n))
    assert rc == 2 and "00-namespace.yaml" in capsys.readouterr().err and FakeCluster.instances[-1].created == []


# --- dry-run(실제 스크립트 + kubectl 심 — 모든 호출에 --context, 읽기만) ---------------------------------------------------

_SHIM = """#!/bin/sh
printf '%s\\n' "$*" >> '{log}'
if [ "$1" = "config" ] && [ "$2" = "get-contexts" ]; then [ "$3" = "known" ] && exit 0; exit 1; fi
case "$*" in
  *"get nodes -o json"*) cat '{nodes}'; exit 0;;
  *"get daemonsets -l k8s-app=cilium -o json"*) cat '{cilium}'; exit 0;;
  *"get namespace dms"*) printf '%s' '{{"metadata":{{"labels":{{"pod-security.kubernetes.io/enforce":"privileged"}}}}}}'; exit 0;;
esac
exit 0
"""


def test_dry_run_reads_only_with_the_named_context(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log, nodes, cilium = tmp_path / "kubectl.log", tmp_path / "nodes.json", tmp_path / "cilium.json"
    nodes.write_text(json.dumps({"items": NODES}))
    cilium.write_text(json.dumps({"items": [{"metadata": {"name": "cilium"}, "spec": {"template": {"spec": {"containers": [
        {"name": "cilium-agent", "image": CILIUM}]}}}}]}))
    shim = bindir / "kubectl"
    shim.write_text(_SHIM.format(log=log, nodes=nodes, cilium=cilium))
    shim.chmod(0o755)
    env = {"PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}", "HOME": str(tmp_path)}
    r = subprocess.run([sys.executable, str(SCRIPT), "--context", "known", "--dry-run", "--json"], env=env, capture_output=True,
                       text=True, timeout=60)
    assert r.returncode == 0, (r.stdout, r.stderr)
    calls = [c for c in log.read_text().splitlines() if not c.startswith("config get-contexts")]
    assert calls and all(c.startswith("--context known ") for c in calls), calls
    assert not [c for c in calls if any(w in c.split() for w in ("create", "delete", "apply", "patch", "exec"))], calls
    out = json.loads(r.stdout)
    assert out["result"] == "DRY-RUN" and out["image"] == CILIUM and out["judged"] == ["cp1", "w1", "w2"] and out["others"] == ["gpu1"]
    assert out["manifest"]["spec"]["template"]["spec"]["containers"][0]["imagePullPolicy"] == "Never"
    r = subprocess.run([sys.executable, str(SCRIPT), "--context", "nope", "--dry-run"], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 2 and "nope" in r.stderr


def test_collect_waits_grace_for_other_nodes_then_stops():
    class K:
        def pods(self, nonce):
            return [{"metadata": {"name": "p-cp1"}, "spec": {"nodeName": "cp1"},
                     "status": {"phase": "Running", "containerStatuses": [{"state": {"running": {}}}]}}]

        def logs(self, pod):
            return UBUNTU

        def events(self, kind, name):
            return []

    t = [0.0]

    def clock():
        return t[0]

    def sleep(s):
        t[0] += s

    got = probe.collect(K(), nonce="n", ds_name="d", judged=["cp1"], others=["gpu1"], not_ready=set(), timeout=120,
                        poll=1.0, grace=5.0, clock=clock, sleep=sleep)
    assert got["cp1"]["raw"]["done"] and got["gpu1"]["problem"] and t[0] <= 6.0           # 120초를 다 기다리지 않는다
