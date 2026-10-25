"""에이전트의 외부 k8s API 조회(dms-k8s 설계 §4.3, agent/k8s_fetch.py). httpx.MockTransport 로 continue·410·401·403·
too_large·deadline·투영을, 로컬 TLS 서버로 CA·SNI 양성/음성을 고정한다."""
import datetime
import json
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from dms.agent.k8s_fetch import (LISTS, fetch_inventory, project_pv, project_pvc, project_role_binding,
                                 project_storage_class)
from dms.k8s_sync import normalize_snapshot

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "k8s"
TOKEN = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ4In0.c2ln-SECRET"
PATHS = {path: key for key, _phase, path, _opt in LISTS}
_FILES = {"storage_classes": "synthetic-storageclasses.json", "pvcs": "synthetic-pvcs.json",
          "pvs": "synthetic-pvs.json", "role_bindings": "synthetic-rolebindings.json"}


def _fixture(key):
    return json.loads((FIXTURES / _FILES[key]).read_text())


def _run(**kw):
    run = {"run_id": "r1", "cluster": "c1", "api_server": "https://k8s.example:6443", "tls_server_name": None,
           "ca_pem": None, "insecure_skip_tls_verify": False, "token": TOKEN, "page_limit": 2,
           "deadline_seconds": 120, "max_bytes": 33554432}
    run.update(kw)
    return run


class Api:
    """가짜 API 서버(MockTransport 핸들러). 목록은 page_limit 단위로 continue 를 붙여 나눠 준다."""

    def __init__(self):
        self.status = {}            # path → 고정 상태 코드
        self.expire_continue = {}   # path → 남은 410 횟수(continue 가 있는 요청에)
        self.calls = []

    def __call__(self, request: httpx.Request):
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        path = request.url.path
        self.calls.append((path, dict(request.url.params)))
        if path == "/version":
            return httpx.Response(200, json={"gitVersion": "v1.34.6", "major": "1", "minor": "34"})
        if path == "/api/v1/namespaces/kube-system":
            return httpx.Response(200, json={"metadata": {"name": "kube-system", "uid": "ks-uid-1"}})
        if path in self.status:
            code = self.status[path]
            return httpx.Response(code, json={"kind": "Status", "code": code,
                                              "message": f"{PATHS.get(path, path)} is forbidden: User x cannot list"})
        key = PATHS.get(path)
        if key is None:
            return httpx.Response(404, json={"kind": "Status", "code": 404})
        items = _fixture(key)["items"]
        limit = int(request.url.params.get("limit"))
        cont = request.url.params.get("continue")
        if cont and self.expire_continue.get(path, 0) > 0:
            self.expire_continue[path] -= 1
            return httpx.Response(410, json={"kind": "Status", "code": 410, "reason": "Expired",
                                             "message": "The provided continue parameter is too old"})
        start = int(cont) if cont else 0
        page = items[start:start + limit]
        nxt = start + limit
        meta = {"resourceVersion": "1"}
        if nxt < len(items):
            meta["continue"] = str(nxt)
        return httpx.Response(200, json={"kind": "List", "metadata": meta, "items": page})


def _fetch(api, **kw):
    return fetch_inventory(_run(**kw), transport=httpx.MockTransport(api))


def test_full_fetch_paginates_and_projects_every_list():
    api = Api()
    out = _fetch(api)
    assert out["ok"] is True, out
    assert out["server_version"] == "v1.34.6" and out["fingerprint"] == "ks-uid-1" and out["warnings"] == []
    data = out["data"]
    assert [p["name"] for p in data["pvcs"]] == [i["metadata"]["name"] for i in _fixture("pvcs")["items"]]
    assert len(data["role_bindings"]) == 9 and len(data["pvs"]) == 7 and len(data["storage_classes"]) == 3
    # continue 를 따라 끝까지(page_limit=2 → PVC 7개 = 4쪽)
    pvc_calls = [params for path, params in api.calls if path == "/api/v1/persistentvolumeclaims"]
    assert len(pvc_calls) == 4 and "continue" not in pvc_calls[0] and pvc_calls[1]["continue"] == "2"
    assert all(params["limit"] == "2" for params in pvc_calls)
    # 순서: /version → kube-system → storageclasses → pvcs → pvs → rolebindings
    order = []
    for path, _ in api.calls:
        if path not in order:
            order.append(path)
    assert order == ["/version", "/api/v1/namespaces/kube-system", "/apis/storage.k8s.io/v1/storageclasses",
                     "/api/v1/persistentvolumeclaims", "/api/v1/persistentvolumes",
                     "/apis/rbac.authorization.k8s.io/v1/rolebindings"]
    # 투영 결과는 서버 검증(normalize_snapshot)을 그대로 통과한다
    assert normalize_snapshot(data)["pvcs"] == data["pvcs"]


def test_projection_shapes():
    pvc = project_pvc(_fixture("pvcs")["items"][0])
    assert pvc == {"namespace": "team-a", "name": "models", "uid": "pvc-uid-a-models", "storage_class": "tb-gpfs",
                   "volume_name": "pvc-5c25f53f-1111-4aaa-8bbb-000000000001", "phase": "Bound", "capacity": "1Gi",
                   "access_modes": ["ReadWriteMany"], "created_at": "2026-10-25T10:00:00Z"}
    pending = project_pvc(_fixture("pvcs")["items"][-1])
    assert pending["volume_name"] is None and pending["capacity"] is None and pending["phase"] == "Pending"
    assert pending["access_modes"] == ["ReadWriteOnce"]                 # status 가 비면 spec 값
    pv = project_pv(_fixture("pvs")["items"][1])
    assert pv["driver"] == "nfs.csi.k8s.io" and pv["volume_attributes"]["subDir"] == "team-a/datasets"
    assert (pv["claim_namespace"], pv["claim_name"], pv["storage_class"]) == ("team-a", "datasets", "tb-nfs")
    assert pv["capacity"] == "2Gi" and pv["reclaim_policy"] == "Retain" and pv["volume_mode"] == "Filesystem"
    sc = project_storage_class(_fixture("storage_classes")["items"][0])
    assert sc == {"name": "tb-nfs", "provisioner": "nfs.csi.k8s.io",
                  "parameters": {"server": "10.10.10.1", "share": "/data/k8s_pv/nfs",
                                 "subDir": "${pvc.metadata.namespace}/${pvc.metadata.name}", "onDelete": "retain"},
                  "reclaim_policy": "Retain", "volume_binding_mode": "Immediate"}
    rb = project_role_binding(_fixture("role_bindings")["items"][6])
    assert rb == {"namespace": "team-a", "name": "default-editor", "role_kind": "ClusterRole", "role_name": "kubeflow-edit",
                  "subjects": [{"kind": "ServiceAccount", "name": "default-editor", "namespace": "team-a"}]}
    # 어떤 투영에도 annotations·uid(바인딩) 같은 불필요한 칸이 실리지 않는다(에이전트 메모리·전송량)
    assert "annotations" not in json.dumps(project_role_binding(_fixture("role_bindings")["items"][4]))


def test_expired_continue_restarts_the_list_once():
    api = Api()
    api.expire_continue["/api/v1/persistentvolumeclaims"] = 1
    out = _fetch(api)
    assert out["ok"] is True and len(out["data"]["pvcs"]) == 7            # 다시 받은 목록에 중복이 없다
    starts = [p for path, p in api.calls if path == "/api/v1/persistentvolumeclaims" and "continue" not in p]
    assert len(starts) == 2


def test_second_410_is_list_expired():
    api = Api()
    api.expire_continue["/apis/rbac.authorization.k8s.io/v1/rolebindings"] = 2
    out = _fetch(api)
    assert out["ok"] is False and out["reason"] == "k8s_sync_list_expired"
    assert out["phase"] == "rolebindings" and out["http_status"] == 410 and "data" not in out


def test_401_fails_the_run_with_the_phase():
    api = Api()
    api.status["/apis/storage.k8s.io/v1/storageclasses"] = 401
    out = _fetch(api)
    assert (out["ok"], out["reason"], out["phase"], out["http_status"]) == (
        False, "k8s_sync_unauthorized", "storageclasses", 401)
    assert TOKEN not in json.dumps(out)


@pytest.mark.parametrize("path,phase", [("/api/v1/persistentvolumeclaims", "persistentvolumeclaims"),
                                        ("/apis/rbac.authorization.k8s.io/v1/rolebindings", "rolebindings")])
def test_403_on_a_required_list_fails(path, phase):
    api = Api()
    api.status[path] = 403
    out = _fetch(api)
    assert (out["ok"], out["reason"], out["phase"], out["http_status"]) == (False, "k8s_sync_forbidden", phase, 403)
    assert "is forbidden" in out["error"]                                   # 어느 권한이 빠졌는지 보인다


def test_403_on_optional_lists_is_a_warning_and_null():
    api = Api()
    api.status["/api/v1/persistentvolumes"] = 403
    api.status["/apis/storage.k8s.io/v1/storageclasses"] = 403
    out = _fetch(api)
    assert out["ok"] is True
    assert out["data"]["pvs"] is None and out["data"]["storage_classes"] is None
    assert len(out["data"]["pvcs"]) == 7 and len(out["data"]["role_bindings"]) == 9
    assert {w["phase"] for w in out["warnings"]} == {"persistentvolumes", "storageclasses"}
    assert all(w["reason"] == "k8s_sync_forbidden" for w in out["warnings"])


def test_too_large_stops_the_run():
    out = _fetch(Api(), max_bytes=1500)
    assert out["ok"] is False and out["reason"] == "k8s_sync_too_large" and out["phase"]


def test_page_cap_is_too_large(monkeypatch):
    import dms.agent.k8s_fetch as kf
    monkeypatch.setattr(kf, "MAX_PAGES", 2)
    out = _fetch(Api(), page_limit=1)
    assert out["ok"] is False and out["reason"] == "k8s_sync_too_large" and out["phase"] == "storageclasses"


def test_deadline_self_abort_reports_timeout_with_phase():
    ticks = iter([0.0] + [float(i) for i in range(1, 400)])

    def clock():
        return next(ticks) * 10          # 요청마다 10초씩 흐른다

    out = fetch_inventory(_run(deadline_seconds=55), transport=httpx.MockTransport(Api()), clock=clock)
    assert out["ok"] is False and out["reason"] == "k8s_sync_timeout" and out["phase"]
    assert out["duration_ms"] >= 55000


def test_redirects_are_not_followed_and_non_json_is_bad_response():
    def handler(request):
        if request.url.path == "/api/v1/persistentvolumeclaims":
            return httpx.Response(302, headers={"location": "https://evil.example/steal"})
        if request.url.path == "/apis/storage.k8s.io/v1/storageclasses":
            return httpx.Response(200, text="<html>proxy login</html>")
        return httpx.Response(200, json={"items": []})
    out = fetch_inventory(_run(), transport=httpx.MockTransport(handler))
    assert out["ok"] is False and out["reason"] == "k8s_sync_bad_response" and out["phase"] == "storageclasses"


def test_status_message_never_echoes_the_token():
    def handler(request):
        return httpx.Response(500, json={"message": f"echo {request.headers['authorization']}"})
    out = fetch_inventory(_run(), transport=httpx.MockTransport(handler))
    assert out["ok"] is False and out["reason"] == "k8s_sync_bad_response"
    assert TOKEN not in json.dumps(out) and "***" in out["error"]


def test_proxy_env_is_ignored(monkeypatch):
    # trust_env=False: 사이트의 HTTPS_PROXY 가 있어도 API 서버 호출이 프록시로 새지 않는다(MockTransport 가 그대로 받는다)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:9")
    assert _fetch(Api())["ok"] is True


def test_connection_errors_are_classified():
    def refuse(request):
        raise httpx.ConnectError("[Errno 111] Connection refused", request=request)
    out = fetch_inventory(_run(), transport=httpx.MockTransport(refuse))
    assert out["ok"] is False and out["reason"] == "k8s_sync_unreachable" and out["phase"] == "storageclasses"

    def dns(request):
        raise httpx.ConnectError("[Errno -2] Name or service not known", request=request)
    assert fetch_inventory(_run(), transport=httpx.MockTransport(dns))["reason"] == "k8s_sync_dns_failed"

    def slow(request):
        raise httpx.ReadTimeout("timed out", request=request)
    assert fetch_inventory(_run(), transport=httpx.MockTransport(slow))["reason"] == "k8s_sync_timeout"


def test_invalid_ca_is_a_tls_failure():
    out = fetch_inventory(_run(ca_pem="-----BEGIN CERTIFICATE-----\nnope\n-----END CERTIFICATE-----\n"),
                          transport=httpx.MockTransport(Api()))
    assert out["ok"] is False and out["reason"] == "k8s_sync_tls_failed"


# --- 로컬 TLS 서버: CA·SNI 양성/음성 -------------------------------------------------------------

def _make_ca_and_server(server_dns):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    now = datetime.datetime.now(datetime.timezone.utc)

    def name(cn):
        return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])

    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca = (x509.CertificateBuilder().subject_name(name("test-ca")).issuer_name(name("test-ca"))
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=30))
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
          .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                       content_commitment=False, key_encipherment=False, data_encipherment=False,
                                       key_agreement=False, encipher_only=False, decipher_only=False), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .sign(ca_key, hashes.SHA256()))
    key = ec.generate_private_key(ec.SECP256R1())
    cert = (x509.CertificateBuilder().subject_name(name(server_dns)).issuer_name(ca.subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=30))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(server_dns)]), critical=False)
            .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    pem = lambda c: c.public_bytes(serialization.Encoding.PEM).decode()  # noqa: E731
    key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
    return pem(ca), pem(cert), key_pem


@pytest.fixture(scope="module")
def tls_api(tmp_path_factory):
    ca_pem, cert_pem, key_pem = _make_ca_and_server("dms-k8s.local")
    other_ca, _, _ = _make_ca_and_server("other.local")
    d = tmp_path_factory.mktemp("tls")
    (d / "cert.pem").write_text(cert_pem)
    (d / "key.pem").write_text(key_pem)
    seen_sni = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if self.headers.get("authorization") != f"Bearer {TOKEN}":
                body, code = {"kind": "Status", "code": 401}, 401
            elif path == "/version":
                body, code = {"gitVersion": "v1.34.6"}, 200
            elif path in PATHS:
                body, code = {"items": _fixture(PATHS[path])["items"], "metadata": {}}, 200
            else:
                body, code = {"metadata": {"uid": "ks"}}, 200
            raw = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(d / "cert.pem"), str(d / "key.pem"))
    ctx.sni_callback = lambda sock, server_name, _ctx: seen_sni.append(server_name)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield {"url": f"https://127.0.0.1:{server.server_address[1]}", "ca": ca_pem, "other_ca": other_ca,
           "sni": seen_sni}
    server.shutdown()
    server.server_close()


def test_tls_ca_and_sni_positive(tls_api):
    out = fetch_inventory(_run(api_server=tls_api["url"], ca_pem=tls_api["ca"], tls_server_name="dms-k8s.local",
                               page_limit=500, deadline_seconds=20))
    assert out["ok"] is True, out
    assert len(out["data"]["pvcs"]) == 7 and out["server_version"] == "v1.34.6"
    assert "dms-k8s.local" in tls_api["sni"]                               # IP 로 접속하면서 SNI 는 그 이름


def test_tls_without_server_name_fails_hostname_check(tls_api):
    # 인증서 SAN 은 dms-k8s.local 뿐 -- IP 로 접속하면서 TLS 서버 이름을 안 주면 검증 실패(SAN 불일치 실증과 같은 모양)
    out = fetch_inventory(_run(api_server=tls_api["url"], ca_pem=tls_api["ca"], deadline_seconds=20))
    assert out["ok"] is False and out["reason"] == "k8s_sync_tls_failed", out
    out = fetch_inventory(_run(api_server=tls_api["url"], ca_pem=tls_api["ca"], tls_server_name="wrong.local",
                               deadline_seconds=20))
    assert out["reason"] == "k8s_sync_tls_failed"


def test_tls_wrong_ca_fails_and_insecure_skips_verification(tls_api):
    out = fetch_inventory(_run(api_server=tls_api["url"], ca_pem=tls_api["other_ca"], tls_server_name="dms-k8s.local",
                               deadline_seconds=20))
    assert out["ok"] is False and out["reason"] == "k8s_sync_tls_failed"
    out = fetch_inventory(_run(api_server=tls_api["url"], ca_pem=None, deadline_seconds=20))   # 시스템 CA 로는 자체 서명 실패
    assert out["reason"] == "k8s_sync_tls_failed"
    out = fetch_inventory(_run(api_server=tls_api["url"], insecure_skip_tls_verify=True, page_limit=500,
                               deadline_seconds=20))
    assert out["ok"] is True
    out = fetch_inventory(_run(api_server=tls_api["url"], insecure_skip_tls_verify=True, token="wrong",
                               deadline_seconds=20))
    assert out["ok"] is False and out["reason"] == "k8s_sync_unauthorized"
