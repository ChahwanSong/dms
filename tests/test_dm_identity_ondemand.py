"""On-demand identity probing — worker가 요청 시점에 등록한 대상을 agent가 다음
사이클에 프로빙해 증거를 만드는 경로.

고정하는 계약:
- repo: register(upsert·TTL 갱신) / list(TTL 윈도·prune-on-read·정렬·상한).
- API: agent report POST 응답에 identity_probe_targets 포함(실패해도 ingest는 성공).
- agent: static+dynamic 병합(검증·중복 제거·상한), 응답 파싱 fail-soft,
  계층형 프로빙(호스트 파일 → 컨테이너 NSS; chroot 실패는 조용히 다음 계층).
- worker: _await_identity_evidence — DM 리포트에 증거가 나타나면 즉시 반환,
  타임아웃 시 조용히 반환(fail-closed 게이트는 하류 그대로), wait=0이면 무동작.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from dms import agent_daemon
from dms.agent_daemon import (
    extract_identity_probe_targets,
    merge_identity_users,
    probe_identities,
)
from dms.api import create_app
from dms.config import Settings
from dms.db import Database
from dms.migrations import migrate_all
from dms.repositories import DmsRepository, ObservabilityRepository
from dms.workers.dm import DMWorkerRuntime

API_HEADERS = {"x-dms-actor": "node:cluster-a:node-1"}


@pytest.fixture()
def harness(tmp_path):
    operational_url = f"sqlite:///{tmp_path / 'operational.db'}"
    observability_url = f"sqlite:///{tmp_path / 'observability.db'}"
    settings = Settings(
        database_url=operational_url,
        observability_database_url=observability_url,
        agent_report_stale_seconds=300,
        control_cluster_name="cluster-a",
    )
    operational = Database(operational_url)
    observability_db = Database(observability_url)
    migrate_all(operational, observability_db)
    repository = DmsRepository(operational)
    observability = ObservabilityRepository(observability_db)
    app = create_app(settings, repository, observability)
    return {"client": TestClient(app), "repository": repository}


# --- repository -------------------------------------------------------------


def test_repo_register_and_list_targets(harness):
    repo = harness["repository"]
    repo.register_identity_probe_target("alice")
    repo.register_identity_probe_target("bob")
    repo.register_identity_probe_target("alice")  # upsert(TTL 갱신), 중복 없음
    assert repo.list_identity_probe_targets(ttl_seconds=3600) == ["alice", "bob"]


def test_repo_targets_expire_and_prune(harness):
    repo = harness["repository"]
    repo.register_identity_probe_target("old-user")
    # 과거로 밀어 TTL 밖으로
    stale = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
    with repo.database.connect() as conn:
        conn.execute(
            "UPDATE dm_identity_probe_targets SET last_requested_at = ?", (stale,)
        )
    repo.register_identity_probe_target("fresh-user")
    assert repo.list_identity_probe_targets(ttl_seconds=3600) == ["fresh-user"]
    # prune-on-read: 만료 행은 삭제됨
    with repo.database.connect() as conn:
        rows = conn.execute("SELECT username FROM dm_identity_probe_targets").fetchall()
    assert len(rows) == 1


def test_repo_targets_limit_keeps_most_recent(harness):
    # Cap must keep the MOST RECENTLY requested (not alphabetically first): a
    # late-alphabet user who just submitted must not be starved when >limit users
    # are active. Set explicit, well-separated timestamps to avoid clock flakiness.
    repo = harness["repository"]
    now = datetime.now(UTC)
    for i in range(7):
        repo.register_identity_probe_target(f"user{i:02d}")
    with repo.database.connect() as conn:
        for i in range(7):
            conn.execute(
                "UPDATE dm_identity_probe_targets SET last_requested_at = ? "
                "WHERE username = ?",
                ((now - timedelta(seconds=i)).isoformat(), f"user{i:02d}"),
            )
    # user00 = newest, user06 = oldest -> top-3 by recency are user00/01/02.
    assert repo.list_identity_probe_targets(ttl_seconds=3600, limit=3) == [
        "user00", "user01", "user02",
    ]
    # A brand-new (late-alphabet) requester lands at the top, evicting the oldest.
    repo.register_identity_probe_target("zzz.newcomer")
    assert repo.list_identity_probe_targets(ttl_seconds=3600, limit=1) == ["zzz.newcomer"]


# --- API: report POST 응답 ---------------------------------------------------


def _minimal_report() -> dict:
    return {
        "cluster_name": "cluster-a",
        "node_name": "node-1",
        "node_uid": "uid-node-1",
        "worker_role": "DM",
    }


def test_report_response_carries_probe_targets(harness):
    harness["repository"].register_identity_probe_target("cocoa.song")
    resp = harness["client"].post(
        "/api/v1/agent/reports", json=_minimal_report(), headers=API_HEADERS
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "Fresh"
    assert body["identity_probe_targets"] == ["cocoa.song"]


def test_report_response_empty_targets_by_default(harness):
    resp = harness["client"].post(
        "/api/v1/agent/reports", json=_minimal_report(), headers=API_HEADERS
    )
    assert resp.status_code == 200
    assert resp.json()["identity_probe_targets"] == []


# --- agent: 병합/파싱/계층형 프로빙 ------------------------------------------


def test_merge_identity_users_validates_and_bounds():
    merged = merge_identity_users(
        ["alice", "alice", " bob ", "bad;rm -rf", "../etc", ""],
        ["carol", "alice", "0lead"],  # 0lead: 숫자 시작 → 거부
    )
    assert merged == ["alice", "bob", "carol"]
    # 상한
    many = merge_identity_users([], [f"user{i:03d}" for i in range(500)])
    assert len(many) == agent_daemon.MAX_IDENTITY_PROBE_USERS


def test_extract_identity_probe_targets_fail_soft():
    assert extract_identity_probe_targets({}) == []
    assert extract_identity_probe_targets({"identity_probe_targets": "junk"}) == []
    assert extract_identity_probe_targets(
        {"identity_probe_targets": ["ok.user", 7, None, "bad name"]}
    ) == ["ok.user"]


def test_probe_identities_host_files_layer(tmp_path, monkeypatch):
    # chroot 계층은 실패(비특권) → 호스트 파일 계층이 노드-로컬 사용자를 해석한다.
    monkeypatch.setattr(
        agent_daemon.subprocess, "run",
        lambda *a, **k: (_ for _ in ()).throw(OSError("no chroot")),
    )
    etc = tmp_path / "etc"
    etc.mkdir()
    (etc / "passwd").write_text(
        "root:x:0:0:root:/root:/bin/sh\n"
        "cocoa.song:x:15000:15000::/home/cocoa.song:/sbin/nologin\n"
    )
    (etc / "group").write_text("wheel:x:10:cocoa.song\nother:x:11:someone\n")
    ev = probe_identities(["cocoa.song", "ghost"], host_root=str(tmp_path))
    by = {e["username"]: e for e in ev}
    assert by["cocoa.song"]["status"] == "Ready"
    assert by["cocoa.song"]["uid"] == 15000 and by["cocoa.song"]["gid"] == 15000
    assert by["cocoa.song"]["groups"] == ["wheel"]
    assert by["ghost"]["status"] == "Missing"


def test_probe_identities_container_fallback_and_validation(monkeypatch):
    monkeypatch.delenv("DMS_AGENT_HOST_ROOT", raising=False)
    ev = probe_identities(["root", "bad name!"])
    by = {e["username"]: e for e in ev}
    assert by["root"]["status"] == "Ready" and by["root"]["uid"] == 0
    assert by["bad name!"]["status"] == "Missing"
    assert "not probed" in by["bad name!"]["reason"]


# --- worker: bounded await ---------------------------------------------------


def _dm_report(node: str, ready: bool):
    users = (
        [{"username": "alice", "status": "Ready", "uid": 15000}] if ready else []
    )
    return {
        "cluster_name": "c", "node_name": node, "worker_role": "DM",
        "report": {"identity_evidence": {"users": users}},
    }


class _AwaitRepo:
    """list_agent_reports가 N번째 호출부터 (모든 DM 노드에) Ready 증거를 반환하는 페이크."""

    def __init__(self, ready_after_calls: int, nodes: int = 1):
        self.calls = 0
        self.ready_after = ready_after_calls
        self.nodes = nodes

    def list_agent_reports(self, **_kw):
        self.calls += 1
        ready = self.calls >= self.ready_after
        return [_dm_report(f"n{i}", ready) for i in range(self.nodes)]


def _runtime(repo, wait: float, poll: float = 0.02) -> DMWorkerRuntime:
    return DMWorkerRuntime(
        repository=repo, observability=None, volcano_adapter=None, worker_id="t",
        identity_probe_wait_seconds=wait, identity_probe_poll_seconds=poll,
    )


def test_await_returns_when_evidence_arrives():
    repo = _AwaitRepo(ready_after_calls=3)
    _runtime(repo, wait=2.0)._await_identity_evidence("alice")
    assert repo.calls == 3  # 두 번 비고 세 번째에 증거 → 즉시 반환


def test_await_waits_for_all_dm_nodes_not_just_one():
    # 증거가 노드별로 점진 전파(1/3 → 2/3 → 3/3). 첫 노드에서 조기 반환하면 후보
    # 선정이 insufficient_eligible_nodes로 실패할 수 있으므로, 전체 fresh DM 노드가
    # Ready가 될 때까지 기다려야 한다.
    class GrowRepo:
        def __init__(self):
            self.calls = 0

        def list_agent_reports(self, **_kw):
            self.calls += 1
            ready_n = min(self.calls, 3)
            return [_dm_report(f"n{i}", ready=(i < ready_n)) for i in range(3)]

    repo = GrowRepo()
    _runtime(repo, wait=2.0, poll=0.01)._await_identity_evidence("alice")
    assert repo.calls == 3  # 1/3, 2/3 에서 반환하지 않고 3/3 에서 반환


def test_await_times_out_quietly():
    repo = _AwaitRepo(ready_after_calls=999)
    _runtime(repo, wait=0.1, poll=0.02)._await_identity_evidence("alice")
    assert repo.calls >= 2  # 폴링은 했고, 예외 없이 조용히 반환


def test_await_disabled_when_wait_zero():
    repo = _AwaitRepo(ready_after_calls=1)
    _runtime(repo, wait=0)._await_identity_evidence("alice")
    assert repo.calls == 0


#  --- job-pod passwd materialization: injection-safe env + shell guards ------
# On-demand probing means unlisted requesters reach the job pod, whose image has no
# NSS — so the launcher/worker materialize a passwd entry from the resolved uid/gid.
# That entry is written to /etc/passwd, so the identity env MUST be injection-safe.


def test_identity_env_vars_validates_shape():
    from dms.adapters.volcano import _identity_env_vars, DataManagementRuntimeError

    assert _identity_env_vars(
        {"posix_username": "seongje.jang", "uid": 10004, "gid": 10000}
    ) == [
        {"name": "DMS_POSIX_USERNAME", "value": "seongje.jang"},
        {"name": "DMS_POSIX_UID", "value": "10004"},
        {"name": "DMS_POSIX_GID", "value": "10000"},
    ]
    # privileged root (uid/gid 0) is a legitimate shape
    assert _identity_env_vars({"posix_username": "root", "uid": 0, "gid": 0})[1][
        "value"
    ] == "0"
    # empty identity (preview/preflight manifest, no resolved user) is allowed and
    # emits "" — the shell prologue then skips passwd materialization.
    assert _identity_env_vars({}) == [
        {"name": "DMS_POSIX_USERNAME", "value": ""},
        {"name": "DMS_POSIX_UID", "value": ""},
        {"name": "DMS_POSIX_GID", "value": ""},
    ]
    # a NON-empty but malformed/injecting identity is rejected (fail-closed)
    for bad in (
        {"posix_username": "a:b", "uid": 1000, "gid": 1000},  # colon
        {"posix_username": "x\nroot:x:0:0", "uid": 1000, "gid": 1000},  # newline
        {"posix_username": "alice", "uid": "0; rm -rf /", "gid": 1000},  # non-numeric
        {"posix_username": "alice", "uid": 1000, "gid": "9 9"},  # non-numeric gid
    ):
        with pytest.raises(DataManagementRuntimeError):
            _identity_env_vars(bad)


def test_rendered_prologues_are_valid_sh_and_guard_injection():
    import re
    import subprocess

    from dms.adapters import volcano

    worker = volcano._mpi_worker_command()
    launcher = volcano._mpiexec_line(stdout='"$out"', stderr='"$err"')
    artifact = volcano._chown_artifact_line("$artifact")
    for script in (worker, "true; " + launcher, artifact):
        assert subprocess.run(["sh", "-n"], input=script, text=True).returncode == 0

    # login shell must be /bin/sh (a nologin shell makes the worker sshd close the MPI
    # orted connection); passwd materialization must PRECEDE the artifact chown so the
    # name resolves; the mpiexec line must NOT re-materialize (single source).
    assert "/tmp/dms-home-%s:/bin/sh" in worker and "nologin" not in worker
    assert artifact.find("printf '%s:x:%s") < artifact.find(
        'chown -R "$DMS_POSIX_USERNAME"'
    )
    assert "printf '%s:x:%s" not in launcher

    # the shell case-guards reject a crafted uid/username even if Python were bypassed
    stmt = volcano._identity_materialize_stmt()
    guards = re.findall(r"case .*?esac;", stmt)
    assert len(guards) == 3

    def run(un, uid, gid=""):
        s = f"DMS_POSIX_USERNAME={un!r}; DMS_POSIX_UID={uid!r}; DMS_POSIX_GID={gid!r}; " + " ".join(guards) + " echo OK"
        return subprocess.run(["sh", "-c", s], text=True, capture_output=True).returncode

    assert run("alice", "0\nroot:x:0:0:") == 1  # newline-injected uid
    assert run("a:b", "1000") == 1  # colon in username
    assert run("alice", "1000", "x") == 1  # non-numeric gid
    assert run("seongje.jang", "10004", "10000") == 0  # valid passes


def test_await_ignores_rm_only_evidence():
    class RmRepo(_AwaitRepo):
        def list_agent_reports(self, **_kw):
            self.calls += 1
            return [{
                "cluster_name": "c", "node_name": "n", "worker_role": "RM",
                "report": {"identity_evidence": {"users": [
                    {"username": "alice", "status": "Ready", "uid": 15000},
                ]}},
            }]

    repo = RmRepo(ready_after_calls=0)
    _runtime(repo, wait=0.08, poll=0.02)._await_identity_evidence("alice")
    assert repo.calls >= 2  # RM 증거로는 만족하지 않고 타임아웃까지 감
