"""노드 기록 정리(2026-11-25 -- OA 사이트 「사라지지 않는 stale 노드」).

증상: 호스트 이름을 바꾸면 RKE2 가 노드를 새 이름으로 다시 등록하고 에이전트(노드 이름 = spec.nodeName -- 50-agent-daemonset
DMS_AGENT_NODE_NAME)는 새 이름으로 보고한다. 옛 이름의 agent_nodes 행은 보고가 올 때만 갈아 끼우므로 지울 길이 없어 「리포트
지연」으로 영원히 남았다(같은 장비 두 줄, 노드 수·마운트 현황·지표 잡음). 배치 판정은 fresh 보고만 쓰므로 그 행이 작업을 받지는
않는다 -- 문제는 표시·집계 잡음과 운영자가 정리할 수 없다는 것이다.

정리 길은 둘이고 **같은 저장소 몸통**(AgentsRepository.forget_node -- 한 트랜잭션·조건부 DELETE·감사 agent_node)과 **같은
in_use 가드**(node_references)를 쓴다:
- 사람: DELETE /api/admin/nodes/{name}(api/routes_nodes.py -- 세션 관리자만, 공유 토큰 403). 포탈 노드 화면의 「리포트 지연」
  행에만 「기록 삭제」 버튼. 감사 operation='delete', actor=세션 사용자.
- 자동(선택, 기본 꺼짐): 컨트롤러 retention 루프 끝(forget_absent_nodes_once). DMS_AGENT_NODE_FORGET_SECONDS 이상 보고가
  없고 **쿠버네티스에 그 Node 가 더는 없을 때만** 지운다. k8s 조회 실패·빈 목록·이름 없음은 「모름」이라 지우지 않는다(null ≠
  false). 감사 operation='auto_forget', actor='system:agent-node-forget'.

지우지 않는 경우(거절 사유 -- 화면·문서가 이 코드를 쓴다):
- node_not_found(404): agent_nodes 에 행이 없다.
- node_fresh(409): 최근 보고가 신선하다(GET 의 fresh 와 같은 판정). 살아 있는 노드를 지우면 다음 보고에 다시 생겨 혼란스럽다.
- node_in_use(409, 상세 `node_in_use:<토큰,...>[;requests=<요청 id,...>]`): 그 노드를 가리키는 설정·진행 중 작업이 있다.
  토큰은 REF_KINDS 순서다 --
  build_node(control_state.build_node_name), k8s_primary·k8s_secondary(k8s_sync_settings.primary_node·secondary_node),
  active_build(그 노드의 Pending·Running 빌드), active_jobs(비종단 잡의 worker_pool.candidates 에 이름 -- 깨진 JSON 은
  fail-closed). 비종단 잡을 막는 이유: stepper·node_exclusions.blocked_nodes 가 latest_reports(신선도 무관)로 그 노드의 마지막
  cordon 을 본다 -- 행을 지우면 fail-open 이고, ConfirmPending 잡이 확인 뒤 사라진 노드로 제출되면 gang 이 영원히 Pending
  이다. plans 표에는 노드 열이 없고, 표시용 포인터(control_state.artifact_base_check_node·api_base_checks.node_name)는
  근거가 아니다. active_jobs 면 막는 요청 id 를 최대 BLOCKING_REQUESTS_SHOWN 개 `;requests=` 뒤에 붙인다(판정과 같은 스냅숏 --
  작업 목록에 노드 필터가 없어 운영자가 찾을 길이 없었다, 포탈 창이 요청 상세 링크로 보인다).

받아들이는 위험(repositories/agents.forget_node docstring): 이력 삭제가 한 트랜잭션이라 큰 노드는 수 초, retention 청크와의
드문 PG 교착(500 -- 다시 누르면 된다), in_use 판정 뒤의 아주 좁은 계획 창(planner 는 fresh 노드만 쓴다).

같은 장비의 새 이름 힌트(rename_hints): 에이전트가 보고에 machine_key(호스트 /etc/machine-id 의 HMAC-SHA256 앞 16hex --
agent/probes.probe_machine_key, 원문 금지)를 싣는다. stale 행의 키와 같은 키를 가진, 이름이 다른 fresh 행이 **정확히 1개**면
그 이름을 힌트로 낸다(복제 VM 템플릿처럼 2개 이상이면 내지 않는다). 키가 없으면(옛 에이전트·direct 모드) 힌트도 없다 --
에이전트 롤아웃 전에 이미 stale 이 된 옛 이름에는 나오지 않는다. 힌트는 표시뿐이다: 노드 이름 = k8s Node 이름이 계약이라 옛·새
이름을 합치거나 이름을 바꾸지 않는다.
"""
import re

from .db import iso_plus, utc_now_iso
from .repositories.agents import NodeForgetRefused

# in_use 근거 토큰 -- 이 순서로 판정·응답한다(포탈 ForgetNodeDialog 가 토큰을 한국어로 옮긴다 -- 바꾸면 거기도).
REF_KINDS = ("build_node", "k8s_primary", "k8s_secondary", "active_build", "active_jobs")

AUTO_FORGET_ACTOR = "system:agent-node-forget"
COMPONENT = "retention"
# 자동 정리 한 틱에 **지우는** 노드 수의 상한(틱 예산 -- 노드마다 이력 DELETE 가 따른다). 남은 것은 다음 retention 틱. in_use
# 거절은 세지 않는다(읽기만 하는 짧은 트랜잭션) -- 시도 수에 걸면 이름순 앞자리의 in_use 노드가 매 틱 예산을 다 써서 뒤의 지울 수
# 있는 노드가 영원히 남는다.
FORGET_BATCH_LIMIT = 20
# node_in_use 거절에 싣는 막는 요청 id 수(상세 문자열·이벤트 payload 크기).
BLOCKING_REQUESTS_SHOWN = 5
# 상세에 싣는 요청 id 모양(정상은 uuid4().hex). DB 는 신뢰 경계라 `,`·`;` 가 든 값이 합성 상세를 깨지 않게 모양이 다르면 싣지 않는다.
_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")

# 보고의 machine_key 모양(agent/probes.MACHINE_KEY_HEX = 16). 보고는 DB 신뢰 경계 밖이라 모양이 다르면 없는 것으로 본다.
MACHINE_KEY_RE = re.compile(r"[0-9a-f]{16}")


def node_references(repos, node_name: str) -> tuple:
    """그 노드를 가리키는 설정·진행 중 작업 -> REF_KINDS 순서의 토큰 튜플(빈 튜플 = 지워도 된다). 읽기만 한다 --
    forget_node 의 트랜잭션 안에서 같은 커넥션으로 불린다(중첩 transaction 금지)."""
    return _references(repos, node_name)[0]


def _references(repos, node_name: str) -> tuple:
    """(node_references 의 토큰, active_jobs 근거의 요청 id 최대 BLOCKING_REQUESTS_SHOWN 개 -- 잡 job_id 순, 중복 제거)."""
    refs = []
    state = repos.control.control_state() or {}
    if state.get("build_node_name") == node_name:
        refs.append("build_node")
    k8s = repos.k8s.get_settings() or {}
    if k8s.get("primary_node") == node_name:
        refs.append("k8s_primary")
    if k8s.get("secondary_node") == node_name:
        refs.append("k8s_secondary")
    if repos.builds.active_on_node(node_name):
        refs.append("active_build")
    requests: list = []
    jobs = repos.data_jobs.active_jobs_on_node(node_name)
    if jobs:
        refs.append("active_jobs")
        for job in jobs:
            rid = job.get("request_id")
            if isinstance(rid, str) and _REQUEST_ID_RE.fullmatch(rid) and rid not in requests:
                requests.append(rid)
    return tuple(refs), tuple(requests[:BLOCKING_REQUESTS_SHOWN])


def forget_node(repos, node_name: str, *, stale_before_iso: str, actor: str, operation: str) -> dict:
    """사람(라우트)과 자동 정리가 같이 쓰는 입구 -- 저장소 몸통에 in_use 판정을 꽂는다. 거절은 NodeForgetRefused 이고,
    node_in_use 면 판정과 같은 스냅숏의 막는 요청 id(.requests)를 싣는다."""
    seen: dict = {}

    def references(name):
        refs, requests = _references(repos, name)
        seen["requests"] = requests
        return refs

    try:
        return repos.agents.forget_node(
            node_name, stale_before_iso=stale_before_iso, actor=actor, operation=operation,
            references=references)
    except NodeForgetRefused as exc:
        if exc.code != "node_in_use":
            raise
        raise NodeForgetRefused(exc.code, exc.refs, seen.get("requests", ())) from None


def k8s_node_names(node_lister) -> "set | None":
    """k8s Node 이름 집합. None = 모름: 목록이 list 가 아니거나 비었거나 문자열 이름이 하나도 없다. 빈 목록을 「노드
    없음」으로 읽으면 안 된다 -- 스텁 백엔드의 node_lister 는 `lambda: []` 라 로컬·CI 의 오래된 행이 전부 지워진다. 조회
    예외는 호출자에게 그대로 올린다(forget_absent_nodes_once 가 이벤트로 남긴다)."""
    items = node_lister()
    if not isinstance(items, list):
        return None
    names = {i["name"] for i in items
             if isinstance(i, dict) and isinstance(i.get("name"), str) and i["name"]}
    return names or None


def forget_absent_nodes_once(repos, settings, node_lister, *, now_iso: "str | None" = None) -> dict:
    """컨트롤러 retention 루프의 끝(controller._retention_step)에서 부른다. 꺼져 있거나(0) 후보가 없으면 k8s 를 부르지
    않는다. 후보 = 마지막 보고가 DMS_AGENT_NODE_FORGET_SECONDS 이상 지난 행(설정 검증이 FORGET ≥ STALE 을 보장하므로
    stale). 그중 k8s Node 목록에 이름이 없는 것만(대소문자 그대로 -- 에이전트 node_name = spec.nodeName) 같은 몸통·같은
    in_use 가드로 지운다 -- 틱당 지운 수가 FORGET_BATCH_LIMIT 에 닿으면 멈춘다(in_use 거절은 세지 않는다).

    「모름」은 지우지 않는다: node_lister 없음(테스트 배선) → 조용히, 조회 예외 → 이벤트 agent_node_forget_skipped
    (k8s_list_failed -- 403 이면 ClusterRoleBinding dms-controller-nodes-readonly 가 아직 없다: S11), 빈 목록·이름 없음 → 같은
    이벤트(no_nodes_listed). node_in_use 는 모아 agent_node_forget_in_use, node_fresh·node_not_found 는 경합(그 사이 다시
    보고했거나 사람이 지움)이라 조용히 넘긴다. DB 예외는 올린다(run_all_once 의 루프별 격리 -- prune 들은 이미 끝났다)."""
    forget_seconds = settings.agent_node_forget_seconds
    if forget_seconds <= 0:
        return {"enabled": False}
    now = now_iso or utc_now_iso()
    before = iso_plus(now, -forget_seconds)
    candidates = repos.agents.node_names_reported_before(before)
    result = {"enabled": True, "candidates": len(candidates), "k8s": None, "deleted": [], "in_use": {},
              "in_use_requests": {}}
    if not candidates:
        return result
    if node_lister is None:
        result["k8s"] = "unknown"
        return result
    try:
        names = k8s_node_names(node_lister)
    except Exception as exc:
        repos.observability.record_event(
            component=COMPONENT, severity="warning", event_type="agent_node_forget_skipped",
            message=f"{type(exc).__name__}: {exc}"[:300],
            payload={"reason": "k8s_list_failed", "candidates": len(candidates)})
        result["k8s"] = "list_failed"
        return result
    if names is None:
        repos.observability.record_event(
            component=COMPONENT, severity="warning", event_type="agent_node_forget_skipped",
            message="k8s Node list came back empty -- not forgetting anything",
            payload={"reason": "no_nodes_listed", "candidates": len(candidates)})
        result["k8s"] = "no_nodes_listed"
        return result
    result["k8s"] = "ok"
    for name in (n for n in candidates if n not in names):
        if len(result["deleted"]) >= FORGET_BATCH_LIMIT:
            break
        try:
            forget_node(repos, name, stale_before_iso=before, actor=AUTO_FORGET_ACTOR, operation="auto_forget")
        except NodeForgetRefused as exc:
            if exc.code == "node_in_use":
                result["in_use"][name] = list(exc.refs)
                if exc.requests:
                    result["in_use_requests"][name] = list(exc.requests)
            continue
        result["deleted"].append(name)
    if result["in_use"]:
        payload = {"nodes": result["in_use"]}
        if result["in_use_requests"]:
            payload["requests"] = result["in_use_requests"]
        repos.observability.record_event(
            component=COMPONENT, severity="warning", event_type="agent_node_forget_in_use",
            message="nodes absent from Kubernetes are still referenced -- not forgotten",
            payload=payload)
    if result["deleted"]:
        repos.observability.record_event(
            component=COMPONENT, severity="info", event_type="agent_node_forgotten",
            message=f"forgot {len(result['deleted'])} node record(s) absent from Kubernetes",
            payload={"nodes": result["deleted"]})
    return result


def machine_key_of(report) -> "str | None":
    """보고의 machine_key -- dict 이고 값이 소문자 hex 16자일 때만. 그 밖(옛 에이전트·모양 불량)은 None(힌트 없음)."""
    if not isinstance(report, dict):
        return None
    key = report.get("machine_key")
    if isinstance(key, str) and MACHINE_KEY_RE.fullmatch(key):
        return key
    return None


def rename_hints(nodes: list) -> dict:
    """list_nodes 결과 -> {stale 노드 이름: 같은 장비로 보이는 fresh 노드 이름}. stale 행의 키와 같은 키를 가진, 이름이
    다른 fresh 행이 정확히 1개일 때만 낸다(0개·2개 이상·키 없음은 힌트 없음). fresh 행은 힌트를 받지 않는다."""
    fresh_by_key: dict = {}
    for n in nodes:
        if n.get("fresh") is True:
            key = machine_key_of(n.get("report"))
            if key is not None:
                fresh_by_key.setdefault(key, set()).add(n.get("node_name"))
    hints = {}
    for n in nodes:
        if n.get("fresh") is True:
            continue
        key = machine_key_of(n.get("report"))
        if key is None:
            continue
        others = fresh_by_key.get(key, set()) - {n.get("node_name")}
        if len(others) == 1:
            hints[n["node_name"]] = next(iter(others))
    return hints
