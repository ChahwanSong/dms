"""입구(Envoy Gateway) 계약 — Gateway API CRD·EG 애드온 층·DMS 플랫폼 층(2026-11-03 ⑥, 설계 v6 #13·#14·#15·4-3·5 「원 IP 기록」·8-2·9 「테스트·게이트」 ⑥).

설계 9 ⑥ 줄: 「CTP 계약 + EnvoyProxy 바인드 모양」. 여기에 ⑤ 의 애드온 계약(test_addons_render 와 같은 결 — 상류 바이트 그대로·버전/lock/디렉터리/라벨이
같이 움직임·자리표시자 base·렌더 이미지 = 애드온 레지스트리 + lock digest·외부 참조 0·Always 0·워커 배치·PriorityClass)과 #14 의 값(HTTPRoute 3600s·
BTP 20Mi·Gateway 리스너 하나 443·hostname 없음·80 없음), prod 앱 렌더에 Ingress·MetalLB 가 없는지(test_prod_k8s_overlay 와 짝)를 더한다.

렌더는 실제 렌더러(deploy/render.sh — 사본 트리) + `kubectl kustomize` 로컬 렌더뿐이다(KUBECONFIG=/dev/null). EG 의 번역 결과(xDS — PROXY 리스너 필터·
X-Real-IP 제거·버퍼 20971520·라우트 3600s·useRemoteAddress)는 egctl v1.9.3 `x translate` 로 2026-11-03 오프라인 확인했다(CHANGELOG ⑥) — 이 테스트는
그 입력(CR 의 모양)을 고정한다.
"""
import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from overlay_render import KUBECTL, REPO_ROOT, doc, docs, expected_context, needs_kubectl

DEPLOY = REPO_ROOT / "deploy"
ENV = {**os.environ, "KUBECONFIG": os.devnull}
PLACEHOLDER = "set-by-overlay.invalid"
EXTERNAL_HOSTS = ("ghcr.io", "docker.io", "quay.io", "registry.k8s.io", "gcr.io", "mcr.microsoft.com", "public.ecr.aws")


def _versions() -> dict:
    out = {}
    for line in (DEPLOY / "versions.env").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Z_]+)=(\S+)$", line)
        if m:
            assert m.group(1) not in out, m.group(1)
            out[m.group(1)] = m.group(2)
    return out


def _lock() -> dict:
    out = {}
    for line in (DEPLOY / "images.lock").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            dest, src, dg = line.split()
            out[dest] = (src, dg)
    return out


VERSIONS = _versions()
GWAPI_DIR = DEPLOY / "addons" / "gateway-api" / VERSIONS["GATEWAY_API_VERSION"]
EG_DIR = DEPLOY / "addons" / "envoy-gateway" / VERSIONS["ENVOY_GATEWAY_VERSION"]
GW_DIR = DEPLOY / "platform" / "gateway"
EP_PATH = "/spec/provider/kubernetes/envoyDaemonSet"


def _kustomize(path: Path) -> str:
    r = subprocess.run([KUBECTL, "kustomize", str(path)], capture_output=True, text=True, timeout=180, env=ENV)
    assert r.returncode == 0, f"kubectl kustomize {path} 실패: {r.stderr}"
    return r.stdout


def _tree(tmp_path: Path) -> Path:
    root = tmp_path / "deploy"
    if not root.exists():
        root.mkdir(parents=True)
        shutil.copy(DEPLOY / "render.sh", root / "render.sh")
        for d in ("platform", "k8s", "overlays", "addons"):
            shutil.copytree(DEPLOY / d, root / d, ignore=shutil.ignore_patterns(".prod-rendered", "values.env.local"))
        for name in ("versions.env", "images.lock"):
            shutil.copy(DEPLOY / name, root / name)
    return root


def _render(tmp_path: Path, site: str, values_text=None, **env_extra):
    root = _tree(tmp_path)
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(tmp_path), **env_extra}
    if values_text is not None:
        vals = tmp_path / f"{site}.values.env"
        vals.write_text(values_text)
        env["VALUES_ENV"] = str(vals)
    r = subprocess.run(["sh", str(root / "render.sh"), site], env=env, capture_output=True, text=True, timeout=60)
    return r, root / ".rendered" / site


def prod_values(**overrides) -> str:
    text = (DEPLOY / "overlays" / "prod" / "values.env.example").read_text()
    text = re.sub(r"\bREPLACE_([A-Z_]+)", lambda m: "v-" + m.group(1).lower().replace("_", "-"), text)
    text = re.sub(r"(?m)^SHARED_DIR=\S+", "SHARED_DIR=/srv/dms_shared", text)
    text = re.sub(r"(?m)^REGISTRY_CLUSTER_IP=\S+", "REGISTRY_CLUSTER_IP=10.96.0.50", text)
    text = re.sub(r"(?m)^SHARED_DIR_ID=\S+", "SHARED_DIR_ID=0123abcd", text)
    for key, value in overrides.items():
        if value is None:
            text = re.sub(rf"(?m)^{key}=.*$", "", text)
        elif re.search(rf"(?m)^#? ?{key}=", text):
            text = re.sub(rf"(?m)^#? ?{key}=\S*", lambda _m: f"{key}={value}", text)
        else:
            text += f"\n{key}={value}\n"
    return text


def _render_env(out: Path) -> dict:
    return dict(re.findall(r'(?m)^([A-Z_]+)="?([^"\n]*)"?$', (out / "render.env").read_text()))


def _image_refs(text: str) -> list:
    """이미지 필드 + EG 설정 문자열 안의 image: 줄(ConfigMap data 의 블록 문자열도 같은 모양이다). CRD 설명문은 `image:` 로 끝나는 줄뿐이다."""
    return re.findall(r"(?m)^\s*(?:- )?image: (\S+)$", text)


def _assert_patch_flag_only_enabled(cm: str):
    """EG 설정의 runtimeFlags 가 정확히 enabled: [EnvoyProxyPatch] 인지(2026-11-03 ⑥ 리뷰 1). EG 의 RuntimeFlags.IsEnabled 는 disabled 를 먼저 봐
    disabled 가 enabled 를 이긴다 — 둘 다에 넣으면 hostNetwork·dnsPolicy 패치가 조용히 무시돼 마스터 :443 이 빈다. 부분 문자열만 보면 그 모양이 통과한다."""
    assert cm.count("runtimeFlags:") == 1, cm
    block = re.search(r"(?m)^( +)runtimeFlags:\n((?:\1 +\S.*\n)+)", cm)
    assert block, cm
    assert " ".join(block.group(2).split()) == "enabled: - EnvoyProxyPatch", block.group(2)
    assert "disabled" not in cm.lower() and "EnvoyProxyPatch" not in cm.replace(block.group(0), ""), cm


def _assert_failed(r, out: Path, *needles):
    assert r.returncode == 2, (r.stdout, r.stderr)
    for n in needles:
        assert n in r.stdout + r.stderr, (n, r.stdout, r.stderr)
    assert not (out / "render.env").exists()


# --- 상류 원본·버전 고정(정적) -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("d, name, url", [
    (GWAPI_DIR, "standard-install.yaml",
     f"kubernetes-sigs/gateway-api/releases/download/{VERSIONS['GATEWAY_API_VERSION']}/standard-install.yaml"),
    (EG_DIR, "install.yaml", f"envoyproxy/gateway/releases/download/{VERSIONS['ENVOY_GATEWAY_VERSION']}/install.yaml"),
])
def test_upstream_manifest_is_byte_for_byte_the_recorded_release(d, name, url):
    data = (d / name).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    assert (d / f"{name}.sha256").read_text().split() == [digest, name]          # sha256sum -c 형식
    source = (d / "SOURCE").read_text(encoding="utf-8")
    assert digest in source and url in source
    text = data.decode()
    assert "dms-install" not in text and PLACEHOLDER not in text                 # DMS 패치는 kustomize 층에만


def test_upstream_traces_show_what_the_patch_layers_change():
    # 상류 그대로의 흔적: EG install.yaml 은 docker.io 이미지·실험 채널 Gateway API CRD·Helm 라벨을 싣는다(DMS 층이 바꾸는 것들).
    eg = (EG_DIR / "install.yaml").read_text()
    v = VERSIONS["ENVOY_GATEWAY_VERSION"]
    assert f"image: envoyproxy/gateway:{v}" in eg and "docker.io/envoyproxy/ratelimit" in eg
    assert "gateway.networking.k8s.io/channel: experimental" in eg and "app.kubernetes.io/managed-by: Helm" in eg
    gw = (GWAPI_DIR / "standard-install.yaml").read_text()
    channels = set(re.findall(r"gateway\.networking\.k8s\.io/channel: (\S+)", gw))
    assert channels == {"standard"}, channels
    assert set(re.findall(r"gateway\.networking\.k8s\.io/bundle-version: (\S+)", gw)) == {VERSIONS["GATEWAY_API_VERSION"]}
    assert _image_refs(gw) == []                                                 # 이미지 0개


def test_versions_lock_addon_dirs_and_labels_move_together():
    v_gw, v_eg = VERSIONS["GATEWAY_API_VERSION"], VERSIONS["ENVOY_GATEWAY_VERSION"]
    assert re.fullmatch(r"v1\.6\.\d+", v_gw) and re.fullmatch(r"v1\.9\.\d+", v_eg), (v_gw, v_eg)   # 설계 8-2 고정
    lock = _lock()
    assert VERSIONS["ENVOY_GATEWAY_IMAGE"] == f"gateway:{v_eg}"
    assert lock[VERSIONS["ENVOY_GATEWAY_IMAGE"]][0] == f"docker.io/envoyproxy/gateway:{v_eg}"
    envoy = VERSIONS["ENVOY_PROXY_IMAGE"]
    assert re.fullmatch(r"envoy:distroless-v1\.\d+\.\d+", envoy), envoy                # EG 의 DefaultEnvoyProxyImage 태그 모양
    assert lock[envoy][0] == f"docker.io/envoyproxy/{envoy}"
    assert envoy.split(":", 1)[1] in (EG_DIR / "SOURCE").read_text()
    for d, v in ((GWAPI_DIR, v_gw), (EG_DIR, v_eg)):
        assert re.search(rf'(?m)^      dms\.io/addon-version: "{re.escape(v)}"$', (d / "kustomization.yaml").read_text())
        assert sorted(p.name for p in d.parent.iterdir() if p.is_dir()) == [v]      # 고른 하나만(업그레이드 때 옛 것을 지운다)
    # 자리표시자 태그 = versions.env(렌더러가 글자 그대로 바꾼다 — 다르면 렌더가 멈춘다).
    for f in (EG_DIR / "patch-config.yaml", GW_DIR / "envoyproxy.yaml"):
        for img in (VERSIONS["ENVOY_GATEWAY_IMAGE"], envoy):
            assert f"image: {PLACEHOLDER}/{img}" in f.read_text(), (f.name, img)


# --- base(사이트 중립) --------------------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def eg_base():
    return _kustomize(EG_DIR)


@needs_kubectl
def test_eg_base_drops_every_gateway_api_object_and_keeps_its_own_crds(eg_base):
    crds = re.findall(r"(?m)^kind: CustomResourceDefinition\n(?:.*\n)*?  name: (\S+)$", eg_base)
    assert crds and all(n.endswith(".gateway.envoyproxy.io") for n in crds), crds  # Gateway API(실험 채널·x-k8s)는 addons/gateway-api 가 원천
    assert len(crds) == 8, crds
    assert "gateway.networking.k8s.io/bundle-version" not in eg_base and "ValidatingAdmissionPolicy" not in eg_base


@needs_kubectl
def test_eg_base_has_only_placeholder_images_and_no_external_reference(eg_base):
    refs = _image_refs(eg_base)
    assert refs and all(r.startswith(PLACEHOLDER + "/") for r in refs), refs
    assert len(refs) == 4, refs                       # 컨트롤러 · certgen · EG 설정의 shutdown-manager · 기본 Envoy
    for host in EXTERNAL_HOSTS:
        assert host not in eg_base, host
    assert "ratelimit" not in doc(eg_base, "ConfigMap", "envoy-gateway-config")


@needs_kubectl
def test_eg_controller_runs_on_workers_with_the_addon_priority(eg_base):
    dep = doc(eg_base, "Deployment", "envoy-gateway")
    assert re.search(r"(?m)^      priorityClassName: dms-addon-critical$", dep)
    assert re.search(r"(?m)^          requiredDuringSchedulingIgnoredDuringExecution:\n            nodeSelectorTerms:\n"
                     r"            - matchExpressions:\n              - key: node-role.kubernetes.io/control-plane\n"
                     r"                operator: DoesNotExist$", dep)
    assert "tolerations" not in dep and re.search(r"(?m)^  replicas: 1$", dep)
    assert set(re.findall(r"(?m)^\s*imagePullPolicy: (\S+)$", eg_base)) == {"IfNotPresent"}


@needs_kubectl
def test_eg_config_pins_images_and_the_patch_flag_and_watches_only_dms(eg_base):
    cm = doc(eg_base, "ConfigMap", "envoy-gateway-config")
    assert "shutdownManager:\n          image: set-by-overlay.invalid/gateway:" in cm
    assert re.search(r"envoyProxy:\n      provider:\n        type: Kubernetes\n        kubernetes:\n          envoyDeployment:\n"
                     r"            container:\n              image: set-by-overlay\.invalid/envoy:", cm)
    _assert_patch_flag_only_enabled(cm)                                        # hostNetwork 패치가 조용히 무시되지 않게(disabled 0)
    assert "controllerName: gateway.envoyproxy.io/gatewayclass-controller" in cm
    # watch 는 ns dms 하나 — 다른 네임스페이스의 Gateway + EnvoyProxy 패치가 privileged ns 에 특권 파드를 띄우지 못하게(보안 리뷰 2026-11-03).
    assert re.search(r"(?m)^        watch:\n          type: Namespaces\n          namespaces:\n            - dms$", cm)


@needs_kubectl
def test_proxy_namespace_is_psa_privileged_and_labels_never_touch_selectors(eg_base):
    ns = doc(eg_base, "Namespace", "envoy-gateway-system")
    for mode in ("enforce", "audit", "warn"):
        assert f"pod-security.kubernetes.io/{mode}: privileged" in ns
    v = VERSIONS["ENVOY_GATEWAY_VERSION"]
    for d in docs(eg_base):
        meta = d.split("\nspec:", 1)[0]
        assert "app.kubernetes.io/managed-by: dms-install" in meta, d.splitlines()[:6]
        assert re.search(rf'(?m)^    dms\.io/addon-version: "?{re.escape(v)}"?$', meta), d.splitlines()[:6]
    dep = doc(eg_base, "Deployment", "envoy-gateway")
    sel = re.search(r"(?m)^  selector:\n    matchLabels:\n((?:      .*\n)+)", dep).group(1)
    assert "dms-install" not in sel and "addon-version" not in sel


@needs_kubectl
def test_gateway_api_base_is_standard_crds_with_install_labels_only():
    text = _kustomize(GWAPI_DIR)
    names = re.findall(r"(?m)^kind: CustomResourceDefinition\n(?:.*\n)*?  name: (\S+)$", text)
    assert len(names) == 10 and all(n.endswith(".gateway.networking.k8s.io") for n in names), names
    assert "x-k8s.io" not in "".join(names) and "channel: experimental" not in text
    assert _image_refs(text) == []
    for d in docs(text):
        assert "app.kubernetes.io/managed-by: dms-install" in d.split("\nspec:", 1)[0]


@needs_kubectl
def test_platform_base_images_are_placeholders():
    text = _kustomize(GW_DIR)
    refs = _image_refs(text)
    assert sorted(refs) == sorted([f"{PLACEHOLDER}/{VERSIONS['ENVOY_PROXY_IMAGE']}", f"{PLACEHOLDER}/{VERSIONS['ENVOY_GATEWAY_IMAGE']}"])
    for host in EXTERNAL_HOSTS:
        assert host not in text, host


# --- 렌더(testbed-k8s·prod) ----------------------------------------------------------------------------------------------------

SITES = ("testbed-k8s", "prod")


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    out = {}
    for site, values in (("testbed-k8s", None), ("prod", prod_values())):
        r, path = _render(tmp_path_factory.mktemp(site), site, values)
        assert r.returncode == 0, (r.stdout, r.stderr)
        out[site] = {
            "stdout": r.stdout, "env": _render_env(path), "path": path,
            "gwapi": _kustomize(path / "addons" / "gateway-api"),
            "eg": _kustomize(path / "addons" / "envoy-gateway"),
            "gw": _kustomize(path / "platform" / "gateway"),
        }
    return out


@needs_kubectl
@pytest.mark.parametrize("site", SITES)
def test_layers_are_announced_in_s8_order_and_recorded(rendered, site):
    s = rendered[site]
    lines = [ln.split(": ", 1)[1] for ln in s["stdout"].splitlines() if ln.startswith("GATEWAY: ")]
    assert lines == [str(s["path"] / p) for p in ("addons/gateway-api", "addons/envoy-gateway", "platform/gateway")]
    assert s["env"]["GATEWAY_LAYERS"] == "addons/gateway-api addons/envoy-gateway platform/gateway"
    assert (s["env"]["GATEWAY_API_VERSION"], s["env"]["ENVOY_GATEWAY_VERSION"]) == (
        VERSIONS["GATEWAY_API_VERSION"], VERSIONS["ENVOY_GATEWAY_VERSION"])
    assert s["stdout"].strip().splitlines()[-1].startswith("RENDERED: ")
    if site == "testbed-k8s":                                                    # 컨텍스트 고정이 층마다 따라간다(가드 exit 4)
        for p in s["env"]["GATEWAY_LAYERS"].split():
            assert expected_context(s["path"] / p) == "dmsk8s"


@needs_kubectl
@pytest.mark.parametrize("site", SITES)
def test_every_image_is_the_addon_registry_plus_the_lock_digest(rendered, site):
    s = rendered[site]
    lock, reg = _lock(), s["env"]["ADDON_REGISTRY"]
    want_eg = f"{reg}/{VERSIONS['ENVOY_GATEWAY_IMAGE']}@{lock[VERSIONS['ENVOY_GATEWAY_IMAGE']][1]}"
    want_envoy = f"{reg}/{VERSIONS['ENVOY_PROXY_IMAGE']}@{lock[VERSIONS['ENVOY_PROXY_IMAGE']][1]}"
    assert (s["env"]["ENVOY_GATEWAY_REF"], s["env"]["ENVOY_PROXY_REF"]) == (want_eg, want_envoy)
    eg_refs, gw_refs = _image_refs(s["eg"]), _image_refs(s["gw"])
    assert sorted(eg_refs) == sorted([want_eg, want_eg, want_eg, want_envoy]), eg_refs   # 컨트롤러·certgen·설정 shutdown-manager·설정 기본 Envoy
    assert sorted(gw_refs) == sorted([want_envoy, want_eg]), gw_refs                     # EnvoyProxy: envoy + shutdown-manager 패치
    for ref in eg_refs + gw_refs:
        assert re.fullmatch(r"[^@\s]+:[^@\s/]+@sha256:[0-9a-f]{64}", ref), ref         # 태그(GC 보호) + digest(pull)
    for text in (s["gwapi"], s["eg"], s["gw"]):
        assert PLACEHOLDER not in text
        for host in EXTERNAL_HOSTS:
            assert host not in text, host                                               # airgap: 외부 참조 0건
    policies = re.findall(r"(?m)^\s*-? ?imagePullPolicy: (\S+)$", s["eg"] + s["gw"])
    assert policies and set(policies) == {"IfNotPresent"}, policies                     # Always 0건


@needs_kubectl
@pytest.mark.parametrize("site", SITES)
def test_envoyproxy_binds_443_on_the_masters_through_the_host_network(rendered, site):
    # 설계 4-3: hostNetwork + ClusterFirstWithHostNet(xDS 주소가 서비스 DNS 이름) + useListenerPortAsContainerPort(443 그대로) + control-plane 배치.
    ep = doc(rendered[site]["gw"], "EnvoyProxy", "dms-portal")
    assert re.search(r"(?m)^      useListenerPortAsContainerPort: true$", ep)
    patch = re.search(r"(?m)^        patch:\n          type: StrategicMerge\n          value:\n((?:            .*\n)+)", ep).group(1)
    assert re.search(r"(?m)^                  hostNetwork: true$", patch)
    assert re.search(r"(?m)^                  dnsPolicy: ClusterFirstWithHostNet$", patch)
    assert [n for n in re.findall(r"(?m)^                    name: (\S+)$", patch)] == ["envoy", "shutdown-manager"]
    assert re.search(r'(?m)^          nodeSelector:\n            node-role.kubernetes.io/control-plane: ""$', ep)
    assert re.search(r"(?m)^          - effect: NoSchedule\n            key: node-role.kubernetes.io/control-plane\n"
                     r"            operator: Exists$", ep)
    assert re.search(r"(?m)^      envoyService:\n        type: ClusterIP$", ep)                    # MetalLB·LoadBalancer 없음
    assert re.search(r"(?m)^  concurrency: 2$", ep)
    assert re.search(r"(?m)^            maxSurge: 0\n            maxUnavailable: 1$", ep)           # 같은 호스트 포트 — 겹칠 수 없다
    # root 로 「해결」하지 않는다(sysctl — 4-3 (i)). EG 는 securityContext·DS patch 의 root 를 막지 않는다(strategic merge) — 막는 것은 이 단언이다.
    for forbidden in ("runAsUser", "runAsNonRoot", "securityContext", "privileged", "capabilities", "NET_BIND_SERVICE"):
        assert forbidden not in ep, forbidden
    # hostNetwork Envoy 의 hot restart 추상 소켓(base-id 0)이 cilium-envoy(hostNetwork, --base-id 0)와 겹치지 않게(2026-11-03 ⑥ 리뷰 1).
    assert re.search(r"(?m)^  extraArgs:\n  - --disable-hot-restart$", ep), ep
    assert "--base-id" not in ep
    assert "hostPort" not in ep


@needs_kubectl
def test_envoy_resources_are_the_design_values_with_the_testbed_shrink(rendered):
    want = {"prod": ("100m", "256Mi"), "testbed-k8s": ("50m", "128Mi")}       # 설계 #14·2-1 / 설계 10 축소값
    for site, (cpu, mem) in want.items():
        ep = doc(rendered[site]["gw"], "EnvoyProxy", "dms-portal")
        res = re.search(r"(?m)^          resources:\n            limits:\n              cpu: (\S+)\n              memory: (\S+)\n"
                        r"            requests:\n              cpu: (\S+)\n              memory: (\S+)$", ep)
        assert res and tuple(x.strip('"') for x in res.groups()) == ("1", "2Gi", cpu, mem), (site, res and res.groups())


@needs_kubectl
@pytest.mark.parametrize("site", SITES)
def test_access_log_records_the_original_ip_and_the_direct_peer(rendered, site):
    # 설계 5 「원 IP 기록」(K15): PROXY 헤더의 출발지와 실제 TCP 피어(Avi SE) 두 칸 — 앱 이벤트의 {client_ip, peer} 와 같은 이름.
    ep = doc(rendered[site]["gw"], "EnvoyProxy", "dms-portal")
    assert "client_ip: '%DOWNSTREAM_REMOTE_ADDRESS_WITHOUT_PORT%'" in ep
    assert "peer: '%DOWNSTREAM_DIRECT_REMOTE_ADDRESS_WITHOUT_PORT%'" in ep
    assert "x_forwarded_for: '%REQ(X-FORWARDED-FOR)%'" in ep
    assert "path: '%REQ_WITHOUT_QUERY(:PATH)%'" in ep and "%REQ(:PATH)%" not in ep     # 쿼리는 남기지 않는다
    assert re.search(r"(?m)^          type: JSON$", ep) and "path: /dev/stdout" in ep


@needs_kubectl
@pytest.mark.parametrize("site", SITES)
def test_client_traffic_policy_requires_proxy_strips_x_real_ip_and_trusts_no_header(rendered, site):
    # 설계 #15·4-3 「PROXY·헤더 계약」. 두 사이트 모두 커밋 값은 required(테스트베드 T8 의 optional 단계는 VALUES_ENV 로 따로 렌더).
    text = rendered[site]["gw"]
    ctp = doc(text, "ClientTrafficPolicy", "dms-portal")
    assert re.search(r"(?m)^  proxyProtocol:\n    optional: false$", ctp)
    assert re.search(r"(?m)^    earlyRequestHeaders:\n      remove:\n      - X-Real-IP$", ctp)
    assert re.search(r"(?m)^    withUnderscoresAction: DropHeader$", ctp)
    assert re.search(r"(?m)^  targetRefs:\n  - group: gateway.networking.k8s.io\n    kind: Gateway\n    name: dms-portal$", ctp)
    for forbidden in ("clientIPDetection", "enableProxyProtocol", "xForwardedFor", "numTrustedHops", "customHeader"):
        assert forbidden not in text, forbidden                                   # 앱이 XFF 마지막 항목을 읽는 계약을 깨지 않는다
    assert rendered[site]["env"]["GATEWAY_PROXY_PROTOCOL"] == "required"


@needs_kubectl
@pytest.mark.parametrize("site", SITES)
def test_gateway_has_one_https_443_listener_without_hostname_or_port_80(rendered, site):
    text = rendered[site]["gw"]
    gw = doc(text, "Gateway", "dms-portal")
    listeners = re.findall(r"(?m)^    port: (\d+)$", gw)
    assert listeners == ["443"] and re.search(r"(?m)^    protocol: HTTPS$", gw)
    assert "hostname" not in gw and not re.search(r"(?m)^\s*port: 80$", text)          # 80 리스너 없음(「80 불가 = 정상」)
    assert re.search(r'(?m)^      certificateRefs:\n      - group: ""\n        kind: Secret\n        name: dms-portal-tls\n      mode: Terminate$', gw)
    assert re.search(r"(?m)^  gatewayClassName: dms$", gw)
    assert re.search(r"(?m)^    parametersRef:\n      group: gateway.envoyproxy.io\n      kind: EnvoyProxy\n      name: dms-portal$", gw)
    gc = doc(text, "GatewayClass", "dms")
    assert "controllerName: gateway.envoyproxy.io/gatewayclass-controller" in gc and "parametersRef" not in gc
    assert "strict-transport-security" not in text.lower()                              # HSTS 없음(#14 — 헤더 이름은 대소문자를 안 가린다)


@needs_kubectl
@pytest.mark.parametrize("site", SITES)
def test_route_timeout_backend_and_request_buffer(rendered, site):
    text = rendered[site]["gw"]
    route = doc(text, "HTTPRoute", "dms-portal")
    assert re.search(r"(?m)^    timeouts:\n      request: 3600s$", route)
    assert re.search(r'(?m)^    - group: ""\n      kind: Service\n      name: dms-api\n      port: 8080$', route)
    assert "hostnames" not in route and re.search(r"(?m)^    sectionName: https$", route)
    btp = doc(text, "BackendTrafficPolicy", "dms-portal")
    assert re.search(r"(?m)^  requestBuffer:\n    limit: 20Mi$", btp)                       # 넘으면 413(옛 Ingress 20m)
    # 상류 유휴 타임아웃 < 앱 uvicorn keep-alive(기본 5초 — src/dms/cli.py 가 값을 주지 않는다) — 상류가 먼저 닫으면 503 UC(2026-11-03 ⑥ 리뷰 1).
    assert re.search(r"(?m)^  timeout:\n    http:\n      connectionIdleTimeout: 4s$", btp), btp
    assert "timeout_keep_alive" not in (REPO_ROOT / "src" / "dms" / "cli.py").read_text()   # 앱이 값을 주면 이 4s 를 다시 본다
    assert re.search(r"(?m)^    kind: HTTPRoute\n    name: dms-portal$", btp)
    assert "httpUpgrade" not in text                                                       # requestBuffer 와 같이 못 쓴다


@needs_kubectl
@pytest.mark.parametrize("site", SITES)
def test_rendered_eg_config_keeps_only_the_patch_flag_enabled(rendered, site):
    _assert_patch_flag_only_enabled(doc(rendered[site]["eg"], "ConfigMap", "envoy-gateway-config"))


@needs_kubectl
def test_eg_config_in_the_render_carries_the_lock_refs(rendered):
    s = rendered["prod"]
    cm = doc(s["eg"], "ConfigMap", "envoy-gateway-config")
    assert f"shutdownManager:\n          image: {s['env']['ENVOY_GATEWAY_REF']}\n" in cm
    assert f"image: {s['env']['ENVOY_PROXY_REF']}\n" in cm
    job = doc(s["eg"], "Job", "eg-gateway-helm-certgen")
    assert f"image: {s['env']['ENVOY_GATEWAY_REF']}" in job


# --- 값(GATEWAY_PROXY_PROTOCOL·PORTAL_TLS_SECRET·Envoy requests) ------------------------------------------------------------------

@needs_kubectl
def test_optional_proxy_only_through_the_explicit_value_and_it_says_so(tmp_path):
    r, out = _render(tmp_path, "prod", prod_values(GATEWAY_PROXY_PROTOCOL="optional"))
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "GATEWAY_PROXY_PROTOCOL=optional" in r.stderr and "컷오버 중에만" in r.stderr
    ctp = doc(_kustomize(out / "platform" / "gateway"), "ClientTrafficPolicy", "dms-portal")
    assert re.search(r"(?m)^  proxyProtocol:\n    optional: true$", ctp)
    assert _render_env(out)["GATEWAY_PROXY_PROTOCOL"] == "optional"


def _app_config(out: Path) -> dict:
    """앱 층 렌더(kustomize)의 ConfigMap dms-config data."""
    cm = doc(_kustomize(out / "app"), "ConfigMap", "dms-config")
    return dict(re.findall(r'(?m)^  (DMS_[A-Z0-9_]+): "?([^"\n]*)"?$', cm))


@needs_kubectl
def test_proxy_off_drops_the_proxy_filter_and_turns_the_source_alarm_off(tmp_path):
    """2026-11-14(OA — Avi L4 가 PROXY v2 를 못 붙인다): off 는 **상시** 모드다. ClientTrafficPolicy 의 proxyProtocol 이 통째로 빠져 Envoy 가
    PROXY 를 해석하지 않는다(optional 은 클라이언트가 심은 헤더를 읽어 원 IP 기록을 위조할 수 있다). 모든 사용자가 LB 주소로 묶이므로 앱의
    출발지 경보 임계는 0(끔)이다 — 나머지 헤더 계약(X-Real-IP 제거·clientIPDetection 없음)은 그대로."""
    base = tmp_path / "default"
    r, out = _render(base, "prod", prod_values())
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert _app_config(out)["DMS_LOGIN_ALARM_SOURCE_FAILURES"] == "20"            # 평소 = base 값 그대로
    off = tmp_path / "off"
    r, out = _render(off, "prod", prod_values(GATEWAY_PROXY_PROTOCOL="off"))
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "GATEWAY_PROXY_PROTOCOL=off" in r.stderr and "컷오버 중에만" not in r.stderr
    text = _kustomize(out / "platform" / "gateway")
    ctp = doc(text, "ClientTrafficPolicy", "dms-portal")
    assert "proxyProtocol" not in ctp, ctp
    assert re.search(r"(?m)^    earlyRequestHeaders:\n      remove:\n      - X-Real-IP$", ctp)
    for forbidden in ("clientIPDetection", "enableProxyProtocol", "xForwardedFor", "numTrustedHops"):
        assert forbidden not in text, forbidden
    assert _render_env(out)["GATEWAY_PROXY_PROTOCOL"] == "off"
    assert _app_config(out)["DMS_LOGIN_ALARM_SOURCE_FAILURES"] == "0"


@needs_kubectl
def test_listen_port_moves_the_gateway_listener_and_default_is_untouched(tmp_path):
    """2026-11-14(OA — RKE2 Traefik 이 hostPort 443 을 쥔다): GATEWAY_LISTEN_PORT 가 Gateway 리스너 포트(= Envoy 가 마스터 호스트에서 여는
    포트)를 바꾼다. 기본(키 없음)은 패치가 없어 기존 출력 그대로다."""
    base = tmp_path / "default"
    r, out = _render(base, "prod", prod_values())
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "/spec/listeners/0/port" not in (out / "platform" / "gateway" / "kustomization.yaml").read_text()
    assert _render_env(out)["GATEWAY_LISTEN_PORT"] == "443"
    moved = tmp_path / "moved"
    r, out = _render(moved, "prod", prod_values(GATEWAY_LISTEN_PORT="10443"))
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "note:" not in r.stderr                                                # 10443 은 임시 포트 범위 밖
    text = _kustomize(out / "platform" / "gateway")
    gw = doc(text, "Gateway", "dms-portal")
    assert re.findall(r"(?m)^    port: (\d+)$", gw) == ["10443"] and re.search(r"(?m)^    protocol: HTTPS$", gw)
    assert "hostNetwork: true" in text and re.search(r"(?m)^\s*useListenerPortAsContainerPort: true$", text)
    assert _render_env(out)["GATEWAY_LISTEN_PORT"] == "10443"
    assert ":10443(" in (out / "platform" / "gateway" / "kustomization.yaml").read_text()   # 적용 안내 줄의 포트도 같은 값


def test_listen_port_in_the_ephemeral_range_renders_with_a_note(tmp_path):
    r, out = _render(tmp_path, "prod", prod_values(GATEWAY_LISTEN_PORT="40443"))
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "note: GATEWAY_LISTEN_PORT=40443" in r.stderr and "임시 포트" in r.stderr


@pytest.mark.parametrize("key, value", [
    ("GATEWAY_PROXY_PROTOCOL", "none"), ("GATEWAY_PROXY_PROTOCOL", "Optional"), ("GATEWAY_PROXY_PROTOCOL", "true"),
    ("GATEWAY_PROXY_PROTOCOL", "Off"),
    # 리스너 포트(2026-11-14): 숫자 아님·범위 밖·앞자리 0·NodePort 범위·Envoy 자체 포트·k8s 구성요소 포트
    ("GATEWAY_LISTEN_PORT", "0"), ("GATEWAY_LISTEN_PORT", "abc"), ("GATEWAY_LISTEN_PORT", "70000"), ("GATEWAY_LISTEN_PORT", "08443"),
    ("GATEWAY_LISTEN_PORT", "30443"), ("GATEWAY_LISTEN_PORT", "19002"), ("GATEWAY_LISTEN_PORT", "6443"), ("GATEWAY_LISTEN_PORT", "10250"),
    ("GATEWAY_LISTEN_PORT", "123456"), ("GATEWAY_LISTEN_PORT", "10 443"),
    ("PORTAL_TLS_SECRET", "Dms-Portal"), ("PORTAL_TLS_SECRET", "-x"), ("PORTAL_TLS_SECRET", "a/b"), ("PORTAL_TLS_SECRET", "x|y"),
    ("PORTAL_TLS_SECRET", "a..b"), ("PORTAL_TLS_SECRET", "a.-b"), ("PORTAL_TLS_SECRET", "a-.b"),
    ("GATEWAY_ENVOY_CPU_REQUEST", "0"), ("GATEWAY_ENVOY_CPU_REQUEST", "0.5"), ("GATEWAY_ENVOY_MEMORY_REQUEST", "128M"),
    # base limits(1/2Gi)보다 큰 requests — SSA 는 통과하고 EG 의 DaemonSet 생성이 apiserver 에서 거절된다(2026-11-03 ⑥ 리뷰 1)
    ("GATEWAY_ENVOY_CPU_REQUEST", "2"), ("GATEWAY_ENVOY_CPU_REQUEST", "1001m"), ("GATEWAY_ENVOY_CPU_REQUEST", "1234567890"),
    ("GATEWAY_ENVOY_MEMORY_REQUEST", "3Gi"), ("GATEWAY_ENVOY_MEMORY_REQUEST", "2049Mi"),
])
def test_render_rejects_bad_gateway_values(tmp_path, key, value):
    r, out = _render(tmp_path, "prod", prod_values(**{key: f"'{value}'"}))
    _assert_failed(r, out, key)


@pytest.mark.parametrize("cpu, mem", [("1", "2Gi"), ("1000m", "2048Mi"), ("1m", "1Mi")])
def test_render_accepts_envoy_requests_up_to_the_base_limits(tmp_path, cpu, mem):
    r, out = _render(tmp_path, "prod", prod_values(GATEWAY_ENVOY_CPU_REQUEST=cpu, GATEWAY_ENVOY_MEMORY_REQUEST=mem,
                                                   PORTAL_TLS_SECRET="a--b.c-d"))
    assert r.returncode == 0, (r.stdout, r.stderr)


@needs_kubectl
def test_tls_secret_name_and_envoy_requests_come_from_values(tmp_path):
    r, out = _render(tmp_path, "prod", prod_values(PORTAL_TLS_SECRET="portal-cert.v2", GATEWAY_ENVOY_CPU_REQUEST="250m",
                                                   GATEWAY_ENVOY_MEMORY_REQUEST="512Mi"))
    assert r.returncode == 0, (r.stdout, r.stderr)
    text = _kustomize(out / "platform" / "gateway")
    assert "        name: portal-cert.v2\n" in doc(text, "Gateway", "dms-portal")
    assert re.search(r"(?m)^            requests:\n              cpu: 250m\n              memory: 512Mi$", doc(text, "EnvoyProxy", "dms-portal"))
    assert _render_env(out)["PORTAL_TLS_SECRET"] == "portal-cert.v2"


def test_gateway_values_in_the_callers_environment_are_ignored(tmp_path):
    # values.env 만 원천(③④ 와 같은 이유) — 셸에 남은 값이 렌더를 바꾸지 않는다.
    r, out = _render(tmp_path, "prod", prod_values(), GATEWAY_PROXY_PROTOCOL="optional", PORTAL_TLS_SECRET="from-shell",
                     GATEWAY_LISTEN_PORT="10443")
    assert r.returncode == 0, (r.stdout, r.stderr)
    env = _render_env(out)
    assert (env["GATEWAY_PROXY_PROTOCOL"], env["PORTAL_TLS_SECRET"]) == ("required", "dms-portal-tls")
    assert env["GATEWAY_LISTEN_PORT"] == "443"


@pytest.mark.parametrize("mutate, needle", [
    (lambda root: (root / "versions.env").write_text((root / "versions.env").read_text().replace(
        "ENVOY_GATEWAY_IMAGE=gateway:v1.9.3", "ENVOY_GATEWAY_IMAGE=gateway:v1.9.2")), "ENVOY_GATEWAY_VERSION"),
    (lambda root: (root / "versions.env").write_text((root / "versions.env").read_text().replace(
        "ENVOY_PROXY_IMAGE=envoy:distroless-v1.39.5", "ENVOY_PROXY_IMAGE=envoy:distroless-v1.39.4")), "자리표시자"),
    (lambda root: (root / "versions.env").write_text((root / "versions.env").read_text().replace(
        "GATEWAY_API_VERSION=v1.6.1", "GATEWAY_API_VERSION=1.6.1")), "GATEWAY_API_VERSION"),
    (lambda root: shutil.rmtree(root / "addons" / "envoy-gateway"), "애드온 base 없음"),
    (lambda root: shutil.rmtree(root / "platform" / "gateway"), "플랫폼 base 없음"),
    (lambda root: (root / "images.lock").write_text("\n".join(
        ln for ln in (root / "images.lock").read_text().splitlines() if not ln.startswith("envoy:")) + "\n"), "envoy:distroless"),
])
def test_render_stops_when_versions_lock_and_bases_disagree(tmp_path, mutate, needle):
    root = _tree(tmp_path)
    mutate(root)
    r, out = _render(tmp_path, "prod", prod_values())
    _assert_failed(r, out, needle)


# --- guard-images.sh 가 EG 컨트롤러를 그 네임스페이스에서 본다 --------------------------------------------------------------------

_SHIM = r"""#!/bin/sh
printf '%s\n' "$*" >> "$FAKE_LOG"
if [ "$1" = "--context" ]; then shift 2; fi   # guard 는 컨텍스트가 있으면 `kubectl --context C kustomize` 로 렌더한다(2026-11-03 T9)
if [ "$1" = "kustomize" ]; then cat "$FAKE_RENDER"; exit 0; fi
if [ "$1" = "config" ]; then [ "$2" = "get-contexts" ] && [ "$3" = "dmsk8s" ] && exit 0; exit 1; fi
case "$*" in
  *"-n envoy-gateway-system get deploy envoy-gateway"*"containers[0].image"*) printf '%s' "$FAKE_LIVE"; exit 0 ;;
esac
echo 'Error from server (NotFound)' >&2; exit 1
"""


@needs_kubectl
def test_guard_reads_the_eg_controller_in_its_namespace_and_names_the_upgrade(tmp_path, rendered):
    s = rendered["testbed-k8s"]
    layer = s["path"] / "addons" / "envoy-gateway"
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "kubectl").write_text(_SHIM)
    (bindir / "kubectl").chmod(0o755)
    (tmp_path / "r.yaml").write_text(s["eg"])

    def run(live, **extra):
        log = tmp_path / "log"
        log.write_text("")
        e = {"PATH": f"{bindir}{os.pathsep}/usr/bin:/bin", "FAKE_LOG": str(log), "FAKE_RENDER": str(tmp_path / "r.yaml"),
             "FAKE_LIVE": live, **extra}
        r = subprocess.run(["sh", str(DEPLOY / "overlays" / "guard-images.sh"), str(layer), "--context", "dmsk8s"],
                           env=e, capture_output=True, text=True, timeout=60)
        return r, log.read_text()

    r, calls = run(s["env"]["ENVOY_GATEWAY_REF"])
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "-n envoy-gateway-system get deploy envoy-gateway" in calls
    assert "--server-side --field-manager=dms-install" in r.stdout
    r, _ = run("10.112.0.50:5000/gateway:v1.9.2@sha256:" + "0" * 64)
    assert r.returncode == 3 and "Envoy Gateway 업그레이드" in r.stderr and "§2g-6" in r.stderr, r.stderr


def _readme_2g() -> str:
    text = (DEPLOY / "README.md").read_text(encoding="utf-8")
    return text.split("\n## 2g.", 1)[1].split("\n## ", 1)[0]


def test_runbook_reads_envoy_logs_from_every_master():
    # 2026-11-03 ⑥ 리뷰 2: `kubectl logs ds/<이름>` 은 파드 하나만 고른다(--all-pods=false) — Envoy DS 는 마스터 ×3 이고 Avi 가 요청을 셋에 나눠
    # 원 IP 확인·UC 세기가 1/3 만 본다. Envoy 접근 로그는 라벨 셀렉터로 마스터 전부를 읽는다.
    lines = [ln for ln in _readme_2g().splitlines() if "kubectl" in ln and " logs " in ln and "-c envoy" in ln]
    assert len(lines) >= 2, lines
    for ln in lines:
        assert "logs ds/" not in ln and "logs -l gateway.envoyproxy.io/owning-gateway-name=dms-portal" in ln, ln


def test_runbook_applies_the_addon_priorityclass_for_external_sites():
    # 2026-11-03 ⑥ 리뷰 2: external 사이트는 §2e(S4)를 거치지 않는다 — A 경로가 확인만 하면 EG 컨트롤러 파드가 admission 에서 거절된다(§2f-3 과 같은 줄).
    sec = _readme_2g()
    assert ("kubectl --context C get priorityclass dms-addon-critical || kubectl --context C apply --server-side "
            "--field-manager=dms-install -k $RD/platform/priorityclass") in sec
    assert "REGISTRY_MODE=external" in sec and "ADDON_REGISTRY" in sec



def test_envoy_prometheus_stats_listener_is_off():
    # 커밋 보안 리뷰(2026-11-03): hostNetwork Envoy 의 stats(:19001)는 마스터 호스트 전 인터페이스에 열린다 — 쓰지 않으니 끈다.
    text = (REPO_ROOT / "deploy" / "platform" / "gateway" / "envoyproxy.yaml").read_text(encoding="utf-8")
    assert re.search(r"(?m)^    metrics:\n      prometheus:\n        disable: true$", text)



def test_envoy_prestop_targets_loopback_and_pod_has_priority():
    # T8(2026-11-03): host 없는 httpGet preStop 은 hostNetwork 파드에서 kubelet 이 실패시킨다(드레인 없이 SIGTERM — 롤링마다 약 61초 공백).
    # 우선순위가 없으면 마스터 메모리 압박 때 Envoy 가 축출 1순위였다.
    text = (REPO_ROOT / "deploy" / "platform" / "gateway" / "envoyproxy.yaml").read_text(encoding="utf-8")
    assert re.search(r"(?m)^                  priorityClassName: dms-addon-critical$", text)
    assert re.search(r"(?m)^                      lifecycle:\n                        preStop:\n                          httpGet:\n"
                     r"                            host: 127\.0\.0\.1\n                            path: /shutdown/ready\n"
                     r"                            port: 19002$", text)
    assert "REQUESTED_SERVER_NAME" not in text        # hostname 없는 리스너라 sni 는 늘 비었다(T8 33/33줄 null)
