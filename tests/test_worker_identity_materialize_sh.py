"""워커(sshd) 컨테이너의 실행 신원 물질화를 **실제 sh**(이 호스트 /bin/sh = dash)로 돌린다(2026-10-07 D13 → 2026-10-26).

문자열 계약만으로는 `set -eu` 아래 case 가드·AND-OR 목록·명령 치환 실패의 상호작용을 증명할 수 없다 -- 그래서
_identity_materialize_stmt 를 임시 passwd/home 경로로 바꿔 끼워 실행한다. 컨테이너의 `id`·`getent` 는 PATH 앞의 심(shim)이
흉내 낸다(T_UID 로 id -u). dms-k8s 는 모든 잡이 root 라 보통 「실행 신원 이름 → uid 0·gid 0」 별칭 계정이다(runuser·sshd 가
그 이름으로 로그인한다). LDAP 보조 그룹(/etc/group dmsg<gid> 줄·자기 검증)은 2026-10-26 에 지웠다.
"""
import os
import subprocess
import sys

import pytest

from dms.execution_manifests import _identity_materialize_stmt

_ID_SHIM = r'''import os, sys
a = sys.argv[1:]
if a == ["-u"]:
    print(os.environ.get("T_UID", "0")); sys.exit(0)
sys.exit(2)
'''
_GETENT_SHIM = r'''import os, sys
if sys.argv[1] != "passwd": sys.exit(2)
for line in open(os.environ["T_PASSWD"]):
    if line.split(":")[0] == sys.argv[2]:
        sys.stdout.write(line); sys.exit(0)
sys.exit(2)
'''
_ALICE_ROOT = {"DMS_JR_USERNAME": "alice", "DMS_JR_UID": "0", "DMS_JR_GID": "0"}


class _Box:
    """임시 passwd/home + id·getent 심. run() 은 (rc, stdout, stderr)."""

    def __init__(self, tmp_path, passwd=""):
        self.dir = tmp_path
        bindir = tmp_path / "bin"
        bindir.mkdir()
        for name, body in (("id", _ID_SHIM), ("getent", _GETENT_SHIM)):
            p = bindir / name
            p.write_text(f"#!{sys.executable}\n{body}")
            p.chmod(0o755)
        self.passwd = tmp_path / "passwd"
        self.passwd.write_text(passwd)
        self.home = tmp_path / "home"
        self.home.mkdir()
        self.script = _identity_materialize_stmt(passwd=str(self.passwd), home=f"{self.home}/dms-home-")

    def run(self, **env):
        e = {"PATH": f"{self.dir / 'bin'}:{os.environ['PATH']}", "T_PASSWD": str(self.passwd), **env}
        proc = subprocess.run(["sh", "-c", "set -eu\n" + self.script], env=e,
                              capture_output=True, text=True)
        return proc.returncode, proc.stdout, proc.stderr


def test_root_alias_account_is_materialized_once(tmp_path):
    # 실행 신원 이름(alice)을 uid 0·gid 0 별칭으로 -- 러너의 runuser·워커 sshd 가 이 이름으로 로그인한다.
    box = _Box(tmp_path)
    rc, out, err = box.run(**_ALICE_ROOT)
    assert rc == 0, err
    assert box.passwd.read_text() == f"alice:x:0:0::{box.home}/dms-home-0:/bin/sh\n"
    assert (box.home / "dms-home-0").is_dir()
    rc, _out, err = box.run(**_ALICE_ROOT)                  # 다시 돌려도(계정이 이제 있다 -- 일치 확인 경로) 한 줄
    assert rc == 0, err
    assert box.passwd.read_text().count("\n") == 1


@pytest.mark.parametrize("pwline, rc_want", [
    ("alice:x:0:999::/home/alice:/bin/sh\n", 1),          # uid 같고 gid 다름
    ("alice:x:1000:0::/home/alice:/bin/sh\n", 1),         # uid 다름(이미지 계정명 충돌)
    ("alice:x:0:0::/home/alice:/bin/sh\n", 0),            # 둘 다 같음
])
def test_existing_account_must_match_uid_and_gid(tmp_path, pwline, rc_want):
    # 예전엔 계정이 있으면 조용히 건너뛰어 sshd 가 이미지 계정의 신원으로 rank 를 돌렸다(잠복 결함).
    box = _Box(tmp_path, passwd=pwline)
    rc, _out, err = box.run(**_ALICE_ROOT)
    assert rc == rc_want, err
    assert box.passwd.read_text() == pwline                 # passwd 는 없을 때만 쓴다
    if rc_want == 1:
        assert "exists with a different uid/gid" in err


@pytest.mark.parametrize("env", [
    {"DMS_JR_USERNAME": "alice", "DMS_JR_UID": "0;id", "DMS_JR_GID": "0"},
    {"DMS_JR_USERNAME": "alice", "DMS_JR_UID": "0", "DMS_JR_GID": "x"},
    {"DMS_JR_USERNAME": "al:ice", "DMS_JR_UID": "0", "DMS_JR_GID": "0"},
])
def test_invalid_values_fail_before_writing_anything(tmp_path, env):
    box = _Box(tmp_path)
    rc, _out, err = box.run(**env)
    assert rc == 1 and "dms: invalid" in err
    assert box.passwd.read_text() == ""


def test_non_root_container_does_nothing(tmp_path):
    # 바깥 가드: root 컨테이너에서만 물질화한다.
    box = _Box(tmp_path)
    rc, _out, _err = box.run(**_ALICE_ROOT, T_UID="1000")
    assert rc == 0 and box.passwd.read_text() == ""


def test_root_job_passes_with_the_image_root_account(tmp_path):
    box = _Box(tmp_path, passwd="root:x:0:0:root:/root:/bin/sh\n")
    rc, _out, err = box.run(DMS_JR_USERNAME="root", DMS_JR_UID="0", DMS_JR_GID="0")
    assert rc == 0, err
    assert box.passwd.read_text() == "root:x:0:0:root:/root:/bin/sh\n"


def test_script_is_a_fixed_string_without_group_materialization():
    # 신원은 env 로만 -- 스크립트는 경로 상수 외엔 잡과 무관하다(test_worker_command_injection_safe 와 같은 성질).
    # LDAP 보조 그룹 물질화(/etc/group·DMS_JR_SUPP_GIDS·identity_groups_not_applied 마커)는 없다.
    script = _identity_materialize_stmt()
    assert "/etc/passwd" in script and "/tmp/dms-home-" in script
    assert "/etc/group" not in script and "DMS_JR_SUPP_GIDS" not in script and "DMS_EXEC_REASON=" not in script
