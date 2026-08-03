from dms_job_runner.commands import (
    passwd_line, parse_hostfile, mpirun_command, nsync_role_map)


def test_passwd_line():
    assert passwd_line("alice", 10001, 10000, "/tmp/h") == \
        "alice:x:10001:10000:dms:/tmp/h:/bin/sh"


def test_parse_hostfile():
    text = "dms-w1 slots=8\n# comment\n\ndms-w2 slots=8\n"
    assert parse_hostfile(text) == ["dms-w1", "dms-w2"]


def test_mpirun_command():
    cmd = mpirun_command(process_count=16, hostfile="/tmp/hf", username="alice",
                         rank_script="/tmp/rank.sh")
    assert cmd[:3] == ["runuser", "-u", "alice"]
    assert "mpirun" in cmd and cmd[cmd.index("-np") + 1] == "16"
    assert cmd[-1] == "/tmp/rank.sh"
    assert cmd[cmd.index("--hostfile") + 1] == "/tmp/hf"


def test_nsync_role_map():
    rm = nsync_role_map(["s1", "s2"], ["d1"], slots_per_host=2)
    # source 2호스트×2slots = rank 0..3 = src, dest 1호스트×2slots = rank 4..5 = dst
    assert rm == "0:src,1:src,2:src,3:src,4:dst,5:dst"
