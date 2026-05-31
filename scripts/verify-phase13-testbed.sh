#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
testbed_dir="${DMS_TESTBED_DIR:-/home/mason/workspace/testbed}"
shared_dir="${testbed_dir}/shared_directory"
suffix="${DMS_PHASE13_DB_SUFFIX:-$(date +%Y%m%d%H%M%S)}"
operational_db="${DMS_PHASE13_OPERATIONAL_DB:-dms_phase13_${suffix}}"
observability_db="${DMS_PHASE13_OBSERVABILITY_DB:-dms_phase13_obs_${suffix}}"
postgres_host="${DMS_PHASE13_POSTGRES_HOST:-192.168.56.11}"
postgres_port="${DMS_PHASE13_POSTGRES_PORT:-30432}"
postgres_user="${DMS_PHASE13_POSTGRES_USER:-appuser}"
namespace="${DMS_PHASE13_NAMESPACE:-dms-phase13}"
node_port="${DMS_PHASE13_API_NODE_PORT:-30093}"
auth_token="${DMS_PHASE13_AUTH_TOKEN:-phase13-testbed-token}"
registry_host="${DMS_PHASE13_REGISTRY_HOST:-192.168.56.11:5000}"
k8s_image="${DMS_PHASE13_K8S_IMAGE:-testbed-registry:5000/dms:phase13}"
docker_image="${DMS_PHASE13_DOCKER_IMAGE:-${registry_host}/dms:phase13}"
manifest_dir="${shared_dir}/dms-phase13-${suffix}"
c1_mount="${DMS_PHASE13_C1_CEPH_MOUNT_PATH:-/mnt/testbed-cephfs}"
c2_mount="${DMS_PHASE13_C2_CEPH_MOUNT_PATH:-/mnt/testbed-cephfs-c2}"
c1_node="${DMS_PHASE13_C1_NODE:-c1-worker}"
c2_node="${DMS_PHASE13_C2_NODE:-c2-worker}"
ldap_admin_password="${DMS_LDAP_BIND_PASSWORD:-${LDAP_ADMIN_PASSWORD:-testbed-admin}}"
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

printf '== Testbed metadata ==\n'
sed -n '1,80p' "${testbed_dir}/testbed-summary.json"
sed -n '1,80p' "${testbed_dir}/testbed-info.json"

printf '== Host-mounted CephFS checks ==\n'
ssh "${c1_node}" "findmnt ${c1_mount@Q} -o TARGET,SOURCE,FSTYPE && stat -f -c '%T' ${c1_mount@Q}"
ssh "${c2_node}" "findmnt ${c2_mount@Q} -o TARGET,SOURCE,FSTYPE && stat -f -c '%T' ${c2_mount@Q}"
ssh ldap 'systemctl is-active slapd'
ssh "${c1_node}" 'systemctl is-active sssd'
ssh "${c2_node}" 'systemctl is-active sssd'

postgres_password="$(
  ssh c1-control "kubectl -n postgresql get secret postgresql-auth -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d"
)"
export POSTGRES_PASSWORD="${postgres_password}"
export PHASE13_OPERATIONAL_DB="${operational_db}"
export PHASE13_OBSERVABILITY_DB="${observability_db}"
export PHASE13_POSTGRES_HOST="${postgres_host}"
export PHASE13_POSTGRES_PORT="${postgres_port}"
export PHASE13_POSTGRES_USER="${postgres_user}"

"${python_bin}" - <<'PY'
import os
import re

import psycopg
from psycopg import sql

db_names = [os.environ["PHASE13_OPERATIONAL_DB"], os.environ["PHASE13_OBSERVABILITY_DB"]]
for db_name in db_names:
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", db_name):
        raise SystemExit(f"unsafe database name: {db_name}")

with psycopg.connect(
    host=os.environ["PHASE13_POSTGRES_HOST"],
    port=int(os.environ["PHASE13_POSTGRES_PORT"]),
    dbname="postgres",
    user=os.environ["PHASE13_POSTGRES_USER"],
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
export DMS_CONTROL_CLUSTER_NAME="${DMS_CONTROL_CLUSTER_NAME:-cluster-a}"
export DMS_AGENT_REPORT_STALE_SECONDS="${DMS_AGENT_REPORT_STALE_SECONDS:-300}"
export DMS_KUBERNETES_INVENTORY_MODE="${DMS_KUBERNETES_INVENTORY_MODE:-ssh-kubectl}"
export DMS_CLUSTER_CONTROL_HOSTS_JSON="${DMS_CLUSTER_CONTROL_HOSTS_JSON:-{\"cluster-a\":\"c1-control\",\"cluster-b\":\"c2-control\"}}"
export DMS_AUTH_SHARED_TOKEN="${auth_token}"
export DMS_LDAP_URI="${DMS_LDAP_URI:-ldap://192.168.56.31}"
export DMS_LDAP_BASE_DN="${DMS_LDAP_BASE_DN:-dc=testbed,dc=local}"
export DMS_LDAP_BIND_DN="${DMS_LDAP_BIND_DN:-cn=admin,dc=testbed,dc=local}"
export DMS_LDAP_BIND_PASSWORD="${ldap_admin_password}"
export DMS_LDAP_USER_SEARCH_BASE="${DMS_LDAP_USER_SEARCH_BASE:-ou=people,dc=testbed,dc=local}"
export DMS_LDAP_GROUP_SEARCH_BASE="${DMS_LDAP_GROUP_SEARCH_BASE:-ou=groups,dc=testbed,dc=local}"
export DMS_FILESYSTEM_MUTATION_MODE="${DMS_FILESYSTEM_MUTATION_MODE:-ssh-host-exec}"
export DMS_FILESYSTEM_EXEC_TIMEOUT_SECONDS="${DMS_FILESYSTEM_EXEC_TIMEOUT_SECONDS:-60}"
export DMS_FILESYSTEM_EXEC_USE_SUDO="${DMS_FILESYSTEM_EXEC_USE_SUDO:-true}"
export DMS_PHASE13_API_URL="http://192.168.56.11:${node_port}"
export DMS_PHASE13_NAMESPACE="${namespace}"
export DMS_PHASE13_C1_CEPH_MOUNT_PATH="${c1_mount}"
export DMS_PHASE13_C2_CEPH_MOUNT_PATH="${c2_mount}"
export DMS_PHASE13_C1_NODE="${c1_node}"
export DMS_PHASE13_C2_NODE="${c2_node}"
export DMS_PHASE10_C1_CEPH_MOUNT_PATH="${c1_mount}"
export DMS_PHASE10_C2_CEPH_MOUNT_PATH="${c2_mount}"
export DMS_PHASE10_C1_NODE="${c1_node}"
export DMS_PHASE10_C2_NODE="${c2_node}"

mkdir -p "${manifest_dir}"

if [[ "${DMS_PHASE13_SKIP_IMAGE_BUILD:-0}" != "1" ]]; then
  printf '== Build and push DMS image ==\n'
  docker build -f "${repo_dir}/deploy/Dockerfile" -t "${docker_image}" "${repo_dir}"
  if ! docker push "${docker_image}"; then
    printf 'docker push failed; falling back to docker save + skopeo copy on c1-control\n'
    image_archive="${manifest_dir}/dms-phase13-image.tar"
    docker save "${docker_image}" -o "${image_archive}"
    ssh c1-control \
      "skopeo copy --dest-tls-verify=false docker-archive:/shared_directory/$(basename "${manifest_dir}")/dms-phase13-image.tar docker://${docker_image}"
  fi
fi

ssh_key="${DMS_PHASE13_SSH_KEY:-${testbed_dir}/.ssh/testbed_ed25519}"
c1_host="$(ssh -G "${c1_node}" | awk '/^hostname / {print $2; exit}')"
c2_host="$(ssh -G "${c2_node}" | awk '/^hostname / {print $2; exit}')"
c1_user="$(ssh -G "${c1_node}" | awk '/^user / {print $2; exit}')"
c2_user="$(ssh -G "${c2_node}" | awk '/^user / {print $2; exit}')"
c1_control_host="$(ssh -G c1-control | awk '/^hostname / {print $2; exit}')"
c2_control_host="$(ssh -G c2-control | awk '/^hostname / {print $2; exit}')"
c1_control_user="$(ssh -G c1-control | awk '/^user / {print $2; exit}')"
c2_control_user="$(ssh -G c2-control | awk '/^user / {print $2; exit}')"
ssh_key_b64="$(base64 -w0 "${ssh_key}")"

cat >"${manifest_dir}/api.yaml" <<EOF
apiVersion: v1
kind: Namespace
metadata:
  name: ${namespace}
---
apiVersion: v1
kind: Secret
metadata:
  name: dms-ssh
  namespace: ${namespace}
type: Opaque
data:
  id_ed25519: ${ssh_key_b64}
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: dms-ssh-config
  namespace: ${namespace}
data:
  ssh_config: |
    Host c1-control
      HostName ${c1_control_host}
      User ${c1_control_user}
      IdentityFile /etc/dms/ssh/id_ed25519
      StrictHostKeyChecking no
      UserKnownHostsFile /tmp/dms-known-hosts
    Host c2-control
      HostName ${c2_control_host}
      User ${c2_control_user}
      IdentityFile /etc/dms/ssh/id_ed25519
      StrictHostKeyChecking no
      UserKnownHostsFile /tmp/dms-known-hosts
    Host ${c1_node}
      HostName ${c1_host}
      User ${c1_user}
      IdentityFile /etc/dms/ssh/id_ed25519
      StrictHostKeyChecking no
      UserKnownHostsFile /tmp/dms-known-hosts
    Host ${c2_node}
      HostName ${c2_host}
      User ${c2_user}
      IdentityFile /etc/dms/ssh/id_ed25519
      StrictHostKeyChecking no
      UserKnownHostsFile /tmp/dms-known-hosts
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
      securityContext:
        runAsUser: 0
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
            - name: DMS_DEFAULT_ACTOR
              value: api-client
            - name: DMS_CONTROL_CLUSTER_NAME
              value: "${DMS_CONTROL_CLUSTER_NAME}"
            - name: DMS_AGENT_REPORT_STALE_SECONDS
              value: "${DMS_AGENT_REPORT_STALE_SECONDS}"
            - name: DMS_KUBERNETES_INVENTORY_MODE
              value: "ssh-kubectl"
            - name: DMS_CLUSTER_CONTROL_HOSTS_JSON
              value: '${DMS_CLUSTER_CONTROL_HOSTS_JSON}'
          ports:
            - name: http
              containerPort: 8080
          volumeMounts:
            - name: ssh-key
              mountPath: /etc/dms/ssh
              readOnly: true
            - name: ssh-config
              mountPath: /etc/ssh/ssh_config
              subPath: ssh_config
              readOnly: true
          resources:
            requests:
              cpu: 50m
              memory: 128Mi
            limits:
              cpu: 250m
              memory: 256Mi
      volumes:
        - name: ssh-key
          secret:
            secretName: dms-ssh
            defaultMode: 0400
        - name: ssh-config
          configMap:
            name: dms-ssh-config
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
EOF

write_agent_manifest() {
  local cluster_name="$1"
  local node_name="$2"
  local storage_name="$3"
  local mount_path="$4"
  local storage_class_name="$5"
  local csi_driver="$6"
  local file="$7"
  cat >"${file}" <<EOF
apiVersion: v1
kind: Namespace
metadata:
  name: ${namespace}
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: dms-agent
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
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: dms-agent-runtime-config
  namespace: ${namespace}
data:
  DMS_AGENT_API_URL: "http://192.168.56.11:${node_port}"
  DMS_AGENT_CLUSTER_NAME: "${cluster_name}"
  DMS_AGENT_REPORT_INTERVAL_SECONDS: "30"
  DMS_AGENT_REPORT_TIMEOUT_SECONDS: "5"
  DMS_AUTH_SHARED_TOKEN: "${auth_token}"
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
          "storage_name": "${storage_name}",
          "backend_type": "cephfs",
          "cluster_name": "${cluster_name}",
          "storage_class_name": "${storage_class_name}",
          "csi_driver": "${csi_driver}",
          "mount_paths": ["${mount_path}"],
          "network_endpoints": []
        }
      ]
    }
---
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: dms-rm-agent
  namespace: ${namespace}
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: dms-rm-agent
  template:
    metadata:
      labels:
        app.kubernetes.io/name: dms-rm-agent
        app.kubernetes.io/part-of: dms
    spec:
      serviceAccountName: dms-agent
      nodeSelector:
        kubernetes.io/hostname: ${node_name}
      containers:
        - name: agent
          image: ${k8s_image}
          imagePullPolicy: Always
          command: ["dms", "agent-loop"]
          envFrom:
            - configMapRef:
                name: dms-agent-runtime-config
          env:
            - name: DMS_AGENT_WORKER_ROLE
              value: RM
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
            - name: cephfs-host
              mountPath: ${mount_path}
          resources:
            requests:
              cpu: 25m
              memory: 64Mi
            limits:
              cpu: 100m
              memory: 128Mi
      volumes:
        - name: storages
          configMap:
            name: dms-agent-storages
        - name: cephfs-host
          hostPath:
            path: ${mount_path}
            type: Directory
EOF
}

cat >"${manifest_dir}/runtime.yaml" <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: dms-ssh
  namespace: ${namespace}
type: Opaque
data:
  id_ed25519: ${ssh_key_b64}
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: dms-ssh-config
  namespace: ${namespace}
data:
  ssh_config: |
    Host c1-control
      HostName ${c1_control_host}
      User ${c1_control_user}
      IdentityFile /etc/dms/ssh/id_ed25519
      StrictHostKeyChecking no
      UserKnownHostsFile /tmp/dms-known-hosts
    Host c2-control
      HostName ${c2_control_host}
      User ${c2_control_user}
      IdentityFile /etc/dms/ssh/id_ed25519
      StrictHostKeyChecking no
      UserKnownHostsFile /tmp/dms-known-hosts
    Host ${c1_node}
      HostName ${c1_host}
      User ${c1_user}
      IdentityFile /etc/dms/ssh/id_ed25519
      StrictHostKeyChecking no
      UserKnownHostsFile /tmp/dms-known-hosts
    Host ${c2_node}
      HostName ${c2_host}
      User ${c2_user}
      IdentityFile /etc/dms/ssh/id_ed25519
      StrictHostKeyChecking no
      UserKnownHostsFile /tmp/dms-known-hosts
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
      containers:
        - name: planner
          image: ${k8s_image}
          imagePullPolicy: Always
          command: ["dms", "planner", "--loop"]
          args: ["--interval", "2", "--limit", "25"]
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
              memory: 192Mi
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: dms-rm-worker
  namespace: ${namespace}
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: dms-rm-worker
  template:
    metadata:
      labels:
        app.kubernetes.io/name: dms-rm-worker
        app.kubernetes.io/part-of: dms
    spec:
      hostNetwork: true
      dnsPolicy: ClusterFirstWithHostNet
      securityContext:
        runAsUser: 0
      containers:
        - name: rm-worker
          image: ${k8s_image}
          imagePullPolicy: Always
          command: ["dms", "rm-worker"]
          args: ["--worker-id", "\$(POD_NAME)", "--loop", "--interval", "2"]
          env:
            - name: POD_NAME
              valueFrom:
                fieldRef:
                  fieldPath: metadata.name
            - name: DMS_DATABASE_URL
              value: "${DMS_DATABASE_URL}"
            - name: DMS_OBSERVABILITY_DATABASE_URL
              value: "${DMS_OBSERVABILITY_DATABASE_URL}"
            - name: DMS_LDAP_URI
              value: "${DMS_LDAP_URI}"
            - name: DMS_LDAP_BASE_DN
              value: "${DMS_LDAP_BASE_DN}"
            - name: DMS_LDAP_BIND_DN
              value: "${DMS_LDAP_BIND_DN}"
            - name: DMS_LDAP_BIND_PASSWORD
              value: "${DMS_LDAP_BIND_PASSWORD}"
            - name: DMS_LDAP_USER_SEARCH_BASE
              value: "${DMS_LDAP_USER_SEARCH_BASE}"
            - name: DMS_LDAP_GROUP_SEARCH_BASE
              value: "${DMS_LDAP_GROUP_SEARCH_BASE}"
            - name: DMS_FILESYSTEM_MUTATION_MODE
              value: "ssh-host-exec"
            - name: DMS_FILESYSTEM_EXEC_TIMEOUT_SECONDS
              value: "${DMS_FILESYSTEM_EXEC_TIMEOUT_SECONDS}"
            - name: DMS_FILESYSTEM_EXEC_USE_SUDO
              value: "${DMS_FILESYSTEM_EXEC_USE_SUDO}"
          volumeMounts:
            - name: ssh-key
              mountPath: /etc/dms/ssh
              readOnly: true
            - name: ssh-config
              mountPath: /etc/ssh/ssh_config
              subPath: ssh_config
              readOnly: true
          resources:
            requests:
              cpu: 50m
              memory: 128Mi
            limits:
              cpu: 300m
              memory: 256Mi
      volumes:
        - name: ssh-key
          secret:
            secretName: dms-ssh
            defaultMode: 0400
        - name: ssh-config
          configMap:
            name: dms-ssh-config
EOF

write_agent_manifest "cluster-a" "${c1_node}" "cephfs-a" "${c1_mount}" "testbed-cephfs" "rook-ceph.cephfs.csi.ceph.com" "${manifest_dir}/agent-cluster-a.yaml"
write_agent_manifest "cluster-b" "${c2_node}" "cephfs-b" "${c2_mount}" "" "" "${manifest_dir}/agent-cluster-b.yaml"

printf '== Apply DMS API on cluster-a ==\n'
ssh c1-control "kubectl apply -f /shared_directory/$(basename "${manifest_dir}")/api.yaml"
ssh c1-control "kubectl -n ${namespace} rollout status deployment/dms-api --timeout=180s"
ssh c1-control "kubectl -n ${namespace} get pods,svc -o wide"

printf '== Apply DMS RM Agents on Ceph host-mounted worker nodes ==\n'
ssh c1-control "kubectl apply -f /shared_directory/$(basename "${manifest_dir}")/agent-cluster-a.yaml"
ssh c2-control "kubectl apply -f /shared_directory/$(basename "${manifest_dir}")/agent-cluster-b.yaml"
ssh c1-control "kubectl -n ${namespace} rollout status daemonset/dms-rm-agent --timeout=180s"
ssh c2-control "kubectl -n ${namespace} rollout status daemonset/dms-rm-agent --timeout=180s"

printf '== Apply DMS Planner and long-running RM Worker ==\n'
ssh c1-control "kubectl apply -f /shared_directory/$(basename "${manifest_dir}")/runtime.yaml"
ssh c1-control "kubectl -n ${namespace} rollout status deployment/dms-planner --timeout=180s"
ssh c1-control "kubectl -n ${namespace} rollout status deployment/dms-rm-worker --timeout=180s"
ssh c1-control "kubectl -n ${namespace} get deploy,pods,svc -o wide"
ssh c2-control "kubectl -n ${namespace} get ds,pods -o wide"

cd "${repo_dir}"
"${python_bin}" scripts/phase13_long_running_rm_worker.py

if [[ "${DMS_PHASE13_CLEANUP:-1}" == "1" ]]; then
  printf '== Cleanup Phase 13 manifests ==\n'
  ssh c1-control "kubectl delete namespace ${namespace} --ignore-not-found=true"
  ssh c2-control "kubectl delete namespace ${namespace} --ignore-not-found=true"
fi
