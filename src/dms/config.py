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
)
# 재시도 설정은 두지 않는다: 상위 스펙에 재시도 요구가 없고, 실패한 rm/sync 를 자동으로
# 재실행하는 것은 파괴적이다. 재실행은 배치 :rerun-failed 와 사용자 재제출로 한다.
# (DMS_JOB_MAX_ATTEMPTS 는 소비처가 0건인 채로 오래 남아 있어 슬라이스 10 에서 제거했다.)


class SettingsError(Exception):
    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


def _is_placeholder(value: str | None) -> bool:
    return not value or value == "CHANGE_ME" or value.startswith("REPLACE_WITH_")


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
    execution_backend: str = "stub"
    job_image: str = ""
    k8s_namespace: str = "dms"
    static_dir: str | None = None
    batch_orchestrator_interval_seconds: int = 5
    vcjob_ttl_seconds: int = 86400
    pod_gc_after_seconds: int = 86400
    pod_gc_interval_seconds: int = 600

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
            ldap_bind_dn=environ.get("DMS_LDAP_BIND_DN", ""),
            ldap_bind_pw=environ.get("DMS_LDAP_BIND_PW", ""),
            execution_backend=environ.get("DMS_EXECUTION_BACKEND", "stub"),
            job_image=environ.get("DMS_JOB_IMAGE", ""),
            k8s_namespace=environ.get("DMS_K8S_NAMESPACE", "dms"),
            static_dir=environ.get("DMS_STATIC_DIR"),
        )


@dataclass(frozen=True)
class AgentSettings:
    api_url: str
    shared_token: str
    node_name: str
    interval_seconds: int = 60
    mountinfo_path: str = "/proc/1/mountinfo"

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
        )
