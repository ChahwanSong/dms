# 슬라이스 16 — 배포 안전망 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 설계가 약속했는데 코드에 없는 불변식과 조용히 프로덕션을 되돌리는 배포 경로 5개를 닫는다 — 매니페스트 드리프트 배지, migrate 자동화(initContainer+어드바이저리 락), 플래너 신원 전파 유예, 워커 required anti-affinity, 에이전트 호스트 네트워크 지표.

**Architecture:** 드리프트는 `deploy/k8s`를 이미지에 동봉하고(§2.1) 테스트에 있던 부분집합 YAML 파서를 `src/dms/manifest_tags.py`로 승격해 api가 `observe().images`(라이브)와 비교한다 — 비교·배지는 프론트 몫, 서버는 두 값만 준다. migrate는 api/controller initContainer로 자동화하되 PG 어드바이저리 락으로 전 경로를 직렬화한다(§2.2). 플래너는 `PlacementError`에 노드별 탈락 사유를 실어 "전원 신원 대기 + grace 안"일 때만 상태를 건드리지 않고 반환한다(§2.3) — Pending으로 남아 다음 틱에 재계획된다. 워커 파드는 라벨을 먼저 붙이고 같은 잡·같은 task 셀렉터의 required podAntiAffinity로 산개한다(§2.4). 네트워크 지표는 `/proc/1/net/dev`(호스트 netns)를 hostPath File로 마운트해 mountinfo와 같은 주입 관례로 고친다(§2.5).

**Tech Stack:** Python 3.11 표준 라이브러리(파서는 손수 짠 부분집합 YAML — PyYAML 금지), FastAPI 라우트 1건 확장, React 18 + Vitest(카드 1건), k8s 매니페스트 3파일.

## Global Constraints

- 설계 문서: `docs/superpowers/specs/2026-08-10-dms-deploy-safety-slice16-design.md`. 충돌하면 설계가 이긴다.
- **전면 fail-soft**(설계 §4): 동봉 매니페스트가 없거나 파싱 실패면 `manifest_image`는 null이고 배지를 내지 않는다 — 추측하지 않는다. 어드바이저리 락 획득 실패만 예외로 올려 initContainer를 실패시킨다(스키마가 불확실한 채 앱이 뜨는 것보다 낫다).
- **어드바이저리 락은 PostgreSQL 전용**(SQLite no-op)이고 **예외 경로에서도 반드시 해제**된다(try/finally).
- **one-shot migrate Job(`30-migrate-job.yaml`)은 유지**한다 — 명시적 실행·복구 수단.
- 플래너 유예는 **오직** (a) 사유가 `no_eligible_nodes`이고 (b) 모든 노드의 사유가 `identity_not_ready_on_node`이며 (c) 요청 나이 < `DMS_PLANNER_IDENTITY_GRACE_SECONDS`(기본 **300**)일 때만이다 — 그 외 모든 거부(스토리지 결격, `no_ready_sync_candidate`, `missing_policy` 등)는 기존대로 **즉시 거부**.
- anti-affinity는 `requiredDuringSchedulingIgnoredDuringExecution` + `topologyKey: kubernetes.io/hostname`, 셀렉터는 **같은 job의 같은 task**(`dms.io/job-id` + `dms.io/task`)로 좁힌다. 런처는 건드리지 않는다.
- **`hostNetwork`/`dnsPolicy` 변경 금지**(설계 §2.5가 기각 — dnsPolicy 동반 변경 없이는 `dms-api` DNS가 죽어 에이전트 보고가 조용히 영구 중단된다).
- **새 pip/npm 의존성 금지.** YAML 파서는 손수 짠다 — PyYAML을 들이지 않는다.
- `legacy/` 아래는 읽기 전용 — 수정·이동·삭제·추가 금지, import 금지, 코드 복사 금지.
- 백엔드 테스트: `.venv/bin/python -m pytest` (`python`은 PATH에 없다). 전체 스위트는 **포그라운드**로 Bash `timeout` 400000ms(기준선 **907 passed**). 백그라운드+Monitor 조합 금지.
- 프론트: `cd frontend && npx vitest run`, 타입체크 `npx tsc -b`.
- 주석은 한국어로 "왜"를 적는다.
- **origin으로 push 금지.** 커밋만 한다.

## 실측 고정값 (코드·매니페스트 직접 확인)

| 항목 | 값 |
|---|---|
| Dockerfile COPY 목록 | `deploy/docker/Dockerfile.dms:45-51` — `pyproject.toml`·`src`·웹 dist만. `deploy/`는 이미지에 없다. `.dockerignore`는 `deploy/`를 제외하지 않는다(COPY 가능) |
| 이미지 내 dms 패키지 위치 | `pip install '.[...]'`(비-editable)라 **site-packages**다 → `Path(__file__)` 기준 상대경로가 저장소를 못 가리킨다. 개발 체크아웃 `.venv`는 editable(실측: `dms.__file__` = `src/dms/__init__.py`) → `parents[2]` = 저장소 루트. 그래서 기본 루트는 **후보 2개**(`__file__` 상대, `/app/deploy/k8s`) 중 존재하는 첫 것 |
| 승격할 파서 | `tests/test_release_manifest_contract.py:29-126` — `_strip_comment`/`_indent`/`_documents`/`_block`/`_find`/`_value`/`_workload_doc`/`_match_labels`/`_container_names`. 계약 테스트는 `_workload_doc`·`_container_names`·`_match_labels`만 직접 쓴다 |
| 파싱 대상 5파일 | `40-api.yaml`(Deployment dms-api, 컨테이너 `api`, `image: pkg-01:5000/dms:d25`), `41-controller.yaml`(컨테이너 `controller`), `50-agent-daemonset.yaml`(컨테이너 `agent`, `pkg-01:5000/dms-agent:d23`), `30-migrate-job.yaml`(Job dms-migrate, 컨테이너 `migrate`), `20-config.yaml`(`DMS_JOB_IMAGE: "pkg-01:5000/dms-mpifileutils:job4"` — 따옴표 있음) |
| 라이브 이미지의 출처 | `rollout_status.py:19-24` `_images`가 워크로드 **파드템플릿**에서 `{container: image}`를 만든다. 소비 관례: `(obs.get("images") or {}).get(spec["container"])`(`routes_releases.py:53`) |
| 드리프트를 붙일 라우트 | `routes_metrics.py:79-142` `metrics_infra` — ThreadPoolExecutor로 observe 3건 병렬, entry dict 리터럴(:108-110)에 키 추가. `settings`는 `request.app.state.settings` |
| COMPONENTS | `repositories/releases.py:12-22` — 컨테이너 이름은 워크로드명에서 유도되지 않는다(`dms-api`→`api`, `dms-controller`→`controller`, `dms-agent`→`agent`) |
| migrate 호출자 | `cli.py:40-44`가 유일. `_ensure_columns`(`migrations.py:346-367`)는 "존재 확인 후 `ALTER TABLE ADD COLUMN`"(비 IF NOT EXISTS) — 동시 실행 경합의 근원. `db.py`: psycopg **autocommit**, `Database.dialect` ∈ {"sqlite","postgresql"}. 어드바이저리 락은 저장소 어디에도 없다 |
| initContainer 현황 | `40-api.yaml:51-90` / `41-controller.yaml:32-52` 모두 **전무**. 둘 다 `envFrom: dms-config + dms-secrets`, command `["dms","api"]`/`["dms","controller"]` |
| 플래너 거부 경로 | `planner.py:97-98` `except PlacementError` → `_reject`(:40-45)가 `set_state`+`record_result`로 종단화. `run_once`(:23-38)는 예외를 삼키고 상태 불변 → `requests.py:85-90` `list_pending`이 다음 틱에 재선출. 멱등 가드 :49-53. `record_event` 시그니처: `(*, component, severity, event_type, message=None, payload=None, request_id=None)` — 절대 예외를 올리지 않는다 |
| PlacementError | `placement.py:6-10` — `reason_code`/`detail`뿐, 추가 속성 없음. `no_eligible_nodes`는 scan(:61)·rm(:68)에서만 raise, sync는 `no_ready_sync_candidate`(:92). 노드별 사유 `identity_not_ready_on_node`는 :47. `eligible_nodes`는 `(ok, reasons)` 반환, reasons = `{node: reason}` 평면 dict |
| grace에 필요한 컬럼 | `requests.created_at`은 이미 있다(`migrations.py:30`) — **새 마이그레이션 불필요**. `iso_plus`/`utc_now_iso`(`db.py`)는 고정 포맷 `%Y-%m-%dT%H:%M:%SZ`라 문자열 비교가 시간 비교와 일치한다 |
| 워커 task 현황 | `execution_manifests.py` — 코로케이션 worker :356-362(`"affinity": _node_affinity(nodes) if nodes else {}` :360), nsync `source-worker` :315-319 / `destination-worker` :320-324, 런처 :348-355(affinity 있음)/:310-314(nsync 런처는 affinity 키 자체가 없음). task 템플릿에 `metadata` 키 **없음**. `_node_affinity`(:223-226)는 단일 키 `{"nodeAffinity": ...}` dict |
| 기존 affinity 테스트 | `test_execution_manifests.py:196-211` `test_nsync_three_tasks_node_affinity` — `["affinity"]["nodeAffinity"]`를 명시 인덱싱하므로 형제 `podAntiAffinity` 키가 생겨도 깨지지 않는다. 코로케이션 worker의 affinity 테스트는 **없다**(이번에 메운다). `_spec` 헬퍼: `job_id="j1"` 기본 |
| 에이전트 net 경로 | `probes.py:92-131` `probe_os_metrics(storages, *, read_text, statvfs)` — `/proc/loadavg`(:97)·`/proc/meminfo`(:103)·`/proc/net/dev`(:121) 하드코딩. mountinfo만 주입 가능(`AgentSettings.mountinfo_path`, `config.py:191,210`). `build_report`(`agent/runner.py:18-33`)는 `os_fn(storages, read_text=read_text)` 호출, 스텁들은 전부 `**k` 흡수. `AgentRunner.run_once`(:41-47)가 settings에서 경로를 읽어 넘기는 구조 |
| DaemonSet 마운트 관례 | `50-agent-daemonset.yaml:100-102`(volumeMount `/host/proc/1/mountinfo` readOnly) + :120-123(hostPath `type: File`) — net/dev도 같은 모양으로 |
| 테스트 스텁 | `test_planner.py:11-14` `_Settings`에 grace 속성 없음(추가 필요). `test_agent_runner.py:5-6` `SETTINGS`는 AgentSettings 직접 생성(새 필드는 기본값으로 흡수). conftest `settings` 픽스처는 `job_image=""`(기본) → 라우트의 live 잡 이미지는 None |

## 파일 구조

| 파일 | 책임 |
|---|---|
| `src/dms/manifest_tags.py` (신규) | 부분집합 YAML 파서(테스트에서 승격) + 동봉 매니페스트 이미지 조회 `manifest_images`/`manifest_job_image` |
| `tests/test_manifest_tags.py` (신규) | 실제 `deploy/k8s/*.yaml`을 그대로 파싱하는 단위 테스트 |
| `tests/test_release_manifest_contract.py` (수정) | 로컬 파서 삭제, 승격된 파서 import |
| `deploy/docker/Dockerfile.dms` (수정) | `COPY deploy/k8s /app/deploy/k8s` 동봉 |
| `src/dms/api/routes_metrics.py` (수정) | `metrics_infra`에 `manifest_image`(컴포넌트별) + `job_image`(live/manifest) 추가 |
| `frontend/src/lib/types.ts` / `features/dashboard/Dashboard.tsx`(+test) (수정) | 드리프트 배지 + 매니페스트 값 + 되돌림 경고, 잡 이미지 한 줄 |
| `src/dms/migrations.py` (수정) | `migrate()` = PG 어드바이저리 락 래퍼, 본문은 `_apply_migrations()`로 개명 |
| `deploy/k8s/40-api.yaml` / `41-controller.yaml` (수정) | `initContainers: dms migrate` |
| `src/dms/placement.py` (수정) | `PlacementError.rejections` + `no_eligible_nodes`에 사유 탑재 |
| `src/dms/planner.py` (수정) | 신원 전파 유예(`_identity_grace_active`) + `identity_propagating` 이벤트 |
| `src/dms/config.py` (수정) | `planner_identity_grace_seconds`(300) + `AgentSettings.net_dev_path` |
| `deploy/k8s/20-config.yaml` (수정) | `DMS_PLANNER_IDENTITY_GRACE_SECONDS: "300"` |
| `src/dms/execution_manifests.py` (수정) | 워커 task 라벨 + required podAntiAffinity 병합 |
| `src/dms/agent/probes.py` / `agent/runner.py` (수정) | `net_dev_path` 주입 배선 |
| `deploy/k8s/50-agent-daemonset.yaml` (수정) | `/proc/1/net/dev` hostPath File 마운트 + env |

---

### Task 1: manifest_tags.py — 파서 승격 + 동봉본 이미지 조회

**Files:**
- Create: `src/dms/manifest_tags.py`
- Create: `tests/test_manifest_tags.py`
- Modify: `tests/test_release_manifest_contract.py` (로컬 파서 삭제 → 승격본 import)

**Interfaces:**
- Consumes: 표준 라이브러리 `pathlib`만 + `dms.repositories.releases.COMPONENTS`(kind/workload/container 좌표 — 표를 중복 정의하면 계약 테스트가 지키는 단일 진실이 깨진다).
- Produces (Task 2가 이 이름·시그니처를 그대로 쓴다):
  - `manifest_images(root=None) -> dict[str, str | None]` — 키는 정확히 `{"dms-api", "dms-controller", "dms-agent", "dms-migrate"}`. 값은 해당 워크로드 문서의 컨테이너 `image:`(못 찾으면 그 항목만 None). root 미지정 시 기본 후보(`__file__` 상대 → `/app/deploy/k8s`) 중 존재하는 첫 디렉터리, 없으면 전량 None. **예외를 올리지 않는다.**
  - `manifest_job_image(root=None) -> str | None` — `20-config.yaml`의 `DMS_JOB_IMAGE` 값(따옴표 제거). 없으면 None.
  - 계약 테스트가 쓰는 승격 헬퍼: `workload_doc(path, kind, name) -> list[str] | None`, `container_names(doc) -> list[str]`, `match_labels(doc) -> dict` (뒤 둘은 assert 기반 — 추출이 빗나가면 조용히 통과하는 대신 빨간불).

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_manifest_tags.py`:

```python
"""manifest_tags 단위 테스트. 실제 deploy/k8s/*.yaml 을 그대로 파싱한다(설계 §5) --
픽스처를 합성하면 "실물 매니페스트를 읽는다"는 보증이 사라진다. 태그 숫자는 배포마다
바뀌므로 리포 접두사만 단언한다 -- 태그 핀 자체는 배포 절차(플랜 이후 절)의 몫이다."""
from pathlib import Path

from dms.manifest_tags import manifest_images, manifest_job_image

REPO_K8S = Path(__file__).resolve().parent.parent / "deploy" / "k8s"


def test_manifest_images_parses_all_four_workloads():
    images = manifest_images(REPO_K8S)
    assert set(images) == {"dms-api", "dms-controller", "dms-agent", "dms-migrate"}
    # api/controller/migrate 는 같은 dms 이미지 계보다(COMPONENTS.repository 실측)
    for comp in ("dms-api", "dms-controller", "dms-migrate"):
        assert images[comp].startswith("pkg-01:5000/dms:"), images
    assert images["dms-agent"].startswith("pkg-01:5000/dms-agent:")


def test_manifest_images_default_root_resolves_in_checkout():
    # 개발 체크아웃에서는 __file__ 기준 후보가 저장소 deploy/k8s 를 찾아야 한다 --
    # 이 배선이 끊기면 이미지 안(/app 후보)에서만 증상이 드러나 테스트가 못 잡는다.
    assert manifest_images() == manifest_images(REPO_K8S)


def test_manifest_job_image_reads_quoted_configmap_value():
    image = manifest_job_image(REPO_K8S)
    assert image.startswith("pkg-01:5000/dms-mpifileutils:")
    assert '"' not in image                       # 20-config.yaml 값의 따옴표는 벗긴다


def test_missing_root_fails_soft_to_all_none(tmp_path):
    # 동봉이 없는 이미지(현행 d25처럼 COPY 이전 빌드)에서도 라우트가 죽으면 안 된다 --
    # 전량 None 이면 프론트는 배지를 내지 않는다(설계 §4: 추측하지 않는다).
    gone = tmp_path / "nope"
    assert set(manifest_images(gone).values()) == {None}
    assert manifest_job_image(gone) is None


def test_unparseable_file_fails_soft(tmp_path):
    # metadata 없는 깨진 문서 + 나머지 파일 부재 -- 항목별 None 강등, 예외 없음
    (tmp_path / "40-api.yaml").write_text("kind: Deployment\nspec: {}\n")
    images = manifest_images(tmp_path)
    assert set(images.values()) == {None}
    assert manifest_job_image(tmp_path) is None
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_manifest_tags.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'dms.manifest_tags'`

- [ ] **Step 3: manifest_tags.py를 구현한다**

`src/dms/manifest_tags.py` — 파서 본문(`_strip_comment`~`container_names`)은 `tests/test_release_manifest_contract.py:29-126`에서 **그대로 옮긴다**(공개 이름으로 개명만). 전문:

```python
"""deploy/k8s 매니페스트에서 이미지 태그를 읽는 부분집합 YAML 파서(슬라이스 16 설계 §2.1).

tests/test_release_manifest_contract.py 에 있던 파서를 승격했다. PyYAML 은 이 저장소의
의존성이 아니다(런타임도 테스트도) -- 파서 하나를 들이는 대신 매니페스트가 실제로 쓰는
부분집합(블록 매핑/시퀀스, 주석, 다중 문서)만 읽는다. 앵커/블록 스칼라/복합 키는 이
파일들에 없다.

동봉본의 의미: Dockerfile.dms 가 deploy/k8s 를 이미지에 COPY 하므로 여기서 읽는 값은
"이 이미지를 만든 소스 트리의 매니페스트"다. 포탈 롤아웃은 매니페스트를 고치지 않으므로
롤아웃 직후에는 반드시 live != manifest 가 되어 정확히 그 위험(다음 kubectl apply 가
되돌림)을 표시한다(설계 §2.1).

두 계층의 오류 정책이 공존한다:
- 런타임 조회(manifest_images/manifest_job_image)는 전면 fail-soft(설계 §4) -- 못 찾으면
  해당 값만 None, 예외를 올리지 않는다(추측하지 않는다).
- 계약 테스트용 헬퍼(match_labels/container_names)는 assert 기반 -- 추출이 빗나가면
  조용히 통과하는 대신 테스트가 빨간불이 되어야 한다."""
from pathlib import Path

from .repositories.releases import COMPONENTS

# 롤아웃 대상 3종의 매니페스트 파일. kind/workload/container 좌표는 COMPONENTS 가
# 단일 진실이다 -- 여기 중복 정의하면 계약 테스트가 지키는 표와 어긋날 수 있다.
MANIFEST_FILES = {
    "dms-api": "40-api.yaml",
    "dms-controller": "41-controller.yaml",
    "dms-agent": "50-agent-daemonset.yaml",
}
# dms-migrate 는 COMPONENTS(롤아웃 대상)가 아니지만 같은 dms 이미지 계보의 네 번째
# image: 라인이다 -- one-shot Job 을 유지하는 한(설계 §2.2) 같은 드리프트 표면이라
# 함께 읽는다.
_MIGRATE = ("30-migrate-job.yaml", "Job", "dms-migrate", "migrate")
_CONFIGMAP = ("20-config.yaml", "ConfigMap", "dms-config")

# 동봉본 위치 후보. 이미지 안의 dms 는 pip install(비-editable)로 site-packages 에
# 들어가 __file__ 기준 상대경로가 저장소를 못 가리킨다 -- Dockerfile 이 COPY 하는
# /app/deploy/k8s 를 둘째 후보로 둔다. 개발 체크아웃(.venv editable)에서는 __file__ 이
# src/dms/ 아래라 parents[2] 가 저장소 루트다. 존재하는 첫 후보를 쓰고 없으면
# None(전량 None 반환) -- fail-soft(설계 §4).
_ROOT_CANDIDATES = (
    Path(__file__).resolve().parents[2] / "deploy" / "k8s",
    Path("/app/deploy/k8s"),
)


def _strip_comment(line: str) -> str:
    """따옴표 밖의 ' #' 이후를 버린다. 값 안의 '#'(없지만)을 지우지 않기 위해서다."""
    out, quote = [], None
    for i, ch in enumerate(line):
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            out.append(ch)
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            break
        else:
            out.append(ch)
    return "".join(out).rstrip()


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def documents(path: Path) -> "list[list[str]]":
    docs, cur = [], []
    for raw in path.read_text().splitlines():
        line = _strip_comment(raw)
        if not line.strip():
            continue
        if line.strip() == "---":
            docs.append(cur)
            cur = []
        else:
            cur.append(line)
    docs.append(cur)
    return [d for d in docs if d]


def _block(lines: "list[str]", start: int) -> "list[str]":
    """lines[start] 보다 깊게 들여쓴 연속 구간(그 키의 몸통)."""
    base = _indent(lines[start])
    body = []
    for line in lines[start + 1:]:
        if _indent(line) <= base:
            break
        body.append(line)
    return body


def _find(lines: "list[str]", key: str, indent: "int | None" = None) -> "int | None":
    for idx, line in enumerate(lines):
        if line.strip().startswith(f"{key}:") and (indent is None
                                                   or _indent(line) == indent):
            return idx
    return None


def _value(line: str) -> str:
    return line.split(":", 1)[1].strip()


def _unquote(value: str) -> str:
    # 20-config.yaml 의 DMS_JOB_IMAGE 는 따옴표로 싸여 있다 -- 벗기지 않으면 라이브
    # env 값과의 비교가 항상 불일치로 나온다.
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def workload_doc(path: Path, kind: str, name: str) -> "list[str] | None":
    for doc in documents(path):
        kind_at = _find(doc, "kind", indent=0)
        if kind_at is None or _value(doc[kind_at]) != kind:
            continue
        meta_at = _find(doc, "metadata", indent=0)
        if meta_at is None:
            continue
        meta = _block(doc, meta_at)
        name_at = _find(meta, "name")
        if name_at is not None and _value(meta[name_at]) == name:
            return doc
    return None


def match_labels(doc: "list[str]") -> dict:
    """계약 테스트 전용 -- assert 로 시끄럽게 실패한다(모듈 docstring 참고)."""
    spec_at = _find(doc, "spec", indent=0)
    assert spec_at is not None, "워크로드 문서에 최상위 spec 이 없다"
    spec = _block(doc, spec_at)
    sel_at = _find(spec, "selector", indent=_indent(spec[0]))
    assert sel_at is not None, "spec.selector 를 못 찾았다"
    ml_at = _find(_block(spec, sel_at), "matchLabels")
    assert ml_at is not None, "spec.selector.matchLabels 를 못 찾았다"
    labels = _block(_block(spec, sel_at), ml_at)
    return {line.split(":", 1)[0].strip(): _value(line) for line in labels}


def container_names(doc: "list[str]") -> "list[str]":
    # initContainers 는 'containers:' 로 시작하지 않으므로 걸리지 않는다. volumes 는
    # containers 와 같은 깊이라 _block 이 거기서 멈춘다.
    at = _find(doc, "containers")
    assert at is not None, "pod template 의 containers 를 못 찾았다"
    body = _block(doc, at)
    assert body, "containers 가 비어 있다"
    item_indent = min(_indent(line) for line in body)
    return [_value(line.strip()[2:]) for line in body
            if _indent(line) == item_indent and line.strip().startswith("- name:")]


def container_image(doc: "list[str]", container: str) -> "str | None":
    """containers 블록에서 이름이 container 인 항목의 image. 런타임 경로라 fail-soft.

    initContainers 는 'containers:' 프리픽스가 달라 _find 에 안 걸리고(Task 4 가
    40/41 에 initContainers 를 넣어도 본 컨테이너 이미지를 정확히 집는다), env 의
    `- name:` 항목들은 item_indent 보다 깊어 이름 추적을 오염시키지 않는다."""
    at = _find(doc, "containers")
    if at is None:
        return None
    body = _block(doc, at)
    if not body:
        return None
    item_indent = min(_indent(line) for line in body)
    current = None
    for line in body:
        stripped = line.strip()
        if _indent(line) == item_indent and stripped.startswith("- name:"):
            current = _value(stripped[2:])
        elif current == container and stripped.startswith("image:"):
            return _unquote(_value(line)) or None
    return None


def _root(root=None) -> "Path | None":
    if root is not None:
        root = Path(root)
        return root if root.is_dir() else None
    for cand in _ROOT_CANDIDATES:
        if cand.is_dir():
            return cand
    return None


def _image_from(path, kind, name, container) -> "str | None":
    try:
        doc = workload_doc(path, kind, name)
    except OSError:
        return None                # 파일 없음/읽기 실패 -- 그 항목만 None(설계 §4)
    if doc is None:
        return None
    return container_image(doc, container)


def manifest_images(root=None) -> "dict[str, str | None]":
    out = {c: None for c in (*MANIFEST_FILES, "dms-migrate")}
    base = _root(root)
    if base is None:
        return out
    for component, filename in MANIFEST_FILES.items():
        spec = COMPONENTS[component]
        out[component] = _image_from(base / filename, spec["kind"],
                                     spec["workload"], spec["container"])
    filename, kind, name, container = _MIGRATE
    out["dms-migrate"] = _image_from(base / filename, kind, name, container)
    return out


def manifest_job_image(root=None) -> "str | None":
    base = _root(root)
    if base is None:
        return None
    filename, kind, name = _CONFIGMAP
    try:
        doc = workload_doc(base / filename, kind, name)
    except OSError:
        return None
    if doc is None:
        return None
    data_at = _find(doc, "data", indent=0)
    if data_at is None:
        return None
    data = _block(doc, data_at)
    key_at = _find(data, "DMS_JOB_IMAGE")
    if key_at is None:
        return None
    return _unquote(_value(data[key_at])) or None
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_manifest_tags.py -q`
Expected: PASS (5 tests)

- [ ] **Step 5: 계약 테스트를 승격본으로 갈아탄다**

`tests/test_release_manifest_contract.py`에 세 가지 수정:

**(1)** 모듈 docstring의 마지막 문단(`PyYAML 은 이 저장소의...`부터 끝까지)을 다음으로 교체:

```python
파서는 슬라이스 16에서 src/dms/manifest_tags.py 로 승격됐다(api 가 런타임에도 같은
파서로 동봉 매니페스트를 읽는다 -- 드리프트 배지). 여기서는 승격본을 import 해 쓰되,
추출이 빗나가면 조용히 통과하는 대신 assert 가 빨간불이 되는 성질(match_labels/
container_names)은 그대로다.
```

**(2)** import를 교체하고 로컬 헬퍼 정의(`_strip_comment`부터 `_container_names`까지, 현행 29-126행)를 **전부 삭제**한다:

```python
from pathlib import Path

import pytest
from dms.manifest_tags import container_names, match_labels, workload_doc
from dms.repositories.releases import COMPONENTS, ROLLOUT_ORDER
```

(`REPO_ROOT`/`MANIFESTS` 상수는 테스트에 남긴다 — 파일 매핑을 독립적으로 못박는 것이 이 테스트의 역할이다.)

**(3)** 테스트 본문의 호출 3곳 개명: `_workload_doc(` → `workload_doc(`, `_container_names(` → `container_names(`, `_match_labels(` → `match_labels(`.

- [ ] **Step 6: 두 파일 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_manifest_tags.py tests/test_release_manifest_contract.py -q`
Expected: PASS (5 + 4 tests)

- [ ] **Step 7: 커밋**

```bash
git add src/dms/manifest_tags.py tests/test_manifest_tags.py tests/test_release_manifest_contract.py
git commit -m "feat(release): 부분집합 YAML 파서를 manifest_tags로 승격 — 동봉 매니페스트 이미지 조회"
```

---

### Task 2: 매니페스트 동봉(Dockerfile) + metrics_infra 드리프트 값

**Files:**
- Modify: `deploy/docker/Dockerfile.dms` (COPY 추가), `.dockerignore` (주석 1줄)
- Modify: `src/dms/api/routes_metrics.py:79-142` (`metrics_infra`)
- Modify: `tests/test_api_metrics.py`

**Interfaces:**
- Consumes (Task 1): `manifest_images() -> dict[str, str | None]`, `manifest_job_image() -> str | None` — `from ..manifest_tags import ...`
- Produces (Task 3 프론트가 이 이름을 그대로 쓴다):
  - `GET /api/admin/metrics/infra` 컴포넌트 entry에 `"manifest_image": str | null` 추가.
  - 응답 최상위에 `"job_image": {"live": str | null, "manifest": str | null}` 추가 (live = `settings.job_image or None`).
  - **비교·배지는 서버가 하지 않는다** — 두 값을 정직하게 줄 뿐(설계 §3, 프론트가 비교).

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_api_metrics.py` 파일 끝에 추가:

```python
def _stub_manifests(monkeypatch, images=None, job=None):
    # 기본 루트는 저장소의 실 deploy/k8s 를 읽는다(테스트 환경에도 존재) -- 실 태그에
    # 단언을 걸면 태그 범프마다 테스트가 깨지므로 라우트 테스트는 조회 함수를 대역화한다.
    # 실물 파싱 자체는 tests/test_manifest_tags.py 가 고정한다.
    monkeypatch.setattr(
        "dms.api.routes_metrics.manifest_images",
        lambda: images if images is not None else
        {"dms-agent": None, "dms-api": None, "dms-controller": None,
         "dms-migrate": None})
    monkeypatch.setattr("dms.api.routes_metrics.manifest_job_image", lambda: job)


def test_metrics_infra_reports_manifest_image_and_job_image(client, monkeypatch):
    client.app.state.rollout_runner = _FakeObserver()
    _stub_manifests(
        monkeypatch,
        images={"dms-agent": "pkg-01:5000/dms-agent:dev6",   # live 와 일치
                "dms-api": "pkg-01:5000/dms:d99",            # live(d23)와 드리프트
                "dms-controller": None,                      # 동봉 파싱 실패
                "dms-migrate": "pkg-01:5000/dms:d99"},
        job="pkg-01:5000/dms-mpifileutils:job9")
    body = client.get("/api/admin/metrics/infra", headers=ADMIN).json()
    by = {c["component"]: c for c in body["components"]}
    assert by["dms-agent"]["manifest_image"] == "pkg-01:5000/dms-agent:dev6"
    assert by["dms-api"]["manifest_image"] == "pkg-01:5000/dms:d99"
    assert by["dms-controller"]["manifest_image"] is None    # null -> 프론트 무배지
    # conftest 의 settings 는 job_image="" (기본) -- 빈 문자열은 None 으로 접는다
    assert body["job_image"] == {"live": None,
                                 "manifest": "pkg-01:5000/dms-mpifileutils:job9"}


def test_metrics_infra_manifest_fail_soft_all_none(client, monkeypatch):
    # 동봉본이 없는 이미지(COPY 이전 빌드)에서도 라우트는 200 -- 값만 전량 null(설계 §4)
    client.app.state.rollout_runner = _FakeObserver()
    _stub_manifests(monkeypatch)
    r = client.get("/api/admin/metrics/infra", headers=ADMIN)
    assert r.status_code == 200
    body = r.json()
    assert all(c["manifest_image"] is None for c in body["components"])
    assert body["job_image"] == {"live": None, "manifest": None}
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_api_metrics.py -q`
Expected: FAIL — 새 2건이 `KeyError: 'manifest_image'`(및 `AttributeError: ... manifest_images` — monkeypatch 대상 부재). 기존 테스트는 PASS 유지.

- [ ] **Step 3: 라우트를 구현한다**

`src/dms/api/routes_metrics.py`에 세 가지 수정:

**(1)** import 추가 — `from ..metrics_series import ...` 블록 아래:

```python
from ..manifest_tags import manifest_images, manifest_job_image
```

**(2)** `metrics_infra` 본문에서 `runner = request.app.state.rollout_runner` 줄을 다음으로 교체:

```python
    runner = request.app.state.rollout_runner
    settings = request.app.state.settings
    # 동봉 매니페스트(이미지에 COPY 된 스냅샷)는 프로세스 수명 동안 불변이지만 수 KB
    # 텍스트 5개라 요청마다 읽어도 5s 폴링에 부담이 없다 -- 캐시 없는 단순성을 택한다.
    # 못 읽으면 값이 전부 None 일 뿐 예외가 없다(설계 §4 fail-soft).
    manifest = manifest_images()
```

**(3)** entry dict 리터럴(:108-110)에 `manifest_image`를 추가하고, 마지막 `return`을 교체:

```python
        entry = {"component": component, "kind": spec["kind"],
                 "workload": spec["workload"], "image": None, "ready": None,
                 "desired": None, "verdict": None, "detail": None,
                 # 비교는 프론트가 한다 -- 서버는 "이 이미지를 만든 소스 트리의
                 # 매니페스트" 값을 정직하게 실어줄 뿐이다(설계 §2.1/§3).
                 "manifest_image": manifest.get(component)}
```

```python
    # 잡 이미지도 같은 위험이다(설계 §2.1): api 가 실제로 든 env 값(live) vs 동봉
    # 20-config.yaml 값(manifest). 빈 문자열(미설정)은 None 으로 접어 프론트가
    # "비교 불가"와 "불일치"를 헷갈리지 않게 한다.
    return {"components": components,
            "job_image": {"live": settings.job_image or None,
                          "manifest": manifest_job_image()}}
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_api_metrics.py tests/test_manifest_tags.py -q`
Expected: 전부 PASS (기존 infra 테스트 4건은 실 동봉본을 읽지만 `manifest_image` 키를 단언하지 않아 무영향)

- [ ] **Step 5: Dockerfile에 동봉을 추가한다**

`deploy/docker/Dockerfile.dms`의

```dockerfile
COPY pyproject.toml /app/
COPY src /app/src
```

를

```dockerfile
COPY pyproject.toml /app/
COPY src /app/src

# 배포 매니페스트 동봉(슬라이스 16 설계 §2.1): api 가 manifest_tags.py 로 이 사본을
# 읽어 라이브 워크로드 이미지와 비교한다(드리프트 배지). 이 사본은 "이 이미지를 만든
# 소스 트리의 매니페스트"라 의미가 정확하다 -- 포탈 롤아웃은 매니페스트를 고치지
# 않으므로 롤아웃 직후에는 반드시 불일치가 표시된다(의도된 동작). dms 패키지는 pip
# install 로 site-packages 에 들어가므로 경로는 /app/deploy/k8s 고정이다(manifest_tags
# 의 _ROOT_CANDIDATES 둘째 후보).
COPY deploy/k8s /app/deploy/k8s
```

로 교체하고, `.dockerignore`의 `# Do NOT exclude anything Dockerfile.dms COPYs: pyproject.toml, src/, or` 줄을

```
# Do NOT exclude anything Dockerfile.dms COPYs: pyproject.toml, src/, deploy/k8s, or
```

로 교체한다. (이미지 빌드는 플랜 밖 — 동봉 경로가 맞는지는 실증 1이 배지 부재로 확인한다.)

- [ ] **Step 6: 커밋**

```bash
git add deploy/docker/Dockerfile.dms .dockerignore src/dms/api/routes_metrics.py tests/test_api_metrics.py
git commit -m "feat(api): metrics/infra에 동봉 매니페스트 이미지 — 드리프트 비교 재료(live vs manifest)"
```

---

### Task 3: 대시보드 드리프트 배지 + 잡 이미지 한 줄

**Files:**
- Modify: `frontend/src/lib/types.ts:186-191`
- Modify: `frontend/src/features/dashboard/Dashboard.tsx` (컴포넌트 카드)
- Modify: `frontend/src/features/dashboard/Dashboard.test.tsx`

**Interfaces:**
- Consumes (Task 2): `InfraComponent.manifest_image`, `InfraMetrics.job_image.{live,manifest}`.
- Produces: 화면 규칙(설계 §3) — live·manifest 둘 다 있고 다를 때**만** `드리프트` 배지 + `매니페스트 <값> — 다음 kubectl apply가 이 태그로 되돌립니다` 한 줄. 일치하거나 어느 한쪽이 null이면 **아무것도 안 낸다**. 잡 이미지 불일치도 같은 카드 하단에 한 줄.

- [ ] **Step 1: 단언을 먼저 써 실패하는 테스트를 만든다**

`frontend/src/features/dashboard/Dashboard.test.tsx`에 세 가지 수정:

**(1)** `INFRA` 상수를 새 계약으로 교체(일치 1 + null 1 — 기본 렌더는 무배지):

```tsx
const INFRA = {
  components: [
    { component: "dms-agent", kind: "DaemonSet", workload: "dms-agent",
      image: "pkg-01:5000/dms-agent:dev6", ready: 5, desired: 5,
      verdict: "applied", detail: null,
      manifest_image: "pkg-01:5000/dms-agent:dev6" },        // live 와 일치
    { component: "dms-api", kind: "Deployment", workload: "dms-api",
      image: null, ready: null, desired: null, verdict: null, detail: null,
      manifest_image: null },                                 // 동봉 없음
  ],
  job_image: { live: null, manifest: null },
};
```

**(2)** `renderDash`의 infra 핸들러를 오버라이드 가능하게 교체:

```tsx
    http.get("/api/admin/metrics/infra",
             () => HttpResponse.json(overrides.infra ?? INFRA)),
```

**(3)** 파일 끝에 테스트 2건 추가:

```tsx
const DRIFTED = {
  components: [
    { component: "dms-agent", kind: "DaemonSet", workload: "dms-agent",
      image: "pkg-01:5000/dms-agent:dev7", ready: 5, desired: 5,
      verdict: "applied", detail: null,
      manifest_image: "pkg-01:5000/dms-agent:dev6" },
  ],
  job_image: { live: "pkg-01:5000/dms-mpifileutils:job4",
               manifest: "pkg-01:5000/dms-mpifileutils:job5" },
};

test("라이브가 동봉 매니페스트와 다르면 드리프트 배지와 되돌림 경고를 낸다", async () => {
  renderDash({ infra: DRIFTED });
  expect(await screen.findByText("드리프트")).toBeInTheDocument();
  expect(screen.getByText(
    "매니페스트 pkg-01:5000/dms-agent:dev6 — 다음 kubectl apply가 이 태그로 되돌립니다",
  )).toBeInTheDocument();
  expect(screen.getByText(
    "잡 이미지 pkg-01:5000/dms-mpifileutils:job4 · 매니페스트 pkg-01:5000/dms-mpifileutils:job5 — 다음 kubectl apply가 매니페스트 값으로 되돌립니다",
  )).toBeInTheDocument();
});

test("일치하거나 매니페스트가 null이면 아무 배지도 내지 않는다", async () => {
  // 기본 INFRA: dms-agent 일치 + dms-api null + job_image null -- 전부 무배지(설계 §3/§4)
  renderDash();
  expect(await screen.findByText("dms-agent")).toBeInTheDocument();
  expect(screen.queryByText("드리프트")).toBeNull();
  expect(screen.queryByText(/되돌립니다/)).toBeNull();
});
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd frontend && npx vitest run src/features/dashboard/Dashboard.test.tsx`
Expected: FAIL — `findByText("드리프트")` 타임아웃 (배지 미구현)

- [ ] **Step 3: 타입과 카드를 구현한다**

**(1)** `frontend/src/lib/types.ts`의 `InfraComponent`/`InfraMetrics`를 교체:

```ts
export interface InfraComponent {
  component: string; kind: string; workload: string;
  image: string | null; ready: number | null; desired: number | null;
  verdict: "applied" | "progressing" | "failed" | null; detail: string | null;
  // 이미지에 동봉된 "이 이미지를 만든 소스 트리"의 매니페스트 image 값.
  // null = 동봉 없음/파싱 실패 -- 비교 자체를 하지 않는다(무배지).
  manifest_image: string | null;
}
export interface InfraMetrics {
  components: InfraComponent[];
  job_image: { live: string | null; manifest: string | null };
}
```

**(2)** `frontend/src/features/dashboard/Dashboard.tsx` — import에 `InfraComponent` 타입 추가:

```tsx
import type { InfraComponent, StateCount } from "../../lib/types";
```

`Dashboard()` 본문에서 `const components = ...` 아래에 추가:

```tsx
  const jobImage = infraQ.data?.job_image;
  // 드리프트 = live(워크로드 파드템플릿)와 동봉 매니페스트가 "둘 다 있고" 다르다.
  // 어느 한쪽이 null 이면 비교하지 않는다 -- 추측 금지(설계 §4).
  const drifted = (c: InfraComponent) =>
    c.image != null && c.manifest_image != null && c.image !== c.manifest_image;
```

컴포넌트 카드(`<Card>` 첫 번째)를 다음으로 교체:

```tsx
        <Card>
          <h2 className="font-medium mb-3">컴포넌트</h2>
          <ul className="space-y-2 text-sm">
            {components.map((c) => (
              <li key={c.component} className="space-y-1">
                <div className="flex items-center gap-2">
                  <span className="shrink-0">{c.component}</span>
                  <span className="text-muted text-xs truncate grow">
                    {c.image ?? "—"}
                  </span>
                  {drifted(c) && <StatusPill state="드리프트" variant="bad" />}
                  <span className="text-xs tabular-nums shrink-0">
                    {`${c.ready ?? "—"}/${c.desired ?? "—"}`}
                  </span>
                  <StatusPill state={c.verdict ?? "unknown"}
                              variant={c.verdict ? VERDICT_VARIANT[c.verdict] : "neutral"} />
                </div>
                {drifted(c) && (
                  <p className="text-xs text-bad">
                    {`매니페스트 ${c.manifest_image} — 다음 kubectl apply가 이 태그로 되돌립니다`}
                  </p>
                )}
              </li>
            ))}
          </ul>
          {jobImage?.live && jobImage?.manifest && jobImage.live !== jobImage.manifest && (
            <p className="mt-3 text-xs text-bad">
              {`잡 이미지 ${jobImage.live} · 매니페스트 ${jobImage.manifest} — 다음 kubectl apply가 매니페스트 값으로 되돌립니다`}
            </p>
          )}
        </Card>
```

(경고 문구는 템플릿 리터럴 **한 개의 텍스트 노드**로 만든다 — JSX 보간으로 쪼개면 testing-library `getByText`가 문자열 전체를 못 찾는다.)

- [ ] **Step 4: 통과를 확인한다**

Run: `cd frontend && npx vitest run` 그리고 `npx tsc -b`
Expected: 전체 PASS, 타입 에러 0 (기존 "컴포넌트 카드" 테스트는 행 구조가 li>div로 바뀌어도 텍스트 단언뿐이라 그대로 통과)

- [ ] **Step 5: 커밋**

```bash
git add frontend/src/lib/types.ts frontend/src/features/dashboard/Dashboard.tsx frontend/src/features/dashboard/Dashboard.test.tsx
git commit -m "feat(portal): 컴포넌트 카드 드리프트 배지 — 매니페스트 값과 kubectl apply 되돌림 경고"
```

---

### Task 4: migrate 어드바이저리 락 + api/controller initContainer

**Files:**
- Modify: `src/dms/migrations.py:15` (`migrate` → 락 래퍼 + `_apply_migrations`)
- Modify: `tests/test_migrations.py`
- Modify: `deploy/k8s/40-api.yaml`, `deploy/k8s/41-controller.yaml`, `deploy/k8s/30-migrate-job.yaml`(주석 1줄)

**Interfaces:**
- Consumes: `Database.dialect`(`"sqlite"`/`"postgresql"`), `Database.execute` — psycopg 연결은 autocommit(db.py)이라 `pg_advisory_lock`이 트랜잭션 경계와 무관하게 세션에 붙는다.
- Produces: `migrate(db)` 시그니처·공개 이름 불변(cli.py:40-44, conftest.py가 그대로 쓴다). 새 모듈 상수 `MIGRATE_LOCK_KEY`, 내부 함수 `_apply_migrations(db)`(기존 migrate 본문 전체). initContainer는 두 Deployment 모두 `dms migrate`를 앱보다 먼저 실행.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_migrations.py` — 파일 맨 위 import에 `import pytest`를 추가하고, `_FakeDb` 클래스 뒤에 3건 추가 (`_FakeDb`는 `dialect`/`executed`/`execute`를 이미 갖춰 그대로 재사용):

```python
def test_migrate_pg_wraps_schema_in_advisory_lock(monkeypatch):
    # initContainer 도입으로 api/controller 가 동시에 migrate 를 돌린다 --
    # _ensure_columns 의 "확인 후 ALTER"가 경합하면 뒤쪽이 42701 로 죽는다(설계 §2.2).
    # 실 PG 하니스가 없으므로 "락 SQL 이 스키마 적용을 감싸는 순서" 자체를 고정한다.
    from dms import migrations
    fake = _FakeDb("postgresql", "bigint")
    monkeypatch.setattr(migrations, "_apply_migrations",
                        lambda db: fake.executed.append("SCHEMA"))
    migrations.migrate(fake)
    assert fake.executed == ["SELECT pg_advisory_lock(:k)", "SCHEMA",
                             "SELECT pg_advisory_unlock(:k)"]


def test_migrate_pg_releases_lock_on_exception(monkeypatch):
    # 세션 락은 커넥션이 살아 있는 한 남는다 -- 예외 경로에서 해제를 빼먹으면 같은
    # 커넥션을 재사용하는 다음 migrate 가 영원히 대기한다(설계 §5).
    from dms import migrations
    fake = _FakeDb("postgresql", "bigint")

    def boom(db):
        raise RuntimeError("column already exists")
    monkeypatch.setattr(migrations, "_apply_migrations", boom)
    with pytest.raises(RuntimeError):
        migrations.migrate(fake)
    assert fake.executed == ["SELECT pg_advisory_lock(:k)",
                             "SELECT pg_advisory_unlock(:k)"]


def test_migrate_sqlite_issues_no_advisory_lock(monkeypatch):
    # SQLite 에 pg_advisory_lock 을 치면 즉사한다 -- 방언 분기 자체를 고정한다.
    from dms import migrations
    fake = _FakeDb("sqlite", "integer")
    monkeypatch.setattr(migrations, "_apply_migrations",
                        lambda db: fake.executed.append("SCHEMA"))
    migrations.migrate(fake)
    assert fake.executed == ["SCHEMA"]
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_migrations.py -q`
Expected: FAIL — 새 3건이 `AttributeError: ... has no attribute '_apply_migrations'`

- [ ] **Step 3: migrate를 락 래퍼로 감싼다**

`src/dms/migrations.py`에서 기존 `def migrate(db: Database) -> None:`(15행)를 `def _apply_migrations(db: Database) -> None:`로 **개명**하고(본문 무변경), 그 바로 앞에 다음을 삽입한다:

```python
# PostgreSQL 어드바이저리 락 키(임의 64비트 상수 "DMS\x10"). migrate() 전체를
# 직렬화하는 전역 락이라 값 자체에 의미는 없다 -- 이 저장소의 유일한 어드바이저리 락
# 사용처라 충돌도 없다. 바꾸면 구/신 이미지가 서로 다른 락을 잡아 경합이 부활한다.
MIGRATE_LOCK_KEY = 0x444D5310


def migrate(db: Database) -> None:
    """스키마 적용 진입점(시그니처 불변 -- cli.py 와 테스트 conftest 가 그대로 쓴다).

    initContainer 도입(슬라이스 16 설계 §2.2)으로 api·controller 두 파드가 동시에
    이걸 돌린다. _ensure_columns 가 "존재 확인 후 ALTER"(비 IF NOT EXISTS)라 동시
    실행이면 뒤쪽이 42701(column already exists)로 죽는다 -- pg_advisory_lock 으로
    전 구간을 직렬화한다. psycopg 연결은 autocommit(db.py)이라 락이 트랜잭션 경계와
    무관하게 세션에 붙는다. SQLite 는 로컬 단일 파일(개발·테스트 전용)이라 no-op.
    락 획득 실패(연결 단절 등)는 그대로 예외로 올라가 initContainer 를 실패시킨다 --
    스키마가 불확실한 채 앱이 뜨는 것보다 낫다(설계 §4). one-shot Job
    (30-migrate-job.yaml)도 같은 경로를 지나므로 함께 안전해진다."""
    locked = db.dialect == "postgresql"
    if locked:
        db.execute("SELECT pg_advisory_lock(:k)", {"k": MIGRATE_LOCK_KEY})
    try:
        _apply_migrations(db)
    finally:
        if locked:
            # 예외 경로에서도 반드시 해제(설계 §5) -- 세션 락은 커넥션이 살아 있는 한
            # 남아, 같은 커넥션의 다음 migrate 를 영원히 기다리게 한다.
            db.execute("SELECT pg_advisory_unlock(:k)", {"k": MIGRATE_LOCK_KEY})
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_migrations.py tests/test_migrations_batch.py tests/test_migrations_policy_seed.py -q`
Expected: 전부 PASS (SQLite 실 migrate 경로는 래퍼를 그대로 통과)

- [ ] **Step 5: initContainer를 두 Deployment에 넣는다**

`deploy/k8s/40-api.yaml`의 `serviceAccountName: dms-api` 줄과 `containers:` 줄 사이에 삽입:

```yaml
      # 스키마 변경 배포가 자동으로 마이그레이션되도록 앱보다 먼저 dms migrate 를
      # 돌린다(슬라이스 16 설계 §2.2 -- 슬라이스 14·15는 이게 없어 실 500/수동
      # 재실행이 났다). api·controller 가 동시에 떠도 migrate() 안의 PG 어드바이저리
      # 락이 직렬화한다. one-shot Job(30-migrate-job.yaml)은 명시적 실행·복구
      # 수단으로 유지된다. 이미지는 본 컨테이너와 같은 태그를 쓴다 -- 포탈 롤아웃
      # 패치는 본 컨테이너만 갱신하므로, 매니페스트를 먼저 고치는 배포 관례가 이
      # 태그의 단일 진실이다(어긋나면 드리프트 배지가 표시한다).
      initContainers:
        - name: migrate
          image: pkg-01:5000/dms:d25
          imagePullPolicy: IfNotPresent
          command: ["dms", "migrate"]
          envFrom:
            - configMapRef:
                name: dms-config
            - secretRef:
                name: dms-secrets
          resources:
            requests:
              cpu: 50m
              memory: 128Mi
            limits:
              cpu: 500m
              memory: 512Mi
```

`deploy/k8s/41-controller.yaml`의 `serviceAccountName: dms-controller` 줄과 `containers:` 줄 사이에 **같은 블록**(주석은 `# 40-api.yaml 의 initContainer 와 동일 -- 근거 주석은 그쪽에.` 한 줄로 축약)을 삽입:

```yaml
      # 40-api.yaml 의 initContainer 와 동일 -- 근거 주석은 그쪽에.
      initContainers:
        - name: migrate
          image: pkg-01:5000/dms:d25
          imagePullPolicy: IfNotPresent
          command: ["dms", "migrate"]
          envFrom:
            - configMapRef:
                name: dms-config
            - secretRef:
                name: dms-secrets
          resources:
            requests:
              cpu: 50m
              memory: 128Mi
            limits:
              cpu: 500m
              memory: 512Mi
```

`deploy/k8s/30-migrate-job.yaml` 머리 주석 끝(6행 `... identity_resolver).` 뒤)에 한 줄 추가:

```yaml
# 슬라이스 16부터 api/controller initContainer 가 기동 시 자동 실행하므로, 이 Job 은
# 명시적 실행·복구 수단으로 유지된다(설계 §2.2).
```

- [ ] **Step 6: 파서·계약이 initContainer에 흔들리지 않음 + 전체 회귀를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_manifest_tags.py tests/test_release_manifest_contract.py -q`
Expected: PASS — `container_image`/`container_names`는 `initContainers:` 블록을 `containers:`로 오인하지 않는다(프리픽스 불일치 + 블록 경계).

Run: `.venv/bin/python -m pytest -q` (포그라운드, Bash timeout 400000ms)
Expected: 전부 PASS — migrate 래퍼는 모든 테스트의 conftest 경로다(회귀 여기서 잡는다).

- [ ] **Step 7: 커밋**

```bash
git add src/dms/migrations.py tests/test_migrations.py deploy/k8s/40-api.yaml deploy/k8s/41-controller.yaml deploy/k8s/30-migrate-job.yaml
git commit -m "feat(migrate): initContainer 자동 마이그레이션 + PG 어드바이저리 락 직렬화"
```

---

### Task 5: 플래너 신원 전파 유예

**Files:**
- Modify: `src/dms/placement.py:6-10,57-69` (PlacementError.rejections + no_eligible_nodes 탑재)
- Modify: `src/dms/planner.py:97-98` (유예 분기 + 이벤트)
- Modify: `src/dms/config.py` (`_SERVER_INT_KEYS` + Settings 필드)
- Modify: `deploy/k8s/20-config.yaml` (키 1개)
- Modify: `tests/test_placement.py`, `tests/test_planner.py`, `tests/test_config_phase3c.py`

**Interfaces:**
- Consumes: `requests.created_at`(이미 존재 — 마이그레이션 불필요), `iso_plus`/`utc_now_iso`(db.py — 고정 포맷이라 문자열 비교 = 시간 비교), `observability.record_event`(절대 예외를 올리지 않는다).
- Produces:
  - `PlacementError.__init__(reason_code, detail="", *, rejections=None)` — `self.rejections: dict[str, str]`(기본 `{}`). scan/rm의 `no_eligible_nodes`만 노드별 사유를 싣는다. sync의 `no_ready_sync_candidate`는 그대로(유예 대상 아님 — 설계 §2.3은 `no_eligible_nodes` 한정).
  - `Settings.planner_identity_grace_seconds: int = 300` (`DMS_PLANNER_IDENTITY_GRACE_SECONDS`).
  - `Planner.run_once` 결과 어휘에 `"deferred:identity_propagating"` 추가 — 상태·results 무변경, `identity_propagating` 이벤트(severity `info`, payload `{"rejections": {...}}`) 기록.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

**(1)** `tests/test_placement.py`의 `test_no_candidates_raise` 아래에 추가:

```python
def test_no_eligible_nodes_error_carries_rejections():
    # 플래너 유예(설계 §2.3)가 "전원이 신원 대기인가"를 예외에서 직접 읽는다 --
    # rejections 가 안 실리면 신원 지연과 진짜 결격(미마운트)을 가를 수 없다.
    reports = [_report("n1", mounts=[_mount("s1")], identities=("bob",))]
    with pytest.raises(PlacementError) as e:
        select_tool_and_candidates("scan", reports, storage_name="s1",
                                   owner="alice", privileged=False)
    assert e.value.reason_code == "no_eligible_nodes"
    assert e.value.rejections == {"n1": "identity_not_ready_on_node"}
```

**(2)** `tests/test_planner.py` — `_Settings`에 속성 추가:

```python
class _Settings:
    agent_report_stale_seconds = 300
    allow_privileged_requesters = False
    privileged_requesters = frozenset()
    planner_identity_grace_seconds = 300
```

`_seed_report` 아래에 헬퍼 2개 추가:

```python
def _seed_identity_pending_report(repos, node="n1", storage="s1"):
    # 마운트·도구는 전부 Ready, 신원만 미전파 -- 유예 대상의 정확한 형태(설계 §2.3).
    # identities 빈 목록 = 에이전트가 아직 alice 를 프로브 대상으로 못 받은 상태.
    repos.agents.ingest(node, {
        "node_name": node,
        "mounts": [{"storage_name": storage, "mount_path": f"/mnt/{storage}",
                    "status": "Ready", "writable": True}],
        "tools": [{"name": t, "status": "Ready"}
                  for t in ("dscan", "dsync", "nsync", "drm")],
        "identities": []},
        reported_at="2026-08-02T09:59:00Z")


def _backdate(db, rid, created_at):
    # repos.requests.create 는 created_at 을 벽시계로 넣는다 -- grace 판정을
    # 결정적으로 만들려면 NOW(고정 시각) 기준으로 나이를 직접 심어야 한다.
    db.execute("UPDATE requests SET created_at = :c WHERE request_id = :id",
               {"c": created_at, "id": rid})
```

파일 끝에 테스트 4건 추가:

```python
def test_identity_only_rejection_defers_within_grace(db):
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_identity_pending_report(repos)
    rid = _scan_request(repos)
    _backdate(db, rid, "2026-08-02T09:58:00Z")        # 나이 120s < grace 300s
    result = _planner(repos).run_once(now_iso=NOW)
    assert result[rid] == "deferred:identity_propagating"
    # 아무 상태도 바꾸지 않는다(설계 §2.3) -- Pending 으로 남아 다음 틱의
    # list_pending 에 다시 걸린다. results 행(종단)도 물론 없다.
    assert repos.requests.get(rid)["state"] == "Pending"
    assert repos.data_jobs.list_jobs(request_id=rid) == []
    # 유예는 관측 가능해야 한다 -- 매 유예마다 이벤트(설계 §2.3)
    events = repos.observability.events_for_request(rid)
    assert [e["event_type"] for e in events] == ["identity_propagating"]
    assert events[0]["severity"] == "info"
    assert events[0]["payload"] == {
        "rejections": {"n1": "identity_not_ready_on_node"}}


def test_identity_grace_expired_rejects(db):
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_identity_pending_report(repos)
    rid = _scan_request(repos)
    _backdate(db, rid, "2026-08-02T09:54:00Z")        # 나이 360s > grace 300s
    assert _planner(repos).run_once(now_iso=NOW)[rid] == "rejected:no_eligible_nodes"
    assert repos.requests.get(rid)["state"] == "Rejected"


def test_mixed_rejections_reject_immediately(db):
    # 신원 대기가 섞여 있어도 다른 결격(마운트 없음)이 하나라도 있으면 즉시 거부 --
    # 유예는 "모든 노드가 신원 대기"일 때만이다(설계 §2.3).
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos)
    _seed_identity_pending_report(repos, node="n1")
    repos.agents.ingest("n2", {"node_name": "n2", "mounts": [],
        "tools": [{"name": "dscan", "status": "Ready"}], "identities": []},
        reported_at="2026-08-02T09:59:00Z")
    rid = _scan_request(repos)
    _backdate(db, rid, "2026-08-02T09:58:00Z")        # grace 안이어도
    assert _planner(repos).run_once(now_iso=NOW)[rid] == "rejected:no_eligible_nodes"


def test_deferred_request_plans_after_identity_propagates(db):
    # 슬라이스 15 실증에서 실패했던 바로 그 시나리오(설계 §6-4): 첫 요청이 전파를
    # 기다렸다가 자동 성공해야 한다.
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_identity_pending_report(repos)
    rid = _scan_request(repos)
    _backdate(db, rid, "2026-08-02T09:58:00Z")
    planner = _planner(repos)
    assert planner.run_once(now_iso=NOW)[rid] == "deferred:identity_propagating"
    _seed_report(repos)                                # 신원 전파 완료(alice Ready)
    assert planner.run_once(now_iso=NOW)[rid] == "planned"
    assert repos.requests.get(rid)["state"] == "Planned"
```

**(3)** `tests/test_config_phase3c.py` 끝에 추가:

```python
def test_planner_identity_grace_default_and_override():
    # _SERVER_INT_KEYS 등록이 빠지면 기본값만 계속 쓰이는 조용한 회귀 --
    # DMS_BUILD_* 와 같은 이유로 from_env 경유를 고정한다.
    assert Settings.from_env(VALID).planner_identity_grace_seconds == 300
    s = Settings.from_env({**VALID, "DMS_PLANNER_IDENTITY_GRACE_SECONDS": "60"})
    assert s.planner_identity_grace_seconds == 60
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_placement.py tests/test_planner.py tests/test_config_phase3c.py -q`
Expected: FAIL — placement 1건(`AttributeError: rejections`), planner 3건(`rejected:no_eligible_nodes` ≠ `deferred:...`), config 1건(`AttributeError: planner_identity_grace_seconds`). `test_identity_grace_expired_rejects`는 구현 전에도 우연히 통과할 수 있다(현행이 항상 즉시 거부) — 나머지의 RED가 계약을 증명한다.

- [ ] **Step 3: 구현한다**

**(1)** `src/dms/placement.py` — `PlacementError`를 교체:

```python
class PlacementError(Exception):
    def __init__(self, reason_code: str, detail: str = "", *, rejections=None):
        self.reason_code = reason_code
        self.detail = detail
        # 노드별 탈락 사유({node: reason}). 플래너의 신원 전파 유예(설계 §2.3)가
        # "왜 0대인가"를 여기서 읽는다 -- 이것 없이는 신원 지연과 진짜 결격
        # (미마운트·도구 없음)을 가를 수 없다.
        self.rejections = dict(rejections or {})
        super().__init__(f"{reason_code}: {detail}" if detail else reason_code)
```

scan/rm의 raise 2곳(`:61`, `:68`)을 각각 교체:

```python
        if not nodes:
            raise PlacementError("no_eligible_nodes", storage_name, rejections=rej)
```

(sync의 `no_ready_sync_candidate`·`invalid_operation`·`resolve_fanout` 계열은 그대로 — 유예는 `no_eligible_nodes` 한정이다, 설계 §2.3.)

**(2)** `src/dms/planner.py` — import에 추가:

```python
from .db import iso_plus, utc_now_iso
```

`_reject` 아래에 메서드 추가:

```python
    def _identity_grace_active(self, req, exc, now_iso):
        """설계 §2.3의 3중 조건: (a) 사유가 no_eligible_nodes, (b) 모든 노드의 탈락
        사유가 identity_not_ready_on_node, (c) 요청 나이 < grace. 셋 다 참일 때만
        유예한다. rejections 가 비면(신선한 리포트 0건) 신원 문제라는 증거가 없다 --
        유예하지 않는다. grace 를 짧게(기본 300s -- 최악 전파 130s 의 2배 남짓) 두는
        이유: 같은 resource_key 의 후속 요청이 find_active 에 걸려 Conflict 가 되므로
        무한정 붙잡으면 안 된다."""
        if exc.reason_code != "no_eligible_nodes" or not exc.rejections:
            return False
        if any(r != "identity_not_ready_on_node" for r in exc.rejections.values()):
            return False
        now = now_iso or utc_now_iso()
        return now < iso_plus(req["created_at"],
                              self._settings.planner_identity_grace_seconds)
```

step 5의 `except PlacementError`(:97-98)를 교체:

```python
        except PlacementError as exc:
            # 신원 전파만이 원인이고 grace 안이면 아무 상태도 바꾸지 않는다 -- 요청은
            # Pending 으로 남아 다음 틱(list_pending)에 재계획된다(설계 §2.3).
            if self._identity_grace_active(req, exc, now_iso):
                # 유예는 관측 가능해야 한다 -- 매 유예마다 이벤트. record_event 는
                # 절대 예외를 올리지 않으므로(observability 계약) 유예 자체는 안전하다.
                self._repos.observability.record_event(
                    component="planner", severity="info",
                    event_type="identity_propagating",
                    message=("identity not ready on: "
                             + ", ".join(sorted(exc.rejections)))[:500],
                    payload={"rejections": exc.rejections}, request_id=rid)
                return "deferred:identity_propagating"
            return self._reject(rid, exc.reason_code)
```

(step 6 `resolve_fanout`의 `except PlacementError`(:104-105)는 **그대로** — `missing_policy`/`policy_disabled`는 유예 대상이 아니다.)

**(3)** `src/dms/config.py` — `_SERVER_INT_KEYS`의 `("DMS_PLANNER_INTERVAL_SECONDS", ...)` 항목 아래에 추가:

```python
    # 신원 전파 유예 창(설계 §2.3). 최악 전파 ≈130s(보고 60s×2 + 플래너 10s)의 2배
    # 남짓. 늘리기 전에: 같은 resource_key 후속 요청이 Conflict 로 죽는 시간도 같이
    # 늘어난다(planner 의 find_active 게이트).
    ("DMS_PLANNER_IDENTITY_GRACE_SECONDS", "planner_identity_grace_seconds", 300),
```

`Settings` dataclass의 `planner_interval_seconds: int = 10` 아래에 추가:

```python
    planner_identity_grace_seconds: int = 300
```

**(4)** `deploy/k8s/20-config.yaml` — `DMS_PLANNER_INTERVAL_SECONDS: "10"` 줄 아래에 추가:

```yaml
  # 슬라이스 16: 신규 사용자의 첫 요청이 노드 신원 전파(최악 ≈130s) 전에 도착하면
  # 즉시 거부하지 않고 이 시간 안에서 Pending 유예 후 재계획한다(설계 §2.3).
  # 신원 외 사유(미마운트 등)는 기존대로 즉시 거부된다.
  DMS_PLANNER_IDENTITY_GRACE_SECONDS: "300"
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_placement.py tests/test_planner.py tests/test_config_phase3c.py tests/test_controller_planner.py -q`
Expected: 전부 PASS — 특히 기존 `test_no_candidates_when_no_fresh_report`(빈 rejections → 즉시 거부)와 `test_worker_pool_records_rejections`가 그대로여야 한다.

- [ ] **Step 5: 커밋**

```bash
git add src/dms/placement.py src/dms/planner.py src/dms/config.py deploy/k8s/20-config.yaml tests/test_placement.py tests/test_planner.py tests/test_config_phase3c.py
git commit -m "feat(planner): 신원 전파 유예 — identity-only 거절은 grace(300s) 안에서 Pending 유지"
```

---

### Task 6: 워커 파드 라벨 + required podAntiAffinity

**Files:**
- Modify: `src/dms/execution_manifests.py` (헬퍼 2개 + 워커 task 3곳)
- Modify: `tests/test_execution_manifests.py`

**Interfaces:**
- Consumes: `_node_affinity(nodes)`(:223-226, 단일 키 dict — 무변경), `spec.job_id`.
- Produces:
  - `_worker_task_metadata(spec, task_name) -> {"labels": {"dms.io/job-id", "dms.io/task"}}`
  - `_worker_affinity(spec, task_name, nodes)` — `_node_affinity` 반환에 `podAntiAffinity`(required, `topologyKey: kubernetes.io/hostname`, 셀렉터 = 같은 job·같은 task) 병합.
  - 적용 대상: 코로케이션 `worker`, nsync `source-worker`/`destination-worker`. **런처는 불변**(라벨 없음, podAntiAffinity 없음).

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_execution_manifests.py` 끝에 추가:

```python
def _anti_affinity_rule(task):
    rules = task["template"]["spec"]["affinity"]["podAntiAffinity"][
        "requiredDuringSchedulingIgnoredDuringExecution"]
    assert len(rules) == 1
    return rules[0]


def test_colocated_worker_spreads_with_required_anti_affinity():
    # 이것이 없으면 max_nodes 는 레플리카 수만 제한할 뿐, 스케줄러가 워커들을 한
    # 노드에 몰아넣을 수 있다(설계 §2.4 -- MPI 팬아웃 붕괴). required 로 걸어도
    # 안전한 근거: resolve_fanout 이 레플리카를 후보 노드 수 이하로 자른다(§1-7).
    spec = _spec(operation="sync", tool="dsync",
                 candidates={"primary": ["dms-w1", "dms-w2", "dms-w3"]},
                 paths={"source": "s", "source_storage": "src",
                        "destination": "d", "destination_storage": "dst"})
    m = build_volcano_job(spec, job_image="i", namespace="dms", volumes=_VOL)
    worker = next(t for t in m["spec"]["tasks"] if t["name"] == "worker")
    labels = worker["template"]["metadata"]["labels"]
    assert labels == {"dms.io/job-id": "j1", "dms.io/task": "worker"}
    rule = _anti_affinity_rule(worker)
    assert rule["topologyKey"] == "kubernetes.io/hostname"
    assert rule["labelSelector"]["matchLabels"] == labels   # 자기참조: 같은 잡·같은 task
    # nodeAffinity(후보 고정)는 그대로 남는다 -- 병합이지 교체가 아니다
    aff = worker["template"]["spec"]["affinity"]
    values = aff["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"][
        "nodeSelectorTerms"][0]["matchExpressions"][0]["values"]
    assert values == ["dms-w1", "dms-w2", "dms-w3"]
    # 런처는 산개 대상이 아니다(rank0 하나뿐) -- 라벨도 안티어피니티도 없다
    launcher = next(t for t in m["spec"]["tasks"] if t["name"] == "launcher")
    assert "metadata" not in launcher["template"]
    assert "podAntiAffinity" not in launcher["template"]["spec"]["affinity"]


def test_nsync_workers_get_task_scoped_anti_affinity():
    spec = _spec(operation="sync", tool="nsync",
                 candidates={"source": ["dms-w1", "dms-w2"],
                             "destination": ["dms-w4"]},
                 paths={"source": "/cephfs-third/a", "source_storage": "cephfs-third",
                        "destination": "/cephfs-secondary/b",
                        "destination_storage": "cephfs-secondary"})
    m = build_volcano_job(spec, job_image="i", namespace="dms", volumes=_VOL)
    for task_name in ("source-worker", "destination-worker"):
        task = next(t for t in m["spec"]["tasks"] if t["name"] == task_name)
        # task 별 셀렉터 -- source 가 destination 을 밀어내면 각자 자기 풀 안에서
        # 퍼진다는 설계(§2.4)가 깨진다.
        assert _anti_affinity_rule(task)["labelSelector"]["matchLabels"] == {
            "dms.io/job-id": "j1", "dms.io/task": task_name}
        assert task["template"]["metadata"]["labels"]["dms.io/task"] == task_name
    launcher = next(t for t in m["spec"]["tasks"] if t["name"] == "launcher")
    assert "metadata" not in launcher["template"]
    assert "affinity" not in launcher["template"]["spec"]   # nsync 런처는 원래 affinity 없음
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_execution_manifests.py -q`
Expected: FAIL — 새 2건이 `KeyError: 'metadata'`. 기존 테스트(특히 `test_nsync_three_tasks_node_affinity`)는 PASS 유지.

- [ ] **Step 3: 구현한다**

`src/dms/execution_manifests.py`의 `_node_affinity`(:223-226) 아래에 헬퍼 2개 추가:

```python
def _worker_task_metadata(spec, task_name):
    # 자기참조 labelSelector 를 쓰려면 라벨이 파드에 먼저 있어야 한다(설계 §2.4) --
    # volcano task 템플릿에는 지금까지 metadata 자체가 없었다.
    return {"labels": {"dms.io/job-id": spec.job_id, "dms.io/task": task_name}}


def _worker_affinity(spec, task_name, nodes):
    """nodeAffinity(후보 노드 고정)에 required podAntiAffinity(같은 잡·같은 task 산개)를
    병합한다.

    required 로 거는 근거(설계 §2.4): resolve_fanout 이 node_count =
    min(len(candidates), max_nodes)(placement.py)라 레플리카가 후보 노드 수를 절대
    넘지 않는다 -- 산개 불가로 인한 영구 Pending 이 구조적으로 없다. 이것이 없으면
    max_nodes 가 노드가 아니라 레플리카만 제한해 MPI 팬아웃이 한 노드로 붕괴할 수
    있다(원본 설계 §181 위반). 셀렉터를 같은 job 의 같은 task 로 좁히는 이유:
    nsync 의 source/destination 은 별개 task 라 서로 밀어내면 안 되고, 다른 잡의
    워커와도 무관해야 한다."""
    return {**_node_affinity(nodes),
            "podAntiAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": [{
                "labelSelector": {"matchLabels": {
                    "dms.io/job-id": spec.job_id, "dms.io/task": task_name}},
                "topologyKey": "kubernetes.io/hostname"}]}}
```

`_build_nsync_job`의 `src_worker`/`dst_worker`(:315-324)를 교체:

```python
    src_worker = {"name": "source-worker", "replicas": len(src_nodes),
        "template": {
            "metadata": _worker_task_metadata(spec, "source-worker"),
            "spec": {"restartPolicy": "Never",
                "affinity": _worker_affinity(spec, "source-worker", src_nodes),
                "containers": [_worker_container("source-worker", job_image, spec, volumes)],
                "volumes": _pod_volumes(volumes)}}}
    dst_worker = {"name": "destination-worker", "replicas": len(dst_nodes),
        "template": {
            "metadata": _worker_task_metadata(spec, "destination-worker"),
            "spec": {"restartPolicy": "Never",
                "affinity": _worker_affinity(spec, "destination-worker", dst_nodes),
                "containers": [_worker_container("destination-worker", job_image, spec, volumes)],
                "volumes": _pod_volumes(volumes)}}}
```

`build_volcano_job`의 코로케이션 `worker`(:356-362)를 교체(런처 :348-355는 **불변**):

```python
    worker = {
        "name": "worker", "replicas": workers,
        "template": {
            "metadata": _worker_task_metadata(spec, "worker"),
            "spec": {
                "restartPolicy": "Never",
                # nodes 가 비면(개발 스텁 경로) 산개할 대상이 없다 -- 기존과 같이 빈 dict
                "affinity": _worker_affinity(spec, "worker", nodes) if nodes else {},
                "containers": [_worker_container("worker", job_image, spec, volumes)],
                "volumes": _pod_volumes(volumes)}}}
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_execution_manifests.py tests/test_execution_volcano.py tests/test_timeout_enforcement.py -q`
Expected: 전부 PASS (`_apply_task_deadlines`는 `task["template"]["spec"]`만 만지므로 metadata 추가와 직교)

- [ ] **Step 5: 커밋**

```bash
git add src/dms/execution_manifests.py tests/test_execution_manifests.py
git commit -m "feat(execution): 워커 task 라벨 + required podAntiAffinity — 같은 잡·같은 task 노드 산개"
```

---

### Task 7: 에이전트 net/dev 경로 주입 + DaemonSet 마운트

**Files:**
- Modify: `src/dms/agent/probes.py:92,121`
- Modify: `src/dms/agent/runner.py:18-33,41-47`
- Modify: `src/dms/config.py` (`AgentSettings`)
- Modify: `deploy/k8s/50-agent-daemonset.yaml`
- Modify: `tests/test_agent_probes.py`, `tests/test_agent_runner.py`, `tests/test_config_phase2.py`

**Interfaces:**
- Consumes: 기존 `read_text` 주입, `AgentSettings.mountinfo_path` 관례(config.py:191,210 — 이것을 그대로 미러링).
- Produces:
  - `probe_os_metrics(storages, *, read_text, statvfs=os.statvfs, net_dev_path="/proc/net/dev")`
  - `build_report(..., net_dev_path="/proc/net/dev")` → `os_fn(storages, read_text=read_text, net_dev_path=net_dev_path)`
  - `AgentSettings.net_dev_path: str = "/proc/net/dev"` (`DMS_AGENT_NET_DEV_PATH`)
  - DaemonSet: hostPath **File** `/proc/1/net/dev` → `/host/proc/1/net/dev`(readOnly) + env. **hostNetwork/dnsPolicy는 절대 만지지 않는다**(설계 §2.5 기각).

- [ ] **Step 1: 실패하는 테스트를 쓴다**

**(1)** `tests/test_agent_probes.py` 끝에 추가:

```python
def test_probe_os_metrics_reads_injected_net_dev_path():
    # /proc/net/* 는 netns 범위라 파드 기본 경로는 veth 값이다(설계 §2.5) --
    # DaemonSet 이 마운트하는 호스트 netns 경로가 실제로 읽혀야 한다. files 에 기본
    # 경로를 안 넣어, 주입이 무시되면 KeyError -> fail-soft None 으로 단언이 깨진다.
    files = {"/proc/loadavg": LOADAVG, "/proc/meminfo": MEMINFO,
             "/host/proc/1/net/dev": NETDEV}
    out = probe_os_metrics([], read_text=lambda p: files[p],
                           statvfs=lambda p: None,
                           net_dev_path="/host/proc/1/net/dev")
    assert out["network_rx_bytes"] == 1500 and out["network_tx_bytes"] == 2700
```

**(2)** `tests/test_agent_runner.py` 끝에 추가:

```python
def test_build_report_threads_net_dev_path_to_os_probe():
    seen = {}

    def os_fn(storages, **kw):
        seen.update(kw)
        return {}

    build_report("node-a", [], [], mountinfo_text="",
                 mounts_fn=lambda s, **k: [], tools_fn=lambda n, **k: [],
                 identities_fn=lambda u, **k: [], os_fn=os_fn,
                 net_dev_path="/host/proc/1/net/dev")
    assert seen["net_dev_path"] == "/host/proc/1/net/dev"


def test_run_once_uses_settings_net_dev_path(monkeypatch):
    # 설정 -> run_once -> build_report -> probe 배선이 한 군데라도 끊기면 기본
    # /proc/net/dev(veth)로 조용히 되돌아간다 -- 배선 자체를 고정한다.
    seen = {}

    def handler(request):
        return httpx.Response(200, json={"storages": [],
                                         "identity_probe_targets": [],
                                         "report_interval_seconds": 60})

    monkeypatch.setattr("dms.agent.runner._read_text", lambda path: "")
    monkeypatch.setattr("dms.agent.runner.probe_tools", lambda names, **k: [])
    monkeypatch.setattr("dms.agent.runner.probe_identities", lambda users, **k: [])
    monkeypatch.setattr("dms.agent.runner.probe_os_metrics",
                        lambda storages, **k: seen.update(k) or {})
    settings = AgentSettings(api_url="http://api", shared_token="tok",
                             node_name="node-a", interval_seconds=60,
                             mountinfo_path="/unused",
                             net_dev_path="/host/proc/1/net/dev")
    AgentRunner(settings, _client(handler)).run_once(
        {"storages": [], "probe_targets": [], "interval": 60})
    assert seen["net_dev_path"] == "/host/proc/1/net/dev"
```

**(3)** `tests/test_config_phase2.py`의 `test_agent_settings_required_and_defaults`를 교체:

```python
def test_agent_settings_required_and_defaults(monkeypatch):
    env = {"DMS_AGENT_API_URL": "http://dms-api:8080", "DMS_SHARED_TOKEN": "tok"}
    s = AgentSettings.from_env(env)
    assert s.api_url == "http://dms-api:8080"
    assert s.interval_seconds == 60
    assert s.mountinfo_path == "/proc/1/mountinfo"
    assert s.net_dev_path == "/proc/net/dev"
    assert s.node_name  # hostname fallback은 비어있지 않다
    s2 = AgentSettings.from_env({**env, "DMS_AGENT_NODE_NAME": "node-7",
                                 "DMS_AGENT_INTERVAL_SECONDS": "10",
                                 "DMS_AGENT_NET_DEV_PATH": "/host/proc/1/net/dev"})
    assert s2.node_name == "node-7" and s2.interval_seconds == 10
    assert s2.net_dev_path == "/host/proc/1/net/dev"
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_agent_probes.py tests/test_agent_runner.py tests/test_config_phase2.py -q`
Expected: FAIL — probes 1건(`TypeError: unexpected keyword argument 'net_dev_path'`), runner 2건(동일/`AttributeError`), config 2단언(`AttributeError: net_dev_path`)

- [ ] **Step 3: 구현한다**

**(1)** `src/dms/agent/probes.py` — 시그니처와 net 블록 교체:

```python
def probe_os_metrics(storages, *, read_text, statvfs=os.statvfs,
                     net_dev_path="/proc/net/dev"):
```

net 블록(:119-130)의 첫 두 줄을:

```python
    try:
        rx = tx = 0
        # /proc/net/* 는 네트워크 네임스페이스 범위다 -- 파드 안에서 기본 경로를
        # 읽으면 veth 값이 나온다. loadavg/meminfo 는 네임스페이스되지 않아 이미
        # 호스트 값이라 그대로 두고(설계 §2.5), 네트워크만 DaemonSet 이 마운트한
        # PID 1 경로(/host/proc/1/net/dev)를 주입받는다 -- mountinfo 와 같은 관례.
        for line in read_text(net_dev_path).splitlines()[2:]:
```

로 교체한다(나머지 파싱 로직 불변).

**(2)** `src/dms/agent/runner.py` — `build_report` 시그니처·os 호출 교체:

```python
def build_report(node_name, storages, probe_targets, *, mountinfo_text,
                 tool_names=AGENT_TOOL_NAMES, mounts_fn=None, tools_fn=None,
                 identities_fn=None, os_fn=None, read_text=None,
                 net_dev_path="/proc/net/dev") -> dict:
```

```python
        "os": os_fn(storages, read_text=read_text, net_dev_path=net_dev_path),
```

`AgentRunner.run_once`의 `build_report(...)` 호출을 교체:

```python
        report = build_report(self._settings.node_name, state["storages"],
                              state["probe_targets"], mountinfo_text=mountinfo_text,
                              net_dev_path=self._settings.net_dev_path)
```

**(3)** `src/dms/config.py` — `AgentSettings`에 필드(`mountinfo_path` 아래)와 from_env 항목(`mountinfo_path=` 줄 아래) 추가:

```python
    net_dev_path: str = "/proc/net/dev"
```

```python
            net_dev_path=environ.get("DMS_AGENT_NET_DEV_PATH", "/proc/net/dev"),
```

**(4)** `deploy/k8s/50-agent-daemonset.yaml`에 네 가지 수정:

env — `DMS_AGENT_MOUNTINFO_PATH` 항목 아래에:

```yaml
            - name: DMS_AGENT_NET_DEV_PATH
              value: "/host/proc/1/net/dev"
```

volumeMounts — `proc-mountinfo` 항목 아래에:

```yaml
            - name: proc-net-dev
              mountPath: /host/proc/1/net/dev
              readOnly: true
```

volumes — `proc-mountinfo` 항목 아래에:

```yaml
        - name: proc-net-dev
          hostPath:
            path: /proc/1/net/dev
            type: File
```

머리 주석 — volume 목록(`#   proc-mountinfo: ...` 문단) 뒤에 추가:

```yaml
#   proc-net-dev: PID 1(호스트 netns)의 net/dev. 파드 자신의 /proc/net/dev 는 자기
#     netns(veth) 값이라 network_rx/tx_bytes 가 틀린다(슬라이스 16 설계 §2.5).
#     hostNetwork 대안은 dnsPolicy: ClusterFirstWithHostNet 동반 변경 없이는
#     dms-api DNS 가 죽어 보고가 조용히 영구 중단되므로 기각 -- mountinfo 와 같은
#     hostPath File 관례를 쓴다.
```

그리고 낡은 주석(`# (host-root /host mount REMOVED: ...` 5줄, :18-22)을 다음으로 교체(net-dev가 호스트 값이라던 서술이 이제 틀렸다):

```yaml
# (host-root /host mount REMOVED: loadavg/meminfo are not namespaced so plain
#  /proc paths already reflect the host -- mounting the entire host filesystem
#  was pure attack surface. /proc/net/* IS netns-scoped though: the in-pod
#  value is the veth's, so net/dev alone is bind-mounted below, like mountinfo.)
```

또한 :8-10의 필요한 env 열거(`only DMS_AGENT_API_URL, ...`)에 `DMS_AGENT_NET_DEV_PATH`를 추가한다(`DMS_AGENT_MOUNTINFO_PATH` 뒤에 `, DMS_AGENT_NET_DEV_PATH`).

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_agent_probes.py tests/test_agent_runner.py tests/test_config_phase2.py -q`
Expected: 전부 PASS (기존 `test_probe_os_metrics_with_failures_are_soft`가 기본 경로 `/proc/net/dev` 유지를 증명)

- [ ] **Step 5: 전체 백엔드 스위트로 회귀를 확인한다**

Run: `.venv/bin/python -m pytest -q` (포그라운드, Bash timeout 400000ms)
Expected: 전부 PASS — 기준선 907 + 이번 슬라이스 신규(대략 +23: manifest_tags 5, api_metrics 2, migrations 3, placement 1, planner 4, config 2, execution_manifests 2, agent probes/runner 3, 기타 1)

- [ ] **Step 6: 커밋**

```bash
git add src/dms/agent/probes.py src/dms/agent/runner.py src/dms/config.py deploy/k8s/50-agent-daemonset.yaml tests/test_agent_probes.py tests/test_agent_runner.py tests/test_config_phase2.py
git commit -m "feat(agent): net/dev 경로 주입(DMS_AGENT_NET_DEV_PATH) — 호스트 netns 네트워크 지표"
```

---

## 플랜 이후: 배포·실증 (설계 §6 — 별도 ops, 플랜 밖)

플랜 실행(7태스크 커밋)이 끝나면 컨트롤러가 테스트베드에서 수행한다 — 플랜 태스크가 아니다(슬라이스 12~15와 동일 관례). **매니페스트 먼저 고치고 빌드·배포**하는 관례를 그대로 지킨다(그래야 배지가 안 뜬다):

1. `deploy/k8s`의 태그를 새 값으로 범프(40/41/30 → `dms:d26`, initContainer 포함 / 50 → `dms-agent:d24`) 후 `dms:d26`·`dms-agent:d24` 빌드/푸시(에이전트 코드도 바뀌었으므로 둘 다).
2. `kubectl apply` — `20-config.yaml`(grace 키), `40/41`(initContainer), `50`(net/dev 마운트). initContainer가 migrate를 자동 수행한다(스키마 변경은 없지만 통과 자체가 실증 §6-3).
3. 실증: (§6-1) 새 이미지 배포 직후 대시보드에 드리프트 배지가 **없는지**. (§6-2) 포탈에서 이전 태그로 롤아웃 → 배지가 **뜨는지** → 매니페스트를 고쳐 재배포하면 사라지는지. (§6-3) api/controller 동시 기동에서 마이그레이션 충돌 로그가 없는지. (§6-4) 신규 사용자 첫 요청이 즉시 거부되지 않고 Pending 유지 후 자동 성공하는지(슬라이스 15 실증에서 실패했던 시나리오 — `identity_propagating` 이벤트 확인). (§6-5) `max_nodes ≥ 2` 정책으로 sync 실행 → 워커 파드가 서로 다른 노드에 뜨는지(Volcano의 podAntiAffinity 집행 확인 포함). (§6-6) `network_rx_bytes`가 노드에서 직접 읽은 호스트 값과 일치하는지.

## Self-Review

**1. 스펙 커버리지**

| 설계 절 | 담당 태스크 |
|---|---|
| §1 실측 전제(매니페스트 미동봉, observe 이미지 출처, migrate 경합, 플래너 즉시 거부, 라벨 부재, 레플리카≤후보, netns) | 실측 고정값 표 + 각 태스크의 근거 주석 |
| §2.1 동봉 비교(승격 파서, Dockerfile COPY, live vs manifest, DMS_JOB_IMAGE 동봉값) | Task 1(파서·조회) + Task 2(COPY·라우트) |
| §2.2 initContainer + PG 어드바이저리 락(SQLite no-op, Job 유지) | Task 4 |
| §2.3 신원 전파 유예(rejections 탑재, 3중 조건, 상태 불변, 이벤트, grace 300) | Task 5 |
| §2.4 라벨 먼저 + required anti-affinity(같은 job·같은 task, nsync 각자 풀) | Task 6 |
| §2.5 hostNetwork 없이 net 수정(PID 1 경로, File 마운트, 주입 관례) | Task 7 |
| §3 화면(배지+매니페스트 값+되돌림 한 줄, 잡 이미지 한 줄, 일치 시 무표시) | Task 3 |
| §4 오류 처리(전면 fail-soft, 락 획득 실패만 예외) | Task 1·2(None 강등) + Task 4(예외 전파) |
| §5 테스트 목록(실물 파싱·드리프트 3상태·락 SQL·유예 3분기·anti-affinity·net 주입) | Task 1~7 각 Step 1 |
| §6 실증 | 플랜 이후 절(관례) |
| §7 하지 않는 것(파일-대-라이브, 자동 수정, DMS_JOB_IMAGE 롤아웃, 롤백/알림, 일반 재시도, loadavg/meminfo/hostPID) | 어떤 태스크도 건드리지 않음 — 서버는 값만 주고 비교는 프론트, 유예는 no_eligible_nodes 한정 |

**2. 플레이스홀더 점검** — "TBD"/"적절히 처리"/코드 없는 테스트 지시 없음. 모든 코드 단계에 전문이 있고, Task 1의 파서는 계약 테스트 원문에서 그대로 옮기는 것임을 명시했다. YAML 삽입 블록도 전문 수록(41-controller는 주석만 축약, 본문 전문).

**3. 타입 일관성** — Task 1의 `manifest_images()`/`manifest_job_image()` 이름·반환형을 Task 2의 import·라우트·테스트 대역이 그대로 쓴다. Task 2가 내는 `manifest_image`/`job_image.{live,manifest}` 키를 Task 3의 `InfraComponent`/`InfraMetrics`/JSX가 그대로 쓴다. Task 4의 `_apply_migrations`는 monkeypatch 대상 이름과 일치한다. Task 5의 `PlacementError(reason_code, detail, *, rejections)` 시그니처를 placement raise·planner `exc.rejections`·테스트가 공유하고, `planner_identity_grace_seconds`는 config 필드·`_SERVER_INT_KEYS`·`_Settings` 스텁·20-config 키가 같은 철자다. Task 6의 라벨 키(`dms.io/job-id`/`dms.io/task`)는 metadata와 labelSelector 양쪽에서 동일 dict 리터럴이다. Task 7의 `net_dev_path`는 probes→build_report→run_once→AgentSettings→env 이름(`DMS_AGENT_NET_DEV_PATH`)까지 한 철자다.

**알려진 위험:**
- **이미지 안 경로 후보**: 이미지의 dms는 site-packages라 `__file__` 후보가 빗나가고 `/app/deploy/k8s` 후보가 받는다 — 테스트로는 checkout 후보만 증명 가능하므로(컨테이너 밖), 이미지 쪽은 실증 §6-1(배지 부재)이 확인한다.
- **initContainer 이미지는 포탈 롤아웃 패치 대상 밖**: strategic merge patch는 본 컨테이너(name 기준)만 갱신하므로 포탈 롤아웃 후 initContainer는 매니페스트 태그의 migrate를 돌린다. 매니페스트-우선 관례에서는 항상 일치하고, 어긋난 상태는 정확히 드리프트 배지가 표시하는 그 상태다(설계 §2.1과 일관 — 매니페스트 자동 수정은 §7이 의도적으로 제외).
- **어드바이저리 락 테스트는 가짜 db**: 이 저장소엔 PG 하니스가 없다(기존 `_widen_count_columns`와 같은 한계). SQL 발행 순서·예외 경로 해제만 고정하고, 실 경합은 실증 §6-3(동시 기동)이 확인한다.
- **Volcano의 podAntiAffinity 집행**: 매니페스트에 넣는 것은 순수 함수로 증명되지만 스케줄러(Volcano v1.15 predicates)가 실제로 산개시키는지는 실증 §6-5의 몫이다.
- **grace 판정은 ISO 문자열 비교**: `utc_now_iso`/`iso_plus`가 고정 포맷(`%Y-%m-%dT%H:%M:%SZ`)이라 안전하다 — 포맷을 바꾸면 이 판정도 같이 봐야 한다(주석으로 남김).
