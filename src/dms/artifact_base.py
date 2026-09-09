"""아티팩트 base 의 단일 진실 원천(슬라이스 18).

- strip_scheme: file:// **접두사만** 벗긴다. str.replace("file://", "") 전체 치환
  계열은 경로 중간의 file:// 까지 지워 두 계열이 같은 문자열에서 다른 경로를
  만든다(설계 §2.2) -- 저장소 전체가 이 함수 하나로 통일된다.
- resolve_artifact_base: DB(control_state.artifact_base_uri)가 있으면 그것, 없으면
  env(settings.artifact_base_uri). 모든 소비자가 이 함수만 통과한다(설계 §2.1).
"""
import os
import stat
import uuid

from .domain import DomainValidationError


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
    증명하지 않는다 -- 노드 홉(에이전트 os.access, root)도 마찬가지고, 잡 파드의
    요청자 권한은 preflight 가 요청자 uid 로 따로 검사한다.

    소유권·mode 전제(2026-09-09 리뷰): base 는 **이 프로세스의 euid 소유**(운영은
    root)이고 world-writable 이 아니어야 한다 -- artifact_files.assert_contained 의
    봉쇄 기준(realpath(<base>/<job_id>))이 그 전제 위에 선다. root 면 존재하는 모든
    디렉터리에 프로브를 쓸 수 있어 allowlist 만으론 "/cephfs/scratch(777)" 가 그대로
    통과하므로 여기서 거른다. 개발·테스트(비root)에선 tmp 디렉터리가 자기 소유라
    같은 규칙이 그대로 성립한다."""
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
