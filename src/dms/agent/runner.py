"""에이전트 러너: 프로브 → POST → 응답으로 설정 갱신. DB를 모르는 순수 HTTP 클라이언트."""
import sys
import time
from typing import Callable, Optional

import httpx

from ..config import AGENT_TOOL_NAMES, AgentSettings
from ..db import utc_now_iso
from .probes import probe_identities, probe_mounts, probe_os_metrics, probe_tools


def _read_text(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def build_report(node_name, storages, probe_targets, *, mountinfo_text,
                 tool_names=AGENT_TOOL_NAMES, mounts_fn=None, tools_fn=None,
                 identities_fn=None, os_fn=None, read_text=None,
                 net_dev_path="/proc/net/dev") -> dict:
    mounts_fn = mounts_fn or probe_mounts
    tools_fn = tools_fn or probe_tools
    identities_fn = identities_fn or probe_identities
    os_fn = os_fn or probe_os_metrics
    read_text = read_text or _read_text
    return {
        "node_name": node_name,
        "probed_at": utc_now_iso(),
        "mounts": mounts_fn(storages, mountinfo_text=mountinfo_text),
        "tools": tools_fn(list(tool_names)),
        "identities": identities_fn(probe_targets),
        "os": os_fn(storages, read_text=read_text, net_dev_path=net_dev_path),
    }


class AgentRunner:
    def __init__(self, settings: AgentSettings, client: httpx.Client):
        self._settings = settings
        self._client = client

    def run_once(self, state: dict) -> dict:
        try:
            mountinfo_text = _read_text(self._settings.mountinfo_path)
        except OSError:
            mountinfo_text = ""
        report = build_report(self._settings.node_name, state["storages"],
                              state["probe_targets"], mountinfo_text=mountinfo_text,
                              net_dev_path=self._settings.net_dev_path)
        try:
            response = self._client.post(
                "/api/agent/report", json=report,
                headers={
                    "Authorization": f"Bearer {self._settings.shared_token}",
                    "x-dms-actor": f"node:{self._settings.node_name}",
                })
            response.raise_for_status()
            body = response.json()
            if not isinstance(body, dict):
                raise ValueError(f"non-dict response: {type(body).__name__}")
            storages = body.get("storages", state["storages"])
            probe_targets = body.get("identity_probe_targets", state["probe_targets"])
            interval = body.get("report_interval_seconds", state["interval"])
            if (not isinstance(storages, list) or not isinstance(probe_targets, list)
                    or not isinstance(interval, int) or isinstance(interval, bool)):
                raise ValueError("malformed response fields")
            new_state = {"storages": storages, "probe_targets": probe_targets,
                         "interval": interval}
        except Exception as exc:
            print(f"agent report failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return state
        return new_state


def run_loop(settings: AgentSettings, *, once: bool = False,
            client_factory: Optional[Callable[[], httpx.Client]] = None) -> None:
    state = {"storages": [], "probe_targets": [],
             "interval": settings.interval_seconds}
    if client_factory is None:
        client_factory = lambda: httpx.Client(base_url=settings.api_url, timeout=10.0)
    with client_factory() as client:
        runner = AgentRunner(settings, client)
        if once:
            state = runner.run_once(state)   # 부트스트랩 사이클: 빈 상태로 설정(storages) 수신
            runner.run_once(state)           # 본 사이클: 받은 storages로 실제 프로브
            return
        while True:
            state = runner.run_once(state)
            time.sleep(max(1, int(state["interval"])))
