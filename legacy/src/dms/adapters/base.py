from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class AdapterResult:
    applied_state: dict[str, Any]
    observed_state: dict[str, Any]
    message: str = "stub adapter completed"
    artifact_uri: str | None = None


class KubernetesInventoryReadError(RuntimeError):
    pass


class DataManagementRuntimeError(RuntimeError):
    pass


class KubernetesReadOnlyInventoryAdapter(Protocol):
    def read_inventory(self) -> dict[str, Any]: ...


@dataclass(frozen=True)
class IdentityLookupResult:
    provider: str
    posix_username: str
    uid: int
    primary_gid: int
    groups: list[str]
    user_dn: str
    source_metadata: dict[str, Any]


class IdentityLookupAdapter(Protocol):
    def lookup(
        self, provider: str, posix_username: str
    ) -> IdentityLookupResult | None: ...


class IdentityLookupConfigurationError(RuntimeError):
    """The identity adapter cannot be built/used from the current configuration
    (missing DMS_LDAP_URI/BASE_DN, or the `ldap` extra is not installed).

    Distinct from IdentityLookupReadError (a live lookup failed): this one is a
    STATIC misconfiguration, and the DM worker turns it into a per-request
    `ldap_not_configured` rejection instead of crashing the loop."""


class IdentityLookupReadError(RuntimeError):
    pass

