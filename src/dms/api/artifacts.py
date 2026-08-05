"""아티팩트 경로 검증·조립·읽기. 경로 탈출은 '정규화 후 검사'가 아니라 '구성으로 불가능하게'
만든다 — 조각을 각각 화이트리스트로 검증하고 그것만으로 경로를 조립한 뒤, 심링크 대비로
realpath 봉쇄를 추가로 건다 (상위 스펙 §5)."""
import os
import re

PHASES = ("preflight", "preview", "execution")
NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")
MAX_BYTES = 256 * 1024
MAX_TAIL_LINES = 5000


class ArtifactError(Exception):
    def __init__(self, reason_code: str, detail: str = ""):
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(reason_code)


def strip_scheme(base_uri: str) -> str:
    return base_uri[len("file://"):] if base_uri.startswith("file://") else base_uri


def artifact_dir(base: str, job_id: str) -> str:
    if not JOB_ID_RE.match(job_id or ""):
        raise ArtifactError("invalid_job_id", job_id or "")
    return os.path.join(base, job_id)


def resolve_artifact_path(base: str, job_id: str, phase: str, name: str) -> str:
    root = artifact_dir(base, job_id)
    if phase not in PHASES:
        raise ArtifactError("invalid_phase", phase or "")
    # NAME_RE의 문자 집합은 '.'을 포함하므로 "."·".."처럼 점으로만 이루어진
    # 이름도 정규식은 통과한다 — 이런 이름은 디렉터리 자기참조/상위참조로 해석되어
    # "구성으로 불가능하게" 원칙을 깨므로 별도로 막는다.
    if not NAME_RE.match(name or "") or set(name) <= {"."}:
        raise ArtifactError("invalid_artifact_name", name or "")
    return os.path.join(root, phase, name)


def _assert_contained(base: str, job_id: str, path: str) -> None:
    root = os.path.realpath(artifact_dir(base, job_id))
    real = os.path.realpath(path)
    if real != root and not real.startswith(root + os.sep):
        raise ArtifactError("artifact_forbidden", name_of(path))


def name_of(path: str) -> str:
    return os.path.basename(path)


def list_artifacts(base: str, job_id: str) -> list[dict]:
    root = artifact_dir(base, job_id)
    out = []
    for phase in PHASES:
        d = os.path.join(root, phase)
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for name in names:
            if not NAME_RE.match(name):
                continue
            p = os.path.join(d, name)
            try:
                st = os.stat(p)
            except OSError:
                continue
            if not os.path.isfile(p):
                continue
            out.append({"phase": phase, "name": name, "size": st.st_size,
                        "modified_at": int(st.st_mtime)})
    return out


def read_artifact(base: str, job_id: str, phase: str, name: str,
                  tail: int | None = None) -> dict:
    path = resolve_artifact_path(base, job_id, phase, name)
    if not os.path.isfile(path):
        raise ArtifactError("artifact_not_found", name)
    _assert_contained(base, job_id, path)
    size = os.path.getsize(path)
    truncated = False
    with open(path, "rb") as f:
        if size > MAX_BYTES:
            f.seek(size - MAX_BYTES)
            truncated = True
        raw = f.read()
    text = raw.decode("utf-8", errors="replace")
    if tail is not None:
        lines = text.splitlines()
        capped = min(max(tail, 1), MAX_TAIL_LINES)
        if len(lines) > capped:
            truncated = True
            lines = lines[-capped:]
        text = "\n".join(lines)
    return {"phase": phase, "name": name, "size": size,
            "truncated": truncated, "content": text}
