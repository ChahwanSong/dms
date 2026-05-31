#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

export DMS_PHASE13_NAMESPACE="${DMS_PHASE14_NAMESPACE:-dms-phase14}"
export DMS_PHASE13_DB_SUFFIX="${DMS_PHASE14_DB_SUFFIX:-phase14_$(date +%Y%m%d%H%M%S)}"
export DMS_PHASE13_K8S_IMAGE="${DMS_PHASE14_K8S_IMAGE:-testbed-registry:5000/dms:phase14}"
export DMS_PHASE13_DOCKER_IMAGE="${DMS_PHASE14_DOCKER_IMAGE:-192.168.56.11:5000/dms:phase14}"
export DMS_PHASE13_CLEANUP=0

cleanup() {
  if [[ "${DMS_PHASE14_CLEANUP:-1}" == "1" ]]; then
    ssh c1-control "kubectl delete namespace ${DMS_PHASE13_NAMESPACE} --ignore-not-found=true"
    ssh c2-control "kubectl delete namespace ${DMS_PHASE13_NAMESPACE} --ignore-not-found=true"
  fi
}
trap cleanup EXIT

source "${repo_dir}/scripts/verify-phase13-testbed.sh"

printf '== Phase 14 runtime hardening checks ==\n'
cd "${repo_dir}"
"${python_bin}" scripts/phase14_runtime_hardening.py
