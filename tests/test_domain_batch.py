import pytest
from dms.domain import (build_data_payload, confirm_gated, validate_batch, validate_batch_options,
                        DomainValidationError)

def test_build_data_payload_scan():
    payload, key = build_data_payload("scan", storage="s1", target="a/b", options={})
    # 생략된 옵션은 서버 기본으로 채워져 payload 에 실린다(2026-09-17, domain._OPTION_DEFAULTS).
    assert payload == {"storage": "s1", "target": "a/b",
                       "options": {"batch_files": 1_000_000, "broken_limit": 100}}
    assert key.startswith("data.scan:s1:a/b:")

def test_build_data_payload_sync():
    payload, key = build_data_payload("sync", source_storage="s1", source="a",
        destination_storage="s2", destination="b", options={"delete": True})
    assert payload == {"source_storage": "s1", "source": "a",
                       "destination_storage": "s2", "destination": "b",
                       "options": {"delete": True, "batch_files": 1_000_000,
                                   "bufsize": 4_194_304}}
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
    with pytest.raises(DomainValidationError) as exc:
        validate_batch("bogus", 3, [{}])         # 배치로 받지 않는 연산
    assert exc.value.reason_code == "invalid_batch_operation"
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


# --- rm 배치(2026-10-21 「배치작업에 rm도」) ---

def test_validate_batch_accepts_rm_on_one_storage():
    validate_batch("rm", 3, [{"storage": "s1", "target": "a"}, {"storage": "s1", "target": "b/c"}])


def test_validate_batch_rm_mixed_storage_rejected():
    with pytest.raises(DomainValidationError) as exc:
        validate_batch("rm", 3, [{"storage": "s1", "target": "a"}, {"storage": "s2", "target": "b"}])
    assert exc.value.reason_code == "batch_storage_mixed"


@pytest.mark.parametrize("targets", [
    ["a", "a"],                     # 같은 경로 두 번
    ["a", "a/"],                    # 정규화하면 같다
    ["a/./b", "a/b"],
    ["a", "a/b"],                   # 조상과 자손(순서 무관)
    ["a/b/c", "x", "a"],
    ["team/old", "team/old/sub/deeper"],
])
def test_rm_batch_rejects_overlapping_targets(targets):
    with pytest.raises(DomainValidationError) as exc:
        validate_batch("rm", 3, [{"storage": "s1", "target": t} for t in targets])
    assert exc.value.reason_code == "batch_rm_targets_overlap"


@pytest.mark.parametrize("targets", [
    ["a/b", "a/c"],                 # 형제
    ["a", "ab", "a b", "a.b"],      # 접두 문자열이 같아도 성분이 다르면 겹치지 않는다
    ["a/b", "b/a"],
])
def test_rm_batch_accepts_disjoint_targets(targets):
    validate_batch("rm", 3, [{"storage": "s1", "target": t} for t in targets])


def test_rm_overlap_check_skips_malformed_targets():
    # 형식 오류 대상은 겹침 판정에서 건너뛴다 -- 정확한 사유(unsafe_path·rm_root_forbidden)는 build_data_payload 가 낸다.
    validate_batch("rm", 3, [{"storage": "s1", "target": t} for t in ("..", ".", "", "/abs", "a")] + [{"storage": "s1"}])


def test_scan_and_sync_batches_do_not_use_the_rm_overlap_rule():
    # scan 은 비파괴라 a 와 a/b 를 함께 재는 것이 정상 운영이다(성장 모니터링).
    validate_batch("scan", 3, [{"storage": "s1", "target": "a"}, {"storage": "s1", "target": "a/b"}])


def test_validate_batch_options_requires_recursive_for_rm():
    for opts in ({}, {"recursive": False}, {"stat": True}):
        with pytest.raises(DomainValidationError) as exc:
            validate_batch_options("rm", opts)
        assert exc.value.reason_code == "rm_recursive_required"
    with pytest.raises(DomainValidationError) as exc:
        validate_batch_options("rm", {"recursive": True, "stat": True, "lite": True})
    assert exc.value.reason_code == "invalid_option"
    assert validate_batch_options("rm", {"recursive": True, "quiet": True}) == {"recursive": True, "quiet": True}
    # scan·sync 는 validate_options 와 같다(서버 기본값 채움 포함)
    assert validate_batch_options("scan", {}) == {"batch_files": 1_000_000, "broken_limit": 100}


def test_confirm_gated_is_everything_but_scan():
    assert confirm_gated("sync") and confirm_gated("rm")
    assert not confirm_gated("scan")
    assert confirm_gated("bogus")                   # DB 변조 미지 연산은 확인 게이트 쪽(fail-closed)
