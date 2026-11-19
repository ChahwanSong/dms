"""작업(요청) 선택 삭제 API(2026-10-08). 판정·삭제·감사·정리 아웃박스는 repositories/request_purges.py 가 한 트랜잭션으로
하고, 이 모듈은 권한·입력 검증·부분 성공 응답만 한다. API 는 파일시스템·k8s 를 전혀 만지지 않는다 -- 결과 파일과 남은
파드는 컨트롤러 request-purge 루프가 비동기로 정리한다(GET /api/admin/request-purges 가 그 진행 상황).

배치 단위 삭제(2026-10-10): 같은 엔드포인트가 본문 batches 로 배치(또는 배치 기록만 지워진 자식 묶음)를 받는다. 배치 자식은
개별로는 지우지 않는다(request_ids 로 오면 batch_child_not_deletable 그대로). 2026-11-19(상한 50000): 작은 배치(≤ 동기 문턱)는
배치 하나 = 트랜잭션 하나로 즉시 지우고(deleted_batches), 큰 배치는 묘비를 세워 컨트롤러가 청크로 지운다(deleting_batches) --
request_purges.delete_batch_auto. 상한은 서버 값(delete_limits)으로 정리 현황·미리보기에 실려 포탈이 그대로 보인다."""
import re
import sys
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..artifact_base import resolve_artifact_base, strip_scheme
from ..repositories.request_purges import (LIMIT_REASONS, MAX_BATCH_DELETE_ITEMS, RLOCK_YIELD_SECONDS,
                                           SYNC_CALL_FACTOR, list_count_cap, within_call_budget)
from ..repositories import request_purges as _rp
from .auth import Identity, audit_actor, require_admin, require_session_admin
from .routes_requests import reject_when_maintenance

router = APIRouter()

# 한 번에 지울 수 있는 요청 수 -- 목록 API 상한(GET /api/user/requests 의 le=200)과 같다. 한 요청 = 한 트랜잭션이라
# 상한은 HTTP 요청 하나가 쥐는 시간의 상한이다.
MAX_DELETE = 200
# 한 번에 지울 수 있는 배치 수. 배치 하나의 크기 상한(자식 = 설정 DMS_BATCH_DELETE_MAX_CHILDREN·항목 MAX_BATCH_DELETE_ITEMS)과
# 그 근거는 repositories/request_purges.py 에 있다. 배치와 배치 사이에는 RLock 이 풀린다(RLOCK_YIELD_SECONDS 양보). 한 호출이
# 시도하는 배치의 자식 합은 MAX_DELETE_CALL_CHILDREN(100000)으로, 그중 동기 경로 합은 SYNC_CALL_FACTOR × 동기 문턱으로 묶는다.
MAX_DELETE_BATCHES = 10
# 미리보기 응답의 배치 id 한 개의 길이 상한(형식 밖은 gone -- 메아리 폭주만 막는다).
_PREVIEW_ID_MAX = 64
# 요청 id 형식(uuid4().hex). 형식 밖은 DB 를 보지 않고 request_not_found -- 없음과 같은 의미라 존재 오라클이 없다.
_RID_RE = re.compile(r"[0-9a-f]{32}")
# 배치 id 형식(uuid4().hex, BatchesRepository.create). 형식 밖("" 포함)은 DB 를 보지 않고 batch_not_found.
_BID_RE = re.compile(r"[0-9a-f]{32}")


class BatchSelection(BaseModel):
    batch_id: str
    # 확인 창이 본 자식 수(CAS) -- 자식은 늘어나기만 하므로(지우는 길은 배치 단위 삭제뿐) 수가 곧 버전이다. 창을 연 뒤
    # 재실행이 자식을 늘렸으면 batch_changed -- 사용자가 보지 않은 자식은 지우지 않는다. strict: JSON 정수만(true·"1"·1.0
    # 은 422) -- CAS 값이라 호출자 버그(불리언 플래그를 실음 등)를 1 로 접어 통과시키지 않는다(2026-10-10 검증 지적).
    expected_request_count: int = Field(ge=0, strict=True)
    # 확인 창이 본 최대 commit_order(2026-11-19 -- 미리보기가 준 값, 선택). 주면 자식 수와 함께 CAS 로 본다: 회차 보존 정리가 지운
    # 수만큼 새 회차가 생긴 배치(수는 같고 자식은 다르다)를 「본 것」으로 통과시키지 않는다. strict(불리언·문자열 422).
    expected_max_commit_order: int | None = Field(default=None, ge=0, strict=True)


class DeleteRequestsBody(BaseModel):
    request_ids: list[str] = []
    batches: list[BatchSelection] = []


def _skip(request_id, *, reason_code) -> dict:
    # reason_code= 키워드로 받는다 -- AST 추출기(tests/test_reason_codes_coverage.py)가 리터럴 자리를 본다(저장소 쪽
    # 사유도 저장소의 _skip(reason_code="…") 리터럴로 등록된다).
    return {"request_id": request_id, "reason": reason_code}


def _skip_batch(batch_id, *, reason_code, request_id=None, **limit_fields) -> dict:
    # request_id = 문제가 된 자식(없으면 None) -- 화면이 「배치 X: …(작업 abc…)」로 붙인다. 키는 늘 있다. limit_fields(2026-11-19)는
    # 상한 사유일 때만(limit·count·count_at_least -- 포탈 blockText 가 「작업 N개가 … 상한 M개를 넘습니다」로 끼운다).
    return {"batch_id": batch_id, "reason": reason_code, "request_id": request_id, **limit_fields}


def delete_limits(settings) -> dict:
    """삭제 화면이 보이는 상한 묶음(2026-11-19 사용자 요청 「상한 값도 삭제 시 보여지고」) -- GET /api/admin/request-purges·POST
    …:delete-preview 가 같은 모양으로 싣는다. 포탈은 이 값으로만 상한을 말하고 판정한다(화면 상수 없음 -- 서버 설정만 바꿔도
    화면이 같은 숫자를 보인다). list_count_cap = 목록 요약이 세는 최대(넘으면 「N개 이상」 -- 정확한 수는 미리보기)."""
    cap = settings.batch_delete_max_children
    return {"requests_per_call": MAX_DELETE, "batches_per_call": MAX_DELETE_BATCHES, "batch_request_count": cap,
            "batch_item_count": MAX_BATCH_DELETE_ITEMS, "call_request_count": _rp.MAX_DELETE_CALL_CHILDREN,
            "sync_request_count": settings.batch_delete_sync_max_children, "list_count_cap": list_count_cap(cap)}


def _limit_keys(r) -> dict:
    """저장소 거부 결과 → 응답에 실을 상한 숫자(상한 사유일 때만 -- 다른 사유는 지금 모양 그대로)."""
    if r.get("reason") not in LIMIT_REASONS:
        return {}
    return {k: r.get(k) for k in ("limit", "count", "count_at_least")}


def _child_of(repos, request_id):
    """요청의 batch_id(없거나 읽지 못하면 None) -- 응답을 짓는 데만 쓴다(삭제 판정은 저장소가 잠근 행으로 이미 했다).
    읽기 실패로 이미 커밋된 삭제 결과를 500 뒤에 숨기지 않는다(pending_count 와 같은 이유)."""
    try:
        row = repos.requests.get(request_id)
    except Exception as exc:
        print(f"request delete: batch lookup failed for {request_id}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None
    return None if row is None else row.get("batch_id")


@router.post("/api/admin/requests:delete")
def delete_requests(body: DeleteRequestsBody, request: Request,
                    identity: Identity = Depends(require_session_admin)):
    """종단 요청 일괄 삭제 -- **세션 관리자만**(공유 토큰 403 admin_session_required). 삭제는 rm/sync 를 누가
    컨펌·취소했는지 남긴 유일한 기록(state_transitions)과 root 실행 산출물을 없앤다 -- 모든 노드 에이전트가 가진
    공유 토큰으로 그걸 지울 수 있으면 안 된다(빌드 삭제·레지스트리 삭제 선례). root 실행 잡이라고 특권 세션을 더
    요구하지는 않는다 -- 관리자가 신뢰 앵커이고, 감사 스냅숏이 run_as_root·실행 신원을 보존한다.

    **부분 성공 모델**(items:rerun 선례): 지울 수 있는 것은 지우고, 못 지운 것은 항목별 사유로 **말한다**. 판정은
    서버만 정확히 할 수 있다(화면은 몇 초 낡은 스냅숏을 본다). 전부 skipped 여도 200 이다. 중복 id 는 접는다(순서
    유지) -- 접지 않으면 두 번째가 request_not_found 로 보고돼 사용자가 있지도 않은 문제를 본다.

    한 항목의 DB 오류(교착·직렬화 실패·연결 끊김·변조 행)는 그 항목만 request_delete_failed 로 제외하고 계속한다 --
    항목마다 자기 트랜잭션이라 앞 항목은 이미 되돌릴 수 없이 지워졌다. 500 으로 끝내면 그 사실이 「전체 실패」 뒤에
    숨고(포탈은 창 안 오류를 보이는데 행은 목록에서 사라진다) 뒤 항목은 시도조차 안 된다(2026-10-09 검증 지적).
    실패한 항목은 롤백됐으니 다시 시도하면 된다.

    **배치 단위**(본문 batches -- [{batch_id, expected_request_count}], 2026-10-10): 같은 세션 관리자 전용 경계 안에서
    request_purges.delete_batch_auto 가 갈래를 고른다(2026-11-19): 확인 창 수 ≤ 동기 문턱(DMS_BATCH_DELETE_SYNC_MAX_CHILDREN,
    기본 500)이고 이 호출의 동기 합(≤ SYNC_CALL_FACTOR × 문턱)에 들어가면 배치 하나 = 트랜잭션 하나 = 자식 전부 + 항목 + 배치
    행(전부 아니면 전무 -- deleted_batches), 그 밖은 묘비(짧은 트랜잭션) + 컨트롤러 batch-delete 루프의 청크 삭제
    (deleting_batches[{batch_id, request_count, already}] -- 배치는 「삭제 중」, 1–2분 뒤 사라진다; 같은 배치를 다시 누르면
    already: true 로 멱등). 배치 트랜잭션 사이에는 RLOCK_YIELD_SECONDS 쉬어 /readyz 가 RLock 을 얻게 한다.
    배치 id 는 접는다(첫 항목의 expected 를 쓰고 순서 유지). 호출당 배치 ≤ 10, 배치당 자식 ≤ 설정 상한(기본 50000 -- 이 라인은
    1000 에서 2026-11-19 에 50000 으로, dms-k8s 14ee820c 이식)·항목 ≤ 10000. **호출당 자식 합 ≤ 100000**(MAX_DELETE_CALL_CHILDREN):
    선택 순서대로 확인 창 자식 수(expected)를 더해 넘는 배치부터는 시도하지 않고 batch_delete_call_budget(limit·count = 선택 합)로
    싣는다(첫 배치는 늘 시도한다 -- 다시 삭제하면 다음 호출이 이어서 지운다). 상한 사유(batch_delete_too_large·
    batch_delete_too_many_items·batch_delete_call_budget)의 skipped_batches 줄엔 limit·count·count_at_least 가 실린다. 배치 하나의
    DB 오류는 그 배치만 batch_delete_failed 로 제외한다(단건과 같은 이유).
    응답의 deleted_batches·deleting_batches·skipped_batches 는 본문에 batches 키를 실은 호출에만 싣는다(빈 목록이어도) --
    batches 를 모르는 옛 호출자(단건만 보내는 스크립트)의 응답 모양은 그대로다.

    **배치를 먼저** 처리한다: 단건 id 가 같은 호출에서 지운 배치의 자식이면 단건 결과(deleted·skipped)에 싣지 않는다 --
    그 운명은 deleted_batches[].request_ids 가 말한다. 단건을 먼저 하면 그 id 를 skipped(batch_child_not_deletable)로
    보고한 뒤 배치 쪽이 지워, 한 응답이 같은 요청을 「제외」와 「삭제」로 동시에 말했다(2026-10-10 검증 지적 -- 포탈은
    그런 조합을 보내지 않지만 스크립트 호출자는 모순된 항목별 결과를 받는다). 배치가 지워지지 않았으면 그 id 는 단건
    판정 그대로(batch_child_not_deletable)다 -- 단, 그 배치를 같은 호출에서 골랐는데 배치가 거부됐으면(skipped_batches)
    그 자식 id 도 단건 결과에 싣지 않는다: 「배치 단위로 선택해 삭제하세요」는 이미 배치 단위로 고른 호출자에게 배치 줄과
    모순된 두 번째 안내였다(2026-10-11 검증 지적). 고른 배치가 그 자식들의 운명을 말한다(지웠든 거부됐든).

    batch_child_shared(자식을 다른 배치의 항목이 가리킨다)로 거부된 배치는, 같은 호출에서 다른 배치가 하나라도 지워졌으면
    한 번 더 시도한다 -- 그 「다른 배치」를 함께 골랐을 때 선택 순서에 따라 결과가 갈리지 않게(2026-10-11 검증 지적).

    본문 모양 검증(FastAPI 422 -- 예: expected_request_count 음수·불리언)은 이 함수 **전에** 돈다: 유지보수 중이어도
    모양이 틀린 본문은 503 maintenance_mode 가 아니라 422 다. Pydantic 본문을 받는 다른 라우트와 같은 성질이고(검증
    2차 확인), 모양이 맞는 본문은 늘 503 이다 -- 무엇도 지우지 않는다는 점은 같다."""
    reject_when_maintenance(request)
    ids = list(dict.fromkeys(body.request_ids))
    picked: dict[str, BatchSelection] = {}
    for sel in body.batches:
        picked.setdefault(sel.batch_id, sel)
    batches = [(bid, sel.expected_request_count) for bid, sel in picked.items()]
    if len(ids) == 0 and len(batches) == 0:
        raise HTTPException(status_code=422, detail="empty_selection")
    if len(ids) > MAX_DELETE:
        raise HTTPException(status_code=422, detail="delete_selection_too_large")
    if len(batches) > MAX_DELETE_BATCHES:
        raise HTTPException(status_code=422, detail="delete_batch_selection_too_large")
    repos, settings = request.app.state.repos, request.app.state.settings
    # 삭제 시점 base(정리 루프가 이 base 아래의 <job_id> 만 다룬다). 빈 값 = 모름(None) -- 정리 루프가 파일 단계를
    # 보류한다(빈 문자열을 base 로 쓰지 않는다).
    base = strip_scheme(resolve_artifact_base(repos.control, settings) or "") or None
    actor = audit_actor(identity)
    cap, sync_max = settings.batch_delete_max_children, settings.batch_delete_sync_max_children
    sync_used = [0]

    def attempt(bid, expected) -> dict:
        """배치 하나 → {"deleted": {...}} · {"deleting": {...}} · {"skipped": {...}}(응답의 한 줄)."""
        if not _BID_RE.fullmatch(bid):
            return {"skipped": _skip_batch(bid, reason_code="batch_not_found")}
        # 이 호출의 동기 합 예산(2026-11-19) -- 넘는 작은 배치는 거절하지 않고 백그라운드로 보낸다(HTTP 응답 시간을 묶는다).
        eligible = expected <= sync_max and sync_used[0] + expected <= SYNC_CALL_FACTOR * sync_max
        try:
            r = repos.request_purges.delete_batch_auto(
                bid, expected_request_count=expected, actor=actor, artifact_base=base,
                quiet_seconds=settings.request_delete_quiet_seconds, cap=cap, sync_max=sync_max,
                allow_sync=eligible, expected_max_commit_order=picked[bid].expected_max_commit_order,
                max_items=MAX_BATCH_DELETE_ITEMS)
        except Exception as exc:       # 한 배치가 일괄 전체를 500 으로 만들지 않는다(위 docstring)
            print(f"batch delete failed for {bid}: {type(exc).__name__}: {exc}", file=sys.stderr)
            return {"skipped": _skip_batch(bid, reason_code="batch_delete_failed")}
        finally:
            # 배치 트랜잭션 사이 양보 -- 단일 코어에서도 /readyz 가 RLock 을 얻는다(request_purges.RLOCK_YIELD_SECONDS).
            time.sleep(RLOCK_YIELD_SECONDS)
        if r["deleted"]:
            sync_used[0] += expected
            # request_ids·job_ids: 클라이언트가 자식의 요청·잡 키 캐시를 지울 수 있게(단건 job_ids 와 같은 이유).
            return {"deleted": {"batch_id": bid, "request_ids": r["request_ids"], "job_ids": r["job_ids"],
                                "dangling": r["dangling"]}}
        if r.get("deleting"):
            # 백그라운드(새로 세움·이미 진행 중) -- 자식 id 는 싣지 않는다(5만 개 -- 3.4MiB). request_count = 묘비가 센 수.
            n = r.get("request_count")
            return {"deleting": {"batch_id": bid, "request_count": expected if n is None else n,
                                 "already": r["already"] is True}}
        return {"skipped": _skip_batch(bid, reason_code=r["reason"], request_id=r["request_id"], **_limit_keys(r))}

    # batch_child_shared 는 「그 항목을 가진 다른 배치를 먼저 지워라」다 -- 그 배치를 같은 호출에서 함께 골랐으면 선택
    # 순서에 따라 결과가 갈렸다([A, B] 면 A 거부·B 삭제, [B, A] 면 둘 다 삭제 -- 2026-10-11 검증 지적). 그래서 한 바퀴에
    # 무엇이든 지워졌으면 batch_child_shared 로 빠진 배치만 다시 시도한다(지운 배치가 없는 바퀴에서 멈춘다 -- 바퀴마다 하나
    # 이상 지워지므로 배치 수(≤ 10)만큼이 상한이다). 응답 순서는 선택 순서 그대로다. 백그라운드 시작은 「지워짐」이 아니다(다른
    # 배치의 항목 참조는 마무리 때 사라진다) -- 재시도 고리를 돌리지 않는다.
    outcome: dict[str, dict] = {}
    # 호출당 자식 합 예산(위 docstring) -- 담지 못한 배치는 시도하지 않는다. batch_child_shared 재시도 고리도 담은 것 안에서만.
    # 예산은 호출 시점의 모듈 값(request_purges.MAX_DELETE_CALL_CHILDREN -- within_call_budget 의 기본값과 같은 값을 응답에 싣는다).
    budget = _rp.MAX_DELETE_CALL_CHILDREN
    take, over = within_call_budget(batches, budget)
    if over:
        total = sum(n for _, n in batches if isinstance(n, int) and not isinstance(n, bool))
        for bid, _ in over:
            outcome[bid] = {"skipped": _skip_batch(bid, reason_code="batch_delete_call_budget",
                                                   limit=budget, count=total, count_at_least=None)}
    todo = take
    while todo:
        progressed, again = False, []
        for bid, expected in todo:
            outcome[bid] = attempt(bid, expected)
            if "deleted" in outcome[bid]:
                progressed = True
            elif "skipped" in outcome[bid] and outcome[bid]["skipped"]["reason"] == "batch_child_shared":
                again.append((bid, expected))
        todo = again if progressed else []
    deleted_batches = [outcome[bid]["deleted"] for bid, _ in batches if "deleted" in outcome[bid]]
    deleting_batches = [outcome[bid]["deleting"] for bid, _ in batches if "deleting" in outcome[bid]]
    skipped_batches = [outcome[bid]["skipped"] for bid, _ in batches if "skipped" in outcome[bid]]
    # 이 호출에서 배치째 지운 요청 -- 단건 결과에 싣지 않는다(위 docstring 「배치를 먼저」).
    gone = {rid for d in deleted_batches for rid in d["request_ids"]}
    # 이 호출에서 골랐지만 지워지지 않은 배치(거부·백그라운드 시작) -- 그 자식 id 가 단건으로도 왔으면 단건 결과에 싣지 않는다
    # (아래 -- 그 운명은 배치 줄이 말한다).
    refused = ({s["batch_id"] for s in skipped_batches if _BID_RE.fullmatch(s["batch_id"])}
               | {d["batch_id"] for d in deleting_batches})
    deleted, skipped = [], []
    for rid in ids:
        if rid in gone:
            continue
        if not _RID_RE.fullmatch(rid):
            skipped.append(_skip(rid, reason_code="request_not_found"))
            continue
        try:
            r = repos.request_purges.delete_terminal(
                rid, actor=actor, artifact_base=base,
                quiet_seconds=settings.request_delete_quiet_seconds)
        except Exception as exc:       # 한 항목이 일괄 전체를 500 으로 만들지 않는다(위 docstring)
            print(f"request delete failed for {rid}: {type(exc).__name__}: {exc}", file=sys.stderr)
            skipped.append(_skip(rid, reason_code="request_delete_failed"))
            continue
        if r["deleted"]:
            # job_ids: 클라이언트가 잡 키 캐시(아티팩트·로그)를 지울 수 있게.
            deleted.append({"request_id": rid, "job_ids": r["job_ids"]})
        elif r["reason"] == "batch_child_not_deletable" and refused and _child_of(repos, rid) in refused:
            # 같은 호출에서 그 배치를 골랐는데 배치가 거부됐다(또는 백그라운드로 지우는 중이다) -- 단건 사유(「…배치 단위로 선택해
            # 삭제하세요」)는 이미 배치 단위로 고른 호출자에게 모순된 두 번째 안내다(2026-10-11 검증 지적). 그 운명은
            # skipped_batches·deleting_batches 의 그 배치 줄이 말한다.
            continue
        else:
            skipped.append(_skip(rid, reason_code=r["reason"]))
    # 정리 대기 건수는 덤이다 -- 루프가 시작된 뒤엔 무엇이 지워졌는지(이미 커밋)가 응답의 본체라, 세다가 DB 가 끊겨도
    # (연결 끊김 뒤 재연결 실패 -- 위 항목들이 request_delete_failed 로 빠진 바로 그 장애) 500 으로 그것을 숨기지 않는다.
    # None = 모름(0 이 아니다 -- 0 은 「정리할 것 없음」이다). 포탈은 이 값을 읽지 않고 GET /api/admin/request-purges 를 본다.
    try:
        pending = repos.request_purges.pending_count()
    except Exception as exc:
        print(f"request delete: pending_count failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        pending = None
    out = {"deleted": deleted, "skipped": skipped, "purge_pending": pending}
    if "batches" in body.model_fields_set:
        out["deleted_batches"] = deleted_batches
        out["deleting_batches"] = deleting_batches
        out["skipped_batches"] = skipped_batches
    return out


class DeletePreviewBody(BaseModel):
    batch_ids: list[str] = []


@router.post("/api/admin/requests:delete-preview")
def delete_preview(body: DeletePreviewBody, request: Request, identity: Identity = Depends(require_admin)):
    """삭제 확인 창의 미리보기(2026-11-19 -- 읽기 전용, 토큰 조회 가능): 상한(limits)과 배치마다 정확한 수(≤ cap + 1, 넘으면
    상한을 넘으면 하한 count_at_least = cap + 1 -- 다시 세지 않는다, dms-k8s cc52a7e3)·최대 commit_order(삭제 CAS 의 둘째 열쇠)·항목 수·서버 판정(verdict). 작업 목록 3초 폴링은
    min(5000, cap) 에서 세기를 멈추므로(폴링 비용) 5000 이 넘는 배치의 정확한 수와 「삭제 불가」 사유는 여기서만 안다.
    verdict: deleting(이미 백그라운드로 지우는 중) · gone(배치·자식·항목 없음) · blocked(A단계 사유 -- reason·request_id·상한이면
    limit·count·count_at_least) · sync(즉시 -- 수 ≤ 동기 문턱이고 이 선택의 동기 합 예산 안, 선택 순서대로) · async(백그라운드).
    호출 단위 초과(작업 합 > call_request_count)는 배치 verdict 가 아니다 -- 포탈이 limits 로 계산하고 서버가 제출 때 다시 본다.
    비용: 배치마다 문장 ~11개(문장마다 RLock 을 따로 잡는다 -- 트랜잭션 아님, 목록 요약도 배치마다 따로 -- 아래). 실측(로컬 PG16,
    tests/perf_cap50k.py preview -- 요청 56.5만 세계): 5만 자식 배치 10개를 골라도 가장 긴 문장 156–187ms·같은 커넥션 readyz 흉내
    최대 146–178ms, 벽시계 5.1–6.2초(1개면 0.5초). 창을 열 때 한 번만 부른다(폴링하지 않는다). 접은 뒤 10개를 넘으면 422
    delete_batch_selection_too_large(제출과 같다)."""
    repos, settings = request.app.state.repos, request.app.state.settings
    bids = list(dict.fromkeys(b for b in body.batch_ids if isinstance(b, str)))
    if len(bids) > MAX_DELETE_BATCHES:
        raise HTTPException(status_code=422, detail="delete_batch_selection_too_large")
    cap, sync_max = settings.batch_delete_max_children, settings.batch_delete_sync_max_children
    valid = [b for b in bids if _BID_RE.fullmatch(b)]
    deleting = repos.request_purges.batch_deletions_for(valid) if valid else {}
    schedules = repos.batch_schedules.summaries_for(valid) if valid else {}
    # 비종단 자식 수·성공 scan 자식 수(확인 창의 사용량 경고) -- 목록 요약과 같은 함수를 삭제 상한까지 센다(창을 열 때 1회).
    # **배치마다 따로** 부른다(2026-11-19 검증 지적): list_summaries 는 넘긴 배치 전부를 UNION ALL 한 문장으로 세고 성공 scan
    # 갈래는 자식마다 data_jobs 탐침이라, 5만 자식 배치 10개를 한 번에 넘기면 한 문장이 로컬 ~1.7초(테스트베드 환산 ~1.9–2.5초)
    # API 단일 커넥션 RLock 을 쥐어 readiness(1초)를 놓쳤다. 배치 하나씩이면 문장 사이에 RLock 이 풀린다.
    summaries: dict = {}
    for b in valid:
        summaries.update(repos.batches.list_summaries([b], cap=cap))
    sync_used = 0
    out = []
    for bid in bids:
        row = {"batch_id": bid[:_PREVIEW_ID_MAX], "exists": False, "name": None, "status": None, "operation": None,
               "request_count": 0, "request_count_capped": False, "max_commit_order": None, "item_count": None,
               "item_count_capped": False, "verdict": "gone", "reason": None, "request_id": None, "limit": None,
               "count": None, "count_at_least": None, "deleting": None, "schedule": None,
               "live_request_count": None, "succeeded_scan_count": None}
        if not _BID_RE.fullmatch(bid):
            row["reason"] = "batch_not_found"
            out.append(row)
            continue
        chk = repos.request_purges.batch_delete_check(
            bid, cap=cap, quiet_seconds=settings.request_delete_quiet_seconds, max_items=MAX_BATCH_DELETE_ITEMS)
        row.update({k: chk[k] for k in ("exists", "name", "status", "operation", "request_count",
                                        "request_count_capped", "max_commit_order", "item_count",
                                        "item_count_capped", "reason", "request_id")})
        if chk["reason"] in LIMIT_REASONS:
            row.update({k: chk[k] for k in ("limit", "count", "count_at_least")})
        row["schedule"] = schedules.get(bid)
        sm = summaries.get(bid) or {}
        row["live_request_count"] = sm.get("batch_live_request_count")
        row["succeeded_scan_count"] = sm.get("batch_succeeded_scan_count")
        d = deleting.get(bid)
        if d is not None or chk["reason"] == "batch_deleting":
            row["verdict"] = "deleting"
            row["deleting"] = d
        elif chk["reason"] == "batch_not_found":
            row["verdict"] = "gone"
        elif chk["reason"] is not None:
            row["verdict"] = "blocked"
        else:
            n = chk["request_count"]
            if n <= sync_max and sync_used + n <= SYNC_CALL_FACTOR * sync_max:
                row["verdict"] = "sync"
                sync_used += n
            else:
                row["verdict"] = "async"
        out.append(row)
    return {"limits": delete_limits(settings), "batches": out}


@router.get("/api/admin/request-purges")
def request_purge_status(request: Request, identity: Identity = Depends(require_admin)):
    """정리 대기 현황 -- 결과 파일·파드 정리의 유일한 운영 표면(전역 이벤트 뷰어가 없다). 조회라 공유 토큰도 된다.
    stalled = 실패 백오프 중이거나 오래 대기 중인 행(last_error 있음), items 는 오래된 순 최대 50건.
    2026-11-19: limits(delete_limits -- 작업 목록 툴바의 늘 있는 상한 줄·미등록 스토리지 정리 창의 재료)와 batch_deletions(진행
    중인 백그라운드 배치 삭제, 오래된 순 최대 50 -- 툴바 「배치 삭제 중 k건」·끝나면 목록 무효화)를 더한다."""
    out = request.app.state.repos.request_purges.status(limit=50)
    out["limits"] = delete_limits(request.app.state.settings)
    return out
