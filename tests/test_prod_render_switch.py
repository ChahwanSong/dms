"""프로덕션 오버레이 render.sh 의 보조 그룹 스위치(2026-10-07, D9 "문제 시 values.env 로 끔").

스위치 DMS_IDENTITY_SUPPLEMENTARY_GROUPS 는 코드 기본 켬이라 base(20-config.yaml)·테스트베드 오버레이에는 키가
없다. 프로덕션은 values.env 하나만 고치는 흐름이라(overlays/prod/README.md), "values.env 로 끈다" 가 실제로
되려면 render.sh → patch-config.yaml 배관이 있어야 한다. 여기서 실제 sh(이 호스트 /bin/sh = dash)로 렌더해
고정한다:
- 키 생략(이 키가 생기기 전의 values.env) → "true" 로 렌더(하위호환 -- 필수 KEYS 에 넣지 않았다),
- false → "false", 그 밖의 값 → rc 2 + FAIL 한 줄(config 파서는 true/1 외 전부 꺼짐이라 렌더가 오타를
  통과시키면 기능이 조용히 꺼진다).
render 는 파일 치환만 하므로 prod 디렉터리를 tmp 의 overlays/prod 로 복사해 돌린다(../../k8s 참조는 렌더 산출물
안의 문자열일 뿐 여기서 해석되지 않는다). 태그는 'live' 가 아닌 명시값이라 kubectl 을 부르지 않는다 -- PATH 앞의
kubectl 심이 불리면 표식을 남겨 테스트가 실패한다(이 호스트의 kubectl 은 실 테스트베드를 가리킨다).
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from dms.config import _parse_bool

REPO_ROOT = Path(__file__).resolve().parent.parent
PROD = REPO_ROOT / "deploy" / "overlays" / "prod"
KEY = "DMS_IDENTITY_SUPPLEMENTARY_GROUPS"
_RENDERED_LINE = re.compile(r'^\s*' + KEY + r':\s*"([^"]*)"\s*$', re.M)
_EXAMPLE_LINE = re.compile(r"^IDENTITY_SUPPLEMENTARY_GROUPS=.*$", re.M)


def _filled_example() -> str:
    """values.env.example 의 REPLACE_<X> 를 그럴듯한 값으로 채운다(태그 포함 -- 'live' 아님)."""
    text = (PROD / "values.env.example").read_text()
    return re.sub(r"\bREPLACE_([A-Z_]+)",
                  lambda m: "v-" + m.group(1).lower().replace("_", "-"), text)


def _render(tmp_path, values_text):
    prod = tmp_path / "overlays" / "prod"
    # 로컬에 채운 values.env(gitignore)가 있어도 복사하지 않는다 -- VALUES_ENV 만 읽게.
    shutil.copytree(PROD, prod, ignore=shutil.ignore_patterns("values.env"))
    vals = tmp_path / "values.env"
    vals.write_text(values_text)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    called = tmp_path / "kubectl-called"
    shim = bindir / "kubectl"
    shim.write_text(f"#!/bin/sh\ntouch '{called}'\nexit 97\n")
    shim.chmod(0o755)
    env = {"PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}",
           "VALUES_ENV": str(vals)}
    r = subprocess.run(["sh", str(prod / "render.sh")], env=env, capture_output=True,
                       text=True, timeout=60)
    assert not called.exists(), "render.sh 가 kubectl 을 불렀다(명시 태그인데)"
    return r, tmp_path / "overlays" / ".prod-rendered" / "patch-config.yaml"


def _rendered_value(r, patch):
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert r.stdout.strip().splitlines()[-1].startswith("RENDERED: "), r.stdout
    text = patch.read_text()
    values = _RENDERED_LINE.findall(text)
    assert len(values) == 1, values
    # 렌더 스크립트의 잔여 토큰 그물과 같은 기준(주석 줄 제외)으로 다시 본다.
    leftover = [line for line in text.splitlines()
                if "REPLACE_" in line and not line.lstrip().startswith("#")]
    assert leftover == []
    return values[0]


def test_example_carries_the_optional_switch_default_true(tmp_path):
    example = (PROD / "values.env.example").read_text()
    assert _EXAMPLE_LINE.findall(example) == ["IDENTITY_SUPPLEMENTARY_GROUPS=true"]
    r, patch = _render(tmp_path, _filled_example())
    assert _rendered_value(r, patch) == "true"


def test_values_env_without_the_key_still_renders_true(tmp_path):
    # 이 키가 생기기 전에 채운 values.env -- 필수 키가 아니라 그대로 렌더되고 코드 기본과 같은 "true".
    old = _EXAMPLE_LINE.sub("", _filled_example())
    assert "IDENTITY_SUPPLEMENTARY_GROUPS" not in [
        line.split("=", 1)[0] for line in old.splitlines() if "=" in line and not line.startswith("#")]
    r, patch = _render(tmp_path, old)
    assert _rendered_value(r, patch) == "true"


def test_empty_value_is_treated_as_omitted(tmp_path):
    # `${VAR:-true}` -- 빈 값은 생략과 같다(꺼짐이 아니다: 끄려면 false 를 명시).
    r, patch = _render(tmp_path, _EXAMPLE_LINE.sub("IDENTITY_SUPPLEMENTARY_GROUPS=", _filled_example()))
    assert _rendered_value(r, patch) == "true"


def test_false_renders_false(tmp_path):
    r, patch = _render(tmp_path, _EXAMPLE_LINE.sub("IDENTITY_SUPPLEMENTARY_GROUPS=false", _filled_example()))
    assert _rendered_value(r, patch) == "false"


@pytest.mark.parametrize("bad", ["maybe", "True", "FALSE", "1", "0", "yes", "off"])
def test_other_values_fail_loudly_before_rendering(tmp_path, bad):
    r, patch = _render(tmp_path, _EXAMPLE_LINE.sub(f"IDENTITY_SUPPLEMENTARY_GROUPS={bad}", _filled_example()))
    assert r.returncode == 2, (r.stdout, r.stderr)
    assert "FAIL: IDENTITY_SUPPLEMENTARY_GROUPS 는 true|false" in r.stdout
    assert "RENDERED:" not in r.stdout
    # 검증이 rm -rf/mkdir 보다 먼저라 산출물이 생기지 않는다(반쯤 렌더된 디렉터리로 apply 되지 않게).
    assert not patch.parent.exists()


@pytest.mark.parametrize("rendered,expected", [("true", True), ("false", False)])
def test_rendered_values_mean_what_they_say_to_the_config_parser(rendered, expected):
    # 렌더가 허용하는 두 값이 서버 설정에서 의도대로 읽힌다(true/1 외 전부 꺼짐 -- config._parse_bool).
    assert _parse_bool({KEY: rendered}, KEY, default=not expected) is expected


def _data_keys(path):
    return [line.split(":", 1)[0].strip() for line in path.read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#") and ":" in line]


def test_base_and_testbed_leave_the_switch_to_the_code_default():
    # base 는 사이트 중립(키 없음 = 코드 기본 켬), 테스트베드도 무변경 -- 끌 때만 사이트 오버레이에 넣는다.
    assert KEY not in _data_keys(REPO_ROOT / "deploy" / "k8s" / "20-config.yaml")
    assert KEY not in _data_keys(REPO_ROOT / "deploy" / "overlays" / "testbed" / "patch-config.yaml")
    assert KEY in _data_keys(PROD / "patch-config.yaml")
