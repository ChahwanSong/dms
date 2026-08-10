import { useQueueMetrics } from "./useMetrics";
import { Card } from "../../components/ui/Card";
import { StatusPill } from "../../components/ui/StatusPill";
import { Table } from "../../components/ui/Table";
import type { QueuePodgroup } from "../../lib/types";

// 초 -> "n초"/"n분 n초"/"n시간 n분". RequestDetail 의 durationText 는 시각 2개를
// 받는 다른 시그니처라 재사용 불가(서버가 이미 초를 계산해 준다).
export function waitText(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds) || seconds < 0) return "—";
  if (seconds < 60) return `${seconds}초`;
  const m = Math.floor(seconds / 60);
  if (m < 60) return seconds % 60 === 0 ? `${m}분` : `${m}분 ${seconds % 60}초`;
  const h = Math.floor(m / 60);
  return m % 60 === 0 ? `${h}시간` : `${h}시간 ${m % 60}분`;
}

const QUEUE_STATE_VARIANT: Record<string, "ok" | "bad"> = {
  Open: "ok", Closed: "bad",
};

export function QueueSection() {
  const q = useQueueMetrics();
  // null = 알 수 없음(403/CRD 부재), [] = 비었음 -- asArray/?? [] 로 접지 않는다.
  // 접으면 권한 누락이 "큐가 한가함"으로 렌더된다(설계 §4). 배열도 null 도 아닌
  // 페이로드(파싱 사고)는 "비었음"이 아니라 "알 수 없음" 쪽으로 접는다 -- 모르는
  // 것을 0 이라고 말하지 않는다는 같은 규칙이다.
  const raw = q.data?.podgroups;
  const pods: QueuePodgroup[] | null = Array.isArray(raw) ? raw : null;
  const counts = { Pending: 0, Inqueue: 0, Running: 0 };
  for (const pg of pods ?? []) {
    if (pg.phase === "Pending" || pg.phase === "Inqueue" || pg.phase === "Running") {
      counts[pg.phase] += 1;
    }
  }
  // Running 은 스케줄이 끝난 잡(나이=수명), Completed 는 삭제 직전 찰나 -- 어느
  // 쪽도 "대기"가 아니므로 표에서 뺀다(카운트에는 Running 이 남는다).
  const waiting = (pods ?? []).filter(
    (pg) => pg.phase !== "Running" && pg.phase !== "Completed");
  const state = q.data?.queue?.state ?? null;
  return (
    <Card>
      <div className="flex items-center gap-2 mb-2">
        <h2 className="font-medium">큐 현황</h2>
        <span className="text-muted text-xs">{q.data?.queue?.name ?? "dms-data"}</span>
        {/* 알 수 없으면(큐 null·state null) 배지를 내지 않는다 -- Open 을
            추측하지 않는다(설계 §3). 용량 게이지도 없다: dms-data 큐엔
            capability/deserved 가 없어 표시할 진실이 없다(설계 §2.2). */}
        {state && (
          <StatusPill state={state}
                      variant={QUEUE_STATE_VARIANT[state] ?? "neutral"} />
        )}
      </div>
      {/* 첫 응답 전에도 data 는 undefined 라 아래 pods===null 과 모양이 같다.
          구분하지 않으면 매 첫 페인트가 "권한(RBAC)을 확인하세요"로 깜빡이며
          없는 원인을 가리킨다 -- 잡 통계 카드와 같은 로딩 문구를 먼저 낸다. */}
      {q.isLoading ? (
        <p className="text-muted text-sm">불러오는 중…</p>
      ) : pods === null ? (
        <p className="text-muted text-sm">
          대기 정보를 알 수 없습니다 — 권한(RBAC) 또는 Volcano 설치를 확인하세요.
        </p>
      ) : (
        <>
          <div className="flex gap-4 text-sm">
            <span>Pending {counts.Pending}</span>
            <span>Inqueue {counts.Inqueue}</span>
            <span>Running {counts.Running}</span>
          </div>
          {/* KPI 타일의 「대기」는 24h 창 DB 집계, 이 표는 무윈도 라이브 PodGroup --
              두 숫자는 어긋날 수 있고 그래야 정상이다. 라벨이 그 사실을 드러낸다
              (설계 §3 주의). */}
          <h3 className="font-medium mt-3 mb-1 text-sm">지금 큐에서 대기 중</h3>
          {waiting.length === 0 ? (
            <p className="text-muted text-sm">대기 중인 잡이 없습니다.</p>
          ) : (
            <Table>
              <thead>
                <tr className="text-muted">
                  <th className="py-1">잡</th><th>phase</th>
                  <th>gang</th><th>대기 시간</th>
                </tr>
              </thead>
              <tbody>
                {waiting.map((pg) => (
                  <tr key={pg.name} className="border-t border-black/5">
                    <td className="py-1 break-all">{pg.name}</td>
                    <td>{pg.phase ?? "—"}</td>
                    <td>{pg.min_member ?? "—"}</td>
                    <td className="tabular-nums">{waitText(pg.wait_seconds)}</td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
        </>
      )}
    </Card>
  );
}
