"""슬라이스 21 §2.3: PriorityClass dms-build(10, Never).

kubelet 축출 순서는 ①사용량이 requests 를 초과하는가 ②pod priority ③초과량이다.
BestEffort 데이터 잡은 요청 0 이라 항상 ①그룹이고, 빌드도 memory requests 128Mi
(§2.2)를 실압박에선 초과해 같은 그룹 -- ②priority 가 방향을 가른다. 값 10 <
dms-low 50 이므로 "빌드가 항상 먼저 죽는다"가 명문화되고, Never 라 빌드는 아무도
선점하지 않는다.

pyyaml 이 venv 에 없어(새 pip 의존성 금지) 텍스트 수준으로 고정한다 --
test_manifest_tags.py 가 deploy/k8s 를 텍스트로 읽는 것과 같은 관례다."""
from pathlib import Path

YAML = (Path(__file__).resolve().parent.parent / "deploy" / "k8s"
        / "05-volcano-queue-priorityclass.yaml")


def _dms_build_block():
    docs = YAML.read_text().split("\n---\n")
    matches = [d for d in docs if "name: dms-build" in d]
    assert len(matches) == 1, "dms-build PriorityClass 문서가 정확히 하나여야 한다"
    return matches[0]


def test_dms_build_priorityclass_is_below_every_data_job_class_and_never_preempts():
    block = _dms_build_block()
    assert "kind: PriorityClass" in block
    assert "value: 10" in block                  # < dms-low 50 -- 축출 1순위
    assert "preemptionPolicy: Never" in block    # 빌드는 아무도 선점하지 않는다
    assert "globalDefault: false" in block       # 클러스터 기본값 오염 금지


def test_existing_data_job_classes_are_untouched():
    # 잡 우선순위 3계급은 이 슬라이스의 대상이 아니다(§2.3: 데이터 잡은 손대지
    # 않는다) -- 실수로 지워지거나 값이 바뀌면 잡 제출이 admission 거절된다.
    text = YAML.read_text()
    for needle in ("name: dms-low", "value: 50", "name: dms-mid", "value: 100",
                   "name: dms-high", "value: 200"):
        assert needle in text, needle
