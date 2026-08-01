import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { opVocApi, type Voc, type VocPeriod } from "../../../api";
import { errMsg } from "../backup/BackupBatches";
import { fmtAgo, fmtTime } from "../../../lib/format";

// dms-voc 운영자 메뉴: 미처리/처리완료 서브탭 + 기간(1달/1년/전체) 필터 + 요청 시간
// 정렬(서버사이드) + 검색 + 무한스크롤 테이블. 페이징은 keyset 커서(마지막 id)라
// 스크롤 도중 다른 운영자의 처리/사용자 회수로 행이 빠져도 누락되지 않는다.
// 행 클릭 펼침에서 본문 확인·답변 작성·처리(:resolve)/복귀(:reopen)/삭제. 처리·복귀·
// 삭제는 성공 시 로컬에서 낙관적으로 제거해 스크롤 위치를 보존한다.
type Tab = "open" | "resolved";

const PAGE = 50;
const PERIODS: { key: VocPeriod; label: string }[] = [
  { key: "1m", label: "최근 1달" },
  { key: "1y", label: "최근 1년" },
  { key: "all", label: "전체" },
];

export default function VocTab() {
  const [tab, setTab] = useState<Tab>("open");
  const [period, setPeriod] = useState<VocPeriod>("all");
  const [order, setOrder] = useState<"asc" | "desc">("desc");
  const [search, setSearch] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const [rows, setRows] = useState<Voc[]>([]);
  const [counts, setCounts] = useState<{ open: number; resolved: number }>({ open: 0, resolved: 0 });
  const [loading, setLoading] = useState(true);
  const [reachedEnd, setReachedEnd] = useState(false);
  const [moreError, setMoreError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [answers, setAnswers] = useState<Record<number, string>>({});
  const [error, setError] = useState<string | null>(null);
  const cursorRef = useRef<number | null>(null);
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  const loadingRef = useRef(false);
  const seqRef = useRef(0);

  // 검색 디바운스(300ms) — 타이핑마다 서버를 두드리지 않는다.
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(search.trim()), 300);
    return () => clearTimeout(t);
  }, [search]);

  // 필터/정렬이 바뀌면 처음부터 다시 로드. seq 가드로 늦게 도착한 응답을 버린다.
  const reload = useCallback(() => {
    seqRef.current += 1;
    const seq = seqRef.current;
    loadingRef.current = true;
    cursorRef.current = null;
    setLoading(true);
    setError(null);
    setMoreError(null);
    setReachedEnd(false);
    setExpanded(new Set());
    setAnswers({}); // 초안은 휘발성 — 낡은 초안이 서버 answer를 가리지 않게 리셋
    opVocApi
      .list({ status: tab, period, order, search: debouncedQ, limit: PAGE })
      .then((r) => {
        if (seq !== seqRef.current) return;
        setRows(r.items);
        setCounts(r.counts);
        cursorRef.current = r.next_cursor;
        setReachedEnd(r.next_cursor == null);
      })
      .catch((e) => {
        if (seq !== seqRef.current) return;
        setRows([]);
        setError(errMsg(e));
        setReachedEnd(true);
      })
      .finally(() => {
        if (seq === seqRef.current) {
          loadingRef.current = false;
          setLoading(false);
        }
      });
  }, [tab, period, order, debouncedQ]);

  useEffect(() => {
    reload();
  }, [reload]);

  const loadMore = useCallback(() => {
    if (loadingRef.current || reachedEnd || cursorRef.current == null) return;
    const seq = seqRef.current;
    loadingRef.current = true;
    setLoading(true);
    setMoreError(null);
    opVocApi
      .list({ status: tab, period, order, search: debouncedQ, cursor: cursorRef.current, limit: PAGE })
      .then((r) => {
        if (seq !== seqRef.current) return;
        setRows((prev) => {
          const seen = new Set(prev.map((x) => x.id));
          return [...prev, ...r.items.filter((x) => !seen.has(x.id))];
        });
        setCounts(r.counts);
        cursorRef.current = r.next_cursor;
        setReachedEnd(r.next_cursor == null);
      })
      .catch((e) => {
        // 실패를 '끝'으로 위장하지 않는다: 오류를 보여주고 수동 재시도를 남긴다.
        if (seq === seqRef.current) setMoreError(errMsg(e));
      })
      .finally(() => {
        if (seq === seqRef.current) {
          loadingRef.current = false;
          setLoading(false);
        }
      });
  }, [tab, period, order, debouncedQ, reachedEnd]);

  // 무한스크롤: sentinel이 보이면 다음 페이지 (오류 시엔 수동 재시도만).
  useEffect(() => {
    const el = sentinelRef.current;
    if (!el || reachedEnd || moreError) return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting) loadMore();
      },
      { rootMargin: "400px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [loadMore, reachedEnd, moreError]);

  const toggle = (id: number) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  // 성공한 상태 전이는 현재 탭에서 행이 빠진다 — 전체 재조회 대신 로컬 제거로
  // 스크롤 위치를 보존하고, counts는 전이 방향에 맞게 보정한다.
  function removeLocal(id: number, delta: Partial<Record<Tab, number>>) {
    setRows((prev) => prev.filter((r) => r.id !== id));
    setCounts((c) => ({
      open: Math.max(0, c.open + (delta.open ?? 0)),
      resolved: Math.max(0, c.resolved + (delta.resolved ?? 0)),
    }));
    setExpanded((prev) => {
      const next = new Set(prev);
      next.delete(id);
      return next;
    });
    setAnswers((prev) => {
      if (!(id in prev)) return prev;
      const next = { ...prev };
      delete next[id];
      return next;
    });
  }

  async function act(id: number, fn: () => Promise<unknown>, delta: Partial<Record<Tab, number>>) {
    setBusyId(id);
    setError(null);
    try {
      await fn();
      removeLocal(id, delta);
    } catch (e) {
      setError(errMsg(e));
      reload(); // 서버와 어긋났을 수 있으니 실패 시에만 전체 재조회
    } finally {
      setBusyId(null);
    }
  }

  const resolve = (v: Voc) =>
    act(v.id, () => opVocApi.resolve(v.id, (answers[v.id] ?? v.answer ?? "").trim() || null),
      { open: -1, resolved: +1 });
  const reopen = (v: Voc) => act(v.id, () => opVocApi.reopen(v.id), { resolved: -1, open: +1 });
  const remove = (v: Voc) => {
    if (!window.confirm("이 VOC를 삭제할까요? (기록이 사라집니다)")) return;
    act(v.id, () => opVocApi.remove(v.id), { [v.status]: -1 });
  };

  const shown = rows.length;
  const total = counts[tab];
  const filtered = period !== "all" || !!debouncedQ;
  const COLS = 5;

  return (
    <div className="inventory">
      <div className="inv-head">
        <h2>VOC</h2>
        <div className="inv-actions">
          <div className="ts-pills">
            {PERIODS.map((p) => (
              <button
                key={p.key}
                className={period === p.key ? "on" : ""}
                onClick={() => setPeriod(p.key)}
              >
                {p.label}
              </button>
            ))}
          </div>
          <button className="ghost" onClick={reload}>새로고침</button>
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
            {filtered && (
              <span className="chip tone-low" title="건수는 기간/검색 필터가 적용된 값입니다">
                필터 적용됨
              </span>
            )}
          </div>
          <div className="scan-toolbar">
            <input
              className="scan-search"
              value={search}
              maxLength={200}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="제목 · 내용 · 사용자 검색…"
            />
          </div>
        </div>
        <div className="ui-card-bd">
          <div className="ui-card-div" />
          {error && <div className="banner err">{error}</div>}

          <table className="grid voc-grid">
            <thead>
              <tr>
                <th className="voc-col-cat">타입</th>
                <th>제목</th>
                <th className="voc-col-user">사용자</th>
                <th
                  className="voc-col-when"
                  aria-sort={order === "asc" ? "ascending" : "descending"}
                >
                  <button
                    type="button"
                    className="sort-th active"
                    onClick={() => setOrder((o) => (o === "desc" ? "asc" : "desc"))}
                    title="요청 시간 정렬 전환"
                  >
                    요청 시간
                    <span className="sort-ind">{order === "asc" ? "▲" : "▼"}</span>
                  </button>
                </th>
                <th className="voc-col-act">조치</th>
              </tr>
            </thead>
            <tbody>
              {shown === 0 && !loading ? (
                <tr>
                  <td colSpan={COLS} className="muted reqa-empty">
                    {error
                      ? "불러오지 못했습니다."
                      : moreError
                        ? "목록을 이어서 불러오지 못했습니다 — 아래 '다시 시도'를 누르세요."
                        : debouncedQ
                        ? "검색 결과가 없습니다."
                        : tab === "open"
                          ? "미처리 VOC가 없습니다."
                          : "처리완료된 VOC가 없습니다."}
                  </td>
                </tr>
              ) : (
                rows.map((v) => {
                  const open = expanded.has(v.id);
                  const busy = busyId === v.id;
                  // 탭 전환 직후 1 RTT 동안 이전 탭 행이 남아 있을 수 있으므로,
                  // 처리/복귀 UI는 tab이 아니라 행 자신의 status 기준으로 분기한다.
                  const isOpen = v.status === "open";
                  return (
                    <Fragment key={v.id}>
                      <tr className="voc-row" onClick={() => toggle(v.id)}>
                        <td data-label="타입" className="voc-col-cat">
                          {v.category ? <span className="chip">{v.category}</span> : <span className="muted">—</span>}
                        </td>
                        <td data-label="제목" className="voc-title-cell">
                          <span className="voc-caret" aria-hidden>{open ? "▾" : "▸"}</span>
                          <span className="voc-title">{v.title}</span>
                        </td>
                        <td data-label="사용자" className="mono small voc-col-user">{v.username}</td>
                        <td
                          data-label="요청 시간"
                          className="muted small voc-col-when"
                          title={fmtTime(v.created_at)}
                        >
                          {fmtAgo(v.created_at)}
                        </td>
                        <td
                          data-label="조치"
                          className="voc-col-act"
                          onClick={(e) => e.stopPropagation()}
                        >
                          {!isOpen && (
                            <button className="mini" disabled={busy} onClick={() => reopen(v)}>
                              복귀
                            </button>
                          )}
                          <button className="mini danger" disabled={busy} onClick={() => remove(v)}>
                            삭제
                          </button>
                        </td>
                      </tr>
                      {open && (
                        <tr className="voc-detail-row">
                          <td colSpan={COLS}>
                            <div className="voc-detail">
                              <pre className="voc-body">{v.body}</pre>
                              {isOpen ? (
                                <>
                                  <label className="voc-answer-label">
                                    <span className="card-eyebrow">
                                      답변 / 처리 내용 (선택)
                                      {v.answer && !(v.id in answers) && (
                                        <span className="muted"> · 이전 답변 초안 불러옴</span>
                                      )}
                                    </span>
                                    <textarea
                                      rows={3}
                                      maxLength={4000}
                                      value={answers[v.id] ?? v.answer ?? ""}
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
                                  </div>
                                </>
                              ) : (
                                <div className="voc-answer">
                                  <div className="card-eyebrow">
                                    답변 · {v.resolved_by || "—"}
                                    {v.resolved_at && (
                                      <span className="muted"> · {fmtTime(v.resolved_at)}</span>
                                    )}
                                  </div>
                                  <pre className="voc-body">{v.answer || "(답변 없이 처리완료)"}</pre>
                                </div>
                              )}
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })
              )}
            </tbody>
          </table>

          {/* 무한스크롤 footer — 오류는 숨기지 않고 재시도 버튼을 남긴다. 낙관적
              제거로 목록이 비어도(shown=0) 재시도/더보기 경로가 사라지지 않게 한다 */}
          {(shown > 0 || moreError || !reachedEnd) && (
            <div className="reqa-foot">
              {moreError ? (
                <span className="small err-num">
                  더 불러오기 실패: {moreError}{" "}
                  <button className="reqa-more-btn" onClick={loadMore}>다시 시도</button>
                </span>
              ) : !reachedEnd ? (
                <>
                  <div ref={sentinelRef} className="reqa-sentinel" aria-hidden />
                  {loading ? (
                    <span className="muted small">
                      <span className="reqa-spin" aria-hidden /> 더 불러오는 중…
                    </span>
                  ) : (
                    <button className="reqa-more-btn" onClick={loadMore}>
                      더 불러오기 (+{PAGE})
                    </button>
                  )}
                </>
              ) : (
                <span className="muted small">
                  {shown}건 표시{total > shown ? ` / 전체 ${total}건` : " · 끝"}
                </span>
              )}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
