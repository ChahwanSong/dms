from dms.execution import JobSpec
from dms.execution_manifests import render_tool_flags, tool_argv, build_volcano_job


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


_VOL = [{"name": "cephfs", "hostPath": {"path": "/cephfs"}, "mountPath": "/cephfs"}]


def test_build_volcano_job_scan_structure():
    spec = _spec(operation="scan", tool="dscan", candidates={"primary": ["dms-w1"]},
                 process_count=8, paths={"target": "/cephfs/data"})
    m = build_volcano_job(spec, job_image="reg/img:1", namespace="dms", volumes=_VOL)
    assert m["apiVersion"] == "batch.volcano.sh/v1alpha1" and m["kind"] == "Job"
    assert m["metadata"]["namespace"] == "dms"
    assert m["metadata"]["labels"]["dms.io/job-id"] == "j1"
    assert m["spec"]["schedulerName"] == "volcano"
    assert m["spec"]["queue"] == "dms-data"
    assert m["spec"]["priorityClassName"] == "dms-mid"
    assert m["spec"]["plugins"] == {"ssh": [], "svc": []}
    names = [t["name"] for t in m["spec"]["tasks"]]
    assert "launcher" in names and "worker" in names
    launcher = next(t for t in m["spec"]["tasks"] if t["name"] == "launcher")
    assert launcher["replicas"] == 1
    worker = next(t for t in m["spec"]["tasks"] if t["name"] == "worker")
    assert worker["replicas"] == 1  # 후보 1개
    assert m["spec"]["minAvailable"] == 2  # worker 1 + launcher 1
    # launcher env에 도구/argv 전달
    env = {e["name"]: e["value"]
           for e in launcher["template"]["spec"]["containers"][0]["env"]}
    assert env["DMS_JR_TOOL"] == "dscan"
    assert env["DMS_JR_UID"] == "10001"
    import json
    assert json.loads(env["DMS_JR_ARGV"])[0] == "--directory"


def test_worker_replicas_follow_candidates():
    spec = _spec(operation="sync", tool="dsync",
                 candidates={"primary": ["dms-w1", "dms-w2", "dms-w3"]},
                 paths={"source": "s", "source_storage": "src",
                        "destination": "d", "destination_storage": "dst"})
    m = build_volcano_job(spec, job_image="i", namespace="dms", volumes=_VOL)
    worker = next(t for t in m["spec"]["tasks"] if t["name"] == "worker")
    assert worker["replicas"] == 3 and m["spec"]["minAvailable"] == 4
