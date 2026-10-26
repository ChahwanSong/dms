"""k8s 신원 모드의 권한 판정과 PV 주소 층(dms-k8s 설계 D2~D4·D8·D12~D14·D17, 2026-10-25).

dms-k8s 는 **k8s 신원 모드 전용**이다(2026-10-26 사용자 결정 (d) -- LDAP 코드·ldap 모드 분기를 지웠다; LDAP 은 dms-ssc 라인만):
  - 모든 단건 잡이 root 다(D2). 서버가 payload.run_as_root=True 를 싣고(routes_requests.submit), root 허용은
    identity.root_allowed(세션 인증 요청인가)가 제출·계획·컨펌 세 지점에서 같은 술어로 본다(D3).
  - 권한 = k8s RoleBinding 맵핑(repositories.k8s.access_for -- 인식된 `<id>-clusterrole-admin|edit` 바인딩이 있는 (클러스터,
    namespace) 의 모든 PVC 읽기·쓰기). 판정 기준 ID 는 **요청자**다(면제 관리자의 「사용자 지정」은 화면 필터 + owner_username
    기록일 뿐이라 서버 판정에 쓰이지 않는다).
  - 면제(unrestricted, D4) = 기존 api.auth.can_run_as_root(관리자 + 특권 목록/계정 부여 + 세션). 면제자는 직접 경로(raw)와
    전체 PV. 그 밖(일반 사용자·목록 밖 관리자)은 **PV 주소만** + 맵핑 판정. 요청 행만 보는 planner·컨펌은 같은 의미의
    unrestricted_request 를 쓴다(배치 자식은 면제 -- 배치는 특권 세션 관리자 전용 화면에서만 생긴다).
「root 실행」과 「맵핑 판정」을 따로 끄는 스위치를 만들지 마라 -- 「root 인데 권한 판정이 없는」 중간 상태가 생긴다(예전 ldap
모드 스위치가 그 둘을 한 번에 껐다 -- config 는 DMS_IDENTITY_MODE=ldap 을 기동 거부한다).

PV 주소 층(D8): 제출 때 (cluster, namespace, pvc[, 하위 경로]) → (storage_name, target = root[/하위 경로]) 로 바꿔 **기존**
build_data_payload 를 그대로 부르고 PV 메타를 payload 에 덧붙인다(PV 블록 {cluster, namespace, pvc, volume, storage_class, root,
path}). resource_key·planner 스토리지 어드미션·노드 배치·stepper._abs·실행·사용량 키는 무변경이다.

집행 지점(한 함수, 여러 지점):
  - resolve_side      : 제출의 PV 한 쪽 해석(클러스터 동기화 → PVC → 경로 → D12 PV 루트 rm 금지).
  - recheck_request   : planner·컨펌의 재확인(D13). DB 가 신뢰 경계라(create_job 무검증 INSERT) 요청 행의 pv 블록을 현재
                        스냅숏으로 다시 해석하고, 비면제 요청은 모든 쪽에 pv 블록이 있어야 하며 맵핑을 다시 판정한다.
  - pair_gate_exempt  : 제출·planner 3b·컨펌의 sync 허용 쌍 면제(D14 -- PV↔PV 는 PV 권한이 판정한다).
PV 목적지 sync 의 결과 소유(2026-10-26 사용자 지시): 제출이 destination_pv 블록에 owner_rule(OWNER_RULES -- 기본 "storage" =
목적지 스토리지 기본 소유자를 owner 로 기록, 특권 운영자 옵션 "pv_root" = 잰 PV 루트 소유자)을 싣고 recheck_request 가 다시 본다.
실행은 stepper._build_spec → execution_manifests._auto_chown(--chown).
사유 코드는 DomainValidationError("<리터럴>") 로 낸다 -- 사유 코드 AST 그물(tests/test_reason_codes_coverage.py)이 읽는 모양이다.
"""
import posixpath

from .domain import DomainValidationError, validate_relative_path
from .identity import privilege_eligible
from .privileged import privileged_names

# 연산 → 쪽마다 (pv 블록 키, 스토리지 키, 경로 키). 단건 payload 의 모양(domain.build_data_payload)과 같다.
_SIDES = {
    "scan": (("pv", "storage", "target"),),
    "rm": (("pv", "storage", "target"),),
    "sync": (("source_pv", "source_storage", "source"), ("destination_pv", "destination_storage", "destination")),
}
PV_KEYS = ("pv", "source_pv", "destination_pv")
# PV 목적지 sync 의 결과 소유 규칙(2026-10-26 사용자 지시 -- destination_pv 블록의 owner_rule): "storage" = 목적지 스토리지 기본
# 소유자(기본, 일반 사용자는 늘 이것 -- 블록 owner 에 그 값을 기록한다), "pv_root" = preflight 가 잰 PV 루트 소유자(특권 운영자
# 옵션). 제출(routes_requests.submit)·재확인(recheck_request)이 이 목록 하나를 본다. 튜플인 이유: 변조된 비해시 값(리스트)도
# 멤버십 검사가 TypeError 없이 「목록 밖」으로 끝나게.
OWNER_RULES = ("storage", "pv_root")
# 제출 라우트의 HTTP 상태(나머지 사유는 422). 모름(스냅숏 없음)은 409, 권한은 403.
SUBMIT_STATUS = {"k8s_mapping_unknown": 409, "raw_path_not_allowed": 403, "k8s_access_denied": 403}


def sides(operation) -> tuple:
    """그 연산의 쪽 목록(알 수 없는 연산은 () -- 제출 검증이 invalid_operation 으로 끊는다)."""
    return _SIDES.get(operation, ())


def unrestricted_request(req, *, settings, repos) -> bool:
    """요청 행 기반 맵핑 면제(D4) -- 제출의 can_run_as_root(관리자 + 특권 목록/부여 + 세션)와 같은 값이 나와야 한다.
    planner·컨펌이 쓴다(그 시점의 계정 역할·목록으로 -- 강등·목록 제외·비활성화는 면제를 잃는다). 배치 자식은 면제다
    (배치 생성·확인이 이미 특권 세션 관리자 게이트를 지났고, 자식의 root 여부는 IF_ELIGIBLE 이 따로 본다)."""
    if not isinstance(req, dict):
        return False
    if req.get("batch_id"):
        return True
    requester = req.get("requester_id")
    account = repos.accounts.get(requester) if isinstance(requester, str) else None
    if account is None or account.get("disabled") or account.get("role") != "admin":
        return False
    return privilege_eligible(requester_id=requester, allow_privileged=settings.allow_privileged_requesters,
                              privileged_requesters=privileged_names(settings, repos),
                              session_authenticated=(req.get("auth_method") == "session"))


def scan_allowed(*, unrestricted: bool) -> bool:
    """scan 은 특권 운영자만(2026-10-26 사용자 결정 (g)): 면제자(can_run_as_root -- 관리자 + 특권 목록·부여 + 세션 = 특권
    운영자)만 scan 을 낸다. 제출(routes_requests, 403 scan_unrestricted_only)·/me(user_operations)·planner·컨펌
    (recheck_request)이 이 술어 하나를 쓴다 -- 배치 자식은 unrestricted_request 가 면제다. 규칙이 바뀌면 여기 한 곳만 고친다."""
    return bool(unrestricted)


def pair_gate_exempt(payload) -> bool:
    """sync 허용 쌍 면제(D14): 모든 쪽이 PV 주소(pv 블록). 제출·planner 3b·컨펌이 같은 술어를 쓴다 -- planner·컨펌은
    recheck_request 가 pv 블록을 현재 스냅숏으로 확인한 **뒤에** 부른다. 관리자 전용 스토리지 판정(storage_admin_only)은
    면제하지 않는다(해석된 storage_name 에 그대로 적용)."""
    if not isinstance(payload, dict):
        return False
    return all(isinstance(payload.get(key), dict) for key, _, _ in _SIDES["sync"])


def normalize_subpath(subpath) -> str:
    """PV 안 하위 경로(빈 값·'.'·'./' = PV 전체 = ''). 절대 경로·NUL·'..' 성분은 unsafe_path(제출 검증의 기존 코드)."""
    if subpath is None or subpath == "":
        return ""
    if (not isinstance(subpath, str) or subpath.startswith("/") or "\x00" in subpath
            or ".." in subpath.split("/")):
        raise DomainValidationError("unsafe_path", repr(subpath))
    norm = posixpath.normpath(subpath)
    return "" if norm == "." else norm


def _raise_unusable(resolved) -> None:
    """repositories.k8s.resolve_volume 의 선택 불가 사유를 사유 코드로(리터럴 -- AST 그물)."""
    reason = resolved.get("reason")
    detail = resolved.get("problem") or ""
    if reason == "pv_not_bound":
        raise DomainValidationError("pv_not_bound", detail)
    if reason == "pv_storageclass_unmapped":
        raise DomainValidationError("pv_storageclass_unmapped", detail)
    if reason is not None or not resolved.get("selectable") or not resolved.get("root") \
            or not resolved.get("storage_name"):
        raise DomainValidationError("pv_path_unresolved", detail)


def resolve_side(repos, ref: dict, subpath, *, operation) -> dict:
    """제출의 PV 한 쪽 → {"storage", "target", "block"}. 순서(설계 §6): 클러스터가 있고 한 번 이상 동기화됐나(아니면 409
    k8s_mapping_unknown -- 모름 ≠ 없음) → PVC 가 스냅숏에 있나(pv_not_found) → Bound·SC 매핑·경로 렌더(pv_not_bound·
    pv_storageclass_unmapped·pv_path_unresolved) → 하위 경로(unsafe_path) → D12: rm 이고 **정규화한 최종 target == PV 루트**면
    pv_root_rm_forbidden(''·'.'·'./' 우회가 같은 판정에 걸린다 -- 하위 경로 문자열이 비었는지를 보지 않는다)."""
    cluster, namespace, pvc = ref.get("cluster"), ref.get("namespace"), ref.get("pvc")
    if not isinstance(cluster, str) or not repos.k8s.cluster_synced(cluster):
        raise DomainValidationError("k8s_mapping_unknown", str(cluster))
    resolved = repos.k8s.resolve_pvc(cluster, namespace, pvc)
    if resolved is None:
        raise DomainValidationError("pv_not_found", f"{cluster}/{namespace}/{pvc}")
    _raise_unusable(resolved)
    sub = normalize_subpath(subpath)
    root = resolved["root"]
    target = posixpath.join(root, sub) if sub else root
    if operation == "rm" and target == root:
        raise DomainValidationError("pv_root_rm_forbidden", f"{cluster}/{namespace}/{pvc}")
    block = {"cluster": cluster, "namespace": namespace, "pvc": pvc, "volume": resolved.get("volume"),
             "storage_class": resolved.get("storage_class"), "root": root, "path": sub}
    # storage_owner: 해석된 스토리지의 기본 소유자 "uid:gid"(repositories.k8s.storage_default_owner -- 무효면 None). 블록에는
    # 싣지 않는다 -- 목적지 sync 일 때만 제출이 owner_rule 과 함께 기록한다(소스 블록은 이 기능 전과 같은 모양).
    return {"storage": resolved["storage_name"], "target": target, "block": block,
            "storage_owner": resolved.get("storage_owner")}


def side_label(block: dict) -> str:
    """사람이 읽는 PV 표기(D17): 「PV 이름 — namespace (storageclass) / 하위경로」(하위 경로가 비면 뒤를 뗀다)."""
    base = f"{block.get('pvc')} — {block.get('namespace')} ({block.get('storage_class')})"
    return f"{base} / {block['path']}" if block.get("path") else base


def target_label(operation, payload) -> "str | None":
    """requests.target_label(D17) -- 작업 목록 경로 검색(RequestsRepository.list path_like)이 resource_key 표기와 OR 로 본다.
    PV 쪽은 side_label, 직접 경로 쪽은 화면 「대상」과 같은 「스토리지:경로」, sync 는 「 → 」로 잇는다. PV 쪽이 하나도 없으면
    None(raw 요청은 resource_key 표기로 이미 검색된다)."""
    parts, any_pv = [], False
    for pv_key, storage_key, path_key in sides(operation):
        block = payload.get(pv_key)
        if isinstance(block, dict):
            parts.append(side_label(block))
            any_pv = True
        else:
            parts.append(f"{payload.get(storage_key)}:{payload.get(path_key)}")
    return " → ".join(parts) if any_pv else None


def _block_ref(block) -> "tuple | None":
    if not isinstance(block, dict):
        return None
    ref = (block.get("cluster"), block.get("namespace"), block.get("pvc"))
    return ref if all(isinstance(v, str) and v for v in ref) else None


def recheck_request(repos, req, *, settings) -> None:
    """planner·컨펌의 재확인(D13) -- 통과하면 None, 아니면 DomainValidationError(사유 코드). 두 곳이 이 함수 하나를 쓴다.

    쪽마다:
      - pv 블록이 없다(직접 경로): 비면제 요청이면 raw_path_not_allowed(DB 직접 삽입·대기 중 면제 상실).
      - pv 블록이 있다: 모양이 틀렸거나 그 PVC·클러스터가 지금 스냅숏에 없으면 pv_not_found(삭제된 클러스터도 -- 모름
        k8s_mapping_unknown 으로 유예하지 않는다, 종단). 선택 불가가 됐으면 그 사유(pv_not_bound·pv_storageclass_unmapped·
        pv_path_unresolved). 해석한 (storage, root) 가 payload 의 것과 다르면 pv_moved(PVC 재생성으로 VOLUME 이 바뀜·SC 매핑·
        템플릿 변경 -- 확인 창이 보인 경로와 실행 경로가 갈라지지 않게). 그 쪽 target 이 root 와 같거나 root+'/' 아래가
        아니면 pv_target_outside(자기 PV 블록 + 남의 target 인 변조 행). rm 이 PV 루트면 pv_root_rm_forbidden(D12 재확인).
        비면제 요청이면 요청자의 맵핑을 다시 판정한다(k8s_access_denied -- 권한 회수 반영).
      - sync 의 destination_pv 블록에 결과 소유 규칙(owner_rule, 2026-10-26)이 있으면: 목록(OWNER_RULES) 밖이면
        pv_owner_not_applicable(변조), "pv_root" 인데 비면제 요청이면 pv_owner_root_not_allowed(제출 403 과 같은 술어 -- 대기 중
        특권 상실·DB 직접 삽입), "storage" 면 기록된 owner 가 목적지 스토리지의 **현재** 기본 소유자와 같아야 한다(아니면
        storage_owner_changed -- 제출 뒤 관리자가 기본값을 바꿈·변조; 확인 창에 보인 소유자와 실행이 갈라지지 않게. 현재 값이
        무효면 invalid_storage_owner). 명시 chown 이 있는 요청은 결과가 chown 값이라 대조하지 않는다. 규칙이 **없는** 블록은 이 기능 전에 제출된 옛 행이라 새 검사를 하지 않는다(종전처럼
        PV 루트 소유자를 잰다 -- 부재를 거부로 "강화" 하면 배포 순간 진행 중인 요청이 전부 끊긴다)."""
    payload = req.get("payload") if isinstance(req, dict) else None
    if not isinstance(payload, dict):
        return
    operation = req.get("operation")
    exempt = None
    # 명시 chown 은 특권 운영자만(2026-10-26 -- PV 목적지 결과 소유자는 목적지 스토리지 기본 소유자로 자동, 제출 게이트와 같은 규칙).
    options = payload.get("options") if isinstance(payload.get("options"), dict) else {}
    if "chown" in options:
        exempt = unrestricted_request(req, settings=settings, repos=repos)
        if not exempt:
            raise DomainValidationError("chown_not_allowed", "explicit chown by a non-privileged requester")
    if "chmod" in options:
        if exempt is None:
            exempt = unrestricted_request(req, settings=settings, repos=repos)
        if not exempt:
            raise DomainValidationError("chmod_not_allowed", "explicit chmod by a non-privileged requester")
    # scan 은 특권 운영자만(2026-10-26 (g), scan_allowed) -- DB 직접 삽입·대기 중 특권 상실.
    if operation == "scan":
        if exempt is None:
            exempt = unrestricted_request(req, settings=settings, repos=repos)
        if not scan_allowed(unrestricted=exempt):
            raise DomainValidationError("scan_unrestricted_only", "scan by a non-privileged requester")
    for pv_key, storage_key, path_key in sides(operation):
        block = payload.get(pv_key)
        if block is None:
            if exempt is None:
                exempt = unrestricted_request(req, settings=settings, repos=repos)
            if not exempt:
                raise DomainValidationError("raw_path_not_allowed", pv_key)
            continue
        ref = _block_ref(block)
        if ref is None:
            raise DomainValidationError("pv_not_found", f"malformed {pv_key}")
        resolved = repos.k8s.resolve_pvc(*ref)
        if resolved is None:
            raise DomainValidationError("pv_not_found", "/".join(ref))
        _raise_unusable(resolved)
        root = resolved["root"]
        if resolved["storage_name"] != payload.get(storage_key) or root != block.get("root"):
            raise DomainValidationError(
                "pv_moved", f"{'/'.join(ref)}: {payload.get(storage_key)}:{block.get('root')} -> "
                            f"{resolved['storage_name']}:{root}")
        target = payload.get(path_key)
        try:
            norm = validate_relative_path(target) if isinstance(target, str) else None
        except DomainValidationError:
            norm = None
        if norm is None or not (norm == root or norm.startswith(root + "/")):
            raise DomainValidationError("pv_target_outside", f"{pv_key}: {target!r} not under {root}")
        if operation == "rm" and norm == root:
            raise DomainValidationError("pv_root_rm_forbidden", "/".join(ref))
        if exempt is None:
            exempt = unrestricted_request(req, settings=settings, repos=repos)
        if not exempt and repos.k8s.access_for(req.get("requester_id"), ref[0], ref[1]) is None:
            raise DomainValidationError("k8s_access_denied", f"{req.get('requester_id')} @ {ref[0]}/{ref[1]}")
        # 명시 chown 이 있으면 결과는 그 값이라(위 chown 분기가 면제를 이미 확인했다) 기록된 규칙을 대조하지 않는다 -- 쓰이지
        # 않는 스토리지 값의 변경으로 운영자 요청을 거부하지 않게(리뷰; 제출도 chown 이면 규칙을 기록하지 않는다).
        if operation == "sync" and pv_key == "destination_pv" and "owner_rule" in block and "chown" not in options:
            _recheck_owner_rule(block, resolved, exempt=exempt)


def _recheck_owner_rule(block: dict, resolved: dict, *, exempt: bool) -> None:
    """PV 목적지 sync 의 결과 소유 규칙 재확인(recheck_request docstring 의 마지막 항목). resolved = 그 목적지 PVC 의 현재 해석
    (repositories.k8s.resolve_pvc -- storage_owner 는 storage_default_owner 로 정규화된 값)."""
    rule = block.get("owner_rule")
    if rule not in OWNER_RULES:
        raise DomainValidationError("pv_owner_not_applicable", f"owner_rule {rule!r}")
    if rule == "pv_root":
        if not exempt:
            raise DomainValidationError("pv_owner_root_not_allowed", "pv_root owner rule by a non-privileged requester")
        return
    current = resolved.get("storage_owner")
    if current is None:
        raise DomainValidationError("invalid_storage_owner", f"storage {resolved.get('storage_name')}")
    if block.get("owner") != current:
        raise DomainValidationError("storage_owner_changed", f"{block.get('owner')!r} -> {current}")
