"""노드 pull 프로브(deploy/pull-probe.py, 2026-11-03 ⑦ — 설계 v6 7-4 「노드 신뢰 설정」·8-1 S6) 계약.

클러스터 없이 본다:
- 이미지: render.env 의 ADDON_REGISTRY(없으면 REGISTRY) + images.lock 의 registry:<태그>, 렌더 디렉터리의 app/kube-context 와 --context 대조.
- 파드 상태 판정(pull 성공·실패·대기), 시간 초과 노드의 이벤트 판정(Pulling 만 → FAIL, 흔적 없음 → 모름), 오류 문구 → 힌트(신뢰 설정·서명
  정책·프록시 NO_PROXY·ClusterIP 경로 — 마지막 줄은 늘 네 가지).
- main(가짜 클러스터): DaemonSet 이 Always·toleration Exists·비 root·sleep 모양, 판정은 DMS 노드만(--nodes 면 nodeAffinity 로 그 노드만),
  끝나면(실패·중단에도) 삭제, 종료 코드 0/1/4/2. dry-run 은 읽기만(kubectl 심 — 모든 호출에 --context).
"""
import ast
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "deploy" / "pull-probe.py"
LOCK = REPO_ROOT / "deploy" / "images.lock"
_spec = importlib.util.spec_from_file_location("pull_probe", SCRIPT)
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)
_pg_spec = importlib.util.spec_from_file_location("pg_path_probe_for_pull", REPO_ROOT / "deploy" / "pg-path-probe.py")
pg_probe = importlib.util.module_from_spec(_pg_spec)
_pg_spec.loader.exec_module(pg_probe)

REG_TAG = re.search(r"(?m)^registry:(\S+)\s", LOCK.read_text()).group(1)
IMAGE = f"10.112.0.50:5000/registry:{REG_TAG}"


def test_script_is_python39_syntax():
    ast.parse(SCRIPT.read_text(encoding="utf-8"), feature_version=(3, 9))


# --- 이미지 출처 ----------------------------------------------------------------------------------------------------------

def _render_dir(tmp_path, *, addon="10.112.0.50:5000", registry="10.112.0.50:5000", context="dmsk8s"):
    d = tmp_path / "rendered" / "site"
    (d / "app").mkdir(parents=True)
    lines = ["# 렌더", "SITE=site", f"REGISTRY={registry}", 'PLATFORM_LAYERS="priorityclass registry"']
    if addon is not None:
        lines.append(f"ADDON_REGISTRY={addon}")
    (d / "render.env").write_text("\n".join(lines) + "\n")
    if context is not None:
        (d / "app" / "kube-context").write_text(f"# 컨텍스트\n{context}\n")
    return d / "render.env"


def test_image_is_the_lock_registry_tag_on_the_addon_registry(tmp_path):
    assert probe.image_from_render(str(_render_dir(tmp_path)), str(LOCK)) == IMAGE
    # external 사이트(REGISTRY_CLUSTER_IP 없음)는 ADDON_REGISTRY == REGISTRY — 옛 render.env 처럼 ADDON_REGISTRY 가 없으면 REGISTRY
    env = _render_dir(tmp_path / "ext", addon=None, registry="registry.corp.example:5000")
    assert probe.image_from_render(str(env), str(LOCK)) == f"registry.corp.example:5000/registry:{REG_TAG}"


def test_lock_without_a_single_registry_line_or_broken_lock_is_an_error(tmp_path):
    env = _render_dir(tmp_path)
    lock = tmp_path / "images.lock"
    lock.write_text("buildah:stable  quay.io/buildah/stable:v1.43.1  sha256:" + "b" * 64 + "\n")
    with pytest.raises(ValueError, match="registry"):
        probe.image_from_render(str(env), str(lock))
    lock.write_text("registry:3.1.2 docker.io/library/registry:3.1.2\n")
    with pytest.raises(ValueError, match="형식"):
        probe.image_from_render(str(env), str(lock))


def test_bad_registry_value_is_an_error(tmp_path):
    with pytest.raises(ValueError):
        probe.image_from_render(str(_render_dir(tmp_path, addon="http://x/y", registry="")), str(LOCK))


def test_registry_of_follows_docker_reference_rules():
    assert probe.registry_of("10.112.0.50:5000/registry:3.1.2") == "10.112.0.50:5000"
    assert probe.registry_of("registry.corp.example/dms:d1") == "registry.corp.example"
    assert probe.registry_of("library/busybox:1") == "" and probe.registry_of("busybox") == ""


# --- 판정 -----------------------------------------------------------------------------------------------------------------

def _pod(node, *, state=None, last=None, image_id="", phase="Pending", conditions=None):
    st = {"state": state or {}, "lastState": last or {}, "imageID": image_id}
    return {"metadata": {"name": f"pull-{node}"}, "spec": {"nodeName": node},
            "status": {"phase": phase, "containerStatuses": [st] if state is not None else [], "conditions": conditions or []}}


@pytest.mark.parametrize("pod, kind", [
    (_pod("a", state={"running": {}}, image_id="10.112.0.50:5000/registry@sha256:" + "c" * 64, phase="Running"), "ok"),
    (_pod("a", state={"waiting": {"reason": "ErrImagePull", "message": "http: server gave HTTP response to HTTPS client"}}), "fail"),
    (_pod("a", state={"waiting": {"reason": "ImagePullBackOff", "message": "Back-off pulling image"}}), "fail"),
    (_pod("a", state={"waiting": {"reason": "ContainerCreating"}}), "pending"),
    (_pod("a", state={"waiting": {"reason": "CreateContainerError", "message": "exec: sleep: not found"}}), "ok"),   # sleep 없는 이미지
    (_pod("a", state={"waiting": {"reason": "CrashLoopBackOff"}}, last={"terminated": {"exitCode": 127}}), "ok"),
    (_pod("a", conditions=[{"type": "PodScheduled", "status": "False", "reason": "Unschedulable", "message": "Insufficient cpu"}]),
     "pending"),
])
def test_pod_pull_state(pod, kind):
    assert probe.pod_pull_state(pod)[0] == kind


def test_unscheduled_pod_says_why():
    pod = _pod("a", conditions=[{"type": "PodScheduled", "status": "False", "reason": "Unschedulable", "message": "Insufficient cpu"}])
    assert "Insufficient cpu" in probe.pod_pull_state(pod)[1]


@pytest.mark.parametrize("events, status, needle", [
    ([{"reason": "Pulling", "message": "Pulling image x"}], "FAIL", "120초 안에 끝나지 않았다"),
    ([{"reason": "Pulling", "message": "p"}, {"reason": "Failed", "message": "Failed to pull image: dial tcp: i/o timeout"}], "FAIL",
     "i/o timeout"),
    ([{"reason": "Pulling", "message": "p"}, {"reason": "Pulled", "message": "Successfully pulled image"}], "PASS", "pull 됨"),
    ([{"reason": "FailedScheduling", "message": "0/3 nodes"}], "UNKNOWN", "스케줄"),
    ([], "UNKNOWN", "흔적이 없다"),
])
def test_judge_events_for_timed_out_nodes(events, status, needle):
    got = probe.judge_events(events, 120)
    assert got[0] == status and needle in got[1]


@pytest.mark.parametrize("message, needle", [
    ('Get "https://10.112.0.50:5000/v2/": http: server gave HTTP response to HTTPS client', "registries.conf.d"),
    ("pinging container registry 10.112.0.50:5000: x509: certificate signed by unknown authority", "insecure=true"),
    ("Source image rejected: Running image docker://10.112.0.50:5000/registry:3.1.2 is rejected by policy.", "/etc/crio/policy.json"),
    ("proxyconnect tcp: dial tcp 10.0.0.9:3128: connect: connection refused", "NO_PROXY 에 10.112.0.50"),
    ('Get "http://10.112.0.50:5000/v2/": dial tcp 10.112.0.50:5000: i/o timeout', "curl -sS http://10.112.0.50:5000/v2/"),
    ("reading manifest 3.1.2 in 10.112.0.50:5000/registry: manifest unknown", "S5"),
])
def test_hints_point_at_the_cause_and_always_list_the_four(message, needle):
    got = probe.hints(message, "10.112.0.50:5000")
    assert any(needle in h for h in got), got
    assert got[-1] == probe.GENERIC_HINT
    for word in ("신뢰 설정(registries.conf.d)", "서명 정책", "프록시 NO_PROXY", "ClusterIP 경로"):
        assert word in probe.GENERIC_HINT


def test_unknown_message_still_gets_the_four_hints():
    assert probe.hints("something odd", "10.112.0.50:5000") == [probe.GENERIC_HINT]


def test_verdict_and_exit_codes():
    r = lambda s: {"status": s}  # noqa: E731
    assert probe.verdict([]) == "UNKNOWN" and probe.verdict([r("INFO")]) == "UNKNOWN"
    assert probe.verdict([r("PASS"), r("INFO")]) == "PASS"
    assert probe.verdict([r("PASS"), r("UNKNOWN")]) == "UNKNOWN"
    assert probe.verdict([r("UNKNOWN"), r("FAIL")]) == "FAIL"
    assert probe.EXIT == {"PASS": 0, "FAIL": 1, "UNKNOWN": 4}


# --- main(가짜 클러스터) ---------------------------------------------------------------------------------------------------

def _node(name, *, cp=False, taints=(), ready=True):
    labels = {"node-role.kubernetes.io/control-plane": ""} if cp else {}
    return {"metadata": {"name": name, "labels": labels}, "spec": {"taints": list(taints)},
            "status": {"conditions": [{"type": "Ready", "status": "True" if ready else "False"}]}}


NODES = [
    _node("cp1", cp=True, taints=[{"key": "node-role.kubernetes.io/control-plane", "effect": "NoSchedule"}]),
    _node("w1"), _node("w2"),
    _node("gpu1", taints=[{"key": "team", "value": "ml", "effect": "NoSchedule"}]),
]


def test_dms_nodes_is_the_pg_path_probe_definition():
    assert probe.dms_nodes(NODES) == pg_probe.dms_nodes(NODES) == (["cp1", "w1", "w2"], ["gpu1"])


OK = {"state": {"running": {}}, "image_id": "10.112.0.50:5000/registry@sha256:" + "d" * 64, "phase": "Running"}
TRUST = {"state": {"waiting": {"reason": "ErrImagePull",
                               "message": 'Get "https://10.112.0.50:5000/v2/": http: server gave HTTP response to HTTPS client'}}}
STUCK = {"state": {"waiting": {"reason": "ContainerCreating"}}}


class FakeCluster:
    instances = []

    def __init__(self, context, namespace, *, nodes=NODES, pods=None, events=None, ns=True, leftovers=(), interrupt=False):
        self.context, self.namespace = context, namespace
        self.node_items, self.pod_spec, self.pod_events = nodes, pods or {}, events or {}
        self.ns, self.left, self.interrupt = ns, list(leftovers), interrupt
        self.created, self.deleted, self.calls = [], [], []
        FakeCluster.instances.append(self)

    def context_exists(self):
        return self.context == "known"

    def nodes(self):
        self.calls.append("nodes")
        return self.node_items

    def namespace_exists(self):
        return self.ns

    def leftovers(self):
        return list(self.left)

    def create(self, manifest):
        self.calls.append("create")
        self.created.append(manifest)

    def pods(self, nonce):
        if self.interrupt:
            self.interrupt = False
            raise KeyboardInterrupt
        if not self.created or [r for r in self.deleted if r.startswith("daemonset/")]:
            return []
        out = []
        for node, spec in self.pod_spec.items():
            p = _pod(node, state=spec["state"], image_id=spec.get("image_id", ""), phase=spec.get("phase", "Pending"))
            out.append(p)
        return out

    def events(self, kind, name):
        return self.pod_events.get(name, [])

    def delete(self, ref):
        self.deleted.append(ref)
        return True


def _main(argv, **kw):
    FakeCluster.instances = []
    rc = probe.main(["--context", "known", "--json", "--image", IMAGE, *argv], kube_factory=lambda c, n: FakeCluster(c, n, **kw))
    return rc, FakeCluster.instances[-1]


def test_main_all_pull_creates_the_designed_daemonset_and_deletes_it(capsys):
    rc, cluster = _main([], pods={"cp1": OK, "w1": OK, "w2": OK, "gpu1": OK})
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["result"] == "PASS"
    assert {n["node"]: n["status"] for n in out["nodes"]} == {"cp1": "PASS", "w1": "PASS", "w2": "PASS", "gpu1": "INFO"}
    [ds] = cluster.created
    assert cluster.deleted == [f"daemonset/{ds['metadata']['name']}"]
    spec = ds["spec"]["template"]["spec"]
    assert spec["tolerations"] == [{"operator": "Exists"}] and "affinity" not in spec
    [c] = spec["containers"]
    assert c["image"] == IMAGE and c["imagePullPolicy"] == "Always" and c["command"][0] == "sleep"
    assert spec["securityContext"]["runAsNonRoot"] is True and c["securityContext"]["capabilities"] == {"drop": ["ALL"]}
    assert "volumes" not in spec and not c.get("securityContext", {}).get("privileged")


def test_main_fails_a_dms_node_with_hints(capsys):
    rc, cluster = _main([], pods={"cp1": OK, "w1": TRUST, "w2": OK, "gpu1": OK})
    cap = capsys.readouterr()
    out = json.loads(cap.out)
    w1 = [n for n in out["nodes"] if n["node"] == "w1"][0]
    assert rc == 1 and out["result"] == "FAIL" and w1["status"] == "FAIL"
    assert any("registries.conf.d" in h for h in w1["hints"]) and w1["hints"][-1] == probe.GENERIC_HINT
    assert "registries.conf.d" in cap.err and cluster.deleted


def test_main_non_dms_failure_is_info_only(capsys):
    rc, _ = _main([], pods={"cp1": OK, "w1": OK, "w2": OK, "gpu1": TRUST})
    out = json.loads(capsys.readouterr().out)
    gpu = [n for n in out["nodes"] if n["node"] == "gpu1"][0]
    assert rc == 0 and gpu["status"] == "INFO" and gpu["measured"] == "FAIL"


def test_main_stuck_pulling_is_fail_and_missing_pod_is_unknown(capsys, monkeypatch):
    monkeypatch.setattr(probe, "POLL_SECONDS", 0.01)
    rc, _ = _main(["--timeout", "0.2"], pods={"cp1": OK, "w1": STUCK, "gpu1": OK},
                  events={"pull-w1": [{"reason": "Pulling", "message": "Pulling image"}]})
    out = json.loads(capsys.readouterr().out)
    by = {n["node"]: n for n in out["nodes"]}
    assert by["w1"]["status"] == "FAIL" and "끝나지 않았다" in by["w1"]["message"]
    assert by["w2"]["status"] == "UNKNOWN" and "생기지 않았다" in by["w2"]["message"]
    assert rc == 1


def test_main_backoff_message_is_replaced_by_the_failed_event_cause(capsys):
    backoff = {"state": {"waiting": {"reason": "ImagePullBackOff", "message": 'Back-off pulling image "x"'}}}
    rc, _ = _main([], pods={"cp1": OK, "w1": backoff, "w2": OK, "gpu1": OK},
                  events={"pull-w1": [{"reason": "Failed", "message": "Failed to pull image: proxyconnect tcp: connection refused"}]})
    out = json.loads(capsys.readouterr().out)
    w1 = [n for n in out["nodes"] if n["node"] == "w1"][0]
    assert rc == 1 and "proxyconnect" in w1["message"] and any("NO_PROXY" in h for h in w1["hints"])


def test_main_nodes_option_pins_the_daemonset_and_judges_only_those(capsys):
    rc, cluster = _main(["--nodes", "cp1"], pods={"cp1": OK})
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and [n["node"] for n in out["nodes"]] == ["cp1"]
    terms = cluster.created[0]["spec"]["template"]["spec"]["affinity"]["nodeAffinity"][
        "requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"]
    assert terms == [{"matchFields": [{"key": "metadata.name", "operator": "In", "values": ["cp1"]}]}]


def test_main_not_ready_node_is_unknown(capsys):
    rc, _ = _main([], nodes=[NODES[0], _node("w1", ready=False)], pods={"cp1": OK})
    out = json.loads(capsys.readouterr().out)
    assert rc == 4 and [n["status"] for n in out["nodes"] if n["node"] == "w1"] == ["UNKNOWN"]


def test_main_deletes_on_interrupt_and_removes_leftovers_first(capsys):
    rc, cluster = _main([], interrupt=True)
    out = json.loads(capsys.readouterr().out)
    assert rc == 4 and out["nodes"][0]["message"].startswith("중단됨") and len(cluster.deleted) == 1
    rc, cluster = _main([], pods={"cp1": OK, "w1": OK, "w2": OK, "gpu1": OK}, leftovers=["daemonset.apps/dms-pull-probe-old"])
    assert rc == 0 and cluster.deleted[0] == "daemonset.apps/dms-pull-probe-old" and len(cluster.deleted) == 2


def test_usage_errors_exit_2_before_creating_anything(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("KUBE_CONTEXT", raising=False)

    class _NoCluster:
        def __init__(self, *_a):
            raise AssertionError("클러스터 객체를 만들기 전에 거부해야 한다")

    env = _render_dir(tmp_path)
    for argv in (["--image", IMAGE],                                                  # --context 없음
                 ["--context", "known"],                                              # 이미지 출처 없음
                 ["--context", "known", "--image", IMAGE, "--render-env", str(env)],  # 둘 다
                 ["--context", "other", "--render-env", str(env)],                    # 렌더는 dmsk8s 전용
                 ["--context", "known", "--render-env", str(tmp_path / "none.env")],
                 ["--context", "known", "--image", IMAGE, "--nodes", ","],
                 ["--context", "known", "--image", IMAGE, "--timeout", "0"]):
        assert probe.main(argv, kube_factory=_NoCluster) == 2, argv
    assert "dmsk8s" in capsys.readouterr().err
    rc, cluster = _main(["--nodes", "cp1,nope"])
    assert rc == 2 and "nope" in capsys.readouterr().err and cluster.created == []
    rc, cluster = _main([], ns=False)
    assert rc == 2 and "00-namespace.yaml" in capsys.readouterr().err and cluster.created == []
    FakeCluster.instances = []
    assert probe.main(["--context", "nope", "--image", IMAGE], kube_factory=lambda c, n: FakeCluster(c, n)) == 2
    assert FakeCluster.instances[-1].calls == []


def test_render_env_with_matching_context_is_accepted(tmp_path, capsys):
    env = _render_dir(tmp_path, context="known")
    FakeCluster.instances = []
    rc = probe.main(["--context", "known", "--render-env", str(env), "--dry-run", "--json"], kube_factory=lambda c, n: FakeCluster(c, n))
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["image"] == IMAGE and FakeCluster.instances[-1].created == []


# --- dry-run(실제 스크립트 + kubectl 심) -------------------------------------------------------------------------------------

_SHIM = """#!/bin/sh
printf '%s\\n' "$*" >> '{log}'
if [ "$1" = "config" ] && [ "$2" = "get-contexts" ]; then [ "$3" = "known" ] && exit 0; exit 1; fi
case "$*" in
  *"get nodes -o json"*) cat '{nodes}'; exit 0;;
  *"get namespace dms"*) echo namespace/dms; exit 0;;
esac
exit 0
"""


def test_dry_run_reads_only_with_the_named_context(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log, nodes = tmp_path / "kubectl.log", tmp_path / "nodes.json"
    nodes.write_text(json.dumps({"items": NODES}))
    shim = bindir / "kubectl"
    shim.write_text(_SHIM.format(log=log, nodes=nodes))
    shim.chmod(0o755)
    env_file = _render_dir(tmp_path, context="known")
    env = {"PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}", "HOME": str(tmp_path)}
    r = subprocess.run([sys.executable, str(SCRIPT), "--context", "known", "--render-env", str(env_file), "--dry-run", "--json"],
                       env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, (r.stdout, r.stderr)
    calls = [c for c in log.read_text().splitlines() if not c.startswith("config get-contexts")]
    assert calls and all(c.startswith("--context known ") for c in calls), calls
    assert not [c for c in calls if any(w in c.split() for w in ("create", "delete", "apply", "patch", "exec"))], calls
    out = json.loads(r.stdout)
    assert out["result"] == "DRY-RUN" and out["image"] == IMAGE and out["judged"] == ["cp1", "w1", "w2"] and out["others"] == ["gpu1"]
