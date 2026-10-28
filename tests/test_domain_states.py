# tests/test_domain_states.py
from dms.domain import (
    RequestState, TERMINAL_REQUEST_STATES,
    DataJobState, TERMINAL_DATA_JOB_STATES,
    Operation, Tool, PRIORITIES, PRIORITY_CLASS, DomainValidationError,
)


def test_request_terminal_states():
    assert TERMINAL_REQUEST_STATES == {
        RequestState.SUCCEEDED, RequestState.FAILED, RequestState.REJECTED,
        RequestState.CONFLICT, RequestState.CANCELLED,
    }
    assert RequestState.PENDING not in TERMINAL_REQUEST_STATES


def test_data_job_terminal_states():
    assert DataJobState.CONFIRM_PENDING not in TERMINAL_DATA_JOB_STATES
    assert {DataJobState.SUCCEEDED, DataJobState.FAILED, DataJobState.TIMED_OUT,
            DataJobState.CANCELLED, DataJobState.REJECTED,
            DataJobState.PREVIEW_EXPIRED} == TERMINAL_DATA_JOB_STATES


def test_enums_and_priority_map():
    assert Operation("sync") is Operation.SYNC
    assert Tool("nsync") is Tool.NSYNC
    assert PRIORITIES == ("low", "mid", "high")
    assert PRIORITY_CLASS["mid"] == "dms-mid"


def test_validation_error_carries_reason():
    err = DomainValidationError("unsafe_path", "leading slash")
    assert err.reason_code == "unsafe_path"
    assert "leading slash" in str(err)


def test_relative_path_rejects_control_characters():
    # 2026-10-28: 셸 검사의 명령 치환이 끝 개행을 지웠다 -- 「x\n」 링크 성분이 실제 디렉터리 「x」로 보였다. 요청 경로에 제어
    # 문자가 오지 않게 한다(C0·DEL). 탭·개행·캐리지 리턴·이스케이프 모두 unsafe_path, 일반 유니코드·공백은 그대로.
    import pytest
    from dms.domain import validate_relative_path
    for bad in ("a/x\n/b", "a\n", "a/\tb", "a\r", "a/\x1bb", "a\x7f", "a/\x00"):
        with pytest.raises(DomainValidationError) as e:
            validate_relative_path(bad)
        assert e.value.reason_code == "unsafe_path"
    assert validate_relative_path("팀 데이터/2026 결과") == "팀 데이터/2026 결과"
    assert validate_relative_path("a b/c") == "a b/c"
