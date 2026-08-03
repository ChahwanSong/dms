from dms.config import Settings
from dms.execution import StubExecutionAdapter
from dms.execution_volcano import VolcanoExecutionAdapter
from dms.repositories import Repositories
from dms.wiring import build_execution_adapter, build_identity_resolver

BASE = {"DMS_DATABASE_URL": "sqlite:///tmp/x.db", "DMS_SHARED_TOKEN": "t",
        "DMS_ADMIN_TOKEN": "a", "DMS_SESSION_SECRET": "s"}


def test_stub_backend_default(db):
    settings = Settings.from_env(BASE)
    adapter = build_execution_adapter(settings, Repositories(db))
    assert isinstance(adapter, StubExecutionAdapter)
    assert build_identity_resolver(settings) is None


def test_volcano_backend_builds_adapter(db):
    settings = Settings.from_env({**BASE, "DMS_EXECUTION_BACKEND": "volcano",
                                  "DMS_JOB_IMAGE": "reg/img:1"})
    adapter = build_execution_adapter(settings, Repositories(db))
    assert isinstance(adapter, VolcanoExecutionAdapter)


def test_ldap_resolver_built_when_configured(db):
    settings = Settings.from_env({**BASE, "DMS_LDAP_URI": "ldap://x:389",
        "DMS_LDAP_USER_BASE": "ou=People", "DMS_LDAP_GROUP_BASE": "ou=Groups"})
    r = build_identity_resolver(settings)
    assert r is not None and hasattr(r, "resolve")
