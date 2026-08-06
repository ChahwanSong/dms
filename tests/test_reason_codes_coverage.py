"""I2: 프론트 reasonCodes.test.ts의 BACKEND_CODES는 사람이 유지하는 목록이라,
백엔드에 새 사유 코드를 추가해도 아무것도 빨간불이 되지 않는다 -- 이 슬라이스
자신의 회귀(I1, invalid_actor)가 바로 그 증거였다.

여기서는 반대 방향 가드를 둔다: src/dms/를 AST로 훑어 실제로 쓰이는 사유 코드
리터럴을 뽑고, 체크인된 목록(frontend/src/lib/reasonCodes.json)에 전부 있는지
확인한다. 새 코드를 추가하면서 목록 갱신을 빠뜨리면 여기서 빨간불이 된다.

목록을 프론트/백엔드 두 파일에 따로 두지 않고 reasonCodes.json 하나로 합친
이유: "목록이 두 개면 한쪽만 갱신됐을 때 아무도 못 잡는다"는 지적이 애초에
reasonCodes.test.ts의 결함이었다. 파일을 하나로 만들면 그 어긋남 자체가
구조적으로 발생할 수 없다 -- 프론트 커버리지 테스트(reasonCodes.test.ts)와 이
추출 테스트가 같은 파일을 읽으므로, 코드를 추가하고 이 JSON을 안 고치면 반드시
둘 중 하나(대개 이 테스트)가 빨간불이 된다.

추출 대상(리뷰어가 실제로 쓴 방법과 동일):
  - HTTPException(..., detail="...")
  - set_state/set_job_state/set_item_status(..., reason_code="...")
  - f"prefix:{...}" 형태의 복합 reason_code -- 접두만 추출한다
  - 예외 생성자 리터럴: IdentityRejected("..."), DomainValidationError("..."),
    ExecutionError("..."), PlacementError("...")

정적 추출은 변수를 경유하는 코드(예: 헬퍼 함수에 리터럴을 넘기고 그 헬퍼가
raise하는 경우)까지는 못 잡는다 -- 그건 이 테스트의 목적이 아니다(완전한 정적
분석은 문자열 조립 때문에 애초에 불가능하다는 것이 설계 문서의 전제다). 목적은
"리뷰어가 처음 발견한 것과 같은 종류의 누락"이 다시 생기면 잡는 것이다.
"""
import ast
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src" / "dms"
REASON_CODES_FILE = REPO_ROOT / "frontend" / "src" / "lib" / "reasonCodes.json"

_EXCEPTION_CTORS = {"IdentityRejected", "DomainValidationError", "ExecutionError",
                    "PlacementError"}
_LITERAL_KEYWORDS = {"detail", "reason_code"}


def _const_str(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _joinedstr_prefix(node: ast.JoinedStr) -> str | None:
    """f"prefix:{...}" 의 리터럴 시작 부분에서 ':' 앞 접두만 뽑는다.
    stepper.py가 f"{prefix}:{exc.reason_code}" 형태의 합성 코드를 만드는 자리다."""
    if not node.values:
        return None
    first = node.values[0]
    if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
        return None
    text = first.value
    if ":" not in text:
        return None
    prefix = text.split(":", 1)[0]
    return prefix or None


def _callee_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def extract_reason_code_literals(source: str) -> set[str]:
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _callee_name(node)
        if name in _EXCEPTION_CTORS and node.args:
            literal = _const_str(node.args[0])
            if literal:
                found.add(literal)
        for kw in node.keywords:
            if kw.arg not in _LITERAL_KEYWORDS:
                continue
            literal = _const_str(kw.value)
            if literal:
                found.add(literal)
                continue
            if isinstance(kw.value, ast.JoinedStr):
                prefix = _joinedstr_prefix(kw.value)
                if prefix:
                    found.add(prefix)
    return found


def test_reason_codes_json_exists_and_is_a_nonempty_list():
    assert REASON_CODES_FILE.is_file(), (
        f"{REASON_CODES_FILE} 가 없다 -- 프론트/백엔드가 공유하는 사유 코드 목록이다")
    codes = json.loads(REASON_CODES_FILE.read_text())
    assert isinstance(codes, list) and codes


def test_every_extracted_reason_code_literal_is_in_the_checked_in_list():
    known = set(json.loads(REASON_CODES_FILE.read_text()))
    missing_by_file: dict[str, list[str]] = {}
    for path in sorted(SRC_DIR.rglob("*.py")):
        literals = extract_reason_code_literals(path.read_text())
        missing = sorted(literals - known)
        if missing:
            missing_by_file[str(path.relative_to(REPO_ROOT))] = missing
    assert missing_by_file == {}, (
        "src/dms/에 새 사유 코드 리터럴이 있는데 "
        f"{REASON_CODES_FILE.relative_to(REPO_ROOT)} 에 없다: {missing_by_file}\n"
        "frontend/src/lib/reasonCodes.json (및 필요하면 REASON_MESSAGES 한국어 매핑)"
        "을 갱신해라.")
