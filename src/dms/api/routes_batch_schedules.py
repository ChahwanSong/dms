"""배치 스케줄 API(2026-11-16 -- 규칙·회차 판정은 dms.batch_scheduler, 저장은 repositories/batch_schedules).

경로가 /api/admin/batches/schedules 가 아닌 이유: GET /api/admin/batches/{batch_id} 와 등록 순서 함정이 생긴다.

게이트(배치 라우트와 같은 결 -- routes_batches 의 특권 블록 주석):
  - 스케줄을 **무장**하는 라우트(생성·규칙 수정·켜기)는 배치 생성과 같은 특권 3중 게이트(_require_batch_privilege -- 허용
    설정 + 특권 목록 + 세션; 공유 Bearer 토큰은 403 -- 토큰으로 무장을 허용하면 모든 노드 에이전트가 주기적 root 실행을 심을
    수 있다)와 유지보수 거부. 무장한 사람이 armed_by 로 남고 회차마다 그 사람의 자격을 다시 본다. 파괴적 스케줄(rm, delete 켠
    sync -- batch_scheduler.is_destructive)은 무장할 때마다 「되돌릴 수 없음」 동의(acknowledge_irreversible: **명시 true** 만,
    StrictBool)를 받고 그 순간의 배치 지문(batch_scheduler.armed_fingerprint -- 스토리지 경로 바인딩 포함)을 남긴다 -- 회차의
    배치가 그 지문과 다르면 자동 일시정지(사람이 다시 동의해야 돈다).
  - 끄기는 require_admin(실행을 줄이기만 한다 -- 유지보수 중에도 받는다, :auto-confirm-off·:cancel 과 같은 브레이크).
  - 삭제는 **require_session_admin**(admin_session_required): 회차 이력(증거)을 함께 지우므로 「배치 기록 삭제」(2026-10-26)와
    같은 경계다. 감사 행은 남는다. 실행을 줄이는 쪽이라 유지보수 중에도 받는다. (dms-ssc WIP 는 require_admin -- 더 엄격한
    쪽으로 갔고 역이식을 제안한다.)
  - 조회·미리보기는 require_admin(토큰 조회 가능 -- 다른 관리 GET 과 같다).
무장 판정(arm)은 저장소 트랜잭션 안, 항목 → 배치 잠금 뒤의 행으로 한다(batch_scheduler.arm_problem -- 회차 판정과 같은 술어):
라우트가 트랜잭션 밖 스냅숏으로 지문을 찍으면 그 사이 CSV 교체가 동의 없이 지문에 묻는다.

응답 행 모양(목록·생성·수정·켜기·끄기 공통): 스케줄 열(schedule_id, batch_id, kind, spec, enabled(bool), next_run_at(UTC …Z |
null), armed_by, created_by, created_at, updated_at, last_run_at, last_outcome, last_reason_code, paused_reason_code) +
acknowledged_irreversible(bool -- 동의 지문이 있다) + destructive(bool | null -- 지금 배치 기준, 배치 행이 없으면 null) +
batch(배치 요약 | null: batch_id, name, operation, status, item_count, requester_id, auto_confirm, auto_confirm_by, options,
storage, source_storage, destination_storage) + **k8s 추가** child_count(재실행 이력 포함 자식 요청 수 -- capped 면 상한 + 1)·
child_count_capped·child_count_limit(배치 단위 삭제 상한 request_purges.MAX_BATCH_DELETE_CHILDREN 5000 -- 화면의 80% 경고 기준) +
keep_rounds(회차 보존 수 DMS_BATCH_SCHEDULE_KEEP_ROUNDS -- 2026-11-16 사용자 결정: 스케줄이 있는 배치는 최근 K회차의 자식만 남고
그보다 오래된 회차의 자식은 컨트롤러 batch-round-prune 루프가 정리한다) + pruned_at(UTC …Z | null)·pruned_total(int | null --
null = 아직 정리 없음, 0 과 다르다) + confirm_wait(batch_scheduler.confirm_wait_note -- 자동 확인이 꺼진
sync·rm 의 주기가 미리보기 만료보다 짧거나 같으면 {period_seconds, preview_ttl_seconds, unconfirmed_every_seconds}, 아니면
null -- 판정은 그대로 건너뜀 기록이고 이것은 경고일 뿐이다). 지문 원문은 싣지 않는다.
"""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, StrictBool

from ..batch_scheduler import (arm_problem, armed_fingerprint, confirm_wait_note, is_destructive, next_run_after,
                               preview_runs, validate_schedule)
from ..db import utc_now_iso
from ..domain import DomainValidationError
from ..repositories.request_purges import MAX_BATCH_DELETE_CHILDREN
from .auth import Identity, audit_actor, require_admin, require_session_admin
from .routes_batches import _require_batch_privilege
from .routes_requests import reject_when_maintenance

router = APIRouter()

_RUNS_DEFAULT = 50
# 회차 이력 조회 상한 = 저장소 보존 상한(RUN_HISTORY_CAP)과 같은 값.
_RUNS_CAP = 200
# 저장소가 던지는 사유 → HTTP 상태(밖의 사유는 422 -- 무장 판정·규칙 검증).
_STATUS = {"batch_not_found": 404, "batch_schedule_not_found": 404, "batch_schedule_exists": 409}


class ScheduleRule(BaseModel):
    # kind/spec 은 Any 로 받고 batch_scheduler.validate_schedule 이 판정한다 -- pydantic 타입 오류는 화면에서 http_422 일반
    # 문구가 되는데, 규칙 오류는 invalid_batch_schedule 하나로 말해야 포탈 폼이 같은 문구를 보인다.
    kind: Any = None
    spec: Any = None


class SchedulePreviewBody(ScheduleRule):
    # 고른 배치(선택) -- 있으면 확인 대기 경고(confirm_wait)를 그 배치의 연산·자동 확인으로 계산해 함께 준다.
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
    (요청자 자격·세션, 연산, 옵션, 스토리지·denylist -- batch_scheduler.arm_problem)를 보고, 파괴적이면 동의를 요구하고 지문을
    돌려준다."""
    repos, settings = request.app.state.repos, request.app.state.settings

    def check(batch, items):
        held = arm_problem(repos, settings, batch, items)
        if held is not None:
            raise DomainValidationError(held.reason_code)
        if is_destructive(batch["operation"], batch.get("options")):
            if acknowledge is not True:
                raise DomainValidationError("batch_schedule_ack_required")
            return armed_fingerprint(repos, batch, items)
        return None
    return check


def _out(row, *, settings, summary) -> dict:
    out = {k: v for k, v in row.items() if k != "ack_fingerprint"}
    out["acknowledged_irreversible"] = row.get("ack_fingerprint") is not None
    # 파괴적 판정은 지금 배치로(batch_scheduler.is_destructive -- 저장하지 않는다). 배치 행이 없으면 모름(None).
    batch = row.get("batch")
    out["destructive"] = None if batch is None else is_destructive(batch.get("operation"), batch.get("options"))
    out["confirm_wait"] = None if batch is None else confirm_wait_note(
        row.get("kind"), row.get("spec"), operation=batch.get("operation"), auto_confirm=batch.get("auto_confirm"),
        preview_ttl_seconds=getattr(settings, "preview_ttl_seconds", None))
    s = summary or {}
    out["child_count"] = s.get("batch_request_count", 0)
    out["child_count_capped"] = s.get("batch_request_count_capped", False) is True
    out["child_count_limit"] = MAX_BATCH_DELETE_CHILDREN
    out["keep_rounds"] = getattr(settings, "batch_schedule_keep_rounds", None)
    out.setdefault("pruned_at", None)
    out.setdefault("pruned_total", None)
    return out


def _rows_out(request: Request, rows) -> list:
    repos, settings = request.app.state.repos, request.app.state.settings
    summaries = repos.batches.list_summaries([r["batch_id"] for r in rows], cap=MAX_BATCH_DELETE_CHILDREN)
    return [_out(r, settings=settings, summary=summaries.get(r["batch_id"])) for r in rows]


def _detail(request: Request, schedule_id: str) -> dict:
    rows = request.app.state.repos.batch_schedules.list(schedule_id=schedule_id)
    if not rows:
        raise HTTPException(status_code=404, detail="batch_schedule_not_found")
    return _rows_out(request, rows)[0]


@router.get("/api/admin/batch-schedules")
def list_batch_schedules(request: Request, identity: Identity = Depends(require_admin)):
    return _rows_out(request, request.app.state.repos.batch_schedules.list())


@router.post("/api/admin/batch-schedules:preview")
def preview_batch_schedule(body: SchedulePreviewBody, request: Request, identity: Identity = Depends(require_admin)):
    """규칙의 다음 5회(UTC) -- 포탈은 다음 실행을 계산하지 않는다(KST·말일 당김 규칙을 두 벌 두지 않는다). 읽기 전용 계산
    (본문이 있어 POST). batch_id 를 주면 그 배치 기준 확인 대기 경고(confirm_wait)도 -- 배치가 없거나 읽지 못하면 null.
    keep_rounds(회차 보존 수)·child_count_limit(배치 단위 삭제 상한)은 늘 싣는다(추가 창의 보존 안내·「항목 × K > 상한」 예측).
    batch_id 를 주면 prune_preview = {rounds_recorded, would_prune, would_prune_capped}(BatchSchedulesRepository.prune_preview --
    지금 스케줄이 걸려 있다고 쳤을 때 곧 정리될 자식 수, 실제 정리와 같은 경계 계산 _round_cut; 막힌 자식까지 세는 근사치,
    배치가 없거나 기록 회차가 K 미만이면 0)."""
    rule = _validated_rule(body)
    settings = request.app.state.settings
    keep = getattr(settings, "batch_schedule_keep_rounds", None)
    out = {"keep_rounds": keep, "child_count_limit": MAX_BATCH_DELETE_CHILDREN}
    note = None
    if body.batch_id is not None:
        out["prune_preview"] = request.app.state.repos.batch_schedules.prune_preview(body.batch_id, keep=keep)
        try:
            batch = request.app.state.repos.batches.get(body.batch_id)
        except (ValueError, TypeError):
            batch = None                    # 깨진 options(DB 변조) -- 경고를 지어내지 않는다
        if batch is not None:
            note = confirm_wait_note(body.kind, rule, operation=batch.get("operation"),
                                     auto_confirm=batch.get("auto_confirm_by") is not None,
                                     preview_ttl_seconds=getattr(request.app.state.settings, "preview_ttl_seconds", None))
    return {"next_runs": preview_runs(body.kind, rule, utc_now_iso(), 5), "confirm_wait": note, **out}


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
    """사람 끄기 -- 유지보수 중에도, 특권 게이트 없이(실행을 줄이기만 한다). 이미 꺼져 있으면 변경 없이 지금 행(멱등)."""
    try:
        request.app.state.repos.batch_schedules.disable(schedule_id, actor=audit_actor(identity))
    except DomainValidationError as e:
        raise _domain_error(e)
    return _detail(request, schedule_id)


@router.delete("/api/admin/batch-schedules/{schedule_id}")
def delete_batch_schedule(schedule_id: str, request: Request, identity: Identity = Depends(require_session_admin)):
    """스케줄 삭제(회차 이력 포함, 감사 행은 남는다) -- 세션 관리자만(모듈 docstring), 유지보수 중에도."""
    try:
        request.app.state.repos.batch_schedules.delete(schedule_id, actor=audit_actor(identity))
    except DomainValidationError as e:
        raise _domain_error(e)
    return {"deleted": schedule_id}
