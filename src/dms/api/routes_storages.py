import posixpath
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from ..domain import DomainValidationError
from .auth import Identity, audit_actor, require_admin, require_user

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
            actor=audit_actor(identity))
    except DomainValidationError as e:
        raise HTTPException(
            status_code=409 if e.reason_code == "storage_exists" else 422,
            detail=e.reason_code)


@router.put("/api/admin/storages/{name}")
def update_storage(name: str, body: StorageUpdate, request: Request,
                   identity: Identity = Depends(require_admin)):
    repos = request.app.state.repos
    current = repos.storages.get(name)
    if current is None:
        raise HTTPException(status_code=404, detail="storage_not_found")
    # 슬라이스 24 §2.4: 진행 중 잡이 참조하는 스토리지의 경로·백엔드 변경을 막는다
    # -- preview 에서 확인한 경로와 execution 이 도는 경로가 갈라지는 TOCTOU(확인
    # 게이트 우회)의 봉인이다. enabled 토글은 가드 없이 통과: 진행 중 잡의 비상
    # 차단(비활성화) 경로를 막으면 안 된다. 비교는 저장값과 같은 normpath 정규화
    # (후행 슬래시만 다른 PUT 의 409 오탐 방지). delete 가드와 같은 요청 레벨
    # check-then-act 라 원자적이지 않다 -- 잔여 창은 stepper._abs fail-closed 가
    # 최종 방어이고, 이 가드는 창을 좁힐 뿐 없애지 못한다(설계 §2.4 정직한 한계).
    changed = (posixpath.normpath(body.mount_path) != current["mount_path"]
               or posixpath.normpath(body.managed_root) != current["managed_root"]
               or body.backend_type != current["backend_type"])
    if changed and repos.requests.active_referencing_storage(name):
        raise HTTPException(status_code=409, detail="storage_in_use")
    try:
        return repos.storages.update(
            name, mount_path=body.mount_path, managed_root=body.managed_root,
            backend_type=body.backend_type, enabled=body.enabled,
            actor=audit_actor(identity))
    except DomainValidationError as e:
        raise HTTPException(status_code=422, detail=e.reason_code)
    except KeyError:
        raise HTTPException(status_code=404, detail="storage_not_found")


@router.delete("/api/admin/storages/{name}")
def delete_storage(name: str, request: Request,
                   identity: Identity = Depends(require_admin)):
    if request.app.state.repos.requests.active_referencing_storage(name):
        raise HTTPException(status_code=409, detail="storage_in_use")
    try:
        return request.app.state.repos.storages.delete(name, actor=audit_actor(identity))
    except KeyError:
        raise HTTPException(status_code=404, detail="storage_not_found")


@router.get("/api/admin/audit-log")
def audit_log(request: Request, limit: int = 50):
    return request.app.state.repos.control.audit_entries(limit)


# 사용자용 읽기 전용 목록. 제출 폼 드롭다운이 유일한 소비자다 — 마운트 경로
# (mount_path)와 운영 내부 정보(status_detail)는 담지 않는다. 비활성 스토리지는
# 고를 수 없어야 하므로 제외하고, Degraded는 남긴다(어드미션 판단은 planner의 몫).
#
# managed_root 만 예외로 **관리자에게만** 싣는다(사용자 보고 2026-08-15: "스토리지
# 이름은 보이는데 관리 디렉토리가 표시가 안 돼서 정확한 path 를 알 수가 없다").
# 근거: 이 목록을 쓰는 화면 중 배치 생성 위저드·scan/sync 제출은 관리자 전용인데,
# **입력 경로가 managed_root 기준 상대경로**라 뿌리를 모르면 어떤 절대경로에
# 작업이 나가는지 화면 어디에서도 알 수 없다. 별도 admin 목록(/api/admin/storages)
# 을 화면마다 겹쳐 부르는 대신 여기서 신원별로 한 필드를 더 싣는 쪽을 택했다 —
# 피커·조회 화면이 쿼리 하나만 쓰고, 비관리자에게는 종전 은닉이 그대로 남는다
# (테스트로 고정: test_non_admin_never_sees_managed_root).
user_router = APIRouter()


@user_router.get("/api/user/storages")
def list_user_storages(request: Request, identity: Identity = Depends(require_user)):
    rows = request.app.state.repos.storages.list()
    is_admin = identity.role == "admin"
    out = []
    for r in rows:
        if r["enabled"] != 1:
            continue
        item = {"storage_name": r["storage_name"], "backend_type": r["backend_type"],
                "status": r["status"]}
        if is_admin:
            item["managed_root"] = r["managed_root"]
        out.append(item)
    return out
