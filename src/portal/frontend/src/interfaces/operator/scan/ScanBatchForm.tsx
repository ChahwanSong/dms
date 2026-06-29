import { useEffect, useState } from "react";
import { operatorApi, type ScanBatch, type ScanNodePolicyResp } from "../../../api";
import {
  pathsToCsv,
  validateScanOptions,
  CSV_TEMPLATE_TEXT,
  CSV_FORMAT_HINT,
  SCAN_OPTION_FIELDS,
  type ScanRow,
  type ScanOptionField,
} from "./helpers";
import ScanCsvModal from "./ScanCsvModal";
import { errMsg } from "./ScanBatches";
import { isFsBackend } from "../storage/helpers";
import Loading from "../../../components/Loading";

// Create or edit a scan batch. STORAGE is a batch-level single dropdown applied to
// every row; each request is just a single relative `path` entered in an inline
// table (with CSV import/export). Scan is read-only — no --delete, no ownership,
// no preview. Saving an edit replaces the whole request set.
export default function ScanBatchForm({
  mode = "create",
  initial,
  onClose,
  onSaved,
}: {
  mode?: "create" | "edit";
  initial?: ScanBatch;
  onClose: () => void;
  onSaved: (info: { id: string; added: number; mode: "create" | "edit" }) => void;
}) {
  const isEdit = mode === "edit";
  const [name, setName] = useState(initial?.name ?? "");
  const [note, setNote] = useState(initial?.note ?? "");
  const [priority, setPriority] = useState(initial?.priority ?? "Low");
  // "" = 자동 (DMS 정책 기본값).
  const [nodeCount, setNodeCount] = useState<string>(
    initial?.node_count != null ? String(initial.node_count) : "",
  );
  const [options, setOptions] = useState<Record<string, unknown>>(
    isEdit ? { ...(initial?.options || {}) } : { summary_only: true },
  );
  const [storage, setStorage] = useState("");
  const [rows, setRows] = useState<ScanRow[]>(isEdit ? [] : [{ path: "" }]);
  const [storages, setStorages] = useState<string[]>([]);
  const [nodePolicy, setNodePolicy] = useState<ScanNodePolicyResp | null>(null);
  const [showOptions, setShowOptions] = useState(
    isEdit && SCAN_OPTION_FIELDS.some((f) => initial?.options?.[f.key] != null),
  );
  const [loading, setLoading] = useState(isEdit);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [warn, setWarn] = useState<string | null>(null);
  const [csvModal, setCsvModal] = useState<{
    mode: "view" | "replace";
    title: string;
    hint?: string;
    text: string;
  } | null>(null);

  // Load the storage list (for the dropdown) and, in edit mode, the batch's
  // existing requests (to populate the table + derive batch-level storage).
  useEffect(() => {
    let alive = true;
    operatorApi.storage
      .list()
      // Scan runs on filesystem backends only (cephfs/gpfs/wekafs); k8s CSI
      // mappings are namespace-quota only and can't be a scan target, so exclude
      // them from the storage candidates.
      .then((list) => alive && setStorages(list.filter(isFsBackend).map((m) => m.storage_name).sort()))
      .catch(() => {});
    // What "자동" (DMS policy default) resolves to, to surface the actual number.
    operatorApi.scan
      .nodePolicy()
      .then((p) => alive && setNodePolicy(p))
      .catch(() => {});
    if (isEdit && initial) {
      operatorApi.scan
        .requests(initial.id, { limit: 2000 })
        .then((reqs) => {
          if (!alive) return;
          setRows(reqs.map((r) => ({ path: r.path })));
          if (reqs[0]) setStorage(reqs[0].storage);
        })
        .catch((e) => alive && setError(errMsg(e)))
        .finally(() => alive && setLoading(false));
    }
    return () => {
      alive = false;
    };
  }, [isEdit, initial]);

  function addRow() {
    setRows((r) => [...r, { path: "" }]);
  }
  function removeRow(i: number) {
    setRows((r) => r.filter((_, j) => j !== i));
  }
  function updateRow(i: number, v: string) {
    setRows((r) => r.map((row, j) => (j === i ? { path: v } : row)));
  }

  function setOptKey(key: string, v: unknown) {
    setOptions((prev) => {
      const next = { ...prev };
      if (v === undefined || v === "" || v === false) delete next[key];
      else next[key] = v;
      return next;
    });
  }

  // CSV/text popups. "현재 항목" copies the current table as text; "업로드 (전체
  // 교체)" pastes/loads text → replaces rows.
  function openTemplate() {
    setCsvModal({
      mode: "view",
      title: "CSV / 텍스트 템플릿 (예시)",
      hint: CSV_FORMAT_HINT,
      text: CSV_TEMPLATE_TEXT,
    });
  }
  function openCurrent() {
    const content = rows.filter((r) => r.path.trim());
    setCsvModal({
      mode: "view",
      title: "현재 항목 (CSV / 텍스트)",
      hint: `${content.length}개 항목. 복사해 다른 배치에 붙여넣을 수 있습니다.`,
      text: pathsToCsv(content),
    });
  }
  function openUpload() {
    setCsvModal({ mode: "replace", title: "텍스트 붙여넣기 → 전체 교체", hint: CSV_FORMAT_HINT, text: "" });
  }
  function replaceFromRows(newRows: ScanRow[]) {
    const existing = rows.filter((r) => r.path.trim()).length;
    if (existing > 0 && !window.confirm(`현재 ${existing}개 항목을 ${newRows.length}개로 교체합니다. 계속할까요?`))
      return;
    setRows(newRows);
    setWarn(null);
    setCsvModal(null);
  }

  async function submit() {
    setError(null);
    if (!name.trim()) return setError("배치 이름은 필수입니다.");
    if (!storage.trim()) return setError("스토리지를 선택하세요.");
    const cleaned = rows.map((r) => r.path.trim()).filter((p) => p);
    if (cleaned.length === 0) return setError("요청을 하나 이상 입력하세요.");
    const optErrors = validateScanOptions(options);
    if (optErrors.length > 0) return setError(`스캔 옵션 오류: ${optErrors.join(" · ")}`);

    const requests = cleaned.map((path) => ({ storage: storage.trim(), path }));
    const node_count = nodeCount ? Number(nodeCount) : null;
    setBusy(true);
    try {
      if (isEdit && initial) {
        await operatorApi.scan.update(initial.id, {
          name: name.trim(),
          note: note.trim() || null,
          options,
          priority,
          node_count,
        });
        await operatorApi.scan.replaceRequests(initial.id, requests);
        onSaved({ id: initial.id, added: requests.length, mode });
      } else {
        const res = await operatorApi.scan.create({
          name: name.trim(),
          note: note.trim() || null,
          options,
          priority,
          node_count,
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

  function renderOption(f: ScanOptionField) {
    if (f.kind === "bool") {
      return (
        <label key={f.key} className="check-row">
          <input
            type="checkbox"
            checked={options[f.key] === true}
            onChange={(e) => setOptKey(f.key, e.target.checked)}
          />
          <span>
            <code>{f.label}</code>
            {f.hint && <span className="muted small"> — {f.hint}</span>}
          </span>
        </label>
      );
    }
    return (
      <label key={f.key}>
        <span>
          <code>{f.label}</code>
          {f.hint && <span className="muted small"> — {f.hint}</span>}
        </span>
        <input
          type="number"
          min={0}
          placeholder={f.placeholder}
          value={options[f.key] === undefined ? "" : String(options[f.key])}
          onChange={(e) =>
            setOptKey(f.key, e.target.value === "" ? undefined : Number(e.target.value))
          }
        />
      </label>
    );
  }

  // What "자동" resolves to: the dscan default worker-node count. Null when the
  // policy hasn't loaded or there is no dscan policy.
  const dscanN = nodePolicy?.dscan?.default_worker_nodes ?? null;
  const autoLabel = dscanN != null ? String(dscanN) : null;

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal wide" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h3>{isEdit ? "배치 편집" : "새 스캔 배치"}</h3>
          <button className="ghost" onClick={onClose}>
            닫기
          </button>
        </div>

        {loading ? (
          <Loading rows={3} />
        ) : (
          <div className="form">
            <label>
              <span>배치 이름 *</span>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="예: 야간 사용량 스캔"
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
            <label>
              <span>병렬 노드 수</span>
              <select value={nodeCount} onChange={(e) => setNodeCount(e.target.value)}>
                <option value="">자동 (정책 기본값{autoLabel ? `: ${autoLabel}` : ""})</option>
                {Array.from({ length: 16 }, (_, i) => i + 1).map((n) => (
                  <option key={n} value={String(n)}>
                    {n}
                  </option>
                ))}
              </select>
              <small className="muted">
                자동이 기본. 적격 노드 수가 정책 요구치보다 적어 실행이 거부될 때
                (insufficient_eligible_nodes) 노드 수를 낮추면 통과한다.
              </small>
            </label>

            <label>
              <span>스토리지 *</span>
              <select value={storage} onChange={(e) => setStorage(e.target.value)}>
                <option value="">선택…</option>
                {storages.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
                {storage && !storages.includes(storage) && (
                  <option value={storage}>{storage}</option>
                )}
              </select>
            </label>

            <button
              type="button"
              className="ghost mini section-toggle"
              onClick={() => setShowOptions((v) => !v)}
            >
              {showOptions ? "▾" : "▸"} 스캔 옵션
            </button>
            {showOptions && (
              <div className="sync-options">{SCAN_OPTION_FIELDS.map(renderOption)}</div>
            )}

            {/* inline request table */}
            <div className="tmpl-bar">
              <span className="muted small">
                요청 목록 — 스토리지 기준 상대 경로 <code>path</code> (한 행에 한 요청)
              </span>
              <span className="spacer" />
              <span className="muted small">CSV / 텍스트</span>
              <button type="button" className="ghost mini" onClick={openTemplate}>
                템플릿
              </button>
              <button
                type="button"
                className="ghost mini"
                onClick={openCurrent}
                disabled={!rows.some((r) => r.path.trim())}
              >
                현재 항목
              </button>
              <button type="button" className="ghost mini" onClick={openUpload}>
                업로드 (전체 교체)
              </button>
            </div>

            <div className="req-table">
              <div className="req-row single req-head">
                <span>스캔 경로 (path)</span>
                <span></span>
              </div>
              {rows.map((r, i) => (
                <div className="req-row single" key={i}>
                  <input
                    className="mono"
                    value={r.path}
                    onChange={(e) => updateRow(i, e.target.value)}
                    placeholder="예: projects/teamA"
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
                요청 <strong>{rows.filter((r) => r.path.trim()).length}</strong>개
                {" "}· 스캔은 읽기 전용이라 데이터를 변경하지 않습니다.
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
        {csvModal && (
          <ScanCsvModal
            title={csvModal.title}
            hint={csvModal.hint}
            initialText={csvModal.text}
            mode={csvModal.mode}
            onReplace={replaceFromRows}
            onClose={() => setCsvModal(null)}
          />
        )}
      </div>
    </div>
  );
}
