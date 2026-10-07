"""ssc 오버레이 render.sh 의 보조 그룹 스위치(2026-10-08, prod 오버레이와 같은 배관 -- tests/test_prod_render_switch.py).

ssc 사이트도 values.env 하나만 고치는 흐름이라 "values.env 로 끈다"(D9)가 되려면 render.sh → patch-config.yaml 배관이
있어야 한다. 실제 sh(dash)로 렌더해 고정한다: 키 생략 → "true"(하위호환), false → "false", 그 밖의 값 → rc 2 + FAIL.
ssc 디렉터리를 tmp 의 overlays/ssc 로 복사해 돌린다. 태그는 명시값이라 kubectl 을 부르지 않는다 -- PATH 앞의 심이
불리면 표식을 남겨 실패한다(이 호스트의 kubectl 은 실 테스트베드를 가리킨다).
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SSC = REPO_ROOT / "deploy" / "overlays" / "ssc"
KEY = "DMS_IDENTITY_SUPPLEMENTARY_GROUPS"
_RENDERED_LINE = re.compile(r'^\s*' + KEY + r':\s*"([^"]*)"\s*$', re.M)
_EXAMPLE_LINE = re.compile(r"^IDENTITY_SUPPLEMENTARY_GROUPS=.*$", re.M)


def _filled_example() -> str:
    text = (SSC / "values.env.example").read_text()
    return re.sub(r"\bREPLACE_([A-Z_]+)", lambda m: "v-" + m.group(1).lower().replace("_", "-"), text)


def _render(tmp_path, values_text):
    ssc = tmp_path / "overlays" / "ssc"
    shutil.copytree(SSC, ssc, ignore=shutil.ignore_patterns("values.env"))
    vals = tmp_path / "values.env"
    vals.write_text(values_text)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    called = tmp_path / "kubectl-called"
    shim = bindir / "kubectl"
    shim.write_text(f"#!/bin/sh\ntouch '{called}'\nexit 97\n")
    shim.chmod(0o755)
    env = {"PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}", "VALUES_ENV": str(vals)}
    r = subprocess.run(["sh", str(ssc / "render.sh")], env=env, capture_output=True, text=True, timeout=60)
    assert not called.exists(), "render.sh 가 kubectl 을 불렀다(명시 태그인데)"
    return r, tmp_path / "overlays" / ".ssc-rendered" / "patch-config.yaml"


def _value(r, patch):
    assert r.returncode == 0, (r.stdout, r.stderr)
    found = _RENDERED_LINE.findall(patch.read_text())
    assert len(found) == 1, found
    return found[0]


def test_example_renders_the_switch_on(tmp_path):
    r, patch = _render(tmp_path, _filled_example())
    assert _value(r, patch) == "true"


def test_values_env_without_the_key_still_renders_on(tmp_path):
    # 이 키가 생기기 전의 values.env -- 필수 KEYS 에 넣지 않았으므로 그대로 렌더되고 코드 기본과 같은 "true".
    r, patch = _render(tmp_path, _EXAMPLE_LINE.sub("", _filled_example()))
    assert _value(r, patch) == "true"


def test_false_renders_off(tmp_path):
    r, patch = _render(tmp_path, _EXAMPLE_LINE.sub("IDENTITY_SUPPLEMENTARY_GROUPS=false", _filled_example()))
    assert _value(r, patch) == "false"


@pytest.mark.parametrize("bad", ["True", "yes", "1", "off", "flase"])
def test_other_values_fail_loudly(tmp_path, bad):
    # config 파서는 true/1 외 전부 꺼짐으로 읽는다 -- 렌더가 오타를 통과시키면 기능이 조용히 꺼진다.
    r, patch = _render(tmp_path, _EXAMPLE_LINE.sub(f"IDENTITY_SUPPLEMENTARY_GROUPS={bad}", _filled_example()))
    assert r.returncode == 2 and "FAIL: IDENTITY_SUPPLEMENTARY_GROUPS" in r.stdout
    assert not patch.exists()
