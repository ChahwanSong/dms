"""마운트 현황 API(2026-11-23) -- 노드 × 스토리지 마운트 매트릭스의 읽기 전용 투영.

관리자 전용(require_admin -- 공유 토큰 GET 도 통과, /api/admin/nodes 와 같은 수준): mount_path·노드 이름은 일반 사용자에게 숨기는
계약이다(/api/user/storages). 읽기 전용이라 세션 관리자(require_session_admin)는 필요 없다.

판정·집계는 mount_status(공용 코어 -- dms-ssc 와 같은 계약)가 하고, dms-k8s 덧붙임(스토리지 종류·PV 수·k8s 호출 노드)은
mount_status_k8s.decorate 한 줄이다(dms-ssc 로 옮길 때 그 줄만 지운다). 포탈이 기존 GET 들을 엮지 않고 이 투영을 따로 두는
이유: 에이전트가 없는 k8s 노드·control-plane 여부·PV 수처럼 서버만 아는 재료가 있고, 열 머리 숫자를 스토리지 목록 상태와 같은
코드(reconciler.summarize_mount_statuses)로 세며, 노드 보고 전체를 10초마다 내려보내지 않는다.

경로는 /api/admin/storages/mounts 가 아니다 -- 스토리지 이름 규칙상 「mounts」도 유효한 이름이라 PUT·DELETE /api/admin/storages/{name}
과 모호해진다. 새 HTTPException 사유가 없다(마운트 사유·cluster.reason 은 응답 값이고 포탈 로컬 맵이 번역한다).
"""
import logging
import time

from fastapi import APIRouter, Depends, Request

from .. import mount_status, mount_status_k8s
from ..artifact_base import resolve_artifact_base, state_root_of, strip_scheme
from ..db import utc_now_iso
from .auth import require_admin

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_admin)])

# k8s 노드 목록 메모(초). 노드 목록은 요청마다 하는 동기 k8s API 호출(최대 10초)이고 화면은 10초마다 다시 묻는다 -- 노드가
# 생기고 사라지는 속도에 비해 30초면 충분하다. 실패·빈 목록도 메모한다(실패하는 k8s API 를 두드리지 않게).
NODE_LIST_TTL_SECONDS = 30


def _cluster_nodes(request: Request):
    """→ (nodes | None, reason | None, observed_at | None). 표시용이라 fail-soft: 실패는 list_failed, 빈 목록(스텁 백엔드·
    권한 밖)은 no_nodes_listed 로 「모름」이다(「노드 없음」이 아니다)."""
    memo = getattr(request.app.state, "mount_status_node_memo", None)
    now = time.monotonic()
    if memo is not None and 0 <= now - memo[0] < NODE_LIST_TTL_SECONDS:
        return memo[1]
    try:
        nodes = [n for n in (request.app.state.node_lister() or []) if isinstance(n, dict) and n.get("name")]
        result = (nodes, None, utc_now_iso()) if nodes else (None, "no_nodes_listed", None)
    except Exception as exc:  # noqa: BLE001 -- 표시용(에이전트 없는 노드·control-plane 꼬리표) -- 실패하면 모름
        logger.warning("mount-status: node listing failed: %s", exc)
        result = (None, "list_failed", None)
    request.app.state.mount_status_node_memo = (now, result)
    return result


@router.get("/api/admin/mount-status")
def get_mount_status(request: Request):
    settings = request.app.state.settings
    repos = request.app.state.repos
    nodes = repos.agents.list_nodes(stale_seconds=settings.agent_report_stale_seconds)
    storages = repos.storages.list()
    exclusions = {r["node_name"]: r for r in repos.node_exclusions.list()}
    base_uri = resolve_artifact_base(repos.control, settings)
    base_path = strip_scheme(base_uri) if base_uri else None
    build_node = (repos.control.control_state() or {}).get("build_node_name")
    node_list, reason, observed_at = _cluster_nodes(request)
    body = mount_status.build(nodes, storages, exclusions=exclusions, artifact_base_path=base_path or None,
                              state_root=state_root_of(base_uri), build_node=build_node, node_list=node_list)
    # dms-k8s 덧붙임 -- dms-ssc 는 이 한 줄이 없다.
    mount_status_k8s.decorate(body, storages=storages, k8s_settings=repos.k8s.get_settings(),
                              pv_index=repos.k8s.pv_root_index, maps_for_storage=repos.k8s.maps_for_storage,
                              unsynced=repos.k8s.unsynced_cluster_exists())
    body["cluster"].update(reason=reason, observed_at=observed_at)
    return {"generated_at": utc_now_iso(), "stale_seconds": settings.agent_report_stale_seconds,
            "report_interval_seconds": settings.agent_report_interval_seconds, **body}
