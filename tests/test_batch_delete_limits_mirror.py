"""배치 단위 삭제 상한과 포탈(2026-11-16 작업 2000 → 5000 · 2026-11-19 사용자 결정 「상한 기본 50000」 + 「상한 값도 삭제 시
보여지고 넘으면 삭제 불가 메시지」).

상한은 이제 설정 키(DMS_BATCH_DELETE_MAX_CHILDREN, 기본 50000)라 사이트마다 다를 수 있다 -- 포탈은 상한을 상수로 들지 않고 서버가
싣는 값만 쓴다(정리 현황·미리보기의 limits = routes_request_purge.delete_limits, 목록 행의 batch_request_count_limit·
batch_item_count_limit). 그래서 **거울이 없어야** 한다: batchGroups.ts 에 배치당·호출 합 상수가 없고, api.ts 의 상한 사유 문구에
숫자가 없다(숫자는 lib/deleteLimits.ts blockText 가 서버 값으로 끼운다). 남는 거울은 호출당 단건 200·배치 10(옛 서버 대비 기본값 --
서버 limits 가 있으면 그 값)."""
import re
from pathlib import Path

from dms.api.routes_request_purge import MAX_DELETE, MAX_DELETE_BATCHES, delete_limits
from dms.config import Settings
from dms.repositories.request_purges import (BATCH_LIST_COUNT_CAP, DELETE_CHUNK, MAX_BATCH_DELETE_CHILDREN_CEILING,
                                             MAX_BATCH_DELETE_ITEMS, MAX_DELETE_CALL_CHILDREN, SYNC_CALL_FACTOR)

ROOT = Path(__file__).resolve().parent.parent
GROUPS = ROOT / "frontend/src/features/jobs/batchGroups.ts"


def _const(text: str, name: str) -> int:
    m = re.search(rf"export const {name} = ([0-9_]+);", text)
    assert m, name
    return int(m.group(1).replace("_", ""))


def test_user_decided_limits():
    s = Settings(database_url="x", shared_token="t", admin_token="a", session_secret="s")
    assert (s.batch_delete_max_children, s.batch_delete_sync_max_children) == (50000, 500)
    assert (MAX_BATCH_DELETE_CHILDREN_CEILING, MAX_BATCH_DELETE_ITEMS, MAX_DELETE_CALL_CHILDREN) == (50000, 10000, 100000)
    assert (BATCH_LIST_COUNT_CAP, SYNC_CALL_FACTOR, DELETE_CHUNK) == (5000, 2, 100)
    # 배치 하나는 늘 호출 예산 안에 들어간다(within_call_budget 의 「첫 배치는 늘 담는다」 전제 -- 설정 상한의 끝까지)
    assert MAX_BATCH_DELETE_CHILDREN_CEILING <= MAX_DELETE_CALL_CHILDREN


def test_module_constant_moved_to_settings():
    # 모듈 상수를 남겨 두면 설정과 갈린다(2026-11-19 -- 설정 키로 옮겼다).
    import dms.repositories.request_purges as rp
    assert not hasattr(rp, "MAX_BATCH_DELETE_CHILDREN")


def test_delete_limits_shape():
    s = Settings(database_url="x", shared_token="t", admin_token="a", session_secret="s")
    assert delete_limits(s) == {"requests_per_call": 200, "batches_per_call": 10, "batch_request_count": 50000,
                                "batch_item_count": 10000, "call_request_count": 100000, "sync_request_count": 500,
                                "list_count_cap": 5000}
    low = Settings(database_url="x", shared_token="t", admin_token="a", session_secret="s",
                   batch_delete_max_children=20, batch_delete_sync_max_children=0)
    assert delete_limits(low)["batch_request_count"] == 20 and delete_limits(low)["list_count_cap"] == 20
    assert delete_limits(low)["sync_request_count"] == 0
    assert (MAX_DELETE, MAX_DELETE_BATCHES) == (200, 10)


def test_portal_has_no_limit_constants():
    text = GROUPS.read_text()
    for name in ("MAX_BATCH_CHILDREN", "MAX_BATCH_ITEMS", "MAX_DELETE_CALL_CHILDREN"):
        assert not re.search(rf"\b(const|let|var)\s+{name}\b", text), f"batchGroups.ts 에 {name} 거울이 다시 생겼다"
    # 판정은 행의 서버 값으로
    assert "batch_request_count_limit" in text and "batch_item_count_limit" in text


def test_portal_call_mirrors_match_the_server():
    # 옛 서버(limits 없음) 대비 기본값 -- 서버 limits 가 있으면 그 값을 쓴다.
    text = GROUPS.read_text()
    assert _const(text, "MAX_DELETE_BATCHES") == MAX_DELETE_BATCHES


def test_reason_messages_have_no_setting_numbers():
    text = (ROOT / "frontend/src/lib/api.ts").read_text()
    for code in ("batch_delete_too_large", "batch_delete_too_many_items", "batch_delete_call_budget"):
        m = re.search(rf'{code}: "([^"]*)"', text)
        assert m, code
        assert not re.search(r"[0-9]", m.group(1)), f"{code} 문구에 설정값 숫자가 박혔다: {m.group(1)}"
