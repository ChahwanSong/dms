from __future__ import annotations

from dataclasses import dataclass
import json
import os


@dataclass(frozen=True)
class Settings:
    """Runtime settings shared by API and background components."""

    database_url: str
    observability_database_url: str
    # PostgreSQL connection-pool sizing + per-session safety timeouts. Defaults are
    # chosen so a typical deployment stays comfortably under the stock
    # max_connections=100; see operational_pool_config / observability_pool_config.
    # min_size is the pool FLOOR — connections kept warm even when unused. The API keeps
    # a warm floor (db_pool_min_size) so user-facing requests never pay cold-connect
    # latency on the first hit. Loop processes (planner / rm-worker / dm-worker / sanity
    # / retention) are NOT latency-sensitive, so they use db_worker_pool_min_size (0):
    # they open connections on demand and let idle ones reap (psycopg_pool max_idle),
    # which removes the per-replica idle floor that dominates the connection budget when
    # scaling to many workers. NOTE: the operational pool of a loop worker still stays
    # warm in practice because run_once polls (claim_next_plan + leader upsert) every
    # --interval seconds (< max_idle), so it is legitimately in use; min_size=0 mainly
    # frees the OBSERVABILITY floor, which idle workers do not touch between events.
    db_pool_min_size: int = 1
    db_worker_pool_min_size: int = 0
    # Loop processes are single-threaded — each holds at most ~1 connection at a time
    # (up to ~2 op during a run when the heartbeat thread overlaps the main thread), so a
    # small pool suffices. The API is concurrent (sync handlers in the anyio threadpool),
    # so it gets a larger pool AND its threadpool is capped to db_api_pool_max_size (see
    # api/app.py) so it can never oversubscribe the pool nor wait on checkout.
    db_pool_max_size: int = 4
    db_api_pool_max_size: int = 16
    db_observability_pool_max_size: int = 3
    # Checkout wait MUST be >= statement_timeout: a waiter has to outlast one
    # legitimately-slow (but bounded) query holding a connection, else waiters fail
    # with PoolTimeout before the busy backend is freed.
    db_pool_timeout_seconds: float = 35.0
    db_statement_timeout_ms: int = 30000
    db_idle_in_txn_timeout_ms: int = 60000
    auth_shared_token: str | None = None
    default_actor: str | None = None
    require_mtls_header: bool = False
    require_mtls_verified_header: bool = False
    mtls_actor_prefix: str = "mtls:"
    worker_lease_seconds: int = 300
    # Single-holder lease for the periodic recovery sweeps (mark_stale/close-orphaned/…).
    # Kept short (a few loop intervals) so a leader that gets busy running a job relinquishes
    # quickly and an idle replica takes over the sweeps. See workers' run_once + try_acquire_leader.
    recovery_sweep_lease_seconds: int = 30
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
    # (A′) Recency window for data-job action-required: only terminal-failed jobs
    # updated within this many seconds alarm; older ones stay as history but stop
    # surfacing, so the alarm is bounded WITHOUT deleting records. Default 7 days;
    # 0 disables the window (legacy: every matching terminal job alarms forever).
    data_job_attention_window_seconds: int = 7 * 24 * 3600
    # agent_reports history retention (the `dms retention` loop). The table grows to
    # millions of rows at 100+ nodes reporting ~1/min; node-health reads agent_node_current
    # (current state preserved independently), so pruning old history is safe. The
    # retention window is FLOORED well above the 72h node-metrics sparkline window
    # (clamped to >= 7 days at parse time) so dashboards never lose their lookback.
    agent_report_retention_seconds: int = 30 * 24 * 60 * 60  # 30 days
    agent_report_retention_interval_seconds: int = 3600
    agent_report_retention_heartbeat_path: str | None = None
    control_cluster_name: str = "cluster-a"
    cluster_kubeconfigs: dict[str, str] | None = None
    cluster_control_hosts: dict[str, str] | None = None
    kubernetes_inventory_mode: str = "kubectl"
    kubernetes_inventory_timeout_seconds: int = 10
    kubernetes_mutation_mode: str = "kubectl"
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
    dm_sync_preview_timeout_seconds: int = 3600
    dm_sync_execution_timeout_seconds: int = 259200
    dm_rm_preview_timeout_seconds: int = 1800
    dm_rm_execution_timeout_seconds: int = 3600
    dm_confirm_require_preview_fingerprint: bool = True
    dm_sync_allow_delete: bool = True
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
    # On-demand identity probing: the DM worker registers a job's resolved POSIX
    # username as a probe target; agents receive recent targets in the report-POST
    # response and probe them next cycle (identity evidence without static list
    # maintenance). wait = how long the worker blocks at identity-resolve time for
    # evidence to appear on ANY fresh DM node before proceeding to (per-node) gating;
    # 0 disables the wait (register-only). ttl = how long a registered target stays
    # in the set handed to agents.
    dm_identity_probe_wait_seconds: float = 90.0
    dm_identity_probe_poll_seconds: float = 5.0
    dm_identity_probe_target_ttl_seconds: int = 3600
    # Privileged (root) DM execution (default OFF -> identical to no-feature behavior).
    # When ON, a DM request whose effective owner_username/requester_id is in
    # `dm_privileged_requesters` resolves to a SYNTHESIZED root identity (uid/gid 0),
    # bypassing the LDAP lookup and the uid/gid floor. The API edge
    # (authorize_privileged_requester_or_403) authorizes the CALLER first: feature flag on,
    # mTLS-verified operator (actor with `mtls_actor_prefix`), optional operator allowlist,
    # and `dm_privileged_scopes` (empty = all storages). The denylist still applies as a
    # kill-switch. See install/4.dms-dm-api.md.
    dm_allow_root_requester: bool = True
    dm_privileged_requesters: frozenset[str] = frozenset({"root"})
    dm_privileged_uid: int = 0
    dm_privileged_gid: int = 0
    dm_privileged_operators: frozenset[str] = frozenset()
    dm_privileged_scopes: frozenset[str] = frozenset()
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
            db_pool_min_size=int(os.getenv("DMS_DB_POOL_MIN_SIZE", "1")),
            db_worker_pool_min_size=int(
                os.getenv("DMS_DB_WORKER_POOL_MIN_SIZE", "0")
            ),
            db_pool_max_size=int(os.getenv("DMS_DB_POOL_MAX_SIZE", "4")),
            db_api_pool_max_size=int(os.getenv("DMS_DB_API_POOL_MAX_SIZE", "16")),
            db_observability_pool_max_size=int(
                os.getenv("DMS_DB_OBSERVABILITY_POOL_MAX_SIZE", "3")
            ),
            db_pool_timeout_seconds=float(
                os.getenv("DMS_DB_POOL_TIMEOUT_SECONDS", "35")
            ),
            db_statement_timeout_ms=int(
                os.getenv("DMS_DB_STATEMENT_TIMEOUT_MS", "30000")
            ),
            db_idle_in_txn_timeout_ms=int(
                os.getenv("DMS_DB_IDLE_IN_TXN_TIMEOUT_MS", "60000")
            ),
            auth_shared_token=os.getenv("DMS_AUTH_SHARED_TOKEN"),
            default_actor=default_actor,
            require_mtls_header=require_mtls_header,
            require_mtls_verified_header=require_mtls_verified_header,
            mtls_actor_prefix=os.getenv("DMS_MTLS_ACTOR_PREFIX", "mtls:"),
            worker_lease_seconds=lease,
            recovery_sweep_lease_seconds=int(
                os.getenv("DMS_RECOVERY_SWEEP_LEASE_SECONDS", "30")
            ),
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
            data_job_attention_window_seconds=int(
                os.getenv("DMS_DATA_JOB_ATTENTION_WINDOW_SECONDS", str(7 * 24 * 3600))
            ),
            # Floor the retention window to >= 7 days so it can never be set below the
            # 72h metrics window (which would prune data the sparklines still read).
            agent_report_retention_seconds=max(
                7 * 24 * 60 * 60,
                int(
                    os.getenv(
                        "DMS_AGENT_REPORT_RETENTION_SECONDS", str(30 * 24 * 60 * 60)
                    )
                ),
            ),
            agent_report_retention_interval_seconds=int(
                os.getenv("DMS_AGENT_REPORT_RETENTION_INTERVAL_SECONDS", "3600")
            ),
            agent_report_retention_heartbeat_path=os.getenv(
                "DMS_AGENT_REPORT_RETENTION_HEARTBEAT_PATH"
            ),
            control_cluster_name=os.getenv("DMS_CONTROL_CLUSTER_NAME", "cluster-a"),
            cluster_kubeconfigs=_json_env("DMS_CLUSTER_KUBECONFIGS_JSON"),
            cluster_control_hosts=_json_env("DMS_CLUSTER_CONTROL_HOSTS_JSON"),
            kubernetes_inventory_mode=os.getenv(
                "DMS_KUBERNETES_INVENTORY_MODE", "kubectl"
            ),
            kubernetes_inventory_timeout_seconds=int(
                os.getenv("DMS_KUBERNETES_INVENTORY_TIMEOUT_SECONDS", "10")
            ),
            kubernetes_mutation_mode=os.getenv(
                "DMS_KUBERNETES_MUTATION_MODE", "kubectl"
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
                os.getenv("DMS_DM_SYNC_PREVIEW_TIMEOUT_SECONDS", "3600")
            ),
            dm_sync_execution_timeout_seconds=int(
                os.getenv("DMS_DM_SYNC_EXECUTION_TIMEOUT_SECONDS", "259200")
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
            dm_sync_allow_delete=_bool_env("DMS_DM_SYNC_ALLOW_DELETE", True),
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
            dm_identity_probe_wait_seconds=float(
                os.getenv("DMS_DM_IDENTITY_PROBE_WAIT_SECONDS", "90")
            ),
            dm_identity_probe_poll_seconds=float(
                os.getenv("DMS_DM_IDENTITY_PROBE_POLL_SECONDS", "5")
            ),
            dm_identity_probe_target_ttl_seconds=int(
                os.getenv("DMS_DM_IDENTITY_PROBE_TARGET_TTL_SECONDS", "3600")
            ),
            dm_allow_root_requester=_bool_env("DMS_DM_ALLOW_ROOT_REQUESTER", True),
            dm_privileged_requesters=_csv_set_env(
                "DMS_DM_PRIVILEGED_REQUESTERS", "root"
            ),
            dm_privileged_uid=int(os.getenv("DMS_DM_PRIVILEGED_UID", "0")),
            dm_privileged_gid=int(os.getenv("DMS_DM_PRIVILEGED_GID", "0")),
            dm_privileged_operators=_csv_set_env("DMS_DM_PRIVILEGED_OPERATORS", ""),
            dm_privileged_scopes=_csv_set_env("DMS_DM_PRIVILEGED_SCOPES", ""),
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

    def _role_pool_min_size(self, role: str) -> int:
        """Pool FLOOR by role: the API keeps a warm floor (latency), loop workers use
        db_worker_pool_min_size (0 by default) so idle replicas hold no floor and connect
        on demand. Applies identically to the operational and observability pools."""
        return (
            self.db_pool_min_size
            if role == "api"
            else self.db_worker_pool_min_size
        )

    def operational_pool_config(self, *, role: str = "worker") -> "PoolConfig":
        """Pool config for the operational DB (request/plan/run/resource writes).

        ``role="api"`` uses the larger ``db_api_pool_max_size`` (the API is
        concurrent); ``role="worker"`` (default, for the single-threaded loop
        processes + one-shot CLI) uses the small ``db_pool_max_size``.

        The FLOOR (min_size) is role-aware too — see ``_role_pool_min_size``.
        """
        from .db import PoolConfig

        max_size = (
            self.db_api_pool_max_size if role == "api" else self.db_pool_max_size
        )
        return PoolConfig(
            min_size=min(self._role_pool_min_size(role), max_size),
            max_size=max_size,
            timeout=self.db_pool_timeout_seconds,
            statement_timeout_ms=self.db_statement_timeout_ms,
            idle_in_txn_timeout_ms=self.db_idle_in_txn_timeout_ms,
        )

    def observability_pool_config(self, *, role: str = "worker") -> "PoolConfig":
        """Pool config for the observability DB.

        The observability DB sees far lighter write traffic (diagnostic events) than
        the operational DB, so it gets a smaller max_size. SIZING MATH (worst-case
        ceiling): total PG connections per server <= sum over processes of
        (op_max_size + obs_max_size). With defaults — API*2 at (16+3) and the 5
        single-threaded loops (planner/rm-worker/dm-worker/sanity/retention) at
        (4+3) — that is 2*19 + 5*7 = 38 + 35 = 73, plus a transient migrate Job,
        comfortably under the stock max_connections=100 (minus superuser_reserved=3).
        That is the CEILING; steady-state is far lower — loops hold ~1 op each (kept
        warm by --interval polling) and, with role="worker" min_size=0, ZERO idle obs
        connections between events (measured: idle worker obs connections sat 4.6h
        unused under the old min_size=1 floor). The API keeps a warm obs floor so its
        diagnostic writes also avoid cold-connect latency. To scale concurrency for
        100+ nodes raise db_api_pool_max_size AND PostgreSQL max_connections together
        (e.g. postgres -c max_connections=300), re-checking the ceiling stays under
        max_connections - superuser_reserved.
        """
        from .db import PoolConfig

        return PoolConfig(
            min_size=min(
                self._role_pool_min_size(role), self.db_observability_pool_max_size
            ),
            max_size=self.db_observability_pool_max_size,
            timeout=self.db_pool_timeout_seconds,
            statement_timeout_ms=self.db_statement_timeout_ms,
            idle_in_txn_timeout_ms=self.db_idle_in_txn_timeout_ms,
        )

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


def _csv_set_env(name: str, default: str) -> frozenset[str]:
    raw = os.getenv(name)
    if raw is None:
        raw = default
    return frozenset(item.strip() for item in raw.split(",") if item.strip())
