"""Storage-mapping readiness auto-refresh.

`readiness` (per storage mapping) is a cached projection of fresh agent evidence.
Without periodic refresh it drifts stale in BOTH directions:

  * stale "Missing" blocks valid work (a freshly-deployed DM agent is ignored), and
  * stale "Ready" admits work to a storage whose agents have since disappeared.

This module provides three layers, all of which REUSE the existing
``StorageMappingSanityService.check_mapping`` + ``update_storage_mapping_sanity`` path
(no logic fork — RM and DM stay consistent):

  (1) ``reconcile_once`` — a periodic sweep (cli ``sanity-reconciler --loop``) that
      re-runs sanity for every storage mapping and persists the result. Fail-isolated
      per storage, writes a heartbeat for k8s liveness.
  (2) ``recompute_storage_readiness`` — a single-storage recompute, reused by the
      optional on-ingest hook (refresh right when an agent (re)reports).
  (3) ``readiness_is_stale`` — the planner's fail-safe staleness check (DM only).

The reconciler is an OPTIMISATION, not a correctness dependency: if it dies, the
planner staleness gate (layer 3) degrades to fail-closed for DM, and k8s restarts the
reconciler via the heartbeat-driven liveness probe.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .adapters import KubectlReadOnlyInventoryAdapter
from .adapters.kubernetes_quota import (
    KubernetesNamespaceQuotaLiveAdapter,
    StubKubernetesNamespaceQuotaAdapter,
)
from .config import Settings
from .inventory import EffectiveInventoryService, StorageMappingSanityService
from .repositories import DmsRepository
from .repositories._base import _parse_iso

RECONCILER_ACTOR = "sanity-reconciler"


def _should_use_live_probe(
    settings: Settings, repository: DmsRepository | None
) -> bool:
    """Whether the mutation-transport probe should actually shell out (live adapter) vs
    use a healthy stub.

    Live when managed clusters are configured globally (``DMS_CLUSTER_CONTROL_HOSTS_JSON``
    / ``DMS_CLUSTER_KUBECONFIGS_JSON``) OR when any registered storage mapping pins a
    per-mapping transport (``mutation_mode`` / ``control_host``) -- the latter is how an
    agentless managed cluster (e.g. ddz26 reached via ssh-kubectl + control_host) is
    addressed WITHOUT a global entry, so its sanity must still be probed for real. Tests
    and the default CLI (no global clusters, no per-mapping transport) get the stub, so
    sanity never shells out to ssh/kubectl there."""
    if settings.cluster_control_hosts or settings.cluster_kubeconfigs:
        return True
    if repository is not None:
        for mapping in repository.list_storage_mappings():
            template = mapping.get("backend_template") or {}
            if template.get("mutation_mode") or template.get("control_host"):
                return True
    return False


def mutation_transport_probe_from_settings(
    settings: Settings, repository: DmsRepository | None = None
):
    """Pick the ResourceQuota mutation-transport probe for the sanity service: a live
    adapter (shells out to ``kubectl auth can-i`` over kubectl / ssh-kubectl) when a real
    transport is in play (see :func:`_should_use_live_probe`), otherwise a healthy stub so
    tests / default CLI never shell out (and CSI mappings stay Ready)."""
    if _should_use_live_probe(settings, repository):
        return KubernetesNamespaceQuotaLiveAdapter.from_settings(settings)
    return StubKubernetesNamespaceQuotaAdapter()


def build_sanity_service(
    repository: DmsRepository, settings: Settings
) -> StorageMappingSanityService:
    """Construct the sanity service outside the API, MIRRORING the API wiring
    (``app.create_app``): a read-only Kubernetes inventory adapter is attached so that
    k8s/CSI storage mappings on AGENTLESS managed clusters (reached via kubectl /
    ssh-kubectl, never running a DMS node agent) are reconciled the same way the API
    validates them at registration. Without it the reconciler would see only agent
    reports and flip every managed-cluster mapping back to a spurious ``Failed`` on each
    sweep, defeating the per-mapping ssh-kubectl transport. DM/filesystem readiness is
    still derived from agent reports; the k8s adapter is per-cluster fail-isolated, so an
    unreachable cluster degrades only its own mappings."""
    return StorageMappingSanityService(
        repository=repository,
        inventory_service=EffectiveInventoryService(
            repository=repository,
            kubernetes_inventory=KubectlReadOnlyInventoryAdapter.from_settings(settings),
            settings=settings,
        ),
        settings=settings,
        kubernetes_quota_probe=mutation_transport_probe_from_settings(
            settings, repository
        ),
    )


def recompute_storage_readiness(
    repository: DmsRepository,
    sanity_service: StorageMappingSanityService,
    storage_name: str,
    *,
    actor: str = RECONCILER_ACTOR,
    observability: Any | None = None,
) -> tuple[bool, dict[str, Any] | None]:
    """Re-run sanity for one storage mapping and persist. Returns (changed, readiness).

    ``changed`` is True when the readiness projection flipped (e.g. Missing->Ready),
    which is the only case that emits an observability event (avoids log spam)."""
    mapping = repository.get_storage_mapping(storage_name)
    if not mapping:
        return (False, None)
    before = mapping.get("readiness") or {}
    sanity = sanity_service.check_mapping(mapping)
    after = sanity.get("readiness") or {}
    repository.update_storage_mapping_sanity(
        storage_name,
        sanity_result=sanity,
        readiness=after,
        actor=actor,
    )
    changed = before != after
    if changed and observability is not None:
        observability.safe_record_event(
            component="sanity-reconciler",
            severity="INFO",
            event_type="storage_mapping_readiness_changed",
            message="storage mapping readiness changed",
            payload={
                "storage_name": storage_name,
                "before": before,
                "after": after,
                "sanity_status": sanity.get("status"),
            },
        )
    return (changed, after)


def reconcile_once(
    repository: DmsRepository,
    sanity_service: StorageMappingSanityService,
    *,
    actor: str = RECONCILER_ACTOR,
    observability: Any | None = None,
    heartbeat_path: str | None = None,
) -> int:
    """Re-run sanity for every storage mapping. Returns the number whose readiness
    changed. One storage's failure is isolated so it never kills the whole sweep."""
    mappings = repository.list_storage_mappings(limit=10000)
    changed = 0
    for mapping in mappings:
        storage_name = mapping.get("storage_name")
        if not storage_name:
            continue
        try:
            did_change, _ = recompute_storage_readiness(
                repository,
                sanity_service,
                storage_name,
                actor=actor,
                observability=observability,
            )
            if did_change:
                changed += 1
        except Exception as exc:  # noqa: BLE001 - per-storage fault isolation
            if observability is not None:
                observability.safe_record_event(
                    component="sanity-reconciler",
                    severity="WARN",
                    event_type="storage_mapping_readiness_reconcile_failed",
                    message=f"readiness reconcile failed for {storage_name}",
                    payload={"storage_name": storage_name, "error": str(exc)},
                )
    _write_heartbeat(heartbeat_path, total=len(mappings), changed=changed)
    return changed


def _write_heartbeat(path: str | None, *, total: int, changed: int) -> None:
    """Write a liveness heartbeat each cycle. A k8s liveness probe reads its mtime/age;
    if the loop hangs the heartbeat goes stale and k8s restarts the pod."""
    if not path:
        return
    try:
        Path(path).write_text(
            json.dumps(
                {
                    "ts": datetime.now(UTC).isoformat(),
                    "total": total,
                    "changed": changed,
                }
            )
        )
    except OSError:
        pass


def readiness_is_stale(
    mapping: dict[str, Any],
    *,
    ttl_seconds: float,
    now: datetime | None = None,
) -> bool:
    """Planner fail-safe: True when the stored sanity is older than ttl_seconds (or has
    never been checked / is unparseable). Treat stale as not-ready so the planner never
    acts on evidence the reconciler hasn't refreshed."""
    checked = mapping.get("sanity_checked_at")
    if not checked:
        return True
    reference = now or datetime.now(UTC)
    try:
        age = (reference - _parse_iso(checked)).total_seconds()
    except Exception:  # noqa: BLE001 - unparseable timestamp is treated as stale
        return True
    return age > ttl_seconds
