"""k8s 테스트베드 오버레이(deploy/overlays/testbed-k8s) 계약(2026-10-21 → 2026-10-26 LDAP 코드 삭제).

사용자 결정: 「dms-k8s 브런치에서는 LDAP 을 사용 안할거야. 이점은 확실히해둬.」 → 2026-10-26 「(d) LDAP 코드 삭제 및 정리」:
dms-k8s 는 k8s 신원 모드 전용이다(모든 단건 잡 root + 권한 = k8s RoleBinding 맵핑, dms-k8s RBAC·PV 설계 D1). base
(deploy/k8s)에 LDAP 키·에이전트 LDAP env·nslcd 엔트리포인트가 더는 없어 오버레이의 LDAP null 패치·patch-agent.yaml 도
지웠다. 여기서 두 층으로 고정한다:

- 정적(kubectl 없이도 돈다): base·오버레이 어디에도 DMS_LDAP_* 가 없고, 신원 모드가 base 의 "k8s" 이며, 오버레이가 거는
  패치 목록·레지스트리가 맞는지. ssc 테스트베드 오버레이(deploy/overlays/testbed)는 이 브랜치에 없다(dms-ssc 소관).
- 렌더(`kubectl kustomize`, 클러스터·kubeconfig 를 쓰지 않는다): 렌더 결과 어디에도 'ldap'(대소문자 무시)이 없고,
  에이전트가 `dms agent` 로 직접 뜨고, 이미지·빌드 레지스트리가 이 클러스터의 :5001 이고, 공인 VIP·포탈 호스트가
  k8s 값인지. (모든 오버레이의 자리표시자 이미지 검사는 tests/test_overlay_render_contract.py — 공용.)

렌더 파싱은 PyYAML 없이(저장소 의존성이 아니다 — manifest_tags 와 같은 방침) kustomize 출력의 고정된 모양
(키 정렬·2칸 들여쓰기·시퀀스는 부모 키와 같은 들여쓰기)에 기대는 최소 정규식이다. 찾지 못하면 조용히 통과하지 않고
assert 로 실패한다.
"""
import re
from pathlib import Path

from dms.config import Settings

from overlay_render import (OVERLAYS, REPO_ROOT, config_data, doc, expected_context, images, needs_kubectl, render,
                            seq)

K8S = OVERLAYS / "testbed-k8s"
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

def test_no_ldap_anywhere_in_base_or_overlay():
    # base 와 오버레이 어디에도 LDAP 키·env 가 없다(되살리면 config 가 DMS_LDAP_URI 로 기동을 거부한다 -- 그 전에 여기서 빨간불).
    for path in [BASE_CONFIG, BASE_AGENT, *sorted(K8S.glob("*.yaml"))]:
        assert "DMS_LDAP_" not in _code(path), path
        assert "nslcd" not in _code(path).lower(), path
    assert not (K8S / "patch-agent.yaml").exists()          # base 가 `dms agent` 라 필요 없다
    assert not (OVERLAYS / "testbed").exists()              # ssc 오버레이는 dms-ssc 브랜치에만


def _config_values(path: Path) -> dict:
    return dict(re.findall(r'^  (DMS_[A-Z0-9_]+): "([^"]*)"\s*$', _code(path), re.M))


def test_identity_mode_comes_from_base_and_user_operations_from_the_overlay():
    # 신원 모드는 base 의 "k8s"(유일한 모드 -- 코드도 값이 없으면 k8s; ssc 설정의 "ldap" 은 기동 거부). 사용자 연산은 코드
    # 기본이 {sync} 라 이 오버레이가 rm 을 연다(scan 은 특권 운영자만, 2026-10-26).
    assert _config_values(BASE_CONFIG)["DMS_IDENTITY_MODE"] == "k8s"
    values = _config_values(K8S / "patch-config.yaml")
    assert "DMS_IDENTITY_MODE" not in values and "DMS_IDENTITY_SUPPLEMENTARY_GROUPS" not in values
    assert values["DMS_USER_ALLOWED_OPERATIONS"] == "sync,rm"
    assert values["DMS_ALLOW_PRIVILEGED_REQUESTERS"] == "true"      # false 면 기동 거부


def test_k8s_overlay_kustomization_wires_every_patch_and_the_k8s_registry():
    text = _code(K8S / "kustomization.yaml")
    for patch in ("patch-config.yaml", "patch-ingress.yaml", "patch-metallb.yaml"):
        assert f"- path: {patch}" in text, f"kustomization.yaml 가 {patch} 를 걸지 않는다"
        assert (K8S / patch).is_file(), patch
    new_names = re.findall(r"newName: (\S+)", text)
    assert new_names and all(n.startswith(K8S_REGISTRY + "/") for n in new_names), new_names


def test_the_k8s_testbed_keeps_its_own_registry_vip_host_and_context():
    # ssc 값(:5000 레지스트리·ssc VIP·컨텍스트 dms)이 복사·붙여넣기로 실리는 회귀(태그·삭제·「사용 중」 판정이 섞이고, 엉뚱한
    # 클러스터를 겨눈다)를 막는다.
    k8s_names = set(re.findall(r"newName: (\S+)", _code(K8S / "kustomization.yaml")))
    assert k8s_names and {n.split("/", 1)[0] for n in k8s_names} == {K8S_REGISTRY}
    assert K8S_VIP in _code(K8S / "patch-metallb.yaml")
    assert K8S_VIP not in _code(REPO_ROOT / "deploy" / "k8s" / "47-metallb-public-pool.yaml")
    assert K8S_HOST in _code(K8S / "patch-ingress.yaml")
    assert expected_context(K8S) == "dmsk8s"


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
def test_render_identity_is_the_k8s_mode_with_the_privileged_list_as_the_exemption():
    # k8s 신원 모드(2026-10-25 설계 D1·D4·D15): 모든 단건 잡 root + 권한 = RoleBinding 맵핑. 특권 목록은 「맵핑 면제(전체 PV·
    # 직접 경로)」의 기준이다(root 자격이 아니라 -- 모두 root 다). 렌더 결과가 실제로 서버 설정으로 기동되는지도 본다.
    data = config_data(render("testbed-k8s"))
    assert not [k for k in data if k.startswith("DMS_LDAP_")]
    assert data["DMS_IDENTITY_MODE"] == "k8s"
    assert data["DMS_USER_ALLOWED_OPERATIONS"] == "sync,rm"
    assert data["DMS_PRIVILEGED_REQUESTERS"] == "root,admin,mason"
    assert data["DMS_ALLOW_PRIVILEGED_REQUESTERS"] == "true"
    assert "DMS_IDENTITY_SUPPLEMENTARY_GROUPS" not in data
    settings = Settings.from_env({**data, "DMS_DATABASE_URL": "postgresql://u:p@h/d", "DMS_SHARED_TOKEN": "s",
                                  "DMS_ADMIN_TOKEN": "a", "DMS_SESSION_SECRET": "x"})
    assert settings.allow_privileged_requesters is True  # 기동 = k8s 신원 모드(모든 잡 root -- 끄면 기동 거부)
    assert settings.user_allowed_operations == frozenset({"sync", "rm"})


@needs_kubectl
def test_render_public_vip_and_portal_host():
    text = render("testbed-k8s")
    pool = doc(text, "IPAddressPool", "dms-public-pool")
    assert seq(pool, "addresses") == [K8S_VIP]
    ingress = doc(text, "Ingress", "dms-portal")
    assert seq(ingress, "hosts") == [K8S_HOST]
