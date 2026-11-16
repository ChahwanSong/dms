"""배치 스케줄러 순수 규칙(2026-11-16, batch_scheduler 위 절반): 규칙 검증 · KST 다음 슬롯(UTC 날짜·요일 경계, 말일 당김,
연말) · 유예 · 확인 대기 경고(설계 비판 #8) · 파괴적 판정 · 배치 지문. DB 를 쓰지 않는다(지문의 스토리지 바인딩은 인자)."""
import pytest

from dms.batch_scheduler import (INTERVAL_HOURS, KINDS, KST, batch_fingerprint, confirm_wait_note, grace_seconds,
                                 is_destructive, item_storage_names, load_spec, next_run_after, parse_utc,
                                 preview_runs, slot_gap_seconds, validate_schedule)
from dms.domain import DomainValidationError


def _bad(kind, spec):
    with pytest.raises(DomainValidationError) as e:
        validate_schedule(kind, spec)
    assert e.value.reason_code == "invalid_batch_schedule"


# --- validate_schedule ----------------------------------------------------------------------------------------------

def test_kinds_are_the_four_presets():
    assert KINDS == ("daily", "weekly", "monthly", "interval")
    assert INTERVAL_HOURS == (1, 2, 3, 4, 6, 8, 12)          # 24 의 약수만


def test_valid_specs_are_normalized():
    assert validate_schedule("daily", {"hour": 2, "minute": 0}) == {"hour": 2, "minute": 0}
    assert validate_schedule("weekly", {"weekdays": [4, 1], "hour": 3, "minute": 5}) == \
        {"weekdays": [1, 4], "hour": 3, "minute": 5}
    assert validate_schedule("monthly", {"day": 31, "hour": 0, "minute": 59})["day"] == 31
    assert validate_schedule("interval", {"every_hours": 6, "hour": 0, "minute": 30})["every_hours"] == 6


@pytest.mark.parametrize("kind,spec", [
    ("hourly", {"hour": 1, "minute": 0}),                       # 모르는 kind
    (None, {"hour": 1, "minute": 0}),
    ("daily", {"hour": 1}),                                     # 키 빠짐
    ("daily", {"hour": 1, "minute": 0, "day": 3}),              # 모르는 키
    ("daily", [1, 0]),                                          # dict 아님
    ("daily", {"hour": 24, "minute": 0}),
    ("daily", {"hour": -1, "minute": 0}),
    ("daily", {"hour": 1, "minute": 60}),
    ("daily", {"hour": True, "minute": 0}),                     # bool 은 정수가 아니다
    ("daily", {"hour": 1.0, "minute": 0}),                      # 실수 거부
    ("daily", {"hour": "1", "minute": 0}),
    ("weekly", {"weekdays": [], "hour": 1, "minute": 0}),       # 요일 0개
    ("weekly", {"weekdays": [0], "hour": 1, "minute": 0}),
    ("weekly", {"weekdays": [8], "hour": 1, "minute": 0}),
    ("weekly", {"weekdays": [1, 1], "hour": 1, "minute": 0}),   # 중복
    ("weekly", {"weekdays": [True], "hour": 1, "minute": 0}),
    ("weekly", {"weekdays": 1, "hour": 1, "minute": 0}),
    ("monthly", {"day": 0, "hour": 1, "minute": 0}),
    ("monthly", {"day": 32, "hour": 1, "minute": 0}),
    ("interval", {"every_hours": 5, "hour": 0, "minute": 0}),   # 24 의 약수 아님
    ("interval", {"every_hours": 24, "hour": 0, "minute": 0}),
    ("interval", {"every_hours": True, "hour": 0, "minute": 0}),
])
def test_invalid_specs_are_rejected(kind, spec):
    _bad(kind, spec)


def test_load_spec_and_parse_utc_fail_closed():
    assert load_spec('{"hour": 1, "minute": 2}') == {"hour": 1, "minute": 2}
    assert load_spec("{broken") is None
    assert load_spec("[1, 2]") is None
    assert load_spec(None) is None
    assert parse_utc("2026-11-16T00:00:00Z").tzinfo is not None
    for bad in ("2026-11-16 00:00:00", None, 5, "2026-11-16T00:00:00+09:00"):
        with pytest.raises(ValueError):
            parse_utc(bad)


# --- next_run_after: KST ↔ UTC 경계 ---------------------------------------------------------------------------------

def test_daily_kst_midnight_is_utc_15_and_same_instant_moves_on():
    spec = {"hour": 0, "minute": 0}
    # UTC 14:59 = KST 23:59 → 바로 다음 KST 자정 = UTC 15:00 같은 날
    assert next_run_after("daily", spec, "2026-11-16T14:59:00Z") == "2026-11-16T15:00:00Z"
    # 같은 순간이면 그 다음 슬롯(발사한 슬롯을 다시 고르지 않는다)
    assert next_run_after("daily", spec, "2026-11-16T15:00:00Z") == "2026-11-17T15:00:00Z"


def test_daily_morning_kst_is_previous_utc_day():
    # KST 02:00 = 전날 UTC 17:00
    assert next_run_after("daily", {"hour": 2, "minute": 0}, "2026-11-16T00:00:00Z") == "2026-11-16T17:00:00Z"


def test_weekly_monday_early_kst_is_sunday_utc():
    # 2026-11-16 은 월요일. KST 월 09:00(UTC 00:00) 이후의 「월 03:00 KST」 = 다음 주 월 03:00 KST = 일요일 18:00 UTC
    nxt = next_run_after("weekly", {"weekdays": [1], "hour": 3, "minute": 0}, "2026-11-16T00:00:00Z")
    assert nxt == "2026-11-22T18:00:00Z"
    assert parse_utc(nxt).isoweekday() == 7                      # UTC 로는 일요일
    assert parse_utc(nxt).astimezone(KST).isoweekday() == 1     # KST 로는 월요일


def test_weekly_multiple_days_and_week_wrap():
    spec = {"weekdays": [1, 4], "hour": 22, "minute": 0}       # 월·목 22:00 KST = 같은 날 13:00 UTC
    runs = preview_runs("weekly", spec, "2026-11-16T14:00:00Z", 3)   # 월 23:00 KST 이후
    assert runs == ["2026-11-19T13:00:00Z", "2026-11-23T13:00:00Z", "2026-11-26T13:00:00Z"]


def test_monthly_first_day_early_kst_is_previous_month_last_day_utc():
    # 매월 1일 05:00 KST = 전달 말일 20:00 UTC
    assert next_run_after("monthly", {"day": 1, "hour": 5, "minute": 0}, "2026-11-16T00:00:00Z") == \
        "2026-11-30T20:00:00Z"


def test_monthly_new_year_crossing():
    # 연말: 1월 1일 05:00 KST = 전년 12-31 20:00 UTC
    assert next_run_after("monthly", {"day": 1, "hour": 5, "minute": 0}, "2026-12-15T00:00:00Z") == \
        "2026-12-31T20:00:00Z"
    # 그 순간 뒤면 2월 1일 05:00 KST = 1-31 20:00 UTC
    assert next_run_after("monthly", {"day": 1, "hour": 5, "minute": 0}, "2026-12-31T20:00:00Z") == \
        "2027-01-31T20:00:00Z"


def test_monthly_31_clamps_to_last_day_in_kst_calendar():
    spec = {"day": 31, "hour": 5, "minute": 0}
    # 2027-01-31 09:00 KST 이후 → 2월은 28일까지 → 2-28 05:00 KST = 2-27 20:00 UTC(말일 당김은 KST 달력)
    assert next_run_after("monthly", spec, "2027-01-31T00:00:00Z") == "2027-02-27T20:00:00Z"
    # 윤년 2028: 2-29 05:00 KST = 2-28 20:00 UTC
    assert next_run_after("monthly", spec, "2028-01-31T00:00:00Z") == "2028-02-28T20:00:00Z"
    # 30일 달(4월): 4-30 05:00 KST = 4-29 20:00 UTC
    assert next_run_after("monthly", spec, "2027-04-01T00:00:00Z") == "2027-04-29T20:00:00Z"
    # 당긴 달 다음엔 다시 31일(3월)
    assert next_run_after("monthly", spec, "2027-02-27T20:00:00Z") == "2027-03-30T20:00:00Z"


def test_monthly_29_in_common_year_february():
    assert next_run_after("monthly", {"day": 29, "hour": 23, "minute": 30}, "2027-02-01T00:00:00Z") == \
        "2027-02-28T14:30:00Z"


def test_interval_grid_crosses_midnight():
    spec = {"every_hours": 8, "hour": 20, "minute": 0}          # KST 04:00·12:00·20:00
    runs = preview_runs("interval", spec, "2026-11-16T10:00:00Z", 4)  # KST 19:00 이후
    assert runs == ["2026-11-16T11:00:00Z",          # 20:00 KST
                    "2026-11-16T19:00:00Z",          # 다음날 04:00 KST
                    "2026-11-17T03:00:00Z",          # 12:00 KST
                    "2026-11-17T11:00:00Z"]


def test_interval_same_instant_moves_to_next_slot():
    spec = {"every_hours": 1, "hour": 0, "minute": 15}
    assert next_run_after("interval", spec, "2026-11-16T00:15:00Z") == "2026-11-16T01:15:00Z"


def test_next_run_after_rejects_broken_rule():
    with pytest.raises(DomainValidationError):
        next_run_after("daily", {"hour": 99, "minute": 0}, "2026-11-16T00:00:00Z")


# --- 유예 · 간격 · 확인 대기 경고 ------------------------------------------------------------------------------------

def test_grace_is_min_one_hour_and_half_period():
    assert grace_seconds("daily", {"hour": 0, "minute": 0}) == 3600
    assert grace_seconds("interval", {"every_hours": 1, "hour": 0, "minute": 0}) == 1800
    assert grace_seconds("interval", {"every_hours": 12, "hour": 0, "minute": 0}) == 3600


def test_slot_gap():
    assert slot_gap_seconds("daily", {"hour": 0, "minute": 0}) == (86400, True)
    assert slot_gap_seconds("interval", {"every_hours": 6, "hour": 0, "minute": 0}) == (21600, True)
    assert slot_gap_seconds("weekly", {"weekdays": [1], "hour": 0, "minute": 0}) == (7 * 86400, True)
    assert slot_gap_seconds("weekly", {"weekdays": [1, 2], "hour": 0, "minute": 0}) == (86400, False)
    assert slot_gap_seconds("weekly", {"weekdays": [1, 2, 3, 4, 5, 6, 7], "hour": 0, "minute": 0}) == (86400, True)
    assert slot_gap_seconds("monthly", {"day": 1, "hour": 0, "minute": 0}) == (28 * 86400, False)


def test_confirm_wait_daily_without_auto_confirm_is_every_other_day():
    note = confirm_wait_note("daily", {"hour": 2, "minute": 0}, operation="sync", auto_confirm=False,
                             preview_ttl_seconds=86400)
    assert note == {"period_seconds": 86400, "preview_ttl_seconds": 86400, "unconfirmed_every_seconds": 172800}
    rm = confirm_wait_note("interval", {"every_hours": 6, "hour": 0, "minute": 0}, operation="rm",
                           auto_confirm=False, preview_ttl_seconds=86400)
    assert rm["unconfirmed_every_seconds"] == 30 * 3600          # 6h × (24h // 6h + 1)


def test_confirm_wait_none_when_not_waiting_for_a_human():
    daily = {"hour": 2, "minute": 0}
    assert confirm_wait_note("daily", daily, operation="scan", auto_confirm=False, preview_ttl_seconds=86400) is None
    assert confirm_wait_note("daily", daily, operation="sync", auto_confirm=True, preview_ttl_seconds=86400) is None
    assert confirm_wait_note("daily", daily, operation="bogus", auto_confirm=False, preview_ttl_seconds=86400) is None
    # 주기 > 만료면 확인하지 않아도 다음 슬롯 전에 만료돼 주기대로 돈다
    assert confirm_wait_note("weekly", {"weekdays": [1], "hour": 0, "minute": 0}, operation="sync",
                             auto_confirm=False, preview_ttl_seconds=86400) is None
    # 깨진 규칙·만료 값은 경고를 지어내지 않는다
    assert confirm_wait_note("daily", {"hour": 99, "minute": 0}, operation="sync", auto_confirm=False,
                             preview_ttl_seconds=86400) is None
    assert confirm_wait_note("daily", daily, operation="sync", auto_confirm=False, preview_ttl_seconds=None) is None
    assert confirm_wait_note("daily", daily, operation="sync", auto_confirm=False, preview_ttl_seconds=0) is None


def test_confirm_wait_uneven_weekly_has_no_single_every():
    note = confirm_wait_note("weekly", {"weekdays": [1, 2], "hour": 0, "minute": 0}, operation="sync",
                             auto_confirm=False, preview_ttl_seconds=86400)
    assert note == {"period_seconds": 86400, "preview_ttl_seconds": 86400, "unconfirmed_every_seconds": None}


# --- 파괴적 판정 · 지문 ---------------------------------------------------------------------------------------------

def test_is_destructive():
    assert is_destructive("rm", {"recursive": True}) is True
    assert is_destructive("rm", None) is True
    assert is_destructive("sync", {"delete": True}) is True
    assert is_destructive("sync", {"delete": False}) is False
    assert is_destructive("sync", {"delete": "true"}) is False      # 명시 true 만
    assert is_destructive("sync", None) is False
    assert is_destructive("scan", {"delete": True}) is False


def _items(*targets):
    return [{"seq": i, "payload": {"storage": "s1", "target": t}} for i, t in enumerate(targets)]


def test_fingerprint_ignores_item_order_and_seq():
    batch = {"operation": "rm", "options": {"recursive": True}, "owner_username": None, "auto_confirm_by": None}
    st = {"s1": ["/m", "/m/r"]}
    a = batch_fingerprint(batch, _items("a", "b"), storages=st)
    b = batch_fingerprint(batch, list(reversed(_items("a", "b"))), storages=st)
    c = batch_fingerprint(batch, [{"seq": 9, "payload": {"target": "b", "storage": "s1"}},
                                  {"seq": 3, "payload": {"target": "a", "storage": "s1"}}], storages=st)
    assert a == b == c and a.startswith("sha256:")


def test_fingerprint_changes_with_what_the_consent_covered():
    batch = {"operation": "rm", "options": {"recursive": True}, "owner_username": None, "auto_confirm_by": None}
    st = {"s1": ["/m", "/m/r"]}
    base = batch_fingerprint(batch, _items("a"), storages=st)
    assert batch_fingerprint(batch, _items("a", "c"), storages=st) != base                       # 항목 추가
    assert batch_fingerprint({**batch, "auto_confirm_by": "ops"}, _items("a"), storages=st) != base  # 자동 확인 켬
    assert batch_fingerprint({**batch, "options": {"recursive": True, "x": 1}}, _items("a"), storages=st) != base
    assert batch_fingerprint({**batch, "owner_username": "bob"}, _items("a"), storages=st) != base
    assert batch_fingerprint(batch, _items("a"), storages={"s1": ["/other", "/other/r"]}) != base  # 스토리지 경로 변경
    assert batch_fingerprint(batch, _items("a"), storages={"s1": None}) != base                     # 스토리지 삭제


def test_item_storage_names_skips_non_strings():
    items = [{"payload": {"source_storage": "a", "destination_storage": "b"}},
             {"payload": {"storage": "c"}}, {"payload": {"storage": 5}}, {"payload": "broken"}, {}]
    assert item_storage_names(items) == ["a", "b", "c"]
