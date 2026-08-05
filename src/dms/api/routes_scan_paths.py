"""사용자 scan 경로 CRUD. 모든 조작은 identity.actor의 행만 다룬다 — 타인 행은
404로 존재 여부를 숨긴다. 등록 경로의 소유권·존재 검증은 하지 않는다(스펙 §8);
경로 형식만 validate_relative_path로 검증하고 정규화된 값을 저장한다."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from ..domain import DomainValidationError, validate_relative_path
from .auth import Identity, require_user

router = APIRouter()


class ScanPathBody(BaseModel):
    storage_name: str
    path: str


def _active_storage_names(request: Request) -> set[str]:
    return {r["storage_name"] for r in request.app.state.repos.storages.list()
            if r["enabled"] == 1}


@router.get("/api/user/scan-paths")
def list_scan_paths(request: Request, identity: Identity = Depends(require_user)):
    return request.app.state.repos.scan_paths.list_for(identity.actor)


@router.post("/api/user/scan-paths", status_code=201)
def add_scan_path(body: ScanPathBody, request: Request,
                  identity: Identity = Depends(require_user)):
    try:
        path = validate_relative_path(body.path)
    except DomainValidationError as e:
        raise HTTPException(status_code=422, detail=e.reason_code)
    if body.storage_name not in _active_storage_names(request):
        raise HTTPException(status_code=422, detail="storage_missing")
    try:
        pid = request.app.state.repos.scan_paths.add(
            identity.actor, body.storage_name, path)
    except DomainValidationError as e:
        raise HTTPException(status_code=409, detail=e.reason_code)
    return request.app.state.repos.scan_paths.get_owned(pid, identity.actor)


@router.delete("/api/user/scan-paths/{path_id}")
def delete_scan_path(path_id: int, request: Request,
                     identity: Identity = Depends(require_user)):
    if not request.app.state.repos.scan_paths.delete_owned(path_id, identity.actor):
        raise HTTPException(status_code=404, detail="scan_path_not_found")
    return {"deleted": path_id}
