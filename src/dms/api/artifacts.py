"""아티팩트 경로 검증·조립·읽기. 경로 탈출은 '정규화 후 검사'가 아니라 '구성으로 불가능하게'
만든다 — 조각을 각각 화이트리스트로 검증하고 그것만으로 경로를 조립한 뒤, 심링크 대비로
realpath 봉쇄를 추가로 건다 (상위 스펙 §5).

위협 모델: job runner는 `chown -R <uid>:<gid> <artifact_base>/<job_id>/<phase>`로 요청자에게
이 디렉터리의 소유권을 넘긴다(dms_job_runner/runner.py). 즉 **인증된 일반 사용자가 자기
소유 잡의 아티팩트 디렉터리 안에 파일을 만들고·바꿔치기하고·심링크를 걸 수 있다.** 소유권
검사(_owned_job)로는 막을 수 없다(공격자는 자기 잡을 쓴다). 그래서 이 모듈은:
  - 경로 문자열을 두 번 해석하지 않는다. 딱 한 번 열고(O_NOFOLLOW), 이후 검사는 전부
    **열린 fd**에 대해서 한다(fstat + /proc/self/fd realpath). 검사와 사용 사이에 대상이
    바뀌는 TOCTOU를 구조적으로 없앤다.
  - 목록은 lstat만 쓴다(심링크를 따라가지 않는다).
  - 읽기·목록 모두 상한(MAX_BYTES / MAX_ENTRIES)을 강제한다.
  - 탈출 시도와 단순 미존재를 호출자에게 구별시키지 않는다(존재 오라클 차단).
"""
import os
import re
import stat

# stepper가 실제로 쓰는 phase 전부. "exec_preflight"는 confirm 후 execution 직전의
# 재검증(stepper._poll_or_submit_execution, execution_volcano._PREFLIGHT_PHASES)이다 —
# 실패하면 잡이 execution_recheck_failed로 거절되고 phase_refs에 pod/<name>이 남는데,
# 여기 빠져 있으면 그 로그·아티팩트가 422로 막혀 운영자가 진단할 방법이 없어진다.
PHASES = ("preflight", "preview", "exec_preflight", "execution")
# fullmatch로만 쓴다: re의 '$'는 문자열 끝의 개행 *앞*에서도 매칭되므로 "..\n" 같은 이름이
# 앵커를 통과해 버린다(그리고 set("..\n")은 {"."}의 부분집합이 아니라 점-전용 가드도 피한다).
NAME_RE = re.compile(r"[A-Za-z0-9._-]+")
JOB_ID_RE = re.compile(r"[0-9a-f]{32}")
MAX_BYTES = 256 * 1024
MAX_TAIL_LINES = 5000
# 사용자가 phase 디렉터리 소유자라 파일을 무한정 만들 수 있다 — 응답(과 stat 횟수)을 묶는다.
MAX_ENTRIES = 1000
# 응답 항목 수 상한만으로는 부족하다: 정규 파일이 하나도 없는 디렉터리(하위 디렉터리·
# 심링크만 수십만 개)는 MAX_ENTRIES를 영원히 채우지 못해 예산이 발동하지 않고, 스캔은
# 사용자가 만든 dirent 수만큼 늘어난다. 타입과 무관하게 "검사한 dirent" 자체를 묶는다.
MAX_SCAN = 10 * MAX_ENTRIES


class ArtifactError(Exception):
    def __init__(self, reason_code: str, detail: str = ""):
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(reason_code)


def strip_scheme(base_uri: str) -> str:
    return base_uri[len("file://"):] if base_uri.startswith("file://") else base_uri


def artifact_dir(base: str, job_id: str) -> str:
    if not JOB_ID_RE.fullmatch(job_id or ""):
        raise ArtifactError("invalid_job_id", job_id or "")
    return os.path.join(base, job_id)


def resolve_artifact_path(base: str, job_id: str, phase: str, name: str) -> str:
    root = artifact_dir(base, job_id)
    if phase not in PHASES:
        raise ArtifactError("invalid_phase", phase or "")
    # NAME_RE의 문자 집합은 '.'을 포함하므로 "."·".."처럼 점으로만 이루어진
    # 이름도 정규식은 통과한다 — 이런 이름은 디렉터리 자기참조/상위참조로 해석되어
    # "구성으로 불가능하게" 원칙을 깨므로 별도로 막는다.
    if not NAME_RE.fullmatch(name or "") or set(name) <= {"."}:
        raise ArtifactError("invalid_artifact_name", name or "")
    return os.path.join(root, phase, name)


def _assert_contained(base: str, job_id: str, real: str) -> None:
    """이미 해석된(realpath) 경로가 잡 디렉터리 안인지 확인한다.

    한계(정직하게 기록): **하드 링크는 이 검사로 막을 수 없다.** 하드 링크는 해석할
    심링크가 없어 realpath가 그대로 base 안의 경로를 돌려주지만 inode는 바깥 파일이다.
    완화 요인은 같은 파일시스템(디바이스) 안에서만 만들 수 있다는 점과 리눅스의
    fs.protected_hardlinks(기본 1)가 자기 소유·쓰기 가능한 파일에만 링크를 허용한다는
    점뿐이다. 애플리케이션 층에서 고칠 수 없고, 배포 측에서 아티팩트 마운트를 별도
    파일시스템으로 두고 protected_hardlinks를 켜서 다뤄야 한다.
    """
    root = os.path.realpath(artifact_dir(base, job_id))
    if real != root and not real.startswith(root + os.sep):
        raise ArtifactError("artifact_forbidden", name_of(real))


def name_of(path: str) -> str:
    return os.path.basename(path)


def list_artifacts(base: str, job_id: str) -> dict:
    """{"entries": [...], "truncated": bool}. 심링크는 항목이든 phase 디렉터리든 건너뛴다.

    read_artifact와 같은 원칙을 목록에도 적용한다 — **경로 문자열을 두 번 해석하지 않는다.**
    예전 구현은 os.lstat(d)로 "디렉터리다"를 확인한 뒤 os.listdir(d)로 같은 경로를 다시
    해석했다. 두 해석 사이에 <phase>를 심링크로 바꿔치기하면 listdir가 링크를 따라가고,
    뒤이은 os.lstat(d/name)도 바뀐 중간 컴포넌트를 통해 해석돼 임의 디렉터리의 이름·크기·
    mtime이 새어 나갔다(리뷰어 재현). 이제 phase 디렉터리를 fd로 한 번만 열어 고정하고
    이후 스캔·stat은 전부 그 fd 기준(scandir(dfd) + fstatat)이라 바꿔치기가 통하지 않는다.

    작업량도 두 축으로 묶는다: 수락한 항목은 MAX_ENTRIES, **검사한 dirent**는 MAX_SCAN.
    디렉터리 소유자가 사용자이므로 둘 중 하나라도 없으면 요청당 메모리·시스템 콜을
    사용자가 좌우한다(팟 메모리 한계까지).
    """
    root = artifact_dir(base, job_id)
    entries: list[dict] = []
    truncated = False
    for phase in PHASES:
        if truncated:
            break
        try:
            # O_NOFOLLOW로 "phase 디렉터리가 심링크면 열지도 않는다"를 커널에 맡긴다
            # (예전 lstat(d) 검사를 대체한다). O_DIRECTORY는 디렉터리가 아니면 ENOTDIR.
            dfd = os.open(os.path.join(root, phase),
                          os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError:
            continue
        try:
            # O_NOFOLLOW는 마지막 컴포넌트만 본다 — 상위(<job_id>)가 바꿔치기된 경우는
            # 지금 손에 쥔 fd가 실제로 어디인지 물어서(/proc/self/fd) 봉쇄한다.
            _assert_contained(base, job_id, os.path.realpath(f"/proc/self/fd/{dfd}"))
            scanned = 0
            with os.scandir(dfd) as it:
                for entry in it:
                    scanned += 1
                    if scanned > MAX_SCAN or len(entries) >= MAX_ENTRIES:
                        truncated = True
                        break
                    if not NAME_RE.fullmatch(entry.name):
                        continue
                    try:
                        # follow_symlinks=False → fstatat(dfd, name, AT_SYMLINK_NOFOLLOW).
                        # 심링크는 S_ISLNK라 아래 검사에서 탈락한다(os.stat/isfile은 둘 다
                        # 따라가서 바깥 파일의 크기·mtime을 흘린다). 보고하는 size·mtime이
                        # 판정에 쓴 stat과 같은 호출에서 나오도록 한 번만 부른다.
                        st = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if not stat.S_ISREG(st.st_mode):
                        continue
                    entries.append({"phase": phase, "name": entry.name, "size": st.st_size,
                                    "modified_at": int(st.st_mtime)})
        except (OSError, ArtifactError):
            continue
        finally:
            os.close(dfd)
    # dirent 순서는 파일시스템 마음대로다 — 수집한 (상한이 걸린) 조각만 정렬해
    # 응답 순서를 결정론적으로 만든다. phase 순서는 PHASES를 따른다.
    entries.sort(key=lambda r: (PHASES.index(r["phase"]), r["name"]))
    return {"entries": entries, "truncated": truncated}


def _read_capped(fd: int, limit: int) -> bytes:
    chunks: list[bytes] = []
    remaining = limit
    while remaining > 0:
        chunk = os.read(fd, remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_artifact(base: str, job_id: str, phase: str, name: str,
                  tail: int | None = None) -> dict:
    path = resolve_artifact_path(base, job_id, phase, name)
    truncated = False
    try:
        # 딱 한 번만 연다. O_NOFOLLOW는 마지막 컴포넌트가 심링크면 ELOOP로 실패시킨다.
        # 이후 stat/봉쇄 검사는 경로 문자열이 아니라 이 fd에 대해서만 한다 — 검사한
        # 대상과 읽는 대상이 같은 inode임이 보장된다(TOCTOU 제거).
        # O_NONBLOCK이 **필수**다: 사용자가 자기 phase 디렉터리 소유자라 아티팩트 이름으로
        # mkfifo를 걸 수 있는데, 이 플래그가 없으면 os.open이 writer를 기다리며 영원히
        # 블록한다(아래 S_ISREG 검사는 실행되지도 않는다). 스타레트는 이 동기 라우트를
        # AnyIO 스레드풀(~40)에서 돌리므로 그런 요청 40번이면 dms-api가 SPA까지 포함해
        # 전부 멈추고, 클라이언트가 끊어도 스레드는 돌아오지 않는다(팟 재시작 전까지).
        # 리눅스에서 정규 파일에는 아무 영향이 없고, FIFO는 즉시 돌아와 S_ISREG에서 걸린다.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        # ELOOP/ENOENT/EACCES/ENAMETOOLONG/EISDIR… 전부 같은 응답으로 뭉갠다 —
        # errno나 경로가 새어 나가면 그 자체가 존재 오라클이 된다.
        raise ArtifactError("artifact_not_found", name)
    try:
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise ArtifactError("artifact_not_found", name)
            # /proc/self/fd/<fd>는 열린 inode의 실제 경로다. 경로를 다시 해석하는 게
            # 아니라 '지금 손에 쥔 fd가 어디인지'를 묻는 것이라 바꿔치기에 영향받지 않는다.
            # (심링크된 phase 디렉터리처럼 마지막 컴포넌트가 아닌 탈출을 여기서 잡는다.)
            _assert_contained(base, job_id, os.path.realpath(f"/proc/self/fd/{fd}"))
            size = st.st_size
            if size > MAX_BYTES:
                os.lseek(fd, size - MAX_BYTES, os.SEEK_SET)
                truncated = True
            raw = _read_capped(fd, MAX_BYTES)
        except OSError:
            raise ArtifactError("artifact_not_found", name)
    finally:
        os.close(fd)
    text = raw.decode("utf-8", errors="replace")
    if tail is not None:
        # str.splitlines()는 '\r'·'\v'·'\f'·'\x85'에서도 쪼갠다 — rsync/dsync류 진행률
        # 출력(한 줄을 '\r'로 덮어쓰는)이 조각조각 나서 tail=N이 N줄이 아니게 된다.
        lines = text.split("\n")
        if lines and lines[-1] == "":
            lines.pop()  # 파일 끝 개행은 마지막 줄의 종결자지 빈 줄이 아니다
        capped = min(max(tail, 1), MAX_TAIL_LINES)
        if len(lines) > capped:
            truncated = True
            lines = lines[-capped:]
        text = "\n".join(lines)
    return {"phase": phase, "name": name, "size": st.st_size,
            "truncated": truncated, "content": text}
