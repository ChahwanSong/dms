import { useEffect, useMemo, useState } from "react";
import { userSyncApi, type UserStorage } from "../../../api";
import { errMsg } from "./helpers";

// 사용자 데이터 Sync 요청 작성 폼. 운영자 단일 Sync와 동일한 흐름이되 제한됨:
//  - 파일시스템↔파일시스템 또는 PVC↔PVC 만 가능 (혼합은 운영자에게 요청).
//  - 옵션 고정: 우선순위 중간, 병렬 노드 정책 기본값, open_noatime ON, batch_files
//    100000, bufsize 4MiB, 소유권(chmod/chown) 없음. 사용자 선택: --delete · contents.
export default function UserSyncForm({ onCreated }: { onCreated: () => void }) {
  const [srcStorage, setSrcStorage] = useState("");
  const [srcPath, setSrcPath] = useState("");
  const [dstStorage, setDstStorage] = useState("");
  const [dstPath, setDstPath] = useState("");
  const [deleteEnabled, setDeleteEnabled] = useState(false);
  const [contents, setContents] = useState(false);
  const [memo, setMemo] = useState("");
  const [storages, setStorages] = useState<UserStorage[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    userSyncApi
      .storages()
      .then((list) => alive && setStorages(list))
      .catch((e) => alive && setError(errMsg(e)));
    return () => {
      alive = false;
    };
  }, []);

  const srcM = useMemo(() => storages.find((s) => s.storage_name === srcStorage), [storages, srcStorage]);
  const dstM = useMemo(() => storages.find((s) => s.storage_name === dstStorage), [storages, dstStorage]);

  // FS↔PVC 혼합 사전 감지 (서버에서도 재검증). 둘 다 선택됐고 subtype이 다르면 차단.
  const mixed =
    !!srcM && !!dstM && srcM.filesystem_subtype !== dstM.filesystem_subtype;
  // PVC↔PVC는 현재 root로 실행 → 네임스페이스 권한 확인 기능 전까지 --delete 금지.
  const bothPv =
    srcM?.filesystem_subtype === "pv" && dstM?.filesystem_subtype === "pv";

  const deleteConfirm =
    "--delete가 켜져 있습니다. 대상(dst)에서 원본(src)에 없는 파일이 삭제됩니다. Preview로 확인 후 실행됩니다. 계속할까요?";

  async function submit() {
    setError(null);
    setOk(null);
    if (!srcStorage || !dstStorage) return setError("출발/대상 스토리지를 선택하세요.");
    if (!srcPath.trim() || !dstPath.trim())
      return setError("출발/대상 경로를 모두 입력하세요.");
    if (srcStorage === dstStorage && srcPath.trim() === dstPath.trim())
      return setError("대상이 출발과 동일합니다.");
    if (mixed)
      return setError(
        "파일시스템과 K8S PVC 간 sync는 직접 실행할 수 없습니다. 운영자에게 요청하세요.",
      );
    if (bothPv && deleteEnabled)
      return setError(
        "PVC↔PVC sync은 현재 root로 실행되어 --delete를 사용할 수 없습니다. 삭제가 필요하면 운영자에게 요청하세요.",
      );
    if (deleteEnabled && !window.confirm(deleteConfirm)) return;

    setBusy(true);
    try {
      await userSyncApi.create({
        src_storage: srcStorage,
        src_path: srcPath.trim(),
        dst_storage: dstStorage,
        dst_path: dstPath.trim(),
        delete_enabled: deleteEnabled,
        contents,
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

  function storageSelect(
    value: string,
    setter: (v: string) => void,
    label: string,
    mapping?: UserStorage,
  ) {
    return (
      <label>
        <span>
          {label} *
          {mapping && (
            <span className={`chip small ${mapping.filesystem_subtype === "pv" ? "tone-low" : ""}`}>
              {mapping.filesystem_subtype === "pv" ? "PVC" : "파일시스템"}
            </span>
          )}
        </span>
        <select value={value} onChange={(e) => setter(e.target.value)}>
          <option value="">선택…</option>
          {storages.map((m) => (
            <option key={m.storage_name} value={m.storage_name}>
              {m.storage_name} ({m.filesystem_subtype === "pv" ? "PVC" : "FS"})
            </option>
          ))}
        </select>
      </label>
    );
  }

  const setSrc = (v: string) => {
    setSrcStorage(v);
    setSrcPath("");
  };
  const setDst = (v: string) => {
    setDstStorage(v);
    setDstPath("");
  };

  return (
    <div className="sync-form form">
      <div className="storage-row">
        <div className="sync-endpoint">
          {storageSelect(srcStorage, setSrc, "출발 스토리지 (src)", srcM)}
          <label>
            <span>출발 경로 (src_path) *</span>
            <input
              value={srcPath}
              onChange={(e) => setSrcPath(e.target.value)}
              placeholder="예: projects/alpha"
            />
            {srcM?.managed_root && (
              <span className="muted small">
                {srcM.managed_root}/{srcPath || "…"}
              </span>
            )}
          </label>
        </div>
        <div className="sync-arrow" aria-hidden>→</div>
        <div className="sync-endpoint">
          {storageSelect(dstStorage, setDst, "대상 스토리지 (dst)", dstM)}
          <label>
            <span>대상 경로 (dst_path) *</span>
            <input
              value={dstPath}
              onChange={(e) => setDstPath(e.target.value)}
              placeholder="예: projects/alpha-copy"
            />
            {dstM?.managed_root && (
              <span className="muted small">
                {dstM.managed_root}/{dstPath || "…"}
              </span>
            )}
          </label>
        </div>
      </div>

      {mixed && (
        <div className="banner err">
          파일시스템 ↔ K8S PVC 간 sync는 직접 실행할 수 없습니다. 운영자에게 요청하세요.
          (파일시스템↔파일시스템 또는 PVC↔PVC 만 가능)
        </div>
      )}

      <div className="sync-options">
        <label className="check-row">
          <input
            type="checkbox"
            checked={deleteEnabled && !bothPv}
            disabled={bothPv}
            onChange={(e) => setDeleteEnabled(e.target.checked)}
          />
          <span>
            <strong className={deleteEnabled && !bothPv ? "err-num" : undefined}>--delete</strong> (미러):
            dst에서 src에 없는 파일을 삭제 — 파괴적, Preview로 확인 후 실행
            {bothPv && (
              <span className="muted small"> · PVC↔PVC(root 실행)에서는 사용 불가 — 운영자에게 요청</span>
            )}
          </span>
        </label>
        <label className="check-row">
          <input
            type="checkbox"
            checked={contents}
            onChange={(e) => setContents(e.target.checked)}
          />
          <span>
            <strong>contents</strong>: 파일 내용까지 비교(느리지만 정확) — 크기·시각만이
            아닌 바이트 단위 비교
          </span>
        </label>
      </div>

      <label>
        <span>메모 (선택)</span>
        <input value={memo} onChange={(e) => setMemo(e.target.value)} placeholder="예: 프로젝트 알파 복사" />
      </label>

      <div className="form-hints muted small">
        대상의 부모 디렉터리는 미리 존재해야 하며(Preview에서 검증), 원본은 유지됩니다(복사).
      </div>

      {error && <div className="banner err">{error}</div>}
      {ok && <div className="banner ok">{ok}</div>}

      <div className="modal-actions">
        <button className="primary" onClick={submit} disabled={busy || mixed}>
          {busy ? "요청 중…" : "Sync 요청"}
        </button>
      </div>
    </div>
  );
}
