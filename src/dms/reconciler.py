"""storage-reconciler: 신선한 에이전트 증거만으로 storages.status를 재계산하는 루프 본체."""
from .repositories import Repositories


def summarize_mount_statuses(statuses: list) -> "tuple[str, str]":
    """한 스토리지의 마운트 status 목록(신선한 노드 보고의 mounts 중 storage_name 이 같은 항목 전부 -- 경로가 옛 값인 항목·
    중복 포함) → (status, status_detail). 스토리지 목록의 상태 배지와 마운트 현황 화면의 열 머리 숫자(mount_status.build)가
    **이 함수 하나**에서 나온다(2026-11-23 추출 -- 둘을 따로 세면 같은 화면 둘이 다른 숫자를 말한다). 읽기 전용(Ready +
    writable False)도 Ready 로 센다 -- status 는 writable 을 반영하지 않는다(agent.probes.probe_mounts)."""
    total = len(statuses)
    ready = sum(1 for s in statuses if s == "Ready")
    if total == 0:
        return "Unknown", "no_fresh_agent_evidence"
    if ready == total:
        return "Ready", f"ready_nodes={total}"
    if ready > 0:
        return "Degraded", f"ready_nodes={ready}/{total}"
    return "Degraded", f"no_ready_mounts (nodes={total})"


def reconcile_storages_once(repos: Repositories, *, stale_seconds: int,
                            now_iso: str | None = None) -> dict[str, str]:
    fresh = repos.agents.fresh_reports(stale_seconds=stale_seconds, now_iso=now_iso)
    result: dict[str, str] = {}
    for storage in repos.storages.list():
        if not storage["enabled"]:
            continue
        name = storage["storage_name"]
        statuses = []
        for node in fresh:
            for mount in (node["report"] or {}).get("mounts", []):
                if mount.get("storage_name") == name:
                    statuses.append(mount.get("status"))
        status, detail = summarize_mount_statuses(statuses)
        result[name] = status
        if storage["status"] != status or storage["status_detail"] != detail:
            repos.storages.set_status(name, status, detail)
    return result
