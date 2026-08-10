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


def test_build_settings_defaults():
    s = Settings.from_env(VALID)
    assert s.build_registry == "pkg-01:5000"
    assert s.build_builder_image == "quay.io/buildah/stable:latest"
    assert s.build_repo_url == "https://github.com/ChahwanSong/dms.git"
    assert s.build_watcher_interval_seconds == 15
    assert s.build_timeout_seconds == 7200


def test_build_settings_overrides_from_env():
    # DMS_BUILD_* env가 실제로 Settings.from_env를 통해 읽히는지 -- 이 값들이
    # config.py에 존재만 하고 from_env 배선이 빠지면(예: _SERVER_INT_KEYS 등록 누락)
    # 기본값만 계속 쓰이는 조용한 회귀가 되는데, 여기서 그걸 잡는다.
    s = Settings.from_env({**VALID,
        "DMS_BUILD_REGISTRY": "reg.example:5000",
        "DMS_BUILD_BUILDER_IMAGE": "quay.io/buildah/stable:v2",
        "DMS_BUILD_REPO_URL": "https://example.com/other.git",
        "DMS_BUILD_WATCHER_INTERVAL_SECONDS": "5",
        "DMS_BUILD_TIMEOUT_SECONDS": "600"})
    assert s.build_registry == "reg.example:5000"
    assert s.build_builder_image == "quay.io/buildah/stable:v2"
    assert s.build_repo_url == "https://example.com/other.git"
    assert s.build_watcher_interval_seconds == 5
    assert s.build_timeout_seconds == 600


def test_planner_identity_grace_default_and_override():
    # _SERVER_INT_KEYS 등록이 빠지면 기본값만 계속 쓰이는 조용한 회귀 --
    # DMS_BUILD_* 와 같은 이유로 from_env 경유를 고정한다.
    assert Settings.from_env(VALID).planner_identity_grace_seconds == 300
    s = Settings.from_env({**VALID, "DMS_PLANNER_IDENTITY_GRACE_SECONDS": "60"})
    assert s.planner_identity_grace_seconds == 60
