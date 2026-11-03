"""애드온 층 렌더 계약 — CNPG 오퍼레이터(2026-11-03 ⑤, 설계 v6 6-1·8-2 A·9 「테스트·게이트」 ⑤ test_addons_render).

설계 9 ⑤ 줄: 전 이미지가 자리표시자(base) + `@sha256` amd64 매니페스트 digest(렌더), 외부 참조 0건, `Always` 0건, `OPERATOR_IMAGE_NAME` ==
manager 이미지, 오퍼레이터에 `dms-addon-critical`. 여기에 더해 6-1 의 나머지(replicas 2·워커 배치), 8-2 A 의 라벨(managed-by dms-install +
dms.io/addon-version — selector 는 그대로), watch 범위(WATCH_NAMESPACE 없음 = 모든 네임스페이스 → dms 포함), 상류 원본의 무결성(SOURCE·
sha256 — 바이트 그대로), versions.env·images.lock·addons/<v>·라벨이 같이 움직이는지, POSTGRES_IMAGE_NAME == Cluster imageName.

「amd64 매니페스트 digest 이고 index 가 아님」은 digest 문자열만으로는 알 수 없다(상류만 안다) — 오프라인 테스트는 렌더된 digest 가 lock 의
그 줄과 같은지를 보고, lock 의 mediaType 판정은 `images-lock.py verify`(인터넷 노드, S-1)가 한다. DMS_NETWORK_TESTS=1 이면 여기서도 돌린다.
렌더는 실제 렌더러(deploy/render.sh — 사본 트리) + `kubectl kustomize` 로컬 렌더뿐이다(KUBECONFIG=/dev/null).
"""
import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from overlay_render import KUBECTL, REPO_ROOT, doc, docs, needs_kubectl

DEPLOY = REPO_ROOT / "deploy"
ENV = {**os.environ, "KUBECONFIG": os.devnull}
PLACEHOLDER = "set-by-overlay.invalid"
EXTERNAL_HOSTS = ("ghcr.io", "docker.io", "quay.io", "registry.k8s.io", "gcr.io", "mcr.microsoft.com", "public.ecr.aws")


def _versions() -> dict:
    out = {}
    for line in (DEPLOY / "versions.env").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Z_]+)=(\S+)$", line)
        if m:
            assert m.group(1) not in out, f"versions.env 에 {m.group(1)} 가 둘이다"
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
CNPG_DIR = DEPLOY / "addons" / "cnpg" / VERSIONS["CNPG_VERSION"]
UPSTREAM = CNPG_DIR / f"cnpg-{VERSIONS['CNPG_VERSION']}.yaml"


def _kustomize(path: Path) -> str:
    r = subprocess.run([KUBECTL, "kustomize", str(path)], capture_output=True, text=True, timeout=180, env=ENV)
    assert r.returncode == 0, f"kubectl kustomize {path} 실패: {r.stderr}"
    return r.stdout


def _render_site(tmp_path: Path, site: str, values_text=None) -> Path:
    root = tmp_path / "deploy"
    if not root.exists():
        root.mkdir(parents=True)
        shutil.copy(DEPLOY / "render.sh", root / "render.sh")
        for d in ("platform", "k8s", "overlays", "addons"):
            shutil.copytree(DEPLOY / d, root / d, ignore=shutil.ignore_patterns(".prod-rendered", "values.env.local"))
        for name in ("versions.env", "images.lock"):
            shutil.copy(DEPLOY / name, root / name)
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(tmp_path)}
    if values_text is not None:
        vals = tmp_path / f"{site}.values.env"
        vals.write_text(values_text)
        env["VALUES_ENV"] = str(vals)
    r = subprocess.run(["sh", str(root / "render.sh"), site], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, (r.stdout, r.stderr)
    return root / ".rendered" / site


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


def _operator(text: str) -> str:
    return doc(text, "Deployment", "cnpg-controller-manager")


def _env_value(dep: str, name: str):
    m = re.search(rf"(?m)^        - name: {re.escape(name)}\n          value: (\S+)$", dep)
    return m.group(1) if m else None


def _image_refs(text: str) -> list:
    """이미지 필드 + 이미지를 담는 env 값(…IMAGE… 이름). CRD 스키마의 설명문은 보지 않는다."""
    refs = re.findall(r"(?m)^\s*(?:- )?image: (\S+)$", text)
    refs += re.findall(r"(?m)^\s*- name: [A-Z_]*IMAGE[A-Z_]*\n\s*value: (\S+)$", text)
    return refs


# --- 상류 원본·버전 고정(정적) -------------------------------------------------------------------------------------------------

def test_upstream_manifest_is_byte_for_byte_the_verified_release():
    data = UPSTREAM.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    recorded = (CNPG_DIR / f"{UPSTREAM.name}.sha256").read_text().split()
    assert recorded == [digest, UPSTREAM.name], recorded               # sha256sum -c 형식
    source = (CNPG_DIR / "SOURCE").read_text(encoding="utf-8")
    v = VERSIONS["CNPG_VERSION"]
    assert digest in source and f"releases/download/v{v}/{UPSTREAM.name}" in source and "cosign verify-blob" in source
    assert "Verified OK" in source
    # 상류 그대로의 흔적(DMS 패치는 kustomize 층에만 있다): 상류 이미지·Always 가 원본에 남아 있어야 한다.
    text = data.decode()
    assert f"image: ghcr.io/cloudnative-pg/cloudnative-pg:{v}" in text and "imagePullPolicy: Always" in text
    assert "dms-install" not in text and PLACEHOLDER not in text


def test_versions_lock_addon_dir_and_label_move_together():
    v = VERSIONS["CNPG_VERSION"]
    assert re.fullmatch(r"1\.30\.\d+", v), v                            # 설계 6-5: 1.30.x 고정
    lock = _lock()
    assert VERSIONS["CNPG_OPERATOR_IMAGE"] == f"cloudnative-pg:{v}"
    assert lock[VERSIONS["CNPG_OPERATOR_IMAGE"]][0] == f"ghcr.io/cloudnative-pg/cloudnative-pg:{v}"
    pg = VERSIONS["PG_IMAGE"]
    assert re.fullmatch(r"postgresql:17\.\d+-\d{12}-minimal-trixie", pg), pg   # PG 17(#27), 날짜 붙은 불변 태그(GC 보호)
    assert lock[pg][0] == f"ghcr.io/cloudnative-pg/{pg}"
    kust = (CNPG_DIR / "kustomization.yaml").read_text()
    assert re.search(rf'(?m)^      dms\.io/addon-version: "{re.escape(v)}"$', kust)
    assert re.search(rf"(?m)^              value: {re.escape(PLACEHOLDER)}/cloudnative-pg:{re.escape(v)}$",
                     (CNPG_DIR / "patch-operator.yaml").read_text())
    # 옛 버전 디렉터리가 남아 있어도 versions.env 가 고른 하나만 렌더된다 — 지금은 하나뿐이어야 한다(업그레이드 때 옛 것을 지운다).
    assert sorted(p.name for p in (DEPLOY / "addons" / "cnpg").iterdir() if p.is_dir()) == [v]


def test_lock_passes_the_offline_format_check():
    r = subprocess.run([sys.executable, str(DEPLOY / "images-lock.py"), "check"], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and r.stdout.rstrip().endswith("RESULT: PASS"), r.stdout


@pytest.mark.skipif(os.environ.get("DMS_NETWORK_TESTS") != "1", reason="상류 레지스트리 조회(index 아님·linux/amd64) — DMS_NETWORK_TESTS=1")
def test_lock_digests_are_linux_amd64_single_manifests_upstream():
    r = subprocess.run([sys.executable, str(DEPLOY / "images-lock.py"), "verify"], capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr


# --- base(사이트 중립) --------------------------------------------------------------------------------------------------------

@needs_kubectl
def test_base_has_only_placeholder_images_and_no_external_reference():
    text = _kustomize(CNPG_DIR)
    refs = _image_refs(text)
    assert refs and all(r.startswith(PLACEHOLDER + "/") for r in refs), refs
    for host in EXTERNAL_HOSTS:
        assert host not in text, host                                   # 설명문까지 0건(상류 원본도 image·env 두 곳뿐)


# --- 렌더(testbed-k8s·prod) ----------------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    out = {}
    tb = _render_site(tmp_path_factory.mktemp("tb"), "testbed-k8s")
    out["testbed-k8s"] = (tb, _kustomize(tb / "addons" / "cnpg"), _render_env(tb))
    pr = _render_site(tmp_path_factory.mktemp("prod"), "prod", prod_values())
    out["prod"] = (pr, _kustomize(pr / "addons" / "cnpg"), _render_env(pr))
    return out


SITES = ("testbed-k8s", "prod")


@needs_kubectl
@pytest.mark.parametrize("site", SITES)
def test_every_image_is_the_addon_registry_plus_the_lock_digest(rendered, site):
    _, text, env = rendered[site]
    lock = _lock()
    reg = env["ADDON_REGISTRY"]
    want_op = f"{reg}/{VERSIONS['CNPG_OPERATOR_IMAGE']}@{lock[VERSIONS['CNPG_OPERATOR_IMAGE']][1]}"
    want_pg = f"{reg}/{VERSIONS['PG_IMAGE']}@{lock[VERSIONS['PG_IMAGE']][1]}"
    refs = _image_refs(text)
    assert set(refs) == {want_op, want_pg}, refs
    for ref in refs:
        assert re.fullmatch(r"[^@\s]+:[^@\s/]+@sha256:[0-9a-f]{64}", ref), ref     # 태그(GC 보호) + digest(pull)
    assert env["CNPG_OPERATOR_REF"] == want_op and env["PG_IMAGE_REF"] == want_pg
    assert PLACEHOLDER not in text
    for host in EXTERNAL_HOSTS:
        assert host not in text, host                                   # airgap: 외부 참조 0건(6-1 함정 1·3)


@needs_kubectl
@pytest.mark.parametrize("site", SITES)
def test_operator_image_name_equals_the_manager_image(rendered, site):
    dep = _operator(rendered[site][1])
    image = re.search(r"(?m)^        image: (\S+)$", dep).group(1)
    assert _env_value(dep, "OPERATOR_IMAGE_NAME") == image             # 6-1 함정 1: 둘 다(인스턴스 파드의 bootstrap init 이미지)
    assert _env_value(dep, "POSTGRES_IMAGE_NAME") == rendered[site][2]["PG_IMAGE_REF"]


@needs_kubectl
@pytest.mark.parametrize("site", SITES)
def test_no_always_pull_policy_anywhere(rendered, site):
    text = rendered[site][1]
    policies = re.findall(r"(?m)^\s*imagePullPolicy: (\S+)$", text)
    assert policies == ["IfNotPresent"], policies                       # 상류의 하나뿐인 Always → IfNotPresent(6-1 함정 2)
    dep = _operator(text)
    # 이미지가 있는 곳(컨테이너)마다 정책이 명시돼 있다 — 키를 빼면 태그가 latest 일 때 기본값이 Always 다. initContainer 는 없다.
    assert len(re.findall(r"(?m)^\s*image: ", text)) == len(policies) and "initContainers" not in dep


@needs_kubectl
@pytest.mark.parametrize("site", SITES)
def test_operator_is_two_replicas_on_workers_with_the_addon_priority(rendered, site):
    dep = _operator(rendered[site][1])
    assert re.search(r"(?m)^  replicas: 2$", dep)
    assert re.search(r"(?m)^      priorityClassName: dms-addon-critical$", dep)
    assert re.search(r"(?m)^          requiredDuringSchedulingIgnoredDuringExecution:\n            nodeSelectorTerms:\n"
                     r"            - matchExpressions:\n              - key: node-role.kubernetes.io/control-plane\n"
                     r"                operator: DoesNotExist$", dep)
    assert "tolerations" not in dep and "nodeSelector:" not in dep      # control-plane taint 를 견디지 않는다 = 워커만
    # 참조하는 PriorityClass 는 플랫폼 층에 있다(S4 가 S9 보다 먼저 만든다).
    pc = (DEPLOY / "platform" / "priorityclass" / "priorityclass.yaml").read_text()
    assert re.search(r"(?m)^  name: dms-addon-critical$", pc)


@needs_kubectl
@pytest.mark.parametrize("site", SITES)
def test_install_labels_on_every_object_but_never_in_selectors(rendered, site):
    text = rendered[site][1]
    v = VERSIONS["CNPG_VERSION"]
    objects = docs(text)
    assert len(objects) > 20
    for d in objects:
        meta = d.split("\nspec:", 1)[0]
        assert "app.kubernetes.io/managed-by: dms-install" in meta, d.splitlines()[:6]
        assert re.search(rf'(?m)^    dms\.io/addon-version: "?{re.escape(v)}"?$', meta), d.splitlines()[:6]
    dep = _operator(text)
    sel = re.search(r"(?m)^  selector:\n    matchLabels:\n((?:      .*\n)+)", dep).group(1)
    assert sel.strip() == "app.kubernetes.io/name: cloudnative-pg"     # selector 는 바꿀 수 없는 필드 — 상류 그대로
    tmpl = re.search(r"(?m)^  template:\n    metadata:\n      labels:\n((?:        .*\n)+)", dep).group(1)
    assert tmpl.strip() == "app.kubernetes.io/name: cloudnative-pg"


@needs_kubectl
@pytest.mark.parametrize("site", SITES)
def test_watch_scope_is_every_namespace_so_it_includes_dms(rendered, site):
    # 설계 8-2: watch 범위에 dms 가 없으면 X. DMS 설치본은 WATCH_NAMESPACE 를 두지 않는다(상류 기본 = 모든 네임스페이스) — 오퍼레이터 설정
    # ConfigMap/Secret(cnpg-controller-manager-config)도 만들지 않는다(거기 WATCH_NAMESPACE 가 있으면 env 처럼 범위를 좁힌다).
    text = rendered[site][1]
    assert "WATCH_NAMESPACE" not in text
    names = [n for n in re.findall(r"(?m)^  name: (\S+)$", text)]
    assert "cnpg-controller-manager-config" not in names
    assert "--config-map-name=cnpg-controller-manager-config" in _operator(text)    # 상류 인자 그대로(감지기가 읽는 이름)


@needs_kubectl
@pytest.mark.parametrize("site", SITES)
def test_webhooks_and_namespace_stay_upstream(rendered, site):
    text = rendered[site][1]
    assert re.search(r"(?m)^kind: Namespace$", text) and re.search(r"(?m)^  name: cnpg-system$", text)
    svc = doc(text, "Service", "cnpg-webhook-service")
    assert re.search(r"(?m)^  - port: 443\n    targetPort: 9443$", svc)                # S9 의 9443 경로(설계 #7)
    # 웹훅은 상류 그대로 failurePolicy Fail — apiserver → 워커 파드 9443 이 막히면 Cluster 생성·변경이 「failed calling webhook」(S9 dry-run 이 실증).
    hooks = [d for d in docs(text) if re.search(r"(?m)^kind: (Mutating|Validating)WebhookConfiguration$", d)]
    assert len(hooks) == 2
    for d in hooks:
        assert set(re.findall(r"(?m)^  failurePolicy: (\S+)$", d)) == {"Fail"}
        assert set(re.findall(r"(?m)^      name: (\S+)\n      namespace: (\S+)$", d)) == {("cnpg-webhook-service", "cnpg-system")}


@needs_kubectl
def test_addon_registry_is_the_in_cluster_registry_even_when_the_app_is_external(tmp_path):
    # 클러스터 내 레지스트리가 있으면(REGISTRY_CLUSTER_IP) 애드온은 거기서 받는다 — S5 가 lock 이미지를 그리로 push 한다(앱이 external 이어도).
    out = _render_site(tmp_path, "prod", prod_values(REGISTRY_MODE="external", REGISTRY="registry.corp.example:5000"))
    env = _render_env(out)
    assert env["ADDON_REGISTRY"] == "10.96.0.50:5000" and env["REGISTRY"] == "registry.corp.example:5000"
    out = _render_site(tmp_path / "noip", "prod", prod_values(REGISTRY_MODE="external", REGISTRY="registry.corp.example:5000",
                                                              REGISTRY_CLUSTER_IP=None, SHARED_DIR_ID=None))
    env = _render_env(out)
    assert env["ADDON_REGISTRY"] == "registry.corp.example:5000"
    assert env["CNPG_OPERATOR_REF"].startswith("registry.corp.example:5000/cloudnative-pg:")


# --- guard-images.sh 가 오퍼레이터를 그 네임스페이스에서 본다(2026-11-03 ⑤) ---------------------------------------------------------

_SHIM = r"""#!/bin/sh
printf '%s\n' "$*" >> "$FAKE_LOG"
if [ "$1" = "kustomize" ]; then cat "$FAKE_RENDER"; exit 0; fi
if [ "$1" = "config" ]; then [ "$2" = "get-contexts" ] && [ "$3" = "dmsk8s" ] && exit 0; exit 1; fi
case "$*" in
  *"-n cnpg-system get deploy cnpg-controller-manager"*"containers[0].image"*) printf '%s' "$FAKE_LIVE"; exit 0 ;;
esac
echo 'Error from server (NotFound)' >&2; exit 1
"""


@needs_kubectl
def test_guard_reads_the_operator_in_its_namespace_and_refuses_an_unflagged_upgrade(tmp_path, rendered):
    layer, text, env = rendered["testbed-k8s"]
    layer = layer / "addons" / "cnpg"
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "kubectl").write_text(_SHIM)
    (bindir / "kubectl").chmod(0o755)
    (tmp_path / "r.yaml").write_text(text)

    def run(live, **extra):
        log = tmp_path / "log"
        log.write_text("")
        e = {"PATH": f"{bindir}{os.pathsep}/usr/bin:/bin", "FAKE_LOG": str(log), "FAKE_RENDER": str(tmp_path / "r.yaml"),
             "FAKE_LIVE": live, **extra}
        r = subprocess.run(["sh", str(DEPLOY / "overlays" / "guard-images.sh"), str(layer), "--context", "dmsk8s"],
                           env=e, capture_output=True, text=True, timeout=60)
        return r, log.read_text()

    r, calls = run(env["CNPG_OPERATOR_REF"])
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "-n cnpg-system get deploy cnpg-controller-manager" in calls     # dms 가 아니다 — 그랬다면 늘 「첫 설치」로 통과했다
    assert "--server-side --field-manager=dms-install" in r.stdout
    r, _ = run("10.112.0.50:5000/cloudnative-pg:1.29.3@sha256:" + "0" * 64)
    assert r.returncode == 3 and "CNPG 업그레이드" in r.stderr and "§2f-6" in r.stderr, r.stderr
    r, _ = run("10.112.0.50:5000/cloudnative-pg:1.29.3@sha256:" + "0" * 64, ALLOW_IMAGE_CHANGE="1")
    assert r.returncode == 0, (r.stdout, r.stderr)
