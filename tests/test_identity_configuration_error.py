"""The LDAP misconfiguration path must raise a typed error, not blow up.

`IdentityLookupConfigurationError` is what keeps a half-configured LDAP from taking
the dm-worker loop down: `cli.py` only builds the adapter when BOTH `DMS_LDAP_URI`
and `DMS_LDAP_BASE_DN` are set, and everything downstream degrades to a per-request
`ldap_not_configured` rejection (workers/dm.py). Nothing in the suite exercised the
raise itself, so the class could be — and briefly was — deleted from
`adapters/base.py` while `adapters/identity.py` kept raising it, turning every
misconfiguration into a bare `NameError`.
"""

from __future__ import annotations

import pytest

from dms.adapters import IdentityLookupConfigurationError
from dms.adapters.identity import LdapIdentityLookupAdapter
from dms.config import Settings


def _settings(**over) -> Settings:
    return Settings(
        database_url="sqlite://", observability_database_url="sqlite://", **over
    )


@pytest.mark.parametrize(
    "uri,base_dn",
    [
        (None, None),
        ("ldap://ldap.example.test", None),
        (None, "dc=example,dc=test"),
        ("", ""),
    ],
)
def test_half_configured_ldap_raises_the_typed_configuration_error(uri, base_dn):
    settings = _settings(ldap_uri=uri, ldap_base_dn=base_dn)

    with pytest.raises(IdentityLookupConfigurationError) as excinfo:
        LdapIdentityLookupAdapter.from_settings(settings)

    # the message must name the env vars an operator has to set
    assert "DMS_LDAP_URI" in str(excinfo.value)
    assert "DMS_LDAP_BASE_DN" in str(excinfo.value)


def test_fully_configured_ldap_builds_the_adapter():
    adapter = LdapIdentityLookupAdapter.from_settings(
        _settings(ldap_uri="ldap://ldap.example.test", ldap_base_dn="dc=example,dc=test")
    )

    assert adapter.uri == "ldap://ldap.example.test"
    assert adapter.base_dn == "dc=example,dc=test"


def test_configuration_error_is_distinct_from_a_read_error():
    """The DM worker treats them differently — a static misconfiguration becomes a
    per-request rejection, a live read failure is retried/reported — so they must not
    collapse into one another."""
    from dms.adapters import IdentityLookupReadError

    assert IdentityLookupConfigurationError is not IdentityLookupReadError
    assert not issubclass(IdentityLookupConfigurationError, IdentityLookupReadError)
    assert not issubclass(IdentityLookupReadError, IdentityLookupConfigurationError)
    assert issubclass(IdentityLookupConfigurationError, RuntimeError)
