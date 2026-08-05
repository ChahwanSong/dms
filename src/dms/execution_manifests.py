"""Volcano 매니페스트 + 도구 명령 빌더. 전부 순수 함수 — 실제 제출은 어댑터(Task 6)."""

import json

_SCAN_BOOL_FLAGS = {"verbose": "--verbose", "quiet": "--quiet"}
_SCAN_VALUE_FLAGS = {"top_k": "--top-k"}
_SYNC_BOOL_FLAGS = {"delete": "--delete", "contents": "--contents",
                    "direct": "--direct", "open_noatime": "--open-noatime",
                    "quiet": "--quiet"}
_SYNC_VALUE_FLAGS = {"batch_files": "--batch-files", "bufsize": "--bufsize",
                     "chmod": "--chmod", "chown": "--chown"}
_RM_BOOL_FLAGS = {"stat": "--stat", "lite": "--lite", "quiet": "--quiet"}


def _render(options, bool_flags, value_flags):
    flags: list[str] = []
    for key, flag in bool_flags.items():
        if options.get(key) is True:
            flags.append(flag)
    for key, flag in value_flags.items():
        if key in options:
            flags.extend([flag, str(options[key])])
    return flags


def render_tool_flags(tool: str, options: dict) -> list[str]:
    options = options or {}
    if tool == "dscan":
        return _render(options, _SCAN_BOOL_FLAGS, _SCAN_VALUE_FLAGS)
    if tool in ("dsync", "nsync"):
        return _render(options, _SYNC_BOOL_FLAGS, _SYNC_VALUE_FLAGS)
    if tool == "drm":
        return _render(options, _RM_BOOL_FLAGS, {})
    return []


def tool_argv(spec, *, abs_paths: dict) -> list[str]:
    if spec.tool == "dscan":
        opts = spec.options or {}
        flags = render_tool_flags("dscan", opts)
        # --output(JSON 리포트)는 항상 필요. --print(rank0 사람용 요약)는 기본이되
        # quiet면 생략한다 — --quiet와 상충하기 때문.
        tail = ["--output", "$DMS_SCAN_REPORT"]
        if opts.get("quiet") is not True:
            tail.append("--print")
        return ["--directory", abs_paths["target"], *flags, *tail]
    flags = render_tool_flags(spec.tool, spec.options)
    dry = ["--dryrun"] if spec.dryrun else []
    if spec.tool in ("dsync", "nsync"):
        return [*flags, *_auto_chown(spec), *dry,
                abs_paths["source"], abs_paths["destination"]]
    # drm
    return [*flags, *dry, abs_paths["target"]]


def _auto_chown(spec) -> list[str]:
    """비특권 요청자의 sync는 목적지를 요청자(uid:gid) 소유로 강제한다(--chown).

    dsync/nsync는 기본적으로 소스의 소유권을 목적지에 재현(chown)하려 하는데, 도구는
    요청자 신원(runuser)으로 실행되므로 소스가 남(예: root) 소유면 목적지를 그 소유자로
    chown할 권한이 없어 메타데이터 적용이 실패한다(데이터는 복사되지만 잡은 Failed).
    비특권 요청자에겐 `--chown <uid>:<gid>`를 주입해 "복사본은 요청자 소유"로 만들어
    (요청자는 자기 소유로 chown 가능) 실패를 없앤다. 특권(root)이면 root가 어떤 소유자로도
    chown 가능하므로 소스 소유권을 그대로 보존한다(개입 안 함). 사용자가 chown 옵션을
    명시했으면 그 값이 우선(중복 주입 안 함)."""
    ident = spec.identity or {}
    if ident.get("privileged") or "chown" in (spec.options or {}):
        return []
    return ["--chown", f"{ident.get('uid', 0)}:{ident.get('gid', 0)}"]


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


def _apply_task_deadlines(spec, tasks):
    """타임아웃은 각 task의 파드 템플릿(PodSpec)에 건다 — Volcano Job의 spec이 아니라.

    Volcano v1.15.0 CRD의 Job.spec 프로퍼티는 maxRetry/minAvailable/minSuccess/
    networkTopology/plugins/policies/priorityClassName/queue/runningEstimate/
    schedulerName/tasks/ttlSecondsAfterFinished/volumes 뿐이고
    x-kubernetes-preserve-unknown-fields도 없다. 즉 spec.activeDeadlineSeconds는
    API 서버가 조용히 prune한다 — create는 200으로 성공하고 데드라인만 사라져
    타임아웃이 영원히 발화하지 않는다. activeDeadlineSeconds는 실제 PodSpec 필드이므로
    tasks[i].template.spec에 두어야 prune를 견디고 kubelet이 집행한다."""
    if not spec.timeout_seconds:
        return
    for task in tasks:
        task["template"]["spec"]["activeDeadlineSeconds"] = spec.timeout_seconds


def _node_affinity(nodes):
    return {"nodeAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": {
        "nodeSelectorTerms": [{"matchExpressions": [
            {"key": "kubernetes.io/hostname", "operator": "In", "values": nodes}]}]}}}


def _preflight_script(spec, *, role=None):
    """(script, path_args) — 경로는 positional 파라미터로 넘겨 셸 인젝션을 원천 차단.

    role: nsync(소스/목적지가 disjoint 노드)는 한 노드에서 양쪽을 검사할 수 없다 —
    소스 노드엔 목적지가, 목적지 노드엔 소스가 마운트되지 않기 때문. role="source"는
    소스 읽기만, role="destination"은 목적지 부모 쓰기만 검사한다(각각 해당 노드에서).
    role=None(dsync 코로케이션/scan/rm)은 한 파드에서 전부 검사."""
    ap = _abs_paths(spec)
    if spec.operation == "sync":
        if role == "source":
            script = ('set -e; '
                      'test -r "$1" || { echo DMS_PREFLIGHT_REASON=source_not_readable; exit 1; }; '
                      'echo DMS_PREFLIGHT_OK')
            return script, [ap["source"]]
        if role == "destination":
            script = ('set -e; '
                      'dest_parent=$(dirname "$1"); '
                      'test -w "$dest_parent" || { echo DMS_PREFLIGHT_REASON=destination_parent_not_writable; exit 1; }; '
                      'echo DMS_PREFLIGHT_OK')
            return script, [ap["destination"]]
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


_PREFLIGHT_ROLE_SEG = {"source": "-src", "destination": "-dst"}


def build_preflight_pod(spec, *, job_image, namespace, volumes, node, role=None):
    ident = spec.identity or {}
    script, path_args = _preflight_script(spec, role=role)
    role_seg = _PREFLIGHT_ROLE_SEG.get(role, "")
    pod_spec = {"restartPolicy": "Never",
                "nodeSelector": {"kubernetes.io/hostname": node},
                "containers": [{
                    "name": "preflight", "image": job_image,
                    "command": ["sh", "-c", script, "sh", *path_args],
                    "securityContext": {"runAsUser": ident.get("uid", 0),
                                        "runAsGroup": ident.get("gid", 0)},
                    "volumeMounts": [{"name": v["name"], "mountPath": v["mountPath"],
                                      "mountPropagation": "HostToContainer"}
                                     for v in volumes]}],
                "volumes": _pod_volumes(volumes)}
    if spec.timeout_seconds:
        pod_spec["activeDeadlineSeconds"] = spec.timeout_seconds
    return {
        "apiVersion": "v1", "kind": "Pod",
        # phase(+role) in the name: one job can run multiple preflight Pods -- the
        # initial (phase "preflight") and the post-confirm re-validation (phase
        # "exec_preflight"), and for nsync EACH of those splits into a source-node
        # and a destination-node Pod (role src/dst). Same job_id[:12]+node would
        # collide (create -> AlreadyExists), so scope the name by phase and role.
        # Underscores are illegal in a Pod name (DNS-1123) -> "exec_preflight"
        # must become "exec-preflight" or the create is rejected (submit_failed).
        "metadata": {"name": (f"dms-preflight-{spec.job_id[:12]}-"
                              f"{spec.phase.replace('_', '-')}{role_seg}-{node}")[:63],
                     "namespace": namespace,
                     "labels": {"dms.io/job-id": spec.job_id,
                                "dms.io/phase": spec.phase}},
        "spec": pod_spec}


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
    job_spec = {"schedulerName": "volcano", "queue": spec.queue,
                "minAvailable": len(src_nodes) + len(dst_nodes) + 1,
                "priorityClassName": spec.priority_class,
                "plugins": {"ssh": [], "svc": []},
                "policies": [{"event": "TaskCompleted", "action": "CompleteJob"},
                             {"event": "PodFailed", "action": "AbortJob"}],
                "tasks": [launcher, src_worker, dst_worker]}
    _apply_task_deadlines(spec, job_spec["tasks"])
    return {
        "apiVersion": "batch.volcano.sh/v1alpha1", "kind": "Job",
        "metadata": {"name": _job_name(spec), "namespace": namespace,
                     "labels": {"dms.io/job-id": spec.job_id,
                                "dms.io/phase": spec.phase, "dms.io/tool": spec.tool}},
        "spec": job_spec}


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
    job_spec = {"schedulerName": "volcano", "queue": spec.queue,
                "minAvailable": workers + 1, "priorityClassName": spec.priority_class,
                "plugins": {"ssh": [], "svc": []},
                "policies": [{"event": "TaskCompleted", "action": "CompleteJob"},
                             {"event": "PodFailed", "action": "AbortJob"}],
                "tasks": [launcher, worker]}
    _apply_task_deadlines(spec, job_spec["tasks"])
    return {
        "apiVersion": "batch.volcano.sh/v1alpha1", "kind": "Job",
        "metadata": {"name": _job_name(spec), "namespace": namespace,
                     "labels": {"dms.io/job-id": spec.job_id,
                                "dms.io/phase": spec.phase, "dms.io/tool": spec.tool}},
        "spec": job_spec}
