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

from ..config import Settings
from .base import *  # noqa: F401,F403


@dataclass
class StubIdentityLookupAdapter:
    mappings: dict[tuple[str, str], IdentityLookupResult] = field(default_factory=dict)

    def lookup(self, provider: str, posix_username: str) -> IdentityLookupResult | None:
        return self.mappings.get((provider, posix_username))


@dataclass(frozen=True)
class LdapIdentityLookupAdapter:
    uri: str
    base_dn: str
    bind_dn: str | None = None
    bind_password: str | None = None
    user_search_base: str | None = None
    group_search_base: str | None = None
    user_filter: str = "(uid={username})"
    timeout_seconds: int = 5

    @classmethod
    def from_settings(cls, settings: Settings) -> "LdapIdentityLookupAdapter":
        if not settings.ldap_uri or not settings.ldap_base_dn:
            raise IdentityLookupConfigurationError(
                "DMS_LDAP_URI and DMS_LDAP_BASE_DN are required for direct LDAP identity lookup"
            )
        return cls(
            uri=settings.ldap_uri,
            base_dn=settings.ldap_base_dn,
            bind_dn=settings.ldap_bind_dn,
            bind_password=settings.ldap_bind_password,
            user_search_base=settings.ldap_user_search_base,
            group_search_base=settings.ldap_group_search_base,
            user_filter=settings.ldap_user_filter,
            timeout_seconds=settings.ldap_timeout_seconds,
        )

    def lookup(self, provider: str, posix_username: str) -> IdentityLookupResult | None:
        try:
            from ldap3 import ALL_ATTRIBUTES, Connection, Server, SUBTREE
            from ldap3.utils.conv import escape_filter_chars
        except ImportError as exc:
            raise IdentityLookupConfigurationError(
                "LDAP identity lookup requires installing the ldap extra: "
                "pip install 'dms[ldap]'"
            ) from exc

        username = escape_filter_chars(posix_username)
        user_base = self.user_search_base or self.base_dn
        group_base = self.group_search_base or self.base_dn
        user_filter = self.user_filter.format(username=username)
        server = Server(self.uri, connect_timeout=self.timeout_seconds)
        try:
            with Connection(
                server,
                user=self.bind_dn,
                password=self.bind_password,
                auto_bind=True,
                receive_timeout=self.timeout_seconds,
                auto_referrals=False,
            ) as connection:
                connection.search(
                    search_base=user_base,
                    search_filter=user_filter,
                    search_scope=SUBTREE,
                    attributes=[ALL_ATTRIBUTES],
                    size_limit=2,
                )
                if not connection.entries:
                    return None
                if len(connection.entries) > 1:
                    raise IdentityLookupReadError(
                        f"LDAP user lookup returned multiple entries for {posix_username}"
                    )
                user_entry = connection.entries[0]
                user_attrs = user_entry.entry_attributes_as_dict
                uid_number = _single_int(user_attrs, "uidNumber")
                gid_number = _single_int(user_attrs, "gidNumber")
                user_dn = user_entry.entry_dn
                groups = self._lookup_groups(
                    connection=connection,
                    group_base=group_base,
                    username=username,
                    user_dn=escape_filter_chars(user_dn),
                    primary_gid=gid_number,
                )
        except IdentityLookupReadError:
            raise
        except Exception as exc:
            raise IdentityLookupReadError(
                f"LDAP identity lookup failed: {exc}"
            ) from exc

        return IdentityLookupResult(
            provider=provider,
            posix_username=posix_username,
            uid=uid_number,
            primary_gid=gid_number,
            groups=groups,
            user_dn=user_dn,
            source_metadata={
                "adapter": "ldap3-direct",
                "read_only": True,
                "uri": self.uri,
                "base_dn": self.base_dn,
                "user_search_base": user_base,
                "group_search_base": group_base,
                "user_filter": user_filter,
                "user_dn": user_dn,
            },
        )

    def _lookup_groups(
        self,
        *,
        connection: Any,
        group_base: str,
        username: str,
        user_dn: str,
        primary_gid: int,
    ) -> list[str]:
        from ldap3 import SUBTREE

        group_filter = f"(|(memberUid={username})(member={user_dn})(uniqueMember={user_dn})(gidNumber={primary_gid}))"
        connection.search(
            search_base=group_base,
            search_filter=group_filter,
            search_scope=SUBTREE,
            attributes=["cn", "gidNumber", "memberUid", "member", "uniqueMember"],
        )
        names: set[str] = set()
        for entry in connection.entries:
            attrs = entry.entry_attributes_as_dict
            cn_values = attrs.get("cn") or []
            if cn_values:
                names.add(str(cn_values[0]))
        return sorted(names)

    def bulk_lookup_all(
        self,
        provider: str,
        posix_usernames: list[str],
        *,
        batch_size: int = 200,
        max_workers: int = 8,
    ) -> tuple[dict[str, IdentityLookupResult], list[str]]:
        """LDAP 연결 1개로 전체 유저 uid/gid/groups를 일괄 조회.

        Returns:
            (results, errors): 성공한 username→result 매핑, 실패한 username 목록
        """
        try:
            from ldap3 import Connection, Server, SUBTREE
            from ldap3.utils.conv import escape_filter_chars
        except ImportError as exc:
            raise IdentityLookupConfigurationError(
                "LDAP identity lookup requires installing the ldap extra"
            ) from exc

        import concurrent.futures

        user_base = self.user_search_base or self.base_dn
        group_base = self.group_search_base or self.base_dn
        server = Server(self.uri, connect_timeout=self.timeout_seconds)

        # 1단계: 전체 그룹을 한 번에 fetch해 역인덱스 빌드
        # memberUid(username), member/uniqueMember(DN) 모두 수집
        try:
            with Connection(
                server,
                user=self.bind_dn,
                password=self.bind_password,
                auto_bind=True,
                receive_timeout=max(self.timeout_seconds * 4, 60),
                auto_referrals=False,
            ) as conn:
                conn.search(
                    search_base=group_base,
                    search_filter="(|(objectClass=posixGroup)(objectClass=groupOfNames)(objectClass=groupOfUniqueNames))",
                    search_scope=SUBTREE,
                    attributes=[
                        "cn",
                        "gidNumber",
                        "memberUid",
                        "member",
                        "uniqueMember",
                    ],
                    paged_size=1000,
                )
                # username → set of group names
                uid_to_groups: dict[str, set[str]] = {}
                # dn(lowercase) → set of group names
                dn_to_groups: dict[str, set[str]] = {}
                # primary gid → group name
                gid_to_group: dict[int, str] = {}

                for entry in conn.entries:
                    attrs = entry.entry_attributes_as_dict
                    cn_list = attrs.get("cn") or []
                    if not cn_list:
                        continue
                    group_name = str(cn_list[0])
                    gid_list = attrs.get("gidNumber") or []
                    if gid_list:
                        try:
                            gid_to_group[int(gid_list[0])] = group_name
                        except (ValueError, TypeError):
                            pass
                    for uid in attrs.get("memberUid") or []:
                        uid_to_groups.setdefault(str(uid), set()).add(group_name)
                    for dn in (attrs.get("member") or []) + (
                        attrs.get("uniqueMember") or []
                    ):
                        dn_to_groups.setdefault(str(dn).lower(), set()).add(group_name)
        except IdentityLookupReadError:
            raise
        except Exception as exc:
            raise IdentityLookupReadError(
                f"LDAP bulk group fetch failed: {exc}"
            ) from exc

        # 2단계: 유저 배치 fetch (200명씩 OR 필터)
        def fetch_batch(
            batch: list[str],
        ) -> list[tuple[str, IdentityLookupResult | None, str | None]]:
            results: list[tuple[str, IdentityLookupResult | None, str | None]] = []
            try:
                with Connection(
                    server,
                    user=self.bind_dn,
                    password=self.bind_password,
                    auto_bind=True,
                    receive_timeout=max(self.timeout_seconds * 2, 30),
                    auto_referrals=False,
                ) as conn:
                    escaped = [escape_filter_chars(u) for u in batch]
                    batch_filter = "(|" + "".join(f"(uid={e})" for e in escaped) + ")"
                    conn.search(
                        search_base=user_base,
                        search_filter=batch_filter,
                        search_scope=SUBTREE,
                        attributes=["uid", "uidNumber", "gidNumber"],
                    )
                    found: dict[str, Any] = {}
                    for entry in conn.entries:
                        attrs = entry.entry_attributes_as_dict
                        uid_vals = attrs.get("uid") or []
                        if uid_vals:
                            found[str(uid_vals[0])] = (entry.entry_dn, attrs)

                    for username in batch:
                        if username not in found:
                            results.append((username, None, "not found in LDAP"))
                            continue
                        user_dn, attrs = found[username]
                        try:
                            uid_number = _single_int(attrs, "uidNumber")
                            gid_number = _single_int(attrs, "gidNumber")
                        except Exception as exc:
                            results.append((username, None, str(exc)))
                            continue
                        # groups: uid index + dn index + primary gid
                        groups: set[str] = set()
                        groups.update(uid_to_groups.get(username, set()))
                        groups.update(dn_to_groups.get(user_dn.lower(), set()))
                        if gid_number in gid_to_group:
                            groups.add(gid_to_group[gid_number])
                        result = IdentityLookupResult(
                            provider=provider,
                            posix_username=username,
                            uid=uid_number,
                            primary_gid=gid_number,
                            groups=sorted(groups),
                            user_dn=user_dn,
                            source_metadata={
                                "adapter": "ldap3-direct",
                                "read_only": True,
                                "uri": self.uri,
                                "base_dn": self.base_dn,
                                "user_search_base": user_base,
                                "group_search_base": group_base,
                                "user_filter": f"(uid={username})",
                                "user_dn": user_dn,
                            },
                        )
                        results.append((username, result, None))
            except Exception as exc:
                for username in batch:
                    results.append((username, None, str(exc)))
            return results

        batches = [
            posix_usernames[i : i + batch_size]
            for i in range(0, len(posix_usernames), batch_size)
        ]

        all_results: dict[str, IdentityLookupResult] = {}
        errors: list[str] = []

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(fetch_batch, b): b for b in batches}
            for future in concurrent.futures.as_completed(futures):
                for username, result, error in future.result():
                    if result is not None:
                        all_results[username] = result
                    else:
                        errors.append(f"{username}: {error}")

        return all_results, errors


def _single_int(attributes: dict[str, Any], name: str) -> int:
    values = attributes.get(name)
    if not values:
        raise IdentityLookupReadError(f"LDAP user entry missing {name}")
    try:
        return int(values[0])
    except (TypeError, ValueError) as exc:
        raise IdentityLookupReadError(f"LDAP user entry has invalid {name}") from exc
