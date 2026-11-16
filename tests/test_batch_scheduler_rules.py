"""배치 스케줄러 시간 규칙(2026-11-16, dms.batch_scheduler 위 절반 -- 순수 함수).

KST = 고정 UTC+9, 저장은 UTC …Z. 경계: 월말(31→30/28/29 당김), 윤년 2/29, 연말, 요일 집합, interval 자정 넘김, KST 날짜 경계
(UTC 15:00 = KST 다음 날 00:00), 「엄격히 뒤」(같은 순간은 다음 슬롯), 유예 창, 파괴적 판정·지문."""
import pytest

from dms.batch_scheduler import (INTERVAL_HOURS, KINDS, batch_fingerprint, grace_seconds, is_destructive, load_spec,
                                 next_run_after, parse_utc, preview_runs, validate_schedule)
from dms.domain import DomainValidationError

DAILY_2 = {"hour": 2, "minute": 0}


def _bad(kind, spec):
    with pytest.raises(DomainValidationError) as e:
        validate_schedule(kind, spec)
    assert e.value.reason_code == "invalid_batch_schedule"


# --- 검증 ------------------------------------------------------------------------------------------------------------

def test_kinds_and_interval_presets_are_the_user_decision():
    assert KINDS == ("daily", "weekly", "monthly", "interval")
    assert INTERVAL_HOURS == (1, 2, 3, 4, 6, 8, 12)          # 24 의 약수만 -- 날마다 같은 슬롯


def test_validate_normalizes_and_sorts_weekdays():
    assert validate_schedule("daily", {"hour": 0, "minute": 0}) == {"hour": 0, "minute": 0}
    assert validate_schedule("weekly", {"weekdays": [5, 1, 3], "hour": 23, "minute": 59}) == \
        {"weekdays": [1, 3, 5], "hour": 23, "minute": 59}
    assert validate_schedule("monthly", {"day": 31, "hour": 2, "minute": 30}) == {"day": 31, "hour": 2, "minute": 30}
    assert validate_schedule("interval", {"every_hours": 6, "hour": 0, "minute": 30}) == \
        {"every_hours": 6, "hour": 0, "minute": 30}


@pytest.mark.parametrize("kind,spec", [
    ("hourly", DAILY_2), (None, DAILY_2), (1, DAILY_2),                           # 모르는 kind
    ("daily", None), ("daily", [2, 0]), ("daily", "02:00"),                        # dict 아님
    ("daily", {"hour": 2}), ("daily", {**DAILY_2, "second": 0}),                   # 키 빠짐·모르는 키
    ("daily", {"hour": True, "minute": 0}), ("daily", {"hour": 2, "minute": False}),   # bool 은 int 가 아니다
    ("daily", {"hour": 2.0, "minute": 0}), ("daily", {"hour": "2", "minute": 0}),
    ("daily", {"hour": 24, "minute": 0}), ("daily", {"hour": -1, "minute": 0}), ("daily", {"hour": 2, "minute": 60}),
    ("weekly", {"weekdays": [], **DAILY_2}), ("weekly", {"weekdays": [1, 1], **DAILY_2}),
    ("weekly", {"weekdays": [0], **DAILY_2}), ("weekly", {"weekdays": [8], **DAILY_2}),
    ("weekly", {"weekdays": [True], **DAILY_2}), ("weekly", {"weekdays": "1,3", **DAILY_2}),
    ("weekly", {"weekdays": (1,), **DAILY_2}), ("weekly", DAILY_2),
    ("monthly", {"day": 0, **DAILY_2}), ("monthly", {"day": 32, **DAILY_2}), ("monthly", {"day": True, **DAILY_2}),
    ("interval", {"every_hours": 5, **DAILY_2}), ("interval", {"every_hours": 24, **DAILY_2}),
    ("interval", {"every_hours": 0, **DAILY_2}), ("interval", {"every_hours": True, **DAILY_2}),   # True == 1 이지만 거부
    ("interval", {"every_hours": 1.0, **DAILY_2}), ("interval", {"every_hours": 6, "hour": 0}),
])
def test_validate_rejects(kind, spec):
    _bad(kind, spec)


def test_load_spec_is_none_for_broken_or_non_dict_json():
    assert load_spec('{"hour": 2, "minute": 0}') == DAILY_2
    for raw in ("{", "[1]", "null", "3", None, 7):
        assert load_spec(raw) is None


def test_parse_utc_rejects_other_shapes():
    assert parse_utc("2026-11-16T00:00:00Z").isoformat() == "2026-11-16T00:00:00+00:00"
    for raw in ("2026-11-16 00:00:00", "2026-11-16T00:00:00+09:00", "", None, 0):
        with pytest.raises(ValueError):
            parse_utc(raw)


# --- 다음 슬롯 --------------------------------------------------------------------------------------------------------

def test_daily_before_at_and_after_the_slot():
    # 02:00 KST = 전날 17:00 UTC.
    assert next_run_after("daily", DAILY_2, "2026-11-16T16:59:59Z") == "2026-11-16T17:00:00Z"
    # 엄격히 뒤: 정확히 그 순간이면 다음 날(발사한 슬롯을 다시 고르지 않는다).
    assert next_run_after("daily", DAILY_2, "2026-11-16T17:00:00Z") == "2026-11-17T17:00:00Z"
    assert next_run_after("daily", DAILY_2, "2026-11-16T17:00:01Z") == "2026-11-17T17:00:00Z"


def test_kst_date_boundary_is_utc_15():
    # UTC 14:59:59 = KST 23:59:59, UTC 15:00 = KST 다음 날 00:00.
    midnight = {"hour": 0, "minute": 0}
    assert next_run_after("daily", midnight, "2026-11-16T14:59:59Z") == "2026-11-16T15:00:00Z"
    assert next_run_after("daily", midnight, "2026-11-16T15:00:00Z") == "2026-11-17T15:00:00Z"
    # 요일도 KST 날짜로 센다: UTC 2026-11-16(월) 15:30 = KST 11-17(화) 00:30 -- 화요일 슬롯 00:00 은 이미 지났다.
    tue = {"weekdays": [2], "hour": 0, "minute": 0}
    assert next_run_after("weekly", tue, "2026-11-16T14:00:00Z") == "2026-11-16T15:00:00Z"   # KST 화 00:00
    assert next_run_after("weekly", tue, "2026-11-16T15:30:00Z") == "2026-11-23T15:00:00Z"   # 다음 주 화


def test_weekly_day_set():
    mwf = {"weekdays": [1, 3, 5], "hour": 9, "minute": 30}       # 09:30 KST = 00:30 UTC 같은 날
    # 2026-11-16 은 월요일.
    runs = preview_runs("weekly", mwf, "2026-11-15T12:00:00Z", 5)
    assert runs == ["2026-11-16T00:30:00Z", "2026-11-18T00:30:00Z", "2026-11-20T00:30:00Z",
                    "2026-11-23T00:30:00Z", "2026-11-25T00:30:00Z"]
    sunday = {"weekdays": [7], "hour": 23, "minute": 0}
    assert next_run_after("weekly", sunday, "2026-11-16T00:00:00Z") == "2026-11-22T14:00:00Z"


@pytest.mark.parametrize("after,expected", [
    ("2026-01-30T00:00:00Z", "2026-01-30T17:00:00Z"),   # 1/31 02:00 KST
    ("2026-01-30T17:00:00Z", "2026-02-27T17:00:00Z"),   # 2월(평년)은 28일로 당긴다
    ("2026-02-27T17:00:00Z", "2026-03-30T17:00:00Z"),   # 3/31
    ("2026-03-30T17:00:00Z", "2026-04-29T17:00:00Z"),   # 4월은 30일
    ("2028-01-30T17:00:00Z", "2028-02-28T17:00:00Z"),   # 윤년 2/29
])
def test_monthly_31_clamps_to_month_end(after, expected):
    assert next_run_after("monthly", {"day": 31, "hour": 2, "minute": 0}, after) == expected


def test_monthly_29_and_30_in_february():
    d29 = {"day": 29, "hour": 2, "minute": 0}
    assert next_run_after("monthly", d29, "2027-02-01T00:00:00Z") == "2027-02-27T17:00:00Z"   # 2027 평년 -- 2/28
    assert next_run_after("monthly", d29, "2028-02-01T00:00:00Z") == "2028-02-28T17:00:00Z"   # 2028 윤년 -- 2/29
    d30 = {"day": 30, "hour": 2, "minute": 0}
    assert next_run_after("monthly", d30, "2028-02-01T00:00:00Z") == "2028-02-28T17:00:00Z"   # 윤년에도 2/29 로 당김
    # 당긴 달 다음에는 원래 날로 돌아간다(3/30).
    assert next_run_after("monthly", d30, "2028-02-28T17:00:00Z") == "2028-03-29T17:00:00Z"


def test_year_end():
    assert next_run_after("daily", {"hour": 0, "minute": 30}, "2026-12-31T15:00:00Z") == "2026-12-31T15:30:00Z"  # KST 1/1
    assert next_run_after("monthly", {"day": 31, "hour": 23, "minute": 0}, "2026-12-31T14:00:00Z") == \
        "2027-01-31T14:00:00Z"
    assert next_run_after("monthly", {"day": 1, "hour": 0, "minute": 0}, "2026-12-15T00:00:00Z") == \
        "2026-12-31T15:00:00Z"                                                                  # KST 2027-01-01 00:00


def test_interval_slots_and_midnight_wrap():
    six = {"every_hours": 6, "hour": 0, "minute": 30}           # KST 00:30·06:30·12:30·18:30
    # KST 20:00(UTC 11:00) 뒤 → KST 다음 날 00:30(UTC 15:30) -- 자정을 넘는다.
    assert next_run_after("interval", six, "2026-11-16T11:00:00Z") == "2026-11-16T15:30:00Z"
    assert preview_runs("interval", six, "2026-11-16T11:00:00Z", 5) == [
        "2026-11-16T15:30:00Z", "2026-11-16T21:30:00Z", "2026-11-17T03:30:00Z", "2026-11-17T09:30:00Z",
        "2026-11-17T15:30:00Z"]
    # 기준 시각이 늦어도(22:00, 4시간) 슬롯은 하루에 고르게 -- 02:00·06:00·…·22:00 KST.
    four = {"every_hours": 4, "hour": 22, "minute": 0}
    runs = preview_runs("interval", four, "2026-11-16T15:00:00Z", 6)            # KST 11-17 00:00 부터
    assert [parse_utc(r).hour for r in runs] == [17, 21, 1, 5, 9, 13]          # UTC = KST - 9
    hourly = {"every_hours": 1, "hour": 5, "minute": 15}
    assert next_run_after("interval", hourly, "2026-11-16T00:15:00Z") == "2026-11-16T01:15:00Z"


def test_preview_runs_strictly_increase():
    for kind, spec in (("daily", DAILY_2), ("weekly", {"weekdays": [1, 7], **DAILY_2}),
                       ("monthly", {"day": 31, **DAILY_2}), ("interval", {"every_hours": 12, **DAILY_2})):
        runs = preview_runs(kind, spec, "2026-11-16T00:00:00Z", 5)
        assert len(runs) == 5 and runs == sorted(set(runs))


def test_next_run_after_validates_the_spec():
    with pytest.raises(DomainValidationError):
        next_run_after("daily", {"hour": 25, "minute": 0}, "2026-11-16T00:00:00Z")


# --- 유예 창 · 파괴적 판정 · 지문 -----------------------------------------------------------------------------------------

def test_grace_is_min_of_one_hour_and_half_period():
    assert grace_seconds("daily", DAILY_2) == 3600
    assert grace_seconds("weekly", {"weekdays": [1], **DAILY_2}) == 3600
    assert grace_seconds("monthly", {"day": 1, **DAILY_2}) == 3600
    assert grace_seconds("interval", {"every_hours": 1, **DAILY_2}) == 1800
    assert grace_seconds("interval", {"every_hours": 2, **DAILY_2}) == 3600
    assert grace_seconds("interval", {"every_hours": 12, **DAILY_2}) == 3600


def test_destructive_is_rm_or_sync_with_delete_true():
    assert is_destructive("rm", {"recursive": True}) is True
    assert is_destructive("sync", {"delete": True}) is True
    assert is_destructive("sync", {"delete": False}) is False
    assert is_destructive("sync", {}) is False
    assert is_destructive("sync", {"delete": "yes"}) is False      # 변조 값은 옵션 검증이 먼저 멈춘다
    assert is_destructive("sync", None) is False
    assert is_destructive("scan", {"delete": True}) is False


def test_fingerprint_ignores_item_order_but_not_content():
    batch = {"operation": "rm", "options": {"recursive": True}, "owner_username": None, "auto_confirm_by": None}
    a = [{"payload": {"storage": "s1", "target": "x"}}, {"payload": {"storage": "s1", "target": "y"}}]
    fp = batch_fingerprint(batch, a)
    assert fp.startswith("sha256:")
    assert batch_fingerprint(batch, list(reversed(a))) == fp
    assert batch_fingerprint(batch, a[:1]) != fp
    assert batch_fingerprint(batch, a + [{"payload": {"storage": "s1", "target": "z"}}]) != fp
    assert batch_fingerprint({**batch, "options": {"recursive": True, "lite": True}}, a) != fp
    assert batch_fingerprint({**batch, "owner_username": "bob"}, a) != fp
    # 자동 확인은 켜짐 여부만(켠 사람이 바뀌어도 같은 배치).
    on = batch_fingerprint({**batch, "auto_confirm_by": "ops"}, a)
    assert on != fp and batch_fingerprint({**batch, "auto_confirm_by": "other"}, a) == on
