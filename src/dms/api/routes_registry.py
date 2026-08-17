"""레지스트리 이미지 관리(슬라이스 34): 3종 빌드 리포의 태그 열람·삭제.

삭제는 **사용 중 태그를 보호**한다 -- 지금 배포돼 도는 이미지(또는 매니페스트가
가리키는 이미지)의 태그를 지우면 노드가 IfNotPresent 로 이미 캐시를 들고 있어도,
파드 재스케줄·다른 노드 배치에서 pull 이 실패해 배포가 깨진다. 보호 판정은
대시보드 드리프트와 같은 재료(live = rollout observe, manifest = 동봉 매니페스트)를
써서 화면 간 두 번째 진실이 생기지 않게 한다.

블롭 회수(garbage-collect)는 이 라우트의 일이 아니다 -- 여기서는 태그(매니페스트)를
지운다. 시간 기반 자동 GC 는 구현하지 않는다(사용자 결정)."""
from fastapi import APIRouter, Depends, HTTPException, Request

from .. import registry as registry_mod
from ..manifest_tags import manifest_images, manifest_job_image
from ..repositories.builds import BUILD_IMAGES
from ..repositories.releases import COMPONENTS
from .auth import Identity, audit_actor, require_admin

router = APIRouter(dependencies=[Depends(require_admin)])

# 관리 대상 리포 = 빌드 대상 3종. 순서는 의존 순서를 그대로 따른다(화면 표시 순서).
_REPOS = BUILD_IMAGES


def _tag_of(image):
    """이미지 참조에서 태그(마지막 콜론 뒤). 파싱 불가면 None."""
    if not isinstance(image, str) or ":" not in image:
        return None
    return image.rsplit(":", 1)[1] or None


def _in_use_tags(request) -> "dict[str, set]":
    """리포별 '지우면 안 되는' 태그 집합. live(실제 도는 이미지) + manifest(동봉본이
    가리키는 이미지) 양쪽을 모은다 -- 어느 쪽이든 그 태그가 사라지면 재적용/재스케줄
    이 깨진다. 조회 실패는 조용히 건너뛴다(보호가 줄 뿐, 삭제를 막지 않는다 -- 단
    fail-safe 방향으로 '모르면 보호 안 함'이 아니라 아래 라우트가 digest 부재 시
    거절하므로 실질 위험은 없다)."""
    runner = request.app.state.rollout_runner
    in_use = {r: set() for r in _REPOS}
    for component, spec in COMPONENTS.items():
        repo = spec["repository"]
        try:
            obs = runner.observe(kind=spec["kind"], name=spec["workload"])
        except Exception:
            obs = None
        live = (obs.get("images") or {}).get(spec["container"]) if obs else None
        for t in (_tag_of(live), _tag_of(manifest_images().get(component))):
            if t is not None:
                in_use[repo].add(t)
    # 잡 이미지(dms-mpifileutils)는 워크로드가 아니라 config 값이다 -- 매니페스트가
    # 가리키는 태그를 보호한다(api 가 이 이미지로 잡을 띄운다).
    job_tag = _tag_of(manifest_job_image())
    if job_tag is not None and "dms-mpifileutils" in in_use:
        in_use["dms-mpifileutils"].add(job_tag)
    return in_use


@router.get("/api/admin/registry/images")
def list_registry_images(request: Request):
    registry = request.app.state.settings.build_registry
    in_use = _in_use_tags(request)
    out = []
    for repo in _REPOS:
        tags = registry_mod.fetch_repo_tags(registry, repo)
        # None(레지스트리 무응답) 은 [] 와 구분해 화면이 "조회 실패"를 말할 수 있게
        # reachable 플래그로 넘긴다(null≠0 -- 태그 0개와 무응답은 다르다).
        reachable = tags is not None
        rows = [{"tag": t, "in_use": t in in_use.get(repo, set())}
                for t in (tags or [])]
        out.append({"repository": repo, "reachable": reachable, "tags": rows})
    return {"registry": registry, "repositories": out}


@router.delete("/api/admin/registry/images/{repository}/{tag}")
def delete_registry_image(repository: str, tag: str, request: Request,
                          identity: Identity = Depends(require_admin)):
    if repository not in _REPOS:
        raise HTTPException(status_code=422, detail="unknown_registry_repo")
    registry = request.app.state.settings.build_registry
    # 사용 중 태그 보호를 먼저 본다 -- 레지스트리를 건드리기 전에 거절한다.
    if tag in _in_use_tags(request).get(repository, set()):
        raise HTTPException(status_code=409, detail="registry_tag_in_use")
    digest = registry_mod.manifest_digest(registry, repository, tag)
    if digest is None:
        # 태그가 없거나 레지스트리 무응답 -- 어느 쪽이든 지울 대상이 없다.
        raise HTTPException(status_code=404, detail="registry_tag_not_found")
    result = registry_mod.delete_manifest(registry, repository, digest)
    if result == "ok":
        # 감사 -- 어떤 이미지를 누가 지웠는지 남긴다(레지스트리는 이력을 안 남긴다).
        # record_event 는 예외를 올리지 않는 진단 채널이라 삭제 성공을 되돌리지 않는다.
        request.app.state.repos.observability.record_event(
            component="registry", severity="info", event_type="registry_image_deleted",
            message=f"{repository}:{tag}",
            payload={"repository": repository, "tag": tag, "digest": digest,
                     "actor": audit_actor(identity)})
        return {"deleted": f"{repository}:{tag}", "digest": digest}
    if result == "disabled":
        raise HTTPException(status_code=409, detail="registry_delete_disabled")
    if result == "not_found":
        raise HTTPException(status_code=404, detail="registry_tag_not_found")
    raise HTTPException(status_code=502, detail="registry_error")
