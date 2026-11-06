"""deploy/docker/build-and-push.sh — DMS 이미지 3종의 최초(부트스트랩)·비상 빌드(deploy/README §1, 2026-11-06 fail-closed).

- REGISTRY 에는 기본값이 없다(예전 기본 = 테스트베드 레지스트리 → 다른 사이트에서 REGISTRY 를 빠뜨리면 남의 레지스트리로 push 했다).
  없거나 비었으면 어떤 외부 명령(podman·docker)도 부르기 전에 rc≠0 이고 stderr 가 REGISTRY 를 말한다.
- REGISTRY 를 주면 세 이미지를 <REGISTRY>/<이름>:<TAG> 로 빌드·push 하고, agent 는 같은 REGISTRY·TAG 의 dms·dms-mpifileutils
  위에서 빌드한다(--build-arg — install.sh S5 PAUSE 가 「세 태그 같게」를 요구하는 이유).
- 스크립트의 코드 줄에는 테스트베드 레지스트리·주소가 없다(머리 주석도 사이트 중립 — prod 운영자가 그대로 따라 한다).

podman·docker 는 PATH 앞의 심이다(받은 인자를 한 줄씩 기록하고 0 으로 끝난다) — 실제 빌드·push 는 하지 않는다.
"""
import os
import re
import shutil
import subprocess

import pytest

from overlay_render import REPO_ROOT

SCRIPT = REPO_ROOT / "deploy" / "docker" / "build-and-push.sh"
BASH = shutil.which("bash")
REG = "10.96.0.50:5000"          # 중립 예시(in-cluster ClusterIP:포트 모양)

_STUB = "#!/bin/sh\nprintf '%s %s\\n' \"$(basename \"$0\")\" \"$*\" >> \"$STUB_LOG\"\n"

pytestmark = pytest.mark.skipif(BASH is None, reason="bash 없음 — build-and-push.sh 는 bash 스크립트")


def _run(tmp_path, **env_over):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    for name in ("podman", "docker"):
        stub = bindir / name
        stub.write_text(_STUB)
        stub.chmod(0o755)
    log = tmp_path / "calls.log"
    env = {k: v for k, v in os.environ.items() if k not in ("REGISTRY", "TAG", "IMAGES", "PUSH", "NO_CACHE")}
    env.update(PATH=f"{bindir}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}", STUB_LOG=str(log))
    env.update(env_over)
    r = subprocess.run([BASH, str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60, cwd=tmp_path)
    calls = log.read_text().splitlines() if log.exists() else []
    return r, calls


@pytest.mark.parametrize("registry", [None, ""], ids=["unset", "empty"])
def test_missing_registry_fails_before_any_external_command(tmp_path, registry):
    over = {} if registry is None else {"REGISTRY": registry}
    r, calls = _run(tmp_path, TAG="d100", **over)
    assert r.returncode != 0, (r.stdout, r.stderr)
    assert "REGISTRY" in r.stderr and "deploy/README §1" in r.stderr, r.stderr
    assert calls == []                                     # podman·docker 를 한 번도 부르지 않았다
    assert "build:" not in r.stdout and "push" not in r.stdout


def test_registry_given_builds_and_pushes_three_images_under_one_registry_and_tag(tmp_path):
    r, calls = _run(tmp_path, REGISTRY=REG, TAG="d100")
    assert r.returncode == 0, (r.stdout, r.stderr)
    mfu, dms, agent = (f"{REG}/{n}:d100" for n in ("dms-mpifileutils", "dms", "dms-agent"))
    assert [c.split()[:2] for c in calls] == [["podman", "build"], ["podman", "push"]] * 3, calls
    builds = [c for c in calls if c.startswith("podman build")]
    assert "-f deploy/docker/Dockerfile.mpifileutils" in builds[0] and builds[0].endswith(f"-t {mfu} .")
    assert "-f deploy/docker/Dockerfile.dms" in builds[1] and builds[1].endswith(f"-t {dms} .")
    assert "-f deploy/docker/Dockerfile.agent" in builds[2] and builds[2].endswith(f"-t {agent} .")
    assert f"--build-arg DMS_IMAGE={dms} --build-arg MFU_IMAGE={mfu}" in builds[2]     # 같은 REGISTRY·TAG 위에서
    pushes = [c for c in calls if c.startswith("podman push")]
    assert pushes == [f"podman push --tls-verify=false {ref}" for ref in (mfu, dms, agent)]
    assert f"registry={REG} tag=d100" in r.stdout


def test_registry_check_comes_before_any_command_and_no_testbed_default_remains():
    text = SCRIPT.read_text(encoding="utf-8")
    code = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    guard = next(i for i, ln in enumerate(code) if ln.startswith(': "${REGISTRY:?'))
    # 가드 앞의 코드 줄은 셸 옵션뿐 — 외부 명령·podman 이 그 앞에 오면 REGISTRY 없이도 무언가 돈다.
    assert code[:guard] == ["set -euo pipefail"], code[:guard]
    assert "REGISTRY:-" not in text                                          # 기본값 없음
    assert not [ln for ln in code if re.match(r"\s*(export\s+)?REGISTRY=", ln)]  # 재대입 없음
    for token in ("pkg-01", "10.10.10.", "10.112.", "dmsk8s"):
        assert token not in "\n".join(code), token
