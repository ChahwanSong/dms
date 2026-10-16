"""큐 현황 파이프라인 조립(queue_pipeline, 2026-10-16)의 순수 함수 계약 -- DB·k8s 없이.

라우트 테스트(test_api_metrics 의 metrics_queue_*)가 sqlite 위 전 경로를 보고, 여기는 대조 규칙·시각 강등·정렬처럼
경계값이 많은 조각만 못박는다."""
from dms.db import iso_epoch
from dms.queue_pipeline import (STAGES, age_seconds, build_queue_view, match_podgroup,
                                until_seconds, vcjob_index)

NOW = iso_epoch("2026-10-16T12:00:00Z")


def _job(job_id, refs):
    return {"job_id": job_id, "request_id": f"r-{job_id}", "phase_refs": refs}


def test_age_and_until_never_invent_a_time():
    # 시각이 없거나 깨졌으면 None -- 0 초(= 방금)로 지어내지 않는다. 시계 스큐 음수는 0.
    assert age_seconds(NOW, "2026-10-16T11:59:00Z") == 60
    assert age_seconds(NOW, "2026-10-16T12:00:30Z") == 0
    assert age_seconds(NOW, None) is None
    assert age_seconds(NOW, "2026-10-16 11:59:00") is None
    assert until_seconds(NOW, "2026-10-16T13:00:00Z") == 3600
    assert until_seconds(NOW, "2026-10-16T11:00:00Z") == 0        # 이미 지남 -- 다음 틱에 만료
    assert until_seconds(NOW, 12) is None


def test_vcjob_index_reads_only_vcjob_refs_and_drops_ambiguous_names():
    idx = vcjob_index([
        _job("a", {"preflight": "pod/dms-sync-preflight-a", "preview": "vcjob/dms-sync-preview-a"}),
        _job("b", {"execution": "vcjob/dms-sync-execution-x"}),
        _job("c", {"execution": "vcjob/dms-sync-execution-x"}),   # 같은 이름을 두 잡이 주장(job_id[:12] 충돌·변조)
        _job("d", "not-a-dict"),                                   # 깨진 phase_refs -- 그 잡만 대조 포기
        _job("e", {"preview": "vcjob/"}),
    ])
    assert set(idx) == {"dms-sync-preview-a", "dms-sync-execution-x"}
    assert idx["dms-sync-preview-a"][1] == "preview"
    # 모호한 이름엔 어느 잡도 붙이지 않는다 -- 남의 요청 링크를 보이느니 대조하지 않는다
    assert match_podgroup({"vcjob": "dms-sync-execution-x"}, idx) is None


def test_match_podgroup_owner_is_authoritative_then_name_prefix():
    idx = vcjob_index([_job("a", {"preview": "vcjob/dms-sync-preview-aaaaaaaaaaaa"})])
    hit = match_podgroup({"name": "whatever", "vcjob": "dms-sync-preview-aaaaaaaaaaaa"}, idx)
    assert hit[0]["job_id"] == "a"
    # 소유자 참조가 있으면 그것만 믿는다 -- 이름이 그럴듯해도 소유자가 다르면 대조하지 않는다
    assert match_podgroup({"name": "dms-sync-preview-aaaaaaaaaaaa-1-2", "vcjob": "other"}, idx) is None
    # 소유자 참조가 없을 때만 이름 접두(<vcjob>-<uid>, uid 안의 '-' 도 견딘다)
    by_name = match_podgroup(
        {"name": "dms-sync-preview-aaaaaaaaaaaa-6f1c2e7a-1b2c-4d5e-8f90-123456789abc", "vcjob": None}, idx)
    assert by_name[0]["job_id"] == "a"
    # 옛 Volcano 처럼 PodGroup 이름 = vcjob 이름이어도 대조된다
    assert match_podgroup({"name": "dms-sync-preview-aaaaaaaaaaaa", "vcjob": None}, idx)[0]["job_id"] == "a"
    # 접두가 vcjob 이름의 일부일 뿐이면 대조하지 않는다('-' 경계 단위로만 자른다)
    assert match_podgroup({"name": "dms-sync-preview-aaaa", "vcjob": None}, idx) is None
    assert match_podgroup({"name": None}, idx) is None


def test_build_view_unknown_axes_stay_null_and_empty_axes_are_zero():
    view = build_queue_view(NOW, pods=None, planning=None, jobs=None, batches=None)
    assert view == {"pipeline": dict.fromkeys(STAGES), "waiting": None, "waiting_truncated": None}
    empty = build_queue_view(
        NOW, pods=[], planning={"count": 0, "oldest_at": None, "rows": []},
        jobs={"by_state": {}, "waiting": {}, "confirm_batches": [], "submitted": []},
        batches={"groups": [], "preview_ready": 0})
    assert empty["waiting"] == [] and empty["waiting_truncated"] == []
    assert empty["pipeline"]["confirm"] == {"count": 0, "oldest_seconds": None, "soonest_expiry": None,
                                            "soonest_expiry_seconds": None}
    assert empty["pipeline"]["running"] == {"count": 0, "oldest_seconds": None,
                                            "includes_volcano_wait": False}


def test_waiting_sort_puts_unknown_ages_last_and_zero_is_a_real_age():
    # 나이를 모르는 항목은 맨 뒤 -- 0 초로 접어 앞에 끼우지 않는다. 0 초는 정상값(방금 들어옴)이다.
    pods = [{"name": "pg-unknown", "phase": "Pending", "created_at": None},
            {"name": "pg-zero", "phase": "Pending", "created_at": "2026-10-16T12:00:00Z"},
            {"name": "pg-old", "phase": "Inqueue", "created_at": "2026-10-16T11:00:00Z"},
            {"name": "pg-run", "phase": "Running", "created_at": "2026-10-16T10:00:00Z"}]
    view = build_queue_view(NOW, pods=pods, planning=None, jobs=None, batches=None)
    assert [e["detail"]["podgroup"] for e in view["waiting"]] == ["pg-old", "pg-zero", "pg-unknown"]
    assert [e["wait_seconds"] for e in view["waiting"]] == [3600, 0, None]
    assert view["pipeline"]["volcano_wait"] == {"count": 3, "oldest_seconds": 3600}
    # 잡 축을 못 읽었으니 「실행 중」은 모름(Volcano 만 읽힘)
    assert view["pipeline"]["running"] is None
