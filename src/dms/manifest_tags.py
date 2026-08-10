"""deploy/k8s 매니페스트에서 이미지 태그를 읽는 부분집합 YAML 파서(슬라이스 16 설계 §2.1).

tests/test_release_manifest_contract.py 에 있던 파서를 승격했다. PyYAML 은 이 저장소의
의존성이 아니다(런타임도 테스트도) -- 파서 하나를 들이는 대신 매니페스트가 실제로 쓰는
부분집합(블록 매핑/시퀀스, 주석, 다중 문서)만 읽는다. 앵커/블록 스칼라/복합 키는 이
파일들에 없다.

동봉본의 의미: Dockerfile.dms 가 deploy/k8s 를 이미지에 COPY 하므로 여기서 읽는 값은
"이 이미지를 만든 소스 트리의 매니페스트"다. 포탈 롤아웃은 매니페스트를 고치지 않으므로
롤아웃 직후에는 반드시 live != manifest 가 되어 정확히 그 위험(다음 kubectl apply 가
되돌림)을 표시한다(설계 §2.1).

두 계층의 오류 정책이 공존한다:
- 런타임 조회(manifest_images/manifest_job_image)는 전면 fail-soft(설계 §4) -- 못 찾으면
  해당 값만 None, 예외를 올리지 않는다(추측하지 않는다).
- 계약 테스트용 헬퍼(match_labels/container_names)는 assert 기반 -- 추출이 빗나가면
  조용히 통과하는 대신 테스트가 빨간불이 되어야 한다."""
from pathlib import Path

from .repositories.releases import COMPONENTS

# 롤아웃 대상 3종의 매니페스트 파일. kind/workload/container 좌표는 COMPONENTS 가
# 단일 진실이다 -- 여기 중복 정의하면 계약 테스트가 지키는 표와 어긋날 수 있다.
MANIFEST_FILES = {
    "dms-api": "40-api.yaml",
    "dms-controller": "41-controller.yaml",
    "dms-agent": "50-agent-daemonset.yaml",
}
# dms-migrate 는 COMPONENTS(롤아웃 대상)가 아니지만 같은 dms 이미지 계보의 네 번째
# image: 라인이다 -- one-shot Job 을 유지하는 한(설계 §2.2) 같은 드리프트 표면이라
# 함께 읽는다.
_MIGRATE = ("30-migrate-job.yaml", "Job", "dms-migrate", "migrate")
_CONFIGMAP = ("20-config.yaml", "ConfigMap", "dms-config")

# 동봉본 위치 후보. 이미지 안의 dms 는 pip install(비-editable)로 site-packages 에
# 들어가 __file__ 기준 상대경로가 저장소를 못 가리킨다 -- Dockerfile 이 COPY 하는
# /app/deploy/k8s 를 둘째 후보로 둔다. 개발 체크아웃(.venv editable)에서는 __file__ 이
# src/dms/ 아래라 parents[2] 가 저장소 루트다. 존재하는 첫 후보를 쓰고 없으면
# None(전량 None 반환) -- fail-soft(설계 §4).
_ROOT_CANDIDATES = (
    Path(__file__).resolve().parents[2] / "deploy" / "k8s",
    Path("/app/deploy/k8s"),
)

# 조회 계층이 삼키는 예외. OSError 만으로는 부족하다: read_text 는 비-UTF-8 바이트에
# UnicodeDecodeError 를 내는데 그건 OSError 가 아니라 ValueError 의 서브클래스라
# 그대로 라우트까지 전파된다(파일 하나가 깨졌을 뿐인데 배지가 아니라 응답이 죽는다).
# errors="replace" 로 뭉개지 않고 예외를 넓히는 쪽을 택한다 -- 깨진 문자로 계속 파싱해
# 엉뚱한 태그를 뽑느니 None 이 낫다(설계 §4: 추측하지 않는다). ValueError 는 파싱 중
# 나올 수 있는 다른 사고(빈 시퀀스 min() 등)도 함께 덮는다.
_READ_ERRORS = (OSError, ValueError)


def _strip_comment(line: str) -> str:
    """따옴표 밖의 ' #' 이후를 버린다. 값 안의 '#'(없지만)을 지우지 않기 위해서다."""
    out, quote = [], None
    for i, ch in enumerate(line):
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            out.append(ch)
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            break
        else:
            out.append(ch)
    return "".join(out).rstrip()


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def documents(path: Path) -> "list[list[str]]":
    docs, cur = [], []
    for raw in path.read_text().splitlines():
        line = _strip_comment(raw)
        if not line.strip():
            continue
        if line.strip() == "---":
            docs.append(cur)
            cur = []
        else:
            cur.append(line)
    docs.append(cur)
    return [d for d in docs if d]


def _block(lines: "list[str]", start: int) -> "list[str]":
    """lines[start] 보다 깊게 들여쓴 연속 구간(그 키의 몸통)."""
    base = _indent(lines[start])
    body = []
    for line in lines[start + 1:]:
        if _indent(line) <= base:
            break
        body.append(line)
    return body


def _find(lines: "list[str]", key: str, indent: "int | None" = None) -> "int | None":
    for idx, line in enumerate(lines):
        if line.strip().startswith(f"{key}:") and (indent is None
                                                   or _indent(line) == indent):
            return idx
    return None


def _value(line: str) -> str:
    return line.split(":", 1)[1].strip()


def _unquote(value: str) -> str:
    # 20-config.yaml 의 DMS_JOB_IMAGE 는 따옴표로 싸여 있다 -- 벗기지 않으면 라이브
    # env 값과의 비교가 항상 불일치로 나온다.
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def workload_doc(path: Path, kind: str, name: str) -> "list[str] | None":
    for doc in documents(path):
        kind_at = _find(doc, "kind", indent=0)
        if kind_at is None or _value(doc[kind_at]) != kind:
            continue
        meta_at = _find(doc, "metadata", indent=0)
        if meta_at is None:
            continue
        meta = _block(doc, meta_at)
        name_at = _find(meta, "name")
        if name_at is not None and _value(meta[name_at]) == name:
            return doc
    return None


def match_labels(doc: "list[str]") -> dict:
    """계약 테스트 전용 -- assert 로 시끄럽게 실패한다(모듈 docstring 참고)."""
    spec_at = _find(doc, "spec", indent=0)
    assert spec_at is not None, "워크로드 문서에 최상위 spec 이 없다"
    spec = _block(doc, spec_at)
    sel_at = _find(spec, "selector", indent=_indent(spec[0]))
    assert sel_at is not None, "spec.selector 를 못 찾았다"
    ml_at = _find(_block(spec, sel_at), "matchLabels")
    assert ml_at is not None, "spec.selector.matchLabels 를 못 찾았다"
    labels = _block(_block(spec, sel_at), ml_at)
    return {line.split(":", 1)[0].strip(): _value(line) for line in labels}


def container_names(doc: "list[str]") -> "list[str]":
    # initContainers 는 'containers:' 로 시작하지 않으므로 걸리지 않는다. volumes 는
    # containers 와 같은 깊이라 _block 이 거기서 멈춘다.
    at = _find(doc, "containers")
    assert at is not None, "pod template 의 containers 를 못 찾았다"
    body = _block(doc, at)
    assert body, "containers 가 비어 있다"
    item_indent = min(_indent(line) for line in body)
    return [_value(line.strip()[2:]) for line in body
            if _indent(line) == item_indent and line.strip().startswith("- name:")]


def init_container_names(doc: "list[str]") -> "list[str]":
    """initContainers 블록의 컨테이너 이름들. 블록이 없으면 빈 리스트.

    container_names 와 대칭이지만 '없음'을 assert 로 막지 않는다 -- initContainers 가
    없는 워크로드(dms-agent DaemonSet)가 정상이고, 그 **부재 자체가 계약의 한쪽
    방향**이기 때문이다(test_release_manifest_contract 가 양방향으로 건다).

    container_names 와 합치지 말 것. 저쪽은 'containers:' 만 집어 **본 컨테이너만**
    돌려줘야 한다 -- 드리프트 배지가 그 반환값으로 라이브 이미지와 비교하므로
    initContainer 가 새어 들어가면 엉뚱한 이미지를 비교하게 된다(설계 §2.1)."""
    at = _find(doc, "initContainers")
    if at is None:
        return []
    body = _block(doc, at)
    if not body:
        return []
    item_indent = min(_indent(line) for line in body)
    return [_value(line.strip()[2:]) for line in body
            if _indent(line) == item_indent and line.strip().startswith("- name:")]


def container_image(doc: "list[str]", container: str) -> "str | None":
    """containers 블록에서 이름이 container 인 항목의 image. 런타임 경로라 fail-soft.

    initContainers 는 'containers:' 프리픽스가 달라 _find 에 안 걸리고(Task 4 가
    40/41 에 initContainers 를 넣어도 본 컨테이너 이미지를 정확히 집는다), env 의
    `- name:` 항목들은 item_indent 보다 깊어 이름 추적을 오염시키지 않는다."""
    at = _find(doc, "containers")
    if at is None:
        return None
    body = _block(doc, at)
    if not body:
        return None
    item_indent = min(_indent(line) for line in body)
    current = None
    for line in body:
        stripped = line.strip()
        if _indent(line) == item_indent and stripped.startswith("- name:"):
            current = _value(stripped[2:])
        elif current == container and stripped.startswith("image:"):
            return _unquote(_value(line)) or None
    return None


def _root(root=None) -> "Path | None":
    if root is not None:
        root = Path(root)
        return root if root.is_dir() else None
    for cand in _ROOT_CANDIDATES:
        if cand.is_dir():
            return cand
    return None


def _image_from(path, kind, name, container) -> "str | None":
    try:
        doc = workload_doc(path, kind, name)
        if doc is None:
            return None
        return container_image(doc, container)
    except _READ_ERRORS:
        return None                # 그 항목만 None(설계 §4)


def manifest_images(root=None) -> "dict[str, str | None]":
    out = {c: None for c in (*MANIFEST_FILES, "dms-migrate")}
    base = _root(root)
    if base is None:
        return out
    for component, filename in MANIFEST_FILES.items():
        spec = COMPONENTS[component]
        out[component] = _image_from(base / filename, spec["kind"],
                                     spec["workload"], spec["container"])
    filename, kind, name, container = _MIGRATE
    out["dms-migrate"] = _image_from(base / filename, kind, name, container)
    return out


def manifest_job_image(root=None) -> "str | None":
    base = _root(root)
    if base is None:
        return None
    filename, kind, name = _CONFIGMAP
    try:
        doc = workload_doc(base / filename, kind, name)
        if doc is None:
            return None
        data_at = _find(doc, "data", indent=0)
        if data_at is None:
            return None
        data = _block(doc, data_at)
        key_at = _find(data, "DMS_JOB_IMAGE")
        if key_at is None:
            return None
        return _unquote(_value(data[key_at])) or None
    except _READ_ERRORS:
        return None
