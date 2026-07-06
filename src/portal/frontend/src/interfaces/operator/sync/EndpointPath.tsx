import { useEffect, useState } from "react";
import type { StorageMapping } from "../../../api";
import { backendType, isForPv, managedRoot } from "../storage/helpers";

type Kind = "plain" | "ceph" | "gpfs" | "pv-other";

// A managed_root-relative path input that adapts to the selected storage. For a PVC(PV)-
// backend filesystem it collects the PV-specific components and assembles the relative path
// (ceph: csi/<subvol>/<uuid>, gpfs: pvc-<uid>/pvc-<uid>-data); otherwise a plain path input.
// Once a storage is picked it also previews the resolved location (managed_root + the
// relative path). The relative string is emitted via onPath and sent verbatim as the DMS
// path. Remount per storage (key=storage_name in the parent) so switching storages resets
// the sub-fields. Reused by 데이터 Sync (src/dst) and 데이터 삭제 (target).
export default function EndpointPath({
  mapping,
  path,
  onPath,
  label,
  placeholder,
}: {
  mapping: StorageMapping | undefined;
  path: string;
  onPath: (p: string) => void;
  label: string;
  placeholder: string;
}) {
  const pv = mapping ? isForPv(mapping) : false;
  const bt = mapping ? backendType(mapping) : "";
  const kind: Kind = pv
    ? bt === "cephfs"
      ? "ceph"
      : bt === "gpfs"
        ? "gpfs"
        : "pv-other"
    : "plain";
  const mr = mapping ? managedRoot(mapping) : null;

  const [subvol, setSubvol] = useState("");
  const [uuid, setUuid] = useState("");
  const [uid, setUid] = useState("");
  const [sub, setSub] = useState("");

  // Assemble the managed_root-relative path from the PV components and emit it.
  useEffect(() => {
    const tail = sub.trim().replace(/^\/+/, "");
    const join = (base: string) => (base ? (tail ? `${base}/${tail}` : base) : "");
    if (kind === "ceph") onPath(join(subvol && uuid ? `csi/${subvol}/${uuid}` : ""));
    else if (kind === "gpfs") onPath(join(uid ? `pvc-${uid}/pvc-${uid}-data` : ""));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind, subvol, uuid, uid, sub]);

  const hint =
    kind === "ceph"
      ? "csi/<subvol>/<uuid>"
      : kind === "gpfs"
        ? "pvc-<uid>/pvc-<uid>-data"
        : "<경로>";

  return (
    <div className="endpoint-path">
      {kind === "plain" || kind === "pv-other" ? (
        <label>
          <span>
            {label} *
            {kind === "pv-other" && <span className="muted small"> · PV(경로 직접 입력)</span>}
          </span>
          <input
            className="mono"
            value={path}
            onChange={(e) => onPath(e.target.value)}
            placeholder={placeholder}
          />
        </label>
      ) : (
        <div className="pv-path">
          <div className="pv-path-head">
            <span>{label} *</span>
            <span className="tag tag-pv">{kind === "ceph" ? "CephFS PV" : "GPFS PV"}</span>
          </div>
          <div className="pv-path-fields">
            {kind === "ceph" ? (
              <>
                <label>
                  <span>subvol</span>
                  <input
                    className="mono"
                    value={subvol}
                    onChange={(e) => setSubvol(e.target.value.trim())}
                    placeholder="csi-vol-d84537e3-4ab6-45fd-89c2-ba0f664cadac"
                  />
                </label>
                <label>
                  <span>uuid (데이터 디렉터리)</span>
                  <input
                    className="mono"
                    value={uuid}
                    onChange={(e) => setUuid(e.target.value.trim())}
                    placeholder="4fd9e534-f1ee-4bbc-be18-a3c5f7710ca3"
                  />
                </label>
              </>
            ) : (
              <label>
                <span>PVC uid</span>
                <input
                  className="mono"
                  value={uid}
                  onChange={(e) => setUid(e.target.value.trim())}
                  placeholder="0ba71aad-d68d-44d7-a300-d6597b51fef3"
                />
              </label>
            )}
            <label>
              <span>하위 경로 (선택)</span>
              <input
                className="mono"
                value={sub}
                onChange={(e) => setSub(e.target.value)}
                placeholder="예: data 또는 subdir/file"
              />
            </label>
          </div>
        </div>
      )}

      {mapping && (
        <div className="path-resolve">
          <span className="muted small">위치 (managed_root 기준)</span>
          <code>
            {mr && <span className="muted">{mr.replace(/\/+$/, "")}/</span>}
            <span className={path ? undefined : "muted"}>{path || hint}</span>
          </code>
        </div>
      )}
    </div>
  );
}
