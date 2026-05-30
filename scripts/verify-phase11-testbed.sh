#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
suffix="${DMS_PHASE11_DB_SUFFIX:-$(date +%Y%m%d%H%M%S)}"
registry_host="${DMS_PHASE11_REGISTRY_HOST:-192.168.56.11:5000}"
namespace="${DMS_PHASE11_NAMESPACE:-dms-phase11}"
node_port="${DMS_PHASE11_API_NODE_PORT:-30091}"

export DMS_PHASE10_DB_SUFFIX="${suffix}"
export DMS_PHASE10_OPERATIONAL_DB="${DMS_PHASE11_OPERATIONAL_DB:-dms_phase11_${suffix}}"
export DMS_PHASE10_OBSERVABILITY_DB="${DMS_PHASE11_OBSERVABILITY_DB:-dms_phase11_obs_${suffix}}"
export DMS_PHASE10_POSTGRES_HOST="${DMS_PHASE11_POSTGRES_HOST:-192.168.56.11}"
export DMS_PHASE10_POSTGRES_PORT="${DMS_PHASE11_POSTGRES_PORT:-30432}"
export DMS_PHASE10_POSTGRES_USER="${DMS_PHASE11_POSTGRES_USER:-appuser}"
export DMS_PHASE10_NAMESPACE="${namespace}"
export DMS_PHASE10_API_NODE_PORT="${node_port}"
export DMS_PHASE10_AUTH_TOKEN="${DMS_PHASE11_AUTH_TOKEN:-phase11-testbed-token}"
export DMS_PHASE10_REGISTRY_HOST="${registry_host}"
export DMS_PHASE10_K8S_IMAGE="${DMS_PHASE11_K8S_IMAGE:-testbed-registry:5000/dms:phase11}"
export DMS_PHASE10_DOCKER_IMAGE="${DMS_PHASE11_DOCKER_IMAGE:-${registry_host}/dms:phase11}"
export DMS_PHASE10_C1_CEPH_MOUNT_PATH="${DMS_PHASE11_C1_CEPH_MOUNT_PATH:-/mnt/testbed-cephfs}"
export DMS_PHASE10_C2_CEPH_MOUNT_PATH="${DMS_PHASE11_C2_CEPH_MOUNT_PATH:-/mnt/testbed-cephfs-c2}"
export DMS_PHASE10_C1_NODE="${DMS_PHASE11_C1_NODE:-c1-worker}"
export DMS_PHASE10_C2_NODE="${DMS_PHASE11_C2_NODE:-c2-worker}"
export DMS_PHASE10_CLEANUP=0
export DMS_PHASE10_SKIP_IMAGE_BUILD="${DMS_PHASE11_SKIP_IMAGE_BUILD:-${DMS_PHASE10_SKIP_IMAGE_BUILD:-0}}"

"${repo_dir}/scripts/verify-phase10-testbed.sh"

python_bin="${DMS_PYTHON:-}"
if [[ -z "${python_bin}" ]]; then
  for candidate in python3 /tmp/dms-phase3-venv/bin/python3 /tmp/dms-phase2-venv/bin/python3; do
    if "${candidate}" - <<'PY' >/dev/null 2>&1
import fastapi
import ldap3
import psycopg
PY
    then
      python_bin="${candidate}"
      break
    fi
  done
fi
python_bin="${python_bin:-python3}"

postgres_password="$(
  ssh c1-control "kubectl -n postgresql get secret postgresql-auth -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d"
)"
encoded_password="$(
  POSTGRES_PASSWORD="${postgres_password}" "${python_bin}" - <<'PY'
import os
from urllib.parse import quote

print(quote(os.environ["POSTGRES_PASSWORD"], safe=""))
PY
)"

export DMS_DATABASE_URL="postgresql://${DMS_PHASE10_POSTGRES_USER}:${encoded_password}@${DMS_PHASE10_POSTGRES_HOST}:${DMS_PHASE10_POSTGRES_PORT}/${DMS_PHASE10_OPERATIONAL_DB}"
export DMS_OBSERVABILITY_DATABASE_URL="postgresql://${DMS_PHASE10_POSTGRES_USER}:${encoded_password}@${DMS_PHASE10_POSTGRES_HOST}:${DMS_PHASE10_POSTGRES_PORT}/${DMS_PHASE10_OBSERVABILITY_DB}"
export DMS_CONTROL_CLUSTER_NAME="${DMS_CONTROL_CLUSTER_NAME:-cluster-a}"
export DMS_AGENT_REPORT_STALE_SECONDS="${DMS_AGENT_REPORT_STALE_SECONDS:-300}"
export DMS_KUBERNETES_INVENTORY_MODE="${DMS_KUBERNETES_INVENTORY_MODE:-ssh-kubectl}"
export DMS_CLUSTER_CONTROL_HOSTS_JSON="${DMS_CLUSTER_CONTROL_HOSTS_JSON:-{\"cluster-a\":\"c1-control\",\"cluster-b\":\"c2-control\"}}"
export DMS_AUTH_SHARED_TOKEN="${DMS_PHASE10_AUTH_TOKEN}"
export DMS_LDAP_URI="${DMS_LDAP_URI:-ldap://192.168.56.31}"
export DMS_LDAP_BASE_DN="${DMS_LDAP_BASE_DN:-dc=testbed,dc=local}"
export DMS_LDAP_BIND_DN="${DMS_LDAP_BIND_DN:-cn=admin,dc=testbed,dc=local}"
export DMS_LDAP_BIND_PASSWORD="${DMS_LDAP_BIND_PASSWORD:-${LDAP_ADMIN_PASSWORD:-testbed-admin}}"
export DMS_LDAP_USER_SEARCH_BASE="${DMS_LDAP_USER_SEARCH_BASE:-ou=people,dc=testbed,dc=local}"
export DMS_LDAP_GROUP_SEARCH_BASE="${DMS_LDAP_GROUP_SEARCH_BASE:-ou=groups,dc=testbed,dc=local}"
export DMS_FILESYSTEM_MUTATION_MODE="${DMS_FILESYSTEM_MUTATION_MODE:-ssh-host-exec}"
export DMS_FILESYSTEM_EXEC_TIMEOUT_SECONDS="${DMS_FILESYSTEM_EXEC_TIMEOUT_SECONDS:-60}"
export DMS_FILESYSTEM_EXEC_USE_SUDO="${DMS_FILESYSTEM_EXEC_USE_SUDO:-true}"
export DMS_PHASE11_C1_CEPH_MOUNT_PATH="${DMS_PHASE10_C1_CEPH_MOUNT_PATH}"
export DMS_PHASE11_C2_CEPH_MOUNT_PATH="${DMS_PHASE10_C2_CEPH_MOUNT_PATH}"
export DMS_PHASE11_C1_NODE="${DMS_PHASE10_C1_NODE}"
export DMS_PHASE11_C2_NODE="${DMS_PHASE10_C2_NODE}"

cd "${repo_dir}"
"${python_bin}" scripts/phase11_ceph_host_filesystem_expiry.py

if [[ "${DMS_PHASE11_CLEANUP:-1}" == "1" ]]; then
  printf '== Cleanup Phase 11 manifests ==\n'
  ssh c1-control "kubectl delete namespace ${namespace} --ignore-not-found=true"
  ssh c2-control "kubectl delete namespace ${namespace} --ignore-not-found=true"
fi
