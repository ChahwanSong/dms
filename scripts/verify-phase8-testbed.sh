#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
testbed_dir="${DMS_TESTBED_DIR:-/home/mason/workspace/testbed}"
shared_dir="${testbed_dir}/shared_directory"
suffix="${DMS_PHASE8_DB_SUFFIX:-$(date +%Y%m%d%H%M%S)}"
operational_db="${DMS_PHASE8_OPERATIONAL_DB:-dms_phase8_${suffix}}"
observability_db="${DMS_PHASE8_OBSERVABILITY_DB:-dms_phase8_obs_${suffix}}"
postgres_host="${DMS_PHASE8_POSTGRES_HOST:-192.168.56.11}"
postgres_port="${DMS_PHASE8_POSTGRES_PORT:-30432}"
postgres_user="${DMS_PHASE8_POSTGRES_USER:-appuser}"
python_bin="${DMS_PYTHON:-}"
namespace="${DMS_PHASE8_NAMESPACE:-dms-phase8}"
node_port="${DMS_PHASE8_API_NODE_PORT:-30088}"
auth_token="${DMS_PHASE8_AUTH_TOKEN:-phase8-testbed-token}"
registry_host="${DMS_PHASE8_REGISTRY_HOST:-192.168.56.11:5000}"
k8s_image="${DMS_PHASE8_K8S_IMAGE:-testbed-registry:5000/dms:phase8}"
docker_image="${DMS_PHASE8_DOCKER_IMAGE:-${registry_host}/dms:phase8}"
manifest_dir="${shared_dir}/dms-phase8-${suffix}"

if [[ -z "${python_bin}" ]]; then
  if python3 - <<'PY' >/dev/null 2>&1
import fastapi
import psycopg
PY
  then
    python_bin="python3"
  elif [[ -x /tmp/dms-phase3-venv/bin/python3 ]]; then
    python_bin="/tmp/dms-phase3-venv/bin/python3"
  elif [[ -x /tmp/dms-phase2-venv/bin/python3 ]]; then
    python_bin="/tmp/dms-phase2-venv/bin/python3"
  else
    python_bin="python3"
  fi
fi

printf '== Testbed metadata ==\n'
sed -n '1,80p' "${testbed_dir}/testbed-summary.json"
sed -n '1,80p' "${testbed_dir}/testbed-info.json"

postgres_password="$(
  ssh c1-control "kubectl -n postgresql get secret postgresql-auth -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d"
)"
export POSTGRES_PASSWORD="${postgres_password}"
export PHASE8_OPERATIONAL_DB="${operational_db}"
export PHASE8_OBSERVABILITY_DB="${observability_db}"
export PHASE8_POSTGRES_HOST="${postgres_host}"
export PHASE8_POSTGRES_PORT="${postgres_port}"
export PHASE8_POSTGRES_USER="${postgres_user}"

"${python_bin}" - <<'PY'
import os
import re

import psycopg
from psycopg import sql

db_names = [os.environ["PHASE8_OPERATIONAL_DB"], os.environ["PHASE8_OBSERVABILITY_DB"]]
for db_name in db_names:
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", db_name):
        raise SystemExit(f"unsafe database name: {db_name}")

with psycopg.connect(
    host=os.environ["PHASE8_POSTGRES_HOST"],
    port=int(os.environ["PHASE8_POSTGRES_PORT"]),
    dbname="postgres",
    user=os.environ["PHASE8_POSTGRES_USER"],
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
export DMS_KUBERNETES_MUTATION_MODE="${DMS_KUBERNETES_MUTATION_MODE:-ssh-kubectl}"
export DMS_KUBERNETES_MUTATION_TIMEOUT_SECONDS="${DMS_KUBERNETES_MUTATION_TIMEOUT_SECONDS:-60}"
export DMS_CLUSTER_CONTROL_HOSTS_JSON="${DMS_CLUSTER_CONTROL_HOSTS_JSON:-{\"cluster-a\":\"c1-control\",\"cluster-b\":\"c2-control\"}}"
export DMS_AUTH_SHARED_TOKEN="${auth_token}"

mkdir -p "${manifest_dir}"

if [[ "${DMS_PHASE8_SKIP_IMAGE_BUILD:-0}" != "1" ]]; then
  printf '== Build and push DMS image ==\n'
  docker build -f "${repo_dir}/deploy/Dockerfile" -t "${docker_image}" "${repo_dir}"
  if ! docker push "${docker_image}"; then
    printf 'docker push failed; falling back to docker save + skopeo copy on c1-control\n'
    image_archive="${manifest_dir}/dms-phase8-image.tar"
    docker save "${docker_image}" -o "${image_archive}"
    ssh c1-control \
      "skopeo copy --dest-tls-verify=false docker-archive:/shared_directory/$(basename "${manifest_dir}")/dms-phase8-image.tar docker://192.168.56.11:5000/dms:phase8"
  fi
fi

cat >"${manifest_dir}/api.yaml" <<EOF
apiVersion: v1
kind: Namespace
metadata:
  name: ${namespace}
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
          ports:
            - name: http
              containerPort: 8080
          resources:
            requests:
              cpu: 50m
              memory: 128Mi
            limits:
              cpu: 250m
              memory: 256Mi
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
  local include_dm="$2"
  local file="$3"
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
  DMS_AGENT_REPORT_INTERVAL_SECONDS: "60"
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
          "storage_name": "cephfs-a",
          "backend_type": "cephfs",
          "cluster_name": "cluster-a",
          "storage_class_name": "testbed-cephfs",
          "csi_driver": "rook-ceph.cephfs.csi.ceph.com",
          "mount_paths": [],
          "network_endpoints": []
        },
        {
          "storage_name": "longhorn-b",
          "backend_type": "longhorn",
          "cluster_name": "cluster-b",
          "storage_class_name": "testbed-longhorn",
          "csi_driver": "driver.longhorn.io",
          "mount_paths": [],
          "network_endpoints": []
        },
        {
          "storage_name": "longhorn-static-b",
          "backend_type": "longhorn",
          "cluster_name": "cluster-b",
          "storage_class_name": "longhorn-static",
          "csi_driver": "driver.longhorn.io",
          "mount_paths": [],
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
EOF
  if [[ "${include_dm}" == "1" ]]; then
    cat >>"${file}" <<EOF
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
              value: DM
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
EOF
  fi
}

write_agent_manifest "cluster-a" "1" "${manifest_dir}/agent-cluster-a.yaml"
write_agent_manifest "cluster-b" "0" "${manifest_dir}/agent-cluster-b.yaml"

printf '== Apply DMS API on cluster-a ==\n'
ssh c1-control "kubectl apply -f /shared_directory/$(basename "${manifest_dir}")/api.yaml"
ssh c1-control "kubectl -n ${namespace} rollout status deployment/dms-api --timeout=180s"
ssh c1-control "kubectl -n ${namespace} get pods,svc -o wide"

printf '== Apply DMS Agents ==\n'
ssh c1-control "kubectl apply -f /shared_directory/$(basename "${manifest_dir}")/agent-cluster-a.yaml"
ssh c2-control "kubectl apply -f /shared_directory/$(basename "${manifest_dir}")/agent-cluster-b.yaml"
ssh c1-control "kubectl -n ${namespace} rollout status daemonset/dms-rm-agent --timeout=180s"
ssh c1-control "kubectl -n ${namespace} rollout status daemonset/dms-dm-agent --timeout=180s"
ssh c2-control "kubectl -n ${namespace} rollout status daemonset/dms-rm-agent --timeout=180s"
ssh c1-control "kubectl -n ${namespace} get pods -o wide"
ssh c2-control "kubectl -n ${namespace} get pods -o wide"
ssh c1-control "kubectl -n ${namespace} logs ds/dms-rm-agent --tail=80"
ssh c2-control "kubectl -n ${namespace} logs ds/dms-rm-agent --tail=80"

cd "${repo_dir}"
DMS_AUTH_SHARED_TOKEN="" "${python_bin}" scripts/phase8_agent_daemonset_live.py

if [[ "${DMS_PHASE8_CLEANUP:-1}" == "1" ]]; then
  printf '== Cleanup Phase 8 manifests ==\n'
  ssh c1-control "kubectl delete namespace ${namespace} --ignore-not-found=true"
  ssh c2-control "kubectl delete namespace ${namespace} --ignore-not-found=true"
fi
