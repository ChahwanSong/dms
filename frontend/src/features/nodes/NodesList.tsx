import { useState } from "react";
import { AlertTriangle } from "lucide-react";
import { useNodes, useNodeReports } from "./useNodes";
import { PlacementBadges, PlacementCell, k8sNodeState } from "./NodePlacement";
import { Card } from "../../components/ui/Card";
import { Table } from "../../components/ui/Table";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { ApiError } from "../../lib/api";
import { kstStamp } from "../../lib/datetime";
import type { NodeInfo, NodeMount, NodeTool, NodeDisk, NodeReport } from "../../lib/types";

// 에이전트 리포트는 스키마 검증 없이 저장된다 — 배열이어야 할 필드가 배열이
// 아닌 값(예: {})으로 와도 여기서 걸러야 목록 화면 전체가 죽지 않는다.
const asArray = <T,>(v: unknown): T[] => (Array.isArray(v) ? v : []);
const asObject = (v: unknown): Record<string, unknown> =>
  (v !== null && typeof v === "object" && !Array.isArray(v) ? v as Record<string, unknown> : {});
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

// 노드 도구는 **존재 확인만** 표시한다(2026-08-30 사용자 결정). 도구는 노드가
// 아니라 잡 파드(dms-mpifileutils 이미지)에서 실행되므로, 노드에선 바이너리가
// 설치돼 있는지만 의미가 있다 -- 버전·실행 프로브는 제거했다. status 와이어 값
// (Ready/Missing, placement 게이트 계약)을 화면 문구로만 옮긴다.
export function toolStatusText(status: string): string {
  if (status === "Ready") return "설치됨";
  if (status === "Missing") return "없음";
  return status;
}

// TiB/GiB/MiB만 다룬다 — 디스크 총량·사용량은 늘 MiB를 넘는다. 소수점 1자리.
const BYTE_UNITS: [string, number][] = [
  ["TiB", 1024 ** 4],
  ["GiB", 1024 ** 3],
  ["MiB", 1024 ** 2],
];
function humanBytes(bytes: unknown): string {
  if (!Number.isFinite(bytes)) return "—";
  const n = bytes as number;
  for (const [unit, size] of BYTE_UNITS) {
    if (n >= size) return `${(n / size).toFixed(1)} ${unit}`;
  }
  return `${n} B`;
}

function fmtDuration(sec: number): string {
  if (sec < 60) return `${sec}초`;
  const m = Math.floor(sec / 60);
  if (m < 60) return `${m}분`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h}시간`;
  return `${Math.floor(h / 24)}일`;
}

/** "N초 전" 상대시각(표시 보조). 경고 판정은 이 값이 아니라 서버의 fresh 다 -- 브라우저
    시계가 틀려도 빨간불이 거짓말하지 않게(placement 가 쓰는 바로 그 판정). */
export function ageText(iso: string, nowMs: number): string | null {
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return null;
  const sec = Math.round((nowMs - ms) / 1000);
  if (sec < 0) return "방금";      // 브라우저 시계가 서버보다 늦다 -- 음수 나이를 지어내지 않는다
  if (sec < 10) return "방금";
  return `${fmtDuration(sec)} 전`;
}

/** 노드 시각 차이(초): 에이전트가 찍은 probed_at(노드 시계, **프로브 시작 전**) - 서버가 받은
    reported_at(서버 시계). "시간이 현재와 많이 다르면" 경고의 두 번째 축(첫째는 리포트 지연).
    정직한 한계(리뷰): probed_at 은 프로브(LDAP 그룹 열거·마운트 statvfs 등) 전에 찍혀 음수 차이는
    노드 시계가 늦은 것일 수도, 프로브가 오래 걸린 것일 수도 있다 -- 화면은 둘을 단정하지 않고
    "시계 또는 프로브 지연"으로 표기한다(양수 = 노드 시계가 빠름, 프로브 지연으로는 생기지 않는다). */
export const CLOCK_SKEW_WARN_SECONDS = 120;
export function clockSkewSeconds(node: Pick<NodeInfo, "reported_at" | "report">): number | null {
  const probed = Date.parse(String(asObject(node.report).probed_at ?? ""));
  const reported = Date.parse(node.reported_at);
  if (Number.isNaN(probed) || Number.isNaN(reported)) return null;
  return Math.round((probed - reported) / 1000);
}

const PILL = {
  ok: "text-ok bg-okbg", bad: "text-bad bg-badbg", busy: "text-busy bg-busybg",
  neutral: "text-muted bg-canvas",
} as const;
function Pill({ tone, children, title }: {
  tone: keyof typeof PILL; children: React.ReactNode; title?: string;
}) {
  return (
    <span title={title}
          className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-semibold whitespace-nowrap ${PILL[tone]}`}>
      {children}
    </span>
  );
}

/** Ready n/m 요약 배지 -- 전부 Ready 초록, 일부 주의, 하나도 없으면 빨강, 0/0 은 중립. */
function ReadyRatio({ items }: { items: unknown }) {
  const arr = asArray<{ status: string }>(items);
  const ready = arr.filter((i) => i.status === "Ready").length;
  const tone = arr.length === 0 ? "neutral" : ready === arr.length ? "ok" : ready === 0 ? "bad" : "busy";
  return <Pill tone={tone}>{`Ready ${ready}/${arr.length}`}</Pill>;
}

function UsageBar({ pct }: { pct: number | null }) {
  if (pct === null) return <span className="text-muted">—</span>;
  const tone = pct >= 90 ? "bg-bad" : pct >= 80 ? "bg-busy" : "bg-ok";
  return (
    <div className="flex items-center gap-2 min-w-[7rem]">
      <div className="h-1.5 flex-1 rounded-full bg-canvas overflow-hidden">
        <div className={`h-full ${tone}`} style={{ width: `${Math.min(100, Math.max(0, pct))}%` }} />
      </div>
      <span className="tabular-nums text-xs">{`${pct.toFixed(1)}%`}</span>
    </div>
  );
}

function memoryPct(os: Record<string, unknown>): number | null {
  const total = num(os.memory_total_kb); const avail = num(os.memory_available_kb);
  return total && avail !== null ? ((total - avail) / total) * 100 : null;
}

/** 마지막 리포트 셀/헤더: 상대시각 + KST 절대시각, 지연·시계 차이면 빨강 + 사유. */
function LastReport({ node, nowMs, compact }: { node: NodeInfo; nowMs: number; compact?: boolean }) {
  const age = ageText(node.reported_at, nowMs);
  const skew = clockSkewSeconds(node);
  const skewBad = skew !== null && Math.abs(skew) > CLOCK_SKEW_WARN_SECONDS;
  const bad = !node.fresh || skewBad;
  return (
    <div className={bad ? "text-bad" : ""}>
      <div className="flex items-center gap-1 font-medium whitespace-nowrap">
        {bad && <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden />}
        {age ?? "—"}
        {!node.fresh && <span className="text-xs">· 리포트 지연</span>}
        {skewBad && (
          <span className="text-xs">{skew! > 0
            ? ` · 노드 시계 +${fmtDuration(skew!)}`
            : ` · 시각 차이 -${fmtDuration(-skew!)}(시계 또는 프로브 지연)`}</span>
        )}
      </div>
      {!compact && <div className={`text-xs ${bad ? "" : "text-muted"}`}>{kstStamp(node.reported_at)}</div>}
    </div>
  );
}

function Section({ title, caption, children }: {
  title: string; caption?: string; children: React.ReactNode;
}) {
  return (
    <section className="rounded-card border border-line">
      <div className="border-b border-line bg-panel px-4 py-2 rounded-t-card">
        <h3 className="text-sm font-semibold">{title}</h3>
        {caption && <p className="text-xs text-muted mt-0.5">{caption}</p>}
      </div>
      <div className="px-4 py-3">{children}</div>
    </section>
  );
}

function Tile({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-card border border-line px-4 py-3">
      <div className="text-xs text-muted">{label}</div>
      <div className="text-lg font-semibold mt-0.5 tabular-nums">{value}</div>
      {sub && <div className="text-xs text-muted mt-0.5">{sub}</div>}
    </div>
  );
}

const Empty = ({ text }: { text: string }) => <p className="text-sm text-muted">{text}</p>;

function NodeDetail({ node }: { node: NodeInfo }) {
  // "최근 리포트"를 누르기 전에는 요청이 나가지 않는다 — enabled는 showHistory 그 자체다.
  const [showHistory, setShowHistory] = useState(false);
  const reportsQ = useNodeReports(node.node_name, showHistory);

  const report = node.report ?? {};
  const mounts = asArray<NodeMount & { writable?: boolean }>(report.mounts);
  const tools = asArray<NodeTool>(report.tools);
  const os = asObject(report.os);
  const disks = asArray<NodeDisk>(os.disks);
  const identities = asArray<unknown>(report.identities);
  const artifact = asObject((report as Record<string, unknown>).artifact_base);
  const directory = asObject((report as Record<string, unknown>).directory);
  const probeMode = (report as Record<string, unknown>).probe_mode;
  const nowMs = Date.now();
  const memPct = memoryPct(os);
  const yesNo = (v: unknown) => (v === true ? "예" : v === false ? "아니오" : "—");

  return (
    <div className="space-y-4 text-sm">
      {/* 상태 줄: 신선도·마지막 리포트(지연·시계 차이 경고)·프로브 방식 */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <Pill tone={node.fresh ? "ok" : "bad"}>{node.fresh ? "정상 보고" : "리포트 지연"}</Pill>
        <PlacementBadges node={node} />
        <LastReport node={node} nowMs={nowMs} />
        {typeof probeMode === "string" && <span className="text-xs text-muted">프로브: {probeMode}</span>}
      </div>
      {/* 배치 상태(2026-10-02): 관리자 배치 제외(사유·누가·언제)와 k8s 스케줄 가능 여부(에이전트 보고). k8s 를
          모르면(조회 실패·옛 에이전트) "모름" -- 막지 않는다(서버 fail-open). */}
      <p className="text-xs text-muted">
        {`배치: ${node.exclusion
          ? `제외됨 — 사유 ${node.exclusion.reason ?? "(없음)"} · ${node.exclusion.created_by} · ${kstStamp(node.exclusion.created_at)}`
          : "포함"} · k8s 스케줄: ${(() => {
            const k = k8sNodeState(node);
            return k.schedulable === true ? "가능"
              : k.schedulable === false ? `${k.transient ? "일시 불가" : "불가"}(${k.reason ?? "사유 모름"})`
              : `모름${k.reason ? `(${k.reason})` : ""}`;
          })()}`}
      </p>

      <div className="grid gap-3 grid-cols-2 lg:grid-cols-4">
        <Tile label="CPU" value={num(os.cpu_count) !== null ? `${os.cpu_count}코어` : "—"} />
        <Tile label="부하(1·5·15분)"
              value={num(os.load1) !== null ? `${num(os.load1)!.toFixed(2)}` : "—"}
              sub={num(os.load5) !== null && num(os.load15) !== null
                ? `${num(os.load5)!.toFixed(2)} · ${num(os.load15)!.toFixed(2)}` : undefined} />
        <Tile label="메모리 사용" value={memPct !== null ? `${memPct.toFixed(1)}%` : "—"}
              sub={num(os.memory_total_kb) !== null
                ? `전체 ${humanBytes(num(os.memory_total_kb)! * 1024)}` : undefined} />
        <Tile label="네트워크 누적(수신/송신)"
              value={num(os.network_rx_bytes) !== null ? humanBytes(os.network_rx_bytes) : "—"}
              sub={num(os.network_tx_bytes) !== null ? `송신 ${humanBytes(os.network_tx_bytes)}` : undefined} />
      </div>

      <Section title="마운트" caption="스토리지 마운트 상태 — Ready 가 아니면 그 노드엔 해당 스토리지 작업이 배치되지 않습니다.">
        {mounts.length === 0 ? <Empty text="보고된 마운트가 없습니다" /> : (
          <Table>
            <thead>
              <tr className="text-muted">
                <th className="py-2">스토리지</th><th>마운트 경로</th><th>상태</th><th>쓰기</th><th>사유</th>
              </tr>
            </thead>
            <tbody>
              {mounts.map((m, i) => (
                <tr key={i} className="border-t border-line">
                  <td className="py-2 font-medium">{m.storage_name}</td>
                  <td className="break-all">{m.mount_path}</td>
                  <td><Pill tone={m.status === "Ready" ? "ok" : "bad"}>{m.status}</Pill></td>
                  <td>{yesNo(m.writable)}</td>
                  <td className="text-muted">{m.reason ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Section>

      <Section title="디스크">
        {disks.length === 0 ? <Empty text="보고된 디스크 사용량이 없습니다" /> : (
          <Table>
            <thead>
              <tr className="text-muted"><th className="py-2">스토리지</th><th>사용</th><th>전체</th><th>사용률(%)</th></tr>
            </thead>
            <tbody>
              {disks.map((d, i) => {
                const pct = Number.isFinite(d.used_bytes) && Number.isFinite(d.total_bytes) && d.total_bytes !== 0
                  ? (d.used_bytes / d.total_bytes) * 100 : null;
                return (
                  <tr key={i} className="border-t border-line">
                    <td className="py-2 font-medium">{d.storage_name}</td>
                    <td>{humanBytes(d.used_bytes)}</td>
                    <td>{humanBytes(d.total_bytes)}</td>
                    <td><UsageBar pct={pct} /></td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
        )}
      </Section>

      <div className="grid gap-4 lg:grid-cols-2">
        {/* 노드엔 바이너리 설치 여부만 표시한다 -- 실제 실행은 잡 파드에서.
            버전·실행 프로브는 노드에서 의미가 없어 제거했다(2026-08-30). */}
        <Section title="도구" caption="도구는 잡 파드에서 실행됩니다 — 여기선 노드에 설치돼 있는지만 확인합니다.">
          {tools.length === 0 ? <Empty text="보고된 도구가 없습니다" /> : (
            <Table>
              <thead><tr className="text-muted"><th className="py-2">이름</th><th>상태</th><th>경로</th></tr></thead>
              <tbody>
                {tools.map((t, i) => (
                  <tr key={i} className="border-t border-line">
                    <td className="py-2 font-medium">{t.name}</td>
                    <td><Pill tone={t.status === "Ready" ? "ok" : "bad"}>{toolStatusText(t.status)}</Pill></td>
                    <td className="text-muted text-xs break-all">{t.path ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
        </Section>

        <Section title="아티팩트 · LDAP 디렉터리">
          <dl className="grid grid-cols-[8rem_1fr] gap-x-3 gap-y-1.5">
            <dt className="text-muted">아티팩트 base</dt>
            <dd className="break-all">{typeof artifact.path === "string" ? artifact.path : "—"}</dd>
            <dt className="text-muted">존재 · 쓰기</dt>
            <dd>{`${yesNo(artifact.exists)} · ${yesNo(artifact.writable)}`}</dd>
            <dt className="text-muted">디렉터리 설정</dt>
            <dd>{typeof directory.source === "string" ? directory.source : "—"}
              {directory.start_tls === true && <span className="text-muted"> · StartTLS</span>}</dd>
            <dt className="text-muted">적용 시각</dt>
            <dd>{typeof directory.applied_at === "string" ? kstStamp(directory.applied_at) : "—"}</dd>
            <dt className="text-muted">오류</dt>
            <dd className={typeof directory.error === "string" && directory.error ? "text-bad" : ""}>
              {typeof directory.error === "string" && directory.error ? directory.error : "없음"}</dd>
          </dl>
        </Section>
      </div>

      {identities.length > 0 && (
        <Section title="신원" caption="에이전트가 노드에서 확인한 실행 신원(LDAP 조회 결과).">
          <Table>
            <thead><tr className="text-muted"><th className="py-2">사용자</th><th>uid</th><th>gid</th><th>상태</th></tr></thead>
            <tbody>
              {identities.map((raw, i) => {
                const id = asObject(raw);
                return (
                  <tr key={i} className="border-t border-line">
                    <td className="py-2">{String(id.username ?? JSON.stringify(raw))}</td>
                    <td className="tabular-nums">{String(id.uid ?? "—")}</td>
                    <td className="tabular-nums">{String(id.gid ?? "—")}</td>
                    <td>{typeof id.status === "string"
                      ? <Pill tone={id.status === "Ready" ? "ok" : "bad"}>{id.status}</Pill> : "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
        </Section>
      )}

      <Section title="최근 리포트">
        {!showHistory ? (
          <Button variant="ghost" onClick={() => setShowHistory(true)}>최근 리포트</Button>
        ) : reportsQ.isLoading ? (
          <p className="text-muted">불러오는 중…</p>
        ) : reportsQ.isError ? (
          <p className="text-bad">{(reportsQ.error as ApiError).message}</p>
        ) : (
          <ul className="grid gap-1 sm:grid-cols-2 lg:grid-cols-3">
            {asArray<NodeReport>(reportsQ.data).map((r, i) => (
              <li key={i} className="tabular-nums">{kstStamp(r.reported_at)}</li>
            ))}
          </ul>
        )}
      </Section>
    </div>
  );
}

export function NodesList() {
  const q = useNodes();
  const nodes = q.data ?? [];
  const nowMs = Date.now();
  const staleCount = nodes.filter((n) => !n.fresh).length;
  const excludedCount = nodes.filter((n) => n.exclusion
    || (k8sNodeState(n).schedulable === false && !k8sNodeState(n).transient)).length;

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-2xl font-bold">노드</h1>
        {!q.isLoading && !q.isError && (
          <p className={`text-sm ${staleCount > 0 ? "text-bad font-medium" : "text-muted"}`}>
            {staleCount > 0 ? `리포트 지연 ${staleCount}대 / 전체 ${nodes.length}대` : `전체 ${nodes.length}대 정상 보고`}
            {excludedCount > 0 && ` · 배치 제외·cordon ${excludedCount}대`}
          </p>
        )}
      </div>
      {/* Card 구획(2026-08-19): 다른 목록 화면들과 같은 서피스 */}
      <Card>
      {q.isLoading ? (
        <p className="text-muted">불러오는 중…</p>
      ) : q.isError ? (
        <p className="text-bad">{(q.error as ApiError).message}</p>
      ) : (
        <>
        {/* 열 순서(사용자 요청 2026-09-30): 마지막 리포트는 맨 끝 -- 상태 판단(무엇이
            Ready 인가)을 먼저 읽고, 언제 보고됐는지는 마지막에 확인한다. */}
        <Table>
          <thead>
            <tr className="text-muted">
              <th className="py-2">노드</th><th>상태</th><th>배치</th><th>마운트</th><th>도구</th>
              <th>CPU · 부하</th><th>메모리</th><th>상세</th><th>마지막 리포트</th>
            </tr>
          </thead>
          <tbody>
            {nodes.map((n) => {
              const os = asObject(asObject(n.report).os);
              return (
                <tr key={n.node_name} className={`border-t border-line ${n.fresh ? "" : "bg-badbg/40"}`}>
                  <td className="py-2 font-medium">{n.node_name}</td>
                  <td>{n.fresh
                    ? <Pill tone="ok">fresh</Pill>
                    : <span className="text-bad font-semibold">stale</span>}</td>
                  {/* 배치 제외·다시 포함·cordon(NodePlacement) */}
                  <td className="pr-3"><PlacementCell node={n} /></td>
                  <td><ReadyRatio items={asObject(n.report).mounts} /></td>
                  <td><ReadyRatio items={asObject(n.report).tools} /></td>
                  <td className="whitespace-nowrap tabular-nums">
                    {num(os.cpu_count) !== null ? `${os.cpu_count}코어` : "—"}
                    {num(os.load1) !== null && <span className="text-muted">{` · ${num(os.load1)!.toFixed(2)}`}</span>}
                  </td>
                  <td className="pr-6"><UsageBar pct={memoryPct(os)} /></td>
                  <td className="py-2">
                    <Dialog title={`${n.node_name} 상세`} size="xl"
                            trigger={<Button variant="ghost">상세</Button>}>
                      <NodeDetail node={n} />
                    </Dialog>
                  </td>
                  <td><LastReport node={n} nowMs={nowMs} /></td>
                </tr>
              );
            })}
          </tbody>
        </Table>
        <p className="mt-3 text-xs text-muted">
          빨간색 = 에이전트 리포트가 기준 시간(DMS_AGENT_REPORT_STALE_SECONDS, 기본 5분)을 넘겨 오지 않았거나,
          노드가 찍은 시각과 서버 수신 시각이 {CLOCK_SKEW_WARN_SECONDS / 60}분 넘게 다른 노드입니다(노드 시계 오차 또는
          프로브가 오래 걸림) — 리포트가 지연된 노드엔 잡이 배치되지 않습니다.
        </p>
        <p className="mt-1 text-xs text-muted">
          배치 제외 = 관리자가 이 화면에서 뺀 노드(다시 포함으로 해제). cordon = k8s 에서 스케줄 불가(cordon·taint)로 보고된
          노드(kubectl uncordon 으로 해제). 둘 다 새 작업이 배치되지 않고, 이미 그 노드로 계획됐거나 대기 중인 작업은
          종료되며, 그 노드에서 이미 실행 중인 작업은 그대로 둡니다. 일시 불가 = 노드 압박·준비 안 됨 같은 k8s 일시
          상태 — 새 작업만 다른 노드로 보내고 이미 계획된 작업은 기다립니다.
        </p>
        </>
      )}
      </Card>
    </section>
  );
}
