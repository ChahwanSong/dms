"""컨트롤러 artifact-expiry 루프 -- 결과 파일 자동 만료(2026-10-26 사용자 결정 「파일만 만료하고 행은 남김, N 기본 1500일」).

종단 뒤 DMS_ARTIFACT_RETENTION_DAYS(기본 1500, 0 = 끔)가 지난 잡의 결과 파일(`<base>/<job_id>` -- stdout·stderr·summary·
dscan 리포트)을 지운다. 요청·잡·전이·요약(result_summary·preview_summary)·지문 행은 **남는다** -- 요청 상세는 결과 수치·
단계·사유를 그대로 보이고, 파일 자리에만 「결과 파일 만료됨」이 뜬다(열람 API 410 artifact_expired).

이 루프는 **고르고 맡기기만** 한다(FS·k8s 무접촉): request_purges.expire_artifacts 가 한 트랜잭션에서 잡에
artifacts_expired_at 을 찍고 kind 'expiry' 정리 아웃박스 행을 넣는다. 실제 정리는 요청 삭제와 **같은 길**이다 --
request-purge 루프(request_purger)가 k8s 객체 0 확인 → `<job_id>` 를 .dms-trash 로 떼어냄(artifact_trash) → 전역 purge
파드 하나가 비움(purge_runner). 제어면이 FS 에서 지우는 코드는 여전히 0건이다(ARCHITECTURE §7 규칙 13 --
tests/test_control_plane_static.py 가 이 모듈도 본다). 그래서 purge_runner 가 배선된 컨트롤러에서만 조립된다(controller.
build_loops -- 정리 루프 없이 표시만 찍으면 파일이 영원히 남는다).

규약:
  - 비종단은 절대 고르지 않는다(잡·요청 둘 다 종단). 종단 시각은 updated_at 근사(단조 비감소 -- 늦출 뿐 일찍 발동하지
    않는다). 경계: 정확히 N일 지난 잡은 아직이다(updated_at < now − N일).
  - 멱등·크래시 안전: 표시와 아웃박스 행이 한 트랜잭션이고, 찍힌 잡은 다음 고르기에서 빠진다. 정리 진행은 아웃박스
    (FS·k8s 실상태 판정)가 맡는다.
  - 대기열 상한(BACKLOG_MAX): 만료 행은 요청 삭제 행과 같은 대기열(재시도 시각 순)·같은 purge 파드를 쓴다. 한 번에 수천
    행을 넣으면 사람이 방금 지운 작업의 정리가 그 뒤에 선다 -- 정리 대기 중인 만료 행이 상한 아래일 때만 그 빈자리만큼
    넣는다(틱 간격 INTERVAL_SECONDS, 하루 최대 ~1.4만 건 -- 첫 배포의 오래된 이력도 며칠 안에 따라잡는다).
  - drain 이면 아무것도 하지 않는다(request_purger·stepper 와 같은 규칙). 보존일 0 = 끔(고르지 않는다 -- 이미 맡긴
    행은 정리 루프가 끝낸다).
  - 옛 base(강제 변경 이전에 쓴 잡)의 파일은 지우지 않는다 -- 정리 루프가 artifact_left_at_old_base 로 남긴다(설계 D11).
"""
import sys

from .artifact_base import resolve_artifact_base, strip_scheme
from .db import iso_plus, utc_now_iso
from .repositories.request_purges import EXPIRY_ACTOR

COMPONENT = "artifact-expiry"
# 루프 간격(초). 새 설정 키를 만들지 않는다(artifact-base-check 와 같은 판단 -- 운영자가 따로 튜닝할 값이 아니다).
# 고르기는 부분 인덱스(idx_data_jobs_expiry)로 오래된 미만료 잡만 읽어 싸다.
INTERVAL_SECONDS = 300
# 정리 대기 중인 만료 행 상한(위 docstring). request-purge 는 틱(15초)마다 20행씩 진행하므로 50행 = 요청 삭제가 기다리는
# 최대 ~3틱.
BACKLOG_MAX = 50
DAY_SECONDS = 86400


class ArtifactExpirer:
    def __init__(self, repos, *, settings, clock=None):
        self._repos = repos
        self._purges = repos.request_purges
        self._settings = settings
        self._clock = clock or utc_now_iso

    def run_once(self) -> dict:
        days = self._settings.artifact_retention_days
        if not isinstance(days, int) or isinstance(days, bool) or days <= 0:
            return {"disabled": True}      # 0 = 끔(Settings.from_env 가 음수를 거부한다 -- 변조·직접 생성도 끔으로)
        control = self._repos.control.control_state()
        if control and control["drain"]:
            return {}
        backlog = self._purges.expiry_backlog()
        room = BACKLOG_MAX - backlog
        if room <= 0:
            return {"backlog": backlog, "queued": 0}
        now = self._clock()
        cutoff = iso_plus(now, -days * DAY_SECONDS)
        base = strip_scheme(resolve_artifact_base(self._repos.control, self._settings))
        queued = self._purges.expire_artifacts(cutoff=cutoff, artifact_base=base, limit=room, now=now,
                                               actor=EXPIRY_ACTOR)
        if queued:
            # 요약 1건(request_id=NULL -- 요청마다의 완료 이벤트는 정리 루프가 그 요청에 단다). 진단용.
            jobs = sum(len(q["job_ids"]) for q in queued)
            try:
                self._repos.observability.record_event(
                    component=COMPONENT, severity="info", event_type="artifact_expiry_queued",
                    message=f"{jobs} job(s) of {len(queued)} request(s) passed {days} days -- artifact removal queued",
                    payload={"retention_days": days, "cutoff": cutoff, "requests": len(queued), "jobs": jobs,
                             "artifact_base": base},
                    request_id=None)
            except Exception as exc:       # record_event 는 원래 던지지 않는다 -- 이중 방어(표시는 이미 커밋됐다)
                print(f"artifact-expiry event failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return {"backlog": backlog, "queued": len(queued)}
