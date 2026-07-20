import { useEffect, useState } from "react";
import { userVocApi, type Voc } from "../../../api";
import { errMsg, fmtAgo, fmtTime } from "../sync/helpers";

// dms-voc 사용자 메뉴: 문의/요청을 등록하고 처리 상태·운영자 답변을 확인한다.
// 미처리(open) 건은 본인이 회수(삭제) 가능, 처리완료 건은 기록으로 보존된다.
const STATUS_META: Record<string, { label: string; tone: string }> = {
  open: { label: "처리 대기", tone: "is-info" },
  resolved: { label: "처리완료", tone: "is-ok" },
};

export default function UserVocTab() {
  const [items, setItems] = useState<Voc[]>([]);
  const [categories, setCategories] = useState<string[]>([]);
  const [category, setCategory] = useState("");
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);

  function reload() {
    userVocApi
      .list()
      .then((r) => setItems(r.items))
      .catch((e) => setError(errMsg(e)));
  }

  useEffect(() => {
    reload();
    userVocApi.categories().then((r) => setCategories(r.categories)).catch(() => {});
  }, []);

  const toggle = (id: number) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  async function submit() {
    setError(null);
    setOk(null);
    if (!title.trim()) return setError("제목을 입력하세요.");
    if (!body.trim()) return setError("내용을 입력하세요.");
    setBusy(true);
    try {
      await userVocApi.create({
        category: category || null,
        title: title.trim(),
        body: body.trim(),
      });
      setOk("VOC가 등록되었습니다. 운영자가 확인 후 처리합니다.");
      setTitle("");
      setBody("");
      setCategory("");
      reload();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: number) {
    if (!window.confirm("이 VOC를 회수(삭제)할까요?")) return;
    setBusyId(id);
    setError(null);
    try {
      await userVocApi.remove(id);
      setItems((prev) => prev.filter((v) => v.id !== id));
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="inventory">
      <div className="inv-head">
        <h2>VOC</h2>
      </div>

      <section className="ui-card">
        <div className="ui-card-hd">
          <h3>
            VOC 등록{" "}
            <span className="muted small">문의·요청·버그 신고 — 운영자가 확인 후 처리합니다</span>
          </h3>
        </div>
        <div className="ui-card-bd">
          <div className="ui-card-div" />
          <div className="form">
            <div className="storage-row">
              <label>
                <span>분류 (선택)</span>
                <select value={category} onChange={(e) => setCategory(e.target.value)}>
                  <option value="">선택 안 함</option>
                  {categories.map((c) => (
                    <option key={c} value={c}>{c}</option>
                  ))}
                </select>
              </label>
              <label>
                <span>제목 *</span>
                <input
                  value={title}
                  maxLength={200}
                  onChange={(e) => setTitle(e.target.value)}
                  placeholder="예: 프로젝트 알파 경로 스캔 요청"
                />
              </label>
            </div>
            <label>
              <span>내용 *</span>
              <textarea
                className="voc-body-input"
                value={body}
                maxLength={4000}
                rows={5}
                onChange={(e) => setBody(e.target.value)}
                placeholder="요청/문의 내용을 자세히 적어주세요. 경로·스토리지 등 구체적일수록 처리가 빠릅니다."
              />
            </label>
            {error && <div className="banner err">{error}</div>}
            {ok && <div className="banner ok">{ok}</div>}
            <div className="modal-actions">
              <button className="primary" onClick={submit} disabled={busy}>
                {busy ? "등록 중…" : "VOC 등록"}
              </button>
            </div>
          </div>
        </div>
      </section>

      <section className="ui-card">
        <div className="ui-card-hd">
          <h3>
            내 VOC
            <span className="hd-cnt">{items.length}</span>
          </h3>
          <button className="ghost mini" onClick={reload}>새로고침</button>
        </div>
        <div className="ui-card-bd">
          <div className="ui-card-div" />
          {items.length === 0 ? (
            <p className="muted">등록한 VOC가 없습니다.</p>
          ) : (
            <ul className="voc-list">
              {items.map((v) => {
                const open = expanded.has(v.id);
                const sm = STATUS_META[v.status] ?? STATUS_META.open;
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
                      <span className={`reqa-badge ${sm.tone}`}>{sm.label}</span>
                      <span className="muted small voc-when" title={fmtTime(v.created_at)}>
                        {fmtAgo(v.created_at)}
                      </span>
                    </div>
                    {open && (
                      <div className="voc-detail">
                        <pre className="voc-body">{v.body}</pre>
                        {v.status === "resolved" ? (
                          <div className="voc-answer">
                            <div className="card-eyebrow">
                              운영자 답변
                              {v.resolved_at && (
                                <span className="muted"> · {fmtTime(v.resolved_at)}</span>
                              )}
                            </div>
                            <pre className="voc-body">{v.answer || "(답변 없이 처리완료)"}</pre>
                          </div>
                        ) : (
                          <div className="voc-actions">
                            <button
                              className="mini danger"
                              disabled={busyId === v.id}
                              onClick={(e) => { e.stopPropagation(); remove(v.id); }}
                            >
                              회수 (삭제)
                            </button>
                          </div>
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
