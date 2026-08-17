"""env 기반 설정. 기동 시 전부 검증하고, placeholder가 통과하는 구멍을 만들지 않는다."""
import socket
from dataclasses import dataclass
from typing import Mapping


AGENT_TOOL_NAMES = ("dscan", "dsync", "nsync", "drm")

_SERVER_INT_KEYS = (
    ("DMS_AGENT_REPORT_STALE_SECONDS", "agent_report_stale_seconds", 300),
    ("DMS_AGENT_REPORT_INTERVAL_SECONDS", "agent_report_interval_seconds", 60),
    ("DMS_RECONCILE_INTERVAL_SECONDS", "reconcile_interval_seconds", 30),
    ("DMS_RETENTION_INTERVAL_SECONDS", "retention_interval_seconds", 3600),
    ("DMS_AGENT_REPORT_RETENTION_DAYS", "agent_report_retention_days", 30),
    ("DMS_IDENTITY_PROBE_TTL_SECONDS", "identity_probe_ttl_seconds", 3600),
    ("DMS_PLANNER_INTERVAL_SECONDS", "planner_interval_seconds", 10),
    # 신원 전파 유예 창(설계 §2.3). 최악 전파 ≈130s(보고 60s×2 + 플래너 10s)의 2배
    # 남짓. 늘리기 전에: 같은 resource_key 후속 요청이 Conflict 로 죽는 시간도 같이
    # 늘어난다(planner 의 find_active 게이트).
    ("DMS_PLANNER_IDENTITY_GRACE_SECONDS", "planner_identity_grace_seconds", 300),
    ("DMS_STEPPER_INTERVAL_SECONDS", "stepper_interval_seconds", 5),
    ("DMS_PREVIEW_TTL_SECONDS", "preview_ttl_seconds", 86400),
    ("DMS_BATCH_ORCHESTRATOR_INTERVAL_SECONDS", "batch_orchestrator_interval_seconds", 5),
    ("DMS_VCJOB_TTL_SECONDS", "vcjob_ttl_seconds", 86400),
    # vcjob TTL과 동일한 86400으로 맞춘다: preflight Pod은 아무 아티팩트도 쓰지 않으므로
    # (dms_job_runner가 아니라 bare `sh -c`) DMS_PREFLIGHT_REASON 같은 진단 정보는
    # 파드 로그에만 존재한다 -- 1시간짜리 GC 창은 운영자가 확인하기 전에 그 유일한
    # 사본을 지워버린다.
    ("DMS_POD_GC_AFTER_SECONDS", "pod_gc_after_seconds", 86400),
    ("DMS_POD_GC_INTERVAL_SECONDS", "pod_gc_interval_seconds", 600),
    ("DMS_BUILD_WATCHER_INTERVAL_SECONDS", "build_watcher_interval_seconds", 15),
    # 빌드 파드 activeDeadlineSeconds + BuildWatcher 나이 기반 회수 창(C2). 기본
    # 7200(2h) -- mpifileutils를 소스에서 컴파일하는 빌드가 가장 오래 걸린다.
    # 둘 다 이 값을 쓴다: 파드가 스케줄된 뒤엔 kubelet이, 스케줄조차 못 된
    # (nodeSelector 오타 등) Pending은 BuildWatcher의 created_at 기반 회수가 잡는다.
    ("DMS_BUILD_TIMEOUT_SECONDS", "build_timeout_seconds", 7200),
    # 슬라이스 21 §2.5: 적합성 프로브(프리플라이트 파드) 대기 상한. 프로브는
    # 캐시된 job_image 로 수 초면 종단한다 -- 이 창을 넘기면 노드 다운/스케줄
    # 불가로 보고 build_preflight_timeout 으로 즉시 회수한다(2h generic 대기를
    # 수 분으로 줄이는 것이 이 슬라이스의 존재 이유다).
    ("DMS_BUILD_PREFLIGHT_TIMEOUT_SECONDS", "build_preflight_timeout_seconds", 180),
    # 연속 readyz 실패 자기 종료 임계(슬라이스 22 §2.4). 프로브 주기 10s 기준
    # 30 이면 약 5분이다. **0 은 명시적 비활성** -- 운영자가 장치를 끄고 관찰만
    # 하고 싶을 때의 탈출구다. liveness 를 DB 에 직결하지 않는 이유는 그쪽이
    # 90초 만에 발화해 DB 순단에도 파드를 재시작시키고, CrashLoopBackOff 백오프가
    # DB 복귀 **후의** 회복을 오히려 늦추기 때문이다(replicas 1 이라 그동안 0대).
    ("DMS_READYZ_EXIT_FAILURES", "readyz_exit_failures", 30),
    ("DMS_EVENT_RETENTION_DAYS", "event_retention_days", 30),
    # 롤아웃 루프 간격 10초 -> per-loop 리스 max(10*3, 30)=30초. 설계 §2: 리스는
    # 갱신되지 않으므로 긴 간격은 컨트롤러 자기 갱신 후 재획득을 그만큼 늦춘다.
    ("DMS_ROLLOUT_INTERVAL_SECONDS", "rollout_interval_seconds", 10),
    # DaemonSet 벽시계 타임아웃(설계 §3: conditions가 없어 이것이 유일한 실패 수단).
    # 600은 Deployment의 progressDeadlineSeconds와 같은 값 -- Deployment에는 이
    # 값의 3배를 최후 회수로만 쓴다(rollout_watcher.py 참고).
    ("DMS_ROLLOUT_TIMEOUT_SECONDS", "rollout_timeout_seconds", 600),
    # 슬라이스 26: 아티팩트 **전체 다운로드** 상한(뷰 256KB 꼬리와 별개). 기본
    # 256MiB. sparse 초대형 파일로 디스크·대역을 태우는 공격을 여기서 끊는다 --
    # 초과는 413 artifact_too_large(판정은 봉쇄 통과 뒤에만, 크기 오라클 방지).
    ("DMS_ARTIFACT_DOWNLOAD_MAX_BYTES", "artifact_download_max_bytes", 268435456),
)
# 재시도 설정은 두지 않는다: 상위 스펙에 재시도 요구가 없고, 실패한 rm/sync 를 자동으로
# 재실행하는 것은 파괴적이다. 재실행은 배치 :rerun-failed 와 사용자 재제출로 한다.
# (DMS_JOB_MAX_ATTEMPTS 는 소비처가 0건인 채로 오래 남아 있어 슬라이스 10 에서 제거했다.)


class SettingsError(Exception):
    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


def _is_placeholder(value: str | None) -> bool:
    # 완전일치만 보면 커밋된 예시값(CHANGE_ME_SHARED_TOKEN 등)이 그대로 통과한다.
    # DMS_SHARED_TOKEN 은 Bearer 로 admin 을 주므로, 공개 저장소에 적힌 값으로
    # 기동하는 것은 곧 무인증 admin 이다. DB URL 처럼 자리표시자가 문자열 중간에
    # 박히는 경우도 있어 부분일치로 본다.
    if not value:
        return True
    return "CHANGE_ME" in value or "REPLACE_WITH_" in value


def _parse_int(environ, key, default, problems):
    raw = environ.get(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        problems.append(f"{key} is not an integer: {raw!r}")
        return default


def _parse_bool(environ, key, default=False):
    value = environ.get(key)
    if value is None:
        return default
    return value.strip().lower() in ("true", "1")


def _parse_csv_set(environ, key, default=frozenset()):
    # 미설정(absent)이면 default. 명시적 빈 문자열("")은 "비활성"으로 default를
    # 덮어써 빈 집합을 준다(운영자가 특권을 끄고 싶을 때).
    raw = environ.get(key)
    if raw is None:
        return default
    return frozenset(item.strip() for item in raw.split(",") if item.strip())


@dataclass(frozen=True)
class Settings:
    database_url: str
    shared_token: str
    admin_token: str
    session_secret: str
    api_host: str = "0.0.0.0"
    api_port: int = 8080
    agent_report_stale_seconds: int = 300
    agent_report_interval_seconds: int = 60
    reconcile_interval_seconds: int = 30
    retention_interval_seconds: int = 3600
    agent_report_retention_days: int = 30
    identity_probe_ttl_seconds: int = 3600
    planner_interval_seconds: int = 10
    planner_identity_grace_seconds: int = 300
    stepper_interval_seconds: int = 5
    preview_ttl_seconds: int = 86400
    artifact_base_uri: str = "file:///artifacts/dms"
    allow_privileged_requesters: bool = True
    privileged_requesters: frozenset = frozenset({"root", "admin"})
    ldap_uri: str = ""
    ldap_user_base: str = ""
    ldap_group_base: str = ""
    ldap_bind_dn: str = ""
    ldap_bind_pw: str = ""
    # 슬라이스 28: true 면 bind DN/PW 결측·자리표시자 시 기동 거부(fail-closed).
    # 익명 바인드로의 침묵 강등을 막는 스위치다 -- identity_ldap 이 아니라 여기서
    # 거부하는 이유는 발화 시점이 배포 순간(기동)이어야 운영자가 알아채기 때문.
    ldap_require_auth_bind: bool = False
    execution_backend: str = "stub"
    job_image: str = ""
    k8s_namespace: str = "dms"
    static_dir: str | None = None
    batch_orchestrator_interval_seconds: int = 5
    vcjob_ttl_seconds: int = 86400
    pod_gc_after_seconds: int = 86400
    pod_gc_interval_seconds: int = 600
    build_registry: str = "pkg-01:5000"
    build_builder_image: str = "quay.io/buildah/stable:latest"
    build_watcher_interval_seconds: int = 15
    build_timeout_seconds: int = 7200
    build_preflight_timeout_seconds: int = 180
    readyz_exit_failures: int = 30
    event_retention_days: int = 30
    rollout_interval_seconds: int = 10
    rollout_timeout_seconds: int = 600
    artifact_download_max_bytes: int = 268435456

    @classmethod
    def from_env(cls, environ: Mapping) -> "Settings":
        problems: list[str] = []
        required = ("DMS_DATABASE_URL", "DMS_SHARED_TOKEN",
                    "DMS_ADMIN_TOKEN", "DMS_SESSION_SECRET")
        values: dict = {}
        for key in required:
            value = environ.get(key)
            if _is_placeholder(value):
                problems.append(f"{key} is missing or a placeholder")
            values[key] = value
        port_raw = environ.get("DMS_API_PORT", "8080")
        try:
            port = int(port_raw)
        except ValueError:
            problems.append(f"DMS_API_PORT is not an integer: {port_raw!r}")
            port = 0
        extra = {field: _parse_int(environ, env_key, default, problems)
                 for env_key, field, default in _SERVER_INT_KEYS}
        ldap_bind_dn = environ.get("DMS_LDAP_BIND_DN", "")
        ldap_bind_pw = environ.get("DMS_LDAP_BIND_PW", "")
        ldap_require_auth_bind = _parse_bool(environ, "DMS_LDAP_REQUIRE_AUTH_BIND")
        if ldap_require_auth_bind:
            # 인증 바인드를 의도했는데 자격증명이 없으면 identity_ldap 이 익명으로
            # 조용히 떨어진다(bind_dn or None) -- 그 침묵을 기동 거부로 바꾼다.
            # _is_placeholder 라 빈 값과 CHANGE_ME 류를 같은 구멍으로 본다.
            for env_key, value in (("DMS_LDAP_BIND_DN", ldap_bind_dn),
                                   ("DMS_LDAP_BIND_PW", ldap_bind_pw)):
                if _is_placeholder(value):
                    problems.append(
                        f"DMS_LDAP_REQUIRE_AUTH_BIND is true but {env_key}"
                        " is missing or a placeholder")
        if problems:
            raise SettingsError(problems)
        return cls(
            database_url=values["DMS_DATABASE_URL"],
            shared_token=values["DMS_SHARED_TOKEN"],
            admin_token=values["DMS_ADMIN_TOKEN"],
            session_secret=values["DMS_SESSION_SECRET"],
            api_host=environ.get("DMS_API_HOST", "0.0.0.0"),
            api_port=port,
            **extra,
            artifact_base_uri=environ.get("DMS_ARTIFACT_BASE_URI",
                                          "file:///artifacts/dms"),
            allow_privileged_requesters=_parse_bool(
                environ, "DMS_ALLOW_PRIVILEGED_REQUESTERS", default=True),
            privileged_requesters=_parse_csv_set(
                environ, "DMS_PRIVILEGED_REQUESTERS",
                default=frozenset({"root", "admin"})),
            ldap_uri=environ.get("DMS_LDAP_URI", ""),
            ldap_user_base=environ.get("DMS_LDAP_USER_BASE", ""),
            ldap_group_base=environ.get("DMS_LDAP_GROUP_BASE", ""),
            ldap_bind_dn=ldap_bind_dn,
            ldap_bind_pw=ldap_bind_pw,
            ldap_require_auth_bind=ldap_require_auth_bind,
            execution_backend=environ.get("DMS_EXECUTION_BACKEND", "stub"),
            job_image=environ.get("DMS_JOB_IMAGE", ""),
            k8s_namespace=environ.get("DMS_K8S_NAMESPACE", "dms"),
            static_dir=environ.get("DMS_STATIC_DIR"),
            build_registry=environ.get("DMS_BUILD_REGISTRY", "pkg-01:5000"),
            build_builder_image=environ.get(
                "DMS_BUILD_BUILDER_IMAGE", "quay.io/buildah/stable:latest"),
        )


@dataclass(frozen=True)
class AgentSettings:
    api_url: str
    shared_token: str
    node_name: str
    interval_seconds: int = 60
    mountinfo_path: str = "/proc/1/mountinfo"
    net_dev_path: str = "/proc/net/dev"
    # 물리 인터페이스 판별용 /sys/devices/virtual/net (설계 §2.6). 기본은 반드시
    # **미설정**이다 -- 파드 안에도 같은 경로가 있지만 거기 든 것은 **파드 netns 의**
    # 가상 인터페이스라, 마운트 없이 기본 경로를 쓰면 다른 네임스페이스의 집합으로
    # 호스트의 인터페이스 목록을 거르게 된다. 그러면 이름이 겹치는 호스트 인터페이스는
    # 무엇이든 가상으로 오판돼 빠진다(eth0 이 가장 흔한 충돌일 뿐, 규칙 자체는 이름을
    # 보지 않는다). 미설정이면 필터 없이 기존대로 lo 만 뺀다.
    virtual_net_path: str = ""

    @classmethod
    def from_env(cls, environ: Mapping) -> "AgentSettings":
        problems: list[str] = []
        api_url = environ.get("DMS_AGENT_API_URL")
        token = environ.get("DMS_SHARED_TOKEN")
        if _is_placeholder(api_url):
            problems.append("DMS_AGENT_API_URL is missing or a placeholder")
        if _is_placeholder(token):
            problems.append("DMS_SHARED_TOKEN is missing or a placeholder")
        interval = _parse_int(environ, "DMS_AGENT_INTERVAL_SECONDS", 60, problems)
        if problems:
            raise SettingsError(problems)
        return cls(
            api_url=api_url.rstrip("/"),
            shared_token=token,
            node_name=environ.get("DMS_AGENT_NODE_NAME") or socket.gethostname(),
            interval_seconds=interval,
            mountinfo_path=environ.get("DMS_AGENT_MOUNTINFO_PATH", "/proc/1/mountinfo"),
            net_dev_path=environ.get("DMS_AGENT_NET_DEV_PATH", "/proc/net/dev"),
            virtual_net_path=environ.get("DMS_AGENT_VIRTUAL_NET_PATH", ""),
        )
