import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from ..build_runner import BUILD_REF_PREFIX
from ..domain import DomainValidationError
from ..repositories.builds import (BUILD_IMAGES, build_pod_name, effective_tag)
from .artifacts import tail_lines
from .auth import Identity, audit_actor, require_admin
from .routes_requests import reject_when_maintenance

router = APIRouter(dependencies=[Depends(require_admin)])

# 소스 경로(컨트롤 상태에 저장된 값)의 제출 시점 재검증. 저장 시점에 같은 검사를
# 하지만(routes_control) DB 는 신뢰 경계다 -- 변조된 행이 hostPath 로 흘러가기 전에
# fail-closed 로 다시 막는다. 절대경로·'..' 금지·제어문자 금지. 셸 인젝션 표면은
# 아니다(파드 env 로만 나르고 스크립트가 항상 따옴표로 감싼다) -- 이 검사는
# hostPath 대상의 모양 검증이다.
SOURCE_PATH_RE = re.compile(r"/[^\x00-\x1f]{0,299}")


def validate_source_path(path) -> str | None:
    """정규화된 경로를 돌려주고, 모양이 틀리면 None. routes_control(저장)과
    여기(제출)가 **같은 함수**를 쓴다 -- 두 곳이 다르게 검사하면 "저장은 됐는데
    제출이 거절되는"(또는 그 반대) 창이 생긴다."""
    path = (path or "").strip()
    if not SOURCE_PATH_RE.fullmatch(path) or ".." in path:
        return None
    return path


# 운영자 지정 이미지 태그: 컨테이너 태그 문법의 보수적 부분집합. 파생 태그(b+8hex)
# 도 이 정규식 안이다.
_TAG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


class BuildBody(BaseModel):
    images: list[str]
    tag: str | None = None


def _detail(row):
    out = dict(row)
    # I2: get_build_log()가 폴백용으로 repos.builds.get()의 SELECT * 결과를 그대로
    # 쓰므로 저장소 레벨에서는 log_text를 빼지 않는다 -- 여기 API 응답 레벨에서만
    # 뺀다. 상세 화면은 로그를 /log로 따로 받으므로 여기 실으면 최대 64KB가 헛돈다.
    # seq도 내부 정렬용 컬럼이라 밖으로 새면 안 된다.
    out.pop("log_text", None)
    out.pop("seq", None)
    out["tag"] = effective_tag(row)
    # 화면 이름은 source_path 다 -- 컬럼명(repo_url)은 스키마 수렴 제약의 산물이라
    # (repositories.builds.create 주석) API 경계에서 이름을 바로잡는다. 옛 git
    # 시절 행은 URL 이 그대로 실린다(git_ref 로 판별 가능).
    out["source_path"] = row.get("repo_url")
    return out


@router.post("/api/admin/builds", status_code=202)
def submit_build(body: BuildBody, request: Request,
                 identity: Identity = Depends(require_admin)):
    reject_when_maintenance(request)
    repos = request.app.state.repos
    state = repos.control.control_state() or {}
    node = state.get("build_node_name")
    if not node:
        raise HTTPException(status_code=422, detail="build_node_not_set")
    # 소스 경로도 빌드 노드처럼 컨트롤 상태가 단일 진실이다 -- 미설정이면 파드를
    # 만들기 전에 즉답한다(고칠 화면이 어디인지 사유 문구가 안내한다).
    source_path = validate_source_path(state.get("build_source_path"))
    if state.get("build_source_path") in (None, ""):
        raise HTTPException(status_code=422, detail="build_source_not_set")
    if source_path is None:
        # 저장 시점 검증(routes_control)을 통과한 값이면 여기 올 수 없다 -- 도달은
        # DB 직접 변조뿐이고, hostPath 로 흘러가기 전에 fail-closed 로 막는다.
        raise HTTPException(status_code=422, detail="invalid_source_path")
    # build_build_pod가 images를 BUILD_IMAGES와 교집합으로 필터링하므로, 하나도
    # 안 겹치면 빌드 파드가 아무것도 하지 않고 조용히 성공한다 -- 여기서 반드시 막는다.
    if not body.images or any(i not in BUILD_IMAGES for i in body.images):
        raise HTTPException(status_code=422, detail="unknown_image")
    tag = (body.tag or "").strip() or None
    if tag is not None and not _TAG_RE.fullmatch(tag):
        raise HTTPException(status_code=422, detail="invalid_build_tag")
    settings = request.app.state.settings
    # 슬라이스 21 §2.5 동기 ②: 빌드 노드 리포트가 stale 이면 노드 다운일 공산이
    # 크다 -- 비동기 프로브(최대 180s 창)까지 가지 않고 제출 시점에 즉답한다.
    # fresh 판정은 agents.list_nodes 의 그것(reported_at > now - stale) 재사용 --
    # 판정을 여기서 복제하면 노드 화면과 다른 답을 주는 두 번째 진실이 생긴다.
    # egress·디스크는 동기로 검사하지 않는다: API 파드는 다른 노드라 무의미하다.
    fresh = {n["node_name"]
             for n in repos.agents.list_nodes(
                 stale_seconds=settings.agent_report_stale_seconds)
             if n["fresh"]}
    if node not in fresh:
        raise HTTPException(status_code=422, detail="build_node_report_stale")
    # 빠른 거절(fail-fast)일 뿐이다 -- 진짜 "동시에 하나만" 가드는
    # repos.builds.create()의 트랜잭션 안에 있다(builds.py 주석 참고). 이 체크가
    # 없어도 정합성은 깨지지 않지만, 있으면 트랜잭션을 시작하기도 전에 흔한 경우를
    # 싸게 걸러낸다.
    if repos.builds.active() is not None:
        raise HTTPException(status_code=409, detail="build_in_progress")
    try:
        build_id = repos.builds.create(
            # 위에서 확정한 값 재사용 -- 검증한 값과 저장하는 값이 갈리면
            # "검증은 통과했는데 프로브를 못 만드는" 창이 생긴다.
            source_path=source_path, tag=tag,
            images=list(body.images), node_name=node,
            actor=audit_actor(identity))
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
