"""에이전트 측 nslcd 수렴(2026-09-29). 제어면이 보고 응답의 "directory" 로 내려주는
LDAP 디렉터리 설정(src/dms/agent_directory.py)을 nslcd.conf 로 렌더하고, 내용이 바뀌었거나
nslcd 가 죽어 있으면 nslcd 를 재기동한다.

왜: 이 설정을 DaemonSet env 로만 받으면 바꿀 때마다 매니페스트 apply 가 필요하다(포탈
릴리스는 이미지만 패치). 응답으로 받으면 이미지 릴리스만으로 수렴하고, 플래너(제어면
리졸버)와 **같은 추출 결과**(identity_ldap.ldap_directory_config)로 해석하게 된다 -- bind
계정이 에이전트 쪽 배선에서만 빠졌던 2026-09-29 프로덕션 사고의 유형 자체를 없앤다.
env(DaemonSet)는 첫 보고 전 부트스트랩일 뿐이다.

렌더러는 하나다: 엔트리포인트의 `--render-nslcd-conf` 모드(URI 정규화·StartTLS·0600·
개행 거부·printf 로 백슬래시 보존이 tests/test_agent_nslcd_conf.py 로 고정돼 있다)를 그대로
부른다 -- 파이썬으로 다시 쓰면 두 렌더러가 갈라진다. 비밀번호는 자식 프로세스 env 로만
건네고 인자·로그·보고 어디에도 싣지 않는다.

상태 기계(적대적 리뷰 2026-09-29 반영):
- 전체 블록: 렌더 → 내용이 같고 nslcd 가 **살아 있으면** 해시만 기록, 아니면 원자적 교체 +
  재기동.
- 해시만 온 블록(서버가 수렴했다고 봄): nslcd 생존을 **여전히 확인**하고 죽었으면 디스크의
  적용본으로 재기동한다 -- 수렴 뒤 nslcd 가 죽으면 영영 안 살아나던 결함.
- 어떤 실패든 보고 해시를 None 으로 비운다 -- 다음 응답이 반드시 전체 블록이 되어 재렌더·
  재기동을 재시도하고, 운영자가 설정을 되돌려도(되돌린 해시 == 옛 해시) 멈춰 서지 않는다.
- 상태가 바뀔 때마다(적용·재기동·실패) 비밀 없는 한 줄을 stderr 로 남긴다 -- kubectl logs
  의 기동 줄은 부트스트랩(env) 모드일 뿐이라 이게 없으면 수렴 여부가 안 보인다.

관리(managed)는 엔트리포인트가 nslcd 를 띄우고 DMS_AGENT_NSLCD_MANAGED=1 을 export 했을
때만이다 -- 컨테이너 밖에서 돈 에이전트가 호스트 /etc/nslcd.conf 를 건드리지 않게, 그리고
관리하지 않는 에이전트는 보고에 "directory" 를 싣지 않아(능력 미선언) 서버가 비밀번호를
보내지 않는다.
"""
import os
import re
import signal
import stat
import subprocess
import sys
import time

from ..db import utc_now_iso

RENDER_CMD = "/usr/local/bin/agent-entrypoint.sh"
CONF_PATH = "/etc/nslcd.conf"
# 엔트리포인트가 부트스트랩 nslcd 의 PID 를 여기 적는다. root 전용(0700) 디렉터리다 --
# nslcd 소유인 /run/nslcd 에 두면 장악된 nslcd 가 심볼릭 링크로 바꿔 root 에이전트가
# 임의 파일을 잘라 쓰게 만들 수 있다.
PIDFILE = "/run/dms-agent/nslcd.pid"
SOCKET_PATH = "/run/nslcd/socket"
LOG_PATH = "/var/log/nslcd.log"

_HASH_RE = re.compile(r"[0-9a-f]{32}")


class DirectoryError(Exception):
    """적용 실패. 메시지에 비밀번호를 담지 않는다(렌더러 stderr 도 담지 않는다고 테스트됨)."""


def _validate(block):
    if not isinstance(block, dict):
        raise DirectoryError("malformed directory block")
    h = block.get("hash")
    if not isinstance(h, str) or not _HASH_RE.fullmatch(h):
        raise DirectoryError("malformed directory hash")
    if "uris" not in block:
        return {"hash": h}
    uris = block.get("uris")
    if (not isinstance(uris, list) or not uris
            or not all(isinstance(u, str) and u for u in uris)):
        raise DirectoryError("malformed directory uris")
    for key in ("user_base", "group_base"):
        if not isinstance(block.get(key), str) or not block[key]:
            raise DirectoryError(f"malformed directory {key}")
    if not isinstance(block.get("start_tls"), bool):
        raise DirectoryError("malformed directory start_tls")
    for key in ("bind_dn", "bind_pw"):
        if block.get(key) is not None and not isinstance(block[key], str):
            raise DirectoryError(f"malformed directory {key}")
    return {"hash": h, "uris": list(uris), "user_base": block["user_base"],
            "group_base": block["group_base"], "start_tls": block["start_tls"],
            "bind_dn": block.get("bind_dn") or None, "bind_pw": block.get("bind_pw") or None}


def _read_bytes(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def _read_comm(pid):
    try:
        with open(f"/proc/{pid}/comm", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return None


def _read_state(pid):
    """/proc/<pid>/stat 의 상태 문자(R/S/D/Z/X…). 없으면 None. comm 에 공백·괄호가 있을 수
    있어 마지막 ')' 뒤를 읽는다."""
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as f:
            raw = f.read()
    except OSError:
        return None
    tail = raw.rpartition(")")[2].split()
    return tail[0] if tail else None


def _stderr(msg):
    print(msg, file=sys.stderr, flush=True)


class NslcdDirectory:
    def __init__(self, *, managed, conf_path=CONF_PATH, render_cmd=RENDER_CMD,
                 pidfile=PIDFILE, socket_path=SOCKET_PATH, log_path=LOG_PATH,
                 run=subprocess.run, spawn=subprocess.Popen, kill=os.kill,
                 waitpid=os.waitpid, read_comm=_read_comm, read_state=_read_state,
                 sleep=time.sleep, monotonic=time.monotonic, log=_stderr,
                 socket_timeout=10.0, stop_timeout=5.0):
        self.managed = bool(managed)
        self._conf, self._render_cmd = conf_path, render_cmd
        self._pidfile, self._socket, self._log_path = pidfile, socket_path, log_path
        self._run, self._spawn, self._kill, self._waitpid = run, spawn, kill, waitpid
        self._read_comm, self._read_state = read_comm, read_state
        self._sleep, self._monotonic, self._log = sleep, monotonic, log
        self._socket_timeout, self._stop_timeout = socket_timeout, stop_timeout
        self._proc = None          # 우리가 띄운 nslcd(Popen) -- 재기동 시 이걸로 거둔다
        self._status = {"source": "env" if self.managed else None, "hash": None,
                        "bind": None, "start_tls": None, "uri_count": None,
                        "error": None, "applied_at": None}

    # -- 보고 ---------------------------------------------------------------
    def report(self):
        """보고 본문의 "directory"(비밀 없음). 관리하지 않으면 None = 능력 미선언."""
        return dict(self._status) if self.managed else None

    # -- 적용 ---------------------------------------------------------------
    def apply(self, block):
        """응답 블록 적용. 예외를 밖으로 내지 않는다 -- 실패는 status.error 로 보고되고
        해시를 비우므로 서버가 다음 주기에 전체 블록을 다시 준다(재시도)."""
        if not self.managed or block is None:
            return
        try:
            payload = _validate(block)
            if "uris" not in payload:
                # 서버는 우리가 수렴했다고 본다. 그래도 nslcd 가 죽었으면(크래시·OOM)
                # 디스크의 적용본 그대로 되살린다 -- 비밀번호가 필요 없다.
                if not self._nslcd_alive():
                    self._log("dms-agent: nslcd not running under the applied directory "
                              "config -- restarting")
                    self._restart()
                    self._status["error"] = None
                    self._log("dms-agent: nslcd restarted")
                return
            tmp = self._conf + ".dms-new"
            self._render(payload, tmp)
            new, cur = _read_bytes(tmp), _read_bytes(self._conf)
            if new is not None and new == cur and self._nslcd_alive():
                os.unlink(tmp)      # 내용 동일 + nslcd 건강 → 재기동 없이 해시만 기록
                restarted = False
            else:
                os.replace(tmp, self._conf)   # 같은 디렉터리 → 원자적 교체
                self._restart()
                restarted = True
        except (DirectoryError, OSError) as exc:
            error = (str(exc) if isinstance(exc, DirectoryError)
                     else f"{type(exc).__name__}: {exc.strerror or ''}")[:300]
            # 해시를 비운다: 다음 응답이 반드시 전체 블록이 되게(재시도·되돌림 수렴).
            self._status.update(hash=None, error=error)
            self._log(f"dms-agent: directory apply failed: {error}")
            return
        bind = (f"dn={payload['bind_dn']}" if payload["bind_dn"] and payload["bind_pw"]
                else "anonymous")
        self._status.update(source="api", hash=payload["hash"], error=None,
                            applied_at=utc_now_iso(), bind=bind,
                            start_tls=payload["start_tls"], uri_count=len(payload["uris"]))
        self._log(f"dms-agent: directory applied source=api uri={len(payload['uris'])} "
                  f"start_tls={'on' if payload['start_tls'] else 'off'} bind={bind} "
                  f"restarted={'yes' if restarted else 'no'}")

    def _render(self, payload, out_path):
        env = {"PATH": os.environ.get("PATH", "/usr/sbin:/usr/bin:/sbin:/bin"),
               "DMS_LDAP_URI": ",".join(payload["uris"]),
               "DMS_LDAP_USER_BASE": payload["user_base"],
               "DMS_LDAP_GROUP_BASE": payload["group_base"],
               "DMS_LDAP_USE_START_TLS": "true" if payload["start_tls"] else "false"}
        if payload["bind_dn"]:
            env["DMS_LDAP_BIND_DN"] = payload["bind_dn"]
        if payload["bind_pw"]:
            env["DMS_LDAP_BIND_PW"] = payload["bind_pw"]
        try:
            proc = self._run([self._render_cmd, "--render-nslcd-conf", out_path],
                             env=env, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as exc:
            raise DirectoryError(f"render failed: {type(exc).__name__}") from None
        if proc.returncode != 0:
            try:
                os.unlink(out_path)
            except OSError:
                pass
            # 렌더러는 값(특히 비밀번호)을 stderr 에 찍지 않는다(테스트로 고정) -- 그래도
            # 마지막 줄만, 길이 제한을 두고 싣는다.
            last = (proc.stderr or "").strip().splitlines()[-1:] or [""]
            raise DirectoryError(f"render refused (exit {proc.returncode}): {last[0][:200]}")

    # -- pidfile(root 전용 디렉터리, 심볼릭 링크 거부) -------------------------
    def _read_pid(self):
        try:
            fd = os.open(self._pidfile, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError:
            return None
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return None
            raw = os.read(fd, 64)
        finally:
            os.close(fd)
        try:
            pid = int(raw.decode().strip())
        except ValueError:
            return None
        return pid if pid > 1 else None

    def _write_pid(self, pid):
        tmp = self._pidfile + ".tmp"
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            os.write(fd, f"{pid}\n".encode())
        finally:
            os.close(fd)
        os.replace(tmp, self._pidfile)

    # -- nslcd 수명주기 -----------------------------------------------------
    def _is_nslcd(self, pid):
        # pidfile 의 PID 가 재사용됐을 수 있다 -- 이름이 nslcd 일 때만 건드린다.
        return self._read_comm(pid) == "nslcd"

    def _pid_running(self, pid):
        """nslcd 가 실제로 돌고 있나. 좀비는 죽은 것이다: 엔트리포인트가 띄운 nslcd 는 exec
        뒤 에이전트(PID 1)의 자식이라, 죽으면 거두기 전까지 /proc 에 comm=nslcd 인 좀비로
        남는다 -- comm 만 보면 산 것으로 오판한다(리뷰 2026-09-29)."""
        if not self._is_nslcd(pid):
            return False
        try:
            done, _ = self._waitpid(pid, os.WNOHANG)
            return done != pid          # pid 가 돌아오면 방금 거둔 것 = 죽었다
        except ChildProcessError:
            return self._read_state(pid) not in (None, "Z", "X")

    def _nslcd_alive(self):
        if self._proc is not None:
            running = self._proc.poll() is None     # poll 이 거둔다
        else:
            pid = self._read_pid()
            running = bool(pid) and self._pid_running(pid)
        return running and os.path.exists(self._socket)

    def _stop(self, pid):
        if self._proc is not None and self._proc.pid == pid:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=self._stop_timeout)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
            self._proc = None
            return
        if not self._is_nslcd(pid):
            return
        # 엔트리포인트가 띄운 nslcd 는 exec 뒤 에이전트(PID 1)의 자식이다 -- 거둬야 좀비가
        # 남지 않는다. 자식이 아니면(ChildProcessError) 존재만 폴링한다.
        try:
            self._kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = self._monotonic() + self._stop_timeout
        while self._monotonic() < deadline:
            try:
                done, _ = self._waitpid(pid, os.WNOHANG)
                if done == pid:
                    return
            except ChildProcessError:
                if self._read_state(pid) in (None, "Z", "X"):
                    return
            self._sleep(0.1)
        try:
            self._kill(pid, signal.SIGKILL)
            self._waitpid(pid, 0)
        except (ProcessLookupError, ChildProcessError):
            pass

    def _restart(self):
        pid = self._read_pid()
        if pid is not None:
            self._stop(pid)
        elif self._proc is not None:
            self._stop(self._proc.pid)
        # SIGKILL 로 끝난 nslcd 는 소켓 파일을 남긴다 -- 남아 있으면 기동 확인이 거짓
        # 양성이 된다(nslcd 는 기동 시 스스로 다시 만든다).
        if os.path.lexists(self._socket):
            os.unlink(self._socket)
        # 최소 env: nslcd 는 환경이 필요 없고, 에이전트 env(관리자급 공유 토큰·bind
        # 비밀번호)를 물려주면 네트워크 입력을 파싱하는 데몬의 environ 에 비밀이 남는다.
        env = {"PATH": os.environ.get("PATH", "/usr/sbin:/usr/bin:/sbin:/bin")}
        try:
            with open(self._log_path, "ab") as log:
                self._proc = self._spawn(["nslcd", "-d"], stdout=log,
                                         stderr=subprocess.STDOUT, env=env)
        except OSError as exc:
            raise DirectoryError(f"nslcd spawn failed: {type(exc).__name__}") from None
        self._write_pid(self._proc.pid)
        deadline = self._monotonic() + self._socket_timeout
        while self._monotonic() < deadline:
            if self._proc.poll() is not None:
                raise DirectoryError(
                    f"nslcd exited rc={self._proc.returncode} (see {self._log_path})")
            if os.path.exists(self._socket):
                return
            self._sleep(0.2)
        raise DirectoryError(f"nslcd socket not up after {self._socket_timeout:.0f}s")
