"""배치 단위 삭제 상한의 포탈 거울(2026-10-26 사용자 결정: 작업 1000 → 2000, 항목 10000 그대로). 포탈은 상한을 넘는 배치를
선택 불가로 미리 잠그고(features/jobs/batchGroups.ts) 사유 문구(api.ts batch_delete_too_large)에 숫자를 쓴다 -- 서버 상수만 바꾸면
화면은 「1000개 초과」로 고를 수 없게 막거나(서버는 지울 수 있는데) 문구가 다른 숫자를 말한다. 세 곳을 한 테스트로 묶는다."""
import re
from pathlib import Path

from dms.repositories.request_purges import MAX_BATCH_DELETE_CHILDREN, MAX_BATCH_DELETE_ITEMS

ROOT = Path(__file__).resolve().parent.parent


def _const(text: str, name: str) -> int:
    m = re.search(rf"export const {name} = ([0-9_]+);", text)
    assert m, name
    return int(m.group(1).replace("_", ""))


def test_user_decided_limits():
    assert MAX_BATCH_DELETE_CHILDREN == 2000 and MAX_BATCH_DELETE_ITEMS == 10000


def test_portal_constants_mirror_the_server():
    text = (ROOT / "frontend/src/features/jobs/batchGroups.ts").read_text()
    assert _const(text, "MAX_BATCH_CHILDREN") == MAX_BATCH_DELETE_CHILDREN
    assert _const(text, "MAX_BATCH_ITEMS") == MAX_BATCH_DELETE_ITEMS


def test_reason_message_names_the_same_numbers():
    text = (ROOT / "frontend/src/lib/api.ts").read_text()
    m = re.search(r'batch_delete_too_large: "([^"]*)"', text)
    assert m
    assert f"{MAX_BATCH_DELETE_CHILDREN}개" in m.group(1) and f"{MAX_BATCH_DELETE_ITEMS}개" in m.group(1)
