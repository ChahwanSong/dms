#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
testbed_dir="${DMS_TESTBED_DIR:-/home/mason/workspace/testbed}"
shared_dir="${testbed_dir}/shared_directory"
suffix="${DMS_PHASE22_SUFFIX:-$(date +%Y%m%d%H%M%S)}"
namespace="${DMS_PHASE22_NAMESPACE:-dms-phase22-${suffix}}"
service_account="${DMS_PHASE22_SERVICE_ACCOUNT:-dms-dm-worker}"
queue="${DMS_PHASE22_QUEUE:-dms-phase22-${suffix}}"
priority_class="${DMS_PHASE22_PRIORITY_CLASS:-dms-phase22-normal}"
ceph_mount="${DMS_PHASE22_CEPH_MOUNT:-/mnt/testbed-cephfs}"
nodes="${DMS_PHASE22_NODES:-c1-control,c1-worker}"
local_image="${DMS_PHASE22_MPIFILEUTILS_LOCAL_IMAGE:-dms-mpifileutils-real:phase22-${suffix}}"
registry_image="${DMS_PHASE22_MPIFILEUTILS_REGISTRY_IMAGE:-192.168.56.11:5000/dms-mpifileutils:phase22-${suffix}}"
k8s_image="${DMS_PHASE22_MPIFILEUTILS_K8S_IMAGE:-testbed-registry:5000/dms-mpifileutils:phase22-${suffix}}"
remote_root="/shared_directory/dms-phase22-${suffix}"
local_shared_root="${shared_dir}/dms-phase22-${suffix}"
image_tar="${local_shared_root}/dms-mpifileutils-phase22.tar"

printf '== Phase 22 Data Management MPI/Volcano verifier ==\n'
printf 'suffix=%s namespace=%s queue=%s priorityClass=%s nodes=%s\n' \
  "${suffix}" "${namespace}" "${queue}" "${priority_class}" "${nodes}"

printf '== Check testbed metadata ==\n'
test -f "${testbed_dir}/testbed-info.json"
test -f "${testbed_dir}/testbed-summary.json"
sed -n '1,80p' "${testbed_dir}/testbed-summary.json" || true

printf '== Verify CephFS host mounts on execution nodes ==\n'
IFS=',' read -r -a node_array <<< "${nodes}"
for node in "${node_array[@]}"; do
  ssh "${node}" "bash -lc 'sudo mkdir -p ${ceph_mount@Q}; sudo mount ${ceph_mount@Q} || true; findmnt -t ceph ${ceph_mount@Q} -o TARGET,SOURCE,FSTYPE; sudo test -w ${ceph_mount@Q}'"
done

printf '== Verify Volcano and MPI Operator prerequisites ==\n'
ssh c1-control "bash -lc '
set -e
kubectl get crd jobs.batch.volcano.sh queues.scheduling.volcano.sh podgroups.scheduling.volcano.sh
if ! kubectl get crd mpijobs.kubeflow.org >/dev/null 2>&1; then
  kubectl apply --server-side -f https://raw.githubusercontent.com/kubeflow/mpi-operator/v0.7.0/deploy/v2beta1/mpi-operator.yaml
fi
kubectl wait --for=condition=Available deployment/mpi-operator -n mpi-operator --timeout=180s
kubectl get crd mpijobs.kubeflow.org
'"

printf '== Build mpifileutils job image ==\n'
if [[ "${DMS_PHASE22_SKIP_IMAGE_BUILD:-0}" != "1" ]]; then
  docker build \
    -f "${repo_dir}/install/docker/Dockerfile.mpifileutils" \
    -t "${local_image}" \
    -t "${registry_image}" \
    "${repo_dir}"
fi
docker run --rm --entrypoint sh "${local_image}" -c '
set -e
command -v dscan
command -v dsync
command -v drm
command -v nsync
command -v mpirun
ompi_info --parsable --all >/dev/null
test -x /usr/sbin/sshd
getent passwd alice
nsync --help 2>&1 | grep -q -- --role-mode
'

printf '== Push image to testbed registry ==\n'
rm -rf "${local_shared_root}"
mkdir -p "${local_shared_root}"
docker save "${registry_image}" -o "${image_tar}"
ssh c1-control "bash -lc 'set -e; command -v skopeo; skopeo copy --dest-tls-verify=false docker-archive:${remote_root}/dms-mpifileutils-phase22.tar docker://192.168.56.11:5000/dms-mpifileutils:phase22-${suffix}'"

printf '== Prepare Kubernetes namespace, queue, priority class, and RBAC ==\n'
ssh c1-control "bash -lc '
set -e
kubectl create namespace ${namespace@Q} --dry-run=client -o yaml | kubectl apply -f -
kubectl -n ${namespace@Q} create serviceaccount ${service_account@Q} --dry-run=client -o yaml | kubectl apply -f -
kubectl create clusterrolebinding dms-phase22-${suffix} --clusterrole=cluster-admin --serviceaccount=${namespace}:${service_account} --dry-run=client -o yaml | kubectl apply -f -
cat <<YAML | kubectl apply -f -
apiVersion: scheduling.volcano.sh/v1beta1
kind: Queue
metadata:
  name: ${queue}
spec:
  weight: 1
  reclaimable: false
---
apiVersion: scheduling.k8s.io/v1
kind: PriorityClass
metadata:
  name: ${priority_class}
value: 1000
globalDefault: false
description: DMS Phase 22 verification priority class.
YAML
kubectl get queue ${queue@Q}
kubectl get priorityclass ${priority_class@Q}
'"

printf '== Copy current DMS source and Phase 22 runner into shared directory ==\n'
mkdir -p "${local_shared_root}/src"
rm -rf "${local_shared_root}/src/dms"
cp -a "${repo_dir}/src/dms" "${local_shared_root}/src/dms"
cp "${repo_dir}/scripts/phase22_data_management_mpi.py" "${local_shared_root}/phase22_data_management_mpi.py"

printf '== Run live Phase 22 MPI/Volcano adapter verification ==\n'
ssh c1-control "bash -lc '
set -e
export PYTHONPATH=${remote_root}/src
export DMS_PHASE22_SUFFIX=${suffix@Q}
export DMS_PHASE22_NAMESPACE=${namespace@Q}
export DMS_PHASE22_SERVICE_ACCOUNT=${service_account@Q}
export DMS_PHASE22_QUEUE=${queue@Q}
export DMS_PHASE22_PRIORITY_CLASS=${priority_class@Q}
export DMS_PHASE22_CEPH_MOUNT=${ceph_mount@Q}
export DMS_PHASE22_NODES=${nodes@Q}
export DMS_PHASE22_K8S_IMAGE=${k8s_image@Q}
python3 ${remote_root}/phase22_data_management_mpi.py
'"

printf '== Show final Kubernetes and artifact evidence ==\n'
ssh c1-control "kubectl -n ${namespace} get mpijob,job.batch.volcano.sh,pod -o wide || true"
ssh c1-control "find ${ceph_mount@Q}/dms-phase22-artifacts-${suffix@Q} -maxdepth 4 -type f | sort"

if [[ "${DMS_PHASE22_CLEANUP:-1}" == "1" ]]; then
  printf '== Cleanup Phase 22 Kubernetes resources ==\n'
  ssh c1-control "bash -lc '
set -e
kubectl delete namespace ${namespace@Q} --ignore-not-found=true
kubectl delete clusterrolebinding dms-phase22-${suffix} --ignore-not-found=true
kubectl delete queue ${queue@Q} --ignore-not-found=true
kubectl delete priorityclass ${priority_class@Q} --ignore-not-found=true
'"
fi
