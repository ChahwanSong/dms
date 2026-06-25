import { useEffect, useState } from "react";
import { ApiError, operatorApi, type BackupBatch } from "../../../api";
import { batchStatus } from "./helpers";
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
        <h2>데이터 백업</h2>
        <div className="inv-actions">
          <button className="ghost" onClick={load} disabled={loading}>
            새로고침
          </button>
          <button className="primary" onClick={() => setShowForm(true)}>
            + 새 배치
          </button>
        </div>
      </div>

      <p className="muted small">
        DMS DM sync로 스토리지 간 미러 백업을 수행합니다(선택적 --delete). 배치를 등록 →
        <strong> 미리보기</strong>(비파괴 dry-run)로 영향 확인 → <strong>승인</strong> 후 실행됩니다.
        요청은 root로 실행되어 원본 소유권을 보존합니다.
      </p>

      {notice && <div className="banner ok">{notice}</div>}
      {error && <div className="banner err">{error}</div>}

      {loading ? (
        <div className="muted">불러오는 중…</div>
      ) : batches.length === 0 ? (
        <div className="muted">등록된 백업 배치가 없습니다. “+ 새 배치”로 시작하세요.</div>
      ) : (
        <table className="grid">
          <thead>
            <tr>
              <th>이름</th>
              <th>상태</th>
              <th>요청</th>
              <th>성공 / 실패</th>
              <th>--delete</th>
              <th>생성</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {batches.map((b) => {
              const st = batchStatus(b.status);
              return (
                <tr key={b.id}>
                  <td data-label="이름">
                    <button className="linklike" onClick={() => setOpenId(b.id)}>
                      {b.name}
                    </button>
                  </td>
                  <td data-label="상태">
                    <span className={`san ${st.cls}`}>{st.label}</span>
                  </td>
                  <td data-label="요청">{b.request_count ?? 0}</td>
                  <td data-label="성공 / 실패">
                    <span className="ok-num">{b.succeeded_count ?? 0}</span> /{" "}
                    <span className="err-num">{b.failed_count ?? 0}</span>
                  </td>
                  <td data-label="--delete">{b.delete_enabled ? "🗑️ 켜짐" : "—"}</td>
                  <td data-label="생성" className="muted small">
                    {fmtTime(b.created_at)}
                  </td>
                  <td className="row-actions">
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
              );
            })}
          </tbody>
        </table>
      )}

      {showForm && (
        <BackupBatchForm
          onClose={() => setShowForm(false)}
          onCreated={(id, added) => {
            setShowForm(false);
            setNotice(`배치 생성됨 (요청 ${added}개). 미리보기를 시작하세요.`);
            setOpenId(id);
          }}
        />
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
