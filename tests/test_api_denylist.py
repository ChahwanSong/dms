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
