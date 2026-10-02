"""Volcano 매니페스트 + 도구 명령 빌더. 전부 순수 함수 — 실제 제출은 어댑터(Task 6)."""

import json

from .artifact_base import ARTIFACT_MOUNT

_SCAN_BOOL_FLAGS = {"verbose": "--verbose", "quiet": "--quiet"}
# dscan(1b93d54): --broken-limit은 롱네임뿐(-B는 optstring에 없다). top_k는
# 신버전에서 기능 삭제 — 렌더 맵에 남기면 미지 플래그로 dscan이 usage 종료한다.
_SCAN_VALUE_FLAGS = {"batch_files": "--batch-files", "broken_limit": "--broken-limit"}
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
    if spec.tool == "drm":
        return [*flags, *dry, abs_paths["target"]]
    # 슬라이스 24 §2.1 층2: 여기가 fall-through 였다 -- dscan/dsync/nsync 가 아닌
    # 모든 문자열이 drm 꼴 argv(맨몸 절대경로)를 받았다. 미지 도구는 argv 를
    # 지어내지 않고 던진다. 어댑터의 blanket except 가 submit_failed(detail=도구명)
    # 로 접으므로 조용히 사라지지 않고, 층1(stepper unknown_tool)이 앞서므로
    # 정상 운영에선 여기 도달 자체가 회귀 신호다(설계 §4).
    raise ValueError(f"unknown tool for argv: {spec.tool!r}")


def _auto_chown(spec) -> list[str]:
    """비 root(비특권) 실행의 sync는 목적지를 **실행 신원**(owner_username 이 있으면 그 사용자, 없으면
    요청자)의 uid:LDAP 주 gid 소유로 강제한다(--chown). 포탈 안내 lib/syncOwnership.ts 가 이 규칙의 미러다.

    dsync/nsync는 기본적으로 소스의 소유권을 목적지에 재현(chown)하려 하는데, 도구는
    실행 신원(runuser)으로 실행되므로 소스가 남(예: root) 소유면 목적지를 그 소유자로
    chown할 권한이 없어 메타데이터 적용이 실패한다 -- dsync 는 데이터를 복사한 뒤 잡이 Failed,
    nsync 는 EPERM 을 무시 가능 오류로 보고 소유 변경만 건너뛴 채 Succeeded(포크 nsync.c).
    비 root 실행에는 `--chown <uid>:<gid>`를 주입해 "복사본은 실행 신원 소유"로 만들어
    (실행 신원은 자기 uid·주 gid 로 chown 가능) 이를 없앤다. 특권(root)이면 root가 어떤 소유자로도
    chown 가능하므로 소스 소유권을 그대로 보존한다(개입 안 함). 사용자가 chown 옵션을
    명시했으면 그 값이 우선(중복 주입 안 함). 어느 경우든 dsync/nsync 의 기본 비교(UID·GID·PERM·
    MTIME)가 목적지에 이미 있던 같은 경로 항목의 메타데이터도 이 값(root 면 소스 값)으로 다시 맞춘다."""
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
    # 파드 **안** 경로다(2026-09-09): base 는 execution_volcano._volumes 가 전용
    # hostPath 볼륨으로 ARTIFACT_MOUNT 에 마운트한다(artifact_base.ARTIFACT_MOUNT 주석
    # -- 공용 디렉터리 770 허용의 근거). 호스트 경로(<base>/<job>/<phase>)는 제어면
    # 읽기(execution_volcano.read_summary, api)와 artifact_uri 가 쓰고, 러너는 이
    # 경로로만 쓴다 -- 두 경로는 같은 디렉터리다(같은 hostPath).
    return f"{ARTIFACT_MOUNT}/{spec.job_id}/{spec.phase}"


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


def _apply_ttl(spec, job_spec):
    """ttlSecondsAfterFinished는 activeDeadlineSeconds와 달리 Volcano v1.15.0 CRD의
    Job.spec 허용 필드 목록에 실제로 포함돼 있다(maxRetry/.../ttlSecondsAfterFinished/
    volumes) -- 그래서 task 템플릿(PodSpec)이 아니라 여기 Job.spec에 바로 얹는다.
    값이 없거나(None) 0이면 키 자체를 넣지 않아 기존 매니페스트와 동일하게 유지한다."""
    if spec.ttl_seconds:
        job_spec["ttlSecondsAfterFinished"] = spec.ttl_seconds


def _node_affinity(nodes):
    return {"nodeAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": {
        "nodeSelectorTerms": [{"matchExpressions": [
            {"key": "kubernetes.io/hostname", "operator": "In", "values": nodes}]}]}}}


def _worker_task_metadata(spec, task_name):
    # 자기참조 labelSelector 를 쓰려면 라벨이 파드에 먼저 있어야 한다(설계 §2.4) --
    # volcano task 템플릿에는 지금까지 metadata 자체가 없었다.
    return {"labels": {"dms.io/job-id": spec.job_id, "dms.io/task": task_name}}


def _worker_affinity(spec, task_name, nodes):
    """nodeAffinity(후보 노드 고정)에 required podAntiAffinity(같은 잡·같은 task 산개)를
    병합한다.

    required 로 거는 근거(설계 §2.4): resolve_fanout 이 node_count =
    min(len(candidates), max_nodes)(placement.py)라 레플리카가 후보 노드 수를 절대
    넘지 않는다 -- 산개 불가로 인한 영구 Pending 이 구조적으로 없다. 이것이 없으면
    max_nodes 가 노드가 아니라 레플리카만 제한해 MPI 팬아웃이 한 노드로 붕괴할 수
    있다(원본 설계 §181 위반). 셀렉터를 같은 job 의 같은 task 로 좁히는 이유:
    nsync 의 source/destination 은 별개 task 라 서로 밀어내면 안 되고, 다른 잡의
    워커와도 무관해야 한다."""
    return {**_node_affinity(nodes),
            "podAntiAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": [{
                "labelSelector": {"matchLabels": {
                    "dms.io/job-id": spec.job_id, "dms.io/task": task_name}},
                "topologyKey": "kubernetes.io/hostname"}]}}


# 아티팩트 base 통과 검사(2026-09-30 아티팩트 쓰기 감사). 러너(launcher root)는 base 아래에
# <job>(0755)·<phase>(요청자로 chown)를 만들고, 도구는 **요청자 uid·주 gid 만**(러너가
# /etc/passwd 한 줄만 물질화 -- 보조 그룹 없음)으로 mpi-hostfile·rank.sh 를 읽고 dscan 리포트를
# 쓴다. 전용 마운트(ARTIFACT_MOUNT)는 base 의 **부모** 권한을 건너뛰지만 마운트 루트인 base
# 자체의 x 는 커널이 그대로 본다 -- base 가 root 700/750/770 이면 비 root 잡은 preview/execution
# 에서야 "unable to open the hostfile" 로 죽고, 제어면 3홉 검사(artifact_base)는 root 관점이라
# 못 본다. preflight 파드는 같은 uid·gid(보조 그룹 없음)로 돌므로 여기서 먼저 명확한 사유로
# 거부한다. 경로는 상수라 인라인해도 안전하다(사용자 입력 아님). base 볼륨을 실제로 마운트한
# 파드에만 붙인다(build_preflight_pod) -- 마운트가 없으면 검사할 대상이 없다.
_ARTIFACT_BASE_CHECK = (
    f'test -x {ARTIFACT_MOUNT} || '
    '{ echo DMS_PREFLIGHT_REASON=artifact_base_not_traversable; exit 1; }; ')


# sync 목적지 검사 조각(_preflight_script docstring). $D = 목적지, $M = "user"|"root".
_DEST_TYPE_CHECK = (
    'test ! -e "$D" || test -d "$D" || '
    '{ echo DMS_PREFLIGHT_REASON=destination_not_directory; exit 1; }; ')
_DEST_CHECK = (
    'if [ -e "$D" ]; then '
    '{ test -w "$D" && test -x "$D"; } || '
    '{ echo DMS_PREFLIGHT_REASON=destination_not_writable; exit 1; }; '
    'if [ "$M" != root ] && [ "$(stat -c %u "$D")" != "$(id -u)" ]; then '
    'echo DMS_PREFLIGHT_REASON=destination_not_owned; exit 1; fi; '
    'fi; '
    'dest_parent=$(dirname "$D"); '
    'test -w "$dest_parent" || '
    '{ echo DMS_PREFLIGHT_REASON=destination_parent_not_writable; exit 1; }; ')


def _preflight_script(spec, *, role=None):
    """(script, path_args) — 경로는 positional 파라미터로 넘겨 셸 인젝션을 원천 차단.

    role: nsync(소스/목적지가 disjoint 노드)는 한 노드에서 양쪽을 검사할 수 없다 —
    소스 노드엔 목적지가, 목적지 노드엔 소스가 마운트되지 않기 때문. role="source"는
    소스 읽기만, role="destination"은 목적지(타입 + 부모 쓰기)만 검사한다(각각 해당
    노드에서). role=None(dsync 코로케이션/scan/rm)은 한 파드에서 전부 검사.

    목적지 타입 검사(destination_not_directory)가 부모 쓰기 검사보다 **앞**인 이유
    (실증 d65): 목적지가 기존 일반 파일이면 부모 쓰기 검사는 통과해버려 sync 가
    실행까지 갔고, dsync 가 목적지 파일을 먼저 지운 뒤(`Removing 1 items`) 그
    자리에 디렉토리를 만들지 못해(mkdir errno=2) 실패했다 — 원본은 그대로인데
    목적지 파일만 사라진 순수 데이터 손실이다. 또 목적지가 파일이면 부모는 반드시
    존재하므로, 부모 검사를 먼저 두면 부모가 쓰기 불가일 때 진짜 원인(목적지가
    파일)이 사유에서 사라진다 — 타입을 먼저 봐야 더 정확한 사유가 나온다.
    `test ! -e "$2" || test -d "$2" || { ...; exit 1; }` 는 set -e 아래서도 안전하다:
    AND-OR 목록의 좌변 실패는 errexit 대상이 아니고, 전부 실패했을 때만 마지막
    블록이 명시적으로 exit 1 한다(기존 마커 관용구와 동일한 형태).

    목적지 권한 검사(2026-09-30 프로덕션 사고, _DEST_CHECK): 예전엔 목적지가 이미 있어도
    **부모**의 쓰기만 봐서, 부모가 쓰기 가능하면 남의 목적지도 통과시켜 실행 단계에서야
    실패했다. 이제 목적지가 있으면 **목적지 자체**를 먼저 본다: 쓰기·진입(-w -x) 불가면
    destination_not_writable, 비특권 실행이면 소유자가 실행 uid 인지까지(아니면
    destination_not_owned). 소유를 요구하는 이유(실측): dsync 는 소스 최상위의 권한·시각
    (특권이면 소유까지)을 **기존 목적지 디렉터리에 적용**한다 -- 남 소유면 비특권은
    chmod()/utime() EPERM 으로 데이터를 다 복사한 뒤 Failed(부분 복사)가 되고, root 는
    남의 디렉터리를 소스 소유로 덮어쓴다.
    부모 쓰기는 목적지가 **있어도** 본다(순서상 목적지 자체 다음): dsync(mpifileutils
    포크 dsync.c "Destination parent directory is not writable")가 목적지 존재와 무관하게
    부모 W_OK 를 요구하고, 실패하면 아무것도 복사하지 않은 채 **종료 코드 0** 으로 끝난다
    (rc 초기값 0 인 채 goto ERROR; dry-run 에선 경고만이라 미리보기는 멀쩡해 보인다).
    목적지가 있다고 부모 검사를 건너뛰면 "root 755 부모 아래 본인 디렉터리" sync 가 복사
    0건 Succeeded 로 끝난다(2026-09-30 d139 실증에서 잡음 -- 그 판에서 한때 건너뛰었다).
    실행 모드("user"/"root")는 경로와 같이 positional 로 넘긴다(셸 인젝션 차단 관례)."""
    ap = _abs_paths(spec)
    mode = "root" if (spec.identity or {}).get("privileged") else "user"
    if spec.operation == "sync":
        if role == "source":
            script = ('set -e; '
                      'test -r "$1" || { echo DMS_PREFLIGHT_REASON=source_not_readable; exit 1; }; '
                      'echo DMS_PREFLIGHT_OK')
            return script, [ap["source"]]
        if role == "destination":
            script = ('set -e; D="$1"; M="$2"; ' + _DEST_TYPE_CHECK + _DEST_CHECK
                      + 'echo DMS_PREFLIGHT_OK')
            return script, [ap["destination"], mode]
        script = ('set -e; D="$2"; M="$3"; '
                  'test -r "$1" || { echo DMS_PREFLIGHT_REASON=source_not_readable; exit 1; }; '
                  + _DEST_TYPE_CHECK + _DEST_CHECK + 'echo DMS_PREFLIGHT_OK')
        return script, [ap["source"], ap["destination"], mode]
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


PREFLIGHT_REASON_MARKER = "DMS_PREFLIGHT_REASON="
# _preflight_script 가 낼 수 있는 사유의 **전수**. 스테퍼는 이 집합 밖 문자열을
# 잡 reason_code 로 쓰지 않는다 — 파드 로그는 신뢰 입력이 아니고(설계 §4), 매핑
# 없는 코드를 박으면 포탈이 원문 코드를 그대로 노출한다. 화이트리스트를 스크립트와
# 같은 파일에 두는 이유: 새 검사를 추가하면서 등록을 빠뜨리는 드리프트를 한 화면에서
# 막는다(계약 테스트가 스크립트 실물에서 마커를 추출해 이 집합과 대조한다). 여기에
# 코드를 추가하면 frontend/src/lib/reasonCodes.json 과 api.ts REASON_MESSAGES 도
# 같이 갱신해야 한다(양방향 계약 테스트).
PREFLIGHT_REASONS = frozenset({
    "source_not_readable", "destination_not_directory",
    "destination_parent_not_writable", "destination_not_writable",
    "destination_not_owned", "parent_not_writable",
    "target_not_readable", "artifact_base_not_traversable"})


# 실행·미리보기(mpirun 을 돌리는 launcher) 실패의 사유 마커(2026-10-02). 러너(dms_job_runner.runner)가 워커
# 준비(IP 해석 + ssh)를 제한 시간 안에 못 끝내면 mpirun 없이 이 마커를 찍고 끝낸다 -- 예전엔 이름이 남은
# hostfile 로 mpirun 이 돌아 "Could not resolve hostname" rc 255 = 사유 없는 execution_failed 였다. 문법은
# preflight 마커와 같고(한 줄 접두), 승격은 화이트리스트로만(파드 로그는 신뢰 입력이 아니다). 러너는 dms 를
# import 하지 않아 같은 값을 따로 정의한다 -- tests/test_job_runner_runner.py 의 계약 테스트가 둘을 잇는다.
# 여기에 코드를 추가하면 frontend/src/lib/reasonCodes.json 과 api.ts REASON_MESSAGES 도 함께.
EXECUTION_REASON_MARKER = "DMS_EXEC_REASON="
EXECUTION_REASONS = frozenset({"workers_unreachable"})


def _parse_marker(entries, marker, allowed):
    for entry in entries or ():
        log = entry[1]
        if log is None:
            continue
        for line in log.split("\n"):
            line = line.strip()
            if not line.startswith(marker):
                continue
            value = line[len(marker):].strip()
            if value in allowed:
                return value
    return None


def parse_execution_reason(entries):
    """launcher(와 실패 워커) 로그에서 실행 실패 사유를 뽑는다 -- 화이트리스트 밖이면 None
    (parse_preflight_reason 과 같은 계약: entries = read_log 의 [(pod, log|None, waiting_reason)])."""
    return _parse_marker(entries, EXECUTION_REASON_MARKER, EXECUTION_REASONS)


def parse_preflight_reason(entries):
    """preflight 파드 로그에서 사유 코드를 뽑는다 — 화이트리스트 밖이면 None.

    entries 는 어댑터 read_log 계약 그대로 [(pod, log|None, waiting_reason)] 다.
    로그 형식이 아니라 마커 관례 한 줄만 본다(build_watcher._marker_value 와 같은
    문법이라 운영자가 하나만 알면 된다). nsync 는 소스·목적지 파드가 따로라 항목이
    여럿이고 한쪽만 실패하는 것이 정상 경로다 — 전부 훑어 첫 유효 마커를 채택한다.
    log=None("얻을 수 없었다")과 ""(정상 빈 로그)는 여기선 똑같이 "마커 없음"이라
    None 을 돌려준다 — 사유를 지어내지 않고 호출자의 폴백으로 접힌다."""
    return _parse_marker(entries, PREFLIGHT_REASON_MARKER, PREFLIGHT_REASONS)


_PREFLIGHT_ROLE_SEG = {"source": "-src", "destination": "-dst"}


def build_preflight_pod(spec, *, job_image, namespace, volumes, node, role=None):
    ident = spec.identity or {}
    script, path_args = _preflight_script(spec, role=role)
    # 아티팩트 base 통과(_ARTIFACT_BASE_CHECK 주석)를 경로 검사보다 **먼저** -- base 가 막혀
    # 있으면 경로를 고쳐도 모든 비 root 잡이 죽는다(운영 설정 문제를 먼저 드러낸다).
    if any(v.get("mountPath") == ARTIFACT_MOUNT for v in volumes):
        script = _ARTIFACT_BASE_CHECK + script
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
        "template": {
            "metadata": _worker_task_metadata(spec, "source-worker"),
            "spec": {"restartPolicy": "Never",
                "affinity": _worker_affinity(spec, "source-worker", src_nodes),
                "containers": [_worker_container("source-worker", job_image, spec, volumes)],
                "volumes": _pod_volumes(volumes)}}}
    dst_worker = {"name": "destination-worker", "replicas": len(dst_nodes),
        "template": {
            "metadata": _worker_task_metadata(spec, "destination-worker"),
            "spec": {"restartPolicy": "Never",
                "affinity": _worker_affinity(spec, "destination-worker", dst_nodes),
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
    _apply_ttl(spec, job_spec)
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
        "template": {
            "metadata": _worker_task_metadata(spec, "worker"),
            "spec": {
                "restartPolicy": "Never",
                # nodes 가 비면(개발 스텁 경로) 산개할 대상이 없다 -- 기존과 같이 빈 dict
                "affinity": _worker_affinity(spec, "worker", nodes) if nodes else {},
                "containers": [_worker_container("worker", job_image, spec, volumes)],
                "volumes": _pod_volumes(volumes)}}}
    job_spec = {"schedulerName": "volcano", "queue": spec.queue,
                "minAvailable": workers + 1, "priorityClassName": spec.priority_class,
                "plugins": {"ssh": [], "svc": []},
                "policies": [{"event": "TaskCompleted", "action": "CompleteJob"},
                             {"event": "PodFailed", "action": "AbortJob"}],
                "tasks": [launcher, worker]}
    _apply_task_deadlines(spec, job_spec["tasks"])
    _apply_ttl(spec, job_spec)
    return {
        "apiVersion": "batch.volcano.sh/v1alpha1", "kind": "Job",
        "metadata": {"name": _job_name(spec), "namespace": namespace,
                     "labels": {"dms.io/job-id": spec.job_id,
                                "dms.io/phase": spec.phase, "dms.io/tool": spec.tool}},
        "spec": job_spec}
