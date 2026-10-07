"""에이전트 디렉터리 설정 하달(방안 A, 2026-09-29).

프로덕션 사고: DaemonSet 에 bind 계정 env 가 없어 nslcd 가 익명으로 돌았고, 그 env 를
넣으려면 포탈 릴리스(이미지만 패치)와 별도로 매니페스트 apply 가 필요했다. 이제 제어면이
보고 응답으로 LDAP 설정을 내려주고 에이전트가 nslcd 를 수렴시킨다 -- 이미지 릴리스만으로
충분하고, 에이전트와 플래너가 같은 추출(identity_ldap.ldap_directory_config)을 쓴다.

고정하는 계약:
- 서버: 능력 선언(보고의 "directory") 없으면 키 자체가 없다 / 해시 다를 때만 비밀번호 /
  해시는 HMAC(session_secret) / 미구성이면 null / 리졸버가 실제로 쓰는 값 == 하달 값.
- 에이전트: 실제 엔트리포인트 렌더러로 렌더(백슬래시 보존) / 내용 같고 nslcd 건강하면 재기동
  없음 / 수렴 뒤 죽은 nslcd 는 해시만 온 주기에도 재기동 / 좀비는 죽은 것 / 실패하면 해시를
  비워 재시도·되돌림 수렴 / PID 재사용·pidfile 심볼릭 링크 방어 / nslcd 최소 env / 상태 변화마다
  비밀 없는 로그 한 줄 / 관리 안 하면 선언도 적용도 없음 / 비밀번호는 보고·로그·오류 어디에도 없다.
(적대적 리뷰 워크플로 2026-09-29 의 발견 A~G 를 각각 여기서 고정한다.)
"""
import dataclasses
import hashlib
import json
import os
import signal
import stat
import subprocess
import sys
import time
import types
from pathlib import Path

import httpx
import pytest

from dms.agent import directory as directory_mod
from dms.agent.directory import NslcdDirectory, _read_state
from dms.agent.runner import AgentRunner
from dms.agent_directory import directory_block, directory_hash, directory_payload
from dms.config import AgentSettings, Settings
from dms.identity_ldap import build_ldap_resolver, ldap_directory_config

REPO_ROOT = Path(__file__).resolve().parent.parent
ENTRYPOINT = REPO_ROOT / "deploy" / "docker" / "agent-entrypoint.sh"
PW = "S3cr#t p@ss"
LDAP = dict(ldap_uri="ldap://p1/, ldap://r3/", ldap_user_base="ou=People,dc=d",
            ldap_group_base="ou=Groups,dc=d", ldap_bind_dn="uid=search,dc=d",
            ldap_bind_pw=PW, ldap_use_start_tls=True)


def _with_ldap(settings, **kw):
    return dataclasses.replace(settings, **{**LDAP, **kw})


def _headers(node="node-a"):
    return {"Authorization": "Bearer tok-shared", "x-dms-actor": f"node:{node}"}


def _report(client, extra=None, node="node-a"):
    r = client.post("/api/agent/report", json={"node_name": node, **(extra or {})},
                    headers=_headers(node))
    assert r.status_code == 200, r.text
    return r


# --- 서버 ---------------------------------------------------------------------

def test_old_agent_without_capability_never_receives_the_block(client):
    client.app.state.settings = _with_ldap(client.app.state.settings)
    r = _report(client)
    assert "directory" not in r.json()
    assert PW not in r.text


def test_new_agent_gets_full_block_once_then_hash_only(client):
    client.app.state.settings = _with_ldap(client.app.state.settings)
    block = _report(client, {"directory": {"hash": None}}).json()["directory"]
    assert block["uris"] == ["ldap://p1", "ldap://r3"]       # _parse_uris 와 같은 분해
    assert block["user_base"] == "ou=People,dc=d" and block["group_base"] == "ou=Groups,dc=d"
    assert block["start_tls"] is True
    assert block["bind_dn"] == "uid=search,dc=d" and block["bind_pw"] == PW
    assert len(block["hash"]) == 32 and int(block["hash"], 16) >= 0
    r = _report(client, {"directory": {"hash": block["hash"]}})
    assert r.json()["directory"] == {"hash": block["hash"]}
    assert PW not in r.text


def test_password_rotation_changes_hash_and_resends(client):
    client.app.state.settings = _with_ldap(client.app.state.settings)
    h1 = _report(client, {"directory": {"hash": None}}).json()["directory"]["hash"]
    client.app.state.settings = _with_ldap(client.app.state.settings, ldap_bind_pw="rotated-pw")
    block = _report(client, {"directory": {"hash": h1}}).json()["directory"]
    assert block["hash"] != h1 and block["bind_pw"] == "rotated-pw"


def test_non_string_reported_hash_is_treated_as_unknown(client):
    client.app.state.settings = _with_ldap(client.app.state.settings)
    block = _report(client, {"directory": {"hash": 12345}}).json()["directory"]
    assert block["bind_pw"] == PW


def test_unconfigured_directory_is_null(client):
    r = _report(client, {"directory": {"hash": None}})
    assert r.json()["directory"] is None


def test_hash_is_keyed_hmac_not_plain_sha256(settings):
    s = _with_ldap(settings)
    payload = directory_payload(s)
    h = directory_hash(payload, s)
    plain = hashlib.sha256(json.dumps(payload, sort_keys=True,
                                      separators=(",", ":")).encode()).hexdigest()[:32]
    assert h != plain                                   # 평문 해시면 오프라인 대입 가능
    assert h == directory_hash(payload, s)              # 결정적
    other = dataclasses.replace(s, session_secret="another-secret")
    assert directory_hash(payload, other) != h          # 키는 서버 비밀


@pytest.mark.parametrize("override", [
    {}, {"ldap_uri": ""}, {"ldap_user_base": ""}, {"ldap_group_base": "REPLACE_WITH_BASE"},
    {"ldap_bind_dn": "", "ldap_bind_pw": ""}, {"ldap_use_start_tls": False},
])
def test_agent_block_exists_exactly_when_the_resolver_does(settings, override):
    s = _with_ldap(settings, **override)
    cfg = ldap_directory_config(s)
    assert (build_ldap_resolver(s) is None) == (cfg is None)
    assert (directory_block(s, None) is None) == (cfg is None)


@pytest.mark.parametrize("override", [
    {}, {"ldap_use_start_tls": False}, {"ldap_bind_dn": "", "ldap_bind_pw": ""},
    {"ldap_uri": "ldap://only/"},
])
def test_resolver_connects_with_exactly_the_values_the_agent_gets(settings, monkeypatch, override):
    # 리뷰 G: "같은 추출점" 을 구조가 아니라 **실제 connect 인자**로 고정한다 -- 리졸버가
    # 설정을 다른 길로 읽기 시작하면(예: getattr 로 되돌아가기, start_tls 반전) 플래너와
    # 노드가 갈라진다(2026-09-29 사고 유형). ldap3 는 가짜로 바꿔 인자만 기록한다.
    # 2026-10-07: URI 를 순서대로 하나씩 연다(connect_first) -- 마지막 URI 만 성공하게 해 시도한 순서 전체를 본다.
    import ssl
    tried = []

    def _conn(server, user=None, password=None, auto_bind=None, receive_timeout=None):
        tried.append(dict(server=server, user=user, password=password, auto_bind=auto_bind))
        if server[1] != payload["uris"][-1]:
            raise OSError("down")
        return "conn"
    fake = types.SimpleNamespace(
        Tls=lambda validate=None: ("tls", validate),
        Server=lambda u, tls=None, connect_timeout=None, get_info=None: ("server", u, tls, get_info),
        AUTO_BIND_TLS_BEFORE_BIND="TLS_BEFORE_BIND", NONE="NO_INFO", Connection=_conn)
    monkeypatch.setitem(sys.modules, "ldap3", fake)
    s = _with_ldap(settings, **override)
    payload = directory_payload(s)
    assert build_ldap_resolver(s)._connect() == "conn"
    servers = [t["server"] for t in tried]
    assert [srv[1] for srv in servers] == payload["uris"]
    # bind 뒤 서버 정보 읽기 끔(get_info=NONE) -- 한 URI 시도 = 3T 틱 시간 불변식의 전제(identity_ldap 모듈 docstring).
    assert all(srv[3] == "NO_INFO" for srv in servers)
    assert all(t["user"] == payload["bind_dn"] and t["password"] == payload["bind_pw"] for t in tried)
    if payload["start_tls"]:
        assert all(t["auto_bind"] == "TLS_BEFORE_BIND" for t in tried)            # bind 전에 TLS
        assert all(srv[2] == ("tls", ssl.CERT_NONE) for srv in servers)
    else:
        assert all(t["auto_bind"] is True for t in tried) and all(srv[2] is None for srv in servers)


def test_persisted_report_holds_only_the_status(client, db):
    client.app.state.settings = _with_ldap(client.app.state.settings)
    _report(client, {"directory": {"hash": None, "source": "env"}})
    stored = [r["report"] for r in db.query("SELECT report FROM agent_reports")]
    assert stored and all(PW not in (s if isinstance(s, str) else json.dumps(s)) for s in stored)


# --- 에이전트: nslcd 수렴 -------------------------------------------------------

class Procs:
    """가짜 프로세스 테이블(부트스트랩 nslcd 용). alive → kill 하면 좀비 → waitpid(WNOHANG)
    가 거두면 사라진다. 모르는 PID 는 ChildProcessError(우리 자식 아님)."""

    def __init__(self, alive=(), zombies=(), comm="nslcd"):
        self.alive, self.zombies, self.comm = set(alive), set(zombies), comm
        self.kills, self.reaped = [], []

    def kill(self, pid, sig):
        self.kills.append((pid, sig))
        if pid in self.alive:
            self.alive.discard(pid)
            self.zombies.add(pid)

    def waitpid(self, pid, flags):
        if pid in self.alive:
            return (0, 0)
        if pid in self.zombies:
            self.zombies.discard(pid)
            self.reaped.append(pid)
            return (pid, 0)
        raise ChildProcessError(pid)

    def read_comm(self, pid):
        return self.comm if (pid in self.alive or pid in self.zombies) else None

    def read_state(self, pid):
        return "S" if pid in self.alive else ("Z" if pid in self.zombies else None)


class FakeProc:
    """Popen 대역(에이전트가 띄운 nslcd)."""

    def __init__(self, pid, sock, make_socket=True):
        self.pid, self.returncode = pid, None
        if make_socket:
            Path(sock).touch()

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


class Clock:
    def __init__(self):
        self.t = 0.0

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.t += s


def _block(**kw):
    base = Settings(database_url="u", shared_token="t", admin_token="a",
                    session_secret="sess-secret")
    return directory_block(_with_ldap(base, **kw), None)


def _nslcd(tmp_path, *, procs=None, bootstrap_pid=4242, sockets=None, managed=True):
    """실제 엔트리포인트 렌더러 + 가짜 프로세스 조작. sockets: spawn 마다 소켓을 만들지 여부
    목록(기본 전부 True). 반환: (dir, rec)."""
    procs = procs if procs is not None else Procs(alive={bootstrap_pid} if bootstrap_pid else ())
    rec = {"procs": procs, "spawned": [], "renders": 0, "socket_at_spawn": [],
           "spawn_env": [], "logs": []}
    sock = tmp_path / "socket"
    pidfile = tmp_path / "nslcd.pid"
    if bootstrap_pid is not None and not pidfile.is_symlink():
        pidfile.write_text(f"{bootstrap_pid}\n")
    clock = Clock()
    plan = list(sockets) if sockets is not None else None

    def run(argv, **kw):
        rec["renders"] += 1
        return subprocess.run(["sh", *argv], **kw)   # 저장소의 엔트리포인트는 실행 비트 없음

    def spawn(argv, stdout=None, stderr=None, env=None):
        rec["socket_at_spawn"].append(sock.exists())
        rec["spawn_env"].append(env)
        make = plan.pop(0) if plan else True
        p = FakeProc(9000 + len(rec["spawned"]), sock, make_socket=make)
        rec["spawned"].append(p)
        return p

    d = NslcdDirectory(managed=managed, conf_path=str(tmp_path / "nslcd.conf"),
                       render_cmd=str(ENTRYPOINT), pidfile=str(pidfile),
                       socket_path=str(sock), log_path=str(tmp_path / "nslcd.log"),
                       run=run, spawn=spawn, kill=procs.kill, waitpid=procs.waitpid,
                       read_comm=procs.read_comm, read_state=procs.read_state,
                       sleep=clock.sleep, monotonic=clock.monotonic,
                       log=rec["logs"].append, socket_timeout=2.0, stop_timeout=1.0)
    return d, rec


def _lay_conf(tmp_path, block):
    """다른 디렉터리 인스턴스로 같은 설정을 미리 깔아 둔다(부트스트랩 흉내)."""
    first, _ = _nslcd(tmp_path, bootstrap_pid=None)
    first.apply(block)
    assert first.report()["error"] is None


def test_first_block_renders_bind_and_restarts_the_bootstrap_nslcd(tmp_path):
    d, rec = _nslcd(tmp_path)
    block = _block()
    d.apply(block)
    conf = (tmp_path / "nslcd.conf")
    lines = conf.read_text().splitlines()
    assert "binddn uid=search,dc=d" in lines and f"bindpw {PW}" in lines
    assert "ssl start_tls" in lines and ["uri ldap://p1", "uri ldap://r3"] == [
        ln for ln in lines if ln.startswith("uri ")]
    assert stat.S_IMODE(conf.stat().st_mode) == 0o600
    procs = rec["procs"]
    assert procs.kills == [(4242, signal.SIGTERM)] and procs.reaped == [4242]   # 정지 + 거둠
    assert len(rec["spawned"]) == 1
    assert (tmp_path / "nslcd.pid").read_text().strip() == "9000"
    st = d.report()
    assert st["source"] == "api" and st["hash"] == block["hash"] and st["error"] is None
    assert st["bind"] == "dn=uid=search,dc=d" and st["start_tls"] is True and st["uri_count"] == 2
    assert rec["logs"] == ["dms-agent: directory applied source=api uri=2 start_tls=on "
                           "bind=dn=uid=search,dc=d restarted=yes"]
    assert PW not in json.dumps(st) and all(PW not in ln for ln in rec["logs"])


def test_nslcd_is_spawned_with_a_minimal_env(tmp_path, monkeypatch):
    # nslcd 는 네트워크 입력을 파싱한다 -- 에이전트 env(관리자급 공유 토큰·bind 비밀번호)를
    # 물려주면 장악된 nslcd 가 /proc/self/environ 으로 읽는다.
    monkeypatch.setenv("DMS_SHARED_TOKEN", "tok-admin-level")
    monkeypatch.setenv("DMS_LDAP_BIND_PW", PW)
    d, rec = _nslcd(tmp_path)
    d.apply(_block())
    assert list(rec["spawn_env"][0]) == ["PATH"]


def test_hash_only_block_is_a_noop_while_nslcd_is_healthy(tmp_path):
    d, rec = _nslcd(tmp_path)
    block = _block()
    d.apply(block)
    d.apply({"hash": block["hash"]})
    assert rec["renders"] == 1 and len(rec["spawned"]) == 1 and len(rec["logs"]) == 1


def test_dead_nslcd_after_convergence_is_restarted_on_a_hash_only_cycle(tmp_path):
    # 리뷰 B: 수렴 뒤엔 서버가 {"hash"} 만 준다. 예전엔 여기서 바로 반환해 크래시·OOM 으로
    # 죽은 nslcd 가 영영 안 살아났고 상태는 정상으로 보고됐다(사고와 같은 증상).
    d, rec = _nslcd(tmp_path)
    block = _block()
    d.apply(block)
    rec["spawned"][0].returncode = -9
    (tmp_path / "socket").unlink()
    d.apply({"hash": block["hash"]})
    assert len(rec["spawned"]) == 2 and rec["renders"] == 1      # 디스크 적용본으로 재기동
    assert d.report()["error"] is None and d.report()["hash"] == block["hash"]
    assert rec["logs"][-1] == "dms-agent: nslcd restarted"


def test_identical_render_with_healthy_bootstrap_nslcd_does_not_restart(tmp_path):
    # env 부트스트랩이 이미 같은 설정으로 띄운 경우 -- 첫 전체 블록을 렌더해 보니 내용이
    # 같고 nslcd 가 살아 있다 → 재기동 없이 해시만 기록.
    block = _block()
    _lay_conf(tmp_path, block)
    d, rec = _nslcd(tmp_path, bootstrap_pid=4242)
    d.apply(block)
    assert rec["procs"].kills == [] and rec["spawned"] == []
    assert d.report()["hash"] == block["hash"] and d.report()["error"] is None
    assert rec["logs"][-1].endswith("restarted=no")
    assert not (tmp_path / "nslcd.conf.dms-new").exists()


def test_identical_render_but_zombie_bootstrap_nslcd_restarts(tmp_path):
    # 리뷰 B: 죽었지만 아직 거두지 않은 부트스트랩 nslcd 는 /proc 에 comm=nslcd 인 좀비로
    # 남는다 -- comm 만 보면 산 것으로 오판해 재기동하지 않았다.
    block = _block()
    _lay_conf(tmp_path, block)
    d, rec = _nslcd(tmp_path, procs=Procs(zombies={4242}), bootstrap_pid=4242)
    d.apply(block)
    assert rec["procs"].reaped == [4242] and len(rec["spawned"]) == 1


def test_real_zombie_is_not_counted_as_alive(tmp_path):
    # 가짜가 아니라 실제 커널: 자식을 끝내 좀비로 만든 뒤 판정한다.
    pid = os.fork()
    if pid == 0:
        os._exit(0)
    deadline = time.monotonic() + 5
    while _read_state(pid) != "Z" and time.monotonic() < deadline:
        time.sleep(0.01)
    assert _read_state(pid) == "Z"
    (tmp_path / "socket").touch()
    (tmp_path / "nslcd.pid").write_text(f"{pid}\n")
    d = NslcdDirectory(managed=True, pidfile=str(tmp_path / "nslcd.pid"),
                       socket_path=str(tmp_path / "socket"),
                       read_comm=lambda p: "nslcd" if p == pid else None)
    assert d._nslcd_alive() is False
    assert _read_state(pid) is None                                  # 거둬졌다


def test_render_refusal_keeps_config_clears_hash_and_never_echoes_the_value(tmp_path):
    d, rec = _nslcd(tmp_path)
    d.apply(_block())
    before = (tmp_path / "nslcd.conf").read_bytes()
    bad = _block(ldap_bind_pw="evil\nbinddn cn=admin")
    d.apply(bad)
    st = d.report()
    assert st["error"].startswith("render refused (exit 64)")
    assert "evil" not in st["error"] and "cn=admin" not in json.dumps(st)
    assert st["hash"] is None                                  # 비움 → 다음 응답은 전체 블록
    assert (tmp_path / "nslcd.conf").read_bytes() == before
    assert len(rec["spawned"]) == 1 and not (tmp_path / "nslcd.conf.dms-new").exists()
    assert rec["logs"][-1].startswith("dms-agent: directory apply failed: render refused")
    assert all("evil" not in ln for ln in rec["logs"])


def test_revert_after_a_failed_apply_converges(tmp_path):
    # 리뷰 C: H1 적용 → H2 가 실패(conf 는 H2, nslcd 죽음) → 운영자가 H1 으로 되돌림. 예전엔
    # 실패 뒤에도 보고 해시가 H1 이라 서버가 {"hash": H1} 만 줘서 H2 conf + 죽은 nslcd 로
    # 멈춰 섰다. 이제 실패하면 해시를 비우므로 서버가 전체 H1 을 다시 주고 수렴한다.
    d, rec = _nslcd(tmp_path, sockets=[True, False, True])
    h1, h2 = _block(), _block(ldap_bind_pw="new-pw")
    d.apply(h1)
    d.apply(h2)
    assert d.report()["error"] == "nslcd socket not up after 2s" and d.report()["hash"] is None
    d.apply(h1)
    assert d.report()["hash"] == h1["hash"] and d.report()["error"] is None
    assert f"bindpw {PW}" in (tmp_path / "nslcd.conf").read_text().splitlines()
    assert len(rec["spawned"]) == 3


def test_socket_timeout_is_reported_and_retried_next_cycle(tmp_path):
    d, rec = _nslcd(tmp_path, sockets=[False, False])
    block = _block()
    d.apply(block)
    assert d.report()["error"] == "nslcd socket not up after 2s"
    assert d.report()["hash"] is None
    d.apply(block)                                            # 서버가 전체 블록 재전송
    assert len(rec["spawned"]) == 2


def test_stale_socket_is_removed_before_spawn(tmp_path):
    (tmp_path / "socket").touch()                             # SIGKILL 된 nslcd 의 잔재
    d, rec = _nslcd(tmp_path, bootstrap_pid=None)
    d.apply(_block())
    assert rec["socket_at_spawn"] == [False]


def test_reused_pid_that_is_not_nslcd_is_never_killed(tmp_path):
    d, rec = _nslcd(tmp_path, procs=Procs(alive={4242}, comm="bash"))
    d.apply(_block())
    assert rec["procs"].kills == [] and len(rec["spawned"]) == 1


def test_pidfile_symlink_is_neither_followed_nor_clobbered(tmp_path):
    # 리뷰 E: 장악된 nslcd 가 PID 파일을 심볼릭 링크로 바꿔도 root 에이전트가 링크 대상을
    # 읽거나 잘라 쓰지 않는다(O_NOFOLLOW, 임시 파일 + rename). 운영 경로는 root 전용 디렉터리.
    victim = tmp_path / "precious.dat"
    victim.write_text("4242\n")                               # 링크를 따라가면 PID 처럼 읽힌다
    (tmp_path / "nslcd.pid").symlink_to(victim)
    d, rec = _nslcd(tmp_path, procs=Procs(alive={4242}), bootstrap_pid=4242)
    d.apply(_block())
    assert rec["procs"].kills == []                           # 링크를 따라가 4242 를 죽이지 않음
    assert victim.read_text() == "4242\n"                     # 대상 무손상
    pidfile = tmp_path / "nslcd.pid"
    assert not pidfile.is_symlink() and pidfile.read_text().strip() == "9000"
    assert directory_mod.PIDFILE.startswith("/run/dms-agent/")


def test_backslash_password_reaches_nslcd_conf_byte_for_byte(tmp_path):
    # 리뷰 A 를 에이전트 경로로: 서버가 준 값이 렌더러를 거쳐 그대로 nslcd.conf 에 닿는다.
    pw = r"Q7\cz\\x\0101\n"
    d, _ = _nslcd(tmp_path)
    d.apply(_block(ldap_bind_pw=pw))
    assert d.report()["error"] is None
    assert f"bindpw {pw}" in (tmp_path / "nslcd.conf").read_text().split("\n")


@pytest.mark.parametrize("block", [
    "x", {"hash": "zz"}, {"hash": "a" * 32, "uris": []},
    {"hash": "a" * 32, "uris": ["ldap://h"], "user_base": "", "group_base": "g", "start_tls": True},
    {"hash": "a" * 32, "uris": ["ldap://h"], "user_base": "u", "group_base": "g", "start_tls": "yes"},
    {"hash": "a" * 32, "uris": ["ldap://h"], "user_base": "u", "group_base": "g",
     "start_tls": True, "bind_pw": 7},
])
def test_malformed_blocks_are_reported_not_raised(tmp_path, block):
    d, rec = _nslcd(tmp_path)
    d.apply(block)
    assert d.report()["error"].startswith("malformed directory")
    assert rec["renders"] == 0 and rec["spawned"] == []


def test_unmanaged_agent_neither_declares_nor_applies(tmp_path):
    d, rec = _nslcd(tmp_path, managed=False)
    assert d.report() is None
    d.apply(_block())
    assert rec["renders"] == 0 and not (tmp_path / "nslcd.conf").exists()


def test_null_block_keeps_bootstrap(tmp_path):
    d, rec = _nslcd(tmp_path)
    d.apply(None)
    assert rec["renders"] == 0 and d.report()["source"] == "env" and rec["logs"] == []


# --- 러너 통합 ------------------------------------------------------------------

SETTINGS = AgentSettings(api_url="http://api", shared_token="tok", node_name="node-a",
                         interval_seconds=60, mountinfo_path="/unused")


def _quiet(monkeypatch):
    monkeypatch.setattr("dms.agent.runner._read_text", lambda path: "")
    monkeypatch.setattr("dms.agent.runner.probe_tools", lambda names, **k: [])
    monkeypatch.setattr("dms.agent.runner.probe_identities", lambda users, **k: [])


def _state():
    return {"storages": [], "probe_targets": [], "interval": 60}


def test_runner_declares_capability_applies_block_and_never_posts_the_password(
        tmp_path, monkeypatch):
    _quiet(monkeypatch)
    d, _ = _nslcd(tmp_path)
    block = _block()
    posted = []

    def handler(request):
        body = json.loads(request.content)
        posted.append((request.content.decode(), body))
        reported = (body.get("directory") or {}).get("hash")
        return httpx.Response(200, json={
            "storages": [], "identity_probe_targets": [], "report_interval_seconds": 60,
            "directory": {"hash": block["hash"]} if reported == block["hash"] else block})

    runner = AgentRunner(SETTINGS, httpx.Client(transport=httpx.MockTransport(handler),
                                                base_url="http://api"), directory=d)
    runner.run_once(_state())
    runner.run_once(_state())
    first, second = posted
    assert first[1]["directory"]["hash"] is None and first[1]["directory"]["source"] == "env"
    assert second[1]["directory"]["hash"] == block["hash"]
    assert second[1]["directory"]["source"] == "api"
    assert all(PW not in raw for raw, _ in posted)


def test_runner_without_management_sends_no_directory_key(monkeypatch):
    _quiet(monkeypatch)
    posted = []

    def handler(request):
        posted.append(json.loads(request.content))
        return httpx.Response(200, json={"storages": [], "identity_probe_targets": [],
                                         "report_interval_seconds": 60})

    runner = AgentRunner(SETTINGS, httpx.Client(transport=httpx.MockTransport(handler),
                                                base_url="http://api"))
    runner.run_once(_state())
    assert "directory" not in posted[0]


def test_directory_crash_does_not_lose_the_rest_of_the_response(monkeypatch, capsys):
    _quiet(monkeypatch)

    class Boom:
        def report(self):
            return {"hash": None}

        def apply(self, block):
            raise RuntimeError(f"leak {PW}")

    def handler(request):
        return httpx.Response(200, json={
            "storages": [{"storage_name": "s", "mount_path": "/s", "managed_root": "/s"}],
            "identity_probe_targets": ["alice"], "report_interval_seconds": 30,
            "directory": {"hash": "a" * 32}})

    runner = AgentRunner(SETTINGS, httpx.Client(transport=httpx.MockTransport(handler),
                                                base_url="http://api"), directory=Boom())
    state = runner.run_once(_state())
    assert state["probe_targets"] == ["alice"] and state["interval"] == 30
    err = capsys.readouterr().err
    assert "RuntimeError" in err and PW not in err


# --- 설정·엔트리포인트 ------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [(None, False), ("1", True), ("true", True),
                                            ("0", False), ("", False)])
def test_agent_settings_nslcd_managed(value, expected):
    env = {"DMS_AGENT_API_URL": "http://api", "DMS_SHARED_TOKEN": "t"}
    if value is not None:
        env["DMS_AGENT_NSLCD_MANAGED"] = value
    assert AgentSettings.from_env(env).nslcd_managed is expected


def test_entrypoint_hands_nslcd_to_the_agent():
    text = ENTRYPOINT.read_text()
    start = text.index('env -i PATH="$PATH" nslcd -d > /var/log/nslcd.log 2>&1 &')
    rootdir = text.index("chmod 0700 /run/dms-agent")
    pid = text.index(f'echo "$!" > {directory_mod.PIDFILE}')
    managed = text.index("export DMS_AGENT_NSLCD_MANAGED=1")
    assert start < rootdir < pid < managed < text.index('exec "$@"')
    assert directory_mod.SOCKET_PATH in text and directory_mod.RENDER_CMD.endswith(
        "agent-entrypoint.sh")
