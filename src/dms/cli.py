import argparse
import os
import socket
import sys

from .config import AgentSettings, Settings, SettingsError


def controller_holder(environ=None) -> str:
    """컨트롤러 루프 리스의 holder(2026-10-25). 파드 이름(HOSTNAME)을 넣는다 -- 이미지 ENTRYPOINT 가 exec 형이라 모든 컨트롤러
    파드에서 PID 가 1 이고, 예전 holder(f"controller-{pid}")는 롤아웃 중 겹친 두 파드가 같은 holder 라 리스가 서로를 막지 못해
    같은 루프가 두 벌 돌았다(k8s-inventory 적용은 run 상태 CAS 로 멱등하지만, 리스가 제 역할을 해야 한다)."""
    env = os.environ if environ is None else environ
    host = (env.get("HOSTNAME") or "").strip() or socket.gethostname()
    return f"controller-{host}-{os.getpid()}"


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
                             build_purge_runner,
                             build_rollout_runner)
        repos = Repositories(db)
        # 슬라이스 22 §2.6: 컨트롤러도 재연결 흔적을 남긴다(api 와 같은 훅).
        # 모듈 속성으로 호출한다 -- 테스트가 monkeypatch 로 배선을 스파이할 수
        # 있어야 하고, from-import 로 묶으면 그 시점에 이름이 고정돼 안 통한다.
        wiring.wire_reconnect_event(db, repos)
        holder = controller_holder()
        execution_adapter = build_execution_adapter(settings, repos)
        build_runner = build_build_runner(settings, repos)
        rollout_runner = build_rollout_runner(settings)
        # 요청 삭제 정리(2026-10-08): 이 배선이 빠지면 request-purge 루프가 등록되지 않아 삭제한 요청의 파드·
        # 결과 파일이 영원히 남는다(test_cli 가 request-purge=ok 로 고정).
        purge_runner = build_purge_runner(settings, repos)
        if args.once:
            loops = build_loops(settings, repos,
                                execution_adapter=execution_adapter,
                                build_runner=build_runner,
                                rollout_runner=rollout_runner,
                                purge_runner=purge_runner)
            results = run_all_once(loops, repos, holder)
            print(" ".join(f"{k}={v}" for k, v in results.items()))
            return 0
        run_forever(settings, repos, holder,
                    execution_adapter=execution_adapter, build_runner=build_runner,
                    rollout_runner=rollout_runner, purge_runner=purge_runner)
        return 0

    return 2
