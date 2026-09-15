"""에이전트 DaemonSet 볼륨 계약(방안 A, 2026-09-16).

스토리지는 등록만으로 모든 노드에서 프로브돼야 한다 -- 매니페스트에 스토리지별
hostPath 를 다시 나열하기 시작하면 새 스토리지가 Missing 으로 돌아간다(프로덕션
2026-09-16). 호스트 루트 바인드는 readOnly 여야 하고(최상위 = 호스트 루트 fs; 전파된 하위
마운트는 호스트 옵션을 유지하며 이는 종전 스토리지별 rw hostPath 와 같은 권한이다 --
에이전트는 root 이고 쓰지 않는다) HostToContainer 여야 뒤에 생긴 마운트가 보인다."""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DS = REPO_ROOT / "deploy" / "k8s" / "50-agent-daemonset.yaml"


def _items(block_key: str) -> list[dict]:
    """`<block_key>:` 아래의 `- name:` 항목들을 {키: 값} 평면 dict 로(중첩 키는 마지막
    세그먼트만 -- hostPath.path 는 path 로). PyYAML 없이 계약을 고정하기 위한 최소 파서."""
    text = DS.read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    start = next(i for i, ln in enumerate(lines) if ln.strip() == f"{block_key}:")
    indent = len(lines[start]) - len(lines[start].lstrip())
    body = []
    for ln in lines[start + 1:]:
        if len(ln) - len(ln.lstrip()) <= indent:
            break
        body.append(ln)
    items, cur = [], None
    for ln in body:
        s = ln.strip()
        if s.startswith("- "):
            cur = {}
            items.append(cur)
            s = s[2:]
        k, _, v = s.partition(":")
        if v.strip():
            cur[k.strip()] = v.strip().strip('"')
    return items


def test_only_host_root_proc_and_sys_hostpaths_no_per_storage_volumes():
    vols = _items("volumes")
    paths = {v["name"]: v["path"] for v in vols if "path" in v}
    assert paths["host-root"] == "/"
    offenders = [f"{n}={p}" for n, p in paths.items()
                 if not (p == "/" or p.startswith("/proc/") or p.startswith("/sys/"))]
    assert offenders == [], (
        f"스토리지별 hostPath 가 다시 생겼다: {offenders} -- 스토리지는 host-root(/) 아래로 "
        f"프로브된다(DMS_AGENT_HOST_ROOT). 매니페스트에 나열하지 마라.")
    host = next(v for v in vols if v["name"] == "host-root")
    assert host["type"] == "Directory"


def test_host_root_mount_is_read_only_and_propagates():
    mounts = {m["name"]: m for m in _items("volumeMounts")}
    host = mounts["host-root"]
    assert host["mountPath"] == "/host/root"
    assert host["readOnly"] == "true", "호스트 루트를 rw 로 붙이지 마라 -- 에이전트는 쓰지 않는다"
    assert host["mountPropagation"] == "HostToContainer"
    # 다른 마운트는 전부 읽기 전용 파일/디렉터리(proc·sys)
    for name, m in mounts.items():
        assert m.get("readOnly") == "true", f"{name} 은 readOnly 여야 한다"


def test_env_points_probes_at_the_host_root_mount():
    text = DS.read_text(encoding="utf-8")
    m = re.search(r'name: DMS_AGENT_HOST_ROOT\s*\n\s*value: "([^"]+)"', text)
    assert m and m.group(1) == "/host/root"
    mp = re.search(r'name: host-root\s*\n\s*mountPath: ([^\s]+)', text)
    assert mp and mp.group(1) == m.group(1), "env 와 mountPath 가 같은 경로여야 한다"
