"""경로 봉쇄 재확인(dms-k8s) — 런처(dsync·dscan·drm)와 nsync rank 가 같이 쓰는 **규칙 하나**의 Python 미러.

execution_manifests._PATH_CONTAINED_FN(preflight·exec_preflight 셸 `dms_path_contained PATH ROOT`)과 **같은 규칙**이다 — 둘이
갈라지면 한쪽만 통과하는 경로가 생긴다(tests/test_sync_containment_sh.py·test_pv_containment_sh.py·test_rm_containment_sh.py 가
같은 트리로 두 판정을 대조한다). 규칙(path_redirected) = 「경로의 실제 위치 == realpath(managed_root) + 글자 그대로의 상대경로」 —
managed_root 아래 **어느 성분도**(끝 포함) 심링크가 아니라는 뜻이다. 아직 없는 끝부분(새 목적지)은 있는 가장 깊은 상위로 본다.
PV 주소 쪽은 여기에 「PV 루트 아래」 글자 경계(within, 셸 dms_within)만 더한다(entry_redirected). 2026-10-26 의 PV 전용 규칙
(pv_path_redirected — 없는 끝은 부모 하나만)은 2026-10-28 에 이 하나로 접었다(사용자 결정 「한 규칙으로 통일」). rm 은 끝 성분 링크를
허용한다(rm_target_redirected — drm 은 링크 자체만 지운다, 2026-10-28 사용자 결정 「rm 끝 링크 허용 유지」).

왜 실행 단계에서도 보나: preflight 파드와 실행 vcjob 은 다른 파드라 그 사이(Volcano 대기는 길 수 있다)에 사용자가 경로 성분을
링크로 바꿀 수 있다. 모든 잡이 root 라 커널 DAC 가 없다 — 경로가 다른 곳을 가리키면 root 도구가 남의 디렉터리·남의 PV 에 쓰거나
지운다(포크 보고 ④ C12·C13).

env DMS_JR_CONTAIN = JSON [[쪽, 경로, 경계, 기준 managed_root], …](execution_manifests.contain_entries) — sync 는 PV 든 직접 경로든
두 쪽 다(직접 경로 쪽은 경계 = 기준 — within 이 봉쇄에 포함된다), scan 은 PV 주소일 때만. 키 부재 = 봉쇄 대상이 아닌 잡(직접 경로
scan)·이 검사 전의 제어면 — 종전대로, 모양이 틀리면 막는다(fail-closed). 막으면 쪽별 사유(REASON_BY_SIDE) 마커를 찍는다.

nsync rank: 런처는 아티팩트 base 만 마운트해 데이터 경로를 볼 수 없다. 그래서 rank.sh 가 도구 exec 직전에
`python3 -m dms_job_runner.contain <소스 rank 수> <DMS_JR_CONTAIN>` 을 부르고, OMPI_COMM_WORLD_RANK < 소스 rank 수면 소스 쪽,
아니면 목적지 쪽 항목만 본다(nsync 파드는 자기 쪽 스토리지만 마운트한다 — rank 번호와 역할의 대응은 commands.nsync_role_map
과 같다). 막으면 마커를 stdout 에 찍고 1 로 끝난다 — mpirun 이 rank 출력을 런처로 모으고, 런처가 마커를 파드 로그로 다시
찍어 stepper 가 사유로 승격한다. 한 rank 가 MPI 초기화 전에 끝나면 mpirun 이 잡 전체를 끊으므로 다른 rank 의 도구도 데이터를
만지기 전에 멈춘다.
"""
import json
import os
import sys

# 제어면(stepper)이 승격하는 실행 실패 마커 -- execution_manifests.EXECUTION_REASON_MARKER·EXECUTION_REASONS 와 같은 값(dms 를
# import 하지 않는 독립 패키지라 중복 정의, 계약 테스트가 잇는다). runner 가 이 이름들을 그대로 다시 내보낸다.
EXECUTION_REASON_MARKER = "DMS_EXEC_REASON="
TARGET_PATH_REDIRECTED = "target_path_redirected"            # rm·PV scan 대상(쪽 "target")
SOURCE_PATH_REDIRECTED = "source_path_redirected"            # sync 소스(2026-10-28 -- PV·직접 경로 공통)
DESTINATION_PATH_REDIRECTED = "destination_path_redirected"  # sync 목적지
REASON_BY_SIDE = {"source": SOURCE_PATH_REDIRECTED, "destination": DESTINATION_PATH_REDIRECTED,
                  "target": TARGET_PATH_REDIRECTED}
# 마커 줄 전체(런처가 nsync rank 출력에서 이 줄만 끌어올린다 -- runner 7b). 쪽을 모르면(모양이 틀린 env) target 코드다.
REDIRECT_LINES = frozenset(f"{EXECUTION_REASON_MARKER}{code}" for code in REASON_BY_SIDE.values())
# 진단 줄 접두. 2026-10-28 전 러너는 "DMS_JR_PV_PATH_REDIRECTED"(sync 도 target 코드)를 찍었다 -- 배포를 건너 그 잡 이미지로 도는
# 잡의 사유는 제어면이 그 줄의 side 로 다시 가른다(execution_manifests.parse_execution_reason).
DETAIL_PREFIX = "DMS_JR_PATH_REDIRECTED"


def _realpath_strict(path):
    return os.path.realpath(path, strict=True)


def _strip_slash(p: str) -> str:
    return p[:-1] if p.endswith("/") else p     # 셸 "${X%/}" 와 같다(끝 슬래시 하나만)


def path_redirected(path, root, *, realpath=_realpath_strict, lexists=os.path.lexists) -> bool:
    """path 가 managed_root(root) 아래 **어느 성분도**(마지막 포함) 심링크 없이 그 자리에 있는가의 반대(True = 막는다).

    execution_manifests._PATH_CONTAINED_FN 의 미러(dms-ssc d191 runner.path_redirected 와 같은 판정). 판정 = 「실제 위치 ==
    realpath(root) + 글자 그대로의 상대경로」. 아직 없는 끝부분은 존재하는 가장 깊은 상위로 본다(lstat 기준 -- 끊긴 링크도
    「있음」이라 막힌다). root 자신·그 위의 심링크는 괜찮다(양쪽을 같은 realpath 로 본다 -- 관리 루트 링크·마운트 경로 링크).
    빈 root, 끝이 「/」「.」「..」거나 「//」「/./」「/../」가 낀 경로(DB 변조 행), 줄바꿈이 든 경로(아래), root 밖 경로, 실제
    경로를 못 구하는 경우(없는 root·고리 등)는 막는다(fail-closed).

    줄바꿈(2026-10-28 리뷰): 셸 미러는 $(dirname …)·$(realpath …) 명령 치환이 끝 줄바꿈을 지워 「x\\n」 성분을 옆의 실제 「x」로
    읽는다 -- 링크 「x\\n」 아래 경로가 셸에선 통과하고(fail-open) 실제 디렉터리 「data\\n」은 셸에서만 막혔다. 두 층이 같은 판정을
    내도록 **양쪽 다** 줄바꿈이 든 경로를 막는다(셸 dms_path_contained 의 case). 제출은 제어 문자를 unsafe_path 로 거부하므로
    (domain.validate_relative_path) 이 줄에 닿는 것은 그 검증 전의 행·변조 행뿐이다."""
    if not isinstance(path, str) or not isinstance(root, str) or root == "":
        return True
    if (path.endswith("/") or path.endswith("/.") or path.endswith("/..")
            or "//" in path or "/./" in path or "/../" in path or "\n" in path):
        return True
    base = _strip_slash(root)
    if not (path == base or path.startswith(base + "/")):
        return True
    try:
        real_root = realpath(root)
    except (OSError, ValueError):
        return True
    q = path
    while q != base and not lexists(q):
        q = os.path.dirname(q)
    want = real_root if q == base else f"{_strip_slash(real_root)}/{q[len(base) + 1:]}"
    try:
        real_q = realpath(q)
    except (OSError, ValueError):
        return True
    return real_q != want


def within(path, bound) -> bool:
    """path 가 글자로 bound(PV 루트) 자신이거나 그 아래인가 -- 셸 dms_within 의 미러. 빈 bound 는 거짓(fail-closed)."""
    if not isinstance(path, str) or not isinstance(bound, str) or bound == "":
        return False
    b = _strip_slash(bound)
    return path in (bound, b) or path.startswith(b + "/")


def entry_redirected(path, bound, anchor, *, realpath=_realpath_strict, lexists=os.path.lexists) -> bool:
    """DMS_JR_CONTAIN 한 항목의 판정(True = 막는다): 봉쇄(path_redirected, 기준 anchor = 그 쪽 managed_root) + 글자 경계
    (within, bound = PV 루트 -- 직접 경로 쪽은 bound == anchor 라 봉쇄에 이미 포함된다). 기준점이 PV 루트가 아니라 managed_root 인
    이유: PV 루트를 기준으로 realpath 를 맞추면 managed_root 와 PV 루트 **사이** 성분(PV 루트 자신 포함)이 링크여도 양쪽이 같이
    따라가 통과한다(2026-10-26 2차 보안 리뷰)."""
    return path_redirected(path, anchor, realpath=realpath, lexists=lexists) or not within(path, bound)


def rm_target_redirected(target, root, *, realpath=_realpath_strict, lexists=os.path.lexists) -> bool:
    """drm 대상 봉쇄 재확인(True = 막는다) -- execution_manifests._RM_CONTAINMENT_CHECK 와 같은 규칙.

    대상의 **상위 경로**에 path_redirected(같은 규칙 하나)를 쓰고 대상 자신(끝 성분)은 링크여도 된다: drm 은 대상을 lstat 해
    링크 자체만 지우고 걷는 동안에도 링크를 따라가지 않는다(2026-10-21 d183 실증, 2026-10-28 사용자 결정 「rm 끝 링크 허용 유지」
    -- dms-ssc d191 은 끝 링크도 거부한다). 끝이 「/」「.」「..」거나 「//」「/./」「/../」가 낀 대상(끝 링크를 따라가게 되거나 상위를
    지우게 된다), 줄바꿈이 든 대상(셸의 parent=$(dirname …) 가 끝 줄바꿈을 지운다 -- path_redirected docstring; 끝 성분만이어도
    막는다 -- 셸이 대상 전체를 본다), managed_root 자신(stepper 는 그 아래만 조합한다 -- 변조 행)은 막는다. target 은 drm argv
    마지막(tool_argv), root 는 env DMS_JR_RM_ROOT(stepper 가 target 을 조합한 값). 왜 러너도 보나: exec_preflight 파드와 실행
    vcjob 사이에 상위 디렉터리를 링크로 바꾸면 root drm 이 다른 트리를 지운다 -- 여기는 mpirun 바로 앞이다(남는 창은 BACKLOG §4)."""
    if not isinstance(target, str) or not isinstance(root, str) or root == "":
        return True
    if (target.endswith("/") or target.endswith("/.") or target.endswith("/..")
            or "//" in target or "/./" in target or "/../" in target or "\n" in target):
        return True
    if target in (root, _strip_slash(root)):
        return True
    return path_redirected(os.path.dirname(target), root, realpath=realpath, lexists=lexists)


def parse_entries(raw) -> list:
    """DMS_JR_CONTAIN → [(쪽, 경로, 경계, 기준)]. JSON 이 아니거나 모양이 틀리면 ValueError(호출자가 막는다)."""
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("DMS_JR_CONTAIN is not a list")
    out = []
    for entry in data:
        if not (isinstance(entry, list) and len(entry) == 4 and all(isinstance(v, str) for v in entry)):
            raise ValueError("bad DMS_JR_CONTAIN entry")
        out.append(tuple(entry))
    return out


def first_blocked(entries, *, side=None, redirected=entry_redirected):
    """막히는 첫 항목(없으면 None). side 가 있으면 그 쪽 항목만 본다(nsync rank)."""
    for entry in entries:
        if side is not None and entry[0] != side:
            continue
        if redirected(entry[1], entry[2], entry[3]):
            return entry
    return None


def reason_for(side) -> str:
    """쪽 → 사유 코드. 모르는 쪽(모양이 틀린 env 의 "?")은 target 코드(가장 넓은 문구)."""
    return REASON_BY_SIDE.get(side, TARGET_PATH_REDIRECTED)


def report_lines(entry) -> str:
    side, path, bound, anchor = entry
    return (f"{EXECUTION_REASON_MARKER}{reason_for(side)}\n"
            f"{DETAIL_PREFIX} side={side!r} path={path!r} root={anchor!r} bound={bound!r}\n")


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
