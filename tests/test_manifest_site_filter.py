"""동봉 매니페스트의 사이트 귀속 필터(2026-09-08, manifest_tags.site_image).

포탈 밖에서 부트스트랩한 이미지는 소스 트리의 매니페스트(테스트베드
pkg-01:5000/dms:d119)를 그대로 담는다. 신규 사이트에서 그 값을 "이 사이트의
매니페스트 기준"으로 읽으면 없는 드리프트와 남의 태그가 화면에 샌다 -- 레지스트리가
다른 동봉값은 None(모름)이어야 한다."""
import pytest
from dms.manifest_tags import image_registry, site_image


@pytest.mark.parametrize("image,registry", [
    ("pkg-01:5000/dms:d119", "pkg-01:5000"),
    ("registry.corp.example/dms:v1", "registry.corp.example"),
    ("localhost:5000/dms:d1", "localhost:5000"),
    ("10.0.0.5:5000/dms-agent:d2", "10.0.0.5:5000"),
    ("python:3.11-slim", None),                # docker.io 암묵 -- 레지스트리 없음
    ("library/python:3.11", None),
    ("dms", None),
])
def test_image_registry(image, registry):
    assert image_registry(image) == registry


def test_site_image_keeps_only_this_sites_registry():
    assert site_image("pkg-01:5000/dms:d119", "pkg-01:5000") == "pkg-01:5000/dms:d119"
    assert site_image("pkg-01:5000/dms:d119", "PKG-01:5000") == "pkg-01:5000/dms:d119"
    assert site_image("pkg-01:5000/dms:d119", "reg.ssc.example:5000") is None
    assert site_image("python:3.11-slim", "pkg-01:5000") is None
    assert site_image(None, "pkg-01:5000") is None
    assert site_image("pkg-01:5000/dms:d119", "") is None


def test_infra_metrics_hide_a_foreign_sites_manifest(client, monkeypatch, settings, db):
    # 테스트베드 소스 트리의 동봉값(pkg-01:5000)을 다른 레지스트리 사이트에서 읽는 상황.
    from dataclasses import replace
    from fastapi.testclient import TestClient
    from dms.api.app import create_app
    monkeypatch.setattr("dms.api.routes_metrics.manifest_images",
                        lambda: {"dms-api": "pkg-01:5000/dms:d119",
                                 "dms-controller": "pkg-01:5000/dms:d119",
                                 "dms-agent": "pkg-01:5000/dms-agent:d118"})
    monkeypatch.setattr("dms.api.routes_metrics.manifest_job_image",
                        lambda: "pkg-01:5000/dms-mpifileutils:d110")
    headers = {"Authorization": "Bearer tok-shared"}
    # 같은 레지스트리(테스트베드): 기준값으로 산다
    body = client.get("/api/admin/metrics/infra", headers=headers).json()
    by_name = {c["component"]: c for c in body["components"]}
    assert by_name["dms-api"]["manifest_image"] == "pkg-01:5000/dms:d119"
    assert by_name["dms-agent"]["manifest_image"] == "pkg-01:5000/dms-agent:d118"
    assert body["job_image"]["manifest"] == "pkg-01:5000/dms-mpifileutils:d110"
    # 다른 사이트: None(모름) -- 드리프트 비교 재료가 아니다
    foreign = TestClient(create_app(replace(settings, build_registry="reg.ssc.example:5000"), db))
    body = foreign.get("/api/admin/metrics/infra", headers=headers).json()
    assert all(c["manifest_image"] is None for c in body["components"])
    assert body["job_image"]["manifest"] is None


def test_registry_in_use_ignores_a_foreign_sites_manifest(client, monkeypatch, settings, db):
    from dataclasses import replace
    from fastapi.testclient import TestClient
    from dms.api.app import create_app
    from dms.api import routes_registry
    monkeypatch.setattr(routes_registry, "manifest_images",
                        lambda: {"dms-api": "pkg-01:5000/dms:d119",
                                 "dms-controller": "pkg-01:5000/dms:d119",
                                 "dms-agent": "pkg-01:5000/dms-agent:d118"})
    monkeypatch.setattr(routes_registry, "manifest_job_image",
                        lambda: "pkg-01:5000/dms-mpifileutils:d110")

    class Req:
        class app:
            class state:
                pass

    req = Req()
    req.app.state.rollout_runner = client.app.state.rollout_runner
    req.app.state.settings = client.app.state.settings
    assert routes_registry._in_use_tags(req)["dms"] == {"d119"}
    req.app.state.settings = replace(settings, build_registry="reg.ssc.example:5000")
    assert routes_registry._in_use_tags(req)["dms"] == set()
