"""노드 배치 제외(2026-10-02 사용자 결정 -- "문제가 생긴 노드에 더 이상 잡이 안 들어가게").

DMS 잡의 노드는 k8s 스케줄러가 아니라 planner 가 고른다: 신선한 에이전트 보고에서 후보를 골라 앞에서 N대를
worker_pool.candidates 에 굳히고(planner._plan_one), 매니페스트가 그 목록에 required nodeAffinity +
노드당 워커 1개(podAntiAffinity)로 고정한다(execution_manifests._worker_affinity). 그래서 `kubectl cordon`
만으로는 막히지 않는다 -- 에이전트(DaemonSet)는 cordon 을 자동 허용받아 계속 보고하고, 그 노드가 낀 잡은
gang 이 안 서서 Pending 에 영원히 멈춘다(데이터 잡엔 Pending 상한이 없다).

막는 길은 두 갈래이고 판정은 `blocked_nodes` 하나로 모은다:
- **배치 제외**(이 테이블): 관리자가 포탈에서 노드를 고르고 사유를 적는다. 행 = 제외 중, 지우면 다시 포함.
  에이전트는 계속 보고하므로 마운트·지표를 보며 복구 시점을 판단할 수 있다(④ 에이전트 내리기와의 차이).
- **k8s 스케줄 불가**(cordon·NoSchedule/NoExecute taint): 에이전트가 자기 노드 상태를 보고(report.k8s_node,
  agent/probes.probe_k8s_node)하고 그 값이 False 일 때만 막는다. null(조회 실패·클러스터 밖)은 모름 --
  막지 않는다(fail-open: 권한·네트워크 문제가 전 노드를 배치 불가로 만들면 안 된다).

강제 지점(셋 다 이 모듈의 함수를 쓴다):
1. planner 후보 선정(placement.eligible_nodes -- 도구 검사 뒤·신원 검사 앞): 새 계획에서 빠진다. 0대면 거부
   사유 nodes_excluded(기다려도 풀리지 않는 사유라 유예하지 않는다).
2. stepper 제출 직전(_build_spec -- preflight·preview·exec_preflight·execution 모두 지난다): 계획 뒤에 막힌
   노드가 후보에 굳어 있으면 node_excluded_at_step 으로 종단(fail-closed). 남은 노드로 다시 계획하지 않는다 --
   노드 수·프로세스 수·신원이 worker_pool 에 함께 굳어 있다. 배치 항목도 그대로 종료된다(사용자 결정).
3. 컨펌(routes_jobs.confirm_job): ConfirmPending 은 stepper 가 건드리지 않으므로 컨펌 시점에 409 node_excluded
   + 종단.
4. stepper 폴링의 PENDING(제출됐지만 아직 스케줄 전 -- Volcano 큐·gang 대기, preflight 파드 대기): 같은 재검사로 종단
   (적대적 리뷰 -- 제출 직전 검사만으로는 큐 대기 중 막힌 노드를 못 봐, cordon 이면 영원히 멈추고 배치 제외면 결국
   그 노드에 앉았다).
노드에서 **이미 실행 중인**(RUNNING) 잡은 건드리지 않는다(maintenance·drain 관례). 관리자·배치·토큰 제출에도 예외
없다 -- 노드 상태 문제다. 해제(다시 포함)는 다음 계획부터 반영되고, 이미 거부·종단된 요청을 되살리지는 않는다.
k8s 일시 조건 taint(node.kubernetes.io/{not-ready,unreachable,*-pressure,network-unavailable})는 새 계획에서만 피하고
이미 계획된 잡은 종단하지 않는다(k8s_hard_block -- 저절로 풀리는 상태다).
"""
from ..db import Database, dump_json, utc_now_iso

# 노드별 탈락 사유(placement rejections 와 같은 문자열 -- 화면 계약 아님).
NODE_EXCLUDED = "node_excluded"
NODE_UNSCHEDULABLE = "node_unschedulable"
MAX_EXCLUSION_REASON = 500


def k8s_unschedulable(report) -> bool:
    """에이전트 보고의 k8s 노드 상태가 **명시적으로** 스케줄 불가인가(일시 조건 포함). 키 부재·null(모름)은 False --
    fail-open. planner 후보 선정이 쓴다: 지금 못 올라가는 노드는 새 계획에서 피한다."""
    node = (report or {}).get("k8s_node") if isinstance(report, dict) else None
    return isinstance(node, dict) and node.get("schedulable") is False


def k8s_hard_block(report) -> bool:
    """스케줄 불가이되 **일시 조건이 아닌** 것(cordon·관리자 taint). 이미 계획·제출된 잡을 종단하는 판정
    (blocked_nodes)은 이것만 본다 -- kubelet 조건 taint(node.kubernetes.io/{not-ready,memory-pressure,...})는
    대개 저절로 풀리므로, 그 순간 잡을 죽이면 일시 압박 하나에 계획된 잡들이 사라진다(리뷰). 그런 잡은 기다리다
    조건이 풀리면 스케줄된다. 에이전트가 transient=true 로 표시한다(agent/probes.k8s_schedulability)."""
    node = (report or {}).get("k8s_node") if isinstance(report, dict) else None
    return isinstance(node, dict) and node.get("schedulable") is False and node.get("transient") is not True


def candidate_nodes(candidates) -> list:
    """worker_pool.candidates({"primary": [...]} 또는 {"source": [...], "destination": [...]}) -> 노드 이름 목록.
    모양이 깨진 값(DB 신뢰 경계)은 문자열만 줍는다."""
    out = []
    if isinstance(candidates, dict):
        for nodes in candidates.values():
            if isinstance(nodes, list):
                out.extend(n for n in nodes if isinstance(n, str))
    return out


def blocked_nodes(repos, candidates) -> dict:
    """후보 중 지금 막힌 노드 -> 사유(node_excluded | node_unschedulable). 배치 제외가 우선(관리자 판단).
    k8s 상태는 노드의 **마지막** 보고 기준이다(신선도 무관 -- 마지막으로 알려진 cordon 을 잊지 않는다). 일시 조건
    taint 는 막지 않는다(k8s_hard_block)."""
    names = candidate_nodes(candidates)
    if not names:
        return {}
    excluded = repos.node_exclusions.excluded_names()
    latest = {n["node_name"]: n.get("report") for n in repos.agents.latest_reports(names)}
    out = {}
    for name in names:
        if name in excluded:
            out[name] = NODE_EXCLUDED
        elif k8s_hard_block(latest.get(name)):
            out[name] = NODE_UNSCHEDULABLE
    return out


class NodeExclusionsRepository:
    def __init__(self, db: Database):
        self._db = db

    def _audit(self, operation, node_name, before, after, actor):
        self._db.execute(
            """INSERT INTO audit_log (mutation_class, operation, target_key, actor,
                   before_state, after_state, at)
               VALUES ('node_exclusion', :op, :key, :actor, :b, :a, :at)""",
            {"op": operation, "key": node_name, "actor": actor,
             "b": dump_json(before) if before else None,
             "a": dump_json(after) if after else None, "at": utc_now_iso()})

    def list(self) -> list:
        return self._db.query("SELECT * FROM node_exclusions ORDER BY node_name")

    def get(self, node_name: str):
        return self._db.query_one("SELECT * FROM node_exclusions WHERE node_name = :n", {"n": node_name})

    def excluded_names(self) -> set:
        return {r["node_name"] for r in self._db.query("SELECT node_name FROM node_exclusions")}

    def exclude(self, node_name: str, *, reason, actor: str):
        """제외(멱등): 이미 제외 중이면 기존 행을 그대로 돌려준다(사유도 덮지 않는다 -- 처음 막은 이유가 남는다).
        새로 막을 때만 감사 행을 남긴다. reason 은 trim 후 빈 값이면 None, 길이 상한 MAX_EXCLUSION_REASON(호출자가 검증)."""
        with self._db.transaction():
            existing = self.get(node_name)
            if existing is not None:
                return existing
            row = {"node_name": node_name, "reason": reason, "created_by": actor, "created_at": utc_now_iso()}
            self._db.execute(
                """INSERT INTO node_exclusions (node_name, reason, created_by, created_at)
                   VALUES (:node_name, :reason, :created_by, :created_at) ON CONFLICT (node_name) DO NOTHING""",
                row)
            self._audit("exclude", node_name, None, row, actor)
        return self.get(node_name)

    def include(self, node_name: str, *, actor: str) -> bool:
        """다시 포함(제외 해제). 제외 중이 아니면 False(호출자 404). 감사 행에 해제 전 값(사유·누가·언제)을 남긴다."""
        with self._db.transaction():
            before = self.get(node_name)
            if before is None:
                return False
            n = self._db.execute_count("DELETE FROM node_exclusions WHERE node_name = :n", {"n": node_name})
            if n == 0:
                return False
            self._audit("include", node_name, before, None, actor)
        return True
