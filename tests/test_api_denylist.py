import json

ADMIN = {"Authorization": "Bearer tok-shared"}


def test_denylist_require_admin(client):
    assert client.get("/api/admin/identity-denylist").status_code == 401


def test_denylist_crud(client):
    r = client.put("/api/admin/identity-denylist/requester/Mallory",
                   json={"reason": "incident"}, headers=ADMIN)
    assert r.status_code == 201
    listed = client.get("/api/admin/identity-denylist", headers=ADMIN).json()
    assert listed == [{"subject_type": "requester", "subject": "mallory",
                       "reason": "incident"}]
    assert client.put("/api/admin/identity-denylist/badtype/x",
                      json={}, headers=ADMIN).status_code == 422
    assert client.delete("/api/admin/identity-denylist/requester/mallory",
                         headers=ADMIN).status_code == 200
    assert client.get("/api/admin/identity-denylist", headers=ADMIN).json() == []


def test_denylist_subject_is_trimmed_and_blank_is_refused(client):
    # 2026-11-11: 「alice 」(복사한 끝 공백)가 그대로 저장되면 is_denied 가 계정 alice 와 맞추지 못하는데 표에서는 「alice」로
    # 보였다(kill-switch 가 조용히 무효). 공백만이면 빈 대상 행이 생겼다.
    r = client.put("/api/admin/identity-denylist/requester/Alice%20", json={"reason": "x"}, headers=ADMIN)
    assert r.status_code == 201 and r.json() == {"subject_type": "requester", "subject": "alice"}
    assert client.get("/api/admin/identity-denylist", headers=ADMIN).json() == [
        {"subject_type": "requester", "subject": "alice", "reason": "x"}]
    assert client.app.state.repos.control.is_denied(requester="alice", owner="alice") == "alice"
    r = client.put("/api/admin/identity-denylist/owner/%20%20", json={}, headers=ADMIN)
    assert r.status_code == 422 and r.json()["detail"] == "denylist_subject_empty"
    assert len(client.get("/api/admin/identity-denylist", headers=ADMIN).json()) == 1


def test_denylist_re_add_with_another_reason_updates_it_and_audits_the_real_change(client):
    # 2026-11-11: 예전엔 다른 사유로 다시 넣어도 201 인데 행은 옛 사유 그대로, 감사에만 새 사유가 남았다.
    def put(reason):
        return client.put("/api/admin/identity-denylist/requester/bob", json={"reason": reason}, headers=ADMIN)
    assert put("abuse").status_code == 201
    assert put("spam").status_code == 201
    assert client.get("/api/admin/identity-denylist", headers=ADMIN).json() == [
        {"subject_type": "requester", "subject": "bob", "reason": "spam"}]
    assert put("spam").status_code == 201      # 같은 사유 재추가 = 멱등, 감사 없음
    rows = [e for e in client.get("/api/admin/audit-log", headers=ADMIN).json()
            if e["mutation_class"] == "denylist"]
    assert [e["operation"] for e in rows] == ["update", "deny"]
    assert json.loads(rows[0]["before_state"])["reason"] == "abuse"
    assert json.loads(rows[0]["after_state"])["reason"] == "spam"
