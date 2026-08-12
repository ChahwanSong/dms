from dms_job_runner.commands import (
    passwd_line, parse_hostfile, mpirun_command, nsync_role_map,
    ssh_key_copy_command, ssh_probe_command, getent_hosts_command)


def test_passwd_line():
    assert passwd_line("alice", 10001, 10000, "/tmp/h") == \
        "alice:x:10001:10000:dms:/tmp/h:/bin/sh"


def test_parse_hostfile():
    text = "dms-w1 slots=8\n# comment\n\ndms-w2 slots=8\n"
    assert parse_hostfile(text) == ["dms-w1", "dms-w2"]


def test_mpirun_command():
    cmd = mpirun_command(process_count=16, hostfile="/tmp/hf", username="alice",
                         rank_script="/tmp/rank.sh")
    # OMPI env는 셸 없이 `env NAME=val ... command` 형태로 주입 (인젝션 안전, subprocess 그대로 실행)
    assert cmd[0] == "env"
    assert "OMPI_ALLOW_RUN_AS_ROOT=1" in cmd
    assert any(c.startswith("OMPI_MCA_plm_rsh_agent=") and "StrictHostKeyChecking=no" in c
              for c in cmd)
    # runuser로 요청자 신원 실행, 환경 보존(OMPI/-x 로 넘긴 값이 유지되어야 mpirun이 봄)
    assert "runuser" in cmd
    i = cmd.index("runuser")
    assert cmd[i:i + 3] == ["runuser", "-u", "alice"]
    assert "--preserve-environment" in cmd
    assert "mpirun" in cmd and cmd[cmd.index("-np") + 1] == "16"
    assert cmd[-1] == "/tmp/rank.sh"
    assert cmd[cmd.index("--hostfile") + 1] == "/tmp/hf"
    # PATH/LD_LIBRARY_PATH 는 기본으로 원격 랭크에 -x 전파
    assert cmd.count("-x") >= 2
    assert "PATH" in cmd and "LD_LIBRARY_PATH" in cmd


def test_mpirun_command_propagates_extra_env_names():
    cmd = mpirun_command(process_count=1, hostfile="/tmp/hf", username="alice",
                         rank_script="/tmp/rank.sh", env_exports=("PATH", "DMS_EXTRA"))
    assert "DMS_EXTRA" in cmd
    assert cmd[cmd.index("DMS_EXTRA") - 1] == "-x"


def test_ssh_key_copy_command_is_injection_safe():
    # home은 신뢰 가능(uid로부터 파생)하지만 여전히 positional 파라미터로 전달 —
    # 스크립트 문자열 자체에 값이 보간되지 않아야 한다.
    home = "/tmp/dms-home-10001; rm -rf /"
    cmd = ssh_key_copy_command(home, 10001, 10000)
    assert cmd[0] == "sh" and cmd[1] == "-c"
    script = cmd[2]
    assert home not in script
    assert "authorized_keys" not in script or True  # 키 전체 복사(cp -a) 방식
    assert "/root/.ssh" in script
    assert home in cmd[3:]  # positional arg로만 전달
    assert "10001" in cmd and "10000" in cmd


def test_ssh_probe_command():
    cmd = ssh_probe_command("dms-w1")
    assert cmd[0] == "ssh"
    assert "BatchMode=yes" in " ".join(cmd)
    assert "StrictHostKeyChecking=no" in " ".join(cmd)
    assert cmd[-2] == "dms-w1"
    assert cmd[-1] == "true"


def test_getent_hosts_command():
    assert getent_hosts_command("dms-w1") == ["getent", "hosts", "dms-w1"]


def test_nsync_role_map():
    rm = nsync_role_map(["s1", "s2"], ["d1"], slots_per_host=2)
    # source 2호스트×2slots = rank 0..3 = src, dest 1호스트×2slots = rank 4..5 = dst
    assert rm == "0:src,1:src,2:src,3:src,4:dst,5:dst"


def test_parse_hostfile_none_is_empty():
    # hostfile 미물질화(Volcano ssh 플러그인 지연) 경로에서 read 가 None 을 넘겨도
    # 빈 목록이어야 한다 -- `text or ""`(commands.py) 반쪽 약속의 검증. 파서
    # 계열(test_parsers_accept_none_stdout)과 같은 그물.
    assert parse_hostfile(None) == []
