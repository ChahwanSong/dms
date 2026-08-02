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


def test_controller_once(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DMS_DATABASE_URL", f"sqlite:///{tmp_path}/c.db")
    monkeypatch.setenv("DMS_SHARED_TOKEN", "t")
    monkeypatch.setenv("DMS_ADMIN_TOKEN", "a")
    monkeypatch.setenv("DMS_SESSION_SECRET", "s")
    assert main(["migrate"]) == 0
    assert main(["controller", "--once"]) == 0
    out = capsys.readouterr().out
    assert "storage-reconciler=ok" in out and "retention=ok" in out


def test_agent_once_uses_agent_settings(monkeypatch):
    called = {}

    def fake_run_loop(settings, *, once):
        called["node"] = settings.node_name
        called["once"] = once

    monkeypatch.setenv("DMS_AGENT_API_URL", "http://api")
    monkeypatch.setenv("DMS_AGENT_NODE_NAME", "node-x")
    monkeypatch.setenv("DMS_SHARED_TOKEN", "t")
    monkeypatch.delenv("DMS_DATABASE_URL", raising=False)  # 서버 설정 없이도 동작해야 함
    monkeypatch.setattr("dms.agent.runner.run_loop", fake_run_loop)
    from dms.cli import main as cli_main
    assert cli_main(["agent", "--once"]) == 0
    assert called == {"node": "node-x", "once": True}


def test_agent_fails_closed_on_bad_settings(monkeypatch, capsys):
    monkeypatch.delenv("DMS_AGENT_API_URL", raising=False)
    monkeypatch.setenv("DMS_SHARED_TOKEN", "t")
    from dms.cli import main as cli_main
    assert cli_main(["agent", "--once"]) == 2
    assert "DMS_AGENT_API_URL" in capsys.readouterr().err
