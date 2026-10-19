"""사용량 분석 목록의 지표 정렬(2026-10-19 사용자 요청: 「사용량 분석에서 실사용량, 파일수, hot비율 등을 오름/내림차순
정렬할수있게」) -- 순수 함수(DB·아티팩트를 모른다).

정렬은 **서버**가 한다: 목록은 limit(기본 100)으로 잘려 오므로 화면이 받은 묶음만 정렬하면 「최근 스캔된 N개 안에서의
순위」가 되어, 그 밖의 큰 디렉터리가 상위에 나오지 않는다. 그런데 정렬 값은 SQL 열이 아니다 -- 화면의 「파일 수」는
리포트 요약의 total_files(잡 행의 files_count 는 디렉터리까지 센 항목 수라 다르다), hot 비율은 리포트의 atime 히스토그램에서
나온다. 그래서 routes_usage 가 필터에 맞는 대상 창(최근 스캔 순 상한 _EXPORT_LIMIT)의 최신 스캔 포인트를 리포트 요약
캐시(scan_digests)로 만들고, 여기서 화면과 **같은 값**으로 정렬한 뒤 limit 만큼 자른다.

hot 비율 규칙은 포탈 usageStats.hotRatio 의 거울이다(목록·타일·CSV 가 쓰는 화면 규칙). 두 벌이 갈라지지 않게 같은 예제
파일(frontend/src/features/usage/hotRatioCases.json)을 양쪽 테스트가 함께 읽는다.

모름(None)은 방향과 상관없이 **맨 뒤**다(null ≠ 0 -- 0 으로 접으면 오름차순 맨 위에 「모름」이 「가장 작다」로 선다).
동률은 최근 스캔이 늦은 것 → storage_name → target 순(결정적).
"""

HOT_AGE_MAX_DAYS = 180
# 화면 열과 같은 이름(목록 sort 파라미터). last_scan·scans 는 SQL 이 정렬한다(repositories.data_jobs.scan_targets).
METRIC_SORTS = ("bytes", "files", "hot")
SQL_SORTS = ("last_scan", "scans")
SORT_PATTERN = "^(" + "|".join(SQL_SORTS + METRIC_SORTS) + ")$"


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v


def hot_ratio(buckets) -> "float | None":
    """나이 ≤HOT_AGE_MAX_DAYS 버킷 bytes / 전체 bytes(usageStats.hotRatio 와 같은 규칙). 전체 0·빈 히스토그램 = None.
    bytes 가 실린 버킷의 hot/cold 를 가를 근거(max_age_days)가 없으면 None -- 단 하한이 180일을 넘는 구간(dscan 의 마지막
    [3651d,INF])은 상한이 없어도 확실히 cold 다."""
    if not isinstance(buckets, list) or not buckets:
        return None
    hot = total = 0
    for b in buckets:
        if not isinstance(b, dict) or not _num(b.get("bytes")):
            return None
        known_cold = _num(b.get("min_age_days")) and b["min_age_days"] > HOT_AGE_MAX_DAYS
        if b["bytes"] > 0 and not _num(b.get("max_age_days")) and not known_cold:
            return None
        total += b["bytes"]
        if _num(b.get("max_age_days")) and b["max_age_days"] <= HOT_AGE_MAX_DAYS:
            hot += b["bytes"]
    return None if total == 0 else hot / total


def metric_value(row: dict, sort: str) -> "float | None":
    """목록 행(latest 포인트를 실은) -> 정렬 값. 화면 열과 같은 원천: 실 사용량 = latest.total_bytes, 파일 수 =
    latest.summary.total_files, hot = latest.time_histograms.atime 의 hot_ratio."""
    p = row.get("latest") or {}
    if sort == "bytes":
        v = p.get("total_bytes")
    elif sort == "files":
        v = (p.get("summary") or {}).get("total_files")
    elif sort == "hot":
        v = hot_ratio((p.get("time_histograms") or {}).get("atime"))
    else:
        raise ValueError(f"not a metric sort: {sort!r}")
    return v if _num(v) else None


def sort_by_metric(rows: list, sort: str, order: str) -> list:
    """지표 정렬(모름은 맨 뒤, 동률은 최근 스캔 늦은 것 → 이름). 안정 정렬을 두 번 건다: 먼저 동률 키, 다음에 값."""
    known = [r for r in rows if metric_value(r, sort) is not None]
    unknown = [r for r in rows if metric_value(r, sort) is None]
    tie = lambda r: (r.get("storage_name") or "", r.get("target") or "")   # noqa: E731
    for group in (known, unknown):
        group.sort(key=tie)
        group.sort(key=lambda r: r.get("last_scan_at") or "", reverse=True)
    known.sort(key=lambda r: metric_value(r, sort), reverse=(order == "desc"))
    return known + unknown
