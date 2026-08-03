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


def _container(name, image, command, env, volumes, *, security_context=None):
    return {
        "name": name, "image": image, "command": command,
        "securityContext": security_context or {"runAsUser": 0},
        "env": [{"name": k, "value": v} for k, v in env.items()],
        "volumeMounts": [{"name": v["name"], "mountPath": v["mountPath"],
                          "mountPropagation": "HostToContainer"} for v in volumes],
    }


def _identity_materialize_stmt():
    """root가 요청자를 컨테이너 /etc/passwd에 idempotent 물질화. legacy
    _identity_materialize_stmt() 이식 — 값은 case-guard로만 검증하고 전부
    ${DMS_JR_*} 환경변수로 참조한다(f-string 보간 금지, 셸 인젝션 방지)."""
    return (
        'if [ "$(id -u)" = 0 ] && [ -n "${DMS_JR_USERNAME:-}" ] && [ -n "${DMS_JR_UID:-}" ] '
        '&& ! getent passwd "$DMS_JR_USERNAME" >/dev/null 2>&1; then '
        'case "$DMS_JR_UID" in ""|*[!0-9]*) echo "dms: invalid DMS_JR_UID" >&2; exit 1;; esac; '
        'case "${DMS_JR_GID:-$DMS_JR_UID}" in ""|*[!0-9]*) echo "dms: invalid DMS_JR_GID" >&2; exit 1;; esac; '
        'case "$DMS_JR_USERNAME" in *[!A-Za-z0-9._-]*) echo "dms: invalid DMS_JR_USERNAME" >&2; exit 1;; esac; '
        "printf '%s:x:%s:%s::/tmp/dms-home-%s:/bin/sh\\n' \"$DMS_JR_USERNAME\" \"$DMS_JR_UID\" "
        '"${DMS_JR_GID:-$DMS_JR_UID}" "$DMS_JR_UID" >> /etc/passwd; '
        'mkdir -p "/tmp/dms-home-$DMS_JR_UID" && '
        'chown "$DMS_JR_UID:${DMS_JR_GID:-$DMS_JR_UID}" "/tmp/dms-home-$DMS_JR_UID" 2>/dev/null || true; '
        "fi"
    )


def _worker_command_script():
    """worker(sshd) 컨테이너 command 본문. legacy _mpi_worker_command() 이식:
    물질화 -> ssh-keygen -A -> 요청자 home ~/.ssh에 authorized_keys 복사/chown ->
    exec sshd. StrictModes=no(온디맨드 물질화 계정), UsePAM=no(shadow 엔트리 없음)."""
    return "\n".join([
        "set -eu",
        _identity_materialize_stmt(),
        "mkdir -p /run/sshd",
        "ssh-keygen -A >/dev/null 2>&1 || true",
        'if [ -n "${DMS_JR_USERNAME:-}" ] && id "$DMS_JR_USERNAME" >/dev/null 2>&1; then',
        "  user_home=$(getent passwd \"$DMS_JR_USERNAME\" | awk -F: '{print $6}')",
        # root는 스킵: Volcano ssh 플러그인이 이미 /root/.ssh에 읽기전용으로 키를 마운트.
        '  if [ -n "$user_home" ] && [ "$user_home" != /root ]; then',
        '    mkdir -p "$user_home/.ssh"',
        '    chown "$DMS_JR_USERNAME" "$user_home" 2>/dev/null || true',
        '    if [ -f /root/.ssh/authorized_keys ]; then '
        'cp /root/.ssh/authorized_keys "$user_home/.ssh/authorized_keys"; fi',
        '    chown -R "$DMS_JR_USERNAME" "$user_home/.ssh"',
        '    chmod 0700 "$user_home/.ssh"',
        '    chmod 0600 "$user_home/.ssh"/* 2>/dev/null || true',
        "  fi",
        "fi",
        # UsePAM=no: 물질화된 요청자는 /etc/shadow 엔트리가 없어 PAM account 단계가
        # 거부한다. StrictModes=no: 온디맨드로 생성된 home/.ssh의 권한을 sshd가
        # 지나치게 깐깐하게 검사하지 않도록.
        "exec /usr/sbin/sshd -D -e -o StrictModes=no -o UsePAM=no",
    ])


def _worker_security_context():
    return {"runAsUser": 0, "capabilities": {"add": ["SYS_CHROOT"]}}


def _processes_per_node(spec):
    if "primary" in spec.candidates:
        node_count = max(1, len(spec.candidates.get("primary", [])))
    else:
        node_count = max(1, len(spec.candidates.get("source", []))
                         + len(spec.candidates.get("destination", [])))
    return max(1, spec.process_count // node_count)


def _worker_env(spec):
    ident = spec.identity or {}
    return {
        "DMS_JR_UID": str(ident.get("uid", 0)), "DMS_JR_GID": str(ident.get("gid", 0)),
        "DMS_JR_USERNAME": ident.get("username", "root"),
        "DMS_JR_PROCESSES_PER_NODE": str(_processes_per_node(spec)),
    }


def _worker_container(name, image, spec, volumes):
    return _container(name, image, ["sh", "-c", _worker_command_script()],
                      _worker_env(spec), volumes,
                      security_context=_worker_security_context())


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
        "DMS_JR_PROCESSES_PER_NODE": str(_processes_per_node(spec)),
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
            "containers": [_worker_container("source-worker", job_image, spec, volumes)],
            "volumes": _pod_volumes(volumes)}}}
    dst_worker = {"name": "destination-worker", "replicas": len(dst_nodes),
        "template": {"spec": {"restartPolicy": "Never",
            "affinity": _node_affinity(dst_nodes),
            "containers": [_worker_container("destination-worker", job_image, spec, volumes)],
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
            "containers": [_worker_container("worker", job_image, spec, volumes)],
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
