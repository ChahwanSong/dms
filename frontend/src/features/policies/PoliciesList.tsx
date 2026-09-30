import { usePolicies } from "./usePolicies";
import { PolicyDialog } from "./PolicyDialog";
import { SyncPairsPanel } from "./SyncPairsPanel";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { ApiError } from "../../lib/api";
import { kstStampOrDash } from "../../lib/datetime";
import type { Policy } from "../../lib/types";

export function humanSeconds(s: number | null): string {
  if (s === null) return "—";
  if (s % 86400 === 0) return `${s}s (${s / 86400}d)`;
  if (s % 3600 === 0) return `${s}s (${s / 3600}h)`;
  if (s % 60 === 0) return `${s}s (${s / 60}m)`;
  return `${s}s`;
}

// 도구별 용도(2026-09-30 정책 화면 개선): 정책 키(placement.TOOL_TO_POLICY)마다 무엇을 하는
// 도구인지 한 줄로 -- 표의 "scan / dsync / nsync / rm" 만으로는 운영자가 dsync 와 nsync 의
// 차이(같은 노드 공존 vs 노드 간)를 화면에서 알 수 없었다.
export const TOOL_INFO: Record<string, { title: string; binary: string; help: string }> = {
  scan: { title: "스캔", binary: "dscan",
          help: "디렉토리를 훑어 파일 수·용량·데이터 온도 리포트를 만듭니다." },
  dsync: { title: "동기화", binary: "dsync",
           help: "소스·목적지를 함께 마운트한 노드가 있을 때 쓰는 sync 입니다(기본)." },
  nsync: { title: "노드 간 동기화", binary: "nsync",
           help: "소스·목적지를 함께 마운트한 노드가 없을 때, 서로 다른 노드 사이로 옮기는 sync 입니다." },
  rm: { title: "삭제", binary: "drm",
        help: "대상 디렉토리를 병렬로 지웁니다 — 미리보기 확인 뒤에만 실행됩니다." },
};

function Metric({ label, value, sub }: { label: string; value: React.ReactNode; sub?: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-line px-3 py-2">
      <div className="text-xs text-muted">{label}</div>
      <div className="mt-0.5 font-semibold tabular-nums">{value}</div>
      {sub && <div className="text-xs text-muted mt-0.5">{sub}</div>}
    </div>
  );
}

function PolicyCard({ p }: { p: Policy }) {
  const info = TOOL_INFO[p.tool];
  const on = p.enabled === 1;
  return (
    <article className={`rounded-card border bg-surface p-4 ${on ? "border-line" : "border-bad/40"}`}
             aria-label={`${p.tool} 정책`}>
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="flex items-center gap-2">
            <span className="text-base font-semibold">{info?.title ?? p.tool}</span>
            <code className="rounded bg-panel px-1.5 py-0.5 text-xs">{p.tool}</code>
            <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${
              on ? "text-ok bg-okbg" : "text-bad bg-badbg"}`}>{on ? "활성" : "비활성"}</span>
          </div>
          {info && <p className="mt-1 text-xs text-muted">{info.help}</p>}
          {!on && (
            <p className="mt-1 text-xs text-bad">비활성 — 이 도구의 새 작업은 계획 단계에서 거부됩니다(policy_disabled).</p>
          )}
        </div>
        <PolicyDialog policy={p} trigger={<Button variant="ghost">수정</Button>} />
      </header>
      <div className="mt-3 grid gap-2 sm:grid-cols-3">
        {/* nsync 는 최대 노드가 **면당**(소스·목적지 각각) 상한이다(placement.resolve_fanout) --
            합계는 2배. dsync·scan·rm 은 한 노드 집합이라 1배. */}
        {p.tool === "nsync" ? (
          <Metric label="병렬 실행(소스·목적지 각각)"
                  value={`${p.max_nodes}노드 × ${p.procs_per_node}프로세스`}
                  sub={`양쪽 합계 최대 ${2 * p.max_nodes}노드 · ${2 * p.max_nodes * p.procs_per_node}개 프로세스`} />
        ) : (
          <Metric label="병렬 실행"
                  value={`${p.max_nodes}노드 × ${p.procs_per_node}프로세스`}
                  sub={`최대 ${p.max_nodes * p.procs_per_node}개 프로세스`} />
        )}
        <Metric label="우선순위(기본 / 최대)" value={`${p.default_priority} / ${p.max_priority}`}
                sub="요청이 최대보다 높으면 최대로 낮춰집니다" />
        <Metric label="큐" value={p.queue} sub="Volcano 큐" />
      </div>
      <div className="mt-2 grid gap-2 sm:grid-cols-2">
        <Metric label="미리보기 타임아웃"
                value={p.preview_timeout_seconds === null ? "없음" : humanSeconds(p.preview_timeout_seconds)} />
        <Metric label="실행 타임아웃" value={humanSeconds(p.execution_timeout_seconds)} />
      </div>
      {(p.updated_at || p.updated_by) && (
        <p className="mt-3 text-xs text-muted">
          마지막 수정 {kstStampOrDash(p.updated_at)}{p.updated_by ? ` · ${p.updated_by}` : ""}
        </p>
      )}
    </article>
  );
}

export function PoliciesList() {
  const q = usePolicies();
  return (
    <section className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold">정책</h1>
        <p className="mt-1 text-sm text-muted">
          도구별 실행 한도입니다 — 작업이 쓸 노드·프로세스 수, 스케줄링 큐·우선순위, 타임아웃을 정합니다.
          노드·프로세스·큐·최대 우선순위는 이후 계획되는 작업부터, 기본 우선순위는 이후 제출되는 작업부터,
          타임아웃은 진행 중 작업의 다음 단계부터 적용됩니다.
        </p>
      </div>
      {q.isLoading ? <p className="text-muted">불러오는 중…</p> : q.isError ? (
        <Card><p className="text-bad">{(q.error as ApiError).message}</p></Card>
      ) : (
        <div className="grid gap-4 xl:grid-cols-2">
          {(q.data ?? []).map((p) => <PolicyCard key={p.tool} p={p} />)}
        </div>
      )}
      <SyncPairsPanel />
    </section>
  );
}
