"""Regression for fix A: creating a DMS access group with zero members must NOT
send an empty ``memberUid`` attribute to LDAP.

OpenLDAP rejects an ``add`` that includes ``memberUid`` with no values
("protocolError / no values for attribute type"). ``memberUid`` is OPTIONAL in
the RFC2307 ``posixGroup`` schema, so the attribute must be omitted entirely
when the group has no members. This previously broke ``filesystem.import`` of a
directory whose live group has no LDAP members (root-owned / empty group).

ldap3 is not a hard test dependency, so a minimal fake module is injected into
``sys.modules`` and the ``connection.add`` payload is inspected directly.
"""

from __future__ import annotations

import sys
import types

import pytest

from dms.adapters import IdentityLookupReadError, LdapIdentityGroupManager

GROUP_BASE = "ou=groups,dc=dms,dc=local"
USER_BASE = "ou=people,dc=dms,dc=local"


class _FakeEntry:
    def __init__(self, dn: str, attrs: dict) -> None:
        self.entry_dn = dn
        self._attrs = attrs

    @property
    def entry_attributes_as_dict(self) -> dict:
        return self._attrs


class _FakeConnection:
    """Minimal stand-in for an ldap3 Connection context manager."""

    def __init__(self, store: dict, users_exist: bool = True) -> None:
        self.store = store  # group_name -> ldap3-style attrs (lists)
        self.users_exist = users_exist
        self.entries: list = []
        self.result: dict = {}
        self.added: list = []
        self.modified: list = []

    def __enter__(self) -> "_FakeConnection":
        return self

    def __exit__(self, *exc) -> bool:
        return False

    @staticmethod
    def _name_from_dn(dn: str) -> str:
        return dn.split(",", 1)[0].split("=", 1)[1]

    def search(self, search_base=None, search_filter=None, **_kw) -> None:
        f = search_filter or ""
        if f.startswith("(uid="):
            self.entries = [_FakeEntry("uid=x", {"uid": ["x"]})] if self.users_exist else []
        elif f == "(gidNumber=*)":
            self.entries = [
                _FakeEntry(f"cn={n},{GROUP_BASE}", a) for n, a in self.store.items()
            ]
        elif f.startswith("(cn="):
            name = f[len("(cn="):-1]
            self.entries = (
                [_FakeEntry(f"cn={name},{GROUP_BASE}", self.store[name])]
                if name in self.store
                else []
            )
        elif "objectClass=posixGroup" in f:
            name = self._name_from_dn(search_base)
            self.entries = (
                [_FakeEntry(search_base, self.store[name])] if name in self.store else []
            )
        else:
            self.entries = []

    def add(self, dn, object_class=None, attributes=None) -> bool:
        attributes = dict(attributes or {})
        self.added.append({"dn": dn, "object_class": object_class, "attributes": attributes})
        stored = {
            "cn": [attributes["cn"]],
            "gidNumber": [str(attributes["gidNumber"])],
            "description": [attributes.get("description", "")],
        }
        if "memberUid" in attributes:
            stored["memberUid"] = list(attributes["memberUid"])
        self.store[attributes["cn"]] = stored
        self.result = {"result": 0, "description": "success"}
        return True

    def modify(self, dn, changes) -> bool:
        self.modified.append({"dn": dn, "changes": changes})
        name = self._name_from_dn(dn)
        for attr, ops in changes.items():
            for _op, vals in ops:
                self.store.setdefault(name, {}).setdefault(attr, []).extend(vals)
        self.result = {"result": 0, "description": "success"}
        return True


def _install_fake_ldap3(monkeypatch, connection: _FakeConnection) -> None:
    mod = types.ModuleType("ldap3")
    mod.SUBTREE = "SUBTREE"
    mod.ALL_ATTRIBUTES = "ALL_ATTRIBUTES"
    mod.MODIFY_ADD = "MODIFY_ADD"
    mod.Server = lambda *a, **k: ("server", a, k)
    mod.Connection = lambda *a, **k: connection
    utils = types.ModuleType("ldap3.utils")
    conv = types.ModuleType("ldap3.utils.conv")
    conv.escape_filter_chars = lambda s: s
    utils.conv = conv
    monkeypatch.setitem(sys.modules, "ldap3", mod)
    monkeypatch.setitem(sys.modules, "ldap3.utils", utils)
    monkeypatch.setitem(sys.modules, "ldap3.utils.conv", conv)


def _manager() -> LdapIdentityGroupManager:
    return LdapIdentityGroupManager(
        uri="ldap://pkg-01:389",
        base_dn="dc=dms,dc=local",
        bind_dn="cn=admin,dc=dms,dc=local",
        bind_password="secret",
        user_search_base=USER_BASE,
        group_search_base=GROUP_BASE,
    )


def test_create_group_with_zero_members_omits_memberuid(monkeypatch):
    conn = _FakeConnection(store={})
    _install_fake_ldap3(monkeypatch, conn)

    result = _manager().ensure_group_members(
        group_name="dms-grp-imp", users=[], resource_key="cephfs-dms:imp"
    )

    assert len(conn.added) == 1, "exactly one group add expected"
    add_attrs = conn.added[0]["attributes"]
    # The critical assertion: no empty memberUid is ever sent to LDAP.
    assert "memberUid" not in add_attrs
    assert add_attrs["cn"] == "dms-grp-imp"
    assert result["created"] is True
    assert result["members"] == []


def test_create_group_with_members_includes_memberuid(monkeypatch):
    conn = _FakeConnection(store={})
    _install_fake_ldap3(monkeypatch, conn)

    result = _manager().ensure_group_members(
        group_name="dms-grp-a", users=["alice", "bob"], resource_key="cephfs-dms:a"
    )

    assert len(conn.added) == 1
    assert conn.added[0]["attributes"]["memberUid"] == ["alice", "bob"]
    assert result["created"] is True
    assert result["members"] == ["alice", "bob"]


def test_existing_group_uses_modify_not_add(monkeypatch):
    # Pre-existing group already carrying one member -> add path must not run.
    conn = _FakeConnection(
        store={
            "dms-grp-a": {
                "cn": ["dms-grp-a"],
                "gidNumber": ["9000000"],
                "memberUid": ["alice"],
            }
        }
    )
    _install_fake_ldap3(monkeypatch, conn)

    result = _manager().ensure_group_members(
        group_name="dms-grp-a", users=["alice", "bob"], resource_key="cephfs-dms:a"
    )

    assert conn.added == [], "must not re-create an existing group"
    assert len(conn.modified) == 1
    assert sorted(result["members"]) == ["alice", "bob"]
