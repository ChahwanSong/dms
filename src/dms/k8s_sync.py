"""k8s 인벤토리 동기화의 순수 규칙(dms-k8s 설계 D6·D7·D16 §4, 2026-10-25) -- 배정·due·상태·오류 분류·토큰 표시값·결과 검증.

라우트(claim·관리 화면)와 컨트롤러 루프가 **같은 함수**를 쓴다(domain.auto_confirmable 처럼 한 곳). DB·네트워크를 모른다.

배정(D16, 결정적 두 규칙):
  (1) 정규 run 은 primary 전용. due 부터 SECONDARY_GRACE_SECONDS(90초)가 지나도록 primary 가 claim 하지 않으면 secondary 도
      받는다(primary 가 지정되지 않았으면 secondary 가 due 에 바로 받는다).
      due = sync_requested_at(「지금 동기화」) ‖ last_attempt_at + 주기 ‖ (시도 이력 없음) created_at.
  (2) primary run 이 노드성 사유(FAILOVER_REASONS: unreachable·dns_failed·timeout·expired)로 끝나면 FAILOVER_DELAY_SECONDS
      (30초) 뒤 secondary 전용으로 1회 대체 재시도(trigger=failover, 클러스터 failover_after). tls·401·403·too_large·
      bad_response 는 대체하지 않는다(다른 노드에서도 같다). 다음 정규 주기는 다시 primary.
  토큰이 없거나 열 수 없는 클러스터는 due 에서 빠진다(run 을 만들지 않는다 -- 상태 token_missing/token_unreadable).
  노드가 둘 다 지정되지 않았으면 표시만(no_node) -- 다른 노드로 확장하지 않는다(대상 API 서버 방화벽은 노드 IP 기준).
  활성 run(claimed 기한 안 ∪ received)이 있는 클러스터는 claim 하지 않는다(저장소가 판정).
"""
import base64
import binascii
import json
import socket
import ssl
from datetime import datetime, timezone

from .db import iso_plus

SECONDARY_GRACE_SECONDS = 90
FAILOVER_DELAY_SECONDS = 30
# claim 응답의 deadline_seconds 위에 서버가 더 기다리는 업로드 유예(설계 §4.2). 이 안에 결과가 오지 않으면 expired.
UPLOAD_GRACE_SECONDS = 30
MIN_INTERVAL_SECONDS = 60

# 동기화 실패 사유 코드 -- reasonCodes.json·api.ts REASON_MESSAGES 양쪽에 있어야 한다(tests 가 이 집합을 대조한다:
# 에이전트가 문자열로 실어 보내는 값이라 AST 추출 그물 밖이다).
SYNC_REASONS = frozenset({
    "k8s_sync_dns_failed", "k8s_sync_unreachable", "k8s_sync_tls_failed", "k8s_sync_unauthorized",
    "k8s_sync_forbidden", "k8s_sync_timeout", "k8s_sync_list_expired", "k8s_sync_too_large",
    "k8s_sync_bad_response", "k8s_sync_expired", "k8s_sync_apply_failed",
})
# 표시용 상태 사유(클러스터 상태 → 화면 문구). 위와 같은 이유로 양쪽 등록.
STATUS_REASONS = frozenset({"k8s_sync_no_node", "k8s_sync_token_missing", "k8s_sync_token_unreadable"})
FAILOVER_REASONS = frozenset({"k8s_sync_unreachable", "k8s_sync_dns_failed", "k8s_sync_timeout",
                              "k8s_sync_expired"})

CLUSTER_STATUSES = ("ok", "syncing", "delayed", "failing", "never", "no_node", "disabled",
                    "token_missing", "token_unreadable")
_STATUS_REASON = {"no_node": "k8s_sync_no_node", "token_missing": "k8s_sync_token_missing",
                  "token_unreadable": "k8s_sync_token_unreadable"}


# --- 설정·배정 ---------------------------------------------------------------------------------

def effective_interval(settings_row, default: int) -> int:
    """주기(초). DB 값(k8s_sync_settings.interval_seconds)이 env 기본값을 이긴다. 하한 MIN_INTERVAL_SECONDS."""
    value = (settings_row or {}).get("interval_seconds")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return max(value, MIN_INTERVAL_SECONDS)
    return max(int(default), MIN_INTERVAL_SECONDS)


def node_role(settings_row, node_name) -> "str | None":
    row = settings_row or {}
    if node_name and node_name == row.get("primary_node"):
        return "primary"
    if node_name and node_name == row.get("secondary_node"):
        return "secondary"
    return None


def nodes_configured(settings_row) -> bool:
    row = settings_row or {}
    return bool(row.get("primary_node") or row.get("secondary_node"))


def due_at(cluster: dict, interval: int) -> "str | None":
    """정규 due 시각(ISO). 비활성이면 None. 대체 재시도(failover_after)는 따로 본다(claim_trigger)."""
    if not cluster.get("enabled"):
        return None
    if cluster.get("sync_requested_at"):
        return cluster["sync_requested_at"]
    if cluster.get("last_attempt_at"):
        return iso_plus(cluster["last_attempt_at"], interval)
    return cluster.get("created_at")


def claim_trigger(cluster: dict, *, role, now: str, interval: int, primary_configured: bool) -> "str | None":
    """이 역할의 노드가 지금 이 클러스터를 claim 해도 되나 → trigger("schedule"|"manual"|"failover") | None.

    토큰 유무·활성 run 은 호출자(저장소)가 먼저 거른다."""
    if role not in ("primary", "secondary") or not cluster.get("enabled"):
        return None
    failover = cluster.get("failover_after")
    if role == "secondary" and failover and now >= failover:
        return "failover"
    due = due_at(cluster, interval)
    if due is None:
        return None
    trigger = "manual" if cluster.get("sync_requested_at") else "schedule"
    if role == "primary":
        return trigger if now >= due else None
    threshold = due if not primary_configured else iso_plus(due, SECONDARY_GRACE_SECONDS)
    return trigger if now >= threshold else None


def failover_after_for(*, role, trigger, reason, now: str) -> "str | None":
    """실패로 끝난 run 이 대체 재시도를 예약하나 → 예약 시각 | None. primary 의 노드성 실패만, 대체 run 자신은 제외."""
    if role == "primary" and trigger != "failover" and reason in FAILOVER_REASONS:
        return iso_plus(now, FAILOVER_DELAY_SECONDS)
    return None


# --- 상태 ----------------------------------------------------------------------------------------

def is_delayed(last_success_at, interval: int, now: str) -> bool:
    return bool(last_success_at) and iso_plus(last_success_at, 2 * interval) < now


def derive_status(cluster: dict, *, token_state: str, nodes_set: bool, active: bool, interval: int,
                  now: str) -> str:
    """클러스터 표시 상태(CLUSTER_STATUSES). 지연(delayed)은 저장하지 않고 여기서 계산한다 -- 에이전트가 전부 죽어도
    화면이 정직하게 「지연」을 보인다. token_state ∈ {"set", "missing", "unreadable"}."""
    if not cluster.get("enabled"):
        return "disabled"
    if token_state == "missing":
        return "token_missing"
    if token_state == "unreadable":
        return "token_unreadable"
    if not nodes_set:
        return "no_node"
    if active:
        return "syncing"
    if (cluster.get("consecutive_failures") or 0) > 0:
        return "failing"
    if not cluster.get("last_success_at"):
        return "never"
    if is_delayed(cluster.get("last_success_at"), interval, now):
        return "delayed"
    return "ok"


def status_reason(status: str) -> "str | None":
    """상태 자체가 사유인 경우의 사유 코드(화면 문구용)."""
    return _STATUS_REASON.get(status)


def freshness(cluster: dict, *, status: str, interval: int, now: str) -> dict:
    """ClusterFreshness -- 사용자 화면의 「n분 전 기준」. synced_at null = 아직 한 번도 동기화되지 않음(권한 없음과
    다르다 -- null ≠ 0). stale 은 synced_at 이 있을 때만 참·거짓, 없으면 null(모름)."""
    synced = cluster.get("last_success_at")
    return {"name": cluster["name"], "synced_at": synced,
            "stale": None if not synced else is_delayed(synced, interval, now),
            "status": status}


# --- 오류 분류 ---------------------------------------------------------------------------------

def _chain(exc):
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__ or exc.__context__


_DNS_TEXT = ("name or service not known", "temporary failure in name resolution",
             "nodename nor servname", "no address associated with hostname", "name resolution")
_TLS_TEXT = ("certificate", "ssl", "tls", "handshake")


def classify_exception(exc) -> str:
    """요청 예외 → 사유 코드. 순서: DNS > TLS > 연결 타임아웃(방화벽 DROP 은 이렇게 보인다 → unreachable) > 읽기 타임아웃
    > 연결 실패 > 그 밖(bad_response). httpx 는 원인 예외(gaierror·SSLError)를 __cause__ 로 잇는다."""
    import httpx
    chain = list(_chain(exc))
    if any(isinstance(e, socket.gaierror) for e in chain):
        return "k8s_sync_dns_failed"
    if any(isinstance(e, ssl.SSLError) for e in chain):
        return "k8s_sync_tls_failed"
    text = " ".join(str(e) for e in chain).lower()
    if any(isinstance(e, httpx.ConnectTimeout) for e in chain):
        return "k8s_sync_unreachable"
    if any(isinstance(e, httpx.TimeoutException) for e in chain):
        return "k8s_sync_timeout"
    if any(t in text for t in _DNS_TEXT):
        return "k8s_sync_dns_failed"
    if any(isinstance(e, httpx.ConnectError) for e in chain):
        if any(t in text for t in _TLS_TEXT):
            return "k8s_sync_tls_failed"
        return "k8s_sync_unreachable"
    if any(isinstance(e, (ConnectionError, httpx.NetworkError)) for e in chain):
        return "k8s_sync_unreachable"
    return "k8s_sync_bad_response"


def classify_http_status(status: int) -> str:
    if status == 401:
        return "k8s_sync_unauthorized"
    if status == 403:
        return "k8s_sync_forbidden"
    if status == 410:
        return "k8s_sync_list_expired"
    return "k8s_sync_bad_response"


# --- 토큰(표시용) -------------------------------------------------------------------------------

def _b64decode_loose(text: str) -> "bytes | None":
    raw = text.strip()
    pad = "=" * (-len(raw) % 4)
    for fn in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            return fn(raw + pad)
        except (binascii.Error, ValueError):
            continue
    return None


def _looks_like_jwt(text: str) -> bool:
    parts = text.split(".")
    return len(parts) == 3 and all(parts[:2]) and parts[0].startswith("eyJ")


def unwrap_token(raw: str) -> str:
    """입력 토큰 정리: 앞뒤 공백 제거, base64 로 한 번 감싼 JWT(kubectl get secret … -o jsonpath='{.data.token}' 값)면
    풀어서 돌려준다. JWT 가 아닌 토큰(정적 토큰 등)은 그대로."""
    text = (raw or "").strip()
    if not text or _looks_like_jwt(text):
        return text
    decoded = _b64decode_loose(text)
    if decoded:
        try:
            inner = decoded.decode("ascii").strip()
        except UnicodeDecodeError:
            return text
        if _looks_like_jwt(inner):
            return inner
    return text


def token_problem(token: str) -> "str | None":
    """저장 전 토큰 형식 검사 → 문제 설명 | None. 빈 값·공백·제어문자·너무 긴 값은 받지 않는다(Authorization 헤더에 실린다)."""
    if not token:
        return "empty"
    if len(token) > 16384:
        return "too long"
    if any(ch.isspace() or ord(ch) < 0x21 or ord(ch) > 0x7E for ch in token):
        return "whitespace or non-printable characters"
    return None


def jwt_expires_at(token) -> "str | None":
    """JWT 의 exp(만료) → ISO. 서명은 검증하지 않는다(화면 표시용 -- 판정은 대상 API 서버가 한다). JWT 가 아니거나
    exp 가 없으면(장기 토큰 Secret) None."""
    if not isinstance(token, str) or not _looks_like_jwt(token):
        return None
    payload = _b64decode_loose(token.split(".")[1])
    if not payload:
        return None
    try:
        claims = json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        return None
    exp = claims.get("exp") if isinstance(claims, dict) else None
    if not isinstance(exp, (int, float)) or isinstance(exp, bool):
        return None
    try:
        return datetime.fromtimestamp(int(exp), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OverflowError, OSError, ValueError):
        return None


# --- 에이전트 결과 검증(서버 측 -- DB 가 신뢰 경계) ------------------------------------------

_MAX_NAME = 253
_MAX_TEXT = 4096
_MAX_MAP = 256
_MAX_LIST = 64


def _s(value, *, required=False, limit=_MAX_TEXT):
    if value is None:
        if required:
            raise ValueError("missing required string")
        return None
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        raise ValueError("bad string")
    if required and not value:
        raise ValueError("empty required string")
    return value


def _str_map(value):
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > _MAX_MAP:
        raise ValueError("bad map")
    out = {}
    for k, v in value.items():
        out[_s(k, required=True, limit=_MAX_NAME)] = _s(v if not isinstance(v, (int, float, bool)) else str(v)) or ""
    return out


def _str_list(value):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > _MAX_LIST:
        raise ValueError("bad list")
    return [_s(v, required=True, limit=_MAX_NAME) for v in value]


def _items(value, *, optional):
    if value is None and optional:
        return None
    if not isinstance(value, list):
        raise ValueError("list expected")
    return value


def normalize_snapshot(data) -> dict:
    """에이전트가 올린 data(§4.1 투영)를 서버가 다시 검증·정규화한다 -- 모양이 틀리면 ValueError(run 은 bad_response).

    필수 목록(pvcs·role_bindings)은 리스트여야 하고, 선택 목록(storage_classes·pvs)은 None(그 목록 403 -- 이전 값 유지)
    또는 리스트다. 키 중복도 거부한다(같은 PVC 가 두 번 오면 어느 쪽이 맞는지 모른다)."""
    if not isinstance(data, dict):
        raise ValueError("data must be an object")
    out = {"storage_classes": None, "pvs": None}
    scs = _items(data.get("storage_classes"), optional=True)
    if scs is not None:
        out["storage_classes"] = []
        seen = set()
        for it in scs:
            if not isinstance(it, dict):
                raise ValueError("storage class must be an object")
            row = {"name": _s(it.get("name"), required=True, limit=_MAX_NAME),
                   "provisioner": _s(it.get("provisioner"), limit=_MAX_NAME),
                   "parameters": _str_map(it.get("parameters")),
                   "reclaim_policy": _s(it.get("reclaim_policy"), limit=64),
                   "volume_binding_mode": _s(it.get("volume_binding_mode"), limit=64)}
            if row["name"] in seen:
                raise ValueError("duplicate storage class")
            seen.add(row["name"])
            out["storage_classes"].append(row)
    pvcs = _items(data.get("pvcs"), optional=False)
    out["pvcs"] = []
    seen = set()
    for it in pvcs:
        if not isinstance(it, dict):
            raise ValueError("pvc must be an object")
        row = {"namespace": _s(it.get("namespace"), required=True, limit=_MAX_NAME),
               "name": _s(it.get("name"), required=True, limit=_MAX_NAME),
               "uid": _s(it.get("uid"), limit=64),
               "storage_class": _s(it.get("storage_class"), limit=_MAX_NAME),
               "volume_name": _s(it.get("volume_name"), limit=_MAX_NAME) or None,
               "phase": _s(it.get("phase"), limit=64),
               "capacity": _s(it.get("capacity"), limit=64),
               "access_modes": _str_list(it.get("access_modes")),
               "created_at": _s(it.get("created_at"), limit=64)}
        key = (row["namespace"], row["name"])
        if key in seen:
            raise ValueError("duplicate pvc")
        seen.add(key)
        out["pvcs"].append(row)
    pvs = _items(data.get("pvs"), optional=True)
    if pvs is not None:
        out["pvs"] = []
        seen = set()
        for it in pvs:
            if not isinstance(it, dict):
                raise ValueError("pv must be an object")
            row = {"name": _s(it.get("name"), required=True, limit=_MAX_NAME),
                   "driver": _s(it.get("driver"), limit=_MAX_NAME),
                   "volume_handle": _s(it.get("volume_handle")),
                   "volume_attributes": _str_map(it.get("volume_attributes")),
                   "nfs_server": _s(it.get("nfs_server"), limit=_MAX_NAME),
                   "nfs_path": _s(it.get("nfs_path")),
                   "storage_class": _s(it.get("storage_class"), limit=_MAX_NAME),
                   "claim_namespace": _s(it.get("claim_namespace"), limit=_MAX_NAME),
                   "claim_name": _s(it.get("claim_name"), limit=_MAX_NAME),
                   "capacity": _s(it.get("capacity"), limit=64),
                   "access_modes": _str_list(it.get("access_modes")),
                   "phase": _s(it.get("phase"), limit=64),
                   "reclaim_policy": _s(it.get("reclaim_policy"), limit=64),
                   "volume_mode": _s(it.get("volume_mode"), limit=64)}
            if row["name"] in seen:
                raise ValueError("duplicate pv")
            seen.add(row["name"])
            out["pvs"].append(row)
    rbs = _items(data.get("role_bindings"), optional=False)
    out["role_bindings"] = []
    seen = set()
    for it in rbs:
        if not isinstance(it, dict):
            raise ValueError("role binding must be an object")
        subjects = it.get("subjects")
        if subjects is None:
            subjects = []
        if not isinstance(subjects, list) or len(subjects) > _MAX_LIST:
            raise ValueError("bad subjects")
        subs = []
        for s in subjects:
            if not isinstance(s, dict):
                raise ValueError("subject must be an object")
            subs.append({"kind": _s(s.get("kind"), limit=64), "name": _s(s.get("name"), limit=_MAX_NAME),
                         "namespace": _s(s.get("namespace"), limit=_MAX_NAME)})
        row = {"namespace": _s(it.get("namespace"), required=True, limit=_MAX_NAME),
               "name": _s(it.get("name"), required=True, limit=_MAX_NAME),
               "role_kind": _s(it.get("role_kind"), limit=64),
               "role_name": _s(it.get("role_name"), limit=_MAX_NAME),
               "subjects": subs}
        key = (row["namespace"], row["name"])
        if key in seen:
            raise ValueError("duplicate role binding")
        seen.add(key)
        out["role_bindings"].append(row)
    return out


def snapshot_counts(data: dict) -> dict:
    """정규화된 data 의 개수(run 이력 표시용). 선택 목록이 None 이면 그 개수도 None(모름 ≠ 0)."""
    from .k8s_rbac import derive_binding
    pvcs = data.get("pvcs") or []
    rbs = data.get("role_bindings") or []
    derived = [derive_binding(rb) for rb in rbs]
    namespaces = {p["namespace"] for p in pvcs} | {rb["namespace"] for rb in rbs}
    return {
        "storage_classes": None if data.get("storage_classes") is None else len(data["storage_classes"]),
        "pvs": None if data.get("pvs") is None else len(data["pvs"]),
        "pvcs": len(pvcs),
        "pvcs_bound": sum(1 for p in pvcs if p.get("phase") == "Bound"),
        "namespaces": len(namespaces),
        "bindings": len(rbs),
        "recognized_bindings": sum(1 for d in derived if d["dms_user"]),
        "users": len({d["dms_user"] for d in derived if d["dms_user"]}),
    }
