#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
testbed_dir="${DMS_TESTBED_DIR:-/home/mason/workspace/testbed}"

command -v openssl >/dev/null || { echo "openssl이 필요합니다" >&2; exit 1; }

export DMS_PHASE15_NAMESPACE="${DMS_PHASE16_NAMESPACE:-dms-phase16}"
export DMS_PHASE15_DB_SUFFIX="${DMS_PHASE16_DB_SUFFIX:-phase16_$(date +%Y%m%d%H%M%S)}"
export DMS_PHASE15_K8S_IMAGE="${DMS_PHASE16_K8S_IMAGE:-testbed-registry:5000/dms:phase16}"
export DMS_PHASE15_DOCKER_IMAGE="${DMS_PHASE16_DOCKER_IMAGE:-192.168.56.11:5000/dms:phase16}"
export DMS_PHASE15_CLEANUP=0
export DMS_PHASE13_CLEANUP=0
export DMS_FILESYSTEM_EXEC_TIMEOUT_SECONDS="${DMS_PHASE16_FILESYSTEM_EXEC_TIMEOUT_SECONDS:-180}"

source "${repo_dir}/scripts/verify-phase15-testbed.sh"

namespace="${DMS_PHASE13_NAMESPACE}"
proxy_node_port="${DMS_PHASE16_MTLS_PROXY_NODE_PORT:-31443}"
cert_dir="${manifest_dir}/phase16-mtls"
mkdir -p "${cert_dir}"

cleanup_phase16() {
  if [[ "${DMS_PHASE16_CLEANUP:-1}" == "1" ]]; then
    ssh c1-control "kubectl delete namespace ${namespace} --ignore-not-found=true"
    ssh c2-control "kubectl delete namespace ${namespace} --ignore-not-found=true"
  fi
}
trap cleanup_phase16 EXIT

printf '== Phase 16: enable mTLS-required API settings ==\n'
export DMS_REQUIRE_MTLS_HEADER=true
export DMS_REQUIRE_MTLS_VERIFIED_HEADER=true
export DMS_MTLS_ACTOR_PREFIX=mtls:
export DMS_DEFAULT_ACTOR=
ssh c1-control \
  "kubectl -n ${namespace} set env deployment/dms-api \
    DMS_REQUIRE_MTLS_HEADER=true \
    DMS_REQUIRE_MTLS_VERIFIED_HEADER=true \
    DMS_MTLS_ACTOR_PREFIX=mtls: \
    DMS_DEFAULT_ACTOR-"
ssh c1-control "kubectl -n ${namespace} rollout status deployment/dms-api --timeout=180s"

printf '== Phase 16: generate short-lived testbed mTLS certificates ==\n'
openssl req -x509 -newkey rsa:2048 -nodes -days 2 \
  -keyout "${cert_dir}/ca.key" \
  -out "${cert_dir}/ca.crt" \
  -subj "/CN=dms-phase16-client-ca/O=testbed" >/dev/null 2>&1
openssl req -x509 -newkey rsa:2048 -nodes -days 2 \
  -keyout "${cert_dir}/bad-ca.key" \
  -out "${cert_dir}/bad-ca.crt" \
  -subj "/CN=dms-phase16-bad-ca/O=testbed" >/dev/null 2>&1

cat >"${cert_dir}/server.cnf" <<EOF
[req]
distinguished_name = dn
req_extensions = v3_req
prompt = no

[dn]
CN = dms-phase16-mtls-proxy
O = testbed

[v3_req]
subjectAltName = @alt_names

[alt_names]
DNS.1 = dms-phase16-mtls-proxy
DNS.2 = localhost
IP.1 = 192.168.56.11
EOF

openssl req -new -nodes -newkey rsa:2048 \
  -keyout "${cert_dir}/server.key" \
  -out "${cert_dir}/server.csr" \
  -config "${cert_dir}/server.cnf" >/dev/null 2>&1
openssl x509 -req -days 2 \
  -in "${cert_dir}/server.csr" \
  -CA "${cert_dir}/ca.crt" \
  -CAkey "${cert_dir}/ca.key" \
  -CAcreateserial \
  -out "${cert_dir}/server.crt" \
  -sha256 \
  -extensions v3_req \
  -extfile "${cert_dir}/server.cnf" >/dev/null 2>&1
openssl req -newkey rsa:2048 -nodes \
  -keyout "${cert_dir}/client.key" \
  -out "${cert_dir}/client.csr" \
  -subj "/CN=phase16-client/O=testbed" >/dev/null 2>&1
openssl x509 -req -days 2 \
  -in "${cert_dir}/client.csr" \
  -CA "${cert_dir}/ca.crt" \
  -CAkey "${cert_dir}/ca.key" \
  -CAcreateserial \
  -out "${cert_dir}/client.crt" \
  -sha256 >/dev/null 2>&1
openssl req -newkey rsa:2048 -nodes \
  -keyout "${cert_dir}/bad-client.key" \
  -out "${cert_dir}/bad-client.csr" \
  -subj "/CN=phase16-bad-client/O=testbed" >/dev/null 2>&1
openssl x509 -req -days 2 \
  -in "${cert_dir}/bad-client.csr" \
  -CA "${cert_dir}/bad-ca.crt" \
  -CAkey "${cert_dir}/bad-ca.key" \
  -CAcreateserial \
  -out "${cert_dir}/bad-client.crt" \
  -sha256 >/dev/null 2>&1

cat >"${cert_dir}/mtls_proxy.py" <<'PY'
from __future__ import annotations

from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
import ssl


DN_KEYS = {
    "commonName": "CN",
    "organizationName": "O",
    "organizationalUnitName": "OU",
    "countryName": "C",
    "localityName": "L",
    "stateOrProvinceName": "ST",
}


def subject_dn(peer: dict) -> str:
    parts: list[str] = []
    for rdn in peer.get("subject", ()):
        for key, value in rdn:
            parts.append(f"{DN_KEYS.get(key, key)}={value}")
    return ",".join(parts)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        self._proxy()

    def do_POST(self) -> None:
        self._proxy()

    def do_PATCH(self) -> None:
        self._proxy()

    def do_PUT(self) -> None:
        self._proxy()

    def do_DELETE(self) -> None:
        self._proxy()

    def _proxy(self) -> None:
        length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(length) if length else None
        peer = self.connection.getpeercert()
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower()
            not in {
                "connection",
                "host",
                "transfer-encoding",
                "x-dms-client-cert-subject",
                "x-dms-client-cert-verify",
                "ssl-client-subject-dn",
                "ssl-client-verify",
            }
        }
        headers["X-DMS-Client-Cert-Subject"] = subject_dn(peer)
        headers["X-DMS-Client-Cert-Verify"] = "SUCCESS"
        connection = HTTPConnection(
            os.environ.get("DMS_PROXY_UPSTREAM_HOST", "dms-api"),
            int(os.environ.get("DMS_PROXY_UPSTREAM_PORT", "80")),
            timeout=10,
        )
        connection.request(self.command, self.path, body=body, headers=headers)
        response = connection.getresponse()
        response_body = response.read()
        self.send_response(response.status)
        for key, value in response.getheaders():
            if key.lower() not in {"connection", "transfer-encoding", "content-length"}:
                self.send_header(key, value)
        self.send_header("content-length", str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)

    def log_message(self, fmt: str, *args) -> None:
        print(fmt % args, flush=True)


server = ThreadingHTTPServer(("0.0.0.0", 8443), Handler)
context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
context.load_cert_chain("/etc/dms/mtls/server.crt", "/etc/dms/mtls/server.key")
context.load_verify_locations("/etc/dms/mtls/ca.crt")
context.verify_mode = ssl.CERT_REQUIRED
server.socket = context.wrap_socket(server.socket, server_side=True)
server.serve_forever()
PY

printf '== Phase 16: deploy testbed mTLS edge proxy ==\n'
ssh c1-control "kubectl -n ${namespace} create secret generic phase16-mtls-proxy-certs \
  --from-file=ca.crt=/shared_directory/$(basename "${manifest_dir}")/phase16-mtls/ca.crt \
  --from-file=server.crt=/shared_directory/$(basename "${manifest_dir}")/phase16-mtls/server.crt \
  --from-file=server.key=/shared_directory/$(basename "${manifest_dir}")/phase16-mtls/server.key \
  --dry-run=client -o yaml | kubectl apply -f -"
ssh c1-control "kubectl -n ${namespace} create configmap phase16-mtls-proxy \
  --from-file=mtls_proxy.py=/shared_directory/$(basename "${manifest_dir}")/phase16-mtls/mtls_proxy.py \
  --dry-run=client -o yaml | kubectl apply -f -"

cat >"${manifest_dir}/phase16-mtls-proxy.yaml" <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: dms-mtls-proxy
  namespace: ${namespace}
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: dms-mtls-proxy
  template:
    metadata:
      labels:
        app.kubernetes.io/name: dms-mtls-proxy
        app.kubernetes.io/part-of: dms
    spec:
      containers:
        - name: proxy
          image: ${k8s_image}
          imagePullPolicy: Always
          command: ["python3", "/etc/dms/proxy/mtls_proxy.py"]
          ports:
            - name: https
              containerPort: 8443
          volumeMounts:
            - name: proxy
              mountPath: /etc/dms/proxy
              readOnly: true
            - name: certs
              mountPath: /etc/dms/mtls
              readOnly: true
          resources:
            requests:
              cpu: 25m
              memory: 64Mi
            limits:
              cpu: 100m
              memory: 128Mi
      volumes:
        - name: proxy
          configMap:
            name: phase16-mtls-proxy
        - name: certs
          secret:
            secretName: phase16-mtls-proxy-certs
---
apiVersion: v1
kind: Service
metadata:
  name: dms-mtls-proxy
  namespace: ${namespace}
spec:
  type: NodePort
  selector:
    app.kubernetes.io/name: dms-mtls-proxy
  ports:
    - name: https
      port: 443
      targetPort: https
      nodePort: ${proxy_node_port}
EOF

ssh c1-control "kubectl apply -f /shared_directory/$(basename "${manifest_dir}")/phase16-mtls-proxy.yaml"
ssh c1-control "kubectl -n ${namespace} rollout status deployment/dms-mtls-proxy --timeout=180s"

printf '== Phase 16: pause planner/RM worker for auth-only endpoint matrix ==\n'
ssh c1-control "kubectl -n ${namespace} scale deployment/dms-planner --replicas=0"
ssh c1-control "kubectl -n ${namespace} scale deployment/dms-rm-worker --replicas=0"
ssh c1-control "kubectl -n ${namespace} rollout status deployment/dms-planner --timeout=180s"
ssh c1-control "kubectl -n ${namespace} rollout status deployment/dms-rm-worker --timeout=180s"

printf '== Phase 16 mTLS authentication checks ==\n'
cd "${repo_dir}"
export DMS_PHASE16_NAMESPACE="${namespace}"
export DMS_PHASE16_K8S_IMAGE="${k8s_image}"
export DMS_PHASE16_MTLS_PROXY_URL="https://192.168.56.11:${proxy_node_port}"
export DMS_PHASE16_DIRECT_API_URL="${DMS_PHASE13_API_URL}"
export DMS_PHASE16_CA_CERT="${cert_dir}/ca.crt"
export DMS_PHASE16_CLIENT_CERT="${cert_dir}/client.crt"
export DMS_PHASE16_CLIENT_KEY="${cert_dir}/client.key"
export DMS_PHASE16_BAD_CLIENT_CERT="${cert_dir}/bad-client.crt"
export DMS_PHASE16_BAD_CLIENT_KEY="${cert_dir}/bad-client.key"
"${python_bin}" scripts/phase16_mtls_auth.py
