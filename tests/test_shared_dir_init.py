"""공용 디렉터리 S0 초기화(deploy/shared-dir-init.sh, 2026-11-03 ③ -- 설계 v6 3-1 · 8-1 「S0 세부」 3 · T3 「가드 음성 대조」) 계약.

실제 sh(dash)로 돌린다. 마운트 판정은 PATH 앞 findmnt 심으로 바꾸고(이 호스트의 실 마운트에 기대지 않는다), root 가 필요한 소유
(26·65532) 맞추기는 fakeroot 아래서 본다(없으면 그 테스트만 건너뛴다 -- 가드 테스트는 fakeroot 없이도 돈다).

고정하는 것:
- 가드: SHARED_DIR 가 없거나 포함 마운트가 '/'(마운트 없음)면 FAIL 이고 **아무것도 만들지 않는다**(SHARED_DIR·상위 포함 -- 마운트가
  빠진 노드에서 경로를 만들면 hostPath type Directory 가 빈 로컬 디렉터리로 통과한다, 설계 #29). findmnt 가 답하지 않으면 모름(4).
  형식 위반·root 아님은 사용법(2).
- 트리·센티널: 3-1 의 소유·모드로 한 단계씩 만들고, `.dms-shared-id`(무작위) = `registry/.dms-registry-id`, 각 `.dms-pv-id` = PV 이름.
- 다시 돌리면 검사만(값·모양 그대로), 어긋나면 FAIL 하고 **고치지 않는다**, 심링크는 따라가지 않는다, --check 는 아무것도 안 만든다.
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "deploy" / "shared-dir-init.sh"
FAKEROOT = shutil.which("fakeroot")
ROOT = os.geteuid() == 0
needs_root_or_fakeroot = pytest.mark.skipif(not (ROOT or FAKEROOT), reason="root 또는 fakeroot 가 필요하다(소유 맞추기)")

TREE = {
    "dms": (0, 0, 0o750), "dms/artifacts": (0, 0, 0o755), "dms/pgdata": (0, 0, 0o700), "dms/pgdata/.retired": (0, 0, 0o700),
    **{f"dms/pgdata/dms-pgdata-0{n}": (26, 26, 0o700) for n in range(1, 6)},
    "dms/backup": (0, 0, 0o750), "dms/backup/pg": (26, 26, 0o700), "dms/registry": (65532, 65532, 0o750),
}


def _env(tmp_path, target="/dms_shared"):
    """findmnt 심: target 이 None 이면 실패(답 없음), 아니면 그 TARGET 을 답한다."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    shim = bindir / "findmnt"
    if target is None:
        shim.write_text("#!/bin/sh\nexit 1\n")
    else:
        shim.write_text('#!/bin/sh\ncase "$*" in *TARGET,SOURCE*) echo "%s 10.0.0.1:/e nfs4 rw,lookupcache=positive";; '
                        '*) echo "%s";; esac\n' % (target, target))
    shim.chmod(0o755)
    return {"PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}", "LC_ALL": "C.UTF-8"}


def _run(tmp_path, *args, target="/dms_shared", fake=True):
    cmd = ["sh", str(SCRIPT), *args]
    if fake and not ROOT and FAKEROOT:
        state = tmp_path / "fakeroot.state"
        cmd = [FAKEROOT, *(["-i", str(state)] if state.exists() else []), "-s", str(state), "--", *cmd]
    return subprocess.run(cmd, env=_env(tmp_path, target), capture_output=True, text=True, timeout=60)


def _owners(tmp_path, shared: Path) -> dict:
    """{상대 경로: (uid, gid, mode)} -- fakeroot 면 그 세션이 기억하는 소유로 읽는다."""
    paths = [str(shared / rel) for rel in TREE]
    cmd = ["stat", "-c", "%u %g %a", *paths]
    if not ROOT and FAKEROOT:
        cmd = [FAKEROOT, "-i", str(tmp_path / "fakeroot.state"), "--", *cmd]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    out = {}
    for rel, line in zip(TREE, r.stdout.splitlines()):
        uid, gid, mode = line.split()
        out[rel] = (int(uid), int(gid), int(mode, 8))
    return out


def _as_root(tmp_path, *cmd):
    """소유·모드 바꾸기를 같은 fakeroot 세션 안에서 한다(밖에서 chmod 하면 fakeroot 가 기억하는 모드가 이긴다)."""
    if not ROOT and FAKEROOT:
        state = str(tmp_path / "fakeroot.state")
        cmd = (FAKEROOT, "-i", state, "-s", state, "--", *cmd)
    subprocess.run(list(cmd), check=True, timeout=30)


def _shared(tmp_path) -> Path:
    d = tmp_path / "dms_shared"
    d.mkdir()
    return d


def _listing(root: Path) -> list:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


# --- 가드(만들기 전) ---------------------------------------------------------------------------------------------------

def test_missing_shared_dir_is_refused_and_nothing_is_created(tmp_path):
    parent = tmp_path / "mnt"
    parent.mkdir()
    r = _run(tmp_path, str(parent / "dms_shared"))
    assert r.returncode == 1 and "RESULT: FAIL" in r.stdout, (r.stdout, r.stderr)
    assert "만들지 않는다" in r.stdout
    assert os.listdir(parent) == []                                       # SHARED_DIR 도 상위도 만들지 않는다


@pytest.mark.parametrize("fake", [True, False])
def test_root_filesystem_target_is_mount_missing_and_nothing_is_created(tmp_path, fake):
    # T3 「가드 음성 대조」: cp1 의 /cephfs 처럼 마운트가 없는 경로(빈 로컬 디렉터리) -- 포함 마운트가 '/'.
    if not fake and not ROOT:
        pytest.skip("root 아님 검사가 먼저 걸린다")
    shared = _shared(tmp_path)
    r = _run(tmp_path, str(shared), target="/", fake=fake)
    assert r.returncode == 1 and "마운트 없음" in r.stdout and "RESULT: FAIL" in r.stdout, (r.stdout, r.stderr)
    assert os.listdir(shared) == []


def test_findmnt_without_an_answer_is_unknown_and_nothing_is_created(tmp_path):
    shared = _shared(tmp_path)
    r = _run(tmp_path, str(shared), target=None)
    assert r.returncode == 4 and "RESULT: UNKNOWN" in r.stdout, (r.stdout, r.stderr)
    assert os.listdir(shared) == []


@pytest.mark.parametrize("bad", ["", "dms_shared", "/", "/a/", "//a", "/a/../b", "/a b", "/a|b", "/한글"])
def test_malformed_shared_dir_is_a_usage_error(tmp_path, bad):
    r = _run(tmp_path, bad) if bad else _run(tmp_path)
    assert r.returncode == 2, (bad, r.stdout, r.stderr)


def test_non_root_is_refused_before_touching_anything(tmp_path):
    if ROOT:
        pytest.skip("root 로 돌고 있다")
    shared = _shared(tmp_path)
    r = _run(tmp_path, str(shared), fake=False)
    assert r.returncode == 2 and "root" in r.stderr, (r.stdout, r.stderr)
    assert os.listdir(shared) == []


def test_script_never_creates_parents():
    # 마운트가 빠진 노드에 경로를 만드는 명령(mkdir -p·install -d)이 코드 줄에 없다(설계 S0 세부 3).
    code = "\n".join(ln for ln in SCRIPT.read_text().splitlines() if not ln.lstrip().startswith("#"))
    assert not re.search(r"\bmkdir\s+(-\w*p|--parents)", code) and "install -d" not in code


# --- 트리·센티널 -------------------------------------------------------------------------------------------------------

@needs_root_or_fakeroot
def test_fresh_init_builds_the_tree_and_sentinels_then_rerun_only_checks(tmp_path):
    shared = _shared(tmp_path)
    r = _run(tmp_path, str(shared))
    assert r.returncode == 0 and r.stdout.rstrip().endswith("RESULT: PASS"), (r.stdout, r.stderr)
    assert "shared-storage-probe.py" in r.stdout                          # 다음 단계(S7) 안내
    assert _owners(tmp_path, shared) == TREE
    sid = (shared / "dms" / ".dms-shared-id").read_text()
    assert re.fullmatch(r"[0-9a-f]{32}\n", sid), sid
    assert (shared / "dms" / "registry" / ".dms-registry-id").read_text() == sid
    for n in range(1, 6):
        assert (shared / "dms" / "pgdata" / f"dms-pgdata-0{n}" / ".dms-pv-id").read_text() == f"dms-pgdata-0{n}\n"
    # 2026-11-03 ⑤: pg_dump CronJob 이 덤프 전에 검사만 하는 센티널(값은 deploy/platform/db/backup/cronjob.yaml 의 DMS_BACKUP_SENTINEL 과 같다).
    assert (shared / "dms" / "backup" / "pg" / ".dms-backup-id").read_text() == "dms-backup-pg\n"
    before = _listing(shared)

    again = _run(tmp_path, str(shared))
    assert again.returncode == 0 and "MADE" not in again.stdout, (again.stdout, again.stderr)
    assert again.stdout.count("이미 있음") == len(TREE) + 1 + 1 + 5 + 1  # 트리 + shared-id + registry-id + pv-id ×5 + backup-id
    assert (shared / "dms" / ".dms-shared-id").read_text() == sid and _listing(shared) == before
    check = _run(tmp_path, "--check", str(shared))
    assert check.returncode == 0 and "MADE" not in check.stdout, check.stdout


@needs_root_or_fakeroot
def test_check_mode_creates_nothing_and_fails_on_missing(tmp_path):
    shared = _shared(tmp_path)
    r = _run(tmp_path, "--check", str(shared))
    assert r.returncode == 1 and "RESULT: FAIL" in r.stdout and "dms 이 없다" in r.stdout, (r.stdout, r.stderr)
    assert os.listdir(shared) == []


@needs_root_or_fakeroot
def test_existing_mismatch_fails_and_is_not_fixed(tmp_path):
    shared = _shared(tmp_path)
    assert _run(tmp_path, str(shared)).returncode == 0
    _as_root(tmp_path, "chmod", "0775", str(shared / "dms" / "artifacts"))   # g+w -- 3홉이 거부하는 모양
    (shared / "dms" / "registry" / ".dms-registry-id").write_text("someone-else\n")
    r = _run(tmp_path, str(shared))
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert "dms/artifacts 모양이 다르다" in r.stdout and "고치지 않는다" in r.stdout
    assert "dms/registry/.dms-registry-id 값이 다르다" in r.stdout
    assert _owners(tmp_path, shared)["dms/artifacts"] == (0, 0, 0o775)  # 고치지 않았다
    assert (shared / "dms" / "registry" / ".dms-registry-id").read_text() == "someone-else\n"


@needs_root_or_fakeroot
def test_existing_dms_with_an_allowed_alternative_mode_passes(tmp_path):
    # 기존 규칙: 공용 디렉터리 750 또는 700, base 755 또는 711 -- 이미 있으면 그 범위는 통과(고치지도 않는다).
    shared = _shared(tmp_path)
    assert _run(tmp_path, str(shared)).returncode == 0
    _as_root(tmp_path, "chmod", "0700", str(shared / "dms"))
    _as_root(tmp_path, "chmod", "0711", str(shared / "dms" / "artifacts"))
    r = _run(tmp_path, str(shared))
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert _owners(tmp_path, shared)["dms"] == (0, 0, 0o700)


@needs_root_or_fakeroot
def test_symlinks_are_never_followed(tmp_path):
    shared = _shared(tmp_path)
    elsewhere = tmp_path / "local-disk"
    elsewhere.mkdir()
    (shared / "dms").mkdir(mode=0o750)
    os.symlink(elsewhere, shared / "dms" / "artifacts")                  # base 를 공용 스토리지 밖으로 돌리는 링크
    r = _run(tmp_path, str(shared))
    assert r.returncode == 1 and "dms/artifacts 이 심링크다" in r.stdout, (r.stdout, r.stderr)
    assert os.listdir(elsewhere) == []


@needs_root_or_fakeroot
def test_registry_sentinel_without_shared_id_is_not_guessed(tmp_path):
    shared = _shared(tmp_path)
    assert _run(tmp_path, str(shared)).returncode == 0
    (shared / "dms" / ".dms-shared-id").unlink()
    r = _run(tmp_path, str(shared))
    assert r.returncode == 1 and "추측하지 않는다" in r.stdout, (r.stdout, r.stderr)
    assert not (shared / "dms" / ".dms-shared-id").exists()
