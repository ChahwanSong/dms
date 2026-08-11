"""잡 파드 launcher에서 도는 오케스트레이션. 모든 I/O는 주입 — main()이 실제 구현을 넣는다."""
import json
import os
import sys

from .commands import (
    getent_hosts_command, mpirun_command, nsync_role_map, passwd_line,
    ssh_key_copy_command, ssh_probe_command)
from .parsers import (parse_nsync_counts, parse_rm_counts, parse_scan_counts,
                      parse_sync_counts)

_SSH_READY_MAX_ATTEMPTS = 90  # legacy _mpiexec_line 이식: 워커당 ~90s 상한

# 슬라이스 24 §2.1 층3: 러너가 exec 할 수 있는 도구의 최종 allowlist.
# dms 패키지의 config.AGENT_TOOL_NAMES 와 같은 값이어야 하지만 dms_job_runner 는
# dms 를 import 하지 않는 독립 패키지(잡 이미지에 단독 설치)라 여기 중복 정의한다
# -- 동일성은 tests/test_job_runner_runner.py 의 계약 테스트가 강제한다.
# rank.sh 가 `exec {tool}` 이라 명령 이름 자체가 tool 값이다: 층1(스테퍼)·층2
# (tool_argv)가 뚫려도 -- 이미 제출된 매니페스트/env 의 사후 변조까지 포함해 --
# 이 층만은 실행을 막는다.
ALLOWED_TOOLS = ("dscan", "dsync", "nsync", "drm")


def run_job(env, *, run, write_text, read_text, sleep, wait_hostfile,
            make_executable=lambda path: None) -> int:
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

    # 4. 원시 호스트명을 getent로 IP 해석 + slots=<procs_per_node> 첨부한 새 hostfile 생성
    #    (legacy _mpi_hostfile_lines 개념 — Volcano svc plugin의 DNS 전파를 기다린다).
    resolved_hosts = [_resolve_host(h, run=run) for h in ordered_hosts]
    hostfile_path = f"{artifact_dir}/mpi-hostfile"
    write_text(hostfile_path,
              "".join(f"{h} slots={procs_per_node}\n" for h in resolved_hosts))

    # 5. SSH-readiness barrier: 모든 worker가 SSH를 수락할 때까지 bounded 대기.
    #    준비되지 않아도 job을 막지 않고 경고 후 진행(legacy와 동일 — mpirun 자체의
    #    재시도/타임아웃에 맡긴다).
    _wait_ssh_ready(resolved_hosts, run=run, sleep=sleep)

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

    # 7. mpirun — runuser로 요청자 신원, OMPI env + -x 전파(commands.mpirun_command)
    proc = run(mpirun_command(process_count=process_count, hostfile=hostfile_path,
                              username=username, rank_script=rank_path))
    write_text(f"{artifact_dir}/stdout.log", proc.stdout or "")
    write_text(f"{artifact_dir}/stderr.log", proc.stderr or "")

    # 8. summary — 도구별 파싱(설계 §3). preview/execution 공통: phase 분기가
    #    없어 preview의 files/bytes도 dryrun 예상치로 실린다(set_preview는 무시).
    summary = _build_summary(tool, proc.stdout, proc.returncode, artifact_dir)
    write_text(f"{artifact_dir}/summary.json", json.dumps(summary))
    return proc.returncode


def _scan_report_path(artifact_dir):
    # 도구에 주는 경로($DMS_SCAN_REPORT)와 summary가 읽는 경로는 반드시 같은 식이어야
    # 한다 -- 리터럴이 두 곳에 갈라지면 한쪽만 바뀌어도 테스트는 초록인 채 프로덕션
    # scan 카운트가 조용히 null이 된다(리뷰 M7 뮤테이션).
    return f"{artifact_dir}/dscan-report.json"


def _resolve_host(host, *, run):
    proc = run(getent_hosts_command(host))
    stdout = getattr(proc, "stdout", "") or ""
    parts = stdout.split()
    return parts[0] if parts else host


def _wait_ssh_ready(hosts, *, run, sleep, max_attempts=_SSH_READY_MAX_ATTEMPTS):
    for host in hosts:
        attempts = 0
        while attempts < max_attempts:
            proc = run(ssh_probe_command(host))
            if getattr(proc, "returncode", 1) == 0:
                break
            attempts += 1
            sleep(1)


def _build_summary(tool, stdout, returncode, artifact_dir):
    """summary.json은 항상 정확히 3키 {"returncode", "files", "bytes"} -- 모르면
    null(설계 §2.3). 기존 "마지막 줄 JSON" 계약은 실 mpifileutils가 JSON을 찍지
    않아 사문이라 제거했다. 파싱이 잡을 죽이는 경로는 없다(설계 §4): 어떤 예외든
    삼키고 returncode만 보존한 채 files/bytes를 null로 강등한다 -- 제어면
    _as_count(bool·비int·음수 거부)가 2차 방어로 이미 배포되어 있다(d24)."""
    files = nbytes = None
    try:
        if tool == "dsync":
            files, nbytes = parse_sync_counts(stdout or "")
        elif tool == "nsync":
            # nsync는 mpifileutils가 아니라 역할 기반 별도 도구라 출력 형식이
            # 완전히 다르다 -- dsync 파서로 보내면 항상 null이었다(설계 부록 A).
            files, nbytes = parse_nsync_counts(stdout or "")
        elif tool == "drm":
            files, nbytes = parse_rm_counts(stdout or "")
        elif tool == "dscan":
            files, nbytes = parse_scan_counts(_scan_report_path(artifact_dir))
        # 그 외 도구: 파싱 규칙이 없다 -- (None, None) 그대로 둔다
    except Exception:  # noqa: BLE001 -- fail-soft가 계약이다(설계 §4)
        files = nbytes = None
    return {"returncode": returncode, "files": files, "bytes": nbytes}


def _shquote(s):
    import shlex
    return shlex.quote(str(s))


def main():  # pragma: no cover - 실증에서 실행
    import subprocess
    import time

    def run(command):
        return subprocess.run(command, capture_output=True, text=True)

    def write_text(path, content, *, append=False):
        os.makedirs(os.path.dirname(path), exist_ok=True) if "/" in path[1:] else None
        with open(path, "a" if append else "w") as f:
            f.write(content)

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
                     make_executable=make_executable))
