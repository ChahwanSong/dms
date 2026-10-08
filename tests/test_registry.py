import httpx
import pytest

from dms import registry
from dms.registry import delete_manifest, fetch_repo_tags, manifest_digest

V2 = {"Docker-Distribution-Api-Version": "registry/2.0"}


def _resp(status, *, json=None, headers=None, text=None, method="GET", url="http://pkg-01:5000/"):
    req = httpx.Request(method, url)
    if json is not None:
        return httpx.Response(status, json=json, headers=headers or {}, request=req)
    return httpx.Response(status, text=text or "", headers=headers or {}, request=req)


def _serve(monkeypatch, handler):
    """registry._request 심을 바꿔 끼우고 받은 호출을 기록한다."""
    calls = []

    def fake(method, url, headers=None):
        calls.append((method, url, dict(headers or {})))
        return handler(method, url, headers or {})
    monkeypatch.setattr("dms.registry._request", fake)
    return calls


def test_tags_are_sorted_and_deterministic(monkeypatch):
    calls = _serve(monkeypatch, lambda m, u, h: _resp(200, json={"name": "dms", "tags": ["d3", "d1", "d2"]}))
    assert fetch_repo_tags("pkg-01:5000", "dms") == ["d1", "d2", "d3"]
    assert calls[0][:2] == ("GET", "http://pkg-01:5000/v2/dms/tags/list")


def test_failure_returns_none_not_raises(monkeypatch):
    def boom(m, u, h):
        raise OSError("connection refused")
    _serve(monkeypatch, boom)
    assert fetch_repo_tags("pkg-01:5000", "dms") is None


def test_malformed_body_returns_none(monkeypatch):
    # tags 키 자체가 없거나 목록이 아니면 형식 불량 -- 응답 불가(None)로 접는다.
    _serve(monkeypatch, lambda m, u, h: _resp(200, json={"name": "dms"}))
    assert fetch_repo_tags("pkg-01:5000", "dms") is None
    _serve(monkeypatch, lambda m, u, h: _resp(200, json={"name": "dms", "tags": "d1"}))
    assert fetch_repo_tags("pkg-01:5000", "dms") is None
    _serve(monkeypatch, lambda m, u, h: _resp(200, text="<html>not json</html>"))
    assert fetch_repo_tags("pkg-01:5000", "dms") is None


def test_tags_null_is_an_empty_list(monkeypatch):
    # 2026-10-08 리뷰 R10: v2 는 태그를 전부 지운 리포에 {"tags": null} 을 준다 -- 레지스트리가
    # 답했고 태그가 0개다. 예전엔 None(연결 불가)으로 접어 화면 전체에 빨간 문구가 떴다.
    _serve(monkeypatch, lambda m, u, h: _resp(200, json={"name": "dms", "tags": None}))
    out = fetch_repo_tags("pkg-01:5000", "dms")
    assert out == [] and out is not None and out.truncated is False


def test_empty_tag_list_is_not_confused_with_failure(monkeypatch):
    # []는 "응답했고 태그가 0개"다 -- None(응답 불가)과 반드시 구분돼야 한다.
    # 이 구분이 무너지면 unknown_tag 검증이 조용히 fail-open 이 된다.
    _serve(monkeypatch, lambda m, u, h: _resp(200, json={"name": "dms", "tags": []}))
    assert fetch_repo_tags("pkg-01:5000", "dms") == []


def test_request_always_carries_a_timeout(monkeypatch):
    # 폴링 엔드포인트(targets)가 이 함수를 부른다 -- 타임아웃 없이 매달리면 레지스트리
    # 행업 하나가 api 워커 스레드를 계속 물고 있게 된다. 계약으로 고정한다.
    # "is not None"만 보면 httpx.Timeout(None)(= 무제한)도 통과한다 -- 값 자체를 못박는다.
    seen = {}

    def capture(method, url, headers=None, timeout=None):
        seen["timeout"] = timeout
        return _resp(200, json={"tags": ["d1"]})
    monkeypatch.setattr("dms.registry.httpx.request", capture)
    fetch_repo_tags("pkg-01:5000", "dms")
    timeout = seen["timeout"]
    assert (timeout.connect, timeout.read, timeout.write, timeout.pool) == (2.0, 3.0, 3.0, 3.0)


def test_missing_repository_404_is_an_empty_list_not_unreachable(monkeypatch):
    # 신규 사이트: 레지스트리는 살아 있고 리포만 아직 없다(push 전). 포탈이 "연결
    # 불가"라고 말하면 안 된다 -- 도달됐고 태그 0개다(None ≠ []). 레지스트리는 오류에도
    # v2 헤더를 싣는다(distribution) -- 헤더 없이 NAME_UNKNOWN 본문만 있어도 레지스트리다.
    _serve(monkeypatch, lambda m, u, h: _resp(404, headers=V2, json={"errors": [{"code": "NAME_UNKNOWN"}]}))
    assert fetch_repo_tags("reg.example:5000", "dms") == []
    _serve(monkeypatch, lambda m, u, h: _resp(404, json={"errors": [{"code": "NAME_UNKNOWN", "message": "x"}]}))
    assert fetch_repo_tags("reg.example:5000", "dms") == []
    _serve(monkeypatch, lambda m, u, h: _resp(404, headers=V2))
    assert fetch_repo_tags("reg.example:5000", "dms") == []


def test_non_registry_404_is_unreachable_not_empty(monkeypatch):
    # 2026-10-08 리뷰 R2: 레지스트리가 아닌 서버(웹서버·ingress 기본 백엔드)의 404 를 []로
    # 접으면 경고 없이 빈 목록이 되고 실제로 있는 태그 제출이 unknown_tag 로 막힌다.
    _serve(monkeypatch, lambda m, u, h: _resp(404, text="<html><h1>Not Found</h1></html>"))
    assert fetch_repo_tags("reg.example", "dms") is None
    _serve(monkeypatch, lambda m, u, h: _resp(404, json={"detail": "Not Found"}))
    assert fetch_repo_tags("reg.example", "dms") is None


def test_other_http_errors_are_still_unreachable(monkeypatch):
    _serve(monkeypatch, lambda m, u, h: _resp(500, headers=V2))
    assert fetch_repo_tags("reg.example:5000", "dms") is None


def test_pagination_follows_link_next_on_the_same_registry(monkeypatch):
    # 2026-10-08 리뷰 R5: 페이지를 나눠 주는 레지스트리에서 첫 페이지만 읽으면 새 태그가
    # 뒤 페이지로 밀려 조용히 빠진다.
    pages = {
        "http://reg:5000/v2/dms/tags/list": (["d1", "d2"], '</v2/dms/tags/list?n=2&last=d2>; rel="next"'),
        "http://reg:5000/v2/dms/tags/list?n=2&last=d2": (["d3", "d4"], '<http://reg:5000/v2/dms/tags/list?n=2&last=d4>; rel="next"'),
        "http://reg:5000/v2/dms/tags/list?n=2&last=d4": (["d5"], None),
    }

    def handler(m, u, h):
        tags, link = pages[u]
        return _resp(200, json={"name": "dms", "tags": tags}, headers={"Link": link} if link else {})
    calls = _serve(monkeypatch, handler)
    out = fetch_repo_tags("reg:5000", "dms")
    assert out == ["d1", "d2", "d3", "d4", "d5"] and out.truncated is False
    assert len(calls) == 3


def test_pagination_cap_and_off_registry_next_mark_truncated(monkeypatch):
    # 끝없이 따라가지 않는다(폴링 화면) -- 상한에 걸리면 그때까지의 목록 + truncated.
    def endless(m, u, h):
        n = int(u.rsplit("=", 1)[1]) if "last=" in u else 0
        return _resp(200, json={"tags": [f"t{n}"]}, headers={"Link": f'</v2/dms/tags/list?last={n + 1}>; rel="next"'})
    calls = _serve(monkeypatch, endless)
    out = fetch_repo_tags("reg:5000", "dms")
    assert out.truncated is True and len(out) == registry._MAX_PAGES and len(calls) == registry._MAX_PAGES
    # 다른 호스트로 보내는 다음 주소는 따라가지 않는다(요청 위조 방지) -- 잘림으로 본다.
    calls = _serve(monkeypatch, lambda m, u, h: _resp(
        200, json={"tags": ["d1"]}, headers={"Link": '<http://evil.example/v2/dms/tags/list?last=d1>; rel="next"'}))
    out = fetch_repo_tags("reg:5000", "dms")
    assert out == ["d1"] and out.truncated is True and len(calls) == 1


def test_bearer_401_retries_once_with_an_anonymous_token(monkeypatch):
    # 2026-10-08 리뷰 R8: 토큰 인증 레지스트리(익명 조회 허용)는 401 + Bearer 챌린지를 준다.
    challenge = 'Bearer realm="https://registry.example:8443/auth/token",service="registry.example",scope="repository:dms:pull"'

    def handler(m, u, h):
        if "/auth/token" in u:
            assert "Authorization" not in h          # 자격 증명 없이(익명)
            return _resp(200, json={"token": "anon-tok"})
        if h.get("Authorization") == "Bearer anon-tok":
            return _resp(200, json={"name": "dms", "tags": ["d2", "d1"]})
        return _resp(401, headers={"WWW-Authenticate": challenge})
    calls = _serve(monkeypatch, handler)
    assert fetch_repo_tags("registry.example", "dms") == ["d1", "d2"]
    token_url = calls[1][1]
    assert token_url.startswith("https://registry.example:8443/auth/token?")
    assert "service=registry.example" in token_url and "scope=repository%3Adms%3Apull" in token_url


def test_bearer_token_failure_or_basic_challenge_is_unreachable(monkeypatch, caplog):
    def denied(m, u, h):
        if "/auth/" in u:
            return _resp(401, json={"errors": [{"code": "UNAUTHORIZED"}]})
        return _resp(401, headers={"WWW-Authenticate": 'Bearer realm="https://registry.example:8443/auth/token"'})
    _serve(monkeypatch, denied)
    assert fetch_repo_tags("registry.example", "dms") is None
    # Basic 은 자격 증명이 필요하다 -- 토큰을 받으러 가지 않는다.
    calls = _serve(monkeypatch, lambda m, u, h: _resp(401, headers={"WWW-Authenticate": 'Basic realm="r"'}))
    assert fetch_repo_tags("registry.example", "dms") is None and len(calls) == 1
    # realm 이 http(s) 가 아니면 따라가지 않는다
    calls = _serve(monkeypatch, lambda m, u, h: _resp(401, headers={"WWW-Authenticate": 'Bearer realm="file:///etc/passwd"'}))
    assert fetch_repo_tags("registry.example", "dms") is None and len(calls) == 1


def test_token_is_never_logged(monkeypatch, caplog):
    def handler(m, u, h):
        if "/auth/" in u:
            return _resp(200, json={"token": "SECRET-TOKEN-VALUE"})
        return _resp(500 if h.get("Authorization") else 401,
                     headers={"WWW-Authenticate": 'Bearer realm="https://registry.example:8443/auth/t"'})
    _serve(monkeypatch, handler)
    with caplog.at_level("WARNING"):
        assert fetch_repo_tags("registry.example", "dms") is None
    assert "SECRET-TOKEN-VALUE" not in caplog.text


@pytest.mark.parametrize("fn", ["digest", "delete"])
def test_manifest_calls_also_use_the_anonymous_token(monkeypatch, fn):
    def handler(m, u, h):
        if "/auth/" in u:
            return _resp(200, json={"access_token": "t2"})
        if h.get("Authorization") != "Bearer t2":
            return _resp(401, headers={"WWW-Authenticate": 'Bearer realm="https://registry.example:8443/auth/t"'}, method=m)
        if m == "HEAD":
            return _resp(200, headers={"Docker-Content-Digest": "sha256:abc"}, method=m)
        return _resp(202, method=m)
    _serve(monkeypatch, handler)
    if fn == "digest":
        assert manifest_digest("registry.example", "dms", "d1") == "sha256:abc"
    else:
        assert delete_manifest("registry.example", "dms", "sha256:abc") == "ok"


# ---- 2026-10-08 검증 후속: 페이지 루프 상한·토큰 재사용·챌린지·깨진 Link ----

def test_next_link_loop_stops_and_marks_truncated(monkeypatch):
    # 자기 자신(또는 빈 <>)을 가리키는 next 는 같은 주소를 50번 다시 부르지 않는다.
    for link in ('</v2/dms/tags/list>; rel="next"', '<>; rel="next"'):
        calls = _serve(monkeypatch, lambda m, u, h, link=link: _resp(200, json={"tags": ["d1"]}, headers={"Link": link}))
        out = fetch_repo_tags("reg:5000", "dms")
        assert out == ["d1"] and out.truncated is True and len(calls) == 1, link


def test_page_loop_has_an_overall_time_budget(monkeypatch):
    # 요청마다 타임아웃이 있어도 페이지 수만큼 곱해지면 api 워커를 분 단위로 문다 -- 둘째 페이지부터
    # 전체 예산을 넘기면 그때까지의 목록을 잘림으로 돌려준다.
    clock = {"t": 0.0}
    monkeypatch.setattr("dms.registry.time.monotonic", lambda: clock["t"])

    def slow(m, u, h):
        clock["t"] += 3.0
        n = int(u.rsplit("=", 1)[1]) if "last=" in u else 0
        return _resp(200, json={"tags": [f"t{n}"]}, headers={"Link": f'</v2/dms/tags/list?last={n + 1}>; rel="next"'})
    calls = _serve(monkeypatch, slow)
    out = fetch_repo_tags("reg:5000", "dms")
    assert out.truncated is True and len(calls) == 3      # 0s→3s→6s→9s(예산 8s 초과) 에서 멈춤


def test_bearer_token_is_reused_across_pages(monkeypatch):
    issued = []

    def handler(m, u, h):
        if "/auth/" in u:
            issued.append(u)
            return _resp(200, json={"token": "t1"})
        if h.get("Authorization") != "Bearer t1":
            return _resp(401, headers={"WWW-Authenticate": 'Bearer realm="http://reg:6000/auth/t",service="r"'})
        if "last=" in u:
            return _resp(200, json={"tags": ["d2"]})
        return _resp(200, json={"tags": ["d1"]}, headers={"Link": '</v2/dms/tags/list?last=d1>; rel="next"'})
    calls = _serve(monkeypatch, handler)
    assert fetch_repo_tags("reg:5000", "dms") == ["d1", "d2"]
    assert len(issued) == 1                                  # 토큰은 한 번만 받는다
    assert [c[2].get("Authorization") for c in calls if "/auth/" not in c[1]] == [None, "Bearer t1", "Bearer t1"]


def test_realm_query_is_kept_and_challenge_parsing_is_tolerant(monkeypatch):
    def run(challenge):
        seen = []

        def handler(m, u, h):
            if "/auth/" in u:
                seen.append(u)
                return _resp(200, json={"access_token": "tok"})
            if h.get("Authorization") == "Bearer tok":
                return _resp(200, json={"tags": ["d1"]})
            return _resp(401, headers={"WWW-Authenticate": challenge})
        _serve(monkeypatch, handler)
        return fetch_repo_tags("reg", "dms"), seen
    out, seen = run('Bearer realm="http://reg:6000/auth/token?account=svc",service="reg",scope="repository:dms:pull"')
    assert out == ["d1"] and "account=svc" in seen[0] and "service=reg" in seen[0]
    for ch in ('Basic realm="x", Bearer realm="http://reg:6000/auth/t"',      # 두 챌린지가 한 헤더로
               'Bearer realm=http://reg:6000/auth/t,service=reg',             # 따옴표 없는 값
               'Bearer Realm="http://reg:6000/auth/t"',                       # 대문자 이름
               'bearer\trealm="http://reg:6000/auth/t"'):                     # 탭
        assert run(ch)[0] == ["d1"], ch


def test_unsafe_token_is_dropped_and_never_logged(monkeypatch, caplog):
    def handler(m, u, h):
        if "/auth/" in u:
            return _resp(200, json={"token": "bad\r\nX-Injected: 1"})
        return _resp(401, headers={"WWW-Authenticate": 'Bearer realm="http://reg:6000/auth/t"'})
    calls = _serve(monkeypatch, handler)
    with caplog.at_level("WARNING"):
        assert fetch_repo_tags("reg", "dms") is None
    assert "X-Injected" not in caplog.text and len(calls) == 2   # 재시도하지 않는다


def test_unreadable_next_link_and_later_page_failure_keep_what_was_read(monkeypatch):
    # 「next」를 말하는데 읽을 수 없는 Link 는 "목록 끝"이 아니라 잘림(뒤 페이지 태그를 잘못 막지 않게).
    for link in ('rel="next"', '<http://[::1>; rel="next"'):
        _serve(monkeypatch, lambda m, u, h, link=link: _resp(200, json={"tags": ["a"]}, headers={"Link": link}))
        out = fetch_repo_tags("reg:5000", "dms")
        assert out == ["a"] and out.truncated is True, link
    # title 에 쉼표가 있어도 next 를 찾는다
    pages = {"http://reg:5000/v2/dms/tags/list": (["a"], '</v2/dms/tags/list?last=a>; title="x, y"; rel="next"'),
             "http://reg:5000/v2/dms/tags/list?last=a": (["b"], None)}
    _serve(monkeypatch, lambda m, u, h: _resp(200, json={"tags": pages[u][0]},
                                              headers={"Link": pages[u][1]} if pages[u][1] else {}))
    out = fetch_repo_tags("reg:5000", "dms")
    assert out == ["a", "b"] and out.truncated is False

    # 둘째 페이지 실패(연결·404·형식)는 첫 페이지를 버리지 않는다
    def second_fails(m, u, h):
        if "last=" in u:
            raise OSError("reset")
        return _resp(200, json={"tags": ["a"]}, headers={"Link": '</v2/dms/tags/list?last=a>; rel="next"'})
    _serve(monkeypatch, second_fails)
    out = fetch_repo_tags("reg:5000", "dms")
    assert out == ["a"] and out.truncated is True



def test_token_realm_on_another_host_is_refused(monkeypatch, caplog):
    # 레지스트리 응답이 정한 주소로 api 파드가 요청을 보낸다 -- 평문 HTTP 중간자·이상한 레지스트리가 realm 을
    # 내부 주소(메타데이터 등)로 바꿔 요청 위조(SSRF)에 쓰지 못하게, 레지스트리와 같은 호스트의 realm 만 따른다.
    for realm in ("http://169.254.169.254/latest/meta-data", "http://auth.example/token", "http://reg.evil/token"):
        calls = _serve(monkeypatch, lambda m, u, h, realm=realm: _resp(
            401, headers={"WWW-Authenticate": f'Bearer realm="{realm}",service="reg"'}))
        with caplog.at_level("WARNING"):
            assert fetch_repo_tags("reg:5000", "dms") is None
        assert len(calls) == 1, realm                     # realm 으로 요청을 보내지 않는다
    assert "realm on another host refused" in caplog.text
