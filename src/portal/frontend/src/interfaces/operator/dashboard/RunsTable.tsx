import { useEffect, useState } from "react";
import { operatorApi, type RunRow } from "../../../api";
import { fmtTime } from "./helpers";
import Section from "./Section";
import Loading from "../../../components/Loading";
import InfoHint from "../../../components/InfoHint";

// "워커 실행 현황" — the run/lease layer of the request→plan→run state machine. It
// splits three genuinely different things that used to sit in one flat table:
//   ① 지금 실행 중 (Claimed/Running/Applying/Verifying) — live worker activity
//   ② 확인 대기   (Blocked)                              — DM preview awaiting confirm (normal)
//   ③ 정체·복구   (StaleClaim/RecoveryNeeded/…)          — actually stuck → 조치 필요
// ③ is delegated to the 조치 필요 panel (which carries the resolution actions), so we
// only summarise + link it here rather than duplicating a raw table.

const CONFIRM_WAIT = "Blocked";

// request statuses that are still ACTIONABLE in the 조치 필요 panel (request-attention).
// A stuck run whose request is one of these = the operator can still act on the REQUEST
// there; anything else = the request already concluded and the run is auto-reconciled
// by the DMS recovery sweep (runs are never cleaned by hand in 조치 필요).
const ACTIONABLE_REQ = new Set([
  "StaleClaim", "RecoveryNeeded", "VerificationFailed",
  "UnknownAfterSideEffect", "BackendApplyFailed",
]);

// operation_kind → 친화 라벨 (기술어는 유지). data.* → 데이터, filesystem.* → 파일시스템,
// kubernetes.namespace_quota.* → 쿼터.
function opLabel(op?: string): string {
  if (!op) return "—";
  if (op.startsWith("data.")) return "데이터";
  if (op.startsWith("filesystem")) return "파일시스템";
  if (op.includes("quota")) return "쿼터";
  if (op.startsWith("identity")) return "아이덴티티";
  return op;
}
function opDetail(op?: string): string {
  // trailing verb, e.g. filesystem.create → create, data.sync → sync
  if (!op) return "";
  const dot = op.lastIndexOf(".");
  return dot >= 0 ? op.slice(dot + 1) : op;
}

const STATE_LABEL: Record<string, string> = {
  Claimed: "할당됨", Running: "실행 중", Applying: "적용 중", Verifying: "검증 중",
  Blocked: "확인 대기", StaleClaim: "리스 만료", RecoveryNeeded: "복구 필요",
  UnknownAfterSideEffect: "결과 불명", BackendApplyFailed: "적용 실패",
};

// elapsed since a start time, compact ("3분"/"2시간"/"1일").
function ago(iso?: string): string {
  if (!iso) return "—";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "—";
  const s = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (s < 60) return `${s}초`;
  if (s < 3600) return `${Math.floor(s / 60)}분`;
  if (s < 86400) return `${Math.floor(s / 3600)}시간`;
  return `${Math.floor(s / 86400)}일`;
}

// worker liveness from the run's lease + heartbeat: is the worker still alive and
// holding this run, or has it gone quiet (lease about to be reclaimed)?
function liveness(r: RunRow): { cls: string; label: string; title: string } {
  const hbAge = r.heartbeat_at
    ? Math.floor((Date.now() - new Date(r.heartbeat_at).getTime()) / 1000)
    : null;
  const leaseGone =
    r.lease_expires_at != null &&
    new Date(r.lease_expires_at).getTime() < Date.now();
  const hbTxt = hbAge == null ? "heartbeat 없음" : `heartbeat ${ago(r.heartbeat_at)} 전`;
  if (leaseGone) return { cls: "live-bad", label: "리스 만료", title: `${hbTxt} · 리스 만료 — 곧 회수` };
  if (r.lease_expiring_soon) return { cls: "live-warn", label: "리스 임박", title: `${hbTxt} · 리스 곧 만료` };
  if (hbAge != null && hbAge > 120) return { cls: "live-warn", label: "응답 지연", title: hbTxt };
  return { cls: "live-ok", label: "정상", title: hbTxt };
}

// A readable target from the run's resource_key. Data-job keys are verbose
// (data.<op>:<srcStore>:<srcPath>:<dstStore>:<dstPath>:sha256:<hash>) — collapse them
// to "src → dst" (or a single path for scan/rm); other keys pass through as-is.
function target(r: RunRow): string {
  const k = r.resource_key;
  if (!k) return r.request_id ? `${r.request_id.slice(0, 12)}…` : "—";
  if (k.startsWith("data.")) {
    const parts = k.split(":");
    const sha = parts.indexOf("sha256");
    const body = (sha > 0 ? parts.slice(1, sha) : parts.slice(1)).filter(Boolean);
    if (body.length >= 4) return `${body[0]}:${body[1]} → ${body[2]}:${body[3]}`;
    if (body.length >= 2) return `${body[0]}:${body[1]}`;
    return body.join(":") || k;
  }
  return k;
}

export default function RunsTable({
  defaultOpen = false,
  onNavigate,
}: {
  defaultOpen?: boolean;
  onNavigate?: (section: string, focus?: { kind: "request"; value: string }) => void;
}) {
  const [active, setActive] = useState<RunRow[]>([]);
  const [stale, setStale] = useState<RunRow[]>([]);
  const [truncated, setTruncated] = useState(false);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    operatorApi.dashboard.runs().then((r) => {
      setActive(r.active.data || []);
      setStale(r.stale.data || []);
      setTruncated(Boolean(r.active.truncated || r.stale.truncated));
    }).catch(() => { setActive([]); setStale([]); setTruncated(false); })
      .finally(() => setLoading(false));
  }, []);

  // Exclude runs whose request was hidden in 조치 필요 (dismiss/ack) — consistency:
  // don't flag work as needing attention here while 조치 필요 hides it. Count them so
  // we can show a small "숨김 N" note (no silent omission).
  const activeVisible = active.filter((r) => !r._hidden);
  const staleVisible = stale.filter((r) => !r._hidden);
  const hiddenWaiting = stale.filter((r) => r._hidden && r.state === CONFIRM_WAIT).length;
  const hiddenAttn = stale.filter((r) => r._hidden && r.state !== CONFIRM_WAIT).length;
  const waiting = staleVisible.filter((r) => r.state === CONFIRM_WAIT);   // ② 확인 대기
  const attention = staleVisible.filter((r) => r.state !== CONFIRM_WAIT); // ③ 정체·복구
  // ③ split: request still actionable in 조치 필요 vs already concluded (auto-cleaned).
  const openStuck = attention.filter((r) => ACTIONABLE_REQ.has(r.request_status || ""));
  const orphanStuck = attention.filter((r) => !ACTIONABLE_REQ.has(r.request_status || ""));

  const badge = (
    <span className="snm-badge">
      <span className="muted small">
        실행 중 <b className={activeVisible.length ? "" : "muted"}>{activeVisible.length}</b>
        {waiting.length > 0 && <> · 확인 대기 <b>{waiting.length}</b></>}
        {attention.length > 0 && <> · <span className="err-num">정체·복구 {attention.length}</span></>}
        {(hiddenAttn + hiddenWaiting) > 0 && <> · <span className="muted">숨김 {hiddenAttn + hiddenWaiting}</span></>}
      </span>
      {truncated && <span className="chip tone-warn">일부만 표시</span>}
      <InfoHint label="워커 실행 현황 설명">
        요청이 플랜으로 바뀌면 <strong>RM/DM 워커</strong>가 리스를 잡고 실제로 처리(run)합니다.
        <br /><strong>실행 중</strong>=지금 워커가 돌리는 작업, <strong>확인 대기</strong>=데이터
        잡 프리뷰 후 승인 대기(정상), <strong>정체·복구</strong>=실제 조치가 필요한 run(‘조치
        필요’에서 해소).
      </InfoHint>
    </span>
  );

  const goRequest = (rid?: string) =>
    rid && onNavigate?.("dashboard-activity", { kind: "request", value: rid });

  return (
    <Section title="워커 실행 현황" badge={badge} defaultOpen={defaultOpen}>
      {loading ? (
        <Loading rows={3} />
      ) : (
        <div className="wrun">
          {/* ① 지금 실행 중 */}
          <div className="wrun-group">
            <div className="wrun-h">지금 실행 중 <span className="muted small">({activeVisible.length})</span></div>
            {activeVisible.length === 0 ? (
              <p className="muted small ok-num">현재 실행 중인 작업이 없습니다 ✅</p>
            ) : (
              <table className="grid wrun-grid">
                <thead><tr>
                  <th>작업</th><th>요청자</th><th>대상</th><th>경과</th><th>단계</th><th>워커</th><th></th>
                </tr></thead>
                <tbody>
                  {activeVisible.map((r) => {
                    const lv = liveness(r);
                    return (
                      <tr key={r.run_id}>
                        <td data-label="작업">
                          <span className="wrun-op">{opLabel(r.operation_kind)}</span>
                          <span className="muted small"> {opDetail(r.operation_kind)}</span>
                        </td>
                        <td data-label="요청자" className="small">{r.requester_id || "—"}</td>
                        <td data-label="대상" className="mono small" title={r.resource_key || r.request_id}>{target(r)}</td>
                        <td data-label="경과" title={fmtTime(r.started_at)}>{ago(r.started_at)}</td>
                        <td data-label="단계"><span className="chip tone-info">{STATE_LABEL[r.state] || r.state}</span></td>
                        <td data-label="워커">
                          <span className={`wrun-live ${lv.cls}`} title={`${r.worker_id || "?"} · ${lv.title}`}>
                            ● {r.worker_role || "?"} · {lv.label}
                          </span>
                        </td>
                        <td data-label="" className="row-actions">
                          {onNavigate && r.request_id && (
                            <button className="attn2-hide attn2-detail-link" title="요청 상세 보기"
                              onClick={() => goRequest(r.request_id)}>상세 →</button>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>

          {/* ② 확인 대기 (DM preview → confirm) */}
          {waiting.length > 0 && (
            <div className="wrun-group">
              <div className="wrun-h">확인 대기 <span className="muted small">({waiting.length}) · 프리뷰 승인/취소 대기{hiddenWaiting > 0 ? ` · 숨김 ${hiddenWaiting}건` : ""}</span></div>
              <table className="grid wrun-grid">
                <thead><tr>
                  <th>작업</th><th>요청자</th><th>대상</th><th>대기</th><th></th>
                </tr></thead>
                <tbody>
                  {waiting.map((r) => {
                    const isScan = (r.operation_kind || "").includes("scan");
                    const dest = isScan ? "scan" : "backup";
                    return (
                      <tr key={r.run_id}>
                        <td data-label="작업"><span className="wrun-op">{opLabel(r.operation_kind)}</span>
                          <span className="muted small"> {opDetail(r.operation_kind)}</span></td>
                        <td data-label="요청자" className="small">{r.requester_id || "—"}</td>
                        <td data-label="대상" className="mono small" title={r.resource_key || r.request_id}>{target(r)}</td>
                        <td data-label="대기" title={fmtTime(r.started_at)}>{ago(r.started_at)}</td>
                        <td data-label="" className="row-actions">
                          {onNavigate && (
                            <button className="attn2-hide attn2-detail-link"
                              title={`${isScan ? "데이터 스캔" : "데이터 백업"}에서 확인`}
                              onClick={() => onNavigate(dest)}>{isScan ? "스캔" : "백업"}에서 확인 →</button>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {/* ③ 정체·복구 — run 자체는 조치 필요에서 정리되지 않는다. 요청이 아직 열린
              run은 조치 필요에서 '요청'을 처리하면 시스템이 run을 회수하고, 요청이 이미
              종료된 잔여 run은 DMS 복구 스윕이 자동 정리한다. */}
          {attention.length > 0 && (
            <div className="wrun-group">
              <div className="wrun-h">정체·복구 <span className="muted small">({attention.length}) · 워커 리스 만료{hiddenAttn > 0 ? ` · 숨김 ${hiddenAttn}건` : ""}</span></div>
              {openStuck.length > 0 && (
                <div className="wrun-attn">
                  <span className="err-num">회수 필요 {openStuck.length}건</span>
                  <span className="muted small">— run은 여기서 직접 정리하지 않습니다. 해당 <b>요청</b>을 조치 필요에서 처리(재처리·중단)하면 시스템이 run을 회수합니다.</span>
                  {onNavigate && (
                    <button className="mini" onClick={() => onNavigate("dashboard-attention")}>조치 필요 열기 →</button>
                  )}
                </div>
              )}
              {orphanStuck.length > 0 && (
                <p className="muted small wrun-orphan-note">
                  종료된 요청의 잔여 run {orphanStuck.length}건 — DMS가 자동 정리합니다(별도 조치 불필요).
                </p>
              )}
              <ul className="wrun-attn-list">
                {attention.map((r) => {
                  const open = ACTIONABLE_REQ.has(r.request_status || "");
                  return (
                    <li key={r.run_id}>
                      <span className="chip tone-low">{STATE_LABEL[r.state] || r.state}</span>
                      <span className="wrun-op">{opLabel(r.operation_kind)}</span>
                      <span className="muted small">{r.requester_id || ""}</span>
                      <span className="mono small">{target(r)}</span>
                      <span className="muted small">{ago(r.started_at)} 전</span>
                      <span className={`chip ${open ? "tone-warn" : "tone-low"}`}
                        title={open ? "요청이 아직 열려 있어 조치 필요에서 처리 가능" : "요청 종료됨 — 자동 정리 예정"}>
                        {open ? "요청 처리 필요" : "자동 정리 예정"}
                      </span>
                    </li>
                  );
                })}
              </ul>
            </div>
          )}
        </div>
      )}
    </Section>
  );
}
