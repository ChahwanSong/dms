"""점검 스크립트의 --context(2026-10-26 사용자 지시 「점검 스크립트에 context 옵션 추가해」): deploy/preflight-cluster.sh·
deploy/verify.sh·deploy/overlays/prod/render.sh(live 태그 해석)가 guard-images.sh 와 같은 문법(--context <ctx> / --context=<ctx>
/ KUBE_CONTEXT, 플래그가 이긴다)으로 대상 클러스터를 고른다 — 두 테스트베드가 같은 네임스페이스라 current-context 에 기대지 않게.

실제 sh(dash)로 돌리고, PATH 앞의 kubectl 심이 받은 인자를 줄마다 기록한다(이 호스트의 진짜 kubectl 은 실 테스트베드를 가리키므로
절대 부르지 않는다). 심은 `config get-contexts known` 만 있는 컨텍스트로 답한다.
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DEPLOY = REPO_ROOT / "deploy"

_SHIM = """#!/bin/sh
printf '%s\\n' "$*" >> '{log}'
if [ "$1" = "config" ] && [ "$2" = "get-contexts" ]; then [ "$3" = "known" ] && exit 0; exit 1; fi
if [ "$1" = "config" ] && [ "$2" = "current-context" ]; then echo current-ctx; exit 0; fi
case "$*" in *DMS_JOB_IMAGE*) printf 'reg/dms-mpifileutils:d7'; exit 0;; esac
case "$*" in *"get deploy dms-api"*|*"get ds dms-agent"*) printf 'reg/dms:d7'; exit 0;; esac
exit 0
"""


def _env(tmp_path, **extra):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    log = tmp_path / "kubectl.log"
    shim = bindir / "kubectl"
    shim.write_text(_SHIM.format(log=log))
    shim.chmod(0o755)
    env = {"PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}", "HOME": str(tmp_path), **extra}
    return env, log


def _calls(log):
    return log.read_text().splitlines() if log.exists() else []


def _cluster_calls(log):
    """config get-contexts(존재 확인) 밖의 호출 -- 클러스터에 붙는 호출이다."""
    return [c for c in _calls(log) if not c.startswith("config get-contexts")]


@pytest.mark.parametrize("script", ["preflight-cluster.sh", "verify.sh"])
@pytest.mark.parametrize("how", [["--context", "known"], ["--context=known"], "env"])
def test_every_kubectl_call_goes_to_the_named_context(tmp_path, script, how):
    env, log = _env(tmp_path, **({"KUBE_CONTEXT": "known"} if how == "env" else {}))
    args = [] if how == "env" else how
    subprocess.run(["sh", str(DEPLOY / script), *args], env=env, capture_output=True, text=True, timeout=120)
    calls = _cluster_calls(log)
    assert calls, "kubectl 을 한 번도 부르지 않았다"
    assert all(c.startswith("--context known ") for c in calls), [c for c in calls if not c.startswith("--context known")]


@pytest.mark.parametrize("script", ["preflight-cluster.sh", "verify.sh"])
def test_unknown_context_stops_before_touching_a_cluster(tmp_path, script):
    env, log = _env(tmp_path)
    r = subprocess.run(["sh", str(DEPLOY / script), "--context", "nope"], env=env, capture_output=True, text=True,
                       timeout=60)
    assert r.returncode == 2 and "nope" in r.stderr
    assert _cluster_calls(log) == []


@pytest.mark.parametrize("script", ["preflight-cluster.sh", "verify.sh"])
def test_flag_beats_env_and_bad_usage_is_rejected(tmp_path, script):
    env, log = _env(tmp_path, KUBE_CONTEXT="nope")
    subprocess.run(["sh", str(DEPLOY / script), "--context", "known"], env=env, capture_output=True, text=True,
                   timeout=120)
    assert all(c.startswith("--context known ") for c in _cluster_calls(log))
    for bad in (["--context"], ["--context="], ["--bogus"]):
        r = subprocess.run(["sh", str(DEPLOY / script), *bad], env=env,
                           capture_output=True, text=True, timeout=60)
        assert r.returncode == 2 and "usage" in r.stderr, bad


def test_without_context_scripts_use_the_current_context_as_before(tmp_path):
    env, log = _env(tmp_path)
    subprocess.run(["sh", str(DEPLOY / "verify.sh")], env=env, capture_output=True, text=True, timeout=120)
    calls = _cluster_calls(log)
    assert calls and not any(c.startswith("--context") for c in calls)


def test_preflight_shows_the_named_context_not_the_kubeconfig_current_one(tmp_path):
    env, _ = _env(tmp_path)
    r = subprocess.run(["sh", str(DEPLOY / "preflight-cluster.sh"), "--context", "known"], env=env,
                       capture_output=True, text=True, timeout=120)
    assert "context: known (--context)" in r.stdout and "current-ctx" not in r.stdout


# --- render.sh(live 태그) ------------------------------------------------------------------------------------------

def _values(tmp_path, mfu_tag):
    text = (DEPLOY / "overlays/prod/values.env.example").read_text()
    text = re.sub(r"\bREPLACE_([A-Z_]+)", lambda m: "v-" + m.group(1).lower().replace("_", "-"), text)
    text = re.sub(r"^MFU_TAG=.*$", f"MFU_TAG={mfu_tag}", text, flags=re.M)
    vals = tmp_path / "values.env"
    vals.write_text(text)
    return vals


def _render(tmp_path, mfu_tag, *args, **extra_env):
    prod = tmp_path / "overlays" / "prod"
    shutil.copytree(DEPLOY / "overlays/prod", prod, ignore=shutil.ignore_patterns("values.env"))
    env, log = _env(tmp_path, VALUES_ENV=str(_values(tmp_path, mfu_tag)), **extra_env)
    r = subprocess.run(["sh", str(prod / "render.sh"), *args], env=env, capture_output=True, text=True, timeout=60)
    return r, log


def test_render_live_tag_reads_from_the_named_context(tmp_path):
    r, log = _render(tmp_path, "live", "--context", "known")
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "live: MFU_TAG=d7" in r.stderr
    assert [c for c in _cluster_calls(log)] == [
        "--context known -n dms get configmap dms-config -o jsonpath={.data.DMS_JOB_IMAGE}"]


def test_render_with_explicit_tags_never_calls_kubectl_even_with_a_context(tmp_path):
    r, log = _render(tmp_path, "d42", "--context", "nope")
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert _calls(log) == []


def test_render_live_tag_with_an_unknown_context_fails(tmp_path):
    r, log = _render(tmp_path, "live", KUBE_CONTEXT="nope")
    assert r.returncode == 2 and "컨텍스트 'nope'" in r.stderr
    assert _cluster_calls(log) == []
