import { describe, it, expect } from "vitest";
import { REASON_MESSAGES } from "./api";

// 백엔드가 실제로 낼 수 있는 사유 코드. 문자열 조립(f"prefix:{...}") 때문에 정적
// 추출이 완전할 수 없어 사람이 유지하는 목록을 둔다 -- 새 코드를 추가하면서 매핑을
// 빠뜨리면 이 테스트가 빨간불이 되는 것이 목적이다.
const BACKEND_CODES = [
  // planner / identity / placement
  "identity_denied", "ldap_not_configured", "ldap_unavailable",
  "ldap_identity_not_found", "missing_policy", "policy_disabled",
  "no_eligible_nodes", "no_ready_sync_candidate", "resource_conflict",
  "requester_disabled", "unsafe_path",
  // stepper / controller
  "orphan_recovery", "preflight_failed", "execution_failed", "empty_preview",
  "preview_timed_out", "preview_failed", "execution_recheck_failed",
  "preview_expired", "build_timeout", "build_failed",
  // 복합 접두
  "preflight_submit_failed", "execution_submit_failed",
  "preview_submit_failed", "execution_recheck_submit_failed",
  // HTTP detail
  "not_authenticated", "admin_required", "admin_token_required", "invalid_token",
  "account_exists", "invalid_username", "job_not_found", "batch_not_found",
  "batch_not_confirmable", "no_failed_items", "no_preview_fingerprint",
  "empty_batch", "invalid_batch_operation", "invalid_max_concurrency",
  "invalid_storage", "invalid_node_name", "agent_node_identity_mismatch",
  "terminate_failed", "invalid_job_id", "invalid_batch", "invalid_phase",
  "log_ref_not_found", "log_not_available", "artifact_not_found",
  "account_disabled", "maintenance_mode", "scan_admin_only",
  "privileged_not_authorized", "fingerprint_mismatch", "not_confirmable",
  "already_terminal", "invalid_credentials", "invalid_policy",
  "invalid_priority", "invalid_denylist_subject_type", "policy_not_found",
  "storage_exists", "storage_in_use", "storage_not_found", "node_not_found",
  "build_node_not_set", "build_in_progress", "unknown_image", "invalid_git_ref",
  "build_not_found", "submit_failed", "poll_failed", "unknown_build_node",
  "invalid_build_ref",
  // 브리프(task-1-brief.md)가 준 목록에는 없지만 src/dms/ grep으로 확인한 실제
  // 발생 코드들 -- 브리프의 BACKEND_CODES 는 이전 감사(59개 중 22개 누락)를 기준으로
  // 작성돼 routes_scan_paths.py, routes_accounts.py, routes_batches.py, routes_jobs.py,
  // routes_requests.py, domain.py, planner.py, placement.py, artifacts.py 의 코드를
  // 놓쳤다. 원래 REASON_MESSAGES 에 이미 한국어 매핑이 있었고, "죽은 키" 판정으로
  // 지우면 기존 ScanPaths.test.tsx / AccountsList.test.tsx 가 깨진다 (실제로 확인함).
  // 목록은 좁히지 않고 넓혔다 -- task-1-report.md 에 이탈 사유를 남긴다.
  "artifact_forbidden", "invalid_artifact_name", "rm_recursive_required",
  "rm_root_forbidden", "unknown_option", "invalid_option", "storage_missing",
  "storage_disabled", "storage_not_ready", "missing_storage",
  "missing_source_storage", "missing_destination_storage",
  "sync_destination_inside_source", "invalid_owner_username", "invalid_operation",
  "cancel_failed", "batch_not_cancelable", "request_not_found",
  "cancelled_by_user", "cancelled_by_batch", "scan_path_exists",
  "scan_path_not_found", "no_covering_scan", "scan_report_too_large",
  "account_not_found", "invalid_role", "cannot_lock_self",
];

describe("REASON_MESSAGES 커버리지", () => {
  it("백엔드가 내는 모든 코드에 한국어 매핑이 있다", () => {
    const missing = BACKEND_CODES.filter((c) => !(c in REASON_MESSAGES));
    expect(missing).toEqual([]);
  });

  it("죽은 키가 없다 -- 백엔드가 내지 않는 코드는 두지 않는다", () => {
    const allowed = new Set([...BACKEND_CODES, "http_401", "http_422", "http_500", "http_503"]);
    const dead = Object.keys(REASON_MESSAGES).filter((k) => !allowed.has(k));
    expect(dead).toEqual([]);
  });
});
