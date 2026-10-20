import pytest

from dms.repositories import Repositories
from dms.batch_orchestrator import BatchOrchestrator
from dms.domain import DataJobState

class _S:
    preview_ttl_seconds = 900

def _orch(db): return BatchOrchestrator(Repositories(db), settings=_S())

# 확인 게이트 연산(sync·rm, 2026-10-21 rm 추가 -- domain.confirm_gated)은 같은 상태기계를 탄다: 아래 두 테스트를 연산별로 돈다.
_ITEM = {"sync": {"source_storage": "s1", "source": "a", "destination_storage": "s2", "destination": "b"},
         "rm": {"storage": "s1", "target": "a"}}
_OPTIONS = {"sync": {}, "rm": {"recursive": True}}

def _make_confirmpending(repos, request_id, fp="fp-x", op="sync"):
    # 자식이 preview 완료(ConfirmPending)한 상태를 시뮬레이션
    plan = repos.data_jobs.create_plan(request_id, actor="planner")
    if op == "rm":
        jid = repos.data_jobs.create_job(request_id, plan, operation="rm", priority="mid",
            storage_name="s1", target="a", options={"recursive": True}, tool="drm",
            worker_pool={}, precondition={}, actor="planner")
    else:
        jid = repos.data_jobs.create_job(request_id, plan, operation="sync", priority="mid",
            source_storage="s1", destination_storage="s2", source="a", destination="b",
            options={}, tool="dsync", worker_pool={}, precondition={}, actor="planner")
    repos.data_jobs.set_preview(jid, fingerprint=fp, expires_at="2099-01-01T00:00:00Z",
                                artifact_uri="x")
    repos.data_jobs.set_job_state(jid, DataJobState.CONFIRM_PENDING, actor="stepper")
    return jid

@pytest.mark.parametrize("op", ["sync", "rm"])
def test_sync_previewing_to_previewready(db, op):
    repos = Repositories(db)
    bid = repos.batches.create(operation=op, requester_id="admin", actor="admin",
        max_concurrency=2, options=_OPTIONS[op], items=[_ITEM[op]],
        note=None, status="Previewing")
    _orch(db).run_once()                                  # materialize (1개)
    it = repos.batches.list_items(bid)[0]
    _make_confirmpending(repos, it["request_id"], op=op)  # preview 완료 시뮬
    _orch(db).run_once()                                  # 전원 previewed → PreviewReady
    assert repos.batches.get(bid)["status"]=="PreviewReady"

@pytest.mark.parametrize("op", ["sync", "rm"])
def test_sync_running_confirms_children(db, op):
    # rm 은 2026-10-21 전엔 Running 분기의 scan 가지로 떨어져 확인 도장이 찍힌 자식이 영영 ConfirmPending 이었다(정체).
    repos = Repositories(db)
    bid = repos.batches.create(operation=op, requester_id="admin", actor="admin",
        max_concurrency=2, options=_OPTIONS[op], items=[_ITEM[op]],
        note=None, status="Previewing")
    _orch(db).run_once()
    it = repos.batches.list_items(bid)[0]
    jid = _make_confirmpending(repos, it["request_id"], fp="fp-9", op=op)
    _orch(db).run_once()                                  # PreviewReady(확인 회차 1)
    assert repos.batches.get(bid)["preview_round"] == 1
    # 운영자 배치 확인 = 회차 CAS + 그 순간 ConfirmPending 자식에 확인 도장(confirmed_fingerprint = 미리보기 지문)
    assert repos.batches.confirm(bid, actor="admin", summary={}, expected_round=1) is True
    assert repos.data_jobs.get_job(jid)["confirmed_fingerprint"] == "fp-9"
    _orch(db).run_once()                                  # 도장 찍힌 자식만 실행
    job = repos.data_jobs.get_job(jid)
    assert job["state"]=="Executing" and job["confirmed_fingerprint"]=="fp-9"
