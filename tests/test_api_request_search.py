"""작업 목록 검색(2026-10-25 사용자 요청 -- 「배치 이름, 대상 경로 등으로 검색·필터, 일부 단어라도 포함하거나 매치되면
그것도 보여주도록」): GET /api/user/requests 의 q(부분 일치, 토큰 OR)와 관리자 requester 부분 일치. 계약의 몸통은
RequestsRepository.list·routes_requests.list_requests docstring.

여기서 고정하는 것: 매치 칸(요청자 -- 관리자 전체 목록에서만·대상 몸통·배치 이름·16진 id 접두)과 **매치하지 않는 것**(키
머리 'data.<op>:'·scan 옵션 지문·id 의 중간·비관리자 목록의 요청자 칸), 대소문자 무시, LIKE 와일드카드의 글자 그대로, 토큰
OR·상한(절대경로 펼침 몫 포함), 절대경로 펼침(managed_root 만 -- mount_path 기준은 다른 디렉터리다)과 그 은닉 범위, 비관리자
격리(정확히 일치 requester 위에서만 좁힌다), 다른 필터·커서와의 AND, 검색이 없을 때 SQL 이 검색 전과 바이트 단위로 같음, 검색
SQL 이 토큰 수와 무관하게 건초더미 식을 한 번만 싣고 응답 행 모양이 검색 전과 같음, 변조된 짧은 키가 목록을 500 으로 만들지
않음, 그리고 _KEY_BODY 가 기대는 domain.build_resource_key 의 모양.

PostgreSQL: DMS_TEST_PG_URL(일회용 서버)을 주면 모든 테스트가 PG 에서도 돈다(db 픽스처를 두 방언으로 덮는다 --
tests/test_usage_equivalence.py 와 같은 스키마 격리). sqlite 의 LIKE 는 ASCII 대소문자를 접고 PG 는 가리며, PG 는 음수
길이 SUBSTR 에서 오류를 던진다 -- 두 방언이 같은 답을 내는지가 여기서만 드러난다. 없으면 그 매개변수만 건너뛴다."""
import os
import re
import uuid

import pytest

from dms.db import Database
from dms.domain import RequestState, build_data_payload, option_fingerprint
from dms.migrations import migrate
from dms.repositories.requests import MAX_SEARCH_TERMS, _KEY_BODY


@pytest.fixture(params=["sqlite", "postgresql"])
def db(request, tmp_path):
    # conftest 의 db 를 두 방언으로 덮는다 -- client 픽스처가 이 db 를 쓴다.
    if request.param == "sqlite":
        database = Database.connect(f"sqlite:///{tmp_path}/test.db")
        migrate(database)
        yield database
        return
    url = os.environ.get("DMS_TEST_PG_URL")
    if not url:
        pytest.skip("DMS_TEST_PG_URL 없음(일회용 PostgreSQL 에서만 돈다)")
    import psycopg
    schema = "dms_search_" + uuid.uuid4().hex[:12]
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(f"CREATE SCHEMA {schema}")
    database = Database.connect(f"{url}{'&' if '?' in url else '?'}options=-csearch_path%3D{schema}")
    try:
        migrate(database)
        yield database
    finally:
        database._conn.close()
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute(f"DROP SCHEMA {schema} CASCADE")


USERS = {"opadm": "admin", "alice": "user", "alice2": "user", "j_kim": "user", "jxkim": "user", "bob": "user"}


@pytest.fixture
def repos(client):
    repos = client.app.state.repos
    # ceph: 뿌리가 마운트 아래(일반형), gpfs: 뿌리 = 마운트, lus: mount_path 로는 펼치지 않음 확인용, adm: 관리자 전용(사용자 비공개).
    for name, mount, root, user_enabled in (("ceph", "/mnt/ceph", "/mnt/ceph/managed", True),
                                            ("gpfs", "/gpfs", "/gpfs", True),
                                            ("lus", "/lus", "/lus/dms", True),
                                            ("adm", "/adm", "/adm/dms", False)):
        repos.storages.create(storage_name=name, mount_path=mount, managed_root=root, backend_type="cephfs",
                              actor="test", user_enabled=user_enabled)
    for name, role in USERS.items():
        repos.accounts.create(name, "p", role, actor="t")
    return repos


def _as(client, name):
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"username": name, "password": "p"}).status_code == 200


def _req(repos, op, *, requester="opadm", batch_id=None, **where):
    # 실제 resource_key 모양으로(build_data_payload) -- 다른 목록 테스트의 가짜 키('k')로는 대상 몸통 매치를 볼 수 없다.
    payload, key = build_data_payload(op, options={"recursive": True} if op == "rm" else {}, **where)
    return repos.requests.create(operation=op, requester_id=requester, actor=requester, resource_key=key,
                                 payload=payload, priority="mid", batch_id=batch_id)


def _scan(repos, target, *, storage="ceph", **kw):
    return _req(repos, "scan", storage=storage, target=target, **kw)


def _batch(repos, name):
    return repos.batches.create(operation="scan", requester_id="opadm", actor="opadm", max_concurrency=1,
                                options={}, note=None, items=[{"storage": "ceph", "target": "t"}],
                                status="Completed", name=name, auth_method="session")


def _ids(client, **params):
    r = client.get("/api/user/requests", params={"limit": 200, **params})
    assert r.status_code == 200, r.text
    return [row["request_id"] for row in r.json()]


def test_q_matches_target_paths_and_storage_names_case_insensitively(client, repos):
    scan = _scan(repos, "Projects/Alpha/Raw")
    rm = _req(repos, "rm", storage="ceph", target="projects/beta")
    sync = _req(repos, "sync", source_storage="gpfs", source="home/x", destination_storage="ceph",
                destination="backup/x")
    _as(client, "opadm")
    assert _ids(client, q="alpha/raw") == [scan]                  # 저장값은 대문자, 검색어는 소문자
    assert _ids(client, q="PROJECTS") == [rm, scan]               # 검색어 대문자 -- 저장값 대소문자 섞임
    assert _ids(client, q="backup/X") == [sync]                   # sync 목적지
    assert _ids(client, q="home/x") == [sync]                     # sync 소스
    assert _ids(client, q="gpfs") == [sync]                       # 스토리지 이름
    assert _ids(client, q="gpfs:home") == [sync]                  # 화면 「대상」 열 글자 그대로
    assert _ids(client, q="ceph") == [sync, rm, scan]


def test_q_ignores_the_key_prefix_and_the_scan_fingerprint(client, repos):
    # 키 머리('data.<op>:')와 scan 옵션 지문(64자리 16진)은 화면에 없는 글자다 -- 걸리면 'data' 같은 경로 단어가 모든
    # 행에, 숫자 검색어가 scan 행에 아무렇게나 걸린다(_KEY_BODY 주석).
    scan = _scan(repos, "t1")
    _req(repos, "rm", storage="ceph", target="t2")
    _req(repos, "sync", source_storage="ceph", source="t3", destination_storage="gpfs", destination="t4")
    fp = option_fingerprint({})
    _as(client, "opadm")
    for term in ("data", "data.scan", "scan", "sync", "rm", "data.rm:ceph", fp, fp[20:40], ":" + fp[:8]):
        assert _ids(client, q=term) == [], term
    assert _ids(client, q="t1") == [scan]                          # 몸통은 그대로 걸린다
    assert _ids(client, q="ceph:t1") == [scan]


def test_q_matches_batch_names_including_korean(client, repos):
    korean = _batch(repos, "성장 모니터링")
    kids = [_scan(repos, f"k{i}", batch_id=korean) for i in range(2)]
    weekly = _batch(repos, "Weekly-REPORT")
    w = _scan(repos, "w1", batch_id=weekly)
    unnamed = _batch(repos, None)
    _scan(repos, "u1", batch_id=unnamed)
    _scan(repos, "single")
    _as(client, "opadm")
    assert _ids(client, q="모니터") == kids[::-1]
    assert _ids(client, q="성장") == kids[::-1]
    assert _ids(client, q="report") == [w]
    assert _ids(client, q="WEEKLY-rep") == [w]


def test_q_treats_like_wildcards_and_backslash_literally(client, repos):
    pct = _scan(repos, "a%b")
    und = _scan(repos, "a_b")
    _scan(repos, "axb")
    bsl = _scan(repos, "a\\b")
    _as(client, "opadm")
    assert _ids(client, q="a%b") == [pct]          # 이스케이프가 없으면 axb·a_b·a\b 도 걸린다
    assert _ids(client, q="%") == [pct]
    assert _ids(client, q="a_b") == [und]          # 이스케이프가 없으면 axb 도 걸린다
    assert _ids(client, q="_") == [und]
    assert _ids(client, q="\\") == [bsl]
    assert _ids(client, q="a\\b") == [bsl]


def test_q_tokens_are_ored_deduped_case_insensitively_and_capped(client, repos):
    alpha = _scan(repos, "alpha")
    beta = _scan(repos, "beta")
    _scan(repos, "gamma")
    _as(client, "opadm")
    assert _ids(client, q="alpha beta") == [beta, alpha]           # 하나라도 맞으면(토큰 OR)
    assert _ids(client, q="alpha  \t nothing-here") == [alpha]     # 안 맞는 단어가 있어도 좁아지지 않는다
    assert _ids(client, q="Alpha　beta") == [beta, alpha]          # 전각 공백도 구분자
    # 상한: 앞의 MAX_SEARCH_TERMS 개만 -- 그 뒤의 토큰은 버린다(드문 검색어는 토큰마다 표 전체를 훑는다).
    misses = " ".join(f"zz{i}" for i in range(MAX_SEARCH_TERMS))
    assert _ids(client, q=f"{misses} gamma") == []
    # 중복은 대소문자를 접어 뺀 뒤에 센다 -- 같은 단어의 대소문자 변형 9개가 상한을 먹지 않는다.
    variants = "alpha Alpha ALPHA aLpha alPha alpHa alphA ALpha ALPha"
    assert _ids(client, q=f"{variants} beta") == [beta, alpha]


def test_hex_tokens_prefix_match_request_and_batch_ids(client, repos, db):
    rid = _scan(repos, "h1")
    forced = "abcd" + "0" * 27 + "1"
    db.execute("UPDATE requests SET request_id = :n WHERE request_id = :o", {"n": forced, "o": rid})
    named = _batch(repos, "이름 있음")
    named_kid = _scan(repos, "h2", batch_id=named)
    gone = _batch(repos, "곧 지움")
    gone_kid = _scan(repos, "h3", batch_id=gone)
    repos.batches.delete(gone)                     # 「기록 없음」 묶음 -- 이름이 사라져 id 로만 찾는다
    _as(client, "opadm")
    everyone = _ids(client)

    def starting(prefix):
        # 무작위 uuid 가 우연히 같은 접두로 시작해도 깨지지 않게 기대값을 실제 id 에서 고른다.
        return [r for r in everyone if r.startswith(prefix)]
    assert forced in starting("abcd")
    assert _ids(client, q="abcd") == starting("abcd")
    assert _ids(client, q="ABCD0000") == starting("abcd0000") == [forced]   # 대문자로 쳐도
    assert _ids(client, q=forced) == [forced]
    assert _ids(client, q="abc") == []             # 4자 미만은 id 접두를 보지 않는다
    assert _ids(client, q="bcd0") == starting("bcd0")   # 접두만 -- forced 의 중간('bcd0…')은 잡음이라 안 본다
    assert _ids(client, q="abcdz") == []           # 16진이 아닌 토큰은 id 를 보지 않는다
    assert _ids(client, q=gone[:12]) == [gone_kid]
    assert _ids(client, q=gone[:12].upper()) == [gone_kid]
    assert _ids(client, q=named[:6]) == [named_kid]
    assert _ids(client, q=gone[3:15]) == []


def test_absolute_paths_expand_to_storage_relative_tokens(client, repos):
    # 저장값은 managed_root 기준 상대경로라(stepper._abs) 펼침도 managed_root 로만 -- 관리자에게도. mount_path 기준 펼침은
    # managed_root 가 마운트 아래인 보통 모양에서 늘 다른 디렉터리를 가리켰다(2026-10-25 리뷰): 아래 wrong 의 실제 경로는
    # /mnt/ceph/managed/managed/proj/x 인데 '/mnt/ceph/managed/proj/x' 가 「ceph:managed/proj/x」 로도 펼쳐져 그것을 잡았다.
    a = _scan(repos, "proj/x")
    wrong = _scan(repos, "managed/proj/x")
    b = _scan(repos, "proj/x", storage="gpfs")
    c = _scan(repos, "z", storage="lus")
    e = _scan(repos, "q1", storage="adm")
    _as(client, "opadm")
    assert _ids(client, q="/mnt/ceph/managed/proj/x") == [a]        # managed_root 아래 -- wrong 은 안 나온다
    assert _ids(client, q="/mnt/ceph/managed/proj/x/") == [a]
    assert _ids(client, q="/mnt/ceph/managed/managed/proj/x") == [wrong]
    assert _ids(client, q="/mnt/ceph/managed") == [wrong, a]        # 뿌리 자체 = 그 스토리지 전부
    assert _ids(client, q="/mnt/ceph/managedX/proj/x") == []        # 이름만 같은 형제 디렉터리는 아래가 아니다
    assert _ids(client, q="/mnt/ceph/managedproj/x") == []          # (접두 글자 비교면 「ceph:proj/x」로 샌다)
    assert _ids(client, q="/gpfs/proj/x") == [b]                    # 뿌리 = 마운트
    assert _ids(client, q="/lus/dms/z") == [c]
    assert _ids(client, q="/lus/z") == []                           # 뿌리 밖 -- 그 잡은 /lus/dms/z 에서 돌았다
    assert _ids(client, q="/adm/dms/q1") == [e]
    assert _ids(client, q="nothing /mnt/ceph/managed/proj/x") == [a]  # 다른 토큰과 함께여도


def test_absolute_paths_share_the_term_cap(client, repos):
    # 펼쳐진 절대경로는 펼친 「이름:상대경로」 하나만 상한(MAX_SEARCH_TERMS) 자리를 먹는다 -- 원래 토큰을 함께 두던 동안은
    # 하나가 둘을 먹어 한 번에 붙여 넣는 절대경로가 둘에서 잘렸다(2026-10-25 검증 지적). 이제 넷까지, 다섯째부터 버려진다.
    rows = [_scan(repos, f"multi/{i}") for i in range(5)]
    _as(client, "opadm")
    paths = [f"/mnt/ceph/managed/multi/{i}" for i in range(5)]
    assert MAX_SEARCH_TERMS == 4
    assert _ids(client, q=" ".join(paths[:4])) == rows[3::-1]
    assert _ids(client, q=" ".join(paths)) == rows[3::-1]           # 다섯째 경로는 상한 밖
    assert _ids(client, q=paths[4]) == [rows[4]]


def test_unexpanded_absolute_path_stays_a_token_for_batch_names(client, repos):
    # 어느 managed_root 에도 속하지 않는 절대경로는 원래 토큰 그대로 남는다 -- 검색 대상 중 '/'로 시작하는 절대경로를 담을 수
    # 있는 것은 배치 이름뿐이라, 그런 이름의 배치는 여전히 찾힌다. 펼쳐진 경로는 원래 토큰을 버리므로 같은 이름이라도
    # 펼침 결과(그 스토리지의 그 경로)만 본다.
    bid = _batch(repos, "nightly /scratch/in copy")
    child = _req(repos, "scan", batch_id=bid, storage="ceph", target="elsewhere")
    other = _scan(repos, "unrelated")
    _as(client, "opadm")
    assert _ids(client, q="/scratch/in") == [child]
    assert other not in _ids(client, q="/scratch/in")


def test_non_admin_path_expansion_uses_only_what_the_storage_picker_shows(client, repos):
    # 비관리자에게 /api/user/storages 는 관리자 전용 스토리지를 숨긴다 -- 펼침이 그 뿌리를 쓰면 「내 작업이 나오나」로 숨긴
    # 경로를 맞혀 보는 오라클이 된다. mount_path 기준 펼침은 이제 누구에게도 없다. 상대경로 토큰은 그대로 걸린다.
    c = _scan(repos, "z", storage="lus", requester="alice")
    e = _scan(repos, "q1", storage="adm", requester="alice")
    _as(client, "alice")
    assert _ids(client, q="/lus/dms/z") == [c]
    assert _ids(client, q="/lus/z") == []
    assert _ids(client, q="/adm/dms/q1") == []
    assert _ids(client, q="adm:q1") == [e]


def test_admin_requester_filter_is_case_insensitive_escaped_substring(client, repos):
    rows = {name: _scan(repos, f"t-{name}", requester=name) for name in ("alice", "alice2", "j_kim", "jxkim", "bob")}
    _as(client, "opadm")
    assert _ids(client, requester="alice") == [rows["alice2"], rows["alice"]]
    assert _ids(client, requester="ALI") == [rows["alice2"], rows["alice"]]
    assert _ids(client, requester="j_kim") == [rows["j_kim"]]     # '_' 는 글자 그대로 -- jxkim 이 섞이지 않는다
    assert _ids(client, requester="kim") == [rows["jxkim"], rows["j_kim"]]
    assert _ids(client, requester="alice", q="t-alice2") == [rows["alice2"]]   # q 와 AND
    everyone = _ids(client)
    assert _ids(client, requester="") == everyone and _ids(client, requester="   ") == everyone


def test_non_admin_stays_isolated_with_q_and_requester(client, repos):
    mine = _scan(repos, "a1", requester="alice")
    other = _scan(repos, "a2-secret", requester="alice2")
    _scan(repos, "bobonly", requester="bob")
    bid = _batch(repos, "성장 모니터링")
    kid = _scan(repos, "b1", batch_id=bid)
    jk = _scan(repos, "home/j_kim/x", requester="j_kim")
    jx = _scan(repos, "home/jxkim/y", requester="jxkim")
    _as(client, "alice")
    assert _ids(client) == [mine]
    assert _ids(client, requester="alice2") == [mine]              # 비관리자의 requester 는 무시(격리)
    assert _ids(client, requester="ali") == [mine]
    assert _ids(client, q="a1") == [mine]
    for term in ("a2-secret", "bobonly", "모니터", "b1", other[:8], kid[:8], bid[:12], "/mnt/ceph/managed/a2-secret",
                 "alice2"):
        assert _ids(client, q=term) == [], term                    # 남의 경로·배치 이름·id·이름은 0건(유무 오라클 없음)
    _as(client, "j_kim")
    assert _ids(client, q="kim") == [jk]
    assert _ids(client, requester="jxkim") == [jk]
    assert _ids(client, q="jx") == []
    _as(client, "jxkim")
    assert _ids(client, q="j_kim") == []                           # 이스케이프 -- 자기 경로 'jxkim' 도 '_' 로 걸리지 않는다
    assert _ids(client, q="jxkim") == [jx]


def test_requester_arm_only_on_the_admin_wide_list(client, repos):
    # 비관리자의 목록은 이미 requester_id = 자기 자신이라 요청자 칸은 모든 행에 참이다 -- 토큰 OR 라 자기 id 의 부분
    # 문자열('ali')만 쳐도 경로와 무관하게 자기 작업 전부가 나왔다(2026-10-25 리뷰). 경로·배치 이름에 맞는 행만 나온다.
    plain = _scan(repos, "proj/a", requester="alice")
    home = _scan(repos, "home/alice/b", requester="alice")
    kid = _scan(repos, "proj/c", requester="alice", batch_id=_batch(repos, "alice 주간"))
    _as(client, "alice")
    for term in ("ali", "alice", "ALI", "alice nothing-here"):
        assert _ids(client, q=term) == [kid, home], term
    assert _ids(client, q="proj") == [kid, plain]
    # 관리자 전체 목록(requester_id 없음)에선 요청자 칸이 산다 -- requester 부분 일치로 좁혀도 여전히 전체 목록이다.
    _as(client, "opadm")
    assert _ids(client, q="ali") == [kid, home, plain]
    assert _ids(client, q="ali", requester="alice") == [kid, home, plain]
    # 저장소 직접 호출도 같다(라우트가 아니라 저장소의 규칙이다).
    assert [r["request_id"] for r in repos.requests.list("alice", search_terms=["ali"])] == [kid, home]
    assert [r["request_id"] for r in repos.requests.list(search_terms=["ali"])] == [kid, home, plain]


def test_q_combines_with_batch_id_operation_and_state(client, repos):
    b1 = _batch(repos, "첫째")
    one = _scan(repos, "p/one", batch_id=b1)
    two = _scan(repos, "p/two", batch_id=b1)
    three = _scan(repos, "p/three", batch_id=b1)
    b2 = _batch(repos, "둘째")
    other_one = _scan(repos, "p/one", batch_id=b2)
    single_one = _scan(repos, "p/one")
    repos.requests.set_state(two, RequestState.SUCCEEDED, actor="test")
    _as(client, "opadm")
    assert _ids(client, q="one") == [single_one, other_one, one]
    assert _ids(client, q="one", batch_id=b1) == [one]
    assert _ids(client, q="one two", batch_id=b1) == [two, one]
    assert _ids(client, q="one", operation="rm") == []
    assert _ids(client, q="one", operation="scan", batch_id=b2) == [other_one]
    assert _ids(client, q="p/", batch_id=b1, state="Succeeded") == [two]
    assert _ids(client, q="첫째", state="Pending") == [three, one]


def _pages(client, **params):
    seen, before = [], None
    for _ in range(50):      # 커서가 무시되면 같은 쪽이 끝없이 온다 -- 매달리지 말고 실패하게
        page = client.get("/api/user/requests",
                          params={"limit": 2, **params, **({"before": before} if before is not None else {})}).json()
        if page == []:
            return seen
        seen += [row["request_id"] for row in page]
        before = page[-1]["commit_order"]
    raise AssertionError(f"커서 쪽 넘김이 끝나지 않는다: {seen[:6]}…")


def test_q_pages_with_the_cursor_without_overlap_or_gap(client, repos):
    # 검색은 WHERE 에만 붙는다 -- before 쪽 넘김이 검색 결과 위에서도 중복·누락 없이 이어진다(배치 필터와 함께여도).
    bid = _batch(repos, "묶음")
    keep, keep_batch = [], []
    for i in range(6):
        keep.append(_scan(repos, f"keep-{i}"))
        _scan(repos, f"drop-{i}")
        keep.append(_scan(repos, f"keep-b{i}", batch_id=bid))
        keep_batch.append(keep[-1])
        _scan(repos, f"drop-b{i}", batch_id=bid)
    _as(client, "opadm")
    assert _pages(client, q="keep") == keep[::-1]
    assert _pages(client, q="keep", batch_id=bid) == keep_batch[::-1]


def test_blank_q_is_no_filter(client, repos):
    rows = [_scan(repos, f"t{i}") for i in range(3)]
    _as(client, "opadm")
    assert _ids(client) == rows[::-1]
    for blank in ("", "   ", "\t \n"):
        assert _ids(client, q=blank) == rows[::-1], repr(blank)


def test_q_length_is_capped_by_the_framework(client, repos):
    _as(client, "opadm")
    assert client.get("/api/user/requests", params={"q": "a" * 200}).status_code == 200
    assert client.get("/api/user/requests", params={"q": "a" * 201}).status_code == 422


def test_tampered_short_resource_keys_do_not_break_the_search(client, repos):
    # DB 는 신뢰 경계 -- 키가 머리·지문보다 짧은 변조 행. PG 는 음수 길이 SUBSTR 에서 오류를 던져 목록 전체가 500 이었다
    # (_KEY_BODY 의 CASE 감쌈). 행은 요청자 칸으로 여전히 찾는다(식이 그 행에서도 평가된다).
    odd = []
    for op, key in (("scan", "x"), ("scan", ""), ("scan", "data.scan:"), ("sync", "y"), ("rm", ""), ("bogus", "z")):
        odd.append(repos.requests.create(operation=op, requester_id="opadm", actor="opadm", resource_key=key,
                                         payload={}, priority="mid"))
    _as(client, "opadm")
    for term in ("x", "y", "z", "data", "zz"):
        assert _ids(client, q=term) == [], term
    assert _ids(client, q="opadm") == odd[::-1]


def test_key_body_sql_follows_build_resource_key_shapes(repos, db):
    # _KEY_BODY 는 키 모양(머리 'data.<op>:' = len(op)+6자, scan 꼬리 ':<64자리 16진>' = 65자)에 기댄다 -- 모양이 바뀌면
    # 검색이 조용히 머리·지문을 매치하거나 경로 끝을 잘라 먹는다. 실제 키를 DB 에 넣고 같은 식으로 잘라 화면 글자와 비교한다
    # (한글 경로 -- 길이·자르기가 바이트가 아니라 글자 단위인지도).
    cases = {
        "sync": (dict(source_storage="s-src", source="팀/원본", destination_storage="s-dst", destination="dir/b"),
                 "s-src:팀/원본:s-dst:dir/b"),
        "rm": (dict(storage="st", target="t/x"), "st:t/x"),
        "scan": (dict(storage="st", target="t/데이터"), "st:t/데이터"),
    }
    for op, (where, body) in cases.items():
        opts = {"recursive": True} if op == "rm" else {"broken_limit": 5} if op == "scan" else {}
        payload, key = build_data_payload(op, options=opts, **where)
        assert key.startswith(f"data.{op}:") and len(f"data.{op}:") == len(op) + 6, key
        if op == "scan":
            fp = option_fingerprint(payload["options"])
            assert re.fullmatch(r"[0-9a-f]{64}", fp) and key.endswith(":" + fp), key
        else:
            assert key == f"data.{op}:{body}", key
        rid = repos.requests.create(operation=op, requester_id="opadm", actor="opadm", resource_key=key,
                                    payload=payload, priority="mid")
        row = db.query_one(f"SELECT {_KEY_BODY} AS body FROM requests WHERE request_id = :r", {"r": rid})
        assert row["body"] == body, (op, row["body"])


def test_sql_without_search_is_byte_identical_and_patterns_are_bound(client, repos, db):
    # 검색이 없으면 SQL 이 검색 전과 바이트 단위로 같다(배치 필터의 계획 고정 -- test_migrations, PG 준비문 캐시).
    # 검색어는 바인딩 값으로만 -- SQL 본문의 '%' 는 PG(psycopg pyformat)에서 자리표시자로 읽혀 깨진다.
    captured = []
    orig = db.query

    def spy(sql, params=None):
        if "FROM requests" in sql and "ORDER BY" in sql:
            captured.append((sql, params))
        return orig(sql, params)

    db.query = spy
    try:
        repos.requests.list(batch_id="b" * 32, before=10, limit=50)
        repos.requests.list(requester_id="alice", operation="scan", state="Pending", limit=50)
        repos.requests.list(limit=50)
        _as(client, "opadm")
        for params in ({}, {"q": ""}, {"q": "   "}, {"requester": ""}, {"requester": " "}):
            client.get("/api/user/requests", params=params)
        n = len(captured)
        client.get("/api/user/requests", params={"q": "100% a_b \\ x", "requester": "a%"})
    finally:
        db.query = orig
    assert [sql for sql, _ in captured[:3]] == [
        "SELECT * FROM requests WHERE batch_id >= :bid AND batch_id <= :bid AND commit_order < :before"
        " ORDER BY batch_id DESC, commit_order DESC LIMIT :n",
        "SELECT * FROM requests WHERE requester_id = :req AND operation = :op AND state = :st"
        " ORDER BY commit_order DESC LIMIT :n",
        "SELECT * FROM requests ORDER BY commit_order DESC LIMIT :n",
    ]
    assert {sql for sql, _ in captured[3:n]} == {"SELECT * FROM requests ORDER BY commit_order DESC LIMIT :n"}
    ((sql, params),) = captured[n:]
    assert "%" not in sql and sql.endswith(" ORDER BY commit_order DESC LIMIT :n"), sql
    assert params["req_like"] == "%a\\%%" and params["q0"] == "%100\\%%" and params["q1"] == "%a\\_b%"


def test_search_sql_builds_the_haystack_once_and_rows_keep_their_shape(client, repos, db):
    # 최악 비용의 상한(2026-10-25 리뷰): 건초더미 식(_KEY_BODY)은 토큰 수와 무관하게 SQL 에 **한 번** -- 칸마다·토큰마다
    # LOWER(...) LIKE 로 되돌아가면 드문 검색어가 토큰마다 표 전체 비용을 한 번씩 더 낸다(PG 실측 4개 1.1초 대 0.36초).
    # PG 는 LIKE ANY(왼쪽 식 한 번) -- 문장 모양이 검색 전과 같아 LIMIT 을 아는 계획 그대로; sqlite 는 펜스 부분 질의.
    # 펜스의 열(search_hay)은 응답에 새지 않는다.
    _scan(repos, "deadbeef/x")
    captured = []
    orig = db.query

    def spy(sql, params=None):
        if "FROM requests" in sql and "ORDER BY" in sql:
            captured.append(sql)
        return orig(sql, params)

    _as(client, "opadm")
    plain = client.get("/api/user/requests").json()
    db.query = spy
    try:
        for q in ("deadbeef", "deadbeef x zz1 zz2", "deadbeef x zz1 zz2 zz3 zz4 zz5"):
            got = client.get("/api/user/requests", params={"q": q}).json()
            assert [sorted(r) for r in got] == [sorted(r) for r in plain], q
        repos.requests.list("alice", search_terms=["a", "b", "c", "d"])
    finally:
        db.query = orig
    assert len(captured) == 4
    for sql in captured:
        assert sql.count(_KEY_BODY) == 1, sql
        if db.dialect == "postgresql":
            assert sql.startswith("SELECT * FROM requests WHERE ") and "OFFSET" not in sql and "LIKE ANY (ARRAY[" in sql
        else:
            assert sql.count("LIMIT -1 OFFSET 0") == 1, sql
    assert all("requester_id || ' ' ||" in sql for sql in captured[:3])     # 관리자 전체 목록 -- 요청자 칸 포함
    assert "requester_id || ' ' ||" not in captured[3]                      # 한 요청자로 묶인 목록 -- 요청자 칸 없음


def test_repository_resplits_tokens_on_whitespace(repos):
    # 건초더미는 「요청자 + ' ' + 대상 몸통」이라 토큰에 ' ' 가 없어야 두 칸에 걸친 매치가 생기지 않는다 -- 라우트는 이미
    # 공백으로 나누지만 저장소를 직접 부르는 쪽도 같은 의미(낱말 OR)가 되게 저장소가 다시 나눈다.
    a = _scan(repos, "t1")
    b = _scan(repos, "t2", storage="gpfs", requester="bob")
    _scan(repos, "t3", storage="gpfs", requester="bob")
    assert [r["request_id"] for r in repos.requests.list(search_terms=["t2 opadm"])] == [b, a]
    assert [r["request_id"] for r in repos.requests.list(search_terms=["   "])] == \
        [r["request_id"] for r in repos.requests.list()]                     # 공백뿐 = 검색 없음
