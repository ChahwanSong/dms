from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from ..domain import DomainValidationError
from .auth import Identity, require_admin

router = APIRouter(dependencies=[Depends(require_admin)])


class StorageCreate(BaseModel):
    storage_name: str
    mount_path: str
    managed_root: str
    backend_type: str


class StorageUpdate(BaseModel):
    mount_path: str
    managed_root: str
    backend_type: str
    enabled: bool


@router.get("/api/admin/storages")
def list_storages(request: Request):
    return request.app.state.repos.storages.list()


@router.post("/api/admin/storages", status_code=201)
def create_storage(body: StorageCreate, request: Request,
                   identity: Identity = Depends(require_admin)):
    try:
        return request.app.state.repos.storages.create(
            storage_name=body.storage_name, mount_path=body.mount_path,
            managed_root=body.managed_root, backend_type=body.backend_type,
            actor=identity.actor)
    except DomainValidationError as e:
        raise HTTPException(
            status_code=409 if e.reason_code == "storage_exists" else 422,
            detail=e.reason_code)


@router.put("/api/admin/storages/{name}")
def update_storage(name: str, body: StorageUpdate, request: Request,
                   identity: Identity = Depends(require_admin)):
    try:
        return request.app.state.repos.storages.update(
            name, mount_path=body.mount_path, managed_root=body.managed_root,
            backend_type=body.backend_type, enabled=body.enabled,
            actor=identity.actor)
    except DomainValidationError as e:
        raise HTTPException(status_code=422, detail=e.reason_code)
    except KeyError:
        raise HTTPException(status_code=404, detail="storage_not_found")


@router.delete("/api/admin/storages/{name}")
def delete_storage(name: str, request: Request,
                   identity: Identity = Depends(require_admin)):
    try:
        return request.app.state.repos.storages.delete(name, actor=identity.actor)
    except KeyError:
        raise HTTPException(status_code=404, detail="storage_not_found")


@router.get("/api/admin/audit-log")
def audit_log(request: Request, limit: int = 50):
    return request.app.state.repos.control.audit_entries(limit)
