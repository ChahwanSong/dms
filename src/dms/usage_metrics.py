"""사용량 분석 목록의 지표 정렬(2026-10-19 사용자 요청: 「사용량 분석에서 실사용량, 파일수, hot비율 등을 오름/내림차순
정렬할수있게」) -- 순수 함수(DB·아티팩트를 모른다).

정렬은 **서버**가 한다: 목록은 limit(기본 100)으로 잘려 오므로 화면이 받은 묶음만 정렬하면 「최근 스캔된 N개 안에서의
순위」가 되어, 그 밖의 큰 디렉터리가 상위에 나오지 않는다. 그런데 정렬 값은 SQL 열이 아니다 -- 화면의 「파일 수」는
리포트 요약의 total_files(잡 행의 files_count 는 디렉터리까지 센 항목 수라 다르다), hot 비율은 리포트의 atime 히스토그램에서
나온다. 그래서 routes_usage 가 필터에 맞는 대상 창(최근 스캔 순 상한 _WINDOW_LIMIT = 1만 -- 포탈은 스토리지 탭마다
storage 를 실어 창도 그 스토리지 안이다, 2026-10-20)의 최신 스캔마다 리포트 요약 캐시(scan_digests)로 정렬 **값만** 만들고,
여기서 화면과 **같은 값**으로 정렬한 뒤 limit 만큼 자른다(sort_keyed -- 창 전체의 포인트를 메모리에 세우지 않는다).

hot 비율 규칙은 포탈 usageStats.hotRatio 의 거울이다(목록·타일·CSV 가 쓰는 화면 규칙). 두 벌이 갈라지지 않게 같은 예제
파일(frontend/src/features/usage/hotRatioCases.json)을 양쪽 테스트가 함께 읽는다.

모름(None)은 방향과 상관없이 **맨 뒤**다(null ≠ 0 -- 0 으로 접으면 오름차순 맨 위에 「모름」이 「가장 작다」로 선다).
동률은 최근 스캔이 늦은 것 → storage_name → target 순(결정적).

스토리지 합계(2026-10-21 사용자 요청: 「스토리지별 사용량 및 정보들의 총합」 -- routes_usage.storage_totals)의 순수 규칙도
여기다: 최상위 대상 고르기(maximal_targets -- 경로 성분 단위), 지표마다 sum·known·unknown 누적(StorageTotals). hot 은
hot_parts 를 더한 bytes 가중이고 hot_ratio 와 같은 판정 한 벌이다(hot_ratio 가 hot_parts 를 부른다).
"""
import posixpath

HOT_AGE_MAX_DAYS = 180
# 화면 열과 같은 이름(목록 sort 파라미터). last_scan·scans 는 SQL 이 정렬한다(repositories.data_jobs.scan_target_heads).
METRIC_SORTS = ("bytes", "files", "hot")
SQL_SORTS = ("last_scan", "scans")
SORT_PATTERN = "^(" + "|".join(SQL_SORTS + METRIC_SORTS) + ")$"


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v


def hot_parts(buckets) -> "tuple | None":
    """hot 비율의 재료 (나이 ≤HOT_AGE_MAX_DAYS 버킷 bytes, 전체 bytes). 판정 불가 = None, 빈 트리 = (0, 0)(값이다 -- 비율만
    정의되지 않는다). 스토리지 합계(2026-10-21)가 대상마다 두 수를 더해 bytes 가중 비율을 낸다 -- 대상별 비율의 평균은 1 B
    디렉터리와 1 TB 디렉터리를 같은 무게로 센다. 판정은 hot_ratio 그대로(한 벌 -- hot_ratio 가 이것을 부른다)."""
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
    return hot, total


def hot_ratio(buckets) -> "float | None":
    """나이 ≤HOT_AGE_MAX_DAYS 버킷 bytes / 전체 bytes(usageStats.hotRatio 와 같은 규칙). 전체 0·빈 히스토그램 = None.
    bytes 가 실린 버킷의 hot/cold 를 가를 근거(max_age_days)가 없으면 None -- 단 하한이 180일을 넘는 구간(dscan 의 마지막
    [3651d,INF])은 상한이 없어도 확실히 cold 다. 판정 몸통은 hot_parts(스토리지 합계와 한 벌)."""
    p = hot_parts(buckets)
    return None if p is None or p[1] == 0 else p[0] / p[1]


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


def sort_keyed(pairs: list, order: str) -> list:
    """(행, 정렬 값) 쌍 -> 정렬된 행(모름 None 은 맨 뒤, 동률은 최근 스캔 늦은 것 → 이름). 안정 정렬을 두 번 건다: 먼저
    동률 키, 다음에 값. 값을 따로 받는 이유(2026-10-20): 라우트는 창(최대 1만 타깃)의 latest 포인트를 만들지 않고 값만
    계산한다 -- 행은 storage_name·target·last_scan_at 만 있으면 된다."""
    known = [p for p in pairs if p[1] is not None]
    unknown = [p for p in pairs if p[1] is None]
    tie = lambda p: (p[0].get("storage_name") or "", p[0].get("target") or "")   # noqa: E731
    for group in (known, unknown):
        group.sort(key=tie)
        group.sort(key=lambda p: p[0].get("last_scan_at") or "", reverse=True)
    known.sort(key=lambda p: p[1], reverse=(order == "desc"))
    return [p[0] for p in known + unknown]


def sort_by_metric(rows: list, sort: str, order: str) -> list:
    """latest 를 실은 행의 지표 정렬 -- sort_keyed 에 화면 값(metric_value)을 붙여 넘긴다(규칙은 한 벌)."""
    return sort_keyed([(r, metric_value(r, sort)) for r in rows], order)


# ---- 스토리지 합계(2026-10-21, routes_usage.storage_totals) ----

def _plain_target(t) -> bool:
    """정규화된 스토리지 상대 경로(domain.validate_relative_path 가 저장하는 모양)인가. DB 가 신뢰 경계라(create_job 은
    무검증 INSERT) 모양이 틀린 행('', '.', '/x', 'a/../b', 'a//b', 'a/', 비문자열)은 합계에도 조상 판정에도 쓰지 않고
    센다 -- 'a/' 를 조상으로 믿으면 'a/b' 가 빠지고, '' 를 조상으로 믿으면 스토리지 전체가 빠진다."""
    return (isinstance(t, str) and t not in ("", ".") and not t.startswith("/") and "\x00" not in t
            and posixpath.normpath(t) == t and ".." not in t.split("/"))


def maximal_targets(heads: list) -> tuple:
    """한 스토리지의 머리 행들 -> (조상 대상이 없는 머리들(순서 유지), 하위라 빠진 수, 모양이 틀려 빠진 수). 대상 경로가
    겹치면(team ⊃ team/sub1) 단순 합은 하위를 두 번 센다(테스트베드 실측 1.94배). 조상 판정은 경로 **성분** 단위다 --
    문자열 접두('team' 이 'team-x' 를 문다)도, 정렬 인접 비교('-' < '/' 라 'team-x' 가 'team' 과 'team/sub1' 사이에 선다)도
    틀린다. 성분마다 집합 조회 -- 합성 10만 대상 0.08초."""
    plain = [h for h in heads if _plain_target(h.get("target"))]
    names = {h["target"] for h in plain}
    top = []
    for h in plain:
        parts = h["target"].split("/")
        if not any("/".join(parts[:i]) in names for i in range(1, len(parts))):
            top.append(h)
    return top, len(plain) - len(top), len(heads) - len(plain)


# 합계가 더하는 리포트 요약 정수(summary 의 키). 응답의 지표 순서이기도 하다.
TOTAL_SUMMARY_KEYS = ("total_files", "total_directories", "total_symlinks", "total_other", "total_entries",
                      "scan_errors")


def _count(v) -> bool:
    """더할 수 있는 개수·용량인가(int, bool 아님, ≥0) -- 실 사용량 규칙(routes_scan_paths._total_bytes)과 같은 엄격함.
    투영은 유한 float·음수도 통과시키므로(_numbers) 여기서 한 번 더 거른다: 음수 하나가 합을 조용히 깎으면 안 된다."""
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def _size_signature(buckets) -> "tuple | None":
    """파일 크기 히스토그램 -> ((lower, upper|None)…, [count…]). 하나라도 깨지면(비 dict·개수나 하한이 정수가 아님·빈
    목록) None -- 그 리포트의 크기 분포는 모름이다. upper None = 마지막 열린 구간(투영이 null 상한 키를 버린다)."""
    if not isinstance(buckets, list) or not buckets:
        return None
    sig, counts = [], []
    for b in buckets:
        if not isinstance(b, dict) or not _count(b.get("count")) or not _count(b.get("lower_inclusive")):
            return None
        up = b.get("upper_inclusive")
        if up is not None and not _count(up):
            return None
        sig.append((b["lower_inclusive"], up))
        counts.append(b["count"])
    return tuple(sig), counts


def _strict_hot_parts(time_histograms) -> "tuple | None":
    """atime 히스토그램의 hot_parts -- 단 버킷 bytes 가 전부 정수 ≥0 일 때만(합계는 정수 합이다; 목록의 hot 열은 같은
    판정에 float·음수까지 받지만, 그건 변조 리포트에서만 생기는 모양이다 -- 음수 버킷이 합계의 분모를 깎으면 안 된다)."""
    atime = time_histograms.get("atime") if isinstance(time_histograms, dict) else None
    parts = hot_parts(atime)
    if parts is None or not all(_count(b["bytes"]) for b in atime):
        return None
    return parts


class StorageTotals:
    """최상위 대상들의 최신 요약 합(순수 누적기). 호출자는 add 를 머리 순서(최근 스캔 순)로 부른다.

    - 지표마다 [합, known, unknown] -- 모름은 0 으로 접지 않고 센다(null≠0). known 이 0 이면 sum None(기록 없음 ≠ 0 B).
      빈 트리의 0 은 값이다(known 에 든다).
    - hot 은 bytes 가중(대상마다 hot_parts 를 더해 Σhot/Σ전체) -- 비율의 평균이 아니다. 빈 트리 (0, 0) 은 known 이고
      기여는 0, Σ전체가 0 이면 value 만 None.
    - 크기 분포는 첫(= 가장 최근 스캔) 유효 리포트의 구간 서명과 같은 리포트만 구간별로 더한다 -- 경계가 다른 리포트를
      더하면 겹치는 구간이 섞인 거짓 분포다. 서버에 dscan 경계를 하드코딩하지 않는 이유: 포크가 경계를 바꾸면 전부 모름이
      된다. 라벨·경계는 기준 리포트의 것을 그대로 싣는다(마지막 열린 구간은 upper_inclusive 키 없음 -- 투영 모양 그대로,
      라벨도 투영이 버렸으면 키 없음).
    - 스캔 시점 범위(oldest/newest)는 더한 대상들의 최신 스캔 시각이다 -- 하위가 상위보다 새로 스캔돼도 합계엔 상위의
      (더 오래된) 값이 들어가므로 화면이 시점 범위를 함께 말한다."""

    METRICS = ("total_bytes", *TOTAL_SUMMARY_KEYS, "broken_paths_total")

    def __init__(self):
        self._m = {k: [0, 0, 0] for k in self.METRICS}
        self._hot = [0, 0, 0, 0]               # Σhot, Σ전체, known, unknown
        self._size_sig = None
        self._size_buckets: list = []
        self._size = [0, 0]                    # known, unknown
        self._oldest = self._newest = None

    def _add(self, key: str, v) -> None:
        m = self._m[key]
        if _count(v):
            m[0] += v
            m[1] += 1
        else:
            m[2] += 1

    def add(self, head: dict, total_bytes, digest: "dict | None") -> None:
        """대상 하나: head = 머리 행(last_scan_at), total_bytes = 목록 열과 같은 실 사용량(리포트를 못 읽었으면 DB 의
        bytes_count 대체 -- 호출자가 정한다), digest = 리포트 요약(못 읽음·지연이면 None -- 리포트 유래 값은 전부 모름)."""
        t = head.get("last_scan_at")
        if isinstance(t, str):
            self._oldest = t if self._oldest is None or t < self._oldest else self._oldest
            self._newest = t if self._newest is None or t > self._newest else self._newest
        self._add("total_bytes", total_bytes)
        d = digest if isinstance(digest, dict) else {}
        summary = d.get("summary") if isinstance(d.get("summary"), dict) else {}
        for k in TOTAL_SUMMARY_KEYS:
            self._add(k, summary.get(k))
        self._add("broken_paths_total", d.get("broken_paths_total"))
        hp = _strict_hot_parts(d.get("time_histograms"))
        if hp is None:
            self._hot[3] += 1
        else:
            self._hot[0] += hp[0]
            self._hot[1] += hp[1]
            self._hot[2] += 1
        sc = _size_signature(d.get("file_size_histogram"))
        if sc is None or (self._size_sig is not None and sc[0] != self._size_sig):
            self._size[1] += 1
            return
        if self._size_sig is None:
            self._size_sig = sc[0]
            for b, (lo, up) in zip(d["file_size_histogram"], sc[0]):
                label = b.get("bucket")
                self._size_buckets.append({**({"bucket": label} if isinstance(label, str) else {}),
                                           "lower_inclusive": lo,
                                           **({"upper_inclusive": up} if up is not None else {}), "count": 0})
        for b, c in zip(self._size_buckets, sc[1]):
            b["count"] += c
        self._size[0] += 1

    def result(self) -> dict:
        def metric(m):
            return {"sum": m[0] if m[1] > 0 else None, "known": m[1], "unknown": m[2]}
        hot, total, hot_known, hot_unknown = self._hot
        known = hot_known > 0
        return {**{k: metric(self._m[k]) for k in self.METRICS},
                "hot_ratio": {"value": hot / total if known and total > 0 else None,
                              "hot_bytes": hot if known else None, "total_bytes": total if known else None,
                              "known": hot_known, "unknown": hot_unknown},
                "file_size_histogram": {"buckets": [dict(b) for b in self._size_buckets] if self._size[0] > 0 else [],
                                        "known": self._size[0], "unknown": self._size[1]},
                "oldest_scan_at": self._oldest, "newest_scan_at": self._newest}
