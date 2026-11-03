"""공용 디렉터리(SHARED_DIR) 렌더 계약(2026-11-03 ③, 설계 v6 3-1·3-2·9 「③ 공유 FS 세부」·T3).

DMS 상태는 사이트가 모든 마스터·워커의 같은 호스트 경로에 미리 마운트한 공용 스토리지 `<SHARED_DIR>` 아래 `dms/` 에 둔다.
api·controller 는 `<SHARED_DIR>/dms/artifacts` 하나만 hostPath(type Directory)로 같은 경로에 붙이고, 아티팩트 base(잡 파드가
붙이는 경로)·allowlist 도 같은 값이다. 여기서 고정하는 것:

- base(deploy/k8s): 경로는 사이트 중립 자리표시자뿐(raw apply 는 없는 경로라 뜨지 않는다), /cephfs 가 코드 줄에 없다,
  DirectoryOrCreate 가 어디에도 없다(마운트 없는 노드에 로컬 디렉터리를 만들어 G2 fail-closed 를 깬다).
- 렌더(prod 는 render.sh + 견본 values.env, testbed-k8s 는 그대로): api/controller hostPath == mountPath == allowlist ==
  env base(scheme 뗀 것) == `<SHARED_DIR>/dms/artifacts`, 그 경로가 `<SHARED_DIR>/dms/` 아래이고 SHARED_DIR 자체가 아니다.
  testbed-k8s 는 렌더 단계가 없어 커밋한 values.env 의 SHARED_DIR 와 patch-config 의 base 가 같은지 본다.
- render.sh: 옛 SHARED_FS 가 남은 values.env 는 FAIL + 「SHARED_DIR=/<옛 값>」 안내, SHARED_DIR 형식 위반은 FAIL.

PyYAML 없이(저장소 의존성이 아니다) kustomize 출력의 고정된 모양에 기대는 최소 정규식이다 -- 못 찾으면 assert 로 실패한다.
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from overlay_render import KUBECTL, OVERLAYS, REPO_ROOT, config_data, doc, needs_kubectl, render

BASE = REPO_ROOT / "deploy" / "k8s"
PROD = OVERLAYS / "prod"
RENDER = REPO_ROOT / "deploy" / "render.sh"          # 렌더러 본체(2026-11-03 ④ — prod/render.sh 는 호환 진입점)
TESTBED = OVERLAYS / "testbed-k8s"
PLACEHOLDER_PATH = "/set-by-overlay.invalid/dms/artifacts"
# 설계 v6 3-2 의 SHARED_DIR 형식(render.sh·shared-storage-probe.py 와 같은 규칙).
_SHARED_DIR_RE = re.compile(r"^/[A-Za-z0-9._/-]+$")
CONTROL_PLANE = {"dms-api": "api", "dms-controller": "controller"}


def _code(path: Path) -> str:
    return "\n".join(ln for ln in path.read_text(encoding="utf-8").splitlines() if not ln.lstrip().startswith("#"))


def _valid_shared_dir(value: str) -> bool:
    return (bool(_SHARED_DIR_RE.fullmatch(value)) and value != "/" and not value.endswith("/")
            and os.path.normpath(value) == value and "//" not in value)


def _values_env(path: Path) -> dict:
    return dict(re.findall(r"(?m)^([A-Z_]+)=(\S*)", path.read_text(encoding="utf-8")))


def _artifact_mounts(rendered: str) -> dict:
    """{deployment: (hostPath.path, hostPath.type, container mountPath)} -- dms-artifacts 볼륨."""
    out = {}
    for name, container in CONTROL_PLANE.items():
        d = doc(rendered, "Deployment", name)
        vol = re.search(r"(?m)^      - hostPath:\n          path: (\S+)\n          type: (\S+)\n        name: dms-artifacts$", d)
        assert vol, f"{name}: dms-artifacts hostPath 볼륨을 못 찾았다"
        mount = re.search(r"(?m)^        - mountPath: (\S+)\n(?:          \S.*\n)*?          name: dms-artifacts$", d)
        assert mount, f"{name}: dms-artifacts volumeMount 를 못 찾았다"
        assert f"name: {container}" in d
        out[name] = (vol.group(1), vol.group(2), mount.group(1))
    return out


def _assert_all_paths_are(rendered: str, shared_dir: str):
    want = f"{shared_dir}/dms/artifacts"
    assert want.startswith(f"{shared_dir}/dms/") and want != shared_dir
    data = config_data(rendered)
    assert data["DMS_ARTIFACT_BASE_URI"] == f"file://{want}", data["DMS_ARTIFACT_BASE_URI"]
    assert data["DMS_ARTIFACT_BASE_ALLOWED_PREFIXES"] == want, data["DMS_ARTIFACT_BASE_ALLOWED_PREFIXES"]
    for name, (host, kind, mount) in _artifact_mounts(rendered).items():
        assert (host, kind, mount) == (want, "Directory", want), name
    assert "DirectoryOrCreate" not in rendered
    assert "set-by-overlay.invalid/dms/artifacts" not in rendered
    offenders = [ln.strip() for ln in rendered.splitlines() if "/cephfs" in ln]
    assert offenders == [], offenders[:5]


# --- base(정적) --------------------------------------------------------------------------------------------------------

def test_base_control_plane_mounts_only_the_placeholder_artifact_path():
    for fname, container in (("40-api.yaml", "api"), ("41-controller.yaml", "controller")):
        code = _code(BASE / fname)
        assert re.search(rf"(?m)^        - name: dms-artifacts\n          hostPath:\n            path: {re.escape(PLACEHOLDER_PATH)}\n"
                         r"            type: Directory$", code), fname
        assert re.search(rf"(?m)^            - name: dms-artifacts\n              mountPath: {re.escape(PLACEHOLDER_PATH)}\n"
                         r"              mountPropagation: HostToContainer$", code), fname
        assert len(re.findall(r"(?m)^\s+hostPath:$", code)) == 1, f"{fname}: 아티팩트 말고 다른 hostPath 가 생겼다"
    config = dict(re.findall(r'(?m)^  (DMS_[A-Z0-9_]+): "([^"]*)"', _code(BASE / "20-config.yaml")))
    assert config["DMS_ARTIFACT_BASE_URI"] == f"file://{PLACEHOLDER_PATH}"
    assert config["DMS_ARTIFACT_BASE_ALLOWED_PREFIXES"] == PLACEHOLDER_PATH


def test_no_cephfs_in_base_code_and_no_directory_or_create_anywhere_in_deploy():
    # 테스트베드의 실 경로(/cephfs)가 base 에 다시 커밋되면 git pull 한 사이트가 그 경로를 붙인다(이미지 실사고와 같은 모양).
    for path in sorted(BASE.glob("*.yaml")):
        assert "/cephfs" not in _code(path), path.name
    offenders = [str(p.relative_to(REPO_ROOT)) for p in sorted((REPO_ROOT / "deploy").rglob("*"))
                 if p.is_file() and p.suffix in {".yaml", ".yml", ".sh", ".py", ".env", ".example"}
                 and "DirectoryOrCreate" in _code(p)]
    assert offenders == [], offenders


# --- testbed-k8s(렌더 단계 없음 — values.env 는 원천 기록) ----------------------------------------------------------------

def test_testbed_values_env_carries_a_valid_shared_dir_matching_the_overlay_base():
    values = _values_env(TESTBED / "values.env")
    shared = values.get("SHARED_DIR")
    assert shared == "/dms_shared", values                   # 설계 v6 10 「SHARED_DIR=/dms_shared」
    assert _valid_shared_dir(shared)
    config = dict(re.findall(r'(?m)^  (DMS_[A-Z0-9_]+): "([^"]*)"', _code(TESTBED / "patch-config.yaml")))
    assert config["DMS_ARTIFACT_BASE_URI"] == f"file://{shared}/dms/artifacts", (
        "values.env 의 SHARED_DIR 와 patch-config.yaml 의 DMS_ARTIFACT_BASE_URI 가 어긋났다 -- 둘을 같이 고친다")
    assert "DMS_ARTIFACT_BASE_ALLOWED_PREFIXES" not in config   # replacements 가 base 에서 낸다(두 원천 금지)


@needs_kubectl
def test_testbed_render_puts_every_artifact_path_under_shared_dir_dms():
    _assert_all_paths_are(render("testbed-k8s"), _values_env(TESTBED / "values.env")["SHARED_DIR"])


# --- prod(render.sh) ---------------------------------------------------------------------------------------------------

def _prod_values(**overrides) -> str:
    text = (PROD / "values.env.example").read_text()
    text = re.sub(r"\bREPLACE_([A-Z_]+)", lambda m: "v-" + m.group(1).lower().replace("_", "-"), text)
    text = re.sub(r"(?m)^SHARED_DIR=\S+", "SHARED_DIR=/srv/dms_shared", text)
    text = re.sub(r"(?m)^REGISTRY_CLUSTER_IP=\S+", "REGISTRY_CLUSTER_IP=10.96.0.50", text)   # IPv4(2026-11-03 ④)
    for key, value in overrides.items():
        if value is None:
            text = re.sub(rf"(?m)^{key}=.*$", "", text)
        elif re.search(rf"(?m)^{key}=", text):
            text = re.sub(rf"(?m)^{key}=\S*", lambda _m: f"{key}={value}", text)
        else:
            text += f"\n{key}={value}\n"
    return text


def _render_prod(tmp_path, values_text, **env_extra):
    prod = tmp_path / "overlays" / "prod"
    shutil.copytree(PROD, prod, ignore=shutil.ignore_patterns("values.env"))
    shutil.copytree(BASE, tmp_path / "k8s")
    shutil.copytree(REPO_ROOT / "deploy" / "platform", tmp_path / "platform")   # 렌더러 본체가 플랫폼 층도 낸다(2026-11-03 ④)
    shutil.copy(RENDER, tmp_path / "render.sh")                                # prod/render.sh(호환 진입점)가 ../../render.sh 로
    vals = tmp_path / "values.env"
    vals.write_text(values_text)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    shim = bindir / "kubectl"
    shim.write_text("#!/bin/sh\nexit 97\n")                               # 명시 태그라 불리면 안 된다
    shim.chmod(0o755)
    env = {"PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}", "VALUES_ENV": str(vals),
           **env_extra}
    r = subprocess.run(["sh", str(prod / "render.sh")], env=env, capture_output=True, text=True, timeout=60)
    return r, tmp_path / ".rendered" / "prod" / "app"


def test_prod_template_has_no_shared_fs_and_one_shared_dir_source():
    code = _code(PROD / "patch-config.yaml")
    assert "SHARED_FS" not in code
    assert re.search(r'(?m)^KEYS="[^"]*\bSHARED_DIR\b', RENDER.read_text())
    assert "s|REPLACE_SHARED_DIR|$SHARED_DIR|g" in RENDER.read_text()
    config = dict(re.findall(r'(?m)^  (DMS_[A-Z0-9_]+): "([^"]*)"', code))
    assert config["DMS_ARTIFACT_BASE_URI"] == "file://REPLACE_SHARED_DIR/dms/artifacts"
    assert "DMS_ARTIFACT_BASE_ALLOWED_PREFIXES" not in config
    assert re.search(r"(?m)^SHARED_DIR=REPLACE_SHARED_DIR\b", (PROD / "values.env.example").read_text())
    assert not re.search(r"(?m)^SHARED_FS=", (PROD / "values.env.example").read_text())


@needs_kubectl
def test_prod_render_puts_every_artifact_path_under_shared_dir_dms(tmp_path):
    r, out = _render_prod(tmp_path, _prod_values())
    assert r.returncode == 0, (r.stdout, r.stderr)
    k = subprocess.run([KUBECTL, "kustomize", str(out)], capture_output=True, text=True, timeout=120,
                       env={**os.environ, "KUBECONFIG": os.devnull})
    assert k.returncode == 0, k.stderr
    _assert_all_paths_are(k.stdout, "/srv/dms_shared")


@pytest.mark.parametrize("old", ["cephfs", "/cephfs", ""])
def test_render_fails_with_a_migration_hint_when_shared_fs_remains(tmp_path, old):
    r, out = _render_prod(tmp_path, _prod_values(SHARED_FS=old))
    assert r.returncode == 2, (r.stdout, r.stderr)
    assert "SHARED_FS" in r.stdout and "SHARED_DIR=/" in r.stdout
    if old:
        assert "SHARED_DIR=/cephfs " in r.stdout                  # 같은 base 를 유지하는 새 값
    assert not out.exists()                                       # 렌더 산출물을 만들지 않는다


def test_render_fails_on_old_values_env_without_shared_dir(tmp_path):
    # 옛 values.env 그대로(SHARED_FS 만 있고 SHARED_DIR 없음) -- 「미치환」이 아니라 이행 안내가 먼저 나온다.
    r, _ = _render_prod(tmp_path, _prod_values(SHARED_DIR=None, SHARED_FS="cephfs"))
    assert r.returncode == 2 and "SHARED_DIR=/cephfs" in r.stdout, (r.stdout, r.stderr)


def test_shared_fs_in_the_callers_environment_does_not_fail_the_render(tmp_path):
    r, _ = _render_prod(tmp_path, _prod_values(), SHARED_FS="cephfs")
    assert r.returncode == 0, (r.stdout, r.stderr)


def test_shared_dir_in_the_callers_environment_never_fills_values_env(tmp_path):
    # 프로브가 env SHARED_DIR 를 받으니 운영자 셸에 export 돼 있을 수 있다(2026-11-03 리뷰) -- values.env 에 SHARED_DIR 줄이 없으면
    # 셸 값으로 렌더하지 않고 「미치환」 FAIL. 줄이 있으면 셸 값이 아니라 values.env 값이 이긴다.
    r, out = _render_prod(tmp_path, _prod_values(SHARED_DIR=None), SHARED_DIR="/elsewhere")
    assert r.returncode == 2 and "미치환" in r.stdout and "SHARED_DIR" in r.stdout, (r.stdout, r.stderr)
    assert not out.exists()
    r, out = _render_prod(tmp_path / "again", _prod_values(), SHARED_DIR="/elsewhere")
    assert r.returncode == 0, (r.stdout, r.stderr)
    config = (out / "patch-config.yaml").read_text()
    assert 'DMS_ARTIFACT_BASE_URI: "file:///srv/dms_shared/dms/artifacts"' in config and "/elsewhere" not in config


@pytest.mark.parametrize("bad", ["cephfs", "/", "/dms_shared/", "//dms_shared", "/a//b", "/a/./b", "/a/../b", "/a/..",
                                 "/a/.", "/a b", "/a|b", "/a&b", "/a$b", "/한글", "/a:b"])
def test_render_rejects_malformed_shared_dir(tmp_path, bad):
    r, out = _render_prod(tmp_path, _prod_values(SHARED_DIR=f"'{bad}'"))
    assert r.returncode == 2, (bad, r.stdout, r.stderr)
    assert "SHARED_DIR" in r.stdout
    assert not out.exists()


@pytest.mark.parametrize("value", ["", "REPLACE_SHARED_DIR"])
def test_render_reports_unfilled_shared_dir(tmp_path, value):
    r, _ = _render_prod(tmp_path, _prod_values(SHARED_DIR=value))
    assert r.returncode == 2 and "SHARED_DIR" in r.stdout and "미치환" in r.stdout, (r.stdout, r.stderr)


@pytest.mark.parametrize("good", ["/dms_shared", "/srv/dms-shared.v2/x_y", "/mgmt_storage/dms"])
def test_render_accepts_well_formed_shared_dir(tmp_path, good):
    assert _valid_shared_dir(good)
    r, out = _render_prod(tmp_path, _prod_values(SHARED_DIR=good))
    assert r.returncode == 0, (good, r.stdout, r.stderr)
    assert f'DMS_ARTIFACT_BASE_URI: "file://{good}/dms/artifacts"' in (out / "patch-config.yaml").read_text()
