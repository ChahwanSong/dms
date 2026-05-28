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

    @classmethod
    def from_env(cls) -> "Settings":
        database_url = os.getenv("DMS_DATABASE_URL", "sqlite:///./dms-operational.db")
        observability_url = os.getenv("DMS_OBSERVABILITY_DATABASE_URL", database_url)
        lease = int(os.getenv("DMS_WORKER_LEASE_SECONDS", "300"))
        preview_ttl = int(os.getenv("DMS_PREVIEW_TTL_SECONDS", str(24 * 60 * 60)))
        ldap_base_dn = os.getenv("DMS_LDAP_BASE_DN")
        return cls(
            database_url=database_url,
            observability_database_url=observability_url,
            auth_shared_token=os.getenv("DMS_AUTH_SHARED_TOKEN"),
            default_actor=os.getenv("DMS_DEFAULT_ACTOR"),
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
        )

    @property
    def observability_is_separate(self) -> bool:
        return self.observability_database_url != self.database_url


def _json_env(name: str) -> dict[str, str] | None:
    raw = os.getenv(name)
    if not raw:
        return None
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a JSON object")
    return {str(key): str(item) for key, item in value.items()}
