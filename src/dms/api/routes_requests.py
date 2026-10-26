import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, StrictBool
from ..artifact_base import resolve_artifact_base
from ..domain import (
    DataJobState, DomainValidationError, Operation, PRIORITIES, RequestState,
    TERMINAL_DATA_JOB_STATES, TERMINAL_REQUEST_STATES, build_data_payload,
    resolve_priority, validate_owner_username,
)
from ..execution import ExecutionError
from ..identity import owner_override_allowed, root_allowed
from .. import k8s_access
from ..privileged import privileged_names
from ..repositories.request_purges import MAX_BATCH_DELETE_CHILDREN
from ..repositories.storages import storage_open_to_users
from ..repositories.sync_pairs import sync_pair_allowed
from ..repositories.text_search import search_tokens
from .artifacts import ArtifactError, job_owner_uid, read_artifact, strip_scheme
from .auth import Identity, can_run_as_root, require_admin, require_user
from .cancel import sweep_after_cancel, terminate_job
from .routes_jobs import _owned_request
# 모양 투영은 scan_path_stats 와 **한 벌**을 공유한다(routes_scan_paths.py:19-73 의
# 「왜」: 키 화이트리스트만으로는 리포트 값 안의 경로 유출을 못 막는다 — 숫자와
# 구간 라벨만 통과시킨다). 두 벌이 되면 한쪽만 고치는 드리프트가 생긴다.
from .routes_scan_paths import (_buckets, _is_number, _numbers,
                                _time_histograms, _total_bytes)

router = APIRouter()

# 작업 목록 검색칸 한 칸의 길이 상한(2026-10-25) -- 사용량 분석 검색(routes_usage)과 같은 512.
SEARCH_MAX_LENGTH = 512


def reject_when_maintenance(request: Request) -> None:
    # 유지보수 창에는 신규 유입을 막는다 (진행 중인 잡은 건드리지 않는다 — 그건 drain의 몫).
    # 관리자도 예외가 아니다. 콘솔의 control-state PUT은 제출 경로가 아니라 잠기지 않는다.
    state = request.app.state.repos.control.control_state()
    if state and state["maintenance"]:
        raise HTTPException(status_code=503, detail="maintenance_mode")


class PvRef(BaseModel):
    """PV 주소(dms-k8s 설계 §6) -- (클러스터, namespace, PVC 이름). 하위 경로는 그 쪽의 target/source/destination 칸이다."""
    cluster: str
    namespace: str
    pvc: str


class RequestBody(BaseModel):
    operation: str
    storage: str | None = None
    source_storage: str | None = None
    destination_storage: str | None = None
    target: str | None = None
    source: str | None = None
    destination: str | None = None
    options: dict = {}
    priority: str | None = None
    owner_username: str | None = None
    # root 실행(2026-10-25 설계 D2 -- dms-k8s 는 k8s 신원 모드 전용): 모든 단건 잡이 root 라 서버가 True 를 싣는다(생략 =
    # root). 명시 False 는 화면이 「비 root」라고 믿고 보낸 값이라 조용히 root 로 바꾸지 않고 422 run_as_root_required 로
    # 끊는다. StrictBool -- "yes"·1 같은 값이 다른 뜻으로 읽히지 않게.
    run_as_root: StrictBool | None = None
    # PV 주소(k8s, 설계 §6): scan·rm 은 pv + target(PV 안 하위 경로, '' = PV 전체 -- rm 은 D12 로 거부), sync 는 쪽마다
    # source_pv + source / destination_pv + destination(쪽마다 선택 -- 면제자는 PV ↔ 직접 경로 혼합). 같은 쪽에 storage 와
    # pv 를 함께 주면 422 pv_and_storage_conflict.
    pv: PvRef | None = None
    source_pv: PvRef | None = None
    destination_pv: PvRef | None = None


def _require(value: str | None, reason: str) -> str:
    if not value:
        raise DomainValidationError(reason, "required field missing")
    return value


def _resolve_locations(body: RequestBody, repos) -> tuple[dict, dict]:
    """바디의 쪽마다 스토리지·경로를 정한다(PV 주소면 k8s_access.resolve_side 로 해석). 반환 (위치, pv 블록들) -- 위치는
    storage·target·source_storage·source·destination_storage·destination 여섯 칸(PV 주소가 아닌 쪽은 바디 값 그대로).
    충돌 검사(pv_and_storage_conflict)를 모든 쪽에 먼저 한 뒤 해석한다 -- 해석의 409·422 가 바디 모양 오류를 가리지 않게."""
    loc = {"storage": body.storage, "target": body.target, "source_storage": body.source_storage,
           "source": body.source, "destination_storage": body.destination_storage, "destination": body.destination}
    present = [(pv_key, storage_key, path_key) for pv_key, storage_key, path_key in k8s_access.sides(body.operation)
               if getattr(body, pv_key) is not None]
    for pv_key, storage_key, _ in present:
        if getattr(body, storage_key) not in (None, ""):
            raise DomainValidationError("pv_and_storage_conflict", pv_key)
    blocks = {}
    for pv_key, storage_key, path_key in present:
        side = k8s_access.resolve_side(repos, getattr(body, pv_key).model_dump(), getattr(body, path_key),
                                       operation=body.operation)
        loc[storage_key], loc[path_key] = side["storage"], side["target"]
        blocks[pv_key] = side["block"]
    return loc, blocks


def _validated_payload(body: RequestBody, priority: str,
                       run_as_root: bool, loc: dict) -> tuple[dict, str]:
    op = Operation(body.operation)
    if priority not in PRIORITIES:
        raise DomainValidationError("invalid_priority", priority)
    if body.owner_username is not None:
        validate_owner_username(body.owner_username)
    # storage 필드 존재 검증은 여기서 구체적 reason_code로 먼저 수행한다
    # (build_data_payload는 sync에 대해 "missing_storage" 하나로 뭉뚱그리므로,
    #  source/destination을 구분하는 기존 API 계약(422 reason_code)을 유지하려면
    #  존재 체크는 라우트에서, 옵션 검증+경로 검증+fingerprint+resource_key는
    #  build_data_payload에 위임한다 — build_data_payload가 이미 validate_options를
    #  수행하고 그 결과를 payload["options"]에 담아 반환하므로 여기서 다시
    #  validate_options를 호출하지 않는다).
    if op is Operation.SYNC:
        src_storage = _require(loc["source_storage"], "missing_source_storage")
        dst_storage = _require(loc["destination_storage"], "missing_destination_storage")
        payload, key = build_data_payload(
            "sync", source_storage=src_storage, source=loc["source"],
            destination_storage=dst_storage, destination=loc["destination"],
            options=body.options)
    elif op is Operation.RM:
        storage = _require(loc["storage"], "missing_storage")
        payload, key = build_data_payload(
            "rm", storage=storage, target=loc["target"], options=body.options)
    else:
        storage = _require(loc["storage"], "missing_storage")
        payload, key = build_data_payload(
            "scan", storage=storage, target=loc["target"], options=body.options)
    payload["owner_username"] = body.owner_username
    # 확정된 root 일 때만 키를 싣는다 -- 키 부재는 "root 아님" 이고 planner 는 `is True`
    # 로만 승격한다(모름을 특권 쪽으로 읽지 않는다).
    if run_as_root:
        payload["run_as_root"] = True
    return payload, key


@router.post("/api/user/requests", status_code=202)
def submit(body: RequestBody, request: Request,
           identity: Identity = Depends(require_user)):
    reject_when_maintenance(request)
    # 사용자 연산 allowlist(2026-08-20, 사용자 결정): 비운영자는 settings.
    # user_allowed_operations(기본 {sync}) 밖의 연산을 제출할 수 없다 -- rm·scan 은
    # 일단 잠긴다. admin 은 무관하게 전부 가능. 원시 문자열로 비교한다(Operation(...)
    # 은 알 수 없는 값에 ValueError 를 던지는데 여기는 그것을 422 로 바꾸는 try 밖이라
    # 500 이 되어 버린다). 표시 게이트(SubmitJob isAdmin)와 짝인 서버 강제다.
    settings = request.app.state.settings
    # **알려진** 연산 중 미허용만 403 -- 알 수 없는 연산(bogus)은 이 게이트를
    # 통과시켜 아래 try 의 422 invalid_operation 으로 흘린다("bogus 는 admin 전용"이
    # 아니라 "그런 연산이 없다"가 맞다). 알려진 집합은 Operation enum 이 단일 진실.
    known_ops = {o.value for o in Operation}
    repos = request.app.state.repos
    # scan 은 특권 운영자만(2026-10-26 (g)) -- allowlist 보다 먼저 정확한 사유로(목록 밖 관리자도 여기서 막힌다).
    if body.operation == Operation.SCAN.value and not k8s_access.scan_allowed(
            unrestricted=can_run_as_root(identity, settings, repos)):
        raise HTTPException(status_code=403, detail="scan_unrestricted_only")
    if (identity.role != "admin" and body.operation in known_ops
            and body.operation not in settings.user_allowed_operations):
        raise HTTPException(status_code=403, detail="operation_admin_only")
    # k8s 신원 모드(2026-10-25 설계 D2·D3): 모든 잡이 root 이고 root 는 세션 인증 요청만이다 -- 공유 토큰 제출은 어떤 경로로도
    # 통과할 수 없으니 정확한 사유로 먼저 끊는다(뒤의 raw·맵핑 게이트가 엉뚱한 사유를 내지 않게). 아래 root 게이트가 같은
    # 술어(identity.root_allowed)로 다시 본다.
    if identity.auth != "session":
        raise HTTPException(status_code=403, detail="privileged_not_authorized")
    # PV 주소 해석(설계 §6 게이트 순서: 유지보수 → 연산 → 클러스터 존재·동기화(409) → PV 해석(422) → 해석된 스토리지로
    # storage_admin_only → 맵핑·raw(403) → sync 쌍 → owner·root → build_data_payload). 이후의 스토리지 게이트는 전부 **해석된**
    # 스토리지 이름(loc)을 본다 -- PV 주소 제출은 바디의 storage 칸이 비어 있어 예전 게이트를 조용히 건너뛰었다(리뷰).
    try:
        loc, pv_blocks = _resolve_locations(body, repos)
    except DomainValidationError as e:
        raise HTTPException(status_code=k8s_access.SUBMIT_STATUS.get(e.reason_code, 422), detail=e.reason_code)
    # 관리자 전용 스토리지(2026-09-30 사용 범위, storage_open_to_users): 비관리자 피커에서
    # 빠지는 건 표시일 뿐이라 여기서 막는다(planner 가 계획 시점에 다시 본다 -- 대기 중
    # 관리자 전용으로 바뀐 요청). 완전 비활성(enabled=0)은 종전대로 planner storage_disabled.
    if identity.role != "admin":
        for storage_name in (loc["storage"], loc["source_storage"], loc["destination_storage"]):
            row = repos.storages.get(storage_name) if storage_name else None
            if row is not None and row["enabled"] == 1 and not storage_open_to_users(row):
                raise HTTPException(status_code=403, detail="storage_admin_only")
    # 맵핑·raw 게이트(설계 D4): 면제자(can_run_as_root -- 관리자 + 특권 목록/부여 + 세션)만 직접 경로·전체 PV. 그 밖은
    # 쪽마다 PV 주소여야 하고(raw_path_not_allowed) 요청자 ID 의 인식된 RoleBinding 이 그 (클러스터, namespace)에 있어야
    # 한다(k8s_access_denied). 스토리지 칸도 PV 도 없는 쪽은 아래 검증의 정확한 422(missing_*)로 흘린다. planner·컨펌이 요청
    # 행으로 다시 본다(k8s_access.recheck_request -- 대기 중 권한 회수·DB 직접 삽입).
    if not can_run_as_root(identity, settings, repos):
        # 결과 소유자는 목적지 PV 루트 소유자로 자동으로 맞춘다(2026-10-26 사용자 결정) -- 명시 chown 은 특권 운영자만(일반
        # 사용자가 API 로 그 규칙을 덮지 못하게). planner·컨펌이 요청 행으로 다시 본다(k8s_access.recheck_request).
        if "chown" in (body.options or {}):
            raise HTTPException(status_code=403, detail="chown_not_allowed")
        # chmod 도 같은 원칙(2026-10-26 보안 리뷰 -- setuid/setgid 비트를 실은 결과 파일을 못 만들게)
        if "chmod" in (body.options or {}):
            raise HTTPException(status_code=403, detail="chmod_not_allowed")
        for pv_key, storage_key, _ in k8s_access.sides(body.operation):
            if pv_key not in pv_blocks and getattr(body, storage_key) not in (None, ""):
                raise HTTPException(status_code=403, detail="raw_path_not_allowed")
        for block in pv_blocks.values():
            if repos.k8s.access_for(identity.actor, block["cluster"], block["namespace"]) is None:
                raise HTTPException(status_code=403, detail="k8s_access_denied")
    # 사용자 sync 허용 스토리지 쌍(2026-09-30, repositories/sync_pairs.py): 기본 전부 불가 --
    # 관리자가 허용한 (소스 -> 목적지) 쌍만. 포탈의 후보 필터는 표시일 뿐이라 여기서 막는다
    # (planner·컨펌이 다시 본다 -- 대기 중 허용이 빠진 요청). 스토리지 필드가 빠진 요청은
    # 아래 검증이 정확한 422(missing_*)를 내도록 쌍 검사를 건너뛴다. 양쪽이 PV 주소면 면제다(설계 D14 -- PV 권한이
    # 판정한다; planner 3b·컨펌이 같은 술어 k8s_access.pair_gate_exempt).
    if (identity.role != "admin" and body.operation == Operation.SYNC.value
            and loc["source_storage"] and loc["destination_storage"]
            and not k8s_access.pair_gate_exempt(pv_blocks)
            and not sync_pair_allowed(repos, loc["source_storage"], loc["destination_storage"])):
        raise HTTPException(status_code=403, detail="sync_pair_not_allowed")
    # 특권 게이트 (스펙 §5): owner_username이 요청자와 다르면 특권 의도 → 인가 필요(면제자의 「사용자 지정」 -- 실행은
    # 그래도 root 이고 권한 판정은 요청자 기준이다, k8s_access 모듈 docstring).
    # 술어는 identity.owner_override_allowed 하나 -- planner 가 계획 시점에 같은 함수로 다시 본다(DB 직접 쓰기
    # 방어, 2026-10-07). 원문 비교라 ' alice' 같은 값은 여기서 403(모양 422 는 그 뒤 _validated_payload).
    owner = body.owner_username
    names = privileged_names(settings, repos)
    if not owner_override_allowed(owner_username=owner, requester_id=identity.actor,
                                  requester_is_admin=(identity.role == "admin"),
                                  allow_privileged=settings.allow_privileged_requesters,
                                  privileged_requesters=names):
        raise HTTPException(status_code=403, detail="privileged_not_authorized")
    # root 실행 게이트(설계 D2): 모든 단건 잡이 root 다 -- 서버가 True 를 싣고(생략 = root), 명시 False 는 422. 허용은
    # identity.root_allowed(세션 인증) -- 계획(resolve_job_identity)·컨펌(confirm_job)이 같은 술어다(DB 가 신뢰 경계라 payload
    # 변조·설정 변경을 막는 2차 방어). 권한 판정은 위 맵핑 게이트.
    if body.run_as_root is False:
        raise HTTPException(status_code=422, detail="run_as_root_required")
    run_as_root = True
    if not root_allowed(allow_privileged=settings.allow_privileged_requesters,
                        session_authenticated=(identity.auth == "session")):
        raise HTTPException(status_code=403, detail="privileged_not_authorized")

    priority = resolve_priority(repos, body.operation, body.priority)
    try:
        # sync 경로 겹침: 같은 스토리지는 여기서(build_data_payload, 상대경로), 다른 스토리지는 실제 경로로 planner 가 도구를
        # 정한 뒤 본다(dsync -- 두 스토리지가 한 노드의 같은 이름 공간일 때만 뜻이 있다, 2026-10-12).
        payload, resource_key = _validated_payload(body, priority, run_as_root, loc)
    except (DomainValidationError, ValueError) as e:
        reason = getattr(e, "reason_code", "invalid_operation")
        raise HTTPException(status_code=422, detail=reason)
    # PV 블록은 build_data_payload 반환 **뒤에** 덧붙인다(그 반환은 단건 계약 -- tests/test_domain_batch.py). planner·컨펌이
    # 이 블록으로 재확인한다(k8s_access.recheck_request). target_label 은 작업 목록 경로 검색용 사람 표기(설계 D17).
    payload.update(pv_blocks)
    rid = repos.requests.create(
        operation=body.operation, requester_id=identity.actor, actor=identity.actor,
        resource_key=resource_key, payload=payload, priority=priority,
        # 특권 승격을 session 요청에만 허용하기 위해 인증 방식을 요청에 실어 둔다
        # (planner 가 plan 시점에 읽는다, 설계 §2.2-2).
        auth_method=identity.auth,
        target_label=k8s_access.target_label(body.operation, payload))
    return {"request_id": rid, "state": "Pending"}


@router.get("/api/user/requests")
def list_requests(request: Request, identity: Identity = Depends(require_user),
                  # 대시보드 「최근 작업」이 200을 요청한다. 서버가 1..200으로 캡한다
                  # -- 무제한이면 전량 SELECT 가 화면 하나에 끌려 나온다. 범위 밖은
                  # FastAPI 기본 422(이 라우터의 도메인 422 는 detail=사유코드지만,
                  # 쿼리 파라미터 검증은 프레임워크 몫이라 굳이 흉내내지 않는다).
                  limit: int = Query(50, ge=1, le=200),
                  # 필터·커서(슬라이스 39, 전체 작업 화면). operation·state 는 값
                  # 검증을 서버에서 하지 않는다 -- 미지 값은 결과 0건이라 무해하고,
                  # enum 강제는 화면 select 가 이미 한다. before 는 무한 스크롤 커서.
                  operation: str | None = Query(None),
                  state: str | None = Query(None),
                  # 검색칸(2026-10-25, 「전체 작업」): 셋 다 대소문자를 무시한 부분 일치, 공백으로 나눈 토큰은 AND
                  # (text_search.search_tokens, RequestsRepository.list docstring). requester(관리자 -- 예전엔 정확 일치,
                  # 결과는 그 상위집합이라 하위 호환)·batch_name(관리자 -- 배치 이름 또는 6자 이상 id 앞부분)·path(누구나 --
                  # 화면 「대상」 표기). 길이 상한은 SQL 크기를 묶는다(넘으면 프레임워크 422 -- 사유 코드 흉내 없음, 위 limit 주석).
                  requester: str | None = Query(None, max_length=SEARCH_MAX_LENGTH),
                  batch_name: str | None = Query(None, max_length=SEARCH_MAX_LENGTH),
                  path: str | None = Query(None, max_length=SEARCH_MAX_LENGTH),
                  # 한 배치의 작업만(2026-10-11) -- 배치 상세의 「전체 작업에서 이 배치의 작업 보기」와 배치 기록만
                  # 지워진 묶음 칸이 여기로 온다. 오래된 배치의 작업은 무한 스크롤 수십 쪽 아래라, 배치 단위 삭제가
                  # 불러온 행에서만 시작되는 화면에선 찾을 길이 없었다(검증 지적). 좁히기만 하므로 누구에게나 열어도
                  # 격리는 그대로다(비운영자는 아래에서 requester 가 자기 자신으로 강제된다). 빈 값은 필터 없음.
                  batch_id: str | None = Query(None),
                  before: int | None = Query(None, ge=1)):
    # 비운영자는 requester 를 자기 자신으로 **강제**한다 -- requester 파라미터로
    # 남의 작업을 넓혀 볼 수 없다(격리). 운영자만 requester 필터를 존중한다.
    # 격리 값은 정확 일치 kwarg(requester_id)로만 넘긴다 -- 부분 일치(requester_like)는 운영자 검색 전용이다. 강제된 값이
    # LIKE 를 타면 'al' 이 'alice' 의 작업을 본다(RequestsRepository.list docstring). 배치 이름 검색도 운영자만: 비운영자
    # 응답엔 배치 요약이 없으니(아래) 검색 결과의 있고 없음으로 배치 이름을 캐 보는 길이 된다. 경로 검색은 자기 행 안에서만
    # 좁히므로 누구에게나 연다.
    admin = identity.role == "admin"
    repos = request.app.state.repos
    rows = repos.requests.list(
        requester_id=None if admin else identity.actor,
        requester_like=search_tokens(requester) if admin else None,
        batch_like=search_tokens(batch_name) if admin else None,
        path_like=search_tokens(path),
        operation=operation, state=state, batch_id=batch_id or None,
        before=before, limit=limit)
    # 사용량 분석 지점(성공 scan 잡)을 가진 요청인가(2026-10-09) -- 작업 삭제 확인 창의 사용량 경고가 쓴다. 요청
    # 상태로는 판정할 수 없다: 취소 경합으로 「요청 Cancelled · 잡 Succeeded」가 생기고 사용량 분석은 잡 상태를 본다.
    # 비 scan 은 false(모름이 아니라 「지점 없음」이 확정값이다).
    hits = repos.data_jobs.usage_scan_request_ids(
        [r["request_id"] for r in rows if r["operation"] == "scan"])
    for row in rows:
        row["has_succeeded_scan"] = row["request_id"] in hits
    # 배치 자식 행의 배치 요약(2026-10-10, **관리자 응답만**) -- 작업 목록의 「배치」 열과 배치 단위 삭제(선택·확인 창의
    # 자식 수 = 삭제 CAS 의 expected_request_count)가 쓴다. 서버가 세는 이유는 BatchesRepository.list_summaries.
    # 단건 행(batch_id NULL)과 비관리자 응답엔 키가 없다. batch_id "" 도 열쇠다(is not None -- truthy 검사 금지).
    # 자식 수는 배치 단위 삭제의 상한 + 1 에서 세기를 멈춘다(폴링마다 큰 배치의 자식 전부를 세지 않는다 --
    # list_summaries docstring).
    if identity.role == "admin":
        bids = list(dict.fromkeys(r["batch_id"] for r in rows if r["batch_id"] is not None))
        if bids:
            summaries = repos.batches.list_summaries(bids, cap=MAX_BATCH_DELETE_CHILDREN)
            for row in rows:
                s = summaries.get(row["batch_id"]) if row["batch_id"] is not None else None
                if s is not None:
                    row.update(s)
    return rows


# 요청 상태 문자열의 종단 집합. RequestState(...) 로 열거형을 거치지 않는 이유:
# state 는 DB 에서 온 값이고 DB 는 신뢰 경계다 — 모르는 문자열 하나가 상세 화면
# 전체를 500 으로 만들면 안 된다. 집합 밖은 "비종단"으로 fail-closed 처리된다.
_TERMINAL_REQUEST_STATE_VALUES = frozenset(s.value for s in TERMINAL_REQUEST_STATES)


def _attach_terminal_result(repo, row: dict) -> None:
    """요청 자신의 종단 사유를 상세 응답에 싣는다(배치 items 조인의 평평한 관례를
    미러 — request_state/files_count/completed_at 처럼 최상위 키다).

    이게 없던 동안 화면은 **잡의** reason_code 만 읽었다: 잡이 만들어지기 전에
    거부된 요청(플래너 어드미션 — ldap_identity_not_found·no_eligible_nodes·
    storage_missing 등)은 잡 행이 아예 없어 사유가 화면에서 통째로 사라졌고,
    잡이 있어도 「요청 정보」에는 사유 자리가 없었다(사용자 보고).

    사유의 소스는 results 행이 1순위다(전이와 원자적으로 기록된다 — 같은
    reason_code 이므로 전이와 어긋날 수 없다). results 행이 없는데 요청은 종단인
    경우에만 마지막 전이의 사유로 폴백한다: 슬라이스 27/30 이전에 종단이 된 요청은
    "Rejected 인데 results 없음"이 될 수 있었고, 그 요청도 사유를 잃으면 안 된다.
    폴백은 사유만 되살린다 — terminal_state/completed_at 은 null 로 남겨 "결과 행
    자체가 없다"는 사실을 숨기지 않는다.

    비종단은 전부 null 이다(키를 빼지 않는다): null 은 "아직 모른다"이지 "사유
    없음"이 아니다 — 프론트가 둘을 구분할 수 있어야 한다(null≠0 규약의 같은 결)."""
    result = repo.result(row["request_id"])
    row["terminal_state"] = result["terminal_state"] if result else None
    row["completed_at"] = result["completed_at"] if result else None
    # results.message 는 사유 코드에 딸린 자유 텍스트다. 최상위 "message" 는 무엇에
    # 대한 메시지인지 모호해서(요청? 이벤트?) reason_message 로 이름을 붙인다.
    row["reason_message"] = result["message"] if result else None
    if result is not None:
        row["reason_code"] = result["reason_code"]
    elif row["state"] in _TERMINAL_REQUEST_STATE_VALUES:
        row["reason_code"] = repo.last_reason_code(row["request_id"])
    else:
        row["reason_code"] = None


@router.get("/api/user/requests/{request_id}")
def get_request(request_id: str, request: Request,
                identity: Identity = Depends(require_user)):
    repo = request.app.state.repos.requests
    row = repo.get(request_id)
    if row is None or (identity.role != "admin"
                       and row["requester_id"] != identity.actor):
        raise HTTPException(status_code=404, detail="request_not_found")
    row["transitions"] = repo.transitions(request_id)
    _attach_terminal_result(repo, row)
    # events_for_request 기본 limit(100)이 조용히 잘리면 운영자가 눈치채지 못한다 --
    # 스택된 요청이 매 컨트롤러 틱마다 같은 예외로 실패를 반복하면 100건을 넘기는 것도
    # 현실적이다(plan_error/step_error는 정확히 이런 루프에서 기록된다). 표시 상한보다
    # 하나 더 가져와 잘림 여부를 판별한다. events_for_request는 이제 최신 N건을
    # 오름차순으로 돌려주므로(오래된 쪽을 버린다), 잘렸을 때 버릴 것도 리스트 앞쪽
    # (가장 오래된 한 건)이다 -- events[-display_limit:]로 뒤에서부터 자른다.
    display_limit = 100
    events = request.app.state.repos.observability.events_for_request(
        request_id, limit=display_limit + 1)
    row["events_truncated"] = len(events) > display_limit
    row["events"] = events[-display_limit:]
    return row


@router.get("/api/admin/requests/{request_id}/scan-stats")
def request_scan_stats(request_id: str, request: Request,
                       identity: Identity = Depends(require_admin)):
    """이 요청의 성공 scan 잡 리포트 1건을 모양 투영해 서빙한다 — scan_path_stats
    와 같은 단일 리포트 계약(합산 없음. 배치 합산 라우트는 제거됐다 — 사용자 결정:
    온도는 배치 전체가 아니라 항목별로 본다). 소비자는 배치 항목 expand 지만
    경로가 요청 단위라 단건 요청 상세(RequestDetail)에도 배선만으로 붙는다.

    리포트가 애초에 없는 경우는 전부 404 no_scan_report 하나다: 비 scan 요청
    (dscan 리포트가 존재할 수 없다)·성공 잡 없음·파일 부재·파싱 불가 — 화면엔
    같은 사실("보여줄 리포트 없음")이다. 기존 no_covering_scan(경로 커버 탐색)·
    invalid_batch_operation(배치 검증)은 다른 사실을 말하는 코드라 재사용하지
    않는다. truncated 만 503 scan_report_too_large — 리포트가 "있는데 못 읽는"
    상태는 운영자가 고칠 수 있어야 한다(scan_path_stats 선례)."""
    repos = request.app.state.repos
    row = repos.requests.get(request_id)
    if row is None:
        raise HTTPException(status_code=404, detail="request_not_found")
    if row["operation"] != Operation.SCAN.value:
        raise HTTPException(status_code=404, detail="no_scan_report")
    job = next((j for j in repos.data_jobs.list_jobs(request_id=request_id)
                if j["state"] == DataJobState.SUCCEEDED.value), None)
    if job is None:
        raise HTTPException(status_code=404, detail="no_scan_report")
    # 슬라이스 18: DB 우선 해석(설계 §2.1) -- routes_artifacts._base 와 같은 이유.
    base = strip_scheme(resolve_artifact_base(repos.control,
                                              request.app.state.settings))
    owner_uid = job_owner_uid(job)
    if owner_uid is None:
        raise HTTPException(status_code=404, detail="no_scan_report")
    try:
        f = read_artifact(base, job["job_id"], "execution", "dscan-report.json",
                          owner_uid=owner_uid)
    except ArtifactError:
        raise HTTPException(status_code=404, detail="no_scan_report")
    if f["truncated"]:
        raise HTTPException(status_code=503, detail="scan_report_too_large")
    try:
        report = json.loads(f["content"])
    except (ValueError, RecursionError):     # 깊은 중첩 리포트(routes_usage._read_digest 주석)
        report = None
    if not isinstance(report, dict):
        # 파싱 불가·비 dict(null·[]·"x" 도 유효한 JSON)는 쓸 수 있는 리포트가
        # 없다는 같은 사실이다.
        raise HTTPException(status_code=404, detail="no_scan_report")
    epoch = report.get("generated_at_epoch")
    broken_total = report.get("broken_paths_total")
    broken_limit = report.get("broken_paths_limit")
    hists = _time_histograms(report.get("time_histograms"))
    return {
        "summary": _numbers(report.get("summary")),
        "file_size_histogram": _buckets(report.get("file_size_histogram")),
        "time_histograms": hists,
        # 실 사용량(2026-08-23): null == 모름(구형·오염 리포트) ≠ 0(빈 트리).
        "total_bytes": _total_bytes(hists),
        "generated_at_epoch": epoch if _is_number(epoch) else None,
        # 숫자만 통과(모양 투영: 문자열이 오면 경로일 수 있다). 구형 리포트(키
        # 부재)는 None(미기록) — null≠0: 0 은 "파손 없음"의 정상값이다.
        "broken_paths_total": broken_total if _is_number(broken_total) else None,
        "broken_paths_limit": broken_limit if _is_number(broken_limit) else None,
    }


@router.post("/api/user/requests/{request_id}:cancel")
def cancel_request(request_id: str, request: Request,
                   identity: Identity = Depends(require_user)):
    repos = request.app.state.repos
    req = _owned_request(request, request_id, identity)
    if RequestState(req["state"]) in TERMINAL_REQUEST_STATES:
        raise HTTPException(status_code=409, detail="already_terminal")
    # planner 경쟁: 요청을 종결하기 전에 잡을 먼저 조회·종료해야 고아가 남지 않는다.
    adapter = request.app.state.execution_adapter
    jobs = repos.data_jobs.list_jobs(request_id=request_id)
    if not jobs:
        # 잡이 아직 없는 요청(2026-10-12): 「아직 Pending 일 때만」 한 문장 CAS 로 취소한다 -- planner 의 잡 생성
        # (create_plan_and_job: Pending→Planned CAS + plan·job INSERT 한 트랜잭션)과 직렬화된다. 예전엔 빈 잡 목록을 읽은
        # 직후 planner 가 잡을 커밋하면 요청만 Cancelled 로 덮이고 잡은 Pending 으로 남아 실제로 실행됐다(stepper 는 요청
        # 상태를 보지 않는다). CAS 가 지면 planner 가 이겼다 -- 다시 읽어 잡 경로로 취소한다.
        if repos.requests.cancel_if_pending(request_id, reason_code="cancelled_by_user", actor=identity.actor):
            return {"state": "Cancelled"}
        fresh = repos.requests.get(request_id)
        if fresh is None:
            raise HTTPException(status_code=404, detail="request_not_found")
        if RequestState(fresh["state"]) in TERMINAL_REQUEST_STATES:
            raise HTTPException(status_code=409, detail="already_terminal")
        jobs = repos.data_jobs.list_jobs(request_id=request_id)
    # 잡은 이미 전부 종단인데 요청만 아직 비종단인 창이 있다 — stepper의 finalize가
    # 누락된 고아(data_jobs.terminal_jobs_with_live_request()가 복구하려는 바로 그 상태,
    # 컨트롤러 크래시 후엔 재시작까지 지속된다). 여기서 취소를 기록하면 실제 결과
    # (rm이면 이미 끝난 삭제!)를 Cancelled로 덮어쓰는 거짓 취소가 되고, 결과 행도 없이
    # 요청이 종단이 되어 고아 리컨실러의 시야에서도 사라진다. 취소 대신 실제 결과로
    # 화해시키고 409를 돌려준다.
    if jobs and all(DataJobState(j["state"]) in TERMINAL_DATA_JOB_STATES for j in jobs):
        for job in jobs:
            repos.requests.finalize_from_job(
                request_id, DataJobState(job["state"]),
                reason_code="orphan_recovery", actor=identity.actor)  # 멱등 — 첫 잡이 이긴다
        raise HTTPException(status_code=409, detail="already_terminal")
    try:
        for job in jobs:
            terminate_job(adapter, job)
    except ExecutionError:
        raise HTTPException(status_code=500, detail="cancel_failed")
    for job in jobs:
        repos.data_jobs.set_job_state(job["job_id"], DataJobState.CANCELLED,
                                      reason_code="cancelled_by_user", actor=identity.actor)
    # 커밋 뒤 회수(cancel.sweep_after_cancel): 취소 도중 제출된 단계를 끝낸다. 잡이 전부 stepper 손에 먼저 끝났으면(아무도
    # Cancelled 가 아니다) 거짓 취소 대신 실제 결과로 맞추고 409 -- 위 「이미 전부 종단」 분기와 같은 처리.
    states = [sweep_after_cancel(repos, adapter, job) for job in jobs]
    if jobs and not any(s == DataJobState.CANCELLED.value for s in states):
        for s in states:
            if s is not None:
                repos.requests.finalize_from_job(request_id, DataJobState(s), reason_code="orphan_recovery",
                                                 actor=identity.actor)   # 멱등 — 첫 잡이 이긴다
        raise HTTPException(status_code=409, detail="already_terminal")
    # set_state가 아니라 finalize_from_job — 다른 모든 종단 전이와 동일하게 results 행을
    # 남긴다(set_state는 상태만 바꾸고 결과를 기록하지 않는다).
    repos.requests.finalize_from_job(request_id, DataJobState.CANCELLED,
                                     reason_code="cancelled_by_user", actor=identity.actor)
    return {"state": "Cancelled"}
