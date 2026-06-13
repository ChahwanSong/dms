#!/usr/bin/env python3
"""
서비스 LDAP(read-only) → 로컬 OpenLDAP 단방향 sync.

동작:
  - posixAccount 사용자(ou=people)와 posixGroup 그룹(ou=groups) 복제
  - 비표준 objectClass(inetuser, inetorgperson, shadowAccount, ...) 제거, 필수 attrs만 보존
  - 로컬에 없으면 add, 있으면 필드 비교 후 수정(uid/uidNumber/gidNumber/cn/sn 변경 시 modify)
  - 로컬에만 있고 소스에 없으면 삭제 (dms- prefix 그룹 제외)
  - --dry-run: DB 변경 없이 예상 변경 출력

사용:
  python3 sync-ldap-to-local.py [--dry-run]
"""

import argparse
import sys
from ldap3 import (
    Connection,
    Server,
    SUBTREE,
    MODIFY_REPLACE,
    ALL_ATTRIBUTES,
)
from ldap3.utils.conv import escape_filter_chars

SRC_URI = "ldap://75.23.118.14"
SRC_BIND_DN = "uid=search_sc,ou=user,ou=ldap_user,dc=supercom,dc=samsung"
SRC_BIND_PW = "search_sc"
SRC_USER_BASE = "ou=people,dc=SC,dc=supercom,dc=samsung"
SRC_GROUP_BASE = "ou=groups,dc=SC,dc=supercom,dc=samsung"

DST_URI = "ldap://localhost:3389"
DST_BIND_DN = "cn=admin,dc=supercom,dc=samsung"
DST_BIND_PW = "dms-ldap-admin"
DST_USER_BASE = "ou=people,dc=supercom,dc=samsung"
DST_GROUP_BASE = "ou=groups,dc=supercom,dc=samsung"

# 로컬 OpenLDAP에 넣을 objectClass만 허용 (비표준 제거)
ALLOWED_USER_CLASSES = {
    "top",
    "person",
    "organizationalPerson",
    "inetOrgPerson",
    "posixAccount",
}
ALLOWED_GROUP_CLASSES = {"top", "posixGroup"}

# 복제할 사용자 속성
USER_ATTRS = [
    "uid",
    "uidNumber",
    "gidNumber",
    "cn",
    "sn",
    "homeDirectory",
    "loginShell",
    "gecos",
]
# 복제할 그룹 속성
GROUP_ATTRS = ["cn", "gidNumber", "memberUid", "description"]


def connect(uri, bind_dn, bind_pw):
    server = Server(uri, connect_timeout=10)
    conn = Connection(
        server,
        user=bind_dn,
        password=bind_pw,
        auto_bind=True,
        auto_referrals=False,
        receive_timeout=60,
    )
    return conn


def fetch_all(conn, base, obj_filter, attrs):
    conn.search(
        search_base=base,
        search_filter=obj_filter,
        search_scope=SUBTREE,
        attributes=attrs + ["objectClass"],
        paged_size=500,
    )
    results = {}
    for entry in conn.entries:
        attrs_dict = entry.entry_attributes_as_dict
        results[str(entry.entry_dn)] = attrs_dict
    # paged 처리
    cookie = (
        conn.result.get("controls", {})
        .get("1.2.840.113556.1.4.319", {})
        .get("value", {})
        .get("cookie")
    )
    while cookie:
        conn.search(
            search_base=base,
            search_filter=obj_filter,
            search_scope=SUBTREE,
            attributes=attrs + ["objectClass"],
            paged_size=500,
            paged_cookie=cookie,
        )
        for entry in conn.entries:
            results[str(entry.entry_dn)] = entry.entry_attributes_as_dict
        cookie = (
            conn.result.get("controls", {})
            .get("1.2.840.113556.1.4.319", {})
            .get("value", {})
            .get("cookie")
        )
    return results


def normalize_dn(src_dn, src_base, dst_base):
    """소스 DN의 base를 목적지 base로 교체."""
    suffix = src_dn[: -len(src_base)].rstrip(",")
    return f"{suffix},{dst_base}" if suffix else dst_base


def first(values):
    if not values:
        return None
    return (
        values[0]
        if not isinstance(values[0], bytes)
        else values[0].decode("utf-8", errors="replace")
    )


def to_str_list(values):
    result = []
    for v in values or []:
        if isinstance(v, bytes):
            result.append(v.decode("utf-8", errors="replace"))
        else:
            result.append(str(v))
    return result


def sync_users(src_conn, dst_conn, dry_run):
    print("=== syncing users ===")
    src_entries = fetch_all(
        src_conn, SRC_USER_BASE, "(objectClass=posixAccount)", USER_ATTRS
    )
    dst_entries = fetch_all(
        dst_conn, DST_USER_BASE, "(objectClass=posixAccount)", USER_ATTRS
    )

    # uid → dst_dn 매핑
    dst_by_uid = {}
    for dn, attrs in dst_entries.items():
        uid = first(attrs.get("uid"))
        if uid:
            dst_by_uid[uid] = (dn, attrs)

    added = modified = skipped = deleted = 0

    for src_dn, src_attrs in src_entries.items():
        uid = first(src_attrs.get("uid"))
        if not uid:
            continue

        # 복제할 objectClass만 필터
        oc_list = [str(o) for o in (src_attrs.get("objectClass") or [])]
        oc_filtered = sorted(set(oc_list) & ALLOWED_USER_CLASSES)
        if "posixAccount" not in oc_filtered:
            oc_filtered.append("posixAccount")
        if "top" not in oc_filtered:
            oc_filtered.append("top")

        dst_dn = f"uid={uid},{DST_USER_BASE}"

        entry_attrs = {
            "uid": to_str_list(src_attrs.get("uid")),
            "uidNumber": to_str_list(src_attrs.get("uidNumber")),
            "gidNumber": to_str_list(src_attrs.get("gidNumber")),
            "cn": to_str_list(src_attrs.get("cn")) or [uid],
            "sn": to_str_list(src_attrs.get("sn")) or [uid],
            "homeDirectory": to_str_list(src_attrs.get("homeDirectory"))
            or [f"/home/{uid}"],
            "loginShell": to_str_list(src_attrs.get("loginShell")) or ["/bin/bash"],
        }
        # gecos는 선택적
        if src_attrs.get("gecos"):
            entry_attrs["gecos"] = to_str_list(src_attrs["gecos"])

        if uid not in dst_by_uid:
            if dry_run:
                print(f"  [ADD] {dst_dn}")
            else:
                ok = dst_conn.add(
                    dst_dn, object_class=oc_filtered, attributes=entry_attrs
                )
                if not ok:
                    print(f"  [ERROR] add {dst_dn}: {dst_conn.result}", file=sys.stderr)
                else:
                    added += 1
            added += 1 if dry_run else 0
        else:
            existing_dn, existing_attrs = dst_by_uid[uid]
            changes = {}
            for attr in [
                "uidNumber",
                "gidNumber",
                "cn",
                "sn",
                "homeDirectory",
                "loginShell",
            ]:
                src_val = to_str_list(src_attrs.get(attr)) or entry_attrs.get(attr, [])
                dst_val = to_str_list(existing_attrs.get(attr))
                if src_val != dst_val:
                    changes[attr] = [(MODIFY_REPLACE, src_val)]
            if changes:
                if dry_run:
                    print(f"  [MODIFY] {existing_dn}: {list(changes.keys())}")
                else:
                    ok = dst_conn.modify(existing_dn, changes)
                    if not ok:
                        print(
                            f"  [ERROR] modify {existing_dn}: {dst_conn.result}",
                            file=sys.stderr,
                        )
                    else:
                        modified += 1
                modified += 1 if dry_run else 0
            else:
                skipped += 1

    # 소스에 없는 로컬 사용자 삭제
    src_uids = {
        first(a.get("uid")) for a in src_entries.values() if first(a.get("uid"))
    }
    for uid, (dn, _) in dst_by_uid.items():
        if uid not in src_uids:
            if dry_run:
                print(f"  [DELETE] {dn}")
            else:
                dst_conn.delete(dn)
            deleted += 1

    print(
        f"  users: added={added} modified={modified} deleted={deleted} skipped={skipped}"
    )
    return added, modified, deleted


def sync_groups(src_conn, dst_conn, dry_run):
    print("=== syncing groups ===")
    src_entries = fetch_all(
        src_conn, SRC_GROUP_BASE, "(objectClass=posixGroup)", GROUP_ATTRS
    )
    dst_entries = fetch_all(
        dst_conn, DST_GROUP_BASE, "(objectClass=posixGroup)", GROUP_ATTRS
    )

    dst_by_cn = {}
    for dn, attrs in dst_entries.items():
        cn = first(attrs.get("cn"))
        if cn:
            dst_by_cn[cn] = (dn, attrs)

    added = modified = skipped = deleted = 0

    for src_dn, src_attrs in src_entries.items():
        cn = first(src_attrs.get("cn"))
        if not cn:
            continue

        dst_dn = f"cn={cn},{DST_GROUP_BASE}"
        gid_number = to_str_list(src_attrs.get("gidNumber"))
        member_uid = to_str_list(src_attrs.get("memberUid"))
        description = to_str_list(src_attrs.get("description"))

        entry_attrs = {"cn": [cn], "gidNumber": gid_number}
        if member_uid:
            entry_attrs["memberUid"] = member_uid
        if description:
            entry_attrs["description"] = description

        if cn not in dst_by_cn:
            if dry_run:
                print(f"  [ADD] {dst_dn} (gid={gid_number})")
            else:
                ok = dst_conn.add(
                    dst_dn, object_class=["top", "posixGroup"], attributes=entry_attrs
                )
                if not ok:
                    print(f"  [ERROR] add {dst_dn}: {dst_conn.result}", file=sys.stderr)
                else:
                    added += 1
            added += 1 if dry_run else 0
        else:
            existing_dn, existing_attrs = dst_by_cn[cn]
            changes = {}
            for attr in ["gidNumber", "memberUid", "description"]:
                src_val = to_str_list(src_attrs.get(attr))
                dst_val = to_str_list(existing_attrs.get(attr))
                if sorted(src_val) != sorted(dst_val):
                    changes[attr] = [(MODIFY_REPLACE, src_val)]
            if changes:
                if dry_run:
                    print(f"  [MODIFY] {existing_dn}: {list(changes.keys())}")
                else:
                    ok = dst_conn.modify(existing_dn, changes)
                    if not ok:
                        print(
                            f"  [ERROR] modify {existing_dn}: {dst_conn.result}",
                            file=sys.stderr,
                        )
                    else:
                        modified += 1
                modified += 1 if dry_run else 0
            else:
                skipped += 1

    # dms- prefix 그룹은 삭제하지 않음, 소스에 없는 기타 그룹은 삭제
    src_cns = {first(a.get("cn")) for a in src_entries.values() if first(a.get("cn"))}
    for cn, (dn, _) in dst_by_cn.items():
        if cn not in src_cns:
            if cn.startswith("dms-"):
                continue  # DMS가 관리하는 그룹은 건드리지 않음
            if dry_run:
                print(f"  [DELETE] {dn}")
            else:
                dst_conn.delete(dn)
            deleted += 1

    print(
        f"  groups: added={added} modified={modified} deleted={deleted} skipped={skipped}"
    )
    return added, modified, deleted


def main():
    parser = argparse.ArgumentParser(
        description="Sync service LDAP users/groups to local OpenLDAP"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Show changes without applying"
    )
    args = parser.parse_args()

    if args.dry_run:
        print("[DRY RUN] no changes will be made")

    print(f"Connecting to source: {SRC_URI}")
    src = connect(SRC_URI, SRC_BIND_DN, SRC_BIND_PW)
    print(f"Connecting to dest:   {DST_URI}")
    dst = connect(DST_URI, DST_BIND_DN, DST_BIND_PW)

    sync_users(src, dst, args.dry_run)
    sync_groups(src, dst, args.dry_run)

    src.unbind()
    dst.unbind()
    print("Done.")


if __name__ == "__main__":
    main()
