from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import json
from pathlib import Path
import shlex
import subprocess
import time
from typing import Any, Protocol
from urllib.parse import urlparse




def _shell_quote(value: str) -> str:
    if not value:
        return "''"
    safe = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-=./:")
    if all(char in safe for char in value):
        return value
    return "'" + value.replace("'", "'\"'\"'") + "'"



def _positive_int(value: Any, label: str) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be an integer") from exc
    if parsed <= 0:
        raise ValueError(f"{label} must be greater than zero")
    return parsed



def _first_present(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return None



def _json_stdout(stdout: str, kind: str) -> dict[str, Any]:
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise KubernetesMutationError(f"kubectl returned non-JSON {kind}") from exc



def _kubectl_not_found(stderr: str) -> bool:
    return "NotFound" in stderr or "not found" in stderr.lower()



# kubectl exits with returncode 1 both for a clean RBAC "no" AND for a failure to reach
# the API server (the latter is NOT a distinct exit code). These stderr substrings mark the
# unreachable case so callers can tell the two apart (verified against kubectl v1.34).
_KUBECTL_TRANSPORT_ERROR_MARKERS = (
    "connection to the server",
    "unable to connect to the server",
    "was refused",
    "connection refused",
    "couldn't get current server api group list",
    "i/o timeout",
    "no such host",
    "tls handshake timeout",
    "eof",
    "dial tcp",
)


def _kubectl_transport_error(stderr: str) -> bool:
    lowered = stderr.lower()
    return any(marker in lowered for marker in _KUBECTL_TRANSPORT_ERROR_MARKERS)
