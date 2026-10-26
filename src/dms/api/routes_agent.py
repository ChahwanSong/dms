import json

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from .. import k8s_sync
from ..artifact_base import resolve_artifact_base, strip_scheme
from ..db import utc_now_iso
from ..k8s_inventory import COMPONENT as K8S_COMPONENT, record_failure_transition
from ..repositories.k8s import RunClosed
from ..secret_box import open_at_rest
# 노드 이름 규칙은 auth 가 유일 출처다 -- 토큰 경로의 actor 게이트가 통과시킨
# node:<이름> 을 여기서 다시 검증하므로 두 규칙이 갈라지면 안 된다(슬라이스 19).
from .auth import Identity, _NODE_NAME_RE, require_user

router = APIRouter()

# 클러스터 토큰의 저장 봉인 용도(secret_box) -- routes_k8s 가 같은 값으로 봉인한다.
K8S_TOKEN_PURPOSE = "k8s_cluster_token"
# 결과 본문의 data 밖 몫(사유·경고·시각 등) 여유 -- 상한은 투영 상한(k8s_sync_max_bytes) + 이것.
_RESULT_ENVELOPE_BYTES = 1048576
_WARNINGS_MAX = 20


def _node_from(body, identity: Identity) -> str:
    node_name = body.get("node_name") if isinstance(body, dict) else None
    if not isinstance(node_name, str) or not _NODE_NAME_RE.fullmatch(node_name):
        raise HTTPException(status_code=422, detail="invalid_node_name")
    if identity.actor != f"node:{node_name}":
        raise HTTPException(status_code=403, detail="agent_node_identity_mismatch")
    return node_name


def open_cluster_token(settings, token_enc) -> "str | None":
    return open_at_rest(token_enc, secret=settings.session_secret, purpose=K8S_TOKEN_PURPOSE)


def k8s_sync_block(repos, settings, node_name) -> dict:
    """보고·claim 응답의 k8s_sync(비밀 없음): 이 노드의 역할과 claim 폴링 간격. role null = 지정 노드가 아니다."""
    return {"role": k8s_sync.node_role(repos.k8s.get_settings(), node_name),
            "poll_seconds": settings.k8s_sync_poll_seconds}


def k8s_capable(report) -> bool:
    """보고 본문의 능력 선언(k8s_sync.v == 1) -- 선언한 에이전트(워커 스레드가 있는 새 에이전트)에게만 run·토큰을 준다."""
    block = (report or {}).get("k8s_sync") if isinstance(report, dict) else None
    return isinstance(block, dict) and block.get("v") == 1


@router.post("/api/agent/report")
def ingest_report(body: dict, request: Request,
                  identity: Identity = Depends(require_user)):
    node_name = _node_from(body, identity)
    repos = request.app.state.repos
    settings = request.app.state.settings
    repos.agents.ingest(node_name, body)
    storages = [{"storage_name": s["storage_name"], "mount_path": s["mount_path"],
                 "managed_root": s["managed_root"]}
                for s in repos.storages.list() if s["enabled"]]
    response = {
        "storages": storages,
        "report_interval_seconds": settings.agent_report_interval_seconds,
        # 슬라이스 18(설계 §2.4b): 에이전트는 ConfigMap 을 envFrom 으로 받지 않아
        # (50-agent-daemonset.yaml) base 를 아는 유일한 경로가 이 응답이다.
        # 스킴을 벗겨 내린다 -- 에이전트 프로브는 os.path 만 안다.
        "artifact_base_path": strip_scheme(
            resolve_artifact_base(repos.control, settings)),
        # k8s 인벤토리 동기화(2026-10-25 dms-k8s 설계 §4.1): 지정 노드(primary·secondary)면 워커 스레드가 claim 폴링을 켠다.
        # 비밀이 없다 -- 토큰은 claim 응답에만 run 단위로 실린다.
        "k8s_sync": k8s_sync_block(repos, settings, node_name),
    }
    # (2026-10-26 dms-k8s LDAP 코드 삭제: 노드 신원 프로브 대상(identity_probe_targets)·nslcd 디렉터리 설정(directory)은
    #  내려주지 않는다. 옛 에이전트의 보고에 실린 identities·directory 는 그대로 저장될 뿐 아무도 읽지 않는다.)
    return response


@router.post("/api/agent/k8s-sync/claim")
def k8s_sync_claim(body: dict, request: Request, identity: Identity = Depends(require_user)):
    """지정 노드 에이전트의 워커가 poll_seconds 마다 부른다 → 처리할 run **최대 1건**(없으면 run null).

    run 을 받으려면 (1) 보고에 능력 선언(k8s_sync.v=1)을 실었고 (2) 지금 primary·secondary 로 지정된 노드여야 한다 -- 아니면
    200 + run null(역할이 바뀐 직후의 폴링을 오류로 만들지 않는다). 배정·경합은 저장소 claim(D16 규칙 + last_attempt_at CAS).
    토큰은 이 응답에만 실린다(공유 토큰 채널 -- 신뢰 경계는 설계 §4·BACKLOG 채널 TLS)."""
    node_name = _node_from(body, identity)
    repos = request.app.state.repos
    settings = request.app.state.settings
    block = k8s_sync_block(repos, settings, node_name)
    response = {"run": None, **block}
    role = block["role"]
    if role is None:
        return response
    latest = repos.agents.latest_reports([node_name])
    if not latest or not k8s_capable(latest[0]["report"]):
        return response
    row = repos.k8s.get_settings() or {}
    tokens: dict = {}

    def token_ok(enc):
        if enc not in tokens:
            tokens[enc] = open_cluster_token(settings, enc)
        return tokens[enc] is not None

    got = repos.k8s.claim(node_name=node_name, role=role, now=utc_now_iso(),
                          interval=k8s_sync.effective_interval(row, settings.k8s_sync_interval_seconds),
                          primary_configured=bool(row.get("primary_node")), token_ok=token_ok,
                          deadline_seconds=settings.k8s_sync_run_deadline_seconds)
    if got is None:
        return response
    run, cluster = got["run"], got["cluster"]
    token = open_cluster_token(settings, cluster.get("token_enc"))
    if token is None:   # claim 과 이 사이에 토큰이 바뀌었다(config_rev CAS 가 거의 막는다) -- run 은 기한 뒤 expired
        return response
    if run["trigger"] == "failover":
        repos.observability.record_event(
            component=K8S_COMPONENT, severity="info", event_type="k8s_sync_failover",
            message=f"{cluster['name']}: secondary {node_name} retries after the primary failed",
            payload={"cluster": cluster["name"], "node": node_name})
    response["run"] = {
        "run_id": run["run_id"], "cluster": cluster["name"], "api_server": cluster["api_server"],
        "tls_server_name": cluster.get("tls_server_name"), "ca_pem": cluster.get("ca_pem"),
        "insecure_skip_tls_verify": bool(cluster.get("insecure_skip_tls_verify")), "token": token,
        "page_limit": settings.k8s_sync_page_limit, "deadline_seconds": settings.k8s_sync_run_deadline_seconds,
        "max_bytes": settings.k8s_sync_max_bytes, "role": run["role"], "trigger": run["trigger"],
    }
    return response


def _short_str(value, limit):
    return value[:limit] if isinstance(value, str) and value else None


def _int_or_none(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _warnings(value) -> list:
    out = []
    for w in (value if isinstance(value, list) else [])[:_WARNINGS_MAX]:
        if not isinstance(w, dict):
            continue
        reason = w.get("reason") if w.get("reason") in k8s_sync.SYNC_REASONS else None
        out.append({"phase": _short_str(w.get("phase"), 64), "reason": reason,
                    "http_status": _int_or_none(w.get("http_status"))})
    return out


def _receive(request: Request, run_id: str, node_name: str, body: dict) -> dict:
    repos = request.app.state.repos
    ok = body.get("ok") is True
    reason, error, data = body.get("reason"), _short_str(body.get("error"), 500), None
    if ok:
        try:
            data = k8s_sync.normalize_snapshot(body.get("data"))
        except ValueError as e:      # DB 가 신뢰 경계 -- 모양이 틀린 결과는 적용하지 않고 실패로 남긴다
            ok, reason, error = False, "k8s_sync_bad_response", f"agent result rejected: {e}"[:500]
    duration = _int_or_none(body.get("duration_ms"))
    try:
        res = repos.k8s.receive(
            run_id, node_name=node_name, now=utc_now_iso(), ok=ok, data=data,
            fingerprint=_short_str(body.get("fingerprint"), 128),
            reason=reason if isinstance(reason, str) else None, phase=_short_str(body.get("phase"), 64),
            http_status=_int_or_none(body.get("http_status")), error=error,
            server_version=_short_str(body.get("server_version"), 64),
            duration_ms=duration if duration is not None and duration >= 0 else None,
            warnings=_warnings(body.get("warnings")),
            email_domain=getattr(request.app.state.settings, "account_email_domain", None))
    except RunClosed:
        raise HTTPException(status_code=409, detail="k8s_sync_expired")
    if res["status"] == "failed":
        record_failure_transition(repos, cluster=res["cluster"], previous_failures=res["previous_failures"],
                                  reason=res["reason"], detail=f"node {node_name}")
    return {"status": res["status"]}


@router.post("/api/agent/k8s-sync/runs/{run_id}")
async def k8s_sync_result(run_id: str, request: Request, identity: Identity = Depends(require_user)):
    """에이전트의 run 결과(§4.4). run 이 그 노드의 claimed 이고 기한(업로드 유예 포함) 안이면 수용, 아니면 409.
    본문 상한 = 투영 상한 + 1MiB(넘으면 413 k8s_sync_too_large). 큰 본문의 파싱·저장은 스레드풀에서 -- 이벤트 루프를 막지
    않는다. 적용(스냅숏 diff)은 컨트롤러 k8s-inventory 루프가 한다."""
    settings = request.app.state.settings
    limit = settings.k8s_sync_max_bytes + _RESULT_ENVELOPE_BYTES
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise HTTPException(status_code=413, detail="k8s_sync_too_large")
    # 길이 머리가 없거나(chunked) 틀려도 상한까지만 받는다 -- 다 받아 둔 뒤 재면 그 사이 메모리를 상한 넘게 쓴다(2026-10-26 리뷰).
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise HTTPException(status_code=413, detail="k8s_sync_too_large")
        chunks.append(chunk)
    raw = b"".join(chunks)
    try:
        body = await run_in_threadpool(json.loads, raw)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise HTTPException(status_code=422, detail="k8s_sync_bad_response")
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="k8s_sync_bad_response")
    node_name = _node_from(body, identity)
    return await run_in_threadpool(_receive, request, run_id, node_name, body)
