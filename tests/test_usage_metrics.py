"""사용량 분석 지표 정렬(usage_metrics, 2026-10-19) -- hot 비율 규칙이 포탈과 같은지(공용 예제), 정렬·모름 위치."""
import json
from pathlib import Path

import pytest

from dms.usage_metrics import HOT_AGE_MAX_DAYS, hot_ratio, metric_value, sort_by_metric

CASES = json.loads((Path(__file__).resolve().parent.parent / "frontend" / "src" / "features" / "usage"
                    / "hotRatioCases.json").read_text())


def test_hot_age_boundary_matches_the_portal_constant():
    # usageStats.HOT_AGE_MAX_DAYS 와 같은 값 -- 포탈 쪽 테스트도 같은 파일의 이 값을 단언한다.
    assert HOT_AGE_MAX_DAYS == CASES["hot_age_max_days"]


@pytest.mark.parametrize("case", CASES["cases"], ids=[c["name"] for c in CASES["cases"]])
def test_hot_ratio_matches_the_shared_cases(case):
    got = hot_ratio(case["buckets"])
    if case["expected"] is None:
        assert got is None
    else:
        assert got == pytest.approx(case["expected"])


def _row(target, *, total=None, files=None, atime=None, last="2026-10-01T00:00:00Z", storage="s1"):
    latest = {"total_bytes": total, "summary": {} if files is None else {"total_files": files},
              "time_histograms": {} if atime is None else {"atime": atime}}
    return {"storage_name": storage, "target": target, "last_scan_at": last, "latest": latest}


HOT = [{"min_age_days": 0, "max_age_days": 7, "bytes": 1}]
COLD = [{"min_age_days": 181, "max_age_days": 365, "bytes": 1}]
HALF = HOT + COLD


def test_metric_values_follow_the_screen_columns():
    r = _row("a", total=10, files=3, atime=HALF)
    assert (metric_value(r, "bytes"), metric_value(r, "files"), metric_value(r, "hot")) == (10, 3, 0.5)
    assert metric_value({"latest": None}, "bytes") is None
    assert metric_value(_row("b", total=True), "bytes") is None          # bool 은 수가 아니다(DB 신뢰 경계)
    with pytest.raises(ValueError):
        metric_value(r, "last_scan")


@pytest.mark.parametrize("order", ["desc", "asc"])
def test_unknown_values_sort_last_in_both_directions(order):
    rows = [_row("none"), _row("big", total=100), _row("small", total=1), _row("zero", total=0)]
    got = [r["target"] for r in sort_by_metric(rows, "bytes", order)]
    assert got[-1] == "none"
    assert got[:3] == (["big", "small", "zero"] if order == "desc" else ["zero", "small", "big"])


def test_ties_break_by_latest_scan_then_name():
    rows = [_row("b", total=5, last="2026-10-01T00:00:00Z"), _row("a", total=5, last="2026-10-01T00:00:00Z"),
            _row("c", total=5, last="2026-10-05T00:00:00Z")]
    assert [r["target"] for r in sort_by_metric(rows, "bytes", "desc")] == ["c", "a", "b"]
    assert [r["target"] for r in sort_by_metric(rows, "bytes", "asc")] == ["c", "a", "b"]


def test_hot_sort_uses_the_atime_ratio():
    rows = [_row("cold", atime=COLD), _row("hot", atime=HOT), _row("half", atime=HALF), _row("unknown")]
    assert [r["target"] for r in sort_by_metric(rows, "hot", "desc")] == ["hot", "half", "cold", "unknown"]


@pytest.mark.parametrize("order", ["desc", "asc"])
def test_sort_keyed_is_the_same_rule_with_precomputed_values(order):
    # 2026-10-20: 라우트는 창의 latest 포인트를 세우지 않고 값만 계산해 sort_keyed 에 넘긴다 -- 행에 latest 가 없어도
    # sort_by_metric 과 같은 순서여야 한다(모름 맨 뒤, 동률은 최근 스캔 → 이름). 0 은 값이다(모름이 아니다).
    from dms.usage_metrics import sort_keyed
    rows = [_row("b", total=5), _row("a", total=5), _row("c", total=5, last="2026-10-05T00:00:00Z"),
            _row("none"), _row("zero", total=0), _row("big", total=100), _row("x", storage="s0")]
    expected = [r["target"] for r in sort_by_metric(rows, "bytes", order)]
    heads = [{k: r[k] for k in ("storage_name", "target", "last_scan_at")} for r in rows]
    got = sort_keyed([(h, metric_value(r, "bytes")) for h, r in zip(heads, rows)], order)
    assert [h["target"] for h in got] == expected
    assert [h["target"] for h in got[-2:]] == ["x", "none"]          # 모름 둘은 맨 뒤 -- 같은 시각이면 이름(s0 < s1)


# ---- 2026-10-21: 스토리지 합계(hot_parts·maximal_targets·StorageTotals) ----

@pytest.mark.parametrize("case", CASES["cases"], ids=[c["name"] for c in CASES["cases"]])
def test_hot_parts_is_the_hot_ratio_rule_on_the_shared_cases(case):
    # 합계의 hot 재료(hot_parts)와 목록의 hot 비율(hot_ratio)은 판정 한 벌이다 -- 공용 예제 전부에서 비율이 같다.
    # 빈 트리(all_zero)는 재료가 (0, 0)(값)이고 비율만 정의되지 않는다.
    from dms.usage_metrics import hot_parts
    parts = hot_parts(case["buckets"])
    ratio = None if parts is None or parts[1] == 0 else parts[0] / parts[1]
    assert ratio == hot_ratio(case["buckets"])
    if case["name"] == "all_zero_is_unknown":
        assert parts == (0, 0)
    if case["name"] in ("empty", "missing_bytes_is_unknown", "bool_bytes_is_unknown"):
        assert parts is None


def _heads(*targets):
    return [{"target": t} for t in targets]


def test_maximal_targets_compare_path_components_not_string_prefixes():
    from dms.usage_metrics import maximal_targets
    heads = _heads("team", "team/sub1", "team-x", "team-x/a", "teamb", "a/b", "a", "x/y/z", "x/y")
    top, nested, invalid = maximal_targets(heads)
    # 'team' 은 'team-x'·'teamb' 의 조상이 아니다(문자열 접두), 'a/b' 는 뒤에 나온 'a' 의 하위다(순서 무관)
    assert [h["target"] for h in top] == ["team", "team-x", "teamb", "a", "x/y"]
    assert (nested, invalid) == (4, 0)


def test_maximal_targets_count_tampered_targets_without_using_them_as_ancestors():
    # DB 가 신뢰 경계(create_job 은 무검증 INSERT) -- 모양이 틀린 target 은 세기만 하고 조상으로도 쓰지 않는다.
    from dms.usage_metrics import maximal_targets
    heads = _heads("", ".", "/abs", "a/../b", "c//d", "c/", None, "x\x00y", "c/d", 7)
    top, nested, invalid = maximal_targets(heads)
    assert [h["target"] for h in top] == ["c/d"]                        # 'c/' 는 조상이 아니다
    assert (nested, invalid) == (0, 9)


def _digest(*, files=None, atime=None, size=None, summary_extra=None, broken=None):
    summary = {} if files is None else {"total_files": files}
    summary.update(summary_extra or {})
    d = {"summary": summary, "time_histograms": {} if atime is None else {"atime": atime}}
    if size is not None:
        d["file_size_histogram"] = size
    if broken is not None:
        d["broken_paths_total"] = broken
    return d


def test_storage_totals_null_is_not_zero():
    from dms.usage_metrics import StorageTotals
    empty = StorageTotals().result()
    for k in ("total_bytes", "total_files", "total_directories", "total_symlinks", "total_other", "total_entries",
              "scan_errors", "invalid_name_entries", "broken_paths_total"):
        assert empty[k] == {"sum": None, "known": 0, "unknown": 0}, k   # 기록 없음 ≠ 0 B
    assert empty["hot_ratio"] == {"value": None, "hot_bytes": None, "total_bytes": None, "known": 0, "unknown": 0}
    assert empty["file_size_histogram"] == {"buckets": [], "known": 0, "unknown": 0}
    assert (empty["oldest_scan_at"], empty["newest_scan_at"]) == (None, None)

    # 빈 트리: 0 은 값이다 -- known 에 들고 sum 0. hot 은 재료 (0, 0) 이라 known 이되 비율만 None.
    z = StorageTotals()
    z.add({"last_scan_at": "2026-01-01T00:00:00Z"}, 0,
          _digest(files=0, broken=0, atime=[{"min_age_days": 0, "max_age_days": 1, "bytes": 0}]))
    zr = z.result()
    assert zr["total_bytes"] == {"sum": 0, "known": 1, "unknown": 0}
    assert zr["total_files"] == {"sum": 0, "known": 1, "unknown": 0}
    assert zr["broken_paths_total"] == {"sum": 0, "known": 1, "unknown": 0}
    assert zr["hot_ratio"] == {"value": None, "hot_bytes": 0, "total_bytes": 0, "known": 1, "unknown": 0}

    # 리포트 없음(못 읽음·지연): 리포트 유래 값은 전부 unknown +1, 실 사용량은 호출자가 넘긴 대체값(bytes_count)
    u = StorageTotals()
    u.add({}, None, None)
    u.add({}, 10, _digest(files=3))
    ur = u.result()
    assert ur["total_bytes"] == {"sum": 10, "known": 1, "unknown": 1}
    assert ur["total_files"] == {"sum": 3, "known": 1, "unknown": 1}
    assert ur["total_directories"] == {"sum": None, "known": 0, "unknown": 2}       # 키 없는 구형 요약도 모름
    assert ur["hot_ratio"]["unknown"] == 2 and ur["hot_ratio"]["value"] is None


def test_storage_totals_reject_values_that_are_not_counts():
    # 투영(_numbers)은 유한 float·음수도 통과시킨다 -- 합에는 정수 ≥0 만 든다(음수 하나가 합을 조용히 깎지 않게).
    from dms.usage_metrics import StorageTotals
    acc = StorageTotals()
    acc.add({}, -5, _digest(files=1.5, summary_extra={"total_directories": -1, "scan_errors": True},
                            atime=[{"min_age_days": 0, "max_age_days": 7, "bytes": 10},
                                   {"min_age_days": 181, "max_age_days": 365, "bytes": -4}]))
    r = acc.result()
    for k in ("total_bytes", "total_files", "total_directories", "scan_errors"):
        assert r[k] == {"sum": None, "known": 0, "unknown": 1}, k
    assert r["hot_ratio"]["known"] == 0 and r["hot_ratio"]["unknown"] == 1


HOT100 = [{"min_age_days": 0, "max_age_days": 7, "bytes": 100}]
COLD900 = [{"min_age_days": 181, "max_age_days": 365, "bytes": 900}]


def test_storage_totals_hot_ratio_is_bytes_weighted():
    # A 100 B 전부 hot, B 900 B 전부 cold -> 0.1(비율의 평균이면 0.5 -- 1 TB 디렉터리와 1 B 디렉터리가 같은 무게가 된다)
    from dms.usage_metrics import StorageTotals
    acc = StorageTotals()
    acc.add({}, 100, _digest(atime=HOT100))
    acc.add({}, 900, _digest(atime=COLD900))
    acc.add({}, 7, _digest(atime=[{"min_age_days": 30, "bytes": 5}]))            # 판정 불가 -> unknown, 합에서 빠짐
    h = acc.result()["hot_ratio"]
    assert h["value"] == pytest.approx(0.1)
    assert (h["hot_bytes"], h["total_bytes"], h["known"], h["unknown"]) == (100, 1000, 2, 1)


def _size(counts, *, lows=(0, 4097, 65537), label=True):
    out = []
    for i, (lo, c) in enumerate(zip(lows, counts)):
        b = {"lower_inclusive": lo, "count": c}
        if i + 1 < len(lows):
            b["upper_inclusive"] = lows[i + 1] - 1
        if label:
            b["bucket"] = f"[{lo},{b.get('upper_inclusive', 'INF')}]"
        out.append(b)
    return out


def test_storage_totals_size_histogram_sums_only_the_newest_signature():
    from dms.usage_metrics import StorageTotals
    acc = StorageTotals()
    acc.add({}, 1, _digest(size=_size([1, 2, 3])))                 # 가장 최근(첫) 유효 리포트 = 기준 서명
    acc.add({}, 1, _digest(size=_size([10, 20, 30], label=False)))  # 같은 서명(라벨 없음) -- 더한다
    acc.add({}, 1, _digest(size=_size([5, 5, 5], lows=(0, 1025, 65537))))   # 경계가 다르다 -- 모름
    acc.add({}, 1, _digest(size=_size([1, -1, 1])))                # 깨진 개수 -- 모름
    acc.add({}, 1, _digest(size=[]))                               # 빈 히스토그램 -- 모름
    acc.add({}, 1, _digest())                                      # 키 없음 -- 모름
    fs = acc.result()["file_size_histogram"]
    assert (fs["known"], fs["unknown"]) == (2, 4)
    assert fs["buckets"] == [
        {"bucket": "[0,4096]", "lower_inclusive": 0, "upper_inclusive": 4096, "count": 11},
        {"bucket": "[4097,65536]", "lower_inclusive": 4097, "upper_inclusive": 65536, "count": 22},
        {"bucket": "[65537,INF]", "lower_inclusive": 65537, "count": 33}]          # 마지막 열린 구간은 상한 키 없음
    # 첫 리포트가 깨져 있으면 기준은 그다음 유효 리포트다(깨진 리포트가 기준을 정하지 않는다)
    acc = StorageTotals()
    acc.add({}, 1, _digest(size=[{"count": 1}]))
    acc.add({}, 1, _digest(size=_size([4, 0, 0], label=False)))
    fs = acc.result()["file_size_histogram"]
    assert (fs["known"], fs["unknown"]) == (1, 1)
    assert fs["buckets"][0] == {"lower_inclusive": 0, "upper_inclusive": 4096, "count": 4}   # 투영이 버린 라벨은 키 없음


def test_storage_totals_scan_age_range_is_of_the_summed_set():
    # 시점 범위는 더한 대상(호출자가 넘긴 최상위)들의 최신 스캔 시각이다 -- 하위 대상의 더 새 시각은 들어가지 않는다
    from dms.usage_metrics import StorageTotals, maximal_targets
    heads = [{"target": "team/sub1", "last_scan_at": "2026-10-06T00:00:00Z"},
             {"target": "team", "last_scan_at": "2026-10-02T00:00:00Z"},
             {"target": "other", "last_scan_at": "2026-08-16T00:00:00Z"}]
    top, nested, _ = maximal_targets(heads)
    acc = StorageTotals()
    for h in top:
        acc.add(h, 1, None)
    r = acc.result()
    assert nested == 1
    assert (r["oldest_scan_at"], r["newest_scan_at"]) == ("2026-08-16T00:00:00Z", "2026-10-02T00:00:00Z")


def test_storage_totals_invalid_name_entries_is_its_own_counter():
    # 2026-10-26(포크 ae781bd, 사용자 결정 「카운터만 둠」): 이름이 엄격 UTF-8 이 아닌 항목 수는 broken 과 따로 더한다. 구형
    # 요약(키 없음)·수상한 값은 unknown -- 0 으로 접지 않는다. summary.total_bytes 는 합계의 실 사용량 원천이 아니다(호출자 값).
    from dms.usage_metrics import StorageTotals
    acc = StorageTotals()
    acc.add({}, 100, _digest(files=1, broken=2, summary_extra={"invalid_name_entries": 5, "total_bytes": 999}))
    acc.add({}, 0, _digest(files=0, broken=0, summary_extra={"invalid_name_entries": 0}))
    acc.add({}, 7, _digest(files=1))                                                  # 구형 리포트
    for bad in (-1, 1.5, True):
        acc.add({}, 1, _digest(files=1, summary_extra={"invalid_name_entries": bad}))
    r = acc.result()
    assert r["invalid_name_entries"] == {"sum": 5, "known": 2, "unknown": 4}
    assert r["broken_paths_total"] == {"sum": 2, "known": 2, "unknown": 4}
    assert r["total_bytes"] == {"sum": 100 + 0 + 7 + 3, "known": 6, "unknown": 0}


# ---- 2026-10-30: 소속별 묶음(MaximalIndex·GroupTotals -- routes_usage.usage_breakdown, 리뷰 [4]) ----

def test_maximal_index_split_is_maximal_targets_on_every_subset():
    # 묶음마다 maximal_targets 를 다시 부르지 않고 창에서 한 번 판정한 조상 관계로 같은 결과를 낸다 -- 겹친 경로·모양이 틀린 대상
    # (조상으로 쓰지 않는다)·문자열 접두(team ⊄ team-x)를 섞은 무작위 부분 집합(창 순서 유지)으로 대조.
    import random
    from dms.usage_metrics import MaximalIndex, maximal_targets
    rng = random.Random(1030)
    vocab = ["team", "team/a", "team/a/b", "team-x", "team-x/a", "teamb", "a", "a/b/c", "a/b", "x/y/z", "", ".", "/abs",
             "c//d", "c/", "c/d", "c", None, "q/../r", "p/q/r/s", "p/q", "p"]
    for _ in range(300):
        targets = rng.sample(vocab, rng.randint(0, len(vocab)))
        heads = [{"job_id": f"j{i}", "target": t} for i, t in enumerate(targets)]
        index = MaximalIndex(heads)
        for _ in range(5):
            sub = [h for h in heads if rng.random() < 0.6]
            top, nested, invalid = index.split(sub)
            want_top, want_nested, want_invalid = maximal_targets(sub)
            assert ([h["job_id"] for h in top], nested, invalid) == ([h["job_id"] for h in want_top], want_nested,
                                                                     want_invalid), [h["target"] for h in sub]
        assert index.split(heads)[1:] == maximal_targets(heads)[1:]


def test_group_totals_are_the_storage_totals_row_fields():
    # 소속별 행 값 = 그 묶음 필터 합계(오라클) -- 묶음 누적기(GroupTotals, 대상마다 head_contribution 한 번)와 StorageTotals 가 같은
    # 칸에서 같은 값을 낸다(null≠0·정수 ≥0 만·hot bytes 가중·시점 범위). 응답 키 순서(storage-totals 바이트)도 그대로다.
    import random
    from dms.usage_metrics import GroupTotals, StorageTotals, head_contribution
    rng = random.Random(30)
    weird = [None, 0, 3, 17, -1, 1.5, True, "9"]
    for _ in range(200):
        st, gt = StorageTotals(), GroupTotals()
        for _ in range(rng.randint(0, 6)):
            head = {"last_scan_at": rng.choice([None, 7, "2026-10-01T00:00:00Z", "2026-10-30T00:00:00Z",
                                                "2026-09-15T00:00:00Z"])}
            atime = rng.choice([None, HOT100, COLD900, HOT100 + COLD900, [{"min_age_days": 30, "bytes": 5}],
                                [{"min_age_days": 0, "max_age_days": 7, "bytes": rng.choice(weird)}], []])
            digest = rng.choice([None, "x", {"summary": "x"},
                                 _digest(files=rng.choice(weird), atime=atime, broken=rng.choice(weird),
                                         size=rng.choice([None, _size((1, 2, 3))]))])
            total = rng.choice(weird)
            st.add(head, total, digest)
            gt.add(head_contribution(head, total, digest))
        s, g = st.result(), gt.result()
        assert g == {k: s[k] for k in ("total_bytes", "total_files", "hot_ratio", "oldest_scan_at", "newest_scan_at")}
        assert list(s) == ["total_bytes", "total_files", "total_directories", "total_symlinks", "total_other",
                           "total_entries", "scan_errors", "invalid_name_entries", "broken_paths_total", "hot_ratio",
                           "file_size_histogram", "oldest_scan_at", "newest_scan_at"]
        assert list(g) == ["total_bytes", "total_files", "hot_ratio", "oldest_scan_at", "newest_scan_at"]
