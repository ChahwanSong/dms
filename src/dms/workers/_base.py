from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import threading
from typing import Any
from urllib.parse import urlparse

from ..adapters import (
    AdapterResult,
    BackendPreconditionError,
    DataManagementRuntimeError,
    FilesystemBackendAdapter,
    IdentityLookupConfigurationError,
    KubernetesNamespaceQuotaAdapter,
    StubFilesystemBackendAdapter,
    StubKubernetesNamespaceQuotaAdapter,
    zero_kubernetes_resource_quota_hard,
)
from ..backend_registry import BackendAdapterRegistry
from ..domain import (
    DataJobState,
    IdentityMappingStatus,
    LifecycleState,
    OperationKind,
    ResourceKind,
    WorkerRole,
)
from ..repositories import (
    DmsRepository,
    ObservabilityRepository,
    SchedulingBlocked,
    iso_at,
)




class RunHeartbeat:
    def __init__(
        self,
        *,
        repository: DmsRepository,
        observability: ObservabilityRepository,
        run_id: str,
        worker_id: str,
        lease_seconds: int,
        interval_seconds: float | None = None,
    ) -> None:
        self.repository = repository
        self.observability = observability
        self.run_id = run_id
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.interval_seconds = (
            interval_seconds
            if interval_seconds is not None
            else max(0.1, min(60.0, lease_seconds / 3))
        )
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> RunHeartbeat:
        if self.lease_seconds <= 0:
            return self
        self._thread = threading.Thread(
            target=self._run,
            name=f"dms-run-heartbeat-{self.run_id}",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=min(5.0, self.interval_seconds))

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                self.repository.heartbeat_run(self.run_id, self.lease_seconds)
            except (
                Exception
            ) as exc:  # noqa: BLE001 - heartbeat must not fail backend work.
                self.observability.safe_record_event(
                    component="worker-heartbeat",
                    severity="WARN",
                    event_type="run_heartbeat_failed",
                    message=str(exc),
                    payload={"run_id": self.run_id, "worker_id": self.worker_id},
                )

