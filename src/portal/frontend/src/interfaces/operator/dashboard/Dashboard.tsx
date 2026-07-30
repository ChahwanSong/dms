import { useCallback, useEffect, useState } from "react";
import { operatorApi, type DashboardSummary, type AttentionItem } from "../../../api";
import { fmtTime } from "./helpers";
import StatusCards, { type AttentionCounts } from "./StatusCards";
import KpiRow from "./KpiRow";
import TimeSeriesCard from "./TimeSeriesCard";
import NodesTable from "./NodesTable";
import StorageNodeMatrix from "./StorageNodeMatrix";
import VolcanoPanel from "./VolcanoPanel";

const POLL_MS = 7000;

// Fold the 조치 필요 list (same /attention the panel uses: refined + non-dismissed)
// into the counts the KPI + detail card show, so card == panel. Default-visible =
// non-INFO (preflight/cancel are INFO); live = 현재 조치 필요, else 과거 이력.
function attentionCounts(items: AttentionItem[]): AttentionCounts {
  const sev = (i: AttentionItem) => String(i.severity || "WARN").toUpperCase();
  const vis = items.filter((i) => ["CRITICAL", "ERROR", "WARN"].includes(sev(i)));
  const live = vis.filter((i) => i.category !== "history");
  return {
    live: live.length,
    liveErr: live.filter((i) => ["CRITICAL", "ERROR"].includes(sev(i))).length,
    history: vis.filter((i) => i.category === "history").length,
    info: items.length - vis.length,
  };
}

export default function Dashboard({ onNavigate }: { onNavigate?: (section: string) => void }) {
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [attention, setAttention] = useState<AttentionCounts | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [auto, setAuto] = useState(true);
  const [updatedAt, setUpdatedAt] = useState<string>("");
  const reload = useCallback(async () => {
    try {
      const [s, items] = await Promise.all([
        operatorApi.dashboard.summary(),
        operatorApi.dashboard.attention().catch(() => [] as AttentionItem[]),
      ]);
      setSummary(s);
      setAttention(attentionCounts(items));
      setError(null);
      setUpdatedAt(new Date().toISOString());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => { reload(); }, [reload]);
  useEffect(() => {
    if (!auto) return;
    const id = setInterval(reload, POLL_MS);
    return () => clearInterval(id);
  }, [auto, reload]);

  return (
    <div className="inventory dashboard">
      <div className="inv-head">
        <h2>종합 대시보드</h2>
        <div className="inv-actions">
          <span className="muted small">갱신 {fmtTime(updatedAt)}</span>
          <button className="ghost" onClick={() => setAuto((v) => !v)}>
            {auto ? "자동새로고침 ⏸" : "자동새로고침 ▶"}
          </button>
          <button className="ghost" onClick={reload}>새로고침</button>
        </div>
      </div>
      {error && <div className="banner err">{error}</div>}
      <div className="dash-stack">
        <KpiRow summary={summary} attention={attention} onNavigate={onNavigate} />
        <TimeSeriesCard />
        <div className="bento">
          {/* 각 섹션이 자기 row(전폭)를 쓴다: 스토리지 마운트 매트릭스와 제어·작업·요청
              카드는 각각 넓은 폭이 필요해 한 줄씩 차지한다. 제어·작업·요청 카드는 내부
              .dash-detail(auto-fit)가 전폭에서 3개 하위카드를 가로로 균등 배치한다. */}
          <div className="span-12"><StorageNodeMatrix /></div>
          <div className="span-12"><StatusCards summary={summary} attention={attention} onNavigate={onNavigate} /></div>
          <div className="span-12"><NodesTable onNavigate={onNavigate} /></div>
          <div className="span-12"><VolcanoPanel /></div>
        </div>
      </div>
    </div>
  );
}
