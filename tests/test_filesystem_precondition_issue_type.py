"""Issue-type labels for filesystem RM backend-precondition failures.

Regression for the create-owner-precondition mislabel: a create that fails because
the requester is not a resolvable POSIX/LDAP user must NOT be labeled
`filesystem_block_failed` (it is not a block). See workers._rm_precondition_issue.
"""

from __future__ import annotations

from dms.domain import OperationKind
from dms.workers import _rm_precondition_issue


def test_create_owner_unresolved_posix_message():
    issue = _rm_precondition_issue(
        OperationKind.FILESYSTEM_CREATE.value,
        "requester 'ghostuser' is not a resolvable POSIX user on the backend node",
    )
    assert issue["issue_type"] == "filesystem_owner_unresolved"
    assert issue["reason"] == "filesystem_owner_unresolved"


def test_create_owner_unresolved_ldap_message():
    issue = _rm_precondition_issue(
        OperationKind.FILESYSTEM_CREATE.value,
        "GPFS owner 'ghost' is not a resolvable LDAP user",
    )
    assert issue["issue_type"] == "filesystem_owner_unresolved"


def test_create_generic_failure_is_create_not_block():
    issue = _rm_precondition_issue(
        OperationKind.FILESYSTEM_CREATE.value, "create failed for some reason"
    )
    assert issue["issue_type"] == "filesystem_create_failed"


def test_block_failure_stays_block_failed():
    issue = _rm_precondition_issue(
        OperationKind.FILESYSTEM_BLOCK.value, "block failed for some reason"
    )
    assert issue["issue_type"] == "filesystem_block_failed"


def test_import_preflight_label_preserved():
    issue = _rm_precondition_issue(
        OperationKind.FILESYSTEM_IMPORT.value,
        "GPFS import: live mode '0750' does not match expected_mode '0770'",
    )
    assert issue["issue_type"] == "filesystem_import_preflight_failed"
