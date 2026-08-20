import pytest
from dms.db import Database
from dms.migrations import migrate
from fastapi.testclient import TestClient
from dms.config import Settings


@pytest.fixture
def db(tmp_path):
    database = Database.connect(f"sqlite:///{tmp_path}/test.db")
    migrate(database)
    return database


@pytest.fixture
def settings():
    # account_verification_required=False: 기존 테스트 43곳이 무인증 signup 을
    # 픽스처로 쓴다 -- 인증번호 흐름 자체는 test_api_auth 가 게이트를 켠 앱으로
    # 따로 검증한다(라이브 기본은 켜짐).
    return Settings(database_url="unused", shared_token="tok-shared",
                    admin_token="tok-admin", session_secret="sess-secret",
                    account_verification_required=False)


@pytest.fixture
def client(db, settings):
    from dms.api.app import create_app
    return TestClient(create_app(settings, db))
