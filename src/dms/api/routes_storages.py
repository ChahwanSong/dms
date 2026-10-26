import posixpath
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, StrictInt
from ..domain import DomainValidationError
from ..repositories.k8s import storage_default_owner, storage_kind
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
    # 스토리지 종류(2026-10-25 dms-k8s 설계 D11): filesystem(직접 경로) | pv(k8s PV 전용 -- StorageClass 매핑 대상) | both.
    # 생략 = filesystem(종전 등록과 같다). 그 밖의 값은 422 invalid_storage_kind.
    kind: str = "filesystem"
    # PV 목적지 sync 결과의 기본 소유자(2026-10-26 사용자 지시 -- 생략 = 0:0 = root:root). StrictInt: "1500"·true 를 정수로
    # 강제 변환하지 않는다(true → 1 은 의도와 다른 uid 다). 범위 밖은 422 invalid_storage_owner(저장소 검증).
    default_uid: StrictInt = 0
    default_gid: StrictInt = 0


class StorageUpdate(BaseModel):
    mount_path: str
    managed_root: str
    backend_type: str
    enabled: bool
    # None = 현재 값 유지(필드를 모르는 옛 클라이언트의 PUT 이 관리자 전용을 풀지 않게).
    user_enabled: bool | None = None
    # None = 현재 값 유지(같은 이유). 매핑이 걸린 스토리지를 filesystem 으로 바꾸면 422 storage_has_sc_mappings(D10).
    kind: str | None = None
    # None = 현재 값 유지(같은 이유 -- 목록 화면의 사용 범위 PUT 도 보내지 않는다).
    default_uid: StrictInt | None = None
    default_gid: StrictInt | None = None


def _owner_id_view(value) -> "int | None":
    """응답의 default_uid·default_gid 한 칸: NULL(기배포 ALTER 직후) = 0(storage_default_owner 와 같은 정의). 정수 아닌 저장값
    (변조)은 None -- 0 으로 뭉개 보이면 무효 값이 root 로 읽힌다. 범위 밖 정수는 그대로 보인다(관리자가 무엇이 틀렸는지 보게;
    default_owner 가 None 이라 무효임은 함께 드러난다)."""
    if value is None:
        return 0
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _with_kind(row: dict) -> dict:
    """응답 행의 kind 를 정규화한다(NULL·알 수 없는 값 = filesystem -- repositories.k8s.storage_kind 와 같은 규칙).
    기본 소유자도 함께 싣는다(2026-10-26): default_uid·default_gid(NULL = 0) + default_owner "uid:gid"(무효 값이면 None --
    storage_default_owner 한 곳의 정규화)."""
    return {**row, "kind": storage_kind(row), "default_uid": _owner_id_view(row.get("default_uid")),
            "default_gid": _owner_id_view(row.get("default_gid")), "default_owner": storage_default_owner(row)}


@router.get("/api/admin/storages")
def list_storages(request: Request):
    return [_with_kind(r) for r in request.app.state.repos.storages.list()]


@router.post("/api/admin/storages", status_code=201)
def create_storage(body: StorageCreate, request: Request,
                   identity: Identity = Depends(require_admin)):
    try:
        return _with_kind(request.app.state.repos.storages.create(
            storage_name=body.storage_name, mount_path=body.mount_path,
            managed_root=body.managed_root, backend_type=body.backend_type,
            enabled=body.enabled, user_enabled=body.user_enabled, kind=body.kind,
            default_uid=body.default_uid, default_gid=body.default_gid,
            actor=audit_actor(identity)))
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
        return _with_kind(repos.storages.update(
            name, mount_path=body.mount_path, managed_root=body.managed_root,
            backend_type=body.backend_type, enabled=body.enabled,
            user_enabled=body.user_enabled, kind=body.kind, default_uid=body.default_uid,
            default_gid=body.default_gid, actor=audit_actor(identity)))
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
def audit_log(request: Request,
              # 감사 로그 무한 스크롤(2026-09-30 사용자 요청: "수십 개만 보인다 -- 스크롤하면 계속"):
              # 한 쪽 limit 건(1..200 -- 무제한이면 전량 SELECT 가 화면 하나에 끌려 나온다), before 는
              # 이전 쪽 마지막 행의 id(키셋 커서, ControlRepository.audit_entries). 둘 다 생략하면
              # 예전처럼 최신 50건 -- 목록 응답 모양은 그대로다.
              limit: int = Query(50, ge=1, le=200),
              before: int | None = Query(None, ge=1)):
    return request.app.state.repos.control.audit_entries(limit, before=before)


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
        # kind(2026-10-25 dms-k8s 설계 D11): 포탈의 직접 경로 피커는 filesystem·both 만 보인다(pv 전용은 PV 주소로만).
        row = {"storage_name": r["storage_name"], "backend_type": r["backend_type"],
               "status": r["status"], "managed_root": r["managed_root"],
               "admin_only": not storage_open_to_users(r), "kind": storage_kind(r)}
        # 기본 소유(2026-10-26 사용자 결정 (4)): 직접 경로 sync 목적지가 PV 를 담는 스토리지면 결과가 이 값이다 -- 직접 경로를 고르는
        # 화면(면제 관리자의 단건·배치 생성)이 안내에 쓴다. 직접 경로는 관리자만이라 관리자 응답에만 싣는다(필요한 만큼만 연다).
        # None = 저장값이 무효(storage_default_owner -- 그 목적지는 409 invalid_storage_owner).
        if is_admin:
            row["default_owner"] = storage_default_owner(r)
        out.append(row)
    return out
