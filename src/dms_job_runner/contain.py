"""PV 주소 봉쇄 재확인(dms-k8s, 2026-10-26 보안 리뷰) — 런처(dsync·dscan)와 nsync rank 가 같이 쓴다.

execution_manifests._PV_CONTAIN_FN(preflight 셸)과 **같은 규칙**이다 — 둘이 갈라지면 한쪽만 통과하는 경로가 생긴다
(tests/test_pv_containment_sh.py 가 같은 트리로 대조). 왜 실행 단계에서도 보나: preflight 파드와 실행 vcjob 은 다른 파드라 그
사이(Volcano 대기는 길 수 있다)에 사용자가 경로 성분을 링크로 바꿀 수 있다(rm_target_redirected 와 같은 이유).

env DMS_JR_CONTAIN = JSON [[쪽, 경로, PV 루트, 기준 managed_root], …](execution_manifests.contain_entries). 키 부재 = PV 가 아닌
잡(종전대로), 모양이 틀리면 막는다(fail-closed).

nsync rank: 런처는 아티팩트 base 만 마운트해 데이터 경로를 볼 수 없다. 그래서 rank.sh 가 도구 exec 직전에
`python3 -m dms_job_runner.contain <소스 rank 수> <DMS_JR_CONTAIN>` 을 부르고, OMPI_COMM_WORLD_RANK < 소스 rank 수면 소스 쪽,
아니면 목적지 쪽 항목만 본다(nsync 파드는 자기 쪽 스토리지만 마운트한다 — rank 번호와 역할의 대응은 commands.nsync_role_map
과 같다). 막으면 마커를 stdout 에 찍고 1 로 끝난다 — mpirun 이 rank 출력을 런처로 모으고, 런처가 마커를 파드 로그로 다시
찍어 stepper 가 target_path_redirected 로 승격한다. 한 rank 가 MPI 초기화 전에 끝나면 mpirun 이 잡 전체를 끊으므로 다른
rank 의 도구도 데이터를 만지기 전에 멈춘다.
"""
import json
import os
import sys

# 제어면(stepper)이 승격하는 실행 실패 마커 -- execution_manifests.EXECUTION_REASON_MARKER·EXECUTION_REASONS 와 같은 값(dms 를
# import 하지 않는 독립 패키지라 중복 정의, 계약 테스트가 잇는다). runner 가 이 이름들을 그대로 다시 내보낸다.
EXECUTION_REASON_MARKER = "DMS_EXEC_REASON="
TARGET_PATH_REDIRECTED = "target_path_redirected"
REDIRECTED_LINE = f"{EXECUTION_REASON_MARKER}{TARGET_PATH_REDIRECTED}"
DETAIL_PREFIX = "DMS_JR_PV_PATH_REDIRECTED"


def _realpath_strict(path):
    return os.path.realpath(path, strict=True)


def _strip_slash(p: str) -> str:
    return p[:-1] if p.endswith("/") else p     # 셸 "${X%/}" 와 같다(끝 슬래시 하나만)


def pv_path_redirected(path, root, anchor, *, realpath=_realpath_strict, lexists=os.path.lexists) -> bool:
    """PV 주소 경로가 PV 루트 안(글자)이고 managed_root 부터 **심링크 성분 없이** 그 자리에 있는가의 반대(True = 막는다).

    경로가 있으면 realpath(경로) == realpath(managed_root) + 글자 그대로의 상대경로(마지막 성분이 링크여도 막는다 — dsync 소스·
    dscan 대상은 도구가 따라가고, 목적지가 링크면 그 너머에 쓴다), 없으면 부모로 같은 판정. 기준점이 PV 루트가 아니라
    managed_root 인 이유는 _PV_CONTAIN_FN 주석(PV 루트 자신이 링크인 경우). 실제 경로를 못 구하면(없는 상위·고리) 막는다."""
    if not all(isinstance(v, str) for v in (path, root, anchor)) or root == "" or anchor == "":
        return True
    if path.endswith("/") or path.endswith("/.") or path.endswith("/..") or "/" not in path:
        return True
    rbase = _strip_slash(root)
    if not (path in (root, rbase) or path.startswith(rbase + "/")):
        return True
    abase = _strip_slash(anchor)
    if path in (anchor, abase):
        rel = ""
    elif path.startswith(abase + "/"):
        rel = path[len(abase) + 1:]
    else:
        return True
    try:
        real_anchor = realpath(anchor)
        want = real_anchor if rel == "" else f"{_strip_slash(real_anchor)}/{rel}"
        if lexists(path):
            return realpath(path) != want
        return realpath(os.path.dirname(path)) != os.path.dirname(want)
    except (OSError, ValueError):
        return True


def parse_entries(raw) -> list:
    """DMS_JR_CONTAIN → [(쪽, 경로, PV 루트, 기준)]. JSON 이 아니거나 모양이 틀리면 ValueError(호출자가 막는다)."""
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("DMS_JR_CONTAIN is not a list")
    out = []
    for entry in data:
        if not (isinstance(entry, list) and len(entry) == 4 and all(isinstance(v, str) for v in entry)):
            raise ValueError("bad DMS_JR_CONTAIN entry")
        out.append(tuple(entry))
    return out


def first_blocked(entries, *, side=None, redirected=pv_path_redirected):
    """막히는 첫 항목(없으면 None). side 가 있으면 그 쪽 항목만 본다(nsync rank)."""
    for entry in entries:
        if side is not None and entry[0] != side:
            continue
        if redirected(entry[1], entry[2], entry[3]):
            return entry
    return None


def report_lines(entry) -> str:
    side, path, root, _anchor = entry
    return f"{REDIRECTED_LINE}\n{DETAIL_PREFIX} side={side!r} path={path!r} root={root!r}\n"


def rank_side(rank, source_ranks):
    """nsync rank 번호 → 역할(소스 rank 가 0..N-1, 목적지가 그 뒤 — commands.nsync_role_map). 모르면 None(막는다)."""
    if not isinstance(rank, str) or not rank.isdigit() or not isinstance(source_ranks, int) or source_ranks < 0:
        return None
    return "source" if int(rank) < source_ranks else "destination"


def main(argv=None, environ=None, out=None) -> int:
    """nsync rank 진입점(rank.sh) — 0 = 통과(도구 exec 로 진행), 1 = 막음(마커를 찍었다)."""
    argv = sys.argv[1:] if argv is None else argv
    environ = os.environ if environ is None else environ
    out = sys.stdout if out is None else out
    try:
        source_ranks = int(argv[0])
        entries = parse_entries(argv[1])
        side = rank_side(environ.get("OMPI_COMM_WORLD_RANK"), source_ranks)
        if side is None:
            raise ValueError("unknown rank")
        bad = first_blocked(entries, side=side)
    except (IndexError, ValueError, TypeError):
        bad = ("?", "?", "?", "?")
    if bad is None:
        return 0
    out.write(report_lines(bad))
    out.flush()
    return 1


if __name__ == "__main__":  # pragma: no cover - rank.sh 가 부른다
    sys.exit(main())
