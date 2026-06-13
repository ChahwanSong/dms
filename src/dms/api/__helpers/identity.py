"""Helpers for LDAP identity-mapping routes."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from ...adapters import (
    IdentityLookupAdapter,
    IdentityLookupConfigurationError,
    IdentityLookupReadError,
    IdentityLookupResult,
)
from ...domain import IdentityMappingInput
from .._services import AppServices


def require_identity_lookup(services: AppServices) -> IdentityLookupAdapter:
    if services.identity_lookup is None:
        raise HTTPException(
            status_code=503,
            detail="direct LDAP identity lookup is not configured",
        )
    return services.identity_lookup


def perform_identity_lookup(
    *,
    lookup: IdentityLookupAdapter,
    provider: str,
    posix_username: str,
) -> IdentityLookupResult | None:
    try:
        return lookup.lookup(provider, posix_username)
    except (IdentityLookupConfigurationError, IdentityLookupReadError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def lookup_identity_or_http(
    *,
    lookup: IdentityLookupAdapter,
    provider: str,
    posix_username: str,
) -> IdentityLookupResult:
    result = perform_identity_lookup(
        lookup=lookup,
        provider=provider,
        posix_username=posix_username,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="LDAP user not found")
    return result


def expected_identity_mismatches(
    body: IdentityMappingInput, result: IdentityLookupResult
) -> list[str]:
    mismatches: list[str] = []
    if body.expected_uid is not None and body.expected_uid != result.uid:
        mismatches.append(
            f"expected uid {body.expected_uid} but LDAP returned {result.uid}"
        )
    if (
        body.expected_primary_gid is not None
        and body.expected_primary_gid != result.primary_gid
    ):
        mismatches.append(
            "expected primary gid "
            f"{body.expected_primary_gid} but LDAP returned {result.primary_gid}"
        )
    expected_groups = set(body.expected_groups)
    actual_groups = set(result.groups)
    missing_groups = sorted(expected_groups - actual_groups)
    if missing_groups:
        mismatches.append(
            "expected groups missing from LDAP result: " + ", ".join(missing_groups)
        )
    return mismatches


def mapping_drift(mapping: dict[str, Any], result: IdentityLookupResult) -> list[str]:
    drift: list[str] = []
    if mapping["uid"] != result.uid:
        drift.append(f"uid changed from {mapping['uid']} to {result.uid}")
    if mapping["gid"] != result.primary_gid:
        drift.append(
            f"primary gid changed from {mapping['gid']} to {result.primary_gid}"
        )
    if set(mapping["groups"]) != set(result.groups):
        drift.append(
            "groups changed from "
            f"{sorted(mapping['groups'])} to {sorted(result.groups)}"
        )
    return drift
