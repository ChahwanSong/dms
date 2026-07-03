import { useEffect, useState } from "react";
import { operatorApi, type RmNodePolicyResp } from "../../../api";
import { errMsg } from "../backup/BackupBatches";
import { isFsBackend } from "../storage/helpers";

// drm advanced flags. None of these change WHAT is deleted (target决定); they only tune
// execution speed / log output. Exposed collapsed, default off. Mirrors _rm_flags in
// dms/adapters/volcano.py (stat/lite/quiet).
const RM_OPTIONS: { key: string; label: string; hint: string }[] = [
  { key: "stat", label: "--stat", hint: "삭제 통계를 로그에 출력" },
  { key: "lite", label: "--lite", hint: "빠른 삭제(스탯 호출 최소화, 대형 트리용)" },
  { key: "quiet", label: "--quiet", hint: "로그 출력 최소화" },
];

// 데이터 삭제 요청 작성 폼 (탭 상단). ONE target per request (single-shot data.rm).
// Destructive: it runs through the DMS preview(dry-run) -> 실행(confirm) flow, so the
// operator sees the delete count before anything is removed. No batch, no re-run.
export default function RmForm({ onCreated }: { onCreated: () => void }) {
  const [storage, setStorage] = useState("");
  const [path, setPath] = useState("");
  const [priority, setPriority] = useState("Low");
  const [nodeCount, setNodeCount] = useState("");
  const [memo, setMemo] = useState("");
  const [options, setOptions] = useState<Record<string, unknown>>({});
  const [showOpts, setShowOpts] = useState(false);
  const [storages, setStorages] = useState<string[]>([]);
  const [drmDefault, setDrmDefault] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    operatorApi.storage
      .list()
      // rm runs on filesystem backends only (cephfs/gpfs/wekafs); k8s CSI mappings are
      // namespace-quota only and can't be an rm target.
      .then((list) =>
        alive && setStorages(list.filter(isFsBackend).map((m) => m.storage_name).sort()),
      )
      .catch(() => {});
    operatorApi.rm
      .nodePolicy()
      .then((p: RmNodePolicyResp) => alive && setDrmDefault(p.drm?.default_worker_nodes ?? null))
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  function setOpt(key: string, on: boolean) {
    setOptions((prev) => {
      const next = { ...prev };
      if (on) {
        next[key] = true;
        // --stat and --lite are mutually exclusive (DMS rejects both).
        if (key === "stat") delete next.lite;
        if (key === "lite") delete next.stat;
      } else {
        delete next[key];
      }
      return next;
    });
  }

  async function submit() {
    setError(null);
    setOk(null);
    const s = storage.trim();
    const p = path.trim();
    if (!s) return setError("대상 스토리지를 선택하세요.");
    if (!p) return setError("대상 경로를 입력하세요.");
    if (
      !window.confirm(
        `대상 «${s}:${p}» 이하를 삭제 요청합니다.\n` +
          "먼저 Preview(dry-run)로 삭제 대상을 확인한 뒤, 아래 목록에서 '실행'을 눌러야 실제로 삭제됩니다.\n" +
          "계속할까요?",
      )
    )
      return;

    setBusy(true);
    try {
      await operatorApi.rm.create({
        target_storage: s,
        target_path: p,
        options,
        priority,
        node_count: nodeCount ? Number(nodeCount) : null,
        memo: memo.trim() || null,
      });
      setOk("삭제 요청이 접수되었습니다 — 아래에서 프리뷰(삭제 대상)를 확인하고 실행하세요.");
      setPath("");
      setMemo("");
      onCreated();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rm-form form">
      <div className="storage-row">
        <label>
          <span>대상 스토리지 (target) *</span>
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
        <label>
          <span>대상 경로 (target_path) *</span>
          <input
            className="mono"
            value={path}
            onChange={(e) => setPath(e.target.value)}
            placeholder="예: e2e/old-data"
          />
        </label>
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
            <option value="">
              자동 (정책 기본값{drmDefault != null ? `: ${drmDefault}` : ""})
            </option>
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
        onClick={() => setShowOpts((v) => !v)}
      >
        {showOpts ? "▾" : "▸"} 삭제 옵션 (고급)
      </button>
      {showOpts && (
        <div className="sync-options">
          {RM_OPTIONS.map((o) => (
            <label key={o.key} className="check-row">
              <input
                type="checkbox"
                checked={options[o.key] === true}
                onChange={(e) => setOpt(o.key, e.target.checked)}
              />
              <span>
                <code>{o.label}</code>
                <span className="muted small"> — {o.hint}</span>
              </span>
            </label>
          ))}
        </div>
      )}

      <label>
        <span>메모 (선택)</span>
        <input value={memo} onChange={(e) => setMemo(e.target.value)} placeholder="예: 만료 프로젝트 정리" />
      </label>

      <div className="form-hints muted small">
        대상 경로 <strong>이하 전체</strong>가 삭제됩니다(복구 불가). 실제 삭제 전에 Preview(dry-run)로
        삭제 대상 수를 먼저 확인합니다. 원본 삭제이므로 백업/Sync와 달리 되돌릴 수 없습니다.
      </div>

      {error && <div className="banner err">{error}</div>}
      {ok && <div className="banner ok">{ok}</div>}

      <div className="modal-actions">
        <button className="primary danger" onClick={submit} disabled={busy}>
          {busy ? "요청 중…" : "삭제 요청"}
        </button>
      </div>
    </div>
  );
}
