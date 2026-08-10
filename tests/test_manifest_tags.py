"""manifest_tags 단위 테스트. 실제 deploy/k8s/*.yaml 을 그대로 파싱한다(설계 §5) --
픽스처를 합성하면 "실물 매니페스트를 읽는다"는 보증이 사라진다. 태그 숫자는 배포마다
바뀌므로 리포 접두사만 단언한다 -- 태그 핀 자체는 배포 절차(플랜 이후 절)의 몫이다."""
from pathlib import Path

from dms.manifest_tags import (container_image, manifest_images, manifest_job_image,
                               workload_doc)

REPO_K8S = Path(__file__).resolve().parent.parent / "deploy" / "k8s"


def test_manifest_images_parses_all_four_workloads():
    images = manifest_images(REPO_K8S)
    assert set(images) == {"dms-api", "dms-controller", "dms-agent", "dms-migrate"}
    # api/controller/migrate 는 같은 dms 이미지 계보다(COMPONENTS.repository 실측)
    for comp in ("dms-api", "dms-controller", "dms-migrate"):
        assert images[comp].startswith("pkg-01:5000/dms:"), images
    assert images["dms-agent"].startswith("pkg-01:5000/dms-agent:")


def test_manifest_images_default_root_resolves_in_checkout():
    # 개발 체크아웃에서는 __file__ 기준 후보가 저장소 deploy/k8s 를 찾아야 한다 --
    # 이 배선이 끊기면 이미지 안(/app 후보)에서만 증상이 드러나 테스트가 못 잡는다.
    assert manifest_images() == manifest_images(REPO_K8S)


def test_manifest_job_image_reads_quoted_configmap_value():
    image = manifest_job_image(REPO_K8S)
    assert image.startswith("pkg-01:5000/dms-mpifileutils:")
    assert '"' not in image                       # 20-config.yaml 값의 따옴표는 벗긴다


def test_missing_root_fails_soft_to_all_none(tmp_path):
    # 동봉이 없는 이미지(현행 d25처럼 COPY 이전 빌드)에서도 라우트가 죽으면 안 된다 --
    # 전량 None 이면 프론트는 배지를 내지 않는다(설계 §4: 추측하지 않는다).
    gone = tmp_path / "nope"
    assert set(manifest_images(gone).values()) == {None}
    assert manifest_job_image(gone) is None


def test_unparseable_file_fails_soft(tmp_path):
    # metadata 없는 깨진 문서 + 나머지 파일 부재 -- 항목별 None 강등, 예외 없음
    (tmp_path / "40-api.yaml").write_text("kind: Deployment\nspec: {}\n")
    images = manifest_images(tmp_path)
    assert set(images.values()) == {None}
    assert manifest_job_image(tmp_path) is None


def test_binary_manifest_fails_soft(tmp_path):
    # 비-UTF-8 바이트는 read_text 에서 UnicodeDecodeError 를 낸다. 이건 OSError 가
    # 아니라 ValueError 계열이라 파일 부재와 같은 except 로는 안 잡힌다 -- 조회 계층이
    # 전면 fail-soft(설계 §4)라는 계약이 여기서 조용히 깨진다. 잘린 다운로드나
    # 잘못 마운트된 파일 하나가 배지가 아니라 라우트 전체를 죽이면 안 된다.
    (tmp_path / "40-api.yaml").write_bytes(b"\xff\xfe\x00\x01\x02\x03")
    images = manifest_images(tmp_path)
    assert set(images.values()) == {None}


def test_binary_configmap_fails_soft(tmp_path):
    # manifest_job_image 도 같은 read_text 경로를 탄다 -- 따로 못박는다.
    (tmp_path / "20-config.yaml").write_bytes(b"\xff\xfe\x00\x01\x02\x03")
    assert manifest_job_image(tmp_path) is None


# 아래 두 테스트만 합성 픽스처를 쓴다. 실물 매니페스트는 파드마다 컨테이너가 하나뿐이라
# "이름으로 스코핑한다"와 "블록의 첫 image: 를 집는다"를 구분하지 못한다 -- 즉 이름 추적을
# 통째로 걷어내는 뮤테이션에도 실물 기반 테스트는 전부 초록이다. Task 4 가 40/41 에
# initContainers 를 넣을 예정이므로 그 전에 스코핑을 고정해둔다.
_MULTI_CONTAINER = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: dms-api
  namespace: dms
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: dms-api
  template:
    metadata:
      labels:
        app.kubernetes.io/name: dms-api
    spec:
      initContainers:
        - name: wait-migrate
          image: pkg-01:5000/dms-INIT:i1
      containers:
        - name: sidecar
          image: pkg-01:5000/dms-SIDECAR:s1
        - name: api
          image: pkg-01:5000/dms:d99
"""


def test_container_image_scopes_by_name_not_by_position(tmp_path):
    # sidecar 가 api 보다 **먼저** 온다 -- 첫 image: 를 집는 구현이라면 여기서 빨간불이다.
    (tmp_path / "40-api.yaml").write_text(_MULTI_CONTAINER)
    assert manifest_images(tmp_path)["dms-api"] == "pkg-01:5000/dms:d99"


def test_init_container_image_does_not_leak_into_lookup(tmp_path):
    # initContainers 는 'containers:' 접두사가 달라 _find 에 안 걸린다(독스트링의 주장).
    # 그 주장을 실제 픽스처로 고정한다: 본 컨테이너 조회가 init 이미지를 돌려주면 안 되고,
    # init 컨테이너 이름은 아예 조회 범위 밖이라 None 이어야 한다.
    (tmp_path / "40-api.yaml").write_text(_MULTI_CONTAINER)
    doc = workload_doc(tmp_path / "40-api.yaml", "Deployment", "dms-api")
    assert container_image(doc, "api") == "pkg-01:5000/dms:d99"
    assert container_image(doc, "sidecar") == "pkg-01:5000/dms-SIDECAR:s1"
    assert container_image(doc, "wait-migrate") is None
