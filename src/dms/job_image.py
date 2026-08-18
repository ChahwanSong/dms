"""잡 이미지의 단일 진실 원천(슬라이스 35). artifact_base 와 같은 패턴.

- resolve_job_image: DB(control_state.job_image)가 있으면 그것, 없으면
  env(settings.job_image). 모든 소비자(잡/프리플라이트 매니페스트, 빌드 프로브)가
  이 함수만 통과한다 -- 두 곳이 다르게 읽으면 "릴리스는 됐는데 잡은 옛 이미지"가
  조용히 생긴다.
- Settings 는 frozen 이라 런타임 재읽기 경로가 없다(artifact_base 주석과 동일) --
  재시작 없이 반영되려면 호출 시점마다 DB 를 조회해야 하고, 그 비용(단일 행
  SELECT)은 잡 제출 한 건당 한 번이라 무시할 수준이다.
"""


def resolve_job_image(control_repo, settings) -> str:
    row = control_repo.control_state() or {}
    return row.get("job_image") or settings.job_image
