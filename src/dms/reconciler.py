"""storage-reconciler: 신선한 에이전트 증거만으로 storages.status를 재계산하는 루프 본체."""
from .repositories import Repositories


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
        total = len(statuses)
        ready = sum(1 for s in statuses if s == "Ready")
        if total == 0:
            status, detail = "Unknown", "no_fresh_agent_evidence"
        elif ready == total:
            status, detail = "Ready", f"ready_nodes={total}"
        elif ready > 0:
            status, detail = "Degraded", f"ready_nodes={ready}/{total}"
        else:
            status, detail = "Degraded", f"no_ready_mounts (nodes={total})"
        result[name] = status
        if storage["status"] != status or storage["status_detail"] != detail:
            repos.storages.set_status(name, status, detail)
    return result
