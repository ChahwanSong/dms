from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import getpass
import grp
import json
import os
from pathlib import Path
import pwd
import shutil
import socket
import ssl
import subprocess
import time
from typing import Any
from urllib import error, parse, request

DEFAULT_AGENT_CONFIG_PATH = "/etc/dms/agent/storages.json"
DEFAULT_TOOL_NAMES = ("dsync", "nsync", "drm", "dscan", "kubectl")
SERVICE_ACCOUNT_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
SERVICE_ACCOUNT_CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"


@dataclass(frozen=True)
class AgentDaemonConfig:
    api_url: str | None
    cluster_name: str
    node_name: str
    node_uid: str | None
    worker_role: str
    report_interval_seconds: float = 60.0
    report_timeout_seconds: float = 5.0
    storages: list[dict[str, Any]] = field(default_factory=list)
    tool_names: list[str] = field(default_factory=lambda: list(DEFAULT_TOOL_NAMES))
    credential_files: list[str] = field(default_factory=list)
    network_endpoints: list[str] = field(default_factory=list)
    identity_users: list[str] = field(default_factory=list)
    auth_shared_token: str | None = None
    pod_name: str | None = None
    pod_namespace: str | None = None
    mountinfo_path: str = "/proc/self/mountinfo"


class AgentPostError(RuntimeError):
    pass


class InClusterKubernetesClient:
    def __init__(
        self,
        *,
        host: str,
        port: str,
        token_path: str = SERVICE_ACCOUNT_TOKEN_PATH,
        ca_path: str = SERVICE_ACCOUNT_CA_PATH,
        timeout_seconds: float = 5.0,
    ) -> None:
        self.base_url = f"https://{host}:{port}"
        self.token_path = token_path
        self.timeout_seconds = timeout_seconds
        self.context = (
            ssl.create_default_context(cafile=ca_path)
            if Path(ca_path).exists()
            else ssl.create_default_context()
        )

    @classmethod
    def from_env(
        cls, environ: dict[str, str] | None = None, *, timeout_seconds: float = 5.0
    ) -> "InClusterKubernetesClient | None":
        environ = environ or os.environ
        host = environ.get("KUBERNETES_SERVICE_HOST")
        port = environ.get("KUBERNETES_SERVICE_PORT", "443")
        if not host:
            return None
        return cls(host=host, port=port, timeout_seconds=timeout_seconds)

    def node_uid(self, node_name: str) -> tuple[str | None, str | None]:
        data, reason = self._get_json(f"/api/v1/nodes/{_quote(node_name)}")
        if data is None:
            return None, reason
        return data.get("metadata", {}).get("uid"), None

    def storage_class(self, name: str) -> tuple[dict[str, Any] | None, str | None]:
        return self._get_json(f"/apis/storage.k8s.io/v1/storageclasses/{_quote(name)}")

    def csi_driver(self, name: str) -> tuple[dict[str, Any] | None, str | None]:
        return self._get_json(f"/apis/storage.k8s.io/v1/csidrivers/{_quote(name)}")

    def _get_json(self, path: str) -> tuple[dict[str, Any] | None, str | None]:
        token = _read_text_if_exists(self.token_path)
        headers = {"accept": "application/json"}
        if token:
            headers["authorization"] = f"Bearer {token}"
        req = request.Request(f"{self.base_url}{path}", headers=headers)
        try:
            with request.urlopen(
                req, timeout=self.timeout_seconds, context=self.context
            ) as response:
                return json.loads(response.read().decode("utf-8")), None
        except error.HTTPError as exc:
            return None, f"kubernetes API HTTP {exc.code}"
        except (
            Exception
        ) as exc:  # pragma: no cover - exact stdlib errors vary by runtime
            return None, str(exc)


def config_from_env(environ: dict[str, str] | None = None) -> AgentDaemonConfig:
    environ = environ or os.environ
    config_path = environ.get("DMS_AGENT_CONFIG_PATH", DEFAULT_AGENT_CONFIG_PATH)
    storages = _load_storages(config_path)
    return AgentDaemonConfig(
        api_url=environ.get("DMS_AGENT_API_URL"),
        cluster_name=environ.get("DMS_AGENT_CLUSTER_NAME", "cluster-a"),
        node_name=environ.get("DMS_AGENT_NODE_NAME")
        or environ.get("NODE_NAME")
        or socket.gethostname(),
        node_uid=environ.get("DMS_AGENT_NODE_UID") or environ.get("NODE_UID"),
        worker_role=environ.get("DMS_AGENT_WORKER_ROLE", "RM"),
        report_interval_seconds=float(
            environ.get("DMS_AGENT_REPORT_INTERVAL_SECONDS", "60")
        ),
        report_timeout_seconds=float(
            environ.get("DMS_AGENT_REPORT_TIMEOUT_SECONDS", "5")
        ),
        storages=storages,
        tool_names=_csv(environ.get("DMS_AGENT_TOOLS")) or list(DEFAULT_TOOL_NAMES),
        credential_files=_csv(environ.get("DMS_AGENT_CREDENTIAL_FILES")),
        network_endpoints=_csv(environ.get("DMS_AGENT_NETWORK_ENDPOINTS")),
        identity_users=_csv(environ.get("DMS_AGENT_IDENTITY_USERS")),
        auth_shared_token=environ.get("DMS_AUTH_SHARED_TOKEN"),
        pod_name=environ.get("DMS_AGENT_POD_NAME") or environ.get("POD_NAME"),
        pod_namespace=environ.get("DMS_AGENT_POD_NAMESPACE")
        or environ.get("POD_NAMESPACE"),
        mountinfo_path=environ.get("DMS_AGENT_MOUNTINFO_PATH", "/proc/self/mountinfo"),
    )


def probe_os_metrics(
    proc_path: str = "/proc", host_root: str | None = None
) -> dict[str, Any]:
    """Host OS metrics from the agent's own procfs.

    cpu/memory/loadavg in procfs are node-wide (not namespaced), so the agent
    container's own /proc reflects the host node — no host mount needed for those.
    Disk usage needs statvfs on a real fs path, so it only runs when a host-root
    mount is provided (DMS_AGENT_HOST_ROOT). Every metric is independently
    fail-soft: a probe failure omits that metric and never breaks the report.
    """
    metrics: dict[str, Any] = {}
    # load average
    try:
        a, b, c = open(f"{proc_path}/loadavg").read().split()[:3]
        metrics["load"] = {"load1": float(a), "load5": float(b), "load15": float(c)}
    except Exception:  # noqa: BLE001 - fail-soft
        pass
    # memory (kB)
    try:
        info: dict[str, int] = {}
        with open(f"{proc_path}/meminfo") as fh:
            for line in fh:
                key, _, rest = line.partition(":")
                if rest.strip():
                    info[key.strip()] = int(rest.split()[0])
        total = info.get("MemTotal", 0)
        avail = info.get("MemAvailable", info.get("MemFree", 0))
        metrics["memory"] = {
            "total_kb": total,
            "available_kb": avail,
            "used_pct": round((total - avail) / total * 100, 1) if total else None,
        }
    except Exception:  # noqa: BLE001
        pass
    # cpu % over two /proc/stat samples
    try:

        def _cpu_sample() -> tuple[int, int] | None:
            with open(f"{proc_path}/stat") as fh:
                for line in fh:
                    if line.startswith("cpu "):
                        vals = [int(x) for x in line.split()[1:]]
                        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
                        return sum(vals), idle
            return None

        first = _cpu_sample()
        time.sleep(0.4)
        second = _cpu_sample()
        if first and second and second[0] != first[0]:
            busy = 1 - (second[1] - first[1]) / (second[0] - first[0])
            metrics["cpu"] = {
                "percent": round(max(0.0, busy) * 100, 1),
                "cores": os.cpu_count(),
            }
    except Exception:  # noqa: BLE001
        pass
    # disk (OS root) — only with a host-root mount; fail-soft otherwise
    root = host_root or os.environ.get("DMS_AGENT_HOST_ROOT")
    if root:
        try:
            st = os.statvfs(root)
            total_b = st.f_frsize * st.f_blocks
            free_b = st.f_frsize * st.f_bavail
            metrics["disk"] = {
                "path": "/",
                "total_gb": round(total_b / 1e9, 1),
                "used_pct": round((total_b - free_b) / total_b * 100, 1)
                if total_b
                else None,
            }
        except Exception:  # noqa: BLE001
            pass
    return metrics


def build_agent_report(
    config: AgentDaemonConfig,
    *,
    kubernetes_client: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    checked_at = (now or datetime.now(UTC)).isoformat()
    kubernetes_client = kubernetes_client or InClusterKubernetesClient.from_env(
        timeout_seconds=config.report_timeout_seconds
    )
    node_uid, node_reason = _probe_node_uid(config, kubernetes_client)
    mounts = probe_mounts(config.storages, config.mountinfo_path, checked_at=checked_at)
    csi = probe_csi(
        config.storages,
        kubernetes_client=kubernetes_client,
        checked_at=checked_at,
    )
    tools = probe_tools(
        config.tool_names, timeout_seconds=config.report_timeout_seconds
    )
    credentials = probe_credentials(config.credential_files, checked_at=checked_at)
    networks = probe_networks(
        [
            endpoint
            for endpoint in [config.api_url, *config.network_endpoints]
            if endpoint
        ],
        timeout_seconds=config.report_timeout_seconds,
        checked_at=checked_at,
    )
    identities = probe_identities(config.identity_users)
    os_metrics = probe_os_metrics()
    identity_evidence: dict[str, Any] = {
        "source": "agent-prober",
        "checked_at": checked_at,
        "pod_name": config.pod_name,
        "pod_namespace": config.pod_namespace,
        "process_user": getpass.getuser(),
    }
    if node_reason:
        identity_evidence["node_uid_reason"] = node_reason
    if identities:
        identity_evidence["users"] = identities
    return {
        "schema_version": "phase9.v1",
        "reported_at": checked_at,
        "cluster_name": config.cluster_name,
        "node_name": config.node_name,
        "node_uid": node_uid,
        "worker_role": config.worker_role,
        "mounts": mounts,
        "csi": csi,
        "tools": tools,
        "credentials": credentials,
        "networks": networks,
        "identity_evidence": identity_evidence,
        "os_metrics": os_metrics,
    }


def probe_mounts(
    storages: list[dict[str, Any]],
    mountinfo_path: str = "/proc/self/mountinfo",
    *,
    checked_at: str | None = None,
) -> list[dict[str, Any]]:
    checked_at = checked_at or datetime.now(UTC).isoformat()
    # host mountinfo 모드: /proc/self/mountinfo 외 경로는 호스트 마운트 네임스페이스로 간주
    host_mountinfo_mode = mountinfo_path != "/proc/self/mountinfo"
    mountinfo = parse_mountinfo(_read_text_if_exists(mountinfo_path) or "")
    evidence: list[dict[str, Any]] = []
    for storage in storages:
        for mount_path in storage.get("mount_paths") or []:
            path = str(mount_path)
            mount = _mount_for_path(path, mountinfo)
            is_mountpoint = (
                mount is not None and _norm_path(path) == mount["mount_point"]
            )
            # host mountinfo 모드에서는 mountinfo 기반으로 존재 여부 판단 (컨테이너 내 경로 없어도 됨)
            exists = is_mountpoint if host_mountinfo_mode else Path(path).exists()
            status = "Ready" if exists and is_mountpoint else "Missing"
            reason = None
            if not exists:
                reason = "configured mount path does not exist"
            elif not is_mountpoint:
                reason = "configured path exists but is not a mount point"
            # host mountinfo 모드: os.access()/statvfs()는 컨테이너 내 경로가 실제 존재할 때만 호출
            local_exists = Path(path).exists() if host_mountinfo_mode else exists
            mount_options = mount.get("options", []) if mount else []
            if host_mountinfo_mode and exists:
                # mountinfo rw/ro 옵션으로 readable/writable 추론
                is_rw = "rw" in mount_options
                readable = is_rw or "ro" in mount_options  # ro도 읽기는 가능
                writable = is_rw
            else:
                readable = os.access(path, os.R_OK) if local_exists else False
                writable = os.access(path, os.W_OK) if local_exists else False
            item = {
                "storage_name": storage.get("storage_name"),
                "path": path,
                "mount_path": path,
                "status": status,
                "reason": reason,
                "exists": exists,
                "is_mountpoint": is_mountpoint,
                "readable": readable,
                "writable": writable,
                "source": "agent-prober",
                "checked_at": checked_at,
            }
            if mount:
                item.update(
                    {
                        "filesystem_type": mount.get("filesystem_type"),
                        "mount_source": mount.get("source"),
                        "mount_options": mount_options,
                        "super_options": mount.get("super_options", []),
                    }
                )
            if local_exists:
                try:
                    stat = os.statvfs(path)
                    item["statvfs"] = {
                        "block_size": stat.f_frsize,
                        "blocks": stat.f_blocks,
                        "blocks_available": stat.f_bavail,
                    }
                except OSError as exc:
                    item["statvfs_error"] = str(exc)
            evidence.append(item)
    return evidence


def parse_mountinfo(text: str) -> list[dict[str, Any]]:
    mounts: list[dict[str, Any]] = []
    for line in text.splitlines():
        if " - " not in line:
            continue
        before, after = line.split(" - ", 1)
        before_fields = before.split()
        after_fields = after.split()
        if len(before_fields) < 5 or len(after_fields) < 3:
            continue
        mounts.append(
            {
                "mount_point": _decode_mountinfo_path(before_fields[4]),
                "options": (
                    before_fields[5].split(",") if len(before_fields) > 5 else []
                ),
                "filesystem_type": after_fields[0],
                "source": _decode_mountinfo_path(after_fields[1]),
                "super_options": after_fields[2].split(","),
            }
        )
    return mounts


def probe_csi(
    storages: list[dict[str, Any]],
    *,
    kubernetes_client: Any | None,
    checked_at: str | None = None,
) -> list[dict[str, Any]]:
    checked_at = checked_at or datetime.now(UTC).isoformat()
    by_driver: dict[str, list[dict[str, Any]]] = {}
    for storage in storages:
        driver = storage.get("csi_driver")
        if driver:
            by_driver.setdefault(str(driver), []).append(storage)
    evidence: list[dict[str, Any]] = []
    for driver, driver_storages in by_driver.items():
        configured_storage_classes = [
            str(storage["storage_class_name"])
            for storage in driver_storages
            if storage.get("storage_class_name")
        ]
        ready_storage_classes: list[str] = []
        checks: list[dict[str, Any]] = []
        status = "Ready"
        if kubernetes_client is None:
            status = "Unknown"
            checks.append(
                {
                    "kind": "kubernetes-api",
                    "status": "Unknown",
                    "reason": "not in cluster",
                }
            )
        else:
            csi_driver, reason = kubernetes_client.csi_driver(driver)
            if csi_driver is None:
                status = "Missing" if reason and "HTTP 404" in reason else "Unknown"
                checks.append(
                    {
                        "kind": "CSIDriver",
                        "name": driver,
                        "status": status,
                        "reason": reason,
                    }
                )
            else:
                checks.append({"kind": "CSIDriver", "name": driver, "status": "Ready"})
            for storage_class_name in configured_storage_classes:
                storage_class, reason = kubernetes_client.storage_class(
                    storage_class_name
                )
                if storage_class is None:
                    status = "Missing" if reason and "HTTP 404" in reason else "Unknown"
                    checks.append(
                        {
                            "kind": "StorageClass",
                            "name": storage_class_name,
                            "status": status,
                            "reason": reason,
                        }
                    )
                    continue
                provisioner = storage_class.get("provisioner")
                if provisioner != driver:
                    status = "Missing"
                    checks.append(
                        {
                            "kind": "StorageClass",
                            "name": storage_class_name,
                            "status": "Missing",
                            "reason": f"provisioner {provisioner} does not match {driver}",
                        }
                    )
                else:
                    ready_storage_classes.append(storage_class_name)
                    checks.append(
                        {
                            "kind": "StorageClass",
                            "name": storage_class_name,
                            "status": "Ready",
                            "provisioner": provisioner,
                        }
                    )
            if ready_storage_classes and any(
                check.get("kind") == "CSIDriver" and check.get("status") == "Ready"
                for check in checks
            ):
                status = "Ready"
        evidence.append(
            {
                "driver": driver,
                "storage_classes": (
                    ready_storage_classes
                    if status == "Ready"
                    else configured_storage_classes
                ),
                "configured_storage_classes": configured_storage_classes,
                "status": status,
                "checks": checks,
                "source": (
                    "kubernetes-api"
                    if kubernetes_client is not None
                    else "agent-prober"
                ),
                "checked_at": checked_at,
            }
        )
    return evidence


def probe_tools(
    tool_names: list[str], *, timeout_seconds: float = 2.0
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for name in tool_names:
        path = shutil.which(name)
        item = {"name": name, "status": "Ready" if path else "Missing", "path": path}
        if path:
            version = _tool_version(path, timeout_seconds=timeout_seconds)
            if version:
                item["version"] = version
        else:
            item["reason"] = "command not found in PATH"
        evidence.append(item)
    return evidence


def probe_credentials(
    credential_files: list[str], *, checked_at: str | None = None
) -> list[dict[str, Any]]:
    checked_at = checked_at or datetime.now(UTC).isoformat()
    paths = [SERVICE_ACCOUNT_TOKEN_PATH, *credential_files]
    evidence = []
    for path in paths:
        file_path = Path(path)
        exists = file_path.exists()
        item = {
            "name": (
                "kubernetes-service-account"
                if path == SERVICE_ACCOUNT_TOKEN_PATH
                else path
            ),
            "path": path,
            "status": "Ready" if exists else "Missing",
            "source": "agent-prober",
            "checked_at": checked_at,
        }
        if exists:
            try:
                item["mode"] = oct(file_path.stat().st_mode & 0o777)
            except OSError as exc:
                item["status"] = "Unknown"
                item["reason"] = str(exc)
        else:
            item["reason"] = "credential file does not exist"
        evidence.append(item)
    return evidence


def probe_networks(
    endpoints: list[str], *, timeout_seconds: float = 2.0, checked_at: str | None = None
) -> list[dict[str, Any]]:
    checked_at = checked_at or datetime.now(UTC).isoformat()
    evidence = []
    for endpoint in endpoints:
        host, port = _endpoint_host_port(endpoint)
        if not host or not port:
            evidence.append(
                {
                    "endpoint": endpoint,
                    "status": "Unknown",
                    "reason": "endpoint has no host or port",
                    "checked_at": checked_at,
                }
            )
            continue
        try:
            socket.getaddrinfo(host, port)
            with socket.create_connection((host, port), timeout=timeout_seconds):
                pass
            evidence.append(
                {
                    "endpoint": endpoint,
                    "host": host,
                    "port": port,
                    "status": "Ready",
                    "source": "agent-prober",
                    "checked_at": checked_at,
                }
            )
        except Exception as exc:
            evidence.append(
                {
                    "endpoint": endpoint,
                    "host": host,
                    "port": port,
                    "status": "Missing",
                    "reason": str(exc),
                    "source": "agent-prober",
                    "checked_at": checked_at,
                }
            )
    return evidence


def probe_identities(users: list[str]) -> list[dict[str, Any]]:
    evidence = []
    for username in users:
        try:
            entry = pwd.getpwnam(username)
            groups = [
                group.gr_name for group in grp.getgrall() if username in group.gr_mem
            ]
            evidence.append(
                {
                    "username": username,
                    "status": "Ready",
                    "uid": entry.pw_uid,
                    "gid": entry.pw_gid,
                    "groups": groups,
                }
            )
        except KeyError:
            evidence.append(
                {
                    "username": username,
                    "status": "Missing",
                    "reason": "user not found by local NSS lookup",
                }
            )
    return evidence


def post_report(config: AgentDaemonConfig, report: dict[str, Any]) -> dict[str, Any]:
    if not config.api_url:
        raise AgentPostError("DMS_AGENT_API_URL is required for posting")
    url = f"{config.api_url.rstrip('/')}/api/v1/agent/reports"
    actor = f"node:{report['cluster_name']}:{report['node_name']}"
    headers = {
        "content-type": "application/json",
        "x-dms-actor": actor,
    }
    if config.auth_shared_token:
        headers["authorization"] = f"Bearer {config.auth_shared_token}"
    req = request.Request(
        url,
        data=json.dumps(report).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=config.report_timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise AgentPostError(f"agent report rejected: HTTP {exc.code}: {body}") from exc
    except Exception as exc:
        raise AgentPostError(f"agent report post failed: {exc}") from exc


def run_loop(config: AgentDaemonConfig) -> int:
    while True:
        started = datetime.now(UTC).isoformat()
        try:
            report = build_agent_report(config)
            result = post_report(config, report)
            _log({"event": "agent_report_posted", "started_at": started, **result})
        except Exception as exc:
            _log(
                {
                    "event": "agent_report_failed",
                    "started_at": started,
                    "error": str(exc),
                }
            )
        time.sleep(config.report_interval_seconds)


def _probe_node_uid(
    config: AgentDaemonConfig, kubernetes_client: Any | None
) -> tuple[str, str | None]:
    if kubernetes_client is None:
        return config.node_uid or config.node_name, "kubernetes API is not available"
    node_uid, reason = kubernetes_client.node_uid(config.node_name)
    if node_uid:
        return node_uid, None
    return config.node_uid or config.node_name, reason or "node UID lookup failed"


def _load_storages(config_path: str) -> list[dict[str, Any]]:
    path = Path(config_path)
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    if isinstance(data, dict):
        return [dict(item) for item in data.get("storages", [])]
    if isinstance(data, list):
        return [dict(item) for item in data]
    raise ValueError(f"unsupported agent storage config: {config_path}")


def _mount_for_path(
    path: str, mountinfo: list[dict[str, Any]]
) -> dict[str, Any] | None:
    normalized = _norm_path(path)
    candidates = [
        mount
        for mount in mountinfo
        if normalized == mount["mount_point"]
        or normalized.startswith(f"{mount['mount_point'].rstrip('/')}/")
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda mount: len(mount["mount_point"]))


def _norm_path(path: str) -> str:
    return os.path.normpath(path)


def _decode_mountinfo_path(value: str) -> str:
    return (
        value.replace("\\040", " ")
        .replace("\\011", "\t")
        .replace("\\012", "\n")
        .replace("\\134", "\\")
    )


def _tool_version(path: str, *, timeout_seconds: float) -> str | None:
    try:
        completed = subprocess.run(
            [path, "--version"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout_seconds,
        )
    except Exception:
        return None
    line = (completed.stdout or "").splitlines()
    return line[0][:200] if line else None


def _endpoint_host_port(endpoint: str) -> tuple[str | None, int | None]:
    parsed = parse.urlparse(endpoint)
    if parsed.scheme and parsed.hostname:
        port = parsed.port or {"http": 80, "https": 443}.get(parsed.scheme)
        return parsed.hostname, port
    if ":" in endpoint:
        host, port_text = endpoint.rsplit(":", 1)
        try:
            return host, int(port_text)
        except ValueError:
            return host, None
    return endpoint or None, None


def _csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _quote(value: str) -> str:
    return parse.quote(value, safe="")


def _read_text_if_exists(path: str) -> str | None:
    try:
        return Path(path).read_text()
    except OSError:
        return None


def _log(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, sort_keys=True), flush=True)
