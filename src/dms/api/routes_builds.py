import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

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
    if repos.builds.active() is not None:
        raise HTTPException(status_code=409, detail="build_in_progress")
    build_id = repos.builds.create(
        repo_url=body.repo_url or request.app.state.settings.build_repo_url,
        git_ref=ref, images=list(body.images), node_name=node,
        actor=identity.actor)
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
        log = runner.read_log(f"buildpod/{build_pod_name(build_id)}")
    if log is None:
        log = row.get("log_text")
    if log is not None and tail is not None:
        log = tail_lines(log, tail)
    return {"build_id": build_id, "log": log}
