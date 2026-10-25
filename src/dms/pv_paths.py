"""PV → DMS 스토리지 안 상대 경로(dms-k8s 설계 D9, 2026-10-25). 순수 함수.

PV 는 「주소 층」이다(D8): 제출 때 (cluster, namespace, pvc) 를 (storage_name, managed_root 기준 상대 경로)로 바꿔 기존
경로 파이프라인(stepper._abs·봉쇄·resource_key)을 그대로 탄다. 그 상대 경로를 만드는 규칙이 여기 있다.

템플릿은 managed_root 기준 상대 경로이고 자리표시자를 쓴다:
  {volume}            PV 이름(kubectl get pvc 의 VOLUME 열 -- pvc-<uuid>·k8s-<10hex>). 화면 용어 「볼륨」.
  {pvc}               PVC 이름(화면 용어 「PV 이름」)
  {namespace}         PVC 의 namespace
  {storageclass}      StorageClass 이름
  {subdir}            PV.spec.csi.volumeAttributes 의 subdir(키 대소문자 무시 -- csi-driver-nfs 는 SC 에 쓴 대소문자
                      그대로 'subDir' 를 둔다)
  {attr:<키>}         volumeAttributes[<키>](대소문자 무시) -- NetApp Trident 의 internalName 류
  {handle:<구분자>:<i>}   volume_handle 을 <구분자>로 나눈 i 번째(0부터). 'i+' 는 i 번째부터 끝까지를 같은 구분자로 다시
                      잇는다 -- WekaFS 핸들 '<type>/<fs>/<innerPath…>' 의 innerPath 처럼 경로가 여러 성분일 때.

우선순위 2단(effective_template): StorageClass 매핑의 템플릿 덮어쓰기 > 스토리지 backend_type 프리셋(PRESETS). 새 벤더는
프리셋 한 줄 또는 SC 덮어쓰기다. Pure FlashBlade(PV 마다 별도 export)는 이 모델 밖이다(PV 별 스토리지 등록 -- BACKLOG).

렌더 값 검증(실패 = 해석 불가 -- 호출자는 pv_path_unresolved 로 보인다. 모름(null)은 실패가 아니다):
  - {subdir}·{attr:}·{handle:} 값은 '/' 를 허용하고 앞뒤 '/' 를 벗긴다. 그 밖의 값은 '/' 금지.
  - 공통으로 빈 값·NUL·'.'·'..' 성분 금지.
  - 결과는 domain.validate_relative_path 를 통과해야 한다('.'(스토리지 루트 전체)도 거부된다).
"""
import re

from .domain import DomainValidationError, managed_abs_path, validate_relative_path

# backend_type → 기본 템플릿. 키는 storages.backend_type 값(D11 -- 라벨이자 PV 경로 프리셋 키).
PRESETS = {
    # IBM Storage Scale(GPFS) CSI classic fileset: {fsMount}/{pv}/{pv}-data (managed_root = 그 FS 마운트)
    "gpfs": "{volume}/{volume}-data",
    # Dell PowerScale(OneFS): IsiPath(/ifs/data/csi)/{pv} (managed_root = /ifs 마운트)
    "onefs": "data/csi/{volume}",
    # csi-driver-nfs: 치환이 끝난 subdir 가 volumeAttributes 에 있다(subDir 미지정이면 PV 이름)
    "nfs": "{subdir}",
}

TEMPLATE_MAX = 512
_SIMPLE = ("volume", "pvc", "namespace", "storageclass")
_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
_ATTR_KEY = re.compile(r"[A-Za-z0-9._/-]{1,253}$")
_HANDLE = re.compile(r"handle:(?P<sep>.+):(?P<idx>\d{1,3})(?P<rest>\+?)$", re.S)


class PvPathUnresolved(Exception):
    """해석 불가(값이 없거나 안전하지 않다). detail 은 사람이 읽는 짧은 설명 -- 사유 코드는 호출자가 정한다."""


def _parse_placeholder(body: str):
    if body in _SIMPLE or body == "subdir":
        return (body,)
    if body.startswith("attr:"):
        key = body[len("attr:"):]
        if not _ATTR_KEY.match(key):
            raise DomainValidationError("invalid_pv_path_template", f"bad attr key {key!r}")
        return ("attr", key)
    m = _HANDLE.match(body)
    if m is not None and "\x00" not in m.group("sep"):
        return ("handle", m.group("sep"), int(m.group("idx")), m.group("rest") == "+")
    raise DomainValidationError("invalid_pv_path_template", f"unknown placeholder {{{body}}}")


def _tokens(template: str):
    """템플릿 → [("lit", text) | ("ph", parsed)]. 짝 없는 중괄호는 거부."""
    out = []
    pos = 0
    for m in _PLACEHOLDER.finditer(template):
        lit = template[pos:m.start()]
        if "{" in lit or "}" in lit:
            raise DomainValidationError("invalid_pv_path_template", "unbalanced braces")
        if lit:
            out.append(("lit", lit))
        out.append(("ph", _parse_placeholder(m.group(1))))
        pos = m.end()
    tail = template[pos:]
    if "{" in tail or "}" in tail:
        raise DomainValidationError("invalid_pv_path_template", "unbalanced braces")
    if tail:
        out.append(("lit", tail))
    return out


def validate_template(template) -> str:
    """경로 템플릿 형식 검증(SC 매핑 저장 때). 앞뒤 공백을 벗긴 값을 돌려준다. 실패 = invalid_pv_path_template.

    자리표시자가 하나도 없는 템플릿은 거부한다 -- 그 SC 의 모든 PV 가 같은 경로가 된다. 리터럴 부분은 절대경로·'.'·'..'
    성분을 가질 수 없다(견본 값으로 렌더해 validate_relative_path 를 통과해야 한다)."""
    if not isinstance(template, str):
        raise DomainValidationError("invalid_pv_path_template", "not a string")
    text = template.strip()
    if not text or len(text) > TEMPLATE_MAX or "\x00" in text:
        raise DomainValidationError("invalid_pv_path_template", "empty or too long")
    if text.startswith("/"):
        raise DomainValidationError("invalid_pv_path_template", "must be relative to managed_root")
    tokens = _tokens(text)
    if not any(kind == "ph" for kind, _ in tokens):
        raise DomainValidationError("invalid_pv_path_template", "needs at least one placeholder")
    for kind, value in tokens:
        if kind == "lit" and any(part in (".", "..") for part in value.split("/")):
            raise DomainValidationError("invalid_pv_path_template", "'.'/'..' segment")
    # 견본 렌더: 자리표시자 자리를 안전한 값으로 채워 리터럴 조합이 상대 경로가 되는지 본다.
    sample = "".join(value if kind == "lit" else "x" for kind, value in tokens)
    try:
        validate_relative_path(sample)
    except DomainValidationError:
        raise DomainValidationError("invalid_pv_path_template", "renders to an unsafe path")
    if any(part == "" for part in sample.split("/")):
        raise DomainValidationError("invalid_pv_path_template", "empty path segment")
    return text


def effective_template(*, sc_template, backend_type) -> "tuple[str | None, str | None]":
    """(실효 템플릿, 출처) -- 출처 "storageclass"(매핑 덮어쓰기) | "preset"(backend_type 프리셋) | None(없음)."""
    if isinstance(sc_template, str) and sc_template.strip():
        return sc_template.strip(), "storageclass"
    preset = PRESETS.get(backend_type) if isinstance(backend_type, str) else None
    if preset:
        return preset, "preset"
    return None, None


def _lookup_ci(mapping, key):
    if not isinstance(mapping, dict):
        return None
    if key in mapping:
        return mapping[key]
    low = key.lower()
    for k, v in mapping.items():
        if isinstance(k, str) and k.lower() == low:
            return v
    return None


def _check_simple(name, value) -> str:
    if not isinstance(value, str) or not value:
        raise PvPathUnresolved(f"{name} is missing")
    if "/" in value or "\x00" in value or value in (".", ".."):
        raise PvPathUnresolved(f"{name} is not a single path segment")
    return value


def _check_multi(name, value) -> str:
    if not isinstance(value, str):
        raise PvPathUnresolved(f"{name} is missing")
    stripped = value.strip("/")
    if not stripped or "\x00" in stripped:
        raise PvPathUnresolved(f"{name} is empty")
    if any(part in ("", ".", "..") for part in stripped.split("/")):
        raise PvPathUnresolved(f"{name} has an empty, '.' or '..' segment")
    return stripped


def render(template: str, *, volume=None, pvc=None, namespace=None, storage_class=None,
           volume_attributes=None, volume_handle=None) -> str:
    """템플릿을 렌더해 managed_root 기준 상대 경로를 돌려준다(정규화됨). 해석 불가는 PvPathUnresolved."""
    try:
        tokens = _tokens(template)
    except DomainValidationError as e:
        raise PvPathUnresolved(f"bad template: {e}")
    simple = {"volume": volume, "pvc": pvc, "namespace": namespace, "storageclass": storage_class}
    parts = []
    for kind, value in tokens:
        if kind == "lit":
            parts.append(value)
            continue
        head = value[0]
        if head in simple:
            parts.append(_check_simple(head, simple[head]))
        elif head == "subdir":
            parts.append(_check_multi("subdir", _lookup_ci(volume_attributes, "subdir")))
        elif head == "attr":
            parts.append(_check_multi(f"attr:{value[1]}", _lookup_ci(volume_attributes, value[1])))
        else:  # handle
            _, sep, idx, rest = value
            if not isinstance(volume_handle, str) or not volume_handle:
                raise PvPathUnresolved("volume_handle is missing")
            fields = volume_handle.split(sep)
            if idx >= len(fields):
                raise PvPathUnresolved(f"volume_handle has no field {idx}")
            picked = sep.join(fields[idx:]) if rest else fields[idx]
            parts.append(_check_multi(f"handle:{idx}", picked))
    rendered = "".join(parts)
    try:
        return validate_relative_path(rendered)
    except DomainValidationError:
        raise PvPathUnresolved("rendered path is not a safe relative path")


def resolve_root(*, template, managed_root, **values) -> dict:
    """{root, abs_root, problem}. problem None 이면 root(상대)·abs_root(절대)가 채워진다. template None = 해석 규칙 없음."""
    if not template:
        return {"root": None, "abs_root": None, "problem": "no path template (storage backend has no preset)"}
    try:
        rel = render(template, **values)
    except PvPathUnresolved as e:
        return {"root": None, "abs_root": None, "problem": str(e)}
    abs_root = managed_abs_path(managed_root, rel) if isinstance(managed_root, str) and managed_root else None
    return {"root": rel, "abs_root": abs_root, "problem": None}
