"""사용량 분석 scan 리포트 요약 캐시(2026-10-02).

성공 scan 잡 1건의 dscan-report.json 을 모양 투영한 결과(routes_usage._project_report)를 잡 단위로 둔다.
- 왜 캐시인가: 목록이 타깃마다 최신 스캔의 실 사용량·파일 수·hot 비율을 보이고 CSV 내보내기가 전 타깃을 싣게
  되면서, 요청마다 아티팩트를 수백 건 열면 화면 하나가 공유 스토리지 I/O 를 붙잡는다. 리포트는 **성공 종단 잡의
  불변 산출물**이라 한 번 투영한 값이 영원히 맞다 -- 무효화가 필요 없는 캐시다.
- 무엇을 두는가: 투영 **뒤** 값(숫자·구간 라벨)만. 원본 리포트의 경로 문자열은 투영이 이미 버렸다.
- 못 읽은 리포트(부재·크기 초과·깨짐)는 두지 않는다 -- 일시 I/O 오류를 영구 "모름"으로 굳히지 않게(다음 조회가
  다시 시도한다). 부재는 open 실패라 싸다.
- 동시 채우기: 두 요청이 같은 잡을 동시에 놓쳐도 INSERT ... ON CONFLICT DO NOTHING 이라 먼저 쓴 값이 남는다(같은
  리포트의 같은 투영이라 어느 쪽이든 같은 값).
"""
from ..db import Database, dump_json, load_json, utc_now_iso

# IN 목록 한 번의 자리표시자 수 상한 -- sqlite 변수 상한(32766)·pg 문 길이와 무관한 넉넉한 묶음.
_CHUNK = 500


class ScanDigestsRepository:
    def __init__(self, db: Database):
        self._db = db

    def get_many(self, job_ids) -> dict:
        """job_id -> digest(dict). 없는 잡은 결과에 없다."""
        ids = list(dict.fromkeys(job_ids))
        out: dict = {}
        for i in range(0, len(ids), _CHUNK):
            chunk = ids[i:i + _CHUNK]
            names = {f"j{n}": jid for n, jid in enumerate(chunk)}
            rows = self._db.query(
                f"SELECT job_id, digest FROM scan_report_digests WHERE job_id IN "
                f"({', '.join(':' + k for k in names)})", names)
            for r in rows:
                digest = load_json(r["digest"])
                if isinstance(digest, dict):
                    out[r["job_id"]] = digest
        return out

    def put(self, job_id: str, digest: dict) -> None:
        self._db.execute(
            """INSERT INTO scan_report_digests (job_id, digest, created_at)
               VALUES (:j, :d, :now) ON CONFLICT (job_id) DO NOTHING""",
            {"j": job_id, "d": dump_json(digest), "now": utc_now_iso()})
