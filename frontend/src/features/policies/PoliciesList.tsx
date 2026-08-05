import { usePolicies } from "./usePolicies";
import { PolicyDialog } from "./PolicyDialog";
import { Table } from "../../components/ui/Table";
import { Button } from "../../components/ui/Button";
import { ApiError } from "../../lib/api";

function humanSeconds(s: number | null): string {
  if (s === null) return "—";
  if (s % 86400 === 0) return `${s}s (${s / 86400}d)`;
  if (s % 3600 === 0) return `${s}s (${s / 3600}h)`;
  if (s % 60 === 0) return `${s}s (${s / 60}m)`;
  return `${s}s`;
}

export function PoliciesList() {
  const q = usePolicies();
  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">정책</h1>
      </div>
      {q.isLoading ? <p className="text-muted">불러오는 중…</p> : q.isError ? (
        <p className="text-bad">{(q.error as ApiError).message}</p>
      ) : (
        <Table>
          <thead>
            <tr className="text-muted">
              <th className="py-2">도구</th><th>최대 노드</th><th>노드당 프로세스</th><th>큐</th>
              <th>기본/최대 우선순위</th><th>미리보기 타임아웃</th><th>실행 타임아웃</th><th>활성</th><th>작업</th>
            </tr>
          </thead>
          <tbody>
            {(q.data ?? []).map((p) => (
              <tr key={p.tool} className="border-t border-black/5">
                <td className="py-2">{p.tool}</td>
                <td>{p.max_nodes}</td>
                <td>{p.procs_per_node}</td>
                <td className="text-muted">{p.queue}</td>
                <td>{p.default_priority} / {p.max_priority}</td>
                <td className="text-muted">{humanSeconds(p.preview_timeout_seconds)}</td>
                <td className="text-muted">{humanSeconds(p.execution_timeout_seconds)}</td>
                <td>{p.enabled === 1 ? "on" : "off"}</td>
                <td className="py-2">
                  <PolicyDialog policy={p} trigger={<Button variant="ghost">수정</Button>} />
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </section>
  );
}
