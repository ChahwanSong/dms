from dms.config import Settings

VALID = {"DMS_DATABASE_URL": "sqlite:///tmp/dms.db", "DMS_SHARED_TOKEN": "tok",
         "DMS_ADMIN_TOKEN": "adm", "DMS_SESSION_SECRET": "sess"}


def test_phase3c_defaults():
    s = Settings.from_env(VALID)
    assert s.ldap_uri == "" and s.ldap_user_base == "" and s.ldap_group_base == ""
    assert s.execution_backend == "stub"
    assert s.k8s_namespace == "dms"


def test_phase3c_overrides():
    s = Settings.from_env({**VALID,
        "DMS_LDAP_URI": "ldap://10.10.10.30:389",
        "DMS_LDAP_USER_BASE": "ou=People,dc=dms,dc=local",
        "DMS_LDAP_GROUP_BASE": "ou=Groups,dc=dms,dc=local",
        "DMS_EXECUTION_BACKEND": "volcano",
        "DMS_JOB_IMAGE": "pkg-01:5000/dms-mpifileutils:latest"})
    assert s.ldap_uri == "ldap://10.10.10.30:389"
    assert s.execution_backend == "volcano"
    assert s.job_image == "pkg-01:5000/dms-mpifileutils:latest"
