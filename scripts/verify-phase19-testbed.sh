#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
testbed_dir="${DMS_TESTBED_DIR:-/home/mason/workspace/testbed}"
shared_dir="${testbed_dir}/shared_directory"
suffix="${DMS_PHASE19_DB_SUFFIX:-$(date +%Y%m%d%H%M%S)}"
operational_db="${DMS_PHASE19_OPERATIONAL_DB:-dms_phase19_${suffix}}"
observability_db="${DMS_PHASE19_OBSERVABILITY_DB:-dms_phase19_obs_${suffix}}"
postgres_host="${DMS_PHASE19_POSTGRES_HOST:-192.168.56.11}"
postgres_port="${DMS_PHASE19_POSTGRES_PORT:-30432}"
postgres_user="${DMS_PHASE19_POSTGRES_USER:-appuser}"
namespace="${DMS_PHASE19_NAMESPACE:-dms-phase19}"
node_port="${DMS_PHASE19_API_NODE_PORT:-30099}"
auth_token="${DMS_PHASE19_AUTH_TOKEN:-phase19-testbed-token}"
registry_host="${DMS_PHASE19_REGISTRY_HOST:-192.168.56.11:5000}"
k8s_image="${DMS_PHASE19_K8S_IMAGE:-testbed-registry:5000/dms:phase19}"
docker_image="${DMS_PHASE19_DOCKER_IMAGE:-${registry_host}/dms:phase19}"
mpifileutils_ref="${DMS_PHASE19_MPIFILEUTILS_REF:-e3bfee10970bb4e24204d28689e3337e9741cca4}"
dm_job_image="${DMS_PHASE19_DM_JOB_IMAGE:-${k8s_image}}"
dm_job_image_ref="${DMS_PHASE19_DM_JOB_IMAGE_REF:-chahwansong/mpifileutils@${mpifileutils_ref};testbed-dscan-fixture}"
manifest_dir="${shared_dir}/dms-phase19-${suffix}"
c1_node="${DMS_PHASE19_C1_NODE:-c1-worker}"
ceph_mount="${DMS_PHASE19_CEPH_MOUNT:-/mnt/testbed-cephfs}"
fixture_root="${ceph_mount}/dms-phase19-${suffix}"
artifact_dir="${ceph_mount}/dms-phase19-artifacts-${suffix}"
target_rel="dms-phase19-${suffix}/input"
input_dir="${ceph_mount}/${target_rel}"
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
sed -n '1,80p' "${testbed_dir}/testbed-summary.json"

printf '== Testbed readiness ==\n'
ssh c1-control 'kubectl get nodes -o wide'
ssh c1-control 'kubectl -n volcano-system get deploy,pod -o wide'
ssh "${c1_node}" "findmnt -rn ${ceph_mount@Q} -o FSTYPE,TARGET,SOURCE && id alice && getent group developers"

printf '== Prepare Phase 19 CephFS fixture ==\n'
ssh "${c1_node}" "
set -e
sudo rm -rf ${fixture_root@Q} ${artifact_dir@Q}
sudo mkdir -p ${input_dir@Q} ${artifact_dir@Q}
sudo chown -R alice:developers ${fixture_root@Q}
sudo chmod 0750 ${fixture_root@Q} ${input_dir@Q}
sudo chown root:root ${artifact_dir@Q}
sudo chmod 0777 ${artifact_dir@Q}
sudo -u alice sh -c 'printf phase19-alpha > ${input_dir@Q}/alpha.txt'
sudo -u alice sh -c 'printf phase19-beta > ${input_dir@Q}/beta.txt'
sudo -u alice mkdir -p ${input_dir@Q}/nested
sudo -u alice sh -c 'printf nested > ${input_dir@Q}/nested/gamma.txt'
sudo find ${fixture_root@Q} -maxdepth 3 -printf '%M %u %g %p\n'
sudo find ${artifact_dir@Q} -maxdepth 2 -printf '%M %u %g %p\n'
"

postgres_password="$(
  ssh c1-control "kubectl -n postgresql get secret postgresql-auth -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d"
)"
export POSTGRES_PASSWORD="${postgres_password}"
export PHASE19_OPERATIONAL_DB="${operational_db}"
export PHASE19_OBSERVABILITY_DB="${observability_db}"
export PHASE19_POSTGRES_HOST="${postgres_host}"
export PHASE19_POSTGRES_PORT="${postgres_port}"
export PHASE19_POSTGRES_USER="${postgres_user}"

"${python_bin}" - <<'PY'
import os
import re

import psycopg
from psycopg import sql

db_names = [os.environ["PHASE19_OPERATIONAL_DB"], os.environ["PHASE19_OBSERVABILITY_DB"]]
for db_name in db_names:
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", db_name):
        raise SystemExit(f"unsafe database name: {db_name}")

with psycopg.connect(
    host=os.environ["PHASE19_POSTGRES_HOST"],
    port=int(os.environ["PHASE19_POSTGRES_PORT"]),
    dbname="postgres",
    user=os.environ["PHASE19_POSTGRES_USER"],
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
export DMS_PHASE19_API_URL="http://192.168.56.11:${node_port}"
export DMS_PHASE19_TARGET_PATH="${target_rel}"
export DMS_PHASE19_CEPH_MOUNT="${ceph_mount}"
export DMS_PHASE19_AUTH_TOKEN="${auth_token}"
export DMS_PHASE19_EXPECTED_MIN_FILES="3"

mkdir -p "${manifest_dir}"

cat >"${manifest_dir}/Dockerfile.phase19" <<'EOF'
FROM python:3.11-slim

ARG KUBECTL_VERSION=v1.34.0

ENV PYTHONUNBUFFERED=1 \
    HOME=/home/dms

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        openssh-client \
    && curl -fsSLo /usr/local/bin/kubectl \
        "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
    && chmod 0755 /usr/local/bin/kubectl \
    && addgroup --system --gid 65532 dms \
    && adduser --system --uid 65532 --ingroup dms --home /home/dms dms \
    && groupadd --gid 10000 developers \
    && useradd --uid 10000 --gid 10000 --no-create-home --shell /usr/sbin/nologin alice \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml /app/
COPY src /app/src
COPY scripts/phase19_dscan_fixture.py /usr/local/bin/dscan

RUN pip install --no-cache-dir '.[postgres,ldap,kubernetes]' \
    && chmod 0755 /usr/local/bin/dscan

USER 65532:65532
ENTRYPOINT ["dms"]
EOF

if [[ "${DMS_PHASE19_SKIP_IMAGE_BUILD:-0}" != "1" ]]; then
  printf '== Build and push Phase 19 DMS/dscan image ==\n'
  docker build -f "${manifest_dir}/Dockerfile.phase19" -t "${docker_image}" "${repo_dir}"
  if ! docker push "${docker_image}"; then
    printf 'docker push failed; falling back to docker save + skopeo copy on c1-control\n'
    image_archive="${manifest_dir}/dms-phase19-image.tar"
    docker save "${docker_image}" -o "${image_archive}"
    ssh c1-control \
      "skopeo copy --dest-tls-verify=false docker-archive:/shared_directory/$(basename "${manifest_dir}")/dms-phase19-image.tar docker://${docker_image}"
  fi
fi

cat >"${manifest_dir}/phase19.yaml" <<EOF
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
              value: "dscan"
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

if [[ "${DMS_PHASE19_RESET_NAMESPACE:-1}" == "1" ]]; then
  printf '== Reset Phase 19 namespace ==\n'
  ssh c1-control "kubectl delete namespace ${namespace} --ignore-not-found=true --wait=true"
fi

printf '== Apply Phase 19 DMS manifests ==\n'
ssh c1-control "kubectl apply -f /shared_directory/$(basename "${manifest_dir}")/phase19.yaml"
ssh c1-control "kubectl -n ${namespace} rollout status deployment/dms-api --timeout=180s"
ssh c1-control "kubectl -n ${namespace} rollout status deployment/dms-planner --timeout=180s"
ssh c1-control "kubectl -n ${namespace} rollout status deployment/dms-dm-worker --timeout=180s"
ssh c1-control "kubectl -n ${namespace} rollout status daemonset/dms-dm-agent --timeout=180s"
ssh c1-control "kubectl -n ${namespace} get deploy,ds,pods,svc -o wide"

printf '== Run Phase 19 Data Management scan verifier ==\n'
cd "${repo_dir}"
"${python_bin}" scripts/phase19_data_management_scan.py

printf '== Volcano and artifact evidence ==\n'
ssh c1-control "kubectl -n ${namespace} get job.batch.volcano.sh,pod -l app.kubernetes.io/name=dms-data-management -o wide || true"
ssh "${c1_node}" "find ${artifact_dir@Q} -maxdepth 3 -type f -print -exec sed -n '1,40p' {} \\;"

if [[ "${DMS_PHASE19_CLEANUP:-1}" == "1" ]]; then
  printf '== Cleanup Phase 19 namespace ==\n'
  ssh c1-control "kubectl delete namespace ${namespace} --ignore-not-found=true"
fi
