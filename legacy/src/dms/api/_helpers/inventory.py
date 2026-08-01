"""Inventory and storage-mapping sanity service factories."""

from __future__ import annotations

from ...inventory import EffectiveInventoryService, StorageMappingSanityService
from .._services import AppServices


def inventory_service(services: AppServices) -> EffectiveInventoryService:
    return EffectiveInventoryService(
        repository=services.repository,
        kubernetes_inventory=services.kubernetes_inventory,
        settings=services.settings,
    )


def sanity_service(services: AppServices) -> StorageMappingSanityService:
    return StorageMappingSanityService(
        repository=services.repository,
        inventory_service=inventory_service(services),
        settings=services.settings,
    )
