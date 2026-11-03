"""로그인 async 경로의 동시성(2026-11-03, routes_auth.login · api/login_guard.py) -- 한 이벤트 루프에서 여러 요청을 동시에
던진다(httpx.AsyncClient + ASGITransport). TestClient 는 요청마다 새 루프라 「슬롯을 이벤트 루프에서 기다린다」를 못 본다.

검증(authenticate)은 threading.Event 로 막는 가짜로 바꾼다 -- 실제 scrypt 대신 「슬롯을 쥔 채 오래 걸리는 검증」을 만든다.
가짜는 스레드에서 돈다(anyio.to_thread.run_sync) -- 이벤트 루프는 막히지 않아야 한다(/healthz 가 바로 답한다)."""
import asyncio
import threading
import time
from dataclasses import replace

import httpx
import pytest

from dms.api.app import create_app
from dms.db import load_json


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FakeAuth:
    """authenticate 대역: blocked 아이디는 release 될 때까지 막고, 비밀번호 "pw" 만 맞다."""

    def __init__(self, blocked=(), delay=None):
        self.blocked = set(blocked)
        self.delay = delay or {}
        self.release = threading.Event()
        self.entered: list[str] = []

    def __call__(self, username, password):
        self.entered.append(username)
        if username in self.blocked:
            self.release.wait(10)
        if username in self.delay:
            time.sleep(self.delay[username])
        return {"role": "user", "session_epoch": None} if password == "pw" else None


def _app(db, settings, fake, **over):
    app = create_app(replace(settings, **over), db)
    app.state.repos.accounts.authenticate = fake
    return app


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _login(c, username, password="pw"):
    return await c.post("/api/auth/login", json={"username": username, "password": password})


async def _wait_for(pred, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not pred():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.01)


def _events(db, event_type):
    return [{**r, "payload": load_json(r["payload"])}
            for r in db.query("SELECT message, payload FROM events WHERE event_type = :t ORDER BY id",
                              {"t": event_type})]


@pytest.mark.anyio
async def test_healthz_answers_while_both_slots_are_held(db, settings):
    fake = FakeAuth(blocked={"slow1", "slow2"})
    app = _app(db, settings, fake)
    async with _client(app) as c:
        t1 = asyncio.create_task(_login(c, "slow1"))
        t2 = asyncio.create_task(_login(c, "slow2"))
        await _wait_for(lambda: len(fake.entered) == 2)
        assert app.state.login_gate.snapshot() == {"in_use": 2, "waiting": 0}
        start = time.monotonic()
        r = await c.get("/healthz")
        assert r.status_code == 200 and r.json() == {"status": "ok"}
        assert time.monotonic() - start < 0.5                # 이벤트 루프가 막히지 않았다
        fake.release.set()
        assert [(await t).status_code for t in (t1, t2)] == [200, 200]
    assert app.state.login_gate.snapshot() == {"in_use": 0, "waiting": 0}


@pytest.mark.anyio
async def test_slot_wait_over_the_cap_is_503_busy_and_not_a_failure(db, settings):
    fake = FakeAuth(blocked={"hold1", "hold2"})
    app = _app(db, settings, fake, login_scrypt_max_wait_seconds=1)
    async with _client(app) as c:
        holders = [asyncio.create_task(_login(c, u)) for u in ("hold1", "hold2")]
        await _wait_for(lambda: len(fake.entered) == 2)
        start = time.monotonic()
        r = await _login(c, "alice", "wrong")
        elapsed = time.monotonic() - start
        assert (r.status_code, r.json()["detail"]) == (503, "login_busy")
        assert r.headers["retry-after"] == "1"
        assert 0.9 <= elapsed < 3
        r2 = await _login(c, "bob", "wrong")                 # 두 번째 바쁨은 이벤트를 늘리지 않는다(창당 1회)
        assert r2.status_code == 503
        fake.release.set()
        for t in holders:
            await t
    assert app.state.login_limiter.failures("user:alice") == 0          # 실패로 세지 않는다
    assert "alice" not in fake.entered                                   # 검증까지 가지 않았다
    ev = _events(db, "login_busy")
    assert len(ev) == 1 and ev[0]["payload"]["cause"] == "slots"
    assert ev[0]["payload"]["retry_after_seconds"] == 1
    assert app.state.login_gate.snapshot() == {"in_use": 0, "waiting": 0}


@pytest.mark.anyio
async def test_a_normal_login_waits_its_turn_behind_attack_requests(db, settings):
    fake = FakeAuth(delay={"att": 0.2})
    app = _app(db, settings, fake)
    async with _client(app) as c:
        attackers = [asyncio.create_task(_login(c, "att", "wrong")) for _ in range(4)]
        await _wait_for(lambda: len(fake.entered) >= 2)
        r = await _login(c, "alice")
        assert r.status_code == 200
        assert [(await t).status_code for t in attackers] == [401] * 4


@pytest.mark.anyio
async def test_pacing_off_never_waits_or_503s(db, settings):
    fake = FakeAuth()
    app = _app(db, settings, fake, login_global_pace_limit=0)
    assert app.state.login_pacer.enabled is False
    async with _client(app) as c:
        start = time.monotonic()
        codes = [(await _login(c, f"u{i}", "wrong")).status_code for i in range(8)]
        assert codes == [401] * 8
        assert time.monotonic() - start < 2
    assert _events(db, "login_pacing_engaged") == [] and _events(db, "login_busy") == []


@pytest.mark.anyio
async def test_pacing_on_waits_then_503s_when_the_queue_is_too_long(db, settings):
    fake = FakeAuth()
    app = _app(db, settings, fake, login_global_pace_limit=2, login_global_pace_window_seconds=2,
               login_global_pace_max_wait_seconds=1)
    async with _client(app) as c:
        assert (await _login(c, "a1", "wrong")).status_code == 401      # 버스트 L=2
        assert (await _login(c, "a2", "wrong")).status_code == 401
        start = time.monotonic()
        third = asyncio.create_task(_login(c, "a3", "wrong"))           # 빚 1 → 약 1초 대기 뒤 진행
        await _wait_for(lambda: app.state.login_pacer.snapshot()["waiting"] == 1)
        fourth = await _login(c, "a4", "wrong")                         # 빚 2 → 예상 2초 > M(1) → 즉시 503
        assert (fourth.status_code, fourth.json()["detail"]) == (503, "login_busy")
        assert int(fourth.headers["retry-after"]) >= 1
        assert time.monotonic() - start < 0.8
        r3 = await third
        assert r3.status_code == 401
        # 약 1초(빚 1 ÷ 초당 1개) -- 앞 두 요청 사이에 다시 찬 몫만큼 짧아질 수 있어 여유를 둔다
        assert time.monotonic() - start >= 0.6
    engaged = _events(db, "login_pacing_engaged")
    assert len(engaged) == 1
    assert (engaged[0]["payload"]["limit"], engaged[0]["payload"]["window_seconds"]) == (2, 2)
    busy = _events(db, "login_busy")
    assert len(busy) == 1 and busy[0]["payload"]["cause"] == "pacing"
    assert "a4" not in fake.entered                                     # 503 은 검증까지 가지 않는다


@pytest.mark.anyio
async def test_success_refunds_the_pacing_token_and_failure_keeps_it_spent(db, settings):
    fake = FakeAuth()
    app = _app(db, settings, fake, login_global_pace_limit=2, login_global_pace_window_seconds=60)
    pacer = app.state.login_pacer
    async with _client(app) as c:
        assert (await _login(c, "alice", "wrong")).status_code == 401
        assert pacer.snapshot()["tokens"] == pytest.approx(1.0, abs=0.1)   # 401 은 쓴 채로
        assert (await _login(c, "alice")).status_code == 200
        assert pacer.snapshot()["tokens"] == pytest.approx(1.0, abs=0.1)   # 성공은 환불(원복)


@pytest.mark.anyio
async def test_recheck_inside_the_slot_catches_failures_that_piled_up_while_waiting(db, settings):
    fake = FakeAuth(blocked={"holder"})
    app = _app(db, settings, fake, login_scrypt_slots=1)
    async with _client(app) as c:
        holder = asyncio.create_task(_login(c, "holder"))
        await _wait_for(lambda: fake.entered == ["holder"])
        victim = asyncio.create_task(_login(c, "alice"))
        await _wait_for(lambda: app.state.login_gate.snapshot()["waiting"] == 1)
        for _ in range(10):                                  # 기다리는 동안 다른 시도가 alice 의 실패 상한을 채웠다
            app.state.login_limiter.record_failure("user:alice")
        fake.release.set()
        r = await victim
        assert (r.status_code, r.json()["detail"]) == (429, "login_rate_limited")
        await holder
    assert "alice" not in fake.entered                       # 재확인에서 막혀 검증하지 않았다


@pytest.mark.anyio
async def test_id_axis_429_is_immediate_even_when_pacing_is_on(db, settings):
    fake = FakeAuth()
    app = _app(db, settings, fake, login_global_pace_limit=1, login_global_pace_window_seconds=60,
               login_global_pace_max_wait_seconds=15)
    pacer = app.state.login_pacer
    for _ in range(10):
        app.state.login_limiter.record_failure("user:alice")
    async with _client(app) as c:
        before = pacer.snapshot()["tokens"]
        start = time.monotonic()
        r = await _login(c, "alice")
        assert (r.status_code, r.json()["detail"]) == (429, "login_rate_limited")
        assert time.monotonic() - start < 0.5
        assert pacer.snapshot()["tokens"] == pytest.approx(before, abs=0.05)   # 토큰을 쓰지 않았다
    assert fake.entered == []


@pytest.mark.anyio
async def test_recheck_429_inside_the_slot_refunds_the_pacing_token(db, settings):
    # 2026-11-03 리뷰: 「401 외 결과는 환불」 중 성공만 단언했다 -- 재확인 429 는 스위치를 끈 앱에서만 돌아 환불을 아무도 보지
    # 않았다. 창이 길어(600초) 다시 차는 몫은 무시할 만하다: 2.0 = 처음 3 - 401(쓴 채로) 1, holder 성공·victim 429 는 환불.
    fake = FakeAuth(blocked={"holder"})
    app = _app(db, settings, fake, login_scrypt_slots=1, login_global_pace_limit=3,
               login_global_pace_window_seconds=600, login_global_pace_max_wait_seconds=15)
    pacer = app.state.login_pacer
    async with _client(app) as c:
        assert (await _login(c, "x", "wrong")).status_code == 401
        assert pacer.snapshot()["tokens"] == pytest.approx(2.0, abs=0.05)
        holder = asyncio.create_task(_login(c, "holder"))
        await _wait_for(lambda: fake.entered == ["x", "holder"])
        victim = asyncio.create_task(_login(c, "alice"))
        await _wait_for(lambda: app.state.login_gate.snapshot()["waiting"] == 1)
        assert pacer.snapshot()["tokens"] == pytest.approx(0.0, abs=0.05)     # 둘 다 예약했다(대기 없음 -- 잔고 0 까지)
        for _ in range(10):
            app.state.login_limiter.record_failure("user:alice")
        fake.release.set()
        r = await victim
        assert (r.status_code, r.json()["detail"]) == (429, "login_rate_limited")
        assert (await holder).status_code == 200
        assert pacer.snapshot()["tokens"] == pytest.approx(2.0, abs=0.05)     # 429·성공 둘 다 돌려받았다
    assert "alice" not in fake.entered


@pytest.mark.anyio
async def test_slot_503_refunds_the_pacing_token(db, settings):
    # 같은 리뷰: 슬롯 503(LoginBusy)도 판정이 아니라 용량 신호다 -- 줄 토큰을 쓴 채로 두면 바쁨이 전체 속도를 깎는다.
    fake = FakeAuth(blocked={"hold1", "hold2"})
    app = _app(db, settings, fake, login_scrypt_max_wait_seconds=1, login_global_pace_limit=4,
               login_global_pace_window_seconds=600, login_global_pace_max_wait_seconds=15)
    pacer = app.state.login_pacer
    async with _client(app) as c:
        assert (await _login(c, "x", "wrong")).status_code == 401                # 4 → 3(쓴 채로)
        holders = [asyncio.create_task(_login(c, u)) for u in ("hold1", "hold2")]
        await _wait_for(lambda: {"hold1", "hold2"} <= set(fake.entered))
        assert pacer.snapshot()["tokens"] == pytest.approx(1.0, abs=0.05)
        r = await _login(c, "alice", "wrong")                                  # 1 → 0 예약, 슬롯 1초 대기 뒤 503
        assert (r.status_code, r.json()["detail"]) == (503, "login_busy")
        assert pacer.snapshot()["tokens"] == pytest.approx(1.0, abs=0.05)     # 503 은 돌려받았다
        fake.release.set()
        assert [(await t).status_code for t in holders] == [200, 200]
        assert pacer.snapshot()["tokens"] == pytest.approx(3.0, abs=0.05)
    assert "alice" not in fake.entered
