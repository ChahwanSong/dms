import { useAuditLog } from "./useAudit";
import { Table } from "../../components/ui/Table";
import { Card } from "../../components/ui/Card";
export function AuditLog() {
  const q = useAuditLog();
  return (
    <section className="space-y-4">
      <h1 className="text-2xl font-bold">감사 로그</h1>
      {/* Card 구획(2026-08-19): 운영 화면들과 같은 서피스 — 회색 페이지 배경 위
          맨 표는 경계가 없어 관리 그룹만 다른 화면처럼 보였다 */}
      <Card>
      {q.isLoading ? <p className="text-muted">불러오는 중…</p> : (
        <Table>
          <thead><tr className="text-muted"><th className="py-2">클래스</th><th>시각</th><th>작업</th><th>대상</th><th>실행자</th></tr></thead>
          <tbody>
            {(q.data ?? []).map((e) => (
              <tr key={e.id} className="border-t border-black/5">
                <td className="py-2">{e.mutation_class}</td>
                <td className="text-muted">{e.at}</td><td>{e.operation}</td>
                <td>{e.target_key}</td><td className="text-muted">{e.actor}</td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
      </Card>
    </section>
  );
}
