from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from ._base import *  # noqa: F401,F403
from ._base import (  # noqa: F401
    _append_basename_issue,
    _append_boolean_payload_issues,
    _append_expiry_issues,
    _default_expires_at,
    _filesystem_quota_issues,
    _filesystem_restore_state,
    _normalize_expires_at_or_none,
    _normalize_future_expires_at,
    _normalized_filesystem_quota,
    _observed_quota_used,
    _unsupported_payload_issues,
    _validate_string_list,
)


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
        if operation in RM_OPERATIONS:
            if self._reject_invalid_kubernetes_quota_request(request):
                return
            if self._reject_invalid_filesystem_request(request):
                return
            if self._reject_unsafe_storage_mapping(request, WorkerRole.RM):
                return
            if self._reject_inconsistent_kubernetes_quota_mapping(request):
                return
            desired_state = self._desired_state(request)
            if self._reject_kubernetes_quota_decrease(request, desired_state):
                return
            self.repository.create_plan(
                request_id=request["request_id"],
                worker_role=WorkerRole.RM,
                operation_kind=operation,
                resource_key=request["resource_key"],
                desired_state=desired_state,
                precondition=self._precondition(request),
                execution_metadata=self._rm_execution_metadata(request, desired_state),
            )
            return

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
            if self._reject_unsafe_storage_mapping(request, WorkerRole.DM):
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
        if request["resource_kind"] == ResourceKind.FILESYSTEM.value:
            desired = self._filesystem_desired_state(request, desired)
        if request["resource_kind"] == ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value:
            if request["operation"] == OperationKind.K8S_QUOTA_AUDIT.value:
                desired.update(
                    {
                        "operation": request["operation"],
                        "resource_kind": request["resource_kind"],
                        "resource_key": request["resource_key"],
                        "targets": self._resolve_kubernetes_quota_audit_targets(
                            request
                        ),
                    }
                )
                return desired
            if request["operation"] == OperationKind.K8S_QUOTA_EXPIRATION_SWEEP.value:
                desired.update(
                    {
                        "operation": request["operation"],
                        "resource_kind": request["resource_kind"],
                        "resource_key": request["resource_key"],
                        "targets": self._resolve_kubernetes_expiration_targets(request),
                    }
                )
                return desired
            existing = self.repository.get_resource(
                request["resource_kind"], request["resource_key"]
            )
            if (
                existing
                and request["operation"] != OperationKind.K8S_QUOTA_CREATE.value
            ):
                desired = self._merge_kubernetes_quota_desired(
                    existing["desired_state"], desired
                )
            self._apply_expiry_desired(
                request=request,
                desired=desired,
                existing_desired=(existing or {}).get("desired_state") or {},
            )
        desired.update(
            {
                "operation": request["operation"],
                "resource_kind": request["resource_kind"],
                "resource_key": request["resource_key"],
                "requester_id": request.get("requester_id"),
            }
        )
        if request["resource_kind"] == ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value:
            desired.setdefault("resource_quota_name", "dms-storage-quota")
            desired.setdefault("resource_type", "user")
            if request["operation"] == OperationKind.K8S_QUOTA_IMPORT.value:
                self._enrich_kubernetes_storage_class_entries(desired)
                desired.setdefault("import_mode", "dms_resourcequota")
                desired.setdefault(
                    "storage_mapping_candidates",
                    self._storage_mapping_candidates(desired),
                )
            if self._is_kubernetes_default_reset(request):
                self._apply_kubernetes_default_quota_reset(request, desired)
                self._apply_expiry_desired(
                    request=request,
                    desired=desired,
                    existing_desired=(existing or {}).get("desired_state") or {},
                )
            if not self._should_preserve_kubernetes_quota_hard(request):
                self._enrich_kubernetes_quota_desired(desired)
            self._apply_kubernetes_block_desired(request, desired)
            self._apply_kubernetes_blocked_update_desired(request, desired)
        return desired


    def _apply_expiry_desired(
        self,
        *,
        request: dict[str, Any],
        desired: dict[str, Any],
        existing_desired: dict[str, Any],
    ) -> None:
        payload = request["payload_summary"]
        operation = request["operation"]
        if (
            operation
            in {
                OperationKind.FILESYSTEM_IMPORT.value,
                OperationKind.K8S_QUOTA_IMPORT.value,
            }
            and "expires_at" not in payload
        ):
            desired["expires_at"] = _default_expires_at()
            return
        if "expires_at" in payload:
            desired["expires_at"] = (
                _normalize_expires_at_or_none(payload.get("expires_at")) or ""
            )
            return
        if operation in {
            OperationKind.FILESYSTEM_UPDATE.value,
            OperationKind.K8S_QUOTA_UPDATE.value,
        } and existing_desired.get("expires_at"):
            desired["expires_at"] = existing_desired["expires_at"]


    def _rm_execution_metadata(
        self, request: dict[str, Any], desired_state: dict[str, Any]
    ) -> dict[str, Any]:
        metadata = {
            "resource_kind": request["resource_kind"],
            "planner": "rm",
            "backend_side_effect_owner": "rm-worker",
            "storage_backend": self._storage_backend(request),
        }
        if request["resource_kind"] == ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value:
            if request["operation"] == OperationKind.K8S_QUOTA_AUDIT.value:
                metadata["planner"] = "k8s-quota-audit"
            elif request["operation"] in {
                OperationKind.K8S_QUOTA_IMPORT.value,
                OperationKind.K8S_QUOTA_EXPIRATION_SWEEP.value,
            }:
                metadata["planner"] = "k8s-quota-lifecycle"
            else:
                metadata["planner"] = (
                    "k8s-multi-storage-quota"
                    if len(desired_state.get("storage_class_quotas") or []) > 1
                    else (
                        "k8s-quota-create"
                        if request["operation"] == OperationKind.K8S_QUOTA_CREATE.value
                        else "k8s-quota"
                    )
                )
            metadata["kubernetes_backend"] = {
                "cluster_name": desired_state.get("cluster_name"),
                "namespace_name": desired_state.get("namespace_name"),
                "resource_quota_name": desired_state.get(
                    "resource_quota_name", "dms-storage-quota"
                ),
                "target_count": len(desired_state.get("targets") or []),
                "storage_classes": [
                    {
                        "storage_name": entry.get("storage_name"),
                        "storage_class_name": entry.get("storage_class_name"),
                    }
                    for entry in desired_state.get("storage_class_quotas") or []
                ],
            }
        elif request["resource_kind"] == ResourceKind.FILESYSTEM.value:
            metadata["planner"] = (
                "filesystem-quota"
                if (
                    request["operation"]
                    in {
                        OperationKind.FILESYSTEM_UPDATE.value,
                        OperationKind.FILESYSTEM_ASSIGN_QUOTA.value,
                        OperationKind.FILESYSTEM_IMPORT.value,
                        OperationKind.FILESYSTEM_CHECK.value,
                        OperationKind.FILESYSTEM_SYNC.value,
                    }
                    or (
                        request["operation"] == OperationKind.FILESYSTEM_CREATE.value
                        and "quota" in desired_state
                    )
                )
                else (
                    "filesystem-lifecycle"
                    if request["operation"]
                    in {
                        OperationKind.FILESYSTEM_BLOCK.value,
                        OperationKind.FILESYSTEM_EXPIRATION_SWEEP.value,
                    }
                    else "filesystem"
                )
            )
            metadata["filesystem_backend"] = {
                "storage_name": desired_state.get("storage_name"),
                "directory_name": desired_state.get("directory_name"),
                "access_group": desired_state.get("access_group"),
                "mode": desired_state.get("mode"),
                "target_count": len(desired_state.get("targets") or []),
            }
        return metadata


    @staticmethod
    def _precondition(request: dict[str, Any]) -> dict[str, Any]:
        return {
            "commit_order": request["commit_order"],
            "resource_key": request["resource_key"],
            "source_of_truth": "operational-postgresql",
        }


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
        registry = self.backend_registry or BackendAdapterRegistry.with_test_stubs(
            self.repository
        )
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
        if request["resource_kind"] == ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value:
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
            return {"backend_type": "kubernetes", "storage_mappings": mappings}
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


    def _dm_readiness_is_stale(
        self, worker_role: WorkerRole, mapping: dict[str, Any]
    ) -> bool:
        ttl = getattr(self, "sanity_ttl_seconds", None)
        if worker_role != WorkerRole.DM or ttl is None:
            return False
        from ..sanity_reconciler import readiness_is_stale

        return readiness_is_stale(mapping, ttl_seconds=ttl)


    def _reject_unsafe_storage_mapping(
        self, request: dict[str, Any], worker_role: WorkerRole
    ) -> bool:
        storage_names = self._required_storage_names(request)
        if not storage_names:
            return False
        # Kubernetes namespace quota is applied directly to the target cluster's
        # API server (kubectl + kubeconfig) and does NOT run on an RM/DM agent.
        # Its storage mapping therefore only needs the live StorageClass / cluster /
        # csi_driver checks to pass (those surface as sanity "Failed"); fresh
        # RM-agent mount/CSI evidence is irrelevant. Filesystem RM still requires
        # RM-agent readiness because it SSHes into the storage node to mutate the
        # filesystem. "Unknown" (no fresh Agent reports at all) is likewise harmless
        # for the agentless quota path, so only "Failed" blocks it.
        is_kubernetes_quota = (
            request["resource_kind"]
            == ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value
        )
        unsafe_sanity = {"Failed"} if is_kubernetes_quota else {"Failed", "Unknown"}
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
            if is_kubernetes_quota:
                # Agentless: namespace quota needs no RM/DM mount or CSI-node
                # readiness — the API-server apply only depends on the StorageClass
                # existing (already verified above via sanity).
                continue
            readiness_key = (
                "resource_management"
                if worker_role == WorkerRole.RM
                else "data_management"
            )
            readiness = (mapping.get("readiness") or {}).get(readiness_key)
            if readiness != "Ready":
                issues.append(
                    {
                        "storage_name": storage_name,
                        "reason": f"missing_{worker_role.value.lower()}_readiness",
                        "sanity_status": mapping["sanity_status"],
                        "readiness": mapping.get("readiness", {}),
                    }
                )
            elif self._dm_readiness_is_stale(worker_role, mapping):
                # DM-only fail-safe: readiness reads "Ready" but its sanity is older than
                # the configured TTL (the reconciler stopped refreshing it), so we cannot
                # trust it. RM is unaffected (the gate only applies to DM).
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


    def _storage_mapping_candidates(
        self, desired: dict[str, Any]
    ) -> list[dict[str, Any]]:
        cluster_name = desired.get("cluster_name")
        candidates: list[dict[str, Any]] = []
        for mapping in self.repository.list_storage_mappings():
            if cluster_name and mapping.get("cluster_name") != cluster_name:
                continue
            candidates.append(
                {
                    "storage_name": mapping["storage_name"],
                    "cluster_name": mapping.get("cluster_name"),
                    "storage_class_name": mapping.get("storage_class_name"),
                    "sanity_status": mapping.get("sanity_status"),
                    "readiness": mapping.get("readiness") or {},
                }
            )
        return candidates


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
        operation = request["operation"]
        if operation == OperationKind.FILESYSTEM_EXPIRATION_SWEEP.value:
            return set()
        if operation in DM_OPERATIONS:
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
        if request["resource_kind"] == ResourceKind.FILESYSTEM.value:
            return {payload["storage_name"]} if payload.get("storage_name") else set()
        if request["resource_kind"] == ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value:
            if operation == OperationKind.K8S_QUOTA_AUDIT.value:
                scope = payload.get("scope") or {}
                return {scope["storage_name"]} if scope.get("storage_name") else set()
            if self._is_kubernetes_default_reset(request):
                policy, _ = self._kubernetes_default_policy_for_request(request)
                if policy:
                    return {
                        entry["storage_name"]
                        for entry in policy["quota"].get("storage_class_quotas") or []
                        if isinstance(entry, dict) and entry.get("storage_name")
                    }
            storage_names = {
                entry["storage_name"]
                for entry in payload.get("storage_class_quotas") or []
                if isinstance(entry, dict) and entry.get("storage_name")
            }
            if payload.get("storage_name"):
                storage_names.add(payload["storage_name"])
            if not storage_names:
                resource = self.repository.get_resource(
                    request["resource_kind"], request["resource_key"]
                )
                desired = resource["desired_state"] if resource else {}
                storage_names = {
                    entry["storage_name"]
                    for entry in desired.get("storage_class_quotas") or []
                    if isinstance(entry, dict) and entry.get("storage_name")
                }
            return storage_names
        return set()
