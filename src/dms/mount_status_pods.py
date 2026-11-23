"""마운트 현황의 DMS 파드 덧붙임(2026-11-23) -- dms-api·dms-controller 파드가 뜬 노드 꼬리표와 그 파드 자신의 아티팩트 경로 점검.

왜: 같은 날 사고(dms-api 가 공용 스토리지 미마운트 노드에 떠 아티팩트·scan 리포트를 못 읽음)의 서명은 「DMS 공용 칸이 로컬
디렉터리인 노드 + 그 노드에 뜬 dms-api」 조합이다. 공용 코어(mount_status.build)는 에이전트 보고만 판정해 뒤쪽 절반을 모른다 --
이 덧붙임이 같은 행에 꼬리표(roles 의 api_pod·controller_pod)를 단다.

경계: 공용 코어 밖이고(코어는 dms-ssc 와 같은 계약 -- 에이전트 보고만 본다), k8s 덧붙임(mount_status_k8s)과도 따로다. 재료
(api_base_checks·control_state 의 컨트롤러 점검 노드)는 dms-ssc 에도 같은 커밋(4add57bf)으로 있어 이 파일은 두 라인에 그대로
옮길 수 있다. 순수 함수만 둔다(I/O 없음 -- 라우트 routes_mount_status 가 재료를 모아 넘긴다).

재료·신선도(**DB 만** 읽는다 -- 화면이 10초마다 묻으므로 k8s API 를 요청마다 부르지 않는다. 파드가 어느 노드에 있는지는 각 파드가
자기 점검 때 남긴 기록이 말한다):
  - API 파드: api_base_checks 행(api.base_monitor 가 파드마다 주기 upsert -- 노드는 env DMS_NODE_NAME, 없으면 자기 파드 조회).
    마지막 활동(checked_at, 없으면 started_at)이 신선 창(base_monitor.fresh_window_seconds -- 주기 3배·최소 180초) 안이고 node_name
    이 있는 행만 -- 죽은 파드의 행은 창이 지나면 빠진다(24시간 보존 행 전체는 아티팩트 경로 화면의 API 파드 목록 몫). 시작 게이트에
    막힌 새 파드(Ready 아님 -- Service 밖)도 행을 남기므로 꼬리표가 붙는다(gate_state waiting).
  - 컨트롤러: control_state.artifact_base_check_node(컨트롤러가 reconcile 주기로 점검하며 남긴다). check_at 이 신선 창(reconcile
    주기 3배·최소 180초 -- controller_fresh_seconds) 안일 때만 -- 다른 노드로 옮겨 간 컨트롤러의 옛 노드가 남지 않게. 인스턴스
    이름은 기록되지 않는다(null).
  - 점검 결과(ok·reason)는 그 점검이 **지금** base(uri)를 본 것일 때만 싣는다 -- 아니면 null(모름; base 를 막 바꾼 직후).
    꼬리표(파드가 그 노드에 있다는 사실)는 uri 와 무관하게 단다.

얹는 것:
  - 최상위 control_pods: [{role, instance, node_name, ok, reason, read_failed, checked_at, gate_state, in_table}] -- 신선한 파드 전부
    (API 파드 → 컨트롤러, 그 안에서 노드·인스턴스 이름순). read_failed = ok False ∧ 권한(posture) 사유가 아님 -- 「경로를 쓸 수
    있느냐」만 본다(소유자·o+w 같은 「안전하냐」는 노드 마운트 문제가 아니다; 관리 화면 ArtifactBaseChecks 의 POSTURE_REASONS 와 같은
    가름). 이름과 달리 읽기 실패만이 아니다 -- 컨트롤러 점검의 쓰기 왕복 실패(artifact_base_not_writable: 읽기 전용 마운트·프로브
    쓰기 실패)와 점검 시간 초과도 든다(포탈 글자는 중립인 「점검 실패」 -- mountTerms.podCheckText).
    in_table = 그 노드가 표의 행(에이전트 보고가 있는 노드)인가 -- 아니면 화면이 각주로 따로 보인다(예: prod 는 제어면을
    control-plane 마스터에 두고 에이전트 DaemonSet 은 NoSchedule taint 를 견디지 않는다 -- overlays/prod/patch-placement).
  - 행 roles: api_pod·controller_pod(행당 한 번 -- 같은 노드의 API 파드 둘도 꼬리표 하나; 상세는 control_pods).
  - 「문제 있는 노드」: read_failed 파드가 있는 신선한 행은, 공용 디렉터리 칸이 이미 문제(코어 SHARED_PROBLEM_STATES)가 아니면
    problem_count 를 1 더하고 totals.problem_nodes 를 다시 센다 -- DMS 공용 열은 **한 칸**이다(에이전트 보고·이 노드 파드 점검 중
    하나라도 문제면 1 -- dms-ssc routes_mounts 와 같은 규칙). 에이전트가 공용 디렉터리를 「정상·있음」으로 보는데 그 노드의 파드가
    못 읽는 경우(파드 hostPath·마운트 전파·옛 에이전트)가 「문제 있는 노드」 필터에서 빠지지 않게. 리포트 지연 행(problem_count
    null -- 이미 문제)과 공용 디렉터리 열이 없는 응답(base 없음)은 그대로 둔다. totals 의 칸·공용 디렉터리 수(shared_problem 등)는
    에이전트 보고 그대로다(코어 계약).

리포트·DB 값은 스키마 검증 없이 읽으므로(DB 가 신뢰 경계) 모든 필드를 _str·_ok 로 읽고 예외를 내지 않는다.
"""
from .mount_status import SHARED_PROBLEM_STATES

API_POD = "api_pod"
CONTROLLER_POD = "controller_pod"
POD_ROLES = (API_POD, CONTROLLER_POD)
# 권한·모양(posture) 사유 -- artifact_base.posture_problem·allowlist_reason 이 내는 것. 「읽을 수 있느냐」가 아니라 「안전하냐」라
# read_failed 로 세지 않는다(tests/test_mount_status_pods.py 가 ⊆ ARTIFACT_BASE_CHECK_REASONS 를 고정한다).
POSTURE_REASONS = frozenset({
    "artifact_base_outside_allowlist", "artifact_base_not_owned", "artifact_base_world_writable",
    "artifact_base_group_writable", "artifact_base_not_traversable",
})
_FRESH_FLOOR_SECONDS = 180


def controller_fresh_seconds(reconcile_interval_seconds) -> int:
    """컨트롤러 점검 기록이 「신선」한 창 -- reconcile 주기 3배, 최소 180초(api 쪽 fresh_window_seconds 와 같은 모양)."""
    try:
        interval = int(reconcile_interval_seconds)
    except (TypeError, ValueError):
        interval = 0
    return max(3 * interval, _FRESH_FLOOR_SECONDS)


def _str(v):
    return v if isinstance(v, str) and v != "" else None


def _ok(v):
    """DB 값 → True/False/None. sqlite 는 0/1 정수, PG 는 bool -- None 은 모름(아직 점검 전)이지 실패가 아니다."""
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, int):
        return v != 0
    return None


def _pod(role, *, instance, node_name, ok, reason, checked_at, gate_state) -> dict:
    return {"role": role, "instance": instance, "node_name": node_name, "ok": ok, "reason": reason,
            "read_failed": ok is False and reason not in POSTURE_REASONS,
            "checked_at": checked_at, "gate_state": gate_state, "in_table": False}


def pods_from(api_rows, control_row, *, base_uri, api_cutoff: str, controller_cutoff: str) -> list:
    """재료 → control_pods(in_table 은 decorate 가 채운다). api_rows = api_base_checks.list_recent 행, control_row =
    control.control_state(), base_uri = 지금 유효한 base, *_cutoff = 신선 창의 시작 시각(ISO -- 문자열 비교)."""
    pods = []
    for r in api_rows or []:
        if not isinstance(r, dict):
            continue
        node = _str(r.get("node_name"))
        last = _str(r.get("checked_at")) or _str(r.get("started_at"))
        if node is None or last is None or last < api_cutoff:
            continue
        current = bool(base_uri) and r.get("uri") == base_uri
        pods.append(_pod(API_POD, instance=_str(r.get("instance")), node_name=node,
                         ok=_ok(r.get("ok")) if current else None,
                         reason=_str(r.get("reason")) if current else None,
                         checked_at=_str(r.get("checked_at")), gate_state=_str(r.get("gate_state"))))
    row = control_row if isinstance(control_row, dict) else {}
    node = _str(row.get("artifact_base_check_node"))
    at = _str(row.get("artifact_base_check_at"))
    if node is not None and at is not None and at >= controller_cutoff:
        current = bool(base_uri) and row.get("artifact_base_check_uri") == base_uri
        pods.append(_pod(CONTROLLER_POD, instance=None, node_name=node,
                         ok=_ok(row.get("artifact_base_check_ok")) if current else None,
                         reason=_str(row.get("artifact_base_check_reason")) if current else None,
                         checked_at=at, gate_state=None))
    pods.sort(key=lambda p: (POD_ROLES.index(p["role"]), p["node_name"], p["instance"] or ""))
    return pods


def decorate(body: dict, pods: list) -> None:
    """body(mount_status.build 의 결과 -- 이 요청의 자기 dict)를 제자리에서 고친다. 규칙은 모듈 docstring."""
    rows = {r.get("node_name"): r for r in body.get("nodes", [])}
    for pod in pods:
        row = rows.get(pod["node_name"])
        pod["in_table"] = row is not None
        if row is not None and pod["role"] not in row["roles"]:
            row["roles"].append(pod["role"])
    if body.get("shared_dir") is not None:
        changed = False
        for name in sorted({p["node_name"] for p in pods if p["read_failed"]}):
            row = rows.get(name)
            if row is None or row.get("problem_count") is None:
                continue
            shared = row.get("shared_dir")
            if shared is not None and shared.get("state") in SHARED_PROBLEM_STATES:
                continue                    # DMS 공용 열은 한 칸 -- 이미 센 문제를 두 번 세지 않는다
            row["problem_count"] += 1
            row["problem"] = True
            changed = True
        if changed:
            body["totals"]["problem_nodes"] = sum(1 for r in body.get("nodes", []) if r.get("problem"))
    body["control_pods"] = pods
