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
