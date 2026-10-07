"""정규화(설계 §2.2)·즉석 검증(설계 §2.4a)의 계약.

정규화는 저장 시점 한 곳에서만 한다 -- 소비자 4곳이 방어 코드를 복제하지 않도록.
즉석 검증은 존재·디렉터리 확인에 그치지 않고 임시 파일 생성→쓰기→읽기→삭제를
**실제로** 한다. 쓰기 불가는 chmod 로 재현하는데 root 는 chmod 강등을 무시하므로
그 경우 해당 테스트를 스킵한다(거짓 초록보다 정직한 스킵이 낫다)."""
import os

import pytest

from dms.artifact_base import (controller_check_once, normalize_artifact_base,
                               roundtrip_artifact_base)
from dms.domain import DomainValidationError
from dms.repositories import Repositories


def _reason(fn, *args):
    with pytest.raises(DomainValidationError) as exc:
        fn(*args)
    return exc.value.reason_code


def test_normalize_keeps_canonical_form_and_strips_trailing_slash():
    assert (normalize_artifact_base("file:///cephfs/dms/artifacts")
            == "file:///cephfs/dms/artifacts")
    assert normalize_artifact_base("/cephfs/dms/artifacts/") == "file:///cephfs/dms/artifacts"
    assert normalize_artifact_base("file:///a/b///") == "file:///a/b"


def test_normalize_rejects_relative_empty_and_root():
    assert _reason(normalize_artifact_base, "cephfs/x") == "artifact_base_not_absolute"
    assert _reason(normalize_artifact_base, "") == "artifact_base_not_absolute"
    assert _reason(normalize_artifact_base, "file://") == "artifact_base_not_absolute"
    # 루트("/") 거부: 루트를 아티팩트 트리로 쓰는 구성은 오타다.
    assert _reason(normalize_artifact_base, "/") == "artifact_base_not_absolute"


def test_normalize_rejects_traversal_segments():
    assert _reason(normalize_artifact_base, "/a/../b") == "artifact_base_traversal"
    assert _reason(normalize_artifact_base, "file:///a/..") == "artifact_base_traversal"


def test_normalize_rejects_mid_path_scheme():
    # 경로 중간 file:// 는 strip_scheme(접두사만)과 전체 치환(replace)이 다른
    # 경로를 만드는 바로 그 입력이다(설계 §2.2) -- 저장 시점에 거부해 해석기
    # 계열 차이가 실제 데이터로 드러날 일 자체를 없앤다.
    assert _reason(normalize_artifact_base, "/data/file://x") == "artifact_base_scheme_in_path"
    assert (_reason(normalize_artifact_base, "file:///data/file://x")
            == "artifact_base_scheme_in_path")


def test_roundtrip_ok_on_writable_dir(artifact_base_dir):
    # artifact_base_dir(conftest) = 0755 -- tmp_path(0700) 는 other-x 가 없어 거부된다.
    assert roundtrip_artifact_base(str(artifact_base_dir)) is None
    assert list(artifact_base_dir.iterdir()) == []   # probe 파일을 지웠다(왕복의 '삭제')


def test_roundtrip_missing_and_not_directory(tmp_path):
    assert roundtrip_artifact_base(str(tmp_path / "nope")) == "artifact_base_missing"
    f = tmp_path / "plain"
    f.write_text("x")
    assert roundtrip_artifact_base(str(f)) == "artifact_base_not_directory"


@pytest.mark.skipif(os.geteuid() == 0,
                    reason="root 는 chmod 권한 강등을 무시해 쓰기 불가를 재현할 수 없다")
def test_roundtrip_not_writable(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    # 0555: mode 검사(o+w·g+w·o+x)는 통과시키고 소유자 쓰기만 뺀다 -- 0500 은 이제
    # other-x 가 없어 쓰기 왕복 전에 artifact_base_not_traversable 로 걸린다.
    locked.chmod(0o555)
    try:
        assert roundtrip_artifact_base(str(locked)) == "artifact_base_not_writable"
    finally:
        locked.chmod(0o755)   # tmp_path 정리가 실패하지 않도록 복원


class _CtlSettings:
    artifact_base_uri = "file:///env/base"


def test_controller_check_records_failure_for_missing_base(db, tmp_path):
    # (c) 컨트롤러 자기 관점(설계 §2.4c): read_summary 마운트 부재의 "SUCCEEDED
    # 인데 요약이 없는" 조용한 실패(§1-3)를 사전에 DB 에 남겨 화면에 보이게 한다.
    repos = Repositories(db)
    repos.control.set_artifact_base(f"file://{tmp_path}/gone", actor="ops")
    result = controller_check_once(repos, _CtlSettings())
    assert result == {"uri": f"file://{tmp_path}/gone", "ok": False,
                      "reason": "artifact_base_missing"}
    st = repos.control.control_state()
    assert st["artifact_base_check_uri"] == f"file://{tmp_path}/gone"
    assert st["artifact_base_check_ok"] == 0
    assert st["artifact_base_check_reason"] == "artifact_base_missing"
    assert st["artifact_base_check_at"] is not None


def test_controller_check_records_success(db, artifact_base_dir):
    repos = Repositories(db)
    repos.control.set_artifact_base(f"file://{artifact_base_dir}", actor="ops")
    assert controller_check_once(repos, _CtlSettings())["ok"] is True
    st = repos.control.control_state()
    assert st["artifact_base_check_ok"] == 1
    assert st["artifact_base_check_reason"] is None


# --- 경로 접두 allowlist(2026-09-09, 제어면 root 전환) -------------------------
# 65532 시절엔 파일시스템이 사실상의 allowlist 였다(대부분 경로가 쓰기 불가). root 면
# 존재하는 모든 디렉터리가 저장 가능해지므로 공유 FS 마운트로 묶는다.
from dms.artifact_base import allowlist_reason  # noqa: E402


def test_allowlist_empty_means_unrestricted(tmp_path):
    assert allowlist_reason(str(tmp_path), ()) is None
    assert allowlist_reason("/etc", ()) is None


def test_allowlist_accepts_prefix_itself_and_descendants(tmp_path):
    base = str(tmp_path)
    assert allowlist_reason(base, (base,)) is None
    sub = tmp_path / "dms" / "artifacts"
    sub.mkdir(parents=True)
    assert allowlist_reason(str(sub), (base,)) is None
    # 문자열 접두가 같아도 경로 조각이 다르면 밖이다(/cephfs vs /cephfs2)
    assert allowlist_reason(base + "2", (base,)) == "artifact_base_outside_allowlist"
    assert allowlist_reason("/etc", (base,)) == "artifact_base_outside_allowlist"


def test_allowlist_resolves_symlinked_base_into_the_prefix(tmp_path):
    # base 접두가 심링크인 배포(/data → /cephfs/x)는 허용(realpath != path 를
    # 거부하지 않는다 -- assert_contained 와 같은 입장); 반대로 allowlist 안에서
    # 밖을 가리키는 심링크는 realpath 로 드러나 거부된다.
    real = tmp_path / "cephfs"
    real.mkdir()
    link = tmp_path / "data"
    os.symlink(real, link)
    assert allowlist_reason(str(link), (str(real),)) is None
    escape = real / "escape"
    os.symlink("/etc", escape)
    assert allowlist_reason(str(escape), (str(real),)) == "artifact_base_outside_allowlist"


def test_controller_check_reports_allowlist_violation_before_roundtrip(db, tmp_path,
                                                                      artifact_base_dir):
    repos = Repositories(db)
    base = artifact_base_dir
    repos.control.set_artifact_base(f"file://{base}", actor="ops")
    settings = _CtlSettings()
    settings.artifact_base_allowed_prefixes = (str(tmp_path / "elsewhere"),)
    result = controller_check_once(repos, settings)
    assert result == {"uri": f"file://{base}", "ok": False,
                      "reason": "artifact_base_outside_allowlist"}
    assert not list(base.glob(".dms-base-check-*")), "허용 밖 경로에 프로브를 만들었다"
    settings.artifact_base_allowed_prefixes = (str(tmp_path),)
    assert controller_check_once(repos, settings)["ok"] is True


def test_controller_check_tolerates_settings_without_the_field(db, artifact_base_dir):
    repos = Repositories(db)
    repos.control.set_artifact_base(f"file://{artifact_base_dir}", actor="ops")
    assert controller_check_once(repos, _CtlSettings())["ok"] is True


# --- base 소유자·mode 전제(2026-09-09 리뷰): allowlist 만으론 777 디렉터리가 통과한다 ---
import stat as _stat  # noqa: E402


def test_roundtrip_rejects_world_writable_base(tmp_path):
    loose = tmp_path / "loose"
    loose.mkdir()
    loose.chmod(0o777)
    assert roundtrip_artifact_base(str(loose)) == "artifact_base_world_writable"
    loose.chmod(0o1777)     # sticky 여도 world-writable 은 거부
    assert roundtrip_artifact_base(str(loose)) == "artifact_base_world_writable"
    loose.chmod(0o755)
    assert roundtrip_artifact_base(str(loose)) is None


def test_roundtrip_rejects_base_not_owned_by_this_process(tmp_path, monkeypatch):
    real_stat = os.stat

    class _Foreign:
        def __init__(self, st):
            self._st = st
        def __getattr__(self, name):
            return 4242 if name == "st_uid" else getattr(self._st, name)

    monkeypatch.setattr(os, "stat", lambda p, *a, **k: _Foreign(real_stat(p, *a, **k))
                        if str(p) == str(tmp_path) else real_stat(p, *a, **k))
    assert roundtrip_artifact_base(str(tmp_path)) == "artifact_base_not_owned"
    assert not list(tmp_path.glob(".dms-base-check-*"))   # 프로브 전에 거른다


def test_allowlist_tolerates_a_bare_string_prefix(tmp_path):
    # 문자열을 글자 단위로 돌면 '/' 가 후보가 돼 allowlist 가 조용히 꺼진다
    assert allowlist_reason("/etc", str(tmp_path)) == "artifact_base_outside_allowlist"
    assert allowlist_reason(str(tmp_path), str(tmp_path)) is None


# --- 그룹 쓰기·POSIX ACL·other-x(2026-10-07, 보조 그룹 D6/D12) ---------------------
# 잡 파드가 LDAP 보조 그룹을 달고 돌면 base 의 g+w(또는 ACL 쓰기 항목)는 그 그룹의 모든
# 요청자에게 base 쓰기를 준다 -- world-writable 과 같은 봉쇄 기준 이동 공격. 보조 gid 0 도
# 인정하므로(D3) gid 와 무관하게 거부한다. launcher 는 보조 그룹 없이 base 를 지나므로
# other-x 도 필수다(D12 대체). 모두 쓰기 프로브 **앞**에서 거른다.
import errno as _errno  # noqa: E402
import shutil as _shutil  # noqa: E402
import struct as _struct  # noqa: E402
import subprocess as _subprocess  # noqa: E402

_USER_OBJ, _USER, _GROUP_OBJ, _GROUP, _MASK, _OTHER = 0x01, 0x02, 0x04, 0x08, 0x10, 0x20
_UNDEF = 0xFFFFFFFF


def _acl(*entries, version=2):
    """리눅스 posix_acl_xattr 표현: <I version + <HHI(tag, perm, id) 엔트리들."""
    return (_struct.pack("<I", version)
            + b"".join(_struct.pack("<HHI", tag, perm, ident) for tag, perm, ident in entries))


_MINIMAL = ((_USER_OBJ, 7, _UNDEF), (_GROUP_OBJ, 5, _UNDEF), (_OTHER, 5, _UNDEF))


def _fake_xattrs(monkeypatch, path, values):
    """path 의 system.posix_acl_* 조회를 values(이름 → bytes 또는 errno 정수)로 바꾼다.
    없는 이름은 ENODATA(= ACL 없음)."""
    real = os.getxattr

    def fake(p, name, *a, **k):
        if str(p) != str(path):
            return real(p, name, *a, **k)
        value = values.get(name, _errno.ENODATA)
        if isinstance(value, int):
            raise OSError(value, os.strerror(value))
        return value

    monkeypatch.setattr(os, "getxattr", fake)


def _no_probe_left(base):
    return not list(base.glob(".dms-base-check-*"))


def test_group_writable_rejected_even_for_root_group(artifact_base_dir, monkeypatch):
    base = artifact_base_dir
    for mode in (0o775, 0o2775, 0o1775, 0o770, 0o730):
        base.chmod(mode)
        # 0770·0730 은 other-x 도 없지만 요청자 쓰기(g+w)가 더 위험해 그 사유가 먼저다.
        assert roundtrip_artifact_base(str(base)) == "artifact_base_group_writable", oct(mode)
        assert _no_probe_left(base)
    # gid 와 무관: root 그룹(gid 0) 소유여도 g+w 면 거부 -- 보조 gid 0 을 인정하므로(D3)
    # "root 그룹이라 안전" 이 성립하지 않는다.
    real_stat = os.stat

    class _RootGroup:
        def __init__(self, st):
            self._st = st
        def __getattr__(self, name):
            return 0 if name == "st_gid" else getattr(self._st, name)

    monkeypatch.setattr(os, "stat", lambda p, *a, **k: _RootGroup(real_stat(p, *a, **k))
                        if str(p) == str(base) else real_stat(p, *a, **k))
    base.chmod(0o775)
    assert roundtrip_artifact_base(str(base)) == "artifact_base_group_writable"
    base.chmod(0o755)
    assert roundtrip_artifact_base(str(base)) is None


def test_not_other_executable_rejected(artifact_base_dir):
    base = artifact_base_dir
    try:
        # 그룹으로 여는 750/710 도 거부: launcher 는 보조 그룹 없이 base 아래 hostfile 을 읽는다.
        for mode in (0o750, 0o710, 0o700, 0o754, 0o1750):
            base.chmod(mode)
            assert roundtrip_artifact_base(str(base)) == "artifact_base_not_traversable", oct(mode)
            assert _no_probe_left(base)
        for mode in (0o755, 0o711, 0o751, 0o1755):
            base.chmod(mode)
            assert roundtrip_artifact_base(str(base)) is None, oct(mode)
    finally:
        base.chmod(0o755)


def test_default_acl_present_is_rejected_regardless_of_content(artifact_base_dir, monkeypatch):
    # default ACL 은 러너가 만드는 <job_id> 가 상속한다 -- 내용과 무관하게 존재만으로 거부.
    base = artifact_base_dir
    for value in (_acl(*_MINIMAL), b""):
        _fake_xattrs(monkeypatch, base, {"system.posix_acl_default": value})
        assert roundtrip_artifact_base(str(base)) == "artifact_base_group_writable"
        assert _no_probe_left(base)


def test_named_acl_write_entries_rejected_regardless_of_mask(artifact_base_dir, monkeypatch):
    base = artifact_base_dir
    cases = {
        "named group rwx": _acl(*_MINIMAL, (_GROUP, 7, 10010), (_MASK, 7, _UNDEF)),
        "named user -w-": _acl(*_MINIMAL, (_USER, 2, 1001), (_MASK, 7, _UNDEF)),
        # mask 가 쓰기를 가려도 거부: chmod g+w 한 번이면 mask 가 넓어져 조용히 열린다.
        "named group w, mask r-x": _acl(*_MINIMAL, (_GROUP, 6, 0), (_MASK, 5, _UNDEF)),
    }
    for label, value in cases.items():
        _fake_xattrs(monkeypatch, base, {"system.posix_acl_access": value})
        assert roundtrip_artifact_base(str(base)) == "artifact_base_group_writable", label
        assert _no_probe_left(base)


def test_acl_without_named_write_passes(artifact_base_dir, monkeypatch):
    # named 항목이 읽기·실행뿐이면 통과. user_obj(소유자 root)의 쓰기와 group_obj 는 판정
    # 대상이 아니다 -- group_obj 쓰기는 mask 를 거쳐 st_mode 의 S_IWGRP 로 드러난다.
    base = artifact_base_dir
    for value in (_acl(*_MINIMAL),
                  _acl(*_MINIMAL, (_GROUP, 5, 10010), (_USER, 4, 1001), (_MASK, 5, _UNDEF))):
        _fake_xattrs(monkeypatch, base, {"system.posix_acl_access": value})
        assert roundtrip_artifact_base(str(base)) is None


def test_malformed_acl_fails_closed(artifact_base_dir, monkeypatch):
    # 형식을 모르면 통과로 접지 않는다 -- 모름을 통과로 읽으면 이 검사가 있으나 마나다.
    base = artifact_base_dir
    for label, value in {"version 1": _acl(*_MINIMAL, version=1),
                         "trailing bytes": _acl(*_MINIMAL) + b"\x00" * 3,
                         "short header": b"\x02\x00"}.items():
        _fake_xattrs(monkeypatch, base, {"system.posix_acl_access": value})
        assert roundtrip_artifact_base(str(base)) == "artifact_base_group_writable", label


def test_acl_absent_errnos_pass(artifact_base_dir, monkeypatch):
    base = artifact_base_dir
    for code in (_errno.ENODATA, _errno.ENOTSUP, _errno.EOPNOTSUPP):
        _fake_xattrs(monkeypatch, base, {"system.posix_acl_default": code,
                                         "system.posix_acl_access": code})
        assert roundtrip_artifact_base(str(base)) is None, code


def test_unexpected_getxattr_error_fails_closed(artifact_base_dir, monkeypatch):
    base = artifact_base_dir
    for values in ({"system.posix_acl_default": _errno.EACCES},
                   {"system.posix_acl_access": _errno.EACCES},
                   {"system.posix_acl_access": _errno.EIO}):
        _fake_xattrs(monkeypatch, base, values)
        assert roundtrip_artifact_base(str(base)) == "artifact_base_group_writable", values
        assert _no_probe_left(base)


def _setfacl(path, *args):
    return _subprocess.run(["setfacl", *args, str(path)], capture_output=True).returncode == 0


@pytest.mark.skipif(_shutil.which("setfacl") is None, reason="setfacl 없음")
def test_real_posix_acl_is_parsed(artifact_base_dir):
    # 커널이 실제로 만든 xattr 바이트로 파서를 확인한다(손으로 만든 _acl 과 표현이 같은지).
    base = artifact_base_dir
    if not _setfacl(base, "-m", "u::rwx"):
        pytest.skip("tmp 파일시스템이 POSIX ACL 을 지원하지 않는다")
    # mask 를 r-x 로 명시 -> st_mode 의 그룹 비트는 r-x(0755 로 보임) -- mode 검사를 지나
    # ACL 파서만이 named group 의 쓰기를 잡는 경우다.
    assert _setfacl(base, "-m", "g:4242:rwx,m::r-x")
    assert _stat.S_IMODE(os.stat(base).st_mode) == 0o755
    assert roundtrip_artifact_base(str(base)) == "artifact_base_group_writable"
    assert _setfacl(base, "-b")
    assert roundtrip_artifact_base(str(base)) is None
    assert _setfacl(base, "-m", "g:4242:r-x")
    assert roundtrip_artifact_base(str(base)) is None
    assert _setfacl(base, "-b")
    assert _setfacl(base, "-d", "-m", "u::rwx,g::r-x,o::r-x")
    assert roundtrip_artifact_base(str(base)) == "artifact_base_group_writable"
    assert _setfacl(base, "-k")
    assert roundtrip_artifact_base(str(base)) is None
