# DMS Phase 2 Verification

Date: 2026-05-27

## Scope

Phase 2 verified two foundations from `docs/dms-phase2.md`.

- PostgreSQL live baseline for operational state and observability/log state
- LDAP-only Identity Mapping using direct read-only OpenLDAP queries

Identity Mapping verification did not use local `/etc/passwd`, local
`/etc/group`, NSS, `getent`, or `id` as success evidence.

## Local Tests

Command:

```bash
cd /home/mason/workspace/dms
python3 -m pytest -q
```

Result:

```text
18 passed in 8.96s
```

The same suite was also run in the Phase 2 dependency venv after installing
`postgres`, `ldap`, and `test` extras:

```bash
/tmp/dms-phase2-venv/bin/python -m pytest -q
```

Result:

```text
18 passed in 8.79s
```

Covered additions:

- Identity Mapping requires a configured direct lookup adapter.
- Upsert stores UID/GID/groups returned by the lookup adapter, not request
  payload values.
- Expected UID/GID/group mismatch stores `NeedsReview`.
- `failed=true` returns `NeedsReview`, `Stale`, and `Disabled` mappings.
- Refresh drift marks the mapping `Stale` without overwriting existing
  UID/GID/group values.
- `Disabled` mapping is not reactivated by refresh.

## Testbed Live Smoke

Command:

```bash
cd /home/mason/workspace/dms
PATH="/tmp/dms-phase2-venv/bin:$PATH" ./scripts/verify-phase2-testbed.sh
```

The script created separate PostgreSQL databases on the testbed PostgreSQL
NodePort and used OpenLDAP directly at `ldap://192.168.56.31`.

Result:

```json
{
  "alice_mapping_status": "Active",
  "bob_mapping_status_after_disable_refresh": "Disabled",
  "data_request_id": "req_0f3e7786c59f43f0bc547803a4f90cf4",
  "filesystem_request_id": "req_9616f165418d47a99085b5afba57815a",
  "ldap_uri": "ldap://192.168.56.31",
  "mismatch_mapping_status": "NeedsReview",
  "observability_database_url": "postgresql://appuser:***@192.168.56.11:30432/dms_phase2_obs_20260527225156",
  "operational_database_url": "postgresql://appuser:***@192.168.56.11:30432/dms_phase2_20260527225156",
  "status": "ok",
  "token": "6135787d"
}
```

Additional CLI migration check against the same live PostgreSQL databases:

```bash
dms migrate
```

Result:

```text
migrations applied
```

Operational migration rows after the live run:

```text
operational-0001-phase1
operational-0002-phase2-identity
```

Identity Mapping status rows after the live run:

```text
Active: 1
Disabled: 1
NeedsReview: 1
```

## Verified Matrix

| Area | Result |
| --- | --- |
| PostgreSQL migration | `dms migrate` and application startup migrations succeeded on live PostgreSQL. |
| DB separation | Operational DB and observability DB used separate PostgreSQL databases; `diagnostic_events` existed only in observability DB. |
| Auth failure | Missing actor returned 401, created no operational request, and wrote an observability event. |
| Authz failure | Blocked actor returned 403, stored `AuthorizationFailed`, and created no plan. |
| RM lifecycle | Filesystem create request planned and completed through RM worker stub on PostgreSQL. |
| DM lifecycle | Data scan request planned and completed through DM worker/Volcano stub on PostgreSQL. |
| Query API | Request history query returned lifecycle result evidence. |
| LDAP direct lookup | `LdapIdentityLookupAdapter` read OpenLDAP directly through `ldap3`; stored source metadata shows `adapter=ldap3-direct`. |
| LDAP no-write | Implementation only performs LDAP search operations; no LDAP add/modify/delete path exists. |
| Active mapping | `portal:alice -> alice` and `portal:bob -> bob` stored LDAP UID/GID/groups as `Active`. |
| Mismatch handling | Expected UID mismatch for `alice` stored `NeedsReview`. |
| Missing LDAP user | Nonexistent POSIX username was rejected with 404 and not stored as `Active`. |
| Refresh | Refresh re-read LDAP and kept matching mapping `Active`. |
| Disable | `bob` mapping stayed `Disabled` after refresh. |
| Failed filter | `failed=true` returned `NeedsReview` and `Disabled` mappings. |

## Notes

- Testbed PostgreSQL password was read from Kubernetes Secret
  `postgresql/postgresql-auth` and was not written to this document.
- The live smoke used LDAP bind DN `cn=admin,dc=testbed,dc=local`; the password
  is provided by the testbed default or `DMS_LDAP_BIND_PASSWORD`.
- The host system Python is externally managed, so live dependency verification
  used `/tmp/dms-phase2-venv`.
