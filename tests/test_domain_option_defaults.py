"""옵션 서버 기본값(사용자 결정 2026-09-17).

sync batch_files 1,000,000·bufsize 4,194,304, scan batch_files 1,000,000·broken_limit 100 은
포탈 프리필이면서 **서버 기본값**이다 -- 키가 생략되면 validate_options 가 박아 잡에 실린다
(폼을 거치든 API 를 직접 치든 같은 동작). 배칭을 끄는 표현은 이제 sync 도 **0 명시**뿐이다."""
import json
import re
from pathlib import Path

import pytest

from dms.domain import (Operation, DomainValidationError, build_data_payload,
                        option_defaults, validate_options)

REPO_ROOT = Path(__file__).resolve().parent.parent
OPTION_RULES = REPO_ROOT / "frontend" / "src" / "features" / "jobs" / "optionRules.ts"


def test_defaults_are_the_agreed_values():
    assert option_defaults("sync") == {"batch_files": 1_000_000, "bufsize": 4_194_304}
    assert option_defaults("scan") == {"batch_files": 1_000_000, "broken_limit": 100}
    assert option_defaults("rm") == {}


@pytest.mark.parametrize("op", ["sync", "scan"])
def test_omitted_keys_get_defaults_but_explicit_values_win(op):
    assert validate_options(Operation(op), {}) == option_defaults(op)
    assert validate_options(Operation(op), None) == option_defaults(op)
    explicit = validate_options(Operation(op), {"batch_files": 7})
    assert explicit["batch_files"] == 7
    assert {k: v for k, v in explicit.items() if k != "batch_files"} == {
        k: v for k, v in option_defaults(op).items() if k != "batch_files"}


def test_defaults_are_within_spec_ranges():
    # 기본값이 범위 밖이면 "생략 = 거부" 가 된다 -- 기본값을 스펙에 다시 통과시켜 고정.
    for op in ("sync", "scan"):
        assert validate_options(Operation(op), option_defaults(op)) == option_defaults(op)


def test_sync_batch_files_zero_is_the_only_way_to_disable_batching():
    out = validate_options(Operation.SYNC, {"batch_files": 0})
    assert out["batch_files"] == 0 and out["bufsize"] == 4_194_304
    with pytest.raises(DomainValidationError):
        validate_options(Operation.SYNC, {"batch_files": -1})


def test_rm_gets_no_defaults_recursive_stays_a_consent_gate():
    assert validate_options(Operation.RM, {"recursive": True}) == {"recursive": True}
    with pytest.raises(DomainValidationError) as e:
        build_data_payload("rm", storage="s", target="a", options={})
    assert e.value.reason_code == "rm_recursive_required"


def test_payload_carries_defaults_so_request_detail_shows_them():
    payload, _ = build_data_payload("scan", storage="s", target="a", options={"verbose": True})
    assert payload["options"] == {"verbose": True, "batch_files": 1_000_000, "broken_limit": 100}
    payload, _ = build_data_payload("sync", source_storage="s", source="a",
                                    destination_storage="d", destination="b", options={})
    assert payload["options"] == {"batch_files": 1_000_000, "bufsize": 4_194_304}


def _ts_fields(name):
    src = OPTION_RULES.read_text(encoding="utf-8")
    block = re.search(rf"export const {name} = \{{(.*?)\}} as const;", src, re.S)
    assert block, name
    out = {}
    for key, lo, hi, prefill in re.findall(
            r"(\w+):\s*\{\s*lo:\s*([\d_]+),\s*hi:\s*([\d_]+),\s*prefill:\s*\"(\d+)\"", block.group(1)):
        out[key] = (int(lo.replace("_", "")), int(hi.replace("_", "")), int(prefill))
    return out


def test_portal_prefill_equals_server_default_and_bounds_mirror_spec():
    sync, scan = _ts_fields("SYNC_INT_FIELDS"), _ts_fields("SCAN_INT_FIELDS")
    assert {k: v[2] for k, v in sync.items()} == option_defaults("sync")
    assert {k: v[2] for k, v in scan.items()} == option_defaults("scan")
    assert sync["batch_files"][:2] == (0, 10_000_000) and sync["bufsize"][:2] == (4096, 1_073_741_824)
    assert scan["batch_files"][:2] == (0, 1_000_000_000) and scan["broken_limit"][:2] == (0, 10_000)
