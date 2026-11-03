#!/usr/bin/env python3
"""DB 시크릿 도우미 — dms-pg-app 과 dms-secrets 의 DMS_DATABASE_URL(2026-11-03 ⑤, 설계 v6 6-3·8-1 S2). 값을 출력하지 않는다.

  python3 deploy/pg-secrets.py --context <ctx> ensure-app      # dms-pg-app 이 없으면 만든다(무작위 48 hex — URL 인코딩이 필요 없다)
  python3 deploy/pg-secrets.py --context <ctx> init-secrets    # 새 사이트: dms-secrets(4키)가 없으면 만든다 — URL 은 아래 모양, 토큰 셋은 무작위
  python3 deploy/pg-secrets.py --context <ctx> set-url         # 있는 dms-secrets 의 DMS_DATABASE_URL 만 CNPG URL 로(옛 값은 dms-secrets-prev 에)
  python3 deploy/pg-secrets.py --context <ctx> restore-url     # 되돌림: dms-secrets-prev 의 옛 URL 을 다시 넣는다(T7 롤백 = URL 원복)
  python3 deploy/pg-secrets.py --context <ctx> check           # 설계 6-3 의 install 게이트 — host·포트·DB·사용자·비밀번호·필수 파라미터가 맞는지
      [--namespace dms] [--cluster dms-pg]

URL(설계 6-3 — CNPG 의 <cluster>-app 시크릿 `uri` 에는 파라미터를 붙일 수 없어 직접 조립한다):
  postgresql://dms:<PW>@<cluster>-rw.<ns>.svc:5432/dms?sslmode=require&target_session_attrs=read-write
      &keepalives=1&keepalives_idle=10&keepalives_interval=5&keepalives_count=3&tcp_user_timeout=15000
  keepalive 와 tcp_user_timeout 이 없으면 primary 가 있던 노드가 죽었을 때 API 가 최대 약 15분 매달린다(추정 — T6' 에서 실증). connect_timeout
  은 넣지 않는다(코드 기본 5초 — db.py 가 URL 에 없을 때만 붙인다). <PW> 는 dms-pg-app 의 password 와 같은 값이다(Cluster bootstrap 이 그
  비밀번호로 dms 역할을 만든다) — 한쪽만 바꾸면 인증이 깨진다.

비밀 취급: 값은 화면·로그·argv 에 싣지 않는다 — kubectl 에는 stdin(--from-env-file=/dev/stdin, patch --patch-file=/dev/stdin)으로만 넘기고, 출력은
「만들었다/이미 있다/같다/다르다」뿐이다. 이미 있는 dms-pg-app 은 덮어쓰지 않는다(Cluster 가 이미 그 비밀번호로 만들어졌을 수 있다).
apply 뒤 api·controller 는 시크릿을 기동 때만 읽는다 — `kubectl --context <ctx> -n dms rollout restart deploy/dms-api deploy/dms-controller`.

kubectl 은 모든 호출에 --context 를 붙인다(필수). 설치 호스트의 python3(RHEL 9 = 3.9), 표준 라이브러리만. 종료: 0 성공·일치 · 1 불일치·실패 · 2 사용법.
"""
from __future__ import annotations

import argparse
import base64
import json
import secrets
import subprocess
import sys
import urllib.parse

APP_SECRET = "dms-pg-app"
DMS_SECRET = "dms-secrets"
PREV_SECRET = "dms-secrets-prev"
DB_NAME = "dms"
DB_USER = "dms"
PARAMS = (("sslmode", "require"), ("target_session_attrs", "read-write"), ("keepalives", "1"), ("keepalives_idle", "10"),
          ("keepalives_interval", "5"), ("keepalives_count", "3"), ("tcp_user_timeout", "15000"))
TOKEN_KEYS = ("DMS_SHARED_TOKEN", "DMS_ADMIN_TOKEN", "DMS_SESSION_SECRET")


def build_url(password: str, cluster: str, namespace: str) -> str:
    pw = urllib.parse.quote(password, safe="")
    query = "&".join(f"{k}={v}" for k, v in PARAMS)
    return f"postgresql://{DB_USER}:{pw}@{cluster}-rw.{namespace}.svc:5432/{DB_NAME}?{query}"


def url_problems(url: str, password: str, cluster: str, namespace: str) -> list:
    """URL 이 이 Cluster·비밀번호·필수 파라미터와 맞는지 — 어긋난 항목 이름만(값은 내지 않는다)."""
    out = []
    try:
        u = urllib.parse.urlsplit(url)
        port = u.port
    except ValueError:
        return ["URL 형식"]
    if u.scheme not in ("postgresql", "postgres"):
        out.append("scheme")
    if u.hostname not in (f"{cluster}-rw", f"{cluster}-rw.{namespace}", f"{cluster}-rw.{namespace}.svc",
                          f"{cluster}-rw.{namespace}.svc.cluster.local"):
        out.append(f"host(기대 {cluster}-rw.{namespace}.svc)")
    if port not in (None, 5432):
        out.append("port")
    if u.path.lstrip("/") != DB_NAME:
        out.append(f"DB(기대 {DB_NAME})")
    if urllib.parse.unquote(u.username or "") != DB_USER:
        out.append(f"사용자(기대 {DB_USER})")
    if urllib.parse.unquote(u.password or "") != password:
        out.append(f"비밀번호(≠ {APP_SECRET})")
    q = dict(urllib.parse.parse_qsl(u.query))
    for key, value in PARAMS:
        if q.get(key) != value:
            out.append(f"{key}={value}")
    return out


class Kube:
    def __init__(self, context: str, namespace: str):
        self.context, self.namespace = context, namespace

    def run(self, *args, stdin=None, check=True):
        r = subprocess.run(["kubectl", "--context", self.context, "-n", self.namespace, *args], input=stdin,
                           capture_output=True, text=True, timeout=60)
        if check and r.returncode != 0:
            # stderr 에 값이 실릴 일은 없다(값은 stdin 으로만 보낸다) — 그래도 첫 줄만.
            raise RuntimeError(f"kubectl {args[0]} {args[1] if len(args) > 1 else ''}: {(r.stderr or r.stdout).strip().splitlines()[:1]}")
        return r

    def context_exists(self) -> bool:
        return subprocess.run(["kubectl", "config", "get-contexts", self.context], capture_output=True, text=True,
                              timeout=30).returncode == 0

    def secret(self, name: str):
        """{키: 값} 또는 None(없음)."""
        r = self.run("get", "secret", name, "-o", "json", check=False)
        if r.returncode != 0:
            if "NotFound" in r.stderr or "not found" in r.stderr:
                return None
            raise RuntimeError(f"secret {name} 을 읽지 못했다: {r.stderr.strip().splitlines()[:1]}")
        data = json.loads(r.stdout).get("data") or {}
        return {k: base64.b64decode(v).decode() for k, v in data.items()}

    def create_secret(self, name: str, values: dict, secret_type=None):
        body = "".join(f"{k}={v}\n" for k, v in values.items())
        args = ["create", "secret", "generic", name, "--from-env-file=/dev/stdin"]
        if secret_type:
            args.append(f"--type={secret_type}")
        self.run(*args, stdin=body)

    def patch_secret(self, name: str, values: dict):
        patch = json.dumps({"data": {k: base64.b64encode(v.encode()).decode() for k, v in values.items()}})
        self.run("patch", "secret", name, "--type=merge", "--patch-file=/dev/stdin", stdin=patch)


def _app_password(kube) -> str:
    app = kube.secret(APP_SECRET)
    if app is None:
        raise LookupError(f"{APP_SECRET} 가 없다 — 먼저 ensure-app")
    if app.get("username") != DB_USER or not app.get("password"):
        raise LookupError(f"{APP_SECRET} 의 username 이 {DB_USER} 가 아니거나 password 가 비었다(kubernetes.io/basic-auth)")
    return app["password"]


def cmd_ensure_app(kube, args) -> int:
    if kube.secret(APP_SECRET) is not None:
        print(f"{APP_SECRET}: 이미 있다 — 그대로 둔다(덮어쓰지 않는다)")
        return 0
    kube.create_secret(APP_SECRET, {"username": DB_USER, "password": secrets.token_hex(24)}, "kubernetes.io/basic-auth")
    print(f"{APP_SECRET}: 만들었다(basic-auth, username {DB_USER}, 비밀번호 48 hex — 출력하지 않음)")
    return 0


def cmd_init_secrets(kube, args) -> int:
    if kube.secret(DMS_SECRET) is not None:
        print(f"{DMS_SECRET}: 이미 있다 — 바꾸지 않는다(URL 만 바꾸려면 set-url)")
        return 0
    values = {"DMS_DATABASE_URL": build_url(_app_password(kube), args.cluster, args.namespace)}
    values.update({k: secrets.token_hex(32) for k in TOKEN_KEYS})
    kube.create_secret(DMS_SECRET, values)
    print(f"{DMS_SECRET}: 만들었다(4키 — DMS_DATABASE_URL = {args.cluster}-rw URL, 토큰 셋 무작위; 출력하지 않음)")
    return 0


def cmd_set_url(kube, args) -> int:
    current = kube.secret(DMS_SECRET)
    if current is None:
        print(f"FAIL: {DMS_SECRET} 가 없다 — 새 사이트면 init-secrets", file=sys.stderr)
        return 1
    url = build_url(_app_password(kube), args.cluster, args.namespace)
    old = current.get("DMS_DATABASE_URL", "")
    if old == url:
        print("DMS_DATABASE_URL: 이미 그 값이다 — 바꾸지 않았다")
        return 0
    prev = kube.secret(PREV_SECRET)
    if prev is None:
        kube.create_secret(PREV_SECRET, {"DMS_DATABASE_URL": old})
    else:
        kube.patch_secret(PREV_SECRET, {"DMS_DATABASE_URL": old})
    kube.patch_secret(DMS_SECRET, {"DMS_DATABASE_URL": url})
    print(f"DMS_DATABASE_URL: {args.cluster}-rw URL 로 바꿨다(옛 값은 {PREV_SECRET} 에 — 되돌림은 restore-url). "
          f"api·controller 를 rollout restart 해야 반영된다")
    return 0


def cmd_restore_url(kube, args) -> int:
    prev = kube.secret(PREV_SECRET)
    if not prev or not prev.get("DMS_DATABASE_URL"):
        print(f"FAIL: {PREV_SECRET} 에 옛 URL 이 없다", file=sys.stderr)
        return 1
    kube.patch_secret(DMS_SECRET, {"DMS_DATABASE_URL": prev["DMS_DATABASE_URL"]})
    print(f"DMS_DATABASE_URL: {PREV_SECRET} 의 옛 값으로 되돌렸다 — api·controller 를 rollout restart")
    return 0


def cmd_check(kube, args) -> int:
    cur = kube.secret(DMS_SECRET)
    if cur is None or not cur.get("DMS_DATABASE_URL"):
        print(f"FAIL: {DMS_SECRET} 의 DMS_DATABASE_URL 이 없다", file=sys.stderr)
        return 1
    problems = url_problems(cur["DMS_DATABASE_URL"], _app_password(kube), args.cluster, args.namespace)
    if problems:
        print(f"FAIL: DMS_DATABASE_URL 이 {args.cluster}·{APP_SECRET} 와 어긋난다: {', '.join(problems)}")
        return 1
    print(f"PASS: DMS_DATABASE_URL = {args.cluster}-rw·DB {DB_NAME}·사용자 {DB_USER}·{APP_SECRET} 비밀번호·필수 파라미터(6-3)")
    return 0


COMMANDS = {"ensure-app": cmd_ensure_app, "init-secrets": cmd_init_secrets, "set-url": cmd_set_url,
            "restore-url": cmd_restore_url, "check": cmd_check}


def main(argv=None, *, kube_factory=Kube) -> int:
    ap = argparse.ArgumentParser(description="DB 시크릿 도우미(값을 출력하지 않는다)")
    ap.add_argument("--context", required=True)
    ap.add_argument("--namespace", default="dms")
    ap.add_argument("--cluster", default="dms-pg")
    ap.add_argument("command", choices=sorted(COMMANDS))
    try:
        args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    kube = kube_factory(args.context, args.namespace)
    if not kube.context_exists():
        print(f"FAIL: kubeconfig 에 컨텍스트 '{args.context}' 가 없다", file=sys.stderr)
        return 2
    try:
        return COMMANDS[args.command](kube, args)
    except (LookupError, RuntimeError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
