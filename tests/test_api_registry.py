import pytest

ADMIN = {"Authorization": "Bearer tok-shared"}


class _FakeRunner:
    def __init__(self, images):
        self._images = images

    def observe(self, *, kind, name):
        images = self._images.get((kind, name))
        return None if images is None else {"kind": kind, "images": dict(images)}


@pytest.fixture
def reg_client(client, monkeypatch):
    # 레지스트리 태그 삭제는 세션 관리자만(admin_session_required, 2026-10-07) -- 공유 토큰은 모든 노드
    # 에이전트가 쥔 자격이다. 세션 관리자로 로그인해 두고 삭제 호출엔 Bearer 헤더를 싣지 않는다(헤더가
    # 세션보다 우선). 목록 조회는 토큰 그대로 둔다(토큰 조회는 계속 된다).
    client.app.state.repos.accounts.create("opadm", "p", "admin", actor="t")
    assert client.post("/api/auth/login", json={"username": "opadm", "password": "p"}).status_code == 200
    # live: dms d74(api·controller), agent be9168b17. 매니페스트(동봉본)는 실
    # deploy/k8s 파일이 아니라 **고정 목**으로 준다 -- 실 파일을 읽게 두면 배포
    # 태그 bump 커밋마다 이 테스트가 깨진다(d80 정렬에서 실제로 깨졌다). 여기서
    # 고정하는 값이 아래 in_use 단언의 유일한 근거다: dms d74·agent d53·mfu d53.
    monkeypatch.setattr("dms.api.routes_registry.manifest_images",
                        lambda: {"dms-api": "pkg-01:5000/dms:d74",
                                 "dms-controller": "pkg-01:5000/dms:d74",
                                 "dms-agent": "pkg-01:5000/dms-agent:d53"})
    monkeypatch.setattr("dms.api.routes_registry.manifest_job_image",
                        lambda: "pkg-01:5000/dms-mpifileutils:d53")
    client.app.state.rollout_runner = _FakeRunner({
        ("Deployment", "dms-api"): {"api": "pkg-01:5000/dms:d74"},
        ("Deployment", "dms-controller"): {"controller": "pkg-01:5000/dms:d74"},
        ("DaemonSet", "dms-agent"): {"agent": "pkg-01:5000/dms-agent:be9168b17"},
    })
    # 레지스트리 태그: dms 는 d74(사용 중)+b99d(미사용), 나머지는 각각 몇 개.
    monkeypatch.setattr("dms.api.routes_registry.registry_mod.fetch_repo_tags",
        lambda registry, repo: {
            "dms": ["b99d97238", "d74"],
            "dms-mpifileutils": ["b99d97238", "d53"],
            "dms-agent": ["b99d97238", "be9168b17", "d53"],
        }.get(repo))
    return client


def test_list_marks_in_use_tags(reg_client):
    r = reg_client.get("/api/admin/registry/images", headers=ADMIN)
    assert r.status_code == 200
    body = r.json()
    assert body["registry"] == "pkg-01:5000"
    by_repo = {x["repository"]: x for x in body["repositories"]}
    # 세 리포가 의존 순서로 온다.
    assert list(by_repo) == ["dms-mpifileutils", "dms", "dms-agent"]
    dms = {t["tag"]: t["in_use"] for t in by_repo["dms"]["tags"]}
    assert dms == {"d74": True, "b99d97238": False}      # d74 live+manifest, b99d 미사용
    agent = {t["tag"]: t["in_use"] for t in by_repo["dms-agent"]["tags"]}
    assert agent["be9168b17"] is True                    # live agent
    assert agent["d53"] is True                          # 동봉 매니페스트 agent 태그
    assert agent["b99d97238"] is False
    mfu = {t["tag"]: t["in_use"] for t in by_repo["dms-mpifileutils"]["tags"]}
    assert mfu["d53"] is True                            # 동봉 job image 태그
    assert mfu["b99d97238"] is False


def test_list_reports_unreachable_repo_distinct_from_empty(reg_client, monkeypatch):
    # None(무응답) 과 [](태그 0개) 는 다른 값이다 -- 화면이 "조회 실패"를 말할 수 있어야.
    monkeypatch.setattr("dms.api.routes_registry.registry_mod.fetch_repo_tags",
                        lambda registry, repo: None if repo == "dms" else [])
    body = reg_client.get("/api/admin/registry/images", headers=ADMIN).json()
    by_repo = {x["repository"]: x for x in body["repositories"]}
    assert by_repo["dms"]["reachable"] is False and by_repo["dms"]["tags"] == []
    assert by_repo["dms-agent"]["reachable"] is True and by_repo["dms-agent"]["tags"] == []


def test_delete_refuses_in_use_tag_before_touching_registry(reg_client, monkeypatch):
    called = {"digest": 0, "delete": 0}
    monkeypatch.setattr("dms.api.routes_registry.registry_mod.manifest_digest",
                        lambda *a: called.__setitem__("digest", called["digest"] + 1))
    monkeypatch.setattr("dms.api.routes_registry.registry_mod.delete_manifest",
                        lambda *a: called.__setitem__("delete", called["delete"] + 1))
    r = reg_client.delete("/api/admin/registry/images/dms/d74")
    assert r.status_code == 409 and r.json()["detail"] == "registry_tag_in_use"
    # 레지스트리를 아예 건드리지 않고 거절한다.
    assert called == {"digest": 0, "delete": 0}


def test_delete_unknown_repo_is_422(reg_client):
    r = reg_client.delete("/api/admin/registry/images/nope/b99d97238")
    assert r.status_code == 422 and r.json()["detail"] == "unknown_registry_repo"


def test_delete_unused_tag_resolves_digest_then_deletes(reg_client, monkeypatch):
    seen = {}
    monkeypatch.setattr("dms.api.routes_registry.registry_mod.manifest_digest",
                        lambda registry, repo, tag: "sha256:" + "a" * 64)
    def _del(registry, repo, digest):
        seen["digest"] = digest
        return "ok"
    monkeypatch.setattr("dms.api.routes_registry.registry_mod.delete_manifest", _del)
    r = reg_client.delete("/api/admin/registry/images/dms/b99d97238")
    assert r.status_code == 200
    assert r.json()["deleted"] == "dms:b99d97238"
    assert seen["digest"] == "sha256:" + "a" * 64


def test_delete_maps_registry_delete_disabled_to_409(reg_client, monkeypatch):
    monkeypatch.setattr("dms.api.routes_registry.registry_mod.manifest_digest",
                        lambda *a: "sha256:" + "b" * 64)
    monkeypatch.setattr("dms.api.routes_registry.registry_mod.delete_manifest",
                        lambda *a: "disabled")
    r = reg_client.delete("/api/admin/registry/images/dms/b99d97238")
    assert r.status_code == 409 and r.json()["detail"] == "registry_delete_disabled"


def test_delete_missing_tag_is_404(reg_client, monkeypatch):
    monkeypatch.setattr("dms.api.routes_registry.registry_mod.manifest_digest",
                        lambda *a: None)
    r = reg_client.delete("/api/admin/registry/images/dms/b99d97238")
    assert r.status_code == 404 and r.json()["detail"] == "registry_tag_not_found"


def test_list_and_delete_are_admin_only(client):
    client.post("/api/auth/signup", json={"username": "u1", "password": "p"})
    client.post("/api/auth/login", json={"username": "u1", "password": "p"})
    assert client.get("/api/admin/registry/images").status_code in (401, 403)
    assert client.delete("/api/admin/registry/images/dms/x").status_code in (401, 403)
