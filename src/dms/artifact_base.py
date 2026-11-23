"""아티팩트 base 의 단일 진실 원천(슬라이스 18).

- strip_scheme: file:// **접두사만** 벗긴다. str.replace("file://", "") 전체 치환
  계열은 경로 중간의 file:// 까지 지워 두 계열이 같은 문자열에서 다른 경로를
  만든다(설계 §2.2) -- 저장소 전체가 이 함수 하나로 통일된다.
- resolve_artifact_base: DB(control_state.artifact_base_uri)가 있으면 그것, 없으면
  env(settings.artifact_base_uri). 모든 소비자가 이 함수만 통과한다(설계 §2.1).
"""
import errno
import os
import posixpath
import socket
import stat
import struct
import threading
import time
import uuid

from .artifact_files import JOB_ID_RE
from .db import utc_now_iso
from .domain import DataJobState, DomainValidationError
from .mountinfo import covering_mount, parse_mount_table


# 잡 파드 **안**에서 아티팩트 base 가 보이는 고정 경로(2026-09-09). 공용 디렉터리(base 의
# 부모 <SHARED_DIR>/dms -- 옛 테스트베드는 /cephfs/dms, 2026-11-03 ③)를 root:root 로 잠가도 잡이 돌아야 한다 -- 잠금은 **그룹 쓰기·other 통과 금지(750)**(ARCHITECTURE
# §7; LDAP 보조 그룹 시절(2026-10-07~25)의 gid 0 그룹 우려는 dms-k8s 에서 사라졌지만 잠금 규칙은 그대로 둔다). 부모의 711·755 는 금지다 -- other-x 가 있으면
# 모든 uid 가 base(755)와 0755 잡 디렉터리·0644 아티팩트를 직접 읽어 API 의 소유자 검사(artifact_files)를 우회한다
# (711/755 는 base **자체**의 선택지다). 러너(launcher)는
# root 지만 도구(dscan/dsync)와 rank.sh 는 요청자 uid 로 돌아 <base>/<job>/<phase> 까지의
# 모든 부모를 통과(x)해야 한다. base 를 **전용 hostPath 볼륨**으로 이 경로에 마운트하면
# 커널은 마운트 루트 위의 호스트 부모(<SHARED_DIR>/dms)를 검사하지 않는다 -- 요청자는 마운트
# 루트(= base 자체, root:root 755)부터 내려간다. 호스트 경로(제어면 읽기·artifact_uri)는
# 그대로 <base>/... 이고, 파드 안 경로만 이 값으로 바뀐다(execution_manifests._artifact_dir,
# execution_volcano._volumes). 스토리지 mount_path 가 이 경로와 겹치면 invalid_storage
# (repositories/storages._validate) -- 같은 mountPath 두 개는 파드 스펙이 깨진다.
ARTIFACT_MOUNT = "/dms-artifact-base"


def strip_scheme(base_uri: str) -> str:
    # api/artifacts.py 에 있던 것을 그대로 승격 -- 실행 계열(execution_*.py)이
    # FastAPI 계층(api/)을 임포트하지 않도록 중립 모듈로 옮겼다. api/artifacts.py
    # 가 재수출하므로 기존 임포트 경로는 그대로 산다.
    return base_uri[len("file://"):] if base_uri.startswith("file://") else base_uri


def resolve_artifact_base(control_repo, settings) -> str:
    """DB 값 우선, NULL 이면 env(하위호환 -- 기존 배포 무변화). Settings 는 frozen
    dataclass 라 런타임 재읽기 경로가 없고, 컨트롤러 루프와 app.state 가 같은
    인스턴스를 캡처한다 -- 재시작 없이 반영되려면 DB 를 매번 조회해야 한다. 비용은
    스테퍼가 이미 매 틱 정책을 DB 재조회하는 것과 같은 규모라 논쟁이 없다."""
    row = control_repo.control_state()
    if row and row.get("artifact_base_uri"):
        return row["artifact_base_uri"]
    return settings.artifact_base_uri


def normalize_artifact_base(raw: str) -> str:
    """PUT/validate 입력을 정규형 file:///<절대경로> 로 정규화한다(설계 §2.2).
    저장 시점 한 곳에서만 한다 -- 소비자 4곳이 방어 코드를 복제하지 않도록.
    실패는 DomainValidationError(reason_code) -- 라우트가 422 로 나른다."""
    value = (raw or "").strip()
    if value.startswith("file://"):
        value = value[len("file://"):]
    if "file://" in value:
        # 경로 중간 file:// 금지: strip_scheme(접두사만)과 전체 치환(replace)이
        # **다른 경로**를 만드는 바로 그 입력이다(설계 §2.2). 해석기를 한 계열로
        # 통일했지만, 그런 값이 저장되는 일 자체를 여기서 없앤다.
        raise DomainValidationError("artifact_base_scheme_in_path", raw)
    while len(value) > 1 and value.endswith("/"):
        value = value[:-1]   # 후행 슬래시 제거 -- f"{base}/{job_id}" 조립 정합성
    if not value.startswith("/") or value == "/":
        # 상대경로·빈 값 거부 + 루트("/") 거부: 루트를 아티팩트 트리로 쓰는
        # 구성은 오타이고, 후행 슬래시 제거와 조합하면 "//" 경로를 만든다.
        raise DomainValidationError("artifact_base_not_absolute", raw)
    if ".." in value.split("/"):
        raise DomainValidationError("artifact_base_traversal", raw)
    return f"file://{value}"


def _abs_norm(path: str) -> str:
    """normpath + 선두 '//' 접기. POSIX 는 선두 '//' 하나를 구현 정의로 남겨 posixpath.normpath 가 보존하지만 리눅스(노드·잡
    파드)에서는 '/' 와 같은 경로다 -- 접지 않으면 '//dms_shared' 가 '/dms_shared/dms' 와의 문자열 비교를 빠져나간다(2026-11-03
    리뷰)."""
    p = posixpath.normpath(path)
    return "/" + p.lstrip("/") if p.startswith("//") else p


def state_root_of(base_uri) -> "str | None":
    """아티팩트 base 에서 DMS 상태 디렉터리를 낸다(2026-11-03 ③, 설계 v6 3-1). 배치 규약이 <SHARED_DIR>/dms/{artifacts,
    pgdata, backup/pg, registry} 라 base 가 **…/dms/artifacts 꼴이면** 그 부모(<SHARED_DIR>/dms)가 상태 디렉터리다 -- 옛
    테스트베드 배치(/cephfs/dms/artifacts → /cephfs/dms)도 같다. 그 꼴이 아니면 base **자신**만 지킨다(2026-11-03 리뷰): 배치
    규약을 따르지 않는 base(e2e 하네스의 <tmp>/artifacts, 옛 allowlist(/cephfs) 시절 포탈에 저장된 /cephfs/scratch-art 같은 DB
    base)의 부모를 지키면 형제 디렉터리(<tmp>/storage, /cephfs 아래 모든 스토리지)가 통째로 막힌다 -- 그런 base 의 부모에는
    PG·레지스트리·백업이 없다. 오버레이 렌더는 늘 file://<SHARED_DIR>/dms/artifacts 를 내므로(env base) 운영 배치는 부모를 지킨다.
    절대 경로가 아니거나 '/' 이면 None(모름 -- 지킬 것이 없다)."""
    if not isinstance(base_uri, str) or not base_uri:
        return None
    path = _abs_norm(strip_scheme(base_uri))
    if not path.startswith("/") or path == "/":
        return None
    parent = posixpath.dirname(path)
    if posixpath.basename(path) == "artifacts" and posixpath.basename(parent) == "dms":
        return parent
    return path


def dms_state_roots(control_repo, settings) -> tuple:
    """스토리지 등록 가드(routes_storages)가 지키는 상태 디렉터리들(2026-11-03 ③). env base(설치기가 SHARED_DIR 에서 렌더한
    값 -- PG·레지스트리·백업이 같은 <SHARED_DIR>/dms 아래라 DB 에서 base 를 바꿔도 이 위치는 그대로다)와 지금 유효한 base(DB
    우선 -- 실제 산출물이 있는 곳) 둘 다에서 낸다. 정렬된 튜플."""
    roots = {state_root_of(getattr(settings, "artifact_base_uri", None)),
             state_root_of(resolve_artifact_base(control_repo, settings))}
    return tuple(sorted(r for r in roots if r))


def overlaps_dms_state(path, roots) -> bool:
    """path 가 상태 디렉터리 하나와 같거나, 그 아래거나, 그것을 덮는가(상위). 문자열 비교다 -- 스토리지 경로는 노드 경로라
    api 파드에서 realpath 로 풀 수 없다(심링크로 우회한 등록은 막지 못한다 -- 정직한 한계). 후행 슬래시로 /dms 가 /dms2 를
    잘못 삼키지 않게 성분 경계로 본다. 선두 '//' 는 '/' 로 접는다(_abs_norm -- '//dms_shared' 우회 방지)."""
    if not isinstance(path, str) or not path.startswith("/"):
        return False
    p = _abs_norm(path).rstrip("/")
    for root in roots:
        r = _abs_norm(root).rstrip("/")
        if p == r or p.startswith(r + "/") or r.startswith(p + "/"):
            return True
    return False


def allowlist_reason(path: str, prefixes) -> "str | None":
    """경로 접두 allowlist(settings.artifact_base_allowed_prefixes) 판정. 비어 있으면
    무제한(None). realpath 로 해석한 경로가 어느 접두(그 자체 또는 그 아래)에도
    없으면 artifact_base_outside_allowlist. `realpath != path` 를 거부하지는 않는다
    -- base 접두가 심링크인 배포(/data → /cephfs/x)는 의도적으로 허용한다
    (artifact_files.assert_contained 와 같은 입장). 접두 자체도 realpath 를 함께
    비교해 마운트 경로가 심링크여도 통한다."""
    if isinstance(prefixes, str):
        prefixes = (prefixes,)     # 문자열을 글자 단위로 돌면 '/' 가 후보가 돼 무제한이 된다
    if not prefixes:
        return None
    real = os.path.realpath(path)
    for prefix in prefixes:
        for candidate in {prefix, os.path.realpath(prefix)}:
            if real == candidate or real.startswith(candidate.rstrip("/") + "/"):
                return None
    return "artifact_base_outside_allowlist"


# POSIX ACL 의 리눅스 xattr 표현(include/uapi/linux/posix_acl_xattr.h): 헤더 <I version(=2)
# 다음 8바이트 엔트리 <HHI(tag, perm, id) 의 나열. 태그·권한 비트는 커널 상수 그대로다.
_ACL_ACCESS, _ACL_DEFAULT = "system.posix_acl_access", "system.posix_acl_default"
_ACL_XATTR_VERSION = 2
_ACL_USER, _ACL_GROUP, _ACL_PERM_WRITE = 0x02, 0x08, 0x02
# "ACL 이 없다" 로 읽는 errno 들 -- 그 밖의 실패(EACCES·EIO…)는 모름이라 fail-closed 다.
_NO_ACL_ERRNOS = frozenset({errno.ENODATA, errno.ENOTSUP, errno.EOPNOTSUPP})


def _posix_acl_problem(path: str) -> "str | None":
    """base 의 POSIX ACL 이 요청자에게 쓰기를 줄 수 있으면 artifact_base_group_writable.

    root 로 os.getxattr 를 **읽기만** 한다(불변식 7 이 금지하는 셸·os.access·chown·chmod
    밖 -- 파일을 열지도 않는다). 판정은 보수적이다:
    - default ACL 이 **있으면**(내용 무관) 거부 -- 러너가 만드는 <job_id> 가 상속해 그 아래를
      요청자에게 여는 통로가 된다.
    - access ACL 에서 named(ACL_USER·ACL_GROUP) 항목이 쓰기 비트를 가지면 mask 와 무관하게
      거부 -- mask 는 chmod 한 번으로 넓어지는 값이라 지금의 유효 권한만 보면 다음 chmod 에
      조용히 열린다. group_obj 의 쓰기는 mask 를 통해 st_mode 의 S_IWGRP 로 드러나 호출자의
      검사가 이미 잡는다.
    - ENODATA/ENOTSUP/EOPNOTSUPP = ACL 없음(통과). 그 밖의 OSError·형식 오류(버전·길이)는
      모름 -- 모름을 통과로 접으면 이 검사가 있으나 마나라 같은 사유로 fail-closed 한다.
    NFSv4/GPFS 고유 ACL 은 이 표현이 아니라 보지 못한다. preflight 의 `test -w` 는 access(2) 라 base **자체**에
    걸린 그런 ACL 은 요청자 관점에서 반영하지만, **상속**(default POSIX ACL·NFSv4/GPFS inheritable ACE)이
    <job_id>·<phase> 에 주는 쓰기는 보지 못한다(base 자체엔 쓰기가 없을 수 있다) -- POSIX default ACL 은 이 함수가
    저장·3홉에서 막는다(잡 단위로는 보지 않는다 -- 2026-10-26 LDAP 보조 그룹 잡의 컨트롤러 정적 관문을 지웠다). NFSv4/GPFS
    상속 ACE 는 남는 위험이다(README §2b-1)."""
    try:
        os.getxattr(path, _ACL_DEFAULT)
        return "artifact_base_group_writable"      # 있다는 사실만으로 거부(내용 무관)
    except OSError as exc:
        if exc.errno not in _NO_ACL_ERRNOS:
            return "artifact_base_group_writable"
    try:
        raw = os.getxattr(path, _ACL_ACCESS)
    except OSError as exc:
        return None if exc.errno in _NO_ACL_ERRNOS else "artifact_base_group_writable"
    if len(raw) < 4 or (len(raw) - 4) % 8:
        return "artifact_base_group_writable"
    (version,) = struct.unpack_from("<I", raw, 0)
    if version != _ACL_XATTR_VERSION:
        return "artifact_base_group_writable"
    for offset in range(4, len(raw), 8):
        tag, perm, _ident = struct.unpack_from("<HHI", raw, offset)
        if tag in (_ACL_USER, _ACL_GROUP) and perm & _ACL_PERM_WRITE:
            return "artifact_base_group_writable"
    return None


def static_base_problem(path: str) -> "str | None":
    """base 의 요청자 관점 mode·POSIX ACL 판정(쓰기 프로브 없음) -- roundtrip_artifact_base(저장·3홉)의 규칙(2026-10-26
    까지는 stepper 의 보조 그룹 잡 제출 관문도 공유했다). 순서: 쓰기 노출(g+w → ACL) 먼저, 통과(o+x) 다음 -- 둘 다
    어기면 더 위험한 쪽(요청자 쓰기)을 보여 준다.
      - S_IWGRP(gid 무관) → artifact_base_group_writable (보조 gid 0 도 인정하므로 root 그룹 g+w 도 안전하지 않다)
      - _posix_acl_problem(named 쓰기 항목·default ACL·읽기 실패) → artifact_base_group_writable
      - S_IXOTH 없음 → artifact_base_not_traversable (D12)
    stat 실패(OSError)는 **올린다** -- '모름' 을 통과로 접지 않도록 호출자가 정한다(roundtrip 은 빨간불). 소유자·o+w 는
    roundtrip 몫이다."""
    st = os.stat(path)
    if st.st_mode & stat.S_IWGRP:
        return "artifact_base_group_writable"
    acl = _posix_acl_problem(path)
    if acl is not None:
        return acl
    if not st.st_mode & stat.S_IXOTH:
        return "artifact_base_not_traversable"
    return None


def roundtrip_artifact_base(path: str) -> "str | None":
    """즉석 검증(설계 §2.4a): 존재·디렉터리 확인에 그치지 않고 임시 파일
    생성→쓰기→읽기→삭제를 **실제로** 한다. hostPath type: Directory 는 존재만
    요구하지만(설계 §1-4), 이 프로세스 관점의 쓰기 왕복이 안 되면 같은 마운트를
    쓰는 아티팩트 읽기·요약 읽기도 죽는다. 성공 None, 실패 reason_code.

    "이 프로세스 관점" 의 뜻(2026-09-09): api/controller 는 uid 0, capabilities
    전부 drop 으로 돈다. 성공 = 마운트 존재 + rw + MDS 가 root 를 squash 하지
    않음 + EROFS/ENOSPC/EDQUOT 아님. cap 이 없으므로 root 도 **소유자 mode 비트**
    의 지배를 받는다(root 소유 0400 은 root 도 못 쓴다; 남의 0600 은 EACCES) --
    운영 base 는 root:root 0755 라 소유자로서 쓴다. 어떤 비root uid 의 쓰기 권한도
    증명하지 않는다 -- 노드 홉(에이전트 os.access, root)도 마찬가지다. 그래서 요청자
    관점의 조건은 mode 비트·ACL 로 **직접** 본다(아래): g+w·POSIX ACL 쓰기 항목·default
    ACL·other-x 없음은 여기서 빨간불이다. 이 함수는 저장(PUT/validate 422)과 3홉 표시용 --
    잡 제출을 막지 않는다(stepper·planner 는 artifact_base_check_ok 를 읽지 않는다). 잡 단위로는
    preflight 가 base 통과(test -x)만 본다(execution_manifests._ARTIFACT_BASE_CHECK -- dms-k8s 는 모든 잡이 root 라
    요청자 uid 관점의 시험이 없다; LDAP 보조 그룹 잡의 컨트롤러 정적 관문은 2026-10-26 에 지웠다).

    소유권·mode 전제(2026-09-09 리뷰): base 는 **이 프로세스의 euid 소유**(운영은
    root)이고 world-writable 이 아니어야 한다 -- artifact_files.assert_contained 의
    봉쇄 기준(realpath(<base>/<job_id>))이 그 전제 위에 선다. root 면 존재하는 모든
    디렉터리에 프로브를 쓸 수 있어 allowlist 만으론 "/cephfs/scratch(777)" 가 그대로
    통과하므로 여기서 거른다. 개발·테스트(비root)에선 tmp 디렉터리가 자기 소유라
    같은 규칙이 그대로 성립한다.

    그룹 쓰기 금지(2026-10-07, 보조 그룹 D6): base 의 g+w 는 **그 그룹의 모든 사용자**에게 base 쓰기를 준다 --
    <job_id> 를 미리 만들어 봉쇄 기준을 옮기는 world-writable 과 같은 공격이다. gid 와 무관하게 거부하고, mode 비트에
    드러나지 않는 POSIX ACL 쓰기 항목·default ACL 도 같은 사유로 거부한다(_posix_acl_problem). (도입 이유였던 LDAP
    보조 그룹 잡은 2026-10-26 dms-k8s 에서 사라졌지만, 공용 파일시스템의 다른 사용자에 대한 봉쇄라 규칙은 남긴다.)

    other-x 필수(2026-10-07, D12 대체): base 는 other 실행(x)이 있어야 하고(755 또는 711), 없으면
    artifact_base_not_traversable(preflight 와 같은 코드)."""
    if not os.path.exists(path):
        return "artifact_base_missing"
    if not os.path.isdir(path):
        return "artifact_base_not_directory"
    try:
        st = os.stat(path)
    except OSError:
        return "artifact_base_not_writable"
    if st.st_uid != os.geteuid():
        return "artifact_base_not_owned"
    if st.st_mode & stat.S_IWOTH:
        return "artifact_base_world_writable"
    # g+w·ACL·o+x(static_base_problem). 프로브 **앞**이라 거부된 경로에는 프로브
    # 파일을 만들지 않는다.
    try:
        problem = static_base_problem(path)
    except OSError:
        return "artifact_base_not_writable"
    if problem is not None:
        return problem
    # 고유 이름: 동시 검증(포탈 폴링 + 컨트롤러 루프)이 서로의 probe 를 지우지
    # 않도록 한다.
    probe = os.path.join(path, f".dms-base-check-{uuid.uuid4().hex}")
    try:
        with open(probe, "w") as f:
            f.write("dms")
        with open(probe) as f:
            if f.read() != "dms":
                return "artifact_base_not_writable"
    except OSError:
        return "artifact_base_not_writable"
    finally:
        try:
            os.unlink(probe)
        except OSError:
            pass  # 생성 자체가 실패했으면 지울 것이 없다 -- 판정에 무관
    return None


# ---- base 가용성(2026-11-23 사고: dms-api 가 CephFS 미마운트 노드에 떠 /mgmt_storage = 빈 로컬 디렉터리) ----------------
# 쓰기 왕복(roundtrip_artifact_base)은 로컬의 root:root 755 디렉터리에서도 통과한다 -- 「진짜 공유 스토리지인가」를 모른다.
# 그래서 쓰기 없는 가용성 판정을 따로 둔다: 디렉터리 열기 + 첫 dirent + **최근 잡 디렉터리**(자연 센티넬). 심는 센티넬 파일은
# 낡음(dotfile 빠진 복사·스냅숏 롤백)·나쁜 노드 오심기·재설정 흐름이라는 새 장애 모드를 만들어 쓰지 않는다(설계 v2).
# 이 판정은 **base 수준**이다 -- 파일별 errno 로 「base 접근 불가」를 가르면 요청자가 자기 <phase> 를 chmod 000·심링크로
# 바꿔 응답을 고르는 존재 오라클이 된다(artifact_files 모듈 docstring). base 와 <base>/<job_id> 는 root 소유라 요청자가
# 판정을 움직일 수 없고, 대상 job_id 는 DB 가 고른다(요청자 입력 아님).

# 이 모듈(과 그 판정을 싣는 라우트·모니터)이 내는 점검 사유 전부 -- return·dict 로 흘러 AST 그물(detail=/reason_code=) 밖이라
# tests/test_artifact_base_availability.py 가 이 집합 ⊆ reasonCodes.json 을 따로 고정한다.
ARTIFACT_BASE_CHECK_REASONS = frozenset({
    "artifact_base_missing", "artifact_base_not_directory", "artifact_base_unreadable",
    "artifact_base_check_timeout", "artifact_base_jobs_missing",
    "artifact_base_outside_allowlist", "artifact_base_not_owned", "artifact_base_world_writable",
    "artifact_base_group_writable", "artifact_base_not_traversable", "artifact_base_not_writable",
})
# 사용자 라우트의 공개 코드는 이것 하나다(503) -- 세부 사유는 관리 화면·로그 전용.
ARTIFACT_BASE_UNAVAILABLE = "artifact_base_unavailable"

# base 를 덮는 마운트가 이 계열이면 「로컬 디스크로 보임」 경고(진단 전용 -- 판정·게이트에 쓰지 않는다: 사이트마다 공유
# 스토리지 종류가 다르고, 이름만으로 공유/로컬을 확정할 수 없다).
LOCAL_FSTYPES = frozenset({"ext2", "ext3", "ext4", "xfs", "btrfs", "overlay", "tmpfs", "zfs", "f2fs"})
# 최근 잡 표본 크기와 그 표본을 고르는 창(성공 잡 최신 N건). 창을 두는 이유: force 로 base 를 바꾼 직후엔 최근 잡이 전부 옛
# base 것이라 현재 base 의 잡을 찾으려 성공 잡 전체를 훑게 된다 -- 창 안에 없으면 판정 생략(모름)이 맞다.
RECENT_JOB_SAMPLE = 5
RECENT_JOB_WINDOW = 200
CHECK_TIMEOUT_SECONDS = 10.0
CHECK_CACHE_SECONDS = 3.0


def identity_job_ids(repos_or_db, settings, base_uri: str) -> list:
    """잡 디렉터리 교차 확인에 쓸 잡 id -- **실 실행 백엔드(volcano)에서만**. stub 백엔드(개발·e2e)는 잡을 성공시키고
    artifact_uri 를 남기지만 러너가 없어 <base>/<job_id> 를 만들지 않는다 -- 거기서 교차 확인하면 멀쩡한 base 가
    jobs_missing 이 돼 아티팩트 열람이 전부 503 이 된다. settings 에 필드가 없으면(테스트 스텁) volcano 로 본다."""
    if getattr(settings, "execution_backend", "volcano") != "volcano":
        return []
    return recent_job_ids(repos_or_db, base_uri, since=last_base_change_at(repos_or_db))


def last_base_change_at(repos_or_db) -> "str | None":
    """마지막 artifact base 변경(PUT 감사 행) 시각. force 변경 때 진행 중이던 잡은 **옛 base** 에 쓰지만 stepper 가 확정
    시점의 새 base 로 artifact_uri 를 남긴다 -- 그 잡들을 교차 확인에 넣으면 멀쩡한 새 base 가 jobs_missing 이 된다
    (2026-11-23 리뷰). 그래서 변경 **뒤에 만든** 잡만 표본으로 쓴다. 감사 행이 없거나(env 만 쓰는 배포) 못 읽으면 None."""
    db = getattr(repos_or_db, "db", repos_or_db)
    try:
        row = db.query_one(
            """SELECT at FROM audit_log WHERE mutation_class = 'artifact_base' AND operation = 'set'
               ORDER BY id DESC LIMIT 1""")
    except Exception:       # noqa: BLE001 -- 모름 = 거르지 않음(정확 일치가 다른 base 의 잡은 이미 뺀다)
        return None
    return row["at"] if row else None


def recent_job_ids(repos_or_db, base_uri: str, limit: int = RECENT_JOB_SAMPLE, *, since: "str | None" = None) -> list:
    """현재 base 아래 아티팩트 디렉터리를 가졌을 최근 성공 잡 id(최신순, 최대 limit).

    성공 잡만 본다: stepper 는 성공 경로에서 artifact_uri = f"{base_uri}/{job_id}" 를 남기고, 그 잡의 <base>/<job_id> 는
    러너가 root 로 만든 디렉터리다(요청 삭제는 행을 먼저 지우므로 행이 있으면 디렉터리도 있어야 한다). **예외 -- 결과 파일
    만료(dms-k8s 전용, 2026-10-26)**: expire_artifacts 는 행·상태·artifact_uri 를 그대로 둔 채 artifacts_expired_at 만 찍고
    (updated_at 은 안 바뀐다) request_purger 가 그 뒤 <base>/<job_id> 를 .dms-trash 로 떼어 낸다 -- 표시가 디렉터리 이동보다
    먼저라 표시 없는(IS NULL) 잡만 「디렉터리가 있어야 하는」 잡이다. 만료 잡을 표본에 넣으면 보존일 안에 성공 잡이 없던
    사이트(보존일을 낮춘 곳)의 멀쩡한 base 가 jobs_missing 이 된다(2026-11-23 이식 리뷰). 거름은 파이썬에서 한다 -- SQL
    WHERE 에 넣으면 전부 만료인 사이트에서 Postgres 가 성공 잡 인덱스 전체를 훑는다(창 RECENT_JOB_WINDOW 로 비용 일정 유지).
    비교는 파이썬의 정확 일치다 -- SQL LIKE 는 '_' 가 와일드카드라 다른 base 의 잡이 섞인다. 질의는
    idx_data_jobs_state(state, updated_at) 를 역순으로 RECENT_JOB_WINDOW 건만 읽는다(data_jobs 가 수만 행이어도 비용 일정).
    job_id 는 JOB_ID_RE fullmatch 만 통과(DB 가 신뢰 경계 -- 변조 id 로 base 밖을 fstatat 하지 않게). dms-ssc 에는
    artifacts_expired_at 칸이 없다 -- 그쪽으로 되돌려 보낼 때 이 거름은 빼고 가져간다."""
    db = getattr(repos_or_db, "db", repos_or_db)
    rows = db.query(
        """SELECT job_id, artifact_uri, created_at, artifacts_expired_at FROM data_jobs WHERE state = :s
           ORDER BY updated_at DESC LIMIT :n""",
        {"s": DataJobState.SUCCEEDED.value, "n": RECENT_JOB_WINDOW})
    out = []
    for r in rows:
        jid = r["job_id"]
        if (isinstance(jid, str) and JOB_ID_RE.fullmatch(jid)
                and r["artifacts_expired_at"] is None
                and r["artifact_uri"] == f"{base_uri}/{jid}"
                and (since is None or (isinstance(r["created_at"], str) and r["created_at"] > since))):
            out.append(jid)
            if len(out) >= limit:
                break
    return out


def availability_check(path: str, job_ids) -> dict:
    """쓰기 없는 base 가용성 판정. {"ok", "reason", "jobs_checked", "jobs_found"}.

    1) base 를 디렉터리로 연다(O_NONBLOCK -- artifact_files 와 같은 습관). ENOENT → artifact_base_missing, ENOTDIR →
       artifact_base_not_directory, 그 밖(EACCES·EIO·ESTALE·ENOTCONN) → artifact_base_unreadable.
    2) 첫 dirent 하나만 읽는다(getdents 의 EIO/ESTALE 를 잡는다). listdir 금지 -- base 아래 잡 디렉터리는 수십만일 수 있다.
    3) job_ids 마다 fstatat(base fd, job_id, AT_SYMLINK_NOFOLLOW) 가 디렉터리면 found. ENOENT/ENOTDIR 은 없음, 그 밖 →
       _unreadable. 하나 이상 확인했는데 하나도 없으면 artifact_base_jobs_missing(빈 로컬 디렉터리 = 사고 서명). job_ids
       가 비면 판정 생략(jobs_found None -- 모름 ≠ 실패: 새 설치·force 변경 직후)."""
    def result(ok, reason, checked=0, found=None):
        return {"ok": ok, "reason": reason, "jobs_checked": checked, "jobs_found": found}
    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NONBLOCK | os.O_CLOEXEC)
    except FileNotFoundError:
        return result(False, "artifact_base_missing")
    except NotADirectoryError:
        return result(False, "artifact_base_not_directory")
    except OSError:
        return result(False, "artifact_base_unreadable")
    try:
        try:
            with os.scandir(fd) as it:
                next(it, None)
        except OSError:
            return result(False, "artifact_base_unreadable")
        ids = [j for j in (job_ids or ()) if isinstance(j, str) and JOB_ID_RE.fullmatch(j)]
        if not ids:
            return result(True, None)
        found = 0
        for jid in ids:
            try:
                st = os.stat(jid, dir_fd=fd, follow_symlinks=False)
            except (FileNotFoundError, NotADirectoryError):
                continue
            except OSError:
                return result(False, "artifact_base_unreadable", len(ids), None)
            if stat.S_ISDIR(st.st_mode):
                found += 1
        if found == 0:
            return result(False, "artifact_base_jobs_missing", len(ids), 0)
        return result(True, None, len(ids), found)
    finally:
        os.close(fd)


def posture_problem(path: str, prefixes) -> "str | None":
    """쓰기 없는 보안 자세(API 홉 표시용): allowlist → 소유자 == euid → o+w 없음 → static_base_problem(g+w·ACL·o+x).
    roundtrip_artifact_base 의 쓰기 직전까지와 같은 규칙이다(저장 뒤 바뀐 g+w 를 API 홉이 계속 드러낸다). stat 실패는 None --
    존재·읽기는 availability_check 의 몫이라 두 번 말하지 않는다. 게이트·503 판정에는 쓰지 않는다(경고 전용)."""
    reason = allowlist_reason(path, prefixes)
    if reason is not None:
        return reason
    try:
        st = os.stat(path)
    except OSError:
        return None
    if st.st_uid != os.geteuid():
        return "artifact_base_not_owned"
    if st.st_mode & stat.S_IWOTH:
        return "artifact_base_world_writable"
    try:
        return static_base_problem(path)
    except OSError:
        return None


def covering_fstype(path: str, *, mountinfo_path: str = "/proc/self/mountinfo") -> "str | None":
    """base 를 덮는 마운트의 파일시스템 종류(진단 전용, 실패 None). mountinfo 는 procfs 라 멈추지 않지만 realpath 는 마운트를
    건드리므로 이 함수도 runner 안에서만 부른다."""
    try:
        with open(mountinfo_path, encoding="utf-8", errors="replace") as f:
            table = parse_mount_table(f.read())
        point = covering_mount(table, os.path.realpath(path))
    except OSError:
        return None
    if point is None:
        return None
    return table[point]["fstype"] or None


def api_view_check(path: str, job_ids, prefixes) -> dict:
    """API 홉 한 번(쓰기 없음): 가용성 + 자세 + 파일시스템. 모니터·요청 프로브·관리 GET 이 같은 함수를 같은 runner 키로
    부른다 -- 진행 중 호출에 합류해 같은 답을 받는다(joiner 의 job_ids 가 조금 달라도 base 에 대한 같은 사실이다)."""
    availability = availability_check(path, job_ids)
    posture = posture_problem(path, prefixes)
    fstype = covering_fstype(path)
    return {"availability": availability, "posture": posture, "fstype": fstype,
            "local_fs": (fstype in LOCAL_FSTYPES) if fstype is not None else None}


def is_timeout(result) -> bool:
    return (isinstance(result, dict) and result.get("timed_out") is True
            and result.get("reason") == "artifact_base_check_timeout")


def normalize_api_check(result: dict) -> dict:
    """runner 결과(api_view_check 또는 타임아웃) → api_view_check 모양. 타임아웃은 가용성 실패(artifact_base_check_timeout)
    이고 자세·fstype 은 모름이다."""
    if is_timeout(result):
        return {"availability": {"ok": False, "reason": "artifact_base_check_timeout",
                                 "jobs_checked": 0, "jobs_found": None},
                "posture": None, "fstype": None, "local_fs": None,
                "stuck_since": result.get("stuck_since")}
    return result


class _Call:
    __slots__ = ("started", "started_at", "done", "result", "error")

    def __init__(self, started):
        self.started = started
        self.started_at = utc_now_iso()
        self.done = threading.Event()
        self.result = None
        self.error = None


class BaseCheckRunner:
    """FS 점검의 프로세스 단일 single-flight + 타임아웃(데몬 워커 스레드).

    멈춘 마운트(CephFS MDS 장애·evict)에서 stat·scandir 는 D 상태로 영원히 블록할 수 있다 -- 요청 스레드·readyz·컨트롤러
    단일 루프가 거기 매달리면 안 된다. 규칙:
      - 같은 key 의 진행 중 호출이 있으면 새로 띄우지 않고 **그 호출에 남은 마감까지 합류**한다(동시 프로브가 서로를
        timeout 으로 오진하지 않는다 -- 정상 점검은 수십 ms).
      - 진행 중 호출이 시작 후 timeout 을 넘겼으면(stuck) 즉시 타임아웃 결과 -- 새 스레드를 띄우지 않는다(D 상태 스레드가
        요청 수만큼 쌓이지 않게. 키당 최대 1개).
      - 끝난 결과는 cache_seconds 동안 재사용(실패 요청 반복이 MDS 를 두드리지 않게). 0 이면 캐시를 읽지 않는다(지금 점검).
    fn 의 예외(코드 결함·DB 오류)는 합류한 모두에게 다시 던진다 -- 「읽기 불가」로 위장하지 않는다."""

    def __init__(self, *, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._inflight: dict = {}
        self._cache: dict = {}

    def run(self, key, fn, *, timeout: float = CHECK_TIMEOUT_SECONDS,
            cache_seconds: float = CHECK_CACHE_SECONDS):
        with self._lock:
            now = self._clock()
            hit = self._cache.get(key)
            if hit is not None and cache_seconds > 0 and now - hit[0] < cache_seconds:
                return hit[1]
            call = self._inflight.get(key)
            if call is None:
                call = _Call(now)
                self._inflight[key] = call
                try:
                    threading.Thread(target=self._work, args=(key, call, fn),
                                     name="dms-base-check", daemon=True).start()
                except BaseException:
                    # 스레드 생성 실패(pids 한도·메모리 압박의 RuntimeError)면 등록을 되돌린다 -- 남겨 두면 done 이 영영
                    # set 되지 않아 이 키가 「멈춤」으로 굳고 아티팩트 라우트가 재시작까지 503 이다(2026-11-23 리뷰).
                    self._inflight.pop(key, None)
                    raise
            elif now - call.started >= timeout:
                return self._timeout(call)
            remaining = max(0.0, call.started + timeout - now)
        if call.done.wait(remaining):
            if call.error is not None:
                raise call.error
            return call.result
        return self._timeout(call)

    def stuck_since(self, key, timeout: float = CHECK_TIMEOUT_SECONDS) -> "str | None":
        """진행 중 호출이 마감을 넘겼으면 그 시작 시각(ISO), 아니면 None -- 라우트가 FS 를 열기 **전에** 보는 단락 신호."""
        with self._lock:
            call = self._inflight.get(key)
            if call is not None and self._clock() - call.started >= timeout:
                return call.started_at
        return None

    def peek(self, key, cache_seconds: float = CHECK_CACHE_SECONDS):
        """캐시에 신선한 결과가 있으면 그것, 없으면 None(점검을 시작하지 않는다) -- 호출자가 점검 재료(DB 질의)를 만들기
        전에 본다."""
        if cache_seconds <= 0:
            return None
        with self._lock:
            hit = self._cache.get(key)
            if hit is not None and self._clock() - hit[0] < cache_seconds:
                return hit[1]
        return None

    def busy(self, key) -> bool:
        """같은 key 의 점검이 진행 중인가(나이 무관)."""
        with self._lock:
            return key in self._inflight

    def _timeout(self, call) -> dict:
        return {"ok": False, "reason": "artifact_base_check_timeout", "timed_out": True,
                "stuck_since": call.started_at}

    def _work(self, key, call, fn):
        try:
            call.result = fn()
        except BaseException as exc:      # noqa: BLE001 -- 합류자에게 그대로 다시 던진다
            call.error = exc
        finally:
            with self._lock:
                if self._inflight.get(key) is call:
                    del self._inflight[key]
                if call.error is None:
                    self._cache[key] = (self._clock(), call.result)
            call.done.set()


# 컨트롤러 프로세스의 runner(컨트롤러 점검·stepper 요약 지연 공용). API 는 create_app 이 app.state 에 자기 것을 둔다.
PROCESS_RUNNER = BaseCheckRunner()


class NodeNameResolver:
    """이 파드가 떠 있는 노드 이름(표시 전용). env DMS_NODE_NAME(downward API, 40·41 매니페스트) → 없으면 rollout_runner.
    pod_briefs(selector) 에서 이름 == 호스트명인 파드의 node. 성공은 영구 캐시(파드는 노드를 옮기지 않는다), 실패(None)는
    ttl 동안 캐시해 k8s 목록 조회를 틱마다 하지 않는다. 매니페스트가 아직 apply 되지 않은 사이트(포탈 릴리스는 이미지만
    바꾼다)도 폴백으로 산다."""

    def __init__(self, rollout_runner, selector: str, *, environ=None, hostname=None,
                 clock=time.monotonic, ttl_seconds: float = 600.0):
        self._runner = rollout_runner
        self._selector = selector
        self._environ = os.environ if environ is None else environ
        self._hostname = hostname
        self._clock = clock
        self._ttl = ttl_seconds
        self._cached = None        # (at, name)

    def get(self) -> "str | None":
        env = (self._environ.get("DMS_NODE_NAME") or "").strip()
        if env:
            return env
        now = self._clock()
        if self._cached is not None and (self._cached[1] is not None or now - self._cached[0] < self._ttl):
            return self._cached[1]
        host = self._hostname or socket.gethostname()
        name = None
        if self._runner is not None:
            try:
                for b in self._runner.pod_briefs(selector=self._selector) or []:
                    node = b.get("node")
                    if b.get("name") == host and isinstance(node, str) and node:
                        name = node
                        break
            except Exception:       # noqa: BLE001 -- 진단 표시용이라 실패는 None(모름)
                name = None
        self._cached = (now, name)
        return name


def controller_check_once(repos, settings, *, runner=None, node_name=None) -> dict:
    """(c) 컨트롤러 자기 관점 검증(설계 §2.4c)의 루프 본체. 컨트롤러는
    read_summary 로 실제 **읽기**를 하는 유일한 프로세스다 -- 마운트가 없으면
    read_text 가 OSError 를 None 으로 접고(wiring) stepper 는 SUCCEEDED 를 유지한
    채 summary_unavailable 경고만 남긴다(§1-3, 실패가 조용하다). 그 실패를 사전에,
    화면에 보이게 주기적으로 자기 파일시스템에서 왕복 검증해 결과를 control_state
    에 남긴다. 검증한 uri 를 함께 남겨 GET 라우트가 "옛 base 의 결과"를 "확인
    대기 중"으로 구분한다(설계 §4).

    2026-11-23: 왕복 통과 뒤 가용성(availability_check -- 최근 잡 디렉터리)도 본다. 쓰기 왕복은 빈 로컬 디렉터리에서도
    통과하므로 컨트롤러가 미마운트 노드에 떠도 jobs_missing 으로 드러난다(표시 전용 -- 잡 제출은 이 값을 읽지 않는다).
    점검 전체는 runner(10초 타임아웃) 안이다 -- 컨트롤러는 모든 루프를 한 스레드로 돌아(run_forever) 멈춘 마운트의 stat 이
    전 루프를 세운다. node_name 은 어느 노드에서 본 결과인지(관리 화면의 엇갈림 상자·마운트 현황)."""
    base = resolve_artifact_base(repos.control, settings)
    path = strip_scheme(base)
    # allowlist 는 라우트(validate/PUT)와 **여기 양쪽**에서 본다 -- env 만 바꾼
    # 뒤 저장돼 있던 옛 base 가 allowlist 밖이면 화면의 컨트롤러 홉이 그 사실을
    # 드러내야 한다(저장 시점 검사만으로는 조용히 남는다).
    # getattr: 컨트롤러 루프 테스트의 설정 스텁 클래스들은 이 필드가 없다 -- 없으면
    # 무제한(() 과 같은 뜻)이지 오류가 아니다.
    prefixes = getattr(settings, "artifact_base_allowed_prefixes", ())
    job_ids = identity_job_ids(repos, settings, base)       # DB 는 runner 밖(멈추는 것은 FS 다)

    def work():
        reason = allowlist_reason(path, prefixes) or roundtrip_artifact_base(path)
        if reason is None:
            reason = availability_check(path, job_ids)["reason"]
        return {"reason": reason}

    result = (runner or PROCESS_RUNNER).run(("controller", path), work, cache_seconds=0)
    reason = result["reason"]
    repos.control.set_artifact_base_check(uri=base, ok=reason is None,
                                          reason=reason, node=node_name)
    return {"uri": base, "ok": reason is None, "reason": reason}
