import { useState } from "react";
import { useNodes, useNodeReports } from "./useNodes";
import { Card } from "../../components/ui/Card";
import { Table } from "../../components/ui/Table";
import { Button } from "../../components/ui/Button";
import { ApiError } from "../../lib/api";
import type { NodeInfo, NodeMount, NodeTool, NodeDisk, NodeReport } from "../../lib/types";

// 에이전트 리포트는 스키마 검증 없이 저장된다 — 배열이어야 할 필드가 배열이
// 아닌 값(예: {})으로 와도 여기서 걸러야 목록 화면 전체가 죽지 않는다.
const asArray = <T,>(v: unknown): T[] => (Array.isArray(v) ? v : []);

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

function readyRatio(items: unknown): string {
  const arr = asArray<{ status: string }>(items);
  const ready = arr.filter((i) => i.status === "Ready").length;
  return `Ready ${ready}/${arr.length}`;
}

function NodeDetail({ node }: { node: NodeInfo }) {
  // "최근 리포트"를 누르기 전에는 요청이 나가지 않는다 — enabled는 showHistory 그 자체다.
  const [showHistory, setShowHistory] = useState(false);
  const reportsQ = useNodeReports(node.node_name, showHistory);

  const report = node.report ?? {};
  const mounts = asArray<NodeMount>(report.mounts);
  const tools = asArray<NodeTool>(report.tools);
  const disks = asArray<NodeDisk>(report.os?.disks);
  const identities = asArray<unknown>(report.identities);

  return (
    <Card className="space-y-5">
      <h2 className="font-semibold">{node.node_name} 상세</h2>

      <div>
        <h3 className="font-medium mb-2">마운트</h3>
        <Table>
          <thead>
            <tr className="text-muted">
              <th className="py-2">스토리지</th><th>마운트 경로</th><th>상태</th><th>사유</th>
            </tr>
          </thead>
          <tbody>
            {mounts.map((m, i) => (
              <tr key={i} className="border-t border-black/5">
                <td className="py-2">{m.storage_name}</td>
                <td>{m.mount_path}</td>
                <td>{m.status}</td>
                <td className="text-muted">{m.reason ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </Table>
      </div>

      <div>
        <h3 className="font-medium mb-2">도구</h3>
        <Table>
          <thead>
            <tr className="text-muted"><th className="py-2">이름</th><th>상태</th><th>버전</th><th>사유</th></tr>
          </thead>
          <tbody>
            {tools.map((t, i) => (
              <tr key={i} className="border-t border-black/5">
                <td className="py-2">{t.name}</td>
                <td>{t.status}</td>
                <td className="text-muted">{t.version ?? "—"}</td>
                <td className="text-muted">{t.reason ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </Table>
      </div>

      <div>
        <h3 className="font-medium mb-2">디스크</h3>
        <Table>
          <thead>
            <tr className="text-muted"><th className="py-2">스토리지</th><th>사용</th><th>전체</th><th>사용률(%)</th></tr>
          </thead>
          <tbody>
            {disks.map((d, i) => (
              <tr key={i} className="border-t border-black/5">
                <td className="py-2">{d.storage_name}</td>
                <td>{humanBytes(d.used_bytes)}</td>
                <td>{humanBytes(d.total_bytes)}</td>
                <td>
                  {Number.isFinite(d.used_bytes) && Number.isFinite(d.total_bytes) && d.total_bytes !== 0
                    ? `${((d.used_bytes / d.total_bytes) * 100).toFixed(1)}%`
                    : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      </div>

      {identities.length > 0 && (
        <div>
          <h3 className="font-medium mb-2">신원</h3>
          <ul className="text-sm space-y-1">
            {identities.map((id, i) => (
              <li key={i} className="text-muted">{JSON.stringify(id)}</li>
            ))}
          </ul>
        </div>
      )}

      <div>
        <Button variant="ghost" onClick={() => setShowHistory(true)}>최근 리포트</Button>
        {showHistory && (
          reportsQ.isLoading ? (
            <p className="text-muted mt-2">불러오는 중…</p>
          ) : reportsQ.isError ? (
            <p className="text-bad mt-2">{(reportsQ.error as ApiError).message}</p>
          ) : (
            <Table>
              <thead><tr className="text-muted"><th className="py-2">리포트 시각</th></tr></thead>
              <tbody>
                {asArray<NodeReport>(reportsQ.data).map((r, i) => (
                  <tr key={i} className="border-t border-black/5">
                    <td className="py-2">{r.reported_at}</td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )
        )}
      </div>
    </Card>
  );
}

export function NodesList() {
  const q = useNodes();
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const nodes = q.data ?? [];
  const selected = nodes.find((n) => n.node_name === selectedName) ?? null;

  return (
    <section className="space-y-4">
      <h1 className="text-2xl font-bold">노드</h1>
      {q.isLoading ? (
        <p className="text-muted">불러오는 중…</p>
      ) : q.isError ? (
        <p className="text-bad">{(q.error as ApiError).message}</p>
      ) : (
        <Table>
          <thead>
            <tr className="text-muted">
              <th className="py-2">노드</th><th>신선도</th><th>마지막 리포트</th><th>마운트</th><th>도구</th><th>작업</th>
            </tr>
          </thead>
          <tbody>
            {nodes.map((n) => (
              <tr key={n.node_name} className="border-t border-black/5">
                <td className="py-2">{n.node_name}</td>
                <td>{n.fresh ? "fresh" : <span className="text-bad">stale</span>}</td>
                <td className="text-muted">{n.reported_at}</td>
                <td>{readyRatio(n.report?.mounts)}</td>
                <td>{readyRatio(n.report?.tools)}</td>
                <td className="py-2">
                  <Button variant="ghost" onClick={() => setSelectedName(n.node_name)}>상세</Button>
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
      {selected && <NodeDetail key={selected.node_name} node={selected} />}
    </section>
  );
}
