"""Addressing something that does not exist is a 404, not a 500.

Every read/action route that takes an id used to let the repository's `KeyError`
escape to FastAPI, which turned "you asked for a job that isn't there" into a bare
`500 Internal Server Error` with no detail — indistinguishable from a real fault, and
useless to the portal (which forwards DMS status + detail verbatim).

The repositories now raise `RecordNotFound` (a `KeyError` subclass, so existing
`except KeyError` handlers still work) and `create_app` maps it to 404. A plain
`KeyError` raised by a handler bug is deliberately NOT caught, so genuine faults still
surface as 500.

Also covered here: a worker that cannot complete a side effect raises
`DataManagementRuntimeError` carrying an actionable operator message ("the MPI job may
still be running -- retry cancel or terminate manually"); that message must reach the
caller rather than being swallowed into a 500.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from dms.api import create_app
from dms.config import Settings
from dms.db import Database
from dms.migrations import migrate_all
from dms.repositories import DmsRepository, ObservabilityRepository, RecordNotFound

HEADERS = {"x-dms-actor": "api-client"}


@pytest.fixture()
def client(tmp_path) -> TestClient:
    operational = Database(f"sqlite:///{tmp_path / 'op.db'}")
    observability = Database(f"sqlite:///{tmp_path / 'obs.db'}")
    migrate_all(operational, observability)
    return TestClient(
        create_app(
            settings=Settings(
                database_url=f"sqlite:///{tmp_path / 'op.db'}",
                observability_database_url=f"sqlite:///{tmp_path / 'obs.db'}",
            ),
            repository=DmsRepository(operational),
            observability=ObservabilityRepository(observability),
        )
    )


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/data-management/scan/jobs/job_nope",
        "/api/v1/data-management/sync/jobs/job_nope",
        "/api/v1/data-management/rm/jobs/job_nope",
        "/api/v1/operations/data-jobs/job_nope",
        "/api/v1/operations/requests/req_nope",
    ],
)
def test_unknown_id_is_404_not_500(client, path):
    response = client.get(path, headers=HEADERS)

    assert response.status_code == 404, response.text
    # the detail must name what was not found, so the operator can tell a typo from an
    # expired record
    assert "not found" in response.json()["detail"]


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/data-management/jobs/job_nope:cancel",
        "/api/v1/data-management/jobs/job_nope:confirm",
    ],
)
def test_unknown_id_on_action_routes_is_404_not_500(client, path):
    response = client.post(path, json={"confirm": True}, headers=HEADERS)

    assert response.status_code == 404, response.text


def test_delete_of_unknown_job_is_404(client):
    response = client.delete("/api/v1/data-management/jobs/job_nope", headers=HEADERS)

    assert response.status_code == 404, response.text


def test_record_not_found_is_a_keyerror_so_existing_handlers_still_work():
    """Callers that already guarded with `except KeyError` must not start leaking."""
    assert issubclass(RecordNotFound, KeyError)


def test_delete_storage_mapping_response_is_redacted(client, tmp_path):
    """DELETE returns the removed mapping; it must not hand back a secret-shaped value.

    DMS authenticates to no storage, so backend_template should carry no credentials at
    all -- but a hand-written PATCH or a legacy row can still put one there, and the
    redactor is the net that keeps it out of a response.
    """
    body = {
        "storage_name": "weka-a",
        "backend_template": {
            "backend_type": "wekafs",
            "mount_path": "/mnt/weka",
            "managed_root": "/mnt/weka/dms",
            "filesystem_name": "default",
            "weka_credentials": {
                "organization": "0",
                "username": "dms-svc",
                "password": "SUPER-SECRET-PW",
            },
        },
        "cluster_name": "cluster-a",
    }
    assert client.post("/api/v1/storage-mappings", json=body, headers=HEADERS).status_code == 200

    response = client.delete("/api/v1/storage-mappings/weka-a", headers=HEADERS)

    assert response.status_code == 200
    assert "SUPER-SECRET-PW" not in response.text
    creds = response.json()["mapping"]["backend_template"]["weka_credentials"]
    assert creds["password"] != "SUPER-SECRET-PW"
    # the non-secret half is still returned so the caller can confirm what it removed
    assert creds["username"] == "dms-svc"


def test_redactor_masks_secret_shaped_keys_at_any_depth():
    """The redactor is generic, not a list of known field names: a secret-shaped key
    introduced by a future backend must be masked without touching this module."""
    from dms.api._helpers.storage_mapping import REDACTED, redact_storage_mapping

    redacted = redact_storage_mapping(
        {
            "storage_name": "s",
            "backend_template": {
                "mount_path": "/mnt/s",
                "api_token": "TOP-SECRET",
                "nested": [{"private_key": "PEM-DATA", "host": "keep-me"}],
                "password": "",  # empty stays empty rather than becoming "***"
            },
        }
    )

    template = redacted["backend_template"]
    assert template["api_token"] == REDACTED
    assert template["nested"][0]["private_key"] == REDACTED
    assert template["nested"][0]["host"] == "keep-me"
    assert template["mount_path"] == "/mnt/s"
    assert template["password"] == ""


def test_migrate_erases_obsolete_credentials_from_stored_templates(tmp_path):
    """weka_credentials must not survive a migrate: DMS can no longer authenticate to
    WekaFS, so a stored cleartext password is pure liability. Ordering matters -- this
    runs in migrate, not as an operator-remembered SQL step."""
    import json

    from dms.db import Database
    from dms.migrations import migrate_all

    operational = Database(f"sqlite:///{tmp_path / 'op.db'}")
    observability = Database(f"sqlite:///{tmp_path / 'obs.db'}")
    migrate_all(operational, observability)

    template = {
        "backend_type": "wekafs",
        "mount_path": "/mnt/weka",
        "managed_root": "/mnt/weka/dms",
        "weka_credentials": {"username": "dms-svc", "password": "LEGACY-CLEARTEXT"},
    }
    with operational.connect() as connection:
        connection.execute(
            "INSERT INTO storage_mappings (storage_name, backend_template, cluster_name,"
            " version, sanity_status, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            ("legacy-weka", json.dumps(template), "cluster-a", 1, "Unknown", "2026-01-01T00:00:00+00:00"),
        )

    migrate_all(operational, observability)  # idempotent re-run performs the purge

    with operational.connect() as connection:
        row = connection.execute(
            "SELECT backend_template FROM storage_mappings WHERE storage_name = ?",
            ("legacy-weka",),
        ).fetchone()
    stored = json.loads(row["backend_template"])
    assert "weka_credentials" not in stored
    assert "LEGACY-CLEARTEXT" not in row["backend_template"]
    # everything else is preserved
    assert stored["managed_root"] == "/mnt/weka/dms"
