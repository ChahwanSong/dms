import { Fragment, useEffect, useState } from "react";
import { ApiError, operatorApi, type BackupBatch } from "../../../api";
import { batchStatus } from "./helpers";
import { OptionChips, SpecGrid, optionEntries } from "./ui";
import InfoHint from "../../../components/InfoHint";
import BackupBatchForm from "./BackupBatchForm";
import BackupBatchDetail from "./BackupBatchDetail";

// Data backup: register lists of DMS DM sync jobs and run them as mirror backups
// (preview -> approve -> execute). This is the list of batches; click one to open
// its detail (jobs, preview summary, run/cancel).
export default function BackupBatches() {
  const [batches, setBatches] = useState<BackupBatch[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setBatches(await operatorApi.backup.list());
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  function toggle(id: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  async function remove(b: BackupBatch) {
    if (!window.confirm(`배치 '${b.name}'을(를) 삭제합니다. 계속할까요?`)) return;
    try {
      await operatorApi.backup.remove(b.id);
      setNotice(`'${b.name}' 삭제됨`);
      await load();
    } catch (e) {
      setError(errMsg(e));
    }
  }

  if (openId) {
    return (
      <BackupBatchDetail
        batchId={openId}
        onBack={() => {
          setOpenId(null);
          load();
        }}
      />
    );
  }

  return (
    <div className="inventory">
      <div className="inv-head">
        <h2 className="title-with-hint">
          데이터 백업
          <InfoHint label="데이터 백업 설명">
            <strong className="hint-title">데이터 백업</strong>
            <p className="hint-lead">DMS DM sync로 스토리지 간 데이터를 미러 복사합니다.</p>
            <div className="hint-flow">
              <span>등록</span>
              <i className="flow-arrow">→</i>
              <span>Preview</span>
              <i className="flow-arrow">→</i>
              <span>승인</span>
              <i className="flow-arrow">→</i>
              <span>실행</span>
            </div>
            <ul className="hint-list">
              <li>
                <b>Preview</b> — 비파괴 dry-run으로 영향(파일·용량·삭제 수)을 먼저 확인합니다.
              </li>
              <li>
                <b>승인</b> — 운영자가 확인한 항목만 실행합니다(선택·단계 승인 가능).
              </li>
              <li>
                <b>소유권 보존</b> — root로 실행해 원본 uid/gid·권한·시각을 그대로 유지합니다.
              </li>
              <li>
                <b>--delete</b> — 원본에 없는 파일을 대상에서도 삭제하는 완전 미러입니다.
              </li>
            </ul>
          </InfoHint>
        </h2>
        <div className="inv-actions">
          <button className="ghost" onClick={load} disabled={loading}>
            새로고침
          </button>
          <button className="primary" onClick={() => setShowForm(true)}>
            + 새 배치
          </button>
        </div>
      </div>

      {notice && <div className="banner ok">{notice}</div>}
      {error && <div className="banner err">{error}</div>}

      {loading ? (
        <div className="muted">불러오는 중…</div>
      ) : batches.length === 0 ? (
        <div className="muted">등록된 백업 배치가 없습니다. “+ 새 배치”로 시작하세요.</div>
      ) : (
        <table className="grid batch-list">
          <thead>
            <tr>
              <th className="col-toggle"></th>
              <th>이름</th>
              <th>상태</th>
              <th>성공/실패/요청</th>
              <th>옵션</th>
              <th>생성</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {batches.map((b) => {
              const st = batchStatus(b.status);
              const open = expanded.has(b.id);
              return (
                <Fragment key={b.id}>
                  <tr
                    className={`expandable-row${open ? " row-open" : ""}`}
                    onClick={() => toggle(b.id)}
                    tabIndex={0}
                    aria-expanded={open}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        toggle(b.id);
                      }
                    }}
                  >
                    <td className="col-toggle">
                      <span className={`expand-toggle${open ? " open" : ""}`} aria-hidden="true">
                        ▸
                      </span>
                    </td>
                    <td data-label="이름">
                      <button
                        className="linklike"
                        onClick={(e) => {
                          e.stopPropagation();
                          setOpenId(b.id);
                        }}
                      >
                        {b.name}
                      </button>
                    </td>
                    <td data-label="상태">
                      <span className={`san ${st.cls}`}>{st.label}</span>
                    </td>
                    <td data-label="성공/실패/요청">
                      <span className="ok-num">{b.succeeded_count ?? 0}</span>
                      <span className="muted"> / </span>
                      <span className="err-num">{b.failed_count ?? 0}</span>
                      <span className="muted"> / </span>
                      <span>{b.request_count ?? 0}</span>
                    </td>
                    <td data-label="옵션">
                      <OptionChips batch={b} />
                    </td>
                    <td data-label="생성" className="muted small">
                      {fmtTime(b.created_at)}
                    </td>
                    <td className="row-actions" onClick={(e) => e.stopPropagation()}>
                      <button className="mini" onClick={() => setOpenId(b.id)}>
                        열기
                      </button>
                      <button
                        className="mini danger"
                        onClick={() => remove(b)}
                        disabled={b.status === "previewing" || b.status === "running"}
                      >
                        삭제
                      </button>
                    </td>
                  </tr>
                  {open && (
                    <tr className="detail-row">
                      <td colSpan={7}>
                        <BatchExpand batch={b} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      )}

      {showForm && (
        <BackupBatchForm
          onClose={() => setShowForm(false)}
          onSaved={({ id, added }) => {
            setShowForm(false);
            setNotice(`배치 생성됨 (요청 ${added}개). Preview를 시작하세요.`);
            setOpenId(id);
          }}
        />
      )}
    </div>
  );
}

function BatchExpand({ batch }: { batch: BackupBatch }) {
  const opts = optionEntries(batch.options);
  const total = batch.request_count ?? 0;
  const ok = batch.succeeded_count ?? 0;
  const fail = batch.failed_count ?? 0;
  const pct = total ? Math.round((ok / total) * 100) : 0;
  return (
    <div className="batch-expand">
      <SpecGrid
        items={[
          { label: "요청 수", value: total.toLocaleString() },
          { label: "우선순위", value: batch.priority ?? "Low" },
          { label: "병렬 노드", value: batch.node_count != null ? String(batch.node_count) : "자동" },
          {
            label: "--delete",
            value: batch.delete_enabled ? "켜짐 (완전 미러)" : "꺼짐",
            tone: batch.delete_enabled ? "tone-danger-text" : "",
          },
          { label: "requester", value: batch.requester_id, mono: true },
          { label: "생성", value: fmtTime(batch.created_at) },
          { label: "수정", value: fmtTime(batch.updated_at) },
          ...(batch.note ? [{ label: "메모", value: batch.note, span: true }] : []),
        ]}
      />
      {total > 0 && (
        <div className="mini-progress" title={`성공 ${ok} / 실패 ${fail} / 전체 ${total}`}>
          <div className="mini-progress-bar">
            <span className="seg ok" style={{ width: `${pct}%` }} />
            <span className="seg fail" style={{ width: `${total ? (fail / total) * 100 : 0}%` }} />
          </div>
          <span className="muted small">
            성공 {ok} · 실패 {fail} · 전체 {total}
          </span>
        </div>
      )}
      {opts.length > 0 && (
        <div className="opt-list">
          <span className="opt-list-label">sync 옵션</span>
          {opts.map((o) => (
            <span className="chip ghost-chip" key={o.k}>
              {o.k} <b>{o.v}</b>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

export function errMsg(e: unknown): string {
  if (e instanceof ApiError) {
    const base = typeof e.detail === "string" ? e.detail : JSON.stringify(e.detail);
    if (e.status === 503) return `포탈 DB 미연동 (503): ${base}`;
    if (e.status === 409) return `상태 충돌 (409): ${base}`;
    if (e.status === 422) return `유효성 오류 (422): ${base}`;
    return `오류 ${e.status}: ${base}`;
  }
  return e instanceof Error ? e.message : "알 수 없는 오류";
}

export function fmtTime(iso?: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}
