"""외부 k8s API 서버에서 인벤토리(RoleBinding·PVC·PV·StorageClass)를 가져온다(dms-k8s 설계 D6 §4, 2026-10-25).

지정 노드(primary·secondary)의 에이전트 워커 스레드가 claim 으로 받은 run 하나를 이 함수로 처리하고 결과를 API 로 올린다.
DB 를 모른다. kubernetes 파이썬 클라이언트를 쓰지 않는 이유: load_incluster_config() 가 프로세스 전역 기본 설정을 바꾸고
(에이전트의 노드 프로브가 그것을 쓴다), 클러스터마다 다른 CA·토큰·SNI 를 메모리 PEM 으로 다룰 수 없다.

연결(설계 §4.3):
  - httpx Client(trust_env=False -- 사이트의 HTTP(S)_PROXY env 로 API 서버 호출이 새지 않게, follow_redirects=False,
    Timeout(30, connect=5)). 요청마다 남은 기한에 맞춰 타임아웃을 줄인다.
  - TLS: ca_pem 이 있으면 그 CA 만 신뢰(ssl.create_default_context(cadata=)), insecure 면 검증 안 함, 둘 다 없으면 이미지
    기본 신뢰 저장소. tls_server_name 이 있으면 요청 extensions 의 sni_hostname 으로 -- IP 로 접속하면서 인증서는 그 이름으로
    검증한다(파드 DNS 가 노드 /etc/hosts 를 못 보는 airgap 사이트).
  - 순서: /version(실패해도 run 실패 아님) → kube-system namespace uid(같은 클러스터 중복 등록 경고용 fingerprint, 실패 무시)
    → storageclasses → pvcs → pvs → rolebindings. ?limit=<page_limit>&continue= 로 끝까지, 410(continue 토큰 만료)이면 그 목록을
    처음부터 1회 다시, 쪽 수 상한 400.
  - 쪽마다 필요한 칸만 투영(§4.1)해 쌓고, 투영 크기 합이 max_bytes 를 넘으면 k8s_sync_too_large(에이전트 메모리 보호).
  - deadline_seconds 가 되면 스스로 멈추고 k8s_sync_timeout + phase 로 실패를 보고한다(서버 기한 = 이것 + 업로드 유예).
  - 오류 분류는 k8s_sync.classify_exception/classify_http_status 하나. 메시지에 토큰·헤더를 싣지 않는다.
  - 필수 목록(pvcs·rolebindings) 실패 = run 실패(서버 스냅숏 무변경). 선택 목록(pvs·storageclasses)의 403 은 그 목록만 null +
    warnings(서버가 이전 값을 유지한다).
"""
import json
import ssl
import time

import httpx

from ..k8s_sync import classify_exception, classify_http_status

MAX_PAGES = 400
_VERSION_TIMEOUT = 5.0
_STATUS_MESSAGE_MAX = 300

# (data 키, 화면 phase 이름, 경로, 선택 목록?)
LISTS = (
    ("storage_classes", "storageclasses", "/apis/storage.k8s.io/v1/storageclasses", True),
    ("pvcs", "persistentvolumeclaims", "/api/v1/persistentvolumeclaims", False),
    ("pvs", "persistentvolumes", "/api/v1/persistentvolumes", True),
    ("role_bindings", "rolebindings", "/apis/rbac.authorization.k8s.io/v1/rolebindings", False),
)


class FetchError(Exception):
    def __init__(self, reason, *, phase=None, http_status=None, error=None):
        self.reason = reason
        self.phase = phase
        self.http_status = http_status
        self.error = error
        super().__init__(reason)


class _Restart(Exception):
    pass


# --- 투영(§4.1) ---------------------------------------------------------------------------------

def _d(value) -> dict:
    return value if isinstance(value, dict) else {}


def _str(value):
    if value is None:
        return None
    return value if isinstance(value, str) else str(value)


def _strs(value) -> list:
    return [_str(v) for v in value if v is not None] if isinstance(value, list) else []


def _str_map(value) -> dict:
    return {str(k): ("" if v is None else _str(v)) for k, v in value.items()} if isinstance(value, dict) else {}


def project_storage_class(raw: dict) -> dict:
    md = _d(raw.get("metadata"))
    return {"name": _str(md.get("name")), "provisioner": _str(raw.get("provisioner")),
            "parameters": _str_map(raw.get("parameters")),
            "reclaim_policy": _str(raw.get("reclaimPolicy")),
            "volume_binding_mode": _str(raw.get("volumeBindingMode"))}


def project_pvc(raw: dict) -> dict:
    md, spec, st = _d(raw.get("metadata")), _d(raw.get("spec")), _d(raw.get("status"))
    # kubectl get pvc 의 CAPACITY·ACCESS MODES 는 status 값이다(Pending 이면 비어 있다 -- 요청량을 지어내지 않는다).
    modes = st.get("accessModes") if isinstance(st.get("accessModes"), list) else spec.get("accessModes")
    return {"namespace": _str(md.get("namespace")), "name": _str(md.get("name")), "uid": _str(md.get("uid")),
            "storage_class": _str(spec.get("storageClassName")),
            "volume_name": _str(spec.get("volumeName")) or None,
            "phase": _str(st.get("phase")),
            "capacity": _str(_d(st.get("capacity")).get("storage")),
            "access_modes": _strs(modes),
            "created_at": _str(md.get("creationTimestamp"))}


def project_pv(raw: dict) -> dict:
    md, spec, st = _d(raw.get("metadata")), _d(raw.get("spec")), _d(raw.get("status"))
    csi, nfs, claim = _d(spec.get("csi")), _d(spec.get("nfs")), _d(spec.get("claimRef"))
    return {"name": _str(md.get("name")), "driver": _str(csi.get("driver")),
            "volume_handle": _str(csi.get("volumeHandle")),
            "volume_attributes": _str_map(csi.get("volumeAttributes")),
            "nfs_server": _str(nfs.get("server")), "nfs_path": _str(nfs.get("path")),
            "storage_class": _str(spec.get("storageClassName")),
            "claim_namespace": _str(claim.get("namespace")), "claim_name": _str(claim.get("name")),
            "capacity": _str(_d(spec.get("capacity")).get("storage")),
            "access_modes": _strs(spec.get("accessModes")),
            "phase": _str(st.get("phase")),
            "reclaim_policy": _str(spec.get("persistentVolumeReclaimPolicy")),
            "volume_mode": _str(spec.get("volumeMode"))}


def project_role_binding(raw: dict) -> dict:
    md, ref = _d(raw.get("metadata")), _d(raw.get("roleRef"))
    subjects = raw.get("subjects") if isinstance(raw.get("subjects"), list) else []
    return {"namespace": _str(md.get("namespace")), "name": _str(md.get("name")),
            "role_kind": _str(ref.get("kind")), "role_name": _str(ref.get("name")),
            "subjects": [{"kind": _str(s.get("kind")), "name": _str(s.get("name")),
                          "namespace": _str(s.get("namespace"))} for s in subjects if isinstance(s, dict)]}


_PROJECT = {"storage_classes": project_storage_class, "pvcs": project_pvc, "pvs": project_pv,
            "role_bindings": project_role_binding}


# --- 연결 ----------------------------------------------------------------------------------------

def build_verify(ca_pem, insecure):
    """httpx verify 값: insecure → False, CA PEM → 그 CA 만 신뢰하는 SSLContext, 없으면 기본 신뢰 저장소."""
    if insecure:
        return False
    if ca_pem:
        return ssl.create_default_context(cadata=ca_pem)
    return ssl.create_default_context()


class _Budget:
    def __init__(self, deadline: float, max_bytes: int, clock):
        self.deadline = deadline
        self.max_bytes = max_bytes
        self.size = 0
        self.clock = clock

    def remaining(self) -> float:
        return self.deadline - self.clock()

    def add(self, item, phase):
        self.size += len(json.dumps(item, separators=(",", ":"), ensure_ascii=False)) + 1
        if self.size > self.max_bytes:
            raise FetchError("k8s_sync_too_large", phase=phase,
                             error=f"projected inventory exceeds {self.max_bytes} bytes")


def _status_message(resp) -> str:
    try:
        body = resp.json()
        message = body.get("message") if isinstance(body, dict) else None
    except Exception:  # noqa: BLE001 -- 본문이 JSON 이 아니어도 상태 코드는 남긴다
        message = None
    text = message if isinstance(message, str) and message else (resp.text or "")[:200]
    return f"HTTP {resp.status_code}: {text}"[:_STATUS_MESSAGE_MAX]


class _Fetcher:
    def __init__(self, run: dict, client: httpx.Client, budget: _Budget):
        self.run = run
        self.client = client
        self.budget = budget
        sni = run.get("tls_server_name")
        self.extensions = {"sni_hostname": sni} if sni else {}

    def _get(self, path, params, phase, *, cap=30.0):
        remaining = self.budget.remaining()
        if remaining <= 0:
            raise FetchError("k8s_sync_timeout", phase=phase, error="run deadline reached")
        total = min(cap, remaining)
        try:
            return self.client.get(path, params=params, extensions=self.extensions,
                                   timeout=httpx.Timeout(total, connect=min(5.0, total)))
        except httpx.HTTPError as exc:
            reason = classify_exception(exc)
            if self.budget.remaining() <= 0:
                reason = "k8s_sync_timeout"
            raise FetchError(reason, phase=phase, error=f"{type(exc).__name__}: {exc}"[:_STATUS_MESSAGE_MAX])

    def best_effort(self, path, pick):
        """/version·kube-system 처럼 실패해도 run 을 실패시키지 않는 조회. 실패 = None."""
        try:
            resp = self._get(path, None, "version", cap=_VERSION_TIMEOUT)
            if resp.status_code != 200:
                return None
            return pick(resp.json())
        except Exception:  # noqa: BLE001 -- 정보용 조회다
            return None

    def list_all(self, key, phase, path, optional):
        restarted = False
        project = _PROJECT[key]
        while True:
            size_before = self.budget.size
            try:
                return self._list_once(key, phase, path, optional, project, restarted)
            except _Restart:
                restarted = True
                self.budget.size = size_before

    def _list_once(self, key, phase, path, optional, project, restarted):
        items = []
        cont = None
        for _page in range(MAX_PAGES):
            params = {"limit": int(self.run.get("page_limit") or 500)}
            if cont:
                params["continue"] = cont
            resp = self._get(path, params, phase)
            if resp.status_code == 410:
                if restarted:
                    raise FetchError("k8s_sync_list_expired", phase=phase, http_status=410,
                                     error=_status_message(resp))
                raise _Restart()
            if resp.status_code != 200:
                if optional and resp.status_code == 403:
                    return None, {"phase": phase, "reason": "k8s_sync_forbidden", "http_status": 403}
                raise FetchError(classify_http_status(resp.status_code), phase=phase,
                                 http_status=resp.status_code, error=_status_message(resp))
            try:
                body = resp.json()
            except ValueError:
                raise FetchError("k8s_sync_bad_response", phase=phase, http_status=200, error="response is not JSON")
            raw_items = body.get("items") if isinstance(body, dict) else None
            if not isinstance(raw_items, list):
                raise FetchError("k8s_sync_bad_response", phase=phase, http_status=200, error="no items list")
            for raw in raw_items:
                if not isinstance(raw, dict):
                    raise FetchError("k8s_sync_bad_response", phase=phase, error="list item is not an object")
                item = project(raw)
                self.budget.add(item, phase)
                items.append(item)
            cont = _d(body.get("metadata")).get("continue")
            if not cont:
                return items, None
        raise FetchError("k8s_sync_too_large", phase=phase, error=f"more than {MAX_PAGES} pages")


def fetch_inventory(run: dict, *, transport=None, clock=time.monotonic) -> dict:
    """claim 으로 받은 run(§4.2) → 결과 POST 본문(node_name 제외). 예외를 올리지 않는다 -- 실패는 ok:false + reason."""
    started = clock()
    deadline = started + float(run.get("deadline_seconds") or 120)
    budget = _Budget(deadline, int(run.get("max_bytes") or 33554432), clock)
    token = run.get("token") or ""
    out = {"ok": False, "warnings": [], "server_version": None, "fingerprint": None}

    def scrub(text):
        if not isinstance(text, str):
            return text
        return text.replace(token, "***") if token else text

    try:
        verify = build_verify(run.get("ca_pem"), bool(run.get("insecure_skip_tls_verify")))
    except (ssl.SSLError, ValueError) as exc:
        out.update(reason="k8s_sync_tls_failed", phase="connect", error=f"CA certificate: {type(exc).__name__}")
        out["duration_ms"] = int((clock() - started) * 1000)
        return out
    client = httpx.Client(base_url=run["api_server"], verify=verify, trust_env=False, follow_redirects=False,
                          timeout=httpx.Timeout(30.0, connect=5.0), transport=transport,
                          headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    try:
        fetcher = _Fetcher(run, client, budget)
        version = fetcher.best_effort("/version", lambda b: b.get("gitVersion") if isinstance(b, dict) else None)
        out["server_version"] = version if isinstance(version, str) else None
        fp = fetcher.best_effort("/api/v1/namespaces/kube-system",
                                 lambda b: _d(b.get("metadata")).get("uid") if isinstance(b, dict) else None)
        out["fingerprint"] = fp if isinstance(fp, str) else None
        data = {}
        for key, phase, path, optional in LISTS:
            items, warning = fetcher.list_all(key, phase, path, optional)
            data[key] = items
            if warning:
                out["warnings"].append(warning)
        out.update(ok=True, data=data)
    except FetchError as e:
        out.update(reason=e.reason, phase=e.phase, http_status=e.http_status, error=scrub(e.error))
    except Exception as exc:  # noqa: BLE001 -- 워커가 죽지 않게, 사유는 분류해 보고한다
        out.update(reason=classify_exception(exc), phase=None, error=scrub(f"{type(exc).__name__}"))
    finally:
        client.close()
    out["duration_ms"] = int((clock() - started) * 1000)
    return out
