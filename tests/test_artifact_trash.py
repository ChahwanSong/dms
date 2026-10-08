"""artifact_trash(요청 삭제 정리의 파일 단계) -- 실 임시 디렉터리, 비 root(euid 기준).

root 제어면 규칙: 컨트롤러가 FS 에서 바꾸는 것은 `.dms-trash` mkdir(0700)과 `<base>/<job_id>` → trash renameat 뿐이고
삭제는 하지 않는다. `<phase>` 안으로 들어가지 않는다. 위험한 base·trash 모양은 사유 코드로 거부한다(옮기지 않는다)."""
import os
import stat

import pytest

from dms import artifact_trash
from dms.artifact_trash import TRASH, TrashError, detach, entry_state, entry_states

JID = "a" * 32
JID2 = "b" * 32


@pytest.fixture
def base(tmp_path):
    # 운영 base 와 같은 0755(umask 무관) -- g+w·o+w 가 있으면 purge_base_unsafe 다.
    b = tmp_path / "artifacts"
    b.mkdir()
    b.chmod(0o755)
    return b


def _job_dir(base, jid=JID, *, phase="execution", content=b"x"):
    d = base / jid
    (d / phase).mkdir(parents=True)
    (d / phase / "stdout.log").write_bytes(content)
    d.chmod(0o755)
    return d


def _mode(path):
    return stat.S_IMODE(os.lstat(path).st_mode)


# ---- detach: 정상·멱등 ----

def test_detach_moves_the_job_dir_into_a_0700_trash(base):
    _job_dir(base)
    assert detach(str(base), JID) == "detached"
    assert not (base / JID).exists()
    trash = base / TRASH
    assert trash.is_dir() and not trash.is_symlink()
    assert _mode(trash) == 0o700
    assert (trash / JID / "execution" / "stdout.log").read_bytes() == b"x"   # 내용은 그대로(삭제는 파드 몫)
    assert entry_state(str(base), JID) == (False, True)


def test_detach_is_idempotent_and_absent_when_nothing_is_there(base):
    assert detach(str(base), JID) == "absent"
    assert not (base / TRASH).exists()           # 옮길 것이 없으면 trash 도 만들지 않는다
    _job_dir(base)
    assert detach(str(base), JID) == "detached"
    assert detach(str(base), JID) == "absent"    # 두 번째 -- 크래시 뒤 재시도와 같은 모양
    assert entry_state(str(base), JID) == (False, True)


def test_detach_does_not_enter_an_unreadable_phase_dir(base):
    # 요청자 소유 0755 phase(비 root 잡)를 흉내 -- 안을 읽을 수 없어도 부모(<job_id>)만 옮기므로 된다.
    d = _job_dir(base)
    (d / "execution").chmod(0o000)
    try:
        assert detach(str(base), JID) == "detached"
        assert (base / TRASH / JID / "execution").exists()
    finally:
        (base / TRASH / JID / "execution").chmod(0o755)


def test_detach_with_an_empty_same_name_in_trash_replaces_it(base):
    (base / TRASH).mkdir(mode=0o700)
    (base / TRASH).chmod(0o700)
    (base / TRASH / JID).mkdir()
    _job_dir(base)
    assert detach(str(base), JID) == "detached"
    assert (base / TRASH / JID / "execution" / "stdout.log").exists()


def test_detach_reports_pending_trash_when_a_nonempty_same_name_is_still_in_trash(base):
    (base / TRASH).mkdir()
    (base / TRASH).chmod(0o700)
    (base / TRASH / JID / "old").mkdir(parents=True)
    _job_dir(base)
    assert detach(str(base), JID) == "pending_trash"
    assert (base / JID).is_dir()                  # 원본은 그대로 -- purge 파드가 비운 뒤 다음 틱에 다시
    assert entry_state(str(base), JID) == (True, True)


def test_detach_failure_other_than_not_empty_is_purge_detach_failed(base):
    if os.geteuid() == 0:
        pytest.skip("root 는 디렉터리 쓰기 비트와 무관하게 rename 한다")
    d = _job_dir(base)
    d.chmod(0o555)            # 다른 부모로 옮기려면 디렉터리 자체에 w 가 필요하다('..' 갱신)
    try:
        with pytest.raises(TrashError) as e:
            detach(str(base), JID)
        assert e.value.reason_code == "purge_detach_failed"
        assert d.is_dir()
    finally:
        d.chmod(0o755)


# ---- detach: 예상 밖 모양은 옮기지 않는다 ----

def test_symlink_job_entry_is_unexpected_and_the_target_is_untouched(base, tmp_path):
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "keep").write_text("k")
    os.symlink(victim, base / JID)
    with pytest.raises(TrashError) as e:
        detach(str(base), JID)
    assert e.value.reason_code == "artifact_dir_unexpected"
    assert (base / JID).is_symlink() and (victim / "keep").read_text() == "k"
    assert not (base / TRASH).exists()
    assert entry_state(str(base), JID) == (True, False)    # lstat -- 링크도 「있음」(완료로 오판하지 않는다)


def test_regular_file_job_entry_is_unexpected(base):
    (base / JID).write_text("not a dir")
    with pytest.raises(TrashError) as e:
        detach(str(base), JID)
    assert e.value.reason_code == "artifact_dir_unexpected"
    assert (base / JID).is_file()


def test_job_dir_owned_by_another_uid_is_unexpected(base, monkeypatch):
    _job_dir(base)
    real_stat = os.stat

    def fake_stat(path, *a, **kw):
        st = real_stat(path, *a, **kw)
        if path == JID and kw.get("dir_fd") is not None:
            fields = list(st)
            fields[stat.ST_UID] = os.geteuid() + 1
            return os.stat_result(fields)
        return st
    monkeypatch.setattr(artifact_trash.os, "stat", fake_stat)
    with pytest.raises(TrashError) as e:
        detach(str(base), JID)
    assert e.value.reason_code == "artifact_dir_unexpected"
    assert (base / JID).is_dir()


# ---- 위험한 base·trash ----

def test_missing_base_is_unavailable_not_absent(tmp_path):
    with pytest.raises(TrashError) as e:
        detach(str(tmp_path / "nope"), JID)
    assert e.value.reason_code == "purge_base_unavailable"     # ENOENT 를 「지울 것 없음」으로 접지 않는다
    with pytest.raises(TrashError) as e:
        entry_state(str(tmp_path / "nope"), JID)
    assert e.value.reason_code == "purge_base_unavailable"


def test_base_that_is_a_file_or_not_a_string_is_unavailable(tmp_path):
    f = tmp_path / "file"
    f.write_text("x")
    for bad in (str(f), None, ""):
        with pytest.raises(TrashError) as e:
            detach(bad, JID)
        assert e.value.reason_code == "purge_base_unavailable"


@pytest.mark.parametrize("mode", [0o775, 0o757, 0o777])
def test_group_or_world_writable_base_is_unsafe(base, mode):
    _job_dir(base)
    base.chmod(mode)
    try:
        with pytest.raises(TrashError) as e:
            detach(str(base), JID)
        assert e.value.reason_code == "purge_base_unsafe"
        assert (base / JID).is_dir() and not (base / TRASH).exists()
    finally:
        base.chmod(0o755)


def test_base_owned_by_another_uid_is_unsafe(base, monkeypatch):
    _job_dir(base)
    monkeypatch.setattr(artifact_trash.os, "geteuid", lambda: os.getuid() + 1)
    with pytest.raises(TrashError) as e:
        detach(str(base), JID)
    assert e.value.reason_code == "purge_base_unsafe"


def test_symlinked_base_is_followed(tmp_path, base):
    # base 접두 심링크 배포는 의도적 허용(규칙 8) -- base 자체는 따라가고 그 뒤는 dir_fd 상대.
    _job_dir(base)
    link = tmp_path / "base-link"
    os.symlink(base, link)
    assert detach(str(link), JID) == "detached"
    assert (base / TRASH / JID).is_dir()


def test_symlinked_trash_is_unsafe_and_nothing_lands_at_the_target(base, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(mode=0o700)
    os.symlink(elsewhere, base / TRASH)
    _job_dir(base)
    with pytest.raises(TrashError) as e:
        detach(str(base), JID)
    assert e.value.reason_code == "purge_base_unsafe"
    assert list(elsewhere.iterdir()) == [] and (base / JID).is_dir()
    with pytest.raises(TrashError):
        entry_state(str(base), JID)


def test_trash_that_is_a_file_is_unsafe(base):
    (base / TRASH).write_text("x")
    _job_dir(base)
    with pytest.raises(TrashError) as e:
        detach(str(base), JID)
    assert e.value.reason_code == "purge_base_unsafe"


@pytest.mark.parametrize("mode", [0o755, 0o750, 0o701])
def test_trash_not_0700_is_unsafe(base, mode):
    (base / TRASH).mkdir()
    (base / TRASH).chmod(mode)
    _job_dir(base)
    with pytest.raises(TrashError) as e:
        detach(str(base), JID)
    assert e.value.reason_code == "purge_base_unsafe"
    assert (base / JID).is_dir()


def test_trash_on_another_filesystem_is_unsafe(base, monkeypatch):
    (base / TRASH).mkdir()
    (base / TRASH).chmod(0o700)
    _job_dir(base)
    trash_ino = os.lstat(base / TRASH).st_ino
    real_fstat = os.fstat

    def fake_fstat(fd):
        st = real_fstat(fd)
        if st.st_ino == trash_ino:
            fields = list(st)
            fields[stat.ST_DEV] = st.st_dev + 1
            return os.stat_result(fields)
        return st
    monkeypatch.setattr(artifact_trash.os, "fstat", fake_fstat)
    with pytest.raises(TrashError) as e:
        detach(str(base), JID)
    assert e.value.reason_code == "purge_base_unsafe"
    assert (base / JID).is_dir()


# ---- 이름 검증·entry_state ----

@pytest.mark.parametrize("bad", ["..", "a/b", "", "A" * 32, "a" * 31, "a" * 33, "../" + "a" * 29, None, 7,
                                 "a" * 32 + "\n"])
def test_invalid_job_id_is_rejected_before_any_fs_access(base, bad):
    with pytest.raises(ValueError):
        detach(str(base), bad)
    with pytest.raises(ValueError):
        entry_states(str(base), [JID, bad])
    assert not (base / TRASH).exists()


def test_entry_state_all_four_combinations(base):
    (base / TRASH).mkdir()
    (base / TRASH).chmod(0o700)
    ids = ["1" * 32, "2" * 32, "3" * 32, "4" * 32]
    (base / ids[1]).mkdir()                      # base 만
    (base / TRASH / ids[2]).mkdir()              # trash 만
    (base / ids[3]).mkdir()                      # 둘 다
    (base / TRASH / ids[3]).mkdir()
    assert entry_states(str(base), ids) == {ids[0]: (False, False), ids[1]: (True, False),
                                            ids[2]: (False, True), ids[3]: (True, True)}


def test_entry_state_without_trash_does_not_create_it(base):
    _job_dir(base)
    assert entry_state(str(base), JID) == (True, False)
    assert entry_state(str(base), JID2) == (False, False)
    assert not (base / TRASH).exists()
