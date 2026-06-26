import { useState } from "react";
import { operatorApi, type BackupRequest } from "../../../api";
import { errMsg } from "./BackupBatches";

// Per-row path editor for a single draft request (storage-relative paths; the
// server re-normalizes and rejects traversal). Draft-only; the caller only
// renders this while the batch is a draft.
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
  const [srcStorage, setSrcStorage] = useState(request.src_storage);
  const [srcPath, setSrcPath] = useState(request.src_path);
  const [dstStorage, setDstStorage] = useState(request.dst_storage);
  const [dstPath, setDstPath] = useState(request.dst_path);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setError(null);
    if (!srcStorage.trim() || !dstStorage.trim()) {
      setError("출발/대상 스토리지 이름은 필수입니다.");
      return;
    }
    setBusy(true);
    try {
      await operatorApi.backup.updateRequest(batchId, request.id, {
        src_storage: srcStorage.trim(),
        src_path: srcPath.trim(),
        dst_storage: dstStorage.trim(),
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
          <label>
            <span>출발 스토리지 (src) *</span>
            <input value={srcStorage} onChange={(e) => setSrcStorage(e.target.value)} />
          </label>
          <label>
            <span>출발 경로 (managed_root 상대)</span>
            <input value={srcPath} onChange={(e) => setSrcPath(e.target.value)} />
          </label>
          <label>
            <span>대상 스토리지 (dst) *</span>
            <input value={dstStorage} onChange={(e) => setDstStorage(e.target.value)} />
          </label>
          <label>
            <span>대상 경로 (managed_root 상대)</span>
            <input value={dstPath} onChange={(e) => setDstPath(e.target.value)} />
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
