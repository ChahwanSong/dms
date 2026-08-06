"""워크로드 상태 정규화와 수렴 판정. 순수 함수 -- k8s 클라이언트에 접근하지 않는다.

kubernetes 파이썬 클라이언트의 to_dict()는 snake_case 키를, (테스트 fake나 원시
CRD처럼) dict 그대로 온 객체는 camelCase 키를 준다. 여기서 한 표기로 정규화하지
않으면 테스트 페어는 통과하고 프로덕션만 None을 읽어 "영원히 수렴 안 함"으로
보고한다(설계 §4). 판정 함수는 정규화된 dict만 받는다."""


def _num(mapping, snake, camel, default=0):
    value = mapping.get(snake, mapping.get(camel))
    return default if value is None else int(value)


def _images(obj):
    spec = obj.get("spec") or {}
    template = spec.get("template") or {}
    pod_spec = template.get("spec") or {}
    return {c.get("name"): c.get("image")
            for c in (pod_spec.get("containers") or []) if c.get("name")}


def _generations(obj):
    meta = obj.get("metadata") or {}
    status = obj.get("status") or {}
    generation = int(meta.get("generation") or 0)
    # observedGeneration 부재는 -1 -- 0으로 두면 generation도 0인 비정상 객체에서
    # 게이트(observed >= generation)가 통과해 버린다. 모르면 수렴 아님이 안전하다.
    observed = _num(status, "observed_generation", "observedGeneration", default=-1)
    return generation, observed


def _conditions(status):
    return [{"type": c.get("type"), "status": c.get("status"),
             "reason": c.get("reason"), "message": c.get("message")}
            for c in (status.get("conditions") or [])]


def normalize_deployment(obj: dict) -> dict:
    status = obj.get("status") or {}
    spec = obj.get("spec") or {}
    generation, observed = _generations(obj)
    replicas = spec.get("replicas")
    return {
        "kind": "Deployment",
        "generation": generation,
        "observed_generation": observed,
        "replicas": 1 if replicas is None else int(replicas),  # k8s 기본값 1
        "status_replicas": _num(status, "replicas", "replicas"),
        "updated_replicas": _num(status, "updated_replicas", "updatedReplicas"),
        "ready_replicas": _num(status, "ready_replicas", "readyReplicas"),
        "conditions": _conditions(status),
        "images": _images(obj),
    }


def normalize_daemonset(obj: dict) -> dict:
    status = obj.get("status") or {}
    generation, observed = _generations(obj)
    return {
        "kind": "DaemonSet",
        "generation": generation,
        "observed_generation": observed,
        "desired_number_scheduled": _num(status, "desired_number_scheduled",
                                         "desiredNumberScheduled"),
        "updated_number_scheduled": _num(status, "updated_number_scheduled",
                                         "updatedNumberScheduled"),
        "number_ready": _num(status, "number_ready", "numberReady"),
        # 0이면 필드 자체가 빠진다 -- unset을 0으로 읽지 않으면 영원히 progressing
        "number_unavailable": _num(status, "number_unavailable", "numberUnavailable"),
        "number_misscheduled": _num(status, "number_misscheduled",
                                    "numberMisscheduled"),
        "images": _images(obj),
    }


def assess_deployment(norm: dict) -> "tuple[str, str | None]":
    """("applied" | "progressing" | "failed", detail).

    세대 게이트를 반드시 먼저 본다 -- 통과 전의 상태 필드는 전부 패치 이전 값이라
    옛 ReplicaSet 기준 거짓 성공이 난다(설계 §3)."""
    if norm["observed_generation"] < norm["generation"]:
        return ("progressing", None)
    for cond in norm["conditions"]:
        if (cond.get("type") == "Progressing" and cond.get("status") == "False"
                and cond.get("reason") == "ProgressDeadlineExceeded"):
            # progressDeadlineSeconds=600이 이미 설정돼 있어 10분 상한을 공짜로
            # 물려받는다 -- 자체 상한을 더 두지 않는다(설계 §3).
            return ("failed", f"ProgressDeadlineExceeded: {cond.get('message') or ''}"[:200])
    if (norm["updated_replicas"] == norm["replicas"]
            and norm["status_replicas"] == norm["updated_replicas"]
            and norm["ready_replicas"] == norm["updated_replicas"]):
        return ("applied", None)
    for cond in norm["conditions"]:
        if cond.get("type") == "ReplicaFailure" and cond.get("status") == "True":
            # 종단이 아니라 노출이다 -- admission 오류(/cephfs hostPath 없음 등)를
            # 운영자가 실패 확정 전에 볼 수 있어야 한다. 확정은 PDE가 한다.
            return ("progressing", (cond.get("message") or cond.get("reason") or "")[:200])
    return ("progressing", None)


def assess_daemonset(norm: dict) -> "tuple[str, str | None]":
    """("applied" | "progressing", detail). DaemonSet에는 conditions도
    progressDeadlineSeconds도 없다(설계 §3) -- 실패 확정은 여기서 하지 않고
    RolloutWatcher의 벽시계 타임아웃이 한다."""
    if norm["observed_generation"] < norm["generation"]:
        return ("progressing", None)
    desired = norm["desired_number_scheduled"]
    if (norm["updated_number_scheduled"] == desired
            and norm["number_ready"] == desired
            and norm["number_unavailable"] == 0
            and norm["number_misscheduled"] == 0):
        return ("applied", None)
    return ("progressing", None)
