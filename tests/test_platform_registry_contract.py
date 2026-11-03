"""클러스터 내 레지스트리 플랫폼 층 계약(2026-11-03 ④, 설계 v6 7·8-2·9 「테스트·게이트 ④」).

설계 9 「테스트·게이트」 ④ 줄: clusterIP == values, hostPath 가 SHARED_DIR 아래·`type: Directory`, delete.enabled, cache 없음, http secret 은
Secret 참조, replicas 1·`maxSurge: 1`·`maxUnavailable: 0`, init 은 센티널을 만들지 않음(검사만), 레지스트리 이미지는 태그 ref 예외,
PriorityClass 존재. 여기에 더해:

- base(deploy/platform/registry)는 사이트 중립 자리표시자뿐이다(raw apply 는 실패) — 앱 base 와 같은 규칙.
- 렌더러(deploy/render.sh — 2026-11-03 ④ 일반화)가 prod(템플릿 + 견본 values)·testbed-k8s(커밋된 values) 두 사이트의 플랫폼 층을 낸다.
  in-cluster 면 「같은 주소가 여섯 곳」(설계 7-4) 중 다섯(REGISTRY·newName·DMS_JOB_IMAGE·DMS_BUILD_REGISTRY·DMS_BUILD_BUILDER_IMAGE)이
  <REGISTRY_CLUSTER_IP>:<REGISTRY_PORT> 하나다. testbed-k8s 는 앱이 아직 pkg-01:5001(external, T4 병행)이고 렌더된 앱 == 커밋된 오버레이다.
- 렌더러 실패는 결과 디렉터리를 만들지도 바꾸지도 않는다.
- 레지스트리 파드 uid(65532)는 S0(deploy/shared-dir-init.sh)가 만드는 저장소 소유와 같다(두 파일 대조).

PyYAML 없이(저장소 의존성이 아니다) kustomize 출력의 고정된 모양(키 정렬·2칸 들여쓰기)에 기대는 최소 정규식이다 — 못 찾으면 assert 로
실패한다. 렌더는 `kubectl kustomize` 로컬 렌더뿐이다(KUBECONFIG=/dev/null — 클러스터를 보지 않는다).
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from overlay_render import KUBECTL, OVERLAYS, REPO_ROOT, config_data, doc, needs_kubectl, seq

DEPLOY = REPO_ROOT / "deploy"
PLATFORM = DEPLOY / "platform"
REGISTRY_BASE = PLATFORM / "registry"
PC_BASE = PLATFORM / "priorityclass"
TESTBED = OVERLAYS / "testbed-k8s"
PROD = OVERLAYS / "prod"
PLACEHOLDER = "set-by-overlay.invalid"
REGISTRY_TAG = "3.1.2"
ENV = {**os.environ, "KUBECONFIG": os.devnull}


def _kustomize(path: Path) -> str:
    r = subprocess.run([KUBECTL, "kustomize", str(path)], capture_output=True, text=True, timeout=120, env=ENV)
    assert r.returncode == 0, f"kubectl kustomize {path} 실패: {r.stderr}"
    return r.stdout


def _values_env(path: Path) -> dict:
    return dict(re.findall(r"(?m)^([A-Z_]+)=(\S*)", path.read_text(encoding="utf-8")))


def _block(text: str, key: str) -> str:
    """`key:` 줄(첫 번째) 아래 블록. kustomize 는 시퀀스 항목(`- `)을 부모 키와 같은 열에 둔다 — 그 줄들도 블록이다."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(rf"^(\s*)((?:- )?){re.escape(key)}:\s*$", line)
        if not m:
            continue
        col = len(m.group(1)) + len(m.group(2))
        out = []
        for nxt in lines[i + 1:]:
            ind = len(nxt) - len(nxt.lstrip())
            if nxt.strip() and (ind < col or (ind == col and not nxt.lstrip().startswith("- "))):
                break
            out.append(nxt)
        return "\n".join(out)
    raise AssertionError(f"`{key}:` 블록이 없다")


def _container(deploy_doc: str, section: str, name: str) -> str:
    """containers / initContainers 의 이름 `name` 항목 텍스트(kustomize 출력: `      - args:` … `        name: <name>`)."""
    sec = _block(deploy_doc, section)
    items = re.split(r"(?m)^      - ", sec)
    for item in items:
        if re.search(rf"(?m)^        name: {re.escape(name)}$", item):
            return "      - " + item                     # 첫 키의 열(시퀀스 항목 머리)을 되살린다 — seq() 가 열로 항목을 찾는다
    raise AssertionError(f"{section} 에 {name} 이 없다")


def _hostpaths(deploy_doc: str) -> list:
    return re.findall(r"(?m)^      - hostPath:\n          path: (\S+)\n          type: (\S+)\n        name: (\S+)$", deploy_doc)


# --- base(사이트 중립) ------------------------------------------------------------------------------------------------------

@needs_kubectl
def test_base_carries_only_site_neutral_placeholders():
    text = _kustomize(REGISTRY_BASE)
    dep = doc(text, "Deployment", "dms-registry")
    svc = doc(text, "Service", "dms-registry")
    cm = doc(text, "ConfigMap", "dms-registry-config")
    assert re.findall(r"(?m)^\s*image: (\S+)$", dep) == [f"{PLACEHOLDER}/registry:{REGISTRY_TAG}"] * 2   # registry + init
    assert re.search(rf"(?m)^  clusterIP: {re.escape(PLACEHOLDER)}$", svc)
    assert _hostpaths(dep) == [(f"/{PLACEHOLDER}/dms/registry", "Directory", "registry-data")]
    assert re.search(rf"(?m)^  expected-registry-id: {re.escape(PLACEHOLDER)}$", cm)
    # 실 주소·경로가 base 에 커밋되면 git pull 한 다른 사이트가 그것을 배포한다(앱 base 2026-09-14 실사고와 같은 모양).
    code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    assert not re.search(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", code)
    assert "/dms_shared" not in code and "pkg-01" not in code


def test_platform_layers_live_outside_the_overlay_and_base_contract_dirs():
    # overlays/·k8s/ 아래 디렉터리를 모두 도는 계약 테스트(설계 8-2)를 피하는 배치이고, 렌더 결과는 git 밖이다.
    assert (REGISTRY_BASE / "kustomization.yaml").is_file() and (PC_BASE / "kustomization.yaml").is_file()
    assert not list(OVERLAYS.glob("*/platform*")) and not (DEPLOY / "k8s" / "platform").exists()
    assert re.search(r"(?m)^deploy/\.rendered/$", (REPO_ROOT / ".gitignore").read_text())


# --- config.yml(설계 7-3) ------------------------------------------------------------------------------------------------

def _flat_yaml(text: str) -> dict:
    """블록 매핑만 있는 YAML(config.yml) → {"a.b.c": "값"}. 주석·빈 줄 무시."""
    out, stack = {}, []
    for raw in text.splitlines():
        line = raw.split(" #", 1)[0].rstrip() if not raw.lstrip().startswith("#") else ""
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        key, _, value = line.strip().partition(":")
        while stack and stack[-1][0] >= indent:
            stack.pop()
        path = ".".join([k for _, k in stack] + [key])
        if value.strip():
            out[path] = value.strip()
        else:
            stack.append((indent, key))
    return out


def test_config_is_the_designed_distribution_config():
    flat = _flat_yaml((REGISTRY_BASE / "config.yml").read_text(encoding="utf-8"))
    assert flat["storage.filesystem.rootdirectory"] == "/var/lib/registry"
    assert flat["storage.delete.enabled"] == "true"
    assert flat["storage.maintenance.uploadpurging.enabled"] == "true"
    assert flat["storage.maintenance.uploadpurging.age"] == "168h"
    assert flat["storage.maintenance.uploadpurging.interval"] == "24h"
    assert flat["storage.maintenance.readonly.enabled"] == "false"
    assert flat["http.addr"] == ":5000"
    assert flat["health.storagedriver.enabled"] == "true"
    # cache 없음(GC 뒤 낡은 「있음」), 디버그 서버 없음(이미지 기본 :5001 을 덮는다), 비밀·TLS·인증 없음(평문·익명 — 설계 7-2).
    for key in flat:
        low = key.lower()
        assert not any(w in low for w in ("cache", "redis", "debug", "secret", "tls", "auth", "proxy")), key


# --- Deployment·Service 모양(설계 7-3) -------------------------------------------------------------------------------------

@needs_kubectl
def test_deployment_is_one_replica_rolling_surge_on_control_plane():
    dep = doc(_kustomize(REGISTRY_BASE), "Deployment", "dms-registry")
    assert re.search(r"(?m)^  replicas: 1$", dep)
    assert re.search(r"(?m)^    rollingUpdate:\n      maxSurge: 1\n      maxUnavailable: 0\n    type: RollingUpdate$", dep)
    assert re.search(r'(?m)^      nodeSelector:\n        node-role.kubernetes.io/control-plane: ""$', dep)
    tol = _block(dep, "tolerations")
    assert re.search(r"(?m)^      - effect: NoSchedule\n        key: node-role.kubernetes.io/control-plane\n        operator: Exists$", tol)
    for key in ("node.kubernetes.io/not-ready", "node.kubernetes.io/unreachable"):
        assert re.search(rf"(?m)^      - effect: NoExecute\n        key: {re.escape(key)}\n        operator: Exists\n"
                         r"        tolerationSeconds: 30$", tol), key
    assert "priorityClassName" not in dep        # 마스터 전용(잡이 없는 노드) — 선점할 일이 없다(priorityclass.yaml 머리 주석)


def _pull_policies(deploy_doc: str) -> list:
    return re.findall(r"(?m)^\s*imagePullPolicy: (\S+)$", deploy_doc)


@needs_kubectl
def test_every_registry_container_pulls_if_not_present():
    # 설계 7-4 의존 규칙·7-6: 레지스트리 자기 이미지는 이 레지스트리에 기대지 않는다 — 노드에 반입·고정한 사본으로만 뜬다. init·본 컨테이너
    # 어느 하나라도 Always 면, 레지스트리가 내려간 동안(다른 마스터로 옮겨질 때·cp 재부팅) kubelet 이 <IP>:<PORT>/registry:<태그> 를 그
    # 레지스트리 자신에게서 받으려 해 파드가 영영 뜨지 않는다(자기 의존 교착). 키를 빼도 안 된다(태그가 latest 면 기본값이 Always).
    dep = doc(_kustomize(REGISTRY_BASE), "Deployment", "dms-registry")
    assert _pull_policies(dep) == ["IfNotPresent"] * 2 and "Always" not in dep
    for section, name in (("initContainers", "sentinel-check"), ("containers", "registry")):
        assert re.search(r"(?m)^        imagePullPolicy: IfNotPresent$", _container(dep, section, name)), name


@needs_kubectl
def test_registry_container_probes_secret_and_resources():
    dep = doc(_kustomize(REGISTRY_BASE), "Deployment", "dms-registry")
    reg = _container(dep, "containers", "registry")
    assert seq(reg, "command") == ["/bin/registry"] and seq(reg, "args") == ["serve", "/etc/distribution/config.yml"]
    # http secret 은 Secret 참조(secretKeyRef)뿐 — 값이 매니페스트에 없다.
    assert re.search(r"(?m)^        - name: REGISTRY_HTTP_SECRET\n          valueFrom:\n            secretKeyRef:\n"
                     r"              key: http-secret\n              name: dms-registry-http$", reg)
    assert not re.search(r"(?m)^          value: ", reg)
    ready = _block(reg, "readinessProbe")
    assert re.search(r"(?m)^          httpGet:\n            path: /v2/_catalog\?n=1\n            port: http$", ready)
    for k, v in (("timeoutSeconds", "5"), ("periodSeconds", "10"), ("failureThreshold", "3")):
        assert re.search(rf"(?m)^          {k}: {v}$", ready), k
    live = _block(reg, "livenessProbe")
    assert re.search(r"(?m)^          tcpSocket:\n            port: http$", live) and "httpGet" not in live
    res = _block(reg, "resources")
    assert re.search(r'(?m)^          limits:\n            cpu: "1"\n            memory: 512Mi\n'
                     r"          requests:\n            cpu: 100m\n            memory: 128Mi$", res)
    assert re.search(r"(?m)^        - containerPort: 5000\n          name: http\n          protocol: TCP$", reg)


@needs_kubectl
def test_storage_is_the_one_hostpath_directory_and_config_mount():
    dep = doc(_kustomize(REGISTRY_BASE), "Deployment", "dms-registry")
    assert _hostpaths(dep) == [(f"/{PLACEHOLDER}/dms/registry", "Directory", "registry-data")]
    assert len(re.findall(r"(?m)hostPath:", dep)) == 1
    reg = _container(dep, "containers", "registry")
    assert re.search(r"(?m)^        - mountPath: /var/lib/registry\n          name: registry-data$", reg)
    assert re.search(r"(?m)^        - mountPath: /etc/distribution\n          name: config\n          readOnly: true$", reg)


@needs_kubectl
def test_service_is_cluster_ip_on_the_http_port():
    svc = doc(_kustomize(REGISTRY_BASE), "Service", "dms-registry")
    assert re.search(r"(?m)^  type: ClusterIP$", svc)
    assert re.search(r"(?m)^  - name: http\n    port: 5000\n    protocol: TCP\n    targetPort: http$", svc)
    assert re.search(r"(?m)^  selector:\n    app.kubernetes.io/name: dms-registry$", svc)


@needs_kubectl
def test_no_secret_object_in_the_platform_layers():
    # Secret dms-registry-http 는 S4 가 없을 때만 만든다(출력하지 않음) — 매니페스트·렌더 결과에 Secret 이 생기면 비밀이 git 에 남는다.
    for path in (REGISTRY_BASE, PC_BASE):
        assert not re.search(r"(?m)^kind: Secret$", _kustomize(path)), path


@needs_kubectl
def test_pod_runs_as_the_uid_that_owns_the_s0_registry_directory():
    # 보안 강화(설계 7-3 「(선택)」 — 한다): S0 가 저장소를 65532:65532 0750 으로 만든다. root 면 저장소 파일이 0 소유로 섞이고, drop ALL 인
    # root 는 DAC_OVERRIDE 가 없어 그 디렉터리에 쓰지도 못한다. 두 파일이 어긋나면 레지스트리가 저장소에 쓰지 못한다.
    tree = re.search(r'(?m)^dms/registry (\d+) (\d+) (\d+)"?$', (DEPLOY / "shared-dir-init.sh").read_text())
    assert tree, "shared-dir-init.sh 의 TREE 에 dms/registry 줄이 없다"
    uid, gid, mode = tree.groups()
    assert mode == "750"
    dep = doc(_kustomize(REGISTRY_BASE), "Deployment", "dms-registry")
    # 파드 수준(init·본 컨테이너 공통) — kustomize 는 키를 정렬한다.
    assert re.search(rf"(?m)^      securityContext:\n        runAsGroup: {gid}\n        runAsNonRoot: true\n"
                     rf"        runAsUser: {uid}\n        seccompProfile:\n          type: RuntimeDefault$", dep)
    for name, section in (("registry", "containers"), ("sentinel-check", "initContainers")):
        c = _container(dep, section, name)
        assert re.search(r"(?m)^          allowPrivilegeEscalation: false\n          capabilities:\n            drop:\n"
                         r"            - ALL\n          readOnlyRootFilesystem: true$", c), name
    assert re.search(r"(?m)^      automountServiceAccountToken: false$", dep)


# --- init 센티널(검사만 — 설계 7-3) ---------------------------------------------------------------------------------------

def _init_script() -> str:
    lines = (REGISTRY_BASE / "deployment.yaml").read_text(encoding="utf-8").splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.strip() == "- name: sentinel-check")
    bar = next(i for i in range(start, len(lines)) if lines[i].strip() == "- |")
    indent = len(lines[bar + 1]) - len(lines[bar + 1].lstrip())
    body = []
    for ln in lines[bar + 1:]:
        if ln.strip() and len(ln) - len(ln.lstrip()) < indent:
            break
        body.append(ln[indent:])
    return "\n".join(body) + "\n"


def test_init_script_never_writes():
    script = _init_script()
    code = "\n".join(ln for ln in script.splitlines() if not ln.lstrip().startswith("#"))
    bare = re.sub(r'"(?:[^"\\]|\\.)*"', '""', code)               # 메시지 문자열 안의 <SHARED_DIR> 같은 글자는 리다이렉션이 아니다
    redirections = re.findall(r"(?<![0-9&])>+(?!&)", bare)
    assert redirections == [], redirections                  # >&2(stderr)만 — 파일로 쓰지 않는다
    for verb in ("touch", "mkdir", "cp ", "mv ", "tee", "rm ", "chmod", "chown", "ln ", "install ", "dd ", "printf"):
        assert verb not in code, verb
    dep = (REGISTRY_BASE / "deployment.yaml").read_text(encoding="utf-8")
    init = dep[dep.index("- name: sentinel-check"):dep.index("      containers:")]
    assert re.search(r"(?m)^            - name: registry-data\n              mountPath: /var/lib/registry\n"
                     r"              readOnly: true$", init)
    assert "terminationMessagePolicy: FallbackToLogsOnError" in init


def _run_init(tmp_path, expected, setup=None):
    root = tmp_path / "registry"
    root.mkdir(exist_ok=True)
    if setup:
        setup(root)
    before = sorted(p.name for p in root.iterdir())
    script = _init_script().replace("/var/lib/registry", str(root))
    env = {"PATH": "/usr/bin:/bin"}
    if expected is not None:
        env["DMS_EXPECTED_REGISTRY_ID"] = expected
    r = subprocess.run(["sh", "-c", script], env=env, capture_output=True, text=True, timeout=30)
    after = sorted(p.name for p in root.iterdir())
    return r, before, after


@pytest.mark.parametrize("expected", [None, "", PLACEHOLDER])
def test_init_refuses_without_an_expected_value(tmp_path, expected):
    r, before, after = _run_init(tmp_path, expected, lambda d: (d / ".dms-registry-id").write_text("abc\n"))
    assert r.returncode == 1 and "기대값 없음" in r.stderr, (r.stdout, r.stderr)
    assert before == after


def test_init_refuses_a_missing_sentinel_and_does_not_create_it(tmp_path):
    r, before, after = _run_init(tmp_path, "abc")
    assert r.returncode == 1 and "센티널 없음" in r.stderr and "S0" in r.stderr, (r.stdout, r.stderr)
    assert before == after == []                              # init 은 만들지 않는다 — 만들면 마운트 빠진 노드에서 무력해진다


def test_init_refuses_a_mismatched_sentinel(tmp_path):
    r, before, after = _run_init(tmp_path, "abc", lambda d: (d / ".dms-registry-id").write_text("other\n"))
    assert r.returncode == 1 and "센티널 불일치" in r.stderr and "'abc'" in r.stderr and "'other'" in r.stderr
    assert (tmp_path / "registry" / ".dms-registry-id").read_text() == "other\n"     # 고치지 않는다
    assert before == after


def test_init_does_not_follow_a_symlinked_sentinel(tmp_path):
    target = tmp_path / "elsewhere"
    target.write_text("abc\n")
    r, _, _ = _run_init(tmp_path, "abc", lambda d: (d / ".dms-registry-id").symlink_to(target))
    assert r.returncode == 1 and "일반 파일이 아님" in r.stderr, (r.stdout, r.stderr)


def test_init_passes_a_matching_sentinel_reading_only_the_first_line(tmp_path):
    r, before, after = _run_init(tmp_path, "e0959a5c84c1d9101dadecf4edc94f31",
                                 lambda d: (d / ".dms-registry-id").write_text("e0959a5c84c1d9101dadecf4edc94f31\n"))
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "센티널 확인" in r.stdout and before == after


@pytest.mark.skipif(os.geteuid() == 0, reason="root 는 모드와 무관하게 읽는다")
def test_init_reports_an_unreadable_directory_distinctly(tmp_path):
    root = tmp_path / "registry"
    root.mkdir()
    (root / ".dms-registry-id").write_text("abc\n")
    root.chmod(0o000)
    try:
        script = _init_script().replace("/var/lib/registry", str(root))
        r = subprocess.run(["sh", "-c", script], env={"PATH": "/usr/bin:/bin", "DMS_EXPECTED_REGISTRY_ID": "abc"},
                           capture_output=True, text=True, timeout=30)
    finally:
        root.chmod(0o755)
    assert r.returncode == 1 and "읽을 수 없음" in r.stderr, (r.stdout, r.stderr)


# --- 이미지: 태그 ref 예외 + lock(설계 7-5) ---------------------------------------------------------------------------------

def _lock_entries() -> dict:
    out = {}
    for line in (DEPLOY / "images.lock").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            dest, src, dg = line.split()
            out[dest] = (src, dg)
    return out


def test_registry_image_is_the_documented_tag_ref_exception_and_matches_the_lock():
    dep = (REGISTRY_BASE / "deployment.yaml").read_text(encoding="utf-8")
    images = re.findall(r"(?m)^\s*image: (\S+)$", dep)
    assert images == [f"{PLACEHOLDER}/registry:{REGISTRY_TAG}"] * 2
    assert not any("@sha256" in i for i in images)            # 태그 ref(반입본 RepoDigest 미실증 — 7-5)
    lock = _lock_entries()
    src, digest = lock[f"registry:{REGISTRY_TAG}"]
    assert src == f"docker.io/library/registry:{REGISTRY_TAG}" and digest.startswith("sha256:")


@needs_kubectl
def test_installed_version_label_moves_with_the_image_tag_and_the_lock():
    # 설계 8-2 A: DMS 가 설치한 객체에 managed-by dms-install + dms.io/addon-version. ⑦ 감지기가 Deployment dms-registry 에서 이 라벨을
    # 읽어 D=/D</D> 를 가른다 — 업그레이드 때 라벨·두 이미지 태그·lock 의 registry:<태그> 가 같이 움직여야 한다(deploy/README §2e-5).
    dep = doc(_kustomize(REGISTRY_BASE), "Deployment", "dms-registry")
    meta = dep.split("\nspec:\n", 1)[0]
    m = re.search(r'(?m)^    dms\.io/addon-version: "?([^"\s]+)"?$', meta)
    assert m, "Deployment dms-registry 에 dms.io/addon-version 라벨이 없다"
    assert re.search(r"(?m)^    app\.kubernetes\.io/managed-by: dms-install$", meta)
    tags = {i.rsplit(":", 1)[1] for i in re.findall(r"(?m)^\s*image: (\S+)$", dep)}
    lock_tags = {d.split(":", 1)[1] for d in _lock_entries() if d.startswith("registry:")}
    assert tags == lock_tags == {m.group(1)} == {REGISTRY_TAG}, (tags, lock_tags, m.group(1))


def test_the_builder_image_the_overlays_name_is_in_the_lock():
    # 포탈 빌드의 빌더(DMS_BUILD_BUILDER_IMAGE = <REGISTRY>/buildah:stable)는 S5 가 lock 으로 push 한다 — lock 에 없으면 빌드가 pull 에 실패.
    prod = re.search(r'(?m)^  DMS_BUILD_BUILDER_IMAGE: "REPLACE_REGISTRY/(\S+)"$', (PROD / "patch-config.yaml").read_text())
    assert prod and prod.group(1) in _lock_entries(), prod


# --- PriorityClass(설계 2-1) ----------------------------------------------------------------------------------------------

@needs_kubectl
def test_priority_class_exists_above_every_job_class():
    pc = doc(_kustomize(PC_BASE), "PriorityClass", "dms-addon-critical")
    value = int(re.search(r"(?m)^value: (\d+)$", pc).group(1))
    assert value == 1000
    assert re.search(r"(?m)^globalDefault: false$", pc)
    # 설계 2-1: 잡이 워커 CPU 를 채운 상태에서 재스케줄된 애드온 컨트롤러는 잡 파드를 **선점해야** 자리를 얻는다 — Never 면 값이 아무리
    # 높아도 대기열 앞에 설 뿐 영영 Pending 일 수 있다(클러스터의 dms-build(10, Never)와 다른 모양이다).
    assert re.search(r"(?m)^preemptionPolicy: PreemptLowerPriority$", pc) and "Never" not in pc
    jobs = (DEPLOY / "k8s" / "05-volcano-queue-priorityclass.yaml").read_text()
    job_values = [int(v) for v in re.findall(r"(?m)^value: (\d+)$", jobs)]
    assert job_values and value > max(job_values)            # dms-high(200)를 선점할 수 있어야 한다


# --- 렌더러(deploy/render.sh) ----------------------------------------------------------------------------------------------

def _tree(tmp_path) -> Path:
    root = tmp_path / "deploy"
    root.mkdir(parents=True)
    shutil.copy(DEPLOY / "render.sh", root / "render.sh")
    shutil.copytree(PLATFORM, root / "platform")
    shutil.copytree(DEPLOY / "k8s", root / "k8s")
    shutil.copytree(OVERLAYS, root / "overlays", ignore=shutil.ignore_patterns(".prod-rendered", "values.env.local"))
    return root


def _render(tmp_path, site, values_text=None, **env_extra):
    root = _tree(tmp_path) if not (tmp_path / "deploy").exists() else tmp_path / "deploy"
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(tmp_path), **env_extra}
    if values_text is not None:
        vals = tmp_path / f"{site}.values.env"
        vals.write_text(values_text)
        env["VALUES_ENV"] = str(vals)
    r = subprocess.run(["sh", str(root / "render.sh"), site], env=env, capture_output=True, text=True, timeout=60)
    return r, root / ".rendered" / site


def _prod_values(**overrides) -> str:
    text = (PROD / "values.env.example").read_text()
    text = re.sub(r"\bREPLACE_([A-Z_]+)", lambda m: "v-" + m.group(1).lower().replace("_", "-"), text)
    text = re.sub(r"(?m)^SHARED_DIR=\S+", "SHARED_DIR=/srv/dms_shared", text)
    text = re.sub(r"(?m)^REGISTRY_CLUSTER_IP=\S+", "REGISTRY_CLUSTER_IP=10.96.0.50", text)
    text = re.sub(r"(?m)^SHARED_DIR_ID=\S+", "SHARED_DIR_ID=0123abcd", text)
    for key, value in overrides.items():
        if value is None:
            text = re.sub(rf"(?m)^{key}=.*$", "", text)
        elif re.search(rf"(?m)^{key}=", text):
            text = re.sub(rf"(?m)^{key}=\S*", lambda _m: f"{key}={value}", text)
        else:
            text += f"\n{key}={value}\n"
    return text


def _registry_render(out: Path) -> dict:
    text = _kustomize(out / "platform" / "registry")
    dep = doc(text, "Deployment", "dms-registry")
    svc = doc(text, "Service", "dms-registry")
    cm = doc(text, "ConfigMap", "dms-registry-config")
    return {
        "images": re.findall(r"(?m)^\s*image: (\S+)$", dep),
        "cluster_ip": re.search(r"(?m)^  clusterIP: (\S+)$", svc).group(1),
        "port": re.search(r"(?m)^  - name: http\n    port: (\d+)$", svc).group(1),
        "hostpaths": _hostpaths(dep),
        "expected": re.search(r"(?m)^  expected-registry-id: (\S+)$", cm).group(1),
        "requests": tuple(v.strip('"') for v in re.search(
            r"(?m)^          requests:\n            cpu: (\S+)\n            memory: (\S+)$",
            _container(dep, "containers", "registry")).groups()),
        "dep": dep,
        "text": text,
    }


def _render_env(out: Path) -> dict:
    return dict(re.findall(r'(?m)^([A-Z_]+)="?([^"\n]*)"?$', (out / "render.env").read_text()))


@needs_kubectl
def test_testbed_platform_render_takes_every_value_from_the_committed_values_env(tmp_path):
    values = _values_env(TESTBED / "values.env")
    r, out = _render(tmp_path, "testbed-k8s")
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert r.stdout.strip().splitlines()[-1] == f"RENDERED: {out}/app"
    assert f"PLATFORM: {out}/platform/registry" in r.stdout and f"PLATFORM: {out}/platform/priorityclass" in r.stdout
    ip, port, shared = values["REGISTRY_CLUSTER_IP"], values["REGISTRY_PORT"], values["SHARED_DIR"]
    assert (ip, port) == ("10.112.0.50", "5000")                      # 설계 10 T4(미사용 실측)
    reg = _registry_render(out)
    assert reg["cluster_ip"] == ip and reg["port"] == port            # clusterIP == values
    assert reg["images"] == [f"{ip}:{port}/registry:{REGISTRY_TAG}"] * 2
    assert reg["hostpaths"] == [(f"{shared}/dms/registry", "Directory", "registry-data")]
    assert reg["hostpaths"][0][0].startswith(f"{shared}/dms/") and reg["hostpaths"][0][0] != shared
    assert reg["expected"] == values["SHARED_DIR_ID"]
    assert reg["requests"] == ("50m", "64Mi")                         # 설계 10 축소값
    assert _pull_policies(reg["dep"]) == ["IfNotPresent"] * 2         # 사이트 패치가 pull 정책을 바꾸지 않는다(7-4 의존 규칙)
    assert re.search(r'(?m)^            cpu: "1"\n            memory: 512Mi$', reg["dep"])   # limits 는 base 그대로
    for layer in ("app", "platform/priorityclass", "platform/registry"):
        assert (out / layer / "kube-context").read_text().strip().endswith("dmsk8s"), layer
    env = _render_env(out)
    assert (env["REGISTRY_MODE"], env["REGISTRY"], env["PLATFORM_LAYERS"]) == (
        values["REGISTRY_MODE"], values["REGISTRY"], "priorityclass registry")
    doc(_kustomize(out / "platform" / "priorityclass"), "PriorityClass", "dms-addon-critical")


@needs_kubectl
def test_testbed_rendered_app_is_the_committed_overlay(tmp_path):
    r, out = _render(tmp_path, "testbed-k8s")
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert _kustomize(out / "app") == _kustomize(TESTBED)


def test_testbed_values_env_records_the_overlay_registry():
    # T4 동안 앱은 pkg-01:5001 그대로(external, 병행). 전환(별도 단계)은 values + 오버레이 다섯 곳을 한 커밋에서 바꾼다 — 그 뒤에는
    # in-cluster 쪽 단언이 선다(이 테스트는 두 상태를 다 안다; 전환 때 고칠 것은 tests/test_testbed_k8s_overlay.py 의 레지스트리 상수).
    values = _values_env(TESTBED / "values.env")
    assert values["REGISTRY_TLS_VERIFY"] == "false"
    cfg = dict(re.findall(r'(?m)^  (DMS_[A-Z0-9_]+): "([^"]*)"', (TESTBED / "patch-config.yaml").read_text()))
    if values["REGISTRY_MODE"] == "in-cluster":
        assert values["REGISTRY"] == f"{values['REGISTRY_CLUSTER_IP']}:{values['REGISTRY_PORT']}"
        assert cfg["DMS_BUILD_BUILDER_IMAGE"].split("/", 1)[0] == values["REGISTRY"]
    else:
        assert (values["REGISTRY_MODE"], values["REGISTRY"]) == ("external", "pkg-01:5001")
    kust = (TESTBED / "kustomization.yaml").read_text()
    hosts = {n.split("/", 1)[0] for n in re.findall(r"(?m)^\s*newName: (\S+)$", kust)}
    assert hosts == {values["REGISTRY"]}
    assert cfg["DMS_BUILD_REGISTRY"] == values["REGISTRY"]
    assert cfg["DMS_JOB_IMAGE"].split("/", 1)[0] == values["REGISTRY"]


@needs_kubectl
def test_prod_in_cluster_render_puts_one_address_in_all_five_places(tmp_path):
    r, out = _render(tmp_path, "prod", _prod_values())
    assert r.returncode == 0, (r.stdout, r.stderr)
    addr = "10.96.0.50:5000"
    assert _render_env(out)["REGISTRY"] == addr                      # REGISTRY 를 비우면 레지스트리 주소로 채운다
    app = _kustomize(out / "app")
    app_images = re.findall(r"(?m)^\s*image: (\S+)$", app)
    assert app_images and {i.split("/", 1)[0] for i in app_images} == {addr}
    data = config_data(app)
    assert data["DMS_BUILD_REGISTRY"] == addr
    assert data["DMS_JOB_IMAGE"].startswith(f"{addr}/dms-mpifileutils:")
    assert data["DMS_BUILD_BUILDER_IMAGE"] == f"{addr}/buildah:stable"
    reg = _registry_render(out)
    assert reg["cluster_ip"] == "10.96.0.50" and reg["port"] == "5000"
    assert {i.split("/", 1)[0] for i in reg["images"]} == {addr}
    assert reg["hostpaths"] == [("/srv/dms_shared/dms/registry", "Directory", "registry-data")]
    assert reg["expected"] == "0123abcd"
    assert reg["requests"] == ("100m", "128Mi")                      # 선택 키가 없으면 base(설계 2-1)
    assert _pull_policies(reg["dep"]) == ["IfNotPresent"] * 2


@needs_kubectl
def test_registry_port_reaches_the_service_and_the_address(tmp_path):
    r, out = _render(tmp_path, "prod", _prod_values(REGISTRY_PORT="5050"))
    assert r.returncode == 0, (r.stdout, r.stderr)
    reg = _registry_render(out)
    assert reg["port"] == "5050" and reg["images"][0] == f"10.96.0.50:5050/registry:{REGISTRY_TAG}"
    assert re.search(r"(?m)^    targetPort: http$", doc(reg["text"], "Service", "dms-registry"))   # 컨테이너는 :5000 그대로
    assert config_data(_kustomize(out / "app"))["DMS_BUILD_REGISTRY"] == "10.96.0.50:5050"


def test_external_without_a_cluster_ip_renders_no_registry_layer(tmp_path):
    r, out = _render(tmp_path, "prod", _prod_values(REGISTRY_MODE="external", REGISTRY="registry.corp.example:5000",
                                                    REGISTRY_CLUSTER_IP="REPLACE_REGISTRY_CLUSTER_IP",
                                                    SHARED_DIR_ID="REPLACE_SHARED_DIR_ID"))
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert not (out / "platform" / "registry").exists() and (out / "platform" / "priorityclass").is_dir()
    assert "platform/registry" not in r.stdout
    env = _render_env(out)
    assert (env["REGISTRY"], env["REGISTRY_CLUSTER_IP"], env["PLATFORM_LAYERS"]) == (
        "registry.corp.example:5000", "", "priorityclass")


def test_old_values_env_without_registry_mode_is_external(tmp_path):
    r, out = _render(tmp_path, "prod", _prod_values(REGISTRY_MODE=None, REGISTRY="registry.corp.example",
                                                    REGISTRY_CLUSTER_IP=None, SHARED_DIR_ID=None))
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "REGISTRY_MODE 가 없다" in r.stderr and _render_env(out)["REGISTRY_MODE"] == "external"


def _assert_failed(r, out, *needles):
    assert r.returncode == 2, (r.stdout, r.stderr)
    for n in needles:
        assert n in r.stdout, (n, r.stdout)
    assert not out.exists()


@pytest.mark.parametrize("ip", ["10.96.0", "10.96.0.256", "10.096.0.1", "a.b.c.d", "10.96.0.50/32", "10.96..1",
                                "0.1.2.3", "127.0.0.1", "224.0.0.1", "10.96.0.50:5000", "1.2.3.4.5"])
def test_render_rejects_malformed_cluster_ip(tmp_path, ip):
    r, out = _render(tmp_path, "prod", _prod_values(REGISTRY_CLUSTER_IP=ip))
    _assert_failed(r, out, "REGISTRY_CLUSTER_IP")


@pytest.mark.parametrize("port", ["0", "65536", "05000", "x", "50 00"])
def test_render_rejects_malformed_port(tmp_path, port):
    r, out = _render(tmp_path, "prod", _prod_values(REGISTRY_PORT=f"'{port}'"))
    _assert_failed(r, out, "REGISTRY_PORT")


def test_in_cluster_registry_must_be_the_registry_address(tmp_path):
    r, out = _render(tmp_path, "prod", _prod_values(REGISTRY="registry.corp.example:5000"))
    _assert_failed(r, out, "REGISTRY_MODE=in-cluster", "10.96.0.50:5000")
    r, out = _render(tmp_path / "same", "prod", _prod_values(REGISTRY="10.96.0.50:5000"))
    assert r.returncode == 0, (r.stdout, r.stderr)


@pytest.mark.parametrize("registry", ["reg.corp/k8s", "http://reg.corp", "reg.corp:5000:1", ":5000", "reg corp"])
def test_registry_is_host_port_only(tmp_path, registry):
    r, out = _render(tmp_path, "prod", _prod_values(REGISTRY_MODE="external", REGISTRY=f"'{registry}'"))
    _assert_failed(r, out, "REGISTRY")


@pytest.mark.parametrize("values, needle", [
    ({"REGISTRY_TLS_VERIFY": "true"}, "REGISTRY_TLS_VERIFY=false"),
    ({"REGISTRY_TLS_VERIFY": "yes"}, "REGISTRY_TLS_VERIFY"),
    ({"REGISTRY_MODE": "incluster"}, "REGISTRY_MODE"),
    ({"REGISTRY_CLUSTER_IP": "REPLACE_REGISTRY_CLUSTER_IP"}, "REGISTRY_CLUSTER_IP"),
    ({"SHARED_DIR_ID": "REPLACE_SHARED_DIR_ID"}, "SHARED_DIR_ID"),
    ({"SHARED_DIR_ID": "'a b'"}, "SHARED_DIR_ID"),
    ({"SHARED_DIR_ID": "'a|b'"}, "SHARED_DIR_ID"),
    ({"SHARED_DIR_ID": "a/b"}, "SHARED_DIR_ID"),
    ({"REGISTRY_CPU_REQUEST": "0m"}, "REGISTRY_CPU_REQUEST"),
    ({"REGISTRY_CPU_REQUEST": "m"}, "REGISTRY_CPU_REQUEST"),
    ({"REGISTRY_CPU_REQUEST": "5m0"}, "REGISTRY_CPU_REQUEST"),
    ({"REGISTRY_MEMORY_REQUEST": "64M"}, "REGISTRY_MEMORY_REQUEST"),
    ({"REGISTRY_MEMORY_REQUEST": "064Mi"}, "REGISTRY_MEMORY_REQUEST"),
    ({"REGISTRY_MEMORY_REQUEST": "Mi"}, "REGISTRY_MEMORY_REQUEST"),
])
def test_render_rejects_bad_registry_values(tmp_path, values, needle):
    r, out = _render(tmp_path, "prod", _prod_values(**values))
    _assert_failed(r, out, needle)


@needs_kubectl
@pytest.mark.parametrize("cpu, mem", [("50m", "64Mi"), ("1", "1Gi"), ("250m", "512Ki")])
def test_render_accepts_well_formed_requests(tmp_path, cpu, mem):
    r, out = _render(tmp_path, "prod", _prod_values(REGISTRY_CPU_REQUEST=cpu, REGISTRY_MEMORY_REQUEST=mem))
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert _registry_render(out)["requests"] == (cpu, mem)


def test_in_cluster_missing_cluster_ip_does_not_blame_the_empty_registry(tmp_path):
    # 2026-11-03 ④ 리뷰 2: in-cluster 면 REGISTRY 는 비워 두라고 한다(원천 = REGISTRY_CLUSTER_IP) — 그 키를 미치환으로 지목하면 운영자가
    # REGISTRY 를 채우고, 다음 렌더가 「레지스트리 주소와 다르다」로 또 막힌다. 사람이 옮겨 적는 키에는 값을 어디서 얻는지 한 줄씩.
    r, out = _render(tmp_path, "prod", _prod_values(REGISTRY_CLUSTER_IP="REPLACE_REGISTRY_CLUSTER_IP"))
    _assert_failed(r, out, "미치환", "ServiceCIDR 안의 미사용 IP")
    first = r.stdout.splitlines()[0]
    assert first.startswith("FAIL: values.env 미치환/빈 값 -->") and first.split("-->")[1].split() == ["REGISTRY_CLUSTER_IP"], first
    r, out = _render(tmp_path, "prod", _prod_values(SHARED_DIR_ID="REPLACE_SHARED_DIR_ID"))
    _assert_failed(r, out, "SHARED_DIR_ID", "dms/.dms-shared-id")
    # external 이면 REGISTRY 가 원천이라 여전히 지목한다.
    r, out = _render(tmp_path, "prod", _prod_values(REGISTRY_MODE="external", REGISTRY_CLUSTER_IP=None, SHARED_DIR_ID=None))
    _assert_failed(r, out, "미치환")
    assert r.stdout.splitlines()[0].split("-->")[1].split() == ["REGISTRY"], r.stdout


def _config_sum(shared_dir_id: str) -> str:
    import hashlib
    body = (REGISTRY_BASE / "config.yml").read_bytes() + f"expected-registry-id={shared_dir_id}\n".encode()
    return hashlib.sha256(body).hexdigest()


def _config_annotation(dep: str) -> str:
    tmpl_meta = _block(_block(dep, "template"), "metadata")
    m = re.search(r'(?m)^\s*dms\.io/config-sha256: "?([0-9a-f]+)"?$', _block(tmpl_meta, "annotations"))
    assert m, tmpl_meta
    return m.group(1)


@needs_kubectl
def test_config_change_rolls_the_registry_pod(tmp_path):
    # 2026-11-03 ④ 리뷰 2: config.yml·expected-registry-id 는 기동 때만 읽힌다 — ConfigMap 이름이 고정이라(GC 런북) 설정만 바뀐 SSA 재적용은
    # 파드를 다시 띄우지 않는다. 렌더러가 두 입력의 sha256 을 파드 템플릿 annotation 에 넣어 설정이 바뀌면 롤링 교체되게 한다.
    r, out = _render(tmp_path, "prod", _prod_values())
    assert r.returncode == 0, (r.stdout, r.stderr)
    first = _config_annotation(_registry_render(out)["dep"])
    assert first == _config_sum("0123abcd")
    r, out = _render(tmp_path, "prod", _prod_values())
    assert _config_annotation(_registry_render(out)["dep"]) == first          # 같은 입력 → 같은 값(재적용이 공연히 롤링하지 않는다)
    r, out = _render(tmp_path, "prod", _prod_values(SHARED_DIR_ID="feedbeef"))
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert _config_annotation(_registry_render(out)["dep"]) == _config_sum("feedbeef") != first
    cfg = tmp_path / "deploy" / "platform" / "registry" / "config.yml"   # 렌더러 사본의 config.yml 만 바꾼다
    cfg.write_text(cfg.read_text().replace("age: 168h", "age: 72h"))
    r, out = _render(tmp_path, "prod", _prod_values(SHARED_DIR_ID="feedbeef"))
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert _config_annotation(_registry_render(out)["dep"]) not in (first, _config_sum("feedbeef"))


def test_render_env_carries_the_resolved_app_tags(tmp_path):
    # install.sh 의 in-cluster 게이트가 이 값으로 레지스트리에 태그가 있는지 본다('live' 면 클러스터에서 읽은 태그 — 여기선 명시 태그).
    r, out = _render(tmp_path, "prod", _prod_values())
    assert r.returncode == 0, (r.stdout, r.stderr)
    env = _render_env(out)
    assert (env["DMS_TAG"], env["DMS_AGENT_TAG"], env["MFU_TAG"]) == ("v-dms-tag", "v-dms-agent-tag", "v-mfu-tag")


# --- install.sh 의 in-cluster 게이트(2026-11-03 ④ 리뷰 2) — kubectl·curl 은 가짜(클러스터를 보지 않는다) ------------------------------

_KUBECTL_SHIM = r'''#!/bin/sh
echo "$*" >> "$SHIM_LOG"
case "$*" in
  version) exit 0 ;;
  "get crd "*|"get ns "*|"get ingressclass "*) exit 0 ;;
  *"get secret dms-secrets -o go-template"*)
    printf 'DMS_DATABASE_URL=x\nDMS_SHARED_TOKEN=x\nDMS_ADMIN_TOKEN=x\nDMS_SESSION_SECRET=x\n'; exit 0 ;;
  *"get secret "*) exit 0 ;;
  *"get svc dms-registry"*) printf '%s' "$FAKE_SVC_IP"; exit 0 ;;
  *"get deploy dms-registry"*) printf '1'; exit 0 ;;
  *"port-forward svc/dms-registry "*)
    if [ -n "${FAKE_PF_FAIL:-}" ]; then echo "error: unable to forward port" >&2; exit 1; fi
    echo "Forwarding from 127.0.0.1:41234 -> 5000"; echo "Forwarding from [::1]:41234 -> 5000"; exec sleep 20 ;;
  *"apply -k "*"--dry-run=server"*) exit 0 ;;
esac
echo "unexpected kubectl $*" >&2; exit 97
'''

_CURL_SHIM = r'''#!/bin/sh
echo "$*" >> "$CURL_LOG"
for a; do url=$a; done
p=${url#http://127.0.0.1:41234/v2/}
[ "$p" != "$url" ] || { printf 000; exit 7; }
repo=${p%%/manifests/*}; tag=${p##*/manifests/}
case " $PRESENT " in *" $repo:$tag "*) printf 200 ;; *) printf 404 ;; esac
'''


def _install_dry_run(tmp_path, present, **env_extra):
    root = _tree(tmp_path)
    shutil.copy(DEPLOY / "install.sh", root / "install.sh")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in (("kubectl", _KUBECTL_SHIM), ("curl", _CURL_SHIM)):
        (bindir / name).write_text(body)
        (bindir / name).chmod(0o755)
    vals = tmp_path / "prod.values.env"
    vals.write_text(_prod_values())
    env = {"PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}", "HOME": str(tmp_path),
           "VALUES_ENV": str(vals), "SHIM_LOG": str(tmp_path / "kubectl.log"), "CURL_LOG": str(tmp_path / "curl.log"),
           "FAKE_SVC_IP": "10.96.0.50", "PRESENT": present, **env_extra}
    r = subprocess.run(["sh", str(root / "install.sh"), "--dry-run"], env=env, capture_output=True, text=True, timeout=60)
    log = lambda n: (tmp_path / n).read_text().splitlines() if (tmp_path / n).exists() else []   # noqa: E731
    return r, log("kubectl.log"), log("curl.log")


ALL_TAGS = "dms:v-dms-tag dms-agent:v-dms-agent-tag dms-mpifileutils:v-mfu-tag"


def test_install_in_cluster_gate_passes_when_every_app_tag_is_in_the_registry(tmp_path):
    r, kcalls, curls = _install_dry_run(tmp_path, ALL_TAGS)
    assert r.returncode == 0, (r.stdout, r.stderr)
    for spec in ALL_TAGS.split():
        assert f"레지스트리에 {spec} 있음" in r.stdout
    assert [c for c in kcalls if "port-forward" in c] == ["-n dms port-forward svc/dms-registry :5000"]   # 무작위 로컬 포트
    assert len(curls) == 3 and all(" -I " in f" {c} " and "application/vnd.oci.image.manifest.v1+json" in c for c in curls), curls
    assert any("apply -k" in c and "--dry-run=server" in c for c in kcalls)


def test_install_in_cluster_gate_stops_before_apply_when_s5_is_missing(tmp_path):
    # S5(DMS 이미지 push)를 빠뜨렸으면 migrate 가 ImagePullBackOff → 180초 타임아웃으로 끝나던 것을 apply 전 한 줄 사유로.
    r, kcalls, _ = _install_dry_run(tmp_path, "dms:v-dms-tag dms-mpifileutils:v-mfu-tag")
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert "S5 미완" in r.stderr and "dms-agent:v-dms-agent-tag(HTTP 404)" in r.stderr, r.stderr
    assert not any("apply -k" in c for c in kcalls), kcalls


def test_install_in_cluster_gate_reports_a_port_forward_that_never_listens(tmp_path):
    r, kcalls, curls = _install_dry_run(tmp_path, ALL_TAGS, FAKE_PF_FAIL="1")
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert "port-forward 가 열리지 않았다" in r.stderr and "unable to forward port" in r.stderr, r.stderr
    assert curls == [] and not any("apply -k" in c for c in kcalls)


def test_registry_values_in_the_callers_environment_are_ignored(tmp_path):
    # values.env 만 원천(③ 의 SHARED_DIR 와 같은 이유) — 셸에 남은 REGISTRY_CLUSTER_IP 가 빠진 줄을 채우지 않는다.
    r, out = _render(tmp_path, "prod", _prod_values(REGISTRY_CLUSTER_IP=None), REGISTRY_CLUSTER_IP="10.96.0.99",
                     SHARED_DIR_ID="zzz")
    _assert_failed(r, out, "미치환", "REGISTRY_CLUSTER_IP")


@pytest.mark.parametrize("key, value", [("DMS_TAG", "d99"), ("DMS_AGENT_TAG", "d99"), ("MFU_TAG", "d99"),
                                        ("PORTAL_VIP", "198.51.100.7"), ("PORTAL_DOMAIN", "x.example"),
                                        ("EMAIL_DOMAIN", "x.example"), ("LOCAL_ADMIN", "root")])
def test_template_values_in_the_callers_environment_are_ignored(tmp_path, key, value):
    # 2026-11-03 ④ 리뷰: 머리 주석 「셸 환경의 같은 이름 변수는 읽지 않는다」가 태그·도메인·VIP 에도 서야 한다 — prod README 1단계가
    # `PORTAL_VIP=… sh preflight-cluster.sh` 처럼 같은 이름을 env 로 쓰게 해 셸에 남아 있을 수 있다.
    r, out = _render(tmp_path, "prod", _prod_values(**{key: None}), **{key: value})
    _assert_failed(r, out, "미치환", key)


def test_failed_render_leaves_the_previous_result_untouched(tmp_path):
    r, out = _render(tmp_path, "prod", _prod_values())
    assert r.returncode == 0, (r.stdout, r.stderr)
    before = (out / "render.env").read_text()
    r, _ = _render(tmp_path, "prod", _prod_values(REGISTRY_CLUSTER_IP="999.1.1.1"))
    assert r.returncode == 2
    assert (out / "render.env").read_text() == before
    assert not [p for p in (out.parent).iterdir() if p.name.startswith(".prod.tmp")]   # 임시 디렉터리를 남기지 않는다


@pytest.mark.parametrize("site", ["", "../prod", "Prod", "nope", "-x"])
def test_unknown_or_malformed_site_is_rejected(tmp_path, site):
    root = _tree(tmp_path)
    r = subprocess.run(["sh", str(root / "render.sh"), site], capture_output=True, text=True, timeout=30,
                       env={"PATH": "/usr/bin:/bin"})
    assert r.returncode == 2, (site, r.stdout, r.stderr)
    assert not (root / ".rendered").exists()


# --- 고정 오버레이(testbed-k8s) 대조 — 기록이 오버레이와 어긋나면 렌더가 멈춘다 ------------------------------------------------------

def _testbed_values(**overrides) -> str:
    text = (TESTBED / "values.env").read_text()
    for key, value in overrides.items():
        text = re.sub(rf"(?m)^{key}=\S*", lambda _m: f"{key}={value}", text)
    return text


@pytest.mark.parametrize("values, needle", [
    ({"REGISTRY": "pkg-01:5000"}, "REGISTRY"),
    ({"SHARED_DIR": "/elsewhere"}, "DMS_ARTIFACT_BASE_URI"),
])
def test_testbed_record_must_match_the_overlay(tmp_path, values, needle):
    r, out = _render(tmp_path, "testbed-k8s", _testbed_values(**values))
    _assert_failed(r, out, needle)


def test_testbed_mode_flip_without_the_overlay_is_refused(tmp_path):
    # values.env 의 REGISTRY_MODE 만 바꾸고 오버레이(newName 등)를 그대로 두면 렌더가 멈춘다 — 전환은 한 커밋에서.
    current = _values_env(TESTBED / "values.env")
    if current["REGISTRY_MODE"] == "external":
        flipped = _testbed_values(REGISTRY_MODE="in-cluster")          # REGISTRY 가 pkg-01:5001 이라 레지스트리 주소와 다르다
        needle = "REGISTRY_MODE=in-cluster"
    else:
        flipped = _testbed_values(REGISTRY_MODE="external", REGISTRY="pkg-01:5001")   # 오버레이는 이미 새 주소
        needle = "newName"
    r, out = _render(tmp_path, "testbed-k8s", flipped)
    _assert_failed(r, out, needle)


_BEFORE_SWITCH = pytest.mark.skipif(
    _values_env(TESTBED / "values.env")["REGISTRY_MODE"] != "external",
    reason="testbed-k8s 가 이미 in-cluster 로 전환됐다 — 커밋된 오버레이 자체를 위 렌더 테스트들이 본다")


def _switch_overlay(root: Path, builder_host: str):
    """T4 뒤 전환 단계의 모양: 오버레이 newName·DMS_JOB_IMAGE·DMS_BUILD_REGISTRY·DMS_BUILD_BUILDER_IMAGE 를 새 주소로."""
    ov = root / "overlays" / "testbed-k8s"
    for name in ("kustomization.yaml", "patch-config.yaml"):
        p = ov / name
        text = p.read_text().replace("pkg-01:5001", "10.112.0.50:5000")
        text = text.replace('DMS_BUILD_BUILDER_IMAGE: "pkg-01:5000/buildah:stable"',
                            f'DMS_BUILD_BUILDER_IMAGE: "{builder_host}/buildah:stable"')
        p.write_text(text)


@_BEFORE_SWITCH
@needs_kubectl
def test_testbed_switch_to_the_in_cluster_registry_renders_one_address(tmp_path):
    _switch_overlay(_tree(tmp_path), "10.112.0.50:5000")
    r, out = _render(tmp_path, "testbed-k8s", _testbed_values(REGISTRY_MODE="in-cluster", REGISTRY="10.112.0.50:5000"))
    assert r.returncode == 0, (r.stdout, r.stderr)
    data = config_data(_kustomize(out / "app"))
    assert data["DMS_BUILD_REGISTRY"] == "10.112.0.50:5000"
    assert data["DMS_BUILD_BUILDER_IMAGE"] == "10.112.0.50:5000/buildah:stable"


@_BEFORE_SWITCH
def test_testbed_switch_must_move_the_builder_too(tmp_path):
    _switch_overlay(_tree(tmp_path), "pkg-01:5000")
    r, out = _render(tmp_path, "testbed-k8s", _testbed_values(REGISTRY_MODE="in-cluster", REGISTRY="10.112.0.50:5000"))
    _assert_failed(r, out, "DMS_BUILD_BUILDER_IMAGE")
