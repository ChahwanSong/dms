"""에이전트 러너: 프로브 → POST → 응답으로 설정 갱신. DB를 모르는 순수 HTTP 클라이언트."""
import sys
import time

import httpx

from ..config import AGENT_TOOL_NAMES, AgentSettings
from ..db import utc_now_iso
from .probes import probe_identities, probe_mounts, probe_os_metrics, probe_tools


def _read_text(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def build_report(node_name, storages, probe_targets, *, mountinfo_text,
                 tool_names=AGENT_TOOL_NAMES, mounts_fn=probe_mounts,
                 tools_fn=probe_tools, identities_fn=probe_identities,
                 os_fn=probe_os_metrics, read_text=_read_text) -> dict:
    return {
        "node_name": node_name,
        "probed_at": utc_now_iso(),
        "mounts": mounts_fn(storages, mountinfo_text=mountinfo_text),
        "tools": tools_fn(list(tool_names)),
        "identities": identities_fn(probe_targets),
        "os": os_fn(storages, read_text=read_text),
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
                              state["probe_targets"], mountinfo_text=mountinfo_text)
        try:
            response = self._client.post(
                "/api/agent/report", json=report,
                headers={
                    "Authorization": f"Bearer {self._settings.shared_token}",
                    "x-dms-actor": f"node:{self._settings.node_name}",
                })
            response.raise_for_status()
            body = response.json()
        except Exception as exc:
            print(f"agent report failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return state
        return {
            "storages": body.get("storages", state["storages"]),
            "probe_targets": body.get("identity_probe_targets", state["probe_targets"]),
            "interval": body.get("report_interval_seconds", state["interval"]),
        }


def run_loop(settings: AgentSettings, *, once: bool = False) -> None:
    state = {"storages": [], "probe_targets": [],
             "interval": settings.interval_seconds}
    with httpx.Client(base_url=settings.api_url, timeout=10.0) as client:
        runner = AgentRunner(settings, client)
        while True:
            state = runner.run_once(state)
            if once:
                return
            time.sleep(max(1, int(state["interval"])))
