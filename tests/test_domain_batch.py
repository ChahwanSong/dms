import pytest
from dms.domain import build_data_payload, validate_batch, DomainValidationError

def test_build_data_payload_scan():
    payload, key = build_data_payload("scan", storage="s1", target="a/b", options={})
    assert payload == {"storage": "s1", "target": "a/b"}
    assert key.startswith("data.scan:s1:a/b:")

def test_build_data_payload_sync():
    payload, key = build_data_payload("sync", source_storage="s1", source="a",
        destination_storage="s2", destination="b", options={"delete": True})
    assert payload == {"source_storage": "s1", "source": "a",
                       "destination_storage": "s2", "destination": "b"}
    assert key.startswith("data.sync:s1:a:s2:b:")

def test_build_data_payload_rejects_bad_path():
    with pytest.raises(DomainValidationError):
        build_data_payload("scan", storage="s1", target="../escape", options={})

def test_validate_batch_ok():
    validate_batch("scan", 3, [{"storage": "s1", "target": "a"}])

def test_validate_batch_rejects():
    with pytest.raises(DomainValidationError):
        validate_batch("rm", 3, [{}])            # rm 불가
    with pytest.raises(DomainValidationError):
        validate_batch("scan", 0, [{}])          # max_concurrency<1
    with pytest.raises(DomainValidationError):
        validate_batch("scan", 3, [])            # empty
