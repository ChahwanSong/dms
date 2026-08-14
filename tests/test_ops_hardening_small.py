"""슬라이스 10 태스크 1: 서로 독립적인 다섯 개 국소 수정에 대한 테스트.

1) exec_preflight Pod가 자기 phase 라벨("exec_preflight")을 갖는다(하드코드된
   "preflight"가 아니라) — 컨트롤러 재시작 후 _reconstruct_summary_path가 올바른
   경로를 재구성하려면 라벨이 실제 phase와 일치해야 한다.
2) artifacts.tail_lines(text, n) — read_artifact에서 추출한 공용 tail 헬퍼.
3) batches.list()가 options를 load_json으로 복원한다(get/list_active와 동일하게).
4) batches.reset_failed_items가 트랜잭션으로 감싸진 뒤에도 정상 경로가 회귀 없이 동작한다.

PreviewReady 취소 케이스는 tests/test_api_batch_cancel.py에 추가한다(그 파일이
배치+자식 픽스처를 이미 갖고 있다).
"""
from dms.execution import JobSpec
from dms.execution_manifests import build_preflight_pod
from dms.repositories import Repositories

_VOL = [{"name": "cephfs", "hostPath": {"path": "/cephfs"}, "mountPath": "/cephfs"}]


def _spec(**kw):
    base = dict(job_id="j1", phase="preflight", operation="scan", tool="dscan",
                dryrun=False, identity={"uid": 10001}, paths={"target": "/cephfs/dms/a"},
                options={}, candidates={"primary": ["n1"]}, process_count=8,
                queue="dms-data", priority_class="dms-mid",
                artifact_base="file:///cephfs/dms/artifacts")
    base.update(kw)
    return JobSpec(**base)


def test_exec_preflight_pod_carries_its_own_phase_label():
    # build_preflight_pod 로 exec_preflight spec 을 만들면 라벨이 "exec_preflight" 여야 한다.
    m = build_preflight_pod(_spec(phase="exec_preflight"), job_image="i", namespace="dms",
                            volumes=_VOL, node="dms-w1")
    assert m["metadata"]["labels"]["dms.io/phase"] == "exec_preflight"
    # (기존 preflight 는 그대로 "preflight")
    m2 = build_preflight_pod(_spec(phase="preflight"), job_image="i", namespace="dms",
                             volumes=_VOL, node="dms-w1")
    assert m2["metadata"]["labels"]["dms.io/phase"] == "preflight"


def test_tail_lines_does_not_split_on_carriage_return():
    from dms.api.artifacts import MAX_TAIL_LINES, tail_lines
    text = "a\rb\rc\nsecond"
    assert tail_lines(text, 1) == "second"
    assert tail_lines("x\n" * 10, 3) == "x\nx\nx"
    # 클램프
    assert tail_lines("y\n" * 5, MAX_TAIL_LINES + 100) == ("y\n" * 5).rstrip("\n")


def test_batches_list_hydrates_options(db):
    repos = Repositories(db)
    bid = repos.batches.create(operation="scan", requester_id="admin", actor="admin",
        max_concurrency=2, options={"broken_limit": 5}, note=None,
        items=[{"storage": "s1", "target": "a"}], status="Running")

    rows = repos.batches.list()

    row = next(r for r in rows if r["batch_id"] == bid)
    assert row["options"] == {"broken_limit": 5}  # raw JSON 문자열이 아니라 dict


def test_reset_failed_items_is_transactional(db):
    # 정상 경로가 여전히 동작하는지 (트랜잭션으로 감싼 뒤 회귀가 없음을 확인)
    repos = Repositories(db)
    bid = repos.batches.create(operation="scan", requester_id="admin", actor="admin",
        max_concurrency=2, options={}, note=None,
        items=[{"storage": "s1", "target": "a"}, {"storage": "s1", "target": "b"}],
        status="Completed")
    repos.batches.set_item_status(bid, 0, "Failed", reason_code="x")
    repos.batches.set_item_status(bid, 1, "Rejected", reason_code="y")
    repos.batches.bump_counts(bid, failed=2)

    n = repos.batches.reset_failed_items(bid)

    assert n == 2
    items = repos.batches.list_items(bid)
    assert all(it["status"] == "Queued" for it in items)
    assert all(it["request_id"] is None and it["reason_code"] is None for it in items)
    assert repos.batches.get(bid)["failed_count"] == 0
