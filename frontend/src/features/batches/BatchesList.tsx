import { Link } from "react-router-dom";
import { useBatches } from "./useBatches";
import { Table } from "../../components/ui/Table";
import { StatusPill } from "../../components/ui/StatusPill";
import { Button } from "../../components/ui/Button";
export function BatchesList() {
  const q = useBatches();
  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">배치 작업</h1>
        <Link to="/admin/batches/new"><Button>배치 생성</Button></Link>
      </div>
      {q.isLoading ? <p className="text-muted">불러오는 중…</p> : (
        <Table>
          <thead><tr className="text-muted"><th className="py-2">배치</th><th>이름</th><th>작업</th><th>상태</th><th>진행</th></tr></thead>
          <tbody>
            {(q.data ?? []).map((b) => (
              <tr key={b.batch_id} className="border-t border-black/5">
                <td className="py-2"><Link className="text-accent" to={`/admin/batches/${b.batch_id}`}>{b.batch_id.slice(0,12)}</Link></td>
                {/* null/부재 = 이름 없음 — 빈칸 대신 "—" 로 명시(모름과 구분할 값이
                    없는 단순 부재라 대시가 정직하다) */}
                <td>{b.name ?? "—"}</td>
                <td>{b.operation}</td><td><StatusPill state={b.status} /></td>
                <td className="text-muted">{b.succeeded_count}/{b.failed_count}/{b.item_count}</td>
              </tr>))}
          </tbody>
        </Table>)}
    </section>
  );
}
