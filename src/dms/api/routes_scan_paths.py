"""사용자 scan 경로 CRUD. 모든 조작은 identity.actor의 행만 다룬다 — 타인 행은
404로 존재 여부를 숨긴다. 등록 경로의 소유권·존재 검증은 하지 않는다(스펙 §8);
경로 형식만 validate_relative_path로 검증하고 정규화된 값을 저장한다."""
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from ..domain import DomainValidationError, validate_relative_path
from ..repositories.scan_paths import covers
from .artifacts import ArtifactError, read_artifact, strip_scheme
from .auth import Identity, require_user

router = APIRouter()

# 노출은 화이트리스트다 (상위 스펙 §8: "집계 통계뿐"). dscan 리포트의 oldest는
# 구체 파일 경로를, directory는 절대 마운트 경로를 담으므로 절대 포함하지 않는다.
_STATS_FIELDS = ("summary", "file_size_histogram", "time_histograms",
                 "generated_at_epoch")
_CANDIDATE_LIMIT = 200


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


@router.get("/api/user/scan-paths/{path_id}/stats")
def scan_path_stats(path_id: int, request: Request,
                    identity: Identity = Depends(require_user)):
    """등록 경로를 커버하는 가장 최근 성공 scan의 집계 통계. 서브트리를 정확히
    커버하는 scan은 존재할 수도, 안 할 수도 있다 — covered_by.exact로 정확 일치인지
    상위 기준(서브트리 통계인 척 안 함)인지 명시한다. 후보는 DB 필드(target)로만
    좁히고, 매치된 1건만 아티팩트를 읽는다."""
    repos = request.app.state.repos
    row = repos.scan_paths.get_owned(path_id, identity.actor)
    if row is None:
        raise HTTPException(status_code=404, detail="scan_path_not_found")
    base = strip_scheme(request.app.state.settings.artifact_base_uri)
    for job in repos.data_jobs.succeeded_scans(row["storage_name"],
                                               limit=_CANDIDATE_LIMIT):
        if not covers(job["target"] or "", row["path"]):
            continue
        try:
            f = read_artifact(base, job["job_id"], "execution", "dscan-report.json")
            report = json.loads(f["content"])
        except (ArtifactError, ValueError):
            continue        # 이 후보는 읽을 수 없다 — 다음 후보로
        out = {k: report.get(k) for k in _STATS_FIELDS}
        out["covered_by"] = {"target": job["target"],
                             "exact": covers(row["path"], job["target"] or "")}
        return out
    raise HTTPException(status_code=404, detail="no_covering_scan")
