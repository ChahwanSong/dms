"""경로 겹침 판정(2026-10-27, domain.overlap_path·overlap_sides·destructive_operation·path_conflict) -- 표 주도.

planner 1b(path_conflict)와 stepper 실행 관문(_overlap_hold)이 이 함수들 하나를 쓴다. 규칙(사용자 결정):
  - 같은 **스토리지 이름**에서 경로가 같거나 한쪽이 다른 쪽의 조상이면 겹친다(경로 **성분** 단위 -- a/b 와 a/bc 는 아니다).
  - 겹침이 충돌인 것은 한쪽이라도 파괴적(rm·미지 연산)일 때뿐: rm↔rm·rm↔sync 소스·rm↔sync 목적지·rm↔scan. sync↔sync·
    scan↔scan·scan↔sync 는 충돌이 아니다.
"""
import pytest

from dms.domain import destructive_operation, overlap_path, overlap_sides, path_conflict


@pytest.mark.parametrize("raw, norm", [
    ("a", "a"),
    ("a/b", "a/b"),
    ("a/b/", "a/b"),                 # 끝 '/'
    ("/a/b", "a/b"),                 # 앞 '/' -- 실행(managed_abs_path)은 lstrip 후 join
    ("//a//b//", "a/b"),
    ("./a/./b", "a/b"),
    ("", ""),                        # 루트
    (".", ""),
    ("/", ""),
    ("a/../b", ""),                  # '..' 성분 = 해석 불가 → 루트(fail-closed)
    ("..", ""),
    ("../x", ""),
    ("a\x00b", ""),                  # NUL
    (None, ""),                      # 비문자열(변조 행)
    (7, ""),
    (["a"], ""),
    ("팀 a/결과", "팀 a/결과"),       # 공백·한글은 그대로
])
def test_overlap_path_normalization(raw, norm):
    assert overlap_path(raw) == norm


def _rm(storage, target):
    return True, overlap_sides("rm", storage=storage, target=target)


def _scan(storage, target):
    return False, overlap_sides("scan", storage=storage, target=target)


def _sync(src_storage, src, dst_storage, dst):
    return False, overlap_sides("sync", source_storage=src_storage, source=src,
                                destination_storage=dst_storage, destination=dst)


def _conflict(a, b):
    return path_conflict(a[0], a[1], b[0], b[1])


# (설명, a, b, 충돌인가)
CASES = [
    ("rm 같은 경로", _rm("s1", "a"), _rm("s1", "a"), True),
    ("rm 조상 vs 자손", _rm("s1", "a"), _rm("s1", "a/b/c"), True),
    ("rm 자손 vs 조상", _rm("s1", "a/b/c"), _rm("s1", "a"), True),
    ("성분 접두는 겹치지 않는다(a/b vs a/bc)", _rm("s1", "a/b"), _rm("s1", "a/bc"), False),
    ("성분 접두는 겹치지 않는다(a vs ab)", _rm("s1", "a"), _rm("s1", "ab"), False),
    ("형제", _rm("s1", "a/b"), _rm("s1", "a/c"), False),
    ("끝 '/' 는 같은 경로", _rm("s1", "a/b/"), _rm("s1", "a/b"), True),
    ("앞 '/' 는 같은 경로", _rm("s1", "/a/b"), _rm("s1", "a/b/c"), True),
    ("다른 스토리지의 같은 경로", _rm("s1", "a"), _rm("s2", "a"), False),
    ("루트('')는 무엇과도", _rm("s1", ""), _scan("s1", "z/y"), True),
    ("깨진 경로(None)는 루트로", _rm("s1", None), _scan("s1", "q"), True),
    ("깨진 경로도 다른 스토리지와는 아니다", _rm("s1", None), _scan("s2", "q"), False),
    ("rm vs scan 같은 경로", _rm("s1", "a"), _scan("s1", "a"), True),
    ("scan 조상 vs rm 자손", _scan("s1", "a"), _rm("s1", "a/b"), True),
    ("rm vs sync 소스(자손)", _rm("s1", "a"), _sync("s1", "a/b", "s2", "x"), True),
    ("rm vs sync 목적지(조상)", _rm("s2", "x/y"), _sync("s1", "a", "s2", "x"), True),
    ("rm vs sync 둘 다 다른 곳", _rm("s1", "z"), _sync("s1", "a", "s2", "z"), False),
    ("sync 목적지 vs rm(같은 스토리지 다른 쪽)", _sync("s1", "a", "s1", "b"), _rm("s1", "b/c"), True),
    ("sync vs sync 같은 경로 -- 충돌 아님", _sync("s1", "a", "s2", "x"), _sync("s1", "a", "s2", "x"), False),
    ("sync vs sync 조상 -- 충돌 아님", _sync("s1", "a", "s2", "x"), _sync("s1", "a/b", "s2", "x/y"), False),
    ("scan vs scan -- 충돌 아님", _scan("s1", "a"), _scan("s1", "a/b"), False),
    ("scan vs sync -- 충돌 아님", _scan("s1", "a"), _sync("s1", "a", "s2", "x"), False),
]


@pytest.mark.parametrize("desc, a, b, expected", CASES, ids=[c[0] for c in CASES])
def test_path_conflict_table(desc, a, b, expected):
    assert (_conflict(a, b) is not None) is expected
    # 대칭: 어느 쪽에서 물어도 같은 답이다(planner 는 늦게 온 쪽, stepper 는 관문의 잡에서 묻는다).
    assert (_conflict(b, a) is not None) is expected


def test_path_conflict_reports_the_overlapping_sides():
    hit = _conflict(_rm("s2", "x/y"), _sync("s1", "a", "s2", "x"))
    assert hit == (("target", "s2", "x/y"), ("destination", "s2", "x"))
    hit = _conflict(_sync("s1", "a/b", "s2", "q"), _rm("s1", "a"))
    assert hit == (("source", "s1", "a/b"), ("target", "s1", "a"))


def test_pv_address_and_direct_path_on_same_storage_compare_by_resolved_path():
    # PV 주소 요청은 제출이 (스토리지, PV 루트 아래 경로)로 풀어 payload 에 싣는다 -- 직접 경로 요청과 같은 키로 비교된다.
    pv_rm = _rm("gpfs1", "fs1/pvc-1234/results/run1")
    direct_sync = _sync("gpfs1", "fs1/pvc-1234", "nfs1", "backup/team-a")
    assert _conflict(pv_rm, direct_sync) is not None
    # 이웃 PV(다른 볼륨 디렉터리)는 겹치지 않는다.
    assert _conflict(pv_rm, _sync("gpfs1", "fs1/pvc-12345", "nfs1", "b")) is None


def test_overlap_sides_shape():
    assert overlap_sides("rm", storage="s1", target="a/b/") == (("target", "s1", "a/b"),)
    assert overlap_sides("scan", storage="s1", target="a") == (("target", "s1", "a"),)
    assert overlap_sides("sync", source_storage="s1", source="a", destination_storage="s2", destination="b") == (
        ("source", "s1", "a"), ("destination", "s2", "b"))
    # 스토리지 이름이 없거나 문자열이 아닌 쪽은 뺀다(그 이름으로는 실행되지 않는다).
    assert overlap_sides("rm", storage=None, target="a") == ()
    assert overlap_sides("rm", storage="", target="a") == ()
    assert overlap_sides("sync", source_storage=3, source="a", destination_storage="s2", destination="b") == (
        ("destination", "s2", "b"),)
    # 미지 연산은 rm·scan 처럼 (storage, target) 한 쪽(stepper._build_spec 의 op != "sync" 분기와 같다).
    assert overlap_sides("weird", storage="s1", target="a") == (("target", "s1", "a"),)


@pytest.mark.parametrize("op, destructive", [
    ("rm", True), ("scan", False), ("sync", False),
    ("weird", True), (None, True), ("", True),     # 미지 연산은 파괴적으로 본다(fail-closed)
])
def test_destructive_operation(op, destructive):
    assert destructive_operation(op) is destructive


def test_unknown_operation_conflicts_like_rm():
    assert path_conflict(destructive_operation("weird"), overlap_sides("weird", storage="s1", target="a"),
                         False, overlap_sides("scan", storage="s1", target="a/b")) is not None
