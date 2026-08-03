from dms.execution import JobSpec
from dms.execution_manifests import render_tool_flags, tool_argv


def _spec(**kw):
    base = dict(job_id="j1", phase="execution", operation="scan", tool="dscan",
                dryrun=False, identity={"uid": 10001}, paths={}, options={},
                candidates={"primary": ["n1"]}, process_count=8, queue="dms-data",
                priority_class="dms-mid", artifact_base="file:///cephfs/dms/artifacts")
    base.update(kw)
    return JobSpec(**base)


def test_sync_flags():
    flags = render_tool_flags("dsync", {"delete": True, "quiet": False,
                                        "batch_files": 1000, "chown": "alice:dev"})
    assert "--delete" in flags and "--quiet" not in flags
    assert flags[flags.index("--batch-files") + 1] == "1000"
    assert flags[flags.index("--chown") + 1] == "alice:dev"


def test_rm_flags():
    assert render_tool_flags("drm", {"stat": True, "lite": False}) == ["--stat"]


def test_scan_argv():
    argv = tool_argv(_spec(operation="scan", tool="dscan"),
                     abs_paths={"target": "/cephfs/dms/team/data"})
    assert argv == ["--directory", "/cephfs/dms/team/data",
                    "--output", "$DMS_SCAN_REPORT", "--print"]


def test_sync_argv_with_dryrun():
    spec = _spec(operation="sync", tool="dsync", dryrun=True, options={"delete": True})
    argv = tool_argv(spec, abs_paths={"source": "/cephfs/a", "destination": "/cephfs/b"})
    assert argv == ["--delete", "--dryrun", "/cephfs/a", "/cephfs/b"]


def test_rm_argv():
    spec = _spec(operation="rm", tool="drm", dryrun=False,
                 options={"recursive": True})
    argv = tool_argv(spec, abs_paths={"target": "/cephfs/junk"})
    assert argv == ["/cephfs/junk"]  # recursive는 drm 기본 재귀 — 플래그 아님
