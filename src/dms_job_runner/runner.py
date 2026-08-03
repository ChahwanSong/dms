"""잡 파드 launcher에서 도는 오케스트레이션. 모든 I/O는 주입 — main()이 실제 구현을 넣는다."""
import json
import os
import sys

from .commands import (
    getent_hosts_command, mpirun_command, passwd_line,
    ssh_key_copy_command, ssh_probe_command)

_SSH_READY_MAX_ATTEMPTS = 90  # legacy _mpiexec_line 이식: 워커당 ~90s 상한


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

    # 1. identity 물질화 (launcher 자신의 /etc/passwd)
    write_text("/etc/passwd", passwd_line(username, uid, gid, home) + "\n", append=True)

    # 2. launcher의 /root/.ssh(Volcano ssh 플러그인이 물질화)를 요청자 home으로 복사.
    #    mpirun을 runuser로 실행해 SSH 나갈 때 요청자 home의 클라이언트 키가 필요.
    run(ssh_key_copy_command(home, uid, gid))

    # 3. hostfile 대기
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

    # 6. rank script — scan은 리포트 경로 치환
    report_path = f"{artifact_dir}/dscan-report.json"
    rendered = [report_path if a == "$DMS_SCAN_REPORT" else a for a in argv]
    rank_body = " ".join(_shquote(a) for a in [tool, *rendered])
    rank_path = f"{artifact_dir}/rank.sh"
    write_text(rank_path, f"#!/bin/sh\nexec {rank_body}\n")
    make_executable(rank_path)

    # 7. mpirun — runuser로 요청자 신원, OMPI env + -x 전파(commands.mpirun_command)
    proc = run(mpirun_command(process_count=process_count, hostfile=hostfile_path,
                              username=username, rank_script=rank_path))
    write_text(f"{artifact_dir}/stdout.log", proc.stdout or "")
    write_text(f"{artifact_dir}/stderr.log", proc.stderr or "")

    # 8. summary
    summary = _summary_from_stdout(proc.stdout, proc.returncode)
    write_text(f"{artifact_dir}/summary.json", json.dumps(summary))
    return proc.returncode


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


def _summary_from_stdout(stdout, returncode):
    last = (stdout or "").strip().splitlines()
    if last:
        try:
            return json.loads(last[-1])
        except (ValueError, TypeError):
            pass
    return {"returncode": returncode}


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

    def wait_hostfile():
        # Volcano ssh plugin이 /etc/volcano/<task>.host 또는 VC_*_HOSTS 제공
        hostfile = os.environ.get("DMS_JR_HOSTFILE", "/etc/volcano/worker.host")
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
