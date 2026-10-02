"""사용량 분석(2026-08-23 사용자 요청): 한 디렉터리의 scan 이력을 시계열로.

- 전 요청자 통합이 계약이다("요청자에 관계없이 모든 작업") — 그래서 admin 전용
  라우터다. scan 리포트는 경로·수치가 든 운영 데이터라 사용자 격리(자기 요청만)
  를 깨는 순간 관리자 화면이어야 한다(request_scan_stats 와 같은 이유).
- 모양 투영은 scan_path_stats 와 **한 벌**을 공유한다(routes_scan_paths 의 「왜」:
  키 화이트리스트만으로는 리포트 값 안의 경로 유출을 못 막는다).
- 리포트 요약 캐시(2026-10-02, repositories/scan_digests.py): 목록이 타깃마다 최신 스캔의 실 사용량·파일 수·hot
  비율을 보이고 내보내기가 전 타깃을 싣게 되면서, 리포트 투영 결과를 잡 단위로 DB 에 둔다. 리포트는 성공 종단
  잡의 불변 산출물이라 한 번 읽으면 끝이다 -- 아티팩트를 여는 것은 캐시에 없는 잡뿐이다.
- 아티팩트 읽기는 동기 스레드풀 점유라(artifacts.py 의 위험 기록) 이력 포인트 수를
  상한(_MAX_POINTS)으로 묶는다. 못 읽는 리포트는 포인트를 지어내는 대신
  skipped 로 센다 — 단일 리포트 라우트(503 scan_report_too_large)와 달리 시계열
  하나가 전체 이력을 죽이면 안 되고, 숨기면 "그날 스캔이 없었다"는 거짓이 된다.
"""
import json

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from ..artifact_base import resolve_artifact_base
from ..db import utc_now_iso
from .artifacts import ArtifactError, job_owner_uid, read_artifact, strip_scheme
from .auth import require_admin
from .routes_scan_paths import (_buckets, _is_number, _numbers, _time_histograms,
                                _total_bytes)

router = APIRouter(dependencies=[Depends(require_admin)])

# 시계열 포인트 상한. 60 = 아티팩트 읽기(최대 256KiB I/O) 예산이자, 화면이 한
# 차트에 정직하게 그릴 수 있는 밀도의 상한(그 이상은 어차피 겹쳐 안 보인다).
_MAX_POINTS = 60
_TARGET_LIMIT = 200
# 내보내기 타깃 상한: 전 타깃이 계약이지만 무한은 아니다 -- 넘으면 잘라 내고 truncated 로 말한다(조용한 절단 금지).
# 5000 = 응답 전체가 메모리에 한 번에 서는 구조의 실측 예산(리뷰 2026-10-02: 1만 행 x 두 포인트 전량이면 응답
# 113MiB·RSS ~1GiB 로 api 컨테이너 한도 1Gi 에 닿았다). 행을 줄인(previous 는 증감용 값만) 뒤 5000 행은 수십 MiB 다.
_EXPORT_LIMIT = 5000
_ORDER = "^(asc|desc)$"


def _project_report(report: dict) -> dict:
    """dscan 리포트(또는 이미 투영된 캐시 값) -> 요약(digest). 투영은 멱등이라 캐시에서 읽은 값에도 다시 건다 --
    DB 가 신뢰 경계라 저장된 모양도 믿지 않는다(경로 문자열이 끼어들 자리가 없게)."""
    epoch = report.get("generated_at_epoch")
    hists = _time_histograms(report.get("time_histograms"))
    broken_total = report.get("broken_paths_total")
    broken_limit = report.get("broken_paths_limit")
    return {
        "generated_at_epoch": epoch if _is_number(epoch) else None,
        # 실 사용량: null == 모름(구형·오염 리포트) ≠ 0(빈 트리) — 화면이 그
        # 포인트를 0 으로 그리면 "그날 다 지워졌다"는 거짓 절벽이 된다.
        "total_bytes": _total_bytes(hists),
        "summary": _numbers(report.get("summary")),
        "time_histograms": hists,
        "file_size_histogram": _buckets(report.get("file_size_histogram")),
        # null≠0: 0 은 "파손 없음"의 정상값, None 은 구형 리포트(키 부재).
        "broken_paths_total": broken_total if _is_number(broken_total) else None,
        "broken_paths_limit": broken_limit if _is_number(broken_limit) else None,
    }


def _read_digest(base: str, job: dict) -> "dict | None":
    """성공 scan 잡 1건의 리포트를 읽어 투영한다. 못 읽으면 None(호출자가 센다).

    truncated(256KiB 초과)도 못 읽는 것으로 접는다: 꼬리만 온 JSON 은 어차피
    파싱이 깨지고, 여기서 503 을 던지면 병든 리포트 하나가 이력 전체를 막는다."""
    owner_uid = job_owner_uid(job)
    if owner_uid is None:
        return None             # 요청자 uid 없는 잡은 열지 않는다(fail-closed)
    try:
        f = read_artifact(base, job["job_id"], "execution", "dscan-report.json",
                          owner_uid=owner_uid)
    except ArtifactError:
        return None
    if f["truncated"]:
        return None
    try:
        report = json.loads(f["content"])
    except (ValueError, RecursionError):
        # RecursionError: 요청자가 자기 아티팩트를 "[[[[…" 같은 깊은 중첩으로 바꿔 두면(artifact_files 위협 모델 --
        # 일반 사용자가 자기 잡 디렉터리에 파일을 만들고 바꿀 수 있다) C 디코더가 ValueError 가 아닌 RecursionError
        # 를 던진다. 못 읽는 리포트로 접는다 -- 아니면 그 한 타깃이 관리자 목록·내보내기 전체를 500 으로 막는다(리뷰).
        return None
    if not isinstance(report, dict):
        return None
    return _project_report(report)


def _digests(repos, base: str, jobs: list) -> dict:
    """job_id -> digest. 캐시에 있으면 그것(재투영), 없으면 리포트를 읽어 투영하고 캐시에 둔다. 못 읽은 잡은 결과에
    없다(캐시에도 두지 않는다 -- 일시 오류를 영구 "모름"으로 굳히지 않게)."""
    cached = {jid: _project_report(d)
              for jid, d in repos.scan_digests.get_many([j["job_id"] for j in jobs]).items()}
    for job in jobs:
        if job["job_id"] in cached:
            continue
        digest = _read_digest(base, job)
        if digest is None:
            continue
        repos.scan_digests.put(job["job_id"], digest)
        cached[job["job_id"]] = digest
    return cached


def _point(job: dict, digest: "dict | None") -> dict:
    """잡 1건 + 리포트 요약 -> 포인트(이력·목록·내보내기 공용 모양). digest 가 없으면(리포트를 못 읽음)
    report_readable=False 이고 리포트 유래 값은 모름(null/빈 값)이다 -- 실 사용량만은 러너가 같은 규칙으로 DB 에
    남긴 bytes_count 로 대신한다(parsers.parse_scan_counts == _total_bytes 규칙)."""
    identity = (job.get("worker_pool") or {}).get("identity") or {}
    requester = identity.get("username")
    d = digest or {}
    total = d.get("total_bytes")
    if total is None and digest is None:
        total = job.get("bytes_count")
    return {
        "job_id": job["job_id"], "request_id": job["request_id"],
        "finished_at": job.get("updated_at"),
        "generated_at_epoch": d.get("generated_at_epoch"),
        "total_bytes": total,
        "summary": d.get("summary") or {},
        "time_histograms": d.get("time_histograms") or {},
        "file_size_histogram": d.get("file_size_histogram") or [],
        "broken_paths_total": d.get("broken_paths_total"),
        "broken_paths_limit": d.get("broken_paths_limit"),
        # 요청자: 잡 스냅샷(worker_pool.identity)의 실행 신원 — 문자열일 때만
        # 노출(모양 투영 원칙: dict 가 오염돼도 경로류가 새지 않는다).
        "requester": requester if isinstance(requester, str) else None,
        "report_readable": digest is not None,
    }


def _base(request: Request) -> str:
    return strip_scheme(resolve_artifact_base(request.app.state.repos.control,
                                              request.app.state.settings))


@router.get("/api/admin/usage/scan-targets")
def scan_targets(request: Request, q: "str | None" = Query(None, max_length=512),
                 storage: "str | None" = Query(None, max_length=512),
                 path: "str | None" = Query(None, max_length=512),
                 order: str = Query("desc", pattern=_ORDER),
                 limit: int = Query(100, ge=1, le=_TARGET_LIMIT)):
    """성공 scan 이 있는 (storage, target) 목록 + 필터(storage 정확 일치·path 부분 문자열, 함께 걸림; q 는 구
    단일 검색) + 최근 스캔 정렬(order). 응답 행: storage_name/target/scan_count/first_scan_at/last_scan_at +
    latest(최신 성공 scan 1건의 포인트 -- 실 사용량·요약 수치·온도 히스토그램, 2026-10-02 목록 컬럼용). 리포트
    요약은 캐시에서 오고, 캐시에 없는 잡만 아티팩트를 연다(보이는 행의 최신 1건뿐)."""
    repos = request.app.state.repos
    rows = repos.data_jobs.scan_targets(q=q or None, storage=storage or None,
                                        path=path or None, order=order, limit=limit)
    recent = repos.data_jobs.recent_scans_by_target(
        per_target=1, pairs=[(r["storage_name"], r["target"]) for r in rows])
    latest_jobs = [jobs[0] for jobs in recent.values()]
    digests = _digests(repos, _base(request), latest_jobs)
    for r in rows:
        jobs = recent.get((r["storage_name"], r["target"])) or []
        r["latest"] = _point(jobs[0], digests.get(jobs[0]["job_id"])) if jobs else None
    return rows


def _previous(job: dict, cached: "dict | None") -> dict:
    """직전 스캔은 증감(실 사용량 차이)만 쓴다 -- 시각·용량만 싣는다(행 크기 절반, 리뷰). 리포트를 새로 읽지 않는다:
    캐시에 있으면 그 총량, 없으면 러너가 같은 규칙으로 DB 에 남긴 bytes_count(_point 의 대체와 같은 근거)."""
    total = _project_report(cached)["total_bytes"] if cached is not None else None
    if total is None and cached is None:
        total = job.get("bytes_count")
    return {"job_id": job["job_id"], "request_id": job["request_id"],
            "finished_at": job.get("updated_at"), "total_bytes": total}


@router.get("/api/admin/usage/export")
def export_usage(request: Request, q: "str | None" = Query(None, max_length=512),
                 storage: "str | None" = Query(None, max_length=512),
                 path: "str | None" = Query(None, max_length=512),
                 order: str = Query("desc", pattern=_ORDER)):
    """사용량 분석 전체 내보내기(2026-10-02 사용자 요청: "모든 정보를 csv 로, 항목당 한 줄"). 필터·정렬은 목록과
    같고 행 상한만 넉넉하다(_EXPORT_LIMIT, 넘으면 truncated). 행마다 latest(최신 성공 scan 의 전체 요약)·previous
    (그 직전 -- 증감용 시각·용량만)를 싣는다. CSV 조립은 포탈이 한다 -- hot 비율·구간 라벨 같은 화면 규칙을 한
    벌로 두려고.

    응답은 이 동기 핸들러(스레드풀) 안에서 JSON 바이트로 만들어 돌려준다 -- dict 를 그대로 돌려주면 FastAPI 가
    jsonable_encoder·직렬화를 **이벤트 루프 위에서** 돌려, 큰 내보내기 동안 /healthz 를 포함한 모든 요청이 멈춘다
    (리뷰 실측: 1만 행에 6초)."""
    repos = request.app.state.repos
    targets = repos.data_jobs.scan_targets(q=q or None, storage=storage or None, path=path or None,
                                           order=order, limit=_EXPORT_LIMIT + 1)
    truncated = len(targets) > _EXPORT_LIMIT
    targets = targets[:_EXPORT_LIMIT]
    recent = repos.data_jobs.recent_scans_by_target(
        per_target=2, pairs=[(t["storage_name"], t["target"]) for t in targets])
    latest_jobs = [js[0] for js in recent.values()]
    digests = _digests(repos, _base(request), latest_jobs)
    prev_cached = repos.scan_digests.get_many([js[1]["job_id"] for js in recent.values() if len(js) > 1])
    rows = []
    for t in targets:
        js = recent.get((t["storage_name"], t["target"])) or []
        rows.append({**t,
                     "latest": _point(js[0], digests.get(js[0]["job_id"])) if js else None,
                     "previous": _previous(js[1], prev_cached.get(js[1]["job_id"])) if len(js) > 1 else None})
    body = {"generated_at": utc_now_iso(), "count": len(rows), "truncated": truncated, "rows": rows}
    return Response(content=json.dumps(body, separators=(",", ":")).encode(), media_type="application/json")


@router.get("/api/admin/usage/scan-storages")
def scan_storages(request: Request):
    """성공 scan 기록이 있는 스토리지 이름(필터 선택지). 등록 스토리지 목록만으로는 삭제·개명된 스토리지 이름으로
    남은 이력을 좁힐 수 없다(리뷰) -- 화면이 등록 목록과 합친다."""
    return request.app.state.repos.data_jobs.scan_storage_names()


@router.get("/api/admin/usage/scan-history")
def scan_history(request: Request, storage: str = Query(..., min_length=1),
                 target: str = Query(..., min_length=1),
                 limit: int = Query(30, ge=1, le=_MAX_POINTS)):
    """한 (storage, target)의 scan 시계열. 포인트마다 실 사용량(total_bytes)·
    요약 수치·온도 히스토그램(모양 투영)을 싣는다. 시간 오름차순 — 차트가
    그대로 그린다. 성공 scan 이 없으면 빈 points(정상값)다: 목록 화면의 빈
    상태이지 오류가 아니다(404 로 접으면 "타깃이 사라졌다"와 구별 불능)."""
    repos = request.app.state.repos
    rows = repos.data_jobs.succeeded_scans_for_target(storage, target, limit=limit)
    digests = _digests(repos, _base(request), rows)
    points, skipped = [], 0
    for row in rows:
        digest = digests.get(row["job_id"])
        if digest is None:
            skipped += 1        # 이력은 못 읽은 포인트를 지어내지 않는다(목록의 latest 와 다르다 -- 위 docstring)
        else:
            points.append(_point(row, digest))
    # updated_at(완료 시각, ISO 문자열) 오름차순 — 문자열 정렬이 곧 시간 정렬인
    # 포맷이다. generated_at_epoch 는 구형 리포트에서 결측이라 1차 키로 못 쓰고
    # (결측을 0으로 두면 맨 앞으로 튄다), **같은 초에 끝난** 포인트의 타이브레이크
    # 로만 쓴다(그 안에서의 결측 -1 은 한 초 안의 순서 문제일 뿐이다). job_id 는
    # 마지막 안정성 키.
    points.sort(key=lambda p: (
        p["finished_at"] or "",
        p["generated_at_epoch"] if p["generated_at_epoch"] is not None else -1,
        p["job_id"]))
    return {"storage_name": storage, "target": target,
            "points": points, "skipped_unreadable": skipped,
            # 창 고지(2026-08-23 리뷰): 이력은 최신 limit 건 창이다. 행 수가 창을
            # 꽉 채웠으면 그 너머가 있을 수 있다 -- 화면이 "전체 이력"인 척하면
            # 타깃 목록의 전수 scan_count 와 한 화면에서 모순된다.
            "window_limit": limit, "window_full": len(rows) == limit}
