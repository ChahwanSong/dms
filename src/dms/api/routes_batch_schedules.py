"""배치 스케줄 API(2026-11-16 -- 규칙·회차 판정은 dms.batch_scheduler, 저장은 repositories/batch_schedules).

게이트(배치 라우트와 같은 결 -- routes_batches 의 특권 블록 주석):
  - 스케줄을 **무장**하는 라우트(생성·규칙 수정·켜기)는 배치 생성과 같은 특권 3중 게이트(_require_batch_privilege -- 허용
    설정 + 특권 목록 + 세션; 공유 Bearer 토큰은 403)와 유지보수 거부. 무장한 사람이 armed_by 로 남고 회차마다 그 사람의 자격을
    다시 본다. 파괴적 스케줄(rm, delete 켠 sync -- batch_scheduler.is_destructive)은 무장할 때마다 「되돌릴 수 없음」 동의
    (acknowledge_irreversible: **명시 true** 만, StrictBool)를 받고 그 순간의 배치 지문(항목이 가리키는 스토리지의 경로 포함 --
    batch_scheduler.armed_fingerprint)을 남긴다 -- 회차의 배치가 그 지문과 다르면 자동 일시정지(사람이 다시 동의해야 돈다).
  - 끄기·삭제는 require_admin(실행을 줄이기만 한다 -- 유지보수 중에도 받는다, :auto-confirm-off 와 같은 브레이크).
  - 조회·미리보기는 require_admin(토큰 조회 가능 -- 다른 관리 GET 과 같다).
무장 판정(arm)은 저장소 트랜잭션 안, 항목 → 배치 잠금 뒤의 행으로 한다(batch_scheduler.arm_problem -- 회차 판정과 같은 술어):
라우트가 트랜잭션 밖 스냅숏으로 지문을 찍으면 그 사이 CSV 교체가 동의 없이 지문에 묻는다.

응답 행 모양(목록·생성·수정·켜기·끄기 공통): 스케줄 열(schedule_id, batch_id, kind, spec, enabled(bool), next_run_at(UTC …Z |
null), armed_by, created_by, created_at, updated_at, last_run_at, last_outcome, last_reason_code, paused_reason_code,
pruned_at(UTC …Z | null), pruned_total(int | null -- 회차 보존 정리의 마지막 시각·누적 수, null = 아직 정리 없음, 0 과 다르다)) +
acknowledged_irreversible(bool -- 동의 지문이 있다) + destructive(bool | null -- 지금 배치 기준, 배치 행이 없으면 null) +
batch(배치 요약 | null: batch_id, name, operation, status, item_count,
auto_confirm, options, storage, source_storage, destination_storage) + keep_rounds(회차 보존 수 DMS_BATCH_SCHEDULE_KEEP_ROUNDS --
2026-11-16 사용자 결정: 스케줄이 있는 배치는 최근 K회차의 자식만 남고 그보다 오래된 회차의 자식은 컨트롤러 batch-round-prune
루프가 정리한다) + child_count_limit(배치 단위 삭제 자식 상한 = 설정 DMS_BATCH_DELETE_MAX_CHILDREN, 기본 50000 -- 2026-11-19;
화면의 「항목 × K > 상한」 경고 기준; 상한을 바꿔도 포탈 빌드와 어긋나지 않게 서버 값을 싣는다). 지문 원문은 싣지 않는다.
"""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, StrictBool

from ..batch_scheduler import (arm_problem, armed_fingerprint, is_destructive, next_run_after, preview_runs,
                               binding_digest, storage_binding, validate_schedule)
from ..db import utc_now_iso
from ..domain import DomainValidationError
from .auth import Identity, require_admin
from .routes_batches import _require_batch_privilege
from .routes_requests import reject_when_maintenance

router = APIRouter()

_RUNS_DEFAULT = 50
_RUNS_CAP = 200
# 저장소가 던지는 사유 → HTTP 상태(밖의 사유는 422 -- 무장 판정·규칙 검증).
_STATUS = {"batch_not_found": 404, "batch_schedule_not_found": 404, "batch_schedule_exists": 409,
           # 묘비가 선 배치(2026-11-19 -- 백그라운드 배치 단위 삭제 중)엔 무장하지 않는다(저장소 원자 가드).
           "batch_deleting": 409}


class ScheduleRule(BaseModel):
    # kind/spec 은 Any 로 받고 batch_scheduler.validate_schedule 이 판정한다 -- pydantic 타입 오류는 화면에서 http_422 일반
    # 문구가 되는데, 규칙 오류는 invalid_batch_schedule 하나로 말해야 포탈 폼이 같은 문구를 보인다.
    kind: Any = None
    spec: Any = None


class SchedulePreviewBody(ScheduleRule):
    # 고른 배치(선택, 2026-11-16 회차 보존 정리) -- 있으면 그 배치의 prune_preview(곧 정리될 작업 수 예측)를 함께 준다.
    batch_id: str | None = None


class ScheduleCreateBody(ScheduleRule):
    batch_id: str
    # 만들면서 켤지(기본 켜짐). 꺼진 채 만들어도 무장(armed_by·동의)은 같다 -- 켜기에서 다시 본다.
    enabled: StrictBool = True
    # 파괴적 스케줄의 「되돌릴 수 없음」 동의 -- **명시 true 만**(생략·false 는 파괴적 배치에서 422 batch_schedule_ack_required).
    # 파괴적이 아닌 스케줄은 보지 않는다(실어도 무시).
    acknowledge_irreversible: StrictBool | None = None


class ScheduleRuleBody(ScheduleRule):
    acknowledge_irreversible: StrictBool | None = None


class ScheduleEnableBody(BaseModel):
    acknowledge_irreversible: StrictBool | None = None


def _validated_rule(body: ScheduleRule) -> dict:
    try:
        return validate_schedule(body.kind, body.spec)
    except DomainValidationError as e:
        raise HTTPException(status_code=422, detail=e.reason_code)


def _domain_error(e: DomainValidationError) -> HTTPException:
    return HTTPException(status_code=_STATUS.get(e.reason_code, 422), detail=e.reason_code)


def _arm(request: Request, acknowledge):
    """저장소에 넘기는 무장 판정 콜백(repositories/batch_schedules 의 arm 계약): 잠근 배치·항목으로 회차 판정 6–8단계
    (요청자 자격·세션, 연산, 옵션 -- batch_scheduler.arm_problem)를 보고, 파괴적이면 동의를 요구한다. 돌려주는 것 =
    (파괴적이면 동의 지문 아니면 None, 그 순간의 스토리지 경로 바인딩 -- 연산 무관)."""
    repos, settings = request.app.state.repos, request.app.state.settings

    def check(batch, items):
        held = arm_problem(repos, settings, batch)
        if held is not None:
            raise DomainValidationError(held.reason_code)
        bound = storage_binding(repos, items)
        if any(v is None for v in bound.values()):
            # 없는 스토리지로 묶지 않는다(batch_hold 와 같은 판정 -- 같은 이름으로 다시 만든 경로를 사람 없이 쓰게 된다).
            raise DomainValidationError("storage_missing")
        binding = binding_digest(bound)
        if is_destructive(batch["operation"], batch.get("options")):
            if acknowledge is not True:
                raise DomainValidationError("batch_schedule_ack_required")
            return armed_fingerprint(repos, batch, items), binding
        return None, binding
    return check


def _out(row, settings) -> dict:
    out = {k: v for k, v in row.items() if k not in ("ack_fingerprint", "storage_binding")}
    out["acknowledged_irreversible"] = row.get("ack_fingerprint") is not None
    # 파괴적 판정은 지금 배치로(batch_scheduler.is_destructive -- 저장하지 않는다). 배치 행이 없으면 모름(None).
    batch = row.get("batch")
    out["destructive"] = None if batch is None else is_destructive(batch.get("operation"), batch.get("options"))
    # 회차 보존(2026-11-16): 설정값·정리 결과·배치 단위 삭제 상한. 아직 정리 없음은 null(0 이 아니다).
    out["keep_rounds"] = getattr(settings, "batch_schedule_keep_rounds", None)
    out.setdefault("pruned_at", None)
    out.setdefault("pruned_total", None)
    out["child_count_limit"] = settings.batch_delete_max_children
    return out


def _detail(request: Request, schedule_id: str) -> dict:
    rows = request.app.state.repos.batch_schedules.list(schedule_id=schedule_id)
    if not rows:
        raise HTTPException(status_code=404, detail="batch_schedule_not_found")
    return _out(rows[0], request.app.state.settings)


@router.get("/api/admin/batch-schedules")
def list_batch_schedules(request: Request, identity: Identity = Depends(require_admin)):
    settings = request.app.state.settings
    return [_out(r, settings) for r in request.app.state.repos.batch_schedules.list()]


@router.post("/api/admin/batch-schedules:preview")
def preview_batch_schedule(body: SchedulePreviewBody, request: Request, identity: Identity = Depends(require_admin)):
    """규칙의 다음 5회(UTC) -- 포탈은 다음 실행을 계산하지 않는다(KST·말일 당김 규칙을 두 벌 두지 않는다). 읽기 전용 계산
    (본문이 있어 POST). keep_rounds(회차 보존 수)·child_count_limit(배치 단위 삭제 자식 상한)은 늘 싣는다(추가 창의 보존 안내·
    「항목 × K > 상한」 예측). batch_id 를 주면 prune_preview = {rounds_recorded, would_prune, would_prune_capped}
    (BatchSchedulesRepository.prune_preview -- 지금 스케줄이 걸려 있다고 쳤을 때 곧 정리될 자식 수, 실제 정리와 같은 경계 계산
    _round_cut; 막힌 자식까지 세는 근사치, 배치가 없거나 기록 회차가 K 미만이면 0). batch_id 가 없으면 prune_preview 키가 없다."""
    rule = _validated_rule(body)
    settings = request.app.state.settings
    keep = getattr(settings, "batch_schedule_keep_rounds", None)
    out = {"next_runs": preview_runs(body.kind, rule, utc_now_iso(), 5),
           "keep_rounds": keep, "child_count_limit": settings.batch_delete_max_children}
    if body.batch_id is not None:
        out["prune_preview"] = request.app.state.repos.batch_schedules.prune_preview(body.batch_id, keep=keep)
    return out


@router.get("/api/admin/batch-schedules/{schedule_id}/runs")
def list_batch_schedule_runs(schedule_id: str, request: Request, limit: int = Query(_RUNS_DEFAULT, ge=1),
                             identity: Identity = Depends(require_admin)):
    repo = request.app.state.repos.batch_schedules
    if repo.get(schedule_id) is None:
        raise HTTPException(status_code=404, detail="batch_schedule_not_found")
    return repo.runs(schedule_id, limit=min(limit, _RUNS_CAP))


@router.post("/api/admin/batch-schedules", status_code=201)
def create_batch_schedule(body: ScheduleCreateBody, request: Request, identity: Identity = Depends(require_admin)):
    reject_when_maintenance(request)
    _require_batch_privilege(request, identity)
    rule = _validated_rule(body)
    next_at = next_run_after(body.kind, rule, utc_now_iso()) if body.enabled else None
    try:
        sid = request.app.state.repos.batch_schedules.create(
            batch_id=body.batch_id, kind=body.kind, spec=rule, enabled=body.enabled, next_run_at=next_at,
            actor=identity.actor, arm=_arm(request, body.acknowledge_irreversible))
    except DomainValidationError as e:
        raise _domain_error(e)
    return _detail(request, sid)


@router.patch("/api/admin/batch-schedules/{schedule_id}")
def update_batch_schedule(schedule_id: str, body: ScheduleRuleBody, request: Request,
                          identity: Identity = Depends(require_admin)):
    """규칙 변경 = 다시 무장(armed_by = 편집자, 파괴적이면 새 동의). 켜져 있으면 다음 실행을 지금 뒤 첫 슬롯으로 다시 잡는다."""
    reject_when_maintenance(request)
    _require_batch_privilege(request, identity)
    rule = _validated_rule(body)
    try:
        request.app.state.repos.batch_schedules.update_rule(
            schedule_id, kind=body.kind, spec=rule, next_run_at=next_run_after(body.kind, rule, utc_now_iso()),
            actor=identity.actor, arm=_arm(request, body.acknowledge_irreversible))
    except DomainValidationError as e:
        raise _domain_error(e)
    return _detail(request, schedule_id)


@router.post("/api/admin/batch-schedules/{schedule_id}:enable")
def enable_batch_schedule(schedule_id: str, request: Request, body: ScheduleEnableBody | None = None,
                          identity: Identity = Depends(require_admin)):
    """켜기(사람 끄기·자동 일시정지에서) = 다시 무장 -- 다음 실행은 지금 뒤 첫 슬롯(바로 쏘지 않는다). 이미 켜져 있으면 변경
    없이 지금 행을 돌려준다."""
    reject_when_maintenance(request)
    _require_batch_privilege(request, identity)
    ack = body.acknowledge_irreversible if body is not None else None
    try:
        # 다음 실행은 저장소가 잠근 행의 규칙으로 계산한다(여기서 미리 읽은 규칙은 그 사이 PATCH 로 낡을 수 있다). 없는
        # 스케줄은 404, 깨진 규칙(DB 변조)은 422 invalid_batch_schedule -- 고치는 길은 규칙 수정(PATCH)이다.
        request.app.state.repos.batch_schedules.enable(schedule_id, actor=identity.actor, arm=_arm(request, ack))
    except DomainValidationError as e:
        raise _domain_error(e)
    return _detail(request, schedule_id)


@router.post("/api/admin/batch-schedules/{schedule_id}:disable")
def disable_batch_schedule(schedule_id: str, request: Request, identity: Identity = Depends(require_admin)):
    """사람 끄기 -- 유지보수 중에도, 특권 게이트 없이(실행을 줄이기만 한다). 이미 꺼져 있으면 변경 없이 지금 행."""
    try:
        request.app.state.repos.batch_schedules.disable(schedule_id, actor=identity.actor)
    except DomainValidationError as e:
        raise _domain_error(e)
    return _detail(request, schedule_id)


@router.delete("/api/admin/batch-schedules/{schedule_id}")
def delete_batch_schedule(schedule_id: str, request: Request, identity: Identity = Depends(require_admin)):
    """스케줄 삭제(회차 이력 포함, 감사 행은 남는다) -- 실행을 줄이기만 해 require_admin, 유지보수 중에도. 보존 회차 경계와 이미
    남은 작업은 그대로이고 회차 보존 정리만 멈춘다(정리는 스케줄 행이 있는 배치만 본다 -- 저장소 delete docstring)."""
    try:
        request.app.state.repos.batch_schedules.delete(schedule_id, actor=identity.actor)
    except DomainValidationError as e:
        raise _domain_error(e)
    return {"deleted": schedule_id}
