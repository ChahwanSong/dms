import re

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from ..domain import DomainValidationError, PRIORITIES
from ..execution_manifests import MAX_PROCESSES_PER_WORKER
from ..repositories.control import POLICY_TOOLS
from .auth import Identity, audit_actor, require_admin

router = APIRouter(dependencies=[Depends(require_admin)])

# policies 의 정수 칸은 INTEGER(int4)다(migrations) -- 상한이 없으면 9999999999 가 검증을 다 통과한 뒤 PostgreSQL 22003 으로
# 처리되지 않은 500 이 됐다(2026-11-11; 테스트 DB 는 SQLite 라 못 잡는다). 프런트 PolicyDialog.INT4_MAX 가 미러.
INT4_MAX = 2147483647
# Volcano 큐 = 클러스터 범위 k8s 객체 이름(DNS-1123 subdomain). 빈 값은 Volcano 가 default 큐로 채워 dms-data 큐 한도 밖에서
# 돌고, 공백 섞인 이름은 없는 큐라 그 도구의 잡이 전부 생성 실패한다(2026-11-11) -- 저장 시점에 막는다. 프런트 queueError 미러.
_QUEUE_RE = re.compile(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?(\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*")
_QUEUE_MAX = 253


class PolicyBody(BaseModel):
    max_nodes: int = Field(ge=1, le=INT4_MAX)
    # 상한 = 워커 빌더의 위생 상한(execution_manifests.MAX_PROCESSES_PER_WORKER, 2026-10-16 리뷰) -- 저장 시점에 422 로 막는다.
    # 상한이 없으면 저장은 되고, 노드 CPU 상한을 모르는 동안 계획된 잡이 사전 점검까지 돈 뒤 빌더 ValueError →
    # submit_failed 로 불투명하게 끝났다.
    procs_per_node: int = Field(ge=1, le=MAX_PROCESSES_PER_WORKER)
    queue: str = "dms-data"
    default_priority: str = "mid"
    max_priority: str = "high"
    # None(타임아웃 없음)은 허용하되 0/음수는 막는다 — 그대로 activeDeadlineSeconds로
    # 흘러가 API 서버가 파드 생성을 거부하고 잡이 submit_failed로 죽는다.
    preview_timeout_seconds: int | None = Field(default=None, ge=1, le=INT4_MAX)
    execution_timeout_seconds: int = Field(ge=1, le=INT4_MAX)
    enabled: bool = True


@router.get("/api/admin/policies")
def list_policies(request: Request):
    control = request.app.state.repos.control
    out = [control.get_policy(t) for t in sorted(POLICY_TOOLS)]
    return [p for p in out if p is not None]


@router.get("/api/admin/policies/{tool}")
def get_policy(tool: str, request: Request):
    policy = request.app.state.repos.control.get_policy(tool)
    if policy is None:
        raise HTTPException(status_code=404, detail="policy_not_found")
    return policy


@router.put("/api/admin/policies/{tool}")
def put_policy(tool: str, body: PolicyBody, request: Request,
               identity: Identity = Depends(require_admin)):
    if body.default_priority not in PRIORITIES or body.max_priority not in PRIORITIES:
        raise HTTPException(status_code=422, detail="invalid_priority")
    queue = body.queue.strip()
    if len(queue) > _QUEUE_MAX or not _QUEUE_RE.fullmatch(queue):
        raise HTTPException(status_code=422, detail="invalid_policy")
    try:
        request.app.state.repos.control.upsert_policy(
            tool, max_nodes=body.max_nodes, procs_per_node=body.procs_per_node,
            queue=queue, default_priority=body.default_priority,
            max_priority=body.max_priority,
            preview_timeout_seconds=body.preview_timeout_seconds,
            execution_timeout_seconds=body.execution_timeout_seconds,
            enabled=body.enabled, actor=audit_actor(identity))
    except DomainValidationError as e:
        raise HTTPException(status_code=422, detail=e.reason_code)
    return request.app.state.repos.control.get_policy(tool)
