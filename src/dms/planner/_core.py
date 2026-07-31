from __future__ import annotations

from typing import Any

from ._base import *  # noqa: F401,F403


class _PlannerCoreMixin:
    """Dispatch and shared planning helpers."""

    def run_once(self, limit: int = 50) -> int:
        planned = 0
        for request in self.repository.list_plannable_requests(limit=limit):
            self._plan_request(request)
            planned += 1
        return planned


    def _plan_request(self, request: dict[str, Any]) -> None:
        prior = self.repository.find_prior_active_request(request)
        if prior:
            self.repository.complete_result(
                request_id=request["request_id"],
                plan_id=None,
                run_id=None,
                terminal_status=LifecycleState.CONFLICT,
                message=(
                    "prior request for the same resource has not reached a "
                    f"terminal state: {prior['request_id']}"
                ),
                verification_summary={
                    "resource_key": request["resource_key"],
                    "prior_request_id": prior["request_id"],
                    "backend_side_effect": False,
                },
                error_category="ordering",
                actor="planner",
            )
            return

        self.repository.update_request_status(
            request["request_id"],
            LifecycleState.PLANNING,
            reason="planner started",
            actor="planner",
        )

        operation = request["operation"]
        if operation in DM_OPERATIONS:
            payload = request["payload_summary"]
            source = payload.get("source") or {}
            destination = payload.get("destination") or {}
            target = payload.get("target") or {}
            if operation == OperationKind.DATA_SYNC.value:
                storage_name = source["storage_name"]
                source_path = source["path"]
                destination_path = destination["path"]
                target_path = None
                normalized_target = {
                    "source": source,
                    "destination": destination,
                    "options": payload.get("options") or {},
                    "option_fingerprint": payload.get("option_fingerprint"),
                }
            else:
                storage_name = target.get("storage_name") or payload["storage_name"]
                target_path = target.get("path") or payload.get("target_path")
                source_path = payload.get("source_path")
                destination_path = payload.get("destination_path")
                normalized_target = {"storage_name": storage_name, "path": target_path}
            if self._reject_unsafe_storage_mapping(request):
                return
            if self.settings is not None and self.settings.dm_path_base == "managed_root":
                normalized_target = self._rebase_paths_for_managed_root(
                    request, operation, normalized_target
                )
                if normalized_target is None:
                    return
                if operation == OperationKind.DATA_SYNC.value:
                    source = normalized_target["source"]
                    destination = normalized_target["destination"]
                    source_path = source["path"]
                    destination_path = destination["path"]
                else:
                    target_path = normalized_target["path"]
            job_id = self.repository.create_data_job(
                request_id=request["request_id"],
                operation=operation,
                storage_name=storage_name,
                source=source_path,
                destination=destination_path,
                target=target_path,
                priority=int(payload.get("priority", 100)),
                worker_pool=self._data_worker_pool(request),
                normalized_target=normalized_target,
            )
            preview_required = operation in {
                OperationKind.DATA_SYNC.value,
                OperationKind.DATA_RM.value,
            }
            self.repository.create_plan(
                request_id=request["request_id"],
                worker_role=WorkerRole.DM,
                operation_kind=operation,
                resource_key=request["resource_key"],
                desired_state=self._desired_state(request),
                precondition={
                    "job_id": job_id,
                    "safe_paths_only": True,
                    "preview_required": preview_required,
                    "normalized_target": normalized_target,
                    "requester_id": request["requester_id"],
                },
                execution_metadata={
                    "resource_kind": ResourceKind.DATA_JOB.value,
                    "job_id": job_id,
                    "phase": "preview" if preview_required else "execution",
                    "backend_side_effect_owner": "dm-worker",
                    "storage_backend": self._storage_backend(request),
                },
            )
            return

        self.repository.complete_result(
            request_id=request["request_id"],
            plan_id=None,
            run_id=None,
            terminal_status=LifecycleState.REJECTED,
            message=f"operation is not supported by planner: {operation}",
            verification_summary={"backend_side_effect": False},
            error_category="planner",
            actor="planner",
        )


    def _desired_state(self, request: dict[str, Any]) -> dict[str, Any]:
        desired = dict(request["payload_summary"])
        desired.update(
            {
                "operation": request["operation"],
                "resource_kind": request["resource_kind"],
                "resource_key": request["resource_key"],
                "requester_id": request.get("requester_id"),
            }
        )
        return desired



    def _worker_pool(self, storage_name: str) -> dict[str, Any]:
        mapping = self.repository.get_storage_mapping(storage_name)
        if self.backend_registry is not None:
            return self.backend_registry.data_worker_pool(storage_name)
        if mapping and mapping.get("sanity_result"):
            agent_observed = mapping["sanity_result"].get("agent_observed", {})
            return {
                "selection": "agent-inventory",
                "required_mounts": [storage_name],
                "readiness": mapping.get("readiness", {}),
                "candidates": agent_observed.get("dm_candidates", []),
                "sanity_status": mapping.get("sanity_status"),
            }
        registry = self.backend_registry or BackendAdapterRegistry(self.repository)
        return registry.data_worker_pool(storage_name)


    def _data_worker_pool(self, request: dict[str, Any]) -> dict[str, Any]:
        payload = request["payload_summary"]
        operation = request["operation"]
        storage_names = sorted(self._required_storage_names(request))
        if operation != OperationKind.DATA_SYNC.value and len(storage_names) == 1:
            pool = dict(self._worker_pool(storage_names[0]))
            pool.setdefault("operation", operation)
            return pool
        pools = {
            storage_name: self._worker_pool(storage_name)
            for storage_name in storage_names
        }
        candidates: list[dict[str, Any]] = []
        seen: set[tuple[str | None, str | None, str | None]] = set()
        for storage_name, pool in pools.items():
            for candidate in pool.get("candidates") or []:
                key = (
                    candidate.get("cluster_name"),
                    candidate.get("node_name"),
                    storage_name,
                )
                if key in seen:
                    continue
                seen.add(key)
                candidates.append({**candidate, "storage_name": storage_name})
        if operation == OperationKind.DATA_SYNC.value:
            return {
                "selection": "agent-inventory",
                "operation": operation,
                "required_mounts": storage_names,
                "source": payload.get("source"),
                "destination": payload.get("destination"),
                "storage_pools": pools,
                "candidates": candidates,
            }
        return {
            "selection": "agent-inventory",
            "operation": operation,
            "required_mounts": storage_names,
            "storage_pools": pools,
            "candidates": candidates,
        }


    def _storage_backend(self, request: dict[str, Any]) -> dict[str, Any]:
        if request["resource_kind"] == ResourceKind.DATA_JOB.value:
            mappings = []
            for storage_name in sorted(self._required_storage_names(request)):
                mapping = self.repository.get_storage_mapping(storage_name)
                if mapping:
                    mappings.append(
                        {
                            "backend_type": mapping["backend_template"].get(
                                "backend_type", "unknown"
                            ),
                            "storage_name": mapping["storage_name"],
                            "cluster_name": mapping.get("cluster_name"),
                            "storage_class_name": mapping.get("storage_class_name"),
                            "sanity_status": mapping["sanity_status"],
                            "version": mapping["version"],
                            "readiness": mapping.get("readiness", {}),
                        }
                    )
            return {"backend_type": "data-management", "storage_mappings": mappings}
        storage_name = request["payload_summary"].get("storage_name")
        if not storage_name:
            return {"backend_type": "unmapped"}
        mapping = self.repository.get_storage_mapping(storage_name)
        if not mapping:
            return {"backend_type": "unmapped", "storage_name": storage_name}
        return {
            "backend_type": mapping["backend_template"].get("backend_type", "unknown"),
            "storage_name": mapping["storage_name"],
            "sanity_status": mapping["sanity_status"],
            "version": mapping["version"],
            "readiness": mapping.get("readiness", {}),
        }


    def _dm_readiness_is_stale(self, mapping: dict[str, Any]) -> bool:
        ttl = getattr(self, "sanity_ttl_seconds", None)
        if ttl is None:
            return False
        from ..sanity_reconciler import readiness_is_stale

        return readiness_is_stale(mapping, ttl_seconds=ttl)


    def _reject_unsafe_storage_mapping(self, request: dict[str, Any]) -> bool:
        """Fail-closed admission gate: a data job may only be planned against a storage
        mapping that exists, is enabled, whose last sanity check did not fail (and is not
        "Unknown" — no evidence at all), and whose ``data_management`` readiness is
        currently Ready and not staler than ``sanity_ttl_seconds``."""
        storage_names = self._required_storage_names(request)
        if not storage_names:
            return False
        unsafe_sanity = {"Failed", "Unknown"}
        issues: list[dict[str, Any]] = []
        for storage_name in sorted(storage_names):
            mapping = self.repository.get_storage_mapping(storage_name)
            if not mapping:
                issues.append(
                    {
                        "storage_name": storage_name,
                        "reason": "storage_mapping_missing",
                    }
                )
                continue
            if mapping.get("disabled_at"):
                issues.append(
                    {
                        "storage_name": storage_name,
                        "reason": "storage_mapping_disabled",
                        "sanity_status": mapping["sanity_status"],
                    }
                )
                continue
            if mapping["sanity_status"] in unsafe_sanity:
                issues.append(
                    {
                        "storage_name": storage_name,
                        "reason": "storage_mapping_sanity",
                        "sanity_status": mapping["sanity_status"],
                        "sanity_result": mapping.get("sanity_result", {}),
                    }
                )
                continue
            readiness = (mapping.get("readiness") or {}).get("data_management")
            if readiness != "Ready":
                issues.append(
                    {
                        "storage_name": storage_name,
                        "reason": "missing_dm_readiness",
                        "sanity_status": mapping["sanity_status"],
                        "readiness": mapping.get("readiness", {}),
                    }
                )
            elif self._dm_readiness_is_stale(mapping):
                # Fail-safe: readiness reads "Ready" but its sanity is older than the
                # configured TTL (the reconciler stopped refreshing it), so we cannot
                # trust it.
                issues.append(
                    {
                        "storage_name": storage_name,
                        "reason": "dm_readiness_stale",
                        "sanity_status": mapping["sanity_status"],
                        "sanity_checked_at": mapping.get("sanity_checked_at"),
                        "readiness": mapping.get("readiness", {}),
                    }
                )
        if not issues:
            return False
        self.repository.complete_result(
            request_id=request["request_id"],
            plan_id=None,
            run_id=None,
            terminal_status=LifecycleState.REJECTED,
            message="storage mapping sanity/readiness guard rejected request",
            verification_summary={
                "backend_side_effect": False,
                "storage_names": sorted(storage_names),
                "issues": issues,
            },
            error_category="storage_mapping_sanity",
            actor="planner",
        )
        return True


    def _reject_planner_issue(
        self,
        request: dict[str, Any],
        *,
        message: str,
        issues: list[dict[str, Any]],
        error_category: str,
    ) -> bool:
        self.repository.complete_result(
            request_id=request["request_id"],
            plan_id=None,
            run_id=None,
            terminal_status=LifecycleState.REJECTED,
            message=message,
            verification_summary={"backend_side_effect": False, "issues": issues},
            error_category=error_category,
            actor="planner",
        )
        return True

    def _rebase_paths_for_managed_root(
        self, request: dict[str, Any], operation: str, normalized_target: dict[str, Any]
    ) -> dict[str, Any] | None:
        """``DMS_DM_PATH_BASE=managed_root``: prepend each storage's managed_root suffix
        (``relpath(managed_root, mount_path)``) to its request path, so the job operates
        under managed_root while volcano/preflight stay mount_path-relative. Returns the
        rebased ``normalized_target``, or ``None`` if the job was rejected (managed_root
        unavailable, or escapes mount_path). storage-keyed suffix cache handles nsync's
        distinct source/destination storages."""
        suffix_cache: dict[str, str] = {}

        def _suffix_for(name: str) -> str | None:
            if name not in suffix_cache:
                mapping = self.repository.get_storage_mapping(name)
                pair = managed_root_for_mapping(mapping) if mapping else None
                if pair is None:
                    self._reject_planner_issue(
                        request,
                        message=f"managed_root unavailable for storage {name}",
                        issues=[
                            {"storage_name": name, "reason": "managed_root_unavailable"}
                        ],
                        error_category="planner",
                    )
                    return None
                try:
                    suffix_cache[name] = managed_root_path_suffix(*pair)
                except ValueError:
                    self._reject_planner_issue(
                        request,
                        message=f"managed_root escapes mount_path for storage {name}",
                        issues=[
                            {
                                "storage_name": name,
                                "reason": "managed_root_outside_mount_path",
                            }
                        ],
                        error_category="planner",
                    )
                    return None
            return suffix_cache[name]

        if operation == OperationKind.DATA_SYNC.value:
            source = normalized_target["source"]
            destination = normalized_target["destination"]
            s_suffix = _suffix_for(source["storage_name"])
            if s_suffix is None:
                return None
            d_suffix = _suffix_for(destination["storage_name"])
            if d_suffix is None:
                return None
            return {
                **normalized_target,
                "source": {
                    **source,
                    "path": apply_managed_root_suffix(source["path"], s_suffix),
                },
                "destination": {
                    **destination,
                    "path": apply_managed_root_suffix(destination["path"], d_suffix),
                },
            }
        suffix = _suffix_for(normalized_target["storage_name"])
        if suffix is None:
            return None
        return {
            **normalized_target,
            "path": apply_managed_root_suffix(normalized_target["path"], suffix),
        }


    def _required_storage_names(self, request: dict[str, Any]) -> set[str]:
        payload = request["payload_summary"]
        if request["operation"] not in DM_OPERATIONS:
            return set()
        names: set[str] = set()
        for key in ("target", "source", "destination"):
            value = payload.get(key)
            if isinstance(value, dict) and value.get("storage_name"):
                names.add(value["storage_name"])
        for key in (
            "storage_name",
            "source_storage_name",
            "destination_storage_name",
        ):
            if payload.get(key):
                names.add(payload[key])
        return names
