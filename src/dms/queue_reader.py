"""Volcano 큐 가시성 리더(슬라이스 17 설계 §2.1). 세 상태를 절대 뭉개지 않는다:
None = 알 수 없음(403/CRD 부재), [] = 정말 비었음(설계 §4). 여기서 한 번 접히면
어떤 상위 계층도 되살릴 수 없으므로 리더가 이 구분의 최초 권위다."""

# PodGroup condition message 상한(2026-10-16). Volcano 의 Unschedulable message 는 노드마다 사유를 나열해
# 클러스터 크기에 비례해 길어진다 -- 5초 폴링 응답에 그대로 실으면 PodGroup 수 x 노드 수로 부푼다. 화면은
# 한두 줄만 보이므로 앞부분이면 충분하다(잘렸다는 표시는 말줄임표).
CONDITION_MESSAGE_MAX = 300

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
      2026-10-16(사용자 요청 「큐 현황에 대해 조금만 더 자세히」): 항목마다 왜 기다리는지
      (가장 최근 status.conditions 의 reason/message), priorityClassName, 그리고 이
      PodGroup 을 만든 vcjob 이름(ownerReferences 의 Volcano Job)을 더 싣는다. vcjob 이름은
      라우트가 data_jobs.phase_refs("vcjob/<이름>")와 대조해 요청·잡을 붙이는 열쇠다 --
      소유자 참조가 없으면 None 이고, 이름 접두 폴백은 리더가 아니라 대조하는 쪽
      (queue_pipeline)의 몫이다(리더는 k8s 가 말한 것만 옮긴다). reason/message 는 표시만
      하고 어디서도 분기하지 않는다 -- 실물 문자열이 계약이 아니다(슬라이스 17 설계 §7).

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
            status = item.get("status") or {}
            cond = _newest_condition(status.get("conditions"))
            out.append({
                "name": meta.get("name") or "",
                "phase": status.get("phase"),
                "min_member": spec.get("minMember"),
                "created_at": meta.get("creationTimestamp"),
                "priority_class": _str_or_none(spec.get("priorityClassName")),
                "reason": _str_or_none(cond.get("reason")),
                "message": _clip(_str_or_none(cond.get("message"))),
                "vcjob": _owner_vcjob(meta.get("ownerReferences")),
            })
        return out


def _str_or_none(value):
    # k8s 오브젝트는 신뢰 입력이 아니다(다른 컨트롤러·사람이 쓴다) -- 문자열이 아닌 값을 화면까지
    # 흘리지 않는다. 빈 문자열도 "없음"이다(Volcano 는 빈 reason 을 omitempty 로 빼지만 보장이 아니다).
    return value if isinstance(value, str) and value else None


def _clip(text):
    if text is None or len(text) <= CONDITION_MESSAGE_MAX:
        return text
    return text[:CONDITION_MESSAGE_MAX - 1] + "…"


def _newest_condition(conditions) -> dict:
    """status.conditions 중 가장 최근 항목(lastTransitionTime 최대, 같으면 뒤에 있는 것). 없거나 모양이
    깨졌으면 {} -- reason/message 가 None 으로 강등될 뿐 항목은 목록에 남는다(설계 §4 항목 단위 강등).
    lastTransitionTime 은 RFC3339 "Z" 문자열이라 사전순 = 시간순이다. 시각이 없는 항목은 ""(가장 옛것)로
    본다 -- 시각이 있는 항목을 이기지 못한다."""
    best, best_key = {}, None
    if not isinstance(conditions, list):
        return best
    for i, cond in enumerate(conditions):
        if not isinstance(cond, dict):
            continue
        ts = cond.get("lastTransitionTime")
        key = (ts if isinstance(ts, str) else "", i)
        if best_key is None or key > best_key:
            best, best_key = cond, key
    return best


def _owner_vcjob(owner_refs):
    """이 PodGroup 을 만든 Volcano Job(batch.volcano.sh 의 kind Job) 이름. k8s batch/v1 Job 등 다른 소유자는
    보지 않는다 -- 같은 kind 이름이라도 다른 API 그룹이면 DMS vcjob 이 아니다."""
    if not isinstance(owner_refs, list):
        return None
    for ref in owner_refs:
        if (isinstance(ref, dict) and ref.get("kind") == "Job"
                and str(ref.get("apiVersion") or "").startswith("batch.volcano.sh/")):
            return _str_or_none(ref.get("name"))
    return None


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
