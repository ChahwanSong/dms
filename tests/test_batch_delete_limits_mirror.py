"""배치 단위 삭제 상한과 포탈(2026-11-16 사용자 결정: 작업 2000 → 5000, 항목 10000 그대로, 호출당 작업 합 10000 신설).

예전엔 포탈이 상한을 상수(batchGroups.ts MAX_BATCH_CHILDREN·MAX_BATCH_ITEMS)로 거울처럼 들고 있어, 서버 상수만 바꾸면 화면이 다른
숫자로 고를 수 없게 막았다. 이제 목록 응답이 행마다 서버 값(batch_request_count_limit·batch_item_count_limit)을 싣고 포탈은 그
값으로 판정한다 -- 상수 거울은 **없어야** 한다. 남는 거울: 호출당 배치 수(MAX_DELETE_BATCHES)·작업 합(MAX_DELETE_CALL_CHILDREN)과
두 사유 문구(batch_delete_too_large·batch_delete_call_budget) 속 숫자."""
import re
from pathlib import Path

from dms.api.routes_request_purge import MAX_DELETE_BATCHES
from dms.repositories.request_purges import (MAX_BATCH_DELETE_CHILDREN, MAX_BATCH_DELETE_ITEMS,
                                             MAX_DELETE_CALL_CHILDREN)

ROOT = Path(__file__).resolve().parent.parent
GROUPS = ROOT / "frontend/src/features/jobs/batchGroups.ts"


def _const(text: str, name: str) -> int:
    m = re.search(rf"export const {name} = ([0-9_]+);", text)
    assert m, name
    return int(m.group(1).replace("_", ""))


def test_user_decided_limits():
    assert (MAX_BATCH_DELETE_CHILDREN, MAX_BATCH_DELETE_ITEMS, MAX_DELETE_CALL_CHILDREN) == (5000, 10000, 10000)
    # 배치 하나는 늘 호출 예산 안에 들어간다(within_call_budget 의 「첫 배치는 늘 담는다」 전제)
    assert MAX_BATCH_DELETE_CHILDREN <= MAX_DELETE_CALL_CHILDREN


def test_portal_has_no_per_batch_limit_constants():
    text = GROUPS.read_text()
    for name in ("MAX_BATCH_CHILDREN", "MAX_BATCH_ITEMS"):
        assert not re.search(rf"\b(const|let|var)\s+{name}\b", text), f"batchGroups.ts 에 {name} 거울이 다시 생겼다"
    # 판정은 행의 서버 값으로
    assert "batch_request_count_limit" in text and "batch_item_count_limit" in text


def test_portal_call_mirrors_match_the_server():
    text = GROUPS.read_text()
    assert _const(text, "MAX_DELETE_BATCHES") == MAX_DELETE_BATCHES
    assert _const(text, "MAX_DELETE_CALL_CHILDREN") == MAX_DELETE_CALL_CHILDREN


def test_reason_messages_name_the_same_numbers():
    text = (ROOT / "frontend/src/lib/api.ts").read_text()
    m = re.search(r'batch_delete_too_large: "([^"]*)"', text)
    assert m
    assert f"{MAX_BATCH_DELETE_CHILDREN}개" in m.group(1) and f"{MAX_BATCH_DELETE_ITEMS}개" in m.group(1)
    m = re.search(r'batch_delete_call_budget: "([^"]*)"', text)
    assert m
    assert f"{MAX_DELETE_CALL_CHILDREN}개" in m.group(1)
