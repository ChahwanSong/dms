"""설정 기반 live 어댑터/리졸버 선택. cli/app 공용."""
import os

from .artifact_base import resolve_artifact_base
from .artifact_files import (JOB_ID_RE, PHASES, SUMMARY_MAX_BYTES, job_owner_uid,
                             read_contained_text)
from .execution import StubExecutionAdapter
from .identity_ldap import build_ldap_resolver
from .job_image import resolve_job_image


def build_identity_resolver(settings):
    return build_ldap_resolver(settings)


def build_summary_reader(repos):
    """컨트롤러의 summary.json 읽기(execution_volcano.read_summary 의 read_text).

    제어면이 root 로 돌므로(2026-09-09) 평범한 open(path).read() 는 요청자가 자기
    phase 디렉터리에 심은 심링크(summary.json → /cephfs/<남>/x.json)를 따라가
    남의 파일을 **요청자 잡 상세(result_summary)로 그대로 노출**하고, mkfifo 면
    단일 스레드 컨트롤러 전체가 멈추며, 다GB 파일이면 OOM 크래시 루프다. API 와
    같은 봉쇄 사슬(artifact_files)로 열되, 경로는 어댑터가 <base>/<job_id>/<phase>/
    summary.json 으로 조립한 것이라 조각으로 되돌려 검증한다(job_id·phase 화이트
    리스트에 어긋나면 사슬이 None). 소유자 기준 uid 는 잡 행(planner 저장)에서
    읽는다 -- 없으면 0(러너가 root 로 쓴 summary 만 허용)."""
    def read_text(path):
        head, name = os.path.split(path)
        head, phase = os.path.split(head)
        base, job_id = os.path.split(head)
        # 모양 검사가 DB 조회보다 먼저 -- 라벨에서 재구성한 경로(execution_volcano.
        # _reconstruct_summary_path)가 엉뚱해도 쿼리를 쓰지 않는다. 어댑터는
        # summary.json 만 읽으므로 이름도 고정한다.
        if (name != "summary.json" or phase not in PHASES
                or not JOB_ID_RE.fullmatch(job_id or "")):
            return None
        uid = job_owner_uid(repos.data_jobs.get_job(job_id))
        return read_contained_text(base, job_id, phase, name,
                                   max_bytes=SUMMARY_MAX_BYTES,
                                   owner_uid=0 if uid is None else uid)
    return read_text


def build_execution_adapter(settings, repos):
    if settings.execution_backend != "volcano":
        return StubExecutionAdapter()
    from .execution_volcano import KubernetesClient, VolcanoExecutionAdapter

    read_text = build_summary_reader(repos)

    return VolcanoExecutionAdapter(
        KubernetesClient(settings.k8s_namespace),
        # 생성자 캡처 금지(artifact_base 와 같은 이유, 슬라이스 35): 포탈 릴리스의
        # job-image 오버라이드(DB)가 재시작 없이 다음 잡부터 반영돼야 한다.
        job_image=lambda: resolve_job_image(repos.control, settings),
        namespace=settings.k8s_namespace,
        storages_lookup=lambda n: repos.storages.get(n), read_text=read_text,
        # 생성자 캡처 금지(설계 §2.1/§1-7): base 변경 후 컨트롤러가 재시작해도
        # 호출 시점의 DB 값으로 summary 경로를 재구성한다.
        artifact_base=lambda: resolve_artifact_base(repos.control, settings))


def build_build_runner(settings, repos):
    if settings.execution_backend != "volcano":
        from .build_runner import StubBuildRunner
        return StubBuildRunner()
    from .build_runner import BuildRunner
    from .execution_volcano import KubernetesClient
    return BuildRunner(KubernetesClient(settings.k8s_namespace),
                       namespace=settings.k8s_namespace,
                       registry=settings.build_registry,
                       builder_image=settings.build_builder_image,
                       timeout_seconds=settings.build_timeout_seconds,
                       # 프로브는 job_image(§2.5): 워커 캐시 존재 + pull 은
                       # pkg-01 만 필요 -- 프로브 기동이 인터넷과 무관해야
                       # "인터넷만 없는 노드"를 정확히 판별한다. resolve 클로저
                       # (슬라이스 35) -- 릴리스의 job-image 가 프로브에도 반영.
                       job_image=lambda: resolve_job_image(repos.control, settings),
                       preflight_timeout_seconds=settings.build_preflight_timeout_seconds,
                       # 빌드 노드 프록시(2026-09-08): 포탈 컨트롤 상태의 값을 제출
                       # 시점마다 읽는다 -- job_image 와 같은 "DB 가 진실" 클로저.
                       proxy=lambda: repos.control.build_proxy())


def build_rollout_runner(settings):
    if settings.execution_backend != "volcano":
        from .rollout_runner import StubRolloutRunner
        return StubRolloutRunner()
    from .execution_volcano import KubernetesClient
    from .rollout_runner import RolloutRunner
    return RolloutRunner(KubernetesClient(settings.k8s_namespace),
                         namespace=settings.k8s_namespace)


def build_node_lister(settings):
    """컨트롤 상태 no_proxy 힌트의 워커 노드 IP 조회(2026-09-09). 스텁 백엔드는
    빈 목록 -- 힌트는 fail-soft 라 로컬·CI 에서 라우트가 500 이 되지 않는다."""
    if settings.execution_backend != "volcano":
        return lambda: []
    from .execution_volcano import KubernetesClient
    client = KubernetesClient(settings.k8s_namespace)
    return client.list_node_addresses


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


def wire_reconnect_event(db, repos) -> None:
    """슬라이스 22 §2.6: 재연결 성공의 영속 흔적 1건. record_event 는 절대
    예외를 올리지 않는 계약(observability.py)이라 재연결 직후 재실패에도
    안전하고, 트랜잭션 밖 단독 INSERT 라 업무 변경을 되돌릴 수도 없다.
    api(create_app)와 controller(cli)가 이 함수 하나를 같이 쓴다 -- 두 곳이
    각자 훅을 만들면 이벤트 모양이 갈라져 SQL 집계가 깨진다."""
    def _record():
        repos.observability.record_event(
            component="db", severity="warning", event_type="db_reconnected",
            message=f"dialect={db.dialect} count={db.reconnect_count}")
    db.on_reconnect = _record
