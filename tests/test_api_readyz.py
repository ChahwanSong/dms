"""슬라이스 22 §2.4: 연속 readyz 실패 자기 종료. exit_fn 주입으로 SIGTERM 없이
검증한다 -- 기본 exit_fn(os.kill SIGTERM)의 실 발화는 실증 §6-3(iptables
REJECT -> RESTARTS +1)이 담당한다. 기각 대안 요약: liveness DB 직결은 90s 발화
+ CrashLoopBackOff 백오프가 DB 복귀 후 회복을 늦추고(replicas 1 이라 백오프
동안 API 0대), 현상 유지는 90분 방치 사건의 재발이다 -- 이미 readiness 503 으로
Service 에서 빠져 있어 종료로 잃는 가용성이 0 이라는 것이 채택 근거다."""
from fastapi.testclient import TestClient

from dms.api.app import create_app
from dms.config import Settings


def _flaky(db, monkeypatch):
    """db.query_one 을 스위치 달린 대역으로 -- readyz 의 SELECT 1 만 조작한다."""
    state = {"fail": False}
    real = db.query_one

    def query_one(sql, params=None):
        if state["fail"]:
            raise RuntimeError("db down")
        return real(sql, params)

    monkeypatch.setattr(db, "query_one", query_one)
    return state


def _client(db, exit_calls, threshold):
    settings = Settings(database_url="unused", shared_token="tok-shared",
                        admin_token="tok-admin", session_secret="sess-secret",
                        readyz_exit_failures=threshold)
    return TestClient(create_app(settings, db,
                                 exit_fn=lambda: exit_calls.append(1)))


def test_consecutive_failures_reach_the_limit_and_self_terminate(db, monkeypatch, capsys):
    calls = []
    state = _flaky(db, monkeypatch)
    client = _client(db, calls, threshold=3)
    state["fail"] = True
    codes = [client.get("/readyz").status_code for _ in range(3)]
    assert codes == [503, 503, 503]   # 상태 코드·503 본문은 기존 그대로(프로브 계약)
    assert calls == [1]               # 정확히 임계 도달 시 1회
    assert "self-terminating" in capsys.readouterr().err   # 종료 사유가 로그에 남는다


def test_a_success_resets_the_counter(db, monkeypatch):
    # 리셋이 없으면 "가끔 한 번씩 실패"가 몇 시간에 걸쳐 누적돼 멀쩡한 파드를
    # 죽인다 -- 임계는 어디까지나 **연속** 실패다(§2.4).
    calls = []
    state = _flaky(db, monkeypatch)
    client = _client(db, calls, threshold=3)
    state["fail"] = True
    client.get("/readyz")
    client.get("/readyz")                             # 연속 2 (임계 3 미만)
    state["fail"] = False
    assert client.get("/readyz").status_code == 200   # 성공 -> 카운터 0
    state["fail"] = True
    client.get("/readyz")
    client.get("/readyz")                             # 다시 연속 2
    assert calls == []   # 리셋이 없었다면 누적 4·5번째에서 이미 발화했다


def test_zero_disables_self_termination(db, monkeypatch):
    # DMS_READYZ_EXIT_FAILURES=0 은 명시적 비활성(§2.4) -- 운영자가 장치를 끄고
    # 관찰만 하고 싶을 때의 탈출구다.
    calls = []
    state = _flaky(db, monkeypatch)
    client = _client(db, calls, threshold=0)
    state["fail"] = True
    for _ in range(10):
        assert client.get("/readyz").status_code == 503
    assert calls == []
