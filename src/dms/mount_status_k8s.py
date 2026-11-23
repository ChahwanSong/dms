"""마운트 현황의 dms-k8s 덧붙임(2026-11-23). 공용 코어(mount_status.build)가 만든 응답 위에 k8s 정보만 얹는다 -- dms-ssc 로
이식할 때는 이 파일과 라우트의 decorate 한 줄만 빼면 된다(코어 응답 모양은 두 라인이 같다).

얹는 것(순수 -- 재료는 라우트가 넘긴다):
  - 스토리지 열: kind(filesystem·pv·both -- repositories.k8s.storage_kind 정규화, NULL = filesystem)와 pv(PV 를 담는 종류만;
    filesystem 이면 null): count = 지금 고를 수 있는 PV(pv_root_index 의 (cluster, namespace, pvc) 중복 없는 수 -- Bound·StorageClass
    매핑·경로 해석 가능), clusters, storage_classes(이 스토리지를 가리키는 매핑). 한 번도 적용되지 않은 클러스터가 있으면
    count 는 null(모름 -- usage_attribution 과 같은 규칙, null ≠ 0).
  - 노드 행: roles 에 k8s 호출 노드 꼬리표(k8s_primary·k8s_secondary -- k8s_sync.node_role).

PV 별 행·열은 만들지 않는다: 에이전트가 PV 별 점검을 하지 않고(데이터가 없다), PV 데이터는 스토리지 마운트 경로 아래 하위
디렉터리라 스토리지 칸 하나가 그 노드에서 그 스토리지의 PV 전부에 접근할 수 있는지를 함께 말한다(mount_status 모듈 docstring).
pv_root_index 의 값은 메모를 그대로 넘기는 읽기 전용 사상이다 -- **읽기만** 한다(바꾸면 다음 소유 판정이 오염된다).
"""
from . import k8s_sync
from .repositories.k8s import PV_CAPABLE_KINDS, storage_kind


def decorate(body: dict, *, storages, k8s_settings, pv_index, maps_for_storage, unsynced: bool) -> None:
    """body(mount_status.build 의 결과 -- 이 요청의 자기 dict)를 제자리에서 고친다."""
    by_name = {s.get("storage_name"): s for s in storages}
    for col in body.get("storages", []):
        name = col.get("storage_name")
        kind = storage_kind(by_name.get(name))
        col["kind"] = kind
        if kind not in PV_CAPABLE_KINDS:
            col["pv"] = None
            continue
        ids = {(entry[0], entry[1], entry[2])
               for identities in pv_index(name).values() for entry in identities}
        col["pv"] = {
            "count": None if unsynced else len(ids),
            "clusters": sorted({c for c, _ns, _pvc in ids if isinstance(c, str)}),
            "storage_classes": sorted(m["storage_class"] for m in maps_for_storage(name)),
        }
    for row in body.get("nodes", []):
        role = k8s_sync.node_role(k8s_settings, row.get("node_name"))
        if role is not None:
            row["roles"].append(f"k8s_{role}")
