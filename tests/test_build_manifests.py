import re

from dms.build_manifests import build_build_pod, build_probe_pod

BID = "0123456789abcdef0123456789abcdef"
SRC = "/home/mason/dms-dev/dms"


def _pod(images=("dms",), timeout_seconds=7200, tag="b01234567", source_path=SRC):
    return build_build_pod(build_id=BID, source_path=source_path, tag=tag,
                           images=list(images), node="dms-w1",
                           namespace="dms", registry="pkg-01:5000",
                           builder_image="quay.io/buildah/stable:latest",
                           timeout_seconds=timeout_seconds)


def test_pod_identity_and_placement():
    pod = _pod()
    assert pod["kind"] == "Pod" and pod["apiVersion"] == "v1"
    assert pod["metadata"]["name"] == "dms-build-0123456789ab"
    assert pod["metadata"]["namespace"] == "dms"
    assert pod["metadata"]["labels"]["dms.io/build-id"] == BID
    assert pod["metadata"]["labels"]["dms.io/phase"] == "build"
    assert pod["spec"]["nodeSelector"] == {"kubernetes.io/hostname": "dms-w1"}
    assert pod["spec"]["restartPolicy"] == "Never"


def test_container_is_privileged_builder_with_container_storage_volume():
    c = _pod()["spec"]["containers"][0]
    assert c["image"] == "quay.io/buildah/stable:latest"
    assert c["securityContext"]["privileged"] is True
    assert any(m["mountPath"] == "/var/lib/containers" for m in c["volumeMounts"])


def test_values_travel_as_env_not_interpolated_into_the_script():
    c = _pod()["spec"]["containers"][0]
    env = {e["name"]: e["value"] for e in c["env"]}
    assert env["DMS_BUILD_SRC"] == SRC
    assert env["DMS_BUILD_TAG"] == "b01234567"
    assert env["DMS_BUILD_REGISTRY"] == "pkg-01:5000"
    script = c["command"][2]
    # 값이 스크립트 본문에 박혀 있으면 주입 표면이 된다
    assert SRC not in script
    assert "b01234567" not in script


def test_tag_is_taken_from_the_caller_not_rederived():
    # 러너가 effective_tag() 로 확정해 넘긴다 -- 운영자 지정 태그(d73)가 그대로
    # 흘러야 화면의 태그와 push 태그가 갈라지지 않는다.
    c = _pod(tag="d73")["spec"]["containers"][0]
    env = {e["name"]: e["value"] for e in c["env"]}
    assert env["DMS_BUILD_TAG"] == "d73"


def test_source_is_mounted_readonly_at_the_same_absolute_path():
    # 같은 절대경로: 경로가 저장소 루트면 .git 이 그대로 보이고, 워크트리의
    # gitdir: 절대경로 해석도 성립할 여지가 남는다. ro 는 경계다 -- 특권 파드가
    # 개발자의 작업 트리를 쓰기로 오염시키는 길을 볼륨 단에서 막는다.
    pod = _pod()
    mounts = pod["spec"]["containers"][0]["volumeMounts"]
    src_mount = next(m for m in mounts if m["name"] == "src")
    assert src_mount["mountPath"] == SRC
    assert src_mount["readOnly"] is True
    src_vol = next(v for v in pod["spec"]["volumes"] if v["name"] == "src")
    # 빌드 파드는 type: Directory 다 -- 프로브가 존재를 이미 검증했으므로 여기서
    # 없으면 오설정이 아니라 사고(마운트 소실)라 시끄럽게 죽는 게 맞다.
    assert src_vol["hostPath"] == {"path": SRC, "type": "Directory"}


def test_script_snapshots_the_source_instead_of_cloning():
    script = _pod()["spec"]["containers"][0]["command"][2]
    assert "git clone" not in script                  # 로컬 소스 빌드 -- git 미연동
    assert "tar -cf -" in script and "tar -xf -" in script
    # 전송량 + .claude 재귀(워크트리가 저장소 안에 있다) 방지 제외 목록. 이미지
    # 내용의 밀폐성은 저장소 .dockerignore 가 지킨다 -- 여기 목록은 최소만.
    for excl in ("./.git", "./.claude", "./legacy",
                 "./.venv", "./frontend/node_modules"):
        assert f"--exclude={excl}" in script, excl


def test_script_reads_sha_from_the_mount_with_root_safe_git():
    script = _pod()["spec"]["containers"][0]["command"][2]
    # 소스는 개발자(비 root) 소유 + ro 마운트다 -- safe.directory 없이는 dubious
    # ownership 거절, GIT_OPTIONAL_LOCKS=0 없이는 index.lock 생성 실패가 난다.
    assert "safe.directory" in script
    assert "GIT_OPTIONAL_LOCKS=0" in script
    # 미커밋 변경 포함 빌드는 -dirty 로 정직하게 표시한다. rev-parse 실패(워크트리
    # gitdir 이 마운트 밖)는 unknown 으로 접는다 -- 지어내지 않는다.
    assert "-dirty" in script
    assert "unknown" in script


def test_images_are_forced_into_dependency_order():
    c = _pod(images=["dms-agent", "dms"])["spec"]["containers"][0]
    env = {e["name"]: e["value"] for e in c["env"]}
    assert env["DMS_BUILD_IMAGES"] == "dms dms-agent"


def test_script_pushes_insecurely_and_emits_the_commit_marker():
    script = _pod()["spec"]["containers"][0]["command"][2]
    assert "--tls-verify=false" in script     # 레지스트리가 평문 HTTP 다
    assert "DMS_COMMIT_SHA=" in script        # 감시 루프가 찾는 마커
    assert "set -eu" in script                # 중간 실패가 성공으로 보이면 안 된다


def test_containers_volume_has_a_size_limit():
    # M8: buildah 3종 빌드(특히 mpifileutils 소스 컴파일)는 수 GB를 쓴다. sizeLimit
    # 없는 emptyDir는 노드 ephemeral-storage 압박 시 kubelet이 같은 노드의
    # api/controller/agent 파드까지 축출할 수 있다.
    volume = _pod()["spec"]["volumes"][0]
    assert volume["name"] == "containers"
    assert volume["emptyDir"]["sizeLimit"]


def test_pod_has_active_deadline_seconds():
    # C2(a): 파드가 스케줄된 뒤엔 kubelet이 activeDeadlineSeconds로 죽인다 --
    # 이게 없으면 buildah가 네트워크에서 멈춰도 영원히 Running으로 남는다.
    assert _pod(timeout_seconds=7200)["spec"]["activeDeadlineSeconds"] == 7200
    assert _pod(timeout_seconds=42)["spec"]["activeDeadlineSeconds"] == 42


def test_dms_agent_case_pins_from_images_to_the_same_build_tag():
    # C1: dms-agent는 dms/dms-mpifileutils를 FROM한다(Dockerfile.agent). --build-arg
    # 없이는 buildah가 Dockerfile의 기본값(:dev, 손으로 만든 옛 이미지)을 조용히
    # 집어 엉뚱한 베이스에서 "성공"한다. 같은 빌드가 쓸 태그($DMS_BUILD_TAG)로
    # 명시적으로 고정해야 한다 -- _SCRIPT는 이미지 선택과 무관하게 항상 3개
    # case 분기를 전부 담은 하나의 상수이므로(런타임에 DMS_BUILD_IMAGES로 순회),
    # 여기서는 dms-agent case 분기 자체의 구조를 검사한다.
    script = _pod()["spec"]["containers"][0]["command"][2]
    block = re.search(r"dms-agent\)(.*?);;", script, re.S).group(1)
    assert '--build-arg "DMS_IMAGE=$DMS_BUILD_REGISTRY/dms:$DMS_BUILD_TAG"' in block
    assert '--build-arg "MFU_IMAGE=$DMS_BUILD_REGISTRY/dms-mpifileutils:$DMS_BUILD_TAG"' in block


def test_dms_solo_build_case_has_no_build_arg():
    # dms/dms-mpifileutils는 아무것도 FROM하지 않으므로 build-arg가 붙으면 안 된다.
    script = _pod()["spec"]["containers"][0]["command"][2]
    dms_block = re.search(r"dms\)(.*?);;", script, re.S).group(1)
    mfu_block = re.search(r"dms-mpifileutils\)(.*?);;", script, re.S).group(1)
    assert "--build-arg" not in dms_block
    assert "--build-arg" not in mfu_block


# ---- 슬라이스 21 §2.2/§2.3/§2.4: 리소스 봉투 + dms-build 클래스 + sizeLimit ----

def test_build_container_resource_envelope_pins_the_design_numbers():
    # 수치는 전부 워커 실측(allocatable 1800m/1355Mi/36.4GB) 역산이다(설계 §2.2):
    # - cpu limit 1000m 이 실질 보호막 -- 빌드가 날뛰어도 잡+제어면 몫 800m 이 남는다.
    # - memory requests 128Mi 는 일부러 작게 -- 실압박에서 빌드가 항상 "requests
    #   초과" 축출 그룹에 들어 ②priority(dms-build 10)가 방향을 가른다.
    # - memory limit 1Gi 는 노드 eviction 전에 빌드가 먼저 혼자 OOM-kill 되는
    #   의도된 1차 방어(M8 재발 방지).
    c = _pod()["spec"]["containers"][0]
    assert c["resources"] == {
        "requests": {"cpu": "250m", "memory": "128Mi", "ephemeral-storage": "10Gi"},
        "limits": {"cpu": "1000m", "memory": "1Gi", "ephemeral-storage": "12Gi"},
    }


def test_build_pod_uses_the_dms_build_priority_class():
    # 미적용 클러스터에서는 admission 거절이다 -- Task 1 매니페스트가 먼저 apply
    # 돼야 한다(플랜 이후 절의 배포 순서).
    assert _pod()["spec"]["priorityClassName"] == "dms-build"


def test_sizelimit_is_10gi_and_not_above_the_eph_limit():
    # §2.4: 20Gi 는 실측 여유(eviction 임계 차감 후 15.7GB)보다 커서 sizeLimit
    # 이전에 노드 압박 eviction(같은 노드 파드 전체가 후보)이 먼저 온다 -- 10Gi 로
    # 내려 레이어 폭주 시 빌드 파드만 축출되게 한다. eph limits(12Gi)는 emptyDir
    # 사용량 포함 파드 단위 집행이라 sizeLimit ≤ limits 여야 sizeLimit 이 먼저
    # 발화한다(관계 단언).
    pod = _pod()
    assert pod["spec"]["volumes"][0]["emptyDir"]["sizeLimit"] == "10Gi"
    eph_limit = pod["spec"]["containers"][0]["resources"]["limits"]["ephemeral-storage"]
    assert 10 <= int(eph_limit.removesuffix("Gi"))


def test_scheduling_shape_is_unchanged_nodeselector_and_default_scheduler():
    # §2.1: nodeSelector+default-scheduler 유지 -- requests 를 얹어 스케줄러
    # Fit(노드 여유 검사)을 공짜로 사고, 지정 노드 보장은 hostname nodeSelector 가
    # 그대로 한다. (현행 고정 가드 -- Step 2 에서 즉시 PASS 가 맞다.)
    pod = _pod()
    assert pod["spec"]["nodeSelector"] == {"kubernetes.io/hostname": "dms-w1"}
    assert "schedulerName" not in pod["spec"]


# ---- 슬라이스 21 §2.5: 적합성 프로브 파드 (매니페스트는 순수 함수) ----

def _probe(source_path=SRC, timeout_seconds=180):
    return build_probe_pod(build_id=BID, source_path=source_path, node="dms-w1",
                           namespace="dms", registry="pkg-01:5000",
                           job_image="pkg-01:5000/dms-mpifileutils:d27",
                           timeout_seconds=timeout_seconds)


def test_probe_identity_small_envelope_and_class():
    pod = _probe()
    # 결정적 이름 + 63자 상한: 워처가 상태를 DB 에 두지 않고도 "이 빌드의 프로브"를
    # 언제든 다시 찾는 근거다(buildpod/ ref 재사용 -- poll/read_log/terminate 공짜).
    assert pod["metadata"]["name"] == "dms-build-pf-0123456789ab"
    assert len(pod["metadata"]["name"]) <= 63
    assert pod["metadata"]["labels"]["dms.io/build-id"] == BID
    assert pod["spec"]["restartPolicy"] == "Never"
    assert pod["spec"]["nodeSelector"] == {"kubernetes.io/hostname": "dms-w1"}
    assert pod["spec"]["priorityClassName"] == "dms-build"   # 빌드와 같은 축출 방향
    assert pod["spec"]["activeDeadlineSeconds"] == 180
    c = pod["spec"]["containers"][0]
    # job_image(캐시 존재·pull 은 pkg-01 만 필요)여야 프로브 기동 자체가 인터넷과
    # 무관하다 -- builder image(quay.io)면 위음성/위양성이 난다(설계 §2.5).
    assert c["image"] == "pkg-01:5000/dms-mpifileutils:d27"
    # 작은 봉투: 소켓 3~4개와 statvfs·isfile 뿐 -- 프로브가 노드에 압박을 만들면
    # 안 된다.
    assert c["resources"] == {"requests": {"cpu": "50m", "memory": "32Mi"},
                              "limits": {"cpu": "200m", "memory": "128Mi"}}


def test_probe_targets_travel_as_env_not_in_the_script():
    c = _probe()["spec"]["containers"][0]
    env = {e["name"]: e["value"] for e in c["env"]}
    # 소스가 로컬이 된 뒤에도 quay.io(빌더 이미지)·registry-1.docker.io(베이스
    # 이미지 -- Dockerfile.dms:13,24 / Dockerfile.mpifileutils:17)는 남는다.
    assert env["DMS_PF_EGRESS_HOSTS"] == "quay.io registry-1.docker.io"
    assert env["DMS_PF_REGISTRY"] == "pkg-01:5000"
    assert env["DMS_PF_SRC"] == SRC
    assert env["DMS_PF_NEED_BYTES"] == str(12 * 1024 ** 3)   # sizeLimit 10Gi + 마진 2Gi
    assert c["command"][:2] == ["python3", "-c"]
    script = c["command"][2]
    # 값이 스크립트 본문에 박히면 경로가 코드가 된다(빌드 파드와 같은 원칙).
    assert SRC not in script
    assert "pkg-01:5000" not in script


def test_probe_script_follows_the_preflight_marker_convention():
    # execution_manifests._preflight_script 와 같은 마커 문법(실패 = REASON= + exit 1,
    # 성공 = OK) -- 워처가 한 가지 파서 계열로 읽는다.
    script = _probe()["spec"]["containers"][0]["command"][2]
    assert "DMS_PREFLIGHT_REASON=" in script
    assert "DMS_PREFLIGHT_OK" in script
    for code in ("build_source_unavailable", "build_node_no_egress",
                 "build_registry_unreachable", "build_node_disk_low"):
        assert code in script, code
    assert "os.statvfs" in script and "0.15" in script   # eviction 미러 상수


def test_probe_checks_the_source_sentinel_dockerfile():
    # 경로가 "존재하지만 DMS 저장소가 아닌" 오설정(상위 디렉토리 지정 등)을
    # isdir 이 아니라 Dockerfile.dms 센티널로 잡는다 -- isdir 만 보면 buildah
    # 깊숙한 곳에서 no such Dockerfile 로 죽어 사유가 뭉개진다.
    script = _probe()["spec"]["containers"][0]["command"][2]
    assert "Dockerfile.dms" in script


def test_probe_mounts_the_source_without_a_hostpath_type():
    # 프로브의 hostPath 는 type 을 비운다: 경로가 노드에 없으면 kubelet 이 빈
    # 디렉토리를 만들어서라도 파드를 띄워, 오타 경로가 FailedMount 영구 Pending
    # (-> 180s 뒤 build_preflight_timeout 으로 뭉개짐)이 아니라 Dockerfile 부재
    # (build_source_unavailable)로 명확히 잡히게 한다.
    pod = _probe()
    src_vol = next(v for v in pod["spec"]["volumes"] if v["name"] == "src")
    assert src_vol["hostPath"] == {"path": SRC}
    mounts = pod["spec"]["containers"][0]["volumeMounts"]
    src_mount = next(m for m in mounts if m["name"] == "src")
    assert src_mount["mountPath"] == SRC
    assert src_mount["readOnly"] is True
