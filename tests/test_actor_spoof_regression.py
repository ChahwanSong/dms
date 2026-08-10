"""공유 토큰 actor 스푸핑 회귀(슬라이스 19 설계 §1-4, §2.2).

두 반쪽을 각각 못박는다:
1) 결과가 실재한다 -- privileged_requesters 이름은 resolve_job_identity 에서 uid 0
   으로 승격된다(이 테스트는 게이트 전후로 계속 통과 -- 승격 메커니즘 자체는
   바뀌지 않는다. "페이로드가 진짜다"의 증거).
2) 공유 토큰 보유자가 그 이름을 스스로 고를 수 있다 -- 게이트 전에는 POST 가 202 로
   통과해 requester_id="root" 인 요청이 만들어진다(구멍이 살아 있다). 게이트 후에는
   400 invalid_actor 로 막히고 요청 자체가 만들어지지 않는다.

슬라이스 17 의 교훈: 테스트가 무언가를 붙잡는다는 주장은 실제로 깨뜨려 RED 를 볼
때까지 추측이다. 그래서 (2)의 RED(assert 202 == 400)가 스푸핑이 오늘 열려 있음을
증명한 다음에야 게이트를 신뢰한다."""
from fastapi.testclient import TestClient

import dms.api.auth as auth_mod
import dms.api.routes_agent as agent_mod
from dms.config import Settings
from dms.identity import resolve_job_identity
from dms.repositories.control import ControlRepository


TOKEN_ROOT = {"Authorization": "Bearer tok-shared", "x-dms-actor": "root"}
RM = {"operation": "rm", "storage": "s1", "target": "a",
      "options": {"recursive": True}}


def _client(db):
    settings = Settings(database_url="unused", shared_token="tok-shared",
                        admin_token="tok-admin", session_secret="sess")
    from dms.api.app import create_app
    return TestClient(create_app(settings, db))


def test_privileged_requester_synthesizes_root_uid_zero(db):
    # (1) 승격은 실재한다: root 는 privileged_requesters 기본값 멤버(config.py:114)이고
    # group deny 가 없으면 LDAP 없이 uid 0 으로 합성된다(identity.py:49,:62-63).
    control = ControlRepository(db)
    # session_authenticated=True 는 "세션 로그인한 관리자가 root 를 requester 로
    # 쓰는" 정상 경로를 뜻한다 -- 승격 자체는 살아 있고(이 슬라이스가 없애는 것이
    # 아니다), 토큰 경로가 그 이름을 고를 수 없게 된 것이 차단의 본질이다.
    ident = resolve_job_identity(
        control, None, requester_id="root", owner_username=None,
        allow_privileged=True,
        privileged_requesters=frozenset({"root", "admin"}),
        session_authenticated=True)
    assert ident.uid == 0 and ident.gid == 0 and ident.privileged is True


def test_shared_token_cannot_choose_privileged_actor(db):
    # (2) 게이트가 이 스푸핑을 닫는지. 게이트 전에는 POST 가 202 로 통과하고
    # requester_id="root" 인 요청이 생겨 (1)의 승격으로 이어진다 -- 그게 구멍이다.
    client = _client(db)
    r = client.post("/api/user/requests", headers=TOKEN_ROOT, json=RM)
    assert r.status_code == 400
    assert r.json()["detail"] == "invalid_actor"
    # 요청 자체가 만들어지면 안 된다(플래너가 소급적으로 uid 0 을 굽는다).
    assert db.query("SELECT request_id FROM requests") == []


def test_token_actor_node_form_is_accepted(db):
    # 에이전트 무손상: node:<이름> 은 그대로 통과해 actor 로 실린다(설계 §2.2-1).
    client = _client(db)
    r = client.get("/api/auth/me",
                   headers={"Authorization": "Bearer tok-shared",
                            "x-dms-actor": "node:n1"})
    assert r.status_code == 200
    assert r.json() == {"actor": "node:n1", "role": "admin"}


def test_token_actor_empty_normalizes_to_shared_token(db):
    # 빈 값/공백만은 400 이 아니라 shared-token 정규화(옆문 유지 금지, 설계 §2.2-1).
    client = _client(db)
    for value in (None, "", "   "):
        headers = {"Authorization": "Bearer tok-shared"}
        if value is not None:
            headers["x-dms-actor"] = value
        assert client.get("/api/auth/me", headers=headers).json() == {
            "actor": "shared-token", "role": "admin"}


def test_node_regex_has_single_source_of_truth():
    # actor 게이트가 통과시킨 node:<이름> 을 에이전트 라우트가 다시 검증한다 --
    # 두 규칙이 갈라지면 한쪽이 통과시킨 값을 다른 쪽이 403 으로 거절한다. 같은
    # 컴파일된 객체를 재사용해 갈라짐 자체를 불가능하게 한다.
    assert auth_mod._NODE_NAME_RE is agent_mod._NODE_NAME_RE
