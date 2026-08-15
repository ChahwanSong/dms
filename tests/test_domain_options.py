import pytest
from dms.domain import (
    Operation, DomainValidationError, validate_options,
    option_fingerprint, build_resource_key,
)


def test_scan_options_ok():
    # dscan(1b93d54)이 실제 지원하는 옵션만 수락한다: batch_files/broken_limit/verbose/quiet.
    out = validate_options(Operation.SCAN,
                           {"batch_files": 500_000, "broken_limit": 200, "verbose": True})
    assert out == {"batch_files": 500_000, "broken_limit": 200, "verbose": True}


@pytest.mark.parametrize("key", ["summary_only", "follow_symlinks",
                                 "one_file_system", "max_depth"])
def test_scan_unsupported_options_rejected(key):
    # dscan에 대응 플래그가 없는 옛 옵션은 이제 unknown_option으로 거부(수락-무시 금지).
    with pytest.raises(DomainValidationError) as e:
        validate_options(Operation.SCAN, {key: 3 if key == "max_depth" else True})
    assert e.value.reason_code == "unknown_option"


def test_scan_top_k_removed():
    # 신 dscan(1b93d54)은 --top-k 기능 자체를 삭제했다 — 이제 unknown_option.
    with pytest.raises(DomainValidationError) as e:
        validate_options(Operation.SCAN, {"top_k": 5})
    assert e.value.reason_code == "unknown_option"


def test_scan_verbose_quiet_exclusive():
    with pytest.raises(DomainValidationError) as e:
        validate_options(Operation.SCAN, {"verbose": True, "quiet": True})
    assert e.value.reason_code == "invalid_option"


@pytest.mark.parametrize("value", [0, 1_000_000_000])
def test_scan_batch_files_bounds_ok(value):
    # dscan 실측(dscan.c:1283-1288): --batch-files 0 허용 — 0 = 배칭 비활성.
    # 상한 10억은 DMS 위생 상한(도구는 uint64 전체 수용).
    assert validate_options(Operation.SCAN, {"batch_files": value}) \
        == {"batch_files": value}


@pytest.mark.parametrize("value", [-1, 1_000_000_001, "5", True])
def test_scan_batch_files_out_of_range(value):
    with pytest.raises(DomainValidationError) as e:
        validate_options(Operation.SCAN, {"batch_files": value})
    assert e.value.reason_code == "invalid_option"


@pytest.mark.parametrize("value", [0, 10_000])
def test_scan_broken_limit_bounds_ok(value):
    # dscan 실측(dscan.c:1289-1293, acc_init): --broken-limit 0 허용 — 표본 미보관,
    # broken_paths_total 총계는 항상 정확. 상한 10,000은 리포트 크기 위생
    # (경로 문자열이 리포트에 그대로 실리고, stats 라우트 읽기 상한은 256 KiB).
    assert validate_options(Operation.SCAN, {"broken_limit": value}) \
        == {"broken_limit": value}


@pytest.mark.parametrize("value", [-1, 10_001, "100", True])
def test_scan_broken_limit_out_of_range(value):
    with pytest.raises(DomainValidationError) as e:
        validate_options(Operation.SCAN, {"broken_limit": value})
    assert e.value.reason_code == "invalid_option"


def test_unknown_option_rejected():
    with pytest.raises(DomainValidationError) as e:
        validate_options(Operation.SCAN, {"command_line": "rm -rf /"})
    assert e.value.reason_code == "unknown_option"


@pytest.mark.parametrize("value", [1, 10_000_000])
def test_sync_batch_files_bounds_ok(value):
    # 상한 1,000만(사용자 조정 2026-08-16): 대규모 sync 에서 100만 단위 배치가 좁아
    # 한 자리 열었다. 도구 파싱(parse_uint64)은 uint64 전체를 받으므로 이 상한은
    # 도구 제약이 아니라 DMS 위생 상한이다.
    assert validate_options(Operation.SYNC, {"batch_files": value}) \
        == {"batch_files": value}


@pytest.mark.parametrize("value", [0, 10_000_001, "5", True])
def test_sync_batch_files_out_of_range(value):
    # 하한 1 유지: dsync 의 0(=배칭 안 함)은 DMS 에서 **키 생략**으로 표현한다
    # (표현이 둘이면 요약·화면이 갈린다). scan 의 batch_files 는 별개 스펙(0 허용).
    with pytest.raises(DomainValidationError) as e:
        validate_options(Operation.SYNC, {"batch_files": value})
    assert e.value.reason_code == "invalid_option"


@pytest.mark.parametrize("opts", [
    {"bufsize": 100}, {"delete": "yes"},
    {"chmod": "999999"}, {"chown": "bad name"},
])
def test_sync_invalid_values(opts):
    with pytest.raises(DomainValidationError) as e:
        validate_options(Operation.SYNC, opts)
    assert e.value.reason_code == "invalid_option"


def test_sync_chmod_chown_ok():
    out = validate_options(Operation.SYNC, {"chmod": "D0750,F0640", "chown": "alice:dev"})
    assert out["chmod"] == "D0750,F0640"


@pytest.mark.parametrize("value", [
    "10003", "10003:10000", "0:0", "10003:mig", "cocoa.song:10000",
    ":10000", ":mig",
])
def test_sync_chown_numeric_and_mixed_ok(value):
    # chown 파트는 「이름 또는 숫자 uid/gid」다 — dsync --chown 이 숫자를 받는 것은
    # auto_chown 의 숫자(uid:gid) 주입이 증명한다(execution_manifests._auto_chown).
    # "0:0" 은 dsync 의미상 root:root 로 유효. 혼합(숫자:이름)도 형식상 허용.
    # ":gid" 꼴(빈 uid 파트)은 기존 정규식 의미 그대로 허용이다.
    out = validate_options(Operation.SYNC, {"chown": value})
    assert out["chown"] == value


@pytest.mark.parametrize("value", ["", "10003:", "1.5:10", "10003abc"])
def test_sync_chown_bad_shapes_rejected(value):
    # 경계 고정: 빈 문자열·후행 콜론("user:" 꼴 빈 gid 파트)·소수·숫자+문자 붙임은
    # 거부 — 숫자 확장이 기존 빈 파트 규칙과 이름 규칙(문자 시작)을 넓히지 않는다.
    with pytest.raises(DomainValidationError) as e:
        validate_options(Operation.SYNC, {"chown": value})
    assert e.value.reason_code == "invalid_option"


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
