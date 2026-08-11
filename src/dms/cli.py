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
        from . import wiring
        from .wiring import (build_build_runner, build_execution_adapter,
                             build_identity_resolver, build_rollout_runner)
        repos = Repositories(db)
        # 슬라이스 22 §2.6: 컨트롤러도 재연결 흔적을 남긴다(api 와 같은 훅).
        # 모듈 속성으로 호출한다 -- 테스트가 monkeypatch 로 배선을 스파이할 수
        # 있어야 하고, from-import 로 묶으면 그 시점에 이름이 고정돼 안 통한다.
        wiring.wire_reconnect_event(db, repos)
        holder = f"controller-{os.getpid()}"
        identity_resolver = build_identity_resolver(settings)
        execution_adapter = build_execution_adapter(settings, repos)
        build_runner = build_build_runner(settings)
        rollout_runner = build_rollout_runner(settings)
        if args.once:
            loops = build_loops(settings, repos, identity_resolver=identity_resolver,
                                execution_adapter=execution_adapter,
                                build_runner=build_runner,
                                rollout_runner=rollout_runner)
            results = run_all_once(loops, repos, holder)
            print(" ".join(f"{k}={v}" for k, v in results.items()))
            return 0
        run_forever(settings, repos, holder, identity_resolver=identity_resolver,
                    execution_adapter=execution_adapter, build_runner=build_runner,
                    rollout_runner=rollout_runner)
        return 0

    return 2
