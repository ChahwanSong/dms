"""deploy/images.lock + deploy/images-lock.py(2026-11-03 ④, 설계 v6 7-5 ①·8-1 S-1 「lock 에 index digest 가 있으면 실패」).

- 저장소의 lock 은 형식 검사(check)를 통과하고, sh 의 `read -r dest src dg` 로 읽어도 파이썬 파서와 같은 항목이다(S5 push 루프·설치기 ⑦).
- 형식 오류(칸 수·호스트 붙은 DEST·태그 없는 DEST/SOURCE·digest 모양·DEST 중복)는 줄 번호와 함께 FAIL.
- 온라인 검사(verify·resolve)는 상류 HTTP 를 바꿔치기해 본다(네트워크 없이): index digest → FAIL, linux/amd64 가 아닌 매니페스트 → FAIL,
  본문 digest 불일치·404 → FAIL, 상류 무응답 → 모름(exit 4 — 실패와 섞지 않는다), 401 → 익명 토큰 한 번 받고 다시, resolve 는 index 에서
  linux/amd64 항목을 고른다.
- 설치 호스트(RHEL 9)의 python3.9 로 돈다 — 3.9 문법만 쓰는지 ast 로 본다.
- compare-registry(S5 대조, 2026-11-03 ⑦)는 진짜 HTTP 서버(127.0.0.1)로 본다: 태그 본문 sha256 == lock → PASS, 태그 없음·다른 digest
  → FAIL(lock digest 가 태그 없이 있으면 GC 위험이라고), 레지스트리 자기 이미지는 태그 ref 예외(7-5)를 사유에 적고, 닿지 않음·익명 아님·
  5xx → 모름(FAIL 이 겹치면 FAIL), env 프록시를 쓰지 않음, --only/--skip, 나쁜 URL·맞지 않는 이름은 사용법 오류.
"""
import ast
import hashlib
import importlib.util
import json
import re
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from overlay_render import REPO_ROOT

SCRIPT = REPO_ROOT / "deploy" / "images-lock.py"
LOCK = REPO_ROOT / "deploy" / "images.lock"
_spec = importlib.util.spec_from_file_location("images_lock", SCRIPT)
lockmod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lockmod)

D = "sha256:" + "a" * 64


def test_script_is_python39_syntax():
    ast.parse(SCRIPT.read_text(encoding="utf-8"), feature_version=(3, 9))


def test_repo_lock_passes_the_format_check():
    r = subprocess.run([sys.executable, str(SCRIPT), "check"], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and r.stdout.strip().splitlines()[-1] == "RESULT: PASS", (r.stdout, r.stderr)
    entries, problems = lockmod.parse_lock(LOCK.read_text())
    assert problems == []
    dests = {d for _, d, _, _ in entries}
    assert {"registry:3.1.2", "buildah:stable"} <= dests


def test_sh_read_loop_sees_the_same_entries():
    # 머리 주석의 push 루프 모양(grep -v '^#' | while read -r dest src dg) — 칸이 공백 셋이라 sh 로도 그대로 읽힌다.
    r = subprocess.run(["sh", "-c", "grep -v '^#' \"$1\" | while read -r dest src dg; do [ -n \"$dest\" ] || continue; "
                        "printf '%s|%s|%s\\n' \"$dest\" \"$src\" \"$dg\"; done", "sh", str(LOCK)],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    from_sh = [tuple(line.split("|")) for line in r.stdout.splitlines()]
    entries, _ = lockmod.parse_lock(LOCK.read_text())
    assert from_sh == [(d, s, g) for _, d, s, g in entries]


@pytest.mark.parametrize("line, needle", [
    (f"registry:3.1.2 docker.io/library/registry:3.1.2", "칸이 2개"),
    (f"registry:3.1.2 docker.io/library/registry:3.1.2 {D} extra", "칸이 4개"),
    (f"docker.io/registry:3.1.2 docker.io/library/registry:3.1.2 {D}", "호스트"),
    (f"10.112.0.50:5000/registry:3.1.2 docker.io/library/registry:3.1.2 {D}", "호스트"),
    (f"registry docker.io/library/registry:3.1.2 {D}", "DEST 에 태그가 없다"),
    (f"registry@{D} docker.io/library/registry:3.1.2 {D}", "DEST 에 digest"),
    (f"Registry:3.1.2 docker.io/library/registry:3.1.2 {D}", "저장소 이름 규칙"),
    (f"registry:3.1.2 library/registry:3.1.2 {D}", "호스트"),
    (f"registry:3.1.2 registry:3.1.2 {D}", "호스트가 없다"),
    (f"registry:3.1.2 docker.io/library/registry {D}", "SOURCE 에 태그가 없다"),
    (f"registry:3.1.2 docker.io/library/registry@{D} {D}", "SOURCE 에 digest"),
    ("registry:3.1.2 docker.io/library/registry:3.1.2 sha256:abc", "DIGEST"),
    (f"registry:3.1.2 docker.io/library/registry:3.1.2 {D.upper()}", "DIGEST"),
    ("registry:3.1.2 docker.io/library/registry:3.1.2 sha512:" + "a" * 128, "DIGEST"),
    (f"registry:3.1.2 docker.io/library/registry:3.1.2 {D[7:]}", "DIGEST"),
])
def test_malformed_lines_are_rejected_with_their_line_number(tmp_path, line, needle):
    lock = tmp_path / "images.lock"
    lock.write_text(f"# 머리\n\nbuildah:stable quay.io/buildah/stable:v1.43.1 {D}\n{line}\n")
    entries, problems = lockmod.parse_lock(lock.read_text())
    assert [no for no, _ in problems] == [4], problems
    assert needle in problems[0][1], problems
    assert [d for _, d, _, _ in entries] == ["buildah:stable"]
    r = subprocess.run([sys.executable, str(SCRIPT), "check", "--lock", str(lock)], capture_output=True, text=True,
                       timeout=30)
    assert r.returncode == 1 and f"{lock}:4" in r.stdout and "RESULT: FAIL" in r.stdout


_SH_READ = ("grep -v '^#' \"$1\" | while read -r dest src dg; do [ -n \"$dest\" ] || continue; "
            "printf '%s|%s|%s\\n' \"$dest\" \"$src\" \"$dg\"; done")


@pytest.mark.parametrize("raw, needle", [
    (f"registry:3.1.2 docker.io/library/registry:3.1.2 {D}\r\n".encode(), "CR"),
    (f"registry:3.1.2　docker.io/library/registry:3.1.2　{D}\n".encode(), "ASCII 밖"),
    (f"registry:3.1.2 docker.io/library/registry:3.1.2 {D}\n".encode(), "ASCII 밖"),
    (f"registry:3.1.2 docker.io/library/registry:3.1.2 {D}\x0b\n".encode(), "제어 문자"),
    (b"  # \xec\xa3\xbc\xec\x84\x9d\n", "들여쓴 주석"),
])
def test_check_reads_lines_and_fields_like_the_sh_consumers(tmp_path, raw, needle):
    # 2026-11-03 ④ 리뷰: lock 의 실제 소비자는 sh(S5 push·대조 루프 `read -r dest src dg`, S0-5 `awk '$1==…'`)다. 파이썬이 너그럽게
    # 읽고 PASS 한 lock 을 sh 가 다르게 읽으면(digest 끝 \r, 전각 공백을 칸으로 안 봄, 들여쓴 주석을 항목으로) skopeo 는 틀린 ref 를 받고
    # 대조는 늘 FAIL 이다 — check 가 그 lock 을 거부해야 한다.
    lock = tmp_path / "images.lock"
    lock.write_bytes(b"# \xeb\xa8\xb8\xeb\xa6\xac\n" + f"buildah:stable quay.io/buildah/stable:v1.43.1 {D}\n".encode() + raw)
    # 바이트로 받는다 — text=True 는 \r\n 을 \n 으로 바꾸고 str.splitlines 는 \v 에서도 잘라 sh 가 본 것을 감춘다.
    sh = subprocess.run(["sh", "-c", _SH_READ, "sh", str(lock)], capture_output=True, timeout=30)
    assert sh.returncode == 0, sh.stderr
    seen = sh.stdout.split(b"\n")
    assert seen[0] == f"buildah:stable|quay.io/buildah/stable:v1.43.1|{D}".encode() and seen[2:] == [b""], seen
    assert seen[1] != f"registry:3.1.2|docker.io/library/registry:3.1.2|{D}".encode(), seen   # sh 는 다른 칸을 읽는다
    r = subprocess.run([sys.executable, str(SCRIPT), "check", "--lock", str(lock)], capture_output=True, text=True,
                       timeout=30)
    assert r.returncode == 1 and f"{lock}:3" in r.stdout and needle in r.stdout, (r.stdout, r.stderr)
    assert "RESULT: FAIL" in r.stdout


def test_tabs_and_extra_spaces_are_fields_for_both_readers(tmp_path):
    # 공백·탭(sh IFS)은 둘 다 칸 구분이다 — 줄 앞 공백도 sh 의 read·awk 가 버리므로 받는다.
    lock = tmp_path / "images.lock"
    lock.write_text(f"  registry:3.1.2\tdocker.io/library/registry:3.1.2   {D}  \n")
    entries, problems = lockmod.parse_lock(lock.read_text())
    assert problems == [] and [(d, s, g) for _, d, s, g in entries] == [
        ("registry:3.1.2", "docker.io/library/registry:3.1.2", D)]
    sh = subprocess.run(["sh", "-c", _SH_READ, "sh", str(lock)], capture_output=True, text=True, timeout=30)
    assert sh.stdout.splitlines() == [f"registry:3.1.2|docker.io/library/registry:3.1.2|{D}"]


def test_duplicate_dest_is_rejected(tmp_path):
    entries, problems = lockmod.parse_lock(f"a:1 docker.io/x/a:1 {D}\na:1 docker.io/y/a:1 {D}\n")
    assert len(entries) == 1 and problems == [(2, "DEST 'a:1' 가 1번째 줄과 겹친다")]


def test_empty_lock_fails_the_check(tmp_path):
    lock = tmp_path / "images.lock"
    lock.write_text("# 비었다\n")
    r = subprocess.run([sys.executable, str(SCRIPT), "check", "--lock", str(lock)], capture_output=True, text=True,
                       timeout=30)
    assert r.returncode == 1 and "항목이 없다" in r.stdout


# --- 온라인 검사(상류 HTTP 바꿔치기) ------------------------------------------------------------------------------------------

OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
OCI_INDEX = "application/vnd.oci.image.index.v1+json"


def _manifest(arch: str, os_: str = "linux"):
    config = json.dumps({"architecture": arch, "os": os_}).encode()
    cfg_digest = "sha256:" + hashlib.sha256(config).hexdigest()
    body = json.dumps({"schemaVersion": 2, "mediaType": OCI_MANIFEST,
                       "config": {"mediaType": "application/vnd.oci.image.config.v1+json", "digest": cfg_digest,
                                  "size": len(config)}, "layers": []}).encode()
    return "sha256:" + hashlib.sha256(body).hexdigest(), body, cfg_digest, config


class FakeUpstream:
    """경로 → (status, headers, body). 토큰이 필요한 레지스트리를 흉내 낸다(첫 요청 401 + WWW-Authenticate)."""

    def __init__(self, need_token=False):
        self.routes = {}
        self.calls = []
        self.need_token = need_token

    def add(self, path, status, body=b"", content_type=None):
        hdrs = {"content-type": content_type} if content_type else {}
        self.routes[path] = (status, hdrs, body)

    def __call__(self, url, headers, timeout):
        self.calls.append((url, dict(headers)))
        if url.startswith("https://auth.example/token"):
            return 200, {}, json.dumps({"token": "t0k"}).encode()
        if self.need_token and headers.get("Authorization") != "Bearer t0k":
            return 401, {"www-authenticate": 'Bearer realm="https://auth.example/token",service="reg"'}, b""
        path = url.split("/v2/", 1)[1]
        if path not in self.routes:
            return 404, {}, b""
        return self.routes[path]


@pytest.fixture
def fake(monkeypatch):
    up = FakeUpstream()
    monkeypatch.setattr(lockmod, "http_get", up)
    return up


def _lock(tmp_path, digest, dest="registry:3.1.2", src="quay.io/acme/registry:3.1.2"):
    p = tmp_path / "images.lock"
    p.write_text(f"{dest} {src} {digest}\n")
    return p


def _verify(lock):
    return lockmod.main(["verify", "--lock", str(lock)])


def test_verify_accepts_a_linux_amd64_single_manifest(tmp_path, fake, capsys):
    dg, body, cfg, config = _manifest("amd64")
    fake.add(f"acme/registry/manifests/{dg}", 200, body, OCI_MANIFEST)
    fake.add(f"acme/registry/blobs/{cfg}", 200, config)
    assert _verify(_lock(tmp_path, dg)) == 0
    assert "RESULT: PASS" in capsys.readouterr().out


def test_verify_rejects_an_index_digest(tmp_path, fake, capsys):
    dg, _, _, _ = _manifest("amd64")
    index = json.dumps({"schemaVersion": 2, "mediaType": OCI_INDEX, "manifests": [
        {"digest": dg, "mediaType": OCI_MANIFEST, "platform": {"architecture": "amd64", "os": "linux"}}]}).encode()
    idg = "sha256:" + hashlib.sha256(index).hexdigest()
    fake.add(f"acme/registry/manifests/{idg}", 200, index, OCI_INDEX)
    assert _verify(_lock(tmp_path, idg)) == 1
    out = capsys.readouterr().out
    assert "index(멀티 아키) digest" in out and "RESULT: FAIL" in out


def test_verify_rejects_a_docker_manifest_list_even_without_content_type(tmp_path, fake, capsys):
    index = json.dumps({"schemaVersion": 2, "mediaType": "application/vnd.docker.distribution.manifest.list.v2+json",
                        "manifests": []}).encode()
    idg = "sha256:" + hashlib.sha256(index).hexdigest()
    fake.add(f"acme/registry/manifests/{idg}", 200, index)               # Content-Type 없음 → 본문 mediaType
    assert _verify(_lock(tmp_path, idg)) == 1
    assert "index(멀티 아키) digest" in capsys.readouterr().out


@pytest.mark.parametrize("arch, os_", [("arm64", "linux"), ("amd64", "windows")])
def test_verify_rejects_a_non_linux_amd64_manifest(tmp_path, fake, capsys, arch, os_):
    dg, body, cfg, config = _manifest(arch, os_)
    fake.add(f"acme/registry/manifests/{dg}", 200, body, OCI_MANIFEST)
    fake.add(f"acme/registry/blobs/{cfg}", 200, config)
    assert _verify(_lock(tmp_path, dg)) == 1
    assert f"linux/amd64 가 아니다({os_}/{arch})" in capsys.readouterr().out


def test_verify_rejects_a_body_that_is_not_the_digest(tmp_path, fake, capsys):
    _, body, _, _ = _manifest("amd64")
    fake.add(f"acme/registry/manifests/{D}", 200, body, OCI_MANIFEST)
    assert _verify(_lock(tmp_path, D)) == 1
    assert "sha256 이 DIGEST 와 다르다" in capsys.readouterr().out


def test_verify_missing_digest_is_a_failure(tmp_path, fake, capsys):
    assert _verify(_lock(tmp_path, D)) == 1
    assert "404" in capsys.readouterr().out


def test_unreachable_upstream_is_unknown_not_failure(tmp_path, monkeypatch, capsys):
    def boom(url, headers, timeout):
        raise lockmod.Unknown(f"{url}: connection refused")
    monkeypatch.setattr(lockmod, "http_get", boom)
    assert _verify(_lock(tmp_path, D)) == 4
    out = capsys.readouterr().out
    assert "UNKNOWN" in out and "RESULT: UNKNOWN" in out


def test_anonymous_token_flow_and_docker_hub_naming(tmp_path, monkeypatch, capsys):
    up = FakeUpstream(need_token=True)
    monkeypatch.setattr(lockmod, "http_get", up)
    dg, body, cfg, config = _manifest("amd64")
    up.add(f"library/registry/manifests/{dg}", 200, body, OCI_MANIFEST)
    up.add(f"library/registry/blobs/{cfg}", 200, config)
    assert _verify(_lock(tmp_path, dg, src="docker.io/library/registry:3.1.2")) == 0
    urls = [u for u, _ in up.calls]
    assert urls[0] == f"https://registry-1.docker.io/v2/library/registry/manifests/{dg}"
    assert any(u.startswith("https://auth.example/token?") and "repository%3Alibrary%2Fregistry%3Apull" in u for u in urls)
    assert up.calls[-1][1].get("Authorization") == "Bearer t0k"


def test_resolve_picks_the_linux_amd64_entry_of_an_index(fake, capsys):
    dg, body, cfg, config = _manifest("amd64")
    arm, _, _, _ = _manifest("arm64")
    index = json.dumps({"schemaVersion": 2, "mediaType": OCI_INDEX, "manifests": [
        {"digest": arm, "mediaType": OCI_MANIFEST, "platform": {"architecture": "arm64", "os": "linux"}},
        {"digest": dg, "mediaType": OCI_MANIFEST, "platform": {"architecture": "amd64", "os": "linux"}},
        {"digest": D, "mediaType": OCI_MANIFEST, "platform": {"architecture": "unknown", "os": "unknown"}}]}).encode()
    fake.add("acme/registry/manifests/3.1.2", 200, index, OCI_INDEX)
    fake.add(f"acme/registry/manifests/{dg}", 200, body, OCI_MANIFEST)
    fake.add(f"acme/registry/blobs/{cfg}", 200, config)
    assert lockmod.main(["resolve", "registry:3.1.2", "quay.io/acme/registry:3.1.2"]) == 0
    out = capsys.readouterr()
    assert out.out.strip() == f"registry:3.1.2  quay.io/acme/registry:3.1.2  {dg}"     # stdout 은 lock 한 줄뿐
    assert "RESULT: PASS" in out.err


def test_resolve_of_a_single_manifest_tag_uses_its_own_digest(fake, capsys):
    dg, body, cfg, config = _manifest("amd64")
    fake.add("acme/tool/manifests/v1", 200, body, OCI_MANIFEST)
    fake.add(f"acme/tool/manifests/{dg}", 200, body, OCI_MANIFEST)
    fake.add(f"acme/tool/blobs/{cfg}", 200, config)
    assert lockmod.main(["resolve", "tool:v1", "quay.io/acme/tool:v1"]) == 0
    assert capsys.readouterr().out.strip().endswith(dg)


def test_resolve_refuses_an_index_without_exactly_one_linux_amd64(fake, capsys):
    index = json.dumps({"schemaVersion": 2, "mediaType": OCI_INDEX, "manifests": [
        {"digest": D, "mediaType": OCI_MANIFEST, "platform": {"architecture": "arm64", "os": "linux"}}]}).encode()
    fake.add("acme/registry/manifests/3.1.2", 200, index, OCI_INDEX)
    assert lockmod.main(["resolve", "registry:3.1.2", "quay.io/acme/registry:3.1.2"]) == 1
    assert "linux/amd64 항목이 0개" in capsys.readouterr().err


def test_resolve_rejects_malformed_arguments(capsys):
    with pytest.raises(SystemExit) as e:
        lockmod.main(["resolve", "docker.io/registry:3.1.2", "docker.io/library/registry:3.1.2"])
    assert e.value.code == 2


# --- compare-registry(S5 대조, 2026-11-03 ⑦) — 진짜 HTTP 서버(127.0.0.1)로 ------------------------------------------------------
# 설치기가 port-forward 로 연 레지스트리 주소를 받아 줄마다 태그의 매니페스트 본문 sha256 을 lock digest 와 견준다(설계 7-5 ④·8-1 S5
# 「없거나 다르면 FAIL」).

DOCKER_V2 = "application/vnd.docker.distribution.manifest.v2+json"


class FakeRegistry:
    def __init__(self):
        self.manifests = {}           # (repo, ref) -> (content_type, body)
        self.requests = []            # (method, path, accept)
        self.override = {}            # 경로 접두 -> (status, headers)
        self.url = None

    def put(self, repo, tag, body, content_type=OCI_MANIFEST, tagged=True):
        dg = "sha256:" + hashlib.sha256(body).hexdigest()
        self.manifests[(repo, dg)] = (content_type, body)
        if tagged:
            self.manifests[(repo, tag)] = (content_type, body)
        return dg


def _handler(reg):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, status, body=b"", headers=None):
            self.send_response(status)
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command == "GET":
                self.wfile.write(body)

        def do_GET(self):
            reg.requests.append((self.command, self.path, self.headers.get("Accept", "")))
            for prefix, (status, hdrs) in reg.override.items():
                if self.path.startswith(prefix):
                    return self._send(status, b'{"errors":[]}', hdrs)
            if self.path == "/v2/":
                return self._send(200, b"{}", {"Content-Type": "application/json"})
            m = re.match(r"^/v2/(.+)/manifests/([^/]+)$", self.path)
            if m and (m.group(1), m.group(2)) in reg.manifests:
                ct, body = reg.manifests[(m.group(1), m.group(2))]
                return self._send(200, body, {"Content-Type": ct,
                                              "Docker-Content-Digest": "sha256:" + hashlib.sha256(body).hexdigest()})
            return self._send(404, b'{"errors":[{"code":"MANIFEST_UNKNOWN"}]}', {"Content-Type": "application/json"})

        do_HEAD = do_GET
    return H


@pytest.fixture
def registry(monkeypatch):
    # 대조는 env 프록시를 쓰지 않는다 — 닿지 않는 프록시를 걸어 두고도 PASS 해야 한다(설치 호스트에 프록시가 있는 사이트; urllib 는
    # 127.0.0.1 도 프록시로 보낸다). os.environ 을 바꾸므로 subprocess 로 돌린 CLI 도 같은 조건이다.
    for k in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
        monkeypatch.setenv(k, "http://127.0.0.1:9")
    for k in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(k, raising=False)
    reg = FakeRegistry()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _handler(reg))
    t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    t.start()
    reg.url = f"http://127.0.0.1:{srv.server_address[1]}"
    yield reg
    srv.shutdown()
    srv.server_close()


def _two(tmp_path, registry, reg_tagged=True, cnpg_tagged=True):
    _, reg_body, _, _ = _manifest("amd64")
    cnpg_body = json.dumps({"schemaVersion": 2, "mediaType": DOCKER_V2, "x": "cnpg"}).encode()
    rdg = registry.put("registry", "3.1.2", reg_body, tagged=reg_tagged)
    cdg = registry.put("cloudnative-pg", "1.30.1", cnpg_body, content_type=DOCKER_V2, tagged=cnpg_tagged)
    lock = tmp_path / "images.lock"
    lock.write_text(f"# 시험\nregistry:3.1.2 docker.io/library/registry:3.1.2 {rdg}\n"
                    f"cloudnative-pg:1.30.1 ghcr.io/cloudnative-pg/cloudnative-pg:1.30.1 {cdg}\n")
    return lock, rdg, cdg


def _compare(registry, lock, *extra):
    return lockmod.main(["compare-registry", "--registry-url", registry.url, "--lock", str(lock), "--timeout", "5", *extra])


def _line(out, needle):
    return [ln for ln in out.splitlines() if needle in ln][0]


def test_compare_registry_passes_when_every_tag_is_the_lock_digest(tmp_path, registry, capsys):
    lock, rdg, cdg = _two(tmp_path, registry)
    assert _compare(registry, lock) == 0
    out = capsys.readouterr().out
    assert f"OK    registry:3.1.2: {rdg[:19]}…" in out and f"OK    cloudnative-pg:1.30.1: {cdg[:19]}…" in out
    assert "태그 ref — 설계 7-5" in _line(out, "registry:3.1.2")       # 레지스트리 자기 이미지 = 문서화된 태그 ref 예외
    assert "태그 ref" not in _line(out, "cloudnative-pg")
    assert out.strip().splitlines()[-1] == "RESULT: PASS"
    manifest_reqs = [(p, a) for _, p, a in registry.requests if "/manifests/" in p]
    assert [p for p, _ in manifest_reqs] == ["/v2/registry/manifests/3.1.2", "/v2/cloudnative-pg/manifests/1.30.1"]
    for _, accept in manifest_reqs:                               # 레지스트리가 저장한 모양 그대로 돌려주게
        assert OCI_MANIFEST in accept and DOCKER_V2 in accept


def test_compare_registry_cli_exit_code_and_result_line(tmp_path, registry):
    lock, _, _ = _two(tmp_path, registry)
    r = subprocess.run([sys.executable, str(SCRIPT), "compare-registry", "--registry-url", registry.url + "/",
                        "--lock", str(lock)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and r.stdout.strip().splitlines()[-1] == "RESULT: PASS", (r.stdout, r.stderr)


def test_a_missing_tag_fails_with_a_push_hint(tmp_path, registry, capsys):
    lock, _, cdg = _two(tmp_path, registry)
    del registry.manifests[("cloudnative-pg", "1.30.1")]
    del registry.manifests[("cloudnative-pg", cdg)]
    assert _compare(registry, lock) == 1
    out = capsys.readouterr().out
    line = _line(out, "cloudnative-pg")
    assert line.startswith(f"  FAIL  {lock}:3 cloudnative-pg:1.30.1: 태그 1.30.1 가 없다(404)")
    assert "레지스트리에 없다" in line
    assert "sh deploy/push-images.sh --registry <REG> --only cloudnative-pg:1.30.1 --run" in line
    assert "OK    registry:3.1.2" in out and out.strip().splitlines()[-1] == "RESULT: FAIL"


def test_an_untagged_lock_digest_is_named_as_a_gc_risk(tmp_path, registry, capsys):
    lock, _, _ = _two(tmp_path, registry, cnpg_tagged=False)
    assert _compare(registry, lock) == 1
    line = _line(capsys.readouterr().out, "cloudnative-pg")
    assert "태그 1.30.1 가 없다(404)" in line and "GC(--delete-untagged)" in line


@pytest.mark.parametrize("lock_digest_present", [True, False])
def test_a_tag_pointing_at_another_digest_fails(tmp_path, registry, capsys, lock_digest_present):
    lock, rdg, _ = _two(tmp_path, registry)
    other = registry.put("registry", "3.1.2", b'{"schemaVersion":2,"other":1}')
    if not lock_digest_present:
        del registry.manifests[("registry", rdg)]
    assert _compare(registry, lock) == 1
    line = _line(capsys.readouterr().out, "registry:3.1.2")
    assert line.startswith(f"  FAIL  {lock}:2 registry:3.1.2: 태그 3.1.2 가 다른 digest({other[:19]}…)를 가리킨다")
    assert ("는 레지스트리에 있지만" in line) is lock_digest_present
    assert ("다른 이미지가 이 태그로 push 됐다" in line) is (not lock_digest_present)
    assert "태그 ref — 설계 7-5" in line                         # 태그로 받는 소비자가 다른 이미지를 받는다


def test_a_tag_whose_bytes_are_an_index_fails_even_when_the_digest_matches(tmp_path, registry, capsys):
    index = json.dumps({"schemaVersion": 2, "mediaType": OCI_INDEX, "manifests": []}).encode()
    idg = registry.put("tool", "v1", index, content_type=OCI_INDEX)
    lock = tmp_path / "images.lock"
    lock.write_text(f"tool:v1 quay.io/acme/tool:v1 {idg}\n")
    assert _compare(registry, lock) == 1
    assert "index 다" in capsys.readouterr().out


def test_an_unreachable_registry_is_unknown_once_not_per_line(tmp_path, registry, capsys):
    lock, _, _ = _two(tmp_path, registry)
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    assert lockmod.main(["compare-registry", "--registry-url", f"http://127.0.0.1:{port}", "--lock", str(lock),
                         "--timeout", "5"]) == 4
    out = capsys.readouterr().out
    assert out.count("UNKNOWN") == 2 and "port-forward" in out and out.strip().endswith("RESULT: UNKNOWN")


def test_a_registry_that_is_not_anonymous_is_unknown(tmp_path, registry, capsys):
    lock, _, _ = _two(tmp_path, registry)
    registry.override["/v2/"] = (401, {"WWW-Authenticate": 'Basic realm="corp"'})
    assert _compare(registry, lock) == 4
    out = capsys.readouterr().out
    assert "UNKNOWN registry:3.1.2" in out and out.strip().endswith("RESULT: UNKNOWN")


def test_server_errors_are_unknown_but_a_failure_wins(tmp_path, registry, capsys):
    lock, _, cdg = _two(tmp_path, registry)
    registry.override["/v2/registry/"] = (500, {})
    assert _compare(registry, lock) == 4                          # 다른 줄은 OK — 모름
    assert "HTTP 500" in capsys.readouterr().out
    del registry.manifests[("cloudnative-pg", "1.30.1")]
    assert _compare(registry, lock) == 1                          # 다른 줄이 FAIL 이면 FAIL


def test_lock_format_problems_fail_the_comparison(tmp_path, registry, capsys):
    lock, _, _ = _two(tmp_path, registry)
    lock.write_text(lock.read_text() + "broken line\n")
    assert _compare(registry, lock) == 1
    out = capsys.readouterr().out
    assert f"{lock}:4: 칸이 2개" in out and "OK    registry:3.1.2" in out


@pytest.mark.parametrize("extra, compared", [
    (["--skip", "registry", "--skip", "volcano-scheduler"], ["cloudnative-pg:1.30.1"]),
    (["--only", "registry:3.1.2"], ["registry:3.1.2"]),
    (["--only", "cloudnative-pg", "--only", "registry"], ["registry:3.1.2", "cloudnative-pg:1.30.1"]),
])
def test_only_and_skip_choose_lines(tmp_path, registry, capsys, extra, compared):
    lock, _, _ = _two(tmp_path, registry)
    lock.write_text(lock.read_text() + f"volcano-scheduler:v1.16.0 docker.io/volcanosh/vc-scheduler:v1.16.0 {D}\n")
    assert _compare(registry, lock, *extra) == 0                  # 레지스트리에 없는 volcano 줄은 고르지 않았다
    out = capsys.readouterr().out
    assert [ln.split()[1].rstrip(":") for ln in out.splitlines() if ln.startswith("  OK")] == compared


@pytest.mark.parametrize("extra", [["--only", "nope"], ["--skip", "stable"], ["--only", "registry:9"]])
def test_a_name_matching_no_line_is_a_usage_error(tmp_path, registry, extra):
    lock, _, _ = _two(tmp_path, registry)
    with pytest.raises(SystemExit) as e:
        _compare(registry, lock, *extra)
    assert e.value.code == 2
    assert not [r for r in registry.requests if "/manifests/" in r[1]]


@pytest.mark.parametrize("url", ["127.0.0.1:5000", "ftp://127.0.0.1:5000", "http://127.0.0.1:5000/v2", "http://u@127.0.0.1:5000",
                                 "http://127.0.0.1:port", "http://", "http://127.0.0.1:5000?x=1"])
def test_registry_url_must_be_a_bare_http_origin(tmp_path, url):
    lock = tmp_path / "images.lock"
    lock.write_text(f"tool:v1 quay.io/acme/tool:v1 {D}\n")
    with pytest.raises(SystemExit) as e:
        lockmod.main(["compare-registry", "--registry-url", url, "--lock", str(lock)])
    assert e.value.code == 2


def test_compare_registry_requires_the_url():
    with pytest.raises(SystemExit) as e:
        lockmod.main(["compare-registry"])
    assert e.value.code == 2
