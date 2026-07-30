import { useEffect, useState } from "react";
import {
  operatorApi,
  type BackupBatch,
  type NodePolicyResp,
  type StorageMapping,
} from "../../../api";
import {
  optionsWithoutDelete,
  rowsToCsv,
  validateSyncOptions,
  CSV_TEMPLATE_TEXT,
  CSV_FORMAT_HINT,
  type BackupRow,
} from "./helpers";
import SyncOptionsFields from "./SyncOptionsFields";
import BackupCsvModal from "./BackupCsvModal";
import { errMsg } from "./BackupBatches";
import { isFsBackend, isForPv, managedRoot, backendType } from "../storage/helpers";
import EndpointPath from "../sync/EndpointPath";
import Loading from "../../../components/Loading";

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
  // "" = 자동 (DMS 정책 기본값). 교차 스토리지(노드 < 정책 요구치)에서 노드 수를
  // 낮춰 잡으면 dsync/nsync 전처리 거부를 피할 수 있다.
  const [nodeCount, setNodeCount] = useState<string>(
    initial?.node_count != null ? String(initial.node_count) : "",
  );
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
  const [storages, setStorages] = useState<StorageMapping[]>([]);
  // PV 경로 도우미(ceph/gpfs PV): src/dst 조립 초안 + remount 리셋용 키.
  const [srcDraft, setSrcDraft] = useState("");
  const [dstDraft, setDstDraft] = useState("");
  const [pvKey, setPvKey] = useState(0);
  const [nodePolicy, setNodePolicy] = useState<NodePolicyResp | null>(null);
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
  const [csvModal, setCsvModal] = useState<{
    mode: "view" | "replace";
    title: string;
    hint?: string;
    text: string;
  } | null>(null);

  // Load the storage list (for the dropdowns) and, in edit mode, the batch's
  // existing requests (to populate the table + derive batch-level storage).
  useEffect(() => {
    let alive = true;
    operatorApi.storage
      .list()
      // Data backup runs on filesystem backends only (cephfs/gpfs/wekafs); k8s CSI
      // mappings (ceph-csi/weka-csi/gpfs-csi) are not host-mounted on the DM agents
      // and can't be a backup src/dst, so exclude them from the storage candidates.
      .then(
        (list) =>
          alive &&
          setStorages(
            list
              .filter(isFsBackend)
              .sort((a, b) => a.storage_name.localeCompare(b.storage_name)),
          ),
      )
      .catch(() => {});
    // What "자동" (DMS policy default) resolves to, to surface the actual number.
    operatorApi.backup
      .nodePolicy()
      .then((p) => alive && setNodePolicy(p))
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
  // PV 도우미: 조립된 src/dst 경로를 완결 요청 행으로 추가하고 빌더를 초기화(remount).
  function addPvRow() {
    const sp = srcDraft.trim();
    const dp = dstDraft.trim();
    if (!sp || !dp) return;
    setRows((r) => [...r, { src_path: sp, dst_path: dp }]);
    setSrcDraft("");
    setDstDraft("");
    setPvKey((k) => k + 1);
  }

  // CSV/text popups (consistent with the batch-detail tab). "현재 항목" copies the
  // current table as text; "업로드 (전체 교체)" pastes/loads text → replaces rows.
  function openTemplate() {
    setCsvModal({
      mode: "view",
      title: "CSV / 텍스트 템플릿 (예시)",
      hint: CSV_FORMAT_HINT,
      text: CSV_TEMPLATE_TEXT,
    });
  }
  function openCurrent() {
    const content = rows.filter((r) => r.src_path.trim() && r.dst_path.trim());
    setCsvModal({
      mode: "view",
      title: "현재 항목 (CSV / 텍스트)",
      hint: `${content.length}개 항목. 복사해 다른 배치에 붙여넣을 수 있습니다.`,
      text: rowsToCsv(content),
    });
  }
  function openUpload() {
    setCsvModal({ mode: "replace", title: "텍스트 붙여넣기 → 전체 교체", hint: CSV_FORMAT_HINT, text: "" });
  }
  function replaceFromRows(newRows: BackupRow[]) {
    const existing = rows.filter((r) => r.src_path.trim() && r.dst_path.trim()).length;
    if (existing > 0 && !window.confirm(`현재 ${existing}개 항목을 ${newRows.length}개로 교체합니다. 계속할까요?`))
      return;
    setRows(newRows);
    setWarn(null);
    setCsvModal(null);
  }

  const deleteConfirm =
    "--delete가 켜져 있습니다. 대상(dst)에서 원본(src)에 없는 파일이 삭제됩니다. Preview로 확인 후 실행됩니다. 계속할까요?";

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
    const node_count = nodeCount ? Number(nodeCount) : null;
    setBusy(true);
    try {
      if (isEdit && initial) {
        await operatorApi.backup.update(initial.id, {
          name: name.trim(),
          note: note.trim() || null,
          delete_enabled: deleteEnabled,
          options,
          priority,
          node_count,
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

  function storageSelect(value: string, setter: (v: string) => void, label: string) {
    return (
      <label>
        <span>{label} *</span>
        <select value={value} onChange={(e) => setter(e.target.value)}>
          <option value="">선택…</option>
          {storages.map((m) => (
            <option key={m.storage_name} value={m.storage_name}>
              {m.storage_name}
            </option>
          ))}
          {value && !storages.some((m) => m.storage_name === value) && (
            <option value={value}>{value}</option>
          )}
        </select>
      </label>
    );
  }

  // What "자동" resolves to: dsync default for same-storage backups, nsync for
  // cross-storage. Before both storages are picked, show the shared value (or both
  // if the policies differ). Null when the policy hasn't loaded yet.
  const dsyncN = nodePolicy?.dsync?.default_worker_nodes ?? null;
  const nsyncN = nodePolicy?.nsync?.default_worker_nodes ?? null;
  const sameStorage = !!srcStorage && srcStorage === dstStorage;
  const crossStorage = !!srcStorage && !!dstStorage && srcStorage !== dstStorage;
  let autoLabel: string | null;
  if (sameStorage) autoLabel = dsyncN != null ? String(dsyncN) : null;
  else if (crossStorage) autoLabel = nsyncN != null ? String(nsyncN) : null;
  else if (dsyncN != null && nsyncN != null)
    autoLabel = dsyncN === nsyncN ? String(dsyncN) : `동일 ${dsyncN} · 교차 ${nsyncN}`;
  else autoLabel = dsyncN != null ? String(dsyncN) : nsyncN != null ? String(nsyncN) : null;

  // Selected storages → managed_root notes + (ceph/gpfs PV) guided path builder.
  // Storage is batch-level, so PV-ness applies to every row on that side. The 도우미
  // assembles a complete src→dst pair (each side adapts: PV builder or raw input) and
  // appears when either side is a ceph/gpfs PV. wekafs PV keeps raw rows.
  const srcMapping = storages.find((m) => m.storage_name === srcStorage);
  const dstMapping = storages.find((m) => m.storage_name === dstStorage);
  const srcMr = srcMapping ? managedRoot(srcMapping) : null;
  const dstMr = dstMapping ? managedRoot(dstMapping) : null;
  const isPvBuilder = (m?: StorageMapping) =>
    !!m && isForPv(m) && ["cephfs", "gpfs"].includes(backendType(m));
  const pvBuilder = isPvBuilder(srcMapping) || isPvBuilder(dstMapping);

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
          <Loading rows={3} />
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
                자동이 기본. 출발·도착 스토리지가 걸친 노드 수가 정책 요구치보다
                적어 Preview가 거부될 때(insufficient_eligible_nodes) 노드 수를 그
                노드 수에 맞춰 낮추면 통과한다.
              </small>
            </label>

            <div className="storage-row">
              {storageSelect(
                srcStorage,
                (v) => {
                  setSrcStorage(v);
                  setSrcDraft("");
                },
                "출발 스토리지 (src)",
              )}
              {storageSelect(
                dstStorage,
                (v) => {
                  setDstStorage(v);
                  setDstDraft("");
                },
                "대상 스토리지 (dst)",
              )}
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
                    dst에서 src에 없는 파일을 삭제 — 파괴적, Preview로 확인 후 실행
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

            {(srcMr || dstMr) && (
              <div className="muted small mr-note">
                상대 경로 기준 —{" "}
                {srcMr && (
                  <>
                    출발 <code>{srcMr.replace(/\/+$/, "")}/</code>
                  </>
                )}
                {srcMr && dstMr && " · "}
                {dstMr && (
                  <>
                    대상 <code>{dstMr.replace(/\/+$/, "")}/</code>
                  </>
                )}
              </div>
            )}
            {pvBuilder && (
              <div className="pv-adder pv-adder-pair">
                <EndpointPath
                  key={`s:${srcStorage}:${pvKey}`}
                  mapping={srcMapping}
                  path={srcDraft}
                  onPath={setSrcDraft}
                  label="출발 (src)"
                  placeholder="예: e2e/src"
                  required={false}
                />
                <EndpointPath
                  key={`d:${dstStorage}:${pvKey}`}
                  mapping={dstMapping}
                  path={dstDraft}
                  onPath={setDstDraft}
                  label="대상 (dst)"
                  placeholder="예: backup/day1"
                  required={false}
                />
                <div className="pv-adder-foot">
                  <button
                    type="button"
                    className="ghost mini"
                    disabled={!srcDraft.trim() || !dstDraft.trim()}
                    onClick={addPvRow}
                  >
                    + 행 추가
                  </button>
                </div>
              </div>
            )}

            {/* inline request table */}
            <div className="tmpl-bar">
              <span className="muted small">
                요청 목록 — 스토리지 기준 상대 경로 <code>src_path</code> → <code>dst_path</code> (한 행에 한 요청)
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
                disabled={!rows.some((r) => r.src_path.trim() && r.dst_path.trim())}
              >
                현재 항목
              </button>
              <button type="button" className="ghost mini" onClick={openUpload}>
                업로드 (전체 교체)
              </button>
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
                {" "}· 대상의 부모 디렉터리는 미리 존재해야 합니다(Preview에서 검증).
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
          <BackupCsvModal
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
