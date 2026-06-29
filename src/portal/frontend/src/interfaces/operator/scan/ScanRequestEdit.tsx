import { useState } from "react";
import { operatorApi, type ScanRequest } from "../../../api";
import { errMsg } from "./ScanBatches";

// Per-row PATH editor for a single scan request. Storage is batch-level (shown
// read-only); editing a path resets the request to 'registered' server-side so a
// re-run re-scans it. Used to fix failed/terminal items before a re-run.
export default function ScanRequestEdit({
  batchId,
  request,
  onClose,
  onSaved,
}: {
  batchId: string;
  request: ScanRequest;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [path, setPath] = useState(request.path);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setError(null);
    setBusy(true);
    try {
      await operatorApi.scan.updateRequest(batchId, request.id, {
        storage: request.storage,
        path: path.trim(),
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
          <h3>스캔 경로 수정</h3>
          <button className="ghost" onClick={onClose}>
            닫기
          </button>
        </div>
        <div className="form">
          <p className="muted small">
            스토리지 <code>{request.storage}</code> (배치 레벨) 기준 상대 경로
          </p>
          <label>
            <span>스캔 경로 (path)</span>
            <input className="mono" value={path} onChange={(e) => setPath(e.target.value)} />
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
