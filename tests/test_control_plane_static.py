"""root 제어면 정적 그물(ARCHITECTURE §7 규칙 7·13, 2026-10-08 요청 삭제 정리로 고정).

- 규칙 7: api/controller(src/dms, agent 제외)는 셸을 띄우거나 os.access·chown·chmod 를 쓰지 않는다 -- 프로세스가 필요하면
  파드 스펙(purge 파드처럼). 문서가 "0건 -- 유지" 라고만 적고 그물이 없었다.
- 규칙 13(개정): 컨트롤러의 FS 변경은 request-purge 의 `.dms-trash` mkdir 과 `<base>/<job_id>` renameat 뿐이고, 실제
  삭제는 purge 파드가 한다 -- 정리 모듈(artifact_trash·request_purger·purge_runner)에 삭제 호출이 0건.
- 정리 모듈은 컨트롤러 전용 -- api 패키지가 import 하지 않는다(api 는 FS·k8s 를 만지지 않는다, 설계 D2).

AST 로 본다: 문자열(파드 스펙 셸 본문·docstring)의 'chown'·'rm' 은 코드가 아니다."""
import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "dms"

_FORBIDDEN_MODULES = {"subprocess", "shutil", "tempfile", "pty", "multiprocessing"}
_FORBIDDEN_OS = {"access", "chown", "fchown", "lchown", "chmod", "fchmod", "lchmod", "system", "popen",
                 "fork", "forkpty", "posix_spawn", "posix_spawnp", "execv", "execve", "execl", "execle", "execlp",
                 "execlpe", "execvp", "execvpe", "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve",
                 "spawnvp", "spawnvpe", "startfile"}
_DELETE_OS = {"unlink", "remove", "rmdir", "removedirs", "truncate", "ftruncate"}
_PURGE_MODULES = ("artifact_trash.py", "request_purger.py", "purge_runner.py")


def _control_plane_files():
    return [p for p in sorted(SRC.rglob("*.py")) if "agent" not in p.relative_to(SRC).parts]


def _offences(path, *, os_names, modules):
    tree = ast.parse(path.read_text())
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] in modules:
                    out.append(f"import {a.name}")
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".")[0]
            if node.level == 0 and mod in modules:
                out.append(f"from {node.module} import ...")
            if node.level == 0 and mod == "os":
                out.extend(f"from os import {a.name}" for a in node.names if a.name in os_names)
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "os":
            if node.attr in os_names:
                out.append(f"os.{node.attr}")
    return out


def test_control_plane_has_no_shell_access_chown_chmod():
    bad = {}
    for path in _control_plane_files():
        found = _offences(path, os_names=_FORBIDDEN_OS, modules=_FORBIDDEN_MODULES)
        if found:
            bad[str(path.relative_to(SRC))] = found
    assert bad == {}, f"규칙 7 위반(셸·os.access·chown·chmod는 파드 스펙으로): {bad}"


def test_purge_modules_never_delete_files_themselves():
    bad = {}
    for name in _PURGE_MODULES:
        path = SRC / name
        found = _offences(path, os_names=_FORBIDDEN_OS | _DELETE_OS, modules=_FORBIDDEN_MODULES)
        # pathlib 의 삭제 메서드(Path.unlink/rmdir)도 금지 -- 이 모듈들은 Path 를 쓸 이유가 없다.
        tree = ast.parse(path.read_text())
        found += [f".{n.attr}()" for n in ast.walk(tree)
                  if isinstance(n, ast.Attribute) and n.attr in {"unlink", "rmdir", "rmtree"}]
        if found:
            bad[name] = found
    assert bad == {}, f"규칙 13: 삭제는 purge 파드 몫이다: {bad}"


def test_api_does_not_import_the_purge_modules():
    bad = {}
    for path in sorted((SRC / "api").rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.ImportFrom):
                names = [node.module or ""] + [f"{node.module}.{a.name}" for a in node.names]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            for n in names:
                if any(m[:-3] in n.split(".") for m in _PURGE_MODULES):
                    bad.setdefault(str(path.relative_to(SRC)), []).append(n)
    assert bad == {}, f"정리 모듈은 컨트롤러 전용이다(api 는 FS·k8s 무접촉): {bad}"


def test_the_net_actually_catches_offences(tmp_path):
    # 그물이 비어 있지 않음을 증명 -- 위 테스트들이 「아무것도 못 보는」 상태로 초록이 되지 않게.
    p = tmp_path / "x.py"
    p.write_text("import os, subprocess\nfrom shutil import rmtree\nos.chmod('a', 0)\nos.unlink('b')\n"
                 "s = 'os.chown in a string is fine'\n")
    found = _offences(p, os_names=_FORBIDDEN_OS | _DELETE_OS, modules=_FORBIDDEN_MODULES)
    assert sorted(found) == sorted(["import subprocess", "from shutil import ...", "os.chmod", "os.unlink"])
