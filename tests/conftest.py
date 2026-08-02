import pytest
from dms.db import Database
from dms.migrations import migrate


@pytest.fixture
def db(tmp_path):
    database = Database.connect(f"sqlite:///{tmp_path}/test.db")
    migrate(database)
    return database
