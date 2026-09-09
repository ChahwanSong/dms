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


def test_roundtrip_ok_on_writable_dir(tmp_path):
    assert roundtrip_artifact_base(str(tmp_path)) is None
    assert list(tmp_path.iterdir()) == []   # probe 파일을 지웠다(왕복의 '삭제')


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
    locked.chmod(0o500)
    try:
        assert roundtrip_artifact_base(str(locked)) == "artifact_base_not_writable"
    finally:
        locked.chmod(0o700)   # tmp_path 정리가 실패하지 않도록 복원


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


def test_controller_check_records_success(db, tmp_path):
    repos = Repositories(db)
    repos.control.set_artifact_base(f"file://{tmp_path}", actor="ops")
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


def test_controller_check_reports_allowlist_violation_before_roundtrip(db, tmp_path):
    repos = Repositories(db)
    repos.control.set_artifact_base(f"file://{tmp_path}", actor="ops")
    settings = _CtlSettings()
    settings.artifact_base_allowed_prefixes = (str(tmp_path / "elsewhere"),)
    result = controller_check_once(repos, settings)
    assert result == {"uri": f"file://{tmp_path}", "ok": False,
                      "reason": "artifact_base_outside_allowlist"}
    assert not list(tmp_path.glob(".dms-base-check-*")), "허용 밖 경로에 프로브를 만들었다"
    settings.artifact_base_allowed_prefixes = (str(tmp_path),)
    assert controller_check_once(repos, settings)["ok"] is True


def test_controller_check_tolerates_settings_without_the_field(db, tmp_path):
    repos = Repositories(db)
    repos.control.set_artifact_base(f"file://{tmp_path}", actor="ops")
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
