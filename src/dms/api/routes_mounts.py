"""마운트 현황(2026-11-23 사용자 요청: 스토리지 하위 「마운트 현황」 -- 노드 × 스토리지 마운트 표).

읽기 전용 · **DB 만** 읽는다(파일시스템을 만지지 않는다): 에이전트 보고(agent_nodes.report 의 mounts[]·artifact_base·
k8s_node), 노드 배치 제외(node_exclusions), API 파드 점검(api_base_checks), 컨트롤러 점검(control_state). 관리 화면이 10초로
폴링하므로 요청마다 FS·k8s 를 건드리지 않는 것이 계약이다.

셀 판정(스토리지 × 노드):
- 비활성 스토리지 = disabled(문제 아님).
- 보고(mounts[])에 그 스토리지가 없거나 보고의 mount_path 가 지금 등록값과 다르면 unreported(옛 프로브 -- 지금 경로를 본 적이
  없다, 모름 ≠ 실패).
- Ready ∧ writable 거짓 = readonly, 그 밖의 Ready(writable 참 또는 모름 -- 키 없는 옛 에이전트) = ready(placement 처럼 writable
  is False 만 뺀다 -- null(모름) ≠ 실패, 2026-11-27 dms-k8s 210cd166 classify_cell 과 맞춤; 칸의 writable 플래그는 원값 그대로).
  Missing ∧ 사유 missing_mount_path ∧ 호스트 mountinfo 에 그 지점의 마운트가 없음(is_mountpoint 가 참이 아님) = absent(그
  노드엔 그 스토리지가 없다 -- 의도적으로 안 붙인 노드일 수 있다, 2026-11-27 dms-k8s 판정 이식), 그 밖의 Missing = missing(사유는 에이전트 사유 그대로: not_a_mountpoint·propagation_stale·not_readable·
  host_root_missing). absent 도 사유를 그대로 싣는다. **is_mountpoint 가 참인 missing_mount_path 는 missing 이다**(2026-11-27
  리뷰): 호스트엔 마운트가 있는데 경로가 안 보이는 것 -- 죽은 ceph-fuse(ENOTCONN)·낡은 NFS(ESTALE)는 isdir 가 오류를 삼켜
  False 가 되고, 부모 마운트의 전파가 끊기면 자식 마운트 지점이 컨테이너에서 사라진다. 이걸 「제공 안 함」으로 읽으면 이 화면이
  잡으려는 끊긴 마운트를 숨긴다.
아티팩트 경로 칸: 에이전트의 artifact_base 프로브(exists/writable + 덮는 마운트 mount_point·fstype -- 2026-11-27, 호스트 루트
모드 에이전트만)로 판정한다. exists 이고 덮는 마운트가 '/' 면 missing(사유 artifact_base_local_dir -- 공유 스토리지가 빠지고
노드 루트 fs 의 로컬 디렉터리만 남았다). 그 밖의 exists 는 writable 거짓이면 readonly, 참·모름이면 ready(스토리지 칸과 같은
규칙). 그 위에 base 를 덮는 등록 스토리지(가장 긴 mount_path 접두)의 셀이 missing·absent 면
missing(사유 covering_storage_missing:<이름>)을 겹친다 -- 마운트가 빠진 노드의 빈 로컬 디렉터리는 exists/writable 만으로는
정상으로 보이기 때문이다(2026-11-23 사고; mount_point 를 싣지 않는 옛 에이전트도 이 겹침으로 잡힌다). base 는 모든 노드에 있어야
하므로 덮는 스토리지의 absent 는 base 칸에선 문제다.

문제(problem) 판정 -- 서버가 진실이고 summary.problem_cells·problem_nodes 는 이 플래그만 센다(포탈은 플래그가 있으면 그대로 쓰고,
없는 옛 서버에서만 자기 규칙으로 센다):
- 셀: 신선한 노드의 missing 은 문제. absent 는 **같은 열에 그 스토리지를 붙인 흔적(attached)이 하나라도 있으면** 문제 아님(그
  스토리지를 붙이지 않은 노드) -- 흔적이 하나도 없으면 그 스토리지는 어디에도 마운트돼 있지 않은 것이라(mount_path 오타·마운트
  배포 전) 문제다(2026-11-27 리뷰 -- 열 전체가 회색 「경로 없음」인데 「문제 0」이던 구멍).
  흔적 = ready·readonly 칸과 사유가 host_root_missing 이 아닌 missing 칸(호스트에 마운트나 디렉터리가 있다 -- 끊긴 마운트·마운트
  빠짐·전파 안 됨·읽기 불가, 사유 없음·미지 사유도; 그 칸 자체가 이미 문제라 열은 문제 열로 남는다)이고, 에이전트 보고가 있는
  노드면 리포트 지연 노드의 칸도 센다(그 칸은 이미 마지막 보고의 판정이다). 흔적이 아닌 것: absent·unreported·disabled·
  host_root_missing(그 노드의 마운트를 점검하지 못했다 -- 아무것도 모른다). 흔적을 신선한 ready·readonly 로만 세던 첫 판은 그
  스토리지를 붙인 노드가 하나뿐일 때(nsync 소스·목적지 노드 분리) 그 노드 하나의 리포트 지연·끊긴 마운트가 다른 모든 노드의
  absent 를 문제로 퍼뜨렸다(dms-k8s 리뷰 실측 -- 노드 6대에서 문제 노드 1 → 6). 2026-11-27 dms-k8s 210cd166(mount_status._attached)
  과 같은 규칙으로 맞췄다. mount_path 오타(모든 노드가 is_mountpoint 거짓인 missing_mount_path = absent)는 그대로 잡히고, 고장 난
  노드만 문제로 남는다. 알려진 한계: 새로 등록한 스토리지를 붙인 노드가 아직 그 경로를 보고하기 전(unreported)이면 다른 노드의
  absent 가 한 보고 주기 동안 문제로 보인다(docs/BACKLOG.md).
  readonly 는 **같은 열에 신선한 ready 노드가 하나라도 있을 때만** 문제(이관 소스처럼 모든 노드에 ro 로 붙인 스토리지는 의도된
  구성 -- 경보 피로; 리포트 지연 노드의 ready 는 세지 않는다). ready·unreported·disabled 와 신선하지 않은 노드의 칸은 문제 아님.
- 아티팩트 경로 칸: (신선 ∧ (missing ∨ readonly)) ∨ 이 노드 API 파드 점검 실패 ∨ 컨트롤러 점검 실패 -- 한 칸이라 최대 1.
  readonly 는 늘 문제다(2026-11-27 dms-k8s 210cd166 과 맞춤 -- 잡이 어느 노드에서든 이 경로에 아티팩트를 써야 한다. 「ready 인
  동료 base 가 있을 때만」 세던 첫 판은 모든 노드의 base 가 읽기 전용인 장애를 「문제 0」으로 숨겼다).
dms-k8s 의 「다른 노드와 다름(differs -- 신선한 정상 행들의 (mount_point, fstype) 엄격 과반과 다른 행)」 판정은 옮기지 않았다
(2026-11-27 -- 화면은 덮는 마운트를 정보로만 보인다, BACKLOG).
"""
from fastapi import APIRouter, Depends, Request

from ..artifact_base import resolve_artifact_base, strip_scheme
from ..db import iso_plus, utc_now_iso
from .auth import require_admin
from .base_monitor import fresh_window_seconds

router = APIRouter(dependencies=[Depends(require_admin)])

# 에이전트 Missing 사유 중 「그 노드엔 그 스토리지가 없다」(경로 자체가 없음) -- 호스트에 그 지점의 마운트도 없을 때만 absent 로
# 가른다(마운트가 있는데 경로가 안 보이면 끊긴 마운트 -- missing, 모듈 docstring).
ABSENT_REASON = "missing_mount_path"
# 그 노드의 마운트를 점검하지 못했다는 Missing 사유(호스트 루트가 없다) -- missing(문제)이지만 붙인 흔적이 아니다(attached).
HOST_ROOT_MISSING = "host_root_missing"
# 덮는 마운트가 이것이면 base 는 공유 스토리지가 아니라 노드 루트 fs 의 로컬 디렉터리다.
_ROOT_MOUNT = "/"


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


def _str_or_none(value):
    return value if isinstance(value, str) and value != "" else None


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
        return {"status": "readonly" if flags["writable"] is False else "ready", "reason": None, **flags}
    if status == "Missing":
        reason = m.get("reason")
        reason = reason if isinstance(reason, str) else None
        absent = reason == ABSENT_REASON and flags["is_mountpoint"] is not True
        return {"status": "absent" if absent else "missing", "reason": reason, **flags}
    return {**_unreported(), **flags}


def attached(cell: dict) -> bool:
    """칸이 그 스토리지를 이 노드에 붙인(붙이려 한) 흔적인가(모듈 docstring 「문제 판정」 -- dms-k8s mount_status._attached 와
    같은 규칙): ready·readonly, 또는 사유가 host_root_missing 이 아닌 missing(호스트에 마운트나 디렉터리가 있다). absent·
    unreported·disabled 는 흔적이 아니다. 신선도는 보지 않는다 -- 리포트 지연 노드의 칸은 이미 마지막 보고의 판정이다."""
    status = cell["status"]
    return status in ("ready", "readonly") or (status == "missing" and cell["reason"] != HOST_ROOT_MISSING)


def cell_problem(cell: dict, *, fresh: bool, column_has_ready: bool, column_mounted: bool) -> bool:
    """셀의 문제 여부(모듈 docstring 「문제 판정」). column_has_ready = 같은 열에 **신선한** ready 노드가 있다(readonly 바깥값),
    column_mounted = 같은 열에 붙인 흔적(attached) 칸이 있다 -- 에이전트 보고가 있는 노드면 리포트 지연 노드의 칸(마지막 판정)도
    든다(그 스토리지가 어딘가엔 붙어 있다 -- 그 열의 absent 는 문제 아님)."""
    if not fresh:
        return False
    if cell["status"] == "missing":
        return True
    if cell["status"] == "absent":
        return not column_mounted
    return cell["status"] == "readonly" and column_has_ready


def _base_cell(report: "dict | None", path: str, covering: "str | None", cells: dict,
               api_rows: list, controller: "dict | None") -> dict:
    ab = (report or {}).get("artifact_base") if isinstance(report, dict) else None
    agent = None
    status, reason = "unreported", None
    if isinstance(ab, dict):
        # mount_point·fstype: 키 없는 옛 에이전트·direct 모드·덮는 마운트를 못 찾음은 모두 None(모름 -- 판정하지 않는다).
        agent = {"exists": _bool_or_none(ab.get("exists")), "writable": _bool_or_none(ab.get("writable")),
                 "path_matches": ab.get("path") == path,
                 "mount_point": _str_or_none(ab.get("mount_point")), "fstype": _str_or_none(ab.get("fstype"))}
        if agent["path_matches"]:
            if agent["exists"] is False:
                status, reason = "missing", "artifact_base_missing"
            elif agent["exists"] is True and agent["mount_point"] == _ROOT_MOUNT:
                status, reason = "missing", "artifact_base_local_dir"
            elif agent["exists"] is True:
                status = "readonly" if agent["writable"] is False else "ready"
    cover_cell = cells.get(covering) if covering is not None else None
    if cover_cell is not None and cover_cell["status"] in ("missing", "absent"):
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
    nodes, checks = [], {}
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
        checks[name] = (any(r["ok"] is False for r in api_rows) or (ctl is not None and ctl["ok"] is False))
        schedulable = k8s.get("schedulable")
        nodes.append({
            "node_name": name, "has_agent": agent is not None, "fresh": fresh,
            "reported_at": agent["reported_at"] if agent is not None else None,
            "probe_mode": (report or {}).get("probe_mode"),
            "schedulable": schedulable if isinstance(schedulable, bool) else None,
            "schedulable_reason": k8s.get("reason") if isinstance(k8s.get("reason"), str) else None,
            "excluded": name in excluded, "roles": roles, "cells": cells, "artifact_base": base,
        })

    # 문제 플래그(모듈 docstring 「문제 판정」) -- 열 단위 판정(readonly·absent 의 바깥값)이 있어 모든 행을 모은 뒤에 단다.
    # readonly 바깥값의 동료는 신선한 ready 만, absent 를 덮는 흔적은 리포트 지연 노드의 칸도(마지막 판정 -- 2026-11-27 퍼짐 수정).
    ready_columns = {sn for n in nodes if n["fresh"] for sn, c in n["cells"].items() if c["status"] == "ready"}
    mounted_columns = {sn for n in nodes if n["has_agent"] for sn, c in n["cells"].items() if attached(c)}
    problem_cells = problem_nodes = 0
    for n in nodes:
        for sn, c in n["cells"].items():
            c["problem"] = cell_problem(c, fresh=n["fresh"], column_has_ready=sn in ready_columns,
                                        column_mounted=sn in mounted_columns)
        base = n["artifact_base"]
        # 「아티팩트 경로」 열은 **한 칸**이다 -- 에이전트 프로브·이 노드 API 파드 점검·컨트롤러 점검 중 하나라도 문제면 1
        # (예전엔 최대 3번 세어 「문제 3칸」 위에 빨간 칸이 2개였다).
        base["problem"] = bool((n["fresh"] and base["status"] in ("missing", "readonly")) or checks[n["node_name"]])
        problems = sum(1 for c in n["cells"].values() if c["problem"]) + (1 if base["problem"] else 0)
        problem_cells += problems
        problem_nodes += 1 if problems else 0
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
