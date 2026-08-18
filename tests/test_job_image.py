"""잡 이미지 DB 오버라이드(슬라이스 35): resolve 우선순위·저장소·릴리스 통합."""
import pytest

from dms.config import Settings
from dms.db import Database
from dms.job_image import resolve_job_image
from dms.migrations import migrate
from dms.repositories import Repositories

ADMIN = {"Authorization": "Bearer tok-shared"}


@pytest.fixture
def repos(tmp_path):
    db = Database.connect(f"sqlite:///{tmp_path}/t.db")
    migrate(db)
    return Repositories(db)


def _settings(job_image="pkg-01:5000/dms-mpifileutils:d53"):
    return Settings(database_url="unused", shared_token="t", admin_token="a",
                    session_secret="s", job_image=job_image)


def test_resolve_prefers_db_over_env(repos):
    s = _settings()
    assert resolve_job_image(repos.control, s) == "pkg-01:5000/dms-mpifileutils:d53"
    repos.control.set_job_image("pkg-01:5000/dms-mpifileutils:d80", actor="ops")
    assert resolve_job_image(repos.control, s) == "pkg-01:5000/dms-mpifileutils:d80"


def test_set_job_image_audits(repos):
    repos.control.set_job_image("pkg-01:5000/dms-mpifileutils:d80", actor="ops")
    entries = repos.control.audit_entries(limit=5)
    assert any(e["mutation_class"] == "job_image" and e["actor"] == "ops"
               for e in entries)


def test_control_state_update_does_not_clobber_job_image(repos):
    # set_control_state 는 job_image 컬럼을 만지지 않아야 한다 -- 무조건 UPDATE 에
    # 얹으면 유지보수 토글 한 번이 오버라이드를 조용히 NULL 로 지운다
    # (set_artifact_base 주석의 그 함정).
    repos.control.set_job_image("pkg-01:5000/dms-mpifileutils:d80", actor="ops")
    repos.control.set_control_state(maintenance=True, drain=False, reason="점검",
                                    build_node_name=None, actor="ops")
    assert (repos.control.control_state()["job_image"]
            == "pkg-01:5000/dms-mpifileutils:d80")


# ---- 릴리스 통합(job-image 컴포넌트) ----

def _mock_registry(monkeypatch, tags=("d53", "d80")):
    monkeypatch.setattr("dms.api.routes_releases.fetch_repo_tags",
                        lambda registry, repo: list(tags))


def test_targets_include_job_image_row(client, monkeypatch):
    _mock_registry(monkeypatch)
    body = client.get("/api/admin/releases/targets", headers=ADMIN).json()
    by = {t["component"]: t for t in body["targets"]}
    assert "job-image" in by
    row = by["job-image"]
    assert row["repository"] == "dms-mpifileutils"
    # current 는 유효값(DB 미설정이면 env). conftest 는 env 가 비어 있어 None 으로
    # 접힌다 -- 빈 문자열을 그대로 내보내면 "비교 불가"와 섞인다.
    assert row["current_image"] is None
    assert row["tags"] == ["d53", "d80"]


def test_release_job_image_applies_immediately(client, monkeypatch):
    _mock_registry(monkeypatch)
    r = client.post("/api/admin/releases",
                    json={"items": [{"component": "job-image", "tag": "d80"}]},
                    headers=ADMIN)
    assert r.status_code == 202, r.text
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["component"] == "job-image"
    assert items[0]["state"] == "Applied"          # 컨트롤러 없이 즉시 적용
    # DB 오버라이드가 실제로 박혔다 -- resolve 가 이 값을 읽는다.
    st = client.app.state.repos.control.control_state()
    assert st["job_image"].endswith("/dms-mpifileutils:d80")
    # 이력·current 에도 남는다.
    cur = client.get("/api/admin/releases", headers=ADMIN).json()["current"]
    assert cur["job-image"]["tag"] == "d80"


def test_release_job_image_same_tag_is_422(client, monkeypatch):
    _mock_registry(monkeypatch)
    client.post("/api/admin/releases",
                json={"items": [{"component": "job-image", "tag": "d80"}]},
                headers=ADMIN)
    r = client.post("/api/admin/releases",
                    json={"items": [{"component": "job-image", "tag": "d80"}]},
                    headers=ADMIN)
    assert r.status_code == 422 and r.json()["detail"] == "same_tag"


def test_release_job_image_unknown_tag_is_422(client, monkeypatch):
    _mock_registry(monkeypatch, tags=("d53",))
    r = client.post("/api/admin/releases",
                    json={"items": [{"component": "job-image", "tag": "nope"}]},
                    headers=ADMIN)
    assert r.status_code == 422 and r.json()["detail"] == "unknown_tag"


def test_adapter_and_probe_read_the_override_per_call(repos):
    # 생성자 캡처 금지 계약: 오버라이드 변경이 재시작 없이 다음 호출에 반영된다.
    s = _settings()
    calls = []

    def fn():
        calls.append(1)
        return resolve_job_image(repos.control, s)

    from dms.build_runner import BuildRunner

    class _K8s:
        def __init__(self):
            self.created = []

        def create(self, m):
            self.created.append(m)

        def get(self, *a):
            return None

    k8s = _K8s()
    runner = BuildRunner(k8s, namespace="dms", registry="pkg-01:5000",
                         builder_image="b", timeout_seconds=10, job_image=fn,
                         preflight_timeout_seconds=10)
    build = {"build_id": "0" * 32, "repo_url": "/src/dms", "git_ref": "local",
             "images": ["dms"], "node_name": "dms-w1"}
    runner.submit_preflight(build)
    img1 = k8s.created[-1]["spec"]["containers"][0]["image"]
    repos.control.set_job_image("pkg-01:5000/dms-mpifileutils:d80", actor="ops")
    runner.submit_preflight(build)   # AlreadyExists 관용 -- get None 이라 재생성
    img2 = k8s.created[-1]["spec"]["containers"][0]["image"]
    assert img1.endswith(":d53") and img2.endswith(":d80")
    assert len(calls) == 2
