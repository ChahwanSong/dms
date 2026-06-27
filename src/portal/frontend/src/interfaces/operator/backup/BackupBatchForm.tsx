import { useMemo, useState } from "react";
import { operatorApi, type BackupBatch } from "../../../api";
import {
  optionsWithoutDelete,
  parseRequestsCsv,
  validateSyncOptions,
} from "./helpers";
import SyncOptionsFields from "./SyncOptionsFields";
import { errMsg } from "./BackupBatches";

const SAMPLE =
  "# src_storage, src_path, dst_storage, dst_path  (한 줄에 한 요청, 쉼표 또는 탭)\n" +
  "cephfs-dms, project/alpha, cephfs-secondary, backup/alpha\n" +
  "cephfs-dms, project/beta, cephfs-secondary, backup/beta";

// Create or edit a backup batch. Editing is draft-only (the caller only mounts
// this for a draft batch). In create mode jobs are entered in bulk as CSV/TSV;
// in edit mode CSV is an optional "append more requests" section. requester=root
// + ownership preservation is implicit (server-side).
export default function BackupBatchForm({
  mode = "create",
  initial,
  focusRequests = false,
  onClose,
  onSaved,
}: {
  mode?: "create" | "edit";
  initial?: BackupBatch;
  focusRequests?: boolean;
  onClose: () => void;
  onSaved: (info: { id: string; added: number; mode: "create" | "edit" }) => void;
}) {
  const isEdit = mode === "edit";
  const [name, setName] = useState(initial?.name ?? "");
  const [note, setNote] = useState(initial?.note ?? "");
  const [priority, setPriority] = useState(initial?.priority ?? "Low");
  // New batches default to a destructive mirror (--delete) + open_noatime ON
  // (operator request). direct (O_DIRECT) is left OFF — it can fail on
  // filesystems without O_DIRECT support. Editing preserves existing values.
  const [deleteEnabled, setDeleteEnabled] = useState(
    isEdit ? initial?.delete_enabled ?? false : true,
  );
  const [options, setOptions] = useState<Record<string, unknown>>(
    isEdit
      ? optionsWithoutDelete(initial?.options)
      : { open_noatime: true, bufsize: 4 * 1024 * 1024, batch_files: 100000 },
  );
  const [csv, setCsv] = useState("");
  // Option sections are collapsed by default; on edit, auto-open a section that
  // already has settings. The collapsed "sync 옵션" header still flags --delete.
  const [showSync, setShowSync] = useState(
    isEdit &&
      (!!initial?.delete_enabled ||
        ["contents", "direct", "open_noatime", "quiet", "batch_files", "bufsize"].some(
          (k) => initial?.options?.[k] != null,
        )),
  );
  const [showOwnership, setShowOwnership] = useState(
    !!(initial?.options && (initial.options.chmod != null || initial.options.chown != null)),
  );
  const [showRequests, setShowRequests] = useState(!isEdit || focusRequests);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const parsed = useMemo(() => parseRequestsCsv(csv), [csv]);

  async function submit() {
    setError(null);
    if (!name.trim()) {
      setError("배치 이름은 필수입니다.");
      return;
    }
    const optErrors = validateSyncOptions(options);
    if (optErrors.length > 0) {
      setError(`sync 옵션 오류: ${optErrors.join(" · ")}`);
      return;
    }
    // create requires ≥1 request; edit may append zero (meta/options-only save).
    if (!isEdit && parsed.requests.length === 0) {
      setError("요청을 하나 이상 입력하세요 (CSV).");
      return;
    }
    if (parsed.errors.length > 0) {
      setError(`CSV 오류 ${parsed.errors.length}건을 먼저 수정하세요.`);
      return;
    }
    if (
      deleteEnabled &&
      !initial?.delete_enabled && // only re-confirm when newly enabling
      !window.confirm(
        "--delete가 켜져 있습니다. 대상(dst)에서 원본(src)에 없는 파일이 삭제됩니다. " +
          "미리보기로 영향을 확인한 뒤 실행됩니다. 계속할까요?",
      )
    )
      return;
    setBusy(true);
    try {
      if (isEdit && initial) {
        await operatorApi.backup.update(initial.id, {
          name: name.trim(),
          note: note.trim() || null,
          delete_enabled: deleteEnabled,
          options,
          priority,
        });
        let added = 0;
        if (parsed.requests.length > 0) {
          const r = await operatorApi.backup.addRequests(initial.id, parsed.requests);
          added = r.added;
        }
        onSaved({ id: initial.id, added, mode });
      } else {
        const res = await operatorApi.backup.create({
          name: name.trim(),
          delete_enabled: deleteEnabled,
          note: note.trim() || null,
          options,
          priority,
          requests: parsed.requests,
        });
        onSaved({ id: res.id, added: res.added, mode });
      }
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  function renderCsv() {
    return (
      <>
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
      </>
    );
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal wide" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h3>{isEdit ? "배치 편집" : "새 백업 배치"}</h3>
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
          <label>
            <span>스케줄러 우선순위</span>
            <select value={priority} onChange={(e) => setPriority(e.target.value)}>
              <option value="Low">Low (기본 · 대화형/운영 작업 방해 없음)</option>
              <option value="Mid">Mid</option>
              <option value="High">High</option>
            </select>
          </label>
          <button
            type="button"
            className="ghost mini section-toggle"
            onClick={() => setShowSync((v) => !v)}
          >
            {showSync ? "▾" : "▸"} sync 옵션
            {!showSync && deleteEnabled && <span className="err-num small"> · --delete 켜짐</span>}
          </button>
          {showSync && (
            <div className="sync-options">
              <label className="check-row">
                <input
                  type="checkbox"
                  checked={deleteEnabled}
                  onChange={(e) => setDeleteEnabled(e.target.checked)}
                />
                <span>
                  <strong className={deleteEnabled ? "err-num" : undefined}>--delete</strong> (미러):
                  dst에서 src에 없는 파일을 삭제 — 파괴적, 미리보기로 확인 후 실행
                </span>
              </label>
              <SyncOptionsFields group="sync" value={options} onChange={setOptions} />
            </div>
          )}

          <button
            type="button"
            className="ghost mini section-toggle"
            onClick={() => setShowOwnership((v) => !v)}
          >
            {showOwnership ? "▾" : "▸"} 소유권 옵션 (chmod · chown)
          </button>
          {showOwnership && (
            <div className="sync-options">
              <SyncOptionsFields group="ownership" value={options} onChange={setOptions} />
            </div>
          )}

          {isEdit ? (
            <>
              <button
                type="button"
                className="ghost mini section-toggle"
                onClick={() => setShowRequests((v) => !v)}
              >
                {showRequests ? "▾ 요청 추가 (CSV)" : "▸ 요청 추가 (CSV)"}
              </button>
              {showRequests && renderCsv()}
            </>
          ) : (
            renderCsv()
          )}

          {error && <div className="banner err">{error}</div>}

          <div className="modal-actions">
            <button className="ghost" onClick={onClose} disabled={busy}>
              취소
            </button>
            <button className="primary" onClick={submit} disabled={busy}>
              {busy
                ? "저장 중…"
                : isEdit
                  ? parsed.requests.length > 0
                    ? `저장 (+요청 ${parsed.requests.length}개)`
                    : "저장"
                  : `배치 생성 (${parsed.requests.length}개 요청)`}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
