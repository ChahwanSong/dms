"""아티팩트 base 의 단일 진실 원천(슬라이스 18).

- strip_scheme: file:// **접두사만** 벗긴다. str.replace("file://", "") 전체 치환
  계열은 경로 중간의 file:// 까지 지워 두 계열이 같은 문자열에서 다른 경로를
  만든다(설계 §2.2) -- 저장소 전체가 이 함수 하나로 통일된다.
- resolve_artifact_base: DB(control_state.artifact_base_uri)가 있으면 그것, 없으면
  env(settings.artifact_base_uri). 모든 소비자가 이 함수만 통과한다(설계 §2.1).
"""
import errno
import os
import stat
import struct
import uuid

from .domain import DomainValidationError


# 잡 파드 **안**에서 아티팩트 base 가 보이는 고정 경로(2026-09-09). 공용 디렉터리(base 의
# 부모, 예 /cephfs/dms)를 root:root 로 잠가도 잡이 돌아야 한다 -- 잠금은 **그룹 쓰기·other 통과 금지(750, gidNumber 0
# LDAP 그룹이 있으면 700)**: 2026-10-07 부터 잡이 LDAP 보조 그룹을 달고 돌고 보조 gid 0 도 인정하므로(D3), 770 은
# gidNumber 0 그룹 멤버의 잡에 root 그룹 쓰기를 연다(ARCHITECTURE §7). 부모의 711·755 는 금지다 -- other-x 가 있으면
# 모든 uid 가 base(755)와 0755 잡 디렉터리·0644 아티팩트를 직접 읽어 API 의 소유자 검사(artifact_files)를 우회한다
# (711/755 는 base **자체**의 선택지다). 러너(launcher)는
# root 지만 도구(dscan/dsync)와 rank.sh 는 요청자 uid 로 돌아 <base>/<job>/<phase> 까지의
# 모든 부모를 통과(x)해야 한다. base 를 **전용 hostPath 볼륨**으로 이 경로에 마운트하면
# 커널은 마운트 루트 위의 호스트 부모(/cephfs/dms)를 검사하지 않는다 -- 요청자는 마운트
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
    (컨트롤러 정적 관문 static_base_problem 으로 잡 단위에서도) 막고, NFSv4/GPFS 상속 ACE 는 남는 위험이다(README §2b-1)."""
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
    """base 의 요청자 관점 mode·POSIX ACL 판정(쓰기 프로브 없음) -- roundtrip_artifact_base(저장·3홉)와 stepper 의
    그룹 잡 제출 관문(_build_spec)이 공유하는 단일 규칙. 순서: 쓰기 노출(g+w → ACL) 먼저, 통과(o+x) 다음 -- 둘 다
    어기면 더 위험한 쪽(요청자 쓰기)을 보여 준다.
      - S_IWGRP(gid 무관) → artifact_base_group_writable (보조 gid 0 도 인정하므로 root 그룹 g+w 도 안전하지 않다)
      - _posix_acl_problem(named 쓰기 항목·default ACL·읽기 실패) → artifact_base_group_writable
      - S_IXOTH 없음 → artifact_base_not_traversable (launcher 는 보조 그룹 없이 hostfile 을 읽는다, D12)
    stat 실패(OSError)는 **올린다** -- '모름' 을 통과로 접지 않도록 호출자가 정한다(roundtrip 은 빨간불, stepper 는
    preflight 에 맡김). 소유자·o+w 는 roundtrip 몫이다(o+w 는 모든 잡의 preflight test -w 가 잡는다)."""
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
    잡 제출을 막지 않는다(stepper·planner 는 artifact_base_check_ok 를 읽지 않는다). 잡
    단위 차단은 둘이다: 보조 그룹이 실린 비 root 잡은 컨트롤러가 제출 직전에 같은 규칙
    (static_base_problem)을 base 에 직접 적용하고(stepper._build_spec -- default ACL 처럼 preflight
    가 못 보는 상속 경로), 모든 비 root 잡은 preflight 가 요청자 uid 로 base 를 시험한다
    (execution_manifests -- base 자체의 access(2)).

    소유권·mode 전제(2026-09-09 리뷰): base 는 **이 프로세스의 euid 소유**(운영은
    root)이고 world-writable 이 아니어야 한다 -- artifact_files.assert_contained 의
    봉쇄 기준(realpath(<base>/<job_id>))이 그 전제 위에 선다. root 면 존재하는 모든
    디렉터리에 프로브를 쓸 수 있어 allowlist 만으론 "/cephfs/scratch(777)" 가 그대로
    통과하므로 여기서 거른다. 개발·테스트(비root)에선 tmp 디렉터리가 자기 소유라
    같은 규칙이 그대로 성립한다.

    그룹 쓰기 금지(2026-10-07, 보조 그룹 D6): 잡 파드가 LDAP 보조 그룹을 달고 돌면 base 의
    g+w 는 **그 그룹의 모든 요청자**에게 base 쓰기를 준다 -- <job_id> 를 미리 만들어 봉쇄
    기준을 옮기는 world-writable 과 같은 공격이다. 보조 gid 0 도 인정하므로(D3) root 그룹
    g+w 도 안전하지 않아 gid 와 무관하게 거부하고, mode 비트에 드러나지 않는 POSIX ACL
    쓰기 항목·default ACL 도 같은 사유로 거부한다(_posix_acl_problem).

    other-x 필수(2026-10-07, D12 대체): 러너(launcher)의 mpirun 은 보조 그룹 없이 base 아래의
    hostfile 을 읽는다 -- 그룹으로 여는 750/710 은 그룹을 가진 preflight 는 통과하고
    launcher 쪽에서 "unable to open the hostfile" 로 죽는다. 그래서 base 는 other 실행(x)이
    있어야 하고(755 또는 711), 없으면 artifact_base_not_traversable(preflight 와 같은 코드)."""
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
    # g+w·ACL·o+x(static_base_problem -- stepper 관문과 같은 규칙). 프로브 **앞**이라 거부된 경로에는 프로브
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


def controller_check_once(repos, settings) -> dict:
    """(c) 컨트롤러 자기 관점 검증(설계 §2.4c)의 루프 본체. 컨트롤러는
    read_summary 로 실제 **읽기**를 하는 유일한 프로세스다 -- 마운트가 없으면
    read_text 가 OSError 를 None 으로 접고(wiring) stepper 는 SUCCEEDED 를 유지한
    채 summary_unavailable 경고만 남긴다(§1-3, 실패가 조용하다). 그 실패를 사전에,
    화면에 보이게 주기적으로 자기 파일시스템에서 왕복 검증해 결과를 control_state
    에 남긴다. 검증한 uri 를 함께 남겨 GET 라우트가 "옛 base 의 결과"를 "확인
    대기 중"으로 구분한다(설계 §4)."""
    base = resolve_artifact_base(repos.control, settings)
    path = strip_scheme(base)
    # allowlist 는 라우트(validate/PUT)와 **여기 양쪽**에서 본다 -- env 만 바꾼
    # 뒤 저장돼 있던 옛 base 가 allowlist 밖이면 화면의 컨트롤러 홉이 그 사실을
    # 드러내야 한다(저장 시점 검사만으로는 조용히 남는다).
    # getattr: 컨트롤러 루프 테스트의 설정 스텁 클래스들은 이 필드가 없다 -- 없으면
    # 무제한(() 과 같은 뜻)이지 오류가 아니다.
    prefixes = getattr(settings, "artifact_base_allowed_prefixes", ())
    reason = allowlist_reason(path, prefixes) or roundtrip_artifact_base(path)
    repos.control.set_artifact_base_check(uri=base, ok=reason is None,
                                          reason=reason)
    return {"uri": base, "ok": reason is None, "reason": reason}
