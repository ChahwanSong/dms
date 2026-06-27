import { useEffect, useState, type ReactNode } from "react";
import { operatorApi, type StorageMapping } from "../../../api";
import { SanityBadge } from "../components/SanityBadge";
import { SpecGrid, BoolChip, type KV } from "../../../components/SpecGrid";
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
            <SpecGrid items={overviewItems(m, readiness, isFs)} />

            {(() => {
              const cfg = configItems(m);
              return cfg.length > 0 ? (
                <section className="obs-card">
                  <h4>경로 · 구성</h4>
                  <SpecGrid items={cfg} />
                </section>
              ) : null;
            })()}

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
                    <h4>통과한 검사 ({sr.checks.length})</h4>
                    <div className="check-chips">
                      {sr.checks.map((c, i) => (
                        <span
                          key={i}
                          className={`chip ${c.status === "Passed" ? "tone-ok" : "tone-unknown"}`}
                          title={`${c.name}: ${c.status}`}
                        >
                          {c.name}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}

            {(k8s || (isFs && agent) || (!isFs && mutation)) && (
              <div className="obs-cards">
                {k8s && (
                  <section className="obs-card">
                    <h4>Kubernetes 관측</h4>
                    <SpecGrid
                      items={[
                        { label: "클러스터", value: k8s.cluster_name ?? "—" },
                        {
                          label: "storage class",
                          value: k8s.storage_class_name ?? "—",
                          mono: !!k8s.storage_class_name,
                        },
                        { label: "SC 존재", value: <BoolChip value={k8s.storage_class_exists} /> },
                        {
                          label: "provisioner",
                          value: k8s.provisioner ?? "—",
                          mono: !!k8s.provisioner,
                          span: true,
                        },
                      ]}
                    />
                  </section>
                )}
                {!isFs && mutation && (
                  <section className="obs-card">
                    <h4>Quota mutation transport</h4>
                    <SpecGrid
                      items={[
                        { label: "모드", value: mutation.mode ?? "kubectl" },
                        ...(mutation.control_host
                          ? [{ label: "control host", value: mutation.control_host, mono: true } as KV]
                          : []),
                        { label: "도달", value: <BoolChip value={mutation.reachable} /> },
                        { label: "변경 가능", value: <BoolChip value={mutation.can_mutate} /> },
                        { label: "can-i · create", value: <BoolChip value={mutation.permissions?.create} /> },
                        { label: "can-i · patch", value: <BoolChip value={mutation.permissions?.patch} /> },
                        { label: "can-i · delete", value: <BoolChip value={mutation.permissions?.delete} /> },
                        ...(mutation.detail
                          ? [{ label: "detail", value: mutation.detail, span: true } as KV]
                          : []),
                      ]}
                    />
                  </section>
                )}
                {isFs && agent && (
                  <section className="obs-card">
                    <h4>Agent 관측 (RM/DM 작업 준비도)</h4>
                    <SpecGrid
                      items={[
                        {
                          label: "RM 준비",
                          value: (
                            <span className="axes">
                              <SanityBadge status={agent.rm_readiness} />
                              <span className="muted small">
                                준비 노드 {agent.rm_candidates?.length ?? 0}
                              </span>
                            </span>
                          ),
                        },
                        {
                          label: "DM 준비",
                          value: (
                            <span className="axes">
                              <SanityBadge status={agent.dm_readiness} />
                              <span className="muted small">
                                준비 노드 {agent.dm_candidates?.length ?? 0}
                              </span>
                            </span>
                          ),
                        },
                        {
                          label: "Fresh 리포트",
                          value: (
                            <span className={(agent.fresh_reports ?? 0) > 0 ? "ok-num" : "err-num"}>
                              {agent.fresh_reports ?? 0}
                            </span>
                          ),
                        },
                        { label: "Stale 리포트", value: <span className="muted">{agent.stale_reports ?? 0}</span> },
                      ]}
                    />
                    <p className="obs-note">
                      에이전트가 <b>최근에 보고</b>한 스토리지 노드가 RM/DM 작업 대상입니다. Fresh
                      리포트가 0이면 해당 노드의 에이전트가 동작하지 않는 상태이며, Stale 리포트는
                      신선도 기준을 지난 과거 보고 누계(정상적으로 쌓임)입니다.
                    </p>
                  </section>
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

function fmtTime(iso?: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

// Top-level identity/status of a mapping.
function overviewItems(
  m: StorageMapping,
  readiness: StorageMapping["readiness"],
  isFs: boolean,
): KV[] {
  const axes: ReactNode = isFs ? (
    <span className="axes">
      <span>RM: <SanityBadge status={readiness?.resource_management} /></span>
      <span>DM: <SanityBadge status={readiness?.data_management} /></span>
      <span>INV: <SanityBadge status={readiness?.inventory} /></span>
    </span>
  ) : (
    <span className="axes" title={quotaTitle(m)}>
      QUOTA: <SanityBadge status={readiness?.kubernetes_mutation} />
    </span>
  );
  const items: KV[] = [
    { label: "클러스터", value: m.cluster_name || "—" },
    { label: "backend", value: backendType(m) },
    { label: "storage class", value: m.storage_class_name || "—", mono: !!m.storage_class_name },
    { label: "version", value: String(m.version) },
    { label: "readiness", value: axes, span: true },
    { label: "마지막 검사", value: fmtTime(m.sanity_checked_at), span: true },
    { label: "갱신", value: `${m.updated_by || "—"} · ${fmtTime(m.updated_at)}`, span: true },
  ];
  if (m.disabled_at)
    items.push({
      label: "비활성",
      value: `${m.disabled_at} (${m.disabled_reason || "—"})`,
      tone: "tone-danger-text",
      span: true,
    });
  return items;
}

// Path/config fields lifted out of backend_template so they aren't buried in raw JSON.
function configItems(m: StorageMapping): KV[] {
  const tpl = (m.backend_template || {}) as Record<string, unknown>;
  const str = (k: string): string | null => {
    const v = tpl[k];
    return typeof v === "string" && v.trim() ? v.trim() : null;
  };
  const nodes = Array.isArray(tpl.rm_worker_nodes)
    ? (tpl.rm_worker_nodes as unknown[]).map(String)
    : [];
  const items: KV[] = [];
  const mr = str("managed_root");
  if (mr) items.push({ label: "경로 (managed_root)", value: mr, mono: true, span: true });
  const mp = str("mount_path");
  if (mp) items.push({ label: "마운트 (mount_path)", value: mp, mono: true, span: true });
  const fsName = str("filesystem_name");
  if (fsName) items.push({ label: "파일시스템", value: fsName, mono: true });
  if (nodes.length)
    items.push({
      label: "RM 노드",
      value: (
        <span className="opt-chips">
          {nodes.map((n) => (
            <span className="chip" key={n}>
              {n}
            </span>
          ))}
        </span>
      ),
    });
  const runner = str("command_runner");
  if (runner) items.push({ label: "실행 방식", value: runner });
  const tmpl = str("fileset_name_template");
  if (tmpl) items.push({ label: "fileset 템플릿", value: tmpl, mono: true });
  const drv = str("csi_driver");
  if (drv) items.push({ label: "csi driver", value: drv, mono: true });
  return items;
}
