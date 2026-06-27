import { useState } from "react";
import { operatorApi, type BackupRequest } from "../../../api";
import { errMsg } from "./BackupBatches";

// Per-row PATH editor for a single request. Storage is batch-level (shown
// read-only); editing a path resets the request to 'registered' server-side so a
// re-preview re-runs it. Used for post-preview re-edit of fixable items.
export default function BackupRequestEdit({
  batchId,
  request,
  onClose,
  onSaved,
}: {
  batchId: string;
  request: BackupRequest;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [srcPath, setSrcPath] = useState(request.src_path);
  const [dstPath, setDstPath] = useState(request.dst_path);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setError(null);
    setBusy(true);
    try {
      await operatorApi.backup.updateRequest(batchId, request.id, {
        src_storage: request.src_storage,
        src_path: srcPath.trim(),
        dst_storage: request.dst_storage,
        dst_path: dstPath.trim(),
      });
      onSaved();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h3>요청 경로 수정</h3>
          <button className="ghost" onClick={onClose}>
            닫기
          </button>
        </div>
        <div className="form">
          <p className="muted small">
            출발 <code>{request.src_storage}</code> → 대상 <code>{request.dst_storage}</code> (스토리지는 배치 레벨)
          </p>
          <label>
            <span>출발 경로 (src_path)</span>
            <input className="mono" value={srcPath} onChange={(e) => setSrcPath(e.target.value)} />
          </label>
          <label>
            <span>대상 경로 (dst_path)</span>
            <input className="mono" value={dstPath} onChange={(e) => setDstPath(e.target.value)} />
          </label>

          {error && <div className="banner err">{error}</div>}

          <div className="modal-actions">
            <button className="ghost" onClick={onClose} disabled={busy}>
              취소
            </button>
            <button className="primary" onClick={save} disabled={busy}>
              {busy ? "저장 중…" : "저장"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
