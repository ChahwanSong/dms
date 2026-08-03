"""도구 선택 + 후보 노드 산정. 신선한 에이전트 증거(리포트)만으로 판단하는 순수 함수."""


class PlacementError(Exception):
    def __init__(self, reason_code: str, detail: str = ""):
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(f"{reason_code}: {detail}" if detail else reason_code)


def _mount_for(report, storage_name):
    for mount in report.get("mounts", []):
        if mount.get("storage_name") == storage_name:
            return mount
    return None


def _tool_ready(report, tool):
    return any(t.get("name") == tool and t.get("status") == "Ready"
               for t in report.get("tools", []))


def _identity_ready(report, owner):
    return any(i.get("username") == owner and i.get("status") == "Ready"
               for i in report.get("identities", []))


def eligible_nodes(fresh_reports, storage_name, *, tool, owner, privileged,
                   require_writable):
    ok, reasons = [], {}
    for entry in fresh_reports:
        node = entry["node_name"]
        report = entry.get("report") or {}
        mount = _mount_for(report, storage_name)
        if mount is None or mount.get("status") != "Ready":
            reasons[node] = "missing_target_mount"
            continue
        if require_writable and mount.get("writable") is False:
            reasons[node] = "target_mount_read_only"
            continue
        if not _tool_ready(report, tool):
            reasons[node] = f"missing_tool:{tool}"
            continue
        if not privileged and not _identity_ready(report, owner):
            reasons[node] = "identity_not_ready_on_node"
            continue
        ok.append(node)
    return sorted(ok), reasons


def select_tool_and_candidates(operation, fresh_reports, *, storage_name=None,
                               source_storage=None, destination_storage=None,
                               owner, privileged):
    if operation == "scan":
        nodes, rej = eligible_nodes(fresh_reports, storage_name, tool="dscan",
                                    owner=owner, privileged=privileged,
                                    require_writable=False)
        if not nodes:
            raise PlacementError("no_eligible_nodes", storage_name)
        return {"tool": "dscan", "candidates": {"primary": nodes}, "rejections": rej}
    if operation == "rm":
        nodes, rej = eligible_nodes(fresh_reports, storage_name, tool="drm",
                                    owner=owner, privileged=privileged,
                                    require_writable=True)
        if not nodes:
            raise PlacementError("no_eligible_nodes", storage_name)
        return {"tool": "drm", "candidates": {"primary": nodes}, "rejections": rej}
    if operation == "sync":
        src_dsync, rej_s = eligible_nodes(fresh_reports, source_storage, tool="dsync",
                                          owner=owner, privileged=privileged,
                                          require_writable=False)
        dst_dsync, rej_d = eligible_nodes(fresh_reports, destination_storage,
                                          tool="dsync", owner=owner,
                                          privileged=privileged, require_writable=True)
        colocated = sorted(set(src_dsync) & set(dst_dsync))
        rejections = {"source": rej_s, "destination": rej_d}
        if colocated:
            return {"tool": "dsync", "candidates": {"primary": colocated},
                    "rejections": rejections}
        src_n, _ = eligible_nodes(fresh_reports, source_storage, tool="nsync",
                                  owner=owner, privileged=privileged,
                                  require_writable=False)
        dst_n, _ = eligible_nodes(fresh_reports, destination_storage, tool="nsync",
                                  owner=owner, privileged=privileged,
                                  require_writable=True)
        if src_n and dst_n:
            return {"tool": "nsync",
                    "candidates": {"source": src_n, "destination": dst_n},
                    "rejections": rejections}
        raise PlacementError("no_ready_sync_candidate")
    raise PlacementError("invalid_operation", operation)
