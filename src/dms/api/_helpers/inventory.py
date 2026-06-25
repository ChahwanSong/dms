"""Inventory and storage-mapping sanity service factories."""

from __future__ import annotations

from ...inventory import EffectiveInventoryService, StorageMappingSanityService
from ...sanity_reconciler import (
    _should_use_live_probe,
    mutation_transport_probe_from_settings,
)
from .._services import AppServices


def inventory_service(services: AppServices) -> EffectiveInventoryService:
    return EffectiveInventoryService(
        repository=services.repository,
        kubernetes_inventory=services.kubernetes_inventory,
        settings=services.settings,
    )


def sanity_service(services: AppServices) -> StorageMappingSanityService:
    # Use the wired quota adapter (live in prod) as the mutation-transport probe when it
    # exposes the probe AND a real transport is in play -- either a global managed cluster
    # or any mapping pinning a per-mapping mutation_mode/control_host (e.g. ddz26 via
    # ssh-kubectl, which has no global entry). Otherwise (tests / default CLI with no
    # transport at all) fall back to a healthy stub so sanity never shells out.
    probe = services.kubernetes_quota
    if probe is None or not hasattr(probe, "check_mutation_transport"):
        probe = mutation_transport_probe_from_settings(
            services.settings, services.repository
        )
    elif not _should_use_live_probe(services.settings, services.repository):
        probe = mutation_transport_probe_from_settings(
            services.settings, services.repository
        )
    return StorageMappingSanityService(
        repository=services.repository,
        inventory_service=inventory_service(services),
        settings=services.settings,
        kubernetes_quota_probe=probe,
    )
