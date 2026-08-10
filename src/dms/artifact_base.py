"""아티팩트 base 의 단일 진실 원천(슬라이스 18).

- strip_scheme: file:// **접두사만** 벗긴다. str.replace("file://", "") 전체 치환
  계열은 경로 중간의 file:// 까지 지워 두 계열이 같은 문자열에서 다른 경로를
  만든다(설계 §2.2) -- 저장소 전체가 이 함수 하나로 통일된다.
- resolve_artifact_base: DB(control_state.artifact_base_uri)가 있으면 그것, 없으면
  env(settings.artifact_base_uri). 모든 소비자가 이 함수만 통과한다(설계 §2.1).
"""


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
