#!/usr/bin/env python3
"""deploy/images.lock 검사·생성(2026-11-03 ④, 설계 v6 7-5 ①·8-1 S-1 「lock 에 index digest 가 있으면 실패」).

  python3 deploy/images-lock.py check   [--lock <파일>]                 # 형식(오프라인): 칸 수·DEST·SOURCE·digest 모양·DEST 중복,
                                                                         # sh 와 같은 줄·칸 규칙(CR·비 ASCII·들여쓴 주석 거부)
  python3 deploy/images-lock.py verify  [--lock <파일>] [--timeout 30]  # 상류에서: digest 가 단일 매니페스트(index 아님)이고 linux/amd64
  python3 deploy/images-lock.py resolve <DEST> <SOURCE> [--timeout 30]  # SOURCE 태그를 읽어 lock 한 줄을 출력(index 면 linux/amd64 항목)
  python3 deploy/images-lock.py compare-registry --registry-url http://127.0.0.1:<port> [--lock <파일>] [--only <이름>]... [--skip <이름>]...
                                                                         # S5 대조(2026-11-03 ⑦): 레지스트리의 각 DEST 태그 digest == lock

compare-registry(설계 7-5 ④·8-1 S5 「없거나 다르면 FAIL」): 설치 호스트가 `kubectl --context C -n dms port-forward svc/dms-registry
:<PORT>` 로 연 주소를 받는다(port-forward 는 apiserver 를 거치므로 설치 호스트가 ClusterIP 에 닿지 않아도 된다 — 여는 것은 호출자 몫).
줄마다 `GET /v2/<이름>/manifests/<태그>`(Accept = 매니페스트·index 네 종류) 본문의 sha256 을 lock digest 와 견준다 — 헤더
(Docker-Content-Digest)가 아니라 본문으로 잰다. 태그가 없거나 다른 digest 면 FAIL 이고, 그때 lock digest 가 레지스트리에 (태그 없이)
있는지도 봐서 사유를 가른다(태그 없는 digest 는 GC --delete-untagged 가 지운다). env 프록시는 쓰지 않는다(127.0.0.1 port-forward 도
urllib 는 HTTP_PROXY 로 보낸다). 레지스트리 자기 이미지(registry)는 설계 7-5 의 **태그 ref 예외** — 매니페스트가 digest 가 아니라 태그로
받으므로(S6 pull 프로브·새 마스터·업그레이드) 다른 것과 똑같이 대조하되 사유에 그것을 적는다. --only/--skip 은 DEST 또는 그 이름(태그 앞)
— 어느 줄에도 맞지 않으면 사용법 오류(exit 2, 오타가 조용히 0개가 되지 않게).

lock 형식은 deploy/images.lock 머리 주석이 정의한다(공백으로 가른 세 칸 DEST SOURCE DIGEST — sh 의 `read -r dest src dg` 로도 읽힌다).

왜 오프라인 검사와 온라인 검사를 나누나: digest 문자열만으로는 index 인지 매니페스트인지 알 수 없다 — 그것은 상류 레지스트리만 안다.
그래서 check 는 어디서나(테스트·설치 호스트) 돌고, verify·resolve 는 인터넷 노드(S-1 릴리스 담당·S5 반입)에서 돈다. 인터넷이 없거나
상류가 답하지 않으면 「모름」(exit 4)이지 실패가 아니다(DMS 규약: 모름 ≠ 실패).

상류 접근은 익명이다(docker.io·quay.io 의 Bearer 토큰 흐름 — WWW-Authenticate 를 따라 한 번 받는다). 프록시는 표준 env
(HTTPS_PROXY·NO_PROXY)를 urllib 가 그대로 쓴다. 설치 호스트의 python3(RHEL 9 = 3.9)로 돌고 표준 라이브러리만 쓴다.

종료: 0 PASS · 1 FAIL(형식 오류·index digest·amd64 아님·digest 불일치, compare-registry 의 태그 없음·digest 다름) · 2 사용법 ·
4 모름(네트워크·상류·레지스트리 무응답, 레지스트리가 익명이 아님). 마지막 줄 `RESULT: …`.
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

def _fetch(opener_open, url: str, headers: dict, timeout: float):
    req = urllib.request.Request(url, headers=headers)
    try:
        with opener_open(req, timeout=timeout) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, e.read() or b""
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise Unknown(f"{url}: {e}") from None


def http_get(url: str, headers: dict, timeout: float):
    """(status, 소문자 헤더 dict, body). 테스트가 이 함수를 바꿔치기한다. 연결 실패는 Unknown. 상류용 — env 프록시를 탄다."""
    return _fetch(urllib.request.urlopen, url, headers, timeout)


# 클러스터 내 레지스트리용(compare-registry, 2026-11-03 ⑦): env 프록시를 쓰지 않는다. urllib 는 127.0.0.1 도 예외로 두지 않아 설치
# 호스트에 HTTP_PROXY 가 있으면 port-forward 주소를 프록시로 보낸다 — 프록시는 그 주소에 닿지 않으므로 「모름」이 된다.
_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http_get_direct(url: str, headers: dict, timeout: float):
    """http_get 과 같되 프록시 없이(레지스트리 대조)."""
    return _fetch(_DIRECT.open, url, headers, timeout)


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
    def __init__(self, api: str, repo: str, timeout: float, fetch=None):
        self.api, self.repo, self.timeout = api, repo, timeout
        self.token = None
        self.fetch = fetch      # None = 모듈의 http_get(부를 때 찾는다 — 테스트의 바꿔치기가 먹게)

    def _get(self, url: str, headers: dict):
        return (self.fetch or http_get)(url, headers, self.timeout)

    def _token(self, challenge: str):
        params = dict(re.findall(r'(\w+)="([^"]*)"', challenge or ""))
        realm = params.get("realm")
        if not challenge.lower().startswith("bearer") or not realm:
            raise Unknown(f"{self.api}: 익명 Bearer 토큰 흐름이 아니다({challenge!r})")
        query = urllib.parse.urlencode({"service": params.get("service", ""), "scope": f"repository:{self.repo}:pull"})
        status, _, body = self._get(f"{realm}?{query}", {})
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
            status, hdrs, body = self._get(url, headers)
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


# --- 레지스트리 대조(S5, 2026-11-03 ⑦) ---------------------------------------------------------------------------------

# 태그로 받는 소비자가 있는 lock 줄(DEST 의 이름) — 이 줄의 태그가 다른 digest 를 가리키면 소비자가 lock 과 다른 이미지를 받는다. 나머지는
# 렌더러가 <REG>/<DEST>@<DIGEST> 로 digest 를 집으므로 태그는 GC 보호(태그 없는 digest 는 GC 가 지운다)뿐이다. 판정은 같고(없거나 다르면
# FAIL — 레지스트리 자기 이미지도 S6 pull 프로브가 이 태그를 Always 로 받으므로 예외로 넘기지 않는다) 사유만 다르다.
TAG_REF = {
    "registry": "태그 ref — 설계 7-5 의 문서화된 예외: deploy/platform/registry 가 태그로 쓰고 S6 pull 프로브·새 마스터·업그레이드가 이 태그로 받는다",
    "buildah": "태그 ref — 포탈 빌드의 빌더(DMS_BUILD_BUILDER_IMAGE = <REGISTRY>/buildah:stable)가 이 태그로 받는다",
}


def _short(digest: str) -> str:
    return digest[:19] + "…"


def _body_digest(body: bytes) -> str:
    return "sha256:" + hashlib.sha256(body).hexdigest()


def compare_entry(reg_url: str, dest: str, digest: str, timeout: float):
    """레지스트리의 DEST 태그가 lock digest 인가. (판정 OK|FAIL, 사유). 닿지 못하거나 익명이 아니면 Unknown."""
    repo, tag = _split_tagged(dest)
    reg = Upstream(reg_url, repo, timeout, fetch=http_get_direct)
    status, hdrs, body = reg.get("manifests", tag)
    if status == 200:
        got = _body_digest(body)
        if got == digest:
            mt = _media_type(hdrs, body)
            if mt in INDEX_TYPES:
                return "FAIL", f"태그 {tag} 가 index 다({mt}) — lock 은 linux/amd64 매니페스트 digest 여야 한다(images-lock.py verify)"
            return "OK", _short(digest)
        tagged = f"태그 {tag} 가 다른 digest({_short(got)})를 가리킨다"
    elif status == 404:
        tagged = f"태그 {tag} 가 없다(404)"
    elif status in (401, 403):
        raise Unknown(f"{reg_url}/v2/{repo}/manifests/{tag}: HTTP {status} — 익명 레지스트리가 아니다(주소 확인)")
    else:
        raise Unknown(f"{reg_url}/v2/{repo}/manifests/{tag}: HTTP {status}")
    # lock digest 가 (태그 없이) 레지스트리에 있는지로 사유를 가른다 — 고치는 길은 같다(태그를 붙여 다시 push).
    st2, _, body2 = reg.get("manifests", digest)
    if st2 == 200 and _body_digest(body2) == digest:
        return "FAIL", (f"{tagged}. lock digest({_short(digest)})는 레지스트리에 있지만 이 태그가 가리키지 않는다 — 태그 없는 digest 는 "
                        "GC(--delete-untagged)가 지운다")
    if st2 in (200, 404):
        if status == 404:
            return "FAIL", f"{tagged} — lock 의 이미지({_short(digest)})가 레지스트리에 없다(S5 push 전이거나 빠졌다)"
        return "FAIL", f"{tagged} — lock({_short(digest)})과 다른 이미지가 이 태그로 push 됐다"
    raise Unknown(f"{reg_url}/v2/{repo}/manifests/{digest}: HTTP {st2}")


def _registry_url(raw: str):
    """http(s)://<호스트>[:<포트>] 만(경로·계정·쿼리 없이). 아니면 None."""
    url = raw.rstrip("/")
    try:
        p = urllib.parse.urlsplit(url)
        p.port                                                # 포트 모양 검사(ValueError)
    except ValueError:
        return None
    if p.scheme not in ("http", "https") or not p.hostname or p.path or p.query or p.fragment or "@" in p.netloc:
        return None
    return url


def _select(entries, only, skip):
    """--only/--skip(DEST 또는 그 이름). 어느 줄에도 맞지 않는 이름은 사용법 오류 — 오타가 조용히 0개·전부가 되지 않게."""
    def hit(name, dest):
        return name == dest or name == _split_tagged(dest)[0]
    for name in list(only) + list(skip):
        if not any(hit(name, d) for _, d, _, _ in entries):
            print(f"FAIL: '{name}' 가 lock 의 어느 줄에도 맞지 않는다(--only/--skip 은 DEST 또는 그 이름)", file=sys.stderr)
            sys.exit(2)
    return [e for e in entries
            if (not only or any(hit(n, e[1]) for n in only)) and not any(hit(n, e[1]) for n in skip)]


def cmd_compare_registry(args) -> str:
    url = _registry_url(args.registry_url)
    if url is None:
        print("FAIL: --registry-url 은 http(s)://<호스트>:<포트> 만(경로·계정 없이 — 예: port-forward 의 http://127.0.0.1:15000)",
              file=sys.stderr)
        sys.exit(2)
    entries, problems = parse_lock(_read(args.lock))
    result = "PASS"
    for no, why in problems:
        print(f"  FAIL  {args.lock}:{no}: {why}")
        result = "FAIL"
    selected = _select(entries, args.only, args.skip)
    if not selected:
        print(f"  FAIL  {args.lock}: 대조할 항목이 없다")
        return "FAIL"
    # 먼저 /v2/ 한 번 — 닿지 않으면 줄마다 시간 초과를 기다리지 않고 「모름」 하나로 끝낸다(port-forward 가 닫혔거나 주소가 틀림).
    try:
        st, _, _ = http_get_direct(f"{url}/v2/", {}, args.timeout)
    except Unknown as e:
        print(f"  UNKNOWN 레지스트리에 닿지 못했다 — {e} (port-forward 가 열렸는지·주소)")
        return "UNKNOWN" if result == "PASS" else result
    if st not in (200, 401):
        print(f"  UNKNOWN {url}/v2/ 가 HTTP {st} — Distribution 레지스트리가 아니다(주소 확인)")
        return "UNKNOWN" if result == "PASS" else result
    unknown = False
    for no, dest, _src, dg in selected:
        name = _split_tagged(dest)[0]
        note = f" [{TAG_REF[name]}]" if name in TAG_REF else ""
        try:
            verdict, why = compare_entry(url, dest, dg, args.timeout)
        except Unknown as e:
            print(f"  UNKNOWN {dest}: {e}")
            unknown = True
            continue
        if verdict == "OK":
            print(f"  OK    {dest}: {why}{note}")
        else:
            print(f"  FAIL  {args.lock}:{no} {dest}: {why} — 인터넷 노드에서 push: "
                  f"sh deploy/push-images.sh --registry <REG> --only {dest} --run{note}")
            result = "FAIL"
    if result == "PASS" and unknown:
        return "UNKNOWN"
    return result


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
    p = sub.add_parser("compare-registry")
    p.add_argument("--registry-url", required=True)
    p.add_argument("--lock", default=LOCK)
    p.add_argument("--only", action="append", default=[])
    p.add_argument("--skip", action="append", default=[])
    p.add_argument("--timeout", type=float, default=30.0)
    args = ap.parse_args(argv)
    if args.cmd is None:
        ap.print_usage(sys.stderr)
        return 2
    result = {"check": cmd_check, "verify": cmd_verify, "resolve": cmd_resolve,
              "compare-registry": cmd_compare_registry}[args.cmd](args)
    stream = sys.stderr if args.cmd == "resolve" else sys.stdout   # resolve 의 stdout 은 lock 한 줄만
    print(f"RESULT: {result}", file=stream)
    return EXIT[result]


if __name__ == "__main__":
    sys.exit(main())
