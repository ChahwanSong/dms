import pytest
from dms.identity import IdentityRejected, ResolvedIdentity, StubIdentityResolver, resolve_job_identity
from dms.repositories import Repositories


def test_group_denylist_blocks_privileged_requester(db):
    repos = Repositories(db)
    repos.control.deny("group", "wheel", "정책 위반", "admin")
    root_ish = ResolvedIdentity("root-ish", 1000, 1000, ("wheel",), False)
    resolver = StubIdentityResolver({"root-ish": root_ish})
    with pytest.raises(IdentityRejected) as e:
        resolve_job_identity(repos.control, resolver,
                             requester_id="admin", owner_username="root-ish",
                             allow_privileged=True, privileged_requesters=("admin",))
    assert e.value.reason_code == "identity_denied"


def test_privileged_path_needs_no_resolver_when_no_group_denies(db):
    repos = Repositories(db)
    repos.control.deny("requester", "mallory", None, "admin")
    identity = resolve_job_identity(repos.control, None,
                                    requester_id="admin", owner_username="alice",
                                    allow_privileged=True, privileged_requesters=("admin",))
    assert identity.privileged is True
    assert identity.uid == 0


def test_has_group_denies(db):
    repos = Repositories(db)
    assert repos.control.has_group_denies() is False
    repos.control.deny("requester", "bob", None, "admin")
    assert repos.control.has_group_denies() is False
    repos.control.deny("group", "wheel", None, "admin")
    assert repos.control.has_group_denies() is True
