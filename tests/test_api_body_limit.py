"""요청 본문 상한(2026-11-16 입구 교체 -- api/body_limit.py docstring). Envoy BTP requestBuffer 20Mi(넘으면 입구 413)를 앱이 대신한다.

고정하는 것:
  - 경계: Content-Length == 상한은 통과, +1 은 413 {"detail": "request_too_large"} -- 앱을 부르지 않는다(인증·라우팅보다 먼저).
  - chunked(길이 머리 없음): 받은 바이트를 세어 넘는 순간 413. FastAPI 가 본문 읽기 실패를 400 으로 바꿔도 413 으로 갈아 끼운다.
    상한과 꼭 같은 chunked 합계는 통과.
  - 실 기본값(20MiB) 경계와 create_app 배선(가장 바깥 사용자 미들웨어, settings.max_request_body_bytes).
  - k8s 동기화 결과 POST 는 비켜선다(자체 상한 k8s_sync_max_bytes + 1MiB -- 기본 33MiB > 20MiB). 그 라우트는 앱에 실제로 있고,
    비켜서는 범위는 그 라우트 모양 하나다.
  - 사유 코드 추출기가 request_too_large 를 본다(detail= 키워드 리터럴 -- dict 리터럴로 바뀌면 그물 밖이 된다).
  - 순수 ASGI 경계: 예외를 삼키고 200 을 내는 앱도 413, 응답 머리를 먼저 흘린 앱은 바꾸지 않고 예외를 올린다, 숫자 아닌
    Content-Length 는 계수로, 읽지 않은 본문은 세지 않는다, lifespan 은 그대로 지난다, Connection: close 를 붙이지 않는다.
"""
import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dms.api import body_limit
from dms.api.app import create_app
from dms.api.body_limit import RequestBodyLimit
from test_reason_codes_coverage import extract_reason_code_literals

LIMIT = 64
AGENT = {"Authorization": "Bearer tok-shared", "x-dms-actor": "node:w1"}
JSON_CT = {"content-type": "application/json"}


def _json_body(size: int) -> bytes:
    """정확히 size 바이트인 유효한 JSON 객체 -- 본문 파싱이 통과해야 상한 판정만 남는다."""
    body = b'{"pad":"' + b"x" * (size - 10) + b'"}'
    assert len(body) == size
    return body


def _chunks(total: int, step: int = 10):
    def gen():
        left = total
        while left > 0:
            n = min(step, left)
            left -= n
            yield b"x" * n
    return gen()


@pytest.fixture
def small(db, settings):
    return TestClient(create_app(replace(settings, max_request_body_bytes=LIMIT), db))


def _too_large(r):
    return r.status_code == 413 and r.json() == {"detail": "request_too_large"}


# --- create_app 경유 -----------------------------------------------------------------------------

def test_content_length_at_the_limit_passes_and_one_more_byte_is_413(small):
    # /api/agent/report: FastAPI 가 본문을 의존성(require_user)보다 먼저 읽는다 -- 상한 이하면 인증 401 까지 간다.
    r = small.post("/api/agent/report", content=_json_body(LIMIT), headers=JSON_CT)
    assert r.status_code == 401, r.text
    r = small.post("/api/agent/report", content=_json_body(LIMIT + 1), headers=JSON_CT)
    assert _too_large(r), r.text
    assert r.headers["content-type"] == "application/json"


def test_declared_oversize_is_rejected_before_auth_and_routing(small):
    # 관리자 라우트·없는 경로 모두 413 -- 인증(401)·라우팅(404)보다 먼저다(옛 입구 상한과 같은 자리).
    for path in ("/api/admin/batches", "/api/no-such-route"):
        r = small.post(path, content=b"x" * (LIMIT + 1), headers=JSON_CT)
        assert _too_large(r), (path, r.text)


def test_chunked_over_the_limit_is_413_even_though_fastapi_turns_it_into_400(small):
    # 길이 머리 없는 본문: FastAPI 는 본문 읽기 실패를 400 「There was an error parsing the body」로 바꾼다 -- 미들웨어가 그
    # 응답을 413 으로 갈아 끼운다.
    r = small.post("/api/agent/report", content=_chunks(LIMIT + 1), headers=JSON_CT)
    assert _too_large(r), r.text


def test_chunked_at_or_under_the_limit_reaches_the_app(small):
    for total in (LIMIT - 1, LIMIT):
        body = _json_body(total)
        r = small.post("/api/agent/report", content=iter([body[:20], body[20:]]), headers=JSON_CT)
        assert r.status_code == 401, (total, r.text)


def test_bodyless_requests_are_untouched(small):
    assert small.get("/healthz").json() == {"status": "ok"}


def test_k8s_sync_result_bounds_its_own_body(db, settings):
    # 결과 POST 는 상한 = k8s_sync_max_bytes + 1MiB(기본 33MiB) -- 20MiB 일반 상한에 걸리면 큰 인벤토리가 거꾸로 막힌다.
    # 일반 상한(64)을 넘는 본문이 라우트까지 가서 그 라우트의 판정(JSON 아님 → 422 k8s_sync_bad_response)을 받는다.
    c = TestClient(create_app(replace(settings, max_request_body_bytes=LIMIT), db))
    for content in (b"x" * 1000, _chunks(1000, step=100)):
        r = c.post("/api/agent/k8s-sync/runs/r1", content=content, headers={**AGENT, **JSON_CT})
        assert r.status_code == 422 and r.json()["detail"] == "k8s_sync_bad_response", r.text
    # 비켜서는 범위는 그 라우트 모양 하나다 -- 이웃 경로·한 세그먼트 더 깊은 경로는 일반 상한이다.
    for path in ("/api/agent/k8s-sync/claim", "/api/agent/k8s-sync/runs/r1/x", "/api/agent/k8s-sync/runs/",
                 "/api/agent/report"):
        r = c.post(path, content=b"x" * (LIMIT + 1), headers={**AGENT, **JSON_CT})
        assert _too_large(r), (path, r.text)


def test_self_limited_paths_match_real_routes(client):
    # 라우트가 옮겨지거나 이름이 바뀌면 예외 목록이 조용히 빈 자리를 가리킨다 -- 앱의 실제 라우트와 대조한다.
    # (라우트 이름으로 실제 경로를 만든다 -- include_router 한 라우트는 app.routes 를 훑어서는 보이지 않을 수 있다.)
    path = client.app.url_path_for("k8s_sync_result", run_id="abc")
    assert path == "/api/agent/k8s-sync/runs/abc"
    assert body_limit.self_limited(path)
    assert not body_limit.self_limited("/api/agent/k8s-sync/runs/abc/def")
    assert not body_limit.self_limited("/api/agent/k8s-sync/runs/")
    assert not body_limit.self_limited("/x/api/agent/k8s-sync/runs/abc")


def test_create_app_wires_the_setting_as_the_outermost_user_middleware(db, settings):
    app = create_app(settings, db)
    first = app.user_middleware[0]
    assert first.cls is RequestBodyLimit
    assert first.kwargs == {"max_bytes": settings.max_request_body_bytes} == {"max_bytes": 20971520}
    app = create_app(replace(settings, max_request_body_bytes=1048576), db)
    assert app.user_middleware[0].kwargs == {"max_bytes": 1048576}


def test_reason_code_is_visible_to_the_extractor():
    source = (Path(body_limit.__file__)).read_text(encoding="utf-8")
    assert "request_too_large" in extract_reason_code_literals(source)


# --- 순수 ASGI 경계 ------------------------------------------------------------------------------

def _drive(app, *, limit=LIMIT, headers=(), chunks=(b"",), scope_type="http"):
    messages = [{"type": "http.request", "body": c, "more_body": i < len(chunks) - 1} for i, c in enumerate(chunks)]
    sent, calls = [], []

    async def receive():
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    async def inner(scope, receive_, send_):
        calls.append(scope["type"])
        await app(scope, receive_, send_)

    scope = {"type": scope_type, "method": "POST", "path": "/x", "headers": list(headers)}
    asyncio.run(RequestBodyLimit(inner, max_bytes=limit)(scope, receive, send))
    return sent, calls


def _status(sent):
    return sent[0]["status"]


def _json(sent):
    return json.loads(b"".join(m.get("body", b"") for m in sent[1:]))


async def _read_all(receive):
    total = b""
    while True:
        message = await receive()
        total += message.get("body", b"")
        if not message.get("more_body"):
            return total


async def _ok(send, body=b"ok"):
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": body})


async def reads_then_ok(scope, receive, send):
    await _ok(send, await _read_all(receive))


def test_real_default_boundary_on_content_length():
    limit = 20971520   # 기본값(옛 Envoy requestBuffer 20Mi) -- 본문 없이 선언만으로 판정한다
    sent, calls = _drive(reads_then_ok, limit=limit, headers=[(b"content-length", b"20971521")])
    assert calls == [] and _status(sent) == 413 and _json(sent) == {"detail": "request_too_large"}
    sent, calls = _drive(reads_then_ok, limit=limit, headers=[(b"content-length", b"20971520")])
    assert calls == ["http"] and _status(sent) == 200


def test_413_has_json_body_and_no_connection_close():
    sent, _ = _drive(reads_then_ok, headers=[(b"content-length", str(LIMIT + 1).encode())])
    headers = dict(sent[0]["headers"])
    assert headers[b"content-type"] == b"application/json"
    # 닫으면 안 읽은 본문 때문에 RST 가 나가 프록시가 413 대신 502 를 볼 수 있다(모듈 docstring).
    assert b"connection" not in headers


def test_streamed_bytes_are_counted_across_chunks():
    sent, _ = _drive(reads_then_ok, chunks=(b"x" * 40, b"x" * 24))          # 합계 64 == 상한
    assert _status(sent) == 200 and sent[1]["body"] == b"x" * 64
    sent, _ = _drive(reads_then_ok, chunks=(b"x" * 40, b"x" * 24, b"x"))    # 65
    assert _status(sent) == 413 and _json(sent) == {"detail": "request_too_large"}


def test_non_numeric_content_length_falls_back_to_counting():
    sent, calls = _drive(reads_then_ok, headers=[(b"content-length", b"abc")], chunks=(b"x" * (LIMIT + 1),))
    assert calls == ["http"] and _status(sent) == 413


def test_app_that_swallows_the_cut_and_answers_200_still_gets_413():
    seen = []

    async def swallows(scope, receive, send):
        try:
            await _read_all(receive)
        except Exception as e:   # noqa: BLE001 -- 삼키는 앱을 흉내 낸다
            seen.append(type(e).__name__)
        try:
            await receive()      # 다시 읽어도 남은 본문을 주지 않는다
        except Exception as e:   # noqa: BLE001
            seen.append(type(e).__name__)
        await _ok(send)

    sent, _ = _drive(swallows, chunks=(b"x" * LIMIT, b"x"))
    assert seen == ["_BodyTooLarge", "_BodyTooLarge"]
    assert [m["type"] for m in sent] == ["http.response.start", "http.response.body"]
    assert _status(sent) == 413 and _json(sent) == {"detail": "request_too_large"}


def test_app_error_after_the_cut_becomes_413_but_unrelated_errors_propagate():
    async def fails_after_read(scope, receive, send):
        await _read_all(receive)
        raise RuntimeError("boom")

    sent, _ = _drive(fails_after_read, chunks=(b"x" * (LIMIT + 1),))
    assert _status(sent) == 413
    with pytest.raises(RuntimeError):
        _drive(fails_after_read, chunks=(b"x" * LIMIT,))   # 끊지 않았으면 앱 오류는 그대로 올라간다


def test_response_already_started_is_not_rewritten():
    async def streams_first(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await _read_all(receive)

    with pytest.raises(body_limit._BodyTooLarge):
        _drive(streams_first, chunks=(b"x" * (LIMIT + 1),))


def test_unread_body_is_not_counted():
    # 본문을 읽지 않는 라우트는 메모리를 쓰지 않는다 -- 세지 않는다(선언된 Content-Length 초과만 앞에서 막는다).
    async def never_reads(scope, receive, send):
        await _ok(send)

    sent, _ = _drive(never_reads, chunks=(b"x" * (LIMIT * 4),))
    assert _status(sent) == 200


def test_non_http_scopes_pass_through():
    async def lifespan_app(scope, receive, send):
        await send({"type": "lifespan.startup.complete"})

    sent, calls = _drive(lifespan_app, scope_type="lifespan", headers=[(b"content-length", b"999999")])
    assert calls == ["lifespan"] and sent == [{"type": "lifespan.startup.complete"}]
