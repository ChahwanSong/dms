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
- 스토리지별(2026-10-20 사용자 요청: 「각 스토리지 별로 처음부터 나뉘어져서 보이게끔 하고 필터 및 서버의 스캔도 각
  스토리지 별로」, 창 상한 1만): 포탈은 스토리지 탭마다 storage 를 싣는다 -- 목록·지표 정렬 창·내보내기가 그 스토리지
  안에서만 돈다. 서버의 storage 는 **선택 인자로 남긴다**(롤링 배포 중 열려 있던 옛 탭이 storage 없이 「전체」를 부른다 --
  422 로 거절하면 그 탭이 깨지고, 서버가 기본 스토리지를 고르면 「전체」라고 적힌 화면에 한 스토리지만 나온다). 탭의
  이름·규모·창 상한은 GET /api/admin/usage/storages 가 준다.
- 메모리(같은 날): 창이 1만이 되면서 창 전체의 latest 포인트를 세우는 옛 구조는 1만 내보내기 한 건이 +~465MiB, 동시
  3건이면 api 한도 1Gi 를 넘었다(PG 실측). 그래서 지표 정렬은 창의 정렬 **값만** _CHUNK 묶음으로 계산하고(요약은 값을 뽑은
  뒤 버린다) 반환할 행만 포인트를 만들며, 내보내기는 _CHUNK 타깃 묶음씩 흘려보낸다(StreamingResponse).
- 스토리지 합계(2026-10-21 사용자 요청: 「스토리지별 사용량 및 정보들의 총합」): 지금 탭 하나만 storage-totals 가 게으르게
  계산한다 -- 최상위 대상만·목록 열과 같은 값·지표마다 known/unknown·한 호출 읽기 예산(규칙은 storage_totals docstring).
- 필터(2026-10-29 사용자 요청: 사용량 분석을 사용자별·PV 이름·namespace 등으로): 새 봉투 엔드포인트 GET /targets·/targets/export·
  /facets 와 storage-totals 의 선택 필터. 모든 필터는 **대상 단위**다 -- 한 (storage, target)의 성공 scan 전부가 함께 걸리거나 함께
  빠진다(scan_count·first/last_scan_at·latest·이력은 늘 그 대상의 전체 기록). PV 소속은 읽을 때 정한다(usage_attribution --
  지금 인벤토리 > PV 주소 scan 기록 > 모름/PV 밖). 요청자(requests.requester_id)는 SQL 이고, PV 축은 정렬과 무관한 최근 스캔 순
  창(최대 _WINDOW_LIMIT + 1 -- /facets·필터 합계와 같은 창)을 순서를 보존하며 거른 뒤 그 안에서 요청한 정렬로 놓는다(_select_heads
  -- 창이 넘치면 matched_count 는 null(모름)이고 포탈이 「최근 스캔된 N개 안에서」라고 말한다).
  응답은 서버가 적용한 필터를 filters 로 되돌려 준다 -- 롤링 배포 중 새 포탈이 옛 API 를 부르면 모르는 파라미터가 조용히 무시돼
  「필터 켜진 화면 + 거르지 않은 목록」이 되는데, 메아리가 없으면 포탈이 그것을 알아챈다.
- 검색(같은 날 사용자 요청: 「경로로 검색할 뿐 아니라, pv 이름도 검색할 수 있게 하고 일부 단어만 포함해도 뜨도록」): /targets·
  /targets/export 의 q -- 토큰(text_search.search_tokens: 공백으로 나눔·최대 8개, 작업 목록 검색과 같은 뜻) 모두가 대상 경로 또는
  소속 PV 정체 하나의 PV 이름(PVC 이름)에 대소문자 무시 부분 일치(usage_attribution.search_matches). 역시 **대상 단위**다. PV 이름이
  생길 수 있는 스토리지(usage_attribution.may_attribute_pv)는 PV 축 필터와 같은 판정 창에서 거르고(넘치면 matched_count null -- 그때도
  모든 토큰이 경로에 든 대상은 창 밖까지 SQL 로 더한다: 경로 검색은 예전 path 처럼 창에 묶이지 않는다, _with_path_hits -- 그래서
  내보내기가 상한에서 알려진 일치를 자를 수 있고 봉투의 capped 가 창 넘침과 따로 그것을 말한다, export_targets), 그 밖은
  SQL(scan_target_heads(tokens=))로 정확히 건다. 옛 path(경로 부분 일치 -- 방언 LIKE 그대로라 대소문자 무시를 보장하지 않는다)는
  옛 탭 호환으로 뜻 그대로 남고 q 와 함께 걸린다.
  facets·storage-totals 는 q 를 받지 않는다(합계는 경로 검색과 무관 -- 2026-10-21).
- **옛 탭 호환 동결**: scan-targets·export 는 본문을 바꾸지 않는다(봉투 없는 배열이라 메아리를 실을 자리가 없다 -- 옛 탭 JS 가
  그 모양을 읽는다; tests/test_usage_equivalence.py 가 바이트를 고정한다). 새 포탈은 /targets 를 쓴다 -- 다음 정리 때 제거한다
  (docs/BACKLOG.md).
"""
import json
import sys
import threading
import time
from dataclasses import dataclass
from typing import Iterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from ..artifact_base import resolve_artifact_base
from ..db import utc_now_iso
from ..repositories.request_purges import MAX_BATCH_DELETE_CHILDREN, MAX_BATCH_DELETE_ITEMS
from .artifacts import ArtifactError, job_owner_uid, read_artifact, strip_scheme
from .auth import Identity, audit_actor, require_admin, require_session_admin
from .routes_request_purge import MAX_DELETE, MAX_DELETE_BATCHES
from .routes_requests import reject_when_maintenance
from .routes_scan_paths import (_buckets, _is_number, _numbers, _report_total_bytes,
                                _summary_count, _time_histograms)
from ..metrics_series import storage_filesystem
from ..repositories.text_search import search_tokens
from ..usage_attribution import (FACET_LIST_CAP, PvFilter, attribute, collect, facet_counts, matches, may_attribute_pv,
                                 ranked, search_matches)
from ..usage_metrics import METRIC_SORTS, SORT_PATTERN, StorageTotals, maximal_targets, metric_value, sort_keyed

router = APIRouter(dependencies=[Depends(require_admin)])

# 시계열 포인트 상한. 60 = 아티팩트 읽기(최대 256KiB I/O) 예산이자, 화면이 한
# 차트에 정직하게 그릴 수 있는 밀도의 상한(그 이상은 어차피 겹쳐 안 보인다).
_MAX_POINTS = 60
_TARGET_LIMIT = 200
# 창 상한(2026-10-20 사용자 요청: 「서버의 스캔 상한선도 5천개에서 1만개로」) -- 지표 정렬(bytes·files·hot)의 대상 창과
# 내보내기 상한에 함께 쓴다(2026-10-29 부터 PV 필터의 판정 창·/facets·필터 합계의 창도). 전 타깃이 계약이지만 무한은 아니다 --
# 내보내기는 넘으면 잘라 내고 truncated(알려진 일치를 잘랐으면 capped 도)로 말한다(조용한 절단 금지), 포탈은 /targets 봉투의
# matched_count 가 이 값을 넘거나 window_truncated(PV 판정 창 넘침)일 때만 「최근 스캔된 N개 안에서 정렬」을 알린다(N = 봉투의
# window_limit -- 탭의 대상 수가 아니다: 검색·필터가 걸리면 그 수는 거짓이다). 메모리 근거(2026-10-20 실측, 1만 타깃 한 스토리지 + 6.5만 잡, 요약 평균 3.6KB): 창
# 전체의 포인트를 세우고 응답을 통째로 만들던 옛 구조는 1만 내보내기 한 건이 +~465MiB(PG·sqlite 같음), PG 동시 3건이면 HWM 1.3GiB
# 로 api 한도 1Gi 를 넘었다. 값만 뽑는 목록은 +18~29MiB, 묶음 스트리밍 내보내기는 +37~53MiB(첫 호출; 반복 +4~13MiB, PG 동시 2건
# +~60MiB), 시간은 sqlite 내보내기 16.5초 → 2.8초·지표 정렬 목록 15초 → 1.3초다(인덱스 포함) -- 이 값을 올리기 전에 같은 측정을
# 다시 하라.
# 필터(2026-10-29 실측, 같은 1만 대상·6.5만 잡 + 다른 스토리지 2천 대상, PV 600개(클러스터 둘)·RoleBinding namespace 마다 3명,
# 요약 캐시 데운 상태, 케이스마다 새 uvicorn, 첫 호출 시간·HWM 증가 「sqlite / PG」): 옛 엔드포인트 대비 무필터 /targets 는
# +0.01~0.03초·+0~3MiB(last 0.25 / 0.25초 +8 / +5MiB, bytes 1.30 / 1.40초 +31 / +30MiB), 요청자 경로 0.30 / 0.27초(bytes 1.05 /
# 1.12초), PV 필터 목록 0.32~0.95 / 0.44~1.04초 +13~33MiB, /facets 0.42 / 0.62초 +15 / +13MiB(동시 3건 1.29 / 1.73초 +44 / +37MiB),
# 필터 합계 0.92~1.13 / 1.08~1.36초 +21~23MiB(무필터 1.33 / 1.45초), 필터 내보내기 1.82~3.19 / 2.02~3.42초 +48~51MiB(무필터 /targets/
# export 3.00 / 3.31초 +53 / +52MiB -- 옛 export 보다 +0.1~0.4초·+4MiB, 행의 pv 칸), 동시 3건 내보내기 +119 / +120MiB(옛 export 와
# 같은 급, 1Gi 대비 여유). 창 넘침(sqlite 1.25만 대상·8만 잡): PV 목록 0.41초 +15MiB, facets 0.50초, 필터 합계 1.06초. 구성
# (PG, 운영 모양 잡음 -- 잡·요청 32만 행·스토리지 14개, 수치는 위와 ±5%): 머리 W+1 216ms·요청자 머리 230ms, collect 141ms(기록 블록
# 1500 대상 포함), pv_root_index 냉 9ms·메모 0.4ms, 판정 1만 건 12ms·거르기 16ms·facets 집계 14ms, scan_requester_counts 120ms
# (custom plan 이 requests 를 Parallel Seq Scan -- 요청 테이블 크기에 비례). PG EXPLAIN (GENERIC_PLAN): 요청자 경로의 data_jobs 접근은
# 옛 무필터 문장과 같은 `Bitmap Index Scan on idx_data_jobs_scan_target` + 서브쿼리 `idx_requests_requester`(hashed SubPlan),
# 기록·요청자 수 질의는 idx_data_jobs_scan_target + requests_pkey(한 스토리지가 잡의 92% 인 밀집 분포에선 무필터·요청자 모두 Seq Scan
# -- 회귀 아님). 측정 스크립트는 리뷰 작업 디렉터리의 perf/ 였다(커밋하지 않음 -- 다시 잴 때 같은 조건으로). 측정 뒤 PV 경로의
# 판정 창을 정렬과 무관한 최근 스캔 순으로 고정했다(_select_heads) -- 머리 문장 모양이 같고 더해진 것은 창 안의 정수 안정 정렬·
# 묶음 뒤집기(_window_order, 1만 행 수 ms)뿐이라 수치는 같은 급이다.
_WINDOW_LIMIT = 10000
# 창·내보내기의 묶음 크기 -- 캐시 조회(get_many)·잡 조회(target IN·job_id IN)·캐시 쓰기(put_many)가 한 번에 다루는 상한.
# 메모리는 묶음 하나(요약·잡 행 500개)만큼만 든다.
_CHUNK = 500
# 스토리지 합계 한 호출이 여는 아티팩트 상한(캐시에 없는 최상위 대상의 리포트) -- 나머지는 pending 으로 말하고, 포탈이
# 다시 부르면 캐시가 찬 만큼 이어 간다. 탭을 열기만 해도 한 요청이 공유 스토리지 I/O 를 최대 1만 건(동기 스레드풀 점유,
# artifacts.py 의 위험 기록) 붙잡지 않게 한 묶음으로 묶는다.
_TOTALS_READ_BUDGET = 500
# 스토리지 합계의 읽기 실패 기억(2026-10-21 리뷰): 못 읽은 리포트는 영구 캐시(scan_digests)에 두지 않으므로, 기억이 없으면
# 다음 호출이 같은 순서(최근 스캔 순)로 같은 실패를 다시 열어 예산을 먼저 쓴다 -- 영구히 못 읽는 최상위가 예산만큼 앞에
# 있으면 그 뒤의 읽을 수 있는 대상에 영영 닿지 못하고(pending 이 줄지 않고 progressed False -- 포탈이 멈춘다), 호출마다 같은
# 아티팩트 500개(256KiB 씩)를 다시 연다. TTL = 일시 오류(마운트 흔들림·롤아웃 중)를 영구 「모름」으로 굳히지 않는 창.
# 상한 = 창(_WINDOW_LIMIT) 둘 -- 한 스토리지의 실패 전부가 들어가고도 남는다(항목당 ~150B, 2만이면 ~3MiB).
_FAILED_READ_TTL_SECONDS = 600
_FAILED_READ_CAP = 20000
_ORDER = "^(asc|desc)$"
# 새 필터 값(2026-10-29): 빈 문자열은 min_length 로 422(「필터 없음」으로 접지 않는다 -- storage 의 빈 이름 교훈), 제어문자도 422
# (NUL 은 PG text 가 받지 못해 psycopg DataError → 500 이 된다 -- text_search docstring).
_FILTER_TEXT = r"^[^\x00-\x1f\x7f]+$"
# 필터 값의 길이 상한(2026-10-29 2차 리뷰): /facets 가 내는 선택지는 같은 API 의 필터가 받아야 한다(서버가 낸 값을 서버가 422 로
# 거절하면 포탈에서 고르는 순간 오류다). PV 축 = k8s 이름 상한(k8s_sync._MAX_NAME 253 -- 스냅숏이 그보다 긴 이름을 거부한다).
# 권한 사용자(dms_user)는 바인딩 이름 `<id>-clusterrole-<level>` 에서 파생해(k8s_rbac) 최대 236자라 같은 상한이다(예전 128 은
# 긴 파생 이름을 거절했다). 요청자는 requests.requester_id(계정 아이디 ≤64, 옛 공유 토큰 제출의 `node:<노드 이름 ≤254>` 259자 --
# api.auth) -- storage·path 와 같은 512.
_NAME_MAX = 253
_REQUESTER_MAX = 512
# 새 엔드포인트(/targets·/targets/export·/facets·storage-totals)의 storage·path(2026-10-29 2차 리뷰): NUL 만 422 로 막는다 -- PG
# text 가 NUL 을 받지 못해 psycopg DataError → 500 이었다(위 _FILTER_TEXT 와 같은 구멍). 빈 문자열(빈 이름 탭 -- storage 의 빈
# 이름 교훈)과 다른 제어문자(구형·변조 행의 이름일 수 있다 -- 탭이 그 이름으로 서야 지울 수 있다)는 그대로 받는다. 옛
# scan-targets·export 는 동결(test_usage_equivalence 의 참조 -- BACKLOG 「기존 결함」).
_NO_NUL = r"^[^\x00]*$"


class ReportReadFailures:
    """job_id -> 마지막 리포트 읽기 실패 시각(monotonic). 프로세스 메모리, 스레드 안전 -- 라우트가 동기 스레드풀에서 돌아
    합계 호출이 겹친다(app.state.report_read_failures, create_app 이 만든다). 레플리카·재시작마다 비지만 목적(같은 실패를 예산
    앞에서 되풀이하지 않기)에는 영향이 없다 -- 비면 한 번 더 열 뿐이다. 영구 캐시가 아니라 여기 두는 이유는 위 상수 주석.

    상한이 차면 만료 항목을 걷고, 그래도 차 있으면 새 실패를 **기록하지 않는다**(오래된 것을 밀어내지 않는다): 밀어내면 여러
    스토리지의 실패가 서로를 밀어내며 포탈이 1초마다 같은 실패를 끝없이 다시 부를 수 있다. 기록하지 못한 실패는 옛 동작(다음
    호출이 다시 연다)으로 돌아갈 뿐이고, record 가 기록한 수를 돌려줘 호출자가 progressed 를 정직하게 말한다."""

    def __init__(self, ttl_seconds: float = _FAILED_READ_TTL_SECONDS, cap: int = _FAILED_READ_CAP,
                 clock=time.monotonic):
        self.ttl_seconds = ttl_seconds
        self.cap = cap
        self._clock = clock
        self._lock = threading.Lock()
        self._failed: dict[str, float] = {}

    def split(self, job_ids) -> "tuple[set, set]":
        """(TTL 안에 실패한 것, TTL 이 지난 옛 실패) -- 나머지는 아직 열어 본 적 없는(또는 기억이 걷힌) 잡이다."""
        now = self._clock()
        recent, expired = set(), set()
        with self._lock:
            for jid in job_ids:
                at = self._failed.get(jid)
                if at is not None:
                    (recent if now - at < self.ttl_seconds else expired).add(jid)
        return recent, expired

    def record(self, job_ids) -> int:
        """실패를 지금 시각으로 기록하고 실제로 기록한 수를 돌려준다(상한 규칙은 클래스 docstring)."""
        now = self._clock()
        noted, swept = 0, False
        with self._lock:
            for jid in job_ids:
                if jid not in self._failed and len(self._failed) >= self.cap:
                    if not swept:           # 한 호출에 한 번만 걷는다(차 있는 동안 id 마다 2만 개를 훑지 않게)
                        self._failed = {j: at for j, at in self._failed.items() if now - at < self.ttl_seconds}
                        swept = True
                    if len(self._failed) >= self.cap:
                        continue
                self._failed[jid] = now
                noted += 1
        return noted


def _project_report(report: dict) -> dict:
    """dscan 리포트(또는 이미 투영된 캐시 값) -> 요약(digest). 투영은 멱등이라 캐시에서 읽은 값에도 다시 건다 --
    DB 가 신뢰 경계라 저장된 모양도 믿지 않는다(경로 문자열이 끼어들 자리가 없게)."""
    epoch = report.get("generated_at_epoch")
    hists = _time_histograms(report.get("time_histograms"))
    summary = _numbers(report.get("summary"))
    broken_total = report.get("broken_paths_total")
    broken_limit = report.get("broken_paths_limit")
    return {
        "generated_at_epoch": epoch if _is_number(epoch) else None,
        # 실 사용량: null == 모름(구형·오염 리포트) ≠ 0(빈 트리) — 화면이 그
        # 포인트를 0 으로 그리면 "그날 다 지워졌다"는 거짓 절벽이 된다. summary.total_bytes(포크 ae781bd~) 우선, 없으면
        # 히스토그램 합(_report_total_bytes) -- 캐시 값도 summary 를 그대로 품고 있어 재투영이 같은 값을 낸다(멱등).
        "total_bytes": _report_total_bytes(summary, hists),
        "summary": summary,
        "time_histograms": hists,
        "file_size_histogram": _buckets(report.get("file_size_histogram")),
        # null≠0: 0 은 "파손 없음"의 정상값, None 은 구형 리포트(키 부재).
        "broken_paths_total": broken_total if _is_number(broken_total) else None,
        "broken_paths_limit": broken_limit if _is_number(broken_limit) else None,
        # 이름이 엄격 UTF-8 이 아닌 항목 수(2026-10-26 -- 사용자 결정 「카운터만 둠」, broken 에 더하지 않는다). 값은 summary
        # 에서 다시 뽑는다(최상위 키를 믿지 않는다 -- 옛 캐시엔 이 키가 없고, 있어도 재투영이 같은 값을 낸다). None = 구형 리포트.
        "invalid_name_entries": _summary_count(summary, "invalid_name_entries"),
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


def _cached_digests(repos, job_ids) -> dict:
    """job_id -> 캐시의 요약(재투영). 캐시에 없는 잡은 결과에 없다."""
    return {jid: _project_report(d) for jid, d in repos.scan_digests.get_many(job_ids).items()}


def _read_and_cache(repos, base: str, jobs: list) -> dict:
    """캐시에 없던 잡들의 리포트를 읽어 투영하고 한 번에(put_many) 캐시에 둔다. job_id -> 요약, 못 읽은 잡은 결과에 없다
    (캐시에도 두지 않는다 -- 일시 오류를 영구 "모름"으로 굳히지 않게)."""
    fresh = {}
    for job in jobs:
        digest = _read_digest(base, job)
        if digest is not None:
            fresh[job["job_id"]] = digest
    if fresh:
        repos.scan_digests.put_many(fresh)
    return fresh


def _digests(repos, base: str, jobs: list, *, skip=frozenset()) -> dict:
    """job_id -> digest. 캐시에 있으면 그것(재투영), 없으면 리포트를 읽어 투영하고 캐시에 둔다. 못 읽은 잡은 결과에
    없다. skip = 같은 요청의 앞 단계(지표 정렬 값 계산)가 이미 읽다 실패한 잡 -- 다시 열지 않는다(한 응답 안에서 정렬 값과
    보이는 값이 같은 읽기에서 나오게, 크기 초과 리포트를 두 번 256KiB 씩 읽지 않게)."""
    cached = _cached_digests(repos, [j["job_id"] for j in jobs])
    cached.update(_read_and_cache(repos, base, [j for j in jobs
                                                if j["job_id"] not in cached and j["job_id"] not in skip]))
    return cached


def _point(job: dict, digest: "dict | None") -> dict:
    """잡 1건 + 리포트 요약 -> 포인트(이력·목록·내보내기 공용 모양). digest 가 없으면(리포트를 못 읽음)
    report_readable=False 이고 리포트 유래 값은 모름(null/빈 값)이다 -- 실 사용량만은 러너가 같은 규칙으로 DB 에
    남긴 bytes_count 로 대신한다(parsers.parse_scan_counts == _report_total_bytes 규칙)."""
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
        "invalid_name_entries": d.get("invalid_name_entries"),
        # 요청자: 잡 스냅샷(worker_pool.identity)의 실행 신원 — 문자열일 때만
        # 노출(모양 투영 원칙: dict 가 오염돼도 경로류가 새지 않는다).
        "requester": requester if isinstance(requester, str) else None,
        "report_readable": digest is not None,
    }


def _base(request: Request) -> str:
    return strip_scheme(resolve_artifact_base(request.app.state.repos.control,
                                              request.app.state.settings))


def _public(head: dict) -> dict:
    """머리 행(DataJobsRepository.scan_target_heads) -> 목록·내보내기 행의 공개 열. 열과 순서는 옛 GROUP BY 질의 그대로다
    (응답 바이트가 같게 -- tests/test_usage_equivalence.py). job_id·bytes_count 같은 정렬 재료는 싣지 않는다."""
    return {"storage_name": head["storage_name"], "target": head["target"], "scan_count": head["scan_count"],
            "first_scan_at": head["first_scan_at"], "last_scan_at": head["last_scan_at"]}


def _recent_jobs(repos, heads: list, per_target: int) -> dict:
    """(storage_name, target) -> 최근 성공 scan 잡 행(최신순, 최대 per_target). 스토리지별로 `target IN (…)` 한 묶음씩
    묻는다 -- 포탈의 스토리지 탭은 한 스토리지라 한 문장이고, storage 없는 옛 탭의 「전체」만 스토리지 수만큼 나뉜다."""
    by_storage: dict = {}
    for h in heads:
        by_storage.setdefault(h["storage_name"], []).append(h["target"])
    out: dict = {}
    for name, targets in by_storage.items():
        for target, jobs in repos.data_jobs.recent_scans_for_targets(name, targets, per_target=per_target).items():
            out[(name, target)] = jobs
    return out


def _with_latest(repos, base: str, heads: list, *, skip=frozenset()) -> list:
    """반환할 행마다 latest(최신 성공 scan 1건의 포인트)를 붙인다. 리포트 요약은 캐시에서 오고, 캐시에 없는 잡만
    아티팩트를 연다. 호출자는 limit(≤ _TARGET_LIMIT) 뒤의 행만 넘긴다 -- 한 묶음이다."""
    recent = _recent_jobs(repos, heads, 1)
    digests = _digests(repos, base, [jobs[0] for jobs in recent.values()], skip=skip)
    out = []
    for h in heads:
        jobs = recent.get((h["storage_name"], h["target"])) or []
        out.append({**_public(h),
                    "latest": _point(jobs[0], digests.get(jobs[0]["job_id"])) if jobs else None})
    return out


def _metric_order(repos, base: str, heads: list, sort: str, order: str, unreadable: set) -> list:
    """창(머리 행들)을 지표 값(bytes·files·hot)으로 정렬한다 -- 화면과 같은 값(usage_metrics.metric_value 를 latest 포인트에
    거는 것과 같은 결과)이지만 포인트는 남기지 않는다. _CHUNK 묶음마다: 최신 잡 job_id 로 캐시를 묻고, 없는 잡만 전체 행
    (요청자 uid)을 읽어 리포트를 열고 한 번에 캐시에 넣은 뒤(put_many), 값만 남기고 요약은 버린다. 못 읽은 잡은
    unreadable 에 모은다(반환할 행·내보낼 행이 다시 열지 않게 -- _digests 의 skip).

    storage_name 이 NULL 인 머리는 값이 없다(모름 -- 리포트도 열지 않는다): 반환 행의 latest 는 (storage, target) 으로 잡을
    찾는데 NULL 은 아무것도 맞지 않는다(recent_scans_for_targets). 정렬 값과 보이는 값이 한 판정이어야 「모름은 맨 뒤」가
    화면과 맞는다."""
    keys = []
    for i in range(0, len(heads), _CHUNK):
        chunk = heads[i:i + _CHUNK]
        ids = [h["job_id"] for h in chunk if h["storage_name"] is not None]
        digests = _cached_digests(repos, ids)
        missing = [jid for jid in ids if jid not in digests]
        if missing:
            fresh = _read_and_cache(repos, base, list(repos.data_jobs.jobs_by_ids(missing).values()))
            unreadable.update(jid for jid in missing if jid not in fresh)
            digests.update(fresh)
        for h in chunk:
            keys.append(None if h["storage_name"] is None
                        else metric_value({"latest": _point(h, digests.get(h["job_id"]))}, sort))
    return sort_keyed(list(zip(heads, keys)), order)


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
    storage 는 포탈의 스토리지 탭이 늘 싣는다(2026-10-20) -- 없으면 전 스토리지(옛 탭 호환, 모듈 docstring). 실렸으면
    빈 문자열도 그 이름 하나다(정확 일치 -- /storages 가 변조·구형 행의 빈 이름도 탭으로 내므로, 「필터 없음」으로 접으면
    그 탭의 목록·CSV 가 전 스토리지가 됐다, 리뷰). 옛 포탈은 값이 있을 때만 storage 를 실어 이 차이를 밟지 않는다.

    정렬(2026-10-19 사용자 요청: 「실사용량, 파일수, hot비율 등을 오름/내림차순 정렬」): last_scan(기본)·scans 는 SQL 이
    limit 전에 정렬하고, 반환 행만 latest 를 만든다. bytes·files·hot 은 리포트 요약 값이라 SQL 열이 아니다 -- 필터에 맞는
    대상 창(최근 스캔 순 상한 _WINDOW_LIMIT, 내보내기와 같은 창)의 정렬 값을 화면과 같은 규칙으로 계산해 정렬한 뒤
    (_metric_order -- 값만, 포인트는 반환 행만) limit 만큼 자른다(usage_metrics). 받은 묶음만 화면이 정렬하면 「최근 N개
    안의 순위」라 그 밖의 큰 디렉터리가 상위에 안 나온다. 값은 리포트 요약 캐시로 만든다(캐시에 없는 잡만 아티팩트를 연다
    -- 첫 정렬만 느리고 그 뒤는 DB 몇 번)."""
    repos = request.app.state.repos
    flt = {"q": q or None, "storage": storage, "path": path or None}
    base = _base(request)
    unreadable: set = set()
    if sort in METRIC_SORTS:
        window = repos.data_jobs.scan_target_heads(**flt, order="desc", limit=_WINDOW_LIMIT)
        page = _metric_order(repos, base, window, sort, order, unreadable)[:limit]
    else:
        page = repos.data_jobs.scan_target_heads(**flt, order=order, limit=limit, sort=sort)
    return _with_latest(repos, base, page, skip=unreadable)


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


def _export_body(repos, base: str, heads: list, infos: dict, opening: bytes, skip, extra=None) -> Iterator[bytes]:
    """내보내기 본문을 _CHUNK 타깃 묶음씩 흘린다. 묶음마다 최근 두 잡(최신 + 직전)·최신의 요약(캐시, 없으면 읽고 put_many)·
    직전의 캐시 요약만 읽어 행 JSON 을 만들고 버린다 -- 피크 메모리가 묶음 하나다. 행의 바이트는 옛 통짜 본문의 그 행과
    같다(json.dumps 같은 인자 -- 목록 안 객체의 인코딩은 단독 인코딩과 같다). extra(머리 → 값)가 있으면 행 끝(previous 뒤)에
    "pv" 로 싣는다(새 /targets/export -- None 이면 바이트가 옛 그대로다).

    도중 실패(DB 끊김 등)는 삼키지 않는다 -- 예외가 그대로 올라가 연결이 끊기고 본문은 닫히지 않은 JSON 이 된다. 포탈
    파서가 그걸 실패로 보여야 한다(조용히 잘린 CSV 가 「전부」인 척하면 안 된다). 묶음 사이에 스냅숏은 없다: 그 사이 지워진
    타깃의 행은 latest·previous 가 null 이다(행 자체는 머리 질의 시점의 것)."""
    yield opening
    n = 0
    for i in range(0, len(heads), _CHUNK):
        chunk = heads[i:i + _CHUNK]
        recent = _recent_jobs(repos, chunk, 2)
        digests = _digests(repos, base, [js[0] for js in recent.values()], skip=skip)
        prev_cached = repos.scan_digests.get_many([js[1]["job_id"] for js in recent.values() if len(js) > 1])
        parts = []
        for h in chunk:
            js = recent.get((h["storage_name"], h["target"])) or []
            row = {
                **_public(h),
                "storage": infos.get(h["storage_name"]),
                "latest": _point(js[0], digests.get(js[0]["job_id"])) if js else None,
                "previous": _previous(js[1], prev_cached.get(js[1]["job_id"])) if len(js) > 1 else None}
            if extra is not None:
                row["pv"] = extra(h)
            parts.append(json.dumps(row, separators=(",", ":")))
        yield (("," if n else "") + ",".join(parts)).encode()
        n += len(chunk)
    yield f'],"count":{n}}}'.encode()


@router.get("/api/admin/usage/export")
def export_usage(request: Request, q: "str | None" = Query(None, max_length=512),
                 storage: "str | None" = Query(None, max_length=512),
                 path: "str | None" = Query(None, max_length=512),
                 order: str = Query("desc", pattern=_ORDER),
                 sort: str = Query("last_scan", pattern=SORT_PATTERN)):
    """사용량 분석 전체 내보내기(2026-10-02 사용자 요청: "모든 정보를 csv 로, 항목당 한 줄"). 필터·정렬은 목록과
    같고 행 상한만 넉넉하다(_WINDOW_LIMIT, 넘으면 truncated). 행마다 latest(최신 성공 scan 의 전체 요약)·previous
    (그 직전 -- 증감용 시각·용량만)를 싣는다. CSV 조립은 포탈이 한다 -- hot 비율·구간 라벨 같은 화면 규칙을 한
    벌로 두려고. 포탈은 지금 탭의 storage 를 싣는다(2026-10-20 -- 한 파일 = 한 스토리지).

    본문 모양은 {generated_at, truncated, rows, count} 이고 _CHUNK 타깃 묶음씩 흘려보낸다(2026-10-20 -- 1만 행을 통째로
    세우면 한 건이 +~465MiB 였다, _WINDOW_LIMIT 주석). count 가 맨 뒤인 이유: 다 흘린 뒤에야 확정된다(포탈 파서는 키
    순서를 보지 않는다). truncated 는 첫 바이트 전에 정한다(머리를 상한 + 1 개 읽는다). 첫 바이트 전의 일(머리 질의·지표
    정렬 값·스토리지 정보)은 이 동기 핸들러(스레드풀) 안에서 한다 -- 거기서 난 실패는 평범한 500 이다. 묶음은 starlette 가
    동기 제너레이터를 청크마다 스레드풀로 돌린다(이벤트 루프를 막지 않는다 -- 옛 「dict 를 돌려주면 직렬화가 루프 위에서
    돈다(1만 행에 6초 동안 /healthz 까지 멈춤)」 문제를 같은 이유로 피한다).

    정렬(sort·order)은 목록과 같다: bytes·files·hot 은 창(최근 스캔 순 _WINDOW_LIMIT)의 정렬 값을 먼저 계산해(_metric_order)
    그 순서로 흘린다."""
    repos = request.app.state.repos
    metric = sort in METRIC_SORTS
    heads = repos.data_jobs.scan_target_heads(q=q or None, storage=storage, path=path or None,
                                              order="desc" if metric else order, limit=_WINDOW_LIMIT + 1,
                                              sort="last_scan" if metric else sort)
    truncated = len(heads) > _WINDOW_LIMIT
    heads = heads[:_WINDOW_LIMIT]
    base = _base(request)
    unreadable: set = set()
    if metric:
        heads = _metric_order(repos, base, heads, sort, order, unreadable)
    infos = _storage_infos(repos, dict.fromkeys(h["storage_name"] for h in heads))
    opening = (f'{{"generated_at":{json.dumps(utc_now_iso())},"truncated":{json.dumps(truncated)},'
               f'"rows":[').encode()
    return StreamingResponse(_export_body(repos, base, heads, infos, opening, unreadable),
                             media_type="application/json")


# ---- 2026-10-29: 필터(사용자별·PV 이름·namespace 등) -- 새 봉투 엔드포인트 ----

@dataclass(frozen=True)
class AttrFilter:
    """요청자·PV 쪽 필터(이미 검증·파싱된 값). 라우트 함수에 직접 넘길 때도 이 객체를 만든다 -- Query 기본값 객체(FieldInfo)가
    필터 값으로 새지 않게(_filter_params 만 FastAPI 가 푼다)."""
    pv: PvFilter = PvFilter()
    requester: "str | None" = None

    @property
    def active(self) -> bool:
        return self.pv.active or self.requester is not None

    def echo(self) -> dict:
        """적용한 필터의 메아리(None 도 그대로 -- 포탈이 보낸 것과 대조한다)."""
        p = self.pv
        return {"cluster": p.cluster, "namespace": p.namespace, "pvc": p.pvc, "storage_class": p.storage_class,
                "user": p.user, "scope": p.scope, "requester": self.requester}


def _filter_params(cluster: "str | None" = Query(None, min_length=1, max_length=_NAME_MAX, pattern=_FILTER_TEXT),
                   namespace: "str | None" = Query(None, min_length=1, max_length=_NAME_MAX, pattern=_FILTER_TEXT),
                   pvc: "str | None" = Query(None, min_length=1, max_length=_NAME_MAX, pattern=_FILTER_TEXT),
                   storage_class: "str | None" = Query(None, min_length=1, max_length=_NAME_MAX, pattern=_FILTER_TEXT),
                   user: "str | None" = Query(None, min_length=1, max_length=_NAME_MAX, pattern=_FILTER_TEXT),
                   scope: "str | None" = Query(None, pattern="^(pv|outside|unknown)$"),
                   requester: "str | None" = Query(None, min_length=1, max_length=_REQUESTER_MAX, pattern=_FILTER_TEXT)
                   ) -> AttrFilter:
    """새 사용량 엔드포인트 공통의 필터 파라미터 한 벌(의미는 usage_attribution 모듈 docstring). cluster·namespace·pvc·
    storage_class = 소속 PVC 정체의 칸 정확 일치(같은 정체 하나가 모두 만족), user = 그 정체의 namespace 에 지금 인식된
    RoleBinding 이 있는 dms_user, scope = pv(인벤토리·기록)·outside·unknown, requester = 그 대상의 성공 scan 중 하나라도
    requests.requester_id 가 이 값. 화면 「PV 이름」 = pvc(PVC 이름 -- k8sTerms)."""
    return AttrFilter(pv=PvFilter(cluster=cluster, namespace=namespace, pvc=pvc, storage_class=storage_class, user=user,
                                  scope=scope), requester=requester)


def _window_order(heads: list, sort: str, order: str) -> list:
    """최근 스캔 순 창(scan_target_heads(order="desc", sort="last_scan") -- `last_scan_at DESC, storage_name, target`)을 요청한
    SQL 정렬(last_scan·scans × asc·desc -- scan_target_heads 의 ORDER BY)의 순서로 다시 놓는다. **문자열을 비교하지 않는다**: 이름
    순(storage_name, target -- 방언 콜레이션)은 SQL 이 정한 창 안의 상대 순서를 그대로 물려받고, 여기서는 정수(scan_count)
    비교와 시각 값의 같음만 쓴다 -- 그래서 창이 대상 전부면 결과가 그 정렬의 SQL 결과와 같다(동률 포함 -- 두 방언 테스트).

    - scans: ORDER BY 가 `scan_count {방향}, last_scan_at DESC, storage_name, target` 이라 2차 이하가 창 순서 그대로다 -- 안정
      정렬을 scan_count 하나로(reverse=True 도 같은 키끼리 원래 순서를 지킨다).
    - last_scan asc: `last_scan_at ASC, storage_name, target` -- 같은 시각 묶음(창에서 이어져 있다)의 순서를 뒤집고 묶음 안은
      그대로 둔다(통째로 뒤집으면 동률이 이름 역순이 된다). NULL 묶음도 한 묶음이라 두 방언의 NULL 자리(PG DESC 앞/ASC 뒤,
      sqlite DESC 뒤/ASC 앞)가 함께 뒤집힌다."""
    if sort == "scans":
        return sorted(heads, key=lambda h: h["scan_count"], reverse=order != "asc")
    if order != "asc":
        return list(heads)
    runs: list = []
    for h in heads:
        if runs and runs[-1][0]["last_scan_at"] == h["last_scan_at"]:
            runs[-1].append(h)
        else:
            runs.append([h])
    return [h for run in reversed(runs) for h in run]


def _select_heads(repos, *, base: str, unreadable: set, storage: str, path: "str | None", requester: "str | None",
                  pvf: PvFilter, order: str, sort: str, limit_rows: int, tokens=()) -> tuple:
    """필터에 맞는 머리를 요청한 순서로 최대 limit_rows 개 → (heads, matched_count | None, window_truncated, capped, attrs). 목록·
    내보내기·합계가 이 한 벌을 쓴다. attrs = 이 함수가 판정한 대상의 소속(target → usage_attribution.attribute) -- SQL 경로는 비어 있다.
    tokens = 검색(q)의 토큰(search_tokens -- 빈 것 = 검색 없음, 합계는 넘기지 않는다). capped = **알려진** 맞는 대상이 돌려준 것보다
    많다(limit_rows 또는 지표 정렬의 창에서 잘랐다 -- 2026-10-29 2차 리뷰): SQL 경로는 matched_count > 행 수, PV 경로는 판정한 결과가
    limit_rows 를 넘음. 창이 넘쳐 판정하지 않은 대상(모름)은 세지 않는다 -- 그건 window_truncated 의 몫이다. 창 넘침이 아닌 PV 경로의
    내보내기(limit_rows = 창)는 잘릴 수 없고, 창 밖 경로 일치를 더하는 _with_path_hits 만 상한을 넘길 수 있다(내보내기가 이 둘을 따로
    말한다 -- export_targets).

    - SQL 경로(PV 축·구분이 없고, 검색이 없거나 그 스토리지에 PV 이름이 생길 수 없음 -- may_attribute_pv False): path·requester·
      검색 토큰(경로만 -- scan_target_heads(tokens=))은 SQL 의 LIMIT 앞이라 정확하다. matched_count = COUNT(*) OVER()(필터에 맞는
      대상 전체 -- 창과 무관). 지표 정렬은 예전처럼 최근 스캔 순 _WINDOW_LIMIT 창 안에서 정렬한다.
    - PV 경로(PV 축·구분이 있거나, PV 이름이 생길 수 있는 스토리지의 검색): 머리를 **정렬과 무관하게 최근 스캔 순으로** SQL 에서
      _WINDOW_LIMIT + 1 개 읽고(path·requester 는 SQL 에 실린다 -- 검색 토큰은 PV 이름이 생길 수 없을 때만 SQL 에, 아니면 판정 뒤
      search_matches 로: 토큰이 PV 이름에만 맞을 수 있다) 순서를 보존하며 소속·검색으로 거른 뒤, 요청한 정렬로 놓는다(지표 정렬은
      _metric_order, last_scan·scans 는 _window_order -- 문자열 비교 없이 SQL 순서를 물려받는다). 창이 넘치지 않았으면 결과가
      전역적으로 정확하다. 창이 넘치면(window_truncated -- 그 스토리지의 (경로 검색·요청자에 맞는) 대상이 창보다 많다) 창 밖은
      판정하지 않았으므로 matched_count 는 null(모름)이다(0 이 아니다). 단 PV 이름까지 보는 검색은 창이 넘쳐도 창 밖의 경로 일치(모든
      토큰이 경로에)를 SQL 로 더한다(_with_path_hits, 2026-10-29 리뷰) -- 창에 묶이는 것은 PV 이름이 있어야 맞는 대상뿐이다.
      판정 창을 정렬마다 바꾸지 않는 이유(2026-10-29 리뷰): 요청한 정렬로 창을 자르면 「오래된 순」·「스캔 횟수」에서 창이 가장
      오래된·가장 많이 스캔된 1만 개가 돼, 정렬을 바꾸기만 해도 걸리는 대상 **집합**이 바뀌고 facets 의 옵션 수(최근 스캔 순 창)·
      필터 합계·포탈의 「최근 스캔된 N개 안에서」와 서로 어긋났다. 이제 판정 창은 /facets·필터 합계·내보내기와 한 규칙이다."""
    metric = sort in METRIC_SORTS
    pv_search = bool(tokens) and may_attribute_pv(repos, storage)
    flt = {"storage": storage, "path": path, "requester": requester,
           "tokens": list(tokens) if tokens and not pv_search else None}
    if not pvf.active and not pv_search:
        if metric:
            window = repos.data_jobs.scan_target_heads(**flt, order="desc", limit=_WINDOW_LIMIT, with_matched=True)
            heads = _metric_order(repos, base, window, sort, order, unreadable)[:limit_rows]
        else:
            window = heads = repos.data_jobs.scan_target_heads(**flt, order=order, sort=sort, limit=limit_rows,
                                                               with_matched=True)
        matched = window[0]["matched_count"] if window else 0
        return heads, matched, False, matched > len(heads), {}
    cand = repos.data_jobs.scan_target_heads(**flt, order="desc", limit=_WINDOW_LIMIT + 1)
    window_truncated = len(cand) > _WINDOW_LIMIT
    cand = cand[:_WINDOW_LIMIT]
    inputs = collect(repos, storage, cand)
    attrs = {h["target"]: attribute(h["target"], inputs) for h in cand}
    kept = [h for h in cand if matches(attrs[h["target"]], pvf, inputs.users)
            and (not pv_search or search_matches(h["target"], attrs[h["target"]], tokens))]
    if pv_search and window_truncated:
        ordered, cut = _with_path_hits(repos, base=base, unreadable=unreadable, storage=storage,
                                       flt={**flt, "tokens": list(tokens)}, pvf=pvf, order=order, sort=sort,
                                       limit_rows=limit_rows, window=cand, kept=kept, attrs=attrs)
        return ordered[:limit_rows], None, True, cut or len(ordered) > limit_rows, attrs
    ordered = (_metric_order(repos, base, kept, sort, order, unreadable) if metric
               else _window_order(kept, sort, order))
    return (ordered[:limit_rows], (None if window_truncated else len(kept)), window_truncated, len(ordered) > limit_rows,
            attrs)


def _with_path_hits(repos, *, base: str, unreadable: set, storage: str, flt: dict, pvf: PvFilter, order: str, sort: str,
                    limit_rows: int, window: list, kept: list, attrs: dict) -> tuple:
    """PV 이름까지 보는 검색의 판정 창이 넘쳤을 때(2026-10-29 리뷰): 창 안의 결과(kept -- 최근 스캔 순)에 **창 밖의 경로 일치**(모든
    토큰이 경로에 든 대상 -- flt 의 tokens 를 실은 scan_target_heads, SQL 이라 창과 무관)를 더해 요청한 순서로 놓는다. 포탈이 path 대신
    q 를 보내면서 경로로 찾던 오래된 대상이 창 밖이라 안 걸렸다(예전 path 는 SQL 의 LIMIT 앞이라 정확했다 -- 사용자 요청은 「경로로
    검색할 뿐 아니라」) -- 이제 판정 창에 묶이는 것은 PV 이름이 있어야 맞는 대상뿐이다(그래서 맞는 수는 여전히 모름). attrs 에 새로
    판정한 소속을 더한다.

    - PV 축·구분이 없고 정렬이 last_scan·scans: 창 밖 일치를 요청한 정렬 그대로 limit_rows(+ 1, 아래 →) 개 SQL 로 읽어 _merge_outside 로
      합친다.
      맞는 대상의 앞 limit_rows 개는 「창 안 결과 ∪ 경로 일치의 앞 limit_rows 개」 안에 있다(경로 일치 중 그 밖의 것 앞에는 맞는
      대상이 이미 limit_rows 개다) -- 경로 일치 수와 무관하게 예전 path 와 같은 순서·같은 앞부분이다.
    - 그 밖(PV 축·구분 -- 창 밖 대상도 소속을 판정해야 한다, 또는 지표 정렬 -- 맞는 대상 중 최근 창 안에서 정렬한다): 경로 일치끼리의
      최근 스캔 순 창(_WINDOW_LIMIT -- 예전 「path + PV 필터」의 판정 창과 같다)에서 창 밖 대상을 PV 필터로 거르고 최근 스캔 순으로
      이어 붙인다(창은 최근 스캔 순의 앞부분이라 창 안의 어느 대상도 창 밖의 어느 대상보다 그 순서에서 앞이다 -- 이어 붙인 것이 곧
      최근 스캔 순이다). 지표 정렬은 그 앞 _WINDOW_LIMIT 개(SQL 경로의 「맞는 대상 중 최근 N개 안에서 정렬」과 같은 범위).

    → (ordered, cut). 호출자는 ordered 를 limit_rows 에서 자르고 capped = cut 또는 len(ordered) > limit_rows 다(_select_heads -- 알려진
    맞는 대상을 잘랐나, 2026-10-29 2차 리뷰: 내보내기의 상한 = 창이라 경로 일치를 더하면 넘을 수 있다). 그래서 경로 일치는 **하나 더**
    읽는다 -- 첫 갈래는 limit_rows + 1 개(앞 limit_rows 개의 순서는 그대로다: 위 논증이 어느 앞부분에도 선다)라 len(ordered) 가 상한을
    넘는 것으로 더 있음을 안다(창 안에 맞는 것이 없으면 ordered 가 꼭 limit_rows 개라 하나 덜 읽으면 못 안다). 둘째 갈래는 경로 일치끼리의
    창(_WINDOW_LIMIT)을 넘었는지 보려고 + 1 개를 읽고 창은 그대로 쓴다: PV 필터가 없으면 그 밖의 경로 일치도 맞는 대상이라 cut 이고
    (지표 정렬이 앞 _WINDOW_LIMIT 개로 자른 것도 cut), PV 필터가 있으면 그 밖은 판정하지 않은 것(모름)이라 cut 이 아니다."""
    inside = {h["target"] for h in window}
    if not pvf.active and sort not in METRIC_SORTS:
        outside = [h for h in repos.data_jobs.scan_target_heads(**flt, order=order, sort=sort, limit=limit_rows + 1)
                   if h["target"] not in inside]
        return _merge_outside(_window_order(kept, sort, order), outside, sort, order), False
    hits = repos.data_jobs.scan_target_heads(**flt, order="desc", limit=_WINDOW_LIMIT + 1)
    beyond = len(hits) > _WINDOW_LIMIT and not pvf.active
    extra = [h for h in hits[:_WINDOW_LIMIT] if h["target"] not in inside]
    if pvf.active:
        more = collect(repos, storage, extra)
        attrs.update({h["target"]: attribute(h["target"], more) for h in extra})
        extra = [h for h in extra if matches(attrs[h["target"]], pvf, more.users)]
    merged = kept + extra
    if sort in METRIC_SORTS:
        return (_metric_order(repos, base, merged[:_WINDOW_LIMIT], sort, order, unreadable),
                beyond or len(merged) > _WINDOW_LIMIT)
    return _window_order(merged, sort, order), beyond


def _merge_outside(inside: list, outside: list, sort: str, order: str) -> list:
    """판정 창 안의 결과(inside)와 창 밖의 대상(outside)을 -- 둘 다 요청한 SQL 정렬(last_scan·scans × asc·desc)의 순서로 놓인 것 --
    그 정렬 하나로 합친다. 문자열은 비교하지 않는다(_window_order 와 같은 이유 -- 방언 콜레이션): 창은 최근 스캔 순(`last_scan_at
    DESC, storage_name, target`)의 앞부분이라 그 순서에서 창 안의 어느 대상도 창 밖의 어느 대상보다 앞이고, 두 대상의 앞뒤는 이
    사실과 정수 비교·시각 값의 같음만으로 정해진다(_inside_first)."""
    out: list = []
    i = j = 0
    while i < len(inside) and j < len(outside):
        if _inside_first(inside[i], outside[j], sort, order):
            out.append(inside[i])
            i += 1
        else:
            out.append(outside[j])
            j += 1
    return out + inside[i:] + outside[j:]


def _inside_first(w: dict, p: dict, sort: str, order: str) -> bool:
    """창 안 대상 w 가 창 밖 대상 p 보다 요청한 정렬에서 앞인가(_merge_outside).
    - scans(`scan_count {방향}, last_scan_at DESC, storage_name, target`): 횟수가 다르면 그 방향으로, 같으면 2차 이하가 최근 스캔
      순이라 창 안이 앞.
    - last_scan desc: 늘 창 안이 앞.
    - last_scan asc(`last_scan_at ASC, storage_name, target`): 같은 시각이면(창 경계에 걸친 묶음 -- 묶음 안은 두 방향 모두 이름순이라
      최근 스캔 순에서 앞인 창 안이 여기서도 앞) 창 안, 아니면 창 밖(더 오래됐다). NULL 도 같다: sqlite 는 NULL 이 DESC 끝·ASC 처음이라
      창 밖의 NULL 이 앞이고, PG 는 DESC 처음·ASC 끝이라 창 밖에 NULL 이 있으면 창이 전부 NULL 이다(같은 묶음)."""
    if sort == "scans":
        a, b = w["scan_count"], p["scan_count"]
        if a != b:
            return a > b if order != "asc" else a < b
        return True
    if order != "asc":
        return True
    return w["last_scan_at"] == p["last_scan_at"]


def _attach_attrs(repos, storage: str, heads: list, attrs: dict) -> dict:
    """반환·내보낼 머리 전부의 소속(SQL 경로는 여기서 처음 판정한다 -- 필터가 없어도 행마다 pv 를 싣는다)."""
    missing = [h for h in heads if h["target"] not in attrs]
    if not missing:
        return attrs
    inputs = collect(repos, storage, missing)
    return {**attrs, **{h["target"]: attribute(h["target"], inputs) for h in missing}}


def _echo(storage: str, path: "str | None", q: "str | None", flt: AttrFilter) -> dict:
    """적용 필터의 메아리. q 는 받은 원문 그대로(빈 문자열은 None -- path 와 같다). 공백뿐인 q 는 원문으로 돌려주되 검색은 없다
    (search_tokens 가 [] -- 포탈이 보낸 값과 대조할 수 있게 낮추거나 다듬지 않는다)."""
    return {"storage": storage, "path": path, "q": q, **flt.echo()}


@router.get("/api/admin/usage/targets")
def usage_targets(request: Request, storage: str = Query(..., max_length=512, pattern=_NO_NUL),
                  path: "str | None" = Query(None, max_length=512, pattern=_NO_NUL),
                  q: "str | None" = Query(None, max_length=512, pattern=_NO_NUL),
                  order: str = Query("desc", pattern=_ORDER),
                  limit: int = Query(100, ge=1, le=_TARGET_LIMIT),
                  sort: str = Query("last_scan", pattern=SORT_PATTERN),
                  flt: AttrFilter = Depends(_filter_params)):
    """사용량 목록(새 포탈 -- scan-targets 의 뒤를 잇는 봉투). storage 필수(정확 일치, 빈 문자열도 이름), path 는 scan-targets 와
    같은 부분 문자열(빈 값 = 없음, 옛 탭 호환), q 는 경로·PV 이름 검색(모듈 docstring 「검색」 -- 토큰 AND·부분 일치·대소문자 무시,
    빈 값·공백뿐 = 없음, path 와 함께 걸린다), 필터는 _filter_params. 응답: {rows:[{storage_name, target, scan_count, first_scan_at,
    last_scan_at, latest, pv:{source, root, volumes, users}}], matched_count(필터에 맞는 대상 수 -- null = 창이 넘쳐 모름),
    window_limit, window_truncated(PV 축·PV 이름 검색 판정이 최근 스캔 순 창 안에서만 이뤄졌나), filters(서버가 적용한 값의 메아리 --
    q 포함)}. 행의 pv 는 필터가 없어도 싣는다(반환 행 ≤ _TARGET_LIMIT 만 판정한다). 정렬·latest 규칙은 scan-targets 그대로다."""
    repos = request.app.state.repos
    path, q = path or None, q or None
    base = _base(request)
    unreadable: set = set()
    heads, matched, window_truncated, _, attrs = _select_heads(
        repos, base=base, unreadable=unreadable, storage=storage, path=path, requester=flt.requester, pvf=flt.pv,
        order=order, sort=sort, limit_rows=limit, tokens=search_tokens(q))
    attrs = _attach_attrs(repos, storage, heads, attrs)
    rows = _with_latest(repos, base, heads, skip=unreadable)
    for row in rows:
        row["pv"] = attrs[row["target"]]
    return {"rows": rows, "matched_count": matched, "window_limit": _WINDOW_LIMIT,
            "window_truncated": window_truncated, "filters": _echo(storage, path, q, flt)}


@router.get("/api/admin/usage/targets/export")
def export_targets(request: Request, storage: str = Query(..., max_length=512, pattern=_NO_NUL),
                   path: "str | None" = Query(None, max_length=512, pattern=_NO_NUL),
                   q: "str | None" = Query(None, max_length=512, pattern=_NO_NUL),
                   order: str = Query("desc", pattern=_ORDER),
                   sort: str = Query("last_scan", pattern=SORT_PATTERN),
                   flt: AttrFilter = Depends(_filter_params)):
    """사용량 내보내기(새 포탈 -- export 의 뒤를 잇는다). 파라미터는 /targets 와 같다(q 포함, limit 없음 -- 상한 _WINDOW_LIMIT). 본문:
    {generated_at, truncated, capped, matched_count, filters, rows:[{…, storage, latest, previous, pv}], count}(count 맨 뒤 -- 다
    흘린 뒤에야 정해진다). truncated·capped 는 첫 바이트 전에 정한다. truncated = 행이 전부가 아닐 수 있다 = 판정 창이 넘침(PV 경로 --
    그 스토리지의 대상이 상한보다 많아 PV 필터·PV 이름 검색이 정렬과 무관하게 최근 스캔된 상한 개 안에서만 걸렀다, matched_count null)
    또는 capped. capped = 알려진 맞는 대상을 상한에서 잘랐다(_select_heads -- SQL 경로는 matched_count 가 상한을 넘음, PV 경로는 창 밖의
    경로 일치를 더한 결과가 상한을 넘음: 2026-10-29 2차 리뷰 -- 그 전엔 창 넘침과 봉투가 같아 포탈이 「PV 이름으로 맞는 대상은 창
    안에서만」만 말했고 경로 일치가 잘린 것이 안 보였다). 포탈은 둘을 따로 말한다(창 문구 · 「상한 N개까지만」). 스트리밍·_CHUNK·
    도중 실패 시 닫히지 않은 JSON 은 export 와 같다(_export_body)."""
    repos = request.app.state.repos
    path, q = path or None, q or None
    base = _base(request)
    unreadable: set = set()
    heads, matched, window_truncated, capped, attrs = _select_heads(
        repos, base=base, unreadable=unreadable, storage=storage, path=path, requester=flt.requester, pvf=flt.pv,
        order=order, sort=sort, limit_rows=_WINDOW_LIMIT, tokens=search_tokens(q))
    truncated = window_truncated or capped
    attrs = _attach_attrs(repos, storage, heads, attrs)
    infos = _storage_infos(repos, dict.fromkeys(h["storage_name"] for h in heads))
    opening = (f'{{"generated_at":{json.dumps(utc_now_iso())},"truncated":{json.dumps(truncated)},'
               f'"capped":{json.dumps(capped)},"matched_count":{json.dumps(matched)},'
               f'"filters":{json.dumps(_echo(storage, path, q, flt), separators=(",", ":"))},"rows":[').encode()
    return StreamingResponse(_export_body(repos, base, heads, infos, opening, unreadable,
                                          extra=lambda h: attrs[h["target"]]),
                             media_type="application/json")


@router.get("/api/admin/usage/facets")
def usage_facets(request: Request, storage: str = Query(..., max_length=512, pattern=_NO_NUL)):
    """스토리지 탭 하나의 필터 선택지와 대상 수. 응답: {storage_name, computed_at, window_limit, window_truncated, target_count,
    scope:{inventory, recorded, outside, unknown}, clusters:[{cluster, target_count}], namespaces:[{cluster, namespace,
    target_count}], pvcs:[{cluster, namespace, pvc, storage_class, target_count}], storage_classes:[{storage_class,
    target_count}], users:[{dms_user, target_count}], requesters:[{requester_id, target_count}], lists_truncated:{…}}.

    PV 쪽은 머리 창(최근 스캔 순 _WINDOW_LIMIT -- 넘으면 window_truncated)을 판정해 센다(옵션마다 한 대상은 한 번 --
    usage_attribution.facet_counts). 목록·내보내기의 PV 필터와 **같은 창**이다(_select_heads -- 정렬과 무관) -- 그래서 창이 넘친
    스토리지에서도 옵션 수 = 그 옵션을 고른 목록이 창 안에서 찾는 대상 수다(포탈이 「최근 스캔된 N개 기준」이라고 말한다). requesters 는 스토리지 전체 기준(scan_requester_counts -- 창과 무관하게 정확하다). 목록마다
    상한 FACET_LIST_CAP(대상 수 내림차순 → 이름순), 넘으면 lists_truncated[이름] = true. 선택지는 지금 기록에 있는 값만이다(k8s
    인벤토리 전체가 아니다 -- 고르면 0건인 선택지를 내지 않는다)."""
    repos = request.app.state.repos
    heads = repos.data_jobs.scan_target_heads(storage=storage, order="desc", limit=_WINDOW_LIMIT + 1)
    window_truncated = len(heads) > _WINDOW_LIMIT
    heads = heads[:_WINDOW_LIMIT]
    inputs = collect(repos, storage, heads)
    facets = facet_counts([attribute(h["target"], inputs) for h in heads], cap=FACET_LIST_CAP)
    requesters, requesters_truncated = ranked(
        {r["requester_id"]: r["target_count"] for r in repos.data_jobs.scan_requester_counts(storage)},
        lambda k, n: {"requester_id": k, "target_count": n}, FACET_LIST_CAP)
    return {"storage_name": storage, "computed_at": utc_now_iso(), "window_limit": _WINDOW_LIMIT,
            "window_truncated": window_truncated, "target_count": len(heads), "scope": facets["scope"],
            "clusters": facets["clusters"], "namespaces": facets["namespaces"], "pvcs": facets["pvcs"],
            "storage_classes": facets["storage_classes"], "users": facets["users"], "requesters": requesters,
            "lists_truncated": {**facets["lists_truncated"], "requesters": requesters_truncated}}


@router.get("/api/admin/usage/scan-storages")
def scan_storages(request: Request):
    """성공 scan 기록이 있는 스토리지 이름(필터 선택지). 등록 스토리지 목록만으로는 삭제·개명된 스토리지 이름으로
    남은 이력을 좁힐 수 없다(리뷰) -- 화면이 등록 목록과 합친다. 2026-10-20 부터 포탈은 /storages 를 쓰고(실패하면 이것으로
    탭만 그린다), 모양(string[])은 옛 탭의 JS 호환으로 그대로 둔다."""
    return request.app.state.repos.data_jobs.scan_storage_names()


def _storage_summary_row(name: str, usage: "dict | None", *, registered: bool, source: "str | None",
                         backend_type, mount_path) -> dict:
    u = usage or {"target_count": 0, "succeeded_scan_count": 0, "last_scan_at": None}
    return {"storage_name": name, "registered": registered, "source": source, "backend_type": backend_type,
            "mount_path": mount_path, "target_count": u["target_count"],
            "succeeded_scan_count": u["succeeded_scan_count"], "last_scan_at": u["last_scan_at"]}


@router.get("/api/admin/usage/storages")
def usage_storages(request: Request):
    """사용량 분석의 스토리지 탭(2026-10-20 사용자 요청: 「각 스토리지 별로 처음부터 나뉘어져서 보이게끔」). 응답:
    {window_limit(_WINDOW_LIMIT -- 포탈이 「최근 스캔된 N개 안에서 정렬」의 N 을 하드코딩하지 않게), storages: [{storage_name,
    registered, source, backend_type, mount_path, target_count, succeeded_scan_count, last_scan_at}]}.

    - 대상 = 등록 스토리지 ∪ 성공 scan 기록이 있는 이름(삭제된 스토리지의 이력). 등록인데 기록이 없으면 0·0·null(0 은
      정상값이다 -- 탭이 숨지 않고 「0」으로 서야 첫 스캔이 끝날 때 탭이 갑자기 생기지 않는다).
    - 등록이면 지금 설정(source "registered"), 미등록이면 감사 기록의 마지막 값(StoragesRepository.last_known_config,
      source "last_known"), 그것도 없으면 null·source null(모름) -- 내보내기 행의 storage 와 같은 규칙(_storage_infos).
    - 순서: 등록 이름순 → 미등록 이름순(파이썬 문자열 순 -- 방언의 콜레이션과 무관하게, unregistered-storages 와 같은 순).
      최근 스캔순이 아닌 이유: 스캔이 끝날 때마다 탭이 움직인다.
    - 총 사용량은 여기 싣지 않는다 -- 지금 탭 하나만 GET /api/admin/usage/storage-totals 가 계산한다(2026-10-21: 최상위
      대상만, 목록 열과 같은 값 규칙, 지표마다 known/unknown). 여기서 전 스토리지를 합하면 페이지를 열 때마다 모든 스토리지의
      모든 대상 요약을 읽는다(GROUP BY 한 번 9ms → 1.5만 대상 ~2초, 캐시가 비면 아티팩트 수천 건). 잡 행 bytes_count 의 SQL
      합으로 대신하지 않는 이유도 그대로다: 타깃 경로가 겹쳐(team ⊃ team/sub) 이중 계산이고, 러너가 용량을 기록하기 전
      잡은 NULL 이라 조용히 빠진다(null≠0).
    - 실패 scan 만 남은 미등록 이름은 여기 없다(사용량 기준 = 성공 scan) -- 정리할 거리는 unregistered-storages 가 말한다.
    집계는 GROUP BY 한 번(DataJobsRepository.scan_storage_summary) + 미등록 이름마다 감사 기록 한 번."""
    repos = request.app.state.repos
    usage = {r["storage_name"]: r for r in repos.data_jobs.scan_storage_summary()}
    registered = sorted(repos.storages.list(), key=lambda s: s["storage_name"])
    names = {s["storage_name"] for s in registered}
    out = [_storage_summary_row(s["storage_name"], usage.get(s["storage_name"]), registered=True,
                                source="registered", backend_type=s["backend_type"], mount_path=s["mount_path"])
           for s in registered]
    for name in sorted(n for n in usage if n not in names):
        known = repos.storages.last_known_config(name)
        out.append(_storage_summary_row(name, usage[name], registered=False,
                                        source="last_known" if known is not None else None,
                                        backend_type=(known or {}).get("backend_type"),
                                        mount_path=(known or {}).get("mount_path")))
    return {"window_limit": _WINDOW_LIMIT, "storages": out}


@router.get("/api/admin/usage/storage-totals")
def storage_totals(request: Request, storage: str = Query(..., max_length=512, pattern=_NO_NUL),
                   flt: AttrFilter = Depends(_filter_params)):
    """스토리지 하나의 합계(2026-10-21 사용자 요청: 「사용량 분석에 스토리지별 사용량 및 정보들의 총합을 볼수있도록」).
    storage 는 필수이고 실린 값은 빈 문자열도 그 이름 하나다(scan-targets 와 같은 정확 일치 -- /storages 가 변조·구형 행의
    빈 이름도 탭으로 낸다). 이름을 경로(/storages/{name}/totals)가 아니라 쿼리로 받는 이유: 미등록 옛 이름은 지금의 이름
    규칙 밖 글자('/' 등)를 가질 수 있다(PurgeScanHistoryBody 와 같은 이유). 없는 이름은 404 가 아니라 빈 합계(0개·null) --
    scan-history 의 「빈 points = 정상」과 같다. 읽기 전용이라 require_admin(라우터)만 건다.

    규칙(값의 원천은 목록·내보내기와 한 벌):
    1. 대상 = 그 스토리지의 머리(scan_target_heads, 최근 스캔 순) 창 _WINDOW_LIMIT 개 -- 넘으면 truncated(「최근 스캔된
       1만 개 기준 부분 합」, 조용한 절단 금지). 창 밖 조상은 모르므로 그 하위가 창 안에서 최상위로 셀 수 있다(부분 합이다).
    2. 그중 **최상위**(조상 대상이 없는) 대상만 더한다(usage_metrics.maximal_targets -- 경로 성분 단위, 모양이 틀린 target 은
       invalid 로 세고 조상 판정에도 쓰지 않는다). 하위 대상이 더 새로 스캔됐어도 상위의 최신 scan 값을 쓴다 -- 상위 리포트
       안에서 하위가 차지하는 몫을 모르므로 섞을 수 없다(시점 범위 oldest/newest 를 함께 싣는다).
    3. 각 최상위 대상의 값 = 그 대상의 최신 성공 scan 하나. 실 사용량은 목록 열과 같은 값(metric_value(latest, "bytes") --
       리포트를 못 읽으면 러너가 남긴 bytes_count), 나머지는 리포트 요약(usage_metrics.StorageTotals -- 지표마다 sum·known·
       unknown, known 0 이면 sum null, hot 은 bytes 가중, 크기 분포는 가장 최근 리포트의 구간 서명과 같은 것만).
    4. 읽기 예산(_TOTALS_READ_BUDGET): 캐시에 없는 최상위 대상의 리포트는 한 호출에 그만큼만 연다(_read_and_cache --
       owner_uid fail-closed). 넘은 대상은 pending_count, 열었는데 못 읽은 대상은 unreadable_count(영구 캐시에 넣지 않는다 --
       일시 오류를 영구 모름으로 굳히지 않게), 둘 다 이번 응답에선 리포트 유래 값이 모름이다(실 사용량만 bytes_count 대체).
       못 읽은 잡은 프로세스 내 실패 기억(ReportReadFailures, TTL _FAILED_READ_TTL_SECONDS)에 둔다 -- TTL 안에선 열지 않고
       unreadable 로 세며 예산에서 뺀다(2026-10-21 리뷰: 기억이 없으면 같은 실패가 호출마다 예산 앞자리를 먼저 써서, 영구히
       못 읽는 최상위가 예산만큼 앞에 있으면 뒤의 읽을 수 있는 대상에 영영 닿지 못했다). 예산은 아직 열어 본 적 없는 잡이
       먼저, TTL 이 지난 옛 실패가 그 뒤다(재시도). progressed = 이번 호출이 다음 호출의 출발점을 바꿨는가(새로 읽어 캐시에
       넣었거나, 못 읽은 것을 실패 기억에 넣어 다음 호출이 그 뒤를 연다) -- 포탈은 pending > 0 && progressed 동안 다시
       부른다(진전이 없으면 멈춘다 -- 기억이 상한이라 기록하지 못한 실패는 진전으로 세지 않는다, 같은 실패를 1초마다 되풀이
       하지 않게). 못 읽은 최상위 대상을 그 하위 대상들로 대체하지 않는다 -- 하위가 상위를 덮는다는 보장이 없어 조용한 과소
       보고가 된다.
    5. filesystem = 그 스토리지가 Ready 마운트포인트인 노드의 statvfs(metrics_series.storage_filesystem -- 신선 우선 → 최신).
       미등록 이름은 null(지운 스토리지의 마지막 사용량은 보이되 df 는 지금 값이 아니다). 스토리지끼리 더하지 않는다 -- 여러
       스토리지가 한 파일시스템일 수 있다. 스캔 합계와 기준이 달라(스캔 안 한 경로·할당 블록·예약 블록) 비율을 내지 않는다.

    메모리는 _CHUNK 묶음 하나(요약 500개 -- 값을 더한 뒤 버린다)와 머리 행들이다. 결과 캐시는 두지 않는다(1만 대상 sqlite
    ~1.2초, 포탈 staleTime 30초) -- 필요해지면 (storage, 머리 job_id 집합) 키의 프로세스 내 사전이다(요약이 불변이라 키가
    같으면 값이 같다; pending·unreadable 이 0 인 결과만).

    필터 합계(2026-10-29): 요청자·PV 필터(_filter_params -- **path 는 받지 않는다**: 10-21 「합계는 경로 검색과 무관」)를 주면
    대상 = 필터에 맞는 머리(_select_heads, 최근 스캔 순 -- 목록과 같은 판정)이고 그 **걸러진 집합 안에서** 최상위만 더한다(필터 밖
    상위 경로가 있으면 그 하위가 최상위로 센다 -- 포탈 도움말이 말한다). truncated = SQL 경로는 맞는 대상이 창을 넘음, PV 경로는
    판정 창이 넘침. 예산·실패 기억·pending·progressed 규칙은 같다. 응답 끝에 filters(적용 필터 메아리)가 붙고 filesystem 은 null
    이다(부분 집합에 df 는 뜻이 없다); target_count 는 필터 뒤 수다. 필터가 없으면 요청·응답 바이트가 예전 그대로다(단 NUL 이 든
    storage 는 2026-10-29 부터 두 경우 모두 422 -- 예전엔 PG 500·sqlite 빈 합계였다, _NO_NUL)."""
    repos, settings = request.app.state.repos, request.app.state.settings
    if not flt.active:
        heads = repos.data_jobs.scan_target_heads(storage=storage, order="desc", limit=_WINDOW_LIMIT + 1)
        truncated = len(heads) > _WINDOW_LIMIT
        heads = heads[:_WINDOW_LIMIT]
        body = _totals(request, heads, truncated, storage=storage)
        filesystem = None
        if repos.storages.get(storage) is not None:
            filesystem = storage_filesystem(
                storage, repos.agents.list_nodes(stale_seconds=settings.agent_report_stale_seconds))
        return {**body, "filesystem": filesystem}
    heads, _, window_truncated, capped, _ = _select_heads(
        repos, base=_base(request), unreadable=set(), storage=storage, path=None, requester=flt.requester, pvf=flt.pv,
        order="desc", sort="last_scan", limit_rows=_WINDOW_LIMIT)
    truncated = window_truncated or capped
    return {**_totals(request, heads, truncated, storage=storage), "filesystem": None, "filters": flt.echo()}


def _totals(request: Request, heads: list, truncated: bool, *, storage: str) -> dict:
    """storage_totals 의 몸통(필터 없는 합계와 필터 합계가 함께 쓴다) -- 규칙은 storage_totals docstring 1~4. filesystem 은
    호출자가 붙인다(키 순서: 이 결과 뒤)."""
    repos = request.app.state.repos
    top, nested, invalid = maximal_targets(heads)
    base = _base(request)
    acc = StorageTotals()
    failures: ReportReadFailures = request.app.state.report_read_failures
    budget, pending, unreadable, read_ok, noted = _TOTALS_READ_BUDGET, 0, 0, 0, 0
    for i in range(0, len(top), _CHUNK):
        chunk = top[i:i + _CHUNK]
        digests = _cached_digests(repos, [h["job_id"] for h in chunk])
        missing = [h["job_id"] for h in chunk if h["job_id"] not in digests]
        recent, expired = failures.split(missing)
        unreadable += len(recent)                  # 방금 못 읽은 잡 -- 열지 않고 센다(예산 밖)
        untried = ([jid for jid in missing if jid not in recent and jid not in expired]
                   + [jid for jid in missing if jid in expired])
        take, later = untried[:budget], untried[budget:]
        budget -= len(take)
        pending += len(later)
        if take:
            fresh = _read_and_cache(repos, base, list(repos.data_jobs.jobs_by_ids(take).values()))
            read_ok += len(fresh)
            failed = [jid for jid in take if jid not in fresh]      # 그 사이 지워진 잡도 못 읽은 것이다
            unreadable += len(failed)
            noted += failures.record(failed)
            digests.update(fresh)
        for h in chunk:
            d = digests.get(h["job_id"])           # 못 읽음·지연 = None -> 실 사용량은 bytes_count(목록 열과 같은 대체)
            acc.add(h, metric_value({"latest": _point(h, d)}, "bytes"), d)
        del digests
    return {"storage_name": storage, "computed_at": utc_now_iso(), "truncated": truncated,
            "window_limit": _WINDOW_LIMIT, "target_count": len(heads), "summed_target_count": len(top),
            "nested_target_count": nested, "invalid_target_count": invalid, "unreadable_count": unreadable,
            "pending_count": pending, "progressed": read_ok > 0 or noted > 0, **acc.result()}


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
