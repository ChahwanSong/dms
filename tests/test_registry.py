from dms.registry import fetch_repo_tags


def test_tags_are_sorted_and_deterministic(monkeypatch):
    def fake_get_json(url, timeout):
        assert url == "http://pkg-01:5000/v2/dms/tags/list"
        return {"name": "dms", "tags": ["d3", "d1", "d2"]}
    monkeypatch.setattr("dms.registry._get_json", fake_get_json)
    assert fetch_repo_tags("pkg-01:5000", "dms") == ["d1", "d2", "d3"]


def test_failure_returns_none_not_raises(monkeypatch):
    def boom(url, timeout):
        raise OSError("connection refused")
    monkeypatch.setattr("dms.registry._get_json", boom)
    assert fetch_repo_tags("pkg-01:5000", "dms") is None


def test_malformed_body_returns_none(monkeypatch):
    monkeypatch.setattr("dms.registry._get_json", lambda url, timeout: {"tags": None})
    assert fetch_repo_tags("pkg-01:5000", "dms") is None


def test_empty_tag_list_is_not_confused_with_failure(monkeypatch):
    # 레지스트리에 리포는 있으나 태그가 없으면 v2 는 {"tags": null} 또는 []를 준다.
    # []는 "응답했고 태그가 0개"다 -- None(응답 불가)과 반드시 구분돼야 한다.
    # 이 구분이 무너지면 unknown_tag 검증이 조용히 fail-open 이 된다.
    monkeypatch.setattr("dms.registry._get_json",
                        lambda url, timeout: {"name": "dms", "tags": []})
    assert fetch_repo_tags("pkg-01:5000", "dms") == []


def test_request_always_carries_a_timeout(monkeypatch):
    # 폴링 엔드포인트(targets)가 이 함수를 부른다 -- 타임아웃 없이 매달리면 레지스트리
    # 행업 하나가 api 워커 스레드를 계속 물고 있게 된다. 계약으로 고정한다.
    seen = {}

    def capture(url, timeout):
        seen["timeout"] = timeout
        return {"tags": ["d1"]}
    monkeypatch.setattr("dms.registry._get_json", capture)
    fetch_repo_tags("pkg-01:5000", "dms")
    assert seen["timeout"] is not None
