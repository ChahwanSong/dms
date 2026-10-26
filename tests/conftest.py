import os
import uuid

import pytest
from dms.db import Database
from dms.migrations import migrate
from fastapi.testclient import TestClient
from dms.config import Settings


@pytest.fixture
def db(tmp_path):
    """기본은 테스트마다 새 sqlite. DMS_TEST_DB=postgresql + DMS_TEST_PG_URL(일회용 서버)이면 테스트마다 새 **스키마**의
    PostgreSQL 로 같은 테스트를 돈다(2026-10-26 -- 운영 방언으로 k8s 테이블·적용 루프·제출 판정을 확인; docs/BACKLOG
    「PostgreSQL 실 DB」 해소 기록). sqlite 전용 SQL(PRAGMA 등)을 직접 쓰는 테스트는 PG 갈래에서 실패할 수 있다."""
    url = os.environ.get("DMS_TEST_PG_URL")
    if os.environ.get("DMS_TEST_DB") != "postgresql" or not url:
        database = Database.connect(f"sqlite:///{tmp_path}/test.db")
        migrate(database)
        yield database
        return
    import psycopg
    schema = "dms_t_" + uuid.uuid4().hex[:12]
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(f"CREATE SCHEMA {schema}")
    database = Database.connect(f"{url}{'&' if '?' in url else '?'}options=-csearch_path%3D{schema}")
    try:
        migrate(database)
        yield database
    finally:
        database._conn.close()
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute(f"DROP SCHEMA {schema} CASCADE")


@pytest.fixture
def settings():
    # account_verification_required=False: 기존 테스트 43곳이 무인증 signup 을
    # 픽스처로 쓴다 -- 인증번호 흐름 자체는 test_api_auth 가 게이트를 켠 앱으로
    # 따로 검증한다(라이브 기본은 켜짐).
    return Settings(database_url="unused", shared_token="tok-shared",
                    admin_token="tok-admin", session_secret="sess-secret",
                    account_verification_required=False)


@pytest.fixture
def artifact_base_dir(tmp_path):
    # 아티팩트 base 로 쓸 디렉터리. tmp_path 를 그대로 base 로 쓰면 안 된다: pytest 의
    # tmp_path 는 0700(other-x 없음 → artifact_base_not_traversable)이고 이 호스트의
    # umask 002 로 만든 하위 디렉터리는 0775(g+w → artifact_base_group_writable)라
    # roundtrip_artifact_base 가 둘 다 거부한다(2026-10-07 D6/D12). 운영 base 와 같은
    # 755 로 고정해 umask·pytest 버전과 무관하게 한다.
    base = tmp_path / "artifacts"
    base.mkdir()
    base.chmod(0o755)
    return base


@pytest.fixture
def client(db, settings):
    from dms.api.app import create_app
    return TestClient(create_app(settings, db))
