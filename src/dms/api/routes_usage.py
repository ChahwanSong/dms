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
import sys

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from ..artifact_base import resolve_artifact_base
from ..db import utc_now_iso
from ..repositories.request_purges import MAX_BATCH_DELETE_CHILDREN, MAX_BATCH_DELETE_ITEMS
from .artifacts import ArtifactError, job_owner_uid, read_artifact, strip_scheme
from .auth import Identity, audit_actor, require_admin, require_session_admin
from .routes_request_purge import MAX_DELETE, MAX_DELETE_BATCHES
from .routes_requests import reject_when_maintenance
from .routes_scan_paths import (_buckets, _is_number, _numbers, _time_histograms,
                                _total_bytes)
from ..usage_metrics import METRIC_SORTS, SORT_PATTERN, sort_by_metric

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


def _with_latest(request: Request, rows: list) -> list:
    """행마다 latest(최신 성공 scan 1건의 포인트)를 붙인다. 리포트 요약은 캐시에서 오고, 캐시에 없는 잡만 아티팩트를 연다."""
    repos = request.app.state.repos
    recent = repos.data_jobs.recent_scans_by_target(
        per_target=1, pairs=[(r["storage_name"], r["target"]) for r in rows])
    latest_jobs = [jobs[0] for jobs in recent.values()]
    digests = _digests(repos, _base(request), latest_jobs)
    for r in rows:
        jobs = recent.get((r["storage_name"], r["target"])) or []
        r["latest"] = _point(jobs[0], digests.get(jobs[0]["job_id"])) if jobs else None
    return rows


@router.get("/api/admin/usage/scan-targets")
def scan_targets(request: Request, q: "str | None" = Query(None, max_length=512),
                 storage: "str | None" = Query(None, max_length=512),
                 path: "str | None" = Query(None, max_length=512),
                 order: str = Query("desc", pattern=_ORDER),
                 limit: int = Query(100, ge=1, le=_TARGET_LIMIT),
                 sort: str = Query("last_scan", pattern=SORT_PATTERN)):
    """성공 scan 이 있는 (storage, target) 목록 + 필터(storage 정확 일치·path 부분 문자열, 함께 걸림; q 는 구
    단일 검색) + 정렬(sort·order). 응답 행: storage_name/target/scan_count/first_scan_at/last_scan_at +
    latest(최신 성공 scan 1건의 포인트 -- 실 사용량·요약 수치·온도 히스토그램, 2026-10-02 목록 컬럼용).

    정렬(2026-10-19 사용자 요청: 「실사용량, 파일수, hot비율 등을 오름/내림차순 정렬」): last_scan(기본)·scans 는 SQL 이
    limit 전에 정렬한다. bytes·files·hot 은 리포트 요약 값이라 SQL 열이 아니다 -- 필터에 맞는 대상 창(최근 스캔 순 상한
    _EXPORT_LIMIT, 내보내기와 같은 창)의 latest 를 만들어 화면과 같은 값으로 정렬한 뒤 limit 만큼 자른다(usage_metrics).
    받은 묶음만 화면이 정렬하면 「최근 N개 안의 순위」라 그 밖의 큰 디렉터리가 상위에 안 나온다. 창의 latest 는 리포트
    요약 캐시로 만든다(캐시에 없는 잡만 아티팩트를 연다 -- 첫 정렬만 느리고 그 뒤는 DB 한 번)."""
    repos = request.app.state.repos
    if sort in METRIC_SORTS:
        window = repos.data_jobs.scan_targets(q=q or None, storage=storage or None, path=path or None,
                                              order="desc", limit=_EXPORT_LIMIT)
        return sort_by_metric(_with_latest(request, window), sort, order)[:limit]
    rows = repos.data_jobs.scan_targets(q=q or None, storage=storage or None,
                                        path=path or None, order=order, limit=limit, sort=sort)
    return _with_latest(request, rows)


def _storage_infos(repos, names) -> dict:
    """스토리지 이름 -> 내보내기 행의 스토리지 정보(2026-10-12 사용자 보고: CSV 「두번째 행부터는 backend type, mount
    path 등이 누락」). 예전엔 포탈이 **지금 등록된** 목록에서만 찾아, 삭제된(미등록) 스토리지의 행은 그 열이 비었다 --
    잡·요청은 스토리지 설정을 스냅숏하지 않는다. 등록돼 있으면 지금 값(source "registered"), 아니면 감사 기록의 마지막
    설정(source "last_known" -- StoragesRepository.last_known_config), 그것도 없으면 값 null·source null(모름). 등록 여부는
    따로 싣는다(registered) -- 빈 칸이 「모름」인지 「등록돼 있지 않음」인지 CSV 가 말하게."""
    registered = {s["storage_name"]: s for s in repos.storages.list()}
    out = {}
    for n in names:
        s = registered.get(n)
        if s is not None:
            out[n] = {"registered": True, "source": "registered", "backend_type": s["backend_type"],
                      "mount_path": s["mount_path"], "managed_root": s["managed_root"]}
            continue
        known = repos.storages.last_known_config(n)
        out[n] = {"registered": False, "source": "last_known" if known else None,
                  **(known or {"backend_type": None, "mount_path": None, "managed_root": None})}
    return out


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
                 order: str = Query("desc", pattern=_ORDER),
                 sort: str = Query("last_scan", pattern=SORT_PATTERN)):
    """사용량 분석 전체 내보내기(2026-10-02 사용자 요청: "모든 정보를 csv 로, 항목당 한 줄"). 필터·정렬은 목록과
    같고 행 상한만 넉넉하다(_EXPORT_LIMIT, 넘으면 truncated). 행마다 latest(최신 성공 scan 의 전체 요약)·previous
    (그 직전 -- 증감용 시각·용량만)를 싣는다. CSV 조립은 포탈이 한다 -- hot 비율·구간 라벨 같은 화면 규칙을 한
    벌로 두려고.

    응답은 이 동기 핸들러(스레드풀) 안에서 JSON 바이트로 만들어 돌려준다 -- dict 를 그대로 돌려주면 FastAPI 가
    jsonable_encoder·직렬화를 **이벤트 루프 위에서** 돌려, 큰 내보내기 동안 /healthz 를 포함한 모든 요청이 멈춘다
    (리뷰 실측: 1만 행에 6초).

    정렬(sort·order)은 목록과 같다: bytes·files·hot 은 창(최근 스캔 순 _EXPORT_LIMIT)을 만든 뒤 행을 그 값으로 정렬한다."""
    repos = request.app.state.repos
    metric = sort in METRIC_SORTS
    targets = repos.data_jobs.scan_targets(q=q or None, storage=storage or None, path=path or None,
                                           order="desc" if metric else order, limit=_EXPORT_LIMIT + 1,
                                           sort="last_scan" if metric else sort)
    truncated = len(targets) > _EXPORT_LIMIT
    targets = targets[:_EXPORT_LIMIT]
    recent = repos.data_jobs.recent_scans_by_target(
        per_target=2, pairs=[(t["storage_name"], t["target"]) for t in targets])
    latest_jobs = [js[0] for js in recent.values()]
    digests = _digests(repos, _base(request), latest_jobs)
    prev_cached = repos.scan_digests.get_many([js[1]["job_id"] for js in recent.values() if len(js) > 1])
    infos = _storage_infos(repos, dict.fromkeys(t["storage_name"] for t in targets))
    rows = []
    for t in targets:
        js = recent.get((t["storage_name"], t["target"])) or []
        rows.append({**t,
                     "storage": infos.get(t["storage_name"]),
                     "latest": _point(js[0], digests.get(js[0]["job_id"])) if js else None,
                     "previous": _previous(js[1], prev_cached.get(js[1]["job_id"])) if len(js) > 1 else None})
    if metric:
        rows = sort_by_metric(rows, sort, order)
    body = {"generated_at": utc_now_iso(), "count": len(rows), "truncated": truncated, "rows": rows}
    return Response(content=json.dumps(body, separators=(",", ":")).encode(), media_type="application/json")


@router.get("/api/admin/usage/scan-storages")
def scan_storages(request: Request):
    """성공 scan 기록이 있는 스토리지 이름(필터 선택지). 등록 스토리지 목록만으로는 삭제·개명된 스토리지 이름으로
    남은 이력을 좁힐 수 없다(리뷰) -- 화면이 등록 목록과 합친다."""
    return request.app.state.repos.data_jobs.scan_storage_names()


@router.get("/api/admin/usage/unregistered-storages")
def unregistered_storages(request: Request):
    """사용량 분석에 남은 미등록 스토리지(등록 목록에 없는 이름 -- 삭제된 스토리지의 scan 기록)와 정리 규모(2026-10-12
    사용자 요청: 「미등록 스토리지 항목들은 스토리지 항목별로 전체 clean up」). 이름 = 그 스토리지의 scan 잡이 있는 이름
    (성공이 아니어도 -- 정리가 실패·취소 scan 도 지우므로, 일시적 사유로 남은 것이 있으면 행이 남아 다시 정리할 수
    있다). 행: storage_name, target_count·succeeded_scan_count·last_scan_at(사용량 화면 기준 = 성공 scan, 없으면 0·null),
    single_count(배치 밖 scan 요청 수), batches([{batch_id, request_count}] -- 배치째 지울 수 있는 배치와 지금 자식 수),
    mixed_batches(같은 모양 -- 다른 스토리지·다른 작업이 섞여 정리가 건너뛸 배치), request_count(정리하면 지워질 작업 수
    = 단건 + batches 의 자식), last_known(감사 기록의 마지막 설정 | null). 미등록 판정은 서버가 한다 -- 화면의 판정(등록
    목록 로딩 여부)에 기대지 않는다. 질의는 이름 수와 무관한 묶음 몇 개다(request_purges.unregistered_scan_history)."""
    repos = request.app.state.repos
    usage = {r["storage_name"]: r for r in repos.data_jobs.unregistered_scan_storages()}
    hist = repos.request_purges.unregistered_scan_history()
    out = []
    for name in sorted(set(usage) | set(hist)):
        u = usage.get(name) or {"target_count": 0, "succeeded_scan_count": 0, "last_scan_at": None}
        h = hist.get(name) or {"singles": [], "batches": {}, "mixed_batches": {}}
        batches = [{"batch_id": b, "request_count": n} for b, n in h["batches"].items()]
        mixed = [{"batch_id": b, "request_count": n} for b, n in h["mixed_batches"].items()]
        out.append({"storage_name": name, "target_count": u["target_count"],
                    "succeeded_scan_count": u["succeeded_scan_count"], "last_scan_at": u["last_scan_at"],
                    "single_count": len(h["singles"]), "batches": batches, "mixed_batches": mixed,
                    "request_count": len(h["singles"]) + sum(b["request_count"] for b in batches),
                    "last_known": repos.storages.last_known_config(name)})
    return out


class PurgeScanHistoryBody(BaseModel):
    # 이름은 경로가 아니라 본문으로 -- 옛 스토리지 이름이 지금의 이름 규칙(storages._NAME_RE)에 맞는다는 보장이 없다.
    storage_name: str = Field(min_length=1, max_length=512)
    # 앞 회차에서 건너뛴 항목(포탈이 모아 보낸다) -- 이번 회차는 그 다음 것들을 시도한다. 없으면 대상 목록 앞쪽의 영구
    # 거부(섞인 배치·비정상 행)가 회차 상한(배치 10·단건 200)을 매번 차지해 뒤의 지울 수 있는 기록에 영영 닿지 못한다.
    # 상한 = 포탈 회차 상한(useUsage.PURGE_MAX_ROUNDS 100) x 회차 상한(단건 200·배치 10) -- 한 번의 정리에서 건너뛴 것을
    # 전부 실을 수 있어야 마지막 회차가 422 로 끊기지 않는다.
    exclude_request_ids: list[str] = Field(default_factory=list, max_length=20000)
    exclude_batch_ids: list[str] = Field(default_factory=list, max_length=1000)


@router.post("/api/admin/usage/unregistered-storages:purge")
def purge_unregistered_storage(body: PurgeScanHistoryBody, request: Request,
                               identity: Identity = Depends(require_session_admin)):
    """미등록 스토리지 하나의 scan 작업 기록 정리 **한 회차**(2026-10-12). **세션 관리자만**(작업 삭제와 같은 경계 --
    CLAUDE.md 「작업(요청) 삭제는 세션 관리자」, 공유 토큰 403 admin_session_required). 유지보수 중 503.

    - 대상: 그 스토리지의 scan 잡을 가진 요청 전부(request_purges.scan_storage_selection -- 성공·실패·취소). 배치 자식은
      배치째(그 스토리지의 scan 만 담은 배치일 때만), 나머지는 단건. rm·sync 기록과 스토리지의 실제 데이터는 건드리지 않는다.
    - 삭제는 일반 작업 삭제와 **같은 몸통**(delete_terminal·delete_batch)을 scan_storage= 로 부른다 -- 같은 게이트(종단·
      조용한 창·상한), 같은 감사·정리 아웃박스에 더해 잠근 행으로 미등록(storage_registered)·순수성
      (storage_history_mismatch·storage_history_batch_mixed)을 다시 판정한다. 여기서 먼저 등록 여부를 보는 409 는 빠른
      거절일 뿐이다(진짜 판정은 트랜잭션 안).
    - 회차 상한 = 작업 삭제 API 와 같다(배치 10·단건 200) -- 한 HTTP 요청이 RLock 을 쥐는 시간을 묶는다. 응답의
      remaining(남은 단건·배치 수 -- 건너뛴 것 포함)을 보고 포탈이 다시 부른다. 건너뛴 항목은 다음 회차의
      exclude_request_ids·exclude_batch_ids 로 돌아와 빠진다(본문 주석) -- 포탈은 시도할 것이 없는 회차에서 멈춘다.
    - 회차마다 묶음 감사 1행(storage_history/purge) -- 지운 요청·배치마다의 감사는 삭제 몸통이 남긴다.
    응답: {storage_name, deleted:[{request_id, job_ids}], deleted_batches:[{batch_id, request_ids, job_ids, dangling}],
    skipped:[{request_id, reason}], skipped_batches:[{batch_id, reason, request_id}], remaining:{requests, batches}(남은
    전부 -- 건너뛴 것 포함), remaining_untried:{requests, batches}(남은 것 중 이번 본문의 exclude_* 와 이번 회차에 건너뛴
    것을 뺀 수 = 다음 회차가 시도할 것 -- 포탈은 이것이 0 이면 멈춘다. 건너뛴 id 의 **개수**와 remaining 을 비교하면, 건너뛴
    뒤 다른 곳에서 지워진 id 만큼 아직 시도하지 않은 기록을 남긴 채 멈췄다), purge_pending}."""
    reject_when_maintenance(request)
    repos, settings = request.app.state.repos, request.app.state.settings
    name = body.storage_name
    if repos.storages.get(name) is not None:
        raise HTTPException(status_code=409, detail="storage_registered")
    base = strip_scheme(resolve_artifact_base(repos.control, settings) or "") or None
    actor = audit_actor(identity)
    sel = repos.request_purges.scan_storage_selection(name)
    skip_r, skip_b = set(body.exclude_request_ids), set(body.exclude_batch_ids)
    batches = [(b, n) for b, n in sel["batches"].items() if b not in skip_b][:MAX_DELETE_BATCHES]
    singles = [r for r in sel["singles"] if r not in skip_r][:MAX_DELETE]
    deleted, deleted_batches, skipped, skipped_batches = [], [], [], []
    for bid, count in batches:
        try:
            r = repos.request_purges.delete_batch(
                bid, expected_request_count=count, actor=actor, artifact_base=base,
                quiet_seconds=settings.request_delete_quiet_seconds, max_children=MAX_BATCH_DELETE_CHILDREN,
                max_items=MAX_BATCH_DELETE_ITEMS, scan_storage=name)
        except Exception as exc:       # 한 배치가 회차 전체를 500 으로 만들지 않는다(작업 삭제 API 와 같은 이유)
            print(f"storage history purge: batch {bid} failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            skipped_batches.append({"batch_id": bid, "reason": "batch_delete_failed", "request_id": None})
            continue
        if r["deleted"]:
            deleted_batches.append({"batch_id": bid, "request_ids": r["request_ids"], "job_ids": r["job_ids"],
                                    "dangling": r["dangling"]})
        else:
            skipped_batches.append({"batch_id": bid, "reason": r["reason"], "request_id": r["request_id"]})
    for rid in singles:
        try:
            r = repos.request_purges.delete_terminal(
                rid, actor=actor, artifact_base=base,
                quiet_seconds=settings.request_delete_quiet_seconds, scan_storage=name)
        except Exception as exc:
            print(f"storage history purge: request {rid} failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            skipped.append({"request_id": rid, "reason": "request_delete_failed"})
            continue
        if r["deleted"]:
            deleted.append({"request_id": rid, "job_ids": r["job_ids"]})
        else:
            skipped.append({"request_id": rid, "reason": r["reason"]})
    # 아래는 덤이다 -- 무엇을 지웠는지(이미 커밋)가 응답의 본체라, 세다가·감사를 쓰다가 DB 가 끊겨도 500 으로 그것을
    # 숨기지 않는다(작업 삭제 API 의 pending_count 와 같은 이유). remaining None = 모름(포탈은 멈춘다).
    if deleted or deleted_batches or skipped or skipped_batches:
        reasons: dict = {}
        for s in skipped + skipped_batches:
            reasons[s["reason"]] = reasons.get(s["reason"], 0) + 1
        try:
            repos.request_purges.audit_scan_history_purge(name, actor=actor, summary={
                "deleted_request_ids": [d["request_id"] for d in deleted],
                "deleted_batch_ids": [d["batch_id"] for d in deleted_batches],
                "deleted_batch_request_count": sum(len(d["request_ids"]) for d in deleted_batches),
                "skipped_reasons": reasons})
        except Exception as exc:
            print(f"storage history purge: audit failed for {name}: {type(exc).__name__}: {exc}", file=sys.stderr)
    try:
        after = repos.request_purges.scan_storage_selection(name)
        remaining = {"requests": len(after["singles"]), "batches": len(after["batches"])}
        tried_r = skip_r | {x["request_id"] for x in skipped}
        tried_b = skip_b | {x["batch_id"] for x in skipped_batches}
        untried = {"requests": sum(1 for r in after["singles"] if r not in tried_r),
                   "batches": sum(1 for b in after["batches"] if b not in tried_b)}
    except Exception as exc:
        print(f"storage history purge: remaining count failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        remaining = untried = None
    try:
        pending = repos.request_purges.pending_count()
    except Exception as exc:
        print(f"storage history purge: pending_count failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        pending = None
    return {"storage_name": name, "deleted": deleted, "deleted_batches": deleted_batches, "skipped": skipped,
            "skipped_batches": skipped_batches, "remaining": remaining, "remaining_untried": untried,
            "purge_pending": pending}


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
