import posixpath
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from ..domain import DomainValidationError
from ..repositories.storages import storage_open_to_users
from .auth import Identity, audit_actor, require_admin, require_user

router = APIRouter(dependencies=[Depends(require_admin)])


class StorageCreate(BaseModel):
    storage_name: str
    mount_path: str
    managed_root: str
    backend_type: str
    # 사용 범위(2026-09-30): 전체(기본) / 관리자 전용(user_enabled=False) / 완전 비활성
    # (enabled=False). repositories.storages.storage_open_to_users 가 세 상태의 정의다.
    enabled: bool = True
    user_enabled: bool = True


class StorageUpdate(BaseModel):
    mount_path: str
    managed_root: str
    backend_type: str
    enabled: bool
    # None = 현재 값 유지(필드를 모르는 옛 클라이언트의 PUT 이 관리자 전용을 풀지 않게).
    user_enabled: bool | None = None


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
            enabled=body.enabled, user_enabled=body.user_enabled,
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
            user_enabled=body.user_enabled, actor=audit_actor(identity))
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
# managed_root 는 예외로 싣는다(사용자 보고 2026-08-15: "스토리지 이름은 보이는데
# 관리 디렉토리가 표시가 안 돼서 정확한 path 를 알 수가 없다"). 근거: 이 목록을
# 쓰는 제출 화면은 **입력 경로가 managed_root 기준 상대경로**라 뿌리를 모르면 어떤
# 절대경로에 작업이 나가는지 화면 어디에서도 알 수 없다. 처음(2026-08-15)엔 소비
# 화면이 관리자 전용이라 관리자에게만 실었는데, 사용자 셀프서비스 sync(단일 작업
# 요청)가 같은 피커를 쓰게 되면서 사용자도 같은 이유로 뿌리가 필요하다 -- 2026-09-29
# 사용자 결정으로 역할과 무관하게 싣는다. mount_path(노드 마운트 지점)·status_detail
# (운영 내부 정보)은 계속 숨긴다 -- 은닉 범위는 필요한 만큼만 연다(테스트로 고정:
# test_non_admin_sees_managed_root_but_not_mount_path).
#
# 사용 범위(2026-09-30): 관리자 전용(user_enabled=0) 스토리지는 **비관리자 응답에서 뺀다**
# (피커에 안 보임). 표시만의 문제가 아니라 제출 게이트(routes_requests.submit)와 planner 가
# storage_admin_only 로 다시 막는다. 관리자에겐 admin_only 표식을 실어 피커에 구분해 보인다.
user_router = APIRouter()


@user_router.get("/api/user/storages")
def list_user_storages(request: Request, identity: Identity = Depends(require_user)):
    rows = request.app.state.repos.storages.list()
    is_admin = identity.role == "admin"
    out = []
    for r in rows:
        if r["enabled"] != 1:
            continue
        if not is_admin and not storage_open_to_users(r):
            continue
        out.append({"storage_name": r["storage_name"], "backend_type": r["backend_type"],
                    "status": r["status"], "managed_root": r["managed_root"],
                    "admin_only": not storage_open_to_users(r)})
    return out
