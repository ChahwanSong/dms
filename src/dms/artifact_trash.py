"""요청 삭제 정리(request-purge)의 파일 단계 -- 아티팩트 `<base>/<job_id>` 를 `<base>/.dms-trash/<job_id>` 로 떼어낸다.

**컨트롤러 전용**(request_purger 만 import -- api 는 import 하지 않는다, tests/test_control_plane_static.py 가 고정).
제어면이 파일시스템에서 바꾸는 것은 이 모듈의 두 가지뿐이다(ARCHITECTURE §7 root 제어면 규칙 13):
  - `.dms-trash` 생성(mkdirat 0700) -- base 바로 아래, root 소유
  - `<base>/<job_id>` → `<base>/.dms-trash/<job_id>` renameat(dir_fd 기준, 경로 문자열 재해석 없음)
실제 삭제(rm)는 하지 않는다 -- purge 파드(purge_runner.build_purge_pod: root + DAC_OVERRIDE·FOWNER 만, base 볼륨 하나,
고정 스크립트)가 trash 를 비운다. 이유(설계 D2·D3): 비 root 잡의 `<phase>` 는 요청자 소유 0755 라 cap 이 0 인 제어면
root 는 그 안을 지울 수 없고(EACCES -- files-k8s 실측 [1]), 제어면 컨테이너에 cap 을 더하는 것은 계약 위반이다
(tests/test_release_manifest_contract.py, 규칙 12).

왜 rename 은 cap 없는 uid 0 으로 되나: 원본 부모 `<base>`(root 0755) w+x, 대상 부모 `.dms-trash`(root 0700) w+x, 그리고
디렉터리를 다른 부모로 옮기므로 `<job_id>` 자체(root 0755) w('..' 갱신) -- 셋 다 소유자 비트로 충족된다. `<phase>`
안으로는 절대 들어가지 않는다(규칙 4) -- 다루는 대상은 root 소유인 `<base>` 와 `<base>/<job_id>` 뿐이다.

판정은 전부 fd 기준이다: base 는 한 번 열어(심링크 base 배포는 의도적 허용 -- 규칙 8) fstat 으로 전제(규칙 5: 소유자 ==
euid, g+w·o+w 없음)를 확인하고, 그 뒤 모든 조작은 그 dir_fd 상대(follow_symlinks=False·O_NOFOLLOW)다. 쓰지 않는 것:
os.path.exists·os.access·shutil·chmod·chown·unlink·rmdir(규칙 7·13 -- tests/test_control_plane_static.py).

진행 상태는 DB 장부가 아니라 **FS 에서 도출**한다(설계 D4): base 항목 유무와 trash 항목 유무. trash 이름이 결정적
(`<job_id>`)이라 rename 직후 크래시해도 다음 틱이 같은 결론에 이른다. DB 에 없는 디렉터리를 「고아」로 추론해 지우는
코드는 없다(설계 D12) -- 호출자는 아웃박스 행이 가리키는 job_id 만 넘긴다.
"""
import errno
import os
import stat

from .artifact_files import JOB_ID_RE

TRASH = ".dms-trash"


class TrashError(Exception):
    """파일 단계 실패(사유 코드). 첫 인자는 리터럴로만 -- tests/test_reason_codes_coverage.py 의 AST 추출기가 본다."""

    def __init__(self, reason_code: str, detail: str = ""):
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(f"{reason_code}: {detail}" if detail else reason_code)


def _check_job_id(job_id) -> None:
    # DB(아웃박스 jobs)가 신뢰 경계다 -- "../x"·"a/b"·"" 가 dir_fd 상대 이름으로 쓰이면 base 밖·base 자체를 가리킨다.
    # assert 가 아니라 예외: python -O 가 지우면 안 되는 검사다.
    if not isinstance(job_id, str) or not JOB_ID_RE.fullmatch(job_id):
        raise ValueError(f"invalid job id: {job_id!r}"[:200])


def _open_base(base) -> int:
    """base 를 디렉터리 fd 로 연다(심링크 base 는 따라간다 -- 규칙 8). 열 수 없으면 purge_base_unavailable(ENOENT 를
    「지울 것 없음」으로 접지 않는다 -- 모름 ≠ 없음). 소유자가 euid 가 아니거나 g+w·o+w 면 purge_base_unsafe(규칙 5 의
    전제가 깨진 base 에서는 rename 대상이 요청자 손에 있을 수 있다)."""
    if not isinstance(base, str) or not base:
        raise TrashError("purge_base_unavailable", repr(base)[:200])
    try:
        bfd = os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    except OSError as exc:
        raise TrashError("purge_base_unavailable", f"{type(exc).__name__}: {exc.strerror}") from exc
    try:
        st = os.fstat(bfd)
    except OSError as exc:
        os.close(bfd)
        raise TrashError("purge_base_unavailable", f"{type(exc).__name__}: {exc.strerror}") from exc
    if st.st_uid != os.geteuid() or st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        os.close(bfd)
        raise TrashError("purge_base_unsafe", f"base uid={st.st_uid} mode={stat.S_IMODE(st.st_mode):o}")
    return bfd


def _open_trash(bfd: int, *, create: bool) -> "int | None":
    """`.dms-trash` 디렉터리 fd. create=False 이고 없으면 None. 심링크(O_NOFOLLOW → ELOOP)·비디렉터리(ENOTDIR)·소유자
    불일치·0700 이 아님(그룹·other 비트)·base 와 다른 파일시스템(st_dev -- rename 이 EXDEV 로 실패하거나, 마운트로
    바꿔치기된 trash)이면 purge_base_unsafe."""
    if create:
        try:
            os.mkdir(TRASH, 0o700, dir_fd=bfd)
        except FileExistsError:
            pass
        except OSError as exc:
            raise TrashError("purge_detach_failed", f"mkdir {TRASH}: {exc.strerror}") from exc
    try:
        tfd = os.open(TRASH, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=bfd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise TrashError("purge_base_unsafe", f"{TRASH}: {exc.strerror}") from exc
    try:
        tst, bst = os.fstat(tfd), os.fstat(bfd)
    except OSError as exc:
        os.close(tfd)
        raise TrashError("purge_base_unsafe", f"{TRASH}: {exc.strerror}") from exc
    if tst.st_uid != os.geteuid() or tst.st_mode & 0o077 or tst.st_dev != bst.st_dev:
        os.close(tfd)
        raise TrashError("purge_base_unsafe",
                         f"{TRASH} uid={tst.st_uid} mode={stat.S_IMODE(tst.st_mode):o} "
                         f"same_dev={tst.st_dev == bst.st_dev}")
    return tfd


def _present(name: str, dir_fd: int) -> bool:
    """lstat(dir_fd 상대, 심링크 비추적) -- 있으면 True(종류 무관: 심링크·파일도 「있음」이다). 없으면 False. 그 밖의
    오류는 모름이라 올린다(호출자가 사유 코드로 접는다)."""
    try:
        os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def entry_states(base, job_ids) -> dict:
    """{job_id: (base 에 있음, trash 에 있음)} -- base 를 한 번만 연다. trash 를 만들지 않는다(읽기 전용 판정).
    잘못된 job_id 는 ValueError(호출자가 아웃박스 행 검증에서 먼저 거른다)."""
    ids = list(job_ids)
    for jid in ids:
        _check_job_id(jid)
    bfd = _open_base(base)
    try:
        tfd = _open_trash(bfd, create=False)
        try:
            out = {}
            for jid in ids:
                try:
                    in_base = _present(jid, bfd)
                    in_trash = tfd is not None and _present(jid, tfd)
                except OSError as exc:
                    raise TrashError("purge_base_unavailable", f"stat {jid}: {exc.strerror}") from exc
                out[jid] = (in_base, in_trash)
            return out
        finally:
            if tfd is not None:
                os.close(tfd)
    finally:
        os.close(bfd)


def entry_state(base, job_id) -> "tuple[bool, bool]":
    """(base 에 있음, trash 에 있음) -- 완료 판정은 (False, False)."""
    return entry_states(base, [job_id])[job_id]


def detach(base, job_id) -> str:
    """`<base>/<job_id>` 를 trash 로 옮긴다. 반환:
      'detached'       옮겼다
      'absent'         base 에 없다(이미 옮겼거나 처음부터 없음 -- trash 유무는 호출자가 entry_state 로 본다)
      'pending_trash'  trash 에 같은 이름(비어 있지 않음)이 아직 있다 -- purge 파드가 비운 뒤 다음 틱에 다시
    실패: TrashError(purge_base_unavailable | purge_base_unsafe | artifact_dir_unexpected | purge_detach_failed).

    `<job_id>` 가 디렉터리가 아니거나(심링크·파일) 소유자가 euid 가 아니면 artifact_dir_unexpected -- 러너는 `<job_id>` 를
    root 디렉터리로 만든다(규칙 5). 그 밖의 모양은 누군가 base 를 건드렸다는 신호라 옮기지 않고 운영자 확인으로 돌린다
    (심링크면 링크도 대상도 건드리지 않는다)."""
    _check_job_id(job_id)
    bfd = _open_base(base)
    try:
        try:
            jst = os.stat(job_id, dir_fd=bfd, follow_symlinks=False)
        except FileNotFoundError:
            return "absent"
        except OSError as exc:
            raise TrashError("purge_base_unavailable", f"stat {job_id}: {exc.strerror}") from exc
        if not stat.S_ISDIR(jst.st_mode) or jst.st_uid != os.geteuid():
            raise TrashError("artifact_dir_unexpected",
                             f"{job_id} mode={jst.st_mode:o} uid={jst.st_uid}")
        tfd = _open_trash(bfd, create=True)
        if tfd is None:                    # 방금 만든 trash 가 사라졌다 -- 경합, 다음 틱 재시도
            raise TrashError("purge_detach_failed", f"{TRASH} vanished")
        try:
            try:
                os.rename(job_id, job_id, src_dir_fd=bfd, dst_dir_fd=tfd)
            except FileNotFoundError:
                return "absent"            # stat 과 rename 사이에 사라졌다(다른 컨트롤러 -- 리스 만료 경합)
            except OSError as exc:
                if exc.errno in (errno.ENOTEMPTY, errno.EEXIST):
                    return "pending_trash"
                raise TrashError("purge_detach_failed", f"rename {job_id}: {exc.strerror}") from exc
            return "detached"
        finally:
            os.close(tfd)
    finally:
        os.close(bfd)
