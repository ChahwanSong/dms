import { useAuditLog } from "./useAudit";
import { Table } from "../../components/ui/Table";
export function AuditLog() {
  const q = useAuditLog();
  return (
    <section className="space-y-4">
      <h1 className="text-lg font-semibold">감사 로그</h1>
      {q.isLoading ? <p className="text-muted">불러오는 중…</p> : (
        <Table>
          <thead><tr className="text-muted"><th className="py-2">시각</th><th>작업</th><th>대상</th><th>실행자</th></tr></thead>
          <tbody>
            {(q.data ?? []).map((e) => (
              <tr key={e.id} className="border-t border-black/5">
                <td className="py-2 text-muted">{e.at}</td><td>{e.operation}</td>
                <td>{e.target_key}</td><td className="text-muted">{e.actor}</td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </section>
  );
}
