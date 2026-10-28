"""잡 파드 launcher에서 도는 오케스트레이션. 모든 I/O는 주입 — main()이 실제 구현을 넣는다."""
import ipaddress
import json
import os
import sys

from .commands import (
    getent_hosts_command, mpirun_command, nsync_role_map, passwd_line,
    ssh_key_copy_command, ssh_probe_command)
from .parsers import (parse_aborted, parse_dsync_counts, parse_nsync_counts, parse_rm_counts, parse_rm_dryrun_counts,
                      parse_scan_counts, parse_sync_dryrun_counts, parse_tool_detail)

# 워커 준비 대기(2026-10-02 프로덕션 간헐 실패 수리): 모든 워커가 "IP 로 풀리고 + 그 IP 로 ssh 가 되는" 상태가
# 될 때까지 기다리는 **전체** 제한 시간. 예전엔 getent 를 한 번만 하고 실패하면 DNS 이름을 그대로 hostfile 에
# 썼고(ssh 대기는 워커당 90회 시도 뒤 조용히 통과), mpirun 시점의 이름 조회가 한 번 더 실패하면 rc 255 로 잡이
# 죽었다. launcher 는 워커보다 먼저 Ready 가 되고 Volcano svc 서비스는 Ready 파드만 DNS 에 올린다(실측: 성공
# 잡의 hostfile 에도 이름이 섞여 있었다). 이미지 pull 이 느린 노드를 감안해 넉넉히 -- 넘기면 mpirun 없이 사유
# 마커를 남기고 끝낸다(WORKERS_UNREACHABLE).
WORKER_READY_TIMEOUT_SECONDS = 300
# 제어면(stepper)이 실행 실패 사유로 승격하는 마커(execution_manifests.EXECUTION_REASON_MARKER 와 같은 값 --
# dms_job_runner 는 dms 를 import 하지 않는 독립 패키지라 중복 정의, 계약 테스트가 둘을 잇는다).
EXECUTION_REASON_MARKER = "DMS_EXEC_REASON="
WORKERS_UNREACHABLE = "workers_unreachable"
# rm 대상 봉쇄 재확인(2026-10-21 리뷰, 2026-10-28 끝 성분 링크는 링크만 삭제 -- rm_target_redirected) -- preflight 마커와 같은
# 코드(execution_manifests 의 PREFLIGHT_REASONS·EXECUTION_REASONS 양쪽, 계약 테스트가 잇는다).
TARGET_PATH_REDIRECTED = "target_path_redirected"
# rm 미리보기 종류 묶기(2026-10-28): 실행 직전 대상 종류가 미리보기와 다르거나 대상이 없어졌으면 mpirun 없이 이 마커
# (execution_manifests.EXECUTION_REASONS). 종류 값은 execution_manifests.RM_TARGET_KINDS 와 같은 넷(계약 테스트가 잇는다).
TARGET_CHANGED_SINCE_PREVIEW = "target_changed_since_preview"
RM_TARGET_KINDS = ("link", "dir", "file", "other", "missing")
# 도구 결과 사유(2026-10-27 d192 spec §2): 도구가 0 이 아닌 코드로 끝나면 summary v2 상세에서 사유를 정해 마커로 찍는다
# (_derive_reason). execution_manifests.TOOL_FAILURE_REASONS 와 같은 집합이어야 한다 -- 갈라지면 스테퍼가 승격하지 못하고
# execution_failed 로 뭉갠다(tests/test_job_runner_tool_detail.py 계약 테스트가 잇는다).
TOOL_ABORTED = "tool_aborted"
DESTINATION_FULL = "destination_full"
DESTINATION_READ_ONLY = "destination_read_only"
SYNC_STOPPED_MAX_ERRORS = "sync_stopped_max_errors"
SYNC_TYPE_CHANGE_REFUSED = "sync_type_change_refused"
SOURCE_SCAN_INCOMPLETE = "source_scan_incomplete"
SYNC_ITEMS_FAILED = "sync_items_failed"
RM_ITEMS_FAILED = "rm_items_failed"
# 미리보기 전용: nsync 스캔 오류(소스·목적지 어느 쪽이든 -- nsync 는 delete 없이 쪽을 말하지 않는다)로 미리보기가 실패했다
# (아무것도 바뀌지 않았다 -- 실행 사유의 「반영됐습니다」 문구를 쓰지 않는다). dsync 는 walk 오류로 실패하지 않아 해당 없다.
PREVIEW_SCAN_INCOMPLETE = "preview_scan_incomplete"
TOOL_FAILURE_REASONS = frozenset({TOOL_ABORTED, DESTINATION_FULL, DESTINATION_READ_ONLY, SYNC_STOPPED_MAX_ERRORS,
                                  SYNC_TYPE_CHANGE_REFUSED, SOURCE_SCAN_INCOMPLETE, SYNC_ITEMS_FAILED, RM_ITEMS_FAILED,
                                  PREVIEW_SCAN_INCOMPLETE})
# summary v2 표식 -- 이 키가 있으면 상세 키가 계산됐다(없으면 옛 잡·조기 종료·상세 파싱 실패: 핵심 3키뿐).
SUMMARY_DETAIL_VERSION = 2
# 실패 시 launcher 로그(포탈 「실행 로그」·진단 박제)에 남기는 도구 출력 꼬리. 도구 출력은 stdout.log/stderr.log 파일에만
# 있어(MFU_LOG 는 stdout) 포탈의 실패 지점 로그(파드 로그)에 원인이 보이지 않았다(포탈 괴리 [19]). 줄마다 "  | " 를 붙여
# 어떤 줄도 마커로 시작하지 못하게 하고(_parse_marker 는 strip 뒤 startswith), 한 줄은 512자로 자른다.
# 바이트 상한(d192 리뷰 runner-5): 줄 수만 묶으면 80줄 × 512자(한글이면 ×3 바이트)가 수십 KB 라, 파드가 사라진 뒤 포탈이 보여
# 주는 박제(stepper._diag_entry -- 파드 로그의 마지막 DIAG_TAIL_BYTES=16 KiB)가 발췌 한가운데서 시작해 사유 마커·
# DMS_JR_WORKER_READY 줄(느린 워커 진단)을 통째로 잃었다. 마커 포함 글 전체를 UTF-8 10 KiB 안에 두고(남는 6 KiB 가 앞의
# launcher 줄 몫), 최신 줄을 남긴다. stderr 는 작은 몫(2 KiB -- MPI 중단 배너·ABORT 줄은 짧다), 나머지는 stdout 이다.
TOOL_OUTPUT_STDOUT_LINES = 80
TOOL_OUTPUT_STDERR_LINES = 20
TOOL_OUTPUT_LINE_MAX = 512
TOOL_OUTPUT_PREFIX = "  | "
TOOL_OUTPUT_MAX_BYTES = 10 * 1024
TOOL_OUTPUT_STDERR_BYTES = 2 * 1024
# 머리 줄("DMS_JR_TOOL_OUTPUT (stdout.log 마지막 80줄)" ≈ 48 바이트) 하나의 예약 -- 줄 수 자릿수가 바뀌어도 넘지 않게 넉넉히.
_TOOL_OUTPUT_HEADER_RESERVE = 64
# 출력을 파싱하는 도구(-q 면 아무것도 찍지 않는다 -- 상세 모름). dscan 의 개수는 리포트 파일에서 온다.
_LOG_PARSED_TOOLS = ("dsync", "nsync", "drm")
_SYNC_TOOLS = ("dsync", "nsync")

# 슬라이스 24 §2.1 층3: 러너가 exec 할 수 있는 도구의 최종 allowlist.
# dms 패키지의 config.AGENT_TOOL_NAMES 와 같은 값이어야 하지만 dms_job_runner 는
# dms 를 import 하지 않는 독립 패키지(잡 이미지에 단독 설치)라 여기 중복 정의한다
# -- 동일성은 tests/test_job_runner_runner.py 의 계약 테스트가 강제한다.
# rank.sh 가 `exec {tool}` 이라 명령 이름 자체가 tool 값이다: 층1(스테퍼)·층2
# (tool_argv)가 뚫려도 -- 이미 제출된 매니페스트/env 의 사후 변조까지 포함해 --
# 이 층만은 실행을 막는다.
ALLOWED_TOOLS = ("dscan", "dsync", "nsync", "drm")


def _realpath_strict(path):
    return os.path.realpath(path, strict=True)


def path_redirected(path, root, *, realpath=_realpath_strict, lexists=os.path.lexists) -> bool:
    """path 가 managed_root(root) 아래 **어느 성분도**(마지막 포함) 심링크 없이 그 자리에 있는가의 반대(True = 막는다).

    execution_manifests._PATH_CONTAINED_FN(preflight·exec_preflight·sync 워커 셸)의 Python 미러 -- 둘이 갈라지면 한쪽만
    통과하는 경로가 생긴다(tests/test_rm_containment_sh.py·test_sync_containment_sh.py 가 같은 트리로 두 판정을 대조한다).
    판정 = 「실제 위치 == realpath(root) + 글자 그대로의 상대경로」. 아직 없는 끝부분은 존재하는 가장 깊은 상위로 본다(lstat
    기준 -- 끊긴 링크도 「있음」). root 자신·그 위의 심링크는 괜찮다. 끝이 「/」「.」「..」, 「//」「/./」「/../」가 낀 경로,
    root 밖 경로, 실제 경로를 못 구하는 경우(고리 등)는 막는다(fail-closed)."""
    if not isinstance(path, str) or not isinstance(root, str) or root == "":
        return True
    if (path.endswith("/") or path.endswith("/.") or path.endswith("/..")
            or "//" in path or "/./" in path or "/../" in path):
        return True
    base = root[:-1] if root.endswith("/") else root      # 셸 "${dms_r%/}" 와 같다(끝 슬래시 하나만)
    if not (path == base or path.startswith(base + "/")):
        return True
    try:
        real_root = realpath(root)
    except (OSError, ValueError):
        return True
    q = path
    while q != base and not lexists(q):
        q = os.path.dirname(q)
    if q == base:
        want = real_root
    else:
        want = (real_root[:-1] if real_root.endswith("/") else real_root) + "/" + q[len(base) + 1:]
    try:
        real_q = realpath(q)
    except (OSError, ValueError):
        return True
    return real_q != want


def rm_target_redirected(target, root, *, realpath=_realpath_strict) -> bool:
    """drm 대상 봉쇄 재확인(True = 막는다). execution_manifests._RM_CONTAINMENT_CHECK 와 같은 순서의 미러다(계약):
    ① 대상 전체 문자열 -- 끝 「/」「/.」「/..」, 「//」「/./」「/../」(변조 행 `…/x/..` 를 drm 이 글자 단위로 접어 managed_root·
    상위를 지운다 -- 부모만 보면 이 검사가 빠진다) ② managed_root 자신(두 표기) ③ **부모**(os.path.dirname -- 셸 `${1%/*}`)의
    path_redirected. 대상 자신이 심링크면 막지 않는다: drm 이 링크만 지운다(2026-10-28 사용자 결정 -- 10-27 의 「거부」를 뒤집음;
    포크 drm 은 대상을 lstat 한다). 왜 러너도 보나: exec_preflight 파드와 실행 vcjob 은 다른 파드라 그 사이(Volcano 대기는 길 수
    있다)에 사용자가 중간 경로를 심링크로 바꾸면 root drm 이 다른 트리를 지운다. 여기는 mpirun 바로 앞이라 창이 「워커 기동 +
    drm 의 걷기 시작」까지로 줄어든다(완전히 닫히지는 않는다 -- BACKLOG ④). 대상 자신의 종류 변화는 target_kind 묶기가 본다.
    target 은 drm argv 마지막(tool_argv), root 는 env DMS_JR_RM_ROOT(stepper 가 target 을 조합한 값)."""
    if not isinstance(target, str) or not isinstance(root, str) or root == "":
        return True
    if (target.endswith("/") or target.endswith("/.") or target.endswith("/..")
            or "//" in target or "/./" in target or "/../" in target):
        return True
    if target in (root, root[:-1] if root.endswith("/") else root):
        return True
    return path_redirected(os.path.dirname(target), root, realpath=realpath)


def target_kind(path, *, lstat=os.lstat):
    """rm 대상 자신의 종류(따라가지 않는다 -- lstat): "link"|"dir"|"file"|"other", 없으면(ENOENT·ENOTDIR) "missing", 그 밖에
    못 보면(권한 등) None(모름 -- 묶지 않는다). summary 의 target_kind(포탈이 「링크만 지움」을 말한다)와 미리보기 종류 묶기
    (DMS_JR_RM_EXPECT_KIND)의 재료다. 경로 문자열이 아니라 커널 값이라 도구 출력(drm 목록 Type=LNK)처럼 이름으로 위조할 수 없다."""
    import errno as _errno
    import stat as _stat
    try:
        mode = lstat(path).st_mode
    except OSError as exc:
        return "missing" if exc.errno in (_errno.ENOENT, _errno.ENOTDIR) else None
    except (ValueError, TypeError):
        return None
    if _stat.S_ISLNK(mode):
        return "link"
    if _stat.S_ISDIR(mode):
        return "dir"
    if _stat.S_ISREG(mode):
        return "file"
    return "other"


def run_job(env, *, run, write_text, read_text, sleep, wait_hostfile,
            make_executable=lambda path: None, clock=None) -> int:
    username = env["DMS_JR_USERNAME"]
    uid = int(env["DMS_JR_UID"])
    gid = int(env["DMS_JR_GID"])
    home = f"/tmp/dms-home-{uid}"
    artifact_dir = env["DMS_JR_ARTIFACT_DIR"]
    process_count = int(env["DMS_JR_PROCESS_COUNT"])
    procs_per_node = max(1, int(env.get("DMS_JR_PROCESSES_PER_NODE", process_count)))
    argv = json.loads(env["DMS_JR_ARGV"])
    tool = env["DMS_JR_TOOL"]

    if tool not in ALLOWED_TOOLS:
        # exec 은 물론 어떤 부작용(passwd 물질화·ssh 키 복사·chown)도 시작하기
        # 전에 끊는다. 마커는 grep 가능한 한 단어 -- 층3 발동 자체가 층1·2 가
        # 뚫렸다는 조사 신호다(설계 §4). summary 는 3키 계약 유지: 모름은 null.
        print(f"DMS_JR_UNKNOWN_TOOL tool={tool!r} allowed={ALLOWED_TOOLS}",
              file=sys.stderr)
        write_text(f"{artifact_dir}/summary.json",
                   json.dumps({"returncode": 1, "files": None, "bytes": None}))
        return 1

    # 1. identity 물질화 (launcher 자신의 /etc/passwd)
    write_text("/etc/passwd", passwd_line(username, uid, gid, home) + "\n", append=True)

    # 2. launcher의 /root/.ssh(Volcano ssh 플러그인이 물질화)를 요청자 home으로 복사.
    #    mpirun을 runuser로 실행해 SSH 나갈 때 요청자 home의 클라이언트 키가 필요.
    run(ssh_key_copy_command(home, uid, gid))

    # 3. hostfile 대기 — nsync는 source/destination 두 role을 각각 기다린다(순서
    #    보존: source 먼저 -> rank 0..N-1, destination 다음 -> role_map과 일치해야 함).
    if tool == "nsync":
        src_hosts, _ = wait_hostfile("source")
        dst_hosts, _ = wait_hostfile("destination")
        ordered_hosts = [*src_hosts, *dst_hosts]
    else:
        ordered_hosts, _ = wait_hostfile()

    # 4~5. 워커 준비 대기: 원시 호스트명(Volcano svc 의 <pod>.<svc>)을 getent 로 **IP 가 나올 때까지** 풀고, 그
    #    IP 로 ssh 가 될 때까지 기다린다(전체 제한 WORKER_READY_TIMEOUT_SECONDS). hostfile 에는 **IP 만** 쓴다 --
    #    mpirun(orted 기동 ssh) 시점에 DNS 를 전혀 쓰지 않는다. 제한 안에 준비되지 않으면 mpirun 을 돌리지 않고
    #    사유 마커(stepper 가 workers_unreachable 로 승격)와 summary 를 남기고 끝낸다 -- 예전처럼 이름을 남긴 채
    #    진행해 "Could not resolve hostname" rc 255 로 죽게 두지 않는다.
    if clock is None:
        clock = _VirtualClock(sleep)
        sleep = clock.sleep
    ips, problem = _wait_workers_ready(ordered_hosts, run=run, sleep=sleep, clock=clock,
                                       timeout=_ready_timeout(env))
    if problem is not None:
        host, stage, waited = problem
        lines = (f"{EXECUTION_REASON_MARKER}{WORKERS_UNREACHABLE}\n"
                 f"DMS_JR_WORKER_UNREACHABLE host={host} stage={stage} waited={waited}s "
                 f"ready={len(ips)}/{len(ordered_hosts)}\n")
        sys.stdout.write(lines)
        sys.stdout.flush()
        write_text(f"{artifact_dir}/stderr.log", lines)
        # 도구는 실행되지 않았다 -- returncode 는 모름(null), 3키 계약은 유지(설계 §2.3).
        write_text(f"{artifact_dir}/summary.json",
                   json.dumps({"returncode": None, "files": None, "bytes": None}))
        return 1
    hostfile_path = f"{artifact_dir}/mpi-hostfile"
    write_text(hostfile_path,
              "".join(f"{ip} slots={procs_per_node}\n" for ip in ips))

    # 6. rank script — scan은 리포트 경로 치환, nsync는 role-map 인자를 삽입.
    #    role_map은 계획 시점의 소스/목적지 노드 수(DMS_JR_SOURCE_NODES/DEST_NODES,
    #    execution_manifests._build_nsync_job이 채움)로 결정론적으로 계산된다 —
    #    개수만 필요하므로 hostfile의 실제 SSH 호스트명과 무관하다(commands.nsync_role_map).
    report_path = _scan_report_path(artifact_dir)
    rendered = [report_path if a == "$DMS_SCAN_REPORT" else a for a in argv]
    if tool == "nsync":
        src_nodes = json.loads(env.get("DMS_JR_SOURCE_NODES", "[]"))
        dst_nodes = json.loads(env.get("DMS_JR_DEST_NODES", "[]"))
        role_map = nsync_role_map(src_nodes, dst_nodes, slots_per_host=procs_per_node)
        rendered = ["--role-mode", "map", "--role-map", role_map, *rendered]
    rank_body = " ".join(_shquote(a) for a in [tool, *rendered])
    rank_path = f"{artifact_dir}/rank.sh"
    write_text(rank_path, f"#!/bin/sh\nexec {rank_body}\n")
    make_executable(rank_path)

    # 6b. mpirun은 runuser로 요청자 신원으로 도구를 돌린다. 도구(dscan/dsync/…)가
    #     결과 파일(dscan-report.json 등)을 execution 디렉터리에 직접 쓰므로, root가
    #     만든 이 디렉터리를 요청자 소유로 넘겨야 쓰기가 가능하다(안 그러면 도구가
    #     "Failed to write output file"로 실패). 이후 summary/로그는 root가 다시 써도
    #     되고(권한 무시), world-readable이라 컨트롤러 read_summary가 읽을 수 있다.
    run(["chown", "-R", f"{uid}:{gid}", artifact_dir])

    # 6c. rm 대상 봉쇄 재확인(rm_target_redirected docstring) -- mpirun 바로 앞(워커 준비 대기 뒤)이라 가장 늦은 자리다.
    #     env 키 부재 = 이 검사 전의 제어면이 만든 매니페스트(롤링 업그레이드) -- 그 제어면의 preflight 도 보지 않았으니
    #     종전대로 둔다. 키가 있으면(빈 값 포함) 판정한다.
    rm_kind = None
    if tool == "drm" and "DMS_JR_RM_ROOT" in env:
        target = argv[-1] if argv else None
        if rm_target_redirected(target, env["DMS_JR_RM_ROOT"]):
            lines = (f"{EXECUTION_REASON_MARKER}{TARGET_PATH_REDIRECTED}\n"
                     f"DMS_JR_RM_TARGET_REDIRECTED target={target!r} root={env['DMS_JR_RM_ROOT']!r}\n")
            sys.stdout.write(lines)
            sys.stdout.flush()
            write_text(f"{artifact_dir}/stderr.log", lines)
            # 도구는 실행되지 않았다 -- returncode 는 모름(null), 3키 계약 유지(워커 준비 실패와 같은 모양).
            write_text(f"{artifact_dir}/summary.json",
                       json.dumps({"returncode": None, "files": None, "bytes": None}))
            return 1
        # 6d. 대상 종류(2026-10-28): 미리보기·실행 summary 의 target_kind, 그리고 실행의 미리보기 종류 묶기. 기대 종류는 stepper 가
        #     실행 단계에만 싣는다(execution_manifests._launcher_env) -- 다르거나 대상이 없어졌으면 mpirun 없이 거부한다: 「링크만
        #     지운다」로 확인한 요청이 그 사이 디렉터리로 바뀐 이름의 트리를 지우지 않게(반대로 디렉터리를 확인했는데 링크가 됐으면
        #     「1개 삭제 성공」으로 조용히 끝나지 않게). 범위 밖 기대값(변조 env)은 묶지 않는다(제어면도 싣지 않는다).
        rm_kind = target_kind(target) if isinstance(target, str) else None
        expect = env.get("DMS_JR_RM_EXPECT_KIND")
        if expect in RM_TARGET_KINDS and rm_kind != expect:
            lines = (f"{EXECUTION_REASON_MARKER}{TARGET_CHANGED_SINCE_PREVIEW}\n"
                     f"DMS_JR_RM_TARGET_CHANGED target={target!r} expected={expect} now={rm_kind or 'unknown'}\n")
            sys.stdout.write(lines)
            sys.stdout.flush()
            write_text(f"{artifact_dir}/stderr.log", lines)
            write_text(f"{artifact_dir}/summary.json",
                       json.dumps({"returncode": None, "files": None, "bytes": None}))
            return 1

    # 7. mpirun — runuser로 요청자 신원, OMPI env + -x 전파(commands.mpirun_command)
    proc = run(mpirun_command(process_count=process_count, hostfile=hostfile_path,
                              username=username, rank_script=rank_path))
    stdout = proc.stdout or ""
    stderr = getattr(proc, "stderr", None) or ""
    write_text(f"{artifact_dir}/stdout.log", stdout)
    write_text(f"{artifact_dir}/stderr.log", stderr)

    # 8. summary — 도구별 파싱(설계 §3, d192 summary v2). preview/execution 공통 계약(핵심 3키 + 상세 키)이되,
    #    dsync·drm --dryrun 은 복사·삭제 요약을 찍지 않아 walk 항목 수로 대신한다(parsers
    #    .parse_sync_dryrun_counts·parse_rm_dryrun_counts). 제어면은 이 사본을 preview_summary 로 저장하고
    #    지문도 이 객체의 해시다(2026-09-17) -- null 이면 지문이 상수가 된다.
    summary = _build_summary(tool, stdout, proc.returncode, artifact_dir,
                             dryrun="--dryrun" in argv, quiet=("--quiet" in argv or "-q" in argv), stderr=stderr,
                             rm_kind=rm_kind)
    write_text(f"{artifact_dir}/summary.json", json.dumps(summary))

    # 9. 도구 실패(d192 spec §2): 사유 마커를 **먼저**(mpirun 뒤 첫 출력), 이어서 도구 출력 꼬리를 launcher 로그에 남긴다.
    #    summary.json 을 쓴 **뒤**라 여기서 무엇이 잘못돼도 결과는 이미 남았다.
    if proc.returncode != 0:
        _emit(_tool_failure_report(summary.get("reason"), stdout, stderr))
    return proc.returncode


def _tool_failure_report(reason, stdout, stderr):
    """도구가 비 0 으로 끝났을 때 launcher stdout 에 쓸 글: 사유 마커(있을 때만, 첫 줄) + stdout·stderr 꼬리.

    마커는 이 러너가 정한 화이트리스트 값만이다 -- 도구 출력은 줄마다 "  | " 접두라 strip 뒤에도 마커로 시작할 수 없다
    (execution_manifests._parse_marker). splitlines 로 \\r·\\x0b 같은 다른 줄 끝도 나눠 각 조각에 접두를 붙인다.
    글 전체(마커·머리 줄 포함)는 UTF-8 TOOL_OUTPUT_MAX_BYTES 이하다(위 상수 주석) -- 줄 수 상한 안에서 최신 줄부터 담는다."""
    marker = [f"{EXECUTION_REASON_MARKER}{reason}"] if reason in TOOL_FAILURE_REASONS else []
    err_tail = _echo_tail(stderr, TOOL_OUTPUT_STDERR_LINES, TOOL_OUTPUT_STDERR_BYTES)
    used = sum(_utf8_len(line) + 1 for line in marker) + sum(_utf8_len(line) + 1 for line in err_tail)
    used += _TOOL_OUTPUT_HEADER_RESERVE * (2 if err_tail else 1)
    out_tail = _echo_tail(stdout, TOOL_OUTPUT_STDOUT_LINES, TOOL_OUTPUT_MAX_BYTES - used)
    lines = [*marker, f"DMS_JR_TOOL_OUTPUT (stdout.log 마지막 {len(out_tail)}줄)", *out_tail]
    if err_tail:
        lines.append(f"DMS_JR_TOOL_OUTPUT (stderr.log 마지막 {len(err_tail)}줄)")
        lines.extend(err_tail)
    return "\n".join(lines) + "\n"


def _utf8_len(text):
    # 도구 출력은 _subprocess_run 의 replace 디코드라 서로게이트가 없다 -- 그래도 길이 재기가 러너를 죽이지 않게.
    return len(text.encode("utf-8", "replace"))


def _echo_tail(text, max_lines, max_bytes):
    """text 의 마지막 max_lines 줄 중 뒤에서부터(최신부터) 접두·512자 자르기를 한 echo 줄을 max_bytes(줄바꿈 포함) 안에서
    담는다 -- 넘치는 줄에서 멈춘다(사이를 건너뛰지 않는다: 남는 것은 늘 연속한 꼬리다). 반환은 원래 순서."""
    kept = []
    budget = max_bytes
    for line in reversed((text or "").splitlines()[-max_lines:]):
        echoed = TOOL_OUTPUT_PREFIX + line[:TOOL_OUTPUT_LINE_MAX]
        cost = _utf8_len(echoed) + 1
        if cost > budget:
            break
        kept.append(echoed)
        budget -= cost
    kept.reverse()
    return kept


def _emit(text):
    """launcher stdout 에 쓴다(파드 로그). 컨테이너 로케일이 ASCII 여도 죽지 않는다 -- 도구 출력에는 replace 문자(U+FFFD)와
    한글 이름이 섞이고, 여기서 UnicodeEncodeError 가 나면 러너가 rc 대신 트레이스백으로 끝난다. 실패해도 잡 결과(summary·
    rc)는 이미 정해졌으니 삼킨다."""
    try:
        try:
            sys.stdout.write(text)
        except UnicodeEncodeError:
            sys.stdout.write(text.encode("ascii", "backslashreplace").decode("ascii"))
        sys.stdout.flush()
    except Exception:  # noqa: BLE001 -- 진단 출력이 잡 결과를 바꾸지 않는다
        pass


def _scan_report_path(artifact_dir):
    # 도구에 주는 경로($DMS_SCAN_REPORT)와 summary가 읽는 경로는 반드시 같은 식이어야
    # 한다 -- 리터럴이 두 곳에 갈라지면 한쪽만 바뀌어도 테스트는 초록인 채 프로덕션
    # scan 카운트가 조용히 null이 된다(리뷰 M7 뮤테이션).
    return f"{artifact_dir}/dscan-report.json"


class _VirtualClock:
    """시계를 주입하지 않은 호출(테스트)용 -- sleep 한 만큼만 시간이 흐른다. 실제 실행(main)은 time.monotonic
    을 넘긴다: getent·ssh 시도 자체가 수 초씩 걸릴 수 있어(DNS 타임아웃, ConnectTimeout) 실제 경과를 재야 한다."""

    def __init__(self, sleep):
        self._sleep = sleep
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self._sleep(seconds)
        self.now += seconds


def _ready_timeout(env):
    """DMS_JR_WORKER_READY_TIMEOUT_SECONDS 로 덮어쓸 수 있다(양의 정수만, 그 밖은 기본값)."""
    raw = env.get("DMS_JR_WORKER_READY_TIMEOUT_SECONDS")
    try:
        value = int(raw) if raw is not None else WORKER_READY_TIMEOUT_SECONDS
    except ValueError:
        return WORKER_READY_TIMEOUT_SECONDS
    return value if value > 0 else WORKER_READY_TIMEOUT_SECONDS


def _getent_ip(host, *, run):
    """getent hosts 의 첫 칸이 IP 면 그 IP, 아니면 None(못 풂). 이름을 IP 대신 돌려주지 않는다."""
    proc = run(getent_hosts_command(host))
    if getattr(proc, "returncode", 1) != 0:
        return None
    parts = (getattr(proc, "stdout", "") or "").split()
    if not parts:
        return None
    try:
        return str(ipaddress.ip_address(parts[0]))
    except ValueError:
        return None


def _wait_workers_ready(hosts, *, run, sleep, clock, timeout):
    """호스트마다 IP 로 풀릴 때까지(getent), 그 IP 로 ssh 가 될 때까지 재시도한다. 제한 시간은 전체 공유.

    반환 (ips, None) = 전원 준비(호스트 순서 보존 -- nsync 의 rank 순서가 이 순서다), 또는 (지금까지의 ips,
    (host, stage, waited초)) = 제한 초과. stage 는 "resolve"(IP 를 못 받음) | "ssh"(IP 는 있는데 접속 불가).
    한 번 받은 IP 는 다시 풀지 않는다(파드 IP 는 파드 수명 동안 고정)."""
    start = clock()
    deadline = start + timeout
    ips = []
    for host in hosts:
        ip = None
        while True:
            if ip is None:
                ip = _getent_ip(host, run=run)
            if ip is not None and getattr(run(ssh_probe_command(ip)), "returncode", 1) == 0:
                break
            if clock() >= deadline:
                return ips, (host, "resolve" if ip is None else "ssh", int(clock() - start))
            sleep(1)
        ips.append(ip)
        # launcher 로그에 남는다(실패 박제 시 진단) -- 어느 워커가 얼마나 늦었는지.
        print(f"DMS_JR_WORKER_READY host={host} ip={ip} waited={int(clock() - start)}s", flush=True)
    return ips, None


def _build_summary(tool, stdout, returncode, artifact_dir, dryrun=False, quiet=False, stderr="", rm_kind=None):
    """summary.json v2(2026-10-27 d192 spec §1) = 핵심 3키 {"returncode", "files", "bytes"}(모르면 null, 설계 §2.3) +
    머리 키 {"detail_version": 2, "tool", "dryrun", "quiet"(참일 때만)} + drm 의 "target_kind"(rm_kind -- 러너의 lstat,
    2026-10-28) + 상세 키(parsers.parse_tool_detail -- 찾은 것만)
    + "reason"(returncode 가 0 이 아닐 때 _derive_reason 이 정한 경우만). 전부 숫자·참거짓·짧은 ASCII 이고 도구 문자열은
    failure_samples[].path 하나(256자) -- json.dumps 기본 ensure_ascii 라 파일은 ASCII 다(제어면 read 는 strict UTF-8).

    files/bytes 의 뜻은 도구·단계별이다(parsers 모듈 docstring 표): 특히 nsync 실행은 이제 계획 합이 아니라 실제로 반영된
    합(Completed), dsync 실행은 최종 블록의 만든 항목 − 데이터 복사 실패(중단·최종 블록 없음이면 모름), drm 실행은 실제로 지운
    수 -- 통계(sync·rm 합계)의 출처다. 상세의 밑줄 머리 키(사유 판정 근거)는 싣지 않는다.

    파싱이 잡을 죽이는 경로는 없다(설계 §4): 개수 파싱의 어떤 예외든 files/bytes 를 null 로 강등하고, 상세 파싱이 실패하면
    핵심 3키만 쓴다(detail_version 없음 = 상세를 계산하지 못했다 -- reason 도 없다). 제어면 _as_count(bool·비int·음수
    거부)가 2차 방어로 이미 배포되어 있다(d24). 파싱 규칙이 없는 도구(allowlist 밖 -- run_job 은 거기까지 오지 않는다)는
    핵심 3키 그대로다."""
    log_quiet = bool(quiet) and tool in _LOG_PARSED_TOOLS
    files = nbytes = None
    try:
        if log_quiet:
            # -q 면 도구가 아무것도 찍지 않는다(MFU_LOG_NONE -- ERROR 줄까지) -- 개수는 모름이다. 미리보기 쪽은 보안 가드이기도
            # 하다(2026-10-12 보안 검토 -- 불완전 수정 보강): -q 는 진짜 walk 요약을 지우지만 drm 목록(printf)은 그대로 찍혀,
            # 이름에 심은 가짜 walk 줄 하나가 「정확히 1개」를 만족했다. 제어면은 미리보기에 --quiet 를 싣지 않지만
            # (execution_manifests.tool_argv) 옛 제어면(롤링 업그레이드)·DB 변조 argv 에 대비해 여기서도 믿지 않는다.
            pass
        elif tool == "dsync" and dryrun:
            # 미리보기: 복사 요약이 없으므로 소스 walk 항목 수(바이트는 null).
            files, nbytes = parse_sync_dryrun_counts(stdout or "")
        elif tool == "dsync":
            # 중단(MPI_Abort)이면 모름 -- 복사 계획·끝나지 않은 블록을 「반영한 수」로 통계에 넣지 않는다(d192 D6).
            files, nbytes = parse_dsync_counts(stdout or "", aborted=parse_aborted(stderr))
        elif tool == "nsync":
            # nsync는 mpifileutils가 아니라 역할 기반 별도 도구라 출력 형식이
            # 완전히 다르다 -- dsync 파서로 보내면 항상 null이었다(설계 부록 A).
            files, nbytes = parse_nsync_counts(stdout or "", dryrun=bool(dryrun))
        elif tool == "drm" and dryrun:
            # 미리보기: 삭제 요약이 없으므로 walk 항목 수(= 지울 수, 2026-10-12 -- 늘 「삭제 대상 모름」이던 것).
            files, nbytes = parse_rm_dryrun_counts(stdout or "")
        elif tool == "drm":
            files, nbytes = parse_rm_counts(stdout or "")
        elif tool == "dscan":
            files, nbytes = parse_scan_counts(_scan_report_path(artifact_dir))
        # 그 외 도구: 파싱 규칙이 없다 -- (None, None) 그대로 둔다
    except Exception:  # noqa: BLE001 -- fail-soft가 계약이다(설계 §4)
        files = nbytes = None
    core = {"returncode": returncode, "files": files, "bytes": nbytes}
    if tool not in ALLOWED_TOOLS:
        return core
    # rm 대상 종류(2026-10-28, run_job 6d -- 러너의 lstat, 도구 출력이 아니다). drm 만, 알려진 값일 때만. 상세 파싱이 실패한
    # 핵심 3키 경로에도 싣는다 -- 미리보기 종류 묶기의 유일한 재료라 도구 출력 파싱에 기대지 않는다(d194 리뷰).
    if tool == "drm" and rm_kind in RM_TARGET_KINDS:
        core["target_kind"] = rm_kind
    try:
        if log_quiet:
            # 상세도 모름 -- quiet 에서도 남는 것은 MPI 중단 흔적(stderr)뿐이다.
            detail = {"aborted": True} if parse_aborted(stderr) else {}
        else:
            detail = parse_tool_detail(tool, stdout, stderr, bool(dryrun))
        if not isinstance(detail, dict):
            return core
        summary = dict(core)
        summary["detail_version"] = SUMMARY_DETAIL_VERSION
        summary["tool"] = tool
        summary["dryrun"] = bool(dryrun)
        if quiet:
            summary["quiet"] = True
        for key, value in detail.items():
            # 밑줄 머리 키는 사유 판정용 내부 근거(parsers 모듈 docstring) -- summary 계약 밖이라 싣지 않는다.
            if key not in summary and key != "reason" and not key.startswith("_"):
                summary[key] = value
        if returncode is not None and returncode != 0:
            reason = _derive_reason(tool, detail, dryrun=bool(dryrun))
            if reason is not None:
                summary["reason"] = reason
        return summary
    except Exception:  # noqa: BLE001 -- 상세는 덤이다: 실패하면 핵심 3키만(spec §1 fail-soft)
        return core


def _positive(value) -> bool:
    """정수이고 0 보다 큰가 -- null(모름)·0(정상값)·bool·이상한 모양은 전부 아니다."""
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _derive_reason(tool, detail, *, dryrun=False):
    """도구가 0 이 아닌 코드로 끝났을 때의 사유(d192 spec §2 표 -- 처음 맞는 규칙, 리뷰 결정 D1-D4 반영). 없으면 None(스테퍼가
    execution_failed / preview_failed 로 접는다).

    미리보기(dryrun)는 tool_aborted 와 미리보기 전용 preview_scan_incomplete(nsync 만) 만 정한다: 나머지 사유의 문구는 실행
    결과라 아무것도 바꾸지 않은 미리보기 실패에 붙이면 거짓이다. nsync 미리보기에서 실제로 생기는 도구 실패는 스캔 오류(비 root
    의 읽을 수 없는 하위 디렉토리, --contents 의 읽을 수 없는 파일)다 -- 소스·목적지 어느 쪽이든(문구가 쪽을 말하지 않는다).
    dsync 의 walk 오류는 종료 코드를 바꾸지 않아(dsync.c walk_rc 는 delete 만 끈다) 실패의 원인이 될 수 없다 -- dsync 에는 스캔
    사유(미리보기·규칙 6)를 붙이지 않는다.

    errno 규칙(2·3)은 sync 도구만이다: rm 에는 「목적지」가 없고 drm 은 멈추지 않고 전부 시도한 뒤 Remove failed 를 낸다(규칙 8
    의 문구가 맞다). 남는 위험(d192 리뷰 contract-35): dsync 는 경로를 날것으로 찍어 이름에 심은 가짜 실패 줄(errno=28·30)이
    샘플에 **더해질** 수 있다 -- 그러면 진짜 원인 대신 destination_full/read_only 가 될 수 있다. 바뀌는 것은 사유 문구뿐이고
    (데이터·권한은 그대로, 진짜 실패 줄도 stdout.log 에 남는다) 이름을 지을 수 있는 사람 자신의 잡이라 받아들인다. nsync 는
    경로를 이스케이프해 이 위조가 없다."""
    if detail.get("aborted") is True:
        return TOOL_ABORTED
    failures = detail.get("failures") if isinstance(detail.get("failures"), dict) else {}
    by_op = failures.get("by_op") if isinstance(failures.get("by_op"), dict) else {}
    if dryrun:
        if tool == "nsync" and (_positive(detail.get("scan_errors")) or _positive(by_op.get("scan"))):
            return PREVIEW_SCAN_INCOMPLETE
        return None
    if tool in _SYNC_TOOLS:
        samples = detail.get("failure_samples")
        errnos = {s.get("errno") for s in samples if isinstance(s, dict)} if isinstance(samples, list) else set()
        stop_cause = detail.get("stop_cause")
        if stop_cause == "destination_full" or errnos & {28, 122}:          # ENOSPC, EDQUOT
            return DESTINATION_FULL
        if stop_cause == "destination_read_only" or 30 in errnos:            # EROFS
            return DESTINATION_READ_ONLY
        if stop_cause == "max_errors":
            return SYNC_STOPPED_MAX_ERRORS
        kinds = {op for op, n in by_op.items() if _positive(n)}
        refused = detail.get("type_change_refused")
        copy_failed = detail.get("copy_failed")
        create_failed = detail.get("create_failed")
        remove_failed = detail.get("remove_failed")
        scan_errors = detail.get("scan_errors")
        if _positive(refused) and _type_change_only(tool, detail, kinds, refused):
            return SYNC_TYPE_CHANGE_REFUSED
        # 6. 소스 스캔 오류만(nsync): 소스 쪽 오류가 --delete 를 실제로 껐고(그 줄이 있다) 스캔 오류가 전부 소스 쪽일 때만 --
        #    목적지 쪽 읽기 오류는 delete 를 끄지 않는다(그 실행의 삭제는 돌았다). delete 없이 돈 실행은 nsync 가 어느 쪽인지
        #    말하지 않아 「소스」를 확인할 수 없다 -- 일반 항목 실패다(d192 D2).
        if (tool == "nsync" and _positive(scan_errors) and kinds <= {"scan"}
                and detail.get("delete_disabled") is True
                and _positive(detail.get("source_scan_errors")) and detail.get("source_scan_errors") == scan_errors):
            return SOURCE_SCAN_INCOMPLETE
        # 7. 그 밖의 항목 실패. dsync 의 scan_incomplete 는 넣지 않는다(종료 코드를 바꾸지 않는다 -- 위 docstring): 그것만으로는
        #    비 0 의 원인을 모른다(일반 execution_failed 가 정직하다).
        if (_positive(failures.get("total")) or _positive(copy_failed) or _positive(create_failed) or _positive(refused)
                or _positive(remove_failed) or _positive(scan_errors)):
            return SYNC_ITEMS_FAILED
    if tool == "drm" and _positive(detail.get("remove_failed")):
        return RM_ITEMS_FAILED
    return None


# 「비어 있지 않은 디렉토리라 지우지 못했다」로 볼 수 있는 제거 errno: ENOTEMPTY(39), 일부 파일시스템의 EEXIST(17).
_ERRNO_DIR_NOT_EMPTY = frozenset({39, 17})


def _type_change_only(tool, detail, kinds, refused):
    """규칙 5(sync_type_change_refused -- 문구: 「비어 있지 않은 디렉토리를 바꾸지 않았습니다(삭제 옵션 꺼짐) -- 삭제를 켜면 바뀝
    니다」)의 조건. 그 문구가 참일 때만이다(d192 D1 -- 권한 오류·삭제가 이미 켜진 실행에 「삭제를 켜라」를 권하면 파괴적 옵션을
    헛되이 권하는 셈이다).

    nsync: 실패가 replace-dir 뿐이고, nsync 가 「--delete 를 주지 않으면 비어 있지 않은 디렉토리는 바뀌지 않는다」 줄을 찍었을 때
    (!opts.delete 일 때만 찍는다 -- --delete 인데 재귀 제거가 ENOTEMPTY 로 끝난 경우는 아니다).
    dsync: 막힌 수(N destination item(s) changed type …) == 「(still a directory), a non-empty directory is replaced only with
    --delete」 줄 수(남은 것이 디렉토리 + delete 꺼짐일 때만 붙는다)이고, 제거 단계 errno 가 전부 39·17(EACCES·EPERM 이면 삭제를
    켜도 안 바뀐다)이며, 다른 실패(복사·만들기 실패, 소스 walk 오류, 막힌 수보다 많은 제거 실패)가 없을 때."""
    if tool == "nsync":
        return kinds <= {"replace-dir"} and detail.get("type_change_needs_delete") is True
    remove_errnos = detail.get("_remove_errnos")
    remove_failed = detail.get("remove_failed")
    # 막힌 타입 교체는 그 제거 실패(Remove failed)로도 세진다 -- 그보다 많은 제거 실패는 다른 항목의 실패다.
    removes_ok = remove_failed is None or remove_failed == 0 or (_positive(remove_failed) and remove_failed <= refused)
    return (_positive(detail.get("_type_change_dir_refused")) and detail.get("_type_change_dir_refused") == refused
            and isinstance(remove_errnos, list) and set(remove_errnos) <= _ERRNO_DIR_NOT_EMPTY
            and not _positive(detail.get("copy_failed")) and not _positive(detail.get("create_failed"))
            and detail.get("scan_incomplete") is not True and removes_ok)


def _shquote(s):
    import shlex
    return shlex.quote(str(s))


def _subprocess_run(command):
    """자식 프로세스 출력의 유일한 디코드 지점(main 의 run). 도구는 파일 이름 바이트를 그대로 찍는다 -- dscan --print 의
    broken 목록, drm --dryrun 의 항목 목록, 오류·경고 줄. 2026-10-20 프로덕션: CP949 이름(「다운로드」 b4 d9 …) 하나가
    strict 디코드에서 UnicodeDecodeError 를 던져, 도구는 전부 처리하고 rc 0 으로 끝났는데 러너가 죽어 잡이 Failed 였다
    (rm 은 미리보기에서 죽어 컨펌조차 못 했다). errors="replace" 인 이유: 결과 str 은 로그 파일(_write_text)·summary·
    제어면 DB/JSON 으로 흐르는데 surrogateescape 의 서로게이트는 그 셋 모두에서 UnicodeEncodeError 다. replace 는
    ASCII(줄바꿈·따옴표)를 삼키지 않아 파서의 줄 머리 앵커·walk 줄 개수 방어가 그대로다. text 모드(줄바꿈 정규화)도
    그대로. 정확한 원래 이름 바이트는 리포트 다운로드에 남는다."""
    import subprocess
    return subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")


def _write_text(path, content, *, append=False):
    """main 의 write_text. encoding 명시(로케일과 무관하게 UTF-8). 내용은 _subprocess_run(replace)·json.dumps(ASCII)
    산출이라 strict 로 인코드된다 -- 여기서 errors 를 느슨하게 하지 않는다(서로게이트가 섞였다면 위 디코드의 회귀다)."""
    os.makedirs(os.path.dirname(path), exist_ok=True) if "/" in path[1:] else None
    with open(path, "a" if append else "w", encoding="utf-8") as f:
        f.write(content)


def main():  # pragma: no cover - 실증에서 실행
    import time

    run = _subprocess_run
    write_text = _write_text

    def read_text(path):
        try:
            with open(path) as f:
                return f.read()
        except OSError:
            return ""

    def wait_hostfile(role=None):
        # Volcano ssh plugin이 /etc/volcano/<task>.host 또는 VC_*_HOSTS 제공.
        # nsync는 source-worker/destination-worker 두 task의 hostfile을 각각 기다린다.
        # Volcano's svc plugin names the per-task hostfile after the task with
        # hyphens converted to UNDERSCORES: task "source-worker" ->
        # /etc/volcano/source_worker.host (verified on the testbed). Reading the
        # hyphenated path yields an empty hostfile -> mpirun "no nodes available".
        env_var = {"source": "DMS_JR_SOURCE_HOSTFILE",
                   "destination": "DMS_JR_DEST_HOSTFILE"}.get(role, "DMS_JR_HOSTFILE")
        default_path = {"source": "/etc/volcano/source_worker.host",
                        "destination": "/etc/volcano/destination_worker.host"}.get(
                            role, "/etc/volcano/worker.host")
        hostfile = os.environ.get(env_var, default_path)
        for _ in range(60):
            if os.path.exists(hostfile):
                with open(hostfile) as f:
                    hosts = [ln.split()[0] for ln in f if ln.strip()]
                if hosts:
                    return hosts, hostfile
            time.sleep(1)
        return [], hostfile

    def make_executable(path):
        os.chmod(path, 0o755)

    sys.exit(run_job(dict(os.environ), run=run, write_text=write_text,
                     read_text=read_text, sleep=time.sleep, wait_hostfile=wait_hostfile,
                     make_executable=make_executable, clock=time.monotonic))
