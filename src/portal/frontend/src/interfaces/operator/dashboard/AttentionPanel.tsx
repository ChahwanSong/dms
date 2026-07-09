import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { operatorApi, type AttentionItem, type DismissedItem, type FocusTarget } from "../../../api";
import { fmtAgo, fmtTime } from "./helpers";
import Section from "./Section";
import Loading from "../../../components/Loading";

// ---- severity ----
const SEVERITIES = ["CRITICAL", "ERROR", "WARN", "INFO"] as const;
const SEV_RANK: Record<string, number> = { CRITICAL: 0, ERROR: 1, WARN: 2, INFO: 3 };

// ---- domains (derived from issue_type prefix) ----
type Domain = "request" | "storage" | "agent" | "quota" | "filesystem" | "datajob" | "etc";
const DOMAIN_LABEL: Record<Domain, string> = {
  request: "요청", storage: "스토리지", agent: "에이전트",
  quota: "쿼터", filesystem: "파일시스템", datajob: "데이터 잡", etc: "기타",
};
function domainOf(issueType: string): Domain {
  if (issueType === "request_attention") return "request";
  if (issueType.startsWith("data_job")) return "datajob";
  if (issueType.startsWith("filesystem")) return "filesystem";
  if (issueType.includes("quota")) return "quota";
  if (issueType === "agent_report_stale") return "agent";
  if (
    issueType.startsWith("storage_") || issueType === "csi_driver_mismatch" ||
    issueType.startsWith("missing_rm") || issueType.startsWith("missing_dm")
  ) return "storage";
  return "etc";
}

// ---- friendly labels + fallback actions (DMS provides recommended_action for
// quota/filesystem/data_job; for request/storage/agent we supply the action here). ----
const ISSUE_META: Record<string, { label: string; action?: string }> = {
  storage_mapping_failed: { label: "스토리지 sanity 실패", action: "스토리지 인벤토리에서 원인 확인 후 sanity 재검사" },
  storage_mapping_unknown: { label: "스토리지 sanity 미검사", action: "스토리지 인벤토리에서 sanity 재검사 실행" },
  storage_class_missing: { label: "StorageClass 없음", action: "클러스터에 해당 StorageClass 생성" },
  csi_driver_mismatch: { label: "CSI 드라이버 불일치", action: "매핑의 CSI provisioner를 실제 드라이버에 맞게 정정" },
  missing_rm_readiness: { label: "RM agent 미준비", action: "해당 스토리지 노드의 RM agent(DaemonSet) 동작 확인" },
  missing_dm_readiness: { label: "DM agent 미준비", action: "해당 스토리지 노드의 DM agent(DaemonSet) 동작 확인" },
  agent_report_stale: { label: "노드 agent report 오래됨", action: "해당 노드의 agent 데몬 상태·시계·네트워크 확인 (DM job 실행 gate)" },
  // quota
  kubernetes_quota_expired_unblocked: { label: "Quota 만료 (미차단)", action: "namespace quota expiration sweep 실행, 또는 resource 수동 block" },
  kubernetes_quota_drifted: { label: "Quota drift", action: "update로 DB desired state 재적용, 또는 sync로 live 상태 수용" },
  kubernetes_quota_missing: { label: "Quota 없음 (live)", action: "DMS 관리 ResourceQuota 재생성, 또는 검토 후 DMS resource 레코드 삭제" },
  kubernetes_quota_db_only: { label: "Quota DB만 존재", action: "live ResourceQuota 재생성, 또는 검토 후 DB resource 레코드 삭제" },
  kubernetes_quota_metadata_drift: { label: "Quota metadata drift", action: "update/reset apply로 DMS metadata 복구, 또는 수동 변경 내역 확인" },
  kubernetes_quota_query_failed: { label: "Quota 조회 실패", action: "Kubernetes API 접근 확인 후 audit 재실행" },
  quota_usage_warning: { label: "Quota 사용량 경고", action: "quota 증설, storage 정리, 또는 namespace 소유자에게 연락" },
  quota_usage_critical: { label: "Quota 사용량 위험", action: "즉시 quota 증설 또는 storage 정리" },
  non_dms_quota_more_restrictive: { label: "non-DMS quota가 더 제한적", action: "non-DMS ResourceQuota 소유자 확인" },
  non_dms_quota_zero_limit: { label: "non-DMS quota 0 제한", action: "non-DMS ResourceQuota 소유자 확인" },
  kubernetes_quota_expiration_sweep_failed: { label: "Quota expiration sweep 실패", action: "실패한 target 점검 후 sweep 재실행, 또는 수동 block" },
  kubernetes_quota_expiration_sweep_skipped: { label: "Quota expiration sweep skip", action: "skip된 namespace quota expiration target 검토" },
  // filesystem
  filesystem_soft_deleted: { label: "Filesystem soft-delete (수동 제거 필요)", action: "backend 노드에서 디렉토리 수동 제거 (CephFS rm -rf / GPFS mmunlinkfileset+mmdelfileset)" },
  filesystem_expired_unblocked: { label: "Filesystem 만료 (미차단)", action: "filesystem expiration sweep 실행, 또는 resource 수동 block" },
  filesystem_quota_drifted: { label: "Filesystem quota drift", action: "filesystem sync로 live 수용, 또는 quota 재적용" },
  filesystem_quota_missing: { label: "Filesystem quota 없음", action: "filesystem quota 재적용, 또는 검토 후 DB 상태 sync" },
  filesystem_marker_mismatch: { label: "Filesystem marker 불일치", action: "수동 변경 전 filesystem marker 점검" },
  filesystem_unblock_restore_missing: { label: "Filesystem unblock 복원 누락", action: "filesystem 접근 수동 복구, 또는 DB block_state 보정" },
  filesystem_access_group_missing: { label: "Filesystem access group 없음", action: "DMS 관리 LDAP access group 복구 후 unblock 재실행" },
  filesystem_unsafe_existing_directory: { label: "기존 디렉토리 안전성 문제", action: "기존 디렉토리 안전성(owner·group·marker) 점검" },
  filesystem_import_preflight_failed: { label: "Filesystem import preflight 실패", action: "기존 디렉토리 owner·group·marker 보정 후 import 재시도" },
  filesystem_assign_quota_failed: { label: "Filesystem assign-quota 실패", action: "디렉토리 안전성 점검 후 assign-quota 재시도" },
  filesystem_block_failed: { label: "Filesystem block 실패", action: "block 결과 점검·조치 후 재실행" },
  filesystem_block_verification_failed: { label: "Filesystem block 검증 실패", action: "block/unblock 결과 점검 후 재실행" },
  filesystem_expiration_sweep_partial_failure: { label: "Filesystem expiration sweep 부분 실패", action: "실패한 target 점검 후 sweep 재실행" },
  filesystem_expiration_sweep_skipped: { label: "Filesystem expiration sweep skip", action: "skip된 filesystem expiration target 검토" },
  // data jobs
  data_job_policy_failed: { label: "Data job policy 실패", action: "data management policy 확인·수정 후 job 재시도" },
  data_job_identity_unresolved: { label: "Data job identity 미해결", action: "LDAP identity 등록/해결 후 job 재시도" },
  data_job_permission_denied: { label: "Data job 권한 거부", action: "POSIX 권한 수정 후 job 재시도" },
  data_job_no_ready_candidate: { label: "Data job 가용 노드 없음", action: "ready한 DM 노드 확보 후 job 재시도" },
  data_job_volcano_timeout: { label: "Data job timeout", action: "timeout 상향 또는 원인 해소 후 job 재시도" },
  data_job_volcano_failed: { label: "Data job scheduler 실패", action: "Volcano/MPI scheduler 문제 디버그 후 job 재시도" },
  data_job_artifact_parse_failed: { label: "Data job artifact 실패", action: "artifact 확인/재생성 후 job 재시도" },
  data_job_nsync_deferred: { label: "Data job nsync 보류", action: "sync 완료 대기, 또는 deferred 원인 점검" },
  data_job_cancelled: { label: "Data job 취소됨", action: "취소 사유 검토 후 필요시 재요청" },
  data_job_preflight_failed: { label: "Data job preflight 실패", action: "preflight 조건 충족 후 job 재시도" },
  data_job_failed: { label: "Data job 실패", action: "원인 수정 후 job 재시도" },
};

// request_attention carries the request's status — refine label/action by it.
const REQ_STATUS_LABEL: Record<string, string> = {
  Blocked: "요청 차단됨", StaleClaim: "워커 리스 만료", RecoveryNeeded: "복구 필요",
  VerificationFailed: "적용 검증 실패",
  UnknownAfterSideEffect: "결과 불명 (수동 확인)", BackendApplyFailed: "백엔드 적용 실패",
};
const REQ_STATUS_ACTION: Record<string, string> = {
  Blocked: "제어 상태(점검/드레인/스케줄링) 확인·해제 후 재처리",
  StaleClaim: "워커 동작 확인 → 자동 회수 대기, 또는 아래 '요청 강제 종료'로 종료(적용된 것 없음)",
  RecoveryNeeded: "실제 백엔드 상태 확인 → 재처리, 또는 아래 '요청 강제 종료'로 종료(부분 적용 가능)",
  VerificationFailed: "적용 결과 검증 실패 — 실제 백엔드 상태 확인, 또는 아래 '요청 강제 종료'로 종료(부분 적용 가능)",
  UnknownAfterSideEffect: "실제 백엔드 상태를 직접 확인 후 DB와 동기화, 또는 아래 '요청 강제 종료'로 종료(부분 적용 가능)",
  BackendApplyFailed: "원인(권한/연결/백엔드) 수정 후 재요청, 또는 아래 '요청 강제 종료'로 종료",
};
// DMS allows operator resolve/abandon for these STUCK request states (planner Conflicts
// new requests for the resource while a prior one here is still active — abandon unblocks
// it). StaleClaim/RecoveryNeeded are dead-ends (workers only re-claim Planned plans), so
// the tab is the escape hatch. Active-lease states (Running/Applying/…) are NOT here.
const RESOLVABLE_REQUEST_STATES = new Set([
  "UnknownAfterSideEffect",
  "BackendApplyFailed",
  "StaleClaim",
  "RecoveryNeeded",
  "VerificationFailed",
]);
// Abandon of these MAY have a partially-applied, unverified backend side effect — the
// operator must verify the backend first (extra confirm).
const BACKEND_UNVERIFIED_STATES = new Set([
  "RecoveryNeeded",
  "UnknownAfterSideEffect",
  "VerificationFailed",
]);

function str(v: unknown): string | undefined {
  return typeof v === "string" && v ? v : undefined;
}
function labelOf(item: AttentionItem): string {
  if (item.issue_type === "request_attention") {
    const st = str(item.status);
    return REQ_STATUS_LABEL[st || ""] || `요청 정체${st ? ` (${st})` : ""}`;
  }
  return ISSUE_META[item.issue_type]?.label || item.issue_type;
}
function actionOf(item: AttentionItem): string {
  if (item.issue_type === "request_attention") {
    return REQ_STATUS_ACTION[str(item.status) || ""] || "요청 상세에서 상태 확인 후 재처리/취소";
  }
  return (
    ISSUE_META[item.issue_type]?.action ||
    str(item.recommended_action) ||
    "항목을 펼쳐 상세를 확인하세요"
  );
}
function identOf(item: AttentionItem): string | undefined {
  const ns = str(item.namespace_name) || str(item.namespace);
  return (
    str(item.storage_name) ||
    (ns ? `${str(item.cluster_name) ? str(item.cluster_name) + "/" : ""}${ns}` : undefined) ||
    str(item.directory_name) ||
    str(item.node_name) ||
    str(item.target) ||
    (str(item.request_id) ? `req…${str(item.request_id)!.slice(-6)}` : undefined)
  );
}
// A request that DMS can resolve/abandon directly (real resolution, not just hide).
function canResolve(item: AttentionItem): boolean {
  return item.issue_type === "request_attention" &&
    RESOLVABLE_REQUEST_STATES.has(str(item.status) || "") &&
    !!str(item.request_id);
}
// A terminal data-job record that can be deleted from DMS (stops perpetual accrual).
function canDelete(item: AttentionItem): boolean {
  return item.issue_type.startsWith("data_job") && !!str(item.job_id);
}

// Where an item's "상세" deep-link goes: the owning view + the specific item to
// focus/open there (storage mapping detail, the request row, etc.).
function detailTarget(
  item: AttentionItem,
): { section: string; focus?: FocusTarget; label: string } | null {
  const dom = domainOf(item.issue_type);
  const st = str(item.storage_name);
  const rid = str(item.request_id);
  if ((dom === "storage" || dom === "filesystem") && st)
    return { section: "storage", focus: { kind: "storage", value: st }, label: "스토리지 상세 열기" };
  if (dom === "request" && rid)
    return { section: "dashboard-activity", focus: { kind: "request", value: rid }, label: "요청 상세 보기" };
  if (dom === "datajob")
    return { section: "backup", label: "데이터 백업에서 보기" };
  return null;
}

// ---- detail grid ----
const FIELD_LABEL: Record<string, string> = {
  storage_name: "스토리지", cluster_name: "클러스터", namespace_name: "네임스페이스",
  namespace: "네임스페이스", node_name: "노드", worker_role: "역할",
  directory_name: "디렉토리", resource_type: "리소스 유형", resource_key: "리소스 키",
  key: "쿼터 항목", used: "사용", hard: "한도", used_percent: "사용률(%)",
  operation: "작업", target: "대상", requester_id: "요청자", status: "상태",
  state: "상태", reason: "사유", message: "메시지", sanity_status: "sanity 상태",
  expires_at: "만료", reported_at: "보고 시각", updated_at: "갱신",
  last_seen: "마지막 관측", created_at: "생성", request_id: "요청 ID",
  job_id: "작업 ID", report_id: "리포트 ID", source_request_id: "출처 요청",
  seconds_overdue: "초과(초)", desired: "원함", live: "라이브",
  actor: "실행자", resource_kind: "리소스 종류", payload_summary: "요청 내용",
  requested_at: "요청 시각", commit_order: "커밋 순서", field: "필드",
  sanity_result: "sanity 결과", preflight_result: "프리플라이트 결과",
  result_summary: "결과 요약", fileset_name: "fileset", recommended: "권고",
};
const TIME_FIELDS = new Set([
  "expires_at", "reported_at", "updated_at", "last_seen", "created_at", "requested_at",
]);
const DETAIL_ORDER = [
  "status", "state", "reason", "field", "message", "storage_name", "cluster_name",
  "namespace_name", "namespace", "node_name", "worker_role", "directory_name",
  "resource_type", "resource_kind", "resource_key", "key", "used", "hard", "used_percent",
  "operation", "target", "requester_id", "actor", "sanity_status", "expires_at",
  "requested_at", "reported_at", "updated_at", "last_seen", "created_at",
  "commit_order", "source_request_id", "request_id", "job_id", "report_id",
  "payload_summary", "result_summary", "preflight_result", "sanity_result",
];
const DETAIL_SKIP = new Set(["issue_type", "severity", "category", "recommended_action", "fingerprint"]);

function Kv({ label, children, span, mono }: {
  label: string; children: ReactNode; span?: boolean; mono?: boolean;
}) {
  return (
    <div className={`spec-kv${span ? " span" : ""}`}>
      <dt>{label}</dt>
      <dd className={mono ? "mono" : undefined}>{children}</dd>
    </div>
  );
}

function detailRows(item: AttentionItem): ReactNode[] {
  const keys = Object.keys(item).filter((k) => !DETAIL_SKIP.has(k) && item[k] != null && item[k] !== "");
  keys.sort((a, b) => {
    const ia = DETAIL_ORDER.indexOf(a), ib = DETAIL_ORDER.indexOf(b);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
  });
  return keys.map((k) => {
    const v = item[k];
    if (v !== null && typeof v === "object") {
      return <Kv key={k} label={FIELD_LABEL[k] || k} span mono>{JSON.stringify(v).slice(0, 240)}</Kv>;
    }
    const text = TIME_FIELDS.has(k) ? fmtTime(String(v)) : String(v);
    const mono = k.endsWith("_id") || k === "resource_key" || k === "target";
    return <Kv key={k} label={FIELD_LABEL[k] || k} mono={mono}>{text}</Kv>;
  });
}

interface ItemActions {
  onNavigate?: (section: string, focus?: FocusTarget) => void;
  onDismiss: (item: AttentionItem) => void;
  onAck: (item: AttentionItem) => void;
  onResolve: (item: AttentionItem) => void;
  onDelete: (item: AttentionItem) => void;
}

// Items with no programmatic DMS action (manual backend cleanup) — the system
// recommends an operator confirm them done. ACK is the primary close-out for these.
function needsManualAck(item: AttentionItem): boolean {
  return !canDelete(item) && !canResolve(item);
}

function Item({ item, act, checked, onToggleSel }: {
  item: AttentionItem; act: ItemActions;
  checked: boolean; onToggleSel: (fp: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const dom = domainOf(item.issue_type);
  const sev = (str(item.severity) || "WARN").toUpperCase();
  const ident = identOf(item);
  const when = itemWhen(item);
  const target = detailTarget(item);
  const fp = item.fingerprint;
  const goDetail = () => target && act.onNavigate?.(target.section, target.focus);
  return (
    <div className={`attn2 attn2-${sev.toLowerCase()}`}>
      <div className="attn2-rowwrap">
        {fp && (
          <label className="check-cell attn2-check" title="선택">
            <input type="checkbox" checked={checked} onChange={() => onToggleSel(fp)} aria-label="선택" />
          </label>
        )}
        <button type="button" className="attn2-row" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
          <span className={`attn2-sev attn2-${sev.toLowerCase()}`}>{sev}</span>
          <span className="attn2-main">
            <span className="attn2-head">
              <span className="attn2-dom">{DOMAIN_LABEL[dom]}</span>
              <span className="attn2-label">{labelOf(item)}</span>
              {ident && <span className="attn2-ident mono">{ident}</span>}
            </span>
            <span className="attn2-action"><span className="attn2-tag">권고</span>{actionOf(item)}</span>
          </span>
          {when && <span className="attn2-when muted small" title={fmtTime(when)}>{fmtAgo(when)}</span>}
          <span className="attn2-caret" aria-hidden="true">{open ? "▾" : "▸"}</span>
        </button>
        {target && act.onNavigate && (
          <button type="button" className="attn2-hide attn2-detail-link" title={target.label}
            onClick={goDetail}>상세 →</button>
        )}
        <button type="button" className="attn2-hide" title="이 포탈에서만 숨김 (해당없음 — 처리 내역에서 복원 가능). 다른 화면에는 계속 보입니다."
          onClick={() => act.onDismiss(item)}>숨김</button>
      </div>
      {open && (
        <div className="attn2-detail">
          <dl className="spec-grid">{detailRows(item)}</dl>
          <div className="attn2-cta">
            {target && act.onNavigate && (
              <button className="mini primary" onClick={goDetail}>{target.label} →</button>
            )}
            {canResolve(item) && (
              <button className="mini danger" onClick={() => act.onResolve(item)}>요청 강제 종료(abandon)</button>
            )}
            {canDelete(item) && (
              <button className="mini danger" onClick={() => act.onDelete(item)}>기록 삭제</button>
            )}
            <button className={needsManualAck(item) ? "mini primary" : "mini"} onClick={() => act.onAck(item)}
              title="운영자가 확인·처리 완료 — DMS에 기록되어 모든 화면의 '조치 필요'에서 제외됩니다 (기록은 보존)">확인(처리완료)</button>
            <button className="mini ghost" onClick={() => act.onDismiss(item)}
              title="이 포탈에서만 숨김 (해당없음/무시) — 처리 내역에서 복원 가능. 다른 화면에는 계속 보입니다.">숨김</button>
          </div>
        </div>
      )}
    </div>
  );
}

// One offender list: select-all + per-section time-sort toggle + "보이는 N건 숨김".
function Group({ items, dir, onToggleSort, onDismissVisible, act, empty, selected, onToggleSel, onToggleSelAll }: {
  items: AttentionItem[];
  dir: "desc" | "asc";
  onToggleSort: () => void;
  onDismissVisible: (items: AttentionItem[]) => void;
  act: ItemActions;
  empty: string;
  selected: Set<string>;
  onToggleSel: (fp: string) => void;
  onToggleSelAll: (items: AttentionItem[], select: boolean) => void;
}) {
  if (items.length === 0) return <p className="muted small">{empty}</p>;
  const selectable = items.filter((i) => i.fingerprint);
  const allSel = selectable.length > 0 && selectable.every((i) => selected.has(i.fingerprint as string));
  return (
    <>
      <div className="attn-sec-tools">
        {selectable.length > 0 && (
          <label className="check-cell" style={{ marginRight: "auto" }} title="이 섹션 전체 선택">
            <input type="checkbox" checked={allSel}
              onChange={() => onToggleSelAll(selectable, !allSel)} aria-label="전체 선택" />
          </label>
        )}
        <button className="attn-sort" onClick={() => onDismissVisible(items)}
          title="현재 보이는(필터된) 항목을 모두 숨김 — 처리 내역에서 복원 가능">
          보이는 {items.length}건 숨김
        </button>
        <button className="attn-sort" onClick={onToggleSort}
          title="시간순 정렬 전환 (갱신·보고·요청 시각 기준)">
          {dir === "desc" ? "최신순 ↓" : "오래된순 ↑"}
        </button>
      </div>
      <div className="attn2-list">
        {items.map((r, i) => (
          <Item key={r.fingerprint || `${r.issue_type}-${i}`} item={r} act={act}
            checked={!!r.fingerprint && selected.has(r.fingerprint)}
            onToggleSel={onToggleSel} />
        ))}
      </div>
    </>
  );
}

// dismissed-record action gates (the row carries the identifiers captured at
// dismiss time, so we don't need the live action-required item).
function dCanDelete(d: DismissedItem): boolean {
  return !!d.job_id;
}
function dCanResolve(d: DismissedItem): boolean {
  return !!d.request_id && RESOLVABLE_REQUEST_STATES.has(d.status || "");
}

// the resource identifier an acknowledged record was about — the key part of the
// fingerprint ("{issue_type}|{key}"), falling back to the captured job/request id.
function dKey(d: DismissedItem): string {
  const fp = d.fingerprint || "";
  const i = fp.indexOf("|");
  const key = i >= 0 ? fp.slice(i + 1) : "";
  return key || str(d.job_id) || str(d.request_id) || "";
}

// A readable resource descriptor from a fingerprint key. Data-job keys are verbose
// (data.<op>:<srcStore>:<srcPath>:<dstStore>:<dstPath>:sha256:<hash>) — collapse them
// to "op · src → dst" (or "op · path" for scan/rm) and drop the sha256 noise, so the
// 처리 내역 / 영구숨김 lists stay scannable. Other keys pass through unchanged.
function friendlyKey(key: string): string {
  if (!key) return "";
  if (key.startsWith("data.")) {
    const parts = key.split(":");
    const op = parts[0].slice("data.".length); // sync / scan / rm
    const sha = parts.indexOf("sha256");
    const body = (sha > 0 ? parts.slice(1, sha) : parts.slice(1)).filter(Boolean);
    if (body.length >= 4) return `${op} · ${body[0]}:${body[1]} → ${body[2]}:${body[3]}`;
    if (body.length >= 2) return `${op} · ${body[0]}:${body[1]}`;
    return body.length ? `${op} · ${body.join(":")}` : op;
  }
  return key;
}

function DismissedList({
  rows, total, dir, onToggleSort, selected, onToggleSel, onToggleSelAll, onClearSel,
  onUndismiss, onUndismissAll, onArchive, onArchiveBefore, onAck, onDelete, onResolve, busy,
}: {
  rows: DismissedItem[];
  total: number; // grand count (may exceed loaded rows — 처리 내역 is paginated)
  dir: "desc" | "asc";
  onToggleSort: () => void;
  selected: Set<string>;
  onToggleSel: (fp: string) => void;
  onToggleSelAll: (rows: DismissedItem[], select: boolean) => void;
  onClearSel: () => void;
  onUndismiss: (fingerprints: string[]) => void;
  onUndismissAll: () => void;
  onArchive: (fingerprints: string[]) => void;
  onArchiveBefore: (beforeIso: string) => void;
  onAck: (rows: DismissedItem[]) => void;
  onDelete: (rows: DismissedItem[]) => void;
  onResolve: (rows: DismissedItem[]) => void;
  busy: boolean;
}) {
  const [purgeAt, setPurgeAt] = useState("");
  if (rows.length === 0) return <p className="muted small">처리 내역이 없습니다.</p>;

  // '영구숨김(archive)': permanently hide the chosen records everywhere (조치 필요/과거
  // 이력/액티비티) AND drop them from 처리 내역. Reversible only via the 영구숨김 항목
  // section (unarchive). Confirm because it leaves the normal view.
  const archiveFps = (fingerprints: string[]) => {
    const fps = fingerprints.filter(Boolean);
    if (!fps.length) return;
    if (!window.confirm(
      `${fps.length}건을 영구숨김할까요?\n` +
      "처리 내역에서 사라지고 조치 필요·과거 이력·액티비티에서 모두 완전히 가려집니다.\n" +
      "복원하려면 '영구숨김 항목'에서 되돌려야 합니다."
    )) return;
    onArchive(fps);
  };
  const allSel = rows.length > 0 && rows.every((d) => selected.has(d.fingerprint));
  const sel = rows.filter((d) => selected.has(d.fingerprint));
  const selAckable = sel.filter((d) => d.kind !== "ack");
  const selDeletable = sel.filter(dCanDelete);
  const selResolvable = sel.filter(dCanResolve);

  // the time we show/sort/purge by: the action-required item's report time
  // (captured at dismiss), falling back to the dismiss time for legacy records.
  const whenOf = (d: DismissedItem) => d.item_at || d.dismissed_at;
  // '이전 영구숨김': archive every record dismissed at/before the chosen time. Resolved
  // SERVER-SIDE over the whole 처리 내역 (not just the loaded page), so pagination can't
  // silently miss older rows. Housekeeping for the forever-accruing list.
  const purgeBefore = () => {
    if (!purgeAt) return;
    const cutoff = new Date(purgeAt).getTime();
    if (Number.isNaN(cutoff)) return;
    if (!window.confirm(
      `${fmtTime(purgeAt)} 이전에 처리한 내역을 모두 영구숨김할까요? (불러온 화면 분량뿐 아니라 전체 대상)\n` +
      "조치 필요·과거 이력·액티비티에서 모두 완전히 가려집니다. 복원은 '영구숨김 항목'에서."
    )) return;
    onArchiveBefore(purgeAt);
  };
  return (
    <>
      <p className="muted small dism-note">
        <b>복원</b> = 조치 필요로 되돌림 · <b className="attn2-archive">영구숨김</b> = 포탈에서
        완전히 가림(복원은 아래 <b>영구숨김 항목</b>에서) · 목록은 <b>50건씩</b> 불러옵니다
        (누적돼도 화면 분량만 로딩)
      </p>
      <div className="attn-sec-tools dism-tools">
        <label className="check-cell" style={{ marginRight: "auto" }} title="불러온 항목 전체 선택">
          <input type="checkbox" checked={allSel}
            onChange={() => onToggleSelAll(rows, !allSel)} aria-label="불러온 항목 전체 선택" />
        </label>
        <span className="muted small">이 시각 이전 영구숨김:</span>
        <input type="datetime-local" className="dism-purge-at" value={purgeAt}
          onChange={(e) => setPurgeAt(e.target.value)} title="이 시각 이전 처리 내역을 영구숨김 (전체 대상)" />
        <button className="attn-sort attn2-archive" disabled={!purgeAt || busy} onClick={purgeBefore}
          title="입력한 시각 이전에 처리한 내역을 전체 영구숨김 — 조치 필요·이력·액티비티서 완전히 가림(복원은 영구숨김 항목)">이전 영구숨김</button>
        <button className="attn-sort" onClick={onToggleSort} title="리포트 시각순 정렬 전환">
          {dir === "desc" ? "최신순 ↓" : "오래된순 ↑"}
        </button>
        <button className="attn-sort" onClick={onUndismissAll}
          title="처리 내역 전체를 조치 필요로 복원 (불러온 화면 분량뿐 아니라 전체 대상)">모두 복원 ({total})</button>
      </div>
      {sel.length > 0 && (
        <div className="bulk-bar">
          <span className="bulk-count">{sel.length}개 선택</span>
          {selAckable.length > 0 && (
            <button className="primary mini" disabled={busy} onClick={() => onAck(selAckable)}
              title="확인(처리완료)로 표시">확인 ({selAckable.length})</button>
          )}
          <button className="mini" disabled={busy} onClick={() => onUndismiss(sel.map((d) => d.fingerprint))}
            title="조치 필요로 복원 (아직 유효한 항목은 다시 표시됨)">
            복원 ({sel.length})
          </button>
          <button className="mini danger" disabled={busy} onClick={() => archiveFps(sel.map((d) => d.fingerprint))}
            title="영구숨김 — 선택 항목을 포탈에서 완전히 가림(조치 필요·이력·액티비티). 복원은 '영구숨김 항목'에서.">
            영구숨김 ({sel.length})
          </button>
          <button className="mini danger" disabled={busy || selDeletable.length === 0} onClick={() => onDelete(selDeletable)}>
            기록 삭제 ({selDeletable.length})
          </button>
          {selResolvable.length > 0 && (
            <button className="ghost mini" disabled={busy} onClick={() => onResolve(selResolvable)}>
              요청 중단 ({selResolvable.length})
            </button>
          )}
          <button className="ghost mini" onClick={onClearSel}>선택 해제</button>
        </div>
      )}
      <div className="attn2-list">
        {rows.map((d) => {
          const isAck = d.kind === "ack";
          const ident = dKey(d);
          return (
            <div key={d.fingerprint} className="attn2 attn2-info">
              <div className="attn2-rowwrap">
                <label className="check-cell attn2-check" title="선택">
                  <input type="checkbox" checked={selected.has(d.fingerprint)}
                    onChange={() => onToggleSel(d.fingerprint)} aria-label="선택" />
                </label>
                <div className="attn2-row dismissed">
                  <span className="attn2-main">
                    <span className="attn2-head">
                      <span className={`chip ${isAck ? "tone-ok" : "tone-low"}`}
                        title={
                          !isAck
                            ? "이 포탈에서만 숨김 — 다른 화면에는 계속 보입니다. '복원' 시 다시 표시됩니다."
                            : d.in_dms
                              ? "DMS에 확인 처리됨 — 모든 화면의 '조치 필요'에서 제외 (기록 보존). '복원' 시 다시 표시됩니다."
                              : "이 포탈에서 확인된 이전 기록 (DMS 서버 반영 전) — '복원' 후 다시 '확인'하면 전체 화면에 반영됩니다."}>
                        {!isAck ? "숨김 · 포탈" : d.in_dms ? "확인됨 · 전체" : "확인됨 · 로컬"}
                      </span>
                      <span className="attn2-dom">{DOMAIN_LABEL[domainOf(d.issue_type || "")]}</span>
                      <span className="attn2-label">{d.label || d.issue_type || d.fingerprint}</span>
                    </span>
                    {ident && (
                      <span className="attn2-res mono" title={ident}>{friendlyKey(ident)}</span>
                    )}
                    <span className="attn2-action muted small">
                      {d.dismissed_by || "operator"}
                      {dCanDelete(d) ? " · data job" : dCanResolve(d) ? " · request" : ""}
                      {d.reason ? ` · ${d.reason}` : ""}
                    </span>
                  </span>
                  {whenOf(d) && (
                    <span className="attn2-when muted small"
                      title={`리포트 ${fmtTime(whenOf(d))}\n처리 ${fmtTime(d.dismissed_at)} · ${d.dismissed_by || "operator"}`}>
                      {fmtAgo(whenOf(d))}
                    </span>
                  )}
                </div>
                <button type="button" className="attn2-hide" title="조치 필요로 복원 (항목이 아직 유효하면 다시 표시됨)"
                  onClick={() => onUndismiss([d.fingerprint])}>복원</button>
                <button type="button" className="attn2-hide attn2-archive" disabled={busy}
                  title="영구숨김 — 포탈에서 완전히 가림(조치 필요·이력·액티비티). 복원은 '영구숨김 항목'에서."
                  onClick={() => archiveFps([d.fingerprint])}>영구숨김</button>
              </div>
            </div>
          );
        })}
      </div>
    </>
  );
}

// the action-required item's own report/updated time (ISO string), in the same
// priority the live/history rows display — captured into 처리 내역 so it shows the
// report time, not the admin's dismiss time.
function itemWhen(item: AttentionItem): string | undefined {
  return str(item.updated_at) || str(item.reported_at) || str(item.last_seen) ||
    str(item.requested_at) || str(item.created_at) || str(item.expires_at);
}
// most relevant timestamp for an item (recency), epoch ms (0 if none).
function timeOf(item: AttentionItem): number {
  const t = itemWhen(item);
  const ms = t ? new Date(t).getTime() : NaN;
  return Number.isNaN(ms) ? 0 : ms;
}

// Acknowledge payload. kind 'ack' = 운영자가 확인·수동 처리함(예: soft-delete 정리 완료),
// 'dismissed' = 해당없음/숨김. We also capture job_id/request_id/status so the hidden
// item can still be DMS-deleted/abandoned later from the 숨김 항목 list.
const ackPayload = (items: AttentionItem[], kind: "ack" | "dismissed") =>
  items
    .filter((i) => i.fingerprint)
    .map((i) => ({
      fingerprint: i.fingerprint as string,
      issue_type: i.issue_type,
      label: labelOf(i),
      kind,
      job_id: str(i.job_id) ?? null,
      request_id: str(i.request_id) ?? null,
      status: str(i.status) ?? null,
      item_at: itemWhen(i) ?? null,
    }));

// re-acknowledge a dismissed record (e.g. 숨김 → 확인) — reuses the stored identifiers
// and the original report time.
const dismToAckPayload = (rows: DismissedItem[], kind: "ack" | "dismissed") =>
  rows.map((d) => ({
    fingerprint: d.fingerprint, issue_type: d.issue_type, label: d.label, kind,
    job_id: d.job_id ?? null, request_id: d.request_id ?? null, status: d.status ?? null,
    item_at: d.item_at ?? null,
  }));

// sort by the item's report time (item_at), falling back to dismiss time.
const dismMs = (d: DismissedItem) => {
  const w = d.item_at || d.dismissed_at;
  return w ? new Date(w).getTime() || 0 : 0;
};

// 처리 내역 (dismissed/ack) — like 영구숨김, a forever-accruing list. Its body is
// fetched LAZILY (only when expanded) and one PAGE at a time ('더 보기'); the parent
// supplies the grand COUNT (`total`) for the badge. Portal rows are paginated; the
// (small) DMS-only ack addendum arrives with the first page. Whole-list ops ('모두
// 복원'/'이전 영구숨김') go server-side so pagination never truncates them.
const DISM_PAGE = 50;
function DismissedSection({
  total,
  busy,
  onChanged,
}: {
  total: number;
  busy: boolean;
  onChanged: () => void | Promise<void>;
}) {
  const [items, setItems] = useState<DismissedItem[]>([]); // paginated portal rows
  const [extra, setExtra] = useState<DismissedItem[]>([]); // DMS-only acks (page 0)
  const [sort, setSort] = useState<"desc" | "asc">("desc");
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false); // last page returned < PAGE → no more
  const [everOpened, setEverOpened] = useState(false);

  const load = useCallback(async (offset: number, order: "desc" | "asc") => {
    setLoading(true);
    try {
      const res = await operatorApi.dashboard.dismissedAttention(offset, DISM_PAGE, order);
      setItems((prev) => (offset === 0 ? res.items : [...prev, ...res.items]));
      if (offset === 0) setExtra(res.extra || []);
      setDone(res.items.length < DISM_PAGE);
    } catch (e) {
      window.alert(`처리 내역 불러오기 실패: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setLoading(false);
    }
  }, []);

  const onOpen = (open: boolean) => {
    if (open && !everOpened) { setEverOpened(true); load(0, sort); }
  };
  const flipSort = () => {
    const next = sort === "desc" ? "asc" : "desc";
    setSort(next); setSel(new Set()); load(0, next);
  };
  const loadMore = () => load(items.length, sort);

  // run a mutating action, then reload page 0 (this section) + refresh the parent
  // (attention counts / archived count that the action may have changed).
  const run = async (fn: () => Promise<unknown>): Promise<boolean> => {
    setLoading(true);
    try {
      await fn();
    } catch (e) {
      window.alert(`작업 실패: ${e instanceof Error ? e.message : String(e)}`);
      setLoading(false);
      return false;
    }
    await load(0, sort);
    await onChanged();
    setLoading(false);
    return true;
  };

  const toggleSel = (fp: string) =>
    setSel((p) => { const n = new Set(p); n.has(fp) ? n.delete(fp) : n.add(fp); return n; });
  const toggleSelAll = (rowsArg: DismissedItem[], select: boolean) =>
    setSel((p) => {
      const n = new Set(p);
      for (const d of rowsArg) select ? n.add(d.fingerprint) : n.delete(d.fingerprint);
      return n;
    });
  const clearSel = () => setSel(new Set());

  const undismiss = (fps: string[]) =>
    run(() => operatorApi.dashboard.undismissAttention(fps)).then((ok) => { if (ok) clearSel(); return ok; });
  const archive = (fps: string[]) =>
    run(() => operatorApi.dashboard.archiveAttention(fps)).then((ok) => { if (ok) clearSel(); return ok; });
  const archiveBefore = (before: string) =>
    run(() => operatorApi.dashboard.archiveAttentionBefore(before));
  const undismissAll = async () => {
    if (!window.confirm(
      `처리 내역 ${total}건을 모두 조치 필요로 복원할까요?\n` +
      "(이미 해소된 항목은 사라지고, 아직 유효한 항목은 다시 표시됩니다)"
    )) return;
    if (await run(() => operatorApi.dashboard.undismissAllAttention())) clearSel();
  };
  const bulkAck = async (rowsArg: DismissedItem[]) => {
    if (!rowsArg.length) return;
    if (await run(() => operatorApi.dashboard.dismissAttention(dismToAckPayload(rowsArg, "ack")))) clearSel();
  };
  const bulkDelete = async (rowsArg: DismissedItem[]) => {
    if (!rowsArg.length) return;
    if (!window.confirm(`선택한 data job 기록 ${rowsArg.length}건을 DMS에서 삭제할까요? (되돌릴 수 없음)`)) return;
    if (await run(async () => {
      for (const d of rowsArg) if (d.job_id) await operatorApi.dashboard.deleteDataJob(d.job_id);
      // the underlying items are gone now — drop their (orphaned) 처리 내역 rows too.
      await operatorApi.dashboard.undismissAttention(rowsArg.map((d) => d.fingerprint));
    })) clearSel();
  };
  const bulkResolve = async (rowsArg: DismissedItem[]) => {
    if (!rowsArg.length) return;
    const reason = window.prompt(
      `선택한 요청 ${rowsArg.length}건을 중단(abandon)합니다. 사유 입력 (감사 기록):`, "obsolete — 정리");
    if (!reason) return;
    if (await run(async () => {
      for (const d of rowsArg) if (d.request_id) await operatorApi.dashboard.resolveRequest(d.request_id, "abandon", reason);
      await operatorApi.dashboard.undismissAttention(rowsArg.map((d) => d.fingerprint));
    })) clearSel();
  };

  // merged display: paginated portal rows + the small DMS-only ack addendum, client-sorted.
  const merged = [...items, ...extra].sort((a, b) =>
    sort === "desc" ? dismMs(b) - dismMs(a) : dismMs(a) - dismMs(b));
  const loadedCount = items.length + extra.length;
  const disabled = busy || loading;

  return (
    <Section title="처리 내역 (확인·숨김)" badge={<span className="muted small">{total}건</span>}
      onOpenChange={onOpen}>
      {loading && !merged.length ? (
        <Loading rows={2} />
      ) : (
        <DismissedList rows={merged} total={total}
          dir={sort} onToggleSort={flipSort}
          selected={sel} onToggleSel={toggleSel} onToggleSelAll={toggleSelAll}
          onClearSel={clearSel} onUndismiss={undismiss} onUndismissAll={undismissAll}
          onArchive={archive} onArchiveBefore={archiveBefore}
          onAck={bulkAck} onDelete={bulkDelete} onResolve={bulkResolve} busy={disabled} />
      )}
      {!done && items.length > 0 && (
        <div className="attn-more">
          <button className="ghost mini" onClick={loadMore} disabled={disabled}>
            더 보기 ({loadedCount} / {total})
          </button>
        </div>
      )}
    </Section>
  );
}

// 영구숨김 항목 (archived) — a forever-growing restore bin. Its body is fetched
// LAZILY (only when the operator expands the section) and one PAGE at a time
// ('더 보기'), so an unbounded archive never loads/renders all at once. The parent
// supplies the authoritative COUNT (`total`) for the badge/visibility; this
// component owns the paged rows + selection + restore(unarchive).
const ARCH_PAGE = 50;
function ArchivedSection({
  total,
  busy,
  onChanged,
}: {
  total: number;
  busy: boolean;
  onChanged: () => void;
}) {
  const [items, setItems] = useState<DismissedItem[]>([]);
  const [sort, setSort] = useState<"desc" | "asc">("desc");
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false); // last page returned < PAGE → no more
  const [everOpened, setEverOpened] = useState(false);

  const load = useCallback(async (offset: number, order: "desc" | "asc") => {
    setLoading(true);
    try {
      const res = await operatorApi.dashboard.archivedAttention(offset, ARCH_PAGE, order);
      setItems((prev) => (offset === 0 ? res.items : [...prev, ...res.items]));
      setDone(res.items.length < ARCH_PAGE);
    } catch (e) {
      window.alert(`영구숨김 목록 불러오기 실패: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setLoading(false);
    }
  }, []);

  // fetch the first page the first time the section is expanded (not on mount).
  const onOpen = (open: boolean) => {
    if (open && !everOpened) {
      setEverOpened(true);
      load(0, sort);
    }
  };
  const flipSort = () => {
    const next = sort === "desc" ? "asc" : "desc";
    setSort(next);
    setSel(new Set());
    load(0, next); // re-page from the top in the new order
  };
  const loadMore = () => load(items.length, sort);

  const unarchive = async (fingerprints: string[]) => {
    if (!fingerprints.length) return;
    setLoading(true);
    try {
      await operatorApi.dashboard.unarchiveAttention(fingerprints);
    } catch (e) {
      window.alert(`복원 실패: ${e instanceof Error ? e.message : String(e)}`);
      setLoading(false);
      return;
    }
    setSel(new Set());
    await load(0, sort); // refresh this section's first page
    onChanged(); // refresh parent (처리 내역 재등장 + archTotal 감소)
    setLoading(false);
  };

  const toggleSel = (fp: string) =>
    setSel((prev) => { const n = new Set(prev); n.has(fp) ? n.delete(fp) : n.add(fp); return n; });
  const allSel = items.length > 0 && items.every((d) => sel.has(d.fingerprint));
  const toggleSelAll = () =>
    setSel((prev) => {
      const n = new Set(prev);
      for (const d of items) allSel ? n.delete(d.fingerprint) : n.add(d.fingerprint);
      return n;
    });

  const disabled = busy || loading;
  return (
    <Section
      title="영구숨김 항목"
      onOpenChange={onOpen}
      badge={<span className="muted small"><span className="attn2-archive">{total}건</span> · 복원 가능</span>}
    >
      <p className="muted small dism-note">
        포탈에서 <b className="attn2-archive">완전히 가려진</b> 항목입니다 (조치 필요·과거 이력·액티비티 모두 미표시).
        <b>처리내역으로 복원</b>하면 다시 처리 내역에 나타나며, 거기서 조치 필요로 되돌릴 수 있습니다.
        <br />목록은 펼칠 때 <b>{ARCH_PAGE}건씩</b> 불러옵니다 (누적돼도 화면 분량만 로딩).
      </p>
      <div className="attn-sec-tools">
        <label className="check-cell" style={{ marginRight: "auto" }} title="불러온 항목 전체 선택">
          <input type="checkbox" checked={allSel} disabled={!items.length}
            onChange={toggleSelAll} aria-label="불러온 항목 전체 선택" />
        </label>
        <button className="attn-sort" onClick={flipSort} disabled={disabled}
          title="시각순 정렬 전환">{sort === "desc" ? "최신순 ↓" : "오래된순 ↑"}</button>
      </div>
      {sel.size > 0 && (
        <div className="bulk-bar">
          <span className="bulk-count">{sel.size}개 선택</span>
          <button className="primary mini" disabled={disabled}
            onClick={() => unarchive([...sel])}>처리내역으로 복원 ({sel.size})</button>
          <button className="ghost mini" onClick={() => setSel(new Set())}>선택 해제</button>
        </div>
      )}
      <div className="attn2-list">
        {items.map((d) => {
          const ident = dKey(d);
          const when = d.item_at || d.dismissed_at;
          return (
            <div key={d.fingerprint} className="attn2 attn2-info">
              <div className="attn2-rowwrap">
                <label className="check-cell attn2-check" title="선택">
                  <input type="checkbox" checked={sel.has(d.fingerprint)}
                    onChange={() => toggleSel(d.fingerprint)} aria-label="선택" />
                </label>
                <div className="attn2-row dismissed">
                  <span className="attn2-main">
                    <span className="attn2-head">
                      <span className="chip tone-low attn2-archive-chip">영구숨김</span>
                      <span className="attn2-dom">{DOMAIN_LABEL[domainOf(d.issue_type || "")]}</span>
                      <span className="attn2-label">{d.label || d.issue_type || d.fingerprint}</span>
                    </span>
                    {ident && (
                      <span className="attn2-res mono" title={ident}>{friendlyKey(ident)}</span>
                    )}
                    <span className="attn2-action muted small">
                      {d.dismissed_by || "operator"}{d.reason ? ` · ${d.reason}` : ""}
                    </span>
                  </span>
                  {when && <span className="attn2-when muted small" title={fmtTime(when)}>{fmtAgo(when)}</span>}
                </div>
                <button type="button" className="attn2-hide attn2-detail-link" disabled={disabled}
                  title="처리 내역으로 되돌림 (다시 처리 내역에 표시 — 이후 조치 필요로 복원 가능)"
                  onClick={() => unarchive([d.fingerprint])}>처리내역으로 복원</button>
              </div>
            </div>
          );
        })}
      </div>
      {loading && !items.length && <Loading rows={2} />}
      {!done && items.length > 0 && (
        <div className="attn-more">
          <button className="ghost mini" onClick={loadMore} disabled={disabled}>
            더 보기 ({items.length} / {total})
          </button>
        </div>
      )}
    </Section>
  );
}

export default function AttentionPanel({ onNavigate }: { onNavigate?: (s: string) => void }) {
  const [rows, setRows] = useState<AttentionItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  // INFO (e.g. soft-deleted awaiting manual cleanup) is hidden by default
  const [sev, setSev] = useState<Set<string>>(new Set(["CRITICAL", "ERROR", "WARN"]));
  const [doms, setDoms] = useState<Set<string>>(new Set());
  const [liveSort, setLiveSort] = useState<"desc" | "asc">("desc");
  const [histSort, setHistSort] = useState<"desc" | "asc">("desc");
  // multi-select (keyed by fingerprint), like the backup/scan bulk bars.
  const [selected, setSelected] = useState<Set<string>>(new Set());
  // 처리 내역/영구숨김은 forever-누적되는 목록이라 rows는 eager로 안 받고 COUNT(정수
  // 1개)만 받아 배지/빈상태 판정에 쓴다. 실제 rows는 각 Section이 펼칠 때 페이지 단위로
  // lazy 로딩한다(DismissedSection·ArchivedSection).
  const [dismTotal, setDismTotal] = useState(0);
  const [archTotal, setArchTotal] = useState(0);

  const refetch = useCallback(async () => {
    const [a, dt, at] = await Promise.all([
      operatorApi.dashboard.attention().catch(() => [] as AttentionItem[]),
      operatorApi.dashboard.dismissedAttentionCount().catch(() => 0),
      operatorApi.dashboard.archivedAttentionCount().catch(() => 0),
    ]);
    setRows(a);
    setDismTotal(dt);
    setArchTotal(at);
    // prune selections to items still present (an actioned item disappears).
    const prune = (prev: Set<string>, present: Set<string>) => {
      if (!prev.size) return prev;
      const next = new Set([...prev].filter((fp) => present.has(fp)));
      return next.size === prev.size ? prev : next;
    };
    const aFps = new Set(a.map((i) => i.fingerprint).filter(Boolean) as string[]);
    setSelected((prev) => prune(prev, aFps));
  }, []);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    refetch().finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [refetch]);

  // run a mutating action, then refresh; surface failures. Returns success so a
  // bulk caller can clear its selection only when the action actually applied.
  const run = useCallback(async (fn: () => Promise<unknown>): Promise<boolean> => {
    setBusy(true);
    try {
      await fn();
      await refetch();
      return true;
    } catch (e) {
      window.alert(`작업 실패: ${e instanceof Error ? e.message : String(e)}`);
      return false;
    } finally {
      setBusy(false);
    }
  }, [refetch]);

  const act: ItemActions = {
    onNavigate,
    onDismiss: (item) => {
      const p = ackPayload([item], "dismissed");
      if (p.length) run(() => operatorApi.dashboard.dismissAttention(p));
    },
    onAck: (item) => {
      const p = ackPayload([item], "ack");
      if (p.length) run(() => operatorApi.dashboard.dismissAttention(p));
    },
    onResolve: (item) => {
      const id = str(item.request_id);
      if (!id) return;
      const st = str(item.status) || "";
      // Abandon terminalizes the request (→ Failed/Cancelled) so its resource stops
      // Conflicting new requests. For states with a possible partial backend side
      // effect, make the operator verify first.
      const warn = BACKEND_UNVERIFIED_STATES.has(st)
        ? `이 요청은 '${st}' 상태입니다 — 백엔드에 변경이 부분 적용됐을 수 있습니다.\n` +
          "먼저 실제 백엔드 상태를 확인하세요. 이미 정상 적용됐다면 abandon 대신 요청 상세에서 확인하세요.\n\n" +
          "강제 종료하면 요청이 Failed로 종료되고, 이후 같은 대상에 대한 요청 차단(Conflict)이 풀립니다. 계속할까요?"
        : `이 요청('${st}')을 강제 종료(abandon)합니다.\n` +
          "요청이 종료되고 같은 대상의 요청 차단(Conflict)이 풀립니다. 계속할까요?";
      if (!window.confirm(warn)) return;
      const reason = window.prompt("요청 강제 종료(abandon) 사유를 입력하세요 (감사 기록):", "정체 요청 정리");
      if (!reason) return;
      run(() => operatorApi.dashboard.resolveRequest(id, "abandon", reason));
    },
    onDelete: (item) => {
      const id = str(item.job_id);
      if (!id) return;
      if (!window.confirm("이 data job 기록을 DMS에서 삭제할까요? (되돌릴 수 없음)")) return;
      run(() => operatorApi.dashboard.deleteDataJob(id));
    },
  };

  const dismissVisible = (items: AttentionItem[]) => {
    const p = ackPayload(items, "dismissed");
    if (!p.length) return;
    if (!window.confirm(`보이는 ${p.length}건을 숨길까요? (처리 내역에서 언제든 복원 가능)`)) return;
    run(() => operatorApi.dashboard.dismissAttention(p));
  };
  // ---- multi-select bulk actions (mirrors the backup/scan bulk bar) ----
  const toggleSel = (fp: string) =>
    setSelected((prev) => {
      const n = new Set(prev);
      n.has(fp) ? n.delete(fp) : n.add(fp);
      return n;
    });
  const toggleSelAll = (items: AttentionItem[], select: boolean) =>
    setSelected((prev) => {
      const n = new Set(prev);
      for (const it of items) {
        if (!it.fingerprint) continue;
        select ? n.add(it.fingerprint) : n.delete(it.fingerprint);
      }
      return n;
    });
  const clearSel = () => setSelected(new Set());

  // selected items still present in the data (independent of the severity/domain
  // view filter, so the count is stable while filtering).
  const selItems = rows.filter((r) => r.fingerprint && selected.has(r.fingerprint));
  const selDeletable = selItems.filter(canDelete);
  const selResolvable = selItems.filter(canResolve);

  const bulkAck = async () => {
    const p = ackPayload(selItems, "ack");
    if (!p.length) return;
    if (!window.confirm(`선택 ${p.length}건을 '확인(처리완료)'로 표시할까요? (처리 내역에 기록)`)) return;
    if (await run(() => operatorApi.dashboard.dismissAttention(p))) clearSel();
  };
  const bulkDismiss = async () => {
    const p = ackPayload(selItems, "dismissed");
    if (!p.length) return;
    if (!window.confirm(`선택 ${p.length}건을 숨길까요? (처리 내역에서 언제든 복원 가능)`)) return;
    if (await run(() => operatorApi.dashboard.dismissAttention(p))) clearSel();
  };
  const bulkDelete = async () => {
    if (!selDeletable.length) return;
    if (!window.confirm(`선택한 data job 기록 ${selDeletable.length}건을 DMS에서 삭제할까요? (되돌릴 수 없음)`)) return;
    const ok = await run(async () => {
      for (const it of selDeletable) {
        const id = str(it.job_id);
        if (id) await operatorApi.dashboard.deleteDataJob(id);
      }
    });
    if (ok) clearSel();
  };
  const bulkResolve = async () => {
    if (!selResolvable.length) return;
    const reason = window.prompt(
      `선택한 요청 ${selResolvable.length}건을 중단(abandon)합니다. 사유 입력 (감사 기록):`,
      "obsolete — 정리",
    );
    if (!reason) return;
    const ok = await run(async () => {
      for (const it of selResolvable) {
        const id = str(it.request_id);
        if (id) await operatorApi.dashboard.resolveRequest(id, "abandon", reason);
      }
    });
    if (ok) clearSel();
  };

  const sevCounts = useMemo(() => {
    const c: Record<string, number> = { CRITICAL: 0, ERROR: 0, WARN: 0, INFO: 0 };
    rows.forEach((r) => { const s = (str(r.severity) || "WARN").toUpperCase(); if (s in c) c[s] += 1; });
    return c;
  }, [rows]);
  const domList = useMemo(() => {
    const c = new Map<Domain, number>();
    rows.forEach((r) => { const d = domainOf(r.issue_type); c.set(d, (c.get(d) || 0) + 1); });
    return [...c.entries()].sort((a, b) => b[1] - a[1]);
  }, [rows]);

  const filtered = rows.filter((r) => {
    const s = (str(r.severity) || "WARN").toUpperCase();
    if (!sev.has(s)) return false;
    if (doms.size && !doms.has(domainOf(r.issue_type))) return false;
    return true;
  });
  const sevRank = (r: AttentionItem) => SEV_RANK[(str(r.severity) || "WARN").toUpperCase()] ?? 2;
  const cmp = (dir: "desc" | "asc") => (a: AttentionItem, b: AttentionItem) => {
    const ta = timeOf(a), tb = timeOf(b);
    if (ta !== tb) return dir === "desc" ? tb - ta : ta - tb;
    return sevRank(a) - sevRank(b);
  };
  const live = filtered.filter((r) => r.category !== "history").sort(cmp(liveSort));
  const history = filtered.filter((r) => r.category === "history").sort(cmp(histSort));

  const toggle = (set: Set<string>, setter: (s: Set<string>) => void, v: string) => {
    const n = new Set(set);
    if (n.has(v)) n.delete(v); else n.add(v);
    setter(n);
  };
  const sevTone: Record<string, string> = { CRITICAL: "crit", ERROR: "err", WARN: "warn", INFO: "info" };

  const liveErr = live.filter((r) => ["CRITICAL", "ERROR"].includes((str(r.severity) || "").toUpperCase())).length;
  const liveBadge = (
    <span className={live.length ? (liveErr ? "err-num" : "muted small") : "ok-num"}>
      {live.length ? `${live.length}건${liveErr ? ` · 긴급 ${liveErr}` : ""}` : "0건 ✅"}
    </span>
  );
  const histBadge = <span className="muted small">{history.length}건</span>;

  if (loading) return <Loading rows={4} />;
  if (rows.length === 0 && dismTotal === 0 && archTotal === 0)
    return <p className="muted">조치 필요한 항목이 없습니다. ✅</p>;

  return (
    <div className={busy ? "attn-busy" : undefined}>
      <div className="attn-filters">
        {SEVERITIES.map((s) => (
          <button key={s} className={`attn-chip sev-${sevTone[s]} ${sev.has(s) ? "on" : ""}`}
            onClick={() => toggle(sev, setSev, s)} title={sev.has(s) ? `${s} 숨기기` : `${s} 보기`}>
            {s} <b>{sevCounts[s]}</b>
          </button>
        ))}
        {domList.length > 1 && <span className="attn-sep" />}
        {domList.length > 1 && domList.map(([d, n]) => (
          <button key={d} className={`attn-chip dom ${doms.size === 0 || doms.has(d) ? "on" : ""}`}
            onClick={() => toggle(doms, setDoms, d)} title="도메인 필터">
            {DOMAIN_LABEL[d]} <b>{n}</b>
          </button>
        ))}
      </div>

      <p className="muted small attn-legend">
        <b className="attn-legend-ack">확인</b> = 처리 완료로 DMS에 반영(모든 화면 공통 · 기록 보존) ·{" "}
        <b className="attn-legend-hide">숨김</b> = 이 포탈에서만 숨김
      </p>

      {selItems.length > 0 && (
        <div className="bulk-bar">
          <span className="bulk-count">{selItems.length}개 선택</span>
          <button className="primary mini" disabled={busy} onClick={bulkAck}
            title="확인·처리 완료 — DMS에 반영되어 모든 화면의 '조치 필요'에서 제외 (기록 보존)">
            확인 ({selItems.length})
          </button>
          <button className="mini" disabled={busy} onClick={bulkDismiss}
            title="이 포탈에서만 숨김 (해당없음/무시) — 처리 내역에서 복원 가능">
            숨김 ({selItems.length})
          </button>
          <button className="mini danger" disabled={busy || selDeletable.length === 0} onClick={bulkDelete}>
            기록 삭제 ({selDeletable.length})
          </button>
          {selResolvable.length > 0 && (
            <button className="ghost mini" disabled={busy} onClick={bulkResolve}>
              요청 중단 ({selResolvable.length})
            </button>
          )}
          <button className="ghost mini" onClick={clearSel}>선택 해제</button>
        </div>
      )}

      <Section title="현재 조치 필요" badge={liveBadge} defaultOpen>
        <Group items={live} dir={liveSort}
          onToggleSort={() => setLiveSort((d) => (d === "desc" ? "asc" : "desc"))}
          onDismissVisible={dismissVisible} act={act}
          selected={selected} onToggleSel={toggleSel} onToggleSelAll={toggleSelAll}
          empty="현재 조치 필요한 항목이 없습니다. ✅" />
      </Section>
      <Section title="과거 작업 이력 (종료된 작업·결과)" badge={histBadge}>
        <Group items={history} dir={histSort}
          onToggleSort={() => setHistSort((d) => (d === "desc" ? "asc" : "desc"))}
          onDismissVisible={dismissVisible} act={act}
          selected={selected} onToggleSel={toggleSel} onToggleSelAll={toggleSelAll}
          empty="이력 없음" />
      </Section>
      <DismissedSection total={dismTotal} busy={busy} onChanged={refetch} />
      {archTotal > 0 && (
        <ArchivedSection total={archTotal} busy={busy} onChanged={refetch} />
      )}
    </div>
  );
}
