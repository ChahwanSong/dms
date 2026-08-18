import pytest
from dms.domain import build_data_payload, validate_batch, DomainValidationError

def test_build_data_payload_scan():
    payload, key = build_data_payload("scan", storage="s1", target="a/b", options={})
    assert payload == {"storage": "s1", "target": "a/b", "options": {}}
    assert key.startswith("data.scan:s1:a/b:")

def test_build_data_payload_sync():
    payload, key = build_data_payload("sync", source_storage="s1", source="a",
        destination_storage="s2", destination="b", options={"delete": True})
    assert payload == {"source_storage": "s1", "source": "a",
                       "destination_storage": "s2", "destination": "b",
                       "options": {"delete": True}}
    # 슬라이스 36: 파괴적 op 키에는 옵션 지문이 없다(같은 대상 = 같은 키).
    assert key == "data.sync:s1:a:s2:b"

def test_build_data_payload_rejects_bad_path():
    with pytest.raises(DomainValidationError):
        build_data_payload("scan", storage="s1", target="../escape", options={})

def test_build_data_payload_missing_storage():
    with pytest.raises(DomainValidationError):
        build_data_payload("scan", target="a", options={})

def test_validate_batch_ok():
    validate_batch("scan", 3, [{"storage": "s1", "target": "a"}])

def test_validate_batch_rejects():
    with pytest.raises(DomainValidationError):
        validate_batch("rm", 3, [{}])            # rm 불가
    with pytest.raises(DomainValidationError):
        validate_batch("scan", 0, [{}])          # max_concurrency<1
    with pytest.raises(DomainValidationError):
        validate_batch("scan", 3, [])            # empty


# --- 슬라이스 32: 단일 스토리지 강제 + 실행 제어(priority/node_count) 검증 ---

def test_validate_batch_scan_mixed_storage_rejected():
    with pytest.raises(DomainValidationError) as exc:
        validate_batch("scan", 3, [{"storage": "s1", "target": "a"},
                                   {"storage": "s2", "target": "b"}])
    assert exc.value.reason_code == "batch_storage_mixed"

def test_validate_batch_sync_mixed_pair_rejected():
    with pytest.raises(DomainValidationError) as exc:
        validate_batch("sync", 3, [
            {"source_storage": "s1", "source": "a",
             "destination_storage": "s2", "destination": "b"},
            {"source_storage": "s1", "source": "c",
             "destination_storage": "s3", "destination": "d"},
        ])
    assert exc.value.reason_code == "batch_storage_mixed"

def test_validate_batch_homogeneous_items_ok():
    validate_batch("scan", 3, [{"storage": "s1", "target": "a"},
                               {"storage": "s1", "target": "b"}])
    validate_batch("sync", 3, [
        {"source_storage": "s1", "source": "a",
         "destination_storage": "s2", "destination": "b"},
        {"source_storage": "s1", "source": "c",
         "destination_storage": "s2", "destination": "d"},
    ])

def test_validate_batch_max_concurrency_upper_bound():
    with pytest.raises(DomainValidationError) as exc:
        validate_batch("scan", 65, [{"storage": "s1", "target": "a"}])
    assert exc.value.reason_code == "invalid_max_concurrency"
    validate_batch("scan", 64, [{"storage": "s1", "target": "a"}])  # 경계 통과

def test_validate_batch_priority():
    with pytest.raises(DomainValidationError) as exc:
        validate_batch("scan", 3, [{"storage": "s1", "target": "a"}], priority="urgent")
    assert exc.value.reason_code == "invalid_priority"
    validate_batch("scan", 3, [{"storage": "s1", "target": "a"}], priority=None)
    validate_batch("scan", 3, [{"storage": "s1", "target": "a"}], priority="high")

def test_validate_batch_node_count():
    for bad in (0, True, "4", -1, 1025):
        with pytest.raises(DomainValidationError) as exc:
            validate_batch("scan", 3, [{"storage": "s1", "target": "a"}], node_count=bad)
        assert exc.value.reason_code == "invalid_node_count"
    validate_batch("scan", 3, [{"storage": "s1", "target": "a"}], node_count=None)
    validate_batch("scan", 3, [{"storage": "s1", "target": "a"}], node_count=4)


# --- 노드당 프로세스 수 override: node_count 와 같은 규칙의 미러 ---

def test_validate_batch_procs_per_node():
    for bad in (0, True, "4", -1, 1025):
        with pytest.raises(DomainValidationError) as exc:
            validate_batch("scan", 3, [{"storage": "s1", "target": "a"}],
                           procs_per_node=bad)
        assert exc.value.reason_code == "invalid_procs_per_node"
    validate_batch("scan", 3, [{"storage": "s1", "target": "a"}], procs_per_node=None)
    validate_batch("scan", 3, [{"storage": "s1", "target": "a"}], procs_per_node=4)
