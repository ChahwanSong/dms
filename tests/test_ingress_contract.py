"""입구(클러스터의 Traefik) 계약 — DMS 플랫폼 층 platform/ingress(2026-11-16, 사용자 결정 「envoy 사용방식은 폐기하고 traefik을 사용하도록」 —
병행 모드 없음; 옛 test_gateway_contract.py 를 대체한다).

DMS 는 Traefik 을 설치·변경하지 않는다(RKE2 는 rke2-traefik). DMS 객체는 Ingress dms/dms-portal-traefik 하나 — host = PORTAL_DOMAIN,
tls = PORTAL_TLS_SECRET, ingressClassName = INGRESS_CLASS(기본 traefik), router.entrypoints = TRAEFIK_ENTRYPOINT(기본 websecure — :80 평문에
붙지 않게), router.tls "true", 백엔드 svc dms-api(port name http). 여기서 고정한다:

- base(사이트 중립): 자리표시자 host, 표준 Ingress 하나뿐(Middleware·ServersTransport·IngressRoute 없음 — 공용 Traefik 에서 ingressClass 필터가
  안 걸리는 CRD 를 쓰지 않는다), host 없는 catch-all 없음, DMS 소유 라벨(managed-by dms-install — install-detect 의 D=/P 판정).
- 두 사이트 실제 렌더(deploy/render.sh 사본 트리 + `kubectl kustomize`, KUBECONFIG=/dev/null): Ingress 모양·사이트 값·render.env 입구 키·
  stdout `INGRESS:` 한 줄·kube-context, 렌더 어디에도 Envoy·Gateway API 객체 0.
- 값: PORTAL_DOMAIN 필수(템플릿·고정 오버레이 모두)·문법, INGRESS_CLASS·TRAEFIK_ENTRYPOINT·PORTAL_TLS_SECRET 문법·반영, 셸 env 무시, 옛 GATEWAY_*
  키는 알림 뒤 무시(실패하지 않는다).
- 버전 고정·lock·디렉터리에서 Envoy 경로가 사라졌는지.
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from overlay_render import KUBECTL, REPO_ROOT, doc, docs, expected_context, needs_kubectl

DEPLOY = REPO_ROOT / "deploy"
ENV = {**os.environ, "KUBECONFIG": os.devnull}
BASE = DEPLOY / "platform" / "ingress"
TESTBED = DEPLOY / "overlays" / "testbed-k8s"
NAME = "dms-portal-traefik"
EP_ANN = "traefik.ingress.kubernetes.io/router.entrypoints"
TLS_ANN = "traefik.ingress.kubernetes.io/router.tls"
OLD_GATEWAY_KEYS = ("GATEWAY_PROXY_PROTOCOL", "GATEWAY_LISTEN_PORT", "GATEWAY_ENVOY_CPU_REQUEST", "GATEWAY_ENVOY_MEMORY_REQUEST")
RETIRED_NOTE = "더 쓰지 않는다(2026-11-16 — 입구는 클러스터의 Traefik)"
# 렌더 결과에 있으면 안 되는 Envoy·Gateway API 의 객체 종류(상류 CNPG CRD 설명문의 「gateway.networking.k8s.io/ReferenceGrant」 같은 글자는 객체가 아니다).
FORBIDDEN_KINDS = {"Gateway", "GatewayClass", "HTTPRoute", "GRPCRoute", "TLSRoute", "TCPRoute", "UDPRoute", "ReferenceGrant", "BackendTLSPolicy",
                   "EnvoyProxy", "ClientTrafficPolicy", "BackendTrafficPolicy", "EnvoyPatchPolicy", "EnvoyExtensionPolicy", "SecurityPolicy",
                   "EnvoyGateway", "Middleware", "MiddlewareTCP", "ServersTransport", "ServersTransportTCP", "IngressRoute", "IngressRouteTCP",
                   "IngressRouteUDP", "TLSOption", "TLSStore", "TraefikService"}


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
    text = re.sub(r"(?m)^PORTAL_DOMAIN=\S+", "PORTAL_DOMAIN=dms.corp.example", text)
    for key, value in overrides.items():
        if value is None:
            text = re.sub(rf"(?m)^#? ?{key}=.*$", "", text)
        elif re.search(rf"(?m)^#? ?{key}=", text):
            text = re.sub(rf"(?m)^#? ?{key}=\S*", lambda _m: f"{key}={value}", text)
        else:
            text += f"\n{key}={value}\n"
    return text


def tb_values(**overrides) -> str:
    text = (TESTBED / "values.env").read_text()
    for key, value in overrides.items():
        if value is None:
            text = re.sub(rf"(?m)^{key}=.*$", "", text)
        elif re.search(rf"(?m)^{key}=", text):
            text = re.sub(rf"(?m)^{key}=\S*", lambda _m: f"{key}={value}", text)
        else:
            text += f"\n{key}={value}\n"
    return text


def _render_env(out: Path) -> dict:
    return dict(re.findall(r'(?m)^([A-Z_]+)="?([^"\n]*)"?$', (out / "render.env").read_text()))


def _assert_failed(r, out: Path, *needles):
    assert r.returncode == 2, (r.stdout, r.stderr)
    for n in needles:
        assert n in r.stdout + r.stderr, (n, r.stdout, r.stderr)
    assert not (out / "render.env").exists()


def _ingress_fields(text: str) -> dict:
    """kustomize 출력(키 정렬·2칸 들여쓰기)에서 Ingress 의 계약 칸을 뽑는다 — 못 찾으면 None 이 아니라 assert 로 실패한다."""
    objs = docs(text)
    assert len(objs) == 1, [d.splitlines()[:4] for d in objs]
    ing = doc(text, "Ingress", NAME)
    assert re.search(r"(?m)^apiVersion: networking\.k8s\.io/v1$", ing) and re.search(r"(?m)^  namespace: dms$", ing), ing

    def one(pattern: str) -> str:
        m = re.findall(pattern, ing)
        assert len(m) == 1, (pattern, m, ing)
        return m[0].strip('"')

    return {
        "entrypoints": one(rf"(?m)^    {re.escape(EP_ANN)}: (\S+)$"),
        "router_tls": one(rf"(?m)^    {re.escape(TLS_ANN)}: (\S+)$"),
        "class": one(r"(?m)^  ingressClassName: (\S+)$"),
        "rule_hosts": re.findall(r"(?m)^  - host: (\S+)$", ing),
        "tls_hosts": re.findall(r"(?m)^  - hosts:\n((?:    - \S+\n)+)", ing),
        "secret": one(r"(?m)^    secretName: (\S+)$"),
        "doc": ing,
    }


def _tls_hosts(f: dict) -> list:
    assert len(f["tls_hosts"]) == 1, f["doc"]
    return [h.strip()[2:] for h in f["tls_hosts"][0].splitlines()]


# --- 정적: Envoy 경로가 사라졌다 ---------------------------------------------------------------------------------------------------

def test_envoy_layers_versions_and_lock_lines_are_gone():
    for gone in (DEPLOY / "addons" / "gateway-api", DEPLOY / "addons" / "envoy-gateway", DEPLOY / "platform" / "gateway"):
        assert not gone.exists(), gone
    versions = (DEPLOY / "versions.env").read_text(encoding="utf-8")
    for key in ("GATEWAY_API_VERSION", "ENVOY_GATEWAY_VERSION", "ENVOY_GATEWAY_IMAGE", "ENVOY_PROXY_IMAGE"):
        assert not re.search(rf"(?m)^{key}=", versions), key
    lock_dests = [ln.split()[0] for ln in (DEPLOY / "images.lock").read_text(encoding="utf-8").splitlines()
                  if ln.strip() and not ln.startswith("#")]
    assert not [d for d in lock_dests if d.startswith(("gateway:", "envoy:"))], lock_dests
    assert "envoy" not in "".join(lock_dests).lower()
    render_code = "\n".join(ln for ln in (DEPLOY / "render.sh").read_text(encoding="utf-8").splitlines()
                            if not ln.lstrip().startswith("#"))
    # 옛 키 이름(GATEWAY_ENVOY_*_REQUEST)은 「무시함」 알림을 위해 남는다 — 사라져야 하는 것은 그 값을 쓰던 코드다.
    for gone in ("GATEWAY_LAYERS", "ENVOY_GATEWAY", "ENVOY_PROXY", "EG_REF", "ENVOY_REF", "PROXY_OPTIONAL", "platform/gateway",
                 "addons/gateway-api", "addons/envoy-gateway", "ALARM_SED", "REPLACE_LOGIN_ALARM_SOURCE_FAILURES", "GATEWAY: "):
        assert gone not in render_code, gone
    assert "REPLACE_LOGIN_ALARM_SOURCE_FAILURES" not in (DEPLOY / "overlays" / "prod" / "patch-config.yaml").read_text()
    for path in (DEPLOY / "overlays" / "prod" / "values.env.example", TESTBED / "values.env"):
        assert not re.search(r"(?m)^#? ?GATEWAY_\w+=", path.read_text()), path


# --- base(사이트 중립) --------------------------------------------------------------------------------------------------------

@needs_kubectl
def test_base_is_one_standard_ingress_with_placeholder_host_and_defaults():
    f = _ingress_fields(_kustomize(BASE))
    assert f["entrypoints"] == "websecure" and f["router_tls"] == "true" and f["class"] == "traefik"
    assert f["rule_hosts"] == ["portal.set-by-overlay.invalid"] and _tls_hosts(f) == ["portal.set-by-overlay.invalid"]
    assert f["secret"] == "dms-portal-tls"
    ing = f["doc"]
    # 백엔드 = svc dms-api 의 이름 붙은 포트(http — 40-api.yaml 의 Service), 경로 / Prefix 하나.
    assert re.search(r"(?m)^      - backend:\n          service:\n            name: dms-api\n            port:\n              name: http\n"
                     r"        path: /\n        pathType: Prefix$", ing), ing
    assert len(re.findall(r"(?m)^        path: ", ing)) == 1
    svc = (DEPLOY / "k8s" / "40-api.yaml").read_text()
    assert re.search(r"(?m)^  name: dms-api\n", svc) and re.search(r"(?m)^    - name: http\n      port: 8080$", svc)
    # DMS 소유 라벨(install-detect 가 managed-by=dms-install 로 D=/P 를 가른다) — 다른 플랫폼 층과 같은 셋.
    for label in ("app.kubernetes.io/managed-by: dms-install", "app.kubernetes.io/part-of: dms", f"app.kubernetes.io/name: {NAME}"):
        assert f"    {label}\n" in ing, label


@needs_kubectl
def test_base_never_defines_a_catch_all_or_traefik_crds():
    text = _kustomize(BASE)
    kinds = re.findall(r"(?m)^kind: (\S+)$", text)
    assert kinds == ["Ingress"], kinds
    assert not FORBIDDEN_KINDS & set(kinds)
    assert "traefik.io/" not in text and "traefik.containo.us" not in text                       # kubernetescrd 에 기대지 않는다
    rules = re.findall(r"(?m)^  - (\S+?):", doc(text, "Ingress", NAME).split("\n  rules:\n", 1)[1].split("\n  tls:\n", 1)[0])
    assert rules == ["host"], rules                                                             # 규칙마다 host(catch-all 0)
    assert "defaultBackend" not in text


def test_base_is_site_neutral_and_the_name_differs_from_the_app_ingress():
    code = "\n".join(ln for ln in (BASE / "ingress.yaml").read_text(encoding="utf-8").splitlines() if not ln.lstrip().startswith("#"))
    for token in ("dms-k8s.local", "dms.local", "10.10.10.", "10.20.20.", "10.112.", "corp.example", "mason", "dmsk8s", "testbed"):
        assert token not in code, token
    assert "set-by-overlay.invalid" in code
    app = (DEPLOY / "k8s" / "46-ingress.yaml").read_text()
    assert re.search(r"(?m)^  name: dms-portal$", app) and NAME != "dms-portal"                  # 되돌림 경로(nginx)와 서로 덮지 않는다
    assert (BASE / "kustomization.yaml").read_text().count("- ingress.yaml") == 1


# --- 렌더(testbed-k8s·prod) ----------------------------------------------------------------------------------------------------

SITES = ("testbed-k8s", "prod")
WANT_HOST = {"testbed-k8s": "dms-k8s.local", "prod": "dms.corp.example"}


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    out = {}
    for site, values in (("testbed-k8s", None), ("prod", prod_values())):
        r, path = _render(tmp_path_factory.mktemp(site), site, values)
        assert r.returncode == 0, (r.stdout, r.stderr)
        out[site] = {"stdout": r.stdout, "stderr": r.stderr, "env": _render_env(path), "path": path,
                     "ingress": _kustomize(path / "platform" / "ingress") if KUBECTL else None}
    return out


@pytest.mark.parametrize("site", SITES)
def test_ingress_layer_is_announced_once_and_recorded(rendered, site):
    s = rendered[site]
    lines = s["stdout"].splitlines()
    assert [ln for ln in lines if ln.startswith("INGRESS: ")] == [f"INGRESS: {s['path'] / 'platform' / 'ingress'}"]
    assert not [ln for ln in lines if ln.startswith("GATEWAY: ")]
    assert lines[-1] == f"RENDERED: {s['path'] / 'app'}"
    env = s["env"]
    assert env["INGRESS_LAYERS"] == "platform/ingress"
    assert (env["INGRESS_CLASS"], env["TRAEFIK_ENTRYPOINT"], env["PORTAL_TLS_SECRET"]) == ("traefik", "websecure", "dms-portal-tls")
    assert env["PORTAL_DOMAIN"] == WANT_HOST[site]
    assert "PORTAL_VIP" in env
    assert not [k for k in env if k.startswith(("GATEWAY_", "ENVOY_"))], env                  # 옛 입구 키·이미지 ref 는 사라졌다
    assert RETIRED_NOTE not in s["stderr"]                                                     # 커밋된 값에는 옛 키가 없다


def test_testbed_kube_context_follows_the_ingress_layer(rendered):
    s = rendered["testbed-k8s"]
    assert expected_context(s["path"] / "platform" / "ingress") == "dmsk8s"                   # guard 의 컨텍스트 고정(exit 4)
    assert expected_context(rendered["prod"]["path"] / "platform" / "ingress") is None


@needs_kubectl
@pytest.mark.parametrize("site", SITES)
def test_rendered_ingress_carries_the_site_values(rendered, site):
    f = _ingress_fields(rendered[site]["ingress"])
    host = WANT_HOST[site]
    assert f["rule_hosts"] == [host] and _tls_hosts(f) == [host]
    assert (f["class"], f["entrypoints"], f["router_tls"], f["secret"]) == ("traefik", "websecure", "true", "dms-portal-tls")
    assert "set-by-overlay" not in rendered[site]["ingress"]


@needs_kubectl
@pytest.mark.parametrize("site", SITES)
def test_no_envoy_or_gateway_api_object_anywhere_in_the_render(rendered, site):
    root = rendered[site]["path"]
    layers = sorted(p.parent for p in root.rglob("kustomization.yaml"))
    assert root / "platform" / "ingress" in layers and root / "app" in layers, layers
    for gone in ("platform/gateway", "addons/gateway-api", "addons/envoy-gateway"):
        assert not (root / gone).exists(), gone
    for layer in layers:
        text = _kustomize(layer)
        kinds = set(re.findall(r"(?m)^kind: (\S+)$", text))
        assert not FORBIDDEN_KINDS & kinds, (layer, FORBIDDEN_KINDS & kinds)
        assert not re.search(r"(?m)^apiVersion: (gateway\.networking\.k8s\.io|gateway\.envoyproxy\.io|traefik\.io)/", text), layer
        assert not re.search(r"(?m)^  name: \S+\.(gateway\.networking\.k8s\.io|gateway\.envoyproxy\.io)$", text), layer   # CRD 0
        assert "envoy" not in text.lower(), (layer, [ln for ln in text.splitlines() if "envoy" in ln.lower()][:5])
    assert "envoy" not in (root / "render.env").read_text().lower()


@needs_kubectl
def test_prod_app_render_still_drops_the_old_ingress_and_metallb(rendered):
    text = _kustomize(rendered["prod"]["path"] / "app")
    kinds = set(re.findall(r"(?m)^kind: (\S+)$", text))
    assert not {"Ingress", "IPAddressPool", "L2Advertisement"} & kinds, kinds                 # prod 의 유일한 입구는 platform/ingress
    tb = set(re.findall(r"(?m)^kind: (\S+)$", _kustomize(rendered["testbed-k8s"]["path"] / "app")))
    assert "Ingress" in tb                                                                     # 테스트베드는 되돌림용 nginx Ingress 를 함께


def test_testbed_portal_domain_is_the_app_overlay_host():
    values = dict(re.findall(r"(?m)^([A-Z_]+)=(\S*)", (TESTBED / "values.env").read_text()))
    assert values["PORTAL_DOMAIN"] == "dms-k8s.local"
    code = "\n".join(ln for ln in (TESTBED / "patch-ingress.yaml").read_text().splitlines() if not ln.lstrip().startswith("#"))
    assert values["PORTAL_DOMAIN"] in code                                                     # 되돌림 경로와 같은 이름·같은 인증서
    assert values.get("PORTAL_TLS_SECRET", "dms-portal-tls") == "dms-portal-tls"


# --- 값 ---------------------------------------------------------------------------------------------------------------------

@needs_kubectl
def test_class_entrypoint_and_secret_come_from_values_as_strings(tmp_path):
    # 숫자만인 이름도 YAML 정수가 아니라 문자열로 들어가야 한다(어노테이션 값·ingressClassName 은 문자열 — 정수면 apiserver 가 거절).
    r, out = _render(tmp_path, "prod", prod_values(INGRESS_CLASS="traefik-ext", TRAEFIK_ENTRYPOINT="8443", PORTAL_TLS_SECRET="portal-cert.v2"))
    assert r.returncode == 0, (r.stdout, r.stderr)
    text = _kustomize(out / "platform" / "ingress")
    f = _ingress_fields(text)
    assert (f["class"], f["entrypoints"], f["secret"]) == ("traefik-ext", "8443", "portal-cert.v2")
    assert f'    {EP_ANN}: "8443"\n' in f["doc"]
    env = _render_env(out)
    assert (env["INGRESS_CLASS"], env["TRAEFIK_ENTRYPOINT"], env["PORTAL_TLS_SECRET"]) == ("traefik-ext", "8443", "portal-cert.v2")


@pytest.mark.parametrize("host", ["dms", "dms.corp.example", "a-b.c-d.example", "x1.y2", "a" * 63 + ".example"])
def test_portal_domain_accepts_lowercase_host_names(tmp_path, host):
    r, out = _render(tmp_path, "prod", prod_values(PORTAL_DOMAIN=host))
    assert r.returncode == 0, (host, r.stdout, r.stderr)
    assert _render_env(out)["PORTAL_DOMAIN"] == host


@pytest.mark.parametrize("host, why", [
    ("'*.corp.example'", "와일드카드"), ("dms.corp.example:443", "포트"), ("https://dms.corp.example", "스킴"), ("10.1.2.3", "IP"),
    ("DMS.corp.example", "소문자"), ("dms..corp", "빈 라벨"), (".dms", "빈 라벨"), ("dms.", "빈 라벨"), ("-dms.x", "- 로"),
    ("dms-.x", "- 로"), ("a/b", "경로"), ("dms_corp.x", "허용 밖"), ("'dms corp'", "허용 밖"), ("a" * 64 + ".example", "63자"),
])
def test_portal_domain_rejects_wildcards_ports_ips_and_bad_syntax(tmp_path, host, why):
    r, out = _render(tmp_path, "prod", prod_values(PORTAL_DOMAIN=host))
    _assert_failed(r, out, "PORTAL_DOMAIN", why)


@pytest.mark.parametrize("site, values", [
    ("prod", prod_values(PORTAL_DOMAIN=None)),
    ("prod", prod_values(PORTAL_DOMAIN="REPLACE_PORTAL_DOMAIN")),
    ("testbed-k8s", tb_values(PORTAL_DOMAIN=None)),                                     # 고정 오버레이도 필수(입구 host 의 원천)
    ("testbed-k8s", tb_values(PORTAL_DOMAIN="")),
])
def test_portal_domain_is_required_for_both_overlay_kinds(tmp_path, site, values):
    r, out = _render(tmp_path, site, values)
    _assert_failed(r, out, "미치환", "PORTAL_DOMAIN")
    assert "PORTAL_DOMAIN" in r.stdout.splitlines()[0].split("-->")[1].split()


def test_portal_vip_stays_optional_for_the_fixed_overlay(tmp_path):
    assert "PORTAL_VIP" not in set(re.findall(r"(?m)^([A-Z_]+)=", (TESTBED / "values.env").read_text()))
    r, out = _render(tmp_path, "testbed-k8s")
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert _render_env(out)["PORTAL_VIP"] == ""
    r, out = _render(tmp_path / "prod", "prod", prod_values(PORTAL_VIP=None))
    _assert_failed(r, out, "미치환", "PORTAL_VIP")                                              # 템플릿은 그대로 필수


@pytest.mark.parametrize("key, value", [
    ("INGRESS_CLASS", "Traefik"), ("INGRESS_CLASS", "-x"), ("INGRESS_CLASS", "x-"), ("INGRESS_CLASS", "a.b"), ("INGRESS_CLASS", "a_b"),
    ("INGRESS_CLASS", "x" * 64), ("INGRESS_CLASS", "x|y"),
    ("TRAEFIK_ENTRYPOINT", "WebSecure"), ("TRAEFIK_ENTRYPOINT", "-x"), ("TRAEFIK_ENTRYPOINT", "web_secure"), ("TRAEFIK_ENTRYPOINT", "web.secure"),
    ("TRAEFIK_ENTRYPOINT", "x" * 64), ("TRAEFIK_ENTRYPOINT", '"a"'),
    ("PORTAL_TLS_SECRET", "Dms-Portal"), ("PORTAL_TLS_SECRET", "-x"), ("PORTAL_TLS_SECRET", "a/b"), ("PORTAL_TLS_SECRET", "x|y"),
    ("PORTAL_TLS_SECRET", "a..b"), ("PORTAL_TLS_SECRET", "a.-b"), ("PORTAL_TLS_SECRET", "a-.b"),
])
def test_render_rejects_bad_ingress_values(tmp_path, key, value):
    r, out = _render(tmp_path, "prod", prod_values(**{key: f"'{value}'"}))
    _assert_failed(r, out, key)


def test_ingress_values_in_the_callers_environment_are_ignored(tmp_path):
    # values.env 만 원천(③④ 와 같은 이유) — 셸에 남은 값이 렌더를 바꾸지 않고, 셸의 옛 GATEWAY_* 는 알림도 내지 않는다.
    r, out = _render(tmp_path, "prod", prod_values(), INGRESS_CLASS="from-shell", TRAEFIK_ENTRYPOINT="web", PORTAL_TLS_SECRET="from-shell",
                     PORTAL_DOMAIN="shell.example", GATEWAY_PROXY_PROTOCOL="off", GATEWAY_LISTEN_PORT="10443")
    assert r.returncode == 0, (r.stdout, r.stderr)
    env = _render_env(out)
    assert (env["INGRESS_CLASS"], env["TRAEFIK_ENTRYPOINT"], env["PORTAL_TLS_SECRET"], env["PORTAL_DOMAIN"]) == (
        "traefik", "websecure", "dms-portal-tls", "dms.corp.example")
    assert RETIRED_NOTE not in r.stderr and "GATEWAY_" not in r.stderr
    r, out = _render(tmp_path / "tb", "testbed-k8s", tb_values(PORTAL_DOMAIN=None), PORTAL_DOMAIN="dms-k8s.local")
    _assert_failed(r, out, "미치환", "PORTAL_DOMAIN")                                            # 셸 값이 빠진 줄을 채우지 않는다


@needs_kubectl
@pytest.mark.parametrize("site", SITES)
def test_old_gateway_keys_are_announced_and_ignored(tmp_path, site):
    # 2026-11-16 이전 values.env(옛 Envoy 키 + 지금은 틀린 값) — 실패하지 않고 stderr 한 줄, 출력은 키가 없을 때와 같다.
    old = {"GATEWAY_PROXY_PROTOCOL": "bogus", "GATEWAY_LISTEN_PORT": "", "GATEWAY_ENVOY_CPU_REQUEST": "9", "GATEWAY_ENVOY_MEMORY_REQUEST": "9Ti"}
    base = prod_values() if site == "prod" else tb_values()
    with_old = base + "".join(f"\n{k}={v}" for k, v in old.items()) + "\n"
    r0, out0 = _render(tmp_path / "plain", site, base)
    r, out = _render(tmp_path / "old", site, with_old)
    assert r0.returncode == 0 and r.returncode == 0, (r.stdout, r.stderr)
    notes = [ln for ln in r.stderr.splitlines() if RETIRED_NOTE in ln]
    assert len(notes) == 1 and notes[0].startswith("note: ") and all(k in notes[0] for k in OLD_GATEWAY_KEYS), r.stderr
    assert RETIRED_NOTE not in r0.stderr
    assert r.stdout.replace(str(out), "<OUT>") == r0.stdout.replace(str(out0), "<OUT>")         # 층·줄이 같다
    assert (out / "render.env").read_text() == (out0 / "render.env").read_text()
    assert _kustomize(out / "platform" / "ingress") == _kustomize(out0 / "platform" / "ingress")
    assert not (out / "platform" / "gateway").exists()


def test_control_plane_label_value_does_not_touch_the_ingress_layer(tmp_path):
    r, out = _render(tmp_path, "prod", prod_values(CONTROL_PLANE_LABEL_VALUE="true"))
    assert r.returncode == 0, (r.stdout, r.stderr)
    kust = (out / "platform" / "ingress" / "kustomization.yaml").read_text()
    assert "control-plane" not in kust and "nodeSelector" not in kust                          # Traefik 배치는 사이트 것 — DMS 층에 배치가 없다


def test_render_stops_when_the_ingress_base_is_missing(tmp_path):
    root = _tree(tmp_path)
    shutil.rmtree(root / "platform" / "ingress")
    r, out = _render(tmp_path, "prod", prod_values())
    _assert_failed(r, out, "플랫폼 base 없음", "platform/ingress")


@needs_kubectl
def test_base_shape_drift_breaks_the_render_loudly(tmp_path):
    # JSON 패치는 경로로 바꾼다 — base 에서 tls 를 빼면 replace 가 실패해야 한다(조용히 host 없는 Ingress·평문 라우터가 되지 않게).
    root = _tree(tmp_path)
    ing = root / "platform" / "ingress" / "ingress.yaml"
    text = ing.read_text()
    ing.write_text(re.sub(r"(?ms)^  tls:\n.*?^  rules:\n", "  rules:\n", text))
    r, out = _render(tmp_path, "prod", prod_values())
    assert r.returncode == 0, (r.stdout, r.stderr)                                             # 렌더러는 kustomize 를 부르지 않는다
    k = subprocess.run([KUBECTL, "kustomize", str(out / "platform" / "ingress")], capture_output=True, text=True, timeout=120, env=ENV)
    assert k.returncode != 0 and "tls" in k.stderr, (k.stdout, k.stderr)
