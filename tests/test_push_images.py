"""deploy/push-images.sh — 인터넷 노드의 S5 push(2026-11-03 ⑦, 설계 v6 7-5 ③·8-1 S5).

- --print(기본)는 lock 의 줄마다 `skopeo copy --preserve-digests --dest-tls-verify=false docker://<SOURCE 저장소>@<DIGEST>
  docker://<REG>/<DEST>` 한 줄을 낸다 — 네트워크·skopeo 없이, 붙여 넣을 수 있는 sh(그 출력을 sh 로 돌리면 --run 과 같은 복사).
- --run 은 멱등이다: 레지스트리의 태그가 이미 lock digest 면 건너뛰고, 아니면 복사한 뒤 digest 를 다시 읽어 lock 과 견준다(본문 바이트의
  sha256 — 끝 개행이 있는 매니페스트도). 한 줄이 실패해도 나머지는 계속하고 끝에 FAIL.
- 프록시 env 가 있는데 NO_PROXY 가 레지스트리를 덮지 않으면 --run 은 아무것도 하지 않고 FAIL, --print 는 경고만(Go httpproxy 규칙:
  NO_PROXY 가 비어 있지 않으면 그것만, 정확한 IP·:포트·CIDR·도메인 접미·*, 루프백은 늘 제외).
- --only/--skip(DEST 또는 이름), 어느 줄에도 맞지 않는 이름·나쁜 --registry 는 사용법 오류(2), 형식이 틀린 lock 은 아무것도 push 하지
  않는다(python3 이 없어도 sh 검사로).

skopeo 는 PATH 앞의 심이다: 상류(digest → 본문 파일)와 레지스트리(태그 → 본문 파일)를 디렉터리로 흉내 내고 받은 인자를 기록한다.
"""
import hashlib
import os
import shutil
import subprocess

import pytest

from overlay_render import REPO_ROOT

SCRIPT = REPO_ROOT / "deploy" / "push-images.sh"
LOCK = REPO_ROOT / "deploy" / "images.lock"
REG = "10.112.0.50:5000"

_SKOPEO = r"""#!/bin/sh
printf '%s\n' "$*" >> "$STUB_LOG"
cmd=$1; shift
prev=""; last=""
for a in "$@"; do prev=$last; last=$a; done
key() { printf '%s' "${1#docker://}" | tr '/:@' '___'; }
case "$cmd" in
  inspect)
    f="$STUB_STATE/$(key "$last")"
    [ -f "$f" ] || { echo "manifest unknown" >&2; exit 1; }
    cat "$f" ;;
  copy)
    name=${last##*/}
    case " ${STUB_COPY_FAIL:-} " in *" $name "*) echo "copy boom" >&2; exit 1 ;; esac
    dg=${prev##*@}
    src="$STUB_UPSTREAM/${dg#sha256:}"
    [ -f "$src" ] || { echo "upstream manifest unknown" >&2; exit 1; }
    case " ${STUB_COPY_CORRUPT:-} " in
      *" $name "*) printf 'corrupt' > "$STUB_STATE/$(key "$last")" ;;
      *) cp "$src" "$STUB_STATE/$(key "$last")" ;;
    esac ;;
  *) exit 9 ;;
esac
"""

# --run 이 쓰는 도구만 — python3 을 빼고 sh 검사만으로 돌리는 경우를 만든다.
_TOOLS = ("sh", "dash", "grep", "awk", "cut", "sha256sum", "mktemp", "rm", "cat", "cp", "tr", "dirname", "env")


def _body(n: int) -> bytes:
    # 끝 개행이 있는 본문 — $(…) 로 읽어 해시하면 개행이 지워져 digest 가 달라진다(스크립트는 파일로 받아야 한다).
    return ('{"schemaVersion":2,"mediaType":"application/vnd.oci.image.manifest.v1+json","n":%d}\n' % n).encode()


def _digest(body: bytes) -> str:
    return "sha256:" + hashlib.sha256(body).hexdigest()


class Env:
    def __init__(self, tmp_path, python=True, skopeo=True):
        self.tmp = tmp_path
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        self.state = tmp_path / "registry"
        self.state.mkdir()
        self.upstream = tmp_path / "upstream"
        self.upstream.mkdir()
        self.log = tmp_path / "skopeo.log"
        if skopeo:
            stub = self.bin / "skopeo"
            stub.write_text(_SKOPEO)
            stub.chmod(0o755)
        if python:
            path = f"{self.bin}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}"
        else:
            for tool in _TOOLS:
                real = shutil.which(tool)
                if real:
                    (self.bin / tool).symlink_to(real)
            path = str(self.bin)
        self.env = {"PATH": path, "HOME": str(tmp_path), "STUB_LOG": str(self.log), "STUB_STATE": str(self.state),
                    "STUB_UPSTREAM": str(self.upstream), "LC_ALL": "C.UTF-8"}
        self.entries = []

    def lock(self, lines):
        """lines = [(dest, src, body)] — 상류에 본문을 두고 lock 을 쓴다."""
        out = ["# 시험 lock"]
        for dest, src, body in lines:
            dg = _digest(body)
            (self.upstream / dg[7:]).write_bytes(body)
            out.append(f"{dest}  {src}  {dg}")
            self.entries.append((dest, src, dg))
        p = self.tmp / "images.lock"
        p.write_text("\n".join(out) + "\n")
        return p

    def put(self, dest, body):
        (self.state / f"{REG}/{dest}".replace("/", "_").replace(":", "_")).write_bytes(body)

    def run(self, *args, **extra_env):
        env = dict(self.env, **extra_env)
        return subprocess.run(["sh", str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=60)

    def calls(self, verb=None):
        lines = self.log.read_text().splitlines() if self.log.exists() else []
        return [c for c in lines if verb is None or c.startswith(verb + " ")]


@pytest.fixture
def three(tmp_path):
    e = Env(tmp_path)
    lock = e.lock([("registry:3.1.2", "docker.io/library/registry:3.1.2", _body(1)),
                   ("buildah:stable", "quay.io/buildah/stable:v1.43.1", _body(2)),
                   ("acme/tool:v1", "ghcr.io/acme/tool:v1", _body(3))])
    return e, lock


def _copy_line(dest, src, dg, tls="false"):
    return f"skopeo copy --preserve-digests --dest-tls-verify={tls} docker://{src.rsplit(':', 1)[0]}@{dg} docker://{REG}/{dest}"


# --- 문법·--print -------------------------------------------------------------------------------------------------------------

def test_script_is_posix_sh():
    for shell in ("sh", "dash"):
        if shutil.which(shell):
            r = subprocess.run([shell, "-n", str(SCRIPT)], capture_output=True, text=True, timeout=30)
            assert r.returncode == 0, (shell, r.stderr)


def test_print_is_the_default_and_covers_every_repo_lock_line(tmp_path):
    e = Env(tmp_path)
    r = e.run("--registry", REG)
    assert r.returncode == 0, r.stderr
    cmds = [line for line in r.stdout.splitlines() if not line.startswith("#")]
    expected = []
    for line in LOCK.read_text().split("\n"):
        if line and not line.startswith("#"):
            dest, src, dg = line.split()
            expected.append(_copy_line(dest, src, dg))
    assert cmds == expected and len(expected) >= 2
    assert e.calls() == []                                    # 네트워크·skopeo 없이
    assert f"sh deploy/push-images.sh --registry {REG} --run" in r.stdout


def test_print_output_is_sh_that_does_the_same_copies_as_run(three, tmp_path):
    e, lock = three
    printed = e.run("--registry", REG, "--lock", str(lock))
    assert printed.returncode == 0, printed.stderr
    script = tmp_path / "printed.sh"
    script.write_text(printed.stdout)
    r = subprocess.run(["sh", str(script)], env=e.env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    from_print = e.calls("copy")
    assert from_print == [_copy_line(d, s, g)[len("skopeo "):] for d, s, g in e.entries]
    for f in e.state.iterdir():
        f.unlink()
    e.log.unlink()
    assert e.run("--registry", REG, "--lock", str(lock), "--run").returncode == 0
    assert e.calls("copy") == from_print


@pytest.mark.parametrize("args, dests", [
    (["--only", "registry"], ["registry:3.1.2"]),
    (["--only", "acme/tool:v1", "--only", "buildah"], ["buildah:stable", "acme/tool:v1"]),
    (["--skip", "registry:3.1.2"], ["buildah:stable", "acme/tool:v1"]),
    (["--only", "acme/tool", "--skip", "buildah"], ["acme/tool:v1"]),
])
def test_only_and_skip_select_by_dest_or_name(three, args, dests):
    e, lock = three
    r = e.run("--registry", REG, "--lock", str(lock), *args)
    assert r.returncode == 0, r.stderr
    got = [line.split(f"docker://{REG}/", 1)[1] for line in r.stdout.splitlines() if not line.startswith("#")]
    assert got == dests
    assert " ".join(args) in r.stdout                         # --run 안내에 같은 선택 인자


@pytest.mark.parametrize("args", [["--only", "nope"], ["--skip", "buildah:v9"], ["--only", "stable"], ["--only", ""]])
def test_a_name_that_matches_no_line_is_a_usage_error(three, args):
    e, lock = three
    r = e.run("--registry", REG, "--lock", str(lock), "--run", *args)
    assert r.returncode == 2 and e.calls() == [], (r.stdout, r.stderr)


@pytest.mark.parametrize("reg", ["", "http://10.0.0.1:5000", "10.0.0.1:5000/dms", "u@10.0.0.1:5000", ":5000", "10.0.0.1:",
                                 "a:b:5000", "10.0.0.1:50a0", "[::1]:5000", "-x:5000", "10.0.0.1 :5000"])
def test_registry_must_be_a_bare_host_port(three, reg):
    e, lock = three
    r = e.run("--registry", reg, "--lock", str(lock))
    assert r.returncode == 2 and r.stdout == "", (reg, r.stdout, r.stderr)


@pytest.mark.parametrize("args", [[], ["--registry"], ["--registry", REG, "--bogus"], ["--registry", REG, "--dest-tls-verify", "no"]])
def test_bad_usage_exits_2(three, args):
    e, _ = three
    assert e.run(*args).returncode == 2


def test_dest_tls_verify_true_for_a_tls_site_registry(three):
    e, lock = three
    r = e.run("--registry", REG, "--lock", str(lock), "--dest-tls-verify", "true", "--only", "registry")
    assert [line for line in r.stdout.splitlines() if not line.startswith("#")] == [
        _copy_line(*e.entries[0], tls="true")]
    assert "--run --only registry --dest-tls-verify true" in r.stdout


# --- lock 형식 ----------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("python", [True, False])
@pytest.mark.parametrize("bad", [
    "buildah:stable quay.io/buildah/stable:v1.43.1 sha256:{hex}\r",
    "buildah:stable quay.io/buildah/stable:v1.43.1 sha256:{hex} extra",
    "buildah:stable quay.io/buildah/stable:v1.43.1 sha256:{short}",
    "buildah:stable quay.io/buildah/stable:v1.43.1 sha256:{upper}",
    "buildah:stable quay.io/buildah/stable sha256:{hex}",
    "buildah quay.io/buildah/stable:v1.43.1 sha256:{hex}",
    "buildah:stable quay.io/buildah/stable@sha256:{hex} sha256:{hex}",
    "buildah:stable quay.io/buildah/stable:v1.43.1;rm sha256:{hex}",
])
def test_a_malformed_lock_pushes_nothing(tmp_path, python, bad):
    e = Env(tmp_path, python=python)
    hx = "a" * 64
    lock = tmp_path / "images.lock"
    lock.write_text(f"registry:3.1.2 docker.io/library/registry:3.1.2 sha256:{'b' * 64}\n"
                    + bad.format(hex=hx, short=hx[:10], upper=hx.upper().replace("A", "F")) + "\n")
    for mode in ("--print", "--run"):
        r = e.run("--registry", REG, "--lock", str(lock), mode)
        assert r.returncode == 1, (python, mode, r.stdout, r.stderr)
        assert "skopeo copy" not in r.stdout and "FAIL" in r.stderr
    assert e.calls() == []


def test_without_python3_the_sh_checks_still_read_the_repo_lock(tmp_path):
    e = Env(tmp_path, python=False)
    r = e.run("--registry", REG)
    assert r.returncode == 0, r.stderr
    assert len([line for line in r.stdout.splitlines() if line.startswith("skopeo copy")]) >= 2


def test_empty_lock_is_a_failure(tmp_path):
    e = Env(tmp_path)
    lock = tmp_path / "images.lock"
    lock.write_text("# 비었다\n")
    assert e.run("--registry", REG, "--lock", str(lock)).returncode == 1


# --- --run --------------------------------------------------------------------------------------------------------------------

def test_run_pushes_what_is_missing_and_is_idempotent(three):
    e, lock = three
    r = e.run("--registry", REG, "--lock", str(lock), "--run")
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert r.stdout.strip().splitlines()[-1] == "RESULT: PASS"
    assert e.calls("copy") == [_copy_line(d, s, g)[len("skopeo "):] for d, s, g in e.entries]
    assert r.stdout.count("  OK      ") == 3
    for dest, _, dg in e.entries:                              # 레지스트리 본문 == 상류 본문(끝 개행 포함)
        assert _digest((e.state / f"{REG}/{dest}".replace("/", "_").replace(":", "_")).read_bytes()) == dg
    e.log.unlink()
    again = e.run("--registry", REG, "--lock", str(lock), "--run")
    assert again.returncode == 0 and again.stdout.count("  SKIP    ") == 3, again.stdout
    assert e.calls("copy") == [] and len(e.calls("inspect")) == 3
    assert all(c.startswith("inspect --raw --tls-verify=false docker://10.112.0.50:5000/") for c in e.calls("inspect"))


def test_run_overwrites_a_tag_that_points_at_another_digest(three):
    e, lock = three
    e.put("registry:3.1.2", _body(99))
    e.put("buildah:stable", _body(2))                         # 이미 맞음
    r = e.run("--registry", REG, "--lock", str(lock), "--run")
    assert r.returncode == 0, r.stdout
    assert "다른 digest" in r.stdout and r.stdout.count("  SKIP    ") == 1
    assert [c.rsplit("/", 1)[-1] for c in e.calls("copy")] == ["registry:3.1.2", "tool:v1"]


def test_a_failed_copy_does_not_stop_the_other_lines(three):
    e, lock = three
    r = e.run("--registry", REG, "--lock", str(lock), "--run", STUB_COPY_FAIL="buildah:stable")
    assert r.returncode == 1
    assert len(e.calls("copy")) == 3
    assert "FAIL    buildah:stable — skopeo copy 실패(exit 1)" in r.stdout and "NO_PROXY" in r.stdout
    assert r.stdout.count("  OK      ") == 2 and r.stdout.strip().splitlines()[-1] == "RESULT: FAIL"


def test_run_rereads_the_digest_after_the_copy(three):
    e, lock = three
    r = e.run("--registry", REG, "--lock", str(lock), "--run", "--only", "acme/tool", STUB_COPY_CORRUPT="tool:v1")
    assert r.returncode == 1 and "push 뒤 레지스트리 digest" in r.stdout and "RESULT: FAIL" in r.stdout


def test_run_needs_skopeo(tmp_path):
    e = Env(tmp_path, python=False, skopeo=False)
    r = e.run("--registry", REG, "--run")
    assert r.returncode == 2 and "skopeo" in r.stderr


# --- 프록시 ---------------------------------------------------------------------------------------------------------------------

P = "http://proxy.corp:3128"


@pytest.mark.parametrize("reg, env, covered", [
    (REG, {}, True),
    (REG, {"HTTPS_PROXY": P}, False),
    (REG, {"http_proxy": P}, False),
    (REG, {"HTTPS_PROXY": P, "NO_PROXY": "10.112.0.50"}, True),
    (REG, {"HTTPS_PROXY": P, "NO_PROXY": "localhost, 10.112.0.50 ,.svc"}, True),
    (REG, {"https_proxy": P, "no_proxy": "10.112.0.50:5000"}, True),
    (REG, {"HTTPS_PROXY": P, "NO_PROXY": "10.112.0.50:6000"}, False),
    (REG, {"HTTPS_PROXY": P, "NO_PROXY": "10.112.0.0/12"}, True),
    (REG, {"HTTPS_PROXY": P, "NO_PROXY": "10.96.0.0/12"}, False),          # 10.96.0.0–10.111.255.255
    (REG, {"HTTPS_PROXY": P, "NO_PROXY": "0.0.0.0/0"}, True),
    (REG, {"HTTPS_PROXY": P, "NO_PROXY": "10.112.0.5"}, False),
    (REG, {"HTTPS_PROXY": P, "NO_PROXY": "*"}, True),
    (REG, {"HTTPS_PROXY": P, "NO_PROXY": ".svc", "no_proxy": "10.112.0.50"}, False),   # Go: NO_PROXY 가 있으면 그것만
    (REG, {"HTTPS_PROXY": P, "NO_PROXY": "", "no_proxy": "10.112.0.50"}, True),
    ("127.0.0.1:5000", {"HTTPS_PROXY": P}, True),                             # 루프백은 늘 직접
    ("reg.example.com:5000", {"HTTPS_PROXY": P, "NO_PROXY": ".example.com"}, True),
    ("reg.example.com:5000", {"HTTPS_PROXY": P, "NO_PROXY": "example.com"}, True),
    ("reg.example.com:5000", {"HTTPS_PROXY": P, "NO_PROXY": "*.example.com"}, True),
    ("reg.example.com:5000", {"HTTPS_PROXY": P, "NO_PROXY": "REG.EXAMPLE.COM"}, True),
    ("reg.example.com:5000", {"HTTPS_PROXY": P, "NO_PROXY": "ample.com"}, False),
])
def test_run_refuses_to_push_through_the_proxy(three, reg, env, covered):
    e, lock = three
    r = e.run("--registry", reg, "--lock", str(lock), "--run", **env)
    if covered:
        assert "NO_PROXY 가 레지스트리" not in r.stderr, r.stderr
        assert len(e.calls("copy")) == 3
    else:
        assert r.returncode == 1 and e.calls() == [], (r.stdout, r.stderr)
        assert "NO_PROXY 가 레지스트리" in r.stderr and r.stdout.strip().splitlines()[-1] == "RESULT: FAIL"
        host = reg.rsplit(":", 1)[0]
        assert f'export NO_PROXY="${{NO_PROXY:+$NO_PROXY,}}{host}"' in r.stderr


def test_print_only_warns_about_the_proxy(three):
    e, lock = three
    r = e.run("--registry", REG, "--lock", str(lock), HTTPS_PROXY=P)
    assert r.returncode == 0 and "WARN" in r.stderr and "NO_PROXY 가 레지스트리" in r.stderr
    assert len([line for line in r.stdout.splitlines() if line.startswith("skopeo copy")]) == 3
