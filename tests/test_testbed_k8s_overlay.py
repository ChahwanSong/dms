"""k8s 테스트베드 오버레이(deploy/overlays/testbed-k8s) 계약(2026-10-21).

사용자 결정: 「dms-k8s 브런치에서는 LDAP 을 사용 안할거야. 이점은 확실히해둬.」 — 코드에는 "LDAP 끔" 스위치가 없고
(값이 비면 리졸버 None = 비 root 잡 ldap_not_configured), base(deploy/k8s)는 ssc 테스트베드의 LDAP 값
(pkg-01 slapd)과 nslcd 를 띄우는 에이전트 엔트리포인트를 싣는다. 오버레이가 한 줄이라도 놓치면 결과는 기동 실패
(시끄러움)이거나 **같은 망의 ssc 용 slapd 를 조용히 쓰는 것**(침묵)이다. 그래서 여기서 두 층으로 고정한다:

- 정적(kubectl 없이도 돈다): base 의 LDAP 키·에이전트 LDAP env 가 **전부** 오버레이에서 지워지는지 — base 에 키가
  새로 생기면 여기서 빨간불(오버레이에 null/$patch: delete 를 더하라).
- 렌더(`kubectl kustomize`, 클러스터·kubeconfig 를 쓰지 않는다): 렌더 결과 어디에도 'ldap'(대소문자 무시)이 없고,
  에이전트가 `dms agent` 로 직접 뜨고, 이미지·빌드 레지스트리가 이 클러스터의 :5001 이고, 공인 VIP·포탈 호스트가
  k8s 값인지. (모든 오버레이의 자리표시자 이미지 검사는 tests/test_overlay_render_contract.py — 공용.)

렌더 파싱은 PyYAML 없이(저장소 의존성이 아니다 — manifest_tags 와 같은 방침) kustomize 출력의 고정된 모양
(키 정렬·2칸 들여쓰기·시퀀스는 부모 키와 같은 들여쓰기)에 기대는 최소 정규식이다. 찾지 못하면 조용히 통과하지 않고
assert 로 실패한다.
"""
import re
from pathlib import Path

from overlay_render import (OVERLAYS, REPO_ROOT, config_data, doc, expected_context, images, needs_kubectl, render,
                            seq)

K8S = OVERLAYS / "testbed-k8s"
SSC = OVERLAYS / "testbed"
BASE_CONFIG = REPO_ROOT / "deploy" / "k8s" / "20-config.yaml"
BASE_AGENT = REPO_ROOT / "deploy" / "k8s" / "50-agent-daemonset.yaml"

K8S_REGISTRY = "pkg-01:5001"
SHARED_BUILDER = "pkg-01:5000/buildah:stable"   # 빌더 미러만 ssc 의 :5000 을 공용으로 읽는다
K8S_VIP = "10.20.20.101/32"
K8S_HOST = "dms-k8s.local"

def _code(path: Path) -> str:
    return "\n".join(ln for ln in path.read_text(encoding="utf-8").splitlines()
                     if not ln.lstrip().startswith("#"))


# --- 정적 계약(kubectl 불필요) ---------------------------------------------------

def _base_ldap_config_keys() -> set:
    keys = set(re.findall(r"^  (DMS_LDAP_[A-Z0-9_]+):", _code(BASE_CONFIG), re.M))
    assert keys, "base 20-config.yaml 에서 LDAP 키를 못 찾았다 -- 파서나 base 모양이 바뀌었다"
    return keys


def _base_ldap_agent_env() -> set:
    names = set(re.findall(r"- name: (DMS_LDAP_[A-Z0-9_]+)", _code(BASE_AGENT)))
    assert names, "base 50-agent-daemonset.yaml 에서 LDAP env 를 못 찾았다 -- 파서나 base 모양이 바뀌었다"
    return names


def test_patch_config_deletes_every_base_ldap_key():
    text = _code(K8S / "patch-config.yaml")
    nulled = set(re.findall(r"^  (DMS_LDAP_[A-Z0-9_]+): null\s*$", text, re.M))
    missing = _base_ldap_config_keys() - nulled
    assert missing == set(), (
        f"base 20-config 의 LDAP 키 {sorted(missing)} 가 testbed-k8s 에서 지워지지 않는다 -- patch-config.yaml 에 "
        f"`<키>: null` 을 더하라(빈 값·\"false\" 가 아니라 삭제: 남은 base 값은 ssc 용 slapd 를 가리킨다)")
    valued = [ln.strip() for ln in text.splitlines()
              if re.match(r"^  DMS_LDAP_[A-Z0-9_]+:", ln) and not re.search(r":\s*null\s*$", ln)]
    assert valued == [], f"testbed-k8s 는 LDAP 키에 값을 두지 않는다(삭제만): {valued}"


def test_patch_agent_bypasses_nslcd_and_deletes_every_base_ldap_env():
    text = _code(K8S / "patch-agent.yaml")
    assert re.search(r'^\s*- name: agent\s*$', text, re.M), "컨테이너 이름(agent)으로 병합해야 한다"
    assert re.search(r'^\s*command: \["dms"\]\s*$', text, re.M), "에이전트는 엔트리포인트(nslcd) 없이 `dms` 로"
    assert re.search(r'^\s*args: \["agent"\]\s*$', text, re.M), "에이전트 args 는 [agent]"
    deleted = set(re.findall(r"- name: (DMS_LDAP_[A-Z0-9_]+)\s*\n\s*\$patch: delete", text))
    missing = _base_ldap_agent_env() - deleted
    assert missing == set(), (
        f"base 에이전트 env {sorted(missing)} 가 testbed-k8s 에서 지워지지 않는다 -- patch-agent.yaml 에 "
        f"`$patch: delete` 항목을 더하라(지운 ConfigMap 키를 참조하면 CreateContainerConfigError)")


def test_k8s_overlay_kustomization_wires_every_patch_and_the_k8s_registry():
    text = _code(K8S / "kustomization.yaml")
    for patch in ("patch-config.yaml", "patch-agent.yaml", "patch-ingress.yaml", "patch-metallb.yaml"):
        assert f"- path: {patch}" in text, f"kustomization.yaml 가 {patch} 를 걸지 않는다"
        assert (K8S / patch).is_file(), patch
    new_names = re.findall(r"newName: (\S+)", text)
    assert new_names and all(n.startswith(K8S_REGISTRY + "/") for n in new_names), new_names


def test_the_two_testbeds_do_not_share_registry_vip_host_or_context():
    # 복사·붙여넣기로 ssc 값이 k8s 오버레이에 실리는 회귀(태그·삭제·「사용 중」 판정이 섞이고, 엉뚱한 클러스터를
    # 겨눈다)를 막는다.
    ssc_names = set(re.findall(r"newName: (\S+)", _code(SSC / "kustomization.yaml")))
    k8s_names = set(re.findall(r"newName: (\S+)", _code(K8S / "kustomization.yaml")))
    assert {n.split("/", 1)[0] for n in ssc_names}.isdisjoint({n.split("/", 1)[0] for n in k8s_names})
    assert K8S_VIP in _code(K8S / "patch-metallb.yaml")
    assert K8S_VIP not in _code(REPO_ROOT / "deploy" / "k8s" / "47-metallb-public-pool.yaml")
    assert K8S_HOST in _code(K8S / "patch-ingress.yaml")
    ctx = {o: expected_context(OVERLAYS / o) for o in ("testbed", "testbed-k8s")}
    assert ctx == {"testbed": "dms", "testbed-k8s": "dmsk8s"}, ctx


# --- 렌더 계약(kubectl kustomize -- 로컬 렌더만, 클러스터·kubeconfig 무관; 헬퍼는 tests/overlay_render.py) ----

@needs_kubectl
def test_render_mentions_ldap_nowhere():
    text = render("testbed-k8s")
    offenders = [ln.strip() for ln in text.splitlines() if "ldap" in ln.lower()]
    assert offenders == [], (
        f"testbed-k8s 렌더에 LDAP 흔적이 있다(사용자 결정: dms-k8s 는 LDAP 없음): {offenders[:10]}")


@needs_kubectl
def test_render_runs_the_agent_directly_without_ldap_env():
    ds = doc(render("testbed-k8s"), "DaemonSet", "dms-agent")
    assert seq(ds, "command") == ["dms"]
    assert seq(ds, "args") == ["agent"]
    assert "agent-entrypoint" not in ds and "nslcd" not in ds.lower()
    env_names = re.findall(r"(?m)^\s*- name: (DMS_\w+)$", ds)
    assert "DMS_SHARED_TOKEN" in env_names and "DMS_AGENT_HOST_ROOT" in env_names, env_names
    secret_keys = re.findall(r"secretKeyRef:\n\s*key: (\w+)", ds)
    assert secret_keys == ["DMS_SHARED_TOKEN"], secret_keys   # 바인드 비밀번호 키를 참조하지 않는다


@needs_kubectl
def test_render_images_and_build_registry_are_the_k8s_registry():
    text = render("testbed-k8s")
    found = images(text)
    assert len(found) >= 6, found        # api·controller 본체+migrate init, migrate job, agent
    assert all(i.startswith(K8S_REGISTRY + "/") for i in found), found
    data = config_data(text)
    assert data["DMS_BUILD_REGISTRY"] == K8S_REGISTRY
    assert data["DMS_JOB_IMAGE"].startswith(f"{K8S_REGISTRY}/dms-mpifileutils:"), data["DMS_JOB_IMAGE"]
    assert data["DMS_BUILD_BUILDER_IMAGE"] == SHARED_BUILDER


@needs_kubectl
def test_render_identity_is_root_only_by_privileged_list():
    data = config_data(render("testbed-k8s"))
    assert not [k for k in data if k.startswith("DMS_LDAP_")]
    assert data["DMS_PRIVILEGED_REQUESTERS"] == "root,admin,mason"
    assert data["DMS_ALLOW_PRIVILEGED_REQUESTERS"] == "true"
    assert data["DMS_IDENTITY_SUPPLEMENTARY_GROUPS"] == "false"


@needs_kubectl
def test_render_public_vip_and_portal_host():
    text = render("testbed-k8s")
    pool = doc(text, "IPAddressPool", "dms-public-pool")
    assert seq(pool, "addresses") == [K8S_VIP]
    ingress = doc(text, "Ingress", "dms-portal")
    assert seq(ingress, "hosts") == [K8S_HOST]


@needs_kubectl
def test_render_ssc_testbed_keeps_its_ldap_and_entrypoint():
    # LDAP 삭제가 testbed-k8s 오버레이 안에서만 일어나는지(base·ssc 오버레이로 새지 않았는지) — ssc 는 LDAP 을 쓴다.
    text = render("testbed")
    data = config_data(text)
    assert data["DMS_LDAP_URI"].startswith("ldap://"), data.get("DMS_LDAP_URI")
    ds = doc(text, "DaemonSet", "dms-agent")
    assert seq(ds, "command") == ["/usr/local/bin/agent-entrypoint.sh"]
    assert not any(i.startswith(K8S_REGISTRY + "/") for i in images(text))
