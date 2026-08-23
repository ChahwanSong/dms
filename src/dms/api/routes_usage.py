"""사용량 분석(2026-08-23 사용자 요청): 한 디렉터리의 scan 이력을 시계열로.

- 전 요청자 통합이 계약이다("요청자에 관계없이 모든 작업") — 그래서 admin 전용
  라우터다. scan 리포트는 경로·수치가 든 운영 데이터라 사용자 격리(자기 요청만)
  를 깨는 순간 관리자 화면이어야 한다(request_scan_stats 와 같은 이유).
- 모양 투영은 scan_path_stats 와 **한 벌**을 공유한다(routes_scan_paths 의 「왜」:
  키 화이트리스트만으로는 리포트 값 안의 경로 유출을 못 막는다).
- 아티팩트 읽기는 동기 스레드풀 점유라(artifacts.py 의 위험 기록) 포인트 수를
  상한(_MAX_POINTS)으로 묶는다. 못 읽는 리포트는 포인트를 지어내는 대신
  skipped 로 센다 — 단일 리포트 라우트(503 scan_report_too_large)와 달리 시계열
  하나가 전체 이력을 죽이면 안 되고, 숨기면 "그날 스캔이 없었다"는 거짓이 된다.
"""
import json

from fastapi import APIRouter, Depends, Query, Request
from ..artifact_base import resolve_artifact_base
from .artifacts import ArtifactError, read_artifact, strip_scheme
from .auth import require_admin
from .routes_scan_paths import (_is_number, _numbers, _time_histograms,
                                _total_bytes)

router = APIRouter(dependencies=[Depends(require_admin)])

# 시계열 포인트 상한. 60 = 아티팩트 읽기(최대 256KiB I/O) 예산이자, 화면이 한
# 차트에 정직하게 그릴 수 있는 밀도의 상한(그 이상은 어차피 겹쳐 안 보인다).
_MAX_POINTS = 60
_TARGET_LIMIT = 200


@router.get("/api/admin/usage/scan-targets")
def scan_targets(request: Request, q: "str | None" = Query(None, max_length=512),
                 limit: int = Query(100, ge=1, le=_TARGET_LIMIT)):
    """성공 scan 이 있는 (storage, target) 목록 + 검색(부분 문자열). 응답 행:
    storage_name/target/scan_count/last_scan_at — DB 필드만이라 아티팩트를 열지
    않는다(목록은 싸고, 리포트는 선택된 타깃에서만 읽는다)."""
    return request.app.state.repos.data_jobs.scan_targets(q=q or None, limit=limit)


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
    base = strip_scheme(resolve_artifact_base(repos.control,
                                              request.app.state.settings))
    points, skipped = [], 0
    for row in rows:
        point = _project_point(base, row)
        if point is None:
            skipped += 1
        else:
            points.append(point)
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


def _project_point(base: str, row: dict) -> "dict | None":
    """성공 scan 잡 1건 -> 시계열 포인트. 리포트를 못 읽으면 None(호출자가 센다).

    truncated(256KiB 초과)도 못 읽는 것으로 접는다: 꼬리만 온 JSON 은 어차피
    파싱이 깨지고, 여기서 503 을 던지면 병든 리포트 하나가 이력 전체를 막는다."""
    try:
        f = read_artifact(base, row["job_id"], "execution", "dscan-report.json")
    except ArtifactError:
        return None
    if f["truncated"]:
        return None
    try:
        report = json.loads(f["content"])
    except ValueError:
        return None
    if not isinstance(report, dict):
        return None
    epoch = report.get("generated_at_epoch")
    hists = _time_histograms(report.get("time_histograms"))
    identity = (row.get("worker_pool") or {}).get("identity") or {}
    requester = identity.get("username")
    return {
        "job_id": row["job_id"], "request_id": row["request_id"],
        "finished_at": row.get("updated_at"),
        "generated_at_epoch": epoch if _is_number(epoch) else None,
        # 실 사용량: null == 모름(구형·오염 리포트) ≠ 0(빈 트리) — 화면이 그
        # 포인트를 0 으로 그리면 "그날 다 지워졌다"는 거짓 절벽이 된다.
        "total_bytes": _total_bytes(hists),
        "summary": _numbers(report.get("summary")),
        "time_histograms": hists,
        # 요청자: 잡 스냅샷(worker_pool.identity)의 실행 신원 — 문자열일 때만
        # 노출(모양 투영 원칙: dict 가 오염돼도 경로류가 새지 않는다).
        "requester": requester if isinstance(requester, str) else None,
    }
