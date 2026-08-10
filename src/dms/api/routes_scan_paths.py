"""사용자 scan 경로 CRUD. 모든 조작은 identity.actor의 행만 다룬다 — 타인 행은
404로 존재 여부를 숨긴다. 등록 경로의 소유권·존재 검증은 하지 않는다(스펙 §8);
경로 형식만 validate_relative_path로 검증하고 정규화된 값을 저장한다."""
import json
import math
import posixpath
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from ..artifact_base import resolve_artifact_base
from ..domain import DomainValidationError, validate_relative_path
from ..repositories.scan_paths import covers
from .artifacts import ArtifactError, read_artifact, strip_scheme
from .auth import Identity, require_user

router = APIRouter()

# 노출은 화이트리스트다 (상위 스펙 §8: "집계 통계뿐"). dscan 리포트의 oldest는
# 구체 파일 경로를, directory는 절대 마운트 경로를 담으므로 절대 포함하지 않는다.
#
# **키 화이트리스트만으로는 부족하다**: 허용한 키의 *값* 안쪽은 전부 dscan이 정한다.
# summary에 largest_file이, 버킷에 example_path가, 히스토그램 이름 자리에 경로가
# 들어오는 순간 그대로 모든 로그인 사용자에게 새어 나간다(리포트 포맷이 바뀌기만 해도
# 조용히 그렇게 된다). 그래서 키가 아니라 **모양으로 투영한다** — 유한한 수치와
# 구간 라벨만 통과시키고 나머지는 버린다.
_CANDIDATE_LIMIT = 200
# 후보 수 상한과 별개로 **아티팩트 읽기 횟수**를 묶는다. 이 라우트는 동기라서
# 스타레트의 AnyIO 스레드풀(~40)을 점유하고, 읽기는 최대 256 KiB의 공유 스토리지
# I/O다. 못 읽는 후보가 쌓였을 때 요청 1건이 200번 읽으면 아무 로그인 사용자나
# API 전체를 묶어세울 수 있다(artifacts.py가 같은 위험을 기록해 둔 그 스레드풀이다).
_MAX_READ_ATTEMPTS = 5
_BUCKET_KEYS = ("bucket", "lower_inclusive", "upper_inclusive",
                "min_age_days", "max_age_days", "count", "bytes")
# 버킷 라벨은 "[0,4096]"·"[0d,1d]" 같은 구간 표기뿐이다 — '/'도 그 밖의 문자도 없다.
_BUCKET_LABEL_RE = re.compile(r"[0-9A-Za-z\[\](),.\-+ ]{1,64}")
# 시간 히스토그램의 키(atime/mtime/ctime)는 식별자다. 경로가 키 자리에 오면 버린다.
_HISTOGRAM_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,31}")


def _is_number(v) -> bool:
    """통계 수치인가. bool은 int의 서브클래스라 명시적으로 뺀다. 비유한값(NaN·
    Infinity)은 json.loads가 받아들이지만 응답 직렬화가 거부해 500이 된다."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _numbers(d) -> dict:
    """숫자 값만 남긴다 — dscan이 나중에 요약에 경로 같은 문자열을 추가해도 새지 않는다."""
    if not isinstance(d, dict):
        return {}
    return {k: v for k, v in d.items() if isinstance(k, str) and _is_number(v)}


def _buckets(rows) -> list:
    """히스토그램 버킷에서 수치 필드와 구간 라벨만 남긴다."""
    out = []
    for b in rows if isinstance(rows, list) else []:
        if not isinstance(b, dict):
            continue
        picked = {k: b[k] for k in _BUCKET_KEYS if _is_number(b.get(k))}
        label = b.get("bucket")
        # 라벨이 구간 표기가 아니면(경로일 수 있다) 라벨만 통째로 버리고 수치는 남긴다.
        if isinstance(label, str) and _BUCKET_LABEL_RE.fullmatch(label):
            picked = {"bucket": label, **picked}
        out.append(picked)
    return out


def _time_histograms(d) -> dict:
    if not isinstance(d, dict):
        return {}
    return {k: _buckets(v) for k, v in d.items()
            if isinstance(k, str) and _HISTOGRAM_NAME_RE.fullmatch(k)}


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
    # 슬라이스 18: DB 우선 해석(설계 §2.1) -- routes_artifacts._base 와 같은 이유.
    base = strip_scheme(resolve_artifact_base(repos.control,
                                              request.app.state.settings))
    attempts = 0
    for job in repos.data_jobs.succeeded_scans(row["storage_name"],
                                               limit=_CANDIDATE_LIMIT):
        target = job["target"] or ""
        if not covers(target, row["path"]):
            continue            # 파일을 열지도 않았다 — 읽기 예산을 쓰지 않는다
        if attempts >= _MAX_READ_ATTEMPTS:
            break               # 예산 소진: 더 뒤지지 않고 "없음"으로 답한다
        attempts += 1
        try:
            f = read_artifact(base, job["job_id"], "execution", "dscan-report.json")
        except ArtifactError:
            continue            # 이 후보는 읽을 수 없다 — 다음 후보로
        if f["truncated"]:
            # 읽기 상한을 넘은 리포트는 꼬리만 와서 JSON이 깨진다. 이걸 "커버하는
            # scan이 없다"로 뭉개면 거짓말이다 — 운영자가 고칠 수 있게 드러낸다.
            raise HTTPException(status_code=503, detail="scan_report_too_large")
        try:
            report = json.loads(f["content"])
        except ValueError:
            continue
        if not isinstance(report, dict):
            continue            # null·[]·"x"도 유효한 JSON이다
        epoch = report.get("generated_at_epoch")
        return {
            # 세 필드는 절대 null이 되지 않는다 — 클라이언트 타입이 non-nullable이라
            # null 하나가 렌더 도중 예외가 되고, ErrorBoundary가 없어 화면 전체가 죽는다.
            "summary": _numbers(report.get("summary")),
            "file_size_histogram": _buckets(report.get("file_size_histogram")),
            "time_histograms": _time_histograms(report.get("time_histograms")),
            "generated_at_epoch": epoch if _is_number(epoch) else None,
            "covered_by": {"target": posixpath.normpath(target),
                           "exact": covers(row["path"], target)},
        }
    raise HTTPException(status_code=404, detail="no_covering_scan")
