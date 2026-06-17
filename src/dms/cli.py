from __future__ import annotations

import argparse
from dataclasses import replace
import json
import os
import sys
import time
from pathlib import Path

import uvicorn

from .agent import AgentReportIngestionService
from .agent_daemon import build_agent_report, config_from_env, post_report, run_loop
from .adapters import (
    LdapIdentityLookupAdapter,
    StubFilesystemBackendAdapter,
    StubKubernetesNamespaceQuotaAdapter,
    volcano_adapter_from_settings,
)
from .backend_registry import BackendAdapterRegistry
from .config import Settings
from .db import Database
from .domain import AgentReport
from .migrations import migrate_all
from .planner import Planner
from .repositories import DmsRepository, ObservabilityRepository
from .workers import DMWorkerRuntime, RMWorkerRuntime


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dms")
    subcommands = parser.add_subparsers(dest="command", required=True)

    subcommands.add_parser("migrate")

    api = subcommands.add_parser("api")
    api.add_argument("--host", default="0.0.0.0")
    api.add_argument("--port", type=int, default=8080)

    planner = subcommands.add_parser("planner")
    planner.add_argument("--loop", action="store_true")
    planner.add_argument("--interval", type=float, default=5.0)
    planner.add_argument("--limit", type=int, default=50)

    rm_worker = subcommands.add_parser("rm-worker")
    rm_worker.add_argument("--worker-id", required=True)
    rm_worker.add_argument("--loop", action="store_true")
    rm_worker.add_argument("--interval", type=float, default=5.0)

    dm_worker = subcommands.add_parser("dm-worker")
    dm_worker.add_argument("--worker-id", required=True)
    dm_worker.add_argument("--loop", action="store_true")
    dm_worker.add_argument("--interval", type=float, default=5.0)

    agent = subcommands.add_parser("agent-submit")
    agent.add_argument("--actor", required=True)
    agent.add_argument("--report-json", help="path to report JSON; stdin is used when omitted")

    agent_probe = subcommands.add_parser("agent-probe")
    agent_probe.add_argument("--once", action="store_true", help="run one probe and print JSON")
    agent_probe.add_argument("--post", action="store_true", help="post the report to DMS API")

    sanity_reconciler = subcommands.add_parser("sanity-reconciler")
    sanity_reconciler.add_argument("--loop", action="store_true")
    sanity_reconciler.add_argument("--interval", type=float, default=30.0)

    agent_loop = subcommands.add_parser("agent-loop")
    agent_loop.add_argument("--interval", type=float)

    args = parser.parse_args(argv)

    if args.command == "agent-probe":
        config = config_from_env()
        report = build_agent_report(config)
        if args.post:
            print(json.dumps(post_report(config, report)))
        else:
            print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    if args.command == "agent-loop":
        config = config_from_env()
        if args.interval is not None:
            config = replace(config, report_interval_seconds=args.interval)
        return run_loop(config)

    settings = Settings.from_env()
    operational = Database(settings.database_url)
    observability_db = Database(settings.observability_database_url)
    migrate_all(operational, observability_db)
    repository = DmsRepository(operational)
    repository.bootstrap_data_management_policies(settings.data_management_policy_defaults())
    observability = ObservabilityRepository(observability_db)

    if args.command == "migrate":
        print("migrations applied")
        return 0
    if args.command == "api":
        uvicorn.run("dms.api:create_app", host=args.host, port=args.port, factory=True)
        return 0
    if args.command == "planner":
        planner = Planner(
            repository,
            sanity_ttl_seconds=(
                settings.sanity_ttl_seconds
                if settings.sanity_planner_gate_enabled
                else None
            ),
        )
        runner = lambda: planner.run_once(limit=args.limit)
        return _run_once_or_loop(runner, loop=args.loop, interval=args.interval)
    if args.command == "sanity-reconciler":
        from .sanity_reconciler import build_sanity_service, reconcile_once

        sanity_service = build_sanity_service(repository, settings)
        runner = lambda: reconcile_once(
            repository,
            sanity_service,
            observability=observability,
            heartbeat_path=settings.sanity_reconcile_heartbeat_path,
        )
        return _run_once_or_loop(runner, loop=args.loop, interval=args.interval)
    if args.command == "rm-worker":
        worker = RMWorkerRuntime(
            repository=repository,
            observability=observability,
            filesystem_adapter=StubFilesystemBackendAdapter(),
            kubernetes_adapter=StubKubernetesNamespaceQuotaAdapter(),
            worker_id=args.worker_id,
            lease_seconds=settings.worker_lease_seconds,
            backend_registry=BackendAdapterRegistry.with_live_defaults(
                repository, settings
            ),
        )
        return _run_once_or_loop(worker.run_once, loop=args.loop, interval=args.interval)
    if args.command == "dm-worker":
        # dm-worker runs as root and writes job metadata into the shared artifact FS;
        # restrict its umask so those files are owner-only (the locked-down artifact
        # must not be world-readable by other tenants' job pods).
        os.umask(0o077)
        # Require BOTH URI and base DN: a half-configured adapter raises in from_settings,
        # which would crash the worker at startup. Gate on both so partial config falls
        # back to None -> clean per-request `ldap_not_configured` rejection (fail closed,
        # worker stays up) instead of a boot crash.
        identity_lookup = (
            LdapIdentityLookupAdapter.from_settings(settings)
            if (settings.ldap_uri and settings.ldap_base_dn)
            else None
        )
        worker = DMWorkerRuntime(
            repository=repository,
            observability=observability,
            volcano_adapter=volcano_adapter_from_settings(settings),
            worker_id=args.worker_id,
            lease_seconds=settings.worker_lease_seconds,
            preview_ttl_seconds=settings.preview_ttl_seconds,
            identity_lookup=identity_lookup,
            identity_provider=settings.dm_identity_provider,
            min_uid=settings.dm_min_uid,
            min_gid=settings.dm_min_gid,
        )
        return _run_once_or_loop(worker.run_once, loop=args.loop, interval=args.interval)
    if args.command == "agent-submit":
        payload = _read_json(args.report_json)
        report = AgentReport.model_validate(payload)
        report_id = AgentReportIngestionService(repository, observability).ingest(
            report, actor=args.actor
        )
        print(json.dumps({"report_id": report_id, "status": "Fresh"}))
        return 0
    parser.error("unknown command")
    return 2


def _run_once_or_loop(callable_once, *, loop: bool, interval: float) -> int:
    if not loop:
        count = callable_once()
        print(json.dumps({"processed": count}))
        return 0
    while True:
        try:
            count = callable_once()
            if count:
                print(json.dumps({"processed": count}), flush=True)
        except Exception as exc:  # noqa: BLE001 - long-running loops must survive one failed unit.
            print(
                json.dumps(
                    {
                        "processed": 0,
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                    }
                ),
                file=sys.stderr,
                flush=True,
            )
        time.sleep(interval)


def _read_json(path: str | None) -> dict:
    if path:
        return json.loads(Path(path).read_text())
    return json.loads(sys.stdin.read())


if __name__ == "__main__":
    raise SystemExit(main())
