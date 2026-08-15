from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from ..domain import (DataJobState, DomainValidationError, Operation,
                      TERMINAL_DATA_JOB_STATES, build_data_payload, validate_batch,
                      validate_owner_username)
from ..execution import ExecutionError
from .auth import Identity, require_admin
from .cancel import terminate_job
from .routes_requests import reject_when_maintenance

router = APIRouter()

# 배치 이름 상한: 목록 열·상세 헤더에 한 줄로 얹히는 식별용 자유 텍스트라 120자면
# 충분하다(위생값 — DB 는 TEXT 라 제약이 없고, 여기가 유일한 심판이다).
_MAX_BATCH_NAME = 120


def normalized_batch_name(name: str | None) -> str | None:
    """배치 이름 정규화(생성·수정 공용): trim 후 빈 문자열은 None(이름 없음)으로
    접는다 — 빈 문자열을 저장하면 "이름 없음"이 두 가지 표현으로 갈라진다.
    상한 초과는 422 — 잘라서 저장하면 조용한 절단이다."""
    if name is None:
        return None
    name = name.strip()
    if name == "":
        return None
    if len(name) > _MAX_BATCH_NAME:
        raise HTTPException(status_code=422, detail="invalid_batch_name")
    return name


class BatchBody(BaseModel):
    operation: str
    max_concurrency: int
    options: dict = {}
    note: str | None = None
    # 배치 이름(선택): 운영자 식별용. 빈값·미지정 = 이름 없음(NULL).
    name: str | None = None
    items: list[dict]
    # 실행 제어(슬라이스 32). None = 미지정(정책 기본) — null≠0.
    priority: str | None = None
    node_count: int | None = None
    # 노드당 프로세스 수 override: node_count 와 같은 계약의 미러.
    procs_per_node: int | None = None
    # 배치 특권 실행: 단건 제출(routes_requests)의 owner_username 이식. None = 비특권.
    owner_username: str | None = None


@router.post("/api/admin/batches", status_code=202)
def create_batch(body: BatchBody, request: Request, identity: Identity = Depends(require_admin)):
    reject_when_maintenance(request)
    # 왜 **모든** 배치 생성에 특권 게이트인가: 배치 메뉴는 관리자 운영 메뉴고,
    # 사용자 결정은 "배치(scan/sync)는 전부 기본 관리자 특권(root) 실행"이다.
    # 이전에는 owner_username 이 있을 때만 게이트를 열고 나머지는 조용히 비특권으로
    # 강등했는데, allowlist 밖 admin·토큰 생성 배치가 LDAP 밖 계정이면 자식이
    # ldap_identity_not_found 로 조용히 죽었다 — 강등 대신 여기서 명시 거부한다.
    # 운영자 계정은 DMS_PRIVILEGED_REQUESTERS 로 관리한다. 게이트는 3중(기능 플래그
    # + allowlist + 세션, admin 역할은 require_admin 이 이미 보장). 세션 조건이
    # 필요한 이유: 배치는 생성 시점 인증 방식을 행에 박제해 자식이 물려받으므로
    # 박제 전에 여기서 끊어야 토큰 생성 배치가 특권을 실어 나르지 못한다(단건은
    # planner 가 요청 auth_method 로 재검증, routes_requests.py:86-94 참고).
    settings = request.app.state.settings
    authorized = (settings.allow_privileged_requesters
                  and identity.actor in settings.privileged_requesters
                  and identity.auth == "session")
    if not authorized:
        raise HTTPException(status_code=403, detail="privileged_not_authorized")
    # owner_username 은 소유자 기록(선택, 기본: 생성자=requester_id)이다 — 특권
    # 여부는 위 게이트가 배치 전체에 판정했고, 이 값은 자식 payload 에 실려 실행
    # 신원 해석과 감사에 쓰인다(orchestrator._materialize).
    owner = body.owner_username
    name = normalized_batch_name(body.name)
    try:
        if owner is not None:
            validate_owner_username(owner)    # 단건 _validated_payload 와 같은 검증
        validate_batch(body.operation, body.max_concurrency, body.items,
                       priority=body.priority, node_count=body.node_count,
                       procs_per_node=body.procs_per_node)
        for item in body.items:                       # 각 행 검증(조기 거부)
            build_data_payload(body.operation, options=body.options, **item)
    except (DomainValidationError, TypeError) as e:
        raise HTTPException(status_code=422, detail=getattr(e, "reason_code", "invalid_batch"))
    status = "Running" if body.operation == Operation.SCAN.value else "Previewing"
    bid = request.app.state.repos.batches.create(
        operation=body.operation, requester_id=identity.actor, actor=identity.actor,
        max_concurrency=body.max_concurrency, options=body.options, note=body.note,
        name=name, items=body.items, status=status,
        priority=body.priority, node_count=body.node_count,
        procs_per_node=body.procs_per_node,
        # auth_method 박제는 특권 여부와 무관한 생성 시점 사실의 기록이다 --
        # 자식 request 가 물려받는다(orchestrator._materialize).
        owner_username=owner, auth_method=identity.auth)
    return {"batch_id": bid, "status": status}


@router.get("/api/admin/batches")
def list_batches(request: Request, identity: Identity = Depends(require_admin)):
    return request.app.state.repos.batches.list()


class BatchPatchBody(BaseModel):
    # 부분 갱신 계약: 키 부재 = 무접촉(exclude_unset 으로 판별), 명시적 null·빈
    # 문자열 = 지우기(NULL 저장으로 단순화). 두 키 밖의 입력은 pydantic 이 버린다.
    name: str | None = None
    note: str | None = None


@router.patch("/api/admin/batches/{batch_id}")
def patch_batch(batch_id: str, body: BatchPatchBody, request: Request,
                identity: Identity = Depends(require_admin)):
    """배치 메타데이터(name/note) 수정. **items·실행 제어(priority/node_count/
    procs_per_node/max_concurrency/owner)는 수정 불가** — 배치는 즉시 실행
    모델이라 생성 직후 orchestrator 가 자식 request 를 materialize 하기 시작하고,
    이미 실린 자식은 배치 행을 다시 읽지 않는다. 실행 제어를 여기서 바꾸면 "일부
    자식은 옛값, 일부는 새값"의 거짓 화면이 된다 — 바꾸려면 취소 후 재생성이
    정직하다. 감사 기록은 기존 배치 mutation 라우트(:confirm/:cancel/:rescan)
    관례를 미러: 별도 audit 행 없음(감사는 저장소가 남기는 도메인 — storages/
    policies 처럼 repo 가 남기는 자원만 audit_entries 에 실린다)."""
    reject_when_maintenance(request)
    repo = request.app.state.repos.batches
    if repo.get(batch_id) is None:
        raise HTTPException(status_code=404, detail="batch_not_found")
    fields = {}
    sent = body.model_dump(exclude_unset=True)
    if "name" in sent:
        fields["name"] = normalized_batch_name(sent["name"])     # 생성과 같은 검증
    if "note" in sent:
        # note 도 name 과 같은 지우기 계약(trim 후 빈값 → NULL) — "이름 없음"이
        # NULL/"" 두 표현으로 갈라지는 것을 막는 동일 규칙.
        note = (sent["note"] or "").strip()
        fields["note"] = note if note != "" else None
    repo.update_meta(batch_id, **fields)
    return repo.get(batch_id)


@router.delete("/api/admin/batches/{batch_id}")
def delete_batch(batch_id: str, request: Request,
                 identity: Identity = Depends(require_admin)):
    """배치 삭제 — **종단(Completed/Cancelled) 배치만**. 활성 배치는 409: 실행면
    정리 없는 삭제는 살아있는 자식(요청·잡)을 고아로 만들고 orchestrator 가
    사라진 배치의 자식을 계속 굴리게 된다 — "취소 먼저"가 동선이다(cancel 이
    실행면을 먼저 종료하고 종단화한다). 자식 요청·잡·results 는 보존(감사
    이력 — repo.delete 주석). 유지보수 거부는 patch(메타 수정)와 같은 배치
    mutation 관례를 미러한다(cancel 만 예외 — 취소는 언제나 가능해야 한다)."""
    reject_when_maintenance(request)
    repo = request.app.state.repos.batches
    b = _get_batch_or_404(request, batch_id)
    if b["status"] not in _TERMINAL_BATCH:
        raise HTTPException(status_code=409, detail="batch_not_deletable")
    repo.delete(batch_id)
    return {"deleted": batch_id}


@router.get("/api/admin/batches/{batch_id}")
def get_batch(batch_id: str, request: Request, identity: Identity = Depends(require_admin)):
    repo = request.app.state.repos.batches
    b = repo.get(batch_id)
    if b is None:
        raise HTTPException(status_code=404, detail="batch_not_found")
    # 화면용 상세 조인(request_state/files_count/completed_at) — 실행 루프가 쓰는
    # list_items 와 분리돼 있다(repo docstring 참고).
    b["items"] = repo.list_items_detail(batch_id)
    return b


# --- 항목 편집(수정·삭제·추가) ---------------------------------------------
# 허용 조건(fail-closed): 종단 배치(Completed/Cancelled)는 전 항목 — 전 item 종단
# ⇒ 살아있는 자식 없음(rescan 과 같은 정합 논리)이라 "편집 후 재실행" 흐름이
# 안전하다. 활성 배치는 **Queued 항목만** — Materialized/종단 항목을 바꾸면 이미
# 실행됐(거나 실행 중인) 기록의 위조다. Queued 판정은 읽고-쓰기가 아니라 repo 의
# SQL 원자 가드(WHERE status='Queued')다 — orchestrator materialize(5s 틱)와의
# 경합에서 영향 행 0 이면 409. 특권 게이트는 편집엔 불요: 특권 여부는 생성 시점에
# 배치 행(owner_username/auth_method/requester_id)에 박제됐고 항목 편집은 그
# 재료를 건드리지 않는다(orchestrator._materialize 가 배치 행만 읽는다 — 실측).
# 편집은 require_admin 뒤의 운영 행위라 생성 게이트를 재통과시킬 새 재료가 없다.

_TERMINAL_BATCH = ("Completed", "Cancelled")
# repositories.batches._ACTIVE 의 거울: orchestrator 가 실제로 굴리는 상태
# (list_active). PreviewReady 는 여기 없다 — 확인 대기는 루프 밖이다.
_ACTIVE_BATCH = ("Previewing", "Running")


def _get_batch_or_404(request, batch_id):
    b = request.app.state.repos.batches.get(batch_id)
    if b is None:
        raise HTTPException(status_code=404, detail="batch_not_found")
    return b


def _validated_item(batch, items, new_item, *, replace_seq=None):
    """항목 payload 검증(생성 create 와 같은 두 겹): ① build_data_payload 로 연산별
    스키마·경로 검증, ② 배치 전체의 단일 스토리지 동질성. 동질성은 편집 후의
    **전체 목록**을 validate_batch 에 그대로 넣어 판정한다 — 규칙을 여기서 다시
    쓰면 validate_batch(스토리지 종류 수만 본다)와 어긋나는 두 벌이 된다."""
    prospective = [it["payload"] for it in items if it["seq"] != replace_seq]
    prospective.append(new_item)
    try:
        build_data_payload(batch["operation"], options=batch["options"], **new_item)
        validate_batch(batch["operation"], batch["max_concurrency"], prospective)
    except (DomainValidationError, TypeError) as e:
        raise HTTPException(status_code=422, detail=getattr(e, "reason_code", "invalid_batch"))


@router.put("/api/admin/batches/{batch_id}/items/{seq}")
def update_batch_item(batch_id: str, seq: int, body: dict, request: Request,
                      identity: Identity = Depends(require_admin)):
    reject_when_maintenance(request)
    repo = request.app.state.repos.batches
    b = _get_batch_or_404(request, batch_id)
    items = repo.list_items(batch_id)
    if not any(it["seq"] == seq for it in items):
        raise HTTPException(status_code=404, detail="batch_item_not_found")
    _validated_item(b, items, body, replace_seq=seq)
    only_queued = b["status"] not in _TERMINAL_BATCH
    if not repo.update_item_payload(batch_id, seq, body, only_queued=only_queued):
        # 존재는 위에서 확인했으니 영향 행 0 = Queued 가드 패배(materialize 경합
        # 포함) — 종단 배치 경로(가드 없음)에선 동시 삭제의 극소 창뿐이다.
        raise HTTPException(status_code=409, detail="batch_item_not_editable")
    return repo.get_item(batch_id, seq)


@router.delete("/api/admin/batches/{batch_id}/items/{seq}")
def delete_batch_item(batch_id: str, seq: int, request: Request,
                      identity: Identity = Depends(require_admin)):
    reject_when_maintenance(request)
    repo = request.app.state.repos.batches
    b = _get_batch_or_404(request, batch_id)
    if repo.get_item(batch_id, seq) is None:
        raise HTTPException(status_code=404, detail="batch_item_not_found")
    only_queued = b["status"] not in _TERMINAL_BATCH
    # 카운터(item/succeeded/failed)는 repo 가 삭제와 같은 트랜잭션에서 절대값
    # 재계산한다(감산 분기 복제 대신 행이 진실 — repo._recount 주석).
    if not repo.delete_item(batch_id, seq, only_queued=only_queued):
        raise HTTPException(status_code=409, detail="batch_item_not_editable")
    return {"deleted": seq}


class ReplaceItemsBody(BaseModel):
    items: list[dict]


@router.put("/api/admin/batches/{batch_id}/items")
def replace_batch_items(batch_id: str, body: ReplaceItemsBody, request: Request,
                        identity: Identity = Depends(require_admin)):
    """항목 전체 교체(CSV 재업로드 동선) — **종단(Completed/Cancelled) 배치만**.
    활성 배치는 409(batch_items_not_replaceable): 전량 DELETE+INSERT 는
    Materialized/실행 중 항목의 기록까지 지우는 위조가 되고, 단건 편집의 Queued
    원자 가드로는 "전체" 교체의 원자성을 지킬 수 없다(일부 행만 가드를 통과하면
    부분 교체) — fail-closed 거부가 정직하다. 교체 후에도 배치는 **종단 유지** —
    교체가 곧 실행은 아니다. 재실행은 기존 :rescan(전체 재실행) 몫: reset_all_items
    는 종단 항목만 만지므로 신규 Queued 전량엔 무접촉으로 상태만 올리고,
    orchestrator 가 Queued 를 집는다. 검증은 생성(create_batch)과 같은 두 겹 —
    validate_batch(empty_batch·단일 스토리지 동질성 재사용) + item 별
    build_data_payload(옵션은 배치 행의 것 — DB 신뢰 경계라 저장값도 재검증 대상).
    라우트 충돌 없음(실측): …/items 와 …/items/{seq} 는 세그먼트 수가 달라
    starlette 매칭에서 겹치지 않는다(test_replace_items_route_does_not_shadow_…)."""
    reject_when_maintenance(request)
    repo = request.app.state.repos.batches
    b = _get_batch_or_404(request, batch_id)
    if b["status"] not in _TERMINAL_BATCH:
        raise HTTPException(status_code=409, detail="batch_items_not_replaceable")
    try:
        validate_batch(b["operation"], b["max_concurrency"], body.items)
        for item in body.items:
            build_data_payload(b["operation"], options=b["options"], **item)
    except (DomainValidationError, TypeError) as e:
        raise HTTPException(status_code=422, detail=getattr(e, "reason_code", "invalid_batch"))
    repo.replace_items(batch_id, body.items)
    return {"replaced": len(body.items)}


@router.post("/api/admin/batches/{batch_id}/items", status_code=202)
def add_batch_item(batch_id: str, body: dict, request: Request,
                   identity: Identity = Depends(require_admin)):
    reject_when_maintenance(request)
    repo = request.app.state.repos.batches
    b = _get_batch_or_404(request, batch_id)
    _validated_item(b, repo.list_items(batch_id), body)
    seq = repo.add_item(batch_id, body)
    status = b["status"]
    if status in _TERMINAL_BATCH:
        # 종단 배치에 추가 = 재활성화(rescan 의 상태 분기 재사용). 기존 종단
        # 항목은 무접촉이라 orchestrator 는 신규 Queued 항목만 materialize 한다
        # (_drive 는 종단 항목을 건너뛴다 — 재실행이 아니라 증분 실행).
        # 활성 배치 추가는 상태 그대로 — 이미 도는 루프가 새 항목을 집는다.
        status = "Running" if b["operation"] == Operation.SCAN.value else "Previewing"
        repo.set_status(batch_id, status)
    return {"seq": seq, "status": status}


class RerunItemsBody(BaseModel):
    seqs: list[int]


# 항목 재실행 가능 집합 = batch_orchestrator._ITEM_TERMINAL(종단 item). 비종단
# (Queued/Materialized)을 되돌리면 살아있는 자식과 이중 실행이 된다.
_TERMINAL_ITEM = ("Succeeded", "Failed", "Rejected", "Cancelled")
# skipped 사유(응답 **본문**의 데이터라 HTTPException detail 이 아니다 — 부분
# 성공은 오류가 아니다). 화면이 reasonText 로 옮기므로 reasonCodes.json /
# REASON_MESSAGES 양측 등록 대상이다(AST 추출기는 detail=/reason_code= 키워드만
# 읽어 이 자리를 못 본다 — 등록은 수기).
_SKIP_NOT_FOUND = "batch_item_not_found"
_SKIP_NOT_RERUNNABLE = "batch_item_not_rerunnable"


@router.post("/api/admin/batches/{batch_id}/items:rerun")
def rerun_batch_items(batch_id: str, body: RerunItemsBody, request: Request,
                      identity: Identity = Depends(require_admin)):
    """항목 **선택** 재실행 — 고른 seq 만 Queued 로 되돌린다(:rerun-failed 는 실패
    전부, :rescan 은 전체라 "이 몇 개만"을 표현할 수단이 없었다).

    **부분 성공 모델**(전체 409 가 아니라): 진행 중(Queued/Materialized) 항목이나
    없는 seq 가 섞이면 그 항목만 skipped 로 돌려주고 나머지는 되돌린다. 여러 개를
    고르는 동선에서 하나 때문에 전부 막히면 사용자는 무엇이 문제인지도 모른 채
    다시 고르게 되는데, 되돌릴 수 있는 것과 없는 것의 판정은 서버만 안다(화면은
    낡은 폴링 스냅샷을 본다) — 그래서 "가능한 것은 하고, 못 한 것은 **말한다**".
    없는 seq 를 조용히 넘기지 않는 것도 같은 이유다(조용한 성공 금지).
    종단 판정은 읽고-쓰기가 아니라 repo 의 SQL 원자 가드(WHERE status IN 종단)다 —
    orchestrator(5s 틱)와의 경합에서 진 항목도 skipped 로 떨어진다.

    배치 상태: :rescan 의 분기를 그대로 재사용(scan→Running / sync→Previewing).
    조건이 "종단 배치"가 아니라 "**활성이 아닌 배치**"인 이유는 PreviewReady 다 —
    list_active 밖이라 orchestrator 가 굴리지 않으므로, 그대로 두면 되돌린 항목이
    확인(:confirm) 전까지 Queued 인 채 방치된다. Previewing 으로 되돌리면 새
    항목이 preview 를 다시 타고 전원 previewed 가 되면 orchestrator 가 스스로
    PreviewReady 로 복귀시킨다(자기 치유). 이미 활성(Running/Previewing)이면
    무접촉 — 도는 루프가 Queued 를 집는다(add_item 의 결정과 같다).
    되돌린 것이 0 이면 상태도 안 건드린다: 돌 것이 없는데 Running 이면 거짓이다."""
    reject_when_maintenance(request)
    repo = request.app.state.repos.batches
    b = _get_batch_or_404(request, batch_id)
    # 빈 선택은 422 — empty_batch("배치에 항목이 없습니다")를 재사용하지 않는 이유:
    # 배치엔 항목이 있고 비어 있는 건 **선택**이라, 그 문구는 화면에서 거짓이 된다.
    if len(body.seqs) == 0:
        raise HTTPException(status_code=422, detail="empty_selection")
    status_by_seq = {it["seq"]: it["status"] for it in repo.list_items(batch_id)}
    # 중복 seq 는 접는다(순서 유지) — DB 가 신뢰 경계라 목록이 집합이라고 믿지
    # 않는다. 접지 않으면 두 번째 UPDATE 가 (이미 Queued 라) 가드에 걸려 "재실행
    # 불가"로 보고돼, 사용자가 있지도 않은 문제를 본다.
    seqs = list(dict.fromkeys(body.seqs))
    skipped, candidates = [], []
    for seq in seqs:
        st = status_by_seq.get(seq)
        if st is None:
            skipped.append({"seq": seq, "reason": _SKIP_NOT_FOUND})
        elif st not in _TERMINAL_ITEM:
            skipped.append({"seq": seq, "reason": _SKIP_NOT_RERUNNABLE})
        else:
            candidates.append(seq)
    requeued = repo.reset_items_to_queued(batch_id, candidates)
    # 위 스냅샷에선 종단이었는데 UPDATE 가 못 잡은 항목 = 그 사이 상태가 바뀐
    # 경합 패배(또는 동시 삭제) — 성공으로 셀 수 없으니 같은 사유로 보고한다.
    lost = set(candidates) - set(requeued)
    if lost:
        skipped = sorted(skipped + [{"seq": s, "reason": _SKIP_NOT_RERUNNABLE}
                                    for s in lost], key=lambda d: d["seq"])
    status = b["status"]
    if len(requeued) > 0 and status not in _ACTIVE_BATCH:
        status = "Running" if b["operation"] == Operation.SCAN.value else "Previewing"
        repo.set_status(batch_id, status)
    return {"requeued": len(requeued), "skipped": skipped, "status": status}


@router.post("/api/admin/batches/{batch_id}:confirm")
def confirm_batch(batch_id: str, request: Request, identity: Identity = Depends(require_admin)):
    repo = request.app.state.repos.batches
    b = repo.get(batch_id)
    if b is None:
        raise HTTPException(status_code=404, detail="batch_not_found")
    if b["status"] != "PreviewReady":
        raise HTTPException(status_code=409, detail="batch_not_confirmable")
    repo.set_status(batch_id, "Running")
    return {"status": "Running"}


@router.post("/api/admin/batches/{batch_id}:rerun-failed")
def rerun_failed(batch_id: str, request: Request, identity: Identity = Depends(require_admin)):
    reject_when_maintenance(request)
    repo = request.app.state.repos.batches
    b = repo.get(batch_id)
    if b is None:
        raise HTTPException(status_code=404, detail="batch_not_found")
    n = repo.reset_failed_items(batch_id)
    if n == 0:
        raise HTTPException(status_code=409, detail="no_failed_items")
    repo.set_status(batch_id, "Running")
    return {"status": "Running", "requeued": n}


@router.post("/api/admin/batches/{batch_id}:rescan")
def rescan_batch(batch_id: str, request: Request, identity: Identity = Depends(require_admin)):
    reject_when_maintenance(request)
    repo = request.app.state.repos.batches
    b = repo.get(batch_id)
    if b is None:
        raise HTTPException(status_code=404, detail="batch_not_found")
    # 종단 배치(Completed/Cancelled) 한정: 종단 배치 ⇒ 전 item 종단 ⇒ 살아있는
    # 자식이 없다 — cancel 선례의 "실행면 먼저 종료" 단계가 공집합으로 성립한다
    # (거짓 취소 방지 정합). 비종단에서 허용하면 활성 자식과 리셋 item 이 충돌
    # (resource_conflict·이중 실행)하므로 fail-closed 거부. PreviewReady 는 자식
    # ConfirmPending(활성)이라 제외 — 취소 후 rescan 이 정상 동선이다.
    if b["status"] not in ("Completed", "Cancelled"):
        raise HTTPException(status_code=409, detail="batch_not_rescannable")
    # 성공 item 포함 전체 리셋: 용도가 성장 모니터링(같은 대상 재스캔)이라
    # "실패만"이 아니라 전부를 다시 돌린다(:rerun-failed 와의 차이).
    n = repo.reset_all_items(batch_id)
    status = "Running" if b["operation"] == Operation.SCAN.value else "Previewing"
    repo.set_status(batch_id, status)
    return {"status": status, "requeued": n}


@router.post("/api/admin/batches/{batch_id}:cancel")
def cancel_batch(batch_id: str, request: Request, identity: Identity = Depends(require_admin)):
    repos = request.app.state.repos
    repo = repos.batches
    b = repo.get(batch_id)
    if b is None:
        raise HTTPException(status_code=404, detail="batch_not_found")
    if b["status"] not in ("Previewing", "PreviewReady", "Running"):
        raise HTTPException(status_code=409, detail="batch_not_cancelable")
    adapter = request.app.state.execution_adapter
    items = repo.list_items(batch_id)
    # 1) 먼저 종료한다. 하나라도 실패하면 DB는 건드리지 않고 실패를 보고한다
    #    (거짓 취소 금지 — 상위 스펙 §5).
    child_jobs = []
    for it in items:
        rid = it.get("request_id")
        if not rid:
            continue
        for job in repos.data_jobs.list_jobs(request_id=rid):
            child_jobs.append((it, job))
    try:
        for _, job in child_jobs:
            terminate_job(adapter, job)
    except ExecutionError:
        raise HTTPException(status_code=500, detail="cancel_failed")
    # 2) 종료가 전부 성공한 뒤에만 기록한다.
    jobs_by_seq = {}
    for it, job in child_jobs:
        jobs_by_seq.setdefault(it["seq"], []).append(job)
        if DataJobState(job["state"]) in TERMINAL_DATA_JOB_STATES:
            # 이미 실제 결과가 난 잡은 Cancelled로 덮어쓰지 않는다 — Succeeded인 rm을
            # "취소됨"으로 보고하는 건 거짓 취소다(상위 스펙 §5).
            continue
        repos.data_jobs.set_job_state(job["job_id"], DataJobState.CANCELLED,
                                      reason_code="cancelled_by_batch", actor=identity.actor)
    # 자식 요청은 잡 루프가 아니라 item에서 돈다. materialize는 됐지만 planner가 아직
    # 도달하지 못한 item은 잡 행이 하나도 없어 위 루프에 등장하지 않는다 —
    # 그 요청을 종결하지 않으면 Pending인 채 list_pending()에 남아 planner가 곧바로
    # 집어 실행해 버린다(거짓 취소). orchestrator 5s / planner 10s 주기라 가장 흔한 창이다.
    for it in items:
        rid = it.get("request_id")
        if not rid:
            continue
        jobs = jobs_by_seq.get(it["seq"], [])
        if jobs and all(DataJobState(j["state"]) in TERMINAL_DATA_JOB_STATES for j in jobs):
            # 잡이 이미 종단이면 취소가 아니라 그 실제 결과로 요청을 화해시킨다.
            state, reason = DataJobState(jobs[0]["state"]), "orphan_recovery"
        else:
            state, reason = DataJobState.CANCELLED, "cancelled_by_batch"
        # finalize_from_job은 멱등(종단 요청은 건드리지 않는다) + results 행을 남긴다.
        repos.requests.finalize_from_job(rid, state, reason_code=reason,
                                         actor=identity.actor)
    for it in items:
        if it["status"] in ("Queued", "Materialized"):
            repo.set_item_status(batch_id, it["seq"], "Cancelled")
    repo.set_status(batch_id, "Cancelled")
    return {"status": "Cancelled"}
