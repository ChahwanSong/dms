"""dms-k8s 프로덕션 오버레이(deploy/overlays/prod)의 k8s 신원 모드 전용 계약(2026-10-25 → 2026-10-26 LDAP 코드 삭제).

사용자 결정(2026-10-21 「dms-k8s 브런치에서는 LDAP 을 사용 안할거야」 → 2026-10-26 「(d) LDAP 코드 삭제 및 정리」)과 RBAC·PV
설계 D1(k8s 신원 모드 = 모든 잡 root + 권한 = k8s RoleBinding 맵핑). base 에 LDAP 키·에이전트 LDAP env·nslcd 엔트리포인트가
없어 오버레이는 사이트 값만 덮는다(예전의 LDAP null 패치·patch-agent.yaml 은 지웠다). 세 층으로 고정한다:

- 정적: 오버레이 템플릿(patch-config·kustomization)·values.env.example·render.sh·install.sh 의 모양.
- render.sh(실제 sh): values.env.example 을 채워 렌더 — 통과하고, 옛 values.env 의 LDAP_*·IDENTITY_SUPPLEMENTARY_GROUPS 는
  무시(알림 한 줄).
- 렌더 산출물을 `kubectl kustomize`(로컬 렌더 — 클러스터 무관)로: 'ldap' 0건, 에이전트 명령, 서버 설정으로 실제 기동.
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

from dms.config import Settings
from overlay_render import KUBECTL, REPO_ROOT, config_data, doc, needs_kubectl, seq

PROD = REPO_ROOT / "deploy" / "overlays" / "prod"
RENDER = REPO_ROOT / "deploy" / "render.sh"          # 렌더러 본체(2026-11-03 ④ — prod/render.sh 는 호환 진입점)
BASE_CONFIG = REPO_ROOT / "deploy" / "k8s" / "20-config.yaml"
BASE_AGENT = REPO_ROOT / "deploy" / "k8s" / "50-agent-daemonset.yaml"
INSTALL = REPO_ROOT / "deploy" / "install.sh"


def _code(path: Path) -> str:
    return "\n".join(ln for ln in path.read_text(encoding="utf-8").splitlines()
                     if not ln.lstrip().startswith("#"))


# --- 정적 -------------------------------------------------------------------------------------------

def test_patch_config_carries_site_values_and_no_ldap():
    text = _code(PROD / "patch-config.yaml")
    assert "DMS_LDAP_" not in text and "DMS_IDENTITY_SUPPLEMENTARY_GROUPS" not in text
    values = dict(re.findall(r'^  (DMS_[A-Z0-9_]+): "([^"]*)"', text, re.M))
    assert "DMS_IDENTITY_MODE" not in values                      # base 의 "k8s"(유일한 모드)
    assert values["DMS_USER_ALLOWED_OPERATIONS"] == "sync,rm"   # scan 은 특권 운영자만(2026-10-26)
    assert values["DMS_ALLOW_PRIVILEGED_REQUESTERS"] == "true"
    assert values["DMS_ACCOUNT_VERIFICATION_REQUIRED"] == "true"   # 맵핑 ID = 계정 아이디의 결속 장치


def test_kustomization_wires_the_site_patches_only():
    text = _code(PROD / "kustomization.yaml")
    for patch in ("patch-config.yaml", "patch-remove-ingress.yaml", "patch-placement.yaml"):
        assert f"- path: {patch}" in text, patch
    assert "patch-agent" not in text and not (PROD / "patch-agent.yaml").exists()   # base 가 `dms agent`
    # 2026-11-03 ⑥(설계 #13·9 ⑥): prod 입구는 Envoy Gateway — 옛 Ingress 도메인·MetalLB VIP 패치는 없어졌다(삭제 패치가 대신한다).
    for gone in ("patch-ingress.yaml", "patch-metallb.yaml"):
        assert gone not in text and not (PROD / gone).exists(), gone


def test_remove_patch_deletes_exactly_the_ingress_and_the_metallb_objects():
    # 공용 base(46-ingress·47-metallb)는 고치지 않는다(testbed-k8s 가 T8 동안 쓰고, main·dms-ssc 와 같은 파일) — prod 렌더에서만 지운다.
    docs_ = [d for d in re.split(r"(?m)^---\s*$", _code(PROD / "patch-remove-ingress.yaml")) if d.strip()]
    got = sorted((re.search(r"(?m)^kind: (\S+)$", d).group(1), re.search(r"(?m)^  name: (\S+)$", d).group(1)) for d in docs_)
    assert got == [("IPAddressPool", "dms-public-pool"), ("Ingress", "dms-portal"), ("L2Advertisement", "dms-public-l2")]
    assert all(re.search(r"(?m)^\$patch: delete$", d) for d in docs_)
    base = REPO_ROOT / "deploy" / "k8s"
    assert "kind: Ingress" in (base / "46-ingress.yaml").read_text()                   # base 는 그대로(testbed-k8s 병행)
    assert "kind: IPAddressPool" in (base / "47-metallb-public-pool.yaml").read_text()


def test_values_render_and_install_need_no_ldap():
    example = _code(PROD / "values.env.example")
    assert not re.findall(r"(?m)^LDAP_\w+=", example)
    render_sh = RENDER.read_text() + (PROD / "render.sh").read_text()
    keys = re.search(r'^KEYS="([^"]*)"', render_sh, re.M)
    assert keys and "LDAP" not in keys.group(1), keys
    assert "REPLACE_LDAP" not in render_sh and "REPLACE_IDENTITY_SUPPLEMENTARY_GROUPS" not in render_sh
    assert "patch-agent.yaml" not in render_sh
    assert "IDENTITY_SUPPLEMENTARY_GROUPS=" not in example
    secret_keys = re.search(r"^\s*for k in (DMS_[A-Z_ ]+); do", _code(INSTALL), re.M)
    assert secret_keys and secret_keys.group(1).split() == [
        "DMS_DATABASE_URL", "DMS_SHARED_TOKEN", "DMS_ADMIN_TOKEN", "DMS_SESSION_SECRET"]


def test_installer_and_preflight_no_longer_gate_on_metallb_or_ingress_nginx():
    # 2026-11-03 ⑦(설계 9 ⑦ — install.sh :74-80·135-138): prod 입구는 Envoy Gateway — MetalLB·IngressClass nginx 가 없다고 멈추거나
    # ingress-nginx Service 에 풀 어노테이션을 다는 줄이 설치기·preflight 에 남아 있으면 prod 에서 install.sh 를 쓸 수 없다(설계 12 위험 19).
    for path in (INSTALL, REPO_ROOT / "deploy" / "preflight-cluster.sh"):
        code = _code(path)
        for gone in ("get ns metallb-system", "ingressclass", "ipaddresspools.metallb.io", "metallb.io/address-pool",
                     "ingress-nginx-controller", "PORTAL_VIP=<"):
            assert gone not in code, (path.name, gone)
    assert "rv PORTAL_TLS_SECRET" in _code(INSTALL)                                 # TLS 게이트는 render.env 의 이름(⑥)


# --- render.sh -------------------------------------------------------------------------------------

def _filled_example(extra: str = "") -> str:
    text = (PROD / "values.env.example").read_text()
    text = re.sub(r"\bREPLACE_([A-Z_]+)", lambda m: "v-" + m.group(1).lower().replace("_", "-"), text)
    # SHARED_DIR 는 절대 경로여야 렌더가 통과한다(2026-11-03 ③ -- render.sh 형식 검사). REGISTRY_CLUSTER_IP 는 IPv4(④).
    text = re.sub(r"(?m)^REGISTRY_CLUSTER_IP=\S+", "REGISTRY_CLUSTER_IP=10.96.0.50", text)
    return re.sub(r"(?m)^SHARED_DIR=\S+", "SHARED_DIR=/v-shared-dir", text) + extra


def _render(tmp_path, values_text):
    prod = tmp_path / "overlays" / "prod"
    shutil.copytree(PROD, prod, ignore=shutil.ignore_patterns("values.env"))
    shutil.copytree(REPO_ROOT / "deploy" / "k8s", tmp_path / "k8s")      # ../../../k8s 가 렌더 산출물에서 풀리게
    shutil.copytree(REPO_ROOT / "deploy" / "platform", tmp_path / "platform")
    shutil.copytree(REPO_ROOT / "deploy" / "addons", tmp_path / "addons")         # ⑤ 애드온·DB 층(2026-11-03)
    for name in ("versions.env", "images.lock"):
        shutil.copy(REPO_ROOT / "deploy" / name, tmp_path / name)
    shutil.copy(RENDER, tmp_path / "render.sh")                           # prod/render.sh(호환 진입점)가 ../../render.sh 로 넘긴다
    vals = tmp_path / "values.env"
    vals.write_text(values_text)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    shim = bindir / "kubectl"
    shim.write_text("#!/bin/sh\nexit 97\n")                               # 명시 태그라 불리면 안 된다
    shim.chmod(0o755)
    env = {"PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}", "VALUES_ENV": str(vals)}
    r = subprocess.run(["sh", str(prod / "render.sh")], env=env, capture_output=True, text=True, timeout=60)
    return r, tmp_path / ".rendered" / "prod" / "app"


def test_render_succeeds_without_ldap_values(tmp_path):
    r, out = _render(tmp_path, _filled_example())
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert r.stdout.strip().splitlines()[-1].startswith("RENDERED: ")
    assert "note:" not in r.stderr
    leftover = [ln for f in out.iterdir() for ln in f.read_text().splitlines()
                if "REPLACE_" in ln and not ln.lstrip().startswith("#")]
    assert leftover == []


def test_old_values_env_with_ldap_keys_still_renders_and_says_they_are_ignored(tmp_path):
    old = _filled_example("\nLDAP_HOST=ldap.corp.example\nLDAP_USER_BASE=ou=People\nIDENTITY_SUPPLEMENTARY_GROUPS=yes\n")
    r, out = _render(tmp_path, old)
    assert r.returncode == 0, (r.stdout, r.stderr)                 # 옛 스위치의 이상한 값도 실패하지 않는다(읽지 않는다)
    assert "LDAP_*·IDENTITY_SUPPLEMENTARY_GROUPS 는 dms-k8s" in r.stderr
    rendered = (out / "patch-config.yaml").read_text()
    assert "ldap.corp.example" not in rendered and "SUPPLEMENTARY" not in rendered


@needs_kubectl
def test_rendered_overlay_has_no_ldap_and_boots_in_k8s_mode(tmp_path):
    r, out = _render(tmp_path, _filled_example())
    assert r.returncode == 0, (r.stdout, r.stderr)
    k = subprocess.run([KUBECTL, "kustomize", str(out)], capture_output=True, text=True, timeout=120,
                       env={**os.environ, "KUBECONFIG": os.devnull})
    assert k.returncode == 0, k.stderr
    text = k.stdout
    offenders = [ln.strip() for ln in text.splitlines() if "ldap" in ln.lower()]
    assert offenders == [], offenders[:10]
    ds = doc(text, "DaemonSet", "dms-agent")
    assert seq(ds, "command") == ["dms"] and seq(ds, "args") == ["agent"]
    assert re.findall(r"secretKeyRef:\n\s*key: (\w+)", ds) == ["DMS_SHARED_TOKEN"]
    data = config_data(text)
    assert (data["DMS_IDENTITY_MODE"], data["DMS_USER_ALLOWED_OPERATIONS"]) == ("k8s", "sync,rm")
    assert data["DMS_PRIVILEGED_REQUESTERS"] == "root,admin,v-local-admin"
    settings = Settings.from_env({**data, "DMS_DATABASE_URL": "postgresql://u:p@h/d", "DMS_SHARED_TOKEN": "s",
                                  "DMS_ADMIN_TOKEN": "a", "DMS_SESSION_SECRET": "x"})
    assert settings.allow_privileged_requesters is True  # 기동 = k8s 신원 모드(모든 잡 root -- 끄면 기동 거부)


@needs_kubectl
def test_rendered_prod_has_no_ingress_or_metallb_and_the_gateway_is_rendered(tmp_path):
    # 2026-11-03 ⑥(설계 v6 #13 — prod 는 MetalLB·ingress-nginx 의존을 없앤다): 앱 렌더에 Ingress·MetalLB CR 0건(metallb CRD 가 없는 prod 에서
    # apply 가 「no matches for kind」로 실패하던 자리), 입구는 플랫폼 층 platform/gateway 의 Gateway·HTTPRoute 다(PROXY 필수가 기본).
    r, out = _render(tmp_path, _filled_example())
    assert r.returncode == 0, (r.stdout, r.stderr)
    env = {**os.environ, "KUBECONFIG": os.devnull}
    k = subprocess.run([KUBECTL, "kustomize", str(out)], capture_output=True, text=True, timeout=120, env=env)
    assert k.returncode == 0, k.stderr
    kinds = re.findall(r"(?m)^kind: (\S+)$", k.stdout)
    assert not {"Ingress", "IPAddressPool", "L2Advertisement"} & set(kinds), kinds
    assert "metallb" not in k.stdout.lower() and "ingress" not in k.stdout.lower()
    doc(k.stdout, "Service", "dms-api")                                   # HTTPRoute 의 백엔드는 그대로 있다
    gw_dir = out.parent / "platform" / "gateway"
    assert f"GATEWAY: {gw_dir}" in r.stdout
    g = subprocess.run([KUBECTL, "kustomize", str(gw_dir)], capture_output=True, text=True, timeout=120, env=env)
    assert g.returncode == 0, g.stderr
    assert re.search(r"(?m)^kind: Gateway$", g.stdout) and re.search(r"(?m)^kind: HTTPRoute$", g.stdout)
    assert re.search(r"(?m)^  proxyProtocol:\n    optional: false$", g.stdout)
    renv = (out.parent / "render.env").read_text()
    assert "PORTAL_DOMAIN=v-portal-domain" in renv and "PORTAL_VIP=v-portal-vip" in renv   # 매니페스트가 아니라 render.env 로만
