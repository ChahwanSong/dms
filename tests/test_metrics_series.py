from dms.metrics_series import (bucket_chars_for, build_node_points,
                                clamp_window_hours, duration_histogram)


def _sample(at, **os_fields):
    return {"reported_at": at, "report": {"os": os_fields}}


def test_clamp_window_hours():
    assert clamp_window_hours(None, retention_days=30) == 24     # 기본 24h
    assert clamp_window_hours(24, retention_days=30) == 24
    assert clamp_window_hours(1000, retention_days=30) == 720    # 보존 상한(설계 §6-2)
    assert clamp_window_hours(0, retention_days=30) == 1         # 하한 1h


def test_bucket_chars_for():
    assert bucket_chars_for(1) == 13      # "YYYY-MM-DDTHH" -- 시간 버킷
    assert bucket_chars_for(48) == 13
    assert bucket_chars_for(49) == 10     # "YYYY-MM-DD" -- 7일 창을 시간으로 쪼개면
    assert bucket_chars_for(168) == 10    # 막대 168개가 나와 읽히지 않는다


def test_points_carry_load_mem_and_disks():
    pts = build_node_points([_sample(
        "2026-08-09T00:00:00Z", load1=0.5, load5=0.4, load15=0.3,
        memory_total_kb=100, memory_available_kb=25,
        disks=[{"storage_name": "s1", "total_bytes": 200, "used_bytes": 50}])])
    # cpu_used_pct·cpu_iowait_pct(2026-10-16) 가 없는 구형 에이전트 보고 -- 0 이 아니라 None(모름)
    assert pts == [{
        "at": "2026-08-09T00:00:00Z", "cpu_used_pct": None, "cpu_iowait_pct": None,
        "load1": 0.5, "load5": 0.4, "load15": 0.3,
        "mem_used_pct": 75.0, "net_rx_bps": None, "net_tx_bps": None,
        "disks": [{"storage_name": "s1", "used_pct": 25.0}]}]


def test_points_pass_cpu_utilisation_through():
    # CPU 사용률은 에이전트 러너가 /proc/stat 차분으로 계산해 싣는다(runner.cpu_utilisation) -- 여기는 통과만.
    # load(213) 는 코어 수를 넘을 수 있는 별개 지표라 사용률과 섞이지 않는다.
    pts = build_node_points([
        _sample("2026-08-09T00:00:00Z", cpu_used_pct=42.5, cpu_iowait_pct=3.1, load1=213.0),
        _sample("2026-08-09T00:01:00Z", cpu_used_pct=0, cpu_iowait_pct=0.0),
    ])
    assert (pts[0]["cpu_used_pct"], pts[0]["cpu_iowait_pct"], pts[0]["load1"]) == (42.5, 3.1, 213.0)
    # 0 은 정상값(유휴 노드) -- None 으로 뭉개지 않는다
    assert pts[1]["cpu_used_pct"] == 0.0 and pts[1]["cpu_iowait_pct"] == 0.0


def test_cpu_utilisation_outside_0_100_or_non_numeric_is_none():
    # DB 가 신뢰 경계: 범위 밖·문자열·bool·NaN 은 모름. 100 과 0 경계값은 통과.
    for bad in (100.1, -0.1, "42", True, float("nan"), float("inf"), None):
        pt = build_node_points([_sample("2026-08-09T00:00:00Z",
                                        cpu_used_pct=bad, cpu_iowait_pct=bad)])[0]
        assert pt["cpu_used_pct"] is None and pt["cpu_iowait_pct"] is None, bad
    pt = build_node_points([_sample("2026-08-09T00:00:00Z", cpu_used_pct=100, cpu_iowait_pct=0)])[0]
    assert (pt["cpu_used_pct"], pt["cpu_iowait_pct"]) == (100.0, 0.0)


def test_network_throughput_is_adjacent_diff():
    pts = build_node_points([
        _sample("2026-08-09T00:00:00Z", network_rx_bytes=1000, network_tx_bytes=0),
        _sample("2026-08-09T00:01:00Z", network_rx_bytes=7000, network_tx_bytes=600),
    ])
    # 첫 포인트는 이전 샘플이 없어 null, 둘째는 (7000-1000)/60초 = 100 B/s
    assert [p["net_rx_bps"] for p in pts] == [None, 100.0]
    assert [p["net_tx_bps"] for p in pts] == [None, 10.0]


def test_counter_reset_yields_null_not_negative():
    pts = build_node_points([
        _sample("2026-08-09T00:00:00Z", network_rx_bytes=9000, network_tx_bytes=0),
        _sample("2026-08-09T00:01:00Z", network_rx_bytes=100, network_tx_bytes=0),
    ])
    # 감소 = 리부팅 카운터 리셋(설계 §3) -- 음수 대역폭을 그리느니 빈 구간이 정직하다
    assert pts[1]["net_rx_bps"] is None


def test_broken_sample_is_skipped_and_diff_spans_the_gap():
    pts = build_node_points([
        _sample("2026-08-09T00:00:00Z", network_rx_bytes=0),
        {"reported_at": "2026-08-09T00:01:00Z", "report": None},         # os 없음
        {"reported_at": "bad-timestamp", "report": {"os": {}}},          # 시각 불가
        _sample("2026-08-09T00:02:00Z", network_rx_bytes=1200),
    ])
    # 깨진 샘플 둘만 빠지고(설계 §3 fail-soft) 차분은 남은 두 샘플 간격(120초)으로
    assert [p["at"] for p in pts] == ["2026-08-09T00:00:00Z", "2026-08-09T00:02:00Z"]
    assert pts[1]["net_rx_bps"] == 10.0


def test_missing_fields_become_none_not_crash():
    pts = build_node_points([_sample("2026-08-09T00:00:00Z")])
    assert pts[0]["load1"] is None and pts[0]["mem_used_pct"] is None
    assert pts[0]["disks"] == []


def test_bool_and_string_values_are_not_numbers():
    # bool은 int의 서브클래스 -- True가 1.0으로 새면 안 된다
    pts = build_node_points([_sample(
        "2026-08-09T00:00:00Z", load1=True, memory_total_kb="100",
        disks=[{"storage_name": "s1", "total_bytes": 0, "used_bytes": 0},
               "not-a-dict"])])
    assert pts[0]["load1"] is None and pts[0]["mem_used_pct"] is None
    # total 0은 나눗셈 불가 -- used_pct만 null, 항목은 살린다
    assert pts[0]["disks"] == [{"storage_name": "s1", "used_pct": None}]


def test_mem_used_pct_none_when_avail_exceeds_total():
    # 오염 os 리포트: avail > total -> used = total - avail 가 음수.
    # fail-soft(모르면 None) -- 음수 사용률을 그리느니 결측이 정직하다.
    pts = build_node_points([_sample(
        "2026-08-09T00:00:00Z", memory_total_kb=100, memory_available_kb=150)])
    assert pts[0]["mem_used_pct"] is None


def test_disk_used_pct_none_when_used_exceeds_total():
    # 같은 오염 규칙: used > total -> used_pct 가 100 초과. None으로 닫는다.
    pts = build_node_points([_sample(
        "2026-08-09T00:00:00Z",
        disks=[{"storage_name": "s1", "total_bytes": 100, "used_bytes": 150}])])
    assert pts[0]["disks"] == [{"storage_name": "s1", "used_pct": None}]


def test_zero_and_backward_dt_yield_null_not_crash():
    # 같은 reported_at(dt=0)과 역행 타임스탬프(dt<0)는 net_rx_bps=None,
    # ZeroDivisionError 없이. 두 경우 모두 cur>prev 라 cur<prev 가드가 아니라
    # dt<=0 가드가 잡는 것임을 격리한다.
    dt_zero = build_node_points([
        _sample("2026-08-09T00:00:00Z", network_rx_bytes=1000),
        _sample("2026-08-09T00:00:00Z", network_rx_bytes=2000),
    ])
    assert dt_zero[1]["net_rx_bps"] is None
    dt_back = build_node_points([
        _sample("2026-08-09T00:01:00Z", network_rx_bytes=1000),
        _sample("2026-08-09T00:00:00Z", network_rx_bytes=2000),
    ])
    assert dt_back[1]["net_rx_bps"] is None


def test_missing_rx_field_propagates_null_to_next_interval():
    # os는 유효하나 network_rx_bytes 필드만 결측인 샘플(전체가 버려지진 않는다):
    # 그 구간(prev,None)과 다음 구간(None,cur) 둘 다 throughput null, 그 뒤 회복.
    pts = build_node_points([
        _sample("2026-08-09T00:00:00Z", network_rx_bytes=1000),
        _sample("2026-08-09T00:01:00Z", load1=0.5),
        _sample("2026-08-09T00:02:00Z", network_rx_bytes=3000),
        _sample("2026-08-09T00:03:00Z", network_rx_bytes=5000),
    ])
    assert [p["net_rx_bps"] for p in pts] == [None, None, None, 33.3]


def test_non_list_disks_degrades_to_empty_without_crash():
    # 스키마 검증 없이 저장된 리포트라 os.disks 가 트루시 스칼라(5)일 수 있다.
    # `disks or []` 였다면 `for disk in 5` -> TypeError 로 노드 전체 시리즈가 죽는다.
    # mem 필드가 오염 시 None 으로 강등되듯, 비-리스트 disks 는 []로 강등해야 한다.
    pts = build_node_points([_sample(
        "2026-08-09T00:00:00Z", load1=0.5, load5=0.4, load15=0.3,
        memory_total_kb=100, memory_available_kb=25, disks=5)])
    # 예외 없이 포인트가 나오고, disks 만 []로 강등, 나머지 필드는 온전하다
    assert pts[0]["disks"] == []
    assert pts[0]["load1"] == 0.5 and pts[0]["mem_used_pct"] == 75.0


def test_dict_and_string_disks_also_degrade_to_empty():
    # isinstance(list) 가드를 고정: dict/str 도 순회하면 각각 키/문자로 새므로 []로 닫는다
    for bad in ({"storage_name": "s1"}, "s1"):
        pts = build_node_points([_sample("2026-08-09T00:00:00Z", disks=bad)])
        assert pts[0]["disks"] == [], bad


def test_duration_histogram_fixed_buckets():
    hist = duration_histogram([30, 3599, 100000, -5, None])
    assert hist == [
        {"bucket": "<1m", "count": 1}, {"bucket": "1-10m", "count": 0},
        {"bucket": "10-60m", "count": 1}, {"bucket": "1-6h", "count": 0},
        {"bucket": "6-24h", "count": 0}, {"bucket": ">24h", "count": 1}]


def test_duration_histogram_accepts_custom_buckets():
    # 제출 대기(슬라이스 17)는 플래너 틱(10s)·스테퍼 틱(5s) 규모다 -- 수행시간
    # 버킷(<1m 시작)을 그대로 쓰면 정상 대기가 전부 첫 버킷에 뭉쳐 분포가
    # 사라진다. 경계는 정상(<30s)/유예·지연(30s-5m: 신원 전파 유예 기본 300s)/
    # 백로그(>5m)를 가른다.
    from dms.metrics_series import (SUBMIT_WAIT_BUCKETS, SUBMIT_WAIT_OVERFLOW,
                                    duration_histogram)
    hist = duration_histogram([5, 45, 2000], buckets=SUBMIT_WAIT_BUCKETS,
                              overflow=SUBMIT_WAIT_OVERFLOW)
    assert {b["bucket"]: b["count"] for b in hist} == {
        "<10s": 1, "10-30s": 0, "30-60s": 1, "1-5m": 0, "5-30m": 0, ">30m": 1}
    # 기본 호출(수행시간)은 파라미터화 전과 완전히 같아야 한다 -- 기존 테스트
    # test_duration_histogram_fixed_buckets 가 그 절반을 지키고, 여기는 라벨 순서.
    assert [b["bucket"] for b in duration_histogram([])] == [
        "<1m", "1-10m", "10-60m", "1-6h", "6-24h", ">24h"]


def test_duration_histogram_counts_zero_as_a_real_value():
    # 제출 대기의 시각 해상도는 1초다 -- 스테퍼가 같은 초 안에 픽업하면 0 이
    # **정상 기록**이지 결측이 아니다. 스킵 가드가 `if not v` 였다면 0 이
    # 음수·None 과 한 덩어리로 버려져 첫 버킷이 통째로 비고, 가장 건강한 잡들만
    # 분포에서 사라진다(= 제출 대기가 실제보다 나빠 보인다).
    from dms.metrics_series import (SUBMIT_WAIT_BUCKETS, SUBMIT_WAIT_OVERFLOW,
                                    duration_histogram)
    hist = duration_histogram([0, 0, -1, None], buckets=SUBMIT_WAIT_BUCKETS,
                              overflow=SUBMIT_WAIT_OVERFLOW)
    assert {b["bucket"]: b["count"] for b in hist}["<10s"] == 2
    assert sum(b["count"] for b in hist) == 2    # 음수·None 만 빠진다


def test_summarize_seconds_mean_median_p95():
    # 평균/중앙값/p95 요약(슬라이스 31). p50/p95 는 nearest-rank -- 실제 관측값만
    # 낸다(보간으로 존재하지 않는 시간을 지어내지 않는다).
    from dms.metrics_series import summarize_seconds
    s = summarize_seconds(list(range(1, 21)))     # 1..20
    assert s == {"mean_seconds": 10.5, "p50_seconds": 10, "p95_seconds": 19}


def test_summarize_seconds_empty_is_none():
    # 표본 없음은 None -- 0(정상값)으로 뭉개면 "즉시 끝났다"는 거짓말이 된다.
    from dms.metrics_series import summarize_seconds
    assert summarize_seconds([]) is None


def test_summarize_seconds_zero_counts_and_junk_is_dropped():
    # duration_histogram 과 같은 가드: 0 은 정상값(표본에 남는다), 음수·None 만
    # 버린다. 전부 버려지면 None(표본 없음)이다.
    from dms.metrics_series import summarize_seconds
    assert summarize_seconds([0, -1, None]) == {
        "mean_seconds": 0.0, "p50_seconds": 0, "p95_seconds": 0}
    assert summarize_seconds([-1, None]) is None


def test_summarize_seconds_single_sample():
    from dms.metrics_series import summarize_seconds
    assert summarize_seconds([42]) == {
        "mean_seconds": 42.0, "p50_seconds": 42, "p95_seconds": 42}


def test_sched_wait_reuses_submit_buckets_and_zero_lands_in_first_bucket():
    # 슬라이스 20 은 새 버킷을 짓지 않고 SUBMIT_WAIT_BUCKETS 를 재사용한다(설계
    # §2.7 -- 두 대기 분포를 같은 축으로 나란히 비교, 실분포는 실증 후 조정).
    # 0(같은 틱 스케줄)이 첫 버킷에 남아야 한다: duration_histogram 의 가드가
    # `v is None or v < 0` 에서 `if not v` 류로 퇴행하면 여기서 잡힌다.
    from dms.metrics_series import (SUBMIT_WAIT_BUCKETS, SUBMIT_WAIT_OVERFLOW,
                                    duration_histogram)
    hist = duration_histogram([0, 12], buckets=SUBMIT_WAIT_BUCKETS,
                              overflow=SUBMIT_WAIT_OVERFLOW)
    counts = {b["bucket"]: b["count"] for b in hist}
    assert counts["<10s"] == 1      # 0 이 산다
    assert counts["10-30s"] == 1


# ---- 2026-10-21: 스토리지 합계의 파일시스템(statvfs) ----

def _node(name, *, at="2026-10-20T08:00:00Z", fresh=True, mounts=None, disks=None):
    return {"node_name": name, "reported_at": at, "fresh": fresh,
            "report": {"mounts": mounts if mounts is not None else [], "os": {"disks": disks or []}}}


def _mount(storage, status="Ready", is_mountpoint=True):
    return {"storage_name": storage, "status": status, "is_mountpoint": is_mountpoint}


def _disk(storage, total, used):
    return {"storage_name": storage, "total_bytes": total, "used_bytes": used}


def test_storage_filesystem_uses_only_ready_mountpoints():
    # 테스트베드 실측 모양: 마운트가 없는 노드(Missing/not_a_mountpoint)도 statvfs 를 불러 노드 루트 디스크 값을 싣는다 --
    # 그 값(40 GB, 노드마다 다름)은 버리고 Ready 마운트포인트 노드의 값만 쓴다.
    from dms.metrics_series import storage_filesystem
    nodes = [_node("w1", at="2026-10-20T09:00:00Z", mounts=[_mount("sec", "Missing", False)],
                   disks=[_disk("sec", 40483942400, 15265992704)]),
             _node("w4", mounts=[_mount("sec")], disks=[_disk("sec", 24498929664, 37748736)]),
             _node("w5", mounts=[_mount("sec")], disks=[_disk("sec", 24498929664, 37748736)]),
             # Ready 라도 마운트포인트가 아니면(보고 모양이 어긋남) 쓰지 않는다
             _node("w6", at="2026-10-20T09:30:00Z", mounts=[_mount("sec", "Ready", False)],
                   disks=[_disk("sec", 1000, 1)])]
    assert storage_filesystem("sec", nodes) == {
        "total_bytes": 24498929664, "used_bytes": 37748736, "node_name": "w5", "reported_at": "2026-10-20T08:00:00Z",
        "fresh": True, "node_count": 2, "consistent": True}
    # Ready 노드가 하나도 없으면 None(루트 디스크 값으로 지어내지 않는다) -- 보고에 없는 스토리지도 None
    assert storage_filesystem("sec", nodes[:1] + nodes[3:]) is None
    assert storage_filesystem("nope", nodes) is None
    assert storage_filesystem("sec", []) is None


def test_storage_filesystem_prefers_fresh_then_latest_and_marks_inconsistency():
    from dms.metrics_series import storage_filesystem
    stale_newer = _node("a", at="2026-10-20T10:00:00Z", fresh=False, mounts=[_mount("s")], disks=[_disk("s", 500, 1)])
    fresh_old = _node("b", at="2026-10-20T07:00:00Z", mounts=[_mount("s")], disks=[_disk("s", 100, 10)])
    fresh_new = _node("c", at="2026-10-20T08:00:00Z", mounts=[_mount("s")], disks=[_disk("s", 200, 20)])
    got = storage_filesystem("s", [stale_newer, fresh_old, fresh_new])
    # 신선 > 최신: 더 늦게 보고한 a 는 신선하지 않아 밀린다. 신선 집합(b, c)의 total 이 달라 consistent False
    # (노드 로컬 스토리지 -- 화면이 「노드마다 다름 — c 기준」으로 말한다). 신선하지 않은 a 는 개수에 들지 않는다.
    assert (got["node_name"], got["total_bytes"], got["used_bytes"]) == ("c", 200, 20)
    assert (got["fresh"], got["node_count"], got["consistent"]) == (True, 2, False)
    # 신선한 후보가 없으면 오래된 보고라도 쓰고 fresh False 로 말한다
    got = storage_filesystem("s", [stale_newer])
    assert (got["node_name"], got["fresh"], got["node_count"], got["consistent"]) == ("a", False, 1, True)


def test_storage_filesystem_rejects_tampered_values():
    # DB 가 신뢰 경계: 비정수·bool·음수·total 0·used > total, 비 dict report·mounts·disks 는 그 노드를 뺀다(500 없음)
    from dms.metrics_series import storage_filesystem
    bad = [_node("b1", disks=[_disk("s", True, 1)]),
           _node("b2", disks=[_disk("s", 100, -1)]),
           _node("b3", disks=[_disk("s", 0, 0)]),
           _node("b4", disks=[_disk("s", 100, 101)]),
           _node("b5", disks=[_disk("s", 100.0, 1)]),
           _node("b6", disks=["s"]),
           {"node_name": "b7", "reported_at": "2026-10-20T08:00:00Z", "fresh": True, "report": "oops"},
           {"node_name": "b8", "reported_at": "2026-10-20T08:00:00Z", "fresh": True,
            "report": {"mounts": "Ready", "os": {"disks": [_disk("s", 100, 1)]}}},
           {"node_name": "b9", "reported_at": "2026-10-20T08:00:00Z", "fresh": True,
            "report": {"mounts": [_mount("s")], "os": {"disks": {"s": 1}}}},
           {"node_name": "b10", "reported_at": "2026-10-20T08:00:00Z", "fresh": True,
            "report": {"mounts": [_mount("s")], "os": "x"}},
           {"node_name": "b11", "reported_at": None, "fresh": True,
            "report": {"mounts": [_mount("s")], "os": {"disks": [_disk("s", 100, 1)]}}},
           "not-a-node"]
    for n in bad:
        if isinstance(n, dict) and isinstance(n.get("report"), dict) and n["report"].get("mounts") == []:
            n["report"]["mounts"] = [_mount("s")]
    assert storage_filesystem("s", bad) is None
    good = _node("ok", mounts=[_mount("s")], disks=[_disk("s", 100, 100)])          # used == total 은 정상(가득 참)
    assert storage_filesystem("s", bad + [good])["node_name"] == "ok"
    assert storage_filesystem("s", None) is None
