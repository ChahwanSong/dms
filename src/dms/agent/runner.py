"""에이전트 러너: 프로브 → POST → 응답으로 설정 갱신. DB를 모르는 순수 HTTP 클라이언트."""
import sys
import time
from typing import Callable, Optional

import httpx

from ..config import AGENT_TOOL_NAMES, AgentSettings
from ..db import utc_now_iso
from .probes import (probe_artifact_base, probe_identities, probe_mounts,
                     probe_os_metrics, probe_tools)


def _read_text(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def build_report(node_name, storages, probe_targets, *, mountinfo_text,
                 tool_names=AGENT_TOOL_NAMES, mounts_fn=None, tools_fn=None,
                 identities_fn=None, os_fn=None, read_text=None,
                 net_dev_path="/proc/net/dev", virtual_net_path="",
                 artifact_base_path=None, artifact_base_fn=None) -> dict:
    mounts_fn = mounts_fn or probe_mounts
    tools_fn = tools_fn or probe_tools
    identities_fn = identities_fn or probe_identities
    os_fn = os_fn or probe_os_metrics
    artifact_base_fn = artifact_base_fn or probe_artifact_base
    # 이 폴백을 지우면 probe_os_metrics 가 read_text=None 을 받는다 -- 그 안의
    # 호출은 전부 try/except Exception 이라 예외가 삼켜지고 **OS 지표 전체가
    # 조용히 null** 이 된다(테스트는 os_fn 을 주입하므로 초록을 유지한다).
    read_text = read_text or _read_text
    return {
        "node_name": node_name,
        "probed_at": utc_now_iso(),
        "mounts": mounts_fn(storages, mountinfo_text=mountinfo_text),
        "tools": tools_fn(list(tool_names)),
        "identities": identities_fn(probe_targets),
        "os": os_fn(storages, read_text=read_text, net_dev_path=net_dev_path,
                    virtual_net_path=virtual_net_path),
        # 슬라이스 18: mounts 와 **별도** 최상위 필드 -- reconciler 가 mounts 를
        # storages.status 로 매핑하므로 거기 섞으면 스토리지 판정이 오염된다.
        # 대상 경로가 아직 없으면(부트스트랩) None.
        "artifact_base": artifact_base_fn(artifact_base_path),
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
                              net_dev_path=self._settings.net_dev_path,
                              virtual_net_path=self._settings.virtual_net_path,
                              artifact_base_path=state.get("artifact_base_path"))
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
            # 에이전트는 ConfigMap 을 envFrom 으로 받지 않는다(50-agent-daemonset
            # .yaml 머리 주석) -- base 를 아는 유일한 경로가 이 응답 필드다(설계
            # §2.4b). 구버전 서버 응답에는 키가 없다 -- 기존 값을 유지한다(지우면
            # 다음 리포트부터 프로브가 사라져 화면이 영구 "확인 대기 중"이 된다).
            artifact_base_path = body.get("artifact_base_path",
                                          state.get("artifact_base_path"))
            if (not isinstance(storages, list) or not isinstance(probe_targets, list)
                    or not isinstance(interval, int) or isinstance(interval, bool)
                    or not isinstance(artifact_base_path, (str, type(None)))):
                raise ValueError("malformed response fields")
            new_state = {"storages": storages, "probe_targets": probe_targets,
                         "interval": interval,
                         "artifact_base_path": artifact_base_path}
        except Exception as exc:
            print(f"agent report failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return state
        return new_state


def run_loop(settings: AgentSettings, *, once: bool = False,
            client_factory: Optional[Callable[[], httpx.Client]] = None) -> None:
    state = {"storages": [], "probe_targets": [],
             "interval": settings.interval_seconds, "artifact_base_path": None}
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
