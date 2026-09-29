"""에이전트 nslcd.conf 템플릿(deploy/docker/agent-entrypoint.sh) 계약 — 프로덕션 2026-09-29.

사고: 에이전트에 LDAP bind 계정이 주입되지 않아 nslcd 가 익명 바인드로 돌았고, 사내 LDAP 은
익명에게 rootDSE 만 보여주고 사용자 검색은 막아 모든 요청이 identity_not_ready_on_node 로
거부됐다. 부수로 sssd 식 콤마 URI 가 nslcd 에 그대로 들어가 한 덩어리 호스트명(gaierror)이
되어 페일오버가 없었다. 테스트베드 slapd 는 익명 읽기를 허용해 둘 다 가려졌다.

엔트리포인트를 --render-nslcd-conf 모드로 실제 실행해(렌더만, nslcd 기동 없음) 제어면
리졸버(identity_ldap.py) 미러를 고정한다: URI 분해, StartTLS-before-bind, bind 자격, 0600,
비밀번호 비노출, 개행 거부."""
import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ENTRYPOINT = REPO_ROOT / "deploy" / "docker" / "agent-entrypoint.sh"
PW = "S3cr#t p@ss"   # '#'·공백 포함 -- nslcd 는 줄 머리 '#'만 주석이고 bindpw 는 줄 나머지


def _render(tmp_path, **env):
    out = tmp_path / "nslcd.conf"
    base = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    proc = subprocess.run(["sh", str(ENTRYPOINT), "--render-nslcd-conf", str(out)],
                          env={**base, **env}, capture_output=True, text=True, timeout=30)
    return proc, out


def _lines(path):
    return path.read_text().splitlines()


def test_prod_shape_comma_uris_starttls_and_bind(tmp_path):
    proc, out = _render(
        tmp_path,
        DMS_LDAP_URI="ldap://ldap_p1/, ldap://ldap_r3/,ldap://ldaps/",
        DMS_LDAP_USER_BASE="dc=supercom,dc=samsung",
        DMS_LDAP_GROUP_BASE="dc=supercom,dc=samsung",
        DMS_LDAP_USE_START_TLS="true",
        DMS_LDAP_BIND_DN="uid=search_sc,ou=user,ou=ldap_user,dc=supercom,dc=samsung",
        DMS_LDAP_BIND_PW=PW)
    assert proc.returncode == 0, proc.stderr
    lines = _lines(out)
    # 한 줄에 URI 하나(콤마·후행 '/' 제거) -- identity_ldap._parse_uris 와 같은 목록
    assert [ln for ln in lines if ln.startswith("uri ")] == [
        "uri ldap://ldap_p1", "uri ldap://ldap_r3", "uri ldap://ldaps"]
    assert not any("," in ln for ln in lines if ln.startswith("uri "))
    # StartTLS 가 bind 보다 먼저 선언(nslcd 는 순서와 무관하게 bind 전에 TLS 를 올리지만
    # 설정이 그 의도를 그대로 읽히게), 인증서는 제어면처럼 검증 생략
    assert "ssl start_tls" in lines and "tls_reqcert never" in lines
    assert "binddn uid=search_sc,ou=user,ou=ldap_user,dc=supercom,dc=samsung" in lines
    assert f"bindpw {PW}" in lines
    assert lines.index("ssl start_tls") < lines.index("bindpw " + PW)
    assert "base passwd dc=supercom,dc=samsung" in lines
    # 비밀번호가 담긴 파일은 0600, 로그(stdout/stderr)에 비밀번호가 없다
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    assert PW not in proc.stdout and PW not in proc.stderr
    # 진단 한 줄: 서버 수·TLS·bind 모드(DN 은 비밀 아님)
    assert ("dms-agent: nslcd uri=3 server(s) start_tls=on "
            "bind=dn=uid=search_sc,ou=user,ou=ldap_user,dc=supercom,dc=samsung (password set)") in proc.stderr


def test_anonymous_when_no_bind_dn_and_says_so(tmp_path):
    proc, out = _render(tmp_path, DMS_LDAP_URI="ldap://10.10.10.30:389",
                        DMS_LDAP_USER_BASE="ou=People,dc=dms,dc=local")
    assert proc.returncode == 0, proc.stderr
    lines = _lines(out)
    assert not any(ln.startswith(("binddn", "bindpw")) for ln in lines)
    assert "bind=anonymous" in proc.stderr
    # DMS_LDAP_USE_START_TLS 미설정 = 리졸버 기본(true) -- config._parse_bool 미러
    assert "ssl start_tls" in lines


def test_dn_without_password_warns_and_does_not_write_half_a_bind(tmp_path):
    proc, out = _render(tmp_path, DMS_LDAP_URI="ldap://h", DMS_LDAP_BIND_DN="uid=x,dc=d")
    assert proc.returncode == 0
    lines = _lines(out)
    assert not any(ln.startswith(("binddn", "bindpw")) for ln in lines)
    assert "WARNING DMS_LDAP_BIND_DN is set but DMS_LDAP_BIND_PW is empty" in proc.stderr
    assert "bind=anonymous" in proc.stderr


@pytest.mark.parametrize("value,expect_tls", [
    ("true", True), ("1", True), ("TRUE", True), (" true ", True),
    ("false", False), ("0", False), ("", False), ("no", False),
])
def test_start_tls_parsing_mirrors_config_parse_bool(tmp_path, value, expect_tls):
    # 명시적 "" 는 false -- 미설정만 기본(true). 제어면 _parse_bool 과 같은 규칙이어야
    # 에이전트와 리졸버가 같은 서버에 같은 방식으로 붙는다.
    proc, out = _render(tmp_path, DMS_LDAP_URI="ldap://h", DMS_LDAP_USE_START_TLS=value)
    assert proc.returncode == 0, proc.stderr
    assert ("ssl start_tls" in _lines(out)) is expect_tls


@pytest.mark.parametrize("var", ["DMS_LDAP_BIND_PW", "DMS_LDAP_BIND_DN", "DMS_LDAP_URI"])
def test_line_break_in_a_value_is_refused_without_echoing_it(tmp_path, var):
    env = {"DMS_LDAP_URI": "ldap://h", "DMS_LDAP_BIND_DN": "uid=x,dc=d",
           "DMS_LDAP_BIND_PW": "pw"}
    env[var] = "evil\nbinddn cn=admin"
    proc, out = _render(tmp_path, **env)
    assert proc.returncode == 64
    assert f"{var} contains a line break" in proc.stderr
    assert "evil" not in proc.stderr and "evil" not in proc.stdout
    assert not out.exists()


@pytest.mark.parametrize("pw", ["pw-trail ", "pw-tab\t", " lead-pw", "\tlead-tab"])
def test_edge_whitespace_in_password_warns_without_echoing_it(tmp_path, pw):
    # nslcd.conf 는 값을 트리밍하므로 앞뒤 공백이 있는 비밀번호는 표현할 수 없다 -- 조용히
    # 다른 비밀번호로 바인드하는 대신 경고한다(값은 찍지 않는다).
    proc, _ = _render(tmp_path, DMS_LDAP_URI="ldap://h", DMS_LDAP_BIND_DN="uid=x,dc=d",
                      DMS_LDAP_BIND_PW=pw)
    assert proc.returncode == 0
    assert "leading/trailing whitespace" in proc.stderr
    assert pw.strip() not in proc.stderr


def test_ordinary_password_does_not_warn(tmp_path):
    proc, _ = _render(tmp_path, DMS_LDAP_URI="ldap://h", DMS_LDAP_BIND_DN="uid=x,dc=d",
                      DMS_LDAP_BIND_PW=PW)
    assert "WARNING" not in proc.stderr


def test_empty_uri_list_is_refused(tmp_path):
    proc, out = _render(tmp_path, DMS_LDAP_URI=" , ,")
    assert proc.returncode == 64
    assert "no usable URI" in proc.stderr


def test_render_only_mode_never_starts_nslcd(tmp_path):
    # 렌더 모드는 설정만 쓰고 끝난다 -- /run/nslcd·nslcd 기동에 손대지 않는다(테스트 격리).
    text = ENTRYPOINT.read_text()
    render_exit = text.index('[ -z "$render_only" ] || exit 0')
    assert render_exit < text.index("mkdir -p /run/nslcd") < text.index("nslcd -d")
