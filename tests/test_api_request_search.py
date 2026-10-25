"""작업 목록 검색(2026-10-25, dms-k8s) -- GET /api/user/requests 의 requester(관리자, 부분 일치로 완화)·batch_name(관리자)·
path(누구나). 사용자 요청: 「요청자 아이디로만 필터를 걸수 있는데 배치 이름, 대상 (경로) 등으로도 검색 및 필터링 …
일부 단어라도 포함하거나 매치되면 그것도 보여주도록 해줘 (exact match 완화)」.

셋 다 대소문자를 무시한 부분 일치, 공백으로 나눈 토큰은 AND, 칸끼리도 AND(행을 좁히기만 한다). 격리(비관리자 =
requester_id 정확 일치 강제)가 부분 일치로 새지 않는 것이 이 파일의 첫째 관심사다."""
from dms.domain import build_resource_key, option_fingerprint

ADMIN = {"Authorization": "Bearer tok-shared"}
FP = option_fingerprint({})


def _login(client, name, role="user"):
    client.app.state.repos.accounts.create(name, "p", role, actor="t")
    assert client.post("/api/auth/login", json={"username": name, "password": "p"}).status_code == 200


def _req(repos, operation, requester="alice", batch_id=None, **where):
    key = build_resource_key(operation, fingerprint=FP, **where)
    return repos.requests.create(operation=operation, requester_id=requester, actor=requester, resource_key=key,
                                 payload=dict(where), priority="mid", batch_id=batch_id)


def _batch(repos, name):
    return repos.batches.create(operation="scan", requester_id="opadm", actor="opadm", max_concurrency=1,
                                options={}, note=None, items=[{"storage": "s1", "target": "t"}], status="Completed",
                                name=name, auth_method="session")


def _ids(client, headers=None, **params):
    r = client.get("/api/user/requests", headers=headers, params={"limit": 200, **params})
    assert r.status_code == 200, r.text
    return {row["request_id"] for row in r.json()}


def test_admin_requester_is_a_case_insensitive_partial_match(client):
    repos = client.app.state.repos
    alice = _req(repos, "rm", "alice", storage="s1", target="a")
    malice = _req(repos, "rm", "malice", storage="s1", target="b")
    bob = _req(repos, "rm", "bob", storage="s1", target="c")
    assert _ids(client, ADMIN, requester="ALI") == {alice, malice}
    assert _ids(client, ADMIN, requester="alice") == {alice, malice}   # 예전 정확 일치의 상위집합(하위 호환)
    assert _ids(client, ADMIN, requester="  ") == {alice, malice, bob}  # 공백뿐 = 필터 없음
    assert _ids(client, ADMIN, requester="ma ce") == {malice}           # 토큰 AND
    assert _ids(client, ADMIN, requester="zz") == set()


def test_like_wildcards_in_the_search_are_literal(client):
    # '_' 는 계정 이름에 허용되는 글자다 -- 이스케이프하지 않으면 'a_c' 가 'abc' 에, '%' 가 전부에 걸린다.
    repos = client.app.state.repos
    under = _req(repos, "rm", "a_c", storage="s1", target="old_x")
    plain = _req(repos, "rm", "abc", storage="s1", target="oldax")
    pct = _req(repos, "rm", "zed", storage="s1", target="ratio100%")
    back = _req(repos, "rm", "yy", storage="s1", target="dir\\name")
    assert _ids(client, ADMIN, requester="a_c") == {under}
    assert _ids(client, ADMIN, requester="%") == set()
    assert _ids(client, ADMIN, path="old_x") == {under}
    assert _ids(client, ADMIN, path="100%") == {pct}
    assert _ids(client, ADMIN, path="o%x") == set()
    assert _ids(client, ADMIN, path="dir\\n") == {back}
    assert plain not in _ids(client, ADMIN, path="_")


def test_path_matches_the_target_column_text_not_the_key_prefix_or_scan_fingerprint(client):
    repos = client.app.state.repos
    sync = _req(repos, "sync", source_storage="s1", source="proj/a", destination_storage="s2",
                destination="mirror/b")
    rm = _req(repos, "rm", storage="s1", target="tmp/old")
    scan = _req(repos, "scan", storage="ceph-3", target="logs/로그/2026")
    assert _ids(client, ADMIN, path="mirror") == {sync}                  # 도착 쪽
    assert _ids(client, ADMIN, path="proj") == {sync}                    # 출발 쪽
    assert _ids(client, ADMIN, path="S2:MIR") == {sync}                  # 스토리지 이름 + 대소문자 무시
    assert _ids(client, ADMIN, path="로그") == {scan}                     # 한글
    assert _ids(client, ADMIN, path="ceph-3") == {scan}                  # 스토리지 이름만
    assert _ids(client, ADMIN, path="tmp old") == {rm}                   # 토큰 AND(순서 무관)
    # 화면 대상 열의 sync 표기를 통째로 붙여 넣어도 맞는다(「→」 는 구분자).
    assert _ids(client, ADMIN, path="s1:proj/a → s2:mirror/b") == {sync}
    # 키 접두(data.<op>:)와 scan 옵션 지문은 화면에 없는 글자다 -- 걸리면 검색이 거짓말이 된다.
    for word in ("data", "sync", "scan", "data.rm", FP[:8], FP[-6:]):
        assert _ids(client, ADMIN, path=word) == set(), word


def test_batch_name_matches_the_name_or_an_id_prefix_and_leaves_out_single_jobs(client):
    repos = client.app.state.repos
    move = _batch(repos, "스토리지 이관(1차)")
    nightly = _batch(repos, "Nightly 백업")
    unnamed = _batch(repos, None)
    gone = _batch(repos, "지워질 배치")
    m1 = _req(repos, "scan", "opadm", batch_id=move, storage="s1", target="a")
    n1 = _req(repos, "scan", "opadm", batch_id=nightly, storage="s1", target="b")
    u1 = _req(repos, "scan", "opadm", batch_id=unnamed, storage="s1", target="c")
    g1 = _req(repos, "scan", "opadm", batch_id=gone, storage="s1", target="d")
    single = _req(repos, "scan", "opadm", storage="s1", target="e")
    repos.batches.delete(gone)                                           # 「기록 없음」 묶음(요청은 남는다)
    assert _ids(client, ADMIN, batch_name="이관") == {m1}
    assert _ids(client, ADMIN, batch_name="이관 1차") == {m1}             # 토큰 AND
    assert _ids(client, ADMIN, batch_name="nightly") == {n1}             # 대소문자 무시
    assert _ids(client, ADMIN, batch_name="배치") == set()                # 지운 배치의 이름은 더는 없다
    # 이름 없는 배치·기록 없는 묶음은 화면에 id 앞 12자로 보인다 -- 6자 이상 hex 는 id 앞부분으로도 찾는다.
    assert _ids(client, ADMIN, batch_name=unnamed[:12]) == {u1}
    assert _ids(client, ADMIN, batch_name=gone[:6].upper()) == {g1}
    assert single not in _ids(client, ADMIN, batch_name=single[:6])      # 요청 id 가 아니라 배치 id 다
    # 6자 미만 hex 는 id 로 보지 않는다(낱말과 우연히 겹친다).
    assert _ids(client, ADMIN, batch_name=unnamed[:5]) == set()


def test_non_admin_stays_on_the_exact_self_filter_whatever_the_search(client):
    # 격리 회귀 그물: 'al' 은 'alice' 의 부분 문자열이다. requester 를 부분 일치로 바꾸면서 강제 값까지 LIKE 를 타면
    # al 이 alice·sal 의 작업을 본다. 기존 격리 테스트(alice/bob)로는 잡히지 않는다.
    repos = client.app.state.repos
    mine = _req(repos, "sync", "al", source_storage="s1", source="mine", destination_storage="s1",
                destination="mine2")
    alice = _req(repos, "sync", "alice", source_storage="s1", source="theirs", destination_storage="s1",
                 destination="theirs2")
    sal = _req(repos, "rm", "sal", storage="s1", target="mine")
    bid = _batch(repos, "비밀 배치")
    alice_child = _req(repos, "scan", "alice", batch_id=bid, storage="s1", target="x")
    _login(client, "al")
    assert _ids(client) == {mine}
    assert _ids(client, requester="alice") == {mine}                     # 요청자 검색은 무시(자기 자신 강제)
    assert _ids(client, requester="a") == {mine}
    assert _ids(client, path="mine") == {mine}                           # sal 의 'mine' 대상은 남의 것
    assert _ids(client, path="theirs") == set()
    # 배치 이름 검색은 관리자 전용 -- 비관리자에겐 무시된다(0건으로 좁혀지지도 않는다: 응답에 없는 배치 이름을 결과의
    # 있고 없음으로 캐 보는 오라클이 되지 않게).
    assert _ids(client, batch_name="비밀") == {mine}
    assert _ids(client, batch_name=bid[:12]) == {mine}
    assert not ({alice, sal, alice_child} & _ids(client, requester="alice", path="theirs mine"))


def test_search_narrows_together_with_the_other_filters_and_the_cursor(client):
    repos = client.app.state.repos
    rids = [_req(repos, "rm", "alice", storage="s1", target=f"growth/{i}") for i in range(5)]
    _req(repos, "rm", "bob", storage="s1", target="growth/x")
    _req(repos, "scan", "alice", storage="s1", target="growth/y")
    page1 = client.get("/api/user/requests", headers=ADMIN,
                       params={"limit": 2, "requester": "lic", "path": "GROWTH", "operation": "rm"}).json()
    assert [r["request_id"] for r in page1] == [rids[4], rids[3]]
    page2 = client.get("/api/user/requests", headers=ADMIN,
                       params={"limit": 10, "requester": "lic", "path": "GROWTH", "operation": "rm",
                               "before": page1[-1]["commit_order"]}).json()
    assert [r["request_id"] for r in page2] == [rids[2], rids[1], rids[0]]


def test_search_length_is_capped_and_nul_does_not_break_the_query(client):
    repos = client.app.state.repos
    rid = _req(repos, "rm", "alice", storage="s1", target="a/b")
    for name in ("requester", "batch_name", "path"):
        assert client.get("/api/user/requests", headers=ADMIN, params={name: "x" * 513}).status_code == 422, name
        assert client.get("/api/user/requests", headers=ADMIN, params={name: "x" * 512}).status_code == 200, name
    # NUL 은 PG text 가 받지 못한다(psycopg DataError → 500) -- 구분자로 지운다.
    assert _ids(client, ADMIN, path="a\x00b") == {rid}


def test_every_search_column_is_lowercased_in_sql_so_pg_matches_sqlite(client, monkeypatch):
    # sqlite LIKE 는 ASCII 대소문자를 원래 무시해 lower() 를 빠뜨려도 이 스위트는 초록이다 -- PG(테스트베드·프로덕션)
    # 에서만 대소문자를 구분하게 된다. 그래서 SQL 모양을 직접 본다.
    repos = client.app.state.repos
    seen: list[str] = []
    real = repos.requests._db.query

    def spy(sql, params=None):
        seen.append(sql)
        return real(sql, params)
    monkeypatch.setattr(repos.requests._db, "query", spy)
    repos.requests.list(requester_like=["a"], batch_like=["b"], path_like=["c"])
    sql = seen[-1]
    # 패턴 쪽도 같은 SQL lower() -- 파이썬 lower 와 DB lower 는 다르다(아래 비 ASCII 테스트).
    assert "lower(requester_id) LIKE lower(:rq0) ESCAPE '\\'" in sql
    assert "lower(name) LIKE lower(:bq0) ESCAPE '\\'" in sql
    assert ") LIKE lower(:pq0) ESCAPE '\\'" in sql and "lower(CASE WHEN" in sql
    # 배치 이름 조건은 COALESCE 로 감싼 IN(PG 가 semi-join 중첩 루프를 고르지 않게 -- requests.py 주석).
    assert "COALESCE(batch_id IN (SELECT batch_id FROM batches" in sql


def test_what_the_screen_shows_finds_its_own_row_even_with_non_ascii_capitals(client):
    # 2026-10-25 리뷰: 검색어를 파이썬 str.lower() 로, 컬럼을 DB lower() 로 낮추면 둘이 달라(sqlite·'C' 로캘 PG 는 ASCII 만)
    # 화면 대상 열에 보이는 「Ärger」 를 그대로 쳐도 그 행이 안 나왔다. 양쪽을 같은 함수로 낮추면 늘 자기 행에 걸린다.
    repos = client.app.state.repos
    rid = _req(repos, "rm", "alice", storage="s1", target="Ärger/Über")
    roman = _req(repos, "rm", "alice", storage="s1", target="실험Ⅱ/결과")
    bid = _batch(repos, "Ärger 정리")
    child = _req(repos, "scan", "opadm", batch_id=bid, storage="s1", target="x")
    assert _ids(client, ADMIN, path="Ärger") == {rid}
    assert _ids(client, ADMIN, path="Über") == {rid}
    assert _ids(client, ADMIN, path="Ⅱ") == {roman}
    assert _ids(client, ADMIN, batch_name="Ärger") == {child}
