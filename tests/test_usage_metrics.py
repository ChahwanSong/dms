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
