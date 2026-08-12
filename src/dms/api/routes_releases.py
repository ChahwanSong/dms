import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from ..domain import DomainValidationError
from ..execution import ExecutionError
from ..registry import fetch_repo_tags
from ..repositories.releases import COMPONENTS, ROLLOUT_ORDER
from .auth import Identity, audit_actor, require_admin
from .routes_requests import reject_when_maintenance

router = APIRouter(dependencies=[Depends(require_admin)])

# docker 태그 문자셋. 이 값은 이미지 참조 문자열에 그대로 들어가 컨트롤러가 워크로드
# spec에 박는다 -- ':'나 '/'가 섞이면 참조 자체가 뒤바뀐다. 레지스트리가 침묵할 때도
# 이 형식 검증만은 항상 강제한다(fail-open은 "존재하는가"에만 적용된다).
_TAG_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,127}")


class ReleaseItem(BaseModel):
    component: str
    tag: str


class ReleaseBody(BaseModel):
    items: list[ReleaseItem]


def _public(row) -> dict:
    # seq는 내부 정렬용 컬럼(누가 head인가)이라 밖으로 새면 안 된다 --
    # routes_builds._detail과 같은 관례다. 화면은 이미 서버가 강제한 순서대로 온
    # 목록을 그대로 그리므로 seq를 쓸 일이 없다.
    out = dict(row)
    out.pop("seq", None)
    # progress는 DaemonSet 회수 시계의 내부 상태다(마지막으로 관찰된 노드 수) --
    # 운영자에게 보여줄 값이 아니고 스키마 계약도 아니다.
    out.pop("progress", None)
    return out


def _current_image(runner, spec) -> "str | None":
    # 설계 §5(1f5f4e5): api Role에 읽기 전용 apps get이 있다(세 워크로드 한정) --
    # 컨트롤러와 같은 get_workload(observe)로 "현재 선언된" spec 이미지를 읽는다.
    # 파드에서 유도하는 대안은 롤링 중 옛/새 파드가 섞여 부정확해 설계가 기각했다.
    try:
        obs = runner.observe(kind=spec["kind"], name=spec["workload"])
    except ExecutionError:
        # 읽기 실패로 화면 전체가 죽으면 안 된다 -- 레지스트리와 같은 강등 원칙.
        return None
    if obs is None:
        return None
    return (obs.get("images") or {}).get(spec["container"])


def _tags_for(cache: dict, registry: str, repository: str) -> "list[str] | None":
    # 요청 하나 안에서 리포당 한 번만 레지스트리를 때린다 -- api/controller가 같은
    # dms 리포를 쓰므로 캐시가 없으면 한 화면 갱신에 같은 조회가 두 번 나간다.
    # 요청을 넘어가는 캐시는 두지 않는다(이유는 registry.py 모듈 주석).
    if repository not in cache:
        cache[repository] = fetch_repo_tags(registry, repository)
    return cache[repository]


@router.get("/api/admin/releases")
def list_releases(request: Request, limit: int = Query(default=50, ge=1, le=200)):
    repos = request.app.state.repos
    return {"current": {c: _public(r) for c, r in repos.releases.current().items()},
            "history": [_public(r) for r in repos.releases.list(limit=limit)]}


@router.get("/api/admin/releases/targets")
def release_targets(request: Request):
    settings = request.app.state.settings
    runner = request.app.state.rollout_runner
    registry_ok = True
    tags_cache: dict = {}
    targets = []
    for component in ROLLOUT_ORDER:
        spec = COMPONENTS[component]
        tags = _tags_for(tags_cache, settings.build_registry, spec["repository"])
        if tags is None:
            # 화면은 살리되 "태그 목록이 비어 보이는 것"이 레지스트리 장애 때문임을
            # 운영자가 알 수 있어야 한다 -- 빈 목록만 주면 선택지가 없는 것과 구분이 안 된다.
            registry_ok = False
        targets.append({
            "component": component, "kind": spec["kind"],
            "workload": spec["workload"], "container": spec["container"],
            "repository": spec["repository"],
            "current_image": _current_image(runner, spec),
            "tags": tags or [],
        })
    return {"targets": targets, "registry_ok": registry_ok}


@router.post("/api/admin/releases", status_code=202)
def submit_releases(body: ReleaseBody, request: Request,
                    identity: Identity = Depends(require_admin)):
    reject_when_maintenance(request)
    repos = request.app.state.repos
    settings = request.app.state.settings
    runner = request.app.state.rollout_runner
    if not body.items:
        raise HTTPException(status_code=422, detail="unknown_component")
    seen = set()
    for item in body.items:
        # 중복 component도 여기로 -- 한 배치에 같은 워크로드 패치 2건은 뒤가 앞을
        # 조용히 덮어 순서 의미가 깨진다.
        if item.component not in COMPONENTS or item.component in seen:
            raise HTTPException(status_code=422, detail="unknown_component")
        seen.add(item.component)
    # 빠른 거절(fail-fast) -- 진짜 가드는 create_batch의 트랜잭션 안에 있다.
    if repos.releases.active():
        raise HTTPException(status_code=409, detail="rollout_in_progress")
    tags_cache: dict = {}
    records = []
    unverified: list[str] = []
    for item in body.items:
        spec = COMPONENTS[item.component]
        tag = (item.tag or "").strip()
        if not _TAG_RE.fullmatch(tag):
            raise HTTPException(status_code=422, detail="unknown_tag")
        tags = _tags_for(tags_cache, settings.build_registry, spec["repository"])
        # 레지스트리가 응답할 때만 강제한다 -- 응답 불가면 통과시키고 잘못된 태그는
        # patch 후 ImagePullBackOff로 드러나게 한다(잘못된 차단보다 낫다, 설계 §7).
        # 슬라이스 28: tradeoff 는 유지하되 침묵은 걷어낸다 -- 건너뛴 사실을
        # 추적해 응답 플래그와 이벤트로 운영자에게 알린다(조용한 fail-open 은
        # "검증됐다"와 구분이 안 된다는 것이 BACKLOG §2.3 항목의 실체다).
        if tags is None:
            unverified.append(item.component)
        elif tag not in tags:
            raise HTTPException(status_code=422, detail="unknown_tag")
        image = f"{settings.build_registry}/{spec['repository']}:{tag}"
        # IfNotPresent 함정: 같은 태그 재적용은 아무 일도 안 일어나는데 롤아웃은
        # 성공한 것처럼 보인다(설계 §7). 현재 선언 이미지를 못 읽으면(observe
        # None/실패) 검사를 건너뛴다 -- 여기서도 fail-open이다.
        if _current_image(runner, spec) == image:
            raise HTTPException(status_code=422, detail="same_tag")
        records.append({"component": item.component, "image": image, "tag": tag})
    try:
        # 감사 로그는 create_batch가 트랜잭션 안에서 직접 쓴다(mutation_class="release")
        # -- 여기서 또 쓰면 같은 제출이 감사 로그에 두 번 나타난다.
        rows = repos.releases.create_batch(items=records, actor=audit_actor(identity))
    except DomainValidationError as e:
        # 사전 체크와 이 사이의 경합 창 -- 트랜잭션 안 가드가 잡는다.
        raise HTTPException(status_code=409, detail=e.reason_code)
    if unverified:
        # create_batch 성공 **뒤에만** 기록한다 -- 거절된 제출(422/409)은 커밋된
        # 것이 없으므로 "건너뛰고 통과시켰다"는 사실이 성립하지 않는다. 이벤트는
        # 포탈에 안 뜨는 영속 흔적(관리자 이벤트 화면 없음 -- db_reconnected 와
        # 같은 계층)이고, 즉시 가시 채널은 아래 tag_verified 플래그 + 포탈 배너다.
        repos.observability.record_event(
            component="api", severity="warning",
            event_type="release_tag_unverified",
            message=f"registry silent; unverified={','.join(unverified)}",
            payload={"components": unverified})
    return {"items": [_public(r) for r in rows], "tag_verified": not unverified}
