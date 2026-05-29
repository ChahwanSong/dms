from __future__ import annotations

import argparse
from dataclasses import replace
import json
import sys
import time
from pathlib import Path

import uvicorn

from .agent import AgentReportIngestionService
from .agent_daemon import build_agent_report, config_from_env, post_report, run_loop
from .adapters import (
    StubFilesystemBackendAdapter,
    StubKubernetesNamespaceQuotaAdapter,
    StubVolcanoAdapter,
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
    observability = ObservabilityRepository(observability_db)

    if args.command == "migrate":
        print("migrations applied")
        return 0
    if args.command == "api":
        uvicorn.run("dms.api:create_app", host=args.host, port=args.port, factory=True)
        return 0
    if args.command == "planner":
        runner = lambda: Planner(repository).run_once(limit=args.limit)
        return _run_once_or_loop(runner, loop=args.loop, interval=args.interval)
    if args.command == "rm-worker":
        worker = RMWorkerRuntime(
            repository=repository,
            observability=observability,
            filesystem_adapter=StubFilesystemBackendAdapter(),
            kubernetes_adapter=StubKubernetesNamespaceQuotaAdapter(),
            worker_id=args.worker_id,
            lease_seconds=settings.worker_lease_seconds,
            backend_registry=BackendAdapterRegistry.with_phase1_defaults(repository),
        )
        return _run_once_or_loop(worker.run_once, loop=args.loop, interval=args.interval)
    if args.command == "dm-worker":
        worker = DMWorkerRuntime(
            repository=repository,
            observability=observability,
            volcano_adapter=StubVolcanoAdapter(),
            worker_id=args.worker_id,
            lease_seconds=settings.worker_lease_seconds,
            preview_ttl_seconds=settings.preview_ttl_seconds,
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
        count = callable_once()
        if count:
            print(json.dumps({"processed": count}), flush=True)
        time.sleep(interval)


def _read_json(path: str | None) -> dict:
    if path:
        return json.loads(Path(path).read_text())
    return json.loads(sys.stdin.read())


if __name__ == "__main__":
    raise SystemExit(main())
