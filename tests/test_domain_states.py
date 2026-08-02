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
