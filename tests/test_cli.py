import pytest
from dms.cli import main
from dms.db import Database


def test_migrate_command(tmp_path, monkeypatch):
    monkeypatch.setenv("DMS_DATABASE_URL", f"sqlite:///{tmp_path}/cli.db")
    monkeypatch.setenv("DMS_SHARED_TOKEN", "t")
    monkeypatch.setenv("DMS_ADMIN_TOKEN", "a")
    monkeypatch.setenv("DMS_SESSION_SECRET", "s")
    assert main(["migrate"]) == 0
    db = Database.connect(f"sqlite:///{tmp_path}/cli.db")
    assert db.query_one("SELECT version FROM schema_migrations")["version"] == "0001-initial"


def test_migrate_fails_closed_on_bad_settings(monkeypatch, capsys):
    monkeypatch.delenv("DMS_DATABASE_URL", raising=False)
    monkeypatch.setenv("DMS_SHARED_TOKEN", "CHANGE_ME")
    monkeypatch.setenv("DMS_ADMIN_TOKEN", "a")
    monkeypatch.setenv("DMS_SESSION_SECRET", "s")
    assert main(["migrate"]) == 2
    err = capsys.readouterr().err
    assert "DMS_DATABASE_URL" in err and "DMS_SHARED_TOKEN" in err


def test_unknown_command(capsys):
    with pytest.raises(SystemExit):
        main(["frobnicate"])
