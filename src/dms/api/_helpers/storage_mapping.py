"""Storage mapping API helpers — secret redaction & merge for PATCH."""

from __future__ import annotations

from typing import Any

REDACTED = "***"


def redact_storage_mapping(mapping: dict[str, Any] | None) -> dict[str, Any] | None:
    """API 응답용으로 backend_template 안의 비밀 필드를 마스킹."""
    if not mapping:
        return mapping
    template = mapping.get("backend_template")
    if not isinstance(template, dict):
        return mapping
    creds = template.get("weka_credentials")
    if isinstance(creds, dict) and creds.get("password") not in (None, "", REDACTED):
        redacted_template = dict(template)
        redacted_template["weka_credentials"] = {**creds, "password": REDACTED}
        redacted = dict(mapping)
        redacted["backend_template"] = redacted_template
        return redacted
    return mapping


def redact_storage_mappings(
    mappings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [redact_storage_mapping(m) for m in mappings]


def merge_storage_mapping_secrets(
    incoming_template: dict[str, Any], existing: dict[str, Any] | None
) -> None:
    """PATCH 입력의 redacted 비밀 필드를 기존 값으로 머지 (in-place).

    클라이언트가 redacted GET 응답을 그대로 PATCH로 다시 보낼 때 기존 password를 유지."""
    if not existing:
        return
    existing_template = existing.get("backend_template") or {}
    incoming_creds = incoming_template.get("weka_credentials")
    if not isinstance(incoming_creds, dict):
        return
    existing_creds = existing_template.get("weka_credentials") or {}
    password = incoming_creds.get("password")
    merged = dict(incoming_creds)
    if password in (None, "", REDACTED) and existing_creds.get("password"):
        merged["password"] = existing_creds["password"]
    if not merged.get("organization") and existing_creds.get("organization"):
        merged["organization"] = existing_creds["organization"]
    if not merged.get("username") and existing_creds.get("username"):
        merged["username"] = existing_creds["username"]
    incoming_template["weka_credentials"] = merged
