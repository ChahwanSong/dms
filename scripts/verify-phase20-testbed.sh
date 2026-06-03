#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
testbed_dir="${DMS_TESTBED_DIR:-/home/mason/workspace/testbed}"
shared_dir="${testbed_dir}/shared_directory"
suffix="${DMS_PHASE20_DB_SUFFIX:-$(date +%Y%m%d%H%M%S)}"
operational_db="${DMS_PHASE20_OPERATIONAL_DB:-dms_phase20_${suffix}}"
observability_db="${DMS_PHASE20_OBSERVABILITY_DB:-dms_phase20_obs_${suffix}}"
postgres_host="${DMS_PHASE20_POSTGRES_HOST:-192.168.56.11}"
postgres_port="${DMS_PHASE20_POSTGRES_PORT:-30432}"
postgres_user="${DMS_PHASE20_POSTGRES_USER:-appuser}"
namespace="${DMS_PHASE20_NAMESPACE:-dms-phase20}"
node_port="${DMS_PHASE20_API_NODE_PORT:-30100}"
auth_token="${DMS_PHASE20_AUTH_TOKEN:-phase20-testbed-token}"
registry_host="${DMS_PHASE20_REGISTRY_HOST:-192.168.56.11:5000}"
mpifileutils_ref="${DMS_PHASE20_MPIFILEUTILS_REF:-e3bfee10970bb4e24204d28689e3337e9741cca4}"
mpifileutils_local_image="${DMS_PHASE20_MPIFILEUTILS_LOCAL_IMAGE:-dms-mpifileutils-real:phase20-${suffix}}"
mpifileutils_docker_image="${DMS_PHASE20_MPIFILEUTILS_DOCKER_IMAGE:-${registry_host}/dms-mpifileutils:phase20-${suffix}}"
docker_image="${DMS_PHASE20_DOCKER_IMAGE:-${registry_host}/dms:phase20-${suffix}}"
k8s_image="${DMS_PHASE20_K8S_IMAGE:-testbed-registry:5000/dms:phase20-${suffix}}"
mpi_docker_image="${DMS_PHASE20_MPI_DOCKER_IMAGE:-${registry_host}/dms-mpifileutils-mpi:phase20-${suffix}}"
mpi_k8s_image="${DMS_PHASE20_MPI_K8S_IMAGE:-testbed-registry:5000/dms-mpifileutils-mpi:phase20-${suffix}}"
dm_job_image="${DMS_PHASE20_DM_JOB_IMAGE:-${k8s_image}}"
dm_job_image_ref="${DMS_PHASE20_DM_JOB_IMAGE_REF:-chahwansong/mpifileutils@${mpifileutils_ref};dms-phase20}"
manifest_dir="${shared_dir}/dms-phase20-${suffix}"
c1_node="${DMS_PHASE20_C1_NODE:-c1-worker}"
ceph_mount="${DMS_PHASE20_CEPH_MOUNT:-/mnt/testbed-cephfs}"
fixture_root="${ceph_mount}/dms-phase20-${suffix}"
artifact_dir="${ceph_mount}/dms-phase20-artifacts-${suffix}"
source_rel="dms-phase20-${suffix}/sync-source"
destination_rel="dms-phase20-${suffix}/sync-dest"
rm_target_rel="dms-phase20-${suffix}/remove-me"
source_dir="${ceph_mount}/${source_rel}"
destination_dir="${ceph_mount}/${destination_rel}"
rm_target_dir="${ceph_mount}/${rm_target_rel}"
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

printf '== Testbed metadata ==\n'
sed -n '1,120p' "${testbed_dir}/testbed-summary.json"

printf '== Testbed readiness ==\n'
ssh c1-control 'kubectl get nodes -o wide'
ssh c1-control 'kubectl -n volcano-system get deploy,pod -o wide'
ssh "${c1_node}" "findmnt -rn ${ceph_mount@Q} -o FSTYPE,TARGET,SOURCE && id alice && getent group developers"

printf '== Prepare Phase 20 CephFS fixture ==\n'
ssh "${c1_node}" "
set -e
sudo rm -rf ${fixture_root@Q} ${artifact_dir@Q}
sudo mkdir -p ${source_dir@Q} ${destination_dir@Q} ${rm_target_dir@Q} ${artifact_dir@Q}
sudo chown -R alice:developers ${fixture_root@Q}
sudo chmod 0750 ${fixture_root@Q} ${source_dir@Q} ${destination_dir@Q} ${rm_target_dir@Q}
sudo chown root:root ${artifact_dir@Q}
sudo chmod 0777 ${artifact_dir@Q}
sudo -u alice sh -c 'printf phase20-alpha > ${source_dir@Q}/alpha.txt'
sudo -u alice sh -c 'printf phase20-beta > ${source_dir@Q}/beta.txt'
sudo -u alice mkdir -p ${source_dir@Q}/nested
sudo -u alice sh -c 'printf phase20-gamma > ${source_dir@Q}/nested/gamma.txt'
sudo -u alice sh -c 'printf doomed > ${rm_target_dir@Q}/doomed.txt'
sudo find ${fixture_root@Q} -maxdepth 3 -printf '%M %u %g %p\n'
sudo find ${artifact_dir@Q} -maxdepth 2 -printf '%M %u %g %p\n'
"

postgres_password="$(
  ssh c1-control "kubectl -n postgresql get secret postgresql-auth -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d"
)"
export POSTGRES_PASSWORD="${postgres_password}"
export PHASE20_OPERATIONAL_DB="${operational_db}"
export PHASE20_OBSERVABILITY_DB="${observability_db}"
export PHASE20_POSTGRES_HOST="${postgres_host}"
export PHASE20_POSTGRES_PORT="${postgres_port}"
export PHASE20_POSTGRES_USER="${postgres_user}"

"${python_bin}" - <<'PY'
import os
import re

import psycopg
from psycopg import sql

db_names = [os.environ["PHASE20_OPERATIONAL_DB"], os.environ["PHASE20_OBSERVABILITY_DB"]]
for db_name in db_names:
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", db_name):
        raise SystemExit(f"unsafe database name: {db_name}")

with psycopg.connect(
    host=os.environ["PHASE20_POSTGRES_HOST"],
    port=int(os.environ["PHASE20_POSTGRES_PORT"]),
    dbname="postgres",
    user=os.environ["PHASE20_POSTGRES_USER"],
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
  "${python_bin}" - <<'PY'
import os
from urllib.parse import quote

print(quote(os.environ["POSTGRES_PASSWORD"], safe=""))
PY
)"

export DMS_DATABASE_URL="postgresql://${postgres_user}:${encoded_password}@${postgres_host}:${postgres_port}/${operational_db}"
export DMS_OBSERVABILITY_DATABASE_URL="postgresql://${postgres_user}:${encoded_password}@${postgres_host}:${postgres_port}/${observability_db}"
export DMS_PHASE20_API_URL="http://192.168.56.11:${node_port}"
export DMS_PHASE20_AUTH_TOKEN="${auth_token}"
export DMS_PHASE20_SYNC_SOURCE_PATH="${source_rel}"
export DMS_PHASE20_SYNC_DESTINATION_PATH="${destination_rel}"
export DMS_PHASE20_RM_TARGET_PATH="${rm_target_rel}"
export DMS_PHASE20_CEPH_MOUNT="${ceph_mount}"
export DMS_PHASE20_PREVIEW_EXPIRY_SLEEP_SECONDS="${DMS_PHASE20_PREVIEW_EXPIRY_SLEEP_SECONDS:-17}"

mkdir -p "${manifest_dir}"

cat >"${manifest_dir}/Dockerfile.phase20" <<'EOF'
ARG MPIFILEUTILS_IMAGE
FROM ${MPIFILEUTILS_IMAGE} AS mpifileutils

FROM python:3.11-slim

ARG KUBECTL_VERSION=v1.34.0

ENV PYTHONUNBUFFERED=1 \
    HOME=/home/dms \
    PATH=/opt/mpifileutils/bin:${PATH} \
    LD_LIBRARY_PATH=/opt/mpifileutils/lib

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        libgomp1 \
        mpich \
        openssh-client \
    && curl -fsSLo /usr/local/bin/kubectl \
        "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
    && chmod 0755 /usr/local/bin/kubectl \
    && addgroup --system --gid 65532 dms \
    && adduser --system --uid 65532 --ingroup dms --home /home/dms dms \
    && groupadd --gid 10000 developers \
    && useradd --uid 10000 --gid 10000 --no-create-home --shell /usr/sbin/nologin alice \
    && rm -rf /var/lib/apt/lists/*

COPY --from=mpifileutils /opt/mpifileutils /opt/mpifileutils
COPY pyproject.toml /app/
COPY src /app/src

RUN pip install --no-cache-dir '.[postgres,ldap,kubernetes]'

USER 65532:65532
ENTRYPOINT ["dms"]
EOF

cat >"${manifest_dir}/Dockerfile.phase20-mpi" <<'EOF'
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends openssh-server \
    && mkdir -p /run/sshd \
    && sed -i 's/^#PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config \
    && sed -i 's/^#PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config \
    && rm -rf /var/lib/apt/lists/*
CMD ["/usr/sbin/sshd", "-D", "-e"]
EOF

if [[ "${DMS_PHASE20_SKIP_IMAGE_BUILD:-0}" != "1" ]]; then
  printf '== Build and push Phase 20 mpifileutils image ==\n'
  docker build \
    -f "${repo_dir}/install/docker/Dockerfile.mpifileutils" \
    --build-arg "MPIFILEUTILS_REF=${mpifileutils_ref}" \
    -t "${mpifileutils_local_image}" \
    -t "${mpifileutils_docker_image}" \
    "${repo_dir}"
  { docker run --rm "${mpifileutils_local_image}" dsync --help 2>&1 || true; } | grep -q 'Usage: dsync'
  { docker run --rm "${mpifileutils_local_image}" nsync --help 2>&1 || true; } | grep -q 'Usage: nsync'
  { docker run --rm "${mpifileutils_local_image}" drm --help 2>&1 || true; } | grep -q 'Usage: drm'
  docker push "${mpifileutils_docker_image}" || true

  printf '== Build and push Phase 20 DMS/mpifileutils image ==\n'
  docker build \
    -f "${manifest_dir}/Dockerfile.phase20" \
    --build-arg "MPIFILEUTILS_IMAGE=${mpifileutils_local_image}" \
    -t "${docker_image}" \
    "${repo_dir}"
  { docker run --rm --entrypoint dscan "${docker_image}" --help 2>&1 || true; } | grep -q 'Usage: dscan'
  { docker run --rm --entrypoint dsync "${docker_image}" --help 2>&1 || true; } | grep -q 'Usage: dsync'
  { docker run --rm --entrypoint nsync "${docker_image}" --help 2>&1 || true; } | grep -q 'Usage: nsync'
  { docker run --rm --entrypoint drm "${docker_image}" --help 2>&1 || true; } | grep -q 'Usage: drm'
  if ! docker push "${docker_image}"; then
    printf 'docker push failed; falling back to docker save + skopeo copy on c1-control\n'
    image_archive="${manifest_dir}/dms-phase20-image.tar"
    docker save "${docker_image}" -o "${image_archive}"
    ssh c1-control \
      "skopeo copy --dest-tls-verify=false docker-archive:/shared_directory/$(basename "${manifest_dir}")/dms-phase20-image.tar docker://${docker_image}"
  fi

  printf '== Build and push Phase 20 MPI ssh image ==\n'
  docker build \
    -f "${manifest_dir}/Dockerfile.phase20-mpi" \
    --build-arg "BASE_IMAGE=${docker_image}" \
    -t "${mpi_docker_image}" \
    "${repo_dir}"
  if ! docker push "${mpi_docker_image}"; then
    printf 'MPI docker push failed; falling back to docker save + skopeo copy on c1-control\n'
    mpi_image_archive="${manifest_dir}/dms-phase20-mpi-image.tar"
    docker save "${mpi_docker_image}" -o "${mpi_image_archive}"
    ssh c1-control \
      "skopeo copy --dest-tls-verify=false docker-archive:/shared_directory/$(basename "${manifest_dir}")/dms-phase20-mpi-image.tar docker://${mpi_docker_image}"
  fi
fi

cat >"${manifest_dir}/phase20.yaml" <<EOF
apiVersion: v1
kind: Namespace
metadata:
  name: ${namespace}
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: dms-api
  namespace: ${namespace}
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: dms-planner
  namespace: ${namespace}
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: dms-dm-worker
  namespace: ${namespace}
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: dms-agent
  namespace: ${namespace}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: dms-dm-volcano
  namespace: ${namespace}
rules:
  - apiGroups: ["batch.volcano.sh"]
    resources: ["jobs"]
    verbs: ["create", "get", "list", "watch", "delete"]
  - apiGroups: [""]
    resources: ["pods", "pods/log"]
    verbs: ["get", "list", "watch"]
  - apiGroups: [""]
    resources: ["pods"]
    verbs: ["create", "delete"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: dms-dm-volcano
  namespace: ${namespace}
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: dms-dm-volcano
subjects:
  - kind: ServiceAccount
    name: dms-dm-worker
    namespace: ${namespace}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: dms-agent-readonly-${namespace}
rules:
  - apiGroups: [""]
    resources: ["nodes", "pods"]
    verbs: ["get", "list"]
  - apiGroups: ["storage.k8s.io"]
    resources: ["storageclasses", "csidrivers"]
    verbs: ["get", "list"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: dms-agent-readonly-${namespace}
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: dms-agent-readonly-${namespace}
subjects:
  - kind: ServiceAccount
    name: dms-agent
    namespace: ${namespace}
  - kind: ServiceAccount
    name: dms-api
    namespace: ${namespace}
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: dms-agent-storages
  namespace: ${namespace}
data:
  storages.json: |
    {
      "storages": [
        {
          "storage_name": "cephfs-a",
          "backend_type": "cephfs",
          "cluster_name": "cluster-a",
          "storage_class_name": "testbed-cephfs",
          "csi_driver": "rook-ceph.cephfs.csi.ceph.com",
          "mount_paths": ["${ceph_mount}"],
          "network_endpoints": []
        }
      ]
    }
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: dms-api
  namespace: ${namespace}
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: dms-api
  template:
    metadata:
      labels:
        app.kubernetes.io/name: dms-api
        app.kubernetes.io/part-of: dms
    spec:
      serviceAccountName: dms-api
      containers:
        - name: api
          image: ${k8s_image}
          imagePullPolicy: Always
          command: ["dms", "api", "--host", "0.0.0.0", "--port", "8080"]
          env:
            - name: DMS_DATABASE_URL
              value: "${DMS_DATABASE_URL}"
            - name: DMS_OBSERVABILITY_DATABASE_URL
              value: "${DMS_OBSERVABILITY_DATABASE_URL}"
            - name: DMS_AUTH_SHARED_TOKEN
              value: "${auth_token}"
            - name: DMS_LDAP_URI
              value: "ldap://192.168.56.31"
            - name: DMS_LDAP_BASE_DN
              value: "dc=testbed,dc=local"
            - name: DMS_LDAP_BIND_DN
              value: "cn=admin,dc=testbed,dc=local"
            - name: DMS_LDAP_BIND_PASSWORD
              value: "${DMS_LDAP_BIND_PASSWORD:-testbed-admin}"
            - name: DMS_LDAP_USER_SEARCH_BASE
              value: "ou=people,dc=testbed,dc=local"
            - name: DMS_LDAP_GROUP_SEARCH_BASE
              value: "ou=groups,dc=testbed,dc=local"
            - name: DMS_AGENT_REPORT_STALE_SECONDS
              value: "300"
            - name: DMS_KUBERNETES_INVENTORY_MODE
              value: "python-client"
            - name: DMS_CLUSTER_CONTROL_HOSTS_JSON
              value: '{"cluster-a":"incluster"}'
          ports:
            - name: http
              containerPort: 8080
          resources:
            requests:
              cpu: 50m
              memory: 128Mi
            limits:
              cpu: 300m
              memory: 384Mi
---
apiVersion: v1
kind: Service
metadata:
  name: dms-api
  namespace: ${namespace}
spec:
  type: NodePort
  selector:
    app.kubernetes.io/name: dms-api
  ports:
    - name: http
      port: 80
      targetPort: http
      nodePort: ${node_port}
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: dms-planner
  namespace: ${namespace}
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: dms-planner
  template:
    metadata:
      labels:
        app.kubernetes.io/name: dms-planner
        app.kubernetes.io/part-of: dms
    spec:
      serviceAccountName: dms-planner
      containers:
        - name: planner
          image: ${k8s_image}
          imagePullPolicy: Always
          command: ["dms", "planner", "--loop", "--interval", "2", "--limit", "25"]
          env:
            - name: DMS_DATABASE_URL
              value: "${DMS_DATABASE_URL}"
            - name: DMS_OBSERVABILITY_DATABASE_URL
              value: "${DMS_OBSERVABILITY_DATABASE_URL}"
          resources:
            requests:
              cpu: 25m
              memory: 96Mi
            limits:
              cpu: 200m
              memory: 256Mi
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: dms-dm-worker
  namespace: ${namespace}
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: dms-dm-worker
  template:
    metadata:
      labels:
        app.kubernetes.io/name: dms-dm-worker
        app.kubernetes.io/part-of: dms
        dms.io/worker-role: dm
    spec:
      serviceAccountName: dms-dm-worker
      nodeSelector:
        kubernetes.io/hostname: ${c1_node}
      containers:
        - name: dm-worker
          image: ${k8s_image}
          imagePullPolicy: Always
          command: ["dms", "dm-worker", "--worker-id", "\$(POD_NAME)", "--loop", "--interval", "2"]
          env:
            - name: POD_NAME
              valueFrom:
                fieldRef:
                  fieldPath: metadata.name
            - name: DMS_DATABASE_URL
              value: "${DMS_DATABASE_URL}"
            - name: DMS_OBSERVABILITY_DATABASE_URL
              value: "${DMS_OBSERVABILITY_DATABASE_URL}"
            - name: DMS_PREVIEW_TTL_SECONDS
              value: "15"
            - name: DMS_DM_NAMESPACE
              value: "${namespace}"
            - name: DMS_DM_JOB_IMAGE
              value: "${dm_job_image}"
            - name: DMS_DM_JOB_IMAGE_REF
              value: "${dm_job_image_ref}"
            - name: DMS_DM_SERVICE_ACCOUNT
              value: "dms-dm-worker"
            - name: DMS_DM_ARTIFACT_BASE_URI
              value: "file://${artifact_dir}"
            - name: DMS_DM_SCAN_TIMEOUT_SECONDS
              value: "180"
            - name: DMS_DM_SYNC_PREVIEW_TIMEOUT_SECONDS
              value: "180"
            - name: DMS_DM_SYNC_EXECUTION_TIMEOUT_SECONDS
              value: "180"
            - name: DMS_DM_RM_PREVIEW_TIMEOUT_SECONDS
              value: "180"
            - name: DMS_DM_RM_EXECUTION_TIMEOUT_SECONDS
              value: "180"
            - name: DMS_DM_CONFIRM_REQUIRE_PREVIEW_FINGERPRINT
              value: "true"
            - name: DMS_DM_MONITOR_POLL_SECONDS
              value: "2"
            - name: DMS_KUBERNETES_MUTATION_TIMEOUT_SECONDS
              value: "60"
            - name: DMS_KUBERNETES_INVENTORY_TIMEOUT_SECONDS
              value: "10"
          volumeMounts:
            - name: cephfs
              mountPath: ${ceph_mount}
          resources:
            requests:
              cpu: 50m
              memory: 128Mi
            limits:
              cpu: 500m
              memory: 512Mi
      volumes:
        - name: cephfs
          hostPath:
            path: ${ceph_mount}
            type: Directory
---
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: dms-dm-agent
  namespace: ${namespace}
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: dms-dm-agent
  template:
    metadata:
      labels:
        app.kubernetes.io/name: dms-dm-agent
        app.kubernetes.io/part-of: dms
    spec:
      serviceAccountName: dms-agent
      nodeSelector:
        kubernetes.io/hostname: ${c1_node}
      containers:
        - name: agent
          image: ${k8s_image}
          imagePullPolicy: Always
          command: ["dms", "agent-loop", "--interval", "5"]
          env:
            - name: DMS_AGENT_API_URL
              value: "http://dms-api.${namespace}.svc.cluster.local"
            - name: DMS_AUTH_SHARED_TOKEN
              value: "${auth_token}"
            - name: DMS_AGENT_CLUSTER_NAME
              value: "cluster-a"
            - name: DMS_AGENT_WORKER_ROLE
              value: "DM"
            - name: DMS_AGENT_REPORT_INTERVAL_SECONDS
              value: "5"
            - name: DMS_AGENT_REPORT_TIMEOUT_SECONDS
              value: "5"
            - name: DMS_AGENT_TOOLS
              value: "dscan,dsync,drm,nsync"
            - name: DMS_AGENT_IDENTITY_USERS
              value: "alice"
            - name: DMS_AGENT_NODE_NAME
              valueFrom:
                fieldRef:
                  fieldPath: spec.nodeName
            - name: DMS_AGENT_POD_NAME
              valueFrom:
                fieldRef:
                  fieldPath: metadata.name
            - name: DMS_AGENT_POD_NAMESPACE
              valueFrom:
                fieldRef:
                  fieldPath: metadata.namespace
          volumeMounts:
            - name: storages
              mountPath: /etc/dms/agent
              readOnly: true
            - name: cephfs
              mountPath: ${ceph_mount}
          resources:
            requests:
              cpu: 25m
              memory: 64Mi
            limits:
              cpu: 150m
              memory: 192Mi
      volumes:
        - name: storages
          configMap:
            name: dms-agent-storages
        - name: cephfs
          hostPath:
            path: ${ceph_mount}
            type: Directory
EOF

if [[ "${DMS_PHASE20_RESET_NAMESPACE:-1}" == "1" ]]; then
  printf '== Reset Phase 20 namespace ==\n'
  ssh c1-control "kubectl delete namespace ${namespace} --ignore-not-found=true --wait=true"
fi

printf '== Apply Phase 20 DMS manifests ==\n'
ssh c1-control "kubectl apply -f /shared_directory/$(basename "${manifest_dir}")/phase20.yaml"
ssh c1-control "kubectl -n ${namespace} rollout status deployment/dms-api --timeout=180s"
ssh c1-control "kubectl -n ${namespace} rollout status deployment/dms-planner --timeout=180s"
ssh c1-control "kubectl -n ${namespace} rollout status deployment/dms-dm-worker --timeout=180s"
ssh c1-control "kubectl -n ${namespace} rollout status daemonset/dms-dm-agent --timeout=180s"
ssh c1-control "kubectl -n ${namespace} get deploy,ds,pods,svc -o wide"

printf '== Run Phase 20 Data Management sync/rm verifier ==\n'
cd "${repo_dir}"
"${python_bin}" scripts/phase20_data_management_sync_rm.py | tee "${manifest_dir}/phase20-api-summary.json"

printf '== Verify sync/rm filesystem effects ==\n'
ssh "${c1_node}" "
set -e
sudo -u alice test -f ${destination_dir@Q}/alpha.txt
sudo -u alice test -f ${destination_dir@Q}/beta.txt
sudo -u alice test -f ${destination_dir@Q}/nested/gamma.txt
sudo -u alice grep -q phase20-alpha ${destination_dir@Q}/alpha.txt
test ! -e ${rm_target_dir@Q}
sudo find ${fixture_root@Q} -maxdepth 3 -printf '%M %u %g %p\n'
"

printf '== Volcano and artifact evidence ==\n'
ssh c1-control "kubectl -n ${namespace} get job.batch.volcano.sh,pod -l app.kubernetes.io/name=dms-data-management -o wide || true"
ssh "${c1_node}" "find ${artifact_dir@Q} -maxdepth 4 -type f -print -exec sed -n '1,30p' {} \\;"

if [[ "${DMS_PHASE20_RUN_MPI_SMOKE:-1}" == "1" ]]; then
  printf '== Run standalone Phase 20 multi-node MPI dscan smoke ==\n'
  mpi_namespace="${DMS_PHASE20_MPI_NAMESPACE:-dms-phase20-mpi-${suffix}}"
  mpi_manifest_dir="/shared_directory/$(basename "${manifest_dir}")"
  ssh-keygen -t ed25519 -N '' -f "${manifest_dir}/mpi_id_ed25519" >/dev/null
  cat >"${manifest_dir}/phase20-mpi-workers.yaml" <<EOF
apiVersion: v1
kind: Namespace
metadata:
  name: ${mpi_namespace}
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: mpi-data
  namespace: ${mpi_namespace}
spec:
  accessModes: ["ReadWriteMany"]
  storageClassName: testbed-cephfs
  resources:
    requests:
      storage: 64Mi
---
apiVersion: v1
kind: Secret
metadata:
  name: mpi-ssh
  namespace: ${mpi_namespace}
type: Opaque
stringData:
  id_ed25519: |
$(sed 's/^/    /' "${manifest_dir}/mpi_id_ed25519")
  id_ed25519.pub: |
$(sed 's/^/    /' "${manifest_dir}/mpi_id_ed25519.pub")
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mpi-worker
  namespace: ${mpi_namespace}
spec:
  replicas: 2
  selector:
    matchLabels:
      app: mpi-worker
  template:
    metadata:
      labels:
        app: mpi-worker
    spec:
      affinity:
        podAntiAffinity:
          requiredDuringSchedulingIgnoredDuringExecution:
            - labelSelector:
                matchExpressions:
                  - key: app
                    operator: In
                    values: ["mpi-worker"]
              topologyKey: kubernetes.io/hostname
      containers:
        - name: sshd
          image: ${mpi_k8s_image}
          imagePullPolicy: Always
          securityContext:
            runAsUser: 0
            runAsGroup: 0
            allowPrivilegeEscalation: false
            capabilities:
              add: ["SYS_CHROOT"]
          command:
            - /bin/sh
            - -c
            - |
              set -eu
              mkdir -p /root/.ssh /run/sshd
              cp /ssh/id_ed25519.pub /root/.ssh/authorized_keys
              chmod 700 /root/.ssh
              chmod 600 /root/.ssh/authorized_keys
              exec /usr/sbin/sshd -D -e
          ports:
            - containerPort: 22
              name: ssh
          volumeMounts:
            - name: data
              mountPath: /data
            - name: ssh
              mountPath: /ssh
              readOnly: true
      volumes:
        - name: data
          persistentVolumeClaim:
            claimName: mpi-data
        - name: ssh
          secret:
            secretName: mpi-ssh
            defaultMode: 0400
EOF
  ssh c1-control "kubectl delete namespace ${mpi_namespace} --ignore-not-found=true --wait=true"
  ssh c1-control "kubectl apply -f ${mpi_manifest_dir}/phase20-mpi-workers.yaml"
  ssh c1-control "kubectl -n ${mpi_namespace} rollout status deployment/mpi-worker --timeout=180s"
  worker_nodes="$(
    ssh c1-control "kubectl -n ${mpi_namespace} get pod -l app=mpi-worker -o jsonpath='{range .items[*]}{.spec.nodeName}{\"\\n\"}{end}'" | sort -u | wc -l
  )"
  if [[ "${worker_nodes}" -lt 2 ]]; then
    printf 'MPI workers did not span two nodes; skipping MPI smoke\n'
  else
    worker_ips="$(
      ssh c1-control "kubectl -n ${mpi_namespace} get pod -l app=mpi-worker -o jsonpath='{range .items[*]}{.status.podIP}{\"\\n\"}{end}'"
    )"
    hostfile="$(printf '%s\n' "${worker_ips}" | sed '/^$/d')"
    hostfile_yaml="$(printf '%s\n' "${hostfile}" | sed 's/^/          /')"
    cat >"${manifest_dir}/phase20-mpi-launcher.yaml" <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: mpi-launcher
  namespace: ${mpi_namespace}
spec:
  restartPolicy: Never
  containers:
    - name: launcher
      image: ${mpi_k8s_image}
      imagePullPolicy: Always
      securityContext:
        runAsUser: 0
        runAsGroup: 0
        allowPrivilegeEscalation: false
      command:
        - /bin/sh
        - -c
        - |
          set -eu
          mkdir -p /root/.ssh /data/input /data/artifacts
          cp /ssh/id_ed25519 /root/.ssh/id_ed25519
          cp /ssh/id_ed25519.pub /root/.ssh/id_ed25519.pub
          chmod 700 /root/.ssh
          chmod 600 /root/.ssh/id_ed25519
          cat >/data/artifacts/hostfile <<'HOSTS'
${hostfile_yaml}
          HOSTS
          printf alpha >/data/input/alpha.txt
          printf beta >/data/input/beta.txt
          mkdir -p /data/input/nested
          printf gamma >/data/input/nested/gamma.txt
          launcher_ip=\$(hostname -i | awk '{print \$1}')
          export HYDRA_LAUNCHER_EXTRA_ARGS='-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null'
          mpiexec -launcher ssh -localhost "\${launcher_ip}" -iface eth0 -n 2 -f /data/artifacts/hostfile \
            /opt/mpifileutils/bin/dscan --directory /data/input \
            --output /data/artifacts/dscan-mpi-report.json --print \
            > /data/artifacts/dscan-mpi-stdout.log \
            2> /data/artifacts/dscan-mpi-stderr.log
          test -s /data/artifacts/dscan-mpi-report.json
      volumeMounts:
        - name: data
          mountPath: /data
        - name: ssh
          mountPath: /ssh
          readOnly: true
  volumes:
    - name: data
      persistentVolumeClaim:
        claimName: mpi-data
    - name: ssh
      secret:
        secretName: mpi-ssh
        defaultMode: 0400
EOF
    ssh c1-control "kubectl apply -f ${mpi_manifest_dir}/phase20-mpi-launcher.yaml"
    ssh c1-control "kubectl -n ${mpi_namespace} wait --for=jsonpath='{.status.phase}'=Succeeded pod/mpi-launcher --timeout=180s"
    ssh c1-control "kubectl -n ${mpi_namespace} logs pod/mpi-launcher"
    ssh c1-control "kubectl -n ${mpi_namespace} exec deploy/mpi-worker -- sh -c 'cat /data/artifacts/hostfile; cat /data/artifacts/dscan-mpi-report.json; cat /data/artifacts/dscan-mpi-stderr.log'"
  fi
  if [[ "${DMS_PHASE20_MPI_CLEANUP:-1}" == "1" ]]; then
    ssh c1-control "kubectl delete namespace ${mpi_namespace} --ignore-not-found=true"
  fi
fi

if [[ "${DMS_PHASE20_CLEANUP:-1}" == "1" ]]; then
  printf '== Cleanup Phase 20 namespace ==\n'
  ssh c1-control "kubectl delete namespace ${namespace} --ignore-not-found=true"
fi
