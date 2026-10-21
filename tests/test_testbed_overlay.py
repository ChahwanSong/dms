"""ssc 테스트베드 오버레이(deploy/overlays/testbed) 계약 — **main·dms-ssc 라인 전용** 파일(2026-10-22 브랜치 분리).

공용 렌더 계약(tests/test_overlay_render_contract.py)은 「있는 오버레이 전부」를 볼 뿐이라 브랜치 ↔ 클러스터 짝을 지키지
못한다. 이 라인의 짝은 여기서 고정한다:

- 이 브랜치는 자기 클러스터(ssc, 컨텍스트 dms) 오버레이만 싣는다 — k8s 클러스터 오버레이(testbed-k8s, dms-k8s 라인)가
  cherry-pick 충돌 해소·`git checkout origin/dms-k8s -- deploy/overlays` 같은 실수로 다시 들어오면 빨간불.
- ssc 라인은 LDAP 을 쓴다 — dms-k8s(무 LDAP) 라인에서 골라 온 커밋이 이 오버레이의 LDAP 을 벗기면 빨간불(렌더에 LDAP URI 와
  nslcd 를 띄우는 에이전트 엔트리포인트가 그대로인지, :5001(k8s 레지스트리) 이미지가 섞이지 않았는지).
"""
from overlay_render import OVERLAYS, config_data, doc, expected_context, images, needs_kubectl, present_overlays, render, seq

THIS_LINE_OVERLAYS = {"testbed", "prod", "ssc"}   # ssc 는 dms-ssc 에만 있다
K8S_REGISTRY = "pkg-01:5001"


def test_branch_carries_only_its_own_cluster_overlays():
    present = set(present_overlays())
    assert "testbed" in present, "main·dms-ssc 라인의 테스트베드 오버레이(deploy/overlays/testbed)가 없다"
    stray = present - THIS_LINE_OVERLAYS
    assert stray == set(), (
        f"이 라인에 다른 라인의 오버레이 {sorted(stray)} 가 있다 — testbed-k8s 는 dms-k8s 라인 전용(별도 세션). "
        f"브랜치마다 자기 클러스터 오버레이만 싣는다(CLAUDE.md 「브랜치 모델」)")


def test_ssc_testbed_overlay_is_pinned_to_the_ssc_context():
    assert expected_context(OVERLAYS / "testbed") == "dms"


@needs_kubectl
def test_render_ssc_testbed_keeps_its_ldap_and_entrypoint():
    text = render("testbed")
    data = config_data(text)
    assert data.get("DMS_LDAP_URI", "").startswith("ldap://"), data.get("DMS_LDAP_URI")
    ds = doc(text, "DaemonSet", "dms-agent")
    assert seq(ds, "command") == ["/usr/local/bin/agent-entrypoint.sh"]
    assert not any(i.startswith(K8S_REGISTRY + "/") for i in images(text)), images(text)
