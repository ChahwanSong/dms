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
    return Settings(database_url="unused", shared_token="tok-shared",
                    admin_token="tok-admin", session_secret="sess-secret")


@pytest.fixture
def client(db, settings):
    from dms.api.app import create_app
    return TestClient(create_app(settings, db))
