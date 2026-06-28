import { useEffect, useMemo, useState, type ReactNode } from "react";
import { operatorApi, type AttentionItem } from "../../../api";
import { fmtAgo, fmtTime } from "./helpers";
import Section from "./Section";

// ---- severity ----
const SEVERITIES = ["CRITICAL", "ERROR", "WARN", "INFO"] as const;
const SEV_RANK: Record<string, number> = { CRITICAL: 0, ERROR: 1, WARN: 2, INFO: 3 };

// ---- domains (derived from issue_type prefix) ----
type Domain = "request" | "storage" | "agent" | "quota" | "filesystem" | "datajob" | "etc";
const DOMAIN_LABEL: Record<Domain, string> = {
  request: "요청", storage: "스토리지", agent: "에이전트",
  quota: "쿼터", filesystem: "파일시스템", datajob: "데이터 잡", etc: "기타",
};
// domain → operator section to jump to (undefined = no portal destination)
const DOMAIN_NAV: Partial<Record<Domain, { section: string; label: string }>> = {
  request: { section: "dashboard-activity", label: "액티비티에서 요청 보기" },
  storage: { section: "storage", label: "스토리지 인벤토리 열기" },
  datajob: { section: "backup", label: "데이터 백업에서 보기" },
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
  // Korean action (ISSUE_META / request-status) first; DMS recommended_action (영문)
  // is a fallback only for issue types we have not localized yet.
  if (item.issue_type === "request_attention") {
    return REQ_STATUS_ACTION[str(item.status) || ""] || "요청 상세에서 상태 확인 후 재처리/취소";
  }
  return (
    ISSUE_META[item.issue_type]?.action ||
    str(item.recommended_action) ||
    "항목을 펼쳐 상세를 확인하세요"
  );
}
// short identifier shown on the collapsed row
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
const DETAIL_SKIP = new Set(["issue_type", "severity", "category", "recommended_action"]);

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

function Item({ item, onNavigate }: { item: AttentionItem; onNavigate?: (s: string) => void }) {
  const [open, setOpen] = useState(false);
  const dom = domainOf(item.issue_type);
  const sev = (str(item.severity) || "WARN").toUpperCase();
  const ident = identOf(item);
  const when = str(item.updated_at) || str(item.reported_at) || str(item.last_seen) || str(item.expires_at);
  const nav = DOMAIN_NAV[dom];
  return (
    <div className={`attn2 attn2-${sev.toLowerCase()}`}>
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
      {open && (
        <div className="attn2-detail">
          <dl className="spec-grid">{detailRows(item)}</dl>
          {nav && onNavigate && (
            <div className="attn2-cta">
              <button className="mini primary" onClick={() => onNavigate(nav.section)}>{nav.label} →</button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Group({ items, onNavigate, empty }: {
  items: AttentionItem[]; onNavigate?: (s: string) => void; empty: string;
}) {
  if (items.length === 0) return <p className="muted small">{empty}</p>;
  return (
    <div className="attn2-list">
      {items.map((r, i) => <Item key={`${r.issue_type}-${i}`} item={r} onNavigate={onNavigate} />)}
    </div>
  );
}

export default function AttentionPanel({ onNavigate }: { onNavigate?: (s: string) => void }) {
  const [rows, setRows] = useState<AttentionItem[]>([]);
  // INFO (e.g. soft-deleted awaiting manual cleanup) is hidden by default
  const [sev, setSev] = useState<Set<string>>(new Set(["CRITICAL", "ERROR", "WARN"]));
  const [doms, setDoms] = useState<Set<string>>(new Set());
  useEffect(() => {
    operatorApi.dashboard.attention().then(setRows).catch(() => setRows([]));
  }, []);

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
  const live = filtered.filter((r) => r.category !== "history")
    .sort((a, b) => SEV_RANK[(str(a.severity) || "WARN").toUpperCase()] - SEV_RANK[(str(b.severity) || "WARN").toUpperCase()]);
  const history = filtered.filter((r) => r.category === "history");

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

  return (
    <>
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

      {rows.length === 0 ? (
        <p className="muted">조치 필요한 항목이 없습니다. ✅</p>
      ) : (
        <>
          <Section title="현재 조치 필요" badge={liveBadge} defaultOpen>
            <Group items={live} onNavigate={onNavigate} empty="현재 조치 필요한 항목이 없습니다. ✅" />
          </Section>
          <Section title="과거 작업 이력 (종료된 작업·결과)" badge={histBadge}>
            <Group items={history} onNavigate={onNavigate} empty="이력 없음" />
          </Section>
        </>
      )}
    </>
  );
}
