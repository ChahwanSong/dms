"""CephFS PATCH owner change applies a real chown on the backend.

Regression for the owner-update no-op bug: a PATCH with `owner_username` set
`update_apply_owner=True` in the plan, but the CephFS `update()` adapter only ever
called `apply_quota` and silently ignored the owner change — the request reported
Succeeded while the on-disk owner never changed. WekaFS already implemented this;
CephFS did not. These tests pin the per-field flag semantics and fail-closed owner
resolution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from dms.adapters import BackendPreconditionError
from dms.backends.cephfs import (
    CephFsBackendTemplate,
    CephFsHostMountedFilesystemBackendAdapter,
)


_UIDS = {"seongje.jang": 10004, "yo.hong": 10006}


@dataclass
class FakeOwnerExecutor:
    calls: list[dict[str, Any]] = field(default_factory=list)
    owner_resolvable: bool = True

    def apply_quota(
        self, *, managed_root, directory_name, resource_key, quota
    ) -> dict[str, Any]:
        self.calls.append({"operation": "apply_quota", "quota": quota})
        return {
            "path": f"{managed_root}/{directory_name}",
            "exists": True,
            "quota_state": {"backend_type": "cephfs", "capacity": {}, "file_count": {}},
            "backend_side_effect": True,
            "verified": True,
        }

    def apply_owner(
        self, *, managed_root, directory_name, resource_key, owner_username
    ) -> dict[str, Any]:
        self.calls.append({"operation": "apply_owner", "owner_username": owner_username})
        if not self.owner_resolvable:
            # Mirrors the host script's PRECONDITION refusal (-> BackendApplyFailed).
            raise BackendPreconditionError(
                f"owner '{owner_username}' is not a resolvable POSIX user on the backend node"
            )
        uid = _UIDS[owner_username]
        return {
            "path": f"{managed_root}/{directory_name}",
            "exists": True,
            "owner_uid": uid,
            "owner_username": owner_username,
            "group_gid": 9000003,
            "group_name": f"dms-grp-{directory_name}",
            "mode": "0770",
            "backend_side_effect": True,
            "verified": True,
        }


def _adapter(executor: FakeOwnerExecutor) -> CephFsHostMountedFilesystemBackendAdapter:
    return CephFsHostMountedFilesystemBackendAdapter(
        template=CephFsBackendTemplate(
            storage_name="cephfs-dms",
            cluster_name="dms",
            mount_path="/cephfs",
            managed_root="/cephfs/dms",
            rm_worker_node="dms-w1",
        ),
        identity_groups=object(),  # unused by update()
        executor=executor,
    )


def _plan(**desired: Any) -> dict[str, Any]:
    base = {
        "directory_name": "proj1",
        "storage_name": "cephfs-dms",
        "resource_key": "cephfs-dms:proj1",
    }
    base.update(desired)
    return {
        "plan_id": "plan-test",
        "request_id": "req-test",
        "resource_key": "cephfs-dms:proj1",
        "desired_state": base,
    }


def _ops(executor: FakeOwnerExecutor) -> list[str]:
    return [c["operation"] for c in executor.calls]


def test_owner_only_update_chowns_and_skips_quota():
    executor = FakeOwnerExecutor()
    # Existing quota is carried in desired from the DB, but update_apply_quota=False.
    result = _adapter(executor).update(
        _plan(
            update_apply_quota=False,
            update_apply_owner=True,
            update_owner_username="seongje.jang",
            quota={"capacity_bytes": 57483648, "file_count": 20000},
        )
    )
    assert _ops(executor) == ["apply_owner"]  # quota NOT re-applied
    assert result.applied_state["owner_change"] == {
        "owner_username": "seongje.jang",
        "uid": 10004,
    }
    assert "owner update completed" in result.message
    assert "quota" not in result.applied_state


def test_owner_only_update_does_not_require_quota():
    # The old code raised "quota is required"; owner-only must NOT require quota.
    executor = FakeOwnerExecutor()
    result = _adapter(executor).update(
        _plan(
            update_apply_quota=False,
            update_apply_owner=True,
            update_owner_username="yo.hong",
        )
    )
    assert _ops(executor) == ["apply_owner"]
    assert result.observed_state["owner_change"]["uid"] == 10006


def test_quota_and_owner_update_applies_both():
    executor = FakeOwnerExecutor()
    result = _adapter(executor).update(
        _plan(
            update_apply_quota=True,
            update_apply_owner=True,
            update_owner_username="seongje.jang",
            quota={"capacity_bytes": 1073741824, "file_count": 10000},
        )
    )
    assert _ops(executor) == ["apply_quota", "apply_owner"]
    assert "quota + owner" in result.message
    assert result.applied_state["owner_change"]["uid"] == 10004
    assert result.applied_state["quota"] == {
        "capacity_bytes": 1073741824,
        "file_count": 10000,
    }


def test_quota_only_update_skips_owner():
    executor = FakeOwnerExecutor()
    result = _adapter(executor).update(
        _plan(
            update_apply_quota=True,
            update_apply_owner=False,
            quota={"capacity_bytes": 1073741824, "file_count": 10000},
        )
    )
    assert _ops(executor) == ["apply_quota"]
    assert result.applied_state["owner_change"] == {}
    assert result.message.endswith("quota update completed")


def test_owner_unresolved_is_fail_closed():
    executor = FakeOwnerExecutor(owner_resolvable=False)
    with pytest.raises(BackendPreconditionError, match="resolvable POSIX user"):
        _adapter(executor).update(
            _plan(
                update_apply_quota=False,
                update_apply_owner=True,
                update_owner_username="seongje.jang",
            )
        )


def test_legacy_plan_without_flags_applies_quota():
    # Plans created before the per-field flags existed carried only a quota.
    executor = FakeOwnerExecutor()
    result = _adapter(executor).update(
        _plan(quota={"capacity_bytes": 57483648, "file_count": 20000})
    )
    assert _ops(executor) == ["apply_quota"]
    assert result.message.endswith("quota update completed")
