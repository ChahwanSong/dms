"""마운트 현황(노드 × 스토리지, 2026-11-23) -- 공용 코어. 순수 함수만 둔다(I/O 없음 -- 라우트 routes_mount_status 가 재료를 모아
넘긴다).

무엇을 답하나: 「이 노드에서 이 스토리지(와 DMS 공용 디렉터리)를 쓸 수 있나」를 표 한 장으로. 재료는 에이전트가 노드마다 보고한
mounts(agent.probes.probe_mounts)·artifact_base(probe_artifact_base)·k8s_node 와 스토리지 목록·배치 제외 행이다.

**dms-ssc 와 같은 계약**: 에이전트 보고 모양과 reconciler 가 두 라인에서 같으므로 칸 판정도 같아야 한다 -- 이 파일에 k8s·PV·LDAP 를
넣지 마라. dms-k8s 의 덧붙임(스토리지 종류·PV 수·k8s 호출 노드)은 mount_status_k8s.decorate 가 이 결과 위에 얹는다(이식 경계 그물
tests/test_mount_status.py). PV 를 담는 스토리지도 같은 칸 하나다: DMS 작업 파드는 PVC 를 CSI 로 붙이지 않고 스토리지 mount_path 를
hostPath 로 붙여 PV 디렉터리에 접근한다(execution_volcano) -- 칸 하나가 그 노드에서 이 스토리지의 PV 작업이 가능한지를 말한다.

칸 판정(classify_cell -- 신선도 무시, 위에서부터):
  보고에 이 스토리지 항목이 없다                      → pending(not_reported)  에이전트는 **직전 응답**의 스토리지 목록으로 점검한다 --
                                                                              추가·활성화 직후 한 주기는 항목이 없다(실패가 아니다)
  항목의 mount_path 가 지금 스토리지 경로와 다르다    → pending(path_changed)  옛 경로로 잰 결과(같은 이유)
  status Ready, writable False                      → read_only              sync 목적지·rm 배치에서 빠진다(placement require_writable)
  status Ready                                      → ready                  writable 키 부재(옛 에이전트)는 placement 처럼 통과 = ready
  status Missing + missing_mount_path                → absent                 경로 자체가 없다 -- 의도적으로 안 붙인 노드일 수 있어
                                                                              문제로 세지 않는다(nsync 소스·목적지 노드 분리 구성)
  그 밖(not_a_mountpoint·propagation_stale·not_readable·host_root_missing·미지 사유·비정상 status) → broken
  신선하지 않은 노드(fresh False)는 칸 전부 stale(last = 위 판정) -- reconciler·placement 처럼 신선한 보고만 집계한다.

공용 디렉터리 칸(classify_shared): artifact_base 보고가 지금 유효한 base 경로를 잰 것일 때만 판정한다(아니면 pending --
routes_artifact_base._node_checks 와 같은 규칙). 없음(missing)은 문제다 -- 스토리지의 absent 와 달리 공용 디렉터리는 모든 노드의 같은
경로에 있어야 한다(작업·dms-api 파드가 hostPath type: Directory 로 붙인다). 덮는 마운트가 '/' 면 local(공유 스토리지가 빠지고 로컬
디렉터리만 남은 노드 -- 같은 날 사고), 덮는 마운트를 보고하지 않는 옛 에이전트는 ok_unverified(있음, 공유 여부 모름). 신선한 ok 행들의
(mount_point, fstype) 에 엄격한 과반이 있으면 그와 다른 ok 행은 differs 다(fstype 이름으로 공유 여부를 판정하지 않는다 -- 사이트마다
다르다; 다른 노드 대다수와 같은지만 본다).

「문제 있는 노드」(행 problem·problem_count): 신선하지 않은 행은 늘 문제(problem_count null -- 지금 상태를 모른다). 신선한 행은
broken 칸 + **바깥값 read_only** 칸 + 공용 디렉터리 문제(local·differs·missing·read_only) 수다. read_only 는 같은 스토리지를 다른 신선한
노드가 쓰기 가능(ready)으로 붙였을 때만 센다 -- 이관 소스처럼 모든 노드에 ro 로 붙인 스토리지는 의도된 구성이라(absent 를 세지 않는
것과 같은 이유 -- 경보 피로) 「문제 있는 노드」 필터가 전체와 같아지지 않게 한다. 칸 상태·totals 의 read_only 수는 그대로다.

열 요약은 reconciler.summarize_mount_statuses 를 그대로 쓴다(스토리지 목록의 상태 배지와 같은 숫자 -- 교차 검사 테스트). 리포트는
스키마 검증 없이 저장되므로(DB 가 신뢰 경계) 모든 필드를 _bool·_str·_int 로 읽고 절대 예외를 내지 않는다.
"""
from collections import Counter

from .reconciler import summarize_mount_statuses

CELL_STATES = ("ready", "read_only", "absent", "broken", "pending", "stale")
SHARED_STATES = ("ok", "ok_unverified", "local", "differs", "missing", "read_only", "pending", "stale")
# 행을 「문제 있는 노드」로 세는 공용 디렉터리 상태(absent·pending·ok_unverified 는 세지 않는다 -- 경보 피로)
SHARED_PROBLEM_STATES = ("local", "differs", "missing", "read_only")
# 늘 문제로 세는 칸 상태. read_only 는 열에 따라(_ro_outlier_columns -- 모듈 docstring 「문제 있는 노드」)
CELL_PROBLEM_STATES = ("broken",)


def _bool(v):
    return v if isinstance(v, bool) else None


def _str(v):
    return v if isinstance(v, str) and v != "" else None


def _int(v):
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


def _report(node) -> "dict | None":
    report = (node or {}).get("report") if isinstance(node, dict) else None
    return report if isinstance(report, dict) else None


def _mounts(report) -> list:
    mounts = report.get("mounts") if isinstance(report, dict) else None
    return [m for m in mounts if isinstance(m, dict)] if isinstance(mounts, list) else []


def _disk(report, storage_name) -> "dict | None":
    os_part = report.get("os") if isinstance(report, dict) else None
    disks = os_part.get("disks") if isinstance(os_part, dict) else None
    if not isinstance(disks, list):
        return None
    for d in disks:
        if isinstance(d, dict) and d.get("storage_name") == storage_name:
            total, used = _int(d.get("total_bytes")), _int(d.get("used_bytes"))
            return None if total is None or used is None else {"total_bytes": total, "used_bytes": used}
    return None


def _empty_cell(state, *, pending=None) -> dict:
    return {"state": state, "last": None, "pending": pending, "status": None, "reason": None,
            "reported_path": None, "exists": None, "is_mountpoint": None, "readable": None, "writable": None,
            "disk": None}


def classify_cell(report, storage: dict) -> dict:
    """노드 보고 한 건 × 스토리지 한 행 → 칸(신선도 무시 -- build 가 stale 로 덮는다). 판정 규칙은 모듈 docstring."""
    name, path = storage.get("storage_name"), storage.get("mount_path")
    cands = [m for m in _mounts(report) if m.get("storage_name") == name]
    if not cands:
        return _empty_cell("pending", pending="not_reported")
    entry = next((m for m in cands if m.get("mount_path") == path), cands[0])
    raw_status = entry.get("status")
    cell = {
        "state": "broken", "last": None, "pending": None,
        "status": raw_status if isinstance(raw_status, str) else None,
        "reason": _str(entry.get("reason")),
        "reported_path": _str(entry.get("mount_path")),
        "exists": _bool(entry.get("exists")), "is_mountpoint": _bool(entry.get("is_mountpoint")),
        "readable": _bool(entry.get("readable")), "writable": _bool(entry.get("writable")),
        "disk": None,
    }
    if entry.get("mount_path") != path:
        cell.update(state="pending", pending="path_changed")
    elif cell["status"] == "Ready":
        cell["state"] = "read_only" if cell["writable"] is False else "ready"
        # 용량은 마운트된 칸에서만(statvfs 는 not_a_mountpoint 노드에선 루트 fs 수치다)
        cell["disk"] = _disk(report, name)
    elif cell["status"] == "Missing" and cell["reason"] == "missing_mount_path":
        cell["state"] = "absent"
    return cell


def classify_shared(report, effective_path) -> dict:
    """노드 보고 한 건 → DMS 공용 디렉터리 칸(신선도 무시). 규칙은 모듈 docstring."""
    ab = report.get("artifact_base") if isinstance(report, dict) else None
    out = {"state": "pending", "last": None, "reported_path": None, "exists": None, "writable": None,
           "mount_point": None, "fstype": None}
    if not isinstance(ab, dict):
        return out
    out["reported_path"] = _str(ab.get("path"))
    if ab.get("path") != effective_path:
        return out
    out.update(exists=_bool(ab.get("exists")), writable=_bool(ab.get("writable")),
               mount_point=_str(ab.get("mount_point")), fstype=_str(ab.get("fstype")))
    if out["exists"] is False:
        out["state"] = "missing"
    elif out["exists"] is not True:
        out["state"] = "pending"
    elif out["mount_point"] == "/":
        out["state"] = "local"
    elif out["writable"] is False:
        out["state"] = "read_only"
    elif out["mount_point"] is None:
        out["state"] = "ok_unverified"
    else:
        out["state"] = "ok"
    return out


def _stale(cell: dict, **extra) -> dict:
    return {**cell, "state": "stale", "last": cell["state"], **extra}


def _majority(rows) -> "dict | None":
    """신선한 ok 행들의 (mount_point, fstype) 엄격한 과반(대상 2개 이상) -- 없으면 None."""
    pairs = [(r["shared_dir"]["mount_point"], r["shared_dir"]["fstype"]) for r in rows
             if r["fresh"] and r["shared_dir"] is not None and r["shared_dir"]["state"] == "ok"]
    if len(pairs) < 2:
        return None
    (pair, count), = Counter(pairs).most_common(1)
    if count * 2 <= len(pairs):
        return None
    return {"mount_point": pair[0], "fstype": pair[1], "nodes": count}


def _ro_outlier_columns(rows) -> set:
    """read_only 칸을 문제로 세는 열: 신선한 ready 칸이 하나라도 있는 열(쓰기 가능으로 붙인 노드가 있는데 이 노드만 ro)."""
    return {name for r in rows if r["fresh"] for name, c in r["cells"].items() if c["state"] == "ready"}


def _problem_count(row, ro_outliers) -> "int | None":
    """행의 문제 수(모듈 docstring 「문제 있는 노드」) -- 신선하지 않은 행은 None(모름)."""
    if not row["fresh"]:
        return None
    n = sum(1 for name, c in row["cells"].items()
            if c["state"] in CELL_PROBLEM_STATES or (c["state"] == "read_only" and name in ro_outliers))
    sh = row["shared_dir"]
    return n + (1 if sh is not None and sh["state"] in SHARED_PROBLEM_STATES else 0)


def build(nodes, storages, *, exclusions: dict, artifact_base_path, state_root, build_node,
          node_list) -> dict:
    """마운트 현황 응답의 공용 몸통. nodes = agents.list_nodes(이름순 -- 행 순서), storages = storages.list()(열 순서),
    exclusions = {node_name: node_exclusions 행}, artifact_base_path = 지금 유효한 base 경로(없으면 공용 디렉터리 열 없음),
    node_list = k8s 노드 목록 [{name, control_plane, …}] 또는 None(모름). 응답 모양은 dms-ssc 와 같다 -- 덧붙이는 키는
    mount_status_k8s 가 단다."""
    columns = [s for s in storages if s.get("enabled")]
    disabled = [s.get("storage_name") for s in storages if not s.get("enabled")]
    cluster_nodes = None
    if node_list is not None:
        cluster_nodes = {n.get("name"): n for n in node_list if isinstance(n, dict) and _str(n.get("name"))}
    agent_names = set()
    rows = []
    for node in nodes:
        name = node.get("node_name")
        agent_names.add(name)
        report = _report(node)
        fresh = node.get("fresh") is True
        cells = {}
        for s in columns:
            cell = classify_cell(report, s)
            cells[s.get("storage_name")] = cell if fresh else _stale(cell, disk=None)
        shared = None
        if artifact_base_path:
            shared = classify_shared(report, artifact_base_path)
            if not fresh:
                shared = _stale(shared)
        k8s_node = report.get("k8s_node") if report is not None else None
        in_cluster = control_plane = None
        if cluster_nodes is not None:
            in_cluster = name in cluster_nodes
            if in_cluster:
                control_plane = cluster_nodes[name].get("control_plane") is True
        rows.append({
            "node_name": name, "reported_at": node.get("reported_at"), "fresh": fresh,
            "probe_mode": _str(report.get("probe_mode")) if report is not None else None,
            "exclusion": exclusions.get(name),
            "k8s_node": k8s_node if isinstance(k8s_node, dict) else None,
            "roles": ["build"] if build_node and name == build_node else [],
            "control_plane": control_plane, "in_cluster": in_cluster,
            "problem": False, "problem_count": None, "cells": cells, "shared_dir": shared,
        })

    majority = _majority(rows) if artifact_base_path else None
    if majority is not None:
        pair = (majority["mount_point"], majority["fstype"])
        for r in rows:
            sh = r["shared_dir"]
            if r["fresh"] and sh is not None and sh["state"] == "ok" and (sh["mount_point"], sh["fstype"]) != pair:
                sh["state"] = "differs"

    ro_outliers = _ro_outlier_columns(rows)
    for r in rows:
        r["problem_count"] = _problem_count(r, ro_outliers)
        r["problem"] = r["problem_count"] is None or r["problem_count"] > 0

    fresh_rows = [(r, _report(n)) for r, n in zip(rows, nodes) if r["fresh"]]
    out_columns = []
    for s in columns:
        name = s.get("storage_name")
        statuses = [m.get("status") for _r, report in fresh_rows for m in _mounts(report)
                    if m.get("storage_name") == name]
        status, detail = summarize_mount_statuses(statuses)
        states = Counter(r["cells"][name]["state"] for r, _ in fresh_rows)
        out_columns.append({
            "storage_name": name, "mount_path": s.get("mount_path"), "backend_type": s.get("backend_type"),
            "user_enabled": bool(s.get("user_enabled")),
            "summary": {"ready": sum(1 for x in statuses if x == "Ready"), "total": len(statuses),
                        "status": status, "detail": detail,
                        "broken": states["broken"], "read_only": states["read_only"],
                        "absent": states["absent"], "pending": states["pending"]},
        })

    cell_states = Counter(c["state"] for r, _ in fresh_rows for c in r["cells"].values())
    shared_states = Counter(r["shared_dir"]["state"] for r, _ in fresh_rows if r["shared_dir"] is not None)
    totals = {
        "nodes": len(rows), "fresh_nodes": len(fresh_rows), "stale_nodes": len(rows) - len(fresh_rows),
        "ready": cell_states["ready"], "read_only": cell_states["read_only"], "absent": cell_states["absent"],
        "broken": cell_states["broken"], "pending": cell_states["pending"],
        "shared_ok": shared_states["ok"] + shared_states["ok_unverified"],
        "shared_unverified": shared_states["ok_unverified"],
        "shared_problem": sum(shared_states[s] for s in SHARED_PROBLEM_STATES),
        "shared_pending": shared_states["pending"],
        "problem_nodes": sum(1 for r in rows if r["problem"]),
    }
    if cluster_nodes is None:
        cluster = {"known": False, "agentless_nodes": []}
    else:
        cluster = {"known": True, "agentless_nodes": [
            {"node_name": n, "control_plane": cluster_nodes[n].get("control_plane") is True}
            for n in sorted(cluster_nodes) if n not in agent_names]}
    return {
        "shared_dir": None if not artifact_base_path else {
            "path": artifact_base_path, "state_root": state_root, "majority": majority},
        "storages": out_columns,
        "disabled_storages": disabled,
        "nodes": rows,
        "totals": totals,
        "cluster": cluster,
    }
