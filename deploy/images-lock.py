#!/usr/bin/env python3
"""deploy/images.lock 검사·생성(2026-11-03 ④, 설계 v6 7-5 ①·8-1 S-1 「lock 에 index digest 가 있으면 실패」).

  python3 deploy/images-lock.py check   [--lock <파일>]                 # 형식(오프라인): 칸 수·DEST·SOURCE·digest 모양·DEST 중복,
                                                                         # sh 와 같은 줄·칸 규칙(CR·비 ASCII·들여쓴 주석 거부)
  python3 deploy/images-lock.py verify  [--lock <파일>] [--timeout 30]  # 상류에서: digest 가 단일 매니페스트(index 아님)이고 linux/amd64
  python3 deploy/images-lock.py resolve <DEST> <SOURCE> [--timeout 30]  # SOURCE 태그를 읽어 lock 한 줄을 출력(index 면 linux/amd64 항목)

lock 형식은 deploy/images.lock 머리 주석이 정의한다(공백으로 가른 세 칸 DEST SOURCE DIGEST — sh 의 `read -r dest src dg` 로도 읽힌다).

왜 오프라인 검사와 온라인 검사를 나누나: digest 문자열만으로는 index 인지 매니페스트인지 알 수 없다 — 그것은 상류 레지스트리만 안다.
그래서 check 는 어디서나(테스트·설치 호스트) 돌고, verify·resolve 는 인터넷 노드(S-1 릴리스 담당·S5 반입)에서 돈다. 인터넷이 없거나
상류가 답하지 않으면 「모름」(exit 4)이지 실패가 아니다(DMS 규약: 모름 ≠ 실패).

상류 접근은 익명이다(docker.io·quay.io 의 Bearer 토큰 흐름 — WWW-Authenticate 를 따라 한 번 받는다). 프록시는 표준 env
(HTTPS_PROXY·NO_PROXY)를 urllib 가 그대로 쓴다. 설치 호스트의 python3(RHEL 9 = 3.9)로 돌고 표준 라이브러리만 쓴다.

종료: 0 PASS · 1 FAIL(형식 오류·index digest·amd64 아님·digest 불일치) · 2 사용법 · 4 모름(네트워크·상류 무응답). 마지막 줄 `RESULT: …`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

LOCK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "images.lock")

# 단일 이미지 매니페스트(허용)와 index(거부). docker v2 schema1 은 digest 의미가 달라 받지 않는다.
MANIFEST_TYPES = ("application/vnd.oci.image.manifest.v1+json", "application/vnd.docker.distribution.manifest.v2+json")
INDEX_TYPES = ("application/vnd.oci.image.index.v1+json", "application/vnd.docker.distribution.manifest.list.v2+json")
ACCEPT = ", ".join(INDEX_TYPES + MANIFEST_TYPES)

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
# 저장소 경로 조각(distribution reference 문법의 부분집합) · 태그.
_COMPONENT = r"[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*"
_REPO = re.compile(rf"^{_COMPONENT}(?:/{_COMPONENT})*$")
_TAG = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")
_HOST = re.compile(r"^(?:localhost|[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+|[A-Za-z0-9.-]+:[0-9]+)$")
EXIT = {"PASS": 0, "FAIL": 1, "UNKNOWN": 4}


class Unknown(Exception):
    """상류에 닿지 못함 — 판정할 수 없다(FAIL 과 섞지 않는다)."""


def _split_tagged(ref: str):
    """'repo:tag' -> (repo, tag) 또는 None. 마지막 ':' 뒤에 '/' 가 있으면 태그가 아니다(host:port/repo)."""
    if ":" not in ref:
        return None
    repo, tag = ref.rsplit(":", 1)
    if "/" in tag:
        return None
    return repo, tag


def dest_problem(dest: str):
    if "@" in dest:
        return "DEST 에 digest(@) 가 있다 — 태그만 쓴다(GC 보호)"
    parts = _split_tagged(dest)
    if parts is None:
        return "DEST 에 태그가 없다(<이름>:<태그>)"
    repo, tag = parts
    first = repo.split("/", 1)[0]
    if "." in first or ":" in first or first == "localhost":
        return "DEST 에 레지스트리 호스트가 있다 — 호스트 없이 <이름>:<태그>(레지스트리는 values.env 의 REGISTRY)"
    if not _REPO.match(repo):
        return f"DEST 이름 '{repo}' 이 저장소 이름 규칙(소문자·숫자·. _ - /) 밖이다"
    if not _TAG.match(tag):
        return f"DEST 태그 '{tag}' 가 태그 규칙 밖이다"
    return None


def source_problem(src: str):
    if "@" in src:
        return "SOURCE 에 digest(@) 가 있다 — 태그를 쓰고 digest 는 셋째 칸에"
    parts = _split_tagged(src)
    if parts is None:
        return "SOURCE 에 태그가 없다(<호스트>/<경로>:<태그>)"
    repo, tag = parts
    if "/" not in repo:
        return "SOURCE 에 레지스트리 호스트가 없다(예: docker.io/library/registry:3.1.2)"
    host, path = repo.split("/", 1)
    if not _HOST.match(host):
        return f"SOURCE 호스트 '{host}' 가 레지스트리 호스트 모양이 아니다"
    if not _REPO.match(path):
        return f"SOURCE 경로 '{path}' 가 저장소 이름 규칙 밖이다"
    if not _TAG.match(tag):
        return f"SOURCE 태그 '{tag}' 가 태그 규칙 밖이다"
    return None


_SH_FIELD_SEP = re.compile(r"[ \t]+")
_PRINTABLE_ASCII = re.compile(r"^[\x20-\x7e\t]*$")


def parse_lock(text: str):
    """(entries, problems). entries = [(줄 번호, dest, source, digest)], problems = [(줄 번호, 사유)].

    실제 소비자는 sh 다(`grep -v '^#' | while read -r dest src dg`, `awk '$1==…'` — deploy/README §2e). 그래서 줄·칸을 sh 와 **같은
    규칙**으로 나눈다(2026-11-03 ④ 리뷰): 줄은 \\n 으로만(splitlines 는 \\r·\\v·\\x1c… 에서도 자른다), 칸은 공백·탭(IFS)으로만(str.split 은
    U+3000·U+00A0 에서도 자른다). 그 밖의 것 — CR(CRLF 저장), 비 ASCII·제어 문자, 들여쓴 주석(grep -v '^#' 가 거르지 못해 sh 가 항목으로
    읽는다) — 은 파이썬이 너그럽게 읽어도 sh 는 다른 digest·ref 를 읽으므로 문제로 보고한다.
    """
    entries, problems, seen = [], [], {}
    for no, raw in enumerate(text.split("\n"), 1):
        if raw.startswith("#"):
            continue                                  # grep -v '^#' 와 같다 — 주석(한글 포함)은 첫 칸의 # 만
        if "\r" in raw:
            problems.append((no, "줄 끝에 CR(\\r)이 있다 — CRLF 로 저장됐다(sh 의 read 는 digest 끝에 \\r 를 붙여 읽는다). LF 로 저장한다"))
            continue
        line = raw.strip(" \t")
        if not line:
            continue
        if line.startswith("#"):
            problems.append((no, "들여쓴 주석이다 — 주석은 첫 칸에서 # 로 시작한다(grep -v '^#' 가 거르지 못해 sh 가 항목으로 읽는다)"))
            continue
        if not _PRINTABLE_ASCII.match(raw):
            problems.append((no, "ASCII 밖 문자·제어 문자가 있다(전각 공백 U+3000·NBSP U+00A0 등) — sh 는 그것을 칸 구분으로 보지 않는다"))
            continue
        fields = _SH_FIELD_SEP.split(line)
        if len(fields) != 3:
            problems.append((no, f"칸이 {len(fields)}개다 — DEST SOURCE DIGEST 세 칸(공백 구분)"))
            continue
        dest, src, dg = fields
        why = dest_problem(dest) or source_problem(src)
        if why is None and not _DIGEST.match(dg):
            why = "DIGEST 가 sha256:<64 소문자 hex> 가 아니다"
        if why is None and dest in seen:
            why = f"DEST '{dest}' 가 {seen[dest]}번째 줄과 겹친다"
        if why is not None:
            problems.append((no, why))
            continue
        seen[dest] = no
        entries.append((no, dest, src, dg))
    return entries, problems


# --- 상류 레지스트리(익명) ---------------------------------------------------------------------------------------------

def http_get(url: str, headers: dict, timeout: float):
    """(status, 소문자 헤더 dict, body). 테스트가 이 함수를 바꿔치기한다. 연결 실패는 Unknown."""
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, e.read() or b""
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise Unknown(f"{url}: {e}") from None


def split_source(src: str):
    """SOURCE -> (API 기준 URL, 저장소, 태그). docker.io 는 registry-1.docker.io 와 library/ 접두."""
    repo, tag = _split_tagged(src)
    host, path = repo.split("/", 1)
    if host in ("docker.io", "index.docker.io", "registry-1.docker.io"):
        host = "registry-1.docker.io"
        if "/" not in path:
            path = "library/" + path
    return f"https://{host}", path, tag


class Upstream:
    def __init__(self, api: str, repo: str, timeout: float):
        self.api, self.repo, self.timeout = api, repo, timeout
        self.token = None

    def _token(self, challenge: str):
        params = dict(re.findall(r'(\w+)="([^"]*)"', challenge or ""))
        realm = params.get("realm")
        if not challenge.lower().startswith("bearer") or not realm:
            raise Unknown(f"{self.api}: 익명 Bearer 토큰 흐름이 아니다({challenge!r})")
        query = urllib.parse.urlencode({"service": params.get("service", ""), "scope": f"repository:{self.repo}:pull"})
        status, _, body = http_get(f"{realm}?{query}", {}, self.timeout)
        if status != 200:
            raise Unknown(f"{realm}: 토큰 HTTP {status}")
        try:
            data = json.loads(body)
        except ValueError:
            raise Unknown(f"{realm}: 토큰 응답이 JSON 이 아니다") from None
        self.token = data.get("token") or data.get("access_token")
        if not self.token:
            raise Unknown(f"{realm}: 토큰이 비었다")

    def get(self, kind: str, ref: str, accept: str = ACCEPT):
        """kind = manifests|blobs. (status, headers, body). 401 이면 토큰을 받아 한 번 다시."""
        url = f"{self.api}/v2/{self.repo}/{kind}/{ref}"
        for attempt in (0, 1):
            headers = {"Accept": accept}
            if self.token:
                headers["Authorization"] = f"Bearer {self.token}"
            status, hdrs, body = http_get(url, headers, self.timeout)
            if status == 401 and attempt == 0:
                self._token(hdrs.get("www-authenticate", ""))
                continue
            return status, hdrs, body
        return status, hdrs, body


def _media_type(hdrs: dict, body: bytes) -> str:
    mt = (hdrs.get("content-type") or "").split(";", 1)[0].strip()
    if mt:
        return mt
    try:
        return json.loads(body).get("mediaType", "")
    except ValueError:
        return ""


def check_manifest(up: Upstream, digest: str):
    """digest 가 상류에서 linux/amd64 단일 매니페스트인지. 문제면 사유 문자열, 맞으면 None. 닿지 못하면 Unknown."""
    status, hdrs, body = up.get("manifests", digest)
    if status == 404:
        return f"상류에 {digest} 가 없다(404)"
    if status != 200:
        raise Unknown(f"{up.api}/{up.repo}@{digest}: HTTP {status}")
    if "sha256:" + hashlib.sha256(body).hexdigest() != digest:
        return "받은 본문의 sha256 이 DIGEST 와 다르다"
    mt = _media_type(hdrs, body)
    if mt in INDEX_TYPES:
        return f"index(멀티 아키) digest 다({mt}) — linux/amd64 매니페스트 digest 를 적는다(resolve)"
    if mt not in MANIFEST_TYPES:
        return f"단일 이미지 매니페스트가 아니다(mediaType {mt or '없음'})"
    try:
        cfg = json.loads(body)["config"]["digest"]
    except (ValueError, KeyError, TypeError):
        return "매니페스트에 config digest 가 없다"
    status, _, cbody = up.get("blobs", cfg, accept="application/json, */*")
    if status != 200:
        raise Unknown(f"{up.api}/{up.repo} config {cfg}: HTTP {status}")
    try:
        conf = json.loads(cbody)
    except ValueError:
        return "config 가 JSON 이 아니다"
    os_, arch = conf.get("os"), conf.get("architecture")
    if (os_, arch) != ("linux", "amd64"):
        return f"linux/amd64 가 아니다({os_}/{arch})"
    return None


def resolve(src: str, timeout: float):
    """SOURCE 태그 -> linux/amd64 매니페스트 digest. index 면 linux/amd64 항목(정확히 하나)을 고른다."""
    api, repo, tag = split_source(src)
    up = Upstream(api, repo, timeout)
    status, hdrs, body = up.get("manifests", tag)
    if status == 404:
        raise ValueError(f"상류에 태그 {tag} 가 없다(404)")
    if status != 200:
        raise Unknown(f"{src}: HTTP {status}")
    mt = _media_type(hdrs, body)
    if mt in INDEX_TYPES:
        cands = [m for m in json.loads(body).get("manifests", [])
                 if (m.get("platform") or {}).get("os") == "linux"
                 and (m.get("platform") or {}).get("architecture") == "amd64"]
        if len(cands) != 1:
            raise ValueError(f"index 에 linux/amd64 항목이 {len(cands)}개다")
        digest = cands[0]["digest"]
    else:
        digest = "sha256:" + hashlib.sha256(body).hexdigest()
    why = check_manifest(up, digest)
    if why:
        raise ValueError(why)
    return digest


# --- CLI -------------------------------------------------------------------------------------------------------------

def _read(path: str) -> str:
    try:
        # newline="": CR 을 그대로 둔다(기본값은 \r\n·\r 을 \n 으로 바꿔 CRLF 로 저장된 lock 을 감춘다 — sh 소비자는 \r 를 본다).
        with open(path, encoding="utf-8", newline="") as f:
            return f.read()
    except (OSError, UnicodeDecodeError) as e:
        print(f"FAIL: lock 을 읽지 못했다: {e}", file=sys.stderr)
        sys.exit(2)


def cmd_check(args) -> str:
    entries, problems = parse_lock(_read(args.lock))
    for no, why in problems:
        print(f"  FAIL  {args.lock}:{no}: {why}")
    if not entries and not problems:
        print(f"  FAIL  {args.lock}: 항목이 없다")
        return "FAIL"
    for _, dest, src, dg in entries:
        print(f"  OK    {dest:<24} {src} {dg[:19]}…")
    return "FAIL" if problems else "PASS"


def cmd_verify(args) -> str:
    entries, problems = parse_lock(_read(args.lock))
    result = "PASS"
    for no, why in problems:
        print(f"  FAIL  {args.lock}:{no}: {why}")
        result = "FAIL"
    unknown = False
    for no, dest, src, dg in entries:
        api, repo, _ = split_source(src)
        try:
            why = check_manifest(Upstream(api, repo, args.timeout), dg)
        except Unknown as e:
            print(f"  UNKNOWN {dest}: 상류에 닿지 못했다 — {e}")
            unknown = True
            continue
        if why:
            print(f"  FAIL  {args.lock}:{no} {dest}: {why}")
            result = "FAIL"
        else:
            print(f"  OK    {dest}: {src}@{dg[:19]}… = linux/amd64 단일 매니페스트")
    if result == "PASS" and unknown:
        return "UNKNOWN"
    return result


def cmd_resolve(args) -> str:
    why = dest_problem(args.dest) or source_problem(args.source)
    if why:
        print(f"FAIL: {why}", file=sys.stderr)
        sys.exit(2)
    try:
        digest = resolve(args.source, args.timeout)
    except Unknown as e:
        print(f"  UNKNOWN 상류에 닿지 못했다 — {e}", file=sys.stderr)
        return "UNKNOWN"
    except ValueError as e:
        print(f"  FAIL  {args.source}: {e}", file=sys.stderr)
        return "FAIL"
    print(f"{args.dest}  {args.source}  {digest}")
    return "PASS"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="images-lock.py", description="deploy/images.lock 검사·생성")
    sub = ap.add_subparsers(dest="cmd")
    for name in ("check", "verify"):
        p = sub.add_parser(name)
        p.add_argument("--lock", default=LOCK)
        p.add_argument("--timeout", type=float, default=30.0)
    p = sub.add_parser("resolve")
    p.add_argument("dest")
    p.add_argument("source")
    p.add_argument("--timeout", type=float, default=30.0)
    args = ap.parse_args(argv)
    if args.cmd is None:
        ap.print_usage(sys.stderr)
        return 2
    result = {"check": cmd_check, "verify": cmd_verify, "resolve": cmd_resolve}[args.cmd](args)
    stream = sys.stderr if args.cmd == "resolve" else sys.stdout   # resolve 의 stdout 은 lock 한 줄만
    print(f"RESULT: {result}", file=stream)
    return EXIT[result]


if __name__ == "__main__":
    sys.exit(main())
