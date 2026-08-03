from dms.controller import build_loops, run_all_once
from dms.identity import ResolvedIdentity, StubIdentityResolver
from dms.repositories import Repositories


class _Settings:
    agent_report_stale_seconds = 300
    reconcile_interval_seconds = 30
    retention_interval_seconds = 3600
    planner_interval_seconds = 10
    stepper_interval_seconds = 5
    agent_report_retention_days = 30
    allow_privileged_requesters = False
    privileged_requesters = frozenset()


def test_planner_loop_registered_first(db):
    loops = build_loops(_Settings(), Repositories(db))
    assert loops[0].name == "planner"
    assert loops[0].interval_seconds == 10
    assert {l.name for l in loops} == {"planner", "job-stepper", "storage-reconciler", "retention"}


def test_planner_loop_runs_end_to_end(db):
    repos = Repositories(db)
    repos.storages.create(storage_name="s1", mount_path="/mnt/s1",
                          managed_root="/mnt/s1/dms", backend_type="cephfs",
                          actor="admin")
    repos.storages.set_status("s1", "Ready", "ready_nodes=1")
    repos.control.upsert_policy("scan", max_nodes=3, procs_per_node=8,
                                queue="dms-data", default_priority="mid",
                                max_priority="high", preview_timeout_seconds=3600,
                                execution_timeout_seconds=3600, enabled=True,
                                actor="admin")
    repos.agents.ingest("n1", {"node_name": "n1",
        "mounts": [{"storage_name": "s1", "mount_path": "/mnt/s1",
                    "status": "Ready", "writable": True}],
        "tools": [{"name": "dscan", "status": "Ready"}],
        "identities": [{"username": "alice", "status": "Ready"}]})
    rid = repos.requests.create(operation="scan", requester_id="alice", actor="alice",
        resource_key="data.scan:s1:a:ff",
        payload={"storage": "s1", "target": "a", "options": {},
                 "owner_username": None}, priority="mid")
    resolver = StubIdentityResolver(
        {"alice": ResolvedIdentity("alice", 10001, 10000, ("dmsusers",), False)})
    loops = build_loops(_Settings(), repos, identity_resolver=resolver)
    run_all_once(loops, repos, holder="h1")
    assert repos.requests.get(rid)["state"] == "Planned"
    assert repos.data_jobs.list_jobs(request_id=rid)[0]["tool"] == "dscan"
