"""deploy/overlays/guard-images.sh 의 컨텍스트 대조(2026-10-21 — 테스트베드가 둘이 되며).

두 클러스터(ssc = 컨텍스트 dms, k8s = 컨텍스트 dmsk8s)가 같은 네임스페이스 이름(dms)을 쓰므로 "엉뚱한 클러스터에
apply" 가 새 사고 유형이다. 오버레이 디렉터리의 `kube-context` 파일이 그 오버레이 전용 컨텍스트를 고정하고, 가드는
겨눈 컨텍스트(--context > KUBE_CONTEXT > 현재 컨텍스트)가 다르면 exit 4 로 거부한다. 파일이 없는 오버레이(prod·ssc
사이트)는 종전 동작 그대로다(install.sh 는 컨텍스트 없이 부른다).

실 kubectl 은 이 호스트에서 실 테스트베드를 가리킨다 -- PATH 앞에 가짜 kubectl 을 두고 실 kubectl 이 불리지 않는지
(가짜가 모든 호출을 기록한다) 함께 본다. 이 호스트 /bin/sh = dash 로 실제 스크립트를 돌린다.
"""
import os
import re
import subprocess
from pathlib import Path

import pytest

from overlay_render import needs_kubectl, render

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
if [ "$1" = "--context" ] && [ "$3" = "kustomize" ]; then cat "$FAKE_RENDER"; exit 0; fi
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
case "$*" in
  *hostPath*)
    # hostPath 조회(2026-11-03): 워크로드가 있으면(FAKE_LIVE_<이름>) FAKE_HP_<이름>(\n 구분, 없으면 hostPath 0개)을 돌려준다.
    if [ -n "$live" ]; then eval "hp=\${FAKE_HP_$name:-}"; printf '%b' "$hp"; exit 0; fi
    echo 'Error from server (NotFound)' >&2; exit 1 ;;
esac
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


def _run(tmp_path, overlay, *args, current="dms", contexts="dms dmsk8s", live=None, extra_env=None, rendered=RENDERED):
    bindir = tmp_path / "bin"
    if not bindir.exists():
        bindir.mkdir()
        shim = bindir / "kubectl"
        shim.write_text(FAKE_KUBECTL)
        shim.chmod(0o755)
    (tmp_path / "rendered.yaml").write_text(rendered)
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
    assert _gets(calls) == [] and not any(" kustomize " in f" {c} " for c in calls), calls


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
    # 로컬 렌더도 같은 --context(2026-11-03 T9 — 「모든 kubectl 에 --context」; 전에는 `kubectl kustomize` 만 컨텍스트가 없었다)
    assert [c for c in calls if " kustomize " in f" {c} "] == [f"--context dmsk8s kustomize {ovl}"], calls
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


# --- hostPath 변경(2026-11-03 ③ 리뷰 — 공용 디렉터리 전환) -------------------------------

# kustomize 출력 모양 그대로(키 정렬): api 는 아티팩트 hostPath 하나, controller 도 하나, 에이전트(DaemonSet)의 hostPath 는
# 비교 대상이 아니다.
RENDERED_HP = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  labels:
    app.kubernetes.io/name: dms-api
  name: dms-api
  namespace: dms
spec:
  template:
    spec:
      containers:
      - image: reg/dms:k1
        name: api
        volumeMounts:
        - mountPath: /dms_shared/dms/artifacts
          name: dms-artifacts
      volumes:
      - hostPath:
          path: /dms_shared/dms/artifacts
          type: Directory
        name: dms-artifacts
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: dms-controller
  namespace: dms
spec:
  template:
    spec:
      containers:
      - image: reg/dms:k1
        name: controller
      volumes:
      - emptyDir: {}
        name: tmp
      - hostPath:
          path: /dms_shared/dms/artifacts
          type: Directory
        name: dms-artifacts
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
      - image: reg/dms-agent:k1
        name: agent
      volumes:
      - hostPath:
          path: /
          type: Directory
        name: host-root
"""
LIVE_ALL = {**LIVE_SAME, "FAKE_LIVE_dms_controller": "reg/dms:k1"}
HP_OLD = {"FAKE_HP_dms_api": "/cephfs\\n", "FAKE_HP_dms_controller": "\\n/cephfs\\n"}       # 옛 배치(d16): /cephfs
HP_NEW = {"FAKE_HP_dms_api": "/dms_shared/dms/artifacts", "FAKE_HP_dms_controller": "\\n/dms_shared/dms/artifacts\\n"}


def test_hostpath_change_is_refused_with_exit_5_even_when_images_match(tmp_path):
    # ③ 을 담은 오버레이를 공용 디렉터리 전환 전에 태그 bump 용으로 apply 하는 사고: 이미지 가드는 통과하지만 api·controller 가
    # 없는 hostPath 로 롤아웃돼 멈춘다 -- 같은 가드가 막는다.
    ovl = _overlay(tmp_path, "dmsk8s\n")
    r, calls = _run(tmp_path, ovl, "--context", "dmsk8s", live={**LIVE_ALL, **HP_OLD}, rendered=RENDERED_HP)
    assert r.returncode == 5, (r.stdout, r.stderr)
    assert "Deployment/dms-api hostPath: live=[ /cephfs ] -> overlay=[ /dms_shared/dms/artifacts ]" in r.stderr
    assert "Deployment/dms-controller hostPath" in r.stderr and "dms-agent" not in r.stderr
    assert "ALLOW_HOSTPATH_CHANGE=1" in r.stderr
    hp_gets = [c for c in _gets(calls) if "hostPath" in c]
    assert len(hp_gets) == 2 and all(c.startswith("--context dmsk8s -n dms get deploy ") for c in hp_gets), calls


@pytest.mark.parametrize("how", ["env", "flag"])
def test_hostpath_change_passes_only_with_the_explicit_permission(tmp_path, how):
    ovl = _overlay(tmp_path, "dmsk8s\n")
    args, env = (["--allow-hostpath-change"], None) if how == "flag" else ([], {"ALLOW_HOSTPATH_CHANGE": "1"})
    r, _ = _run(tmp_path, ovl, "--context", "dmsk8s", *args, live={**LIVE_ALL, **HP_OLD}, extra_env=env,
                rendered=RENDERED_HP)
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "hostPath 변경 허용" in r.stdout and "kubectl --context dmsk8s apply -k" in r.stdout


def test_same_hostpaths_or_first_install_pass(tmp_path):
    ovl = _overlay(tmp_path, "dmsk8s\n")
    r, _ = _run(tmp_path, ovl, "--context", "dmsk8s", live={**LIVE_ALL, **HP_NEW}, rendered=RENDERED_HP)
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "변경 없음" in r.stdout
    r, _ = _run(tmp_path, ovl, "--context", "dmsk8s", rendered=RENDERED_HP)          # 라이브 없음 = 첫 설치
    assert r.returncode == 0, (r.stdout, r.stderr)


def test_live_deployment_without_hostpath_is_a_change(tmp_path):
    ovl = _overlay(tmp_path, "dmsk8s\n")
    r, _ = _run(tmp_path, ovl, "--context", "dmsk8s", live=LIVE_ALL, rendered=RENDERED_HP)
    assert r.returncode == 5, (r.stdout, r.stderr)
    assert "live=[ ] -> overlay=[ /dms_shared/dms/artifacts ]" in r.stderr


def test_image_and_hostpath_change_need_both_permissions(tmp_path):
    ovl = _overlay(tmp_path, "dmsk8s\n")
    live = {**LIVE_ALL, "FAKE_LIVE_dms_api": "reg/dms:k0", **HP_OLD}
    r, _ = _run(tmp_path, ovl, "--context", "dmsk8s", live=live, rendered=RENDERED_HP)
    assert r.returncode == 3, (r.stdout, r.stderr)                 # 이미지가 먼저
    r, _ = _run(tmp_path, ovl, "--context", "dmsk8s", live=live, rendered=RENDERED_HP,
                extra_env={"ALLOW_IMAGE_CHANGE": "1"})
    assert r.returncode == 5, (r.stdout, r.stderr)
    r, _ = _run(tmp_path, ovl, "--context", "dmsk8s", "--allow-hostpath-change", live=live, rendered=RENDERED_HP,
                extra_env={"ALLOW_IMAGE_CHANGE": "1"})
    assert r.returncode == 0, (r.stdout, r.stderr)


@needs_kubectl
def test_repo_testbed_render_against_the_pre_switch_live_layout_is_refused(tmp_path):
    # 실제 testbed-k8s 렌더(SHARED_DIR=/dms_shared) 대 전환 전 라이브(d16 -- /cephfs 하나) -- 태그를 맞춰도 5 로 멈춘다.
    rendered = render("testbed-k8s")
    img = re.search(r"image: (\S+/dms:\S+)", rendered).group(1)
    agent = re.search(r"image: (\S+/dms-agent:\S+)", rendered).group(1)
    live = {"FAKE_LIVE_dms_api": img, "FAKE_LIVE_dms_controller": img, "FAKE_LIVE_dms_agent": agent, **HP_OLD}
    ovl = _overlay(tmp_path, "dmsk8s\n")
    r, _ = _run(tmp_path, ovl, "--context", "dmsk8s", live=live, rendered=rendered)
    assert r.returncode == 5, (r.stdout, r.stderr)


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


# --- 플랫폼 층: 클러스터 내 레지스트리(2026-11-03 ④) ----------------------------------------------------------------------
# 가드의 워크로드·hostPath 추출은 Deployment 일반이라 렌더된 platform/registry 에도 그대로 건다: 저장소(<SHARED_DIR>/dms/registry)가
# 바뀌면 exit 5, 이미지(태그 ref)가 바뀌면 exit 3 + 업그레이드 순서 안내, DMS 소유 층이면 다음 명령이 SSA.

def _rendered_registry(tmp_path) -> tuple:
    """실제 렌더러(deploy/render.sh testbed-k8s)가 낸 platform/registry 디렉터리와 그 kustomize 출력."""
    import shutil
    root = tmp_path / "tree" / "deploy"
    root.mkdir(parents=True)
    shutil.copy(REPO_ROOT / "deploy" / "render.sh", root / "render.sh")
    for d in ("platform", "k8s", "overlays", "addons"):              # addons·versions.env·images.lock — ⑤ 애드온·DB 층(2026-11-03)
        shutil.copytree(REPO_ROOT / "deploy" / d, root / d)
    for name in ("versions.env", "images.lock"):
        shutil.copy(REPO_ROOT / "deploy" / name, root / name)
    r = subprocess.run(["sh", str(root / "render.sh"), "testbed-k8s"], capture_output=True, text=True, timeout=60,
                       env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")})
    assert r.returncode == 0, (r.stdout, r.stderr)
    layer = root / ".rendered" / "testbed-k8s" / "platform" / "registry"
    k = subprocess.run(["kubectl", "kustomize", str(layer)], capture_output=True, text=True, timeout=120,
                       env={**os.environ, "KUBECONFIG": os.devnull})
    assert k.returncode == 0, k.stderr
    return layer, k.stdout


REG_IMG = "10.112.0.50:5000/registry:3.1.2"
REG_HP = "/dms_shared/dms/registry"


@needs_kubectl
def test_registry_layer_same_as_live_passes_with_an_ssa_next_command(tmp_path):
    layer, rendered = _rendered_registry(tmp_path)
    live = {"FAKE_LIVE_dms_registry": REG_IMG, "FAKE_HP_dms_registry": REG_HP}
    r, calls = _run(tmp_path, layer, "--context", "dmsk8s", live=live, rendered=rendered)
    assert r.returncode == 0, (r.stdout, r.stderr)
    # DMS 소유 층(managed-by dms-install)은 SSA — 앱 오버레이(client-side apply)와 다르다(설계 8-2).
    assert f"kubectl --context dmsk8s apply --server-side --field-manager=dms-install -k {layer}" in r.stdout
    assert any("get deploy dms-registry" in c for c in _gets(calls))


@needs_kubectl
def test_registry_first_install_passes(tmp_path):
    layer, rendered = _rendered_registry(tmp_path)
    r, _ = _run(tmp_path, layer, "--context", "dmsk8s", rendered=rendered)
    assert r.returncode == 0, (r.stdout, r.stderr)


@needs_kubectl
def test_registry_image_change_is_exit_3_with_the_upgrade_order(tmp_path):
    layer, rendered = _rendered_registry(tmp_path)
    live = {"FAKE_LIVE_dms_registry": "10.112.0.50:5000/registry:3.1.1", "FAKE_HP_dms_registry": REG_HP}
    r, _ = _run(tmp_path, layer, "--context", "dmsk8s", live=live, rendered=rendered)
    assert r.returncode == 3, (r.stdout, r.stderr)
    assert f"Deployment/dms-registry: live=10.112.0.50:5000/registry:3.1.1 -> overlay={REG_IMG}" in r.stderr
    assert "crictl pull" in r.stderr and "99-dms-pinned.conf" in r.stderr
    r, _ = _run(tmp_path, layer, "--context", "dmsk8s", live=live, rendered=rendered,
                extra_env={"ALLOW_IMAGE_CHANGE": "1"})
    assert r.returncode == 0, (r.stdout, r.stderr)


@needs_kubectl
def test_registry_storage_move_is_exit_5_with_the_copy_hint(tmp_path):
    layer, rendered = _rendered_registry(tmp_path)
    live = {"FAKE_LIVE_dms_registry": REG_IMG, "FAKE_HP_dms_registry": "/old_shared/dms/registry"}
    r, _ = _run(tmp_path, layer, "--context", "dmsk8s", live=live, rendered=rendered)
    assert r.returncode == 5, (r.stdout, r.stderr)
    assert f"Deployment/dms-registry hostPath: live=[ /old_shared/dms/registry ] -> overlay=[ {REG_HP} ]" in r.stderr
    assert "센티널" in r.stderr and "--numeric-ids" in r.stderr
    # 2026-11-03 ④ 리뷰 2: 새 경로의 S0 가 쓴 센티널을 옛 값으로 덮지 않게 복사에서 빼고, values 의 SHARED_DIR_ID 를 새 값으로 다시 렌더한다
    # — 통째로 rsync 하면 「센티널 불일치」로 기동을 거부한다.
    assert "--exclude=/.dms-registry-id" in r.stderr and "SHARED_DIR_ID" in r.stderr and "render.sh" in r.stderr, r.stderr


@needs_kubectl
def test_registry_layer_is_pinned_like_its_overlay(tmp_path):
    # 렌더러는 오버레이의 kube-context 를 각 층에 복사한다 — testbed-k8s 의 레지스트리 층을 dms(ssc)에 겨누면 exit 4.
    layer, rendered = _rendered_registry(tmp_path)
    r, calls = _run(tmp_path, layer, "--context", "dms", rendered=rendered)
    assert r.returncode == 4, (r.stdout, r.stderr)
    assert _gets(calls) == [] and not any(" kustomize " in f" {c} " for c in calls), calls
    # 「맞는 짝」 안내도 DMS 소유 층이면 SSA(2026-11-03 ④ 리뷰) — client-side 로 따라 하면 last-applied 주석이 붙고 필드 소유가 갈린다.
    assert (f"kubectl --context dmsk8s apply --server-side --field-manager=dms-install -k {layer}" in r.stderr), r.stderr
    r, _ = _run(tmp_path, f"{layer}/", "--context", "dms", rendered=rendered)
    assert r.returncode == 4 and "--server-side --field-manager=dms-install" in r.stderr, r.stderr


def test_app_overlay_context_refusal_hint_stays_client_side(tmp_path):
    r, _ = _run(tmp_path, _overlay(tmp_path, "dmsk8s\n"), "--context", "dms", live=LIVE_SAME)
    assert r.returncode == 4 and "맞는 짝" in r.stderr and "--server-side" not in r.stderr, r.stderr


def test_app_overlay_next_command_stays_client_side(tmp_path):
    r, _ = _run(tmp_path, _overlay(tmp_path, "dmsk8s\n"), "--context", "dmsk8s", live=LIVE_SAME)
    assert r.returncode == 0 and "--server-side" not in r.stdout, (r.stdout, r.stderr)
