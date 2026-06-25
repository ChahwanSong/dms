import { useMemo, useState } from "react";
import { operatorApi } from "../../../api";
import { parseRequestsCsv } from "./helpers";
import { errMsg } from "./BackupBatches";

const SAMPLE =
  "# src_storage, src_path, dst_storage, dst_path  (한 줄에 한 요청, 쉼표 또는 탭)\n" +
  "cephfs-dms, project/alpha, cephfs-secondary, backup/alpha\n" +
  "cephfs-dms, project/beta, cephfs-secondary, backup/beta";

// Create a backup batch. Jobs are entered in bulk as CSV/TSV (up to a few
// thousand). requester=root + ownership preservation is implicit (server-side).
export default function BackupBatchForm({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (id: string, added: number) => void;
}) {
  const [name, setName] = useState("");
  const [note, setNote] = useState("");
  const [deleteEnabled, setDeleteEnabled] = useState(false);
  const [csv, setCsv] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const parsed = useMemo(() => parseRequestsCsv(csv), [csv]);

  async function submit() {
    setError(null);
    if (!name.trim()) {
      setError("배치 이름은 필수입니다.");
      return;
    }
    if (parsed.requests.length === 0) {
      setError("요청을 하나 이상 입력하세요 (CSV).");
      return;
    }
    if (parsed.errors.length > 0) {
      setError(`CSV 오류 ${parsed.errors.length}건을 먼저 수정하세요.`);
      return;
    }
    if (
      deleteEnabled &&
      !window.confirm(
        "--delete가 켜져 있습니다. 대상(dst)에서 원본(src)에 없는 파일이 삭제됩니다. " +
          "미리보기로 영향을 확인한 뒤 실행됩니다. 계속할까요?",
      )
    )
      return;
    setBusy(true);
    try {
      const res = await operatorApi.backup.create({
        name: name.trim(),
        delete_enabled: deleteEnabled,
        note: note.trim() || null,
        requests: parsed.requests,
      });
      onCreated(res.id, res.added);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal wide" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h3>새 백업 배치</h3>
          <button className="ghost" onClick={onClose}>
            닫기
          </button>
        </div>

        <div className="form">
          <label>
            <span>배치 이름 *</span>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="예: 야간 미러 백업"
            />
          </label>
          <label>
            <span>메모 (선택)</span>
            <input value={note} onChange={(e) => setNote(e.target.value)} />
          </label>
          <label className="check-row">
            <input
              type="checkbox"
              checked={deleteEnabled}
              onChange={(e) => setDeleteEnabled(e.target.checked)}
            />
            <span>
              <strong>--delete</strong> (미러): dst에서 src에 없는 파일을 삭제 — 파괴적,
              미리보기로 확인 후 실행
            </span>
          </label>

          <div className="tmpl-bar">
            <span className="muted small">
              요청 목록 (CSV/TSV): <code>src_storage, src_path, dst_storage, dst_path</code> — 한 줄에 한 요청
            </span>
            <span className="spacer" />
            <button
              type="button"
              className="ghost mini"
              onClick={() => setCsv(SAMPLE)}
              disabled={!!csv}
            >
              예시 채우기
            </button>
          </div>
          <textarea
            className="json"
            rows={10}
            value={csv}
            onChange={(e) => setCsv(e.target.value)}
            placeholder={SAMPLE}
          />
          <div className="form-hints muted small">
            <span>
              인식된 요청: <strong>{parsed.requests.length.toLocaleString()}</strong>개
              {parsed.errors.length > 0 && (
                <span className="err-num"> · 오류 {parsed.errors.length}건</span>
              )}
            </span>
            {parsed.errors.slice(0, 5).map((e, i) => (
              <span key={i} className="err-num">
                {e}
              </span>
            ))}
            {parsed.errors.length > 5 && (
              <span className="err-num">…외 {parsed.errors.length - 5}건</span>
            )}
          </div>

          {error && <div className="banner err">{error}</div>}

          <div className="modal-actions">
            <button className="ghost" onClick={onClose} disabled={busy}>
              취소
            </button>
            <button className="primary" onClick={submit} disabled={busy}>
              {busy ? "생성 중…" : `배치 생성 (${parsed.requests.length}개 요청)`}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
