"""deploy/overlays/guard-images.sh 의 컨텍스트 대조(2026-10-21 — 테스트베드가 둘이 되며).

두 클러스터(ssc = 컨텍스트 dms, k8s = 컨텍스트 dmsk8s)가 같은 네임스페이스 이름(dms)을 쓰므로 "엉뚱한 클러스터에
apply" 가 새 사고 유형이다. 오버레이 디렉터리의 `kube-context` 파일이 그 오버레이 전용 컨텍스트를 고정하고, 가드는
겨눈 컨텍스트(--context > KUBE_CONTEXT > 현재 컨텍스트)가 다르면 exit 4 로 거부한다. 파일이 없는 오버레이(prod·ssc
사이트)는 종전 동작 그대로다(install.sh 는 컨텍스트 없이 부른다).

실 kubectl 은 이 호스트에서 실 테스트베드를 가리킨다 -- PATH 앞에 가짜 kubectl 을 두고 실 kubectl 이 불리지 않는지
(가짜가 모든 호출을 기록한다) 함께 본다. 이 호스트 /bin/sh = dash 로 실제 스크립트를 돌린다.
"""
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GUARD = REPO_ROOT / "deploy" / "overlays" / "guard-images.sh"

RENDERED = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: dms-api
  namespace: dms
spec:
  template:
    spec:
      containers:
      - image: reg/dms:k1
        name: api
---
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: dms-agent
  namespace: dms
spec:
  template:
    spec:
      containers:
      - args:
        - agent
        command:
        - dms
        image: reg/dms-agent:k1
        name: agent
"""

# 가짜 kubectl: 모든 호출을 FAKE_LOG 에 한 줄씩 남긴다. get 은 워크로드 이름별 FAKE_LIVE_<이름(-→_)> 를 라이브
# 이미지로 돌려주고, 없으면 NotFound(= 첫 설치).
FAKE_KUBECTL = r"""#!/bin/sh
printf '%s\n' "$*" >> "$FAKE_LOG"
if [ "$1" = "kustomize" ]; then cat "$FAKE_RENDER"; exit 0; fi
if [ "$1" = "config" ]; then
  case "$2" in
    current-context)
      if [ -n "${FAKE_CURRENT:-}" ]; then echo "$FAKE_CURRENT"; exit 0; fi
      echo "error: current-context is not set" >&2; exit 1 ;;
    get-contexts)
      for c in ${FAKE_CONTEXTS:-}; do [ "$c" = "$3" ] && { echo "$c"; exit 0; }; done
      echo "error: context $3 not found" >&2; exit 1 ;;
  esac
  exit 1
fi
name=$(printf '%s\n' "$*" | sed -n 's/.* get [a-z]* \([a-z-]*\) .*/\1/p' | tr '-' '_')
eval "live=\${FAKE_LIVE_$name:-}"
if [ -n "$live" ]; then printf '%s' "$live"; exit 0; fi
echo 'Error from server (NotFound)' >&2; exit 1
"""

LIVE_SAME = {"FAKE_LIVE_dms_api": "reg/dms:k1", "FAKE_LIVE_dms_agent": "reg/dms-agent:k1"}


def _overlay(tmp_path, context_file=None, name="ovl"):
    d = tmp_path / name
    d.mkdir()
    (d / "kustomization.yaml").write_text("resources: []\n")   # 가짜 kubectl 은 읽지 않는다
    if context_file is not None:
        (d / "kube-context").write_text(context_file)
    return d


def _run(tmp_path, overlay, *args, current="dms", contexts="dms dmsk8s", live=None, extra_env=None):
    bindir = tmp_path / "bin"
    if not bindir.exists():
        bindir.mkdir()
        shim = bindir / "kubectl"
        shim.write_text(FAKE_KUBECTL)
        shim.chmod(0o755)
        (tmp_path / "rendered.yaml").write_text(RENDERED)
    log = tmp_path / "kubectl.log"
    log.write_text("")
    env = {"PATH": f"{bindir}{os.pathsep}/usr/bin:/bin",
           "FAKE_LOG": str(log), "FAKE_RENDER": str(tmp_path / "rendered.yaml"),
           "FAKE_CURRENT": current or "", "FAKE_CONTEXTS": contexts}
    env.update(live or {})
    env.update(extra_env or {})
    r = subprocess.run(["sh", str(GUARD), str(overlay), *args], env=env, capture_output=True, text=True,
                       timeout=30)
    calls = [ln for ln in log.read_text().splitlines() if ln]
    return r, calls


def _gets(calls):
    return [c for c in calls if " get " in f" {c} "]


# --- 고정된 오버레이(kube-context 있음) ----------------------------------------------

def test_k8s_overlay_against_the_ssc_context_is_refused_before_reading_live(tmp_path):
    ovl = _overlay(tmp_path, "# k8s\ndmsk8s\n")
    r, calls = _run(tmp_path, ovl, "--context", "dms", live=LIVE_SAME)
    assert r.returncode == 4, (r.stdout, r.stderr)
    assert "dmsk8s" in r.stderr and "dms" in r.stderr
    assert _gets(calls) == [] and not any(c.startswith("kustomize") for c in calls), calls


def test_ssc_overlay_against_the_k8s_context_is_refused(tmp_path):
    ovl = _overlay(tmp_path, "dms\n")
    r, _ = _run(tmp_path, ovl, "--context", "dmsk8s", live=LIVE_SAME)
    assert r.returncode == 4, (r.stdout, r.stderr)


def test_matching_explicit_context_passes_and_pins_every_kubectl_read(tmp_path):
    ovl = _overlay(tmp_path, "dmsk8s\n")
    r, calls = _run(tmp_path, ovl, "--context", "dmsk8s", current="dms", live=LIVE_SAME)
    assert r.returncode == 0, (r.stdout, r.stderr)
    gets = _gets(calls)
    assert len(gets) == 2 and all(g.startswith("--context dmsk8s -n dms get ") for g in gets), gets
    assert f"kubectl --context dmsk8s apply -k {ovl}" in r.stdout


def test_context_equals_form_and_flag_order_are_accepted(tmp_path):
    ovl = _overlay(tmp_path, "dmsk8s\n")
    r, _ = _run(tmp_path, ovl, "--context=dmsk8s", "--allow-image-change", live=LIVE_SAME)
    assert r.returncode == 0, (r.stdout, r.stderr)


def test_without_explicit_context_the_current_context_must_match(tmp_path):
    # 종전 README 흐름(`sh guard-images.sh deploy/overlays/testbed`, 현재 컨텍스트 dms)은 그대로 통과하고,
    # 현재 컨텍스트가 다른 클러스터면 거부한다.
    ssc = _overlay(tmp_path, "dms\n", name="ssc")
    r, calls = _run(tmp_path, ssc, current="dms", live=LIVE_SAME)
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert all(g.startswith("--context dms ") for g in _gets(calls)), calls
    k8s = _overlay(tmp_path, "dmsk8s\n", name="k8s")
    r, _ = _run(tmp_path, k8s, current="dms", live=LIVE_SAME)
    assert r.returncode == 4, (r.stdout, r.stderr)
    assert "현재 컨텍스트" in r.stderr


def test_no_current_context_with_a_pinned_overlay_is_refused(tmp_path):
    ovl = _overlay(tmp_path, "dmsk8s\n")
    r, _ = _run(tmp_path, ovl, current=None, live=LIVE_SAME)
    assert r.returncode == 4, (r.stdout, r.stderr)


def test_kube_context_env_is_used_and_the_argument_wins(tmp_path):
    ovl = _overlay(tmp_path, "dmsk8s\n")
    r, _ = _run(tmp_path, ovl, extra_env={"KUBE_CONTEXT": "dms"}, live=LIVE_SAME)
    assert r.returncode == 4 and "KUBE_CONTEXT" in r.stderr, (r.stdout, r.stderr)
    r, _ = _run(tmp_path, ovl, extra_env={"KUBE_CONTEXT": "dmsk8s"}, live=LIVE_SAME)
    assert r.returncode == 0, (r.stdout, r.stderr)
    r, _ = _run(tmp_path, ovl, "--context", "dmsk8s", extra_env={"KUBE_CONTEXT": "dms"}, live=LIVE_SAME)
    assert r.returncode == 0, (r.stdout, r.stderr)


def test_empty_kube_context_file_is_a_configuration_error_not_unpinned(tmp_path):
    ovl = _overlay(tmp_path, "# 주석만\n\n")
    r, _ = _run(tmp_path, ovl, "--context", "dms", live=LIVE_SAME)
    assert r.returncode == 2, (r.stdout, r.stderr)


def test_image_change_is_still_refused_on_the_right_context(tmp_path):
    ovl = _overlay(tmp_path, "dmsk8s\n")
    live = {"FAKE_LIVE_dms_api": "reg/dms:k0", "FAKE_LIVE_dms_agent": "reg/dms-agent:k1"}
    r, _ = _run(tmp_path, ovl, "--context", "dmsk8s", live=live)
    assert r.returncode == 3, (r.stdout, r.stderr)
    assert "Deployment/dms-api: live=reg/dms:k0 -> overlay=reg/dms:k1" in r.stderr
    r, _ = _run(tmp_path, ovl, "--context", "dmsk8s", live=live, extra_env={"ALLOW_IMAGE_CHANGE": "1"})
    assert r.returncode == 0, (r.stdout, r.stderr)


# --- 고정 없는 오버레이(prod·ssc 사이트) — 종전 동작 ---------------------------------

def test_unpinned_overlay_without_context_keeps_the_old_behaviour(tmp_path):
    ovl = _overlay(tmp_path)
    live = {"FAKE_LIVE_dms_api": "reg/dms:k0", "FAKE_LIVE_dms_agent": "reg/dms-agent:k1"}
    r, calls = _run(tmp_path, ovl, current="dmsk8s", live=live)
    assert r.returncode == 3, (r.stdout, r.stderr)
    assert not any(c.startswith("config") for c in calls), calls          # 현재 컨텍스트를 묻지도 않는다
    assert _gets(calls) and all(g.startswith("-n dms get ") for g in _gets(calls)), calls
    r, _ = _run(tmp_path, ovl, "--allow-image-change", live=live)
    assert r.returncode == 0, (r.stdout, r.stderr)
    r, _ = _run(tmp_path, ovl)                                             # 첫 설치(라이브 없음)
    assert r.returncode == 0, (r.stdout, r.stderr)


def test_unpinned_overlay_with_context_reads_live_through_that_context(tmp_path):
    ovl = _overlay(tmp_path)
    r, calls = _run(tmp_path, ovl, "--context", "dmsk8s", live=LIVE_SAME)
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert all(g.startswith("--context dmsk8s ") for g in _gets(calls)), calls


def test_unpinned_overlay_refuses_an_env_context_that_differs_from_the_current_one(tmp_path):
    # install.sh(prod·ssc)는 가드를 컨텍스트 없이 부르고 현재 컨텍스트로 apply 한다 -- 셸에 남은 KUBE_CONTEXT 가 다른
    # 클러스터를 가리키면 가드가 엉뚱한 라이브를 보고 통과시킨다. 모호하면 거부, 같으면 통과, 명시 --context 는 존중.
    ovl = _overlay(tmp_path)
    r, calls = _run(tmp_path, ovl, current="dms", extra_env={"KUBE_CONTEXT": "dmsk8s"}, live=LIVE_SAME)
    assert r.returncode == 4 and "KUBE_CONTEXT" in r.stderr, (r.stdout, r.stderr)
    assert _gets(calls) == [], calls
    r, _ = _run(tmp_path, ovl, current="dms", extra_env={"KUBE_CONTEXT": "dms"}, live=LIVE_SAME)
    assert r.returncode == 0, (r.stdout, r.stderr)
    r, _ = _run(tmp_path, ovl, "--context", "dmsk8s", current="dms", extra_env={"KUBE_CONTEXT": "dmsk8s"},
                live=LIVE_SAME)
    assert r.returncode == 0, (r.stdout, r.stderr)


def test_unknown_context_fails_instead_of_looking_like_a_first_install(tmp_path):
    # 오타 컨텍스트면 라이브 조회가 전부 실패해 "첫 설치" 로 통과할 뻔한다 -- kubeconfig 에서 먼저 거른다.
    ovl = _overlay(tmp_path)
    r, calls = _run(tmp_path, ovl, "--context", "dmsk8z", live=LIVE_SAME)
    assert r.returncode == 2, (r.stdout, r.stderr)
    assert _gets(calls) == [], calls


@pytest.mark.parametrize("args", [[], ["--context"], ["--context", ""], ["--bogus"], ["--context="]])
def test_usage_errors_exit_2(tmp_path, args):
    ovl = _overlay(tmp_path)
    if args == []:
        r = subprocess.run(["sh", str(GUARD)], capture_output=True, text=True, timeout=30,
                           env={"PATH": "/usr/bin:/bin"})
    else:
        r, _ = _run(tmp_path, ovl, *args)
    assert r.returncode == 2, (args, r.stdout, r.stderr)


# --- 저장소의 실제 고정값 -------------------------------------------------------------

def _pinned(overlay_dir: Path):
    path = overlay_dir / "kube-context"
    if not path.is_file():
        return None
    r = subprocess.run(["sh", "-c", "sed -e 's/#.*$//' -e 's/[[:space:]]//g' \"$1\" | grep -v '^$' | head -n 1",
                        "sh", str(path)], capture_output=True, text=True, timeout=10)
    return r.stdout.strip()


# 오버레이 → 고정 컨텍스트(None = kube-context 없음). 브랜치마다 싣는 오버레이가 다르다(2026-10-22 브랜치 분리 —
# main·dms-ssc = testbed·prod(+ssc), dms-k8s = testbed-k8s·prod) — 이 표는 공용이고 검사는 **있는** 오버레이만 본다.
_PINS = {
    "testbed": "dms",          # ssc 테스트베드(dms-cp1) — main·dms-ssc
    "testbed-k8s": "dmsk8s",   # k8s 테스트베드(k8s-cp1), LDAP 없음 — dms-k8s
    "prod": None,              # 사이트 설치기(install.sh)는 컨텍스트 없이 부른다
    "ssc": None,               # SSC 사이트 설치기(dms-ssc) — 같은 이유
}


def test_repo_testbed_overlays_are_pinned_to_their_clusters():
    overlays = REPO_ROOT / "deploy" / "overlays"
    present = sorted(p.parent.name for p in overlays.glob("*/kustomization.yaml"))
    unknown = [o for o in present if o not in _PINS]
    assert unknown == [], f"새 오버레이 {unknown} — _PINS 에 고정 컨텍스트(또는 None)를 적어라"
    assert any(_PINS[o] for o in present), f"테스트베드 오버레이(kube-context 고정)가 하나도 없다: {present}"
    for o in present:
        assert _pinned(overlays / o) == _PINS[o], o
