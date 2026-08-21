import pytest
from dms.config import Settings, SettingsError

VALID = {
    "DMS_DATABASE_URL": "sqlite:///tmp/dms.db",
    "DMS_SHARED_TOKEN": "tok-abc",
    "DMS_ADMIN_TOKEN": "adm-xyz",
    "DMS_SESSION_SECRET": "sess-123",
}


def test_valid_env():
    s = Settings.from_env(VALID)
    assert s.database_url == "sqlite:///tmp/dms.db"
    assert s.api_port == 8080


def test_missing_and_placeholder_collected():
    env = dict(VALID)
    env.pop("DMS_DATABASE_URL")
    env["DMS_SHARED_TOKEN"] = "CHANGE_ME"
    env["DMS_ADMIN_TOKEN"] = "REPLACE_WITH_TOKEN"
    with pytest.raises(SettingsError) as e:
        Settings.from_env(env)
    text = str(e.value)
    assert "DMS_DATABASE_URL" in text
    assert "DMS_SHARED_TOKEN" in text
    assert "DMS_ADMIN_TOKEN" in text


def test_committed_manifest_placeholders_are_rejected():
    # deploy/k8s/20-secret.example.yaml 이 담고 있는 값 그대로. 이 값들은 공개
    # 저장소에 적혀 있고 DMS_SHARED_TOKEN 은 Bearer 로 admin 을 준다 -- 접미사가
    # 붙었다는 이유로 통과시키면 누구나 아는 admin 토큰으로 기동한다.
    env = {
        "DMS_DATABASE_URL":
            "postgresql://dmsapp:CHANGE_ME_DB_PASSWORD@10.10.10.30:5432/dmsdb",
        "DMS_SHARED_TOKEN": "CHANGE_ME_SHARED_TOKEN",
        "DMS_ADMIN_TOKEN": "CHANGE_ME_ADMIN_TOKEN",
        "DMS_SESSION_SECRET": "CHANGE_ME_SESSION_SECRET",
    }
    with pytest.raises(SettingsError) as e:
        Settings.from_env(env)
    text = str(e.value)
    for key in env:
        assert key in text


def test_placeholder_check_does_not_reject_real_values():
    # 실제 자격증명이 우연히 그 단어를 품고 있을 뿐인 경우까지 막지는 않는다.
    s = Settings.from_env({**VALID, "DMS_SESSION_SECRET": "xchange_mexico"})
    assert s.session_secret == "xchange_mexico"


def test_port_parsing():
    s = Settings.from_env({**VALID, "DMS_API_PORT": "9000"})
    assert s.api_port == 9000
    with pytest.raises(SettingsError):
        Settings.from_env({**VALID, "DMS_API_PORT": "not-a-number"})


def test_build_preflight_timeout_default_and_env_override():
    # 슬라이스 21 §2.5: 프로브 대기 상한. _SERVER_INT_KEYS 튜플에만 넣으면
    # from_env 의 **extra 가 배선한다 -- 필드/키 양쪽이 실제로 이어졌는지 고정.
    assert Settings.from_env(VALID).build_preflight_timeout_seconds == 180
    s = Settings.from_env({**VALID, "DMS_BUILD_PREFLIGHT_TIMEOUT_SECONDS": "60"})
    assert s.build_preflight_timeout_seconds == 60


def test_readyz_exit_failures_default_and_env_override():
    # 슬라이스 22 §2.4: 연속 readyz 실패 자기 종료 임계(10s 프로브 기준 30 ≈ 5분,
    # 0=비활성). _SERVER_INT_KEYS 튜플에만 넣으면 from_env 의 **extra 가
    # 배선한다 -- 필드/키 양쪽이 실제로 이어졌는지 고정(빌드 프리플라이트 선례).
    assert Settings.from_env(VALID).readyz_exit_failures == 30
    assert Settings.from_env(
        {**VALID, "DMS_READYZ_EXIT_FAILURES": "0"}).readyz_exit_failures == 0


def test_artifact_download_max_bytes_default():
    # 슬라이스 26: 아티팩트 전체 다운로드 상한(뷰 256KB 와 별개). 기본 256MiB.
    # 튜플에만 넣고 dataclass 필드를 빼먹으면 **extra 가 TypeError 로 기동 실패하고,
    # 필드만 넣으면 env 가 조용히 무시된다 -- 양쪽 배선을 여기서 고정한다.
    assert Settings.from_env(VALID).artifact_download_max_bytes == 268435456


def test_artifact_download_max_bytes_env_override():
    s = Settings.from_env(
        {**VALID, "DMS_ARTIFACT_DOWNLOAD_MAX_BYTES": "1048576"})
    assert s.artifact_download_max_bytes == 1048576


def test_require_auth_bind_refuses_startup_without_credentials():
    # 슬라이스 28(BACKLOG §2.3): 인증 바인드를 의도(플래그 true)했는데 자격증명이
    # 없으면 익명으로 조용히 떨어지는 대신 기동을 거부한다 -- 운영자가 "인증
    # 바인드로 돌고 있다"고 믿는 채 익명으로 도는 상태가 이 항목의 실체다.
    with pytest.raises(SettingsError) as e:
        Settings.from_env({**VALID, "DMS_LDAP_REQUIRE_AUTH_BIND": "true"})
    text = str(e.value)
    assert "DMS_LDAP_BIND_DN" in text
    assert "DMS_LDAP_BIND_PW" in text


def test_require_auth_bind_rejects_placeholder_credentials():
    # 빈 값만 걸면 20-secret.example.yaml 의 CHANGE_ME 류가 실 DN 으로 흘러간다 --
    # _is_placeholder 를 재사용해 결측과 자리표시자를 같은 구멍으로 본다(기존 규약).
    with pytest.raises(SettingsError):
        Settings.from_env({**VALID, "DMS_LDAP_REQUIRE_AUTH_BIND": "1",
                           "DMS_LDAP_BIND_DN": "cn=CHANGE_ME_BIND_DN,dc=dms,dc=local",
                           "DMS_LDAP_BIND_PW": "REPLACE_WITH_BIND_PW"})


def test_require_auth_bind_passes_with_real_credentials():
    s = Settings.from_env({**VALID, "DMS_LDAP_REQUIRE_AUTH_BIND": "true",
                           "DMS_LDAP_BIND_DN": "cn=dms-svc,ou=People,dc=dms,dc=local",
                           "DMS_LDAP_BIND_PW": "s3cret"})
    assert s.ldap_require_auth_bind is True
    assert s.ldap_bind_dn == "cn=dms-svc,ou=People,dc=dms,dc=local"


def test_anonymous_bind_remains_the_default():
    # 플래그 미설정이면 현행 유지 -- 테스트베드는 익명 바인드가 실 구성이다
    # (20-secret.example.yaml). 기본값을 true 로 하면 이 배포 자체가 못 뜬다.
    s = Settings.from_env(VALID)
    assert s.ldap_require_auth_bind is False
    assert s.ldap_bind_dn == ""


def test_user_allowed_operations_default_and_override():
    # 기본 {sync} -- 사용자는 sync 만, rm·scan 은 잠김(2026-08-20).
    assert Settings.from_env(VALID).user_allowed_operations == frozenset({"sync"})
    # env 로 넓히면 잠금 해제(예: rm 도 허용)
    s = Settings.from_env({**VALID, "DMS_USER_ALLOWED_OPERATIONS": "sync,rm"})
    assert s.user_allowed_operations == frozenset({"sync", "rm"})


def test_account_verification_env_defaults_on():
    # 운영 경로(from_env)는 fail-closed: 명시로 끄지 않는 한 인증번호 필수.
    # (dataclass 직접 생성 기본은 False -- 테스트 전용 관례, config.py 주석.)
    assert Settings.from_env(VALID).account_verification_required is True
    assert Settings.from_env({**VALID, "DMS_ACCOUNT_VERIFICATION_REQUIRED": "false"}
                             ).account_verification_required is False
    assert Settings.from_env(VALID).account_email_domain == "samsung.com"


def test_session_cookie_secure_parses_and_defaults_off():
    # TLS 종단(ingress) 배포 대비 게이트. 기본 false 인 이유는 테스트베드의
    # HTTP 경로(NodePort 30080·port-forward)가 살아 있어야 하기 때문.
    assert Settings.from_env(VALID).session_cookie_secure is False
    assert Settings.from_env(
        {**VALID, "DMS_SESSION_COOKIE_SECURE": "true"}).session_cookie_secure is True
