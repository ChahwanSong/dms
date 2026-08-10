"""아티팩트 base 의 단일 진실 원천(슬라이스 18).

- strip_scheme: file:// **접두사만** 벗긴다. str.replace("file://", "") 전체 치환
  계열은 경로 중간의 file:// 까지 지워 두 계열이 같은 문자열에서 다른 경로를
  만든다(설계 §2.2) -- 저장소 전체가 이 함수 하나로 통일된다.
- resolve_artifact_base: DB(control_state.artifact_base_uri)가 있으면 그것, 없으면
  env(settings.artifact_base_uri). 모든 소비자가 이 함수만 통과한다(설계 §2.1).
"""
import os
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


def roundtrip_artifact_base(path: str) -> "str | None":
    """즉석 검증(설계 §2.4a): 존재·디렉터리 확인에 그치지 않고 임시 파일
    생성→쓰기→읽기→삭제를 **실제로** 한다. hostPath type: Directory 는 존재만
    요구하지만(설계 §1-4), 이 프로세스 관점의 쓰기 왕복이 안 되면 같은 마운트를
    쓰는 아티팩트 읽기·요약 읽기도 죽는다. 성공 None, 실패 reason_code."""
    if not os.path.exists(path):
        return "artifact_base_missing"
    if not os.path.isdir(path):
        return "artifact_base_not_directory"
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
