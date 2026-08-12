import { Link } from "react-router-dom";
import { useRequests } from "./useJobs";
import { Table } from "../../components/ui/Table";
import { StatusPill } from "../../components/ui/StatusPill";
import { Button } from "../../components/ui/Button";

export function JobsList() {
  const q = useRequests();
  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">내 작업</h1>
        <Link to="/jobs/new"><Button>작업 제출</Button></Link>
      </div>
      {q.isLoading ? <p className="text-muted">불러오는 중…</p> : (
        <Table>
          <thead><tr className="text-muted"><th className="py-2">요청</th><th>작업</th><th>상태</th><th>생성</th></tr></thead>
          <tbody>
            {(q.data ?? []).map((r) => (
              <tr key={r.request_id} className="border-t border-black/5">
                <td className="py-2"><Link className="text-accent" to={`/jobs/${r.request_id}`}>{r.request_id}</Link></td>
                <td>{r.operation}</td><td><StatusPill state={r.state} /></td>
                <td className="text-muted">{r.created_at}</td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </section>
  );
}
