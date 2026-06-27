import { useState } from "react";
import { operatorApi, type StorageMapping } from "../../../api";
import {
  BACKEND_SKELETONS,
  CSI_BACKEND_TYPES,
  FIELD_DOCS,
  FS_BACKEND_TYPES,
  formatApiError,
  pickStr,
  templateForEdit,
} from "./helpers";

// Create/edit a storage mapping. The whole backend config — including
// cluster_name and storage_class_name — is edited as ONE backend_template JSON.
// On submit the form derives the top-level StorageMappingInput fields DMS reads
// (cluster_name, storage_class_name) from the template. DMS PATCH takes the FULL
// input, so on edit the template is pre-filled from the current state (top-level
// values merged in) and the whole thing is re-sent. Secrets (weka password) come
// back redacted as "***"; leaving them is safe — DMS merges the stored secret.
export default function StorageMappingForm({
  mode,
  initial,
  onClose,
  onSaved,
}: {
  mode: "create" | "edit";
  initial?: StorageMapping;
  onClose: () => void;
  onSaved: (name: string, status: string) => void;
}) {
  const [storageName, setStorageName] = useState(initial?.storage_name ?? "");
  const [templateText, setTemplateText] = useState<string>(
    JSON.stringify(
      initial ? templateForEdit(initial) : BACKEND_SKELETONS["cephfs"],
      null,
      2,
    ),
  );
  const initialBackendType = initial?.backend_template?.backend_type;
  const [skeleton, setSkeleton] = useState<string>(
    typeof initialBackendType === "string" && BACKEND_SKELETONS[initialBackendType]
      ? initialBackendType
      : "cephfs",
  );
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Changing the backend_type immediately refills the template (no separate button).
  function fillSkeleton(bt: string) {
    setSkeleton(bt);
    setTemplateText(JSON.stringify(BACKEND_SKELETONS[bt], null, 2));
    setError(null);
  }

  // Drive the field reference off the backend_type actually in the editor; fall
  // back to the skeleton dropdown while the JSON is mid-edit/invalid.
  const currentBackend = (() => {
    try {
      const bt = JSON.parse(templateText)?.backend_type;
      if (typeof bt === "string" && FIELD_DOCS[bt]) return bt;
    } catch {
      /* ignore invalid JSON */
    }
    return FIELD_DOCS[skeleton] ? skeleton : "cephfs";
  })();
  const fieldDocs = FIELD_DOCS[currentBackend] ?? [];

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    if (!storageName.trim()) {
      setError("storage_name은 필수입니다.");
      return;
    }
    let backend_template: Record<string, unknown>;
    try {
      backend_template = JSON.parse(templateText);
    } catch (err) {
      setError(`backend_template JSON 파싱 오류: ${(err as Error).message}`);
      return;
    }
    if (
      backend_template === null ||
      typeof backend_template !== "object" ||
      Array.isArray(backend_template)
    ) {
      setError("backend_template은 JSON 객체여야 합니다.");
      return;
    }

    // Enforce the required fields marked in FIELD_DOCS for this backend_type. A
    // string field must be present and non-blank; object fields (e.g. weka_credentials)
    // only need to be present (DMS validates their contents server-side).
    const missing = fieldDocs
      .filter((f) => f.required)
      .filter((f) => {
        const v = (backend_template as Record<string, unknown>)[f.name];
        if (v === undefined || v === null) return true;
        if (typeof v === "string") return v.trim() === "";
        if (Array.isArray(v)) return v.length === 0;
        return false;
      })
      .map((f) => f.name);
    if (missing.length > 0) {
      setError(`필수 필드 누락: ${missing.join(", ")}`);
      return;
    }

    // Cross-field: control_host is only consumed by mutation_mode='ssh-kubectl'. With the
    // default mutation mode 'kubectl' it would be ignored, so DMS rejects a control_host
    // set without an explicit mutation_mode (422). Catch it client-side for a clearer message.
    const bt = backend_template as Record<string, unknown>;
    const ctlHost =
      typeof bt.control_host === "string" ? bt.control_host.trim() : bt.control_host;
    const mutMode =
      typeof bt.mutation_mode === "string" ? bt.mutation_mode.trim() : bt.mutation_mode;
    if (ctlHost && !mutMode) {
      setError(
        "control_host를 쓰려면 mutation_mode=\"ssh-kubectl\"을 함께 지정해야 합니다 " +
          "(기본 kubectl 모드에선 control_host가 무시됩니다).",
      );
      return;
    }

    // DMS reads cluster_name / storage_class_name from the top-level input; derive
    // them from the template so the operator only edits one place.
    const payload = {
      storage_name: storageName.trim(),
      backend_template,
      cluster_name: pickStr(backend_template, "cluster_name"),
      storage_class_name: pickStr(backend_template, "storage_class_name"),
      version: initial?.version ?? 1,
    };

    setBusy(true);
    try {
      const res =
        mode === "create"
          ? await operatorApi.storage.create(payload)
          : await operatorApi.storage.update(initial!.storage_name, payload);
      onSaved(res.storage_name, res.status);
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal wide" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h3>{mode === "create" ? "새 스토리지 매핑" : `매핑 수정 · ${storageName}`}</h3>
          <button className="ghost" onClick={onClose}>
            닫기
          </button>
        </div>

        <form className="form" onSubmit={submit}>
          {mode === "edit" && (
            <div className="banner info">
              <strong>저장하면 변경사항이 즉시 적용됩니다.</strong> 전체 설정을 다시 보내
              교체하고(부분 수정 아님) sanity를 재검사합니다.
              <ul>
                <li>
                  <code>storage_name</code>은 식별자라 변경할 수 없습니다.
                </li>
                <li>
                  아래 <strong>필드 설명</strong>의 <code>{currentBackend}</code> 필드만 실제
                  효과가 있습니다 — 표에 없는 키는 저장돼도 무시됩니다.
                </li>
                <li>
                  해당 스토리지에 <strong>진행 중인 작업</strong>이 있으면 수정이 거부됩니다(409).
                  작업이 끝난 뒤 다시 시도하세요.
                </li>
                <li>
                  비밀번호가 <code>***</code>로 보이면 그대로 두세요 — 기존 값이 유지됩니다.
                </li>
              </ul>
            </div>
          )}
          <label>
            {mode === "edit" ? "storage_name (변경 불가)" : "storage_name *"}
            <input
              value={storageName}
              onChange={(e) => setStorageName(e.target.value)}
              disabled={mode === "edit"}
              autoFocus={mode === "create"}
            />
          </label>

          <div className="tmpl-bar">
            <span className="muted small">backend_template (JSON)</span>
            <span className="spacer" />
            <label className="tmpl-select">
              <span className="muted small">backend_type</span>
              <select value={skeleton} onChange={(e) => fillSkeleton(e.target.value)}>
                <optgroup label="filesystem (fs)">
                  {FS_BACKEND_TYPES.map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                </optgroup>
                <optgroup label="k8s CSI (csi)">
                  {CSI_BACKEND_TYPES.map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                </optgroup>
              </select>
            </label>
          </div>
          <textarea
            className="json"
            value={templateText}
            onChange={(e) => setTemplateText(e.target.value)}
            spellCheck={false}
            rows={16}
          />
          <ul className="form-hints muted small">
            <li>
              <strong>filesystem (fs)</strong> (cephfs · gpfs · wekafs): filesystem 전용 — CSI 정보
              (<code>csi_driver</code> · <code>storage_class_name</code>) 없음
            </li>
            <li>
              <strong>k8s CSI (csi)</strong> (ceph-csi · gpfs-csi · weka-csi): CSI StorageClass · driver 보유
            </li>
            <li>
              <code>cluster_name</code> · <code>storage_class_name</code>은 제출 시 DMS가 읽는 상위 필드로
              자동 반영됩니다
            </li>
            <li>backend_type을 바꾸면 템플릿이 자동으로 다시 채워집니다</li>
            <li>템플릿은 예시값이므로 실제 환경 값으로 수정하세요</li>
          </ul>

          {fieldDocs.length > 0 && (
            <div className="field-ref">
              <div className="field-ref-head">
                <code>{currentBackend}</code> 필드 설명
              </div>
              <table className="field-ref-table">
                <thead>
                  <tr>
                    <th>필드</th>
                    <th>구분</th>
                    <th>설명</th>
                    <th>기본값</th>
                  </tr>
                </thead>
                <tbody>
                  {fieldDocs.map((f) => (
                    <tr key={f.name}>
                      <td>
                        <code>{f.name}</code>
                        {f.secret && <span title="secret"> 🔒</span>}
                      </td>
                      <td>
                        <span className={f.required ? "fr-req" : "fr-opt"}>
                          {f.required ? "필수" : "선택"}
                        </span>
                      </td>
                      <td>{f.desc}</td>
                      <td className="muted">{f.default}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {error && <div className="banner err">{error}</div>}

          <div className="modal-actions">
            <button type="button" className="ghost" onClick={onClose} disabled={busy}>
              취소
            </button>
            <button type="submit" className="primary" disabled={busy}>
              {busy ? "저장 중…" : mode === "create" ? "생성" : "저장"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
