"""잡 파드 launcher에서 도는 오케스트레이션. 모든 I/O는 주입 — main()이 실제 구현을 넣는다."""
import json
import os
import sys

from .commands import mpirun_command, passwd_line


def run_job(env, *, run, write_text, read_text, sleep, wait_hostfile) -> int:
    username = env["DMS_JR_USERNAME"]
    uid = int(env["DMS_JR_UID"])
    gid = int(env["DMS_JR_GID"])
    home = f"/tmp/dms-home-{uid}"
    artifact_dir = env["DMS_JR_ARTIFACT_DIR"]
    process_count = int(env["DMS_JR_PROCESS_COUNT"])
    argv = json.loads(env["DMS_JR_ARGV"])

    # 1. identity 물질화
    write_text("/etc/passwd", passwd_line(username, uid, gid, home) + "\n", append=True)

    # 2. hostfile 대기
    hosts, hostfile = wait_hostfile()

    # 3. rank script — scan은 리포트 경로 치환
    report_path = f"{artifact_dir}/dscan-report.json"
    rendered = [report_path if a == "$DMS_SCAN_REPORT" else a for a in argv]
    tool = env["DMS_JR_TOOL"]
    rank_body = " ".join(_shquote(a) for a in [tool, *rendered])
    rank_path = f"{artifact_dir}/rank.sh"
    write_text(rank_path, f"#!/bin/sh\nexec {rank_body}\n")

    # 4. mpirun
    proc = run(mpirun_command(process_count=process_count, hostfile=hostfile,
                              username=username, rank_script=rank_path))
    write_text(f"{artifact_dir}/stdout.log", proc.stdout or "")
    write_text(f"{artifact_dir}/stderr.log", proc.stderr or "")

    # 5. summary
    summary = _summary_from_stdout(proc.stdout, proc.returncode)
    write_text(f"{artifact_dir}/summary.json", json.dumps(summary))
    return proc.returncode


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

    sys.exit(run_job(dict(os.environ), run=run, write_text=write_text,
                     read_text=read_text, sleep=time.sleep, wait_hostfile=wait_hostfile))
