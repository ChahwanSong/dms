#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

export DMS_PHASE13_NAMESPACE="${DMS_PHASE15_NAMESPACE:-dms-phase15}"
export DMS_PHASE13_DB_SUFFIX="${DMS_PHASE15_DB_SUFFIX:-phase15_$(date +%Y%m%d%H%M%S)}"
export DMS_PHASE13_K8S_IMAGE="${DMS_PHASE15_K8S_IMAGE:-testbed-registry:5000/dms:phase15}"
export DMS_PHASE13_DOCKER_IMAGE="${DMS_PHASE15_DOCKER_IMAGE:-192.168.56.11:5000/dms:phase15}"
export DMS_PHASE13_CLEANUP=0
export DMS_FILESYSTEM_EXEC_TIMEOUT_SECONDS="${DMS_PHASE15_FILESYSTEM_EXEC_TIMEOUT_SECONDS:-180}"

cleanup() {
  if [[ "${DMS_PHASE15_CLEANUP:-1}" == "1" ]]; then
    ssh c1-control "kubectl delete namespace ${DMS_PHASE13_NAMESPACE} --ignore-not-found=true"
    ssh c2-control "kubectl delete namespace ${DMS_PHASE13_NAMESPACE} --ignore-not-found=true"
  fi
}
trap cleanup EXIT

source "${repo_dir}/scripts/verify-phase13-testbed.sh"

printf '== Phase 15 resource expiry checks ==\n'
cd "${repo_dir}"
"${python_bin}" scripts/phase15_resource_expiry.py
