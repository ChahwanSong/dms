"""포탈 단계·상태 설명(frontend/src/lib/stateHelp.json, 2026-10-12)의 키가 서버 상태 목록과 같은지 -- 새 상태를 서버에 더하고
설명을 빠뜨리면(툴팁이 조용히 사라진다) 또는 없어진 상태의 설명이 남으면 여기서 빨개진다."""
import json
from pathlib import Path

from dms.api.routes_batches import _TERMINAL_BATCH
from dms.batch_orchestrator import _BATCH_DRIVEN
from dms.domain import DataJobState, RequestState

HELP = json.loads((Path(__file__).resolve().parent.parent / "frontend" / "src" / "lib" / "stateHelp.json").read_text())


def test_request_and_job_help_cover_every_server_state():
    assert set(HELP["request"]) == {s.value for s in RequestState}
    assert set(HELP["job"]) == {s.value for s in DataJobState}
    assert set(HELP["job_batch_child"]) <= set(HELP["job"])


def test_batch_help_covers_every_batch_state():
    # Cancelling(2026-10-16): 취소 라우트가 쓰는 비종단 상태 -- orchestrator 가 굴리지 않고 종단도 아니라 위 두 목록 밖이다.
    assert set(HELP["batch"]) == set(_BATCH_DRIVEN) | set(_TERMINAL_BATCH) | {"Cancelling"}


def test_stage_help_keys():
    assert set(HELP["stage"]) == {"pre_scan", "pre_previewed", "gate", "gate_batch_child", "exec_scan", "exec_previewed"}
