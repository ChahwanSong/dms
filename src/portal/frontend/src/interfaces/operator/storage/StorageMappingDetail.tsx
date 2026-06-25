import { useEffect, useState } from "react";
import { operatorApi, type StorageMapping } from "../../../api";
import { SanityBadge } from "../components/SanityBadge";
import { backendType, formatApiError, isFsBackend, quotaTitle } from "./helpers";

export default function StorageMappingDetail({
  storageName,
  onClose,
  onRecheck,
  onEdit,
  onDelete,
}: {
  storageName: string;
  onClose: () => void;
  onRecheck: (name: string) => void | Promise<void>;
  onEdit: (m: StorageMapping) => void;
  onDelete: (name: string) => void;
}) {
  const [m, setM] = useState<StorageMapping | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    operatorApi.storage
      .get(storageName)
      .then(setM)
      .catch((e) => setError(formatApiError(e)));
  }, [storageName]);

  // Recheck from inside the modal: run the parent's check (refreshes the list),
  // then re-fetch this modal's own data so it doesn't show the stale pre-check view.
  async function recheckAndRefresh() {
    setError(null);
    await onRecheck(storageName);
    try {
      setM(await operatorApi.storage.get(storageName));
    } catch (e) {
      setError(formatApiError(e));
    }
  }

  const sr = m?.sanity_result;
  const readiness = m?.readiness || sr?.readiness;
  const agent = sr?.agent_observed;
  const k8s = sr?.kubernetes_observed;
  const mutation = sr?.mutation_observed;
  // CSI (agentless namespace-quota) mappings don't track RM/DM/INV agent readiness —
  // they show a single QUOTA axis from the ResourceQuota mutation transport probe.
  const isFs = m ? isFsBackend(m) : false;

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal wide" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h3>
            {storageName} {m && <SanityBadge status={m.sanity_status} />}
          </h3>
          <button className="ghost" onClick={onClose}>
            닫기
          </button>
        </div>

        {error && <div className="banner err">{error}</div>}
        {!m && !error && <div className="muted">불러오는 중…</div>}

        {m && (
          <div className="detail-body">
            <dl className="kv">
              <dt>클러스터</dt><dd>{m.cluster_name || "—"}</dd>
              <dt>backend_type</dt><dd>{backendType(m)}</dd>
              <dt>storage class</dt><dd>{m.storage_class_name || "—"}</dd>
              <dt>version</dt><dd>{m.version}</dd>
              <dt>readiness</dt>
              <dd>
                <span className="axes">
                  {isFs ? (
                    <>
                      <span>RM: <SanityBadge status={readiness?.resource_management} /></span>
                      <span>DM: <SanityBadge status={readiness?.data_management} /></span>
                      <span>INV: <SanityBadge status={readiness?.inventory} /></span>
                    </>
                  ) : (
                    <span title={m ? quotaTitle(m) : undefined}>
                      QUOTA: <SanityBadge status={readiness?.kubernetes_mutation} />
                    </span>
                  )}
                </span>
              </dd>
              <dt>마지막 검사</dt><dd className="muted">{m.sanity_checked_at || "—"}</dd>
              <dt>갱신</dt>
              <dd className="muted">
                {m.updated_by || "—"} · {m.updated_at || "—"}
              </dd>
              {m.disabled_at && (
                <>
                  <dt>비활성</dt>
                  <dd className="san-failed">
                    {m.disabled_at} ({m.disabled_reason || "—"})
                  </dd>
                </>
              )}
            </dl>

            {sr && (
              <div className="sanity-block">
                {!!sr.errors?.length && (
                  <div className="codes err">
                    <h4>오류 (작업 차단)</h4>
                    <ul>
                      {sr.errors.map((c, i) => (
                        <li key={i}>
                          <code>{c.code}</code> {c.message}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {!!sr.warnings?.length && (
                  <div className="codes warn">
                    <h4>경고</h4>
                    <ul>
                      {sr.warnings.map((c, i) => (
                        <li key={i}>
                          <code>{c.code}</code> {c.message}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {!!sr.checks?.length && (
                  <div className="codes ok">
                    <h4>통과한 검사</h4>
                    <ul>
                      {sr.checks.map((c, i) => (
                        <li key={i}>
                          <code>{c.name}</code> {c.status}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            )}

            {(k8s || agent || mutation) && (
              <div className="observed">
                {k8s && (
                  <div>
                    <h4>Kubernetes 관측</h4>
                    <div className="muted small">
                      cluster={k8s.cluster_name ?? "—"} · sc={k8s.storage_class_name ?? "—"} ·
                      exists={String(k8s.storage_class_exists ?? "—")} · provisioner=
                      {k8s.provisioner ?? "—"}
                    </div>
                  </div>
                )}
                {!isFs && mutation && (
                  <div>
                    <h4>Quota mutation transport</h4>
                    <div className="muted small">
                      mode={mutation.mode ?? "kubectl"}
                      {mutation.control_host ? ` · control_host=${mutation.control_host}` : ""} ·
                      reachable={String(mutation.reachable ?? "—")} · can_mutate=
                      {String(mutation.can_mutate ?? "—")} · can-i(create/patch/delete)=
                      {fmtPerm(mutation.permissions?.create)}/
                      {fmtPerm(mutation.permissions?.patch)}/
                      {fmtPerm(mutation.permissions?.delete)}
                      {mutation.detail ? ` · ${mutation.detail}` : ""}
                    </div>
                  </div>
                )}
                {isFs && agent && (
                  <div>
                    <h4>Agent 관측</h4>
                    <div className="muted small">
                      fresh={agent.fresh_reports ?? 0} · stale={agent.stale_reports ?? 0} ·
                      rm={agent.rm_readiness ?? "—"} ({agent.rm_candidates?.length ?? 0}) ·
                      dm={agent.dm_readiness ?? "—"} ({agent.dm_candidates?.length ?? 0})
                    </div>
                  </div>
                )}
              </div>
            )}

            <details className="raw">
              <summary>backend_template (raw)</summary>
              <pre>{JSON.stringify(m.backend_template, null, 2)}</pre>
            </details>

            <div className="modal-actions">
              <button className="ghost" onClick={recheckAndRefresh}>
                sanity 재검사
              </button>
              <button className="ghost" onClick={() => onEdit(m)}>
                수정
              </button>
              <button className="ghost danger" onClick={() => onDelete(storageName)}>
                삭제
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// can-i permission tri-state: true -> yes, false -> no, null/undefined -> ? (unknown).
function fmtPerm(v?: boolean | null): string {
  if (v == null) return "?";
  return v ? "yes" : "no";
}
