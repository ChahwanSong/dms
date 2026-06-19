from __future__ import annotations

from dataclasses import dataclass
import json
import os


@dataclass(frozen=True)
class Settings:
    """Runtime settings shared by API and background components."""

    database_url: str
    observability_database_url: str
    auth_shared_token: str | None = None
    default_actor: str | None = None
    require_mtls_header: bool = False
    require_mtls_verified_header: bool = False
    mtls_actor_prefix: str = "mtls:"
    worker_lease_seconds: int = 300
    preview_ttl_seconds: int = 24 * 60 * 60
    ldap_uri: str | None = None
    ldap_base_dn: str | None = None
    ldap_bind_dn: str | None = None
    ldap_bind_password: str | None = None
    ldap_user_search_base: str | None = None
    ldap_group_search_base: str | None = None
    ldap_user_filter: str = "(uid={username})"
    ldap_timeout_seconds: int = 5
    agent_report_stale_seconds: int = 300
    control_cluster_name: str = "cluster-a"
    cluster_kubeconfigs: dict[str, str] | None = None
    cluster_control_hosts: dict[str, str] | None = None
    kubernetes_inventory_mode: str = "ssh-kubectl"
    kubernetes_inventory_timeout_seconds: int = 10
    kubernetes_mutation_mode: str = "ssh-kubectl"
    kubernetes_mutation_timeout_seconds: int = 30
    filesystem_mutation_mode: str = "ssh-host-exec"
    filesystem_exec_timeout_seconds: int = 30
    filesystem_exec_use_sudo: bool = True
    ldap_group_gid_start: int = 9000000
    ldap_group_gid_end: int = 9999999
    dm_namespace: str = "dms"
    dm_job_image: str | None = None
    dm_job_image_ref: str | None = None
    dm_service_account: str = "dms-dm-worker"
    dm_artifact_base_uri: str = "file:///var/lib/dms/artifacts"
    dm_default_priority: str = "Mid"
    # DM request path base. "mount_path" (default, current): paths relative to mount_path.
    # "managed_root": planner prepends the managed_root-relative suffix (volcano/preflight
    # unchanged). See domain.apply_managed_root_suffix.
    dm_path_base: str = "mount_path"
    dm_default_max_nodes: int = 1
    dm_max_nodes: int = 1
    dm_scan_timeout_seconds: int = 3600
    dm_sync_preview_timeout_seconds: int = 1800
    dm_sync_execution_timeout_seconds: int = 3600
    dm_rm_preview_timeout_seconds: int = 1800
    dm_rm_execution_timeout_seconds: int = 3600
    dm_confirm_require_preview_fingerprint: bool = True
    dm_sync_allow_delete: bool = False
    dm_max_sync_nodes: int = 1
    dm_max_rm_nodes: int = 1
    dm_nsync_enabled: bool = True
    dm_nsync_service_prefix: str = "dms-nsync"
    dm_monitor_poll_seconds: int = 5
    dm_job_delete_on_terminal: bool = False
    dm_kubernetes_mode: str = "cluster"
    dm_policy_default_worker_nodes: int = 3
    dm_policy_max_worker_nodes: int = 3
    dm_policy_default_processes_per_node: int = 3
    dm_policy_max_processes_per_node: int = 10
    dm_policy_default_queue: str | None = "dms-data"
    dm_policy_default_priority_class: str | None = "dms-normal"
    dm_scheduler_backend: str = "auto"
    dm_identity_provider: str = "ldap"
    # Lowest acceptable POSIX uid/gid for a DM-resolved identity. Data jobs run as the
    # resolved user via `runuser` inside a ROOT MPI worker pod; an LDAP entry that maps to
    # uid 0 (or any system account) would run the data operation AS ROOT and defeat the
    # POSIX-identity isolation. Resolved identities below these floors (or uid/gid 0) are
    # rejected with `uid_below_floor`.
    dm_min_uid: int = 1000
    dm_min_gid: int = 1000
    # Storage-mapping readiness auto-refresh (sanity reconciler). readiness is a cached
    # projection of agent evidence; without periodic refresh it drifts stale in BOTH
    # directions (stale "Missing" blocks valid work, stale "Ready" admits work after the
    # evidence is gone). These knobs power the reconciler (cli `sanity-reconciler`), the
    # planner staleness gate (DM only), and the optional on-ingest recompute.
    sanity_reconcile_enabled: bool = True
    sanity_reconcile_interval_seconds: float = 30.0
    sanity_reconcile_heartbeat_path: str | None = None
    sanity_ttl_seconds: float = 120.0
    sanity_event_recompute_enabled: bool = False
    # Planner staleness gate (DM only). OFF by default so enabling it is a deliberate,
    # post-reconciler-deploy step: turning it on without a running reconciler would
    # fail-close DM requests once sanity_checked_at ages past sanity_ttl_seconds.
    sanity_planner_gate_enabled: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        database_url = os.getenv("DMS_DATABASE_URL", "sqlite:///./dms-operational.db")
        observability_url = os.getenv("DMS_OBSERVABILITY_DATABASE_URL", database_url)
        lease = int(os.getenv("DMS_WORKER_LEASE_SECONDS", "300"))
        preview_ttl = int(os.getenv("DMS_PREVIEW_TTL_SECONDS", str(24 * 60 * 60)))
        ldap_base_dn = os.getenv("DMS_LDAP_BASE_DN")
        require_mtls_header = _bool_env("DMS_REQUIRE_MTLS_HEADER", False)
        require_mtls_verified_header = _bool_env(
            "DMS_REQUIRE_MTLS_VERIFIED_HEADER", False
        )
        if require_mtls_verified_header and not require_mtls_header:
            raise ValueError(
                "DMS_REQUIRE_MTLS_VERIFIED_HEADER=true requires "
                "DMS_REQUIRE_MTLS_HEADER=true"
            )
        default_actor = os.getenv("DMS_DEFAULT_ACTOR")
        if require_mtls_header and default_actor:
            raise ValueError(
                "DMS_DEFAULT_ACTOR must not be set when "
                "DMS_REQUIRE_MTLS_HEADER=true"
            )
        return cls(
            database_url=database_url,
            observability_database_url=observability_url,
            auth_shared_token=os.getenv("DMS_AUTH_SHARED_TOKEN"),
            default_actor=default_actor,
            require_mtls_header=require_mtls_header,
            require_mtls_verified_header=require_mtls_verified_header,
            mtls_actor_prefix=os.getenv("DMS_MTLS_ACTOR_PREFIX", "mtls:"),
            worker_lease_seconds=lease,
            preview_ttl_seconds=preview_ttl,
            ldap_uri=os.getenv("DMS_LDAP_URI"),
            ldap_base_dn=ldap_base_dn,
            ldap_bind_dn=os.getenv("DMS_LDAP_BIND_DN"),
            ldap_bind_password=os.getenv("DMS_LDAP_BIND_PASSWORD"),
            ldap_user_search_base=os.getenv(
                "DMS_LDAP_USER_SEARCH_BASE",
                f"ou=people,{ldap_base_dn}" if ldap_base_dn else None,
            ),
            ldap_group_search_base=os.getenv(
                "DMS_LDAP_GROUP_SEARCH_BASE",
                f"ou=groups,{ldap_base_dn}" if ldap_base_dn else None,
            ),
            ldap_user_filter=os.getenv("DMS_LDAP_USER_FILTER", "(uid={username})"),
            ldap_timeout_seconds=int(os.getenv("DMS_LDAP_TIMEOUT_SECONDS", "5")),
            agent_report_stale_seconds=int(
                os.getenv("DMS_AGENT_REPORT_STALE_SECONDS", "300")
            ),
            control_cluster_name=os.getenv("DMS_CONTROL_CLUSTER_NAME", "cluster-a"),
            cluster_kubeconfigs=_json_env("DMS_CLUSTER_KUBECONFIGS_JSON"),
            cluster_control_hosts=_json_env("DMS_CLUSTER_CONTROL_HOSTS_JSON"),
            kubernetes_inventory_mode=os.getenv(
                "DMS_KUBERNETES_INVENTORY_MODE", "ssh-kubectl"
            ),
            kubernetes_inventory_timeout_seconds=int(
                os.getenv("DMS_KUBERNETES_INVENTORY_TIMEOUT_SECONDS", "10")
            ),
            kubernetes_mutation_mode=os.getenv(
                "DMS_KUBERNETES_MUTATION_MODE", "ssh-kubectl"
            ),
            kubernetes_mutation_timeout_seconds=int(
                os.getenv("DMS_KUBERNETES_MUTATION_TIMEOUT_SECONDS", "30")
            ),
            filesystem_mutation_mode=os.getenv(
                "DMS_FILESYSTEM_MUTATION_MODE", "ssh-host-exec"
            ),
            filesystem_exec_timeout_seconds=int(
                os.getenv("DMS_FILESYSTEM_EXEC_TIMEOUT_SECONDS", "30")
            ),
            filesystem_exec_use_sudo=os.getenv(
                "DMS_FILESYSTEM_EXEC_USE_SUDO", "true"
            ).lower()
            not in {"0", "false", "no"},
            ldap_group_gid_start=int(os.getenv("DMS_LDAP_GROUP_GID_START", "9000000")),
            ldap_group_gid_end=int(os.getenv("DMS_LDAP_GROUP_GID_END", "9999999")),
            dm_namespace=os.getenv("DMS_DM_NAMESPACE", "dms"),
            dm_job_image=os.getenv("DMS_DM_JOB_IMAGE"),
            dm_job_image_ref=os.getenv("DMS_DM_JOB_IMAGE_REF"),
            dm_service_account=os.getenv("DMS_DM_SERVICE_ACCOUNT", "dms-dm-worker"),
            dm_artifact_base_uri=os.getenv(
                "DMS_DM_ARTIFACT_BASE_URI", "file:///var/lib/dms/artifacts"
            ),
            dm_default_priority=os.getenv("DMS_DM_DEFAULT_PRIORITY", "Mid"),
            dm_path_base=_choice_env(
                "DMS_DM_PATH_BASE", "mount_path", {"mount_path", "managed_root"}
            ),
            dm_default_max_nodes=int(os.getenv("DMS_DM_DEFAULT_MAX_NODES", "1")),
            dm_max_nodes=int(os.getenv("DMS_DM_MAX_NODES", "1")),
            dm_scan_timeout_seconds=int(os.getenv("DMS_DM_SCAN_TIMEOUT_SECONDS", "3600")),
            dm_sync_preview_timeout_seconds=int(
                os.getenv("DMS_DM_SYNC_PREVIEW_TIMEOUT_SECONDS", "1800")
            ),
            dm_sync_execution_timeout_seconds=int(
                os.getenv("DMS_DM_SYNC_EXECUTION_TIMEOUT_SECONDS", "3600")
            ),
            dm_rm_preview_timeout_seconds=int(
                os.getenv("DMS_DM_RM_PREVIEW_TIMEOUT_SECONDS", "1800")
            ),
            dm_rm_execution_timeout_seconds=int(
                os.getenv("DMS_DM_RM_EXECUTION_TIMEOUT_SECONDS", "3600")
            ),
            dm_confirm_require_preview_fingerprint=_bool_env(
                "DMS_DM_CONFIRM_REQUIRE_PREVIEW_FINGERPRINT", True
            ),
            dm_sync_allow_delete=_bool_env("DMS_DM_SYNC_ALLOW_DELETE", False),
            dm_max_sync_nodes=int(os.getenv("DMS_DM_MAX_SYNC_NODES", "1")),
            dm_max_rm_nodes=int(os.getenv("DMS_DM_MAX_RM_NODES", "1")),
            dm_nsync_enabled=_bool_env("DMS_DM_NSYNC_ENABLED", True),
            dm_nsync_service_prefix=os.getenv(
                "DMS_DM_NSYNC_SERVICE_PREFIX", "dms-nsync"
            ),
            dm_monitor_poll_seconds=int(os.getenv("DMS_DM_MONITOR_POLL_SECONDS", "5")),
            dm_job_delete_on_terminal=_bool_env("DMS_DM_JOB_DELETE_ON_TERMINAL", False),
            dm_kubernetes_mode=os.getenv("DMS_DM_KUBERNETES_MODE", "cluster"),
            dm_policy_default_worker_nodes=int(
                os.getenv("DMS_DM_POLICY_DEFAULT_WORKER_NODES", "3")
            ),
            dm_policy_max_worker_nodes=int(
                os.getenv("DMS_DM_POLICY_MAX_WORKER_NODES", "3")
            ),
            dm_policy_default_processes_per_node=int(
                os.getenv("DMS_DM_POLICY_DEFAULT_PROCESSES_PER_NODE", "3")
            ),
            dm_policy_max_processes_per_node=int(
                os.getenv("DMS_DM_POLICY_MAX_PROCESSES_PER_NODE", "10")
            ),
            dm_policy_default_queue=os.getenv("DMS_DM_POLICY_DEFAULT_QUEUE", "dms-data"),
            dm_policy_default_priority_class=os.getenv(
                "DMS_DM_POLICY_DEFAULT_PRIORITY_CLASS", "dms-normal"
            ),
            dm_scheduler_backend=os.getenv("DMS_DM_SCHEDULER_BACKEND", "auto"),
            dm_identity_provider=os.getenv("DMS_DM_IDENTITY_PROVIDER", "ldap"),
            dm_min_uid=int(os.getenv("DMS_DM_MIN_UID", "1000")),
            dm_min_gid=int(os.getenv("DMS_DM_MIN_GID", "1000")),
            sanity_reconcile_enabled=_bool_env("DMS_SANITY_RECONCILE_ENABLED", True),
            sanity_reconcile_interval_seconds=float(
                os.getenv("DMS_SANITY_RECONCILE_INTERVAL_SECONDS", "30")
            ),
            sanity_reconcile_heartbeat_path=os.getenv(
                "DMS_SANITY_RECONCILE_HEARTBEAT_PATH"
            ),
            sanity_ttl_seconds=float(os.getenv("DMS_SANITY_TTL_SECONDS", "120")),
            sanity_event_recompute_enabled=_bool_env(
                "DMS_SANITY_EVENT_RECOMPUTE_ENABLED", False
            ),
            sanity_planner_gate_enabled=_bool_env(
                "DMS_SANITY_PLANNER_GATE_ENABLED", False
            ),
        )

    @property
    def observability_is_separate(self) -> bool:
        return self.observability_database_url != self.database_url

    def data_management_policy_defaults(self) -> list[dict[str, object]]:
        default_nodes = self.dm_policy_default_worker_nodes
        max_nodes = self.dm_policy_max_worker_nodes
        default_processes = self.dm_policy_default_processes_per_node
        max_processes = self.dm_policy_max_processes_per_node
        common = {
            "default_processes_per_node": default_processes,
            "max_processes_per_node": max_processes,
            "default_queue": self.dm_policy_default_queue,
            "default_priority_class": self.dm_policy_default_priority_class,
            "enabled": True,
        }
        return [
            {
                "operation": "scan",
                "default_worker_nodes": default_nodes,
                "max_worker_nodes": max_nodes,
                **common,
            },
            {
                "operation": "rm",
                "default_worker_nodes": default_nodes,
                "max_worker_nodes": max_nodes,
                **common,
            },
            {
                "operation": "dsync",
                "default_worker_nodes": default_nodes,
                "max_worker_nodes": max_nodes,
                **common,
            },
            {
                "operation": "nsync",
                "default_source_nodes": default_nodes,
                "default_destination_nodes": default_nodes,
                "max_source_nodes": max_nodes,
                "max_destination_nodes": max_nodes,
                **common,
            },
        ]


def _json_env(name: str) -> dict[str, str] | None:
    raw = os.getenv(name)
    if not raw:
        return None
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a JSON object")
    return {str(key): str(item) for key, item in value.items()}


def _choice_env(name: str, default: str, allowed: set[str]) -> str:
    value = os.getenv(name, default)
    if value not in allowed:
        raise ValueError(f"{name} must be one of {sorted(allowed)}")
    return value


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")
