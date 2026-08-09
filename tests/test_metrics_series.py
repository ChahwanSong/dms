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
    assert pts == [{
        "at": "2026-08-09T00:00:00Z", "load1": 0.5, "load5": 0.4, "load15": 0.3,
        "mem_used_pct": 75.0, "net_rx_bps": None, "net_tx_bps": None,
        "disks": [{"storage_name": "s1", "used_pct": 25.0}]}]


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


def test_duration_histogram_fixed_buckets():
    hist = duration_histogram([30, 3599, 100000, -5, None])
    assert hist == [
        {"bucket": "<1m", "count": 1}, {"bucket": "1-10m", "count": 0},
        {"bucket": "10-60m", "count": 1}, {"bucket": "1-6h", "count": 0},
        {"bucket": "6-24h", "count": 0}, {"bucket": ">24h", "count": 1}]
