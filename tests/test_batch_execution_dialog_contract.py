"""포탈 실행 설정 변경 다이얼로그의 옵션 키 집합 == 서버 옵션 스펙(domain._OPTION_SPECS) 계약.

PATCH /api/admin/batches/{id}/execution 의 options 는 통째 교체다. 다이얼로그(BatchExecutionSettingsDialog.tsx)는 자기
키 집합(BOOLS/INTS/STRS) 밖의 저장 키를 "서버가 거부하는 옛 옵션"으로 보고 저장 때 뺀다 -- 이 판단은 두 집합이 같을
때만 참이다. 서버에 옵션을 새로 더하고 다이얼로그에 안 넣으면, 그 옵션이 든 배치를 다이얼로그로 저장하는 순간 옵션이
조용히 지워진다(그리고 화면은 "지원하지 않는 옵션"이라고 거짓말한다). 그래서 둘을 여기서 고정한다.
"""
import re
from pathlib import Path

from dms.domain import Operation, _OPTION_SPECS

REPO_ROOT = Path(__file__).resolve().parent.parent
DIALOG = REPO_ROOT / "frontend/src/features/batches/BatchExecutionSettingsDialog.tsx"


def _dialog_keys(const: str) -> dict[str, set[str]]:
    src = DIALOG.read_text()
    m = re.search(rf"const {const} = \{{(.*?)\}} as const;", src, re.S)
    assert m, f"{const} 정의를 못 찾음 -- 다이얼로그 구조가 바뀌면 이 테스트도 함께 고쳐라"
    out = {}
    for op in ("scan", "sync"):
        mm = re.search(rf"{op}: \[(.*?)\]", m.group(1), re.S)
        assert mm, (const, op)
        out[op] = set(re.findall(r'"([a-z_]+)"', mm.group(1)))
    return out


def test_dialog_option_keys_equal_server_option_specs():
    bools, ints, strs = _dialog_keys("BOOLS"), _dialog_keys("INTS"), _dialog_keys("STRS")
    for op, operation in (("scan", Operation.SCAN), ("sync", Operation.SYNC)):
        dialog = bools[op] | ints[op] | strs[op]
        assert dialog == set(_OPTION_SPECS[operation]), op
        # 종류도 맞아야 한다(bool 칸에 정수 옵션이 들어가면 체크박스가 true 만 싣는다)
        kinds = {k: rule[0] for k, rule in _OPTION_SPECS[operation].items()}
        assert {k for k, kind in kinds.items() if kind == "bool"} == bools[op], op
        assert {k for k, kind in kinds.items() if kind == "int"} == ints[op], op
        assert {k for k, kind in kinds.items() if kind in ("chmod", "chown")} == strs[op], op
