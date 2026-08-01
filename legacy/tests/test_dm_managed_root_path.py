"""DM managed_root path base (DMS_DM_PATH_BASE=managed_root) + filesystem managed_root
mandatory registration.

The planner rebases request paths under each storage's managed_root by prepending the
managed_root-relative suffix; managed_root itself is now mandatory at registration for
filesystem backends (no implicit {mount_path}/dms default).
"""
from __future__ import annotations

import pytest

from dms.domain import (
    LifecycleState,
    apply_managed_root_suffix,
    managed_root_for_mapping,
    managed_root_path_suffix,
    validate_filesystem_managed_root,
)
from dms.planner import Planner


# --- domain helpers -------------------------------------------------------------

def test_managed_root_path_suffix():
    assert managed_root_path_suffix("/cephfs", "/cephfs/dms") == "dms"
    assert managed_root_path_suffix("/cephfs/", "/cephfs/dms") == "dms"
    assert managed_root_path_suffix("/cephfs", "/cephfs/a/b") == "a/b"
    assert managed_root_path_suffix("/cephfs", "/cephfs") == ""  # equal -> no-op suffix
    with pytest.raises(ValueError):
        managed_root_path_suffix("/cephfs", "/other/dms")  # escapes mount_path


def test_apply_managed_root_suffix():
    assert apply_managed_root_suffix("scan-test", "dms") == "dms/scan-test"
    assert apply_managed_root_suffix("a/b", "dms") == "dms/a/b"
    assert apply_managed_root_suffix(".", "dms") == "dms"  # empty path -> suffix only
    assert apply_managed_root_suffix("scan-test", "") == "scan-test"  # empty suffix -> unchanged


def test_managed_root_for_mapping():
    cephfs = {
        "backend_template": {
            "backend_type": "cephfs",
            "mount_path": "/cephfs",
            "managed_root": "/cephfs/dms",
        }
    }
    assert managed_root_for_mapping(cephfs) == ("/cephfs", "/cephfs/dms")
    # non-filesystem backend -> None
    assert managed_root_for_mapping({"backend_template": {"backend_type": "ceph-csi"}}) is None
    # filesystem but missing managed_root -> None (fail-closed signal)
    assert (
        managed_root_for_mapping(
            {"backend_template": {"backend_type": "cephfs", "mount_path": "/cephfs"}}
        )
        is None
    )


def test_validate_filesystem_managed_root():
    # valid filesystem mapping
    validate_filesystem_managed_root(
        {"backend_type": "cephfs", "mount_path": "/cephfs", "managed_root": "/cephfs/dms"}
    )
    # non-filesystem backend is skipped
    validate_filesystem_managed_root({"backend_type": "ceph-csi"})
    with pytest.raises(ValueError, match="managed_root"):
        validate_filesystem_managed_root({"backend_type": "cephfs", "mount_path": "/cephfs"})
    with pytest.raises(ValueError, match="mount_path"):
        validate_filesystem_managed_root({"backend_type": "gpfs"})
    with pytest.raises(ValueError, match="under mount_path"):
        validate_filesystem_managed_root(
            {"backend_type": "cephfs", "mount_path": "/cephfs", "managed_root": "/other"}
        )
    # GPFS requires an explicit filesystem_name (no storage_name fallback at registration)
    with pytest.raises(ValueError, match="filesystem_name"):
        validate_filesystem_managed_root(
            {"backend_type": "gpfs", "mount_path": "/gpfs/gpfs0", "managed_root": "/gpfs/gpfs0/dms"}
        )
    # valid GPFS mapping with filesystem_name passes
    validate_filesystem_managed_root(
        {
            "backend_type": "gpfs",
            "mount_path": "/gpfs/gpfs0",
            "managed_root": "/gpfs/gpfs0/dms",
            "filesystem_name": "gpfs0",
        }
    )
    # cephfs/wekafs do NOT require filesystem_name
    validate_filesystem_managed_root(
        {"backend_type": "cephfs", "mount_path": "/cephfs", "managed_root": "/cephfs/dms"}
    )


# --- planner rebasing (_rebase_paths_for_managed_root is settings-independent) ----

class _StubRepo:
    def __init__(self, mappings: dict):
        self._mappings = mappings
        self.rejected: dict | None = None

    def get_storage_mapping(self, name):
        return self._mappings.get(name)

    def complete_result(self, **kwargs):
        self.rejected = kwargs


def _mapping(backend_type: str, mount_path: str, managed_root: str | None = None) -> dict:
    template = {"backend_type": backend_type, "mount_path": mount_path}
    if managed_root:
        template["managed_root"] = managed_root
    return {"backend_template": template}


def test_planner_rebase_scan():
    repo = _StubRepo({"cephfs-dms": _mapping("cephfs", "/cephfs", "/cephfs/dms")})
    planner = Planner(repo)
    nt = planner._rebase_paths_for_managed_root(
        {"request_id": "r"}, "data.scan", {"storage_name": "cephfs-dms", "path": "scan-test"}
    )
    assert nt == {"storage_name": "cephfs-dms", "path": "dms/scan-test"}


def test_planner_rebase_nsync_multi_storage():
    repo = _StubRepo(
        {
            "cephfs-third": _mapping("cephfs", "/cephfs-third", "/cephfs-third/dms"),
            "cephfs-secondary": _mapping(
                "cephfs", "/cephfs-secondary", "/cephfs-secondary/dms"
            ),
        }
    )
    planner = Planner(repo)
    nt = planner._rebase_paths_for_managed_root(
        {"request_id": "r"},
        "data.sync",
        {
            "source": {"storage_name": "cephfs-third", "path": "proj/in"},
            "destination": {"storage_name": "cephfs-secondary", "path": "proj/out"},
            "options": {"contents": True},
        },
    )
    # each storage gets its own suffix
    assert nt["source"]["path"] == "dms/proj/in"
    assert nt["destination"]["path"] == "dms/proj/out"
    assert nt["options"] == {"contents": True}  # unrelated keys preserved


def test_planner_rebase_rejects_missing_managed_root():
    repo = _StubRepo({"cephfs-dms": _mapping("cephfs", "/cephfs")})  # no managed_root
    planner = Planner(repo)
    nt = planner._rebase_paths_for_managed_root(
        {"request_id": "r"}, "data.scan", {"storage_name": "cephfs-dms", "path": "x"}
    )
    assert nt is None
    assert repo.rejected is not None
    assert repo.rejected["terminal_status"] == LifecycleState.REJECTED
    assert repo.rejected["error_category"] == "planner"
