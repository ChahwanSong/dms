"""manifest_tags 단위 테스트. 실제 deploy/k8s/*.yaml 을 그대로 파싱한다(설계 §5) --
픽스처를 합성하면 "실물 매니페스트를 읽는다"는 보증이 사라진다. 태그 숫자는 배포마다
바뀌므로 리포 접두사만 단언한다 -- 태그 핀 자체는 배포 절차(플랜 이후 절)의 몫이다."""
from pathlib import Path

from dms.manifest_tags import manifest_images, manifest_job_image

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
