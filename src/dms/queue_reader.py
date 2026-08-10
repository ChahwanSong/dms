"""Volcano 큐 가시성 리더(슬라이스 17 설계 §2.1). 세 상태를 절대 뭉개지 않는다:
None = 알 수 없음(403/CRD 부재), [] = 정말 비었음(설계 §4). 여기서 한 번 접히면
어떤 상위 계층도 되살릴 수 없으므로 리더가 이 구분의 최초 권위다."""

# 읽는 큐 이름. 설정으로 빼지 않는다: RBAC(10-rbac.yaml)의 ClusterRole 이
# resourceNames=["dms-data"] 로 이 이름만 GET 을 허용하므로, 설정으로 다른 이름을
# 넣으면 RBAC 와 어긋나 조용히 403(=화면 "알 수 없음")이 된다. policies.queue 의
# 기본값과 같은 값이다 -- execution_manifests 가 잡을 제출하는 그 큐.
DMS_QUEUE = "dms-data"


class VolcanoQueueReader:
    """k8s 에서 Queue.state 와 살아 있는 PodGroup 을 읽는다.

    - read_queue: 이름 지정 GET 하나. resourceNames 는 list 에 적용되지 않는다
      (이 저장소가 두 번 적어 둔 함정) -- Queue 는 반드시 GET 이어야 한다.
      Queue 에서 읽는 것은 state(Open/Closed) 하나다: phase 카운터는 omitempty
      (키 부재=0)인 데다 PodGroup 을 세면 유도되므로 읽지 않는다(설계 §2.1).
    - read_podgroups: 네임스페이스 list 후 spec.queue 필터. PodGroup 이름
      규칙(<vcjob>-<uid>)은 문서화된 계약이 아니고 DMS 라벨도 없어 이름 유도/라벨
      셀렉터가 불가능하다 -- 목록+필터가 유일하게 안전한 경로다.

    404 는 k8s 클라이언트가 None 으로 접어 준다(CRD 부재=Volcano 미설치, 또는 큐
    오브젝트 부재 -- 어느 쪽도 "빈 큐"가 아니다). 403 등 그 외 예외는 그대로
    올라간다 -- 라우트가 잡아 그 축만 null 강등 + 로그를 남긴다."""

    def __init__(self, k8s, *, namespace, queue=DMS_QUEUE):
        self._k8s = k8s
        self._namespace = namespace
        self._queue = queue

    def read_queue(self):
        obj = self._k8s.get_queue(self._queue)
        if obj is None:
            return None
        return {"name": self._queue,
                "state": (obj.get("status") or {}).get("state")}

    def read_podgroups(self):
        objs = self._k8s.list_podgroups(self._namespace)
        if objs is None:
            return None
        out = []
        for item in (objs.get("items") or []):
            spec = item.get("spec") or {}
            if spec.get("queue") != self._queue:
                continue
            meta = item.get("metadata") or {}
            out.append({
                "name": meta.get("name") or "",
                "phase": (item.get("status") or {}).get("phase"),
                "min_member": spec.get("minMember"),
                "created_at": meta.get("creationTimestamp"),
            })
        return out


class StubQueueReader:
    """클러스터가 없을 때(execution_backend != "volcano") 쓰는 결정적 페어
    (StubRolloutRunner 와 같은 역할). 기본 백엔드가 stub 이라 이 페어가 없으면
    모든 로컬·CI 환경에서 /api/admin/metrics/queue 가 500 이고, app.state 주입
    기반 테스트 관례도 못 쓴다(설계 §2.5). "열린 빈 큐"가 스텁의 정직한 모양이다
    -- 스텁 백엔드는 아무것도 큐에 넣지 않는다."""

    def read_queue(self):
        return {"name": DMS_QUEUE, "state": "Open"}

    def read_podgroups(self):
        return []
