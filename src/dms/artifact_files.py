"""아티팩트 파일 열기의 **단일 봉쇄 사슬** -- API 와 컨트롤러가 같은 함수로 연다
(2026-09-09, 제어면 root 전환).

배경: dms-api/dms-controller 는 uid 0 으로 돈다(deploy/k8s/40-api.yaml ·
41-controller.yaml 의 컨테이너 securityContext). 운영 아티팩트 base 가 root:root 라
65532 로는 쓰기 왕복 검증이 항상 실패했기 때문이다. capabilities 는 전부 버리므로
(drop ALL) CAP_DAC_OVERRIDE/DAC_READ_SEARCH 는 없고 root 도 **소유자 mode 비트**의
지배를 받지만, 러너가 root 로 쓰는 파일(0644)과 요청자 디렉터리(0755)는 그 비트로
읽히므로 파일시스템 권한은 더 이상 2차 방어가 아니다 -- 인가는 DB(_owned_job /
require_admin)와 이 모듈의 사슬뿐이다. 이 모듈이 api/ 밖(중립)에 있는 이유는
strip_scheme 과 같다: 실행 계열·wiring 이 FastAPI 계층을 임포트하지 않고 같은
사슬을 쓰기 위해서다. 컨트롤러가 실제로 이 사슬로 배선돼 있는지는
tests/test_wiring_phase3c.py 의 고정 테스트가 지킨다(배선은 wiring.py 한 줄이다).

위협 모델(api/artifacts.py 모듈 docstring 과 같다): job runner 가 <base>/<job_id>/<phase>
를 요청자에게 chown 하므로(dms_job_runner/runner.py) **인증된 일반 사용자가 그 안에
파일을 만들고·바꿔치기하고·심링크/하드링크/rename 으로 남의 파일을 들여올 수 있다.**

사슬(순서가 계약이다):
  단일 open(O_RDONLY|O_NOFOLLOW|O_NONBLOCK) → fstat S_ISREG → nlink == 1
  → 소유자(st_uid ∈ {0, 요청자 uid}) → fd 봉쇄(/proc/self/fd realpath 가
  <base>/<job_id>/ 아래) → (호출자) 크기 상한.

- O_NOFOLLOW: 마지막 컴포넌트가 심링크면 ELOOP. 중간 컴포넌트(phase 디렉터리
  심링크)는 fd 봉쇄가 잡는다 -- 경로 문자열을 두 번 해석하지 않고 "지금 손에 쥔
  fd 가 어디인지"를 커널에 묻는다(TOCTOU 제거).
- O_NONBLOCK: 요청자가 mkfifo 를 걸면 open 이 writer 를 기다리며 영원히 블록한다 --
  API 는 스레드풀 고갈, 컨트롤러는 단일 스레드라 **모든 루프 정지**(replicas 1,
  liveness 없음). FIFO 는 즉시 돌아와 S_ISREG 에서 탈락한다.
- nlink: 하드링크는 realpath 봉쇄로 못 잡는다(inode 가 바깥 것). 러너·도구는
  하드링크를 만들지 않으므로 nlink>1 거부는 오탐 없는 심층 방어다. 근본 방어는
  fs.protected_hardlinks=1 이 /cephfs 를 마운트한 **모든** 호스트에 켜져 있는 것
  (링커 커널의 may_linkat 에서 검사 -- 배포 전제, deploy/README).
- 소유자: 65532 시절 EACCES 가 조용히 해 주던 세 번째 장벽의 대체. rename(2) 으로
  들여온 남의 정규 파일(nlink=1, 봉쇄 통과)은 소유자가 다르다. 0 을 함께 허용하는
  이유는 러너가 chown **뒤에** root 로 stdout.log/stderr.log/summary.json 을 새로
  쓰기 때문 -- 러너가 그 셋을 요청자로 chown 하면 정확히 == 요청자로 좁힐 수 있다
  (docs/BACKLOG.md). st_uid == 0 만 허용하는 게이트는 쓰지 않는다: 요청자가 미리
  만들어 둔 정규 파일을 root 의 open("w") 가 재사용하면 요청자 소유가 된다(정상).
- 실패는 전부 같은 ArtifactError("artifact_not_found") -- errno·경로가 새면 그
  자체가 존재 오라클이다.
"""
import os
import re
import stat

# stepper 가 실제로 쓰는 phase 전부. "exec_preflight" 는 confirm 후 execution 직전의
# 재검증(stepper._poll_or_submit_execution, execution_volcano._PREFLIGHT_PHASES)이다 --
# 실패하면 잡이 execution_recheck_failed 로 거절되고 phase_refs 에 pod/<name> 이 남는데,
# 여기 빠져 있으면 그 로그·아티팩트가 422 로 막혀 운영자가 진단할 방법이 없어진다.
PHASES = ("preflight", "preview", "exec_preflight", "execution")
# fullmatch 로만 쓴다: re 의 '$' 는 문자열 끝의 개행 *앞* 에서도 매칭되므로 "..\n" 같은
# 이름이 앵커를 통과해 버린다(그리고 set("..\n") 은 {"."} 의 부분집합이 아니라 점-전용
# 가드도 피한다).
NAME_RE = re.compile(r"[A-Za-z0-9._-]+")
JOB_ID_RE = re.compile(r"[0-9a-f]{32}")
# 컨트롤러 summary.json 읽기 상한. 정상 summary 는 3키 수십 바이트(runner._build_summary)
# 라 1MiB 는 넉넉하고, 요청자가 다GB 파일을 summary.json 으로 심어 컨트롤러를 OOM
# 크래시 루프에 넣는 것을 끊는다.
SUMMARY_MAX_BYTES = 1024 * 1024


class ArtifactError(Exception):
    def __init__(self, reason_code: str, detail: str = ""):
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(reason_code)


def artifact_dir(base: str, job_id: str) -> str:
    if not JOB_ID_RE.fullmatch(job_id or ""):
        raise ArtifactError("invalid_job_id", job_id or "")
    return os.path.join(base, job_id)


def resolve_artifact_path(base: str, job_id: str, phase: str, name: str) -> str:
    root = artifact_dir(base, job_id)
    if phase not in PHASES:
        raise ArtifactError("invalid_phase", phase or "")
    # NAME_RE 의 문자 집합은 '.' 을 포함하므로 "."·".." 처럼 점으로만 이루어진
    # 이름도 정규식은 통과한다 -- 이런 이름은 디렉터리 자기참조/상위참조로 해석되어
    # "구성으로 불가능하게" 원칙을 깨므로 별도로 막는다.
    if not NAME_RE.fullmatch(name or "") or set(name) <= {"."}:
        raise ArtifactError("invalid_artifact_name", name or "")
    return os.path.join(root, phase, name)


def name_of(path: str) -> str:
    return os.path.basename(path)


def assert_contained(base: str, job_id: str, real: str) -> None:
    """이미 해석된(realpath) 경로가 잡 디렉터리 안인지 확인한다.

    기준은 realpath(<base>/<job_id>) 다 -- base 자체가 심링크(예: /data → /cephfs/x)
    인 배포를 허용하기 위해서다. 그 대신 **<base> 와 <base>/<job_id> 는 요청자가
    쓸 수 없어야 한다**(root:root, 비-world-writable; 러너는 <job_id> 를 root 로
    만들고 <phase> 만 chown 한다). 그 전제가 깨지면 요청자가 <job_id> 를 심링크로
    바꿔치기해 기준 자체를 옮길 수 있다(docs/ARCHITECTURE.md 불변식).

    하드 링크는 이 검사로 막을 수 없다(inode 가 바깥 것인데 realpath 는 base 안) --
    그건 inode_allowed 의 nlink 검사와 배포 전제(fs.protected_hardlinks)가 맡는다.
    """
    root = os.path.realpath(artifact_dir(base, job_id))
    if real != root and not real.startswith(root + os.sep):
        raise ArtifactError("artifact_forbidden", name_of(real))


def inode_allowed(st: os.stat_result, owner_uid: "int | None") -> bool:
    """fstat/fstatat 결과 하나로 "서빙해도 되는 inode 인가"를 답한다. 목록(list)과
    열기(open) 가 **같은 판정**을 써야 목록에 뜬 파일이 404 가 되거나 그 반대가
    되지 않는다. owner_uid None 은 소유자 검사 생략(요청자 개념이 없는 호출자
    전용 -- 라우트는 항상 int 를 넘긴다)."""
    if not stat.S_ISREG(st.st_mode):
        return False
    if st.st_nlink != 1:
        return False
    if owner_uid is not None and st.st_uid not in (0, owner_uid):
        return False
    return True


def job_owner_uid(job: "dict | None") -> "int | None":
    """잡 행의 요청자 실행 uid(planner 가 worker_pool.identity 에 저장). 정수가
    아니면 None -- bool 은 int 의 서브클래스라 명시적으로 거른다. None 인 잡은
    stepper 의 identity_missing_at_step 가드에 걸려 아티팩트를 만들 수 없으므로
    라우트는 None 을 "없음(404)" 으로 접는다(fail-closed)."""
    identity = ((job or {}).get("worker_pool") or {}).get("identity") or {}
    uid = identity.get("uid") if isinstance(identity, dict) else None
    if isinstance(uid, bool) or not isinstance(uid, int):
        return None
    return uid


def open_artifact_fd(base: str, job_id: str, phase: str, name: str, *,
                     owner_uid: "int | None") -> "tuple[int, os.stat_result]":
    """봉쇄 사슬을 통과한 (fd, fstat 결과)를 돌려준다. 반환 전 어떤 실패든 열린
    fd 는 여기서 닫는다 -- 성공 반환 뒤의 fd 소유권은 호출자가 진다.

    owner_uid 는 **기본값 없는 키워드 인자**다: 생략이 곧 소유자 검사 생략이라면
    새 라우트가 인자 하나를 빠뜨리는 것만으로 rename 벡터가 열리고 아무 테스트도
    빨간불이 아니다(CLAUDE.md 의 password 통로와 같은 모양). None 을 넘기려면
    명시적으로 써야 하고, 그것은 리뷰에서 보인다."""
    path = resolve_artifact_path(base, job_id, phase, name)
    try:
        # 딱 한 번만 연다. 이후 검사는 경로 문자열이 아니라 이 fd 에 대해서만 한다 --
        # 검사한 대상과 읽는 대상이 같은 inode 임이 보장된다(모듈 docstring).
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        # ELOOP/ENOENT/EACCES/ENAMETOOLONG/EISDIR… 전부 같은 응답으로 뭉갠다.
        raise ArtifactError("artifact_not_found", name)
    try:
        try:
            st = os.fstat(fd)
            if not inode_allowed(st, owner_uid):
                raise ArtifactError("artifact_not_found", name)
            # /proc/self/fd/<fd> 는 열린 inode 의 실제 경로다. 경로를 다시 해석하는
            # 게 아니라 '지금 손에 쥔 fd 가 어디인지'를 묻는 것이라 바꿔치기에
            # 영향받지 않는다(심링크된 phase 디렉터리 같은 중간 탈출을 여기서 잡는다).
            assert_contained(base, job_id, os.path.realpath(f"/proc/self/fd/{fd}"))
        except OSError:
            raise ArtifactError("artifact_not_found", name)
        return fd, st
    except BaseException:
        os.close(fd)
        raise


def read_capped(fd: int, limit: int) -> bytes:
    chunks: list[bytes] = []
    remaining = limit
    while remaining > 0:
        chunk = os.read(fd, remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_contained_text(base: str, job_id: str, phase: str, name: str, *,
                        max_bytes: int, owner_uid: "int | None") -> "str | None":
    """컨트롤러용 읽기(wiring.build_execution_adapter 의 read_text). 사슬 위반·
    크기 초과·OSError·**비-UTF-8(ValueError)** 전부 None(모름) -- 예외를 올리면
    stepper 가 매 틱 step_error 로 같은 잡에 멈춘다(예전 wiring 은 OSError 만
    잡아 비-UTF-8 summary.json 이 정확히 그 정지를 만들었다). None 은 stepper 가
    summary_unavailable 로 렌더한다."""
    try:
        fd, st = open_artifact_fd(base, job_id, phase, name, owner_uid=owner_uid)
    except ArtifactError:
        return None
    try:
        if st.st_size > max_bytes:
            return None
        # fstat 뒤 파일이 자라도 상한까지만 읽는다 -- 잘린 JSON 은 호출자의
        # json.loads 가 None 으로 접는다(execution_volcano.read_summary).
        return read_capped(fd, max_bytes).decode("utf-8")
    except (OSError, ValueError):
        return None
    finally:
        os.close(fd)
