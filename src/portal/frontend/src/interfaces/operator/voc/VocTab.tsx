import { useCallback, useEffect, useState } from "react";
import { opVocApi, type Voc } from "../../../api";
import { errMsg } from "../backup/BackupBatches";
import { fmtAgo, fmtTime } from "../../../lib/format";

// dms-voc 운영자 메뉴: 사용자 VOC를 미처리/처리완료 서브탭으로 보고 처리한다.
// 미처리 → 행 펼침에서 답변 작성 후 '처리 완료', 처리완료 → 답변 열람·복귀·삭제.
type Tab = "open" | "resolved";

export default function VocTab() {
  const [tab, setTab] = useState<Tab>("open");
  const [items, setItems] = useState<Voc[]>([]);
  const [counts, setCounts] = useState<{ open: number; resolved: number }>({ open: 0, resolved: 0 });
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [answers, setAnswers] = useState<Record<number, string>>({});
  const [error, setError] = useState<string | null>(null);

  const load = useCallback((t: Tab) => {
    setLoading(true);
    setError(null);
    opVocApi
      .list(t)
      .then((r) => {
        setItems(r.items);
        setCounts(r.counts);
      })
      .catch((e) => setError(errMsg(e)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load(tab);
    setExpanded(new Set());
  }, [tab, load]);

  const toggle = (id: number) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  async function act(id: number, fn: () => Promise<unknown>) {
    setBusyId(id);
    setError(null);
    try {
      await fn();
      load(tab);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusyId(null);
    }
  }

  const resolve = (v: Voc) =>
    act(v.id, () => opVocApi.resolve(v.id, (answers[v.id] || "").trim() || null));
  const reopen = (v: Voc) => act(v.id, () => opVocApi.reopen(v.id));
  const remove = (v: Voc) => {
    if (!window.confirm("이 VOC를 삭제할까요? (기록이 사라집니다)")) return;
    act(v.id, () => opVocApi.remove(v.id));
  };

  return (
    <div className="inventory">
      <div className="inv-head">
        <h2>VOC</h2>
        <div className="inv-actions">
          <button className="ghost" onClick={() => load(tab)}>새로고침</button>
        </div>
      </div>

      <section className="ui-card">
        <div className="ui-card-hd">
          <div className="voc-tabs" role="tablist" aria-label="VOC 상태">
            {([
              ["open", "미처리", counts.open],
              ["resolved", "처리완료", counts.resolved],
            ] as [Tab, string, number][]).map(([k, label, n]) => (
              <button
                key={k}
                role="tab"
                aria-selected={tab === k}
                className={`mini ${tab === k ? "primary" : "ghost"}`}
                onClick={() => setTab(k)}
              >
                {label}
                <span className={`voc-tab-cnt${k === "open" && n > 0 ? " err-num" : ""}`}>{n}</span>
              </button>
            ))}
          </div>
        </div>
        <div className="ui-card-bd">
          <div className="ui-card-div" />
          {error && <div className="banner err">{error}</div>}
          {items.length === 0 && !loading ? (
            <p className="muted">
              {tab === "open" ? "미처리 VOC가 없습니다." : "처리완료된 VOC가 없습니다."}
            </p>
          ) : (
            <ul className="voc-list">
              {items.map((v) => {
                const open = expanded.has(v.id);
                const busy = busyId === v.id;
                return (
                  <li key={v.id} className="voc-item">
                    <div
                      className="voc-hd"
                      role="button"
                      tabIndex={0}
                      onClick={() => toggle(v.id)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          toggle(v.id);
                        }
                      }}
                    >
                      <span className="voc-caret" aria-hidden>{open ? "▾" : "▸"}</span>
                      {v.category && <span className="chip">{v.category}</span>}
                      <span className="voc-title">{v.title}</span>
                      <span className="muted small mono">{v.username}</span>
                      <span className="muted small voc-when" title={fmtTime(v.created_at)}>
                        {fmtAgo(v.created_at)}
                      </span>
                    </div>
                    {open && (
                      <div className="voc-detail">
                        <pre className="voc-body">{v.body}</pre>
                        {tab === "open" ? (
                          <>
                            <label className="voc-answer-label">
                              <span className="card-eyebrow">답변 / 처리 내용 (선택)</span>
                              <textarea
                                rows={3}
                                maxLength={4000}
                                value={answers[v.id] || ""}
                                placeholder="처리 결과나 안내를 남기면 사용자에게 표시됩니다."
                                onChange={(e) =>
                                  setAnswers((prev) => ({ ...prev, [v.id]: e.target.value }))
                                }
                              />
                            </label>
                            <div className="voc-actions">
                              <button className="mini go" disabled={busy} onClick={() => resolve(v)}>
                                처리 완료
                              </button>
                              <button className="mini danger" disabled={busy} onClick={() => remove(v)}>
                                삭제
                              </button>
                            </div>
                          </>
                        ) : (
                          <>
                            <div className="voc-answer">
                              <div className="card-eyebrow">
                                답변 · {v.resolved_by || "—"}
                                {v.resolved_at && (
                                  <span className="muted"> · {fmtTime(v.resolved_at)}</span>
                                )}
                              </div>
                              <pre className="voc-body">{v.answer || "(답변 없이 처리완료)"}</pre>
                            </div>
                            <div className="voc-actions">
                              <button className="mini" disabled={busy} onClick={() => reopen(v)}>
                                미처리로 복귀
                              </button>
                              <button className="mini danger" disabled={busy} onClick={() => remove(v)}>
                                삭제
                              </button>
                            </div>
                          </>
                        )}
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </section>
    </div>
  );
}
