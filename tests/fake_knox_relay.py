"""가짜 Knox 메일 릴레이(knox_mail_dms_certi 흉내, 2026-10-01) -- 테스트·테스트베드 공용.

실제 릴레이(메신저 서버 /usr/lib/zabbix/alertscripts/knox_mail_dms_certi.py)의 소스는 이 저장소에 없다. 이
가짜는 src/dms/api/mailer.py 가 기대하는 계약(요청·응답 모양, error 코드)을 그대로 흉내 낸다 -- 실제 릴레이가
이 계약과 다르게 답하면 그건 mailer.py 쪽 가정이 틀린 것이고, 사내 실검증에서 드러난다.

계약(mailer.MailerError 의 사유 표와 같은 이름):
  GET  /healthz                                   200 {"ok": true}
  POST /send   Authorization: Bearer <token>
       본문 {"to": [주소], "subject": str, "body": str, "content_type": "HTML"|"TEXT"}
       200 {"ok": true}
       401 {"error": "unauthorized"}
       403 {"error": "client_not_allowed"}             allowed_clients 를 주면(RELAY_ALLOWED_CLIENTS)
       403 {"error": "recipient_not_allowed"}          allowed_domains 를 주면(RELAY_ALLOWED_DOMAINS)
       429 {"error": "rate_limited", "retry_after": N} + Retry-After   수신자별 rate_limit/window(RELAY_RATE_LIMIT)
       400 {"error": "bad_request", "detail": ...}
       502 {"error": "knox_rejected", "upstream_status": 401, "upstream_body": ...}   knox_mode="reject"
       504 {"error": "knox_unreachable", "detail": ...}                              knox_mode="unreachable"
       200 (JSON 아님)                                                               knox_mode="garbage"
       500 (JSON 아님)                                                               knox_mode="http500"
       429 (본문 없음, Retry-After 헤더만)                                            knox_mode="retry_header_only"
       302 Location: redirect_to                                                     knox_mode="redirect"
  delay=N 이면 /send 가 본문을 읽은 뒤 N 초 기다렸다 답한다(그 사이 클라이언트가 타임아웃하면 "요청은 갔는데 응답
  없음" -- mailer 의 relay_no_response. 기다린 뒤 메일은 그대로 기록된다: 실제로도 갔을 수 있는 경우).
  requests 에는 모든 요청(GET 포함)을 method·path·Authorization 과 함께 남긴다 -- 리다이렉트 대상이 됐을 때
  무엇이든 도착했는지 볼 수 있게.
  (/healthz 는 토큰·허용 IP 를 보지 않는다고 가정한다 -- 운영 확인 절차가 인증 없이 curl 한다. 실제 릴레이가 /healthz
   에도 허용 IP 를 적용하면 "연결 확인"이 403 relay_http_403 이 된다 -- 그때는 RELAY_ALLOWED_CLIENTS 를 본다.)
보낸 메일은 메모리(sent)에, outbox 를 주면 <순번>.json(메타)·<순번>.html(본문) 파일로도 남긴다.

테스트: FakeRelay(token=...).start() -> base_url, .sent, .stop()
테스트베드(CLI):
  python3 tests/fake_knox_relay.py --host 0.0.0.0 --port 8025 --token <T> --outbox <DIR> \\
      [--allowed-domains samsung.com] [--allowed-clients 10.10.10.11,...] [--rate-limit 10 --window 600] [--knox-mode ok]
"""
from __future__ import annotations

import argparse
import json
import threading
import time
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class _Handler(BaseHTTPRequestHandler):
    server_version = "fake-knox-relay/1"

    def log_message(self, fmt, *args):   # 조용히 -- 테스트 출력 오염 금지(CLI 는 relay.log 로 남긴다)
        relay = self.server.relay
        if relay.verbose:
            print(f"[fake-relay] {self.client_address[0]} {fmt % args}", flush=True)

    def _reply(self, status, obj=None, *, raw=None, headers=None):
        payload = raw if raw is not None else json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8" if raw is None else "text/plain")
        self.send_header("Content-Length", str(len(payload)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(payload)

    def _note(self):
        self.server.relay.requests.append({
            "method": self.command, "path": self.path, "client": self.client_address[0],
            "authorization": self.headers.get("Authorization") or "",
            "content_type": self.headers.get("Content-Type")})

    def do_GET(self):
        self._note()
        if self.path == "/healthz":
            return self._reply(200, {"ok": True})
        return self._reply(404, {"error": "not_found"})

    def do_POST(self):
        relay = self.server.relay
        self._note()
        if self.path != "/send":
            return self._reply(404, {"error": "not_found"})
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        auth = self.headers.get("Authorization") or ""
        if relay.delay:
            time.sleep(relay.delay)
        if auth != f"Bearer {relay.token}":
            return self._reply(401, {"error": "unauthorized"})
        if relay.allowed_clients and self.client_address[0] not in relay.allowed_clients:
            return self._reply(403, {"error": "client_not_allowed", "detail": self.client_address[0]})
        problem = None
        try:
            msg = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            msg, problem = {}, "body is not JSON"
        to = msg.get("to") if isinstance(msg, dict) else None
        if problem is None and not (isinstance(to, list) and to
                                    and all(isinstance(a, str) and "@" in a for a in to)):
            problem = "to must be a non-empty list of addresses"
        elif problem is None and not (isinstance(msg.get("subject"), str) and msg["subject"].strip()
                                      and isinstance(msg.get("body"), str) and msg["body"].strip()):
            problem = "subject/body must be non-empty strings"
        elif problem is None and msg.get("content_type", "HTML") not in ("HTML", "TEXT"):
            problem = "content_type must be HTML or TEXT"
        if problem:
            return self._reply(400, {"error": "bad_request", "detail": problem})
        if relay.allowed_domains and any(a.rsplit("@", 1)[1].lower() not in relay.allowed_domains for a in to):
            return self._reply(403, {"error": "recipient_not_allowed"})
        retry = relay.throttle([a.lower() for a in to])
        if retry:
            return self._reply(429, {"error": "rate_limited", "retry_after": retry},
                               headers={"Retry-After": str(retry)})
        if relay.knox_mode == "reject":
            return self._reply(502, {"error": "knox_rejected", "upstream_status": 401,
                                     "upstream_body": '{"code":"TOKEN_EXPIRED"}'})
        if relay.knox_mode == "unreachable":
            return self._reply(504, {"error": "knox_unreachable", "detail": "connect timeout"})
        if relay.knox_mode == "garbage":
            return self._reply(200, raw=b"<html>not a relay</html>")
        if relay.knox_mode == "http500":
            return self._reply(500, raw=b"Internal Server Error")
        if relay.knox_mode == "retry_header_only":
            return self._reply(429, raw=b"", headers={"Retry-After": "77"})
        if relay.knox_mode == "redirect":
            return self._reply(302, raw=b"", headers={"Location": relay.redirect_to})
        relay.record(msg)
        return self._reply(200, {"ok": True})


class FakeRelay:
    def __init__(self, *, token="fake-relay-token", host="127.0.0.1", port=0, outbox=None,
                 allowed_clients=(), allowed_domains=(), rate_limit=10, window=600.0,
                 knox_mode="ok", redirect_to="", delay=0.0, verbose=False):
        self.token = token
        self.host, self.port = host, port
        self.outbox = Path(outbox) if outbox else None
        self.allowed_clients = set(allowed_clients)
        self.allowed_domains = {d.lower() for d in allowed_domains}
        self.rate_limit, self.window = rate_limit, window
        self.knox_mode = knox_mode
        self.redirect_to = redirect_to
        self.delay = delay
        self.verbose = verbose
        self.sent: list[dict] = []
        self.requests: list[dict] = []
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()
        self._server = None
        self._thread = None

    # -- 상태 ------------------------------------------------------------------------------
    def throttle(self, recipients) -> int:
        if self.rate_limit <= 0:
            return 0
        now = time.monotonic()
        with self._lock:
            for r in recipients:
                q = self._hits[r]
                while q and now - q[0] >= self.window:
                    q.popleft()
                if len(q) >= self.rate_limit:
                    return int(self.window - (now - q[0])) + 1
            for r in recipients:
                self._hits[r].append(now)
        return 0

    def record(self, msg: dict) -> None:
        with self._lock:
            seq = len(self.sent) + 1
            entry = {"seq": seq, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), **msg}
            self.sent.append(entry)
        if self.outbox:
            self.outbox.mkdir(parents=True, exist_ok=True)
            meta = {k: v for k, v in entry.items() if k != "body"}
            (self.outbox / f"{seq:04d}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
            (self.outbox / f"{seq:04d}.html").write_text(msg["body"])

    # -- 수명 ------------------------------------------------------------------------------
    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def start(self) -> str:
        self._server = ThreadingHTTPServer((self.host, self.port), _Handler)
        self._server.relay = self
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self.base_url

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()


def main(argv=None):
    ap = argparse.ArgumentParser(description="fake knox_mail_dms_certi relay (DMS 테스트베드 검증용)")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8025)
    ap.add_argument("--token", required=True)
    ap.add_argument("--outbox", required=True)
    ap.add_argument("--allowed-clients", default="")
    ap.add_argument("--allowed-domains", default="")
    ap.add_argument("--rate-limit", type=int, default=10)
    ap.add_argument("--window", type=float, default=600.0)
    ap.add_argument("--knox-mode", default="ok",
                    choices=("ok", "reject", "unreachable", "garbage", "http500", "retry_header_only"))
    ap.add_argument("--delay", type=float, default=0.0)
    a = ap.parse_args(argv)
    split = lambda s: [x.strip() for x in s.split(",") if x.strip()]  # noqa: E731
    relay = FakeRelay(token=a.token, host=a.host, port=a.port, outbox=a.outbox,
                      allowed_clients=split(a.allowed_clients), allowed_domains=split(a.allowed_domains),
                      rate_limit=a.rate_limit, window=a.window, knox_mode=a.knox_mode, delay=a.delay,
                      verbose=True)
    relay.start()
    print(f"fake knox relay listening on {relay.base_url} (outbox {a.outbox})", flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        relay.stop()


if __name__ == "__main__":
    main()
