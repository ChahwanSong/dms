"""install-detect — 애드온 감지 상태 기계(설계 v6 8-2) + 설치 계획(S3) (2026-11-03 ⑦ → 2026-11-16 입구 = 클러스터의 Traefik).

설계 9 「테스트·게이트」 ⑦: `test_install_detect`(kubectl 심으로 A~P, 「CNPG 가 어디에든 있으면 두 번째를 설치하지 않음」, 「C 에서는 apply 를
부르지 않음」). PATH 앞의 kubectl 은 tests/fixtures/install_detect/fake_kubectl.py(세계 JSON → 응답, 호출 기록)다 — 이 호스트의 진짜 kubectl
은 실 테스트베드를 가리키므로 절대 부르지 않는다. 고정 버전은 tests/fixtures/install_detect/versions.env(deploy/versions.env 가 올라가도
시나리오가 그대로이게), 레지스트리·PriorityClass 고정값은 저장소 매니페스트에서 읽는다(load_pins).
2026-11-16(설계 D5): 항목 gateway-api·envoy-gateway·gateway 를 없애고 traefik(남의 것 — DMS 는 설치·변경하지 않는다: C·X·U)·ingress(DMS 층
platform/ingress — Ingress dms/dms-portal-traefik)를 넣었다.
"""
import ast
import base64
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
DEPLOY = REPO / "deploy"
SCRIPT = DEPLOY / "install-detect.py"
FIX = REPO / "tests" / "fixtures" / "install_detect"
VERSIONS = FIX / "versions.env"

_spec = importlib.util.spec_from_file_location("install_detect", SCRIPT)
det = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(det)
PINS = det.load_pins(str(VERSIONS))
PRIV = {"pod-security.kubernetes.io/enforce": "privileged"}
DIGEST = "sha256:" + "0" * 64

# rke2-traefik(차트 40.x — v3.7.4) DaemonSet 의 args 모양 — 차트가 렌더하는 그대로(대소문자 섞임), PROXY trustedIPs 는 HelmChartConfig 로 넣은 것.
RKE2_TRAEFIK_ARGS = [
    "--global.checknewversion", "--global.sendanonymoususage", "--entryPoints.metrics.address=:9100/tcp",
    "--entryPoints.traefik.address=:8080/tcp", "--entryPoints.web.address=:8000/tcp", "--entryPoints.websecure.address=:8443/tcp",
    "--api.dashboard=true", "--ping=true", "--metrics.prometheus=true", "--metrics.prometheus.entrypoint=metrics",
    "--providers.kubernetescrd", "--providers.kubernetescrd.ingressClass=traefik", "--providers.kubernetescrd.allowEmptyServices=true",
    "--providers.kubernetesingress", "--providers.kubernetesingress.allowEmptyServices=true",
    "--providers.kubernetesingress.ingressendpoint.publishedservice=kube-system/rke2-traefik",
    "--entryPoints.websecure.http.tls=true", "--log.level=INFO",
    "--entryPoints.websecure.proxyProtocol.trustedIPs=12.201.53.242,12.201.53.243",
]
TRAEFIK_IMAGE = "docker.io/rancher/hardened-traefik:v3.7.4-build20260608"


def _args_without(*prefixes, extra=()):
    return [a for a in RKE2_TRAEFIK_ARGS if not a.startswith(prefixes)] + list(extra)


# --- 세계 만들기 -----------------------------------------------------------------------------------------------------------------

def meta(name, ns=None, labels=None, annotations=None):
    m = {"name": name}
    if ns:
        m["namespace"] = ns
    if labels:
        m["labels"] = dict(labels)
    if annotations:
        m["annotations"] = dict(annotations)
    return m


def owned(version=None, **extra):
    labels = {det.MANAGED_BY: det.OWNER}
    if version:
        labels[det.ADDON_VERSION] = version
    labels.update(extra)
    return labels


def crd(name, labels=None):
    return {"kind": "CustomResourceDefinition", "metadata": meta(name, labels=labels)}


def deploy(ns, name, image, labels=None, args=None, env=None, cname=None, mounts=None, volumes=None):
    c = {"name": cname or name, "image": image}
    if args is not None:
        c["args"] = list(args)
    if env is not None:
        c["env"] = list(env)
    if mounts:
        c["volumeMounts"] = mounts
    spec = {"containers": [c]}
    if volumes:
        spec["volumes"] = volumes
    return {"kind": "Deployment", "metadata": meta(name, ns, labels), "spec": {"template": {"spec": spec}}}


def traefik_workload(ns="kube-system", name="rke2-traefik", args=None, ready=3, image=TRAEFIK_IMAGE, kind="DaemonSet"):
    w = deploy(ns, name, image, labels={"app.kubernetes.io/name": "traefik", det.MANAGED_BY: "Helm"},
               args=RKE2_TRAEFIK_ARGS if args is None else args, cname="rke2-traefik")
    w["kind"] = kind
    w["status"] = {"numberReady": ready, "desiredNumberScheduled": ready} if kind == "DaemonSet" else {"readyReplicas": ready}
    return w


def ingressclass(name="traefik", controller=None):
    return {"kind": "IngressClass", "metadata": meta(name), "spec": {"controller": controller or det.TRAEFIK_CONTROLLER}}


def dms_ingress(is_owned=True, cls="traefik", host="dms-k8s.local"):
    return {"kind": "Ingress", "metadata": meta(det.INGRESS_NAME, "dms", owned() if is_owned else {"team": "web"}),
            "spec": {"ingressClassName": cls, "rules": [{"host": host}]}}


def nsobj(name, labels=None):
    return {"kind": "Namespace", "metadata": meta(name, labels=labels)}


def cm(ns, name, data):
    return {"kind": "ConfigMap", "metadata": meta(name, ns), "data": dict(data)}


def secret(ns, name, data):
    return {"kind": "Secret", "metadata": meta(name, ns),
            "data": {k: base64.b64encode(v.encode()).decode() for k, v in data.items()}}


CNPG_ARGS = ["controller", "--leader-elect", "--config-map-name=cnpg-controller-manager-config",
             "--secret-name=cnpg-controller-manager-config", "--webhook-port=9443"]


def cnpg_op(ns="cnpg-system", version="1.30.1", is_owned=True, name="cnpg-controller-manager", labels=None, env=None,
            image=None):
    if labels is None:
        labels = (owned(version, **{"app.kubernetes.io/name": "cloudnative-pg"}) if is_owned else
                  {"app.kubernetes.io/name": "cloudnative-pg", det.MANAGED_BY: "Helm", "app.kubernetes.io/version": version})
    return deploy(ns, name, image or "reg/cloudnative-pg:%s@%s" % (version, DIGEST), labels=labels, args=CNPG_ARGS,
                  env=env if env is not None else [{"name": "OPERATOR_IMAGE_NAME", "value": "reg/cloudnative-pg"}], cname="manager")


def volcano_deploys(version="v1.15.0", is_owned=False, sched_args=None, ns="volcano-system", sched_name="volcano-scheduler",
                    sched_labels=None):
    def labels(app):
        return owned(version, app=app) if is_owned else {"app": app}
    args = sched_args if sched_args is not None else ["--logtostderr", "--scheduler-conf=/volcano.scheduler/volcano-scheduler.conf"]
    return [deploy(ns, sched_name, "docker.io/volcanosh/vc-scheduler:%s" % version,
                   labels=labels("volcano-scheduler") if sched_labels is None else sched_labels, args=args, cname="volcano-scheduler"),
            deploy(ns, "volcano-controllers", "docker.io/volcanosh/vc-controller-manager:%s" % version, labels=labels("volcano-controller")),
            deploy(ns, "volcano-admission", "docker.io/volcanosh/vc-webhook-manager:%s" % version, labels=labels("volcano-admission"),
                   cname="admission")]


def pg_cluster(tag="17.11-202611020810-minimal-trixie", is_owned=True):
    return {"kind": "Cluster", "metadata": meta("dms-pg", "dms", owned() if is_owned else None),
            "spec": {"imageName": "10.112.0.50:5000/postgresql:%s@%s" % (tag, DIGEST)}}


def pg_volumes(shared="/dms_shared", phases=None, is_owned=True):
    sc = {"kind": "StorageClass", "metadata": meta("dms-pg-shared", labels=owned() if is_owned else None),
          "provisioner": "kubernetes.io/no-provisioner", "reclaimPolicy": "Retain", "volumeBindingMode": "Immediate"}
    pvs = []
    for n, name in enumerate(det.PG_PVS):
        pvs.append({"kind": "PersistentVolume", "metadata": meta(name, labels=owned()),
                    "spec": {"capacity": {"storage": "20Gi"}, "storageClassName": "dms-pg-shared",
                             "hostPath": {"path": "%s/dms/pgdata/%s" % (shared, name), "type": "Directory"}},
                    "status": {"phase": (phases or {}).get(name, "Bound" if n < 3 else "Available")}})
    return sc, pvs


def world(k8s="v1.34.6"):
    return {"contexts": ["ctx"], "k8s": k8s, "objects": {}, "errors": {}, "dry_run": [], "kustomize": {}}


def add(w, kind, *objs):
    w["objects"].setdefault(kind, []).extend(objs)
    return w


def drop(w, kind, name, ns=None):
    w["objects"][kind] = [o for o in w["objects"].get(kind, [])
                          if not (o["metadata"]["name"] == name and (ns is None or o["metadata"].get("namespace") == ns))]
    return w


def empty_world(k8s="v1.34.6"):
    return add(world(k8s), "namespaces", nsobj("kube-system"), nsobj("default"))


def with_traefik(w, **kw):
    """클러스터에 이미 있는 Traefik(RKE2 rke2-traefik 모양) — IngressClass traefik + kube-system DaemonSet. DMS 는 이것을 설치하지 않는다."""
    add(w, "ingressclasses", ingressclass())
    add(w, "daemonsets", traefik_workload(**kw))
    return w


def bare_world(k8s="v1.34.6"):
    """DMS 가 아직 아무것도 깔지 않은 클러스터 — 입구(Traefik)는 클러스터 운영자가 이미 깔아 둔 것(2026-11-16 전제)."""
    return with_traefik(empty_world(k8s))


def tb_world(k8s="v1.34.6"):
    """dmsk8s 의 2026-11-16 이후 모양: DMS 가 깐 PriorityClass·레지스트리·Ingress dms-portal-traefik·CNPG·DB + 테스트베드 IaC 가 깐 Traefik·
    Volcano v1.15.0(+ 되돌림용 IngressClass nginx)."""
    w = bare_world(k8s)
    add(w, "ingressclasses", ingressclass("nginx", "k8s.io/ingress-nginx"))
    add(w, "namespaces", nsobj("dms", PRIV), nsobj("cnpg-system", owned("1.30.1")), nsobj("volcano-system"))
    add(w, "priorityclasses", {"kind": "PriorityClass", "metadata": meta("dms-addon-critical", labels=owned()),
                               "value": 1000, "preemptionPolicy": "PreemptLowerPriority"})
    add(w, "deployments", deploy("dms", "dms-registry", "10.112.0.50:5000/registry:%s" % PINS["registry"],
                                 labels=owned(PINS["registry"], **{"app.kubernetes.io/name": "dms-registry"})))
    add(w, "services", {"kind": "Service", "metadata": meta("dms-registry", "dms", owned()), "spec": {"clusterIP": "10.112.0.50"}})
    add(w, "ingresses", dms_ingress())
    add(w, "crds", crd(det.CNPG_CRD, labels=owned("1.30.1")))
    add(w, "deployments", cnpg_op())
    add(w, "crds", *[crd(n) for n in det.VOLCANO_CRDS])
    add(w, "deployments", *volcano_deploys())
    add(w, "queues", {"kind": "Queue", "metadata": meta("default")}, {"kind": "Queue", "metadata": meta("dms-data")})
    sc, pvs = pg_volumes()
    add(w, "clusters", pg_cluster())
    add(w, "storageclasses", sc)
    add(w, "persistentvolumes", *pvs)
    add(w, "cronjobs", {"kind": "CronJob", "metadata": meta("dms-pg-dump", "dms", owned())})
    return w


ALL_LAYERS = ("platform/priorityclass", "platform/registry", "platform/ingress", "addons/cnpg", "platform/db-volumes",
              "platform/db-cluster", "platform/db-backup", "platform/db-probe", "app")


def make_rendered(tmp_path, layers=ALL_LAYERS, ctx="ctx", **values):
    rd = tmp_path / "rendered"
    env = {"SITE": "t", "REGISTRY_MODE": "in-cluster", "REGISTRY": "10.112.0.50:5000", "REGISTRY_CLUSTER_IP": "10.112.0.50",
           "PLATFORM_LAYERS": '"priorityclass registry"', "SHARED_DIR": "/dms_shared", "PG_STORAGE_SIZE": "20Gi",
           "INGRESS_LAYERS": '"platform/ingress"', "INGRESS_CLASS": "traefik", "TRAEFIK_ENTRYPOINT": "websecure",
           "PORTAL_DOMAIN": "dms-k8s.local"}
    env.update(values)
    for layer in layers:
        (rd / layer).mkdir(parents=True, exist_ok=True)
        if ctx is not None:
            (rd / layer / "kube-context").write_text("# 테스트\n%s\n" % ctx)
    rd.mkdir(exist_ok=True)
    (rd / "render.env").write_text("".join("%s=%s\n" % kv for kv in env.items()))
    return rd


# --- 실행 ------------------------------------------------------------------------------------------------------------------------

class KubeEnv:
    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.wfile = tmp_path / "world.json"
        self.log = tmp_path / "kubectl.log"

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def cluster_calls(self):
        return [c for c in self.calls() if c["args"][:2] != ["config", "get-contexts"]]

    def applies(self):
        return [c for c in self.cluster_calls() if c["args"][0] in ("apply", "create")]


@pytest.fixture
def kenv(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    shim = bindir / "kubectl"
    shim.write_text('#!/bin/sh\nexec "%s" "%s" "$@"\n' % (sys.executable, FIX / "fake_kubectl.py"))
    shim.chmod(0o755)
    env = KubeEnv(tmp_path)
    monkeypatch.setenv("PATH", "%s%s%s" % (bindir, os.pathsep, os.environ.get("PATH", "/usr/bin:/bin")))
    monkeypatch.setenv("FAKE_KUBE_WORLD", str(env.wfile))
    monkeypatch.setenv("FAKE_KUBE_LOG", str(env.log))
    for key in ("KUBE_CONTEXT", "ADDON_INSTALL", "DMS_NS"):
        monkeypatch.delenv(key, raising=False)
    return env


def run(kenv, capsys, w, *args, mode="detect"):
    """→ (rc, JSON 문서 또는 None, {항목: 항목 dict})."""
    kenv.wfile.write_text(json.dumps(w))
    rc = det.main([mode, "--context", "ctx", "--versions-env", str(VERSIONS), "--json", *args])
    out = capsys.readouterr().out
    if rc == 2:
        return rc, None, {}
    doc = json.loads(out)
    return rc, doc, {i["item"]: i for i in doc["items"]}


def codes(items):
    return {k: v["code"] for k, v in items.items()}


# --- 3.9·고정값·래퍼 -------------------------------------------------------------------------------------------------------------

def test_python39_syntax():
    ast.parse(SCRIPT.read_text(encoding="utf-8"), feature_version=(3, 9))
    py39 = shutil.which("python3.9")
    if py39:
        subprocess.run([py39, "-c", "import ast,sys; compile(open(sys.argv[1]).read(), sys.argv[1], 'exec')", str(SCRIPT)],
                       check=True, timeout=60)


def test_pins_come_from_repo_files():
    pins = det.load_pins()                      # 진짜 deploy/versions.env + platform 매니페스트
    env = det.read_env_file(str(DEPLOY / "versions.env"))
    assert pins["cnpg"] == env["CNPG_VERSION"]
    assert pins["db"] == det.image_tag(env["PG_IMAGE"])
    assert pins["volcano"] == env.get("VOLCANO_VERSION", det.VOLCANO_DEFAULT_PIN)
    reg = (DEPLOY / "platform" / "registry" / "deployment.yaml").read_text()
    assert 'dms.io/addon-version: "%s"' % pins["registry"] in reg
    assert pins["pc_value"] == 1000 and pins["pc_preemption"] == "PreemptLowerPriority"
    # 2026-11-16: 입구(Traefik)는 DMS 가 설치하지 않는다 — Gateway API·EG 고정값을 요구하지도 읽지도 않는다.
    assert not {"gateway-api", "envoy-gateway", "gateway_controller"} & set(pins), pins


def test_missing_pin_is_usage_error(tmp_path):
    bad = tmp_path / "versions.env"
    bad.write_text("CNPG_VERSION=1.30.1\n")
    with pytest.raises(det.UsageError):
        det.load_pins(str(bad))


def test_wrapper_refuses_mutating_calls():
    kube = det.Kube("ctx")
    for args in (["delete", "ns", "dms"], ["apply", "-k", "x"], ["create", "-f", "-"], ["patch", "x"], ["label", "ns", "a", "b=c"]):
        with pytest.raises(RuntimeError):
            kube._exec(args)
    with pytest.raises(det.UsageError):
        det.Kube("")


def test_version_helpers():
    assert det.parse_version("v1.9.3") == (1, 9, 3) and det.parse_version("1.30") == (1, 30, 0)
    assert det.parse_version("latest") is None and det.parse_version(None) is None
    assert det.image_tag("10.112.0.50:5000/a/b:1.2@sha256:ff") == "1.2"
    assert det.image_tag("10.112.0.50:5000/a/b@sha256:ff") is None
    assert det.image_base("ghcr.io/cloudnative-pg/postgresql:17") == "postgresql"
    assert det.pg_version("17.11-202611020810-minimal-trixie") == (17, 11, 202611020810)


def test_traefik_static_flags_are_case_insensitive():
    cfg = det.traefik_static(["traefik", "--entryPoints.WebSecure.address=:8443/tcp", "--providers.kubernetesIngress",
                              "--entrypoints.websecure.http.tls", "--log.level", "INFO"])
    assert cfg == {"entrypoints.websecure.address": ":8443/tcp", "providers.kubernetesingress": "true",
                   "entrypoints.websecure.http.tls": "true", "log.level": "INFO"}
    assert det._flag_on({"providers.kubernetesingress.ingressclass": "x"}, "providers.kubernetesingress")   # 하위 옵션도 그 절을 켠다
    assert not det._flag_on({"providers.kubernetesingress": "false"}, "providers.kubernetesingress")


# --- 테스트베드 모양 · 빈 클러스터 ---------------------------------------------------------------------------------------------------

def test_testbed_shape_dms_owned_is_d_eq_and_cluster_traefik_is_c(kenv, capsys):
    rc, doc, items = run(kenv, capsys, tb_world())
    assert codes(items) == {"priorityclass": "D=", "registry": "D=", "traefik": "C", "ingress": "D=", "cnpg": "D=", "volcano": "C",
                            "db": "D="}
    assert rc == 0 and doc["result"] == "PASS"
    # 서드파티 D= 는 no-op, 플랫폼 D= 는 SSA 멱등 재적용, C 는 재사용(적용 안 함 — Traefik 은 DMS 가 바꾸지 않는다)
    assert items["cnpg"]["action"] == "none"
    assert [items[i]["action"] for i in ("priorityclass", "registry", "ingress", "db")] == ["reapply"] * 4
    for item in ("traefik", "volcano"):
        assert items[item]["action"] == "reuse" and items[item]["apply"] is False, item
    assert items["traefik"]["installed"] == "v3.7.4-build20260608" and items["traefik"]["where"] == "kube-system/rke2-traefik(DaemonSet)"
    assert items["traefik"]["third_party"] is True and items["ingress"]["third_party"] is False


def test_every_cluster_call_carries_context_and_detect_never_applies(kenv, capsys):
    run(kenv, capsys, tb_world())
    calls = kenv.cluster_calls()
    assert calls and all(c["ctx"] == "ctx" for c in calls), [c for c in calls if c["ctx"] != "ctx"]
    assert kenv.applies() == []
    assert all(c["args"][0] in ("get", "version") for c in calls)
    # 공통 읽기: IngressClass·DaemonSet(traefik 항목) — 한 번씩
    gets = [c["args"][:3] for c in calls if c["args"][0] == "get"]
    assert gets.count(["get", "ingressclass", "-o"]) == 1 and gets.count(["get", "ds", "-A"]) == 1, gets


def test_cluster_with_traefik_and_nothing_else_is_all_a(kenv, capsys):
    rc, doc, items = run(kenv, capsys, bare_world())
    for item in ("cnpg", "volcano", "priorityclass", "ingress", "db"):
        assert items[item]["code"] == "A" and items[item]["action"] == "install" and items[item]["apply"], item
    assert items["traefik"]["code"] == "C" and items["traefik"]["apply"] is False      # Traefik 은 A 가 없다 — 설치하지 않는다
    # 렌더 없이는 클러스터 내 레지스트리를 쓰는 사이트인지 모른다
    assert items["registry"]["code"] == "-" and items["registry"]["action"] == "skip"
    assert rc == 0


def test_empty_cluster_without_traefik_stops_on_the_traefik_item(kenv, capsys):
    """2026-11-16: DMS 는 Traefik 을 설치하지 않는다 — IngressClass 가 없으면 A(설치)가 아니라 X(운영자에게 — RKE2 는 rke2-traefik)."""
    rc, doc, items = run(kenv, capsys, empty_world())
    t = items["traefik"]
    assert (t["code"], t["action"], t["apply"], t["level"]) == ("X", "stop", False, "FAIL"), t
    assert "IngressClass traefik 없음" in t["message"] and "설치하지 않는다" in t["message"]
    assert any("클러스터 운영자" in n and "rke2-traefik" in n for n in t["notes"]), t["notes"]
    assert rc == 1 and doc["result"] == "FAIL"


def test_registry_a_when_render_says_in_cluster(kenv, capsys):
    rd = make_rendered(kenv.tmp)
    rc, doc, items = run(kenv, capsys, bare_world(), "--rendered", str(rd))
    assert items["registry"]["code"] == "A" and items["registry"]["action"] == "install"


def test_addon_install_never_fails_third_party_a_only(kenv, capsys, monkeypatch):
    monkeypatch.setenv("ADDON_INSTALL", "never")
    rc, doc, items = run(kenv, capsys, bare_world())
    for item in ("cnpg", "volcano"):
        assert items[item]["action"] == "stop" and items[item]["level"] == "FAIL"
        assert any("운영자" in n for n in items[item]["notes"])
    assert items["traefik"]["action"] == "reuse"                 # 남의 Traefik 재사용은 never 와 무관(DMS 는 원래 설치하지 않는다)
    for item in ("priorityclass", "ingress", "db"):              # 플랫폼은 늘 DMS 소유 — never 와 무관
        assert items[item]["action"] == "install"
    assert rc == 1 and doc["result"] == "FAIL"


@pytest.mark.parametrize("item", ["cnpg", "volcano"])
def test_a_with_foreign_addon_namespace_is_p(kenv, capsys, item):
    """설계 8-1 S1 「애드온 ns 를 남이 쓰는지」 — 설치할 애드온 네임스페이스를 남이 쓰면 A 가 아니라 P(--adopt 로만)."""
    w = add(bare_world(), "namespaces", nsobj(det.ADDON_NAMESPACES[item], {"team": "other"}))
    rc, doc, items = run(kenv, capsys, w)
    assert items[item]["code"] == "P" and items[item]["action"] == "stop" and rc == 1
    rc, doc, items = run(kenv, capsys, w, "--adopt", item)
    assert items[item]["action"] == "adopt" and items[item]["apply"]
    # DMS 가 만든(라벨 있는) 네임스페이스만 남은 재실행은 A 그대로
    w = add(bare_world(), "namespaces", nsobj(det.ADDON_NAMESPACES[item], owned()))
    rc, doc, items = run(kenv, capsys, w)
    assert items[item]["code"] == "A"


def test_bad_addon_install_value_is_usage(kenv, capsys, monkeypatch):
    monkeypatch.setenv("ADDON_INSTALL", "sometimes")
    rc, _, _ = run(kenv, capsys, empty_world())
    assert rc == 2 and kenv.cluster_calls() == []


# --- Traefik(2026-11-16 설계 D5 — 남의 것: C·X·U 뿐) -----------------------------------------------------------------------------

def _traefik_world(*, classes=None, workloads=None, k8s="v1.34.6"):
    w = add(empty_world(k8s), "namespaces", nsobj("dms", PRIV))
    add(w, "ingressclasses", *(classes if classes is not None else [ingressclass()]))
    for wl in (workloads if workloads is not None else [traefik_workload()]):
        add(w, "daemonsets" if wl["kind"] == "DaemonSet" else "deployments", wl)
    return w


@pytest.mark.parametrize("label, w, code, needle", [
    ("RKE2 모양", lambda: _traefik_world(), "C", "Ready 3"),
    ("IngressClass 없음(nginx 만)", lambda: _traefik_world(classes=[ingressclass("nginx", "k8s.io/ingress-nginx")]), "X",
     "IngressClass traefik 없음(있는 IngressClass: nginx(k8s.io/ingress-nginx))"),
    ("같은 이름인데 controller 다름", lambda: _traefik_world(classes=[ingressclass("traefik", "k8s.io/ingress-nginx")]), "X",
     "controller 가 k8s.io/ingress-nginx 다"),
    ("워크로드 없음", lambda: _traefik_world(workloads=[]), "U", "Traefik 워크로드"),
    ("Ready 0", lambda: _traefik_world(workloads=[traefik_workload(ready=0)]), "U", "Ready 파드가 0"),
    ("kubernetesingress 공급자 없음", lambda: _traefik_world(workloads=[traefik_workload(args=_args_without("--providers.kubernetesingress"))]),
     "X", "--providers.kubernetesingress 가 없다"),
    ("ingressClass 필터가 다른 이름", lambda: _traefik_world(workloads=[traefik_workload(
        args=RKE2_TRAEFIK_ARGS + ["--providers.kubernetesingress.ingressClass=team-a"])]), "X", "ingressClass=team-a"),
    ("websecure 진입점 없음", lambda: _traefik_world(workloads=[traefik_workload(args=_args_without("--entryPoints.websecure"))]), "X",
     "있는 진입점: metrics, traefik, web"),
    ("PROXY trustedIPs 없음", lambda: _traefik_world(workloads=[traefik_workload(
        args=_args_without("--entryPoints.websecure.proxyProtocol"))]), "C!", "Ready 3"),
    ("PROXY insecure", lambda: _traefik_world(workloads=[traefik_workload(
        args=RKE2_TRAEFIK_ARGS + ["--entryPoints.websecure.proxyProtocol.insecure=true"])]), "C!", "Ready 3"),
    # 손 설치(Deployment)·소문자 플래그·http.tls 없음 — 판정은 같다(router.tls 어노테이션이 TLS 를 켠다)
    ("Deployment·소문자 플래그", lambda: _traefik_world(workloads=[traefik_workload(
        ns="traefik", name="traefik", kind="Deployment", image="docker.io/traefik:v3.6.2", ready=2,
        args=["--entrypoints.websecure.address=:8443", "--providers.kubernetesingress",
              "--entrypoints.websecure.proxyprotocol.trustedips=10.0.0.0/8"])]), "C", "traefik/traefik(Deployment)"),
    ("정적 설정이 configFile", lambda: _traefik_world(workloads=[traefik_workload(args=["--configFile=/config/traefik.yml"])]), "C!",
     "Ready 3"),
    # 레지스트리 호스트 이름에 traefik 이 있어도 이미지 이름이 아니면 Traefik 이 아니다
    ("이름만 traefik 인 남의 이미지", lambda: _traefik_world(workloads=[traefik_workload(
        image="traefik-mirror.corp/team/nginx:1.27")]), "U", "Traefik 워크로드"),
])
def test_traefik(kenv, capsys, label, w, code, needle):
    rc, doc, items = run(kenv, capsys, w())
    t = items["traefik"]
    assert t["code"] == code and needle in t["message"], (label, t)
    assert t["apply"] is False and t["action"] in ("reuse", "stop", "verify"), t        # 어떤 판정이든 Traefik 을 적용하지 않는다
    if code == "X":
        assert t["action"] == "stop" and t["level"] == "FAIL" and any("운영자" in n for n in t["notes"]), t
    if code == "U":
        assert t["action"] == "verify" and t["level"] == "WARN" and any("ingress-probe" in n for n in t["notes"]), t


def test_traefik_notes_and_warnings_from_the_static_config(kenv, capsys):
    rc, doc, items = run(kenv, capsys, _traefik_world())
    t = items["traefik"]
    assert t["warnings"] == [] and t["level"] == "PASS"
    notes = " | ".join(t["notes"])
    assert "진입점 websecure address :8443/tcp" in notes and "proxyProtocol.trustedIPs=12.201.53.242,12.201.53.243" in notes
    assert "readTimeout 기본 60s" in notes and "http.tls 가 없다" not in notes
    # PROXY trustedIPs 가 없으면 경고(LB 가 PROXY v2 를 붙이면 연결이 깨진다), http.tls 가 없으면 근거만
    rc, doc, items = run(kenv, capsys, _traefik_world(workloads=[traefik_workload(
        args=_args_without("--entryPoints.websecure.proxyProtocol", "--entryPoints.websecure.http.tls",
                           extra=["--entryPoints.websecure.transport.respondingTimeouts.readTimeout=600s",
                                  "--entryPoints.websecure.forwardedHeaders.insecure=true"]))]))
    t = items["traefik"]
    assert t["code"] == "C!" and rc == 3
    assert any("trustedIPs 가 없다" in x and "PROXY 바이트가 TLS 앞에" in x for x in t["warnings"]), t["warnings"]
    notes = " | ".join(t["notes"])
    assert "http.tls 가 없다" in notes and "router.tls" in notes and "readTimeout 600s" in notes and "forwardedHeaders.insecure=true" in notes
    assert "DMS 는 traefik 을(를) 바꾸지 않는다" in notes and "DMS 고정" not in notes          # 고정 버전이 없는 남의 것 — 버전이 아니라 설정을


def test_traefik_entrypoint_and_class_come_from_the_render(kenv, capsys):
    args = ["--entryPoints.https.address=:443/tcp", "--providers.kubernetesingress.ingressClass=edge",
            "--entryPoints.https.proxyProtocol.trustedIPs=10.0.0.1/32"]
    w = _traefik_world(classes=[ingressclass("edge")], workloads=[traefik_workload(args=args)])
    rc, doc, items = run(kenv, capsys, w)                                           # 기본(traefik·websecure)이면 X
    assert items["traefik"]["code"] == "X"
    rd = make_rendered(kenv.tmp, INGRESS_CLASS="edge", TRAEFIK_ENTRYPOINT="https")
    rc, doc, items = run(kenv, capsys, w, "--rendered", str(rd))
    assert items["traefik"]["code"] == "C", items["traefik"]
    assert "class edge, 진입점 https" in items["traefik"]["message"]


def test_two_traefiks_for_the_class_warn(kenv, capsys):
    w = _traefik_world(workloads=[traefik_workload(), traefik_workload(ns="edge", name="traefik", ready=1)])
    rc, doc, items = run(kenv, capsys, w)
    t = items["traefik"]
    assert t["code"] == "C!" and any("둘 이상" in x for x in t["warnings"]) and "Ready 4" in t["message"], t


def test_traefik_is_never_installed_upgraded_or_adopted(kenv, capsys):
    for args in ([], ["--upgrade-addon", "traefik"], ["--adopt", "traefik"]):
        rc, doc, items = run(kenv, capsys, tb_world(), *args)
        assert items["traefik"]["action"] == "reuse" and items["traefik"]["apply"] is False, args


@pytest.mark.parametrize("resource", ["ingressclasses", "daemonsets"])
def test_traefik_read_forbidden_is_question_mark_for_that_item_only(kenv, capsys, resource):
    w = tb_world()
    w["errors"][resource] = "Error from server (Forbidden): %s is forbidden" % resource
    rc, doc, items = run(kenv, capsys, w)
    assert items["traefik"]["code"] == "?" and items["traefik"]["level"] == "UNKNOWN" and rc == 4, items["traefik"]
    assert items["cnpg"]["code"] == "D=" and items["ingress"]["code"] == "D="            # 다른 항목은 그대로 판정(공통 실패로 번지지 않는다)


# --- CNPG ---------------------------------------------------------------------------------------------------------------------

def _cnpg_world(*deps, crd_present=True, extra=None):
    w = add(bare_world(), "namespaces", nsobj("dms", PRIV))
    if crd_present:
        add(w, "crds", crd(det.CNPG_CRD))
    for d in deps:
        add(w, "deployments", d)
        add(w, "namespaces", nsobj(d["metadata"]["namespace"]))
    for kind, obj in extra or ():
        add(w, kind, obj)
    return w


@pytest.mark.parametrize("label,w", [
    ("Helm 오퍼레이터(다른 ns)", lambda: _cnpg_world(cnpg_op("postgres-operator", "1.29.3", is_owned=False))),
    ("라벨 없음·이름 다름(이미지로 찾음)", lambda: _cnpg_world(cnpg_op("db-team", "1.30.0", is_owned=False, name="pg-op", labels={}))),
    ("CRD 만(오퍼레이터 없음)", lambda: _cnpg_world()),
    ("오퍼레이터만(CRD 없음)", lambda: _cnpg_world(cnpg_op("x", "1.30.1", is_owned=False), crd_present=False)),
    ("오퍼레이터 둘", lambda: _cnpg_world(cnpg_op("a", "1.30.1", is_owned=False), cnpg_op("b", "1.29.0", is_owned=False))),
])
def test_cnpg_anywhere_never_installs_a_second(kenv, capsys, label, w):
    """「CNPG 가 어디에든 있으면 두 번째를 설치하지 않음」(설계 #34·9 ⑦) — 흔적이 하나라도 있으면 A·install 이 아니다."""
    rc, doc, items = run(kenv, capsys, w())
    assert items["cnpg"]["code"] != "A" and items["cnpg"]["action"] != "install", (label, items["cnpg"])


def test_cnpg_foreign_compatible_is_c_reuse(kenv, capsys):
    rc, doc, items = run(kenv, capsys, _cnpg_world(cnpg_op("postgres-operator", "1.29.3", is_owned=False)))
    c = items["cnpg"]
    assert (c["code"], c["action"], c["apply"], c["level"]) == ("C", "reuse", False, "PASS")
    assert c["installed"] == "1.29.3" and c["where"] == "postgres-operator/cnpg-controller-manager"


def test_cnpg_crd_only_is_p_and_adoptable(kenv, capsys):
    rc, doc, items = run(kenv, capsys, _cnpg_world())
    assert items["cnpg"]["code"] == "P" and items["cnpg"]["action"] == "stop" and rc == 1
    rc, doc, items = run(kenv, capsys, _cnpg_world(), "--adopt", "cnpg")
    assert items["cnpg"]["action"] == "adopt" and items["cnpg"]["apply"] and items["cnpg"]["level"] == "WARN" and rc == 3


def test_cnpg_two_operators_is_p_even_with_adopt(kenv, capsys):
    w = _cnpg_world(cnpg_op("a", "1.30.1", is_owned=False), cnpg_op("b", "1.29.0", is_owned=False))
    rc, doc, items = run(kenv, capsys, w, "--adopt", "cnpg")
    assert items["cnpg"]["code"] == "P" and items["cnpg"]["action"] == "stop" and rc == 1
    assert "a/cnpg-controller-manager" in items["cnpg"]["message"] and "b/cnpg-controller-manager" in items["cnpg"]["message"]


@pytest.mark.parametrize("env,extra,code", [
    ([{"name": "WATCH_NAMESPACE", "value": "team-a"}], None, "X"),
    ([{"name": "WATCH_NAMESPACE", "value": "team-a, dms"}], None, "C"),
    ([], [("configmaps", cm("op", "cnpg-controller-manager-config", {"WATCH_NAMESPACE": "team-a"}))], "X"),
    # Secret 이 ConfigMap 을 덮는다(CNPG 설정 우선순위)
    ([], [("configmaps", cm("op", "cnpg-controller-manager-config", {"WATCH_NAMESPACE": "team-a"})),
          ("secrets", secret("op", "cnpg-controller-manager-config", {"WATCH_NAMESPACE": "dms,team-a"}))], "C"),
    ([{"name": "WATCH_NAMESPACE", "valueFrom": {"fieldRef": {"fieldPath": "metadata.annotations['olm.targetNamespaces']"}}}], None, "C!"),
])
def test_cnpg_watch_namespace(kenv, capsys, env, extra, code):
    w = _cnpg_world(cnpg_op("op", "1.30.1", is_owned=False, env=env), extra=extra)
    rc, doc, items = run(kenv, capsys, w)
    assert items["cnpg"]["code"] == code, items["cnpg"]
    if code == "X":
        assert "watch" in items["cnpg"]["message"] and rc == 1


def test_cnpg_watch_secret_is_read_by_key_only(kenv, capsys):
    """Secret 은 WATCH_NAMESPACE 키 하나만 jsonpath 로 읽는다 — 다른 값은 받지도 않는다(비밀번호를 찍지 않는다)."""
    w = _cnpg_world(cnpg_op("op", "1.30.1", is_owned=False))
    run(kenv, capsys, w)
    secret_reads = [c["args"] for c in kenv.cluster_calls() if c["args"][:2] == ["get", "secret"]]
    assert secret_reads and all(a[-2:] == ["-o", "jsonpath={.data.WATCH_NAMESPACE}"] for a in secret_reads)


def test_cnpg_watch_secret_forbidden_is_warning_not_unknown_item(kenv, capsys):
    w = _cnpg_world(cnpg_op("op", "1.30.1", is_owned=False))
    w["errors"]["secrets"] = 'Error from server (Forbidden): secrets is forbidden: User "x" cannot get resource "secrets"'
    rc, doc, items = run(kenv, capsys, w)
    assert items["cnpg"]["code"] == "C!" and any("watch" in x for x in items["cnpg"]["warnings"])


@pytest.mark.parametrize("version,is_owned,image,code,level", [
    ("1.24.0", False, None, "X", "FAIL"),                       # 하한 1.25(dataDurability)
    ("1.27.2", False, None, "C!", "WARN"),                      # 지원 minor(1.29·1.30) 밖 — EOL
    ("1.31.0", False, None, "C!", "WARN"),                      # 고정보다 새 minor — 검증 범위 밖
    ("1.29.4", False, None, "C", "PASS"),
    ("?", False, "ghcr.io/cloudnative-pg/cloudnative-pg@" + DIGEST, "U", "WARN"),   # digest 만·버전 라벨 없음
    ("1.29.2", True, None, "D<", "WARN"),
    ("1.30.2", True, None, "D>", "WARN"),
])
def test_cnpg_versions(kenv, capsys, version, is_owned, image, code, level):
    labels = None if image is None else {"app.kubernetes.io/name": "cloudnative-pg"}
    w = _cnpg_world(cnpg_op("cnpg-system", version, is_owned=is_owned, image=image, labels=labels))
    rc, doc, items = run(kenv, capsys, w)
    assert (items["cnpg"]["code"], items["cnpg"]["level"]) == (code, level), items["cnpg"]
    assert rc == det.EXIT[level]
    if code == "U":
        assert items["cnpg"]["action"] == "verify" and any("버전을 모른다" in n for n in items["cnpg"]["notes"])
    if code == "C!":
        assert any("소유자" in n for n in items["cnpg"]["notes"])


def test_cnpg_upgrade_one_minor_at_a_time(kenv, capsys):
    rc, doc, items = run(kenv, capsys, _cnpg_world(cnpg_op(version="1.29.2")), "--upgrade-addon", "cnpg")
    assert items["cnpg"]["action"] == "upgrade" and items["cnpg"]["apply"] and rc == 0
    rc, doc, items = run(kenv, capsys, _cnpg_world(cnpg_op(version="1.28.0")), "--upgrade-addon", "cnpg")
    assert items["cnpg"]["code"] == "D<" and items["cnpg"]["action"] == "stop" and rc == 1
    assert any("minor 는 하나씩" in n for n in items["cnpg"]["notes"])
    rc, doc, items = run(kenv, capsys, _cnpg_world(cnpg_op(version="1.30.2")), "--upgrade-addon", "cnpg")
    assert items["cnpg"]["code"] == "D>" and items["cnpg"]["action"] == "keep"      # 다운그레이드하지 않는다
    rc, doc, items = run(kenv, capsys, _cnpg_world(cnpg_op(version="1.30.1")), "--upgrade-addon", "cnpg")
    assert items["cnpg"]["code"] == "D=" and items["cnpg"]["action"] == "reapply"


def test_cnpg_owned_watch_excluding_dms_is_x(kenv, capsys):
    w = _cnpg_world(cnpg_op(env=[{"name": "WATCH_NAMESPACE", "value": "other"}]))
    rc, doc, items = run(kenv, capsys, w)
    assert items["cnpg"]["code"] == "X"


# --- Volcano --------------------------------------------------------------------------------------------------------------------

def _volcano_world(k8s="v1.34.6", crds=det.VOLCANO_CRDS, deps=None):
    w = add(bare_world(k8s), "namespaces", nsobj("dms", PRIV), nsobj("volcano-system"))
    add(w, "crds", *[crd(n) for n in crds])
    add(w, "deployments", *(volcano_deploys() if deps is None else deps))
    return w


@pytest.mark.parametrize("w,code", [
    # 상류 라벨은 app=volcano-scheduler(preflight-cluster.sh 의 app.kubernetes.io/name 셀렉터는 늘 빈 결과였다)
    (lambda: _volcano_world(), "C"),
    (lambda: _volcano_world(deps=volcano_deploys(sched_name="vc-sched-x", sched_labels={})), "C"),       # 이름·라벨 없이 이미지로
    (lambda: _volcano_world(deps=volcano_deploys(sched_args=["--scheduler-name=team-sched"])), "X"),
    (lambda: _volcano_world(deps=volcano_deploys(sched_args=["--scheduler-name", "team", "--scheduler-name=volcano"])), "C"),
    (lambda: _volcano_world(deps=volcano_deploys("v1.14.2")), "X"),
    (lambda: _volcano_world("v1.36.1"), "X"),                                                          # 1.36 은 v1.16 부터
    (lambda: _volcano_world("v1.36.1", deps=volcano_deploys("v1.16.0")), "C"),
    (lambda: _volcano_world(deps=volcano_deploys("v1.17.0")), "C!"),
    (lambda: _volcano_world(deps=volcano_deploys("v1.16.0", is_owned=True)), "D="),
    (lambda: _volcano_world(deps=[]), "P"),                                                            # CRD 만
    (lambda: _volcano_world(crds=det.VOLCANO_CRDS[:2]), "P"),
    (lambda: _volcano_world(deps=volcano_deploys() + [volcano_deploys(ns="vc2")[0]]), "P"),            # 스케줄러 둘
])
def test_volcano(kenv, capsys, w, code):
    rc, doc, items = run(kenv, capsys, w())
    assert items["volcano"]["code"] == code, items["volcano"]


def test_volcano_c_is_never_reapplied(kenv, capsys):
    for args in ([], ["--upgrade-addon", "volcano"], ["--adopt", "volcano"]):
        rc, doc, items = run(kenv, capsys, _volcano_world(), *args)
        assert items["volcano"]["action"] == "reuse" and items["volcano"]["apply"] is False, args


def test_volcano_owned_old_on_136_is_upgrade_path_not_x(kenv, capsys):
    w = _volcano_world("v1.36.0", deps=volcano_deploys("v1.15.0", is_owned=True))
    rc, doc, items = run(kenv, capsys, w)
    assert items["volcano"]["code"] == "D<" and any("v1.16" in x for x in items["volcano"]["warnings"])
    rc, doc, items = run(kenv, capsys, w, "--upgrade-addon", "volcano")
    assert items["volcano"]["action"] == "upgrade"


def test_volcano_crd_only_adoptable(kenv, capsys):
    rc, doc, items = run(kenv, capsys, _volcano_world(deps=[]), "--adopt", "volcano")
    assert items["volcano"]["action"] == "adopt"


# --- 플랫폼(늘 DMS 소유 — 조용히 다시 만들지 않는다) ---------------------------------------------------------------------------------

def test_priorityclass_foreign_is_p_and_adopt(kenv, capsys):
    w = tb_world()
    w["objects"]["priorityclasses"][0]["metadata"]["labels"] = {}
    rc, doc, items = run(kenv, capsys, w)
    assert items["priorityclass"]["code"] == "P" and rc == 1
    rc, doc, items = run(kenv, capsys, w, "--adopt", "priorityclass")
    assert items["priorityclass"]["action"] == "adopt" and rc == 3


def test_priorityclass_immutable_value_differs_is_x_even_with_adopt(kenv, capsys):
    w = tb_world()
    w["objects"]["priorityclasses"][0]["value"] = 2000
    rc, doc, items = run(kenv, capsys, w, "--adopt", "priorityclass")
    assert items["priorityclass"]["code"] == "X" and items["priorityclass"]["action"] == "stop" and rc == 1


def test_registry_version_label(kenv, capsys):
    w = tb_world()
    dep = next(d for d in w["objects"]["deployments"] if d["metadata"]["name"] == "dms-registry")
    dep["metadata"]["labels"][det.ADDON_VERSION] = "3.0.0"
    rc, doc, items = run(kenv, capsys, w)
    assert items["registry"]["code"] == "D<" and items["registry"]["action"] == "keep"
    dep["metadata"]["labels"] = {}
    rc, doc, items = run(kenv, capsys, w)
    assert items["registry"]["code"] == "P"


def test_registry_cluster_ip_contract(kenv, capsys):
    rd = make_rendered(kenv.tmp)
    w = tb_world()
    w["objects"]["services"][0]["spec"]["clusterIP"] = "10.112.0.51"
    rc, doc, items = run(kenv, capsys, w, "--rendered", str(rd))
    assert items["registry"]["code"] == "X" and "clusterIP" in items["registry"]["message"]
    # 레지스트리가 아직 없는데 그 IP 를 남의 Service 가 쓴다 → 충돌
    w = drop(drop(tb_world(), "services", "dms-registry"), "deployments", "dms-registry")
    add(w, "services", {"kind": "Service", "metadata": meta("other", "team"), "spec": {"clusterIP": "10.112.0.50"}})
    rc, doc, items = run(kenv, capsys, w, "--rendered", str(rd))
    assert items["registry"]["code"] == "X" and "충돌" in items["registry"]["message"] and "team/other" in items["registry"]["message"]


def test_registry_not_applicable_on_external_site(kenv, capsys):
    rd = make_rendered(kenv.tmp, REGISTRY_MODE="external", REGISTRY_CLUSTER_IP="", PLATFORM_LAYERS='"priorityclass"')
    rc, doc, items = run(kenv, capsys, bare_world(), "--rendered", str(rd))
    assert items["registry"]["code"] == "-" and items["registry"]["action"] == "skip"


def test_ingress_layer_codes(kenv, capsys):
    """platform/ingress(2026-11-16) — Ingress dms/dms-portal-traefik: 없으면 A, DMS 라벨이면 D=(SSA 멱등 재적용), 같은 이름에 DMS 라벨이 없으면
    P(--adopt ingress 로만)."""
    rc, doc, items = run(kenv, capsys, tb_world())
    i = items["ingress"]
    assert (i["code"], i["action"], i["apply"], i["where"]) == ("D=", "reapply", True, "dms/dms-portal-traefik"), i
    assert "host dms-k8s.local" in i["message"] and i["installed"] == "class traefik"
    rc, doc, items = run(kenv, capsys, drop(tb_world(), "ingresses", det.INGRESS_NAME))
    assert items["ingress"]["code"] == "A" and items["ingress"]["action"] == "install"
    w = tb_world()
    w["objects"]["ingresses"] = [dms_ingress(is_owned=False)]
    rc, doc, items = run(kenv, capsys, w)
    assert items["ingress"]["code"] == "P" and items["ingress"]["action"] == "stop" and rc == 1
    rc, doc, items = run(kenv, capsys, w, "--adopt", "ingress")
    assert items["ingress"]["action"] == "adopt" and items["ingress"]["apply"] and rc == 3
    # 다른 class 로 남은 DMS Ingress — 바꿀 수 있는 필드라 SSA 가 맞춘다(근거만)
    w = tb_world()
    w["objects"]["ingresses"] = [dms_ingress(cls="nginx")]
    rc, doc, items = run(kenv, capsys, w)
    assert items["ingress"]["code"] == "D=" and any("ingressClassName nginx → 렌더 traefik" in n for n in items["ingress"]["notes"])


def test_ingress_before_the_dms_namespace_exists_is_a_without_reading(kenv, capsys):
    rc, doc, items = run(kenv, capsys, bare_world())
    assert items["ingress"]["code"] == "A" and "S2" in items["ingress"]["message"]
    assert not [c for c in kenv.cluster_calls() if "ingresses.networking.k8s.io" in c["args"]]


@pytest.mark.parametrize("tag,code,upgrade_action", [
    ("17.11-202611020810-minimal-trixie", "D=", "reapply"),
    ("17.10-202609010000-minimal-trixie", "D<", "upgrade"),
    ("17.11-202610010000-minimal-trixie", "D<", "upgrade"),          # 같은 minor·옛 빌드
    ("16.9-202609010000-minimal-trixie", "D<", "stop"),               # PG major 는 SSA 로 바꾸지 않는다
    ("18.1-202611010000-minimal-trixie", "D>", "keep"),
])
def test_db_pg_image(kenv, capsys, tag, code, upgrade_action):
    w = tb_world()
    w["objects"]["clusters"] = [pg_cluster(tag)]
    rc, doc, items = run(kenv, capsys, w)
    assert items["db"]["code"] == code
    rc, doc, items = run(kenv, capsys, w, "--upgrade-addon", "db")
    assert items["db"]["action"] == upgrade_action


def test_db_foreign_sc_is_p(kenv, capsys):
    w = tb_world()
    w["objects"]["storageclasses"][0]["metadata"]["labels"] = {}
    rc, doc, items = run(kenv, capsys, w)
    assert items["db"]["code"] == "P" and "StorageClass" in items["db"]["message"]


def test_db_pv_check_mismatch_is_x(kenv, capsys):
    """pg-path-probe.py 의 pv-check 를 그대로 부른다 — 렌더 SHARED_DIR 와 다른 경로의 PV 는 조용히 다시 만들지 않는다(X)."""
    rd = make_rendered(kenv.tmp, SHARED_DIR="/other")
    rc, doc, items = run(kenv, capsys, tb_world(), "--rendered", str(rd))
    assert items["db"]["code"] == "X" and "pv-check" in items["db"]["message"] and rc == 1
    rd = make_rendered(kenv.tmp, SHARED_DIR="/dms_shared", PG_STORAGE_SIZE="30Gi")
    rc, doc, items = run(kenv, capsys, tb_world(), "--rendered", str(rd))
    assert items["db"]["code"] == "X" and "capacity" in items["db"]["message"]
    rd = make_rendered(kenv.tmp)
    rc, doc, items = run(kenv, capsys, tb_world(), "--rendered", str(rd))
    assert items["db"]["code"] == "D="


def test_db_released_pv_is_warning(kenv, capsys):
    w = tb_world()
    sc, pvs = pg_volumes(phases={"dms-pgdata-05": "Released"})
    w["objects"]["persistentvolumes"] = pvs
    rc, doc, items = run(kenv, capsys, w)
    assert items["db"]["code"] == "D=" and items["db"]["level"] == "WARN" and rc == 3


def test_db_volumes_without_cluster_is_a(kenv, capsys):
    w = drop(tb_world(), "clusters", "dms-pg")
    rc, doc, items = run(kenv, capsys, w)
    assert items["db"]["code"] == "A" and "이미 있음" in items["db"]["message"]


# --- 읽기 실패·사용법 -----------------------------------------------------------------------------------------------------------

def test_common_read_forbidden_is_unknown_exit_4(kenv, capsys):
    w = tb_world()
    w["errors"]["deployments"] = "Error from server (Forbidden): deployments.apps is forbidden"
    kenv.wfile.write_text(json.dumps(w))
    rc = det.main(["detect", "--context", "ctx", "--versions-env", str(VERSIONS)])
    out = capsys.readouterr().out
    assert rc == 4 and out.strip().splitlines()[-1] == "RESULT: UNKNOWN"


def test_item_read_forbidden_is_question_mark(kenv, capsys):
    w = tb_world()
    w["errors"]["priorityclasses"] = "Error from server (Forbidden): priorityclasses is forbidden"
    rc, doc, items = run(kenv, capsys, w)
    assert items["priorityclass"]["code"] == "?" and items["priorityclass"]["level"] == "UNKNOWN" and rc == 4
    assert items["cnpg"]["code"] == "D="           # 다른 항목은 그대로 판정


@pytest.mark.parametrize("argv,needs_rendered", [
    (["detect"], False),                                         # --context 없음
    (["detect", "--context", "nope"], False),                    # kubeconfig 에 없는 컨텍스트
    (["detect", "--context", "ctx", "--adopt", "bogus"], False),
    (["detect", "--context", "ctx", "--upgrade-addon", "envoy-gateway"], False),   # 2026-11-16 에 없앤 항목
    (["plan", "--context", "ctx"], False),                       # plan 은 --rendered 필수
    (["plan", "--context", "ctx"], True),                        # 렌더의 kube-context 와 다름
])
def test_usage_errors_stop_before_cluster(kenv, capsys, argv, needs_rendered):
    kenv.wfile.write_text(json.dumps(tb_world()))
    if needs_rendered:
        argv = argv + ["--rendered", str(make_rendered(kenv.tmp, ctx="dmsk8s"))]
    rc = det.main(argv + ["--versions-env", str(VERSIONS)])
    assert rc == 2 and kenv.cluster_calls() == []
    assert capsys.readouterr().out.strip().endswith("RESULT: UNKNOWN")


@pytest.mark.parametrize("name", ["gateway-api", "envoy-gateway", "gateway"])
def test_retired_item_names_say_what_replaced_them(kenv, capsys, name):
    kenv.wfile.write_text(json.dumps(tb_world()))
    rc = det.main(["detect", "--context", "ctx", "--adopt", "cnpg,%s" % name, "--versions-env", str(VERSIONS)])
    err = capsys.readouterr().err
    assert rc == 2 and "2026-11-16" in err and "traefik" in err and "ingress" in err, err


def test_kube_context_env_is_used(kenv, capsys, monkeypatch):
    monkeypatch.setenv("KUBE_CONTEXT", "ctx")
    kenv.wfile.write_text(json.dumps(tb_world()))
    rc = det.main(["detect", "--versions-env", str(VERSIONS)])
    assert rc == 0 and all(c["ctx"] == "ctx" for c in kenv.cluster_calls())


# --- plan(S3) -------------------------------------------------------------------------------------------------------------------

def _plan(kenv, capsys, w, rd, *args):
    rc, doc, items = run(kenv, capsys, w, "--rendered", str(rd), *args, mode="plan")
    layers = {r["layer"]: r for r in doc["layers"]}
    return rc, doc, items, layers


def test_plan_testbed_shape(kenv, capsys):
    rd = make_rendered(kenv.tmp)
    rc, doc, items, layers = _plan(kenv, capsys, tb_world(), rd)
    assert rc == 0 and doc["result"] == "PASS"
    for layer in ("platform/priorityclass", "platform/registry", "platform/ingress", "platform/db-volumes", "platform/db-cluster",
                  "platform/db-backup", "vcjob", "app"):
        assert layers[layer]["status"] == "PASS", layers[layer]
    assert layers["addons/cnpg"]["status"] == "SKIP"                       # D= no-op
    assert layers["addons/volcano"]["status"] == "SKIP"
    assert not [k for k in layers if "gateway" in k], list(layers)          # 2026-11-16: Gateway API·EG·platform/gateway 층 없음
    applies = kenv.applies()
    assert applies and all("--dry-run=server" in c["args"] for c in applies)
    ssa = [c["args"] for c in applies if c["args"][0] == "apply" and c["args"][-1] != str(rd / "app")]
    assert ssa and all("--server-side" in a and "--field-manager=dms-install" in a for a in ssa)
    assert [a for a in ssa if a[-1] == str(rd / "platform/ingress")]       # DMS 소유 입구 층은 늘 dry-run(SSA 멱등)
    app = [c["args"] for c in applies if c["args"][-1] == str(rd / "app")]
    assert app and "--server-side" not in app[0]          # 앱은 client-side(포탈 릴리스가 strategic-merge)
    vc = next(c for c in applies if c["args"][0] == "create")
    job = json.loads(vc["stdin"])
    assert job["kind"] == "Job" and job["spec"]["schedulerName"] == "volcano" and job["spec"]["queue"] == "dms-data"
    assert job["metadata"]["namespace"] == "dms"
    assert any("최종 판정: platform/ingress server dry-run 통과" in n and "ingress-probe" in n for n in items["traefik"]["notes"])


def test_plan_c_never_calls_apply_on_third_party_layers(kenv, capsys):
    """「C 에서는 apply 를 부르지 않음」(설계 9 ⑦) — 남이 설치한 CNPG·Volcano 층에는 dry-run 조차 부르지 않고, Traefik 은 층 자체가 없다.
    CNPG C 는 platform/db-cluster 의 dry-run 이 최종 판정이다."""
    w = add(bare_world(), "namespaces", nsobj("dms", PRIV))
    add(w, "crds", crd(det.CNPG_CRD), *[crd(n) for n in det.VOLCANO_CRDS])
    add(w, "deployments", cnpg_op("postgres-operator", "1.29.3", is_owned=False), *volcano_deploys())
    add(w, "namespaces", nsobj("postgres-operator"), nsobj("volcano-system"))
    rd = make_rendered(kenv.tmp, layers=ALL_LAYERS + ("addons/volcano",))
    rc, doc, items, layers = _plan(kenv, capsys, w, rd)
    assert {k: items[k]["code"] for k in det.THIRD_PARTY} == {"traefik": "C", "cnpg": "C", "volcano": "C"}
    touched = " ".join(" ".join(c["args"]) for c in kenv.applies())
    for layer in ("addons/cnpg", "addons/volcano"):
        assert layer not in touched, layer
        assert layers[layer]["status"] == "SKIP"
    assert "traefik" not in touched.lower()
    assert layers["platform/ingress"]["status"] == "PASS" and layers["platform/db-cluster"]["status"] == "PASS"
    assert any("최종 판정" in n for n in items["cnpg"]["notes"])


def test_plan_traefik_u_stays_warn_and_points_to_the_probe(kenv, capsys):
    w = tb_world()
    w["objects"]["daemonsets"][0]["status"]["numberReady"] = 0
    rc, doc, items, layers = _plan(kenv, capsys, w, make_rendered(kenv.tmp))
    t = items["traefik"]
    assert t["code"] == "U" and t["level"] == "WARN" and rc == 3, t
    assert layers["platform/ingress"]["status"] == "PASS"
    assert any("WARN(Traefik 은 S8 ingress-probe 가 본다)" in n for n in t["notes"]), t["notes"]


def test_plan_first_install_namespace_trap_is_skip_not_fail(kenv, capsys):
    """첫 설치: server dry-run 은 같은 묶음의 Namespace 를 저장하지 않는다(kubernetes#83562) — 「ns will be created」 SKIP 이지 FAIL 이 아니다.
    아직 없는 애드온 CRD 의 no matches for kind 도 같다. 오류 문구는 2026-11-03 dmsk8s 실측 그대로다."""
    w = bare_world()
    w["kustomize"] = {
        "addons/cnpg": "apiVersion: v1\nkind: Namespace\nmetadata:\n  labels:\n    a: b\n  name: cnpg-system\n---\n"
                       "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: cnpg-controller-manager\n  namespace: cnpg-system\n",
    }
    mapping = ('resource mapping not found for name: "dms-pg" namespace: "dms" from "%s": no matches for kind "Cluster" in version '
               '"postgresql.cnpg.io/v1"\nensure CRDs are installed first\n')
    w["dry_run"] = [
        {"match": "addons/cnpg", "stderr": 'Error from server (NotFound): namespaces "cnpg-system" not found\n'},
        {"match": "platform/db-cluster", "stderr": mapping % "x" + 'Error from server (NotFound): namespaces "dms" not found\n'},
        {"match": "platform/ingress", "stderr": 'Error from server (NotFound): namespaces "dms" not found\n'},
        {"match": "/app", "stderr": 'resource mapping not found for name: "dms-data" namespace: "" from "x": no matches for kind '
                                   '"Queue" in version "scheduling.volcano.sh/v1beta1"\nensure CRDs are installed first\n'
                                   'Error from server (NotFound): namespaces "dms" not found\n'},
    ]
    rd = make_rendered(kenv.tmp)
    rc, doc, items, layers = _plan(kenv, capsys, w, rd)
    assert layers["addons/cnpg"]["status"] == "SKIP" and "ns cnpg-system will be created" in layers["addons/cnpg"]["detail"]
    assert layers["platform/db-cluster"]["status"] == "SKIP" and "cnpg 설치가 만든다" in layers["platform/db-cluster"]["detail"]
    assert layers["platform/ingress"]["status"] == "SKIP" and "ns dms will be created" in layers["platform/ingress"]["detail"]
    assert layers["app"]["status"] == "SKIP"
    assert layers["vcjob"]["status"] == "SKIP"
    assert rc == 0 and doc["result"] == "PASS"


_MIGRATE_IMMUTABLE = ('The Job "dms-migrate" is invalid: spec.template: Invalid value: {"labels":{"app.kubernetes.io/name":"dms-migrate"},'
                      '"Spec":{"Containers":[{"Name":"migrate","Image":"10.112.0.50:5000/dms:d20"}]}}: field is immutable\n')


def test_plan_app_tag_bump_is_not_blocked_by_the_finished_migrate_job(kenv, capsys):
    """2026-11-03 ⑦ 리뷰 1(high): 끝난 dms-migrate Job 은 spec 을 바꿀 수 없어 태그를 올린 앱 층 dry-run 이 늘 `field is immutable` 이다 —
    S11 이 apply 전에 지우고 다시 만드는 그 Job 이라 계획(S3·--plan)이 막으면 앱 업그레이드가 설치기로 진행되지 않았다(dmsk8s d19→d20 실측 문구)."""
    w = tb_world()
    w["dry_run"] = [{"match": "/app", "stderr": _MIGRATE_IMMUTABLE}]
    rc, doc, items, layers = _plan(kenv, capsys, w, make_rendered(kenv.tmp))
    assert layers["app"]["status"] == "PASS" and "S11 이 지우고 다시 만든다" in layers["app"]["detail"], layers["app"]
    assert rc == 0 and doc["result"] == "PASS"


def test_plan_migrate_note_does_not_hide_other_app_errors(kenv, capsys):
    w = tb_world()
    w["dry_run"] = [{"match": "/app", "stderr": _MIGRATE_IMMUTABLE + 'The Deployment "dms-api" is invalid: spec.replicas: Invalid value: -1\n'}]
    rc, doc, items, layers = _plan(kenv, capsys, w, make_rendered(kenv.tmp))
    assert layers["app"]["status"] == "FAIL" and "dms-api" in layers["app"]["detail"] and rc == 1


def test_plan_immutable_migrate_outside_the_app_layer_is_a_real_failure(kenv, capsys):
    w = tb_world()
    w["dry_run"] = [{"match": "platform/db-backup", "stderr": _MIGRATE_IMMUTABLE}]
    rc, doc, items, layers = _plan(kenv, capsys, w, make_rendered(kenv.tmp))
    assert layers["platform/db-backup"]["status"] == "FAIL" and rc == 1


def test_namespace_flag_beats_env_dms_ns(kenv, capsys, monkeypatch):
    # install.sh·verify.sh·preflight 는 --namespace dms 를 넘긴다 — 셸에 남은 옛 DMS_NS 가 감지 ns 를 바꾸지 않게(⑦ 리뷰 1).
    monkeypatch.setenv("DMS_NS", "dms-old")
    rc, doc, items = run(kenv, capsys, tb_world(), "--namespace", "dms")
    assert doc["namespace"] == "dms" and items["registry"]["code"] != "-", items["registry"]


def test_plan_missing_namespace_nobody_creates_is_fail(kenv, capsys):
    w = tb_world()
    w["dry_run"] = [{"match": "platform/db-backup", "stderr": 'Error from server (NotFound): namespaces "backup-x" not found\n'}]
    rc, doc, items, layers = _plan(kenv, capsys, w, make_rendered(kenv.tmp))
    assert layers["platform/db-backup"]["status"] == "FAIL" and rc == 1


def test_plan_missing_crd_of_reused_addon_is_fail(kenv, capsys):
    """no matches for kind 가 설치하지 않을 애드온의 것이면 무해가 아니다(SKIP 으로 덮지 않는다)."""
    w = tb_world()
    w["dry_run"] = [{"match": "platform/db-cluster", "stderr": 'resource mapping not found for name: "dms-pg" namespace: "dms" from "x": '
                                                              'no matches for kind "Cluster" in version "postgresql.cnpg.io/v1"\n'}]
    rc, doc, items, layers = _plan(kenv, capsys, w, make_rendered(kenv.tmp))
    assert layers["platform/db-cluster"]["status"] == "FAIL" and items["cnpg"]["level"] == "FAIL" and rc == 1


def test_plan_u_with_dry_run_pass_is_warn(kenv, capsys):
    w = tb_world()
    op = next(d for d in w["objects"]["deployments"] if d["metadata"]["name"] == "cnpg-controller-manager")
    op["metadata"]["labels"] = {"app.kubernetes.io/name": "cloudnative-pg"}            # 남의 것, 버전 라벨 없음
    op["spec"]["template"]["spec"]["containers"][0]["image"] = "ghcr.io/cloudnative-pg/cloudnative-pg@" + DIGEST
    rc, doc, items, layers = _plan(kenv, capsys, w, make_rendered(kenv.tmp))
    assert items["cnpg"]["code"] == "U" and items["cnpg"]["level"] == "WARN" and rc == 3
    assert layers["platform/db-cluster"]["status"] == "PASS" and layers["addons/cnpg"]["status"] == "SKIP"
    assert any("최종 판정" in n and "WARN(버전 모름)" in n for n in items["cnpg"]["notes"])


def test_plan_u_or_c_with_webhook_failure_is_fail(kenv, capsys):
    w = tb_world()
    op = next(d for d in w["objects"]["deployments"] if d["metadata"]["name"] == "cnpg-controller-manager")
    op["metadata"]["labels"] = {"app.kubernetes.io/name": "cloudnative-pg"}
    w["dry_run"] = [{"match": "platform/db-cluster",
                     "stderr": 'Error from server (InternalError): Internal error occurred: failed calling webhook "mcluster.cnpg.io": '
                               'failed to call webhook: Post "https://cnpg-webhook-service.cnpg-system.svc:443/mutate-postgresql-cnpg-io-'
                               'v1-cluster?timeout=10s": dial tcp 10.0.0.1:443: connect: connection refused\n'}]
    rc, doc, items, layers = _plan(kenv, capsys, w, make_rendered(kenv.tmp))
    assert items["cnpg"]["code"] == "C" and items["cnpg"]["level"] == "FAIL" and rc == 1
    assert "9443" in layers["platform/db-cluster"]["detail"]


def test_plan_adopt_uses_force_conflicts(kenv, capsys):
    w = tb_world()
    w["objects"]["priorityclasses"][0]["metadata"]["labels"] = {}
    rc, doc, items, layers = _plan(kenv, capsys, w, make_rendered(kenv.tmp), "--adopt", "priorityclass")
    pc = next(c["args"] for c in kenv.applies() if c["args"][-1].endswith("platform/priorityclass"))
    assert "--force-conflicts" in pc and "--dry-run=server" in pc
    others = [c["args"] for c in kenv.applies() if not c["args"][-1].endswith("platform/priorityclass")]
    assert all("--force-conflicts" not in a for a in others)


def test_plan_stop_items_are_not_dry_run(kenv, capsys):
    w = tb_world()
    w["objects"]["priorityclasses"][0]["value"] = 5
    rc, doc, items, layers = _plan(kenv, capsys, w, make_rendered(kenv.tmp))
    assert layers["platform/priorityclass"]["status"] == "SKIP"
    assert not any(c["args"][-1].endswith("platform/priorityclass") for c in kenv.applies())


def test_plan_traefik_x_still_dry_runs_the_dms_ingress_layer_but_fails(kenv, capsys):
    """Traefik 이 X(IngressClass 없음)면 그 판정이 FAIL 사유다 — DMS Ingress 층의 dry-run 은 CRD 의존이 없어 그대로 돈다(PASS)."""
    w = tb_world()
    w["objects"]["ingressclasses"] = [ingressclass("nginx", "k8s.io/ingress-nginx")]
    rc, doc, items, layers = _plan(kenv, capsys, w, make_rendered(kenv.tmp))
    assert items["traefik"]["code"] == "X" and rc == 1 and doc["result"] == "FAIL"
    assert layers["platform/ingress"]["status"] == "PASS"


def _no_kind(name, kind, group_version):
    return ('resource mapping not found for name: "%s" namespace: "dms" from "x": no matches for kind "%s" in version "%s"\n'
            'ensure CRDs are installed first\n' % (name, kind, group_version))


def test_classify_bundled_missing_kinds_are_benign():
    """없는 kind 가 둘 이상이면 kubectl 이 오류를 `[a, b, c]` 로 묶어 마지막 줄이 「ensure CRDs are installed first]」다(2026-11-13 OA 실측 —
    첫 설치에서 애드온 CRD 여럿이 한꺼번에 없을 때). 그 줄을 진짜 오류로 세면 S3 계획이 정상 첫 설치에서 FAIL 이었다."""
    one = 'resource mapping not found for name: "dms-x" namespace: "dms" from "x": no matches for kind "%s" in version "%s"'
    kinds = (("Queue", "scheduling.volcano.sh/v1beta1"), ("PodGroup", "scheduling.volcano.sh/v1beta1"), ("Job", "batch.volcano.sh/v1alpha1"))
    err = "error: [" + "\nensure CRDs are installed first, ".join(one % kv for kv in kinds) + "\nensure CRDs are installed first]\n"
    benign, real, deps = det._classify(err, {"dms": "S2"}, {"volcano"})
    assert real == []
    assert benign == sorted("CRD %s(%s) 는 volcano 설치가 만든다" % (k, gv.split("/")[0]) for k, gv in kinds)
    # 묶음 끝 「]」만 무해다 — 진짜 오류 줄은 그대로 남는다.
    benign, real, deps = det._classify(err + "Error from server (Forbidden): something real\n", {"dms": "S2"}, {"volcano"})
    assert real == ["Error from server (Forbidden): something real"]


def _cnpg_p_world():
    """T9 P4 의 모양(2026-11-16 판 — EG 대신 CNPG): DMS DB 층은 그대로인데 cnpg-system 을 남이 쓰고(라벨 없음) CNPG 가 없다 → cnpg P(stop)."""
    w = tb_world()
    drop(w, "deployments", "cnpg-controller-manager", "cnpg-system")
    drop(w, "crds", det.CNPG_CRD)
    drop(w, "namespaces", "cnpg-system")
    add(w, "namespaces", nsobj("cnpg-system", {"team": "other"}))
    return w


def test_plan_layer_needing_a_stopped_addon_is_a_named_skip_not_a_raw_fail(kenv, capsys):
    """T9(2026-11-03 일회용 k3s P4): 애드온이 P(stop)면 그 소비 층이 날것의 `no matches for kind` 로 FAIL 이었다 — vcjob 층처럼 그 애드온
    이름을 단 SKIP 이다(판정은 그 애드온 항목의 FAIL 하나로)."""
    w = _cnpg_p_world()
    w["dry_run"] = [{"match": "platform/db-cluster", "stderr": _no_kind("dms-pg", "Cluster", "postgresql.cnpg.io/v1")}]
    rc, doc, items, layers = _plan(kenv, capsys, w, make_rendered(kenv.tmp))
    assert items["cnpg"]["code"] == "P" and items["cnpg"]["action"] == "stop"
    db = layers["platform/db-cluster"]
    assert db["status"] == "SKIP", db
    assert db["detail"].startswith("skipped: cnpg 판정 FAIL(P stop) — Cluster 는 확인하지 않는다"), db
    assert "no matches" not in db["detail"]
    assert rc == 1 and doc["result"] == "FAIL"
    assert [k for k, v in layers.items() if v["status"] == "FAIL"] == []


def test_plan_addon_install_never_names_each_stopped_dependency(kenv, capsys, monkeypatch):
    """T9 A2(ADDON_INSTALL=never 의 빈 클러스터 — Traefik 은 있다): db-cluster·app 이 FAIL 이었다 — 각 층이 기대는 애드온 이름을 단 SKIP,
    vcjob 과 같은 문구. 첫 설치의 「ns dms 가 아직 없음」 SKIP 과 함께 나온다."""
    monkeypatch.setenv("ADDON_INSTALL", "never")
    w = bare_world()
    w["dry_run"] = [
        {"match": "platform/db-cluster", "stderr": _no_kind("dms-pg", "Cluster", "postgresql.cnpg.io/v1")
                                                  + 'Error from server (NotFound): namespaces "dms" not found\n'},
        {"match": "/app", "stderr": _no_kind("dms-data", "Queue", "scheduling.volcano.sh/v1beta1")
                                   + 'Error from server (NotFound): namespaces "dms" not found\n'},
    ]
    rc, doc, items, layers = _plan(kenv, capsys, w, make_rendered(kenv.tmp))
    assert {k: items[k]["action"] for k in ("cnpg", "volcano")} == {"cnpg": "stop", "volcano": "stop"}
    db, app, vc = layers["platform/db-cluster"], layers["app"], layers["vcjob"]
    assert db["status"] == "SKIP" and "cnpg 판정 FAIL(A stop) — Cluster 는" in db["detail"] and "ns dms will be created" in db["detail"]
    assert app["status"] == "SKIP" and "volcano 판정 FAIL(A stop) — Queue 는" in app["detail"], app
    assert vc["status"] == "SKIP" and vc["detail"] == "volcano 판정 FAIL(A stop) — 확인하지 않는다"
    assert [k for k, v in layers.items() if v["status"] == "FAIL"] == [] and rc == 1


def test_plan_real_error_beside_a_stopped_dependency_stays_fail(kenv, capsys):
    w = _cnpg_p_world()
    w["dry_run"] = [{"match": "platform/db-cluster",
                     "stderr": _no_kind("dms-pg", "Cluster", "postgresql.cnpg.io/v1")
                     + 'The PersistentVolume "dms-pgdata-01" is invalid: spec.capacity: Invalid value: 0\n'}]
    rc, doc, items, layers = _plan(kenv, capsys, w, make_rendered(kenv.tmp))
    db = layers["platform/db-cluster"]
    assert db["status"] == "FAIL" and "Invalid value: 0" in db["detail"] and "no matches" not in db["detail"], db
    assert "(또 cnpg 판정 FAIL(P stop)" in db["detail"]


_SSA_CONFLICT = ('error: Apply failed with 1 conflict: conflict with "kubectl-edit" using scheduling.k8s.io/v1: .description\n'
                 'Please review the fields above--they currently have other managers.\n')


def test_plan_conflict_on_a_dms_owned_layer_names_the_manager_not_adopt(kenv, capsys):
    """T9: --adopt 는 감지 P 에만 듣는다 — D= 층의 충돌 힌트가 「채택은 --adopt 로만」이면 운영자가 효과 없는 플래그로 다시 돌린다."""
    w = tb_world()
    w["dry_run"] = [{"match": "platform/priorityclass", "stderr": _SSA_CONFLICT}]
    rd = make_rendered(kenv.tmp)
    rc, doc, items, layers = _plan(kenv, capsys, w, rd)
    pc = layers["platform/priorityclass"]
    assert pc["status"] == "FAIL" and "field manager(kubectl-edit)" in pc["detail"], pc
    assert ("kubectl --context ctx apply --server-side --field-manager=dms-install --force-conflicts -k %s" % (rd / "platform/priorityclass")
            in pc["detail"]), pc
    assert "채택은 --adopt" not in pc["detail"] and "--adopt 는 감지 P 에만" in pc["detail"]


def test_conflict_hint_keeps_adopt_only_for_p_and_parses_every_manager():
    args = ["apply", "--server-side", "--dry-run=server", "--field-manager=dms-install", "-k", "/r/platform/registry"]
    p = det.Finding("registry").set("P", "이름 충돌")
    r = det._layer_result("platform/registry", "registry", args, 1, _SSA_CONFLICT, {}, set(), finding=p, context="ctx")
    assert r.status == "FAIL" and "채택은 --adopt registry 로만" in r.detail and "--force-conflicts" not in r.detail, r.detail
    multi = ('error: Apply failed with 2 conflicts: conflicts with "kubectl-patch" using apps/v1:\n- .spec.replicas\n'
             'conflicts with "helm" using apps/v1:\n- .metadata.labels.x\n')
    assert det.conflict_managers(multi) == ["helm", "kubectl-patch"]
    assert det.conflict_managers("Error from server: something else") == []


def test_plan_table_and_machine_lines(kenv, capsys):
    rd = make_rendered(kenv.tmp)
    kenv.wfile.write_text(json.dumps(tb_world()))
    out_json = kenv.tmp / "plan.json"
    rc = det.main(["plan", "--context", "ctx", "--rendered", str(rd), "--versions-env", str(VERSIONS), "--json-out", str(out_json)])
    out = capsys.readouterr().out.splitlines()
    assert rc == 0 and out[-1] == "RESULT: PASS"
    detect = [line.split() for line in out if line.startswith("DETECT: ")]
    assert [d[1] for d in detect] == list(det.ITEMS)
    assert ["DETECT:", "volcano", "C", "reuse", "0", "PASS"] in detect
    assert ["DETECT:", "traefik", "C", "reuse", "0", "PASS"] in detect and ["DETECT:", "ingress", "D=", "reapply", "1", "PASS"] in detect
    assert any(line.startswith("PLAN: vcjob PASS volcano") for line in out)
    assert "PLAN: platform/ingress PASS ingress" in out
    assert any(line.startswith("  [traefik] C PASS — 클러스터의 Traefik") for line in out)     # install.sh S8 이 사유를 읽는 줄 모양
    doc = json.loads(out_json.read_text())
    assert doc["mode"] == "plan" and doc["result"] == "PASS" and len(doc["items"]) == len(det.ITEMS)


# --- vcjob 모드(S9b — install.sh 가 Volcano 적용 뒤 부른다; 2026-11-03 ⑦ 리뷰 2: 이 모드를 직접 부르는 테스트가 없었다) ------------------

def _vcjob(kenv, capsys, w, *args):
    """→ (rc, 표 출력 줄들). 설치기와 같은 모양(--rendered, 표 + DETECT·PLAN·RESULT 줄)."""
    rd = make_rendered(kenv.tmp)
    kenv.wfile.write_text(json.dumps(w))
    rc = det.main(["vcjob", "--context", "ctx", "--namespace", "dms", "--rendered", str(rd), "--versions-env", str(VERSIONS), *args])
    return rc, capsys.readouterr().out.splitlines()


def _cnpg_unknown(w):
    op = next(d for d in w["objects"]["deployments"] if d["metadata"]["name"] == "cnpg-controller-manager")
    op["metadata"]["labels"] = {"app.kubernetes.io/name": "cloudnative-pg"}            # 남의 것, 버전 라벨 없음 → U(WARN)
    op["spec"]["template"]["spec"]["containers"][0]["image"] = "ghcr.io/cloudnative-pg/cloudnative-pg@" + DIGEST
    return w


def test_vcjob_mode_judges_only_volcano(kenv, capsys):
    # 다른 항목의 WARN(여기서는 cnpg U)은 S9b 결과로 새지 않는다 — 결과는 volcano 항목과 vcjob 층만.
    w = _cnpg_unknown(tb_world())
    rc, out = _vcjob(kenv, capsys, w)
    assert ["DETECT:", "cnpg", "U", "verify", "0", "WARN"] in [ln.split() for ln in out if ln.startswith("DETECT: ")]
    assert rc == 0 and out[-1] == "RESULT: PASS", out
    assert [ln for ln in out if ln.startswith("PLAN: ")] == ["PLAN: vcjob PASS volcano"]   # install.sh S9b 가 읽는 줄
    applies = kenv.applies()
    assert len(applies) == 1 and applies[0]["args"][:2] == ["create", "--dry-run=server"]   # vcjob 하나만(다른 층 dry-run 없음)
    job = json.loads(applies[0]["stdin"])
    assert job["spec"]["schedulerName"] == "volcano" and job["spec"]["queue"] == "dms-data" and job["metadata"]["namespace"] == "dms"


def test_vcjob_mode_dry_run_failure_is_fail(kenv, capsys):
    w = tb_world()
    w["dry_run"] = [{"match": "dms-install-detect-vcjob", "rc": 1,
                     "stderr": 'Error from server (InternalError): Internal error occurred: failed calling webhook "validatejob.volcano.sh": '
                               'failed to call webhook: Post "https://volcano-admission-service.volcano-system.svc:443/jobs/validate": '
                               'dial tcp 10.0.0.2:443: connect: connection refused\n'}]
    rc, out = _vcjob(kenv, capsys, w)
    assert rc == 1 and out[-1] == "RESULT: FAIL", out
    assert "PLAN: vcjob FAIL volcano" in out
    assert ["DETECT:", "volcano", "C", "reuse", "0", "FAIL"] in [ln.split() for ln in out if ln.startswith("DETECT: ")]
    assert any("8443" in ln for ln in out if ln.startswith("  vcjob ")), out            # 웹훅 경로 힌트


@pytest.mark.parametrize("w, code", [
    (lambda: _volcano_world(deps=volcano_deploys("v1.14.2")), "X"),
    (lambda: _volcano_world(deps=[]), "P"),
])
def test_vcjob_mode_stop_verdict_is_fail_without_a_dry_run(kenv, capsys, w, code):
    rc, out = _vcjob(kenv, capsys, w())
    assert rc == 1 and out[-1] == "RESULT: FAIL", out
    assert ["DETECT:", "volcano", code, "stop", "0", "FAIL"] in [ln.split() for ln in out if ln.startswith("DETECT: ")]
    assert "PLAN: vcjob SKIP volcano" in out
    assert kenv.applies() == []                                                         # 판정이 stop 이면 dry-run 도 부르지 않는다


def test_script_runs_as_subprocess(kenv, capsys):
    kenv.wfile.write_text(json.dumps(tb_world()))
    r = subprocess.run([sys.executable, str(SCRIPT), "--context", "ctx", "detect", "--versions-env", str(VERSIONS)],
                       capture_output=True, text=True, timeout=120, env=os.environ.copy())
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip().splitlines()[-1] == "RESULT: PASS"


def test_no_envoy_gateway_remnants_in_the_detector():
    # 2026-11-16 입구 교체(설계 D1): 항목·상수·CRD 그룹·웹훅 힌트·고정값까지 Envoy Gateway·Gateway API 경로를 남기지 않는다.
    code = "\n".join(ln for ln in SCRIPT.read_text(encoding="utf-8").splitlines() if not ln.lstrip().startswith("#"))
    for gone in ("gateway.networking.k8s.io", "gateway.envoyproxy.io", "envoy-gateway-system", "GATEWAY_API_VERSION",
                 "ENVOY_GATEWAY_VERSION", "platform/gateway\"", "addons/envoy-gateway", "addons/gateway-api", "EnvoyProxyPatch"):
        assert gone not in code, gone
