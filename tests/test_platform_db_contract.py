"""DB 층(CNPG Cluster·정적 hostPath PV·pg_dump CronJob·경로 프로브) 계약(2026-11-03 ⑤, 설계 v6 6·8-1 S10·9 「테스트·게이트」 ⑤).

설계 9 ⑤ test_platform_db_contract 줄: instances 3, ANY 1 preferred, required, toleration, PV 5개가 hostPath `Directory`·Retain·nodeAffinity 0·
claimRef 0·경로가 `<SHARED_DIR>/dms/pgdata/` 하위, **`storage.size` == PV capacity**, SC no-provisioner, `resizeInUseVolumes: false`, **모든 렌더
오버레이(prod·testbed-k8s)에 로컬 디스크 PV·local-path 0건**(③ 의 같은 접두 조건과 함께 — 모든 DMS 상태 경로가 같은 `<SHARED_DIR>/dms/` 아래).
여기에 더해: CronJob 모양(매시간 17분·-Fc·-rw·워커·uid 26·비밀은 secretKeyRef·보존 개수)과 그 스크립트를 실제 sh 로 돌린 동작(센티널 검사만·
보존은 성공 뒤·조각 정리), 경로 프로브 DaemonSet 의 모양·스크립트, deploy/pg-path-probe.py 의 판정, 렌더러의 DB 값 검사.

PyYAML 없이(저장소 의존성이 아니다) kustomize 출력의 고정된 모양에 기대는 최소 정규식이다. 렌더는 실제 렌더러(사본 트리) + `kubectl kustomize`
로컬 렌더뿐이다(KUBECONFIG=/dev/null — 클러스터를 보지 않는다).
"""
import importlib.util
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from overlay_render import KUBECTL, OVERLAYS, REPO_ROOT, config_data, doc, docs, needs_kubectl

DEPLOY = REPO_ROOT / "deploy"
DB = DEPLOY / "platform" / "db"
ENV = {**os.environ, "KUBECONFIG": os.devnull}
PLACEHOLDER = "set-by-overlay.invalid"
PVS = [f"dms-pgdata-0{n}" for n in range(1, 6)]


def _kustomize(path: Path) -> str:
    r = subprocess.run([KUBECTL, "kustomize", str(path)], capture_output=True, text=True, timeout=180, env=ENV)
    assert r.returncode == 0, f"kubectl kustomize {path} 실패: {r.stderr}"
    return r.stdout


def _values_env(path: Path) -> dict:
    return dict(re.findall(r"(?m)^([A-Z_]+)=(\S*)", path.read_text(encoding="utf-8")))


def _tree(tmp_path: Path) -> Path:
    root = tmp_path / "deploy"
    if not root.exists():
        root.mkdir(parents=True)
        shutil.copy(DEPLOY / "render.sh", root / "render.sh")
        for d in ("platform", "k8s", "overlays", "addons"):
            shutil.copytree(DEPLOY / d, root / d, ignore=shutil.ignore_patterns(".prod-rendered", "values.env.local"))
        for name in ("versions.env", "images.lock"):
            shutil.copy(DEPLOY / name, root / name)
    return root


def _render(tmp_path: Path, site: str, values_text=None, **env_extra):
    root = _tree(tmp_path)
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(tmp_path), **env_extra}
    if values_text is not None:
        vals = tmp_path / f"{site}.values.env"
        vals.write_text(values_text)
        env["VALUES_ENV"] = str(vals)
    r = subprocess.run(["sh", str(root / "render.sh"), site], env=env, capture_output=True, text=True, timeout=60)
    return r, root / ".rendered" / site


def prod_values(**overrides) -> str:
    text = (OVERLAYS / "prod" / "values.env.example").read_text()
    text = re.sub(r"\bREPLACE_([A-Z_]+)", lambda m: "v-" + m.group(1).lower().replace("_", "-"), text)
    text = re.sub(r"(?m)^SHARED_DIR=\S+", "SHARED_DIR=/srv/dms_shared", text)
    text = re.sub(r"(?m)^REGISTRY_CLUSTER_IP=\S+", "REGISTRY_CLUSTER_IP=10.96.0.50", text)
    text = re.sub(r"(?m)^SHARED_DIR_ID=\S+", "SHARED_DIR_ID=0123abcd", text)
    for key, value in overrides.items():
        if value is None:
            text = re.sub(rf"(?m)^#? ?{key}=.*$", "", text)
        elif re.search(rf"(?m)^#? ?{key}=", text):
            text = re.sub(rf"(?m)^#? ?{key}=\S*", lambda _m: f"{key}={value}", text)
        else:
            text += f"\n{key}={value}\n"
    return text


def _render_env(out: Path) -> dict:
    return dict(re.findall(r'(?m)^([A-Z_]+)="?([^"\n]*)"?$', (out / "render.env").read_text()))


def _block(text: str, key: str) -> str:
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


# --- 사이트 렌더(모듈 한 번) ------------------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def sites(tmp_path_factory):
    out = {}
    for site, values in (("testbed-k8s", None), ("prod", prod_values())):
        tmp = tmp_path_factory.mktemp(site)
        r, rendered = _render(tmp, site, values)
        assert r.returncode == 0, (r.stdout, r.stderr)
        layers = {name: _kustomize(rendered / "platform" / name) for name in ("db-volumes", "db-cluster", "db-backup", "db-probe")}
        layers["addons"] = _kustomize(rendered / "addons" / "cnpg")
        layers["app"] = _kustomize(rendered / "app")
        layers["registry"] = _kustomize(rendered / "platform" / "registry")
        env = _render_env(rendered)
        out[site] = {"dir": rendered, "layers": layers, "env": env, "stdout": r.stdout}
    return out


SITES = ("testbed-k8s", "prod")
SHARED = {"testbed-k8s": _values_env(OVERLAYS / "testbed-k8s" / "values.env")["SHARED_DIR"], "prod": "/srv/dms_shared"}


# --- base(사이트 중립) ------------------------------------------------------------------------------------------------------

@needs_kubectl
@pytest.mark.parametrize("layer", ["volumes", "cluster", "backup", "probe"])
def test_base_layers_carry_only_placeholders(layer):
    text = _kustomize(DB / layer)
    code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    assert PLACEHOLDER in code
    assert "/dms_shared" not in code and "pkg-01" not in code and "ghcr.io" not in code
    assert not re.search(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", code)
    for path in re.findall(r"(?m)^\s*path: (/\S+)$", code):
        assert path.startswith(f"/{PLACEHOLDER}/dms/"), path            # raw apply 는 없는 경로 — 파드가 뜨지 않는다
    for size in re.findall(r"(?m)^\s*(?:storage|size): (\S+)$", code):
        assert size == "set-by-overlay", size                           # raw apply 는 수량이 아니라 apiserver·웹훅이 거절한다
    for image in re.findall(r"(?m)^\s*(?:image|imageName): (\S+)$", code):
        assert image.startswith(PLACEHOLDER + "/"), image


def test_db_layers_live_outside_the_overlay_and_base_contract_dirs():
    assert all((DB / layer / "kustomization.yaml").is_file() for layer in ("volumes", "cluster", "backup", "probe"))
    assert not list(OVERLAYS.glob("*/db*")) and not (DEPLOY / "k8s" / "db").exists()


# --- Cluster(설계 6-1) -------------------------------------------------------------------------------------------------------

@needs_kubectl
@pytest.mark.parametrize("site", SITES)
def test_cluster_is_three_instances_quorum_sync_and_one_per_node(sites, site):
    cl = doc(sites[site]["layers"]["db-cluster"], "Cluster", "dms-pg")
    assert re.search(r"(?m)^  instances: 3$", cl)
    assert re.search(r"(?m)^    synchronous:\n      dataDurability: preferred\n      method: any\n      number: 1$", cl)   # #28
    aff = _block(cl, "affinity")
    assert re.search(r"(?m)^    podAntiAffinityType: required$", aff) and re.search(r"(?m)^    topologyKey: kubernetes.io/hostname$", aff)
    # 상류 CRD: enablePodAntiAffinity 가 명시적 false 면 required·topologyKey 는 무시된다(두 인스턴스가 한 마스터에 — #27 위반, 2026-11-03 ⑤ 리뷰).
    assert re.search(r"(?m)^    enablePodAntiAffinity: true$", aff), aff
    assert re.search(r"(?m)^    - effect: NoSchedule\n      key: node-role.kubernetes.io/control-plane\n      operator: Exists$", aff)
    has_selector = bool(re.search(r'(?m)^    nodeSelector:\n      node-role.kubernetes.io/control-plane: ""$', aff))
    # prod 는 마스터마다 1개(#27). 테스트베드만 all-nodes(control-plane 이 cp1 하나 — 설계 10 T6).
    assert has_selector == (site == "prod"), aff
    assert "nodeSelector" in aff or site != "prod"
    for key, value in (("smartShutdownTimeout", "0"), ("enablePDB", "true"), ("imagePullPolicy", "IfNotPresent"),
                       ("primaryUpdateMethod", "switchover"), ("primaryUpdateStrategy", "unsupervised")):
        assert re.search(rf"(?m)^  {key}: {value}$", cl), key
    assert re.search(r"(?m)^    initdb:\n      database: dms\n      owner: dms\n      secret:\n        name: dms-pg-app$", cl)
    assert "superuserSecret" not in cl and "enableSuperuserAccess: true" not in cl


@needs_kubectl
@pytest.mark.parametrize("site", SITES)
def test_cluster_image_is_the_pinned_pg17_from_the_lock(sites, site):
    cl = doc(sites[site]["layers"]["db-cluster"], "Cluster", "dms-pg")
    image = re.search(r"(?m)^  imageName: (\S+)$", cl).group(1)
    assert image == sites[site]["env"]["PG_IMAGE_REF"]
    assert re.fullmatch(r"[^/]+/postgresql:17\.\d+-\d{12}-minimal-trixie@sha256:[0-9a-f]{64}", image), image   # 6-1 함정 3·#27
    assert image.split("/", 1)[0] == sites[site]["env"]["ADDON_REGISTRY"]


@needs_kubectl
@pytest.mark.parametrize("site, cpu, mem, buffers", [("prod", '"1"', "4Gi", "1GB"), ("testbed-k8s", "100m", "256Mi", "64MB")])
def test_cluster_resources_and_shared_buffers(sites, site, cpu, mem, buffers):
    cl = doc(sites[site]["layers"]["db-cluster"], "Cluster", "dms-pg")
    assert re.search(rf"(?m)^  resources:\n    limits:\n      memory: {mem}\n    requests:\n      cpu: {cpu}\n      memory: {mem}$", cl)
    assert "      cpu:" not in _block(cl, "limits")                       # CPU limit 없음(2-1)
    assert re.search(rf"(?m)^      shared_buffers: {buffers}$", cl)


# --- 볼륨(설계 6-2) ----------------------------------------------------------------------------------------------------------

def _pvs(text: str) -> dict:
    out = {}
    for d in docs(text):
        if not re.search(r"(?m)^kind: PersistentVolume$", d):
            continue
        name = re.search(r"(?m)^  name: (\S+)$", d).group(1)
        out[name] = d
    return out


@needs_kubectl
@pytest.mark.parametrize("site", SITES)
def test_five_static_hostpath_pvs_under_shared_dir_pgdata(sites, site):
    pvs = _pvs(sites[site]["layers"]["db-volumes"])
    assert sorted(pvs) == PVS
    shared = SHARED[site]
    for name, d in pvs.items():
        m = re.search(r"(?m)^  hostPath:\n    path: (\S+)\n    type: (\S+)$", d)
        assert m and m.groups() == (f"{shared}/dms/pgdata/{name}", "Directory"), (name, m and m.groups())
        assert m.group(1).startswith(f"{shared}/dms/pgdata/")
        assert re.search(r"(?m)^  persistentVolumeReclaimPolicy: Retain$", d)
        assert re.search(r"(?m)^  storageClassName: dms-pg-shared$", d)
        assert re.search(r"(?m)^  accessModes:\n  - ReadWriteOnce$", d)
        assert "nodeAffinity" not in d and "claimRef" not in d, name   # 어느 노드에서든 같은 PGDATA, 재생성 때 자가 치유(6-2)
        assert re.search(r"(?m)^    dms\.io/pg-cluster: dms-pg$", d) and "app.kubernetes.io/managed-by: dms-install" in d


@needs_kubectl
@pytest.mark.parametrize("site", SITES)
def test_storage_size_equals_every_pv_capacity(sites, site):
    cl = doc(sites[site]["layers"]["db-cluster"], "Cluster", "dms-pg")
    size = re.search(r"(?m)^  storage:\n    resizeInUseVolumes: false\n    size: (\S+)\n    storageClass: dms-pg-shared$", cl)
    assert size, _block(cl, "storage")
    caps = {re.search(r"(?m)^  capacity:\n    storage: (\S+)$", d).group(1) for d in _pvs(sites[site]["layers"]["db-volumes"]).values()}
    assert caps == {size.group(1)} == {sites[site]["env"]["PG_STORAGE_SIZE"]}


@needs_kubectl
def test_one_value_sets_both_sizes(tmp_path):
    r, out = _render(tmp_path, "prod", prod_values(PG_STORAGE_SIZE="50Gi"))
    assert r.returncode == 0, (r.stdout, r.stderr)
    cl = _kustomize(out / "platform" / "db-cluster")
    assert re.search(r"(?m)^    size: 50Gi$", cl)
    assert set(re.findall(r"(?m)^    storage: (\S+)$", _kustomize(out / "platform" / "db-volumes"))) == {"50Gi"}


@needs_kubectl
@pytest.mark.parametrize("site", SITES)
def test_storage_class_is_static_retain_immediate(sites, site):
    sc = doc(sites[site]["layers"]["db-volumes"], "StorageClass", "dms-pg-shared")
    for line in ("provisioner: kubernetes.io/no-provisioner", "reclaimPolicy: Retain", "volumeBindingMode: Immediate",
                 "allowVolumeExpansion: false"):
        assert re.search(rf"(?m)^{re.escape(line)}$", sc), line


@needs_kubectl
@pytest.mark.parametrize("site", SITES)
def test_no_local_disk_pg_anywhere_and_every_state_path_shares_one_prefix(sites, site):
    # #31: 어느 렌더 오버레이에도 로컬 디스크 PG 를 두지 않는다 — local PV·local-path·경로를 만드는 hostPath 0건. 그 조건은 ③ 의 같은 접두
    # 조건과 함께라야 의미가 있다: DMS 상태 경로(api·controller 아티팩트·base·allowlist·레지스트리·백업·PV·프로브)가 모두 같은 <SHARED_DIR>/dms/ 아래.
    layers = sites[site]["layers"]
    every = "\n---\n".join(layers.values())
    assert not re.search(r"(?m)^\s+local:\s*$", every)                  # PV .spec.local
    assert "local-path" not in every and "rancher.io" not in every and "DirectoryOrCreate" not in every
    assert "openebs" not in every.lower() and "hostpath-provisioner" not in every
    prefix = f"{SHARED[site]}/dms/"
    data = config_data(layers["app"])
    state = [data["DMS_ARTIFACT_BASE_URI"][len("file://"):], data["DMS_ARTIFACT_BASE_ALLOWED_PREFIXES"]]
    for name in ("dms-api", "dms-controller"):
        state += re.findall(r"(?m)^      - hostPath:\n          path: (\S+)", doc(layers["app"], "Deployment", name))
    for layer in ("registry", "db-volumes", "db-backup", "db-probe"):
        state += re.findall(r"(?m)hostPath:\n\s+path: (/\S+)$", layers[layer])
    assert len(state) >= 2 + 2 + 1 + 5 + 1 + 5
    for path in state:
        assert path.startswith(prefix) and path != SHARED[site], (site, path)
    for layer in ("db-volumes", "db-backup", "db-probe", "registry"):
        assert set(re.findall(r"(?m)^\s+type: (Directory\S*|File\S*)$", layers[layer])) <= {"Directory"}, layer


# --- pg_dump CronJob(설계 6-4) -----------------------------------------------------------------------------------------------

@needs_kubectl
@pytest.mark.parametrize("site, keep", [("prod", "336"), ("testbed-k8s", "24")])
def test_cronjob_shape(sites, site, keep):
    cj = doc(sites[site]["layers"]["db-backup"], "CronJob", "dms-pg-dump")
    assert re.search(r'(?m)^  schedule: 17 \* \* \* \*$', cj) and re.search(r"(?m)^  concurrencyPolicy: Forbid$", cj)
    assert re.search(r"(?m)^            image: (\S+)$", cj).group(1) == sites[site]["env"]["PG_IMAGE_REF"]
    assert re.findall(r"(?m)^\s*imagePullPolicy: (\S+)$", cj) == ["IfNotPresent"]
    assert re.search(r"(?m)^          - hostPath:\n              path: (\S+)\n              type: Directory\n            name: backup$", cj).group(1) \
        == f"{SHARED[site]}/dms/backup/pg"
    assert re.search(r"(?m)^            runAsGroup: 26\n            runAsNonRoot: true\n            runAsUser: 26$", cj)
    assert re.search(r"(?m)^                  - key: node-role.kubernetes.io/control-plane\n                    operator: DoesNotExist$", cj)
    assert "tolerations" not in cj
    assert re.search(r'(?m)^              limits:\n                cpu: "1"\n                memory: 1Gi\n              requests:\n'
                     r"                cpu: 100m\n                memory: 256Mi$", cj)
    env = _block(cj, "env")
    for name, value in (("PGHOST", "dms-pg-rw"), ("PGDATABASE", "dms"), ("PGSSLMODE", "require"), ("DMS_BACKUP_KEEP", f'"{keep}"')):
        assert re.search(rf"(?m)^            - name: {name}\n              value: {re.escape(value)}$", env), name
    for name, key in (("PGUSER", "username"), ("PGPASSWORD", "password")):
        assert re.search(rf"(?m)^            - name: {name}\n              valueFrom:\n                secretKeyRef:\n"
                         rf"                  key: {key}\n                  name: dms-pg-app$", env), name
    assert "--format=custom" in cj and "set -x" not in cj
    assert not re.search(r"(?m)^\s*kind: Secret$", sites[site]["layers"]["db-backup"])


def test_backup_sentinel_value_is_the_one_s0_writes():
    s0 = (DEPLOY / "shared-dir-init.sh").read_text()
    m = re.search(r"(?m)^_sentinel dms/backup/pg/\.dms-backup-id (\S+)$", s0)
    assert m, "shared-dir-init.sh 가 backup/pg/.dms-backup-id 를 쓰지 않는다"
    cj = (DB / "backup" / "cronjob.yaml").read_text()
    assert re.search(rf"(?m)^                - name: DMS_BACKUP_SENTINEL\n                  value: {re.escape(m.group(1))}$", cj)
    tree = re.search(r'(?m)^dms/backup/pg (\d+) (\d+) (\d+)"?$', s0)
    assert tree and tree.groups() == ("26", "26", "700")                # 덤프 파드 uid 26 이 그 디렉터리 소유자


def _script(path: Path, marker: str) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.strip() == marker)
    bar = next(i for i in range(start, len(lines)) if lines[i].strip() == "- |")
    indent = len(lines[bar + 1]) - len(lines[bar + 1].lstrip())
    body = []
    for ln in lines[bar + 1:]:
        if ln.strip() and len(ln) - len(ln.lstrip()) < indent:
            break
        body.append(ln[indent:])
    return "\n".join(body) + "\n"


FAKE_PG_DUMP = r"""#!/bin/sh
[ -z "${FAKE_FAIL:-}" ] || { echo "pg_dump: error: connection refused" >&2; exit 1; }
for a; do case "$a" in --file=*) f=${a#--file=} ;; esac; done
printf 'PGDMP fake %s %s\n' "$PGHOST" "$PGDATABASE" > "$f"
"""
FAKE_PG_RESTORE = r"""#!/bin/sh
[ "$1" = "--list" ] || exit 2
head -c 5 "$2" | grep -q PGDMP
"""


def _run_dump(tmp_path, setup=None, keep="3", shims=None, **extra):
    backup = tmp_path / "backup"
    backup.mkdir(exist_ok=True)
    if setup:
        setup(backup)
    bindir = tmp_path / "fakebin"
    bindir.mkdir(exist_ok=True)
    for name, body in (("pg_dump", FAKE_PG_DUMP), ("pg_restore", FAKE_PG_RESTORE), *(shims or {}).items()):
        (bindir / name).write_text(body)
        (bindir / name).chmod(0o755)
    script = _script(DB / "backup" / "cronjob.yaml", "- name: pg-dump").replace("d=/backup", f"d={backup}")
    env = {"PATH": f"{bindir}:/usr/bin:/bin", "PGHOST": "dms-pg-rw", "PGDATABASE": "dms", "PGPASSWORD": "s3cret-value",
           "DMS_BACKUP_SENTINEL": "dms-backup-pg", "DMS_BACKUP_KEEP": keep, **extra}
    r = subprocess.run(["sh", "-c", script], env=env, capture_output=True, text=True, timeout=60)
    return r, backup


def _sentinel(d: Path, value="dms-backup-pg"):
    (d / ".dms-backup-id").write_text(value + "\n")


def _dumps(d: Path) -> list:
    return sorted(p.name for p in d.iterdir() if re.fullmatch(r"dms-\d{8}T\d{6}Z\.dump", p.name))


def test_dump_writes_one_verified_dump_and_never_echoes_the_password(tmp_path):
    r, d = _run_dump(tmp_path, _sentinel)
    assert r.returncode == 0, (r.stdout, r.stderr)
    dumps = _dumps(d)
    assert len(dumps) == 1 and (d / dumps[0]).read_text().startswith("PGDMP fake dms-pg-rw dms")
    assert not [p for p in d.iterdir() if p.name.endswith(".partial")]
    assert "s3cret-value" not in r.stdout + r.stderr
    assert (d / ".dms-backup-id").read_text() == "dms-backup-pg\n"


@pytest.mark.parametrize("case, setup, needle", [
    ("missing", None, "센티널 없음"),
    ("mismatch", lambda d: _sentinel(d, "other"), "센티널 불일치"),
    ("symlink", lambda d: (d / ".dms-backup-id").symlink_to(d / "elsewhere"), "일반 파일이 아님"),
])
def test_dump_refuses_without_the_s0_sentinel_and_creates_nothing(tmp_path, case, setup, needle):
    r, d = _run_dump(tmp_path, setup)
    assert r.returncode == 1 and needle in r.stderr, (case, r.stdout, r.stderr)
    assert _dumps(d) == [] and not [p for p in d.iterdir() if p.name.endswith(".partial")]
    if case == "missing":
        assert not (d / ".dms-backup-id").exists()                      # 만들지 않는다(마운트 빠진 노드에서 무력해진다)


@pytest.mark.parametrize("keep", ["", "0", "x", "-1"])
def test_dump_refuses_a_bad_keep_count(tmp_path, keep):
    r, d = _run_dump(tmp_path, _sentinel, keep=keep)
    assert r.returncode == 1 and "DMS_BACKUP_KEEP" in r.stderr and _dumps(d) == []


def test_retention_keeps_the_newest_and_touches_nothing_else(tmp_path):
    old = ["dms-20260101T001700Z.dump", "dms-20260101T011700Z.dump", "dms-20260101T021700Z.dump", "dms-20260101T031700Z.dump"]

    def setup(d):
        _sentinel(d)
        for name in old:
            (d / name).write_text("PGDMP old\n")
        (d / "manual-before-upgrade.dump").write_text("keep me\n")
        (d / "dms-latest.dump").write_text("not our format\n")

    r, d = _run_dump(tmp_path, setup, keep="3")
    assert r.returncode == 0, (r.stdout, r.stderr)
    dumps = _dumps(d)
    assert len(dumps) == 3 and dumps[:2] == old[2:] and dumps[2] not in old    # 새 덤프 + 최신 옛 것 둘
    assert (d / "manual-before-upgrade.dump").exists() and (d / "dms-latest.dump").exists() and (d / ".dms-backup-id").exists()
    assert r.stdout.count("보존 개수(3) 밖 삭제") == 2


def test_failed_dump_keeps_every_old_dump_and_leaves_no_partial(tmp_path):
    def setup(d):
        _sentinel(d)
        for h in range(5):
            (d / f"dms-20260101T0{h}1700Z.dump").write_text("PGDMP old\n")

    r, d = _run_dump(tmp_path, setup, keep="1", FAKE_FAIL="1")
    assert r.returncode == 1 and "보존분은 지우지 않았다" in r.stderr
    assert len(_dumps(d)) == 5 and not [p for p in d.iterdir() if p.name.endswith(".partial")]


# PATH 앞의 가짜 sync·mv(2026-11-03 ⑤ 리뷰): `sync -- <경로>` 의 $2 로 파일(.partial)·디렉터리를 가른다.
_FAIL = "#!/bin/sh\necho \"$0: I/O error\" >&2\nexit 1\n"
_SYNC_FILE_FAILS = "#!/bin/sh\ncase \"$2\" in *.partial) echo 'sync: EIO' >&2; exit 1 ;; esac\nexit 0\n"
_SYNC_DIR_FAILS = "#!/bin/sh\ncase \"$2\" in *.partial) exit 0 ;; esac\necho 'sync: EIO' >&2\nexit 1\n"


@pytest.mark.parametrize("shims, needle, new_dump", [
    ({"mv": _FAIL}, "rename 실패", False),
    ({"sync": _SYNC_FILE_FAILS}, "덤프 fsync 실패", False),
    ({"sync": _SYNC_DIR_FAILS}, "디렉터리 fsync 실패", True),
])
def test_fsync_or_rename_failure_fails_the_job_and_keeps_every_old_dump(tmp_path, shims, needle, new_dump):
    old = [f"dms-20260101T0{h}1700Z.dump" for h in range(3)]

    def setup(d):
        _sentinel(d)
        for name in old:
            (d / name).write_text("PGDMP old\n")

    r, d = _run_dump(tmp_path, setup, keep="1", shims=shims)
    assert r.returncode == 1 and needle in r.stderr, (r.stdout, r.stderr)
    assert "완료" not in r.stdout and "밖 삭제" not in r.stdout             # 보존 정리를 하지 않았다
    dumps = _dumps(d)
    assert dumps[:3] == old and len(dumps) == (4 if new_dump else 3)        # 옛 덤프 그대로(디렉터리 fsync 실패면 새 덤프는 남는다)
    assert not [p for p in d.iterdir() if p.name.endswith(".partial")]


def test_only_stale_partials_are_cleaned(tmp_path):
    def setup(d):
        _sentinel(d)
        stale = d / ".dms-20260101T001700Z.dump.partial"
        fresh = d / ".dms-20260101T011700Z.dump.partial"
        stale.write_text("x")
        fresh.write_text("x")
        os.utime(stale, (1, 1))                                         # 3시간보다 오래된 조각만 지운다(동시에 도는 손 실행의 조각은 둔다)

    r, d = _run_dump(tmp_path, setup)
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert not (d / ".dms-20260101T001700Z.dump.partial").exists() and (d / ".dms-20260101T011700Z.dump.partial").exists()


# --- 경로 프로브 DaemonSet(S10) -----------------------------------------------------------------------------------------------

@needs_kubectl
@pytest.mark.parametrize("site", SITES)
def test_probe_daemonset_reads_the_five_pv_paths_only(sites, site):
    ds = doc(sites[site]["layers"]["db-probe"], "DaemonSet", "dms-pg-path-probe")
    paths = re.findall(r"(?m)^      - hostPath:\n          path: (\S+)\n          type: Directory\n        name: pv0\d$", ds)
    assert paths == [f"{SHARED[site]}/dms/pgdata/{pv}" for pv in PVS]
    assert len(re.findall(r"hostPath:", ds)) == 5
    mounts = re.findall(r"(?m)^        - mountPath: /pv/(\S+)\n          name: pv0\d\n          readOnly: true$", ds)
    assert mounts == PVS
    assert re.search(r"(?m)^      - operator: Exists$", ds)                 # 모든 노드(control-plane 포함)
    assert re.search(r"(?m)^        runAsGroup: 26\n        runAsNonRoot: true\n        runAsUser: 26$", ds)
    assert re.search(r"(?m)^        image: (\S+)$", ds).group(1) == sites[site]["env"]["PG_IMAGE_REF"]
    assert "automountServiceAccountToken: false" in ds


def _run_probe(tmp_path, setup):
    pv = tmp_path / "pv"
    pv.mkdir()
    for name in PVS:
        (pv / name).mkdir(mode=0o700)
    setup(pv)
    script = _script(DB / "probe" / "daemonset.yaml", "- name: probe").replace("/pv/", f"{pv}/")
    script = script.replace("exec sleep 2147483647", "exit 0")
    env = {"PATH": "/usr/bin:/bin", "NODE_NAME": "n1", "DMS_PROBE_RETRY_SECONDS": "0"}
    r = subprocess.run(["sh", "-c", script], env=env, capture_output=True, text=True, timeout=60)
    return r


def _load_probe():
    spec = importlib.util.spec_from_file_location("pg_path_probe", DEPLOY / "pg-path-probe.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


PROBE = _load_probe()


def test_probe_script_reports_shape_sentinel_and_pg_version(tmp_path):
    def setup(pv):
        for name in PVS:
            (pv / name / ".dms-pv-id").write_text(name + "\n")
        (pv / PVS[0] / "pgdata").mkdir()
        (pv / PVS[0] / "pgdata" / "PG_VERSION").write_text("17\n")
        (pv / PVS[1] / "pgdata").mkdir()                                  # PG_VERSION 없는 pgdata(깨진 흔적)
        (pv / PVS[2] / ".dms-pv-id").write_text("dms-pgdata-99\n")

    r = _run_probe(tmp_path, setup)
    assert r.returncode == 0, r.stderr
    parsed = PROBE.parse_logs(r.stdout)
    assert parsed["done"] and parsed["node"] == "n1"
    uid, gid = os.getuid(), os.getgid()
    assert parsed["pv"][PVS[0]] == {"shape": f"{uid}:{gid}:700", "id": PVS[0], "pgdata": "17"}
    assert parsed["pv"][PVS[1]]["pgdata"] == "<no-PG_VERSION>"
    assert parsed["pv"][PVS[2]]["id"] == "dms-pgdata-99"
    assert parsed["pv"][PVS[4]]["pgdata"] == "none"


def test_probe_script_never_writes(tmp_path):
    code = "\n".join(ln for ln in _script(DB / "probe" / "daemonset.yaml", "- name: probe").splitlines()
                     if not ln.lstrip().startswith("#"))
    bare = re.sub(r'"(?:[^"\\]|\\.)*"', '""', code)
    assert re.findall(r"(?<![0-9&])>+(?![&/])", bare) == []             # 2>/dev/null 만
    for verb in ("touch", "mkdir", "cp ", "mv ", "tee", "rm ", "chmod", "chown", "ln "):
        assert verb not in code, verb


# --- deploy/pg-path-probe.py 판정 ---------------------------------------------------------------------------------------------

def _node_result(pgdata=None, shape="26:26:700", ids=None):
    pgdata = pgdata or {}
    return {"done": True, "node": "x", "pv": {pv: {"shape": shape, "id": (ids or {}).get(pv, pv), "pgdata": pgdata.get(pv, "none")}
                                              for pv in PVS}}


NODES = ["cp1", "w1", "w2"]
AVAILABLE = {pv: {"phase": "Available", "claim": None} for pv in PVS}


def _bound(n=3):
    out = dict(AVAILABLE)
    for i in range(n):
        out[PVS[i]] = {"phase": "Bound", "claim": f"dms/dms-pg-{i + 1}"}
    return out


def test_precheck_passes_a_clean_pool():
    f = PROBE.judge("precheck", NODES, {n: _node_result() for n in NODES}, AVAILABLE, {})
    assert PROBE.verdict(f) == "PASS", f


@pytest.mark.parametrize("results, needle", [
    ({n: _node_result(shape="0:0:700") for n in NODES}, "모양 0:0:700"),
    ({**{n: _node_result() for n in NODES}, "w2": _node_result(ids={PVS[0]: "<missing>"})}, "센티널"),
    ({n: _node_result(pgdata={PVS[3]: "17"}) for n in NODES}, "옛 PGDATA"),
])
def test_precheck_fails_on_shape_sentinel_or_old_pgdata(results, needle):
    f = PROBE.judge("precheck", NODES, results, AVAILABLE, {})
    assert PROBE.verdict(f) == "FAIL" and any(needle in m for lvl, m in f if lvl == "FAIL"), f


def test_missing_pv_and_mount_failure_are_failures_pull_is_unknown():
    results = {n: _node_result() for n in ("cp1", "w1")}
    pvs = {k: v for k, v in AVAILABLE.items() if k != PVS[4]}
    f = PROBE.judge("precheck", NODES, results, pvs, {"w2": ("mount", "hostPath type check failed")})
    assert PROBE.verdict(f) == "FAIL"
    assert any("w2" in m and "마운트" in m for _, m in f) and any(PVS[4] in m and "PV 가 없다" in m for _, m in f)
    f = PROBE.judge("precheck", NODES, results, AVAILABLE, {"w2": ("pull", "ImagePullBackOff")})
    assert PROBE.verdict(f) == "UNKNOWN"


def test_contrast_passes_when_every_node_sees_every_bound_pgdata():
    seen = {pv: "17" for pv in PVS[:3]}
    f = PROBE.judge("contrast", NODES, {n: _node_result(seen) for n in NODES}, _bound(), {})
    assert PROBE.verdict(f) == "PASS", f
    assert sum(1 for lvl, m in f if lvl == "PASS" and "모든 노드" in m) == 3


def test_contrast_fails_when_a_node_cannot_see_a_bound_pgdata():
    seen = {pv: "17" for pv in PVS[:3]}
    local = dict(seen)
    del local[PVS[1]]                                                   # w1 에서는 dms-pgdata-02 가 비어 보인다 = 그 인스턴스가 로컬에 썼다
    results = {"cp1": _node_result(seen), "w1": _node_result(local), "w2": _node_result(seen)}
    f = PROBE.judge("contrast", NODES, results, _bound(), {})
    assert PROBE.verdict(f) == "FAIL"
    assert any("dms-pgdata-02" in m and "w1" in m and "공용 경로가 아닌 곳" in m for lvl, m in f if lvl == "FAIL"), f


def test_contrast_without_bound_pvs_is_unknown_and_no_spare_is_a_warning():
    f = PROBE.judge("contrast", NODES, {n: _node_result() for n in NODES}, AVAILABLE, {})
    assert PROBE.verdict(f) == "UNKNOWN"
    full = {pv: {"phase": "Bound", "claim": f"dms/dms-pg-{i}"} for i, pv in enumerate(PVS)}
    f = PROBE.judge("contrast", NODES, {n: _node_result({pv: "17" for pv in PVS}) for n in NODES}, full, {})
    assert PROBE.verdict(f) == "WARN" and any("여분" in m for _, m in f)


def test_released_pv_is_a_warning_not_touched():
    pvs = _bound()
    pvs[PVS[3]] = {"phase": "Released", "claim": "dms/dms-pg-4"}
    seen = {pv: "17" for pv in PVS[:4]}
    f = PROBE.judge("contrast", NODES, {n: _node_result(seen) for n in NODES}, pvs, {})
    assert PROBE.verdict(f) == "WARN" and any("Released" in m and "런북" in m for _, m in f)


def test_contrast_defers_an_initializing_instance_instead_of_failing():
    # 2026-11-03 ⑤ 리뷰: readyInstances ≥ 1 직후의 대조 — join(pg_basebackup) 중인 dms-pg-3 의 PV 는 아직 PG_VERSION 이 없다. FAIL 이 아니라 모름.
    seen = {pv: "17" for pv in PVS[:2]}
    results = {n: _node_result(seen) for n in NODES}
    f = PROBE.judge("contrast", NODES, results, _bound(), {}, initializing={"dms-pg-3"})
    assert PROBE.verdict(f) == "UNKNOWN", f
    assert any(PVS[2] in m and "초기화 중" in m for lvl, m in f if lvl == "UNKNOWN")
    assert not any(lvl == "FAIL" for lvl, _ in f)
    f = PROBE.judge("contrast", NODES, results, _bound(), {}, initializing=set())   # 초기화 끝 = 대조(그래도 안 보이면 FAIL)
    assert PROBE.verdict(f) == "FAIL" and any(PVS[2] in m and "공용 경로가 아닌 곳" in m for lvl, m in f if lvl == "FAIL")


# --- 이미 있는 SC·PV 의 값 대조(S10 — 2026-11-03 ⑤ 리뷰: 용량·SC 는 가변이라 SSA 가 조용히 바꾼다) --------------------------------

EXPECTED = {"shared_dir": "/srv/dms_shared", "size": "20Gi"}
GOOD_SC = {"provisioner": "kubernetes.io/no-provisioner", "reclaimPolicy": "Retain", "volumeBindingMode": "Immediate"}


def _live_pvs(**changes):
    out = {}
    for pv in PVS:
        st = {"phase": "Available", "claim": None, "path": f"/srv/dms_shared/dms/pgdata/{pv}", "type": "Directory",
              "capacity": "20Gi", "sc": "dms-pg-shared"}
        st.update(changes.get(pv, {}))
        out[pv] = st
    return out


def test_pv_check_passes_absent_or_identical_and_normalises_quantities():
    f = PROBE.check_pvs({}, None, EXPECTED, verbose=True)
    assert PROBE.verdict(f) == "PASS" and sum(1 for _, m in f if "없음" in m) == 6, f
    live = _live_pvs(**{PVS[0]: {"capacity": "21474836480", "phase": "Bound", "claim": "dms/dms-pg-1"}})   # apiserver 정규화 모양
    f = PROBE.check_pvs(live, GOOD_SC, EXPECTED, verbose=True)
    assert PROBE.verdict(f) == "PASS", f
    assert PROBE.check_pvs(live, GOOD_SC, EXPECTED) == []                # precheck·contrast 에서는 어긋남만 보인다


@pytest.mark.parametrize("change, needle", [
    ({"capacity": "10Gi", "phase": "Bound", "claim": "dms/dms-pg-1"}, "capacity '10Gi'"),
    ({"sc": "local-path"}, "storageClassName 'local-path'"),
    ({"path": "/srv/old_shared/dms/pgdata/dms-pgdata-01"}, "<SHARED_DIR>/dms/pgdata/ 밖"),
    ({"path": "/srv/dms_shared/dms/pgdata/other"}, "hostPath '/srv/dms_shared/dms/pgdata/other'"),
    ({"type": "DirectoryOrCreate"}, "hostPath type 'DirectoryOrCreate'"),
])
def test_pv_check_fails_when_an_existing_pv_differs_from_the_render(change, needle):
    for verbose in (True, False):
        f = PROBE.check_pvs(_live_pvs(**{PVS[0]: change}), GOOD_SC, EXPECTED, verbose=verbose)
        assert PROBE.verdict(f) == "FAIL", f
        assert any(PVS[0] in m and needle in m for lvl, m in f if lvl == "FAIL"), f


def test_pv_check_fails_on_an_immutable_storage_class_difference():
    f = PROBE.check_pvs({}, {**GOOD_SC, "volumeBindingMode": "WaitForFirstConsumer"}, EXPECTED, verbose=True)
    assert PROBE.verdict(f) == "FAIL" and any("volumeBindingMode" in m for lvl, m in f if lvl == "FAIL"), f


class _FakeKube:
    """apply·get·logs·delete 를 흉내 — 노드 셋, 각 노드의 프로브 로그는 정해진 값. PV 는 이름으로 묻는다(--ignore-not-found: 없으면 빈 출력)."""

    def __init__(self, context, namespace, logs=None, pvs=None, sc=None, initializing=None, foreign=()):
        self.context, self.namespace = context, namespace
        self.foreign = tuple(foreign)                                    # NoSchedule taint 노드(DMS 노드 아님) — 공용 스토리지 없음
        self.calls = []
        self.applied = False
        self.logs = logs
        self.pvs = pvs or {}
        self.sc = sc
        self.initializing = initializing

    def context_exists(self):
        return self.context == "dmsk8s"

    def run(self, *args, timeout=60, check=True, namespaced=True):
        import json
        self.calls.append(args)
        if args[:2] == ("get", "nodes"):
            items = [{"metadata": {"name": n}} for n in NODES]
            items += [{"metadata": {"name": n}, "spec": {"taints": [{"key": "team.example/gpu", "effect": "NoSchedule"}]}}
                      for n in self.foreign]
            return json.dumps({"items": items})
        if args[:2] == ("get", "pv"):
            assert "--ignore-not-found" in args and all(pv in args for pv in PVS)
            items = []
            for k, v in self.pvs.items():
                spec = {"capacity": {"storage": v.get("capacity", "20Gi")}, "storageClassName": v.get("sc", "dms-pg-shared"),
                        "hostPath": {"path": v.get("path", f"/srv/dms_shared/dms/pgdata/{k}"), "type": v.get("type", "Directory")}}
                if v.get("claim"):
                    spec["claimRef"] = {"namespace": v["claim"].split("/")[0], "name": v["claim"].split("/")[1]}
                items.append({"metadata": {"name": k}, "status": {"phase": v["phase"]}, "spec": spec})
            return json.dumps({"kind": "List", "items": items}) if items else ""
        if args[:2] == ("get", "sc"):
            return json.dumps({"metadata": {"name": "dms-pg-shared"}, **self.sc}) if self.sc else ""
        if args[:2] == ("get", "clusters.postgresql.cnpg.io"):
            if self.initializing is None:
                return ""
            return json.dumps({"metadata": {"name": "dms-pg"}, "status": {"initializingPVC": sorted(self.initializing)}})
        if args[:2] == ("get", "pods"):
            if not self.applied:
                return json.dumps({"items": []})
            return json.dumps({"items": [{"metadata": {"name": f"probe-{n}"}, "spec": {"nodeName": n},
                                          "status": {"phase": "Running"}} for n in NODES]
                              + [{"metadata": {"name": f"probe-{n}"}, "spec": {"nodeName": n},
                                  "status": {"phase": "Pending"}} for n in self.foreign]})
        if args[:2] == ("get", "events"):
            return json.dumps({"items": [{"reason": "FailedMount", "message": "hostPath type check failed: /srv/dms_shared/dms/pgdata/dms-pgdata-01 is not a directory"}]})
        if args[0] == "logs":
            return self.logs[args[1][len("probe-"):]]
        if args[0] == "apply":
            self.applied = True
            return ""
        return ""


def _log(node, pgdata=None):
    lines = [f"PGPROBE node={node} pv={pv} shape=26:26:700 id={pv}" for pv in PVS]
    lines += [f"PGPROBE node={node} pv={pv} pgdata={(pgdata or {}).get(pv, 'none')} waited=0" for pv in PVS]
    return "\n".join(lines + [f"PGPROBE node={node} done"]) + "\n"


def _rendered_layer(tmp_path, render_env="SHARED_DIR=/srv/dms_shared\nPG_STORAGE_SIZE=20Gi\nDB_LAYERS=\"db-volumes db-cluster db-backup\"\n"):
    # render.sh 의 모양: <렌더>/render.env + <렌더>/platform/db-probe/{kustomization.yaml,kube-context}
    out = tmp_path / "rendered"
    layer = out / "platform" / "db-probe"
    layer.mkdir(parents=True)
    (layer / "kustomization.yaml").write_text("resources: []\n")
    (layer / "kube-context").write_text("dmsk8s\n")
    if render_env is not None:
        (out / "render.env").write_text(render_env)
    return layer


def test_main_deletes_old_probe_applies_judges_and_cleans_up(tmp_path, capsys):
    layer = _rendered_layer(tmp_path)
    made = []

    def factory(ctx, ns):
        k = _FakeKube(ctx, ns, logs={n: _log(n) for n in NODES}, pvs=AVAILABLE, sc=GOOD_SC)
        made.append(k)
        return k

    rc = PROBE.main(["--context", "dmsk8s", "--rendered", str(layer), "--mode", "precheck"], kube_factory=factory)
    out = capsys.readouterr().out
    assert rc == 0 and out.rstrip().endswith("RESULT: PASS"), out
    verbs = [c[0] for c in made[0].calls]
    assert verbs.index("delete") < verbs.index("apply")                 # 옛 프로브를 먼저 지운다(옛 로그를 읽지 않게)
    assert made[0].calls[-1][:2] == ("delete", "-k")                    # 끝나면 지운다
    assert ("apply", "--server-side", "--field-manager=dms-install", "-k", str(layer)) in made[0].calls


def test_main_pv_check_reads_only_and_fails_on_a_changed_capacity(tmp_path, capsys):
    layer = _rendered_layer(tmp_path)
    made = []

    def factory(ctx, ns):
        k = _FakeKube(ctx, ns, pvs={pv: {"phase": "Bound", "claim": "dms/dms-pg-1", "capacity": "50Gi"} for pv in PVS[:1]}, sc=GOOD_SC)
        made.append(k)
        return k

    rc = PROBE.main(["--context", "dmsk8s", "--rendered", str(layer), "--mode", "pv-check"], kube_factory=factory)
    out = capsys.readouterr().out
    assert rc == 1 and out.rstrip().endswith("RESULT: FAIL") and "capacity '50Gi'" in out, out
    assert {c[0] for c in made[0].calls} == {"get"}                     # 프로브를 띄우지 않는다 — 읽기만
    rc = PROBE.main(["--context", "dmsk8s", "--rendered", str(layer), "--mode", "pv-check"],
                    kube_factory=lambda c, n: _FakeKube(c, n))
    assert rc == 0 and capsys.readouterr().out.rstrip().endswith("RESULT: PASS")   # 첫 설치(아무것도 없음)


def test_main_contrast_reads_initializing_pvcs_from_the_cluster(tmp_path, capsys):
    layer = _rendered_layer(tmp_path)
    logs = {n: _log(n, {PVS[0]: "17", PVS[1]: "17"}) for n in NODES}
    rc = PROBE.main(["--context", "dmsk8s", "--rendered", str(layer), "--mode", "contrast"],
                    kube_factory=lambda c, n: _FakeKube(c, n, logs=logs, pvs=_bound(), sc=GOOD_SC, initializing={"dms-pg-3"}))
    out = capsys.readouterr().out
    assert rc == 4 and "초기화 중" in out and "FAIL" not in out, out


def test_main_needs_the_render_env(tmp_path, capsys):
    calls = []
    for text in (None, "SHARED_DIR=/srv/dms_shared\n", "PG_STORAGE_SIZE=20Gi\n"):
        sub = tmp_path / str(len(calls))
        sub.mkdir()
        layer = _rendered_layer(sub, text)
        calls.append(1)
        rc = PROBE.main(["--context", "dmsk8s", "--rendered", str(layer), "--mode", "pv-check"],
                        kube_factory=lambda c, n: pytest.fail("클러스터를 건드리기 전에 멈춰야 한다"))
        assert rc == 2 and "render.env" in capsys.readouterr().err


def test_main_refuses_a_context_other_than_the_rendered_pin(tmp_path, capsys):
    layer = _rendered_layer(tmp_path)
    calls = []
    rc = PROBE.main(["--context", "dms", "--rendered", str(layer), "--mode", "precheck"],
                    kube_factory=lambda c, n: calls.append(1) or _FakeKube(c, n))
    assert rc == 2 and calls == [] and "dmsk8s" in capsys.readouterr().err


def test_dms_nodes_are_masters_and_workers_without_a_role_noschedule_taint():
    # 설계 2-3(S2b 와 같은 정의): 마스터 + NoSchedule taint 없는 워커. cordon 같은 node.kubernetes.io/* 상태 taint 는 세지 않는다.
    def node(name, cp=False, taints=()):
        meta = {"name": name, "labels": {"node-role.kubernetes.io/control-plane": ""} if cp else {}}
        return {"metadata": meta, "spec": {"taints": [{"key": k, "effect": e} for k, e in taints]}}
    items = [node("cp1", cp=True, taints=[("node-role.kubernetes.io/control-plane", "NoSchedule")]), node("w1"),
             node("w2", taints=[("node.kubernetes.io/unschedulable", "NoSchedule")]), node("w3", taints=[("dms.io/drain", "NoExecute")]),
             node("gpu1", taints=[("nvidia.com/gpu", "NoSchedule")]), node("w4", taints=[("team.example/x", "PreferNoSchedule")])]
    assert PROBE.dms_nodes(items) == (["cp1", "w1", "w2", "w3", "w4"], ["gpu1"])


def test_main_judges_only_dms_nodes_on_a_shared_cluster(tmp_path, capsys):
    # 2026-11-03 ⑤ 리뷰 2: 다른 팀의 taint 노드(공용 스토리지 없음 — 프로브 파드 FailedMount)가 섞인 클러스터에서 S10 이 늘 FAIL 했다.
    layer = _rendered_layer(tmp_path)
    logs = {n: _log(n) for n in NODES}
    rc = PROBE.main(["--context", "dmsk8s", "--rendered", str(layer), "--mode", "precheck"],
                    kube_factory=lambda c, n: _FakeKube(c, n, logs=logs, pvs=AVAILABLE, sc=GOOD_SC, foreign=["gpu1"]))
    out = capsys.readouterr().out
    assert rc == 0 and out.rstrip().endswith("RESULT: PASS"), out
    assert "판정 대상 노드 3(DMS 노드" in out and "cp1, w1, w2" in out
    assert "판정 밖 노드 1" in out and "gpu1" in out and "FailedMount" not in out
    # --nodes 로 넣으면 판정한다 — 그 노드의 FailedMount 는 FAIL(「마운트 없음」).
    rc = PROBE.main(["--context", "dmsk8s", "--rendered", str(layer), "--mode", "precheck", "--timeout", "1", "--nodes", "cp1,w1,w2,gpu1"],
                    kube_factory=lambda c, n: _FakeKube(c, n, logs=logs, pvs=AVAILABLE, sc=GOOD_SC, foreign=["gpu1"]))
    out = capsys.readouterr().out
    assert rc == 1 and "gpu1" in out and "마운트" in out and "판정 대상 노드 4(--nodes)" in out, out


def test_main_nodes_option_rejects_an_unknown_node_before_touching_the_cluster(tmp_path, capsys):
    layer = _rendered_layer(tmp_path)
    made = []

    def factory(c, n):
        k = _FakeKube(c, n, logs={x: _log(x) for x in NODES}, pvs=AVAILABLE, sc=GOOD_SC)
        made.append(k)
        return k

    rc = PROBE.main(["--context", "dmsk8s", "--rendered", str(layer), "--mode", "contrast", "--nodes", "cp1,nope"], kube_factory=factory)
    cap = capsys.readouterr()
    assert rc == 2 and "nope" in cap.err, (cap.out, cap.err)
    assert not any(c[0] in ("apply", "delete") for c in made[0].calls)       # 프로브를 띄우지도 지우지도 않았다


def test_probe_script_is_python39_syntax():
    import ast
    ast.parse((DEPLOY / "pg-path-probe.py").read_text(encoding="utf-8"), feature_version=(3, 9))


# --- 렌더러의 DB 값 검사 -------------------------------------------------------------------------------------------------------

def test_prod_defaults_are_the_design_values(tmp_path):
    r, out = _render(tmp_path, "prod", prod_values())
    assert r.returncode == 0, (r.stdout, r.stderr)
    env = _render_env(out)
    assert (env["PG_STORAGE_SIZE"], env["PG_PLACEMENT"], env["PG_BACKUP_KEEP"]) == ("20Gi", "control-plane", "336")
    assert env["DB_LAYERS"] == "db-volumes db-cluster db-backup" and env["ADDON_LAYERS"] == "cnpg"
    order = [ln.split(": ", 1)[1].rsplit("/", 1)[1] for ln in r.stdout.splitlines() if ln.startswith(("PLATFORM: ", "ADDON: ", "PROBE: "))]
    assert order == ["priorityclass", "registry", "cnpg", "db-volumes", "db-cluster", "db-backup", "db-probe"]
    assert r.stdout.strip().splitlines()[-1] == f"RENDERED: {out}/app"


def test_testbed_values_are_the_design_10_shrink():
    values = _values_env(OVERLAYS / "testbed-k8s" / "values.env")
    assert (values["PG_CPU_REQUEST"], values["PG_MEMORY"], values["PG_SHARED_BUFFERS"]) == ("100m", "256Mi", "64MB")
    assert values["PG_PLACEMENT"] == "all-nodes"
    assert "PG_PLACEMENT=all-nodes" not in (OVERLAYS / "prod" / "values.env.example").read_text().replace("# PG_PLACEMENT", "")


@pytest.mark.parametrize("key, value", [
    ("PG_STORAGE_SIZE", "20G"), ("PG_STORAGE_SIZE", "020Gi"), ("PG_STORAGE_SIZE", "Gi"),
    ("PG_CPU_REQUEST", "0"), ("PG_CPU_REQUEST", "1.5"), ("PG_MEMORY", "4G"), ("PG_SHARED_BUFFERS", "1Gi"),
    ("PG_PLACEMENT", "workers"), ("PG_BACKUP_KEEP", "0"), ("PG_BACKUP_KEEP", "999999"), ("PG_BACKUP_KEEP", "x"),
])
def test_render_rejects_bad_db_values(tmp_path, key, value):
    r, out = _render(tmp_path, "prod", prod_values(**{key: f"'{value}'"}))
    assert r.returncode == 2 and key in r.stdout, (r.stdout, r.stderr)
    assert not out.exists()


@needs_kubectl
@pytest.mark.parametrize("memory, buffers", [("1Gi", "256MB"), ("4Gi", "1GB"), ("256Mi", "64MB"), ("6Mi", "1536kB"), ("8Gi", "2GB")])
def test_shared_buffers_follow_pg_memory_when_unset(tmp_path, memory, buffers):
    # 2026-11-03 ⑤ 리뷰 2: PG_MEMORY 만 줄이면 base 의 shared_buffers 1GB 가 그대로 남아 메모리 limit 과 같거나 컸다(cgroup OOM 재기동).
    r, out = _render(tmp_path, "prod", prod_values(PG_MEMORY=memory))
    assert r.returncode == 0, (r.stdout, r.stderr)
    cl = _kustomize(out / "platform" / "db-cluster")
    assert re.search(rf"(?m)^      shared_buffers: {buffers}$", cl), _block(cl, "parameters")
    assert re.search(rf"(?m)^      memory: {memory}$", cl)


@pytest.mark.parametrize("overrides", [
    {"PG_MEMORY": "1Gi", "PG_SHARED_BUFFERS": "1GB"}, {"PG_MEMORY": "1Gi", "PG_SHARED_BUFFERS": "512MB"},
    {"PG_SHARED_BUFFERS": "2GB"},                                        # PG_MEMORY 없음 = base 4Gi 의 절반
])
def test_render_rejects_shared_buffers_of_half_the_memory_or_more(tmp_path, overrides):
    r, out = _render(tmp_path, "prod", prod_values(**overrides))
    assert r.returncode == 2 and "PG_SHARED_BUFFERS" in r.stdout and "절반" in r.stdout, (r.stdout, r.stderr)
    assert not out.exists()


def test_db_values_in_the_callers_environment_are_ignored(tmp_path):
    r, out = _render(tmp_path, "prod", prod_values(), PG_STORAGE_SIZE="99Gi", PG_PLACEMENT="all-nodes")
    assert r.returncode == 0, (r.stdout, r.stderr)
    env = _render_env(out)
    assert (env["PG_STORAGE_SIZE"], env["PG_PLACEMENT"]) == ("20Gi", "control-plane")


@pytest.mark.parametrize("mutate, needle", [
    (lambda root: (root / "versions.env").write_text((root / "versions.env").read_text().replace(
        "PG_IMAGE=postgresql:", "PG_IMAGE=postgresql-x:")), "postgresql-x"),
    (lambda root: (root / "versions.env").write_text((root / "versions.env").read_text() + "CNPG_VERSION=1.30.0\n"),
     "CNPG_VERSION= 줄이 정확히 하나"),
    (lambda root: (root / "versions.env").write_text((root / "versions.env").read_text().replace(
        "CNPG_OPERATOR_IMAGE=cloudnative-pg:", "CNPG_OPERATOR_IMAGE=cloudnative-pg:9.")), "CNPG_VERSION"),
    (lambda root: shutil.rmtree(root / "addons"), "애드온 base 없음"),
    (lambda root: (root / "images.lock").write_text("\n".join(
        ln for ln in (root / "images.lock").read_text().splitlines() if not ln.startswith("cloudnative-pg:")) + "\n"),
     "cloudnative-pg"),
    (lambda root: os.remove(root / "versions.env"), "versions.env"),
])
def test_render_fails_closed_when_versions_lock_and_addons_disagree(tmp_path, mutate, needle):
    mutate(_tree(tmp_path))
    r, out = _render(tmp_path, "prod", prod_values())
    assert r.returncode == 2 and needle in r.stdout, (r.stdout, r.stderr)
    assert not out.exists()


# --- deploy/pg-secrets.py — dms-pg-app·DMS_DATABASE_URL(설계 6-3, 값을 출력·argv 에 싣지 않는다) ---------------------------------------------

def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SECRETS = _load("pg_secrets", DEPLOY / "pg-secrets.py")

# 가짜 kubectl(python): 시크릿을 JSON 파일에 두고, 모든 호출의 argv 를 기록한다 — 값이 argv 에 실리면 테스트가 잡는다. stdin 은
# create(--from-env-file=/dev/stdin)·patch(--patch-file=/dev/stdin)에서만 읽는다.
_FAKE_KUBECTL_PY = r'''#!/usr/bin/env python3
import base64, json, os, sys
argv = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as fh:
    fh.write(json.dumps(argv) + "\n")
if argv[:2] == ["config", "get-contexts"]:
    sys.exit(0 if argv[2] == "dmsk8s" else 1)
store_path = os.environ["FAKE_STORE"]
store = json.load(open(store_path)) if os.path.exists(store_path) else {}
args = argv[4:]                       # --context X -n NS
if args[:2] == ["get", "secret"]:
    name = args[2]
    if name not in store:
        sys.stderr.write('Error from server (NotFound): secrets "%s" not found\n' % name); sys.exit(1)
    print(json.dumps({"data": {k: base64.b64encode(v.encode()).decode() for k, v in store[name].items()}})); sys.exit(0)
if args[:3] == ["create", "secret", "generic"]:
    name = args[3]
    assert "--from-env-file=/dev/stdin" in args
    if name in store:
        sys.stderr.write("AlreadyExists\n"); sys.exit(1)
    store[name] = dict(line.split("=", 1) for line in sys.stdin.read().splitlines() if line)
    json.dump(store, open(store_path, "w")); sys.exit(0)
if args[:2] == ["patch", "secret"]:
    name = args[2]
    assert "--patch-file=/dev/stdin" in args and "--type=merge" in args
    patch = json.loads(sys.stdin.read())
    store[name].update({k: base64.b64decode(v).decode() for k, v in patch["data"].items()})
    json.dump(store, open(store_path, "w")); sys.exit(0)
sys.stderr.write("unexpected %r\n" % argv); sys.exit(97)
'''


def _secrets_run(tmp_path, *cmd, store=None):
    import json
    import sys
    bindir = tmp_path / "bin"
    if not bindir.exists():
        bindir.mkdir()
        (bindir / "kubectl").write_text(_FAKE_KUBECTL_PY.replace("#!/usr/bin/env python3", f"#!{sys.executable}"))
        (bindir / "kubectl").chmod(0o755)
    st = tmp_path / "store.json"
    if store is not None:
        st.write_text(json.dumps(store))
    log = tmp_path / "log"
    env = {"PATH": f"{bindir}:/usr/bin:/bin", "FAKE_STORE": str(st), "FAKE_LOG": str(log)}
    r = subprocess.run([sys.executable, str(DEPLOY / "pg-secrets.py"), "--context", "dmsk8s", *cmd], env=env,
                       capture_output=True, text=True, timeout=60)
    data = json.loads(st.read_text()) if st.exists() else {}
    argvs = log.read_text() if log.exists() else ""
    return r, data, argvs


def test_url_is_the_design_6_3_shape():
    url = SECRETS.build_url("ab12", "dms-pg", "dms")
    assert url == ("postgresql://dms:ab12@dms-pg-rw.dms.svc:5432/dms?sslmode=require&target_session_attrs=read-write"
                   "&keepalives=1&keepalives_idle=10&keepalives_interval=5&keepalives_count=3&tcp_user_timeout=15000")
    assert "connect_timeout" not in url                                   # 코드 기본 5초 유지(6-3 — 코드 변경 없음)
    assert SECRETS.url_problems(url, "ab12", "dms-pg", "dms") == []
    assert SECRETS.url_problems(SECRETS.build_url("p/w@:x", "dms-pg", "dms"), "p/w@:x", "dms-pg", "dms") == []   # 인코딩 왕복


def test_ensure_app_then_init_secrets_never_print_or_pass_values_in_argv(tmp_path):
    r, store, argvs = _secrets_run(tmp_path, "ensure-app", store={})
    assert r.returncode == 0, (r.stdout, r.stderr)
    pw = store["dms-pg-app"]["password"]
    assert re.fullmatch(r"[0-9a-f]{48}", pw) and store["dms-pg-app"]["username"] == "dms"
    r2, store, argvs = _secrets_run(tmp_path, "init-secrets")
    assert r2.returncode == 0, (r2.stdout, r2.stderr)
    s = store["dms-secrets"]
    assert sorted(s) == ["DMS_ADMIN_TOKEN", "DMS_DATABASE_URL", "DMS_SESSION_SECRET", "DMS_SHARED_TOKEN"]
    assert SECRETS.url_problems(s["DMS_DATABASE_URL"], pw, "dms-pg", "dms") == []
    for value in [pw] + list(s.values()):
        assert value not in r.stdout + r.stderr + r2.stdout + r2.stderr + argvs
    assert '"--type=kubernetes.io/basic-auth"' in argvs
    r3, store2, _ = _secrets_run(tmp_path, "ensure-app")
    assert r3.returncode == 0 and "이미 있다" in r3.stdout and store2["dms-pg-app"]["password"] == pw   # 덮어쓰지 않는다


def test_set_url_keeps_the_old_url_for_rollback_and_check_gates(tmp_path):
    old = "postgresql://dmsapp_k8s:oldpw@pkg-01:5432/dmsdb_k8s"
    store = {"dms-pg-app": {"username": "dms", "password": "f" * 48},
             "dms-secrets": {"DMS_DATABASE_URL": old, "DMS_SHARED_TOKEN": "t1", "DMS_ADMIN_TOKEN": "t2", "DMS_SESSION_SECRET": "t3"}}
    r, _, _ = _secrets_run(tmp_path, "check", store=store)
    assert r.returncode == 1 and "host" in r.stdout and "oldpw" not in r.stdout + r.stderr
    r, data, argvs = _secrets_run(tmp_path, "set-url")
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert data["dms-secrets-prev"]["DMS_DATABASE_URL"] == old
    assert data["dms-secrets"]["DMS_SHARED_TOKEN"] == "t1"                # URL 만 바뀐다
    assert "f" * 48 not in argvs + r.stdout and "oldpw" not in argvs + r.stdout
    r, _, _ = _secrets_run(tmp_path, "check")
    assert r.returncode == 0 and r.stdout.startswith("PASS"), r.stdout
    r, data, _ = _secrets_run(tmp_path, "restore-url")
    assert r.returncode == 0 and data["dms-secrets"]["DMS_DATABASE_URL"] == old


def test_check_names_the_mismatched_parts_only(tmp_path):
    bad = "postgresql://dms:wrong@dms-pg-rw.dms.svc:5432/dms?sslmode=require"
    store = {"dms-pg-app": {"username": "dms", "password": "right"}, "dms-secrets": {"DMS_DATABASE_URL": bad}}
    r, _, _ = _secrets_run(tmp_path, "check", store=store)
    assert r.returncode == 1 and "비밀번호" in r.stdout and "tcp_user_timeout=15000" in r.stdout
    assert "wrong" not in r.stdout + r.stderr and "right" not in r.stdout + r.stderr


def test_secrets_helper_is_python39_syntax_and_needs_a_context():
    import ast
    import sys
    ast.parse((DEPLOY / "pg-secrets.py").read_text(encoding="utf-8"), feature_version=(3, 9))
    r = subprocess.run([sys.executable, str(DEPLOY / "pg-secrets.py"), "check"], capture_output=True, text=True, timeout=30)
    assert r.returncode == 2                                              # --context 필수



@needs_kubectl
@pytest.mark.parametrize("site", SITES)
def test_pg_storage_class_pvcs_are_limited_to_namespace_dms(sites, site):
    # 6-2 「소유·보안」(2026-11-03 커밋 보안 리뷰): claimRef 없는 여분 PV 에 다른 네임스페이스 PVC 가 붙지 못하게 — VAP + Deny 바인딩이
    # 볼륨 층과 함께 적용된다(따로 미루면 PV 가 먼저 열린다).
    layer = sites[site]["layers"]["db-volumes"]
    vap = doc(layer, "ValidatingAdmissionPolicy", "dms-pg-shared-pvc-namespace")
    assert re.search(r"(?m)^  failurePolicy: Fail$", vap)
    assert "persistentvolumeclaims" in vap and "CREATE" in vap and "UPDATE" in vap
    expr = " ".join(vap.split()).replace("''", "'")      # kustomize 가 작은따옴표 스칼라로 다시 쓴다
    assert "object.spec.storageClassName != 'dms-pg-shared'" in expr and "object.metadata.namespace == 'dms'" in expr
    binding = doc(layer, "ValidatingAdmissionPolicyBinding", "dms-pg-shared-pvc-namespace")
    assert re.search(r"(?m)^  policyName: dms-pg-shared-pvc-namespace$", binding)
    assert re.search(r"(?m)^  - Deny$", binding)
