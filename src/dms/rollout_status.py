"""워크로드 상태 정규화와 수렴 판정. 순수 함수 -- k8s 클라이언트에 접근하지 않는다.

kubernetes 파이썬 클라이언트의 to_dict()는 snake_case 키를, (테스트 fake나 원시
CRD처럼) dict 그대로 온 객체는 camelCase 키를 준다. 여기서 한 표기로 정규화하지
않으면 테스트 페어는 통과하고 프로덕션만 None을 읽어 "영원히 수렴 안 함"으로
보고한다(설계 §4). 판정 함수는 정규화된 dict만 받는다.

판정 함수는 순수하다 -- I/O도 전역 상태도 없다. 기준 시각(since)은 인자로 받는다."""
import re
from datetime import datetime, timezone

_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"      # db.utc_now_iso()와 같은 포맷


def _num(mapping, snake, camel, default=0):
    value = mapping.get(snake, mapping.get(camel))
    return default if value is None else int(value)


def _images(obj):
    spec = obj.get("spec") or {}
    template = spec.get("template") or {}
    pod_spec = template.get("spec") or {}
    return {c.get("name"): c.get("image")
            for c in (pod_spec.get("containers") or []) if c.get("name")}


# --- securityContext 관측(2026-10-26, BACKLOG 「securityContext 드리프트는 배지가 못 본다」) -------------------------
# 포탈 롤아웃(rollout_runner.image_patch_body)은 이미지만 patch 한다 -- 40/41 을 kubectl apply 하지 않은 사이트는 새 이미지를
# 옛 securityContext(예: d128 이전 65532)로 굴리고 3홉 artifact_base_not_writable 로만 드러났다. 그래서 라이브 파드 템플릿의
# 실효 securityContext 를 이 축으로 접어 동봉 매니페스트 값(manifest_tags.manifest_security_contexts)과 비교한다.
# 필드명은 k8s 원문(camelCase)이 화면 어휘다. run_as_* 셋은 파드 수준(PodSecurityContext)에도 있어 **실효값** = 컨테이너 값,
# 없으면 파드 값(k8s 의 우선순위)으로 접는다 -- 한쪽만 컨테이너로 옮겨도 거짓 드리프트가 나지 않게.
SECURITY_FIELDS = ("runAsUser", "runAsGroup", "runAsNonRoot", "allowPrivilegeEscalation",
                   "readOnlyRootFilesystem", "privileged", "capabilities.add", "capabilities.drop")
_SC_KEYS = {  # 화면 이름 -> (snake, camel)
    "runAsUser": ("run_as_user", "runAsUser"),
    "runAsGroup": ("run_as_group", "runAsGroup"),
    "runAsNonRoot": ("run_as_non_root", "runAsNonRoot"),
    "allowPrivilegeEscalation": ("allow_privilege_escalation", "allowPrivilegeEscalation"),
    "readOnlyRootFilesystem": ("read_only_root_filesystem", "readOnlyRootFilesystem"),
    "privileged": ("privileged", "privileged"),
}
_POD_LEVEL = ("runAsUser", "runAsGroup", "runAsNonRoot")
_INT_FIELDS = ("runAsUser", "runAsGroup")
_INT_RE = re.compile(r"-?[0-9]{1,19}")       # ASCII 숫자만(유니코드 숫자 「²」는 int() 가 터진다)


class _Unreadable(ValueError):
    """값을 정규화할 수 없다 -- 그 컨테이너의 관측 전체가 None(모름)이 된다(거짓 경보보다 침묵)."""


def _coerce(field, value):
    """int/bool 필드를 한 형으로 접는다. 매니페스트 파서는 '0'·'false' 같은 문자열을, to_dict()·원시 dict 는 원래 형을
    준다. bool 은 int 의 하위형이라 runAsUser 에 True 가 오면 1 로 읽히지 않게 막는다."""
    if value is None:
        return None
    if field in _INT_FIELDS:
        if isinstance(value, bool):
            raise _Unreadable(field)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and _INT_RE.fullmatch(value.strip()):
            return int(value.strip())
        raise _Unreadable(field)
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    raise _Unreadable(field)


def _caps(value):
    """capabilities.add/drop -> 정렬된 이름 목록. 빈 목록은 None(부재)과 같은 뜻이라 None 으로 접는다."""
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise _Unreadable("capabilities")
    return sorted(value) or None


def _pick(mapping, field):
    snake, camel = _SC_KEYS[field]
    value = mapping.get(snake)
    return mapping.get(camel) if value is None else value


def security_context_view(container_sc, pod_sc=None) -> "dict | None":
    """컨테이너(+파드) securityContext -> {SECURITY_FIELDS 이름: 정규화 값 | None}. 순수 함수.

    snake(to_dict)·camel(원시 dict·매니페스트 파서) 둘 다 받는다. 값을 정규화할 수 없으면(형이 이상함) None -- 그
    컨테이너는 비교하지 않는다. 블록 부재(None)는 「아무것도 선언하지 않음」이라 전 필드 None 의 정상 관측이다."""
    if container_sc is not None and not isinstance(container_sc, dict):
        return None
    csc = container_sc or {}
    psc = pod_sc if isinstance(pod_sc, dict) else {}
    try:
        out = {}
        for field in _SC_KEYS:
            value = _coerce(field, _pick(csc, field))
            if value is None and field in _POD_LEVEL:
                value = _coerce(field, _pick(psc, field))
            out[field] = value
        caps = csc.get("capabilities")
        if caps is not None and not isinstance(caps, dict):
            raise _Unreadable("capabilities")
        caps = caps or {}
        out["capabilities.add"] = _caps(caps.get("add"))
        out["capabilities.drop"] = _caps(caps.get("drop"))
        return out
    except _Unreadable:
        return None


def security_drift(live, manifest) -> "list[dict] | None":
    """라이브 vs 동봉 매니페스트의 실효 securityContext 차이. 둘 중 하나라도 모르면 None(모름 -- 배지 없음, 거짓 경보
    금지), 같으면 [], 다르면 [{"field", "live", "manifest"}] (SECURITY_FIELDS 순서)."""
    if live is None or manifest is None:
        return None
    return [{"field": f, "live": live.get(f), "manifest": manifest.get(f)}
            for f in SECURITY_FIELDS if live.get(f) != manifest.get(f)]


def _security_contexts(obj):
    """파드 템플릿의 컨테이너별 실효 securityContext(security_context_view). 못 읽는 컨테이너는 None."""
    spec = obj.get("spec") or {}
    pod_spec = (spec.get("template") or {}).get("spec") or {}
    pod_sc = pod_spec.get("security_context", pod_spec.get("securityContext"))
    return {c.get("name"): security_context_view(c.get("security_context", c.get("securityContext")), pod_sc)
            for c in (pod_spec.get("containers") or []) if c.get("name")}


def _generations(obj):
    meta = obj.get("metadata") or {}
    status = obj.get("status") or {}
    generation = int(meta.get("generation") or 0)
    # observedGeneration 부재는 -1 -- 0으로 두면 generation도 0인 비정상 객체에서
    # 게이트(observed >= generation)가 통과해 버린다. 모르면 수렴 아님이 안전하다.
    observed = _num(status, "observed_generation", "observedGeneration", default=-1)
    return generation, observed


def _ts(value):
    """조건의 lastUpdateTime을 releases.applied_at과 같은 문자열 포맷으로 접는다.

    to_dict()는 metav1.Time을 tz-aware datetime으로, 원시 dict는 RFC3339 문자열로
    준다 -- 한 축으로 접지 않으면 applied_at과의 비교가 TypeError로 터지거나(전자)
    tz 표기 차이로 어긋난다. apiserver는 항상 UTC로 직렬화하므로 naive면 UTC로 본다."""
    if value is None or isinstance(value, str):
        return value
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc)
    return value.strftime(_TS_FMT)


def _conditions(status):
    # lastUpdateTime을 반드시 실어 나른다 -- 이것이 없으면 "이 롤아웃이 만든
    # 조건"과 "앞 롤아웃이 남긴 sticky 조건"을 구분할 수단이 아예 없다(assess의 I1).
    return [{"type": c.get("type"), "status": c.get("status"),
             "reason": c.get("reason"), "message": c.get("message"),
             "last_update_time": _ts(c.get("last_update_time",
                                           c.get("lastUpdateTime")))}
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
        "security_contexts": _security_contexts(obj),
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
        "security_contexts": _security_contexts(obj),
    }


def _is_stale_pde(cond, since) -> bool:
    """이 PDE 조건이 앞 롤아웃이 남긴 것인가.

    k8s의 Progressing=False/ProgressDeadlineExceeded는 sticky다: 배포 컨트롤러가
    조건을 세대 너머로 그대로 이어 나르고, SetDeploymentCondition은 status/reason이
    안 바뀌면 early-return하므로 옛 조건은 옛 lastUpdateTime을 그대로 유지한다 --
    그래서 lastUpdateTime < applied_at이 "앞 롤아웃의 잔재"의 정확한 판별식이다.
    판별할 근거가 없으면(since 미제공, lastUpdateTime 부재) stale로 보지 않는다:
    PDE는 Deployment의 유일한 실패 확정 수단이라 삼키면 종단이 사라진다."""
    if since is None:
        return False
    at = cond.get("last_update_time")
    return bool(at) and at < since


def assess_deployment(norm: dict, *, since: "str | None" = None) -> "tuple[str, str | None]":
    """("applied" | "progressing" | "failed", detail).

    since: 이 롤아웃의 기준 시각(releases 행의 applied_at). 순수 함수를 유지하기
    위해 시각을 읽지 않고 인자로 받는다.

    세대 게이트를 반드시 먼저 본다 -- 통과 전의 상태 필드는 전부 패치 이전 값이라
    옛 ReplicaSet 기준 거짓 성공이 난다(설계 §3)."""
    if norm["observed_generation"] < norm["generation"]:
        return ("progressing", None)
    # 수렴 검사가 PDE 스캔보다 먼저다. 진짜로 마감을 넘긴 배포는 절대 수렴하지
    # 않으므로 순서를 바꿔도 실패를 놓치지 않고, 대신 "이미 수렴했는데 옛 PDE
    # 조건이 아직 안 지워진" 창(패치 직후 첫 status 쓰기)에서 거짓 실패가 나던
    # 경로가 닫힌다. 그 거짓 실패는 _fail이 남은 컴포넌트까지 rollout_aborted로
    # 죽여 복구 배치 전체를 첫 컴포넌트에서 멈추게 했다.
    if (norm["updated_replicas"] == norm["replicas"]
            and norm["status_replicas"] == norm["updated_replicas"]
            and norm["ready_replicas"] == norm["updated_replicas"]):
        return ("applied", None)
    for cond in norm["conditions"]:
        if (cond.get("type") == "Progressing" and cond.get("status") == "False"
                and cond.get("reason") == "ProgressDeadlineExceeded"
                and not _is_stale_pde(cond, since)):
            # progressDeadlineSeconds=600이 이미 설정돼 있어 10분 상한을 공짜로
            # 물려받는다 -- 자체 상한을 더 두지 않는다(설계 §3).
            return ("failed", f"ProgressDeadlineExceeded: {cond.get('message') or ''}"[:200])
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
