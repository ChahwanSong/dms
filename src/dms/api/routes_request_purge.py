"""작업(요청) 선택 삭제 API(2026-10-08). 판정·삭제·감사·정리 아웃박스는 repositories/request_purges.py 가 한 트랜잭션으로
하고, 이 모듈은 권한·입력 검증·부분 성공 응답만 한다. API 는 파일시스템·k8s 를 전혀 만지지 않는다 -- 결과 파일과 남은
파드는 컨트롤러 request-purge 루프가 비동기로 정리한다(GET /api/admin/request-purges 가 그 진행 상황)."""
import re
import sys

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..artifact_base import resolve_artifact_base, strip_scheme
from .auth import Identity, audit_actor, require_admin, require_session_admin
from .routes_requests import reject_when_maintenance

router = APIRouter()

# 한 번에 지울 수 있는 요청 수 -- 목록 API 상한(GET /api/user/requests 의 le=200)과 같다. 한 요청 = 한 트랜잭션이라
# 상한은 HTTP 요청 하나가 쥐는 시간의 상한이다.
MAX_DELETE = 200
# 요청 id 형식(uuid4().hex). 형식 밖은 DB 를 보지 않고 request_not_found -- 없음과 같은 의미라 존재 오라클이 없다.
_RID_RE = re.compile(r"[0-9a-f]{32}")


class DeleteRequestsBody(BaseModel):
    request_ids: list[str]


def _skip(request_id, *, reason_code) -> dict:
    # reason_code= 키워드로 받는다 -- AST 추출기(tests/test_reason_codes_coverage.py)가 리터럴 자리를 본다(저장소 쪽
    # 사유도 저장소의 _skip(reason_code="…") 리터럴로 등록된다).
    return {"request_id": request_id, "reason": reason_code}


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
    실패한 항목은 롤백됐으니 다시 시도하면 된다."""
    reject_when_maintenance(request)
    ids = list(dict.fromkeys(body.request_ids))
    if len(ids) == 0:
        raise HTTPException(status_code=422, detail="empty_selection")
    if len(ids) > MAX_DELETE:
        raise HTTPException(status_code=422, detail="delete_selection_too_large")
    repos, settings = request.app.state.repos, request.app.state.settings
    # 삭제 시점 base(정리 루프가 이 base 아래의 <job_id> 만 다룬다). 빈 값 = 모름(None) -- 정리 루프가 파일 단계를
    # 보류한다(빈 문자열을 base 로 쓰지 않는다).
    base = strip_scheme(resolve_artifact_base(repos.control, settings) or "") or None
    actor = audit_actor(identity)
    deleted, skipped = [], []
    for rid in ids:
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
    return {"deleted": deleted, "skipped": skipped, "purge_pending": pending}


@router.get("/api/admin/request-purges")
def request_purge_status(request: Request, identity: Identity = Depends(require_admin)):
    """정리 대기 현황 -- 결과 파일·파드 정리의 유일한 운영 표면(전역 이벤트 뷰어가 없다). 조회라 공유 토큰도 된다.
    stalled = 실패 백오프 중이거나 오래 대기 중인 행(last_error 있음), items 는 오래된 순 최대 50건."""
    return request.app.state.repos.request_purges.status(limit=50)
