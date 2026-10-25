"""k8s 인벤토리 배선(dms-k8s 설계 §4·§9 슬라이스 1): 설정 키·하한, 컨트롤러 리스 holder(HOSTNAME), 컨트롤러 루프·보존의
실제 동작, 봉인 용도의 서버·프런트 일치, 사유 코드 양쪽 등록."""
import json
from pathlib import Path

import pytest

from dms import k8s_sync
from dms.api import password_transport as pt
from dms.cli import controller_holder
from dms.config import Settings, SettingsError
from dms.controller import build_loops, run_all_once
from dms.db import dump_json, iso_plus, utc_now_iso
from dms.repositories import Repositories

ROOT = Path(__file__).resolve().parent.parent
VALID = {"DMS_DATABASE_URL": "sqlite:///x.db", "DMS_SHARED_TOKEN": "t", "DMS_ADMIN_TOKEN": "a",
         "DMS_SESSION_SECRET": "s"}
SECTION_4_2 = (
    "k8s_sync_dns_failed k8s_sync_unreachable k8s_sync_tls_failed k8s_sync_unauthorized k8s_sync_forbidden "
    "k8s_sync_timeout k8s_sync_list_expired k8s_sync_too_large k8s_sync_bad_response k8s_sync_expired "
    "k8s_sync_no_node k8s_sync_token_missing k8s_sync_token_unreadable k8s_sync_apply_failed k8s_cluster_exists "
    "k8s_cluster_not_found invalid_k8s_cluster_name invalid_k8s_api_server k8s_ca_invalid k8s_token_required "
    "invalid_k8s_sync_settings k8s_node_unknown storage_class_not_found storage_not_pv_capable "
    "invalid_pv_path_template storage_has_sc_mappings denylist_group_unsupported").split()


def test_k8s_settings_defaults_and_env_overrides():
    s = Settings.from_env(VALID)
    assert (s.k8s_sync_interval_seconds, s.k8s_sync_poll_seconds, s.k8s_sync_run_deadline_seconds,
            s.k8s_sync_page_limit, s.k8s_sync_max_bytes, s.k8s_inventory_interval_seconds,
            s.k8s_sync_run_retention_days) == (300, 15, 120, 500, 33554432, 10, 30)
    s = Settings.from_env({**VALID, "DMS_K8S_SYNC_INTERVAL_SECONDS": "600", "DMS_K8S_SYNC_POLL_SECONDS": "5",
                           "DMS_K8S_SYNC_RUN_DEADLINE_SECONDS": "60", "DMS_K8S_SYNC_PAGE_LIMIT": "100",
                           "DMS_K8S_SYNC_MAX_BYTES": "2097152", "DMS_K8S_INVENTORY_INTERVAL_SECONDS": "3",
                           "DMS_K8S_SYNC_RUN_RETENTION_DAYS": "7"})
    assert (s.k8s_sync_interval_seconds, s.k8s_sync_poll_seconds, s.k8s_sync_run_deadline_seconds,
            s.k8s_sync_page_limit, s.k8s_sync_max_bytes, s.k8s_inventory_interval_seconds,
            s.k8s_sync_run_retention_days) == (600, 5, 60, 100, 2097152, 3, 7)


@pytest.mark.parametrize("key,value", [
    ("DMS_K8S_SYNC_INTERVAL_SECONDS", "59"), ("DMS_K8S_SYNC_POLL_SECONDS", "4"),
    ("DMS_K8S_SYNC_RUN_DEADLINE_SECONDS", "9"), ("DMS_K8S_SYNC_PAGE_LIMIT", "0"), ("DMS_K8S_SYNC_PAGE_LIMIT", "5001"),
    ("DMS_K8S_SYNC_MAX_BYTES", "1000"), ("DMS_K8S_INVENTORY_INTERVAL_SECONDS", "0"),
    ("DMS_K8S_SYNC_RUN_RETENTION_DAYS", "0"), ("DMS_K8S_SYNC_INTERVAL_SECONDS", "five")])
def test_k8s_settings_floors_refuse_startup(key, value):
    with pytest.raises(SettingsError) as e:
        Settings.from_env({**VALID, key: value})
    assert any(key in p for p in e.value.problems)


def test_controller_holder_includes_the_pod_name():
    # 컨테이너 PID 는 늘 1 -- 파드 이름이 없으면 롤아웃 중 겹친 두 컨트롤러가 같은 holder 라 리스가 무력했다
    a = controller_holder({"HOSTNAME": "dms-controller-7d9c-abcde"})
    b = controller_holder({"HOSTNAME": "dms-controller-7d9c-fghij"})
    assert a.startswith("controller-dms-controller-7d9c-abcde-") and a != b
    assert controller_holder({}).startswith("controller-")


def test_k8s_inventory_loop_and_retention_actually_run(db, settings):
    repos = Repositories(db)
    repos.k8s.create_cluster(name="c1", api_server="https://x:6443", tls_server_name=None, ca_pem=None,
                             insecure_skip_tls_verify=False, token_enc="v1:x", enabled=True, actor="ops")
    now = utc_now_iso()
    db.execute("""INSERT INTO k8s_sync_runs (run_id, cluster_name, node_name, role, trigger, status, claimed_at,
                  deadline_at, finished_at, config_rev, payload)
                  VALUES ('r1', 'c1', 'k8s-w1', 'primary', 'schedule', 'received', :t, :t, :t, 0, :p)""",
               {"t": now, "p": dump_json({"fingerprint": None, "data": {
                   "storage_classes": [], "pvs": [], "role_bindings": [],
                   "pvcs": [{"namespace": "n", "name": "p", "uid": None, "storage_class": None, "volume_name": None,
                             "phase": "Pending", "capacity": None, "access_modes": [], "created_at": None}]}})})
    loops = {loop.name: loop for loop in build_loops(settings, repos)}
    results = run_all_once([loops["k8s-inventory"]], repos, holder="h1")
    assert results == {"k8s-inventory": "ok"}
    assert repos.k8s.get_run("r1")["status"] == "applied" and repos.k8s.cluster_synced("c1")
    old = iso_plus(now, -60 * 86400)
    for i in range(55):
        db.execute("""INSERT INTO k8s_sync_runs (run_id, cluster_name, node_name, role, trigger, status, claimed_at,
                      deadline_at, config_rev) VALUES (:r, 'c1', 'n', 'primary', 'schedule', 'failed', :t, :t, 0)""",
                   {"r": f"old{i}", "t": old})
    db.execute("UPDATE k8s_sync_runs SET claimed_at = :t WHERE run_id = 'r1'", {"t": old})
    assert run_all_once([loops["retention"]], repos, holder="h1") == {"retention": "ok"}
    left = {r["run_id"] for r in db.query("SELECT run_id FROM k8s_sync_runs")}
    assert len(left) == 51 and "r1" in left                  # 최근 50건 + 마지막 성공


def test_seal_purpose_matches_the_frontend_exactly():
    assert "k8s_cluster_token" in pt.PURPOSES
    src = (ROOT / "frontend" / "src" / "lib" / "passwordTransport.ts").read_text()
    assert ('export type Purpose = "login" | "signup" | "password_reset" | "admin_create" | "mail_relay_token" | '
            '"k8s_cluster_token";') in src


def test_every_k8s_reason_is_registered_on_both_sides():
    codes = set(json.loads((ROOT / "frontend" / "src" / "lib" / "reasonCodes.json").read_text()))
    api = (ROOT / "frontend" / "src" / "lib" / "api.ts").read_text()
    wanted = set(SECTION_4_2) | k8s_sync.SYNC_REASONS | k8s_sync.STATUS_REASONS
    assert sorted(wanted - codes) == []
    missing_messages = [c for c in sorted(wanted) if f"\n  {c}: " not in api]
    assert missing_messages == []
    # 상태 사유는 상태 이름과 1:1(화면이 status_reason 으로 문구를 고른다)
    assert {k8s_sync.status_reason(s) for s in ("no_node", "token_missing", "token_unreadable")} == \
        k8s_sync.STATUS_REASONS
