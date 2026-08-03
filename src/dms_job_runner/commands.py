"""잡 파드 안에서 도는 job-runner의 순수 헬퍼. 실제 실행은 runner.main (Task 10)."""


def passwd_line(username, uid, gid, home):
    return f"{username}:x:{uid}:{gid}:dms:{home}:/bin/sh"


def parse_hostfile(text):
    hosts = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        hosts.append(line.split()[0])
    return hosts


_OMPI_ENV = [
    ("OMPI_ALLOW_RUN_AS_ROOT", "1"),
    ("OMPI_ALLOW_RUN_AS_ROOT_CONFIRM", "1"),
    ("OMPI_MCA_plm_rsh_agent",
     "ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"),
]
_DEFAULT_ENV_EXPORTS = ("PATH", "LD_LIBRARY_PATH")


def mpirun_command(*, process_count, hostfile, username, rank_script,
                    env_exports=_DEFAULT_ENV_EXPORTS):
    """mpirun을 요청자 신원(runuser)으로 실행. legacy _mpiexec_line 이식:
    OMPI_ALLOW_RUN_AS_ROOT(런처가 root라서 필요), OMPI_MCA_plm_rsh_agent(호스트키
    미확인 SSH), `env NAME=val ...`로 셸 없이 값 주입 후 `runuser --preserve-environment`
    로 그 값들을 요청자 프로세스까지 보존, `-x`로 원격 rank에 PATH 등 전파."""
    env_args = [f"{k}={v}" for k, v in _OMPI_ENV]
    x_flags = []
    for name in env_exports:
        x_flags += ["-x", name]
    return [
        "env", *env_args,
        "runuser", "-u", username, "--preserve-environment", "--",
        "mpirun", "--allow-run-as-root",
        "--mca", "pml", "ob1", "--mca", "btl", "tcp,self",
        "-np", str(process_count), "--hostfile", hostfile,
        *x_flags, rank_script,
    ]


def ssh_key_copy_command(home, uid, gid):
    """launcher 자신의(Volcano ssh 플러그인이 물질화한) /root/.ssh를 요청자 home으로
    복사·chown. mpirun이 runuser로 SSH 나갈 때 요청자 home의 클라이언트 키가 필요.
    home은 파생값이지만 positional 파라미터로만 참조 — 셸 인젝션 원천 차단."""
    script = (
        'set -e; '
        'mkdir -p "$1/.ssh"; '
        'if [ -d /root/.ssh ]; then cp -a /root/.ssh/. "$1/.ssh/" 2>/dev/null || true; fi; '
        'chown -R "$2:$3" "$1/.ssh" 2>/dev/null || true; '
        'chmod 0700 "$1/.ssh"; '
        'chmod 0600 "$1/.ssh"/* 2>/dev/null || true'
    )
    return ["sh", "-c", script, "sh", home, str(uid), str(gid)]


def ssh_probe_command(host):
    """host가 SSH를 수락하는지 논-인터랙티브로 확인(readiness barrier에 사용)."""
    return ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null", "-o", "ConnectTimeout=3",
            host, "true"]


def getent_hosts_command(host):
    """hostfile의 원시 호스트명을 IP로 해석(Volcano svc plugin이 뒤늦게 DNS를 채움)."""
    return ["getent", "hosts", host]


def nsync_role_map(source_hosts, dest_hosts, *, slots_per_host):
    ranks = []
    rank = 0
    for _ in source_hosts:
        for _ in range(slots_per_host):
            ranks.append(f"{rank}:src")
            rank += 1
    for _ in dest_hosts:
        for _ in range(slots_per_host):
            ranks.append(f"{rank}:dst")
            rank += 1
    return ",".join(ranks)
