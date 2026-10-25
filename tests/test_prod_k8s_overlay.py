"""dms-k8s 프로덕션 오버레이(deploy/overlays/prod)의 무 LDAP·k8s 신원 모드 계약(2026-10-25, BACKLOG 「overlays/prod 의 LDAP
필수 vs 무 LDAP dms-k8s」 해소).

사용자 결정(2026-10-21 「dms-k8s 브런치에서는 LDAP 을 사용 안할거야」)과 RBAC·PV 설계 D1(k8s 신원 모드 = 모든 잡 root + 권한 =
k8s RoleBinding 맵핑)을 사이트 설치기에도 적용한다 — testbed-k8s 오버레이와 같은 모양이다(LDAP 키 null·에이전트 `dms agent`·
LDAP env 삭제·시크릿 4키). 한 줄이라도 놓치면 결과는 기동 실패(k8s 모드는 DMS_LDAP_URI 가 남아 있으면 기동 거부 — 시끄러움)
이거나 에이전트 CreateContainerConfigError(지운 키 참조)다. 세 층으로 고정한다:

- 정적: 오버레이 템플릿(patch-config·patch-agent·kustomization)·values.env.example·render.sh·install.sh 의 모양.
- render.sh(실제 sh): values.env.example 을 채워 렌더 — LDAP 키 없이 통과하고, 옛 values.env 의 LDAP_* 는 무시(알림 한 줄).
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
BASE_CONFIG = REPO_ROOT / "deploy" / "k8s" / "20-config.yaml"
BASE_AGENT = REPO_ROOT / "deploy" / "k8s" / "50-agent-daemonset.yaml"
INSTALL = REPO_ROOT / "deploy" / "install.sh"


def _code(path: Path) -> str:
    return "\n".join(ln for ln in path.read_text(encoding="utf-8").splitlines()
                     if not ln.lstrip().startswith("#"))


def _base_ldap_config_keys() -> set:
    keys = set(re.findall(r"^  (DMS_LDAP_[A-Z0-9_]+):", _code(BASE_CONFIG), re.M))
    assert keys, "base 20-config.yaml 에서 LDAP 키를 못 찾았다"
    return keys


def _base_ldap_agent_env() -> set:
    names = set(re.findall(r"- name: (DMS_LDAP_[A-Z0-9_]+)", _code(BASE_AGENT)))
    assert names, "base 50-agent-daemonset.yaml 에서 LDAP env 를 못 찾았다"
    return names


# --- 정적 -------------------------------------------------------------------------------------------

def test_patch_config_deletes_every_base_ldap_key_and_turns_on_the_k8s_mode():
    text = _code(PROD / "patch-config.yaml")
    nulled = set(re.findall(r"^  (DMS_LDAP_[A-Z0-9_]+): null\s*$", text, re.M))
    assert _base_ldap_config_keys() - nulled == set(), "base 의 LDAP 키를 patch-config.yaml 에서 `<키>: null` 로 지워라"
    valued = [ln.strip() for ln in text.splitlines()
              if re.match(r"^  DMS_LDAP_[A-Z0-9_]+:", ln) and not re.search(r":\s*null\s*$", ln)]
    assert valued == [], f"LDAP 키에 값을 두지 않는다(삭제만): {valued}"
    values = dict(re.findall(r'^  (DMS_[A-Z0-9_]+): "([^"]*)"', text, re.M))
    assert values["DMS_IDENTITY_MODE"] == "k8s"
    assert values["DMS_USER_ALLOWED_OPERATIONS"] == "sync,scan,rm"
    assert values["DMS_ALLOW_PRIVILEGED_REQUESTERS"] == "true"
    assert values["DMS_ACCOUNT_VERIFICATION_REQUIRED"] == "true"   # 맵핑 ID = 계정 아이디의 결속 장치


def test_patch_agent_bypasses_nslcd_and_deletes_every_base_ldap_env():
    text = _code(PROD / "patch-agent.yaml")
    assert re.search(r'^\s*- name: agent\s*$', text, re.M)
    assert re.search(r'^\s*command: \["dms"\]\s*$', text, re.M)
    assert re.search(r'^\s*args: \["agent"\]\s*$', text, re.M)
    deleted = set(re.findall(r"- name: (DMS_LDAP_[A-Z0-9_]+)\s*\n\s*\$patch: delete", text))
    assert _base_ldap_agent_env() - deleted == set()
    # testbed-k8s 와 같은 패치다(두 벌이 갈라지면 한쪽만 base 변화를 따라간다)
    assert _code(PROD / "patch-agent.yaml").split("apiVersion:", 1)[1] == \
        _code(REPO_ROOT / "deploy" / "overlays" / "testbed-k8s" / "patch-agent.yaml").split("apiVersion:", 1)[1]


def test_kustomization_wires_the_agent_patch():
    text = _code(PROD / "kustomization.yaml")
    for patch in ("patch-config.yaml", "patch-agent.yaml", "patch-ingress.yaml", "patch-metallb.yaml"):
        assert f"- path: {patch}" in text, patch


def test_values_render_and_install_need_no_ldap():
    example = _code(PROD / "values.env.example")
    assert not re.findall(r"(?m)^LDAP_\w+=", example)
    render_sh = (PROD / "render.sh").read_text()
    keys = re.search(r'^KEYS="([^"]*)"', render_sh, re.M)
    assert keys and "LDAP" not in keys.group(1), keys
    assert "REPLACE_LDAP" not in render_sh
    assert "patch-agent.yaml" in render_sh                       # 렌더 산출물에 복사돼야 kustomization 이 찾는다
    secret_keys = re.search(r"^for k in (DMS_[A-Z_ ]+); do", _code(INSTALL), re.M)
    assert secret_keys and secret_keys.group(1).split() == [
        "DMS_DATABASE_URL", "DMS_SHARED_TOKEN", "DMS_ADMIN_TOKEN", "DMS_SESSION_SECRET"]


# --- render.sh -------------------------------------------------------------------------------------

def _filled_example(extra: str = "") -> str:
    text = (PROD / "values.env.example").read_text()
    return re.sub(r"\bREPLACE_([A-Z_]+)", lambda m: "v-" + m.group(1).lower().replace("_", "-"), text) + extra


def _render(tmp_path, values_text):
    prod = tmp_path / "overlays" / "prod"
    shutil.copytree(PROD, prod, ignore=shutil.ignore_patterns("values.env"))
    shutil.copytree(REPO_ROOT / "deploy" / "k8s", tmp_path / "k8s")      # ../../k8s 가 렌더 산출물에서 풀리게
    vals = tmp_path / "values.env"
    vals.write_text(values_text)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    shim = bindir / "kubectl"
    shim.write_text("#!/bin/sh\nexit 97\n")                               # 명시 태그라 불리면 안 된다
    shim.chmod(0o755)
    env = {"PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}", "VALUES_ENV": str(vals)}
    r = subprocess.run(["sh", str(prod / "render.sh")], env=env, capture_output=True, text=True, timeout=60)
    return r, tmp_path / "overlays" / ".prod-rendered"


def test_render_succeeds_without_ldap_values(tmp_path):
    r, out = _render(tmp_path, _filled_example())
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert r.stdout.strip().splitlines()[-1].startswith("RENDERED: ")
    assert "note:" not in r.stderr
    assert (out / "patch-agent.yaml").is_file()
    leftover = [ln for f in out.iterdir() for ln in f.read_text().splitlines()
                if "REPLACE_" in ln and not ln.lstrip().startswith("#")]
    assert leftover == []


def test_old_values_env_with_ldap_keys_still_renders_and_says_they_are_ignored(tmp_path):
    old = _filled_example("\nLDAP_HOST=ldap.corp.example\nLDAP_USER_BASE=ou=People\n")
    r, out = _render(tmp_path, old)
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "LDAP_* 는 dms-k8s" in r.stderr
    assert "ldap.corp.example" not in (out / "patch-config.yaml").read_text()


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
    assert (data["DMS_IDENTITY_MODE"], data["DMS_USER_ALLOWED_OPERATIONS"]) == ("k8s", "sync,scan,rm")
    assert data["DMS_PRIVILEGED_REQUESTERS"] == "root,admin,v-local-admin"
    settings = Settings.from_env({**data, "DMS_DATABASE_URL": "postgresql://u:p@h/d", "DMS_SHARED_TOKEN": "s",
                                  "DMS_ADMIN_TOKEN": "a", "DMS_SESSION_SECRET": "x"})
    assert settings.identity_mode == "k8s" and settings.ldap_uri == ""
