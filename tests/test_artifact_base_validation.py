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
