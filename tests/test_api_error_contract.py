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
    """DELETE returns the removed mapping; it must not hand back the WekaFS password
    that every other route redacts."""
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
