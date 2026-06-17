"""Operational + observability repositories.

Historically a single repositories.py module. DmsRepository is now assembled
from domain mixins, but the public ``dms.repositories`` import surface is
unchanged: every prior symbol is re-exported here.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..db import Database
from ._base import *  # noqa: F401,F403
from .requests import RequestsMixin
from .execution import ExecutionMixin
from .resources import ResourcesMixin
from .storage_mappings import StorageMappingsMixin
from .policies import PoliciesMixin
from .identity import DmIdentityDenylistMixin
from .data_jobs import DataJobsMixin
from .operational import OperationalMixin


@dataclass
class DmsRepository(
    RequestsMixin,
    ExecutionMixin,
    ResourcesMixin,
    StorageMappingsMixin,
    PoliciesMixin,
    DmIdentityDenylistMixin,
    DataJobsMixin,
    OperationalMixin,
):
    """Operational-store repository (composed from domain mixins)."""

    database: Database
