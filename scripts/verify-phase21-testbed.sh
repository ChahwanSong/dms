#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
testbed_dir="${DMS_TESTBED_DIR:-/home/mason/workspace/testbed}"
shared_dir="${testbed_dir}/shared_directory"
suffix="${DMS_PHASE21_DB_SUFFIX:-$(date +%Y%m%d%H%M%S)}"
namespace="${DMS_PHASE21_NAMESPACE:-dms-phase21}"
node_port="${DMS_PHASE21_API_NODE_PORT:-30101}"
auth_token="${DMS_PHASE21_AUTH_TOKEN:-phase21-testbed-token}"
c1_node="${DMS_PHASE21_C1_NODE:-c1-worker}"
ceph_mount="${DMS_PHASE21_CEPH_MOUNT:-/mnt/testbed-cephfs}"
source_rel="${DMS_PHASE21_SYNC_SOURCE_PATH:-dms-phase20-${suffix}/sync-source}"
phase20_dest_rel="${DMS_PHASE21_PHASE20_DESTINATION_PATH:-dms-phase20-${suffix}/sync-dest}"
phase20_rm_rel="${DMS_PHASE21_PHASE20_RM_TARGET_PATH:-dms-phase20-${suffix}/remove-me}"
phase21_dest_rel="${DMS_PHASE21_SYNC_DESTINATION_PATH:-dms-phase21-${suffix}/sync-dest}"
phase21_rm_rel="${DMS_PHASE21_RM_TARGET_PATH:-dms-phase21-${suffix}/remove-me}"
manifest_dir="${shared_dir}/dms-phase20-${suffix}"
python_bin="${DMS_PYTHON:-}"

if [[ -z "${python_bin}" ]]; then
  for candidate in /tmp/dms-phase3-venv/bin/python3 /tmp/dms-phase2-venv/bin/python3 python3; do
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

printf '== Phase 21 uses Phase 20 deployment harness with Phase 21 assertions ==\n'
printf 'suffix=%s namespace=%s node_port=%s\n' "${suffix}" "${namespace}" "${node_port}"

export DMS_PHASE20_DB_SUFFIX="${suffix}"
export DMS_PHASE20_NAMESPACE="${namespace}"
export DMS_PHASE20_API_NODE_PORT="${node_port}"
export DMS_PHASE20_AUTH_TOKEN="${auth_token}"
export DMS_PHASE20_C1_NODE="${c1_node}"
export DMS_PHASE20_CEPH_MOUNT="${ceph_mount}"
export DMS_PHASE20_SYNC_SOURCE_PATH="${source_rel}"
export DMS_PHASE20_SYNC_DESTINATION_PATH="${phase20_dest_rel}"
export DMS_PHASE20_RM_TARGET_PATH="${phase20_rm_rel}"
export DMS_PHASE20_RUN_MPI_SMOKE="${DMS_PHASE21_RUN_MPI_SMOKE:-0}"
export DMS_PHASE20_CLEANUP=0
export DMS_PHASE20_DOCKER_IMAGE="${DMS_PHASE21_DOCKER_IMAGE:-192.168.56.11:5000/dms:phase21-${suffix}}"
export DMS_PHASE20_K8S_IMAGE="${DMS_PHASE21_K8S_IMAGE:-testbed-registry:5000/dms:phase21-${suffix}}"
export DMS_PHASE20_MPIFILEUTILS_LOCAL_IMAGE="${DMS_PHASE21_MPIFILEUTILS_LOCAL_IMAGE:-dms-mpifileutils-real:phase21-${suffix}}"
export DMS_PHASE20_MPIFILEUTILS_DOCKER_IMAGE="${DMS_PHASE21_MPIFILEUTILS_DOCKER_IMAGE:-192.168.56.11:5000/dms-mpifileutils:phase21-${suffix}}"

"${repo_dir}/scripts/verify-phase20-testbed.sh"

printf '== Prepare extra Phase 21 rm/destination fixture ==\n'
ssh "${c1_node}" "
set -e
sudo mkdir -p ${ceph_mount@Q}/$(dirname "${phase21_dest_rel}") ${ceph_mount@Q}/${phase21_rm_rel@Q}
sudo chown -R alice:developers ${ceph_mount@Q}/$(dirname "${phase21_dest_rel}") ${ceph_mount@Q}/${phase21_rm_rel@Q}
sudo chmod 0750 ${ceph_mount@Q}/$(dirname "${phase21_dest_rel}") ${ceph_mount@Q}/${phase21_rm_rel@Q}
sudo -u alice sh -c 'printf phase21-doomed > ${ceph_mount@Q}/${phase21_rm_rel@Q}/doomed.txt'
sudo find ${ceph_mount@Q}/dms-phase21-${suffix@Q} -maxdepth 3 -printf '%M %u %g %p\n'
"

printf '== Prepare Phase 21 synthetic split StorageClass aliases ==\n'
for storage_class in testbed-cephfs-phase21-src testbed-cephfs-phase21-dst; do
  ssh c1-control "kubectl get storageclass testbed-cephfs -o json | python3 -c '
import json
import sys

obj = json.load(sys.stdin)
obj[\"metadata\"] = {
    \"name\": \"${storage_class}\",
    \"annotations\": {
        \"dms.openai.com/phase21-synthetic\": \"true\",
        \"dms.openai.com/source-storage-class\": \"testbed-cephfs\",
    },
}
obj.pop(\"status\", None)
print(json.dumps(obj))
' | kubectl apply -f -"
done

printf '== Run Phase 21 Data Management minimal verifier ==\n'
export DMS_PHASE21_API_URL="http://192.168.56.11:${node_port}"
export DMS_PHASE21_AUTH_TOKEN="${auth_token}"
export DMS_PHASE21_CEPH_MOUNT="${ceph_mount}"
export DMS_PHASE21_SCAN_TARGET_PATH="${source_rel}"
export DMS_PHASE21_SYNC_SOURCE_PATH="${source_rel}"
export DMS_PHASE21_SYNC_DESTINATION_PATH="${phase21_dest_rel}"
export DMS_PHASE21_RM_TARGET_PATH="${phase21_rm_rel}"
export DMS_PHASE21_NSYNC_SOURCE_PATH="input-${suffix}"
export DMS_PHASE21_NSYNC_DESTINATION_PATH="output-${suffix}"
cd "${repo_dir}"
"${python_bin}" scripts/phase21_data_management_minimal.py | tee "${manifest_dir}/phase21-api-summary.json"

printf '== Verify Phase 21 filesystem effects and artifact evidence ==\n'
ssh "${c1_node}" "
set -e
sudo -u alice test -f ${ceph_mount@Q}/${phase21_dest_rel@Q}/alpha.txt
sudo -u alice test -f ${ceph_mount@Q}/${phase21_dest_rel@Q}/beta.txt
sudo -u alice test -f ${ceph_mount@Q}/${phase21_dest_rel@Q}/nested/gamma.txt
test ! -e ${ceph_mount@Q}/${phase21_rm_rel@Q}
sudo find ${ceph_mount@Q}/dms-phase21-${suffix@Q} -maxdepth 3 -printf '%M %u %g %p\n'
"
ssh c1-control "kubectl -n ${namespace} get job.batch.volcano.sh,pod -l app.kubernetes.io/name=dms-data-management -o wide || true"
ssh "${c1_node}" "find ${ceph_mount@Q}/dms-phase20-artifacts-${suffix@Q} -maxdepth 4 -type f -print -exec sed -n '1,20p' {} \\; || true"

if [[ "${DMS_PHASE21_CLEANUP:-1}" == "1" ]]; then
  printf '== Cleanup Phase 21 namespace ==\n'
  ssh c1-control "kubectl delete namespace ${namespace} --ignore-not-found=true"
  ssh c1-control "kubectl delete storageclass testbed-cephfs-phase21-src testbed-cephfs-phase21-dst --ignore-not-found=true"
fi
