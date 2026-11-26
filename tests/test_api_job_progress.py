"""sync 진행률 API(GET /api/user/jobs/{job_id}/progress -- 2026-11-26 설계 §4·§7.2)와 dms.job_progress.sanitize 시험.

열기 사슬은 아티팩트 라우트와 같고(소유 → 만료 → 신원 uid → base 프로브 → open_artifact_fd), 파일 없음·사슬 실패·모양 틀림은
모두 200 {available:false} 한 몸통, 그 실패 경로에서만 base 수준 503. 모든 응답에 Cache-Control: no-store. 응답·스냅숏 모양은
계약 파일(frontend/src/lib/progressContract.json)과 대조한다(범위 밖 사례도 계약의 min/max 에서 만든다 -- 손으로 두 번 쓰지
않는다). 클라이언트·잡 헬퍼는 tests/test_api_artifact_base_unavailable.py 와 같은 모양(시험 파일끼리 import 하지 않는다)."""
import dataclasses
import json
import os
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dms import job_progress
from dms.api.app import create_app
from dms.config import Settings
from dms.db import Database
from dms.domain import DataJobState, RequestState
from dms.migrations import migrate

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = json.loads((ROOT / "frontend" / "src" / "lib" / "progressContract.json").read_text(encoding="utf-8"))
FIELDS = CONTRACT["snapshot"]["fields"]
EXAMPLES = CONTRACT["examples"]["snapshots"]
RESPONSES = CONTRACT["examples"]["responses"]
SHARED = {"Authorization": "Bearer tok-shared"}
NO_STORE = CONTRACT["api"]["headers"]


def _client(tmp_path, base):
    db = Database.connect(f"sqlite:///{tmp_path}/t.db")
    migrate(db)
    settings = Settings(database_url="unused", shared_token="tok-shared", admin_token="tok-admin",
                        session_secret="sess-secret", artifact_base_uri=f"file://{base}")
    app = create_app(settings, db)
    # 잡 디렉터리 교차 확인은 실 실행 백엔드에서만(identity_job_ids) -- 판정이 읽는 설정만 volcano 로(어댑터는 이미 stub).
    app.state.settings = dataclasses.replace(settings, execution_backend="volcano")
    return TestClient(app)


def _login(client, name="alice", role=None):
    client.cookies.clear()
    if role is not None:
        client.app.state.repos.accounts.create(name, "p", role, actor="t")
    else:
        client.post("/api/auth/signup", json={"username": name, "password": "p"})
    client.post("/api/auth/login", json={"username": name, "password": "p"})


def _job(client, *, requester="alice", uid=None, state=DataJobState.RUNNING, with_uri=False, base=None):
    repos = client.app.state.repos
    rid = repos.requests.create(operation="sync", requester_id=requester, actor=requester,
                                resource_key=f"k:{os.urandom(4).hex()}", payload={"storage": "s1", "target": "t"},
                                priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    identity = {"gid": os.getgid(), "username": requester}
    if uid is not False:
        identity["uid"] = os.getuid() if uid is None else uid
    jid = repos.data_jobs.create_job(
        rid, repos.data_jobs.create_plan(rid, actor="planner"), operation="sync", priority="mid",
        storage_name="s1", target="t", options={}, tool="dsync", worker_pool={"identity": identity},
        precondition={}, actor="planner")
    if with_uri:
        repos.data_jobs.set_artifact(jid, artifact_uri=f"file://{base}/{jid}", result_summary={})
    repos.data_jobs.set_job_state(jid, state, actor="stepper")
    if state == DataJobState.SUCCEEDED:
        repos.requests.finalize_from_job(rid, DataJobState.SUCCEEDED, actor="stepper")
    return jid


def _base(tmp_path):
    b = tmp_path / "artifacts"
    b.mkdir()
    b.chmod(0o755)
    return b


def _place(base, jid, data, phase="execution"):
    d = base / jid / phase
    d.mkdir(parents=True, exist_ok=True)
    path = d / "progress.json"
    path.write_bytes(data if isinstance(data, bytes) else json.dumps(data).encode())
    return path


def _get(client, jid, phase=None, **kw):
    url = f"/api/user/jobs/{jid}/progress" + (f"?phase={phase}" if phase is not None else "")
    return client.get(url, **kw)


def _assert_no_store(r):
    assert {k: r.headers.get(k) for k in NO_STORE} == NO_STORE, r.headers


@pytest.fixture
def env(tmp_path):
    base = _base(tmp_path)
    client = _client(tmp_path, base)
    jid = _job(client)
    (base / jid / "execution").mkdir(parents=True)                # 잡 디렉터리가 보인다 = 정상 base
    _login(client)
    return client, base, jid


# ---- 성공 경로·모양 ---------------------------------------------------------------------------------------------------------

def test_available_snapshot_matches_the_contract_response(env):
    client, base, jid = env
    snap = EXAMPLES["dsync_copy_overall"]
    path = _place(base, jid, snap)
    now = time.time()
    os.utime(path, (now - 4, now - 4))
    r = _get(client, jid)
    assert r.status_code == 200
    _assert_no_store(r)
    body = r.json()
    want = RESPONSES["available"]
    assert list(body) == list(want) and body["phase"] == want["phase"] and body["progress"] == want["progress"]
    assert 3 <= body["age_seconds"] <= 6 and type(body["age_seconds"]) is int


def test_preview_phase_reads_the_preview_file(env):
    client, base, jid = env
    _place(base, jid, EXAMPLES["nsync_batches_dry"], phase="preview")
    body = _get(client, jid, "preview").json()
    assert set(body) == set(RESPONSES["available_preview"]) and body["progress"] == RESPONSES[
        "available_preview"]["progress"] and body["phase"] == "preview"
    assert _get(client, jid).json() == RESPONSES["unavailable"]   # 실행 파일은 아직 없다


def test_missing_file_on_a_healthy_base_is_available_false(env):
    client, base, jid = env
    r = _get(client, jid)
    assert (r.status_code, r.json()) == (200, RESPONSES["unavailable"])
    _assert_no_store(r)
    assert list(r.json()) == CONTRACT["api"]["response"]["available_false_keys"]


def test_future_mtime_is_age_zero(env):
    client, base, jid = env
    path = _place(base, jid, EXAMPLES["workers"])
    os.utime(path, (time.time() + 3600, time.time() + 3600))
    assert _get(client, jid).json()["age_seconds"] == 0


# ---- 인가·오류(모두 no-store) -----------------------------------------------------------------------------------------------

def test_other_users_job_is_404_and_admin_and_shared_token_can_read(env):
    client, base, jid = env
    _place(base, jid, EXAMPLES["workers"])
    _login(client, "bob")
    r = _get(client, jid)
    assert (r.status_code, r.json()) == (404, {"detail": "job_not_found"})
    _assert_no_store(r)
    _login(client, "boss", role="admin")
    assert _get(client, jid).json()["available"] is True
    client.cookies.clear()
    assert _get(client, jid, headers=SHARED).json()["available"] is True     # 읽기 전용 -- 로그·아티팩트와 같다
    assert _get(client, jid).status_code == 401


def test_unknown_job_is_404_with_no_store(env):
    client, _, _ = env
    r = _get(client, "0" * 32)
    assert (r.status_code, r.json()) == (404, {"detail": "job_not_found"})
    _assert_no_store(r)


def test_bad_phase_is_422_after_the_ownership_check(env):
    client, _, jid = env
    r = _get(client, jid, "preflight")
    assert (r.status_code, r.json()) == (422, {"detail": "invalid_phase"})
    _assert_no_store(r)
    _login(client, "bob")
    assert _get(client, jid, "preflight").status_code == 404     # 남의 잡에는 phase 검사보다 소유가 먼저


def test_expired_job_is_410(env):
    client, base, jid = env
    _place(base, jid, EXAMPLES["workers"])
    client.app.state.repos.db.execute("UPDATE data_jobs SET artifacts_expired_at = :x WHERE job_id = :j",
                                      {"x": "2026-11-01T00:00:00Z", "j": jid})
    r = _get(client, jid)
    assert (r.status_code, r.json()) == (410, {"detail": "artifact_expired"})
    _assert_no_store(r)


def test_job_without_requester_uid_is_404_artifact_not_found(tmp_path):
    base = _base(tmp_path)
    client = _client(tmp_path, base)
    jid = _job(client, uid=False)
    _place(base, jid, EXAMPLES["workers"])
    _login(client)
    r = _get(client, jid)
    assert (r.status_code, r.json()) == (404, {"detail": "artifact_not_found"})
    _assert_no_store(r)


def test_missing_file_on_an_empty_local_base_is_503(tmp_path):
    # 사고 서명(불변식 16): base 는 있는데 DB 의 최근 성공 잡 디렉터리가 하나도 없다(미마운트 노드의 빈 로컬 디렉터리).
    base = _base(tmp_path)
    client = _client(tmp_path, base)
    _job(client, state=DataJobState.SUCCEEDED, with_uri=True, base=base)
    jid = _job(client)
    _login(client)
    r = _get(client, jid)
    assert (r.status_code, r.json()) == (503, {"detail": "artifact_base_unavailable"})
    _assert_no_store(r)


def test_hung_mount_short_circuits_before_opening_anything(tmp_path, monkeypatch):
    import dms.api.routes_artifacts as ra
    base = _base(tmp_path)
    client = _client(tmp_path, base)
    jid = _job(client)
    _login(client)
    runner = client.app.state.base_check_runner
    release = threading.Event()
    runner.run(("api", str(base)), lambda: release.wait(10), timeout=0.05)
    monkeypatch.setattr("dms.artifact_base.CHECK_TIMEOUT_SECONDS", 0.05)
    original = runner.stuck_since
    monkeypatch.setattr(runner, "stuck_since", lambda key, timeout=0.05: original(key, timeout=0.05))
    monkeypatch.setattr(ra, "open_artifact_fd", lambda *a, **k: (_ for _ in ()).throw(AssertionError("opened")))
    try:
        r = _get(client, jid)
        assert (r.status_code, r.json()) == (503, {"detail": "artifact_base_unavailable"})
        _assert_no_store(r)
    finally:
        release.set()


# ---- 사슬 실패·모양 틀림은 전부 같은 몸통 -------------------------------------------------------------------------------------

def _bad_cases(base, jid, tmp_path):
    d = base / jid / "execution"
    target = d / "progress.json"
    good = json.dumps(EXAMPLES["workers"]).encode()

    def symlink():
        outside = tmp_path / "outside.json"
        outside.write_bytes(good)
        target.symlink_to(outside)

    def hardlink():
        target.write_bytes(good)
        os.link(target, tmp_path / "second-link")

    def fifo():
        os.mkfifo(target)

    def directory():
        target.mkdir()

    return {
        "symlink": symlink, "hardlink": hardlink, "fifo": fifo, "directory": directory,
        "too_big": lambda: target.write_bytes(b'{"v":1,"tool":"dsync","phase":"copy","pad":"' + b"x" * 17000 + b'"}'),
        "not_utf8": lambda: target.write_bytes(b'{"v":1,"tool":"dsync","phase":"\xff"}'),
        "broken_json": lambda: target.write_bytes(good[:-3]),
        "deep_nesting": lambda: target.write_bytes(b"[" * 16000),
        "deep_object": lambda: target.write_bytes(b'{"a":' * 3000 + b"1" + b"}" * 3000),
        "not_object": lambda: target.write_bytes(b"[1, 2]"),
        "v2": lambda: target.write_bytes(json.dumps({**EXAMPLES["workers"], "v": 2}).encode()),
        "bool_v": lambda: target.write_bytes(json.dumps({**EXAMPLES["workers"], "v": True}).encode()),
        "bad_tool": lambda: target.write_bytes(json.dumps({**EXAMPLES["workers"], "tool": "drm"}).encode()),
        "bad_phase": lambda: target.write_bytes(json.dumps({**EXAMPLES["workers"], "phase": "Copy"}).encode()),
        "no_phase": lambda: target.write_bytes(json.dumps({"v": 1, "tool": "dsync"}).encode()),
        "empty": lambda: target.write_bytes(b""),
        "nan": lambda: target.write_bytes(b'{"v": NaN, "tool": "dsync", "phase": "copy"}'),
        "huge_int_text": lambda: target.write_bytes(b'{"v": 1, "tool": "dsync", "phase": "copy", "lines": '
                                                    + b"9" * 5000 + b"}"),
    }


@pytest.mark.parametrize("case", ["symlink", "hardlink", "fifo", "directory", "too_big", "not_utf8", "broken_json",
                                  "deep_nesting", "deep_object", "not_object", "v2", "bool_v", "bad_tool", "bad_phase",
                                  "no_phase", "empty", "nan", "huge_int_text"])
def test_chain_failures_and_bad_shapes_all_fold_into_available_false(env, tmp_path, case):
    client, base, jid = env
    _bad_cases(base, jid, tmp_path)[case]()
    r = _get(client, jid)
    assert (r.status_code, r.json()) == (200, RESPONSES["unavailable"]), case
    _assert_no_store(r)


@pytest.mark.skipif(os.getuid() == 0, reason="root 소유 파일은 사슬이 늘 허용한다(st_uid 0)")
def test_file_owned_by_someone_else_is_available_false(tmp_path):
    base = _base(tmp_path)
    client = _client(tmp_path, base)
    jid = _job(client, uid=os.getuid() + 1)                       # 요청자 uid ≠ 파일 소유자(이 프로세스) ≠ 0
    _place(base, jid, EXAMPLES["workers"])
    _login(client)
    assert _get(client, jid).json() == RESPONSES["unavailable"]


# ---- sanitize ------------------------------------------------------------------------------------------------------------

def _raw(obj):
    return json.dumps(obj).encode()


def test_api_spec_equals_the_contract_descriptors():
    def strip(desc):
        out = {k: v for k, v in desc.items() if k not in ("doc", "tools", "mode")}
        if "values" in out:
            out["values"] = list(out["values"])
        if "fields" in out:
            out["fields"] = {k: strip(v) for k, v in out["fields"].items()}
        return out
    assert {k: strip(v) for k, v in job_progress.FIELDS.items()} == {k: strip(v) for k, v in FIELDS.items()}
    assert job_progress.READ_MAX_BYTES == CONTRACT["api"]["read_max_bytes"]
    assert list(job_progress.PHASES) == CONTRACT["phases_param"]
    assert job_progress.SCHEMA_VERSION == CONTRACT["schema_version"] and job_progress.FILE_NAME == CONTRACT["file_name"]


@pytest.mark.parametrize("name", list(EXAMPLES))
def test_sanitize_is_idempotent_on_every_contract_example(name):
    snap = EXAMPLES[name]
    assert job_progress.sanitize(_raw(snap)) == snap
    assert job_progress.sanitize(_raw(job_progress.sanitize(_raw(snap)))) == snap


def _out_of_range(desc):
    """계약 서술자에서 만든 범위·타입 밖 값들."""
    t = desc["type"]
    bad = [None, "1", [1], {"a": 1}]
    if t == "int":
        bad += [True, 1.5, desc.get("min", 0) - 1, desc.get("max", 2 ** 63 - 1) + 1]
    elif t == "float":
        bad += [True, desc["min"] - 1, desc["max"] * 2 + 1, float("inf")]
    elif t == "bool":
        bad += [0, 1, "true"]
    return bad


def _leaf_paths():
    for name, desc in FIELDS.items():
        if desc.get("required"):
            continue
        if desc["type"] == "obj":
            for sub, sdesc in desc["fields"].items():
                yield (name, sub), sdesc
        else:
            yield (name,), desc


@pytest.mark.parametrize("path,desc", list(_leaf_paths()), ids=[".".join(p) for p, _ in _leaf_paths()])
def test_out_of_range_field_is_dropped_alone(path, desc):
    base = next(s for s in EXAMPLES.values() if (path[0] in s and (len(path) == 1 or path[1] in s[path[0]])))
    for bad in _out_of_range(desc):
        doc = json.loads(json.dumps(base))
        if len(path) == 1:
            doc[path[0]] = bad
        else:
            doc[path[0]][path[1]] = bad
        got = job_progress.sanitize(json.dumps(doc).encode())     # inf 는 JSON 의 Infinity 로 실린다(파이썬 json 이 읽는다)
        want = json.loads(json.dumps(base))
        if len(path) == 1:
            del want[path[0]]
        else:
            del want[path[0]][path[1]]
            if not want[path[0]]:
                del want[path[0]]
        assert got == want, (path, bad)


def test_unknown_keys_and_the_old_line_key_are_dropped():
    snap = {**EXAMPLES["dsync_create"], "line": "[ts] Created 1 items (1%) in 1 secs (1 items/sec) 9 secs left ...",
            "extra": 1, "step": {**EXAMPLES["dsync_create"]["step"], "eta": 9}}
    assert job_progress.sanitize(_raw(snap)) == EXAMPLES["dsync_create"]


def test_nan_and_percent_bounds():
    s = {**EXAMPLES["dsync_copy_overall"]}
    s["overall"] = {"items": 1, "total": 2, "percent": 101}
    raw = json.dumps(s).replace('"elapsed_s": 50.5', '"elapsed_s": NaN').encode()
    got = job_progress.sanitize(raw)
    assert "elapsed_s" not in got and got["overall"] == {"items": 1, "total": 2}


def test_sanitize_rejects_non_bytes_and_oversize():
    assert job_progress.sanitize(None) is None and job_progress.sanitize("{}") is None
    assert job_progress.sanitize(b" " * (job_progress.READ_MAX_BYTES + 1)) is None
    assert job_progress.sanitize(bytearray(_raw(EXAMPLES["workers"]))) == EXAMPLES["workers"]


def test_progress_route_uses_existing_reason_codes_only():
    codes = set(json.loads((ROOT / "frontend" / "src" / "lib" / "reasonCodes.json").read_text(encoding="utf-8")))
    assert {e["body"]["detail"] for e in RESPONSES["errors"]} <= codes
