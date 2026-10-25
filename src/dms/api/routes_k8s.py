"""k8s 연동 관리·조회 API(dms-k8s 설계 §5, 2026-10-25).

권한(설계 §5 + CLAUDE.md 「공유 토큰은 계정·배포 경로를 못 만진다」):
  - 조회(GET /api/admin/k8s/*) = require_admin(토큰 관리자도 된다 -- 스크립트·모니터링).
  - 변경(호출 노드·주기, 클러스터 등록·주소·토큰·TLS·삭제, StorageClass 매핑) = require_session_admin -- 매핑·토큰은
    「누가 어느 PVC 에 root 로 읽고 쓰나」의 원천이고, 공유 토큰은 모든 노드 에이전트가 가진 관리자 자격이다.
  - 「지금 동기화」(:sync) = require_admin(스크립트 가능, 무해 -- 다음 claim 을 앞당길 뿐).
  - GET /api/user/k8s/volumes = require_user: 자기 맵핑 범위(인식된 RoleBinding 이 있는 namespace)의 PVC 만. 맵핑 면제
    (api.auth.can_run_as_root -- 관리자 + 특권 목록/계정 부여 + 세션)는 전체.

토큰(클러스터 API 토큰):
  - 전송: password_transport 봉인(용도 k8s_cluster_token, AAD 사용자 자리 = 클러스터 이름). 평문 token 은
    password_encryption_required=false(테스트)일 때만 -- 라이브는 422 password_encryption_required.
  - base64 로 한 번 감싼 JWT(kubectl get secret … -o jsonpath='{.data.token}')는 풀어서 저장한다.
  - 저장: secret_box 봉인(k8s_clusters.token_enc). 응답·감사·이벤트 어디에도 값이 나가지 않는다(set/missing/unreadable 과
    JWT exp 표시값만).
  - API 서버 주소를 바꾸면 같은 요청에서 토큰을 다시 넣어야 한다(422 k8s_token_required) -- 관리자 권한만으로 주소를 자기
    서버로 돌려 다음 동기화 때 토큰을 받아 내지 못하게(mail_settings 선례). 검사는 저장 트랜잭션 안(guard).
"""
import base64
import binascii
import ipaddress
import re
import ssl
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from .. import k8s_sync
from ..db import load_json, utc_now_iso
from ..domain import DomainValidationError
from ..pv_paths import effective_template, validate_template
from ..repositories.k8s import storage_kind
from ..repositories.storages import storage_open_to_users
from ..secret_box import seal_at_rest
from .auth import Identity, audit_actor, can_run_as_root, require_admin, require_session_admin, require_user
from .password_transport import PasswordTransportError
from .routes_agent import K8S_TOKEN_PURPOSE, k8s_capable, open_cluster_token
from .routes_auth import EncryptedPassword

router = APIRouter()

SEAL_PURPOSE = K8S_TOKEN_PURPOSE           # 전송 봉인(password_transport) 용도 -- 프런트 Purpose 와 같은 값
_CLUSTER_NAME_RE = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_HOST_RE = re.compile(r"(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$")
_CA_MAX = 65536
MAX_INTERVAL_SECONDS = 86400
VOLUME_LIMIT_DEFAULT = 200
VOLUME_LIMIT_MAX = 2000


# --- 입력 검증 --------------------------------------------------------------------------------------

def validate_cluster_name(name) -> str:
    if not isinstance(name, str) or not _CLUSTER_NAME_RE.fullmatch(name):
        raise HTTPException(status_code=422, detail="invalid_k8s_cluster_name")
    return name


def _valid_host(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return bool(_HOST_RE.fullmatch(host))


def validate_api_server(value) -> str:
    """https://host[:port] 만(경로·쿼리·프래그먼트·사용자 정보 금지). 뒤 '/' 는 벗긴다. host 는 IP(IPv6 는 [..]) 또는 도메인."""
    if not isinstance(value, str):
        raise HTTPException(status_code=422, detail="invalid_k8s_api_server")
    text = value.strip()
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid_k8s_api_server")
    host = parts.hostname or ""
    if (parts.scheme != "https" or not host or parts.username is not None or parts.password is not None
            or parts.path not in ("", "/") or parts.query or parts.fragment or "@" in parts.netloc
            or parts.netloc.endswith(":")
            or not _valid_host(host) or (port is not None and not 1 <= port <= 65535) or len(text) > 300):
        raise HTTPException(status_code=422, detail="invalid_k8s_api_server")
    return f"https://{parts.netloc}"


def validate_server_name(value) -> "str | None":
    """TLS 서버 이름(SNI·인증서 검증 이름). 빈 값 = 없음."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, str) or not _valid_host(value.strip()):
        raise HTTPException(status_code=422, detail="invalid_k8s_api_server")
    return value.strip()


def normalize_ca(value) -> "str | None":
    """CA 인증서: PEM 또는 base64(PEM)(kubeconfig 의 certificate-authority-data) → PEM 텍스트. 빈 값 = 없음.
    실제로 읽히는지(cryptography x509 + ssl 컨텍스트)까지 본다 -- 못 읽으면 422 k8s_ca_invalid."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, str) or len(value) > _CA_MAX:
        raise HTTPException(status_code=422, detail="k8s_ca_invalid")
    text = value.strip()
    if "-----BEGIN CERTIFICATE-----" not in text:
        try:
            decoded = base64.b64decode("".join(text.split()), validate=True).decode("ascii")
        except (binascii.Error, ValueError, UnicodeDecodeError):
            raise HTTPException(status_code=422, detail="k8s_ca_invalid")
        if "-----BEGIN CERTIFICATE-----" not in decoded:
            raise HTTPException(status_code=422, detail="k8s_ca_invalid")
        text = decoded.strip()
    pem = text + "\n"
    try:
        from cryptography import x509
        if not x509.load_pem_x509_certificates(pem.encode("ascii")):
            raise ValueError("no certificate")
        ssl.create_default_context(cadata=pem)
    except Exception:  # noqa: BLE001 -- 형식·인코딩·SSL 무엇이든 「읽을 수 없는 CA」 하나다
        raise HTTPException(status_code=422, detail="k8s_ca_invalid")
    return pem


class ClusterBody(BaseModel):
    """POST 는 name·api_server·토큰 필수. PUT 은 보낸 칸만 바꾼다(토큰 생략 = 유지). ca_pem·tls_server_name 의 null·"" = 지움."""
    name: "str | None" = None
    api_server: "str | None" = None
    tls_server_name: "str | None" = None
    ca_pem: "str | None" = None
    insecure_skip_tls_verify: "bool | None" = None
    enabled: "bool | None" = None
    token: "str | None" = None                     # 평문 -- 봉인 정책이 꺼진 환경(테스트)만
    token_enc: "EncryptedPassword | None" = None   # 포탈은 항상 이쪽


class SettingsBody(BaseModel):
    primary_node: "str | None" = None
    secondary_node: "str | None" = None
    interval_seconds: "int | None" = None


class StorageClassMapBody(BaseModel):
    storage_name: "str | None" = None
    path_template: "str | None" = None


def _token_from(request: Request, body: ClusterBody, cluster_name: str) -> "str | None":
    """봉인(token_enc) 또는 평문(token) → 정리된 토큰(None = 보내지 않음). 형식이 틀리면 422 k8s_token_required."""
    if body.token_enc is not None:
        try:
            raw = request.app.state.password_transport.decrypt(
                body.token_enc.model_dump(), purpose=SEAL_PURPOSE, username=cluster_name)
        except PasswordTransportError as e:
            raise HTTPException(status_code=422, detail=e.reason_code)
    elif body.token is not None:
        if request.app.state.settings.password_encryption_required:
            raise HTTPException(status_code=422, detail="password_encryption_required")
        raw = body.token
    else:
        return None
    token = k8s_sync.unwrap_token(raw)
    if k8s_sync.token_problem(token) is not None:
        raise HTTPException(status_code=422, detail="k8s_token_required")
    return token


def _seal(request: Request, token: str) -> str:
    return seal_at_rest(token, secret=request.app.state.settings.session_secret, purpose=K8S_TOKEN_PURPOSE)


# --- 상태 조립 --------------------------------------------------------------------------------------

class _State:
    """한 요청 동안의 공통 재료(설정·주기·활성 run·토큰 상태) -- 개요·목록·신선도가 같은 판정을 쓴다."""

    def __init__(self, request: Request):
        self.repos = request.app.state.repos
        self.settings = request.app.state.settings
        self.now = utc_now_iso()
        self.row = self.repos.k8s.get_settings() or {}
        self.interval = k8s_sync.effective_interval(self.row, self.settings.k8s_sync_interval_seconds)
        self.nodes_set = k8s_sync.nodes_configured(self.row)
        self.active = self.repos.k8s.active_cluster_names(self.now)
        self.clusters = self.repos.k8s.list_clusters()
        self._tokens = {}

    def token(self, cluster) -> "tuple[str, str | None]":
        """(token 상태 set|missing|unreadable, JWT 만료 ISO|None)."""
        name = cluster["name"]
        if name not in self._tokens:
            enc = cluster.get("token_enc")
            if not enc:
                self._tokens[name] = ("missing", None)
            else:
                plain = open_cluster_token(self.settings, enc)
                self._tokens[name] = ("unreadable", None) if plain is None else \
                    ("set", k8s_sync.jwt_expires_at(plain))
        return self._tokens[name]

    def status(self, cluster) -> str:
        return k8s_sync.derive_status(cluster, token_state=self.token(cluster)[0], nodes_set=self.nodes_set,
                                      active=cluster["name"] in self.active, interval=self.interval, now=self.now)

    def freshness(self) -> list:
        return [k8s_sync.freshness(c, status=self.status(c), interval=self.interval, now=self.now)
                for c in self.clusters]


def _json(value):
    return load_json(value) if isinstance(value, str) else value


def _run_view(run) -> dict:
    out = dict(run)
    for key in ("counts", "warnings", "changes"):
        out[key] = _json(out.get(key))
    out.pop("payload", None)
    return out


def _cluster_summaries(state: _State) -> list:
    counts = state.repos.k8s.cluster_counts()
    latest = state.repos.k8s.latest_run_per_cluster()
    by_fp: dict = {}
    for c in state.clusters:
        if c.get("fingerprint"):
            by_fp.setdefault(c["fingerprint"], []).append(c["name"])
    out = []
    for c in state.clusters:
        token_state, expires = state.token(c)
        status = state.status(c)
        last = latest.get(c["name"])
        dupes = [n for n in by_fp.get(c.get("fingerprint"), []) if n != c["name"]] if c.get("fingerprint") else []
        out.append({
            "name": c["name"], "api_server": c["api_server"], "tls_server_name": c.get("tls_server_name"),
            "ca_pem": c.get("ca_pem"),
            "tls_mode": "insecure" if c.get("insecure_skip_tls_verify") else ("ca" if c.get("ca_pem") else "default"),
            "token": token_state, "token_expires_at": expires, "enabled": bool(c.get("enabled")),
            "status": status, "status_reason": k8s_sync.status_reason(status),
            "last_attempt_at": c.get("last_attempt_at"), "last_success_at": c.get("last_success_at"),
            "last_reason": c.get("last_reason"), "last_error": c.get("last_error"),
            "consecutive_failures": c.get("consecutive_failures") or 0, "server_version": c.get("server_version"),
            "fingerprint": c.get("fingerprint"), "duplicate_of": dupes[0] if dupes else None,
            "sync_requested_at": c.get("sync_requested_at"), "failover_after": c.get("failover_after"),
            "interval_seconds": state.interval, "counts": counts.get(c["name"]),
            "last_run": None if last is None else {
                "run_id": last["run_id"], "node_name": last["node_name"], "role": last["role"],
                "trigger": last["trigger"], "status": last["status"], "duration_ms": last["duration_ms"],
                "reason": last["reason"], "warnings": _json(last.get("warnings")) or [],
                "claimed_at": last["claimed_at"], "finished_at": last["finished_at"]},
            "created_at": c.get("created_at"), "updated_at": c.get("updated_at"), "updated_by": c.get("updated_by"),
        })
    return out


def _node_ips(request: Request) -> dict:
    try:
        return {n.get("name"): n.get("ip") for n in (request.app.state.node_lister() or []) if n.get("name")}
    except Exception:  # noqa: BLE001 -- 표시용(방화벽 허용 IP) -- 실패하면 모름
        return {}


def _nodes(request: Request, state: _State) -> list:
    repos, settings = state.repos, state.settings
    agents = {n["node_name"]: n for n in repos.agents.list_nodes(stale_seconds=settings.agent_report_stale_seconds)}
    stats = repos.k8s.node_run_stats()
    serving: dict = {}
    existing = {c["name"] for c in state.clusters}
    for cluster, run in repos.k8s.latest_run_per_cluster().items():
        if cluster in existing:             # 지운 클러스터의 이력은 「맡은 클러스터」가 아니다
            serving.setdefault(run["node_name"], []).append(cluster)
    ips = _node_ips(request)
    names = [n for n in (state.row.get("primary_node"), state.row.get("secondary_node")) if n]
    names += [n for n in sorted(agents) if n not in names]
    out = []
    for name in names:
        agent = agents.get(name)
        report = (agent or {}).get("report") or {}
        worker = report.get("k8s_sync") if isinstance(report.get("k8s_sync"), dict) else None
        st = stats.get(name, {})
        out.append({
            "node_name": name, "role": k8s_sync.node_role(state.row, name),
            "fresh": bool(agent and agent["fresh"]), "reported": agent is not None,
            "capable": k8s_capable(report), "reported_at": (agent or {}).get("reported_at"),
            "internal_ip": ips.get(name),
            "worker": None if worker is None else {"state": worker.get("worker"), "run_id": worker.get("run_id")},
            "last_claim_at": st.get("last_claim_at"), "last_ok_at": st.get("last_ok_at"),
            "last_fail_at": st.get("last_fail_at"), "last_reason": st.get("last_reason"),
            "serving": sorted(serving.get(name, [])),
        })
    return out


def _settings_view(state: _State) -> dict:
    return {"primary_node": state.row.get("primary_node"), "secondary_node": state.row.get("secondary_node"),
            "interval_seconds": state.interval,
            "interval_source": "portal" if state.row.get("interval_seconds") is not None else "env",
            "interval_default": state.settings.k8s_sync_interval_seconds,
            "poll_seconds": state.settings.k8s_sync_poll_seconds,
            "updated_by": state.row.get("updated_by"), "updated_at": state.row.get("updated_at")}


def _cluster_or_404(repos, name):
    row = repos.k8s.get_cluster(name)
    if row is None:
        raise HTTPException(status_code=404, detail="k8s_cluster_not_found")
    return row


# --- 개요·설정 --------------------------------------------------------------------------------------

@router.get("/api/admin/k8s/overview", dependencies=[Depends(require_admin)])
def overview(request: Request):
    state = _State(request)
    return {"now": state.now, "settings": _settings_view(state), "nodes": _nodes(request, state),
            "clusters": _cluster_summaries(state), "totals": state.repos.k8s.totals()}


@router.put("/api/admin/k8s/settings")
def put_settings(body: SettingsBody, request: Request, identity: Identity = Depends(require_session_admin)):
    repos = request.app.state.repos
    current = repos.k8s.get_settings() or {}
    primary = (body.primary_node or "").strip() or None
    secondary = (body.secondary_node or "").strip() or None
    interval = body.interval_seconds
    if interval is not None and not k8s_sync.MIN_INTERVAL_SECONDS <= interval <= MAX_INTERVAL_SECONDS:
        raise HTTPException(status_code=422, detail="invalid_k8s_sync_settings")
    if primary is not None and primary == secondary:
        raise HTTPException(status_code=422, detail="invalid_k8s_sync_settings")
    for name, before in ((primary, current.get("primary_node")), (secondary, current.get("secondary_node"))):
        # 바꾸는 값만 「보고된 적 있는 노드」인지 본다 -- 주기만 바꾸는 저장이 잠시 보고가 끊긴 지정 노드 때문에 막히지 않게.
        if name is not None and name != before and not repos.agents.node_exists(name):
            raise HTTPException(status_code=422, detail="k8s_node_unknown")
    repos.k8s.update_settings(primary_node=primary, secondary_node=secondary, interval_seconds=interval,
                              actor=audit_actor(identity))
    state = _State(request)
    return _settings_view(state)


# --- 클러스터 ---------------------------------------------------------------------------------------

@router.get("/api/admin/k8s/clusters", dependencies=[Depends(require_admin)])
def list_clusters(request: Request):
    return _cluster_summaries(_State(request))


def _one_summary(request: Request, name: str) -> dict:
    return next(c for c in _cluster_summaries(_State(request)) if c["name"] == name)


@router.post("/api/admin/k8s/clusters", status_code=201)
def create_cluster(body: ClusterBody, request: Request, identity: Identity = Depends(require_session_admin)):
    repos = request.app.state.repos
    name = validate_cluster_name(body.name)
    api_server = validate_api_server(body.api_server)
    sni = validate_server_name(body.tls_server_name)
    ca = normalize_ca(body.ca_pem)
    token = _token_from(request, body, name)
    if token is None:
        raise HTTPException(status_code=422, detail="k8s_token_required")
    try:
        repos.k8s.create_cluster(name=name, api_server=api_server, tls_server_name=sni, ca_pem=ca,
                                 insecure_skip_tls_verify=bool(body.insecure_skip_tls_verify),
                                 token_enc=_seal(request, token),
                                 enabled=True if body.enabled is None else body.enabled, actor=audit_actor(identity))
    except DomainValidationError as e:
        raise HTTPException(status_code=409, detail=e.reason_code)
    return _one_summary(request, name)


@router.put("/api/admin/k8s/clusters/{name}")
def update_cluster(name: str, body: ClusterBody, request: Request,
                   identity: Identity = Depends(require_session_admin)):
    repos = request.app.state.repos
    _cluster_or_404(repos, name)
    sent = body.model_fields_set
    changes: dict = {}
    if "api_server" in sent:
        changes["api_server"] = validate_api_server(body.api_server)
    if "tls_server_name" in sent:
        changes["tls_server_name"] = validate_server_name(body.tls_server_name)
    if "ca_pem" in sent:
        changes["ca_pem"] = normalize_ca(body.ca_pem)
    if "insecure_skip_tls_verify" in sent and body.insecure_skip_tls_verify is not None:
        changes["insecure_skip_tls_verify"] = body.insecure_skip_tls_verify
    if "enabled" in sent and body.enabled is not None:
        changes["enabled"] = body.enabled
    token = _token_from(request, body, name)
    if token is not None:
        changes["token_enc"] = _seal(request, token)

    def guard(before: dict) -> None:
        # 토큰이 어디로 가는가(주소·검증 대상 이름)나 검증을 끄는 쪽으로 바뀌는데 토큰을 같이 넣지 않았다 → 거절(토큰은
        # 「검증된 그 서버」에 묶인다 — 2026-10-26 보안 리뷰: 주소만 묶으면 검증 끄기·TLS 서버 이름 변경으로 같은 주소에서
        # 가로채기를 열 수 있었다). CA 교체는 인증서 갱신 때 흔한 정상 작업이라 묻지 않는다(사용성 — 사용자 지시).
        if token is not None:
            return
        if "api_server" in changes and changes["api_server"] != before.get("api_server"):
            raise HTTPException(status_code=422, detail="k8s_token_required")
        if "tls_server_name" in changes and (changes["tls_server_name"] or None) != (before.get("tls_server_name") or None):
            raise HTTPException(status_code=422, detail="k8s_token_required")
        if changes.get("insecure_skip_tls_verify") is True and not before.get("insecure_skip_tls_verify"):
            raise HTTPException(status_code=422, detail="k8s_token_required")

    try:
        repos.k8s.update_cluster(name, changes, actor=audit_actor(identity), guard=guard)
    except KeyError:
        raise HTTPException(status_code=404, detail="k8s_cluster_not_found")
    return _one_summary(request, name)


@router.delete("/api/admin/k8s/clusters/{name}")
def delete_cluster(name: str, request: Request, identity: Identity = Depends(require_session_admin)):
    try:
        request.app.state.repos.k8s.delete_cluster(name, actor=audit_actor(identity))
    except KeyError:
        raise HTTPException(status_code=404, detail="k8s_cluster_not_found")
    return {"deleted": name}


@router.post("/api/admin/k8s/clusters/{name}:sync", dependencies=[Depends(require_admin)])
def sync_now(name: str, request: Request):
    """「지금 동기화」 -- sync_requested_at = 지금. 다음 claim(지정 노드 폴링, 기본 15초 안)에 due 가 된다."""
    if not request.app.state.repos.k8s.request_sync(name):
        raise HTTPException(status_code=404, detail="k8s_cluster_not_found")
    return _one_summary(request, name)


@router.get("/api/admin/k8s/runs", dependencies=[Depends(require_admin)])
def list_runs(request: Request, cluster: "str | None" = None, node: "str | None" = None,
              before: "int | None" = Query(default=None, ge=1), limit: int = Query(default=50, ge=1, le=200)):
    rows = request.app.state.repos.k8s.list_runs(cluster=cluster, node=node, before=before, limit=limit)
    return [_run_view(r) for r in rows]


# --- StorageClass 매핑 ------------------------------------------------------------------------------

def _sample_view(resolved) -> "dict | None":
    if resolved is None:
        return None
    return {"namespace": resolved["namespace"], "pvc": resolved["pvc"], "volume": resolved["volume"],
            "root": resolved["root"], "abs_root": resolved["abs_root"], "reason": resolved["reason"],
            "problem": resolved["problem"]}


@router.get("/api/admin/k8s/storage-classes", dependencies=[Depends(require_admin)])
def list_storage_classes(request: Request):
    repos = request.app.state.repos
    rows = repos.k8s.storage_class_rows()
    maps = repos.k8s.sc_maps()
    storages = {s["storage_name"]: s for s in repos.storages.list()}
    clusters_by_name: dict = {}
    for r in rows:
        clusters_by_name.setdefault(r["name"], []).append(r["cluster"])
    out = []

    def view(row):
        m = maps.get(row["name"])
        storage = storages.get(m["storage_name"]) if m else None
        template, source = effective_template(sc_template=(m or {}).get("path_template"),
                                              backend_type=(storage or {}).get("backend_type"))
        sample = repos.k8s.sample_volume(row["cluster"], row["name"]) if row["cluster"] else None
        return {**row, "storage_name": (m or {}).get("storage_name"),
                "storage_kind": storage_kind(storage) if storage else None,
                "storage_backend": (storage or {}).get("backend_type"),
                "path_template": (m or {}).get("path_template"),
                "effective_template": template if m else None, "template_source": source if m else None,
                "sample": _sample_view(sample),
                "duplicate_in": sorted(c for c in clusters_by_name.get(row["name"], []) if c != row["cluster"])}

    for row in rows:
        out.append(view(row))
    seen = set(clusters_by_name)
    for sc in sorted(set(maps) - seen):        # 매핑은 있는데 어느 클러스터에도 안 보이는 이름(해제할 수 있게)
        out.append(view({"cluster": None, "name": sc, "provisioner": None, "parameters": {}, "reclaim_policy": None,
                         "volume_binding_mode": None, "pvc_count": 0}))
    return out


@router.put("/api/admin/k8s/storage-classes/{storage_class}")
def put_storage_class(storage_class: str, body: StorageClassMapBody, request: Request,
                      identity: Identity = Depends(require_session_admin)):
    repos = request.app.state.repos
    actor = audit_actor(identity)
    if not repos.k8s.storage_class_known(storage_class):
        raise HTTPException(status_code=404, detail="storage_class_not_found")
    storage_name = (body.storage_name or "").strip() or None
    if storage_name is None:                            # 매핑 해제
        repos.k8s.delete_sc_map(storage_class, actor=actor)
        return {"storage_class": storage_class, "storage_name": None, "path_template": None}
    template = None
    if body.path_template is not None and body.path_template.strip():
        try:
            template = validate_template(body.path_template)
        except DomainValidationError as e:
            raise HTTPException(status_code=422, detail=e.reason_code)
    storage = repos.storages.get(storage_name)
    if storage is None:
        raise HTTPException(status_code=422, detail="storage_missing")
    if template is None and effective_template(sc_template=None, backend_type=storage.get("backend_type"))[0] is None:
        # 백엔드 프리셋이 없는 스토리지(cephfs 등)는 템플릿을 꼭 적어야 한다(설계 D11).
        raise HTTPException(status_code=422, detail="invalid_pv_path_template")
    try:
        row = repos.k8s.set_sc_map(storage_class, storage_name=storage_name, path_template=template, actor=actor)
    except DomainValidationError as e:
        raise HTTPException(status_code=422, detail=e.reason_code)
    return {"storage_class": row["storage_class"], "storage_name": row["storage_name"],
            "path_template": row["path_template"]}


# --- 볼륨·권한 --------------------------------------------------------------------------------------

def _volume_query(request, *, dms_user, cluster, namespace, storage_class, q, limit, pvc=None):
    rows, truncated = request.app.state.repos.k8s.volumes(
        cluster=cluster, namespace=namespace, storage_class=storage_class, q=q, dms_user=dms_user, pvc=pvc,
        limit=limit)
    return rows, truncated


def _mark_unusable_storages(repos, rows, *, is_admin) -> None:
    """PV 고르기에서 매핑 스토리지를 지금 쓸 수 없는 행은 고를 수 없게(2026-10-26 리뷰): 완전 비활성(enabled≠1)은 누구에게나
    storage_disabled(planner 가 거부), 관리자 전용(storage_open_to_users 거짓)은 비관리자에게 storage_admin_only(제출이 403) --
    직접 경로 피커(/api/user/storages)가 같은 두 상태를 빼는 것과 같은 술어. 해석(resolve_volume)은 그대로 둔다 -- 제출·재확인이
    같은 함수를 쓰고 그 사유 순서를 바꾸지 않는다."""
    storages = {r["storage_name"]: r for r in repos.storages.list()}
    for r in rows:
        row = storages.get(r.get("storage_name")) if r.get("selectable") else None
        if row is None:
            continue
        if row["enabled"] != 1:
            r.update(selectable=False, reason="storage_disabled")
        elif not is_admin and not storage_open_to_users(row):
            r.update(selectable=False, reason="storage_admin_only")


@router.get("/api/admin/k8s/volumes", dependencies=[Depends(require_admin)])
def admin_volumes(request: Request, cluster: "str | None" = None, namespace: "str | None" = None,
                  storage_class: "str | None" = None, q: "str | None" = Query(default=None, max_length=200),
                  user: "str | None" = Query(default=None, max_length=64),
                  pvc: "str | None" = Query(default=None, max_length=253),
                  limit: int = Query(default=VOLUME_LIMIT_DEFAULT, ge=1, le=VOLUME_LIMIT_MAX)):
    """PV 탐색(관리자). user 를 주면 그 사용자의 맵핑 범위만(행마다 access). 행마다 권한 사용자 목록(users). pvc = 이름 정확
    일치(제출 화면이 고른 PV 를 다시 찾을 때)."""
    state = _State(request)
    rows, truncated = _volume_query(request, dms_user=(user or "").strip() or None, cluster=cluster,
                                    namespace=namespace, storage_class=storage_class, q=q, limit=limit, pvc=pvc)
    users = state.repos.k8s.users_by_namespace()
    for r in rows:
        r["users"] = users.get((r["cluster"], r["namespace"]), [])
    # 면제 관리자의 PV 고르기(검색·권한 기준 사용자)도 이 경로다 -- 비활성 스토리지의 PV 는 고를 수 없게(관리자라 관리자 전용은 통과).
    _mark_unusable_storages(state.repos, rows, is_admin=True)
    return {"clusters": state.freshness(), "truncated": truncated, "volumes": rows}


@router.get("/api/admin/k8s/access", dependencies=[Depends(require_admin)])
def admin_access(request: Request, q: "str | None" = Query(default=None, max_length=200)):
    """사용자 권한 화면: 인식된 바인딩을 사용자별로(근거 바인딩·subject 불일치·계정 존재) + 인식하지 않은 바인딩."""
    state = _State(request)
    k8s = state.repos.k8s
    accounts = {a["username"] for a in state.repos.accounts.list()}
    pvc_counts = k8s.pvc_counts_by_namespace()
    users: dict = {}
    for b in k8s.bindings(recognized=True, q=q):
        entry = users.setdefault(b["dms_user"], {"dms_user": b["dms_user"],
                                                 "account_exists": b["dms_user"] in accounts,
                                                 "namespaces": [], "_ns": set()})
        entry["namespaces"].append({"cluster": b["cluster_name"], "namespace": b["namespace"], "access": b["access"],
                                    "binding": b["name"], "role_name": b["role_name"],
                                    "subject_mismatch": None if b["subject_mismatch"] is None
                                    else bool(b["subject_mismatch"]),
                                    "subjects": b["subjects"]})
        entry["_ns"].add((b["cluster_name"], b["namespace"]))
    out_users = []
    for entry in users.values():
        entry["volume_count"] = sum(pvc_counts.get(key, 0) for key in entry.pop("_ns"))
        out_users.append(entry)
    unrecognized = [{"cluster": b["cluster_name"], "namespace": b["namespace"], "name": b["name"],
                     "role_kind": b["role_kind"], "role_name": b["role_name"], "subjects": b["subjects"]}
                    for b in k8s.bindings(recognized=False, q=q)]
    return {"clusters": state.freshness(), "users": out_users, "unrecognized": unrecognized}


@router.get("/api/user/k8s/volumes")
def user_volumes(request: Request, identity: Identity = Depends(require_user),
                 cluster: "str | None" = None, namespace: "str | None" = None,
                 storage_class: "str | None" = None, q: "str | None" = Query(default=None, max_length=200),
                 pvc: "str | None" = Query(default=None, max_length=253),
                 limit: int = Query(default=VOLUME_LIMIT_MAX, ge=1, le=VOLUME_LIMIT_MAX)):
    """제출 화면의 PV 고르기. 자기 맵핑 범위(인식된 RoleBinding 이 있는 namespace)의 PVC 만 -- 면제자(can_run_as_root)는 전체.
    namespaces = 권한이 있는 namespace(PVC 가 0개여도) -- 「권한 namespace 없음」과 「PVC 없음」을 화면이 가른다. clusters 의
    synced_at null = 아직 동기화 안 됨(권한 없음과 다르다 -- null ≠ 0)."""
    state = _State(request)
    unrestricted = can_run_as_root(identity, state.settings, state.repos)
    dms_user = None if unrestricted else identity.actor
    rows, truncated = _volume_query(request, dms_user=dms_user, cluster=cluster, namespace=namespace,
                                    storage_class=storage_class, q=q, limit=limit, pvc=pvc)
    _mark_unusable_storages(state.repos, rows, is_admin=identity.role == "admin")
    if unrestricted:
        namespaces = None
    else:
        namespaces = [{"cluster": c, "namespace": ns, "access": access}
                      for (c, ns), access in sorted(state.repos.k8s.namespace_access(identity.actor).items())]
    return {"clusters": state.freshness(), "unrestricted": unrestricted, "namespaces": namespaces,
            "truncated": truncated, "volumes": rows}
