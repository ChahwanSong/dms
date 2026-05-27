#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
suffix="${DMS_PHASE2_DB_SUFFIX:-$(date +%Y%m%d%H%M%S)}"
operational_db="${DMS_PHASE2_OPERATIONAL_DB:-dms_phase2_${suffix}}"
observability_db="${DMS_PHASE2_OBSERVABILITY_DB:-dms_phase2_obs_${suffix}}"
postgres_host="${DMS_PHASE2_POSTGRES_HOST:-192.168.56.11}"
postgres_port="${DMS_PHASE2_POSTGRES_PORT:-30432}"
postgres_user="${DMS_PHASE2_POSTGRES_USER:-appuser}"

postgres_password="$(
  ssh c1-control "kubectl -n postgresql get secret postgresql-auth -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d"
)"
export POSTGRES_PASSWORD="${postgres_password}"
export PHASE2_OPERATIONAL_DB="${operational_db}"
export PHASE2_OBSERVABILITY_DB="${observability_db}"
export PHASE2_POSTGRES_HOST="${postgres_host}"
export PHASE2_POSTGRES_PORT="${postgres_port}"
export PHASE2_POSTGRES_USER="${postgres_user}"

python3 - <<'PY'
import os
import re

import psycopg
from psycopg import sql

db_names = [os.environ["PHASE2_OPERATIONAL_DB"], os.environ["PHASE2_OBSERVABILITY_DB"]]
for db_name in db_names:
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", db_name):
        raise SystemExit(f"unsafe database name: {db_name}")

with psycopg.connect(
    host=os.environ["PHASE2_POSTGRES_HOST"],
    port=int(os.environ["PHASE2_POSTGRES_PORT"]),
    dbname="postgres",
    user=os.environ["PHASE2_POSTGRES_USER"],
    password=os.environ["POSTGRES_PASSWORD"],
    autocommit=True,
) as connection:
    for db_name in db_names:
        exists = connection.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s",
            (db_name,),
        ).fetchone()
        if not exists:
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))
PY

encoded_password="$(
  python3 - <<'PY'
import os
from urllib.parse import quote

print(quote(os.environ["POSTGRES_PASSWORD"], safe=""))
PY
)"

export DMS_DATABASE_URL="postgresql://${postgres_user}:${encoded_password}@${postgres_host}:${postgres_port}/${operational_db}"
export DMS_OBSERVABILITY_DATABASE_URL="postgresql://${postgres_user}:${encoded_password}@${postgres_host}:${postgres_port}/${observability_db}"
export DMS_LDAP_URI="${DMS_LDAP_URI:-ldap://192.168.56.31}"
export DMS_LDAP_BASE_DN="${DMS_LDAP_BASE_DN:-dc=testbed,dc=local}"
export DMS_LDAP_BIND_DN="${DMS_LDAP_BIND_DN:-cn=admin,dc=testbed,dc=local}"
export DMS_LDAP_BIND_PASSWORD="${DMS_LDAP_BIND_PASSWORD:-testbed-admin}"
export DMS_LDAP_USER_SEARCH_BASE="${DMS_LDAP_USER_SEARCH_BASE:-ou=people,dc=testbed,dc=local}"
export DMS_LDAP_GROUP_SEARCH_BASE="${DMS_LDAP_GROUP_SEARCH_BASE:-ou=groups,dc=testbed,dc=local}"

cd "${repo_dir}"
python3 scripts/phase2_postgres_ldap_smoke.py
