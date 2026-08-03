"""Volcano 매니페스트 + 도구 명령 빌더. 전부 순수 함수 — 실제 제출은 어댑터(Task 6)."""

import json

_SYNC_BOOL_FLAGS = {"delete": "--delete", "contents": "--contents",
                    "direct": "--direct", "open_noatime": "--open-noatime",
                    "quiet": "--quiet"}
_SYNC_VALUE_FLAGS = {"batch_files": "--batch-files", "bufsize": "--bufsize",
                     "chmod": "--chmod", "chown": "--chown"}
_RM_BOOL_FLAGS = {"stat": "--stat", "lite": "--lite", "quiet": "--quiet"}


def render_tool_flags(tool: str, options: dict) -> list[str]:
    options = options or {}
    flags: list[str] = []
    if tool in ("dsync", "nsync"):
        for key, flag in _SYNC_BOOL_FLAGS.items():
            if options.get(key) is True:
                flags.append(flag)
        for key, flag in _SYNC_VALUE_FLAGS.items():
            if key in options:
                flags.extend([flag, str(options[key])])
    elif tool == "drm":
        for key, flag in _RM_BOOL_FLAGS.items():
            if options.get(key) is True:
                flags.append(flag)
    return flags


def tool_argv(spec, *, abs_paths: dict) -> list[str]:
    if spec.tool == "dscan":
        return ["--directory", abs_paths["target"],
                "--output", "$DMS_SCAN_REPORT", "--print"]
    flags = render_tool_flags(spec.tool, spec.options)
    dry = ["--dryrun"] if spec.dryrun else []
    if spec.tool in ("dsync", "nsync"):
        return [*flags, *dry, abs_paths["source"], abs_paths["destination"]]
    # drm
    return [*flags, *dry, abs_paths["target"]]


def _worker_count(spec) -> int:
    return max(1, len(spec.candidates.get("primary", [])))


def _abs_paths(spec):
    # paths는 상대. 절대경로는 stepper가 spec.paths에 이미 절대로 넣어준다(Task 7).
    # 여기선 spec.paths를 그대로 절대로 취급.
    return spec.paths


def _container(name, image, command, env, volumes):
    return {
        "name": name, "image": image, "command": command,
        "securityContext": {"runAsUser": 0},
        "env": [{"name": k, "value": v} for k, v in env.items()],
        "volumeMounts": [{"name": v["name"], "mountPath": v["mountPath"],
                          "mountPropagation": "HostToContainer"} for v in volumes],
    }


def _pod_volumes(volumes):
    return [{"name": v["name"], "hostPath": {"path": v["hostPath"]["path"],
                                             "type": "Directory"}} for v in volumes]


def _artifact_dir(spec):
    # artifact_base는 URI(file:///cephfs/...) — 파드 안 파일 연산용으로 스킴 제거.
    base = spec.artifact_base.replace("file://", "")
    return f"{base}/{spec.job_id}/{spec.phase}"


def _launcher_env(spec):
    ap = _abs_paths(spec)
    argv = tool_argv(spec, abs_paths=ap)
    ident = spec.identity or {}
    return {
        "DMS_JR_TOOL": spec.tool, "DMS_JR_OPERATION": spec.operation,
        "DMS_JR_PHASE": spec.phase, "DMS_JR_DRYRUN": "1" if spec.dryrun else "0",
        "DMS_JR_PROCESS_COUNT": str(spec.process_count),
        "DMS_JR_ARGV": json.dumps(argv),
        "DMS_JR_UID": str(ident.get("uid", 0)), "DMS_JR_GID": str(ident.get("gid", 0)),
        "DMS_JR_USERNAME": ident.get("username", "root"),
        "DMS_JR_ARTIFACT_DIR": _artifact_dir(spec),  # 스킴 제거된 파일시스템 경로
    }


def _job_name(spec):
    return f"dms-{spec.operation}-{spec.phase}-{spec.job_id[:12]}"


def _node_affinity(nodes):
    return {"nodeAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": {
        "nodeSelectorTerms": [{"matchExpressions": [
            {"key": "kubernetes.io/hostname", "operator": "In", "values": nodes}]}]}}}


def _preflight_script(spec):
    """(script, path_args) — 경로는 positional 파라미터로 넘겨 셸 인젝션을 원천 차단."""
    ap = _abs_paths(spec)
    if spec.operation == "sync":
        script = ('set -e; '
                  'test -r "$1" || { echo DMS_PREFLIGHT_REASON=source_not_readable; exit 1; }; '
                  'dest_parent=$(dirname "$2"); '
                  'test -w "$dest_parent" || { echo DMS_PREFLIGHT_REASON=destination_parent_not_writable; exit 1; }; '
                  'echo DMS_PREFLIGHT_OK')
        return script, [ap["source"], ap["destination"]]
    if spec.operation == "rm":
        script = ('set -e; '
                  'parent=$(dirname "$1"); '
                  'test -w "$parent" || { echo DMS_PREFLIGHT_REASON=parent_not_writable; exit 1; }; '
                  'echo DMS_PREFLIGHT_OK')
        return script, [ap["target"]]
    script = ('set -e; '
              'test -r "$1" || { echo DMS_PREFLIGHT_REASON=target_not_readable; exit 1; }; '
              'echo DMS_PREFLIGHT_OK')
    return script, [ap["target"]]


def build_preflight_pod(spec, *, job_image, namespace, volumes, node):
    ident = spec.identity or {}
    script, path_args = _preflight_script(spec)
    return {
        "apiVersion": "v1", "kind": "Pod",
        "metadata": {"name": f"dms-preflight-{spec.job_id[:12]}-{node}"[:63],
                     "namespace": namespace,
                     "labels": {"dms.io/job-id": spec.job_id,
                                "dms.io/phase": "preflight"}},
        "spec": {"restartPolicy": "Never",
                 "nodeSelector": {"kubernetes.io/hostname": node},
                 "containers": [{
                     "name": "preflight", "image": job_image,
                     "command": ["sh", "-c", script, "sh", *path_args],
                     "securityContext": {"runAsUser": ident.get("uid", 0),
                                         "runAsGroup": ident.get("gid", 0)},
                     "volumeMounts": [{"name": v["name"], "mountPath": v["mountPath"],
                                       "mountPropagation": "HostToContainer"}
                                      for v in volumes]}],
                 "volumes": _pod_volumes(volumes)}}


def _build_nsync_job(spec, *, job_image, namespace, volumes):
    src_nodes = spec.candidates["source"]
    dst_nodes = spec.candidates["destination"]
    env = _launcher_env(spec)
    env["DMS_JR_SOURCE_NODES"] = json.dumps(src_nodes)
    env["DMS_JR_DEST_NODES"] = json.dumps(dst_nodes)
    launcher = {"name": "launcher", "replicas": 1, "template": {"spec": {
        "restartPolicy": "Never",
        "containers": [_container("launcher", job_image,
            ["/usr/local/bin/dms-job-runner"], env, volumes)],
        "volumes": _pod_volumes(volumes)}}}
    src_worker = {"name": "source-worker", "replicas": len(src_nodes),
        "template": {"spec": {"restartPolicy": "Never",
            "affinity": _node_affinity(src_nodes),
            "containers": [_container("source-worker", job_image,
                ["/usr/sbin/sshd", "-D"], {}, volumes)],
            "volumes": _pod_volumes(volumes)}}}
    dst_worker = {"name": "destination-worker", "replicas": len(dst_nodes),
        "template": {"spec": {"restartPolicy": "Never",
            "affinity": _node_affinity(dst_nodes),
            "containers": [_container("destination-worker", job_image,
                ["/usr/sbin/sshd", "-D"], {}, volumes)],
            "volumes": _pod_volumes(volumes)}}}
    return {
        "apiVersion": "batch.volcano.sh/v1alpha1", "kind": "Job",
        "metadata": {"name": _job_name(spec), "namespace": namespace,
                     "labels": {"dms.io/job-id": spec.job_id,
                                "dms.io/phase": spec.phase, "dms.io/tool": spec.tool}},
        "spec": {"schedulerName": "volcano", "queue": spec.queue,
                 "minAvailable": len(src_nodes) + len(dst_nodes) + 1,
                 "priorityClassName": spec.priority_class,
                 "plugins": {"ssh": [], "svc": []},
                 "policies": [{"event": "TaskCompleted", "action": "CompleteJob"},
                              {"event": "PodFailed", "action": "AbortJob"}],
                 "tasks": [launcher, src_worker, dst_worker]}}


def build_volcano_job(spec, *, job_image, namespace, volumes):
    if "primary" not in spec.candidates:
        return _build_nsync_job(spec, job_image=job_image, namespace=namespace,
                                volumes=volumes)
    workers = _worker_count(spec)
    nodes = spec.candidates.get("primary", [])
    launcher = {
        "name": "launcher", "replicas": 1,
        "template": {"spec": {
            "restartPolicy": "Never",
            "affinity": _node_affinity(nodes) if nodes else {},
            "containers": [_container("launcher", job_image,
                ["/usr/local/bin/dms-job-runner"], _launcher_env(spec), volumes)],
            "volumes": _pod_volumes(volumes)}}}
    worker = {
        "name": "worker", "replicas": workers,
        "template": {"spec": {
            "restartPolicy": "Never",
            "affinity": _node_affinity(nodes) if nodes else {},
            "containers": [_container("worker", job_image,
                ["/usr/sbin/sshd", "-D"], {}, volumes)],
            "volumes": _pod_volumes(volumes)}}}
    return {
        "apiVersion": "batch.volcano.sh/v1alpha1", "kind": "Job",
        "metadata": {"name": _job_name(spec), "namespace": namespace,
                     "labels": {"dms.io/job-id": spec.job_id,
                                "dms.io/phase": spec.phase, "dms.io/tool": spec.tool}},
        "spec": {"schedulerName": "volcano", "queue": spec.queue,
                 "minAvailable": workers + 1, "priorityClassName": spec.priority_class,
                 "plugins": {"ssh": [], "svc": []},
                 "policies": [{"event": "TaskCompleted", "action": "CompleteJob"},
                              {"event": "PodFailed", "action": "AbortJob"}],
                 "tasks": [launcher, worker]}}
