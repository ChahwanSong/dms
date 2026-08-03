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


def mpirun_command(*, process_count, hostfile, username, rank_script):
    return ["runuser", "-u", username, "--",
            "mpirun", "--allow-run-as-root",
            "--mca", "pml", "ob1", "--mca", "btl", "tcp,self",
            "-np", str(process_count), "--hostfile", hostfile, rank_script]


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
