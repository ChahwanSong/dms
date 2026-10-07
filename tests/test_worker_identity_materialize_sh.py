"""워커(sshd) 컨테이너의 신원·보조 그룹 물질화를 **실제 sh**(이 호스트 /bin/sh = dash)로 돌린다(2026-10-07 D13).

문자열 계약만으로는 `set -eu` 아래 case 가드·AND-OR 목록·명령 치환 실패의 상호작용을 증명할 수 없다 -- 그래서
_identity_materialize_stmt 를 임시 passwd/group/home 경로로 바꿔 끼워 실행한다. 컨테이너의 `id`·`getent` 는 PATH
앞의 심(shim)이 임시 passwd/group 에서 계산해 흉내 낸다(T_UID 로 id -u, T_DROP/T_EXTRA 로 id -G 결과를 빼거나
더해 '클러스터·이미지가 그룹을 바꾼' 경우를 만든다). 셸 조각은 계획 단계에서 probe/shcheck2.py 로 dash 실측한
그대로다.
"""
import os
import subprocess
import sys

import pytest

from dms.execution_manifests import _identity_materialize_stmt

_ID_SHIM = r'''import os, sys
pw, gr = os.environ["T_PASSWD"], os.environ["T_GROUP"]
drop = os.environ.get("T_DROP", "")
extra = os.environ.get("T_EXTRA", "")
a = sys.argv[1:]
if a == ["-u"]:
    print(os.environ.get("T_UID", "0")); sys.exit(0)
if len(a) == 2 and a[0] == "-G":
    user = a[1]; prim = None
    for line in open(pw):
        f = line.rstrip("\n").split(":")
        if f[0] == user: prim = f[3]
    if prim is None: sys.exit(1)
    gs = [prim]
    for line in open(gr):
        f = line.rstrip("\n").split(":")
        if len(f) >= 4 and user in f[3].split(",") and f[2] not in gs and f[2] != drop: gs.append(f[2])
    if extra: gs.append(extra)
    print(" ".join(gs)); sys.exit(0)
sys.exit(2)
'''
_GETENT_SHIM = r'''import os, sys
if sys.argv[1] != "passwd": sys.exit(2)
for line in open(os.environ["T_PASSWD"]):
    if line.split(":")[0] == sys.argv[2]:
        sys.stdout.write(line); sys.exit(0)
sys.exit(2)
'''
_GROUP_SEED = "root:x:0:\nusers:x:100:\n"
_ALICE = {"DMS_JR_USERNAME": "alice", "DMS_JR_UID": "10001", "DMS_JR_GID": "10000"}
_MARKER = "DMS_EXEC_REASON=identity_groups_not_applied"


class _Box:
    """임시 passwd/group/home + id·getent 심. run() 은 (rc, stdout, stderr)."""

    def __init__(self, tmp_path, passwd=""):
        self.dir = tmp_path
        bindir = tmp_path / "bin"
        bindir.mkdir()
        for name, body in (("id", _ID_SHIM), ("getent", _GETENT_SHIM)):
            p = bindir / name
            p.write_text(f"#!{sys.executable}\n{body}")
            p.chmod(0o755)
        self.passwd = tmp_path / "passwd"
        self.group = tmp_path / "group"
        self.passwd.write_text(passwd)
        self.group.write_text(_GROUP_SEED)
        self.home = tmp_path / "home"
        self.home.mkdir()
        self.script = _identity_materialize_stmt(passwd=str(self.passwd), group=str(self.group),
                                                 home=f"{self.home}/dms-home-")

    def run(self, **env):
        e = {"PATH": f"{self.dir / 'bin'}:{os.environ['PATH']}",
             "T_PASSWD": str(self.passwd), "T_GROUP": str(self.group), **env}
        proc = subprocess.run(["sh", "-c", "set -eu\n" + self.script], env=e,
                              capture_output=True, text=True)
        return proc.returncode, proc.stdout, proc.stderr

    def dmsg_lines(self):
        return [line for line in self.group.read_text().splitlines() if line.startswith("dmsg")]


def test_two_gids_materialize_account_and_group_lines(tmp_path):
    box = _Box(tmp_path)
    rc, out, err = box.run(**_ALICE, DMS_JR_SUPP_GIDS="10010,20001")
    assert rc == 0, err
    assert box.passwd.read_text() == f"alice:x:10001:10000::{box.home}/dms-home-10001:/bin/sh\n"
    assert box.dmsg_lines() == ["dmsg10010:x:10010:alice", "dmsg20001:x:20001:alice"]
    assert "dms: groups=10000 10010 20001" in err          # sshd 의 initgroups 가 볼 것과 같은 경로
    assert (box.home / "dms-home-10001").is_dir()
    assert _MARKER not in out


def test_rerun_is_idempotent(tmp_path):
    box = _Box(tmp_path)
    env = dict(_ALICE, DMS_JR_SUPP_GIDS="10010,20001")
    assert box.run(**env)[0] == 0
    rc, _out, err = box.run(**env)                          # 계정이 이제 있다 -- (2) 일치 확인 경로
    assert rc == 0, err
    assert len(box.dmsg_lines()) == 2 and box.passwd.read_text().count("\n") == 1


@pytest.mark.parametrize("extra, lines", [({"DMS_JR_SUPP_GIDS": ""}, []), ({}, []),
                                          ({"DMS_JR_SUPP_GIDS": "0"}, ["dmsg0:x:0:alice"]),
                                          ({"DMS_JR_SUPP_GIDS": "2147483647"},
                                           ["dmsg2147483647:x:2147483647:alice"])])
def test_empty_unset_zero_and_k8s_max_are_accepted(tmp_path, extra, lines):
    # 빈 목록·미설정은 기존 동작(passwd 한 줄, group 무변경). 0 은 D3 로 인정, 2147483647 은 k8s 상한(GID_MAX).
    box = _Box(tmp_path)
    rc, out, err = box.run(**_ALICE, **extra)
    assert rc == 0, err
    assert box.passwd.read_text().count("\n") == 1
    assert box.dmsg_lines() == lines
    if not lines:
        assert box.group.read_text() == _GROUP_SEED and "dms: groups=" not in err


@pytest.mark.parametrize("value", ["2147483648", "4294967294", "010", "1,2;id", "1\n2", "12345678901",
                                   "10010,abc", "-1"])
def test_invalid_lists_fail_before_writing_anything(tmp_path, value):
    # (1) 목록 전체 검증이 **아무것도 쓰기 전**이다 -- 부분 물질화(passwd 만 있고 그룹 없음)로 sshd 가 뜨지 않게.
    box = _Box(tmp_path)
    rc, out, _err = box.run(**_ALICE, DMS_JR_SUPP_GIDS=value)
    assert rc == 1 and _MARKER in out
    assert box.passwd.read_text() == "" and box.group.read_text() == _GROUP_SEED


@pytest.mark.parametrize("pwline, rc_want", [
    ("alice:x:10001:999::/home/alice:/bin/sh\n", 1),      # uid 같고 gid 다름
    ("alice:x:1000:10000::/home/alice:/bin/sh\n", 1),     # uid 다름(이미지 계정명 충돌)
    ("alice:x:10001:10000::/home/alice:/bin/sh\n", 0),    # 둘 다 같음
])
def test_existing_account_must_match_uid_and_gid(tmp_path, pwline, rc_want):
    # 예전엔 계정이 있으면 조용히 건너뛰어 sshd 가 이미지 계정의 신원으로 rank 를 돌렸다(잠복 결함).
    box = _Box(tmp_path, passwd=pwline)
    rc, _out, err = box.run(**_ALICE, DMS_JR_SUPP_GIDS="10010")
    assert rc == rc_want, err
    assert box.passwd.read_text() == pwline                 # passwd 는 없을 때만 쓴다
    if rc_want == 0:
        assert box.dmsg_lines() == ["dmsg10010:x:10010:alice"]   # 그룹 줄은 가드 밖에서 항상
    else:
        assert "exists with a different uid/gid" in err


@pytest.mark.parametrize("shim", [{"T_DROP": "20001"}, {"T_EXTRA": "4242"}])
def test_self_check_is_set_equality_both_ways(tmp_path, shim):
    # 빠진 gid = 권한 부족, 더 붙은 gid = preflight 가 보지 못한 권한(이미지 /etc/group 소속 등) -- 둘 다 실패.
    box = _Box(tmp_path)
    rc, out, err = box.run(**_ALICE, DMS_JR_SUPP_GIDS="10010,20001", **shim)
    assert rc == 1 and _MARKER in out
    assert "expected=" in err


def test_non_root_container_with_groups_fails_closed(tmp_path):
    # 바깥 가드가 거짓(웹훅이 runAsUser 를 바꿈 등)인데 목록이 있다 -- 조용히 진행하면 preflight 와 drift.
    box = _Box(tmp_path)
    rc, out, _err = box.run(**_ALICE, DMS_JR_SUPP_GIDS="10010", T_UID="1000")
    assert rc == 1 and _MARKER in out
    rc, out, _err = box.run(**_ALICE, T_UID="1000")        # 목록이 없으면 기존 동작(아무것도 안 함)
    assert rc == 0 and _MARKER not in out and box.passwd.read_text() == ""


def test_root_job_passes_with_the_image_root_account(tmp_path):
    box = _Box(tmp_path, passwd="root:x:0:0:root:/root:/bin/sh\n")
    rc, _out, err = box.run(DMS_JR_USERNAME="root", DMS_JR_UID="0", DMS_JR_GID="0")
    assert rc == 0, err
    assert box.passwd.read_text() == "root:x:0:0:root:/root:/bin/sh\n" and box.dmsg_lines() == []


def test_script_is_a_fixed_string_with_only_one_marker_token():
    # 신원은 env 로만 -- 스크립트는 경로 상수 외엔 잡과 무관하다(test_worker_command_injection_safe 와 같은 성질).
    script = _identity_materialize_stmt()
    assert "/etc/passwd" in script and "/etc/group" in script and "/tmp/dms-home-" in script
    tokens = {c.split(";")[0].strip() for c in script.split("DMS_EXEC_REASON=")[1:]}
    assert tokens == {"identity_groups_not_applied"}
