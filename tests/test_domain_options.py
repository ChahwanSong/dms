import pytest
from dms.domain import (
    Operation, DomainValidationError, validate_options,
    option_fingerprint, build_resource_key,
)


def test_scan_options_ok():
    out = validate_options(Operation.SCAN, {"summary_only": True, "max_depth": 3})
    assert out == {"summary_only": True, "max_depth": 3}


def test_unknown_option_rejected():
    with pytest.raises(DomainValidationError) as e:
        validate_options(Operation.SCAN, {"command_line": "rm -rf /"})
    assert e.value.reason_code == "unknown_option"


@pytest.mark.parametrize("opts", [
    {"batch_files": 0}, {"bufsize": 100}, {"delete": "yes"},
    {"chmod": "999999"}, {"chown": "bad name"},
])
def test_sync_invalid_values(opts):
    with pytest.raises(DomainValidationError) as e:
        validate_options(Operation.SYNC, opts)
    assert e.value.reason_code == "invalid_option"


def test_sync_chmod_chown_ok():
    out = validate_options(Operation.SYNC, {"chmod": "D0750,F0640", "chown": "alice:dev"})
    assert out["chmod"] == "D0750,F0640"


def test_rm_stat_lite_exclusive():
    with pytest.raises(DomainValidationError) as e:
        validate_options(Operation.RM, {"recursive": True, "stat": True, "lite": True})
    assert e.value.reason_code == "invalid_option"


def test_fingerprint_is_order_insensitive():
    a = option_fingerprint({"x": 1, "y": 2})
    b = option_fingerprint({"y": 2, "x": 1})
    assert a == b and len(a) == 64


def test_resource_keys():
    fp = "f" * 64
    assert build_resource_key(Operation.SCAN, storage="s1", target="a/b", fingerprint=fp) \
        == f"data.scan:s1:a/b:{fp}"
    assert build_resource_key(
        Operation.SYNC, source_storage="s1", source="a", destination_storage="s2",
        destination="b", fingerprint=fp) == f"data.sync:s1:a:s2:b:{fp}"
