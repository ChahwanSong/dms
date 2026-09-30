"""사용자 sync 허용 스토리지 쌍 API(2026-09-30, repositories/sync_pairs.py 모듈 docstring).

관리자: 목록·추가(멱등)·삭제. 사용자: 자기에게 적용되는 쌍 조회 -- 포탈 단일 작업 화면이 소스를
고르면 목적지 후보를, 목적지를 고르면 소스 후보를 이 목록으로 거른다(표시일 뿐, 강제는 제출·계획·
컨펌이 sync_pair_allowed 로 한다).
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..domain import DomainValidationError
from ..repositories.storages import storage_open_to_users
from .auth import Identity, audit_actor, require_admin, require_user

router = APIRouter(dependencies=[Depends(require_admin)])
user_router = APIRouter()


class SyncPairBody(BaseModel):
    source_storage: str
    destination_storage: str


@router.get("/api/admin/sync-pairs")
def list_sync_pairs(request: Request):
    return request.app.state.repos.sync_pairs.list()


@router.post("/api/admin/sync-pairs", status_code=201)
def add_sync_pair(body: SyncPairBody, request: Request,
                  identity: Identity = Depends(require_admin)):
    # 존재하는 스토리지끼리만(확인은 저장소가 삽입과 같은 트랜잭션에서 한다 -- SyncPairsRepository.add).
    try:
        row, _created = request.app.state.repos.sync_pairs.add(
            body.source_storage, body.destination_storage, actor=audit_actor(identity))
    except DomainValidationError as e:
        raise HTTPException(status_code=422, detail=e.reason_code)
    return row


@router.delete("/api/admin/sync-pairs/{source_storage}/{destination_storage}")
def remove_sync_pair(source_storage: str, destination_storage: str, request: Request,
                     identity: Identity = Depends(require_admin)):
    if not request.app.state.repos.sync_pairs.remove(
            source_storage, destination_storage, actor=audit_actor(identity)):
        raise HTTPException(status_code=404, detail="sync_pair_not_found")
    return {"source_storage": source_storage, "destination_storage": destination_storage,
            "deleted": True}


@user_router.get("/api/user/sync-pairs")
def my_sync_pairs(request: Request, identity: Identity = Depends(require_user)):
    """restricted=false 면 제한 없음(관리자 -- 화면은 거르지 않는다). 사용자에겐 **자기가 고를 수
    있는** 스토리지(활성 + 사용자 공개, /api/user/storages 와 같은 규칙)끼리의 쌍만 준다 -- 관리자
    전용·비활성 스토리지가 낀 쌍은 사용자에게 의미가 없고 그 존재를 알릴 이유도 없다."""
    if identity.role == "admin":
        return {"restricted": False, "pairs": []}
    repos = request.app.state.repos
    open_names = {s["storage_name"] for s in repos.storages.list() if storage_open_to_users(s)}
    return {"restricted": True,
            "pairs": [{"source_storage": p["source_storage"],
                       "destination_storage": p["destination_storage"]}
                      for p in repos.sync_pairs.list()
                      if p["source_storage"] in open_names
                      and p["destination_storage"] in open_names]}
