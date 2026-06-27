import { useEffect, useRef, useState, type ChangeEvent } from "react";
import { operatorApi, type BackupBatch } from "../../../api";
import {
  optionsWithoutDelete,
  parseRequestsCsv,
  rowsToCsv,
  validateSyncOptions,
  type BackupRow,
} from "./helpers";
import SyncOptionsFields from "./SyncOptionsFields";
import { errMsg } from "./BackupBatches";

const SAMPLE_ROWS: BackupRow[] = [
  { src_path: "e2e/src", dst_path: "e2e/dst/backup1" },
  { src_path: "e2e/src/d1", dst_path: "e2e/dst/d1" },
];

// Create or edit a backup batch. Source & destination STORAGE are batch-level
// single inputs; each request is just a (src_path, dst_path) pair entered in an
// inline table (with CSV import/export). Storage is applied to every row on save,
// so the wire format (per-request storage) is unchanged. Editing is draft-only;
// saving replaces the whole request set.
export default function BackupBatchForm({
  mode = "create",
  initial,
  onClose,
  onSaved,
}: {
  mode?: "create" | "edit";
  initial?: BackupBatch;
  onClose: () => void;
  onSaved: (info: { id: string; added: number; mode: "create" | "edit" }) => void;
}) {
  const isEdit = mode === "edit";
  const [name, setName] = useState(initial?.name ?? "");
  const [note, setNote] = useState(initial?.note ?? "");
  const [priority, setPriority] = useState(initial?.priority ?? "Low");
  const [deleteEnabled, setDeleteEnabled] = useState(
    isEdit ? initial?.delete_enabled ?? false : true,
  );
  const [options, setOptions] = useState<Record<string, unknown>>(
    isEdit
      ? optionsWithoutDelete(initial?.options)
      : { open_noatime: true, bufsize: 4 * 1024 * 1024, batch_files: 100000 },
  );
  const [srcStorage, setSrcStorage] = useState("");
  const [dstStorage, setDstStorage] = useState("");
  const [rows, setRows] = useState<BackupRow[]>(isEdit ? [] : [{ src_path: "", dst_path: "" }]);
  const [storages, setStorages] = useState<string[]>([]);
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
  const [loading, setLoading] = useState(isEdit);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [warn, setWarn] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  // Load the storage list (for the dropdowns) and, in edit mode, the batch's
  // existing requests (to populate the table + derive batch-level storage).
  useEffect(() => {
    let alive = true;
    operatorApi.storage
      .list()
      .then((list) => alive && setStorages(list.map((m) => m.storage_name).sort()))
      .catch(() => {});
    if (isEdit && initial) {
      operatorApi.backup
        .requests(initial.id, { limit: 2000 })
        .then((reqs) => {
          if (!alive) return;
          setRows(reqs.map((r) => ({ src_path: r.src_path, dst_path: r.dst_path })));
          if (reqs[0]) {
            setSrcStorage(reqs[0].src_storage);
            setDstStorage(reqs[0].dst_storage);
          }
        })
        .catch((e) => alive && setError(errMsg(e)))
        .finally(() => alive && setLoading(false));
    }
    return () => {
      alive = false;
    };
  }, [isEdit, initial]);

  function addRow() {
    setRows((r) => [...r, { src_path: "", dst_path: "" }]);
  }
  function removeRow(i: number) {
    setRows((r) => r.filter((_, j) => j !== i));
  }
  function updateRow(i: number, key: keyof BackupRow, v: string) {
    setRows((r) => r.map((row, j) => (j === i ? { ...row, [key]: v } : row)));
  }

  async function onUpload(e: ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (!f) return;
    const text = await f.text();
    const parsed = parseRequestsCsv(text);
    setRows(parsed.rows.length ? parsed.rows : [{ src_path: "", dst_path: "" }]);
    if (parsed.srcStorage) setSrcStorage(parsed.srcStorage);
    if (parsed.dstStorage) setDstStorage(parsed.dstStorage);
    setWarn(parsed.errors.length ? parsed.errors.join(" · ") : null);
    if (fileRef.current) fileRef.current.value = "";
  }

  function onDownload() {
    const blob = new Blob([rowsToCsv(rows)], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${(name || "batch").replace(/[^\w.-]+/g, "_")}-requests.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  const deleteConfirm =
    "--delete가 켜져 있습니다. 대상(dst)에서 원본(src)에 없는 파일이 삭제됩니다. 미리보기로 확인 후 실행됩니다. 계속할까요?";

  async function submit() {
    setError(null);
    if (!name.trim()) return setError("배치 이름은 필수입니다.");
    if (!srcStorage.trim() || !dstStorage.trim())
      return setError("출발/대상 스토리지를 선택하세요.");
    const cleaned = rows
      .map((r) => ({ src_path: r.src_path.trim(), dst_path: r.dst_path.trim() }))
      .filter((r) => r.src_path || r.dst_path);
    if (cleaned.length === 0) return setError("요청을 하나 이상 입력하세요.");
    const bad = cleaned.findIndex((r) => !r.src_path || !r.dst_path);
    if (bad >= 0) return setError(`${bad + 1}행: 출발/대상 경로가 모두 필요합니다.`);
    const optErrors = validateSyncOptions(options);
    if (optErrors.length > 0) return setError(`sync 옵션 오류: ${optErrors.join(" · ")}`);
    if (deleteEnabled && !initial?.delete_enabled && !window.confirm(deleteConfirm)) return;

    const requests = cleaned.map((r) => ({
      src_storage: srcStorage.trim(),
      src_path: r.src_path,
      dst_storage: dstStorage.trim(),
      dst_path: r.dst_path,
    }));
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
        await operatorApi.backup.replaceRequests(initial.id, requests);
        onSaved({ id: initial.id, added: requests.length, mode });
      } else {
        const res = await operatorApi.backup.create({
          name: name.trim(),
          delete_enabled: deleteEnabled,
          note: note.trim() || null,
          options,
          priority,
          requests,
        });
        onSaved({ id: res.id, added: res.added, mode });
      }
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  function storageSelect(value: string, setter: (v: string) => void, label: string) {
    return (
      <label>
        <span>{label} *</span>
        <select value={value} onChange={(e) => setter(e.target.value)}>
          <option value="">선택…</option>
          {storages.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
          {value && !storages.includes(value) && <option value={value}>{value}</option>}
        </select>
      </label>
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

        {loading ? (
          <div className="muted" style={{ padding: "1rem" }}>
            불러오는 중…
          </div>
        ) : (
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

            <div className="storage-row">
              {storageSelect(srcStorage, setSrcStorage, "출발 스토리지 (src)")}
              {storageSelect(dstStorage, setDstStorage, "대상 스토리지 (dst)")}
            </div>

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

            {/* inline request table */}
            <div className="tmpl-bar">
              <span className="muted small">
                요청 목록 — 스토리지 기준 상대 경로 <code>src_path</code> → <code>dst_path</code> (한 행에 한 요청)
              </span>
              <span className="spacer" />
              <button type="button" className="ghost mini" onClick={() => fileRef.current?.click()}>
                CSV 업로드
              </button>
              <button type="button" className="ghost mini" onClick={onDownload} disabled={rows.length === 0}>
                CSV 다운로드
              </button>
              <button
                type="button"
                className="ghost mini"
                onClick={() => setRows(SAMPLE_ROWS.map((r) => ({ ...r })))}
              >
                예시 채우기
              </button>
              <input
                ref={fileRef}
                type="file"
                accept=".csv,.tsv,.txt"
                style={{ display: "none" }}
                onChange={onUpload}
              />
            </div>

            <div className="req-table">
              <div className="req-row req-head">
                <span>출발 경로 (src_path)</span>
                <span>대상 경로 (dst_path)</span>
                <span></span>
              </div>
              {rows.map((r, i) => (
                <div className="req-row" key={i}>
                  <input
                    className="mono"
                    value={r.src_path}
                    onChange={(e) => updateRow(i, "src_path", e.target.value)}
                    placeholder="예: e2e/src"
                  />
                  <input
                    className="mono"
                    value={r.dst_path}
                    onChange={(e) => updateRow(i, "dst_path", e.target.value)}
                    placeholder="예: e2e/dst/backup1"
                  />
                  <button className="mini danger" type="button" onClick={() => removeRow(i)}>
                    삭제
                  </button>
                </div>
              ))}
              <button type="button" className="ghost mini" onClick={addRow}>
                + 행 추가
              </button>
            </div>
            <div className="form-hints muted small">
              <span>
                요청 <strong>{rows.filter((r) => r.src_path.trim() && r.dst_path.trim()).length}</strong>개
                {" "}· 대상의 부모 디렉터리는 미리 존재해야 합니다(미리보기에서 검증).
              </span>
            </div>

            {warn && <div className="banner err">{warn}</div>}
            {error && <div className="banner err">{error}</div>}

            <div className="modal-actions">
              <button className="ghost" onClick={onClose} disabled={busy}>
                취소
              </button>
              <button className="primary" onClick={submit} disabled={busy}>
                {busy ? "저장 중…" : isEdit ? "저장" : "배치 생성"}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
