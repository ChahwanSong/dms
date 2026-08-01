"""Storage mapping API helpers — defensive secret redaction for responses.

DMS holds no storage credentials: it authenticates to no filesystem and runs no
storage-vendor CLI, so ``backend_template`` should carry mount/identification metadata
only. The RM-era ``weka_credentials`` field is stripped from stored templates by
``_purge_storage_template_secrets`` in migrations.py.

This redactor stays anyway, as a generic safety net rather than support for one named
field: it masks anything secret-shaped that a legacy row, a hand-written PATCH or a
future field might put in the template, so no such value can reach an API response.
There is deliberately no merge-back counterpart -- the old one restored a stored
password whenever a client sent ``weka_credentials: {}``, which resurrected the very
secret a cleanup was trying to remove.
"""

from __future__ import annotations

from typing import Any

REDACTED = "***"

# Substring match on the key name, applied at every depth of backend_template.
_SECRET_KEY_HINTS = ("password", "secret", "token", "api_key", "apikey", "private_key")


def _is_secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(hint in lowered for hint in _SECRET_KEY_HINTS)


def _redact_value(value: Any) -> Any:
    """Recursively mask secret-shaped keys. Returns a copy; input is not mutated."""
    if isinstance(value, dict):
        return {
            key: (
                REDACTED
                if _is_secret_key(str(key)) and item not in (None, "")
                else _redact_value(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    return value


def redact_storage_mapping(mapping: dict[str, Any] | None) -> dict[str, Any] | None:
    """Mask secret-shaped fields inside ``backend_template`` for API responses."""
    if not mapping:
        return mapping
    template = mapping.get("backend_template")
    if not isinstance(template, dict):
        return mapping
    redacted_template = _redact_value(template)
    if redacted_template == template:
        return mapping
    return {**mapping, "backend_template": redacted_template}


def redact_storage_mappings(
    mappings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [redact_storage_mapping(m) for m in mappings]
