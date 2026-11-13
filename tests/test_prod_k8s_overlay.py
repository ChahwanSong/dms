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


# --- 사이트 중립(2026-11-06 문서 감사 — 「배포 문서는 실제 프로덕션 기준, 테스트베드에 국한되지 않게」) ------------------------------
# base·플랫폼·애드온 층의 어느 기본값도 테스트베드 값(레지스트리·VIP·도메인·운영자 이름·공용 디렉터리·노드 이름)을 prod 매니페스트에
# 싣지 않는다. 오버레이가 덮지 않은 키는 base 값이 그대로 배포되므로, 이 그물이 「base 에 테스트베드 값을 다시 커밋한」 회귀와
# 「prod 오버레이가 사이트 키를 빠뜨린」 회귀를 함께 잡는다(kustomize 출력에는 주석이 없다 — 남는 것은 실제로 배포되는 글자다).
_TESTBED_TOKENS = ("pkg-01", "10.10.10.", "10.20.20.", "10.112.", "mason", "dms-k8s.local", "dms.local", "/dms_shared",
                   "/cephfs/dms", "/home/", "dmsk8s", "k8s-w1", "k8s-cp1", "luminous", "testbed")


@needs_kubectl
def test_rendered_prod_layers_carry_no_testbed_value(tmp_path):
    r, out = _render(tmp_path, _filled_example())
    assert r.returncode == 0, (r.stdout, r.stderr)
    env = {**os.environ, "KUBECONFIG": os.devnull}
    root = out.parent
    layers = sorted(p.parent for p in root.rglob("kustomization.yaml"))
    assert out in layers and (root / "platform" / "gateway") in layers and (root / "platform" / "db-cluster") in layers, layers
    offenders = []
    for layer in layers:
        k = subprocess.run([KUBECTL, "kustomize", str(layer)], capture_output=True, text=True, timeout=180, env=env)
        assert k.returncode == 0, (layer, k.stderr)
        for ln in k.stdout.splitlines():
            if any(t in ln.lower() for t in _TESTBED_TOKENS):
                offenders.append((layer.relative_to(root).as_posix(), ln.strip()[:160]))
    for ln in (root / "render.env").read_text().splitlines():
        if not ln.startswith("#") and any(t in ln.lower() for t in _TESTBED_TOKENS):
            offenders.append(("render.env", ln))
    assert offenders == [], offenders[:20]


def test_prod_patch_config_overrides_every_site_key_of_the_base():
    # 오버레이가 덮지 않으면 base 값이 배포되는 사이트 키(레지스트리 셋 + 특권 목록). 레지스트리 셋은 render.sh 도 FAIL 로 막지만
    # (「base 의 값(다른 사이트 레지스트리)이 배포된다」) 특권 목록은 렌더 검사가 없다 — 템플릿에서 줄이 빠지면 base 의 root,admin 만
    # 남아 values.env 의 LOCAL_ADMIN 이 조용히 특권 목록 밖이 된다(첫 관리자가 맵핑 면제를 잃는다).
    values = dict(re.findall(r'^  (DMS_[A-Z0-9_]+): "([^"]*)"', _code(PROD / "patch-config.yaml"), re.M))
    assert values["DMS_PRIVILEGED_REQUESTERS"] == "root,admin,REPLACE_LOCAL_ADMIN"
    for key in ("DMS_JOB_IMAGE", "DMS_BUILD_REGISTRY", "DMS_BUILD_BUILDER_IMAGE"):
        assert values[key].startswith("REPLACE_REGISTRY"), (key, values[key])


# --- control-plane 라벨 값(2026-11-13 OA 사이트 — RKE2 는 node-role.kubernetes.io/control-plane=true) ------------------------------------
# base·prod 의 nodeSelector 는 kubeadm 관례인 "" 를 고른다. RKE2 에선 레지스트리·PG·Envoy·api/controller 가 Pending 이라 values.env 의
# CONTROL_PLANE_LABEL_VALUE 로 네 곳(앱 patch-placement·platform/registry·platform/gateway·platform/db-cluster)의 값을 바꾼다.
_CP_LAYERS = ("platform/registry", "platform/gateway", "platform/db-cluster")
_CP_PTR = "node-role.kubernetes.io~1control-plane"


def test_control_plane_label_value_patches_every_placement_layer(tmp_path):
    base_dir = tmp_path / "default"
    base_dir.mkdir()
    r, out = _render(base_dir, _filled_example())
    assert r.returncode == 0, (r.stdout, r.stderr)
    root = out.parent
    for layer in _CP_LAYERS:                                          # 기본(키 없음) = 패치 없음 — 기존 출력과 같다
        assert _CP_PTR not in (root / layer / "kustomization.yaml").read_text(), layer
    assert 'node-role.kubernetes.io/control-plane: ""' in (out / "patch-placement.yaml").read_text()

    cp_dir = tmp_path / "rke2"
    cp_dir.mkdir()
    r, out = _render(cp_dir, _filled_example("\nCONTROL_PLANE_LABEL_VALUE=true\n"))
    assert r.returncode == 0, (r.stdout, r.stderr)
    root = out.parent
    for layer in _CP_LAYERS:
        text = (root / layer / "kustomization.yaml").read_text()
        assert re.search(r'path: /spec/\S*/nodeSelector/%s\n\s*value: "true"' % re.escape(_CP_PTR), text), (layer, text)
    placement = (out / "patch-placement.yaml").read_text()
    assert 'node-role.kubernetes.io/control-plane: ""' not in placement
    assert placement.count('node-role.kubernetes.io/control-plane: "true"') == 2      # api·controller
    # PG_PLACEMENT=all-nodes 는 nodeSelector 를 통째로 빼므로 값 패치를 더하지 않는다(없는 경로 replace 는 kustomize 실패).
    an_dir = tmp_path / "all-nodes"
    an_dir.mkdir()
    r, out = _render(an_dir, _filled_example("\nCONTROL_PLANE_LABEL_VALUE=true\nPG_PLACEMENT=all-nodes\n"))
    assert r.returncode == 0, (r.stdout, r.stderr)
    db = (out.parent / "platform" / "db-cluster" / "kustomization.yaml").read_text()
    assert _CP_PTR not in db and "path: /spec/affinity/nodeSelector\n" in db


def test_control_plane_label_value_rejects_non_label_values(tmp_path):
    for i, bad in enumerate(("a/b", "-true", "true.", "x" * 64, "tr ue")):
        d = tmp_path / str(i)
        d.mkdir()
        r, _ = _render(d, _filled_example("\nCONTROL_PLANE_LABEL_VALUE='%s'\n" % bad))     # values.env 는 sh 가 읽는다 — 공백 값은 따옴표
        assert r.returncode == 2 and "CONTROL_PLANE_LABEL_VALUE" in r.stdout, (bad, r.stdout, r.stderr)


@needs_kubectl
def test_control_plane_label_value_reaches_the_rendered_objects(tmp_path):
    # JSON 포인터 경로가 base 와 맞는지는 실제 kustomize 렌더로만 확인된다(replace 는 없는 경로에서 실패한다).
    r, out = _render(tmp_path, _filled_example("\nCONTROL_PLANE_LABEL_VALUE=true\n"))
    assert r.returncode == 0, (r.stdout, r.stderr)
    env = {**os.environ, "KUBECONFIG": os.devnull}
    root = out.parent
    for layer in ("app",) + _CP_LAYERS:
        k = subprocess.run([KUBECTL, "kustomize", str(root / layer)], capture_output=True, text=True, timeout=180, env=env)
        assert k.returncode == 0, (layer, k.stderr)
        assert re.search(r'nodeSelector:\n\s+node-role\.kubernetes\.io/control-plane: "true"', k.stdout), layer
        assert 'node-role.kubernetes.io/control-plane: ""' not in k.stdout, layer
