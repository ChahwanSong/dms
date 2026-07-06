import { useEffect, useState } from "react";
import { operatorApi, type NodePolicyResp, type StorageMapping } from "../../../api";
import { validateSyncOptions } from "../backup/helpers";
import SyncOptionsFields from "../backup/SyncOptionsFields";
import { errMsg } from "../backup/BackupBatches";
import { isFsBackend } from "../storage/helpers";
import EndpointPath from "./EndpointPath";

// 데이터 Sync 요청 작성 폼 (탭 상단). ONE source → ONE destination per request
// (single-shot). Reuses the backup sync-option UI in full (sync + ownership groups
// + --delete). On submit it creates a sync_jobs row; the orchestrator previews it,
// and the operator approves it in the list below. No batch, no re-run.
export default function SyncForm({ onCreated }: { onCreated: () => void }) {
  const [srcStorage, setSrcStorage] = useState("");
  const [srcPath, setSrcPath] = useState("");
  const [dstStorage, setDstStorage] = useState("");
  const [dstPath, setDstPath] = useState("");
  const [priority, setPriority] = useState("Low");
  const [nodeCount, setNodeCount] = useState("");
  const [memo, setMemo] = useState("");
  const [deleteEnabled, setDeleteEnabled] = useState(false);
  const [options, setOptions] = useState<Record<string, unknown>>({
    open_noatime: true,
    bufsize: 4 * 1024 * 1024,
    batch_files: 100000,
  });
  const [showSync, setShowSync] = useState(false);
  const [showOwnership, setShowOwnership] = useState(false);
  const [storages, setStorages] = useState<StorageMapping[]>([]);
  const [nodePolicy, setNodePolicy] = useState<NodePolicyResp | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    operatorApi.storage
      .list()
      // sync runs on filesystem backends only (cephfs/gpfs/wekafs); k8s CSI mappings
      // are namespace-quota only and can't be a sync src/dst.
      .then((list) =>
        alive &&
          setStorages(
            list
              .filter(isFsBackend)
              .sort((a, b) => a.storage_name.localeCompare(b.storage_name)),
          ),
      )
      .catch(() => {});
    operatorApi.sync
      .nodePolicy()
      .then((p) => alive && setNodePolicy(p))
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  const deleteConfirm =
    "--delete가 켜져 있습니다. 대상(dst)에서 원본(src)에 없는 파일이 삭제됩니다. Preview로 확인 후 실행됩니다. 계속할까요?";

  async function submit() {
    setError(null);
    setOk(null);
    if (!srcStorage.trim() || !dstStorage.trim())
      return setError("출발/대상 스토리지를 선택하세요.");
    if (!srcPath.trim() || !dstPath.trim())
      return setError("출발/대상 경로를 모두 입력하세요.");
    if (srcStorage.trim() === dstStorage.trim() && srcPath.trim() === dstPath.trim())
      return setError("대상이 출발과 동일합니다.");
    const optErrors = validateSyncOptions(options);
    if (optErrors.length > 0) return setError(`sync 옵션 오류: ${optErrors.join(" · ")}`);
    if (deleteEnabled && !window.confirm(deleteConfirm)) return;

    setBusy(true);
    try {
      await operatorApi.sync.create({
        src_storage: srcStorage.trim(),
        src_path: srcPath.trim(),
        dst_storage: dstStorage.trim(),
        dst_path: dstPath.trim(),
        delete_enabled: deleteEnabled,
        options,
        priority,
        node_count: nodeCount ? Number(nodeCount) : null,
        memo: memo.trim() || null,
      });
      setOk("Sync 요청이 접수되었습니다 — 아래에서 프리뷰를 확인하고 승인하세요.");
      setSrcPath("");
      setDstPath("");
      setMemo("");
      onCreated();
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

  // "자동" (DMS policy default): dsync for same-storage, nsync for cross-storage.
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

  // Switching storage resets the endpoint path (a PV builder's assembled path is
  // meaningless for a different storage). EndpointPath is remounted via key=storage.
  const setSrc = (v: string) => {
    setSrcStorage(v);
    setSrcPath("");
  };
  const setDst = (v: string) => {
    setDstStorage(v);
    setDstPath("");
  };
  const srcMapping = storages.find((m) => m.storage_name === srcStorage);
  const dstMapping = storages.find((m) => m.storage_name === dstStorage);

  return (
    <div className="sync-form form">
      <div className="storage-row">
        <div className="sync-endpoint">
          {storageSelect(srcStorage, setSrc, "출발 스토리지 (src)")}
          <EndpointPath
            key={srcStorage}
            mapping={srcMapping}
            path={srcPath}
            onPath={setSrcPath}
            label="출발 경로 (src_path)"
            placeholder="예: e2e/src"
          />
        </div>
        <div className="sync-arrow" aria-hidden>→</div>
        <div className="sync-endpoint">
          {storageSelect(dstStorage, setDst, "대상 스토리지 (dst)")}
          <EndpointPath
            key={dstStorage}
            mapping={dstMapping}
            path={dstPath}
            onPath={setDstPath}
            label="대상 경로 (dst_path)"
            placeholder="예: e2e/dst/copy1"
          />
        </div>
      </div>

      <div className="storage-row">
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
        </label>
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

      <label>
        <span>메모 (선택)</span>
        <input value={memo} onChange={(e) => setMemo(e.target.value)} placeholder="예: 프로젝트 알파 복사" />
      </label>

      <div className="form-hints muted small">
        대상의 부모 디렉터리는 미리 존재해야 합니다(Preview에서 검증). 원본은 유지됩니다(복사).
      </div>

      {error && <div className="banner err">{error}</div>}
      {ok && <div className="banner ok">{ok}</div>}

      <div className="modal-actions">
        <button className="primary" onClick={submit} disabled={busy}>
          {busy ? "요청 중…" : "Sync 요청"}
        </button>
      </div>
    </div>
  );
}
