# tests/test_domain_paths.py
import pytest
from dms.domain import (
    DomainValidationError, validate_relative_path, validate_sync_paths,
    validate_rm_target, validate_owner_username,
)


@pytest.mark.parametrize("bad", ["/abs/path", "a/../b", "..", "a/b\x00c", ""])
def test_unsafe_paths_rejected(bad):
    with pytest.raises(DomainValidationError) as e:
        validate_relative_path(bad)
    assert e.value.reason_code == "unsafe_path"


# ASCII 제어 문자(\x00-\x1f·\x7f)는 unsafe_path(2026-10-28 리뷰): 줄바꿈 이름은 셸 봉쇄의 $(dirname)·$(realpath) 가 끝 줄바꿈을
# 지워 옆의 다른 이름(「x\n」 링크 → 실제 「x」)으로 읽혔다 -- 셸·러너 봉쇄도 막지만 사용자에겐 제출에서 즉답한다.
@pytest.mark.parametrize("bad", ["alice/x\n/new", "data\n", "\n", "a\nb", "a\rb", "a\tb", "a\x01b", "a\x1b[31mb", "a\x1f",
                                 "a\x7fb"])
def test_control_characters_are_unsafe(bad):
    with pytest.raises(DomainValidationError) as e:
        validate_relative_path(bad)
    assert e.value.reason_code == "unsafe_path"
    with pytest.raises(DomainValidationError) as e:
        validate_sync_paths("alice/src", bad)
    assert e.value.reason_code == "unsafe_path"
    with pytest.raises(DomainValidationError) as e:
        validate_rm_target(bad, {"recursive": True})
    assert e.value.reason_code == "unsafe_path"


@pytest.mark.parametrize("ok", ["팀/데이터 1", "a b/c", "x y", "émoji/🙂", "a~b!c#d"])
def test_printable_and_non_ascii_names_are_not_control_characters(ok):
    # 거부는 ASCII 제어 문자뿐 -- 공백·비 ASCII(한글·NBSP·이모지)는 이름 그대로다(서버는 다듬지 않는다).
    assert validate_relative_path(ok) == ok


def test_valid_path_normalized():
    assert validate_relative_path("a/b/./c/") == "a/b/c"


def test_dotdot_substring_in_filename_is_allowed():
    assert validate_relative_path("notes..txt") == "notes..txt"
    assert validate_relative_path("v1..2/report.csv") == "v1..2/report.csv"


@pytest.mark.parametrize("bad", ["a/..", "../x", "./."])
def test_dotdot_component_still_rejected(bad):
    with pytest.raises(DomainValidationError) as e:
        validate_relative_path(bad)
    assert e.value.reason_code == "unsafe_path"


@pytest.mark.parametrize("src,dst", [("a/b", "a/b"), ("a", "a/b/c")])
def test_sync_destination_inside_source_rejected(src, dst):
    with pytest.raises(DomainValidationError) as e:
        validate_sync_paths(src, dst)
    assert e.value.reason_code == "sync_destination_inside_source"


def test_sync_sibling_ok():
    assert validate_sync_paths("a/b", "a/c") == ("a/b", "a/c")


def test_rm_root_forbidden():
    with pytest.raises(DomainValidationError) as e:
        validate_rm_target(".", {"recursive": True})
    assert e.value.reason_code == "rm_root_forbidden"


def test_rm_requires_recursive():
    with pytest.raises(DomainValidationError) as e:
        validate_rm_target("a/b", {})
    assert e.value.reason_code == "rm_recursive_required"


def test_owner_username():
    assert validate_owner_username("alice_01") == "alice_01"
    with pytest.raises(DomainValidationError) as e:
        validate_owner_username("bad name!")
    assert e.value.reason_code == "invalid_owner_username"
