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


class FilesystemPlannerMixin:
    """Filesystem request validation and desired-state planning."""


    def _filesystem_desired_state(
        self, request: dict[str, Any], desired: dict[str, Any]
    ) -> dict[str, Any]:
        if request["operation"] == OperationKind.FILESYSTEM_EXPIRATION_SWEEP.value:
            desired.update(
                {
                    "operation": request["operation"],
                    "resource_kind": request["resource_kind"],
                    "resource_key": request["resource_key"],
                    "targets": self._resolve_filesystem_expiration_targets(request),
                }
            )
            return desired
        existing = self.repository.get_resource(
            request["resource_kind"], request["resource_key"]
        )
        if (
            request["operation"]
            in {
                OperationKind.FILESYSTEM_UPDATE.value,
                OperationKind.FILESYSTEM_BLOCK.value,
                OperationKind.FILESYSTEM_DELETE.value,
                OperationKind.FILESYSTEM_ASSIGN_QUOTA.value,
                OperationKind.FILESYSTEM_IMPORT.value,
                OperationKind.FILESYSTEM_CHECK.value,
                OperationKind.FILESYSTEM_SYNC.value,
            }
            and existing
            and existing.get("status") != "Deleted"
        ):
            merged = dict(existing["desired_state"])
            merged.update(desired)
            desired = merged
        self._apply_expiry_desired(
            request=request,
            desired=desired,
            existing_desired=(existing or {}).get("desired_state") or {},
        )
        if request["operation"] == OperationKind.FILESYSTEM_CREATE.value:
            directory_name = desired.get("directory_name")
            if directory_name and not desired.get("access_group"):
                desired["access_group"] = f"dms-grp-{directory_name}"
            desired.setdefault("mode", "0750")
            desired.setdefault("resource_type", "user")
            # Default the directory owner to requester_id; payload may override
            # via owner_username for create-on-behalf flows.
            if not desired.get("owner_username"):
                desired["owner_username"] = request["payload_summary"].get(
                    "owner_username"
                ) or request.get("requester_id")
            if "quota" in desired:
                desired["quota"] = _normalized_filesystem_quota(desired["quota"])
        if request["operation"] == OperationKind.FILESYSTEM_UPDATE.value:
            payload = request["payload_summary"]
            if "quota" in payload:
                desired["quota"] = _normalized_filesystem_quota(payload["quota"])
                desired["update_apply_quota"] = True
            else:
                desired["update_apply_quota"] = False
            if "owner_username" in payload:
                desired["update_owner_username"] = payload["owner_username"]
                desired["owner_username"] = payload["owner_username"]
                desired["update_apply_owner"] = True
            else:
                desired["update_apply_owner"] = False
        if request["operation"] == OperationKind.FILESYSTEM_ASSIGN_QUOTA.value:
            desired.setdefault("management_mode", "quota_only")
            desired["quota"] = _normalized_filesystem_quota(
                request["payload_summary"]["quota"]
            )
            desired.setdefault("resource_type", "user")
        if request["operation"] == OperationKind.FILESYSTEM_IMPORT.value:
            desired.setdefault("import_mode", "full")
            desired.setdefault("management_mode", "full")
            access_policy = desired.get("access_policy") or {}
            if access_policy.get("users") is not None:
                desired["users"] = list(access_policy["users"])
            if access_policy.get("denied_users") is not None:
                desired["validation_denied_users"] = list(access_policy["denied_users"])
            if access_policy.get("expected_group") and not desired.get("access_group"):
                desired["access_group"] = access_policy["expected_group"]
            if access_policy.get("expected_mode") and not desired.get("mode"):
                desired["mode"] = access_policy["expected_mode"]
            desired.setdefault("resource_type", "user")
            if "quota" in request["payload_summary"]:
                desired["quota"] = _normalized_filesystem_quota(
                    request["payload_summary"]["quota"]
                )
        if request["operation"] == OperationKind.FILESYSTEM_CHECK.value:
            desired.setdefault("include_quota", True)
            desired.setdefault("include_permission", True)
        if request["operation"] == OperationKind.FILESYSTEM_SYNC.value:
            desired.setdefault("source", "live")
            desired.setdefault("include_quota", True)
        if request["operation"] == OperationKind.FILESYSTEM_BLOCK.value:
            self._apply_filesystem_block_desired(request, existing, desired)
        return desired


    def _apply_filesystem_block_desired(
        self,
        request: dict[str, Any],
        existing: dict[str, Any] | None,
        desired: dict[str, Any],
    ) -> None:
        block = bool(request["payload_summary"].get("block"))
        desired["block"] = block
        if block:
            prior_block_state = dict(
                (existing or {}).get("desired_state", {}).get("block_state") or {}
            )
            if prior_block_state.get("blocked"):
                desired["block_state"] = prior_block_state
                return
            desired["block_state"] = {
                "blocked": True,
                "block_mode": request["payload_summary"].get(
                    "block_mode", "permission-zero"
                ),
                "reason": request["payload_summary"].get("reason"),
            }
            return
        block_state = dict(
            (existing or {}).get("desired_state", {}).get("block_state") or {}
        )
        if not block_state:
            block_state = dict(
                (existing or {}).get("observed_state", {}).get("block_state") or {}
            )
        block_state["blocked"] = False
        block_state["reason"] = request["payload_summary"].get("reason")
        # previous_mode 없으면 desired_state.mode 또는 기본값 0750으로 fallback
        if not block_state.get("previous_mode"):
            block_state["previous_mode"] = (existing or {}).get(
                "desired_state", {}
            ).get("mode") or "0750"
        desired["block_state"] = block_state


    def _resolve_filesystem_expiration_targets(
        self, request: dict[str, Any]
    ) -> list[dict[str, Any]]:
        payload = request["payload_summary"]
        scope = payload.get("scope") or {}
        max_targets = int(payload.get("max_targets", 100))
        resources = self.repository.list_filesystem_resources_expiring(
            storage_name=scope.get("storage_name"),
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
                    "storage_name": desired.get("storage_name"),
                    "directory_name": desired.get("directory_name"),
                    "resource_key": resource["resource_key"],
                    "resource_id": resource["resource_id"],
                    "resource_status": resource["status"],
                    "resource_type": desired.get("resource_type") or "user",
                    "expires_at": resource.get("expires_at"),
                    "block_state": resource.get("block_state") or {},
                    "desired_state": desired,
                }
            )
        return targets


    def _reject_invalid_filesystem_request(self, request: dict[str, Any]) -> bool:
        if request["resource_kind"] != ResourceKind.FILESYSTEM.value:
            return False
        operation = request["operation"]
        payload = request["payload_summary"]
        issues: list[dict[str, Any]] = []

        if operation not in FILESYSTEM_RM_OPERATIONS:
            return self._reject_planner_issue(
                request,
                message="filesystem operation is unsupported in Phase 12",
                issues=[
                    {
                        "reason": "filesystem_operation_unsupported",
                        "operation": operation,
                    }
                ],
                error_category="unsupported",
            )

        if operation == OperationKind.FILESYSTEM_EXPIRATION_SWEEP.value:
            return self._reject_invalid_filesystem_expiration_sweep_request(
                request, issues
            )

        storage_name = payload.get("storage_name")
        directory_name = payload.get("directory_name")
        _append_basename_issue(issues, "storage_name", storage_name)
        _append_basename_issue(issues, "directory_name", directory_name)

        if operation == OperationKind.FILESYSTEM_CREATE.value:
            _append_expiry_issues(
                issues,
                payload,
                operation=operation,
                existing_desired={},
                resource_kind=ResourceKind.FILESYSTEM.value,
            )
            unsupported = sorted(
                field
                for field in FILESYSTEM_CREATE_UNSUPPORTED_PAYLOAD_FIELDS
                if field in payload
            )
            if unsupported:
                issues.append(
                    {
                        "reason": "filesystem_payload_fields_unsupported",
                        "fields": unsupported,
                    }
                )
            if "quota" in payload:
                issues.extend(_filesystem_quota_issues(payload.get("quota")))
            resource_type = payload.get("resource_type")
            if resource_type is not None and str(resource_type) not in {
                "user",
                "project",
                "system",
                "admin",
            }:
                issues.append(
                    {
                        "reason": "filesystem_resource_type_unsupported",
                        "resource_type": resource_type,
                    }
                )
            existing = self.repository.get_resource(
                request["resource_kind"], request["resource_key"]
            )
            if existing and existing.get("status") != "Deleted":
                issues.append(
                    {
                        "reason": "filesystem_resource_already_exists",
                        "resource_key": request["resource_key"],
                        "status": existing.get("status"),
                    }
                )
            users = payload.get("users")
            users_list = _validate_string_list(issues, "users", users, required=True)
            if users_list is not None and len(users_list) < 1:
                issues.append({"reason": "filesystem_users_minimum_one_required"})
            access_group = payload.get("access_group")
            if access_group is not None:
                _append_basename_issue(issues, "access_group", access_group)
                if isinstance(access_group, str) and not access_group.startswith(
                    "dms-"
                ):
                    issues.append(
                        {
                            "reason": "filesystem_access_group_must_be_dms_managed",
                            "access_group": access_group,
                        }
                    )
            mode = payload.get("mode")
            if mode is not None and str(mode) not in {"0750", "0770"}:
                issues.append(
                    {
                        "reason": "filesystem_mode_unsupported",
                        "mode": mode,
                    }
                )
            denied_users = payload.get("denied_users") or payload.get(
                "validation_denied_users"
            )
            if denied_users is not None:
                _validate_string_list(
                    issues,
                    "denied_users",
                    denied_users,
                    required=False,
                )
            # The directory owner defaults to the requester (requester_id); a create
            # may override it with an explicit owner_username. requester_id is a
            # free-form logical id, so only an EXPLICIT owner_username is sanity-checked
            # here (safe POSIX basename, not a reserved/privileged account). The backend
            # additionally enforces that the effective owner resolves to a real,
            # non-system POSIX user before any side effect.
            owner_override = payload.get("owner_username")
            if owner_override is not None:
                _append_basename_issue(issues, "owner_username", owner_override)
                if str(owner_override) in {"root", "nobody"}:
                    issues.append(
                        {
                            "reason": "filesystem_owner_username_unsupported",
                            "owner_username": owner_override,
                        }
                    )
        if operation == OperationKind.FILESYSTEM_BLOCK.value:
            unsupported = sorted(
                field
                for field in FILESYSTEM_BLOCK_UNSUPPORTED_PAYLOAD_FIELDS
                if field in payload
            )
            if unsupported:
                issues.append(
                    {
                        "reason": "filesystem_payload_fields_unsupported",
                        "fields": unsupported,
                    }
                )
            existing = self.repository.get_resource(
                request["resource_kind"], request["resource_key"]
            )
            if not existing or existing.get("status") == "Deleted":
                issues.append(
                    {
                        "reason": "filesystem_resource_missing",
                        "resource_key": request["resource_key"],
                    }
                )
            block = payload.get("block")
            if not isinstance(block, bool):
                issues.append({"reason": "block_boolean_required", "value": block})
            if block is True and existing:
                resource_type = existing["desired_state"].get("resource_type") or "user"
                if resource_type in {"system", "admin"}:
                    issues.append(
                        {
                            "reason": "resource_type_cannot_be_blocked",
                            "resource_type": resource_type,
                        }
                    )
                block_mode = payload.get("block_mode", "permission-zero")
                if block_mode != "permission-zero":
                    issues.append(
                        {
                            "reason": "filesystem_block_mode_unsupported",
                            "block_mode": block_mode,
                        }
                    )
            if block is False and existing:
                pass  # restore_state 없어도 desired_state.mode fallback으로 복원

        if operation == OperationKind.FILESYSTEM_DELETE.value:
            existing = self.repository.get_resource(
                request["resource_kind"], request["resource_key"]
            )
            if not existing or existing.get("status") == "Deleted":
                issues.append(
                    {
                        "reason": "filesystem_resource_missing",
                        "resource_key": request["resource_key"],
                    }
                )
            elif existing["desired_state"].get("management_mode") == "quota_only":
                issues.append(
                    {
                        "reason": "filesystem_quota_only_delete_refused",
                        "resource_key": request["resource_key"],
                    }
                )

        if operation == OperationKind.FILESYSTEM_UPDATE.value:
            existing = self.repository.get_resource(
                request["resource_kind"], request["resource_key"]
            )
            _append_expiry_issues(
                issues,
                payload,
                operation=operation,
                existing_desired=(existing or {}).get("desired_state") or {},
                resource_kind=ResourceKind.FILESYSTEM.value,
            )
            unsupported_field_issues = _unsupported_payload_issues(
                payload,
                FILESYSTEM_UPDATE_ALLOWED_PAYLOAD_FIELDS
                | EXPIRY_UNSUPPORTED_PAYLOAD_FIELDS,
                "filesystem_payload_fields_unsupported",
            )
            issues.extend(unsupported_field_issues)
            if not existing or existing.get("status") == "Deleted":
                issues.append(
                    {
                        "reason": "filesystem_resource_missing",
                        "resource_key": request["resource_key"],
                    }
                )
            if "quota" in payload:
                issues.extend(_filesystem_quota_issues(payload.get("quota")))
            elif (
                not unsupported_field_issues
                and "expires_at" not in payload
                and "resource_type" not in payload
                and "owner_username" not in payload
            ):
                # Only flag "empty" when the payload carries no recognized update field
                # AND no unsupported field — an unsupported-field-only PATCH is reported
                # as unsupported, not as empty.
                issues.append({"reason": "filesystem_update_payload_empty"})
            update_resource_type = payload.get("resource_type")
            if update_resource_type is not None and str(update_resource_type) not in {
                "user",
                "project",
                "system",
                "admin",
            }:
                issues.append(
                    {
                        "reason": "filesystem_resource_type_unsupported",
                        "resource_type": update_resource_type,
                    }
                )

        if operation == OperationKind.FILESYSTEM_ASSIGN_QUOTA.value:
            existing = self.repository.get_resource(
                request["resource_kind"], request["resource_key"]
            )
            if existing and existing.get("status") != "Deleted":
                mode = existing["desired_state"].get("management_mode", "full")
                if mode != "quota_only":
                    issues.append(
                        {
                            "reason": "filesystem_resource_already_exists",
                            "resource_key": request["resource_key"],
                            "status": existing.get("status"),
                        }
                    )
            management_mode = payload.get("management_mode", "quota_only")
            if management_mode != "quota_only":
                issues.append(
                    {
                        "reason": "filesystem_management_mode_unsupported",
                        "management_mode": management_mode,
                    }
                )
            if "quota" not in payload:
                issues.append({"reason": "filesystem_quota_required"})
            else:
                issues.extend(_filesystem_quota_issues(payload.get("quota")))

        if operation == OperationKind.FILESYSTEM_IMPORT.value:
            existing = self.repository.get_resource(
                request["resource_kind"], request["resource_key"]
            )
            _append_expiry_issues(
                issues,
                payload,
                operation=operation,
                existing_desired=(existing or {}).get("desired_state") or {},
                resource_kind=ResourceKind.FILESYSTEM.value,
            )
            if existing and existing.get("status") != "Deleted":
                mode = existing["desired_state"].get("management_mode", "full")
                if mode != "quota_only":
                    issues.append(
                        {
                            "reason": "filesystem_resource_already_exists",
                            "resource_key": request["resource_key"],
                            "status": existing.get("status"),
                        }
                    )
            import_mode = payload.get("import_mode", "full")
            if import_mode != "full":
                issues.append(
                    {
                        "reason": "filesystem_import_mode_unsupported",
                        "import_mode": import_mode,
                    }
                )
            # access_policy is now optional — the backend discovers the live
            # group via stat()+LDAP and either adopts it (if dms-grp-*) or
            # creates a new dms-grp-{dir} carrying the external members. Hints
            # are still validated when provided.
            access_policy = payload.get("access_policy")
            if access_policy is not None and not isinstance(access_policy, dict):
                issues.append({"reason": "filesystem_access_policy_invalid"})
                access_policy = {}
            access_policy = access_policy or {}
            if (
                access_policy
                and access_policy.get("mode", "adopt_existing_group")
                != "adopt_existing_group"
            ):
                issues.append(
                    {
                        "reason": "filesystem_access_policy_mode_unsupported",
                        "mode": access_policy.get("mode"),
                    }
                )
            expected_group = access_policy.get("expected_group")
            if expected_group is not None:
                _append_basename_issue(issues, "expected_group", expected_group)
            expected_mode = access_policy.get("expected_mode", "0750")
            if str(expected_mode) not in {"0750", "0770"}:
                issues.append(
                    {
                        "reason": "filesystem_mode_unsupported",
                        "mode": expected_mode,
                    }
                )
            users = _validate_string_list(
                issues,
                "access_policy.users",
                access_policy.get("users"),
                required=False,
            )
            if users is not None and len(users) < 1:
                issues.append({"reason": "filesystem_users_minimum_one_required"})
            denied_users = access_policy.get("denied_users")
            if denied_users is not None:
                _validate_string_list(
                    issues,
                    "access_policy.denied_users",
                    denied_users,
                    required=False,
                )
            if "quota" in payload:
                issues.extend(_filesystem_quota_issues(payload.get("quota")))

        if operation == OperationKind.FILESYSTEM_CHECK.value:
            existing = self.repository.get_resource(
                request["resource_kind"], request["resource_key"]
            )
            issues.extend(
                _unsupported_payload_issues(
                    payload,
                    FILESYSTEM_CHECK_ALLOWED_PAYLOAD_FIELDS,
                    "filesystem_payload_fields_unsupported",
                )
            )
            if not existing or existing.get("status") == "Deleted":
                issues.append(
                    {
                        "reason": "filesystem_resource_missing",
                        "resource_key": request["resource_key"],
                    }
                )
            _append_boolean_payload_issues(
                issues,
                payload,
                ("include_quota", "include_permission", "record_action_required"),
            )

        if operation == OperationKind.FILESYSTEM_SYNC.value:
            existing = self.repository.get_resource(
                request["resource_kind"], request["resource_key"]
            )
            issues.extend(
                _unsupported_payload_issues(
                    payload,
                    FILESYSTEM_SYNC_ALLOWED_PAYLOAD_FIELDS,
                    "filesystem_payload_fields_unsupported",
                )
            )
            if not existing or existing.get("status") == "Deleted":
                issues.append(
                    {
                        "reason": "filesystem_resource_missing",
                        "resource_key": request["resource_key"],
                    }
                )
            if payload.get("source", "live") != "live":
                issues.append(
                    {
                        "reason": "filesystem_sync_source_unsupported",
                        "source": payload.get("source"),
                    }
                )
            _append_boolean_payload_issues(
                issues,
                payload,
                ("include_quota",),
            )

        if not issues:
            return False
        return self._reject_planner_issue(
            request,
            message="invalid filesystem request",
            issues=issues,
            error_category="validation",
        )


    def _reject_invalid_filesystem_expiration_sweep_request(
        self, request: dict[str, Any], issues: list[dict[str, Any]]
    ) -> bool:
        payload = request["payload_summary"]
        action = payload.get("action", "block")
        if action != "block":
            issues.append(
                {"reason": "filesystem_sweep_action_unsupported", "action": action}
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
                        "reason": "filesystem_expired_before_invalid",
                        "expired_before": expired_before,
                    }
                )
        scope = payload.get("scope") or {}
        if scope and not isinstance(scope, dict):
            issues.append({"reason": "filesystem_sweep_scope_invalid"})
            scope = {}
        storage_name = scope.get("storage_name")
        if storage_name is not None:
            _append_basename_issue(issues, "storage_name", storage_name)
        resource_type = scope.get("resource_type")
        if resource_type is not None and str(resource_type) not in {
            "user",
            "project",
            "system",
            "admin",
        }:
            issues.append(
                {
                    "reason": "filesystem_resource_type_unsupported",
                    "resource_type": resource_type,
                }
            )
        if not issues:
            targets = self._resolve_filesystem_expiration_targets(request)
            if len(targets) > int(max_targets):
                issues.append(
                    {
                        "reason": "filesystem_sweep_targets_exceed_max",
                        "target_count": len(targets),
                        "max_targets": int(max_targets),
                    }
                )
        if not issues:
            return False
        return self._reject_planner_issue(
            request,
            message="invalid filesystem Phase 11 request",
            issues=issues,
            error_category="validation",
        )
