import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from ..build_runner import BUILD_REF_PREFIX
from ..domain import DomainValidationError
from ..repositories.builds import BUILD_IMAGES, build_pod_name, build_tag
from .artifacts import tail_lines
from .auth import Identity, require_admin
from .routes_requests import reject_when_maintenance

router = APIRouter(dependencies=[Depends(require_admin)])

# git ref: 공백·제어문자·'..'·선행 '-' 를 막는다. 이 값은 파드 env 로 흘러가
# `git clone --branch "$DMS_BUILD_REF"` 에 쓰인다 -- 셸 인젝션/옵션 인젝션 표면이다.
_REF_RE = re.compile(r"[A-Za-z0-9._/-]{1,200}")


class BuildBody(BaseModel):
    git_ref: str
    images: list[str]
    repo_url: str | None = None


def _detail(row):
    out = dict(row)
    # I2: get_build_log()가 폴백용으로 repos.builds.get()의 SELECT * 결과를 그대로
    # 쓰므로 저장소 레벨에서는 log_text를 빼지 않는다 -- 여기 API 응답 레벨에서만
    # 뺀다. 상세 화면은 로그를 /log로 따로 받으므로 여기 실으면 최대 64KB가 헛돈다.
    # seq도 내부 정렬용 컬럼이라 밖으로 새면 안 된다.
    out.pop("log_text", None)
    out.pop("seq", None)
    out["tag"] = build_tag(row["build_id"])
    return out


@router.post("/api/admin/builds", status_code=202)
def submit_build(body: BuildBody, request: Request,
                 identity: Identity = Depends(require_admin)):
    reject_when_maintenance(request)
    repos = request.app.state.repos
    node = (repos.control.control_state() or {}).get("build_node_name")
    if not node:
        raise HTTPException(status_code=422, detail="build_node_not_set")
    # build_build_pod가 images를 BUILD_IMAGES와 교집합으로 필터링하므로, 하나도
    # 안 겹치면 빌드 파드가 아무것도 하지 않고 조용히 성공한다 -- 여기서 반드시 막는다.
    if not body.images or any(i not in BUILD_IMAGES for i in body.images):
        raise HTTPException(status_code=422, detail="unknown_image")
    ref = (body.git_ref or "").strip()
    if not _REF_RE.fullmatch(ref) or ".." in ref or ref.startswith("-"):
        raise HTTPException(status_code=422, detail="invalid_git_ref")
    # 빠른 거절(fail-fast)일 뿐이다 -- 진짜 "동시에 하나만" 가드는
    # repos.builds.create()의 트랜잭션 안에 있다(builds.py 주석 참고). 이 체크가
    # 없어도 정합성은 깨지지 않지만, 있으면 트랜잭션을 시작하기도 전에 흔한 경우를
    # 싸게 걸러낸다.
    if repos.builds.active() is not None:
        raise HTTPException(status_code=409, detail="build_in_progress")
    try:
        build_id = repos.builds.create(
            repo_url=body.repo_url or request.app.state.settings.build_repo_url,
            git_ref=ref, images=list(body.images), node_name=node,
            actor=identity.actor)
    except DomainValidationError as e:
        # 위 사전 체크와 이 사이의 경합 창에서 다른 요청이 먼저 활성 빌드를
        # 만든 경우 -- 트랜잭션 안 가드가 여기서 잡는다.
        raise HTTPException(status_code=409, detail=e.reason_code)
    return {"build_id": build_id, "state": "Pending"}


@router.get("/api/admin/builds")
def list_builds(request: Request, limit: int = Query(default=50, ge=1, le=200)):
    return [_detail(r) for r in request.app.state.repos.builds.list(limit=limit)]


@router.get("/api/admin/builds/{build_id}")
def get_build(build_id: str, request: Request):
    row = request.app.state.repos.builds.get(build_id)
    if row is None:
        raise HTTPException(status_code=404, detail="build_not_found")
    return _detail(row)


@router.get("/api/admin/builds/{build_id}/log")
def get_build_log(build_id: str, request: Request,
                  tail: int | None = Query(default=None, ge=1)):
    repos = request.app.state.repos
    row = repos.builds.get(build_id)
    if row is None:
        raise HTTPException(status_code=404, detail="build_not_found")
    runner = getattr(request.app.state, "build_runner", None)
    log = None
    if row["state"] in ("Pending", "Running") and runner is not None:
        # 진행 중이면 파드에서 실시간으로 읽는다. 종단이면 박제된 사본이 진실이다
        # -- 파드는 GC 되어 사라질 수 있다.
        log = runner.read_log(f"{BUILD_REF_PREFIX}/{build_pod_name(build_id)}")
    if log is None:
        log = row.get("log_text")
    if log is not None and tail is not None:
        log = tail_lines(log, tail)
    return {"build_id": build_id, "log": log}
