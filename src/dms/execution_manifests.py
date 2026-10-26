"""Volcano 매니페스트 + 도구 명령 빌더. 전부 순수 함수 — 실제 제출은 어댑터(Task 6)."""

import json
import re

from .artifact_base import ARTIFACT_MOUNT
from .node_cpu import LAUNCHER_CPU_REQUEST_MILLICORES

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
    if spec.dryrun:
        # 미리보기에는 --quiet 를 싣지 않는다(2026-10-12): 세 도구 모두 -q 가 로그 수준을 NONE 으로 내려
        # 미리보기 개수의 유일한 출처를 지운다 -- drm·dsync 는 walk 요약("Walked N items in X seconds",
        # mfu_flist_walk.c 가 VERBOSE 일 때만), nsync 는 "Planned actions:"(MFU_LOG_INFO). 그러면 컨펌 창이 늘
        # 「삭제·복사 대상 모름」이고 지문(요약 해시)도 상수가 된다. quiet 는 로그 양만 바꾸고 무엇을 지우거나
        # 복사하는지는 바꾸지 않으므로, 사용자가 고른 quiet 는 실행 단계에 그대로 적용된다.
        flags = [f for f in flags if f != "--quiet"]
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


_OWNER_RE = re.compile(r"^([0-9]{1,10}):([0-9]{1,10})$")
_OWNER_ID_MAX = 2**32 - 2      # uid/gid 상한(4294967295 = (uid_t)-1 은 「바꾸지 않음」 뜻이라 못 쓴다)


def valid_owner(value) -> bool:
    """결과 소유자 "uid:gid"(숫자 둘) 형식인가 — 파드 로그·DB 행 어느 쪽에서 왔든 같은 검사(신뢰 경계)."""
    if not isinstance(value, str):
        return False
    m = _OWNER_RE.match(value)
    return m is not None and int(m.group(1)) <= _OWNER_ID_MAX and int(m.group(2)) <= _OWNER_ID_MAX


def pv_owner_measured(spec) -> bool:
    """목적지 PV 루트 소유자로 결과를 맞추는 잡인가(2026-10-26 사용자 결정 「root sync 의 결과 소유권 — 목적지 PV
    소유자로 자동 chown」): sync ∧ root ∧ 목적지가 PV 주소(stepper 가 paths["contain"]["destination"] 을 실었다) ∧ 명시
    chown 없음. preflight(측정 마커)·미리보기·실행(--chown)이 이 술어 하나를 쓴다 — stepper._pv_owner_required 가 요청
    payload 로 같은 판정을 한다(destination_pv 블록 = contain 의 destination 키)."""
    if spec.operation != "sync" or not (spec.identity or {}).get("privileged"):
        return False
    if "chown" in (spec.options or {}):
        return False
    contain = (spec.paths or {}).get("contain")
    return isinstance(contain, dict) and "destination" in contain


def _auto_chown(spec) -> list[str]:
    """비 root(비특권) 실행의 sync는 목적지를 **실행 신원**(스냅숏의 uid:gid) 소유로 강제한다(--chown). dms-k8s 는 모든 잡이
    root 라(설계 D2) stepper 는 비 root 신원을 제출 전에 끊는다(2026-10-26, stepper.identity_problem) -- 이 갈래는 빌더를
    직접 부르는 경로(테스트)에서만 닿는 방어 기본값이다(더 좁은 권한). 포탈 안내
    lib/syncOwnership.ts 가 이 규칙의 미러다.

    dsync/nsync는 기본적으로 소스의 소유권을 목적지에 재현(chown)하려 하는데, 도구는
    실행 신원(runuser)으로 실행되므로 소스가 남(예: root) 소유면 목적지를 그 소유자로
    chown할 권한이 없어 메타데이터 적용이 실패한다 -- dsync 는 데이터를 복사한 뒤 잡이 Failed,
    nsync 는 EPERM 을 무시 가능 오류로 보고 소유 변경만 건너뛴 채 Succeeded(포크 nsync.c).
    비 root 실행에는 `--chown <uid>:<gid>`를 주입해 "복사본은 실행 신원 소유"로 만들어
    (실행 신원은 자기 uid·주 gid 로 chown 가능) 이를 없앤다. 특권(root)이면 root가 어떤 소유자로도
    chown 가능하므로 소스 소유권을 그대로 보존한다(개입 안 함). 사용자가 chown 옵션을
    명시했으면 그 값이 우선(중복 주입 안 함). 어느 경우든 dsync/nsync 의 기본 비교(UID·GID·PERM·
    MTIME)가 목적지에 이미 있던 같은 경로 항목의 메타데이터도 이 값(root 면 소스 값)으로 다시 맞춘다.

    PV 목적지(2026-10-26 사용자 결정): root sync 라도 목적지가 PV 주소면 결과를 **목적지 PV 루트 소유자**로 맞춘다 —
    소스 소유를 그대로 두면 다른 namespace 파드(그 PV 의 uid/fsGroup)가 결과를 못 읽거나 못 고친다. 값은 목적지 쪽
    preflight 가 봉쇄 확인 뒤 잰 PV 루트의 uid:gid(spec.result_owner)다. 우선순위: 명시 chown > PV 루트 소유자 > root 면
    개입 없음 > 비 root 면 실행 신원. 그 값이 없거나 형식이 틀리면 ValueError — 소스 소유 보존으로 조용히 접지 않는다
    (어댑터가 submit_failed 로 접는다, fail-closed). nsync 는 이 값을 소스 쪽 스캔 기록에 덮어써 비교·적용하므로 소스·목적지
    rank 가 같은 argv 를 받는 이 방식이어야 한다(rank 마다 따로 재는 방식은 소스 rank 가 목적지를 못 본다)."""
    ident = spec.identity or {}
    if "chown" in (spec.options or {}):
        return []
    if pv_owner_measured(spec):
        if not valid_owner(spec.result_owner):
            raise ValueError(f"PV destination sync without a measured owner: {spec.result_owner!r}")
        return ["--chown", spec.result_owner]
    if ident.get("privileged"):
        return []
    return ["--chown", f"{ident.get('uid', 0)}:{ident.get('gid', 0)}"]


def _worker_count(spec) -> int:
    return max(1, len(spec.candidates.get("primary", [])))


def _abs_paths(spec):
    # paths는 상대. 절대경로는 stepper가 spec.paths에 이미 절대로 넣어준다(Task 7).
    # 여기선 spec.paths를 그대로 절대로 취급.
    return spec.paths


def _container(name, image, command, env, volumes, *, security_context=None, resources=None):
    c = {
        "name": name, "image": image, "command": command,
        "securityContext": security_context or {"runAsUser": 0},
        "env": [{"name": k, "value": v} for k, v in env.items()],
        "volumeMounts": [{"name": v["name"], "mountPath": v["mountPath"],
                          "mountPropagation": "HostToContainer"} for v in volumes],
    }
    # resources 는 워커·launcher 만 싣는다(아래 _worker_resources·_launcher_resources). preflight 맨 Pod 는 키 자체가 없다
    # (BestEffort) -- 워커가 코어를 다 예약한 노드에서도 다음 잡의 점검이 서야 한다(activeDeadline 은 바인딩 뒤부터라
    # 스케줄 못 된 점검은 영원히 기다린다).
    if resources is not None:
        c["resources"] = resources
    return c


def _identity_materialize_stmt(passwd="/etc/passwd", home="/tmp/dms-home-"):
    """root가 실행 신원을 컨테이너 /etc/passwd 에 idempotent 물질화. legacy _identity_materialize_stmt() 이식 — 값은
    case-guard로만 검증하고 전부 ${DMS_JR_*} 환경변수로 참조한다(f-string 보간 금지, 셸 인젝션 방지). 인자(passwd·home)는
    경로 **상수**다 -- 실 sh 테스트가 임시 파일로 바꿔 끼우는 자리일 뿐 신원 값은 절대 들어오지 않는다(스크립트는 신원과
    무관한 고정 문자열).

    dms-k8s 는 모든 잡이 root 라(설계 D2) 보통 uid 0·gid 0 의 **별칭 계정**(실행 신원 이름 → 0:0)이다 -- 러너의 runuser·
    워커 sshd 가 그 이름으로 로그인하므로 LDAP 이 없어도 이 물질화는 필요하다(2026-10-26 LDAP 코드 삭제에서 남긴 이유).
      (1) 계정이 이미 있으면(사이트 커스텀 이미지·이미지 계정명 충돌) getent 의 uid·gid 가 둘 다 DMS_JR_UID/GID 와
          같아야 한다 -- 예전엔 조용히 건너뛰어 sshd 가 **이미지 계정의 신원**으로 rank 를 돌렸다(잠복 결함).
      (2) passwd 줄은 없을 때만.
    계정 충돌은 새 사유 없이 stderr 진단 + exit 1. (예전 LDAP 보조 그룹의 /etc/group dmsg<gid> 줄·자기 검증은 2026-10-26
    에 지웠다 -- 잡 이미지·러너는 그대로다.)"""
    return (
        'if [ "$(id -u)" = 0 ] && [ -n "${DMS_JR_USERNAME:-}" ] && [ -n "${DMS_JR_UID:-}" ]; then '
        'case "$DMS_JR_UID" in ""|*[!0-9]*) echo "dms: invalid DMS_JR_UID" >&2; exit 1;; esac; '
        'case "${DMS_JR_GID:-$DMS_JR_UID}" in ""|*[!0-9]*) echo "dms: invalid DMS_JR_GID" >&2; exit 1;; esac; '
        'case "$DMS_JR_USERNAME" in *[!A-Za-z0-9._-]*) echo "dms: invalid DMS_JR_USERNAME" >&2; exit 1;; esac; '
        'dms_gid="${DMS_JR_GID:-$DMS_JR_UID}"; '
        # (1) 기존 계정은 uid·gid 둘 다 일치해야 / (2) passwd 는 없을 때만
        'if dms_pw=$(getent passwd "$DMS_JR_USERNAME"); then '
        '[ "$(printf %s "$dms_pw" | cut -d: -f3)" = "$DMS_JR_UID" ] && '
        '[ "$(printf %s "$dms_pw" | cut -d: -f4)" = "$dms_gid" ] || '
        '{ echo "dms: account $DMS_JR_USERNAME exists with a different uid/gid" >&2; exit 1; }; '
        'else '
        f"printf '%s:x:%s:%s::{home}%s:/bin/sh\\n' \"$DMS_JR_USERNAME\" \"$DMS_JR_UID\" "
        f'"$dms_gid" "$DMS_JR_UID" >> {passwd}; '
        f'mkdir -p "{home}$DMS_JR_UID" && '
        f'chown "$DMS_JR_UID:$dms_gid" "{home}$DMS_JR_UID" 2>/dev/null || true; '
        'fi; '
        'fi'
    )


def _worker_command_script():
    """worker(sshd) 컨테이너 command 본문. legacy _mpi_worker_command() 이식:
    물질화 -> ssh-keygen -A -> 요청자 home ~/.ssh에 authorized_keys 복사/chown ->
    exec sshd. StrictModes=no(온디맨드 물질화 계정), UsePAM=no(shadow 엔트리 없음).
    물질화는 계정 하나(_identity_materialize_stmt docstring) -- 신원은 env 로만 들어오므로 이 스크립트는 잡과 무관한
    고정 문자열이다."""
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


# 워커 한 파드가 돌리는 rank 수의 위생 상한(2026-10-16). domain._validate_batch_overrides 의 1024 와 같은 값 -- 설정을
# 보지 않는 하드 상수다. 실제 「노드에 들어가는가」는 이 상한이 아니라 노드 CPU 상한이 정한다
# (placement·stepper 가 node_cpu 로 줄인다).
MAX_PROCESSES_PER_WORKER = 1024


def _launcher_resources():
    """launcher 는 rank 를 돌리지 않아 작게 요청한다(node_cpu.LAUNCHER_CPU_REQUEST_MILLICORES -- 노드 CPU 상한 계산과 같은
    원천). limits 없음. 매번 새 dict(매니페스트끼리 공유하지 않는다)."""
    return {"requests": {"cpu": f"{LAUNCHER_CPU_REQUEST_MILLICORES}m"}}


def worker_pod_count(candidates) -> int:
    """워커 파드 수 -- primary 는 후보 수, nsync 는 출발 + 목적지(같은 노드가 양면이면 그 노드에 워커가 둘)."""
    if "primary" in candidates:
        return max(1, len(candidates.get("primary", [])))
    return max(1, len(candidates.get("source", [])) + len(candidates.get("destination", [])))


def processes_per_node(process_count, candidates) -> int:
    """워커 파드 하나의 rank 수 -- hostfile slots(DMS_JR_PROCESSES_PER_NODE)·워커 CPU 요청의 **단일 원천**(stepper 의 노드
    CPU 재클램프도 이 함수로 현재 값을 읽는다). worker_pool 은 DB 신뢰 경계 밖이라 다시 본다: process_count 가 int(bool
    제외)·≥ 1 이 아니거나 결과가 MAX_PROCESSES_PER_WORKER 를 넘으면 ValueError(어댑터가 submit_failed 로 접는다). 예전엔
    0·음수를 1 로 조용히 접고, True 를 1 로 통과시키고, 실수 8.0 은 "8.0" 으로 러너 int() 에서 터졌다 -- 10**9 는 rank
    폭주(지금은 CPU 요청이라 영구 Pending). 나누어떨어지지 않는 값(옛 행·변조)은 내림이다(기존 동작 -- -np 가 slot 합보다
    커서 mpirun 이 실패한다; 요청 코어와 slots 는 여전히 같다)."""
    pc = process_count
    if not isinstance(pc, int) or isinstance(pc, bool) or pc < 1:
        raise ValueError(f"process_count: {pc!r}")
    ppn = max(1, pc // worker_pod_count(candidates))
    if ppn > MAX_PROCESSES_PER_WORKER:
        raise ValueError(f"processes per worker {ppn} > {MAX_PROCESSES_PER_WORKER}")
    return ppn


def _processes_per_node(spec):
    return processes_per_node(spec.process_count, spec.candidates)


def _worker_resources(spec):
    """워커 CPU 요청 = 그 워커의 rank 수(2026-10-16 사용자 결정 「프로세스 개수만큼 코어」, 옵션 없음). 정수 코어 문자열
    ("2") -- CRD 는 int-or-string 이지만 문자열로 고정한다. limits 는 두지 않는다: CFS quota 는 sshd·orted·busy-poll 하는
    rank(Open MPI ob1/tcp)·libcircle 작업 훔치기를 합쳐 100ms 창마다 끊어 MPI 전체를 가장 느린 rank 에 묶는다(실행
    타임아웃까지 늘어진다). requests 만으로 경합 시 cpu.weight 몫이 보장되고 남는 코어는 쓴다. memory 는 요청하지 않는다
    (QoS Burstable -- 메모리 축출 순위는 BestEffort 와 같은 ①그룹, 05-volcano-queue-priorityclass.yaml dms-build 주석)."""
    return {"requests": {"cpu": str(_processes_per_node(spec))}}


def _reject_supplementary_gids(spec) -> None:
    """빌더는 순수 함수라 입력을 믿지 않는다: 보조 gid 목록이 실린 spec(2026-10-07~25 LDAP 보조 그룹 시절의 스냅숏 모양 --
    dms-k8s 에는 정상 producer 가 없다)은 ValueError(어댑터가 submit_failed 로 접는다). 그룹 물질화를 지웠으니 조용히
    버리고 진행하면 스냅숏과 다른 권한으로 돈다(stepper.identity_problem 이 제출 전에 먼저 막는다 -- 이것은 백스톱).
    키 부재·None·[] 는 정상."""
    v = (spec.identity or {}).get("supplementary_gids")
    if v is not None and v != []:
        raise ValueError("supplementary gids are not supported (k8s identity mode)")


def _worker_env(spec):
    _reject_supplementary_gids(spec)
    ident = spec.identity or {}
    return {
        "DMS_JR_UID": str(ident.get("uid", 0)), "DMS_JR_GID": str(ident.get("gid", 0)),
        "DMS_JR_USERNAME": ident.get("username", "root"),
        "DMS_JR_PROCESSES_PER_NODE": str(_processes_per_node(spec)),
    }


def _worker_container(name, image, spec, volumes):
    return _container(name, image, ["sh", "-c", _worker_command_script()],
                      _worker_env(spec), volumes,
                      security_context=_worker_security_context(),
                      resources=_worker_resources(spec))


def _pod_volumes(volumes):
    return [{"name": v["name"], "hostPath": {"path": v["hostPath"]["path"],
                                             "type": "Directory"}} for v in volumes]


def _artifact_dir(spec):
    # 파드 **안** 경로다(2026-09-09): base 는 execution_volcano._volumes 가 전용
    # hostPath 볼륨으로 ARTIFACT_MOUNT 에 마운트한다(artifact_base.ARTIFACT_MOUNT 주석
    # -- 공용 디렉터리를 그룹 쓰기·other 통과 금지로 잠가도 되는 근거). 호스트 경로(<base>/<job>/<phase>)는 제어면
    # 읽기(execution_volcano.read_summary, api)와 artifact_uri 가 쓰고, 러너는 이
    # 경로로만 쓴다 -- 두 경로는 같은 디렉터리다(같은 hostPath).
    return f"{ARTIFACT_MOUNT}/{spec.job_id}/{spec.phase}"


def _launcher_env(spec):
    ap = _abs_paths(spec)
    argv = tool_argv(spec, abs_paths=ap)
    ident = spec.identity or {}
    env = {
        "DMS_JR_TOOL": spec.tool, "DMS_JR_OPERATION": spec.operation,
        "DMS_JR_PHASE": spec.phase, "DMS_JR_DRYRUN": "1" if spec.dryrun else "0",
        "DMS_JR_PROCESS_COUNT": str(spec.process_count),
        "DMS_JR_PROCESSES_PER_NODE": str(_processes_per_node(spec)),
        "DMS_JR_ARGV": json.dumps(argv),
        "DMS_JR_UID": str(ident.get("uid", 0)), "DMS_JR_GID": str(ident.get("gid", 0)),
        "DMS_JR_USERNAME": ident.get("username", "root"),
        "DMS_JR_ARTIFACT_DIR": _artifact_dir(spec),  # 스킴 제거된 파일시스템 경로
    }
    if spec.tool == "drm":
        # 러너가 drm 직전에 대상 봉쇄를 다시 본다(EXECUTION_REASONS 주석) -- 대상은 argv 마지막(tool_argv), 기준은 이 값.
        # 연산이 아니라 도구로 거는 이유: 지우는 것은 drm 이다(변조 행의 연산·도구 불일치도 봉쇄 밖으로 새지 않는다).
        env["DMS_JR_RM_ROOT"] = _rm_managed_root(spec)
    elif spec.tool in ("dsync", "dscan", "nsync"):
        # PV 주소 봉쇄 재확인(_PV_CONTAIN_FN, 2026-10-26) -- dsync·dscan 은 런처가 mpirun 직전에(런처는 데이터 경로를 마운트한다 --
        # primary 볼륨), nsync 는 런처가 경로를 볼 수 없어(아티팩트 base 만 마운트) 각 rank 가 도구 exec 직전에 **자기 역할 쪽만**
        # 본다(dms_job_runner.contain — 소스 rank 는 소스, 목적지 rank 는 목적지). 키 부재 = PV 가 아닌 잡.
        entries = contain_entries(spec, ("source", "destination") if spec.operation == "sync" else ("target",))
        if entries:
            env["DMS_JR_CONTAIN"] = json.dumps(entries)
    return env


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


# 잡 파드의 종료 유예(2026-10-16 사용자 보고: 「배치작업 Cancel 했는데 몇분 후에 ... 아직 돌아가고 있는 launcher/worker
# pod」). 기본 30초면 취소(vcjob 삭제) 뒤 launcher 가 30초 Terminating 으로 남는다 -- PID 1 인 dms-job-runner(Python)는
# SIGTERM 처리기가 없어 신호를 무시하고 SIGKILL 을 기다린다(preflight 셸도 같다). 이 파드들은 정리할 상태가 없다(산출물은
# 단계마다 이미 base 에 쓴 파일이고, 지우는 쪽은 취소·요청 삭제·Volcano 완료 처리뿐이다) -- 짧게 둔다.
TASK_TERMINATION_GRACE_SECONDS = 5


def _apply_termination_grace(tasks):
    for task in tasks:
        task["template"]["spec"]["terminationGracePeriodSeconds"] = TASK_TERMINATION_GRACE_SECONDS


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
# <job>(0755)·<phase>(요청자로 chown)를 만들고, 도구는 요청자 uid·주 gid 로 mpi-hostfile·rank.sh 를
# 읽고 dscan 리포트를 쓴다. 전용 마운트(ARTIFACT_MOUNT)는 base 의 **부모** 권한을 건너뛰지만
# 마운트 루트인 base 자체의 x 는 커널이 그대로 본다 -- base 가 root 700/750/770 이면 비 root 잡은
# preview/execution 에서야 "unable to open the hostfile" 로 죽는다. preflight 파드는 같은 uid·gid 로
# 돌므로 여기서 먼저 명확한 사유로 거부한다. 경로는 상수라 인라인해도 안전하다(사용자 입력 아님).
# base 볼륨을 실제로 마운트한 파드에만 붙인다(build_preflight_pod) -- 마운트가 없으면 검사할 대상이 없다.
# (2026-10-07~25 의 LDAP 보조 그룹 잡 전용 조각 -- 그룹 자기 검증·base other-x·쓰기 불가 -- 은 2026-10-26 에 지웠다.
# base 의 g+w·ACL·other-x 는 제어면 저장·3홉 검증(artifact_base.roundtrip_artifact_base)이 그대로 막는다.)
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

# rm 대상 봉쇄(2026-10-21 리뷰): $1 = 대상 절대경로(drm argv 의 그 문자열), $R = 그 스토리지 managed_root(stepper._abs 가
# $1 을 조합한 바로 그 값), $parent = dirname "$1". 「대상의 상위 경로 실제 위치 == realpath(managed_root) + 글자 그대로의
# 상대 상위 경로」여야 통과 -- managed_root 아래 상위 경로 성분 어디에도 심링크가 없다는 뜻이다. 왜: drm 은 경로 문자열을
# 걸어 들어가 중간 심링크를 커널이 따라가고, 배치 rm 은 root 라 `test -w` 가 늘 참이다 -- 사용자가 users/alice/data 를
# ../bob(또는 /cephfs/dms 같은 마운트 안 다른 곳)으로 바꿔 두면 확인 창이 보인 「실제 경로」와 다른 트리가 지워졌다(스토리지
# mount_path 전체가 잡 파드에 마운트된다). 마지막 성분은 심링크여도 된다 -- drm 은 대상을 lstat 해 링크 자체만 지운다.
# managed_root 자신·그 위가 심링크인 것은 괜찮다(양쪽을 같은 realpath 로 본다). 끝이 「/」「.」「..」인 대상(DB 변조 행 --
# validate_rm_target 은 정규화된 상대경로만 저장한다)과 상위 경로에 「.」「..」「//」가 낀 대상은 realpath 와 글자가 맞을
# 수 없어 같은 사유로 막힌다(fail-closed). 값은 전부 positional·변수로만 쓴다(case 패턴의 "..." 는 글자 그대로 비교 --
# 경로의 * [ 가 글롭이 되지 않는다). 같은 규칙의 러너 재확인: dms_job_runner.runner.rm_target_redirected.
_RM_TARGET_REDIRECTED = 'echo DMS_PREFLIGHT_REASON=target_path_redirected; exit 1'
_RM_CONTAINMENT_CHECK = (
    'case "$1" in */|*/.|*/..) ' + _RM_TARGET_REDIRECTED + ';; esac; '
    'case "$parent" in "$R"|"${R%/}") rel=;; "${R%/}"/*) rel=${parent#"${R%/}"/};; '
    '*) ' + _RM_TARGET_REDIRECTED + ';; esac; '
    'real_root=$(realpath -e -- "$R") && real_parent=$(realpath -e -- "$parent") || '
    '{ ' + _RM_TARGET_REDIRECTED + '; }; '
    'if [ -z "$rel" ]; then want=$real_root; else want=${real_root%/}/$rel; fi; '
    '[ "$real_parent" = "$want" ] || { ' + _RM_TARGET_REDIRECTED + '; }; ')


# PV 주소 봉쇄(2026-10-26 보안 리뷰 — dms-k8s 의 「모든 잡 root + namespace 단위 권한」): $1 = 경로, $2 = 그 쪽 PV 루트 절대경로
# (글자 경계), $3 = 그 쪽 스토리지 managed_root(심링크 검사의 **기준점** — stepper 가 경로와 PV 루트를 조합한 바로 그 값).
# 통과 조건: (a) 경로가 글자로 PV 루트이거나 그 아래, (b) managed_root 부터 경로까지 **어느 성분도 심링크가 아니다** — 경로가
# 있으면 realpath(경로) == realpath(managed_root) + 글자 그대로의 상대경로(마지막 성분이 링크여도 막는다: dsync 소스·dscan 대상이
# 링크면 도구가 따라가고, 목적지가 링크면 그 너머에 쓴다), 없으면(sync 목적지가 아직 없음) 부모로 같은 판정. 기준점이 PV 루트가
# 아니라 managed_root 인 이유(같은 날 2차 보안 리뷰): PV 루트를 기준으로 realpath 를 맞추면 managed_root 와 PV 루트 **사이**의
# 성분(PV 루트 자신 포함)이 링크여도 양쪽이 같이 따라가 통과한다 — 파드가 PV 루트의 부모에 쓸 수 있는 CSI 에서 `{vol}-data` 를
# 남의 PV 로 가는 링크로 바꾸면 그대로 넘어갔다. 왜 막나: 모든 잡이 root 라 커널 DAC 가 없고, 사용자 파드가 자기 PV 안에 남의 PV 를
# 가리키는 링크(../../pvc-b/…)를 심으면 PV A 권한만으로 PV B 를 읽거나 덮어쓸 수 있었다. 트리 **안쪽**의 링크는 상관없다(dsync 는
# 링크를 링크로 복사, dscan 은 lstat). managed_root 자신·그 위가 링크인 것은 괜찮다(양쪽을 같은 realpath 로 본다). 빈 값·「/」「.」
# 「..」로 끝나는 경로는 막는다(fail-closed). 같은 규칙의 러너 재확인: dms_job_runner.contain.pv_path_redirected(dsync·dscan 은
# 런처, nsync 는 역할별 rank — tests/test_pv_containment_sh.py 가 같은 트리로 대조). PV 가 아닌 잡은 이 함수를 싣지 않는다(매니페스트가
# 이 기능 이전과 바이트 단위로 같다).
_PV_CONTAIN_FN = (
    'dms_contain() { '
    'case "$1" in */|*/.|*/..) ' + _RM_TARGET_REDIRECTED + ';; esac; '
    '[ -n "$2" ] && [ -n "$3" ] || { ' + _RM_TARGET_REDIRECTED + '; }; '
    'case "$1" in "$2"|"${2%/}"|"${2%/}"/*) ;; *) ' + _RM_TARGET_REDIRECTED + ';; esac; '
    'case "$1" in "$3"|"${3%/}") rel=;; "${3%/}"/*) rel=${1#"${3%/}"/};; *) ' + _RM_TARGET_REDIRECTED + ';; esac; '
    'real_root=$(realpath -e -- "$3") || { ' + _RM_TARGET_REDIRECTED + '; }; '
    'if [ -z "$rel" ]; then want=$real_root; else want=${real_root%/}/$rel; fi; '
    'if [ -e "$1" ] || [ -L "$1" ]; then '
    'real=$(realpath -e -- "$1") || { ' + _RM_TARGET_REDIRECTED + '; }; '
    '[ "$real" = "$want" ] || { ' + _RM_TARGET_REDIRECTED + '; }; '
    'else '
    'real=$(realpath -e -- "$(dirname -- "$1")") || { ' + _RM_TARGET_REDIRECTED + '; }; '
    '[ "$real" = "$(dirname -- "$want")" ] || { ' + _RM_TARGET_REDIRECTED + '; }; '
    'fi; }; ')

# PV 주소 rm 의 글자 경계: $1 = 대상, $2 = PV 루트 — 대상이 PV 루트 **아래**여야 한다(PV 루트 자신은 제출이 pv_root_rm_forbidden
# 으로 막는다). 심링크 검사는 기존 rm 봉쇄(_RM_CONTAINMENT_CHECK, 기준 managed_root — 마지막 성분 링크는 drm 이 링크만 지우므로
# 허용)가 그대로 맡는다. 빈 PV 루트는 막는다("${2%/}"/* 가 「/*」가 되어 모든 절대경로에 맞지 않게).
_PV_WITHIN_FN = (
    'dms_within() { '
    '[ -n "$2" ] || { ' + _RM_TARGET_REDIRECTED + '; }; '
    'case "$1" in "${2%/}"/*) ;; *) ' + _RM_TARGET_REDIRECTED + ';; esac; }; ')


# 목적지 PV 루트의 소유자 측정(2026-10-26 사용자 결정 — 결과를 목적지 PV 소유자로 chown): $1 = PV 루트. 봉쇄(dms_contain)가
# 경로 사슬에 링크가 없음을 확인한 **뒤에** 부른다. PV 루트가 링크가 아닌 디렉터리여야 하고(아니면 pv_owner_unknown — 모름을
# 소스 소유 보존으로 접지 않는다), stat 은 GNU 기본이 lstat 이다. 마커 한 줄을 stepper 가 parse_pv_owner 로 읽어 잡에 기록한다.
_PV_OWNER_FN = (
    'dms_pv_owner() { '
    'if [ -L "$1" ] || [ ! -d "$1" ]; then echo DMS_PREFLIGHT_REASON=pv_owner_unknown; exit 1; fi; '
    'o=$(stat -c %u:%g -- "$1") || { echo DMS_PREFLIGHT_REASON=pv_owner_unknown; exit 1; }; '
    'echo "DMS_PV_OWNER=$o"; }; ')

PV_OWNER_MARKER = "DMS_PV_OWNER="


def parse_pv_owner(entries):
    """preflight 파드 로그(read_log 계약 [(pod, log|None, waiting)])에서 목적지 PV 루트 소유자 "uid:gid" 를 뽑는다.
    마커 줄이 **정확히 하나**이고 형식이 맞을 때만 — 0개(옛 파드·로그 못 읽음)·여럿(경로 문자열의 개행 등 위조 의심)·형식
    오류는 None(호출자가 pv_owner_unknown 으로 막는다). nsync 는 목적지 파드만 찍는다."""
    found = []
    for entry in entries or ():
        log = entry[1]
        if log is None:
            continue
        for line in log.split("\n"):
            line = line.strip()
            if line.startswith(PV_OWNER_MARKER):
                found.append(line[len(PV_OWNER_MARKER):].strip())
    if len(found) != 1 or not valid_owner(found[0]):
        return None
    return found[0]


def contain_entries(spec, sides) -> list:
    """PV 주소 쪽의 [쪽, 경로, PV 루트, 기준 managed_root] — stepper 가 paths["contain"] = {쪽: {"root", "anchor"}} 로 싣는다.
    키가 없으면 [](PV 가 아닌 잡). 값이 문자열이 아니면 빈 값으로 실어 봉쇄가 막게 한다(fail-closed — 빌더는 입력을 믿지
    않는다). 러너 env DMS_JR_CONTAIN 도 이 모양 그대로다(dms_job_runner.contain.parse_entries)."""
    paths = spec.paths or {}
    contain = paths.get("contain")
    if not isinstance(contain, dict):
        return []
    out = []
    for side in sides:
        if side not in contain:
            continue
        c = contain[side] if isinstance(contain[side], dict) else {}
        path = paths.get(side)
        out.append([side, path if isinstance(path, str) else "",
                    c.get("root") if isinstance(c.get("root"), str) else "",
                    c.get("anchor") if isinstance(c.get("anchor"), str) else ""])
    return out


def _with_checks(script: str, args: list, fn: str, calls: list) -> tuple:
    """기존 preflight 스크립트에 봉쇄 검사를 덧붙인다 — 함수 정의는 맨 앞(set -e 뒤), 호출은 마지막 OK 바로 앞(기존 사유가 먼저:
    소스 읽기·목적지 종류·부모 쓰기 실패는 그 사유로 말한다). calls = 호출마다의 인자 목록(전부 positional). 없으면 그대로."""
    if not calls:
        return script, args
    name = fn.split("(", 1)[0]
    text = ""
    n = len(args)
    for values in calls:
        args = [*args, *values]
        text += name + "".join(f' "${{{n + i + 1}}}"' for i in range(len(values))) + "; "
        n += len(values)
    head = "set -e; "
    assert script.startswith(head) and script.endswith("echo DMS_PREFLIGHT_OK")
    body = script[len(head):-len("echo DMS_PREFLIGHT_OK")]
    return head + fn + body + text + "echo DMS_PREFLIGHT_OK", args


def _with_containment(script: str, args: list, entries: list) -> tuple:
    return _with_checks(script, args, _PV_CONTAIN_FN, [[path, root, anchor] for _, path, root, anchor in entries])


def _with_pv_owner(spec, script: str, args: list) -> tuple:
    """PV 목적지 root sync 의 preflight 에 목적지 PV 루트 소유자 측정을 덧붙인다(_PV_OWNER_FN — 봉쇄 호출 **뒤**). 그 밖의
    잡은 그대로(바이트 동일)."""
    if not pv_owner_measured(spec):
        return script, args
    entries = contain_entries(spec, ("destination",))
    root = entries[0][2] if entries else ""
    return _with_checks(script, args, _PV_OWNER_FN, [[root]])


def _rm_managed_root(spec) -> str:
    """rm spec 의 managed_root(stepper._build_spec 이 paths 에 싣는다) — PV 주소 rm 도 같은 기준점이다(PV 루트로 좁히면
    managed_root 와 PV 루트 사이의 링크를 놓친다, _PV_CONTAIN_FN 주석). 없거나 빈 값이면 ValueError -- 봉쇄 검사 없이 root
    drm 을 내보내지 않는다(어댑터가 submit_failed 로 접는다, fail-closed). 빌더는 입력을 믿지 않는다."""
    root = (spec.paths or {}).get("managed_root")
    if not isinstance(root, str) or root == "":
        raise ValueError("rm spec without managed_root -- refusing to build an uncontained drm")
    return root


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
    실행 모드("user"/"root")는 경로와 같이 positional 로 넘긴다(셸 인젝션 차단 관례).

    rm(2026-10-21 리뷰): 부모 쓰기 다음에 대상 봉쇄(_RM_CONTAINMENT_CHECK -- 상위 경로에 심링크 성분 없음)를 본다.
    root 든 아니든, 단건이든 배치 자식이든, preflight·exec_preflight 모두 이 한 스크립트다(phase 무관) -- root 는 `test -w`
    가 늘 참이라 봉쇄가 유일한 검사다. 부모 쓰기가 먼저인 이유: 부모가 없으면 realpath -e 도 실패하는데, 그 경우의 사유는
    종전대로 parent_not_writable 이어야 한다(대상 오타를 「다른 곳을 가리킨다」로 말하지 않는다). managed_root 는 경로처럼
    positional 로 넘긴다($2)."""
    ap = _abs_paths(spec)
    mode = "root" if (spec.identity or {}).get("privileged") else "user"
    if spec.operation == "sync":
        # PV 주소 쪽은 기존 검사 뒤에 봉쇄(_PV_CONTAIN_FN)를 덧붙인다 — nsync 의 역할별 preflight 는 자기 쪽만 본다.
        if role == "source":
            script = ('set -e; '
                      'test -r "$1" || { echo DMS_PREFLIGHT_REASON=source_not_readable; exit 1; }; '
                      'echo DMS_PREFLIGHT_OK')
            return _with_containment(script, [ap["source"]], contain_entries(spec, ("source",)))
        if role == "destination":
            script = ('set -e; D="$1"; M="$2"; ' + _DEST_TYPE_CHECK + _DEST_CHECK
                      + 'echo DMS_PREFLIGHT_OK')
            return _with_pv_owner(spec, *_with_containment(script, [ap["destination"], mode],
                                                           contain_entries(spec, ("destination",))))
        script = ('set -e; D="$2"; M="$3"; '
                  'test -r "$1" || { echo DMS_PREFLIGHT_REASON=source_not_readable; exit 1; }; '
                  + _DEST_TYPE_CHECK + _DEST_CHECK + 'echo DMS_PREFLIGHT_OK')
        return _with_pv_owner(spec, *_with_containment(script, [ap["source"], ap["destination"], mode],
                                                       contain_entries(spec, ("source", "destination"))))
    if spec.operation == "rm":
        script = ('set -e; R="$2"; '
                  'parent=$(dirname "$1"); '
                  'test -w "$parent" || { echo DMS_PREFLIGHT_REASON=parent_not_writable; exit 1; }; '
                  + _RM_CONTAINMENT_CHECK + 'echo DMS_PREFLIGHT_OK')
        # PV 주소 rm: 심링크 검사는 위 rm 봉쇄(기준 managed_root)가 그대로 맡고, 대상이 PV 루트 아래라는 글자 경계만 더한다.
        return _with_checks(script, [ap["target"], _rm_managed_root(spec)], _PV_WITHIN_FN,
                            [[path, root] for _, path, root, _ in contain_entries(spec, ("target",))])
    script = ('set -e; '
              'test -r "$1" || { echo DMS_PREFLIGHT_REASON=target_not_readable; exit 1; }; '
              'echo DMS_PREFLIGHT_OK')
    return _with_containment(script, [ap["target"]], contain_entries(spec, ("target",)))


PREFLIGHT_REASON_MARKER = "DMS_PREFLIGHT_REASON="
# _preflight_script 가 낼 수 있는 사유의 **전수**. 스테퍼는 이 집합 밖 문자열을
# 잡 reason_code 로 쓰지 않는다 — 파드 로그는 신뢰 입력이 아니고(설계 §4), 매핑
# 없는 코드를 박으면 포탈이 원문 코드를 그대로 노출한다. 화이트리스트를 스크립트와
# 같은 파일에 두는 이유: 새 검사를 추가하면서 등록을 빠뜨리는 드리프트를 한 화면에서
# 막는다(계약 테스트가 스크립트 실물에서 마커를 추출해 이 집합과 대조한다). 여기에
# 코드를 추가하면 frontend/src/lib/reasonCodes.json 과 api.ts REASON_MESSAGES 도
# 같이 갱신해야 한다(양방향 계약 테스트). 2026-10-21 rm 대상 봉쇄: target_path_redirected(_RM_CONTAINMENT_CHECK --
# 같은 코드를 러너의 drm 직전 재확인도 낸다, EXECUTION_REASONS). 2026-10-26: LDAP 보조 그룹 조각이 내던
# identity_groups_not_applied·artifact_base_group_writable 은 스크립트에서 사라져 이 집합에서도 뺐다(계약 테스트가 스크립트
# 실물과 대조한다 -- 사유 코드 목록·포탈 문구는 옛 행 표시용으로 남는다).
PREFLIGHT_REASONS = frozenset({
    "pv_owner_unknown",     # 2026-10-26 목적지 PV 루트 소유자 측정(_PV_OWNER_FN)
    "source_not_readable", "destination_not_directory",
    "destination_parent_not_writable", "destination_not_writable",
    "destination_not_owned", "parent_not_writable",
    "target_not_readable", "artifact_base_not_traversable",
    "target_path_redirected"})


# 실행·미리보기(mpirun 을 돌리는 launcher) 실패의 사유 마커(2026-10-02). 러너(dms_job_runner.runner)가 워커
# 준비(IP 해석 + ssh)를 제한 시간 안에 못 끝내면 mpirun 없이 이 마커를 찍고 끝낸다 -- 예전엔 이름이 남은
# hostfile 로 mpirun 이 돌아 "Could not resolve hostname" rc 255 = 사유 없는 execution_failed 였다. 문법은
# preflight 마커와 같고(한 줄 접두), 승격은 화이트리스트로만(파드 로그는 신뢰 입력이 아니다). 러너는 dms 를
# import 하지 않아 같은 값을 따로 정의한다 -- tests/test_job_runner_runner.py 의 계약 테스트가 둘을 잇는다.
# 여기에 코드를 추가하면 frontend/src/lib/reasonCodes.json 과 api.ts REASON_MESSAGES 도 함께.
# 러너 계약(WORKERS_UNREACHABLE ∈ 이 집합)은 부분집합이다(2026-10-26: 워커 셸이 찍던 LDAP 보조 그룹의
# identity_groups_not_applied 는 물질화 조각과 함께 지웠다 -- 러너·잡 이미지는 바뀌지 않는다).
# 2026-10-21 rm 대상 봉쇄: target_path_redirected 는 러너가 drm(미리보기·실행) 직전에 preflight 와 같은 규칙으로 다시
# 봐서 찍는다(runner.rm_target_redirected, env DMS_JR_RM_ROOT -- _launcher_env). exec_preflight 파드와 vcjob 은 다른
# 파드라 그 사이(Volcano 대기 포함) 바뀐 심링크를 여기서 잡는다 -- 잡 이미지가 바뀐다.
EXECUTION_REASON_MARKER = "DMS_EXEC_REASON="
EXECUTION_REASONS = frozenset({"workers_unreachable", "target_path_redirected"})


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
    _reject_supplementary_gids(spec)
    ident = spec.identity or {}
    script, path_args = _preflight_script(spec, role=role)
    # 접두: 아티팩트 base 통과(_ARTIFACT_BASE_CHECK 주석) → 경로 검사. base 검사가 경로 검사보다 **먼저**인 이유: base 가
    # 막혀 있으면 경로를 고쳐도 모든 비 root 잡이 죽는다(운영 설정 문제를 먼저 드러낸다).
    if any(v.get("mountPath") == ARTIFACT_MOUNT for v in volumes):
        script = _ARTIFACT_BASE_CHECK + script
    role_seg = _PREFLIGHT_ROLE_SEG.get(role, "")
    container = {"name": "preflight", "image": job_image,
                 "command": ["sh", "-c", script, "sh", *path_args],
                 "securityContext": {"runAsUser": ident.get("uid", 0),
                                     "runAsGroup": ident.get("gid", 0)},
                 "volumeMounts": [{"name": v["name"], "mountPath": v["mountPath"],
                                   "mountPropagation": "HostToContainer"}
                                  for v in volumes]}
    pod_spec = {"restartPolicy": "Never",
                "terminationGracePeriodSeconds": TASK_TERMINATION_GRACE_SECONDS,
                "nodeSelector": {"kubernetes.io/hostname": node},
                "containers": [container],
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


def _build_nsync_job(spec, *, job_image, namespace, volumes, role_volumes):
    # 볼륨은 task 별이다(2026-10-22): 소스·목적지 노드가 disjoint 라 반대편 hostPath(type Directory)는 그 노드에
    # 없다 -- 모든 task 에 양쪽을 붙이면 FailedMount 로 gang 이 영원히 안 선다. volumes = 런처(아티팩트 base 만),
    # role_volumes["source"/"destination"] = 각 워커(자기 스토리지 + 아티팩트 base). 없으면 fail-closed.
    if not role_volumes or "source" not in role_volumes or "destination" not in role_volumes:
        raise ValueError("nsync job needs per-role volumes (source/destination)")
    src_volumes, dst_volumes = role_volumes["source"], role_volumes["destination"]
    src_nodes = spec.candidates["source"]
    dst_nodes = spec.candidates["destination"]
    env = _launcher_env(spec)
    env["DMS_JR_SOURCE_NODES"] = json.dumps(src_nodes)
    env["DMS_JR_DEST_NODES"] = json.dumps(dst_nodes)
    # 런처도 후보 노드(출발 ∪ 목적지) 안에 둔다(2026-10-02 노드 배치 제외): 예전엔 affinity 가 없어 아무 노드에나
    # 놓였다 -- 관리자가 배치에서 뺀(그러나 cordon 은 안 한) 노드에 런처가 앉을 수 있었다. primary 런처와 같은 규칙
    # (워커와 같은 노드에 함께 있어도 된다 -- 산개는 워커 task 끼리만).
    launcher_nodes = sorted(set(src_nodes) | set(dst_nodes))
    launcher = {"name": "launcher", "replicas": 1, "template": {"spec": {
        "restartPolicy": "Never",
        "affinity": _node_affinity(launcher_nodes) if launcher_nodes else {},
        "containers": [_container("launcher", job_image,
            ["/usr/local/bin/dms-job-runner"], env, volumes, resources=_launcher_resources())],
        "volumes": _pod_volumes(volumes)}}}
    src_worker = {"name": "source-worker", "replicas": len(src_nodes),
        "template": {
            "metadata": _worker_task_metadata(spec, "source-worker"),
            "spec": {"restartPolicy": "Never",
                "affinity": _worker_affinity(spec, "source-worker", src_nodes),
                "containers": [_worker_container("source-worker", job_image, spec, src_volumes)],
                "volumes": _pod_volumes(src_volumes)}}}
    dst_worker = {"name": "destination-worker", "replicas": len(dst_nodes),
        "template": {
            "metadata": _worker_task_metadata(spec, "destination-worker"),
            "spec": {"restartPolicy": "Never",
                "affinity": _worker_affinity(spec, "destination-worker", dst_nodes),
                "containers": [_worker_container("destination-worker", job_image, spec, dst_volumes)],
                "volumes": _pod_volumes(dst_volumes)}}}
    job_spec = {"schedulerName": "volcano", "queue": spec.queue,
                "minAvailable": len(src_nodes) + len(dst_nodes) + 1,
                "priorityClassName": spec.priority_class,
                "plugins": {"ssh": [], "svc": []},
                "policies": [{"event": "TaskCompleted", "action": "CompleteJob"},
                             {"event": "PodFailed", "action": "AbortJob"}],
                "tasks": [launcher, src_worker, dst_worker]}
    _apply_task_deadlines(spec, job_spec["tasks"])
    _apply_termination_grace(job_spec["tasks"])
    _apply_ttl(spec, job_spec)
    return {
        "apiVersion": "batch.volcano.sh/v1alpha1", "kind": "Job",
        "metadata": {"name": _job_name(spec), "namespace": namespace,
                     "labels": {"dms.io/job-id": spec.job_id,
                                "dms.io/phase": spec.phase, "dms.io/tool": spec.tool}},
        "spec": job_spec}


def build_volcano_job(spec, *, job_image, namespace, volumes, role_volumes=None):
    # nsync 는 role_volumes 가 필수다(_build_nsync_job) -- volumes 는 그 런처 몫. primary 잡은 volumes 하나를 모든 task 에.
    if "primary" not in spec.candidates:
        return _build_nsync_job(spec, job_image=job_image, namespace=namespace,
                                volumes=volumes, role_volumes=role_volumes)
    workers = _worker_count(spec)
    nodes = spec.candidates.get("primary", [])
    launcher = {
        "name": "launcher", "replicas": 1,
        "template": {"spec": {
            "restartPolicy": "Never",
            "affinity": _node_affinity(nodes) if nodes else {},
            "containers": [_container("launcher", job_image,
                ["/usr/local/bin/dms-job-runner"], _launcher_env(spec), volumes,
                resources=_launcher_resources())],
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
    _apply_termination_grace(job_spec["tasks"])
    _apply_ttl(spec, job_spec)
    return {
        "apiVersion": "batch.volcano.sh/v1alpha1", "kind": "Job",
        "metadata": {"name": _job_name(spec), "namespace": namespace,
                     "labels": {"dms.io/job-id": spec.job_id,
                                "dms.io/phase": spec.phase, "dms.io/tool": spec.tool}},
        "spec": job_spec}
