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


class KubernetesPlannerMixin:
    """Kubernetes namespace-quota validation and desired-state planning."""


    def _reject_invalid_kubernetes_quota_request(self, request: dict[str, Any]) -> bool:
        if request["resource_kind"] != ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value:
            return False
        operation = request["operation"]
        payload = request["payload_summary"]
        issues: list[dict[str, Any]] = []
        if operation == OperationKind.K8S_QUOTA_AUDIT.value:
            return self._reject_invalid_kubernetes_quota_audit_request(request)
        if operation == OperationKind.K8S_QUOTA_EXPIRATION_SWEEP.value:
            return self._reject_invalid_kubernetes_expiration_sweep_request(request)
        if not payload.get("cluster_name"):
            issues.append({"reason": "cluster_name_missing"})
        if not payload.get("namespace_name"):
            issues.append({"reason": "namespace_name_missing"})
        existing = self.repository.get_resource(
            request["resource_kind"], request["resource_key"]
        )
        if operation in {
            OperationKind.K8S_QUOTA_IMPORT.value,
            OperationKind.K8S_QUOTA_CREATE.value,
        }:
            if existing and existing.get("status") != "Deleted":
                issues.append(
                    {
                        "reason": "kubernetes_namespace_quota_resource_already_exists",
                        "resource_key": request["resource_key"],
                        "status": existing.get("status"),
                    }
                )
        elif not existing:
            issues.append(
                {"reason": "resource_missing", "resource_key": request["resource_key"]}
            )
        _append_expiry_issues(
            issues,
            payload,
            operation=operation,
            existing_desired=(existing or {}).get("desired_state") or {},
            resource_kind=ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value,
        )
        raw_storage_class_quotas = payload.get("storage_class_quotas")
        storage_class_quotas = raw_storage_class_quotas or []
        if raw_storage_class_quotas is not None and not isinstance(
            raw_storage_class_quotas, list
        ):
            issues.append({"reason": "storage_class_quotas_must_be_list"})
            storage_class_quotas = []
        seen_storage_names: set[str] = set()
        for entry in storage_class_quotas:
            if not isinstance(entry, dict) or not entry.get("storage_name"):
                issues.append({"reason": "storage_class_quota_storage_name_missing"})
                continue
            storage_name = entry["storage_name"]
            if storage_name in seen_storage_names:
                issues.append(
                    {
                        "reason": "duplicate_storage_name",
                        "storage_name": storage_name,
                    }
                )
            seen_storage_names.add(storage_name)
            if (
                operation
                in {
                    OperationKind.K8S_QUOTA_CREATE.value,
                    OperationKind.K8S_QUOTA_UPDATE.value,
                }
                and len(storage_class_quotas) > 1
            ):
                if (
                    entry.get("requests_storage_bytes") is None
                    and entry.get("capacity_bytes") is None
                ):
                    issues.append(
                        {
                            "reason": "storage_class_quota_requests_storage_bytes_required",
                            "storage_name": storage_name,
                        }
                    )
            for key in ("requests_storage_bytes", "capacity_bytes", "pvc_count"):
                if entry.get(key) is None:
                    continue
                try:
                    if int(entry[key]) <= 0:
                        issues.append(
                            {
                                "reason": f"storage_class_quota_{key}_invalid",
                                "storage_name": storage_name,
                                "value": entry[key],
                            }
                        )
                except (TypeError, ValueError):
                    issues.append(
                        {
                            "reason": f"storage_class_quota_{key}_invalid",
                            "storage_name": storage_name,
                            "value": entry[key],
                        }
                    )
        quota = payload.get("quota") or {}
        if operation in {
            OperationKind.K8S_QUOTA_CREATE.value,
            OperationKind.K8S_QUOTA_UPDATE.value,
        }:
            reset_to_default = self._is_kubernetes_default_reset(request)
            # quota is no longer mandatory on create/update — storage_class_quotas[]
            # may supply the hard keys instead (§1 model: at least one hard key from
            # quota OR storage_class_quotas). Validate quota fields only when present.
            for key in ("requests_storage_bytes", "pvc_count"):
                value = quota.get(key)
                if value is None or reset_to_default:
                    continue
                try:
                    if int(value) <= 0:
                        issues.append(
                            {"reason": f"quota_{key}_invalid", "value": value}
                        )
                except (TypeError, ValueError):
                    issues.append({"reason": f"quota_{key}_invalid", "value": value})
            if (
                operation == OperationKind.K8S_QUOTA_CREATE.value
                and not reset_to_default
                and quota.get("requests_storage_bytes") is None
                and quota.get("pvc_count") is None
                and not any(
                    isinstance(entry, dict)
                    and (
                        entry.get("requests_storage_bytes") is not None
                        or entry.get("capacity_bytes") is not None
                        or entry.get("pvc_count") is not None
                    )
                    for entry in storage_class_quotas
                )
            ):
                issues.append({"reason": "kubernetes_quota_hard_key_required"})
            if reset_to_default:
                policy, policy_issues = self._kubernetes_default_policy_for_request(
                    request
                )
                issues.extend(policy_issues)
                if policy:
                    for issue in self._validate_default_policy_storage_entries(
                        request, policy["quota"]
                    ):
                        issues.append(issue)
        if operation == OperationKind.K8S_QUOTA_BLOCK.value:
            block = payload.get("block")
            if not isinstance(block, bool):
                issues.append({"reason": "block_boolean_required", "value": block})
            if block is True and existing:
                resource_type = existing["desired_state"].get("resource_type")
                if resource_type in {"system", "admin"}:
                    issues.append(
                        {
                            "reason": "resource_type_cannot_be_blocked",
                            "resource_type": resource_type,
                        }
                    )
            if block is False and existing:
                restore_hard = (
                    existing["desired_state"].get("block_state", {}).get("restore_hard")
                )
                if not restore_hard:
                    issues.append({"reason": "block_restore_state_missing"})
        if operation == OperationKind.K8S_QUOTA_IMPORT.value:
            resource_quota_name = payload.get(
                "resource_quota_name", "dms-storage-quota"
            )
            if resource_quota_name != "dms-storage-quota":
                issues.append(
                    {
                        "reason": "kubernetes_resource_quota_name_unsupported",
                        "resource_quota_name": resource_quota_name,
                    }
                )
            resource_type = payload.get("resource_type", "user")
            if resource_type not in {"user", "project", "system", "admin"}:
                issues.append(
                    {
                        "reason": "kubernetes_resource_type_unsupported",
                        "resource_type": resource_type,
                    }
                )
        if not issues:
            return False
        return self._reject_planner_issue(
            request,
            message="invalid Kubernetes namespace quota request",
            issues=issues,
            error_category="validation",
        )


    def _reject_invalid_kubernetes_expiration_sweep_request(
        self, request: dict[str, Any]
    ) -> bool:
        payload = request["payload_summary"]
        issues: list[dict[str, Any]] = []
        action = payload.get("action", "block")
        if action != "block":
            issues.append(
                {"reason": "kubernetes_sweep_action_unsupported", "action": action}
            )
        dry_run = payload.get("dry_run", False)
        if not isinstance(dry_run, bool):
            issues.append({"reason": "dry_run_boolean_required", "value": dry_run})
        max_targets = payload.get("max_targets", 100)
        try:
            if int(max_targets) <= 0 or int(max_targets) > 1000:
                issues.append({"reason": "max_targets_invalid", "value": max_targets})
        except (TypeError, ValueError):
            issues.append({"reason": "max_targets_invalid", "value": max_targets})
        expired_before = payload.get("expired_before")
        if expired_before is not None:
            try:
                datetime.fromisoformat(str(expired_before).replace("Z", "+00:00"))
            except ValueError:
                issues.append(
                    {
                        "reason": "kubernetes_expired_before_invalid",
                        "expired_before": expired_before,
                    }
                )
        scope = payload.get("scope") or {}
        if scope and not isinstance(scope, dict):
            issues.append({"reason": "kubernetes_sweep_scope_invalid"})
            scope = {}
        if scope.get("namespace_name") and not scope.get("cluster_name"):
            issues.append({"reason": "cluster_name_required_with_namespace_name"})
        resource_type = scope.get("resource_type")
        if resource_type is not None and str(resource_type) not in {
            "user",
            "project",
            "system",
            "admin",
        }:
            issues.append(
                {
                    "reason": "kubernetes_resource_type_unsupported",
                    "resource_type": resource_type,
                }
            )
        if not issues:
            targets = self._resolve_kubernetes_expiration_targets(request)
            if len(targets) > int(max_targets):
                issues.append(
                    {
                        "reason": "kubernetes_sweep_targets_exceed_max",
                        "target_count": len(targets),
                        "max_targets": int(max_targets),
                    }
                )
        if not issues:
            return False
        return self._reject_planner_issue(
            request,
            message="invalid Kubernetes namespace quota expiration sweep request",
            issues=issues,
            error_category="validation",
        )


    def _resolve_kubernetes_expiration_targets(
        self, request: dict[str, Any]
    ) -> list[dict[str, Any]]:
        payload = request["payload_summary"]
        scope = payload.get("scope") or {}
        max_targets = int(payload.get("max_targets", 100))
        resources = self.repository.list_kubernetes_namespace_quota_resources_expiring(
            cluster_name=scope.get("cluster_name"),
            namespace_name=scope.get("namespace_name"),
            resource_type=scope.get("resource_type"),
            status="expired",
            before=payload.get("expired_before"),
            include_blocked=True,
            limit=max_targets + 1,
        )
        targets: list[dict[str, Any]] = []
        for resource in resources[:max_targets]:
            desired = resource["desired_state"]
            targets.append(
                {
                    "cluster_name": desired.get("cluster_name"),
                    "namespace_name": desired.get("namespace_name"),
                    "resource_key": resource["resource_key"],
                    "resource_id": resource["resource_id"],
                    "resource_status": resource["status"],
                    "resource_type": desired.get("resource_type") or "user",
                    "expires_at": resource.get("expires_at"),
                    "block_state": resource.get("block_state") or {},
                    "desired_hard": desired.get("resource_quota_hard") or {},
                    "desired_state": desired,
                }
            )
        return targets


    def _reject_inconsistent_kubernetes_quota_mapping(
        self, request: dict[str, Any]
    ) -> bool:
        if request["resource_kind"] != ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value:
            return False
        payload = request["payload_summary"]
        if request["operation"] == OperationKind.K8S_QUOTA_AUDIT.value:
            return False
        cluster_name = payload.get("cluster_name")
        issues: list[dict[str, Any]] = []
        seen_storage_classes: set[str] = set()
        entries = payload.get("storage_class_quotas") or []
        if self._is_kubernetes_default_reset(request):
            policy, _ = self._kubernetes_default_policy_for_request(request)
            entries = (policy or {}).get("quota", {}).get("storage_class_quotas") or []
        for entry in entries:
            storage_name = (
                entry.get("storage_name") if isinstance(entry, dict) else None
            )
            if not storage_name:
                continue
            mapping = self.repository.get_storage_mapping(storage_name)
            if not mapping:
                continue
            if mapping.get("cluster_name") != cluster_name:
                issues.append(
                    {
                        "reason": "storage_mapping_cluster_mismatch",
                        "storage_name": storage_name,
                        "request_cluster_name": cluster_name,
                        "mapping_cluster_name": mapping.get("cluster_name"),
                    }
                )
            requested_storage_class = entry.get("storage_class_name")
            if requested_storage_class and requested_storage_class != mapping.get(
                "storage_class_name"
            ):
                issues.append(
                    {
                        "reason": "storage_class_name_mismatch",
                        "storage_name": storage_name,
                        "request_storage_class_name": requested_storage_class,
                        "mapping_storage_class_name": mapping.get("storage_class_name"),
                    }
                )
            derived_storage_class = mapping.get("storage_class_name")
            if derived_storage_class in seen_storage_classes:
                issues.append(
                    {
                        "reason": "duplicate_storage_class_name",
                        "storage_name": storage_name,
                        "storage_class_name": derived_storage_class,
                    }
                )
            if derived_storage_class:
                seen_storage_classes.add(derived_storage_class)
        if not issues:
            return False
        return self._reject_planner_issue(
            request,
            message="Kubernetes namespace quota storage mapping mismatch",
            issues=issues,
            error_category="storage_mapping_sanity",
        )


    def _enrich_kubernetes_quota_desired(self, desired: dict[str, Any]) -> None:
        if (
            desired.get("resource_kind")
            != ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value
        ):
            return
        self._enrich_kubernetes_storage_class_entries(desired)
        desired["resource_quota_name"] = desired.get(
            "resource_quota_name", "dms-storage-quota"
        )
        desired["resource_quota_hard"] = render_kubernetes_resource_quota_hard(desired)


    def _enrich_kubernetes_storage_class_entries(self, desired: dict[str, Any]) -> None:
        storage_class_quotas: list[dict[str, Any]] = []
        for entry in desired.get("storage_class_quotas") or []:
            enriched = dict(entry)
            storage_name = enriched.get("storage_name")
            mapping = (
                self.repository.get_storage_mapping(storage_name)
                if storage_name
                else None
            )
            if mapping:
                enriched["storage_class_name"] = mapping.get("storage_class_name")
                enriched["cluster_name"] = mapping.get("cluster_name")
            storage_class_quotas.append(enriched)
        desired["storage_class_quotas"] = storage_class_quotas


    def _resolve_cluster_mutation_config(
        self, cluster_name: str | None
    ) -> dict[str, str]:
        """The per-mapping ResourceQuota mutation transport pinned for ``cluster_name``.

        A k8s/CSI storage mapping may pin ``mutation_mode`` (``kubectl`` /
        ``ssh-kubectl``) and/or ``control_host`` in its ``backend_template`` (see
        domain.validate_kubernetes_mutation_template). DMS applies quota mutations to a
        single cluster per operation, so the **first** mapping of that cluster (ordered
        by ``storage_name`` for determinism) carrying either field wins; the adapter
        falls back to the global ``DMS_KUBERNETES_MUTATION_MODE`` /
        ``DMS_CLUSTER_CONTROL_HOSTS_JSON`` when nothing is pinned. ``control_host`` alone
        (no ``mutation_mode``) is honoured -- it overrides the global control host while
        keeping the global mode.
        """
        if not cluster_name:
            return {}
        mappings = sorted(
            self.repository.list_storage_mappings(cluster_name=cluster_name),
            key=lambda m: m.get("storage_name") or "",
        )
        for mapping in mappings:
            template = mapping.get("backend_template") or {}
            mode = template.get("mutation_mode")
            control_host = template.get("control_host")
            if mode or control_host:
                config: dict[str, str] = {}
                if mode:
                    config["mutation_mode"] = mode
                if control_host:
                    config["control_host"] = control_host
                return config
        return {}

    def _apply_kubernetes_mutation_config(self, desired: dict[str, Any]) -> None:
        """Embed the target cluster's per-mapping mutation transport in ``desired_state``.

        Re-resolved on every plan (stale keys cleared first) so removing the override on
        the mapping reverts to the global default.
        """
        desired.pop("mutation_mode", None)
        desired.pop("control_host", None)
        desired.update(self._resolve_cluster_mutation_config(desired.get("cluster_name")))

    def _apply_kubernetes_mutation_config_to_targets(
        self, targets: list[dict[str, Any]] | None
    ) -> None:
        """Per-target variant for multi-cluster AUDIT / EXPIRATION_SWEEP plans.

        Each target may address a different cluster, so the transport is resolved per
        target. The config is written to both the target dict (read by the audit adapter
        via ``_bind(target)``) and its nested ``desired_state`` (which the sweep turns
        into the block's desired, applied via the rebinding ``apply_resource_quota``).
        """
        for target in targets or []:
            if not isinstance(target, dict):
                continue
            inner = target.get("desired_state")
            cluster_name = target.get("cluster_name") or (
                inner.get("cluster_name") if isinstance(inner, dict) else None
            )
            config = self._resolve_cluster_mutation_config(cluster_name)
            for sink in (target, inner):
                if isinstance(sink, dict):
                    sink.pop("mutation_mode", None)
                    sink.pop("control_host", None)
                    sink.update(config)


    def _apply_kubernetes_default_quota_reset(
        self, request: dict[str, Any], desired: dict[str, Any]
    ) -> None:
        policy, issues = self._kubernetes_default_policy_for_request(request)
        if issues or not policy:
            return
        quota = dict(policy["quota"])
        storage_class_quotas = quota.pop("storage_class_quotas", None)
        desired["quota"] = quota
        desired["storage_class_quotas"] = list(storage_class_quotas or [])
        desired["resource_type"] = policy["resource_type"]
        desired["reset_quota_to_default"] = True
        desired["default_quota_policy_id"] = policy["policy_id"]


    @staticmethod
    def _merge_kubernetes_quota_desired(
        existing_desired: dict[str, Any], request_payload: dict[str, Any]
    ) -> dict[str, Any]:
        desired = dict(existing_desired)
        desired.update(request_payload)
        if isinstance(existing_desired.get("quota"), dict):
            quota = dict(existing_desired["quota"])
            if isinstance(request_payload.get("quota"), dict):
                quota.update(request_payload["quota"])
            desired["quota"] = quota
        return desired


    @staticmethod
    def _should_preserve_kubernetes_quota_hard(request: dict[str, Any]) -> bool:
        if request["resource_kind"] != ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value:
            return False
        if request["operation"] == OperationKind.K8S_QUOTA_IMPORT.value:
            return True
        if request["operation"] not in {
            OperationKind.K8S_QUOTA_CHECK.value,
            OperationKind.K8S_QUOTA_DELETE.value,
            OperationKind.K8S_QUOTA_SYNC.value,
            OperationKind.K8S_QUOTA_AUDIT.value,
        }:
            return False
        payload = request["payload_summary"]
        return not payload.get("quota") and not payload.get("storage_class_quotas")


    def _apply_kubernetes_block_desired(
        self, request: dict[str, Any], desired: dict[str, Any]
    ) -> None:
        if request["operation"] != OperationKind.K8S_QUOTA_BLOCK.value:
            return
        block = bool(request["payload_summary"].get("block"))
        if block:
            restore_hard = dict(desired["resource_quota_hard"])
            desired["resource_quota_hard"] = zero_kubernetes_resource_quota_hard(
                restore_hard
            )
            desired["block"] = True
            desired["block_state"] = {
                "blocked": True,
                "block_mode": request["payload_summary"].get(
                    "block_mode", "quota-zero"
                ),
                "restore_hard": restore_hard,
                "reason": request["payload_summary"].get("reason"),
            }
            return
        restore_hard = desired.get("block_state", {}).get("restore_hard")
        if restore_hard:
            desired["resource_quota_hard"] = dict(restore_hard)
        desired["block"] = False
        desired["block_state"] = {
            "blocked": False,
            "restored_hard": desired.get("resource_quota_hard", {}),
            "reason": request["payload_summary"].get("reason"),
        }


    def _apply_kubernetes_blocked_update_desired(
        self, request: dict[str, Any], desired: dict[str, Any]
    ) -> None:
        if request["operation"] != OperationKind.K8S_QUOTA_UPDATE.value:
            return
        block_state = desired.get("block_state") or {}
        if not block_state.get("blocked"):
            return
        restore_hard = dict(desired.get("resource_quota_hard") or {})
        if not restore_hard:
            return
        updated_block_state = dict(block_state)
        updated_block_state["restore_hard"] = restore_hard
        updated_block_state["updated_while_blocked"] = True
        desired["resource_quota_hard"] = zero_kubernetes_resource_quota_hard(
            restore_hard
        )
        desired["block"] = True
        desired["block_state"] = updated_block_state


    def _reject_kubernetes_quota_decrease(
        self, request: dict[str, Any], desired_state: dict[str, Any]
    ) -> bool:
        if request["operation"] != OperationKind.K8S_QUOTA_UPDATE.value:
            return False
        resource = self.repository.get_resource(
            request["resource_kind"], request["resource_key"]
        )
        if not resource:
            return False
        used = _observed_quota_used(resource["observed_state"])
        hard = desired_state.get("resource_quota_hard") or {}
        block_state = desired_state.get("block_state") or {}
        if block_state.get("blocked") and block_state.get("restore_hard"):
            hard = block_state["restore_hard"]
        issues: list[dict[str, Any]] = []
        for key, used_value in used.items():
            if key not in hard:
                continue
            desired_value = kubernetes_resource_quota_value_to_base_units(
                key, hard[key]
            )
            parsed_used = kubernetes_resource_quota_value_to_base_units(key, used_value)
            if desired_value < parsed_used:
                issues.append(
                    {
                        "reason": "quota_decrease_below_live_used",
                        "resource": key,
                        "desired": hard[key],
                        "used": used_value,
                    }
                )
        if not issues:
            return False
        return self._reject_planner_issue(
            request,
            message="Kubernetes namespace quota decrease is below live used",
            issues=issues,
            error_category="quota_decrease_guard",
        )


    @staticmethod
    def _is_kubernetes_default_reset(request: dict[str, Any]) -> bool:
        return (
            request["operation"] == OperationKind.K8S_QUOTA_UPDATE.value
            and request["payload_summary"].get("reset_quota_to_default") is True
        )


    def _kubernetes_default_policy_for_request(
        self, request: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        existing = self.repository.get_resource(
            request["resource_kind"], request["resource_key"]
        )
        resource_type = request["payload_summary"].get("resource_type") or (
            existing or {}
        ).get("desired_state", {}).get("resource_type")
        if not resource_type:
            return None, [{"reason": "resource_type_required_for_default_quota_reset"}]
        policy = self.repository.get_default_quota_policy(
            resource_kind=ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value,
            resource_type=resource_type,
        )
        if not policy:
            return None, [
                {
                    "reason": "default_quota_policy_missing",
                    "resource_kind": ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value,
                    "resource_type": resource_type,
                }
            ]
        return policy, []


    def _validate_default_policy_storage_entries(
        self, request: dict[str, Any], quota: dict[str, Any]
    ) -> list[dict[str, Any]]:
        target_cluster = request["payload_summary"].get("cluster_name")
        issues: list[dict[str, Any]] = []
        for entry in quota.get("storage_class_quotas") or []:
            if not isinstance(entry, dict) or not entry.get("storage_name"):
                issues.append({"reason": "default_policy_storage_name_missing"})
                continue
            mapping = self.repository.get_storage_mapping(entry["storage_name"])
            if (
                mapping
                and target_cluster
                and mapping.get("cluster_name") != target_cluster
            ):
                issues.append(
                    {
                        "reason": "default_policy_storage_mapping_cluster_mismatch",
                        "storage_name": entry["storage_name"],
                        "request_cluster_name": target_cluster,
                        "mapping_cluster_name": mapping.get("cluster_name"),
                    }
                )
            for key in ("requests_storage_bytes", "capacity_bytes", "pvc_count"):
                if entry.get(key) is None:
                    continue
                try:
                    if int(entry[key]) <= 0:
                        issues.append(
                            {
                                "reason": f"default_policy_{key}_invalid",
                                "storage_name": entry["storage_name"],
                                "value": entry[key],
                            }
                        )
                except (TypeError, ValueError):
                    issues.append(
                        {
                            "reason": f"default_policy_{key}_invalid",
                            "storage_name": entry["storage_name"],
                            "value": entry[key],
                        }
                    )
        return issues


    def _reject_invalid_kubernetes_quota_audit_request(
        self, request: dict[str, Any]
    ) -> bool:
        payload = request["payload_summary"]
        issues: list[dict[str, Any]] = []
        scope = payload.get("scope")
        if not isinstance(scope, dict) or not scope:
            issues.append({"reason": "audit_scope_required"})
            scope = {}
        max_targets = payload.get("max_targets", 100)
        try:
            if int(max_targets) <= 0 or int(max_targets) > 1000:
                issues.append({"reason": "max_targets_invalid", "value": max_targets})
        except (TypeError, ValueError):
            issues.append({"reason": "max_targets_invalid", "value": max_targets})
        thresholds = payload.get("usage_thresholds") or {}
        try:
            warning = float(thresholds.get("warning_percent", 80))
            critical = float(thresholds.get("critical_percent", 95))
            if warning <= 0 or critical <= 0 or critical <= warning:
                issues.append(
                    {
                        "reason": "usage_thresholds_invalid",
                        "warning_percent": warning,
                        "critical_percent": critical,
                    }
                )
        except (TypeError, ValueError):
            issues.append({"reason": "usage_thresholds_invalid", "value": thresholds})
        if scope and not issues:
            targets = self._resolve_kubernetes_quota_audit_targets(request)
            if not targets:
                issues.append({"reason": "audit_targets_empty", "scope": scope})
            if len(targets) > int(max_targets):
                issues.append(
                    {
                        "reason": "audit_targets_exceed_max",
                        "target_count": len(targets),
                        "max_targets": int(max_targets),
                    }
                )
        if not issues:
            return False
        return self._reject_planner_issue(
            request,
            message="invalid Kubernetes namespace quota audit request",
            issues=issues,
            error_category="validation",
        )


    def _resolve_kubernetes_quota_audit_targets(
        self, request: dict[str, Any]
    ) -> list[dict[str, Any]]:
        payload = request["payload_summary"]
        scope = payload.get("scope") or {}
        max_targets = int(payload.get("max_targets", 100))
        status_filter = scope.get("status")
        if isinstance(status_filter, str):
            statuses: list[str] | None = [status_filter]
        elif isinstance(status_filter, list):
            statuses = [str(value) for value in status_filter]
        else:
            statuses = None
        resources = self.repository.list_kubernetes_namespace_quota_resources(
            cluster_name=scope.get("cluster_name"),
            namespace_name=scope.get("namespace_name"),
            requester_id=scope.get("requester_id"),
            resource_type=scope.get("resource_type"),
            status=statuses,
            storage_name=scope.get("storage_name"),
            limit=max_targets + 1,
        )
        targets: list[dict[str, Any]] = []
        for resource in resources[:max_targets]:
            desired = resource["desired_state"]
            targets.append(
                {
                    "cluster_name": desired.get("cluster_name"),
                    "namespace_name": desired.get("namespace_name"),
                    "resource_key": resource["resource_key"],
                    "resource_id": resource["resource_id"],
                    "db_exists": True,
                    "resource_status": resource["status"],
                    "resource_type": desired.get("resource_type"),
                    "desired_hard": desired.get("resource_quota_hard") or {},
                    "desired_state": desired,
                }
            )
        if not targets and scope.get("cluster_name") and scope.get("namespace_name"):
            targets.append(
                {
                    "cluster_name": scope["cluster_name"],
                    "namespace_name": scope["namespace_name"],
                    "resource_key": f"{scope['cluster_name']}:{scope['namespace_name']}",
                    "db_exists": False,
                    "desired_hard": {},
                    "desired_state": {},
                }
            )
        return targets
