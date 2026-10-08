"""purge 파드 스크립트(purge_runner._purge_script)를 **실제 sh**(이 호스트 /bin/sh)와 GNU rm 으로 돌린다.

문자열 계약만으로는 `set -eu`·case 가드·rm 의 심링크 처리를 증명할 수 없다 -- 경로 상수(root)를 임시 디렉터리로
바꿔 끼워 실행한다(선례 tests/test_worker_identity_materialize_sh.py). 0000·sticky 하위 디렉터리는 root+cap 전용이라
테스트베드 실증으로 돌린다(여기선 비 root 에서 실패가 **보고되는지**만 본다)."""
import os
import subprocess

import pytest

from dms.purge_runner import PURGE_SCRIPT, _purge_script

A = "a" * 32
B = "b" * 32


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "mnt"
    (r / ".dms-trash").mkdir(parents=True)
    (r / ".dms-trash").chmod(0o700)
    return r


def _run(root, *names):
    p = subprocess.run(["sh", "-c", _purge_script(str(root)), "sh", *names],
                       capture_output=True, text=True, timeout=30)
    return p.returncode, p.stdout


def _tree(path, depth=30):
    d = path
    for i in range(depth):
        d = d / f"d{i}"
    d.mkdir(parents=True)
    (d / "leaf").write_text("x")


def test_script_constant_is_the_mount_path_version():
    assert PURGE_SCRIPT == _purge_script()
    assert "T=/dms-artifact-base/.dms-trash" in PURGE_SCRIPT


def test_deletes_only_the_named_entries(root):
    t = root / ".dms-trash"
    for n in (A, B):
        _tree(t / n, depth=3)
    rc, out = _run(root, A)
    assert rc == 0 and out.strip() == "DMS_PURGE_OK"
    assert not (t / A).exists() and (t / B).is_dir()


def test_symlink_pointing_outside_is_removed_but_its_target_survives(root, tmp_path):
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "keep").write_text("k")
    t = root / ".dms-trash"
    (t / A / "execution").mkdir(parents=True)
    os.symlink(victim, t / A / "execution" / "evil")
    os.symlink(victim, t / B)                    # 이름 자체가 심링크
    rc, out = _run(root, A, B)
    assert rc == 0 and "DMS_PURGE_OK" in out
    assert not (t / A).exists() and not os.path.lexists(t / B)
    assert (victim / "keep").read_text() == "k"


def test_fifo_and_deep_tree_are_removed(root):
    t = root / ".dms-trash"
    _tree(t / A, depth=60)
    os.mkfifo(t / A / "pipe")                  # 열면 막히는 FIFO -- rm 은 열지 않고 unlink 한다
    rc, out = _run(root, A)
    assert rc == 0 and "DMS_PURGE_OK" in out
    assert not (t / A).exists()


def test_missing_name_is_a_noop(root):
    rc, out = _run(root, A)
    assert rc == 0 and "DMS_PURGE_OK" in out


def test_no_names_is_ok(root):
    rc, out = _run(root)
    assert rc == 0 and "DMS_PURGE_OK" in out


@pytest.mark.parametrize("bad", ["..", "a/b", "a" * 31, "A" * 32, "", "a" * 33, "*", "-rf", "a" * 31 + "/"])
def test_bad_name_exits_2_and_deletes_nothing_even_before_it(root, bad):
    t = root / ".dms-trash"
    _tree(t / A, depth=2)
    rc, out = _run(root, A, bad)             # 앞의 정상 이름도 지우지 않는다(이름 전부를 먼저 검사)
    assert rc == 2 and "DMS_PURGE_BAD_NAME" in out and "DMS_PURGE_OK" not in out
    assert (t / A).is_dir()
    assert (root.parent / "mnt").is_dir()


def test_trash_symlink_exits_3_and_touches_nothing(tmp_path):
    r = tmp_path / "mnt"
    r.mkdir()
    elsewhere = tmp_path / "elsewhere"
    _tree(elsewhere / A, depth=1)
    os.symlink(elsewhere, r / ".dms-trash")
    rc, out = _run(r, A)
    assert rc == 3 and "DMS_PURGE_NO_TRASH" in out
    assert (elsewhere / A).is_dir()


def test_missing_trash_exits_3(tmp_path):
    r = tmp_path / "mnt"
    r.mkdir()
    rc, out = _run(r, A)
    assert rc == 3 and "DMS_PURGE_NO_TRASH" in out


def test_rm_failure_is_reported_not_swallowed(root):
    # 비 root 에서 0000 하위 디렉터리는 지울 수 없다(파드는 DAC_OVERRIDE 로 지운다) -- set -e 가 실패를 종료 코드로
    # 올리고 OK 마커가 없어야 컨트롤러가 purge_pod_failed 로 본다.
    if os.geteuid() == 0:
        pytest.skip("root 는 0000 디렉터리도 지운다")
    t = root / ".dms-trash"
    locked = t / A / "locked"
    (locked / "inner").mkdir(parents=True)
    locked.chmod(0o000)
    try:
        rc, out = _run(root, A)
        assert rc != 0 and "DMS_PURGE_OK" not in out
    finally:
        locked.chmod(0o755)


def test_one_failing_entry_does_not_stop_the_names_after_it(root):
    # 정렬상 앞 이름(A)이 지워지지 않아도 뒤 이름(B)은 시도된다(2026-10-09 검증 지적) -- 첫 실패에서 멈추면 같은 파드에
    # 실린 뒤쪽 요청이 영영 함께 지연된다. 실패는 마지막 종료 코드로 보고된다(OK 마커 없음).
    if os.geteuid() == 0:
        pytest.skip("root 는 0500 디렉터리 안의 파일도 지운다")
    t = root / ".dms-trash"
    stuck = t / A / "execution" / "sub"
    stuck.mkdir(parents=True)
    (stuck / "f").write_text("x")
    stuck.chmod(0o500)                     # 비 root 에게 지속 실패(파드에선 EIO·다른 장치 등이 같은 자리)
    _tree(t / B, depth=3)
    try:
        rc, out = _run(root, A, B)
        assert rc == 1 and "DMS_PURGE_INCOMPLETE" in out and "DMS_PURGE_OK" not in out
        assert (t / A).exists()            # 실패한 이름은 남고
        assert not (t / B).exists()        # 뒤 이름은 지워졌다
    finally:
        stuck.chmod(0o700)
