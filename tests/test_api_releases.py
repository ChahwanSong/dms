import pytest

from dms.execution import ExecutionError

ADMIN = {"Authorization": "Bearer tok-shared"}


class _FakeRunner:
    """observe만 쓰인다(읽기 전용) -- api는 patch를 부르면 안 된다(patch RBAC은
    컨트롤러에만 있다, 설계 §5)."""
    def __init__(self, images=None):
        self._images = images or {}   # (kind, workload) -> {container: image}
        self.fail_observe = False

    def observe(self, *, kind, name):
        if self.fail_observe:
            raise ExecutionError("observe_failed", "down")
        images = self._images.get((kind, name))
        if images is None:
            return None
        return {"kind": kind, "generation": 1, "observed_generation": 1,
                "images": dict(images)}

    def patch_image(self, **kw):
        raise AssertionError("api must never patch")


@pytest.fixture
def rollout_client(client, monkeypatch):
    # 레지스트리: dms/dms-agent 둘 다 응답. 개별 테스트가 monkeypatch로 덮는다.
    monkeypatch.setattr(
        "dms.api.routes_releases.fetch_repo_tags",
        lambda registry, repo: {"dms": ["d22", "d23"],
                                "dms-agent": ["dev5", "dev6"],
                                "dms-mpifileutils": ["d53", "d80"]}.get(repo))
    client.app.state.rollout_runner = _FakeRunner({
        ("Deployment", "dms-api"): {"api": "pkg-01:5000/dms:d22"},
        ("Deployment", "dms-controller"): {"controller": "pkg-01:5000/dms:d22"},
        ("DaemonSet", "dms-agent"): {"agent": "pkg-01:5000/dms-agent:dev5"},
    })
    return client


def test_targets_expose_current_image_and_tags(rollout_client):
    r = rollout_client.get("/api/admin/releases/targets", headers=ADMIN)
    assert r.status_code == 200
    body = r.json()
    assert body["registry_ok"] is True
    by_comp = {t["component"]: t for t in body["targets"]}
    # 넷째 행 job-image(슬라이스 35): 워크로드 뒤에 온다.
    assert list(by_comp) == ["dms-agent", "dms-api", "dms-controller", "job-image"]
    assert by_comp["dms-agent"]["current_image"] == "pkg-01:5000/dms-agent:dev5"
    assert by_comp["dms-agent"]["tags"] == ["dev5", "dev6"]
    assert by_comp["dms-controller"]["container"] == "controller"


def test_targets_query_registry_once_per_repository(rollout_client, monkeypatch):
    # api/controller는 같은 dms 리포를 쓴다 -- 한 응답을 만드는 데 같은 리포를 두 번
    # 때리면 폴링 화면이 레지스트리 부하를 두 배로 만든다.
    calls = []

    def counting(registry, repo):
        calls.append(repo)
        return ["d22", "d23"]
    monkeypatch.setattr("dms.api.routes_releases.fetch_repo_tags", counting)
    rollout_client.get("/api/admin/releases/targets", headers=ADMIN)
    # job-image 행(mfu 리포) 몫이 하나 늘지만, 같은 리포 중복 조회는 여전히 없다.
    assert sorted(calls) == ["dms", "dms-agent", "dms-mpifileutils"]


def test_targets_survive_registry_outage(rollout_client, monkeypatch):
    # 레지스트리가 죽어도 화면 전체가 죽으면 안 된다(설계 §7) -- 빈 목록 + 경고
    monkeypatch.setattr("dms.api.routes_releases.fetch_repo_tags",
                        lambda registry, repo: None)
    r = rollout_client.get("/api/admin/releases/targets", headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["registry_ok"] is False
    assert all(t["tags"] == [] for t in r.json()["targets"])


def test_targets_survive_workload_read_failure(rollout_client):
    # 워크로드 읽기 실패도 같은 강등 원칙 -- current_image만 null이 되고 화면은 산다
    rollout_client.app.state.rollout_runner.fail_observe = True
    r = rollout_client.get("/api/admin/releases/targets", headers=ADMIN)
    assert r.status_code == 200
    # job-image 의 current 는 observe 가 아니라 resolve(DB→env)라 이 실패와 무관 --
    # conftest 는 env(job_image)가 비어 있어 None 으로 접힌다(결과적으로 전 행 None).
    assert all(t["current_image"] is None for t in r.json()["targets"])


def test_submit_orders_components_server_side(rollout_client):
    r = rollout_client.post(
        "/api/admin/releases",
        json={"items": [{"component": "dms-controller", "tag": "d23"},
                        {"component": "dms-agent", "tag": "dev6"}]},
        headers=ADMIN)
    assert r.status_code == 202
    items = r.json()["items"]
    assert [i["component"] for i in items] == ["dms-agent", "dms-controller"]
    assert items[0]["image"] == "pkg-01:5000/dms-agent:dev6"
    assert all(i["state"] == "Pending" for i in items)


def test_unknown_component_and_duplicates_rejected(rollout_client):
    r = rollout_client.post("/api/admin/releases",
                            json={"items": [{"component": "nope", "tag": "t"}]},
                            headers=ADMIN)
    assert r.status_code == 422 and r.json()["detail"] == "unknown_component"
    r = rollout_client.post(
        "/api/admin/releases",
        json={"items": [{"component": "dms-api", "tag": "d23"},
                        {"component": "dms-api", "tag": "d23"}]},
        headers=ADMIN)
    assert r.status_code == 422 and r.json()["detail"] == "unknown_component"
    r = rollout_client.post("/api/admin/releases", json={"items": []}, headers=ADMIN)
    assert r.status_code == 422 and r.json()["detail"] == "unknown_component"


def test_rejected_submits_write_nothing(rollout_client):
    # 거절은 릴리스 행을 남기면 안 된다 -- 남으면 active()가 비지 않아 다음 롤아웃이
    # rollout_in_progress로 영원히 막힌다.
    rollout_client.post("/api/admin/releases",
                        json={"items": [{"component": "dms-api", "tag": "d22"}]},
                        headers=ADMIN)
    body = rollout_client.get("/api/admin/releases", headers=ADMIN).json()
    assert body["history"] == [] and body["current"] == {}


def test_unknown_tag_enforced_only_when_registry_answers(rollout_client, monkeypatch):
    r = rollout_client.post("/api/admin/releases",
                            json={"items": [{"component": "dms-api", "tag": "ghost"}]},
                            headers=ADMIN)
    assert r.status_code == 422 and r.json()["detail"] == "unknown_tag"
    # 레지스트리 침묵 -> 통과시키고 ImagePullBackOff로 드러나게 한다(잘못된 차단보다 낫다)
    monkeypatch.setattr("dms.api.routes_releases.fetch_repo_tags",
                        lambda registry, repo: None)
    r = rollout_client.post("/api/admin/releases",
                            json={"items": [{"component": "dms-api", "tag": "ghost"}]},
                            headers=ADMIN)
    assert r.status_code == 202


def test_malformed_tag_is_rejected_even_when_registry_is_silent(rollout_client,
                                                                monkeypatch):
    # 태그 문자열은 이미지 참조에 그대로 들어간다 -- 형식 검증은 레지스트리 응답
    # 여부와 무관하게 항상 강제한다(fail-open은 "존재하는가"에만 적용된다).
    monkeypatch.setattr("dms.api.routes_releases.fetch_repo_tags",
                        lambda registry, repo: None)
    for bad in ("", "  ", "-lead", "has space", "a" * 200, "tag:extra", "../evil"):
        r = rollout_client.post(
            "/api/admin/releases",
            json={"items": [{"component": "dms-api", "tag": bad}]}, headers=ADMIN)
        assert r.status_code == 422 and r.json()["detail"] == "unknown_tag", bad


def test_same_tag_is_rejected(rollout_client):
    # IfNotPresent 함정: 같은 태그 재적용은 아무 일도 안 일어난다
    r = rollout_client.post("/api/admin/releases",
                            json={"items": [{"component": "dms-api", "tag": "d22"}]},
                            headers=ADMIN)
    assert r.status_code == 422 and r.json()["detail"] == "same_tag"


def test_same_tag_skipped_when_current_unreadable(rollout_client):
    # 워크로드를 못 읽으면(observe None) fail-open -- 레지스트리와 같은 원칙
    rollout_client.app.state.rollout_runner = _FakeRunner()
    r = rollout_client.post("/api/admin/releases",
                            json={"items": [{"component": "dms-api", "tag": "d22"}]},
                            headers=ADMIN)
    assert r.status_code == 202


def test_same_tag_skipped_when_observe_fails(rollout_client):
    # observe가 ExecutionError로 죽어도 같은 강등 -- 제출 자체가 막히면 안 된다
    rollout_client.app.state.rollout_runner.fail_observe = True
    r = rollout_client.post("/api/admin/releases",
                            json={"items": [{"component": "dms-api", "tag": "d22"}]},
                            headers=ADMIN)
    assert r.status_code == 202


def test_concurrent_rollout_is_409(rollout_client):
    rollout_client.post("/api/admin/releases",
                        json={"items": [{"component": "dms-api", "tag": "d23"}]},
                        headers=ADMIN)
    r = rollout_client.post("/api/admin/releases",
                            json={"items": [{"component": "dms-agent", "tag": "dev6"}]},
                            headers=ADMIN)
    assert r.status_code == 409 and r.json()["detail"] == "rollout_in_progress"


def test_submit_rejected_during_maintenance(rollout_client):
    rollout_client.put("/api/admin/control-state",
                       json={"maintenance": True, "drain": False, "reason": "정비"},
                       headers=ADMIN)
    r = rollout_client.post("/api/admin/releases",
                            json={"items": [{"component": "dms-api", "tag": "d23"}]},
                            headers=ADMIN)
    assert r.status_code == 503 and r.json()["detail"] == "maintenance_mode"


def test_list_carries_current_and_history(rollout_client):
    rollout_client.post("/api/admin/releases",
                        json={"items": [{"component": "dms-api", "tag": "d23"}]},
                        headers=ADMIN)
    r = rollout_client.get("/api/admin/releases", headers=ADMIN)
    assert r.status_code == 200
    body = r.json()
    assert body["current"]["dms-api"]["tag"] == "d23"
    assert body["history"][0]["component"] == "dms-api"


def test_internal_columns_never_leave_the_api(rollout_client):
    # seq(누가 head인가)와 progress(DaemonSet 회수 시계의 내부 상태)는 저장소
    # 내부 값이다 -- routes_builds._detail과 같은 관례로 응답에서 뺀다. SELECT *가
    # 그대로 나가면 내부 컬럼이 조용히 공개 스키마가 된다.
    posted = rollout_client.post(
        "/api/admin/releases",
        json={"items": [{"component": "dms-api", "tag": "d23"}]},
        headers=ADMIN).json()
    body = rollout_client.get("/api/admin/releases", headers=ADMIN).json()
    rows = (posted["items"] + body["history"] + list(body["current"].values()))
    assert rows, "검사할 행이 없으면 이 테스트는 아무것도 증명하지 못한다"
    for row in rows:
        assert "seq" not in row and "progress" not in row, row
        assert row["component"] == "dms-api"        # 나머지 필드는 그대로 나간다


def test_submit_writes_release_audit_with_actor(rollout_client):
    rollout_client.post("/api/admin/releases",
                        json={"items": [{"component": "dms-api", "tag": "d23"}]},
                        headers=ADMIN)
    entries = rollout_client.app.state.repos.control.audit_entries(limit=3)
    entry = next(e for e in entries if e["mutation_class"] == "release")
    # 공유 토큰 인증은 token: 접두(슬라이스 12 audit_actor). 슬라이스 19 에서
    # x-dms-actor 를 헤더에서 지운 뒤 토큰 actor 는 shared-token 으로 정규화된다.
    assert entry["actor"] == "token:shared-token"


def test_submit_writes_exactly_one_release_audit_row(rollout_client):
    # create_batch가 트랜잭션 안에서 이미 감사 행을 쓴다 -- 라우터가 하나 더 쓰면
    # 같은 제출이 감사 로그에 두 번 나타난다.
    rollout_client.post("/api/admin/releases",
                        json={"items": [{"component": "dms-api", "tag": "d23"},
                                        {"component": "dms-agent", "tag": "dev6"}]},
                        headers=ADMIN)
    entries = rollout_client.app.state.repos.control.audit_entries(limit=20)
    assert len([e for e in entries if e["mutation_class"] == "release"]) == 1


def test_releases_are_admin_only(client):
    client.post("/api/auth/signup", json={"username": "u1", "password": "p"})
    client.post("/api/auth/login", json={"username": "u1", "password": "p"})
    assert client.get("/api/admin/releases").status_code in (401, 403)
    assert client.get("/api/admin/releases/targets").status_code in (401, 403)
    assert client.post("/api/admin/releases", json={"items": []}).status_code in (401, 403)


def test_submit_reports_unverified_when_repo_is_silent(rollout_client, db,
                                                       monkeypatch):
    """슬라이스 28(BACKLOG §2.3): fail-open 은 유지하되 침묵을 걷어낸다.
    리포별 부분 침묵(dms 는 응답, dms-agent 만 None)으로 검증의 리포 단위
    granularity 까지 고정한다 -- 응답한 리포의 태그는 정상 검증되고(dev 태그가
    목록에 있어 통과), 침묵 리포만 미검증으로 표시된다."""
    monkeypatch.setattr(
        "dms.api.routes_releases.fetch_repo_tags",
        lambda registry, repo: {"dms": ["d22", "d23"]}.get(repo))  # dms-agent 는 None
    r = rollout_client.post(
        "/api/admin/releases",
        json={"items": [{"component": "dms-api", "tag": "d23"},
                        {"component": "dms-agent", "tag": "dev9"}]},
        headers=ADMIN)
    assert r.status_code == 202
    assert r.json()["tag_verified"] is False
    events = db.query(
        "SELECT payload FROM events WHERE event_type = 'release_tag_unverified'")
    assert len(events) == 1
    assert "dms-agent" in events[0]["payload"]
    assert "dms-api" not in events[0]["payload"]   # 검증된 컴포넌트는 안 싣는다


def test_submit_verified_when_registry_answers(rollout_client, db):
    # 정상 경로: 레지스트리가 답했고 태그가 존재 -- 플래그 true, 이벤트 0건.
    # 이벤트가 정상 제출마다 쌓이면 "경고"의 의미가 죽는다(늑대 소년).
    r = rollout_client.post("/api/admin/releases",
                            json={"items": [{"component": "dms-api", "tag": "d23"}]},
                            headers=ADMIN)
    assert r.status_code == 202
    assert r.json()["tag_verified"] is True
    assert db.query("SELECT id FROM events"
                    " WHERE event_type = 'release_tag_unverified'") == []


def test_rejected_submit_leaves_no_unverified_event(rollout_client, db,
                                                    monkeypatch):
    # 레지스트리 침묵 + 형식 불량 태그 -> 422 거절. 거절엔 커밋된 것이 없으므로
    # "건너뛰고 통과시켰다"는 사실 자체가 없다 -- 이벤트도 없어야 한다.
    # (현행도 그렇다 -- 이 테스트는 이벤트 기록이 create_batch 앞으로 끌려가는
    # 회귀를 막는 그물이다.)
    monkeypatch.setattr("dms.api.routes_releases.fetch_repo_tags",
                        lambda registry, repo: None)
    r = rollout_client.post("/api/admin/releases",
                            json={"items": [{"component": "dms-api", "tag": "has space"}]},
                            headers=ADMIN)
    assert r.status_code == 422
    assert db.query("SELECT id FROM events"
                    " WHERE event_type = 'release_tag_unverified'") == []
