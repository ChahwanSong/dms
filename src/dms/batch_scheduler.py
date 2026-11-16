"""배치 스케줄러(2026-11-16 사용자 결정): 정해진 KST 시각에 배치 하나의 「전체 재실행」(:rescan 과 **같은** 저장소 함수 --
BatchesRepository._rescan_locked)을 사람 대신 누른다. 허용 연산은 scan·sync·rm 전부이고, sync·rm 은 회차마다 기존 확인
게이트를 그대로 거친다(재실행이 Previewing 으로 보낼 뿐 -- 확인은 배치 자동 확인이 켜졌으면 오케스트레이터가 기존 경로로,
아니면 사람이). cron 식은 없다: KST 프리셋 넷(매일 HH:MM · 매주 요일 HH:MM · 매월 N일 HH:MM · N시간마다).

이 모듈은 두 부분이다.
  1) 시간 규칙(위 절반 -- 순수 함수, DB·api 모듈을 모른다): 규칙 검증(validate_schedule), 다음 슬롯(next_run_after),
     미리보기(preview_runs), 유예 창(grace_seconds), 파괴적 판정·배치 지문(is_destructive·batch_fingerprint). 컨트롤러
     (회차 판정)와 라우트(생성·수정·켜기·미리보기)가 같이 쓴다 -- 프런트는 다음 실행을 계산하지 않는다(:preview).
  2) 회차 판정·컨트롤러 틱(아래 절반 -- schedule_hold·BatchScheduler): controller 의 batch-scheduler 루프(batch-orchestrator
     앞)가 틱마다 due 스케줄을 판정하고 저장소의 fire/record 를 부른다.

KST 는 고정 UTC+9 다(tzdata 의존 없음 -- 에어갭 이미지에 zoneinfo 가 없을 수 있고 한국은 서머타임이 없다). 저장은 UTC
`%Y-%m-%dT%H:%M:%SZ`(db.utc_now_iso 형식 -- 문자열 비교가 곧 시간 비교라 due 질의가 인덱스로 탄다).

스케줄러가 **하지 않는 것**(CLAUDE.md 「도장·회차 CAS 를 건너뛰는 지름길 금지」): confirm 호출·auto_confirm_by 쓰기·Running
직행(sync·rm 은 Previewing 으로만 -- domain.confirm_gated). 실행 신원은 배치 행의 것이다(자식은 배치 requester_id·auth_method
를 물려받는다 -- 스케줄을 켠 사람으로 바꾸지 않는다). 그래서 회차마다 켠 사람(armed_by)과 배치 요청자 **둘 다** 지금도 자격
있는지 본다: 요청자가 자격을 잃으면 자식은 거부되지 않고 그 사람의 LDAP uid 로 조용히 비 root 강등되거나 항목마다
실패한다 -- 사람이 누르는 :rescan 이면 그 사람이 결과를 보지만, 스케줄은 아무도 보지 않는다.
"""
import calendar
import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from .db import iso_plus, like_escape, utc_now_iso
from .domain import BATCH_OPERATIONS, DomainValidationError, Operation, validate_batch_options
from .privileged import privileged_admin_problem

KST = timezone(timedelta(hours=9), "KST")
_ISO = "%Y-%m-%dT%H:%M:%SZ"

# kind → spec 키 집합(정확히 이 키만 -- 모르는 키도 빠진 키도 422). 포탈 폼(lib/batchSchedule.ts)이 같은 모양을 싣는다.
_SPEC_KEYS = {
    "daily": frozenset({"hour", "minute"}),
    "weekly": frozenset({"weekdays", "hour", "minute"}),
    "monthly": frozenset({"day", "hour", "minute"}),
    "interval": frozenset({"every_hours", "hour", "minute"}),
}
KINDS = tuple(_SPEC_KEYS)
# N시간마다의 N -- 24 의 약수만(사용자 결정): 기준 시각에서 N시간 간격이 날마다 같은 슬롯이 된다(자정을 넘어도 어긋나지
# 않는다). 하한 1시간 -- 그보다 잦은 전체 재실행은 자식 요청만 쌓는다(배치 단위 삭제의 자식 상한 1000).
INTERVAL_HOURS = (1, 2, 3, 4, 6, 8, 12)
# next_run_after 가 훑는 날 수 상한. 최악은 monthly(31일 → 말일로 당김): 다음 슬롯이 늘 두 달 안에 있다.
_SEARCH_DAYS = 70
_GRACE_CAP_SECONDS = 3600


def _int_in(value, lo, hi) -> bool:
    # bool 은 int 의 하위형이고 1.0 == 1 이다 -- 둘 다 거부한다(포탈 폼 밖의 손이 실은 값).
    return isinstance(value, int) and not isinstance(value, bool) and lo <= value <= hi


def _invalid(detail: str) -> DomainValidationError:
    return DomainValidationError("invalid_batch_schedule", detail)


def validate_schedule(kind, spec) -> dict:
    """kind/spec 을 검증하고 정규화한 spec 을 돌려준다(weekly 의 weekdays 는 정렬). 실패는 DomainValidationError
    ("invalid_batch_schedule") -- 라우트는 422, 컨트롤러는 DB 변조로 보고 자동 일시정지한다(DB 가 신뢰 경계).
      daily    {hour, minute}
      weekly   {weekdays: [1..7](ISO, 1=월) 1개 이상·중복 없음, hour, minute}
      monthly  {day: 1..31, hour, minute} -- 그 달에 없는 날(29~31)은 그 달 말일로 당긴다
      interval {every_hours ∈ INTERVAL_HOURS, hour, minute} -- hour:minute(KST)를 기준으로 N시간 간격
    hour 0–23, minute 0–59 정수."""
    if not isinstance(kind, str) or kind not in _SPEC_KEYS:
        raise _invalid(f"unknown kind {kind!r}")
    if not isinstance(spec, dict) or set(spec) != _SPEC_KEYS[kind]:
        raise _invalid(f"{kind} spec keys must be {sorted(_SPEC_KEYS[kind])}")
    if not _int_in(spec["hour"], 0, 23) or not _int_in(spec["minute"], 0, 59):
        raise _invalid("hour 0-23 and minute 0-59 must be integers")
    out = {"hour": spec["hour"], "minute": spec["minute"]}
    if kind == "weekly":
        days = spec["weekdays"]
        if (not isinstance(days, list) or not days or not all(_int_in(d, 1, 7) for d in days)
                or len(set(days)) != len(days)):
            raise _invalid("weekdays must be distinct integers 1-7 (ISO, 1=Mon), at least one")
        out["weekdays"] = sorted(days)
    elif kind == "monthly":
        if not _int_in(spec["day"], 1, 31):
            raise _invalid("day must be an integer 1-31")
        out["day"] = spec["day"]
    elif kind == "interval":
        n = spec["every_hours"]
        if not (isinstance(n, int) and not isinstance(n, bool) and n in INTERVAL_HOURS):
            raise _invalid(f"every_hours must be one of {list(INTERVAL_HOURS)}")
        out["every_hours"] = n
    return out


def load_spec(text):
    """저장된 spec(JSON 문자열) → dict, 깨졌거나 dict 가 아니면 None(DB 가 신뢰 경계 -- 판정이 invalid 로 일시정지한다)."""
    if not isinstance(text, str):
        return None
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def parse_utc(ts) -> datetime:
    """저장 형식(…Z) UTC 문자열 → aware datetime. 형식이 다르면 ValueError(TypeError 도 ValueError 로)."""
    if not isinstance(ts, str):
        raise ValueError(f"not a timestamp: {ts!r}")
    return datetime.strptime(ts, _ISO).replace(tzinfo=timezone.utc)


def _fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(_ISO)


def _slots_on(kind, spec, day: date) -> "list[tuple[int, int]]":
    """그 KST 날짜의 슬롯 (시, 분) 목록(오름차순)."""
    hm = (spec["hour"], spec["minute"])
    if kind == "daily":
        return [hm]
    if kind == "weekly":
        return [hm] if day.isoweekday() in spec["weekdays"] else []
    if kind == "monthly":
        last = calendar.monthrange(day.year, day.month)[1]
        return [hm] if day.day == min(spec["day"], last) else []
    n = spec["every_hours"]
    base = spec["hour"] * 60 + spec["minute"]
    return [divmod(m, 60) for m in sorted((base + k * n * 60) % 1440 for k in range(24 // n))]


def next_run_after(kind, spec, after_utc: str) -> str:
    """after_utc **엄격히 뒤**의 첫 슬롯(UTC 문자열). 같은 순간을 넘기면 그 다음 슬롯 -- 발사한 슬롯을 다시 고르지 않는다
    (컨트롤러는 max(예정, 지금) 을 넘긴다: 시계가 뒤로 가도 같은 슬롯이 두 번 돌지 않는다)."""
    spec = validate_schedule(kind, spec)
    after = parse_utc(after_utc)
    start = after.astimezone(KST).date()
    for offset in range(_SEARCH_DAYS):
        day = start + timedelta(days=offset)
        for hour, minute in _slots_on(kind, spec, day):
            slot = datetime(day.year, day.month, day.day, hour, minute, tzinfo=KST)
            if slot > after:
                return _fmt(slot)
    raise AssertionError(f"no slot within {_SEARCH_DAYS} days for {kind} {spec}")   # 도달 불능(위 상수 주석)


def preview_runs(kind, spec, now_utc: str, n: int = 5) -> "list[str]":
    """now 뒤 n 개 슬롯 -- 포탈의 「다음 실행」 미리보기(:preview). 말일로 당겨진 결과가 그대로 보인다."""
    out, cur = [], now_utc
    for _ in range(n):
        cur = next_run_after(kind, spec, cur)
        out.append(cur)
    return out


def grace_seconds(kind, spec) -> int:
    """늦게 집은 슬롯을 그래도 쏘는 유예 = min(1h, 주기/2). 넘으면 놓침(missed)으로 한 번 기록하고 다음 슬롯으로 --
    새벽 02:00 rm·sync 가 컨트롤러 복구 뒤 업무 시간에 터지지 않게. 리스 이양 지연(최대 ~30초 + 루프 주기)은 안에 든다."""
    if kind == "interval":
        return min(_GRACE_CAP_SECONDS, spec["every_hours"] * 3600 // 2)
    return _GRACE_CAP_SECONDS


def is_destructive(operation, options) -> bool:
    """「되돌릴 수 없음」 동의·지문 대상 스케줄인가 -- rm, 그리고 delete 옵션이 켜진 sync(root 미러 삭제). 판정은 **지금의**
    배치로 한다(저장하지 않는다): 스케줄을 만든 뒤 sync 에 delete 를 켜면 동의 없는 파괴적 스케줄이 되어 회차에서 멈춘다."""
    if operation == Operation.RM.value:
        return True
    return operation == Operation.SYNC.value and isinstance(options, dict) and options.get("delete") is True


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# 항목 payload 에서 스토리지 이름을 읽는 키(scan·rm = storage, sync = 소스·목적지) -- 지문의 스토리지 바인딩 재료.
_ITEM_STORAGE_KEYS = ("storage", "source_storage", "destination_storage")


def item_storage_names(items) -> "list[str]":
    """항목들이 가리키는 스토리지 이름(정렬·중복 없음). 문자열이 아닌 값·dict 가 아닌 payload 는 건너뛴다(지문의 항목 부분이
    그 payload 를 그대로 담으므로 빠져도 변조는 지문에 드러난다)."""
    names = set()
    for it in items:
        p = it.get("payload")
        if isinstance(p, dict):
            names.update(p[k] for k in _ITEM_STORAGE_KEYS if isinstance(p.get(k), str))
    return sorted(names)


def batch_fingerprint(batch, items, *, storages) -> str:
    """동의한 순간의 배치 = sha256(canonical JSON: 연산·옵션·항목 payload(정렬)·실행 신원·자동 확인 여부·스토리지 바인딩).
    파괴적 스케줄은 회차마다 지금 지문이 동의 때(ack_fingerprint)와 같아야 쏜다 -- CSV 교체·옵션 변경·나중에 켠 자동 확인이
    있으면, 동의한 사람이 본 적 없는 경로를 사람 없이 root 로 지우게 된다(사람의 :rescan 은 누르는 순간 그 사람이 본다). 항목
    순서·seq 는 대상이 아니다(같은 경로 집합이면 같은 배치).
    storages = {스토리지 이름: [mount_path, managed_root] | None(행 없음)} -- 항목이 가리키는 스토리지 전부(storage_binding).
    payload 에는 스토리지 **이름**과 상대 경로만 있고 절대 경로는 stepper 가 실행 때의 스토리지 행으로 만든다: 동의 뒤 스토리지의
    경로를 바꾸면(PUT /api/admin/storages 는 require_admin -- 공유 Bearer 토큰도 된다) 같은 이름·같은 상대 경로가 동의한 사람이 본
    적 없는 절대 경로를 가리키게 된다(2026-11-16 리뷰). 그래서 이름이 아니라 그 순간의 경로를 동의에 묶는다."""
    doc = {"operation": batch.get("operation"), "options": batch.get("options"),
           "items": sorted(_canonical(it.get("payload")) for it in items),
           "owner_username": batch.get("owner_username"),
           "auto_confirm": batch.get("auto_confirm_by") is not None,
           "storages": storages}
    return "sha256:" + hashlib.sha256(_canonical(doc).encode()).hexdigest()


# --- 회차 판정 · 컨트롤러 틱 ----------------------------------------------------------------------------------------------

# 감사 행·이력의 actor. 계정 이름 규칙(accounts._USERNAME_RE)에 없는 ':' 라 같은 이름의 사람 계정과 섞이지 않는다
# (batch_orchestrator.AUTO_CONFIRM_ACTOR 와 같은 관례). 켠 사람은 after_state 의 armed_by 에 남는다.
SCHEDULER_ACTOR = "system:batch-scheduler"
# 틱당 판정 상한(ARCH §3-9 루프 규약 -- 틱은 짧게 끝난다). 남은 due 는 다음 틱(기본 5초)이 집는다.
TICK_LIMIT = 20
# batch_schedule_error 이벤트의 스케줄별 쿨다운(batch_cancel._EVENT_COOLDOWN_SECONDS 와 같은 값).
_ERROR_EVENT_COOLDOWN_SECONDS = 600
_TERMINAL_BATCH = ("Completed", "Cancelled")


@dataclass(frozen=True)
class Verdict:
    """회차 판정. outcome: fired | skipped | missed | paused(= enabled 0 + next NULL + paused_reason_code)."""
    outcome: str
    reason_code: "str | None" = None


FIRE = Verdict("fired")


# 사유 코드 커버리지 추출기는 `reason_code=` 키워드 리터럴만 읽는다(tests/test_reason_codes_coverage.py) -- 판정마다 이
# 세 생성자에 리터럴로 넘긴다(batch_orchestrator._hold 와 같은 관례).
def _skip(*, reason_code: str) -> Verdict:
    return Verdict("skipped", reason_code)


def _missed(*, reason_code: str) -> Verdict:
    return Verdict("missed", reason_code)


def _pause(*, reason_code: str) -> Verdict:
    return Verdict("paused", reason_code)


def storage_binding(repos, items) -> dict:
    """항목이 가리키는 스토리지마다 **지금의** [mount_path, managed_root](행이 없으면 None) -- 지문의 storages(batch_fingerprint
    docstring). enabled 는 묶지 않는다: 어디를 지우는지를 바꾸지 않고(꺼진 스토리지는 자식이 planner storage_disabled 로 막힌다),
    묶으면 스토리지를 잠시 껐다 켜는 운영마다 파괴적 스케줄이 전부 멈춘다. 무장(라우트 트랜잭션)과 회차(판정·발사 트랜잭션)가
    이 한 함수로 읽는다."""
    out = {}
    for name in item_storage_names(items):
        row = repos.storages.get(name)
        out[name] = None if row is None else [row.get("mount_path"), row.get("managed_root")]
    return out


def binding_digest(binding) -> str:
    """storage_binding(dict)의 해시 -- 무장이 저장하고 회차가 비교하는 값(storage_binding_digest 와 같은 계산)."""
    return "sha256:" + hashlib.sha256(_canonical(binding).encode()).hexdigest()


def storage_binding_digest(repos, items) -> str:
    """**모든** 스케줄(연산 무관)이 무장 때 저장하고 회차마다 비교하는 스토리지 경로 바인딩 = sha256(canonical storage_binding).
    파괴적 지문(armed_fingerprint)은 rm·delete 켠 sync 만 묶는데, 자동 확인이 켜진 일반 sync 도 회차 사이 목적지 스토리지의
    경로를 바꾸면(PUT /api/admin/storages 는 require_admin -- 공유 Bearer 토큰도 된다) 사람 없이 root 로 엉뚱한 곳에 덮어쓴다
    (2026-11-16 보안 리뷰). 항목·옵션은 묶지 않는다(scan 배치는 항목을 자주 고친다 -- 그건 파괴적 지문만의 몫)."""
    return binding_digest(storage_binding(repos, items))


def armed_fingerprint(repos, batch, items) -> str:
    """무장·회차가 비교하는 지문 = batch_fingerprint(배치, 항목, 지금의 스토리지 바인딩). 라우트의 동의(routes_batch_schedules._arm)
    와 회차(batch_hold·발사 verify)가 반드시 이 함수 하나를 쓴다 -- 한쪽만 스토리지를 묶으면 모든 파괴적 스케줄이 첫 회차에서
    batch_schedule_batch_changed 로 멈춘다."""
    return batch_fingerprint(batch, items, storages=storage_binding(repos, items))


def arm_problem(repos, settings, batch) -> "Verdict | None":
    """배치가 스케줄로 돌 수 있는 배치인가(회차 판정 6–8단계) -- 라우트(생성·수정·켜기 = 거부 422)와 회차(일시정지)가 같은
    술어를 쓴다: 켤 때 통과한 배치가 첫 회차에서 같은 규칙으로 멈추는 일이 없게.
      6) 배치 요청자가 지금도 자격 있는 활성 관리자이고 배치가 세션으로 만들어졌다 -- 자식은 배치 행 신원을 물려받는다(모듈
         docstring). auth_method NULL(구형 행)은 세션이 아니다(orchestrator 가 token 으로 접는 것과 같은 fail-closed).
      7) 연산이 배치 연산(scan·sync·rm)이다 -- DB 변조로 들어온 미지 연산을 confirm_gated(게이트 쪽)로 넘기지 않는다.
      8) 옵션이 지금 규칙에 맞다(validate_batch_options -- 사람의 :rescan 의 _reject_stale_options 와 같은 함수, rm 은
         recursive 동의까지). 사유는 그 검증의 사유 코드 그대로(이미 등록된 코드들). dict 가 아닌 옵션(변조)은 invalid_option."""
    if (privileged_admin_problem(settings, repos, batch.get("requester_id")) is not None
            or batch.get("auth_method") != "session"):
        return _pause(reason_code="batch_schedule_requester_not_privileged")
    if batch.get("operation") not in BATCH_OPERATIONS:
        return _pause(reason_code="batch_schedule_unsupported_operation")
    try:
        validate_batch_options(batch["operation"], batch.get("options"))
    except DomainValidationError as exc:
        return Verdict("paused", exc.reason_code)
    except (AttributeError, TypeError, ValueError):
        return _pause(reason_code="invalid_option")
    return None


def batch_hold(repos, settings, batch, items, *, ack_fingerprint, bound_storages) -> "Verdict | None":
    """배치에 걸린 판정(5–11단계). None 이면 이 배치는 지금 쏠 수 있다. fire 가 잠근 행으로 **한 번 더** 부른다(판정과 발사
    사이 배치가 바뀌었으면 롤백 -- 다음 틱이 새 값으로 판정한다). bound_storages = 스케줄 행의 storage_binding(무장 때의
    storage_binding_digest) -- NULL(열 이전 행·변조)은 지금 값과 같을 수 없어 멈춘다(fail-closed)."""
    if batch is None:
        # 정상 경로에선 배치 삭제(두 경로 모두)가 스케줄도 지운다 -- 여기 오는 것은 경합·변조의 잔재다.
        return _pause(reason_code="batch_not_found")
    held = arm_problem(repos, settings, batch)
    if held is not None:
        return held
    if len(items) == 0:
        # 쏘면 곧바로 Completed 가 되는 가짜 「실행됨」만 남는다 -- 항목이 생기면 다음 슬롯부터 돈다.
        return _skip(reason_code="batch_schedule_empty")
    binding = storage_binding(repos, items)
    if any(v is None for v in binding.values()):
        # 항목이 가리키는 스토리지가 없다 -- 그 이름으로 다시 만들면(POST storages 도 require_admin -- 공유 토큰도 된다) 남은
        # 항목이 새 경로에서 돈다. 「없음」끼리 같다(None == None)는 바인딩으로는 쏘지 않는다(2026-11-16 리뷰 -- 지웠다가 같은
        # 이름으로 다시 만들기). 무장도 같은 이유로 거부한다(routes_batch_schedules._arm).
        return _pause(reason_code="storage_missing")
    if binding_digest(binding) != bound_storages:
        # 연산 무관 -- 켠 사람이 본 경로가 아니다(자동 확인 sync 의 root 덮어쓰기, scan 의 root 읽기). 다시 켜면 그 순간의
        # 경로로 묶인다(파괴적이면 동의도 다시).
        return _pause(reason_code="batch_schedule_storage_changed")
    if is_destructive(batch["operation"], batch.get("options")) and \
            armed_fingerprint(repos, batch, items) != ack_fingerprint:
        return _pause(reason_code="batch_schedule_batch_changed")
    status = batch.get("status")
    if status in _TERMINAL_BATCH:
        return None
    # 비종단은 그 회차만 건너뛴다(밀린 회차를 끝난 뒤 몰아 쏘지 않는다). 확인 대기(PreviewReady)는 운영자가 「확인하라」로
    # 읽어야 해서 따로 -- 자동 확인이 꺼진 sync·rm 은 확인하지 않으면 미리보기 만료(preview_ttl_seconds, 기본 24시간 -- 실패
    # 집계)까지 다음 회차가 계속 건너뛴다.
    if status in ("Previewing", "Running"):
        return _skip(reason_code="batch_schedule_batch_running")
    if status == "PreviewReady":
        return _skip(reason_code="batch_schedule_awaiting_confirm")
    if status == "Cancelling":
        return _skip(reason_code="batch_schedule_batch_cancelling")
    return _skip(reason_code="batch_not_rescannable")          # 모르는 상태(변조) -- 쏘지 않는다


def _rule_and_due(schedule):
    """(정규화한 spec, 예정 시각) -- 깨졌으면(DB 변조) None. 판정 0단계와 틱의 예외 경로(error_verdict)가 같이 쓴다."""
    try:
        return validate_schedule(schedule.get("kind"), schedule.get("spec")), parse_utc(schedule.get("next_run_at"))
    except (DomainValidationError, ValueError):
        return None


def _late(schedule, spec, due, now) -> bool:
    """지연 > 유예(grace_seconds) -- 판정 2단계와 틱의 예외 경로(error_verdict)가 같이 쓴다."""
    return (parse_utc(now) - due).total_seconds() > grace_seconds(schedule["kind"], spec)


def schedule_hold(repos, settings, schedule, batch, items, *, control, now, unreadable: bool = False) -> Verdict:
    """due 스케줄 한 건의 회차 판정 -- 순서가 계약이다(첫 해당이 결과):
      0) 규칙·예정 시각이 깨졌다(DB 변조) → paused invalid_batch_schedule(다음 슬롯을 계산할 수 없다)
      1) 유지보수 → skipped maintenance_mode / drain → skipped batch_schedule_drain(일시정지 없음 -- 컨트롤러 루프는 유지보수를
         안 보지만 스케줄러는 「관리자도 예외 없음」인 신규 유입이다)
      2) 지연 > 유예(grace_seconds) → missed(한 번만 -- 다음 슬롯은 지금 뒤로)
      3) 사이트 특권 실행 꺼짐 → skipped batch_schedule_privileged_disabled(켠 사람 탓이 아니다 -- 일시정지 없음)
      4) 켠 사람(armed_by)이 지금 자격 있는 활성 관리자가 아님 → paused batch_schedule_owner_not_privileged(자격이 돌아와도
         아무도 모르게 다시 돌지 않게 -- 다시 켠 사람이 새 주체다)
      4') 배치 행·항목의 JSON 을 읽지 못했다(unreadable -- 호출자가 읽다가 디코드 오류를 만났다, DB 변조) → paused
         batch_schedule_batch_unreadable. 예외로 두면 행이 next_run_at 을 넘기지 못해 틱마다 다시 집히고(오류 이벤트 5초마다),
         그런 행이 TICK_LIMIT 개면 due 의 앞자리를 차지해 다른 스케줄이 영영 판정되지 않는다(2026-11-16 리뷰)
      5–11) batch_hold
      12) fired"""
    rule = _rule_and_due(schedule)
    if rule is None:
        return _pause(reason_code="invalid_batch_schedule")
    spec, due = rule
    if control is not None and control.get("maintenance"):
        return _skip(reason_code="maintenance_mode")
    if control is not None and control.get("drain"):
        return _skip(reason_code="batch_schedule_drain")
    if _late(schedule, spec, due, now):
        return _missed(reason_code="batch_schedule_missed")
    if not settings.allow_privileged_requesters:
        return _skip(reason_code="batch_schedule_privileged_disabled")
    if privileged_admin_problem(settings, repos, schedule.get("armed_by")) is not None:
        return _pause(reason_code="batch_schedule_owner_not_privileged")
    if unreadable:
        return _pause(reason_code="batch_schedule_batch_unreadable")
    held = batch_hold(repos, settings, batch, items, ack_fingerprint=schedule.get("ack_fingerprint"),
                      bound_storages=schedule.get("storage_binding"))
    return FIRE if held is None else held


def error_verdict(schedule, now) -> "Verdict | None":
    """판정·발사가 예외로 끝난 행의 처분(BatchScheduler.run_once). None = 유예 안 -- 그대로 두고 다음 틱이 다시 판정한다(일시적
    오류는 다음 틱에 풀린다). 원인이 무엇이든 유예를 넘겨 계속되면 정상 판정과 같은 missed 로 그 슬롯을 넘긴다 -- 실패하는
    행은 슬롯마다 유예 창 동안만 시도되고 due 의 앞자리를 영영 차지하지 못한다. 규칙이 깨졌으면(다음 슬롯을 계산할 수 없다)
    0단계와 같은 일시정지."""
    rule = _rule_and_due(schedule)
    if rule is None:
        return _pause(reason_code="invalid_batch_schedule")
    spec, due = rule
    return _missed(reason_code="batch_schedule_missed") if _late(schedule, spec, due, now) else None


class BatchScheduler:
    """controller 의 batch-scheduler 루프 본체. 틱마다 새로 만든다(상태는 DB 에만 -- 리스가 넘어가도 이어진다)."""

    def __init__(self, repos, *, settings, now=None):
        self._repos = repos
        self._settings = settings
        self._now = now             # 시험용 고정 시각(None = 지금)

    def run_once(self) -> "list[tuple[str, str]]":
        """due 스케줄(예정 시각 순, TICK_LIMIT 건)을 판정한다 -- (schedule_id, 결과) 목록(lost = CAS 패배, error = 예외).
        한 행의 예외는 그 행만 실패시킨다(이벤트 batch_schedule_error, 트랜잭션 밖) -- 독 행 하나가 나머지를 막지 않게. 예외가
        유예를 넘겨 계속되면 그 슬롯을 missed 로 넘기고(error_verdict), 오류 이벤트는 스케줄마다 쿨다운 안에 한 번만 남긴다."""
        now = self._now or utc_now_iso()
        done = []
        for row in self._repos.batch_schedules.due(now, limit=TICK_LIMIT):
            try:
                done.append((row["schedule_id"], self._handle(row, now)))
            except Exception as exc:
                done.append((row["schedule_id"], self._after_error(row, now, exc)))
        return done

    def _after_error(self, row, now, exc) -> str:
        self._error_event(row, f"{type(exc).__name__}: {exc}")
        try:
            verdict = error_verdict(row, now)
            if verdict is None:
                return "error"
            return self._record(row, verdict, now, batch_status=None)
        except Exception as again:                  # 기록마저 실패(DB 단절 등) -- 다음 틱이 다시 본다
            self._error_event(row, f"after error: {type(again).__name__}: {again}")
            return "error"

    def _error_event(self, row, detail):
        """batch_schedule_error -- 같은 스케줄은 _ERROR_EVENT_COOLDOWN_SECONDS 안에 한 번만(batch_cancel._event 와 같은 결: 영구
        실패하는 행이 5초 틱마다 events 를 채우지 않게). 스케줄은 요청이 아니라 request_id 로 못 찾으니 메시지 머리(「schedule
        <id> 」)로 찾는다(like_escape -- 변조된 id 의 %·_ 가 다른 스케줄의 이벤트를 집지 않게). 이벤트 시각은 record_event 의
        실제 시각이라 쿨다운도 실제 시각으로 잰다. 쿨다운 조회·이벤트 기록의 실패는 판정을 막지 않는다."""
        head = f"schedule {row.get('schedule_id')} "
        try:
            if self._repos.db.query_one(
                    "SELECT 1 AS x FROM events WHERE at > :since AND event_type = 'batch_schedule_error' "
                    "AND message LIKE :head ESCAPE '\\' LIMIT 1",
                    {"since": iso_plus(utc_now_iso(), -_ERROR_EVENT_COOLDOWN_SECONDS),
                     "head": like_escape(head) + "%"}):
                return
        except Exception:
            pass                                    # 쿨다운을 모르면 한 건 더 남길 뿐이다
        self._repos.observability.record_event(
            component="batch-scheduler", severity="error", event_type="batch_schedule_error",
            message=f"{head}batch {row.get('batch_id')}: {detail}"[:500])

    def _record(self, row, verdict, now, *, batch_status) -> str:
        """쏘지 않은 회차(skipped·missed·paused)를 스케줄 CAS 로 남긴다 -- 진 쪽은 "lost"."""
        # 다음 슬롯은 max(예정, 지금) 보다 엄격히 뒤 -- 밀린 회차를 몰아 쏘지 않고, 시계가 뒤로 가도 같은 슬롯을 두 번 쏘지
        # 않는다. 일시정지는 다음이 없다(다시 켤 때 그 시각 뒤로 새로 잡는다).
        next_at = (None if verdict.outcome == "paused"
                   else next_run_after(row["kind"], row["spec"], max(row["next_run_at"], now)))
        if not self._repos.batch_schedules.record(row, outcome=verdict.outcome, reason_code=verdict.reason_code,
                                                  next_run_at=next_at, now=now, batch_status=batch_status):
            return "lost"
        if verdict.outcome in ("paused", "missed"):
            # 건너뜀은 이벤트를 남기지 않는다(확인 대기 동안 회차마다 쌓인다 -- 이력 행으로 충분). 놓침은 다운타임마다
            # 한 번, 일시정지는 사람이 다시 켜야 하는 전환이라 남긴다.
            self._repos.observability.record_event(
                component="batch-scheduler", severity="warning", event_type=f"batch_schedule_{verdict.outcome}",
                message=(f"schedule {row['schedule_id']} batch {row['batch_id']}: {verdict.outcome} "
                         f"({verdict.reason_code}) slot {row['next_run_at']}")[:500])
        return verdict.outcome

    def _handle(self, row, now) -> str:
        repos = self._repos
        bid = row["batch_id"]
        batch, items, unreadable = None, [], False
        try:
            batch = repos.batches.get(bid)
            items = repos.batches.list_items(bid) if batch is not None else []
        except (ValueError, TypeError):
            # 배치 options·항목 payload 의 깨진 JSON(DB 변조 -- load_json 은 json.loads 그대로)은 예외로 두지 않고 판정에
            # 넘긴다(schedule_hold 4'). 배치 행이 읽혔으면 그 상태를 이력에 남긴다.
            unreadable = True
        verdict = schedule_hold(repos, self._settings, row, batch, items,
                                control=repos.control.control_state(), now=now, unreadable=unreadable)
        batch_status = None if batch is None else batch.get("status")
        if verdict.outcome != "fired":
            return self._record(row, verdict, now, batch_status=batch_status)
        next_at = next_run_after(row["kind"], row["spec"], max(row["next_run_at"], now))
        env = frozenset(self._settings.privileged_requesters)

        def verify(locked_batch, locked_items):
            # 잠근 행으로 5–11단계를 다시 본다 -- 판정 뒤 CSV 교체·옵션 변경(지문)·항목 삭제가 커밋됐으면 롤백(다음 틱이 새
            # 값으로 판정해 일시정지·건너뜀을 남긴다). 쏠 수 있으면 감사 행에 남길 지금 지문을 돌려준다.
            if batch_hold(repos, self._settings, locked_batch, locked_items,
                          ack_fingerprint=row.get("ack_fingerprint"),
                          bound_storages=row.get("storage_binding")) is not None:
                return None
            return armed_fingerprint(repos, locked_batch, locked_items)

        result = repos.batch_schedules.fire(
            row, next_run_at=next_at, now=now, verify=verify,
            armed_env_listed=row.get("armed_by") in env,
            requester_env_listed=batch.get("requester_id") in env)
        if result is None:
            return "lost"
        repos.observability.record_event(
            component="batch-scheduler", severity="info", event_type="batch_schedule_fired",
            message=(f"schedule {row['schedule_id']} batch {bid}: slot {row['next_run_at']} -> {result['status']}, "
                     f"requeued {result['requeued']} (armed by {row.get('armed_by')})")[:500])
        return "fired"
