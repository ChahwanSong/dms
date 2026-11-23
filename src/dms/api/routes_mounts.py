"""마운트 현황(2026-11-23 사용자 요청: 스토리지 하위 「마운트 현황」 -- 노드 × 스토리지 마운트 표).

읽기 전용 · **DB 만** 읽는다(파일시스템을 만지지 않는다): 에이전트 보고(agent_nodes.report 의 mounts[]·artifact_base·
k8s_node), 노드 배치 제외(node_exclusions), API 파드 점검(api_base_checks), 컨트롤러 점검(control_state). 관리 화면이 10초로
폴링하므로 요청마다 FS·k8s 를 건드리지 않는 것이 계약이다.

셀 판정(스토리지 × 노드):
- 비활성 스토리지 = disabled(문제 아님).
- 보고(mounts[])에 그 스토리지가 없거나 보고의 mount_path 가 지금 등록값과 다르면 unreported(옛 프로브 -- 지금 경로를 본 적이
  없다, 모름 ≠ 실패).
- Ready ∧ writable = ready, Ready ∧ ¬writable = readonly, Missing = missing(사유는 에이전트 사유 그대로: missing_mount_path·
  not_a_mountpoint·propagation_stale·not_readable·host_root_missing).
아티팩트 경로 칸: 에이전트의 artifact_base 프로브(exists/writable -- 마운트 여부는 모른다)에, base 를 덮는 등록 스토리지
(가장 긴 mount_path 접두)의 셀이 missing 이면 missing(사유 covering_storage_missing:<이름>)을 겹친다 -- 마운트가 빠진 노드의
빈 로컬 디렉터리는 exists/writable 만으로는 정상으로 보이기 때문이다(2026-11-23 사고).
"""
from fastapi import APIRouter, Depends, Request

from ..artifact_base import resolve_artifact_base, strip_scheme
from ..db import iso_plus, utc_now_iso
from .auth import require_admin
from .base_monitor import fresh_window_seconds

router = APIRouter(dependencies=[Depends(require_admin)])

_PROBLEM_CELL_STATES = ("missing", "readonly")


def _covers(mount_path: str, path: str) -> bool:
    mp = mount_path.rstrip("/") or "/"
    return path == mp or path.startswith(mp.rstrip("/") + "/")


def covering_storage(path: str, storages: list) -> "str | None":
    """base 경로를 덮는 **활성** 등록 스토리지(가장 긴 mount_path 접두). 비활성은 에이전트에 내려가지 않아(routes_agent)
    셀이 늘 disabled 라, 그걸 고르면 덮는 마운트가 빠진 노드(2026-11-23 사고 서명)의 base 칸이 조용히 「정상」이 된다
    (2026-11-23 리뷰) -- 더 긴 비활성 스토리지가 있어도 활성 중 가장 긴 것을 고른다."""
    best = None
    for s in storages:
        mp = s["mount_path"]
        if s["enabled"] and isinstance(mp, str) and mp and _covers(mp, path):
            if best is None or len(mp) > len(best["mount_path"]):
                best = s
    return best["storage_name"] if best is not None else None


def _bool_or_none(value):
    return value if isinstance(value, bool) else None


def _unreported():
    return {"status": "unreported", "reason": None, "exists": None, "is_mountpoint": None,
            "readable": None, "writable": None}


def storage_cell(storage: dict, mounts_by_name: dict) -> dict:
    if not storage["enabled"]:
        return {**_unreported(), "status": "disabled"}
    m = mounts_by_name.get(storage["storage_name"])
    if not isinstance(m, dict) or m.get("mount_path") != storage["mount_path"]:
        return _unreported()
    flags = {k: _bool_or_none(m.get(k)) for k in ("exists", "is_mountpoint", "readable", "writable")}
    status = m.get("status")
    if status == "Ready":
        return {"status": "ready" if flags["writable"] is True else "readonly", "reason": None, **flags}
    if status == "Missing":
        reason = m.get("reason")
        return {"status": "missing", "reason": reason if isinstance(reason, str) else None, **flags}
    return {**_unreported(), **flags}


def _base_cell(report: "dict | None", path: str, covering: "str | None", cells: dict,
               api_rows: list, controller: "dict | None") -> dict:
    ab = (report or {}).get("artifact_base") if isinstance(report, dict) else None
    agent = None
    status, reason = "unreported", None
    if isinstance(ab, dict):
        agent = {"exists": _bool_or_none(ab.get("exists")), "writable": _bool_or_none(ab.get("writable")),
                 "path_matches": ab.get("path") == path}
        if agent["path_matches"]:
            if agent["exists"] is False:
                status, reason = "missing", "artifact_base_missing"
            elif agent["exists"] is True:
                status = "ready" if agent["writable"] is True else "readonly"
    cover_cell = cells.get(covering) if covering is not None else None
    if cover_cell is not None and cover_cell["status"] == "missing":
        status, reason = "missing", f"covering_storage_missing:{covering}"
    return {"status": status, "reason": reason, "agent": agent,
            "api": [{"instance": r["instance"], "ok": r["ok"], "reason": r["reason"],
                     "checked_at": r["checked_at"], "gate_state": r["gate_state"]} for r in api_rows],
            "controller": controller}


@router.get("/api/admin/mounts")
def mounts(request: Request):
    repos, settings = request.app.state.repos, request.app.state.settings
    now = utc_now_iso()
    uri = resolve_artifact_base(repos.control, settings)
    path = strip_scheme(uri)
    storages = sorted(repos.storages.list(), key=lambda s: s["storage_name"])
    covering = covering_storage(path, storages)
    agent_nodes = {n["node_name"]: n for n in
                   repos.agents.list_nodes(stale_seconds=settings.agent_report_stale_seconds)}
    excluded = {r["node_name"] for r in repos.node_exclusions.list()}

    # API 파드 점검(신선 행만 -- 죽은 파드의 행은 창이 지나면 빠진다). node_name 이 없는 행은 노드에 놓을 수 없다(관리
    # 화면 /admin/artifact-base 의 파드 목록이 보인다).
    interval = int(getattr(settings, "api_artifact_base_check_seconds", 60))
    api_cutoff = iso_plus(now, -fresh_window_seconds(interval))
    api_by_node: dict = {}
    for r in repos.api_base_checks.list_recent(now_iso=now):
        if r["node_name"] and (r["checked_at"] or r["started_at"]) >= api_cutoff:
            api_by_node.setdefault(r["node_name"], []).append(r)

    # 컨트롤러 점검(현재 base 를 본 신선한 결과만 -- 옛 base 결과·구버전 컨트롤러는 모름).
    row = repos.control.control_state() or {}
    controller_node, controller_check = row.get("artifact_base_check_node"), None
    ctl_cutoff = iso_plus(now, -max(3 * int(getattr(settings, "reconcile_interval_seconds", 30)), 180))
    if (controller_node and row.get("artifact_base_check_uri") == uri
            and (row.get("artifact_base_check_at") or "") >= ctl_cutoff):
        controller_check = {"ok": None if row.get("artifact_base_check_ok") is None
                            else bool(row["artifact_base_check_ok"]),
                            "reason": row.get("artifact_base_check_reason"),
                            "checked_at": row.get("artifact_base_check_at")}
    else:
        controller_node = None

    names = set(agent_nodes) | set(api_by_node) | ({controller_node} if controller_node else set())
    nodes, problem_cells, problem_nodes = [], 0, 0
    for name in sorted(names):
        agent = agent_nodes.get(name)
        report = (agent or {}).get("report") if agent is not None else None
        report = report if isinstance(report, dict) else None
        mounts_by_name = {}
        for m in (report or {}).get("mounts") or []:
            if isinstance(m, dict) and isinstance(m.get("storage_name"), str):
                mounts_by_name[m["storage_name"]] = m
        cells = {s["storage_name"]: (storage_cell(s, mounts_by_name) if agent is not None else
                                     ({**_unreported(), "status": "disabled"} if not s["enabled"] else _unreported()))
                 for s in storages}
        api_rows = api_by_node.get(name, [])
        ctl = controller_check if name == controller_node else None
        base = _base_cell(report, path, covering, cells, api_rows, ctl)
        k8s = (report or {}).get("k8s_node")
        k8s = k8s if isinstance(k8s, dict) else {}
        roles = (["api"] if api_rows else []) + (["controller"] if ctl is not None else [])
        fresh = bool(agent["fresh"]) if agent is not None else False
        problems = 0
        if fresh:
            problems += sum(1 for s in storages
                            if s["enabled"] and cells[s["storage_name"]]["status"] in _PROBLEM_CELL_STATES)
        # 「아티팩트 경로」 열은 **한 칸**이다 -- 에이전트 프로브(missing·readonly)·이 노드 API 파드 점검·컨트롤러 점검 중
        # 하나라도 문제면 1(포탈 mountView.baseProblem 과 같은 규칙; 예전엔 최대 3번 세어 「문제 3칸」 위에 빨간 칸이 2개였다).
        base_problem = ((fresh and base["status"] in _PROBLEM_CELL_STATES)
                        or any(r["ok"] is False for r in api_rows)
                        or (ctl is not None and ctl["ok"] is False))
        problems += 1 if base_problem else 0
        problem_cells += problems
        problem_nodes += 1 if problems else 0
        schedulable = k8s.get("schedulable")
        nodes.append({
            "node_name": name, "has_agent": agent is not None, "fresh": fresh,
            "reported_at": agent["reported_at"] if agent is not None else None,
            "probe_mode": (report or {}).get("probe_mode"),
            "schedulable": schedulable if isinstance(schedulable, bool) else None,
            "schedulable_reason": k8s.get("reason") if isinstance(k8s.get("reason"), str) else None,
            "excluded": name in excluded, "roles": roles, "cells": cells, "artifact_base": base,
        })
    return {
        "generated_at": now,
        "artifact_base": {"uri": uri, "path": path, "covering_storage": covering},
        "storages": [{"storage_name": s["storage_name"], "mount_path": s["mount_path"],
                      "enabled": bool(s["enabled"])} for s in storages],
        "nodes": nodes,
        "summary": {"node_count": len(nodes),
                    "fresh_node_count": sum(1 for n in nodes if n["fresh"]),
                    "stale_node_count": sum(1 for n in nodes if n["has_agent"] and not n["fresh"]),
                    "storage_count": len(storages), "problem_cells": problem_cells,
                    "problem_nodes": problem_nodes},
    }
