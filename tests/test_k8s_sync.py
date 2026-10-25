"""k8s 동기화 순수 규칙(dms-k8s 설계 D16·§4·§5): 배정 두 규칙·대체 1회·due·상태 derive·오류 분류·토큰 표시값·결과 검증."""
import base64
import json
import socket
import ssl

import httpx
import pytest

from dms import k8s_sync as ks

NOW = "2026-10-25T12:00:00Z"


def _cluster(**kw):
    base = {"name": "c1", "enabled": 1, "created_at": "2026-10-25T11:00:00Z", "last_attempt_at": None,
            "sync_requested_at": None, "failover_after": None, "last_success_at": None,
            "consecutive_failures": 0}
    base.update(kw)
    return base


# --- 주기·역할 ---------------------------------------------------------------------------------

def test_effective_interval_db_wins_with_floor():
    assert ks.effective_interval(None, 300) == 300
    assert ks.effective_interval({"interval_seconds": None}, 300) == 300
    assert ks.effective_interval({"interval_seconds": 600}, 300) == 600
    assert ks.effective_interval({"interval_seconds": 10}, 300) == 60       # 하한
    assert ks.effective_interval({"interval_seconds": True}, 300) == 300    # bool 은 정수가 아니다


def test_node_role():
    row = {"primary_node": "k8s-w1", "secondary_node": "k8s-w2"}
    assert ks.node_role(row, "k8s-w1") == "primary"
    assert ks.node_role(row, "k8s-w2") == "secondary"
    assert ks.node_role(row, "k8s-w3") is None
    assert ks.node_role(None, "k8s-w1") is None
    assert ks.nodes_configured(row) and not ks.nodes_configured({}) and ks.nodes_configured({"secondary_node": "x"})


# --- due·배정(D16) -----------------------------------------------------------------------------

def test_due_at_rules():
    assert ks.due_at(_cluster(), 300) == "2026-10-25T11:00:00Z"                           # 이력 없음 → 생성 시각
    assert ks.due_at(_cluster(last_attempt_at="2026-10-25T11:58:00Z"), 300) == "2026-10-25T12:03:00Z"
    assert ks.due_at(_cluster(last_attempt_at="2026-10-25T11:58:00Z",
                              sync_requested_at="2026-10-25T11:59:00Z"), 300) == "2026-10-25T11:59:00Z"
    assert ks.due_at(_cluster(enabled=0), 300) is None


def test_rule1_primary_first_then_secondary_after_90_seconds():
    c = _cluster(last_attempt_at="2026-10-25T11:55:00Z")              # due = 12:00:00
    kw = dict(interval=300, primary_configured=True)
    assert ks.claim_trigger(c, role="primary", now="2026-10-25T11:59:59Z", **kw) is None
    assert ks.claim_trigger(c, role="primary", now=NOW, **kw) == "schedule"
    assert ks.claim_trigger(c, role="secondary", now=NOW, **kw) is None
    assert ks.claim_trigger(c, role="secondary", now="2026-10-25T12:01:29Z", **kw) is None
    assert ks.claim_trigger(c, role="secondary", now="2026-10-25T12:01:30Z", **kw) == "schedule"
    assert ks.claim_trigger(c, role=None, now="2026-10-25T13:00:00Z", **kw) is None        # 지정되지 않은 노드
    # primary 가 지정되지 않았으면 secondary 가 due 에 바로 받는다
    assert ks.claim_trigger(c, role="secondary", now=NOW, interval=300, primary_configured=False) == "schedule"


def test_manual_sync_is_due_immediately_and_marked_manual():
    c = _cluster(last_attempt_at="2026-10-25T11:59:30Z", sync_requested_at="2026-10-25T11:59:50Z")
    assert ks.claim_trigger(c, role="primary", now=NOW, interval=300, primary_configured=True) == "manual"
    assert ks.claim_trigger(c, role="secondary", now=NOW, interval=300, primary_configured=True) is None


def test_rule2_failover_is_secondary_only_once():
    c = _cluster(last_attempt_at="2026-10-25T11:59:00Z", failover_after="2026-10-25T11:59:40Z")
    kw = dict(interval=300, primary_configured=True)
    assert ks.claim_trigger(c, role="secondary", now=NOW, **kw) == "failover"
    assert ks.claim_trigger(c, role="primary", now=NOW, **kw) is None              # 대체는 secondary 전용
    assert ks.claim_trigger(c, role="secondary", now="2026-10-25T11:59:39Z", **kw) is None
    # 대체 예약 = primary 의 노드성 실패만, 대체 run 자신의 실패는 다시 대체하지 않는다
    for reason in ("k8s_sync_unreachable", "k8s_sync_dns_failed", "k8s_sync_timeout", "k8s_sync_expired"):
        assert ks.failover_after_for(role="primary", trigger="schedule", reason=reason, now=NOW) == "2026-10-25T12:00:30Z"
    for reason in ("k8s_sync_tls_failed", "k8s_sync_unauthorized", "k8s_sync_forbidden", "k8s_sync_too_large",
                   "k8s_sync_bad_response", "k8s_sync_list_expired"):
        assert ks.failover_after_for(role="primary", trigger="schedule", reason=reason, now=NOW) is None
    assert ks.failover_after_for(role="secondary", trigger="schedule", reason="k8s_sync_unreachable", now=NOW) is None
    assert ks.failover_after_for(role="secondary", trigger="failover", reason="k8s_sync_unreachable", now=NOW) is None
    assert ks.failover_after_for(role="primary", trigger="manual", reason="k8s_sync_timeout", now=NOW) is not None


def test_disabled_cluster_is_never_claimed():
    c = _cluster(enabled=0, failover_after="2026-10-25T11:00:00Z")
    for role in ("primary", "secondary"):
        assert ks.claim_trigger(c, role=role, now=NOW, interval=300, primary_configured=True) is None


# --- 상태 ----------------------------------------------------------------------------------------

def _status(c, **kw):
    args = dict(token_state="set", nodes_set=True, active=False, interval=300, now=NOW)
    args.update(kw)
    return ks.derive_status(c, **args)


def test_derive_status_order():
    assert _status(_cluster(enabled=0), token_state="missing") == "disabled"
    assert _status(_cluster(), token_state="missing") == "token_missing"
    assert _status(_cluster(), token_state="unreadable") == "token_unreadable"
    assert _status(_cluster(), nodes_set=False) == "no_node"
    assert _status(_cluster(), active=True) == "syncing"
    assert _status(_cluster()) == "never"
    assert _status(_cluster(consecutive_failures=2)) == "failing"           # 한 번도 성공 못 했어도 실패가 더 정확하다
    ok = _cluster(last_success_at="2026-10-25T11:51:00Z")
    assert _status(ok) == "ok"
    assert _status(_cluster(last_success_at="2026-10-25T11:49:59Z")) == "delayed"   # > 2×주기
    assert _status(_cluster(last_success_at="2026-10-25T11:51:00Z", consecutive_failures=1)) == "failing"
    assert ks.status_reason("no_node") == "k8s_sync_no_node" and ks.status_reason("ok") is None


def test_freshness_null_is_not_zero():
    never = ks.freshness(_cluster(), status="never", interval=300, now=NOW)
    assert never == {"name": "c1", "synced_at": None, "stale": None, "status": "never"}
    fresh = ks.freshness(_cluster(last_success_at="2026-10-25T11:59:00Z"), status="ok", interval=300, now=NOW)
    assert fresh["stale"] is False and fresh["synced_at"] == "2026-10-25T11:59:00Z"
    old = ks.freshness(_cluster(last_success_at="2026-10-25T10:00:00Z"), status="delayed", interval=300, now=NOW)
    assert old["stale"] is True


# --- 오류 분류 ---------------------------------------------------------------------------------

def _chained(outer, inner):
    try:
        try:
            raise inner
        except Exception as e:
            raise outer from e
    except Exception as final:
        return final


def test_classify_exception():
    req = httpx.Request("GET", "https://k8s.example:6443/version")
    assert ks.classify_exception(_chained(httpx.ConnectError("x", request=req),
                                          socket.gaierror(-2, "Name or service not known"))) == "k8s_sync_dns_failed"
    assert ks.classify_exception(httpx.ConnectError("[Errno -3] Temporary failure in name resolution")) == "k8s_sync_dns_failed"
    assert ks.classify_exception(_chained(httpx.ConnectError("x"),
                                          ssl.SSLCertVerificationError("certificate verify failed"))) == "k8s_sync_tls_failed"
    assert ks.classify_exception(httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")) == "k8s_sync_tls_failed"
    assert ks.classify_exception(httpx.ConnectTimeout("timed out")) == "k8s_sync_unreachable"   # 방화벽 DROP
    assert ks.classify_exception(httpx.ReadTimeout("timed out")) == "k8s_sync_timeout"
    assert ks.classify_exception(httpx.ConnectError("[Errno 111] Connection refused")) == "k8s_sync_unreachable"
    assert ks.classify_exception(httpx.RemoteProtocolError("bad")) == "k8s_sync_bad_response"
    assert ks.classify_exception(ValueError("x")) == "k8s_sync_bad_response"


def test_classify_http_status():
    assert ks.classify_http_status(401) == "k8s_sync_unauthorized"
    assert ks.classify_http_status(403) == "k8s_sync_forbidden"
    assert ks.classify_http_status(410) == "k8s_sync_list_expired"
    assert ks.classify_http_status(500) == "k8s_sync_bad_response"


# --- 토큰 ----------------------------------------------------------------------------------------

def _jwt(claims):
    def enc(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")
    return f"{enc({'alg': 'RS256', 'kid': 'k'})}.{enc(claims)}.c2lnbmF0dXJl"


def test_jwt_expiry_is_read_without_verification():
    assert ks.jwt_expires_at(_jwt({"exp": 1793000000, "sub": "system:serviceaccount:a:b"})) == "2026-10-26T07:33:20Z"
    assert ks.jwt_expires_at(_jwt({"sub": "x"})) is None                     # 장기 토큰 Secret 은 exp 가 없다
    assert ks.jwt_expires_at("not-a-jwt") is None
    assert ks.jwt_expires_at(None) is None
    assert ks.jwt_expires_at(_jwt({"exp": "soon"})) is None


def test_unwrap_base64_wrapped_jwt():
    token = _jwt({"exp": 1793000000})
    wrapped = base64.b64encode(token.encode()).decode()                       # kubectl get secret -o jsonpath='{.data.token}'
    assert ks.unwrap_token(f"  {wrapped}\n") == token
    assert ks.unwrap_token(token) == token
    assert ks.unwrap_token(" static-token-abc ") == "static-token-abc"       # JWT 가 아닌 토큰은 그대로
    assert ks.unwrap_token(base64.b64encode(b"just text").decode()) == base64.b64encode(b"just text").decode()


def test_token_problem():
    assert ks.token_problem("abc.def") is None
    assert ks.token_problem("") == "empty"
    assert ks.token_problem("a b") is not None and ks.token_problem("a\nb") is not None
    assert ks.token_problem("x" * 20000) == "too long"
    assert ks.token_problem("토큰") is not None


# --- 결과 검증 ---------------------------------------------------------------------------------

def _data(**kw):
    base = {"storage_classes": [{"name": "sc1", "provisioner": "p", "parameters": {"a": "b"}}],
            "pvcs": [{"namespace": "n", "name": "p1", "volume_name": "v1", "phase": "Bound",
                      "access_modes": ["ReadWriteMany"]}],
            "pvs": [{"name": "v1", "volume_attributes": {"subDir": "n/p1"}}],
            "role_bindings": [{"namespace": "n", "name": "alice-clusterrole-edit", "role_kind": "ClusterRole",
                               "role_name": "kubeflow-edit", "subjects": [{"kind": "User", "name": "alice"}]}]}
    base.update(kw)
    return base


def test_normalize_snapshot_fills_every_field_and_keeps_optional_nulls():
    out = ks.normalize_snapshot(_data(pvs=None, storage_classes=None))
    assert out["pvs"] is None and out["storage_classes"] is None
    assert set(out["pvcs"][0]) == {"namespace", "name", "uid", "storage_class", "volume_name", "phase", "capacity",
                                   "access_modes", "created_at"}
    assert out["role_bindings"][0]["subjects"] == [{"kind": "User", "name": "alice", "namespace": None}]
    full = ks.normalize_snapshot(_data())
    assert full["pvs"][0]["volume_attributes"] == {"subDir": "n/p1"}


@pytest.mark.parametrize("bad", [
    None, [], {"pvcs": [], "role_bindings": None},
    {"pvcs": None, "role_bindings": []},
    _data(pvcs=[{"namespace": "n"}]),                                               # name 없음
    _data(pvcs=[{"namespace": "n", "name": 3}]),
    _data(pvcs=[{"namespace": "n", "name": "a"}, {"namespace": "n", "name": "a"}]),  # 중복
    _data(role_bindings=[{"namespace": "n", "name": "x", "subjects": "nope"}]),
    _data(pvs=[{"name": "v", "volume_attributes": {"k": {"nested": 1}}}]),
    _data(storage_classes=[{"name": "s"}, {"name": "s"}]),
    _data(pvcs=[{"namespace": "n", "name": "a\x00"}]),
])
def test_normalize_snapshot_rejects_malformed(bad):
    with pytest.raises(ValueError):
        ks.normalize_snapshot(bad)


def test_big_role_binding_does_not_reject_the_snapshot():
    """팀 바인딩 하나에 subjects 가 많아도 스냅숏 전체를 거부하지 않는다(2026-10-26 리뷰 — 65명에서 동기화가 멈췄다).
    상한(_MAX_SUBJECTS)을 넘는 몫은 잘라 낸다 -- subjects 는 불일치 경고·표시에만 쓰인다."""
    many = [{"kind": "User", "name": f"u{i}@example.com"} for i in range(ks._MAX_SUBJECTS + 10)]
    out = ks.normalize_snapshot(_data(role_bindings=[{"namespace": "n", "name": "alice-clusterrole-edit",
                                                     "role_kind": "ClusterRole", "role_name": "kubeflow-edit",
                                                     "subjects": many}]))
    assert len(out["role_bindings"][0]["subjects"]) == ks._MAX_SUBJECTS
    assert ks.normalize_snapshot(_data(role_bindings=[{"namespace": "n", "name": "b", "subjects": many[:65]}]))


def test_snapshot_counts():
    counts = ks.snapshot_counts(ks.normalize_snapshot(_data(pvs=None)))
    assert counts == {"storage_classes": 1, "pvs": None, "pvcs": 1, "pvcs_bound": 1, "namespaces": 1,
                      "bindings": 1, "recognized_bindings": 1, "users": 1}


def test_reason_sets_are_disjoint_and_failover_is_a_subset():
    assert ks.FAILOVER_REASONS <= ks.SYNC_REASONS
    assert not ks.SYNC_REASONS & ks.STATUS_REASONS
