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
from ..identity import owner_override_allowed
from ..privileged import privileged_names
from ..repositories.request_purges import MAX_BATCH_DELETE_CHILDREN
from ..repositories.requests import MAX_SEARCH_TERMS
from ..repositories.storages import storage_open_to_users
from ..repositories.sync_pairs import sync_pair_allowed
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


def reject_when_maintenance(request: Request) -> None:
    # 유지보수 창에는 신규 유입을 막는다 (진행 중인 잡은 건드리지 않는다 — 그건 drain의 몫).
    # 관리자도 예외가 아니다. 콘솔의 control-state PUT은 제출 경로가 아니라 잠기지 않는다.
    state = request.app.state.repos.control.control_state()
    if state and state["maintenance"]:
        raise HTTPException(status_code=503, detail="maintenance_mode")


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
    # root 실행(2026-09-30). True = root(자격 필요, 없으면 403), False/생략 = 실행 신원의 LDAP
    # uid/gid. "관리자 기본 root" 는 **포탈**이 정해 확정값을 명시로 싣는다(SubmitJob
    # rootEffective) -- 서버의 생략 기본값을 root 로 두면 옛 포탈 탭(생략 = 비 root 라고
    # 표시)·me 판정이 어긋난 화면이 표시와 다른 root 로 돈다(적대적 리뷰). StrictBool --
    # "yes"·1 같은 값이 root 로 승격되지 않게.
    run_as_root: StrictBool | None = None


def _require(value: str | None, reason: str) -> str:
    if not value:
        raise DomainValidationError(reason, "required field missing")
    return value


def _validated_payload(body: RequestBody, priority: str,
                       run_as_root: bool) -> tuple[dict, str]:
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
        src_storage = _require(body.source_storage, "missing_source_storage")
        dst_storage = _require(body.destination_storage, "missing_destination_storage")
        payload, key = build_data_payload(
            "sync", source_storage=src_storage, source=body.source,
            destination_storage=dst_storage, destination=body.destination,
            options=body.options)
    elif op is Operation.RM:
        storage = _require(body.storage, "missing_storage")
        payload, key = build_data_payload(
            "rm", storage=storage, target=body.target, options=body.options)
    else:
        storage = _require(body.storage, "missing_storage")
        payload, key = build_data_payload(
            "scan", storage=storage, target=body.target, options=body.options)
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
    if (identity.role != "admin" and body.operation in known_ops
            and body.operation not in settings.user_allowed_operations):
        raise HTTPException(status_code=403, detail="operation_admin_only")
    # 관리자 전용 스토리지(2026-09-30 사용 범위, storage_open_to_users): 비관리자 피커에서
    # 빠지는 건 표시일 뿐이라 여기서 막는다(planner 가 계획 시점에 다시 본다 -- 대기 중
    # 관리자 전용으로 바뀐 요청). 완전 비활성(enabled=0)은 종전대로 planner storage_disabled.
    if identity.role != "admin":
        for storage_name in (body.storage, body.source_storage, body.destination_storage):
            row = request.app.state.repos.storages.get(storage_name) if storage_name else None
            if row is not None and row["enabled"] == 1 and not storage_open_to_users(row):
                raise HTTPException(status_code=403, detail="storage_admin_only")
    # 사용자 sync 허용 스토리지 쌍(2026-09-30, repositories/sync_pairs.py): 기본 전부 불가 --
    # 관리자가 허용한 (소스 -> 목적지) 쌍만. 포탈의 후보 필터는 표시일 뿐이라 여기서 막는다
    # (planner·컨펌이 다시 본다 -- 대기 중 허용이 빠진 요청). 스토리지 필드가 빠진 요청은
    # 아래 검증이 정확한 422(missing_*)를 내도록 쌍 검사를 건너뛴다.
    if (identity.role != "admin" and body.operation == Operation.SYNC.value
            and body.source_storage and body.destination_storage
            and not sync_pair_allowed(request.app.state.repos, body.source_storage,
                                      body.destination_storage)):
        raise HTTPException(status_code=403, detail="sync_pair_not_allowed")
    # 특권 게이트 (스펙 §5): owner_username이 요청자와 다르면 특권 의도 → 인가 필요.
    # 다른 사용자로 실행하는 것 자체가 특권이다 -- 2026-09-30 부터 그 실행은 기본이
    # **그 사용자의 uid/gid** 다(run_as_root 를 명시해야 root -- 포탈은 이 경우 체크박스를 끈다).
    # 술어는 identity.owner_override_allowed 하나 -- planner 가 계획 시점에 같은 함수로 다시 본다(DB 직접 쓰기
    # 방어, 2026-10-07). 원문 비교라 ' alice' 같은 값은 여기서 403(모양 422 는 그 뒤 _validated_payload).
    owner = body.owner_username
    if not owner_override_allowed(owner_username=owner, requester_id=identity.actor,
                                  requester_is_admin=(identity.role == "admin"),
                                  allow_privileged=settings.allow_privileged_requesters,
                                  privileged_requesters=privileged_names(settings, request.app.state.repos)):
        raise HTTPException(status_code=403, detail="privileged_not_authorized")
    # root 실행 게이트: planner 와 같은 자격(세션 인증 포함)을 제출 시점에 즉시 확인한다
    # -- 통과 못 할 요청을 Pending 으로 받아 뒀다가 계획 때 거부하지 않는다. planner 도
    # 다시 확인한다(DB 가 신뢰 경계라 payload 변조·설정 변경을 막는 2차 방어).
    # 서버는 **명시한 True 만** root 로 받는다 -- 생략·False 는 비 root(fail-closed). "관리자는
    # 기본 root"(사용자 결정 2026-09-30)와 "다른 실행 신원이면 기본 그 사용자 권한"은 포탈이
    # 정해 확정값을 명시로 싣는다(SubmitJob rootEffective). 생략을 root 로 읽으면 옛 포탈 탭·
    # 판정이 어긋난 화면이 "비 root" 라고 표시한 채 root 로 돈다.
    run_as_root = body.run_as_root is True
    if run_as_root and not can_run_as_root(identity, settings, request.app.state.repos):
        raise HTTPException(status_code=403, detail="privileged_not_authorized")

    repos = request.app.state.repos
    priority = resolve_priority(repos, body.operation, body.priority)
    try:
        # sync 경로 겹침: 같은 스토리지는 여기서(build_data_payload, 상대경로), 다른 스토리지는 실제 경로로 planner 가 도구를
        # 정한 뒤 본다(dsync -- 두 스토리지가 한 노드의 같은 이름 공간일 때만 뜻이 있다, 2026-10-12).
        payload, resource_key = _validated_payload(body, priority, run_as_root)
    except (DomainValidationError, ValueError) as e:
        reason = getattr(e, "reason_code", "invalid_operation")
        raise HTTPException(status_code=422, detail=reason)
    rid = repos.requests.create(
        operation=body.operation, requester_id=identity.actor, actor=identity.actor,
        resource_key=resource_key, payload=payload, priority=priority,
        # 특권 승격을 session 요청에만 허용하기 위해 인증 방식을 요청에 실어 둔다
        # (planner 가 plan 시점에 읽는다, 설계 §2.2-2).
        auth_method=identity.auth)
    return {"request_id": rid, "state": "Pending"}


def _blank_to_none(value: str | None) -> str | None:
    """앞뒤 공백을 떼고, 빈 값은 None(필터 없음) -- 화면이 trim 해 보내지만 API 를 직접 부르는 쪽도 같게."""
    if value is None:
        return None
    value = value.strip()
    return value if value != "" else None


def _search_terms(text: str, repos, *, is_admin: bool) -> list[str]:
    """검색어 → 저장소 토큰 목록(공백 분리, 소문자로 접어 중복 제거, 순서 유지, 펼친 뒤 MAX_SEARCH_TERMS 개까지).

    '/' 로 시작하는 토큰은 등록 스토리지마다 그 managed_root 아래인지 보고 「이름:상대경로」 로 **바꾼다**(뿌리와 같으면
    「이름:」 -- 그 스토리지의 작업 전부; 둘 이상의 뿌리 아래면 모두). 뿌리 비교는 대소문자를 가린다(경로라서). 원래 토큰은
    어느 뿌리에도 속하지 않을 때만 남긴다(2026-10-25 검증 지적): 검색 대상 문자열(요청자·「스토리지:상대경로」)엔 '/'로 시작하는
    절대경로가 없어 원래 토큰은 배치 이름에 그 경로가 통째로 든 경우에만 맞는다 -- 펼쳐진 경로까지 원래 토큰을 함께 두면 상한(4)
    의 절반을 거의 쓸모없는 토큰이 먹어 한 번에 붙여 넣는 절대경로가 둘에서 잘렸다. 이제 넷까지 들어간다.

    뿌리는 **managed_root 하나뿐이다 -- 누구에게나**(2026-10-25 리뷰): 요청 payload·resource_key 는 managed_root 기준
    상대경로만 저장한다(stepper._abs·domain.managed_abs_path 가 managed_root 와 잇고, 포탈의 절대경로 표시
    lib/storagePaths 도 그 뿌리로 만든다). 관리자에게 mount_path 기준 펼침도 하던 동안은 managed_root 가 mount_path 아래
    디렉터리인 보통 모양(storages._validate)에서 그 상대경로가 늘 **다른 디렉터리**를 가리켰다 -- '/lus/z' 가 실제로는
    /lus/dms/z 에서 돈 잡을, '/mnt/ceph/managed/proj/x' 가 …/managed/managed/proj/x 의 잡을 잡았고(누가 그 경로를 지웠나를
    좇는 관리자에게 거짓 단서) 그 엉뚱한 토큰이 상한 자리까지 먹었다. 둘이 같은 스토리지에선 같은 토큰이라 더하는 것이 없다.

    펼침에 쓰는 스토리지는 그 사람이 볼 수 있는 것만: 관리자는 등록 스토리지 전부, 비관리자는 /api/user/storages 가
    보여 주는 스토리지(사용자에게 열린 활성 스토리지). 결과 유무로 숨긴 스토리지의 뿌리를 맞혀 보는 오라클이 되지 않게 --
    관리자 전용 스토리지는 그 목록이 일부러 숨긴다(routes_storages)."""
    tokens = text.split()
    storages = repos.storages.list() if any(t.startswith("/") for t in tokens) else []
    out: list[str] = []
    for token in tokens:
        if not token.startswith("/"):
            out.append(token)
            continue
        expanded: list[str] = []
        for row in storages:
            if not is_admin and not storage_open_to_users(row):
                continue
            root = row["managed_root"]
            # DB 는 신뢰 경계 -- 문자열이 아닌·빈 뿌리는 펼치지 않는다(빈 뿌리면 모든 절대경로가 「그 아래」가 된다).
            if not isinstance(root, str) or root == "":
                continue
            if token == root or token.startswith(root.rstrip("/") + "/"):
                expanded.append(f"{row['storage_name']}:{token[len(root):].strip('/')}")
        out += expanded or [token]
    return list(dict.fromkeys(t.lower() for t in out))[:MAX_SEARCH_TERMS]


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
                  requester: str | None = Query(None),
                  # 한 배치의 작업만(2026-10-11) -- 배치 상세의 「전체 작업에서 이 배치의 작업 보기」와 배치 기록만
                  # 지워진 묶음 칸이 여기로 온다. 오래된 배치의 작업은 무한 스크롤 수십 쪽 아래라, 배치 단위 삭제가
                  # 불러온 행에서만 시작되는 화면에선 찾을 길이 없었다(검증 지적). 좁히기만 하므로 누구에게나 열어도
                  # 격리는 그대로다(비운영자는 아래에서 requester 가 자기 자신으로 강제된다). 빈 값은 필터 없음.
                  batch_id: str | None = Query(None),
                  before: int | None = Query(None, ge=1),
                  # 부분 일치 검색어(2026-10-25). 길이 상한은 프레임워크 422(limit 과 같은 이유로 사유 코드 없음).
                  q: str | None = Query(None, max_length=200)):
    """작업 목록(전체 작업 화면). 필터는 전부 AND 로 좁히기만 하고 정렬·커서(before)는 필터와 무관하다.

    검색(2026-10-25 사용자 요청 -- 「배치 이름, 대상 경로 등으로 검색·필터, 일부 단어라도 포함하거나 매치되면 보여주도록」):
      - q: 공백으로 나눈 토큰(펼친 뒤 MAX_SEARCH_TERMS 개까지) 중 **하나라도** 대상(스토리지 이름·상대경로)·배치 이름·
        요청자 id(관리자 전체 목록에서만 -- 비관리자의 목록은 이미 자기 자신이라 그 칸은 거르는 힘 없이 자기 작업 전부를 냈다)에
        부분 일치(대소문자 무시)하거나, 16진 4~32자 토큰이 요청 id·배치 id 의 접두면 그 행을 낸다(RequestsRepository.list
        docstring). '/' 로 시작하는 토큰은 절대경로로도 읽어 managed_root 기준 「스토리지 이름:상대경로」 토큰을 덧붙인다
        (_search_terms) -- 요청은 managed_root 기준 상대경로만 저장해서, 셸에서 복사한 절대경로는 그대로는 어디에도 안 걸린다.
      - requester: 관리자에게만 부분 일치(alice 가 alice2 도 잡는다 -- 관리자가 일부만 쳐도 찾게).
      - 공백뿐인 값은 필터 없음. 둘 다 없으면 저장소 SQL 이 검색 전과 같다.

    격리: 비관리자는 requester_id = 자기 자신(**정확히 일치**)이 늘 AND 로 붙고 requester 파라미터는 무시된다. 검색은
    그 위에서만 좁힌다 -- 남의 경로·배치 이름을 쳐도 결과는 0건이라 남의 작업 유무를 알아낼 길이 없다. 요청자 부분
    일치는 저장소의 별도 인자(requester_like)로만 간다: 격리 값(requester_id)에 LIKE 가 끼면 alice 가 alice2 의 작업을,
    이스케이프 없는 '_' 로 j_kim 이 jxkim 의 작업을 본다.

    비용: 드물거나 없는 검색어는 requests 표 전체를 훑고 그동안 단일 연결 DB 잠금을 쥔다(/readyz 도 기다린다) -- PG16 32만
    행 실측 토큰 1개 0.30초, 상한 4개 0.35초(행마다 건초더미를 한 번만 만든다 -- 그 전엔 토큰마다 ~0.28초씩 늘어 8개 2.1초,
    RequestsRepository.list docstring). 흔한 검색어는 LIMIT 만큼 찾고 멈춰 수 ms. 화면이 입력을 300ms 디바운스하고 검색
    중엔 폴링 간격을 3초에서 10초로 늘린다(useJobs.useInfiniteRequests). 비관리자는 자기 행만 인덱스로 훑어 짧다."""
    repos = request.app.state.repos
    is_admin = identity.role == "admin"
    # 비운영자는 requester 를 자기 자신으로 **강제**한다 -- requester 파라미터로
    # 남의 작업을 넓혀 볼 수 없다(격리). 운영자만 requester 필터를 존중한다(부분 일치).
    req = None if is_admin else identity.actor
    requester_like = _blank_to_none(requester) if is_admin else None
    text = _blank_to_none(q)
    terms = _search_terms(text, repos, is_admin=is_admin) if text is not None else None
    rows = repos.requests.list(
        requester_id=req, operation=operation, state=state, batch_id=batch_id or None,
        before=before, limit=limit, requester_like=requester_like, search_terms=terms)
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
