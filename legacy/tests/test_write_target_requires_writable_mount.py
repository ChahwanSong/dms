"""A read-only mount must not be picked as a sync destination or an rm target.

The node agent probes `writable` (from the mountinfo `rw` flag, or `os.access(W_OK)`),
but a mount's `status` is decided purely by "exists and is a mountpoint" — so a
read-only mount reports `status: "Ready"`. `_ready_mount` then accepted it for every
operation, and `writable` was consumed nowhere in `src/dms/`. A `data.sync` destination
or a `data.rm` target on such a node was selected and only failed later, inside the MPI
job, after the launcher had already been scheduled.

Read paths (`dscan`, and the sync SOURCE) must keep accepting read-only mounts — that
is exactly what a read-only export is for.

Backwards compatibility: only an explicit `writable: false` disqualifies. An agent too
old to report the field is still accepted, so upgrading the control plane ahead of the
fleet cannot strand every write job.
"""

from __future__ import annotations

from dms.workers.dm import (
    _ready_mount,
    _scan_candidate_rejection_reason,
    _sync_dsync_candidate_rejection_reason,
)


def _mount(storage: str, **over) -> dict:
    return {
        "storage_name": storage,
        "status": "Ready",
        "readable": True,
        "writable": True,
        **over,
    }


def _report(mounts: list[dict], *, tools: list[str]) -> dict:
    return {
        "mounts": mounts,
        "tools": [{"name": t, "healthy": True} for t in tools],
        "credentials": [{"status": "Ready"}],
        "networks": [{"status": "Ready"}],
        "identity_evidence": {"users": [{"username": "alice", "status": "Ready"}]},
    }


# --- _ready_mount ------------------------------------------------------------
def test_read_only_mount_is_skipped_only_when_write_is_required():
    mounts = [_mount("s1", writable=False)]

    assert _ready_mount(mounts, "s1") is not None  # read path still fine
    assert _ready_mount(mounts, "s1", require_writable=True) is None


def test_writable_mount_serves_both_paths():
    mounts = [_mount("s1")]

    assert _ready_mount(mounts, "s1") is not None
    assert _ready_mount(mounts, "s1", require_writable=True) is not None


def test_mount_that_does_not_report_writable_is_still_accepted():
    """An agent older than the writable probe must not strand every write job."""
    mounts = [_mount("s1")]
    del mounts[0]["writable"]

    assert _ready_mount(mounts, "s1", require_writable=True) is not None


def test_a_writable_mount_is_preferred_over_a_read_only_one_for_the_same_storage():
    mounts = [_mount("s1", writable=False), _mount("s1", writable=True)]

    picked = _ready_mount(mounts, "s1", require_writable=True)

    assert picked is not None and picked["writable"] is True


# --- rm target ---------------------------------------------------------------
def test_rm_rejects_a_read_only_target_with_a_distinguishable_reason():
    report = _report([_mount("s1", writable=False)], tools=["drm"])

    reason = _scan_candidate_rejection_reason(
        report, storage_name="s1", tool="drm", posix_username="alice"
    )

    # not "missing_target_mount" — the mount IS there, it just cannot be written
    assert reason == "target_mount_read_only"


def test_rm_accepts_a_writable_target():
    report = _report([_mount("s1")], tools=["drm"])

    assert (
        _scan_candidate_rejection_reason(
            report, storage_name="s1", tool="drm", posix_username="alice"
        )
        is None
    )


def test_scan_still_accepts_a_read_only_target():
    report = _report([_mount("s1", writable=False)], tools=["dscan"])

    assert (
        _scan_candidate_rejection_reason(
            report, storage_name="s1", tool="dscan", posix_username="alice"
        )
        is None
    )


def test_missing_mount_still_reports_missing_not_read_only():
    report = _report([], tools=["drm"])

    assert (
        _scan_candidate_rejection_reason(
            report, storage_name="s1", tool="drm", posix_username="alice"
        )
        == "missing_target_mount"
    )


# --- sync destination --------------------------------------------------------
def test_sync_rejects_a_read_only_destination():
    report = _report(
        [_mount("src"), _mount("dst", writable=False)], tools=["dsync"]
    )

    reason = _sync_dsync_candidate_rejection_reason(
        report,
        source_storage="src",
        destination_storage="dst",
        posix_username="alice",
    )

    assert reason == "destination_mount_read_only"


def test_sync_accepts_a_read_only_source():
    """Copying FROM a read-only export is the normal case."""
    report = _report([_mount("src", writable=False), _mount("dst")], tools=["dsync"])

    reason = _sync_dsync_candidate_rejection_reason(
        report,
        source_storage="src",
        destination_storage="dst",
        posix_username="alice",
    )

    assert reason is None


def test_sync_missing_destination_still_reports_missing():
    report = _report([_mount("src")], tools=["dsync"])

    assert (
        _sync_dsync_candidate_rejection_reason(
            report,
            source_storage="src",
            destination_storage="dst",
            posix_username="alice",
        )
        == "missing_destination_mount"
    )
