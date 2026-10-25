import type { ReactNode } from "react";
import { agoText, kstStamp } from "../../lib/datetime";
import { useNow } from "../../lib/useNow";
import { clusterStatusMeta } from "./k8sTerms";
import type { ClusterFreshness } from "./types";

// k8s 연동 화면들이 함께 쓰는 작은 잎 부품. 화면 파일마다 같은 표기를 다시 짓지 않게 한 곳에 둔다.

/** 「n분 전」 — 15초 틱(useNow)으로 혼자 다시 그린다(폴링 사이에도 숫자가 늙는다). 모르면 empty 문구(기본 「—」).
 *  절대 시각(KST)은 title 로. offsetMs = 서버 시계 - 브라우저 시계(개요 응답 now 로 잰 값) — 브라우저 시계가 틀려도 거짓 나이를
 *  만들지 않게. */
export function Ago({ iso, empty = "—", offsetMs = 0, className = "" }: {
  iso: string | null | undefined; empty?: string; offsetMs?: number; className?: string;
}) {
  const now = useNow(15_000, true);
  if (!iso) return <span className={className}>{empty}</span>;
  return <span className={className} title={kstStamp(iso)}>{agoText(iso, now + offsetMs)}</span>;
}

/** 작은 꼬리표(종류·출처·권한 수준). 점(dot)을 앞에 달지 않는다(디자인 규약) — 글자와 면 색만. */
export function Tag({ children, tone = "neutral", title }: {
  children: ReactNode; tone?: "neutral" | "info" | "warn" | "bad" | "ok"; title?: string;
}) {
  const cls = {
    neutral: "bg-panel text-ink/80", info: "bg-infobg text-accent", warn: "bg-attnbg text-attn",
    bad: "bg-badbg text-bad", ok: "bg-okbg text-ok",
  }[tone];
  return (
    <span title={title}
          className={`inline-flex items-center whitespace-nowrap rounded-md px-1.5 py-0.5 text-[11px] font-medium ${cls}`}>
      {children}
    </span>
  );
}

/** 긴 식별자(pvc-uuid·API 주소·경로) — 등폭 + 아무 데서나 줄바꿈(좁은 칸을 뚫지 않게). */
export function Mono({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <span className={`font-mono text-[12px] [overflow-wrap:anywhere] ${className}`}>{children}</span>;
}

/** 스냅숏 기준 시각 줄 — PV 탐색·사용자 권한은 「마지막 성공 동기화 기준」 데이터라, 그 기준을 클러스터마다 보인다.
 *  synced_at null = 한 번도 성공하지 않음(모름 — 「없음」과 다르다), stale = 2×주기보다 오래됨. */
export function FreshnessLine({ clusters }: { clusters: ClusterFreshness[] }) {
  if (clusters.length === 0) {
    return <p className="text-sm text-ink/70">등록된 클러스터가 없습니다 — 「개요」에서 클러스터를 등록하세요.</p>;
  }
  return (
    <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink/70">
      <span className="font-medium text-ink/80">스냅숏 기준</span>
      {clusters.map((c) => {
        const tone = c.synced_at === null ? "text-bad" : c.stale ? "text-attn" : "text-ink/80";
        return (
          <span key={c.name} className={`whitespace-nowrap ${tone}`}>
            <span className="font-medium">{c.name}</span>{" "}
            {c.synced_at === null
              ? "아직 동기화 안 됨(모름)"
              : <><Ago iso={c.synced_at} /> 성공{c.stale ? " · 오래됨" : ""}</>}
            {c.status !== "ok" && c.synced_at !== null && ` · ${clusterStatusMeta(c.status).label}`}
          </span>
        );
      })}
    </p>
  );
}

/** 정의 목록 한 줄(상세 창) — 라벨 열은 max-content, 값 열은 minmax(0,1fr) 라 긴 토큰이 칸을 밀지 않는다. */
export function DefList({ children }: { children: ReactNode }) {
  return <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-sm">{children}</dl>;
}
export function DefRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="contents">
      <dt className="text-ink/70">{label}</dt>
      <dd className="min-w-0 [overflow-wrap:anywhere]">{children}</dd>
    </div>
  );
}

/** 표 위 요약 숫자 한 칸(조밀한 타일). null 은 「—」(모름). */
export function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="min-w-0 rounded-card border border-line bg-surface px-4 py-3">
      <div className="text-xs text-ink/70">{label}</div>
      <div className="mt-0.5 text-xl font-semibold tabular-nums">{value}</div>
      {sub && <div className="mt-0.5 text-xs text-ink/70">{sub}</div>}
    </div>
  );
}
