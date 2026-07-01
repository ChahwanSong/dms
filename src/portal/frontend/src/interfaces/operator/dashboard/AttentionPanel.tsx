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
  UnknownAfterSideEffect: "결과 불명 (수동 확인)", BackendApplyFailed: "백엔드 적용 실패",
};
const REQ_STATUS_ACTION: Record<string, string> = {
  Blocked: "제어 상태(점검/드레인/스케줄링) 확인·해제 후 재처리",
  StaleClaim: "워커 동작 확인 → 자동 회수 대기 또는 워커 재기동",
  RecoveryNeeded: "백엔드 상태 점검 후 재처리 또는 취소",
  UnknownAfterSideEffect: "실제 백엔드 상태를 직접 확인 후 DB와 동기화",
  BackendApplyFailed: "원인(권한/연결/백엔드) 수정 후 재요청",
};
// DMS only allows resolve/abandon for these stuck request states.
const RESOLVABLE_REQUEST_STATES = new Set(["UnknownAfterSideEffect", "BackendApplyFailed"]);

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
        <button type="button" className="attn2-hide" title="이 항목 숨김 (해당없음/처리됨 — 처리 내역에서 복원 가능)"
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
              <button className="mini" onClick={() => act.onResolve(item)}>요청 중단(abandon)</button>
            )}
            {canDelete(item) && (
              <button className="mini danger" onClick={() => act.onDelete(item)}>기록 삭제</button>
            )}
            <button className={needsManualAck(item) ? "mini primary" : "mini"} onClick={() => act.onAck(item)}
              title="운영자가 확인·처리함 (예: 수동 정리 완료) — 처리 내역에 기록">확인(처리완료)</button>
            <button className="mini ghost" onClick={() => act.onDismiss(item)}
              title="해당없음/무시 — 처리 내역에 숨김으로 기록">숨김</button>
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

function DismissedList({
  rows, dir, onToggleSort, selected, onToggleSel, onToggleSelAll, onClearSel,
  onUndismiss, onUndismissAll, onAck, onDelete, onResolve, busy,
}: {
  rows: DismissedItem[];
  dir: "desc" | "asc";
  onToggleSort: () => void;
  selected: Set<string>;
  onToggleSel: (fp: string) => void;
  onToggleSelAll: (rows: DismissedItem[], select: boolean) => void;
  onClearSel: () => void;
  onUndismiss: (fingerprints: string[]) => void;
  onUndismissAll: () => void;
  onAck: (rows: DismissedItem[]) => void;
  onDelete: (rows: DismissedItem[]) => void;
  onResolve: (rows: DismissedItem[]) => void;
  busy: boolean;
}) {
  const [purgeAt, setPurgeAt] = useState("");
  if (rows.length === 0) return <p className="muted small">처리 내역이 없습니다.</p>;
  const allSel = rows.length > 0 && rows.every((d) => selected.has(d.fingerprint));
  const sel = rows.filter((d) => selected.has(d.fingerprint));
  const selAckable = sel.filter((d) => d.kind !== "ack");
  const selDeletable = sel.filter(dCanDelete);
  const selResolvable = sel.filter(dCanResolve);

  // the time we show/sort/purge by: the action-required item's report time
  // (captured at dismiss), falling back to the dismiss time for legacy records.
  const whenOf = (d: DismissedItem) => d.item_at || d.dismissed_at;
  // delete (purge) every record whose report time is at/before the chosen time.
  // Reuses undismiss (remove_dismissals) — housekeeping for the accruing 처리 내역.
  const purgeBefore = () => {
    if (!purgeAt) return;
    const cutoff = new Date(purgeAt).getTime();
    if (Number.isNaN(cutoff)) return;
    const victims = rows.filter((d) => {
      const w = whenOf(d);
      const t = w ? new Date(w).getTime() : NaN;
      return !Number.isNaN(t) && t <= cutoff;
    });
    if (!victims.length) { window.alert("해당 시각 이전의 처리 내역이 없습니다."); return; }
    if (!window.confirm(
      `${fmtTime(purgeAt)} 이전 처리 내역 ${victims.length}건을 정리(제거)할까요?\n` +
      "(이미 해소된 항목은 사라지고, 아직 유효한 항목은 조치 필요에 다시 나타납니다)"
    )) return;
    onUndismiss(victims.map((d) => d.fingerprint));
  };
  return (
    <>
      <div className="attn-sec-tools dism-tools">
        <label className="check-cell" style={{ marginRight: "auto" }} title="전체 선택">
          <input type="checkbox" checked={allSel}
            onChange={() => onToggleSelAll(rows, !allSel)} aria-label="전체 선택" />
        </label>
        <span className="muted small">오래된 항목 정리:</span>
        <input type="datetime-local" className="dism-purge-at" value={purgeAt}
          onChange={(e) => setPurgeAt(e.target.value)} title="이 시각 이전의 처리 내역을 정리(제거)" />
        <button className="attn-sort" disabled={!purgeAt || busy} onClick={purgeBefore}
          title="입력한 시각 이전의 처리 내역 기록을 정리(제거) — 이미 해소된 항목 청소용">이전 정리</button>
        <button className="attn-sort" onClick={onToggleSort} title="리포트 시각순 정렬 전환">
          {dir === "desc" ? "최신순 ↓" : "오래된순 ↑"}
        </button>
        <button className="attn-sort" onClick={onUndismissAll}
          title="처리 내역을 모두 조치 필요로 복원">모두 복원</button>
      </div>
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
                      <span className={`chip ${isAck ? "tone-ok" : "tone-low"}`}>{isAck ? "확인됨" : "숨김"}</span>
                      <span className="attn2-dom">{DOMAIN_LABEL[domainOf(d.issue_type || "")]}</span>
                      <span className="attn2-label">{d.label || d.issue_type || d.fingerprint}</span>
                      {ident && <span className="attn2-ident mono">{ident}</span>}
                    </span>
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
              </div>
            </div>
          );
        })}
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

export default function AttentionPanel({ onNavigate }: { onNavigate?: (s: string) => void }) {
  const [rows, setRows] = useState<AttentionItem[]>([]);
  const [dismissed, setDismissed] = useState<DismissedItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  // INFO (e.g. soft-deleted awaiting manual cleanup) is hidden by default
  const [sev, setSev] = useState<Set<string>>(new Set(["CRITICAL", "ERROR", "WARN"]));
  const [doms, setDoms] = useState<Set<string>>(new Set());
  const [liveSort, setLiveSort] = useState<"desc" | "asc">("desc");
  const [histSort, setHistSort] = useState<"desc" | "asc">("desc");
  const [dismSort, setDismSort] = useState<"desc" | "asc">("desc");
  // multi-select (keyed by fingerprint), like the backup/scan bulk bars.
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [dismSel, setDismSel] = useState<Set<string>>(new Set());

  const refetch = useCallback(async () => {
    const [a, d] = await Promise.all([
      operatorApi.dashboard.attention().catch(() => [] as AttentionItem[]),
      operatorApi.dashboard.dismissedAttention().catch(() => [] as DismissedItem[]),
    ]);
    setRows(a);
    setDismissed(d);
    // prune selections to items still present (an actioned item disappears).
    const prune = (prev: Set<string>, present: Set<string>) => {
      if (!prev.size) return prev;
      const next = new Set([...prev].filter((fp) => present.has(fp)));
      return next.size === prev.size ? prev : next;
    };
    const aFps = new Set(a.map((i) => i.fingerprint).filter(Boolean) as string[]);
    const dFps = new Set(d.map((i) => i.fingerprint));
    setSelected((prev) => prune(prev, aFps));
    setDismSel((prev) => prune(prev, dFps));
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
      const reason = window.prompt("요청 중단(abandon) 사유를 입력하세요 (감사 기록):", "obsolete — 정리");
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
  const undismiss = (fingerprints: string[]) =>
    run(() => operatorApi.dashboard.undismissAttention(fingerprints));
  const undismissAll = () => {
    if (!dismissed.length) return;
    if (!window.confirm(
      `처리 내역 ${dismissed.length}건을 모두 조치 필요로 복원할까요?\n` +
      "(이미 해소된 항목은 사라지고, 아직 유효한 항목은 다시 표시됩니다)"
    )) return;
    undismiss(dismissed.map((d) => d.fingerprint));
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

  // ---- 처리 내역 (dismissed/ack) section selection + bulk ----
  const dismToggleSel = (fp: string) =>
    setDismSel((prev) => { const n = new Set(prev); n.has(fp) ? n.delete(fp) : n.add(fp); return n; });
  const dismToggleSelAll = (items: DismissedItem[], select: boolean) =>
    setDismSel((prev) => {
      const n = new Set(prev);
      for (const d of items) select ? n.add(d.fingerprint) : n.delete(d.fingerprint);
      return n;
    });
  const dismClearSel = () => setDismSel(new Set());

  const dismBulkAck = async (items: DismissedItem[]) => {
    if (!items.length) return;
    if (await run(() => operatorApi.dashboard.dismissAttention(dismToAckPayload(items, "ack")))) dismClearSel();
  };
  const dismBulkDelete = async (items: DismissedItem[]) => {
    if (!items.length) return;
    if (!window.confirm(`선택한 data job 기록 ${items.length}건을 DMS에서 삭제할까요? (되돌릴 수 없음)`)) return;
    const ok = await run(async () => {
      for (const d of items) if (d.job_id) await operatorApi.dashboard.deleteDataJob(d.job_id);
      // the underlying items are gone now — drop their (orphaned) 처리 내역 rows too.
      await operatorApi.dashboard.undismissAttention(items.map((d) => d.fingerprint));
    });
    if (ok) dismClearSel();
  };
  const dismBulkResolve = async (items: DismissedItem[]) => {
    if (!items.length) return;
    const reason = window.prompt(
      `선택한 요청 ${items.length}건을 중단(abandon)합니다. 사유 입력 (감사 기록):`, "obsolete — 정리");
    if (!reason) return;
    const ok = await run(async () => {
      for (const d of items) if (d.request_id) await operatorApi.dashboard.resolveRequest(d.request_id, "abandon", reason);
      await operatorApi.dashboard.undismissAttention(items.map((d) => d.fingerprint));
    });
    if (ok) dismClearSel();
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
  const dismBadge = <span className="muted small">{dismissed.length}건</span>;
  // sort by the item's report time (item_at), falling back to dismiss time.
  const dismMs = (d: DismissedItem) => {
    const w = d.item_at || d.dismissed_at;
    return w ? new Date(w).getTime() || 0 : 0;
  };
  const dismissedSorted = [...dismissed].sort((a, b) =>
    dismSort === "desc" ? dismMs(b) - dismMs(a) : dismMs(a) - dismMs(b));

  if (loading) return <Loading rows={4} />;
  if (rows.length === 0 && dismissed.length === 0)
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

      {selItems.length > 0 && (
        <div className="bulk-bar">
          <span className="bulk-count">{selItems.length}개 선택</span>
          <button className="primary mini" disabled={busy} onClick={bulkAck}
            title="운영자가 확인·처리함 (수동 정리 완료 등) — 처리 내역에 기록">
            확인 ({selItems.length})
          </button>
          <button className="mini" disabled={busy} onClick={bulkDismiss}
            title="해당없음/무시 — 처리 내역에 숨김으로 기록">
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
      <Section title="처리 내역 (확인·숨김)" badge={dismBadge}>
        <DismissedList rows={dismissedSorted}
          dir={dismSort} onToggleSort={() => setDismSort((d) => (d === "desc" ? "asc" : "desc"))}
          selected={dismSel} onToggleSel={dismToggleSel} onToggleSelAll={dismToggleSelAll}
          onClearSel={dismClearSel} onUndismiss={undismiss} onUndismissAll={undismissAll}
          onAck={dismBulkAck} onDelete={dismBulkDelete} onResolve={dismBulkResolve} busy={busy} />
      </Section>
    </div>
  );
}
