"""purge_runner 의 k8s I/O -- ref 검증(DB 신뢰 경계), 라벨 스윕(Pod·vcjob·launcher), purge 파드 수명. 가짜 k8s
(tests/fake_purge_k8s.py)로 본다. KubernetesClient 의 새 필드·메서드는 _core/_custom 을 가짜로 끼워 본다."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from dms.execution_volcano import KubernetesClient
from dms.purge_runner import (PURGE_POD_DEADLINE_SECONDS, PURGE_POD_PENDING_MAX_SECONDS, PURGE_POD_STUCK_SECONDS,
                              PurgeError, PurgeRunner, StubPurgeRunner, build_purge_pod, parse_ref)
from fake_purge_k8s import FakeK8s

JID = "0123456789ab" + "c" * 20
J12 = JID[:12]
SIB = J12 + "e" * 20          # 앞 12자가 JID 와 같은 다른 잡(이름으로는 구분되지 않는다)
OTHER = "fedcba987654" + "d" * 20
NOW = datetime(2026, 10, 8, 12, 0, 0, tzinfo=timezone.utc).timestamp()


def _runner(k8s, image="reg/job:1"):
    return PurgeRunner(k8s, namespace="dms", job_image=image, clock=lambda: NOW)


# ---- ref 검증 ----

@pytest.mark.parametrize("ref,ok", [
    (f"pod/dms-preflight-{J12}-preflight-dms-w1", True),
    (f"pod/dms-preflight-{J12}-exec-preflight-dms-w1.example.com", True),
    (f"pods/dms-preflight-{J12}-preflight-src-a,dms-preflight-{J12}-preflight-dst-b", True),
    (f"vcjob/dms-scan-execution-{J12}", True),
    (f"vcjob/dms-sync-preview-{J12}", True),
    (f"vcjob/dms-rm-execution-{J12}", True),
    # 제어면·남의 객체·다른 잡
    ("pod/dms-api-7d9f8b7c6-abcde", False),
    ("pod/dms-controller-0", False),
    (f"pod/dms-preflight-{OTHER[:12]}-preflight-dms-w1", False),
    (f"pods/dms-preflight-{J12}-preflight-src-a,dms-api-x", False),
    (f"vcjob/dms-scan-execution-{OTHER[:12]}", False),
    (f"vcjob/dms-scan-preflight-{J12}", False),      # preflight 는 vcjob 이 아니다
    (f"vcjob/dms-evil-execution-{J12}", False),
    (f"vcjob/dms-scan-execution-{J12}-x", False),
    (f"job/dms-scan-execution-{J12}", False),
    (f"buildpod/dms-build-{J12}", False),
    (f"stub-execution-{JID}", False),
    (f"pod/dms-preflight-{J12}-Preflight", False),    # DNS-1123 아님
    (f"pod/dms-preflight-{J12}-x/../y", False),
    ("pod/", False), ("", False), (None, False), (7, False),
])
def test_ref_must_be_dms_named_for_its_own_job(ref, ok):
    assert (parse_ref(ref, JID) is not None) is ok
    assert PurgeRunner.ref_belongs_to_job(ref, JID) is ok


def test_parse_ref_rejects_a_bad_job_id():
    assert parse_ref(f"vcjob/dms-scan-execution-{J12}", "x" * 32) is None
    assert parse_ref(f"vcjob/dms-scan-execution-{J12}", None) is None


# ---- sweep ----

def _seed_job(k8s, jid=JID):
    j12 = jid[:12]
    vc = f"dms-scan-execution-{j12}"
    k8s.add_pod(f"dms-preflight-{j12}-preflight-dms-w1", {"dms.io/job-id": jid}, phase="Succeeded")
    k8s.add_vcjob(vc, {"dms.io/job-id": jid})
    k8s.add_pod(f"{vc}-worker-0", {"dms.io/job-id": jid, "volcano.sh/job-name": vc})
    k8s.add_pod(f"{vc}-launcher-0", {"volcano.sh/job-name": vc}, phase="Failed")   # launcher 엔 dms 라벨이 없다
    return vc


def test_sweep_deletes_labeled_pods_vcjobs_and_launchers_only():
    k8s = FakeK8s()
    vc = _seed_job(k8s)
    _seed_job(k8s, OTHER)
    k8s.add_pod("dms-api-7d9f8b7c6-abcde", {"app": "dms-api"})
    refs = [f"pod/dms-preflight-{J12}-preflight-dms-w1", f"vcjob/{vc}"]
    r = _runner(k8s).sweep(JID, refs)
    assert r == {"deleted": 4, "remaining": 0}
    assert set(k8s.pods) == {f"dms-preflight-{OTHER[:12]}-preflight-dms-w1",
                             f"dms-scan-execution-{OTHER[:12]}-worker-0",
                             f"dms-scan-execution-{OTHER[:12]}-launcher-0", "dms-api-7d9f8b7c6-abcde"}
    assert set(k8s.vcjobs) == {f"dms-scan-execution-{OTHER[:12]}"}


def test_terminating_objects_count_as_remaining_and_are_not_recounted():
    k8s = FakeK8s()
    vc = _seed_job(k8s)
    k8s.linger.add(("Pod", f"{vc}-launcher-0"))
    runner = _runner(k8s)
    assert runner.sweep(JID, [f"vcjob/{vc}"]) == {"deleted": 4, "remaining": 1}
    assert k8s.pods[f"{vc}-launcher-0"]["deleting"] is True
    before = len(k8s.deleted)
    assert runner.sweep(JID, [f"vcjob/{vc}"]) == {"deleted": 0, "remaining": 1}
    # Terminating 은 다시 지우지 않는다(ref 의 vcjob 은 이미 없어 404 삼킴 -- 이름으로만 한 번 더 부른다)
    assert ("Pod", f"{vc}-launcher-0") not in k8s.deleted[before:]
    k8s.release()
    assert runner.sweep(JID, [f"vcjob/{vc}"]) == {"deleted": 0, "remaining": 0}


def test_vcjob_named_by_its_ref_is_not_deleted_without_this_jobs_label():
    # DMS vcjob 은 첫 매니페스트 빌더부터 dms.io/job-id 를 단다 -- 이름(ref)만 맞고 라벨이 없는 vcjob 은 우리 것이라는
    # 근거가 없다(이름은 job_id[:12] 만 담는다). 그 vcjob 과 launcher 는 지우지도 세지도 않는다.
    k8s = FakeK8s()
    vc = f"dms-sync-execution-{J12}"
    k8s.add_vcjob(vc, {})
    k8s.add_pod(f"{vc}-launcher-0", {"volcano.sh/job-name": vc})
    assert _runner(k8s).sweep(JID, [f"vcjob/{vc}"]) == {"deleted": 0, "remaining": 0}
    assert vc in k8s.vcjobs and f"{vc}-launcher-0" in k8s.pods and k8s.deleted == []


def _seed_live_sibling(k8s, op="sync"):
    """접두 12자가 JID 와 같은 **살아 있는 다른 잡**(SIB)의 객체 -- 이름만으로는 JID 의 것과 구분되지 않는다."""
    vc = f"dms-{op}-execution-{J12}"
    k8s.add_pod(f"dms-preflight-{J12}-exec-preflight-dms-w1", {"dms.io/job-id": SIB}, phase="Succeeded")
    k8s.add_vcjob(vc, {"dms.io/job-id": SIB})
    k8s.add_pod(f"{vc}-launcher-0", {"volcano.sh/job-name": vc})
    k8s.add_pod(f"{vc}-worker-0", {"dms.io/job-id": SIB, "volcano.sh/job-name": vc})
    return vc


def test_sibling_prefix_live_jobs_objects_are_not_ours():
    # 2026-10-09 검증 지적: 변조·복원된 아웃박스 행의 job_id 가 살아 있는 잡과 앞 12자만 같으면, 결정적 이름 후보와
    # ref 이름으로 그 잡의 launcher·worker·vcjob·preflight 를 지웠다(실행 중 rm/sync 를 중간에 죽인다).
    k8s = FakeK8s()
    vc = _seed_live_sibling(k8s)
    before = set(k8s.pods), set(k8s.vcjobs)
    refs = [f"vcjob/{vc}", f"pod/dms-preflight-{J12}-exec-preflight-dms-w1"]
    assert _runner(k8s).sweep(JID, refs) == {"deleted": 0, "remaining": 0}
    assert (set(k8s.pods), set(k8s.vcjobs)) == before and k8s.deleted == []


def test_own_objects_are_reaped_while_a_sibling_prefix_jobs_are_left():
    k8s = FakeK8s()
    mine = _seed_job(k8s)                                   # dms-scan-execution-<J12>
    theirs = _seed_live_sibling(k8s, op="rm")               # dms-rm-execution-<J12>(같은 접두, 남의 잡)
    r = _runner(k8s).sweep(JID, [f"vcjob/{mine}", f"vcjob/{theirs}"])
    assert r == {"deleted": 4, "remaining": 0}
    assert set(k8s.vcjobs) == {theirs}
    assert set(k8s.pods) == {f"dms-preflight-{J12}-exec-preflight-dms-w1", f"{theirs}-launcher-0",
                             f"{theirs}-worker-0"}


def test_launcher_whose_owner_uid_is_not_the_listed_vcjob_is_judged_by_a_fresh_get():
    # 라벨 나열과 파드 조회 사이에 우리 vcjob 이 지워지고 같은 이름으로 남의 vcjob 이 생겼다(접두가 같은 잡) --
    # 파드의 ownerReference uid 가 나열한 vcjob 과 다르면 다시 조회해 그 주인의 라벨로 판정한다.
    class Racing(FakeK8s):
        swapped = False

        def list_pod_briefs(self, namespace, label_selector):
            if label_selector.startswith("volcano.sh/job-name") and not self.swapped:
                self.swapped = True
                vc = f"dms-scan-execution-{J12}"
                del self.vcjobs[vc]
                self.pods.pop(f"{vc}-launcher-0", None)
                self.add_vcjob(vc, {"dms.io/job-id": SIB})
                self.add_pod(f"{vc}-launcher-0", {"volcano.sh/job-name": vc})     # 새 주인(SIB vcjob)의 uid
            return super().list_pod_briefs(namespace, label_selector)

    k8s = Racing()
    vc = f"dms-scan-execution-{J12}"
    k8s.add_vcjob(vc, {"dms.io/job-id": JID})
    k8s.add_pod(f"{vc}-launcher-0", {"volcano.sh/job-name": vc})
    objs = _runner(k8s)._live(JID, set())
    assert ("Pod", f"{vc}-launcher-0") not in objs          # 남의 vcjob 의 launcher 다
    assert ("Job", vc) in objs                              # 나열 시점의 우리 vcjob(지울 때는 404 삼킴)


def test_orphan_launcher_without_any_owner_reference_is_judged_by_its_vcjob_label():
    k8s = FakeK8s()
    vc = f"dms-scan-execution-{J12}"
    k8s.add_vcjob(vc, {"dms.io/job-id": SIB})
    k8s.add_pod(f"{vc}-launcher-0", {"volcano.sh/job-name": vc}, owner=None)
    assert _runner(k8s).sweep(JID, []) == {"deleted": 0, "remaining": 0}
    k8s2 = FakeK8s()
    k8s2.add_pod(f"{vc}-launcher-0", {"volcano.sh/job-name": vc}, owner=None)     # 주인 vcjob 이 없다 = 고아
    assert _runner(k8s2).sweep(JID, []) == {"deleted": 1, "remaining": 0}


def test_orphan_launcher_of_an_unrecorded_vcjob_is_found_by_its_deterministic_name():
    # vcjob 은 이미 없고(앞 틱 삭제·TTL) phase_refs 에도 없었다(제출 직후 크래시) -- GC 중인 launcher 는 dms 라벨이
    # 없어 결정적 이름으로만 닿는다. 다른 잡의 launcher 는 건드리지 않는다.
    k8s = FakeK8s()
    vc = f"dms-rm-preview-{J12}"
    k8s.add_pod(f"{vc}-launcher-0", {"volcano.sh/job-name": vc})
    k8s.add_pod(f"dms-rm-preview-{OTHER[:12]}-launcher-0", {"volcano.sh/job-name": f"dms-rm-preview-{OTHER[:12]}"})
    assert _runner(k8s).sweep(JID, []) == {"deleted": 1, "remaining": 0}
    assert list(k8s.pods) == [f"dms-rm-preview-{OTHER[:12]}-launcher-0"]


def test_ref_pod_without_this_jobs_label_is_not_deleted_by_name():
    # preflight Pod 는 처음부터 dms.io/job-id 를 단다 -- 이름(ref)만 맞는 파드는 우리 것이라는 근거가 없다.
    k8s = FakeK8s()
    name = f"dms-preflight-{J12}-preflight-dms-w1"
    k8s.add_pod(name, {})
    assert _runner(k8s).sweep(JID, [f"pod/{name}"]) == {"deleted": 0, "remaining": 0}
    assert name in k8s.pods and k8s.deleted == []


def test_sweep_revalidates_refs():
    k8s = FakeK8s()
    k8s.add_pod("dms-api-7d9f8b7c6-abcde", {"app": "dms-api"})
    _runner(k8s).sweep(JID, ["pod/dms-api-7d9f8b7c6-abcde"])
    assert "dms-api-7d9f8b7c6-abcde" in k8s.pods and k8s.deleted == []


@pytest.mark.parametrize("method", ["list_pod_briefs", "list_vcjob_briefs", "delete", "get"])
def test_k8s_errors_become_purge_k8s_failed(method):
    k8s = FakeK8s()
    vc = _seed_job(k8s)
    k8s.fail[method] = RuntimeError("apiserver down")
    with pytest.raises(PurgeError) as e:
        _runner(k8s).sweep(JID, [f"vcjob/{vc}", f"pod/dms-preflight-{J12}-x"])
    assert e.value.reason_code == "purge_k8s_failed"


def test_sweep_rejects_an_invalid_job_id_without_listing():
    k8s = FakeK8s()
    with pytest.raises(PurgeError):
        _runner(k8s).sweep("dms.io/x", [])
    assert k8s.calls == []


# ---- purge 파드 ----

def _purge_pod(k8s, names, *, phase, created="2026-10-08T11:59:00Z", deleting=False, waiting=None):
    m = build_purge_pod(names=names, base="/b", nodes=["dms-w1"], image="i", namespace="dms")
    k8s.add_pod(m["metadata"]["name"], m["metadata"]["labels"], phase=phase, spec=m["spec"], created=created,
                deleting=deleting, waiting_reason=waiting)
    return m["metadata"]["name"]


def test_list_purge_pods_states():
    k8s = FakeK8s()
    a, b, c, d = ("1" * 32,), ("2" * 32,), ("3" * 32,), ("4" * 32,)
    running = _purge_pod(k8s, a, phase="Running")
    done = _purge_pod(k8s, b, phase="Succeeded")
    failed = _purge_pod(k8s, c, phase="Failed")
    term = _purge_pod(k8s, d, phase="Running", deleting=True)
    k8s.add_pod("someone-else", {"dms.io/purge": "1"})       # 라벨만 같은 남의 파드 -- 관리 밖
    got = {p["name"]: p for p in _runner(k8s).list_purge_pods()}
    assert set(got) == {running, done, failed, term}
    # 실행 중·종료 중도 조회한다(나이 -- 끝나지 않는 파드를 보려면, 2026-10-09). 갓 만든 파드는 stuck 이 아니다.
    assert got[running] == {"name": running, "phase": "Running", "deleting": False, "names": list(a),
                            "age_seconds": 60.0, "failed": False, "stuck": False}
    assert got[done] == {"name": done, "phase": "Succeeded", "deleting": False, "names": list(b),
                         "age_seconds": 60.0, "failed": False, "stuck": False}
    assert got[failed]["failed"] is True and got[failed]["names"] == list(c)
    assert got[term]["deleting"] is True and got[term]["stuck"] is False


def _ts(age):
    return datetime.fromtimestamp(NOW - age, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_unfinished_purge_pod_past_deadline_and_grace_is_stuck():
    # 죽은 노드의 Running 파드·taint eviction 뒤 Terminating 에 갇힌 파드·D 상태 rm(kubelet 이 데드라인을 집행 못 함)은
    # 영영 끝나지 않는다 -- 생성 후 데드라인 + 유예가 지나면 stuck(호출자가 막힌 행을 지연으로 표면화, 2026-10-09).
    assert PURGE_POD_STUCK_SECONDS > PURGE_POD_DEADLINE_SECONDS
    k8s = FakeK8s()
    old = PURGE_POD_STUCK_SECONDS + 1
    running = _purge_pod(k8s, ("1" * 32,), phase="Running", created=_ts(old))
    term = _purge_pod(k8s, ("2" * 32,), phase="Succeeded", deleting=True, created=_ts(old))
    unknown_phase = _purge_pod(k8s, ("3" * 32,), phase="", created=_ts(old))
    young = _purge_pod(k8s, ("4" * 32,), phase="Running", created=_ts(PURGE_POD_STUCK_SECONDS - 1))
    no_age = _purge_pod(k8s, ("5" * 32,), phase="Running", created=None)
    done = _purge_pod(k8s, ("6" * 32,), phase="Succeeded", created=_ts(old))
    pending = _purge_pod(k8s, ("7" * 32,), phase="Pending", created=_ts(old))
    got = {p["name"]: p for p in _runner(k8s).list_purge_pods()}
    assert got[running]["stuck"] is True and got[running]["failed"] is False
    assert got[running]["age_seconds"] == float(old)
    assert got[term]["stuck"] is True                       # 종료 중에 갇힌 파드도
    assert got[unknown_phase]["stuck"] is True
    assert got[young]["stuck"] is False
    assert got[no_age]["stuck"] is False                    # 나이 모름 ≠ 초과 -- 단정하지 않는다
    assert got[done]["stuck"] is False                      # 끝난 파드는 수거 대상이지 stuck 이 아니다
    assert got[pending]["failed"] is True and got[pending]["stuck"] is False   # Pending 상한이 먼저 실패로 접는다


def test_pending_purge_pod_fails_on_fatal_wait_or_age():
    k8s = FakeK8s()
    young = _purge_pod(k8s, ("1" * 32,), phase="Pending")
    pull = _purge_pod(k8s, ("2" * 32,), phase="Pending", waiting="ImagePullBackOff")
    old_ts = datetime.fromtimestamp(NOW - PURGE_POD_PENDING_MAX_SECONDS - 1, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    old = _purge_pod(k8s, ("3" * 32,), phase="Pending", created=old_ts)
    unknown = _purge_pod(k8s, ("4" * 32,), phase="Pending", created=None)
    got = {p["name"]: p for p in _runner(k8s).list_purge_pods()}
    assert got[young]["failed"] is False
    assert got[pull]["failed"] is True
    assert got[old]["failed"] is True
    assert got[unknown]["failed"] is False and got[unknown]["age_seconds"] is None   # 모름 ≠ 0 -- 기다린다


def test_list_purge_pods_error_is_purge_k8s_failed():
    k8s = FakeK8s()
    k8s.fail["list_pod_briefs"] = RuntimeError("x")
    with pytest.raises(PurgeError) as e:
        _runner(k8s).list_purge_pods()
    assert e.value.reason_code == "purge_k8s_failed"


def test_create_purge_pod_is_idempotent_and_uses_the_job_image_at_call_time():
    k8s = FakeK8s()
    image = {"v": "reg/job:1"}
    runner = PurgeRunner(k8s, namespace="dms", job_image=lambda: image["v"])
    name = runner.create_purge_pod(base="/b", names=["1" * 32], nodes=["dms-w1"])
    image["v"] = "reg/job:2"
    assert runner.create_purge_pod(base="/b", names=["1" * 32], nodes=["dms-w1"]) == name   # AlreadyExists
    assert len(k8s.created) == 1 and k8s.created[0]["spec"]["containers"][0]["image"] == "reg/job:1"
    runner.create_purge_pod(base="/b", names=["2" * 32], nodes=["dms-w1"])
    assert k8s.created[1]["spec"]["containers"][0]["image"] == "reg/job:2"


def test_create_purge_pod_failures_are_purge_pod_failed():
    k8s = FakeK8s()
    k8s.fail["create"] = RuntimeError("forbidden")
    with pytest.raises(PurgeError) as e:
        _runner(k8s).create_purge_pod(base="/b", names=["1" * 32], nodes=["dms-w1"])
    assert e.value.reason_code == "purge_pod_failed"
    with pytest.raises(PurgeError) as e:
        _runner(FakeK8s()).create_purge_pod(base="/b", names=[".."], nodes=["dms-w1"])
    assert e.value.reason_code == "purge_pod_failed"


def test_delete_purge_pod_only_touches_purge_pods():
    k8s = FakeK8s()
    k8s.add_pod("dms-api-x", {"app": "dms-api"})
    with pytest.raises(PurgeError):
        _runner(k8s).delete_purge_pod("dms-api-x")
    assert "dms-api-x" in k8s.pods
    name = _purge_pod(k8s, ("1" * 32,), phase="Succeeded")
    _runner(k8s).delete_purge_pod(name)
    assert name not in k8s.pods


def test_created_timestamp_from_the_real_client_shape():
    # 실 클라이언트 get 은 to_dict()(snake_case, datetime) -- 테스트 가짜(camelCase 문자열)와 둘 다 읽는다.
    class K(FakeK8s):
        def get(self, kind, name, namespace):
            obj = super().get(kind, name, namespace)
            obj["metadata"] = {"name": name, "creation_timestamp":
                               datetime.fromtimestamp(NOW - 30, timezone.utc)}
            return obj
    k8s = K()
    name = _purge_pod(k8s, ("1" * 32,), phase="Succeeded")
    (p,) = _runner(k8s).list_purge_pods()
    assert p["name"] == name and p["age_seconds"] == 30.0 and p["names"] == ["1" * 32]


# ---- 스텁 ----

def test_stub_runner_accepts_only_stub_refs_and_has_nothing_to_sweep():
    s = StubPurgeRunner()
    assert s.ref_belongs_to_job(f"stub-execution-{JID}", JID)
    assert s.ref_belongs_to_job(f"stub-exec_preflight-{JID}", JID)
    assert not s.ref_belongs_to_job(f"stub-execution-{OTHER}", JID)
    assert not s.ref_belongs_to_job(f"vcjob/dms-scan-execution-{J12}", JID)
    assert s.sweep(JID, [f"stub-execution-{JID}"]) == {"deleted": 0, "remaining": 0}
    name = s.create_purge_pod(base="/b", names=["1" * 32], nodes=["n"])
    assert [p["name"] for p in s.list_purge_pods()] == [name]
    assert s.list_purge_pods()[0]["phase"] == "Succeeded"
    s.delete_purge_pod(name)
    assert s.list_purge_pods() == []
    with pytest.raises(PurgeError):
        s.create_purge_pod(base="/b", names=["bad"], nodes=["n"])


# ---- KubernetesClient 추가분(실 클라이언트 모양) ----

def test_kubernetes_client_pod_briefs_report_deleting_and_vcjob_briefs():
    client = KubernetesClient("dms")

    def pod(name, deleting):
        return SimpleNamespace(
            metadata=SimpleNamespace(name=name, deletion_timestamp=(datetime.now(timezone.utc) if deleting
                                                                    else None)),
            spec=SimpleNamespace(node_name="dms-w1", containers=[SimpleNamespace(name="c", image="i")]),
            status=SimpleNamespace(phase="Running", container_statuses=[]))

    seen = {}

    class Core:
        def list_namespaced_pod(self, ns, label_selector, _request_timeout):
            seen["pods"] = (ns, label_selector, _request_timeout)
            return SimpleNamespace(items=[pod("a", False), pod("b", True)])

    class Custom:
        def list_namespaced_custom_object(self, group, version, ns, plural, label_selector, _request_timeout):
            seen["vcjobs"] = (group, version, ns, plural, label_selector)
            return {"items": [{"metadata": {"name": "v1", "uid": "u-1"}},
                              {"metadata": {"name": "v2", "deletionTimestamp": "2026-10-08T00:00:00Z"}}]}

    client._core, client._custom = Core(), Custom()
    briefs = client.list_pod_briefs("dms", "dms.io/job-id=x")
    assert [(b["name"], b["deleting"]) for b in briefs] == [("a", False), ("b", True)]
    assert client.list_vcjob_briefs("dms", "dms.io/job-id=x") == [
        {"name": "v1", "deleting": False, "uid": "u-1"}, {"name": "v2", "deleting": True, "uid": None}]
    assert seen["vcjobs"] == ("batch.volcano.sh", "v1alpha1", "dms", "jobs", "dms.io/job-id=x")
    assert seen["pods"][2] is not None                       # 요청 상한(무제한 블록 금지)
