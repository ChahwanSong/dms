import argparse
import os
import sys

from .config import AgentSettings, Settings, SettingsError


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="dms")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="apply database schema")
    sub.add_parser("api", help="run the API server")
    controller = sub.add_parser("controller", help="run controller loops")
    controller.add_argument("--once", action="store_true")
    agent = sub.add_parser("agent", help="run the node agent")
    agent.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)

    if args.command == "agent":
        try:
            agent_settings = AgentSettings.from_env(os.environ)
        except SettingsError as e:
            for problem in e.problems:
                print(f"settings error: {problem}", file=sys.stderr)
            return 2
        from .agent import runner
        runner.run_loop(agent_settings, once=args.once)
        return 0

    try:
        settings = Settings.from_env(os.environ)
    except SettingsError as e:
        for problem in e.problems:
            print(f"settings error: {problem}", file=sys.stderr)
        return 2

    from .db import Database
    db = Database.connect(settings.database_url)

    if args.command == "migrate":
        from .migrations import migrate
        migrate(db)
        print("migrated")
        return 0

    if args.command == "api":
        import uvicorn
        from .api.app import create_app
        uvicorn.run(create_app(settings, db), host=settings.api_host,
                    port=settings.api_port)
        return 0

    if args.command == "controller":
        from .controller import build_loops, run_all_once, run_forever
        from .repositories import Repositories
        repos = Repositories(db)
        holder = f"controller-{os.getpid()}"
        if args.once:
            results = run_all_once(build_loops(settings, repos), repos, holder)
            print(" ".join(f"{k}={v}" for k, v in results.items()))
            return 0
        run_forever(settings, repos, holder)
        return 0

    return 2
