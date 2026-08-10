"""스킴 제거 통일(슬라이스 18 설계 §2.2). 전체 치환(str.replace("file://", ""))은
경로 중간의 file:// 까지 지워, 접두사만 벗기는 strip_scheme 계열과 **다른 경로**를
만든다 -- env 가 신뢰 입력이던 동안 잠복했지만 자유 입력을 받기 시작하면 실제
갈라짐이다(저장 시점 정규화가 그런 입력을 거부하더라도, 같은 문자열을 두 방식으로
해석하는 코드를 남겨 두지 않는다). (1) 네 지점의 동작을 접두사-전용으로 행동
수준에서, (2) 전체 치환 코드가 소스에서 사라졌음을 grep 수준으로 고정한다."""
from pathlib import Path

from dms.execution import JobSpec
from dms.execution_manifests import build_volcano_job
from dms.execution_volcano import VolcanoExecutionAdapter

SRC = Path(__file__).resolve().parent.parent / "src" / "dms"

# 경로 중간에 file:// 가 든 base. 접두사만 벗기면 /data/file://x 이고, 전체
# 치환이 남아 있으면 /data/x 가 된다 -- 두 계열이 갈라지는 최소 재현이다.
TRICKY = "file:///data/file://x"


def _spec(phase="execution"):
    return JobSpec(job_id="a" * 32, phase=phase, operation="scan", tool="dscan",
                   dryrun=False, identity={},
                   paths={"target": "/mnt/s/t", "storage": "s1"},
                   options={}, candidates={"primary": ["n1"]}, process_count=1,
                   queue="dms-data", priority_class="dms-mid", artifact_base=TRICKY)


def _adapter(k8s=None):
    return VolcanoExecutionAdapter(
        k8s if k8s is not None else object(), job_image="img", namespace="dms",
        storages_lookup=lambda n: {"mount_path": "/mnt/s"},
        read_text=lambda p: None, artifact_base=TRICKY)


def test_volumes_mount_keeps_mid_path_scheme():
    # execution_volcano._volumes: 전체 치환이면 /data/x 를 마운트해 -- 잡 파드가
    # 실제 base(/data/file://x)와 **다른 디렉터리**를 hostPath 로 받는다.
    paths = [v["hostPath"]["path"] for v in _adapter()._volumes(_spec())]
    assert "/data/file://x" in paths
    assert "/data/x" not in paths


def test_launcher_artifact_dir_keeps_mid_path_scheme():
    # execution_manifests._artifact_dir: 러너가 summary.json 을 쓰는 위치다 --
    # 마운트 계산(_volumes)과 다른 계열로 해석되면 쓰는 곳과 읽는 곳이 갈라진다.
    manifest = build_volcano_job(_spec(), job_image="img", namespace="dms",
                                 volumes=[])
    launcher = manifest["spec"]["tasks"][0]
    env = {e["name"]: e["value"]
           for e in launcher["template"]["spec"]["containers"][0]["env"]}
    assert env["DMS_JR_ARTIFACT_DIR"] == f"/data/file://x/{'a' * 32}/execution"


def test_submit_summary_path_keeps_mid_path_scheme():
    class _K8s:
        def create(self, manifest):
            pass
    a = _adapter(_K8s())
    ref = a.submit(_spec())
    assert a._summary_paths[ref] == f"/data/file://x/{'a' * 32}/execution/summary.json"


def test_reconstruct_summary_path_keeps_mid_path_scheme():
    class _K8s:
        def get(self, kind, name, namespace):
            return {"metadata": {"labels": {"dms.io/job-id": "a" * 32,
                                            "dms.io/phase": "preview"}}}
    a = _adapter(_K8s())
    assert a._reconstruct_summary_path("vcjob/x") == (
        f"/data/file://x/{'a' * 32}/preview/summary.json")


def test_full_replace_scheme_stripping_is_gone():
    # 설계 §5: 전체 치환 4곳(execution_volcano.py / execution_manifests.py)이
    # 제거됐는지 grep 수준으로 고정한다 -- 새로 생기는 것도 여기서 잡힌다.
    for name in ("execution_volcano.py", "execution_manifests.py"):
        source = (SRC / name).read_text()
        assert 'replace("file://"' not in source, name
