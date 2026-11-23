"""포탈 이미지 빌드의 프런트 단계 계약(2026-11-23).

d37 포탈 빌드가 빌드 파드(memory limit 1Gi — build_manifests, Node 힙 자동 상한 약 505MB) 안에서 `tsc -b`(테스트 128개 파일
포함, 힙 510~550MB 필요)로 heap out of memory 가 났다. 이미지 빌드는 앱 코드만 타입 검사(`build:image` =
`tsc -p tsconfig.build.json && vite build`)하고, 테스트 타입 검사는 개발 게이트 `npx tsc -b` 가 한다. 누가 Dockerfile 을 다시
`npm run build` 로 되돌리거나 tsconfig.build.json 에서 테스트 제외를 빼면 다음 빌드가 다시 OOM 으로 죽는다 — 그 회귀를 여기서 잡는다.
"""
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FRONTEND = REPO_ROOT / "frontend"


def _jsonc(path: Path) -> dict:
    # tsconfig 는 JSONC 다 — 줄 주석만 쓴다(문자열 안의 // 는 없다).
    text = "\n".join(re.sub(r"^\s*//.*$", "", line) for line in path.read_text().splitlines())
    return json.loads(text)


def test_dockerfile_builds_the_portal_with_build_image():
    dockerfile = (REPO_ROOT / "deploy" / "docker" / "Dockerfile.dms").read_text()
    run_lines = [ln.strip() for ln in dockerfile.splitlines() if ln.strip().startswith("RUN npm run build")]
    assert run_lines == ["RUN npm run build:image"]


def test_build_image_script_typechecks_app_only_then_bundles():
    scripts = json.loads((FRONTEND / "package.json").read_text())["scripts"]
    assert scripts["build:image"] == "tsc -p tsconfig.build.json && vite build"
    # 개발 게이트는 그대로 테스트까지 본다(tsc -b = tsconfig.json include src).
    assert scripts["build"] == "tsc -b && vite build"


def test_build_tsconfig_excludes_tests_and_test_helpers():
    cfg = _jsonc(FRONTEND / "tsconfig.build.json")
    assert cfg["extends"] == "./tsconfig.json"
    assert cfg["include"] == ["src"]
    assert cfg["compilerOptions"]["types"] == []          # vitest 전역 타입을 싣지 않는다
    for pattern in ("src/**/*.test.ts", "src/**/*.test.tsx", "src/test"):
        assert pattern in cfg["exclude"]
    base = _jsonc(FRONTEND / "tsconfig.json")
    assert base["include"] == ["src"]                       # 개발 게이트는 테스트 포함


def test_node_heap_is_pinned_and_fits_the_build_pod_limit():
    # 힙을 Node 의 자동 계산(cgroup 상한의 약 절반 — 판마다 다를 수 있다)에 맡기지 않는다. 빌드 파드 memory limit(build_manifests)
    # 안에 힙 + buildah·RSS 여유가 남아야 한다 — 상한을 내리거나 힙을 올릴 때 둘을 같이 본다(2026-11-23 1Gi → 2Gi, 힙 1024MB).
    dockerfile = (REPO_ROOT / "deploy" / "docker" / "Dockerfile.dms").read_text()
    m = re.search(r"^ENV NODE_OPTIONS=--max-old-space-size=(\d+)$", dockerfile, re.M)
    assert m, "Dockerfile.dms 의 web 단계가 NODE_OPTIONS 힙을 고정해야 한다"
    heap_mib = int(m.group(1))
    assert dockerfile.index("ENV NODE_OPTIONS") < dockerfile.index("RUN npm run build:image")
    src = (REPO_ROOT / "src" / "dms" / "build_manifests.py").read_text()
    lim = re.search(r'"limits": \{"cpu": "1000m", "memory": "(\d+)Gi"', src)
    assert lim, "빌드 컨테이너 memory limit 을 찾지 못했다"
    limit_mib = int(lim.group(1)) * 1024
    assert heap_mib >= 768                      # build:image 는 힙 ~400MB(350 에서 실패) — 넉넉히
    assert limit_mib - heap_mib >= 768          # buildah·Node 비힙 RSS 여유

