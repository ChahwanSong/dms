from __future__ import annotations

from typing import Any

from ._base import *  # noqa: F401,F403


class DmIdentityDenylistMixin:
    """DM identity denylist persistence.

    `identity_mappings` was removed: DM resolves the requester's POSIX identity by a
    READ-ONLY LDAP lookup at preflight time (no cached, admission-gated mapping table).
    The only DM-side identity state is this denylist — an instant per-requester / per-
    owner / per-group kill-switch and admission block (default empty = allow all).
    """

    def add_dm_denylist_entry(
        self,
        *,
        subject_type: str,
        subject: str,
        reason: str | None,
        actor: str,
    ) -> dict[str, Any]:
        subject = (subject or "").strip()
        now = iso_now()
        with self.database.connect() as connection:
            connection.execute(
                "DELETE FROM dm_identity_denylist WHERE subject_type = ? AND subject = ?",
                (subject_type, subject),
            )
            connection.execute(
                """
                INSERT INTO dm_identity_denylist
                    (subject_type, subject, reason, created_at, created_by)
                VALUES (?, ?, ?, ?, ?)
                """,
                (subject_type, subject, reason, now, actor),
            )
        return {
            "subject_type": subject_type,
            "subject": subject,
            "reason": reason,
            "created_at": now,
            "created_by": actor,
        }

    def remove_dm_denylist_entry(self, *, subject_type: str, subject: str) -> bool:
        subject = (subject or "").strip()
        with self.database.connect() as connection:
            existed = (
                connection.execute(
                    "SELECT 1 FROM dm_identity_denylist WHERE subject_type = ? AND subject = ?",
                    (subject_type, subject),
                ).fetchone()
                is not None
            )
            connection.execute(
                "DELETE FROM dm_identity_denylist WHERE subject_type = ? AND subject = ?",
                (subject_type, subject),
            )
        return existed

    def list_dm_denylist(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT subject_type, subject, reason, created_at, created_by
                FROM dm_identity_denylist
                ORDER BY subject_type, subject
                """
            ).fetchall()
        return [row_to_dict(row) for row in rows]

    def dm_identity_denied(
        self,
        *,
        requester_id: str | None,
        owner_username: str | None,
        groups: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """Return the matching denylist entry (with reason) if the requester, the POSIX
        owner, or any of the resolved LDAP groups is denied; else None."""
        checks: list[tuple[str, str]] = []
        if requester_id:
            checks.append(("requester", requester_id))
        if owner_username:
            checks.append(("owner", owner_username))
        for group in groups or []:
            checks.append(("group", group))
        if not checks:
            return None
        with self.database.connect() as connection:
            for subject_type, subject in checks:
                normalized = (subject or "").strip()
                if not normalized:
                    continue
                # Case-insensitive + whitespace-stripped match: the kill-switch must not be
                # bypassable by `Alice`/` alice ` when `alice` is denied (LDAP/SSSD username
                # matching is itself case-insensitive, so the data op would still run as the
                # same user).
                row = connection.execute(
                    """
                    SELECT subject_type, subject, reason
                    FROM dm_identity_denylist
                    WHERE subject_type = ? AND LOWER(subject) = LOWER(?)
                    """,
                    (subject_type, normalized),
                ).fetchone()
                if row:
                    return row_to_dict(row)
        return None
