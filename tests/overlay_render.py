"""오버레이 렌더 계약 테스트의 공용 헬퍼(2026-10-22 브랜치 분리 — 테스트 모듈이 아니다, 수집되지 않는다).

브랜치마다 싣는 오버레이가 다르다(main·dms-ssc = testbed·prod(+ssc), dms-k8s = testbed-k8s·prod). 헬퍼는 어느 브랜치에서나
같은 파일로 두고(공용 — main 에서 고쳐 cherry-pick), 오버레이별 계약은 그 오버레이를 싣는 브랜치의 테스트가 이걸 import 해 쓴다.
렌더는 `kubectl kustomize` 로컬 렌더뿐이다 — 클러스터·kubeconfig 를 보지 않는다(KUBECONFIG=/dev/null)."""
import functools
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from dms.manifest_tags import _unquote

REPO_ROOT = Path(__file__).resolve().parent.parent
OVERLAYS = REPO_ROOT / "deploy" / "overlays"

KUBECTL = shutil.which("kubectl")
needs_kubectl = pytest.mark.skipif(KUBECTL is None, reason="kubectl 없음 — 오버레이 렌더 계약은 kubectl kustomize 가 필요")


def present_overlays() -> list:
    """이 브랜치가 싣는 오버레이(kustomization.yaml 이 있는 deploy/overlays/* 디렉터리 이름)."""
    return sorted(p.parent.name for p in OVERLAYS.glob("*/kustomization.yaml"))


def expected_context(overlay: Path):
    """guard-images.sh 와 같은 규칙: kube-context 의 첫 비주석·비공백 줄(공백 제거). 파일이 없으면 None."""
    path = overlay / "kube-context"
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        value = re.sub(r"\s", "", line.split("#", 1)[0])
        if value:
            return value
    return ""


@functools.lru_cache(maxsize=None)
def render(overlay: str) -> str:
    # KUBECONFIG 를 없는 경로로 -- kustomize 는 클러스터를 보지 않지만, 혹시라도 실 테스트베드를 건드리지 않게.
    env = {**os.environ, "KUBECONFIG": os.devnull}
    r = subprocess.run([KUBECTL, "kustomize", str(OVERLAYS / overlay)], capture_output=True, text=True,
                       timeout=120, env=env)
    assert r.returncode == 0, f"kubectl kustomize deploy/overlays/{overlay} 실패: {r.stderr}"
    return r.stdout


def docs(text: str) -> list:
    return [d for d in re.split(r"(?m)^---\s*$", text) if d.strip()]


def doc(text: str, kind: str, name: str) -> str:
    for d in docs(text):
        k = re.search(r"(?m)^kind: (\S+)$", d)
        meta = re.search(r"(?m)^metadata:\n((?:  .*(?:\n|$))*)", d)
        if not (k and meta and k.group(1) == kind):
            continue
        n = re.search(r"(?m)^  name: (\S+)$", meta.group(1))
        if n and n.group(1) == name:
            return d
    raise AssertionError(f"렌더 결과에 {kind}/{name} 이 없다")


def seq(doc_text: str, key: str) -> list:
    """스칼라 블록 시퀀스. kustomize 는 `- ` 를 부모 키와 같은 열에 둔다(`  - hosts:` 처럼 키가 시퀀스 항목의 첫
    키이면 그 키의 열 = `- ` 뒤)."""
    lines = doc_text.splitlines()
    for idx, line in enumerate(lines):
        m = re.match(rf"^([ ]*(?:- )?){re.escape(key)}:\s*$", line)
        if not m:
            continue
        col = " " * len(m.group(1))
        items = []
        for nxt in lines[idx + 1:]:
            if not nxt.startswith(col + "- "):
                break
            items.append(_unquote(nxt[len(col) + 2:].strip()))
        assert items, f"`{key}:` 아래 시퀀스 항목이 없다"
        return items
    raise AssertionError(f"`{key}:` 시퀀스를 못 찾았다")


def config_data(text: str) -> dict:
    d = doc(text, "ConfigMap", "dms-config")
    m = re.search(r"(?m)^data:\n((?:  .*(?:\n|$))*)", d)
    assert m, "dms-config 에 data 가 없다"
    return {k: _unquote(v.strip()) for k, v in re.findall(r"(?m)^  ([A-Z0-9_]+):(.*)$", m.group(1))}


def images(text: str) -> list:
    return re.findall(r"(?m)^\s*image: (\S+)\s*$", text)
