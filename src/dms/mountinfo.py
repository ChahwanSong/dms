"""/proc/<pid>/mountinfo 파싱(중립 모듈, 2026-11-23).

agent/probes.py 에 있던 것을 그대로 옮겼다 -- 제어면(api·controller)의 아티팩트 base 파일시스템 진단
(artifact_base.covering_fstype)도 같은 파서를 쓰는데, 제어면이 dms.agent 를 임포트하면 에이전트 전용 의존(shutil 류
프로브)이 제어면 프로세스로 들어온다(strip_scheme 을 artifact_base 로 승격한 것과 같은 이유). agent.probes 가 이 모듈의
이름을 재수출하므로 기존 임포트 경로·동작은 그대로다.
"""

_OCTAL_ESCAPES = {"\\040": " ", "\\011": "\t", "\\012": "\n", "\\134": "\\"}


def _unescape(field: str) -> str:
    for escaped, char in _OCTAL_ESCAPES.items():
        field = field.replace(escaped, char)
    return field


def parse_mountinfo(text: str) -> set[str]:
    points: set[str] = set()
    for line in text.splitlines():
        fields = line.split()
        if len(fields) > 4:
            points.add(_unescape(fields[4]))
    return points


def parse_mount_table(text: str) -> dict:
    """mountinfo → {mount_point: {"opts", "fstype", "sb_opts"}}. 같은 지점에 여러
    마운트(overmount)면 나중 줄(위에 쌓인 것)이 이긴다 -- 커널이 그 순서로 나열한다.
    옵션은 writable 판정(_mount_rw)에 쓴다: 6열은 per-mount 옵션(바인드 ro 등),
    '-' 뒤 세 번째는 슈퍼블록 옵션(fs 전체 ro)이다."""
    table: dict = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 6:
            continue
        fstype = sb_opts = ""
        if "-" in fields[6:]:
            sep = fields.index("-", 6)
            rest = fields[sep + 1:]
            fstype = rest[0] if rest else ""
            sb_opts = rest[2] if len(rest) > 2 else ""
        table[_unescape(fields[4])] = {"opts": fields[5], "fstype": fstype,
                                       "sb_opts": sb_opts}
    return table


def covering_mount(table, path):
    """path 를 덮는 가장 깊은 마운트 지점(없으면 None). 아티팩트 base 처럼 마운트
    지점 아래 하위 디렉터리의 쓰기 가능 여부는 그 마운트의 옵션이 정한다."""
    best = None
    for point in table:
        if path == point or path.startswith(point.rstrip("/") + "/"):
            if best is None or len(point) > len(best):
                best = point
    return best
