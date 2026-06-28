"""Typed async HTTP client for the DMS API — the ONLY thing that talks to DMS.

The portal is a pure client of the DMS `/api/v1/` surface (see CLAUDE.md). This
wraps the storage-mapping endpoints the operator console needs, attaches the
testbed auth (bearer token + ``x-dms-actor``), and translates DMS error
responses into ``DmsApiError`` so routers can re-raise them with the same status.

Storage-mapping endpoints used:
- GET    /api/v1/operations/storage-mappings(?cluster_name=)        list (redacted)
- GET    /api/v1/operations/storage-mappings/{name}                 get (redacted)
- POST   /api/v1/resource-management/storage-mappings               upsert/create
- PATCH  /api/v1/resource-management/storage-mappings/{name}        update (full body)
- POST   /api/v1/resource-management/storage-mappings/{name}:check  re-run sanity
- DELETE /api/v1/resource-management/storage-mappings/{name}        hard delete
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from .config import Settings

_OPS = "/api/v1/operations/storage-mappings"
_OPS_BASE = "/api/v1/operations"
_RM = "/api/v1/resource-management/storage-mappings"
_DM = "/api/v1/data-management"
_DATA_JOBS = "/api/v1/operations/data-jobs"


class DmsApiError(Exception):
    """A non-2xx response (or transport failure) from the DMS API."""

    def __init__(self, status_code: int, detail: Any):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"DMS API error {status_code}: {detail}")


class DmsNotConfigured(DmsApiError):
    def __init__(self) -> None:
        super().__init__(503, "dms_not_configured")


class DmsClient:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._base = (settings.dms_api_url or "").rstrip("/")
        self._client: httpx.AsyncClient | None = None
        if settings.dms_configured:
            self._client = httpx.AsyncClient(
                timeout=settings.dms_timeout_seconds,
                verify=settings.dms_verify_tls,
            )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    def _headers(self, actor: str | None) -> dict[str, str]:
        headers = {"x-dms-actor": actor or self._settings.dms_actor}
        if self._settings.dms_token:
            headers["authorization"] = f"Bearer {self._settings.dms_token}"
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        actor: str | None,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
    ) -> Any:
        if self._client is None:
            raise DmsNotConfigured()
        try:
            resp = await self._client.request(
                method,
                f"{self._base}{path}",
                headers=self._headers(actor),
                params=params,
                json=json,
            )
        except httpx.HTTPError as exc:  # transport-level failure
            raise DmsApiError(502, f"dms_unreachable: {exc}") from exc
        if resp.status_code >= 400:
            raise DmsApiError(resp.status_code, _extract_detail(resp))
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    # --- storage mappings -------------------------------------------------

    async def list_storage_mappings(
        self, *, actor: str | None = None, cluster_name: str | None = None
    ) -> list[dict[str, Any]]:
        params = {"cluster_name": cluster_name} if cluster_name else None
        return await self._request("GET", _OPS, actor=actor, params=params)

    async def get_storage_mapping(
        self, storage_name: str, *, actor: str | None = None
    ) -> dict[str, Any]:
        return await self._request(
            "GET", f"{_OPS}/{_seg(storage_name)}", actor=actor
        )

    async def upsert_storage_mapping(
        self, body: dict[str, Any], *, actor: str | None = None
    ) -> dict[str, Any]:
        return await self._request("POST", _RM, actor=actor, json=body)

    async def patch_storage_mapping(
        self, storage_name: str, body: dict[str, Any], *, actor: str | None = None
    ) -> dict[str, Any]:
        return await self._request(
            "PATCH", f"{_RM}/{_seg(storage_name)}", actor=actor, json=body
        )

    async def check_storage_mapping(
        self, storage_name: str, *, actor: str | None = None
    ) -> dict[str, Any]:
        # DMS uses a colon-suffixed action route: {name}:check
        return await self._request(
            "POST", f"{_RM}/{_seg(storage_name)}:check", actor=actor
        )

    async def delete_storage_mapping(
        self, storage_name: str, *, actor: str | None = None
    ) -> dict[str, Any]:
        return await self._request(
            "DELETE", f"{_RM}/{_seg(storage_name)}", actor=actor
        )

    # --- data-management (DM) sync jobs (data-backup orchestrator) --------
    # NOTE: these take an explicit `actor`. Backup jobs run as the privileged
    # `root` requester, which DMS gates behind an mTLS-verified operator, so the
    # orchestrator passes actor="mtls:<operator>" (see Settings.backup_actor_prefix).

    async def list_data_management_policies(
        self, *, actor: str | None = None
    ) -> list[dict[str, Any]]:
        # Read-only: per-operation policy (incl. default/max worker nodes). Used to
        # show what "자동" (policy default) resolves to in the backup form.
        return await self._request("GET", f"{_DM}/policies", actor=actor)

    async def submit_sync(self, body: dict[str, Any], *, actor: str) -> dict[str, Any]:
        return await self._request("POST", f"{_DM}/sync", actor=actor, json=body)

    async def get_sync_job(self, job_id: str, *, actor: str) -> dict[str, Any]:
        return await self._request(
            "GET", f"{_DM}/sync/jobs/{_seg(job_id)}", actor=actor
        )

    async def confirm_job(
        self, job_id: str, body: dict[str, Any], *, actor: str
    ) -> dict[str, Any]:
        return await self._request(
            "POST", f"{_DM}/jobs/{_seg(job_id)}:confirm", actor=actor, json=body
        )

    async def cancel_job(self, job_id: str, *, actor: str) -> dict[str, Any]:
        return await self._request(
            "POST", f"{_DM}/jobs/{_seg(job_id)}:cancel", actor=actor
        )

    async def list_data_jobs(
        self,
        *,
        actor: str,
        limit: int = 500,
        state: str | None = None,
        operation: str | None = None,
        storage_name: str | None = None,
    ) -> list[dict[str, Any]]:
        # Newest-first; used to resolve a freshly-submitted request_id -> job_id
        # (DMS ignores a request_id query filter, so we match client-side).
        params: dict[str, Any] = {"limit": limit}
        if state:
            params["state"] = state
        if operation:
            params["operation"] = operation
        if storage_name:
            params["storage_name"] = storage_name
        return await self._request(
            "GET", _DATA_JOBS, actor=actor, params=params
        )

    async def get_control_state(self, *, actor: str) -> dict[str, Any]:
        return await self._request(
            "GET", f"{_OPS_BASE}/control-state", actor=actor
        )

    async def get_work_summary(self, *, actor: str) -> dict[str, Any]:
        return await self._request(
            "GET", f"{_OPS_BASE}/work-summary", actor=actor
        )

    async def list_agent_reports(
        self, *, actor: str, freshness: str | None = None
    ) -> list[dict[str, Any]]:
        params = {"freshness": freshness} if freshness else None
        return await self._request(
            "GET", f"{_OPS_BASE}/agent-reports", actor=actor, params=params
        )

    async def get_data_job_summary(self, *, actor: str) -> dict[str, Any]:
        return await self._request(
            "GET", f"{_OPS_BASE}/data-jobs/summary", actor=actor
        )

    async def list_agent_metric_samples(
        self, *, since_seconds: int = 21600, actor: str | None = None
    ) -> list[dict[str, Any]]:
        # Per-node OS-metric time-series (cpu/mem/load/disk) for the node-workload
        # graphs. Read-only; serves the existing agent_reports history.
        return await self._request(
            "GET",
            f"{_OPS_BASE}/agent-reports/metrics",
            actor=actor,
            params={"since_seconds": since_seconds},
        )

    async def list_active_runs(
        self, *, actor: str, limit: int = 200
    ) -> list[dict[str, Any]]:
        return await self._request(
            "GET", f"{_OPS_BASE}/runs/active", actor=actor, params={"limit": limit}
        )

    async def list_stale_runs(self, *, actor: str) -> list[dict[str, Any]]:
        return await self._request(
            "GET", f"{_OPS_BASE}/runs/stale", actor=actor
        )

    async def list_action_required(self, *, actor: str) -> list[dict[str, Any]]:
        return await self._request(
            "GET", f"{_OPS_BASE}/action-required", actor=actor
        )

    async def get_volcano_status(self, *, actor: str) -> dict[str, Any]:
        return await self._request("GET", f"{_OPS_BASE}/volcano", actor=actor)

    async def volcano_job_metrics(
        self, *, actor: str, limit: int = 300
    ) -> dict[str, Any]:
        # Per-job Volcano lifecycle metrics (timestamps/latencies/resources) for the
        # dashboard throughput/latency/top-offenders views. Read-only.
        return await self._request(
            "GET",
            f"{_OPS_BASE}/volcano/job-metrics",
            actor=actor,
            params={"limit": limit},
        )


def _seg(value: str) -> str:
    # Path segment; keep ':' (DMS action routes use it) but escape slashes etc.
    return quote(value, safe=":")


def _extract_detail(resp: httpx.Response) -> Any:
    try:
        body = resp.json()
    except ValueError:
        return resp.text or resp.reason_phrase
    if isinstance(body, dict) and "detail" in body:
        return body["detail"]
    return body
