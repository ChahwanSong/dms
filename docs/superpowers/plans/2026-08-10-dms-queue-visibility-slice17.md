# 슬라이스 17 — 큐 가시성 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 대시보드에 (A) Volcano 큐 현황(Queue state + 라이브 PodGroup 대기 표)과 (B) 전역 제출 대기 통계(`queue_wait_seconds` 파생 컬럼)를 낸다 — 슬라이스 14가 풀스캔을 이유로 미룬 (B)를 쓰기 시점 접기로 해소하고, 14가 붙인 거짓 「큐 대기」 라벨을 정정한다.

**Architecture:** (A)는 PodGroup이 코어다(설계 §2.1): 대기 중 잡·대기 시간은 살아 있는 PodGroup에만 있고 잡이 끝나면 삭제된다. `queue_reader.py`의 `VolcanoQueueReader`가 namespaced PodGroup **list**(spec.queue 필터)와 cluster-scoped Queue **이름 지정 GET**(state 하나만)을 읽고, `StubQueueReader` 페어가 클러스터 없는 로컬·CI를 살린다(기본 백엔드가 `stub`이므로 페어가 없으면 전부 500). RBAC은 기존 `dms-api` Role에 `podgroups: get,list` 한 줄 + 새 ClusterRole(`queues` GET, `resourceNames: ["dms-data"]`)이고, 처음으로 RBAC 계약 테스트를 만든다. (B)는 `set_job_state`의 기존 SELECT/UPDATE에 얹는 write-once 기록 + migrate 시 `state_transitions` one-shot 백필 + `(created_at, queue_wait_seconds)` 커버링 인덱스 — 기존 `created_at BETWEEN` 풀스캔 7개도 덤으로 레인지 스캔이 된다.

**Tech Stack:** Python 3.11 표준 라이브러리(FastAPI 라우트 1건 + kubernetes CustomObjectsApi 호출 2종), React 18 + Vitest(카드 1건 + 분포 1건 + 라벨 정정), k8s RBAC 매니페스트 1파일.

## Global Constraints

- 설계 문서: `docs/superpowers/specs/2026-08-10-dms-queue-visibility-slice17-design.md`. 충돌하면 설계가 이긴다.
- **세 상태를 절대 뭉개지 않는다**(설계 §4): 403(권한 없음)·404(CRD 부재/Volcano 미설치)·정상 빈 결과. `null` = 알 수 없음, `[]` = 비었음 — API 응답이 이 둘을 타입에서 구분한다. `[]`로 접으면 권한 누락이 "큐가 한가함"으로 렌더된다.
- **모든 새 k8s 호출에 `_request_timeout`을 명시**한다(설계 §1-8): urllib3 기본은 무제한이라 5초 폴링에서 무제한 대기는 스레드풀을 고갈시킨다. 기존 커스텀 오브젝트 호출(:290/:301/:316)은 안 넘긴다 — 새 호출은 롤아웃 경로처럼 `ROLLOUT_REQUEST_TIMEOUT_SECONDS`를 쓴다.
- **스텁 큐 리더 페어는 필수**다(설계 §2.5): 기본 실행 백엔드가 `stub`(`config.py:120,:178`)이라, 페어가 없으면 모든 로컬·CI 환경에서 `/api/admin/metrics/queue`가 500이고 `app.state` 주입 테스트 관례도 못 쓴다.
- **`resourceNames`는 `get`에만, `list`에는 절대 금지**: `resourceNames`는 `list`에 적용되지 않는다(10-rbac.yaml이 두 번 적어 둔 함정, ~:53-60/:115-119). Queue는 반드시 이름 지정 GET.
- **용량/사용률 게이지는 만들지 않는다**(설계 §2.2): `dms-data` 큐에 `capability`도 `deserved`도 없다(실측). weight 1을 사용률처럼 그리면 없는 사실을 지어내는 것이다.
- **`queue_wait_seconds`는 첫 비-Pending 엣지에서 write-once**(비터미널 재전이가 덮어쓰지 못하게), NULL 행은 집계에서 제외하고 **제외 건수를 표면화**한다(백필 공백을 화면에서 숨기지 않는다).
- **컬럼은 CREATE TABLE과 `_ensure_columns` 양쪽에** 넣는다 — 기배포 DB는 CREATE를 다시 안 탄다(슬라이스 14가 실 500으로 배운 교훈). 테이블 추가는 없다(`tests/test_migrations.py:173`이 `len(ALL_TABLES) == 20`을 고정).
- **「잡 통계」에 이미 있는 것을 중복하지 않는다**: 처리량·성공률·도구/스토리지/사용자별 분해·실패 사유·처리 항목/바이트는 `JobStatsSection.tsx`에 이미 있다.
- **새 pip/npm 의존성 금지.**
- `legacy/` 아래는 읽기 전용 — 수정·이동·삭제·추가 금지, import 금지, 코드 복사 금지.
- 백엔드 테스트: `.venv/bin/python -m pytest` (`python`은 PATH에 없다). 전체 스위트는 **포그라운드**로 Bash `timeout` 400000ms(기준선 **963 passed**). 백그라운드+Monitor 조합 금지.
- 프론트: `cd frontend && npx vitest run`(기준선 **206**), 타입체크 `npx tsc -b`.
- 주석은 한국어로 "왜"를 적는다.
- **origin으로 push 금지.** 커밋만 한다.

## 실측 고정값 (코드·클러스터 직접 확인)

| 항목 | 값 |
|---|---|
| PodGroup 수명 | **잡이 끝나면 삭제된다**(6일 된 Completed vcjob 20여 개, `kubectl get pg -A` 0건). 끝난 잡의 Volcano 대기는 소급 불가 — 이력은 §7의 후속 슬라이스 |
| PodGroup 좌표 | `podgroups.scheduling.volcano.sh/v1beta1` **namespaced**. 이름은 `<vcjob-name>-<vcjob-uid>`(관측일 뿐 문서화된 계약 아님), DMS 라벨 없음 → 이름 유도·라벨 셀렉터 불가, **네임스페이스 list 후 `spec.queue` 필터가 유일하게 안전한 경로** |
| PodGroup 필드 | `spec.minMember`, `spec.queue`, `metadata.creationTimestamp`, `status.phase`(Pending/Inqueue/Running/…), `status.conditions[]`(type/reason/message — **이번 슬라이스는 읽지 않는다**, 설계 §3의 표 열에 없고 §7이 문자열 분기를 금지) |
| Queue 좌표 | `queues.scheduling.volcano.sh/v1beta1` **cluster-scoped**(ClusterRole 필요). `dms-data` spec = `{dequeueStrategy, parent: root, reclaimable, weight: 1}` — capability/deserved 없음. `status`는 `state` + phase 카운터(`omitempty`라 키 부재=0) — 카운터는 PodGroup으로 유도되므로 **state만 읽는다**(설계 §2.1) |
| k8s 클라이언트 | `execution_volcano.py:259` `_VC`(batch.volcano.sh)가 유일한 커스텀 좌표. `list_*` 없음, cluster-scoped 호출 없음, :290/:301/:316은 `_request_timeout` 안 넘김. 롤아웃 메서드(:353,:357,:370,:373,:396)는 `ROLLOUT_REQUEST_TIMEOUT_SECONDS`(=10, :33)를 넘김. 404 판별은 `getattr(exc, "status", None)` 덕타이핑(:380 — .venv에 kubernetes 없음), 403 로그는 `_log_forbidden`(:385-390). 인스턴스 페이크 주입 관례: `tests/test_k8s_read_pod_log.py:25-29`(`c._custom = fake; c._ensure = lambda: None`) |
| RBAC | `deploy/k8s/10-rbac.yaml`: Role `dms-controller`(:16-64)·Role `dms-api`(:93-123) 둘 다 **`scheduling.volcano.sh` 전무** → 지금 어떤 큐/PodGroup 읽기도 런타임 403. `dms-api`는 `batch.volcano.sh/jobs: get,list,watch` 보유. 헤더(:1-3)가 "agent ClusterRole 빼고 전부 namespace-scoped"라 단언 — 새 ClusterRole은 명시적 예외. **RBAC를 붙잡는 테스트 0건**. 규칙 값은 전부 더블쿼트 flow 시퀀스(JSON 호환) |
| 라우트 패턴 | `routes_metrics.py:19` `APIRouter(dependencies=[Depends(require_admin)])` — 새 라우트는 자동 admin 게이트. `metrics_infra`(:80-156)가 병렬 k8s 읽기 템플릿: 동기 def + ThreadPoolExecutor + 항목별 try/except + `logger.warning`. 테스트는 `app.state.rollout_runner` 주입(`test_api_metrics.py:158-186` `_FakeObserver`) |
| 스텁 배선 | `wiring.py:10-12` `execution_backend != "volcano"` → 스텁. 기본 백엔드 `stub`. `rollout_runner.py:72-101` `StubRolloutRunner`가 미러링할 페어 모양. `app.py:38` `app.state.rollout_runner` 배선 관례 |
| data_jobs 시각 | 시각 컬럼은 `created_at`/`updated_at`/`preview_expires_at`뿐, `updated_at`은 매 전이마다 덮어씀. `runs` 테이블은 죽어 있음(읽기·쓰기 0). **`created_at` 인덱스 없음** → `repositories/metrics.py`의 `created_at BETWEEN` 집계 7개가 매 폴링 풀스캬 |
| `set_job_state` | `repositories/data_jobs.py:129-164` — 이미 SELECT(:133-134)+UPDATE(:145-148)+터미널 가드(:137-143). 기존 SELECT에 `created_at`을 얹고 기존 UPDATE에 값을 추가한다 — 추가 statement 0 |
| 시각 산술 | SQL로 이식 불가(julianday=SQLite, EXTRACT(EPOCH)=PG 전용, `metrics.py:98-99`) → 파이썬에서 뺀다. `_epoch`가 `repositories/metrics.py:17-19`와 `metrics_series.py:26-28`에 중복 — `db.py`로 승격해 한 벌로 만든다. 타임스탬프 해상도 1초(`db.py:12-13`). `Database`는 RLock 하나의 단일 커넥션 — 긴 스캔은 API 프로세스 전체를 멈춘다 |
| 마이그레이션 | `migrations.py` CREATE TABLE data_jobs :129-167, `_ensure_columns` 튜플 :384-400, "컬럼 보강 후 인덱스" 선례 `idx_requests_batch` :306-308. `migrate()`는 PG 어드바이저리 락으로 직렬화(:27-47) — 백필도 그 안에서 돈다 |
| 프론트 | `useMetrics.ts` 쿼리 키 `["metrics", ...]` + 5s 폴링. `Dashboard.tsx:111-112` `<NodeMetricsSection />` 다음 `<JobStatsSection />` — 큐 카드는 그 사이. `JobStatsSection.tsx:90-99` 처리량/수행시간 grid(md:grid-cols-2). 방어적 `asArray` 관례(:10). `types.ts` 규약: `\| null` = 서버가 명시적으로 null을 보냄. KPI 타일 「대기」는 24h 창 DB 집계 — 큐 카드는 무윈도 라이브 카운트라 라벨로 구분(「지금 큐에서 대기 중」) |
| 슬라이스 14 거짓 라벨 | `RequestDetail.tsx:138-141(주석),:156-157(라벨)` 「큐 대기」 = 요청 Pending→첫 비-Pending 전이(플래너 틱 지연). 테스트 `RequestDetail.test.tsx:289-320` 2건이 그 라벨을 고정 |
| 히스토그램 | `metrics_series.py:117-134` `DURATION_BUCKETS`(<1m 시작) + `duration_histogram(seconds)`. 제출 대기는 플래너 틱(10s)·스테퍼 틱(5s) 규모라 <1m에 전부 뭉친다 → 버킷 파라미터화 필요 |

## 파일 구조

| 파일 | 책임 |
|---|---|
| `src/dms/queue_reader.py` (신규) | `DMS_QUEUE` 상수 + `VolcanoQueueReader`(정규화) + `StubQueueReader`(결정적 페어) |
| `src/dms/execution_volcano.py` (수정) | `KubernetesClient.get_queue`/`list_podgroups` — scheduling.volcano.sh, `_request_timeout` 명시, 404→None |
| `src/dms/wiring.py` (수정) | `build_queue_reader(settings)` |
| `src/dms/api/app.py` (수정) | `app.state.queue_reader` 배선 |
| `src/dms/db.py` (수정) | `iso_epoch` 승격(중복 `_epoch` 두 벌 재지향) |
| `src/dms/api/routes_metrics.py` (수정) | `GET /api/admin/metrics/queue` + `metrics_jobs`에 제출 대기 분포 |
| `deploy/k8s/10-rbac.yaml` (수정) | `dms-api` Role에 podgroups get,list + ClusterRole/Binding `dms-api-queue-readonly` |
| `tests/test_rbac_contract.py` (신규) | RBAC 계약 테스트(설계 §1-10: 지금은 아무것도 이걸 안 붙잡는다) |
| `src/dms/migrations.py` (수정) | `queue_wait_seconds` 컬럼(양쪽) + `idx_data_jobs_created` + one-shot 백필 |
| `src/dms/repositories/data_jobs.py` (수정) | `set_job_state` write-once 기록 |
| `src/dms/repositories/metrics.py` (수정) | `job_stats`에 제출 대기 2쿼리(인덱스 커버) |
| `src/dms/metrics_series.py` (수정) | `duration_histogram` 버킷 파라미터화 + `SUBMIT_WAIT_BUCKETS` |
| `frontend/src/lib/types.ts` (수정) | `QueueMetrics`/`QueuePodgroup` + `JobMetrics` 제출 대기 3필드 |
| `frontend/src/features/dashboard/useMetrics.ts` (수정) | `useQueueMetrics`(5s 폴링) |
| `frontend/src/features/dashboard/QueueSection.tsx` (+test, 신규) | 큐 현황 카드(null≠[] 렌더 구분) |
| `frontend/src/features/dashboard/Dashboard.tsx` (+test, 수정) | 카드 배치(잡 통계 앞) |
| `frontend/src/features/dashboard/JobStatsSection.tsx` (+test, 수정) | 제출 대기 분포 + 집계/제외 건수 |
| `frontend/src/features/jobs/RequestDetail.tsx` (+test, 수정) | 「큐 대기」→「제출 대기」 라벨 정정 |

---

### Task 1: 큐 리더 + 스텁 페어 + KubernetesClient scheduling 호출

**Files:**
- Create: `src/dms/queue_reader.py`
- Create: `tests/test_queue_reader.py`
- Modify: `src/dms/execution_volcano.py` (KubernetesClient에 메서드 2개)
- Modify: `src/dms/wiring.py`, `tests/test_wiring_phase3c.py`

**Interfaces:**
- Consumes: `ROLLOUT_REQUEST_TIMEOUT_SECONDS`(`execution_volcano.py:33`), `KubernetesClient`의 `_custom`/`_ensure`/`_log_forbidden` 내부 관례, `Settings.execution_backend`/`k8s_namespace`.
- Produces (Task 3이 이 이름·모양을 그대로 쓴다):
  - `queue_reader.DMS_QUEUE = "dms-data"` (모듈 상수).
  - `VolcanoQueueReader(k8s, *, namespace, queue=DMS_QUEUE)` / `StubQueueReader()` — 공통 두 메서드:
    - `read_queue() -> dict | None` — `{"name": str, "state": str | None}` 또는 None(알 수 없음).
    - `read_podgroups() -> list[dict] | None` — 항목 `{"name": str, "phase": str | None, "min_member": int | None, "created_at": str | None}`, `[]` = 비었음, None = 알 수 없음. **wait_seconds는 여기 없다** — 라우트(Task 3)가 계산해 얹는다.
  - `KubernetesClient.get_queue(name) -> dict | None`, `KubernetesClient.list_podgroups(namespace) -> dict | None` — 404는 None으로 접고 403 등은 올린다.
  - `wiring.build_queue_reader(settings)` — stub 백엔드면 `StubQueueReader`.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_queue_reader.py` (신규 파일 전체):

```python
"""큐 리더(슬라이스 17 설계 §2.1·§4)의 계약.

핵심은 세 상태의 비뭉개짐이다: 403(예외 전파)·404/CRD 부재(None)·빈 목록([])이
각각 다른 결과로 나와야 한다. 여기서 한 번 접히면 어떤 상위 계층도 되살릴 수 없다.
KubernetesClient 쪽은 test_k8s_read_pod_log.py 와 같은 인스턴스 페이크 주입으로
kwargs(_request_timeout)와 좌표를 못박는다 -- .venv 에 kubernetes 가 없어 실제
ApiException 타입은 만들 수 없고, 클라이언트가 보는 것도 status 속성뿐이다."""
import pytest

from dms.execution_volcano import (KubernetesClient,
                                   ROLLOUT_REQUEST_TIMEOUT_SECONDS)
from dms.queue_reader import DMS_QUEUE, StubQueueReader, VolcanoQueueReader


class _FakeK8s:
    """리더가 쓰는 두 메서드만 가진 페어(K8sClient 와 같은 구조적 타이핑 관례)."""
    def __init__(self, queue=None, podgroups=None):
        self.queue = queue
        self.podgroups = podgroups

    def get_queue(self, name):
        self.requested = name
        return self.queue

    def list_podgroups(self, namespace):
        self.listed = namespace
        return self.podgroups


def test_read_queue_extracts_only_name_and_state():
    # Queue 에서 읽는 것은 state 하나다(설계 §2.1) -- phase 카운터는 omitempty
    # (키 부재=0)인 데다 PodGroup 으로 유도되므로 여기서 읽지 않는다.
    k8s = _FakeK8s(queue={"metadata": {"name": "dms-data"},
                          "status": {"state": "Open", "running": 2},
                          "spec": {"weight": 1}})
    reader = VolcanoQueueReader(k8s, namespace="dms")
    assert reader.read_queue() == {"name": "dms-data", "state": "Open"}
    assert k8s.requested == DMS_QUEUE


def test_read_queue_unknown_stays_none():
    assert VolcanoQueueReader(_FakeK8s(queue=None),
                              namespace="dms").read_queue() is None


def test_read_queue_without_status_has_null_state():
    # 막 생성된 큐는 status 가 없을 수 있다 -- state 만 null 로 강등, 죽지 않는다
    k8s = _FakeK8s(queue={"metadata": {"name": "dms-data"}})
    assert VolcanoQueueReader(k8s, namespace="dms").read_queue() == {
        "name": "dms-data", "state": None}


def test_read_podgroups_filters_by_queue_and_normalizes():
    # PodGroup 에는 DMS 라벨이 없고 이름 접미(-<uid>)는 문서화된 계약이 아니다 --
    # 네임스페이스 list 후 spec.queue 필터가 유일하게 안전한 경로다(설계 §2.1).
    k8s = _FakeK8s(podgroups={"items": [
        {"metadata": {"name": "job-a-uid1",
                      "creationTimestamp": "2026-08-10T00:00:00Z"},
         "spec": {"queue": "dms-data", "minMember": 3},
         "status": {"phase": "Pending"}},
        {"metadata": {"name": "other-uid2"},
         "spec": {"queue": "default", "minMember": 1},
         "status": {"phase": "Running"}},                # 다른 큐 -- 제외
        {"metadata": {"name": "job-c-uid3"},
         "spec": {"queue": "dms-data"}},                 # status/시각 없음 -- null 강등
    ]})
    reader = VolcanoQueueReader(k8s, namespace="dms")
    assert reader.read_podgroups() == [
        {"name": "job-a-uid1", "phase": "Pending", "min_member": 3,
         "created_at": "2026-08-10T00:00:00Z"},
        {"name": "job-c-uid3", "phase": None, "min_member": None,
         "created_at": None},
    ]
    assert k8s.listed == "dms"


def test_read_podgroups_distinguishes_absent_from_empty():
    # None(CRD 부재)과 [](빈 큐)는 다른 결과여야 한다(설계 §4).
    assert VolcanoQueueReader(_FakeK8s(podgroups=None),
                              namespace="dms").read_podgroups() is None
    assert VolcanoQueueReader(_FakeK8s(podgroups={"items": []}),
                              namespace="dms").read_podgroups() == []


def test_stub_pair_is_deterministic_without_cluster():
    # 기본 백엔드가 stub 이다(config.py) -- 이 페어가 없으면 모든 로컬·CI 에서
    # /api/admin/metrics/queue 가 500 이다(설계 §2.5).
    stub = StubQueueReader()
    assert stub.read_queue() == {"name": DMS_QUEUE, "state": "Open"}
    assert stub.read_podgroups() == []


# ---- KubernetesClient.get_queue / list_podgroups ----

class _FakeCustom:
    def __init__(self, *, fail_status=None, result=None):
        self.calls = []
        self._fail = fail_status
        self._result = result

    def _maybe_fail(self):
        if self._fail is not None:
            exc = RuntimeError("api error")
            exc.status = self._fail   # ApiException 덕타이핑(get_workload:380 관례)
            raise exc

    def get_cluster_custom_object(self, group, version, plural, name, **kw):
        self.calls.append((group, version, plural, name, kw))
        self._maybe_fail()
        return self._result

    def list_namespaced_custom_object(self, group, version, namespace, plural, **kw):
        self.calls.append((group, version, namespace, plural, kw))
        self._maybe_fail()
        return self._result


def _k8s(custom):
    c = KubernetesClient("dms")
    c._custom = custom
    c._ensure = lambda: None          # in-cluster config 로드를 건너뛴다
    return c


def test_get_queue_passes_request_timeout_and_coordinates():
    custom = _FakeCustom(result={"status": {"state": "Open"}})
    assert _k8s(custom).get_queue("dms-data") == {"status": {"state": "Open"}}
    group, version, plural, name, kw = custom.calls[0]
    assert (group, version, plural, name) == (
        "scheduling.volcano.sh", "v1beta1", "queues", "dms-data")
    # urllib3 기본은 무제한 -- 이 kwarg 가 빠지면 apiserver 멈춤이 5초 폴링의
    # 스레드풀을 고갈시킨다(설계 §1-8). _preload_content 를 못박은
    # test_k8s_read_pod_log 와 같은 방식으로 kwarg 자체를 고정한다.
    assert kw.get("_request_timeout") == ROLLOUT_REQUEST_TIMEOUT_SECONDS


def test_list_podgroups_passes_request_timeout_and_coordinates():
    custom = _FakeCustom(result={"items": []})
    assert _k8s(custom).list_podgroups("dms") == {"items": []}
    group, version, namespace, plural, kw = custom.calls[0]
    assert (group, version, namespace, plural) == (
        "scheduling.volcano.sh", "v1beta1", "dms", "podgroups")
    assert kw.get("_request_timeout") == ROLLOUT_REQUEST_TIMEOUT_SECONDS


def test_404_folds_to_none_but_403_raises():
    # 404 = CRD/오브젝트 부재 -> None(화면 "알 수 없음"). 403 은 올려서 라우트가
    # 로그와 함께 그 축만 강등한다 -- 어느 쪽도 빈 결과로 접히면 안 된다(설계 §4).
    assert _k8s(_FakeCustom(fail_status=404)).get_queue("dms-data") is None
    assert _k8s(_FakeCustom(fail_status=404)).list_podgroups("dms") is None
    with pytest.raises(RuntimeError):
        _k8s(_FakeCustom(fail_status=403)).get_queue("dms-data")
    with pytest.raises(RuntimeError):
        _k8s(_FakeCustom(fail_status=403)).list_podgroups("dms")
```

`tests/test_wiring_phase3c.py` — import 두 줄을 추가·확장하고:

```python
from dms.queue_reader import StubQueueReader, VolcanoQueueReader
from dms.wiring import (build_build_runner, build_execution_adapter,
                        build_identity_resolver, build_queue_reader,
                        build_rollout_runner)
```

파일 끝에 테스트 2건 추가:

```python
def test_queue_reader_is_stub_when_backend_is_not_volcano():
    # 기본 백엔드(stub)에서 스텁 페어가 안 꽂히면 conftest 의 create_app 경로
    # 전부가 /api/admin/metrics/queue 에서 500 이다(설계 §2.5).
    settings = Settings.from_env(BASE)
    assert isinstance(build_queue_reader(settings), StubQueueReader)


def test_queue_reader_builds_volcano_reader_when_volcano():
    settings = Settings.from_env({**BASE, "DMS_EXECUTION_BACKEND": "volcano",
                                  "DMS_JOB_IMAGE": "reg/img:1"})
    reader = build_queue_reader(settings)
    assert isinstance(reader, VolcanoQueueReader)
    assert reader._namespace == settings.k8s_namespace
    assert reader._queue == "dms-data"
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_queue_reader.py tests/test_wiring_phase3c.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'dms.queue_reader'` (wiring은 `ImportError: cannot import name 'build_queue_reader'`)

- [ ] **Step 3: queue_reader.py를 구현한다**

`src/dms/queue_reader.py` (전체):

```python
"""Volcano 큐 가시성 리더(슬라이스 17 설계 §2.1). 세 상태를 절대 뭉개지 않는다:
None = 알 수 없음(403/CRD 부재), [] = 정말 비었음(설계 §4). 여기서 한 번 접히면
어떤 상위 계층도 되살릴 수 없으므로 리더가 이 구분의 최초 권위다."""

# 읽는 큐 이름. 설정으로 빼지 않는다: RBAC(10-rbac.yaml)의 ClusterRole 이
# resourceNames=["dms-data"] 로 이 이름만 GET 을 허용하므로, 설정으로 다른 이름을
# 넣으면 RBAC 와 어긋나 조용히 403(=화면 "알 수 없음")이 된다. policies.queue 의
# 기본값과 같은 값이다 -- execution_manifests 가 잡을 제출하는 그 큐.
DMS_QUEUE = "dms-data"


class VolcanoQueueReader:
    """k8s 에서 Queue.state 와 살아 있는 PodGroup 을 읽는다.

    - read_queue: 이름 지정 GET 하나. resourceNames 는 list 에 적용되지 않는다
      (이 저장소가 두 번 적어 둔 함정) -- Queue 는 반드시 GET 이어야 한다.
      Queue 에서 읽는 것은 state(Open/Closed) 하나다: phase 카운터는 omitempty
      (키 부재=0)인 데다 PodGroup 을 세면 유도되므로 읽지 않는다(설계 §2.1).
    - read_podgroups: 네임스페이스 list 후 spec.queue 필터. PodGroup 이름
      규칙(<vcjob>-<uid>)은 문서화된 계약이 아니고 DMS 라벨도 없어 이름 유도/라벨
      셀렉터가 불가능하다 -- 목록+필터가 유일하게 안전한 경로다.

    404 는 k8s 클라이언트가 None 으로 접어 준다(CRD 부재=Volcano 미설치, 또는 큐
    오브젝트 부재 -- 어느 쪽도 "빈 큐"가 아니다). 403 등 그 외 예외는 그대로
    올라간다 -- 라우트가 잡아 그 축만 null 강등 + 로그를 남긴다."""

    def __init__(self, k8s, *, namespace, queue=DMS_QUEUE):
        self._k8s = k8s
        self._namespace = namespace
        self._queue = queue

    def read_queue(self):
        obj = self._k8s.get_queue(self._queue)
        if obj is None:
            return None
        return {"name": self._queue,
                "state": (obj.get("status") or {}).get("state")}

    def read_podgroups(self):
        objs = self._k8s.list_podgroups(self._namespace)
        if objs is None:
            return None
        out = []
        for item in (objs.get("items") or []):
            spec = item.get("spec") or {}
            if spec.get("queue") != self._queue:
                continue
            meta = item.get("metadata") or {}
            out.append({
                "name": meta.get("name") or "",
                "phase": (item.get("status") or {}).get("phase"),
                "min_member": spec.get("minMember"),
                "created_at": meta.get("creationTimestamp"),
            })
        return out


class StubQueueReader:
    """클러스터가 없을 때(execution_backend != "volcano") 쓰는 결정적 페어
    (StubRolloutRunner 와 같은 역할). 기본 백엔드가 stub 이라 이 페어가 없으면
    모든 로컬·CI 환경에서 /api/admin/metrics/queue 가 500 이고, app.state 주입
    기반 테스트 관례도 못 쓴다(설계 §2.5). "열린 빈 큐"가 스텁의 정직한 모양이다
    -- 스텁 백엔드는 아무것도 큐에 넣지 않는다."""

    def read_queue(self):
        return {"name": DMS_QUEUE, "state": "Open"}

    def read_podgroups(self):
        return []
```

- [ ] **Step 4: KubernetesClient에 메서드 2개를 추가한다**

`src/dms/execution_volcano.py` — `list_pod_briefs`(:392) 메서드 **아래**(클래스 끝)에 추가:

```python
    # 슬라이스 17(큐 가시성): scheduling.volcano.sh 는 잡 제출용 _VC(batch.volcano.sh)
    # 와 다른 그룹이다. queues 는 cluster-scoped, podgroups 는 namespaced.
    _SCHED = {"group": "scheduling.volcano.sh", "version": "v1beta1"}

    def get_queue(self, name):
        """이름 지정 Queue GET. 404 는 None 으로 접는다: CRD 부재(Volcano 미설치)
        또는 큐 오브젝트 부재 -- 어느 쪽도 "빈 큐"가 아니라 "알 수 없음"이다(설계
        §4). _request_timeout 필수: urllib3 기본은 무제한이라 5초 폴링 라우트가
        apiserver 멈춤에 스레드풀째 매달린다(설계 §1-8)."""
        self._ensure()
        try:
            return self._custom.get_cluster_custom_object(
                self._SCHED["group"], self._SCHED["version"], "queues", name,
                _request_timeout=ROLLOUT_REQUEST_TIMEOUT_SECONDS)
        except Exception as exc:
            if getattr(exc, "status", None) == 404:
                return None
            self._log_forbidden("get", "Queue", name, "-", exc)
            raise

    def list_podgroups(self, namespace):
        """네임스페이스 PodGroup list(필터는 리더의 몫 -- PodGroup 엔 DMS 라벨이
        없어 셀렉터를 못 쓴다). 404 접기·타임아웃은 get_queue 와 같은 이유."""
        self._ensure()
        try:
            return self._custom.list_namespaced_custom_object(
                self._SCHED["group"], self._SCHED["version"], namespace,
                "podgroups", _request_timeout=ROLLOUT_REQUEST_TIMEOUT_SECONDS)
        except Exception as exc:
            if getattr(exc, "status", None) == 404:
                return None
            self._log_forbidden("list", "PodGroup", "*", namespace, exc)
            raise
```

(`_log_forbidden`의 인자 순서는 `(verb, kind, name, namespace, exc)`다 — get_queue는 cluster-scoped라 namespace 자리에 `"-"`를 넣는다.)

- [ ] **Step 5: wiring에 build_queue_reader를 추가한다**

`src/dms/wiring.py` — `build_rollout_runner` 아래에 추가:

```python
def build_queue_reader(settings):
    # StubRolloutRunner 와 같은 선택 규칙(설계 §2.5): 기본 백엔드(stub)에서 스텁
    # 페어가 없으면 /api/admin/metrics/queue 가 모든 로컬·CI 에서 500 이다.
    if settings.execution_backend != "volcano":
        from .queue_reader import StubQueueReader
        return StubQueueReader()
    from .execution_volcano import KubernetesClient
    from .queue_reader import VolcanoQueueReader
    return VolcanoQueueReader(KubernetesClient(settings.k8s_namespace),
                              namespace=settings.k8s_namespace)
```

- [ ] **Step 6: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_queue_reader.py tests/test_wiring_phase3c.py tests/test_execution_volcano.py -q`
Expected: 전부 PASS (기존 execution_volcano 테스트는 `_VC` 경로만 만져 무영향)

- [ ] **Step 7: 커밋**

```bash
git add src/dms/queue_reader.py tests/test_queue_reader.py src/dms/execution_volcano.py src/dms/wiring.py tests/test_wiring_phase3c.py
git commit -m "feat(queue): Volcano 큐 리더 + 스텁 페어 — scheduling.volcano.sh 읽기(타임아웃 명시, 404≠403≠빈 결과)"
```

---

### Task 2: RBAC — podgroups 읽기 + 이름 지정 Queue GET + 계약 테스트

**Files:**
- Modify: `deploy/k8s/10-rbac.yaml`
- Create: `tests/test_rbac_contract.py`

**Interfaces:**
- Consumes: `dms.manifest_tags.workload_doc(path, kind, name)` — 슬라이스 16에서 승격된 부분집합 YAML 파서(kind/metadata.name 매칭은 Role/ClusterRole/ClusterRoleBinding에도 그대로 동작한다). rules 파싱은 이 테스트 전용 헬퍼로 짠다 — 10-rbac.yaml의 규칙 값은 전부 더블쿼트 flow 시퀀스라 `json.loads`가 그대로 받는다.
- Produces: Role `dms-api`에 `scheduling.volcano.sh/podgroups: get,list`(resourceNames 없음), ClusterRole+ClusterRoleBinding `dms-api-queue-readonly`(`queues` GET, `resourceNames: ["dms-data"]`, SA `dms-api`에 바인딩). 매니페스트 이름들은 실증(플랜 이후 절)의 `kubectl apply` 대상이다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_rbac_contract.py` (신규 파일 전체):

```python
"""10-rbac.yaml 계약 테스트(슬라이스 17 설계 §5). RBAC 를 붙잡는 테스트가 지금까지
0건이라 규칙을 잘못 써도 배포 전까지 아무 신호가 없었다(설계 §1-10) -- 그리고 큐
읽기의 실패 모드는 유난히 조용하다: 403 은 화면에서 "알 수 없음"일 뿐 아무것도
죽지 않는다.

두 함정을 못박는다:
- resourceNames 는 list 에 적용되지 않는다(10-rbac.yaml 이 두 번 적어 둔 함정) --
  podgroups list 규칙에 붙이면 모든 list 가 조용히 403 이 되고, Queue 를 list 로
  열면 resourceNames 가 무력화되어 클러스터의 모든 큐가 열린다.
- ClusterRole 은 바인딩 없이 무효다 -- Binding 까지 함께 계약이다.

문서 분리·kind/name 매칭은 manifest_tags 의 승격 파서를 쓰고, rules 파싱만 이
파일 전용이다: 규칙 값이 전부 더블쿼트 flow 시퀀스(JSON 호환)라 json.loads 로
충분하다."""
import json
from pathlib import Path

from dms.manifest_tags import workload_doc

RBAC = Path(__file__).resolve().parent.parent / "deploy" / "k8s" / "10-rbac.yaml"


def _indent(line):
    return len(line) - len(line.lstrip(" "))


def _top_block(doc, key):
    """최상위 key 의 몸통(더 깊게 들여쓴 연속 구간). 없으면 시끄럽게 실패한다."""
    at = next((i for i, line in enumerate(doc)
               if _indent(line) == 0 and line.strip().startswith(f"{key}:")), None)
    assert at is not None, f"{key}: 최상위 키가 없다"
    body = []
    for line in doc[at + 1:]:
        if _indent(line) == 0:
            break
        body.append(line)
    return body


def _rules(doc):
    """rules: 블록 -> [{apiGroups, resources, resourceNames?, verbs}]."""
    rules = []
    for line in _top_block(doc, "rules"):
        stripped = line.strip()
        if stripped.startswith("- "):
            rules.append({})
            stripped = stripped[2:]
        key, _, value = stripped.partition(":")
        rules[-1][key.strip()] = json.loads(value.strip())
    return rules


def _find_rule(rules, api_group, resource):
    matched = [r for r in rules if api_group in r.get("apiGroups", [])
               and resource in r.get("resources", [])]
    assert len(matched) == 1, (api_group, resource, matched)
    return matched[0]


def test_api_role_lists_podgroups_without_resource_names():
    doc = workload_doc(RBAC, "Role", "dms-api")
    assert doc is not None, "Role dms-api 문서가 없다"
    rule = _find_rule(_rules(doc), "scheduling.volcano.sh", "podgroups")
    assert set(rule["verbs"]) == {"get", "list"}
    # resourceNames 는 list 에 적용되지 않는다 -- 여기 붙는 순간 모든 list 가
    # 조용히 403 이 되어 화면이 영구 "알 수 없음"이 된다.
    assert "resourceNames" not in rule


def test_queue_clusterrole_is_named_get_only():
    doc = workload_doc(RBAC, "ClusterRole", "dms-api-queue-readonly")
    assert doc is not None, "ClusterRole dms-api-queue-readonly 문서가 없다"
    rule = _find_rule(_rules(doc), "scheduling.volcano.sh", "queues")
    # 이름 지정 GET 만(설계 §2.1 최소 표면): list 를 열면 resourceNames 가
    # 무력화되어 클러스터의 모든 큐가 열린다.
    assert rule["verbs"] == ["get"]
    assert rule["resourceNames"] == ["dms-data"]


def test_queue_clusterrole_bound_to_api_service_account():
    doc = workload_doc(RBAC, "ClusterRoleBinding", "dms-api-queue-readonly")
    assert doc is not None, "ClusterRoleBinding 이 없다 -- ClusterRole 은 바인딩 없이 무효다"
    role_ref = [line.strip() for line in _top_block(doc, "roleRef")]
    assert "kind: ClusterRole" in role_ref
    assert "name: dms-api-queue-readonly" in role_ref
    subjects = [line.strip() for line in _top_block(doc, "subjects")]
    assert "- kind: ServiceAccount" in subjects
    assert "name: dms-api" in subjects
    assert "namespace: dms" in subjects


def test_controller_role_untouched_by_queue_visibility():
    # 큐를 읽는 소비자는 api 라우트 하나뿐이다 -- controller 에 권한이 새면 최소
    # 표면 원칙이 깨진다. "실수로 양쪽에 붙이는" 리뷰 누락을 여기서 잡는다.
    doc = workload_doc(RBAC, "Role", "dms-controller")
    assert doc is not None
    grants = [r for r in _rules(doc)
              if "scheduling.volcano.sh" in r.get("apiGroups", [])]
    assert grants == []
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_rbac_contract.py -q`
Expected: FAIL — podgroups 규칙 테스트가 `assert len(matched) == 1`(0건), ClusterRole/Binding 테스트가 `doc is not None` 단언에서. controller 테스트만 이미 통과(현행도 grants 없음 — 나머지 3건의 RED가 계약을 증명한다).

- [ ] **Step 3: 10-rbac.yaml을 고친다**

**(1)** 파일 머리(:1-3)의

```yaml
# ServiceAccounts + namespaced RBAC for the three DMS workloads. All
# resources this file grants are namespace-scoped (dms) except the agent's
# ClusterRole (Nodes are cluster-scoped).
```

를 다음으로 교체(기존 영문 문장의 수정이라 영문 유지 — 예외 목록이 둘이 된다):

```yaml
# ServiceAccounts + namespaced RBAC for the three DMS workloads. All
# resources this file grants are namespace-scoped (dms) except two
# ClusterRoles: the agent's (Nodes are cluster-scoped) and the api's
# dms-api-queue-readonly (Volcano Queues are cluster-scoped; named GET only).
```

**(2)** Role `dms-api`의 마지막 규칙(`apps` 규칙, `verbs: ["get"]`로 끝) **뒤**, `---` 앞에 추가:

```yaml
  # 슬라이스 17(큐 가시성): /api/admin/metrics/queue 가 라이브 PodGroup 을 읽는다
  # (VolcanoQueueReader.read_podgroups -> list_namespaced_custom_object).
  # PodGroup 엔 DMS 라벨이 없고 이름 접미(-<uid>)는 문서화된 계약이 아니라,
  # 리더는 네임스페이스를 list 한 뒤 spec.queue 로 거른다 -- 그래서 list 가
  # 실제로 필요한 동사다. resourceNames 를 여기 붙이지 말 것: resourceNames 는
  # list 에 적용되지 않아(위에 두 번 적힌 함정) 모든 list 가 조용히 403 이 된다.
  - apiGroups: ["scheduling.volcano.sh"]
    resources: ["podgroups"]
    verbs: ["get", "list"]
```

**(3)** `dms-api` RoleBinding(`---` 다음이 dms-agent ServiceAccount 주석인 지점) 뒤에 ClusterRole+Binding을 삽입:

```yaml
---
# 슬라이스 17: Volcano Queue 는 cluster-scoped 다 -- 이 파일에서 api 가 갖는
# 유일한 클러스터 권한(머리 주석의 두 번째 예외). api 가 Queue 에서 읽는 것은
# status.state(Open/Closed) 하나뿐이라("잡이 왜 하나도 안 도는가"의 1순위 원인,
# 설계 §2.1) 이름 지정 GET 으로 최소 표면만 연다. verbs 는 ["get"] 을 유지할 것:
# resourceNames 는 list 에 적용되지 않으므로 list 를 열면 이 이름 제한이
# 무력화되어 클러스터의 모든 큐가 열린다. tests/test_rbac_contract.py 가 이
# 모양을 계약으로 고정한다.
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: dms-api-queue-readonly
rules:
  - apiGroups: ["scheduling.volcano.sh"]
    resources: ["queues"]
    resourceNames: ["dms-data"]
    verbs: ["get"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: dms-api-queue-readonly
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: dms-api-queue-readonly
subjects:
  - kind: ServiceAccount
    name: dms-api
    namespace: dms
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_rbac_contract.py tests/test_release_manifest_contract.py -q`
Expected: 전부 PASS (release 계약 테스트는 40/41/50만 파싱 — 10-rbac 무관, 파서 회귀 확인용)

- [ ] **Step 5: 커밋**

```bash
git add deploy/k8s/10-rbac.yaml tests/test_rbac_contract.py
git commit -m "feat(rbac): 큐 가시성 최소 권한 — podgroups list + 이름 지정 Queue GET + 첫 RBAC 계약 테스트"
```

---

### Task 3: GET /api/admin/metrics/queue 라우트 + iso_epoch 승격

**Files:**
- Modify: `src/dms/db.py` (`iso_epoch` 승격), `src/dms/repositories/metrics.py`·`src/dms/metrics_series.py` (중복 `_epoch` 재지향)
- Modify: `src/dms/api/routes_metrics.py`, `src/dms/api/app.py`
- Modify: `tests/test_api_metrics.py`

**Interfaces:**
- Consumes (Task 1): `app.state.queue_reader`의 `read_queue()`/`read_podgroups()` — 반환 모양은 Task 1 Produces 그대로. 403 등 예외는 리더가 올린다(라우트가 잡아 그 축만 null).
- Produces:
  - `db.iso_epoch(ts: str) -> float` — ISO-8601 UTC(...Z) → epoch 초. Task 5(write-once·백필)가 이 이름을 그대로 쓴다.
  - `GET /api/admin/metrics/queue` → `{"queue": {"name", "state"} | null, "podgroups": [{"name", "phase", "min_member", "created_at", "wait_seconds"}] | null}` — 항목은 대기 오래된 순, `wait_seconds`는 서버 계산(int, 시각 깨지면 null). Task 4 프론트가 이 키를 그대로 쓴다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_api_metrics.py` 파일 끝에 추가:

```python
class _FakeQueueReader:
    """StubQueueReader(queue_reader.py)와 같은 두 메서드 페어. 축별로 값/예외를
    주입해 403(예외)·404/CRD 부재(None)·빈 목록([])·정상이 각각 다른 응답으로
    나오는지 -- 뭉개짐 금지(설계 §4) -- 를 고정한다."""
    _UNSET = object()

    def __init__(self, queue=_UNSET, podgroups=_UNSET):
        self._queue = ({"name": "dms-data", "state": "Open"}
                       if queue is self._UNSET else queue)
        self._podgroups = [] if podgroups is self._UNSET else podgroups

    def _resolve(self, value):
        if isinstance(value, Exception):
            raise value
        return value

    def read_queue(self):
        return self._resolve(self._queue)

    def read_podgroups(self):
        return self._resolve(self._podgroups)


def test_metrics_queue_stub_pair_serves_without_cluster(client):
    # 주입 없이 그대로 -- conftest 기본 백엔드(stub)의 wiring 이 StubQueueReader 를
    # 꽂는다. 이 스텁 페어가 없으면 모든 로컬·CI 가 여기서 500 이다(설계 §2.5).
    body = client.get("/api/admin/metrics/queue", headers=ADMIN).json()
    assert body == {"queue": {"name": "dms-data", "state": "Open"},
                    "podgroups": []}


def test_metrics_queue_admin_only(client):
    assert client.get("/api/admin/metrics/queue").status_code == 401


def test_metrics_queue_computes_wait_and_sorts_longest_first(client):
    now = utc_now_iso()
    client.app.state.queue_reader = _FakeQueueReader(podgroups=[
        {"name": "dms-b-uid2", "phase": "Inqueue", "min_member": 1,
         "created_at": iso_plus(now, -30)},
        {"name": "dms-a-uid1", "phase": "Pending", "min_member": 3,
         "created_at": iso_plus(now, -300)},
        {"name": "dms-c-uid3", "phase": "Pending", "min_member": 1,
         "created_at": None},                    # 시각 없음 -- null 강등
    ])
    body = client.get("/api/admin/metrics/queue", headers=ADMIN).json()
    pods = body["podgroups"]
    # 오래 기다린 잡이 먼저 -- 표의 목적이 "무엇이 막혀 있나"다(설계 §3)
    assert [p["name"] for p in pods] == ["dms-a-uid1", "dms-b-uid2", "dms-c-uid3"]
    assert 300 <= pods[0]["wait_seconds"] <= 302   # 1초 해상도 + 호출 지연 여유
    assert pods[0]["min_member"] == 3
    assert pods[2]["wait_seconds"] is None


def test_metrics_queue_unknown_axes_stay_null_not_empty(client):
    # 403(리더 예외)과 CRD 부재(None)는 "빈 큐"가 아니다 -- []로 접으면 권한
    # 누락이 "큐가 한가함"으로 렌더된다(설계 §4). 축 강등이지 라우트 실패가
    # 아니므로 응답은 200 이다.
    client.app.state.queue_reader = _FakeQueueReader(
        queue=RuntimeError("forbidden 403"), podgroups=None)
    r = client.get("/api/admin/metrics/queue", headers=ADMIN)
    assert r.status_code == 200
    assert r.json() == {"queue": None, "podgroups": None}


def test_metrics_queue_empty_list_is_not_null(client):
    # 반대 방향도 고정: 정말 빈 큐([])가 null 로 승격되면 "알 수 없음" 경고가
    # 정상 상태에 뜬다.
    client.app.state.queue_reader = _FakeQueueReader(queue=None, podgroups=[])
    assert client.get("/api/admin/metrics/queue", headers=ADMIN).json() == {
        "queue": None, "podgroups": []}
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_api_metrics.py -q`
Expected: FAIL — 새 5건이 404(라우트 부재). 기존 테스트는 PASS 유지.

- [ ] **Step 3: iso_epoch을 db.py로 승격한다**

**(1)** `src/dms/db.py` — `iso_plus` 아래에 추가:

```python
def iso_epoch(ts: str) -> float:
    """ISO-8601 UTC(...Z) 문자열 -> epoch 초. 시각의 차는 SQL 로 이식성 있게 못
    뺀다(julianday 는 SQLite, EXTRACT(EPOCH)는 PG 전용) -- 전부 파이썬에서 뺀다.
    metrics_series/repositories.metrics 의 사본 _epoch 두 벌을 여기로 승격했다."""
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc).timestamp()
```

**(2)** `src/dms/metrics_series.py` — `_epoch` 정의(:26-28)와 `from datetime import datetime, timezone` import를 삭제하고, 대신:

```python
# 사본이던 _epoch 를 db.iso_epoch 로 승격(슬라이스 17) -- 별칭 유지로 호출부 무변경.
from .db import iso_epoch as _epoch
```

**(3)** `src/dms/repositories/metrics.py` — `_epoch` 정의(:17-19)와 `from datetime import datetime, timezone` import를 삭제하고, `from ..db import Database, load_json` 아래에:

```python
# metrics_series 와 같은 승격(슬라이스 17) -- 별칭 유지로 호출부 무변경.
from ..db import iso_epoch as _epoch
```

Run: `.venv/bin/python -m pytest tests/test_metrics_series.py tests/test_repo_metrics.py tests/test_db.py -q`
Expected: 전부 PASS (별칭이라 동작 동일)

- [ ] **Step 4: 라우트와 배선을 구현한다**

**(1)** `src/dms/api/routes_metrics.py` — import 한 줄 교체:

```python
from ..db import iso_epoch, iso_plus, utc_now_iso
```

**(2)** 파일 끝에 라우트 추가:

```python
@router.get("/api/admin/metrics/queue")
def metrics_queue(request: Request):
    """Volcano 큐 현황(슬라이스 17 설계 §3). 축마다 독립 fail-soft: queue(이름
    지정 GET)와 podgroups(네임스페이스 list)는 필요한 권한이 다르므로 --
    ClusterRole 누락은 queue 만, Role 누락은 podgroups 만 403 -- 한쪽이 죽어도
    다른 쪽은 산다. null = 알 수 없음(403/CRD 부재), [] = 정말 비었음. 이 구분을
    접으면 권한 누락이 "큐가 한가함"으로 렌더된다(설계 §4).

    metrics_infra 와 같은 이유로 동기 def + ThreadPoolExecutor 병렬이다: 각 k8s
    호출엔 _request_timeout(10s)이 걸려 있지만 순차면 최악 2x10초가 5초 폴링에
    쌓여 threadpool 을 고갈시킨다."""
    reader = request.app.state.queue_reader
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = {"queue": pool.submit(reader.read_queue),
                   "podgroups": pool.submit(reader.read_podgroups)}
    results = {}
    for axis, future in futures.items():
        try:
            results[axis] = future.result()
        except Exception as exc:
            # 403 등 리더가 올린 예외는 그 축만 null(알 수 없음) 강등. 로그가
            # 없으면 RBAC 누락이 화면의 "알 수 없음"으로만 보여 원인 추적이
            # 안 된다(read_pod_log 403 사고의 교훈).
            logger.warning("metrics/queue read failed axis=%s: %s", axis, exc)
            results[axis] = None
    pods = results["podgroups"]
    if pods is not None:
        now = iso_epoch(utc_now_iso())
        for pg in pods:
            # 대기 시간(now - creationTimestamp)은 서버가 계산한다 -- 브라우저
            # 시계 스큐가 대기 시간을 왜곡하지 않게. 시각이 깨진 항목만 null.
            try:
                pg["wait_seconds"] = max(0, int(now - iso_epoch(pg["created_at"])))
            except (TypeError, ValueError):
                pg["wait_seconds"] = None
        # 오래 기다린 잡이 위로 -- 표의 목적이 "무엇이 막혀 있나"이므로.
        pods.sort(key=lambda p: -(p["wait_seconds"] or 0))
    return {"queue": results["queue"], "podgroups": pods}
```

**(3)** `src/dms/api/app.py` — import를 확장하고:

```python
from ..wiring import (build_build_runner, build_execution_adapter,
                     build_identity_resolver, build_queue_reader,
                     build_rollout_runner)
```

`app.state.rollout_runner = ...` 줄 아래에 추가:

```python
    # 슬라이스 17: /api/admin/metrics/queue 가 쓴다. 기본 백엔드(stub)에선
    # StubQueueReader 라 클러스터 없이도 라우트가 산다(설계 §2.5).
    app.state.queue_reader = build_queue_reader(settings)
```

- [ ] **Step 5: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_api_metrics.py tests/test_queue_reader.py -q`
Expected: 전부 PASS

- [ ] **Step 6: 커밋**

```bash
git add src/dms/db.py src/dms/metrics_series.py src/dms/repositories/metrics.py src/dms/api/routes_metrics.py src/dms/api/app.py tests/test_api_metrics.py
git commit -m "feat(api): /api/admin/metrics/queue — null(알 수 없음)과 [](비었음)를 구분하는 큐 현황"
```

---

### Task 4: 대시보드 「큐 현황」 카드

**Files:**
- Modify: `frontend/src/lib/types.ts`, `frontend/src/features/dashboard/useMetrics.ts`
- Create: `frontend/src/features/dashboard/QueueSection.tsx`, `frontend/src/features/dashboard/QueueSection.test.tsx`
- Modify: `frontend/src/features/dashboard/Dashboard.tsx`, `frontend/src/features/dashboard/Dashboard.test.tsx`

**Interfaces:**
- Consumes (Task 3): `GET /api/admin/metrics/queue`의 `{queue, podgroups}` — `null` = 알 수 없음, `[]` = 비었음, `wait_seconds`는 서버 계산 완료(int|null), 정렬 완료.
- Produces: `QueueMetrics`/`QueuePodgroup` 타입, `useQueueMetrics()`(5s 폴링), `QueueSection`(+ export `waitText`). 화면 규칙(설계 §3): 상태 배지는 알 수 없으면 **안 낸다**, phase 카운트(Pending/Inqueue/Running), 대기 표(잡 이름·phase·gang·대기 시간)는 Running/Completed 제외, 라벨 「지금 큐에서 대기 중」(KPI 타일의 24h 창 「대기」와 다른 수임을 라벨로 구분). **용량 게이지 없음**(설계 §2.2).

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`frontend/src/features/dashboard/QueueSection.test.tsx` (신규 파일 전체):

```tsx
import { render, screen } from "@testing-library/react";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { QueueSection, waitText } from "./QueueSection";

const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderQueue(body: unknown) {
  server.use(http.get("/api/admin/metrics/queue", () => HttpResponse.json(body)));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}><QueueSection /></QueryClientProvider>);
}

test("Open 배지·phase 카운트·대기 표를 그리고 Running은 표에서 뺀다", async () => {
  renderQueue({
    queue: { name: "dms-data", state: "Open" },
    podgroups: [
      { name: "dms-sync-abc-uid1", phase: "Pending", min_member: 3,
        created_at: "2026-08-10T00:00:00Z", wait_seconds: 125 },
      { name: "dms-scan-def-uid2", phase: "Running", min_member: 1,
        created_at: "2026-08-10T00:01:00Z", wait_seconds: 60 },
    ],
  });
  expect(await screen.findByText("Open")).toBeInTheDocument();
  expect(screen.getByText("Pending 1")).toBeInTheDocument();
  expect(screen.getByText("Running 1")).toBeInTheDocument();
  expect(screen.getByText("dms-sync-abc-uid1")).toBeInTheDocument();
  expect(screen.getByText("2분 5초")).toBeInTheDocument();   // wait_seconds 125
  // Running 은 카운트에는 있지만 「지금 큐에서 대기 중」 표에는 없다 --
  // 그 나이(now-creation)는 수명이지 대기가 아니다
  expect(screen.queryByText("dms-scan-def-uid2")).toBeNull();
});

test("null(알 수 없음)은 빈 큐로 렌더되지 않는다", async () => {
  // 403/CRD 부재 -- []로 접으면 권한 누락이 "큐가 한가함"으로 보인다(설계 §4)
  renderQueue({ queue: null, podgroups: null });
  expect(await screen.findByText(/알 수 없습니다/)).toBeInTheDocument();
  expect(screen.queryByText(/대기 중인 잡이 없습니다/)).toBeNull();
  expect(screen.queryByText("Open")).toBeNull();             // 상태 추측 금지(설계 §3)
});

test("빈 배열(비었음)은 알 수 없음과 다르게 렌더된다", async () => {
  renderQueue({ queue: { name: "dms-data", state: "Closed" }, podgroups: [] });
  expect(await screen.findByText("Closed")).toBeInTheDocument();
  expect(screen.getByText(/대기 중인 잡이 없습니다/)).toBeInTheDocument();
  expect(screen.queryByText(/알 수 없습니다/)).toBeNull();
});

test("waitText는 초를 사람이 읽는 단위로 접는다", () => {
  expect(waitText(null)).toBe("—");
  expect(waitText(42)).toBe("42초");
  expect(waitText(125)).toBe("2분 5초");
  expect(waitText(3660)).toBe("1시간 1분");
});
```

`frontend/src/features/dashboard/Dashboard.test.tsx` — `renderDash`의 `server.use(...)`에 핸들러 1개 추가(`/api/admin/metrics/infra` 줄 아래):

```tsx
    http.get("/api/admin/metrics/queue",
             () => HttpResponse.json(overrides.queue ??
               { queue: { name: "dms-data", state: "Open" }, podgroups: [] })),
```

그리고 파일 끝에 배치 테스트 추가:

```tsx
test("큐 현황 카드가 잡 통계 앞에 뜬다", async () => {
  renderDash();
  const queueCard = await screen.findByText("큐 현황");
  const jobStats = await screen.findByText("잡 통계");
  // DOM 순서 단언(설계 §3: 「잡 통계」 앞 자립형 카드)
  expect(queueCard.compareDocumentPosition(jobStats)
         & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd frontend && npx vitest run src/features/dashboard/QueueSection.test.tsx src/features/dashboard/Dashboard.test.tsx`
Expected: FAIL — QueueSection 모듈 부재(import 에러), Dashboard는 `findByText("큐 현황")` 타임아웃

- [ ] **Step 3: 타입·훅·카드를 구현한다**

**(1)** `frontend/src/lib/types.ts` 끝에 추가:

```ts
// 큐 현황(슬라이스 17). null 은 서버가 "알 수 없음"(403/CRD 부재)을 명시적으로
// 보낸 것이다 -- []("비었음")와 절대 같지 않다(설계 §4). 여기서 ?? [] 로 접으면
// 권한 누락이 "큐가 한가함"으로 렌더된다.
export interface QueuePodgroup {
  name: string; phase: string | null; min_member: number | null;
  created_at: string | null; wait_seconds: number | null;
}
export interface QueueMetrics {
  queue: { name: string; state: string | null } | null;
  podgroups: QueuePodgroup[] | null;
}
```

**(2)** `frontend/src/features/dashboard/useMetrics.ts` — import에 `QueueMetrics` 추가:

```ts
import type { InfraMetrics, JobMetrics, NodeMetrics, QueueMetrics } from "../../lib/types";
```

파일 끝에 추가:

```ts
export const useQueueMetrics = () =>
  useQuery({
    queryKey: ["metrics", "queue"],
    queryFn: () => apiGet<QueueMetrics>("/api/admin/metrics/queue"),
    refetchInterval: 5000,   // infra 와 같은 개요 폴링 -- PodGroup 은 잡과 함께
                             // 사라지므로(설계 §1-1) 짧게 봐야 대기가 보인다
  });
```

**(3)** `frontend/src/features/dashboard/QueueSection.tsx` (신규 파일 전체):

```tsx
import { useQueueMetrics } from "./useMetrics";
import { Card } from "../../components/ui/Card";
import { StatusPill } from "../../components/ui/StatusPill";
import { Table } from "../../components/ui/Table";

// 초 -> "n초"/"n분 n초"/"n시간 n분". RequestDetail 의 durationText 는 시각 2개를
// 받는 다른 시그니처라 재사용 불가(서버가 이미 초를 계산해 준다).
export function waitText(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds) || seconds < 0) return "—";
  if (seconds < 60) return `${seconds}초`;
  const m = Math.floor(seconds / 60);
  if (m < 60) return seconds % 60 === 0 ? `${m}분` : `${m}분 ${seconds % 60}초`;
  const h = Math.floor(m / 60);
  return m % 60 === 0 ? `${h}시간` : `${h}시간 ${m % 60}분`;
}

const QUEUE_STATE_VARIANT: Record<string, "ok" | "bad"> = {
  Open: "ok", Closed: "bad",
};

export function QueueSection() {
  const q = useQueueMetrics();
  // null = 알 수 없음(403/CRD 부재), [] = 비었음 -- asArray/?? [] 로 접지 않는다.
  // 접으면 권한 누락이 "큐가 한가함"으로 렌더된다(설계 §4).
  const pods = q.data?.podgroups ?? null;
  const counts = { Pending: 0, Inqueue: 0, Running: 0 };
  for (const pg of pods ?? []) {
    if (pg.phase === "Pending" || pg.phase === "Inqueue" || pg.phase === "Running") {
      counts[pg.phase] += 1;
    }
  }
  // Running 은 스케줄이 끝난 잡(나이=수명), Completed 는 삭제 직전 찰나 -- 어느
  // 쪽도 "대기"가 아니므로 표에서 뺀다(카운트에는 Running 이 남는다).
  const waiting = (pods ?? []).filter(
    (pg) => pg.phase !== "Running" && pg.phase !== "Completed");
  const state = q.data?.queue?.state ?? null;
  return (
    <Card>
      <div className="flex items-center gap-2 mb-2">
        <h2 className="font-medium">큐 현황</h2>
        <span className="text-muted text-xs">{q.data?.queue?.name ?? "dms-data"}</span>
        {/* 알 수 없으면(큐 null·state null) 배지를 내지 않는다 -- Open 을
            추측하지 않는다(설계 §3). 용량 게이지도 없다: dms-data 큐엔
            capability/deserved 가 없어 표시할 진실이 없다(설계 §2.2). */}
        {state && (
          <StatusPill state={state}
                      variant={QUEUE_STATE_VARIANT[state] ?? "neutral"} />
        )}
      </div>
      {pods === null ? (
        <p className="text-muted text-sm">
          대기 정보를 알 수 없습니다 — 권한(RBAC) 또는 Volcano 설치를 확인하세요.
        </p>
      ) : (
        <>
          <div className="flex gap-4 text-sm">
            <span>Pending {counts.Pending}</span>
            <span>Inqueue {counts.Inqueue}</span>
            <span>Running {counts.Running}</span>
          </div>
          {/* KPI 타일의 「대기」는 24h 창 DB 집계, 이 표는 무윈도 라이브 PodGroup --
              두 숫자는 어긋날 수 있고 그래야 정상이다. 라벨이 그 사실을 드러낸다
              (설계 §3 주의). */}
          <h3 className="font-medium mt-3 mb-1 text-sm">지금 큐에서 대기 중</h3>
          {waiting.length === 0 ? (
            <p className="text-muted text-sm">대기 중인 잡이 없습니다.</p>
          ) : (
            <Table>
              <thead>
                <tr className="text-muted">
                  <th className="py-1">잡</th><th>phase</th>
                  <th>gang</th><th>대기 시간</th>
                </tr>
              </thead>
              <tbody>
                {waiting.map((pg) => (
                  <tr key={pg.name} className="border-t border-black/5">
                    <td className="py-1 break-all">{pg.name}</td>
                    <td>{pg.phase ?? "—"}</td>
                    <td>{pg.min_member ?? "—"}</td>
                    <td className="tabular-nums">{waitText(pg.wait_seconds)}</td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
        </>
      )}
    </Card>
  );
}
```

**(4)** `frontend/src/features/dashboard/Dashboard.tsx` — import 추가:

```tsx
import { QueueSection } from "./QueueSection";
```

본문 끝의

```tsx
      <NodeMetricsSection />
      <JobStatsSection />
```

를

```tsx
      <NodeMetricsSection />
      {/* 설계 §3: 「잡 통계」 앞 자립형 카드 -- 잡 통계와 달리 DB 가 아니라
          라이브 PodGroup 을 본다 */}
      <QueueSection />
      <JobStatsSection />
```

로 교체.

- [ ] **Step 4: 통과를 확인한다**

Run: `cd frontend && npx vitest run` 그리고 `npx tsc -b`
Expected: 전체 PASS(기준선 206 + 신규 5), 타입 에러 0

- [ ] **Step 5: 커밋**

```bash
git add frontend/src/lib/types.ts frontend/src/features/dashboard/useMetrics.ts frontend/src/features/dashboard/QueueSection.tsx frontend/src/features/dashboard/QueueSection.test.tsx frontend/src/features/dashboard/Dashboard.tsx frontend/src/features/dashboard/Dashboard.test.tsx
git commit -m "feat(portal): 대시보드 큐 현황 카드 — 라이브 PodGroup 대기 표(null≠[] 렌더 구분)"
```

---

### Task 5: data_jobs.queue_wait_seconds — write-once + one-shot 백필 + 커버링 인덱스

**Files:**
- Modify: `src/dms/migrations.py` (CREATE TABLE + `_ensure_columns` + 인덱스 + 백필)
- Modify: `src/dms/repositories/data_jobs.py` (`set_job_state`)
- Modify: `tests/test_migrations.py`, `tests/test_repo_data_jobs.py`

**Interfaces:**
- Consumes (Task 3): `db.iso_epoch`.
- Produces (Task 6이 이 컬럼·인덱스를 그대로 쓴다):
  - `data_jobs.queue_wait_seconds BIGINT`(NULL 허용) — Pending → 첫 비-Pending 전이까지의 초. **write-once**: `set_job_state`가 `from_state == Pending`인 엣지에서만, 기존 값이 NULL일 때만 기록. NULL = 기록 전(아직 Pending / 백필 불가분 / 시각 오염).
  - `idx_data_jobs_created ON data_jobs (created_at, queue_wait_seconds)` — 제출 대기 집계 커버링 + 기존 `created_at BETWEEN` 집계 7개의 레인지 스캔화(설계 §2.3: 읽기 비용 순감).
  - `migrations._backfill_queue_wait(db)` — migrate 시 1회, `state_transitions`의 `from_state='Pending'` 첫 전이로 NULL 행만 채움(멱등).

- [ ] **Step 1: 실패하는 테스트를 쓴다**

**(1)** `tests/test_repo_data_jobs.py` 파일 끝에 추가:

```python
def test_queue_wait_recorded_once_on_first_pickup(db):
    # 슬라이스 17 설계 §2.3: Pending -> 첫 비-Pending 엣지에서 한 번만(write-once).
    # 이름과 달리 Volcano 큐 대기가 아니라 DMS 내부 픽업 지연이다 -- 화면 라벨은
    # "제출 대기"(설계 §2.4).
    from dms.db import iso_plus, utc_now_iso
    repos = _repos(db)
    rid = _mk_request(repos)
    job_id = _mk_job(repos, rid)
    db.execute("UPDATE data_jobs SET created_at = :c WHERE job_id = :j",
               {"c": iso_plus(utc_now_iso(), -120), "j": job_id})
    repos.data_jobs.set_job_state(job_id, DataJobState.PREFLIGHT, actor="stepper")
    wait = repos.data_jobs.get_job(job_id)["queue_wait_seconds"]
    assert 120 <= wait <= 122            # 1초 해상도 + 실행 지연 여유
    # 이후 전이는 덮어쓰지 않는다 -- created_at 을 더 과거로 밀어도 값이 그대로여야
    # "재계산 없음"이 증명된다(비터미널 재전이의 덮어쓰기 금지, 설계 §2.3).
    db.execute("UPDATE data_jobs SET created_at = :c WHERE job_id = :j",
               {"c": "2020-01-01T00:00:00Z", "j": job_id})
    repos.data_jobs.set_job_state(job_id, DataJobState.EXECUTING, actor="stepper")
    assert repos.data_jobs.get_job(job_id)["queue_wait_seconds"] == wait


def test_queue_wait_stays_null_while_pending(db):
    # 아직 Pending 인 잡은 "대기 미확정"이다 -- 0 이나 지금까지의 경과로 채우면
    # 집계가 진행 중인 대기를 완료된 대기처럼 오염시킨다(NULL = 집계 제외).
    repos = _repos(db)
    job_id = _mk_job(repos, _mk_request(repos))
    assert repos.data_jobs.get_job(job_id)["queue_wait_seconds"] is None
```

**(2)** `tests/test_migrations.py` 파일 끝에 추가:

```python
def test_queue_wait_column_and_covering_index_on_fresh_db(tmp_path):
    # CREATE 경로(신규 DB). BIGINT 선언은 files_count 와 같은 규약 --
    # 두 경로(CREATE/ALTER)가 같은 선언형으로 수렴해야 한다.
    db = Database.connect(f"sqlite:///{tmp_path}/t.db")
    migrate(db)
    assert _declared_type(db, "data_jobs", "queue_wait_seconds") == "BIGINT"
    rows = db.query("SELECT name FROM sqlite_master WHERE type = 'index'")
    assert "idx_data_jobs_created" in {r["name"] for r in rows}


def test_migrate_backfills_queue_wait_from_transitions(db):
    # ALTER 경로(구형 DB) + one-shot 백필(설계 §2.3). PodGroup 은 잡 종료와 함께
    # 삭제되므로(설계 §1-1) 이력에서 소급할 수 있는 것은 이 DMS 내부 대기뿐이다.
    db.execute("DROP TABLE data_jobs")
    db.execute("""CREATE TABLE data_jobs (job_id TEXT PRIMARY KEY,
        request_id TEXT NOT NULL, operation TEXT NOT NULL, tool TEXT,
        storage_name TEXT, source_storage TEXT, destination_storage TEXT,
        source TEXT, destination TEXT, target TEXT, options TEXT NOT NULL,
        priority TEXT NOT NULL, state TEXT NOT NULL, reason_code TEXT,
        preview_fingerprint TEXT, preview_expires_at TEXT, volcano_job_ref TEXT,
        artifact_uri TEXT, result_summary TEXT, files_count BIGINT,
        bytes_count BIGINT, worker_pool TEXT, precondition TEXT,
        confirmed_fingerprint TEXT, phase_refs TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    for job_id, state in (("j-done", "Succeeded"), ("j-pending", "Pending")):
        db.execute("""INSERT INTO data_jobs (job_id, request_id, operation,
            options, priority, state, created_at, updated_at)
            VALUES (:j, 'r1', 'scan', '{}', 'mid', :st,
                    '2026-08-01T00:00:00Z', '2026-08-01T01:00:00Z')""",
                   {"j": job_id, "st": state})
    for from_s, to_s, at in ((None, "Pending", "2026-08-01T00:00:00Z"),
                             ("Pending", "Preflight", "2026-08-01T00:01:30Z"),
                             ("Preflight", "Succeeded", "2026-08-01T01:00:00Z")):
        db.execute("""INSERT INTO state_transitions (entity_kind, entity_id,
            from_state, to_state, actor, at)
            VALUES ('data_job', 'j-done', :f, :t, 'stepper', :at)""",
                   {"f": from_s, "t": to_s, "at": at})
    from dms.migrations import _column_exists, migrate
    migrate(db)
    assert _column_exists(db, "data_jobs", "queue_wait_seconds")
    waits = {r["job_id"]: r["queue_wait_seconds"]
             for r in db.query("SELECT job_id, queue_wait_seconds FROM data_jobs")}
    assert waits["j-done"] == 90         # 첫 비-Pending 전이(00:01:30) - created_at
    assert waits["j-pending"] is None    # 아직 Pending -- 백필 대상이 아니다(집계 제외)


def test_backfill_only_fills_null_rows(db):
    # migrate 는 파드 기동마다(initContainer) 재실행된다 -- 이미 채워진 값(런타임
    # write-once 포함)을 백필이 재계산해 덮으면 write-once 계약이 마이그레이션
    # 경로로 우회된다. NULL 만 채우는 멱등성이 계약이다.
    db.execute("""INSERT INTO data_jobs (job_id, request_id, operation, options,
        priority, state, queue_wait_seconds, created_at, updated_at)
        VALUES ('j1', 'r1', 'scan', '{}', 'mid', 'Succeeded', 7,
                '2026-08-01T00:00:00Z', '2026-08-01T01:00:00Z')""")
    db.execute("""INSERT INTO state_transitions (entity_kind, entity_id,
        from_state, to_state, actor, at)
        VALUES ('data_job', 'j1', 'Pending', 'Preflight', 'stepper',
                '2026-08-01T00:05:00Z')""")   # 재계산되면 300 이 된다
    migrate(db)
    row = db.query_one("SELECT queue_wait_seconds FROM data_jobs WHERE job_id = 'j1'")
    assert row["queue_wait_seconds"] == 7
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_repo_data_jobs.py tests/test_migrations.py -q`
Expected: FAIL — repo 2건은 `KeyError: 'queue_wait_seconds'`(SELECT * 결과에 컬럼 부재), migrations 3건은 선언형/`_column_exists`/백필 값 단언에서. 기존 테스트는 PASS 유지.

- [ ] **Step 3: migrations.py를 고친다**

**(1)** import 교체:

```python
from .db import Database, iso_epoch, utc_now_iso
```

**(2)** CREATE TABLE data_jobs의 `phase_refs TEXT,` 줄과 `created_at TEXT NOT NULL,` 줄 사이에 삽입:

```sql
            phase_refs TEXT,
            -- 제출 대기(슬라이스 17 설계 §2.3): Pending -> 첫 비-Pending 전이까지의
            -- 초. set_job_state 가 그 엣지에서 한 번만 쓴다(write-once). 컬럼명과
            -- 달리 Volcano 큐 대기가 아니라 DMS 내부 픽업 지연이다 -- 화면 라벨은
            -- "제출 대기"로 정직하게 붙인다(설계 §2.4). NULL = 기록 전(아직
            -- Pending/백필 불가분/시각 오염) -- 집계에서 제외하고 제외 건수를
            -- 표면화한다. BIGINT 는 files_count 와 같은 규약(두 경로 동일 선언형).
            queue_wait_seconds BIGINT,
            created_at TEXT NOT NULL,
```

**(3)** `_ensure_columns` 튜플의 `("data_jobs", "bytes_count", "BIGINT"),` 아래에 추가:

```python
        # 슬라이스 17 제출 대기 -- 기배포 DB 는 CREATE 를 다시 안 탄다(슬라이스 14 의
        # 실 500 교훈: 양쪽에 넣지 않으면 라이브에서만 컬럼이 없다).
        ("data_jobs", "queue_wait_seconds", "BIGINT"),
```

**(4)** `_apply_migrations`의 `idx_requests_batch` 생성(:308) 바로 아래에 추가:

```python
    # queue_wait_seconds 는 CREATE(신규) 또는 _ensure_columns(구형)로 보강된 뒤에만
    # 존재하므로 이 인덱스도 그 이후다(idx_requests_batch 와 같은 이유).
    # (created_at, queue_wait_seconds) 커버링: 제출 대기 집계 2쿼리가 인덱스만 읽고,
    # 덤으로 기존 created_at BETWEEN 집계 7개(repositories/metrics.py)가 풀스캔에서
    # 레인지 스캔이 된다 -- 이 슬라이스는 읽기 비용의 순증이 아니라 순감이다(설계 §2.3).
    db.execute("CREATE INDEX IF NOT EXISTS idx_data_jobs_created"
               " ON data_jobs (created_at, queue_wait_seconds)")
    _backfill_queue_wait(db)
```

**(5)** `_ensure_columns` 함수 아래에 백필 함수 추가:

```python
def _backfill_queue_wait(db):
    """queue_wait_seconds one-shot 백필(설계 §2.3). state_transitions 에서 잡별
    Pending -> 첫 전이 시각을 찾아 created_at 과의 차를 채운다. NULL 행만 채우므로
    멱등이다 -- migrate 는 파드 기동마다(initContainer) 재실행되고, 이미 채워진
    값을 재계산하면 write-once 계약이 마이그레이션 경로로 우회된다. 잔여 NULL 은
    아직 Pending 인 잡뿐이라(조인이 걸러 낸다) 재실행 비용은 무시할 수준이다.
    시각 산술은 SQL 로 이식 불가(julianday=SQLite, EXTRACT=PG 전용) -- 파이썬에서
    빼고 행별 UPDATE 를 친다. 마이그레이션 1회 경로라 허용한다(폴링 경로가 아니고,
    PG 어드바이저리 락 안이라 동시 기동과도 경합하지 않는다)."""
    rows = db.query(
        """SELECT d.job_id, d.created_at, MIN(t.at) AS picked_at
           FROM data_jobs d
           JOIN state_transitions t
             ON t.entity_kind = 'data_job' AND t.entity_id = d.job_id
                AND t.from_state = 'Pending'
           WHERE d.queue_wait_seconds IS NULL
           GROUP BY d.job_id, d.created_at""")
    for row in rows:
        try:
            wait = int(iso_epoch(row["picked_at"]) - iso_epoch(row["created_at"]))
        except (TypeError, ValueError):
            continue          # 시각이 깨진 행은 NULL 로 남긴다(집계 제외 -- 설계 §4)
        if wait < 0:
            continue          # 시계 역행/오염 -- 값을 지어내느니 NULL 이 정직하다
        db.execute(
            "UPDATE data_jobs SET queue_wait_seconds = :w WHERE job_id = :j",
            {"w": wait, "j": row["job_id"]})
```

- [ ] **Step 4: set_job_state에 write-once 기록을 얹는다**

`src/dms/repositories/data_jobs.py` — import 교체:

```python
from ..db import Database, dump_json, iso_epoch, iso_plus, load_json, utc_now_iso
```

`set_job_state`의 SELECT(:133-134)를 교체:

```python
            current = self._db.query_one(
                """SELECT state, request_id, created_at, queue_wait_seconds
                   FROM data_jobs WHERE job_id = :j""", {"j": job_id})
```

`else:` 분기의 UPDATE(:145-148)를 다음으로 교체(추가 statement 0, 추가 왕복 0 — 설계 §2.3):

```python
            else:
                # 제출 대기(슬라이스 17 설계 §2.3): Pending -> 첫 비-Pending 엣지에서만
                # 계산해 한 번 쓴다(write-once). IS NULL 가드는 이중 안전장치다 --
                # 상태 기계상 Pending 재진입은 없지만, 생겨도 덮어쓰지 않는다.
                queue_wait = current["queue_wait_seconds"]
                if (queue_wait is None
                        and current["state"] == DataJobState.PENDING.value
                        and to_state is not DataJobState.PENDING):
                    try:
                        # 시계 스큐(다른 프로세스가 쓴 created_at)로 음수가 나올 수
                        # 있다 -- 1초 해상도 세계에서 0 으로 접는 것이 정직하다.
                        queue_wait = max(
                            0, int(iso_epoch(now) - iso_epoch(current["created_at"])))
                    except (TypeError, ValueError):
                        queue_wait = None   # 시각이 깨졌으면 지어내지 않는다(NULL)
                self._db.execute(
                    """UPDATE data_jobs SET state = :s, reason_code = :rc,
                           updated_at = :now, queue_wait_seconds = :qw
                       WHERE job_id = :j""",
                    {"s": to_state.value, "rc": reason_code, "now": now,
                     "qw": queue_wait, "j": job_id})
                self._record_transition(
                    "data_job", job_id, DataJobState(current["state"]),
                    to_state, reason_code, actor, now)
```

(`_record_transition` 호출은 기존 그대로 — UPDATE만 바뀐다. 엣지가 아닐 때는 `queue_wait`에 기존 값을 되쓰므로 값이 변하지 않는다.)

- [ ] **Step 5: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_repo_data_jobs.py tests/test_migrations.py tests/test_data_jobs_terminal_guard.py tests/test_repo_data_jobs_stepper.py -q`
Expected: 전부 PASS (터미널 가드 경로는 UPDATE 자체를 안 타므로 무영향)

- [ ] **Step 6: 전체 백엔드 회귀를 확인한다**

Run: `.venv/bin/python -m pytest -q` (포그라운드, Bash timeout 400000ms)
Expected: 전부 PASS — set_job_state·migrate는 거의 모든 테스트의 공용 경로다(회귀 여기서 잡는다)

- [ ] **Step 7: 커밋**

```bash
git add src/dms/migrations.py src/dms/repositories/data_jobs.py tests/test_migrations.py tests/test_repo_data_jobs.py
git commit -m "feat(db): data_jobs.queue_wait_seconds — write-once 기록 + one-shot 백필 + 커버링 인덱스"
```

---

### Task 6: 제출 대기 분포 — job_stats 집계 + 전용 버킷 + 라우트

**Files:**
- Modify: `src/dms/metrics_series.py` (`duration_histogram` 파라미터화 + `SUBMIT_WAIT_BUCKETS`)
- Modify: `src/dms/repositories/metrics.py` (`job_stats` 2쿼리)
- Modify: `src/dms/api/routes_metrics.py` (`metrics_jobs`)
- Modify: `tests/test_metrics_series.py`, `tests/test_repo_metrics.py`, `tests/test_api_metrics.py`

**Interfaces:**
- Consumes (Task 5): `data_jobs.queue_wait_seconds` + `idx_data_jobs_created`.
- Produces (Task 7 프론트가 이 키를 그대로 쓴다):
  - `metrics_series.SUBMIT_WAIT_BUCKETS = (("<10s", 10), ("10-30s", 30), ("30-60s", 60), ("1-5m", 300), ("5-30m", 1800))`, `SUBMIT_WAIT_OVERFLOW = ">30m"`.
  - `duration_histogram(seconds, *, buckets=DURATION_BUCKETS, overflow=">24h")` — 기본 호출 무변경(기존 수행시간 분포 동작 동일).
  - `job_stats` 반환에 `"submit_wait_seconds": list[int]`(NULL 제외 원자료)와 `"submit_wait_excluded": int`(창 안 NULL 건수) 추가.
  - `GET /api/admin/metrics/jobs` 응답에 `submit_wait_histogram`(6버킷)·`submit_wait_counted`·`submit_wait_excluded` 추가, `submit_wait_seconds` 원자료는 응답에서 제거(duration과 같은 규칙).

- [ ] **Step 1: 실패하는 테스트를 쓴다**

**(1)** `tests/test_metrics_series.py` 파일 끝에 추가:

```python
def test_duration_histogram_accepts_custom_buckets():
    # 제출 대기(슬라이스 17)는 플래너 틱(10s)·스테퍼 틱(5s) 규모다 -- 수행시간
    # 버킷(<1m 시작)을 그대로 쓰면 정상 대기가 전부 첫 버킷에 뭉쳐 분포가
    # 사라진다. 경계는 정상(<30s)/유예·지연(30s-5m: 신원 전파 유예 기본 300s)/
    # 백로그(>5m)를 가른다.
    from dms.metrics_series import (SUBMIT_WAIT_BUCKETS, SUBMIT_WAIT_OVERFLOW,
                                    duration_histogram)
    hist = duration_histogram([5, 45, 2000], buckets=SUBMIT_WAIT_BUCKETS,
                              overflow=SUBMIT_WAIT_OVERFLOW)
    assert {b["bucket"]: b["count"] for b in hist} == {
        "<10s": 1, "10-30s": 0, "30-60s": 1, "1-5m": 0, "5-30m": 0, ">30m": 1}
    # 기본 호출(수행시간)은 파라미터화 전과 완전히 같아야 한다 -- 기존 테스트
    # test_duration_histogram_fixed_buckets 가 그 절반을 지키고, 여기는 라벨 순서.
    assert [b["bucket"] for b in duration_histogram([])] == [
        "<1m", "1-10m", "10-60m", "1-6h", "6-24h", ">24h"]
```

**(2)** `tests/test_repo_metrics.py` — `_seed_job` 헬퍼의 시그니처와 UPDATE를 교체(기존 호출부는 전부 기본값이라 무영향):

```python
def _seed_job(db, repos, *, created_at, state="Succeeded", tool="dscan",
              storage="s1", dest_storage=None, requester="alice",
              reason_code=None, updated_at=None, files=None, nbytes=None,
              wait=None):
    """data_jobs 한 행을 원하는 상태·시각으로 심는다. set_job_state는 updated_at을
    현재 시각으로 찍으므로 창(window) 테스트가 불가능하다 -- 정상 경로로 만들고
    시각·상태만 UPDATE로 덮는다. wait 는 queue_wait_seconds(기본 NULL -- 백필
    공백/진행 중과 같은 모양)."""
    rid = repos.requests.create(
        operation="scan", requester_id=requester, actor=requester,
        resource_key=f"k:{created_at}:{tool}:{state}:{requester}", payload={},
        priority="mid")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    job_id = repos.data_jobs.create_job(
        rid, plan_id, operation="scan", priority="mid", storage_name=storage,
        destination_storage=dest_storage, target="a", options={}, tool=tool,
        worker_pool={}, precondition={}, actor="planner")
    db.execute(
        """UPDATE data_jobs SET state = :st, reason_code = :rc, created_at = :c,
               updated_at = :u, files_count = :f, bytes_count = :b,
               queue_wait_seconds = :w
           WHERE job_id = :j""",
        {"st": state, "rc": reason_code, "c": created_at,
         "u": updated_at or created_at, "f": files, "b": nbytes, "w": wait,
         "j": job_id})
    return job_id
```

파일 끝에 테스트 추가:

```python
def test_job_stats_submit_wait_excludes_null_and_surfaces_the_gap(db, repos):
    # NULL(백필 불가분·아직 Pending)을 0 으로 세면 평균·분포가 통째로 왜곡된다 --
    # 제외하되, 제외 건수를 함께 내 화면이 공백을 숨기지 못하게 한다(설계 §3).
    _seed_job(db, repos, created_at="2026-08-09T01:00:00Z", wait=5)
    _seed_job(db, repos, created_at="2026-08-09T02:00:00Z", wait=45)
    _seed_job(db, repos, created_at="2026-08-09T03:00:00Z")            # NULL
    _seed_job(db, repos, created_at="2026-07-01T00:00:00Z", wait=999)  # 창 밖
    stats = repos.metrics.job_stats(start="2026-08-09T00:00:00Z",
                                    end="2026-08-09T23:59:59Z")
    assert sorted(stats["submit_wait_seconds"]) == [5, 45]
    assert stats["submit_wait_excluded"] == 1
```

**(3)** `tests/test_api_metrics.py` 파일 끝에 추가:

```python
def test_metrics_jobs_submit_wait_distribution_and_counts(client, db):
    repos = Repositories(db)
    now = utc_now_iso()
    rid = _seed_job(db, repos, created_at=iso_plus(now, -3600))
    _seed_job(db, repos, created_at=iso_plus(now, -1800))              # NULL 유지
    db.execute("UPDATE data_jobs SET queue_wait_seconds = 12 WHERE request_id = :r",
               {"r": rid})
    body = client.get("/api/admin/metrics/jobs?window=24", headers=ADMIN).json()
    hist = {b["bucket"]: b["count"] for b in body["submit_wait_histogram"]}
    assert list(hist) == ["<10s", "10-30s", "30-60s", "1-5m", "5-30m", ">30m"]
    assert hist["10-30s"] == 1
    assert body["submit_wait_counted"] == 1
    assert body["submit_wait_excluded"] == 1     # NULL 잡의 제외를 표면화(설계 §3)
    assert "submit_wait_seconds" not in body     # 원자료는 내보내지 않는다(duration 규칙)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_metrics_series.py tests/test_repo_metrics.py tests/test_api_metrics.py -q`
Expected: FAIL — series 1건(`ImportError: SUBMIT_WAIT_BUCKETS` 또는 `TypeError: unexpected keyword 'buckets'`), repo 1건(`KeyError: 'submit_wait_seconds'`), api 1건(`KeyError: 'submit_wait_histogram'`)

- [ ] **Step 3: 구현한다**

**(1)** `src/dms/metrics_series.py` — `DURATION_BUCKETS` 정의 아래에 추가:

```python
# 제출 대기(슬라이스 17)의 버킷. 수행시간 버킷(<1m 시작)을 그대로 쓰면 플래너 틱
# (10s)+스테퍼 틱(5s) 안에 끝나는 정상 대기가 전부 첫 버킷에 뭉쳐 분포가 사라진다 --
# 정상(<30s), 유예·지연(30s-5m: 신원 전파 유예 기본 300s), 백로그(>5m)가 구분되는
# 경계로 자른다.
SUBMIT_WAIT_BUCKETS = (("<10s", 10), ("10-30s", 30), ("30-60s", 60),
                       ("1-5m", 300), ("5-30m", 1800))
SUBMIT_WAIT_OVERFLOW = ">30m"
```

`duration_histogram`을 교체(기본값이 기존과 동일해 호출부 무변경):

```python
def duration_histogram(seconds: list, *, buckets=DURATION_BUCKETS,
                       overflow=">24h") -> list[dict]:
    counts = [0] * (len(buckets) + 1)
    for value in seconds:
        v = _num(value)
        if v is None or v < 0:
            continue
        for i, (_, upper) in enumerate(buckets):
            if v < upper:
                counts[i] += 1
                break
        else:
            counts[-1] += 1
    labels = [label for label, _ in buckets] + [overflow]
    return [{"bucket": label, "count": counts[i]} for i, label in enumerate(labels)]
```

**(2)** `src/dms/repositories/metrics.py` — `job_stats`의 `sums = ...` 쿼리 앞에 추가:

```python
        # 제출 대기(슬라이스 17 설계 §2.3): NULL(백필 불가분·아직 Pending)은 집계에서
        # 제외하고 제외 건수를 함께 낸다 -- 백필 공백을 화면에서 숨기지 않는다(설계
        # §3). 두 쿼리 모두 idx_data_jobs_created (created_at, queue_wait_seconds)
        # 가 커버한다 -- 테이블을 건드리지 않는 인덱스 온리 레인지 스캔이라,
        # 슬라이스 14 가 (B) 를 금지했던 근거(전기간 풀스캔)가 성립하지 않는다.
        waits = self._db.query(
            """SELECT queue_wait_seconds AS w FROM data_jobs
               WHERE created_at BETWEEN :s AND :e
                 AND queue_wait_seconds IS NOT NULL""", params)
        excluded = self._db.query_one(
            """SELECT COUNT(*) AS c FROM data_jobs
               WHERE created_at BETWEEN :s AND :e
                 AND queue_wait_seconds IS NULL""", params)
```

반환 dict의 `"duration_seconds": duration_seconds,` 아래에 추가:

```python
            "submit_wait_seconds": [row["w"] for row in waits],
            "submit_wait_excluded": excluded["c"],
```

**(3)** `src/dms/api/routes_metrics.py` — import 확장:

```python
from ..metrics_series import (SUBMIT_WAIT_BUCKETS, SUBMIT_WAIT_OVERFLOW,
                              bucket_chars_for, build_node_points,
                              clamp_window_hours, duration_histogram)
```

`metrics_jobs`의 `stats["duration_histogram"] = ...` 줄 아래에 추가:

```python
    # 제출 대기 분포(슬라이스 17): duration 과 같은 이유로 원자료 대신 분포만.
    # counted 는 NULL 제외 후 집계 대상 건수 -- excluded 와 함께 내 화면이 백필
    # 공백을 숨기지 못하게 한다(설계 §3).
    submit_waits = stats.pop("submit_wait_seconds")
    stats["submit_wait_counted"] = len(submit_waits)
    stats["submit_wait_histogram"] = duration_histogram(
        submit_waits, buckets=SUBMIT_WAIT_BUCKETS, overflow=SUBMIT_WAIT_OVERFLOW)
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_metrics_series.py tests/test_repo_metrics.py tests/test_api_metrics.py -q`
Expected: 전부 PASS (기존 `test_metrics_jobs_aggregates_and_histogram_shape`는 새 키를 단언하지 않아 무영향)

- [ ] **Step 5: 커밋**

```bash
git add src/dms/metrics_series.py src/dms/repositories/metrics.py src/dms/api/routes_metrics.py tests/test_metrics_series.py tests/test_repo_metrics.py tests/test_api_metrics.py
git commit -m "feat(metrics): 제출 대기 분포 — NULL 제외 집계 + 제외 건수 표면화(인덱스 커버)"
```

---

### Task 7: 프론트 제출 대기 렌더링 + 「큐 대기」 라벨 정정

**Files:**
- Modify: `frontend/src/lib/types.ts` (`JobMetrics` 3필드)
- Modify: `frontend/src/features/dashboard/JobStatsSection.tsx`, `frontend/src/features/dashboard/JobStatsSection.test.tsx`
- Modify: `frontend/src/features/jobs/RequestDetail.tsx`, `frontend/src/features/jobs/RequestDetail.test.tsx`

**Interfaces:**
- Consumes (Task 6): `/api/admin/metrics/jobs`의 `submit_wait_histogram`/`submit_wait_counted`/`submit_wait_excluded`.
- Produces: 「잡 통계」에 제출 대기 분포(수행시간 분포 옆) + 집계/제외 건수 + 포함 관계 명시(설계 §2.4: 수행시간은 이 대기를 포함한 전체 수명). 요청 상세의 「큐 대기」 라벨을 「제출 대기」로 정정(설계 §2.4 — 그 값은 플래너 틱 지연이지 Volcano 큐 대기가 아니다). 값 계산 로직은 무변경 — 라벨과 주석만 바뀐다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

**(1)** `frontend/src/features/dashboard/JobStatsSection.test.tsx` — `STATS` 상수의 `files_total: null, bytes_total: null,` 줄을 다음으로 교체:

```tsx
  submit_wait_histogram: [
    { bucket: "<10s", count: 2 }, { bucket: "10-30s", count: 1 },
    { bucket: "30-60s", count: 0 }, { bucket: "1-5m", count: 0 },
    { bucket: "5-30m", count: 0 }, { bucket: ">30m", count: 0 }],
  submit_wait_counted: 3, submit_wait_excluded: 1,
  files_total: null, bytes_total: null,
```

파일 끝에 테스트 추가:

```tsx
test("제출 대기 분포와 집계/제외 건수를 보여준다", async () => {
  renderSection();
  const chart = await screen.findByRole("img", { name: "제출 대기 분포" });
  expect(chart.querySelectorAll("rect")).toHaveLength(6);
  // 제외 건수를 숨기지 않는다(설계 §3) + 수행시간과의 포함 관계 명시(설계 §2.4)
  expect(screen.getByText(/집계 3건 · 제외\(기록 없음\) 1건/)).toBeInTheDocument();
  expect(screen.getByText(/수행시간 분포는 이 대기를 포함/)).toBeInTheDocument();
});
```

**(2)** `frontend/src/features/jobs/RequestDetail.test.tsx` — 기존 2건(:289-320)을 교체:

```tsx
test("제출 대기를 첫 비-Pending 전이에서 유도해 보여준다", async () => {
  // 슬라이스 17이 슬라이스 14의 「큐 대기」 라벨을 정정했다(설계 §2.4): 이 값은
  // 플래너 픽업 지연이지 Volcano 큐 대기가 아니다 -- 옛 라벨은 사용자가 "Volcano
  // 큐에서 기다린 시간"으로 읽는다.
  server.use(
    http.get("/api/user/requests/r1", () => HttpResponse.json({
      ...REQUEST,
      transitions: [
        { from_state: null, to_state: "Pending", at: "2026-08-05T00:00:00Z" },
        { from_state: "Pending", to_state: "Planned", at: "2026-08-05T00:01:30Z" },
        { from_state: "Planned", to_state: "Failed",
          reason_code: "no_eligible_nodes", at: "2026-08-05T00:02:00Z" },
      ],
    })),
    http.get("/api/user/requests/r1/jobs", () => HttpResponse.json(JOBS)),
  );
  renderAt();
  expect(await screen.findByText("제출 대기")).toBeInTheDocument();
  expect(screen.getByText("1분 30초")).toBeInTheDocument();
  expect(screen.queryByText("큐 대기")).toBeNull();   // 옛 라벨이 되살아나면 회귀
});

test("실행 전이가 아직 없으면 제출 대기는 —", async () => {
  server.use(
    http.get("/api/user/requests/r1", () => HttpResponse.json({
      ...REQUEST, state: "Pending",
      transitions: [
        { from_state: null, to_state: "Pending", at: "2026-08-05T00:00:00Z" },
      ],
    })),
    http.get("/api/user/requests/r1/jobs", () => HttpResponse.json([])),
  );
  renderAt();
  const dt = await screen.findByText("제출 대기");
  expect(dt.nextElementSibling).toHaveTextContent("—");
});
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd frontend && npx vitest run src/features/dashboard/JobStatsSection.test.tsx src/features/jobs/RequestDetail.test.tsx`
Expected: FAIL — JobStats 1건(`findByRole` 타임아웃), RequestDetail 2건(`findByText("제출 대기")` 타임아웃)

- [ ] **Step 3: 구현한다**

**(1)** `frontend/src/lib/types.ts` — `JobMetrics`의 `duration_histogram` 줄 아래에 추가:

```ts
  // 제출 대기(슬라이스 17): created_at -> 첫 비-Pending 전이. Volcano 큐 대기가
  // 아니라 DMS 내부 픽업 지연이다(설계 §2.4 -- 그래서 이름이 "제출 대기"다).
  // excluded = NULL(백필 불가분·아직 Pending)로 집계에서 빠진 건수.
  submit_wait_histogram: { bucket: string; count: number }[];
  submit_wait_counted: number;
  submit_wait_excluded: number;
```

**(2)** `frontend/src/features/dashboard/JobStatsSection.tsx` — `durations` 정의 아래에 추가:

```tsx
  const submitWaits = asArray<{ bucket: string; count: number }>(d?.submit_wait_histogram)
    .map((b) => ({ label: b.bucket, value: b.count }));
```

처리량/수행시간 grid를 교체(`md:grid-cols-2` → 3열 + 셋째 칸):

```tsx
      <div className="grid md:grid-cols-3 gap-4 mt-3">
        <div>
          <h3 className="font-medium mb-2 text-sm">처리량</h3>
          <BarChart data={throughput} label="처리량" />
        </div>
        <div>
          <h3 className="font-medium mb-2 text-sm">수행시간 분포</h3>
          <BarChart data={durations} label="수행시간 분포" />
        </div>
        <div>
          <h3 className="font-medium mb-2 text-sm">제출 대기 분포</h3>
          <BarChart data={submitWaits} label="제출 대기 분포" />
          {/* 수행시간(created_at -> updated_at)은 이 대기를 포함한 전체 수명이다 --
              나란히 놓인 두 분포의 포함 관계를 화면에 명시한다(설계 §2.4). 제외
              건수(NULL)는 백필 공백 -- 숨기지 않는다(설계 §3). 한 개의 템플릿
              리터럴 = 한 개의 텍스트 노드(getByText 가 통으로 찾도록). */}
          <p className="text-muted text-xs mt-1">
            {`집계 ${d?.submit_wait_counted ?? 0}건 · 제외(기록 없음) ${d?.submit_wait_excluded ?? 0}건 — 수행시간 분포는 이 대기를 포함합니다`}
          </p>
        </div>
      </div>
```

**(3)** `frontend/src/features/jobs/RequestDetail.tsx` — 주석(:138-140)과 라벨(:156)을 교체:

```tsx
  // 제출 대기(슬라이스 17이 슬라이스 14의 「큐 대기」 라벨을 정정 -- 설계 §2.4):
  // 이 값은 요청 Pending -> 첫 비-Pending 전이(플래너 픽업)의 지연이지 Volcano 큐
  // 대기가 아니다. 진짜 Volcano 대기는 대시보드 큐 카드(라이브 PodGroup)에만 있다.
  const firstPickup = transitions.find((t) => t.to_state !== "Pending");
```

```tsx
          <dt className="text-muted">제출 대기</dt>
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd frontend && npx vitest run` 그리고 `npx tsc -b`
Expected: 전체 PASS(기준선 206 + Task 4의 5 + 신규 1 = 212), 타입 에러 0

- [ ] **Step 5: 백엔드 전체 회귀도 최종 확인한다**

Run: `.venv/bin/python -m pytest -q` (포그라운드, Bash timeout 400000ms)
Expected: 전부 PASS — 기준선 963 + 이번 슬라이스 신규(대략 +28: queue_reader 9, wiring 2, rbac 4, api_metrics 6, migrations 3, repo_data_jobs 2, repo_metrics 1, metrics_series 1 — 정확 수는 실행이 확정한다)

- [ ] **Step 6: 커밋**

```bash
git add frontend/src/lib/types.ts frontend/src/features/dashboard/JobStatsSection.tsx frontend/src/features/dashboard/JobStatsSection.test.tsx frontend/src/features/jobs/RequestDetail.tsx frontend/src/features/jobs/RequestDetail.test.tsx
git commit -m "feat(portal): 제출 대기 분포 + 「큐 대기」→「제출 대기」 라벨 정정"
```

---

## 플랜 이후: 배포·실증 (설계 §6 — 별도 ops, 플랜 밖)

플랜 실행(7태스크 커밋)이 끝나면 컨트롤러가 테스트베드에서 수행한다 — 플랜 태스크가 아니다(슬라이스 12~16과 동일 관례). 에이전트 코드는 이번에 안 바뀌었으므로 `dms` 이미지만 범프한다. **순서가 실증의 일부다** — RBAC 적용 전 상태를 먼저 확인해야 §6-1이 의미를 가진다:

1. `deploy/k8s`의 태그를 새 값으로 범프(40/41/30 → `dms:d28`) 후 빌드/푸시, `kubectl apply` — 단 **`10-rbac.yaml`은 아직 apply 하지 않는다**.
2. (§6-1) RBAC 적용 전: `/api/admin/metrics/queue`가 403을 **null(알 수 없음)**로 내고, 대시보드 큐 카드가 "대기 정보를 알 수 없습니다"를 렌더하는지 — 빈 큐로 렌더되면 실패다.
3. `kubectl apply -f deploy/k8s/10-rbac.yaml` → (§6-2) 큐 상태 Open + Phase 카운트가 나오는지.
4. (§6-3) 실제 sync 잡 제출 → 대기 중 PodGroup이 표에 뜨고 대기 시간이 증가하는지, 완료 후 표에서 사라지는지(PodGroup 삭제 §1-1의 귀결).
5. (§6-4) `queue_wait_seconds`가 신규 잡에 채워지고, initContainer migrate의 백필이 기존 잡을 채웠는지(`SELECT COUNT(*) FROM data_jobs WHERE queue_wait_seconds IS NULL` 전후 비교).
6. (§6-5) 제출 대기 분포가 「잡 통계」에 뜨고 집계/제외 건수가 함께 표시되는지.
7. (§6-6) 요청 상세의 라벨이 「큐 대기」에서 「제출 대기」로 바뀌었는지.

## Self-Review

**1. 스펙 커버리지**

| 설계 절 | 담당 태스크 |
|---|---|
| §1 실측 전제(PodGroup 삭제, RBAC 전무, capability 없음, vcjob conditions 결함, created_at 인덱스 없음, 시작 시각 컬럼 없음, stub 기본, timeout 미전달, 단일 커넥션, RBAC 테스트 0건) | 실측 고정값 표 + 각 태스크 근거 주석 |
| §2.1 PodGroup 코어 + Queue state 하나(이름 지정 GET, podgroups namespaced list) | Task 1(리더) + Task 2(RBAC) |
| §2.2 용량 게이지 금지 | Task 4가 만들지 않음(QueueSection 주석으로 명시) + Global Constraints |
| §2.3 queue_wait_seconds 컬럼 + 커버링 인덱스 + write-once + 백필 + 풀스캔 순감 | Task 5 + Task 6(읽기) |
| §2.4 정직한 이름(제출 대기) + 기존 거짓 라벨 정정 + 포함 관계 명시 | Task 6(키 이름) + Task 7(라벨·문구) |
| §2.5 스텁 페어 필수 | Task 1(StubQueueReader) + Task 3(스텁 경유 라우트 테스트) |
| §3 화면(배지/카운트/대기 표/제출 대기 분포+건수, 중복 금지, KPI 어긋남 라벨) | Task 4 + Task 7 — 기존 JobStatsSection 항목은 하나도 재현하지 않음 |
| §4 오류 처리(3상태 비뭉개짐, omitempty 구분, _request_timeout, metrics_infra 패턴) | Task 1(404 접기·타임아웃) + Task 3(축별 fail-soft) — omitempty 카운터는 아예 읽지 않음(설계 §2.1) |
| §5 테스트 목록(4경우 구분, 스텁 결정성, write-once/백필/NULL 제외, CREATE+ALTER 양쪽+인덱스, RBAC 계약, 프론트 null vs []) | Task 1·2·3·4·5·6 각 Step 1 |
| §6 실증 | 플랜 이후 절(관례 — 플랜 태스크 아님) |
| §7 하지 않는 것(용량 게이지, Volcano 대기 이력, condition 문자열 분기, 큐 전용 페이지, 쓰기 동작, runs 부활, Prometheus) | 어떤 태스크도 건드리지 않음 — conditions는 읽지도 않고(§3 표 열에 없음), 큐 관련 신규 화면은 대시보드 카드 하나뿐 |

**2. 플레이스홀더 점검** — "TBD"/"적절히 처리"/코드 없는 테스트 지시 없음. 모든 코드 단계(신규 파일 2 + 프론트 신규 2 포함)에 전문이 있고, YAML 삽입 블록도 전문 수록. 다른 태스크 참조는 Interfaces 블록의 시그니처로만 한다.

**3. 타입 일관성** — Task 1의 `read_queue()`/`read_podgroups()` 반환 모양(`name/phase/min_member/created_at`)을 Task 3 라우트가 그대로 받아 `wait_seconds`만 얹고, Task 4의 `QueuePodgroup`이 다섯 키를 같은 철자로 선언한다. `DMS_QUEUE`/`build_queue_reader`/`app.state.queue_reader`는 Task 1→3의 한 철자다. `iso_epoch`는 Task 3이 정의하고 Task 5(set_job_state·백필)가 같은 이름으로 import한다. `queue_wait_seconds`는 컬럼(Task 5)·쿼리(Task 6)·테스트 시드 전부 동일 철자, API 키는 일관되게 `submit_wait_*`(histogram/counted/excluded)이고 Task 7 타입·JSX가 그대로 쓴다. `SUBMIT_WAIT_BUCKETS`/`SUBMIT_WAIT_OVERFLOW`는 Task 6 정의·import·테스트가 같은 이름이다. RBAC 이름 `dms-api-queue-readonly`는 매니페스트·계약 테스트·실증 절에서 동일하다.

**알려진 위험:**
- **PodGroup 응답 모양은 실측 기반**: `spec.queue`/`status.phase`/`creationTimestamp`는 v1.15.0 클러스터에서 확인했지만 페이크 기반 테스트는 클러스터 드리프트를 못 잡는다 — 실증 §6-3이 실물로 확인한다.
- **ClusterRole은 kubectl apply가 필요**: 포탈 롤아웃은 RBAC을 안 만진다 — 실증 절이 apply 전/후를 순서로 고정했다.
- **백필은 state_transitions가 남아 있는 잡만** 채운다 — 이 저장소는 전이를 purge하지 않으므로(retention은 events/agent_reports만) 현재 전 잡이 대상이지만, 전이 purge가 생기면 그 이전 잡은 NULL로 남는다(정직한 강등 — 제외 건수로 표면화된다).
- **큐 이름 하드코딩(dms-data)**: 운영자가 policies.queue를 다른 큐로 바꾸면 카드는 여전히 dms-data만 본다 — `DMS_QUEUE` 주석이 RBAC(resourceNames) 결합을 문서화했고, 다중 큐는 설계 밖이다.
- **`_seed_job`(test_repo_metrics) 시그니처 확장**: 기본값 유지라 기존 호출부 무영향이지만, 같은 이름의 헬퍼가 `test_api_metrics.py`에도 따로 있다(그쪽은 확장하지 않고 직접 UPDATE) — 두 파일을 혼동하지 말 것.



