"""모든 오버레이의 렌더 계약(공용 — 세 브랜치에 같은 파일, 2026-10-22 브랜치 분리 때 test_testbed_k8s_overlay.py 에서 옮김).

어느 브랜치든 그 브랜치가 싣는 오버레이 전부를 본다(main·dms-ssc = testbed·prod(+ssc), dms-k8s = testbed-k8s·prod)."""
import pytest

from dms.manifest_tags import PLACEHOLDER_REGISTRY
from overlay_render import config_data, images, needs_kubectl, present_overlays, render


def test_branch_carries_at_least_one_overlay():
    assert present_overlays(), "deploy/overlays/*/kustomization.yaml 이 하나도 없다"


@needs_kubectl
@pytest.mark.parametrize("overlay", present_overlays())
def test_every_overlay_renders_without_placeholder_images(overlay):
    text = render(overlay)
    found = images(text) + [config_data(text)["DMS_JOB_IMAGE"]]
    leftover = [i for i in found if i.startswith(PLACEHOLDER_REGISTRY + "/")]
    assert leftover == [], f"{overlay}: 자리표시자 이미지가 렌더에 남았다(images.name 매핑 누락) {leftover}"
