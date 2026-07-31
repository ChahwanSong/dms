"""The preview fingerprint must actually bind a confirm to observed evidence.

`data.sync` and `data.rm` are destructive, so DMS makes the operator echo back a
fingerprint of the preview they looked at before it will execute
(`dm_confirm_require_preview_fingerprint`). Two holes made that gate bypassable:

1. `_summary_fingerprint({})` hashed an empty dict, producing the CONSTANT
   `sha256:44136fa3...`. Any caller can compute that without ever reading a preview,
   and an unreadable artifact / an adapter that reported no summary produced exactly
   that value. (The same constant is observable in the wild: it is the option
   fingerprint of a request submitted with no options, and it appears in the
   `resource_key` returned by the submit API.)

2. `confirm_data_job` only compared the hashes `if preview_observed_hash and
   expected_hash` — so a job whose stored preview had NO fingerprint skipped the
   comparison entirely and accepted any value.

Both now fail closed.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest

from dms.workers.dm import _summary_fingerprint

# what an empty summary used to hash to — kept explicit so the test fails loudly if the
# degenerate value ever comes back
_EMPTY_DICT_SHA = (
    "sha256:"
    + hashlib.sha256(
        json.dumps({}, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
)


def test_no_evidence_yields_no_fingerprint_not_a_guessable_constant():
    assert _summary_fingerprint({}) is None
    assert _summary_fingerprint(None) is None  # type: ignore[arg-type]
    assert _EMPTY_DICT_SHA == (
        "sha256:44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"
    )


def test_real_evidence_still_yields_a_stable_fingerprint():
    summary = {"files": 3, "bytes": 65551}

    first = _summary_fingerprint(summary)
    second = _summary_fingerprint(dict(reversed(list(summary.items()))))

    assert first is not None
    assert first.startswith("sha256:")
    assert first == second  # key order must not change the fingerprint
    assert first != _EMPTY_DICT_SHA


def test_different_evidence_yields_a_different_fingerprint():
    assert _summary_fingerprint({"files": 3}) != _summary_fingerprint({"files": 4})


class _Repo:
    """Minimal stand-in for the confirm path's repository access."""

    def __init__(self, job: dict, plan: dict) -> None:
        self._job = job
        self._plan = plan
        self.completed: list[dict] = []

    def get_data_job(self, job_id: str) -> dict:
        return self._job

    def get_plan_by_request(self, request_id: str) -> dict:
        return self._plan

    def get_request(self, request_id: str) -> dict:
        return {"request_id": request_id, "requester_id": "alice"}

    def complete_result(self, **kwargs) -> None:
        self.completed.append(kwargs)

    def update_data_job(self, *a, **k) -> None:  # pragma: no cover - not reached
        raise AssertionError("confirm must not proceed")

    def update_plan_metadata(self, *a, **k) -> None:  # pragma: no cover
        raise AssertionError("confirm must not proceed")

    def update_plan_status(self, *a, **k) -> None:  # pragma: no cover
        raise AssertionError("confirm must not proceed")

    def update_request_status(self, *a, **k) -> None:  # pragma: no cover
        raise AssertionError("confirm must not proceed")


def _job(preview: dict) -> dict:
    return {
        "job_id": "job_1",
        "request_id": "req_1",
        "operation": "data.rm",
        "state": "ConfirmPending",
        # a live preview -- expiry is a SEPARATE gate and must not be what rejects here
        "preview_expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        "result_summary": {"preview": preview},
    }


def test_confirm_refuses_when_the_preview_recorded_no_fingerprint():
    """The hole: `expected_hash` falsy skipped the comparison, so ANY value passed."""
    from dms.workers.dm import confirm_data_job

    repo = _Repo(
        _job(preview={"state": "Succeeded"}),  # no "fingerprint" key
        plan={"plan_id": "plan_1", "execution_metadata": {}, "request_id": "req_1"},
    )

    with pytest.raises(ValueError) as excinfo:
        confirm_data_job(
            repo,
            "job_1",
            actor="operator",
            confirm=True,
            preview_observed_hash=_EMPTY_DICT_SHA,
            require_preview_fingerprint=True,
        )

    assert "no fingerprint evidence" in str(excinfo.value)


def test_confirm_still_requires_the_caller_to_supply_a_hash():
    from dms.workers.dm import confirm_data_job

    repo = _Repo(
        _job(preview={"fingerprint": "sha256:abc"}),
        plan={"plan_id": "plan_1", "execution_metadata": {}, "request_id": "req_1"},
    )

    with pytest.raises(ValueError) as excinfo:
        confirm_data_job(
            repo,
            "job_1",
            actor="operator",
            confirm=True,
            preview_observed_hash=None,
            require_preview_fingerprint=True,
        )

    assert "required" in str(excinfo.value)


def test_confirm_rejects_a_mismatched_hash():
    from dms.workers.dm import confirm_data_job

    repo = _Repo(
        _job(preview={"fingerprint": "sha256:abc"}),
        plan={"plan_id": "plan_1", "execution_metadata": {}, "request_id": "req_1"},
    )

    with pytest.raises(ValueError) as excinfo:
        confirm_data_job(
            repo,
            "job_1",
            actor="operator",
            confirm=True,
            preview_observed_hash="sha256:def",
            require_preview_fingerprint=True,
        )

    assert "does not match" in str(excinfo.value)
