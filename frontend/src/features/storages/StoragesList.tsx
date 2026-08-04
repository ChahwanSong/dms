import { useStorages } from "./useStorages";
import { Table } from "../../components/ui/Table";
import { StatusPill } from "../../components/ui/StatusPill";
export function StoragesList() {
  const q = useStorages();
  return (
    <section className="space-y-4">
      <h1 className="text-lg font-semibold">스토리지</h1>
      {q.isLoading ? <p className="text-muted">불러오는 중…</p> : (
        <Table>
          <thead><tr className="text-muted"><th className="py-2">이름</th><th>백엔드</th><th>마운트</th><th>상태</th></tr></thead>
          <tbody>
            {(q.data ?? []).map((s) => (
              <tr key={s.storage_name} className="border-t border-black/5">
                <td className="py-2">{s.storage_name}</td><td>{s.backend_type}</td>
                <td className="text-muted">{s.mount_path}</td><td><StatusPill state={s.status} /></td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </section>
  );
}
